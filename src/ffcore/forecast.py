"""
ffcore.forecast — what a player might score, as a DISTRIBUTION.

    fc = Bootstrap.load(players, pool)
    fc.expected(jornada)        -> {key: mean points}
    fc.draw(jornada, rng)       -> {key: one sampled outcome}

THE INTERFACE IS THE POINT. Everything above this module — the simulator, the
standings, every decision — consumes `expected` and `draw` and knows nothing
else. Replacing the model is replacing one class. That matters because the
point estimate is the binding constraint on this whole system: measured on 386
logged scores the forecast spread is sd 1.07, while a single match has sd 3.69
around a mean of 3.44. The model will be wrong for a long time, so the design
question is not "is it right" but "can it be swapped".

`draw` RETURNS A WHOLE JORNADA, not one player, and that is deliberate. Two
defenders from the same club share a clean sheet; two forwards share the goals
they are competing for. Sampling players independently understates the
variance of a squad that is concentrated in a few clubs, and overstates how
reliably a differential punt pays off. The baseline below ignores that
correlation — but a model that wants it needs no interface change, because it
is handed the whole round at once. That is the one design decision here that
would be expensive to get wrong.

WHY NOT A NORMAL. The obvious sampler is mean plus sd times a standard normal,
and every fantasy Monte Carlo write-up I could find does exactly that. Real
per-match scores are mostly 0-2 with an occasional 16, floored near zero and
sharply right-skewed: of 64 observed matches, 34 scored 3 or less and one
scored 16. A normal fitted to that mean and sd produces negative scores about
a fifth of the time and never produces the tail that actually decides a
league. Shape comes from the data instead.
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


@runtime_checkable
class Forecaster(Protocol):
    """What the simulator needs, and the whole of it."""

    def expected(self, jornada: int) -> dict[str, float]:
        """Mean points per player for that jornada. Cheap; used for ranking
        and for the XI a manager would pick, which must be chosen on what is
        knowable rather than on the sampled outcome."""

    def draw(self, jornada: int, rng: random.Random) -> dict[str, float]:
        """One sampled outcome for every player in that jornada."""


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
                 pool=(), matches=None):
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
        # HOW WRONG THE RATE ITSELF CAN BE. Everything above draws a season
        # around a rate taken as given, and a rate is an average of a handful
        # of matches: 34 of them for a regular, four for a man who came up in
        # January. The two are not the same claim and were being simulated as
        # if they were, which is most of why a 74% chance of winning could sit
        # under a table showing third place.
        #
        # sd of a mean over n matches is the per-match sd over root n; the
        # per-match sd scales with the player's own level (the draw is
        # multiplicative), so as a FRACTION of his rate it is the pool's
        # coefficient of variation over root n. n counts the shrinkage's
        # pseudo-matches, because a shrunk rate really is anchored by them.
        self._cv = (sd / self._pool_mean) if self._pool_mean else 0.0
        self.rate_rel = {}
        for k, n in (matches or {}).items():
            self.rate_rel[k] = self._cv / math.sqrt(
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

    def rate_draw(self, rng: random.Random) -> dict[str, float]:
        """One multiplier per player, for one whole SEASON.

        The rate is estimated once and is then wrong in the same direction for
        every jornada of a trial — that is what makes it different from
        match-to-match noise, and why it cannot be averaged away over 38
        rounds. Drawn per trial, never per jornada.

        Truncated at zero: a rate is points per match and cannot be negative.
        """
        return {k: max(0.0, 1.0 + rng.gauss(0.0, rel))
                for k, rel in self.rate_rel.items()}

    def draw(self, jornada: int, rng: random.Random,
             rates: dict | None = None) -> dict[str, float]:
        per = self.per_jornada.get(jornada, {})
        out = {}
        for k in self._order.get(jornada, ()):
            pts, p = per[k]
            if p <= 0.0 or rng.random() >= p:
                out[k] = 0.0
                continue
            m = 1.0 if rates is None else rates.get(k, 1.0)
            out[k] = rng.choice(self.pool) * (pts * m / self._pool_mean)
        return out


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

    # The multiplier is drawn once per SEASON and averages one, so the mean of
    # the forecast is untouched and only its spread moves.
    r = random.Random(3)
    draws = [thin.rate_draw(r)["kid"] for _ in range(4000)]
    assert abs(sum(draws) / len(draws) - 1.0) < 0.02, sum(draws) / len(draws)
    assert min(draws) >= 0.0, "a rate cannot be negative"
    assert max(draws) > 1.3, "and it has to be able to be wrong"

    rng = random.Random(7)
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

    # A man who cannot play scores nothing, every single time.
    assert all(fc.draw(1, rng)["out"] == 0.0 for _ in range(200))

    # draw() converges on expected(). 4000 draws of a sd~mean variable puts
    # the standard error near 1.6%, so 8% is loose enough not to flake and
    # tight enough to catch a scaling error.
    n = 4000
    tot = {"nailed": 0.0, "rota": 0.0}
    for _ in range(n):
        d = fc.draw(1, rng)
        for k in tot:
            tot[k] += d[k]
    for k, want in (("nailed", 5.0), ("rota", 2.5)):
        got = tot[k] / n
        assert abs(got - want) / want < 0.08, (k, got, want)

    # THE SHAPE SURVIVES. A normal would be symmetric and would go negative
    # about a fifth of the time; the real thing is skewed right and floored.
    draws = [fc.draw(1, rng)["nailed"] for _ in range(4000)]
    below = sum(1 for d in draws if d < 0)
    assert below / len(draws) < 0.06, below / len(draws)
    med = statistics.median(draws)
    assert med < statistics.mean(draws), (med, statistics.mean(draws))
    assert max(draws) > 3 * statistics.mean(draws), max(draws)

    # Reproducible: the same seed is the same season, which is what makes a
    # comparison between two candidate squads a comparison and not a coin.
    a = Bootstrap({1: {"x": (4.0, 0.7)}}).draw(1, random.Random(1))
    b = Bootstrap({1: {"x": (4.0, 0.7)}}).draw(1, random.Random(1))
    assert a == b, (a, b)

    # ...AND ACROSS PROCESSES, which is a stronger claim and the one that was
    # not true. Every player pulls from one rng in dict order, so the SAME
    # players inserted in a different order get each other's numbers. The
    # callers build that dict by iterating a set, and set order over strings
    # moves with Python's per-process hash seed — so the headline P(win) in
    # the report drifted a point or two between runs on identical data, which
    # is noise a reader has no way to tell from news.
    same = {"x": (4.0, 1.0), "y": (2.0, 1.0), "z": (3.0, 1.0)}
    fwd = Bootstrap({1: {k: same[k] for k in ("x", "y", "z")}})
    rev = Bootstrap({1: {k: same[k] for k in ("z", "y", "x")}})
    assert fwd.draw(1, random.Random(3)) == rev.draw(1, random.Random(3)), \
        "the same season must not depend on dict insertion order"

    # -- the pool ----------------------------------------------------------
    assert "seed prior" in Bootstrap({}, pool=[1, 2, 3]).pool_note()
    big = list(range(MIN_POOL))
    assert "observed matches" in Bootstrap({}, pool=big).pool_note()
    # A pool that averages zero must not divide by zero or zero every draw.
    z = Bootstrap({1: {"x": (4.0, 1.0)}}, pool=[0] * MIN_POOL)
    assert z.draw(1, rng)["x"] == 0.0

    rows = [{"games_delta": "1", "points_delta": "4"},
            {"games_delta": "2", "points_delta": "9"},     # two matches
            {"games_delta": "1", "points_delta": "-1"},
            {"games_delta": "x", "points_delta": "3"}]     # unparseable
    assert pool_from_perjornada(rows) == [4, -1]

    print("ffcore.forecast self-test OK (19 cases)")


if __name__ == "__main__":
    _selftest()
