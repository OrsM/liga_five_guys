"""stats.py — the one "does A actually beat B" test every backtest calls:
a bootstrap CI on mean(a) - mean(b), not an ad hoc mean-difference check.

Stdlib only (`random`, not numpy).
"""

import random
import statistics


def percentile(data: list[float], p: float) -> float:
    """The p-th percentile (0-100) of `data`. < 2 points: the single value,
    or 0.0 for none.
    """
    if len(data) < 2:
        return float(data[0]) if data else 0.0
    cuts = statistics.quantiles(sorted(data), n=100, method="inclusive")
    return cuts[min(max(round(p), 1), 99) - 1]


def bootstrap_gap(a: list[float], b: list[float], n_boot: int = 2000,
                  seed: int = 0, ci: float = 0.90) -> dict | None:
    """CI on mean(a) - mean(b) via resampling each list independently.

    Returns {"n_a", "n_b", "diff", "lo", "hi", "beats"} — `beats` is True
    only when the CI on the gap excludes zero (a's values, read as
    errors/losses, are really smaller). `None` when either list is empty.
    """
    if not a or not b:
        return None
    rng = random.Random(seed)
    diff = sum(a) / len(a) - sum(b) / len(b)
    diffs = []
    for _ in range(n_boot):
        ra = rng.choices(a, k=len(a))
        rb = rng.choices(b, k=len(b))
        diffs.append(sum(ra) / len(ra) - sum(rb) / len(rb))
    tail = (1 - ci) / 2
    lo = percentile(diffs, tail * 100)
    hi = percentile(diffs, (1 - tail) * 100)
    return {"n_a": len(a), "n_b": len(b), "diff": diff, "lo": lo, "hi": hi,
           "beats": hi < 0}


def _selftest() -> None:
    # Two lists an order of magnitude apart should read as a clear beat —
    # the CI should not straddle zero.
    r = bootstrap_gap([1.0] * 30, [5.0] * 30, seed=1)
    assert r["beats"], r
    assert r["hi"] < 0, r
    # Identical distributions, small n — should NOT claim a beat just
    # because one bootstrap draw happened to land negative.
    same = [1.0, 2.0, 3.0, 2.0, 1.0, 3.0, 2.0, 1.0]
    r2 = bootstrap_gap(same, list(same), seed=2)
    assert not r2["beats"], r2
    assert abs(r2["diff"]) < 1e-9, r2
    # Empty input never crashes.
    assert bootstrap_gap([], [1.0]) is None
    assert bootstrap_gap([1.0], []) is None

    data = list(range(1, 101))  # 1..100 — the p-th percentile is ~p
    assert abs(percentile(data, 10) - 10) < 1.5, percentile(data, 10)
    assert abs(percentile(data, 50) - 50) < 1.5, percentile(data, 50)
    assert abs(percentile(data, 90) - 90) < 1.5, percentile(data, 90)
    assert percentile(data, 10) < percentile(data, 50) < percentile(data, 90)
    assert percentile([], 50) == 0.0
    assert percentile([7.0], 10) == percentile([7.0], 90) == 7.0
    print("stats self-test OK (4 cases)")


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        _selftest()
