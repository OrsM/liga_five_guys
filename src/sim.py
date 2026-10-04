from __future__ import annotations

import json
import sys

from assemble import universe
from decide import CONFIDENCE, at_risk, board, verdict
from ffcore.league import app_fielded
from ffcore.render import title_name
from ffcore.clock import run_now
from ffcore.jornadas import load_deadline
from ffcore.tidy import REPORTS

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


def player(m, k) -> dict:
    return {"name": title_name(m.name.get(k, k)), "pos": m.pos.get(k, "")}


def buy_row(m, r) -> dict:
    a = r.action
    by_clause = a.kind == "clause"
    return {**player(m, a.buy), "ask": m.price.get(a.buy, a.cost), "bid": a.cost,
            "clause_from": m.owner.get(a.buy) if by_clause else None,
            "gain": r.d_pts, "per_million": r.per_million,
            "chance": round(r.p_better, 3),
            "trend": m.trend.get(a.buy),
            "placed": m.my_bid.get(a.buy),
            "done": a.buy in m.my_bid}


def sell_row(m, r, k) -> dict:
    """A sale in the plan, there to pay for its buys or a debt: what it
    raises (Market.fetches), the offer standing on him and whether to take
    it tonight or (done for tonight) wait, and the points it costs a
    million."""
    take = m.takes(k)
    return {**player(m, k), "proceeds": m.fetches(k), "offer": m.offer.get(k),
            "take": take, "gain": r.d_pts, "per_million": r.per_million,
            "done": m.route.get(k) == "listed" and not take}


def holding(u, b, k) -> dict:
    """One of your players as the board shows him: how he plays, what he
    is worth, what he cost, the points his sale costs per million it
    raises, and the offer standing on him, if any."""
    m, o = u.market, u.outlook
    exp, xi = o.xi
    sale = b.sale(k)
    return {**player(m, k), "xi": k in xi,
            "start": o.next_up.get(k, (0.0, 0.0))[1], "next": exp.get(k, 0.0),
            "season": o.season.get(k, 0.0), "value": m.value.get(k),
            "trend": m.trend.get(k), "paid": m.paid.get(k),
            "per_million": sale and sale.per_million,
            "offer": m.offer.get(k)}


def exposed(u) -> list[dict]:
    m, o = u.market, u.outlook
    return [{**player(m, k), "clause": m.clause[k], "by": by,
             "xi": k in o.xi.players, "season": o.season.get(k, 0.0)}
            for k, by in at_risk(u)]


def report(u, b, fielded: list[str], lock_at=None) -> dict:
    """The board as the phone shows it; every choice in it is decide.board's.
    The line-up is the plan's: the best eleven once its moves are made,
    against the one fielded now."""
    o, m, mine, base = u.outlook, u.market, u.mine, b.base
    exp, xi = o.xi
    plan_xi = u.after(*(r.action for r in b.plan)).outlook.xi
    then = plan_xi.expected
    chg = xi_change(fielded, plan_xi.ranked())
    sold = {k for r in b.plan for k in r.action.sell}
    todo = []
    if chg["in"] or chg["out"]:
        todo.append({"what": "field", "label": "field " + ", ".join(
                         player(m, k)["name"] for k in chg["in"]), "legal": chg["legal"],
                     "on": [player(m, k)["name"] for k in chg["in"]],
                     "off": [player(m, k)["name"] for k in chg["out"] if k not in sold],
                     "gain": (sum(then.get(k, 0.0) for k in chg["in"])
                              - sum(exp.get(k, 0.0) for k in chg["out"]))
                     if chg["legal"] else None})
    names = {k: player(m, k)["name"] for k in m.name}
    todo += [{"what": "buy" if r.action.buy else "sell", "label": r.action.label(names),
              **(buy_row(m, r) if r.action.buy else sell_row(m, r, r.action.sell[0]))}
             for r in b.plan]
    backup = [buy_row(m, r) for r in b.others][:BACKUPS]
    lo, hi = base.band(u.me)
    return {
        "generated_at": run_now().strftime("%Y-%m-%dT%H:%MZ"),
        "lock_at": lock_at.isoformat() if lock_at else None,
        "cash": m.cash, "cash_locked": m.locked_cash,
        "finish": round(base.expected_position(), 2),
        "p_win": round(base.position().get(1, 0.0), 3),
        "band": [lo, hi],
        "do": todo, "plan_gain": b.gain, "backup": backup,
        "cash_after": m.left([r.action for r in b.plan]),
        "ping": "; ".join(d["label"] for d in todo if not d.get("done")),
        "exposed": exposed(u),
        "squad": [holding(u, b, k) for k in sorted(
            mine, key=lambda k: (SLOT_ORDER.get(mine[k], 9), -exp.get(k, 0.0)))],
        "standings": [
            {"manager": mgr, "me": mgr == u.me,
             "now": u.state.carried.get(mgr, 0.0), "mean": base.mean(mgr),
             "lo": base.band(mgr)[0], "hi": base.band(mgr)[1],
             "cash": m.cash if mgr == u.me else u.rival_cash.get(mgr, 0.0),
             "p_above": None if mgr == u.me else base.beat(mgr)}
            for mgr in sorted(u.state.squads, key=lambda g: -base.mean(g))],
    }


def _selftest() -> None:
    from dataclasses import replace

    from decide import Action, Move, Universe
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
                  forecaster=Bootstrap(perb),
                  market=Market(cash=10e6, pos={**sqb, "cand": "MED", "twin": "MED"},
                                price={"cand": 5e6, "twin": 5e6},
                                value={"dead": 1e6, "star": 20e6}))
    b = board(ub)
    rows = {r.action.buy or r.action.sell: r for r in ub.rank(ub.candidates()).rows}
    assert rows[("star",)].d_pts < -20 and -5 < rows[("dead",)].d_pts < 5, \
        "a sale is scored like any move"
    bought = [p.action.buy for p in b.plan if p.action.buy]
    assert bought and set(bought) <= {"cand", "twin"}, bought
    assert sum(p.action.net for p in b.plan) <= ub.market.cash, b.plan
    sold = [k for p in b.plan for k in p.action.sell]
    assert len(sold) == len(set(sold)), sold
    assert b.gain >= max(r.d_pts for r in b.plan) - 5.0, (b.gain, b.plan)
    assert all(r not in b.plan and verdict(r) is None for r in b.others)

    doc = report(ub, b, [])
    after = ub.after(*(r.action for r in b.plan))
    field = doc["do"][0]
    sold = {k for r in b.plan for k in r.action.sell}
    assert field["what"] == "field" and set(field["on"]) == {
        player(ub.market, k)["name"] for k in after.outlook.xi.ranked()}, \
        "the line-up is the plan's: the squad after its moves"
    assert not {player(ub.market, k)["name"] for k in sold} & set(field["on"] + field["off"]), \
        "a sold player is neither fielded nor benched"
    assert all("gain" in d for d in doc["do"]), "every move says its points"
    assert [d["what"] for d in doc["do"]][:1] == ["field"], doc["do"]
    assert {d["name"].lower() for d in doc["do"] if d["what"] == "buy"} == set(bought)
    assert all(b["name"].lower() not in bought for b in doc["backup"])
    assert doc["cash_after"] == ub.market.left([r.action for r in b.plan])
    rich = replace(ub, rival_cash={"riv": 25e6},
                   market=replace(ub.market, clause={"star": 20e6, "dead": 30e6}))
    assert [(e["name"].lower(), e["by"]) for e in exposed(rich)] == [("star", ["riv"])], \
        "exposed: what a rival can take now and can afford"
    assert all(CONFIDENCE <= d["chance"] <= 1.0 for d in doc["do"] + doc["backup"]
               if "chance" in d), "only moves that clear the bar are shown"
    assert [s["pos"] for s in doc["squad"]][0] == "POR", doc["squad"]
    assert sum(s["xi"] for s in doc["squad"]) == 11
    assert [r["manager"] for r in doc["standings"]][0] in ("me", "riv")
    assert doc["standings"][[r["me"] for r in doc["standings"]].index(True)][
        "p_above"] is None
    json.dumps(doc)
    assert doc["ping"] == "; ".join(d["label"] for d in doc["do"]), \
        "the ping is each move's Action.label, not words of its own"
    assert doc["do"][0]["label"].startswith("field ")

    ub = replace(ub, market=replace(ub.market, my_bid={bought[0]: 4e6},
                                    route={"dead": "listed"}))
    sell_dead = Move(Action("sell", sell=("dead",), proceeds=1e6), 0.5, p_better=0.8)
    doc = report(ub, b._replace(plan=b.plan + [sell_dead]), [])
    buys = {d["name"].lower(): d for d in doc["do"] if d["what"] == "buy"}
    assert buys[bought[0]]["done"] and buys[bought[0]]["placed"] == 4e6, buys
    assert all(not d["done"] and d["placed"] is None
               for n, d in buys.items() if n != bought[0]), buys
    assert [(d["name"].lower(), d["done"], d["proceeds"]) for d in doc["do"]
            if d["what"] == "sell"] == [("dead", True, 1e6)], doc["do"]

    def sale(offer):
        mk = replace(ub.market, offer={"dead": offer}, nights_left=2,
                     offer_ratios=(0.9, 1.0, 1.0, 1.1))
        doc = report(replace(ub, market=mk), b._replace(plan=[sell_dead]), [])
        return (next(d for d in doc["do"] if d["what"] == "sell"),
                doc["ping"].split("; ")[-1])
    (take, said), (wait, quiet) = sale(1.1e6), sale(0.9e6)
    assert take["take"] and not take["done"] and take["offer"] == take["proceeds"] == 1.1e6
    assert said == "sell dead", said
    assert not wait["take"] and wait["done"] and wait["proceeds"] > wait["offer"] == 0.9e6, \
        "waiting: the sale raises what waiting is worth"
    assert "Dead" not in quiet, quiet
    assert set(take) == {"what", "label", "name", "pos", "proceeds", "offer", "take",
                         "gain", "per_million", "done"}

    print("sim self-test OK")


def main() -> None:
    u = universe()
    if len(u.state.squads) < 2 or not u.state.jornadas:
        print("sim: nothing to simulate (%d squads, %d jornadas left)"
              % (len(u.state.squads), len(u.state.jornadas)))
        return
    b = board(u)
    REPORTS.mkdir(exist_ok=True)
    (REPORTS / "decisions.json").write_text(json.dumps(
        report(u, b, app_fielded(u.mine, u.market.name), load_deadline()),
        ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("wrote %s" % (REPORTS / "decisions.json"))


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        raise SystemExit(0)
    main()
