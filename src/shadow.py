"""The shadow price of cash at the lock (#156), in shadow mode: computed and reported, acted on by nothing.

Every move that touches cash at the lock is a `Move` on one axis: its `net` (points, the funded net of approach.approach, or the
points a sale of one player costs or saves, both against the plan with no buy) and its `need` (euros of lock cash it takes, a bid priced
at its win chance times its amount, less what its sales raise; negative when it raises cash). `solve` is the one place that prices cash:
the multiplier s of the constraint "the moves' needs total at most the capacity" (the base plan's cash at the lock), the slope between
the two moves-sets of the Lagrangian that straddle the capacity, i.e. the density (net per euro of need) of the break move. s = 0 when the
set that is best with cash free fits. Moves of one group (the rungs of a bid ladder) are alternatives; moves that claim the same player
(a sale, a kept sale, the place of the eleven a buy takes) do not combine; the squad slots are a hard limit inside every search. The funded
set is the exact optimum within the capacity (branch and bound), not a prefix by density. Moves are additive: the points of two buys that
fill one place of the eleven are not summed, because they claim it together.

Everything else reads `Shadow`; nothing here changes a bid, a ranking, a walk-away or an advice."""
from __future__ import annotations

import random
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from functools import partial
from itertools import product
from typing import Any, NamedTuple

import approach
import hurdle
import valuation
from approach import Approach, Bidding, Club, Money, Offer, Plan, Side

__all__ = ["Move", "Limits", "Shadow", "Ladder", "Base", "solve", "base_of", "buy_rungs", "toggles", "report", "walk_away_at", "BINDING", "DISPLACED"]

EPS = 1e-9
STEPS = 200
NODES = 100_000
NEAR = 5
BINDING = "cash_binding"
DISPLACED = "displaced"
PER_M = 1e6


class Move(NamedTuple):
    """One alternative on the axis of lock cash: `net` points against the plan with no buy, `need` euros of lock cash it takes (negative: it
    raises cash), `slots` the squad places it uses, `claims` what it cannot share with another move, `group` the alternatives it is one of (at most
    one move of a group is made), `amount` the bid of a buy, `worst` the need if every bid is won (None: `need`)."""
    key: str
    kind: str
    group: str
    net: float
    need: float
    slots: int = 0
    claims: frozenset[str] = frozenset()
    amount: float | None = None
    worst: float | None = None

    @property
    def hardest(self) -> float:
        """The lock cash it takes when every bid is won (its `worst` need; `need` for a move that has no bid)."""
        return self.need if self.worst is None else self.worst


class Limits(NamedTuple):
    """What the moves draw on: `cash` the lock cash the base plan leaves (euros, 0 or more) and `slots` the squad places it leaves."""
    cash: float
    slots: int


class Shadow(NamedTuple):
    """The price of cash at the lock. `s` points per euro (0 when cash does not bind), `high` the density of the least dense move funded above
    it (None when none), `funded` the exact optimum within the limits, `lean` the best set at s that takes more than the capacity (empty when
    s = 0) and `rich` the one within it: the break is what `lean` has and `rich` has not. `free` the best set with cash free, `limits` what it was
    solved over, `exact` whether every search ran to its end (else the numbers are the best found)."""
    s: float
    high: float | None
    funded: tuple[Move, ...]
    lean: tuple[Move, ...]
    rich: tuple[Move, ...]
    free: tuple[Move, ...]
    limits: Limits
    exact: bool = True

    @property
    def binding(self) -> bool:
        return self.s > 0.0

    @property
    def break_item(self) -> tuple[Move, ...]:
        """The moves the capacity refuses: those of `lean` that `rich` does not make."""
        return tuple(m for m in self.lean if m not in self.rich)

    @property
    def to_zero(self) -> float:
        """The euros of cash that would stop mattering: what the set that is best with cash free takes beyond the capacity (0 when it fits)."""
        return max(0.0, used(self.free) - self.limits.cash)


def weight(m: Move, s: float) -> float:
    """A move's net at the price s of cash: net less s x need (what every reader of the one valuation reads)."""
    return m.net - s * m.need


def net_of(m: Move) -> float:
    return m.net


def used(moves: Iterable[Move]) -> float:
    return sum(m.need for m in moves)


def total(moves: Iterable[Move]) -> float:
    return sum(m.net for m in moves)


def clash(a: Move, b: Move) -> bool:
    """Whether two moves cannot both be made: one group, or a claim in common."""
    return a.group == b.group or bool(a.claims & b.claims)


def feasible(moves: Sequence[Move], limits: Limits | None, slots: int) -> bool:
    """Whether `moves` combine: no two clash, the slots fit, and (with limits) the cash."""
    return (all(not clash(a, b) for i, a in enumerate(moves) for b in moves[i + 1:]) and sum(m.slots for m in moves) <= slots
            and (limits is None or used(moves) <= limits.cash + EPS))


class Frame(NamedTuple):
    """A step of the branch and bound: groups from index `i` are still to decide, `picked` made, worth `value`, taking `cash` and `slots`, with
    `claimed` taken."""
    i: int
    value: float
    picked: tuple[Move, ...]
    claimed: frozenset[str]
    cash: float
    slots: int


class Found(NamedTuple):
    """What `search` found: the `value` of the `picked` moves and whether the search ran to its end (`exact`) or stopped at NODES."""
    value: float
    picked: tuple[Move, ...]
    exact: bool = True


def search(groups: Sequence[Sequence[Move]], score: Callable[[Move], float], s: float, limits: Limits | None, slots: int,
           incumbent: tuple[Move, ...] = ()) -> Found:
    """The moves, at most one per group, that maximise the total of `score` and combine (`feasible`; with `limits` None there is no cash limit,
    and a move of no score is made only to free a squad place): branch and bound, groups in order of their best score, the bound the value so far
    plus the best of each group left (with limits, plus s x the cash left: weak duality, valid at any s >= 0). `incumbent` (feasible, scored by
    net) starts the bound. Deterministic: ties go to the earlier move. Stops after NODES steps with the best found so far (`exact` False)."""
    capped = limits is not None
    rate = s if capped else 0.0
    cash = limits.cash if limits is not None else 0.0
    ordered = sorted((sorted(g, key=lambda m: (-score(m), m.need, m.key, m.amount or 0.0)) for g in groups if g),
                     key=lambda g: (-(score(g[0]) - rate * g[0].need), g[0].group))
    reach = [sum(max(0.0, max(score(m) - rate * m.need for m in g)) for g in ordered[i:]) for i in range(len(ordered) + 1)]
    relief = [sum(min(0.0, min(m.need for m in g)) for g in ordered[i:]) for i in range(len(ordered) + 1)]
    freed = [sum(min(0, min(m.slots for m in g)) for g in ordered[i:]) for i in range(len(ordered) + 1)]
    best = Found(sum(score(m) for m in incumbent), incumbent) if incumbent else Found(0.0, ()) if slots >= 0 else Found(float("-inf"), ())
    stack = [Frame(0, 0.0, (), frozenset(), 0.0, 0)]
    steps = 0
    while stack:
        steps += 1
        if steps > NODES:
            return best._replace(exact=False)
        f = stack.pop()
        if f.slots <= slots and (not capped or f.cash <= cash + EPS) and f.value > best.value + EPS:
            best = Found(f.value, f.picked)
        if f.i == len(ordered) or f.slots + freed[f.i] > slots or capped and f.cash + relief[f.i] > cash + EPS:
            continue
        if f.value + reach[f.i] + (rate * (cash - f.cash) if capped else 0.0) <= best.value + EPS:
            continue
        stack.append(Frame(f.i + 1, f.value, f.picked, f.claimed, f.cash, f.slots))
        for m in reversed(ordered[f.i]):
            if (not capped and score(m) <= EPS and m.slots >= 0) or f.claimed & m.claims:
                continue
            stack.append(Frame(f.i + 1, f.value + score(m), f.picked + (m,), f.claimed | m.claims, f.cash + m.need, f.slots + m.slots))
    return best


def solve(groups: Iterable[Sequence[Move]], limits: Limits) -> Shadow:
    """The Shadow of the moves over `limits` (cash 0 or more): s = 0 when the best set with cash free takes no more than the capacity, else the
    slope between the sets, best at one s, that take more and less than it (the Lagrangian cutting plane: each step adds a new set, so it ends),
    and the funded set the exact optimum within the limits. `exact` is False when a search stopped at NODES or the cutting plane at STEPS: the
    result is then the best found, not proven."""
    assert limits.cash >= 0.0, "a base plan that leaves cash below 0 has no shadow price"
    pool = [list(g) for g in groups if g]
    slots = limits.slots
    free = search(pool, net_of, 0.0, None, slots)
    if used(free.picked) <= limits.cash + EPS:
        funded = search(pool, net_of, 0.0, limits, slots, free.picked)
        return Shadow(0.0, None, funded.picked, (), free.picked, free.picked, limits, free.exact and funded.exact)
    top = 1.0 + max((m.net / m.need for g in pool for m in g if m.need > EPS and m.net > 0), default=0.0)
    lean, s = free.picked, top
    top_found = search(pool, partial(weight, s=top), top, None, slots)
    rich, exact = top_found.picked, free.exact and top_found.exact
    converged = False
    for _ in range(STEPS):
        s = (total(lean) - total(rich)) / (used(lean) - used(rich))
        got = search(pool, partial(weight, s=s), s, None, slots)
        exact = exact and got.exact
        if total(got.picked) - s * used(got.picked) <= total(lean) - s * used(lean) + EPS * (1.0 + abs(total(lean))):
            converged = True
            break
        lean, rich = (got.picked, rich) if used(got.picked) > limits.cash + EPS else (lean, got.picked)
    funded = search(pool, net_of, s, limits, slots, rich)
    above = [m.net / m.need for m in rich if m.need > EPS and m.net > 0 and weight(m, s) > EPS]
    return Shadow(s, min(above, default=None), funded.picked, lean, rich, free.picked, limits, exact and converged and funded.exact)


class Ladder(NamedTuple):
    """A listing the board looked at: the player `key`, his `name`, the player of the eleven he `replaces`, the `reason` the board gives for
    leaving him out (None when it funds him) and the `bidding` of its bids (their Offers carry the funded Approach of each)."""
    key: str
    name: str
    replaces: str | None
    reason: str | None
    bidding: Bidding


class Base(NamedTuple):
    """The plan with no buy that every move is measured against: its `sells`, the `cash` it leaves at the lock (expected, euros) and the
    squad `slots` it leaves."""
    sells: frozenset[str]
    cash: float
    slots: int


def base_of(club: Club, base: Approach | None, found: Plan | None) -> Base | None:
    """The Base of a club: the secured cover's sales and expected cash when cash is below 0 and it has options (as board.base_doc), else the
    approach with no buy's. None when there is no such plan or it leaves cash below 0 at the lock."""
    if base is None or not base.feasible or found is None or not found.feasible:
        return None
    cash = found.options[0].cash if club.cash < 0 and found.options else base.cash_if_lost
    return Base(found.sells, cash, club.squad_max - len(club.squad - found.sells)) if cash >= 0 else None


def need_of(offer: Offer, base: Base) -> float:
    """The lock cash a bid takes beyond the base plan, euros: the bid priced at its win chance (the cash after it, if won and if lost, weighted by
    the chance) less what the base leaves. Negative when its sales raise more than it costs."""
    a = offer.found
    return base.cash - (offer.win * a.lock_margin + (1 - offer.win) * a.cash_if_lost)


def buy_rungs(ladder: Ladder, base: Base) -> list[Move]:
    """The moves of a listing: one per bid of its ladder that is funded, alternatives of one group. A buy claims the players it sells that the base
    does not, the base's sellers it keeps, and the place of the eleven it takes; it uses a squad place less those its sales free, against the base."""
    out = []
    for o in ladder.bidding.offers:
        a = o.found
        if not a.feasible:
            continue
        sold = a.tonight | a.if_won | a.if_lost
        claims = {"sold:" + k for k in sold - base.sells} | {"kept:" + k for k in base.sells - sold} | ({"place:" + ladder.replaces} if ladder.replaces else set())
        out.append(Move(ladder.key, "buy", "buy:" + ladder.key, a.net, need_of(o, base), 1 - len(a.tonight | a.if_won) + len(base.sells), frozenset(claims), o.amount,
                        base.cash - a.lock_margin))
    return out


def toggles(club: Club, side: Side, base: Base, money: Money) -> list[list[Move]]:
    """The moves that flip one player of the squad against the base plan: sell one the base keeps (net: less the points the eleven loses, plus the
    money value of his price over his expected offer; raises his price) or keep one it sells (the reverse). Each is its own group and claims the
    player; a flip that leaves no legal eleven is not a move."""
    price = approach.prices(club, False)
    gain = approach.gains(club, money, price)
    here = side.cost(base.sells)
    out: list[list[Move]] = []
    for k in sorted(club.squad):
        if k not in club.worth or here is None:
            continue
        keep = k in base.sells
        there = side.cost(base.sells - {k} if keep else base.sells | {k})
        if there is None:
            continue
        out.append([Move(k, "keep" if keep else "sale", ("keep:" if keep else "sale:") + k,
                         here - there + (-gain[k] if keep else gain[k]),
                         price[k] if keep else -price[k], 1 if keep else -1, frozenset({("kept:" if keep else "sold:") + k, "place:" + k}))])
    return out


def walk_away_at(offers: Sequence[Offer], base: Base, s: float, kappa: float) -> float | None:
    """The price at which a listing's net, charged s per euro of lock cash (net less s x need, per bid), is 0: approach.zero_price of the shifted
    nets (a bid's cash moves with its price at its win chance, so past the ladder the net falls by (kappa + s) x win per euro). Nonincreasing in s
    for bids that take cash; s = 0 is the walk-away of approach.bidding."""
    return approach.zero_price([o._replace(found=o.found._replace(net=o.found.net - s * need_of(o, base))) for o in offers], kappa + s)


def move_row(m: Move, s: float, names: Mapping[str, str], funded: bool, reason: str | None = None) -> dict[str, Any]:
    """A move as the board shows it: net (points), need (M of lock cash), `per_million` its points per M of need (None with none), the bid of a buy,
    its net at the price s, whether it is funded and the reason it is not."""
    return {"key": m.key, "name": names.get(m.key, m.key), "kind": m.kind, "net": m.net, "need": m.need / PER_M,
            "per_million": m.net * PER_M / m.need if m.need else None, "net_at_s": weight(m, s), "bid": m.amount,
            "funded": funded, "reason": reason}


def refusal(group: Sequence[Move], found: Shadow) -> str:
    """Why a listing with a funded bid that nets above 0 is not funded: `displaced` when the price of cash is 0, a funded move claims what it does or the
    squad places are full, else cash_binding."""
    full = sum(f.slots for f in found.funded) + min(m.slots for m in group) > found.limits.slots
    return DISPLACED if not found.binding or full or any(clash(m, f) for m in group for f in found.funded) else BINDING


def report(club: Club, side: Side, base: Base | None, money: Money, ladders: Sequence[Ladder], names: Mapping[str, str]) -> dict[str, Any]:
    """The shadow document of a cut (no key of the board reads it): s in points per M of lock cash with its bracket, the capacity and what is used,
    the cash at the lock if every funded bid is won, the break, the funded moves, every listing looked at (`candidates`: funded, or with the
    reason it is not and its density; none is dropped) and the sales and keeps nearest the price. `s` is None when there is no base plan to
    measure against (`why`)."""
    if base is None:
        return {"s": None, "why": "no plan with no buy that leaves cash at the lock at 0 or more", "candidates": [], "funded": []}
    rungs = {x.key: buy_rungs(x, base) for x in ladders}
    sales = toggles(club, side, base, money)
    found = solve([r for r in rungs.values() if r] + sales, Limits(base.cash, base.slots))
    s, taken = found.s, set(found.funded)
    rows = []
    for x in ladders:
        group = rungs[x.key]
        chosen = next((m for m in group if m in taken), None)
        positive = any(m.net > EPS for m in group)
        shown = chosen or max(group, key=lambda m: (weight(m, s), -m.need), default=None)
        reason = None if chosen else refusal(group, found) if positive else x.reason or "net_not_positive"
        row = {"key": x.key, "name": x.name, "rungs": len(group), "walk_away": x.bidding.walk_away,
               "walk_away_at_s": walk_away_at(x.bidding.offers, base, s, money.kappa) if group else None}
        rows.append({**(move_row(shown, s, names, chosen is not None, reason) if shown else {"key": x.key, "name": x.name, "funded": False, "reason": x.reason or "net_not_positive"}), **row})
    near = [m for g in sales for m in g if m not in taken]
    near = sorted(near, key=lambda m: (-weight(m, s), m.key))[:NEAR]
    moves = [move_row(m, s, names, True) for m in found.funded if m.kind != "buy"] + [move_row(m, s, names, False, refusal([m], found) if m.net > EPS else "costs_points") for m in near if weight(m, s) > -EPS]
    dense = found.high
    spent = used(found.funded)
    return {"s": s * PER_M, "per_jornada": s * PER_M / money.n if money.n else None, "bracket": [s * PER_M, None if dense is None else dense * PER_M], "binding": found.binding,
            "capacity": base.cash / PER_M, "used": spent / PER_M, "to_zero": found.to_zero / PER_M, "slots": base.slots,
            "cash_all_won": (base.cash - sum(m.hardest for m in found.funded)) / PER_M, "all_won_short": base.cash < sum(m.hardest for m in found.funded),
            "exact": found.exact,
            "break": [names.get(m.key, m.key) + (" (%s)" % m.kind) for m in found.break_item],
            "funded": [move_row(m, s, names, True) for m in found.funded], "candidates": rows, "sales": moves,
            "base_sells": [names.get(k, k) for k in sorted(base.sells)], "lambda": money.rate,
            "line": line_of(found, names)}



def line_of(found: Shadow, names: Mapping[str, str]) -> str:
    """The shadow in one line: the price of cash and its bracket, what the capacity is and what stops mattering."""
    cap = found.limits.cash / PER_M
    if not found.binding:
        return "cash at the lock does not bind: every move that adds points fits in the %.1fM it leaves" % cap
    low, high = found.s * PER_M, found.high
    broke = ", ".join(sorted({names.get(m.key, m.key) for m in found.break_item}))
    return "cash at the lock is worth %.3f points per M (up to %s): %.1fM of capacity is used; %s is refused; %.1fM more would stop it mattering" % (
        low, "%.3f" % (high * PER_M) if high is not None else "no more", cap, broke or "nothing", found.to_zero / PER_M)


def _instance(rng: random.Random, conflicts: bool) -> tuple[list[list[Move]], Limits]:
    groups = []
    for g in range(rng.randint(2, 6)):
        rungs = []
        for r in range(rng.randint(1, 3)):
            claims = frozenset(rng.sample("abcd", rng.randint(0, 2))) if conflicts else frozenset()
            rungs.append(Move("p%d" % g, rng.choice(["buy", "sale"]), "g%d" % g, rng.uniform(-3, 10), rng.uniform(-4, 10) * 1e6,
                              rng.choice([-1, 0, 1]) if conflicts else 0, claims, 1e6 * (r + 1)))
        groups.append(rungs)
    return groups, Limits(rng.uniform(0, 12) * 1e6, rng.randint(0, 3) if conflicts else 9)


def _brute(groups: Sequence[Sequence[Move]], limits: Limits | None, slots: int, score: Callable[[Move], float]) -> float:
    best = float("-inf")
    for choice in product(*[[None, *g] for g in groups]):
        made = [m for m in choice if m is not None]
        if feasible(made, limits, slots):
            best = max(best, sum(score(m) for m in made))
    return best


def _selftest_solver() -> None:
    rng = random.Random(156)
    binding = free = 0
    for case in range(400):
        conflicts = case % 2 == 0
        groups, limits = _instance(rng, conflicts)
        found = solve(groups, limits)
        want = _brute(groups, limits, limits.slots, lambda m: m.net)
        assert abs(total(found.funded) - want) < 1e-6, "the funded set is the optimum of every feasible set"
        assert feasible(list(found.funded), limits, limits.slots), "joint feasibility: no clash, the slots, the cash"
        assert found.s >= 0.0
        wants_more = used(found.free) > limits.cash + EPS
        assert (found.s > 0.0) == wants_more, "s is 0 exactly when the best set with cash free fits"
        if not found.binding:
            free += 1
            assert not found.lean and abs(total(found.funded) - _brute(groups, None, limits.slots, lambda m: m.net)) < 1e-6, "s = 0: the optimum with cash free, so today's"
            continue
        binding += 1
        assert used(found.rich) <= limits.cash + EPS < used(found.lean), "the break straddles the capacity"

        def dual(s: float, groups: list[list[Move]] = groups, limits: Limits = limits) -> float:
            return s * limits.cash + _brute(groups, None, limits.slots, lambda m: weight(m, s))
        here = dual(found.s)
        assert here >= want - 1e-6, "weak duality: the bound is never under the optimum"
        assert all(dual(found.s * x) >= here - 1e-6 for x in (0.0, 0.5, 0.9, 1.1, 2.0)), "s is the minimum of the dual: no other price bounds tighter"
        if not conflicts:
            assert here - want <= max(m.net for g in groups for m in g) + 1e-6, "without clashes the bound is within one move of the optimum"
            refused = [m for g in groups for m in g if m.net > EPS and m.need > EPS and not any(m.group == f.group for f in found.funded)]
            assert all(limits.cash - used(found.funded) < m.need + EPS for m in refused), "complementary slackness: what is left is under any refused move's need"
        for c in (0.5, 3.0):
            scaled = solve([[m._replace(need=m.need * c) for m in g] for g in groups], limits._replace(cash=limits.cash * c))
            assert {(m.key, m.amount) for m in scaled.funded} == {(m.key, m.amount) for m in found.funded} and abs(scaled.s * c - found.s) < 1e-9 * (1 + found.s), \
                "homogeneity: money scaled, the same funding, s in points per unit of money"
            worth = solve([[m._replace(net=m.net * c) for m in g] for g in groups], limits)
            assert {(m.key, m.amount) for m in worth.funded} == {(m.key, m.amount) for m in found.funded} and abs(worth.s - found.s * c) < 1e-9 * (1 + c * found.s), \
                "homogeneity: points scaled, s scaled"
        mixed = [rng.sample(g, len(g)) for g in rng.sample(groups, len(groups))]
        again = solve(mixed, limits)
        assert {(m.key, m.amount) for m in again.funded} == {(m.key, m.amount) for m in found.funded} and abs(again.s - found.s) < 1e-12 * (1 + found.s), \
            "the order of the list does not matter"
    assert binding >= 100 and free >= 50, (binding, free)
    for case in range(120):
        groups, limits = _instance(rng, False)
        caps = sorted(rng.uniform(0, 14) * 1e6 for _ in range(6))
        prices = [solve(groups, limits._replace(cash=c)).s for c in caps]
        assert all(a >= b - 1e-12 for a, b in zip(prices, prices[1:])), "s is nonincreasing in the cash at the lock"
        base = solve(groups, limits)
        extra = Move("new", "buy", "gnew", rng.uniform(0, 10), rng.uniform(0.5, 8) * 1e6, 0, frozenset(), 1e6)
        assert solve([*groups, [extra]], limits).s >= base.s - 1e-12, "a candidate that needs cash never lowers s"
        pick = [(i, 0) for i, g in enumerate(groups) if len(g) == 1 and g[0].need > EPS]
        if pick:
            i, j = rng.choice(pick)
            better = [list(g) for g in groups]
            better[i][j] = better[i][j]._replace(net=better[i][j].net + rng.uniform(0, 5))
            assert solve(better, limits).s >= base.s - 1e-12, "improving a move that needs cash (one with no alternative) never lowers s"
        dead = Move("dead", "buy", "gdead", 0.0, 1e6, 0, frozenset(), 1e6)
        dead = dead._replace(net=base.s * dead.need * rng.uniform(0.0, 1.0))
        withdead = solve([*groups, [dead]], limits)
        assert abs(withdead.s - base.s) < 1e-12 * (1 + base.s) and total(withdead.funded) >= total(base.funded) - 1e-9, \
            "a candidate that nets under s x its need leaves s as it was and the optimum no worse"
    divisible = [[Move("d%02d" % i, "buy", "d%02d" % i, rng.uniform(0.2, 2.0) * 1e-1, 0.1e6, 0, frozenset(), 1.0)] for i in range(60)]
    room = Limits(2.05e6, 99)
    got = solve(divisible, room)
    order = sorted((g[0] for g in divisible), key=lambda m: (-m.net / m.need, m.key))
    assert set(m.key for m in got.funded) == {m.key for m in order[:20]}, "divisible moves: the funded set is the prefix by density"
    assert order[20].net / order[20].need - 1e-12 <= got.s <= order[19].net / order[19].need + 1e-12, "and s is the density at the cut"
    dead = Move("dead", "buy", "gdead", got.s * 0.1e6 * 0.9, 0.1e6, 0, frozenset(), 1.0)
    assert {m.key for m in solve([*divisible, [dead]], room).funded} == {m.key for m in got.funded}, "divisible: a candidate that nets under s x its need is never funded"


def _club(rng: random.Random, cash: float, offered: int = 4) -> tuple[Club, approach.Rounds, Side]:
    own = {"m%02d" % i: p for i, p in enumerate(["POR"] + ["DEF"] * 5 + ["MED"] * 5 + ["DEL"] * 3)}
    pos = own | {"a": "DEL", "b": "MED", "c": "DEF"}
    jornadas = (1, 2, 3)
    cells = {j: {k: rng.choice([0.0, 1.0, 2.0, 3.0, 5.0]) * 2 for k in own} | {"a": 18.0, "b": 14.0, "c": 12.0} for j in jornadas}
    window = valuation.Window(jornadas)
    rounds = approach.Rounds(cells, window, window)
    worth = {k: rng.choice([2e6, 4e6, 6e6]) for k in own}
    offers = {k: worth[k] * rng.choice([0.95, 1.0, 1.05]) for k in rng.sample(sorted(own), offered)}
    club = Club(frozenset(own), pos, worth, offers, cash, 5e6 * len(own))
    return club, rounds, Side(club.squad, pos, cells, rounds.arrives)


def _ladders(club: Club, rounds: approach.Rounds, base: Approach, money: Money, rng: random.Random) -> list[Ladder]:
    sides: dict[tuple[frozenset[str], tuple[int, ...]], Side] = {}
    out = []
    for k, ask in (("a", 8e6), ("b", 6e6), ("c", 5e6)):
        curve = [(1.0, 0.4), (1.1, 0.7), (1.3, 0.95)]

        def fund(amount: float, win: float, k: str = k) -> Approach | None:
            return approach.approach(club, k, amount, win, rounds, -base.net, sides, 0.0, money._replace(resale=hurdle.draw(ask)))
        found = approach.bidding(curve, ask, fund, money.kappa)
        reason = None if found.best is not None and found.best.found.net > 0.5 else "net_not_positive" if found.best is not None else "unfundable"
        out.append(Ladder(k, "name " + k, rng.choice(["m06", "m11", None]), reason, found))
    return out


def _selftest_report() -> None:
    rng = random.Random(7)
    rate = 0.045
    seen = {"binding": 0, "free": 0, "funded": 0}
    for case in range(40):
        cash = rng.choice([-9e6, -3e6, 0.0, 2e6, 4e7])
        club, rounds, side = _club(rng, cash)
        money = Money(rate, len(rounds.arrives))
        base = approach.approach(club, None, 0.0, 1.0, rounds, money=money)
        found = approach.plan(club, base, rounds, 1, side, rate=rate)
        floor = base_of(club, base, found)
        ladders = _ladders(club, rounds, base, money, rng) if base is not None else []
        names = {k: k for k in club.squad | {"a", "b", "c"}}
        doc = report(club, side, floor, money, ladders, names)
        if floor is None:
            assert doc["s"] is None and doc["why"]
            continue
        assert [c["key"] for c in doc["candidates"]] == [x.key for x in ladders], "no silent drop: every listing looked at is a row, in order"
        for c in doc["candidates"]:
            assert (c["reason"] is None) == c["funded"], "a candidate is funded or has a reason"
            if not c["funded"]:
                assert c["reason"] in ("net_not_positive", "unfundable", BINDING, DISPLACED), c
                assert c["reason"] != BINDING or doc["binding"], "cash_binding says the price of cash is above 0"
        assert doc["used"] <= doc["capacity"] + 1e-6, "the funded moves fit in the cash the base leaves"
        funded = [c for c in doc["candidates"] if c["funded"]]
        seen["funded"] += len(funded)
        if doc["binding"]:
            seen["binding"] += 1
        else:
            seen["free"] += 1
            assert doc["s"] == 0.0 and doc["to_zero"] == 0.0
            for x in ladders:
                if x.bidding.offers:
                    assert walk_away_at(x.bidding.offers, floor, 0.0, money.kappa) == x.bidding.walk_away, "s = 0: the walk-away of the board"
        for x in ladders:
            if x.bidding.offers and all(need_of(o, floor) >= 0 for o in x.bidding.offers):
                walks = [walk_away_at(x.bidding.offers, floor, s * 1e-7, money.kappa) or 0.0 for s in (0.0, 0.5, 1.0, 2.0, 5.0)]
                assert all(a >= b - 1e-6 for a, b in zip(walks, walks[1:])), "a bid that takes cash walks away no higher at a higher price of cash"
        joint = solve([*(r for r in (buy_rungs(x, floor) for x in ladders) if r), *toggles(club, side, floor, money)], Limits(floor.cash, floor.slots))
        assert feasible(list(joint.funded), joint.limits, joint.limits.slots), "joint feasibility: no player sold twice, the places, the cash"
        assert joint.exact
    assert seen["binding"] >= 3 and seen["free"] >= 3 and seen["funded"] >= 3, seen
    club, rounds, side = _club(random.Random(3), -6e6)
    money = Money(rate, len(rounds.arrives))
    base = approach.approach(club, None, 0.0, 1.0, rounds, money=money)
    floor = base_of(club, base, approach.plan(club, base, rounds, 1, side, rate=rate))
    assert floor is not None
    alone = report(club, side, floor, money, [], {k: k for k in club.squad})
    assert alone["funded"] == [] and alone["candidates"] == [], "no listing: no buy"
    assert alone["binding"] and all(m["kind"] in ("keep", "sale") for m in alone["sales"]) and alone["s"] > 0, \
        "no buy and cash below 0: the price of cash is the density of the cover's marginal sale"
    assert any(abs(m["per_million"] * 1.0 - alone["s"]) < 1e-9 * (1 + alone["s"]) for m in alone["sales"] if m["per_million"] is not None), \
        "and s equals that density"
    sale = Move("p", "sale", "sale:p", -1.0, -2e6, -1, frozenset({"sold:p", "place:p"}))
    buy = Move("q", "buy", "buy:q", 5.0, 1e6, 1, frozenset({"place:p"}))
    assert clash(sale, buy), "a sale of the player a buy replaces does not combine with it"
    crowded = [[Move("g%d" % i, "buy", "g%d" % i, 1.0 + i % 7, 1e6 * (1 + i % 5), 1, frozenset({"c%d" % (i % 9)}), float(r)) for r in range(10)] for i in range(40)]
    big = solve(crowded, Limits(20e6, 5))
    assert not big.exact and feasible(list(big.funded), big.limits, 5), "a search that cannot finish says so and still returns a feasible set"


def _selftest() -> None:
    _selftest_solver()
    _selftest_report()
    print("shadow self-test OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        raise SystemExit(0)
