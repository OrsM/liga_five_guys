"""The sim stage: the decision (decision.plan, ffcore/decide.sql), what the
run decided recorded (decision_option, decision_plan, forecast, params,
sim_*), and decisions.json, the phone's copy of the board, written from
the board views (schema.sql: board_json)."""
from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from contextlib import closing
from dataclasses import replace
from typing import Any

from assemble import built
import decision
from decide import Action, Universe
from ffcore.db import connect, record, rows, this_run
from ffcore.tidy import REPORTS

__all__ = ["with_squad", "board_doc", "players", "record_run", "standings"]

Doc = dict[str, Any]  # a JSON object of decisions.json

# The plan as the run keeps it: dec_plan's moves, each sale with when it is
# made (decide.sql dec_when).
KEEP_PLAN = ("INSERT INTO decision_plan (run_id, step, kind, player, amount, win, worth, after)"
             " SELECT ?, step, kind, player, amount, win, worth, after FROM dec_when")


def _selftest() -> None:
    import tempfile
    from pathlib import Path

    from ffcore.db import connect
    from ffcore.market import Market
    from ffcore.rules import DEBT_SHARE
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

    def decided(u: Universe) -> list[Action]:
        with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
            return decision.plan(u)
    acts = decided(ub)
    bought = [a.buy for a in acts if a.buy]
    assert sorted(bought) == ["cand", "twin"], "each 4 a jornada for a 2.7 in the eleven"
    sold = [k for a in acts for k in a.sell]

    held = with_squad(ub, {"star": "MED"}, 1e6, {"cand": 5e6, "gone": 1e6})
    assert held.mine == {"star": "MED"} and held.market.cash == 1e6 and held.me == "me" \
        and held.state.squads["riv"] == riv and held.market.my_bid == {"cand": 5e6}, \
        "your squad, cash and bids (on players still on the market) swapped, the rest as it was"
    assert ub.mine == sqb and ub.market.cash == 10e6, "the original is untouched"

    Plan = Sequence[tuple[str, str, float, float, float]]          # kind, player, amount, win, worth
    Option = tuple[str, str, float, float, float, float]    # kind, player, amount, win, gain, worth

    def report(u: Universe, fielded: Sequence[str] = (),
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
        from ffcore.names import Name
        u = replace(u, market=replace(u.market, name={
            **{k: Name(k) for k in set(u.market.pos) | set(u.mine)}, **u.market.name,
            **{k: Name(v) for k, v in (names or {}).items()}}))
        m = u.market
        with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
            with closing(connect()) as db, db:
                db.execute("DROP VIEW player_facts")
                db.execute("CREATE TABLE player_facts (player, name, pos, value, owner, clause,"
                           " price, route, my_bid, offer, paid, trend)")
                db.executemany("INSERT INTO player_facts VALUES (%s)" % ", ".join("?" * 12), [
                    (k, m.shown(k), m.pos.get(k), m.value.get(k), m.owner.get(k), m.clause.get(k),
                     m.price.get(k), m.route.get(k), m.my_bid.get(k), m.offer.get(k),
                     m.paid.get(k), m.trend.get(k)) for k in set(m.pos) | set(u.mine)])
                db.execute("DROP VIEW sale_odds")
                db.execute("CREATE TABLE sale_odds (player, offer, waiting, fetches)")
                db.executemany("INSERT INTO sale_odds VALUES (?, ?, NULL, ?)",
                               [(k, o, m.fetch[k]) for k, o in m.offer.items()])
                db.execute("DROP VIEW offer_ratios")
                db.execute("CREATE TABLE offer_ratios (created_at, ratio)")
                db.executemany("INSERT INTO offer_ratios VALUES ('', ?)", [(r,) for r in ratios])
                db.executemany("INSERT INTO crosswalk (player_id, app_id) VALUES (?, ?)",
                               [(k, k) for k in dict.fromkeys([*fielded, *(k for k, _e in listings),
                                                                *(k for k, _n in bids)])])
                db.execute("DROP VIEW waiting")
                db.execute("CREATE TABLE waiting (player, kind, later)")
                db.executemany("INSERT INTO waiting VALUES (?, 'sale', ?)", waits)
                db.execute("DROP VIEW nights_left")
                db.execute("CREATE TABLE nights_left AS SELECT ? AS nights,"
                           " julianday('2026-08-02T20:24:00Z') AS first", (nights,))
                db.executemany("INSERT INTO model_constants VALUES (variant(), ?, ?)", [
                    ("PRICE_WINDOW", 50.0)])
            store_sql("api_standings", [{"observed_at": "2026-08-01T0000Z", "manager": u.me}])
            store_sql("api_market", [{"observed_at": "2026-08-01T0000Z", "player_id": k, "seller": "marketPlayerTeam",
                                      "expires_at": e} for k, e in listings]
                      + [{"observed_at": "2026-08-01T0000Z", "player_id": k, "seller": "marketPlayerLeague",
                          "sale_price": str(m.price.get(k, 1e6)), "bids": str(n)} for k, n in bids])
            store_sql("api_lineup", [{"observed_at": "2026-08-01T0000Z", "player_id": k}
                                     for k in fielded])
            record_run(u, 7)
            with closing(connect()) as db, db:
                db.executemany("INSERT OR REPLACE INTO decision_option VALUES (7, ?, ?, ?, ?, ?, ?, 0)", options)
                if plan is not None:
                    db.execute("DELETE FROM decision_plan WHERE run_id = 7")
                    db.execute("DELETE FROM dec_plan")
                    db.executemany("INSERT INTO dec_plan VALUES (?, ?, ?, ?, ?, ?)",
                                   [(i, *p) for i, p in enumerate(plan)])
                    db.execute(KEEP_PLAN, (7,))
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
    now = list(ub.outlook.xi.ranked())
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
            {"asking", "win", "asking_win"} if d["what"] == "buy" else set()), d
    assert all("gain" in d for d in doc["do"]), "every move says its points"
    assert all(len(d["act"]) == 1 for d in doc["do"] if d["what"] in ("buy", "sell")) \
        and len(field["act"]) == 11 \
        and all(e["pos"] for e in field["act"]), \
        ("each line names the players it acts on, as the app knows them (act.py reads nothing else)",
         [(d["label"], d["act"]) for d in doc["do"]][:3])
    assert [d["what"] for d in doc["do"]][:1] == ["field"], doc["do"]
    assert {d["name"].lower() for d in doc["do"] if d["what"] == "buy"} == set(bought)
    assert doc["cash_after"] == ub.market.left(acts)
    exposed = report(replace(ub, market=replace(ub.market, clause={"star": 20e6, "dead": 30e6})))["exposed"]
    assert [e["name"].lower() for e in exposed] == ["star", "dead"], \
        "exposed: every player with a clause, most valuable first"
    assert report(ub, plan=[])["cash_after"] is None, "no cash line when the plan moves no money"
    assert [r["estimated"] for r in doc["standings"] if r["me"]] == [False]
    riv_row = next(r for r in doc["standings"] if not r["me"])
    assert abs(riv_row["p_above"] - (1.0 - ub.standings.beat("riv"))) < 1e-12, \
        "above you: the chance he finishes above you, not that you beat him (JSON keeps 15 digits)"
    assert all(r["estimated"] for r in doc["standings"] if not r["me"]), \
        "rival cash is estimated from the feed"
    shown = sorted((d["name"].lower(), d["amount"]) for d in doc["do"] if d["what"] == "buy")
    assert shown == sorted((a.buy, a.cost) for a in acts if a.buy), \
        ("the plan's buys, each once at its bid, and no move it does not make: the plan is the one judgement", shown)
    assert all(abs(d["gain"] - facts(d)["points"]) < 1e-9 and "money" not in facts(d)
               for d in doc["do"] if d["what"] == "buy"), "its worth: its points, and no money counted as points"
    assert all(facts(d)["listed at"] == ub.market.price[d["name"].lower()]
               for d in doc["do"] if d["what"] == "buy"), \
        "what he is listed at, and the bid it takes"
    assert all((d["asking"], d["win"], d["asking_win"], facts(d)["chance to win"])
               == (ub.market.price[d["name"].lower()], 1.0, 1.0, 1.0) for d in doc["do"] if d["what"] == "buy"), \
        "each bid at its asking price too, and the chance of each (with no past auctions to go on, sure)"
    won = ub.after(*acts).standings
    assert (doc["p_win_after"], doc["finish_after"]) == (round(won.position(ub.me).get(1, 0.0), 3),
                                                         round(won.expected_position(ub.me), 2)), \
        "your chance of first and finish once the plan is made, every bid won: the same standings, on points"
    assert "lam" not in doc, "no money rate: money is a limit, not points"
    assert doc["buys"] and all(b["gain"] > 0 for b in doc["buys"]) \
        and [b["gain"] for b in doc["buys"]] == sorted((b["gain"] for b in doc["buys"]), reverse=True), \
        "the buys ranked: each that adds points, most first"
    assert {b["name"].lower() for b in doc["buys"]} <= set(ub.market.price) - set(ub.market.owner), \
        "only players you can buy: the game's market, never a rival's listing"
    assert {s["name"].lower() for s in doc["sells"]} == set(ub.state.squads[ub.me]) \
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
        == set(ub.mine) - set(sold), ("list every player of yours not listed and not sold", listing)
    assert doc["standings"][[r["me"] for r in doc["standings"]].index(True)]["p_above"] is None
    json.dumps(doc)
    assert doc["ping"] == "; ".join(d["label"] for d in doc["do"]), \
        "the ping is each move's Action.label, not words of its own"

    def valued(u: Universe, total: float) -> Universe:
        """Your players worth total between them: the team value the debt is a share of."""
        return replace(u, market=replace(u.market, value={**u.market.value,
                                                          **{k: total / len(u.mine) for k in u.mine}}))

    rich = valued(ub, 100e6)
    rich_doc = report(rich)
    assert rich_doc["can_bid"] == max(0.0, rich.market.cash + DEBT_SHARE * 100e6
                                      - sum(d["amount"] for d in rich_doc["do"] if d["what"] == "buy")), \
        ("what you can bid: your cash and the debt the game allows (rules.DEBT_SHARE) less the plan's bids;"
         " sales do not count, made after the auctions")
    stray = replace(rich.market, pos={**rich.market.pos, "ghost": "DEF"}, my_bid={"ghost": 3e6})
    assert abs(report(replace(rich, market=stray))["can_bid"]
               - (rich_doc["can_bid"] - 3e6)) < 1, \
        "a bid of yours the plan does not make is money you cannot bid again"
    cost = next(a.cost for a in acts if a.buy == bought[0])
    in_plan = replace(rich.market, my_bid={bought[0]: cost})
    assert report(replace(rich, market=in_plan))["can_bid"] == rich_doc["can_bid"], \
        "the plan's own bid is counted once, in the plan"
    pulled = [(d["what"], d["label"], d["step"], d["amount"], d["done"])
              for d in report(replace(ub, market=stray))["do"] if d["what"] == "withdraw"]
    assert pulled == [("withdraw", "withdraw bid on Ghost", "withdraw bid", 3e6, False)], \
        ("a bid of yours the plan does not make: take it back, or it may win beside the plan's", pulled)

    def bid_line(mine: float) -> tuple[Any, ...]:
        d = next(d for d in report(replace(ub, market=replace(ub.market, my_bid={bought[0]: mine})))["do"]
                 if d["what"] == "buy" and d["name"].lower() == bought[0])
        return d["step"], d["amount"], d["done"], facts(d).get("your bid")
    assert bid_line(cost) == ("bid in", cost, True, None), "your bid is the plan's: done"
    assert bid_line(cost - 1e6) == ("bid up to", cost, False, cost - 1e6), \
        "your bid under the plan's: change it to the plan's"
    ub = replace(ub, market=replace(ub.market, my_bid={bought[0]: cost}, route={"dead": "listed"}))
    buys_planned: Plan = [("buy", a.buy, a.cost, 1.0, 1.0) for a in acts if a.buy]
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
    mine, *others = [bought[0], *(n for n in buys if n != bought[0])]

    def hot(n_mine: int, n_other: int) -> dict[str, Any]:
        crowd = report(ub, bids=[(mine, n_mine), *((o, n_other) for o in others[:1])])
        return {d["name"].lower(): facts(d).get("bids") for d in crowd["do"] if d["what"] == "buy"}
    assert others, "the case: a buy of the plan you have not bid on"
    assert hot(1, 3) == {mine: "only yours", others[0]: "3", **{o: None for o in others[1:]}}, hot(1, 3)
    assert hot(4, 0) == {mine: "4, yours among them", others[0]: "none yet", **{o: None for o in others[1:]}}, \
        "how many bid on each: a crowd, only yours, or none yet"
    unlisted = report(replace(ub, market=replace(ub.market, route={})), plan=sell_dead, options=[dead_alone])
    assert [(d["step"], d["done"]) for d in unlisted["do"] if d["what"] == "sell"] == [("list", False)]

    def offered_doc(offer: float, nights: int = 0) -> Doc:
        mk = replace(ub.market, offer={"dead": offer}, fetch={"dead": max(offer, 1.0e6)})
        return report(replace(ub, market=mk), plan=sell_dead, options=[dead_alone],
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

    def relists(ends: str, nights: int) -> list[tuple[Any, ...]]:
        doc = report(replace(ub, market=replace(ub.market, route={"dead": "listed"})),
                     nights=nights, listings=[("dead", ends)])
        return [(d["label"], d["facts"]) for d in doc["do"] if d["what"] == "relist"]
    assert relists("2026-08-03T10:00:00+02:00", 3) == [
        ("relist Dead", [["once the listing ends", "2026-08-03T10:00:00+02:00", "time"]])], \
        "his listing ends before two of the three offers to come: relist him once it does"
    assert relists("2026-08-03T10:00:00+02:00", 1) == relists("2026-08-05T10:00:00+02:00", 3) == [], \
        "a listing that outlasts the offers to come needs nothing"

    # a sale beside the plan's buy
    def hedge(offer: float, waits: float, nights: int, win: float = 0.5, worth: float = -5.0) -> Doc:
        mk = replace(ub.market, route={"dead": "listed"}, offer={"dead": offer}, fetch={"dead": max(offer, waits)})
        doc = report(replace(ub, market=mk), waits=[("dead", waits)], nights=nights,
                     plan=[("buy", "cand", 2e6, win, 3.0), ("sell", "dead", 1e6, 1.0, worth)],
                     options=[("buy", "cand", 2e6, win, 3.0, 3.0), ("sell", "dead", 1e6, 1.0, worth, worth)])
        return next(d for d in doc["do"] if d["what"] == "sell")
    near = hedge(1.0e6, 0.99e6, 1)
    assert (near["step"], near["amount"], near["done"], near["accepts"]) \
        == ("sell after the auctions", 1e6, False, []), \
        "beside the plan's bid: sold to the game at his value once the bid is won, no button before"

    offered = replace(ub, market=replace(ub.market, offer={"dead": 1.1e6, "star": 19e6},
                                         fetch={"dead": 1.1e6, "star": 19e6}))
    told = report(offered, ratios=[0.9, 0.95, 1.0, 1.05])
    assert not [d for d in told["do"] if d["what"] == "sell"], \
        "an offer the plan does not take is not shown, however good: the plan is the one judgement"
    assert "star" in told["do"][-1]["label"].lower(), "Star is listed for: no offers come until then"
    took = report(offered, plan=[("sell", "dead", 1.1e6, 1.0, 0.5)], ratios=[0.9, 0.95, 1.0, 1.05])
    assert [(d["step"], facts(d)["the game's recent offers"]) for d in took["do"] if d["what"] == "sell"] \
        == [("take offer", 1.0)], "the plan sells him: to do, and how his offer ranks among the game's"

    ub = replace(ub, market=replace(ub.market, my_bid={}, route={}))
    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        got = record_run(ub, 7)
        assert got == acts and rows("SELECT step, kind, player, amount FROM decision_plan WHERE run_id = 7") \
            == [(i, a.kind, a.buy, a.cost) for i, a in enumerate(acts)], "the plan's moves, in order"
        assert rows("SELECT kind, player, in_plan FROM decision_option WHERE run_id = 7 AND in_plan") \
            == [("buy", k, 1) for k in sorted(bought)], "each move alone, the plan's marked"
        assert rows("SELECT count(*) FROM decision_option WHERE run_id = 7 AND kind = 'sell'") == [(len(sqb), )]
        m = ub.market
        assert rows("SELECT player, pos, value, price, offer, fetches FROM sim_facts WHERE run_id = 7"
                    " ORDER BY player") == sorted(
            (k, m.pos.get(k, ""), m.value.get(k), m.price.get(k), m.offer.get(k), m.fetches(k) if k in ub.mine else None)
            for k in set(m.name) | set(m.pos)), "the facts the decision was made on, as its universe holds them"
        assert rows("SELECT moment FROM sim_runs WHERE run_id = 7") == [(None,)]
        with closing(connect()) as db, db:
            db.execute("INSERT INTO forecast_moment VALUES (5, 'c', 'k', 't', 1)")
            db.executemany("INSERT INTO forecast_cell VALUES (5, ?, 3, ?, ?, ?, ?, ?, ?)",
                           [("star", 6.6, 0.45, 6.0, 1.1, 0.9, 0.5), ("plain", 4.0, 0.8, None, None, None, None)])
            db.execute("UPDATE sim_runs SET moment = 5 WHERE run_id = 7")
        got_fc, = rows("SELECT player, ppm, fixture, picked, fit, points, plays, expected FROM run_forecast"
                       " WHERE run_id = 7")
        assert got_fc[:5] == ("star", 6.0, 1.1, 0.9, 0.5) and abs(got_fc[5] - 6.6) < 1e-9 \
            and abs(got_fc[6] - 0.45) < 1e-9 and abs(got_fc[7] - 6.6 * 0.45) < 1e-9, \
            "a run's forecast is its moment's cells with factors, their products"

    base = ub.standings
    sim = standings(ub, 7)
    me_row = next(r for r in sim if r["me"])
    assert len(sim) == 2 and me_row["p_above"] is None and me_row["manager"] == "me"
    assert (me_row["finish"], me_row["p_first"]) == (base.expected_position(), base.position().get(1, 0.0))
    riv_st = next(r for r in sim if not r["me"])
    assert (me_row["cash"], riv_st["cash"]) == (ub.market.cash, ub.rival_cash.get("riv", 0.0)), \
        "yours from the app, a rival's as estimated"
    assert riv_st["p_above"] == 1.0 - base.beat("riv") and riv_st["finish"] == \
        base.expected_position("riv"), "every manager's finish, not only yours"
    assert (riv_st["lo"], riv_st["hi"]) == base.band("riv") and riv_st["mean"] == base.mean("riv")
    who = {r["player"]: r for r in players(ub, acts, 7)}
    assert set(who) == set(ub.mine) | set(bought), "your squad and the plan's buys"
    assert sum(r["xi"] for r in who.values()) == 11
    assert who["star"]["next"] == ub.outlook.xi.expected["star"] \
        and who["star"]["season"] == ub.outlook.season["star"]

    debt = replace(ub, market=replace(ub.market, cash=-5e6,
                                      value={**ub.market.value, "k": 4e6, "d1": 9e6, "m1": 1e6}))
    owed = report(debt)
    assert [d["name"] for d in owed["do"] if d["what"] in ("buy", "sell")] == ["Dead", "D1"] \
        and owed["cash_after"] == 5e6, "in debt: sales only, the least bad first, until clear (5M short: 1M, then 9M)"

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



def standings(u: Universe, run: int) -> list[Doc]:
    """Each manager's season if you do nothing (Universe.standings): mean
    points and their 10th-90th percentile band, expected final position,
    the chance of first, the chance he finishes above you, and his cash."""
    base = u.standings
    return [{"run_id": run, "manager": mgr, "me": int(mgr == u.me),
             "now": u.state.carried.get(mgr, 0.0), "mean": base.mean(mgr),
             "lo": base.band(mgr)[0], "hi": base.band(mgr)[1],
             "finish": base.expected_position(mgr), "p_first": base.position(mgr).get(1, 0.0),
             "cash": u.market.cash if mgr == u.me else u.rival_cash.get(mgr, 0.0),
             "p_above": None if mgr == u.me else 1.0 - base.beat(mgr)}
            for mgr in u.state.squads]


def players(u: Universe, acts: Sequence[Action], run: int) -> list[Doc]:
    """Your squad, every player on the market and the plan's buys as the
    model sees them: the chance he plays next, his expected points next and
    over the season, and whether he is in the best eleven now."""
    o = u.outlook
    exp, xi = o.xi
    keys = list(dict.fromkeys([*u.mine, *sorted(u.market.price), *(a.buy for a in acts if a.buy)]))
    return [{"run_id": run, "player": k, "start": o.next_up.get(k, (0.0, 0.0))[1],
             "next": exp.get(k, 0.0), "season": o.season.get(k, 0.0),
             "mine": int(k in u.mine), "xi": int(k in xi)}
            for k in keys]


def facts(u: Universe, run: int) -> list[Doc]:
    """Every player the universe knows, with the facts the decision was
    made on: his name as shown, position, value, trend, what was paid, the
    offer on him, his clause, price and route on the market, your bid, and
    what a sale of him gets."""
    m = u.market
    return [{"run_id": run, "player": k, "name": m.shown(k), "pos": m.pos.get(k, ""),
             "value": m.value.get(k), "trend": m.trend.get(k), "paid": m.paid.get(k),
             "offer": m.offer.get(k), "clause": m.clause.get(k), "price": m.price.get(k),
             "route": m.route.get(k), "my_bid": m.my_bid.get(k),
             "fetches": m.fetches(k) if k in u.mine else None}
            for k in sorted(set(m.name) | set(m.pos))]


def record_run(u: Universe, run: int, params: Sequence[Doc] = (), moment: int | None = None) -> list[Action]:
    """The decision (decision.plan) and everything the run saw, into its
    tables: the board views read them; the forecast moment it was built on,
    and your chance of first and finish once the plan is made, every bid
    won. The plan's moves."""
    acts = decision.plan(u)
    won = u.after(*acts).standings
    record([{"run_id": run, **r} for r in params], "params")
    record(standings(u, run), "sim_standings")
    record(players(u, acts, run), "sim_players")
    record(facts(u, run), "sim_facts")
    with closing(connect()) as db, db:
        db.execute("INSERT INTO decision_option SELECT ?, kind, player, amount, win, gain, worth, in_plan"
                   " FROM dec_rows", (run,))
        db.execute(KEEP_PLAN, (run,))
        db.execute("INSERT INTO sim_runs SELECT ?, ?, ?, ?, c.free, r.room FROM dec_cash c, dec_room r",
                   (run, moment, won.position(u.me).get(1, 0.0), won.expected_position(u.me)))
        db.execute("INSERT INTO sim_waiting SELECT ?, player, kind, later FROM waiting", (run,))
    return acts


def with_squad(u: Universe, squad: dict[str, str], cash: float,
               bids: dict[str, float] | None = None) -> Universe:
    """The league with this squad, cash and bids in (on players still on
    the market) as yours, and none of your own bids or the offers on your
    players (they are not these players')."""
    return replace(u, state=replace(u.state, squads={**u.state.squads, u.me: dict(squad)}),
                   market=replace(u.market, cash=cash, offer={}, fetch={},
                                  my_bid={k: b for k, b in (bids or {}).items() if k in u.market.price}))


def board_doc() -> Doc:
    """decisions.json as board_json makes it, from the last run's rows."""
    (doc,), = rows("SELECT json FROM board_json")
    got: Doc = json.loads(doc)
    return got


def main() -> None:
    b = built()
    u = b.universe
    if len(u.state.squads) < 2 or not u.state.jornadas:
        print("sim: nothing to decide (%d squads, %d jornadas left)"
              % (len(u.state.squads), len(u.state.jornadas)))
        return
    run = this_run()
    if run is None:
        raise SystemExit("sim: the board is the views over a run's rows; run it as run.py sim")
    record_run(u, run, b.params, b.moment)
    REPORTS.mkdir(exist_ok=True)
    (REPORTS / "decisions.json").write_text(json.dumps(
        board_doc(), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("wrote %s" % (REPORTS / "decisions.json"))


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        raise SystemExit(0)
    main()
