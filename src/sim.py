"""The sim stage: the decision made and kept (decision.record: decision_plan,
decision_option, decision_*), the run's forecast moment (sim_runs) and params, and
decisions.json, the phone's copy of the board, written from the board views
(schema.sql: board_json), which read the decision's contract only."""
from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from contextlib import closing
from typing import Any

from assemble import built
import decision
from ffcore.db import connect, cut, record, rows, this_run
from ffcore.jornadas import rounds
from ffcore.tidy import REPORTS

__all__ = ["board_doc", "record_run"]

Doc = dict[str, Any]  # a JSON object of decisions.json

def _selftest() -> None:
    _selftest_board_contract()
    import math
    import tempfile
    from pathlib import Path
    from statistics import NormalDist

    from ffcore.db import connect
    from ffcore.fixtures import seed_league
    from ffcore.names import Name
    from ffcore.rules import DEBT_SHARE
    from ffcore.tidy import store_sql, tables_in

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
    def put(w: dict[str, Any], col: str, vals: dict[str, Any]) -> dict[str, Any]:
        """The league with this column of seen as vals (players not in it have none)."""
        p = {k: {**r, col: None} for k, r in w["p"].items()}
        for k, v in vals.items():
            p.setdefault(k, {})[col] = v
        return {**w, "p": p}

    def get(w: dict[str, Any], col: str) -> dict[str, Any]:
        return {k: r[col] for k, r in w["p"].items() if r.get(col) is not None}

    def mine(w: dict[str, Any]) -> list[str]:
        return [k for k, r in w["p"].items() if r.get("owner") == "me"]

    def seed(w: dict[str, Any], names: dict[str, str] | None = None) -> None:
        """The league's rows, in the current tables; his name shown as Name makes it of his key."""
        seed_league("me", w["cash"], {k: {**r, "name": Name((names or {}).get(k, k)).shown} for k, r in w["p"].items()},
                    w["cells"])

    def net(steps: Sequence[tuple[Any, ...]]) -> float:
        return sum(a if kind == "buy" else -a for _, kind, _, a, *_ in steps)

    # A league is its seed_league rows: your cash, a row a player (columns of seen), the cells.
    World = dict[str, Any]
    ub: World = {"cash": 10e6, "cells": perb, "p": {
        **{k: {"pos": p, "owner": "me"} for k, p in sqb.items()},
        **{k: {"pos": p, "owner": "riv"} for k, p in riv.items()},
        "cand": {"pos": "MED", "price": 5e6}, "twin": {"pos": "MED", "price": 5e6}}}
    ub = put(ub, "value", {"dead": 1e6, "star": 20e6})

    def decided(w: World) -> list[tuple[Any, ...]]:
        with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
            seed(w)
            return decision.plan()
    acts = decided(ub)
    bought = [k for _, kind, k, *_ in acts if kind == "buy"]
    assert sorted(bought) == ["cand", "twin"], "each 4 a jornada for a 2.7 in the eleven"
    sold = [k for _, kind, k, *_ in acts if kind == "sell"]


    Plan = Sequence[tuple[str, str, float, float, float]]          # kind, player, amount, win, worth
    Option = tuple[str, str, float, float, float, float]    # kind, player, amount, win, gain, worth

    def report(w: World, fielded: Sequence[str] = (),
               ratios: Sequence[float] = (),
               waits: Sequence[tuple[str, float]] = (), nights: int = 0,
               listings: Sequence[tuple[str, str]] = (),
               bids: Sequence[tuple[str, int]] = (), names: dict[str, str] | None = None,
               plan: Plan | None = None, options: Sequence[Option] = ()) -> Doc:
        """The board the views make of this universe's run, its market's
        facts standing in for player_facts and cash, ratios for the game's
        past offers, waits what turning down an offer on a player of yours is worth, nights the
        game's offers still to come before the lock (the first a day after
        2026-08-01), listings when each of yours ends, bids how many bids
        each on the game's market has (yours among them, if you have one),
        names the names shown where they are not the player's key; plan, a
        plan of these moves (each its own step) in place of the decision's,
        options, moves valued in place of the decision's."""
        shown = {k: Name((names or {}).get(k, k)).shown for k in w["p"]}
        held = {k: r for k, r in w["p"].items() if r.get("owner") in (None, "me")}
        with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
            with closing(connect()) as db, db:
                db.execute("DROP VIEW player_facts")
                db.execute("CREATE TABLE player_facts (player, name, pos, value, owner, clause,"
                           " price, route, my_bid, offer, paid, trend)")
                db.executemany("INSERT INTO player_facts VALUES (%s)" % ", ".join("?" * 12), [
                    (k, shown[k], r.get("pos"), r.get("value"), None, r.get("clause"), r.get("price"),
                     r.get("route"), r.get("my_bid"), r.get("offer"), r.get("paid"), r.get("trend"))
                    for k, r in held.items()])
                db.execute("DROP VIEW sale_odds")
                db.execute("CREATE TABLE sale_odds (player, offer, waiting, fetches)")
                db.executemany("INSERT INTO sale_odds VALUES (?, ?, NULL, ?)",
                               [(k, r["offer"], r.get("fetches")) for k, r in held.items() if r.get("offer") is not None])
                db.execute("DROP VIEW offer_ratios")
                db.execute("CREATE TABLE offer_ratios (created_at, ratio)")
                db.executemany("INSERT INTO offer_ratios VALUES ('', ?)", [(r,) for r in ratios])
                db.executemany("INSERT OR REPLACE INTO crosswalk (player_id, app_id) VALUES (?, ?)",
                               [(k, k) for k in dict.fromkeys([*fielded, *(k for k, _e in listings),
                                                                *(k for k, _n in bids)])])
                db.execute("DROP VIEW waiting")
                db.execute("CREATE TABLE waiting (player, kind, later)")
                db.executemany("INSERT INTO waiting VALUES (?, 'sale', ?)", waits)
                db.execute("DROP VIEW nights_left")
                db.execute("CREATE TABLE nights_left AS SELECT ? AS nights,"
                           " julianday('2026-08-02T20:24:00Z') AS first", (nights,))
            store_sql("api_standings", [{"observed_at": "2026-08-01T0000Z", "manager": "me"}])
            store_sql("api_market", [{"observed_at": "2026-08-01T0000Z", "player_id": k, "seller": "marketPlayerTeam",
                                      "expires_at": e} for k, e in listings]
                      + [{"observed_at": "2026-08-01T0000Z", "player_id": k, "seller": "marketPlayerLeague",
                          "sale_price": str(held.get(k, {}).get("price") or 1e6), "bids": str(n)} for k, n in bids])
            store_sql("api_lineup", [{"observed_at": "2026-08-01T0000Z", "player_id": k}
                                     for k in fielded])
            seed(w, names)
            record_run(7)
            with closing(connect()) as db, db:
                db.executemany("INSERT OR REPLACE INTO decision_option (run_id, kind, player, amount, win, gain, worth, in_plan)"
                               " VALUES (7, ?, ?, ?, ?, ?, ?, 0)", options)
                if plan is not None:
                    db.execute("DELETE FROM decision_plan WHERE run_id = 7")
                    db.execute("DELETE FROM dec_plan")
                    db.executemany("INSERT INTO dec_plan VALUES (?, ?, ?, ?, ?, ?)",
                                   [(i, *p) for i, p in enumerate(plan)])
                    db.execute(decision.KEEP_PLAN, (7,))
                db.execute("UPDATE decision_option SET in_plan = EXISTS (SELECT 1 FROM decision_plan p"
                           " WHERE p.run_id = 7 AND p.kind = decision_option.kind"
                           " AND p.player = decision_option.player) WHERE run_id = 7")
            return board_doc()

    def facts(d: Doc) -> dict[str, Any]:
        return {f[0]: f[1] for f in d["facts"]}

    ROW = {"what", "label", "step", "amount", "done", "gain", "per", "facts", "ranked"}
    doc = report(ub, nights=1)
    alike = [d for d in report(ub, nights=1, names={"cand": "Pablo", "twin": "Pablo"})["do"]
             if d["what"] == "buy"]
    assert len(alike) == len(bought) == 2, \
        ("a buy's asking and odds are his, not those of whoever shares his name", alike)
    field = doc["do"][0]
    now = ["star", *sorted(set(sqb) - {"star", "dead"})]       # 5.4 a jornada, then the 2.7s; dead 0.15 out
    on = field["label"].removeprefix("field ").split(", ")
    assert field["what"] == "field" and {n.lower() for n in on} == set(now) and on[0].lower() == now[0], \
        "the line-up is of the squad you hold now: tonight's buys are not yours until their auction closes"
    assert (field["step"], field["gain"], field["per"]) == ("check line-up", None, "next"), \
        "a line-up the app has not given is a step to take, in the row"
    for nights in (0, 1):
        assert not [d for d in report(ub, now, nights=nights)["do"] if d["what"] == "field"], \
            "set as the best eleven now: nothing to field, before the auctions or after (Dituro, 2026-10-30)"
    for d in doc["do"]:
        assert set(d) >= ROW and not set(d) - ROW - {"name", "pos", "act", "accepts"} - (
            {"asking", "win", "asking_win"} if d["what"] == "buy" else set()) - (
            {"cash_then", "skip"} if d["what"] in ("buy", "sell") else set()), d
    assert all("gain" in d for d in doc["do"]), "every move says its points"
    assert all(len(d["act"]) == 1 for d in doc["do"] if d["what"] in ("buy", "sell")) \
        and len(field["act"]) == 11 \
        and all(e["pos"] for e in field["act"]), \
        ("each line names the players it acts on, as the app knows them (act.py reads nothing else)",
         [(d["label"], d["act"]) for d in doc["do"]][:3])
    assert [d["what"] for d in doc["do"]][:1] == ["field"], doc["do"]
    assert {d["name"].lower() for d in doc["do"] if d["what"] == "buy"} == set(bought)
    assert doc["cash_after"] == ub["cash"] - net(acts)
    exposed = report(put(ub, "clause", {"star": 20e6, "dead": 30e6}))["exposed"]
    assert [e["name"].lower() for e in exposed] == ["star", "dead"], \
        "exposed: every player with a clause, most valuable first"
    assert report(ub, plan=[])["cash_after"] is None, "no cash line when the plan moves no money"
    assert [r["estimated"] for r in doc["standings"] if r["me"]] == [False]
    assert all(r["estimated"] for r in doc["standings"] if not r["me"]), \
        "rival cash is estimated from the feed"
    shown = sorted((d["name"].lower(), d["amount"]) for d in doc["do"] if d["what"] == "buy")
    assert shown == sorted((k, a) for _, kind, k, a, *_ in acts if kind == "buy"), \
        ("the plan's buys, each once at its bid, and no move it does not make: the plan is the one judgement", shown)
    assert all(abs(d["gain"] - facts(d)["points"]) < 1e-9 and "money" not in facts(d)
               for d in doc["do"] if d["what"] == "buy"), "its worth: its points, and no money counted as points"
    assert all(facts(d)["listed at"] == get(ub, "price")[d["name"].lower()]
               for d in doc["do"] if d["what"] == "buy"), \
        "what he is listed at, and the bid it takes"
    assert all((d["asking"], d["win"], d["asking_win"], facts(d)["chance to win"])
               == (get(ub, "price")[d["name"].lower()], 1.0, 1.0, 1.0) for d in doc["do"] if d["what"] == "buy"), \
        "each bid at its asking price too, and the chance of each (with no past auctions to go on, sure)"
    assert "lam" not in doc, "no money rate: money is a limit, not points"
    assert doc["buys"] and all(b["gain"] > 0 for b in doc["buys"]) \
        and [b["gain"] for b in doc["buys"]] == sorted((b["gain"] for b in doc["buys"]), reverse=True), \
        "the buys ranked: each that adds points, most first"
    assert {b["name"].lower() for b in doc["buys"]} <= set(get(ub, "price")) - {k for k, o in get(ub, "owner").items() if o != "me"}, \
        "only players you can buy: the game's market, never a rival's listing"
    assert {s["name"].lower() for s in doc["sells"]} == set(mine(ub)) \
        and [s["gain"] for s in doc["sells"]] == sorted((s["gain"] for s in doc["sells"]), reverse=True), \
        "every player of yours ranked, the fewest points lost first"
    plan_lines = {d["label"]: d for d in doc["do"] if d["ranked"]}
    assert {d["what"] for d in plan_lines.values()} <= {"buy", "sell"} \
        and all(d["ranked"] == (d["what"] in ("buy", "sell")) for d in doc["do"]), \
        "the plan's buys and sales are drawn in their rankings; the rest of the plan stands alone"
    assert all({k: r[k] for k in ("step", "amount", "facts", "accepts")}
               == {k: plan_lines[r["label"]][k] for k in ("step", "amount", "facts", "accepts")}
               for r in doc["buys"] + doc["sells"] if r["plan"]) \
        and sum(r["plan"] for r in doc["buys"] + doc["sells"]) == len(plan_lines), \
        "a move of the plan in its ranking as the plan's line says it"
    assert all(r["facts"] for r in doc["buys"] + doc["sells"]), "every ranked move says what it is, the plan's or not"
    assert all([r["more"] for r in doc[k]] == [i >= 3 and not r["plan"] for i, r in enumerate(doc[k])]
               for k in ("buys", "sells")) and any(r["more"] for r in doc["sells"]), \
        "past the first three of each ranking, the moves not the plan's fold away"
    assert {b["name"].lower() for b in doc["buys"] if b["plan"]} == set(bought), \
        "the plan's buys are marked in the ranking"
    assert all(b["what"] == "buy" and b["accepts"] and len(b["act"]) == 1 for b in doc["buys"]), \
        "each buy ranked is a bid to place on the phone, as the plan's are"
    assert all(len(s["act"]) == 1 and s["accepts"] == ([s["amount"]] if s["step"] == "take offer" else
                                                       [None] if s["what"] == "list" else [])
               for s in doc["sells"]), "each player ranked: his offer to take, or he listed, or as the plan says"
    assert "market" not in doc and "offers" not in doc, \
        "one list of lines, not a section per source"
    listing = doc["do"][-1]
    assert listing["what"] == "list" and set(listing["label"].removeprefix("list ").lower().split(", ")) \
        == set(mine(ub)) - set(sold), ("list every player of yours not listed and not sold", listing)
    assert doc["standings"][[r["me"] for r in doc["standings"]].index(True)]["p_above"] is None
    json.dumps(doc)
    assert doc["ping"] == "; ".join(d["label"] for d in doc["do"]), \
        "the ping is each move's label, not words of its own"

    def valued(w: World, total: float) -> World:
        """Your players worth total between them: the team value the debt is a share of."""
        return put(w, "value", {**get(w, "value"), **{k: total / len(mine(w)) for k in mine(w)}})

    rich = valued(ub, 100e6)
    rich_doc = report(rich)
    assert rich_doc["can_bid"] == max(0.0, rich["cash"] + DEBT_SHARE * 100e6
                                      - sum(d["amount"] for d in rich_doc["do"] if d["what"] == "buy")), \
        ("what you can bid: your cash and the debt the game allows (rules.DEBT_SHARE) less the plan's bids;"
         " sales do not count, made after the auctions")
    stray = put(put(rich, "pos", {**get(rich, "pos"), "ghost": "DEF"}), "my_bid", {"ghost": 3e6})
    assert abs(report(stray)["can_bid"]
               - (rich_doc["can_bid"] - 3e6)) < 1, \
        "a bid of yours the plan does not make is money you cannot bid again"
    cost = next(a for _, kind, k, a, *_ in acts if kind == "buy" and k == bought[0])
    in_plan = put(rich, "my_bid", {bought[0]: cost})
    assert report(in_plan)["can_bid"] == rich_doc["can_bid"], \
        "the plan's own bid is counted once, in the plan"
    pulled = [(d["what"], d["label"], d["step"], d["amount"], d["done"])
              for d in report(stray)["do"] if d["what"] == "withdraw"]
    assert pulled == [("withdraw", "withdraw bid on Ghost", "withdraw bid", 3e6, False)], \
        ("a bid of yours the plan does not make: take it back, or it may win beside the plan's", pulled)

    def bid_line(mine: float) -> tuple[Any, ...]:
        d = next(d for d in report(put(ub, "my_bid", {bought[0]: mine}))["do"]
                 if d["what"] == "buy" and d["name"].lower() == bought[0])
        return d["step"], d["amount"], d["done"], facts(d).get("your bid")
    assert bid_line(cost) == ("bid in", cost, True, None), "your bid is the plan's: done"
    assert bid_line(cost - 1e6) == ("bid up to", cost, False, cost - 1e6), \
        "your bid under the plan's: change it to the plan's"
    ub = put(put(ub, "my_bid", {bought[0]: cost}), "route", {"dead": "listed"})
    buys_planned: Plan = [("buy", k, a, 1.0, 1.0) for _, kind, k, a, *_ in acts if kind == "buy"]
    sell_dead: Plan = [("sell", "dead", 1e6, 1.0, 0.5)]
    dead_alone: Option = ("sell", "dead", 1e6, 1.0, 0.5, 0.5)
    doc = report(ub, plan=[*buys_planned, *sell_dead], options=[dead_alone])
    buys = {d["name"].lower(): d for d in doc["do"] if d["what"] == "buy"}
    assert buys[bought[0]]["done"] and (buys[bought[0]]["step"], buys[bought[0]]["amount"]) \
        == ("bid in", cost), buys
    assert all(not d["done"] and d["step"] == "bid up to"
               for n, d in buys.items() if n != bought[0]), buys
    assert [(d["name"].lower(), d["done"], d["step"], d["amount"]) for d in doc["do"]
            if d["what"] == "sell"] == [("dead", False, "sell after the auctions", 1e6)], \
        "beside the plan's bids: once they are won"
    yours, *others = [bought[0], *(n for n in buys if n != bought[0])]

    def hot(n_mine: int, n_other: int) -> dict[str, Any]:
        crowd = report(ub, bids=[(yours, n_mine), *((o, n_other) for o in others[:1])])
        return {d["name"].lower(): facts(d).get("bids") for d in crowd["do"] if d["what"] == "buy"}
    assert others, "the case: a buy of the plan you have not bid on"
    assert hot(1, 3) == {yours: "only yours", others[0]: "3", **{o: None for o in others[1:]}}, hot(1, 3)
    assert hot(4, 0) == {yours: "4, yours among them", others[0]: "none yet", **{o: None for o in others[1:]}}, \
        "how many bid on each: a crowd, only yours, or none yet"
    unlisted = report(put(ub, "route", {}), plan=sell_dead, options=[dead_alone])
    assert [(d["step"], d["done"]) for d in unlisted["do"] if d["what"] == "sell"] == [("list", False)]

    def offered_doc(offer: float, nights: int = 0) -> Doc:
        mk = put(put(ub, "offer", {"dead": offer}), "fetches", {"dead": max(offer, 1.0e6)})
        return report(mk, plan=sell_dead, options=[dead_alone],
                      waits=[("dead", 1.0e6)], nights=nights)

    def sale(offer: float, nights: int = 0) -> tuple[Doc, list[str]]:
        doc = offered_doc(offer, nights)
        return (next(d for d in doc["do"] if d["what"] == "sell"), doc["ping"].split("; "))
    (take, said), (wait, quiet) = sale(1.1e6), sale(0.9e6)
    assert [(s["step"], s["amount"], s["accepts"], s["offer"], s["waiting"])
            for o in (1.1e6, 0.9e6) for s in offered_doc(o)["sells"] if s["name"] == "Dead"] \
        == [("take offer", 1.1e6, [1.1e6], 1.1e6, 1.0e6), ("wait", 1.0e6, [], 0.9e6, 1.0e6)], \
        "the ranking's sale as the plan's: the offer taken only when it beats waiting, both shown"
    assert (take["step"], take["amount"], take["done"]) == ("take offer", 1.1e6, False)
    assert facts(take)["value"] == 1e6 and "raises" not in facts(take), \
        "his value beside the offer; what it raises is the step's amount, not said twice"
    assert "sell Dead" in said, said
    assert wait["step"] == "wait" and wait["done"] and wait["amount"] == 1.0e6, \
        "waiting: done for tonight, and the sale raises what waiting is worth"
    assert facts(wait)["offer"] == 0.9e6
    assert "waiting is worth" not in facts(take) and facts(take)["more offers before the lock"] == 0, \
        "the last offer before the lock: none to wait for"
    rolls = facts(sale(0.9e6, nights=2)[0])
    assert (rolls["waiting is worth"], rolls["more offers before the lock"]) == (1.0e6, 2), \
        "turning an offer down: what the game's offers still to come are worth, and how many come"
    assert "sell Dead" not in quiet, quiet
    owed = report(put(put(put(ub, "offer", {"dead": 0.9e6}), "fetches", {"dead": 1.0e6}), "paid", {"dead": 0.7e6}),
                  plan=sell_dead, options=[dead_alone], waits=[("dead", 1.0e6)], nights=2)
    made = facts(next(d for d in owed["do"] if d["what"] == "sell"))
    assert (made["paid"], made["made on the sale"], made["waiting less the offer"]) == (0.7e6, 0.2e6, 0.1e6), \
        "what you make on an offer is the decision's (decision_sale): the offer less what you paid, waiting less the offer"

    def relists(ends: str, nights: int) -> list[tuple[Any, ...]]:
        doc = report(put(ub, "route", {"dead": "listed"}),
                     nights=nights, listings=[("dead", ends)])
        return [(d["label"], d["facts"]) for d in doc["do"] if d["what"] == "relist"]
    assert relists("2026-08-03T10:00:00+02:00", 3) == [
        ("relist Dead", [["once the listing ends", "2026-08-03T10:00:00+02:00", "time"]])], \
        "his listing ends before two of the three offers to come: relist him once it does"
    assert relists("2026-08-03T10:00:00+02:00", 1) == relists("2026-08-05T10:00:00+02:00", 3) == [], \
        "a listing that outlasts the offers to come needs nothing"

    # a sale beside the plan's buy
    def hedge(offer: float, waits: float, nights: int, win: float = 0.5, worth: float = -5.0) -> Doc:
        mk = put(put(put(ub, "route", {"dead": "listed"}), "offer", {"dead": offer}), "fetches",
                 {"dead": max(offer, waits)})
        doc = report(mk, waits=[("dead", waits)], nights=nights,
                     plan=[("buy", "cand", 2e6, win, 3.0), ("sell", "dead", 1e6, 1.0, worth)],
                     options=[("buy", "cand", 2e6, win, 3.0, 3.0), ("sell", "dead", 1e6, 1.0, worth, worth)])
        return next(d for d in doc["do"] if d["what"] == "sell")
    near = hedge(1.0e6, 0.99e6, 1)
    assert (near["step"], near["amount"], near["done"], near["accepts"]) \
        == ("sell after the auctions", 1e6, False, []), \
        "beside the plan's bid: sold to the game at his value once the bid is won, no button before"

    offered = put(put(ub, "offer", {"dead": 1.1e6, "star": 19e6}), "fetches", {"dead": 1.1e6, "star": 19e6})
    told = report(offered, ratios=[0.9, 0.95, 1.0, 1.05])
    assert not [d for d in told["do"] if d["what"] == "sell"], \
        "an offer the plan does not take is not shown, however good: the plan is the one judgement"
    assert "star" in told["do"][-1]["label"].lower(), "Star is listed for: no offers come until then"
    took = report(offered, plan=[("sell", "dead", 1.1e6, 1.0, 0.5)], ratios=[0.9, 0.95, 1.0, 1.05])
    assert [(d["step"], facts(d)["the game's recent offers"]) for d in took["do"] if d["what"] == "sell"] \
        == [("take offer", 1.0)], "the plan sells him: to do, and how his offer ranks among the game's"

    ub = put(put(ub, "my_bid", {}), "route", {})
    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        seed(ub)
        got = record_run(7)
        assert got == acts and rows("SELECT step, kind, player, amount FROM decision_plan WHERE run_id = 7") \
            == [(s, kind, k, a) for s, kind, k, a, *_ in acts], "the plan's moves, in order"
        assert rows("SELECT kind, player, in_plan FROM decision_option WHERE run_id = 7 AND in_plan") \
            == [("buy", k, 1) for k in sorted(bought)], "each move alone, the plan's marked"
        assert rows("SELECT count(*) FROM decision_option WHERE run_id = 7 AND kind = 'sell'") == [(len(sqb), )]
        assert sorted(decision.options()) == sorted(rows(
            "SELECT kind, player, amount, in_plan FROM decision_option WHERE run_id = 7")), \
            "the options as the decision hands them to a replay are the ones it keeps"
        held = {k: r for k, r in ub["p"].items() if r.get("owner") != "riv"}
        assert [r for r in rows("SELECT player, pos, value, price, offer, fetches FROM decision_facts WHERE run_id = 7"
                                " ORDER BY player") if r[0] in held] == sorted(
            (k, r.get("pos") or "", r.get("value"), r.get("price"), r.get("offer"),
             (r["fetches"] if r.get("fetches") is not None else r.get("value") or 0.0) if r.get("owner") == "me" else None)
            for k, r in held.items()), "the facts the decision was made on, as its league holds them"
        assert rows("SELECT moment FROM sim_runs WHERE run_id = 7") == [(None,)]
        assert rows("SELECT count(*) FROM decision_option WHERE run_id = 7 AND kind = 'buy'"
                    " AND asking_win IS NOT 1.0") == [(0,)], "a buy's chance at his asking price: sure with no past"
        with closing(connect()) as db, db:
            db.execute("INSERT INTO forecast_moment (moment, code, as_of, variant) VALUES (5, 'c', 't', 'live')")
            db.executemany("INSERT INTO forecast_cell VALUES (5, ?, 3, ?, ?, ?, ?, ?, ?)",
                           [("star", 6.6, 0.45, 6.0, 1.1, 0.9, 0.5), ("plain", 4.0, 0.8, None, None, None, None)])
            db.execute("UPDATE sim_runs SET moment = 5 WHERE run_id = 7")
        got_fc, = rows("SELECT player, ppm, fixture, picked, fit, points, plays, expected FROM sim_forecast"
                       " WHERE run_id = 7")
        assert got_fc[:5] == ("star", 6.0, 1.1, 0.9, 0.5) and abs(got_fc[5] - 6.6) < 1e-9 \
            and abs(got_fc[6] - 0.45) < 1e-9 and abs(got_fc[7] - 6.6 * 0.45) < 1e-9, \
            "a run's forecast is its moment's cells with factors, their products"

        sim = {r[0]: r[1:] for r in rows("SELECT manager, me, finish, p_first, p_above, cash, lo, mean, hi"
                                         " FROM decision_standings WHERE run_id = 7")}
        assert set(sim) == {"me", "riv"} and sim["me"][0] == 1 and sim["me"][3] is None
        (_, finish, first, _, cash, lo, mean, hi), (_, r_finish, r_first, r_above, *_) = sim["me"], sim["riv"]
        assert cash == ub["cash"], "yours from the app"
        assert abs(finish - (1 + r_above)) < 1e-12 and abs(r_finish - (2 - r_above)) < 1e-12, \
            "every manager's finish: 1 and the chance each other ends above him"
        assert abs(first + r_first - 1) < 1e-3 and abs(first - (1 - r_above)) < 1e-3, \
            "two managers: first is ending above the other (on 401 points of his total)"
        assert lo < mean < hi
        (r_lo, r_mean, r_hi), = rows("SELECT lo, mean, hi FROM decision_standings WHERE run_id = 7 AND NOT me")
        z = NormalDist().inv_cdf(0.9)
        assert abs(r_above - NormalDist().cdf((r_mean - mean) / math.hypot((hi - mean) / z, (r_hi - r_mean) / z))) < 1e-9, \
            "above you: the chance he finishes above you, not that you beat him"
        (won_first, won_finish), = rows("SELECT p_first, finish FROM league_standings WHERE me")
        assert rows("SELECT p_first_after, finish_after FROM decision_run WHERE run_id = 7") == [(won_first, won_finish)] \
            and won_finish < finish, \
            "your chance of first and finish once the plan is made, every bid won (the star in): the same standings"
        who = {k: r for k, *r in rows("SELECT player, start, next, season, mine, xi FROM decision_players WHERE run_id = 7")}
        assert set(who) == set(mine(ub)) | set(bought), "your squad and the plan's buys"
        assert sum(r[4] for r in who.values()) == 11 and not who["dead"][4] and not who["cand"][4], \
            "your eleven next is of the squad you hold now"
        assert max(abs(a - b) for a, b in zip(who["star"][:3], (0.9, 5.4, 54.0))) < 1e-9, \
            "the chance he plays next, his expected points next and over the ten jornadas left"

    debt = {**put(ub, "value", {**get(ub, "value"), "k": 4e6, "d1": 9e6, "m1": 1e6}), "cash": -5e6}
    owed = report(debt)
    assert [d["name"] for d in owed["do"] if d["what"] in ("buy", "sell")] == ["Dead", "D1"] \
        and owed["cash_after"] == 5e6, "in debt: sales only, the least bad first, until clear (5M short: 1M, then 9M)"
    assert [d["cash_then"] for d in owed["do"] if d["what"] in ("buy", "sell")] == [-4e6, 5e6], \
        "each step of the plan says the cash it leaves, running from today's: -5M, then +1M, then +9M"
    assert all("cash_then" not in d for d in owed["do"] if d["what"] not in ("buy", "sell")), \
        "a line that moves no money says none"

    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        seed(debt)
        record_run(7)
        assert rows("SELECT future, blocker, amount FROM decision_blocker WHERE run_id = 7 ORDER BY future") \
            == [("nothing", "debt", -5e6), ("plan", "eleven", None)], \
            "the run keeps the plan's check: doing nothing stays 5M in debt; the plan sells its way out but its two " \
            "sales (a defender among them) leave this squad two defenders, short of any formation"

    _selftest_lineup()
    print("sim self-test OK")


def _selftest_lineup() -> None:
    """schema.sql's lineup_gap: the app's line-up against your best eleven now,
    as the board's field line and the reminder both read it."""
    import tempfile
    from contextlib import closing
    from pathlib import Path

    from ffcore.db import connect
    from ffcore.fixtures import lineup_board
    from ffcore.tidy import tables_in

    def gap(app: list[tuple[str, str]], ids: dict[str, str]) -> list[tuple[str, str, int]]:
        """The app line-up as (app id, name) rows, with these players' app ids."""
        with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
            lineup_board(app, ids)
            with closing(connect()) as db:
                return [tuple(r) for r in db.execute(
                    "SELECT side, player, legal FROM lineup_gap ORDER BY side, player")]

    lineup = [("1070", "Ionut Radu"), ("2464", "Pepelu")]
    assert gap(lineup, {"ionut radu": "1070"}) == [], \
        "set as the best eleven now: nothing to change, though the plan buys cai coro tonight " \
        "(a line-up is of players you hold)"
    whole = [("in", "ionut radu", 0), ("in", "pepelu", 0)]
    assert gap(lineup + [("999", "Nobody")], {"ionut radu": "1070"}) == whole, \
        "a line-up naming a player you do not own is unknown: the whole eleven to set"
    assert gap(lineup, {"ionut radu": "1070", "someone else": "2464"}) == whole, \
        "an app id that is another's player is the same"
    assert gap([("1070", "Ionut Radu"), ("3", "Bench Boy")], {"ionut radu": "1070", "bench boy": "3"}) == [
        ("in", "pepelu", 1), ("out", "bench boy", 1)], "bench boy fielded for pepelu: swap them"
    assert gap([], {}) == whole, "no line-up given: the whole eleven"


def _selftest_board_contract() -> None:
    """The board reads the decision's plan-detail contract and nothing else of the decision: seeded rows
    (decision_run, decision_plan, decision_option, decision_players, decision_waiting, decision_standings, and the
    facts the run saw, seen at its cut) in, the board's lines out. No decision view is made, so a board that reached
    for the decision's internals (bid_odds, recent_offers, nights_left, waiting) would find nothing."""
    import tempfile
    from pathlib import Path

    from ffcore.db import connect
    from ffcore.tidy import tables_in

    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        c = cut()
        with closing(connect()) as db, db:
            db.execute("INSERT INTO runs (run_id, started, as_of, trigger, commit_sha) VALUES (3, 't', 't', 'test', 'c')")
            db.execute("INSERT INTO decision_run (run_id, cut, p_first_after, finish_after, cash_after, can_bid, nights, first)"
                       " VALUES (3, ?, 0.25, 2.5, 4e6, 6e6, 2, julianday('2026-08-02T20:00:00Z'))", (c,))
            db.execute("INSERT INTO decision_sale (run_id, player, offer, paid, made, z, p_better, waiting_less)"
                       " VALUES (3, 'a', 1.1e6, 0.8e6, 0.3e6, 2.0, 0.4375, -0.1e6)")
            db.executemany("INSERT INTO decision_blocker (run_id, future, blocker, amount) VALUES (3, ?, ?, ?)",
                           [("nothing", "debt", -6e6), ("plan", "debt", -1e6), ("plan", "eleven", None)])
            db.executemany("INSERT INTO decision_skip (run_id, player, kind, blocker, amount) VALUES (3, ?, ?, ?, ?)",
                           [("c", "buy", "none", None), ("a", "sell", "debt", -1e6), ("a", "sell", "eleven", None)])
            db.executemany("INSERT INTO seen (cut, variant, player, name, pos, owner, value, trend, offer, price, route,"
                           " fetches, paid) VALUES (?, 'live', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                           [(c, "a", "Ana", "MED", "me", 1e6, None, 1.1e6, None, "listed", 1.1e6, 0.8e6),
                            (c, "b", "Beto", "DEF", "me", 2e6, 3.0, None, None, None, 2e6, None),
                            (c, "c", "Cai", "DEL", None, 4e6, 1.0, None, 5e6, None, None, None)])
            db.executemany("INSERT INTO decision_players (run_id, player, start, next, season, mine, xi)"
                           " VALUES (3, ?, 0.9, ?, 20.0, ?, ?)",
                           [("a", 1.0, 1, 0), ("b", 3.0, 1, 1), ("c", 4.0, 0, 0)])
            db.executemany("INSERT INTO decision_option (run_id, kind, player, amount, win, gain, worth, in_plan,"
                           " asking_win, beats) VALUES (3, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                           [("sell", "a", 1.1e6, 1.0, -1.0, -0.5, 1, None, 0.75),
                            ("sell", "b", 2e6, 1.0, -3.0, -2.0, 0, None, None),
                            ("buy", "c", 5.2e6, 0.8, 4.0, 3.0, 1, 0.6, None)])
            db.executemany("INSERT INTO decision_plan (run_id, step, kind, player, amount, win, worth, after)"
                           " VALUES (3, 0, ?, ?, ?, ?, ?, 0)",
                           [("buy", "c", 5.2e6, 0.8, 3.0), ("sell", "a", 1.1e6, 1.0, -0.5)])
            db.execute("INSERT INTO decision_waiting (run_id, player, kind, later) VALUES (3, 'a', 'sale', 1.0e6)")
            db.executemany("INSERT INTO decision_standings (run_id, manager, me, now, mean, lo, hi, finish, p_first,"
                           " p_above, cash) VALUES (3, ?, ?, 10, ?, 0, 0, ?, ?, ?, ?)",
                           [("me", 1, 50.0, 2.0, 0.4, None, 4e6), ("riv", 0, 60.0, 1.0, 0.6, 0.7, 1e6)])
        doc = board_doc()
        do = {d["what"]: d for d in doc["do"]}
        buy = do["buy"]
        assert (buy["name"], buy["step"], buy["amount"], buy["win"], buy["asking"], buy["asking_win"]) \
            == ("Cai", "bid up to", 5.2e6, 0.8, 5e6, 0.6), \
            "a buy: the plan's bid and its chance, and the chance of winning at his asking price (decision_option)"
        sell = do["sell"]
        assert (sell["name"], sell["step"], sell["amount"]) == ("Ana", "take offer", 1.1e6), sell
        assert buy["skip"] == [{"blocker": "none", "label": "safe to skip", "amount": None}] \
            and sell["skip"] == [
                {"blocker": "debt", "label": "if you skip this: in debt at the lock", "amount": -1e6},
                {"blocker": "eleven", "label": "if you skip this: no eleven can be fielded", "amount": None}], \
            "what skipping a step leaves (decision_skip), worded once by the board, by blocker"
        assert all("skip" not in d for d in doc["do"] if d["what"] not in ("buy", "sell")) \
            and all("skip" not in d for d in doc["buys"] + doc["sells"]), "only the plan's buys and sales carry skip"
        got = {f[0]: f[1] for f in sell["facts"]}
        assert got["the game's recent offers"] == 0.75 and got["more offers before the lock"] == 2 \
            and got["waiting is worth"] == 1.0e6, \
            "a sale: where his offer ranks among the game's recent offers (decision_option.beats), the nights left " \
            "(decision_run) and what waiting is worth (decision_waiting)"
        facts_of = {f[0]: (f[1], f[2]) for f in sell["facts"]}
        assert facts_of["paid"] == (0.8e6, "money") and facts_of["made on the sale"] == (0.3e6, "money"), \
            "what you make: the offer less what you paid"
        assert facts_of["offer vs the usual (sd)"] == (2.0, "sd") and facts_of["a better offer comes"] == (0.4375, "chance"), \
            "the board shows what the decision recorded (decision_sale), it works out nothing"
        assert facts_of["waiting less the offer"] == (-0.1e6, "money"), "waiting worth 1.0M against the offer 1.1M"
        assert (doc["finish"], doc["finish_after"], doc["p_win"], doc["p_win_after"], doc["cash"],
                doc["cash_after"], doc["can_bid"]) == (2.0, 2.5, 0.4, 0.25, 4e6, 4e6, 6e6), doc
        assert doc["blockers"] == [
            {"future": "nothing", "blocker": "debt", "label": "if you do nothing: in debt at the lock", "amount": -6e6},
            {"future": "plan", "blocker": "debt", "label": "if you do the plan: in debt at the lock", "amount": -1e6},
            {"future": "plan", "blocker": "eleven", "label": "if you do the plan: no eleven can be fielded", "amount": None}], \
            "what the decision's check recorded for both futures, worded once by the board"
        assert [(r["manager"], r["p_above"]) for r in doc["standings"]] == [("riv", 0.7), ("me", None)], \
            "the standings forecast the run recorded, most points first"
        assert [r["name"] for r in doc["buys"]] == ["Cai"] and [r["name"] for r in doc["sells"]][0] in ("Ana", "Beto")


def record_run(run: int, params: Sequence[Doc] = (), moment: int | None = None) -> list[tuple[Any, ...]]:
    """The decision at t (decision.plan) and
    everything the run saw, into its tables: the board views read them; the
    forecast moment it was built on, and your chance of first and finish
    once the plan is made, every bid won. The plan's moves."""
    acts = decision.record(run)
    record([{"run_id": run, **r} for r in params], "params")
    with closing(connect()) as db, db:
        db.execute("INSERT INTO sim_runs (run_id, moment) VALUES (?, ?)", (run, moment))
        db.execute("UPDATE forecast_moment SET pinned = 1 WHERE moment = ?", (moment,))   # a run keeps its forecast
    return acts


def board_doc() -> Doc:
    """decisions.json as board_json makes it, from the last run's rows."""
    (doc,), = rows("SELECT json FROM board_json")
    got: Doc = json.loads(doc)
    return got


def main() -> None:
    b = built()
    (managers, ), = rows("SELECT count(*) FROM cash")
    left = rounds().rem
    if managers < 2 or not left:
        print("sim: nothing to decide (%d managers, %d jornadas left)" % (managers, len(left)))
        return
    run = this_run()
    if run is None:
        raise SystemExit("sim: the board is the views over a run's rows; run it as run.py sim")
    record_run(run, b.params, b.moment)
    REPORTS.mkdir(exist_ok=True)
    (REPORTS / "decisions.json").write_text(json.dumps(
        board_doc(), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("wrote %s" % (REPORTS / "decisions.json"))


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        raise SystemExit(0)
    main()
