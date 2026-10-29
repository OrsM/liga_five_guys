"""Act on the board: place the bid a board line says, when you accept it on
the phone (lfg-watch hands the request here).

The request names the board it was made on (generated_at), the player and
the amount. The bid is placed only if that is still the board, the line is
one of its bids at that amount, and the player is still on the market; it is
then read back from the market. The bid endpoints are the app's own
(fantasy-api.llt-services.com, as LaLigaApp calls them): POST a bid, PUT a
new amount on yours.

    python src/act.py accept REQUEST.json   # what lfg-watch runs; prints the result as JSON
"""
from __future__ import annotations

import json
import math
import sys
from collections.abc import Callable
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ffcore import db as lfg
from ffcore.auth import API_BASE
from ffcore.laliga_api import API_LEAGUES_URL, API_MARKET_URL, parse_api_leagues

# One call to the app: (method, url, JSON body or None) to (status, JSON reply).
Call = Callable[[str, str, dict[str, Any] | None], tuple[int, Any]]

BID_URL = "{base}/v1/competition/1/league/{league}/market/{market}/bid?x-lang=es"
REBID_URL = "{base}/v1/competition/1/league/{league}/market/{market}/bid/{bid}?x-lang=es"

REPORT = Path("reports/decisions.json")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def target(name: str) -> dict[str, str] | None:
    """The plan's buy of this name, as the last run saw him on the app's
    market: his market line and the app's id for him (board_buys,
    current_api_market)."""
    with closing(lfg.connect()) as db:
        row = db.execute(
            "SELECT m.market_id, m.player_id FROM board_buys b"
            " JOIN current_api_market m ON m.player = b.player"
            " WHERE b.in_plan AND b.name = ?", (name,)).fetchone()
    return {"market_id": row[0], "player_id": row[1]} if row else None


def accept(req: dict[str, Any], board: dict[str, Any], where: Callable[[str], dict[str, str] | None],
           call: Call, base: str) -> dict[str, Any]:
    """The bid the request accepts, placed and read back, or why not."""
    out = {"requested_at": req.get("requested_at"), "name": req.get("name"), "amount": req.get("amount")}

    def done(ok: bool, message: str) -> dict[str, Any]:
        return {**out, "state": "placed" if ok else "failed", "message": message, "at": _now()}

    if req.get("generated_at") != board.get("generated_at"):
        return done(False, "the board has changed since: refresh and accept again")
    line = next((d for d in board.get("do", []) if d.get("what") == "buy" and d.get("name") == req.get("name")),
                None)
    if line is None or line.get("amount") is None or round(line["amount"]) != round(req.get("amount") or -1):
        return done(False, "not a bid of this board at this amount")
    money = math.ceil(line["amount"])
    at = where(line["name"])
    if at is None:
        return done(False, "not on the app's market as the last run saw it")
    status, leagues = call("GET", API_LEAGUES_URL.format(base=base), None)
    ids = [r["league_id"] for r in parse_api_leagues(json.dumps(leagues), "")] if status == 200 else []
    if len(ids) != 1:
        return done(False, "the app's leagues: %s" % (status if status != 200 else "%d, not one" % len(ids)))
    league = ids[0]

    def item() -> dict[str, Any] | None:
        status, market = call("GET", API_MARKET_URL.format(base=base, league=league), None)
        if status != 200 or not isinstance(market, list):
            return None
        return next((it for it in market if str(it.get("id")) == at["market_id"]
                     and str((it.get("playerMaster") or {}).get("id")) == at["player_id"]), None)

    it = item()
    if it is None:
        return done(False, "no longer on the market")
    mine = it.get("bid") or {}
    if mine.get("money") == money:
        return done(True, "already bid %d" % money)
    if mine.get("id"):
        status, reply = call("PUT", REBID_URL.format(base=base, league=league, market=at["market_id"],
                                                     bid=mine["id"]), {"money": money})
    else:
        status, reply = call("POST", BID_URL.format(base=base, league=league, market=at["market_id"]),
                             {"money": money})
    if status not in (200, 201):
        return done(False, "the app said %d: %s" % (status, json.dumps(reply)[:200]))
    back = item()
    got = ((back or {}).get("bid") or {}).get("money")
    if got != money:
        return done(False, "sent, but the market shows %s" % got)
    return done(True, "bid %d, read back from the market" % money)


def http_call() -> Call:
    """The app, with your league login (ffcore.auth)."""
    import httpx
    from ffcore.auth import TokenStore
    from ingest import HEADERS, TIMEOUT
    bearer = TokenStore().bearer()
    client = httpx.Client(headers={**HEADERS, "Accept": "application/json",
                                   "Authorization": "Bearer %s" % bearer}, timeout=TIMEOUT)

    def call(method: str, url: str, body: dict[str, Any] | None) -> tuple[int, Any]:
        r = client.request(method, url, json=body)
        try:
            return r.status_code, r.json()
        except ValueError:
            return r.status_code, r.text
    return call


def _selftest() -> None:
    base = "https://api"
    board = {"generated_at": "2026-10-29T15:57Z",
             "do": [{"what": "buy", "name": "Thomas Lemar", "amount": 7044268.7},
                    {"what": "sell", "name": "Gerard Martin", "amount": 46738575.0}]}
    req = {"requested_at": "t", "generated_at": "2026-10-29T15:57Z", "name": "Thomas Lemar", "amount": 7044268.7}
    where = {"Thomas Lemar": {"market_id": "525303444", "player_id": "3182"}}.get

    def app(bid: dict[str, Any] | None, answer: int = 200) -> tuple[Call, list[tuple[str, str, Any]]]:
        sent: list[tuple[str, str, Any]] = []
        state = {"bid": bid}

        def call(method: str, url: str, body: dict[str, Any] | None) -> tuple[int, Any]:
            sent.append((method, url, body))
            if "/leagues?" in url:
                return 200, [{"id": 77, "name": "five guys", "team": {"id": 9}}]
            if method == "GET":
                return 200, [{"id": 525303444, "playerMaster": {"id": 3182}, "bid": state["bid"]},
                             {"id": 1, "playerMaster": {"id": 2}}]
            if answer == 200:
                state["bid"] = {"id": 5, "money": body["money"]} if body else None
            return answer, {}
        return call, sent

    call, sent = app({"id": 54045570, "money": 6674686})
    got = accept(req, board, where, call, base)
    assert got["state"] == "placed", got
    assert [(m, u, b) for m, u, b in sent if m != "GET"] == [
        ("PUT", base + "/v1/competition/1/league/77/market/525303444/bid/54045570?x-lang=es", {"money": 7044269})], \
        ("a bid of yours on him already: raised to the board's amount, whole euros up", sent)
    call, sent = app(None)
    assert accept(req, board, where, call, base)["state"] == "placed" and [m for m, _u, _b in sent if m != "GET"] == \
        ["POST"], "none yet: placed"
    call, sent = app({"id": 5, "money": 7044269})
    assert accept(req, board, where, call, base)["message"] == "already bid 7044269" and all(
        m == "GET" for m, _u, _b in sent), "already at that amount: nothing sent"
    call, sent = app(None, answer=400)
    assert accept(req, board, where, call, base)["state"] == "failed", "the app refuses: said so"
    call, sent = app(None)
    for bad, why in (({**req, "generated_at": "older"}, "a board since"),
                     ({**req, "amount": 9e6}, "another amount"),
                     ({**req, "name": "Gerard Martin", "amount": 46738575.0}, "a sale, not a bid")):
        assert accept(bad, board, where, call, base)["state"] == "failed", why
    assert sent == [], "refused before any call to the app"
    assert accept(req, board, {}.get, call, base)["state"] == "failed", "not on the market"
    print("act self-test OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    elif sys.argv[1:2] == ["accept"]:
        request = json.loads(Path(sys.argv[2]).read_text())
        result = accept(request, json.loads(REPORT.read_text()), target, http_call(), API_BASE)
        print(json.dumps(result))
        raise SystemExit(0 if result["state"] == "placed" else 1)
    else:
        print(__doc__)
        raise SystemExit(2)
