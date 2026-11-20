from __future__ import annotations

import random
import sys
from collections.abc import Iterable, Mapping
from functools import lru_cache, reduce
from itertools import combinations
from operator import or_
from typing import NamedTuple

import eleven
import valuation
from ffcore.rules import DEBT_SHARE, SALE_ESTIMATE, SALE_SD, SQUAD_MAX, bid_margin, cash_at_lock

__all__ = ["Club", "Rounds", "Approach", "Settlement", "approach", "settle"]


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


class Rounds(NamedTuple):
    """The jornadas a bid is valued over and the forecast of each: `now` those a sale made now counts for, `arrives` those
    a buy won counts for (inside `now`: a buy closing after a lock does not count for it), `cells` each jornada's forecast
    per player."""
    cells: valuation.Cells
    now: valuation.Window
    arrives: valuation.Window

    @property
    def before(self) -> tuple[int, ...]:
        """The jornadas of `now` the buy is not yet in force for: a sale that funds it leaves the squad less the sold."""
        return tuple(j for j in self.now if j not in self.arrives)


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


class Round:
    """The best eleven's points of the squad that is left when a set of its players (a bitmask over the squad, `bit`) is
    sold, in one jornada of forecast `value`, memoised on the set; None when no legal eleven is left. Only the first
    `eleven.DEPTH` survivors of each position can field, so sets that leave the same ones share a value."""

    def __init__(self, squad: frozenset[str], pos: Mapping[str, str], value: Mapping[str, float], bit: Mapping[str, int]):
        self.pos, self.value, self.bit = pos, value, bit
        self.points = eleven.points(squad, pos, value) or 0.0
        self.lanes = {p: (reduce(or_, (bit[k] for k in ks), 0), ks) for p, ks in eleven.rank(squad, pos, value).items()}
        self.lane_id: dict[tuple[str, int], int] = {}
        self.depths: dict[tuple[str, tuple[float, ...]], int] = {}
        self.fielding: list[tuple[str, ...]] = []
        self.shared: dict[tuple[int, ...], float | None] = {}

    def survivors(self, position: str, mask: int) -> int:
        lane, ranked = self.lanes[position]
        key = (position, mask & lane)
        got = self.lane_id.get(key)
        if got is None:
            fielding = tuple(k for k in ranked if not mask & self.bit[k])[:eleven.DEPTH[position]]
            got = self.lane_id[key] = self.depths.setdefault((position, tuple(self.value.get(k, 0.0) for k in fielding)), len(self.fielding))
            if got == len(self.fielding):
                self.fielding.append(fielding)
        return got

    def left_of(self, mask: int) -> float | None:
        key = tuple([self.survivors(p, mask) for p in self.lanes])
        if key not in self.shared:
            self.shared[key] = eleven.points(frozenset(k for i in key for k in self.fielding[i]), self.pos, self.value)
        return self.shared[key]


class Side:
    """The points of a squad over a window (V_j of each jornada summed, the eleven picked afresh in each) and what it
    costs to sell a set of its players, memoised on the set. A set of players is a bitmask over the squad (`bit`); `cost`
    takes the players, `cost_of` the mask, both None when no legal eleven remains (in every jornada alike). The cost is the
    points of the squad less those of what is left, summed over the window, nothing else."""

    def __init__(self, squad: frozenset[str], pos: Mapping[str, str], cells: valuation.Cells, window: Iterable[int]):
        self.fields = eleven.points(squad, pos, {}) is not None
        self.bit = {k: 1 << i for i, k in enumerate(sorted(squad))}
        self.rounds = [Round(squad, pos, cells[j], self.bit) for j in window]
        self.points = sum(r.points for r in self.rounds) if self.fields else 0.0
        self.memo: dict[int, float | None] = {}

    def mask(self, players: Iterable[str]) -> int:
        return reduce(or_, (self.bit[k] for k in players), 0)

    def players(self, mask: int) -> frozenset[str]:
        return frozenset(k for k, b in self.bit.items() if mask & b)

    def cost_of(self, mask: int) -> float | None:
        if mask in self.memo:
            return self.memo[mask]
        left = [r.left_of(mask) for r in self.rounds]
        got = self.memo[mask] = None if None in left else self.points - sum(x for x in left if x is not None)
        return got

    def cost(self, gone: frozenset[str]) -> float | None:
        return self.cost_of(self.mask(gone))


def per_unit(cost: float | None, price: float) -> tuple[bool, float]:
    """The sort key of a cost per unit of price: ascending by it puts every cost before a None (no eleven remaining)."""
    return cost is None, 0.0 if cost is None else cost / price


def proceeds(club: Club, k: str) -> float:
    return club.offers.get(k, SALE_ESTIMATE * club.worth[k])


def cover(side: Side, fixed: frozenset[str], pool: frozenset[str], price: Mapping[str, float], need: float
          ) -> tuple[float, frozenset[str]] | None:
    """The cheapest set of `pool` to sell, on top of `fixed`, raising at least `need` at `price`, as (cost, set);
    None when no eleven remains or the pool cannot raise it."""
    held = side.mask(fixed)
    if need <= 1e-6:
        free = side.cost_of(held)
        return None if free is None else (free, frozenset())
    cost_of, memo = side.cost_of, side.memo
    alone = {k: cost_of(held | side.bit[k]) for k in pool if price[k] > 0}
    order = sorted(alone, key=lambda k: (per_unit(alone[k], price[k]), k))
    bits = [side.bit[k] for k in order]
    worth = [price[k] for k in order]
    left = [sum(worth[i:]) for i in range(len(order) + 1)]
    best: tuple[float, int] | None = None
    stack: list[tuple[int, int, float]] = [(0, 0, 0.0)]
    while stack:
        i, gone, got = stack.pop()
        m = held | gone
        c = memo[m] if m in memo else cost_of(m)
        if c is None or best is not None and c >= best[0] - 1e-12:
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


def settle(club: Club, cells: valuation.Cells, window: Iterable[int]) -> Settlement | None:
    """The cheapest standing offers to take so that cash is 0 or more at the lock with no buy: the players, the money
    they raise and the points the eleven loses (over the window, the eleven picked in each jornada from `cells`), by `cover`. Nothing to sell (empty set, 0 lost) when
    cash is 0 or more. Infeasible, with the sells empty and `short` the money the offers lack (0 when they suffice but
    would leave no eleven), when no set of them does it. None when the squad fields no eleven."""
    side = Side(club.squad, club.pos, cells, window)
    if not side.fields:
        return None
    offered = frozenset(club.offers) & club.squad
    got = cover(side, frozenset(), offered, club.offers, -club.cash)
    if got is None:
        return Settlement(False, frozenset(), 0.0, 0.0, max(0.0, -club.cash - sum(club.offers[k] for k in offered)))
    return Settlement(True, got[1], sum(club.offers[k] for k in got[1]), got[0], 0.0)


def approach(club: Club, player: str, bid: float, win: float, rounds: Rounds) -> Approach | None:
    """How to fund a bid on `player` (see Approach): the sales tonight, those after the result, their cost in points and the
    net. Costs are over `rounds`: a sale made tonight is gone from the squad in every jornada of `rounds.now`, the player
    bought plays only in `rounds.arrives`, and the sales after the result count only there. None when the squad fields
    no eleven."""
    lost_side = Side(club.squad, club.pos, rounds.cells, rounds.arrives)
    won_side = Side(club.squad | {player}, club.pos, rounds.cells, rounds.arrives)
    before = Side(club.squad, club.pos, rounds.cells, rounds.before)
    if not lost_side.fields:
        return None
    gain = won_side.points - lost_side.points
    pend_n, pend_amount = club.pending
    price = {k: proceeds(club, k) for k in club.squad}
    offered = sorted((k for k in club.squad if k in club.offers),
                     key=lambda k: (per_unit(won_side.cost(frozenset({k})), max(club.offers[k], 1.0)), k))
    reach = [sum(club.offers[k] - club.debt * club.worth[k] for k in offered[i:]) for i in range(len(offered) + 1)]
    best: tuple[float, frozenset[str], frozenset[str], frozenset[str], float, float] | None = None
    ceiling = bid_margin(club.cash, sum(club.offers[k] for k in offered), club.team_value,
                         sum(club.worth[k] for k in offered), bid, pend_amount, club.debt)
    stack: list[tuple[int, frozenset[str]]] = [(0, frozenset())]
    while stack:
        i, a = stack.pop()
        cw, cl = won_side.cost(a), lost_side.cost(a)
        early = before.cost(a)
        if cw is None or cl is None or early is None or best is not None and win * cw + (1 - win) * cl + early >= best[0] - 1e-12:
            continue
        raised, sold = sum(club.offers[k] for k in a), sum(club.worth[k] for k in a)
        margin = bid_margin(club.cash, raised, club.team_value, sold, bid, pend_amount, club.debt)
        if margin >= -1e-6:
            need_won = -cash_at_lock(club.cash, raised, bid, 0.0)
            need_lost = -(club.cash + raised)
            won = cover(won_side, a, club.squad - a, price, need_won)
            lost = cover(lost_side, a, club.squad - a, price, need_lost)
            if won and lost and len(club.squad) + 1 - len(a) - len(won[1]) + pend_n <= club.squad_max:
                ev = win * won[0] + (1 - win) * lost[0] + early
                if best is None or ev < best[0]:
                    best = (ev, a, won[1], lost[1], won[0] + early, lost[0] + early)
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


def _cells(rng: random.Random, players: Iterable[str], jornadas: Iterable[int], pick: Iterable[float]) -> dict[int, dict[str, float]]:
    return {j: {k: rng.choice(list(pick)) for k in players} for j in jornadas}


def _selftest() -> None:
    rng = random.Random(5)
    made = 0
    seen_early = 0
    for case in range(16):
        own = {"m%02d" % i: p for i, p in enumerate(["POR"] + ["DEF"] * 5 + ["MED"] * 5 + ["DEL"] * 3)}
        pos = own | {"star": "DEL"}
        cells = _cells(rng, own, (1, 2, 3), [0, 1, 2, 3, 5]) 
        cells = {j: {k: v * 2.0 for k, v in c.items()} | {"star": 18.0} for j, c in cells.items()}
        now = valuation.Window((1, 2, 3))
        arrives = valuation.Window(now[case % 3:])
        rounds = Rounds(cells, now, arrives)
        assert rounds.before == now[:case % 3]
        worth = {k: rng.choice([2e6, 4e6, 6e6]) for k in own}
        offers = {k: worth[k] * rng.choice([0.9, 1.0, 1.05]) for k in rng.sample(sorted(own), 4)}
        club = Club(frozenset(own), pos, worth, offers, rng.choice([-5e6, -1e6, 3e6]), 5e6 * len(own))
        bid, win = rng.choice([8e6, 12e6, 16e6]), rng.choice([0.3, 0.7, 0.95])
        got = approach(club, "star", bid, win, rounds)
        assert got is not None
        squad, here = club.squad, club.squad | {"star"}
        price = {k: proceeds(club, k) for k in squad}

        @lru_cache(maxsize=None)
        def points(sq: frozenset[str], js: tuple[int, ...]) -> float | None:
            seen = [eleven.best_total(sq, pos, cells[j]) for j in js]
            return None if None in seen else sum(x for x in seen if x is not None)
        base_t, base_w, with_star = points(squad, tuple(now)), points(squad, tuple(arrives)), points(here, tuple(arrives))
        assert base_t is not None and base_w is not None and with_star is not None

        def after_sales(a: frozenset[str], b: frozenset[str], star: bool) -> float | None:
            early = points(squad - a, rounds.before)
            late = points((here if star else squad) - a - b, tuple(arrives))
            return None if early is None or late is None else early + late
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
                       and len(here - a - frozenset(b)) <= SQUAD_MAX and (x := after_sales(a, frozenset(b), True)) is not None]
                lost = [x for m in range(len(rest) + 1) for b in combinations(rest, m)
                        if club.cash + pa + sum(price[k] for k in b) >= -1e-6 and (x := after_sales(a, frozenset(b), False)) is not None]
                if won and lost:
                    ev = win * max(won) + (1 - win) * max(lost)
                    want = ev if want is None else max(want, ev)
        assert got.feasible == (want is not None), (club.cash, bid, win)
        if not got.feasible:
            assert got.short >= 0 and got.tonight == frozenset()
            continue
        assert want is not None
        have = win * (base_t + with_star - base_w - got.cost_won) + (1 - win) * (base_t - got.cost_lost)
        assert abs(have - want) < 1e-6, (have, want)
        assert abs(got.net - (have - base_t)) < 1e-6 and abs(got.gain - (with_star - base_w)) < 1e-9
        assert got.cost_won >= -1e-9 and got.cost_lost >= -1e-9
        seen_early += bool(rounds.before and got.tonight)
        assert got.tonight <= frozenset(offers) and not got.tonight & got.if_won and not got.tonight & got.if_lost
        assert got.margin >= -1e-6 and got.lock_margin >= -1e-6 and got.lock_margin_low <= got.lock_margin + 1e-9
        assert club.cash + got.raised + sum(price[k] for k in got.if_lost) >= -1e-6, "cash at the lock if the bid is lost"
        assert len(here - got.tonight - got.if_won) <= SQUAD_MAX
        assert got.held == frozenset(offers) - got.tonight
        assert eleven.best_eleven(here - got.tonight - got.if_won, pos, cells[arrives[0]]) is not None, "an eleven remains"
        made += 1
    assert made >= 4, "the leagues are not all infeasible"
    assert seen_early >= 1, "a sale tonight with the buy arriving after a lock was costed"
    rng = random.Random(9)

    def total(sq: frozenset[str], pos: Mapping[str, str], cells: valuation.Cells, window: Iterable[int]) -> float | None:
        seen = [eleven.best_total(sq, pos, cells[j]) for j in window]
        return None if None in seen else sum(x for x in seen if x is not None)
    for _ in range(40):
        own = {"m%02d" % i: p for i, p in enumerate(["POR"] * 2 + ["DEF"] * 5 + ["MED"] * 5 + ["DEL"] * 3)}
        cells = _cells(rng, own, (1, 2, 3, 4), [0, 1, 2, 3, 5])
        window = tuple(j for j in cells if rng.random() < 0.6) or (2,)
        squad = frozenset(rng.sample(sorted(own), rng.randint(11, len(own))))
        side = Side(squad, own, cells, window)
        base = total(squad, own, cells, window)
        assert side.fields == (base is not None) and (base is None or abs(side.points - base) < 1e-9)
        if base is None:
            continue
        for _ in range(12):
            gone = frozenset(rng.sample(sorted(squad), rng.randint(0, 5)))
            left = total(squad - gone, own, cells, window)
            paid = side.cost(gone)
            assert (paid is None) if left is None else paid is not None and abs(paid - (base - left)) < 1e-9
            kept = eleven.points(squad - gone, own, {})
            assert (paid is None) == (kept is None), "the cost is None exactly when no legal eleven is left"
        price = {k: rng.choice([1.0, 2.0, 3.0]) for k in squad}
        fixed = frozenset(rng.sample(sorted(squad), rng.randint(0, 2)))
        pool, need = squad - fixed, rng.choice([0.0, 2.0, 5.0, 9.0])
        covered = cover(side, fixed, pool, price, need)
        rest = sorted(pool)
        direct = [(side.points - left, set(b)) for m in range(len(rest) + 1) for b in combinations(rest, m)
                  if sum(price[k] for k in b) >= need - 1e-6
                  and (left := total(squad - fixed - frozenset(b), own, cells, window)) is not None]
        assert (covered is None) == (not direct), "a cover exists exactly when some set of the pool raises the need and keeps an eleven"
        if covered is not None:
            assert abs(covered[0] - min(c for c, _ in direct)) < 1e-9, "the cover is the cheapest set"
            kept = side.cost(fixed | covered[1])
            assert covered[1] <= pool and sum(price[k] for k in covered[1]) >= need - 1e-6 and kept is not None and abs(kept - covered[0]) < 1e-9
    rng = random.Random(11)
    for _ in range(30):
        own = {"m%02d" % i: p for i, p in enumerate(["POR"] * 2 + ["DEF"] * 5 + ["MED"] * 5 + ["DEL"] * 3)}
        cells = _cells(rng, own, (1, 2, 3), [0, 0, 1, 3, 5])
        window = (1, 2, 3)
        worth = {k: rng.choice([2e6, 4e6, 6e6]) for k in own}
        offers = {k: worth[k] * rng.choice([0.2, 0.9, 1.1]) for k in rng.sample(sorted(own), 6)}
        cash = rng.choice([-9e6, -5e6, -1e6, 0.0, 2e6])
        club = Club(frozenset(own), own, worth, offers, cash, 5e6 * len(own))
        settled = settle(club, cells, window)
        assert settled is not None
        if cash >= 0:
            assert settled == Settlement(True, frozenset(), 0.0, 0.0, 0.0), "cash 0 or more: nothing to sell"
            continue
        rest = sorted(offers)
        owed = [(total(club.squad, own, cells, window) or 0.0) - left for m in range(len(rest) + 1) for b in combinations(rest, m)
                  if sum(offers[k] for k in b) >= -cash - 1e-6
                  and (left := total(club.squad - frozenset(b), own, cells, window)) is not None]
        assert settled.feasible == bool(owed)
        if not settled.feasible:
            assert settled.sells == frozenset() and settled.short >= 0
            continue
        assert settled.sells <= frozenset(offers) and club.cash + settled.raised >= -1e-6, "the sales bring cash to 0 or more"
        assert abs(settled.lost - min(owed)) < 1e-9, "no cheaper set"
    nothing = Club(frozenset({"a"}), {"a": "POR", "s": "DEL"}, {"a": 1e6}, {"a": 1e6}, 0.0, 1e6)
    day = {1: {"a": 1.0, "s": 5.0}}
    assert approach(nothing, "s", 1e6, 0.5, Rounds(day, valuation.Window((1,)), valuation.Window((1,)))) is None, "no eleven: nothing to say"
    print("approach self-test OK")


if __name__ == "__main__" and "--selftest" in sys.argv:
    _selftest()
