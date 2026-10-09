from __future__ import annotations

import json
import sys
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

from assemble import universe
from decide import CONFIDENCE, Board, Move, Universe, at_risk, board
from ffcore.market import Market
from ffcore.league import app_fielded
from ffcore.rules import POSITIONS
from ffcore.clock import run_now
from ffcore.jornadas import load_deadline
from ffcore.tidy import REPORTS

__all__ = ["report"]

ROW = {"what", "label", "step", "amount", "done", "gain", "per", "facts"}
Doc = dict[str, Any]  # a JSON object of decisions.json


def xi_change(marked: Sequence[str], best: Iterable[str]) -> Doc:
    eleven = list(best)
    if len(marked) != len(eleven) or not marked:
        return {"legal": False, "in": eleven, "out": []}
    have, want = set(marked), set(eleven)
    return {"legal": True, "in": [k for k in eleven if k not in have],
            "out": [k for k in marked if k not in want]}


def player(m: Market, k: str) -> dict[str, str]:
    return {"name": m.shown(k), "pos": m.pos.get(k, "")}


def row(what: str, label: str, step: str | None, amount: float | None, done: bool,
        gain: float | None, per: str, facts: list[tuple[str, Any, str]]) -> Doc:
    """One line of the board, every decision in it made here: the move
    (Action.label), the step it takes in the app and the amount that step
    names, whether it is done, its points (per: "season" or "next") and
    the numbers that made it, as (label, value, unit)."""
    return {"what": what, "label": label, "step": step, "amount": amount, "done": done,
            "gain": gain, "per": per, "facts": [f for f in facts if f[1] is not None]}


def buy_row(m: Market, r: Move, label: str) -> Doc:
    a = r.action
    placed = m.my_bid.get(a.buy)
    return {**player(m, a.buy), **row(
        "buy", label, "bid up to" if placed is None else "bid in",
        a.cost if placed is None else placed, placed is not None, r.d_pts, "season",
        [("asking", m.price.get(a.buy, a.cost), "money"), ("", r.per_million, "per_million"),
         ("better off in", round(r.p_better, 3), "seasons"), ("", m.trend.get(a.buy), "trend")])}


def sell_row(m: Market, r: Move, k: str, label: str) -> Doc:
    """A sale in the plan, there to pay for its buys or a debt: take the
    offer standing on him tonight, or wait for what waiting is worth
    (Market.fetches), or list him; done once nothing is left to do tonight."""
    take, listed, offer = m.takes(k), m.route.get(k) == "listed", m.offer.get(k)
    step = ("take offer" if take else "wait" if offer is not None
            else "listed" if listed else "list")
    return {**player(m, k), **row(
        "sell", label, step, m.fetches(k) if offer is not None else None,
        listed and not take, r.d_pts, "season",
        [("raises", m.fetches(k), "money"), ("", r.per_million, "per_million"),
         ("offer", None if take else offer, "money")])}


def holding(u: Universe, b: Board, k: str) -> Doc:
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


def exposed(u: Universe) -> list[Doc]:
    return [{**player(u.market, k), "clause": u.market.clause[k]} for k in at_risk(u)]


def report(u: Universe, b: Board, fielded: Sequence[str],
           lock_at: datetime | None = None) -> Doc:
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
        off = ", ".join(player(m, k)["name"] for k in chg["out"] if k not in sold)
        todo.append(row(
            "field", "field " + ", ".join(player(m, k)["name"] for k in chg["in"]),
            None if chg["legal"] else "check line-up", None, False,
            (sum(then.get(k, 0.0) for k in chg["in"])
             - sum(exp.get(k, 0.0) for k in chg["out"])) if chg["legal"] else None,
            "next", [("bench", off or None, "names")]))
    names = m.names
    todo += [buy_row(m, r, r.action.label(names)) if r.action.buy
             else sell_row(m, r, r.action.sell[0], r.action.label(names))
             for r in b.plan]
    lo, hi = base.band(u.me)
    return {
        "generated_at": run_now().strftime("%Y-%m-%dT%H:%MZ"),
        "lock_at": lock_at.isoformat() if lock_at else None,
        "cash": m.cash, "cash_locked": m.locked_cash,
        "finish": round(base.expected_position(), 2),
        "p_win": round(base.position().get(1, 0.0), 3),
        "band": [lo, hi],
        "do": todo, "plan_gain": b.gain,
        "cash_after": m.left([r.action for r in b.plan]) if b.plan else None,
        "ping": "; ".join(d["label"] for d in todo if not d.get("done")),
        "exposed": exposed(u),
        "squad": [holding(u, b, k) for k in sorted(
            mine, key=lambda k: (POSITIONS.index(mine[k]) if mine[k] in POSITIONS else 9, -exp.get(k, 0.0)))],
        "standings": [
            {"manager": mgr, "me": mgr == u.me,
             "now": u.state.carried.get(mgr, 0.0), "mean": base.mean(mgr),
             "lo": base.band(mgr)[0], "hi": base.band(mgr)[1],
             "cash": m.cash if mgr == u.me else u.rival_cash.get(mgr, 0.0),
             "estimated": mgr != u.me,
             "p_above": None if mgr == u.me else 1.0 - base.beat(mgr)}
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

    doc = report(ub, b, [])
    after = ub.after(*(r.action for r in b.plan))
    field = doc["do"][0]
    gone = {k for r in b.plan for k in r.action.sell}
    on = field["label"].removeprefix("field ").split(", ")
    benched = dict((f[0], f[1]) for f in field["facts"]).get("bench", "")
    assert field["what"] == "field" and set(on) == {
        player(ub.market, k)["name"] for k in after.outlook.xi.ranked()}, \
        "the line-up is the plan's: the squad after its moves"
    assert not {player(ub.market, k)["name"] for k in gone} & set(on + benched.split(", ")), \
        "a sold player is neither fielded nor benched"
    assert (field["step"], field["gain"], field["per"]) == ("check line-up", None, "next"), \
        "a line-up the app has not given is a step to take, in the row"
    xi = list(after.outlook.xi.ranked())
    spare = next(k for k in after.mine if k not in xi)
    known = report(ub, b, xi[:-1] + [spare])["do"][0]
    assert known["step"] is None and known["gain"] is not None, known
    for d in doc["do"]:
        assert set(d) >= ROW and not set(d) - ROW - {"name", "pos"}, d
    assert all("gain" in d for d in doc["do"]), "every move says its points"
    assert [d["what"] for d in doc["do"]][:1] == ["field"], doc["do"]
    assert {d["name"].lower() for d in doc["do"] if d["what"] == "buy"} == set(bought)
    assert "backup" not in doc, "the plan is the one judgement; nothing second-guesses it"
    assert doc["cash_after"] == ub.market.left([r.action for r in b.plan])
    from assemble import open_clauses
    from ffcore.tidy import typed
    assert open_clauses(typed("api_teams", [
        {"key": "p", "buyout": "9", "buyout_until": "2099-01-01T00:00:00+02:00"},
        {"key": "q", "buyout": ""}])) == {"p": 9.0}, \
        "buyout_until protects no one: Luismi Cruz and Yamal were taken before it"
    rich = replace(ub, rival_cash={"riv": 25e6},
                   market=replace(ub.market, clause={"star": 20e6, "dead": 30e6}))
    assert [e["name"].lower() for e in exposed(rich)] == ["star", "dead"], \
        "exposed: every player with a clause, most valuable first"
    assert all("by" not in e for e in exposed(rich)), \
        "no 'who can afford him': a rival's cash is no limit when sales to the game pay at once"
    assert all(CONFIDENCE <= v <= 1.0 for d in doc["do"] for _, v, unit in d["facts"]
               if unit == "seasons"), "only moves that clear the bar are shown"
    assert report(ub, b._replace(plan=[]), [])["cash_after"] is None, \
        "no cash line when the plan moves no money"
    assert [r["estimated"] for r in doc["standings"] if r["me"]] == [False]
    riv_row = next(r for r in doc["standings"] if not r["me"])
    assert riv_row["p_above"] == 1.0 - b.base.beat("riv"), \
        "above you: the chance he finishes above you, not that you beat him"
    assert all(r["estimated"] for r in doc["standings"] if not r["me"]), \
        "rival cash is estimated from the feed"
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
    assert buys[bought[0]]["done"] and (buys[bought[0]]["step"], buys[bought[0]]["amount"]) \
        == ("bid in", 4e6), buys
    assert all(not d["done"] and d["step"] == "bid up to"
               for n, d in buys.items() if n != bought[0]), buys
    assert [(d["name"].lower(), d["done"], d["step"], d["amount"]) for d in doc["do"]
            if d["what"] == "sell"] == [("dead", True, "listed", None)], doc["do"]
    unlisted = report(replace(ub, market=replace(ub.market, route={})),
                      b._replace(plan=[sell_dead]), [])
    assert [(d["step"], d["done"]) for d in unlisted["do"] if d["what"] == "sell"] \
        == [("list", False)]

    def sale(offer: float) -> tuple[Doc, str]:
        mk = replace(ub.market, offer={"dead": offer}, nights_left=2,
                     offer_ratios=(0.9, 1.0, 1.0, 1.1))
        doc = report(replace(ub, market=mk), b._replace(plan=[sell_dead]), [])
        return (next(d for d in doc["do"] if d["what"] == "sell"),
                doc["ping"].split("; ")[-1])
    (take, said), (wait, quiet) = sale(1.1e6), sale(0.9e6)
    def facts(d: Doc) -> dict[str, Any]:
        return {f[0]: f[1] for f in d["facts"]}
    assert (take["step"], take["amount"], take["done"]) == ("take offer", 1.1e6, False)
    assert facts(take)["raises"] == 1.1e6
    assert said == "sell dead", said
    assert wait["step"] == "wait" and wait["done"] and wait["amount"] > 0.9e6, \
        "waiting: done for tonight, and the sale raises what waiting is worth"
    assert facts(wait)["offer"] == 0.9e6 and facts(wait)["raises"] == wait["amount"]
    assert "Dead" not in quiet, quiet

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
