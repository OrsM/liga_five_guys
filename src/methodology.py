"""
methodology.py — how the forecast works, and how it is doing against reality.

Writes .runtime/parts/methodology.md (digest.py's last appendix section).
Two halves:

  1. The formula, in words — pulled from ffcore/score.py's own constants
     so the text can't drift from the code.
  2. Forecast vs actual — squad_log.csv predictions joined against
     realised points in data/season/live/perjornada_* (points.py), over
     the last WINDOW_DAYS days.

Join rule: for each per-jornada row where games went up, use the LAST
prediction logged strictly before the interval began (never hindsight).
Predicted points = score × games_delta.

Sample is your own squad only (squad_log's own scope) — thin for weeks;
an empty comparison prints as a fact, not an error.

Deps: stdlib only, except decide.py imports drift_frac_from_history()
at Bootstrap-construction time, and _fc() below lazily imports decide.py
back — the two never import each other at module top level.

    python src/methodology.py             # writes .runtime/parts/methodology.md
    python src/methodology.py --selftest  # pure join logic, no IO
"""

from __future__ import annotations

import datetime as dt
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import stats  # noqa: E402
from ffcore.fixture import FIX_BAND, HOME_EDGE  # noqa: E402
from ffcore.score import SHRINK_K  # noqa: E402
from ffcore.text import norm, resolve  # noqa: E402
from ffcore.tidy import (run_now,  # noqa: E402
                         DECISIONS, PARTS, LINEUP_SOURCE,  # noqa: E402
                         DAILY_FRESH_DAYS, EVERY_RUN_FRESH_DAYS,
                         SEASON, TIDY, age_phrase, load_elo,
                         stale_feeds,
                         load_lineups,
                         read_csv, snapshot_stamp, write_csv, write_lines,
                         team_slug_of, lock_order, JornadaClock)

LIVE = SEASON / "live"
WINDOW_DAYS = 21

# Rows for the one "not graded, and why" table. Filled by the graders as they
# discard a claim, so nothing is dropped silently: a source that looks good
# because half its calls were thrown away is the failure mode here.
NOT_GRADED: list[str] = []


# ---------------------------------------------------------------------------
# pure join logic — selftested below
# ---------------------------------------------------------------------------

def latest_before(preds: list[tuple[dt.datetime, dict]],
                  cutoff: dt.datetime) -> dict | None:
    """The last prediction logged strictly before `cutoff`, or None.

    `preds` must be sorted by timestamp ascending. Returns the whole factor
    dict — score, and the terms that produced it — so the caller can attribute
    an error rather than only measure it.
    """
    best = None
    for when, fac in preds:
        if when < cutoff:
            best = fac
        else:
            break
    return best


def pair(actuals: list[dict],
         preds: dict[str, list[tuple[dt.datetime, dict]]]) -> list[dict]:
    """Join realised per-jornada rows with the prediction that preceded them.

    actuals: parsed perjornada rows with keys `key`, `keys` (all name forms),
    `from_dt`, `points_delta`, `games_delta`. preds: {norm name: [(dt, factor
    dict)] sorted ascending}. Returns one dict per matched pair, carrying the
    factors that need grading alongside the error.
    """
    out = []
    for a in actuals:
        if a["games_delta"] < 1:
            continue
        fac = None
        for k in a["keys"]:
            hits = preds.get(k)
            if hits:
                fac = latest_before(hits, a["from_dt"])
            if fac is not None:
                break
        if fac is None:
            continue
        predicted = fac["score"] * a["games_delta"]
        out.append({
            "name": a["name"],
            "predicted": predicted,
            "actual": a["points_delta"],
            "per_match": fac["score"],
            "matches": a["games_delta"],
            "err": predicted - a["points_delta"],
            "fix": fac.get("fix"),
            # See load_actuals()'s own note — carried through for the same
            # future join, not read by anything here yet.
            "jornada": a.get("jornada"),
        })
    return out


def lagged_pair(actuals: list[dict],
                preds: dict[str, list[tuple[dt.datetime, dict]]],
                locks: dict[int, dt.datetime], lag: int) -> list[dict]:
    """`pair()`, generalised to a prediction made `lag` LOCKED JORNADAS
    before the one being graded, instead of always the freshest one —
    squad_log.csv already holds predictions at several lead times for
    the same outcome, so this tests a real multi-jornada-ahead forecast
    without waiting for new data. A jornada with fewer than `lag` earlier
    locked jornadas is skipped.
    """
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
        fac = None
        for k in a["keys"]:
            hits = preds.get(k)
            if hits:
                fac = latest_before(hits, cutoff)
            if fac is not None:
                break
        if fac is None:
            continue
        predicted = fac["score"] * a["games_delta"]
        out.append({"name": a["name"], "predicted": predicted,
                    "actual": a["points_delta"], "per_match": fac["score"],
                    "matches": a["games_delta"],
                    "err": predicted - a["points_delta"],
                    "jornada": j})
    return out


def drift_frac_from_history(lag1: int = 1, lag3: int = 3) -> tuple[float, str]:
    """Fits DRIFT_FRAC off real, already-elapsed multi-jornada-ahead
    forecasts (lagged_pair()), rather than waiting for new data.

    Uses one pooled rate_rel (dispersion of actual/predicted at lag1)
    instead of Bootstrap's real per-player rate_rel, since squad_log.csv
    logs no per-row rate_rel today — a disclosed simplification, not an
    invented number.
    Why: docs/notes/methodology.md#drift_frac_from_history--why-lag-not-a-new-column
    """
    from ffcore.forecast import fit_drift_frac

    matches = read_csv(TIDY / "matches.csv")
    fixtures = read_csv(TIDY / "fixtures.csv")
    locks = JornadaClock(matches, fixtures).round_locks
    actuals, _label = load_actuals()
    preds = load_predictions()

    h1 = lagged_pair(actuals, preds, locks, lag1)
    h3 = lagged_pair(actuals, preds, locks, lag3)
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


BUCKETS = [(-1e9, 2, "under 2"), (2, 3, "2–3"), (3, 4, "3–4"), (4, 1e9, "4+")]

# Below this, a bucket's mean-forecast/mean-actual gap is as likely noise
# as real miscalibration — print "not enough data" instead of a table a
# reader could over-read.
MIN_BUCKET_N = 20

# Where a fixture stops being a median one, for grading purposes only. Wide
# enough that a home game against an average side lands in "neutral".
FIX_EDGE = 0.03

FIX_BUCKETS = [(-1e9, 1.0 - FIX_EDGE, "harder"),
               (1.0 - FIX_EDGE, 1.0 + FIX_EDGE, "neutral"),
               (1.0 + FIX_EDGE, 1e9, "easier")]


def fixture_rows(pairs: list[dict]) -> tuple[list[tuple], int]:
    """[(label, n, mean forecast/match, mean actual/match, mean err/match)],
    plus how many pairs carried no fixture factor at all. Grades
    FIX_BAND: a too-wide fixture term over-forecasts easy games and
    under-forecasts hard ones; too narrow reverses the signs.
    """
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
    """(label, n, mean predicted per match, mean actual per match)."""
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


# ---------------------------------------------------------------------------
# grading the probable-XI sources — the gate for LINEUP_SOURCE
#
# Grades P(APPEAR) against points.py's per-jornada `games` diff, not
# P(start) — flatters both sources equally, so the comparison stays valid.
# Why: docs/notes/methodology.md#start-grade-appearance-vs-real-starters
# ---------------------------------------------------------------------------

# A claim inside this band of the middle is not a call either way, and neither
# source is graded on one: it is what they publish when they do not know.
START_EDGE = 10.0


def appearances(actuals: list[dict]) -> list[tuple[dt.datetime, set]]:
    """[(interval start, {keys of everyone who played in it})], ascending.

    Built from the same rows the forecast join uses. Anyone absent from an
    interval did not play in it — points.py emits movers only.
    """
    by_start: dict[dt.datetime, set] = {}
    for a in actuals:
        seen = by_start.setdefault(a["from_dt"], set())
        if a["games_delta"] >= 1:
            seen.update(a["keys"])
    return sorted(by_start.items())


def start_grade(intervals, claims, universe=None, instances=None):
    """Per source: did the players it called actually appear?

    Returns (numbered, named, skipped):

      numbered  (source, n, mean claim %, appeared %, Brier) for claims
                carrying a percentage; Brier's mean squared prob error,
                0.25 = coin flip.
      named     (source, n, appeared %) for calls with no number (a
                final answer, not a 100% — turning it into one would
                invent the missing constant).
      skipped   claims in the undecided middle band.

    `instances`, when given, restricts every source to the exact same
    (player, interval) set (see _start_instances()) rather than just the
    same population, so one source can't out-count another by having
    more claims logged.
    Why: docs/notes/methodology.md#start_grade--same-instance-not-just-same-population

    Whoever wins this table earns tidy.LINEUP_SOURCE.
    """
    per: dict[str, dict[str, list]] = {}
    for r in claims:
        src = (r.get("source") or "").strip()
        key = norm(r.get("player_name", ""))
        when = snapshot_stamp(r.get("observed_at", ""))
        if not src or not key or when is None:
            continue
        if universe is not None and key not in universe:
            continue
        per.setdefault(src, {}).setdefault(key, []).append((when, r))
    for byname in per.values():
        for v in byname.values():
            v.sort(key=lambda t: t[0])

    num: dict[str, list] = {}
    nam: dict[str, list] = {}
    skipped = 0
    for interval in intervals:
        # A third element, when present, is the interval's population: the
        # clubs whose eleven we hold. Appearance intervals cover every club and
        # carry none.
        start, played = interval[0], interval[1]
        teams = interval[2] if len(interval) > 2 else None
        for src, byname in per.items():
            for key, hist in byname.items():
                if instances is not None and (key, start) not in instances:
                    continue
                row = latest_before(hist, start)
                if row is None:
                    continue
                if teams is not None \
                        and (row.get("team_slug") or "").strip() not in teams:
                    continue
                # The slug is tried as well as the name because one truth set —
                # the realised starters off the match pages — carries the same
                # /jugadores/ ids as futbolfantasy's claims. That join is exact
                # where the name join is merely usually right.
                slug = (row.get("player_slug") or "").strip()
                hit = 1.0 if key in played or (slug and slug in played) else 0.0
                try:
                    pct = float(row.get("start_pct"))
                except (TypeError, ValueError):
                    pct = None
                if pct is None:
                    if (row.get("role") or "") == "starter":
                        nam.setdefault(src, []).append(hit)
                    else:
                        skipped += 1
                elif abs(pct - 50.0) < START_EDGE:
                    skipped += 1
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
    """(player key, interval start) pairs where `src` had a gradeable
    (non-skipped, numeric) claim — mirrors start_grade()'s own per-
    interval lookup so the two definitions of "graded" can't diverge."""
    per: dict[str, list] = {}
    for r in claims:
        if (r.get("source") or "").strip() != src:
            continue
        key = norm(r.get("player_name", ""))
        when = snapshot_stamp(r.get("observed_at", ""))
        if not key or when is None:
            continue
        if universe is not None and key not in universe:
            continue
        per.setdefault(key, []).append((when, r))
    for v in per.values():
        v.sort(key=lambda t: t[0])

    out = set()
    for interval in intervals:
        start = interval[0]
        teams = interval[2] if len(interval) > 2 else None
        for key, hist in per.items():
            row = latest_before(hist, start)
            if row is None:
                continue
            if teams is not None \
                    and (row.get("team_slug") or "").strip() not in teams:
                continue
            try:
                pct = float(row.get("start_pct"))
            except (TypeError, ValueError):
                pct = None
            if pct is None or abs(pct - 50.0) < START_EDGE:
                continue
            out.add((key, start))
    return out


# ---------------------------------------------------------------------------
# grading against who ACTUALLY started
#
# starters.csv graded against the JORNADA LOCK (earliest kickoff observed
# that round), not each match's own, so a source is never credited with
# information it couldn't have used. An unlocked round is ungraded.
# Why: docs/notes/methodology.md#start-grade-appearance-vs-real-starters
# ---------------------------------------------------------------------------

def market_names(market: list[dict], slugs) -> dict[str, list[dict]]:
    """{team slug: [one row per player the market prices for that club]}.

    One row per NAME: market.csv holds a row per player per snapshot, and
    handing all of them to resolve() would make every player ambiguous with
    himself.
    """
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
    """The market key for a futbolfantasy player-slug, or ""."""
    slug = (slug or "").strip()
    if not slug:
        return ""
    if not _XW_CACHE:
        from ffcore.tidy import load_crosswalk
        _XW_CACHE.append(load_crosswalk())
    xw = _XW_CACHE[0]
    return (xw.player(ff_slug=slug) or "") if xw else ""


def start_intervals(matches: list[dict], starters: list[dict],
                    fixtures: list[dict], market: list[dict] = ()):
    """([(lock, {keys who started}, {teams captured})], graded, no-lock rounds).

    Keys are both normalised name and player slug; rows are deduplicated
    on (match, player) since a match page repeats across snapshots. A
    starter's short match-page name is also resolved to his market name
    (an ambiguous squad match, e.g. two Romeros, resolves to nothing
    rather than a guess). The team set is the interval's population —
    absence from the key set means "did not start" only for a club whose
    eleven we hold, not for the rest of the round.
    """
    jornada_of = {}
    for m in matches:
        try:
            jornada_of[m["match_id"]] = int(m["jornada"])
        except (KeyError, ValueError, TypeError):
            continue
    # PER-TEAM, not per-round — a deferred fixture must lock its own two
    # teams at its own real kickoff, not the round's earliest one (see
    # JornadaClock's own docstring for the real case this fixes).
    locks = JornadaClock(matches, fixtures).team_locks
    squads = market_names(market, {r.get("team_slug") for r in starters})

    seen, by_round, teams, ungraded = set(), {}, {}, set()
    graded = 0
    for r in starters:
        if (r.get("role") or "") != "starter":
            continue
        jor = jornada_of.get(r.get("match_id"))
        if jor is None:
            continue
        mark = (r.get("match_id"), r.get("player_name"))
        if mark in seen:
            continue
        seen.add(mark)
        team = (r.get("team_slug") or "").strip()
        lock = locks.get((jor, team))
        if lock is None:
            ungraded.add(jor)
            continue
        graded += 1
        priced, _ = resolve(r.get("player_name", ""), squads.get(team, []))
        # THE MARKET KEY FIRST — the site's own id, reached from the slug on
        # this very row through the crosswalk. The name and the slug stay in
        # the set beside it: this is a membership test, and the predictions
        # it is matched against were keyed by name for every run before
        # squad_log carried the id. Dropping them would ungrade the history.
        by_round.setdefault(lock, set()).update(
            k for k in (_market_key(r.get("player_slug")),
                        norm(r.get("player_name", "")),
                        (r.get("player_slug") or "").strip(),
                        norm(priced["name"]) if priced else "") if k)
        teams.setdefault(lock, set()).add(team)
    out = [(lock, keys, teams[lock]) for lock, keys in sorted(by_round.items())]
    return out, graded, sorted(ungraded)


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------

def load_actuals(window_days: int | None = WINDOW_DAYS) -> tuple[list[dict], str]:
    """Per-jornada rows from the newest season's file, parsed and windowed.

    `window_days=None` returns the WHOLE season's history unwindowed — for
    a report section this would be the wrong default (a stale row reading
    as current), but a real caller replaying the whole season's history
    (`backtest.replay_recommendations()`) needs every jornada, not just
    the last `WINDOW_DAYS`.
    """
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
        # Carried through to let a rate-side row (this one) and a
        # start-side row (start_grade()'s) join on jornada number rather
        # than on two different clocks (diff snapshot vs. kickoff lock).
        jor = r.get("jornada", "")
        rows.append({"name": full or short, "keys": keys,
                     "from_dt": from_dt, "points_delta": pd_,
                     "games_delta": gd,
                     "jornada": int(jor) if jor else None})
    return rows, label


def load_universe() -> set:
    """Every player the app prices — the only players worth grading a call on.

    Read straight off the market table rather than through Market, because all
    this needs is the set of names and building the valuation index would be
    the expensive half of the job.
    """
    return {norm(r.get("name", "")) for r in read_csv(TIDY / "market.csv")
            if r.get("name")}


def load_starts():
    """(intervals, starters graded, rounds with no lock) from the tidy tables.

    Empty everywhere until a jornada has been played AND its opener's kickoff
    was observed before it kicked off. Both conditions are reported by the
    caller rather than collapsed into a silent zero.
    """
    return start_intervals(read_csv(TIDY / "matches.csv"),
                           read_csv(TIDY / "starters.csv"),
                           read_csv(TIDY / "fixtures.csv"),
                           read_csv(TIDY / "market.csv"))


def load_predictions() -> dict[str, list[tuple[dt.datetime, dict]]]:
    """{norm name: [(when, factors)] ascending} from squad_log.csv.

    The whole logged row comes through, not just the score, because grading a
    forecast means asking WHICH factor was wrong. `fix` is empty on every row
    written before the fixture term existed; those rows still score, they just
    cannot be attributed, and the section says how many.
    """
    preds: dict[str, list[tuple[dt.datetime, dict]]] = {}
    for r in read_csv(DECISIONS / "squad_log.csv"):
        try:
            when = snapshot_stamp(r["observed_at"])
            fac = {"score": float(r["score"])}
        except (KeyError, ValueError, TypeError):
            continue
        for col in ("fix", "ppm", "flat", "start_pct", "cur_pj"):
            try:
                fac[col] = float(r[col])
            except (KeyError, ValueError, TypeError):
                fac[col] = None
        # NOT numeric — carried through as-is for golden_dataset()'s own
        # counterfactual reconstructions (home/away, position gates,
        # status overrides), same reasoning the docstring above already
        # gives for carrying the whole row: grading which FACTOR was
        # wrong needs these, not just the final score.
        fac["home"] = r.get("home") == "1"
        fac["pos"] = (r.get("pos") or "").lower()
        fac["status"] = r.get("status") or ""
        # THE ID THE ROW WAS LOGGED WITH, and the name for the rows written
        # before the column existed. Both sides of the grade have to agree
        # about who a prediction was about, and the confirmed-starts side
        # carries the id, the slug and the name for the same reason.
        key = (r.get("ff_id") or "").strip() or norm(r.get("player", ""))
        if key and when is not None:
            preds.setdefault(key, []).append((when, fac))
    for v in preds.values():
        v.sort(key=lambda t: t[0])
    return preds


def golden_dataset() -> dict[int, dict[str, dict]]:
    """{jornada: {key: {score, fix, ppm, flat, start_pct, cur_pj, home,
    pos, status, actual, games}}} — the one canonical join between
    logged predictions and real outcomes, for walk_forward_compare()/
    backtest_predictor() to build any hypothesis on, instead of a fresh
    ad hoc join per candidate. Reuses pair()'s own matching, unnarrowed
    (a counterfactual needs the raw ppm/fix/flat/home/status/pos, not
    just the collapsed "score").

    A caller wanting a subset (e.g. one position) filters this dict
    directly rather than asking this function to slice it.
    Why: docs/notes/methodology.md#golden_dataset--the-shared-join
    """
    preds = load_predictions()
    actuals, _label = load_actuals()
    out: dict[int, dict[str, dict]] = {}
    for a in actuals:
        if a["games_delta"] < 1 or a.get("jornada") is None:
            continue
        fac = matched_key = None
        for k in a["keys"]:
            hits = preds.get(k)
            if hits:
                fac = latest_before(hits, a["from_dt"])
            if fac is not None:
                matched_key = k
                break
        if fac is None:
            continue
        row = dict(fac)
        row["actual"] = a["points_delta"]
        row["games"] = a["games_delta"]
        out.setdefault(a["jornada"], {})[matched_key] = row
    return out


# ---------------------------------------------------------------------------
# the page
# ---------------------------------------------------------------------------

# What each tidy table is FOR, in the report's terms. The feeds themselves are
# read out of the source registry so a new one cannot be missed here; this is
# only the half a registry entry does not know, which is what the number is
# used for once it arrives.
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

# Written by src/crosswalk.py out of the tables above rather than fetched.
DERIVED = {"players", "clubs"}

# Sources the registry cannot name statically, because the sweep discovers
# them: the API's urls carry a league id that the API itself supplies, and the
# match pages are whichever ones the calendar says have been played.
HOSTS = {"api_leagues": ("LaLiga Fantasy API", "every_run"),
         "api_market": ("LaLiga Fantasy API", "every_run"),
         "api_teams": ("LaLiga Fantasy API", "every_run"),
         "api_lineup": ("LaLiga Fantasy API", "every_run"),
         "api_standings": ("LaLiga Fantasy API", "every_run"),
         "api_activity": ("LaLiga Fantasy API", "every_run"),
         "api_players": ("LaLiga Fantasy API", "once"),
         "api_stats": ("LaLiga Fantasy API", "every_run"),
         "starters": ("futbolfantasy.com", "once"),
         # The points PAGE is fetched every run; this table only gains a row
         # when somebody actually plays, so its age is the last jornada
         # scored and not a health reading.
         "points": ("futbolfantasy.com", "as played")}

# The same distinction, derived rather than listed: a table in
# sources.STORE_ONCE keeps the FIRST sighting of each fact, so its newest row
# is the last thing that HAPPENED and not the last time the feed answered.
# Ageing it as a health reading called the activity feed "17 hours stale" the
# moment it stopped being stored once per sweep — while it was answering every
# sweep, with nothing to report but a quiet transfer window.
def _as_it_happens() -> dict[str, str]:
    from sources import STORE_ONCE

    return {t: "as dealt" for t in STORE_ONCE}

# The column that carries the reading's time, where it is not observed_at.
STAMPED = {"points": "to_stamp"}

# How long a feed may go without answering before its age is the news.
# ffcore.tidy's own bounds, not a second opinion — load_elo() and the three
# load_api_* readers refuse a reading older than these. "every_run" and
# "twice_daily" share a bound because the fetch timer's two sweeps (11h/13h
# apart) are both longer than a naive 6h cadence would assume. Why:
# docs/notes/methodology.md#feed-freshness-bounds.
FRESH = {"every_run": EVERY_RUN_FRESH_DAYS, "daily": DAILY_FRESH_DAYS,
         "twice_daily": EVERY_RUN_FRESH_DAYS, "once": 1e9, "as played": 1e9,
         "as dealt": 1e9, "derived": 1e9}


def _hosts() -> dict[str, tuple[str, str, int]]:
    """{table: (host, cadence, how many pages)} from the source registry.

    Read from the registry rather than listed here, so a source added to
    sources.py appears in the appendix without anyone remembering to say so.
    """
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
    """{page: what the sweep has been doing lately}, or {} before the first
    sweep that logged anything.

    Consecutive failures at the TAIL, and what they cost. A source that has
    failed once is weather; one that has failed every sweep for a week is a
    decision — either its url moved or it should be dropped — and the seconds
    it burns while never answering is the number that decides which. Club Elo
    was taking eight of a sweep's eleven seconds for a page that did not come.
    """
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
    return days, when.strftime("%d %b %H:%M")


# Which fetched page feeds which tidy table, for the pages the registry cannot
# name statically. One payload can fill several tables: the squad call carries
# the league table and every player's stat lines as well as the squads.
API_PAGES = {"match": ("starters",),
             "api_lineup": ("api_lineup",),
             "api_leagues": ("api_leagues",),
             "api_market": ("api_market",),
             "api_teams": ("api_teams", "api_standings", "api_stats"),
             "api_activity": ("api_activity",),
             "api_player": ("api_players",)}


def _fetched() -> dict[str, list[float]]:
    """{table: [hours since each of its pages was last ASKED FOR]}.

    THE ONE HONEST AGE, and it is not the one the tidy store holds. parse
    re-stamps a carried-forward document with the sweep that carried it, so
    `observed_at` says "now" for a page nobody has requested since midnight —
    by design, because that column answers "which snapshot is this row from".
    The manifest keeps the other half: `seen` is when the page was last
    actually fetched, and that is what says whether a source is still feeding
    us. On 2026-08-19 the probable-XI pages read "ok, 0 hours" in this table
    while the last request for them was 17.6 hours old.
    """
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


# The light. A table is green when every page behind it was fetched within its
# own cadence, amber when one of them has missed a turn, red when the readers
# have given up on it — ffcore.tidy refuses a reading past FRESH, so red here
# and a fallback downstream are the same event.
GREEN, AMBER, RED, GREY = "🟢", "🟡", "🔴", "⚪"


def _light(cadence: str, ages: list[float], refused: bool) -> tuple[str, str]:
    """(light, what it means) for one table, from when its pages were fetched."""
    if cadence == "derived":
        return GREY, "rebuilt every run from the tables above"
    if not ages:
        # A page the last sweep did not put in its manifest: it failed, or it
        # has never been asked for at all. `why` below names which, off the
        # fetch log, because a 403 and a page nobody has ever requested are
        # not the same problem.
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
    """The data flow, and which parts of it have stopped answering. A
    failed fetch leaves stale rows that every downstream reader treats as
    current, so age is printed as a column rather than assumed fresh.
    """
    now = run_now()
    reg, feeds = _hosts(), _feed_state()
    asked, quiet = _fetched(), stale_feeds()
    rows = []
    for name in sorted(FILLS):
        path = (SEASON / "live" / "perjornada_2026-27.csv") if name == "points" \
            else (TIDY / f"{name}.csv")
        got = read_csv(path)
        col = STAMPED.get(name, "observed_at")
        newest = max((r.get(col, "") for r in got), default="")
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
            "{:,}".format(len(got)), seen, state))

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
    """What actually ranked the opponents in today's run — asked of the
    same functions the scorer uses, never described from memory.
    """
    from ffcore.fixture import elo_strength, team_strength

    # load_elo() returns nothing for two different reasons and the reader is
    # owed which: never fetched, or fetched and now too old to be about these
    # teams. The raw file separates them — it still holds the stale rows.
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
    # Age is a feed-table column, not restated here.
    return "**Club Elo rating**, a result-based rating with no transfer fees in it"


def latest_market() -> list[dict]:
    """The newest market snapshot. Read here only to list the league's clubs."""
    from ffcore.tidy import load_market_latest

    return load_market_latest()


def formula_lines() -> list[str]:
    """The live constants, read from the modules that use them (never
    restated by hand) so a changed value shows up here on the next run.
    The reasoning behind them lives in the README, not duplicated here.
    """
    return [
        "### The model, as configured right now", "",
        "| Term | Setting | Fitted? |",
        "|---|---|---|",
        f"| Formula | `xPts/j = shrunk pts-per-match × fixture × P(start)` | "
        "— |",
        f"| Shrinkage | K = {SHRINK_K:g} matches, applied twice: last season "
        "toward the positional prior, then this season toward that | yes |",
        f"| Fixture band | ±{FIX_BAND * 100:.0f}% across the opponents by "
        "rank, not by ratio | **no, a guess** |",
        f"| Home advantage | +{HOME_EDGE * 100:.0f}% | **no, a guess** |",
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
    """The full explanation of every table column the daily report only
    footnotes in one line: the ladder and the league table.
    """
    return [
        "### How to read the tables", "",
        # Ladder/league-table glossary added 2026-09-01, replacing two
        # independently-drifted copies (sim.py's ladder(), Fantasy.jsx).
        # Why: docs/notes/methodology.md#column-guide-ladder-and-league-table.
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
        "season, never a prediction he actually wants the player.", "",
        "**The league table** — `Pts` is the real league total today. "
        "`Season` is the simulated total and its 10-90 band — a mean "
        "with no band beside it reads as a prediction it is not. "
        "`P(above)` is how often the simulation has you finish above "
        "them.", "",
    ]


def _fc():
    """The forecaster this run built, or None — asked, never described."""
    try:
        import decide
        return decide.load().forecaster
    except Exception:                                    # pragma: no cover
        return None


_RATE_NOTE: list = []


def _rate_note() -> str:
    """How wide the rate uncertainty came out, in this run's own numbers —
    a formula alone can't be checked against the bands it widens.
    """
    if _RATE_NOTE:
        return _RATE_NOTE[0]
    import statistics
    fc = _fc()
    rel = sorted(getattr(fc, "rate_rel", {}).values())
    _RATE_NOTE.append("±%.0f%% of a rate" % (100 * statistics.median(rel))
                      if rel else "not applied — no match counts")
    return _RATE_NOTE[0]


def start_lines() -> list[str]:
    """The same gate, graded on who actually started rather than who appeared.

    Printed above the appearance table because it answers the real question. It
    stays silent until there is something to say: this is the table that fills
    in as jornadas are played, and an empty one would read as a source scoring
    zero rather than as a season that has not started.
    """
    intervals, graded, ungraded = load_starts()
    out: list[str] = []
    if not intervals:
        if ungraded:
            NOT_GRADED.append(
                "| jornada " + ", ".join(str(j) for j in ungraded)
                + " — confirmed elevens exist but no kickoff was observed "
                  "before the round locked | all |")
        return out

    # OUR OWN FORECAST, GRADED ON THE SAME REAL JORNADA-LOCKED BOUNDARIES —
    # see forecast_claims()'s own note on why "starts" (not "appearances")
    # is the table that actually answers this.
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

    # "our forecast" only covers the actively-managed squad — a harder,
    # more uncertain population than the whole league, so comparing its
    # Brier against a raw source's whole-league Brier is unfair to us.
    # `our_instances` pins every source to the exact (player, jornada-lock)
    # pairs our own forecast was graded on, not just the same population,
    # so no source's n can be inflated by intervals we had no opinion on.
    ours = forecast_claims()
    universe = load_universe()
    our_instances = _start_instances(intervals, ours, "our forecast", universe)
    our_names = {norm(c["player_name"]) for c in ours}
    if our_names and our_instances:
        restricted = [c for c in load_lineups(source="")
                     if norm(c.get("player_name", "")) in our_names]
        fair_num, _fair_named, _fair_skip = start_grade(
            intervals, restricted + ours, universe, instances=our_instances)
        if len(fair_num) > 1:      # nothing to compare with just ourselves
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
    """Per-instance squared Brier terms for one source, restricted to
    `instances` — the raw per-claim values `stats.bootstrap_gap()` needs to
    test whether a Brier gap between two sources is real or noise (the
    aggregate Brier alone can't be bootstrapped, only its ingredients can)."""
    per: dict[str, list] = {}
    for r in claims:
        if (r.get("source") or "").strip() != src:
            continue
        key = norm(r.get("player_name", ""))
        when = snapshot_stamp(r.get("observed_at", ""))
        if key and when is not None:
            per.setdefault(key, []).append((when, r))
    for v in per.values():
        v.sort(key=lambda t: t[0])

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
            slug = (row.get("player_slug") or "").strip()
            hit = 1.0 if key in played or (slug and slug in played) else 0.0
            out.append((pct / 100.0 - hit) ** 2)
    return out


def forecast_claims() -> list[dict]:
    """Our own start-probability forecast (squad_log.csv's `start_pct`,
    the blended number that actually feeds the simulation), in
    start_grade()'s claim shape — resolves team_slug through the
    crosswalk so these claims reach the team-scoped "starts" table
    rather than only the looser "appearances" one.
    Why: docs/notes/methodology.md#forecast_claims--our-own-number-graded-the-same-way
    """
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
        # "ff_id" is squad_log.csv's historical misnomer for the crosswalk
        # key (report.py writes p["key"] under that name), not an app_id.
        team_slug = ""
        p = xw.players.get((r.get("ff_id") or "").strip())
        if p:
            club = xw.clubs.get(p.club_id)
            team_slug = club.ff_slug if club else ""
        out.append({"source": "our forecast", "player_name": r["player"],
                    "observed_at": r["observed_at"], "start_pct": pct,
                    "team_slug": team_slug})
    return out


def golden_rows() -> list[dict]:
    """One row per (player, jornada): the rate side and the start side on
    the same table, joined on jornada number. Uses JornadaClock.team_locks
    (each fixture's own kickoff), not .round_locks (the round-wide lock) —
    a deferred fixture's real lock differs from its round's.

    A row's rate fields are None when nothing matched — a real
    start-probability claim with no rate prediction for that jornada
    (he might not have played) is still kept for start-calibration.
    Why: docs/notes/methodology.md#golden_rows--the-player-jornada-join
    """
    actuals, _label = load_actuals()
    rate_by_key: dict[tuple[str, int], dict] = {}
    for p in pair(actuals, load_predictions()):
        if p.get("jornada") is not None:
            rate_by_key[(norm(p["name"]), p["jornada"])] = p

    matches = read_csv(TIDY / "matches.csv")
    fixtures = read_csv(TIDY / "fixtures.csv")
    jornada_of_lock = {when: jor
                       for (jor, _team), when
                       in JornadaClock(matches, fixtures).team_locks.items()}

    intervals, _graded, _ungraded = load_starts()
    per: dict[str, list[tuple[dt.datetime, dict]]] = {}
    for c in forecast_claims():
        key = norm(c.get("player_name", ""))
        when = snapshot_stamp(c.get("observed_at", ""))
        if key and when is not None:
            per.setdefault(key, []).append((when, c))
    for v in per.values():
        v.sort(key=lambda t: t[0])

    out = []
    for lock, played, teams in intervals:
        jor = jornada_of_lock.get(lock)
        if jor is None:
            continue
        for key, hist in per.items():
            row = latest_before(hist, lock)
            if row is None:
                continue
            if teams is not None \
                    and (row.get("team_slug") or "").strip() not in teams:
                continue
            golden = {"player": row["player_name"], "jornada": jor,
                      "predicted_start_pct": row["start_pct"],
                      "actual_started": key in played,
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
    """Does our start-probability forecast beat a naive constant
    baseline, by Brier score? `None` when there are no rows to check —
    not zero, which would read as "beats nothing by a mile."
    """
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
    """The gate for LINEUP_SOURCE: which site's eleven was right more often.

    ONE TABLE, TWO SAMPLES. Starts is the question — a substitute is a miss —
    and appearances is the blunter one kept because it reaches back to before
    the match pages were collected, where a twenty-minute substitute counts.
    They flatter both sources equally, so the comparison holds even though the
    level does not; printing them as one table with a Sample column is what
    stops the second being read as the answer.
    """
    NOT_GRADED.clear()
    starts = start_lines()
    head = ["### Who to believe about the eleven", "",
            "| Source | Calls | Mean claim | Hit | Brier |",
            "|---|--:|--:|--:|--:|"]
    rows = list(starts)

    intervals = appearances(actuals)
    # OUR OWN FORECAST, GRADED THE SAME WAY — see forecast_claims()'s own
    # note. Concatenated with the raw sources so it's one more row in the
    # same table, not a second table nobody reads.
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
    """Does the scoring-RATE forecast beat a trivial constant guess (the
    sample's own mean per-match rate)? `None` when there's nothing to
    check.

    `ours`/`naive` are per-pair per-match errors — the unit
    stats.bootstrap_gap() needs, treating each entry as one independent
    draw. The match-weighted headline MAE is weighted_mae() below, a
    different question with a different unit.
    """
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
    """Total absolute error over total matches played — not an average
    of already-averaged per-interval errors, which would weight a
    3-match interval the same as a 1-match one."""
    total_matches = sum(p["matches"] for p in pairs)
    return sum(abs(p["err"]) for p in pairs) / total_matches


ACCURACY_LOG = "forecast_accuracy_log.csv"


def log_forecast_accuracy(n: int, mae: float, naive_mae: float) -> None:
    """Append today's rate-forecast accuracy reading. Never overwrites —
    the series is the record, same append-only discipline as
    sim.log_cash_price()/cash_price_log.csv.
    Why: docs/notes/methodology.md#log_forecast_accuracy--why-this-exists
    """
    from ffcore.tidy import DECISIONS, append_csv

    DECISIONS.mkdir(parents=True, exist_ok=True)
    append_csv(DECISIONS / ACCURACY_LOG,
              [{"observed_at": run_now().strftime("%Y-%m-%dT%H%MZ"),
                "n": n, "mae": "%.4f" % mae, "naive_mae": "%.4f" % naive_mae}],
              ["observed_at", "n", "mae", "naive_mae"])


def forecast_accuracy_history() -> list[dict]:
    """[{observed_at, n, mae, naive_mae}], oldest first, from
    log_forecast_accuracy()'s own log. [] before its first row.
    """
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
    # `gap` is a bootstrap CI on ours-vs-naive error, not a bare point
    # estimate — "beats" only claims a real margin when the CI excludes zero.
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

    # Attribution: not "how wrong", but "wrong about WHAT" — decides
    # whether the fixture band is kept, widened or deleted.
    if fx:
        out += [f"| Next fixture (±{FIX_BAND*100:.0f}%, unfitted) | n | Mean "
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
    """Is DRIFT_FRAC still the bare default, or fit off real data this
    run? See forecast.fit_drift_frac()/drift_frac_from_history().
    """
    fitted, why = drift_frac_from_history()
    from ffcore.forecast import DRIFT_FRAC as _DEFAULT
    out = ["### Season-long drift", ""]
    if fitted == _DEFAULT and "not enough" in why:
        out += [f"Still the unfitted default ({_DEFAULT:.2f}) — {why}.", ""]
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
    out += source_lines(load_actuals()[0])
    PARTS.mkdir(parents=True, exist_ok=True)
    write_lines(PARTS / "methodology.md", out)
    print(f"wrote {PARTS / 'methodology.md'} ({len(out)} lines)")


# ---------------------------------------------------------------------------
# selftest — join logic only
# ---------------------------------------------------------------------------

def _selftest() -> None:
    # -- the light is on the asking, not on the re-stamp --------------------
    # A page carried into a sweep it was never asked for is re-stamped with
    # that sweep, so `observed_at` said "0 hours" for probable-XI pages last
    # requested seventeen hours earlier. These are the states that must be
    # distinguishable at a glance.
    assert _light("every_run", [0.5], False)[0] == GREEN
    assert _light("twice_daily", [17.6], False)[0] == AMBER      # missed 11:40
    assert _light("twice_daily", [0.2, 30.0], False)[0] == AMBER  # one page of forty
    assert "1 of 2 pages" in _light("twice_daily", [0.2, 30.0], False)[1]
    assert _light("every_run", [80.0], False)[0] == RED
    # Refused by ffcore.tidy's gate IS red, whatever the clock says: the
    # readers have already dropped it and the report is on its fallback.
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

    # Ane played once between the 15th and the 17th: the prediction that
    # counts is the one from the 14th (3.0), not the hindsight 9.9.
    actuals = [
        {"name": "Ane", "keys": ["ane"], "from_dt": t(15),
         "points_delta": 8.0, "games_delta": 1.0},
        {"name": "Bo", "keys": ["bo"], "from_dt": t(15),
         "points_delta": 4.0, "games_delta": 2.0},   # doubled interval
        {"name": "Cai", "keys": ["cai"], "from_dt": t(15),
         "points_delta": 5.0, "games_delta": 1.0},   # never predicted
        {"name": "Didi", "keys": ["didi"], "from_dt": t(15),
         "points_delta": 1.0, "games_delta": 0.0},   # no match played
    ]
    got = pair(actuals, preds)
    assert [g["name"] for g in got] == ["Ane", "Bo"], got
    ane, bo = got
    assert ane["predicted"] == 3.0 and ane["err"] == -5.0, ane
    assert bo["predicted"] == 3.0 and bo["matches"] == 2.0, bo

    # No prediction strictly before the cutoff -> excluded.
    assert latest_before(preds["bo"], t(14)) is None
    assert latest_before(preds["bo"], t(14, 1))["score"] == 1.5

    rows = bucket_rows(got)
    assert [r[0] for r in rows] == ["under 2", "3–4"], rows

    # -- grading the fixture term ------------------------------------------
    # Ane faced an easier fixture (1.10) and beat the forecast; Bo faced a
    # harder one (0.90) and beat it too. Both land in their own bucket, with
    # the error signed forecast-minus-actual and stated PER MATCH: Bo's two
    # matches are one row, not two.
    fx, no_fix = fixture_rows(got)
    assert [r[0] for r in fx] == ["harder", "easier"], fx
    assert no_fix == 0
    hard, easy = fx
    assert hard[1] == 1 and abs(hard[4] - (1.5 - 2.0)) < 1e-9, hard
    assert easy[1] == 1 and abs(easy[4] - (3.0 - 8.0)) < 1e-9, easy

    # A row logged before the fixture term existed is counted as unattributable
    # rather than dropped or treated as neutral — the difference between "we
    # did not measure it" and "it was average".
    old = pair([{"name": "Ane", "keys": ["ane"], "from_dt": t(11),
                 "points_delta": 4.0, "games_delta": 1.0}], preds)
    fx2, no_fix2 = fixture_rows(old)
    assert fx2 == [] and no_fix2 == 1, (fx2, no_fix2)

    # -- lagged_pair()/lock_order(): multi-jornada-ahead test off data
    # already in squad_log.csv --------------------------------------------
    # Jornada 3 locks before jornada 1 (a real rescheduling case) —
    # lock_order() must sort by actual lock time, not jornada label.
    locks3 = {1: t(20), 2: t(14), 3: t(10)}
    assert lock_order(locks3) == [3, 2, 1], lock_order(locks3)

    # Jornada 1 is THIRD in real lock order (index 2) — two locked jornadas
    # (3, then 2) already sit behind it, so it's the one that can test
    # lag=0/1/2 all at once. Five snapshots of the same prediction, far
    # enough apart that each lag's cutoff lands on a different one.
    preds3 = {"eli": [(t(9), f(1.0)), (t(11), f(2.0)), (t(15), f(3.0)),
                      (t(19), f(4.0)), (t(21), f(9.9))]}
    actuals3 = [{"name": "Eli", "keys": ["eli"], "from_dt": t(20, 1),
                "points_delta": 3.0, "games_delta": 1.0, "jornada": 1}]
    # lag=0: cutoff is jornada 1's OWN lock (t20) -> freshest before it, t19
    # (4.0) — not t21's 9.9, which is hindsight (logged after jornada 1
    # itself locked) and must never be reachable at any lag.
    lag0 = lagged_pair(actuals3, preds3, locks3, 0)
    assert len(lag0) == 1 and lag0[0]["predicted"] == 4.0, lag0
    # lag=1: cutoff steps back to jornada 2's lock (t14) -> t11 (2.0).
    lag1 = lagged_pair(actuals3, preds3, locks3, 1)
    assert len(lag1) == 1 and lag1[0]["predicted"] == 2.0, lag1
    # lag=2: cutoff steps back to jornada 3's lock (t10) -> t9 (1.0).
    lag2 = lagged_pair(actuals3, preds3, locks3, 2)
    assert len(lag2) == 1 and lag2[0]["predicted"] == 1.0, lag2
    # lag=3: only 3 jornadas exist at all, jornada 1 is 2 steps in — a
    # third step back has nothing behind it, skipped rather than guessed.
    assert lagged_pair(actuals3, preds3, locks3, 3) == []

    # -- drift_frac_from_history(): honest refusal on too little real data,
    # same discipline as fit_drift_frac() itself ----------------------------
    fitted, why = drift_frac_from_history()
    assert fitted == 1.0 and isinstance(why, str) and why, (fitted, why)

    # -- grading the probable-XI sources ------------------------------------
    # Ane and Bo played in the interval opening on the 15th; Cai did not, and
    # says so by being absent from it — points.py emits movers only.
    played = [{"name": "Ane", "keys": ["ane"], "from_dt": t(15),
               "points_delta": 8.0, "games_delta": 1.0},
              {"name": "Bo", "keys": ["bo"], "from_dt": t(15),
               "points_delta": 4.0, "games_delta": 2.0},
              {"name": "Didi", "keys": ["didi"], "from_dt": t(15),
               "points_delta": 1.0, "games_delta": 0.0}]   # points, no match
    iv = appearances(played)
    assert [s for s, _ in iv] == [t(15)], iv
    assert iv[0][1] == {"ane", "bo"}, iv[0][1]
    # A row with points but no match is not an appearance.
    assert "didi" not in iv[0][1]

    def claim(src, name, pct, when=14, role="starter"):
        return {"source": src, "player_name": name, "start_pct": pct,
                "role": role, "observed_at": when}

    # Two sites, four players, one interval. `ff` called all three right; `af`
    # was confident about Cai, who never played.
    claims = [claim("ff", "Ane", "90"), claim("ff", "Bo", "80"),
              claim("ff", "Cai", "20", role="doubt"),
              claim("af", "Ane", "90"), claim("af", "Bo", "80"),
              claim("af", "Cai", "90"),
              claim("ff", "Ane", "10", when=16),   # hindsight: never scored
              claim("ff", "Ghost", "90"),          # not in the app at all
              claim("ff", "Eve", "55"),            # no call either way
              claim("af", "Fay", "", role="starter"),      # a named starter
              claim("af", "Gus", "", role="doubt")]        # neither
    # observed_at wants a stamp string; snapshot_stamp parses these.
    for c in claims:
        c["observed_at"] = ("2026-08-%02dT1200Z" % c["observed_at"]
                            if isinstance(c["observed_at"], int)
                            else c["observed_at"])

    universe = {"ane", "bo", "cai", "eve", "fay", "gus"}
    num, named, skipped = start_grade(iv, claims, universe)
    got = {s: (n, round(b, 3)) for s, n, _, _, b in num}
    # Three graded calls each, and af is punished for the confident miss.
    assert got["ff"][0] == 3 and got["af"][0] == 3, got
    assert got["af"][1] > got["ff"][1], got
    # Perfect confidence on two hits and a correct doubt is a good Brier.
    assert got["ff"][1] < 0.05, got
    # The hindsight claim from the 16th never enters: only calls logged before
    # the interval opened are scored.
    ff = next(r for r in num if r[0] == "ff")
    assert abs(ff[2] - (90 + 80 + 20) / 3) < 1e-9, ff
    assert abs(ff[3] - 200.0 / 3) < 1e-9, ff        # 2 of 3 appeared
    # A name the app does not price is not graded, so the more complete source
    # is not penalised for being more complete.
    assert all(n == 3 for _, n, _, _, _ in num), num
    # A call with no number is a hit rate, never dressed up as 100%.
    assert named == [("af", 1, 0.0)], named
    # The undecided middle and a listing with neither figure are skipped, and
    # the count is reported rather than silently dropped.
    assert skipped == 2, skipped
    # No interval, no record: never a zero.
    assert start_grade([], claims, universe) == ([], [], 0)

    # -- grading against who actually started ------------------------------
    matches = [{"match_id": "1", "jornada": "1", "home": "alaves",
                "away": "getafe", "score": "3-0"},
               {"match_id": "2", "jornada": "1", "home": "espanyol",
                "away": "levante", "score": "1-0"},
               {"match_id": "9", "jornada": "2", "home": "rayo-vallecano",
                "away": "alaves", "score": "2-2"}]
    # The two sites spell clubs differently and still join, and a round whose
    # kickoff was never observed gets no lock rather than an assumed one.
    fixtures = [{"kickoff": "2026-08-16T17:00:00+00:00", "home": "Espanyol",
                 "away": "Levante"},
                {"kickoff": "2026-08-15T19:30:00+00:00", "home": "Alaves",
                 "away": "Getafe"}]
    assert team_slug_of("Racing Santander", {"racing", "real-madrid"}) \
        == "racing"
    assert team_slug_of("Real Betis", {"betis", "real-sociedad"}) == "betis"
    assert team_slug_of("Nowhere FC", {"racing"}) is None
    locks = JornadaClock(matches, fixtures).round_locks
    # The round locks at its EARLIEST kickoff, not each match's own: the app
    # locks the whole lineup once, so Sunday's starter is already frozen.
    assert list(locks) == [1] and locks[1].day == 15, locks
    assert 2 not in locks                       # no kickoff observed for it

    def start(match, name, slug, role="starter", team="alaves"):
        return {"match_id": match, "player_name": name, "player_slug": slug,
                "role": role, "team_slug": team}

    xi = [start("1", "Ane", "ane-slug"), start("1", "Bo", "bo-slug"),
          start("1", "Bo", "bo-slug"),          # repeats: carried forward
          start("2", "Cai", "cai-slug", team="levante"),
          start("2", "Dee", "dee-slug", role="sub"),   # a sub is not a starter
          start("9", "Eve", "eve-slug")]        # round 2 has no lock
    # TWO intervals, not one, even inside the same jornada — Alaves-Getafe
    # and Espanyol-Levante kick off six hours apart in this fixture, so
    # each locks its OWN two teams at its OWN kickoff (JornadaClock.team_lock),
    # not both grouped under the round's single earliest one. Grading
    # Espanyol/Levante as of Friday's kickoff would throw away a real
    # day of team news nobody needed to discard.
    mlocks = JornadaClock(matches, fixtures).team_locks
    iv2, graded, ungraded = start_intervals(matches, xi, fixtures)
    assert graded == 3, graded                  # Ane, Bo, Cai — Bo once
    assert ungraded == [2], ungraded            # said out loud, not dropped
    assert len(iv2) == 2, iv2
    by_lock = {lock: (keys, teams) for lock, keys, teams in iv2}
    alaves_keys, alaves_teams = by_lock[mlocks[(1, "alaves")]]
    levante_keys, levante_teams = by_lock[mlocks[(1, "levante")]]
    assert mlocks[(1, "alaves")] == locks[1]     # alaves was the earliest
    assert mlocks[(1, "levante")] != locks[1]    # levante's own, not the round's
    # Both keys are carried, so a claim matches on whichever it has.
    assert alaves_keys == {"ane", "ane-slug", "bo", "bo-slug"}, alaves_keys
    assert "dee" not in alaves_keys and "eve" not in alaves_keys
    assert alaves_teams == {"alaves"}, alaves_teams
    assert levante_keys == {"cai", "cai-slug"}, levante_keys
    assert levante_teams == {"levante"}, levante_teams

    # The short name a match page prints resolves to the market name, so a
    # claim carrying only the full name is graded. Ambiguity inside the squad
    # adds nothing: "Romero" stays "romero" and whoever claimed a Romero by
    # full name alone goes ungraded rather than half-credited.
    market = [{"name": "abdel abqar", "team": "Alaves"},
              {"name": "abdel abqar", "team": "Alaves"},   # a second snapshot
              {"name": "ivan romero", "team": "Alaves"},
              {"name": "rafael romero", "team": "Alaves"},
              {"name": "someone else", "team": "Barcelona"}]
    iv3, _, _ = start_intervals(
        matches, [start("1", "Abqar", "abqar-slug"),
                  start("1", "Romero", "romero-slug")], fixtures, market)
    assert "abdel abqar" in iv3[0][1], iv3[0][1]
    assert "ivan romero" not in iv3[0][1] \
        and "rafael romero" not in iv3[0][1], iv3[0][1]
    # A club nobody played keeps its players out of the squad index entirely.
    assert set(market_names(market, {"alaves"})) == {"alaves"}

    # A claim is graded on the SLUG when the name would not match. This is the
    # whole reason the outcome comes off the same site: "U. Núñez" on one page
    # and "Unai Núñez" on another are the same player, and the id says so.
    slugged = [{"source": "ff", "player_name": "Whoever They Call Him",
                "player_slug": "ane-slug", "start_pct": "90", "role": "starter",
                "team_slug": "alaves", "observed_at": "2026-08-14T1200Z"}]
    num2, _, _ = start_grade(iv2, slugged, None)
    assert num2 == [("ff", 1, 90.0, 100.0, (0.9 - 1) ** 2)], num2
    # A confident call about a club whose match we never read is not a miss —
    # it is not graded at all. Scoring it would punish the source for our gap.
    absent = dict(slugged[0], player_slug="zed-slug", team_slug="barcelona")
    assert start_grade(iv2, [absent], None) == ([], [], 0)
    # Nothing played, nothing claimed: still not a zero score for anyone.
    assert start_intervals([], [], []) == ([], 0, [])

    guide = "\n".join(column_guide_lines())
    for heading in ("The ladder", "The league table"):
        assert heading in guide, heading
    # The current columns actually named, not just the section headings.
    for term in ("pts/M€", "P(above)"):
        assert term in guide, term
    for term in ("market price", "premium"):
        assert term in guide, term
    assert "vs X" not in guide, guide
    # Retired sections stay gone.
    for heading in ("Field these eleven", "What to bid", "Fitness",
                   "Starting"):
        assert heading not in guide, heading

    # -- forecast_claims(): our own start_pct, graded the same way ----------
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
            # Predicted to start, never played.
            {"observed_at": "2026-08-10T1200Z", "player": "Benched",
             "start_pct": "85", "ff_id": "benched"},
            {"observed_at": "2026-08-10T1200Z", "player": "NoNumber",
             "start_pct": "", "ff_id": "nonumber"},  # unparseable, skipped
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

    # start_grade() scores "predicted 85%, never a mover" as a real miss,
    # including on a team-scoped interval.
    iv_f = [(snapshot_stamp("2026-08-10T1800Z"), {"nailed"}, {"fc-slug"})]
    numf, _namf, _skipf = start_grade(iv_f, claims)
    row = next(r for r in numf if r[0] == "our forecast")
    _src, n_f, claim_pct, hit_pct, brier = row
    assert n_f == 2, row
    assert hit_pct == 50.0, row           # 1 of 2 actually appeared
    assert brier > 0.0, row               # a real, nonzero miss on Benched
    # A different team population excludes both outright.
    iv_other = [(snapshot_stamp("2026-08-10T1800Z"), {"nailed"},
                {"some-other-club"})]
    num_other, _, _ = start_grade(iv_other, claims)
    assert not any(r[0] == "our forecast" for r in num_other), num_other

    # -- golden_rows(): the player-jornada join, checked against real data -
    # A real, cheap invariant: a row where the start side says he never
    # appeared must show zero points on the rate side, wherever both
    # sides exist.
    golden = golden_rows()
    checked = [r for r in golden if r["predicted_rate"] is not None]
    assert checked, "golden_rows() must find at least one fully-joined row"
    # Known, tracked exception: points.py's jornada_asof() can mislabel a
    # delta onto an earlier jornada for a round with a deferred match —
    # a separate, deferred fix, not a join bug here.
    bad = [r for r in checked
          if not r["actual_started"] and r["actual_points"] != 0.0]
    assert len(bad) <= 2, bad
    print(f"  golden_rows(): {len(golden)} rows, {len(checked)} fully "
         f"joined, {len(bad)} known points.py-mislabel exception(s)")

    # -- golden_dataset(): the shared join every walk-forward hypothesis
    # test should build on ------------------------------------------------
    gd = golden_dataset()
    total_rows = sum(len(v) for v in gd.values())
    assert total_rows >= 30, (
        "golden_dataset() should reach the same order of magnitude as "
        "pair()'s own real-data join (40-60 rows this season as of "
        "2026-09) — a much smaller number means the shared join itself "
        "broke, not that a hypothesis's own data is thin: %d" % total_rows)
    assert len(gd) >= 3, gd   # spans several real jornadas, not one
    sample_row = next(iter(next(iter(gd.values())).values()))
    for field in ("score", "fix", "ppm", "flat", "home", "pos", "status",
                 "actual", "games"):
        assert field in sample_row, (field, sample_row)
    print(f"  golden_dataset(): {total_rows} rows across {len(gd)} jornadas")

    # -- baseline_check(): a synthetic case where the "right" answer is
    # known by construction, since real data can only show what today's
    # forecast happens to score, not prove the arithmetic is right -------
    assert baseline_check([]) is None            # nothing to check, not 0
    perfect = [{"predicted_start_pct": 100.0, "actual_started": True}] * 5
    chk = baseline_check(perfect)
    assert chk["ours"] == 0.0, chk                # perfect claims, zero Brier
    assert chk["coin_flip"] == 0.25, chk           # every 50%-guess case
    always_wrong = [{"predicted_start_pct": 90.0, "actual_started": False}] * 3
    chk2 = baseline_check(always_wrong)
    # 90% claim, never happened: (0.9-0)^2 = 0.81, worse than guessing 50%.
    assert chk2["ours"] > chk2["coin_flip"], chk2

    # -- start_lines()'s fair, same-population comparison (real data) ------
    # 2026-09-06: the whole-league "starts" table made our forecast look
    # WORSE than the raw sources (0.114 vs futbolfantasy's 0.088) purely
    # because our forecast only ever covers the squad Miguel actively
    # manages — a harder, more genuinely contested population than the
    # whole league's mostly-easy cases. Restricted to the same population,
    # our forecast actually wins. Checked against real data since the
    # whole point is whether it's still true, not whether the arithmetic
    # can be made to say so on a synthetic fixture.
    fair = start_lines()
    if any("same population as our forecast" in ln for ln in fair):
        i = next(i for i, ln in enumerate(fair)
                if "same population as our forecast" in ln)
        block = "\n".join(fair[i:])
        assert "our forecast" in block, block

    # -- rate_baseline_check(): the rate-side twin of baseline_check(), a
    # known-answer synthetic case ------------------------------------------
    assert rate_baseline_check([]) is None
    perfect_rate = [{"predicted": 6.0, "actual": 6.0, "matches": 1.0}] * 4
    rchk = rate_baseline_check(perfect_rate)
    assert rchk["ours"] == 0.0, rchk               # exact every time
    assert rchk["naive"] == 0.0, rchk              # no variance to miss either
    # A forecast that's ALWAYS off by the same fixed amount is worse than
    # guessing the sample's own mean, which is exactly right on a constant
    # sample — the naive guess wins here BY CONSTRUCTION, the point being
    # the comparison can go either way, not that ours always wins.
    always_off = [{"predicted": 9.0, "actual": 6.0, "matches": 1.0}] * 4
    rchk2 = rate_baseline_check(always_off)
    assert rchk2["ours"] > rchk2["naive"] == 0.0, rchk2

    # -- log_forecast_accuracy / forecast_accuracy_history: append-only,
    # real dated rows, not a single overwritten reading — same discipline
    # sim.py's cash_price_log.csv already uses ------------------------
    import tempfile as _tempfile4
    from ffcore import tidy as _tidy4

    with _tempfile4.TemporaryDirectory() as _d4:
        _real_decisions4 = _tidy4.DECISIONS
        _tidy4.DECISIONS = __import__("pathlib").Path(_d4)
        try:
            assert forecast_accuracy_history() == []   # nothing logged yet
            log_forecast_accuracy(12, 3.25, 4.12)
            log_forecast_accuracy(14, 3.10, 4.05)       # a later, real run
            hist = forecast_accuracy_history()
            assert [h["n"] for h in hist] == [12, 14], hist
            assert abs(hist[0]["mae"] - 3.25) < 1e-9, hist
            assert abs(hist[1]["naive_mae"] - 4.05) < 1e-9, hist
            # NEVER OVERWRITES — the file has two real rows, not the
            # latest reading clobbering the first.
            assert len(hist) == 2, hist
        finally:
            _tidy4.DECISIONS = _real_decisions4

    print("methodology.py selftest OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        main()
