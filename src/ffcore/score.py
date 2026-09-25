
from __future__ import annotations

import statistics
from typing import NamedTuple

from ffcore import schema
from ffcore.parse import money, pct100, ratio
from ffcore.startprob import Calibration
from ffcore.text import norm
from ffcore.tidy import minutes_played

__all__ = ["SLOT", "SLOT_LABEL", "SLOT_MIN", "MAX_SLOT", "THIN",
           "FREE_FORMATIONS", "formations", "starters_per_slot",
           "Rating", "Scorer", "pick_xi", "squad_pool",
           "replacement", "vor",
           "load_points", "build", "load_understat_current"]

SLOT = {
    "portero": "POR",
    "defensa": "DEF",
    "mediocampista": "MED",
    "centrocampista": "MED",
    "delantero": "DEL",
}
SLOT_LABEL = {"POR": "portero", "DEF": "defensa", "MED": "mediocampista",
              "DEL": "delantero"}
SLOT_MIN = {"POR": 1, "DEF": 3, "MED": 3, "DEL": 1}
MAX_SLOT = {"POR": 1, "DEF": 5, "MED": 5, "DEL": 3}
THIN = {"POR": 2, "DEF": 4, "MED": 4, "DEL": 2}

FREE_FORMATIONS = [(5, 4, 1), (5, 3, 2), (4, 5, 1), (4, 4, 2), (4, 3, 3),
                   (3, 5, 2), (3, 4, 3)]

SHRINK_K = 8.0
NEUTRAL_START = 60.0
ABSENT_START = 15.0
DOUBT_FACTOR = 0.5

OUT_STATUSES = frozenset({"injured", "suspended", "unavailable"})
PROMOTED_DISCOUNT = 0.70

PROMOTED_DISCOUNT_K = 50.0     # shrinkage weight, in matches, toward the default


def position_priors(market: list[dict], history: dict
                    ) -> tuple[dict[str, float], float]:
    samples: dict[str, list[float]] = {}
    for r in market:
        h = history.get(norm(r.get("name", "")))
        slot = SLOT.get((r.get("position") or "").lower())
        if h and slot and h["pj"] >= 10:
            samples.setdefault(slot, []).append(h["pts"] / h["pj"])
    priors = {k: statistics.median(v) for k, v in samples.items() if v}
    flat = [p for v in samples.values() for p in v]
    return priors, (statistics.median(flat) if flat else 0.0)


def detect_promoted(market: list[dict], history: dict) -> set[str]:
    """Teams whose squad mostly has no top-flight history -- found from the
    data (no hand-kept promotion list to go stale)."""
    per_team: dict[str, list[int]] = {}
    for r in market:
        team = r.get("team") or "?"
        h = history.get(norm(r.get("name", "")))
        tally = per_team.setdefault(team, [0, 0])
        tally[0] += 1
        tally[1] += 1 if h and h["pj"] > 0 else 0
    return {t for t, (n, k) in per_team.items() if n >= 10 and k / n < 0.15}


def fit_promoted_discount(market: list[dict], history: dict,
                          prior_of: dict) -> tuple[float, str]:
    """How much a promoted player's positional prior overstates him,
    shrunk toward PROMOTED_DISCOUNT (weight PROMOTED_DISCOUNT_K matches)
    so a thin or lopsided sample cannot swing it far from the stated
    default: measured 2026-09-24 at 0.75 pooled (0.64-0.91 by position,
    n=21-110 each -- too uneven to split), vs the shipped 0.70."""
    from ffcore.tidy import SEASON, read_csv

    promoted = {norm(t) for t in detect_promoted(market, history)}
    facts = {r["ff_id"]: (norm(r.get("team", "")),
                          SLOT.get((r.get("position") or "").lower()))
            for r in market if r.get("ff_id")}
    live = SEASON / "live"
    files = sorted(live.glob("perjornada_*.csv")) if live.exists() else []
    if not files:
        return PROMOTED_DISCOUNT, "no live per-jornada file yet"
    num = den = n = 0.0
    for r in read_csv(files[-1]):
        try:
            games = float(r.get("games_delta") or 0)
        except (TypeError, ValueError):
            continue
        if games <= 0:
            continue
        team, slot = facts.get(r.get("ff_id"), ("", None))
        if team not in promoted:
            continue
        prior = prior_of.get(slot)
        if not prior:
            continue
        try:
            pts = float(r.get("points_delta") or 0)
        except (TypeError, ValueError):
            continue
        num += pts
        den += prior * games
        n += games
    if den <= 0 or n < 1:
        return PROMOTED_DISCOUNT, "no graded promoted-player matches yet"
    measured = num / den
    k = PROMOTED_DISCOUNT_K
    fitted = (k * PROMOTED_DISCOUNT + n * measured) / (k + n)
    return fitted, ("%.2f measured pooled over %.0f promoted-player matches, "
                    "shrunk %.0f%% toward the stated %.2f -> %.2f"
                    % (measured, n, 100 * k / (k + n), PROMOTED_DISCOUNT,
                       fitted))


STATUS_FACTOR: dict[str, float] = {}


def status_multiplier(status: str) -> float:
    if status in STATUS_FACTOR:
        return STATUS_FACTOR[status]
    if status in OUT_STATUSES:
        return 0.0
    if status == "doubt":
        return DOUBT_FACTOR
    return 1.0


DECAY_GRID = (1.0, 0.85, 0.7, 0.55, 0.4)




def load_understat_current(xw=None) -> dict[str, dict]:
    from ffcore.tidy import load_understat_players, load_crosswalk

    xw = xw if xw is not None else load_crosswalk()
    if xw is None:
        return {}
    out: dict[str, dict] = {}
    for r in load_understat_players("2026"):
        if "F" not in (r.get("position") or ""):
            continue
        uid = schema.text(r, schema.UNDERSTAT_PLAYERS.UNDERSTAT_ID)
        if not uid:
            continue
        key = xw.player(understat_id=uid)
        if not key:
            continue
        player = xw.players.get(key)
        market_name = norm(player.name) if player and player.name else key
        mins = float(r.get("minutes") or 0)
        if mins <= 0:
            continue
        out[market_name] = {
            "xg90": (float(r.get("xg") or 0) + float(r.get("xa") or 0))
                    / mins * 90, "minutes": mins}
    return out


def _linreg(xs, ys) -> tuple[float, float]:
    try:
        r = statistics.linear_regression(xs, ys)
        return r.slope, r.intercept
    except statistics.StatisticsError:
        return 0.0, statistics.fmean(ys)


def _xg_points_fit(xw) -> tuple[float, float, int]:
    from ffcore.tidy import load_understat_players, SEASON, read_csv

    pts_files = sorted(SEASON.glob("points_*.csv")) if SEASON.exists() else []
    if not pts_files or xw is None:
        return 0.0, 0.0, 0
    pts_by_key = {}
    for r in read_csv(pts_files[-1]):
        pid = schema.text(r, "ff_id")
        key = pid if pid in xw.players else xw.player(
            name=r.get("player_name_full") or r.get("player_name"))
        if key:
            pts_by_key[key] = r
    xs, ys = [], []
    for r in load_understat_players("2025"):
        if "F" not in (r.get("position") or ""):
            continue
        uid = schema.text(r, schema.UNDERSTAT_PLAYERS.UNDERSTAT_ID)
        key = xw.player(understat_id=uid) if uid else None
        if not key or key not in pts_by_key:
            continue
        mins = float(r.get("minutes") or 0)
        pr = pts_by_key[key]
        games = float(pr.get("games") or 0)
        if mins < 450 or games < 10:
            continue
        xs.append((float(r.get("xg") or 0) + float(r.get("xa") or 0))
                  / mins * 90)
        ys.append(float(pr.get("points") or 0) / games)
    if len(xs) < 10:
        return 0.0, 0.0, len(xs)
    slope, intercept = _linreg(xs, ys)
    return slope, intercept, len(xs)


def _xg_stickiness_boost() -> tuple[float, str]:
    from ffcore.tidy import load_understat_players

    r25 = {r["understat_id"]: r for r in load_understat_players("2025")}
    r26 = {r["understat_id"]: r for r in load_understat_players("2026")}
    common = set(r25) & set(r26)
    pairs = []
    for uid in common:
        a, b = r25[uid], r26[uid]
        m25 = float(a.get("minutes") or 0)
        m26 = float(b.get("minutes") or 0)
        if m25 < 450 or m26 < 30:
            continue
        pairs.append((
            (float(a.get("goals") or 0) + float(a.get("assists") or 0))
            / m25 * 90,
            (float(b.get("goals") or 0) + float(b.get("assists") or 0))
            / m26 * 90,
            (float(a.get("xg") or 0) + float(a.get("xa") or 0)) / m25 * 90,
            (float(b.get("xg") or 0) + float(b.get("xa") or 0)) / m26 * 90))
    if len(pairs) < 30:
        return 1.0, ("only %d paired players (need 30) — trusting an "
                     "xG match the same as a raw one until more "
                     "accumulate" % len(pairs))

    def corr(xs, ys):
        try:
            return statistics.correlation(xs, ys)
        except statistics.StatisticsError:
            return 0.0

    r_raw = max(0.02, min(0.9, corr([p[0] for p in pairs],
                                    [p[1] for p in pairs])))
    r_xg = max(0.02, min(0.9, corr([p[2] for p in pairs],
                                   [p[3] for p in pairs])))
    k_raw_odds = (1 - r_raw) / r_raw
    k_xg_odds = (1 - r_xg) / r_xg
    boost = max(0.5, min(3.0, k_raw_odds / k_xg_odds))
    return boost, ("%d paired players: G+A/90 year-over-year r=%.3f, "
                   "xG+xA/90 r=%.3f -> boost %.2f"
                   % (len(pairs), r_raw, r_xg, boost))


def _shots_by_jornada(xw) -> dict[str, dict[int, float]]:
    from ffcore.tidy import load_api_stats

    if xw is None:
        return {}
    out: dict[str, dict[int, float]] = {}
    for r in load_api_stats():
        if r.get("stat") != "total_scoring_att":
            continue
        key = xw.player(app_id=schema.text(r, schema.API_STATS.PLAYER_ID))
        if not key:
            continue
        try:
            wk = int(r.get("week"))
            val = float(r.get("value") or 0)
        except (TypeError, ValueError):
            continue
        out.setdefault(key, {})[wk] = val
    return out


def _forward_shots_minutes(xw, players=None):
    """Forward players' shots-per-jornada, minutes-per-jornada (both keyed
    by crosswalk key), and the players table -- the shared setup
    _shots_points_fit() and load_shots_current() each rebuilt separately
    (same _shots_by_jornada() call, same _per_jornada_current() call, same
    "pos == delantero" filter applied downstream) before diverging into a
    fit and a rate. () for by_key when no perjornada file exists yet."""
    from ffcore.tidy import (SEASON, load, load_players,
                             load_perjornada, jornada_of_match)

    players = players if players is not None else load_players()
    shots_by_key = _shots_by_jornada(xw)
    live = SEASON / "live"
    files = sorted(live.glob("perjornada_*.csv")) if live.exists() else []
    by_key = _per_jornada_current(load("starters"), load_perjornada(),
                                  jornada_of_match(), xw) if files else {}
    return shots_by_key, by_key, players


def _shots_points_fit(xw, players=None) -> tuple[float, float, int]:
    if xw is None:
        return 0.0, 0.0, 0
    shots_by_key, by_key, players = _forward_shots_minutes(xw, players)
    if not by_key:
        return 0.0, 0.0, 0

    xs, ys = [], []
    for key, jd_shots in shots_by_key.items():
        if (players.get(key) or {}).get("pos", "").lower() != "delantero":
            continue
        jd_points = by_key.get(key, {})
        common = sorted(set(jd_shots) & set(jd_points))
        if len(common) < 2:
            continue
        prior, last = common[:-1], common[-1]
        total_min = sum(jd_points[j][1] for j in prior)
        if total_min <= 0:
            continue
        shots90 = sum(jd_shots[j] for j in prior) / total_min * 90
        last_pts, last_min = jd_points[last]
        if last_min <= 0:
            continue
        xs.append(shots90)
        ys.append(last_pts)
    if len(xs) < 10:
        return 0.0, 0.0, len(xs)
    slope, intercept = _linreg(xs, ys)
    return slope, intercept, len(xs)


def load_shots_current(xw=None, players=None) -> dict[str, dict]:
    from ffcore.tidy import load_crosswalk

    xw = xw if xw is not None else load_crosswalk()
    if xw is None:
        return {}
    shots_by_key, by_key, players = _forward_shots_minutes(xw, players)
    minutes_by_key = {k: {j: mins for j, (_pts, mins) in jd.items()}
                      for k, jd in by_key.items()}
    out = {}
    for key, jd_shots in shots_by_key.items():
        if (players.get(key) or {}).get("pos", "").lower() != "delantero":
            continue
        player = xw.players.get(key)
        if not player or not player.name:
            continue
        mins_jd = minutes_by_key.get(key, {})
        total_min = sum(mins_jd.get(j, 0.0) for j in jd_shots)
        if total_min <= 0:
            continue
        total_shots = sum(jd_shots.values())
        out[norm(player.name)] = {"shots90": total_shots / total_min * 90,
                                  "minutes": total_min}
    return out


def _per_jornada_current(starters_rows, perjornada_rows, jornada_of_match,
                         xw) -> dict[str, dict[int, tuple[float, float]]]:
    minutes_by_jor: dict[str, dict[int, float]] = {}
    seen: set[tuple[str, str]] = set()
    for r in starters_rows:
        slug = schema.text(r, schema.STARTERS.PLAYER_SLUG)
        mid = schema.text(r, schema.STARTERS.MATCH_ID)
        jor = jornada_of_match.get(mid)
        if not slug or jor is None or r.get("role") not in ("starter", "sub"):
            continue
        key = xw.player(ff_slug=slug, name=r.get("player_name"))
        if not key:
            continue
        dedup = (mid, key)
        if dedup in seen:
            continue
        seen.add(dedup)
        by_j = minutes_by_jor.setdefault(key, {})
        by_j[jor] = by_j.get(jor, 0.0) + minutes_played(r.get("role"),
                                                         r.get("minute"))

    end_total: dict[str, dict[int, float]] = {}
    seen_at: dict[str, dict[int, str]] = {}
    for r in perjornada_rows:
        raw_jor = schema.text(r, schema.PERJORNADA.JORNADA)
        if not raw_jor:
            continue
        jor = int(raw_jor)
        pid = schema.text(r, schema.PERJORNADA.FF_ID)
        key = pid if pid in xw.players else xw.player(
            name=r.get("player_name_full") or r.get("player_name"))
        if not key:
            continue
        total = ratio(r.get("points_total"))
        if total is None:
            continue
        end_total.setdefault(key, {})[jor] = total
        seen_at.setdefault(key, {})[jor] = (r.get("to_stamp")
                                            or r.get("from_stamp") or "")

    points_by_jor: dict[str, dict[int, float]] = {}
    for key, totals in end_total.items():
        order = sorted(totals, key=lambda j: seen_at[key].get(j, ""))
        prev = 0.0
        for jor in order:
            points_by_jor.setdefault(key, {})[jor] = totals[jor] - prev
            prev = totals[jor]

    out: dict[str, dict[int, tuple[float, float]]] = {}
    for key, points_jd in points_by_jor.items():
        minutes_jd = minutes_by_jor.get(key, {})
        jors = set(points_jd) | set(minutes_jd)
        out[key] = {j: (points_jd.get(j, 0.0), minutes_jd.get(j, 0.0))
                   for j in jors}
    return out


def _decay_walk(per_jornada: dict[int, tuple[float, float]], decay: float,
                terms) -> tuple[float, float]:
    """Decay-weighted sum of terms(pts, mins, w) over `per_jornada`, newest
    jornada weighted 1.0 -- the accumulation loop _weighted_totals() and
    _weighted_start() each walked separately, only the per-step terms
    differ."""
    if not per_jornada:
        return 0.0, 0.0
    latest = max(per_jornada)
    a = b = 0.0
    for j, (pts, mins) in per_jornada.items():
        w = decay ** (latest - j)
        da, db = terms(pts, mins, w)
        a += da
        b += db
    return a, b


def _weighted_totals(per_jornada: dict[int, tuple[float, float]],
                     decay: float) -> tuple[float, float]:
    return _decay_walk(per_jornada, decay,
                       lambda pts, mins, w: (pts * w, (mins / 90.0) * w))


def _weighted_start(per_jornada: dict[int, tuple[float, float]],
                    decay: float) -> tuple[float, float]:
    wsum, wn = _decay_walk(
        per_jornada, decay,
        lambda pts, mins, w: (w * min(1.0, mins / 90.0), w))
    return (wsum / wn if wn else 0.0), wn


def _fit_decay(by_key: dict[str, dict[int, tuple[float, float]]]) -> tuple[float, str]:
    def walk_error(decay: float) -> tuple[float, int]:
        se, n = 0.0, 0
        for jd in by_key.values():
            jors = sorted(jd)
            for i in range(1, len(jors)):
                target = jors[i]
                actual_pts, actual_min = jd[target]
                if actual_min <= 0:
                    continue
                train = {j: jd[j] for j in jors[:i]}
                wpts, wmatch = _weighted_totals(train, decay)
                if wmatch <= 0:
                    continue
                pred = wpts / wmatch
                actual = actual_pts / (actual_min / 90.0)
                se += (pred - actual) ** 2
                n += 1
        return (se / n, n) if n else (float("inf"), 0)

    baseline, base_n = walk_error(1.0)
    if base_n == 0:
        return 1.0, "no player has a second jornada to predict yet"
    best_decay, best_err = 1.0, baseline
    for d in DECAY_GRID:
        err, n = walk_error(d)
        if n and err < best_err:
            best_decay, best_err = d, err
    if best_decay == 1.0:
        return 1.0, "flat average %.3f, no decay beat it out of sample" % baseline
    return best_decay, "decay %.2f beat flat %.3f with %.3f out of sample" % (
        best_decay, baseline, best_err)


def _current_from_perjornada() -> tuple[dict, str]:
    from ffcore.tidy import (SEASON, load, load_crosswalk,
                             load_perjornada, jornada_of_match)

    live = SEASON / "live"
    files = sorted(live.glob("perjornada_*.csv")) if live.exists() else []
    if not files:
        return {}, ""
    label = files[-1].stem.replace("perjornada_", "")
    xw = load_crosswalk()
    if xw is None:
        return {}, ""

    by_key = _per_jornada_current(load("starters"), load_perjornada(),
                                  jornada_of_match(), xw)
    decay, _why = _fit_decay(by_key)

    out = {}
    for key, per_jornada in by_key.items():
        player = xw.players.get(key)
        market_name = norm(player.name) if player else key
        wpts, wmatch = _weighted_totals(per_jornada, decay)
        start_rate, start_n = _weighted_start(per_jornada, decay)
        out[market_name] = {"pts": wpts, "pj": wmatch,
                            "start_rate": start_rate, "start_n": start_n}
    return out, label


def load_points() -> tuple[dict, str, dict, str]:
    from ffcore.tidy import SEASON, load_crosswalk, read_csv

    files = sorted(SEASON.glob("points_*.csv")) if SEASON.exists() else []
    xw = load_crosswalk()

    def read(path) -> dict:
        out: dict[str, dict] = {}
        for r in read_csv(path):
            rec = {"pts": ratio(r.get("points")) or 0.0,
                   "pj": ratio(r.get("games")) or 0.0}
            pid = schema.text(r, "ff_id")
            player = xw.players.get(pid) if pid and xw is not None else None
            if player and player.name:
                out.setdefault(norm(player.name), rec)
            for key in (r.get("player_name"), r.get("player_name_full")):
                if key:
                    out.setdefault(norm(key), rec)
        return out

    def label(path) -> str:
        return path.stem.replace("points_", "")

    cur, cur_label = _current_from_perjornada()
    if not files:
        return {}, "", cur, cur_label
    prior, prior_label = read(files[-1]), label(files[-1])
    if cur:
        return prior, prior_label, cur, cur_label
    if len(files) > 1:
        return (read(files[-2]), label(files[-2]),
                read(files[-1]), label(files[-1]))
    return prior, prior_label, {}, ""


def build(market: list[dict], xi_rows: list[dict], now,
          shrink_k: float = SHRINK_K, calibrate: bool = True) -> tuple:
    from ffcore.fixture import fixture_board
    from ffcore.tidy import load, load_fixtures

    prior, prior_label, cur, cur_label = load_points()
    cal, second = None, None
    if calibrate:
        cal, second = _calibrated()
    from ffcore.tidy import (load, load_crosswalk,
                             load_understat_players)
    xw = load_crosswalk()
    board = fixture_board(market, load_fixtures(), now, load("elo"),
                          xw=xw, results=load("results_history"),
                          understat_rows=load_understat_players("2025"))
    xg_cur = load_understat_current(xw)
    xg_slope, xg_intercept, xg_n = _xg_points_fit(xw)
    xg_boost, xg_why = _xg_stickiness_boost()
    shots_cur = load_shots_current(xw)
    shots_slope, shots_intercept, shots_n = _shots_points_fit(xw)
    priors, _global_prior = position_priors(market, prior)
    promoted_discount, promoted_why = fit_promoted_discount(
        market, prior, priors)
    sc = Scorer(market, xi_rows, prior, shrink_k=shrink_k,
                current=cur, board=board, cal=cal, second=second,
                xg=xg_cur, xg_slope=xg_slope, xg_intercept=xg_intercept,
                xg_n=xg_n, xg_boost=xg_boost, xg_why=xg_why,
                shots=shots_cur, shots_slope=shots_slope,
                shots_intercept=shots_intercept, shots_n=shots_n,
                promoted_discount=promoted_discount)
    sc.promoted_why = promoted_why
    return sc, (prior_label, cur_label)


_CAL_CACHE: list = []


def _calibrated():
    if _CAL_CACHE:
        return _CAL_CACHE[0]
    import hashlib
    import json
    from ffcore.startprob import (Calibration, METHOD_VERSION, observations,
                                  fit_start_fallbacks)
    from ffcore.tidy import load_crosswalk, load_lineups, read_csv, TIDY
    from ffcore.second import SECOND_SOURCE

    second = load_lineups(SECOND_SOURCE)
    truth = read_csv(TIDY / "starters.csv")
    cut = min((r.get("observed_at", "") for r in truth), default="")
    xw = load_crosswalk()
    global NEUTRAL_START, ABSENT_START
    if cut:
        NEUTRAL_START, ABSENT_START, _fallback_why = fit_start_fallbacks(
            load_lineups() + second, truth, cut,
            neutral_default=NEUTRAL_START, absent_default=ABSENT_START,
            xw=xw)
    # KEYED ON THE OBSERVATIONS THEMSELVES, not len(truth): starters.csv
    # grows on every scrape, so that key missed every run and the
    # leave-one-group-out grid search (36s of a 38s `squads` stage, 2026-09-24,
    # sampled) re-ran to arrive at the same answer. Building them is 0.11s.
    obs = observations(load_lineups() + second, truth, cut,
                       neutral=NEUTRAL_START, absent=ABSENT_START,
                       xw=xw) if cut else []
    stamp = "%d:%s" % (METHOD_VERSION,
                       hashlib.sha1(repr(obs).encode()).hexdigest())
    path = TIDY / "startcal.json"
    cal = Calibration()
    try:
        was = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        was = {}
    if cut and was.get("fingerprint") == stamp:
        cal = Calibration(was["alpha"], was["beta"], was["weight"],
                          was["titular"], was["n"], was["fitted"],
                          was["gain"], was["why"], was["groups"])
    elif cut:
        cal = Calibration.fit(obs)
        try:
            path.write_text(json.dumps({
                "fingerprint": stamp, "alpha": cal.alpha, "beta": cal.beta,
                "weight": cal.weight, "titular": cal.titular, "n": cal.n,
                "fitted": cal.fitted, "gain": cal.gain, "why": cal.why,
                "groups": cal.groups}) + "\n", encoding="utf-8")
        except OSError:
            pass
    from ffcore.lineupweight import fit_lineup_weight, fit_status_factors
    cal.lineup_k, cal.lineup_why = fit_lineup_weight()
    flagged = fit_status_factors()
    STATUS_FACTOR.clear()
    STATUS_FACTOR.update({f: v for f, (v, _n) in flagged.items()})
    if flagged:
        cal.lineup_why += "; regulars flagged " + ", ".join(
            "%s played %.0f%% of normal (%d)" % (f, 100 * v, n)
            for f, (v, n) in sorted(flagged.items()))
    _CAL_CACHE.append((cal, second))
    return _CAL_CACHE[0]


def formations() -> list[tuple]:
    return list(FREE_FORMATIONS)


class Rating(NamedTuple):
    ppm: float
    why: str
    assumed: bool
    cur_pj: float = 0.0
    pj: float = 0.0


class Scored(NamedTuple):
    name: str
    key: str
    slot: str
    pos: str
    score: float
    flat: float
    ppm: float
    pct: float | None
    pct_used: float
    pct_rest: float
    on_page: bool
    status: str
    assumed: bool
    value: float
    fix: float = 1.0
    opp: str = ""
    home: bool = True
    cur_pj: float = 0.0
    pj: float = 0.0
    fix_basis: str = "none"
    elo_gap: float | None = None

    def as_row(self) -> dict:
        return dict(self._asdict())


class Scorer:

    def __init__(self, market: list[dict], xi: list[dict],
                 history: dict | None = None, shrink_k: float = SHRINK_K,
                 current: dict | None = None, board: dict | None = None, cal=None, second=None,
                 xg: dict | None = None, xg_slope: float = 0.0,
                 xg_intercept: float = 0.0, xg_n: int = 0,
                 xg_boost: float = 1.0, xg_why: str = "",
                 shots: dict | None = None, shots_slope: float = 0.0,
                 shots_intercept: float = 0.0, shots_n: int = 0,
                 promoted_discount: float = PROMOTED_DISCOUNT):
        self.market = market
        self.history = history or {}
        self.shrink_k = shrink_k
        self.promoted_discount = promoted_discount
        self.current = current or {}
        self.board = board or {}
        self.xg = xg or {}
        self.xg_slope = xg_slope
        self.xg_intercept = xg_intercept
        self.xg_n = xg_n
        self.xg_boost = xg_boost
        self.xg_why = xg_why
        self.shots = shots or {}
        self.shots_slope = shots_slope
        self.shots_intercept = shots_intercept
        self.shots_n = shots_n

        from ffcore.tidy import row_key, shared_names, load_crosswalk

        shared = shared_names(market)
        self.lookup: dict[str, dict] = {}
        self._name_keys: dict[str, list] = {}
        for r in market:
            if r.get("name"):
                k = row_key(r, shared)
                self.lookup[k] = r
                seen_for = self._name_keys.setdefault(norm(r.get("name")), [])
                if k not in seen_for:
                    seen_for.append(k)
        xw = load_crosswalk()
        self._by_ff_slug = {norm(p.ff_slug): p.player_id
                            for p in (xw.players.values() if xw else ())
                            if p.ff_slug}

        self.cal = cal or Calibration()
        self.second: dict[str, dict] = {}
        for r in second or []:
            k = self._by_ff_slug.get(norm(r.get("player_slug") or ""))
            if not k:
                hits = self._name_keys.get(norm(r.get("player_name") or ""), [])
                k = hits[0] if len(hits) == 1 else None
            if k:
                self.second[k] = r

        self.start_pct: dict[str, float] = {}
        self.listed: set[str] = set()
        self.status: dict[str, str] = {}
        for r in xi or []:
            key = self._by_ff_slug.get(norm(r.get("player_slug") or ""))
            if not key:
                hits = self._name_keys.get(norm(r.get("player_name") or ""), [])
                key = hits[0] if len(hits) == 1 else None
            if not key:
                continue
            self.listed.add(key)
            p = pct100(r.get("start_pct"))
            if p is not None and p >= 0:
                self.start_pct[key] = max(self.start_pct.get(key, 0.0), p)
            if r.get("status") and r["status"] != "ok":
                self.status[key] = r["status"]

        self.promoted = self._detect_promoted()
        self.priors, self.global_prior = self._priors()


    def _detect_promoted(self) -> set[str]:
        return detect_promoted(self.market, self.history)

    def _priors(self):
        return position_priors(self.market, self.history)


    def rate(self, rec: dict) -> Rating:
        key = norm(rec.get("name", ""))
        slot = SLOT.get((rec.get("position") or "").lower(), "")
        prior = self.priors.get(slot, self.global_prior)
        k = self.shrink_k

        h = self.history.get(key)
        prior_pj = float(h["pj"]) if h and h["pj"] > 0 else 0.0
        if h and h["pj"] > 0:
            base, why, assumed = ((h["pts"] + k * prior) / (h["pj"] + k),
                                  "%.0fp/%.0fj" % (h["pts"], h["pj"]), False)
        elif (rec.get("team") or "") in self.promoted:
            base, why, assumed = prior * self.promoted_discount, "assumed", True
        else:
            base, why, assumed = prior, "assumed", True

        c = self.current.get(key)
        cur_pj = float(c["pj"]) if c and c["pj"] > 0 else 0.0
        terms = [(k, base)]
        if cur_pj > 0:
            terms.append((cur_pj, c["pts"] / cur_pj))
        xg = self.xg.get(key)
        xg_note = ""
        if xg and xg["minutes"] > 0:
            xg_matches = xg["minutes"] / 90.0 * self.xg_boost
            xg_rate = self.xg_slope * xg["xg90"] + self.xg_intercept
            terms.append((xg_matches, xg_rate))
            xg_note = " + xg %.2f/%.1fj" % (xg_rate, xg["minutes"] / 90.0)
        shots = self.shots.get(key)
        shots_note = ""
        if shots and shots["minutes"] > 0 and self.shots_n >= 10:
            shots_matches = shots["minutes"] / 90.0
            shots_rate = (self.shots_slope * shots["shots90"]
                          + self.shots_intercept)
            terms.append((shots_matches, shots_rate))
            shots_note = (" + shots %.2f/%.1fj"
                         % (shots_rate, shots["minutes"] / 90.0))
        if len(terms) == 1:
            return Rating(base, why, assumed, 0.0, prior_pj)
        w_sum = sum(w for w, _ in terms)
        blended = sum(w * m for w, m in terms) / w_sum
        why_now = why
        if cur_pj > 0:
            why_now += " + %.0fp/%.0fj now" % (c["pts"], cur_pj)
        why_now += xg_note + shots_note
        return Rating(blended, why_now, assumed and cur_pj < k, cur_pj,
                     prior_pj + cur_pj)

    def row_for(self, name):
        return self.lookup.get(name) or self.lookup.get(norm(name))

    def score(self, rec: dict) -> Scored:
        from ffcore.tidy import row_key
        key = row_key(rec, ()) or norm(rec.get("name", ""))
        st = self.status.get(key, "")
        pct = self.start_pct.get(key)
        on_page = key in self.listed
        rating = self.rate(rec)

        raw = pct if pct is not None else (
            NEUTRAL_START if on_page else ABSENT_START)
        pct_used = 100.0 * self.cal.p(raw, self.second.get(key))
        cur = self.current.get(norm(rec.get("name", "")))
        start_n = cur.get("start_n", 0.0) if cur else 0.0
        if start_n > 0.0:
            k_s, k_l = self.shrink_k, self.cal.lineup_k or self.shrink_k
            pct_rest = (k_s * NEUTRAL_START + start_n * 100.0
                       * cur["start_rate"]) / (k_s + start_n)
            pct_used = (k_l * pct_used + start_n * 100.0 * cur["start_rate"]
                       ) / (k_l + start_n)
        else:
            pct_rest = pct_used
        m = self.board.get(schema.text(rec, schema.MARKET.TEAM))
        slot = SLOT.get((rec.get("position") or "").lower(), "")
        fix_factor = (m.def_factor if slot in ("POR", "DEF")
                     else m.atk_factor) if m else 1.0
        flat = rating.ppm * pct_used / 100.0
        score = flat * fix_factor
        mult = status_multiplier(st)
        score *= mult
        flat *= mult

        return Scored(
            name=rec.get("name", key), key=key,
            slot=slot,
            pos=(rec.get("position") or "").lower(),
            score=score, flat=flat, fix=fix_factor,
            opp=m.opponent if m else "", home=m.home if m else True,
            fix_basis=m.basis if m else "none",
            elo_gap=m.gap if m else None,
            cur_pj=rating.cur_pj, pj=rating.pj,
            ppm=rating.ppm, pct=pct, pct_used=pct_used, pct_rest=pct_rest,
            on_page=on_page, status=st,
            assumed=rating.assumed,
            value=money(rec.get("value")) or 0.0,
        )

    def score_squad(self, names) -> tuple[list[Scored], list[str]]:
        out, missing = [], []
        for n in names:
            r = self.row_for(n)
            if r is None:
                missing.append(n)
            else:
                out.append(self.score(r))
        return out, missing


def squad_pool(scored) -> dict[str, list[dict]]:
    pool: dict[str, list[dict]] = {}
    for p in scored:
        row = p.as_row() if isinstance(p, Scored) else p
        if row.get("slot"):
            pool.setdefault(row["slot"], []).append(row)
    for v in pool.values():
        v.sort(key=lambda p: p["score"], reverse=True)
    return pool




def starters_per_slot() -> dict[str, float]:
    shapes = formations()
    n = len(shapes)
    tot = {"POR": float(n), "DEF": 0.0, "MED": 0.0, "DEL": 0.0}
    for d, m, f in shapes:
        tot["DEF"] += d
        tot["MED"] += m
        tot["DEL"] += f
    return {k: v / n for k, v in tot.items()}


def replacement(pool: dict, squads: int) -> dict[str, float]:
    per = starters_per_slot()
    out = {}
    for slot, rows in pool.items():
        if not rows:
            continue
        rung = max(1, round(squads * per.get(slot, 0.0)))
        out[slot] = rows[min(rung, len(rows)) - 1]["score"]
    return out


def vor(row: dict, repl: dict) -> float:
    slot = row.get("slot")
    if not slot:
        return 0.0
    return row.get("score", 0.0) - repl.get(slot, 0.0)



def _xi_search(by_slot: dict[str, list], shapes, force=None):
    if force is None:
        prefix: dict[str, list[float]] = {}
        for slot, items in by_slot.items():
            acc, ps = 0.0, [0.0]
            for _, v in items:
                acc += v
                ps.append(acc)
            prefix[slot] = ps

        best = None
        for shape in shapes:
            picked, total, ok = [], 0.0, True
            for slot, n in shape.items():
                items = by_slot.get(slot, [])
                if len(items) < n:
                    ok = False
                    break
                picked += [it for it, _ in items[:n]]
                total += prefix[slot][n]
            if ok and (best is None or total > best[0]):
                best = (total, shape, picked)
        return best

    f_item, f_slot, f_val = force
    best = None
    for shape in shapes:
        if not f_slot or shape.get(f_slot, 0) < 1:
            continue
        picked, ok = [], True
        for slot, n in shape.items():
            items = by_slot.get(slot, [])
            if slot == f_slot:
                rest = [it for it in items if it[0] is not f_item][:n - 1]
                take = [(f_item, f_val)] + rest
            else:
                take = items[:n]
            if len(take) < n:
                ok = False
                break
            picked += take
        if not ok:
            continue
        total = sum(v for _, v in picked)
        if best is None or total > best[0]:
            best = (total, shape, [it for it, _ in picked])
    return best


def pick_xi(pool: dict, force: dict | None = None):
    by_slot = {slot: [(p, p["score"]) for p in rows]
              for slot, rows in pool.items()}
    shapes = [{"POR": 1, "DEF": d, "MED": m, "DEL": f}
             for d, m, f in formations()]
    f = (force, force["slot"], force["score"]) if force is not None else None
    got = _xi_search(by_slot, shapes, f)
    if got is None:
        return None
    total, shape, picked = got
    return total, (shape["DEF"], shape["MED"], shape["DEL"]), picked


def _selftest() -> None:
    assert status_multiplier("injured") == 0.0 and status_multiplier("doubt") == DOUBT_FACTOR
    STATUS_FACTOR["injured"] = 0.5
    try:
        assert status_multiplier("injured") == 0.5 and status_multiplier("ok") == 1.0
    finally:
        STATUS_FACTOR.clear()
    from ffcore.fixture import Match

    def mk(name, pos="defensa", team="Mid", value="10.00M"):
        return {"name": name, "position": pos, "team": team, "value": value}

    market = [mk("p%d" % i) for i in range(10)] + [mk("Sub"), mk("Newbie")]
    hist = {"p%d" % i: {"pts": 100.0 + i, "pj": 34.0} for i in range(10)}
    hist["sub"] = {"pts": 20.0, "pj": 4.0}
    xi = [{"player_name": n, "start_pct": "100"}
          for n in [r["name"] for r in market]]

    sc = Scorer(market, xi, hist)
    prior = sc.priors["DEF"]
    assert 3.0 < prior < 3.1, prior

    thin = sc.rate(mk("Sub"))
    assert abs(thin.ppm - (20.0 + 8 * prior) / (4.0 + 8)) < 1e-9
    assert not thin.assumed and thin.cur_pj == 0.0
    assert sc.rate(mk("Newbie")).assumed

    full = sc.rate(mk("p0"))
    cur = {"p0": {"pts": 30.0, "pj": 3.0}}
    sc2 = Scorer(market, xi, hist, current=cur)
    blended = sc2.rate(mk("p0"))
    assert abs(blended.ppm - (30.0 + 8 * full.ppm) / (3.0 + 8)) < 1e-9
    assert blended.cur_pj == 3.0
    assert full.ppm < blended.ppm < 10.0

    # A promoted side: 10 players, <15% with a top-flight record.
    promo_market = [{**mk("q%d" % i, team="Rise"), "ff_id": "q%d" % i}
                    for i in range(10)]
    promo_hist = {"q0": {"pts": 34.0, "pj": 34.0}}   # the one veteran on it
    assert detect_promoted(promo_market, promo_hist) == {"Rise"}
    # detect_promoted's own team strings are UNNORMALISED (as read from the
    # market) -- fit_promoted_discount must normalise before comparing
    # against team_of, or every promoted match reads as ungraded, silently
    # keeping the stated default with no error (found 2026-09-24).
    accented = [mk("r%d" % i, team="Málaga") for i in range(10)]
    assert detect_promoted(accented, {}) == {"Málaga"}
    sc3 = Scorer(promo_market, [], promo_hist)
    assert sc3.promoted == {"Rise"}
    newbie = sc3.rate(mk("q5", team="Rise"))
    assert newbie.assumed and abs(newbie.ppm - sc3.priors["DEF"]
                                  * PROMOTED_DISCOUNT) < 1e-9, newbie
    lower = Scorer(promo_market, [], promo_hist, promoted_discount=0.5)
    assert lower.rate(mk("q5", team="Rise")).ppm < newbie.ppm

    priors3 = position_priors(promo_market, promo_hist)[0]

    import tempfile
    import ffcore.tidy as _tidy_sc
    real_season = _tidy_sc.SEASON
    with tempfile.TemporaryDirectory() as _d_sc:
        _tidy_sc.SEASON = __import__("pathlib").Path(_d_sc)
        try:
            assert fit_promoted_discount(promo_market, promo_hist, priors3) \
                == (PROMOTED_DISCOUNT, "no live per-jornada file yet")

            (_tidy_sc.SEASON / "live").mkdir()
            import csv as _csv_sc
            cols = ["ff_id", "games_delta", "points_delta"]
            rows = [{"ff_id": "q%d" % (i % 10), "games_delta": "1",
                     "points_delta": "%.1f" % (priors3["DEF"]
                                               * PROMOTED_DISCOUNT)}
                    for i in range(80)]
            with open(_tidy_sc.SEASON / "live" / "perjornada_x.csv", "w",
                     newline="", encoding="utf-8") as fh:
                w = _csv_sc.DictWriter(fh, fieldnames=cols)
                w.writeheader()
                w.writerows(rows)
            # exactly the shipped ratio, on real rows: shrinkage should
            # barely move it, and the confirming direction is unambiguous
            fitted, why = fit_promoted_discount(promo_market, promo_hist,
                                                priors3)
            assert abs(fitted - PROMOTED_DISCOUNT) < 0.01, (fitted, why)
            assert "80 promoted-player matches" in why, why

            # a side that scores at the full prior (no discount at all,
            # discount=1.0): the fit must move UP off the stated default
            full_rows = [{"ff_id": "q%d" % (i % 10), "games_delta": "1",
                         "points_delta": "%.1f" % priors3["DEF"]}
                        for i in range(400)]
            with open(_tidy_sc.SEASON / "live" / "perjornada_x.csv", "w",
                     newline="", encoding="utf-8") as fh:
                w = _csv_sc.DictWriter(fh, fieldnames=cols)
                w.writeheader()
                w.writerows(full_rows)
            fitted2, _why2 = fit_promoted_discount(promo_market, promo_hist,
                                                   priors3)
            assert fitted2 > PROMOTED_DISCOUNT + 0.15, fitted2
        finally:
            _tidy_sc.SEASON = real_season
    assert "now" in blended.why and "3j" in blended.why

    assert Scorer(market, xi, hist, current={}).rate(mk("p0")) == full
    assert Scorer(market, xi, hist,
                  current={"p0": {"pts": 0.0, "pj": 0.0}}).rate(mk("p0")) \
        == full

    when = __import__("datetime").datetime.fromisoformat(
        "2026-08-20T19:00:00+00:00")
    easy = Match("Elche", True, when, atk_factor=1.30, def_factor=1.10,
                rank=20, of=20)
    sc3 = Scorer(market, xi, hist, board={"Mid": easy})
    s = sc3.score(mk("p0"))
    assert abs(s.flat - full.ppm) < 1e-9
    assert abs(s.score - full.ppm * 1.10) < 1e-9
    assert s.opp == "Elche" and s.home and s.fix == 1.10
    fwd = sc3.score(mk("p0", pos="delantero"))
    assert abs(fwd.score - full.ppm * 1.30) < 1e-9, fwd
    assert fwd.fix == 1.30
    solo = Scorer(market, xi, hist, board={}).score(mk("p0"))
    assert solo.fix == 1.0 and solo.opp == "" and solo.score == solo.flat

    out = [{"player_name": "p0", "start_pct": "100", "status": "suspended"}]
    zero = Scorer(market, out, hist, board={"Mid": easy}).score(mk("p0"))
    assert zero.score == 0.0 and zero.flat == 0.0
    dbt = [{"player_name": "p0", "start_pct": "100", "status": "doubt"}]
    half = Scorer(market, dbt, hist, board={"Mid": easy}).score(mk("p0"))
    assert abs(half.flat - full.ppm * DOUBT_FACTOR) < 1e-9
    assert abs(half.score - full.ppm * 1.10 * DOUBT_FACTOR) < 1e-9

    assert "fix" in s.as_row() and "flat" in s.as_row()

    per = starters_per_slot()
    assert per == {"POR": 1.0, "DEF": 4.0, "MED": 4.0, "DEL": 2.0}, per
    assert abs(sum(per.values()) - 11.0) < 1e-9
    pool = {"POR": [{"score": s_} for s_ in (9.0, 8.0, 7.0, 6.0, 5.0, 4.0)],
           "DEL": [{"score": s_} for s_ in (9.0, 8.0)]}
    repl = replacement(pool, squads=5)
    assert repl["POR"] == 5.0, repl
    assert repl["DEL"] == 8.0, repl
    assert vor({"slot": "POR", "score": 9.0}, repl) == 4.0
    assert vor({"slot": "DEL", "score": 6.0}, repl) == -2.0
    assert vor({"slot": ""}, repl) == 0.0

    benched_cur = {"p0": {"pts": 30.0, "pj": 3.0,
                          "start_rate": 0.0, "start_n": 6.0}}
    sc4 = Scorer(market, xi, hist, current=benched_cur, board={"Mid": easy})
    benched_s = sc4.score(mk("p0"))
    assert benched_s.pct_used < 100.0, benched_s.pct_used
    assert abs(benched_s.pct_used - 800.0 / 14.0) < 1e-9, benched_s.pct_used

    untouched = Scorer(market, xi, hist, current={}, board={"Mid": easy}
                       ).score(mk("p0"))
    assert untouched.pct_used == 100.0, untouched.pct_used
    assert untouched.pct_rest == 100.0, untouched.pct_rest

    starter_cur = {"p0": {"pts": 30.0, "pj": 2.0,
                          "start_rate": 0.9, "start_n": 2.0}}
    susp = [{"player_name": "p0", "start_pct": "0", "status": "suspended"}]
    sc5 = Scorer(market, susp, hist, current=starter_cur, board={"Mid": easy})
    susp_s = sc5.score(mk("p0"))
    assert abs(susp_s.pct_used - (8 * 0.0 + 2 * 90.0) / 10) < 1e-9, susp_s
    assert abs(susp_s.pct_rest - (8 * NEUTRAL_START + 2 * 90.0) / 10) < 1e-9, \
        susp_s
    assert susp_s.pct_rest > susp_s.pct_used + 25.0, susp_s

    from ffcore.crosswalk import Crosswalk, Player

    xw2 = Crosswalk({
        "antonio blanco": Player("antonio blanco", "Antonio Blanco",
                                 ff_slug="blanco", app_id="1"),
        "came on": Player("came on", "Came On", ff_slug="came-on"),
        "unused sub": Player("unused sub", "Unused Sub", ff_slug="unused"),
    }, {})
    jornada_map = {"m1": 1, "m2": 2}
    starters_rows = [
        {"player_name": "Blanco", "player_slug": "blanco",
         "role": "starter", "minute": "", "match_id": "m1"},
        {"player_name": "Came On", "player_slug": "came-on",
         "role": "sub", "minute": "70", "match_id": "m1"},
        {"player_name": "Unused Sub", "player_slug": "unused",
         "role": "sub", "minute": "", "match_id": "m1"},
        {"player_name": "Blanco", "player_slug": "blanco",
         "role": "starter", "minute": "45", "match_id": "m2"},
        {"player_name": "Blanco", "player_slug": "blanco",
         "role": "coach", "minute": "", "match_id": "m3"},
        {"player_name": "Nobody", "player_slug": "", "role": "starter",
         "minute": "", "match_id": "m1"},
        *({"player_name": "Blanco", "player_slug": "blanco",
           "role": "starter", "minute": "", "match_id": "m1"}
          for _ in range(56)),
    ]
    perjornada_rows = [
        {"ff_id": "1", "player_name_full": "Antonio Blanco",
         "points_delta": "3", "points_total": "8", "jornada": "1"},
        {"ff_id": "1", "player_name_full": "Antonio Blanco",
         "points_delta": "5", "points_total": "13", "jornada": "2"},
        {"ff_id": "1", "player_name_full": "Antonio Blanco",
         "points_delta": "99", "points_total": "112", "jornada": ""},
    ]
    by_key = _per_jornada_current(starters_rows, perjornada_rows,
                                  jornada_map, xw2)
    assert by_key["antonio blanco"] == {1: (8.0, 90.0), 2: (5.0, 45.0)}, \
        by_key["antonio blanco"]
    assert "came on" not in by_key, by_key
    assert "unused sub" not in by_key, by_key
    assert _per_jornada_current([], [], {}, xw2) == {}

    corrected = _per_jornada_current(
        starters_rows,
        [{"ff_id": "1", "player_name_full": "Antonio Blanco",
          "points_total": "8", "jornada": "1"},
         {"ff_id": "1", "player_name_full": "Antonio Blanco",
          "points_total": "9", "jornada": "1"}],
        jornada_map, xw2)
    assert corrected["antonio blanco"] == {1: (9.0, 90.0), 2: (0.0, 45.0)}, \
        corrected

    wpts, wmatch = _weighted_totals(by_key["antonio blanco"], 1.0)
    assert (wpts, wmatch) == (13.0, 1.5), (wpts, wmatch)
    wpts, wmatch = _weighted_totals(by_key["antonio blanco"], 0.5)
    assert abs(wpts - 9.0) < 1e-9, wpts
    assert _weighted_totals({}, 0.5) == (0.0, 0.0)

    one_jornada = {"a": {1: (4.0, 90.0)}, "b": {1: (2.0, 45.0)}}
    decay, why = _fit_decay(one_jornada)
    assert decay == 1.0 and "second jornada" in why, (decay, why)
    assert _fit_decay({}) == (1.0, "no player has a second jornada to "
                              "predict yet")
    decay0, _ = _fit_decay(
        {p: {1: (1.0, 90.0), 2: (9.0, 90.0)} for p in "ab"})
    assert decay0 == 1.0, decay0

    trending = {p: {1: (1.0, 90.0), 2: (5.0, 90.0), 3: (9.0, 90.0)}
               for p in ("p%d" % i for i in range(8))}
    decay2, why2 = _fit_decay(trending)
    assert decay2 < 1.0 and "beat flat" in why2, (decay2, why2)

    from ffcore.crosswalk import Crosswalk as _CW, Player as _P
    import ffcore.tidy as _tidy
    import tempfile as _tempfile
    import os as _os
    import csv as _csv

    xw_us = _CW({
        "striker": _P("striker", "Striker Sam", understat_id="10"),
        "defender": _P("defender", "Defender Dan", understat_id="20"),
    }, {})
    with _tempfile.TemporaryDirectory() as _d:
        _os.makedirs(_d, exist_ok=True)
        path = _os.path.join(_d, "understat_players.csv")
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = _csv.DictWriter(fh, fieldnames=[
                "observed_at", "source", "season", "understat_id",
                "player_name", "team_title", "team", "position", "games",
                "minutes", "goals", "assists", "xg", "xa", "npg", "npxg",
                "shots", "key_passes"])
            w.writeheader()
            w.writerow({"observed_at": "2026-08-21T0000Z", "source": "understat",
                       "season": "2026", "understat_id": "10",
                       "player_name": "Striker Sam", "team_title": "X",
                       "team": "x", "position": "F S", "games": "1",
                       "minutes": "90", "goals": "1", "assists": "0",
                       "xg": "0.6", "xa": "0.2", "npg": "1", "npxg": "0.6",
                       "shots": "3", "key_passes": "1"})
            w.writerow({"observed_at": "2026-08-21T0000Z", "source": "understat",
                       "season": "2026", "understat_id": "20",
                       "player_name": "Defender Dan", "team_title": "X",
                       "team": "x", "position": "D S", "games": "1",
                       "minutes": "90", "goals": "0", "assists": "0",
                       "xg": "0.1", "xa": "0.0", "npg": "0", "npxg": "0.1",
                       "shots": "1", "key_passes": "0"})
        _real_tidy = _tidy.TIDY
        _tidy.TIDY = __import__("pathlib").Path(_d)
        try:
            us_cur = load_understat_current(xw_us)
            boost_thin, why_thin = _xg_stickiness_boost()
        finally:
            _tidy.TIDY = _real_tidy
    assert set(us_cur) == {"striker sam"}, us_cur
    assert abs(us_cur["striker sam"]["xg90"] - 0.8) < 1e-9
    assert boost_thin == 1.0 and "30" in why_thin, (boost_thin, why_thin)

    slope, intercept = _linreg([1.0, 2.0, 3.0], [2.0, 4.0, 6.0])
    assert abs(slope - 2.0) < 1e-9 and abs(intercept) < 1e-9
    slope0, intercept0 = _linreg([1.0, 1.0, 1.0], [5.0, 5.0, 5.0])
    assert slope0 == 0.0 and abs(intercept0 - 5.0) < 1e-9

    market_xg = [mk("Attacker", pos="delantero")]
    hist_xg = {"attacker": {"pts": 100.0, "pj": 34.0}}
    xi_xg = [{"player_name": "Attacker", "start_pct": "100"}]
    sc_plain = Scorer(market_xg, xi_xg, hist_xg)
    plain = sc_plain.rate(mk("Attacker", pos="delantero"))

    sc_xg = Scorer(market_xg, xi_xg, hist_xg,
                  xg={"attacker": {"xg90": 1.0, "minutes": 180.0}},
                  xg_slope=10.0, xg_intercept=0.0, xg_boost=1.0)
    with_xg = sc_xg.rate(mk("Attacker", pos="delantero"))
    expect = (SHRINK_K * plain.ppm + 2.0 * 10.0) / (SHRINK_K + 2.0)
    assert abs(with_xg.ppm - expect) < 1e-9, (with_xg.ppm, expect)
    assert min(plain.ppm, 10.0) < with_xg.ppm < max(plain.ppm, 10.0)
    assert "xg" in with_xg.why
    assert with_xg.cur_pj == 0.0 and with_xg.pj == 34.0

    sc_noxg = Scorer(market_xg, xi_xg, hist_xg, xg={})
    assert sc_noxg.rate(mk("Attacker", pos="delantero")) == plain

    sc_shots_untrained = Scorer(
        market_xg, xi_xg, hist_xg,
        shots={"attacker": {"shots90": 3.0, "minutes": 180.0}},
        shots_slope=0.0, shots_intercept=0.0, shots_n=3)
    assert sc_shots_untrained.rate(mk("Attacker", pos="delantero")) == plain

    sc_shots = Scorer(
        market_xg, xi_xg, hist_xg,
        shots={"attacker": {"shots90": 2.0, "minutes": 180.0}},
        shots_slope=5.0, shots_intercept=0.0, shots_n=10)
    with_shots = sc_shots.rate(mk("Attacker", pos="delantero"))
    expect_shots = (SHRINK_K * plain.ppm + 2.0 * 10.0) / (SHRINK_K + 2.0)
    assert abs(with_shots.ppm - expect_shots) < 1e-9, \
        (with_shots.ppm, expect_shots)
    assert "shots" in with_shots.why

    import csv as _csv3
    import os as _os3
    import tempfile as _tempfile3
    from ffcore import tidy as _tidy3

    xw3 = Crosswalk({
        "fwd guy": Player("fwd guy", "Fwd Guy", ff_slug="fwd-guy",
                          app_id="900"),
        "def guy": Player("def guy", "Def Guy", ff_slug="def-guy",
                          app_id="901"),
    }, {})
    matches3 = [{"match_id": "m1", "jornada": "1"},
               {"match_id": "m2", "jornada": "2"},
               {"match_id": "m3", "jornada": "3"}]
    starters3 = [
        {"observed_at": "2026-09-01T0000Z", "player_name": "Fwd Guy",
         "player_slug": "fwd-guy", "role": "starter", "minute": "",
         "match_id": "m1"},
        {"observed_at": "2026-09-01T0000Z", "player_name": "Fwd Guy",
         "player_slug": "fwd-guy", "role": "starter", "minute": "",
         "match_id": "m2"},
        {"observed_at": "2026-09-01T0000Z", "player_name": "Fwd Guy",
         "player_slug": "fwd-guy", "role": "starter", "minute": "",
         "match_id": "m3"},
    ]
    perjornada3 = [
        {"ff_id": "900", "player_name_full": "Fwd Guy",
         "points_total": "2", "jornada": "1"},
        {"ff_id": "900", "player_name_full": "Fwd Guy",
         "points_total": "6", "jornada": "2"},
        {"ff_id": "900", "player_name_full": "Fwd Guy",
         "points_total": "16", "jornada": "3"},
    ]
    with _tempfile3.TemporaryDirectory() as _d3:
        _os3.makedirs(_os3.path.join(_d3, "season", "live"))
        with open(_os3.path.join(_d3, "api_stats.csv"), "w", newline="",
                 encoding="utf-8") as fh:
            w = _csv3.DictWriter(fh, fieldnames=[
                "observed_at", "source", "player_id", "week", "stat",
                "value", "points"])
            w.writeheader()
            for wk, val in ((1, "1"), (2, "3")):
                w.writerow({"observed_at": "2026-08-%02dT0000Z" % (15+wk*7),
                           "source": "laliga", "player_id": "900",
                           "week": str(wk), "stat": "total_scoring_att",
                           "value": val, "points": "0"})
            w.writerow({"observed_at": "2026-08-22T0000Z", "source": "laliga",
                       "player_id": "901", "week": "1",
                       "stat": "total_scoring_att", "value": "5",
                       "points": "0"})
        fake_players3 = {"fwd guy": {"pos": "delantero"},
                        "def guy": {"pos": "defensa"}}
        with open(_os3.path.join(_d3, "starters.csv"), "w", newline="",
                 encoding="utf-8") as fh:
            w = _csv3.DictWriter(fh, fieldnames=[
                "observed_at", "player_name", "player_slug", "role",
                "minute", "match_id"])
            w.writeheader()
            for r in starters3:
                w.writerow(r)
        with open(_os3.path.join(_d3, "matches.csv"), "w", newline="",
                 encoding="utf-8") as fh:
            w = _csv3.DictWriter(fh, fieldnames=["match_id", "jornada"])
            w.writeheader()
            for r in matches3:
                w.writerow(r)
        with open(_os3.path.join(_d3, "season", "live",
                                "perjornada_2026-27.csv"),
                 "w", newline="", encoding="utf-8") as fh:
            w = _csv3.DictWriter(fh, fieldnames=[
                "ff_id", "player_name_full", "points_total", "jornada"])
            w.writeheader()
            for r in perjornada3:
                w.writerow(r)
        _real_tidy3, _real_season3 = _tidy3.TIDY, _tidy3.SEASON
        _tidy3.TIDY = __import__("pathlib").Path(_d3)
        _tidy3.SEASON = __import__("pathlib").Path(_d3) / "season"
        try:
            sbj = _shots_by_jornada(xw3)
            assert sbj == {"fwd guy": {1: 1.0, 2: 3.0},
                          "def guy": {1: 5.0}}, sbj
            fit_slope, fit_intercept, fit_n = _shots_points_fit(
                xw3, players=fake_players3)
            assert (fit_slope, fit_intercept, fit_n) == (0.0, 0.0, 1), \
                (fit_slope, fit_intercept, fit_n)
            lsc = load_shots_current(xw3, players=fake_players3)
            assert set(lsc) == {"fwd guy"}, lsc
            assert abs(lsc["fwd guy"]["shots90"] - 2.0) < 1e-9, lsc
            assert lsc["fwd guy"]["minutes"] == 180.0, lsc
        finally:
            _tidy3.TIDY, _tidy3.SEASON = _real_tidy3, _real_season3

    print("ffcore.score self-test OK (82 cases)")


if __name__ == "__main__":
    _selftest()
