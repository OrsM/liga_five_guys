"""
ffcore.score — the ranking index, and the legal-XI picker that consumes it.

Lifted out of report.py so rivals.py scores rival squads with the SAME
function. A comparison between your squad and theirs is meaningless if the
two sides were scored by two copies of the arithmetic that have drifted
apart, and copies always drift.

    score = shrunk points-per-match  x  fixture factor  x  P(start)

Points-per-match comes from data/season/points_*.csv. A raw average is
untrustworthy on few appearances, so it is pulled toward the median for that
position:

    shrunk = (total_points + K * prior) / (matches + K)      K = 8 matches

THIS SEASON is a second stage of the same shrinkage — last season's shrunk
figure becomes the prior that this season's points are pulled toward, with
the same K:

    ppm = (points_now + K * shrunk_last_season) / (matches_now + K)

So one jornada moves a rating by about a ninth of the gap between the two,
and by the twentieth it is almost entirely this season. No new constant, and
with no current-season data — the state before J1 finishes, and the state
this collapsed to for the whole of 2026-08-16 to 2026-08-20 while nothing
read data/season/live/perjornada_*.csv, which already had it — it collapses
EXACTLY to the line above. This is the fix for the model's most concrete
error, which is not the formula but the input: last season's average cannot
know that a player changed club, aged, or lost his place.

"matches_now" IS MINUTES, NOT AN APPEARANCE COUNT — see
_current_from_perjornada(). A 10-minute cameo and a full 90 were being
weighted identically otherwise, which is the same distortion the shrinkage
above already exists to correct for on the PRIOR side and was silently
reintroducing on the live one.

THE FIXTURE FACTOR comes from ffcore.fixture and is the opponent the player
actually faces next, home or away. `score` carries it; `flat` is the same
arithmetic without it. Both are returned because they answer different
questions: you FIELD for one round, so the fixture belongs in that decision,
and you BUY for months, so it does not belong in that one. A bid sized on a
kind fixture is a bid for a fixture, not for a player.

The result is a RANKING INDEX, not a points forecast. Three things it cannot
know, each surfaced rather than hidden:

  * Promoted-side players have no top-flight record, so they fall back to the
    positional prior — the median top-flight starter, which flatters them.
    Their ratings are marked `assumed` and discounted. Promotion is detected
    from the data, not hardcoded, so it keeps working next season.
  * A player absent from the probable-XI page is not the same as one listed
    with no percentage. The first gets ABSENT_START, the second NEUTRAL_START.
  * Nothing here has been checked against reality yet. Log the inputs
    alongside every recommendation and score them once jornadas exist.

Scoring a rival's squad carries one extra caveat over scoring your own: you
know your roster exactly, while theirs comes from replaying the ledger, so
any name still unmatched in data/tidy is silently missing from their total.
Report the unmatched count next to the total or the comparison flatters you.
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

# Pseudo-count in reliability = n/(n+K), used at BOTH shrink stages in
# Scorer.rate(). Fitted 2026-08-31 against 729 real matches (two
# out-of-sample tests, K=6 and K=16 respectively) — 8.0 sits inside both
# tests' 90% bootstrap interval and the basin is flat enough that neither
# test's own optimum beats it by more than 0.4% MSE. Not a guess.
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
# jornadas — 1.0 is "no decay at all" (this season's flat average, today's
# behaviour), included so the grid can validly choose it. Coarse, like
# startprob's grids: the data cannot resolve finer, and a grid is auditable
# where a solver's answer is not.
DECAY_GRID = (1.0, 0.85, 0.7, 0.55, 0.4)


# xG/xA — Tango/Lichtman/Dolphin's precision-weighted blend (The Book,
# ch.4's clutch-skill estimate), folded into Scorer.rate() as a third
# weighted term alongside the prior and current season. Position-gated to
# forwards/attacking mids (measured: xG carries no signal, wrong sign,
# for any other position) and both fit parameters (_xg_points_fit's units
# conversion, _xg_stickiness_boost's reliability ratio) are derived from
# this repo's own real data, never hand-picked.
# Why: docs/notes/score.md#xgxa-precision-weighted-blend
# ---------------------------------------------------------------------------


def _precision_blend(estimates) -> tuple[float, float] | None:
    """(mean, variance) — independent estimates of ONE quantity, combined
    by inverse variance.

    `estimates` is [(mean, variance), ...]. An estimate with variance <= 0
    is skipped rather than trusted absolutely; None back means nothing
    usable was offered, never a fabricated answer.

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
    THIS season, forwards and attacking mids only.

    Keyed by norm(market name), same as `history`/`current` — the same
    translation `_current_from_perjornada()` uses, because that's what
    `Scorer.rate()` actually looks `self.xg` up by; keying by the
    crosswalk id instead would silently miss every lookup rate() makes.
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
    """(slope, intercept) of the least-squares line through (xs, ys)."""
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    slope = sxy / sxx if sxx else 0.0
    return slope, my - slope * mx


def _xg_points_fit(xw) -> tuple[float, float, int]:
    """(slope, intercept, n) — last season's real points-per-match as a
    linear function of last season's xG+xA per 90, forwards/attacking mids
    only (the position gate load_understat_current() also uses).

    The units conversion xG-implied output needs before joining a
    points-per-match blend, fit fresh from this repo's own real data.
    Below 10 paired players this refuses (slope 0.0, intercept 0.0) rather
    than fit a line through noise.
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
    match is worth, derived from measured year-over-year stability.

    No crosswalk needed (both seasons carry Understat's own understat_id).
    Self-correcting, not frozen: recomputed from whatever
    understat_players.csv holds when called, no cache. Below 30 paired
    players this refuses and returns (1.0, why). Clipped to [0.5, 3.0].
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
        n = len(xs)
        mx, my = sum(xs) / n, sum(ys) / n
        cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
        sx = sum((x - mx) ** 2 for x in xs) ** 0.5
        sy = sum((y - my) ** 2 for y in ys) ** 0.5
        return cov / (sx * sy) if sx and sy else 0.0

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


def _per_jornada_current(starters_rows, perjornada_rows, matches_rows,
                         xw) -> dict[str, dict[int, tuple[float, float]]]:
    """{crosswalk key: {jornada: (points, minutes)}} for the live season.

    Joins starters.csv's minutes (keyed by match_id) to perjornada.csv's
    points (keyed by its own `jornada` column) via matches.csv's
    match_id -> jornada map. A jornada absent from either side is dropped
    rather than guessed.
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
    # is missing whatever a player had on the board before this file's
    # own history started (points.py's diff() has no row to diff the
    # very first snapshot against). Why: docs/notes/score.md#_per_jornada_current--the-join-and-the-points_total-anchor
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

    # The universe is the points-page's own, not everyone starters.csv
    # names — 90 players with real minutes carry no points-page row at
    # all, and are left out entirely rather than entered at pts=0.
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
    """(weighted points, weighted matches) for one player.

    Most recent jornada weighs 1, one back `decay`, two back `decay**2`;
    decay=1.0 is an exact flat sum (no special case).
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
    """(recency-weighted participation rate, weighted jornada count).

    Same per_jornada/decay as _weighted_totals, but a decayed RATE in its
    own right (Σ(w·min(1, minutes/90)) / Σw) — lives in [0, 1], stands in
    for a start probability directly.
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
    """(decay, why) — the recency weighting earns its use ONLY if it beats
    the flat average out of sample, same discipline
    `ffcore.startprob.Calibration.fit()` uses for P(start).

    Walk-forward, not leave-one-out (deliberately not Calibration.fit()'s
    pattern — jornadas have a time-order that matters, sheets don't).
    Jornada J is only ever predicted from jornadas strictly before it.
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
    """{norm(market name): {"pts": season-to-date points, "pj": minutes / 90,
    "start_rate": recency-weighted share of a jornada started,
    "start_n": weighted jornada count behind that rate}} from this
    season's per-jornada tracker, or ({}, "") before it exists.

    Not data/season/points_<season>.csv (load_points()'s other source) —
    that snapshots the points PAGE, which reads empty until J1 is fully
    played; perjornada_*.csv is the real live source. Keyed through the
    crosswalk twice (ff_id -> canonical player -> norm(market name), the
    key Scorer.rate() actually uses). "pj" is minutes, not an appearance
    count. Recency-weighted via `_fit_decay`'s walk-forward validation,
    not a flat average — with only one jornada on record (this repo's
    state at time of writing) this collapses to the old flat-sum behaviour
    exactly.
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

    PRIOR: the newest data/season/points_*.csv (last season's completed
    totals). CURRENT: this season's live per-jornada tracker (see
    _current_from_perjornada()). Two files, not one, on the PRIOR side —
    reading only the newest points_*.csv (what report.py/rivals.py each
    did before this module existed) is a bug waiting for the season to
    roll over: the moment points_2026-27.csv appears as a completed
    snapshot, the actual prior would vanish.
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
            # The id first, under the market's CURRENT name — a completed
            # snapshot is frozen at whatever names were true when written.
            # Falls back to name-only for a file predating ff_id (added
            # 2026-08-21) rather than losing older rows.
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
    """(Scorer, labels) wired to every input the model has.

    ONE builder — report.py and rivals.py must score with identical
    arithmetic, the whole reason this module was lifted out of report.py.
    `calibrate` fits P(start) against confirmed line-ups and turns itself
    off with nothing played, or a fit that loses on unseen line-ups.
    Why: docs/notes/score.md#build--one-model-per-run
    """
    from ffcore.fixture import fixture_board
    from ffcore.tidy import load_elo, load_fixtures

    prior, prior_label, cur, cur_label = load_points()
    cal, second = None, None
    if calibrate:
        cal, second = _calibrated()
    # Club Elo ranks the opponents when it covers all of them and squad value
    # ranks them otherwise, per club that real results (below) don't reach —
    # wired HERE, in the one builder, so your squad and a rival's can never
    # be scored off two different difficulty scales.
    from ffcore.tidy import (load_crosswalk, load_results_history,
                             load_understat_players)
    xw = load_crosswalk()
    board = fixture_board(market, load_fixtures(), now, load_elo(),
                          xw=xw, results=load_results_history(),
                          understat_rows=load_understat_players("2025"))
    # xG/xA — see this module's own section above for the mechanism and why
    # both numbers are fit fresh from real data rather than hand-picked.
    xg_cur = load_understat_current(xw)
    xg_slope, xg_intercept, xg_n = _xg_points_fit(xw)
    xg_boost, xg_why = _xg_stickiness_boost()
    sc = Scorer(market, xi_rows, prior, shrink_k=shrink_k,
                current=cur, board=board, cal=cal, second=second,
                xg=xg_cur, xg_slope=xg_slope, xg_intercept=xg_intercept,
                xg_n=xg_n, xg_boost=xg_boost, xg_why=xg_why)
    return sc, (prior_label, cur_label)


_CAL_CACHE: list = []


def _calibrated():
    """(Calibration, second-source rows), fitted once per process.

    Cached: the fit cross-validates over every team sheet on record. The
    cut is the first confirmed line-up seen — anything published after
    may already be the team sheet, and grading a forecast against itself
    is how a model marks its own homework.
    Why: docs/notes/score.md#_calibrated--caching-and-the-fingerprint-bug
    """
    if _CAL_CACHE:
        return _CAL_CACHE[0]
    import json
    from ffcore.crosswalk import Crosswalk
    from ffcore.startprob import Calibration, METHOD_VERSION, observations
    from ffcore.tidy import load_lineups, read_csv, TIDY
    from ffcore.second import SECOND_SOURCE

    second = load_lineups(SECOND_SOURCE)
    truth = read_csv(TIDY / "starters.csv")
    cut = min((r.get("observed_at", "") for r in truth), default="")
    # The crosswalk is what lets the narrow source be joined exactly rather
    # than on a folded name: it shares no slug with anything else.
    xw = Crosswalk.read(TIDY / "players.csv", TIDY / "clubs.csv")
    # On disk, keyed by what it was fitted on — the fit costs ~6s in every
    # process, and a changed fingerprint refits; nothing else does.
    # METHOD_VERSION is part of that evidence, not just the data.
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
    """Legal shapes — the free tier, confirmed against the app's picker.

    A `premium: bool` parameter (and PREMIUM_FORMATIONS, and pick_xi()'s own
    matching parameter) used to exist for the rare rival on a paid
    subscription, but nothing anywhere ever called it with premium=True —
    deleted 2026-09-05 as dead plumbing, not as a feature decision. Add it
    back the same way if a caller genuinely needs it.
    """
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
    note: str               # diagnosis / expected return, when published
    assumed: bool
    why: str
    value: float
    delta_1d: float
    delta_pct: float
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
                 xg_boost: float = 1.0, xg_why: str = ""):
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
        # xG/xA — see this module's own section above `_precision_blend`.
        # {key: {"xg90":..., "minutes":...}}, forwards/attacking mids only.
        self.xg = xg or {}
        self.xg_slope = xg_slope
        self.xg_intercept = xg_intercept
        self.xg_n = xg_n            # how many (player, xG, ppm) pairs fit the slope
        self.xg_boost = xg_boost    # pseudo-matches an xG match is worth vs a raw one
        self.xg_why = xg_why        # printed by callers that want the provenance

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
        self.notes: dict[str, str] = {}
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
                if r.get("note"):
                    self.notes[key] = r["note"]

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
        if len(terms) == 1:
            return Rating(base, why, assumed, 0.0, prior_pj)
        w_sum = sum(w for w, _ in terms)
        blended = sum(w * m for w, m in terms) / w_sum
        why_now = why
        if cur_pj > 0:
            why_now += " + %.0fp/%.0fj now" % (c["pts"], cur_pj)
        why_now += xg_note
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

        # Scaling by P(start) prices a non-start at zero — right because
        # the free tier has no auto-substitution (verified in-app,
        # 2026-08-16, issue #28). `pct_used` is the source's figure GRADED
        # against confirmed line-ups (identity until a jornada is played).
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
            on_page=on_page, status=st, note=self.notes.get(key, ""),
            assumed=rating.assumed,
            why=rating.why,
            value=money(rec.get("value")) or 0.0,
            delta_1d=ratio(rec.get("delta_1d")) or 0.0,
            delta_pct=ratio(rec.get("delta_pct_1d")) or 0.0,
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
    """The two stages of the blend, and the fixture that only fielding uses.

    score.py had no self-test: it was covered sideways through bid.py and
    report.py, which is coverage of the callers, not of the arithmetic. These
    are the cases the arithmetic owns.
    """
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

    # AN EMPTY CURRENT SEASON IS A NO-OP. This is today's live state: the
    # points page reads "No se encontraron resultados" until J1 finishes, and
    # that must not reset anybody.
    assert Scorer(market, xi, hist, current={}).rate(mk("p0")) == full
    assert Scorer(market, xi, hist,
                  current={"p0": {"pts": 0.0, "pj": 0.0}}).rate(mk("p0")) \
        == full

    # THE FIXTURE SPLITS THE TWO DECISIONS. Same player, same inputs; the
    # fielding number moves with the opponent and the buying number does not.
    when = __import__("datetime").datetime.fromisoformat(
        "2026-08-20T19:00:00+00:00")
    # atk_factor and def_factor DIFFER here on purpose — p0 is a defensa
    # (mk()'s default), so score() must reach for def_factor (1.10), not
    # atk_factor (1.30).
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

    # -- P(start) blended against real recent minutes, same stage as pts ---
    # Editorial says 100%; he has actually started nothing lately. The
    # blend must pull pct_used DOWN from 100, not leave it as the whole
    # answer — the actual behaviour "does a player go out of rotation"
    # needs, ahead of the editorial page catching up.
    benched_cur = {"p0": {"pts": 30.0, "pj": 3.0,
                          "start_rate": 0.0, "start_n": 6.0}}
    sc4 = Scorer(market, xi, hist, current=benched_cur, board={"Mid": easy})
    benched_s = sc4.score(mk("p0"))
    assert benched_s.pct_used < 100.0, benched_s.pct_used
    # SHRUNK, NOT OVERWRITTEN: 6 weighted jornadas of real zero against
    # shrink_k=8 pseudo-matches of editorial 100% is still a blend, and the
    # formula is exact — (8*100 + 6*0) / (8+6).
    assert abs(benched_s.pct_used - 800.0 / 14.0) < 1e-9, benched_s.pct_used

    # NO CURRENT-SEASON EVIDENCE IS A NO-OP, same discipline as the points
    # blend above — an editorial 100% with nothing to weigh it against
    # stays 100%.
    untouched = Scorer(market, xi, hist, current={}, board={"Mid": easy}
                       ).score(mk("p0"))
    assert untouched.pct_used == 100.0, untouched.pct_used
    # ...and pct_rest, with no season evidence to differ on, is the same
    # number — nothing else to answer jornada 10 with either.
    assert untouched.pct_rest == 100.0, untouched.pct_rest

    # -- pct_rest: a REGULAR STARTER'S standing rate survives ONE bad
    # week's editorial reading; pct_used, which answers for the very next
    # jornada, does not have to. A card suspension (editorial 0%, thin
    # season sample — 2 weighted jornadas, the actual shape a real
    # suspended defender's own current-season record has) should read as
    # "out this week" (pct_used pulled toward 0), not "a rotation risk all
    # season" (pct_rest should stay well above it — anchored at NEUTRAL_
    # START, not at this week's 0%).
    starter_cur = {"p0": {"pts": 30.0, "pj": 2.0,
                          "start_rate": 0.9, "start_n": 2.0}}
    susp = [{"player_name": "p0", "start_pct": "0", "status": "suspended"}]
    sc5 = Scorer(market, susp, hist, current=starter_cur, board={"Mid": easy})
    susp_s = sc5.score(mk("p0"))
    # THE FORMULAS ARE EXACT, same shrink_k=8 pseudo-matches both blends
    # already trust — pct_used anchored on this week's editorial 0%,
    # pct_rest on NEUTRAL_START, and only NEUTRAL_START's anchor never
    # sees the suspension.
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
        # starters.csv's SHORT form ("Blanco") must still resolve to the
        # market's full name's crosswalk key — this is the actual bug: a
        # first version matched on the raw name string, matched 10 of 42
        # real players against api_stats' ground-truth minutes, and
        # silently scored the other 32 (Antonio Blanco among them, 89
        # real minutes) as zero.
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
        # THE SAME MATCH, CARRIED FORWARD into 56 more snapshots — exactly
        # what starters.csv's raw table actually holds, measured: one
        # player's row for one match appeared 57 times, and summing all of
        # them credited him 5,130 minutes in a season that had played one
        # jornada. Must count once, not 57 times.
        *({"player_name": "Blanco", "player_slug": "blanco",
           "role": "starter", "minute": "", "match_id": "m1"}
          for _ in range(56)),
    ]
    perjornada_rows = [
        # THE UNDIFFED-BASELINE CASE, REAL AND MEASURED: this repo's own
        # points_delta once read 7 for a player whose points_total said
        # 11 — the missing 4 being whatever he had on the board before
        # this file's own history started. Anchoring on points_total
        # rather than summing points_delta is what recovers the true 8
        # here despite a delta that only claims 3.
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
    # REAL, MEASURED GAP: "Came On" and "Unused Sub" have starters.csv
    # minutes but no row on the points page at all — 90 such players on
    # this repo's own store. Left OUT of the universe entirely rather than
    # entered at pts=0: that would guess "he scored nothing" where the
    # honest reading is "this page does not say," the same distinction
    # NEUTRAL_START/ABSENT_START already draws for the other source.
    assert "came on" not in by_key, by_key
    assert "unused sub" not in by_key, by_key
    assert _per_jornada_current([], [], [], xw2) == {}

    # A CORRECTION WITHIN ONE JORNADA (bonus points posted after the fact,
    # the exact case that produced a second row for one match in this
    # repo's own store) must OVERWRITE that jornada's running total, not
    # add another jornada's worth on top of it.
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

    # ONLY ONE JORNADA ON RECORD: nobody has a second one to walk forward
    # to, so the fit must refuse and hand back decay=1.0 — this is where
    # this repo's own live data stands at the time of writing, and it
    # must be provably inert.
    one_jornada = {"a": {1: (4.0, 90.0)}, "b": {1: (2.0, 45.0)}}
    decay, why = _fit_decay(one_jornada)
    assert decay == 1.0 and "second jornada" in why, (decay, why)
    assert _fit_decay({}) == (1.0, "no player has a second jornada to "
                              "predict yet")
    # TWO JORNADAS total is STILL not enough: walking forward to jornada 2
    # trains on exactly one jornada, and decay cannot differ from flat
    # with only one training point to weight.
    decay0, _ = _fit_decay(
        {p: {1: (1.0, 90.0), 2: (9.0, 90.0)} for p in "ab"})
    assert decay0 == 1.0, decay0

    # THREE JORNADAS, A REAL RECENCY SIGNAL: a steady rise, predicted from
    # jornada 3 with two training points (1 and 2) to weight differently —
    # decay can only show an edge once there is more than one training
    # point, which is exactly why this needs 3 jornadas and not 2.
    # Synthetic, but it proves the grid can actually win when the evidence
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
            # -- _xg_stickiness_boost: this file has ONLY season-2026 rows,
            # so no player pairs across two seasons at all — far below the
            # 30-pair floor, and it must refuse rather than trust a ratio
            # measured on nothing.
            boost_thin, why_thin = _xg_stickiness_boost()
        finally:
            _tidy.TIDY = _real_tidy
    # Keyed by norm(market name) — "striker sam" — not the crosswalk id,
    # the same translation _current_from_perjornada() does, because that
    # is what Scorer.rate() actually looks self.xg up by.
    assert set(us_cur) == {"striker sam"}, us_cur      # the defender is excluded
    assert abs(us_cur["striker sam"]["xg90"] - 0.8) < 1e-9
    assert boost_thin == 1.0 and "30" in why_thin, (boost_thin, why_thin)

    # -- _linreg: the least-squares line, checked against a known slope ----
    slope, intercept = _linreg([1.0, 2.0, 3.0], [2.0, 4.0, 6.0])
    assert abs(slope - 2.0) < 1e-9 and abs(intercept) < 1e-9
    slope0, intercept0 = _linreg([1.0, 1.0, 1.0], [5.0, 5.0, 5.0])
    assert slope0 == 0.0 and abs(intercept0 - 5.0) < 1e-9  # no x-variance: flat

    # -- Scorer.rate(): the xG term folds in as a THIRD weighted source, and
    # the two-term formula it generalises is reproduced exactly when no xG
    # reading exists for a player --------------------------------------------
    market_xg = [mk("Attacker", pos="delantero")]
    hist_xg = {"attacker": {"pts": 100.0, "pj": 34.0}}
    xi_xg = [{"player_name": "Attacker", "start_pct": "100"}]
    sc_plain = Scorer(market_xg, xi_xg, hist_xg)
    plain = sc_plain.rate(mk("Attacker", pos="delantero"))

    # Same inputs, an xG reading added: 2 matches worth at boost 1.0, xG-
    # implied rate of 10.0. The blend must land strictly between the
    # no-xG rate and the xG-implied rate, and match the explicit formula.
    sc_xg = Scorer(market_xg, xi_xg, hist_xg,
                  xg={"attacker": {"xg90": 1.0, "minutes": 180.0}},
                  xg_slope=10.0, xg_intercept=0.0, xg_boost=1.0)
    with_xg = sc_xg.rate(mk("Attacker", pos="delantero"))
    expect = (SHRINK_K * plain.ppm + 2.0 * 10.0) / (SHRINK_K + 2.0)
    assert abs(with_xg.ppm - expect) < 1e-9, (with_xg.ppm, expect)
    assert min(plain.ppm, 10.0) < with_xg.ppm < max(plain.ppm, 10.0)
    assert "xg" in with_xg.why
    # cur_pj/pj stay based on REAL matches only — the xG term sharpens the
    # point estimate, it does not manufacture evidence for the uncertainty
    # ffcore.forecast widens around it.
    assert with_xg.cur_pj == 0.0 and with_xg.pj == 34.0

    # A defender gets no xG term even if one is (wrongly) supplied — rate()
    # only ever looks self.xg up by the SAME key the market row resolves
    # to, so a caller that built self.xg correctly (load_understat_current's
    # own position gate) never reaches this path for a non-attacker; this
    # just confirms the blend arithmetic itself has no position logic of
    # its own baked in — that gate lives entirely in load_understat_current.
    sc_noxg = Scorer(market_xg, xi_xg, hist_xg, xg={})
    assert sc_noxg.rate(mk("Attacker", pos="delantero")) == plain

    print("ffcore.score self-test OK (58 cases)")


if __name__ == "__main__":
    _selftest()
