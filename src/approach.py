from __future__ import annotations

import random
import sys
from collections.abc import Iterable, Mapping
from functools import lru_cache, reduce
from itertools import combinations
from operator import or_
from typing import NamedTuple

import eleven
from ffcore.rules import DEBT_SHARE, SALE_ESTIMATE, SALE_SD, SQUAD_MAX, bid_margin, cash_at_lock

__all__ = ["Club", "Approach", "Settlement", "approach", "settle"]

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
    """The best eleven's points of a squad and what it costs to sell a set of its players, memoised on the set.
    A set of players is a bitmask over the squad (`bit`); `cost` takes the players, `cost_of` the mask.
    Only the first `eleven.DEPTH` survivors of each position can field, so sets that leave the same ones share a cost."""

    def __init__(self, squad: frozenset[str], pos: Mapping[str, str], value: Mapping[str, float]):
        self.value, self.ranked = value, eleven.rank(squad, pos, value)
        top = eleven.points_without(self.ranked, value, frozenset())
        self.fields = top is not None
        self.points = top or 0.0
        self.bit = {k: 1 << i for i, k in enumerate(sorted(squad))}
        self.lanes = {p: (reduce(or_, (self.bit[k] for k in ks), 0), ks) for p, ks in self.ranked.items()}
        self.lane_id: dict[tuple[str, int], int] = {}
        self.depths: dict[tuple[str, tuple[float, ...]], int] = {}
        self.fielding: list[tuple[float, ...]] = []
        self.shared: dict[tuple[int, ...], float] = {}
        self.memo: dict[int, float] = {}

    def mask(self, players: Iterable[str]) -> int:
        return reduce(or_, (self.bit[k] for k in players), 0)

    def players(self, mask: int) -> frozenset[str]:
        return frozenset(k for k, b in self.bit.items() if mask & b)

    def survivors(self, position: str, mask: int) -> int:
        lane, ranked = self.lanes[position]
        key = (position, mask & lane)
        got = self.lane_id.get(key)
        if got is None:
            fielding = tuple(self.value.get(k, 0.0) for k in ranked if not mask & self.bit[k])[:eleven.DEPTH[position]]
            got = self.lane_id[key] = self.depths.setdefault((position, fielding), len(self.fielding))
            if got == len(self.fielding):
                self.fielding.append(fielding)
        return got

    def cost_of(self, mask: int) -> float:
        got = self.memo.get(mask)
        if got is None:
            key = tuple([self.survivors(p, mask) for p in self.lanes])
            got = self.shared.get(key)
            if got is None:
                left = eleven.top_points({p: self.fielding[i] for p, i in zip(self.lanes, key)})
                got = self.shared[key] = INF if left is None else self.points - left
            self.memo[mask] = got
        return got

    def cost(self, gone: frozenset[str]) -> float:
        return self.cost_of(self.mask(gone))


def proceeds(club: Club, k: str) -> float:
    return club.offers.get(k, SALE_ESTIMATE * club.worth[k])


def cover(side: Side, fixed: frozenset[str], pool: frozenset[str], price: Mapping[str, float], need: float
          ) -> tuple[float, frozenset[str]] | None:
    """The cheapest set of `pool` to sell, on top of `fixed`, raising at least `need` at `price`, as (cost, set);
    None when no eleven remains or the pool cannot raise it."""
    held = side.mask(fixed)
    if need <= 1e-6:
        free = side.cost_of(held)
        return None if free == INF else (free, frozenset())
    cost_of, memo = side.cost_of, side.memo
    order = sorted((k for k in pool if price[k] > 0), key=lambda k: (cost_of(held | side.bit[k]) / price[k], k))
    bits = [side.bit[k] for k in order]
    worth = [price[k] for k in order]
    left = [sum(worth[i:]) for i in range(len(order) + 1)]
    best: tuple[float, int] | None = None
    stack: list[tuple[int, int, float]] = [(0, 0, 0.0)]
    while stack:
        i, gone, got = stack.pop()
        m = held | gone
        c = memo.get(m)
        if c is None:
            c = cost_of(m)
        if c == INF or best is not None and c >= best[0] - 1e-12:
            continue
        if got >= need - 1e-6:
            best = (c, gone)
            continue
        if i == len(order) or got + left[i] < need - 1e-6:
            continue
        stack.append((i + 1, gone, got))
        stack.append((i + 1, gone | bits[i], got + worth[i]))
    return None if best is None else (best[0], side.players(best[1]))


class Settlement(NamedTuple):
    feasible: bool
    sells: frozenset[str]
    raised: float
    lost: float
    short: float


def settle(club: Club, value: Mapping[str, float]) -> Settlement | None:
    """The cheapest standing offers to take so that cash is 0 or more at the lock with no buy: the players, the money
    they raise and the points the eleven loses (valued by `value`), by `cover`. Nothing to sell (empty set, 0 lost) when
    cash is 0 or more. Infeasible, with the sells empty and `short` the money the offers lack (0 when they suffice but
    would leave no eleven), when no set of them does it. None when the squad fields no eleven."""
    side = Side(club.squad, club.pos, value)
    if not side.fields:
        return None
    offered = frozenset(club.offers) & club.squad
    got = cover(side, frozenset(), offered, club.offers, -club.cash)
    if got is None:
        return Settlement(False, frozenset(), 0.0, 0.0, max(0.0, -club.cash - sum(club.offers[k] for k in offered)))
    return Settlement(True, got[1], sum(club.offers[k] for k in got[1]), got[0], 0.0)


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
    rng = random.Random(9)
    for _ in range(40):
        own = {"m%02d" % i: p for i, p in enumerate(["POR"] * 2 + ["DEF"] * 5 + ["MED"] * 5 + ["DEL"] * 3)}
        value = {k: rng.choice([0, 1, 2, 3, 5]) * 1.0 for k in own}
        squad = frozenset(rng.sample(sorted(own), rng.randint(11, len(own))))
        side = Side(squad, own, value)
        base = eleven.best_total(squad, own, value)
        assert side.fields == (base is not None) and (base is None or abs(side.points - base) < 1e-9)
        if base is None:
            continue
        for _ in range(12):
            gone = frozenset(rng.sample(sorted(squad), rng.randint(0, 5)))
            left = eleven.best_total(squad - gone, own, value)
            want = INF if left is None else base - left
            assert side.cost(gone) == INF if left is None else abs(side.cost(gone) - want) < 1e-9
        price = {k: rng.choice([1.0, 2.0, 3.0]) for k in squad}
        fixed = frozenset(rng.sample(sorted(squad), rng.randint(0, 2)))
        pool, need = squad - fixed, rng.choice([0.0, 2.0, 5.0, 9.0])
        covered = cover(side, fixed, pool, price, need)
        rest = sorted(pool)
        direct = [(side.points - left, set(b)) for m in range(len(rest) + 1) for b in combinations(rest, m)
                  if sum(price[k] for k in b) >= need - 1e-6
                  and (left := eleven.best_total(squad - fixed - frozenset(b), own, value)) is not None]
        assert (covered is None) == (not direct), "a cover exists exactly when some set of the pool raises the need and keeps an eleven"
        if covered is not None:
            assert abs(covered[0] - min(c for c, _ in direct)) < 1e-9, "the cover is the cheapest set"
            assert covered[1] <= pool and sum(price[k] for k in covered[1]) >= need - 1e-6 and abs(side.cost(fixed | covered[1]) - covered[0]) < 1e-9
    rng = random.Random(11)
    for _ in range(30):
        own = {"m%02d" % i: p for i, p in enumerate(["POR"] * 2 + ["DEF"] * 5 + ["MED"] * 5 + ["DEL"] * 3)}
        value = {k: rng.choice([0, 0, 1, 3, 5]) * 1.0 for k in own}
        worth = {k: rng.choice([2e6, 4e6, 6e6]) for k in own}
        offers = {k: worth[k] * rng.choice([0.2, 0.9, 1.1]) for k in rng.sample(sorted(own), 6)}
        cash = rng.choice([-9e6, -5e6, -1e6, 0.0, 2e6])
        club = Club(frozenset(own), own, worth, offers, cash, 5e6 * len(own))
        settled = settle(club, value)
        assert settled is not None
        if cash >= 0:
            assert settled == Settlement(True, frozenset(), 0.0, 0.0, 0.0), "cash 0 or more: nothing to sell"
            continue
        rest = sorted(offers)
        owed = [(eleven.best_total(club.squad, own, value) or 0.0) - left for m in range(len(rest) + 1) for b in combinations(rest, m)
                  if sum(offers[k] for k in b) >= -cash - 1e-6
                  and (left := eleven.best_total(club.squad - frozenset(b), own, value)) is not None]
        assert settled.feasible == bool(owed)
        if not settled.feasible:
            assert settled.sells == frozenset() and settled.short >= 0
            continue
        assert settled.sells <= frozenset(offers) and club.cash + settled.raised >= -1e-6, "the sales bring cash to 0 or more"
        assert abs(settled.lost - min(owed)) < 1e-9, "no cheaper set"
    nothing = Club(frozenset({"a"}), {"a": "POR", "s": "DEL"}, {"a": 1e6}, {"a": 1e6}, 0.0, 1e6)
    assert approach(nothing, "s", 1e6, 0.5, {"a": 1.0, "s": 5.0}) is None, "no eleven: nothing to say"
    print("approach self-test OK")


if __name__ == "__main__" and "--selftest" in sys.argv:
    _selftest()
