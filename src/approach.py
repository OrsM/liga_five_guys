from __future__ import annotations

import random
import sys
from collections.abc import Mapping
from functools import lru_cache
from itertools import combinations
from typing import NamedTuple

import eleven
from ffcore.rules import DEBT_SHARE, SALE_ESTIMATE, SALE_SD, SQUAD_MAX, bid_margin, cash_at_lock

__all__ = ["Club", "Approach", "approach"]

INF = float("inf")


class Club(NamedTuple):
    squad: frozenset[str]
    pos: Mapping[str, str]
    worth: Mapping[str, float]
    offers: Mapping[str, float]
    cash: float
    team_value: float
    pending: tuple[int, float] = (0, 0.0)
    squad_max: int = SQUAD_MAX
    debt: float = DEBT_SHARE


class Approach(NamedTuple):
    feasible: bool
    short: float
    tonight: frozenset[str]
    held: frozenset[str]
    if_won: frozenset[str]
    if_lost: frozenset[str]
    raised: float
    margin: float
    lock_margin: float
    lock_margin_low: float
    cash_if_lost: float
    gain: float
    cost_won: float
    cost_lost: float
    net: float


class Side:
    def __init__(self, squad: frozenset[str], pos: Mapping[str, str], value: Mapping[str, float]):
        self.value, self.ranked = value, eleven.rank(squad, pos, value)
        top = eleven.points_without(self.ranked, value, frozenset())
        self.fields = top is not None
        self.points = top or 0.0
        self.memo: dict[frozenset[str], float] = {}

    def cost(self, gone: frozenset[str]) -> float:
        if gone not in self.memo:
            left = eleven.points_without(self.ranked, self.value, gone)
            self.memo[gone] = INF if left is None else self.points - left
        return self.memo[gone]


def proceeds(club: Club, k: str) -> float:
    return club.offers.get(k, SALE_ESTIMATE * club.worth[k])


def cover(side: Side, fixed: frozenset[str], pool: frozenset[str], price: Mapping[str, float], need: float
          ) -> tuple[float, frozenset[str]] | None:
    if need <= 1e-6:
        c = side.cost(fixed)
        return None if c == INF else (c, frozenset())
    order = sorted((k for k in pool if price[k] > 0), key=lambda k: (side.cost(fixed | {k}) / price[k], k))
    left = [sum(price[k] for k in order[i:]) for i in range(len(order) + 1)]
    best: tuple[float, frozenset[str]] | None = None
    stack: list[tuple[int, frozenset[str], float]] = [(0, frozenset(), 0.0)]
    while stack:
        i, gone, got = stack.pop()
        c = side.cost(fixed | gone)
        if c == INF or best is not None and c >= best[0] - 1e-12:
            continue
        if got >= need - 1e-6:
            best = (c, gone)
            continue
        if i == len(order) or got + left[i] < need - 1e-6:
            continue
        stack.append((i + 1, gone, got))
        stack.append((i + 1, gone | {order[i]}, got + price[order[i]]))
    return best


def approach(club: Club, player: str, bid: float, win: float, value: Mapping[str, float]) -> Approach | None:
    lost_side = Side(club.squad, club.pos, value)
    won_side = Side(club.squad | {player}, club.pos, value)
    if not lost_side.fields:
        return None
    gain = won_side.points - lost_side.points
    pend_n, pend_amount = club.pending
    price = {k: proceeds(club, k) for k in club.squad}
    offered = sorted((k for k in club.squad if k in club.offers),
                     key=lambda k: (won_side.cost(frozenset({k})) / max(club.offers[k], 1.0), k))
    reach = [sum(club.offers[k] - club.debt * club.worth[k] for k in offered[i:]) for i in range(len(offered) + 1)]
    best: tuple[float, frozenset[str], frozenset[str], frozenset[str], float, float] | None = None
    ceiling = bid_margin(club.cash, sum(club.offers[k] for k in offered), club.team_value,
                         sum(club.worth[k] for k in offered), bid, pend_amount, club.debt)
    stack: list[tuple[int, frozenset[str]]] = [(0, frozenset())]
    while stack:
        i, a = stack.pop()
        cw, cl = won_side.cost(a), lost_side.cost(a)
        if cw == INF or cl == INF or best is not None and win * cw + (1 - win) * cl >= best[0] - 1e-12:
            continue
        raised, sold = sum(club.offers[k] for k in a), sum(club.worth[k] for k in a)
        margin = bid_margin(club.cash, raised, club.team_value, sold, bid, pend_amount, club.debt)
        if margin >= -1e-6:
            need_won = -cash_at_lock(club.cash, raised, bid, 0.0)
            need_lost = -(club.cash + raised)
            won = cover(won_side, a, club.squad - a, price, need_won)
            lost = cover(lost_side, a, club.squad - a, price, need_lost)
            if won and lost and len(club.squad) + 1 - len(a) - len(won[1]) + pend_n <= club.squad_max:
                ev = win * won[0] + (1 - win) * lost[0]
                if best is None or ev < best[0]:
                    best = (ev, a, won[1], lost[1], won[0], lost[0])
        elif margin + reach[i] < -1e-6:
            continue
        for j in range(i, len(offered)):
            stack.append((j + 1, a | {offered[j]}))
    if best is None:
        return Approach(False, max(0.0, -ceiling), frozenset(), frozenset(club.offers) & club.squad, frozenset(),
                        frozenset(), 0.0, ceiling, 0.0, 0.0, 0.0, gain, 0.0, 0.0, 0.0)
    _, a, b, ell, cost_won, cost_lost = best
    raised = sum(club.offers[k] for k in a)
    later = sum(price[k] for k in b)
    return Approach(
        True, 0.0, a, (frozenset(club.offers) & club.squad) - a, b, ell, raised,
        bid_margin(club.cash, raised, club.team_value, sum(club.worth[k] for k in a), bid, pend_amount, club.debt),
        cash_at_lock(club.cash, raised, bid, later),
        cash_at_lock(club.cash, raised, bid, later * (1 - SALE_SD)),
        club.cash + raised + sum(price[k] for k in ell),
        gain, cost_won, cost_lost, win * (gain - cost_won) - (1 - win) * cost_lost)


def _selftest() -> None:
    rng = random.Random(5)
    made = 0
    for _ in range(10):
        own = {"m%02d" % i: p for i, p in enumerate(["POR"] + ["DEF"] * 5 + ["MED"] * 5 + ["DEL"] * 3)}
        pos = own | {"star": "DEL"}
        value = {k: rng.choice([0, 1, 2, 3, 5]) * 2.0 for k in own} | {"star": 18.0}
        worth = {k: rng.choice([2e6, 4e6, 6e6]) for k in own}
        offers = {k: worth[k] * rng.choice([0.9, 1.0, 1.05]) for k in rng.sample(sorted(own), 4)}
        club = Club(frozenset(own), pos, worth, offers, rng.choice([-5e6, -1e6, 3e6]), 5e6 * len(own))
        bid, win = rng.choice([8e6, 12e6, 16e6]), rng.choice([0.3, 0.7, 0.95])
        got = approach(club, "star", bid, win, value)
        assert got is not None
        squad, here = club.squad, club.squad | {"star"}
        price = {k: proceeds(club, k) for k in squad}
        points = lru_cache(maxsize=None)(lambda sq: eleven.best_total(sq, pos, value))
        base, with_star = points(squad), points(here)
        assert base is not None and with_star is not None
        want: float | None = None
        for n in range(len(offers) + 1):
            for sold in combinations(sorted(offers), n):
                a = frozenset(sold)
                pa, va = sum(offers[k] for k in a), sum(worth[k] for k in a)
                if bid_margin(club.cash, pa, club.team_value, va, bid, 0.0) < -1e-6:
                    continue
                rest = sorted(squad - a)
                won = [x for m in range(len(rest) + 1) for b in combinations(rest, m)
                       if cash_at_lock(club.cash, pa, bid, sum(price[k] for k in b)) >= -1e-6
                       and len(here - a - frozenset(b)) <= SQUAD_MAX and (x := points(here - a - frozenset(b))) is not None]
                lost = [x for m in range(len(rest) + 1) for b in combinations(rest, m)
                        if club.cash + pa + sum(price[k] for k in b) >= -1e-6 and (x := points(squad - a - frozenset(b))) is not None]
                if won and lost:
                    ev = win * max(won) + (1 - win) * max(lost)
                    want = ev if want is None else max(want, ev)
        assert got.feasible == (want is not None), (club.cash, bid, win)
        if not got.feasible:
            assert got.short >= 0 and got.tonight == frozenset()
            continue
        assert want is not None
        have = win * (with_star - got.cost_won) + (1 - win) * (base - got.cost_lost)
        assert abs(have - want) < 1e-6, (have, want)
        assert abs(got.net - (have - base)) < 1e-6 and abs(got.gain - (with_star - base)) < 1e-9
        assert got.tonight <= frozenset(offers) and not got.tonight & got.if_won and not got.tonight & got.if_lost
        assert got.margin >= -1e-6 and got.lock_margin >= -1e-6 and got.lock_margin_low <= got.lock_margin + 1e-9
        assert club.cash + got.raised + sum(price[k] for k in got.if_lost) >= -1e-6, "cash at the lock if the bid is lost"
        assert len(here - got.tonight - got.if_won) <= SQUAD_MAX
        assert got.held == frozenset(offers) - got.tonight
        assert eleven.best_eleven(here - got.tonight - got.if_won, pos, value) is not None, "an eleven remains"
        made += 1
    assert made >= 4, "the leagues are not all infeasible"
    nothing = Club(frozenset({"a"}), {"a": "POR", "s": "DEL"}, {"a": 1e6}, {"a": 1e6}, 0.0, 1e6)
    assert approach(nothing, "s", 1e6, 0.5, {"a": 1.0, "s": 5.0}) is None, "no eleven: nothing to say"
    print("approach self-test OK")


if __name__ == "__main__" and "--selftest" in sys.argv:
    _selftest()
