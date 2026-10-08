
import statistics
from collections.abc import Sequence


def percentile(data: Sequence[float], p: float) -> float:
    if len(data) < 2:
        return float(data[0]) if data else 0.0
    cuts = statistics.quantiles(sorted(data), n=100, method="inclusive")
    return cuts[min(max(round(p), 1), 99) - 1]


def shrink(prior: float, k: float, total: float, n: float) -> float:
    """An observed rate pulled toward a prior: k is how many observations the
    prior is worth, total the sum observed over n."""
    return (k * prior + total) / (k + n)


def _selftest() -> None:
    assert shrink(0.5, 4.0, 0.0, 0.0) == 0.5, "no data, the prior"
    assert shrink(0.5, 0.0, 3.0, 4.0) == 0.75, "no weight, the data"
    assert shrink(0.6, 4.0, 4.0, 4.0) == 0.8, "halfway at k == n"
    data = list(range(1, 101))
    assert abs(percentile(data, 10) - 10) < 1.5, percentile(data, 10)
    assert abs(percentile(data, 50) - 50) < 1.5, percentile(data, 50)
    assert abs(percentile(data, 90) - 90) < 1.5, percentile(data, 90)
    assert percentile(data, 10) < percentile(data, 50) < percentile(data, 90)
    assert percentile([], 50) == 0.0
    assert percentile([7.0], 10) == percentile([7.0], 90) == 7.0
    print("stats self-test OK")


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        _selftest()
