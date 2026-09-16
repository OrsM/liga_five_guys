
from __future__ import annotations

import math
import random
import statistics

try:
    import numpy as np
except ImportError:                      # pragma: no cover
    np = None

__all__ = ["Offers", "quantiles"]

EXPONENTS = [round(0.05 * i, 2) for i in range(21)]

FIT_AT = (0.25, 0.5, 0.75)


def quantiles(values, ps=FIT_AT) -> tuple:
    v = sorted(values)
    if not v:
        return tuple(0.0 for _ in ps)
    return tuple(v[min(len(v) - 1, max(0, int(p * (len(v) - 1))))] for p in ps)


class Offers:

    def __init__(self, pool: dict, per_cycle: int = 12, exponent: float = 0.0,
                 n_observed: int = 0, cycles: int = 0, runner: tuple = ()):
        self.pool = dict(pool)
        self.per_cycle = max(1, per_cycle)
        self.exponent = exponent
        self.n_observed = n_observed
        self.cycles = cycles
        self.runner = runner
        self._keys = list(self.pool)
        self._w = [max(0.0, v) ** exponent for v in self.pool.values()]
        if not any(self._w):
            self._w = [1.0] * len(self._keys)
        self._at = {k: i for i, k in enumerate(self._keys)}
        self._wa = np.asarray(self._w, dtype=float) if np is not None else None

    @classmethod
    def fit(cls, pool: dict, observed: list, per_cycle: int = 12,
            cycles: int = 0, trials: int = 200, seed: int = 0) -> "Offers":
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

    def draw(self, rng: random.Random) -> list:
        return [self._keys[i] for i in self._draw_idx(rng)]

    def _draw_idx(self, rng: random.Random) -> list:
        n = min(self.per_cycle, len(self._keys))
        at, w, out = list(range(len(self._keys))), list(self._w), []
        for _ in range(n):
            pick = rng.choices(range(len(at)), weights=w, k=1)[0]
            out.append(at.pop(pick))
            w.pop(pick)
        return out

    def _draw_np(self, rng, cycles: int):
        if np is None or not self._keys:
            return None
        n = min(self.per_cycle, len(self._keys))
        w = np.where(self._wa > 0, self._wa, np.finfo(float).tiny)
        clock = rng.exponential(size=(cycles, len(self._keys))) / w
        return np.argpartition(clock, n - 1, axis=1)[:, :n]

    def chance(self, key) -> float:
        total = sum(self._w)
        if not total or key not in self.pool:
            return 0.0
        return min(1.0, self.per_cycle * self._w[self._at[key]] / total)

    def median_wait(self, key) -> float | None:
        p = self.chance(key)
        if p <= 0.0:
            return None
        if p >= 1.0:
            return 1.0
        wait = math.log(0.5) / math.log(1.0 - p)
        return wait if wait < 400 else None

    def best_over(self, cycles: int, gain, rng: random.Random,
                  trials: int = 400) -> list:
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

    assert quantiles([1, 2, 3, 4, 5], (0.5,)) == (3,)
    assert quantiles([], (0.5,)) == (0.0,)

    pool = {"cheap%d" % i: 1e6 for i in range(200)}
    pool.update({"dear%d" % i: 50e6 for i in range(20)})
    flat = Offers.fit({"a": 1e6, "b": 2e6, "c": 30e6}, [1e6, 2e6], cycles=1)
    assert flat.runner and 0.0 <= flat.runner[1], flat.runner
    assert "within" in flat.note() and "roughly" in flat.note(), flat.note()
    assert Offers.fit({"a": 1e6}, []).runner == ()
    assert "uniform draw" in Offers.fit({"a": 1e6}, []).note()

    rng = random.Random(4)

    blind = Offers.fit(pool, [])
    assert blind.exponent == 0.0
    assert "uniform" in blind.note() and "no cycle" in blind.note()

    rich = Offers.fit(pool, [50e6] * 30 + [1e6] * 3, trials=60)
    assert rich.exponent > 0.0, rich.exponent
    assert "value^" in rich.note() and "33 offers" in rich.note()
    drawn = [pool[k] for k in rich.draw(rng)]
    assert statistics.mean(drawn) > 2e6, drawn

    flat = Offers.fit(pool, [1e6] * 30 + [50e6] * 3, trials=60)
    assert flat.exponent <= rich.exponent, (flat.exponent, rich.exponent)

    got = blind.draw(rng)
    assert len(got) == len(set(got)) == 12, got
    assert len(Offers({"only": 1e6}, per_cycle=12).draw(rng)) == 1
    assert Offers({}, per_cycle=12).draw(rng) == []

    def gain(k):
        return 5.0 if k == "dear0" else 0.0

    short = Offers(pool, per_cycle=12).best_over(1, gain, random.Random(1))
    long_ = Offers(pool, per_cycle=12).best_over(10, gain, random.Random(1))
    assert statistics.mean(long_) > statistics.mean(short), \
        "waiting longer must see more of the market"
    assert max(short) <= 5.0 and min(short) >= 0.0
    assert set(Offers(pool).best_over(3, lambda k: 0.0, rng)) == {0.0}

    one = Offers({"a": 1e6, **{"x%d" % i: 1e6 for i in range(99)}},
                 per_cycle=10)
    assert abs(one.chance("a") - 0.1) < 1e-9, one.chance("a")
    w = one.median_wait("a")
    assert 6.0 < w < 7.5, w
    assert one.chance("nobody") == 0.0
    assert one.median_wait("nobody") is None
    assert Offers({"a": 1e6}, per_cycle=10).chance("a") == 1.0
    assert Offers({"a": 1e6}, per_cycle=10).median_wait("a") == 1.0

    print("ffcore.market self-test OK (25 cases)")


if __name__ == "__main__":
    _selftest()
