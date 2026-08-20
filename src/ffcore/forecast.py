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
reliably a differential punt pays off. The interface needs no change for a
model that wants that correlation, because it is handed the whole round at
once — that is the one design decision here that would be expensive to get
wrong.

PARTIALLY DONE, HONESTLY: rate_draw()'s SEASON-LONG multiplier now has a
per-club shared component (club_rel, from ffcore.fixture.club_volatility) on
top of each player's own individual uncertainty — two players of the same
club move together across a WHOLE TRIAL's 38 rounds. What is still
independent is the PER-MATCH noise inside draw() itself: `rng.choice(pool)`
draws each player's individual-match luck separately, so two teammates can
still land on opposite ends of the pool in the SAME round of the SAME trial.
The season-long piece is the bigger of the two — a club's whole SEASON
being stronger or weaker than expected compounds over 38 rounds where a
single round's noise does not — but per-match correlation is real and not
yet built.

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
        # {player key: club}, and {club: rel} — ffcore.fixture.club_volatility().
        # ADDED to rate_rel's own individual uncertainty, not netted against
        # it — see rate_draw()'s docstring for why.
        self.club_of = dict(club_of or {})
        self.club_rel = dict(club_rel or {})

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

        TWO INDEPENDENT SOURCES OF "WRONG", COMBINED MULTIPLICATIVELY: this
        player's own rate could be off (rate_rel, as before), and separately
        his WHOLE CLUB could be having a stronger or weaker season than its
        own attack_defense() rating expects (club_rel — see
        ffcore.fixture.club_volatility). The second is what makes two
        players of the same club move together in a trial instead of
        independently, which is most of why sampling a squad concentrated
        in a few clubs used to understate its own variance — see this
        module's own opening docstring, "the baseline below ignores that
        correlation". A player with no club_of entry (club_rel empty, or a
        club too thin on history — see MIN_AD_MATCHES) draws exactly as
        before: shared=1.0 is a no-op, not a guess.

        CLUB SHOCKS DRAWN FIRST, sorted, always — same reason players draw
        in a fixed order (see __init__): which rng call lands on which key
        decides its value, and a dict's own iteration order is not
        something two processes are guaranteed to agree on.

        Truncated at zero: a rate is points per match and cannot be negative.
        """
        club_shock = {c: max(0.0, 1.0 + rng.gauss(0.0, self.club_rel[c]))
                     for c in sorted(self.club_rel)}
        out = {}
        for k in sorted(self.rate_rel):
            individual = max(0.0, 1.0 + rng.gauss(0.0, self.rate_rel[k]))
            shared = club_shock.get(self.club_of.get(k, ""), 1.0)
            out[k] = individual * shared
        return out

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

    # -- club_rel: two players of one club move TOGETHER --------------------
    # No club_of/club_rel passed: exactly the old behaviour, not a new
    # no-op path that happens to look the same.
    assert thin.club_of == {} and thin.club_rel == {}

    same_club = Bootstrap(
        {1: {"a": (5.0, 1.0), "b": (5.0, 1.0), "c": (5.0, 1.0)}},
        pool=[0, 2, 4, 6, 8] * 40, matches={"a": 20, "b": 20, "c": 20},
        club_of={"a": "Rich", "b": "Rich", "c": "Poor"},
        club_rel={"Rich": 0.20, "Poor": 0.0})
    # "a" and "b" share Rich's shock; "c" is on Poor, rel 0.0 — his OWN
    # rate_rel still applies (players still individually wrong), but the
    # CLUB component is exactly a no-op multiplier for him.
    trials = [same_club.rate_draw(random.Random(i)) for i in range(500)]
    # THE ACTUAL CLAIM: a and b are CORRELATED (same club, same shock) far
    # more than either is with c (different club). Measured as how often
    # a and b land on the SAME SIDE of 1.0, against how often a and c do.
    ab_same_side = sum(1 for t in trials
                       if (t["a"] > 1.0) == (t["b"] > 1.0))
    ac_same_side = sum(1 for t in trials
                       if (t["a"] > 1.0) == (t["c"] > 1.0))
    assert ab_same_side > ac_same_side, (ab_same_side, ac_same_side)
    # a and b are NOT identical — each still carries his own individual
    # rate_rel on top of the shared club shock.
    assert any(t["a"] != t["b"] for t in trials)
    # The mean is still ~1.0 — an ADDED source of spread, not a bias.
    a_mean = sum(t["a"] for t in trials) / len(trials)
    assert abs(a_mean - 1.0) < 0.05, a_mean

    # REPRODUCIBLE, THE SAME STRONGER CLAIM AS draw() BELOW: fixed order
    # (sorted club names, then sorted player keys), not dict-insertion or
    # per-process hash-seed order — the exact bug class .draw() already had
    # to be fixed for once.
    fwd_clubs = {"a": "Rich", "b": "Rich", "c": "Poor"}
    rev_clubs = {"c": "Poor", "b": "Rich", "a": "Rich"}
    fwd_cs = Bootstrap({1: {"a": (5.0, 1.0), "b": (5.0, 1.0), "c": (5.0, 1.0)}},
                       matches={"a": 20, "b": 20, "c": 20}, club_of=fwd_clubs,
                       club_rel={"Rich": 0.2, "Poor": 0.1})
    rev_cs = Bootstrap({1: {"a": (5.0, 1.0), "b": (5.0, 1.0), "c": (5.0, 1.0)}},
                       matches={"a": 20, "b": 20, "c": 20}, club_of=rev_clubs,
                       club_rel={"Poor": 0.1, "Rich": 0.2})
    assert fwd_cs.rate_draw(random.Random(9)) == rev_cs.rate_draw(
        random.Random(9)), "insertion order must not change the season"

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

    print("ffcore.forecast self-test OK (28 cases)")


if __name__ == "__main__":
    _selftest()
