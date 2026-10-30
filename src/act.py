"""Act on the board: place the bid a board line says, list its list line,
or take the offer its sell line takes, when you accept it on the phone
(lfg-watch hands the request here).

The request names the board it was made on (generated_at), the player and
the amount. The bid is placed only if that is still the board, the line is
one of its bids at that amount or at his asking price, and the player is still on the market; it is
then read back from the market. The bid endpoints are the app's own
(fantasy-api.llt-services.com, as LaLigaApp calls them): POST a bid, PUT a
new amount on yours.

    python src/act.py accept REQUESTS.json  # what lfg-watch runs: each request's result, as JSON
"""
from __future__ import annotations

import json
import math
import sys
import time
from collections.abc import Callable
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ffcore import db as lfg
from ffcore.auth import API_BASE, TokenStore
from ffcore.laliga_api import API_LEAGUES_URL, API_MARKET_URL, API_OFFER_URL, parse_api_leagues
from ingest import HEADERS, TIMEOUT

# One call to the app: (method, url, JSON body or None) to (status, JSON reply).
Call = Callable[[str, str, dict[str, Any] | None], tuple[int, Any]]

BID_URL = "{base}/v1/competition/1/league/{league}/market/{market}/bid?x-lang=es"
SELL_URL = "{base}/v1/competition/1/league/{league}/market/sell?x-lang=es"
REBID_URL = "{base}/v1/competition/1/league/{league}/market/{market}/bid/{bid}?x-lang=es"
PLAYER_URL = "{base}/v1/competition/1/player/{player}/league/{league}?x-lang=es"
ACCEPT_URL = "{base}/v1/competition/1/league/{league}/market/{market}/offer/{offer}/accept?x-lang=es"

REPORT = Path("reports/decisions.json")

# The market shows a bid a moment after the app takes it (19:26 on
# 2026-10-29: placed, absent from the read right after): read back this many
# times, this many seconds apart.
READS = 5
READ_WAIT = 2.0


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


def owned(name: str) -> dict[str, Any] | None:
    """A player of yours the board would list or sell, as the app knows him:
    his place in your team (the id the app lists him by), his id, his value,
    and his market line if listed (board_mine, current_api_teams,
    player_facts, current_api_market)."""
    with closing(lfg.connect()) as db:
        row = db.execute(
            "SELECT t.player_team_id, t.player_id, f.value, k.market_id FROM board_mine m"
            " JOIN current_api_teams t ON t.player = m.player JOIN player_facts f ON f.player = m.player"
            " LEFT JOIN current_api_market k ON k.player = m.player"
            " WHERE m.name = ?", (name,)).fetchone()
    return {"player_team_id": row[0], "player_id": row[1], "value": row[2], "market_id": row[3]} \
        if row and row[2] else None


def league_of(call: Call, base: str) -> str:
    """Your league's id in the app, or "!" and why not."""
    status, leagues = call("GET", API_LEAGUES_URL.format(base=base), None)
    ids = [r["league_id"] for r in parse_api_leagues(json.dumps(leagues), "")] if status == 200 else []
    if len(ids) != 1:
        return "!the app's leagues: %s" % (status if status != 200 else "%d, not one" % len(ids))
    return ids[0]


def list_all(req: dict[str, Any], board: dict[str, Any], own: Callable[[str], dict[str, Any] | None],
             call: Call, base: str) -> dict[str, Any]:
    """The players the board's list line names, each put on the market at
    his value (as the app does by default), or why not. Offers on them are
    yours to take or turn down; a listed player still plays for you."""
    out = {"requested_at": req.get("requested_at"), "what": "list", "name": req.get("name")}
    line = next((d for d in board.get("do", []) if d.get("what") == "list"), None)
    names = line["label"].removeprefix("list ").split(", ") if line else []

    def done(ok: bool, message: str) -> dict[str, Any]:
        return {**out, "state": "placed" if ok else "failed", "message": message, "at": _now()}

    if req.get("generated_at") != board.get("generated_at"):
        return done(False, "the board has changed since: refresh and accept again")
    if not names:
        return done(False, "nothing for this board to list")
    them = {n: own(n) for n in names}
    if any(t is None for t in them.values()):
        return done(False, "not yours as the last run saw them: %s" % ", ".join(n for n, t in them.items() if t is None))
    league = league_of(call, base)
    if league.startswith("!"):
        return done(False, league[1:])
    listed: list[str] = []
    failed: list[str] = []
    for n, t in them.items():
        assert t is not None
        # Never under his value now: it moves overnight, and a board from
        # the night before has the old one (the app refuses a price under it).
        status, now = call("GET", PLAYER_URL.format(base=base, player=t["player_id"], league=league), None)
        live = ((now.get("playerMaster") or {}).get("marketValue") or 0) if status == 200 and isinstance(now, dict) else 0
        status, reply = call("POST", SELL_URL.format(base=base, league=league),
                             {"playerId": int(t["player_team_id"]), "salePrice": int(round(max(t["value"], live)))})
        (listed if status in (200, 201) else failed).append(
            n if status in (200, 201) else "%s (the app said %d: %s)" % (n, status, json.dumps(reply)[:120]))
    if failed:
        return done(False, "listed %s; not %s" % (", ".join(listed) or "none", "; ".join(failed)))
    return done(True, "listed %s at their value, the app took each" % ", ".join(listed))


def take(req: dict[str, Any], board: dict[str, Any], own: Callable[[str], dict[str, Any] | None],
         call: Call, base: str) -> dict[str, Any]:
    """The game's offer the board's sell line takes, accepted, or why not:
    read from the app first, and accepted only while it stands at the
    board's amount."""
    out = {"requested_at": req.get("requested_at"), "what": "sell", "name": req.get("name"),
           "amount": req.get("amount")}

    def done(ok: bool, message: str) -> dict[str, Any]:
        return {**out, "state": "placed" if ok else "failed", "message": message, "at": _now()}

    if req.get("generated_at") != board.get("generated_at"):
        return done(False, "the board has changed since: refresh and accept again")
    line = next((d for d in board.get("do", []) if d.get("what") == "sell" and d.get("step") == "take offer"
                 and d.get("name") == req.get("name")), None)
    if line is None or round(line["amount"]) != round(req.get("amount") or -1):
        return done(False, "not an offer of this board at this amount")
    t = own(line["name"])
    if t is None or not t.get("market_id"):
        return done(False, "not yours on the market as the last run saw it")
    league = league_of(call, base)
    if league.startswith("!"):
        return done(False, league[1:])
    status, standing = call("GET", API_OFFER_URL.format(base=base, league=league, ptid=t["player_team_id"]), None)
    offer = next((o for o in standing if o.get("status") == "pending"
                  and round(o.get("money") or -1) == round(line["amount"])), None) \
        if status == 200 and isinstance(standing, list) else None
    if offer is None:
        return done(False, "the app shows no offer of %d on him now" % round(line["amount"]))
    status, reply = call("POST", ACCEPT_URL.format(base=base, league=league, market=t["market_id"],
                                                   offer=offer["id"]), {"offerMoney": offer["money"]})
    if status not in (200, 201, 204):
        return done(False, "the app said %d: %s" % (status, json.dumps(reply)[:200]))
    return done(True, "sold for %d, the app took it" % offer["money"])


def accept(req: dict[str, Any], board: dict[str, Any], where: Callable[[str], dict[str, str] | None],
           call: Call, base: str, nap: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """The bid the request accepts, placed and read back, or why not."""
    out = {"requested_at": req.get("requested_at"), "name": req.get("name"), "amount": req.get("amount")}

    def done(ok: bool, message: str) -> dict[str, Any]:
        return {**out, "state": "placed" if ok else "failed", "message": message, "at": _now()}

    if req.get("generated_at") != board.get("generated_at"):
        return done(False, "the board has changed since: refresh and accept again")
    line = next((d for d in board.get("do", []) if d.get("what") == "buy" and d.get("name") == req.get("name")),
                None)
    amounts = [a for a in (line.get("amount"), line.get("asking")) if a is not None] if line else []
    chosen = next((a for a in amounts if round(a) == round(req.get("amount") or -1)), None)
    if line is None or chosen is None:
        return done(False, "not a bid of this board at this amount")
    money = math.ceil(chosen)
    at = where(line["name"])
    if at is None:
        return done(False, "not on the app's market as the last run saw it")
    league = league_of(call, base)
    if league.startswith("!"):
        return done(False, league[1:])

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
    if isinstance(reply, dict) and reply.get("money") == money and reply.get("status") == "pending":
        return done(True, "bid %d, the app confirmed" % money)
    got = None
    for i in range(READS):
        if i:
            nap(READ_WAIT)
        got = ((item() or {}).get("bid") or {}).get("money")
        if got == money:
            break
    if got != money:
        return done(False, "sent, but the market shows %s" % got)
    return done(True, "bid %d, read back from the market" % money)


def http_call() -> Call:
    """The app, with your league login (ffcore.auth)."""
    import httpx
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
             "do": [{"what": "buy", "name": "Thomas Lemar", "amount": 7044268.7, "asking": 6356402.0},
                    {"what": "sell", "name": "Gerard Martin", "amount": 46738575.0}]}
    req = {"requested_at": "t", "generated_at": "2026-10-29T15:57Z", "name": "Thomas Lemar", "amount": 7044268.7}
    where = {"Thomas Lemar": {"market_id": "525303444", "player_id": "3182"}}.get

    def app(bid: dict[str, Any] | None, answer: int = 200, shown_after: int = 0,
            confirm: bool = False) -> tuple[Call, list[tuple[str, str, Any]]]:
        sent: list[tuple[str, str, Any]] = []
        state: dict[str, Any] = {"bid": bid, "hidden": 0}

        def call(method: str, url: str, body: dict[str, Any] | None) -> tuple[int, Any]:
            sent.append((method, url, body))
            if "/leagues?" in url:
                return 200, [{"id": 77, "name": "five guys", "team": {"id": 9}}]
            if method == "GET" and "/player/" in url:
                return 200, {"playerMaster": {"marketValue": 600000 if "/player/2687/" in url else None}}
            if method == "GET":
                shown = state["bid"] if state["hidden"] <= 0 else None
                state["hidden"] -= 1
                return 200, [{"id": 525303444, "playerMaster": {"id": 3182}, "bid": shown},
                             {"id": 1, "playerMaster": {"id": 2}}]
            if answer == 200:
                if body and "money" in body:
                    state["bid"] = {"id": 5, "money": body["money"]}
                state["hidden"] = shown_after
            return answer, ({"id": "5", "money": body["money"], "status": "pending"}
                            if confirm and body and "money" in body else {})
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
    call, sent = app(None, shown_after=2)
    naps: list[float] = []
    assert accept(req, board, where, call, base, naps.append)["state"] == "placed" and naps == [READ_WAIT] * 2, \
        "the market shows a new bid a moment late: read again before calling it lost"
    call, sent = app(None, shown_after=9)
    assert accept(req, board, where, call, base, naps.append)["message"].startswith("sent, but"), \
        "and not for ever"
    call, sent = app(None, shown_after=99, confirm=True)
    got = accept(req, board, where, call, base, naps.append)
    assert got["state"] == "placed" and got["message"] == "bid 7044269, the app confirmed", \
        "the app's own answer is the bid, pending: the market shows it later (over 10s, 2026-10-29)"
    call, sent = app(None)
    assert accept({**req, "amount": 6356402.0}, board, where, call, base)["state"] == "placed" and \
        [b for m, _u, b in sent if m != "GET"] == [{"money": 6356402}], "or at his asking price: the stingy bid"
    call, sent = app(None, answer=400)
    assert accept(req, board, where, call, base)["state"] == "failed", "the app refuses: said so"
    call, sent = app(None)
    for bad, why in (({**req, "generated_at": "older"}, "a board since"),
                     ({**req, "amount": 9e6}, "another amount"),
                     ({**req, "name": "Gerard Martin", "amount": 46738575.0}, "a sale, not a bid")):
        assert accept(bad, board, where, call, base)["state"] == "failed", why
    assert sent == [], "refused before any call to the app"
    assert accept(req, board, lambda _n: None, call, base)["state"] == "failed", "not on the market"
    listing = {"generated_at": board["generated_at"],
               "do": [{"what": "list", "label": "list Bil Nsongo, Manu Bueno"}]}
    mine = {"Bil Nsongo": {"player_team_id": "108468054", "player_id": "2973", "value": 9697829.0},
            "Manu Bueno": {"player_team_id": "108468059", "player_id": "2687", "value": 592909.0}}
    call, sent = app(None)
    got = list_all({"requested_at": "t", "generated_at": board["generated_at"], "what": "list"},
                   listing, mine.get, call, base)
    assert got["state"] == "placed" and got["message"].startswith("listed Bil Nsongo, Manu Bueno"), got
    assert [(m, u, b) for m, u, b in sent if m != "GET"] == [
        ("POST", base + "/v1/competition/1/league/77/market/sell?x-lang=es", {"playerId": 108468054, "salePrice": 9697829}),
        ("POST", base + "/v1/competition/1/league/77/market/sell?x-lang=es", {"playerId": 108468059, "salePrice": 600000})], \
        ("each player of the board's list line, by his place in your team, at his value: the app's now if"
         " it has risen since the board (Lemar, 2026-10-30: listed at the night before's, refused)", sent)
    call, sent = app(None)
    assert list_all({"generated_at": "older", "what": "list"}, listing, mine.get, call, base)["state"] == "failed" \
        and sent == [], "a board since: nothing sent"
    assert list_all({"generated_at": board["generated_at"], "what": "list"}, listing, lambda _n: None, call, base)["state"] \
        == "failed" and sent == [], "not yours: nothing sent"
    call, sent = app(None, answer=400)
    assert list_all({"generated_at": board["generated_at"], "what": "list"}, listing, mine.get, call, base)["state"] \
        == "failed", "the app refuses: said so"
    offers = {"generated_at": board["generated_at"],
              "do": [{"what": "sell", "name": "Pablo Fornals", "step": "take offer", "amount": 44133958.0}]}
    fornals = {"Pablo Fornals": {"player_team_id": "32308191", "player_id": "3099", "value": 40916093.0,
                                 "market_id": "50121357"}}
    ask = {"requested_at": "t", "generated_at": board["generated_at"], "what": "sell", "name": "Pablo Fornals",
           "amount": 44133958.0}

    def seller(standing: list[dict[str, Any]], answer: int = 200) -> tuple[Call, list[tuple[str, str, Any]]]:
        sent: list[tuple[str, str, Any]] = []

        def call(method: str, url: str, body: dict[str, Any] | None) -> tuple[int, Any]:
            sent.append((method, url, body))
            if "/leagues?" in url:
                return 200, [{"id": 77, "name": "five guys", "team": {"id": 9}}]
            if method == "GET":
                return 200, standing
            return answer, {}
        return call, sent

    call, sent = seller([{"id": 197765870, "money": 44133958, "status": "pending"}])
    got = take(ask, offers, fornals.get, call, base)
    assert got["state"] == "placed" and got["message"] == "sold for 44133958, the app took it", got
    assert [(m, u, b) for m, u, b in sent if m != "GET"] == [
        ("POST", base + "/v1/competition/1/league/77/market/50121357/offer/197765870/accept?x-lang=es",
         {"offerMoney": 44133958})], ("the offer the app shows standing, by his market line", sent)
    call, sent = seller([{"id": 1, "money": 39000000, "status": "pending"}])
    assert take(ask, offers, fornals.get, call, base)["state"] == "failed" and \
        all(m == "GET" for m, _u, _b in sent), "no longer at the board's amount: nothing accepted"
    call, sent = seller([{"id": 197765870, "money": 44133958, "status": "pending"}], answer=400)
    assert take(ask, offers, fornals.get, call, base)["state"] == "failed", "the app refuses: said so"
    call, sent = seller([])
    for bad, why in (({**ask, "generated_at": "older"}, "a board since"), ({**ask, "amount": 4e7}, "another amount"),
                     ({**ask, "name": "Thomas Lemar"}, "not a sale of the board")):
        assert take(bad, offers, fornals.get, call, base)["state"] == "failed" and sent == [], why
    assert take(ask, offers, lambda _n: None, call, base)["state"] == "failed" and sent == [], "not yours"
    print("act self-test OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    elif sys.argv[1:2] == ["accept"]:
        asked = json.loads(Path(sys.argv[2]).read_text())
        board, call = json.loads(REPORT.read_text()), http_call()
        print(json.dumps([list_all(r, board, owned, call, API_BASE) if r.get("what") == "list"
                          else take(r, board, owned, call, API_BASE) if r.get("what") == "sell"
                          else accept(r, board, target, call, API_BASE)
                          for r in (asked if isinstance(asked, list) else [asked])]))
    else:
        print(__doc__)
        raise SystemExit(2)
