
from __future__ import annotations

import math
import configparser
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import Path
from typing import NamedTuple

from ffcore.parse import text
from ffcore.parse import money
from ffcore.tidy import shown
from ffcore.text import norm
from ffcore.tidy import (load_crosswalk, run_now, Market, input_path,
                         ledger_stamp, load_api, load_api_activity, newest,
                         read_ledger, snapshot_stamp, table)

__all__ = ["MARKET", "Config", "load_config", "read_api_balances",
           "app_fielded", "flat_income", "bonus_income", "allowance",
           "Cash", "Manager", "League"]

MARKET = "market"


DEFAULTS = {
    "me": "miguel_autentico",
    "budget": "100000000",
    "min_start": "60",
    "start_cross": "70",
    "shrink_k": "8",
    "daily_bonus": "0",
}


@dataclass
class Config:
    me: str = DEFAULTS["me"]
    budget: float = float(DEFAULTS["budget"])
    min_start: float = 60.0
    start_cross: float = 70.0
    shrink_k: float = 8.0
    daily_bonus: float = 0.0


def load_config(name: str = "league.ini") -> Config:
    path = input_path(name)
    cp = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
    cp.optionxform = str
    if path.exists():
        cp.read(path, encoding="utf-8")

    def get(section, key, default):
        try:
            return cp.get(section, key)
        except (configparser.NoSectionError, configparser.NoOptionError):
            return default

    cfg = Config(
        me=get("league", "me", DEFAULTS["me"]),
        budget=money(get("league", "budget", DEFAULTS["budget"])) or 0.0,
        min_start=float(get("thresholds", "min_start", DEFAULTS["min_start"])),
        start_cross=float(get("thresholds", "start_cross",
                              DEFAULTS["start_cross"])),
        shrink_k=float(get("thresholds", "shrink_k", DEFAULTS["shrink_k"])),
        daily_bonus=money(get("league", "daily_bonus",
                              DEFAULTS["daily_bonus"])) or 0.0,
    )
    return cfg


def read_api_balances(rows) -> dict[str, tuple[float, str]]:
    out: dict[str, tuple[float, str]] = {}
    for r in rows:
        handle = text(r, "manager")
        raw = text(r, "team_money")
        if not handle or not raw:
            continue
        try:
            value = float(raw)
        except ValueError:
            continue
        when = snapshot_stamp(r.get("observed_at") or "")
        out[handle] = (value, when,
                       "balance the app reported at %s" % shown(when))
    return out


def flat_income(observed, budget: float, bought: float, sold: float):
    if observed is None:
        return None
    return max(0.0, observed - (budget + sold - bought))


def _user_of(r: dict, users: dict, field: str = "user_id") -> str | None:
    return users.get(str(r.get(field) or ""))


def bonus_income(activity: list[dict], users: dict) -> dict[str, float]:
    out: dict[str, float] = {}
    for r in activity:
        if r.get("kind") != "bonus":
            continue
        who = _user_of(r, users)
        if not who:
            continue
        out[who] = out.get(who, 0.0) + (money(r.get("amount")) or 0.0)
    return out


def allowance(since, now, daily_bonus: float) -> tuple[float, float]:
    if since is None or now is None:
        return 0.0, 0.0
    days = max(0.0, (now - since).total_seconds() / 86400.0)
    return math.floor(days) * (daily_bonus or 0.0), days


def app_fielded(squad, names: dict, rows=None, xw=None) -> list[str]:
    from ffcore.crosswalk import Crosswalk
    from ffcore.tidy import load_api

    rows = load_api("lineup") if rows is None else rows
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


def ledger_from_api(activity: list[dict], users: dict,
                    names: dict) -> list[dict]:
    out = []
    for r in sorted(activity, key=lambda x: x.get("at") or ""):
        kind = r.get("kind")
        if kind not in ("buy", "sell", "clause"):
            continue
        who = _user_of(r, users)
        player = names.get(str(r.get("player_id") or ""))
        if not who or not player:
            continue
        if kind == "clause":
            victim = _user_of(r, users, "counterparty")
            if not victim:
                continue
            frm, to = victim, who
        else:
            frm = MARKET if kind == "buy" else who
            to = who if kind == "buy" else MARKET
        out.append({
            "date": (r.get("at") or "")[:16],
            "player": player,
            "player_id": str(r.get("player_id") or ""),
            "from": frm,
            "to": to,
            "price": str(r.get("amount") or ""),
            "note": "from the app",
        })
    return out


def gone_at(history: list[dict], pid: str, manager: str, bought):
    last = max((r["observed_at"] for r in history
                if r["player_id"] == pid and r["manager"] == manager),
               default="")
    for s in sorted({r["observed_at"] for r in history}):
        t = snapshot_stamp(s)
        if s > last and t and bought and t > bought:
            return t
    return None


class Cash(NamedTuple):
    value: float | None
    confidence: str
    basis: str
    as_of: str
    base: float = 0.0
    bought: float = 0.0
    sold: float = 0.0

    def label(self) -> str:
        if self.value is None:
            return "—"
        v = ("%.2fM" % (self.value / 1e6) if abs(self.value) >= 1e6
             else "%.0fK" % (self.value / 1e3))
        return v if self.confidence == "known" else "~" + v

    @property
    def overdrawn(self) -> bool:
        return self.value is not None and self.value < 0


@dataclass
class Manager:
    handle: str
    players: list = field(default_factory=list)
    buys: list = field(default_factory=list)
    sales: list = field(default_factory=list)
    cash: Cash = Cash(None, "unknown", "", "")

    @staticmethod
    def _total(txns) -> float:
        return sum(money(t.get("price")) or 0 for t in txns)

    @property
    def spend(self) -> float:
        return self._total(self.buys)

    @property
    def proceeds(self) -> float:
        return self._total(self.sales)

    @property
    def net(self) -> float:
        return self.proceeds - self.spend

    @property
    def max_bid(self) -> float | None:
        if self.cash.value is None:
            return None
        return max(0.0, self.cash.value)


class League:

    def __init__(self, cfg: Config, txns, market: Market | None, xw,
                 api_teams=(), standings=(), roster_history=()):
        self.cfg = cfg
        self.market = market
        self.xw = xw
        self._standings = standings
        self.txns = [dict(t, key=xw.player(app_id=text(
            t, "player_id"))) for t in txns]
        self.owner: dict[str, str] = {}
        self.api_unjoined: list[str] = []
        for r in api_teams:
            handle = text(r, "manager")
            key = xw.player(app_id=text(r, "player_id"))
            if key and handle:
                self.owner[key] = handle
            elif handle:
                self.api_unjoined.append(
                    text(r, "player_name"))
        self.warnings = [
            "**%s** — the app says he is owned, but no player in the "
            "crosswalk has his app id, so he is missing from the board." % raw
            for raw in self.api_unjoined]

        last: dict[str, tuple] = {}
        for t in self.txns:
            if t["key"]:
                last[t["key"]] = (
                    text(t, "to") or MARKET,
                    text(t, "player_id"),
                    ledger_stamp(t.get("date", "")))
        self.dropped = {k: (m, gone_at(roster_history, pid, m, when))
                        for k, (m, pid, when) in last.items()
                        if self.owner and m != MARKET and k not in self.owner}

        handles = ({cfg.me} | set(self.owner.values())
                   | {text(r, "manager")
                      for r in standings}
                   | {text(t, f) for t in self.txns
                      for f in ("from",
                                "to")}) - {MARKET, ""}
        self.managers: dict[str, Manager] = {h: Manager(h)
                                             for h in sorted(handles)}
        for key, mgr in self.owner.items():
            self.managers[mgr].players.append(key)
        for t in self.txns:
            src = text(t, "from") or MARKET
            dst = text(t, "to") or MARKET
            if dst != MARKET:
                self.managers[dst].buys.append(t)
            if src != MARKET:
                self.managers[src].sales.append(t)

        self._estimate_cash()

    @classmethod
    def load(cls, with_market: bool = True) -> "League":
        return cls(load_config(), read_ledger(),
                   Market(table("market")) if with_market else None,
                   load_crosswalk(), api_teams=newest("api_teams"),
                   standings=newest("api_standings"),
                   roster_history=table("api_teams"))

    def __getitem__(self, handle: str) -> Manager:
        return self.managers[handle]

    def __iter__(self):
        return iter(sorted(self.managers.values(),
                           key=lambda m: (m.handle != self.cfg.me, m.handle)))


    def _estimate_cash(self) -> None:
        balances = read_api_balances(self._standings)

        paid = None
        me_anchor = balances.get(self.cfg.me)
        if me_anchor and me_anchor[1] and self.cfg.budget:
            b, sd = 0.0, 0.0
            for t in self.txns:
                price = money(t.get("price")) or 0.0
                if text(t, "to") == self.cfg.me:
                    b += price
                if text(t, "from") == self.cfg.me:
                    sd += price
            paid = flat_income(me_anchor[0], self.cfg.budget, b, sd)

        users = {r.get("user_id"): r.get("manager")
                 for r in self._standings
                 if r.get("user_id") and r.get("manager")}
        own_bonus = bonus_income(load_api_activity(), users)

        click_rate, my_clicks, my_days = 0.0, 0, 0.0
        me_txns = [t for t in self.txns
                  if text(t, "to") == self.cfg.me
                  or text(t, "from") == self.cfg.me]
        me_start = min((ledger_stamp(t.get("date", "")) for t in me_txns
                       if ledger_stamp(t.get("date", ""))), default=None)
        if paid is not None and self.cfg.daily_bonus:
            _, my_days = allowance(me_start, run_now(), self.cfg.daily_bonus)
            my_clicks = round((paid - own_bonus.get(self.cfg.me, 0.0))
                              / self.cfg.daily_bonus)
            click_rate = my_clicks / my_days if my_days else 0.0

        for handle, mgr in self.managers.items():
            budget = self.cfg.budget
            anchor = balances.get(handle)

            if anchor:
                base, since, basis = anchor
                conf = "known"
            else:
                if not budget:
                    mgr.cash = Cash(None, "unknown",
                                    "no balance recorded and no budget "
                                    "configured", "")
                    continue
                base, since, conf = budget, None, "estimated"
                basis = "%.0fM starting budget" % (budget / 1e6)

            bought = sold = 0.0
            counted = 0
            for t in self.txns:
                when = ledger_stamp(t.get("date", ""))
                if since and when and when <= since:
                    continue
                price = money(t.get("price")) or 0.0
                src = text(t, "from") or MARKET
                dst = text(t, "to") or MARKET
                if dst == handle:
                    bought += price
                    counted += 1
                if src == handle:
                    sold += price
                    counted += 1

            notes = []
            start = since or min(
                (ledger_stamp(t.get("date", "")) for t in self.txns
                 if ledger_stamp(t.get("date", ""))), default=None)
            daily, days = allowance(start, run_now(), self.cfg.daily_bonus)
            if since is None and handle in own_bonus:
                clicks = round(click_rate * days) if days else 0
                bonus = clicks * self.cfg.daily_bonus + own_bonus[handle]
                if clicks:
                    notes.append("%d payments of %.0fk assuming they claim "
                                 "as often as you do (you: %d in %.0f days)"
                                 % (clicks, self.cfg.daily_bonus / 1e3,
                                    my_clicks, my_days))
                notes.append("%.2fM of their own weekly performance bonus, "
                             "recorded in the app's activity feed"
                             % (own_bonus[handle] / 1e6))
            elif since is None and paid is not None:
                bonus = paid
                notes.append("%.2fM the app has paid you since the season "
                             "began (no bonus activity found for them, so "
                             "yours stands in)" % (bonus / 1e6))
            else:
                bonus = daily
                if days is not None and daily:
                    notes.append("%.2fM of daily allowance over %.0f days"
                                 % (daily / 1e6, days))
            refund = 0.0
            for k, (m, when) in self.dropped.items():
                v = (self.market.at(k, when)
                     if m == handle and since is None and self.market else None)
                if v is not None and v.value:
                    refund += v.value
                    notes.append("%.2fM assuming the app paid %s's market value "
                                 "when he left their squad (gone by %s, no sale "
                                 "in the feed)" % (v.value / 1e6, v.name,
                                                   shown(when)))
            bonus_note = " and ".join(notes) if notes else None

            value = base + sold - bought + bonus + refund
            math = ("%s − %.2fM bought + %.2fM sold across %d ledger row(s)"
                    "%s = %.2fM"
                    % (basis, bought / 1e6, sold / 1e6, counted,
                       ((" + " + bonus_note) if bonus_note else ""),
                       value / 1e6))
            if value < 0:
                self.warnings.append(
                    "%s is %.2fM overdrawn: %s. Going over the budget "
                    "mid-window is allowed; being overdrawn when the jornada "
                    "locks is not, so they must sell before they can buy "
                    "again. If the ledger is missing a sale of theirs, this "
                    "is stale rather than wrong."
                    % (handle, -value / 1e6, math))
            mgr.cash = Cash(value, conf, math, shown(), base, bought, sold)


    def squad(self, handle: str) -> list[str]:
        return sorted(k for k, m in self.owner.items() if m == handle)


    def unmatched(self, known_keys) -> list[str]:
        return sorted(k for k in self.owner if k not in known_keys)


def _selftest() -> None:
    import tempfile

    ini = """
[league]
me = someone
budget = 100.000.000

[thresholds]
min_start     = 60    ; watchlist floor
start_cross   = 70    # rows per position
"""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "league.ini"
        p.write_text(ini, encoding="utf-8")
        cfg = load_config(str(p))

    assert cfg.me == "someone", cfg.me
    assert cfg.budget == 100_000_000, cfg.budget
    assert cfg.min_start == 60.0, cfg.min_start
    assert cfg.start_cross == 70.0, cfg.start_cross
    assert cfg.shrink_k == 8.0, cfg.shrink_k
    assert load_config is not None
    assert not hasattr(cfg, "lambda_buffer"), "lambda_buffer should be gone"
    for gone in ("top_n_per_pos", "keeper_start", "riser_pct"):
        assert not hasattr(cfg, gone), gone

    real = load_config()
    assert real.min_start is not None

    from ffcore.crosswalk import Crosswalk, Player

    xw = Crosswalk({k: Player(k, k.title(), app_id=a) for k, a in
                    [("p", "1"), ("ghost", "66"), ("seen", "77"),
                     ("kept", "5")]})
    at = "2026-08-17T2318Z"
    mkt = Market([{"name": "P", "value": "1000000", "observed_at": at}])
    table = [{"manager": "me", "user_id": "1", "team_id": "1",
              "team_money": "23596582", "observed_at": at}]
    buy = {"date": "2026-08-17T22:24", "player": "P", "player_id": "1",
           "from": MARKET, "to": "me", "price": "10000000"}

    lg = League(Config(me="me"), [buy], mkt, xw, standings=table,
                api_teams=[{"manager": "me", "player_id": "1"},
                           {"manager": "me", "player_id": "404",
                            "player_name": "Stranger"}])
    assert lg.owner == {"p": "me"} and lg["me"].players == ["p"], lg.owner
    assert lg.api_unjoined == ["Stranger"], lg.api_unjoined
    assert lg.txns[0]["key"] == "p", lg.txns
    assert lg["me"].cash.value == 23596582.0, lg["me"].cash
    assert lg["me"].cash.confidence == "known", lg["me"].cash
    later = League(Config(me="me"), [buy, dict(buy, date="2026-08-18T09:00",
                                               price="1000000")],
                   mkt, xw, standings=table)
    assert later["me"].cash.value == 23596582.0 - 1000000.0, later["me"].cash

    cash = League(Config(me="me", budget=100e6), [
        {"date": "2026-08-11T21:24", "player": "x", "from": MARKET,
         "to": "rich", "price": "10000000"},
        {"date": "2026-08-11T21:42", "player": "b", "from": "rich",
         "to": MARKET, "price": "4000000"},
        {"date": "2026-08-12T21:24", "player": "y", "from": MARKET,
         "to": "spent", "price": "124560000"}], None, xw)
    rich, over = cash["rich"].cash, cash["spent"].cash
    assert rich.value == 94e6 and cash["me"].cash.value == 100e6, rich
    assert cash["me"].cash.confidence == "estimated"
    for part in ("10.00M bought", "4.00M sold", "= 94.00M"):
        assert part in rich.basis, (part, rich.basis)
    assert over.value == 100e6 - 124.56e6 and over.overdrawn, over
    assert "= -24.56M" in over.basis and cash["spent"].max_bid == 0.0
    assert not rich.overdrawn
    assert any("overdrawn" in w for w in cash.warnings), cash.warnings

    blind = League(Config(me="nobody", budget=0.0), [], None, xw)
    assert blind["nobody"].cash.value is None
    assert blind["nobody"].max_bid is None and not blind["nobody"].cash.overdrawn

    old = [{"date": "2026-01-01T12:00", "player": "P", "from": MARKET,
            "to": "rival", "price": "10000000"}]
    plain = League(Config(me="me", budget=100e6), old, None, xw)
    bonused = League(Config(me="me", budget=100e6, daily_bonus=100000.0),
                     old, None, xw)
    assert bonused["rival"].cash.value > plain["rival"].cash.value
    assert "daily allowance" in bonused["rival"].cash.basis

    for days, extra in [(0, 0.0), (4, 400000.0)]:
        seen = (datetime.now(dt_timezone.utc) - timedelta(days=days)
                ).strftime("%Y-%m-%dT%H%MZ")
        got = League(Config(me="me", daily_bonus=100000.0), [], mkt, xw,
                     api_teams=[{"manager": "me", "player_id": "1"}],
                     standings=[dict(table[0], observed_at=seen)])["me"].cash
        assert abs(got.value - (23596582.0 + extra)) < 5000, (days, got)
    assert "4 days" in got.basis, got.basis

    history = Market([{"name": n, "value": v, "observed_at": t}
                      for n, t, v in [
                          ("Ghost", "2026-08-17T2246Z", "8000000"),
                          ("Ghost", "2026-08-25T0600Z", "3000000"),
                          ("Seen", "2026-08-17T2246Z", "9000000"),
                          ("Seen", "2026-08-19T0600Z", "6000000"),
                          ("Seen", "2026-08-25T0600Z", "2000000"),
                          ("Kept", "2026-08-17T2246Z", "1000000")]])
    snaps = [{"observed_at": t, "manager": "rival", "player_id": p}
             for t, ps in [("2026-08-17T2246Z", "5 77"),
                           ("2026-08-18T0600Z", "5 77"),
                           ("2026-08-19T0600Z", "5")] for p in ps.split()]
    gone = League(Config(me="me", budget=100e6), [
        {"date": "2026-08-12T22:24", "player": n, "player_id": i,
         "from": MARKET, "to": "rival", "price": pr}
        for n, i, pr in [("Ghost", "66", "20000000"),
                         ("Seen", "77", "10000000"),
                         ("Kept", "5", "1000000")]],
        history, xw, api_teams=[{"manager": "rival", "player_id": "5"}],
        roster_history=snaps)
    assert {k: v[1].strftime("%m-%dT%H%M") for k, v in gone.dropped.items()} \
        == {"ghost": "08-17T2246", "seen": "08-19T0600"}, gone.dropped
    assert gone["rival"].cash.value == 83e6, gone["rival"].cash
    assert "8.00M assuming the app paid Ghost's market value when he left" \
        in gone["rival"].cash.basis
    assert gone_at([], "1", "m", None) is None
    assert not lg.dropped

    now = datetime(2026, 8, 19, 12, 0, tzinfo=dt_timezone.utc)
    four = datetime(2026, 8, 15, 12, 0, tzinfo=dt_timezone.utc)
    for since, until, rate, want in [
            (four, now, 100000, (400000.0, 4.0)), (now, now, 100000, (0.0, 0.0)),
            (four, now, 0, (0.0, 4.0)),
            (four - timedelta(hours=12), now, 100000, (400000.0, 4.5)),
            (now, four, 100000, (0.0, 0.0)), (None, now, 100000, (0.0, 0.0))]:
        assert allowance(since, until, rate) == want, (since, until, rate)
    assert round(flat_income(8906184.0, 100e6, 142.18e6, 49.82e6)) == 1266184
    assert flat_income(1.0, 100e6, 0.0, 0.0) == 0.0
    assert flat_income(None, 100e6, 0.0, 0.0) is None

    _selftest_derived_ledger()
    print("ffcore.league self-test OK")


def _selftest_derived_ledger() -> None:
    users = {"11881989": "miguel_autentico", "11883172": "BurtonGM89"}
    names = {"1337": "Fornals", "652": "Hugo Duro"}
    feed = [
        {"at": "2026-08-15T22:24:00+02:00", "kind": "buy", "user_id":
         "11881989", "player_id": "1337", "amount": "58220110"},
        {"at": "2026-08-17T00:21:10+02:00", "kind": "sell", "user_id":
         "11881989", "player_id": "652", "amount": "15202722"},
        {"at": "2026-08-10T22:24:00+02:00", "kind": "joined", "user_id":
         "3480702", "player_id": "", "amount": "0"},
    ]
    rows = ledger_from_api(feed, users, names)

    assert len(rows) == 2, rows
    assert rows[0]["date"] < rows[1]["date"], rows

    assert rows[0] == {"date": "2026-08-15T22:24", "player": "Fornals",
                       "player_id": "1337",
                       "from": MARKET, "to": "miguel_autentico",
                       "price": "58220110", "note": "from the app"}, rows[0]
    assert rows[1]["from"] == "miguel_autentico", rows[1]
    assert rows[1]["to"] == MARKET and rows[1]["player"] == "Hugo Duro"

    assert rows[0]["date"] == "2026-08-15T22:24", rows[0]

    rows2 = ledger_from_api(
        feed + [{"at": "2026-08-16T10:00:00+02:00", "kind": "buy",
                 "user_id": "11881989", "player_id": "9999",
                 "amount": "1"}], users, names)
    assert len(rows2) == 2, rows2

    rows3 = ledger_from_api(
        [{"at": "2026-08-16T10:00:00+02:00", "kind": "buy",
          "user_id": "404", "player_id": "1337", "amount": "1"}],
        users, names)
    assert rows3 == [], rows3

    assert ledger_from_api([], users, names) == []

    clause = ledger_from_api(
        [{"at": "2026-09-18T22:25:51+02:00", "kind": "clause",
          "user_id": "11883172", "counterparty": "11881989",
          "player_id": "1337", "amount": "141425721"}], users, names)
    assert len(clause) == 1, clause
    assert clause[0]["from"] == "miguel_autentico", clause[0]
    assert clause[0]["to"] == "BurtonGM89", clause[0]
    assert clause[0]["from"] != MARKET and clause[0]["to"] != MARKET, \
        "a clause is manager to manager, never via the market"
    assert clause[0]["price"] == "141425721", clause[0]

    assert ledger_from_api(
        [{"at": "2026-09-18T22:25:51+02:00", "kind": "clause",
          "user_id": "11883172", "counterparty": "404",
          "player_id": "1337", "amount": "1"}], users, names) == []
    assert ledger_from_api(
        [{"at": "2026-09-18T22:25:51+02:00", "kind": "clause",
          "user_id": "11883172", "player_id": "1337",
          "amount": "1"}], users, names) == [], "no counterparty, no row"


if __name__ == "__main__":                      # pragma: no cover
    if "--selftest" in sys.argv:
        _selftest()
        raise SystemExit(0)
    lg = League.load(with_market=bool(os.environ.get("FF_ROOT", "./data")))
    for m in lg:
        print("%-18s %2d players  spent %7.2fM  took %7.2fM  cash %8s  (%s)"
              % (m.handle, len(m.players), m.spend / 1e6, m.proceeds / 1e6,
                 m.cash.label(), m.cash.confidence))
    for w in lg.warnings:
        print("WARN " + w)
