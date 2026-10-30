"""Act on the board: what a line says, done in the app when you accept it on
the phone (lfg-watch hands the request here).

A request names the board it was made on (generated_at), the line (its
label) and, for a bid, the amount: the line's or his asking price. It is
acted on only if that is still the board and the line one the app can do.
Each line carries what it acts on as the app knows it (board_json's act:
each player's place in your team, market line, bid, position and value),
so nothing here reads lfg.db. The calls are the app's own
(fantasy-api.llt-services.com, as LaLigaApp makes them), and what each
leaves is read back from the app where the app shows it late.

    python src/act.py accept REQUESTS.json  # what lfg-watch runs: each request's result, as JSON
"""
from __future__ import annotations

import json
import math
import sys
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ffcore.auth import API_BASE, TokenStore
from ffcore.laliga_api import API_LEAGUES_URL, API_MARKET_URL, API_OFFER_URL, parse_api_leagues
from ffcore.rules import XI_SIZE
from ingest import HEADERS, TIMEOUT

# One call to the app: (method, url, JSON body or None) to (status, JSON reply).
Call = Callable[[str, str, dict[str, Any] | None], tuple[int, Any]]
Nap = Callable[[float], None]

LEAGUE = "{base}/v1/competition/1/league/{league}"
BID_URL = LEAGUE + "/market/{market}/bid?x-lang=es"
REBID_URL = LEAGUE + "/market/{market}/bid/{bid}?x-lang=es"
CANCEL_URL = LEAGUE + "/market/{market}/bid/{bid}/cancel?x-lang=es"
SELL_URL = LEAGUE + "/market/sell?x-lang=es"
ACCEPT_URL = LEAGUE + "/market/{market}/offer/{offer}/accept?x-lang=es"
PLAYER_URL = "{base}/v1/competition/1/player/{player}/league/{league}?x-lang=es"
TEAM_URL = "{base}/v1/competition/1/leagues/{league}/teams/{team}?x-lang=es"
LINEUP_URL = "{base}/v1/competition/1/teams/{team}/lineup?x-lang=es"
# The app's line-up, slot by position.
SLOTS = {"POR": "goalkeeper", "DEF": "defender", "MED": "midfield", "DEL": "striker"}
OK = (200, 201, 204)

REPORT = Path("reports/decisions.json")

# The app shows what it took a moment later (19:26 on 2026-10-29: a bid
# placed, absent from the read right after): read back this many times,
# this many seconds apart.
READS = 5
READ_WAIT = 2.0


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class App:
    """The app, as one request acts on it: your league and team in it, and
    each call made through call."""

    def __init__(self, call: Call, base: str, league: str, team: str, nap: Nap) -> None:
        self.call, self.base, self.league, self.team, self.nap = call, base, league, team, nap

    def url(self, template: str, **ids: Any) -> str:
        return template.format(base=self.base, league=self.league, team=self.team, **ids)

    def get(self, template: str, **ids: Any) -> Any:
        """A read, None unless the app answers it."""
        status, reply = self.call("GET", self.url(template, **ids), None)
        return reply if status == 200 else None

    def send(self, method: str, template: str, body: dict[str, Any] | None = None, **ids: Any) -> str | None:
        """A change, None if the app takes it, else what it said."""
        status, reply = self.call(method, self.url(template, **ids), body)
        return None if status in OK else "the app said %d: %s" % (status, json.dumps(reply)[:200])

    def until(self, read: Callable[[], Any], want: Any) -> Any:
        """What read gives, read again (READS times, READ_WAIT apart) until it is want."""
        got = None
        for i in range(READS):
            if i:
                self.nap(READ_WAIT)
            if (got := read()) == want:
                break
        return got


def home(call: Call, base: str) -> tuple[str, str] | str:
    """Your league's id in the app and your team's in it, or "!" and why not."""
    status, leagues = call("GET", API_LEAGUES_URL.format(base=base), None)
    got = [(r["league_id"], r["team_id"]) for r in parse_api_leagues(json.dumps(leagues), "")] \
        if status == 200 else []
    if len(got) != 1:
        return "!the app's leagues: %s" % (status if status != 200 else "%d, not one" % len(got))
    return got[0]


def bid(app: App, line: dict[str, Any], amount: float) -> tuple[bool, str]:
    """A bid on his market line at amount, whole euros up: placed, or yours raised to it."""
    p, money = line["act"][0], math.ceil(amount)

    def mine() -> dict[str, Any] | None:
        market = app.get(API_MARKET_URL)
        it = next((it for it in market or [] if str(it.get("id")) == p["market"]), None)
        return None if it is None else (it.get("bid") or {})
    was = mine()
    if was is None:
        return False, "no longer on the market"
    if was.get("money") == money:
        return True, "already bid %d" % money
    status, reply = app.call(*(("PUT", app.url(REBID_URL, market=p["market"], bid=was["id"]))
                               if was.get("id") else ("POST", app.url(BID_URL, market=p["market"]))),
                             {"money": money})
    if status not in OK:
        return False, "the app said %d: %s" % (status, json.dumps(reply)[:200])
    if isinstance(reply, dict) and reply.get("money") == money and reply.get("status") == "pending":
        return True, "bid %d, the app confirmed" % money
    got = app.until(lambda: (mine() or {}).get("money"), money)
    return (True, "bid %d, read back from the market" % money) if got == money else \
        (False, "sent, but the market shows %s" % got)


def withdraw(app: App, line: dict[str, Any], _amount: float | None) -> tuple[bool, str]:
    """Your bid on him taken back."""
    p = line["act"][0]
    why = app.send("DELETE", CANCEL_URL, market=p["market"], bid=p["bid"])
    return (False, why) if why else (True, "withdrawn")


def take(app: App, line: dict[str, Any], amount: float) -> tuple[bool, str]:
    """The game's offer on him accepted, read from the app first: only while it stands at amount."""
    p = line["act"][0]
    offer = next((o for o in app.get(API_OFFER_URL, ptid=p["player_team"]) or []
                  if o.get("status") == "pending" and round(o.get("money") or -1) == round(amount)), None)
    if offer is None:
        return False, "the app shows no offer of %d on him now" % round(amount)
    why = app.send("POST", ACCEPT_URL, {"offerMoney": offer["money"]}, market=p["market"], offer=offer["id"])
    return (False, why) if why else (True, "sold for %d, the app took it" % offer["money"])


def list_all(app: App, line: dict[str, Any], _amount: float | None) -> tuple[bool, str]:
    """Each player of the line put on the market at his value, or at the
    app's now if it has risen since the board (it refuses a price under it:
    Lemar, 2026-10-30). Offers on them are yours to take; a listed player
    still plays for you."""
    listed: list[str] = []
    failed: list[str] = []
    for p in line["act"]:
        now = (app.get(PLAYER_URL, player=p["player"]) or {}).get("playerMaster") or {}
        why = app.send("POST", SELL_URL, {"playerId": int(p["player_team"]),
                                          "salePrice": int(round(max(p["value"] or 0, now.get("marketValue") or 0)))})
        (failed if why else listed).append("%s (%s)" % (p["player"], why) if why else p["player"])
    names = line["label"].removeprefix("list ")
    if failed:
        return False, "listed %d of %s; not %s" % (len(listed), names, "; ".join(failed))
    return True, "listed %s at their value, the app took each" % names


def field(app: App, line: dict[str, Any], _amount: float | None) -> tuple[bool, str]:
    """The line's eleven set as your line-up, and read back: only while every one is still yours."""
    them = line["act"]
    if len(them) != XI_SIZE or any(p["pos"] not in SLOTS for p in them):
        return False, "the board's eleven is %d players, not a line-up" % len(them)
    ours = {str(p.get("playerTeamId")) for p in (app.get(TEAM_URL) or {}).get("players") or []}
    if gone := [p["player"] for p in them if p["player_team"] not in ours]:
        return False, "no longer yours: %s; refresh the board first" % ", ".join(gone)
    slot = {s: [int(p["player_team"]) for p in them if SLOTS[p["pos"]] == s] for s in SLOTS.values()}
    if len(slot["goalkeeper"]) != 1:
        return False, "the board's eleven has %d goalkeepers" % len(slot["goalkeeper"])
    shape = [len(slot[s]) for s in ("defender", "midfield", "striker")]
    if why := app.send("PUT", LINEUP_URL, {**slot, "goalkeeper": slot["goalkeeper"][0], "tactical_formation": shape}):
        return False, why
    want = {str(i) for ids in slot.values() for i in ids}

    def lined() -> set[str]:
        form = (app.get(LINEUP_URL) or {}).get("formation") or {}
        return {str((p or {}).get("playerTeamId")) for s in SLOTS.values() for p in (form.get(s) or [])}
    got = app.until(lined, want)
    return (True, "fielded %s, read back from the app" % "-".join(map(str, shape))) if got == want else \
        (False, "sent, but the app's line-up shows %d of the eleven" % len(got & want))


# What each kind of line does in the app (kind); the amounts it is accepted
# at are the line's own (board_json's accepts).
ACTS: dict[str, Callable[[App, dict[str, Any], Any], tuple[bool, str]]] = {
    "buy": bid, "withdraw": withdraw, "take offer": take, "list": list_all, "field": field}


def kind(line: dict[str, Any]) -> str:
    """What the app is to do for a line: its what, a sale's its step (only an offer to take is done here)."""
    if line.get("what") == "sell":
        return "take offer" if line.get("step") == "take offer" else "sell"
    return line.get("what") or ""


def accept(req: dict[str, Any], board: dict[str, Any], call: Call, base: str,
           nap: Nap = time.sleep) -> dict[str, Any]:
    """What the request accepts, done in the app, or why not."""
    out = {"requested_at": req.get("requested_at"), "label": req.get("label"), "amount": req.get("amount")}

    def done(ok: bool, message: str) -> dict[str, Any]:
        return {**out, "state": "placed" if ok else "failed", "message": message, "at": _now()}

    if req.get("generated_at") != board.get("generated_at"):
        return done(False, "the board has changed since: refresh and accept again")
    line = next((d for d in board.get("do", []) if d.get("label") == req.get("label")), None)
    if line is None or kind(line) not in ACTS or not line.get("act") or not line.get("accepts"):
        return done(False, "not a line of this board the app can do")
    amount = next((a for a in line["accepts"] if (a is None) == (req.get("amount") is None)
                   and (a is None or round(a) == round(req["amount"]))), -1.0)
    if amount == -1.0:
        return done(False, "not at an amount this line is accepted at")
    h = home(call, base)
    if isinstance(h, str):
        return done(False, h[1:])
    return done(*ACTS[kind(line)](App(call, base, h[0], h[1], nap), line, amount))


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
    lemar: dict[str, Any] = {"player_team": None, "player": "3182", "market": "525303444", "bid": None, "pos": "MED", "value": 7.0e6}
    xi: list[dict[str, Any]] = [{"player_team": str(100 + i), "player": "k%d" % i, "market": None, "bid": None, "pos": pos, "value": 1e6}
          for i, pos in enumerate(["POR"] + ["DEF"] * 3 + ["MED"] * 4 + ["DEL"] * 3)]
    board: dict[str, Any] = {"generated_at": "2026-10-30T18:09Z", "do": [
        {"what": "buy", "label": "buy Thomas Lemar", "amount": 7044268.7, "asking": 6356402.0,
         "accepts": [6356402.0, 7044268.7], "act": [lemar]},
        {"what": "withdraw", "label": "withdraw bid on Ivan Romero", "amount": 8094581.0, "accepts": [None],
         "act": [{"player_team": None, "player": "2303", "market": "532765551", "bid": "54329086", "pos": "DEL",
                  "value": 8.0e6}]},
        {"what": "sell", "step": "take offer", "label": "sell Pablo Fornals", "amount": 44133958.0,
         "accepts": [44133958.0],
         "act": [{"player_team": "32308191", "player": "3099", "market": "50121357", "bid": None, "pos": "MED",
                  "value": 40916093.0}]},
        {"what": "sell", "step": "list", "label": "sell Alvaro Mantilla", "amount": None, "accepts": [],
         "act": [{"player_team": "1", "player": "9", "market": None, "bid": None, "pos": "DEF", "value": 1e6}]},
        {"what": "list", "label": "list Bil Nsongo, Manu Bueno", "amount": None, "accepts": [None], "act": [
            {"player_team": "108468054", "player": "2973", "market": None, "bid": None, "pos": "DEL", "value": 9697829.0},
            {"player_team": "108468059", "player": "2687", "market": None, "bid": None, "pos": "MED", "value": 592909.0}]},
        {"what": "field", "label": "field k1", "amount": None, "accepts": [None], "act": xi}]}

    def ask(label: str, amount: float | None = None, **more: Any) -> dict[str, Any]:
        return {"requested_at": "t", "generated_at": board["generated_at"], "label": label, "amount": amount, **more}

    def app(bid: dict[str, Any] | None = None, answer: int = 200, shown_after: int = 0, confirm: bool = False,
            offers: list[dict[str, Any]] | None = None, squad: list[str] | None = None
            ) -> tuple[Call, list[tuple[str, str, Any]]]:
        """A fake app: the league, the market (your bid on Lemar, shown shown_after reads late), offers,
        players' values, your squad and line-up; changes answered with answer."""
        sent: list[tuple[str, str, Any]] = []
        state: dict[str, Any] = {"bid": bid, "hidden": 0, "lineup": {}}

        def call(method: str, url: str, body: dict[str, Any] | None) -> tuple[int, Any]:
            sent.append((method, url, body))
            if "/leagues?" in url:
                return 200, [{"id": 77, "name": "five guys", "team": {"id": 9}}]
            if method == "GET":
                state["hidden"] -= 1
                seen = state["hidden"] < 0
                if "/offer?" in url:
                    return 200, offers or []
                if "/player/" in url:
                    return 200, {"playerMaster": {"marketValue": 600000 if "/player/2687/" in url else None}}
                if "/teams/9?" in url:
                    return 200, {"players": [{"playerTeamId": t} for t in squad or []]}
                if "/lineup?" in url:
                    return 200, {"formation": state["lineup"] if seen else {}}
                return 200, [{"id": 525303444, "playerMaster": {"id": 3182}, "bid": state["bid"] if seen else None},
                             {"id": 1, "playerMaster": {"id": 2}}]
            if answer in OK and body:
                if "money" in body:
                    state["bid"] = {"id": 5, "money": body["money"]}
                state["lineup"] = {s: [{"playerTeamId": str(i)} for i in (v if isinstance(v, list) else [v])]
                                   for s, v in body.items() if s in SLOTS.values()}
                state["hidden"] = shown_after
            return answer, ({"id": "5", "money": body["money"], "status": "pending"}
                            if confirm and body and "money" in body else {})
        return call, sent

    def changes(sent: list[tuple[str, str, Any]]) -> list[tuple[str, str, Any]]:
        return [(m, u, b) for m, u, b in sent if m != "GET"]

    # bids
    call, sent = app({"id": 54045570, "money": 6674686})
    assert accept(ask("buy Thomas Lemar", 7044268.7), board, call, base)["state"] == "placed"
    assert changes(sent) == [("PUT", base + "/v1/competition/1/league/77/market/525303444/bid/54045570?x-lang=es",
                              {"money": 7044269})], ("a bid of yours on him already: raised, whole euros up", sent)
    call, sent = app()
    assert accept(ask("buy Thomas Lemar", 7044268.7), board, call, base)["state"] == "placed" and \
        [m for m, _u, _b in changes(sent)] == ["POST"], "none yet: placed"
    call, sent = app({"id": 5, "money": 7044269})
    assert accept(ask("buy Thomas Lemar", 7044268.7), board, call, base)["message"] == "already bid 7044269" and \
        not changes(sent), "already at that amount: nothing sent"
    naps: list[float] = []
    call, sent = app(shown_after=2)
    assert accept(ask("buy Thomas Lemar", 7044268.7), board, call, base, naps.append)["state"] == "placed" and \
        naps == [READ_WAIT] * 2, "the market shows a new bid a moment late: read again before calling it lost"
    call, sent = app(shown_after=9)
    assert accept(ask("buy Thomas Lemar", 7044268.7), board, call, base, naps.append)["message"].startswith(
        "sent, but"), "and not for ever"
    call, sent = app(shown_after=99, confirm=True)
    assert accept(ask("buy Thomas Lemar", 7044268.7), board, call, base, naps.append)["message"] == \
        "bid 7044269, the app confirmed", "the app's own answer is the bid, pending: the market shows it later"
    call, sent = app()
    assert accept(ask("buy Thomas Lemar", 6356402.0), board, call, base)["state"] == "placed" and \
        [b for _m, _u, b in changes(sent)] == [{"money": 6356402}], "or at his asking price: the stingy bid"
    call, sent = app(answer=400)
    assert accept(ask("buy Thomas Lemar", 7044268.7), board, call, base)["state"] == "failed", "the app refuses"
    # refused before any call
    call, sent = app()
    for bad, why in ((ask("buy Thomas Lemar", 7044268.7, generated_at="older"), "a board since"),
                     (ask("buy Thomas Lemar", 9e6), "another amount"),
                     (ask("buy Thomas Lemar"), "a bid with no amount"),
                     (ask("sell Pablo Fornals", 4e7), "an offer at another amount"),
                     (ask("sell Alvaro Mantilla"), "a sale to list: by the list line, not here"),
                     (ask("list Bil Nsongo, Manu Bueno", 5.0), "a list line takes no amount"),
                     (ask("buy nobody", 1.0), "no such line")):
        assert accept(bad, board, call, base)["state"] == "failed", why
    assert sent == [], "refused before any call to the app"
    # withdraw
    call, sent = app()
    got = accept(ask("withdraw bid on Ivan Romero"), board, call, base)
    assert got["state"] == "placed" and changes(sent) == [
        ("DELETE", base + "/v1/competition/1/league/77/market/532765551/bid/54329086/cancel?x-lang=es", None)], got
    # take
    call, sent = app(offers=[{"id": 197765870, "money": 44133958, "status": "pending"}])
    got = accept(ask("sell Pablo Fornals", 44133958.0), board, call, base)
    assert got["message"] == "sold for 44133958, the app took it" and changes(sent) == [
        ("POST", base + "/v1/competition/1/league/77/market/50121357/offer/197765870/accept?x-lang=es",
         {"offerMoney": 44133958})], ("the offer the app shows standing, by his market line", sent)
    call, sent = app(offers=[{"id": 1, "money": 39000000, "status": "pending"}])
    assert accept(ask("sell Pablo Fornals", 44133958.0), board, call, base)["state"] == "failed" and \
        not changes(sent), "no longer at the board's amount: nothing accepted"
    # list
    call, sent = app()
    got = accept(ask("list Bil Nsongo, Manu Bueno"), board, call, base)
    assert got["state"] == "placed" and changes(sent) == [
        ("POST", base + "/v1/competition/1/league/77/market/sell?x-lang=es", {"playerId": 108468054, "salePrice": 9697829}),
        ("POST", base + "/v1/competition/1/league/77/market/sell?x-lang=es", {"playerId": 108468059, "salePrice": 600000})], \
        ("each at his value, the app's now if it has risen since the board (Lemar, 2026-10-30)", sent)
    # field
    every = [p["player_team"] for p in xi] + ["999"]
    call, sent = app(squad=every)
    got = accept(ask("field k1"), board, call, base)
    assert got["message"] == "fielded 3-4-3, read back from the app" and changes(sent) == [
        ("PUT", base + "/v1/competition/1/teams/9/lineup?x-lang=es",
         {"goalkeeper": 100, "defender": [101, 102, 103], "midfield": [104, 105, 106, 107],
          "striker": [108, 109, 110], "tactical_formation": [3, 4, 3]})], ("slot by position", sent)
    call, sent = app(squad=every[1:])
    got = accept(ask("field k1"), board, call, base)
    assert got["state"] == "failed" and "k0" in got["message"] and not changes(sent), \
        "one sold since the board (Fornals, 2026-10-30): nothing set, refresh first"
    print("act self-test OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    elif sys.argv[1:2] == ["accept"]:
        asked = json.loads(Path(sys.argv[2]).read_text())
        board, call = json.loads(REPORT.read_text()), http_call()
        print(json.dumps([accept(r, board, call, API_BASE) for r in (asked if isinstance(asked, list) else [asked])]))
    else:
        print(__doc__)
        raise SystemExit(2)
