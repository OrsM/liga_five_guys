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
           "FREE_FORMATIONS", "PREMIUM_FORMATIONS", "formations",
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
# Premium subscription: these shapes, the captain boost and the coach slot.
PREMIUM_FORMATIONS = [(5, 2, 3), (4, 6, 0), (4, 2, 4), (3, 6, 1), (3, 3, 4)]

# Matches of prior weight — the pseudo-count in reliability = n/(n+K), used
# at BOTH shrink stages in Scorer.rate() (last season toward the positional
# median, then this season toward that). It matters more than it looks:
# checked what published win-probability models actually rely on for "how
# much should an early lead be trusted" (FiveThirtyEight's NBA/NHL/MLB
# methodology, 2026-08-21) and it is THIS mechanism — a one-time, measured
# revert-to-mean fraction on the prior — not a forward drift term.
#
# NO LONGER A GUESS: FITTED 2026-08-31 AGAINST REAL DATA, AND LEFT AT 8.0
# BECAUSE THE FIT CANNOT TELL 8 APART FROM ANYTHING NEAR IT. This note used
# to say the fit was blocked on MIN_POOL=200 observed matches (159 as of
# 2026-08-21). That bar cleared over a week ago — 729 observed matches in
# data/season/live/perjornada_2026-27.csv — so the fit was actually run,
# two ways, both out of sample, on the same store the scorer itself reads:
#
#   A. CROSS-SEASON. Predict each of the 534 real 2026-27 per-match scores
#      belonging to a player with a 2025-26 record, from his K-shrunk
#      last-season rate (stage one, exactly as rate() computes it). The
#      training and scored samples are DIFFERENT SEASONS, so nothing leaks.
#   B. WALK-FORWARD WITHIN 2026-27. Both stages, one shared K, predicting
#      each jornada from that player's earlier ones only (n=271).
#      Walk-forward and not leave-one-out, for the reason _fit_decay()
#      below already gives: leave-one-out lets a later jornada leak into a
#      "prediction" for an earlier one.
#
#      K        A: MSE      B: MSE
#      0        15.514      26.264
#      4        15.287      16.597
#      6        15.279  <-  16.382
#      8        15.285  ship 16.296  ship
#      16       15.365      16.237  <-
#      32       15.555      16.326
#      infinite 16.572      17.122     (the prior alone, no player record)
#
# BOTH TESTS AGREE THAT SHRINKING HELPS A LOT AND DISAGREE ON HOW MUCH, IN
# OPPOSITE DIRECTIONS: A's optimum is K=6, B's is K=16, and they straddle
# the shipped 8. The basin is flat enough that the disagreement costs
# almost nothing — running 8.0 rather than each test's own optimum is
# +0.04% MSE on A and +0.37% on B. A cluster bootstrap over PLAYERS (4000
# resamples, whole players resampled because one player's matches are not
# independent of each other) puts 90% of A's argmin between K=1 and K=20,
# and 90% of B's between K=5 and K=48. 8.0 sits comfortably inside both.
#
# SO THE NUMBER DOES NOT MOVE AND ITS DESCRIPTION DOES. This is no longer
# "a round number nobody checked"; it is a value this repo's own 729 real
# matches cannot distinguish from the out-of-sample optimum, which is a
# different and much weaker claim than "fitted to 8.0". What would actually
# move it is SPLITTING the two stages — A and B genuinely want different
# Ks and are forced to share one here — but that is a second constant
# rather than a better value for this one, and B's n=271 is far too thin
# to justify introducing it today. Revisit at a completed season.
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


# ---------------------------------------------------------------------------
# xG/xA — Tango/Lichtman/Dolphin's precision-weighted blend (The Book,
# ch.4's clutch-skill estimate), not another ad hoc shrink constant.
#
# WHY A NEW MECHANISM, NOT ANOTHER SHRINK_K STAGE: this repo's own measured
# check (2026-08-21) showed xG is a WORSE fit to any single season's actual
# fantasy points than raw goals+assists (r=0.275 vs 0.381) — unsurprising,
# since points reward the actual result, not the process behind it. But
# fit-to-outcome is the wrong test for whether xG is USEFUL. The right one
# is year-over-year STICKINESS — does the same player's rate this
# independent sample predict his rate in another one — because a stat that
# repeats for the same player is measuring skill, and a stat that doesn't
# is measuring luck no matter how well it explains any one outcome. Measured
# on this repo's own real data, same player across two seasons (n=85, last
# season 450+ minutes, this season 30+): goals/90 r=0.169, xG/90 r=0.222,
# G+A/90 r=0.306, xG+xA/90 r=0.303 — xG alone IS stickier than raw goals
# alone, folding in assists roughly closes the gap. That is the DIPS pattern
# (McCracken: strikeout/walk rate repeats far better than a pitcher's ERA)
# applied to this repo's own numbers rather than assumed from the
# literature.
#
# POSITION-GATED, MEASURED NOT ASSUMED: restricted to forwards/attacking
# mids (Understat's own "F" position tag) because that same real-data check
# showed the opposite sign for every other position (n=46, corr(xG+xA, this
# season's early points) = -0.169) — a defender's or goalkeeper's fantasy
# points come from clean sheets and defensive actions, which an attacking
# metric says nothing about. The defensive-side analogue (xGA — Understat's
# own team-level `getLeagueData`, already GET-accessible) is real and NOT
# yet captured; see the 2026-08-21 handoff.
#
# WIRED IN, WITH THE VARIANCE DERIVED FROM WHAT EXISTS TODAY RATHER THAN
# GUESSED OR LEFT UNTIL MORE JORNADAS ACCUMULATE. Waiting doesn't teach
# anything a live number can't already start teaching: this repo already
# has the mechanism to grade and refit a live parameter against reality as
# it arrives — ffcore.startprob.Calibration.fit()'s cache-on-fingerprint
# does exactly that for P(start), and _xg_stickiness_boost() below is built
# the same way, so it strengthens on its own as more paired seasons of
# Understat data accumulate rather than staying frozen at today's estimate.
#
# THE TWO NUMBERS A BLEND NEEDS, both fit from real data, neither hand-
# picked: _xg_points_fit() converts xG+xA/90 into THIS scoring system's
# points/match via ordinary least squares on last season's real (xG, ppm)
# pairs — a unit of chance created is worth nothing until it is translated
# into what THIS app actually pays for it. _xg_stickiness_boost() turns
# measured year-over-year correlation into pseudo-matches, using the same
# reliability = n/(n+K) relationship SHRINK_K itself already assumes: a
# stickier stat implies a smaller effective K, i.e. fewer of its own
# matches are needed before it is trusted at face value, so one xG-
# informed match is allowed to outweigh one raw match by the ratio of
# their implied Ks. THE APPLES-TO-APPLES COMPARISON IS G+A/90 vs xG+xA/90
# — both include assists — not goals alone against xG+xA, which would
# understate the raw side by leaving assists out of only one column; on
# that fair comparison the two are close to tied (measured 2026-08-21:
# r=0.306 vs r=0.303, boost≈0.99), a materially different, more honest
# number than comparing goals alone against xG+xA would give. Below a
# floor of paired players this refuses and returns boost=1.0 — an xG
# match trusted no more than a raw one — rather than trust a ratio
# measured on a handful of names.
#
# Scorer.rate() folds this in as a THIRD weighted term alongside the prior
# and the current season's raw returns — see the general precision-weighted
# blend there, which the ORIGINAL two-term shrink formula is a special
# case of (weight = pseudo-matches, K for the prior, real matches for the
# current season). Wiring in a THIRD source before it was calibrated
# against the walk-forward-beats-baseline bar the other two use would be
# assuming a variance and hiding it inside a fancier formula; deriving that
# variance from this repo's own measured numbers instead — thin as they
# are today — is what The Book's whole method is FOR, and it only gets
# better armed as more jornadas are captured, which happens automatically
# every run regardless of whether this is wired in or not.
# ---------------------------------------------------------------------------


def _precision_blend(estimates) -> tuple[float, float] | None:
    """(mean, variance) — independent estimates of ONE quantity, combined
    by inverse variance.

    `estimates` is [(mean, variance), ...]. An estimate with variance <= 0
    is skipped rather than trusted absolutely — 0 would claim infinite
    precision, which no real measurement here has. None back means nothing
    usable was offered, never a fabricated answer.

    THE BOOK'S OWN WORKED EXAMPLE (ch.4, clutch skill): a player's measured
    clutch skill is +.100 (wOBA) over 100 clutch PA, with sampling
    uncertainty .055; the population's own clutch-skill spread is .000 ±
    .006. Weighted 1/variance, that comes out to +.001 — almost entirely
    the prior, because 100 PA is a sliver of evidence next to the
    population's own tightly-known spread. Reproduced in this module's
    self-test.
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

    KEYED THE SAME WAY `history`/`current` ALREADY ARE — Scorer.rate()
    looks everything up by norm(rec["name"]), not by the crosswalk's own
    id, the same translation _current_from_perjornada() already does for
    exactly this reason (see its own docstring: "translate that back into
    norm(market name), because that is what Scorer.rate() actually looks
    self.current up by"). Keying this by the crosswalk id instead would
    silently miss every lookup rate() makes.

    See the module section above for why the position gate is measured
    rather than assumed.
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

    THE UNITS CONVERSION xG-implied output needs before it can join a
    points-per-match blend, fit fresh from this repo's own real data
    rather than assumed: a unit of xG+xA is only worth whatever THIS
    scoring system actually pays for the goals and assists it tends to
    produce, which nothing in the literature knows and this repo's own
    (data/season/points_2025-26.csv, data/tidy/understat_players.csv)
    pairing does. Below 10 paired players this refuses (slope 0.0,
    intercept 0.0) rather than fit a line through noise.
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

    NO CROSSWALK NEEDED — both seasons' rows already carry Understat's own
    understat_id, so the same-player pairing across seasons is exact
    without going through name resolution at all.

    THE BOOK'S OWN LOGIC (ch.2-4: hot streaks, clutch skill — a stat's
    reliability IS how much it repeats for the same player across
    independent samples): reliability relates to the shrinkage a stat
    needs the same way SHRINK_K already relates to this repo's own points
    blend, reliability = n/(n+K). Comparing the K implied by xG+xA/90's
    year-over-year correlation against the K implied by raw goals+assists/
    90's own — same players, same two seasons, this repo's own Understat
    capture — gives a real, self-updating ratio: how much sooner an xG-
    informed match earns trust than a raw one, on THIS repo's own numbers.
    GOALS+ASSISTS, NOT GOALS ALONE: `xg90` everywhere in this module is
    xG+xA, so its fair raw counterpart is G+A, not goals by itself — an
    earlier version of this compared goals alone against xG+xA, which
    understated the raw side by leaving assists out of only one column.

    SELF-CORRECTING, NOT FROZEN: recomputed from whatever
    understat_players.csv holds when called, so it strengthens on its own
    as more seasons or paired players accumulate — no cache, unlike
    ffcore.startprob.Calibration.fit(), because it costs microseconds
    against ~200 rows rather than cross-validating team sheets.

    Below 30 paired players (this repo's own real count as of
    2026-08-21: 85, well past this floor, but a fresh store could start
    thinner) this refuses and returns (1.0, why) — an xG match trusted no
    more than a raw one — rather than trust a ratio measured on a handful
    of names. Clipped to [0.5, 3.0]: a single noisy correlation swing
    should not let one xG match outweigh six raw ones, or count for half
    of one.
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

    THE JOIN points.py's own docstring called "model code to do later":
    starters.csv's minutes are keyed by match_id, which matches.csv's own
    rows translate to a jornada number directly; perjornada.csv's points
    now carry a `jornada` column of their own (points.match_jornadas(),
    stamped from when a match's score was first seen in the tidy store's
    history — the closest thing to a calendar this repo has without a
    kickoff-date feed in a matching id space). Both halves land on the
    same jornada axis, which is what makes them combinable at all.

    A jornada absent from `matches.csv` (unparsed, or the calendar hasn't
    been swept) or from a points row (no timeline, or nothing had finished
    yet when it was observed) is dropped from that side rather than
    guessed — a player is credited 0 for a jornada he is silent about, not
    the average of the ones he is not.
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

    # ANCHORED ON points_total, NOT SUMMED FROM points_delta — a real bug,
    # caught on real data: points.py's diff() never emits a row for the
    # very FIRST kept snapshot (nothing precedes it to diff against), so a
    # player who already had points on the board by then has that baseline
    # in no delta at all. Measured: Abde Rebbach's one row says
    # points_delta=7, points_total=11 — the missing 4 is whatever he had
    # before this file's own history starts. Summing deltas alone would
    # have under-rated him forever; points_total is the page's own
    # cumulative figure and carries no such gap.
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

    # THE UNIVERSE IS THE POINTS-PAGE'S OWN, not everyone starters.csv ever
    # names — checked on real data and deliberately narrower than a plain
    # union: 90 players who have real starters.csv minutes carry NO row on
    # the points page at all (verified: zero, not a join failure — nothing
    # under any spelling). Whether that silence means "scored exactly
    # zero" or "this page does not track him" is not knowable from here,
    # and this repo already has a name for guessing between two readings
    # of silence — NEUTRAL_START vs ABSENT_START exists for exactly this
    # question on the OTHER source. Including him at pts=0 would shrink a
    # possibly-real season toward zero on a guess; leaving him out keeps
    # him on last season's rate, which is what happened before this
    # function existed. Matches the pre-2026-08-21 universe exactly.
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

    The most recent jornada he has a row for weighs 1; one back weighs
    `decay`; two back `decay**2`; and so on — decay=1.0 is an exact flat
    sum, so this collapses to today's cumulative behaviour with no special
    case. `matches` is minutes/90, decayed the same way, so the ratio the
    caller divides by is a like-for-like recency-weighted rate, not a
    decayed numerator over an undecayed denominator.
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

    SAME per_jornada, SAME decay as _weighted_totals — one already-fitted
    recency weighting, applied a second time to a second question. Where
    that function's `wmatch` is a decayed NUMERATOR (minutes/90, to be
    divided by decayed points), this is a decayed RATE in its own right:
    Sigma(w * min(1, minutes/90)) / Sigma(w), a share of a jornada rather
    than a share of points, so it lives in [0, 1] and can stand in for a
    start probability directly. A jornada he is silent about (0 minutes,
    same "silence is not evidence of the average" rule as the points side)
    pulls the rate down; a jornada nobody has a row for yet does not enter
    the sum at all.
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
    the flat average out of sample, same discipline `ffcore.startprob.
    Calibration.fit()` already uses for P(start).

    WALK-FORWARD, NOT ARBITRARY LEAVE-ONE-OUT — deliberately not the
    pattern Calibration.fit() uses (holding out one team SHEET, order
    irrelevant, because sheets have no time-order that matters to what is
    being predicted). Jornadas do: predicting jornada 3 from jornadas 1
    and 5 is not a forecast, it is hindsight, and scoring it that way once
    handed a run of monotonically increasing jornadas a training set with
    FUTURE data on both sides of the point being "predicted" — the error
    looked identical for every decay candidate because the flat mean of
    two symmetric bracketing points already equals a linear trend's
    midpoint, so decay could never show an edge no matter how real the
    trend was. Here, jornada J is only ever predicted from jornadas
    STRICTLY BEFORE it, exactly like the live report does the week before
    a new one is played.

    THE TEST: for each player's jornadas in order, from the second onward,
    predict his per-match rate from everything strictly earlier (weighted
    by each decay candidate) and score against what he actually returned
    — points per match he was actually on the pitch for, so a jornada he
    did not feature in contributes nothing to either side rather than a
    phantom zero. Needs at least one player with 2+ distinct jornadas on
    record; with the whole sample on 1 (where this repo stands at the
    time of writing) there is nothing to walk forward through and this
    always returns decay=1.0 — the flat average, correctly, because there
    is no evidence recency weighting would help yet.
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

    WHY NOT data/season/points_<this season>.csv, WHICH load_points() ALSO
    LOOKS FOR: that file is a snapshot of the points PAGE, and the page
    reads empty until J1 is fully played — see this module's own opening
    docstring. data/season/live/perjornada_*.csv is written every run from
    snapshots this repo already takes (points.py) and has real numbers from
    the first confirmed match, so it is the actual live source, not
    points_*.csv's hypothetical future one.

    KEYED THROUGH THE CROSSWALK, TWICE — once to join perjornada's own
    `ff_id` (which IS the crosswalk's player_id directly, verified: 5 of 5
    checked matched exactly) to a canonical player, and again to translate
    that back into norm(market name), because that is what Scorer.rate()
    actually looks self.current up by. Going name-to-name directly (what
    the first version of this did) meant perjornada's own two name
    columns and starters.csv's short form were three different spellings
    of the same person, agreeing by luck on some players and silently
    giving others zero minutes on the rest.

    "pj" IS MINUTES, NOT AN APPEARANCE COUNT — games_total in the perjornada
    file weights a 10-minute cameo and a full 90 identically, the same
    distortion fixed here that a raw pts/games average already gets
    shrunk to correct for on the PRIOR side; this fixes it at the source
    for the season that is actually live. A player perjornada has a row
    for but starters.csv has never confirmed a line-up for gets 0 minutes,
    not a guess — silence about how long he played is not evidence he
    played the average amount.

    RECENCY-WEIGHTED NOW, NOT A FLAT SEASON AVERAGE — Step 1 of the
    2026-08-21 forecasting plan. Both `pts` and `pj` are rebuilt per
    jornada (`_per_jornada_current`) and combined with a decay
    (`_weighted_totals`) chosen the same way `ffcore.startprob.Calibration.
    fit()` chooses its parameters: used ONLY if it beats the flat average
    (decay=1.0) walking forward through real jornadas, one at a time
    (`_fit_decay` — deliberately NOT leave-one-out here; see its own
    docstring for why arbitrary leave-one-out let future jornadas leak
    into a "prediction" for an earlier one). With one jornada on record —
    where this repo stands at the time of writing — nobody has a second
    one to walk forward to, and this is a flat sum, identical to what
    this function returned before; verified byte-identical against the
    pre-change output on the real store. It starts weighting recent form
    the moment there is evidence that doing so helps, not before.
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

    PRIOR: the newest data/season/points_*.csv — last season's completed
    totals, written once a year by `ingest.py baseline` when the points page
    flips to a new season. CURRENT: this season's live per-jornada tracker,
    minutes-weighted — see _current_from_perjornada().

    The historical two-points-files shape (this season's own points_*.csv as
    "current", shrinking further into an even older prior) is kept below for
    the day `baseline` is run again at THIS season's actual close and a
    second such file exists; perjornada takes priority over it whenever it
    has data, being the fresher source while the season is still live.

    Two files, not one, on the PRIOR side specifically. The newest
    points_*.csv is what a naive read would call "this season"; reading only
    the newest — which is what report.py and rivals.py each did, in their
    own copy of this function, before this module existed — was a bug
    waiting for the season to roll over: the moment points_2026-27.csv
    appeared as a completed-season snapshot, every rating would have been
    rebuilt from that one file and the actual prior would have vanished.
    """
    from ffcore.tidy import SEASON, load_crosswalk, read_csv

    files = sorted(SEASON.glob("points_*.csv")) if SEASON.exists() else []
    xw = load_crosswalk()

    def read(path) -> dict:
        out: dict[str, dict] = {}
        for r in read_csv(path):
            rec = {"pts": ratio(r.get("points")) or 0.0,
                   "pj": ratio(r.get("games")) or 0.0}
            # THE ID FIRST, under the market's CURRENT name for him — the
            # same fix rosters_initial.txt and the current-season blend
            # already got: a display name a season is free to move on
            # from is not a stable key, and a completed prior-season
            # snapshot is exactly that shape, frozen at whatever names were
            # true when it was written. ff_id was missing from every
            # points_*.csv before 2026-08-21 (ingest.baseline's writer
            # dropped it though parse_points() already extracted it), so
            # this degrades to the name-only behaviour below for an older
            # file rather than losing rows written before the fix.
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

    ONE builder, because report.py and rivals.py must score with identical
    arithmetic — the whole reason this module was lifted out of report.py. They
    previously held a copy each of the points loader, and neither knew about
    the fixture board; a comparison between your squad and a rival's would
    have been between two different models.

    `calibrate` fits P(start) against confirmed line-ups (ffcore.startprob).
    It is the one thing here that reads the past to price the future, it costs
    a few seconds, and it turns itself off: with nothing played, or with a fit
    that loses on line-ups it has not seen, the source's own figure stands.
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

    Cached because the fit cross-validates over every team sheet on record and
    costs a few seconds, while `build` is called more than once in some runs
    and the answer cannot change between calls.

    THE CUT IS THE FIRST CONFIRMED LINE-UP WE SAW. Anything the source
    published after that may already be the team sheet rather than a forecast
    of it — the two live on the same page — and grading a forecast against
    itself is how a model marks its own homework.
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
    # ON DISK, KEYED BY WHAT IT WAS FITTED ON. The fit cross-validates over
    # every team sheet on record and costs six seconds — in EVERY process, and
    # the chain runs several. It cannot change unless the confirmed line-ups
    # do, so the answer is written down and the fingerprint is the evidence
    # that produced it. A changed fingerprint refits; nothing else does.
    #
    # METHOD_VERSION IS PART OF THAT EVIDENCE, not just the data. Real bug,
    # caught before it shipped: the fingerprint used to be data-only, so
    # Step 4 changing what fit() optimises (binary played/didn't -> minutes-
    # graded) touched no confirmed line-up and no cut, and the stale
    # binary-fitted coefficients would have kept being read off disk forever.
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


def formations(premium: bool = False) -> list[tuple]:
    """Legal shapes. Rivals may hold a premium subscription even if you
    don't, so this is a parameter rather than a module constant."""
    return FREE_FORMATIONS + (PREMIUM_FORMATIONS if premium else [])


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

        # THE SAME KEY THE MARKET INDEX USES. Keyed on norm(name) alone this
        # held one row for the two Álvaro Garcías — so a squad that correctly
        # named the Rayo one scored a blank, because the only row filed under
        # that name was the Villarreal one.
        from ffcore.tidy import row_key, shared_names, load_crosswalk

        shared = shared_names(market)
        self.lookup: dict[str, dict] = {}
        # name -> the market keys answering to it, so the probable-XI feed
        # can fall back to a name when it has no slug — but only when the
        # name names one man.
        self._name_keys: dict[str, list] = {}
        for r in market:
            if r.get("name"):
                k = row_key(r, shared)
                self.lookup[k] = r
                # DISTINCT keys. `market` is sometimes every snapshot ever,
                # so appending blindly filed one player's key once per
                # reading and the "does this name name one man" test could
                # never be true — which silently dropped the second source
                # for everyone whose slug is not in the crosswalk.
                seen_for = self._name_keys.setdefault(norm(r.get("name")), [])
                if k not in seen_for:
                    seen_for.append(k)
        # ff_slug -> market key. THE TEAM PAGES DO PUBLISH PLAYER LINKS —
        # /jugadores/<slug>, 153 of them on one page — and the comment below
        # used to say they did not, so the one identifier both files share
        # went unused and the join ran on names. By slug 497 of 512 XI rows
        # reach a player and none is ambiguous; by name 494 do and three name
        # two men.
        xw = load_crosswalk()
        self._by_ff_slug = {norm(p.ff_slug): p.player_id
                            for p in (xw.players.values() if xw else ())
                            if p.ff_slug}

        self.cal = cal or Calibration()
        self.second: dict[str, dict] = {}
        for r in second or []:
            # By identifier, like everything else. Keyed by name while
            # score() looked players up by id, EVERY player silently lost
            # the second source and every calibrated P(start) moved with it
            # — which is what a key that only half-migrated looks like.
            # These rows carry the same name-slug the probable-XI pages do:
            # 247 of 274 reach the crosswalk's ff_slug, none reach af_slug.
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

        # Scaling by P(start) prices a non-start at zero, which is only right
        # if a player who doesn't play cannot be covered from the bench. The
        # free tier has no auto-substitution (verified in-app, 2026-08-16,
        # issue #28), so it is right: a benched starter costs his whole score,
        # not the gap to a replacement, and rotation risk is as dear as this
        # says. If auto-subs ever arrive, this multiplication is the line to
        # change.
        # THE SOURCE'S FIGURE IS NOT A PROBABILITY UNTIL IT HAS BEEN GRADED.
        # `raw` is what the page says, or the fallback for a man it does not
        # cover; `pct_used` is what that has been WORTH against confirmed
        # line-ups, blended with the second source where it has an opinion.
        # Until a jornada has been played the calibration is the identity and
        # these are the same number — see ffcore.startprob, which reports which
        # of the two is in force rather than leaving it to be assumed.
        raw = pct if pct is not None else (
            NEUTRAL_START if on_page else ABSENT_START)
        pct_used = 100.0 * self.cal.p(raw, self.second.get(key))
        # BLENDED AGAINST WHAT HE HAS ACTUALLY DONE THIS SEASON, the same
        # k-shrink stage rate() already runs on the POINTS side (self.current
        # is that stage's own dict, "start_rate"/"start_n" its participation
        # half — see _weighted_start). Editorial P(start) is real news
        # (this week's team talk, a fresh knock) that minutes history
        # cannot know about, so it stays the WHOLE answer until a player has
        # actually featured; once he has, real recent minutes pull the
        # number toward what is happening rather than waiting on the page
        # to catch up. Keyed by norm(name), same as rate()'s own lookup —
        # not the row_key `key` above, which self.current was never built
        # against.
        cur = self.current.get(norm(rec.get("name", "")))
        start_n = cur.get("start_n", 0.0) if cur else 0.0
        # `pct_used` ABOVE ANSWERS FOR ONE JORNADA — the next one, which is
        # the only match this week's editorial page and status flag (a
        # suspension, a knock, a doubt) actually describe. Bootstrap.
        # __init__ used to be handed this SAME number for the whole
        # remaining season (decide.load() reused one `base` dict for every
        # jornada) — so a player suspended for one match read as ~unlikely
        # to start for the other thirty-seven too, and a player rested for
        # one week never recovered in the forecast. On 2026-08-25 that
        # priced a first-choice centre-back (91.7% of this season's
        # minutes, one card suspension) at 27% for the rest of his season
        # and had decide.dead_weight() list him as sellable for zero points.
        #
        # `pct_rest` is what jornadas AFTER the next one get instead: his
        # own recency-weighted minutes share this season, shrunk toward
        # NEUTRAL_START (not toward this week's status-tainted editorial
        # reading — see NEUTRAL_START's own note, "no percentage given" is
        # exactly the "no news either way" case this wants) by the same
        # SHRINK_K this repo already trusts for the points side. Nothing to
        # shrink AGAINST but this week's own number when he has no current-
        # season minutes at all (start_n == 0) — a debutant's forecast
        # cannot know more about jornada 10 than it does about jornada 3.
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
        # A CLEAN SHEET IS OPPONENT-ATTACK-DRIVEN, A GOAL OPPONENT-DEFENSE-
        # DRIVEN — the whole reason Match carries two factors instead of
        # one. A slot this repo does not recognise (should not happen; SLOT
        # covers every position the market publishes) gets the attacking
        # number rather than crashing, since attacking is the larger group.
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
# replacement level — the baseline that does not move when your eleven does
#
# Pricing a player by what YOUR eleven loses without him answers one question
# — does he play Saturday — and is wrong for every other, because the answer
# changes as you act: sell one midfielder and every other midfielder's number
# is stale, so the ranking is not a list of decisions.
#
# So: a fixed baseline set by the rules (value-based drafting's answer). Value
# is what a player is worth ABOVE THE LEVEL THE MARKET SUPPLIES FREE at his
# position — the rung where the league runs out of starters. Five managers
# starting four defenders each makes the 20th-best defender replaceable by
# anyone, and nothing above that depends on your own eleven.
#
# Not the positional AVERAGE, a far higher bar that would price most of the
# league negative and hide real scarcity; not value-weighted, which drags the
# bar toward whoever is expensive.
#
# The rung is a MEAN over legal shapes, because 3-4-3 and 5-4-1 start
# different numbers of defenders — that keeps the eleven adding to eleven
# while letting a position only some formations use price as scarcer.
# ---------------------------------------------------------------------------



def pick_xi(pool: dict, force: dict | None = None, premium: bool = False):
    """Best legal XI by total score, or None if no legal shape fits.

    force pins one player into his slot. Exact, not heuristic: the only
    coupling between players is the per-slot count, so top-N per slot within
    each legal shape is optimal.

    Returns (total, (d, m, f), picked). A None return for a rival squad is
    itself a finding — it means they cannot field a legal XI today.
    """
    best = None
    for d, m, f in formations(premium):
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
