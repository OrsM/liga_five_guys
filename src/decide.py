from __future__ import annotations

import sys
from dataclasses import dataclass, field, replace
from functools import cached_property
from typing import NamedTuple

from ffcore.action import Action
from ffcore.forecast import Bootstrap
from ffcore.market import Market
from ffcore.outlook import Outlook
from ffcore.pricing import cash_price
from ffcore.render import title_name
from ffcore.rules import FREE_FORMATIONS
from ffcore.schedule import phantom_topup
from ffcore.season import LeagueState, Standings, simulate_many
from stats import percentile

__all__ = ["Action", "Board", "CONFIDENCE", "FUNNEL", "Move", "Universe", "at_risk",
           "board", "plan", "verdict"]

SCREEN_TRIALS = 250
FINAL_TRIALS = 3000
CONFIDENCE = 0.7

@dataclass(frozen=True)
class Move:
    """One action, scored against doing nothing: its median change in season
    points, the cash it frees or spends priced in points, and the share of
    simulated seasons in which it leaves you better off, cash included."""
    action: Action
    d_pts: float
    cash_pts: float = 0.0
    p_better: float = 1.0

    @property
    def net_pts(self) -> float:
        return self.d_pts + self.cash_pts


class Ranking(NamedTuple):
    rows: list[Move]
    base: Standings
    measured: float | None


class Board(NamedTuple):
    """The recommendation: the moves to make and what they gain together,
    every move ranked, and the season if you do nothing."""
    plan: list[Move]
    gain: float
    rows: list[Move]
    base: Standings
    measured: float | None

    @property
    def others(self) -> list[Move]:
        """Other players worth getting, best way first, should a bid fail."""
        seen = {p.action.buy for p in self.plan}
        out = []
        for r in self.rows:
            if r.action.buy not in seen and verdict(r) is None:
                seen.add(r.action.buy)
                out.append(r)
        return out


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

    def offer(self, k: str) -> Action | None:
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

    def worth_a_look(self, a: Action) -> bool:
        """He would beat your weakest starter over the season, or his value
        is expected to rise by more than the premium."""
        return (self.outlook.season.get(a.buy, 0.0) > self.outlook.xi_bar
                or self.market.cash_pts(a) > 0)

    def candidates(self) -> list[Action]:
        """Every move you could make. Sell a spare, get a player, or get
        one selling a spare to pay for him; affordable or not."""
        m = self.market
        spares = [(s, m.proceeds.get(s, 0.0)) for s in fieldable_spares(self)]
        out = [Action("sell", sell=(s, ), proceeds=got) for s, got in spares]
        gets = [a for k in sorted(m.price.keys() | m.clause.keys())
                if (a := self.offer(k)) and self.worth_a_look(a)]
        for a in sorted(gets, key=lambda a: a.cost):
            out.append(a)
            out += [replace(a, sell=(s, ), proceeds=got) for s, got in spares]
        return out

    def rank(self, acts: list[Action], seed: int = 1) -> Ranking:
        """Each move scored against doing nothing. In season points, cash
        priced in. A quick screen picks, for each player, the spare whose
        sale best pays for him; then he is scored in full both ways, from
        your cash and with that sale. The unaffordable ones only measure
        what cash is worth."""
        m = self.market
        screen = score_many(self, [self.state.squads]
                            + [apply(self, a) for a in acts], SCREEN_TRIALS, seed)
        gains = [median_gain(paired(r, screen[0], self.me)) for r in screen[1:]]
        measured = cash_price([(a.net - m.cash, d) for a, d in zip(acts, gains)])
        lam = m.lam if m.lam is not None else (measured or 0.0)
        best: dict = {}
        for a, d in zip(acts, gains):
            if a.net > m.cash:
                continue
            key, way = (d + m.cash_pts(a, lam), -a.net), (a.buy or a.sell, bool(a.buy and a.sell))
            if way not in best or key > best[way][0]:
                best[way] = (key, a)
        keep = [a for _key, a in best.values()]
        final = score_many(self, [self.state.squads]
                           + [apply(self, a) for a in keep], FINAL_TRIALS, seed)
        rows = sorted((self._move(a, paired(r, final[0], self.me), lam)
                       for a, r in zip(keep, final[1:])),
                      key=lambda mv: (-mv.net_pts, mv.action.net))
        return Ranking(rows, final[0], measured)

    def _move(self, a: Action, pairs: list[float], lam) -> Move:
        cash = self.market.cash_pts(a, lam)
        better = sum(1 for x in pairs if x + cash > 0) / len(pairs) if pairs else 0.0
        return Move(a, median_gain(pairs), cash, better)


def verdict(mv: Move) -> str | None:
    """Worth doing only if it gains and is likely to help. It must gain in
    the median, cash included, and leave you better off in at least
    CONFIDENCE of simulated seasons; below that its gain is noise. Returns
    why not, or None."""
    if mv.net_pts <= 0:
        return "no gain in the median (%+.1f points)" % mv.net_pts
    if mv.p_better < CONFIDENCE:
        return "better off in %.0f%% of seasons, under %.0f%%" % (
            100 * mv.p_better, 100 * CONFIDENCE)
    return None


def plan(u, good: list[Move], base: Standings) -> tuple[list[Move], float]:
    """The best set of moves to make together. Best first, each taken if
    it shares no player with those already taken, is paid for by the cash
    left, and adds to their joint gain; again until none is taken, so a
    sale taken late can still pay for a buy. Every move left out was
    last tried against the whole plan."""
    picked: list[Move] = []
    cash, gain, grew = u.market.cash, 0.0, True
    while grew:
        grew = False
        for r in sorted(good, key=lambda r: -r.net_pts):
            if r in picked or blocked(u, picked, r.action, cash):
                continue
            total = joint_gain(u, [*picked, r], base)
            if total > gain:
                picked.append(r)
                cash, gain, grew = cash - r.action.net, total, True
    return picked, gain


def joint_gain(u, moves: list[Move], base: Standings) -> float:
    acts = [mv.action for mv in moves]
    after = score_many(u, [apply(u, *acts)], FINAL_TRIALS, 1)[0]
    return median_gain(paired(after, base, u.me)) + sum(
        u.market.cash_pts(a) for a in acts)


def blocked(u, picked: list[Move], a: Action, cash: float) -> str | None:
    """Why a move cannot join these: it shares a player with one of them,
    or the cash they leave does not pay for it. None if it can."""
    names = {k: title_name(n) for k, n in u.market.name.items()}
    for p in picked:
        if shared := set(p.action.players) & set(a.players):
            return "%s is in %s" % (" + ".join(names.get(k, k) for k in shared),
                                    p.action.label(names))
    if a.net > cash:
        return "it needs %.1fM and %.1fM is left" % (a.net / 1e6, cash / 1e6)
    return None


# The funnel, in order: every recommendation passes these steps, the
# diagram draws them (tools/uml.py) and tools/ask.py names the one a
# player stopped at.
FUNNEL = (Universe.candidates, Universe.rank, verdict, plan)


def board(u) -> Board:
    """The funnel end to end: what the report shows and ask.py explains."""
    rows, base, measured = u.rank(u.candidates())
    good = [r for r in rows if verdict(r) is None]
    picked, gain = plan(u, good, base)
    return Board(picked, gain, rows, base, measured)


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
    return simulate_many(
        [LeagueState(squads=sq, jornadas=u.state.jornadas, me=u.me,
                     carried=u.state.carried) for sq in many],
        u.forecaster, trials=trials, seed=seed)


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
    for a in acts:
        for gone in a.sell:
            sq[u.me].pop(gone, None)
        if a.kind == "clause":
            sq.get(u.market.owner.get(a.buy), {}).pop(a.buy, None)
        if a.buy:
            sq[u.me][a.buy] = u.market.pos.get(a.buy, "MED")
    return {m: phantom_topup(s) for m, s in sq.items()}


def _selftest() -> None:
    from dataclasses import replace

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
            proceeds={"me_bench": 8e6}, owner={"th_m1": "riv"}))

    first = u.outlook.xi
    assert u.outlook.xi is first, "cached_property must not recompute"
    assert u.outlook.xi_bar is u.outlook.xi_bar, "cached_property must not recompute"

    cxi_exp, cxi = u.outlook.xi
    fallback_j = next((j for j in u.state.jornadas if j not in u.part_played),
                      u.state.jornadas[0] if u.state.jornadas else 0)
    assert cxi_exp == u.forecaster.expected(fallback_j), cxi_exp
    assert "me_bench" not in cxi, cxi
    assert len(cxi) == 11, cxi
    bar = u.outlook.xi_bar
    assert bar == min(cxi_exp.get(k, 0.0) for k in cxi), bar
    assert bar > 0.5, bar

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
    per3[1].update({"dear": (11.0, 1.0), "me_spare2": (0.4, 1.0),
                    "me_spare3": (0.3, 1.0)})
    u3 = Universe(
        state=LeagueState({"me": {**mine, "me_spare2": "MED", "me_spare3": "POR"},
                           "riv": dict(theirs)}, [1], "me"),
        forecaster=B(per3),
        market=Market(
            cash=4e6, pos={**u.market.pos, "dear": "MED"},
            price={"dear": 20e6},
            proceeds={"me_bench": 8e6, "me_spare2": 5e6, "me_spare3": 4e6}))
    acts3 = u3.candidates()
    assert not any(len(a.sell) > 1 for a in acts3), "one spare funds a buy"
    assert any(a.buy == "dear" for a in acts3), "unaffordable ones are offered..."
    assert not any(r.action.buy == "dear" for r in u3.rank(acts3).rows), \
        "...to measure what cash is worth, and never ranked"

    sw = next(x for x in acts if x.buy == "star" and x.sell == ("me_bench",))
    af = apply(u, sw)
    assert "me_bench" not in af["me"] and "star" in af["me"]

    rows, base, _lam = u.rank(acts)
    assert rows, "something should be worth doing"
    assert all(r.action.net <= u.market.cash for r in rows), rows
    ways = [(r.action.buy or r.action.sell, bool(r.action.buy and r.action.sell))
            for r in rows]
    assert len(set(ways)) == len(ways) and ("star", False) in ways, \
        "each player at most twice: from your cash, and with his best sale"
    top = rows[0]
    assert top.d_pts > 0, top.d_pts
    assert top.net_pts > 0, top
    assert [r.net_pts for r in rows] == sorted(
        (r.net_pts for r in rows), reverse=True)

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
    assert uvor.outlook.xi_bar == 1.0, uvor.outlook.xi_bar
    assert min(vexp[k] for k in vxi if uvor.market.pos[k] == "DEL") == 1.0, vxi
    assert min(vexp[k] for k in vxi if uvor.market.pos[k] == "MED") == 5.0, vxi
    vrows, _vb, _vl = uvor.rank([Action("buy", buy="thin_del", cost=5e6),
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
                "target": (9.0, 1.0)}}
    sq_cd = {"me_k": "POR", "me_d1": "DEF", "me_d2": "DEF", "me_d3": "DEF",
           "me_d4": "DEF", "me_m1": "MED", "me_m2": "MED", "me_m3": "MED",
           "me_m4": "MED", "me_f1": "DEL", "me_f2": "DEL", "me_f3": "DEL"}
    from ffcore.forecast import Bootstrap as BCD
    u_cd = Universe(
        state=LeagueState({"me": dict(sq_cd)}, [1], "me"),
        forecaster=BCD(per_cd),
        market=Market(pos={**sq_cd, "target": "DEL"}, price={"target": 5e6},
                      proceeds={"me_f3": 5e6}))
    acts_cd = u_cd.candidates()
    assert not any("me_k" in a.sell for a in acts_cd), "never your only keeper"
    rows_cd = u_cd.rank(acts_cd).rows
    assert [r.action.sell for r in rows_cd if r.action.buy == "target"] == [("me_f3",)], \
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
                      proceeds={"spare_d": 4e6, "dead_f": 6e6}))
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

    from ffcore.fixtures import tiny_market_universe
    mu = tiny_market_universe(lam=0.3, premium=1.05)
    mu = replace(mu, market=replace(
        mu.market, value={"bench_m": 3e6, "riser": 2e6},
        trend={"riser": 20.0, "bench_m": -10.0},
        price={**mu.market.price, "riser": 2e6},
        pos={**mu.market.pos, "riser": "MED"}))
    buy_riser = mu.offer("riser")
    assert abs(mu.market.cash_pts(buy_riser) - 0.3 * (0.4 - 0.1)) < 1e-9, \
        "his value gains 0.4M; the bid to win it burns 0.1M"
    assert "riser" in {a.buy for a in mu.candidates()}
    sell_m = Action("sell", sell=("bench_m",), proceeds=3e6)
    assert abs(mu.market.cash_pts(sell_m) - 0.3 * 0.3) < 1e-9
    assert mu.market.cash_pts(buy_riser, lam=0.0) == 0.0
    mu = replace(mu, market=replace(mu.market, trend={"riser": 0.0}))
    assert mu.market.cash_pts(buy_riser) < 0
    assert [f.__name__ for f in FUNNEL] == ["candidates", "rank", "verdict", "plan"]
    assert "riser" not in {a.buy for a in mu.candidates()}

    print("decide self-test OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
