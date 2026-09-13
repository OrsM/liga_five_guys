"""
ffcore.score — the ranking index, and the legal-XI picker that consumes it.

Shared by report.py and rivals.py, so both sides of a comparison are always
scored by the same arithmetic.

    score = shrunk points-per-match  x  fixture factor  x  P(start)

Points-per-match is shrunk twice, toward the position's median then toward
last season's own shrunk figure:

    shrunk = (total_points + K * prior) / (matches + K)      K = 8 matches
    ppm = (points_now + K * shrunk_last_season) / (matches_now + K)

`matches_now` is minutes/90, not an appearance count. `score` includes the
fixture factor (for fielding this round); `flat` omits it (for buying, which
spans months).

A RANKING INDEX, not a points forecast: promoted-side players fall back to
the positional prior and are marked `assumed`; a player absent from the
probable-XI page (ABSENT_START) is scored differently from one listed with
no percentage (NEUTRAL_START).

Scoring a rival's squad: their roster comes from replaying the ledger, so an
unmatched name is silently missing from their total — report the unmatched
count alongside it.
"""

from __future__ import annotations

import statistics
from typing import NamedTuple

from ffcore.parse import money, pct100, ratio
from ffcore.startprob import Calibration
from ffcore.text import norm
from ffcore.tidy import minutes_played

__all__ = ["SLOT", "SLOT_LABEL", "SLOT_MIN", "MAX_SLOT", "THIN",
           "FREE_FORMATIONS", "formations",
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
# Most that can ever be on the pitch — anyone deeper than this in his position
# can never start under any legal formation.
MAX_SLOT = {"POR": 1, "DEF": 5, "MED": 5, "DEL": 3}
# Below this you cannot absorb a single injury without a scramble.
THIN = {"POR": 2, "DEF": 4, "MED": 4, "DEL": 2}

# Confirmed against the app's formation picker.
FREE_FORMATIONS = [(5, 4, 1), (5, 3, 2), (4, 5, 1), (4, 4, 2), (4, 3, 3),
                   (3, 5, 2), (3, 4, 3)]

# Pseudo-count in reliability = n/(n+K), used at both shrink stages in
# Scorer.rate(). Fitted against real matches, not a guess.
# Why: docs/notes/score.md#shrink_k-calibration
SHRINK_K = 8.0
NEUTRAL_START = 60.0      # listed on the XI page but no percentage given
ABSENT_START = 15.0       # not on the XI page at all — not in the picture
DOUBT_FACTOR = 0.5

# Statuses that mean he cannot play at all, as opposed to might not. Scored
# at zero rather than shrunk: a suspended player is not a risk, he is an
# absence, and the XI picker has to see that difference.
OUT_STATUSES = frozenset({"injured", "suspended", "unavailable"})
PROMOTED_DISCOUNT = 0.70  # the LaLiga median overstates a promoted squad


# Candidate half-lives for the current-season rate's recency weighting, in
# jornadas — 1.0 means no decay. Coarse grid: the data can't resolve finer.
DECAY_GRID = (1.0, 0.85, 0.7, 0.55, 0.4)


# xG/xA: a precision-weighted blend folded into Scorer.rate() as a third
# weighted term. Position-gated to forwards/attacking mids (xG carries no
# signal elsewhere); both fit parameters are derived from real data.
# Why: docs/notes/score.md#xgxa-precision-weighted-blend


def _precision_blend(estimates) -> tuple[float, float] | None:
    """(mean, variance) combining independent estimates [(mean, var), ...]
    of one quantity by inverse variance. Skips var<=0; None if nothing
    usable was offered.
    Why: docs/notes/score.md#_precision_blend--the-books-worked-example
    """
    w_sum = m_sum = 0.0
    for mean, var in estimates:
        if var is None or var <= 0:
            continue
        w = 1.0 / var
        w_sum += w
        m_sum += mean * w
    if w_sum <= 0:
        return None
    return m_sum / w_sum, 1.0 / w_sum


def load_understat_current(xw=None) -> dict[str, dict]:
    """{norm(market name): {"xg90": xG+xA per 90, "minutes": minutes}} for
    THIS season, forwards and attacking mids only. Keyed by norm(market
    name) — the same key Scorer.rate() looks `self.xg` up by.
    Position gate: docs/notes/score.md#xgxa-precision-weighted-blend
    """
    from ffcore.tidy import load_understat_players, load_crosswalk

    xw = xw if xw is not None else load_crosswalk()
    if xw is None:
        return {}
    out: dict[str, dict] = {}
    for r in load_understat_players("2026"):
        if "F" not in (r.get("position") or ""):
            continue
        uid = (r.get("understat_id") or "").strip()
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
    """(slope, intercept) of the least-squares line through (xs, ys).
    Flat line (0.0, mean(ys)) if xs is constant, not a crash.
    """
    try:
        r = statistics.linear_regression(xs, ys)
        return r.slope, r.intercept
    except statistics.StatisticsError:
        return 0.0, statistics.fmean(ys)


def _xg_points_fit(xw) -> tuple[float, float, int]:
    """(slope, intercept, n) — last season's points-per-match as a linear
    function of last season's xG+xA per 90, forwards/attacking mids only.
    Refuses (0.0, 0.0, n) below 10 paired players rather than fit noise.
    Why: docs/notes/score.md#_xg_points_fit--units-conversion
    """
    from ffcore.tidy import load_understat_players, SEASON, read_csv

    pts_files = sorted(SEASON.glob("points_*.csv")) if SEASON.exists() else []
    if not pts_files or xw is None:
        return 0.0, 0.0, 0
    pts_by_key = {}
    for r in read_csv(pts_files[-1]):
        pid = (r.get("ff_id") or "").strip()
        key = pid if pid in xw.players else xw.player(
            name=r.get("player_name_full") or r.get("player_name"))
        if key:
            pts_by_key[key] = r
    xs, ys = [], []
    for r in load_understat_players("2025"):
        if "F" not in (r.get("position") or ""):
            continue
        uid = (r.get("understat_id") or "").strip()
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
    """(boost, why) — how many raw current-season matches one xG-informed
    match is worth, from measured year-over-year stability. Recomputed
    fresh each call, no cache. Refuses (1.0, why) below 30 paired players.
    Clipped to [0.5, 3.0].
    Why: docs/notes/score.md#_xg_stickiness_boost--year-over-year-reliability
    """
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
        # 0.0 for a constant series (no correlation to report), not a crash.
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
    """{crosswalk key: {jornada: total_scoring_att that jornada}} — LaLiga's
    per-match box score (data/tidy/api_stats.csv), joined through the
    crosswalk's app_id. `week` is the API's own per-match field, not
    inferred from round-completion timing.
    """
    from ffcore.tidy import TIDY, read_csv

    if xw is None:
        return {}
    path = TIDY / "api_stats.csv"
    if not path.exists():
        return {}
    out: dict[str, dict[int, float]] = {}
    for r in read_csv(path):
        if r.get("stat") != "total_scoring_att":
            continue
        key = xw.player(app_id=(r.get("player_id") or "").strip())
        if not key:
            continue
        try:
            wk = int(r.get("week"))
            val = float(r.get("value") or 0)
        except (TypeError, ValueError):
            continue
        out.setdefault(key, {})[wk] = val
    return out


def backtest_predictor(feature_by_key_jornada: dict[str, dict[int, float]],
                       actual_by_key_jornada: dict[str, dict[int, float]],
                       min_pairs: int = 10) -> dict | None:
    """Does FEATURE (through a player's second-to-last shared jornada)
    predict his LAST shared jornada's real outcome better than the pooled
    sample mean — a general, reusable leave-one-player-out significance
    test. Pass in any two {key: {jornada: value}} dicts.

    BASELINE IS THE POOLED SAMPLE MEAN, NOT EACH PLAYER'S OWN AVERAGE —
    with only 1-3 prior jornadas per player, an unpooled individual
    average is noisy enough that any fitted line beats it purely by
    pooling (regression to the mean), making every candidate feature look
    like it "works." Matches rate_baseline_check()'s convention.

    One test point per player (his own last shared jornada), graded
    leave-one-player-out: the fit and baseline for player P never see P's
    own held-out point.

    Returns {"n", "mae_feature", "mae_baseline", "gap"} (gap is
    stats.bootstrap_gap() on the paired MAE series). None below
    `min_pairs` players with >=2 shared jornadas.
    Why: docs/notes/score.md#backtest_predictor--the-pooled-baseline-fix
    """
    import statistics as _statistics

    from stats import bootstrap_gap

    pairs = []
    for key, jd_feat in feature_by_key_jornada.items():
        jd_actual = actual_by_key_jornada.get(key, {})
        common = sorted(set(jd_feat) & set(jd_actual))
        if len(common) < 2:
            continue
        prior, last = common[:-1], common[-1]
        x = sum(jd_feat[j] for j in prior) / len(prior)
        pairs.append((x, jd_actual[last]))
    n = len(pairs)
    if n < min_pairs:
        return None

    feature_err, baseline_err = [], []
    for i in range(n):
        train = pairs[:i] + pairs[i + 1:]
        train_ys = [t[1] for t in train]
        slope, intercept = _linreg([t[0] for t in train], train_ys)
        x_i, y_i = pairs[i]
        feature_err.append(abs((slope * x_i + intercept) - y_i))
        baseline_err.append(abs(_statistics.mean(train_ys) - y_i))
    return {"n": n, "mae_feature": sum(feature_err) / n,
           "mae_baseline": sum(baseline_err) / n,
           "gap": bootstrap_gap(feature_err, baseline_err)}


def walk_forward_compare(jornadas: list[int], fit_new, predict_new,
                         predict_old, actual, min_history: int = 1
                         ) -> dict | None:
    """Does a newly-fitted approach beat the previous one, walk-forward:
    at every jornada, fit using only jornadas before it, predict that one
    jornada, never look ahead. General tool — wire in a new candidate by
    writing four small callables, not a new jornada-by-jornada loop.

    `jornadas`: every jornada with a real graded outcome, in order.
    `fit_new(cutoff)`: fit params using only data strictly before cutoff
        (enforcing that is the caller's job).
    `predict_new(params, jornada)` / `predict_old(jornada)`: {key: value}
        under the new/previous approach. `predict_old` takes no fit call
        since "old" doesn't change.
    `actual(jornada)`: {key: real outcome}.

    Only keys common to all three dicts for a jornada are scored.
    Jornadas before `min_history` are skipped.

    Returns {"per_jornada", "n", "mae_old", "mae_new", "gap"} (gap is
    stats.bootstrap_gap()). Does NOT decide keep/revert — reports the
    numbers, same discipline as backtest_predictor()'s `beats`. None if
    no jornada produced a scoreable overlap.
    Why: docs/notes/score.md#walk_forward_compare--the-general-tool
    """
    from stats import bootstrap_gap

    per_jornada = []
    all_old_err, all_new_err = [], []
    for i, j in enumerate(jornadas):
        if i < min_history:
            continue
        params = fit_new(j)
        preds_new = predict_new(params, j)
        preds_old = predict_old(j)
        acts = actual(j)
        common = set(preds_new) & set(preds_old) & set(acts)
        if not common:
            continue
        old_err = [abs(preds_old[k] - acts[k]) for k in common]
        new_err = [abs(preds_new[k] - acts[k]) for k in common]
        all_old_err += old_err
        all_new_err += new_err
        per_jornada.append({"jornada": j, "n": len(common),
                            "mae_old": sum(old_err) / len(old_err),
                            "mae_new": sum(new_err) / len(new_err)})
    if not all_old_err:
        return None
    return {"per_jornada": per_jornada, "n": len(all_old_err),
           "mae_old": sum(all_old_err) / len(all_old_err),
           "mae_new": sum(all_new_err) / len(all_new_err),
           "gap": bootstrap_gap(all_new_err, all_old_err)}


EXPERIMENT_LOG = "experiment_log.csv"


def log_experiment(feature: str, position: str, result: dict | None,
                   verdict: str, notes: str = "") -> None:
    """Append one row per deliberate hypothesis test — distinct from
    methodology.log_forecast_accuracy() (one row per run, the live
    forecast's own trend). A negative result is logged too, so a later
    session doesn't re-run a test that already has an answer.

    `result` is backtest_predictor()'s own dict, or None when nothing
    cleared min_pairs — logged as empty MAE fields, not skipped.
    """
    from ffcore.tidy import DECISIONS, append_csv, run_now

    DECISIONS.mkdir(parents=True, exist_ok=True)
    row = {"observed_at": run_now().strftime("%Y-%m-%dT%H%MZ"),
          "feature": feature, "position": position,
          "n": result["n"] if result else "",
          "mae_feature": "%.4f" % result["mae_feature"] if result else "",
          "mae_baseline": "%.4f" % result["mae_baseline"] if result else "",
          "beats": (result["gap"]["beats"]
                   if result and result.get("gap") else ""),
          "verdict": verdict, "notes": notes}
    append_csv(DECISIONS / EXPERIMENT_LOG, [row],
              ["observed_at", "feature", "position", "n", "mae_feature",
               "mae_baseline", "beats", "verdict", "notes"])


def experiment_history() -> list[dict]:
    """Every logged experiment, oldest first — read log_experiment()'s
    own file back, for a caller (or a future session) that wants to check
    "has this already been tried" before re-running it."""
    from ffcore.tidy import DECISIONS, read_csv

    path = DECISIONS / EXPERIMENT_LOG
    return read_csv(path) if path.exists() else []


def _shots_points_fit(xw, players=None) -> tuple[float, float, int]:
    """(slope, intercept, n) — this season's points in a jornada as a
    linear function of a forward's own shot volume (total_scoring_att/90)
    in every jornada before it — forwards only.

    IN-SEASON, NOT PRIOR-SEASON (unlike _xg_points_fit): api_stats.csv has
    no earlier season to fit a units conversion against, so this fits on
    each forward's own history instead.

    VALIDATED WITH backtest_predictor() (leave-one-player-out against the
    pooled sample mean): MAE 2.99 vs 3.76, n=25, bootstrap CI on the gap
    straddles zero — real and promising, not yet statistically proven.
    Kept live above the 10-pair floor on a structural argument (shots are
    the more stable underlying skill for a goal-driven position) plus a
    directionally consistent reading. Re-check with backtest_predictor()
    as more jornadas accumulate.

    `players`, when given, is load_players()'s own output — injectable so
    a caller can fake a small fixture; `None` loads it for real.
    Why: docs/notes/score.md#_shots_points_fit--in-season-not-prior-season
    """
    from ffcore.tidy import TIDY, SEASON, read_csv, load_players

    if xw is None:
        return 0.0, 0.0, 0
    live = SEASON / "live"
    files = sorted(live.glob("perjornada_*.csv")) if live.exists() else []
    if not files:
        return 0.0, 0.0, 0
    by_key = _per_jornada_current(
        read_csv(TIDY / "starters.csv"), read_csv(files[-1]),
        read_csv(TIDY / "matches.csv"), xw)
    shots_by_key = _shots_by_jornada(xw)
    players = players if players is not None else load_players()

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
    """{norm(market name): {"shots90": shots per 90 minutes THIS season,
    "minutes": total minutes}} — forwards only, from LaLiga's per-match
    box score. Same shape as load_understat_current(). `players`: see
    _shots_points_fit()'s note — injectable, `None` loads for real.
    """
    from ffcore.tidy import (TIDY, SEASON, read_csv, load_crosswalk,
                             load_players)

    xw = xw if xw is not None else load_crosswalk()
    if xw is None:
        return {}
    players = players if players is not None else load_players()
    shots_by_key = _shots_by_jornada(xw)
    live = SEASON / "live"
    files = sorted(live.glob("perjornada_*.csv")) if live.exists() else []
    minutes_by_key: dict[str, dict[int, float]] = {}
    if files:
        by_key = _per_jornada_current(
            read_csv(TIDY / "starters.csv"), read_csv(files[-1]),
            read_csv(TIDY / "matches.csv"), xw)
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


def _per_jornada_current(starters_rows, perjornada_rows, matches_rows,
                         xw) -> dict[str, dict[int, tuple[float, float]]]:
    """{crosswalk key: {jornada: (points, minutes)}} for the live season.

    Joins starters.csv's minutes (keyed by match_id) to perjornada.csv's
    points (keyed by `jornada`) via matches.csv's match_id -> jornada
    map. A jornada absent from either side is dropped, not guessed.
    Why: docs/notes/score.md#_per_jornada_current--the-join-and-the-points_total-anchor
    """
    jornada_of_match: dict[str, int] = {}
    for r in matches_rows:
        mid = (r.get("match_id") or "").strip()
        if mid and mid not in jornada_of_match:
            try:
                jornada_of_match[mid] = int(r.get("jornada"))
            except (TypeError, ValueError):
                continue

    minutes_by_jor: dict[str, dict[int, float]] = {}
    seen: set[tuple[str, str]] = set()
    for r in starters_rows:
        slug = (r.get("player_slug") or "").strip()
        mid = (r.get("match_id") or "").strip()
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

    # Anchored on points_total, not summed from points_delta — the delta
    # is missing whatever a player had before this file's own history
    # started. Why: docs/notes/score.md#_per_jornada_current--the-join-and-the-points_total-anchor
    end_total: dict[str, dict[int, float]] = {}
    for r in perjornada_rows:
        raw_jor = (r.get("jornada") or "").strip()
        if not raw_jor:
            continue
        jor = int(raw_jor)
        pid = (r.get("ff_id") or "").strip()
        key = pid if pid in xw.players else xw.player(
            name=r.get("player_name_full") or r.get("player_name"))
        if not key:
            continue
        total = ratio(r.get("points_total"))
        if total is None:
            continue
        # LAST WRITE FOR THE JORNADA WINS, by observation order in the
        # file (points.py writes rows chronologically) — a correction row
        # for a jornada already seen must overwrite its running total,
        # not add to it twice.
        end_total.setdefault(key, {})[jor] = total

    points_by_jor: dict[str, dict[int, float]] = {}
    for key, totals in end_total.items():
        prev = 0.0
        for jor in sorted(totals):
            points_by_jor.setdefault(key, {})[jor] = totals[jor] - prev
            prev = totals[jor]

    # The universe is the points-page's own — a player with real minutes
    # but no points-page row is left out entirely, not entered at pts=0.
    # Why: docs/notes/score.md#_per_jornada_current--the-join-and-the-points_total-anchor
    out: dict[str, dict[int, tuple[float, float]]] = {}
    for key, points_jd in points_by_jor.items():
        minutes_jd = minutes_by_jor.get(key, {})
        jors = set(points_jd) | set(minutes_jd)
        out[key] = {j: (points_jd.get(j, 0.0), minutes_jd.get(j, 0.0))
                   for j in jors}
    return out


def _weighted_totals(per_jornada: dict[int, tuple[float, float]],
                     decay: float) -> tuple[float, float]:
    """(weighted points, weighted matches) for one player. Most recent
    jornada weighs 1, one back `decay`, two back `decay**2`; decay=1.0 is
    an exact flat sum.
    Why: docs/notes/score.md#_weighted_totals--_weighted_start-recency-weighting
    """
    if not per_jornada:
        return 0.0, 0.0
    latest = max(per_jornada)
    wpts = wmatch = 0.0
    for j, (pts, mins) in per_jornada.items():
        w = decay ** (latest - j)
        wpts += pts * w
        wmatch += (mins / 90.0) * w
    return wpts, wmatch


def _weighted_start(per_jornada: dict[int, tuple[float, float]],
                    decay: float) -> tuple[float, float]:
    """(recency-weighted participation rate, weighted jornada count) —
    Σ(w·min(1, minutes/90)) / Σw, in [0, 1], a start probability directly.
    Why: docs/notes/score.md#_weighted_totals--_weighted_start-recency-weighting
    """
    if not per_jornada:
        return 0.0, 0.0
    latest = max(per_jornada)
    wsum = wn = 0.0
    for j, (_pts, mins) in per_jornada.items():
        w = decay ** (latest - j)
        wsum += w * min(1.0, mins / 90.0)
        wn += w
    return (wsum / wn if wn else 0.0), wn


def _fit_decay(by_key: dict[str, dict[int, tuple[float, float]]]) -> tuple[float, str]:
    """(decay, why) — the recency weighting earns its use only if it beats
    the flat average out of sample. Walk-forward (not leave-one-out:
    jornadas have a time-order that matters) — jornada J is only ever
    predicted from jornadas strictly before it.
    Why: docs/notes/score.md#_fit_decay--walk-forward-validation
    """
    def walk_error(decay: float) -> tuple[float, int]:
        se, n = 0.0, 0
        for jd in by_key.values():
            jors = sorted(jd)
            for i in range(1, len(jors)):
                target = jors[i]
                actual_pts, actual_min = jd[target]
                if actual_min <= 0:
                    continue                       # did not feature — no claim
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
    """{norm(market name): {"pts": season-to-date points, "pj": minutes/90,
    "start_rate": recency-weighted share of a jornada started,
    "start_n": weighted jornada count behind that rate}} from this
    season's per-jornada tracker, or ({}, "") before it exists.

    Not points_<season>.csv (that snapshots the points PAGE, empty until
    J1 is fully played). Keyed through the crosswalk to norm(market
    name), the key Scorer.rate() uses. "pj" is minutes, not appearances.
    Recency-weighted via `_fit_decay`'s walk-forward validation.
    Why: docs/notes/score.md#_current_from_perjornada--why-not-points_csv
    """
    from ffcore.tidy import SEASON, TIDY, load_crosswalk, read_csv

    live = SEASON / "live"
    files = sorted(live.glob("perjornada_*.csv")) if live.exists() else []
    if not files:
        return {}, ""
    label = files[-1].stem.replace("perjornada_", "")
    xw = load_crosswalk()
    if xw is None:
        return {}, ""

    by_key = _per_jornada_current(
        read_csv(TIDY / "starters.csv"), read_csv(files[-1]),
        read_csv(TIDY / "matches.csv"), xw)
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
    """(prior, prior_label, current, current_label) from data/season/.

    PRIOR: the newest points_*.csv (last season's completed totals).
    CURRENT: this season's live per-jornada tracker. Reading only the
    single newest points_*.csv would lose the actual prior the moment a
    new season's own snapshot appears.
    Why: docs/notes/score.md#load_points--the-two-file-prior
    """
    from ffcore.tidy import SEASON, load_crosswalk, read_csv

    files = sorted(SEASON.glob("points_*.csv")) if SEASON.exists() else []
    xw = load_crosswalk()

    def read(path) -> dict:
        out: dict[str, dict] = {}
        for r in read_csv(path):
            rec = {"pts": ratio(r.get("points")) or 0.0,
                   "pj": ratio(r.get("games")) or 0.0}
            # The id first, under the market's current name; falls back to
            # name-only for a file predating ff_id, rather than losing rows.
            pid = (r.get("ff_id") or "").strip()
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
    """(Scorer, labels) wired to every input the model has — the one
    builder report.py and rivals.py both use, so they score identically.
    `calibrate` fits P(start) against confirmed line-ups, or turns itself
    off with nothing played or a fit that loses on unseen line-ups.
    Why: docs/notes/score.md#build--one-model-per-run
    """
    from ffcore.fixture import fixture_board
    from ffcore.tidy import load_elo, load_fixtures

    prior, prior_label, cur, cur_label = load_points()
    cal, second = None, None
    if calibrate:
        cal, second = _calibrated()
    # Club Elo ranks opponents when it covers all of them, squad value
    # otherwise — wired here so your squad and a rival's never score off
    # two different difficulty scales.
    from ffcore.tidy import (load_crosswalk, load_results_history,
                             load_understat_players)
    xw = load_crosswalk()
    board = fixture_board(market, load_fixtures(), now, load_elo(),
                          xw=xw, results=load_results_history(),
                          understat_rows=load_understat_players("2025"))
    xg_cur = load_understat_current(xw)
    xg_slope, xg_intercept, xg_n = _xg_points_fit(xw)
    xg_boost, xg_why = _xg_stickiness_boost()
    # Shot volume, forwards only — see Scorer.__init__ and
    # _shots_points_fit() for the validated case.
    shots_cur = load_shots_current(xw)
    shots_slope, shots_intercept, shots_n = _shots_points_fit(xw)
    sc = Scorer(market, xi_rows, prior, shrink_k=shrink_k,
                current=cur, board=board, cal=cal, second=second,
                xg=xg_cur, xg_slope=xg_slope, xg_intercept=xg_intercept,
                xg_n=xg_n, xg_boost=xg_boost, xg_why=xg_why,
                shots=shots_cur, shots_slope=shots_slope,
                shots_intercept=shots_intercept, shots_n=shots_n)
    return sc, (prior_label, cur_label)


_CAL_CACHE: list = []


def _calibrated():
    """(Calibration, second-source rows), fitted once per process and
    cached. `cut` is the first confirmed line-up seen — anything
    published after may already be the team sheet, and grading a
    forecast against itself is marking its own homework.
    Why: docs/notes/score.md#_calibrated--caching-and-the-fingerprint-bug
    """
    if _CAL_CACHE:
        return _CAL_CACHE[0]
    import json
    from ffcore.crosswalk import Crosswalk
    from ffcore.startprob import (Calibration, METHOD_VERSION, observations,
                                  fit_start_fallbacks)
    from ffcore.tidy import load_lineups, read_csv, TIDY
    from ffcore.second import SECOND_SOURCE

    second = load_lineups(SECOND_SOURCE)
    truth = read_csv(TIDY / "starters.csv")
    cut = min((r.get("observed_at", "") for r in truth), default="")
    # The crosswalk is what lets the narrow source be joined exactly rather
    # than on a folded name: it shares no slug with anything else.
    xw = Crosswalk.read(TIDY / "players.csv", TIDY / "clubs.csv")
    # Mutates the module globals — Scorer.score() reads NEUTRAL_START/
    # ABSENT_START live off this module — before any Scorer built this
    # run calls .score(). Unconditional (not gated behind the cache check
    # below): cheap, and independent of the Calibration fit.
    global NEUTRAL_START, ABSENT_START
    if cut:
        NEUTRAL_START, ABSENT_START, _fallback_why = fit_start_fallbacks(
            load_lineups() + second, truth, cut,
            neutral_default=NEUTRAL_START, absent_default=ABSENT_START,
            xw=xw)
    # On disk, keyed by what it was fitted on — the fit costs ~6s; a
    # changed fingerprint (METHOD_VERSION included) refits.
    # Why: docs/notes/score.md#_calibrated--caching-and-the-fingerprint-bug
    stamp = "%d:%d:%s" % (METHOD_VERSION, len(truth), cut)
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
        cal = Calibration.fit(observations(
            load_lineups() + second, truth, cut, neutral=NEUTRAL_START,
            absent=ABSENT_START, xw=xw))
        try:
            path.write_text(json.dumps({
                "fingerprint": stamp, "alpha": cal.alpha, "beta": cal.beta,
                "weight": cal.weight, "titular": cal.titular, "n": cal.n,
                "fitted": cal.fitted, "gain": cal.gain, "why": cal.why,
                "groups": cal.groups}) + "\n", encoding="utf-8")
        except OSError:
            pass
    _CAL_CACHE.append((cal, second))
    return _CAL_CACHE[0]


def formations() -> list[tuple]:
    """Legal shapes — the free tier, confirmed against the app's picker."""
    return list(FREE_FORMATIONS)


class Rating(NamedTuple):
    ppm: float          # shrunk points per match
    why: str            # "412p/34j" or "assumed"
    assumed: bool       # no top-flight record — treat with suspicion
    cur_pj: float = 0.0  # matches of THIS season inside ppm, 0 = none yet
    # HOW MUCH EVIDENCE IS UNDER THE RATE, in matches, prior and current
    # together. A rate off 34 matches and a rate off 4 are not the same claim,
    # and until this was carried the simulation treated them as if they were —
    # every player's rate entered the season as a fact. ffcore.forecast turns
    # it into the width of that rate's own uncertainty.
    pj: float = 0.0


class Scored(NamedTuple):
    name: str
    key: str
    slot: str
    pos: str
    score: float           # includes the fixture — for FIELDING this round
    flat: float            # ignores the fixture — for BUYING, which is months
    ppm: float
    pct: float | None       # as published, None if unknown
    pct_used: float         # what the score actually used, THIS jornada
    # HIS STANDING RATE, for every jornada AFTER this one — see score()'s
    # own note on why pct_used cannot answer for the rest of the season.
    pct_rest: float
    on_page: bool
    status: str
    assumed: bool
    value: float
    fix: float = 1.0        # fixture factor, 1.0 = neutral or unknown
    opp: str = ""           # who he faces next, "" if no fixture is known
    home: bool = True
    cur_pj: float = 0.0     # matches of this season behind ppm
    pj: float = 0.0         # every match behind ppm, prior season included
    # What ranked the opponent — "elo", "value" or "none". Logged rather than
    # printed: the fixture band is a guess, and re-fitting it later means
    # knowing which scale each row's factor came off.
    fix_basis: str = "none"
    elo_gap: float | None = None   # raw Elo difference, you minus opponent

    def as_row(self) -> dict:
        """pick_xi and the report renderers work in plain dicts."""
        return dict(self._asdict())


class Scorer:
    """Build once per run, then score any player from any squad.

        sc = Scorer(market_rows, xi_rows, history)
        rec = sc.score(market_row)

    `history` is {normalised name: {"pts": float, "pj": float}} — what
    report.load_history() already produces.
    """

    def __init__(self, market: list[dict], xi: list[dict],
                 history: dict | None = None, shrink_k: float = SHRINK_K,
                 current: dict | None = None, board: dict | None = None, cal=None, second=None,
                 xg: dict | None = None, xg_slope: float = 0.0,
                 xg_intercept: float = 0.0, xg_n: int = 0,
                 xg_boost: float = 1.0, xg_why: str = "",
                 shots: dict | None = None, shots_slope: float = 0.0,
                 shots_intercept: float = 0.0, shots_n: int = 0):
        self.market = market
        self.history = history or {}
        self.shrink_k = shrink_k
        # This season so far, same shape as `history`. Empty until a jornada
        # has been played, which is the state it must handle gracefully.
        self.current = current or {}
        # {team: ffcore.fixture.Match}. A team absent from it has no known
        # next fixture, and gets factor 1.0 with the reason printed — never a
        # silently average opponent.
        self.board = board or {}
        # xG/xA — see _precision_blend above. {key: {"xg90", "minutes"}},
        # forwards/attacking mids only.
        self.xg = xg or {}
        self.xg_slope = xg_slope
        self.xg_intercept = xg_intercept
        self.xg_n = xg_n            # how many (player, xG, ppm) pairs fit the slope
        self.xg_boost = xg_boost    # pseudo-matches an xG match is worth vs a raw one
        self.xg_why = xg_why        # printed by callers that want the provenance
        # Shot volume, forwards only — see _shots_points_fit(). No
        # xg_boost equivalent: no prior season of this feed exists to
        # calibrate one against, so 1 raw match = 1 informed match.
        self.shots = shots or {}
        self.shots_slope = shots_slope
        self.shots_intercept = shots_intercept
        self.shots_n = shots_n

        # Same key the market index uses, not norm(name) alone (that held
        # one row for the two Álvaro Garcías). See docs/notes/score.md#scorerinit--key-joins
        from ffcore.tidy import row_key, shared_names, load_crosswalk

        shared = shared_names(market)
        self.lookup: dict[str, dict] = {}
        # name -> the market keys answering to it, for a probable-XI feed
        # with no slug — only when the name names one man.
        self._name_keys: dict[str, list] = {}
        for r in market:
            if r.get("name"):
                k = row_key(r, shared)
                self.lookup[k] = r
                # Distinct keys: `market` can be every snapshot ever, so
                # appending blindly broke the "one man" test below.
                seen_for = self._name_keys.setdefault(norm(r.get("name")), [])
                if k not in seen_for:
                    seen_for.append(k)
        # ff_slug -> market key (the team pages DO publish player links,
        # /jugadores/<slug> — 497/512 XI rows reach a player by slug, none
        # ambiguous). Why: docs/notes/score.md#scorerinit--key-joins
        xw = load_crosswalk()
        self._by_ff_slug = {norm(p.ff_slug): p.player_id
                            for p in (xw.players.values() if xw else ())
                            if p.ff_slug}

        self.cal = cal or Calibration()
        self.second: dict[str, dict] = {}
        for r in second or []:
            # By identifier, like everything else — see
            # docs/notes/score.md#scorerinit--key-joins.
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
            # The slug first: it is an identifier, the name is not.
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

    # -- calibration ---------------------------------------------------

    def _detect_promoted(self) -> set[str]:
        """A team with a full squad and essentially no top-flight record."""
        per_team: dict[str, list[int]] = {}
        for r in self.market:
            team = r.get("team") or "?"
            h = self.history.get(norm(r.get("name", "")))
            tally = per_team.setdefault(team, [0, 0])
            tally[0] += 1
            tally[1] += 1 if h and h["pj"] > 0 else 0
        return {t for t, (n, k) in per_team.items()
                if n >= 10 and k / n < 0.15}

    def _priors(self):
        samples: dict[str, list[float]] = {}
        for r in self.market:
            h = self.history.get(norm(r.get("name", "")))
            slot = SLOT.get((r.get("position") or "").lower())
            if h and slot and h["pj"] >= 10:
                samples.setdefault(slot, []).append(h["pts"] / h["pj"])
        priors = {k: statistics.median(v) for k, v in samples.items() if v}
        flat = [p for v in samples.values() for p in v]
        return priors, (statistics.median(flat) if flat else 0.0)

    # -- scoring -------------------------------------------------------

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
            base, why, assumed = prior * PROMOTED_DISCOUNT, "assumed", True
        else:
            base, why, assumed = prior, "assumed", True

        # Second stage: this season blended against last season's figure,
        # same K, generalised to a THIRD source when one exists — see this
        # module's own section above. `terms` is [(pseudo-matches, rate)];
        # the original two-term shrink formula is the special case with no
        # xG term, exactly reproduced below when self.xg has nothing for
        # this player. With no matches played and no xG reading this is a
        # no-op — an empty points page must not reset anyone to the prior.
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
        # Shot volume, forwards only. Gated on shots_n >= 10 explicitly
        # (unlike the xG term) — an untrained fit would otherwise add a
        # real-weight, zero-rate term and drag a thin-evidence player
        # toward zero.
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
        """Market row for a player name or slug, or None."""
        return self.lookup.get(name) or self.lookup.get(norm(name))

    def score(self, rec: dict) -> Scored:
        from ffcore.tidy import row_key
        # The row's own key, so fitness and start probability are looked up
        # by the same identifier everything else uses. This was norm(name),
        # which meant two men of one name shared a fitness reading.
        key = row_key(rec, ()) or norm(rec.get("name", ""))
        st = self.status.get(key, "")
        pct = self.start_pct.get(key)
        on_page = key in self.listed
        rating = self.rate(rec)

        # Scaling by P(start) prices a non-start at zero — the free tier
        # has no auto-substitution. `pct_used` is graded against confirmed
        # line-ups (identity until a jornada is played).
        # Why: docs/notes/score.md#scorerscore--the-pstart-blend
        raw = pct if pct is not None else (
            NEUTRAL_START if on_page else ABSENT_START)
        pct_used = 100.0 * self.cal.p(raw, self.second.get(key))
        # Blended against real recent minutes (self.current's start_rate/
        # start_n) once a player has actually featured — editorial P(start)
        # stays the whole answer until then. Keyed by norm(name), same as
        # rate()'s own lookup, not the row_key `key` above.
        cur = self.current.get(norm(rec.get("name", "")))
        start_n = cur.get("start_n", 0.0) if cur else 0.0
        # pct_used answers for the NEXT jornada only; pct_rest is jornadas
        # AFTER that, shrunk toward NEUTRAL_START rather than toward this
        # week's status-tainted reading — a one-match suspension must not
        # read as "unlikely to start" for the other 37 too.
        # Why: docs/notes/score.md#scorerscore--the-pstart-blend
        if start_n > 0.0:
            k_s = self.shrink_k
            pct_rest = (k_s * NEUTRAL_START + start_n * 100.0
                       * cur["start_rate"]) / (k_s + start_n)
            pct_used = (k_s * pct_used + start_n * 100.0 * cur["start_rate"]
                       ) / (k_s + start_n)
        else:
            pct_rest = pct_used
        m = self.board.get((rec.get("team") or "").strip())
        slot = SLOT.get((rec.get("position") or "").lower(), "")
        # A clean sheet is opponent-attack-driven, a goal opponent-defense-
        # driven — why Match carries two factors. An unrecognised slot
        # gets the attacking number rather than crashing.
        fix_factor = (m.def_factor if slot in ("POR", "DEF")
                     else m.atk_factor) if m else 1.0
        flat = rating.ppm * pct_used / 100.0
        score = flat * fix_factor
        if st in OUT_STATUSES:
            score = flat = 0.0
        elif st == "doubt":
            score *= DOUBT_FACTOR
            flat *= DOUBT_FACTOR

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
        """Score a list of player names. Returns (scored, unresolved).

        Unresolved names are handed back rather than dropped: for a rival
        squad assembled by replaying the ledger, the count of names that
        didn't match is the honest error bar on their total.
        """
        out, missing = [], []
        for n in names:
            r = self.row_for(n)
            if r is None:
                missing.append(n)
            else:
                out.append(self.score(r))
        return out, missing


def squad_pool(scored) -> dict[str, list[dict]]:
    """Group scored players by slot, best first — the input to pick_xi."""
    pool: dict[str, list[dict]] = {}
    for p in scored:
        row = p.as_row() if isinstance(p, Scored) else p
        if row.get("slot"):
            pool.setdefault(row["slot"], []).append(row)
    for v in pool.values():
        v.sort(key=lambda p: p["score"], reverse=True)
    return pool


# ---------------------------------------------------------------------------
# replacement level — a fixed baseline (value-based drafting's answer),
# not "what YOUR eleven loses without him" (goes stale the moment you act).
# Why: docs/notes/score.md#replacement-level--why-not-λ
# ---------------------------------------------------------------------------



def pick_xi(pool: dict, force: dict | None = None):
    """Best legal XI by total score, or None if no legal shape fits.

    force pins one player into his slot. Exact, not heuristic: the only
    coupling between players is the per-slot count, so top-N per slot within
    each legal shape is optimal.

    Returns (total, (d, m, f), picked). A None return for a rival squad is
    itself a finding — it means they cannot field a legal XI today.
    """
    best = None
    for d, m, f in formations():
        need = {"POR": 1, "DEF": d, "MED": m, "DEL": f}
        if force is not None:
            slot = force["slot"]
            if not slot or need.get(slot, 0) < 1:
                continue
        picked, ok = [], True
        for k, n in need.items():
            avail = pool.get(k, [])
            if force is not None and force["slot"] == k:
                rest = [p for p in avail if p is not force][:n - 1]
                take = [force] + rest
            else:
                take = avail[:n]
            if len(take) < n:
                ok = False
                break
            picked += take
        if not ok:
            continue
        tot = sum(p["score"] for p in picked)
        if best is None or tot > best[0]:
            best = (tot, (d, m, f), picked)
    return best


def _selftest() -> None:
    """The two shrink stages, and the fixture factor that only fielding
    uses."""
    from ffcore.fixture import Match

    def mk(name, pos="defensa", team="Mid", value="10.00M"):
        return {"name": name, "position": pos, "team": team, "value": value}

    # Ten priced defenders with a full record each, so the positional prior is
    # a real median rather than one player's average.
    market = [mk("p%d" % i) for i in range(10)] + [mk("Sub"), mk("Newbie")]
    hist = {"p%d" % i: {"pts": 100.0 + i, "pj": 34.0} for i in range(10)}
    hist["sub"] = {"pts": 20.0, "pj": 4.0}          # thin record: shrunk hard
    xi = [{"player_name": n, "start_pct": "100"}
          for n in [r["name"] for r in market]]

    sc = Scorer(market, xi, hist)
    prior = sc.priors["DEF"]
    assert 3.0 < prior < 3.1, prior                  # median of 100..109 / 34

    # STAGE ONE, unchanged: a thin record is pulled toward the prior, and a
    # player with no record at all IS the prior, flagged assumed.
    thin = sc.rate(mk("Sub"))
    assert abs(thin.ppm - (20.0 + 8 * prior) / (4.0 + 8)) < 1e-9
    assert not thin.assumed and thin.cur_pj == 0.0
    assert sc.rate(mk("Newbie")).assumed

    # STAGE TWO: this season shrunk toward last season's shrunk figure.
    full = sc.rate(mk("p0"))
    cur = {"p0": {"pts": 30.0, "pj": 3.0}}
    sc2 = Scorer(market, xi, hist, current=cur)
    blended = sc2.rate(mk("p0"))
    assert abs(blended.ppm - (30.0 + 8 * full.ppm) / (3.0 + 8)) < 1e-9
    assert blended.cur_pj == 3.0
    # 10 points a match beats his 3-ish, so the rating rises — but only part
    # of the way, because three matches is not a season.
    assert full.ppm < blended.ppm < 10.0
    assert "now" in blended.why and "3j" in blended.why

    # An empty current season is a no-op.
    assert Scorer(market, xi, hist, current={}).rate(mk("p0")) == full
    assert Scorer(market, xi, hist,
                  current={"p0": {"pts": 0.0, "pj": 0.0}}).rate(mk("p0")) \
        == full

    # The fixture splits the two decisions: fielding moves with the
    # opponent, buying does not.
    when = __import__("datetime").datetime.fromisoformat(
        "2026-08-20T19:00:00+00:00")
    # p0 is a defensa (mk()'s default) -> def_factor, not atk_factor.
    easy = Match("Elche", True, when, atk_factor=1.30, def_factor=1.10,
                rank=20, of=20)
    sc3 = Scorer(market, xi, hist, board={"Mid": easy})
    s = sc3.score(mk("p0"))
    assert abs(s.flat - full.ppm) < 1e-9              # P(start) is 100%
    assert abs(s.score - full.ppm * 1.10) < 1e-9
    assert s.opp == "Elche" and s.home and s.fix == 1.10
    # The SAME fixture, a delantero instead: atk_factor, not def_factor.
    fwd = sc3.score(mk("p0", pos="delantero"))
    assert abs(fwd.score - full.ppm * 1.30) < 1e-9, fwd
    assert fwd.fix == 1.30
    # No fixture for his team: neutral, and the report can see it is unknown.
    solo = Scorer(market, xi, hist, board={}).score(mk("p0"))
    assert solo.fix == 1.0 and solo.opp == "" and solo.score == solo.flat

    # An absence is an absence in BOTH numbers — a suspended player is not a
    # cheap fielding risk on a kind fixture.
    out = [{"player_name": "p0", "start_pct": "100", "status": "suspended"}]
    zero = Scorer(market, out, hist, board={"Mid": easy}).score(mk("p0"))
    assert zero.score == 0.0 and zero.flat == 0.0
    # A doubt halves both.
    dbt = [{"player_name": "p0", "start_pct": "100", "status": "doubt"}]
    half = Scorer(market, dbt, hist, board={"Mid": easy}).score(mk("p0"))
    assert abs(half.flat - full.ppm * DOUBT_FACTOR) < 1e-9
    assert abs(half.score - full.ppm * 1.10 * DOUBT_FACTOR) < 1e-9

    # pick_xi still ranks on `score`, so the fixture reaches the eleven it is
    # meant to reach, and as_row() carries the new fields to the renderers.
    assert "fix" in s.as_row() and "flat" in s.as_row()

    # -- P(start) blended against real recent minutes -----------------
    # Editorial says 100%; he has started nothing lately. pct_used must
    # pull down from 100, not stay the whole answer.
    benched_cur = {"p0": {"pts": 30.0, "pj": 3.0,
                          "start_rate": 0.0, "start_n": 6.0}}
    sc4 = Scorer(market, xi, hist, current=benched_cur, board={"Mid": easy})
    benched_s = sc4.score(mk("p0"))
    assert benched_s.pct_used < 100.0, benched_s.pct_used
    # Shrunk, not overwritten: (8*100 + 6*0) / (8+6).
    assert abs(benched_s.pct_used - 800.0 / 14.0) < 1e-9, benched_s.pct_used

    # No current-season evidence is a no-op — editorial 100% stays 100%.
    untouched = Scorer(market, xi, hist, current={}, board={"Mid": easy}
                       ).score(mk("p0"))
    assert untouched.pct_used == 100.0, untouched.pct_used
    assert untouched.pct_rest == 100.0, untouched.pct_rest

    # -- pct_rest: a regular starter's standing rate survives one bad
    # week's editorial reading (a suspension), while pct_used does not —
    # "out this week" should not read as "a rotation risk all season."
    starter_cur = {"p0": {"pts": 30.0, "pj": 2.0,
                          "start_rate": 0.9, "start_n": 2.0}}
    susp = [{"player_name": "p0", "start_pct": "0", "status": "suspended"}]
    sc5 = Scorer(market, susp, hist, current=starter_cur, board={"Mid": easy})
    susp_s = sc5.score(mk("p0"))
    # pct_used anchored on this week's editorial 0%, pct_rest on
    # NEUTRAL_START — only NEUTRAL_START's anchor never sees the suspension.
    assert abs(susp_s.pct_used - (8 * 0.0 + 2 * 90.0) / 10) < 1e-9, susp_s
    assert abs(susp_s.pct_rest - (8 * NEUTRAL_START + 2 * 90.0) / 10) < 1e-9, \
        susp_s
    assert susp_s.pct_rest > susp_s.pct_used + 25.0, susp_s

    # -- _per_jornada_current: minutes weighted, corrections folded in, and
    # -- Step 1's recency weighting gated on real out-of-sample evidence --
    from ffcore.crosswalk import Crosswalk, Player

    xw2 = Crosswalk({
        "antonio blanco": Player("antonio blanco", "Antonio Blanco",
                                 ff_slug="blanco", app_id="1"),
        "came on": Player("came on", "Came On", ff_slug="came-on"),
        "unused sub": Player("unused sub", "Unused Sub", ff_slug="unused"),
    }, {})
    matches_rows = [
        {"match_id": "m1", "jornada": "1"},
        {"match_id": "m2", "jornada": "2"},
        # A second sighting of the same match must not change its jornada.
        {"match_id": "m1", "jornada": "1"},
    ]
    starters_rows = [
        # starters.csv's short form ("Blanco") must resolve to the
        # market's full-name crosswalk key.
        {"player_name": "Blanco", "player_slug": "blanco",
         "role": "starter", "minute": "", "match_id": "m1"},
        # Came on at 70: the REMAINING 20, not a full match.
        {"player_name": "Came On", "player_slug": "came-on",
         "role": "sub", "minute": "70", "match_id": "m1"},
        # Unused substitute: zero, not a guess.
        {"player_name": "Unused Sub", "player_slug": "unused",
         "role": "sub", "minute": "", "match_id": "m1"},
        # A SECOND match, a SEPARATE jornada — minutes land in their own
        # bucket rather than accumulating into one season total.
        {"player_name": "Blanco", "player_slug": "blanco",
         "role": "starter", "minute": "45", "match_id": "m2"},
        # A role that is neither "starter" nor "sub" contributes nothing.
        {"player_name": "Blanco", "player_slug": "blanco",
         "role": "coach", "minute": "", "match_id": "m3"},
        # No slug at all does not resolve — skipped, not guessed.
        {"player_name": "Nobody", "player_slug": "", "role": "starter",
         "minute": "", "match_id": "m1"},
        # The same match, repeated 56 more times (starters.csv's raw
        # table really does this) — must count once, not 57 times.
        *({"player_name": "Blanco", "player_slug": "blanco",
           "role": "starter", "minute": "", "match_id": "m1"}
          for _ in range(56)),
    ]
    perjornada_rows = [
        # Anchoring on points_total (not summing points_delta) recovers
        # the true 8 here despite a delta that only claims 3.
        {"ff_id": "1", "player_name_full": "Antonio Blanco",
         "points_delta": "3", "points_total": "8", "jornada": "1"},
        {"ff_id": "1", "player_name_full": "Antonio Blanco",
         "points_delta": "5", "points_total": "13", "jornada": "2"},
        # No jornada at all (nothing had finished yet when observed) is
        # dropped, not guessed into either bucket.
        {"ff_id": "1", "player_name_full": "Antonio Blanco",
         "points_delta": "99", "points_total": "112", "jornada": ""},
    ]
    by_key = _per_jornada_current(starters_rows, perjornada_rows,
                                  matches_rows, xw2)
    assert by_key["antonio blanco"] == {1: (8.0, 90.0), 2: (5.0, 45.0)}, \
        by_key["antonio blanco"]
    # "Came On"/"Unused Sub" have minutes but no points-page row at all —
    # left out of the universe entirely rather than entered at pts=0.
    assert "came on" not in by_key, by_key
    assert "unused sub" not in by_key, by_key
    assert _per_jornada_current([], [], [], xw2) == {}

    # A correction within one jornada (bonus points posted after the fact)
    # must overwrite that jornada's running total, not add to it.
    corrected = _per_jornada_current(
        starters_rows,
        [{"ff_id": "1", "player_name_full": "Antonio Blanco",
          "points_total": "8", "jornada": "1"},
         {"ff_id": "1", "player_name_full": "Antonio Blanco",
          "points_total": "9", "jornada": "1"}],   # +1 bonus point, same jornada
        matches_rows, xw2)
    # jornada 2 still carries his minutes (from starters_rows) with 0
    # points, since this fixture's perjornada_rows says nothing about it.
    assert corrected["antonio blanco"] == {1: (9.0, 90.0), 2: (0.0, 45.0)}, \
        corrected

    # Flat sum (decay=1.0) equals what the old cumulative approach gave:
    # 90 + 45 minutes, 8 + 5 points.
    wpts, wmatch = _weighted_totals(by_key["antonio blanco"], 1.0)
    assert (wpts, wmatch) == (13.0, 1.5), (wpts, wmatch)
    # Decayed at 0.5, one jornada back counts half: pts = 5 + 8*0.5 = 9.
    wpts, wmatch = _weighted_totals(by_key["antonio blanco"], 0.5)
    assert abs(wpts - 9.0) < 1e-9, wpts
    assert _weighted_totals({}, 0.5) == (0.0, 0.0)

    # Only one jornada on record: nobody has a second to walk forward to,
    # so the fit must refuse and hand back decay=1.0.
    one_jornada = {"a": {1: (4.0, 90.0)}, "b": {1: (2.0, 45.0)}}
    decay, why = _fit_decay(one_jornada)
    assert decay == 1.0 and "second jornada" in why, (decay, why)
    assert _fit_decay({}) == (1.0, "no player has a second jornada to "
                              "predict yet")
    # Two jornadas is still not enough: walking forward to jornada 2 trains
    # on exactly one point, so decay cannot differ from flat.
    decay0, _ = _fit_decay(
        {p: {1: (1.0, 90.0), 2: (9.0, 90.0)} for p in "ab"})
    assert decay0 == 1.0, decay0

    # Three jornadas: a steady rise, predicted from jornada 3 with two
    # training points to weight differently. Synthetic, but it proves the
    # grid can win when the evidence
    # favours it, and hand back a note that says so.
    trending = {p: {1: (1.0, 90.0), 2: (5.0, 90.0), 3: (9.0, 90.0)}
               for p in ("p%d" % i for i in range(8))}
    decay2, why2 = _fit_decay(trending)
    assert decay2 < 1.0 and "beat flat" in why2, (decay2, why2)

    # -- _precision_blend: reproduces The Book's own worked example ---------
    # Tango/Lichtman/Dolphin, ch.4: measured clutch skill +.100 (100 PA,
    # uncertainty .055) blended against the population's own clutch-skill
    # spread .000 ± .006 comes out to +.001 — almost entirely the prior,
    # because the population's own spread is known far more precisely than
    # 100 PA can measure one player's deviation from it.
    mean, var = _precision_blend([(0.100, 0.055 ** 2), (0.000, 0.006 ** 2)])
    assert abs(mean - 0.001) < 0.0005, mean
    assert var < 0.006 ** 2                    # more precise than either input alone
    # An estimate with no real precision (var <= 0) is skipped, not trusted
    # absolutely — 0 variance would silently claim infinite precision.
    assert _precision_blend([(5.0, 0.0), (3.0, 1.0)]) == (3.0, 1.0)
    # Nothing usable offered at all is None, not a fabricated answer.
    assert _precision_blend([]) is None
    assert _precision_blend([(5.0, 0.0)]) is None
    # Equal precision splits the difference exactly.
    eq_mean, eq_var = _precision_blend([(2.0, 1.0), (4.0, 1.0)])
    assert abs(eq_mean - 3.0) < 1e-9 and abs(eq_var - 0.5) < 1e-9

    # -- load_understat_current: position-gated on the real 2026-08-21
    # measurement (xG/xA carries signal for forwards/attacking mids, not for
    # anyone else), tested through the real function against a real
    # understat_players.csv shape, not a reimplementation of it ------------
    from ffcore.crosswalk import Crosswalk as _CW, Player as _P
    import ffcore.tidy as _tidy
    import tempfile as _tempfile, os as _os, csv as _csv

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
            # A defender is captured too — must be excluded, not blended in.
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
            # Only season-2026 rows here, so no cross-season pairs at all —
            # far below the 30-pair floor, must refuse.
            boost_thin, why_thin = _xg_stickiness_boost()
        finally:
            _tidy.TIDY = _real_tidy
    assert set(us_cur) == {"striker sam"}, us_cur      # the defender is excluded
    assert abs(us_cur["striker sam"]["xg90"] - 0.8) < 1e-9
    assert boost_thin == 1.0 and "30" in why_thin, (boost_thin, why_thin)

    # -- _linreg: the least-squares line, checked against a known slope ----
    slope, intercept = _linreg([1.0, 2.0, 3.0], [2.0, 4.0, 6.0])
    assert abs(slope - 2.0) < 1e-9 and abs(intercept) < 1e-9
    slope0, intercept0 = _linreg([1.0, 1.0, 1.0], [5.0, 5.0, 5.0])
    assert slope0 == 0.0 and abs(intercept0 - 5.0) < 1e-9  # no x-variance: flat

    # -- Scorer.rate(): the xG term as a third weighted source, exactly
    # reproducing the two-term formula when no xG reading exists ----------
    market_xg = [mk("Attacker", pos="delantero")]
    hist_xg = {"attacker": {"pts": 100.0, "pj": 34.0}}
    xi_xg = [{"player_name": "Attacker", "start_pct": "100"}]
    sc_plain = Scorer(market_xg, xi_xg, hist_xg)
    plain = sc_plain.rate(mk("Attacker", pos="delantero"))

    # 2 matches worth at boost 1.0, xG-implied rate 10.0 — must land
    # strictly between the no-xG rate and the xG-implied rate.
    sc_xg = Scorer(market_xg, xi_xg, hist_xg,
                  xg={"attacker": {"xg90": 1.0, "minutes": 180.0}},
                  xg_slope=10.0, xg_intercept=0.0, xg_boost=1.0)
    with_xg = sc_xg.rate(mk("Attacker", pos="delantero"))
    expect = (SHRINK_K * plain.ppm + 2.0 * 10.0) / (SHRINK_K + 2.0)
    assert abs(with_xg.ppm - expect) < 1e-9, (with_xg.ppm, expect)
    assert min(plain.ppm, 10.0) < with_xg.ppm < max(plain.ppm, 10.0)
    assert "xg" in with_xg.why
    # cur_pj/pj stay based on real matches only — xG sharpens the point
    # estimate, it doesn't manufacture evidence for the uncertainty.
    assert with_xg.cur_pj == 0.0 and with_xg.pj == 34.0

    # No xG data supplied -> no xG term, confirming the blend arithmetic
    # has no position logic of its own (that gate lives in
    # load_understat_current).
    sc_noxg = Scorer(market_xg, xi_xg, hist_xg, xg={})
    assert sc_noxg.rate(mk("Attacker", pos="delantero")) == plain

    # -- Scorer.rate(): the shots term, gated on shots_n >= 10 explicitly —
    # an untrained fit must not drag a thin-evidence forward down --------
    sc_shots_untrained = Scorer(
        market_xg, xi_xg, hist_xg,
        shots={"attacker": {"shots90": 3.0, "minutes": 180.0}},
        shots_slope=0.0, shots_intercept=0.0, shots_n=3)   # below the floor
    assert sc_shots_untrained.rate(mk("Attacker", pos="delantero")) == plain

    sc_shots = Scorer(
        market_xg, xi_xg, hist_xg,
        shots={"attacker": {"shots90": 2.0, "minutes": 180.0}},
        shots_slope=5.0, shots_intercept=0.0, shots_n=10)
    with_shots = sc_shots.rate(mk("Attacker", pos="delantero"))
    # 2 matches worth, shots-implied rate 5.0*2.0=10.0 — same arithmetic.
    expect_shots = (SHRINK_K * plain.ppm + 2.0 * 10.0) / (SHRINK_K + 2.0)
    assert abs(with_shots.ppm - expect_shots) < 1e-9, \
        (with_shots.ppm, expect_shots)
    assert "shots" in with_shots.why

    # -- backtest_predictor: known-answer synthetic cases first ----------
    assert backtest_predictor({}, {}, min_pairs=1) is None    # nothing to test
    # An exact linear function of the actual (y=2x, no noise) must beat
    # the baseline: 12 players, feature rising 1..12, actual doubles it.
    exact_feat = {str(i): {1: float(i), 2: float(i)} for i in range(1, 13)}
    exact_act = {str(i): {1: float(i) - 1, 2: float(i) + 1, 3: float(i) * 2}
                for i in range(1, 13)}
    exact = backtest_predictor(exact_feat, exact_act, min_pairs=10)
    assert exact is not None and exact["n"] == 12, exact
    assert exact["mae_feature"] < exact["mae_baseline"], exact
    assert exact["gap"]["beats"], exact          # real, not a coin flip
    # Pure noise: a fitted line must not reliably beat the baseline just
    # because it's fitted.
    import random as _random_bp
    _rng_bp = _random_bp.Random(7)
    noise_feat = {str(i): {1: _rng_bp.random(), 2: _rng_bp.random()}
                 for i in range(1, 13)}
    noise_act = {str(i): {1: 5.0, 2: 5.0, 3: 5.0 + _rng_bp.uniform(-0.5, 0.5)}
                for i in range(1, 13)}
    noisy = backtest_predictor(noise_feat, noise_act, min_pairs=10)
    assert noisy is not None and not noisy["gap"]["beats"], noisy

    # Regression case for the pooled-baseline fix: players' actual levels
    # vary a lot with just 1-2 prior jornadas each — an unpooled "his own
    # average" baseline is noisy enough that a fitted line beats it on
    # pure random noise, purely by pooling. Pinned down synthetically so
    # it can't come back silently.
    _rng_reg = _random_bp.Random(11)
    varied_levels = {str(i): 2.0 + i * 3.0 for i in range(1, 21)}  # 5..62
    noise_feat2 = {k: {1: _rng_reg.random(), 2: _rng_reg.random()}
                  for k in varied_levels}
    noise_act2 = {k: {1: lvl + _rng_reg.uniform(-1, 1),
                      2: lvl + _rng_reg.uniform(-1, 1),
                      3: lvl + _rng_reg.uniform(-1, 1)}
                 for k, lvl in varied_levels.items()}
    noisy2 = backtest_predictor(noise_feat2, noise_act2, min_pairs=10)
    assert noisy2 is not None and not noisy2["gap"]["beats"], noisy2

    # -- walk_forward_compare: known-answer cases first -------------------
    jornadas_wf = [1, 2, 3, 4, 5]
    # p1 always scores 10.0, p2 always 4.0 — a fixed, known truth.
    actual_map = {1: {"p1": 10.0, "p2": 4.0}, 2: {"p1": 10.0, "p2": 4.0},
                 3: {"p1": 10.0, "p2": 4.0}, 4: {"p1": 10.0, "p2": 4.0},
                 5: {"p1": 10.0, "p2": 4.0}}

    def actual_wf(j):
        return actual_map[j]

    # Old approach: a fixed 7.0 for everyone, always wrong by a constant.
    def predict_old_flat(j):
        return {"p1": 7.0, "p2": 7.0}

    # New approach: averages every prior jornada's true value — exact
    # after jornada 2, so it must clearly beat the flat-7.0 old approach.
    def fit_mean(cutoff):
        prior = [actual_map[j] for j in jornadas_wf if j < cutoff]
        if not prior:
            return {"p1": 7.0, "p2": 7.0}   # no history yet: same as "old"
        return {k: sum(p[k] for p in prior) / len(prior) for k in ("p1", "p2")}

    def predict_mean(params, j):
        return dict(params)

    exact_wf = walk_forward_compare(jornadas_wf, fit_mean, predict_mean,
                                    predict_old_flat, actual_wf,
                                    min_history=1)
    assert exact_wf is not None and exact_wf["n"] == 8, exact_wf  # 4 jornadas x 2 players
    assert exact_wf["mae_new"] < 1e-9 < exact_wf["mae_old"], exact_wf
    assert exact_wf["gap"]["beats"], exact_wf
    assert len(exact_wf["per_jornada"]) == 4, exact_wf   # jornada 1 skipped (min_history)

    # min_history skips the right jornadas, not an off-by-one.
    exact_wf0 = walk_forward_compare(jornadas_wf, fit_mean, predict_mean,
                                     predict_old_flat, actual_wf,
                                     min_history=0)
    assert len(exact_wf0["per_jornada"]) == 5, exact_wf0

    # A candidate that's genuinely WORSE than the old approach reports
    # that honestly — this function does not flatter its own "new" side.
    def fit_bad(cutoff):
        return {"p1": 0.0, "p2": 0.0}

    def predict_bad(params, j):
        return dict(params)

    worse_wf = walk_forward_compare(jornadas_wf, fit_bad, predict_bad,
                                    predict_old_flat, actual_wf,
                                    min_history=1)
    assert worse_wf["mae_new"] > worse_wf["mae_old"], worse_wf

    # No overlap at all between the two prediction dicts and the actuals:
    # None, not a crash or a fabricated zero.
    assert walk_forward_compare(
        jornadas_wf, lambda c: {}, lambda p, j: {},
        lambda j: {"nobody": 1.0}, actual_wf, min_history=1) is None

    # -- log_experiment / experiment_history: a negative result logs the
    # same as a positive one ----------------------------------------
    import tempfile as _tempfile5
    from ffcore import tidy as _tidy5

    with _tempfile5.TemporaryDirectory() as _d5:
        _real_decisions5 = _tidy5.DECISIONS
        _tidy5.DECISIONS = __import__("pathlib").Path(_d5)
        try:
            assert experiment_history() == []
            log_experiment("shots", "delantero", exact, "kept",
                          "synthetic exact-fit case")
            log_experiment("team_defense", "defensa", None, "no effect",
                          "not enough data")
            hist = experiment_history()
            assert len(hist) == 2, hist
            assert hist[0]["feature"] == "shots" and hist[0]["verdict"] == "kept"
            assert hist[1]["n"] == "", hist[1]   # None result -> blank, not 0
        finally:
            _tidy5.DECISIONS = _real_decisions5

    # -- _shots_by_jornada / _shots_points_fit / load_shots_current: the
    # real join through api_stats.csv, gated to forwards -----------------
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
        {"player_name": "Fwd Guy", "player_slug": "fwd-guy", "role": "starter",
         "minute": "", "match_id": "m1"},
        {"player_name": "Fwd Guy", "player_slug": "fwd-guy", "role": "starter",
         "minute": "", "match_id": "m2"},
        {"player_name": "Fwd Guy", "player_slug": "fwd-guy", "role": "starter",
         "minute": "", "match_id": "m3"},
    ]
    perjornada3 = [
        {"ff_id": "900", "player_name_full": "Fwd Guy",
         "points_total": "2", "jornada": "1"},
        {"ff_id": "900", "player_name_full": "Fwd Guy",
         "points_total": "6", "jornada": "2"},   # +4 that jornada
        {"ff_id": "900", "player_name_full": "Fwd Guy",
         "points_total": "16", "jornada": "3"},  # +10 that jornada
    ]
    with _tempfile3.TemporaryDirectory() as _d3:
        _os3.makedirs(_os3.path.join(_d3, "season", "live"))
        with open(_os3.path.join(_d3, "api_stats.csv"), "w", newline="",
                 encoding="utf-8") as fh:
            w = _csv3.DictWriter(fh, fieldnames=[
                "observed_at", "source", "player_id", "week", "stat",
                "value", "points"])
            w.writeheader()
            # Fwd Guy: rising shot volume, jornadas 1-2 (the "prior" side).
            for wk, val in ((1, "1"), (2, "3")):
                w.writerow({"observed_at": "2026-08-%02dT0000Z" % (15+wk*7),
                           "source": "laliga", "player_id": "900",
                           "week": str(wk), "stat": "total_scoring_att",
                           "value": val, "points": "0"})
            # A non-forward with real shot data too — must be excluded.
            w.writerow({"observed_at": "2026-08-22T0000Z", "source": "laliga",
                       "player_id": "901", "week": "1",
                       "stat": "total_scoring_att", "value": "5",
                       "points": "0"})
        fake_players3 = {"fwd guy": {"pos": "delantero"},
                        "def guy": {"pos": "defensa"}}
        with open(_os3.path.join(_d3, "starters.csv"), "w", newline="",
                 encoding="utf-8") as fh:
            w = _csv3.DictWriter(fh, fieldnames=[
                "player_name", "player_slug", "role", "minute", "match_id"])
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
            # NOT position-gated here — a raw join, same shape for every
            # player api_stats.csv covers. The position gate lives in the
            # two callers below (_shots_points_fit/load_shots_current),
            # same split load_understat_current()/_xg_points_fit() use.
            assert sbj == {"fwd guy": {1: 1.0, 2: 3.0},
                          "def guy": {1: 5.0}}, sbj
            fit_slope, fit_intercept, fit_n = _shots_points_fit(
                xw3, players=fake_players3)
            # Only ONE forward in this tiny fixture — below the 10-pair
            # floor, so it must refuse rather than fit a line through one
            # point, same discipline _xg_points_fit() already has.
            assert (fit_slope, fit_intercept, fit_n) == (0.0, 0.0, 1), \
                (fit_slope, fit_intercept, fit_n)
            lsc = load_shots_current(xw3, players=fake_players3)
            # 2 jornadas' worth of minutes (90 each, from starters3's
            # m1/m2), 1.0+3.0=4.0 shots total -> 4.0/180*90 = 2.0 per 90.
            assert set(lsc) == {"fwd guy"}, lsc
            assert abs(lsc["fwd guy"]["shots90"] - 2.0) < 1e-9, lsc
            assert lsc["fwd guy"]["minutes"] == 180.0, lsc
        finally:
            _tidy3.TIDY, _tidy3.SEASON = _real_tidy3, _real_season3

    print("ffcore.score self-test OK (82 cases)")


if __name__ == "__main__":
    _selftest()
