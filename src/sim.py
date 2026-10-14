"""The simulation stage: rank and plan the moves (decide.board), record
what they decided for the run (funnel, forecast, params, sim_*), and write
decisions.json, the phone's copy of the board, from the board views
(schema.sql: board_json)."""
from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from typing import Any

from assemble import built, universe
from decide import CONFIDENCE, Board, Universe, board, verdict
from ffcore.db import record, rows, this_run
from ffcore.forecast import expected_points
from ffcore.tidy import REPORTS

__all__ = ["board_doc", "forecast", "funnel", "plan", "plan_head", "players", "record_run",
           "standings"]

Doc = dict[str, Any]  # a JSON object of decisions.json


def _selftest() -> None:
    from dataclasses import replace

    import tempfile
    from contextlib import closing
    from pathlib import Path

    from decide import Action, Move
    from ffcore.db import connect
    from ffcore.market import Market
    from ffcore.tidy import store_sql, tables_in
    from ffcore.forecast import Bootstrap
    from ffcore.season import LeagueState

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

    def report(u: Universe, bd: Board, fielded: Sequence[str] = ()) -> Doc:
        """The board the views make of this universe's run, its market's
        facts standing in for player_facts and cash (assemble tests those)."""
        m = u.market
        with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
            with closing(connect()) as db, db:
                db.execute("DROP VIEW player_facts")
                db.execute("CREATE TABLE player_facts (player, name, pos, value, owner, clause,"
                           " price, route, my_bid, offer, paid, trend)")
                db.executemany("INSERT INTO player_facts VALUES (%s)" % ", ".join("?" * 12), [
                    (k, k, m.pos.get(k), m.value.get(k), m.owner.get(k), m.clause.get(k),
                     m.price.get(k), m.route.get(k), m.my_bid.get(k), m.offer.get(k),
                     m.paid.get(k), m.trend.get(k)) for k in set(m.pos) | set(u.mine)])
                db.execute("DROP VIEW cash")
                db.execute("CREATE TABLE cash (manager, cash)")
                db.executemany("INSERT INTO cash VALUES (?, ?)",
                               [(u.me, m.cash), *u.rival_cash.items()])
                db.executemany("INSERT INTO crosswalk (player_id, app_id) VALUES (?, ?)",
                               [(k, k) for k in fielded])
            store_sql("api_lineup", [{"observed_at": "2026-08-01T0000Z", "player_id": k}
                                     for k in fielded])
            record_run(u, bd, 7)
            return board_doc()

    ROW = {"what", "label", "step", "amount", "done", "gain", "per", "facts"}
    doc = report(ub, b)
    after = ub.after(*(r.action for r in b.plan))
    field = doc["do"][0]
    gone = {k for r in b.plan for k in r.action.sell}
    on = field["label"].removeprefix("field ").split(", ")
    benched = dict((f[0], f[1]) for f in field["facts"]).get("bench", "")
    assert field["what"] == "field" and {n.lower() for n in on} == set(after.outlook.xi.ranked()) \
        and on[0].lower() == after.outlook.xi.ranked()[0], \
        "the line-up is the plan's: the squad after its moves"
    assert not gone & {n.lower() for n in on + benched.split(", ")}, \
        "a sold player is neither fielded nor benched"
    assert (field["step"], field["gain"], field["per"]) == ("check line-up", None, "next"), \
        "a line-up the app has not given is a step to take, in the row"
    now = list(ub.outlook.xi.ranked())
    known = report(ub, b, now)["do"][0]
    came = set(after.outlook.xi.players) - set(now)
    left = set(now) - set(after.outlook.xi.players) - gone
    assert {n.lower() for n in known["label"].removeprefix("field ").split(", ")} == came \
        and {n.lower() for f in known["facts"] for n in f[1].split(", ")} == left, known
    assert known["step"] is None and known["gain"] is not None, known
    for d in doc["do"]:
        assert set(d) >= ROW and not set(d) - ROW - {"name", "pos"}, d
    assert all("gain" in d for d in doc["do"]), "every move says its points"
    assert [d["what"] for d in doc["do"]][:1] == ["field"], doc["do"]
    assert {d["name"].lower() for d in doc["do"] if d["what"] == "buy"} == set(bought)
    assert "backup" not in doc, "the plan is the one judgement; nothing second-guesses it"
    assert doc["cash_after"] == ub.market.left([r.action for r in b.plan])
    rich = replace(ub, rival_cash={"riv": 25e6},
                   market=replace(ub.market, clause={"star": 20e6, "dead": 30e6}))
    exposed = report(rich, b)["exposed"]
    assert [e["name"].lower() for e in exposed] == ["star", "dead"], \
        "exposed: every player with a clause, most valuable first"
    assert all("by" not in e for e in exposed), \
        "no 'who can afford him': a rival's cash is no limit when sales to the game pay at once"
    assert all(CONFIDENCE <= v <= 1.0 for d in doc["do"] for _, v, unit in d["facts"]
               if unit == "seasons"), "only moves that clear the bar are shown"
    assert report(ub, b._replace(plan=[]))["cash_after"] is None, \
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
    doc = report(ub, b._replace(plan=b.plan + [sell_dead], rows=b.rows + [sell_dead]))
    buys = {d["name"].lower(): d for d in doc["do"] if d["what"] == "buy"}
    assert buys[bought[0]]["done"] and (buys[bought[0]]["step"], buys[bought[0]]["amount"]) \
        == ("bid in", 4e6), buys
    assert all(not d["done"] and d["step"] == "bid up to"
               for n, d in buys.items() if n != bought[0]), buys
    assert [(d["name"].lower(), d["done"], d["step"], d["amount"]) for d in doc["do"]
            if d["what"] == "sell"] == [("dead", True, "listed", None)], doc["do"]
    unlisted = report(replace(ub, market=replace(ub.market, route={})),
                      b._replace(plan=[sell_dead], rows=b.rows + [sell_dead]))
    assert [(d["step"], d["done"]) for d in unlisted["do"] if d["what"] == "sell"] \
        == [("list", False)]

    def sale(offer: float) -> tuple[Doc, str]:
        mk = replace(ub.market, offer={"dead": offer}, nights_left=2,
                     offer_ratios=(0.9, 1.0, 1.0, 1.1))
        doc = report(replace(ub, market=mk), b._replace(plan=[sell_dead], rows=b.rows + [sell_dead]))
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

    ranked = funnel(ub, b, 7)
    assert [r["rank"] for r in ranked] == list(range(len(b.rows))) \
        and {r["run_id"] for r in ranked} == {7}, "every ranked move, in board order"
    assert [r["in_plan"] for r in ranked] == [int(m in b.plan) for m in b.rows]
    assert [r["verdict"] for r in ranked] == [verdict(m) for m in b.rows], \
        "why a move fails the bar, or NULL when it clears it"
    mv = next(m for m in b.rows if m.action.buy == "cand")
    got = next(r for r in ranked if r["buy"] == "cand")
    assert (got["gain"], got["p_better"], got["per_million"], got["cost"], got["label"]) \
        == (mv.d_pts, mv.p_better, mv.per_million, mv.action.cost,
            mv.action.label(ub.market.names)), got
    sale_row = next(r for r in ranked if r["kind"] == "sell")
    assert sale_row["buy"] is None and sale_row["sell"], sale_row

    from ffcore.schedule import Factors
    star = Factors(6.0, 1.1, 0.9, 0.5)
    fc = forecast(replace(ub, factors={3: {"star": star}}), 7)
    assert fc == [{"run_id": 7, "jornada": 3, "player": "star", "ppm": 6.0, "fixture": 1.1,
                   "picked": 0.9, "fit": 0.5, "points": star.cell[0], "plays": star.cell[1],
                   "expected": expected_points(star.cell)}], fc

    sim = standings(ub, b, 7)
    me_row = next(r for r in sim if r["me"])
    assert len(sim) == 2 and me_row["p_above"] is None and me_row["manager"] == "me"
    assert (me_row["finish"], me_row["p_first"]) == (
        b.base.expected_position(), b.base.position().get(1, 0.0))
    riv = next(r for r in sim if not r["me"])
    assert riv["p_above"] == 1.0 - b.base.beat("riv") and riv["finish"] == \
        b.base.expected_position("riv"), "every manager's finish, not only yours"
    assert (riv["lo"], riv["hi"]) == b.base.band("riv") and riv["mean"] == b.base.mean("riv")
    who = {r["player"]: r for r in players(ub, b, 7)}
    plan_xi = ub.after(*(r.action for r in b.plan)).outlook.xi.players
    assert set(who) == set(ub.mine) | set(bought), "your squad and the plan's buys"
    assert sum(r["xi"] for r in who.values()) == 11 \
        and {k for k, r in who.items() if r["plan_xi"]} == plan_xi
    assert who["star"]["next"] == ub.outlook.xi.expected["star"] \
        and who["star"]["season"] == ub.outlook.season["star"]
    dead = b.sale("dead")
    assert dead is not None and who["dead"]["raises"] == ub.market.fetches("dead") \
        and who["dead"]["per_million"] == dead.per_million, who["dead"]
    assert who[bought[0]]["raises"] is None, "a buy is not yours to sell"
    assert plan(ub, b, 7) == [{"run_id": 7, "ord": i, "rank": b.rows.index(m)}
                              for i, m in enumerate(b.plan)]
    assert plan_head(b, 7) == {"run_id": 7, "plan_gain": b.gain}

    print("sim self-test OK")


def funnel(u: Universe, b: Board, run: int) -> list[Doc]:
    """Every ranked move, in board order: the funnel's first three steps
    (candidates, rank, verdict) and whether the plan took it."""
    return [{"run_id": run, "rank": i, "kind": m.action.kind,
             "buy": m.action.buy or None, "sell": ",".join(m.action.sell) or None,
             "label": m.action.label(u.market.names),
             "cost": m.action.cost, "proceeds": m.action.proceeds,
             "gain": m.d_pts, "p_better": m.p_better, "per_million": m.per_million,
             "verdict": verdict(m), "in_plan": int(m in b.plan)}
            for i, m in enumerate(b.rows)]


def forecast(u: Universe, run: int) -> list[Doc]:
    """Every forecast cell that has factors, factor by factor
    (ffcore.schedule.Factors), with the expected points they make."""
    return [{"run_id": run, "jornada": j, "player": k, "ppm": f.ppm, "fixture": f.fixture,
             "picked": f.picked, "fit": f.fit, "points": f.cell[0], "plays": f.cell[1],
             "expected": expected_points(f.cell)}
            for j, layer in u.factors.items() for k, f in layer.items()]


def standings(u: Universe, b: Board, run: int) -> list[Doc]:
    """Each manager's simulated season if you do nothing (Board.base): mean
    points and their 10th-90th percentile band, expected final position,
    the chance of first, and the chance he finishes above you."""
    base = b.base
    return [{"run_id": run, "manager": mgr, "me": int(mgr == u.me),
             "now": u.state.carried.get(mgr, 0.0), "mean": base.mean(mgr),
             "lo": base.band(mgr)[0], "hi": base.band(mgr)[1],
             "finish": base.expected_position(mgr), "p_first": base.position(mgr).get(1, 0.0),
             "p_above": None if mgr == u.me else 1.0 - base.beat(mgr)}
            for mgr in u.state.squads]


def players(u: Universe, b: Board, run: int) -> list[Doc]:
    """Your squad and the plan's buys as the model sees them: the chance he
    plays next, his expected points next and over the season, whether he is
    in the best eleven now and once the plan is made, and, if yours, what
    selling him raises (Market.fetches), whether that takes tonight's offer,
    and the points his sale costs per million."""
    m, o = u.market, u.outlook
    exp, xi = o.xi
    plan_xi = u.after(*(r.action for r in b.plan)).outlook.xi.players
    keys = list(u.mine) + [r.action.buy for r in b.plan if r.action.buy and r.action.buy not in u.mine]
    return [{"run_id": run, "player": k, "start": o.next_up.get(k, (0.0, 0.0))[1],
             "next": exp.get(k, 0.0), "season": o.season.get(k, 0.0),
             "mine": int(k in u.mine), "xi": int(k in xi), "plan_xi": int(k in plan_xi),
             "raises": m.fetches(k) if k in u.mine else None,
             "takes": int(m.takes(k)) if k in u.mine else None,
             "per_million": (sale := b.sale(k)) and sale.per_million}
            for k in keys]


def plan(u: Universe, b: Board, run: int) -> list[Doc]:
    """The plan's moves in order, each by its rank in the funnel."""
    return [{"run_id": run, "ord": i, "rank": b.rows.index(m)} for i, m in enumerate(b.plan)]


def plan_head(b: Board, run: int) -> Doc:
    """What the plan gains, its moves made together."""
    return {"run_id": run, "plan_gain": b.gain}


def record_run(u: Universe, b: Board, run: int, params: Sequence[Doc] = ()) -> None:
    """Everything the run decided, into its tables: the board views read them."""
    record(funnel(u, b, run), "funnel")
    record(forecast(u, run), "forecast")
    record([{"run_id": run, **r} for r in params], "params")
    record(standings(u, b, run), "sim_standings")
    record(players(u, b, run), "sim_players")
    record(plan(u, b, run), "sim_plan")
    record([plan_head(b, run)], "sim_runs")


def board_doc() -> Doc:
    """decisions.json as board_json makes it, from the last run's rows."""
    (doc,), = rows("SELECT json FROM board_json")
    got: Doc = json.loads(doc)
    return got


def main() -> None:
    u = universe()
    if len(u.state.squads) < 2 or not u.state.jornadas:
        print("sim: nothing to simulate (%d squads, %d jornadas left)"
              % (len(u.state.squads), len(u.state.jornadas)))
        return
    run = this_run()
    if run is None:
        raise SystemExit("sim: the board is the views over a run's rows; run it as run.py sim")
    record_run(u, board(u), run, built().params)
    REPORTS.mkdir(exist_ok=True)
    (REPORTS / "decisions.json").write_text(json.dumps(
        board_doc(), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("wrote %s" % (REPORTS / "decisions.json"))


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        raise SystemExit(0)
    main()
