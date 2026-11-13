"""Act on the board: the one thing done from the phone, listing your players for sale at their value.

A request names the line (its label). It is acted on if that line is still on the board as it stands now, whatever board the
phone saw it on, and is one the app can do. A line carries what it acts on as the app knows it (board.py's act: each
player's place in your team, value), so nothing here reads lfg.db. The calls are the app's own
(fantasy-api.llt-services.com, as LaLigaApp makes them).

    python src/act.py accept REQUESTS.json  # what lfg-watch runs: each request's result, as JSON
"""
from __future__ import annotations

import json
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ffcore.auth import API_BASE, TokenStore
from ffcore.laliga_api import API_LEAGUES_URL, parse_api_leagues
from ingest import HEADERS, TIMEOUT

# One call to the app: (method, url, JSON body or None) to (status, JSON reply).
Call = Callable[[str, str, dict[str, Any] | None], tuple[int, Any]]

LEAGUE = "{base}/v1/competition/1/league/{league}"
SELL_URL = LEAGUE + "/market/sell?x-lang=es"
PLAYER_URL = "{base}/v1/competition/1/player/{player}/league/{league}?x-lang=es"
OK = (200, 201, 204)
ALREADY_LISTED = "030.01.52"

REPORT = Path("reports/decisions.json")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class App:
    """The app, as one request acts on it: your league in it, and each call made through call."""

    def __init__(self, call: Call, base: str, league: str) -> None:
        self.call, self.base, self.league = call, base, league

    def url(self, template: str, **ids: Any) -> str:
        return template.format(base=self.base, league=self.league, **ids)

    def get(self, template: str, **ids: Any) -> Any:
        """A read, None unless the app answers it."""
        status, reply = self.call("GET", self.url(template, **ids), None)
        return reply if status == 200 else None

    def send(self, method: str, template: str, body: dict[str, Any] | None = None, **ids: Any) -> str | None:
        """A change, None if the app takes it, else what it said."""
        status, reply = self.call(method, self.url(template, **ids), body)
        return None if status in OK else "the app said %d: %s" % (status, json.dumps(reply)[:200])


def home(call: Call, base: str) -> tuple[str, str] | str:
    """Your league's id in the app and your team's in it, or "!" and why not."""
    status, leagues = call("GET", API_LEAGUES_URL.format(base=base), None)
    got = [(r["league_id"], r["team_id"]) for r in parse_api_leagues(json.dumps(leagues), "")] \
        if status == 200 else []
    if len(got) != 1:
        return "!the app's leagues: %s" % (status if status != 200 else "%d, not one" % len(got))
    return got[0]


def list_all(app: App, line: dict[str, Any]) -> tuple[bool, str]:
    """Each player of the line put on the market at his value, or at the
    app's now if it has risen since the board (it refuses a price under it). Offers on them are yours to take; a listed player
    still plays for you."""
    refused: list[str] = []
    for p in line["act"]:
        now = (app.get(PLAYER_URL, player=p["player"]) or {}).get("playerMaster") or {}
        why = app.send("POST", SELL_URL, {"playerId": int(p["player_team"]),
                                          "salePrice": int(round(max(p["value"] or 0, now.get("marketValue") or 0)))})
        if why and ALREADY_LISTED not in why:
            refused.append(why)
    if refused:
        return False, "listed %d of %d; the app refused %d: %s" % (
            len(line["act"]) - len(refused), len(line["act"]), len(refused), refused[0])
    return True, "listed %s: on the market at their value" % line["label"].removeprefix("list ")


def accept(req: dict[str, Any], board: dict[str, Any], call: Call, base: str) -> dict[str, Any]:
    """What the request accepts, done in the app, or why not."""
    out = {"requested_at": req.get("requested_at"), "label": req.get("label"), "amount": req.get("amount")}

    def done(ok: bool, message: str) -> dict[str, Any]:
        return {**out, "state": "placed" if ok else "failed", "message": message, "at": _now()}

    line = next((d for d in board.get("do") or [] if d.get("label") == req.get("label") and d.get("what") == "list"
                 and d.get("act") and d.get("accepts") == [None]), None)
    if line is None:
        return done(False, "that line is no longer on the board: refresh")
    if req.get("amount") is not None:
        return done(False, "a list line takes no amount")
    h = home(call, base)
    if isinstance(h, str):
        return done(False, h[1:])
    return done(*list_all(App(call, base, h[0]), line))


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
    lines: list[dict[str, Any]] = [
        {"what": "list", "label": "list Bil Nsongo, Manu Bueno", "amount": None, "accepts": [None], "act": [
            {"player_team": "108468054", "player": "2973", "market": None, "bid": None, "pos": "DEL", "value": 9697829.0},
            {"player_team": "108468059", "player": "2687", "market": None, "bid": None, "pos": "MED", "value": 592909.0}]},
        {"what": "sell", "step": "take offer", "label": "sell Pablo Fornals", "amount": 44133958.0, "accepts": [],
         "act": [{"player_team": "32308191", "player": "3099", "market": "50121357", "bid": None, "pos": "MED",
                  "value": 40916093.0}]}]
    board: dict[str, Any] = {"generated_at": "2026-10-30T18:09Z", "do": lines[:1], "sells": lines[1:]}

    def ask(label: str, amount: float | None = None, **more: Any) -> dict[str, Any]:
        return {"requested_at": "t", "generated_at": board["generated_at"], "label": label, "amount": amount, **more}

    def app(answer: int = 200, refusal: Any = None) -> tuple[Call, list[tuple[str, str, Any]]]:
        sent: list[tuple[str, str, Any]] = []

        def call(method: str, url: str, body: dict[str, Any] | None) -> tuple[int, Any]:
            sent.append((method, url, body))
            if "/leagues?" in url:
                return 200, [{"id": 77, "name": "five guys", "team": {"id": 9}}]
            if method == "GET":
                return 200, {"playerMaster": {"marketValue": 600000 if "/player/2687/" in url else None}}
            return answer, refusal or {}
        return call, sent

    def changes(sent: list[tuple[str, str, Any]]) -> list[tuple[str, str, Any]]:
        return [(m, u, b) for m, u, b in sent if m != "GET"]

    call, sent = app()
    got = accept(ask("list Bil Nsongo, Manu Bueno"), board, call, base)
    assert got["state"] == "placed" and changes(sent) == [
        ("POST", base + "/v1/competition/1/league/77/market/sell?x-lang=es", {"playerId": 108468054, "salePrice": 9697829}),
        ("POST", base + "/v1/competition/1/league/77/market/sell?x-lang=es", {"playerId": 108468059, "salePrice": 600000})], \
        ("each at his value, the app's now if it has risen since the board (Lemar, 2026-10-30)", sent)
    call, sent = app(409, {"code": 409, "message": "Player already added to the market", "errorCode": ALREADY_LISTED})
    got = accept(ask("list Bil Nsongo, Manu Bueno"), board, call, base)
    assert got["state"] == "placed" and len(changes(sent)) == 2, \
        ("already on the market is listed, not a failure: every one is still tried", got)
    call, sent = app(400)
    got = accept(ask("list Bil Nsongo, Manu Bueno"), board, call, base)
    assert got["state"] == "failed" and got["message"].startswith("listed 0 of 2; the app refused 2: the app said 400"), \
        ("a real refusal says how many and why once, not each player's", got)
    call, sent = app()
    for bad, why in ((ask("list Bil Nsongo, Manu Bueno", 5.0), "a list line takes no amount"),
                     (ask("sell Pablo Fornals", 44133958.0), "an offer is not taken from the phone"),
                     (ask("list nobody"), "no such line")):
        assert accept(bad, board, call, base)["state"] == "failed", why
    assert sent == [], "refused before any call to the app"
    got = accept(ask("list nobody"), board, call, base)
    assert got["message"] == "that line is no longer on the board: refresh", got
    call, sent = app()
    got = accept(ask("list Bil Nsongo, Manu Bueno", generated_at="an older board"), board, call, base)
    assert got["state"] == "placed" and len(changes(sent)) == 2, \
        ("a tap made on an older board is done while its line is still on the board as it stands", got)
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
