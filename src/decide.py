
from __future__ import annotations

import math
import sys
from dataclasses import dataclass, field
from functools import cache, cached_property
from types import MappingProxyType
from typing import Mapping


from ffcore.forecast import Bootstrap, pool_from_perjornada
import grading
from stats import percentile
from ffcore.schedule import (rounds_left, next_then_rest,
                             first_jornada_per_player, apply_fixtures,
                             phantom_fill, phantom_topup)
from ffcore.pricing import burn, cash_price
from ffcore.action import Action
from ffcore.profile import (PlayerProfile, UNSCORED_DEFAULT,
                            build_profiles)
from ffcore.fixture import club_volatility, season_board
from ffcore.league import League
from ffcore.score import SLOT, Scorer, build, replacement, squad_pool, vor
from ffcore.season import (LeagueState, best_xi,
                           simulate_many)
from ffcore.tidy import (LINEUP_SOURCE, age_hours, current, load_perjornada,
                         load_players, market_routes, pending, run_now)
from ffcore.parse import num, text

__all__ = ["Action", "Universe"]

APP_FRESH_HOURS = 14.4
SCREEN_TRIALS = 250
FINAL_TRIALS = 3000
KEEP = 12

KEEP_RELIABLE_MIN = 6

KEEP_VALUE_MIN = 4


@dataclass
class Universe:
    state: LeagueState
    forecaster: Bootstrap
    cash: float
    me: str
    players: dict[str, PlayerProfile] = field(default_factory=dict)
    rival_cash: dict[str, float] = field(default_factory=dict)
    part_played: dict[int, set[str]] = field(default_factory=dict)
    first_jornada_of: dict[str, int] = field(default_factory=dict)
    locked_cash: float = 0.0
    received_offers: dict[str, float] = field(default_factory=dict)
    mae: float | None = None
    lg: League | None = None
    sc: Scorer | None = None

    @cached_property
    def current_xi(self) -> tuple[dict[str, float], set[str]]:
        if self.first_jornada_of:
            exp = self.forecaster.expected_own(self.first_jornada_of)
        else:
            j = next((j for j in self.state.jornadas
                      if j not in self.part_played),
                     self.state.jornadas[0] if self.state.jornadas else 0)
            exp = self.forecaster.expected(j)
        return exp, set(best_xi(self.state.squads.get(self.me, {}), exp))

    @cached_property
    def xi_bar(self) -> float:
        exp, xi = self.current_xi
        return min((exp.get(k, 0.0) for k in xi), default=0.0)

    def route_kind(self, k: str) -> str:
        if k in self.state.squads.get(self.me, {}):
            return "mine"
        owner = self.view("owner").get(k)
        return "free" if not owner or owner == self.me else "listed"

    def dead_weight(self) -> list[tuple[str, float]]:
        mine = self.state.squads.get(self.me, {})
        choosable = [j for j in self.state.jornadas
                     if j not in self.part_played] or list(self.state.jornadas)
        starts: set[str] = set()
        for j in choosable:
            starts.update(best_xi(mine, self.forecaster.expected(j)))
        return sorted(((k, self.view("proceeds").get(k, 0.0)) for k in mine
                       if k not in starts),
                      key=lambda kv: -kv[1])

    def candidates(self, budget: float | None = None) -> list["Action"]:
        cash = self.cash if budget is None else budget
        mine = set(self.state.squads.get(self.me, {}))
        exp, _xi = self.current_xi
        par_of = {k: v["par"] for k, v in self.player_forecasts.items()}

        spare = sorted(fieldable_spares(self), key=lambda k: _nulls_last(
            value_rate(par_of.get(k, 0.0), self.view("proceeds").get(k, 0.0))))

        out: list[Action] = []
        for c, price in sorted(self.view("price").items(), key=lambda kv: kv[1]):
            if c in mine or exp.get(c, 0.0) <= self.xi_bar:
                continue
            if self.route_kind(c) == "listed":
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
    def player_forecasts(self) -> dict[str, dict]:
        jornadas = self.state.jornadas
        n_rem = len(jornadas)
        sim_season: dict[str, float] = {}
        sim_next: dict[str, float] = {}
        for i, j in enumerate(jornadas):
            exp = self.forecaster.expected(j)
            for k, pts in exp.items():
                sim_season[k] = sim_season.get(k, 0.0) + pts
            if i == 0:
                sim_next = exp

        pos = self.view("pos")
        wide_pool = squad_pool(
            {"key": k, "slot": pos.get(k, ""), "score": pts}
            for k, pts in sim_season.items() if pos.get(k))
        repl = replacement(wide_pool, len(self.state.squads)) \
            if self.state.squads else {}

        out = {}
        for k, p in self.players.items():
            in_sim = k in sim_season
            market_exp = self.view("market_exp").get(k, 0.0)
            season_pts = sim_season.get(k) if in_sim else market_exp * n_rem
            out[k] = {
                "season_pts": season_pts,
                "next_pts": sim_next.get(k) if in_sim else market_exp,
                "par": vor({"slot": pos.get(k), "score": season_pts}, repl),
                "pj": p.derived.pj,
                "simulated": in_sim,
            }
        return out

    def rank(self, acts: list["Action"], seed: int = 1, price=None,
             extra: list[tuple[str, "Action"]] = ()) -> tuple:
        screen = _score_many(self, [self.state.squads]
                             + [apply(self, a) for a in acts],
                             SCREEN_TRIALS, seed)
        base_s, rest = screen[0], screen[1:]
        screened, reach = [], []
        for a, r in zip(acts, rest):
            d, _lo, _hi = band(paired(r, base_s, self.me))
            reach.append((a.cost - a.proceeds - self.cash, d))
            if a.cost <= self.cash + a.proceeds:
                screened.append((d, a))
        measured = cash_price(reach)
        lam = price if price is not None else measured

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

        top = screened[:KEEP]
        top = _top_up(top, screened,
                     ok=lambda d, a: self.view("route").get(a.buy, "free") != "listed",
                     rank_key=lambda t: -t[0], minimum=KEEP_RELIABLE_MIN)
        best_value = {a.buy or a.sell for _, a in
                     sorted((t for t in screened if t[0] > 0 and t[1].net > 0),
                            key=_per_million)[:KEEP_VALUE_MIN]}
        top = _top_up(top, screened,
                     ok=lambda d, a: (a.buy or a.sell) in best_value,
                     rank_key=_per_million, minimum=KEEP_VALUE_MIN)
        keep = [a for _, a in top]
        afters = [apply(self, a) for a in keep]
        answered = {a.buy for a in keep if a.buy}
        rest = [(k, a) for k, a in extra if k not in answered]
        final = _score_many(self, [self.state.squads] + afters
                            + [apply(self, a) for _k, a in rest], FINAL_TRIALS, seed)
        base, scored = final[0], final[1:len(afters) + 1]
        bands = {k: (*band(pairs), a, sum(pairs) / len(pairs) if pairs else 0.0)
                for (k, a), pairs in ((ka, paired(r, base, self.me)) for ka, r in
                                      zip(rest, final[len(afters) + 1:]))}
        out = []
        for a, r in zip(keep, scored):
            b_ = burn(self, a)
            charge = 0.0 if (lam is None or b_ is None) else lam * b_ / 1e6
            pairs = paired(r, base, self.me)
            d_pts, lo, hi = band(pairs)
            out.append({
                "action": a,
                "d_pts": d_pts,
                "pts_lo": lo,
                "pts_hi": hi,
                "net_pts": d_pts - charge,
                "burn": b_,
                "charge": charge,
                "d_win": r.position().get(1, 0.0) - base.position().get(1, 0.0),
                "mean": r.mean(self.me),
                "value": value_rate(d_pts, a.net),
            })
        rows = sorted(out, key=lambda d: (-d["net_pts"], d["action"].net))
        return rows, base, measured, bands

    _FIELDS = {
        "pos": (lambda p: p.current.pos, bool, lambda v: _pos_of(v)),
        "price": (lambda p: p.current.price, None, None),
        "proceeds": (lambda p: p.current.proceeds, None, None),
        "owner": (lambda p: p.current.owner, bool, None),
        "value": (lambda p: p.current.value, None, None),
        "market_exp": (lambda p: p.derived.market_exp, None, None),
        "start": (lambda p: p.derived.start_p, None, None),
        "route": (lambda p: p.current.route, bool, None),
        "name": (lambda p: p.identity.name, lambda v: True, None),
    }

    def view(self, field: str) -> Mapping:
        cache = self.__dict__.setdefault("_view_cache", {})
        if field not in cache:
            get, keep, transform = self._FIELDS[field]
            keep = keep or (lambda v: v is not None)
            transform = transform or (lambda v: v)
            cache[field] = MappingProxyType(
                {k: transform(v) for k, p in self.players.items()
                 for v in (get(p),) if keep(v)})
        return cache[field]


def _nulls_last(v: float | None) -> tuple[bool, float]:
    return v is None, v or 0.0


def _per_million(t: tuple) -> float:
    return -t[0] / (t[1].net / 1e6)


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


def _score_many(u: Universe, many: list, trials: int, seed: int):
    return simulate_many(
        [LeagueState(squads=sq, jornadas=u.state.jornadas, me=u.me,
                     carried=u.state.carried) for sq in many],
        u.forecaster, trials=trials, seed=seed, antithetic=True)


def paired(after, base, me) -> list[float]:
    return sorted(x - y for x, y in zip(after.totals.get(me, []),
                                        base.totals.get(me, [])))


def band(pairs) -> tuple[float, float, float]:
    if not pairs:
        return (0.0, 0.0, 0.0)
    return (percentile(pairs, 50), percentile(pairs, 10),
            percentile(pairs, 90))


def _top_up(top: list[tuple], screened: list[tuple], ok, rank_key,
           minimum: int) -> list[tuple]:
    kept = {a.buy or a.sell for _, a in top}
    have = sum(1 for d, a in top if ok(d, a))
    if have >= minimum:
        return top
    more = sorted((t for t in screened
                   if (t[1].buy or t[1].sell) not in kept and ok(*t)),
                  key=rank_key)
    return top + more[:minimum - have]


def value_rate(pts, cost) -> float | None:
    if pts is None or cost is None or cost <= 0:
        return None
    return pts / (cost / 1e6)


def fieldable_spares(u) -> list[str]:
    mine_squad = u.state.squads.get(u.me, {})
    return [k for k in mine_squad if _fieldable(
        {p: s for p, s in mine_squad.items() if p != k})]


def max_spare_proceeds(u) -> float:
    return max((u.view("proceeds").get(k, 0.0) for k in fieldable_spares(u)),
              default=0.0)


def apply(u, a: Action) -> dict[str, dict[str, str]]:
    sq = {m: dict(s) for m, s in u.state.squads.items()}
    for gone in a.sell:
        sq[u.me].pop(gone, None)
    if a.buy:
        sq[u.me][a.buy] = u.view("pos").get(a.buy, "MED")
    return {m: phantom_topup(s) for m, s in sq.items()}


def _clears_par_floor(par_of: dict, mae, k: str, horizon: int = 1,
                      pj_of: dict | None = None) -> bool:
    if mae is None:
        return True
    par = par_of.get(k)
    if par is None:
        return False
    from ffcore.score import SHRINK_K

    pj = pj_of.get(k) if pj_of else None
    if pj is not None:
        par = par * pj / (pj + SHRINK_K)
    return par >= mae * math.sqrt(max(1, horizon))


def _gains(r) -> bool:
    d = r.get("d_pts")
    return d is not None and d > 0


def worth_doing(u, rows) -> list:
    par_of = {k: v["par"] for k, v in u.player_forecasts.items()}
    pj_of = {k: v["pj"] for k, v in u.player_forecasts.items()}
    mae = u.mae
    rows = [r for r in rows if not r["action"].buy
            or _clears_par_floor(par_of, mae, r["action"].buy,
                                 len(u.state.jornadas), pj_of)]
    return [r for r in rows if _gains(r)]


@cache
def load() -> Universe:
    age = age_hours("api_teams")
    if age is None or age > APP_FRESH_HOURS:
        raise SystemExit("the app's squads are %s old; no board is built from "
                         "them" % ("unknown" if age is None else "%.0fh" % age))
    lg = League.load()
    sc = build(current("market"), current("lineups", LINEUP_SOURCE), run_now(),
               shrink_k=lg.cfg.shrink_k)
    me, now = lg.cfg.me, run_now()
    players = load_players()
    m = current("matches")
    rem, played = rounds_left(m)

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
    profiles = build_profiles(
        players, sc, xw=lg.xw,
        market_keyed={k: {"listed": k in price, "price": price.get(k),
                          "owner": lg.owner.get(k), "value": value.get(k),
                          "route": route.get(k),
                          "proceeds": proceeds.get(k)} for k in players})

    pos = {k: _pos_of(p.current.pos) for k, p in profiles.items()}
    club = {k: p.current.club for k, p in profiles.items() if p.current.club}
    squads = {mgr: {k: pos[k] for k in lg.squad(mgr) if k in pos}
              for mgr in lg.managers}
    base, base_rest, matches, ppm_of, status_of = {}, {}, {}, {}, {}
    for k in set(price).union(*squads.values()):
        p = profiles.get(k)
        base[k], base_rest[k] = (p.to_bootstrap_input() if p
                                 else (UNSCORED_DEFAULT, UNSCORED_DEFAULT))
        s = p.derived.scored if p else None
        if s:
            matches[k], ppm_of[k], status_of[k] = s.pj, s.ppm, s.status

    sboard = season_board(sc.ratings, m, rem, now)
    first_jornada_of = first_jornada_per_player(base, rem, played, club)
    per_j = apply_fixtures(
        next_then_rest(base, base_rest, rem, played, club),
        sboard, club, pos, ppm_of, status_of=status_of,
        first_jornada_of=first_jornada_of, status_factor=sc.cal.status_factor)
    squads, per_j = phantom_fill(squads, per_j, pos)
    assert all(_fieldable(sq) for sq in squads.values()), squads
    if rem:
        first_jornada_of.update({k: rem[0] for layer in per_j.values()
                                 for k in layer if k.startswith("__phantom_")})

    pool = pool_from_perjornada(load_perjornada())
    history = grading.graded_history()
    fc = Bootstrap(per_j, pool=pool, matches=matches, club_of=club,
                   club_rel=club_volatility(current("results_history"),
                                            set(club.values())),
                   drift_frac=grading.drift_frac_from_history(history),
                   rate_floor=grading.fit_rate_rel_floor(pool, history))

    carried = {r["manager"]: num(r, "team_points", default=0.0)
               for r in lg.standings if r.get("manager")}
    return Universe(
        state=LeagueState(squads, rem, me, carried), forecaster=fc,
        cash=lg[me].cash.value or 0.0, me=me, players=profiles, lg=lg, sc=sc,
        rival_cash={h: lg[h].cash.value or 0.0 for h in lg.managers
                    if h != me},
        part_played=played, first_jornada_of=first_jornada_of,
        locked_cash=sum(pending(mkt, "bid_status", "bid_money").values()),
        received_offers=received_offers,
        mae=grading.current_mae(history[1], history[2], history[0]))


def _selftest() -> None:
    from ffcore.forecast import Bootstrap as B
    from ffcore.fixtures import players_from_flat

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
        players=players_from_flat(
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
        players=players_from_flat(
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

    spend = next(r for r in rows if r["action"].net > 0)
    assert abs(spend["value"] - spend["d_pts"] / (spend["action"].net / 1e6)
              ) < 1e-9, spend
    sale = next((r for r in rows if r["action"].net <= 0), None)
    if sale is not None:
        assert sale["value"] is None, sale

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
        players=players_from_flat(
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
    assert vby["thin_del"]["value"] > 2 * vby["deep_med"]["value"] > 0, vby

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

    top_a = [(9.0, Action("buy", buy="a", cost=1e6)),
             (8.0, Action("buy", buy="b", cost=1e6))]
    screened_a = top_a + [(7.0, Action("buy", buy="c", cost=1e6)),
                          (1.0, Action("buy", buy="ok1", cost=1e6)),
                          (0.5, Action("buy", buy="ok2", cost=1e6)),
                          (0.1, Action("buy", buy="bad", cost=1e6))]
    topped = _top_up(top_a, screened_a, lambda d, a: a.buy in ("ok1", "ok2", "bad"),
                     rank_key=lambda t: -t[0], minimum=2)
    keys = [a.buy for _, a in topped]
    assert keys == ["a", "b", "ok1", "ok2"], keys
    already_enough = _top_up(top_a, screened_a, lambda d, a: True,
                             rank_key=lambda t: -t[0], minimum=2)
    assert already_enough == top_a, already_enough
    dup_check = _top_up([(9.0, Action("buy", buy="a", cost=1e6))],
                        [(9.0, Action("buy", buy="a", cost=1e6)),
                         (5.0, Action("buy", buy="b", cost=1e6))],
                        lambda d, a: True, rank_key=lambda t: -t[0],
                        minimum=2)
    assert [a.buy for _, a in dup_check] == ["a", "b"], dup_check
    gain_aware = _top_up([], screened_a, lambda d, a: d > 0.5,
                         rank_key=lambda t: -t[0], minimum=10)
    assert [a.buy for _, a in gain_aware] == ["a", "b", "c", "ok1"], \
        gain_aware

    per5 = {1: dict(per[1])}
    acts5 = []
    for i in range(15):
        key = "listed%d" % i
        per5[1][key] = (10.0 - i * 0.1, 1.0)
        acts5.append(Action("buy", buy=key, cost=1e6))
    for i in range(3):
        key = "reliable%d" % i
        per5[1][key] = (2.0, 1.0)
        acts5.append(Action("buy", buy=key, cost=1e6))
    route5 = {"listed%d" % i: "listed" for i in range(15)}
    route5.update({"reliable%d" % i: "free" for i in range(3)})
    u5 = Universe(
        state=LeagueState({"me": dict(mine), "riv": dict(theirs)}, [1], "me"),
        forecaster=B(per5), cash=100e6, me="me",
        players=players_from_flat(
            pos={**u.view("pos"), **{a.buy: "MED" for a in acts5}},
            price={a.buy: 1e6 for a in acts5}, route=route5))
    rows5, *_ = u5.rank(acts5)
    kept5 = {r["action"].buy for r in rows5}
    assert all(("reliable%d" % i) in kept5 for i in range(3)), kept5
    assert len(kept5) == 15, kept5
    assert sum(1 for k in kept5 if k.startswith("listed")) == 12, kept5

    per6 = {1: dict(per[1])}
    acts6 = []
    for i in range(15):
        key = "big%d" % i
        per6[1][key] = (10.0 - i * 0.1, 1.0)
        acts6.append(Action("buy", buy=key, cost=20e6))
    for i in range(3):
        key = "eff%d" % i
        per6[1][key] = (6.0, 1.0)
        acts6.append(Action("buy", buy=key, cost=1e4))
    per6[1]["sham"] = (0.1, 1.0)
    acts6.append(Action("buy", buy="sham", cost=1e3))
    u6 = Universe(
        state=LeagueState({"me": dict(mine), "riv": dict(theirs)}, [1], "me"),
        forecaster=B(per6), cash=1000e6, me="me",
        players=players_from_flat(
            pos={**u.view("pos"), **{a.buy: "MED" for a in acts6}},
            price={a.buy: a.cost for a in acts6}))
    rows6, *_ = u6.rank(acts6)
    kept6 = {r["action"].buy for r in rows6}
    assert all(("eff%d" % i) in kept6 for i in range(3)), kept6
    assert sum(1 for k in kept6 if k.startswith("big")) == 12, kept6
    assert "sham" not in kept6, kept6

    half = Universe(
        state=LeagueState({"me": dict(mine), "riv": dict(theirs)}, [1, 2],
                          "me", ),
        forecaster=B({1: {"me_k": (0.1, 1.0), "dud": (1.0, 1.0)},
                      2: {**{k: (5.0, 1.0) for k in mine}, "dud": (1.0, 1.0)}}),
        cash=50e6, me="me",
        players=players_from_flat(pos={**u.view("pos"), "dud": "MED"},
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
        players=players_from_flat(pos={**sq_cd, "target": "DEL"},
                                  price={"target": 5e6},
                                  proceeds={"me_f3": 5e6}))
    acts_cd = u_cd.candidates()
    assert any(a.buy == "target" and a.sell == ("me_f3",) for a in acts_cd), \
        acts_cd
    assert not any(k in a.sell for a in acts_cd
                  for k in ("me_k", "me_d1", "me_d2", "me_d3", "me_d4",
                           "me_m1", "me_m2", "me_m3", "me_m4")), \
        [a for a in acts_cd if a.sell]


    from ffcore.fixtures import tiny_profile as _tiny_p

    _FALSY_DROP = ("pos", "owner", "route")
    _NONE_ONLY_DROP = ("price", "proceeds", "value", "market_exp", "start")

    falsy_edge = _tiny_p("falsy_edge", pos="", owner="", route="")
    zero_edge = _tiny_p("zero_edge", price=0.0, proceeds=0.0, value=0.0,
                       market_exp=0.0, start_p=0.0)
    none_edge = _tiny_p("none_edge", owner=None, route=None, price=None,
                       proceeds=None, value=None, market_exp=None,
                       start_p=None)
    u_views = Universe(
        state=LeagueState({"me": {}}, [1], "me"), forecaster=B({}),
        cash=0.0, me="me",
        players={"falsy_edge": falsy_edge, "zero_edge": zero_edge,
                "none_edge": none_edge})

    for field_name in _FALSY_DROP:
        view = u_views.view(field_name)
        assert "falsy_edge" not in view, (field_name, dict(view))
    for field_name in ("owner", "route"):
        view = u_views.view(field_name)
        assert "none_edge" not in view, (field_name, dict(view))

    for field_name in _NONE_ONLY_DROP:
        view = u_views.view(field_name)
        assert "zero_edge" in view and not view["zero_edge"], \
            (field_name, dict(view))
        assert "none_edge" not in view, (field_name, dict(view))

    assert u_views.view("name")["falsy_edge"] == "falsy_edge"
    assert u_views.view("name")["zero_edge"] == "zero_edge"
    assert u_views.view("name")["none_edge"] == "none_edge"

    sq = {"k": "POR", "d1": "DEF", "d2": "DEF", "d3": "DEF", "d4": "DEF",
         "spare_d": "DEF", "m1": "MED", "m2": "MED", "m3": "MED", "m4": "MED",
         "f1": "DEL", "dead_f": "DEL"}
    per = {1: {k: (3.0, 1.0) for k in sq}}
    per[1]["f1"] = (10.0, 1.0)
    per[1]["dead_f"] = (0.1, 1.0)
    u = Universe(
        state=LeagueState({"me": dict(sq)}, [1], "me"),
        forecaster=Bootstrap(per), cash=0.0, me="me",
        players=players_from_flat(pos=dict(sq),
                                  proceeds={"spare_d": 4e6, "dead_f": 6e6}),
        received_offers={})
    mine = u.state.squads["me"]
    spares = fieldable_spares(u)
    for s in spares:
        assert _fieldable({p: pos for p, pos in mine.items() if p != s}), s
    assert "k" not in spares and "f1" in spares and "dead_f" in spares, spares
    assert max_spare_proceeds(u) == 6e6, max_spare_proceeds(u)
    bare = Universe(
        state=LeagueState({"me": {"k": "POR", "d1": "DEF", "d2": "DEF",
                                  "d3": "DEF", "m1": "MED", "m2": "MED",
                                  "m3": "MED", "f1": "DEL"}}, [1], "me"),
        forecaster=Bootstrap({1: {}}), cash=0.0, me="me")
    assert fieldable_spares(bare) == [] and max_spare_proceeds(bare) == 0.0
    assert dict(u.dead_weight()) == {"dead_f": 6e6}, u.dead_weight()
    after = apply(u, Action("buy", buy="new_por", sell=("spare_d",)))
    assert "spare_d" not in after["me"], after["me"]
    assert after["me"]["new_por"] == "MED", after["me"]

    for pts, cost, want in [(120.0, 14.13e6, 120.0 / 14.13), (120.0, 0.0, None),
                            (120.0, -5e6, None), (None, 5e6, None),
                            (0.0, 5e6, 0.0)]:
        got = value_rate(pts, cost)
        assert got == want or abs(got - want) < 1e-9, (pts, cost, got)

    from ffcore.profile import mk_profile
    pf_per = {j: {"me_a": (2.0, 1.0), "me_b": (5.0, 1.0), "cand": (4.0, 1.0)}
              for j in (1, 2)}
    fc = Universe(
        state=LeagueState({"me": {"me_a": "MED", "me_b": "MED"}}, [1, 2], "me"),
        forecaster=Bootstrap(pf_per), cash=0.0, me="me",
        players={"me_a": mk_profile(20.0), "me_b": mk_profile(15.0),
                 "cand": mk_profile(8.0),
                 "unsimmed": mk_profile(1.0, "DEL", market_exp=3.0)}
    ).player_forecasts
    for k, season, nxt, par, simulated in [
            ("me_a", 4.0, 2.0, 0.0, True), ("me_b", 10.0, 5.0, 6.0, True),
            ("cand", 8.0, 4.0, 4.0, True), ("unsimmed", 6.0, 3.0, 6.0, False)]:
        assert (fc[k]["season_pts"], fc[k]["next_pts"], fc[k]["par"],
                fc[k]["simulated"]) == (season, nxt, par, simulated), (k, fc[k])
    assert fc["cand"]["pj"] == 8.0, fc["cand"]

    for d_pts, want in [(0.1, True), (-2.0, False), (0.0, False), (None, False)]:
        assert _gains({"d_pts": d_pts}) is want, d_pts
    assert not _gains({})

    par_of = {"good": 5.0, "weak": 1.99, "unknown": None}
    for mae, k, want in [(None, "weak", True), (2.9, "good", True),
                         (2.9, "weak", False), (2.9, "unknown", False),
                         (2.9, "missing", False)]:
        assert _clears_par_floor(par_of, mae, k) is want, (mae, k)

    print("decide self-test OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
