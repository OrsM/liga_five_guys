from __future__ import annotations

import configparser
import sys
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from ffcore import db as lfg
from ffcore.parse import money
from ffcore.tidy import input_path

__all__ = ["Config", "load_config", "write_config"]


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


def write_config(cfg: Config | None = None, path: Path | None = None) -> str:
    """The one input of the league's views no table holds (schema.sql:
    owners, users, cash, price_paid over the app's tables as of t): you and
    the budget, into league_config. You."""
    cfg = cfg or load_config()
    with closing(lfg.connect(path)) as db, db:
        db.execute("DELETE FROM league_config")
        db.execute("INSERT INTO league_config VALUES (?, ?)", (cfg.me, cfg.budget))
    return cfg.me


def _selftest() -> None:
    import tempfile
    from pathlib import Path
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
    pq = {"p": "7", "q": "8"}

    class League:
        """The league's views read from lfg.db: owners, cash, price_paid."""
        def __init__(self) -> None:
            write_config(Config(me="me", budget=100.0))
            self.owner = dict(lfg.rows("SELECT player, manager FROM owners"))
            self.cash = dict(lfg.rows("SELECT manager, cash FROM cash"))
            self.paid = dict(lfg.rows("SELECT player, paid FROM price_paid"))
            self.managers = sorted({"me"} | set(self.owner.values())
                                   | {m for (m,) in lfg.rows("SELECT manager FROM users")})

        def squad(self, handle: str) -> list[str]:
            return sorted(k for k, m in self.owner.items() if m == handle)

    def league(activity: list[dict[str, str]], standings: list[dict[str, str]],
               teams: list[dict[str, str]], xw: dict[str, str] = pq) -> League:
        """The league's views over these rows (teams stamped a or b)."""
        with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
            store_sql("api_activity", [{"at": "", "counterparty": "", "player_id": "", **r,
                                        "observed_at": a} for r in activity])
            store_sql("api_standings", [dict(r, team_id="t" + r["user_id"], observed_at=b) for r in standings])
            for stamp in (a, b):
                store_sql("api_teams", [r for r in teams if r["observed_at"] == stamp]
                          or [{"observed_at": stamp, "manager": "", "player_id": ""}])
            with closing(lfg.connect()) as db, db:
                db.executemany("INSERT INTO crosswalk (player_id, app_id) VALUES (?, ?)", xw.items())
            return League()

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

    xw = {"p": "7"}
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

    print("ffcore.league self-test OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        raise SystemExit(0)
    write_config()
    for m, n, cash in lfg.rows("SELECT c.manager, count(o.player), c.cash FROM cash c"
                               " LEFT JOIN owners o USING (manager) GROUP BY c.manager"):
        print("%-18s %2d players  cash %7.2fM" % (m, n, cash / 1e6))
