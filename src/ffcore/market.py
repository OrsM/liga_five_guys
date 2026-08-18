"""
ffcore.market — what the app is likely to offer you next, and what it is worth.

    m = Offers.fit(pool_values, observed_values)
    m.draw(rng)                  -> the keys of one cycle's offers
    m.best_over(days, gain, rng) -> distribution of the best upgrade you'd see

THE QUESTION THIS EXISTS FOR IS "SPEND NOW OR SPEND LATER". Every move the
simulation ranks is scored against doing nothing for the rest of the season,
which is not the alternative on offer: the alternative is doing something
better in a few days, with the balance intact, against a market that deals a
fresh dozen players every cycle. Waiting therefore scored exactly zero and any
move with a positive number beat it BY CONSTRUCTION — which is how a report
comes to recommend spending everything you have on the first thing that clears
a low bar.

THE MARKET IS NOT A RANDOM DRAW, and assuming it was would have been worse
than not modelling it at all. Measured across every cycle on record, the
players actually offered are about five and a half times more valuable than
the unowned pool they come from — median 9.58M against 1.72M — while the
POSITION mix is close to proportional. So the sampler is weighted by value,
with the exponent fitted to reproduce the observed quantiles rather than
chosen: uniform would offer you journeymen and flatter waiting, proportional
would offer you Raphinha every cycle and flatter it far more.

WHAT IT CANNOT DO. It has one league's worth of cycles behind it, it assumes
the pool and the values stand still, and it knows nothing about a rival buying
the man you were waiting for. Every one of those makes waiting look better
than it is, which is the opposite of the bias it was built to correct — so the
report prints the band and the sample size beside the number, always.
"""

from __future__ import annotations

import random
import statistics

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
                 n_observed: int = 0, cycles: int = 0):
        # {player key: market value}
        self.pool = dict(pool)
        self.per_cycle = max(1, per_cycle)
        self.exponent = exponent
        self.n_observed = n_observed
        self.cycles = cycles
        self._keys = list(self.pool)
        self._w = [max(0.0, v) ** exponent for v in self.pool.values()]
        if not any(self._w):
            self._w = [1.0] * len(self._keys)

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
        best, arg = None, 0.0
        for e in EXPONENTS:
            trial = cls(pool, per_cycle, e)
            got = []
            for _ in range(trials):
                got += [trial.pool[k] for k in trial.draw(rng)]
            q = quantiles(got)
            # Relative error, because the quantiles span two orders of
            # magnitude and an absolute one would fit the top and ignore the
            # rest.
            err = sum(abs(a - b) / max(1.0, b) for a, b in zip(q, want))
            if best is None or err < best:
                best, arg = err, e
        return cls(pool, per_cycle, arg, len(observed), cycles)

    def note(self) -> str:
        if not self.n_observed:
            return ("the market is modelled as a uniform draw — no cycle has "
                    "been recorded yet to fit anything against")
        return ("the market is modelled from %d offers over %d cycles, "
                "weighted by value^%.2f" % (self.n_observed, self.cycles,
                                            self.exponent))

    # -- use ---------------------------------------------------------------
    def draw(self, rng: random.Random) -> list:
        """One cycle's offers. Without replacement: the app deals a dozen
        different players, not a dozen draws that may repeat."""
        n = min(self.per_cycle, len(self._keys))
        keys, w, out = list(self._keys), list(self._w), []
        for _ in range(n):
            pick = rng.choices(range(len(keys)), weights=w, k=1)[0]
            out.append(keys.pop(pick))
            w.pop(pick)
        return out

    def best_over(self, cycles: int, gain, rng: random.Random,
                  trials: int = 400) -> list:
        """[best upgrade seen] over `cycles` cycles, one entry per trial.

        `gain(key)` is what owning him would add to your eleven. The answer is
        a DISTRIBUTION and must be reported as one: the median is what a
        typical wait buys and the upper decile is the reason to wait at all.
        """
        out = []
        for _ in range(trials):
            best = 0.0
            for _ in range(cycles):
                for k in self.draw(rng):
                    g = gain(k)
                    if g > best:
                        best = g
            out.append(best)
        return out


def _selftest() -> None:
    # -- quantiles ---------------------------------------------------------
    assert quantiles([1, 2, 3, 4, 5], (0.5,)) == (3,)
    assert quantiles([], (0.5,)) == (0.0,)

    pool = {"cheap%d" % i: 1e6 for i in range(200)}
    pool.update({"dear%d" % i: 50e6 for i in range(20)})
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

    print("ffcore.market self-test OK (18 cases)")


if __name__ == "__main__":
    _selftest()
