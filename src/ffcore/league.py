from __future__ import annotations

import configparser
from collections.abc import Callable
import sys
from dataclasses import dataclass
from typing import NamedTuple

from ffcore.parse import money, text
from ffcore.players import load_crosswalk
from ffcore.tidy import current, history, input_path

from ffcore.crosswalk import Crosswalk
from ffcore.names import AppId, Name, PlayerKey, app_id
__all__ = ["Config", "Entry", "load_config", "app_fielded", "estimate_cash", "ledger",
           "League", "price_paid"]


@dataclass
class Config:
    me: str = "miguel_autentico"
    budget: float = 100e6


def load_config(name: str = "league.ini") -> Config:
    cp = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
    cp.optionxform = str  # type: ignore[assignment,method-assign]  # keep keys' case: configparser's own idiom
    path = input_path(name)
    if path.exists():
        cp.read(path, encoding="utf-8")
    base = Config()
    return Config(
        me=cp.get("league", "me", fallback=base.me),
        budget=money(cp.get("league", "budget", fallback=str(base.budget))) or 0.0)


def app_fielded(squad, names: dict[str, Name], rows=None, xw=None) -> list[str]:
    """Your eleven as the app has it, as our keys: by app id, else by name;
    [] if any of them is not in your squad."""
    rows = current("api_lineup") if rows is None else rows
    xw = xw or load_crosswalk() or Crosswalk()
    squad = set(squad)
    by_name = {names.get(k) or Name(k): k for k in squad}
    out = []
    for r in rows or []:
        key = xw.player(app_id=app_id(r))
        if key is None:
            for field in ("player_name", "player_name_full"):
                key = by_name.get(Name(r.get(field)))
                if key:
                    break
        if not key or key not in squad:
            return []
        out.append(key)
    return out


class Entry(NamedTuple):
    """One event of the activity feed: who (the manager it is about) and
    other (the one paid, in a transfer), the player keyed like every other
    table, and the money."""
    at: str
    kind: str
    who: str | None
    other: str | None
    key: str | None
    amount: float

    @property
    def buyer(self) -> str | None:
        """Who paid amount for the player: a buy's or a clause's who."""
        return self.who if self.kind in ("buy", "transfer") else None

    @property
    def seller(self) -> str | None:
        """Who parted with the player and was paid amount: a sale's who, a
        clause's other."""
        return {"sell": self.who, "transfer": self.other}.get(self.kind)


def ledger(activity, users: dict, key_of: Callable[[AppId], PlayerKey | None],
           seen=()) -> list[Entry]:
    """The activity feed read once, each event once, completed by the squads
    seen (api_teams history): the feed leaves out some exits, so those are
    sales at the player's last value (_unrecorded_exits). Oldest first."""
    rows = {r.get("activity_id") or id(r): r for r in activity}.values()
    feed = [Entry(r.get("at") or "", r.get("kind") or "",
                  users.get(text(r, "user_id")), users.get(text(r, "counterparty")),
                  key_of(app_id(r)), r.get("amount") or 0.0)
            for r in rows]
    return sorted(feed + _unrecorded_exits(feed, seen, key_of), key=lambda e: e.at)


def _unrecorded_exits(feed: list[Entry], seen,
                      key_of: Callable[[AppId], PlayerKey | None]) -> list[Entry]:
    """For each manager and player, the exits the squads show beyond those
    the feed records (a sale by him, a clause paid to him), as sales at the
    player's last value. Counted, not matched by time: the squads and the
    feed are stamped minutes apart."""
    snaps: dict[str, dict] = {}
    for r in seen:
        if (k := key_of(app_id(r))) and text(r, "manager"):
            snaps.setdefault(r["observed_at"], {})[k] = (text(r, "manager"),
                                                         r.get("market_value") or 0.0)
    shown: dict[tuple, list] = {}
    stamps = sorted(snaps)
    for a, b in zip(stamps, stamps[1:]):
        managers = {who for who, _ in snaps[b].values()}
        for k, (who, value) in snaps[a].items():
            if who in managers and snaps[b].get(k, (None,))[0] != who:
                shown.setdefault((who, k), []).append(Entry(b, "sell", who, None, k, value))
    recorded: dict[tuple, int] = {}
    for e in feed:
        if e.seller:
            recorded[e.seller, e.key] = recorded.get((e.seller, e.key), 0) + 1
    return [x for wk, xs in shown.items() for x in xs[recorded.get(wk, 0):]]


def estimate_cash(entries: list[Entry], managers, me: str, my_cash: float | None,
                  budget: float) -> dict[str, float]:
    feed = {m: budget for m in managers}
    for e in entries:
        if e.buyer:
            feed[e.buyer] -= e.amount
        if e.seller:
            feed[e.seller] += e.amount
        if e.who and e.kind == "bonus":
            feed[e.who] += e.amount
    untracked = my_cash - feed[me] if my_cash is not None and me in feed else 0.0
    return {m: (my_cash if m == me and my_cash is not None else v + untracked)
            for m, v in feed.items()}


def price_paid(entries: list[Entry], owner: dict) -> dict[str, float]:
    """What each owned player's owner paid for him: his last buy or
    transfer to that manager. Players from the starting squad have none."""
    return {e.key: e.amount for e in entries
            if e.key and e.buyer and e.buyer == owner.get(e.key)}


class League:

    def __init__(self, cfg: Config, xw: Crosswalk, api_teams=(), standings=(),
                 activity=(), seen=()):
        self.cfg, self.xw, self.standings = cfg, xw, standings
        self.owner = {k: text(r, "manager") for r in api_teams
                      if (k := xw.player(app_id=app_id(r)))
                      and text(r, "manager")}
        users = {text(r, "user_id"): text(r, "manager") for r in standings
                 if text(r, "user_id") and text(r, "manager")}
        mine = next((r.get("team_money") for r in standings
                     if text(r, "manager") == cfg.me and r.get("team_money")),
                    None)
        entries = ledger(activity, users, self.key_of_app, seen)
        self.cash = estimate_cash(entries, users.values(), cfg.me, mine, cfg.budget)
        self.paid = price_paid(entries, self.owner)
        self.managers = sorted({cfg.me} | set(self.owner.values())
                               | set(users.values()))

    @classmethod
    def load(cls) -> "League":
        return cls(load_config(), load_crosswalk(), current("api_teams"),
                   current("api_standings"), current("api_activity"), history("api_teams"))

    @property
    def me(self) -> str:
        return self.cfg.me

    def key_of_app(self, app_id: AppId) -> PlayerKey | None:
        return self.xw.player(app_id=app_id)

    def squad(self, handle: str) -> list[str]:
        return sorted(k for k, m in self.owner.items() if m == handle)


def _selftest() -> None:
    from ffcore.tidy import typed
    import tempfile
    from pathlib import Path
    from ffcore.crosswalk import Crosswalk, Player

    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "league.ini"
        p.write_text("[league]\nme = someone\nbudget = 100.000.000\n"
                     , encoding="utf-8")
        cfg = load_config(str(p))
    assert (cfg.me, cfg.budget) == ("someone", 100e6), cfg

    users = {"1": "me", "2": "riv", "3": "quiet"}
    feed = typed("api_activity", [{"activity_id": "a", "kind": "buy", "user_id": "1", "amount": "30"},
            {"activity_id": "a", "kind": "buy", "user_id": "1", "amount": "30"},
            {"activity_id": "b", "kind": "sell", "user_id": "2", "amount": "20"},
            {"activity_id": "c", "kind": "bonus", "user_id": "2", "amount": "5"},
            {"activity_id": "d", "kind": "transfer", "user_id": "2",
             "counterparty": "1", "amount": "40"},
            {"activity_id": "e", "kind": "joined", "user_id": "3"}])
    entries = ledger(feed, users, lambda _id: None)
    assert len(entries) == 5, "each event once"
    got = estimate_cash(entries, users.values(), "me", 116.0, 100.0)
    assert got == {"me": 116.0, "riv": 91.0, "quiet": 106.0}, got
    assert estimate_cash(entries, users.values(), "me", None, 100.0)["riv"] == 85.0

    seen = typed("api_teams", [{"observed_at": "2026-09-01T1000Z", "manager": "riv", "player_id": "7", "market_value": "30"},
            {"observed_at": "2026-09-02T1000Z", "manager": "riv", "player_id": "8", "market_value": "5"}])
    def key(app_id: AppId) -> PlayerKey | None:
        return {"7": PlayerKey("p"), "8": PlayerKey("q")}.get(app_id)
    gone = ledger(feed, users, key, seen)
    assert estimate_cash(gone, users.values(), "me", None, 100.0)["riv"] == 85.0 + 30, \
        "a player who left with no event in the feed was sold at his last value"
    sold = feed + typed("api_activity", [{"activity_id": "f", "kind": "sell", "user_id": "2", "player_id": "7",
                    "amount": "31", "at": "2026-09-01T15:00:00+02:00"}])
    assert estimate_cash(ledger(sold, users, key, seen), users.values(), "me", None,
                         100.0)["riv"] == 85.0 + 31, "a recorded sale is not counted twice"
    taken = feed + typed("api_activity", [{"activity_id": "g", "kind": "transfer", "user_id": "1", "player_id": "7",
                     "counterparty": "2", "amount": "33", "at": "2026-09-02T12:00:30+02:00"}])
    assert estimate_cash(ledger(taken, users, key, seen), users.values(), "me", None,
                         100.0)["riv"] == 85.0 + 33, \
        "nor a clause paid for him, stamped after the snapshot that shows him gone"
    away = seen[:1] + typed("api_teams", [{"observed_at": "2026-09-02T1000Z", "manager": "me", "player_id": "8",
                        "market_value": "5"}])
    assert estimate_cash(ledger(feed, users, key, away), users.values(), "me", None,
                         100.0)["riv"] == 85.0, "a snapshot without his squad shows no exit"

    xw = Crosswalk({"p": Player("p", app_id="7")})
    lg = League(Config(me="me", budget=100.0), xw,
                api_teams=[{"manager": "riv", "player_id": "7"},
                           {"manager": "riv", "player_id": "404"}],
                standings=typed("api_standings", [{"user_id": "1", "manager": "me", "team_money": "116"},
                           {"user_id": "2", "manager": "riv"}]),
                activity=feed)
    assert lg.owner == {"p": "riv"} and lg.squad("riv") == ["p"], lg.owner
    assert lg.paid == {}, "nobody bought p"
    bought = League(Config(me="me", budget=100.0), xw,
                    api_teams=[{"manager": "riv", "player_id": "7"}],
                    standings=typed("api_standings", [{"user_id": "1", "manager": "me"}, {"user_id": "2", "manager": "riv"}]),
                    activity=typed("api_activity", [{"activity_id": "x", "kind": "buy", "user_id": "1", "player_id": "7",
                               "amount": "9", "at": "2026-09-01"},
                              {"activity_id": "y", "kind": "transfer", "user_id": "2", "player_id": "7",
                               "counterparty": "1", "amount": "12", "at": "2026-09-20"}]))
    assert bought.paid == {"p": 12.0}, ("what his owner paid, not the one before", bought.paid)
    assert lg.managers == ["me", "riv"] and lg.cash["riv"] == 91.0, lg.cash

    lineup = [{"player_id": "1070", "player_name": "Ionut Radu",
               "player_name_full": "Ionut Andrei Radu"},
              {"player_id": "2464", "player_name": "Pepelu",
               "player_name_full": "José Luis García Vayá"}]
    squad = {"ionut radu": 1, "pepelu": 1}
    for sq, names, rows, app_ids, want in [
            (squad, {"pepelu": "Pepelu"}, lineup, {"1070": "ionut radu"},
             ["ionut radu", "pepelu"]),
            (squad, {}, lineup + [{"player_id": "999", "player_name": "Nobody"}],
             {}, []),
            (squad, {}, lineup, {"1070": "ionut radu", "2464": "someone else"},
             []),
            ({}, {}, [], {}, [])]:
        xw_f = Crosswalk({k: Player(k, app_id=a) for a, k in app_ids.items()})
        assert app_fielded(sq, {k: Name(n) for k, n in names.items()}, rows, xw_f) == want, \
            (app_ids, want)

    print("ffcore.league self-test OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        raise SystemExit(0)
    lg = League.load()
    for m in lg.managers:
        print("%-18s %2d players  cash %7.2fM" % (m, len(lg.squad(m)),
                                                 lg.cash[m] / 1e6))
