
from __future__ import annotations

import sys
from statistics import median
from dataclasses import dataclass, field
from functools import cache, cached_property
from types import MappingProxyType
from typing import Mapping


from ffcore.forecast import Bootstrap
from stats import percentile
from ffcore.schedule import expectations, phantom_fill, phantom_topup
from ffcore.pricing import auction_ratios, burn, cash_price, steps, trend
from ffcore.action import Action
from ffcore.league import League
from ffcore.score import SLOT, Scorer, build, replacement, squad_pool, vor
from ffcore.season import (LeagueState, best_xi,
                           simulate_many)
from ffcore.tidy import (DECISIONS, LINEUP_SOURCE, age_hours, current, history,
                         load_deadline, load_players, market_routes, pending,
                         read_csv, run_now, scored)
from ffcore.parse import num, text

__all__ = ["Action", "Universe"]

APP_FRESH_HOURS = 14.4
SCREEN_TRIALS = 250
FINAL_TRIALS = 3000
KEEP = 12


@dataclass
class Universe:
    state: LeagueState
    forecaster: Bootstrap
    cash: float
    me: str
    facts: dict[str, dict] = field(default_factory=dict)
    rival_cash: dict[str, float] = field(default_factory=dict)
    part_played: dict[int, set[str]] = field(default_factory=dict)
    first_jornada_of: dict[str, int] = field(default_factory=dict)
    locked_cash: float = 0.0
    received_offers: dict[str, float] = field(default_factory=dict)
    lam: float | None = None
    premium: float = 1.0
    lg: League | None = None
    sc: Scorer | None = None

    @cached_property
    def next_up(self) -> dict[str, tuple[float, float]]:
        per_j = self.forecaster.per_jornada
        if not self.first_jornada_of:
            j = next((j for j in self.state.jornadas
                      if j not in self.part_played),
                     self.state.jornadas[0] if self.state.jornadas else 0)
            return dict(per_j.get(j, {}))
        return {k: per_j[j][k] for k, j in self.first_jornada_of.items()
                if k in per_j.get(j, {})}

    @cached_property
    def current_xi(self) -> tuple[dict[str, float], set[str]]:
        exp = {k: pts * p for k, (pts, p) in self.next_up.items()}
        return exp, set(best_xi(self.state.squads.get(self.me, {}), exp))

    @cached_property
    def season(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for j in self.state.jornadas:
            for k, pts in self.forecaster.expected(j).items():
                out[k] = out.get(k, 0.0) + pts
        return out

    @cached_property
    def xi_bar(self) -> float:
        _exp, xi = self.current_xi
        return min((self.season.get(k, 0.0) for k in xi), default=0.0)

    def route_kind(self, k: str) -> str:
        if k in self.state.squads.get(self.me, {}):
            return "mine"
        owner = self.view("owner").get(k)
        return "free" if not owner or owner == self.me else "listed"

    def cash_pts(self, a: "Action", lam: float | None = None) -> float:
        lam = self.lam if lam is None else lam
        if not lam:
            return 0.0
        value, trend = self.view("value"), self.view("trend")
        proceeds = self.view("proceeds")
        money = -(burn(self, a) or 0.0)
        if a.buy:
            money += value.get(a.buy, 0.0) * trend.get(a.buy, 0.0) / 100
        for k in a.sell:
            v = value.get(k, proceeds.get(k, 0.0))
            money += proceeds.get(k, 0.0) - v * (1 + trend.get(k, 0.0) / 100)
        return lam * money / 1e6

    def candidates(self, budget: float | None = None) -> list["Action"]:
        cash = self.cash if budget is None else budget
        mine = set(self.state.squads.get(self.me, {}))
        par_of = self.par

        spare = sorted(fieldable_spares(self), key=lambda k: _nulls_last(
            value_rate(par_of.get(k, 0.0), self.view("proceeds").get(k, 0.0))))

        out: list[Action] = []
        for c, price in sorted(self.view("price").items(), key=lambda kv: kv[1]):
            if c in mine or self.route_kind(c) == "listed":
                continue
            if self.season.get(c, 0.0) <= self.xi_bar and self.cash_pts(
                    Action("buy", buy=c, cost=price)) <= 0:
                continue
            if price <= cash:
                out.append(Action("buy", buy=c, cost=price))
            for s in spare:
                got = self.view("proceeds").get(s, 0.0)
                if price <= cash + got:
                    out.append(Action("swap", buy=c, sell=s, cost=price,
                                      proceeds=got))
        return out

    @cached_property
    def par(self) -> dict[str, float]:
        season = self.season
        pos = self.view("pos")
        repl = replacement(squad_pool(
            {"key": k, "slot": pos.get(k, ""), "score": v}
            for k, v in season.items() if pos.get(k)),
            len(self.state.squads)) if self.state.squads else {}
        return {k: vor({"slot": pos.get(k), "score": v}, repl)
                for k, v in season.items()}

    def rank(self, acts: list["Action"], seed: int = 1,
             extra: list[tuple[str, "Action"]] = ()) -> tuple:
        screen = score_many(self, [self.state.squads]
                             + [apply(self, a) for a in acts],
                             SCREEN_TRIALS, seed)
        base_s, rest = screen[0], screen[1:]
        screened, reach = [], []
        for a, r in zip(acts, rest):
            d, _lo, _hi = band(paired(r, base_s, self.me))
            reach.append((a.cost - a.proceeds - self.cash, d))
        measured = cash_price(reach)
        lam = self.lam if self.lam is not None else (measured or 0.0)
        for a, r in zip(acts, rest):
            if a.cost <= self.cash + a.proceeds:
                d, _lo, _hi = band(paired(r, base_s, self.me))
                screened.append((d + self.cash_pts(a, lam), a))

        _, cur_xi = self.current_xi
        pick: dict[str, tuple] = {}
        for d, a in screened:
            k = a.buy or a.sell
            cur = pick.get(k)
            key = (not cur_xi.intersection(a.sell), d, -a.net)
            if cur is None or key > (not cur_xi.intersection(cur[1].sell),
                                     cur[0], -cur[1].net):
                pick[k] = (d, a)
        screened = sorted(pick.values(), key=lambda t: (-t[0], t[1].net))

        keep = [a for _, a in screened[:KEEP]]
        afters = [apply(self, a) for a in keep]
        answered = {a.buy for a in keep if a.buy}
        rest = [(k, a) for k, a in extra if k not in answered]
        final = score_many(self, [self.state.squads] + afters
                            + [apply(self, a) for _k, a in rest], FINAL_TRIALS, seed)
        base, scored = final[0], final[1:len(afters) + 1]
        bands = {k: (*band(pairs), a, sum(pairs) / len(pairs) if pairs else 0.0)
                for (k, a), pairs in ((ka, paired(r, base, self.me)) for ka, r in
                                      zip(rest, final[len(afters) + 1:]))}
        out = []
        for a, r in zip(keep, scored):
            cash = self.cash_pts(a, lam)
            pairs = paired(r, base, self.me)
            d_pts, lo, hi = band(pairs)
            out.append({
                "action": a,
                "d_pts": d_pts,
                "pts_lo": lo,
                "pts_hi": hi,
                "net_pts": d_pts + cash,
                "burn": burn(self, a),
                "cash_pts": cash,
                "d_win": r.position().get(1, 0.0) - base.position().get(1, 0.0),
                "mean": r.mean(self.me),
            })
        rows = sorted(out, key=lambda d: (-d["net_pts"], d["action"].net))
        return rows, base, measured, bands

    def view(self, field: str) -> Mapping:
        return MappingProxyType(self.facts.get(field, {}))


def _nulls_last(v: float | None) -> tuple[bool, float]:
    return v is None, v or 0.0


def _pos_of(raw: str) -> str:
    mapped = SLOT.get(raw.lower())
    if mapped:
        return mapped
    return raw if raw in ("POR", "DEF", "MED", "DEL") else "MED"


def _fieldable(squad: dict[str, str]) -> bool:
    from ffcore.score import FREE_FORMATIONS
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


def band(pairs) -> tuple[float, float, float]:
    if not pairs:
        return (0.0, 0.0, 0.0)
    return (percentile(pairs, 50), percentile(pairs, 10),
            percentile(pairs, 90))


def value_rate(pts, cost) -> float | None:
    if pts is None or cost is None or cost <= 0:
        return None
    return pts / (cost / 1e6)


def fieldable_spares(u) -> list[str]:
    mine_squad = u.state.squads.get(u.me, {})
    return [k for k in mine_squad if _fieldable(
        {p: s for p, s in mine_squad.items() if p != k})]


def apply(u, *acts: Action) -> dict[str, dict[str, str]]:
    sq = {m: dict(s) for m, s in u.state.squads.items()}
    for a in acts:
        for gone in a.sell:
            sq[u.me].pop(gone, None)
        if a.buy:
            sq[u.me][a.buy] = u.view("pos").get(a.buy, "MED")
    return {m: phantom_topup(s) for m, s in sq.items()}


def worth_doing(u, rows) -> list:
    return [r for r in rows if r["net_pts"] > 0]


PRICE_LOG = "cash_price_log.csv"
PRICE_WINDOW = 50


def cash_price_history() -> float | None:
    seen = [x for r in read_csv(DECISIONS / PRICE_LOG)
            if (x := num(r, "places_per_million")) is not None]
    return median(seen[-PRICE_WINDOW:]) if seen else None


def _updates_to_lock() -> int:
    deadline = load_deadline()
    hours = (deadline - run_now()).total_seconds() / 3600 if deadline else 24.0
    return max(1, round(hours / 24))


def _premium() -> float:
    paid = auction_ratios(history("api_market"), sorted(
        (a for a in current("api_activity") if a["kind"] == "buy"),
        key=lambda a: a["at"]))
    return median(paid[-PRICE_WINDOW:]) if paid else 1.0


@cache
def load() -> Universe:
    age = age_hours("api_teams")
    if age is None or age > APP_FRESH_HOURS:
        raise SystemExit("the app's squads are %s old; no board is built from "
                         "them" % ("unknown" if age is None else "%.0fh" % age))
    lg = League.load()
    sc = build(current("market"), current("lineups", LINEUP_SOURCE), run_now())
    me = lg.cfg.me
    players = load_players()
    m = current("matches")

    teams, mkt = ([dict(r, key=lg.xw.player(app_id=text(r, "player_id")))
                   for r in current(name)] for name in ("api_teams", "api_market"))
    price, route = market_routes(mkt)
    pt_to_key = {r["player_team_id"]: r["key"] for r in teams
                 if r["key"] and r.get("player_team_id")}

    value = {k: rec["value"] for k, rec in players.items() if rec.get("value")}
    received_offers = pending(
        [dict(r, key=pt_to_key.get(r.get("player_team_id") or ""))
         for r in current("api_offers")], "status", "money")
    proceeds = {k: max(value.get(k, 0.0), received_offers.get(k, 0.0))
                for k in lg.squad(me)}
    pos = {k: _pos_of((rec.get("pos") or "").upper())
           for k, rec in players.items()}
    squads = {mgr: {k: pos[k] for k in lg.squad(mgr) if k in pos}
              for mgr in lg.managers}
    per_j, first_jornada_of, rates, rem, played = expectations(
        sc, set(price).union(*squads.values()), m)
    facts = {"name": {k: rec.get("name") or k for k, rec in players.items()},
             "pos": pos,
             "price": {k: v for k, v in price.items() if k in players},
             "route": {k: v for k, v in route.items() if k in players},
             "owner": {k: v for k, v in lg.owner.items() if k in players},
             "value": {k: v for k, v in value.items() if k in players},
             "proceeds": {k: v for k, v in proceeds.items() if k in players},
             "pj": {k: r.pj for k, r in rates.items() if r},
             "trend": trend(steps(history("market")), _updates_to_lock())}
    squads, per_j = phantom_fill(squads, per_j, pos)
    assert all(_fieldable(sq) for sq in squads.values()), squads
    if rem:
        first_jornada_of.update({k: rem[0] for layer in per_j.values()
                                 for k in layer if k.startswith("__phantom_")})

    fc = Bootstrap(per_j, pool=[s.pts for s in scored() if s.games == 1])

    carried = {r["manager"]: num(r, "team_points", default=0.0)
               for r in lg.standings if r.get("manager")}
    return Universe(
        state=LeagueState(squads, rem, me, carried), forecaster=fc,
        cash=lg.cash[me], me=me, facts=facts, lg=lg, sc=sc,
        rival_cash={h: v for h, v in lg.cash.items() if h != me},
        part_played=played, first_jornada_of=first_jornada_of,
        locked_cash=sum(pending(mkt, "bid_status", "bid_money").values()),
        received_offers=received_offers, lam=cash_price_history(),
        premium=_premium(),
        )


def _selftest() -> None:
    from ffcore.forecast import Bootstrap as B

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
        forecaster=B(per), cash=12e6, me="me",
        facts=dict(
            pos={**{k: v for k, v in mine.items()},
                **{k: v for k, v in theirs.items()},
                "star": "MED", "dud": "MED"},
            price={"star": 10e6, "dud": 1e6, "th_m1": 5e6},
            proceeds={"me_bench": 8e6}, owner={"th_m1": "riv"}))

    first = u.current_xi
    assert u.current_xi is first, "cached_property must not recompute"
    assert u.xi_bar is u.xi_bar, "cached_property must not recompute"

    cxi_exp, cxi = u.current_xi
    fallback_j = next((j for j in u.state.jornadas if j not in u.part_played),
                      u.state.jornadas[0] if u.state.jornadas else 0)
    assert cxi_exp == u.forecaster.expected(fallback_j), cxi_exp
    assert "me_bench" not in cxi, cxi
    assert len(cxi) == 11, cxi
    bar = u.xi_bar
    assert bar == min(cxi_exp.get(k, 0.0) for k in cxi), bar
    assert bar > 0.5, bar

    acts = u.candidates()
    names = {a.buy for a in acts}
    assert "dud" not in names, names
    assert "star" in names, names

    acts = u.candidates()
    assert not any(a.buy == "th_m1" for a in acts), "a rival's player is not for sale"
    assert all(a.cost <= u.cash + a.proceeds for a in acts), acts


    u3 = Universe(
        state=LeagueState({"me": dict(mine), "riv": dict(theirs)}, [1], "me"),
        forecaster=B(per), cash=4e6, me="me",
        facts=dict(
            pos={**u.view("pos"), "dear": "MED"},
            price={"dear": 20e6},
            proceeds={"me_bench": 8e6, "me_spare2": 5e6, "me_spare3": 4e6}))
    u3.state.squads["me"]["me_spare2"] = "MED"
    u3.state.squads["me"]["me_spare3"] = "POR"
    per3 = {1: dict(per[1])}
    per3[1].update({"dear": (11.0, 1.0), "me_spare2": (0.4, 1.0),
                    "me_spare3": (0.3, 1.0)})
    u3.forecaster = B(per3)
    acts3 = u3.candidates()
    assert not any(a.buy == "dear" and len(a.sell) > 1 for a in acts3), \
        [a for a in acts3 if a.buy == "dear"]
    assert not any(a.buy == "dear" for a in acts3), acts3

    sw = next(x for x in acts if x.buy == "star" and x.sell == ("me_bench",))
    af = apply(u, sw)
    assert "me_bench" not in af["me"] and "star" in af["me"]

    rows, base, _lam, _b = u.rank(acts)
    assert rows, "something should be worth doing"
    top = rows[0]
    assert top["d_pts"] > 0, top["d_pts"]
    assert top["pts_lo"] <= top["d_pts"] <= top["pts_hi"]
    assert top["net_pts"] > 0, top
    assert [r["net_pts"] for r in rows] == sorted(
        (r["net_pts"] for r in rows), reverse=True)

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
        forecaster=B(vper), cash=6e6, me="me",
        facts=dict(
            pos={**vsq, **vth, "thin_del": "DEL", "deep_med": "MED"},
            price={"thin_del": 5e6, "deep_med": 5e6},
            route={"thin_del": "free", "deep_med": "free"}))
    vexp, vxi = uvor.current_xi
    assert uvor.xi_bar == 1.0, uvor.xi_bar
    assert min(vexp[k] for k in vxi if uvor.view("pos")[k] == "DEL") == 1.0, vxi
    assert min(vexp[k] for k in vxi if uvor.view("pos")[k] == "MED") == 5.0, vxi
    vrows, _vb, _vl, _vbd = uvor.rank([Action("buy", buy="thin_del", cost=5e6),
               Action("buy", buy="deep_med", cost=5e6)])
    vby = {r["action"].buy: r for r in vrows}
    assert vby["thin_del"]["action"].net == vby["deep_med"]["action"].net
    assert vby["thin_del"]["d_pts"] > 2 * vby["deep_med"]["d_pts"] > 0, vby

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

    per6 = {1: dict(per[1])}
    acts6 = []
    for i in range(15):
        key = "big%d" % i
        per6[1][key] = (40.0 - 2 * i, 1.0)
        acts6.append(Action("buy", buy=key, cost=20e6))
    per6[1]["sham"] = (0.1, 1.0)
    acts6.append(Action("buy", buy="sham", cost=1e3))
    u6 = Universe(
        state=LeagueState({"me": dict(mine), "riv": dict(theirs)}, [1], "me"),
        forecaster=B(per6), cash=1000e6, me="me",
        facts=dict(pos={**u.view("pos"), **{a.buy: "MED" for a in acts6}},
                   price={a.buy: a.cost for a in acts6}))
    rows6, *_ = u6.rank(acts6)
    kept6 = [r["action"].buy for r in rows6]
    assert len(kept6) == KEEP and "sham" not in kept6, kept6
    assert set(kept6) <= {"big%d" % i for i in range(KEEP + 1)}, kept6

    half = Universe(
        state=LeagueState({"me": dict(mine), "riv": dict(theirs)}, [1, 2],
                          "me", ),
        forecaster=B({1: {"me_k": (0.1, 1.0), "dud": (1.0, 1.0)},
                      2: {**{k: (5.0, 1.0) for k in mine}, "dud": (1.0, 1.0)}}),
        cash=50e6, me="me",
        facts=dict(pos={**u.view("pos"), "dud": "MED"},
                                  price={"dud": 1e6}))
    half.part_played = {1: {"somewhere"}}
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
        forecaster=BCD(per_cd), cash=0.0, me="me",
        facts=dict(pos={**sq_cd, "target": "DEL"},
                                  price={"target": 5e6},
                                  proceeds={"me_f3": 5e6}))
    acts_cd = u_cd.candidates()
    assert any(a.buy == "target" and a.sell == ("me_f3",) for a in acts_cd), \
        acts_cd
    assert not any(k in a.sell for a in acts_cd
                  for k in ("me_k", "me_d1", "me_d2", "me_d3", "me_d4",
                           "me_m1", "me_m2", "me_m3", "me_m4")), \
        [a for a in acts_cd if a.sell]


    sq = {"k": "POR", "d1": "DEF", "d2": "DEF", "d3": "DEF", "d4": "DEF",
         "spare_d": "DEF", "m1": "MED", "m2": "MED", "m3": "MED", "m4": "MED",
         "f1": "DEL", "dead_f": "DEL"}
    per = {1: {k: (3.0, 1.0) for k in sq}}
    per[1]["f1"] = (10.0, 1.0)
    per[1]["dead_f"] = (0.1, 1.0)
    u = Universe(
        state=LeagueState({"me": dict(sq)}, [1], "me"),
        forecaster=Bootstrap(per), cash=0.0, me="me",
        facts=dict(pos=dict(sq),
                                  proceeds={"spare_d": 4e6, "dead_f": 6e6}),
        received_offers={})
    mine = u.state.squads["me"]
    spares = fieldable_spares(u)
    for s in spares:
        assert _fieldable({p: pos for p, pos in mine.items() if p != s}), s
    assert "k" not in spares and "f1" in spares and "dead_f" in spares, spares
    bare = Universe(
        state=LeagueState({"me": {"k": "POR", "d1": "DEF", "d2": "DEF",
                                  "d3": "DEF", "m1": "MED", "m2": "MED",
                                  "m3": "MED", "f1": "DEL"}}, [1], "me"),
        forecaster=Bootstrap({1: {}}), cash=0.0, me="me")
    assert fieldable_spares(bare) == []
    after = apply(u, Action("buy", buy="new_por", sell=("spare_d",)),
                  Action("sell", sell=("dead_f",)))
    assert "dead_f" not in after["me"], after["me"]
    assert "spare_d" not in after["me"], after["me"]
    assert after["me"]["new_por"] == "MED", after["me"]

    for pts, cost, want in [(120.0, 14.13e6, 120.0 / 14.13), (120.0, 0.0, None),
                            (120.0, -5e6, None), (None, 5e6, None),
                            (0.0, 5e6, 0.0)]:
        got = value_rate(pts, cost)
        assert got == want or abs(got - want) < 1e-9, (pts, cost, got)

    pf_per = {j: {"me_a": (2.0, 1.0), "me_b": (5.0, 1.0), "cand": (4.0, 1.0)}
              for j in (1, 2)}
    par = Universe(
        state=LeagueState({"me": {"me_a": "MED", "me_b": "MED"}}, [1, 2], "me"),
        forecaster=Bootstrap(pf_per), cash=0.0, me="me",
        facts={"pos": dict.fromkeys(("me_a", "me_b", "cand"), "MED")}).par
    assert par == {"me_a": 0.0, "me_b": 6.0, "cand": 4.0}, par

    from ffcore.fixtures import tiny_market_universe
    mu = tiny_market_universe(lam=0.3, premium=1.05)
    mu.facts.update(value={"bench_m": 3e6, "riser": 2e6},
                    trend={"riser": 20.0, "bench_m": -10.0},
                    price={**mu.facts["price"], "riser": 2e6},
                    pos={**mu.facts["pos"], "riser": "MED"})
    buy_riser = Action("buy", buy="riser", cost=2e6)
    assert abs(mu.cash_pts(buy_riser) - 0.3 * (0.4 - 0.1)) < 1e-9
    assert "riser" in {a.buy for a in mu.candidates()}
    sell_m = Action("sell", sell=("bench_m",), proceeds=3e6)
    assert abs(mu.cash_pts(sell_m) - 0.3 * 0.3) < 1e-9
    assert mu.cash_pts(buy_riser, lam=0.0) == 0.0
    mu.facts["trend"] = {"riser": 0.0}
    assert mu.cash_pts(buy_riser) < 0
    assert "riser" not in {a.buy for a in mu.candidates()}

    print("decide self-test OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
