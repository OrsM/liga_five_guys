"""The decision's inputs: the league at t (schema.sql look, my_squad, my_cash) made into the rows the board and
approach.approach read (decide.sql dec_*), and the players on the market who would improve your eleven."""
from __future__ import annotations

from contextlib import closing
from typing import Any

import eleven
from ffcore.db import connect
from ffcore.rules import DEBT_SHARE, POSITIONS, SQUAD_MAX, XI_SIZE, in_force

__all__ = ["inputs", "read_inputs", "cells_of", "window_value", "opportunities", "recommended"]

INPUTS = ("dec_state", "dec_player", "dec_odds", "dec_money", "dec_bid", "dec_close", "dec_lock", "dec_offer")


def inputs() -> None:
    """What the decision looks at, the league at t (look: the market seen, the forecast, your squad and cash then),
    made once a decision (decide.sql dec_player_seen, dec_odds_seen, dec_money_seen, dec_bid_seen)."""
    with closing(connect()) as db, db:
        for t in INPUTS:
            db.execute("DELETE FROM %s" % t)
        db.execute("INSERT INTO dec_state VALUES (?, ?, ?)", (DEBT_SHARE, SQUAD_MAX, XI_SIZE))
        for t in INPUTS[1:]:
            db.execute("INSERT INTO %s SELECT * FROM %s_seen" % (t, t))


def read_inputs() -> dict[str, list[tuple[Any, ...]]]:
    """inputs() made, and read back: every table of it and the cells (player, jornada, e)."""
    inputs()
    with closing(connect()) as db:
        inp = {t: db.execute("SELECT * FROM %s" % t).fetchall() for t in INPUTS[1:]}
        inp["cell"] = db.execute("SELECT player, jornada, e FROM cell").fetchall()
    return inp


def cells_of(inp: dict[str, list[tuple[Any, ...]]]) -> tuple[dict[str, str], dict[int, dict[str, float]]]:
    """(position of each player, e of each player by jornada)."""
    pos = {k: p for k, p, *_ in inp["dec_player"] if p in POSITIONS}
    cells: dict[int, dict[str, float]] = {}
    for k, j, e in inp["cell"]:
        if k in pos:
            cells.setdefault(j, {})[k] = e
    return pos, cells


def window_value(pos: dict[str, str], cells: dict[int, dict[str, float]], where: frozenset[int]) -> dict[str, float]:
    """Each player's points over some jornadas: his e summed over them."""
    return {k: sum(cells[j].get(k, 0.0) for j in where) for k in pos}


def opportunities(inp: dict[str, list[tuple[Any, ...]]]) -> list[tuple[str, list[tuple[float, float]], float, frozenset[int]]]:
    """Each player on the market who would improve your settled eleven, most first: (player, his bids as (bid, win
    chance) from the odds at his asking price, the points he adds over the jornadas that lock after his auction closes,
    those jornadas). Nobody if you have no eleven."""
    pos, cells = cells_of(inp)
    mine = frozenset(k for k, _, m, *_ in inp["dec_player"] if m)
    ratios: dict[str, list[tuple[float, float]]] = {}
    for k, r, w in sorted(inp["dec_odds"]):
        ratios.setdefault(k, []).append((r, w))
    locks, closes = dict(inp["dec_lock"]), dict(inp["dec_close"])
    soon = min(locks.values(), default=None)
    seen: dict[frozenset[int], tuple[dict[str, float], eleven.Settled | None]] = {}
    out = []
    for k, _, m, asking, _ in inp["dec_player"]:
        if m or asking is None or k not in pos:
            continue
        where = frozenset(j for j in cells if in_force(locks, j, closes.get(k) or soon))
        if where not in seen:
            value = window_value(pos, cells, where)
            seen[where] = (value, eleven.best_eleven(mine, pos, value))
        value, settled = seen[where]
        if settled is None:
            continue
        got = eleven.swap(k, settled.eleven, pos, value)
        if got is not None and got.improves:
            out.append((k, [(asking * r, w) for r, w in ratios.get(k, [(1.0, 1.0)])], got.gain, where))
    return sorted(out, key=lambda o: (-o[2], o[0]))


def recommended(bids: list[tuple[float, float]]) -> tuple[float, float]:
    """The bid to make: the least of those within 0.02 of the best chance of winning."""
    best = max(w for _, w in bids)
    return min((b, w) for b, w in bids if w >= best - 0.02)


def _selftest_opportunities() -> None:
    squad = {"k": "POR", "d1": "DEF", "d2": "DEF", "d3": "DEF", "m1": "MED", "m2": "MED", "m3": "MED",
             "m4": "MED", "f1": "DEL", "f2": "DEL", "f3": "DEL"}
    e = {**{k: 4.0 for k in squad}, "m4": 2.0, "star": 8.0, "good": 5.0, "keeper": 3.0, "late": 9.0}
    kinds = {"star": "MED", "good": "MED", "keeper": "POR", "late": "MED"}
    inp: dict[str, list[tuple[Any, ...]]] = {
        "dec_player": [(k, p, 1, None, 5e6) for k, p in squad.items()]
                      + [(k, kinds[k], 0, a, None) for k, a in (("star", 8e6), ("good", 5e6), ("keeper", 3e6), ("late", 6e6))],
        "dec_odds": [("star", 1.0, 0.25), ("star", 1.25, 1.0), ("star", 1.3, 1.0)],
        "cell": [(k, j, v) for k, v in e.items() for j in (1, 2)],
        "dec_lock": [(1, "2026-11-06T20:00Z"), (2, "2026-11-13T20:00Z")],
        "dec_close": [("late", "2026-11-06T21:24Z")]}
    got = {k: (bids, gain, where) for k, bids, gain, where in opportunities(inp)}
    assert got["star"][:2] == ([(8e6, 0.25), (10e6, 1.0), (10.4e6, 1.0)], 12.0), "each bid the odds offer, as asking x ratio; star for m4 is 6 a jornada"
    assert got["good"][1] == 6.0 and "keeper" not in got, "a second keeper plays for nobody"
    assert got["late"][1] == 7.0 and got["late"][2] == frozenset({2}), "an auction closing after jornada 1's lock counts for jornada 2 only: your eleven is frozen at the lock"
    assert [k for k, *_ in opportunities(inp)][0] == "star", "most points first"
    assert recommended(got["star"][0]) == (10e6, 1.0), "the least bid that is as likely to win as any"
    assert recommended([(5e6, 0.5), (6e6, 0.51), (9e6, 0.99)]) == (9e6, 0.99), "a bid that is much likelier to win is worth its price"
    short = dict(inp, dec_player=[row for row in inp["dec_player"] if row[0] != "k"])
    assert opportunities(short) == [], "no eleven: no opportunities"


def _selftest() -> None:
    _selftest_opportunities()
    print("decision self-test OK")


if __name__ == "__main__":
    _selftest()
