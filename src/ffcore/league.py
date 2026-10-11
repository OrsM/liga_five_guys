from __future__ import annotations

import configparser
import sys
from collections.abc import Iterable, Mapping
from contextlib import closing
from dataclasses import dataclass

from ffcore import db as lfg
from ffcore.parse import Rows, money
from ffcore.players import load_crosswalk
from ffcore.tidy import current, input_path

from ffcore.crosswalk import Crosswalk
from ffcore.names import AppId, Name, PlayerKey, app_id
__all__ = ["Config", "load_config", "app_fielded", "League"]


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


def app_fielded(squad: Iterable[str], names: Mapping[str, Name], rows: Rows | None = None,
                xw: Crosswalk | None = None) -> list[str]:
    """Your eleven as the app has it, as our keys: by app id, else by name;
    [] if any of them is not in your squad."""
    rows = current("api_lineup") if rows is None else rows
    xw = xw or load_crosswalk()
    mine = set(squad)
    by_name = {names.get(k) or Name(k): k for k in mine}
    out: list[str] = []
    for r in rows or []:
        key: str | None = xw.player(app_id=app_id(r))
        if key is None:
            for field in ("player_name", "player_name_full"):
                key = by_name.get(Name(r.get(field)))
                if key:
                    break
        if not key or key not in mine:
            return []
        out.append(key)
    return out


class League:
    """The league as lfg.db has it (schema.sql: owners, users, cash,
    price_paid over the app's tables, as of cut()). Writes the two inputs no
    table holds: the crosswalk's app ids and the config."""

    def __init__(self, cfg: Config, xw: Crosswalk) -> None:
        self.cfg, self.xw = cfg, xw
        ids = {p.app_id for p in xw.players.values() if p.app_id}
        with closing(lfg.connect()) as db:
            with db:
                db.execute("DELETE FROM app_keys")
                db.executemany("INSERT INTO app_keys VALUES (?, ?)",
                               [(a, k) for a in sorted(ids) if (k := xw.player(app_id=a))])
                db.execute("DELETE FROM league_config")
                db.execute("INSERT INTO league_config VALUES (?, ?)", (cfg.me, cfg.budget))
            self.owner: dict[str, str] = dict(db.execute("SELECT player, manager FROM owners"))
            users = [m for (m,) in db.execute("SELECT manager FROM users")]
            self.cash: dict[str, float] = dict(db.execute("SELECT manager, cash FROM cash"))
            self.paid: dict[str, float] = dict(db.execute("SELECT player, paid FROM price_paid"))
        self.standings = current("api_standings")
        self.managers = sorted({cfg.me} | set(self.owner.values()) | set(users))

    @classmethod
    def load(cls) -> "League":
        return cls(load_config(), load_crosswalk())

    @property
    def me(self) -> str:
        return self.cfg.me

    def key_of_app(self, app_id: AppId) -> PlayerKey | None:
        return self.xw.player(app_id=app_id)

    def squad(self, handle: str) -> list[str]:
        return sorted(k for k, m in self.owner.items() if m == handle)


def _selftest() -> None:
    import tempfile
    from pathlib import Path
    from ffcore.crosswalk import Crosswalk, Player
    from ffcore.tidy import store_sql, tables_in

    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "league.ini"
        p.write_text("[league]\nme = someone\nbudget = 100.000.000\n"
                     , encoding="utf-8")
        cfg = load_config(str(p))
    assert (cfg.me, cfg.budget) == ("someone", 100e6), cfg

    a, b = "2026-09-01T1000Z", "2026-09-02T1000Z"
    feed = [{"activity_id": "a", "kind": "buy", "user_id": "1", "amount": "30"},
            {"activity_id": "a", "kind": "buy", "user_id": "1", "amount": "30"},
            {"activity_id": "b", "kind": "sell", "user_id": "2", "amount": "20"},
            {"activity_id": "c", "kind": "bonus", "user_id": "2", "amount": "5"},
            {"activity_id": "d", "kind": "transfer", "user_id": "2",
             "counterparty": "1", "amount": "40"},
            {"activity_id": "e", "kind": "joined", "user_id": "3"}]
    users = [{"user_id": "1", "manager": "me"}, {"user_id": "2", "manager": "riv"},
             {"user_id": "3", "manager": "quiet"}]
    pq = Crosswalk({"p": Player(PlayerKey("p"), app_id=AppId("7")),
                    "q": Player(PlayerKey("q"), app_id=AppId("8"))})

    def league(activity: list[dict[str, str]], standings: list[dict[str, str]],
               teams: list[dict[str, str]], xw: Crosswalk = pq) -> League:
        """A League read from lfg.db holding these rows (teams stamped a or b)."""
        with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
            store_sql("api_activity", [{"at": "", "counterparty": "", "player_id": "", **r,
                                        "observed_at": a} for r in activity])
            store_sql("api_standings", [dict(r, observed_at=b) for r in standings])
            for stamp in (a, b):
                store_sql("api_teams", [r for r in teams if r["observed_at"] == stamp]
                          or [{"observed_at": stamp, "manager": "", "player_id": ""}])
            return League(Config(me="me", budget=100.0), xw)

    with_money = [dict(users[0], team_money="116")] + users[1:]
    assert league(feed, with_money, []).cash == {"me": 116.0, "riv": 91.0, "quiet": 106.0}, \
        "each event once; what the feed misses is the gap to your real balance"
    assert league(feed, users, []).cash["riv"] == 85.0

    seen = [{"observed_at": a, "manager": "riv", "player_id": "7", "market_value": "30"},
            {"observed_at": b, "manager": "riv", "player_id": "8", "market_value": "5"}]
    assert league(feed, users, seen).cash["riv"] == 85.0 + 30, \
        "a player who left with no event in the feed was sold at his last value"
    sold = feed + [{"activity_id": "f", "kind": "sell", "user_id": "2", "player_id": "7",
                    "amount": "31", "at": "2026-09-01T15:00:00+02:00"}]
    assert league(sold, users, seen).cash["riv"] == 85.0 + 31, \
        "a recorded sale is not counted twice"
    taken = feed + [{"activity_id": "g", "kind": "transfer", "user_id": "1", "player_id": "7",
                     "counterparty": "2", "amount": "33", "at": "2026-09-02T12:00:30+02:00"}]
    assert league(taken, users, seen).cash["riv"] == 85.0 + 33, \
        "nor a clause paid for him, stamped after the snapshot that shows him gone"
    away = seen[:1] + [{"observed_at": b, "manager": "me", "player_id": "8",
                        "market_value": "5"}]
    assert league(feed, users, away).cash["riv"] == 85.0, \
        "a snapshot without his squad shows no exit"

    xw = Crosswalk({"p": Player(PlayerKey("p"), app_id=AppId("7"))})
    lg = league(feed, with_money[:2], [{"observed_at": b, "manager": "riv", "player_id": "7"},
                                       {"observed_at": b, "manager": "riv", "player_id": "404"}],
                xw)
    assert lg.owner == {"p": "riv"} and lg.squad("riv") == ["p"], lg.owner
    assert lg.paid == {}, "nobody bought p"
    assert lg.managers == ["me", "riv"] and lg.cash["riv"] == 91.0, lg.cash
    bought = league([{"activity_id": "x", "kind": "buy", "user_id": "1", "player_id": "7",
                      "amount": "9", "at": "2026-09-01"},
                     {"activity_id": "y", "kind": "transfer", "user_id": "2", "player_id": "7",
                      "counterparty": "1", "amount": "12", "at": "2026-09-20"}],
                    users[:2], [{"observed_at": b, "manager": "riv", "player_id": "7"}], xw)
    assert bought.paid == {"p": 12.0}, ("what his owner paid, not the one before", bought.paid)

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
        xw_f = Crosswalk({k: Player(PlayerKey(k), app_id=AppId(a)) for a, k in app_ids.items()})
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
