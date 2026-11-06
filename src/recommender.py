"""Recommender: which players on offer can be bid for, at what prices, and what each is worth alone.

The contract with the rest of the code is two things.
  In:  the decision's rows (read_inputs): dec_player (player, pos, mine, asking, proceeds),
       dec_odds (player, ratio, win chance), cell (player, jornada, e).
  Out: Cands(bids, alone). bids: each player on offer not in the squad, his bids as (bid, win chance).
       alone: (b, s, bid, win, proceeds, gain, worth), every single move valued alone from the squad as it is
       (the board's rows): every bid of every candidate (b), and every sale of yours (s), the points he is worth.
It picks nothing and decides nothing about money or sales: which buys, and the sales that pay for them,
are the plan's (decision.plan_rows), judged together on the one objective, worth_on.

Value: gain = the best legal eleven's points over the jornadas with him, less without; worth = win x gain."""
from __future__ import annotations

from typing import Any, NamedTuple

from ffcore.rules import POSITIONS, best_eleven

Rows = dict[str, list[tuple[Any, ...]]]


class Cands(NamedTuple):
    bids: dict[str, list[tuple[float, float]]]
    alone: list[tuple[Any, ...]]


def cells_of(inp: Rows) -> tuple[dict[str, str], dict[int, dict[str, float]]]:
    """(position of each player, e of each player by jornada)."""
    pos = {k: p for k, p, *_ in inp["dec_player"] if p in POSITIONS}
    cells: dict[int, dict[str, float]] = {}
    for k, j, e in inp["cell"]:
        if k in pos:
            cells.setdefault(j, {})[k] = e
    return pos, cells


def points(pos: dict[str, str], cells: dict[int, dict[str, float]], squad: set[str],
           js: set[int] | None = None) -> float:
    """The best legal eleven's points over the jornadas (js: only these) of a squad."""
    total = 0.0
    for j, e in cells.items():
        if js is None or j in js:
            by: dict[str, list[tuple[float, str]]] = {p: [] for p in POSITIONS}
            for k in squad:
                if k in e:
                    by[pos[k]].append((-e[k], k))
            total += best_eleven({p: sorted(v) for p, v in by.items()})
    return total


def candidates(inp: Rows) -> Cands:
    pos, cells = cells_of(inp)
    mine = {k: proceeds or 0.0 for k, _, m, _, proceeds in inp["dec_player"] if m}
    ratios: dict[str, list[tuple[float, float]]] = {}
    for k, r, w in sorted(inp["dec_odds"]):
        ratios.setdefault(k, []).append((r, w))
    bids = {k: [(a * r, w) for r, w in ratios.get(k, [(1.0, 1.0)])]
            for k, _, _, a, _ in inp["dec_player"] if a is not None and k in pos and k not in mine}
    where = {k: {j for j, e in cells.items() if k in e} for k in pos}
    alone: list[tuple[Any, ...]] = []
    for b in sorted(bids):
        g = points(pos, cells, set(mine) | {b}, where[b]) - points(pos, cells, set(mine), where[b])
        alone += [(b, "", bid, win, None, g, win * g) for bid, win in bids[b]]
    now = points(pos, cells, set(mine))
    alone += [("", s, None, None, mine[s], g, g) for s in sorted(mine) for g in [points(pos, cells, set(mine) - {s}) - now]]
    return Cands(bids, alone)


def _selftest() -> None:
    """Seeded rows -> the candidates: one jornada, a squad of 11 (5M each; 4 points, m4 2)."""
    from ffcore.rules import DEBT_SHARE, SQUAD_MAX, XI_SIZE

    squad = {"k": "POR", "d1": "DEF", "d2": "DEF", "d3": "DEF", "m1": "MED", "m2": "MED", "m3": "MED",
             "m4": "MED", "f1": "DEL", "f2": "DEL", "f3": "DEL"}
    e = {**{k: 4.0 for k in squad}, "m4": 2.0, "star": 8.0, "good": 5.0, "keeper": 3.0, "pricey": 9.0}
    kinds = {"star": "MED", "good": "MED", "keeper": "POR", "pricey": "MED"}

    def rows(cash: float, ask: dict[str, float], odds: list[tuple[str, float, float]] | None = None,
             extra: int = 0) -> Rows:
        sq = {**squad, **{"x%02d" % i: "DEF" for i in range(extra)}}
        return {"dec_player": [(k, p, 1, None, 5e6) for k, p in sq.items()]
                              + [(k, kinds[k], 0, a, None) for k, a in ask.items()],
                "dec_odds": list(odds or []), "dec_money": [(cash, 5e6 * len(sq), DEBT_SHARE, SQUAD_MAX, XI_SIZE)],
                "cell": [(k, 1, v) for k, v in e.items() if k in sq or k in ask]}

    got = candidates(rows(10e6, {"star": 8e6}, odds=[("star", 1.0, 0.25), ("star", 1.25, 1.0), ("star", 1.3, 1.0)]))
    assert got.bids == {"star": [(8e6, 0.25), (10e6, 1.0), (10.4e6, 1.0)]}, "each bid the odds offer, as asking x ratio"
    assert [(a[2], a[3], a[5], a[6]) for a in got.alone if a[0]] == [(8e6, 0.25, 6.0, 1.5), (10e6, 1.0, 6.0, 6.0),
                                                              (10.4e6, 1.0, 6.0, 6.0)], \
        "alone: every bid of every candidate from the squad as it is: star for m4 is 6, at the chance of the bid"
    got = candidates(rows(10e6, {"keeper": 3e6, "good": 5e6}))
    assert {(a[0], a[5]) for a in got.alone if a[0]} == {("keeper", 0.0), ("good", 3.0)}, \
        "a second keeper plays for nobody: worth nothing; a 5 for m4's 2: 3"
    assert candidates(rows(10e6, {})).bids == {}, "nobody on offer"
    sales = {a[1]: (a[4], a[5]) for a in candidates(rows(10e6, {})).alone if a[1]}
    assert sales["m4"] == (5e6, -2.0) and sales["k"][1] == -4.0, \
        "each sale alone from the squad as it is, what he brings and the points the eleven loses: m4 2, k 4 (one jornada, 11 players)"
    print("recommender self-test OK")


if __name__ == "__main__":
    _selftest()
