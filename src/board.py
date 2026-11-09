"""The board stage: decisions.json, the phone's copy of the board, from the league at t. Your cash and what you can
bid, your squad with its eleven, the players on the market who would add points to it (buys, most first, each with
the bid to make and, for the first few, the sales that fund it: approach.approach), and each standing offer on your
players ranked by what taking it costs your eleven (eleven.remove). The one line act.py takes is do: list your unlisted players at
their value. finish and band are placeholders: the page reads them."""
from __future__ import annotations

import json
import sys
from contextlib import closing
from typing import Any

import decision
import eleven
from approach import Approach, Club, approach
from assemble import built
from ffcore.db import connect, cut, record, rows, this_run
from ffcore.jornadas import rounds
from ffcore.rules import POSITIONS, bid_limit
from ffcore.tidy import REPORTS

__all__ = ["board", "main"]

Doc = dict[str, Any]
AHEAD = (4, 8, 0)
TOP_BUYS = 10
TOP_FUNDED = 3
TOP_TARGETS = 10


def stamped(t: str) -> str:
    return t[:13] + ":" + t[13:]


def squad_rows(inp: dict[str, list[tuple[Any, ...]]]) -> tuple[list[Doc], dict[str, str]]:
    pos, cells = decision.cells_of(inp)
    mine = frozenset(k for k, _, m, *_ in inp["dec_player"] if m)
    names = {k: n for k, n in rows("SELECT player, name_shown(name) FROM player_facts")}
    e = cells.get(min((j for j, _ in inp["dec_lock"]), default=min(cells, default=0)), {})
    settled = eleven.best_eleven(mine, pos, e)
    xi = settled.eleven if settled else frozenset()
    order = sorted(mine, key=lambda k: (k not in xi, POSITIONS.index(pos[k]) if k in pos else len(POSITIONS), -e.get(k, 0.0), k))
    return [{"name": names.get(k, k), "pos": pos.get(k, ""), "next": e.get(k, 0.0), "xi": k in xi, "sold": False,
             "key": k} for k in order], names


def standing(mine: set[str]) -> list[tuple[Any, ...]]:
    return rows("SELECT t.player, t.market_value, o.money, f.pos, f.trend FROM offers o"
                " JOIN squads t ON t.player_team_id = o.player_team_id LEFT JOIN player_facts f ON f.player = t.player"
                " WHERE o.status = 'pending' AND o.money <> 0 AND t.player IN (%s)" % ",".join("?" * len(mine)),
                sorted(mine))


def offer_lines(inp: dict[str, list[tuple[Any, ...]]], names: dict[str, str], got: list[tuple[Any, ...]]) -> list[Doc]:
    pos, cells = decision.cells_of(inp)
    mine = frozenset(k for k, _, m, *_ in inp["dec_player"] if m)
    js = sorted(cells)
    sides = {}
    for n in AHEAD:
        value = decision.window_value(pos, cells, frozenset(js[:n or len(js)]))
        sides[n] = (value, eleven.best_eleven(mine, pos, value))
    out = []
    for k, value, money, p, trend in got:
        lost = {}
        for n, (v, settled) in sides.items():
            gone = eleven.remove(k, settled.eleven, settled.bench, pos, v) if settled else None
            lost[n] = gone.cost if gone else (eleven.total(settled.eleven, v) if settled else 0.0)
        ratio = money / value if value else None
        name = names.get(k, k)
        out.append({
            "name": name, "pos": p or pos.get(k, ""), "what": "sell", "label": "sell " + name, "step": "take offer",
            "offer": money, "waiting": None, "trend": trend, "plan": False, "amount": money, "done": False, "key": k,
            "gain": 0.0 - lost[0], "per": "season", "more": False, "detail": None, "trace": None, "flip": None,
            "ratio": ratio, "lost": {"next4": lost[4], "next8": lost[8], "season": lost[0]},
            "per_million": lost[8] / (money / 1e6) if money else None,
            "facts": [["value", value, "money"], ["offer", money, "money"], ["offer over value", ratio, "ratio"],
                      ["points lost, next 4", lost[4], "points"], ["next 8", lost[8], "points"],
                      ["season", lost[0], "points"]],
            "accepts": [], "act": []})
    return sorted(out, key=lambda d: (d["per_million"] is None, d["per_million"], d["key"]))


def funding_doc(club: Club, names: dict[str, str], found: Approach | None, bid: float) -> Doc:
    if found is None:
        return {"feasible": False, "short": None}
    if not found.feasible:
        return {"feasible": False, "short": found.short, "gain": found.gain}
    tonight = sorted(found.tonight, key=lambda k: (-club.offers[k], k))
    return {"feasible": True, "short": 0.0,
            "tonight": [{"name": names.get(k, k), "offer": club.offers[k],
                         "ratio": club.offers[k] / club.worth[k] if club.worth[k] else None} for k in tonight],
            "raises": found.raised, "margin": found.margin, "lock_margin": found.lock_margin,
            "lock_margin_low": found.lock_margin_low,
            "if_won": [names.get(k, k) for k in sorted(found.if_won)], "if_lost": [names.get(k, k) for k in sorted(found.if_lost)],
            "held": [names.get(k, k) for k in sorted(found.held)], "cash_if_lost": found.cash_if_lost,
            "gain": found.gain, "points_lost_if_won": found.cost_won, "points_lost_if_lost": found.cost_lost, "net": found.net}


def buy_lines(inp: dict[str, list[tuple[Any, ...]]], names: dict[str, str], club: Club, got: list[dict[str, Any]]) -> list[Doc]:
    pos, cells = decision.cells_of(inp)
    odds: dict[str, list[tuple[float, float]]] = {}
    for k, r, w in sorted(inp["dec_odds"]):
        odds.setdefault(k, []).append((r, w))
    bids = {k: amount for k, amount in inp["dec_bid"]}
    out = []
    for i, r in enumerate([r for r in got if r["status"] == "market" and r["gain"] > 0][:TOP_BUYS]):
        k, placed = r["player"], r["placed"]
        amount = placed if placed is not None else r["bid"]
        win = decision.win_at(r["asking"], odds.get(k, []), placed) if placed is not None else r["win"]
        others = (len(bids) - (k in bids), sum(bids.values()) - bids.get(k, 0.0))
        where = frozenset(j for j in cells if j >= r["from_j"])
        funding = funding_doc(club, names, approach(club._replace(pending=others), k, amount, win,
                                                    decision.window_value(pos, cells, where)), amount) if i < TOP_FUNDED else None
        name = names.get(k, k)
        out.append({
            "name": name, "pos": r["pos"], "what": "buy", "label": "buy " + name, "step": "placed" if placed is not None else "bid",
            "price": r["asking"], "asking": r["asking"], "amount": amount, "win": win, "plan": False, "done": placed is not None,
            "key": k, "gain": r["gain"], "per": "season", "more": False, "detail": None, "trace": None, "flip": None,
            "replaces": names.get(r["replaces"], r["replaces"]), "per_million": r["per_million"],
            "price_next": r["price_next"], "price_lock": r["price_lock"],
            "facts": [["asking", r["asking"], "money"], ["bid", amount, "money"], ["chance to win", win, "chance"],
                      ["points added", r["gain"], "points"]],
            "funding": funding, "accepts": [], "act": []})
    return out


def target_lines(names: dict[str, str], got: list[dict[str, Any]]) -> list[Doc]:
    return [{"name": names.get(r["player"], r["player"]), "pos": r["pos"], "status": r["status"], "gain": r["gain"],
             "asking": r["asking"], "bid": r["bid"], "win": r["win"], "replaces": names.get(r["replaces"], r["replaces"]),
             "per_million": r["per_million"], "price_next": r["price_next"], "price_lock": r["price_lock"],
             "available": r["status"] == "market"}
            for r in [r for r in got if r["gain"] > 0][:TOP_TARGETS]]


def list_line(inp: dict[str, list[tuple[Any, ...]]], names: dict[str, str]) -> list[Doc]:
    mine = {k for k, _, m, *_ in inp["dec_player"] if m}
    listed = {k for (k,) in rows("SELECT player FROM listings WHERE player IS NOT NULL")}
    got = {k: (ptid, pid, value, p) for k, ptid, pid, value, p in rows(
        "SELECT t.player, t.player_team_id, t.player_id, t.market_value, f.pos FROM squads t"
        " LEFT JOIN player_facts f ON f.player = t.player WHERE t.player IN (%s)" % ",".join("?" * len(mine)), sorted(mine))}
    todo = sorted(mine - listed, key=lambda k: (names.get(k, k), k))
    if not todo:
        return []
    label = "list " + ", ".join(names.get(k, k) for k in todo)
    return [{"name": ", ".join(names.get(k, k) for k in todo), "pos": "", "what": "list", "label": label, "step": "list",
             "amount": None, "done": False, "accepts": [None], "gain": None, "per": None, "facts": [], "more": False,
             "detail": None, "trace": None, "flip": None, "plan": False,
             "act": [{"player_team": got[k][0], "player": got[k][1] or k, "market": None, "bid": None, "pos": got[k][3],
                      "value": got[k][2]} for k in todo if k in got]}]


def funds(buys: list[Doc], sells: list[Doc]) -> None:
    for sale in sells:
        sale["for"] = [b["name"] for b in buys
                       if any(t["name"] == sale["name"] for t in (b.get("funding") or {}).get("tonight", []))]


def market_at() -> str | None:
    (at,), = rows("SELECT at FROM market_read")
    return stamped(at) if at else None


def sources() -> list[Doc]:
    return [{"src": src, "at": stamped(at), "hours": hours, "status": status}
            for src, at, hours, status in rows("SELECT src, last_at, hours_since, status FROM parser_health ORDER BY src")]


def board(inp: dict[str, list[tuple[Any, ...]]], got: list[dict[str, Any]], arrivals: list[str]) -> Doc:
    (cash, team_value, debt, _squad_max, _xi), = inp["dec_money"]
    locks = [lock for _, lock in inp["dec_lock"]]
    bids = sum(amount for _, amount in inp["dec_bid"])
    squad, names = squad_rows(inp)
    mine = {k for k, _, m, *_ in inp["dec_player"] if m}
    offered = standing(mine)
    pos = {k: p for k, p, *_ in inp["dec_player"] if p in POSITIONS}
    club = Club(frozenset(mine), pos, {k: value for k, value, *_ in rows("SELECT player, market_value FROM squads") if k in mine},
                {k: money for k, _, money, *_ in offered}, cash, team_value, (len(inp["dec_bid"]), bids), debt=debt)
    top = [r for r in got if r["gain"] > 0][:TOP_TARGETS]
    alerts = [{"name": names.get(r["player"], r["player"]), "gain": r["gain"], "asking": r["asking"], "bid": r["bid"],
               "win": r["win"]} for r in top if r["player"] in arrivals]
    buys = buy_lines(inp, names, club, got)
    sells = offer_lines(inp, names, offered)
    funds(buys, sells)
    return {
        "generated_at": stamped(cut()), "market_at": market_at(), "sources": sources(),
        "lock_at": min(locks).replace("Z", ":00+00:00") if locks else None,
        "cash": cash, "cash_locked": bids, "can_bid": max(0.0, bid_limit(cash, team_value, debt) - bids),
        "ping": "on the market: " + ", ".join("%s (+%.0f points)" % (a["name"], a["gain"]) for a in alerts) if alerts else "",
        "do": list_line(inp, names), "buys": buys, "targets": target_lines(names, got),
        "alerts": alerts,
        "sells": sells,
        "squad": [{k: v for k, v in r.items() if k != "key"} for r in squad],
        "finish": 0.0, "band": [0.0, 0.0]}


def main() -> None:
    b = built()
    (managers, ), = rows("SELECT count(*) FROM cash")
    left = rounds().rem
    if managers < 2 or not left:
        print("board: nothing to decide (%d managers, %d jornadas left)" % (managers, len(left)))
        return
    run = this_run()
    if run is None:
        raise SystemExit("board: the board is the run's: run it as run.py board")
    record([{"run_id": run, **r} for r in b.params], "params")
    with closing(connect()) as db, db:
        db.execute("UPDATE forecast_moment SET pinned = 1 WHERE moment = ?", (b.moment,))
    inp = decision.read_inputs()
    got = decision.opportunity_rows(inp)
    decision.keep_opportunities(cut(), got)
    doc = board(inp, got, decision.read_arrivals())
    REPORTS.mkdir(exist_ok=True)
    (REPORTS / "decisions.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("wrote %s" % (REPORTS / "decisions.json"))


def _selftest() -> None:
    assert stamped("2026-10-30T1809Z") == "2026-10-30T18:09Z"
    buys: list[Doc] = [{"name": "Raphinha", "funding": {"tonight": [{"name": "Pepelu"}, {"name": "Alonso"}]}},
                       {"name": "Gueye", "funding": None}, {"name": "Nobody", "funding": {"feasible": False}}]
    sells: list[Doc] = [{"name": "Pepelu"}, {"name": "Ryan"}]
    funds(buys, sells)
    assert [s["for"] for s in sells] == [["Raphinha"], []], "an offer is taken tonight when a funded buy sells him tonight"
    print("board self-test OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        raise SystemExit(0)
    main()
