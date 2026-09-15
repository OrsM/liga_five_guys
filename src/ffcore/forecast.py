"""
ffcore.forecast — what a player might score, as a DISTRIBUTION.

    fc = Bootstrap.load(players, pool)
    fc.expected(jornada)        -> {key: mean points}

Bootstrap's own public interface is just that point estimate — the
sampling that turns it into a season DISTRIBUTION lives in
ffcore.season._run_np, a numpy-vectorized reader of this class's
internal fields (per_jornada/pool/rate_rel/club_of/club_rel/start_rel),
not a caller of a draw() method. That split (removed 2026-09-16) used
to be a pure-Python sampler here plus a hand-synced numpy mirror there,
kept in sync by hand for a fallback (`numpy` missing) that
pyproject.toml's own hard dependency on numpy already rules out.
Why: docs/notes/forecast.md#draw-rate_draw-start_draw-removed-numpy-only-now

WHY NOT A NORMAL. The obvious sampler is mean plus sd times a standard normal,
and every fantasy Monte Carlo write-up I could find does exactly that. Real
per-match scores are mostly 0-2 with an occasional 16, floored near zero and
sharply right-skewed: of 64 observed matches, 34 scored 3 or less and one
scored 16. A normal fitted to that mean and sd produces negative scores about
a fifth of the time and never produces the tail that actually decides a
league. Shape comes from the data instead — see _run_np for where that
shape is actually sampled from now.
"""

from __future__ import annotations

import random
import math
import statistics
from typing import Protocol, runtime_checkable

__all__ = ["Forecaster", "Bootstrap", "SEED_POOL", "MIN_POOL"]

# Per-match scores observed in 2026-27 before there were enough to fit
# anything. A PRIOR ON SHAPE, not on level: the player's own mean sets the
# level and this only says what the spread around it looks like. Replaced
# outright once MIN_POOL real observations exist, and `pool_note()` reports
# which of the two is in use so it can never be mistaken for measurement.
SEED_POOL = (-1, -1, -1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1,
             1, 1, 1, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 3, 3, 3, 3, 3, 3, 3,
             4, 4, 4, 4, 4, 5, 5, 5, 6, 6, 6, 6, 8, 8, 8, 8, 9, 9, 10, 11,
             12, 13, 16)

# Below this the pooled shape is mostly the seed above, and saying so is more
# useful than pretending 30 matches is a distribution.
MIN_POOL = 200

# The shrinkage's pseudo-matches, the same 8 the scorer anchors a rate with
# (ffcore.score.SHRINK_K). Imported as a number rather than from score.py to
# keep this module free of that import; the self-test holds the two equal.
SHRINK_MATCHES = 8.0

# How much a rate can drift per jornada that passes, as a fraction of the
# player's own rate_rel. Module-level default, used until fit_drift_frac()
# below has real graded pairs at more than one horizon (report.py's score_h3)
# to fit against; refuses to guess otherwise.
# Why + the derivation, full sweep tables: docs/notes/forecast.md#drift_frac-calibration-history
DRIFT_FRAC = 1.0


def fit_drift_frac(h1_pairs, h3_pairs) -> tuple[float, str]:
    """(drift_frac, why) — fit DRIFT_FRAC from real, multi-horizon forecast
    error, or say honestly why not. Never a guess.

    THE DERIVATION. rate_draw()'s own model says the per-player log-error
    at horizon h has variance rate_rel[k]**2 * (1 + h * DRIFT_FRAC**2) —
    one unit from the FLAT per-trial error `eps0` (present at every
    horizon equally, rate_draw()'s own docstring), plus `h` independent
    accumulated drift steps, each contributing DRIFT_FRAC**2 more. Dividing
    each observed log-error by its own rate_rel[k] (the already-derived,
    evidence-based per-player uncertainty this repo already computes, see
    Bootstrap.__init__) puts every player on the SAME scale regardless of
    how much evidence he individually has, so the population's observed
    variance at h1 and h3 isolates DRIFT_FRAC directly:

        Var(z_h1) ~= 1 + 1 * DRIFT_FRAC**2
        Var(z_h3) ~= 1 + 3 * DRIFT_FRAC**2
        DRIFT_FRAC = sqrt(max(0, (Var(z_h3) - Var(z_h1)) / (3 - 1)))

    `h1_pairs`/`h3_pairs` are `[(predicted, actual, rate_rel), ...]` at
    each horizon — deliberately NOT dicts keyed by player, since a fit
    only needs the population's residuals, not identity; the caller
    (methodology.py, off squad_log.csv's `score`/`score_h3` columns and
    real outcomes) owns matching a player's own rate_rel to his own pair.

    "h1"/"h3" here means 1 and 3 ACCUMULATED DRIFT STEPS, matching
    rate_draw()'s own accounting: it advances `cum_var` once per entry of
    its `jornadas` argument, not once per calendar jornada elapsed — real
    usage always walks the remaining schedule consecutively (decide.py's
    `rem`), so the two coincide in production, but a caller feeding this
    from a gapped or resampled jornada list would silently under- or
    over-count steps and mis-scale the fit.

    HONEST REFUSAL, same discipline as ffcore.score._fit_decay(): too few
    pairs at either horizon, a non-positive predicted/actual ratio (can't
    take a log of it), or a negative variance difference (real drift
    cannot produce h3 being LESS variable than h1 — a negative reading
    here is noise at this sample size, not evidence DRIFT_FRAC should
    shrink) all keep the CURRENT module-level DRIFT_FRAC, with a stated
    reason, rather than silently emitting a number nobody asked to trust.
    Why: docs/notes/forecast.md#fit_drift_frac--the-derivation
    """
    def _z(pairs):
        out = []
        for predicted, actual, rel in pairs:
            if predicted <= 0 or actual <= 0 or rel <= 0:
                continue
            out.append(math.log(actual / predicted) / rel)
        return out

    z1, z3 = _z(h1_pairs), _z(h3_pairs)
    if len(z1) < 20 or len(z3) < 20:
        return DRIFT_FRAC, ("not enough graded pairs yet (h1=%d, h3=%d, "
                            "need >=20 each) — keeping %.2f"
                            % (len(z1), len(z3), DRIFT_FRAC))
    var1 = statistics.pvariance(z1)
    var3 = statistics.pvariance(z3)
    growth = (var3 - var1) / 2.0
    if growth <= 0:
        return DRIFT_FRAC, ("h3 wasn't more variable than h1 (%.3f vs "
                            "%.3f, rate_rel-normalised) — no evidence "
                            "DRIFT_FRAC should move from %.2f"
                            % (var3, var1, DRIFT_FRAC))
    fitted = math.sqrt(growth)
    return fitted, ("h1 var %.3f, h3 var %.3f (rate_rel-normalised, "
                   "n=%d/%d) -> drift_frac %.2f" % (var1, var3,
                                                    len(z1), len(z3), fitted))


@runtime_checkable
class Forecaster(Protocol):
    """What the simulator needs, and the whole of it."""

    def expected(self, jornada: int) -> dict[str, float]:
        """Mean points per player for that jornada. Cheap; used for ranking
        and for the XI a manager would pick, which must be chosen on what is
        knowable rather than on the sampled outcome."""

    def expected_own(self, first_jornada_of: dict[str, int]) -> dict[str, float]:
        """Mean points per player, each read at HIS OWN next jornada rather
        than one shared jornada for everyone — the right question for "who
        should I field right now" when the round in progress means players
        aren't all waiting on the same next match."""


class Bootstrap:
    """Per-player mean from the scorer; shape resampled from real matches.

    Two independent parts, because the game has two:

      * WHETHER HE PLAYS — Bernoulli(p_start). A benched player scores
        nothing, and that is most of the variance for a rotation player.
      * WHAT HE SCORES GIVEN HE PLAYS — a draw from the pooled distribution
        of real per-match scores, rescaled so its mean is his own.

    Rescaling multiplicatively keeps the skew: a player twice as good is
    modelled as the same shape stretched, which is closer to the truth than
    the same spread shifted. It also cannot produce a mean other than the one
    the scorer gave it, so improving the point estimate improves this
    immediately and nothing else has to change.
    """

    def __init__(self, per_jornada: dict[int, dict[str, tuple[float, float]]],
                 pool=(), matches=None, club_of=None, club_rel=None):
        # {jornada: {key: (points_if_he_plays, p_start)}}
        self.per_jornada = per_jornada
        # THE ORDER PLAYERS DRAW IN, fixed here rather than left to the dict.
        # One rng feeds the whole round, so the order the players come out in
        # decides which of them gets which number — and the callers build
        # these dicts by iterating a set, whose order over strings changes
        # with Python's per-process hash seed. Sorted once at construction,
        # the same data is the same season in every process, on every box.
        self._order = {j: sorted(d) for j, d in per_jornada.items()}
        real = [p for p in pool if p is not None]
        self._real_n = len(real)
        self.pool = tuple(real) if len(real) >= MIN_POOL else SEED_POOL
        mean = statistics.mean(self.pool) if self.pool else 1.0
        # Guard a degenerate pool rather than dividing by it: a pool that
        # averages zero would send every scaled draw to zero or to infinity.
        self._pool_mean = mean if abs(mean) > 1e-9 else 1.0
        sd = statistics.pstdev(self.pool) if len(self.pool) > 1 else 0.0
        # Uncertainty in the rate itself, not just the season drawn around it:
        # sd of a mean over n matches is per-match sd / sqrt(n); since the
        # draw is multiplicative, per-match sd scales with the player's own
        # level, so as a fraction of rate it's the pool's coefficient of
        # variation / sqrt(n). n counts the shrinkage's pseudo-matches.
        self._cv = (sd / self._pool_mean) if self._pool_mean else 0.0
        self.rate_rel = {}
        for k, n in (matches or {}).items():
            self.rate_rel[k] = self._cv / math.sqrt(
                max(1.0, float(n) + SHRINK_MATCHES))
        # {player key: club}, and {club: rel} — ffcore.fixture.club_volatility().
        # ADDED to rate_rel's own individual uncertainty, not netted against
        # it — see rate_draw()'s docstring for why.
        self.club_of = dict(club_of or {})
        self.club_rel = dict(club_rel or {})

        # Same idea as rate_rel but for P(start), which has no fixed-shape
        # rescaling like the points pool — it's a proportion, whose sampling
        # variance depends on p itself (tightest near 0/1, widest near 0.5).
        # sqrt((1-p)/p) is that shape expressed as odds; reproduces the usual
        # Bernoulli sqrt(p(1-p))/p coefficient of variation up to a constant
        # absorbed into SHRINK_MATCHES.
        p0 = {}
        for j in sorted(per_jornada):
            for k, (_pts, p) in per_jornada[j].items():
                p0.setdefault(k, p)
        self.start_rel = {}
        for k, n in (matches or {}).items():
            p = p0.get(k)
            if p is None or p <= 0.0 or p >= 1.0:
                self.start_rel[k] = 0.0
                continue
            self.start_rel[k] = math.sqrt((1.0 - p) / p) / math.sqrt(
                max(1.0, float(n) + SHRINK_MATCHES))

    # -- provenance --------------------------------------------------------
    def pool_note(self) -> str:
        """Which shape is in use, and on what. Printed, never inferred."""
        if self._real_n >= MIN_POOL:
            return "shape from %d observed matches" % self._real_n
        return ("shape from the seed prior (%d observed, %d needed)"
                % (self._real_n, MIN_POOL))

    # -- the interface -----------------------------------------------------
    def expected(self, jornada: int) -> dict[str, float]:
        return {k: pts * p
                for k, (pts, p) in self.per_jornada.get(jornada, {}).items()}

    def expected_own(self, first_jornada_of: dict[str, int]) -> dict[str, float]:
        out = {}
        for k, j in first_jornada_of.items():
            rec = self.per_jornada.get(j, {}).get(k)
            if rec:
                out[k] = rec[0] * rec[1]
        return out

    # No sampling methods here (draw()/rate_draw()/start_draw() removed
    # 2026-09-16): numpy is a hard dependency (pyproject.toml), so
    # ffcore.season._run_np is always the live path, and it reads
    # per_jornada/pool/rate_rel/club_of/club_rel/start_rel/_order/
    # _pool_mean directly as a hand-synced vectorized mirror rather than
    # calling these per-key methods 20M times. The pure-Python sampler
    # they fed (ffcore.season._run(), only reachable if numpy were
    # missing) is gone too. Why: docs/notes/forecast.md#draw-rate_draw-start_draw-removed-numpy-only-now


def pool_from_perjornada(rows) -> list[int]:
    """Per-match scores out of points.py's per-jornada diff.

    Only rows where the appearance count went up by exactly one: a row
    covering two matches is not one match's distribution, and averaging it in
    would quietly narrow the tail this exists to capture.
    """
    out = []
    for r in rows:
        try:
            if int(r.get("games_delta") or 0) == 1:
                out.append(int(r["points_delta"]))
        except (TypeError, ValueError):
            continue
    return out


def _selftest() -> None:
    # -- how wrong the RATE is, not just the match ---------------------------
    # THE VARIANCE THAT DOES NOT AVERAGE OUT. A season drawn around a fixed
    # rate says the rate is a fact; it is a mean of a handful of matches, and
    # being wrong about it is wrong in the same direction all 38 rounds. This
    # is what made a 74% chance of winning sit under a table showing third.
    from ffcore.score import SHRINK_K
    assert SHRINK_MATCHES == SHRINK_K, "one shrinkage, two modules"

    thin = Bootstrap({1: {"vet": (5.0, 1.0), "kid": (5.0, 1.0)}},
                     pool=[0, 2, 4, 6, 8] * 40, matches={"vet": 34, "kid": 0})
    # A rate off 34 matches is a firmer claim than one off none, and the
    # shrinkage's own pseudo-matches are what stop the second being infinite.
    assert thin.rate_rel["vet"] < thin.rate_rel["kid"], thin.rate_rel
    assert 0.05 < thin.rate_rel["vet"] < 0.25, thin.rate_rel
    # cv of that pool is sd/mean = 2.83/4 = 0.707; over sqrt(34+8) = 6.48.
    assert abs(thin.rate_rel["vet"] - 0.707 / 42 ** 0.5) < 0.01, thin.rate_rel
    assert abs(thin.rate_rel["kid"] - 0.707 / 8 ** 0.5) < 0.01, thin.rate_rel
    # No evidence count is no widening — the caller that passes nothing gets
    # exactly the behaviour there was before this existed.
    assert Bootstrap({1: {"vet": (5.0, 1.0)}}, pool=[1, 2, 3]).rate_rel == {}

    # -- club_of/club_rel: stored as given, no second computation -----------
    # No club_of/club_rel passed: exactly the old behaviour, not a new
    # no-op path that happens to look the same. The correlation these
    # actually produce in a season is verified where it matters — through
    # a real simulate() call — in ffcore.season's own self-test, not here:
    # this class no longer draws anything itself for _run_np to duplicate.
    assert thin.club_of == {} and thin.club_rel == {}
    same_club = Bootstrap(
        {1: {"a": (5.0, 1.0), "b": (5.0, 1.0), "c": (5.0, 1.0)}},
        pool=[0, 2, 4, 6, 8] * 40, matches={"a": 20, "b": 20, "c": 20},
        club_of={"a": "Rich", "b": "Rich", "c": "Poor"},
        club_rel={"Rich": 0.20, "Poor": 0.0})
    assert same_club.club_of == {"a": "Rich", "b": "Rich", "c": "Poor"}
    assert same_club.club_rel == {"Rich": 0.20, "Poor": 0.0}

    # fit_drift_frac(): recovers a known ground truth from a synthetic
    # log-normal walk built right here (not via a Bootstrap method — the
    # class no longer draws anything; this is the same generative process
    # ffcore.season._run_np now samples from).
    global DRIFT_FRAC
    truth = 0.6
    gen = Bootstrap({1: {"kid": (5.0, 1.0)}}, pool=[0, 2, 4, 6, 8] * 40,
                    matches={"kid": 10})
    rel = gen.rate_rel["kid"]
    rng2 = random.Random(11)

    def _walked(n_steps: int, drift: float) -> float:
        # CONSECUTIVE steps from jornada 1, matching real usage (decide.py
        # always walks the remaining schedule in order) — one step per
        # jornada, log-normal with the same mean-1 correction rate_draw()
        # used to apply by hand.
        walk = cum_var = 0.0
        step_var = (drift * rel) ** 2
        eps0 = max(0.0, 1.0 + rng2.gauss(0.0, rel))
        for _ in range(n_steps):
            walk += rng2.gauss(0.0, step_var ** 0.5)
            cum_var += step_var
        return eps0 * math.exp(walk - cum_var / 2.0)

    h1_pairs = [(1.0, _walked(1, truth), rel) for _ in range(4000)]
    h3_pairs = [(1.0, _walked(3, truth), rel) for _ in range(4000)]
    fitted, why = fit_drift_frac(h1_pairs, h3_pairs)
    # RECOVERS THE TRUTH — within sampling noise (n=4000), not exactly.
    assert abs(fitted - truth) < 0.08, (fitted, truth, why)
    assert "n=4000/4000" in why, why

    # HONEST REFUSAL: too few pairs keeps the CURRENT module constant and
    # says why, rather than fitting noise.
    fitted_thin, why_thin = fit_drift_frac(h1_pairs[:5], h3_pairs[:5])
    assert fitted_thin == DRIFT_FRAC, (fitted_thin, why_thin)
    assert "not enough" in why_thin, why_thin

    # HONEST REFUSAL: h3 genuinely no more variable than h1 (drift truly
    # near zero) must not be reported as evidence to SHRINK below the
    # current constant — it's silence, not a negative signal.
    flat_h1 = [(1.0, 1.0 + rng2.gauss(0.0, rel), rel) for _ in range(200)]
    flat_h3 = [(1.0, 1.0 + rng2.gauss(0.0, rel), rel) for _ in range(200)]
    fitted_flat, why_flat = fit_drift_frac(flat_h1, flat_h3)
    if fitted_flat == DRIFT_FRAC:
        assert "wasn't more variable" in why_flat, why_flat

    # A non-positive predicted/actual value is skipped, not a crash.
    fitted_bad, why_bad = fit_drift_frac(
        [(0.0, 1.0, 0.1)] * 30, [(1.0, -1.0, 0.1)] * 30)
    assert fitted_bad == DRIFT_FRAC and "not enough" in why_bad, why_bad

    # One jornada, three players: a nailed-on starter, a rotation risk, and
    # somebody who is not playing at all.
    fc = Bootstrap({1: {"nailed": (5.0, 1.0),
                        "rota": (5.0, 0.5),
                        "out": (5.0, 0.0)}})
    assert isinstance(fc, Forecaster)

    # expected() is mean points, which is the product of the two parts.
    e = fc.expected(1)
    assert e == {"nailed": 5.0, "rota": 2.5, "out": 0.0}, e
    assert fc.expected(99) == {}, "a jornada nobody plays is empty, not an error"

    # expected_own(): each player at HIS OWN jornada, not one shared jornada.
    fc2 = Bootstrap({1: {"early": (4.0, 1.0)}, 2: {"late": (6.0, 0.5)}})
    assert fc2.expected_own({"early": 1, "late": 2}) == {"early": 4.0,
                                                          "late": 3.0}
    # No record at that jornada for him: simply absent, not zero.
    assert fc2.expected_own({"early": 2}) == {}

    # -- the pool ------------------------------------------------------------
    assert "seed prior" in Bootstrap({}, pool=[1, 2, 3]).pool_note()
    big = list(range(MIN_POOL))
    assert "observed matches" in Bootstrap({}, pool=big).pool_note()
    # A pool that averages zero must leave _pool_mean guarded, not zero —
    # _run_np divides by this same field directly.
    z = Bootstrap({1: {"x": (4.0, 1.0)}}, pool=[0] * MIN_POOL)
    assert z._pool_mean != 0.0, z._pool_mean

    rows = [{"games_delta": "1", "points_delta": "4"},
            {"games_delta": "2", "points_delta": "9"},     # two matches
            {"games_delta": "1", "points_delta": "-1"},
            {"games_delta": "x", "points_delta": "3"}]     # unparseable
    assert pool_from_perjornada(rows) == [4, -1]

    # -- start_rel: the same fact as rate_rel, on p rather than the rate ----
    # "vet" starts 90% on 34 matches of evidence; "kid" starts 90% on none.
    sthin = Bootstrap({1: {"vet": (5.0, 0.9), "kid": (5.0, 0.9)}},
                      matches={"vet": 34, "kid": 0})
    assert sthin.start_rel["vet"] < sthin.start_rel["kid"], sthin.start_rel
    # A CERTAIN reading (p=0 or p=1) has no odds to be wrong about — the
    # guard exists so a genuinely-never-plays man does not blow up sqrt of
    # a negative or divide by zero.
    certain = Bootstrap({1: {"never": (5.0, 0.0), "always": (5.0, 1.0)}},
                        matches={"never": 10, "always": 10})
    assert certain.start_rel == {"never": 0.0, "always": 0.0}
    # No evidence handed in at all: no widening, same as rate_rel's own
    # no-matches behaviour above.
    assert Bootstrap({1: {"vet": (5.0, 0.9)}}).start_rel == {}

    print("ffcore.forecast self-test OK (24 cases)")


if __name__ == "__main__":
    _selftest()
