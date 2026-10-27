"""The sim stage: rank and plan the moves (decide.board), record
what they decided for the run (funnel, forecast, params, sim_*), and write
decisions.json, the phone's copy of the board, from the board views
(schema.sql: board_json)."""
from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from contextlib import closing
from dataclasses import replace
from typing import Any

from assemble import built, universe
import decide
from decide import CONFIDENCE, Board, Universe, board, verdict
from ffcore.db import connect, record, rows, this_run
from ffcore.forecast import expected_points
from ffcore.tidy import REPORTS

__all__ = ["with_squad", "rival_rows", "rival_universe", "board_doc", "forecast", "funnel", "plan", "plan_head", "players", "record_run",
           "standings"]

Doc = dict[str, Any]  # a JSON object of decisions.json


def _selftest() -> None:
    """The board's tests, with money at no value (decide._selftest): their
    players have no market value to count."""
    was = decide.MONEY_RATE
    try:
        decide.MONEY_RATE = 0.0
        _selftest_board()
    finally:
        decide.MONEY_RATE = was


def _selftest_board() -> None:
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
    rows = {r.action.buy or r.action.sell: r for r in ub.rank(ub.candidates())}
    assert rows[("star",)].d_pts < -20 and -5 < rows[("dead",)].d_pts < 5, \
        "a sale is scored like any move"
    bought = [p.action.buy for p in b.plan if p.action.buy]
    assert bought and set(bought) <= {"cand", "twin"}, bought
    assert sum(p.action.net for p in b.plan) <= ub.market.cash, b.plan
    sold = [k for p in b.plan for k in p.action.sell]
    assert len(sold) == len(set(sold)), sold
    assert b.gain >= max(r.d_pts for r in b.plan) - 5.0, (b.gain, b.plan)

    # every rival's board: the same funnel with him as "me", his cash our estimate, no bids or offers of yours
    riv11 = {**riv, "rf2": "DEL"}
    perv = {j: {**layer, "rf2": (3.0, 0.9)} for j, layer in perb.items()}
    uv = Universe(state=LeagueState({"me": dict(sqb), "riv": dict(riv11)}, many_j, "me"),
                  forecaster=Bootstrap(perv),
                  market=Market(cash=10e6, pos={**sqb, **riv11, "cand": "MED", "twin": "MED"},
                                price={"cand": 5e6, "twin": 5e6}, value={"dead": 1e6, "star": 20e6},
                                owner={"star": "me", "rk": "riv"}, my_bid={"cand": 5e6},
                                offer={"star": 9e6}, fetch={"star": 9e6}),
                  rival_cash={"riv": 7e6})
    ru = rival_universe(uv, "riv")
    assert ru.me == "riv" and ru.mine == riv11, "his squad is the one decided for"
    assert ru.market.cash == 7e6 and ru.rival_cash == {"me": 10e6}, "his cash the estimate; yours now a rival's"
    assert not (ru.market.my_bid or ru.market.offer or ru.market.fetch), \
        "your bids and the offers on your players are not his"
    held = with_squad(uv, {"star": "MED"}, 1e6)
    assert held.mine == {"star": "MED"} and held.market.cash == 1e6 and held.me == "me" \
        and held.state.squads["riv"] == riv11, "your squad and cash swapped, the rest as it was"
    assert not (held.market.my_bid or held.market.offer or held.market.fetch)
    assert uv.mine == sqb and uv.market.cash == 10e6, "the original is untouched"
    heads, moves = rival_rows(uv, 7)
    assert [(h["manager"], h["cash"]) for h in heads] == [("riv", 7e6)], heads
    want = board(ru)
    assert heads[0]["plan_gain"] == want.gain and [m["label"] for m in moves] == [
        r.action.label(ru.market.names) for r in want.plan], "the board of the universe he is"
    assert {m["manager"] for m in moves} <= {"riv"} and [m["ord"] for m in moves] == list(range(len(moves)))
    assert not {m["label"] for m in moves} & {"buy star"}, "never a player you own: the owner stays"

    def report(u: Universe, bd: Board, fielded: Sequence[str] = (),
               ratios: Sequence[float] = (),
               resale: Sequence[tuple[str, float, float]] = (), team_value: float = 0.0,
               waits: Sequence[tuple[str, float]] = (), nights: int = 0,
               listings: Sequence[tuple[str, str]] = ()) -> Doc:
        """The board the views make of this universe's run, its market's
        facts standing in for player_facts and cash, ratios for the game's
        past offers, resale for what waiting sells the game's market players
        for, rising and flat (assemble tests those), team_value yours, waits
        what turning down an offer on a player of yours is worth, nights the
        game's offers still to come before the lock (the first a day after
        2026-08-01), listings when each of yours ends."""
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
                db.execute("DROP VIEW sale_odds")
                db.execute("CREATE TABLE sale_odds (player, offer, waiting, fetches)")
                db.executemany("INSERT INTO sale_odds VALUES (?, ?, NULL, ?)",
                               [(k, o, m.fetch[k]) for k, o in m.offer.items()])
                db.execute("DROP VIEW offer_ratios")
                db.execute("CREATE TABLE offer_ratios (created_at, ratio)")
                db.executemany("INSERT INTO offer_ratios VALUES ('', ?)", [(r,) for r in ratios])
                db.executemany("INSERT INTO crosswalk (player_id, app_id) VALUES (?, ?)",
                               [(k, k) for k in dict.fromkeys([*fielded, *(k for k, _e in listings)])])
                db.execute("DROP VIEW waiting")
                db.execute("CREATE TABLE waiting (player, kind, later)")
                db.executemany("INSERT INTO waiting VALUES (?, ?, ?)", [
                    (k, kind, v) for k, up, flat in resale for kind, v in (("resale", up), ("flat", flat))])
                db.executemany("INSERT INTO waiting VALUES (?, 'sale', ?)", waits)
                db.execute("DROP VIEW nights_left")
                db.execute("CREATE TABLE nights_left AS SELECT ? AS nights,"
                           " julianday('2026-08-02T20:24:00Z') AS first", (nights,))
                db.executemany("INSERT INTO model_constants VALUES (variant(), ?, ?)", [
                    ("RESALE_MARGIN", 0.05), ("GOOD_OFFER", 0.9), ("PRICE_WINDOW", 50.0),
                    ("MONEY_RATE", 8.0)])
            store_sql("api_standings", [{"observed_at": "2026-08-01T0000Z", "manager": u.me,
                                         "team_value": str(team_value)}])
            store_sql("api_market", [{"observed_at": "2026-08-01T0000Z", "player_id": k, "seller": "marketPlayerTeam",
                                      "expires_at": e} for k, e in listings])
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
    assert all(CONFIDENCE <= v <= 1.0 for d in doc["do"] for label, v, _unit in d["facts"]
               if label == "chance of helping"), "only moves that clear the bar are shown"
    assert report(ub, b._replace(plan=[]))["cash_after"] is None, \
        "no cash line when the plan moves no money"
    assert [r["estimated"] for r in doc["standings"] if r["me"]] == [False]
    riv_row = next(r for r in doc["standings"] if not r["me"])
    assert abs(riv_row["p_above"] - (1.0 - b.base.beat("riv"))) < 1e-12, \
        "above you: the chance he finishes above you, not that you beat him (JSON keeps 15 digits)"
    assert all(r["estimated"] for r in doc["standings"] if not r["me"]), \
        "rival cash is estimated from the feed"
    assert "squad" not in doc, "no squad table: offers and the market are what to act on"
    cleared = [(m.action.buy, m.action.cost, round(m.d_pts, 6), "do" if m in b.plan else "consider")
               for m in b.rows if m.action.kind == "buy" and verdict(m) is None]
    shown = [(d["name"].lower(), d["amount"], round(d["gain"], 6), sec)
             for sec in ("do", "consider") for d in doc[sec] if d["what"] == "buy"]
    assert cleared and sorted(shown) == sorted(cleared), \
        ("each buy that clears the bar, once: to do if the plan buys him, else to consider", shown)
    assert all(dict((f[0], f[1]) for f in d["facts"])["listed at"] == ub.market.price[d["name"].lower()]
               for sec in ("do", "consider") for d in doc[sec] if d["what"] == "buy"), \
        "what he is listed at, and the bid it takes"
    wins = {m.action.buy: round(m.win, 3) for m in b.rows if m.action.kind == "buy"}
    assert all(dict((f[0], f[1]) for f in d["facts"])["chance to win"] == wins[d["name"].lower()]
               for sec in ("do", "consider") for d in doc[sec] if d["what"] == "buy"), \
        "and the chance that bid wins his auction"
    assert "market" not in doc and "offers" not in doc, "one list of lines, not a section per source"
    listing = doc["do"][-1]
    assert listing["what"] == "list" and set(listing["label"].removeprefix("list ").lower().split(", ")) \
        == set(ub.mine) - set(sold), ("list every player of yours not listed and not sold", listing)
    assert [r["manager"] for r in doc["standings"]][0] in ("me", "riv")
    assert doc["standings"][[r["me"] for r in doc["standings"]].index(True)][
        "p_above"] is None
    json.dumps(doc)
    assert doc["ping"] == "; ".join(d["label"] for d in doc["do"]), \
        "the ping is each move's Action.label, not words of its own"
    assert doc["do"][0]["label"].startswith("field ")

    rich_doc = report(ub, b, team_value=100e6)
    assert rich_doc["can_bid"] == max(0.0, rich_doc["cash_after"] + 20e6), \
        "what you can bid: your cash after the plan and the debt the game allows, 20% of team value"
    stray = replace(ub.market, pos={**ub.market.pos, "ghost": "DEF"}, my_bid={"ghost": 3e6})
    assert abs(report(replace(ub, market=stray), b, team_value=100e6)["can_bid"]
               - (rich_doc["can_bid"] - 3e6)) < 1, \
        "a bid of yours the plan does not make is money you cannot bid again"
    in_plan = replace(ub.market, my_bid={bought[0]: 4e6})
    assert report(replace(ub, market=in_plan), b, team_value=100e6)["can_bid"] == rich_doc["can_bid"], \
        "the plan's own bid is counted once, in the plan"
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

    def sale(offer: float, nights: int = 0) -> tuple[Doc, list[str]]:
        mk = replace(ub.market, offer={"dead": offer}, fetch={"dead": max(offer, 1.0e6)})
        doc = report(replace(ub, market=mk), b._replace(plan=[sell_dead], rows=b.rows + [sell_dead]),
                     waits=[("dead", 1.0e6)], nights=nights)
        return (next(d for d in doc["do"] if d["what"] == "sell"),
                doc["ping"].split("; "))
    (take, said), (wait, quiet) = sale(1.1e6), sale(0.9e6)
    def facts(d: Doc) -> dict[str, Any]:
        return {f[0]: f[1] for f in d["facts"]}
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
    assert "only pays if you win" not in rolls, "a sale that pays by itself waits for no bid"

    def relists(ends: str, nights: int) -> list[tuple[Any, ...]]:
        doc = report(replace(ub, market=replace(ub.market, route={"dead": "listed"})), b,
                     nights=nights, listings=[("dead", ends)])
        return [(d["label"], d["facts"]) for d in doc["do"] if d["what"] == "relist"]
    assert relists("2026-08-03T10:00:00+02:00", 3) == [
        ("relist Dead", [["once the listing ends", "2026-08-03T10:00:00+02:00", "time"]])], \
        "his listing ends before two of the three offers to come: relist him once it does"
    assert relists("2026-08-03T10:00:00+02:00", 1) == relists("2026-08-05T10:00:00+02:00", 3) == [], \
        "a listing that outlasts the offers to come needs nothing"

    # a sale that pays only with the plan's buy, which may be lost at auction
    gamble = Move(Action("buy", buy="cand", cost=2e6), 3.0, p_better=0.9, win=0.5)
    weak = Move(Action("sell", sell=("dead",), proceeds=1e6), -5.0, p_better=0.2)
    def hedge(offer: float, waits: float, nights: int, win: float = 0.5) -> Doc:
        mk = replace(ub.market, route={"dead": "listed"}, offer={"dead": offer}, fetch={"dead": max(offer, waits)})
        both = [replace(gamble, win=win), weak]
        doc = report(replace(ub, market=mk), b._replace(plan=both, rows=b.rows + both),
                     waits=[("dead", waits)], nights=nights)
        return next(d for d in doc["do"] if d["what"] == "sell")
    near = hedge(1.0e6, 0.99e6, 1)
    assert (near["step"], near["amount"], near["done"]) == ("sell after the auctions", 0.99e6, True), near
    assert abs(facts(near)["waiting for them"] - (0.5 * -8.0 * 0.01 + 0.5 * 5.0)) < 1e-9 \
        and facts(near)["only pays if you win"] == "Cand" and facts(near)["chance of winning them"] == 0.5, \
        "won (half the time): waiting loses 0.01M at 8 points a million; lost: the -5 sale is not made"
    assert hedge(1.0e6, 0.3e6, 1)["step"] == "take offer", "waiting loses 0.7M if won: 5.6 points half the time, over 2.5"
    assert hedge(1.0e6, 0.99e6, 0)["step"] == "take offer", "no offer after the auctions before the lock"
    assert hedge(0.9e6, 1.0e6, 1)["step"] == "wait", "an offer under what waiting is worth: wait, bids or not"
    assert "only pays if you win" not in facts(hedge(1.0e6, 0.99e6, 1, win=1.0)), "a buy sure to land"
    weak = replace(weak, d_pts=1.0)
    assert hedge(1.0e6, 0.99e6, 1)["step"] == "take offer" and "only pays if you win" not in facts(
        hedge(1.0e6, 0.99e6, 1)), "a sale that gains by itself, if not surely, waits for no bid"
    assert "sell Dead" not in quiet, quiet

    offered = replace(ub, market=replace(ub.market, offer={"dead": 1.1e6, "star": 19e6},
                                         fetch={"dead": 1.1e6, "star": 19e6}))
    def tips(doc: Doc) -> list[tuple[Any, ...]]:
        return [(d["name"].lower(), d["step"], d["amount"], round(d["gain"], 6),
                 facts(d).get("the game's recent offers")) for d in doc["consider"] if d["what"] == "sell"]
    sales = {m.action.sell[0]: m for m in b.rows if m.action.kind == "sell"}
    told = report(offered, b, ratios=[0.9, 0.95, 1.0, 1.05])
    assert tips(told) == [("dead", "take offer", 1.1e6, round(sales["dead"].d_pts, 6), 1.0)], \
        ("consider selling a listed player whose offer beats waiting and GOOD_OFFER of the game's:"
         " what it costs in points, and how it ranks; Star is not listed, so no offer to take", tips(told))
    assert "star" in told["do"][-1]["label"].lower(), "Star is listed for: no offers come until then"
    assert tips(report(offered, b, ratios=[0.9, 0.95, 1.0, 1.05, 1.2])) == [], \
        "beating 80% of the game's offers is not a good offer"
    assert tips(report(offered, b)) == [], "no past offers, no rank among them: nothing to consider"
    took = report(offered, b._replace(plan=[sales["dead"]]), ratios=[0.9, 0.95, 1.0, 1.05])
    assert tips(took) == [] and [d["step"] for d in took["do"] if d["what"] == "sell"] == ["take offer"], \
        "the plan sells him: to do"

    # buys to resell: the game's market players whose sale before the lock beats the bid
    free = replace(ub, market=replace(ub.market, cash=1e6,
                                      pos={**ub.market.pos, "up": "MED", "thin": "MED", "dear": "MED"},
                                      price={"up": 2e6, "thin": 2e6, "dear": 8e6},
                                      route={"up": "free", "thin": "free", "dear": "free"}))
    # ranked like any buy (the funnel's bid), none worth it for points
    none = b._replace(plan=[], gain=0.0, rows=b.rows + [
        Move(Action("buy", buy=k, cost=c), -1.0, 0.1) for k, c in free.market.price.items()])
    def resold(cash: float, team_value: float, bids: dict[str, float] | None = None,
               **flat: float) -> list[tuple[str, float, Any]]:
        u = replace(free, market=replace(free.market, cash=cash, my_bid=bids or {},
                                         pos={**free.market.pos, "ghost": "DEF"}))
        doc = report(u, none, resale=[("up", 2.4e6, flat.get("up", 2e6)), ("thin", 2.06e6, 2e6),
                                      ("dear", 9e6, flat.get("dear", 8e6))], team_value=team_value)
        return [(d["name"].lower(), d["amount"], round(dict((f[0], f[1]) for f in d["facts"])["profit"]))
                for d in doc["do"] if d["what"] == "resale"]
    assert resold(1e6, 50e6) == [("dear", 8e6, 1e6), ("up", 2e6, 0.4e6)], \
        "most profit first; a 3% margin is under RESALE_MARGIN; 9M of bids on 1M cash is within 20% debt"
    assert resold(1e6, 30e6) == [("up", 2e6, 0.4e6)], "8M of bids is over the 7M allowed: skipped, the next fits"
    assert resold(1e6, 50e6, dear=7.5e6) == [("dear", 8e6, 1e6), ("up", 2e6, 0.4e6)], \
        "flat, he loses 0.5M and Up nothing: 1M of cash covers both"
    assert resold(0.2e6, 50e6, dear=7.5e6, up=1.9e6) == [("up", 2e6, 0.4e6)], \
        "flat, Dear would leave you below zero at the lock; Up loses 0.1M, which 0.2M covers"
    assert resold(1e6, 50e6, bids={"ghost": 3e6}) == [("dear", 8e6, 1e6)], \
        "your bid in on a player nothing here buys leaves 8M of the 11M: Up's 2M no longer fits"
    assert resold(1e6, 50e6, bids={"dear": 8e6}) == [("dear", 8e6, 1e6), ("up", 2e6, 0.4e6)], \
        "your bid in on a buy to resell is its cost, counted once"

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
    assert (me_row["cash"], riv["cash"]) == (ub.market.cash, ub.rival_cash.get("riv", 0.0)), \
        "yours from the app, a rival's as estimated"
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
    assert dead is not None and who["dead"]["per_million"] == dead.per_million, who["dead"]
    assert plan(ub, b, 7) == [{"run_id": 7, "ord": i, "rank": b.rows.index(m)}
                              for i, m in enumerate(b.plan)]
    assert plan_head(b, 7) == {"run_id": 7, "plan_gain": b.gain}

    debt = replace(ub, market=replace(ub.market, cash=-5e6,
                                      value={**ub.market.value, "k": 4e6, "d1": 9e6, "m1": 1e6}))
    owed = board(debt)
    from ffcore.db import named
    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        record_run(debt, owed, 8)
        cols, why = named("why_sell")
    assert cols == ["player", "debt_m", "raises_m", "season_points", "points_per_m",
                    "raised_so_far_m", "debt_left_m", "covers_alone", "in_plan"], cols
    per = [r[4] for r in why]
    assert per == sorted(per, reverse=True), "cheapest points per million first"
    taken = [r for r in why if r[8]]
    assert taken and {r[0] for r in taken} == {
        m.action.label(debt.market.names).removeprefix("sell ") for m in owed.plan
        if m.action.kind == "sell"}, \
        "in_plan is the plan's sales, no others"
    assert all(r[1] == 5.0 for r in why), "the debt, in millions"
    assert all(a[5] <= b[5] for a, b in zip(why, why[1:])), "raised so far only grows"
    assert min(r[6] for r in taken) == 0.0, "the plan's sales clear the debt"
    assert all(r[6] >= 0 for r in why)

    _selftest_lineup()
    print("sim self-test OK")


def _selftest_lineup() -> None:
    """schema.sql's lineup_gap: the app's line-up against your best eleven now
    ('now') and once the plan is made ('plan'), as board_moves and the
    reminder read it."""
    import tempfile
    from contextlib import closing
    from pathlib import Path

    from ffcore.db import connect
    from ffcore.fixtures import lineup_board
    from ffcore.tidy import tables_in

    def gap(app: list[tuple[str, str]], ids: dict[str, str]) -> list[tuple[str, str, str, int]]:
        """The app line-up as (app id, name) rows, with these players' app ids."""
        with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
            lineup_board(app, ids)
            with closing(connect()) as db:
                return [tuple(r) for r in db.execute(
                    "SELECT basis, side, player, legal FROM lineup_gap ORDER BY basis, side, player")]

    lineup = [("1070", "Ionut Radu"), ("2464", "Pepelu")]
    got = gap(lineup, {"ionut radu": "1070"})
    assert got == [("plan", "in", "cai coro", 1), ("plan", "out", "pepelu", 1)], \
        "set as the best eleven now; the plan, which buys cai for pepelu, wants it changed"
    assert gap(lineup + [("999", "Nobody")], {"ionut radu": "1070"}) == [
        ("now", "in", "ionut radu", 0), ("now", "in", "pepelu", 0),
        ("plan", "in", "cai coro", 0), ("plan", "in", "ionut radu", 0)], \
        "a line-up naming a player you do not own is unknown: the whole eleven to set"
    assert gap(lineup, {"ionut radu": "1070", "someone else": "2464"}) == [
        ("now", "in", "ionut radu", 0), ("now", "in", "pepelu", 0),
        ("plan", "in", "cai coro", 0), ("plan", "in", "ionut radu", 0)], \
        "an app id that is another's player is the same"
    assert gap([], {}) == [("now", "in", "ionut radu", 0), ("now", "in", "pepelu", 0),
                           ("plan", "in", "cai coro", 0), ("plan", "in", "ionut radu", 0)], \
        "no line-up given: the whole eleven"


def funnel(u: Universe, b: Board, run: int) -> list[Doc]:
    """Every ranked move, in board order: the funnel's first three steps
    (candidates, rank, verdict) and whether the plan took it."""
    return [{"run_id": run, "rank": i, "kind": m.action.kind,
             "buy": m.action.buy or None, "sell": ",".join(m.action.sell) or None,
             "label": m.action.label(u.market.names),
             "cost": m.action.cost, "proceeds": m.action.proceeds,
             "gain": m.d_pts, "p_better": m.p_better, "per_million": m.per_million,
             "verdict": verdict(m), "in_plan": int(m in b.plan), "money": m.money,
             "win": m.win, "alone": int(m.alone)}
            for i, m in enumerate(b.rows)]


def forecast(u: Universe, run: int) -> list[Doc]:
    """Every forecast cell that has factors, factor by factor
    (ffcore.schedule.Factors), with the expected points they make."""
    return [{"run_id": run, "jornada": j, "player": k, "ppm": f.ppm, "fixture": f.fixture,
             "picked": f.picked, "fit": f.fit, "points": f.cell[0], "plays": f.cell[1],
             "expected": expected_points(f.cell)}
            for j, layer in u.factors.items() for k, f in layer.items()]


def standings(u: Universe, b: Board, run: int) -> list[Doc]:
    """Each manager's season if you do nothing (Board.base): mean
    points and their 10th-90th percentile band, expected final position,
    the chance of first, the chance he finishes above you, and his cash."""
    base = b.base
    return [{"run_id": run, "manager": mgr, "me": int(mgr == u.me),
             "now": u.state.carried.get(mgr, 0.0), "mean": base.mean(mgr),
             "lo": base.band(mgr)[0], "hi": base.band(mgr)[1],
             "finish": base.expected_position(mgr), "p_first": base.position(mgr).get(1, 0.0),
             "cash": u.market.cash if mgr == u.me else u.rival_cash.get(mgr, 0.0),
             "p_above": None if mgr == u.me else 1.0 - base.beat(mgr)}
            for mgr in u.state.squads]


def players(u: Universe, b: Board, run: int) -> list[Doc]:
    """Your squad and the plan's buys as the model sees them: the chance he
    plays next, his expected points next and over the season, whether he is
    in the best eleven now and once the plan is made, and, if yours, the
    points his sale costs per million."""
    o = u.outlook
    exp, xi = o.xi
    plan_xi = u.after(*(r.action for r in b.plan)).outlook.xi.players
    keys = list(u.mine) + [r.action.buy for r in b.plan if r.action.buy and r.action.buy not in u.mine]
    return [{"run_id": run, "player": k, "start": o.next_up.get(k, (0.0, 0.0))[1],
             "next": exp.get(k, 0.0), "season": o.season.get(k, 0.0),
             "mine": int(k in u.mine), "xi": int(k in xi), "plan_xi": int(k in plan_xi),
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
    with closing(connect()) as db, db:
        db.execute("INSERT INTO sim_facts SELECT ?, * FROM sim_facts_now", (run,))
        db.execute("INSERT INTO sim_waiting SELECT ?, player, kind, later FROM waiting", (run,))


def with_squad(u: Universe, squad: dict[str, str], cash: float,
               bids: dict[str, float] | None = None) -> Universe:
    """The league with this squad, cash and bids in (on players still on
    the market) as yours, and none of your own bids or the offers on your
    players (they are not these players')."""
    return replace(u, state=replace(u.state, squads={**u.state.squads, u.me: dict(squad)}),
                   market=replace(u.market, cash=cash, offer={}, fetch={},
                                  my_bid={k: b for k, b in (bids or {}).items() if k in u.market.price}))


def rival_universe(u: Universe, mgr: str) -> Universe:
    """The league as mgr decides in it: his squad, his cash (our estimate),
    you a rival, and none of your bids or the offers on your players."""
    cash = {**u.rival_cash, u.me: u.market.cash}
    return replace(u, state=replace(u.state, me=mgr),
                   market=replace(u.market, cash=cash[mgr], my_bid={}, offer={}, fetch={}),
                   rival_cash={m: c for m, c in cash.items() if m != mgr})


def rival_rows(u: Universe, run: int) -> tuple[list[Doc], list[Doc]]:
    """Every rival's board (the same funnel, him as "me"): per rival his
    cash and what his plan gains, and his plan's moves in order."""
    heads, moves = [], []
    for mgr in u.state.squads:
        if mgr == u.me:
            continue
        ru = rival_universe(u, mgr)
        b = board(ru)
        heads.append({"run_id": run, "manager": mgr, "cash": ru.market.cash, "plan_gain": b.gain})
        moves += [{"run_id": run, "manager": mgr, "ord": i, "label": m.action.label(ru.market.names),
                   "kind": m.action.kind, "gain": m.d_pts, "p_better": m.p_better,
                   "cost": m.action.cost, "proceeds": m.action.proceeds}
                  for i, m in enumerate(b.plan)]
    return heads, moves


def rivals() -> None:
    """The rivals stage (run.py rivals): each rival's board for this run."""
    run = this_run()
    if run is None:
        raise SystemExit("rivals: a board is kept for a run; run it as run.py rivals")
    heads, moves = rival_rows(universe(), run)
    record(heads, "rival_boards")
    record(moves, "rival_moves")
    print("  rivals: %d boards, %d planned moves" % (len(heads), len(moves)))


def board_doc() -> Doc:
    """decisions.json as board_json makes it, from the last run's rows."""
    (doc,), = rows("SELECT json FROM board_json")
    got: Doc = json.loads(doc)
    return got


def main() -> None:
    u = universe()
    if len(u.state.squads) < 2 or not u.state.jornadas:
        print("sim: nothing to rank (%d squads, %d jornadas left)"
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
