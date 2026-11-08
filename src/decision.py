"""The decision's inputs and the funding of a buy: the league at t (schema.sql look, my_squad, my_cash) made into
the rows the board and fund() read (decide.sql dec_*), and fund(), the sales that pay for a buy."""
from __future__ import annotations

import random
from collections import Counter
from contextlib import closing
from functools import lru_cache, partial
from itertools import combinations
from typing import Any, Callable, NamedTuple

from ffcore.db import connect
from ffcore.rules import DEBT_SHARE, POSITIONS, SHAPES, SQUAD_MAX, XI_SIZE, best_eleven

__all__ = ["inputs", "read_inputs", "cells_of", "by_jornada", "eleven", "can_field", "fieldable", "fund", "Funding", "cheapest"]

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


def by_jornada(pos: dict[str, str], cells: dict[int, dict[str, float]], squad: set[str],
               js: set[int] | None = None) -> dict[int, float]:
    """The best legal eleven's points of a squad in each jornada (js: only these)."""
    out: dict[int, float] = {}
    for j, e in cells.items():
        if js is None or j in js:
            by: dict[str, list[tuple[float, str]]] = {p: [] for p in POSITIONS}
            for k in squad:
                if k in e:
                    by[pos[k]].append((-e[k], k))
            out[j] = best_eleven({p: sorted(v) for p, v in by.items()})
    return out


def eleven(pos: dict[str, str], e: dict[str, float], squad: set[str] | frozenset[str]) -> tuple[float, list[str]]:
    """The best legal eleven of a squad in one jornada (e: each player's expected points): its points and its players."""
    best: tuple[float, list[str]] = (0.0, [])
    for shape in SHAPES:
        pick: list[str] = []
        for p in POSITIONS:
            pick += sorted((k for k in squad if pos.get(k) == p and k in e), key=lambda k: (-e[k], k))[:shape[p]]
        if len(pick) == XI_SIZE and (not best[1] or sum(e[k] for k in pick) > best[0]):
            best = (sum(e[k] for k in pick), pick)
    return best


def can_field(count: Counter[str]) -> bool:
    """Whether some formation can be fielded from so many players of each position."""
    return any(all(count[p] >= n for p, n in shape.items()) for shape in SHAPES)


def fieldable(pos: dict[str, str], squad: set[str] | frozenset[str]) -> bool:
    """Whether some formation can be fielded from the squad (pos: each player's position)."""
    return can_field(Counter(pos[k] for k in squad if k in pos))


class Funding(NamedTuple):
    tonight: frozenset[str]
    if_won: frozenset[str]
    if_lost: frozenset[str]
    won: float
    lost: float
    ev: float
    cash_if_lost: float


def cheapest(score: Callable[[frozenset[str]], float], legal: Callable[[frozenset[str]], bool], squad: frozenset[str],
             price: dict[str, float], need: float) -> tuple[float, frozenset[str]] | None:
    order = sorted((k for k in squad if price.get(k, 0.0) > 0),
                   key=lambda k: ((score(squad) - score(squad - {k})) / price[k], k))
    left = [sum(price[k] for k in order[i:]) for i in range(len(order) + 1)]
    best: tuple[float, frozenset[str]] | None = None
    stack: list[tuple[int, frozenset[str], float]] = [(0, frozenset(), 0.0)]
    while stack:
        i, gone, got = stack.pop()
        if got >= need - 1e-6:
            pts = score(squad - gone)
            if best is None or pts > best[0]:
                best = (pts, gone)
            continue
        if i == len(order) or got + left[i] < need - 1e-6 or best is not None and score(squad - gone) <= best[0]:
            continue
        stack.append((i + 1, gone, got))
        nxt = squad - gone - {order[i]}
        if legal(nxt):
            stack.append((i + 1, gone | {order[i]}, got + price[order[i]]))
    return best


def fund(inp: dict[str, list[tuple[Any, ...]]], buy: tuple[str, float, float], offers: dict[str, float],
         worth: dict[str, float]) -> Funding | None:
    (cash, team_value, debt, squad_max, _), = inp["dec_money"]
    k, bid, win = buy
    pos, cells = cells_of(inp)
    locks = dict(inp.get("dec_lock", []))
    js = {j for j in cells if not locks or j >= min(locks)}
    later = {p: got or 0.0 for p, _, m, _, got in inp["dec_player"] if m}
    squad, here = frozenset(later), frozenset(later) | {k}
    score = lru_cache(maxsize=None)(lambda sq: sum(by_jornada(pos, cells, set(sq), js).values()))
    legal = partial(fieldable, pos)
    memo: dict[tuple[frozenset[str], int], tuple[float, frozenset[str]] | None] = {}

    def need_of(base: frozenset[str], gap: float) -> tuple[float, frozenset[str]] | None:
        if (base, round(gap)) not in memo:
            memo[base, round(gap)] = cheapest(score, legal, base, later, gap) if gap > 0 else (score(base), frozenset())
        return memo[base, round(gap)]

    pool = sorted((p for p in squad if p in offers), key=lambda p: ((score(here) - score(here - {p})) / max(offers[p], 1.0), p))
    best: Funding | None = None
    stack: list[tuple[int, frozenset[str]]] = [(0, frozenset())]
    while stack:
        i, A = stack.pop()
        pa, va = sum(offers[p] for p in A), sum(worth[p] for p in A)
        if cash + pa + debt * (team_value - va) >= bid - 1e-6:
            won = need_of(here - A, bid - cash - pa)
            lost = need_of(squad - A, -(cash + pa))
            if won and lost and len(here - A - won[1]) <= squad_max and legal(here - A - won[1]):
                ev = win * won[0] + (1 - win) * lost[0]
                if best is None or ev > best.ev:
                    best = Funding(A, won[1], lost[1], won[0], lost[0], ev, cash + pa + sum(later[p] for p in lost[1]))
        for j in range(i, len(pool)):
            nxt = A | {pool[j]}
            if legal(squad - nxt) and (best is None or win * score(here - nxt) + (1 - win) * score(squad - nxt) > best.ev):
                stack.append((j + 1, nxt))
    return best


def _selftest_fund() -> None:
    rng = random.Random(5)
    made = 0
    for _ in range(8):
        own = {"m%02d" % i: p for i, p in enumerate(["POR"] + ["DEF"] * 4 + ["MED"] * 4 + ["DEL"] * 3)}
        pos = own | {"star": "DEL"}
        e = {k: rng.choice([0, 1, 2, 3, 5]) * 1.0 for k in own} | {"star": 9.0}
        got = {k: rng.choice([2e6, 4e6, 6e6]) for k in own}
        offers = {k: got[k] * rng.choice([0.9, 1.0, 1.05]) for k in rng.sample(sorted(own), 4)}
        cash, bid, win = rng.choice([-6e6, -2e6, 3e6]), rng.choice([9e6, 14e6, 20e6]), rng.choice([0.3, 0.7, 0.95])
        inp: dict[str, list[tuple[Any, ...]]] = {
            "dec_player": [(k, p, 1, None, got[k]) for k, p in own.items()] + [("star", "DEL", 0, bid, None)],
            "dec_odds": [], "dec_money": [(cash, 5e6 * len(own), DEBT_SHARE, SQUAD_MAX, XI_SIZE)],
            "cell": [(k, j, e[k]) for j in (1, 2) for k in e], "dec_lock": [(1, "2026-11-06T20:00Z"), (2, "2026-11-13T20:00Z")]}
        worth = {k: 5e6 for k in own}
        found = fund(inp, ("star", bid, win), offers, worth)
        squad, here = frozenset(own), frozenset(own) | {"star"}
        score = lru_cache(maxsize=None)(lambda sq: sum(by_jornada(pos, {j: dict(e) for j in (1, 2)}, set(sq)).values()))
        legal = partial(fieldable, pos)
        best: float | None = None
        for n in range(len(offers) + 1):
            for sold in combinations(sorted(offers), n):
                A = frozenset(sold)
                pa, va = sum(offers[k] for k in A), sum(worth[k] for k in A)
                if cash + pa + DEBT_SHARE * (5e6 * len(own) - va) < bid - 1e-6 or not legal(squad - A):
                    continue
                rest = sorted(squad - A)
                won = max((score(here - A - frozenset(B)) for m in range(len(rest) + 1) for B in combinations(rest, m)
                           if cash + pa - bid + sum(got[k] for k in B) >= -1e-6 and legal(here - A - frozenset(B))), default=None)
                lost = max((score(squad - A - frozenset(L)) for m in range(len(rest) + 1) for L in combinations(rest, m)
                            if cash + pa + sum(got[k] for k in L) >= -1e-6 and legal(squad - A - frozenset(L))), default=None)
                if won is not None and lost is not None:
                    ev = win * won + (1 - win) * lost
                    best = ev if best is None else max(best, ev)
        assert (found is None) == (best is None), (cash, bid, offers)
        if found and best is not None:
            assert abs(found.ev - best) < 1e-9, (found.ev, best)
            assert found.tonight <= set(offers) and not found.tonight & found.if_won
            assert cash + sum(offers[k] for k in found.tonight) - bid + sum(got[k] for k in found.if_won) >= -1e-6, "cash at the lock if won"
            assert cash + sum(offers[k] for k in found.tonight) + sum(got[k] for k in found.if_lost) >= -1e-6, "cash at the lock if lost"
            assert cash + sum(offers[k] for k in found.tonight) + DEBT_SHARE * (5e6 * len(own) - sum(worth[k] for k in found.tonight)) >= bid - 1e-6
            assert min(found.won, found.lost) - 1e-9 <= found.ev <= max(found.won, found.lost) + 1e-9
            made += 1
    assert made >= 3, "the leagues are not all infeasible"




def _selftest_xi() -> None:
    squad = {"k": "POR", "d1": "DEF", "d2": "DEF", "d3": "DEF", "m1": "MED", "m2": "MED", "m3": "MED",
             "m4": "MED", "f1": "DEL", "f2": "DEL", "f3": "DEL", "bench": "DEF"}
    e = {k: 4.0 for k in squad} | {"m4": 2.0, "bench": 1.0}
    inp: dict[str, list[tuple[Any, ...]]] = {"dec_player": [(k, p, 1, None, 5e6) for k, p in squad.items()],
                                             "cell": [(k, j, e[k]) for j in (1, 2) for k in e]}
    pos, cells = cells_of(inp)
    got = by_jornada(pos, cells, set(squad))
    assert got == {1: 42.0, 2: 42.0}, "the best formation's eleven"
    assert by_jornada(pos, cells, set(squad), {2}) == {2: 42.0}, "only the jornadas asked for"
    points, xi = eleven(pos, cells[1], set(squad))
    assert points == 42.0 and len(xi) == 11 and "bench" not in xi, (points, xi)
    assert not can_field(Counter(pos[k] for k in squad if k != "k")), "no keeper, no eleven"
    assert eleven(pos, cells[1], {k for k in squad if k != "k"}) == (0.0, []), "no legal eleven: none"


def _selftest() -> None:
    _selftest_xi()
    _selftest_fund()
    print("decision self-test OK")


if __name__ == "__main__":
    _selftest()
