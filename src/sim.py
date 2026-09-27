from __future__ import annotations

import json
import sys

from decide import (BID_BEATS, FINAL_TRIALS, PRICE_LOG, Action, apply, band, paired,
                    score_many, worth_doing)
from ffcore.league import app_fielded
from ffcore.render import title_name
from ffcore.tidy import DECISIONS, REPORTS, load_deadline, log_row, run_now

__all__ = ["report"]

SLOT_ORDER = {"POR": 0, "DEF": 1, "MED": 2, "DEL": 3}
BACKUPS = 3


def xi_change(marked: list[str], best) -> dict:
    best = list(best)
    if len(marked) != len(best) or not marked:
        return {"legal": False, "in": list(best), "out": []}
    have, want = set(marked), set(best)
    return {"legal": True, "in": [k for k in best if k not in have],
            "out": [k for k in marked if k not in want]}


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


def why(r) -> str:
    return " + ".join(w for w, v in (("points", r["d_pts"]), ("cash", r["cash_pts"]))
                      if v > 0)


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


def player(m, k) -> dict:
    return {"name": title_name(m.name.get(k, k)), "pos": m.pos.get(k, "")}


def buy_row(m, r) -> dict:
    a = r["action"]
    return {**player(m, a.buy), "ask": a.cost,
            "bid": min(a.cost * m.premium, max(a.cost, m.cash + a.proceeds)),
            "sell": [player(m, k)["name"] for k in a.sell],
            "proceeds": a.proceeds, "gain": r["d_pts"], "why": why(r),
            "trend": m.trend.get(a.buy),
            "placed": m.my_bid.get(a.buy),
            "done": a.buy in m.my_bid}


def ping(todo: list[dict]) -> str:
    said = {"field": lambda d: "Field " + ", ".join(d["on"]),
            "buy": lambda d: "Buy %s (bid up to %.1fM)%s" % (
                d["name"], d["bid"] / 1e6,
                ", selling " + " + ".join(d["sell"]) if d["sell"] else ""),
            "sell": lambda d: "Sell " + d["name"]}
    return "; ".join(said[d["what"]](d) for d in todo if not d.get("done"))


def report(u, ranked, chg, lock_at=None) -> dict:
    rows, base, bands = ranked.rows, ranked.base, ranked.bands
    o, m, mine = u.outlook, u.market, u.mine
    exp, xi = o.xi
    picked, gain = plan(u, rows, base)
    gone = {k for p in picked for k in p["action"].sell}
    todo = []
    if chg["in"] or chg["out"]:
        todo.append({"what": "field", "legal": chg["legal"],
                     "on": [player(m, k)["name"] for k in chg["in"]],
                     "off": [player(m, k)["name"] for k in chg["out"]],
                     "gain": (sum(exp.get(k, 0.0) for k in chg["in"])
                              - sum(exp.get(k, 0.0) for k in chg["out"]))
                     if chg["legal"] else None})
    todo += [{"what": "buy", **buy_row(m, r)} for r in picked]
    todo += [{"what": "sell", **player(m, k),
              "proceeds": m.proceeds.get(k, 0.0),
              "done": m.route.get(k) == "listed"}
             for k, v in sorted(sale_pts(u, bands).items(), key=lambda kv: -kv[1])
             if v > 0 and k not in gone]
    chosen = {p["action"].buy for p in picked}
    backup = [buy_row(m, r) for r in sorted(worth_doing(u, rows),
                                            key=lambda r: -r["net_pts"])
              if r["action"].buy and r["action"].buy not in chosen][:BACKUPS]
    lo, hi = base.band(u.me)
    return {
        "generated_at": run_now().strftime("%Y-%m-%dT%H:%MZ"),
        "lock_at": lock_at.isoformat() if lock_at else None,
        "cash": m.cash, "cash_locked": m.locked_cash,
        "finish": round(base.expected_position(), 2),
        "p_win": round(base.position().get(1, 0.0), 3),
        "band": [lo, hi],
        "do": todo, "plan_gain": gain, "backup": backup, "ping": ping(todo),
        "bid_beats": BID_BEATS,
        "squad": [
            {**player(m, k), "xi": k in xi,
             "start": o.next_up.get(k, (0.0, 0.0))[1], "next": exp.get(k, 0.0),
             "season": o.season.get(k, 0.0), "value": m.value.get(k),
             "trend": m.trend.get(k)}
            for k in sorted(mine, key=lambda k: (SLOT_ORDER.get(mine[k], 9),
                                                 -exp.get(k, 0.0)))],
        "standings": [
            {"manager": mgr, "me": mgr == u.me,
             "now": u.state.carried.get(mgr, 0.0), "mean": base.mean(mgr),
             "lo": base.band(mgr)[0], "hi": base.band(mgr)[1],
             "cash": m.cash if mgr == u.me else u.rival_cash.get(mgr, 0.0),
             "p_above": None if mgr == u.me else base.beat(mgr)}
            for mgr in sorted(u.state.squads, key=lambda g: -base.mean(g))],
    }


def log_cash_price(measured) -> None:
    if measured is not None:
        log_row(DECISIONS / PRICE_LOG,
                {"measured_at": run_now().strftime("%Y-%m-%dT%H%MZ"),
                 "places_per_million": "%.6f" % measured})


def _selftest() -> None:
    from dataclasses import replace

    from decide import Band, Universe
    from ffcore.fixtures import tiny_market_universe
    from ffcore.market import Market
    from ffcore.forecast import Bootstrap
    from ffcore.season import LeagueState

    best = ["gk", "d1", "d2", "d3", "d4", "m1", "m2", "m3", "m4", "m5", "f1"]
    for marked, legal, ins, outs in [
            (list(best), True, [], []),
            ([k for k in best if k != "m5"] + ["bench1"], True, ["m5"], ["bench1"]),
            (best[:10], False, best, [])]:
        got = xi_change(marked, best)
        assert (got["legal"], got["in"], got["out"]) == (legal, ins, outs), got

    for d_pts, cash, want in [(1.0, 0.2, "points + cash"), (-1.0, 0.2, "cash"),
                              (2.0, -0.1, "points"), (0.0, 0.0, "")]:
        assert why({"d_pts": d_pts, "cash_pts": cash}) == want, (d_pts, cash)

    mu = tiny_market_universe(lam=0.3)
    mu.market = replace(mu.market, value={"bench_m": 3e6}, trend={"bench_m": -10.0})
    bm = {"bench_m": Band(0.0, 0.0, 0.0,
                          Action("sell", sell=("bench_m",), proceeds=3e6), -2.0)}
    assert abs(sale_pts(mu, bm)["bench_m"] - (0.09 - 2.0)) < 1e-9

    many_j = list(range(1, 11))
    sqb = {"k": "POR", **{f"d{i}": "DEF" for i in range(1, 5)},
           "star": "MED", **{f"m{i}": "MED" for i in range(1, 5)},
           "f1": "DEL", "dead": "MED"}
    riv = {"rk": "POR", **{f"rd{i}": "DEF" for i in range(1, 5)},
           **{f"rm{i}": "MED" for i in range(1, 5)}, "rf1": "DEL"}
    perb = {j: {**{k: (3.0, 0.9) for k in sqb if k not in ("star", "dead")},
                "star": (6.0, 0.9), "dead": (3.0, 0.05), "cand": (5.0, 0.8),
                "twin": (5.0, 0.8), **{k: (3.0, 0.9) for k in riv}}
            for j in many_j}
    ub = Universe(state=LeagueState({"me": dict(sqb), "riv": dict(riv)}, many_j,
                                    "me"),
                  forecaster=Bootstrap(perb), me="me",
                  market=Market(cash=10e6, pos={**sqb, "cand": "MED", "twin": "MED"},
                             price={"cand": 5e6, "twin": 5e6},
                             proceeds={"dead": 1e6, "star": 20e6}))
    asked = dict(band_acts(ub))
    assert set(asked) == {*sqb, "cand", "twin"}, asked
    assert all(asked[k].buy == "" and asked[k].sell == (k,) for k in sqb)
    ranked = ub.rank(ub.candidates(), extra=list(asked.items()))
    rows, base, bands = ranked.rows, ranked.base, ranked.bands
    assert bands["star"].median < -20 and -5 < bands["dead"].median < 5
    assert bands["dead"].action == asked["dead"], bands["dead"]
    picked, gain = plan(ub, rows, base)
    bought = [p["action"].buy for p in picked]
    assert bought and set(bought) <= {"cand", "twin"}, bought
    assert sum(p["action"].net for p in picked) <= ub.market.cash, picked
    sold = [k for p in picked for k in p["action"].sell]
    assert len(sold) == len(set(sold)), sold
    assert gain >= max(r["net_pts"] for r in rows) - 5.0, (gain, rows[0])

    doc = report(ub, ranked, xi_change([], ub.outlook.xi.players))
    assert [d["what"] for d in doc["do"]][:1] == ["field"], doc["do"]
    assert {d["name"].lower() for d in doc["do"] if d["what"] == "buy"} == set(bought)
    assert all(b["name"].lower() not in bought for b in doc["backup"])
    assert [s["pos"] for s in doc["squad"]][0] == "POR", doc["squad"]
    assert sum(s["xi"] for s in doc["squad"]) == 11
    assert [r["manager"] for r in doc["standings"]][0] in ("me", "riv")
    assert doc["standings"][[r["me"] for r in doc["standings"]].index(True)][
        "p_above"] is None
    json.dumps(doc)
    assert ping([{"what": "field", "on": ["A", "B"]},
                 {"what": "buy", "name": "C", "bid": 12.34e6, "sell": ["D"]},
                 {"what": "sell", "name": "E"}]) == (
        "Field A, B; Buy C (bid up to 12.3M), selling D; Sell E")
    assert ping([]) == ""
    assert ping([{"what": "buy", "name": "C", "bid": 1e6, "sell": [], "done": True},
                 {"what": "sell", "name": "E", "done": False}]) == "Sell E"

    ub.market = replace(ub.market, my_bid={bought[0]: 4e6}, route={"dead": "listed"})
    doc = report(ub, ranked, xi_change([], ub.outlook.xi.players))
    buys = {d["name"].lower(): d for d in doc["do"] if d["what"] == "buy"}
    assert buys[bought[0]]["done"] and buys[bought[0]]["placed"] == 4e6, buys
    assert all(not d["done"] and d["placed"] is None
               for n, d in buys.items() if n != bought[0]), buys
    assert all(d["done"] == (d["name"].lower() == "dead")
               for d in doc["do"] if d["what"] == "sell"), doc["do"]

    print("sim self-test OK")


def main() -> None:
    import decide

    u = decide.load()
    if len(u.state.squads) < 2 or not u.state.jornadas:
        print("sim: nothing to simulate (%d squads, %d jornadas left)"
              % (len(u.state.squads), len(u.state.jornadas)))
        return
    ranked = u.rank(u.candidates(budget=float("inf")), extra=band_acts(u))
    log_cash_price(ranked.measured)
    chg = xi_change(app_fielded(u.mine, u.market.name),
                    u.outlook.xi.players)
    REPORTS.mkdir(exist_ok=True)
    (REPORTS / "decisions.json").write_text(json.dumps(
        report(u, ranked, chg, load_deadline()),
        ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("wrote %s" % (REPORTS / "decisions.json"))


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        raise SystemExit(0)
    main()
