from __future__ import annotations

import math
import statistics
from collections.abc import Iterable

__all__ = ["Bootstrap", "Cell", "PerJornada", "expected_points", "price_blend", "SEED_POOL", "MIN_POOL", "PERSISTENT_SHARE"]

SEED_POOL = (-1, -1, -1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1,
             1, 1, 1, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 3, 3, 3, 3, 3, 3, 3,
             4, 4, 4, 4, 4, 5, 5, 5, 6, 6, 6, 6, 8, 8, 8, 8, 9, 9, 10, 11,
             12, 13, 16)

MIN_POOL = 200

PERSISTENT_SHARE = 0.08


# A player's forecast for a jornada: (points if he plays, chance he plays).
Cell = tuple[float, float]
PerJornada = dict[int, dict[str, Cell]]


def expected_points(cell: Cell | None) -> float:
    """A forecast cell (points if he plays, chance he plays) as expected
    points; no cell is none."""
    return cell[0] * cell[1] if cell else 0.0


def price_blend(per_jornada: PerJornada, pos: dict[str, str], value: dict[str, float], w: float) -> PerJornada:
    """Each forecast pulled toward what it is for players at his price: his
    expected points E become w E + (1 - w) P, P the line of E on log value
    across the players of his position that jornada (the forecast's own,
    no outcome read). The chance he plays is kept; his points if he plays
    carry the change. The plan picks those the forecast rates above their
    price, and the price is the league's judgement of what the forecast
    lacks: 2026-10-30, the players a replay bought scored 28% under their
    forecast, those it started with as forecast, and the market's players
    forecast below their price 58% over it. w = 1: the forecast as it is."""
    if w >= 1.0:
        return per_jornada
    out: PerJornada = {}
    for j, layer in per_jornada.items():
        lines: dict[str, tuple[float, float]] = {}
        by: dict[str, list[tuple[float, float]]] = {}
        for k, cell in layer.items():
            if value.get(k, 0.0) > 0 and pos.get(k):
                by.setdefault(pos[k], []).append((math.log(value[k]), expected_points(cell)))
        for p, xy in by.items():
            if len(xy) < 3:
                continue
            mx = sum(x for x, _ in xy) / len(xy)
            my = sum(y for _, y in xy) / len(xy)
            sxx = sum((x - mx) ** 2 for x, _ in xy)
            b = sum((x - mx) * (y - my) for x, y in xy) / sxx if sxx > 0 else 0.0
            lines[p] = (my - b * mx, b)
        out[j] = {}
        for k, (pts, chance) in layer.items():
            line = lines.get(pos.get(k, ""))
            if line is None or value.get(k, 0.0) <= 0 or chance <= 0:
                out[j][k] = (pts, chance)
                continue
            e = w * pts * chance + (1 - w) * max(line[0] + line[1] * math.log(value[k]), 0.0)
            out[j][k] = (e / chance, chance)
    return out


class Bootstrap:

    def __init__(self, per_jornada: PerJornada, pool: Iterable[float | None] = (),
                 share: float | None = None) -> None:
        self.per_jornada = per_jornada
        self.share = PERSISTENT_SHARE if share is None else share
        real = [p for p in pool if p is not None]
        self.pool = tuple(real) if len(real) >= MIN_POOL else SEED_POOL
        mean = statistics.mean(self.pool)
        self.pool_mean = mean if abs(mean) > 1e-9 else 1.0

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
    layer = {"a": (2.0, 1.0), "b": (4.0, 1.0), "c": (6.0, 1.0), "odd": (9.0, 0.5), "nopos": (5.0, 1.0)}
    value = {"a": 1e6, "b": math.e * 1e6, "c": math.e ** 2 * 1e6, "odd": 1e6, "nopos": 1e6}
    pos = {k: "MED" for k in ("a", "b", "c", "odd")}
    assert price_blend({1: layer}, pos, value, 1.0) == {1: layer}, "w = 1: the forecast as it is"
    got = price_blend({1: layer}, pos, value, 0.5)[1]
    assert got["nopos"] == layer["nopos"], "no position, no line: as it is"
    assert got["odd"][1] == 0.5 and got["odd"][0] < 9.0, "the chance he plays kept, his points pulled toward his price's"
    e_line = expected_points(got["odd"]) * 2 - expected_points(layer["odd"])
    assert abs(e_line - (expected_points(got["a"]) * 2 - 2.0)) < 1e-9, "the same price, the same line"
    assert all(abs(sum(expected_points(got[k]) for k in pos) - sum(expected_points(layer[k]) for k in pos)) < 1e-9
               for _ in [0]), "the line goes through the mean: the position's points in all are kept"
    print("ffcore.forecast self-test OK")


if __name__ == "__main__":
    _selftest()
