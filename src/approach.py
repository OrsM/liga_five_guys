from __future__ import annotations

import heapq
import math
import random
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from functools import lru_cache, reduce
from itertools import combinations
from operator import or_
from statistics import NormalDist
from typing import NamedTuple

import eleven
import hurdle
import valuation
import worth
from ffcore.rules import DEBT_SHARE, SALE_ESTIMATE, SALE_SD, SQUAD_MAX, bid_margin, cash_at_lock
from inflow import NONE, Inflow
from worth import NO_OFFERS, offer_ratio_distribution

__all__ = ["Club", "Rounds", "Approach", "Plan", "Option", "Reserve", "Atom", "Closer", "Covered", "Filled", "Pricing", "approach", "replacement", "plan", "secure", "prices", "pricing_of", "ratios_of", "sold_at"]


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


class Pricing(NamedTuple):
    """What a player sells for, in money: his `standing` offer (None when he has none), the `expected` price of a draw (hurdle.draw of his
    worth), the `low` price (his worth at the FALLBACK_LOW quantile of the offer ratios) and the `best` (the highest offer ratio seen, or his
    standing offer when that is more). `now` is the standing offer where there is one, else the expected price."""
    standing: float | None
    expected: float
    low: float
    best: float

    @property
    def now(self) -> float:
        return self.expected if self.standing is None else self.standing


def pricing_of(club: Club, ratios: Sequence[float] = ()) -> dict[str, Pricing]:
    """The Pricing of each player of the squad; `ratios` (ascending) the offer ratios the low and best prices are read from (the
    expected price for both when none are given)."""
    out = {}
    for k in club.squad:
        value, standing = club.worth[k], club.offers.get(k)
        expected = hurdle.draw(value)
        out[k] = Pricing(standing, expected, ratios[len(ratios) // FALLBACK_LOW] * value if ratios else expected,
                         max(standing or 0.0, ratios[-1] * value) if ratios else max(standing or 0.0, expected))
    return out


def prices(club: Club, bought: bool) -> dict[str, float]:
    """What each player of the squad raises when sold after the result of a bid (`bought`): the expected draw (hurdle.draw of
    his worth), since a standing offer expires at the close and is redrawn. With no bid (`bought` False) the standing offer where
    there is one, else the expected draw."""
    return {k: p.expected if bought else p.now for k, p in pricing_of(club).items()}


class Covered(NamedTuple):
    """A set of players to sell (`sells`) and the points it loses with those already sold (`cost`)."""
    cost: float
    sells: frozenset[str]


class Branch(NamedTuple):
    """A step of the search of `cover`: the players from index `i` of the order are still to decide, `gone` (a bitmask) are sold and raise `got`."""
    i: int
    gone: int
    got: float


class Chosen(NamedTuple):
    """The best set `cover` has found: its `cost` in points and who is sold (`gone`, a bitmask)."""
    cost: float
    gone: int


def cover(side: Side, fixed: frozenset[str], pool: frozenset[str], price: Mapping[str, float], need: float
          ) -> Covered | None:
    """The cheapest set of `pool` to sell, on top of `fixed`, raising at least `need` at `price`, with the cost of `fixed` and it
    together; None when no eleven remains or the pool cannot raise it."""
    held = side.mask(fixed)
    if need <= 1e-6:
        free = side.cost_of(held)
        return None if free is None else Covered(free, frozenset())
    cost_of, memo = side.cost_of, side.memo
    alone = {k: cost_of(held | side.bit[k]) for k in pool if price[k] > 0}
    order = sorted(alone, key=lambda k: (per_unit(alone[k], price[k]), k))
    bits = [side.bit[k] for k in order]
    worth = [price[k] for k in order]
    left = [sum(worth[i:]) for i in range(len(order) + 1)]
    best: Chosen | None = None
    stack = [Branch(0, 0, 0.0)]
    while stack:
        step = stack.pop()
        m = held | step.gone
        c = memo[m] if m in memo else cost_of(m)
        if c is None or best is not None and c >= best.cost - 1e-12:
            continue
        if step.got >= need - 1e-6:
            best = Chosen(c, step.gone)
            continue
        if step.i == len(order) or step.got + left[step.i] < need - 1e-6:
            continue
        stack.append(Branch(step.i + 1, step.gone, step.got))
        stack.append(Branch(step.i + 1, step.gone | bits[step.i], step.got + worth[step.i]))
    return None if best is None else Covered(best.cost, side.players(best.gone))


class Atom(NamedTuple):
    """One outcome of money: its probability `p` and the `money`."""
    p: float
    money: float


UNIT = (Atom(1.0, 0.0),)


class Step(NamedTuple):
    """The outcomes of one size of gap: their probability `p` and the `money` they raise, weighted by it."""
    p: float
    money: float


class Filled(NamedTuple):
    """What closing a gap costs: the `points` lost, the money `raised` and the players sold (`sells`)."""
    points: float
    raised: float
    sells: frozenset[str]


class Reserve(NamedTuple):
    """What a waiting player is to be sold for: at the j-th of the closes left an offer of `least[j]` or more is accepted (the last is
    the lowest, 0 when any offer is)."""
    player: str
    least: tuple[float, ...]


class Option(NamedTuple):
    """A way to bring cash to 0 or more at the lock: sell `now` at their standing offers, wait for the offers of `wait` (each
    sold at the first close whose offer is at or above its Reserve, at the last close whatever it is) and, when cash would still be
    below 0, sell the cheapest of the `fallback` players (cheapest per money first) after seeing the last close's offers. `points`
    and `cash` the expectations (points lost; cash at the lock, the credit at its expected), `worst` the most points it can lose and
    `p_worst` the chance of that."""
    now: frozenset[str]
    wait: tuple[Reserve, ...]
    fallback: tuple[str, ...]
    points: float
    cash: float
    worst: float
    p_worst: float


class Plan(NamedTuple):
    """The sales that bring cash to 0 or more at the lock with no buy: `sells` the players, `raised` what they
    raise at `prices`, `cost` the points the eleven loses. Infeasible (`short` the money the pool lacks, 0 when it suffices but
    would leave no eleven) when no set does it. Cash is counted at the lock with the Club's inflow at its expected. `secure` fills the
    rest: `now` the sales taken at their standing offer (the others wait for the offers still to come), `options` the ways to cover
    the cash, the recommended first (`secure`), with `sells`, `cost` and `raised` those of it before any fallback."""
    feasible: bool
    sells: frozenset[str]
    raised: float
    cost: float
    short: float
    now: frozenset[str] = frozenset()
    options: tuple[Option, ...] = ()


def plan(club: Club, found: Approach | None, rounds: Rounds, n_closes: int, side: Side | None = None) -> Plan | None:
    """The cheapest Plan with no buy, by `cover`. With closes still to come (`n_closes` 1 or more) it is `found`, the Approach with
    no player, whose sales are every player of the squad at his standing offer or the expected draw; with none to come (0) only
    the players with a standing offer are sold, at it. Empty and free when cash is 0 or more. None when the squad fields no eleven.
    `side` is the Side of the squad over rounds.arrives when the caller has it."""
    side = side or Side(club.squad, club.pos, rounds.cells, rounds.arrives)
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
    return Plan(True, got.sells, sum(price[k] for k in got.sells), got.cost, 0.0)


SPREAD = 100
PAIR = 40
BUCKET = 50_000.0
FALLBACK_LOW = 10
UNFILLED = 1e6
WAITERS = 2
EVALUATIONS = 50


def ratios_of(offers: worth.OfferDistribution) -> tuple[float, ...]:
    """The offer ratios a player's offer is drawn from, iid: those of `offers` thinned to SPREAD evenly spaced ones, or when none were seen
    SPREAD quantiles of a normal of mean SALE_ESTIMATE and spread SALE_SD."""
    seen = offers.ratios
    if not seen:
        return tuple(NormalDist(SALE_ESTIMATE, SALE_SD).inv_cdf((i + 0.5) / SPREAD) for i in range(SPREAD))
    return seen if len(seen) <= SPREAD else tuple(seen[int((i + 0.5) * len(seen) / SPREAD)] for i in range(SPREAD))


def sold_at(value: float, ratios: Sequence[float], least: Sequence[float]) -> list[Atom]:
    """The money one waiting player of `value` raises over the closes `least` names (the least offer accepted at each, one or more):
    his first offer at or above that close's least, an offer being value times a ratio of `ratios` drawn afresh at each; the last
    close's least is 0 or less. The Atoms' probabilities sum to 1."""
    n = len(ratios)
    out: list[Atom] = []
    reach = 1.0
    for need in least:
        taken = [r for r in ratios if r * value >= need]
        out += [Atom(reach / n, r * value) for r in taken]
        reach *= 1.0 - len(taken) / n
    return out


def thinned(atoms: Sequence[Atom], n: int) -> list[Atom]:
    """The Atoms merged, neighbours in order of money, into at most n: the same probability and the same mean money."""
    ordered = sorted(atoms, key=lambda x: x.money)
    if len(ordered) <= n:
        return ordered
    step = len(ordered) / n
    groups = [ordered[int(i * step):int((i + 1) * step)] for i in range(n)]
    return [Atom(w, sum(x.p * x.money for x in g) / w) for g in groups if (w := sum(x.p for x in g)) > 0]


def together(first: Sequence[Atom], second: Sequence[Atom]) -> list[Atom]:
    """The Atoms of the sum of two independent moneys, each thinned to PAIR first (UNIT, no money, is left out)."""
    if first is UNIT:
        return list(second)
    one, other = thinned(first, PAIR), thinned(second, PAIR)
    return thinned([Atom(a.p * b.p, a.money + b.money) for a in one for b in other], SPREAD * 4)


def bucket_of(gap: float) -> int:
    """The number of BUCKET steps that cover a gap in money: 0 when it is 0 or less."""
    return 0 if gap <= 0 else int(gap // BUCKET) + 1


class Closer:
    """The Options of a Club, judged over `closes` closes (1 or more) with the offers a player draws from `ratios`: sales fixed, then the
    cheapest fallback for what cash still lacks. `outcome` gives an Option's expectations and worst case, memoised on the fallback's gap in
    steps of BUCKET; `pricing` is what each player sells for."""

    def __init__(self, club: Club, side: Side, ratios: Sequence[float], closes: int):
        self.club, self.side, self.ratios, self.closes = club, side, ratios, closes
        self.pricing = pricing_of(club, ratios)
        self.low = {k: p.low for k, p in self.pricing.items()}
        self.filled: dict[tuple[int, int], Filled | None] = {}

    def fill(self, fixed: frozenset[str], mask: int, step: int) -> Filled | None:
        """The Filled of the cheapest fallback, at the low prices, that closes a gap of `step` BUCKETs (bucket_of) on top of `fixed` (`mask` its
        bitmask); None when none does."""
        key = (mask, step)
        if key not in self.filled:
            got = cover(self.side, fixed, self.club.squad - fixed, self.low, step * BUCKET)
            self.filled[key] = None if got is None else Filled(got.cost, sum(self.low[k] for k in got.sells), got.sells)
        return self.filled[key]

    def least(self, player: str, payoff: Callable[[float], float], rho: float) -> float:
        """The least offer to `player` whose `payoff` (nondecreasing in the offer) reaches `rho`, to a euro; infinite when even the highest
        offer does not."""
        top = self.ratios[-1] * self.club.worth[player]
        if payoff(top) < rho - 1e-9:
            return math.inf
        low, high = 0.0, top
        while high - low > 1.0:
            mid = (low + high) / 2
            low, high = (low, mid) if payoff(mid) >= rho - 1e-9 else (mid, high)
        return high

    def money(self, now: frozenset[str], wait: frozenset[str]) -> tuple[list[Atom], tuple[Reserve, ...]]:
        """The Atoms of the money the waiting players raise and their Reserves. Each is sold by worth.waiting_values over the points the
        sale then saves (the payoff of an offer: minus the points lost with the cheapest fallback for what cash would still lack, the
        others of the plan at their standing offer or their expected price, cash above what is lacking worth nothing) with no cost of
        waiting, the last close taking any offer."""
        club = self.club
        base = club.cash + club.inflow.expected + sum(club.offers[k] for k in now)
        mask = self.side.mask(now | wait)
        atoms: Sequence[Atom] = UNIT
        reserves = []
        for k in sorted(wait):
            rest = base + sum(self.pricing[j].expected for j in wait if j != k)
            def payoff(offer: float, rest: float = rest, mask: int = mask) -> float:
                filled = self.fill(now | wait, mask, bucket_of(-(rest + offer)))
                return -UNFILLED if filled is None else -filled.points
            payoffs = [payoff(r * club.worth[k]) for r in self.ratios]
            rho = worth.waiting_values(self.closes - 1, tuple(payoffs), 0.0, payoffs[0])
            leasts = (*(self.least(k, payoff, rho[self.closes - 1 - j]) for j in range(self.closes - 1)), 0.0)
            reserves.append(Reserve(k, leasts))
            atoms = together(atoms, sold_at(club.worth[k], self.ratios, leasts))
        return list(atoms), tuple(reserves)

    def outcome(self, now: frozenset[str], wait: frozenset[str]) -> Option | None:
        """The Option that sells `now` and waits for `wait`: None when some outcome leaves no eleven or no fallback closes the gap."""
        club = self.club
        fixed = now | wait
        atoms, reserves = self.money(now, wait)
        base = club.cash + club.inflow.expected + sum(club.offers[k] for k in now)
        mask = self.side.mask(fixed)
        steps: dict[int, Step] = {}
        for a in atoms:
            at = bucket_of(-(base + a.money))
            seen = steps.get(at, Step(0.0, 0.0))
            steps[at] = Step(seen.p + a.p, seen.money + a.p * a.money)
        points = cash = worst = p_worst = 0.0
        for at, step in steps.items():
            filled = self.fill(fixed, mask, at)
            if filled is None:
                return None
            points += step.p * filled.points
            cash += step.p * (base + filled.raised) + step.money
            if filled.points > worst + 1e-9:
                worst, p_worst = filled.points, step.p
            elif filled.points > worst - 1e-9:
                p_worst += step.p
        short = {at: step for at, step in steps.items() if at > 0}
        gap = sum(s.p * at for at, s in short.items()) / sum(s.p for s in short.values()) if short else 0.0
        likely = self.fill(fixed, mask, max(1, round(gap))) if short else None
        order = tuple(sorted(likely.sells, key=lambda k: (per_unit(self.side.cost(fixed | {k}), self.low[k]), k))) if likely else ()
        return Option(now, reserves, order, points, cash, worst, p_worst)


class Node(NamedTuple):
    """A set of players the search may sell: the least points any way of covering the cash with it can lose (`bound`) and who is in it
    (`members`, indexes in order)."""
    bound: float
    members: tuple[int, ...]


def secure(club: Club, found: Plan, side: Side, offers: worth.OfferDistribution, n_closes: int) -> Plan:
    """`found` with `options` for covering cash below 0 at the lock when `n_closes` (1 or more) closes are left, the recommended first
    (the fewest expected points lost, Closer.outcome), then the plan taking its sales with standing offers now; `sells`, `now`, `cost`
    (the expected points) and `raised` are the recommended's, before any fallback. The sets of players are searched in the order of the
    least points any cover with them can lose (cover at the highest prices), stopping when that reaches the best found or after
    EVALUATIONS options judged, at most WAITERS players waiting. The recommended never loses more points in expectation than the second.
    `found` unchanged when infeasible or when it sells nothing; with no close left (`n_closes` 0), or when no option can be judged, `now` is its
    sales with a standing offer and `options` stays empty."""
    if not found.feasible or not found.sells:
        return found
    if n_closes < 1:
        return found._replace(now=found.sells)
    judge = Closer(club, side, ratios_of(offers), n_closes)
    need = -(club.cash + club.inflow.expected)
    own = frozenset(club.offers)
    seed = judge.outcome(found.sells & own, found.sells - own) if len(found.sells - own) <= WAITERS else None
    best = seed
    squad = sorted(k for k in club.squad if judge.pricing[k].best > 0)
    top = {k: judge.pricing[k].best for k in club.squad}

    def bound_of(chosen: frozenset[str]) -> float:
        got = cover(side, chosen, club.squad - chosen, top, max(0.0, need - sum(top.get(k, 0.0) for k in chosen)))
        return math.inf if got is None else got.cost
    queue = [Node(b, (i,)) for i, k in enumerate(squad) if (b := bound_of(frozenset({k}))) < math.inf]
    heapq.heapify(queue)
    judged = 0
    while queue and judged < EVALUATIONS:
        node = heapq.heappop(queue)
        if best is not None and node.bound >= best.points - 1e-9:
            break
        chosen = frozenset(squad[i] for i in node.members)
        for i in range(node.members[-1] + 1, len(squad)):
            if (b := bound_of(chosen | {squad[i]})) < math.inf and (best is None or b < best.points - 1e-9):
                heapq.heappush(queue, Node(b, (*node.members, i)))
        offered = sorted(chosen & own)
        for m in range(min(len(offered), WAITERS - len(chosen - own)) + 1):
            for held in combinations(offered, m):
                tried = judge.outcome(frozenset(offered) - frozenset(held), (chosen - own) | frozenset(held))
                judged += 1
                if tried is not None and (best is None or tried.points < best.points - 1e-9):
                    best = tried
    if best is None:
        return found._replace(now=found.sells & own)
    sells = best.now | frozenset(r.player for r in best.wait)
    raised = sum(club.offers[k] if k in best.now else judge.pricing[k].expected for k in sells)
    return found._replace(sells=sells, now=best.now, cost=best.points, raised=raised,
                          options=tuple(dict.fromkeys(x for x in (best, seed) if x is not None)))


class Pick(NamedTuple):
    """A step of the search of `approach`: the standing offers from index `i` of the order are still to decide, `sold` are taken tonight."""
    i: int
    sold: frozenset[str]


class Choice(NamedTuple):
    """The best funding `approach` has found: its expected points lost `value`, the sales `tonight`, those `if_won` and `if_lost` after the
    result and the points the sales lose in each case."""
    value: float
    tonight: frozenset[str]
    if_won: frozenset[str]
    if_lost: frozenset[str]
    cost_won: float
    cost_lost: float


class Points(NamedTuple):
    """The points of a bid: the `gain` of the buy and the costs of the sales if it is won and if it is lost."""
    gain: float
    cost_won: float
    cost_lost: float


def approach(club: Club, player: str | None, bid: float, win: float, rounds: Rounds, base: float = 0.0,
             sides: dict[tuple[frozenset[str], tuple[int, ...]], Side] | None = None) -> Approach | None:
    """How to fund a bid on `player` (see Approach): the sales tonight, those after the result, their cost in points and the
    net. Costs are over `rounds`: a sale made tonight is gone from the squad in every jornada of `rounds.now`, the player
    bought plays only in `rounds.arrives`, and the sales after the result count only there. `base` is the cost of the
    cheapest plan with no buy (the cost of `approach` with player None, bid 0, win 1 and `rounds.arrives` = `rounds.now`:
    the gain 0, both costs the cost of the sales that bring cash to 0 or more), and the net is measured against it. `sides` shares the Side of a squad and window (and its memo of costs)
    between calls over one club's positions and cells. None when
    the squad fields no eleven. Cash at the lock includes `club.inflow`: the sales must make it 0 or more at its expected, and
    `lock_margin` is shown at its expected, `lock_margin_low` at its low (with the sales that follow low too)."""
    shared = {} if sides is None else sides

    def side(squad: frozenset[str], window: tuple[int, ...]) -> Side:
        if (squad, window) not in shared:
            shared[(squad, window)] = Side(squad, club.pos, rounds.cells, window)
        return shared[(squad, window)]
    lost_side = side(club.squad, tuple(rounds.arrives))
    won_side = lost_side if player is None else side(club.squad | {player}, tuple(rounds.arrives))
    before = side(club.squad, rounds.before)
    if not lost_side.fields:
        return None
    gain = won_side.points - lost_side.points
    pend_n, pend_amount = club.pending
    price = prices(club, player is not None)
    offered = sorted((k for k in club.squad if k in club.offers),
                     key=lambda k: (per_unit(won_side.cost(frozenset({k})), max(club.offers[k], 1.0)), k))
    reach = [sum(club.offers[k] - club.debt * club.worth[k] for k in offered[i:]) for i in range(len(offered) + 1)]
    best: Choice | None = None
    ceiling = bid_margin(club.cash, sum(club.offers[k] for k in offered), club.team_value,
                         sum(club.worth[k] for k in offered), bid, pend_amount, club.debt)
    stack = [Pick(0, frozenset())]
    while stack:
        pick = stack.pop()
        i, a = pick.i, pick.sold
        cw, cl = won_side.cost(a), lost_side.cost(a)
        early = before.cost(a)
        if cw is None or cl is None or early is None or best is not None and win * cw + (1 - win) * cl + early >= best.value - 1e-12:
            continue
        raised, sold = sum(club.offers[k] for k in a), sum(club.worth[k] for k in a)
        margin = bid_margin(club.cash, raised, club.team_value, sold, bid, pend_amount, club.debt)
        if margin >= -1e-6:
            need_won = -cash_at_lock(club.cash, raised, bid, 0.0, club.inflow.expected)
            need_lost = -(club.cash + raised + club.inflow.expected)
            won = cover(won_side, a, club.squad - a, price, need_won)
            lost = cover(lost_side, a, club.squad - a, price, need_lost)
            if won and lost and len(club.squad) + (player is not None) - len(a) - len(won.sells) + pend_n <= club.squad_max:
                ev = win * won.cost + (1 - win) * lost.cost + early
                if best is None or ev < best.value:
                    best = Choice(ev, a, won.sells, lost.sells, won.cost + early, lost.cost + early)
        elif margin + reach[i] < -1e-6:
            continue
        for j in range(i, len(offered)):
            stack.append(Pick(j + 1, a | {offered[j]}))
    if best is None:
        return unfunded(club, max(0.0, -ceiling), ceiling, gain)._replace(base=base)
    return funded(club, best.tonight, best.if_won, best.if_lost, bid, win, Points(gain, best.cost_won, best.cost_lost), base, price)


def unfunded(club: Club, short: float, margin: float, gain: float) -> Approach:
    """The Approach of a bid no set of the standing offers funds: `short` the money lacking, `margin` the bid margin."""
    return Approach(False, short, frozenset(), frozenset(club.offers) & club.squad, frozenset(), frozenset(), 0.0, margin,
                    0.0, 0.0, 0.0, gain, 0.0, 0.0, 0.0)


def funded(club: Club, tonight: frozenset[str], if_won: frozenset[str], if_lost: frozenset[str], bid: float, win: float,
           points: Points, base: float, price: Mapping[str, float]) -> Approach:
    """The feasible Approach that sells `tonight` to their offers, then `if_won` or `if_lost` at `price` once the result is
    known; `points` the gain of the buy and the costs of the sales if it is won and if it is lost; `base` the cost of the plan
    with no buy; the net is win * (gain - (cost_won - base)) - (1 - win) * (cost_lost - base): the points the bid adds
    to doing nothing."""
    gain, cost_won, cost_lost = points.gain, points.cost_won, points.cost_lost
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
    if won is None or len(club.squad) - len(tonight) - len(won.sells) + pend_n + 1 > club.squad_max:
        return unfunded(club, 0.0, margin, gain)
    early = before.forgone(tonight)
    return funded(club, tonight, won.sells, frozenset(), bid, win,
                  Points(gain, won_side.forgone(tonight | won.sells) + early, lost_side.forgone(tonight) + early), 0.0, price)


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
        shared: dict[tuple[frozenset[str], tuple[int, ...]], Side] = {}
        assert approach(club, "star", bid, win, rounds, sides=shared) == got == approach(club, "star", bid, win, rounds, sides=shared), "shared Sides change nothing"
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
            assert abs(covered.cost - min(c for c, _ in direct)) < 1e-9, "the cover is the cheapest set"
            kept = side.cost(fixed | covered.sells)
            assert covered.sells <= pool and sum(price[k] for k in covered.sells) >= need - 1e-6 and kept is not None and abs(kept - covered.cost) < 1e-9
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
    rng = random.Random(31)
    seen_wait = seen_options = 0
    for _ in range(40):
        town = {"m%02d" % i: p for i, p in enumerate(["POR"] * 2 + ["DEF"] * 5 + ["MED"] * 5 + ["DEL"] * 3)}
        tcells = _cells(rng, town, (1, 2, 3), [0, 0, 1, 3, 5])
        tworth = {k: rng.choice([2e6, 4e6, 6e6]) for k in town}
        toffers = {k: tworth[k] * rng.choice([0.9, 1.0, 1.1]) for k in rng.sample(sorted(town), rng.randint(0, 6))}
        tcash = rng.choice([-12e6, -5e6, -1e6, 0.0, 2e6])
        tclub = Club(frozenset(town), town, tworth, toffers, tcash, 5e7)
        tflat = Rounds(tcells, valuation.Window((1, 2, 3)), valuation.Window((1, 2, 3)))
        n = rng.choice([1, 3])
        tfound = plan(tclub, approach(tclub, None, 0.0, 1.0, tflat), tflat, n)
        assert tfound is not None
        tside = Side(tclub.squad, town, tcells, tflat.arrives)
        dist = rng.choice([NO_OFFERS, offer_ratio_distribution([0.85, 0.9, 0.95, 1.0, 1.0, 1.05, 1.1])])
        secured = secure(tclub, tfound, tside, dist, n)
        if tcash >= 0 or not tfound.feasible:
            assert secured == tfound, "no deficit, or no plan: nothing to secure"
            continue
        if not secured.options:
            continue
        seen_options += 1
        best = secured.options[0]
        assert secured.now == best.now and secured.sells == best.now | {r.player for r in best.wait} and abs(secured.cost - best.points) < 1e-9
        assert best.now <= frozenset(toffers) and not best.now & {r.player for r in best.wait}
        assert best.cash >= -1e-6, "the expected cash at the lock is 0 or more"
        assert best.worst >= best.points - 1e-9 and 0.0 < best.p_worst <= 1.0 + 1e-9
        for r in best.wait:
            assert len(r.least) == n and r.least[-1] == 0.0 and all(a >= b - 1e-9 for a, b in zip(r.least, r.least[1:])), \
                "the reserve never rises as the lock nears, and the last close takes any offer"
            seen_wait += 1
        assert len(set(secured.options)) == len(secured.options)
        judge = Closer(tclub, tside, ratios_of(dist), n)
        everything = judge.outcome(tfound.sells & frozenset(toffers), tfound.sells - frozenset(toffers)) if len(tfound.sells - frozenset(toffers)) <= WAITERS else None
        if everything is not None:
            assert best.points <= everything.points + 1e-9, "the policy loses no more points in expectation than taking the plan's offers now"
    assert seen_options >= 8 and seen_wait >= 3, (seen_options, seen_wait)
    free = {"m%02d" % i: p for i, p in enumerate(["POR"] * 2 + ["DEF"] * 5 + ["MED"] * 5 + ["DEL"] * 3)}
    fcells = {1: {k: (0.0 if i >= 13 else 3.0) for i, k in enumerate(sorted(free))}}
    fclub = Club(frozenset(free), free, {k: 4e6 for k in free}, {"m13": 4.2e6}, -4e6, 5e7)
    fflat = Rounds(fcells, valuation.Window((1,)), valuation.Window((1,)))
    fside = Side(fclub.squad, free, fcells, fflat.arrives)
    fjudge = Closer(fclub, fside, ratios_of(NO_OFFERS), 3)
    taken, waited = fjudge.outcome(frozenset({"m13"}), frozenset()), fjudge.outcome(frozenset(), frozenset({"m13"}))
    assert taken is not None and waited is not None and waited.points <= taken.points + 1e-9, "with a fallback that costs nothing, waiting is never worse than taking now"
    assert waited.wait[0].least[0] >= waited.wait[0].least[-1], "a reserve for a player who is not needed is any offer"
    drawn = sold_at(4e6, (0.9, 1.0, 1.1), (4.4e6, 0.0))
    assert abs(sum(a.p for a in drawn) - 1.0) < 1e-12 and drawn[0] == Atom(1 / 3, 4.4e6) and sorted(a.money for a in drawn)[0] < 4.4e6, "the Atoms are a distribution"
    assert all(a.money >= 4.4e6 for a in sold_at(4e6, (0.9, 1.0, 1.1), (4.4e6,))), "the only close left takes offers at or above its least"
    own = {"m%02d" % i: p for i, p in enumerate(["POR"] * 2 + ["DEF"] * 5 + ["MED"] * 5 + ["DEL"] * 3)}
    cells = {1: {k: 1.0 + 0.37 * i for i, k in enumerate(sorted(own))}}
    worth_of = {k: 4e6 for k in own}
    tclub = Club(frozenset(own), own, worth_of, {}, -3e6, 5e7)
    tflat = Rounds(cells, valuation.Window((1,)), valuation.Window((1,)))
    cheapest = plan(tclub, approach(tclub, None, 0.0, 1.0, tflat), tflat, 3)
    assert cheapest is not None
    bench = secure(tclub, cheapest, Side(tclub.squad, own, cells, tflat.arrives), NO_OFFERS, 3)
    assert bench.feasible and bench.cost < 1e-6 and len(bench.sells) == 1, "a player of the same money who costs no points is sold, not a starter"
    broke = Club(frozenset(own), own, worth_of, {}, -3e8, 5e7)
    lacking = plan(broke, approach(broke, None, 0.0, 1.0, tflat), tflat, 3)
    assert lacking is not None and not lacking.feasible and cheapest.feasible, "a plan exists exactly when what can be sold covers the deficit"
    rng = random.Random(77)
    checked = 0
    for _ in range(70):
        shape = ["POR"] * 2 + ["DEF"] * 6 + ["MED"] * 5 + ["DEL"] * 3
        big = {"m%02d" % i: p for i, p in enumerate(shape)}
        sizes = {k: rng.choice([0.5e6, 1e6, 2e6, 4e6, 8e6, 16e6, 32e6]) for k in big}
        grid = {1: {k: rng.choice([0, 0, 0.5, 1, 2, 3, 5, 8]) for k in big}}
        quotes = {k: sizes[k] * rng.choice([0.85, 0.92, 1.0, 1.08]) for k in rng.sample(sorted(big), rng.randint(0, 8))}
        lack = rng.uniform(0.1, 0.8) * sum(sizes.values())
        crowd = Club(frozenset(big), big, sizes, quotes, -lack, 5e7)
        span = Rounds(grid, valuation.Window((1,)), valuation.Window((1,)))
        wing = Side(crowd.squad, big, grid, span.arrives)
        closes = rng.choice([1, 3])
        first = plan(crowd, approach(crowd, None, 0.0, 1.0, span), span, closes, wing)
        assert first is not None
        if not first.feasible or not first.sells:
            continue
        seen = rng.choice([NO_OFFERS, offer_ratio_distribution([0.85, 0.9, 0.95, 1.0, 1.0, 1.05, 1.1, 1.2])])
        chosen = secure(crowd, first, wing, seen, closes)
        if not chosen.options:
            continue
        judge = Closer(crowd, wing, ratios_of(seen), closes)
        floor = chosen.options[0].points
        owned = frozenset(quotes)
        for size in range(1, 5):
            for members in combinations(sorted(big), size):
                group = frozenset(members)
                if (c := wing.cost(group)) is None or c >= floor - 1e-9 or len(group - owned) > WAITERS:
                    continue
                offered = sorted(group & owned)
                for m in range(min(len(offered), WAITERS - len(group - owned)) + 1):
                    for held in combinations(offered, m):
                        other = judge.outcome(frozenset(offered) - frozenset(held), (group - owned) | frozenset(held))
                        assert other is None or other.points >= floor - 0.05, "no split of up to four players loses fewer points in expectation"
        checked += 1
    assert checked >= 8, checked
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
