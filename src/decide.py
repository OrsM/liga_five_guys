
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from functools import cached_property
from typing import NamedTuple


from ffcore.forecast import Bootstrap
from stats import percentile
from ffcore.schedule import phantom_topup
from ffcore.pricing import cash_price
from ffcore.action import Action
from ffcore.market import Market
from ffcore.outlook import Outlook
from ffcore.season import LeagueState, best_xi, simulate_many

__all__ = ["Action", "Band", "Ranking", "Universe", "band_acts", "plan",
           "sale_pts"]

SCREEN_TRIALS = 250
FINAL_TRIALS = 3000
KEEP = 12


class Band(NamedTuple):
    median: float
    lo: float
    hi: float
    action: Action
    mean: float


class Ranking(NamedTuple):
    rows: list[dict]
    base: object
    measured: float | None
    bands: dict[str, Band]


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

    def route_kind(self, k: str) -> str:
        if k in self.mine:
            return "mine"
        owner = self.market.owner.get(k)
        return "free" if not owner or owner == self.me else "listed"

    def candidates(self, budget: float | None = None) -> list["Action"]:
        cash = self.market.cash if budget is None else budget
        mine = set(self.mine)
        o = self.outlook
        par_of = o.par

        spare = sorted(fieldable_spares(self), key=lambda k: _nulls_last(
            value_rate(par_of.get(k, 0.0), self.market.proceeds.get(k, 0.0))))

        out: list[Action] = []
        for c, price in sorted(self.market.price.items(), key=lambda kv: kv[1]):
            if c in mine or self.route_kind(c) == "listed":
                continue
            if o.season.get(c, 0.0) <= o.xi_bar and self.market.cash_pts(
                    Action("buy", buy=c, cost=price)) <= 0:
                continue
            if price <= cash:
                out.append(Action("buy", buy=c, cost=price))
            for s in spare:
                got = self.market.proceeds.get(s, 0.0)
                if price <= cash + got:
                    out.append(Action("swap", buy=c, sell=s, cost=price,
                                      proceeds=got))
        return out

    def rank(self, acts: list["Action"], seed: int = 1,
             extra: list[tuple[str, "Action"]] = ()) -> Ranking:
        screen = score_many(self, [self.state.squads]
                             + [apply(self, a) for a in acts],
                             SCREEN_TRIALS, seed)
        base_s, rest = screen[0], screen[1:]
        screened, reach = [], []
        for a, r in zip(acts, rest):
            d, _lo, _hi = band(paired(r, base_s, self.me))
            reach.append((a.cost - a.proceeds - self.market.cash, d))
        measured = cash_price(reach)
        lam = self.market.lam if self.market.lam is not None else (measured or 0.0)
        for a, r in zip(acts, rest):
            if a.cost <= self.market.cash + a.proceeds:
                d, _lo, _hi = band(paired(r, base_s, self.me))
                screened.append((d + self.market.cash_pts(a, lam), a))

        cur_xi = self.outlook.xi.players
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
        bands = {k: Band(*band(pairs), a, sum(pairs) / len(pairs) if pairs else 0.0)
                for (k, a), pairs in ((ka, paired(r, base, self.me)) for ka, r in
                                      zip(rest, final[len(afters) + 1:]))}
        out = []
        for a, r in zip(keep, scored):
            cash = self.market.cash_pts(a, lam)
            pairs = paired(r, base, self.me)
            d_pts, lo, hi = band(pairs)
            out.append({
                "action": a,
                "d_pts": d_pts,
                "pts_lo": lo,
                "pts_hi": hi,
                "net_pts": d_pts + cash,
                "burn": self.market.burn(a),
                "cash_pts": cash,
                "d_win": r.position().get(1, 0.0) - base.position().get(1, 0.0),
                "mean": r.mean(self.me),
            })
        rows = sorted(out, key=lambda d: (-d["net_pts"], d["action"].net))
        return Ranking(rows, base, measured, bands)


def _nulls_last(v: float | None) -> tuple[bool, float]:
    return v is None, v or 0.0


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
    mine_squad = u.mine
    return [k for k in mine_squad if _fieldable(
        {p: s for p, s in mine_squad.items() if p != k})]


def apply(u, *acts: Action) -> dict[str, dict[str, str]]:
    sq = {m: dict(s) for m, s in u.state.squads.items()}
    for a in acts:
        for gone in a.sell:
            sq[u.me].pop(gone, None)
        if a.buy:
            sq[u.me][a.buy] = u.market.pos.get(a.buy, "MED")
    return {m: phantom_topup(s) for m, s in sq.items()}


def worth_doing(u, rows) -> list:
    return [r for r in rows if r["net_pts"] > 0]


def band_acts(u) -> list:
    mine, o, m = u.mine, u.outlook, u.market
    return ([(k, Action("sell", sell=(k,),
                        proceeds=m.proceeds.get(k, 0.0))) for k in mine]
            + [(k, Action("buy", buy=k, cost=price))
               for k, price in m.price.items()
               if k not in mine and o.season.get(k, 0.0) > o.xi_bar])


def sale_pts(u, bands) -> dict[str, float]:
    xi = u.outlook.xi.players
    return {k: u.market.cash_pts(bands[k].action) + bands[k].mean
            for k in u.mine if k in bands and k not in xi}


def plan(u, rows, base) -> tuple[list[dict], float]:
    picked: list[dict] = []
    cash, gain = u.market.cash, 0.0
    for r in sorted(worth_doing(u, rows), key=lambda r: -r["net_pts"]):
        a = r["action"]
        used = {k for p in picked for k in (p["action"].buy, *p["action"].sell)}
        if used & {a.buy, *a.sell} or a.net > cash:
            continue
        acts = [p["action"] for p in picked] + [a]
        after = score_many(u, [apply(u, *acts)], FINAL_TRIALS, 1)[0]
        total = band(paired(after, base, u.me))[0] + sum(
            u.market.cash_pts(x) for x in acts)
        if total > gain:
            picked.append(r)
            cash, gain = cash - a.net, total
    return picked, gain


PRICE_WINDOW = 50
BID_BEATS = 0.8


def premium_to_beat(ratios: list[float]) -> float:
    recent = sorted(ratios[-PRICE_WINDOW:])
    return recent[min(len(recent) - 1, int(BID_BEATS * len(recent)))] if recent else 1.0


def _selftest() -> None:
    from dataclasses import replace

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
    assert all(a.cost <= u.market.cash + a.proceeds for a in acts), acts


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
        forecaster=B(per6),
        market=Market(cash=1000e6, pos={**u.market.pos, **{a.buy: "MED" for a in acts6}},
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

    for pts, cost, want in [(120.0, 14.13e6, 120.0 / 14.13), (120.0, 0.0, None),
                            (120.0, -5e6, None), (None, 5e6, None),
                            (0.0, 5e6, 0.0)]:
        got = value_rate(pts, cost)
        assert got == want or abs(got - want) < 1e-9, (pts, cost, got)

    assert premium_to_beat([1.0] * 5 + [1.3] * 5) == 1.3
    assert premium_to_beat([1.0] * 9 + [1.3]) == 1.0
    assert premium_to_beat([]) == 1.0

    from ffcore.fixtures import tiny_market_universe
    mu = tiny_market_universe(lam=0.3, premium=1.05)
    mu = replace(mu, market=replace(
        mu.market, value={"bench_m": 3e6, "riser": 2e6},
        trend={"riser": 20.0, "bench_m": -10.0},
        price={**mu.market.price, "riser": 2e6},
        pos={**mu.market.pos, "riser": "MED"}))
    buy_riser = Action("buy", buy="riser", cost=2e6)
    assert abs(mu.market.cash_pts(buy_riser) - 0.3 * (0.4 - 0.1)) < 1e-9
    assert "riser" in {a.buy for a in mu.candidates()}
    sell_m = Action("sell", sell=("bench_m",), proceeds=3e6)
    assert abs(mu.market.cash_pts(sell_m) - 0.3 * 0.3) < 1e-9
    assert mu.market.cash_pts(buy_riser, lam=0.0) == 0.0
    mu = replace(mu, market=replace(mu.market, trend={"riser": 0.0}))
    assert mu.market.cash_pts(buy_riser) < 0
    assert "riser" not in {a.buy for a in mu.candidates()}

    print("decide self-test OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
