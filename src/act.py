"""Act on the board: the one thing done from the phone, listing your players for sale at their value.

A request names the line (its label). It is acted on if that line is still on the board as it stands now, whatever board the
phone saw it on, and is one the app can do. A line carries what it acts on as the app knows it (board.py's act: each
player's place in your team, value), so nothing here reads the store. The calls are the app's own
(fantasy-api.llt-services.com, as LaLigaApp makes them).

    python src/act.py accept REQUESTS.json  # what lfg-watch runs: each request's result, as JSON
    python src/act.py ensure                # what lfg-list runs: the squad's unlisted players listed, one JSON line
    python src/act.py raise [--dry]         # what lfg-raise runs: DRY ONLY, what raising the placed bids to the board's `to` would do

The raiser cannot send a bid in this build: raise_bid, the one place the app's bid call goes, is not wired, and mode "live" is refused.
"""
from __future__ import annotations

import json
import math
import os
import sys
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Any, NamedTuple

from ffcore import records as rec
from ffcore.auth import API_BASE, TokenStore
from ffcore.decision_facts import TEAM_SELLER
from ffcore.laliga_api import API_LEAGUES_URL, API_MARKET_URL, API_TEAMS_URL, parse_api_leagues, parse_api_market, parse_api_teams
import ingest
from ingest import HEADERS, TIMEOUT

# One call to the app: (method, url, JSON body or None) to (status, JSON reply).
Call = Callable[[str, str, dict[str, Any] | None], tuple[int, Any]]

LEAGUE = "{base}/v1/competition/1/league/{league}"
SELL_URL = LEAGUE + "/market/sell?x-lang=es"
PLAYER_URL = "{base}/v1/competition/1/player/{player}/league/{league}?x-lang=es"
OK = (200, 201, 204)
ALREADY_LISTED = "030.01.52"

REPORT = Path(os.environ.get("LFG_REPORTS", "reports")) / "decisions.json"

# The raiser (dry only). State lives beside the other units' (lfg-list's list.log, run.lock).
STATE = Path(os.environ.get("LFG_STATE", str(Path.home() / ".local/state/lfg")))
MADRID = ZoneInfo("Europe/Madrid")
WINDOW = (timedelta(hours=22), timedelta(hours=22, minutes=24) - timedelta(seconds=90))   # Madrid time of day: lock at the close, never after
SECOND_PASS = timedelta(hours=22, minutes=12)       # the 22:06 pass is 1, the 22:20 pass 2
BOARD_MAX_AGE = timedelta(minutes=20)
CLOSE_MARGIN = timedelta(seconds=90)
PER_NIGHT = 2                                       # raises of one auction in one evening


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


def raise_bid(app: App, market_id: str, bid_id: str, target: float) -> str | None:
    """THE one place the app's bid call goes (URL, method, body and reply are not known: the design's step 0 is a supervised capture
    of the app's own call). Not wired: nothing in this build can send a bid, and raise_bids never reaches it."""
    raise NotImplementedError("the app's bid call is not captured: raising is dry-run only")


def raise_mode(state: Path) -> str:
    """off, dry or live from state/RAISE_MODE; a missing or garbled file is off."""
    try:
        word = (state / "RAISE_MODE").read_text(encoding="utf-8").strip().lower()
    except OSError:
        return "off"
    return word if word in ("dry", "live") else "off"


def latest_look() -> str | None:
    """The stamp (2026-11-27T2135Z) of the newest raw snapshot, the last look at the market, or None."""
    return ingest.latest_stamp()


def _stamp(text: str | None) -> str:
    """A board time (2026-11-27T19:52Z) or snapshot stamp (2026-11-27T1952Z) in the stamp form; "" if None."""
    return (text or "").replace(":", "")


def _utc(text: str) -> datetime:
    """A time as UTC: ISO (2026-11-27T19:52Z, with or without seconds or an offset)."""
    t = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


class RaisePass(NamedTuple):
    """What one pass did: the mode it ran in, why it did nothing at all (None when it ran), and a row for each line it weighed."""
    mode: str
    stopped: str | None
    rows: list[rec.PlaceDry]


def _done(path: Path) -> list[list[Any]]:
    try:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except OSError:
        return []


def _target(b: dict[str, Any], amount: float, room: float) -> float:
    """What a raise would be: the advised `to`, held to the walk-away and to the bid limit's room, floored to the euro. A belt: `to`
    is already cleared against both by the board."""
    return float(math.floor(min(x for x in (float(b["to"]), b.get("walk_away"), room + amount) if x is not None)))


def raise_bids(app: App | None, board: dict[str, Any], now: datetime, state: Path, mode: str, look: str | None = None,
               market: list[dict[str, Any]] | None = None) -> RaisePass:
    """Weigh each buy line of the board whose move is raise, in the design's order (~/.cache/place-design/DESIGN.md), and write what
    raising it would be. DRY ONLY: raise_bid is never called, mode live is refused, and the app is read (GET the market, as
    ensure_listed does), never changed. The market may be given (a parsed snapshot) instead of read with app.
    The board decides: the line's `to` and `amount`, its `walk_away`, its funding.lock_margin; nothing here re-derives a raise.
    A stale board (over 20 min old, or made before the last look) is still weighed in dry mode, each row marked stale with the board's age
    (what the owner saw and placed from it is the gap measured); live mode, which this build refuses, would skip it. A pass that stops (kill switch, mode, clock) or finds no raise line writes nothing. A line weighed in an earlier pass
    of the same look is not written again (state/raise.dry.done.jsonl, [market id, evening, pass, raised], appended before the rows are returned), so a repeat is a no-op."""
    if (state / "NO_RAISE").exists():
        return RaisePass(mode, "kill switch: NO_RAISE", [])
    if mode == "live":
        return RaisePass(mode, "live is refused: the app's bid call is not wired in this build", [])
    if mode != "dry":
        return RaisePass(mode, "mode is off", [])
    local = now.astimezone(MADRID)
    since_midnight = timedelta(hours=local.hour, minutes=local.minute, seconds=local.second)
    if not WINDOW[0] <= since_midnight < WINDOW[1]:
        return RaisePass(mode, "outside the window (Madrid %s)" % local.strftime("%H:%M:%S"), [])
    try:
        made, read = _utc(board["generated_at"]), board.get("market_at")
    except (KeyError, ValueError):
        return RaisePass(mode, "the board has no readable time", [])
    age = (now - made).total_seconds() / 60
    stale = now - made > BOARD_MAX_AGE or bool(look and _stamp(read) < _stamp(look))
    lines = sorted((b for b in board.get("buys") or [] if b.get("move") == "raise" and b.get("to") is not None),
                   key=lambda b: (-(b.get("net") or 0.0), str(b.get("key"))))
    if not lines:
        return RaisePass(mode, None, [])
    if market is None and app is not None:
        got = app.get(API_MARKET_URL)
        market = got if isinstance(got, list) else None
    items = None if market is None else {r["player_id"]: r for r in parse_api_market(json.dumps(market), "") if r["bid_id"]}
    date, number = local.strftime("%Y-%m-%d"), 2 if since_midnight >= SECOND_PASS else 1
    done_path = state / "raise.dry.done.jsonl"
    past = _done(done_path)
    room, spent, cap, rows, fresh = float(board.get("can_bid") or 0.0), 0.0, float("inf"), [], []
    for b in lines:
        amount, item = float(b.get("amount") or 0.0), None if items is None else items.get(str(b.get("key")))
        mid = item["market_id"] if item else None
        key = [mid or str(b.get("key")), date, number]
        if key in [k[:3] for k in past]:
            continue                                    # this look's line, written before
        target, skip = None, None
        if items is None:
            skip = "market not readable"
        elif item is None:
            skip = "no bid of yours on the market for him"
        elif not float(b["to"]) > amount:
            skip = "the advised bid is not above the placed one"
        elif not item["expires_at"] or _utc(item["expires_at"]) - now <= CLOSE_MARGIN:
            skip = "closes within 90 s (or no close time)"
        elif abs(float(item["bid_money"] or 0) - amount) > 0.5:
            skip = "owner changed it: the app holds %s, the board %s" % (item["bid_money"], amount)
        elif sum(1 for k in past if k[:2] == key[:2] and k[3]) >= PER_NIGHT:
            skip = "already raised %d times this evening" % PER_NIGHT
        elif (target := _target(b, amount, room)) <= amount:
            skip = "no room above the bid (walk-away or limit)"
        elif (margin := (b.get("funding") or {}).get("lock_margin")) is None or spent + target - amount > min(cap, margin):
            skip = "cash at the lock: +%.0f in all would pass the margin %s" % (spent + target - amount, margin if margin is None else min(cap, margin))
        elif spent + target - amount > room:
            skip = "over the bid limit"
        else:
            spent += target - amount
            cap = min(cap, margin)
        rows.append(rec.PlaceDry(board["generated_at"], now.strftime("%Y-%m-%dT%H:%M:%SZ"), date, number, b["name"], mid, amount,
                                 float(b["to"]), target, None if target is None else target - amount, skip is None, skip, stale, round(age, 1)))
        fresh.append([*key, skip is None])
    if fresh:
        done_path.parent.mkdir(parents=True, exist_ok=True)
        with open(done_path, "a", encoding="utf-8") as f:
            f.writelines(json.dumps(k) + "\n" for k in fresh)
    return RaisePass(mode, None, rows)


def append_place_dry(path: Path, rows: list[rec.PlaceDry]) -> None:
    """The rows appended to the file (a header first when it is new), in the run records' line format."""
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fresh = not path.exists()
    with open(path, "a", encoding="utf-8") as f:
        if fresh:
            f.write(rec.header("place_dry"))
        f.writelines(rec.encode(r) for r in rows)


def read_place_dry(path: Path) -> list[rec.PlaceDry]:
    """Every row of the file (empty if absent), through the versioned reader."""
    if not path.exists():
        return []
    lines = path.read_bytes().splitlines(keepends=True)
    version = rec.parse_header(lines[0], "place_dry")
    return [rec.decode("place_dry", version, line) for line in lines[1:]]


def raise_run(board: dict[str, Any], dry_flag: bool, make_call: Callable[[], Call] | None = None, now: datetime | None = None,
              state: Path = STATE) -> dict[str, Any]:
    """One pass of the raiser as lfg-raise runs it, the mode from state/RAISE_MODE (--dry holds live down to dry), rows appended to
    state/place_dry.jsonl. The app (make_call() builds the login) is read, for home and the market, only when the mode is dry. Returns a JSON-able summary."""
    mode = raise_mode(state)
    if dry_flag and mode == "live":
        mode = "dry"
    now = now or datetime.now(timezone.utc)
    app = None
    if mode == "dry" and make_call is not None:
        call = make_call()
        h = home(call, API_BASE)
        if isinstance(h, str):
            return {"mode": mode, "stopped": h[1:], "rows": []}
        app = App(call, API_BASE, h[0])
    got = raise_bids(app, board, now, state, mode, latest_look())
    append_place_dry(state / "place_dry.jsonl", got.rows)
    return {"mode": got.mode, "stopped": got.stopped, "rows": [r._asdict() for r in got.rows]}


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


def _selftest_raise() -> None:
    import random
    import tempfile
    from unittest import mock

    now = datetime(2026, 11, 27, 21, 6, 0, tzinfo=timezone.utc)       # 22:06 Madrid (winter, UTC+1)
    close = "2026-11-27T21:24:00Z"
    after = now + timedelta(minutes=14)                               # the 22:20 pass

    def buy(key: str, amount: float, to: float, net: float = 1.0, margin: float | None = 1e9, walk: float | None = 1e9,
            move: str = "raise") -> dict[str, Any]:
        return {"name": "P" + key, "key": key, "amount": amount, "to": to, "net": net, "walk_away": walk, "move": move,
                "funding": {"lock_margin": margin}}

    def board(*buys: dict[str, Any], can_bid: float = 1e9) -> dict[str, Any]:
        return {"generated_at": "2026-11-27T21:00Z", "market_at": "2026-11-27T21:04Z", "can_bid": can_bid, "buys": list(buys)}

    def item(key: str, money: float | None, ends: str = close) -> dict[str, Any]:
        return {"id": "m" + key, "discr": "marketPlayerLeague", "salePrice": 1, "expirationDate": ends, "playerMaster": {"id": key},
                "bid": None if money is None else {"id": "b" + key, "money": money, "status": "pending"}}

    def run(b: dict[str, Any], market: list[dict[str, Any]], state: Path, mode: str = "dry", at: datetime = now,
            look: str | None = "2026-11-27T2104Z") -> RaisePass:
        with mock.patch.object(sys.modules[__name__], "raise_bid", side_effect=AssertionError("a bid was sent")) as sent:
            got = raise_bids(None, b, at, state, mode, look, market)
            assert not sent.called, "raise_bid is never called, in any mode"
        return got

    def one(b: dict[str, Any], money: float | None, can_bid: float = 1e9, ends: str = close) -> rec.PlaceDry:
        with tempfile.TemporaryDirectory() as t:
            (got,) = run(board(b, can_bid=can_bid), [item(b["key"], money, ends)], Path(t)).rows
            return got

    market = [item("1", 10e6), item("2", 20e6), item("3", 5e6)]
    two = board(buy("1", 10e6, 12e6, net=3.0), buy("2", 20e6, 21e6, net=2.0), buy("3", 5e6, 5e6 + 100.5, net=1.0))
    with tempfile.TemporaryDirectory() as t:
        d = Path(t)
        got = run(two, market, d)
        assert got.stopped is None and [(r.player, r.target, r.delta, r.would_raise, r.pass_no) for r in got.rows] == [
            ("P1", 12e6, 2e6, True, 1), ("P2", 21e6, 1e6, True, 1), ("P3", 5e6 + 100, 100.0, True, 1)], got
        assert run(two, market, d).rows == [], "a repeat of the same look is a no-op"
        assert [r.pass_no for r in run(two, market, d, at=after).rows] == [2, 2, 2], "the second look is its own"
        assert run(two, market, d, at=after).rows == [], "and a repeat of it too"
        # one raise per look, and at most PER_NIGHT raises of an auction in an evening (a third look is refused)
        (d / "raise.dry.done.jsonl").write_text(json.dumps(["m1", "2026-11-27", 7, True]) + "\n" + json.dumps(["m1", "2026-11-27", 8, True]) + "\n")
        got = run(board(buy("1", 10e6, 12e6)), [item("1", 10e6)], d)
        assert [(r.would_raise, r.skip) for r in got.rows] == [(False, "already raised 2 times this evening")]
        (d / "raise.dry.done.jsonl").write_text(json.dumps(["m1", "2026-11-27", 7, False]) + "\n" + json.dumps(["m1", "2026-11-27", 8, True]) + "\n")
        assert run(board(buy("1", 10e6, 12e6)), [item("1", 10e6)], d).rows[0].would_raise, "a skipped look does not count as a raise"

    # mode file, kill switch, live refused
    with tempfile.TemporaryDirectory() as t:
        d = Path(t)
        assert raise_mode(d) == "off", "the default is off"
        for word, want in (("dry\n", "dry"), ("live", "live"), ("off", "off"), ("", "off"), ("garbage", "off"), ("DRY", "dry")):
            (d / "RAISE_MODE").write_text(word)
            assert raise_mode(d) == want, word
        for mode in ("off", "garbage"):
            got = run(two, market, d, mode=mode)
            assert got.rows == [] and got.stopped, "off does nothing"
        got = run(two, market, d, mode="live")
        assert got.rows == [] and "refused" in (got.stopped or ""), "live is refused in this build"
        (d / "RAISE_MODE").write_text("live")
        with mock.patch.object(sys.modules[__name__], "latest_look", return_value=None):
            for flag in (False, True):
                r = raise_run(two, flag, lambda: (_ for _ in ()).throw(AssertionError("no login for live")), now, d) if not flag else \
                    raise_run(two, flag, None, now, d)
                assert (r["mode"] == "live") == (not flag) and (flag or "refused" in r["stopped"]), r
        (d / "NO_RAISE").touch()
        got = run(two, market, d)
        assert got.rows == [] and "NO_RAISE" in (got.stopped or ""), "the kill switch beats dry"
    try:
        raise_bid(App(lambda m, u, b: (500, {}), "x", "1"), "m", "b", 1.0)
    except NotImplementedError:
        pass
    else:
        raise AssertionError("raise_bid is not wired")

    # clock window, board age, the last look
    with tempfile.TemporaryDirectory() as t:
        d = Path(t)
        for at in (datetime(2026, 11, 27, 20, 59, 59, tzinfo=timezone.utc), datetime(2026, 11, 27, 21, 22, 30, tzinfo=timezone.utc),
                   datetime(2026, 11, 27, 22, 0, 0, tzinfo=timezone.utc)):
            got = run(two, market, d, at=at)
            assert got.rows == [] and "window" in (got.stopped or ""), at
        assert run(two, market, d, at=datetime(2026, 11, 27, 21, 22, 29, tzinfo=timezone.utc)).rows, "just inside the window"
        old = {**two, "generated_at": "2026-11-27T20:40Z"}
        got = run(old, market, d)
        assert got.stopped is None and [r.would_raise for r in got.rows] == [True] * 3 and all(r.stale and r.age_min == 26.0 for r in got.rows), \
            "dry weighs a board over 20 min old, each row stale with its age"
        got = run(two, market, Path(tempfile.mkdtemp()))
        assert got.rows and not any(r.stale for r in got.rows) and all(r.age_min == 6.0 for r in got.rows), "a fresh board: stale false"
        got = run(two, market, Path(tempfile.mkdtemp()), look="2026-11-27T2105Z")
        assert got.rows and all(r.stale for r in got.rows), "a board whose market is older than the last look is weighed, stale"
        got = run(old, market, d, mode="live")
        assert got.rows == [] and "refused" in (got.stopped or ""), "live is refused, stale or not"
        assert run({**two, "market_at": None}, market, Path(tempfile.mkdtemp())).rows[0].stale, "no market time: stale"
        assert run({}, market, d).stopped, "no board"

    # no raise line: nothing written, no file
    with tempfile.TemporaryDirectory() as t:
        d = Path(t)
        (d / "RAISE_MODE").write_text("dry")
        for none in (board(buy("1", 10e6, 12e6, move="bid"), buy("2", 20e6, 21e6, move="hold")), board()):
            with mock.patch.object(sys.modules[__name__], "latest_look", return_value=None):
                r = raise_run(none, False, None, now, d)
            assert r["rows"] == [] and sorted(p.name for p in d.iterdir()) == ["RAISE_MODE"], "nothing written"

    # raise only: never lower, withdraw or open; never above to, walk-away or the room
    assert not one(buy("1", 10e6, 9e6), 10e6).would_raise, "to below the bid: no lowering"
    assert not one(buy("1", 10e6, 10e6), 10e6).would_raise, "to equal: nothing"
    seen = one(buy("1", 10e6, 12e6), None)
    assert not seen.would_raise and seen.target is None and seen.market_id is None, "no bid held: no new bid"
    assert not one(buy("1", 10e6, 12e6), 11e6).would_raise, "the owner changed it"
    assert not one(buy("1", 10e6, 12e6), 10e6, ends="2026-11-27T21:07:00Z").would_raise, "closing within 90 s"
    for to, walk, can_bid in ((12e6, 11e6, 1e9), (12e6, 1e9, 0.5e6), (12e6, None, 1e9), (12.7e6, 1e9, 1e9), (12e6, 1e9, 1e9)):
        seen = one(buy("1", 10e6, to, walk=walk), 10e6, can_bid)
        assert seen.target is not None and 10e6 < seen.target <= min(to, walk or to, can_bid + 10e6) and seen.target == int(seen.target), seen
        assert seen.would_raise and seen.delta == seen.target - 10e6
    assert one(buy("1", 10e6, 12e6, walk=10e6), 10e6).skip == "no room above the bid (walk-away or limit)"
    assert one(buy("1", 10e6, 12e6), 10e6, 0.0).skip == "no room above the bid (walk-away or limit)"

    # cash at the lock: the cumulative delta stays within each raised line's margin and the bid limit, whatever the order
    rng = random.Random(5)
    for _ in range(40):
        buys = [buy(str(i), 10e6, 10e6 + rng.randint(1, 9) * 1e5, net=rng.random(), margin=rng.choice([0.2e6, 0.5e6, 0.9e6, 2e6]))
                for i in range(1, 6)]
        rng.shuffle(buys)
        with tempfile.TemporaryDirectory() as t:
            rows_ = run(board(*buys, can_bid=1.5e6), [item(str(i), 10e6) for i in range(1, 6)], Path(t)).rows
        margins, total, lowest = {b["name"]: b["funding"]["lock_margin"] for b in buys}, 0.0, float("inf")
        assert len(rows_) == 5 and all(w.skip for w in rows_ if not w.would_raise)
        for w in rows_:
            if w.would_raise:
                assert w.delta is not None
                total += w.delta
                lowest = min(lowest, margins[w.player])
                assert total <= lowest + 1e-6 and total <= 1.5e6, "cumulative delta within the smallest margin so far and the limit"
        order = sorted(buys, key=lambda b: (-b["net"], b["key"]))
        assert [r.player for r in rows_] == [b["name"] for b in order], "the best net first"
    # a later line with a roomy margin does not lift the cap an earlier tighter one set, in either order
    for first, second in ((("1", 0.4e6, 0.5e6), ("2", 0.2e6, 9e6)), (("2", 0.2e6, 9e6), ("1", 0.4e6, 0.5e6))):
        pair = [buy(k, 10e6, 10e6 + d, net=5.0 - i, margin=m) for i, (k, d, m) in enumerate((first, second))]
        with tempfile.TemporaryDirectory() as t:
            rows_ = run(board(*pair), [item("1", 10e6), item("2", 10e6)], Path(t)).rows
        assert sum(r.delta or 0 for r in rows_ if r.would_raise) <= 0.5e6 + 1e-6 and len(rows_) == 2, rows_
    seen = one(buy("1", 10e6, 12e6, margin=1e6), 10e6)
    assert not seen.would_raise and "cash at the lock" in (seen.skip or "")
    assert not one(buy("1", 10e6, 12e6, margin=None), 10e6).would_raise, "no margin stated: not raised"

    # records: appended in the run records' format, read back through the versioned reader
    with tempfile.TemporaryDirectory() as t:
        d = Path(t)
        rows = [rec.PlaceDry("c", "a", "2026-11-27", 1, "Ñ", "m", 1.5, 2.5, 2.0, 0.5, True, None),
                rec.PlaceDry("c", "a", "2026-11-27", 2, "B", None, 1.0, 2.0, None, None, False, "no bid")]
        append_place_dry(d / "p.jsonl", rows[:1])
        append_place_dry(d / "p.jsonl", rows[1:])
        append_place_dry(d / "p.jsonl", [])
        assert read_place_dry(d / "p.jsonl") == rows and read_place_dry(d / "none") == []
        assert (d / "p.jsonl").read_text().splitlines()[0] == '{"format": "place_dry", "v": 2}'
        v1 = {k: v for k, v in rows[0]._asdict().items() if k not in ("stale", "age_min")}
        (d / "v1.jsonl").write_text('{"format": "place_dry", "v": 1}\n' + json.dumps(v1) + "\n")
        assert read_place_dry(d / "v1.jsonl") == rows[:1], "a version 1 file reads, stale false and no age"
        (d / "q.jsonl").write_text('{"format": "place_dry", "v": 9}\n')
        try:
            read_place_dry(d / "q.jsonl")
        except rec.StoreError:
            pass
        else:
            raise AssertionError("an unknown version is an error")
        (d / "RAISE_MODE").write_text("dry")
        with mock.patch.object(sys.modules[__name__], "latest_look", return_value=None):
            r = raise_run(two, False, None, now, d)
        assert r["stopped"] is None and [x["skip"] for x in r["rows"]] == ["market not readable"] * 3, "an unreadable market raises nothing"
        assert read_place_dry(d / "place_dry.jsonl") == [rec.PlaceDry(**x) for x in r["rows"]]

        # through the app: the login is made only for dry, the market is a GET, nothing else is asked
        calls: list[tuple[str, str]] = []

        def call(method: str, url: str, body: dict[str, Any] | None) -> tuple[int, Any]:
            calls.append((method, url.split("/api")[-1]))
            return (200, [{"id": 77, "team": {"id": 9}}]) if "/leagues?" in url else (200, market)
        (d / "raise.dry.done.jsonl").unlink()
        with mock.patch.object(sys.modules[__name__], "latest_look", return_value=None):
            r = raise_run(two, False, lambda: call, now, d)
        assert [x["would_raise"] for x in r["rows"]] == [True] * 3 and {m for m, _ in calls} == {"GET"}, (r, calls)


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
    _selftest_raise()
    print("act self-test OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    elif sys.argv[1:2] == ["accept"]:
        asked = json.loads(Path(sys.argv[2]).read_text())
        board, call = json.loads(REPORT.read_text()), http_call()
        print(json.dumps([accept(r, board, call, API_BASE) for r in (asked if isinstance(asked, list) else [asked])]))
    elif sys.argv[1:2] == ["raise"]:
        board = json.loads(REPORT.read_text())
        print(json.dumps(raise_run(board, "--dry" in sys.argv, http_call)))
    elif sys.argv[1:2] == ["ensure"]:
        done = ensure(http_call(), API_BASE)
        print(json.dumps({"listed": done.listed, "refused": [{"name": n, "why": w} for n, w in done.refused],
                          "already": done.already}))
    else:
        print(__doc__)
        raise SystemExit(2)
