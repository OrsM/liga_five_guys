from __future__ import annotations

import statistics

__all__ = ["Bootstrap", "expected_points", "SEED_POOL", "MIN_POOL", "PERSISTENT_SHARE"]

SEED_POOL = (-1, -1, -1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1,
             1, 1, 1, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 3, 3, 3, 3, 3, 3, 3,
             4, 4, 4, 4, 4, 5, 5, 5, 6, 6, 6, 6, 8, 8, 8, 8, 9, 9, 10, 11,
             12, 13, 16)

MIN_POOL = 200

PERSISTENT_SHARE = 0.08


def expected_points(cell) -> float:
    """A forecast cell (points if he plays, chance he plays) as expected
    points; no cell is none."""
    return cell[0] * cell[1] if cell else 0.0


class Bootstrap:

    def __init__(self, per_jornada: dict[int, dict[str, tuple[float, float]]],
                 pool=(), share: float = PERSISTENT_SHARE):
        self.per_jornada = per_jornada
        self.share = share
        self._order = {j: sorted(d) for j, d in per_jornada.items()}
        real = [p for p in pool if p is not None]
        self.pool = tuple(real) if len(real) >= MIN_POOL else SEED_POOL
        mean = statistics.mean(self.pool)
        self.pool_mean = mean if abs(mean) > 1e-9 else 1.0

    def order(self, jornada: int) -> list[str]:
        return self._order.get(jornada, [])

    def expected(self, jornada: int) -> dict[str, float]:
        return {k: expected_points(cell)
                for k, cell in self.per_jornada.get(jornada, {}).items()}


def _selftest() -> None:
    fc = Bootstrap({1: {"nailed": (5.0, 1.0), "rota": (5.0, 0.5),
                        "out": (5.0, 0.0)}})
    assert fc.expected(1) == {"nailed": 5.0, "rota": 2.5, "out": 0.0}
    assert expected_points((5.0, 0.5)) == 2.5 and expected_points(None) == 0.0, \
        "a cell (points if he plays, chance he plays) is worth their product"
    assert fc.expected(99) == {}, "a jornada nobody plays is empty, not an error"
    assert Bootstrap({}, pool=[1, 2, 3]).pool == SEED_POOL
    big = list(range(MIN_POOL))
    assert Bootstrap({}, pool=big).pool == tuple(big)
    assert Bootstrap({1: {"x": (4.0, 1.0)}}, pool=[0] * MIN_POOL).pool_mean == 1.0
    print("ffcore.forecast self-test OK")


if __name__ == "__main__":
    _selftest()
