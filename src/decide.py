from __future__ import annotations

import sys
from dataclasses import dataclass, field, replace
from functools import cached_property
from typing import NamedTuple

from ffcore.action import Action
from ffcore.forecast import Bootstrap
from ffcore.market import Market
from ffcore.outlook import Outlook
from ffcore.render import title_name
from ffcore.rules import FREE_FORMATIONS
from ffcore.schedule import phantom_topup
from ffcore.season import LeagueState, Standings, expected_totals, simulate_many
from stats import percentile

__all__ = ["Action", "Board", "CONFIDENCE", "FUNNEL", "Move", "Universe", "at_risk",
           "board", "plan", "verdict"]

FINAL_TRIALS = 3000
CONFIDENCE = 0.7

@dataclass(frozen=True)
class Move:
    """One action, scored against doing nothing: its median change in season
    points and the share of simulated seasons in which it leaves you better
    off. Money only bounds what can be done (decide.raised)."""
    action: Action
    d_pts: float
    p_better: float = 1.0

    @property
    def per_million(self) -> float:
        """Points it adds per million it spends, or for a sale costs per
        million it raises: what the plan weighs money by."""
        return self.d_pts / max(abs(self.action.net) / 1e6, 1.0)


class Ranking(NamedTuple):
    rows: list[Move]
    base: Standings


class Board(NamedTuple):
    """The recommendation: the moves to make and what they gain together,
    every move ranked, and the season if you do nothing."""
    plan: list[Move]
    gain: float
    rows: list[Move]
    base: Standings

    def sale(self, k: str) -> Move | None:
        """Selling him on his own, as ranked."""
        return next((r for r in self.rows
                     if not r.action.buy and r.action.sell == (k, )), None)

    @property
    def others(self) -> list[Move]:
        """Other players worth getting, should a bid fail."""
        return [r for r in self.rows
                if r.action.buy and r not in self.plan and verdict(r) is None]


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
    _means: dict = field(default_factory=dict, repr=False, compare=False)

    @property
    def me(self) -> str:
        return self.state.me

    @property
    def mine(self) -> dict[str, str]:
        return self.state.squads.get(self.me, {})

    @cached_property
    def outlook(self) -> Outlook:
        return Outlook(self.state, self.forecaster, self.market.pos,
                       self.part_played, self.first_jornada_of)

    def acquire(self, k: str) -> Action | None:
        """How you could get him and what it would cost you: a bid for a
        player on the market, at the premium it takes to win the auction, or
        a rival's player's release clause, paid as it stands and at once."""
        m = self.market
        owner = m.owner.get(k)
        if k in self.mine:
            return None
        if (not owner or owner == self.me) and k in m.price:
            return Action("buy", buy=k, cost=m.price[k] * m.premium)
        if owner and owner != self.me and k in m.clause:
            return Action("clause", buy=k, cost=m.clause[k])
        return None

    def squad_after(self, *acts: Action) -> dict[str, str]:
        sq = dict(self.mine)
        for a in acts:
            for k in a.sell:
                sq.pop(k, None)
            if a.buy:
                sq[a.buy] = self.market.pos.get(a.buy, "MED")
        return sq

    def expected(self, *acts: Action) -> float:
        """Your season after these moves, as the simulated seasons average:
        worked out exactly, so cheap enough to try many moves with."""
        sq = apply(self, *acts)[self.me]
        key = frozenset(sq.items())
        if key not in self._means:
            st = LeagueState({self.me: sq}, self.state.jornadas, self.me,
                             fielded=fielded(self))
            self._means[key] = expected_totals([st], self.forecaster)[0][self.me]
        return self._means[key]

    def points(self, a: Action) -> float:
        """What a move adds to your expected season: a player's points over
        whoever would play instead of him."""
        return self.expected(a) - self.expected()


    def candidates(self) -> list[Action]:
        """Every move worth a look: each spare sold, and each player you
        could get, from your cash, if worth anything at a glance (worth).
        What a buy needs beyond your cash, the plan raises by selling."""
        m = self.market
        out = [Action("sell", sell=(s, ), proceeds=m.fetches(s))
               for s in fieldable_spares(self)]
        for k in sorted(m.price.keys() | m.clause.keys()):
            if (a := self.acquire(k)) and self.points(a) > 0:
                out.append(a)
        return out

    def rank(self, acts: list[Action], seed: int = 1) -> Ranking:
        """Each move, scored against doing nothing in simulated seasons."""
        final = score_many(self, [self.state.squads]
                           + [apply(self, a) for a in acts], FINAL_TRIALS, seed)
        rows = sorted((_move(a, paired(r, final[0], self.me))
                       for a, r in zip(acts, final[1:])),
                      key=lambda mv: (-mv.d_pts, mv.action.net))
        return Ranking(rows, final[0])


def _move(a: Action, pairs: list[float]) -> Move:
    better = sum(1 for x in pairs if x > 0) / len(pairs) if pairs else 0.0
    return Move(a, median_gain(pairs), better)


def verdict(mv: Move) -> str | None:
    """Worth doing only if it gains and is likely to help. It must gain in
    the median and leave you better off in at least
    CONFIDENCE of simulated seasons; below that its gain is noise. Returns
    why not, or None."""
    if mv.d_pts <= 0:
        return "no gain in the median (%+.1f points)" % mv.d_pts
    if mv.p_better < CONFIDENCE:
        return "better off in %.0f%% of seasons, under %.0f%%" % (
            100 * mv.p_better, 100 * CONFIDENCE)
    return None


def plan(u, rows: list[Move], base: Standings) -> tuple[list[Move], float]:
    """The best set of moves to make together. The moves that clear the
    bar, built twice: best first and best per million it ties up first,
    since one dear move can crowd out two cheaper ones that gain more; the
    set that gains more is the plan. Every move it leaves out was last
    tried against the whole of it."""
    good = [r for r in rows if verdict(r) is None]
    return max((fill(u, good, rows, base, key) for key in (
        lambda r: -r.d_pts, lambda r: -r.per_million)), key=lambda pg: pg[1])


def raised(u, moves: list[Move], rows: list[Move]) -> list[Move] | None:
    """These moves and the sales that pay for them, or None if they cannot
    be made together: a player is in one move at most, and the game's one
    money rule is a balance of at least zero at the lock (below it you
    score nothing), so what they leave below zero is raised by the ranked
    sales that cost fewest points a million, a side left to field."""
    m, out = u.market, list(moves)
    players = [k for mv in out for k in mv.action.players]
    taken = set(players)
    if len(taken) < len(players):
        return None
    sales = sorted((r for r in rows if not r.action.buy and r.action.proceeds
                    and not taken & set(r.action.sell)),
                   key=lambda r: -r.per_million)
    for r in sales:
        if m.left([mv.action for mv in out]) >= 0:
            break
        if _fieldable(u.squad_after(*(mv.action for mv in out), r.action)):
            out.append(r)
    return out if m.left([mv.action for mv in out]) >= 0 else None


def fill(u, good: list[Move], rows: list[Move], base: Standings,
         order) -> tuple[list[Move], float]:
    """Moves in this order, each taken, with the sales that pay for it,
    if it shares no player with those taken and adds to their joint gain;
    again until none is taken."""
    picked = raised(u, [], rows) or []
    gain, grew = joint_gain(u, picked, base) if picked else 0.0, True
    while grew:
        grew = False
        for r in sorted(good, key=order):
            if r in picked or (trial := raised(u, [*picked, r], rows)) is None:
                continue
            total = joint_gain(u, trial, base)
            if total > gain:
                picked, gain, grew = trial, total, True
    return picked, gain


def joint_gain(u, moves: list[Move], base: Standings) -> float:
    acts = [mv.action for mv in moves]
    after = score_many(u, [apply(u, *acts)], FINAL_TRIALS, 1)[0]
    return median_gain(paired(after, base, u.me))


def blocked(u, picked: list[Move], a: Action, rows: list[Move]) -> str | None:
    """Why raised refuses a move alongside these, in words. None if it
    does not."""
    names = {k: title_name(n) for k, n in u.market.name.items()}
    for p in picked:
        if shared := set(p.action.players) & set(a.players):
            return "%s is in %s" % (" + ".join(names.get(k, k) for k in shared),
                                    p.action.label(names))
    if raised(u, [*picked, Move(a, 0.0)], rows) is None:
        done = [p.action for p in picked]
        return "it needs %.1fM, %.1fM is left and no sales raise the rest" % (
            a.net / 1e6, u.market.left(done) / 1e6)
    return None


# The funnel, in order: every recommendation passes these steps, the
# diagram draws them (tools/uml.py) and tools/ask.py names the one a
# player stopped at.
FUNNEL = (Universe.candidates, Universe.rank, verdict, plan)


def board(u) -> Board:
    """The funnel end to end: what the report shows and ask.py explains."""
    rows, base = u.rank(u.candidates())
    picked, gain = plan(u, rows, base)
    return Board(picked, gain, rows, base)


def at_risk(u) -> list[tuple[str, list[str]]]:
    """Your players a rival can take now by paying their clause, with the
    rivals whose estimated cash covers it, most valuable to you first."""
    m, season = u.market, u.outlook.season
    out = []
    for k in sorted(u.mine, key=lambda k: -season.get(k, 0.0)):
        by = sorted(g for g, cash in u.rival_cash.items()
                    if k in m.clause and cash >= m.clause[k])
        if by:
            out.append((k, by))
    return out


def _fieldable(squad: dict[str, str]) -> bool:
    depth: dict[str, int] = {}
    for slot in squad.values():
        depth[slot] = depth.get(slot, 0) + 1
    if depth.get("POR", 0) < 1:
        return False
    return any(depth.get("DEF", 0) >= d and depth.get("MED", 0) >= m
              and depth.get("DEL", 0) >= n for d, m, n in FREE_FORMATIONS)


def score_many(u: Universe, many: list, trials: int, seed: int):
    under_way = fielded(u)
    return simulate_many(
        [LeagueState(squads=sq, jornadas=u.state.jornadas, me=u.me,
                     carried=u.state.carried, fielded=under_way) for sq in many],
        u.forecaster, trials=trials, seed=seed)


def fielded(u) -> dict[int, dict[str, dict[str, str]]]:
    """The squads as they stand, for the jornadas already under way: a
    move made now cannot change those."""
    now = apply(u)
    return {j: now for j in u.part_played}


def paired(after, base, me) -> list[float]:
    return sorted(x - y for x, y in zip(after.totals.get(me, []),
                                        base.totals.get(me, [])))


def median_gain(pairs) -> float:
    return percentile(pairs, 50) if pairs else 0.0


def fieldable_spares(u) -> list[str]:
    mine_squad = u.mine
    return [k for k in mine_squad if _fieldable(
        {p: s for p, s in mine_squad.items() if p != k})]


def apply(u, *acts: Action) -> dict[str, dict[str, str]]:
    sq = {m: dict(s) for m, s in u.state.squads.items()}
    sq[u.me] = u.squad_after(*acts)
    for a in acts:
        if a.kind == "clause":
            sq.get(u.market.owner.get(a.buy), {}).pop(a.buy, None)
    return {m: phantom_topup(s) for m, s in sq.items()}


def _selftest() -> None:

    from ffcore.forecast import Bootstrap as B
    from ffcore.season import best_xi

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
    assert "dud" not in names, names
    assert "star" in names, names

    acts = u.candidates()
    assert not any(a.buy == "th_m1" for a in acts), "a rival's player is not for sale"
    open_th = replace(u, market=replace(u.market, clause={"th_m1": 5e6}))
    took = [a for a in open_th.candidates() if a.buy == "th_m1"]
    assert took and all(a.kind == "clause" and a.cost == 5e6 for a in took), \
        "...unless his clause can be paid: then at the clause, at once"
    dear = replace(u, market=replace(u.market, premium=1.25))
    assert next(a for a in dear.candidates() if a.buy == "star").cost == 12.5e6, \
        "an auction costs the bid it takes to win"
    after = apply(open_th, took[0])
    assert "th_m1" in after["me"] and "th_m1" not in after["riv"], \
        "a clause moves him out of the rival's squad too"
    sells = {a.sell for a in acts if a.kind == "sell"}
    assert ("me_bench",) in sells and ("me_k",) not in sells, \
        "a spare can be sold; your only keeper cannot"


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
    assert any(r.action.buy == "dear" for r in u3.rank(acts3).rows), \
        "and ranked though you cannot pay for it yet"
    got = board(u3).plan
    assert {r.action.buy for r in got} - {""} == {"dear"}, got
    assert sorted(k for r in got for k in r.action.sell) == [
        "me_bench", "me_spare2", "me_spare3"], ("no one spare pays for him; three together do", got)

    af = apply(u, Action("buy", buy="star", sell=("me_bench",)))
    assert "me_bench" not in af["me"] and "star" in af["me"]

    rows, base = u.rank(acts)
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
    vrows, _vb = uvor.rank([Action("buy", buy="thin_del", cost=5e6),
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
    bxi = set(best_xi(bsq, bexp))
    assert bxi == set(bsq), bxi
    assert min(bexp[k] for k in bxi) == 1.0
    assert min(bexp[k] for k in bxi if bsq[k] == "MED") == 4.0
    bsq2 = {**bsq, "cand": "MED"}
    bxi2 = set(best_xi(bsq2, bexp))
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
    assert not any(a.buy == "dud"
                   for a in half.candidates())

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
    acts_cd = u_cd.candidates()
    assert not any("me_k" in a.sell for a in acts_cd), "never your only keeper"
    assert sorted(r.action.label() for r in board(u_cd).plan) == ["buy target", "sell me_f3"], \
        "with no cash, he is paid for by the one spare that raises it"


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
    spares = fieldable_spares(u)
    for s in spares:
        assert _fieldable({p: pos for p, pos in mine.items() if p != s}), s
    assert "k" not in spares and "f1" in spares and "dead_f" in spares, spares
    bare = Universe(
        state=LeagueState({"me": {"k": "POR", "d1": "DEF", "d2": "DEF",
                                  "d3": "DEF", "m1": "MED", "m2": "MED",
                                  "m3": "MED", "f1": "DEL"}}, [1], "me"),
        forecaster=Bootstrap({1: {}}))
    assert fieldable_spares(bare) == []
    after = apply(u, Action("buy", buy="new_por", sell=("spare_d",)),
                  Action("sell", sell=("dead_f",)))
    assert "dead_f" not in after["me"], after["me"]
    assert "spare_d" not in after["me"], after["me"]
    assert after["me"]["new_por"] == "MED", after["me"]

    sure = Move(Action("buy", buy="s"), 5.0, p_better=0.8)
    coin = Move(Action("buy", buy="c"), 9.0, p_better=0.55)
    loss = Move(Action("buy", buy="l"), -1.0, p_better=0.9)
    assert verdict(sure) is None
    assert verdict(coin) == "better off in 55% of seasons, under 70%", \
        "a bigger median that is a coin flip is not worth doing"
    assert verdict(loss) == "no gain in the median (-1.0 points)"

    assert [f.__name__ for f in FUNNEL] == ["candidates", "rank", "verdict", "plan"]

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
    got = [r.action.label() for r in board(owing).plan]
    assert got == [Action("sell", sell=("idle",)).label()], \
        ("a debt is raised by the sale that costs fewest points a million", got)
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
    rows = star.rank(star.candidates()).rows
    sale, ace = (next(r for r in rows if r.action.label() == x) for x in ("sell star", "buy ace"))
    assert raised(star, [sale], rows) == [sale]
    assert raised(star, [sale, sale], rows) is None, "a player is in one move at most"
    assert raised(star, [ace], rows) == [ace, sale], "what a buy leaves below zero, sales raise"
    assert blocked(star, [sale], sale.action, rows) == "star is in sell star"
    assert all(r.action.buy for r in board(star).others), "backups are buys"

    print("decide self-test OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
