from __future__ import annotations

import random
import sys
from collections.abc import Iterable, Mapping
from functools import lru_cache, reduce
from itertools import combinations
from operator import or_
from typing import NamedTuple

import eleven
import hurdle
import valuation
from inflow import NONE, Inflow
from ffcore.rules import DEBT_SHARE, SALE_SD, SQUAD_MAX, bid_margin, cash_at_lock

__all__ = ["Club", "Rounds", "Approach", "Plan", "approach", "replacement", "plan", "prices"]


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
    inflow: Inflow = NONE


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
    base: float = 0.0


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

    def forgone(self, gone: frozenset[str]) -> float:
        """The points the sale of `gone` costs when a jornada with no legal eleven left scores nothing: the points of the squad
        less those of what is left, a jornada with no eleven left counting 0. Equals `cost` where every jornada keeps one."""
        mask = self.mask(gone)
        return self.points - sum(x for r in self.rounds if (x := r.left_of(mask)) is not None)


def per_unit(cost: float | None, price: float) -> tuple[bool, float]:
    """The sort key of a cost per unit of price: ascending by it puts every cost before a None (no eleven remaining)."""
    return cost is None, 0.0 if cost is None else cost / price


def prices(club: Club, bought: bool) -> dict[str, float]:
    """What each player of the squad raises when sold after the result of a bid (`bought`): the expected draw (hurdle.draw of
    his worth), since a standing offer expires at the close and is redrawn. With no bid (`bought` False) the standing offer where
    there is one, else the expected draw."""
    return {k: hurdle.draw(club.worth[k]) if bought else club.offers.get(k, hurdle.draw(club.worth[k])) for k in club.squad}


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


class Plan(NamedTuple):
    """The sales that bring cash to 0 or more at the lock with no buy: `sells` the players, `raised` what they
    raise at `prices`, `cost` the points the eleven loses. Infeasible (`short` the money the pool lacks, 0 when it suffices but
    would leave no eleven) when no set does it. Cash is counted at the lock with the Club's inflow at its expected."""
    feasible: bool
    sells: frozenset[str]
    raised: float
    cost: float
    short: float


def plan(club: Club, found: Approach | None, rounds: Rounds, n_closes: int) -> Plan | None:
    """The cheapest Plan with no buy, by `cover`. With closes still to come (`n_closes` 1 or more) it is `found`, the Approach with
    no player, whose sales are every player of the squad at his standing offer or the expected draw; with none to come (0) only
    the players with a standing offer are sold, at it. Empty and free when cash is 0 or more. None when the squad fields no eleven."""
    side = Side(club.squad, club.pos, rounds.cells, rounds.arrives)
    if not side.fields or found is None:
        return None
    price = prices(club, False)
    if n_closes > 0 and found.feasible:
        sells = found.tonight | found.if_lost
        return Plan(True, sells, sum(price[k] for k in sells), found.cost_lost, 0.0)
    pool = club.squad if n_closes > 0 else frozenset(club.offers) & club.squad
    got = None if n_closes > 0 else cover(side, frozenset(), pool, price, -(club.cash + club.inflow.expected))
    if got is None:
        return Plan(False, frozenset(), 0.0, 0.0, max(0.0, -(club.cash + club.inflow.expected) - sum(price[k] for k in pool)))
    return Plan(True, got[1], sum(price[k] for k in got[1]), got[0], 0.0)


def approach(club: Club, player: str | None, bid: float, win: float, rounds: Rounds, base: float = 0.0) -> Approach | None:
    """How to fund a bid on `player` (see Approach): the sales tonight, those after the result, their cost in points and the
    net. Costs are over `rounds`: a sale made tonight is gone from the squad in every jornada of `rounds.now`, the player
    bought plays only in `rounds.arrives`, and the sales after the result count only there. `base` is the cost of the
    cheapest plan with no buy (the cost of `approach` with player None, bid 0, win 1 and `rounds.arrives` = `rounds.now`:
    the gain 0, both costs the cost of the sales that bring cash to 0 or more), and the net is measured against it. None when
    the squad fields no eleven. Cash at the lock includes `club.inflow`: the sales must make it 0 or more at its expected, and
    `lock_margin` is shown at its expected, `lock_margin_low` at its low (with the sales that follow low too)."""
    lost_side = Side(club.squad, club.pos, rounds.cells, rounds.arrives)
    won_side = lost_side if player is None else Side(club.squad | {player}, club.pos, rounds.cells, rounds.arrives)
    before = Side(club.squad, club.pos, rounds.cells, rounds.before)
    if not lost_side.fields:
        return None
    gain = won_side.points - lost_side.points
    pend_n, pend_amount = club.pending
    price = prices(club, player is not None)
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
            need_won = -cash_at_lock(club.cash, raised, bid, 0.0, club.inflow.expected)
            need_lost = -(club.cash + raised + club.inflow.expected)
            won = cover(won_side, a, club.squad - a, price, need_won)
            lost = cover(lost_side, a, club.squad - a, price, need_lost)
            if won and lost and len(club.squad) + (player is not None) - len(a) - len(won[1]) + pend_n <= club.squad_max:
                ev = win * won[0] + (1 - win) * lost[0] + early
                if best is None or ev < best[0]:
                    best = (ev, a, won[1], lost[1], won[0] + early, lost[0] + early)
        elif margin + reach[i] < -1e-6:
            continue
        for j in range(i, len(offered)):
            stack.append((j + 1, a | {offered[j]}))
    if best is None:
        return unfunded(club, max(0.0, -ceiling), ceiling, gain)._replace(base=base)
    _, a, b, ell, cost_won, cost_lost = best
    return funded(club, a, b, ell, bid, win, (gain, cost_won, cost_lost), base, price)


def unfunded(club: Club, short: float, margin: float, gain: float) -> Approach:
    """The Approach of a bid no set of the standing offers funds: `short` the money lacking, `margin` the bid margin."""
    return Approach(False, short, frozenset(), frozenset(club.offers) & club.squad, frozenset(), frozenset(), 0.0, margin,
                    0.0, 0.0, 0.0, gain, 0.0, 0.0, 0.0)


def funded(club: Club, tonight: frozenset[str], if_won: frozenset[str], if_lost: frozenset[str], bid: float, win: float,
           points: tuple[float, float, float], base: float, price: Mapping[str, float]) -> Approach:
    """The feasible Approach that sells `tonight` to their offers, then `if_won` or `if_lost` at `price` once the result is
    known; `points` the gain of the buy and the costs of the sales if it is won and if it is lost; `base` the cost of the plan
    with no buy; the net is win * (gain - (cost_won - base)) - (1 - win) * (cost_lost - base): the points the bid adds
    to doing nothing."""
    gain, cost_won, cost_lost = points
    raised = sum(club.offers[k] for k in tonight)
    later = sum(price[k] for k in if_won)
    return Approach(
        True, 0.0, tonight, (frozenset(club.offers) & club.squad) - tonight, if_won, if_lost, raised,
        bid_margin(club.cash, raised, club.team_value, sum(club.worth[k] for k in tonight), bid, club.pending[1], club.debt),
        cash_at_lock(club.cash, raised, bid, later, club.inflow.expected),
        cash_at_lock(club.cash, raised, bid, later * (1 - SALE_SD), club.inflow.low),
        club.cash + raised + sum(price[k] for k in if_lost) + club.inflow.expected,
        gain, cost_won, cost_lost, win * (gain - (cost_won - base)) - (1 - win) * (cost_lost - base), base)


def replacement(club: Club, sold: str, player: str, bid: float, win: float, rounds: Rounds) -> Approach:
    """How to replace `sold` (a player whose offer is taken tonight and whose sale leaves no legal eleven) by a bid on `player`:
    the Approach of the pair, `sold` the only sale tonight, the sales after the result chosen by `cover`. A jornada with no
    legal eleven scores nothing, so costs count it: before the buy arrives, and if the bid is lost, the squad plays nothing
    where it has no eleven. Infeasible (`short` the money lacking, 0 when the squad or the lock cash cannot be met) when the
    bid is not covered by the offer and the sales after the result, or the squad would exceed its maximum."""
    pend_n, pend_amount = club.pending
    tonight = frozenset({sold})
    raised = club.offers[sold]
    margin = bid_margin(club.cash, raised, club.team_value, club.worth[sold], bid, pend_amount, club.debt)
    if margin < -1e-6 or club.cash + raised + club.inflow.expected < -1e-6:
        return unfunded(club, max(0.0, -margin, -(club.cash + raised + club.inflow.expected)), margin, 0.0)
    won_side = Side(club.squad | {player}, club.pos, rounds.cells, rounds.arrives)
    lost_side = Side(club.squad, club.pos, rounds.cells, rounds.arrives)
    before = Side(club.squad, club.pos, rounds.cells, rounds.before)
    gain = won_side.points - lost_side.points
    price = prices(club, True)
    won = cover(won_side, tonight, club.squad - tonight, price, -cash_at_lock(club.cash, raised, bid, 0.0, club.inflow.expected))
    if won is None or len(club.squad) - len(tonight) - len(won[1]) + pend_n + 1 > club.squad_max:
        return unfunded(club, 0.0, margin, gain)
    early = before.forgone(tonight)
    return funded(club, tonight, won[1], frozenset(), bid, win,
                  (gain, won_side.forgone(tonight | won[1]) + early, lost_side.forgone(tonight) + early), 0.0, price)


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
        price = {k: hurdle.draw(worth[k]) for k in squad}

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
        flat = Rounds(cells, valuation.Window(window), valuation.Window(window))
        found = approach(club, None, 0.0, 1.0, flat)
        planned = plan(club, found, flat, 0)
        assert planned is not None and found is not None
        if cash >= 0:
            assert planned == Plan(True, frozenset(), 0.0, 0.0, 0.0), "cash 0 or more: nothing to sell"
            assert plan(club, found, flat, 3) == planned
            continue
        rest = sorted(offers)
        owed = [(total(club.squad, own, cells, window) or 0.0) - left for m in range(len(rest) + 1) for b in combinations(rest, m)
                if sum(offers[k] for k in b) >= -cash - 1e-6
                and (left := total(club.squad - frozenset(b), own, cells, window)) is not None]
        assert planned.feasible == bool(owed)
        if planned.feasible:
            assert planned.sells <= frozenset(offers) and club.cash + planned.raised >= -1e-6, "the sales bring cash to 0 or more"
            assert abs(planned.cost - min(owed)) < 1e-9, "no close left: the cheapest set of the standing offers"
        else:
            assert planned.sells == frozenset() and planned.short >= 0
        later = plan(club, found, flat, 3)
        assert later is not None and (later.feasible == found.feasible)
        if later.feasible:
            assert later.sells == found.tonight | found.if_lost and abs(later.cost - found.cost_lost) < 1e-9
            assert later.cost <= (planned.cost if planned.feasible else later.cost) + 1e-9, "more closes to come never cost more points"
    rng = random.Random(21)
    for case in range(30):
        squad_pos = {"m%02d" % i: p for i, p in enumerate(["POR"] + ["DEF"] * 4 + ["MED"] * 4 + ["DEL"] * 2)}
        leaver = rng.choice(sorted(squad_pos))
        places = squad_pos | {"new": squad_pos[leaver]}
        forecast = _cells(rng, places, (1, 2, 3), [0, 1, 2, 3, 5])
        now = valuation.Window((1, 2, 3))
        rounds = Rounds(forecast, now, valuation.Window(now[case % 3:]))
        values = {k: 4e6 for k in squad_pos}
        club = Club(frozenset(squad_pos), places, values, {leaver: 5e6}, 20e6, 5e7)
        win, bid = rng.choice([0.4, 0.8, 1.0]), 6e6
        got = replacement(club, leaver, "new", bid, win, rounds)
        squad = frozenset(squad_pos)

        def played(sq: frozenset[str], js: Iterable[int]) -> float:
            return sum(eleven.best_total(sq, places, forecast[j]) or 0.0 for j in js)
        base = played(squad, now)
        won_total = played((squad - {leaver}) | {"new"}, rounds.arrives) + played(squad - {leaver}, rounds.before)
        lost_total = played(squad - {leaver}, now)
        assert got.feasible and got.tonight == frozenset({leaver}) and not got.if_lost
        assert abs(got.net - (win * (won_total - base) + (1 - win) * (lost_total - base))) < 1e-6, \
            "the pair's net: the jornadas with no legal eleven score nothing, whether the bid is won or lost"
        assert abs(got.gain - (played(squad | {"new"}, rounds.arrives) - played(squad, rounds.arrives))) < 1e-9
        short = replacement(club._replace(cash=-20e6), leaver, "new", bid, win, rounds)
        assert not short.feasible and short.short > 0 and short.tonight == frozenset(), "the offer does not cover the bid"
    rng = random.Random(33)
    seen_base = seen_free = 0
    for case in range(24):
        own = {"m%02d" % i: p for i, p in enumerate(["POR"] * 2 + ["DEF"] * 5 + ["MED"] * 5 + ["DEL"] * 3)}
        places = own | {"star": rng.choice(["DEF", "MED", "DEL"])}
        window = valuation.Window((1, 2, 3))
        forecast = _cells(rng, places, window, [0, 1, 2, 3, 5])
        rounds = Rounds(forecast, window, valuation.Window(window[case % 3:]))
        flat = Rounds(forecast, window, window)
        worth = {k: rng.choice([2e6, 4e6, 6e6]) for k in own}
        offers = {k: worth[k] * rng.choice([0.9, 1.0, 1.05]) for k in rng.sample(sorted(own), 5)}
        cash = rng.choice([-9e6, -5e6, -1e6, 0.0, 3e6, 30e6, 80e6])
        club = Club(frozenset(own), places, worth, offers, cash, 5e6 * len(own))
        none = approach(club, None, 0.0, 1.0, flat)
        assert none is not None and none.gain == 0.0
        if cash >= 0:
            assert none.feasible and none.cost_won == none.cost_lost == 0.0 and not none.tonight and not none.if_lost, \
                "cash 0 or more: the plan with no buy sells nothing and costs nothing"
        elif none.feasible:
            assert none.cost_won == none.cost_lost >= 0.0 and club.cash + none.raised + sum(prices(club, False)[k] for k in none.if_lost) >= -1e-6
            full = Side(club.squad, places, forecast, window)
            price = prices(club, False)
            rest = sorted(own)
            owed = [c for n in range(5) for b in combinations(rest, n)
                    if sum(price[k] for k in b) >= -cash - 1e-6 and (c := full.cost(frozenset(b))) is not None]
            assert abs(none.cost_lost - min(owed)) < 1e-9, "the baseline is the cheapest set of sales that brings cash to 0 or more"
            seen_base += 1
        c0 = none.cost_lost if none.feasible else 0.0
        bid, win = rng.choice([8e6, 12e6, 16e6]), rng.choice([0.3, 0.7, 0.95])
        gross = approach(club, "star", bid, win, rounds)
        net = approach(club, "star", bid, win, rounds, c0)
        assert gross is not None and net is not None and gross.feasible == net.feasible
        if not net.feasible:
            continue
        assert abs(net.net - (gross.net + c0)) < 1e-9 and (net.gain, net.cost_won, net.cost_lost) == (gross.gain, gross.cost_won, gross.cost_lost), \
            "the net is measured against the plan with no buy: the same plan, the gross net plus its cost"
        assert net.net <= net.gain + c0 + 1e-9, "a buy adds at most its gain to doing nothing, less what it costs to fund"
        if cash >= bid and not net.tonight and not net.if_won and not net.if_lost:
            assert abs(net.net - win * net.gain) < 1e-9, "no funding needed: the net is the chance of winning times the gain"
            seen_free += 1
    assert seen_base >= 3, "the cases with cash below 0 and a baseline were seen"
    assert seen_free >= 3, "the cases with no funding needed were seen"
    rng = random.Random(41)
    seen_free_by_credit = seen_cheaper = seen_buy = 0
    for case in range(40):
        own = {"m%02d" % i: p for i, p in enumerate(["POR"] * 2 + ["DEF"] * 5 + ["MED"] * 5 + ["DEL"] * 3)}
        places = own | {"star": rng.choice(["DEF", "MED", "DEL"])}
        window = valuation.Window((1, 2, 3))
        forecast = _cells(rng, places, window, [0, 1, 2, 3, 5])
        flat = Rounds(forecast, window, window)
        rounds = Rounds(forecast, window, valuation.Window(window[case % 3:]))
        worth = {k: rng.choice([2e6, 4e6, 6e6]) for k in own}
        offers = {k: worth[k] * rng.choice([0.9, 1.0, 1.05]) for k in rng.sample(sorted(own), 5)}
        cash = rng.choice([-9e6, -5e6, -1e6, 0.0, 3e6, 30e6])
        club = Club(frozenset(own), places, worth, offers, cash, 5e6 * len(own))
        expected = rng.choice([1e6, 4e6, 8e6, 12e6])
        spread = rng.choice([0.0, 1e6, 3e6])
        credit = Inflow(expected, max(0.0, expected - spread), "2026-11-24T03:40Z", (13,))
        none = approach(club, None, 0.0, 1.0, flat)
        got = approach(club._replace(inflow=credit), None, 0.0, 1.0, flat)
        assert none is not None and got is not None
        assert none.feasible <= got.feasible, "a plan feasible with no credit is feasible with one"
        if none.feasible and got.feasible:
            assert got.cost_lost <= none.cost_lost + 1e-9, "the cost of the plan never rises with the credit"
            seen_cheaper += got.cost_lost < none.cost_lost - 1e-9
        if cash + credit.expected >= 0:
            assert got.feasible and got.cost_lost == 0.0 and not got.tonight and not got.if_lost, \
                "cash at the lock, the expected credit counted, is 0 or more: nothing to sell"
            seen_free_by_credit += cash < 0
        if got.feasible:
            assert got.lock_margin >= -1e-6 and got.cash_if_lost >= -1e-6, "a plan feasible is feasible at the expected credit"
            assert club.cash + got.raised + sum(prices(club, False)[k] for k in got.if_lost) + credit.expected >= -1e-6
        planned = plan(club._replace(inflow=credit), got, flat, 0)
        before = plan(club, none, flat, 0)
        assert planned is not None and before is not None
        assert before.feasible <= planned.feasible and (not (before.feasible and planned.feasible) or planned.cost <= before.cost + 1e-9), \
            "no closes left: the plan costs no more as the credit grows"
        if cash >= 0:
            assert planned == before == Plan(True, frozenset(), 0.0, 0.0, 0.0), "cash 0 or more: the credit changes no plan"
        bid, win = rng.choice([8e6, 12e6, 16e6]), rng.choice([0.3, 0.7, 0.95])
        flatten = approach(club, "star", bid, win, rounds, 0.0)
        credited = approach(club._replace(inflow=credit), "star", bid, win, rounds, 0.0)
        assert flatten is not None and credited is not None and flatten.feasible <= credited.feasible
        if flatten.feasible and credited.feasible:
            assert credited.net >= flatten.net - 1e-9, "the net of a buy never falls with the credit"
            seen_buy += 1
        if cash >= bid and credited.feasible:
            assert (credited.tonight, credited.if_won, credited.if_lost, credited.net) == (flatten.tonight, flatten.if_won, flatten.if_lost, flatten.net), \
                "cash covers the bid: the credit is irrelevant"
        assert approach(club._replace(inflow=NONE), "star", bid, win, rounds, 0.0) == flatten, "no credit due: the output is unchanged"
        tied = replacement(club._replace(inflow=credit), sorted(offers)[0], "star", bid, win, rounds)
        untied = replacement(club, sorted(offers)[0], "star", bid, win, rounds)
        assert untied.feasible <= tied.feasible, "a pair feasible with no credit is feasible with one"
    assert seen_free_by_credit >= 3 and seen_cheaper >= 3 and seen_buy >= 5, "the credit freed a plan, cheapened one and funded buys"
    nothing = Club(frozenset({"a"}), {"a": "POR", "s": "DEL"}, {"a": 1e6}, {"a": 1e6}, 0.0, 1e6)
    day = {1: {"a": 1.0, "s": 5.0}}
    assert approach(nothing, "s", 1e6, 0.5, Rounds(day, valuation.Window((1,)), valuation.Window((1,)))) is None, "no eleven: nothing to say"
    print("approach self-test OK")


if __name__ == "__main__" and "--selftest" in sys.argv:
    _selftest()
