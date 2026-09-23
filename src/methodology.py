
from __future__ import annotations

import datetime as dt
import statistics
import sys


import stats
from ffcore import schema
from ffcore.fixture import FIX_BAND
from ffcore.score import SHRINK_K
from ffcore.text import norm, resolve
from ffcore.tidy import (clock_history, load_starters, run_now, shown,
                         table_stats,
                         DECISIONS, PARTS, LINEUP_SOURCE,
                         DAILY_FRESH_DAYS, EVERY_RUN_FRESH_DAYS,
                         SEASON, TIDY, age_phrase, load_elo,
                         stale_feeds,
                         load_lineups, load_matches,
                         read_csv, snapshot_stamp, write_csv, write_lines,
                         team_slug_of, lock_order, JornadaClock)

LIVE = SEASON / "live"
WINDOW_DAYS = 21

NOT_GRADED: list[str] = []



def latest_before(preds: list[tuple[dt.datetime, dict]],
                  cutoff: dt.datetime) -> dict | None:
    best = None
    for when, fac in preds:
        if when < cutoff:
            best = fac
        else:
            break
    return best


def match_claim(candidate_keys, store: dict[str, list[tuple[dt.datetime, dict]]],
                cutoff: dt.datetime) -> tuple[str, dict] | None:
    for k in candidate_keys:
        hits = store.get(k)
        if hits:
            fac = latest_before(hits, cutoff)
            if fac is not None:
                return k, fac
    return None


def _group_by_key(rows, key_fn=None, when_fn=None, value_fn=None,
                  src=None, universe=None) -> dict[str, list[tuple]]:
    key_fn = key_fn or (lambda r: norm(r.get("player_name", "")))
    when_fn = when_fn or (lambda r: snapshot_stamp(r.get("observed_at", "")))
    value_fn = value_fn or (lambda r: r)
    per: dict[str, list] = {}
    for r in rows:
        if src is not None and schema.text(r, "source") != src:
            continue
        try:
            key = key_fn(r)
            when = when_fn(r)
            value = value_fn(r)
        except (KeyError, ValueError, TypeError):
            continue
        if not key or when is None:
            continue
        if universe is not None and key not in universe:
            continue
        per.setdefault(key, []).append((when, value))
    for v in per.values():
        v.sort(key=lambda t: t[0])
    return per


def pair(actuals: list[dict],
         preds: dict[str, list[tuple[dt.datetime, dict]]]) -> list[dict]:
    out = []
    for a in actuals:
        if a["games_delta"] < 1:
            continue
        got = match_claim(a["keys"], preds, a["from_dt"])
        if got is None:
            continue
        _key, fac = got
        predicted = fac["score"] * a["games_delta"]
        out.append({
            "name": a["name"],
            "predicted": predicted,
            "actual": a["points_delta"],
            "per_match": fac["score"],
            "matches": a["games_delta"],
            "err": predicted - a["points_delta"],
            "fix": fac.get("fix"),
            "jornada": a.get("jornada"),
        })
    return out


def _conditional(fac: dict) -> float:
    """The prediction CONDITIONAL on the player having played: his rate
    times the fixture, with no P(start) in it.

    `_default_predicted` below is the unconditional one -- it carries
    P(start) -- and load_actuals() only ever yields rows for players who
    DID play. Grade one against the other and P(start) miscalibration
    reads as rate error. Both fitters here want this one; shared rather
    than nested inside one of them, which is why the other never got it.
    """
    return (fac.get("ppm") or 0.0) * (fac.get("fix") or 1.0)


def _default_predicted(fac: dict) -> float:
    return fac["score"]


def lagged_pair(actuals: list[dict],
                preds: dict[str, list[tuple[dt.datetime, dict]]],
                locks: dict[int, dt.datetime], lag: int,
                predicted_fn=_default_predicted) -> list[dict]:
    order = lock_order(locks)
    pos = {j: i for i, j in enumerate(order)}
    out = []
    for a in actuals:
        if a["games_delta"] < 1:
            continue
        j = a.get("jornada")
        i = pos.get(j)
        if i is None or i - lag < 0:
            continue
        cutoff = locks[order[i - lag]]
        got = match_claim(a["keys"], preds, cutoff)
        if got is None:
            continue
        _key, fac = got
        per_match = predicted_fn(fac)
        predicted = per_match * a["games_delta"]
        out.append({"name": a["name"], "predicted": predicted,
                    "actual": a["points_delta"], "per_match": per_match,
                    "matches": a["games_delta"],
                    "err": predicted - a["points_delta"],
                    "jornada": j, "pj": fac.get("pj")})
    return out


def fit_rate_rel_floor(pool, min_pairs: int = 30) -> tuple[float, str]:
    import statistics as _stats

    from ffcore.forecast import RATE_REL_FLOOR as _default
    from ffcore.forecast import SHRINK_MATCHES

    real = [p for p in pool if p is not None]
    if len(real) < 50:
        return _default, "too few real matches in the pool (n=%d) to " \
            "measure cv — keeping %.2f" % (len(real), _default)
    mean = _stats.mean(real)
    if abs(mean) < 1e-9:
        return _default, "pool mean measured as 0 — can't normalise"
    cv = _stats.pstdev(real) / mean

    locks = clock_history().round_locks
    actuals, _label = load_actuals()
    preds = load_predictions()

    graded = lagged_pair(actuals, preds, locks, 0, predicted_fn=_conditional)
    rels = []
    for g in graded:
        pj = g.get("pj")
        if pj is None or g["predicted"] <= 0 or g["actual"] <= 0:
            continue
        rel = cv / (max(1.0, pj + SHRINK_MATCHES)) ** 0.5
        rels.append((rel, g["predicted"], g["actual"]))
    if len(rels) < min_pairs:
        return _default, "too few graded pairs with a logged pj (n=%d, " \
            "need >=%d) — keeping %.2f" % (len(rels), min_pairs, _default)

    import math

    def _var_at(floor):
        zs = [math.log(a / p) / max(floor, rel) for rel, p, a in rels]
        return _stats.pvariance(zs)

    grid = [0.10 + 0.05 * i for i in range(19)]
    best_floor = min(grid, key=lambda f: abs(_var_at(f) - 2.0))
    return best_floor, ("Var(z)=%.2f at the shipped floor %.2f -> best-fit "
                        "%.2f (n=%d real graded pairs, cv=%.3f)"
                        % (_var_at(_default), _default, best_floor,
                           len(rels), cv))


def drift_frac_from_history(lag1: int = 1, lag3: int = 3) -> tuple[float, str]:
    from ffcore.forecast import fit_drift_frac

    locks = clock_history().round_locks
    actuals, _label = load_actuals()
    preds = load_predictions()

    # CONDITIONAL, matching fit_rate_rel_floor() above. Bootstrap's own
    # prediction is UNCONDITIONAL -- pts * p_start -- while load_actuals()
    # only yields rows for players who actually played (games_delta >= 1).
    # Comparing the two mixes P(start) miscalibration into what reads as
    # RATE drift, and it gets worse at longer lags because P(start)
    # further out is less accurate. That is experiment_log.csv's
    # drift_frac_conditional_fix (2026-09-16, n=279), which SUPERSEDED
    # three earlier entries chasing a drift signal that turned out to be
    # this artifact. The correction was applied to rate_rel's fit and
    # never to this one. Measured: rate_rel 1.725 -> 1.196.
    h1 = lagged_pair(actuals, preds, locks, lag1, predicted_fn=_conditional)
    h3 = lagged_pair(actuals, preds, locks, lag3, predicted_fn=_conditional)
    if not h1:
        return 1.0, "no lag-%d pairs available yet" % lag1
    ratios = [p["actual"] / p["predicted"] for p in h1 if p["predicted"] > 0]
    if len(ratios) < 5:
        return 1.0, "too few lag-%d pairs to measure a pooled rate_rel (n=%d)" \
            % (lag1, len(ratios))
    pooled_rel = statistics.pstdev(ratios)
    if pooled_rel <= 0:
        return 1.0, "pooled rate_rel measured as 0 — can't normalise"
    h1_pairs = [(p["predicted"], p["actual"], pooled_rel) for p in h1]
    h3_pairs = [(p["predicted"], p["actual"], pooled_rel) for p in h3]
    return fit_drift_frac(h1_pairs, h3_pairs)


PAR_DEFINITION = (
    "season points above the LEAGUE's own replacement level at his slot "
    "(the score of the last man the league can start there, pooled across "
    "every squad, not just yours)"
)

BUCKETS = [(-1e9, 2, "under 2"), (2, 3, "2–3"), (3, 4, "3–4"), (4, 1e9, "4+")]

MIN_BUCKET_N = 20

FIX_EDGE = 0.03

FIX_BUCKETS = [(-1e9, 1.0 - FIX_EDGE, "harder"),
               (1.0 - FIX_EDGE, 1.0 + FIX_EDGE, "neutral"),
               (1.0 + FIX_EDGE, 1e9, "easier")]


def fixture_rows(pairs: list[dict]) -> tuple[list[tuple], int]:
    known = [p for p in pairs if p.get("fix") is not None]
    out = []
    for lo, hi, label in FIX_BUCKETS:
        grp = [p for p in known if lo <= p["fix"] < hi]
        if not grp:
            continue
        n = len(grp)
        per = lambda f: sum(p[f] / p["matches"] for p in grp) / n  # noqa: E731
        out.append((label, n, per("predicted"), per("actual"), per("err")))
    return out, len(pairs) - len(known)


def bucket_rows(pairs: list[dict]) -> list[tuple[str, int, float, float]]:
    out = []
    for lo, hi, label in BUCKETS:
        grp = [p for p in pairs if lo <= p["per_match"] < hi]
        if not grp:
            continue
        n = len(grp)
        mp = sum(p["predicted"] / p["matches"] for p in grp) / n
        ma = sum(p["actual"] / p["matches"] for p in grp) / n
        out.append((label, n, mp, ma))
    return out



START_EDGE = 10.0


def appearances(actuals: list[dict]) -> list[tuple[dt.datetime, set]]:
    by_start: dict[dt.datetime, set] = {}
    for a in actuals:
        seen = by_start.setdefault(a["from_dt"], set())
        if a["games_delta"] >= 1:
            seen.update(a["keys"])
    return sorted(by_start.items())


def _matched_start_row(hist, start, teams):
    row = latest_before(hist, start)
    if row is None:
        return None
    if teams is not None and schema.text(row, "team_slug") not in teams:
        return None
    return row


def _start_classify(row):
    try:
        pct = float(row.get("start_pct"))
    except (TypeError, ValueError):
        pct = None
    if pct is None:
        return ("named", None) if (row.get("role") or "") == "starter" else None
    if abs(pct - 50.0) < START_EDGE:
        return None
    return ("numeric", pct)


def start_grade(intervals, claims, universe=None, instances=None):
    sources = {schema.text(r, "source") for r in claims} - {""}
    per = {src: g for src in sources
          if (g := _group_by_key(claims, src=src, universe=universe))}

    num: dict[str, list] = {}
    nam: dict[str, list] = {}
    skipped = 0
    for interval in intervals:
        start, played = interval[0], interval[1]
        teams = interval[2] if len(interval) > 2 else None
        for src, byname in per.items():
            for key, hist in byname.items():
                if instances is not None and (key, start) not in instances:
                    continue
                row = _matched_start_row(hist, start, teams)
                if row is None:
                    continue
                slug = schema.text(row, "player_slug")
                hit = 1.0 if key in played or (slug and slug in played) else 0.0
                kind, pct = _start_classify(row) or (None, None)
                if kind is None:
                    skipped += 1
                elif kind == "named":
                    nam.setdefault(src, []).append(hit)
                else:
                    num.setdefault(src, []).append((pct, hit))

    numbered = []
    for src in sorted(num):
        rows = num[src]
        n = len(rows)
        mean_claim = sum(p for p, _ in rows) / n
        rate = 100.0 * sum(h for _, h in rows) / n
        brier = sum((p / 100.0 - h) ** 2 for p, h in rows) / n
        numbered.append((src, n, mean_claim, rate, brier))
    named = [(src, len(v), 100.0 * sum(v) / len(v))
             for src, v in sorted(nam.items())]
    return numbered, named, skipped


def _start_instances(intervals, claims, src, universe=None) -> set:
    per = _group_by_key(claims, src=src, universe=universe)

    out = set()
    for interval in intervals:
        start = interval[0]
        teams = interval[2] if len(interval) > 2 else None
        for key, hist in per.items():
            row = _matched_start_row(hist, start, teams)
            if row is None:
                continue
            kind, _pct = _start_classify(row) or (None, None)
            if kind == "numeric":
                out.add((key, start))
    return out



def market_names(market: list[dict], slugs) -> dict[str, list[dict]]:
    latest = {}
    for r in market:
        if r.get("name"):
            latest[r["name"]] = r
    out: dict[str, list[dict]] = {}
    for r in latest.values():
        slug = team_slug_of(r.get("team") or "", slugs)
        if slug:
            out.setdefault(slug, []).append(r)
    return out


_XW_CACHE: list = []


def _market_key(slug) -> str:
    slug = (slug or "").strip()
    if not slug:
        return ""
    if not _XW_CACHE:
        from ffcore.tidy import load_crosswalk
        _XW_CACHE.append(load_crosswalk())
    xw = _XW_CACHE[0]
    return (xw.player(ff_slug=slug) or "") if xw else ""


def start_intervals(matches: list[dict], starters: list[dict],
                    fixtures: list[dict], market: list[dict] = (),
                    roles=("starter",)):
    jornada_of = {}
    for m in matches:
        try:
            jornada_of[m["match_id"]] = int(m["jornada"])
        except (KeyError, ValueError, TypeError):
            continue
    locks = JornadaClock(matches, fixtures).team_locks
    squads = market_names(market, {r.get("team_slug") for r in starters})

    seen, by_round, teams, ungraded = set(), {}, {}, set()
    graded = 0
    for r in starters:
        if (r.get("role") or "") not in roles:
            continue
        jor = jornada_of.get(r.get("match_id"))
        if jor is None:
            continue
        mark = (r.get("match_id"), r.get("player_name"))
        if mark in seen:
            continue
        seen.add(mark)
        team = schema.text(r, "team_slug")
        lock = locks.get((jor, team))
        if lock is None:
            ungraded.add(jor)
            continue
        graded += 1
        priced, _ = resolve(r.get("player_name", ""), squads.get(team, []))
        by_round.setdefault(lock, set()).update(
            k for k in (_market_key(r.get("player_slug")),
                        norm(r.get("player_name", "")),
                        schema.text(r, "player_slug"),
                        norm(priced["name"]) if priced else "") if k)
        teams.setdefault(lock, set()).add(team)
    out = [(lock, keys, teams[lock]) for lock, keys in sorted(by_round.items())]
    return out, graded, sorted(ungraded)



def load_actuals(window_days: int | None = WINDOW_DAYS) -> tuple[list[dict], str]:
    files = sorted(LIVE.glob("perjornada_*.csv")) if LIVE.exists() else []
    if not files:
        return [], ""
    label = files[-1].stem.replace("perjornada_", "")
    cutoff = (run_now() - dt.timedelta(days=window_days)
             if window_days is not None else dt.datetime.min.replace(
                 tzinfo=dt.timezone.utc))
    rows = []
    for r in read_csv(files[-1]):
        try:
            from_dt = snapshot_stamp(r["from_stamp"])
            to_dt = snapshot_stamp(r["to_stamp"])
            gd = float(r["games_delta"] or 0)
            pd_ = float(r["points_delta"] or 0)
        except (KeyError, ValueError, TypeError):
            continue
        if to_dt is None or from_dt is None or to_dt < cutoff:
            continue
        full = r.get("player_name_full", "")
        short = r.get("player_name", "")
        keys = [k for k in {norm(full), norm(short)} if k]
        jor = r.get("jornada", "")
        rows.append({"name": full or short, "keys": keys,
                     "from_dt": from_dt, "points_delta": pd_,
                     "games_delta": gd,
                     "jornada": int(jor) if jor else None})
    return rows, label


def load_universe() -> set:
    return {norm(r.get("name", "")) for r in read_csv(TIDY / "market.csv")
            if r.get("name")}


def load_starts(roles=("starter",)):
    return start_intervals(load_matches(),
                           load_starters(),
                           read_csv(TIDY / "fixtures.csv"),
                           read_csv(TIDY / "market.csv"),
                           roles)


def load_predictions() -> dict[str, list[tuple[dt.datetime, dict]]]:
    def _factors(r):
        fac = {"score": float(r["score"])}
        for col in ("fix", "ppm", "flat", "start_pct", "cur_pj", "pj"):
            try:
                fac[col] = float(r[col])
            except (KeyError, ValueError, TypeError):
                fac[col] = None
        fac["home"] = schema.flag(r, "home")
        fac["pos"] = (r.get("pos") or "").lower()
        fac["status"] = r.get("status") or ""
        return fac

    return _group_by_key(
        read_csv(DECISIONS / "squad_log.csv"),
        key_fn=lambda r: schema.text(r, "ff_id") or norm(r.get("player", "")),
        when_fn=lambda r: snapshot_stamp(r["observed_at"]),
        value_fn=_factors)


def golden_dataset() -> dict[int, dict[str, dict]]:
    preds = load_predictions()
    actuals, _label = load_actuals()
    out: dict[int, dict[str, dict]] = {}
    for a in actuals:
        if a["games_delta"] < 1 or a.get("jornada") is None:
            continue
        got = match_claim(a["keys"], preds, a["from_dt"])
        if got is None:
            continue
        matched_key, fac = got
        row = dict(fac)
        row["actual"] = a["points_delta"]
        row["games"] = a["games_delta"]
        out.setdefault(a["jornada"], {})[matched_key] = row
    return out



FILLS = {
    "market": "price, value, position, fitness — every player in the game",
    "lineups": "probable XI percentages, both sources",
    "matches": "fixtures, kickoffs, results",
    "starters": "confirmed elevens, which is what P(start) is graded on",
    "fixtures": "who plays whom next, for the fixture term",
    "elo": "team strength, which ranks the fixture term",
    "points": "realised points per jornada, the actuals in every table below",
    "api_leagues": "your cash and the league's id",
    "api_market": "what is on offer, and the bids on it",
    "api_teams": "all five squads",
    "api_lineup": "the eleven you have actually fielded, and the formation "
                  "the app says you are playing",
    "api_standings": "the league table — position, points, squad value, and "
                     "your balance",
    "api_activity": "every transfer, which is what the ledger replays — one row per deal, so the newest is the last deal and not the last sweep",
    "api_players": "names for players nobody owns any more — one row per player, first sighting kept",
    "api_stats": "what the app scored each player, broken into what he did — "
                 "one row per player per week per stat, a correction being a "
                 "later row rather than an overwrite",
    "players": "the crosswalk: one key per player across all four spellings",
    "clubs": "the same, for clubs",
}

DERIVED = {"players", "clubs"}

HOSTS = {"api_leagues": ("LaLiga Fantasy API", "every_run"),
         "api_market": ("LaLiga Fantasy API", "every_run"),
         "api_teams": ("LaLiga Fantasy API", "every_run"),
         "api_lineup": ("LaLiga Fantasy API", "every_run"),
         "api_standings": ("LaLiga Fantasy API", "every_run"),
         "api_activity": ("LaLiga Fantasy API", "every_run"),
         "api_players": ("LaLiga Fantasy API", "once"),
         "api_stats": ("LaLiga Fantasy API", "every_run"),
         "starters": ("futbolfantasy.com", "once"),
         "points": ("futbolfantasy.com", "as played")}

def _as_it_happens() -> dict[str, str]:
    from sources import STORE_ONCE

    return {t: "as dealt" for t in STORE_ONCE}

STAMPED = {"points": "to_stamp"}

FRESH = {"every_run": EVERY_RUN_FRESH_DAYS, "daily": DAILY_FRESH_DAYS,
         "twice_daily": EVERY_RUN_FRESH_DAYS, "once": 1e9, "as played": 1e9,
         "as dealt": 1e9, "derived": 1e9}


def _hosts() -> dict[str, tuple[str, str, int]]:
    from urllib.parse import urlparse

    from sources import sources

    out: dict[str, tuple[set, set, set]] = {}
    for src in sources():
        host = urlparse(src.url).netloc.removeprefix("www.")
        hosts, cadences, keys = out.get(src.table, (set(), set(), set()))
        out[src.table] = (hosts | ({host} if host else set()),
                          cadences | {src.cadence}, keys | {src.key})
    return {t: (", ".join(sorted(h)) or HOSTS.get(t, ("—",))[0],
                "/".join(sorted(c)), k)
            for t, (h, c, k) in out.items()}


def _feed_state() -> dict[str, str]:
    runs: dict[str, list[dict]] = {}
    for r in read_csv(TIDY / "feeds.csv"):
        if r.get("page"):
            runs.setdefault(r["page"], []).append(r)
    out = {}
    for page, rows in runs.items():
        bad = []
        for r in reversed(rows):
            if r.get("status") != "FAILED":
                break
            bad.append(float(r.get("seconds") or 0))
        if bad:
            out[page] = ("failed the last sweep, %.1fs" % bad[0]
                         if len(bad) == 1 else
                         "failed the last %d sweeps, %.1fs each"
                         % (len(bad), sum(bad) / len(bad)))
    return out


def _age(stamp: str, now: dt.datetime) -> tuple[float | None, str]:
    when = snapshot_stamp(stamp)
    if when is None:
        return None, "—"
    days = (now - when).total_seconds() / 86400.0
    return days, shown(when, "%d %b %H:%M")


API_PAGES = {"match": ("starters",),
             "api_lineup": ("api_lineup",),
             "api_leagues": ("api_leagues",),
             "api_market": ("api_market",),
             "api_teams": ("api_teams", "api_standings", "api_stats"),
             "api_activity": ("api_activity",),
             "api_player": ("api_players",)}


def _fetched() -> dict[str, list[float]]:
    from ingest import state

    reg = _hosts()
    of_table: dict[str, list[float]] = {}
    now = run_now()
    for page, row in state().items():
        when = snapshot_stamp(row.get("seen") or "")
        if when is None:
            continue
        hours = (now - when).total_seconds() / 3600.0
        tables = [t for t, (_h, _c, keys) in reg.items() if page in keys]
        for prefix, named in API_PAGES.items():
            if page == prefix or page.startswith(prefix + "_"):
                tables = list(named)
        for t in tables:
            of_table.setdefault(t, []).append(hours)
    return of_table


GREEN, AMBER, RED, GREY = "🟢", "🟡", "🔴", "⚪"


def _light(cadence: str, ages: list[float], refused: bool) -> tuple[str, str]:
    if cadence == "derived":
        return GREY, "rebuilt every run from the tables above"
    if not ages:
        return RED, "**not in the last sweep**"
    bound = FRESH.get(cadence, 1e9) * 24
    newest, oldest = min(ages), max(ages)
    if refused or newest > 2 * bound:
        return RED, "**not fetched for %s — the readers have dropped it**" % (
            age_phrase(newest / 24))
    if newest > bound:
        return AMBER, "**%s since it was asked** (due every %s)" % (
            age_phrase(newest / 24), CADENCE_WORD.get(cadence, cadence))
    if oldest > bound and len(ages) > 1:
        return AMBER, "%d of %d pages last asked %s ago" % (
            sum(1 for a in ages if a > bound), len(ages),
            age_phrase(oldest / 24))
    return GREEN, "fetched %s ago" % age_phrase(newest / 24)


CADENCE_WORD = {"every_run": "sweep", "twice_daily": "6 hours",
                "daily": "day", "once": "—", "as played": "—",
                "as dealt": "—"}


def feed_lines() -> list[str]:
    now = run_now()
    reg, feeds = _hosts(), _feed_state()
    asked, quiet = _fetched(), stale_feeds()
    rows = []
    for name in sorted(FILLS):
        if name == "points":
            files = sorted(LIVE.glob("perjornada_*.csv"))
            path = files[-1] if files else (LIVE / "perjornada_none.csv")
        else:
            path = TIDY / f"{name}.csv"
        col = STAMPED.get(name, "observed_at")
        n_rows, newest = table_stats(path, col)
        days, seen = _age(newest, now)

        host, cadence, pages = reg.get(name, (None, None, set()))
        if host is None or name in HOSTS:
            host = (host or HOSTS.get(name, ("—", ""))[0])
            cadence = HOSTS.get(name, (None, cadence or "derived"))[1]
        if name in DERIVED:
            host, cadence = "src/crosswalk.py", "derived"
        cadence = _as_it_happens().get(name, cadence)

        light, state = _light(cadence, asked.get(name, []), name in quiet)
        if days is None and cadence != "derived":
            light, state = RED, "**never answered**"
        why = sorted({feeds[k] for k in pages if k in feeds})
        if why and light != GREEN:
            state += " — " + "; ".join(why)
        rows.append("| %s | %s | %s | %s | %s | %s | %s |" % (
            light, name, FILLS[name],
            host + (" ×%d" % len(pages) if len(pages) > 1 else ""),
            "{:,}".format(n_rows), seen, state))

    return ["## Where the numbers come from", "",
            "🟢 asked for within its own cadence · 🟡 it has missed a turn and "
            "what you are reading is the last answer · 🔴 the readers have "
            "dropped it and the report is on its fallback · ⚪ not fetched at "
            "all, built here from the rest.", "",
            "**Newest row** is the snapshot that carried the reading, which is "
            "not when it was fetched: a page nobody asked for is carried into "
            "the next sweep and re-stamped. The light is on the asking.", "",
            "| | Table | What it is used for | Fetched from | Rows | "
            "Newest row | Fetching |",
            "|---|---|---|---|--:|---|---|"] + rows + [""]


def elo_basis() -> str:
    from ffcore.fixture import elo_strength, team_strength

    rows = load_elo()
    if not rows:
        return ("summed squad value — %s, so the wallet is standing in for "
                "the pitch (see the feed table for how long)"
                % ("Club Elo has stopped answering and its last reading is "
                   "too old to rank a jornada it predates"
                   if read_csv(TIDY / "elo.csv") else
                   "Club Elo has not been scraped yet"))
    teams = list(team_strength(latest_market()))
    if elo_strength(teams, rows) is None:
        return ("summed squad value — Club Elo was scraped but did not cover "
                "every club in the market, and half a league ranked by Elo is "
                "not a ranking")
    return "**Club Elo rating**, a result-based rating with no transfer fees in it"


def latest_market() -> list[dict]:
    from ffcore.tidy import load_market_latest

    return load_market_latest()


def formula_lines() -> list[str]:
    from ffcore.fixture import attack_defense, fit_home_edge
    from ffcore.tidy import load_crosswalk, load_results_history

    results_hist = load_results_history()
    matches = load_matches()
    home_edge, home_edge_why = fit_home_edge(results_hist, matches)
    teams = sorted({r.get("team") for r in latest_market() if r.get("team")})
    xw = load_crosswalk()
    slug_of = {c.market: c.ff_slug for c in xw.clubs.values()
              if c.market and c.ff_slug} if xw is not None else {}
    ad = attack_defense(results_hist, list(slug_of.values())) if slug_of \
        else {}
    fixture_line = (
        f"| Fixture factor | per-club attack/defense fitted from real "
        f"goals ({len(ad)} of {len(teams)} clubs; the rest fall back to "
        f"±{FIX_BAND * 100:.0f}% by squad-value rank, MIN_AD_MATCHES not "
        f"yet met) | yes, for {len(ad)} of {len(teams)} |")
    return [
        "### The model, as configured right now", "",
        "| Term | Setting | Fitted? |",
        "|---|---|---|",
        "| Formula | `xPts/j = shrunk pts-per-match × fixture × P(start)` | "
        "— |",
        f"| Shrinkage | K = {SHRINK_K:g} matches, applied twice: last season "
        "toward the positional prior, then this season toward that | yes |",
        fixture_line,
        f"| Home advantage | +{home_edge * 100:.1f}% | yes — {home_edge_why} |",
        f"| Team strength | {elo_basis()} | — |",
        f"| P(start) read from | `{LINEUP_SOURCE}` | see the Brier table |",
        "| Fixture applies to | fielding only — never a buy, a sale or the "
        "line | — |",
        "| Season spread, match to match | each round resamples a real "
        "per-match score, rescaled to the player's rate | from %d observed "
        "matches |" % getattr(_fc(), "_real_n", 0),
        "| Season spread, RATE ERROR | the rate is a mean of a few matches, "
        "so each simulated season multiplies it by one draw of "
        "cv/√(matches+K) held all year — median %s across the squads | "
        "derived, not fitted |" % _rate_note(), "",
    ]



def column_guide_lines() -> list[str]:
    return [
        "### How to read the tables", "",
        "**The ladder** — one table, read top to bottom: a plan, not a "
        "menu. The funding is implicit — sell the SELL rows and the BUY "
        "rows are what the money reaches. `Start` is the probable-XI "
        "read, recalibrated and blended, the same figure the forecast "
        "multiplies by. `xPts/j` already has that applied. `€` on a KEEP "
        "or SELL row is what it raises; on a SAVE row, how far short you "
        "are. On a BUY or RAID row it is his market price, plus (on a "
        "RAID) the extra premium a clause forces above it — his real "
        "worth and what taking him costs, not a net cash figure: a raid "
        "can leave you holding more cash than it spent, and reading that "
        "surplus as the price would say the opposite of what happened. "
        "`Season` is "
        "simulated: extra points over the jornadas left, the same "
        "seasons with and without the move — on a KEEP or SELL row, "
        "\"without\" is his best REAL replacement, not nothing, so a "
        "negative number there can still mean keep him: his own best "
        "alternative costs more than he does, not that he scores less "
        "than zero. `pts/M€` is Season "
        "per million actually spent — how CHEAPLY a gain arrived, not "
        "how big it is, so read Season first: there are only eleven "
        "starting shirts, and a tiny gain at a tiny price can still "
        "carry a flattering rate. A dash means the move is net "
        "cash-neutral-or-positive (nothing to divide by) or, on a SAVE "
        "row, a shortfall nobody can act on yet. `Where` names who "
        "holds him, and on a payable clause also the rival closest to "
        "affording it — a first name and `today`/`~Nd`, an ESTIMATE "
        "off their reconstructed balance, the app's daily allowance, "
        "and how fast that manager has actually raised money this "
        "season, never a prediction he actually wants the player. "
        "`PAR` is " + PAR_DEFINITION + " — comparable across "
        "positions on that basis, and a different question from "
        "`Season`: a good player in a deep position can show a modest "
        "PAR while still being the right one to keep.", "",
        "**The league table** — `Pts` is the real league total today. "
        "`Season` is the simulated total and its 10-90 band — a mean "
        "with no band beside it reads as a prediction it is not. "
        "`P(above)` is how often the simulation has you finish above "
        "them.", "",
    ]


def _fc():
    try:
        import decide
        return decide.load().forecaster
    except Exception:                                    # pragma: no cover
        return None


_RATE_NOTE: list = []


def _rate_note() -> str:
    if _RATE_NOTE:
        return _RATE_NOTE[0]
    import statistics
    fc = _fc()
    rel = sorted(getattr(fc, "rate_rel", {}).values())
    _RATE_NOTE.append("±%.0f%% of a rate" % (100 * statistics.median(rel))
                      if rel else "not applied — no match counts")
    return _RATE_NOTE[0]


def start_lines() -> list[str]:
    intervals, graded, ungraded = load_starts()
    out: list[str] = []
    if not intervals:
        if ungraded:
            NOT_GRADED.append(
                "| jornada " + ", ".join(str(j) for j in ungraded)
                + " — confirmed elevens exist but no kickoff was observed "
                  "before the round locked | all |")
        return out

    numbered, named, skipped = start_grade(
        intervals, load_lineups(source="") + forecast_claims(),
        load_universe())
    if not numbered and not named:
        return out

    out += [f"| **starts** — {graded} confirmed, {len(intervals)} locked "
            "round(s) | | | | |"]
    for src, n, claim, rate, brier in numbered:
        mark = " ←read" if src == LINEUP_SOURCE else ""
        out.append(f"| {src}{mark} | {n} | {claim:.0f}% | {rate:.0f}% | "
                   f"{brier:.3f} |")
    for src, n, rate in named:
        out.append(f"| {src} — named, no number | {n} | — | {rate:.0f}% | — |")
    if skipped:
        NOT_GRADED.append(f"| within {START_EDGE:.0f} points of 50%, on "
                          f"starts | {skipped} |")
    if ungraded:
        NOT_GRADED.append(
            "| jornada " + ", ".join(str(j) for j in ungraded)
            + " — its opener kicked off before this repo saw a kickoff for "
              "it, so there is no honest cutoff | all |")

    ours = forecast_claims()
    universe = load_universe()
    our_instances = _start_instances(intervals, ours, "our forecast", universe)
    our_names = {norm(c["player_name"]) for c in ours}
    if our_names and our_instances:
        restricted = [c for c in load_lineups(source="")
                     if norm(c.get("player_name", "")) in our_names]
        fair_num, _fair_named, _fair_skip = start_grade(
            intervals, restricted + ours, universe, instances=our_instances)
        if len(fair_num) > 1:
            out += ["", f"| **starts, same population AND same instances as "
                    "our forecast only** — the real fair comparison "
                    f"(n={len(our_instances)} claims) | | | | |"]
            for src, n, claim, rate, brier in fair_num:
                mark = " ←read" if src == LINEUP_SOURCE else ""
                out.append(f"| {src}{mark} | {n} | {claim:.0f}% | "
                           f"{rate:.0f}% | {brier:.3f} |")
            ours_row = next((r for r in fair_num if r[0] == "our forecast"),
                            None)
            if ours_row is not None:
                our_briers = _instance_briers(intervals, restricted + ours,
                                              "our forecast", our_instances)
                for src, *_rest in fair_num:
                    if src == "our forecast":
                        continue
                    rival_briers = _instance_briers(
                        intervals, restricted + ours, src, our_instances)
                    gap = stats.bootstrap_gap(our_briers, rival_briers)
                    if gap is None:
                        continue
                    verdict = ("beats it" if gap["beats"]
                              else "no significant difference")
                    out.append(f"_vs {src}: 90% CI on the Brier gap "
                              f"{gap['lo']:+.3f} to {gap['hi']:+.3f} — "
                              f"{verdict}._")
                out.append("")
    out.append(f"_Every Brier above excludes claims within {START_EDGE:.0f} "
              "points of 50% — a source's hardest, most genuinely uncertain "
              "calls, which never enter any number shown here._")
    out.append("")
    return out


def _instance_briers(intervals, claims, src, instances) -> list[float]:
    per = _group_by_key(claims, src=src)

    out = []
    for interval in intervals:
        start, played = interval[0], interval[1]
        for key, hist in per.items():
            if (key, start) not in instances:
                continue
            row = latest_before(hist, start)
            if row is None:
                continue
            try:
                pct = float(row.get("start_pct"))
            except (TypeError, ValueError):
                continue
            slug = schema.text(row, "player_slug")
            hit = 1.0 if key in played or (slug and slug in played) else 0.0
            out.append((pct / 100.0 - hit) ** 2)
    return out


def forecast_claims() -> list[dict]:
    from ffcore.crosswalk import Crosswalk

    xw = Crosswalk.read(TIDY / "players.csv", TIDY / "clubs.csv")
    out = []
    for r in read_csv(DECISIONS / "squad_log.csv"):
        try:
            pct = float(r["start_pct"])
        except (KeyError, ValueError, TypeError):
            continue
        if not r.get("player") or not r.get("observed_at"):
            continue
        team_slug = ""
        p = xw.players.get(schema.text(r, "ff_id"))
        if p:
            club = xw.clubs.get(p.club_id)
            team_slug = club.ff_slug if club else ""
        out.append({"source": "our forecast", "player_name": r["player"],
                    "observed_at": r["observed_at"], "start_pct": pct,
                    "team_slug": team_slug})
    return out


def golden_rows() -> list[dict]:
    actuals, _label = load_actuals()
    rate_by_key: dict[tuple[str, int], dict] = {}
    for p in pair(actuals, load_predictions()):
        if p.get("jornada") is not None:
            rate_by_key[(norm(p["name"]), p["jornada"])] = p

    jornada_of_lock = {when: jor for (jor, _team), when
                       in clock_history().team_locks.items()}

    intervals, _graded, _ungraded = load_starts()
    squad_of = {lock: keys for lock, keys, _t
                in load_starts(("starter", "sub"))[0]}
    per = _group_by_key(forecast_claims())

    out = []
    for lock, played, teams in intervals:
        jor = jornada_of_lock.get(lock)
        if jor is None:
            continue
        for key, hist in per.items():
            row = _matched_start_row(hist, lock, teams)
            if row is None:
                continue
            golden = {"player": row["player_name"], "jornada": jor,
                      "predicted_start_pct": row["start_pct"],
                      "actual_started": key in played,
                      "in_squad": key in squad_of.get(lock, ()),
                      "predicted_rate": None, "actual_points": None,
                      "rate_err": None}
            rp = rate_by_key.get((key, jor))
            if rp is not None:
                golden["predicted_rate"] = rp["per_match"]
                golden["actual_points"] = rp["actual"]
                golden["rate_err"] = rp["err"]
            out.append(golden)
    return out


def baseline_check(golden: list[dict]) -> dict | None:
    if not golden:
        return None
    n = len(golden)
    mean_claim = sum(r["predicted_start_pct"] for r in golden) / n
    ours_terms = [(r["predicted_start_pct"] / 100 - r["actual_started"]) ** 2
                 for r in golden]
    coin_terms = [(0.5 - r["actual_started"]) ** 2 for r in golden]
    const_terms = [(mean_claim / 100 - r["actual_started"]) ** 2
                  for r in golden]
    return {"n": n, "mean_claim": mean_claim,
           "ours": sum(ours_terms) / n, "coin_flip": sum(coin_terms) / n,
           "constant": sum(const_terms) / n,
           "vs_coin": stats.bootstrap_gap(ours_terms, coin_terms),
           "vs_constant": stats.bootstrap_gap(ours_terms, const_terms)}


def source_lines(actuals: list[dict]) -> list[str]:
    NOT_GRADED.clear()
    starts = start_lines()
    head = ["### Who to believe about the eleven", "",
            "| Source | Calls | Mean claim | Hit | Brier |",
            "|---|--:|--:|--:|--:|"]
    rows = list(starts)

    intervals = appearances(actuals)
    numbered, named, skipped = ([], [], 0) if not intervals else start_grade(
        intervals, load_lineups(source="") + forecast_claims(),
        load_universe())
    if numbered or named:
        rows.append("| **appearances** — the wider, blunter sample; a "
                    "20-minute substitute counts | | | | |")
        for src, n, claim, rate, brier in numbered:
            mark = " ←read" if src == LINEUP_SOURCE else ""
            rows.append(f"| {src}{mark} | {n} | {claim:.0f}% | {rate:.0f}% | "
                        f"{brier:.3f} |")
        for src, n, rate in named:
            rows.append(f"| {src} — named, no number | {n} | — | {rate:.0f}% "
                        "| — |")
        if skipped:
            NOT_GRADED.append(f"| within {START_EDGE:.0f} points of 50%, on "
                              f"appearances | {skipped} |")

    if not rows:
        return head[:2] + [
            f"| `{LINEUP_SOURCE}` | 0 | — | — | — |", "",
            "_Read because it was first, not because it won anything._", ""]

    out = head + rows + [""]
    if NOT_GRADED:
        out += ["| Not graded | Calls |", "|---|--:|"] + NOT_GRADED + [""]
    out += ["_Brier: mean squared error of the probability, 0 perfect and "
            "0.25 a coin flip. Claims are scored as last published before the "
            "round's first kickoff. Lower Brier **on starts** earns "
            "`LINEUP_SOURCE` in ffcore/tidy.py; appearances break ties only._",
            ""]
    check = baseline_check(golden_rows())
    if check is not None:
        vs_coin, vs_const = check["vs_coin"], check["vs_constant"]
        beats_coin = vs_coin is not None and vs_coin["beats"]
        beats_const = vs_const is not None and vs_const["beats"]
        if beats_coin and beats_const:
            verdict = "beats both, adding real information."
        elif not beats_coin and not beats_const:
            verdict = "does not clearly beat either trivial guess yet."
        else:
            verdict = ("beats one of the two trivial guesses but not the "
                      "other — a real, if partial, edge.")
        out += [f"_Our forecast vs. trivial baselines, n={check['n']} — a "
                "DIFFERENT sample than the tables above (this one joins the "
                "rate and start sides on jornada via golden_rows(), no "
                f"{START_EDGE:.0f}-point undecided-band exclusion): ours "
                f"{check['ours']:.3f}, a flat 50% guess "
                f"{check['coin_flip']:.3f}, a constant "
                f"{check['mean_claim']:.0f}% guess {check['constant']:.3f} "
                "(Brier, lower is better) — " + verdict + "_", ""]
    return out


def rate_baseline_check(pairs: list[dict]) -> dict | None:
    if not pairs:
        return None
    n = len(pairs)
    actual_rates = [p["actual"] / p["matches"] for p in pairs]
    mean_rate = sum(actual_rates) / n
    ours = [abs(p["predicted"] / p["matches"] - a)
           for p, a in zip(pairs, actual_rates)]
    naive = [abs(mean_rate - a) for a in actual_rates]
    gap = stats.bootstrap_gap(ours, naive)
    return {"n": n, "mean_rate": mean_rate,
           "ours": sum(ours) / n, "naive": sum(naive) / n, "gap": gap}


def weighted_mae(pairs: list[dict]) -> float:
    total_matches = sum(p["matches"] for p in pairs)
    return sum(abs(p["err"]) for p in pairs) / total_matches


ACCURACY_LOG = "forecast_accuracy_log.csv"


def log_forecast_accuracy(n: int, mae: float, naive_mae: float) -> None:
    from ffcore.tidy import DECISIONS, append_csv

    DECISIONS.mkdir(parents=True, exist_ok=True)
    append_csv(DECISIONS / ACCURACY_LOG,
              [{"observed_at": run_now().strftime("%Y-%m-%dT%H%MZ"),
                "n": n, "mae": "%.4f" % mae, "naive_mae": "%.4f" % naive_mae}],
              ["observed_at", "n", "mae", "naive_mae"])


def forecast_accuracy_history() -> list[dict]:
    from ffcore.tidy import DECISIONS, read_csv

    path = DECISIONS / ACCURACY_LOG
    if not path.exists():
        return []
    out = []
    for r in read_csv(path):
        try:
            out.append({"observed_at": r["observed_at"], "n": int(r["n"]),
                       "mae": float(r["mae"]), "naive_mae": float(r["naive_mae"])})
        except (KeyError, ValueError, TypeError):
            continue
    return out


def current_mae() -> float | None:
    hist = forecast_accuracy_history()
    return hist[-1]["mae"] if hist else None


def comparison_lines() -> list[str]:
    out = [f"### Forecast vs actual — last {WINDOW_DAYS} days", ""]
    actuals, label = load_actuals()
    if not actuals:
        out += ["| Player-intervals scored | 0 |", "|---|--:|",
                "| Why | no completed jornada in the window yet |", ""]
        return out

    pairs = pair(actuals, load_predictions())
    if not pairs:
        out += ["| Player-intervals scored | 0 |", "|---|--:|",
                f"| Per-jornada rows for {label} | {len(actuals)} |",
                "| Why none matched | no prediction was logged before the "
                "matches; squad_log.csv starts once report.py has run with a "
                "roster |", ""]
        return out

    n = len(pairs)
    tp = sum(p["predicted"] for p in pairs)
    ta = sum(p["actual"] for p in pairs)
    mae = weighted_mae(pairs)
    fx, no_fix = fixture_rows(pairs)
    over = sum(1 for p in pairs if p["err"] > 0)
    under = sum(1 for p in pairs if p["err"] < 0)
    mean_signed = sum(p["err"] for p in pairs) / n
    out += [
        "| Measure | Value |", "|---|--:|",
        f"| Player-intervals scored ({label}) | {n} |",
        f"| Predicted, total | {tp:.0f} pts |",
        f"| Actual, total | {ta:.0f} pts |",
        f"| **Mean absolute error (per match played)** | **{mae:.1f} pts** |",
        f"| Pairs predating the fixture term | {no_fix} of {n} |", "",
        "_Read every xPts/j in this report as ± the error above, at least. "
        "Only predictions logged before an interval are scored, so hindsight "
        "is excluded by construction; the sample is your own squad and grows "
        "about 15 pairs a jornada._", "",
        f"_{over} of {n} intervals overpredicted, {under} underpredicted "
        f"(mean signed error {mean_signed:+.1f} pts) — the \"Biggest miss\" "
        "table below is the tail, not the whole picture._", "",
    ]
    rbc = rate_baseline_check(pairs)
    if rbc is not None:
        gap = rbc["gap"]
        if gap is not None and gap["beats"]:
            verdict = "beats it, adding real information."
        elif gap is not None:
            verdict = (f"does not clearly beat it yet (90% CI on the gap: "
                      f"{gap['lo']:+.2f} to {gap['hi']:+.2f} pts, straddles "
                      "zero).")
        else:
            verdict = "not enough data to say."
        out += [f"_vs. a trivial guess (everyone scores the sample's own "
                f"mean, {rbc['mean_rate']:.1f} pts/match, no player identity "
                f"at all): ours {rbc['ours']:.2f} MAE, that guess "
                f"{rbc['naive']:.2f} MAE — " + verdict + "_", ""]
        log_forecast_accuracy(n, rbc["ours"], rbc["naive"])
    buckets = bucket_rows(pairs)
    if buckets and min(cnt for _, cnt, _, _ in buckets) >= MIN_BUCKET_N:
        out += [
            "| Forecast bucket | n | Mean forecast | Mean actual |",
            "|---|--:|--:|--:|",
        ]
        for label_, cnt, mp, ma in buckets:
            out.append(f"| {label_} | {cnt} | {mp:.1f} | {ma:.1f} |")
        out.append("")
    else:
        out += [f"_Not enough data yet to break out by scoring band (needs "
                f"{MIN_BUCKET_N}+ per bucket) — will start showing once more "
                "jornadas have locked._", ""]

    if fx:
        out += ["| Next fixture (the blended factor actually applied — "
                "attack/defense where fitted, else the "
                f"±{FIX_BAND*100:.0f}% rank fallback) | n | Mean "
                "forecast | Mean actual | Error |",
                "|---|--:|--:|--:|--:|"]
        for label_, cnt, mp, ma, me in fx:
            out.append(f"| {label_} | {cnt} | {mp:.1f} | {ma:.1f} | "
                       f"{me:+.1f} |")
        out += ["", "_Per player-match. Positive error on **easier** together "
                "with negative on **harder** means the band is too wide; the "
                "reverse, too narrow; both near zero, about right. Judge "
                "nothing on a single-digit n._", ""]

    out += ["| Biggest miss | Forecast | Actual | Error |",
            "|---|--:|--:|--:|"]
    for p in sorted(pairs, key=lambda p: -abs(p["err"]))[:5]:
        out.append(f"| {p['name']} | {p['predicted']:.1f} | "
                   f"{p['actual']:.0f} | {p['err']:+.1f} |")
    out.append("")
    return out


def drift_lines() -> list[str]:
    fitted, why = drift_frac_from_history()
    from ffcore.forecast import DRIFT_FRAC as _DEFAULT
    out = ["### Season-long drift", ""]
    if fitted == _DEFAULT and "not enough" in why:
        out += [f"Still the unfitted default ({_DEFAULT:.2f}) — {why}.", ""]
    else:
        out += [f"**Fit from real data this run: {fitted:.2f}** ({why}).", ""]
    return out


def rate_rel_floor_lines(pool) -> list[str]:
    fitted, why = fit_rate_rel_floor(pool)
    from ffcore.forecast import RATE_REL_FLOOR as _DEFAULT
    out = ["### Rate uncertainty floor", ""]
    if fitted == _DEFAULT and ("too few" in why or "0 " in why):
        out += [f"Still the stated default ({_DEFAULT:.2f}) — {why}.", ""]
    else:
        out += [f"**Fit from real data this run: {fitted:.2f}** ({why}).", ""]
    return out


def main() -> None:
    out = ["# How the forecast works — and how it's doing", ""]
    out += feed_lines()
    out += formula_lines()
    out += column_guide_lines()
    out += comparison_lines()
    out += drift_lines()
    fc = _fc()
    if fc is not None:
        out += rate_rel_floor_lines(fc.pool)
    out += source_lines(load_actuals()[0])
    PARTS.mkdir(parents=True, exist_ok=True)
    write_lines(PARTS / "methodology.md", out)
    print(f"wrote {PARTS / 'methodology.md'} ({len(out)} lines)")



def _selftest() -> None:
    assert _light("every_run", [0.5], False)[0] == GREEN
    assert _light("twice_daily", [17.6], False)[0] == AMBER
    assert _light("twice_daily", [0.2, 30.0], False)[0] == AMBER
    assert "1 of 2 pages" in _light("twice_daily", [0.2, 30.0], False)[1]
    assert _light("every_run", [80.0], False)[0] == RED
    assert _light("every_run", [1.0], True)[0] == RED
    assert _light("every_run", [], False)[0] == RED
    assert _light("derived", [], False)[0] == GREY

    utc = dt.timezone.utc
    t = lambda d, h=0: dt.datetime(2026, 8, d, h, tzinfo=utc)  # noqa: E731

    def f(score, fix=None):
        return {"score": score, "fix": fix}

    preds = {"ane": [(t(10), f(2.0)), (t(14), f(3.0, 1.10)),
                     (t(16), f(9.9))],
             "bo": [(t(14), f(1.5, 0.90))]}

    actuals = [
        {"name": "Ane", "keys": ["ane"], "from_dt": t(15),
         "points_delta": 8.0, "games_delta": 1.0},
        {"name": "Bo", "keys": ["bo"], "from_dt": t(15),
         "points_delta": 4.0, "games_delta": 2.0},
        {"name": "Cai", "keys": ["cai"], "from_dt": t(15),
         "points_delta": 5.0, "games_delta": 1.0},
        {"name": "Didi", "keys": ["didi"], "from_dt": t(15),
         "points_delta": 1.0, "games_delta": 0.0},
    ]
    got = pair(actuals, preds)
    assert [g["name"] for g in got] == ["Ane", "Bo"], got
    ane, bo = got
    assert ane["predicted"] == 3.0 and ane["err"] == -5.0, ane
    assert bo["predicted"] == 3.0 and bo["matches"] == 2.0, bo

    assert latest_before(preds["bo"], t(14)) is None
    assert latest_before(preds["bo"], t(14, 1))["score"] == 1.5

    rows = bucket_rows(got)
    assert [r[0] for r in rows] == ["under 2", "3–4"], rows

    fx, no_fix = fixture_rows(got)
    assert [r[0] for r in fx] == ["harder", "easier"], fx
    assert no_fix == 0
    hard, easy = fx
    assert hard[1] == 1 and abs(hard[4] - (1.5 - 2.0)) < 1e-9, hard
    assert easy[1] == 1 and abs(easy[4] - (3.0 - 8.0)) < 1e-9, easy

    old = pair([{"name": "Ane", "keys": ["ane"], "from_dt": t(11),
                 "points_delta": 4.0, "games_delta": 1.0}], preds)
    fx2, no_fix2 = fixture_rows(old)
    assert fx2 == [] and no_fix2 == 1, (fx2, no_fix2)

    locks3 = {1: t(20), 2: t(14), 3: t(10)}
    assert lock_order(locks3) == [3, 2, 1], lock_order(locks3)

    preds3 = {"eli": [(t(9), f(1.0)), (t(11), f(2.0)), (t(15), f(3.0)),
                      (t(19), f(4.0)), (t(21), f(9.9))]}
    actuals3 = [{"name": "Eli", "keys": ["eli"], "from_dt": t(20, 1),
                "points_delta": 3.0, "games_delta": 1.0, "jornada": 1}]
    lag0 = lagged_pair(actuals3, preds3, locks3, 0)
    assert len(lag0) == 1 and lag0[0]["predicted"] == 4.0, lag0
    lag1 = lagged_pair(actuals3, preds3, locks3, 1)
    assert len(lag1) == 1 and lag1[0]["predicted"] == 2.0, lag1
    lag2 = lagged_pair(actuals3, preds3, locks3, 2)
    assert len(lag2) == 1 and lag2[0]["predicted"] == 1.0, lag2
    assert lagged_pair(actuals3, preds3, locks3, 3) == []

    # Pins the CONTRACT, not a constant. This asserted `fitted == 1.0`,
    # which was only ever true because the fitter returned the module
    # default whenever it could not measure compounding -- the behaviour
    # that put an unfitted 1.00 into every band. What must hold is that a
    # real number comes back with a reason attached, and that it lands
    # inside the range this estimator can actually produce: sqrt() of a
    # bootstrapped variance growth, which cannot sensibly exceed 1.
    fitted, why = drift_frac_from_history()
    assert isinstance(fitted, float) and 0.0 <= fitted <= 1.0, (fitted, why)
    assert isinstance(why, str) and why, (fitted, why)

    played = [{"name": "Ane", "keys": ["ane"], "from_dt": t(15),
               "points_delta": 8.0, "games_delta": 1.0},
              {"name": "Bo", "keys": ["bo"], "from_dt": t(15),
               "points_delta": 4.0, "games_delta": 2.0},
              {"name": "Didi", "keys": ["didi"], "from_dt": t(15),
               "points_delta": 1.0, "games_delta": 0.0}]
    iv = appearances(played)
    assert [s for s, _ in iv] == [t(15)], iv
    assert iv[0][1] == {"ane", "bo"}, iv[0][1]
    assert "didi" not in iv[0][1]

    def claim(src, name, pct, when=14, role="starter"):
        return {"source": src, "player_name": name, "start_pct": pct,
                "role": role, "observed_at": when}

    claims = [claim("ff", "Ane", "90"), claim("ff", "Bo", "80"),
              claim("ff", "Cai", "20", role="doubt"),
              claim("af", "Ane", "90"), claim("af", "Bo", "80"),
              claim("af", "Cai", "90"),
              claim("ff", "Ane", "10", when=16),
              claim("ff", "Ghost", "90"),
              claim("ff", "Eve", "55"),
              claim("af", "Fay", "", role="starter"),
              claim("af", "Gus", "", role="doubt")]
    for c in claims:
        c["observed_at"] = ("2026-08-%02dT1200Z" % c["observed_at"]
                            if isinstance(c["observed_at"], int)
                            else c["observed_at"])

    universe = {"ane", "bo", "cai", "eve", "fay", "gus"}
    num, named, skipped = start_grade(iv, claims, universe)
    got = {s: (n, round(b, 3)) for s, n, _, _, b in num}
    assert got["ff"][0] == 3 and got["af"][0] == 3, got
    assert got["af"][1] > got["ff"][1], got
    assert got["ff"][1] < 0.05, got
    ff = next(r for r in num if r[0] == "ff")
    assert abs(ff[2] - (90 + 80 + 20) / 3) < 1e-9, ff
    assert abs(ff[3] - 200.0 / 3) < 1e-9, ff
    assert all(n == 3 for _, n, _, _, _ in num), num
    assert named == [("af", 1, 0.0)], named
    assert skipped == 2, skipped
    assert start_grade([], claims, universe) == ([], [], 0)

    matches = [{"match_id": "1", "jornada": "1", "home": "alaves",
                "away": "getafe", "score": "3-0"},
               {"match_id": "2", "jornada": "1", "home": "espanyol",
                "away": "levante", "score": "1-0"},
               {"match_id": "9", "jornada": "2", "home": "rayo-vallecano",
                "away": "alaves", "score": "2-2"}]
    fixtures = [{"kickoff": "2026-08-16T17:00:00+00:00", "home": "Espanyol",
                 "away": "Levante"},
                {"kickoff": "2026-08-15T19:30:00+00:00", "home": "Alaves",
                 "away": "Getafe"}]
    assert team_slug_of("Racing Santander", {"racing", "real-madrid"}) \
        == "racing"
    assert team_slug_of("Real Betis", {"betis", "real-sociedad"}) == "betis"
    assert team_slug_of("Nowhere FC", {"racing"}) is None
    locks = JornadaClock(matches, fixtures).round_locks
    assert list(locks) == [1] and locks[1].day == 15, locks
    assert 2 not in locks

    def start(match, name, slug, role="starter", team="alaves"):
        return {"match_id": match, "player_name": name, "player_slug": slug,
                "role": role, "team_slug": team}

    xi = [start("1", "Ane", "ane-slug"), start("1", "Bo", "bo-slug"),
          start("1", "Bo", "bo-slug"),
          start("2", "Cai", "cai-slug", team="levante"),
          start("2", "Dee", "dee-slug", role="sub"),
          start("9", "Eve", "eve-slug")]
    mlocks = JornadaClock(matches, fixtures).team_locks
    iv2, graded, ungraded = start_intervals(matches, xi, fixtures)
    assert graded == 3, graded
    assert ungraded == [2], ungraded
    assert len(iv2) == 2, iv2
    by_lock = {lock: (keys, teams) for lock, keys, teams in iv2}
    alaves_keys, alaves_teams = by_lock[mlocks[(1, "alaves")]]
    levante_keys, levante_teams = by_lock[mlocks[(1, "levante")]]
    assert mlocks[(1, "alaves")] == locks[1]
    assert mlocks[(1, "levante")] != locks[1]
    assert alaves_keys == {"ane", "ane-slug", "bo", "bo-slug"}, alaves_keys
    assert "dee" not in alaves_keys and "eve" not in alaves_keys
    assert alaves_teams == {"alaves"}, alaves_teams
    assert levante_keys == {"cai", "cai-slug"}, levante_keys
    assert levante_teams == {"levante"}, levante_teams

    market = [{"name": "abdel abqar", "team": "Alaves"},
              {"name": "abdel abqar", "team": "Alaves"},
              {"name": "ivan romero", "team": "Alaves"},
              {"name": "rafael romero", "team": "Alaves"},
              {"name": "someone else", "team": "Barcelona"}]
    iv3, _, _ = start_intervals(
        matches, [start("1", "Abqar", "abqar-slug"),
                  start("1", "Romero", "romero-slug")], fixtures, market)
    assert "abdel abqar" in iv3[0][1], iv3[0][1]
    assert "ivan romero" not in iv3[0][1] \
        and "rafael romero" not in iv3[0][1], iv3[0][1]
    assert set(market_names(market, {"alaves"})) == {"alaves"}

    slugged = [{"source": "ff", "player_name": "Whoever They Call Him",
                "player_slug": "ane-slug", "start_pct": "90", "role": "starter",
                "team_slug": "alaves", "observed_at": "2026-08-14T1200Z"}]
    num2, _, _ = start_grade(iv2, slugged, None)
    assert num2 == [("ff", 1, 90.0, 100.0, (0.9 - 1) ** 2)], num2
    absent = dict(slugged[0], player_slug="zed-slug", team_slug="barcelona")
    assert start_grade(iv2, [absent], None) == ([], [], 0)
    assert start_intervals([], [], []) == ([], 0, [])

    guide = "\n".join(column_guide_lines())
    for heading in ("The ladder", "The league table"):
        assert heading in guide, heading
    for term in ("pts/M€", "P(above)"):
        assert term in guide, term
    for term in ("market price", "premium"):
        assert term in guide, term
    assert "vs X" not in guide, guide
    for heading in ("Field these eleven", "What to bid", "Fitness",
                   "Starting"):
        assert heading not in guide, heading

    import tempfile
    global DECISIONS, TIDY
    real_decisions, real_tidy = DECISIONS, TIDY
    tmp = tempfile.mkdtemp()
    try:
        DECISIONS = __import__("pathlib").Path(tmp)
        TIDY = DECISIONS
        write_csv(DECISIONS / "squad_log.csv", [
            {"observed_at": "2026-08-10T1200Z", "player": "Nailed",
             "start_pct": "90", "ff_id": "nailed"},
            {"observed_at": "2026-08-10T1200Z", "player": "Benched",
             "start_pct": "85", "ff_id": "benched"},
            {"observed_at": "2026-08-10T1200Z", "player": "NoNumber",
             "start_pct": "", "ff_id": "nonumber"},
            {"observed_at": "", "player": "NoStamp", "start_pct": "50",
             "ff_id": "nostamp"},
        ], ["observed_at", "player", "start_pct", "ff_id"])
        write_csv(TIDY / "players.csv",
                 [{"player_id": "nailed", "name": "Nailed", "club_id": "fc"},
                  {"player_id": "benched", "name": "Benched", "club_id": "fc"}],
                 ["player_id", "name", "club_id", "ff_slug", "af_slug",
                  "app_id", "understat_id", "app_names"])
        write_csv(TIDY / "clubs.csv",
                 [{"club_id": "fc", "market": "FC", "ff_slug": "fc-slug"}],
                 ["club_id", "market", "ff_slug", "elo", "market_id",
                  "af_id", "aliases"])
        claims = forecast_claims()
    finally:
        DECISIONS, TIDY = real_decisions, real_tidy
    assert {c["player_name"] for c in claims} == {"Nailed", "Benched"}, claims
    got = {c["player_name"]: c["start_pct"] for c in claims}
    assert got == {"Nailed": 90.0, "Benched": 85.0}, got
    assert all(c["source"] == "our forecast" for c in claims), claims
    assert {c["player_name"]: c["team_slug"] for c in claims} == \
        {"Nailed": "fc-slug", "Benched": "fc-slug"}, claims

    iv_f = [(snapshot_stamp("2026-08-10T1800Z"), {"nailed"}, {"fc-slug"})]
    numf, _namf, _skipf = start_grade(iv_f, claims)
    row = next(r for r in numf if r[0] == "our forecast")
    _src, n_f, claim_pct, hit_pct, brier = row
    assert n_f == 2, row
    assert hit_pct == 50.0, row
    assert brier > 0.0, row
    iv_other = [(snapshot_stamp("2026-08-10T1800Z"), {"nailed"},
                {"some-other-club"})]
    num_other, _, _ = start_grade(iv_other, claims)
    assert not any(r[0] == "our forecast" for r in num_other), num_other

    golden = golden_rows()
    checked = [r for r in golden if r["predicted_rate"] is not None]
    assert checked, "golden_rows() must find at least one fully-joined row"
    bad = [r for r in checked
          if not r["in_squad"] and r["actual_points"] != 0.0]
    assert len(bad) <= max(2, len(checked) // 8), (len(bad), len(checked), bad)
    print(f"  golden_rows(): {len(golden)} rows, {len(checked)} fully "
         f"joined, {len(bad)} scored without a lineup row")

    gd = golden_dataset()
    total_rows = sum(len(v) for v in gd.values())
    assert total_rows >= 30, (
        "golden_dataset() should reach the same order of magnitude as "
        "pair()'s own real-data join (40-60 rows this season as of "
        "2026-09) — a much smaller number means the shared join itself "
        "broke, not that a hypothesis's own data is thin: %d" % total_rows)
    assert len(gd) >= 3, gd
    sample_row = next(iter(next(iter(gd.values())).values()))
    for field in ("score", "fix", "ppm", "flat", "home", "pos", "status",
                 "actual", "games"):
        assert field in sample_row, (field, sample_row)
    print(f"  golden_dataset(): {total_rows} rows across {len(gd)} jornadas")

    assert baseline_check([]) is None
    perfect = [{"predicted_start_pct": 100.0, "actual_started": True}] * 5
    chk = baseline_check(perfect)
    assert chk["ours"] == 0.0, chk
    assert chk["coin_flip"] == 0.25, chk
    always_wrong = [{"predicted_start_pct": 90.0, "actual_started": False}] * 3
    chk2 = baseline_check(always_wrong)
    assert chk2["ours"] > chk2["coin_flip"], chk2

    fair = start_lines()
    if any("same population as our forecast" in ln for ln in fair):
        i = next(i for i, ln in enumerate(fair)
                if "same population as our forecast" in ln)
        block = "\n".join(fair[i:])
        assert "our forecast" in block, block

    assert rate_baseline_check([]) is None
    perfect_rate = [{"predicted": 6.0, "actual": 6.0, "matches": 1.0}] * 4
    rchk = rate_baseline_check(perfect_rate)
    assert rchk["ours"] == 0.0, rchk
    assert rchk["naive"] == 0.0, rchk
    always_off = [{"predicted": 9.0, "actual": 6.0, "matches": 1.0}] * 4
    rchk2 = rate_baseline_check(always_off)
    assert rchk2["ours"] > rchk2["naive"] == 0.0, rchk2

    import tempfile as _tempfile4
    from ffcore import tidy as _tidy4

    with _tempfile4.TemporaryDirectory() as _d4:
        _real_decisions4 = _tidy4.DECISIONS
        _tidy4.DECISIONS = __import__("pathlib").Path(_d4)
        try:
            assert forecast_accuracy_history() == []
            assert current_mae() is None
            log_forecast_accuracy(12, 3.25, 4.12)
            log_forecast_accuracy(14, 3.10, 4.05)
            hist = forecast_accuracy_history()
            assert [h["n"] for h in hist] == [12, 14], hist
            assert abs(hist[0]["mae"] - 3.25) < 1e-9, hist
            assert abs(hist[1]["naive_mae"] - 4.05) < 1e-9, hist
            assert len(hist) == 2, hist
            assert abs(current_mae() - 3.10) < 1e-9, current_mae()
        finally:
            _tidy4.DECISIONS = _real_decisions4

    print("methodology.py selftest OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        main()
