from __future__ import annotations

import json
import sys

import grading
from decide import (PRICE_LOG, Action, max_spare_proceeds, value_rate,
                    worth_doing)
from ffcore.league import app_fielded
from ffcore.render import title_name
from ffcore.season import best_xi
from ffcore.tidy import (DECISIONS, REPORTS, load_deadline, log_row,
                         run_now)

__all__ = ["shape"]

SLOT_ORDER = {"POR": 0, "DEF": 1, "MED": 2, "DEL": 3}

GROUP_LABEL = {
    "in": "PUT ON", "out": "TAKE OFF",
    "field": "FIELD — your eleven — the app has not said what you are playing",
    "keep": "KEEP — bench", "sell": "SELL — worth more as cash",
    "buy": "BUY — free agents",
    "save": "SAVE — better than yours, out of reach", "pass": "PASS",
}


def xi_change(marked: list[str], best) -> dict:
    best = list(best)
    if len(marked) != len(best) or not marked:
        return {"legal": False, "marked": marked, "in": [], "out": []}
    have, want = set(marked), set(best)
    return {"legal": True, "marked": marked,
            "in": [k for k in best if k not in have],
            "out": [k for k in marked if k not in want]}


def short(key, u) -> str:
    full = title_name(u.view("name").get(key, key))
    last = full.split()[-1:]
    clash = sum(1 for k in u.state.squads.get(u.me, {})
                if title_name(u.view("name").get(k, k)).split()[-1:] == last)
    return full if clash > 1 or not last else last[0]


def by_slot(u, keys) -> list[str]:
    exp, _xi = u.current_xi
    return sorted(keys, key=lambda k: (SLOT_ORDER.get(u.view("pos").get(k, ""), 9),
                                       -exp.get(k, 0.0)))


def shape(u, keys) -> str:
    n = {}
    for k in keys:
        n[u.view("pos").get(k, "")] = n.get(u.view("pos").get(k, ""), 0) + 1
    return "%d-%d-%d" % (n.get("DEF", 0), n.get("MED", 0), n.get("DEL", 0))


def xi_total(u, who) -> float:
    exp, _xi = u.current_xi
    return sum(exp.get(k, 0.0) for k in best_xi(u.state.squads.get(who, {}), exp))


def where(u, k) -> str:
    owner = u.view("owner").get(k)
    return owner.split()[0] if owner else "free agent"


def cell(u, k, group, place, money=None, pts=None, note="", value=None,
         market=None, premium=None) -> dict:
    exp, _xi = u.current_xi
    return {"name": title_name(u.view("name").get(k, k)),
            "pos": u.view("pos").get(k, ""), "start": u.next_up.get(k, (0.0, 0.0))[1],
            "xpts": exp.get(k, 0.0), "group": group, "where": place,
            "label": GROUP_LABEL[group], "money": money, "pts": pts,
            "note": note, "value": value, "market": market, "premium": premium}


def move_rank(r, u):
    return (u.view("route").get(r["action"].buy, "free") == "listed", -r["net_pts"])


def band_acts(u) -> list:
    exp, _xi = u.current_xi
    mine = u.state.squads.get(u.me, {})
    return ([(k, Action("sell", sell=(k,),
                        proceeds=u.view("proceeds").get(k, 0.0))) for k in mine]
            + [(k, Action("buy", buy=k, cost=price))
               for k, price in u.view("price").items()
               if k not in mine and exp.get(k, 0.0) > u.xi_bar])


def sale_pts(u, bands, dead) -> dict[str, float]:
    _exp, xi = u.current_xi
    return {k: u.cash_pts(bands[k][3])
            - (0.0 if k in dead else max(0.0, -bands[k][4]))
            for k in u.state.squads.get(u.me, {}) if k in bands and k not in xi}


def why(r) -> str:
    return " + ".join(w for w, v in (("points", r["d_pts"]), ("cash", r["cash_pts"]))
                      if v > 0)


def ladder_rows(u, rows, bands, chg) -> list[dict]:
    exp, xi = u.current_xi
    mine = u.state.squads.get(u.me, {})
    sale = sale_pts(u, bands, {k for k, _ in u.dead_weight()})
    dead = {k for k, v in sale.items() if v >= 0}
    won = {r["action"].buy: r for r in rows if r["action"].buy}
    pts = {k: v[0] for k, v in bands.items() if k not in won}
    reach = u.cash + max_spare_proceeds(u)
    price = u.view("price")
    rest = [k for k in price if k not in mine and exp.get(k, 0.0) > u.xi_bar]

    plan = ([(k, "in", "bench") for k in by_slot(u, chg["in"])]
            + [(k, "out", "yours") for k in by_slot(u, chg["out"])]
            if chg["legal"] else [(k, "field", "yours") for k in by_slot(u, xi)])
    moving = set(chg["in"]) | set(chg["out"])
    plan += [(k, "keep", "yours") for k in by_slot(
        u, [k for k in mine if k not in xi and k not in dead and k not in moving])]
    out = [cell(u, k, g, place, pts=pts.get(k)) for k, g, place in plan]
    out += [cell(u, k, "sell", "yours", money=u.view("proceeds").get(k, 0.0),
                 pts=pts.get(k), note="cash" if u.cash_pts(bands[k][3]) > 0 else "")
            for k in sorted(dead, key=lambda k: -sale[k])]

    screened = {r["action"].buy
                for r in worth_doing(u, [won[k] for k in rest if k in won])}
    for k in sorted(screened, key=lambda k: move_rank(won[k], u)):
        if u.route_kind(k) != "free":
            continue
        r = won[k]
        sold = r["action"].sell
        out.append(cell(
            u, k, "buy", where(u, k),
            money=-r["action"].net, pts=r["d_pts"],
            note=" · ".join(n for n in (
                ("sell " + " + ".join(short(s, u) for s in sold)) if sold else "",
                "for " + why(r) if why(r) else "") if n),
            value=r.get("value"), market=u.view("value").get(k),
            premium=r.get("burn") or 0.0))
    for k in sorted((k for k in rest if k not in won and price[k] > reach),
                    key=lambda k: -exp.get(k, 0.0)):
        out.append(cell(u, k, "save", where(u, k), money=reach - price[k],
                        pts=pts.get(k), note="short",
                        value=value_rate(pts.get(k), price[k] - reach)))
    for k in sorted((k for k in rest if k not in won and price[k] <= reach
                     and u.route_kind(k) == "free"),
                    key=lambda k: -exp.get(k, 0.0)):
        out.append(cell(u, k, "pass", where(u, k), money=-price[k],
                        pts=pts.get(k)))
    return out


def payload(u, base, ladder, chg, locks_h=None) -> dict:
    _exp, xi = u.current_xi
    lo, hi = base.band(u.me)
    if not chg["legal"]:
        xi_note = ("the app has not said which eleven you are fielding, so "
                   "this is the whole sheet rather than a change list")
    elif not chg["in"] and not chg["out"]:
        xi_note = "no change — you are already fielding the best eleven"
    else:
        xi_note = ""
    mine = xi_total(u, u.me)
    rival = max(((xi_total(u, m), m) for m in u.state.squads if m != u.me),
                default=None)
    return {
        "locks_in_h": locks_h,
        "cash": u.cash,
        "cash_locked": u.locked_cash,
        "squad_value": sum(u.view("proceeds").values()),
        "expected_finish": round(base.expected_position(), 2),
        "p_win": round(base.position().get(1, 0.0), 3),
        "band": [lo, hi],
        "ladder": ladder,
        "xi_total": mine,
        "shape": shape(u, xi),
        "shape_now": shape(u, chg["marked"]) if chg["legal"] else "",
        "rival_best": ({"manager": rival[1], "xi": rival[0],
                        "gap": mine - rival[0]} if rival else {}),
        "xi_note": xi_note,
        "standings": [
            {"manager": m, "me": m == u.me,
             "now": u.state.carried.get(m, 0.0), "mean": base.mean(m),
             "lo": base.band(m)[0], "hi": base.band(m)[1],
             "cash": u.cash if m == u.me else u.rival_cash.get(m, 0.0),
             "cash_known": m == u.me,
             "p_above": None if m == u.me else base.beat(m)}
            for m in sorted(u.state.squads, key=lambda m: -base.mean(m))],
    }


def log_cash_price(measured) -> None:
    if measured is not None:
        log_row(DECISIONS / PRICE_LOG,
                {"measured_at": run_now().strftime("%Y-%m-%dT%H%MZ"),
                 "places_per_million": "%.6f" % measured})


def _selftest() -> None:
    from decide import Universe
    from ffcore.forecast import Bootstrap
    from ffcore.season import LeagueState, Standings

    best = ["gk", "d1", "d2", "d3", "d4", "m1", "m2", "m3", "m4", "m5", "f1"]
    for marked, legal, ins, outs in [
            (list(best), True, [], []),
            ([k for k in best if k != "m5"] + ["bench1"], True, ["m5"], ["bench1"]),
            (best[:10], False, [], []),
            ([], False, [], [])]:
        got = xi_change(marked, best)
        assert (got["legal"], got["in"], got["out"]) == (legal, ins, outs), got

    st = Standings(totals={"me": [1000.0, 1200.0, 1400.0, 1600.0],
                           "riv": [1500.0, 1300.0, 1100.0, 900.0]}, me="me")
    u = Universe(state=LeagueState({"me": {}, "riv": {}}, [1, 2], "me",
                                   carried={"me": 17.0, "riv": 23.0}),
                 forecaster=Bootstrap({}, pool=[1, 2, 3]), cash=23.6e6, me="me",
                 facts=dict(
                     name={"yuri": "yuri berchiche",
                           "benat": "benat turrientes"}))
    rows = [{"action": Action("swap", buy="yuri", sell="benat",
                              cost=20e6, proceeds=5.87e6),
             "net_pts": 0.433, "cash_pts": 0.0, "d_win": 0.364,
             "d_pts": 120.0, "pts_lo": 43.3, "pts_hi": 210.0, "mean": 1510.0,
             "value": 120.0 / (14.13e6 / 1e6)}]

    sq = {"k": "POR", "d1": "DEF", "d2": "DEF", "d3": "DEF", "d4": "DEF",
          "m1": "MED", "m2": "MED", "m3": "MED", "m4": "MED",
          "f1": "DEL", "f2": "DEL", "spare_m": "MED", "spare_k": "POR"}
    val = {k: 5.0 for k in sq}
    val["spare_m"], val["spare_k"] = 0.4, 0.2
    u2 = Universe(state=LeagueState({"me": dict(sq), "riv": dict(sq)}, [1, 2],
                                    "me"),
                  forecaster=Bootstrap({1: {k: (v, 1.0) for k, v in val.items()},
                                        2: {k: (v, 1.0) for k, v in val.items()}}),
                  cash=0.0, me="me",
                  facts=dict(
                      pos=dict(sq),
                      proceeds={"spare_m": 7.45e6, "spare_k": 4.73e6, "d1": 9e6},
                      name={"spare_m": "benat turrientes",
                            "spare_k": "alvaro fernandez"}))
    dead = u2.dead_weight()
    assert dead == [("spare_m", 7.45e6), ("spare_k", 4.73e6)], dead
    assert len(best_xi({k: v for k, v in sq.items() if k not in dict(dead)},
                       val)) == 11

    u2.part_played = {1: {"alaves"}}
    u2.forecaster = Bootstrap({1: {k: (v, 1.0) for k, v in val.items()},
                               2: {k: ((9.0 if k == "spare_m" else v), 1.0)
                                   for k, v in val.items()}})
    assert "spare_m" not in dict(u2.dead_weight())
    u2.forecaster = Bootstrap(
        {1: {k: ((9.0 if k == "spare_m" else v), 1.0) for k, v in val.items()},
         2: {k: (v, 1.0) for k, v in val.items()}})
    assert "spare_m" in dict(u2.dead_weight())
    u2.state.jornadas = [1]
    assert "spare_m" not in dict(u2.dead_weight())

    chg = xi_change([], [])
    d = payload(u, st, [], chg, locks_h=41.1)
    assert (d["expected_finish"], d["p_win"], d["band"]) == (1.5, 0.5, [1060.0, 1540.0])
    assert d["locks_in_h"] == 41.1 and d["cash"] == 23.6e6 and d["cash_locked"] == 0.0
    assert [r["manager"] for r in d["standings"]] == ["me", "riv"], d
    assert d["standings"][0]["me"] and d["standings"][1]["p_above"] == 0.5
    st3 = Standings(totals={"me": [1000.0, 1200.0, 1000.0],
                            "riv": [1500.0, 900.0, 1500.0]}, me="me")
    assert payload(u, st3, [], chg)["p_win"] == 0.333

    flat = [{**rows[0], "net_pts": 0.0, "d_win": 0.0, "d_pts": 0.0}]
    assert worth_doing(u, flat) == []

    all_rows = [{"action": Action("buy", buy=buy, cost=5e6),
                 "net_pts": d_pts, "cash_pts": 0.0, "d_win": 0.0, "value": value,
                 "d_pts": d_pts, "pts_lo": lo, "pts_hi": hi, "burn": burn}
                for buy, d_pts, lo, hi, value, burn in [
                    ("steady", 40.0, 10.0, 70.0, 8.0, None),
                    ("dud", 20.0, 5.0, 35.0, 4.0, 1.2e6),
                    ("maverick", 10.0, -50.0, 260.0, 2.0, None),
                    ("wished", 50.0, 15.0, 80.0, 10.0, None)]]
    uc_price = dict.fromkeys(("steady", "maverick", "dud", "wished"), 5e6)
    uc_owner = {"wished": "riv"}
    uc_route = {"wished": "listed"}
    uc_value = {"steady": 5e6, "dud": 3.8e6}
    uc = Universe(
        state=LeagueState({"me": {}, "riv": {}}, [1, 2], "me"),
        forecaster=Bootstrap(
            {j: {"steady": (5.0, 1.0), "maverick": (4.0, 1.0),
                 "dud": (3.0, 1.0),
                 "wished": (6.5, 1.0)} for j in (1, 2)}),
        cash=10e6, me="me",
        facts={"pos": dict.fromkeys(uc_price, "MED"), "price": uc_price,
               "owner": uc_owner, "route": uc_route, "value": uc_value,
               "pj": dict.fromkeys(uc_price, 5.0)})
    lad = ladder_rows(uc, all_rows, {}, xi_change([], []))
    by_group = {}
    for r in lad:
        by_group.setdefault(r["group"], []).append(r["name"].lower())
    assert by_group["buy"] == ["steady", "dud", "maverick"], by_group
    assert "wished" not in sum(by_group.values(), []), by_group
    cells = {r["name"].lower(): r for r in lad}
    assert (cells["steady"]["market"], cells["steady"]["premium"]) == (5e6, 0.0)
    assert (cells["dud"]["market"], cells["dud"]["premium"]) == (3.8e6, 1.2e6)
    assert cells["steady"]["where"] == "free agent"

    u_pos = Universe(
        state=LeagueState({"me": {}}, [1], "me"), forecaster=Bootstrap({}),
        cash=0.0, me="me",
        facts={"pos": {
            "a": "POR", "b": "DEF", "c": "DEF", "d": "DEF", "e": "DEF",
            "f": "MED", "g": "MED", "h": "MED", "i": "MED", "j": "DEL",
            "k": "DEL"}})
    assert by_slot(u_pos, ["j", "f", "a", "b"]) == ["a", "b", "f", "j"]
    assert shape(u_pos, list("abcdefghijk")) == "4-4-2"
    assert shape(u_pos, ["a", "b", "c", "d", "f", "g"]) == "3-2-0"
    assert shape(u_pos, []) == "0-0-0"

    many_j = list(range(1, 11))
    sqb = {"k": "POR", **{f"d{i}": "DEF" for i in range(1, 5)},
           "star": "MED", **{f"m{i}": "MED" for i in range(1, 5)},
           "f1": "DEL", "dead": "MED"}
    riv = {"rk": "POR", **{f"rd{i}": "DEF" for i in range(1, 5)},
           **{f"rm{i}": "MED" for i in range(1, 5)}, "rf1": "DEL"}
    perb = {j: {**{k: (3.0, 0.9) for k in sqb if k not in ("star", "dead")},
                "star": (6.0, 0.9), "dead": (3.0, 0.05), "cand": (5.0, 0.8),
                **{k: (3.0, 0.9) for k in riv}} for j in many_j}
    ub = Universe(state=LeagueState({"me": dict(sqb), "riv": dict(riv)}, many_j,
                                    "me"),
                  forecaster=Bootstrap(perb),
                  cash=10e6, me="me",
                  facts=dict(
                      pos={**sqb, "cand": "MED"}, price={"cand": 5e6},
                      proceeds={"dead": 1e6, "star": 20e6}))
    asked = dict(band_acts(ub))
    assert set(asked) == {*sqb, "cand"}, asked
    assert all(asked[k].buy == "" and asked[k].sell == (k,) for k in sqb)
    assert asked["cand"].buy == "cand" and asked["cand"].sell == ()
    _rows, _base, _lam, bands = ub.rank([], extra=list(asked.items()))
    assert set(bands) == {*sqb, "cand"}, sorted(bands)
    assert all(lo <= med <= hi for med, lo, hi, _a, _m in bands.values())
    assert bands["star"][0] < -20 and -5 < bands["dead"][0] < 5
    assert bands["cand"][0] > 0, bands["cand"]
    assert ub.rank([], extra=[])[3] == {}
    rows2, _b2, _l2, bands2 = ub.rank([Action("buy", buy="cand", cost=5e6)],
                                      extra=list(asked.items()))
    assert [r for r in rows2 if r["action"].buy == "cand"] and "cand" not in bands2

    for d_pts, cash, want in [(1.0, 0.2, "points + cash"), (-1.0, 0.2, "cash"),
                              (2.0, -0.1, "points"), (0.0, 0.0, "")]:
        assert why({"d_pts": d_pts, "cash_pts": cash}) == want, (d_pts, cash)
    from ffcore.fixtures import tiny_market_universe
    mu = tiny_market_universe(lam=0.3)
    mu.facts.update(value={"bench_m": 3e6}, trend={"bench_m": -10.0})
    bm = {"bench_m": (0.0, 0.0, 0.0,
                      Action("sell", sell=("bench_m",), proceeds=3e6), -2.0)}
    assert abs(sale_pts(mu, bm, set())["bench_m"] - (0.09 - 2.0)) < 1e-9
    assert abs(sale_pts(mu, bm, {"bench_m"})["bench_m"] - 0.09) < 1e-9

    print("sim self-test OK")


def main() -> None:
    import decide

    u = decide.load()
    grading.log_predictions(u.sc)
    if len(u.state.squads) < 2 or not u.state.jornadas:
        print("sim: nothing to simulate (%d squads, %d jornadas left)"
              % (len(u.state.squads), len(u.state.jornadas)))
        return
    deadline = load_deadline()
    locks_h = (None if deadline is None
               else (deadline - run_now()).total_seconds() / 3600)
    rows, base, measured, bands = u.rank(
        u.candidates(budget=float("inf")), extra=band_acts(u))
    log_cash_price(measured)
    chg = xi_change(app_fielded(u.state.squads.get(u.me, {}), u.view("name")),
                    u.current_xi[1])

    doc = {"generated_at": run_now().strftime("%Y-%m-%dT%H:%MZ"),
           **payload(u, base, ladder_rows(u, rows, bands, chg), chg, locks_h)}
    REPORTS.mkdir(exist_ok=True)
    (REPORTS / "decisions.json").write_text(
        json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("wrote %s" % (REPORTS / "decisions.json"))



if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        raise SystemExit(0)
    main()
