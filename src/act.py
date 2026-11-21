"""Act on the board: the one thing done from the phone, listing your players for sale at their value.

A request names the line (its label). It is acted on if that line is still on the board as it stands now, whatever board the
phone saw it on, and is one the app can do. A line carries what it acts on as the app knows it (board.py's act: each
player's place in your team, value), so nothing here reads the store. The calls are the app's own
(fantasy-api.llt-services.com, as LaLigaApp makes them).

    python src/act.py accept REQUESTS.json  # what lfg-watch runs: each request's result, as JSON
    python src/act.py ensure                # what lfg-list runs: the squad's unlisted players listed, one JSON line
"""
from __future__ import annotations

import json
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NamedTuple

from ffcore.auth import API_BASE, TokenStore
from ffcore.decision_facts import TEAM_SELLER
from ffcore.laliga_api import API_LEAGUES_URL, API_MARKET_URL, API_TEAMS_URL, parse_api_leagues, parse_api_market, parse_api_teams
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


class Ensured(NamedTuple):
    """What ensure_listed did: the names listed, the (name, reason) pairs the app refused, how many were already listed."""
    listed: list[str]
    refused: list[tuple[str, str]]
    already: int


def ensure_listed(app: App, team: str) -> Ensured:
    """Every player of team's squad, as the app reads it now, that has no listing of the team in the league market and no
    bid placed on him is put on the market (list_all, one line each, so a refusal names its player and does not stop the
    others). A player without a playerTeam id (not yet in the squad) is left alone, and one the app says is already listed
    counts as listed. A second run lists nothing. Never raises: an unreadable app or an error is a refusal of "the app"."""
    try:
        teams, market = app.get(API_TEAMS_URL), app.get(API_MARKET_URL)
        if not isinstance(teams, list) or not isinstance(market, list):
            return Ensured([], [("the app", "squad or market not readable")], 0)
        rows = parse_api_market(json.dumps(market), "")
        listed = {r["player_id"] for r in rows if r["seller"] == TEAM_SELLER}
        bid_on = {r["player_id"] for r in rows if r["bid_id"]}
        squad = [r for r in parse_api_teams(json.dumps(teams), "")
                 if r["table"] == "api_teams" and r["team_id"] == team and r["player_team_id"]]
        todo = [r for r in squad if r["player_id"] not in listed | bid_on]
        out = Ensured([], [], len(squad) - len(todo))
        for r in todo:
            ok, message = list_all(app, {"label": "list " + r["player_name"], "act": [
                {"player_team": r["player_team_id"], "player": r["player_id"], "value": float(r["market_value"] or 0)}]})
            if ok:
                out.listed.append(r["player_name"])
            else:
                out.refused.append((r["player_name"], message))
        return out
    except Exception as e:
        return Ensured([], [("the app", "%s: %s" % (type(e).__name__, e))], 0)


def ensure(call: Call, base: str) -> Ensured:
    """ensure_listed for the one team and league the login holds. Never raises."""
    try:
        h = home(call, base)
    except Exception as e:
        h = "!%s: %s" % (type(e).__name__, e)
    if isinstance(h, str):
        return Ensured([], [("the app", h[1:])], 0)
    return ensure_listed(App(call, base, h[0]), h[1])


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


def _selftest_ensure() -> None:
    base, me = "https://api", "9"
    squad = {"1": ("Ana", 5e6), "2": ("Beto", 2e6), "3": ("Cai", 3e6), "4": ("Dan", 4e6)}

    def world(listed: set[str], bids: frozenset[str] = frozenset(), refuse: frozenset[str] = frozenset(),
              already: frozenset[str] = frozenset(), now: float = 0.0) -> tuple[Call, list[dict[str, Any]]]:
        market = [{"id": "m" + p, "discr": TEAM_SELLER, "playerMaster": {"id": p}} for p in listed | {"6"}]
        market += [{"id": "b" + p, "discr": "marketPlayerLeague", "bid": {"id": "x"}, "playerMaster": {"id": p}} for p in bids]
        sent: list[dict[str, Any]] = []
        mine = [{"playerTeamId": str(100 + int(p)), "playerMaster": {"id": p, "nickname": n, "marketValue": v}}
                for p, (n, v) in squad.items()] + [{"playerMaster": {"id": "5", "nickname": "Eli", "marketValue": 1e6}}]
        teams = [{"id": me, "players": mine},
                 {"id": "8", "players": [{"playerTeamId": "106", "playerMaster": {"id": "6", "nickname": "Rival"}}]}]

        def call(method: str, url: str, body: dict[str, Any] | None) -> tuple[int, Any]:
            if "/leagues?" in url:
                return 200, [{"id": 77, "team": {"id": me}}]
            if url.endswith("/teams?x-lang=es"):
                return 200, teams
            if url.endswith("/market?x-lang=es"):
                return 200, market
            if method == "GET":
                return 200, {"playerMaster": {"marketValue": now}}
            assert body is not None
            who = str(body["playerId"] - 100)
            sent.append(body)
            if who in already:
                return 409, {"errorCode": ALREADY_LISTED}
            if who in refuse:
                return 400, {"message": "no"}
            market.append({"id": "m" + who, "discr": TEAM_SELLER, "playerMaster": {"id": who}})
            return 200, {}
        return call, sent

    call, sent = world({"1"}, bids=frozenset({"2"}))
    got = ensure(call, base)
    assert sorted(got.listed) == ["Cai", "Dan"] and not got.refused and got.already == 2, got
    assert sorted(b["playerId"] for b in sent) == [103, 104], "exactly the unlisted, not the bid-pending, not the one not yet in the squad"
    again = ensure(call, base)
    assert not again.listed and not again.refused and again.already == 4 and len(sent) == 2, ("idempotent", again)
    call, sent = world(set(), refuse=frozenset({"2"}), already=frozenset({"3"}))
    got = ensure(call, base)
    assert sorted(got.listed) == ["Ana", "Cai", "Dan"] and [n for n, _ in got.refused] == ["Beto"] and len(sent) == 4, \
        ("already listed counts as listed; a refusal is reported and the others go on", got)
    assert "the app said 400" in got.refused[0][1]
    call, sent = world(set(squad))
    assert ensure(call, base) == Ensured([], [], 4) and sent == [], "all listed: no change"
    for now, want in ((9e6, [9000000] * 4), (1.0, [2000000, 3000000, 4000000, 5000000])):
        call, sent = world(set(), now=now)
        ensure(call, base)
        assert sorted(b["salePrice"] for b in sent) == want, ("the price is the greater of the value and the app's now", sent)

    def down(method: str, url: str, body: dict[str, Any] | None) -> tuple[int, Any]:
        raise OSError("down")
    deads: list[Call] = [down, lambda m, u, b: (500, {})]
    for dead in deads:
        got = ensure(dead, base)
        assert not got.listed and got.refused and got.refused[0][0] == "the app", "never raises"
    got = ensure_listed(App(lambda m, u, b: (200, {}), base, "77"), me)
    assert not got.listed and got.refused, "an unreadable squad lists nothing"


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
    _selftest_ensure()
    print("act self-test OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    elif sys.argv[1:2] == ["accept"]:
        asked = json.loads(Path(sys.argv[2]).read_text())
        board, call = json.loads(REPORT.read_text()), http_call()
        print(json.dumps([accept(r, board, call, API_BASE) for r in (asked if isinstance(asked, list) else [asked])]))
    elif sys.argv[1:2] == ["ensure"]:
        done = ensure(http_call(), API_BASE)
        print(json.dumps({"listed": done.listed, "refused": [{"name": n, "why": w} for n, w in done.refused],
                          "already": done.already}))
    else:
        print(__doc__)
        raise SystemExit(2)
