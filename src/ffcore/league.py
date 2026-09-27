from __future__ import annotations

import configparser
import sys
from dataclasses import dataclass

from ffcore.parse import money, text
from ffcore.text import norm
from ffcore.players import load_crosswalk
from ffcore.tidy import current, input_path

__all__ = ["Config", "load_config", "app_fielded", "estimate_cash", "League"]


@dataclass
class Config:
    me: str = "miguel_autentico"
    budget: float = 100e6


def load_config(name: str = "league.ini") -> Config:
    cp = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
    cp.optionxform = str
    path = input_path(name)
    if path.exists():
        cp.read(path, encoding="utf-8")
    base = Config()
    return Config(
        me=cp.get("league", "me", fallback=base.me),
        budget=money(cp.get("league", "budget", fallback=str(base.budget))) or 0.0)


def app_fielded(squad, names: dict, rows=None, xw=None) -> list[str]:
    from ffcore.crosswalk import Crosswalk

    rows = current("api_lineup") if rows is None else rows
    xw = xw or load_crosswalk() or Crosswalk()
    squad = set(squad)
    by_name = {norm(names.get(k, k)): k for k in squad}
    out = []
    for r in rows or []:
        key = xw.player(app_id=text(r, "player_id"))
        if key is None:
            for field in ("player_name", "player_name_full"):
                key = by_name.get(norm(r.get(field) or ""))
                if key:
                    break
        if not key or key not in squad:
            return []
        out.append(key)
    return out


def estimate_cash(activity, users: dict, me: str, my_cash: float | None,
                  budget: float) -> dict[str, float]:
    feed = {m: budget for m in users.values()}
    for r in {r.get("activity_id") or id(r): r for r in activity}.values():
        amount = money(r.get("amount")) or 0.0
        who, other = users.get(text(r, "user_id")), users.get(
            text(r, "counterparty"))
        kind = r.get("kind")
        if who and kind in ("sell", "bonus"):
            feed[who] += amount
        elif who and kind in ("buy", "clause"):
            feed[who] -= amount
        if other and kind == "clause":
            feed[other] += amount
    untracked = my_cash - feed[me] if my_cash is not None and me in feed else 0.0
    return {m: (my_cash if m == me and my_cash is not None else v + untracked)
            for m, v in feed.items()}


class League:

    def __init__(self, cfg: Config, xw, api_teams=(), standings=(),
                 activity=()):
        self.cfg, self.xw, self.standings = cfg, xw, standings
        self.owner = {k: text(r, "manager") for r in api_teams
                      if (k := xw.player(app_id=text(r, "player_id")))
                      and text(r, "manager")}
        users = {text(r, "user_id"): text(r, "manager") for r in standings
                 if text(r, "user_id") and text(r, "manager")}
        mine = next((money(r.get("team_money")) for r in standings
                     if text(r, "manager") == cfg.me and r.get("team_money")),
                    None)
        self.cash = estimate_cash(activity, users, cfg.me, mine, cfg.budget)
        self.managers = sorted({cfg.me} | set(self.owner.values())
                               | set(users.values()))

    @classmethod
    def load(cls) -> "League":
        return cls(load_config(), load_crosswalk(), current("api_teams"),
                   current("api_standings"), current("api_activity"))

    def squad(self, handle: str) -> list[str]:
        return sorted(k for k, m in self.owner.items() if m == handle)


def _selftest() -> None:
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
    feed = [{"activity_id": "a", "kind": "buy", "user_id": "1", "amount": "30"},
            {"activity_id": "a", "kind": "buy", "user_id": "1", "amount": "30"},
            {"activity_id": "b", "kind": "sell", "user_id": "2", "amount": "20"},
            {"activity_id": "c", "kind": "bonus", "user_id": "2", "amount": "5"},
            {"activity_id": "d", "kind": "clause", "user_id": "2",
             "counterparty": "1", "amount": "40"},
            {"activity_id": "e", "kind": "joined", "user_id": "3"}]
    got = estimate_cash(feed, users, "me", 116.0, 100.0)
    assert got == {"me": 116.0, "riv": 91.0, "quiet": 106.0}, got
    assert estimate_cash(feed, users, "me", None, 100.0)["riv"] == 85.0

    xw = Crosswalk({"p": Player("p", app_id="7")})
    lg = League(Config(me="me", budget=100.0), xw,
                api_teams=[{"manager": "riv", "player_id": "7"},
                           {"manager": "riv", "player_id": "404"}],
                standings=[{"user_id": "1", "manager": "me", "team_money": "116"},
                           {"user_id": "2", "manager": "riv"}],
                activity=feed)
    assert lg.owner == {"p": "riv"} and lg.squad("riv") == ["p"], lg.owner
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
        assert app_fielded(sq, names, rows, xw_f) == want, (app_ids, want)

    print("ffcore.league self-test OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        raise SystemExit(0)
    lg = League.load()
    for m in lg.managers:
        print("%-18s %2d players  cash %7.2fM" % (m, len(lg.squad(m)),
                                                 lg.cash[m] / 1e6))
