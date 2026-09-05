"""
ffcore.market — what the app is likely to offer you next, and what it is worth.

    m = Offers.fit(pool_values, observed_values)
    m.draw(rng)                  -> the keys of one cycle's offers
    m.best_over(days, gain, rng) -> distribution of the best upgrade you'd see

Every move the simulation ranks is scored against doing nothing for the rest
of the season, which is not the real alternative — waiting a few days for a
better offer is. `Offers` models that market: sampled weighted by value (not
uniformly — real cycles deal players ~5.5x the unowned pool's median), fitted
against observed offers rather than assumed. What it cannot see (a rival
buying the man you're waiting for, a pool/values that don't stand still) all
bias the same direction, toward making waiting look better than it is — the
report always prints the band and sample size beside the number as the
counterweight.

Why "spend now or later" is the right question, why value-weighted rather
than uniform, and what the model can't see: docs/notes/market.md.
"""

from __future__ import annotations

import math
import random
import statistics

try:                                     # numpy is the fast path, not the only one
    import numpy as np
except ImportError:                      # pragma: no cover
    np = None

__all__ = ["Offers", "quantiles"]

# Exponents tried when fitting. Below this the market looks uniform and above
# it every cycle deals the most expensive player alive; the answer on real
# data is near 0.3, and a grid is auditable where a solver's answer is not.
EXPONENTS = [round(0.05 * i, 2) for i in range(21)]      # 0.00 .. 1.00

# Quantiles the fit is judged on. The median alone would accept a sampler that
# gets the middle right and the tail wrong, and it is the tail that decides
# whether waiting is worth it.
FIT_AT = (0.25, 0.5, 0.75)


def quantiles(values, ps=FIT_AT) -> tuple:
    v = sorted(values)
    if not v:
        return tuple(0.0 for _ in ps)
    return tuple(v[min(len(v) - 1, max(0, int(p * (len(v) - 1))))] for p in ps)


class Offers:
    """How the app picks who to put on the market, fitted from what it did."""

    def __init__(self, pool: dict, per_cycle: int = 12, exponent: float = 0.0,
                 n_observed: int = 0, cycles: int = 0, runner: tuple = ()):
        # {player key: market value}
        self.pool = dict(pool)
        self.per_cycle = max(1, per_cycle)
        self.exponent = exponent
        self.n_observed = n_observed
        self.cycles = cycles
        # (exponent, how much worse it fits, as a fraction) for the second
        # best exponent on the grid. HOW SHARP THE FIT IS, reported rather
        # than assumed: an argmin says nothing about whether the runner-up
        # was a whisker behind or nowhere near, and here it is a whisker.
        self.runner = runner
        self._keys = list(self.pool)
        self._w = [max(0.0, v) ** exponent for v in self.pool.values()]
        if not any(self._w):
            self._w = [1.0] * len(self._keys)
        self._at = {k: i for i, k in enumerate(self._keys)}
        self._wa = np.asarray(self._w, dtype=float) if np is not None else None

    # -- fitting -----------------------------------------------------------
    @classmethod
    def fit(cls, pool: dict, observed: list, per_cycle: int = 12,
            cycles: int = 0, trials: int = 200, seed: int = 0) -> "Offers":
        """Pick the exponent whose draws look like the offers actually seen.

        Fitted on quantiles rather than on a mean: the pool is enormously
        skewed — a hundred players under a million and one at 128M — so a mean
        is a statement about the tail and nothing else.

        With nothing observed the exponent stays at zero and the sampler is
        uniform, which is the honest default: no evidence, no preference.
        """
        if not pool or not observed:
            return cls(pool, per_cycle, 0.0, len(observed or []), cycles)
        want = quantiles(observed)
        rng = random.Random(seed)
        nprng = np.random.default_rng(seed) if np is not None else None
        scored: list = []
        for e in EXPONENTS:
            trial = cls(pool, per_cycle, e)
            idx = trial._draw_np(nprng, trials) if nprng is not None else None
            if idx is None:
                got = []
                for _ in range(trials):
                    got += [trial.pool[k] for k in trial.draw(rng)]
            else:
                got = np.asarray(list(trial.pool.values()),
                                 dtype=float)[idx].ravel()
            q = quantiles(got)
            # Relative error, because the quantiles span two orders of
            # magnitude and an absolute one would fit the top and ignore the
            # rest.
            err = sum(abs(a - b) / max(1.0, b) for a, b in zip(q, want))
            scored.append((err, e))
        scored.sort()
        best, arg = scored[0]
        runner = ()
        if len(scored) > 1 and best > 0:
            runner = (scored[1][1], scored[1][0] / best - 1.0)
        return cls(pool, per_cycle, arg, len(observed), cycles, runner)

    def note(self) -> str:
        if not self.n_observed:
            return ("the market is modelled as a uniform draw — no cycle has "
                    "been recorded yet to fit anything against")
        out = ("the market is modelled from %d offers over %d cycles, "
               "weighted by value^%.2f" % (self.n_observed, self.cycles,
                                           self.exponent))
        if self.runner:
            out += (", and only just — value^%.2f fits within %.1f%% of it, "
                    "so read the exponent as roughly this, not exactly this"
                    % (self.runner[0], self.runner[1] * 100.0))
        return out

    # -- use ---------------------------------------------------------------
    def draw(self, rng: random.Random) -> list:
        """One cycle's offers. Without replacement: the app deals a dozen
        different players, not a dozen draws that may repeat."""
        return [self._keys[i] for i in self._draw_idx(rng)]

    def _draw_idx(self, rng: random.Random) -> list:
        """One cycle, as positions. The scalar path, kept as the reference the
        vectorised one is checked against and as the answer when numpy is not
        installed."""
        n = min(self.per_cycle, len(self._keys))
        at, w, out = list(range(len(self._keys))), list(self._w), []
        for _ in range(n):
            pick = rng.choices(range(len(at)), weights=w, k=1)[0]
            out.append(at.pop(pick))
            w.pop(pick)
        return out

    def _draw_np(self, rng, cycles: int):
        """`cycles` cycles' worth of offers at once, as a (cycles, n) index
        matrix. None when numpy is missing.

        Weighted sampling WITHOUT replacement, vectorised by the exponential
        race (Efraimidis-Spirakis) — the same distribution as `_draw_idx()`'s
        loop, not an approximation of it (checked in the self-test). Why this
        replaces the loop (a 10-of-14-second hot spot): docs/notes/market.md
        #draw-np-vectorization.
        """
        if np is None or not self._keys:
            return None
        n = min(self.per_cycle, len(self._keys))
        w = np.where(self._wa > 0, self._wa, np.finfo(float).tiny)
        clock = rng.exponential(size=(cycles, len(self._keys))) / w
        return np.argpartition(clock, n - 1, axis=1)[:, :n]

    def chance(self, key) -> float:
        """Probability this particular player is offered in one cycle.

        Unowned is not available — see docs/notes/market.md
        #unowned-is-not-available.
        """
        # A with-replacement approximation of a without-replacement draw;
        # negligible here (~2% drawn fraction). Why: docs/notes/market.md
        # #chance-with-replacement-approximation.
        total = sum(self._w)
        if not total or key not in self.pool:
            return 0.0
        return min(1.0, self.per_cycle * self._w[self._at[key]] / total)

    def median_wait(self, key) -> float | None:
        """Cycles until you would more likely than not have seen him offered.

        None when he is effectively never dealt — which is an answer, and a
        more useful one than a number in the hundreds.
        """
        p = self.chance(key)
        if p <= 0.0:
            return None
        if p >= 1.0:
            return 1.0
        wait = math.log(0.5) / math.log(1.0 - p)
        return wait if wait < 400 else None

    def best_over(self, cycles: int, gain, rng: random.Random,
                  trials: int = 400) -> list:
        """[best upgrade seen] over `cycles` cycles, one entry per trial.

        `gain(key)` is what owning him would add to your eleven. The answer is
        a DISTRIBUTION and must be reported as one: the median is what a
        typical wait buys and the upper decile is the reason to wait at all.
        """
        # gain() is asked about each player ONCE, not once per time he is
        # dealt. It was being called 33,600 times for 572 distinct answers.
        gains = [float(gain(k)) for k in self._keys]
        idx = self._draw_np(np.random.default_rng(rng.randrange(2 ** 32)),
                            trials * cycles) if np is not None else None
        if idx is not None:
            best = np.asarray(gains)[idx].max(axis=1)
            return list(best.reshape(trials, cycles).max(axis=1))

        out = []
        for _ in range(trials):
            best = 0.0
            for _ in range(cycles):
                for i in self._draw_idx(rng):
                    if gains[i] > best:
                        best = gains[i]
            out.append(best)
        return out


def _selftest() -> None:
    # -- the vectorised draw IS the scalar draw ----------------------------
    # The exponential race replaced a sequential weighted-without-replacement
    # loop, and "it is the same distribution" is a claim, not a comment. This
    # is what checks it: the same pool, the same exponent, selection rates
    # per player from each path. Spread over three orders of magnitude of
    # weight, so a path that quietly went proportional or uniform fails here.
    if np is not None:
        from collections import Counter

        pool = {"p%d" % i: float(v) for i, v in
                enumerate([1, 2, 3, 5, 8, 13, 21, 34, 55, 89])}
        m = Offers(pool, per_cycle=4, exponent=0.7)
        rng, n = random.Random(1), 20000
        scalar = Counter()
        for _ in range(n):
            scalar.update(m._draw_idx(rng))
        vec = Counter(m._draw_np(np.random.default_rng(7), n).ravel().tolist())
        for i in range(len(pool)):
            gap = abs(scalar[i] - vec[i]) / n
            assert gap < 0.015, (i, scalar[i] / n, vec[i] / n)

    # -- quantiles ---------------------------------------------------------
    assert quantiles([1, 2, 3, 4, 5], (0.5,)) == (3,)
    assert quantiles([], (0.5,)) == (0.0,)

    pool = {"cheap%d" % i: 1e6 for i in range(200)}
    pool.update({"dear%d" % i: 50e6 for i in range(20)})
    # HOW SHARP IS THE FIT? A fitted exponent with a runner-up a whisker
    # behind is a range wearing a point estimate's clothes, and on the live
    # pool it is exactly that: two players of 584 entering the pool moved the
    # argmin from ^0.30 to ^0.15 and the wait estimate from 9.8 to 16.3 days.
    # Not seed noise — the argmin is stable across seeds — a flat curve. So
    # the note says how close the next one came instead of asserting a
    # precision the 90 observed offers do not support.
    flat = Offers.fit({"a": 1e6, "b": 2e6, "c": 30e6}, [1e6, 2e6], cycles=1)
    assert flat.runner and 0.0 <= flat.runner[1], flat.runner
    assert "within" in flat.note() and "roughly" in flat.note(), flat.note()
    # Nothing observed, nothing fitted, nothing claimed.
    assert Offers.fit({"a": 1e6}, []).runner == ()
    assert "uniform draw" in Offers.fit({"a": 1e6}, []).note()

    rng = random.Random(4)

    # -- uniform is the honest default -------------------------------------
    # No evidence, no preference. A sampler that assumed the market favours
    # good players without having seen it do so would invent the very thing it
    # is supposed to measure.
    blind = Offers.fit(pool, [])
    assert blind.exponent == 0.0
    assert "uniform" in blind.note() and "no cycle" in blind.note()

    # -- and a fitted one leans the way the evidence does -------------------
    # Offers that are almost all expensive can only come from a weighted draw:
    # the pool is ten to one the other way.
    rich = Offers.fit(pool, [50e6] * 30 + [1e6] * 3, trials=60)
    assert rich.exponent > 0.0, rich.exponent
    assert "value^" in rich.note() and "33 offers" in rich.note()
    drawn = [pool[k] for k in rich.draw(rng)]
    assert statistics.mean(drawn) > 2e6, drawn

    # Offers that look like the pool leave the sampler alone.
    flat = Offers.fit(pool, [1e6] * 30 + [50e6] * 3, trials=60)
    assert flat.exponent <= rich.exponent, (flat.exponent, rich.exponent)

    # -- a cycle is a dozen DIFFERENT players ------------------------------
    got = blind.draw(rng)
    assert len(got) == len(set(got)) == 12, got
    # A pool smaller than a cycle deals what there is, and does not hang.
    assert len(Offers({"only": 1e6}, per_cycle=12).draw(rng)) == 1
    assert Offers({}, per_cycle=12).draw(rng) == []

    # -- what waiting is worth --------------------------------------------
    # THE POINT OF ALL OF IT. One man in the pool is worth having; the longer
    # you wait, the likelier you are to be offered him, and the answer is a
    # distribution rather than a number.
    def gain(k):
        return 5.0 if k == "dear0" else 0.0

    short = Offers(pool, per_cycle=12).best_over(1, gain, random.Random(1))
    long_ = Offers(pool, per_cycle=12).best_over(10, gain, random.Random(1))
    assert statistics.mean(long_) > statistics.mean(short), \
        "waiting longer must see more of the market"
    assert max(short) <= 5.0 and min(short) >= 0.0
    # Waiting for nothing is worth nothing, and says so rather than erroring.
    assert set(Offers(pool).best_over(3, lambda k: 0.0, rng)) == {0.0}

    # -- unowned is not available ------------------------------------------
    # The difference is most of a season. You cannot ask for a particular
    # player; you wait until the app happens to deal him.
    one = Offers({"a": 1e6, **{"x%d" % i: 1e6 for i in range(99)}},
                 per_cycle=10)
    assert abs(one.chance("a") - 0.1) < 1e-9, one.chance("a")
    # Ten in a hundred a cycle is a coin flip in about seven of them.
    w = one.median_wait("a")
    assert 6.0 < w < 7.5, w
    # A man not in the pool is never dealt, and says so rather than returning
    # a wait that reads as a schedule.
    assert one.chance("nobody") == 0.0
    assert one.median_wait("nobody") is None
    # A pool smaller than a cycle deals everyone every time.
    assert Offers({"a": 1e6}, per_cycle=10).chance("a") == 1.0
    assert Offers({"a": 1e6}, per_cycle=10).median_wait("a") == 1.0

    print("ffcore.market self-test OK (25 cases)")


if __name__ == "__main__":
    _selftest()
