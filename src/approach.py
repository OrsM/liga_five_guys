from __future__ import annotations

import heapq
import math
import random
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from functools import lru_cache, reduce
from itertools import combinations, product
from operator import or_
from statistics import NormalDist
from typing import NamedTuple

import eleven
import hurdle
import valuation
import worth
from ffcore.auction import Curve
from ffcore.rules import DEBT_SHARE, SALE_ESTIMATE, SALE_SD, SQUAD_MAX, bid_margin, cash_at_lock
from inflow import NONE, Inflow
from worth import NO_OFFERS, offer_ratio_distribution

__all__ = ["Club", "Rounds", "Approach", "Plan", "Option", "Reserve", "Atom", "Closer", "Covered", "gains", "Filled", "Pricing", "approach", "replacement", "Money", "Offer", "Bidding", "bidding", "plan", "secure", "prices", "pricing_of", "ratios_of", "sold_at"]


class Club(NamedTuple):
    """The squad with its positions, worths and standing offers, cash, team value, bids pending (count, money), the squad cap and debt
    share, the credit due, `unlisted` (the players of the squad not on the market, who are listed before the close) and `drift` the price
    fold's expected change of each player's value per night, as a fraction."""
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
    unlisted: frozenset[str] = frozenset()
    drift: Mapping[str, float] = {}


class Rounds(NamedTuple):
    """The jornadas a bid is valued over and the forecast of each: `now` those a sale made now counts for, `arrives` those
    a buy won counts for (inside `now`: a buy closing after a lock does not count for it), `cells` each jornada's forecast
    per player, `closes` the closes left before the lock (1 or more: the sales after a result are made at the last, as the cover's recourse
    is)."""
    cells: valuation.Cells
    now: valuation.Window
    arrives: valuation.Window
    closes: int = 1

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
    credit_won: float = 0.0
    credit_lost: float = 0.0
    paid: float = 0.0


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
        self.masks: dict[frozenset[str], int] = {}

    def mask(self, players: Iterable[str]) -> int:
        """The bitmask of a set of players, memoised on the set."""
        key = players if isinstance(players, frozenset) else frozenset(players)
        got = self.masks.get(key)
        if got is None:
            got = self.masks[key] = reduce(or_, (self.bit[k] for k in key), 0)
        return got

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

    def lapse(self, gone: frozenset[str]) -> float:
        """The points the first jornada of the window scores for what is left when `gone` is sold: lost when cash is below 0 at its lock (the
        jornada scores nothing). 0 when no eleven is left."""
        return x if self.rounds and (x := self.rounds[0].left_of(self.mask(gone))) is not None else 0.0

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
    worth) and the `best` (the highest offer ratio seen, or his standing offer when that is more). `now` is the standing offer where there
    is one, else the expected price."""
    standing: float | None
    expected: float
    best: float

    @property
    def now(self) -> float:
        return self.expected if self.standing is None else self.standing


def pricing_of(club: Club, ratios: Sequence[float] = ()) -> dict[str, Pricing]:
    """The Pricing of each player of the squad; `ratios` (ascending) the offer ratios the best price is read from (the expected price when
    none are given)."""
    out = {}
    for k in club.squad:
        value, standing = club.worth[k], club.offers.get(k)
        expected = hurdle.draw(value)
        out[k] = Pricing(standing, expected, max(standing or 0.0, ratios[-1] * value) if ratios else max(standing or 0.0, expected))
    return out


def prices(club: Club, bought: bool, closes: int = 1) -> dict[str, float]:
    """What each player of the squad raises when sold after the result of a bid (`bought`): the expected draw (hurdle.draw of
    his worth) at his value of the last of `closes` closes (`drifted`, as the cover's recourse prices it: a standing offer expires at the close
    and is redrawn, and a value that falls or rises over the nights moves the sale). With no bid (`bought` False) the standing offer where
    there is one, else the expected draw."""
    return {k: hurdle.drifted(p.expected, club.drift.get(k, 0.0), closes - 1) if bought else p.now for k, p in pricing_of(club).items()}


class Covered(NamedTuple):
    """A set of players to sell (`sells`), the points it loses with those already sold (`cost`) and the `credit` (points) of its members that the
    search was given for the members of `sells` (0 with none): its net is cost less credit."""
    cost: float
    sells: frozenset[str]
    credit: float = 0.0

    @property
    def net(self) -> float:
        return self.cost - self.credit


class Branch(NamedTuple):
    """A step of the search of `covers`: the players from index `i` of the order are still to decide, `gone` (a bitmask) are sold, raise `got`
    and are credited `credit` points."""
    i: int
    gone: int
    got: float
    credit: float


class Chosen(NamedTuple):
    """A set `covers` has found: its `net`, the cost in points less the credit of its members, its `cost` and who is sold (`gone`, a bitmask)."""
    net: float
    cost: float
    gone: int


def covers(side: Side, fixed: frozenset[str], pool: frozenset[str], price: Mapping[str, float], need: float,
           gain: Mapping[str, float] | None = None, keep: int = 1, proper: bool = False) -> list[Covered]:
    """The `keep` best sets of `pool` to sell, on top of `fixed`, raising at least `need` at `price`, best first: cheapest in points less the
    `gain` (points credited, by player; none: 0) of its members. With `proper` only a cover none of whose members is superfluous (without any
    of them it raises less than `need`) counts. Covered.cost is the cost of `fixed` and the set together. Fewer than `keep` (none) when the pool
    has no more such covers, or no eleven remains."""
    held = side.mask(fixed)
    if need <= 1e-6:
        free = side.cost_of(held)
        return [] if free is None else [Covered(free, frozenset())]
    credit = gain or {}
    cost_of, memo = side.cost_of, side.memo
    alone = {k: cost_of(held | side.bit[k]) for k in pool if price[k] > 0}
    order = sorted(alone, key=lambda k: (per_unit(alone[k], price[k]), k))
    bits = [side.bit[k] for k in order]
    worth = [price[k] for k in order]
    credits = [credit.get(k, 0.0) for k in order]
    left = [sum(worth[i:]) for i in range(len(order) + 1)]
    ahead = [sum(max(0.0, x) for x in credits[i:]) for i in range(len(order) + 1)]
    best: list[Chosen] = []
    stack = [Branch(0, 0, 0.0, 0.0)]
    while stack:
        step = stack.pop()
        m = held | step.gone
        c = memo[m] if m in memo else cost_of(m)
        if c is None or len(best) == keep and c - step.credit - ahead[step.i] >= best[-1].net - 1e-12:
            continue
        if step.got >= need - 1e-6:
            if not proper or all(step.got - w < need - 1e-6 for b, w in zip(bits, worth) if step.gone & b):
                best = sorted([*best, Chosen(c - step.credit, c, step.gone)])[:keep]
            continue
        if step.i == len(order) or step.got + left[step.i] < need - 1e-6:
            continue
        stack.append(Branch(step.i + 1, step.gone, step.got, step.credit))
        stack.append(Branch(step.i + 1, step.gone | bits[step.i], step.got + worth[step.i], step.credit + credits[step.i]))
    return [Covered(x.cost, side.players(x.gone), x.cost - x.net) for x in best]


def cover(side: Side, fixed: frozenset[str], pool: frozenset[str], price: Mapping[str, float], need: float,
          gain: Mapping[str, float] | None = None, proper: bool = False) -> Covered | None:
    """The best of `covers` (the cheapest set of `pool` to sell on top of `fixed`, raising at least `need` at `price`, in points less the `gain`
    of its members, `proper` or not); None when there is none."""
    got = covers(side, fixed, pool, price, need, gain, 1, proper)
    return got[0] if got else None


class Atom(NamedTuple):
    """One outcome of money: its probability `p` and the `money`."""
    p: float
    money: float


UNIT = (Atom(1.0, 0.0),)


class End(NamedTuple):
    """One way a waiting player's wait can end: `sold` or not, and the `atoms` of the money it raises (their probabilities sum to the chance of
    that end)."""
    sold: bool
    atoms: tuple[Atom, ...]


class Step(NamedTuple):
    """The outcomes of one size of gap: their probability `p` and the `money` they raise, weighted by it."""
    p: float
    money: float


class Filled(NamedTuple):
    """What closing a gap costs, in expectation over which players have an offer at the last close: the `points` lost, the money `raised`, the
    `gain` (points the prices above the players' worth are worth), the players sold when all have one (`sells`), the most points it can lose
    (`worst`) and the chance of that (`p_worst`)."""
    points: float
    raised: float
    gain: float
    sells: frozenset[str]
    worst: float
    p_worst: float


def first_available(sets: Sequence[frozenset[str]], exists: float) -> list[float]:
    """The chance that each of `sets` (in order of preference) is the first all of whose players have an offer, each player having one
    independently with probability `exists`: the inclusion-exclusion of the earlier sets. They sum to at most 1."""
    out = []
    for i, one in enumerate(sets):
        out.append(sum((-1) ** len(t) * exists ** len(one.union(*(sets[j] for j in t))) for r in range(i + 1) for t in combinations(range(i), r)))
    return out


class Reserve(NamedTuple):
    """What a waiting player is to be sold for: at the j-th of the closes left an offer of `least[j]` or more is accepted (the last is
    the lowest: 0 when any offer is, infinite when none is, he is then kept)."""
    player: str
    least: tuple[float, ...]


class Option(NamedTuple):
    """A way to bring cash to 0 or more at the lock: sell `now` at their standing offers, wait for the offers of `wait` (each
    sold at the first close whose offer is at or above its Reserve, at the last close one that beats keeping him) and, when cash would still be
    below 0, sell the cheapest of the `fallback` players (cheapest per money first) after seeing the last close's offers. `points`
    and `cash` the expectations (points lost; cash at the lock, the credit at its expected), `worst` the most points it can lose and
    `p_worst` the chance of that; `gain` the expected points the prices fetched above the players' worth are worth (kappa x (price less the
    expected offer), summed over the players sold, at the rate), 0 when not read: `net`, the points lost less that, is what options are
    ranked by."""
    now: frozenset[str]
    wait: tuple[Reserve, ...]
    fallback: tuple[str, ...]
    points: float
    cash: float
    worst: float
    p_worst: float
    gain: float = 0.0

    @property
    def net(self) -> float:
        return self.points - self.gain


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


def plan(club: Club, found: Approach | None, rounds: Rounds, n_closes: int, side: Side | None = None, *, rate: float) -> Plan | None:
    """The cheapest Plan with no buy, by `cover`. With closes still to come (`n_closes` 1 or more) it is `found`, the Approach with
    no player, whose sales are every player of the squad at his standing offer or the expected draw; with none to come (0) only
    the players with a standing offer are sold, at it, by the recourse the last close takes (`cover` proper, by points less the gains of the sales
    at `rate`, as Closer.fill and grading.follow choose it). Empty and free when cash is 0 or more. None when the squad fields no eleven.
    `side` is the Side of the squad over rounds.arrives when the caller has it."""
    side = side or Side(club.squad, club.pos, rounds.cells, rounds.arrives)
    if not side.fields or found is None:
        return None
    price = prices(club, False)
    if n_closes > 0 and found.feasible:
        sells = found.tonight | found.if_lost
        return Plan(True, sells, sum(price[k] for k in sells), found.cost_lost, 0.0)
    pool = club.squad if n_closes > 0 else frozenset(club.offers) & club.squad
    got = None if n_closes > 0 else cover(side, frozenset(), pool, price, -(club.cash + club.inflow.expected), gains(club, Money(rate, len(side.rounds)), price), True)
    if got is None:
        return Plan(False, frozenset(), 0.0, 0.0, max(0.0, -(club.cash + club.inflow.expected) - sum(price[k] for k in pool)))
    return Plan(True, got.sells, sum(price[k] for k in got.sells), got.cost, 0.0)


SPREAD = 100
PAIR = 40
BUCKET = 50_000.0
UNFILLED = 1e6
WAITERS = 2
EVALUATIONS = 50
COVERS = 6
UNCOVERED = 0.01
STEP = 0.02
MISSED = 4


def ratios_of(offers: worth.OfferDistribution) -> tuple[float, ...]:
    """The offer ratios a player's offer is drawn from, iid: those of `offers` thinned to SPREAD evenly spaced ones, or when none were seen
    SPREAD quantiles of a normal of mean SALE_ESTIMATE and spread SALE_SD."""
    seen = offers.ratios
    if not seen:
        return tuple(NormalDist(SALE_ESTIMATE, SALE_SD).inv_cdf((i + 0.5) / SPREAD) for i in range(SPREAD))
    return seen if len(seen) <= SPREAD else tuple(seen[int((i + 0.5) * len(seen) / SPREAD)] for i in range(SPREAD))


def sold_at(value: float, ratios: Sequence[float], least: Sequence[float], exists: float = 1.0, drift: float = 0.0) -> list[Atom]:
    """The money one waiting player of `value` raises over the closes `least` names (the least offer accepted at each, one or more):
    his first offer at or above that close's least, an offer being drawn afresh at each (worth.offers_at: a ratio of `ratios` over his value as it
    drifts) and existing with probability `exists`; the last close's least is 0 or less. The Atoms' probabilities sum to 1 less the chance he is not
    sold."""
    n = len(ratios)
    out: list[Atom] = []
    reach = 1.0
    for close, need in enumerate(least):
        taken = [price for price in worth.offers_at(ratios, value, drift, close) if price >= need]
        out += [Atom(reach * exists / n, price) for price in taken]
        reach *= 1.0 - exists * len(taken) / n
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
    """The Options of a Club, judged over `closes` closes (1 or more) with the offers a player draws from the offer model `offers` (its ratios, each existing with
    probability its `exists`) and the value drifting as the club's `drift` says: sales fixed, then the recourse for what cash still lacks (`fill`: the
    cover at the expected offers, chosen as `grading.follow` chooses it at the offers drawn). `outcome` gives an Option's expectations and worst
    case, memoised on the recourse's gap in steps of BUCKET; `pricing` is what each player sells for. A player sold at a price above his expected
    offer (his worth) is worth `rate` points per million per jornada over the jornadas the side scores (hurdle.in_points) of the excess, below it
    costs the same: an Option's objective is its points lost less that gain."""

    def __init__(self, club: Club, side: Side, offers: worth.OfferDistribution, closes: int, rate: float = hurdle.FALLBACK_LAMBDA):
        self.club, self.side, self.closes = club, side, closes
        self.ratios, self.exists = ratios_of(offers), offers.exists
        self.n = len(side.rounds)
        self.pricing = pricing_of(club, self.ratios)
        self.drift = {k: club.drift.get(k, 0.0) for k in club.squad}
        self.price = {k: hurdle.drifted(p.expected, self.drift[k], closes - 1) for k, p in self.pricing.items()}
        self.rated = Money(rate, self.n)
        self.sale_gain = gains(club, self.rated, self.price)
        self.filled: dict[tuple[int, int, int], Filled | None] = {}

    def gain(self, player: str, price: float) -> float:
        """The points a sale of `player` at `price` is worth over his expected offer (his worth), negative below it; 0 at rate 0."""
        return gains(self.club, self.rated, {player: price})[player]

    def fill(self, fixed: frozenset[str], plan: frozenset[str], step: int) -> Filled | None:
        """The Filled of the recourse that closes a gap of `step` BUCKETs (bucket_of) on top of `fixed` (sold), at the expected offers, from the
        squad less `plan` (the players already sold or waited for), chosen by points less the gains of the sales at the prices the drift leaves (`gains`, as grading.follow chooses it): the best of the best
        sets whose players all have an offer at the last close, the jornada lost (Side.lapse) where none does. The COVERS best sets are listed and the shortest prefix kept that leaves at most
        UNCOVERED chance that none is available (one call, not a doubling: it profiled faster). That chance goes to the lapse when the sets are exhausted
        (all there are), else to the last set kept: exact to UNCOVERED, but optimistic at low existence when the COVERS cap binds; None when no eleven is left."""
        key = (self.side.mask(fixed), self.side.mask(plan), step)
        if key not in self.filled:
            got = covers(self.side, fixed, self.club.squad - plan, self.price, step * BUCKET, self.sale_gain, COVERS if step else 1, True)
            exhausted = len(got) < COVERS or not step
            while len(got) > 1 and 1.0 - sum(first_available([x.sells for x in got[:-1]], self.exists)) <= UNCOVERED:
                got = got[:-1]
            chance = first_available([x.sells for x in got], self.exists)
            missing = 1.0 - sum(chance)
            lost = self.side.cost(fixed)
            lapsed = None if lost is None else lost + self.side.lapse(fixed)
            if not got:
                self.filled[key] = None if lapsed is None else Filled(lapsed, 0.0, 0.0, frozenset(), lapsed, 1.0)
            else:
                if not exhausted:
                    chance[-1] += missing
                outcomes = [(w, x.cost, sum(self.price[k] for k in x.sells), sum(self.gain(k, self.price[k]) for k in x.sells)) for x, w in zip(got, chance)]
                if exhausted:
                    outcomes.append((missing, lapsed if lapsed is not None else 0.0, 0.0, 0.0))
                seen = [o for o in outcomes if o[0] > 1e-12]
                worst = max(o[1] for o in seen)
                self.filled[key] = Filled(sum(w * c for w, c, _, _ in seen), sum(w * m for w, _, m, _ in seen), sum(w * g for w, _, _, g in seen),
                                          got[0].sells, worst, sum(w for w, c, _, _ in seen if c > worst - 1e-9))
        return self.filled[key]

    def least(self, player: str, payoff: Callable[[float], float], rho: float) -> float:
        """The least offer to `player` whose `payoff` (nondecreasing in the offer) reaches `rho`, to a euro; infinite when even the highest
        offer does not; 0 when an offer of nothing does (any offer is)."""
        if payoff(0.0) >= rho - 1e-9:
            return 0.0
        top = self.ratios[-1] * self.club.worth[player]
        if payoff(top) < rho - 1e-9:
            return math.inf
        low, high = 0.0, top
        while high - low > 1.0:
            mid = (low + high) / 2
            low, high = (low, mid) if payoff(mid) >= rho - 1e-9 else (mid, high)
        return high

    def money(self, now: frozenset[str], wait: frozenset[str]) -> tuple[tuple[Reserve, ...], list[tuple[End, ...]]]:
        """The Reserves of the waiting players and, for each, how it can end: sold (the Atoms of the money it raises, their probabilities
        summing to the chance of a sale) or not sold (one Atom of no money: no offer was drawn at the last close). Each is sold by
        worth.waiting_values over the net the sale then leaves (the payoff of an offer: the gain of selling at it plus the recourse's less its
        points, for what cash would still lack, the others of the plan at their expected price) with no cost of waiting. Not sold is worth `unsold`:
        he is kept (no gain, no asset debit, not in the recourse's pool) and the recourse covers what cash lacks from the rest; no offer, an offer
        declined and a lapsed listing all give it, so the last close takes an offer iff selling at it is worth at least that (a reserve that
        may exceed every offer: then he is not sold) and an offer, being an option, never hurts."""
        club = self.club
        plan = now | wait
        base = club.cash + club.inflow.expected + sum(club.offers[k] for k in now)
        reserves, ends = [], []
        for k in sorted(wait):
            rest = base + sum(self.price[j] for j in wait if j != k)
            def payoff(offer: float, k: str = k, rest: float = rest) -> float:
                filled = self.fill(plan, plan, bucket_of(-(rest + offer)))
                return -UNFILLED if filled is None else self.gain(k, offer) + filled.gain - filled.points
            seen: dict[int, tuple[float, ...]] = {}
            def payoffs(draws: int, k: str = k, payoff: Callable[[float], float] = payoff, seen: dict[int, tuple[float, ...]] = seen) -> tuple[float, ...]:
                if draws not in seen:
                    seen[draws] = tuple(payoff(price) for price in worth.offers_at(self.ratios, club.worth[k], self.drift[k], self.closes - draws))
                return seen[draws]
            kept = self.fill(plan - {k}, plan, bucket_of(-rest))
            unsold = -UNFILLED if kept is None else kept.gain - kept.points
            rho = worth.waiting_values(self.closes - 1, payoffs, unsold, self.exists)
            leasts = tuple(self.least(k, payoff, rho[self.closes - 1 - j]) for j in range(self.closes))
            reserves.append(Reserve(k, leasts))
            sold = sold_at(club.worth[k], self.ratios, leasts, self.exists, self.drift[k])
            missed = 1.0 - sum(x.p for x in sold)
            ends.append((End(True, tuple(sold)), *([End(False, (Atom(missed, 0.0),))] if missed > 1e-12 else [])))
        return tuple(reserves), ends

    def outcome(self, now: frozenset[str], wait: frozenset[str]) -> Option | None:
        """The Option that sells `now` and waits for `wait`: None when some outcome leaves no eleven. An outcome no recourse closes loses the first
        jornada of the window (Side.lapse): cash is below 0 at its lock."""
        club = self.club
        plan = now | wait
        reserves, ends = self.money(now, wait)
        base = club.cash + club.inflow.expected + sum(club.offers[k] for k in now)
        steps: dict[tuple[frozenset[str], int], Step] = {}
        for combo in product(*ends):
            atoms: Sequence[Atom] = UNIT
            for end in combo:
                atoms = together(atoms, end.atoms)
            sold = now | frozenset(r.player for r, end in zip(reserves, combo) if end.sold)
            if sold != plan:
                atoms = thinned(atoms, MISSED)
            for a in atoms:
                key = (sold, bucket_of(-(base + a.money)))
                seen = steps.get(key, Step(0.0, 0.0))
                steps[key] = Step(seen.p + a.p, seen.money + a.p * a.money)
        points = cash = gain = worst = p_worst = 0.0
        for (fixed, gap), step in steps.items():
            filled = self.fill(fixed, plan, gap)
            if filled is None:
                return None
            points += step.p * filled.points
            cash += step.p * (base + filled.raised) + step.money
            gain += step.p * filled.gain + self.rated.over(step.money, step.p * sum(self.pricing[k].expected for k in fixed - now))
            if filled.worst > worst + 1e-9:
                worst, p_worst = filled.worst, step.p * filled.p_worst
            elif filled.worst > worst - 1e-9:
                p_worst += step.p * filled.p_worst
        gain += self.rated.over(sum(club.offers[k] for k in now), sum(self.pricing[k].expected for k in now))
        short = {key: step for key, step in steps.items() if key[1] > 0}
        lack = sum(s.p * key[1] for key, s in short.items()) / sum(s.p for s in short.values()) if short else 0.0
        likely = self.fill(plan, plan, max(1, round(lack))) if short else None
        order = tuple(sorted(likely.sells, key=lambda k: (per_unit(self.side.cost(plan | {k}), self.price[k]), k))) if likely else ()
        return Option(now, reserves, order, points, cash, worst, p_worst, gain)


class Node(NamedTuple):
    """A set of players the search may sell: the order it is tried in (`bound`, what covering the cash with it is expected to net at the highest prices) and who is in it
    (`members`, indexes in order)."""
    bound: float
    members: tuple[int, ...]


def secure(club: Club, found: Plan, side: Side, offers: worth.OfferDistribution, n_closes: int, rate: float = hurdle.FALLBACK_LAMBDA) -> Plan:
    """`found` with `options` for covering cash below 0 at the lock when `n_closes` (1 or more) closes are left, the recommended first
    (the least net, Option.net: the fewest expected points lost less the gain of selling at the prices fetched against the players' expected
    offers, at `rate`, Closer.outcome), then the plan taking its sales with standing offers now; `sells`, `now`, `cost` (the expected points) and
    `raised` are the recommended's, before any fallback. The sets of players are searched in the order of the net a cover with them is expected
    to have at the highest prices, a set being left out (with every set that contains it) when no cover with it can net less than the best found, or after
    EVALUATIONS options judged, at most WAITERS players waiting. Only sets none of whose players is superfluous (the others raise the need at
    their standing offers or expected prices) are searched: selling for the money alone is the sale rule's (worth.rent). The recommended never
    has a greater net than the second. `found` unchanged when infeasible or when it sells nothing; with no close left (`n_closes` 0), or when
    no option can be judged, `now` is its sales with a standing offer and `options` stays empty."""
    if not found.feasible or not found.sells:
        return found
    if n_closes < 1:
        return found._replace(now=found.sells)
    judge = Closer(club, side, offers, n_closes, rate)
    need = -(club.cash + club.inflow.expected)
    own = frozenset(club.offers)
    seed = judge.outcome(found.sells & own, found.sells - own) if len(found.sells - own) <= WAITERS else None
    best = seed
    squad = sorted(k for k in club.squad if judge.pricing[k].best > 0)
    last = judge.closes - 1
    top = {k: max(judge.pricing[k].best, worth.offer_price(judge.ratios[-1], club.worth[k], judge.drift[k], last)) for k in club.squad}
    top_gain = gains(club, judge.rated, top)
    credit = {k: max(0.0, g) for k, g in top_gain.items()}
    expected = {k: max(0.0, g) for k, g in gains(club, judge.rated, judge.price).items()}

    def net_bound(chosen: frozenset[str], members: Mapping[str, float]) -> float:
        got = cover(side, chosen, club.squad - chosen, top, max(0.0, need - sum(top.get(k, 0.0) for k in chosen)), members)
        return math.inf if got is None else got.net - sum(top_gain[k] for k in chosen)
    queue = [Node(b, (i,)) for i, k in enumerate(squad) if (b := net_bound(frozenset({k}), expected)) < math.inf]
    heapq.heapify(queue)
    judged = 0
    while queue and judged < EVALUATIONS:
        node = heapq.heappop(queue)
        chosen = frozenset(squad[i] for i in node.members)
        if best is not None and net_bound(chosen, credit) >= best.net - 1e-9:
            continue
        if any(sum(judge.pricing[j].now for j in chosen - {k}) >= need for k in chosen):
            continue
        for i in range(node.members[-1] + 1, len(squad)):
            if (b := net_bound(chosen | {squad[i]}, expected)) < math.inf:
                heapq.heappush(queue, Node(b, (*node.members, i)))
        offered = sorted(chosen & own)
        for m in range(min(len(offered), WAITERS - len(chosen - own)) + 1):
            for held in combinations(offered, m):
                tried = judge.outcome(frozenset(offered) - frozenset(held), (chosen - own) | frozenset(held))
                judged += 1
                if tried is not None and (best is None or tried.net < best.net - 1e-9):
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
    """The best funding `approach` has found: its expected cost `value` (points lost less the money value of the prices fetched), the sales
    `tonight`, those `if_won` and `if_lost` after the result, the points the sales lose in each case and the money value (`credit_*`, in
    points) of the prices they fetch over the players' expected offers."""
    value: float
    tonight: frozenset[str]
    if_won: frozenset[str]
    if_lost: frozenset[str]
    cost_won: float
    cost_lost: float
    credit_won: float = 0.0
    credit_lost: float = 0.0


class Points(NamedTuple):
    """The points of a bid: the `gain` of the buy, the costs of the sales if it is won and if it is lost, the `credit` (their money value over the
    players' expected offers, in points) in each case and `paid` the money value of the bid over what the player is worth."""
    gain: float
    cost_won: float
    cost_lost: float
    credit_won: float = 0.0
    credit_lost: float = 0.0
    paid: float = 0.0


class Money(NamedTuple):
    """How a plan's money is valued: `rate` (hurdle.LAMBDA, points per million per jornada) over `n` jornadas (one n for the whole board: the
    window of a sale made now, so the cover's, the base's and every buy's nets are in one unit), the one conversion
    hurdle.in_points makes; `resale` what the player bought is expected to fetch (hurdle.draw of his worth; None: the bid, a buy at his
    price). `kappa` is the points a euro is worth. Rate 0 values money at nothing: the net is points alone."""
    rate: float = 0.0
    n: int = 0
    resale: float | None = None

    @property
    def kappa(self) -> float:
        return hurdle.in_points(1.0, self.n, self.rate)

    def over(self, price: float, expected: float) -> float:
        """The points a sale at `price` of a player expected to fetch `expected` is worth in money: kappa x (price - expected)."""
        return self.kappa * (price - expected)

    def paid(self, bid: float) -> float:
        """The points a bid costs in money over what the player bought is worth: kappa x (bid - resale), 0 with no resale given."""
        return 0.0 if self.resale is None else self.kappa * (bid - self.resale)


def gains(club: Club, money: Money, price: Mapping[str, float]) -> dict[str, float]:
    """The points a sale of each player of `price` is worth over his expected offer (Money.over against hurdle.draw of his worth): the one gain
    of a sale, which a recourse chooses by (`covers`), a replay reads off (grading.follow) and the Closer sums over the players it sells."""
    return {k: money.over(p, hurdle.draw(club.worth[k])) for k, p in price.items()}


def lost_branch(cost: float, base: float) -> float:
    """The cost of the branch where the bid is lost, never under `base`: after a lost bid the plan is a plan with no buy, and none beats the best."""
    return max(cost, base)


def approach(club: Club, player: str | None, bid: float, win: float, rounds: Rounds, base: float = 0.0,
             sides: dict[tuple[frozenset[str], tuple[int, ...]], Side] | None = None, extra: float = 0.0,
             money: Money = Money()) -> Approach | None:
    """How to fund a bid on `player` (see Approach): the sales tonight, those after the result, their cost in points and the
    net. Costs are over `rounds`: a sale made tonight is gone from the squad in every jornada of `rounds.now`, the player
    bought plays only in `rounds.arrives`, and the sales after the result count only there. Money is valued as the cover values it
    (`money`: a sale fetching more than the player's expected offer gains kappa x the excess, a buy at more than he is worth loses it):
    the funding is the one with the least expected cost, points lost less that. `base` is the cost of the
    cheapest plan with no buy (the cost of `approach` with player None, bid 0, win 1 and `rounds.arrives` = `rounds.now`:
    the gain 0, both costs the cost of the sales that bring cash to 0 or more, less their money value), and the net is measured against it; the
    cost of a lost bid is never under it (after a lost bid the plan is a plan with no buy, and none beats the best: `credit_lost` is capped
    so). `sides` shares the Side of a squad and window (and its memo of costs)
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
    gain = won_side.points - lost_side.points + extra
    pend_n, pend_amount = club.pending
    price = prices(club, player is not None, rounds.closes)
    over = gains(club, money, {k: price[k] for k in club.squad})
    tonight_credit = gains(club, money, {k: v for k, v in club.offers.items() if k in club.squad})
    offered = sorted((k for k in club.squad if k in club.offers),
                     key=lambda k: (per_unit(won_side.cost(frozenset({k})), max(club.offers[k], 1.0)), k))
    reach = [sum(club.offers[k] - club.debt * club.worth[k] for k in offered[i:]) for i in range(len(offered) + 1)]
    more = [sum(max(0.0, tonight_credit[k]) for k in offered[i:]) + sum(max(0.0, x) for x in over.values()) for i in range(len(offered) + 1)]
    best: Choice | None = None
    ceiling = bid_margin(club.cash, sum(club.offers[k] for k in offered), club.team_value,
                         sum(club.worth[k] for k in offered), bid, pend_amount, club.debt)
    stack = [Pick(0, frozenset())]
    while stack:
        pick = stack.pop()
        i, a = pick.i, pick.sold
        cw, cl = won_side.cost(a), lost_side.cost(a)
        early = before.cost(a)
        now_credit = sum(tonight_credit[k] for k in a)
        if cw is None or cl is None or early is None or best is not None and win * cw + (1 - win) * cl + early - now_credit - more[i] >= best.value - 1e-12:
            continue
        raised, sold = sum(club.offers[k] for k in a), sum(club.worth[k] for k in a)
        margin = bid_margin(club.cash, raised, club.team_value, sold, bid, pend_amount, club.debt)
        if margin >= -1e-6:
            need_won = -cash_at_lock(club.cash, raised, bid, 0.0, club.inflow.expected)
            need_lost = -(club.cash + raised + club.inflow.expected)
            credits = over if money.kappa else None
            won = cover(won_side, a, club.squad - a, price, need_won, credits)
            lost = cover(lost_side, a, club.squad - a, price, need_lost, credits)
            if won and lost and len(club.squad) + (player is not None) - len(a) - len(won.sells) + pend_n <= club.squad_max:
                after_won, after_lost = sum(over[k] for k in won.sells), sum(over[k] for k in lost.sells)
                lost_cost = lost.cost + early - now_credit - after_lost
                if player is not None:
                    lost_cost = lost_branch(lost_cost, base)
                ev = win * (won.cost - after_won + early - now_credit) + (1 - win) * lost_cost
                if best is None or ev < best.value:
                    best = Choice(ev, a, won.sells, lost.sells, won.cost + early, lost.cost + early, now_credit + after_won, lost.cost + early - lost_cost)
        elif margin + reach[i] < -1e-6:
            continue
        for j in range(i, len(offered)):
            stack.append(Pick(j + 1, a | {offered[j]}))
    if best is None:
        return unfunded(club, max(0.0, -ceiling), ceiling, gain)._replace(base=base)
    return funded(club, best.tonight, best.if_won, best.if_lost, bid, win,
                  Points(gain, best.cost_won, best.cost_lost, best.credit_won, best.credit_lost, money.paid(bid)), base, price)


def unfunded(club: Club, short: float, margin: float, gain: float) -> Approach:
    """The Approach of a bid no set of the standing offers funds: `short` the money lacking, `margin` the bid margin."""
    return Approach(False, short, frozenset(), frozenset(club.offers) & club.squad, frozenset(), frozenset(), 0.0, margin,
                    0.0, 0.0, 0.0, gain, 0.0, 0.0, 0.0)


def funded(club: Club, tonight: frozenset[str], if_won: frozenset[str], if_lost: frozenset[str], bid: float, win: float,
           points: Points, base: float, price: Mapping[str, float]) -> Approach:
    """The feasible Approach that sells `tonight` to their offers, then `if_won` or `if_lost` at `price` once the result is
    known; `points` the gain of the buy, the costs of the sales if it is won and if it is lost, their money value and what the bid pays over
    the player's worth; `base` the cost of the plan with no buy. The net is the cost of the plan with no buy less the cost of this one, in points:
    win * (gain - (cost_won - credit_won) - paid) - (1 - win) * (cost_lost - credit_lost), against base."""
    gain, cost_won, cost_lost = points.gain, points.cost_won, points.cost_lost
    raised = sum(club.offers[k] for k in tonight)
    later = sum(price[k] for k in if_won)
    return Approach(
        True, 0.0, tonight, (frozenset(club.offers) & club.squad) - tonight, if_won, if_lost, raised,
        bid_margin(club.cash, raised, club.team_value, sum(club.worth[k] for k in tonight), bid, club.pending[1], club.debt),
        cash_at_lock(club.cash, raised, bid, later, club.inflow.expected),
        cash_at_lock(club.cash, raised, bid, later * (1 - SALE_SD), club.inflow.low),
        club.cash + raised + sum(price[k] for k in if_lost) + club.inflow.expected,
        gain, cost_won, cost_lost,
        base - (win * (cost_won - points.credit_won + points.paid - gain) + (1 - win) * (cost_lost - points.credit_lost)),
        base, points.credit_won, points.credit_lost, points.paid)


def replacement(club: Club, sold: str, player: str, bid: float, win: float, rounds: Rounds, money: Money = Money(), base: float = 0.0) -> Approach:
    """How to replace `sold` (a player whose offer is taken tonight and whose sale leaves no legal eleven) by a bid on `player`:
    the Approach of the pair, `sold` the only sale tonight, the sales after the result chosen by `cover`. A jornada with no
    legal eleven scores nothing, so costs count it: before the buy arrives, and if the bid is lost, the squad plays nothing
    where it has no eleven. Infeasible (`short` the money lacking, 0 when the squad or the lock cash cannot be met) when the
    bid is not covered by the offer and the sales after the result, or the squad would exceed its maximum. Money is valued
    as in `approach`, and so is the net: against `base` (the cost of the plan with no buy), with the cost of a lost bid never under it."""
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
    price = prices(club, True, rounds.closes)
    over = gains(club, money, {k: price[k] for k in club.squad})
    won = cover(won_side, tonight, club.squad - tonight, price, -cash_at_lock(club.cash, raised, bid, 0.0, club.inflow.expected),
                over if money.kappa else None)
    if won is None or len(club.squad) - len(tonight) - len(won.sells) + pend_n + 1 > club.squad_max:
        return unfunded(club, 0.0, margin, gain)
    early = before.forgone(tonight)
    now_credit = gains(club, money, {sold: club.offers[sold]})[sold]
    lost_cost = lost_branch(lost_side.forgone(tonight) + early - now_credit, base)
    return funded(club, tonight, won.sells, frozenset(), bid, win,
                  Points(gain, won_side.forgone(tonight | won.sells) + early, lost_side.forgone(tonight) + early,
                         now_credit + sum(over[k] for k in won.sells), lost_side.forgone(tonight) + early - lost_cost, money.paid(bid)), base, price)


class Offer(NamedTuple):
    """A bid on a listing and what it nets: its `amount`, its win chance `win` and the Approach `found` that funds it."""
    amount: float
    win: float
    found: Approach


class Bidding(NamedTuple):
    """The bids tried on one listing, `offers` in ascending price (those with a chance of WIN_FLOOR or more, up to the first the sales cannot fund
    or the one past the best whose net has fallen below 0, or the first of a chance of 1: dearer bids only lose
    net), `best` the one of the greatest net (the lowest price on ties; None when none was funded) and `walk_away` the price at which the net is
    0: interpolated between the last net of 0 or more and the first below it; otherwise, from the one tried (the first when all are below 0, the
    last when none is), its price plus its net over kappa x its chance, which is exact while the funding does not change with the price (the
    highest price tried when kappa is 0; None when the first net is below 0 and the price not positive)."""
    offers: tuple[Offer, ...]
    best: Offer | None
    walk_away: float | None


def zero_price(offers: Sequence[Offer], kappa: float) -> float | None:
    """The price at which the net of `offers` (ascending price, one or more) is 0, as Bidding says."""
    if not offers:
        return None
    below = next((i for i, o in enumerate(offers) if o.found.net < 0 and all(x.found.net < 0 for x in offers[i:])), None)
    if below is None:
        last = offers[-1]
        return last.amount + last.found.net / (kappa * last.win) if kappa > 0 and last.win > 0 else last.amount
    if below == 0:
        first = offers[0]
        got = first.amount + first.found.net / (kappa * first.win) if kappa > 0 and first.win > 0 else None
        return got if got is not None and got > 0 else None
    before, after = offers[below - 1], offers[below]
    return before.amount + (after.amount - before.amount) * before.found.net / (before.found.net - after.found.net)


def advised_offer(curve: Curve, asking: float, ratio: float, chance: float, fund: Callable[[float, float], Approach | None]) -> Offer | None:
    """The Offer of one rung of the ladder: the one place a rung's ratio becomes an amount. The amount is the rung's asking x ratio cleared
    (hurdle.cleared: above the round grid, where the rivals' prices pile), its win chance and its funded net read at that amount, not at the rung's
    (what is bid is what is priced). Over a limit the funding cannot meet (the bid limit: Approach.short is the euros over it) the amount is clamped to
    the limit (whole euros, never under the rung's own amount); if the clamped amount is not funded either (the cash at the lock, the squad cap) the
    rung's own amount is bid. None when even that is not funded."""
    plain = asking * ratio
    amount = hurdle.cleared(plain)
    found = fund(amount, hurdle.at_amount(curve, asking, amount).win)
    if found is not None and not found.feasible and found.short > 0 and amount - found.short >= plain - 1:
        amount = max(plain, math.floor(amount - found.short))
        found = fund(amount, hurdle.at_amount(curve, asking, amount).win)
    if found is None or not found.feasible:
        amount = plain
        found = fund(amount, chance)
    if found is None or not found.feasible:
        return None
    return Offer(amount, hurdle.at_amount(curve, asking, amount).win if amount != plain else chance, found)


def bidding(curve: Iterable[tuple[float, float]], asking: float, fund: Callable[[float, float], Approach | None], kappa: float,
            floor: float = hurdle.WIN_FLOOR) -> Bidding:
    """The Bidding on a listing at `asking` with the auction `curve` (ratio, chance pairs): `fund` (amount, chance) gives the Approach of a bid.
    The rungs are the curve's ratios of 1 or more whose chance is `floor` or more and at least STEP over the last tried (the net moves by the price,
    not the chance), as the curve gives them; each is bid at its amount cleared of the round numbers (advised_offer), its chance and net read there.
    The net is the one objective: the bid is its greatest, and the walk-away its zero."""
    cv = tuple(sorted(curve))
    offers: list[Offer] = []
    last = None
    for ratio, p in cv:
        if ratio < 1.0 or p < floor or last is not None and p < last + STEP:
            continue
        offer = advised_offer(cv, asking, ratio, p, fund)
        if offer is None:
            break
        if offers and offer.amount <= offers[-1].amount:
            continue
        offers.append(offer)
        last = p
        if p >= 0.999 or len(offers) > 1 and offer.found.net < 0 and offer.found.net < offers[-2].found.net:
            break
    best = min(offers, key=lambda o: (-o.found.net, o.amount), default=None)
    return Bidding(tuple(offers), best, zero_price(offers, kappa))


def given(found: Bidding, loss: float, kappa: float) -> Bidding:
    """The Bidding `found` with every bid's net lowered by its win chance times `loss` (the points the buy's gain falls by, win-weighted, once other
    buys are held): the bids stay, the best and the walk-away (zero_price) are read again. `loss` 0 gives `found`; the one way a net is judged
    given other buys, so `stand` and the board read it unchanged. A loss under 1e-9 is rounding and leaves `found` as it is."""
    if not found.offers or abs(loss) < 1e-9:
        return found
    offers = tuple(o._replace(found=o.found._replace(net=o.found.net - o.win * loss)) for o in found.offers)
    return Bidding(offers, min(offers, key=lambda o: (-o.found.net, o.amount)), zero_price(offers, kappa))


def given_at(here: Approach | None, win: float | None, loss: float) -> Approach | None:
    """The Approach `here` of one bid with its net lowered as `given` lowers it (None, or a bid of unknown chance, unchanged)."""
    return here if here is None or win is None else here._replace(net=here.net - win * loss)


STAND_MARGIN = 0.1


class Spend(NamedTuple):
    """The raises already advised on the bids placed, which share one cash at the lock (and one bid limit): `total` their extra spend over what the bids
    placed cost, `floor` the least room (`room`) any of them rests on. A raise of `delta` on a bid whose room is `h` fits when delta <= min(h, floor) - total:
    the cash it leaves, in the case it is won with every raise advised before it won too, is not below 0. Raises are cumulative, in the order stand is called."""
    total: float = 0.0
    floor: float = math.inf

    def left(self, room: float) -> float:
        """What a raise on a bid of room `room` may still add."""
        return min(room, self.floor) - self.total

    def after(self, delta: float, room: float) -> "Spend":
        return Spend(self.total + delta, min(self.floor, room))


def room(found: Approach | None) -> float:
    """What a bid funded as `found` can still spend before the cash at the lock, if it is won, or the bid limit, would be below 0: the least of `lock_margin`
    and `margin` (-inf when it is not funded)."""
    return min(found.margin, found.lock_margin) if found is not None and found.feasible else -math.inf


class Stand(NamedTuple):
    """What to do with a bid placed: `move` (hold, lower, withdraw or raise) and `to`, the amount to move it to (lower and raise; None otherwise).
    `cut` is the dearer bid the cash at the lock did not allow when the raise was cut back to `to` or held (else None); `spent` the raises advised
    up to and with this one."""
    move: str
    to: float | None
    cut: float | None = None
    spent: Spend = Spend()


def stand(found: Bidding, here: Approach | None, placed: float, spent: Spend = Spend()) -> Stand:
    """The move for a bid placed at `placed` on a listing whose Bidding is `found`, `here` the Approach that funds it at `placed` (None when none
    does): the same funded net that advises a new bid, never a second valuation. withdraw iff no bid tried has a net above 0. Otherwise lower
    (to the best bid) when the placed bid's net is not above 0 and the best is under it, raise (to the best) when its net is not above 0 and
    the best is over it, or when the best bid is dearer than the placed one and nets more; else, a placed bid whose net is above 0 is never lowered,
    and one under its cleared amount (hurdle.cleared: just under a round number) is raised to it when the walk-away and what `here` has left of
    the bid limit and of the cash at the lock allow it; else hold. Every raise, on either path, must fit the room (`room(here)`: the cash at the lock if
    the bid is won, and the bid limit) that the raises already advised (`spent`) have left; when the best bid does not fit, the raise is cut back to the
    dearest bid of the Bidding that fits (its net read there, no new valuation) and still nets as the raise requires, else the bid is held; `cut` says it.
    With no `here` funded, the room is the one the bid's own funding leaves, less what it adds over `placed`."""
    best = found.best
    here_net = here.net if here is not None and here.feasible else None
    if best is None or max(best.found.net, here_net if here_net is not None else best.found.net) <= 0:
        return Stand("withdraw", None)
    if here_net is None or here_net <= 0:
        if best.amount == placed:
            return Stand("hold", None)
        if best.amount < placed:
            return Stand("lower", best.amount)
        return _raise(found, here, placed, spent, lambda o: o.found.net > 0)
    if best.amount > placed and best.found.net > here_net + STAND_MARGIN:
        return _raise(found, here, placed, spent, lambda o: o.found.net > here_net + STAND_MARGIN)
    clear = hurdle.cleared(placed)
    if here is not None and clear > placed and clear <= (found.walk_away if found.walk_away is not None else placed) \
            and clear - placed <= spent.left(room(here)):
        return Stand("raise", clear, None, spent.after(clear - placed, room(here)))
    return Stand("hold", None)


def _raise(found: Bidding, here: Approach | None, placed: float, spent: Spend, nets: Callable[[Offer], bool]) -> Stand:
    """The raise of a bid placed at `placed` to the best bid of `found`, or to the dearest bid under it that `nets` accepts and that fits (Stand: cut says
    the best bid did not), or a hold when none fits."""
    assert found.best is not None

    def fits(o: Offer) -> bool:
        own = room(here) if here is not None and here.feasible else room(o.found) + o.amount - placed
        return o.amount - placed <= spent.left(own) + 1e-6
    ok = [o for o in found.offers if placed < o.amount <= found.best.amount and nets(o) and fits(o)]
    got = max(ok, key=lambda o: o.amount, default=None) if found.best not in ok else found.best
    if got is None:
        return Stand("hold", None, found.best.amount, spent)
    own = room(here) if here is not None and here.feasible else room(got.found) + got.amount - placed
    return Stand("raise", got.amount, None if got is found.best else found.best.amount, spent.after(got.amount - placed, own))


def _town(rng: random.Random, case: int) -> tuple[Club, Rounds, float, float]:
    own = {"m%02d" % i: p for i, p in enumerate(["POR"] + ["DEF"] * 5 + ["MED"] * 5 + ["DEL"] * 3)}
    pos = own | {"star": "DEL"}
    cells = {j: {k: v * 2.0 for k, v in c.items()} | {"star": 18.0} for j, c in _cells(rng, own, (1, 2, 3), [0, 1, 2, 3, 5]).items()}
    now = valuation.Window((1, 2, 3))
    rounds = Rounds(cells, now, valuation.Window(now[case % 3:]))
    worth = {k: rng.choice([2e6, 4e6, 6e6]) for k in own}
    offers = {k: worth[k] * rng.choice([0.9, 1.0, 1.05]) for k in rng.sample(sorted(own), 4)}
    club = Club(frozenset(own), pos, worth, offers, rng.choice([-5e6, -1e6, 3e6]), 5e6 * len(own))
    return club, rounds, rng.choice([8e6, 12e6, 16e6]), rng.choice([0.3, 0.7, 0.95])


def _selftest_money() -> None:
    rng = random.Random(154)
    rate = 0.3
    exercised = clamped = 0
    for case in range(9):
        club, rounds, bid, win = _town(rng, case)
        cells, pos = rounds.cells, club.pos
        squad, here = club.squad, club.squad | {"star"}
        n = len(rounds.arrives)
        spend = Money(rate, n, hurdle.draw(rng.choice([6e6, 10e6, 14e6])))
        base = approach(club, None, 0.0, 1.0, rounds, money=Money(rate, n))
        assert base is not None
        base_j = -base.net
        got = approach(club, "star", bid, win, rounds, base_j, None, 0.0, spend)
        assert got is not None
        free = approach(club, "star", bid, win, rounds, 0.0, None, 0.0, Money(0.0, n, spend.resale))
        zero = approach(club, "star", bid, win, rounds, 0.0)
        assert free is not None and zero is not None and free.net == zero.net and free.feasible == zero.feasible, "rate 0: money is worth nothing, the net is points"

        @lru_cache(maxsize=None)
        def points(sq: frozenset[str], js: tuple[int, ...]) -> float | None:
            seen = [eleven.best_total(sq, pos, cells[j]) for j in js]
            return None if None in seen else sum(x for x in seen if x is not None)
        total = points(squad, tuple(rounds.now))
        assert total is not None
        kappa = spend.kappa
        price = {k: hurdle.draw(club.worth[k]) for k in squad}
        best: float | None = None
        for m in range(len(club.offers) + 1):
            for sold in combinations(sorted(club.offers), m):
                a = frozenset(sold)
                pa, va = sum(club.offers[k] for k in a), sum(club.worth[k] for k in a)
                if bid_margin(club.cash, pa, club.team_value, va, bid, 0.0) < -1e-6:
                    continue
                credit = kappa * sum(club.offers[k] - price[k] for k in a)
                rest = sorted(squad - a)

                def after(star: bool, b: frozenset[str]) -> float | None:
                    early = points(squad - a, rounds.before)
                    late = points((here if star else squad) - a - b, tuple(rounds.arrives))
                    return None if early is None or late is None else early + late
                won = [x for r in range(len(rest) + 1) for b in combinations(rest, r)
                       if cash_at_lock(club.cash, pa, bid, sum(price[k] for k in b)) >= -1e-6
                       and len(here - a - frozenset(b)) <= SQUAD_MAX and (x := after(True, frozenset(b))) is not None]
                lost = [x for r in range(len(rest) + 1) for b in combinations(rest, r)
                        if club.cash + pa + sum(price[k] for k in b) >= -1e-6 and (x := after(False, frozenset(b))) is not None]
                if won and lost:
                    value = win * (max(won) + credit - spend.paid(bid)) + (1 - win) * min(max(lost) + credit, total - base_j)
                    best = value if best is None else max(best, value)
        assert got.feasible == (best is not None)
        if best is None:
            continue
        exercised += 1
        assert abs(got.net - (best - total + base_j)) < 1e-6, (got.net, best - total + base_j)
        assert got.cost_lost - got.credit_lost >= base_j - 1e-9, "after a lost bid no plan beats the best plan with no buy"
        clamped += got.cost_lost - got.credit_lost <= base_j + 1e-9
        pure = {k: "z" + k for k in club.squad | {"star"}}
        renamed = Club(frozenset(pure[k] for k in club.squad), {pure[k]: v for k, v in club.pos.items()}, {pure[k]: v for k, v in club.worth.items()},
                       {pure[k]: v for k, v in club.offers.items()}, club.cash, club.team_value)
        again = approach(renamed, "zstar", bid, win, Rounds({j: {pure[k]: v for k, v in c.items()} for j, c in cells.items()}, rounds.now, rounds.arrives),
                         base_j, None, 0.0, spend)
        assert again is not None and abs(again.net - got.net) < 1e-9 and again.feasible == got.feasible, "the net does not depend on how the players are named"
        double = Club(club.squad, club.pos, {k: 2 * v for k, v in club.worth.items()}, {k: 2 * v for k, v in club.offers.items()}, 2 * club.cash, 2 * club.team_value)
        twice = approach(double, "star", 2 * bid, win, rounds, base_j, None, 0.0, Money(rate / 2, n, None if spend.resale is None else 2 * spend.resale))
        assert twice is not None and twice.feasible and abs(twice.net - got.net) < 1e-6, "twice the money at half the rate is the same plan and the same net"
    assert exercised >= 5 and clamped >= 1, (exercised, clamped)


def _selftest_after_result() -> None:
    rng = random.Random(155)
    changed = 0
    for case in range(9):
        club, rounds, bid, win = _town(rng, case)
        n = len(rounds.arrives)
        spend = Money(0.3, n, hurdle.draw(rng.choice([6e6, 10e6, 14e6])))
        base = approach(club, None, 0.0, 1.0, rounds, money=Money(0.3, n))
        assert base is not None
        plain = approach(club, "star", bid, win, rounds, -base.net, None, 0.0, spend)
        assert plain is not None
        for drift in (-0.02, 0.02):
            moving = club._replace(drift={k: drift for k in club.squad})
            same = approach(moving, "star", bid, win, rounds, -base.net, None, 0.0, spend)
            assert same is not None and same.feasible == plain.feasible and abs(same.net - plain.net) < 1e-9, "one close left: the sale after the result is at the expected offer"
            later = approach(moving, "star", bid, win, rounds._replace(closes=3), -base.net, None, 0.0, spend)
            assert later is not None
            if not plain.feasible:
                continue
            assert (later.net <= plain.net + 1e-9) if drift < 0 else (later.net >= plain.net - 1e-9 and later.feasible), \
                "every sale after the result fetches less (more) as the value falls (rises) over the nights: the best net can not be more (less)"
            changed += abs(later.net - plain.net) > 1e-6
            if not (plain.if_won or plain.if_lost) and not (later.if_won or later.if_lost):
                assert abs(later.net - plain.net) < 1e-9, "nothing sold after the result: the drift does not matter"
    assert changed >= 3, changed
    rng = random.Random(156)
    for case in range(30):
        squad_pos = {"m%02d" % i: p for i, p in enumerate(["POR"] + ["DEF"] * 4 + ["MED"] * 4 + ["DEL"] * 2)}
        leaver = rng.choice(sorted(squad_pos))
        places = squad_pos | {"new": squad_pos[leaver]}
        now = valuation.Window((1, 2, 3))
        rounds = Rounds(_cells(rng, places, (1, 2, 3), [0, 1, 2, 3, 5]), now, valuation.Window(now[case % 3:]))
        club = Club(frozenset(squad_pos), places, {k: 4e6 for k in squad_pos}, {leaver: rng.choice([4e6, 6e6])}, 20e6, 5e7)
        floor = rng.choice([0.0, 1.0, 6.0])
        got = replacement(club, leaver, "new", 6e6, rng.choice([0.4, 0.8]), rounds, Money(0.3, 3, hurdle.draw(5e6)), floor)
        assert got.feasible and got.base == floor
        assert got.cost_lost - got.credit_lost >= floor - 1e-9, "after a lost bid no plan beats the plan with no buy, a pair's as a buy's"
    walk = bidding(((1.0, 0.5), (1.1, 0.8), (1.2, 0.95)), 10e6, lambda amount, win: _stub(3.0 - (amount - 10e6) / 1e6 * 1.0), 1e-6)
    assert walk.best is not None and walk.best.amount == hurdle.cleared(10e6)
    held = {m: stand(walk, _stub(x)._replace(margin=0.0, lock_margin=0.0), 11e6).move for m, x in (("a", 2.5), ("b", -1.0))}
    assert held == {"a": "hold", "b": "lower"}, held  # no room (margin, lock_margin 0): the cleared amount is not bid
    assert stand(walk, None, 12e6).move == "lower" and stand(walk, None, 9e6).move == "raise", "unfundable at the placed amount: to the best bid"
    assert stand(walk, _stub(2.0), walk.best.amount) == Stand("hold", None) and stand(walk._replace(best=walk.offers[1]), _stub(1.0), walk.best.amount)[:2] == ("raise", walk.offers[1].amount)
    nothing = bidding(((1.0, 0.5),), 10e6, lambda amount, win: _stub(-1.0), 1e-6)
    assert stand(nothing, _stub(-1.0), 10e6) == Stand("withdraw", None) and stand(nothing._replace(best=None), None, 10e6).move == "withdraw"


def _stub(net: float) -> Approach:
    return Approach(True, 0.0, frozenset(), frozenset(), frozenset(), frozenset(), 0.0, 1e12, 1e12, 1e12, 0.0, 0.0, 0.0, 0.0, net)


def _selftest_bidding() -> None:
    rng = random.Random(154)
    grid = (1.0, 1.02, 1.05, 1.1, 1.2, 1.3, 1.5)
    seen = {"inside": 0, "never": 0, "already": 0, "none": 0}
    for _ in range(2000):
        asking = rng.choice([1e6, 10e6, 40e6])
        kappa = rng.choice([0.2, 0.9, 2.0]) / 1e6
        top = rng.uniform(-3.0, 30.0)
        dead = rng.random() < 0.1
        curve = tuple((r, 0.1 if dead else min(1.0, rng.choice([0.1, 0.3, 0.6]) + (r - 1.0) * rng.choice([0.5, 2.0, 5.0]))) for r in (0.9, *grid))
        shape = rng.choice(["flat", "plus"])

        def fund(amount: float, win: float, top: float = top, kappa: float = kappa, asking: float = asking, shape: str = shape) -> Approach | None:
            return _stub(win * (top - kappa * (amount - asking)) + (win * 1.0 if shape == "plus" else 0.0))
        got = bidding(curve, asking, fund, kappa)
        tried = [(r, p) for r, p in curve if r >= 1.0 and p >= hurdle.WIN_FLOOR]
        steady = all(a[1] <= b[1] for a, b in zip(sorted(curve), sorted(curve)[1:]))
        assert [o.amount for o in got.offers] == sorted(o.amount for o in got.offers) and all(o.amount >= asking for o in got.offers)
        rungs = sorted({r for r, _ in tried})
        assert all(any(o.amount == hurdle.cleared(asking * r) for r in rungs) for o in got.offers), "each bid is a rung of the curve, cleared of the round numbers"
        assert not steady or [o.win for o in got.offers] == sorted(o.win for o in got.offers) and all(o.win >= hurdle.WIN_FLOOR for o in got.offers), "on a curve that never falls the chance never falls up the ladder"
        for o in got.offers:
            r = next(r for r in rungs if o.amount == hurdle.cleared(asking * r))
            assert o.amount >= asking * r and (not steady or o.win >= dict(tried)[r] - 1e-12), "never under the rung's amount, and its chance is read at the amount: not below the rung's"
            assert o.win == hurdle.at_amount(tuple(sorted(curve)), asking, o.amount).win
        if not tried:
            assert got.best is None and got.walk_away is None and not got.offers
            seen["none"] += 1
            continue
        assert got.best is not None and all(o.found.net <= got.best.found.net + 1e-12 for o in got.offers), "the bid is the greatest net tried"
        away = got.walk_away
        if away is not None and got.best.found.net > 0:
            assert got.best.amount <= away + 1e-6, "a bid whose net is above 0 is never over the walk-away"
        if away is None:
            continue
        nets = [o.found.net for o in got.offers]
        if all(x >= 0 for x in nets):
            last = got.offers[-1]
            assert abs(away - (last.amount + last.found.net / (kappa * last.win))) < 1e-3, "net never below 0: the price where the last would reach 0"
            seen["never"] += 1
        elif nets[0] < 0 and all(x < 0 for x in nets):
            first = got.offers[0]
            assert abs(away - (first.amount + first.found.net / (kappa * first.win))) < 1e-3, "a net below 0 at the lowest price: the price where it would be 0"
            seen["already"] += 1
        else:
            seen["inside"] += 1
            below = next(i for i, x in enumerate(nets) if x < 0 and all(y < 0 for y in nets[i:]))
            lo, hi = got.offers[below - 1], got.offers[below]
            assert lo.amount <= away <= hi.amount and lo.found.net >= 0 > hi.found.net, "the walk-away lies between the last net of 0 or more and the first below it"
            line = lo.found.net + (hi.found.net - lo.found.net) * (away - lo.amount) / (hi.amount - lo.amount)
            assert abs(line) < 1e-6 * (1 + abs(lo.found.net)), "the net is 0 at the walk-away price"
    assert all(v > 20 for v in seen.values()), seen
    refused = bidding(((1.0, 0.9), (1.1, 1.0)), 1e6, lambda amount, win: _unfundable(), 1e-6)
    assert refused.best is None and refused.offers == () and refused.walk_away is None, "no funded price: nothing is bid"
    _selftest_limits()
    _selftest_raise_cash()


def _selftest_limits() -> None:
    """A bid cleared of the round numbers never passes a limit the funding has: over the bid limit (short says by how much) it is clamped to the
    limit, never under the rung's own amount; over a limit that does not say (the cash at the lock) the rung's own amount is bid."""
    ladder = ((1.0, 0.3), (1.01, 0.5), (1.02, 0.7), (1.05, 0.9), (1.1, 1.0))
    asking = 10e6
    for limit in (10_000_000.0, 10_000_005.0, 10_000_011.0, 10_000_400.0, 10_149_999.5, 10_200_000.0, 10_700_000.0, 11_500_000.0):
        for says in (True, False):
            def fund(amount: float, win: float, limit: float = limit, says: bool = says) -> Approach | None:
                return _stub(win * 3.0 - amount / asking) if amount <= limit else _unfundable()._replace(short=amount - limit if says else 0.0)
            got = bidding(ladder, asking, fund, 1e-6)
            assert all(o.amount <= limit for o in got.offers), "never over the limit"
            assert all(o.amount >= asking * r for o, r in zip(got.offers, (1.0, 1.01, 1.02, 1.05, 1.1))), "never under the rung's own amount"
            if says and limit >= 10_000_011.0 or not says and limit >= hurdle.cleared(asking):
                assert got.offers and got.offers[0].amount == hurdle.cleared(asking), "room for the cleared amount: it is bid"
            if says and limit < hurdle.cleared(asking) and limit >= asking:
                assert got.offers[0].amount == math.floor(limit), "over the limit by the clearance: clamped to the limit"
            if not says and limit < hurdle.cleared(asking) and limit >= asking:
                assert got.offers[0].amount == asking, "a limit that is not said: the rung's own amount"
    placed = bidding(ladder, asking, lambda amount, win: _stub(win * 3.0 - amount / asking), 1e-6)
    assert placed.best is not None and placed.walk_away is not None
    assert all(b.amount > a.amount for a, b in zip(placed.offers, placed.offers[1:])), "the offers strictly increase in amount"
    room = _stub(placed.best.found.net)._replace(margin=1e9, lock_margin=1e9)
    for over in (0.0, 1.0, 5e4, 2e5):
        mv = stand(placed, room, hurdle.cleared(placed.best.amount) + over)
        assert mv.move == "hold" or mv.to is not None and mv.to > hurdle.cleared(placed.best.amount) + over, "a placed bid at or above its cleared amount, its net above 0, is never lowered"
    rng = random.Random(5)
    for _ in range(500):
        at = rng.uniform(asking, placed.walk_away * 1.01)
        clear = hurdle.cleared(at)
        free = rng.choice([0.0, clear - at - 1.0, clear - at, 1e9])
        here = _stub(rng.choice([-1.0, 0.5, 2.0]))._replace(margin=free, lock_margin=free)
        mv = stand(placed, here, at)
        if mv.move == "raise" and mv.to == clear and clear > at and clear != placed.best.amount and mv.cut is None:
            assert here.net > 0 and clear <= placed.walk_away and free >= clear - at, "raised to the cleared amount: net above 0, within the walk-away and the room"
        if clear == at and here.net > 0 and placed.best.amount <= at:
            assert mv.move == "hold", "a placed amount already cleared, its net above 0, is left alone"
        if here.net <= 0:
            assert mv.to is None or mv.to == placed.best.amount or mv.cut is not None, "a net not above 0 moves to the best bid (or the dearest bid of the room), not to the cleared amount"
        if mv.to is not None and mv.move == "raise":
            assert mv.to > at and (mv.to <= placed.walk_away or mv.to == placed.best.amount), "a raise never lowers, and stays within the walk-away"
    snug = _stub(2.9)._replace(margin=1e9, lock_margin=1e9)
    low = 10_100_000.0 - 5.0
    assert stand(placed, snug, low)[:2] == ("raise", hurdle.cleared(low)) == ("raise", 10_100_011.0), "just under a round number: raised to the cleared amount"


def _selftest_raise_cash() -> None:
    """A raise never takes the cash at the lock (or the bid limit) below 0, on either path, alone or with the other raises advised: the cash a placed bid
    leaves if won, `room`, less the raises already advised (Spend) fits the extra the raise spends; a raise that fits is the advised one, a raise that does
    not is cut back to the dearest bid tried that fits (monotone in the room) or held, and the whole holds when every amount is scaled."""
    rng = random.Random(27)
    ladder = ((1.0, 0.3), (1.02, 0.5), (1.05, 0.7), (1.1, 0.85), (1.2, 0.95))
    cut = raised = 0
    for scale in (1.0, 3.0, 0.25):
        asking = 10e6 * scale
        peak = bidding(ladder, asking, lambda amount, win: _stub(win * 3.0 - 0.5 * abs(amount / asking - 1.15)), 1e-6)
        assert peak.best is not None and len(peak.offers) >= 4
        for case in range(300):
            placed = asking * rng.choice([0.97, 1.0, 1.01, 1.03])
            lock = rng.choice([0.0, 0.2, 0.5, 1.0, 2.0, 5.0, 50.0]) * asking * 0.1
            here = _stub(rng.choice([-1.0, 0.5, 1.0]))._replace(lock_margin=lock, margin=rng.choice([lock, 1e12]))
            chain = Spend(rng.choice([0.0, 0.1 * lock, 0.6 * lock]), rng.choice([math.inf, 2 * lock]))
            at = stand(peak, here, placed, chain)
            if at.move == "raise" and at.to is not None:
                raised += 1
                extra = at.to - placed
                assert extra <= chain.left(room(here)) + 1e-6 and chain.total + extra <= min(here.lock_margin, here.margin, chain.floor) + 1e-6, "cash at the lock if won stays 0 or more"
                assert at.spent == chain.after(extra, room(here)), "the raises are cumulative"
                if at.cut is not None:
                    cut += 1
                    assert at.cut == peak.best.amount and at.to < at.cut and at.to in [o.amount for o in peak.offers], "cut back to a bid tried"
                    assert all(o.amount - placed > chain.left(room(here)) for o in peak.offers if o.amount > at.to and o.amount <= at.cut and o.found.net > 0), "the dearest that fits"
                else:
                    assert at.to == peak.best.amount or at.to == hurdle.cleared(placed), "a raise that fits is the advised one"
            elif at.cut is not None:
                assert at.move == "hold" and at.cut == peak.best.amount, "a raise that does not fit is held, and says so"
            wide = stand(peak, here._replace(lock_margin=1e12, margin=1e12), placed)
            if at.move == "raise" and at.cut is None and here.net > 0 or here.net <= 0 and at.move == "raise" and at.cut is None:
                assert (wide.move, wide.to) == (at.move, at.to), "a raise that fits is unchanged by more room"
            more = stand(peak, here, placed, chain._replace(total=0.0))
            if at.move == "raise" and more.move == "raise" and at.to is not None and more.to is not None:
                assert more.to >= at.to, "the less already spent, the dearer the raise: monotone"
            elif at.move == "raise" and at.cut is not None:
                assert more.move == "raise", "more room never turns a cut raise into a hold"
    assert cut > 50 and raised > 200, (cut, raised)
    # cumulative across several placed bids in any order: the extra spent never passes the room of any of them
    for _ in range(200):
        bids = [(10e6 * rng.choice([1.0, 2.0, 4.0]), _stub(rng.choice([-1.0, 1.0]))._replace(lock_margin=rng.choice([0.2e6, 1e6, 3e6, 20e6]))) for _ in range(rng.randint(2, 5))]
        chain, rooms = Spend(), []
        for asking, here in bids:
            def net(amount: float, win: float, a: float = asking) -> Approach:
                return _stub(win * 3.0 - 0.5 * abs(amount / a - 1.15))
            book = bidding(ladder, asking, net, 1e-6)
            at = stand(book, here._replace(margin=here.lock_margin), asking, chain)
            if at.move == "raise":
                chain = at.spent
                rooms.append(here.lock_margin)
                assert chain.total <= min(rooms) + 1e-6, "total extra spend across the raises stays within the least lock cash"
    # no bid funded at the placed amount: the room is the bid's own funding at the target
    book = bidding(ladder, 10e6, lambda amount, win: _stub(win * 3.0 - 0.5 * abs(amount / 10e6 - 1.15))._replace(lock_margin=0.5e6 + (amount - 10e6) * 0.0), 1e-6)
    assert stand(book, None, 9e6, Spend(1e6, math.inf)).move == "hold" and stand(book, None, 9e6, Spend(0.4e6, math.inf)).move == "raise", "cash at the target, less the others' raises"


def _unfundable() -> Approach:
    return _stub(0.0)._replace(feasible=False)


def _cells(rng: random.Random, players: Iterable[str], jornadas: Iterable[int], pick: Iterable[float]) -> dict[int, dict[str, float]]:
    return {j: {k: rng.choice(list(pick)) for k in players} for j in jornadas}


def _star() -> tuple[Club, Side, Plan]:
    """A squad of 16 in which one player (m07, 32.47M) is the only cover of a 31M deficit, the rest worth 1M, over a season of 25 jornadas."""
    star = {"m%02d" % i: p for i, p in enumerate(["POR"] * 2 + ["DEF"] * 6 + ["MED"] * 5 + ["DEL"] * 3)}
    season = tuple(range(1, 26))
    cells = {j: {k: (0.04 if k in ("m14", "m15") else 2.0) for k in star} for j in season}
    dear = {k: (32.47e6 if k == "m07" else 1.0e6) for k in star}
    club = Club(frozenset(star), star, dear, {"m07": 0.982 * dear["m07"]}, -31.07e6, 5e7)
    rounds = Rounds(cells, valuation.Window(season), valuation.Window(season))
    side = Side(club.squad, star, cells, rounds.arrives)
    found = plan(club, approach(club, None, 0.0, 1.0, rounds), rounds, 3, side, rate=hurdle.FALLBACK_LAMBDA)
    assert found is not None
    return club, side, found


def _selftest_offer_model() -> None:
    """The properties of the offer model the sale side reads: existence (OfferDistribution.exists) and drift (Club.drift)."""
    study = offer_ratio_distribution([NormalDist(0.997, 0.057).inv_cdf((i + 0.5) / 100) for i in range(100)], 1.0)
    ratios = ratios_of(study)
    rng = random.Random(8)
    for _ in range(60):
        value, e, d = rng.uniform(1e6, 4e7), rng.choice([0.0, 0.3, 0.93, 1.0]), rng.uniform(-0.03, 0.03)
        closes = rng.randint(1, 5)
        atoms = sold_at(value, ratios, [0.0] * closes, e, d)
        assert abs(sum(a.p for a in atoms) - (1 - (1 - e) ** closes)) < 1e-9, "a player taking any offer is sold unless no offer exists at any close"
        mean = sum(worth.offer_price(sum(ratios) / len(ratios), value, d, c) * e * (1 - e) ** c for c in range(closes))
        assert abs(sum(a.p * a.money for a in atoms) - mean) < 1e-3, "the money is the offers at each night's value, weighted by reaching that night"
        assert sum(a.p * a.money for a in sold_at(value, ratios, [0.0] * closes, e, d - 0.01)) <= sum(a.p * a.money for a in atoms) + 1e-6, "a faster fall never raises the money"
        need = rng.uniform(0.8, 1.2) * value
        picky = sold_at(value, ratios, [need] * (closes - 1) + [0.0], e, d)
        assert sum(a.p for a in picky) <= sum(a.p for a in atoms) + 1e-9, "a reserve never sells more often"
        assert sum(a.p for a in sold_at(value, ratios, [need] * closes, e, d)) <= sum(a.p for a in picky) + 1e-9 and all(a.money >= need for a in sold_at(value, ratios, [need] * closes, e, d)), "a sale under a reserve is at or above it"
    club, side, found = _star()
    assert club.drift == {} and Closer(club, side, study, 3).drift == {k: 0.0 for k in club.squad}, "no drift given: none"
    tried: dict[object, Option] = {}
    for e in (0.0, 0.5, 0.93, 1.0):
        for d in (-0.02, 0.0, 0.02):
            judge = Closer(club._replace(drift={"m07": d}), side, study._replace(exists=e), 3, hurdle.FALLBACK_LAMBDA)
            assert abs(judge.price["m07"] - hurdle.drifted(judge.pricing["m07"].expected, d, 2)) < 1e-6, "the recourse sells at the value the drift leaves by the last close"
            waiting, now = judge.outcome(frozenset(), frozenset({"m07"})), judge.outcome(frozenset({"m07"}), frozenset())
            assert waiting is not None and now is not None
            tried[e, d] = waiting
            assert now.cash >= -1e-6 and abs(now.points - tried.setdefault("now", now).points) < 1e-9 and abs(now.cash - tried["now"].cash) < 1e-6, \
                "a sale tonight is at the standing offer, which neither existence nor drift moves"
            assert waiting.cash <= judge.club.cash + judge.club.inflow.expected + 1.2 * 32.47e6 * (1 + max(d, 0) * 3), "never more cash than the best offer at its night's value"
    for d in (-0.02, 0.0, 0.02):
        costs = [tried[e, d].points for e in (0.0, 0.5, 0.93, 1.0)]
        assert all(a >= b - 1e-6 for a, b in zip(costs, costs[1:])), "offers that are likelier to exist never raise the points lost"
    for e in (0.0, 0.5, 0.93, 1.0):
        nets = [tried[e, d].net for d in (-0.02, 0.0, 0.02)]
        assert all(a >= b - 1e-6 for a, b in zip(nets, nets[1:])), "a price that rises is never worse for the wait, a falling one never better, at any existence"
    for d in (-0.02, 0.0, 0.02):
        nets = [tried[e, d].net for e in (0.0, 0.5, 0.93, 1.0)]
        assert all(a >= b - 1e-6 for a, b in zip(nets, nets[1:])), "offers that are likelier to exist never raise the net: an offer is an option, declining it is free"
    assert all(abs(tried[0.0, d].net - tried[0.0, 0.0].net) < 1e-6 and sum(a.p for end in Closer(club._replace(drift={"m07": d}), side, study._replace(exists=0.0), 3).money(frozenset(), frozenset({"m07"}))[1][0] if end.sold for a in end.atoms) < 1e-12
               for d in (-0.02, 0.0, 0.02)), "no offer ever exists: the option is the no-sale branch, whatever the drift"
    _selftest_unsold(club, side, study)
    twins = [Closer(club, side, study._replace(exists=e), 3, 0.1) for e in (0.2, 0.9)]
    assert all(c.exists == e for c, e in zip(twins, (0.2, 0.9))), "the Closer reads the existence of the offer model"
    assert secure(club, found, side, study._replace(exists=1.0), 3).options and secure(club, found, side, study._replace(exists=0.2), 3).options[0].points >= \
        secure(club, found, side, study._replace(exists=1.0), 3).options[0].points - 1e-6, "the secured option reads the existence"


def _selftest_unsold(club: Club, side: Side, study: worth.OfferDistribution) -> None:
    """Not selling is worth one thing (the unsold payoff: the player kept, no gain, not in the recourse's pool), whether no offer came, one came
    and was declined or the listing lapsed; the waiting recursion is translation and scale invariant, and at existence 1 the hold does not
    matter where it is at most the lowest payoff (the old rule, which held the lowest offer, agrees exactly there)."""
    rng = random.Random(5)
    for _ in range(40):
        m, e = rng.randint(0, 5), rng.choice([0.0, 0.2, 0.5, 0.93, 1.0])
        u = tuple(sorted(rng.uniform(-9, 9) for _ in range(rng.randint(1, 6))))
        hold, c, s = rng.uniform(-12, 12), rng.uniform(-5, 5), rng.uniform(0.2, 5)
        base = worth.waiting_values(m, u, hold, e)
        assert all(abs(a - (b + c)) < 1e-9 for a, b in zip(worth.waiting_values(m, tuple(x + c for x in u), hold + c, e), base)), "a constant added to every payoff and to the hold shifts the values"
        assert all(abs(a - b * s) < 1e-9 for a, b in zip(worth.waiting_values(m, tuple(x * s for x in u), hold * s, e), base)), "payoffs and hold scaled scale the values"
        assert all(a >= hold - 1e-12 for a in base) and all(a <= b + 1e-12 for a, b in zip(base, base[1:])), "waiting is worth at least not selling, more with more closes"
        assert all(a <= b + 1e-12 for a, b in zip(base, worth.waiting_values(m, u, hold, min(1.0, e + 0.1)))), "likelier offers never lower the value, for any hold"
        if e == 0.0:
            assert base == (hold,) * (m + 1), "no offer ever exists: the value is not selling"
        if e == 1.0:
            assert worth.waiting_values(m, u, min(hold, u[0]), 1.0)[1:] == worth.waiting_values(m, u, u[0], 1.0)[1:], "at existence 1 a hold at or under the lowest payoff is no part of the values"
    none = Closer(club._replace(drift={"m07": 0.0}), side, study._replace(exists=0.0), 3, hurdle.FALLBACK_LAMBDA)
    poor = study._replace(ratios=tuple(1e-3 * r for r in study.ratios), exists=1.0)
    declined = Closer(club._replace(drift={"m07": 0.0}), side, poor, 3, hurdle.FALLBACK_LAMBDA).outcome(frozenset(), frozenset({"m07"}))
    missing = none.outcome(frozenset(), frozenset({"m07"}))
    assert declined is not None and missing is not None and abs(declined.net - missing.net) < 1e-6 and abs(declined.points - missing.points) < 1e-6 and \
        abs(declined.cash - missing.cash) < 1e-6, "an offer declined, no offer and a lapsed listing all give the unsold payoff"
    assert all(r.least[-1] == math.inf for r in declined.wait), "no offer reaches the unsold payoff: the last close declines whatever comes"
    for d in (-0.02, 0.0, 0.02):
        sure = Closer(club._replace(drift={"m07": d}), side, study._replace(exists=1.0), 3, hurdle.FALLBACK_LAMBDA)
        (reserve,), (ends,) = sure.money(frozenset(), frozenset({"m07"}))
        taken = sum(a.p for end in ends if end.sold for a in end.atoms)
        lowest = worth.offer_price(sure.ratios[0], club.worth["m07"], d, 2)
        assert reserve.least[-1] > lowest + 1e-9 or abs(taken - 1.0) < 1e-9, "at existence 1, where keeping him is worth no more than the lowest offer, every offer is taken: the old rule"
    step = bucket_of(5e6)
    lacks = [Closer(club, side, study._replace(exists=e), 3).fill(frozenset(), frozenset(), step) for e in (1.0, 0.93, 0.5, 0.2)]
    points = [f.points for f in lacks if f is not None]
    assert len(points) == len(lacks) and all(a <= b + 1e-9 for a, b in zip(points, points[1:])), "the recourse never costs fewer points when its offers are less likely to exist, however many sets that takes"


def _selftest_gain() -> None:
    """One gain of a sale: gains, Closer.gain and the net of an option agree; the plan with no close left takes the best proper cover by net."""
    club, side, _ = _star()
    study = offer_ratio_distribution([NormalDist(0.997, 0.057).inv_cdf((i + 0.5) / 100) for i in range(100)], 1.0)
    rng = random.Random(3)
    for rate in (0.0, hurdle.FALLBACK_LAMBDA, 0.2):
        judge = Closer(club, side, study, 3, rate)
        price = {k: rng.uniform(0.7, 1.3) * club.worth[k] for k in club.squad}
        got = gains(club, Money(rate, len(side.rounds)), price)
        assert all(abs(got[k] - judge.gain(k, price[k])) < 1e-12 for k in club.squad), "the Closer reads the gain of approach.gains"
        assert all(abs(got[k] - hurdle.in_points(price[k] - hurdle.draw(club.worth[k]), len(side.rounds), rate)) < 1e-9 for k in club.squad), "kappa x (price less the expected offer)"
        assert rate > 0 or not any(got.values()), "rate 0: sales gain nothing"
        assert all(abs(got[k]) < 1e-12 for k in club.squad if abs(price[k] - hurdle.draw(club.worth[k])) < 1e-9), "a sale at the expected offer gains nothing"
    rng = random.Random(14)
    seen = 0
    for _ in range(40):
        town = {"m%02d" % i: p for i, p in enumerate(["POR"] * 2 + ["DEF"] * 5 + ["MED"] * 5 + ["DEL"] * 3)}
        cells = _cells(rng, town, (1, 2, 3), [0, 0, 1, 3, 5])
        worths = {k: rng.choice([2e6, 4e6, 6e6]) for k in town}
        offers = {k: worths[k] * rng.choice([0.5, 0.9, 1.0, 1.3]) for k in rng.sample(sorted(town), rng.randint(3, 8))}
        club = Club(frozenset(town), town, worths, offers, rng.choice([-9e6, -5e6, -1e6]), 5e7)
        rounds = Rounds(cells, valuation.Window((1, 2, 3)), valuation.Window((1, 2, 3)))
        tside = Side(club.squad, town, cells, rounds.arrives)
        rate = rng.choice([0.0, hurdle.FALLBACK_LAMBDA, 0.3])
        found = plan(club, approach(club, None, 0.0, 1.0, rounds), rounds, 0, tside, rate=rate)
        assert found is not None
        if not found.feasible:
            continue
        seen += 1
        pay = gains(club, Money(rate, len(tside.rounds)), prices(club, False))
        need = -(club.cash + club.inflow.expected)
        pool = sorted(offers)
        nets = [(tside.cost(frozenset(b)) or 0.0) - sum(pay[k] for k in b) for m in range(1, len(pool) + 1) for b in combinations(pool, m)
                if tside.cost(frozenset(b)) is not None and sum(offers[k] for k in b) >= need - 1e-6 and all(sum(offers[j] for j in b if j != k) < need - 1e-6 for k in b)]
        assert nets and abs(found.cost - sum(pay[k] for k in found.sells) - min(nets)) < 1e-9, "with no close left the plan is the recourse: the best proper cover of the standing offers by points less gains"
        assert all(sum(offers[j] for j in found.sells if j != k) < need - 1e-6 for k in found.sells), "no sale of the plan is superfluous"
    assert seen >= 10, seen


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
        if need > 1e-6:
            bonus = {k: rng.choice([-1.0, 0.0, 0.5, 2.0]) for k in pool}
            ranked = covers(side, fixed, pool, price, need, bonus, 3, True)
            minimal = sorted(side.points - left - sum(bonus[k] for k in b) for m in range(len(rest) + 1) for b in combinations(rest, m)
                             if sum(price[k] for k in b) >= need - 1e-6 and all(sum(price[j] for j in b if j != k) < need - 1e-6 for k in b)
                             and (left := total(squad - fixed - frozenset(b), own, cells, window)) is not None)
            assert len(ranked) == min(3, len(minimal)) and all(abs(x.cost - sum(bonus[k] for k in x.sells) - y) < 1e-9 for x, y in zip(ranked, minimal)), \
                "covers are the best minimal covers by cost less the credit of their members, best first"
            assert all(sum(price[j] for j in x.sells if j != k) < need - 1e-6 for x in ranked for k in x.sells), "no member of a cover is superfluous"
    hands = random.Random(4)
    for _ in range(40):
        names = [chr(97 + i) for i in range(6)]
        groups = [frozenset(hands.sample(names, hands.randint(1, 3))) for _ in range(hands.randint(1, 4))]
        chance = hands.choice([0.0, 0.5, 0.9, 1.0])
        firsts = first_available(groups, chance)
        expect = [0.0] * len(groups)
        for has in range(1 << len(names)):
            here = frozenset(k for i, k in enumerate(names) if has >> i & 1)
            weight = chance ** len(here) * (1 - chance) ** (len(names) - len(here))
            winner = next((i for i, one in enumerate(groups) if one <= here), None)
            if winner is not None:
                expect[winner] += weight
        assert all(abs(x - y) < 1e-9 for x, y in zip(firsts, expect)) and sum(firsts) <= 1.0 + 1e-9, "the chance each set is the first whose players all have an offer"
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
        planned = plan(club, found, flat, 0, rate=0.0)
        assert planned is not None and found is not None
        if cash >= 0:
            assert planned == Plan(True, frozenset(), 0.0, 0.0, 0.0), "cash 0 or more: nothing to sell"
            assert plan(club, found, flat, 3, rate=hurdle.FALLBACK_LAMBDA) == planned
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
        later = plan(club, found, flat, 3, rate=hurdle.FALLBACK_LAMBDA)
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
        tfound = plan(tclub, approach(tclub, None, 0.0, 1.0, tflat), tflat, n, rate=hurdle.FALLBACK_LAMBDA)
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
            assert len(r.least) == n and all(a >= b - 1e-9 for a, b in zip(r.least, r.least[1:])), \
                "the reserve never rises as the lock nears"
            seen_wait += 1
        assert len(set(secured.options)) == len(secured.options)
        judge = Closer(tclub, tside, dist, n)
        everything = judge.outcome(tfound.sells & frozenset(toffers), tfound.sells - frozenset(toffers)) if len(tfound.sells - frozenset(toffers)) <= WAITERS else None
        if everything is not None:
            assert best.net <= everything.net + 1e-9, "the policy's net is no worse than taking the plan's offers now"
    assert seen_options >= 8 and seen_wait >= 3, (seen_options, seen_wait)
    free = {"m%02d" % i: p for i, p in enumerate(["POR"] * 2 + ["DEF"] * 5 + ["MED"] * 5 + ["DEL"] * 3)}
    fcells = {1: {k: (0.0 if i >= 13 else 3.0) for i, k in enumerate(sorted(free))}}
    fclub = Club(frozenset(free), free, {k: 4e6 for k in free}, {"m13": 4.2e6}, -4e6, 5e7)
    fflat = Rounds(fcells, valuation.Window((1,)), valuation.Window((1,)))
    fside = Side(fclub.squad, free, fcells, fflat.arrives)
    fjudge = Closer(fclub, fside, NO_OFFERS, 3, 0.0)
    taken, waited = fjudge.outcome(frozenset({"m13"}), frozenset()), fjudge.outcome(frozenset(), frozenset({"m13"}))
    assert taken is not None and waited is not None and waited.points <= taken.points + 1e-9, "with a fallback that costs nothing and money worth nothing, waiting is never worse than taking now"
    assert taken.gain == 0.0 and taken.net == taken.points, "at rate 0 money is worth nothing: the net is the points lost"
    assert waited.wait[0].least[0] >= waited.wait[0].least[-1], "a reserve for a player who is not needed is any offer"
    lumpy = Club(frozenset(free), free, {**{k: 4e6 for k in free}, "m00": 40e6}, {}, -5e6, 5e7)
    lside = Side(lumpy.squad, free, fcells, fflat.arrives)
    for rate in (0.0, 0.2):
        lump = Closer(lumpy, lside, NO_OFFERS, 3, rate).outcome(frozenset(), frozenset())
        assert lump is not None and lump.gain == 0.0 and lump.cash > 0, "a sale at the expected offer, even one far above the gap, gains nothing: the asset leaves with the cash"
    cheap = Closer(fclub, fside, NO_OFFERS, 3, 0.2).outcome(frozenset({"m13"}), frozenset())
    assert cheap is not None and cheap.gain > 0, "a standing offer above the expected one gains its excess"
    assert Closer(fclub, fside, NO_OFFERS, 3, 0.2).outcome(frozenset(), frozenset({"m13"})) is not None
    drawn = sold_at(4e6, (0.9, 1.0, 1.1), (4.4e6, 0.0))
    assert abs(sum(a.p for a in drawn) - 1.0) < 1e-12 and drawn[0] == Atom(1 / 3, 4.4e6) and sorted(a.money for a in drawn)[0] < 4.4e6, "the Atoms are a distribution"
    assert all(a.money >= 4.4e6 for a in sold_at(4e6, (0.9, 1.0, 1.1), (4.4e6,))), "the only close left takes offers at or above its least"
    own = {"m%02d" % i: p for i, p in enumerate(["POR"] * 2 + ["DEF"] * 5 + ["MED"] * 5 + ["DEL"] * 3)}
    cells = {1: {k: 1.0 + 0.37 * i for i, k in enumerate(sorted(own))}}
    worth_of = {k: 4e6 for k in own}
    tclub = Club(frozenset(own), own, worth_of, {}, -3e6, 5e7)
    tflat = Rounds(cells, valuation.Window((1,)), valuation.Window((1,)))
    cheapest = plan(tclub, approach(tclub, None, 0.0, 1.0, tflat), tflat, 3, rate=hurdle.FALLBACK_LAMBDA)
    assert cheapest is not None
    bench = secure(tclub, cheapest, Side(tclub.squad, own, cells, tflat.arrives), NO_OFFERS, 3)
    assert bench.feasible and bench.cost < 1e-6 and sum(worth_of[k] for k in bench.sells) >= 3e6, "players who cost no points are sold, not a starter"
    broke = Club(frozenset(own), own, worth_of, {}, -3e8, 5e7)
    lacking = plan(broke, approach(broke, None, 0.0, 1.0, tflat), tflat, 3, rate=hurdle.FALLBACK_LAMBDA)
    assert lacking is not None and not lacking.feasible and cheapest.feasible, "a plan exists exactly when what can be sold covers the deficit"
    star = {"m%02d" % i: p for i, p in enumerate(["POR"] * 2 + ["DEF"] * 6 + ["MED"] * 5 + ["DEL"] * 3)}
    season = tuple(range(1, 26))
    flat_cells = {j: {k: (0.0 if k in ("m14", "m15") else 2.0) for k in star} for j in season}
    flat_cells = {j: {**c, "m14": 0.04, "m15": 0.04} for j, c in flat_cells.items()}
    dear = {k: (32.47e6 if k == "m07" else 1.0e6) for k in star}
    club = Club(frozenset(star), star, dear, {"m07": 0.982 * dear["m07"]}, -31.07e6, 5e7)
    flat_rounds = Rounds(flat_cells, valuation.Window(season), valuation.Window(season))
    flat_side = Side(club.squad, star, flat_cells, flat_rounds.arrives)
    flat_found = plan(club, approach(club, None, 0.0, 1.0, flat_rounds), flat_rounds, 3, flat_side, rate=hurdle.FALLBACK_LAMBDA)
    assert flat_found is not None
    seen_offers = offer_ratio_distribution([NormalDist(0.997, 0.057).inv_cdf((i + 0.5) / 200) for i in range(200)])
    by_rate = {r: Closer(club, flat_side, seen_offers, 3, r) for r in (0.0, hurdle.FALLBACK_LAMBDA, 0.2)}
    leasts = {}
    for level, closer in by_rate.items():
        now_option, wait_option = closer.outcome(frozenset({"m07"}), frozenset()), closer.outcome(frozenset(), frozenset({"m07"}))
        assert now_option is not None and wait_option is not None
        leasts[level] = wait_option.wait[0].least
        assert abs((wait_option.net - now_option.net) - ((wait_option.points - now_option.points) - (wait_option.gain - now_option.gain))) < 1e-9
        assert wait_option.wait[0].least[-1] <= wait_option.wait[0].least[0], "the last close's reserve is the lowest"
    assert all(a <= b + 1.0 for a, b in zip(leasts[0.0], leasts[hurdle.FALLBACK_LAMBDA])) and all(a <= b + 1.0 for a, b in zip(leasts[hurdle.FALLBACK_LAMBDA], leasts[0.2])), \
        "the dearer the money, the higher the reserve: waiting is for the money above the deficit, at the one exchange rate"
    assert leasts[hurdle.FALLBACK_LAMBDA][0] > 0.982 * dear["m07"], "the standing offer, under the mean draw, is not enough to stop waiting"
    wait_now, wait_later = (by_rate[hurdle.FALLBACK_LAMBDA].outcome(frozenset({"m07"}), frozenset()),
                            by_rate[hurdle.FALLBACK_LAMBDA].outcome(frozenset(), frozenset({"m07"})))
    assert wait_now is not None and wait_later is not None and wait_later.net < wait_now.net, "waiting for a better offer beats taking the standing one when its mean is better"
    assert wait_later.gain > wait_now.gain, "the price above the expected offer is what the wait gains"
    recommended = secure(club, flat_found, flat_side, seen_offers, 3).options[0]
    assert "m07" not in recommended.now and "m07" in {r.player for r in recommended.wait}, "secure advises waiting for the better offer, with a reserve per close"
    only = Club(frozenset(star), star, {k: (32.47e6 if k == "m07" else 0.0) for k in star}, {"m07": 0.982 * 32.47e6}, -31.07e6, 5e7)
    only_found = plan(only, approach(only, None, 0.0, 1.0, flat_rounds), flat_rounds, 3, flat_side, rate=hurdle.FALLBACK_LAMBDA)
    assert only_found is not None and only_found.feasible
    alone = secure(only, only_found, flat_side, seen_offers, 3).options[0]
    assert (alone.now | {r.player for r in alone.wait}) == frozenset({"m07"}) and all(r.least[-1] <= only.worth[r.player] for r in alone.wait), \
        "when one sale alone covers the deficit it is still sold, and the last close declines only an offer that leaves the gap uncovered at a loss"
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
        first = plan(crowd, approach(crowd, None, 0.0, 1.0, span), span, closes, wing, rate=hurdle.FALLBACK_LAMBDA)
        assert first is not None
        if not first.feasible or not first.sells:
            continue
        seen = rng.choice([NO_OFFERS, offer_ratio_distribution([0.85, 0.9, 0.95, 1.0, 1.0, 1.05, 1.1, 1.2])])
        rate = rng.choice([0.0, hurdle.FALLBACK_LAMBDA])
        chosen = secure(crowd, first, wing, seen, closes, rate)
        if not chosen.options:
            continue
        judge = Closer(crowd, wing, seen, closes, rate)
        floor = chosen.options[0].net
        owned = frozenset(quotes)
        for size in range(1, 5):
            for members in combinations(sorted(big), size):
                group = frozenset(members)
                if (c := wing.cost(group)) is None or 0.7 * c - 3.0 >= floor or len(group - owned) > WAITERS:
                    continue
                if rate > 0 and any(sum(judge.pricing[j].now for j in group - {k}) >= lack for k in group):
                    continue
                offered = sorted(group & owned)
                for m in range(min(len(offered), WAITERS - len(group - owned)) + 1):
                    for held in combinations(offered, m):
                        other = judge.outcome(frozenset(offered) - frozenset(held), (group - owned) | frozenset(held))
                        assert other is None or other.net >= floor - 0.25, "no split of up to four players has a net smaller by more than a quarter point (the search bound ignores that a waiter may not be sold)"
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
        assert abs(net.net - (gross.net + c0 - (1 - win) * max(0.0, c0 - net.cost_lost))) < 1e-9 and (net.gain, net.cost_won, net.cost_lost) == (gross.gain, gross.cost_won, gross.cost_lost), \
            "the net is measured against the plan with no buy: the same plan, the gross net plus its cost (a lost bid never costs less than it)"
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
        planned = plan(club._replace(inflow=credit), got, flat, 0, rate=hurdle.FALLBACK_LAMBDA)
        before = plan(club, none, flat, 0, rate=hurdle.FALLBACK_LAMBDA)
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
    _selftest_money()
    _selftest_after_result()
    _selftest_bidding()
    _selftest_offer_model()
    _selftest_gain()
    print("approach self-test OK")


if __name__ == "__main__" and "--selftest" in sys.argv:
    _selftest()
