"""stats.py — the one "does A actually beat B" test every backtest calls.

Every validation surface in this repo (rate-forecast accuracy, start-
probability calibration, ranking-strategy backtests) used to print its own
ad hoc "beats"/"validated" verdict off a raw mean difference, any margin, any
n. `bootstrap_gap()` is the single shared replacement — a paired bootstrap CI
on mean(a) - mean(b), matching this repo's own standing discipline of one
function every caller reuses (route_kind(), _move_rank_key(),
illegal_squads()) rather than each site re-deriving "is this real" its own
way.

Stdlib only (`random`, not numpy) — matches methodology.py/backtest.py's own
style; season.py is the only file that reaches for numpy, for vectorized
Monte Carlo trials at scale, not for a small-n stat like this.
"""

import random


def bootstrap_gap(a: list[float], b: list[float], n_boot: int = 2000,
                  seed: int = 0, ci: float = 0.90) -> dict | None:
    """CI on mean(a) - mean(b) via resampling each list independently.

    Returns {"n_a", "n_b", "diff", "lo", "hi", "beats"} — `beats` is True
    only when the CI on the gap excludes zero (mean(a) < mean(b), i.e. a's
    values — read as errors/losses, lower is better — are really smaller).
    `None` when either list is empty; a single-element list still returns a
    (degenerate, usually very wide) CI rather than crashing, since "not
    enough data to say" is itself the honest answer worth printing.
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
    diffs.sort()
    tail = (1 - ci) / 2
    lo = diffs[int(tail * n_boot)]
    hi = diffs[int((1 - tail) * n_boot) - 1]
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
    print("stats self-test OK (3 cases)")


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        _selftest()
