from __future__ import annotations

import math
import sys
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from functools import cached_property
from typing import NamedTuple

from ffcore.action import Action
from ffcore.forecast import Bootstrap, expected_points
from ffcore.gain import Standings, cell_variance, moments, normal_above, standings
from ffcore import pricing
from ffcore.market import Market
from ffcore.outlook import Outlook
from ffcore.rules import MAX_SLOT, POSITIONS, SHAPES, Squad, Squads, position, shortfall
from ffcore.schedule import PerJornadaFactors, phantom_topup
from ffcore.season import LeagueState

__all__ = ["Action", "Board", "FUNNEL", "Move", "Universe", "board", "joint", "options", "plan", "program"]

# What a million of your worth (cash and squad value) left at the horizon is
# worth in season points: a move's money (Market.money, MONEY_H updates on)
# counts at this rate beside its points. The price of a million now is not
# this: it is the budget's, out of the plan (Board.lam). grading.py money
# replays it, every bid settled against the real auctions: over jornadas 2-9,
# against a manager who never moves (332 points, 247M), 8 made 360 points and
# 373M; points alone (0) 350 and 297M, overbidding for certainty
# (2026-10-20).
MONEY_RATE = 8.0
# What each move costs the plan by itself, in points: none worth making
# that gains nothing (a tie goes to doing nothing).
TIE = 1e-4
# How much a point of season variance is worth, in points, for each plan
# the program makes (Hunter, Vielma and Zaman, arXiv 1604.01455): above 0 it
# seeks variance, below it avoids it. Of the plans, the one that most
# raises your chance of first (Move.d_first) is made: behind the leader a
# riskier one, ahead a steadier one (docs/notes/plan.md).
KAPPAS = (0.0, -0.05, -0.02, 0.02, 0.05)
# The longest one program may take, in seconds: past it, the best plan
# found so far (a board is built at every run, and rivals' too, within
# lfg.service's ten minutes).
SOLVE_SECONDS = 5.0

@dataclass(frozen=True)
class Move:
    """One action, scored against doing nothing: its expected change in
    season points, its money counted at MONEY_RATE (score), and the chance
    it leaves you better off (ffcore.gain, Market.money); money, what it
    does to your money MONEY_H updates on, in millions; win, for a buy the
    chance its bid wins the auction (Universe.bid); d_first, what it adds to
    your chance of finishing first, in points of percentage; alone, whether it can be
    made by itself (a sale of a player your eleven can do without: one of
    any other can only pay for a buy that leaves a side to field). The
    money rule bounds what can be done (decide.raised)."""
    action: Action
    d_pts: float
    p_better: float = 1.0
    money: float = 0.0
    win: float = 1.0
    alone: bool = True
    d_first: float = 0.0

    @property
    def per_million(self) -> float:
        """Points it adds per million it spends, or for a sale costs per
        million it raises: what the plan weighs money by."""
        return self.d_pts / max(abs(self.action.net) / 1e6, 1.0)


class Board(NamedTuple):
    """The recommendation: the moves to make and what they gain together,
    every move ranked from your squad as it is, the league they are
    measured from (start), and what a million is worth at the margin
    (lam, points a million: plan)."""
    plan: list[Move]
    gain: float
    rows: list[Move]
    start: Universe
    lam: float = 0.0

    @property
    def base(self) -> Standings:
        """The season if you do nothing (from start)."""
        return self.start.standings

    def sale(self, k: str) -> Move | None:
        """Selling him on his own, as ranked."""
        return next((r for r in self.rows
                     if not r.action.buy and r.action.sell == (k, )), None)


@dataclass(frozen=True)
class Universe:
    """The decision: the league as it stands, what players will score and
    what they cost. Frozen, so its cached outlook can never go stale; a
    different universe is a new one (dataclasses.replace)."""
    state: LeagueState
    forecaster: Bootstrap
    market: Market = field(default_factory=Market)
    rival_cash: dict[str, float] = field(default_factory=dict)
    part_played: dict[int, set[str]] = field(default_factory=dict)
    first_jornada_of: dict[str, int] = field(default_factory=dict)
    factors: PerJornadaFactors = field(default_factory=dict)

    @property
    def me(self) -> str:
        return self.state.me

    @property
    def mine(self) -> Squad:
        return self.state.squads.get(self.me, {})

    @cached_property
    def standings(self) -> Standings:
        """Every manager's season if nobody moves (ffcore.gain)."""
        st = self.state
        return standings(self.forecaster, apply(self), st.jornadas, st.carried, st.me)

    @cached_property
    def outlook(self) -> Outlook:
        return Outlook(self.state, self.forecaster, self.market.pos,
                       self.part_played, self.first_jornada_of)

    def acquire(self, k: str) -> Action | None:
        """How you could get him: a bid for a player on the market, at his
        asking price (rank raises it, bid). Never a rival's player by his
        clause: you do not shoot first."""
        m = self.market
        if k in m.owner or k not in m.price:
            return None
        return Action("buy", buy=k, cost=m.price[k])

    def after(self, *acts: Action) -> Universe:
        """The league once these moves are made, your cash with it."""
        return replace(self, state=replace(self.state, squads=apply(self, *acts)),
                       market=replace(self.market, cash=self.market.left(acts)))

    def squad_after(self, *acts: Action) -> Squad:
        sq = dict(self.mine)
        for a in acts:
            for k in a.sell:
                sq.pop(k, None)
            if a.buy:
                sq[a.buy] = position(self.market.pos.get(a.buy))
        return sq

    def candidates(self) -> list[Action]:
        """Every move to rank: each of your players sold, and each player on
        the market you could get. What a buy needs beyond your cash, the plan
        raises by selling."""
        m = self.market
        out = [Action("sell", sell=(s, ), proceeds=m.fetches(s)) for s in sorted(self.mine)]
        return out + [a for k in sorted(m.price) if (a := self.acquire(k))]

    def why_not(self, k: str) -> str | None:
        """Why getting him is not a candidate, or None if it is."""
        a = self.acquire(k)
        if a is None:
            owner = self.market.owner.get(k)
            return "%s's player" % owner if owner else "nobody's, and not on the market now"
        return None

    def rank(self, acts: list[Action]) -> list[Move]:
        """Each move, scored against doing nothing (score), best first; a buy
        at its cost, with its auction's chance at the lowest bid it offers
        (the plan chooses the bid: options); a sale marked if it leaves no
        side to field (Move.alone)."""
        odds = self.market.odds
        return sorted((Move(a, g, p, c, (odds.get(a.buy) or ((1.0, 1.0),))[0][1] if a.buy else 1.0,
                            bool(a.buy) or _fieldable(self.squad_after(a)), f)
                       for a, (g, p, c, f) in zip(acts, score(self, [[a] for a in acts]))),
                      key=lambda mv: (-mv.d_pts, mv.action.net))


def score(u: Universe, sets: list[list[Action]]) -> list[tuple[float, float, float, float]]:
    """Each set of actions made together against doing nothing: (its
    expected change in your season points with its money at MONEY_RATE, the
    chance that is more than nothing, its money in millions, and what it
    adds to your chance of first in points of percentage). Points and money
    are independent normals (ffcore.gain, Market.money). A jornada under
    way is fielded as it stands, so only the ones to come count. Your
    chance of first: your season (Universe.standings) with the jornadas to
    come played by the squad after it, its money at MONEY_RATE, against the
    rivals' as they stand (Standings.position)."""
    to_come = [j for j in u.state.jornadas if j not in u.part_played]
    squads = [apply(u, *acts)[u.me] for acts in sets]
    pts = moments(u.forecaster, to_come, u.mine, squads)
    (old_m, old_v, _p), *alone = moments(u.forecaster, to_come, {}, [apply(u)[u.me], *squads])
    base = u.standings
    (mean_me, sd_me), first = base.dist.get(u.me, (0.0, 0.0)), base.position().get(1, 0.0)
    out = []
    for acts, (mean, var, _p), (new_m, new_v, _q) in zip(sets, pts, alone):
        cash, spread = u.market.money(acts, pricing.MONEY_H)
        total = mean + MONEY_RATE * cash
        mine = (mean_me - old_m + new_m + MONEY_RATE * cash,
                math.sqrt(max(sd_me ** 2 - old_v + new_v + MONEY_RATE ** 2 * spread, 0.0)))
        after = Standings({**base.dist, u.me: mine}, u.me).position().get(1, 0.0)
        out.append((total, normal_above(total, var + MONEY_RATE ** 2 * spread), cash, 100.0 * (after - first)))
    return out


def plan(u: Universe, rows: list[Move], kappas: tuple[float, ...] = KAPPAS) -> tuple[list[Move], float]:
    """The best set of the ranked moves, made together, and what a million
    is worth at the margin (docs/notes/plan.md). One integer program
    (program) over each sale and each buy at each bid its auction offers
    (options): the squad that leaves and the eleven each jornada to come
    fields from it, so moves are valued as a set against the slots. A buy's
    chosen bid is ranked again at it, with its chance of winning. What a
    million is worth: the budget's price in the program's linear
    relaxation, in points a million. The program is solved at each of
    KAPPAS, a weight on the season's variance, and the plan that most raises
    your chance of first is the one made."""
    best: tuple[float, list[Move], float] | None = None
    for kappa in kappas:
        chosen, _value, _price = program(u, rows, relax=False, kappa=kappa)
        moves = [replace(u.rank([o.action])[0], win=o.win) if o.action.buy else o for o in chosen]
        first = joint(u, moves).d_first if moves else 0.0
        if best is None or first > best[0] + 1e-9:
            best = (first, moves, kappa)
    assert best is not None
    _x, _v, lam = program(u, rows, relax=True, kappa=best[2])
    return best[1], lam


def options(u: Universe, rows: list[Move]) -> list[Move]:
    """What the plan chooses from: each sale as ranked, and each buy at every
    bid his auction's odds offer (Market.odds; at asking, sure, with none),
    with its chance of winning (Move.win) and its money at that bid."""
    m, out = u.market, []
    for r in rows:
        if not r.action.buy:
            out.append(r)
            continue
        asking = m.price[r.action.buy]
        for ratio, p in m.odds.get(r.action.buy) or ((1.0, 1.0),):
            a = replace(r.action, cost=asking * ratio)
            out.append(replace(r, action=a, win=p, money=m.money([a], pricing.MONEY_H)[0]))
    return out


def program(u: Universe, rows: list[Move], relax: bool, force: Move | None = None,
            kappa: float = 0.0) -> tuple[list[Move], float, float]:
    """The plan's program over the options of these moves: the options it
    makes, the objective, and the budget's price (relax: its linear
    relaxation, where the price is defined); force: a move the plan must
    make, at some bid (what it costs the plan).

    One bid at most a player. Each jornada's eleven is one formation
    (rules.SHAPES) from the squad after the moves; a slot nobody fills
    scores nothing (empty). A bid that wins with chance P holds his slot
    that share of the time, the rest left to whoever else would fill it,
    and brings its points and its money that share of the time. The money
    rule holds if every bid wins: cash at the lock, after every bid and
    every sale, at least zero. A squad that fields a side keeps one if every
    bid that can lose does. Objective: the elevens' expected points and the moves'
    money at MONEY_RATE, less TIE a move, and kappa times the elevens'
    season variance (gain.py's: each cell's match variance and the
    persistent share, the latter taken at each player's own season)."""
    import highspy

    m = u.market
    h = highspy.Highs()
    h.setOptionValue("output_flag", False)
    h.setOptionValue("mip_rel_gap", 0.0)
    h.setOptionValue("time_limit", SOLVE_SECONDS)
    binary = (lambda: h.addVariable(0.0, 1.0)) if relax else h.addBinary
    opts = options(u, rows)
    made = [binary() for _o in opts]
    sold = {o.action.sell[0]: x for o, x in zip(opts, made) if not o.action.buy}
    keep = {k: 1 - sold[k] if k in sold else 1 for k in u.mine}
    bids = [(o, x) for o, x in zip(opts, made) if o.action.buy]
    for k in {o.action.buy for o, _x in bids}:
        h.addConstr(sum(x for o, x in bids if o.action.buy == k) <= 1)
    pos = {k: position(m.pos.get(k)) for k in [*u.mine, *(o.action.buy for o, _x in bids)]}
    to_come = [j for j in u.state.jornadas if j not in u.part_played]
    fc = u.forecaster
    cells, var, s2 = fc.per_jornada, cell_variance(fc), fc.share ** 2
    season = {k: sum(expected_points(cells.get(j, {}).get(k)) for j in to_come) for k in pos}

    def worth(k: str, j: int) -> float:
        """What his place in jornada j's eleven adds: its expected points and
        kappa times its variance."""
        cell = cells.get(j, {}).get(k)
        e = expected_points(cell)
        return e + kappa * ((1 + s2) * (var(*cell) if cell else 0.0) + s2 * season[k] * e)
    points = []
    for j in to_come:
        shape = [binary() for _s in SHAPES]
        h.addConstr(sum(shape) == 1)
        for p in POSITIONS:
            xi = {k: h.addVariable(0.0, 1.0) for k in u.mine if pos[k] == p}
            yi = [(o, h.addVariable(0.0, 1.0)) for o, _x in bids if pos[o.action.buy] == p]
            for k, x in xi.items():
                if k in sold:
                    h.addConstr(x <= keep[k])
            for (o, y), x in ((oy, x) for oy in yi for o2, x in bids if o2 is oy[0]):
                h.addConstr(y <= x)
            empty = h.addVariable(0.0, MAX_SLOT[p])
            h.addConstr(sum(xi.values()) + sum(o.win * y for o, y in yi) + empty
                        == sum(sh[p] * z for sh, z in zip(SHAPES, shape)))
            points += [worth(k, j) * x for k, x in xi.items()]
            points += [worth(o.action.buy, j) * o.win * y for o, y in yi]
    if _fieldable(u.mine):
        lost = [binary() for _s in SHAPES]
        h.addConstr(sum(lost) == 1)
        for p in POSITIONS:
            h.addConstr(sum(keep[k] for k in u.mine if pos[k] == p)
                        + sum(x for o, x in bids if o.win >= 1.0 and pos[o.action.buy] == p)
                        >= sum(sh[p] * z for sh, z in zip(SHAPES, lost)))
    budget = h.addConstr(sum(o.action.net / 1e6 * x for o, x in zip(opts, made)) <= m.cash / 1e6)
    if force is not None:
        h.addConstr(sum(x for o, x in zip(opts, made) if o.action.players == force.action.players) == 1)
    h.maximize(sum(points) + sum((MONEY_RATE * o.win * o.money - TIE) * x for o, x in zip(opts, made)))
    sol = h.getSolution()
    return ([o for o, x in zip(opts, made) if sol.col_value[x.index] > 0.5], h.getInfo().objective_function_value,
            abs(sol.row_dual[budget.index]) if relax else 0.0)


def joint(u: Universe, moves: list[Move]) -> Move:
    """These moves made together, scored against doing nothing."""
    (g, p, c, f), = score(u, [[mv.action for mv in moves]])
    return Move(Action("plan"), g, p, c, d_first=f)



# The funnel, in order: every recommendation passes these steps, the
# diagram draws them (tools/uml.py) and tools/ask.py names the one a
# player stopped at.
FUNNEL = (Universe.candidates, Universe.rank, plan)


def board(u: Universe, kappas: tuple[float, ...] = KAPPAS) -> Board:
    """The funnel end to end: what the report shows and ask.py explains.
    Every move ranked from your squad as it is (a buy the plan makes, at
    its bid), the plan the program makes of them (in debt, the sales that clear it are among its moves), what
    they gain together and what a million is worth."""
    rows = u.rank(u.candidates())
    picked, lam = plan(u, rows, kappas)
    made = {m.action.players: m for m in picked}
    return Board(picked, joint(u, picked).d_pts if picked else 0.0,
                 [made.get(r.action.players, r) for r in rows], u, lam)


def _fieldable(squad: Squad) -> bool:
    return not shortfall(squad)




def apply(u: Universe, *acts: Action) -> Squads:
    sq = {m: dict(s) for m, s in u.state.squads.items()}
    sq[u.me] = u.squad_after(*acts)
    return {m: phantom_topup(s) for m, s in sq.items()}


def _selftest() -> None:
    """The funnel's tests run with money at no value but where a test sets
    MONEY_RATE: their players have no market value to count."""
    was = globals()["MONEY_RATE"]
    try:
        globals()["MONEY_RATE"] = 0.0
        _selftest_funnel()
    finally:
        globals()["MONEY_RATE"] = was


def _selftest_funnel() -> None:

    from ffcore.forecast import Bootstrap as B
    from ffcore.gain import eleven

    sq = {"k": "POR", **{f"d{i}": "DEF" for i in range(1, 5)},
          **{f"m{i}": "MED" for i in range(1, 6)}, "f1": "DEL", "bench": "MED"}
    mine = {f"me_{k}": v for k, v in sq.items()}
    theirs = {f"th_{k}": v for k, v in sq.items()}
    allk = list(mine) + list(theirs) + ["star", "dud"]
    per = {1: {k: (3.0, 1.0) for k in allk}}
    per[1]["star"] = (12.0, 1.0)
    per[1]["dud"] = (0.2, 1.0)
    per[1]["me_bench"] = (0.5, 1.0)
    per[1]["th_m1"] = (6.0, 1.0)

    u = Universe(
        state=LeagueState({"me": dict(mine), "riv": dict(theirs)}, [1], "me"),
        forecaster=B(per),
        market=Market(
            cash=12e6,
            pos={**mine, **theirs, "star": "MED", "dud": "MED"},
            price={"star": 10e6, "dud": 1e6, "th_m1": 5e6},
            value={"me_bench": 8e6}, owner={"th_m1": "riv"}))

    first = u.outlook.xi
    assert u.outlook.xi is first, "cached_property must not recompute"

    cxi_exp, cxi = u.outlook.xi
    fallback_j = next((j for j in u.state.jornadas if j not in u.part_played),
                      u.state.jornadas[0] if u.state.jornadas else 0)
    assert cxi_exp == u.forecaster.expected(fallback_j), cxi_exp
    assert "me_bench" not in cxi, cxi
    assert len(cxi) == 11, cxi

    acts = u.candidates()
    names = {a.buy for a in acts}
    assert {"dud", "star"} <= names, "every player on the market is ranked; no second measure filters first"
    assert u.why_not("star") is None and u.why_not("dud") is None
    assert "dud" not in {r.action.buy for r in board(u).plan}, "he adds nothing to the eleven"
    behind = replace(u, state=replace(u.state, carried={"riv": 30.0}))
    by = {r.action.buy: r for r in behind.rank(behind.candidates()) if r.action.buy}
    assert by["star"].d_first > 0 and by["star"].d_first > by["dud"].d_first, \
        "behind, a buy that adds points adds to your chance of first, a better one more"
    (_g, _p, _c, none), = score(behind, [[]])
    assert abs(none) < 1e-9, "doing nothing changes nothing"
    assert u.why_not("nobody") == "nobody's, and not on the market now"
    assert u.why_not("th_m1") == "riv's player"

    acts = u.candidates()
    assert not any(a.buy == "th_m1" for a in acts), "a rival's player is not for sale"
    open_th = replace(u, market=replace(u.market, clause={"th_m1": 5e6}))
    assert not any(a.buy == "th_m1" for a in open_th.candidates()), \
        "not even when his clause can be paid: no clause buys"
    assert next(a for a in acts if a.buy == "star").cost == 10e6, "a buy is offered at asking"
    sells = {r.action.sell: r for r in u.rank(acts) if r.action.kind == "sell"}
    assert sells[("me_bench",)].alone and not sells[("me_k",)].alone, \
        "a spare can be sold by himself; your only keeper cannot"


    per3 = {1: dict(per[1])}
    per3[1].update({"dear": (40.0, 1.0), "me_spare2": (0.4, 1.0),
                    "me_spare3": (0.3, 1.0)})
    u3 = Universe(
        state=LeagueState({"me": {**mine, "me_spare2": "MED", "me_spare3": "POR"},
                           "riv": dict(theirs)}, [1], "me"),
        forecaster=B(per3),
        market=Market(
            cash=4e6, pos={**u.market.pos, "dear": "MED"},
            price={"dear": 20e6},
            value={"me_bench": 8e6, "me_spare2": 5e6, "me_spare3": 4e6}))
    acts3 = u3.candidates()
    assert [a.sell for a in acts3 if a.buy == "dear"] == [()], \
        "a buy is offered from your cash; sales are moves of their own"
    assert any(r.action.buy == "dear" for r in u3.rank(acts3)), \
        "and ranked though you cannot pay for it yet"
    plan3 = board(u3).plan
    assert {r.action.buy for r in plan3} - {""} == {"dear"}, plan3
    assert sorted(k for r in plan3 for k in r.action.sell) == [
        "me_bench", "me_spare2", "me_spare3"], ("no one spare pays for him; three together do", plan3)

    af = apply(u, Action("buy", buy="star", sell=("me_bench",)))
    assert "me_bench" not in af["me"] and "star" in af["me"]

    rows = u.rank(acts)
    assert rows, "something should be worth doing"
    ways = [r.action.buy or r.action.sell for r in rows]
    assert len(set(ways)) == len(ways) and "star" in ways, "each move once"
    top = rows[0]
    assert top.d_pts > 0, top.d_pts
    assert [r.d_pts for r in rows] == sorted((r.d_pts for r in rows), reverse=True)

    vsq = {"me_k": "POR",
           **{"me_d%d" % i: "DEF" for i in range(1, 6)},
           **{"me_m%d" % i: "MED" for i in range(1, 7)},
           "me_f1": "DEL"}
    vth = {"th_" + k[3:]: v for k, v in vsq.items()}
    vrate = {k: (5.0 if v == "MED" else 3.0) for k, v in vsq.items()}
    vrate["me_f1"] = 1.0
    vrate.update({k: 3.0 for k in vth})
    vper = {1: {k: (r, 1.0) for k, r in vrate.items()}}
    vper[1]["thin_del"] = (8.0, 1.0)
    vper[1]["deep_med"] = (8.0, 1.0)
    uvor = Universe(
        state=LeagueState({"me": dict(vsq), "riv": dict(vth)}, [1], "me"),
        forecaster=B(vper),
        market=Market(
            cash=6e6,
            pos={**vsq, **vth, "thin_del": "DEL", "deep_med": "MED"},
            price={"thin_del": 5e6, "deep_med": 5e6},
            route={"thin_del": "free", "deep_med": "free"}))
    vexp, vxi = uvor.outlook.xi
    assert min(vexp[k] for k in vxi if uvor.market.pos[k] == "DEL") == 1.0, vxi
    assert min(vexp[k] for k in vxi if uvor.market.pos[k] == "MED") == 5.0, vxi
    vrows = uvor.rank([Action("buy", buy="thin_del", cost=5e6),
               Action("buy", buy="deep_med", cost=5e6)])
    vby = {r.action.buy: r for r in vrows}
    assert vby["thin_del"].action.net == vby["deep_med"].action.net
    assert vby["thin_del"].d_pts > 2 * vby["deep_med"].d_pts > 0, vby

    bsq = {"me_k": "POR", "me_d1": "DEF", "me_d2": "DEF", "me_d3": "DEF",
           "me_d4": "DEF", "me_d5": "DEF", "me_m1": "MED", "me_m2": "MED",
           "me_m3": "MED", "me_m4": "MED", "me_f1": "DEL"}
    bexp = {"me_k": 3.0, "me_d1": 3.0, "me_d2": 3.0, "me_d3": 3.0,
            "me_d4": 3.0, "me_d5": 1.0, "me_m1": 4.0, "me_m2": 4.0,
            "me_m3": 4.0, "me_m4": 4.0, "me_f1": 3.0, "cand": 2.0}
    bxi = set(eleven(bsq, bexp))
    assert bxi == set(bsq), bxi
    assert min(bexp[k] for k in bxi) == 1.0
    assert min(bexp[k] for k in bxi if bsq[k] == "MED") == 4.0
    bsq2 = {**bsq, "cand": "MED"}
    bxi2 = set(eleven(bsq2, bexp))
    assert "cand" in bxi2 and "me_d5" not in bxi2, bxi2
    assert sum(1 for k in bxi2 if bsq2[k] == "DEF") == 4, bxi2
    assert sum(bexp[k] for k in bxi2) - sum(bexp[k] for k in bxi) == 1.0

    half = Universe(
        state=LeagueState({"me": dict(mine), "riv": dict(theirs)}, [1, 2],
                          "me", ),
        forecaster=B({1: {"me_k": (0.1, 1.0), "dud": (1.0, 1.0)},
                      2: {**{k: (5.0, 1.0) for k in mine}, "dud": (1.0, 1.0)}}),
        market=Market(cash=50e6, pos={**u.market.pos, "dud": "MED"},
                      price={"dud": 1e6}),
        part_played={1: {"somewhere"}})
    assert "dud" not in {r.action.buy for r in board(half).plan}, \
        "a jornada already under way is the squads as they stand: he adds nothing"

    # a squad with no side to field (no keeper, no forward) rebuilds one, a buy at a time
    gap = {k: v for k, v in mine.items() if v not in ("POR", "DEL")}
    holed = Universe(state=LeagueState({"me": gap, "riv": dict(theirs)}, [1], "me"),
                     forecaster=B({1: {**per[1], "keeper": (4.0, 1.0), "striker": (5.0, 1.0)}}),
                     market=Market(cash=50e6, pos={**u.market.pos, "keeper": "POR", "striker": "DEL"},
                                   price={"keeper": 1e6, "striker": 1e6}))
    assert not _fieldable(holed.mine) and {"keeper", "striker"} <= {r.action.buy for r in board(holed).plan}, \
        ("no keeper and no forward: both bought, though neither alone leaves a side", board(holed).plan)

    ok_squad = {"k": "POR", "d1": "DEF", "d2": "DEF", "d3": "DEF",
               "d4": "DEF", "m1": "MED", "m2": "MED", "m3": "MED",
               "m4": "MED", "f1": "DEL", "f2": "DEL"}
    assert _fieldable(ok_squad), ok_squad
    assert not _fieldable({k: v for k, v in ok_squad.items() if k != "k"})
    two_keepers = {k: v for k, v in ok_squad.items() if k != "f2"}
    two_keepers["k2"] = "POR"
    assert not _fieldable(two_keepers), two_keepers
    assert _fieldable({**ok_squad, "d5": "DEF", "m5": "MED"})

    per_cd = {1: {"me_k": (2.0, 1.0), "me_d1": (3.0, 1.0), "me_d2": (3.0, 1.0),
                "me_d3": (3.0, 1.0), "me_d4": (3.0, 1.0),
                "me_m1": (3.0, 1.0), "me_m2": (3.0, 1.0), "me_m3": (3.0, 1.0),
                "me_m4": (3.0, 1.0), "me_f1": (5.0, 1.0), "me_f2": (4.0, 1.0),
                "me_f3": (0.1, 1.0),
                "target": (20.0, 1.0)}}
    sq_cd = {"me_k": "POR", "me_d1": "DEF", "me_d2": "DEF", "me_d3": "DEF",
           "me_d4": "DEF", "me_m1": "MED", "me_m2": "MED", "me_m3": "MED",
           "me_m4": "MED", "me_f1": "DEL", "me_f2": "DEL", "me_f3": "DEL"}
    from ffcore.forecast import Bootstrap as BCD
    u_cd = Universe(
        state=LeagueState({"me": dict(sq_cd)}, [1], "me"),
        forecaster=BCD(per_cd),
        market=Market(pos={**sq_cd, "target": "DEL"}, price={"target": 5e6},
                      value={"me_f3": 5e6}))
    assert sorted(r.action.label() for r in board(u_cd).plan) == ["buy target", "sell me_f3"], \
        "with no cash, he is paid for by the one spare that raises it"
    squad11 = {k: v for k, v in sq_cd.items() if k != "me_f3"}
    u_11 = Universe(state=LeagueState({"me": dict(squad11)}, [1], "me"), forecaster=BCD(per_cd),
                    market=Market(pos={**squad11, "target": "DEL"}, price={"target": 5e6},
                                  value={"me_f2": 5e6, "me_k": 9e6}))
    assert sorted(r.action.label() for r in board(u_11).plan) == ["buy target", "sell me_f2"], \
        "a bare eleven with no cash still swaps: the forward sold pays for the one bought"


    sq = {"k": "POR", "d1": "DEF", "d2": "DEF", "d3": "DEF", "d4": "DEF",
         "spare_d": "DEF", "m1": "MED", "m2": "MED", "m3": "MED", "m4": "MED",
         "f1": "DEL", "dead_f": "DEL"}
    per = {1: {k: (3.0, 1.0) for k in sq}}
    per[1]["f1"] = (10.0, 1.0)
    per[1]["dead_f"] = (0.1, 1.0)
    u = Universe(
        state=LeagueState({"me": dict(sq)}, [1], "me"),
        forecaster=Bootstrap(per),
        market=Market(pos=dict(sq),
                      value={"spare_d": 4e6, "dead_f": 6e6}))
    mine = u.state.squads["me"]
    alone = {r.action.sell[0]: r for r in u.rank(u.candidates()) if r.action.kind == "sell"}
    assert set(alone) == set(mine), "every player of yours has a sale ranked"
    for s, r in alone.items():
        assert r.alone == _fieldable({p: pos for p, pos in mine.items() if p != s}), s
    assert not alone["k"].alone and alone["f1"].alone and alone["dead_f"].alone
    assert "k" not in {k for r in board(u).plan for k in r.action.sell}, "your only keeper's slot is not left empty"
    bare = Universe(
        state=LeagueState({"me": {"k": "POR", "d1": "DEF", "d2": "DEF",
                                  "d3": "DEF", "m1": "MED", "m2": "MED",
                                  "m3": "MED", "f1": "DEL"}}, [1], "me"),
        forecaster=Bootstrap({1: {}}))
    assert not any(r.alone for r in bare.rank(bare.candidates())), "a bare eleven has no player to spare"
    after = apply(u, Action("buy", buy="new_por", sell=("spare_d",)),
                  Action("sell", sell=("dead_f",)))
    assert "dead_f" not in after["me"], after["me"]
    assert "spare_d" not in after["me"], after["me"]
    assert after["me"]["new_por"] == "MED", after["me"]

    assert [f.__name__ for f in FUNNEL] == ["candidates", "rank", "plan"]

    ks = {"k": "POR", **{"d%d" % i: "DEF" for i in range(1, 5)},
          **{"m%d" % i: "MED" for i in range(1, 5)}, "f1": "DEL", "f2": "DEL"}
    js = list(range(1, 11))
    weak = {"m2", "m3", "m4"}
    perk = {j: {**{k: (0.5 if k in weak else 3.0, 1.0) for k in ks},
                "dear": (4.0, 1.0), "b": (3.0, 1.0), "c": (3.0, 1.0)} for j in js}
    uk = Universe(state=LeagueState({"me": dict(ks)}, js, "me"), forecaster=Bootstrap(perk),
                  market=Market(cash=20e6,
                                pos={**ks, "dear": "MED", "b": "MED", "c": "MED"},
                                price={"dear": 20e6, "b": 10e6, "c": 10e6}))
    got = sorted(r.action.buy for r in board(uk).plan)
    assert got == ["b", "c"], ("two cheaper moves gaining more beat one dear one", got)

    perd = {j: {**perk[j], "idle": (0.1, 1.0), "useful": (2.0, 1.0)} for j in js}
    owing = Universe(state=LeagueState({"me": {**ks, "idle": "MED", "useful": "MED"}}, js, "me"),
                     forecaster=Bootstrap(perd),
                     market=Market(cash=-5e6, pos={**uk.market.pos, "idle": "MED",
                                                             "useful": "MED"},
                                   price=uk.market.price,
                                   value={"idle": 6e6, "useful": 6e6}))
    owed = board(owing)
    got = [r.action.label() for r in owed.plan]
    assert got == [Action("sell", sell=("idle",)).label()], \
        ("a debt is raised by the sale that costs fewest points", got)
    perc = {j: {**perk[j], "big": (3.0, 1.0), "small": (1.0, 1.0)} for j in js}
    short = Universe(state=LeagueState({"me": {**ks, "big": "MED", "small": "MED"}}, js, "me"),
                     forecaster=Bootstrap(perc),
                     market=Market(cash=-5e6, pos={**uk.market.pos, "big": "MED", "small": "MED"},
                                   price=uk.market.price, value={"big": 60e6, "small": 6e6}))
    by = {r.action.label(): r for r in short.rank(short.candidates())}
    assert by["sell big"].per_million > by["sell small"].per_million, "the case: big is cheaper a million"
    got = [r.action.label() for r in board(replace(short, market=replace(short.market, price={}))).plan]
    assert got == ["sell small"], ("nothing to buy: a 5M gap is not raised by a 60M sale costing more points", got)
    got = sorted(r.action.label() for r in board(short).plan)
    assert got == ["buy b", "buy c", "buy dear", "sell big"], \
        ("with buys to make, the 60M sale pays for three that lift the weak midfield: worth more together", got)
    perx = {j: {**perk[j], "x1": (0.1, 1.0), "x2": (2.0, 1.0), "f3": (3.0, 1.0)} for j in js}
    sq = {**{k: p for k, p in ks.items() if k not in ("d3", "d4")},
          "x1": "DEF", "x2": "DEF", "f3": "DEL"}
    tight = Universe(state=LeagueState({"me": sq}, js, "me"), forecaster=Bootstrap(perx),
                     market=Market(cash=-8e6, pos={**uk.market.pos, "x1": "DEF", "x2": "DEF", "f3": "DEL"},
                                   price=uk.market.price, value={"x1": 1e6, "x2": 10e6}))
    got = [r.action.label() for r in board(tight).plan]
    assert got[:1] == ["sell x2"], (
        "the sales that cover the gap at fewest points, though the cheapest one leaves"
        " no second defender to sell", got)
    assert owed.sale("idle") is owed.plan[0] and owed.rows == owing.rank(owing.candidates()), \
        "in debt, the sale clearing it is a move like any other, every move ranked from your squad as it is"
    assert owing.after(owed.plan[0].action).market.cash == 1e6, "after a move, its cash too"
    listed = replace(owing, market=replace(owing.market, owner={"idle": "me"},
                                           price={**owing.market.price, "idle": 6e6}))
    assert listed.after(Action("sell", sell=("idle",))).acquire("idle") is None, \
        "your own listed player is not a buy, sold or not"
    deep = replace(owing, market=replace(owing.market, cash=-8e6))
    assert sorted(s for r in board(deep).plan for s in r.action.sell) == ["idle", "useful"], \
        "whatever it costs"
    pers = {j: {**perk[j], "star": (5.0, 1.0), "ace": (4.0, 1.0)} for j in js}
    star = Universe(state=LeagueState({"me": {**ks, "star": "MED"}}, js, "me"),
                    forecaster=Bootstrap(pers),
                    market=Market(cash=-30e6, pos={**ks, "star": "MED", "ace": "MED"},
                                  price={"ace": 1e6}, value={"star": 31e6}))
    got = sorted(r.action.label() for r in board(star).plan)
    assert got == ["buy ace", "sell star"], ("the sale that clears a debt pays for a buy too", got)
    sold = star.after(Action("sell", sell=("star",)))
    assert "star" not in sold.mine and "star" in star.mine and "star" not in sold.outlook.xi.players
    assert joint(uk, board(uk).plan).d_pts == board(uk).gain, "the plan's gain: its moves made together"

    # money at MONEY_RATE: a falling spare is worth selling, a rising buy beats a falling one
    sqm = {**ks, "fall": "MED"}
    perm = {j: {**perk[j], "fall": (0.1, 1.0), "up": (3.0, 1.0), "down": (3.0, 1.0)} for j in js}
    um = Universe(state=LeagueState({"me": sqm}, js, "me"), forecaster=Bootstrap(perm),
                  market=Market(cash=20e6, pos={**sqm, "up": "MED", "down": "MED"},
                                price={"up": 10e6, "down": 10e6},
                                value={"fall": 5e6, "up": 10e6, "down": 10e6},
                                trend={"fall": -40.0, "up": 30.0, "down": -30.0}, carry=(1.0,)))
    def by_rate(rate: float) -> dict[str, Move]:
        was = globals()["MONEY_RATE"]
        try:
            globals()["MONEY_RATE"] = rate
            return {r.action.label(): r for r in um.rank(um.candidates())}
        finally:
            globals()["MONEY_RATE"] = was
    flat, rich = by_rate(0.0), by_rate(3.0)
    def planned(rate: float) -> set[str]:
        was = globals()["MONEY_RATE"]
        try:
            globals()["MONEY_RATE"] = rate
            return {r.action.label() for r in board(um).plan}
        finally:
            globals()["MONEY_RATE"] = was
    assert "sell fall" not in planned(0.0) and "sell fall" in planned(3.0), \
        "points alone, his sale gains nothing; at 3 points a million, 5M now for one worth 3M is worth it"
    assert abs(rich["sell fall"].money - 2.0) < 1e-9 and flat["sell fall"].money == rich["sell fall"].money
    assert flat["buy up"].d_pts == flat["buy down"].d_pts, "points alone, the same buy"
    assert abs(rich["buy up"].d_pts - flat["buy up"].d_pts - 3.0 * 3.0) < 1e-9 \
        and abs(rich["buy down"].d_pts - flat["buy down"].d_pts + 3.0 * 3.0) < 1e-9, \
        "+3M and -3M in money, at 3 points a million"

    _selftest_program()
    print("decide self-test OK")


def _selftest_program() -> None:
    """The plan as one program (docs/notes/plan.md): moves valued as a set
    against the slots, and the price of money read off the budget."""
    js = list(range(1, 11))
    sq = {"k": "POR", **{"d%d" % i: "DEF" for i in range(1, 5)}, **{"m%d" % i: "MED" for i in range(1, 6)},
          "f1": "DEL", "f2": "DEL", "star": "DEL"}
    cells = {j: {**{k: (3.0, 1.0) for k in sq}, "star": (5.0, 1.0), "m5": (0.0, 1.0)} for j in js}
    owing = Universe(state=LeagueState({"me": dict(sq)}, js, "me"), forecaster=Bootstrap(cells),
                     market=Market(cash=-10e6, pos=dict(sq), value={"f1": 5e6, "f2": 5e6, "star": 12e6}))
    singles = {r.action.sell[0]: r.d_pts for r in owing.rank(owing.candidates()) if not r.action.buy}
    assert singles["f1"] + singles["f2"] > singles["star"], ("the case: alone, each forward's sale costs nothing", singles)
    b = board(owing)
    assert [k for r in b.plan for k in r.action.sell] == ["star"], \
        ("two forwards sold together leave a midfielder of 0 in the eleven: dearer than the star's sale", b.plan)
    assert abs(b.gain - joint(owing, b.plan).d_pts) < 1e-6, "the program's gain is the board's own measure of its moves"
    assert b.lam > 0, "in debt, a million more is worth points: one sale fewer to make"
    rich = replace(owing, market=replace(owing.market, cash=50e6))
    assert board(rich).plan == [] and board(rich).lam == 0.0, \
        "with cash and nothing worth buying, a million more is worth nothing now"

    # bids: an amount each, with its chance of winning; the budget if all win, the squad if all lose
    xi = {"k": "POR", **{"d%d" % i: "DEF" for i in range(1, 5)}, **{"m%d" % i: "MED" for i in range(1, 5)},
          "f1": "DEL", "f2": "DEL"}
    base = {j: {**{k: (3.0, 1.0) for k in xi}, "m5": (3.0, 1.0), "ace": (9.0, 1.0), "sure": (6.0, 1.0), "long": (6.0, 1.0)} for j in js}
    def plan_of(cash: float, odds: dict[str, tuple[tuple[float, float], ...]], value: dict[str, float] | None = None,
                price: dict[str, float] | None = None, rate: float = 0.0) -> dict[str, float]:
        was = globals()["MONEY_RATE"]
        try:
            globals()["MONEY_RATE"] = rate
            uu = Universe(state=LeagueState({"me": dict(xi)}, js, "me"), forecaster=Bootstrap(base),
                          market=Market(cash=cash, pos={**xi, "m5": "MED", "ace": "DEL", "sure": "MED", "long": "MED"},
                                        price=price or {"ace": 10e6}, odds=odds, value=value or {}))
            got = board(uu)
            assert all(m in got.rows for m in got.plan), "the plan's moves are the board's rows"
            return {m.action.label(): m.action.cost or m.action.proceeds for m in got.plan}
        finally:
            globals()["MONEY_RATE"] = was
    assert plan_of(20e6, {"ace": ((1.0, 0.2), (1.25, 1.0))}) == {"buy ace": 12.5e6}, \
        "a contested auction: the bid that wins, worth five times one at asking"
    assert plan_of(20e6, {"ace": ((1.0, 0.95), (1.25, 1.0))}, value={"ace": 10e6}) == {"buy ace": 12.5e6} \
        and plan_of(20e6, {"ace": ((1.0, 0.95), (1.25, 1.0))}, value={"ace": 10e6}, rate=3.0) == {"buy ace": 10e6}, \
        "points alone pay 2.5M for the last 5%; at 3 points a million they do not"
    assert plan_of(10e6, {"sure": ((1.0, 1.0),), "long": ((1.0, 0.1),)},
                   price={"sure": 6e6, "long": 6e6}) == {"buy sure": 6e6}, \
        "the same points, one sure to come and one a long shot: room for one, the sure one"
    assert len(plan_of(10e6, {"sure": ((1.0, 0.5),), "long": ((1.0, 0.5),)},
                       price={"sure": 6e6, "long": 6e6})) == 1, \
        "two bids of 6M on 10M: half the time each, but both may win, and then you are 2M under at the lock"
    lone = {**{k: v for k, v in xi.items() if k != "f2"}, "m5": "MED"}
    assert _fieldable(lone), "the case: a side of 4-5-1, one forward"
    xi.clear()
    xi.update(lone)
    sold = plan_of(0.0, {"ace": ((1.0, 0.5),)}, value={"f1": 10e6})
    assert not ("sell f1" in sold and "buy ace" in sold), \
        ("selling your only forward to bid on another: if the bid is lost you field no forward", sold)

    # the result wanted is first: behind, the riskier of two buys alike on average; ahead, the steadier
    eleven = {"k": "POR", **{"d%d" % i: "DEF" for i in range(1, 5)}, **{"m%d" % i: "MED" for i in range(1, 5)},
              "f1": "DEL", "f2": "DEL"}
    theirs = {"r_" + k: v for k, v in eleven.items()}
    cells2 = {j: {**{k: (3.0, 1.0) for k in [*eleven, *theirs]}, "m4": (0.5, 1.0),
                  "steady": (3.0, 1.0), "swing": (6.0, 0.5)} for j in js}
    def bought(lead: float, kappas: tuple[float, ...] = KAPPAS) -> set[str]:
        uu = Universe(state=LeagueState({"me": dict(eleven), "riv": dict(theirs)}, js, "me",
                                        carried={"me": max(lead, 0.0), "riv": max(-lead, 0.0)}),
                      forecaster=Bootstrap(cells2),
                      market=Market(cash=1e6, pos={**eleven, **theirs, "steady": "MED", "swing": "MED"},
                                    price={"steady": 1e6, "swing": 1e6}))
        return {m.action.buy for m in board(uu, kappas).plan if m.action.buy}
    assert bought(-40.0, (0.0,)) == {"steady"}, "variance weighed at nothing: the tie goes the first way"
    assert bought(-40.0) == {"swing"} and bought(40.0) == {"steady"}, \
        ("the same points on average: behind, the swing of the one who plays half the time is a chance to catch up;"
         " ahead, the steady one keeps the lead", bought(-40.0), bought(40.0))


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
