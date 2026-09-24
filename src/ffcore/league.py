
from __future__ import annotations

import math
import configparser
import pathlib
import re
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import Path
from typing import NamedTuple

from ffcore import schema
from ffcore.parse import money
from ffcore.tidy import shown
from ffcore.text import norm
from ffcore.tidy import (load_crosswalk,
                         run_now,
                         Market, input_path, ledger_stamp,
                         load_api_activity, load_api_standings, load_api_teams,
                         load_api_team_history, load_market_frozen,
                         read_ledger, snapshot_stamp)

__all__ = ["MARKET", "Config", "load_config", "read_rosters", "identify",
           "read_api_balances", "owner_from_api", "owner_drift",
           "app_ids_known", "app_fielded", "flat_income", "bonus_income",
           "allowance",
           "replay", "Cash", "Manager", "League"]

MARKET = "market"

PLAUSIBLE = 2.0

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


def read_rosters(name: str = "rosters_initial.txt") -> dict[str, list[str]]:
    path = input_path(name)
    if not path.exists():
        raise SystemExit("missing %s" % path)
    rosters, current = {}, None
    with path.open(encoding="utf-8") as fh:
        for raw in fh:
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            if line.startswith("[") and line.endswith("]"):
                current = line[1:-1].strip()
                rosters.setdefault(current, [])
            elif current:
                m = _ROSTER_CLUB.match(line)
                if m:
                    line = "%s@%s" % (norm(m.group(1)), norm(m.group(2)))
                rosters[current].append(line)
    return rosters


_ROSTER_CLUB = re.compile(r"^(.*?)\s*\(([^)]+)\)\s*$")


def read_balances(name: str = "cash.txt") -> dict[str, tuple[float, str]]:
    path = input_path(name)
    if not path.exists():
        return {}
    out = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if money(parts[0]) is not None:
            out["__me__"] = (money(parts[0]),
                             parts[1] if len(parts) > 1 else "")
            continue
        nums = [i for i, p in enumerate(parts) if money(p) is not None]
        if not nums:
            continue
        i = nums[0]
        handle = " ".join(parts[:i]).strip()
        out[handle] = (money(parts[i]),
                       parts[i + 1] if len(parts) > i + 1 else "")
    return out


def read_api_balances(rows=None) -> dict[str, tuple[float, str]]:
    out: dict[str, tuple[float, str]] = {}
    for r in (load_api_standings() if rows is None else rows):
        handle = schema.text(r, schema.API_STANDINGS.MANAGER)
        raw = schema.text(r, schema.API_STANDINGS.TEAM_MONEY)
        if not handle or not raw:
            continue
        try:
            value = float(raw)
        except ValueError:
            continue
        out[handle] = (value, snapshot_stamp(r.get("observed_at") or ""))
    return out


def flat_income(observed, budget: float, bought: float, sold: float):
    if observed is None:
        return None
    return max(0.0, observed - (budget + sold - bought))


def bonus_income(activity: list[dict], users: dict) -> dict[str, float]:
    out: dict[str, float] = {}
    for r in activity:
        if r.get("kind") != "bonus":
            continue
        who = users.get(str(r.get("user_id") or ""))
        if not who:
            continue
        out[who] = out.get(who, 0.0) + (money(r.get("amount")) or 0.0)
    return out


def allowance(since, now, daily_bonus: float) -> tuple[float, float]:
    if since is None or now is None:
        return 0.0, 0.0
    days = max(0.0, (now - since).total_seconds() / 86400.0)
    return math.floor(days) * (daily_bonus or 0.0), days


def _app_ids_of(xw) -> dict:
    if xw is None:
        return {}
    return {p.app_id: p.player_id for p in xw.players.values() if p.app_id}


def app_ids_known() -> dict:
    return _app_ids_of(load_crosswalk())


def owner_from_api(rows: list[dict], market, ledger_owner: dict | None = None,
                   xw=None) -> tuple[dict, list]:
    from ffcore.crosswalk import Crosswalk
    from ffcore.tidy import latest_only
    out, unjoined = {}, []
    index = latest_only(market.rows) if market is not None else []
    resolve = (xw or Crosswalk()).resolve_api
    for r in rows:
        handle = schema.text(r, schema.API_TEAMS.MANAGER)
        raw = schema.text(r, schema.API_TEAMS.PLAYER_NAME)
        if not handle or not raw:
            continue
        key = resolve(raw, handle, market, ledger_owner, index,
                      r.get("market_value"), r.get("player_name_full") or "",
                      r.get("player_id") or "")
        if key:
            out[key] = handle
        else:
            unjoined.append(raw)
    return out, unjoined


def app_fielded(squad, names: dict, rows=None, ids=None) -> list[str]:
    from ffcore.tidy import load_api_lineup

    rows = load_api_lineup() if rows is None else rows
    ids = app_ids_known() if ids is None else ids
    squad = set(squad)
    by_name = {norm(names.get(k, k)): k for k in squad}
    out = []
    for r in rows or []:
        key = ids.get(schema.text(r, schema.API_LINEUP.PLAYER_ID))
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
        who = users.get(str(r.get("user_id") or ""))
        player = names.get(str(r.get("player_id") or ""))
        if not who or not player:
            continue
        if kind == "clause":
            # A CLAUSE IS THE ONLY MANAGER-TO-MANAGER MOVE. A buy comes from
            # the market and a sell goes to it; this one has a real payee, so
            # the money leaves one squad's balance and lands in another's.
            # Dropped entirely until 2026-09-18, which left the buyer looking
            # richer than he was by exactly what he had paid.
            victim = users.get(str(r.get("counterparty") or ""))
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
    """When the app first showed `pid` no longer with `manager`, or None.

    The first roster snapshot after the purchase AND after the last snapshot
    that did list him. It is the earliest moment we KNOW he was gone, so the
    removal happened at or before it -- and, for a player never in any
    snapshot (bought before the league API was first read), the first
    snapshot after the purchase. `history` is every roster snapshot.
    """
    last = max((r["observed_at"] for r in history
                if r["player_id"] == pid and r["manager"] == manager),
               default="")
    for s in sorted({r["observed_at"] for r in history}):
        t = snapshot_stamp(s)
        if s > last and t and bought and t > bought:
            return t
    return None


def owner_drift(ledger: dict, api: dict, names=None) -> list[str]:
    if not api:
        return []
    def _who(k):
        return (names or {}).get(k) or k

    out = []
    for key, held in sorted(ledger.items()):
        now = api.get(key)
        if now is None:
            out.append("**%s** — the ledger has him at %s; the app says "
                       "nobody in the league holds him. The feed has no sale "
                       "of him: the app drops players without publishing "
                       "one." % (_who(key), held))
        elif now != held:
            out.append("**%s** — the ledger has him at %s; the app says %s."
                       % (_who(key), held, now))
    for key, now in sorted(api.items()):
        if key not in ledger:
            out.append("**%s** — the app has him at %s; the ledger has no "
                       "record of him." % (_who(key), now))
    return out


def identify(t: dict, owner: dict, market=None, xw=None) -> tuple[str, str]:
    key = norm(t["player"])
    pid = schema.text(t, schema.TRANSACTIONS.PLAYER_ID)
    if pid and xw is not None:
        got = xw.player(app_id=pid)
        if got:
            return got, ""
    if market is None:
        return key, ""

    got, cands = market.candidates(t["player"])
    if got is not None:
        return (got, "" if got == key else "matched %s" % got)
    if not cands:
        return key, ""

    src = schema.text(t, schema.TRANSACTIONS.FROM_) or MARKET
    dst = schema.text(t, schema.TRANSACTIONS.TO_) or MARKET
    why = ""

    if src != MARKET:
        kept = [c for c in cands if owner.get(c) == src]
        if kept:
            cands, why = kept, "held by %s at the time" % src
    elif dst != MARKET:
        kept = [c for c in cands if c not in owner]
        if kept:
            cands, why = kept, "the only one nobody owned"

    price = money(t.get("price"))
    when = ledger_stamp(t.get("date", ""))
    if len(cands) > 1 and price and when:
        kept = []
        for c in cands:
            v = market.at(c, when)
            if v and v.value and 1 / PLAUSIBLE <= price / v.value <= PLAUSIBLE:
                kept.append(c)
        if kept:
            cands = kept
            why = ("price fits only his value" if len(kept) == 1
                   else why)

    if len(cands) != 1:
        return key, ""
    return cands[0], why


def _roster_key(raw: str, market, xw=None) -> str:
    stripped = raw.strip()
    if stripped.isdigit():
        return stripped
    name, _, club = raw.partition("@")
    if xw is not None:
        got = xw.resolve(name, hint_club=club, market=market)
        if got:
            return got
    elif market is not None:
        got = market.key_for(name, team=club)
        if got:
            return got
    return norm(raw)


def replay(rosters: dict[str, list[str]], txns: list[dict], market=None,
           xw=None):
    owner: dict[str, str] = {}
    for mgr, names in rosters.items():
        for n in names:
            owner[_roster_key(n, market, xw)] = mgr
    warnings: list[str] = []
    resolved: list[str] = []
    for t in txns:
        key, why = identify(t, owner, market, xw)
        if why:
            resolved.append("%s: %s → %s (%s)" % (
                t.get("date", "?"), t["player"], key, why))
        src = schema.text(t, schema.TRANSACTIONS.FROM_) or MARKET
        dst = schema.text(t, schema.TRANSACTIONS.TO_) or MARKET
        if src != MARKET and owner.get(key) not in (src, None):
            warnings.append("%s: %s was not owned by %s" % (
                t.get("date", "?"), t["player"], src))
        elif src != MARKET and owner.get(key) is None:
            warnings.append("%s: %s sold %s, but nobody was holding him — "
                            "missing a purchase, or a different spelling?"
                            % (t.get("date", "?"), src, t["player"]))
        if dst == MARKET:
            owner.pop(key, None)
        else:
            if src == MARKET and owner.get(key) is not None:
                warnings.append("%s: %s bought from market but already "
                                "owned by %s — missing a sale?"
                                % (t.get("date", "?"), t["player"],
                                   owner[key]))
            owner[key] = dst
    return owner, warnings, resolved


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

    @property
    def spend(self) -> float:
        return sum(money(t.get("price")) or 0 for t in self.buys)

    @property
    def proceeds(self) -> float:
        return sum(money(t.get("price")) or 0 for t in self.sales)

    @property
    def net(self) -> float:
        return self.proceeds - self.spend

    @property
    def max_bid(self) -> float | None:
        if self.cash.value is None:
            return None
        return max(0.0, self.cash.value)


class League:

    def __init__(self, cfg: Config, rosters, txns, market: Market | None,
                 api_teams=None, standings=None, xw=None,
                 roster_history=None):
        self.cfg = cfg
        self.rosters = rosters
        self.txns = txns
        self.market = market
        self.xw = xw
        self.owner, self.warnings, self.resolved = replay(rosters, txns,
                                                          market, xw)

        self.api_unjoined: list[str] = []
        self.dropped: dict[str, tuple] = {}
        self._api_teams = api_teams
        self._standings = standings
        if api_teams and market is None:
            api_teams = None
        if api_teams:
            api_owner, self.api_unjoined = owner_from_api(
                api_teams, market, ledger_owner=self.owner, xw=self.xw)
            if api_owner:
                self.warnings += owner_drift(
                    self.owner, api_owner,
                    {k: (v.get("name") or k)
                     for k, v in (market.latest().items()
                                  if market is not None else ())})
                for raw in self.api_unjoined:
                    self.warnings.append(
                        "**%s** — the app says he is owned, but no market row "
                        "matches the name, so he is missing from the board."
                        % raw)
                # The app drops a player from a squad WITHOUT publishing a sale;
                # the ledger, built from the feed, keeps him. {key: (who, when
                # the rosters first showed him gone)} of exactly those -- the
                # cash estimate prices them, AT THAT MOMENT, below.
                bought = {self.txn_key(t): (schema.text(
                    t, schema.TRANSACTIONS.PLAYER_ID),
                    ledger_stamp(t.get("date", ""))) for t in txns}
                for k, m in self.owner.items():
                    if k not in api_owner and k in bought:
                        pid, when = bought[k]
                        self.dropped[k] = (
                            m, gone_at(roster_history or [], pid, m, when))
                self.owner = api_owner

        self.managers: dict[str, Manager] = {
            h: Manager(h) for h in rosters
        }
        for key, mgr in self.owner.items():
            self.managers.setdefault(mgr, Manager(mgr)).players.append(key)
        for t in txns:
            src = schema.text(t, schema.TRANSACTIONS.FROM_) or MARKET
            dst = schema.text(t, schema.TRANSACTIONS.TO_) or MARKET
            if dst != MARKET:
                self.managers.setdefault(dst, Manager(dst)).buys.append(t)
            if src != MARKET:
                self.managers.setdefault(src, Manager(src)).sales.append(t)

        self._estimate_cash()


    @classmethod
    def load(cls, with_market: bool = True) -> "League":
        cfg = load_config()
        market = Market(load_market_frozen()) if with_market else None
        return cls(cfg, read_rosters(), read_ledger(), market,
                   api_teams=load_api_teams(),
                   roster_history=load_api_team_history(),
                   standings=load_api_standings(), xw=load_crosswalk())

    def txn_key(self, t: dict) -> str | None:
        pid = schema.text(t, schema.TRANSACTIONS.PLAYER_ID)
        if pid and self.xw is not None:
            got = self.xw.player(app_id=pid)
            if got:
                return got
        key, _why = identify(t, self.owner, self.market, self.xw)
        return key or None

    def __getitem__(self, handle: str) -> Manager:
        return self.managers[handle]

    def __iter__(self):
        return iter(sorted(self.managers.values(),
                           key=lambda m: (m.handle != self.cfg.me, m.handle)))


    def _estimate_cash(self) -> None:
        balances = read_balances()
        me_balance = balances.pop("__me__", None)
        if me_balance:
            balances.setdefault(self.cfg.me, me_balance)
        for handle, (value, when) in read_api_balances(
                self._standings).items():
            balances[handle] = (value, when)

        paid = None
        me_anchor = balances.get(self.cfg.me)
        if me_anchor and isinstance(me_anchor[1], datetime) and self.cfg.budget:
            b, sd = 0.0, 0.0
            for t in self.txns:
                price = money(t.get("price")) or 0.0
                if schema.text(t, schema.TRANSACTIONS.TO_) == self.cfg.me:
                    b += price
                if schema.text(t, schema.TRANSACTIONS.FROM_) == self.cfg.me:
                    sd += price
            paid = flat_income(me_anchor[0], self.cfg.budget, b, sd)

        users = {r.get("user_id"): r.get("manager")
                 for r in (self._standings if self._standings is not None
                          else load_api_standings())
                 if r.get("user_id") and r.get("manager")}
        own_bonus = bonus_income(load_api_activity(), users)

        click_rate, my_clicks, my_days = 0.0, 0, 0.0
        me_txns = [t for t in self.txns
                  if schema.text(t, schema.TRANSACTIONS.TO_) == self.cfg.me
                  or schema.text(t, schema.TRANSACTIONS.FROM_) == self.cfg.me]
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
                base, since_s = anchor
                if isinstance(since_s, datetime):
                    since, from_app = since_s, True
                else:
                    since = ledger_stamp(since_s) if since_s else None
                    from_app = False
                conf = "known"
                basis = ("balance the app reported at %s"
                         % shown(since)) if from_app \
                    else ("balance you recorded%s"
                          % (" on " + since_s if since_s else ""))
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
                src = schema.text(t, schema.TRANSACTIONS.FROM_) or MARKET
                dst = schema.text(t, schema.TRANSACTIONS.TO_) or MARKET
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
            mgr.cash = Cash(value, conf, math, _now(), base, bought, sold)


    def squad(self, handle: str) -> list[str]:
        return sorted(k for k, m in self.owner.items() if m == handle)


    def unmatched(self, known_keys) -> list[str]:
        return sorted(k for k in self.owner if k not in known_keys)


def _now() -> str:
    return shown()


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

    _selftest_cash()
    _selftest_identify()
    _selftest_api_owner()
    print("ffcore.league self-test OK (8 cases + cash + identify + api)")


def _selftest_api_owner() -> None:
    at = "2026-08-17T2246Z"
    players = Market([
        {"name": "Pablo Fornals", "value": "10000000", "observed_at": at,
         "position": "MED"},
        {"name": "Simeone", "value": "5000000", "observed_at": at,
         "position": "DEL"},
        {"name": "Carl Starfelt", "value": "9000000", "observed_at": at,
         "position": "DEF"}])
    rows = [{"manager": "miguel_autentico", "player_name": "Pablo Fornals"},
            {"manager": "BurtonGM89", "player_name": "Simeone"}]

    owner, unjoined = owner_from_api(rows, players)
    assert owner == {norm("Pablo Fornals"): "miguel_autentico",
                     norm("Simeone"): "BurtonGM89"}, owner
    assert unjoined == [], unjoined

    owner, unjoined = owner_from_api(
        rows + [{"manager": "SusoGattuso", "player_name": "Nobody At All"}],
        players)
    assert unjoined == ["Nobody At All"], unjoined
    assert len(owner) == 2, owner

    owner, _ = owner_from_api([{"manager": "", "player_name": "Simeone"},
                               {"manager": "x", "player_name": ""}], players)
    assert owner == {}, owner

    owner, unjoined = owner_from_api(
        [{"manager": "miguel_autentico", "player_name": "Fornals"}], players)
    assert owner == {norm("Pablo Fornals"): "miguel_autentico"}, owner
    assert unjoined == [], unjoined

    two = Market([
        {"name": "Fabio Cardoso", "value": "925408", "observed_at": at,
         "position": "DEF"},
        {"name": "Johnny Cardoso", "value": "6310000", "observed_at": at,
         "position": "MED"}])
    ambiguous_row = [{"manager": "Magic Mike 333", "player_name": "Cardoso"}]

    from ffcore.crosswalk import Crosswalk

    xw0 = Crosswalk()
    led = {norm("Fabio Cardoso"): "Magic Mike 333"}
    assert xw0.resolve_api("Cardoso", "Magic Mike 333", two, led) \
        == norm("Fabio Cardoso")
    assert xw0.resolve_api("Cardoso", "Magic Mike 333", two) is None
    assert xw0.resolve_api("Fabio Cardoso", "Magic Mike 333", two) \
        == norm("Fabio Cardoso")
    assert xw0.resolve_api("", "Magic Mike 333", two) is None

    assert xw0.resolve_api("Cardoso", "Magic Mike 333", two,
                          full="Fabio Cardoso") == norm("Fabio Cardoso")
    assert xw0.resolve_api("Fabio Cardoso", "Magic Mike 333", two,
                          full="Somebody Entirely Different") \
        == norm("Fabio Cardoso")
    assert xw0.resolve_api("Cardoso", "Magic Mike 333", two, full="") is None
    owner, unjoined = owner_from_api(
        [{"manager": "Magic Mike 333", "player_name": "Cardoso",
          "player_name_full": "Fabio Cardoso"}], two)
    assert owner == {norm("Fabio Cardoso"): "Magic Mike 333"}, owner
    assert unjoined == [], unjoined
    assert xw0.resolve_api("Cardoso", "Magic Mike 333", two,
                          full="Cardoso") is None

    owner, unjoined = owner_from_api(ambiguous_row, two)
    assert owner == {} and unjoined == ["Cardoso"], (owner, unjoined)

    led = {norm("Fabio Cardoso"): "Magic Mike 333"}
    owner, unjoined = owner_from_api(ambiguous_row, two, ledger_owner=led)
    assert owner == {norm("Fabio Cardoso"): "Magic Mike 333"}, owner
    assert unjoined == [], unjoined

    owner, unjoined = owner_from_api(
        ambiguous_row, two, ledger_owner={norm("Fabio Cardoso"): "BurtonGM89"})
    assert owner == {} and unjoined == ["Cardoso"], (owner, unjoined)

    owner, unjoined = owner_from_api(
        ambiguous_row, two,
        ledger_owner={norm("Fabio Cardoso"): "Magic Mike 333",
                      norm("Johnny Cardoso"): "Magic Mike 333"})
    assert owner == {} and unjoined == ["Cardoso"], (owner, unjoined)

    twins = Market([
        {"name": "Carlos Romero", "value": "43240000", "observed_at": at,
         "position": "DEF"},
        {"name": "Isaac Romero", "value": "6150000", "observed_at": at,
         "position": "DEL"}])
    assert xw0.resolve_api("C. Romero", "BurtonGM89", twins,
                          market_value="43244323",
                          full="Carlos Romero") == norm("Carlos Romero")
    assert xw0.resolve_api("Isaac Romero", "BurtonGM89", twins) \
        == norm("Isaac Romero")
    assert xw0.resolve_api("Isaac Romero", "BurtonGM89", twins,
                          market_value="6150000") == norm("Isaac Romero")
    assert xw0.resolve_api("Isaac Romero", "BurtonGM89", twins,
                          market_value="43244323") == norm("Isaac Romero")
    assert xw0.resolve_api("Isaac Romero", "BurtonGM89", twins,
                          market_value="6160000") == norm("Isaac Romero")
    assert xw0.resolve_api("C. Romero", "BurtonGM89", twins,
                          market_value="43244323") == norm("Carlos Romero")

    lone = Market([{"name": "Jonny Castro", "value": "5602302",
                    "observed_at": at, "position": "DEF"}])
    from ffcore.crosswalk import Player

    def _xw_of(app_id, key):
        return Crosswalk({key: Player(player_id=key, app_id=app_id)})

    assert xw0.resolve_api("Jonny Otto", "SusoGattuso", lone) is None
    assert _xw_of("2552", norm("Jonny Castro")).resolve_api(
        "Jonny Otto", "SusoGattuso", lone,
        app_id="2552") == norm("Jonny Castro")
    assert _xw_of("9999", "somebody").resolve_api(
        "Jonny Otto", "SusoGattuso", lone, app_id="2552") is None
    assert xw0.resolve_api("Fornals", "miguel_autentico", players) \
        == norm("Pablo Fornals")
    assert _xw_of("2552", "someone else").resolve_api(
        "Jonny Castro", "SusoGattuso", lone,
        app_id="2552") == "someone else"
    owner, unjoined = owner_from_api(
        [{"manager": "SusoGattuso", "player_name": "Jonny Otto",
          "player_id": "2552"}], lone, xw=_xw_of("2552", norm("Jonny Castro")))
    assert owner == {norm("Jonny Castro"): "SusoGattuso"}, owner
    assert unjoined == [], unjoined

    priced = Market([
        {"name": "Jonny Castro", "value": "5602302", "observed_at": at,
         "position": "DEF"},
        {"name": "Someone Else", "value": "9999999", "observed_at": at,
         "position": "DEF"}])
    owner, unjoined = owner_from_api(
        [{"manager": "SusoGattuso", "player_name": "Jonny Otto",
          "market_value": "5602302"}], priced)
    assert owner == {norm("Jonny Castro"): "SusoGattuso"}, owner
    assert unjoined == [], unjoined

    owner, unjoined = owner_from_api(
        [{"manager": "SusoGattuso", "player_name": "Jonny Otto",
          "market_value": "5602303"}], priced)
    assert owner == {} and unjoined == ["Jonny Otto"], (owner, unjoined)

    aged = Market([
        {"name": "Alvaro Fernandez", "value": "4486912",
         "observed_at": "2026-08-17T2246Z", "position": "POR"},
        {"name": "Alvaro Fernandez", "value": "4499000",
         "observed_at": "2026-08-17T2318Z", "position": "POR"},
        {"name": "Other Keeper", "value": "3000000",
         "observed_at": "2026-08-17T2318Z", "position": "POR"}])
    owner, unjoined = owner_from_api(
        [{"manager": "me", "player_name": "A. Ferllo",
          "market_value": "4486912"}], aged)
    assert owner == {norm("Alvaro Fernandez"): "me"}, owner
    assert unjoined == [], unjoined

    shared = Market([
        {"name": "One", "value": "500", "observed_at": "2026-08-16T0000Z",
         "position": "DEF"},
        {"name": "Two", "value": "500", "observed_at": "2026-08-17T0000Z",
         "position": "DEF"}])
    owner, unjoined = owner_from_api(
        [{"manager": "me", "player_name": "Nobody", "market_value": "500"}],
        shared)
    assert owner == {} and unjoined == ["Nobody"], (owner, unjoined)

    repeated = Market([
        {"name": "One", "value": "500", "observed_at": "2026-08-16T0000Z",
         "position": "DEF"},
        {"name": "One", "value": "500", "observed_at": "2026-08-17T0000Z",
         "position": "DEF"}])
    owner, _ = owner_from_api(
        [{"manager": "me", "player_name": "Nobody", "market_value": "500"}],
        repeated)
    assert owner == {norm("One"): "me"}, owner

    twinned = Market([
        {"name": "A One", "value": "500", "observed_at": at,
         "position": "DEF"},
        {"name": "B Two", "value": "500", "observed_at": at,
         "position": "DEF"}])
    owner, unjoined = owner_from_api(
        [{"manager": "x", "player_name": "Unknown", "market_value": "500"}],
        twinned)
    assert owner == {} and unjoined == ["Unknown"], (owner, unjoined)

    owner, _ = owner_from_api(
        [{"manager": "x", "player_name": "Jonny Castro",
          "market_value": "9999999"}], priced)
    assert owner == {norm("Jonny Castro"): "x"}, owner

    _selftest_derived_ledger()


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

    # A CLAUSE MOVES A PLAYER BETWEEN TWO MANAGERS, and the money with him.
    # This is the shape the app publishes as a "Market operation" and the one
    # that was dropped for want of a name: Albert Laporta took Raphinha from
    # Magic Mike 333 for 141,425,721 on 2026-09-18, and because no row
    # reached the ledger he still looked like he had the money.
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

    # An unknown counterparty is dropped rather than booked against the
    # market -- crediting the wrong side is worse than crediting nobody.
    assert ledger_from_api(
        [{"at": "2026-09-18T22:25:51+02:00", "kind": "clause",
          "user_id": "11883172", "counterparty": "404",
          "player_id": "1337", "amount": "1"}], users, names) == []
    assert ledger_from_api(
        [{"at": "2026-09-18T22:25:51+02:00", "kind": "clause",
          "user_id": "11883172", "player_id": "1337",
          "amount": "1"}], users, names) == [], "no counterparty, no row"

    _selftest_anchor_is_current()


def _selftest_anchor_is_current() -> None:
    at = "2026-08-17T2318Z"
    mkt = Market([{"name": "P", "value": "1000000", "observed_at": at,
                   "position": "MED"}])
    txns = [{"date": "2026-08-17T22:24", "player": "P", "from": "market",
             "to": "me", "price": "10000000", "note": ""}]
    api_teams = [{"manager": "me", "player_name": "P", "observed_at": at}]
    table = [{"manager": "me", "user_id": "1", "team_id": "1",
              "team_money": "23596582", "observed_at": at}]

    lg = League(Config(me="me"), {"me": []}, txns, mkt, api_teams=api_teams,
                standings=table)
    got = lg["me"].cash.value
    assert got == 23596582.0, (
        "the app's balance is current; a deal it already includes was "
        "subtracted again — got %r" % got)
    assert lg["me"].cash.confidence == "known", lg["me"].cash

    later = txns + [{"date": "2026-08-18T09:00", "player": "P",
                     "from": "market", "to": "me", "price": "1000000",
                     "note": ""}]
    lg2 = League(Config(me="me"), {"me": []}, later, mkt,
                 api_teams=api_teams, standings=table)
    assert lg2["me"].cash.value == 23596582.0 - 1000000.0, lg2["me"].cash

    api_odd = [{"manager": "me", "player_name": "A. Ferllo", "observed_at": at}]
    lg3 = League(Config(me="me"), {"me": ["P"]}, [], None,
                 api_teams=api_odd, standings=table)
    assert norm("a ferllo") not in lg3.owner, lg3.owner
    assert lg3.owner.get(norm("P")) == "me", lg3.owner
    assert lg3["me"].cash.value == 23596582.0, lg3["me"].cash

    ledger = {norm("Pablo Fornals"): "miguel_autentico",
              norm("Simeone"): "SusoGattuso",
              norm("Carl Starfelt"): "miguel_autentico"}
    api = {norm("Pablo Fornals"): "miguel_autentico",
           norm("Simeone"): "BurtonGM89"}
    drift = owner_drift(ledger, api)
    assert any("simeone" in d.lower() and "SusoGattuso" in d
               and "BurtonGM89" in d for d in drift), drift
    assert any("starfelt" in d.lower() and "nobody" in d for d in drift), drift
    assert len(drift) == 2, drift
    assert owner_drift(api, api) == []
    assert owner_drift(ledger, {}) == []

    mkt = Market([{"name": "Pablo Fornals", "value": "10000000",
                   "observed_at": "2026-08-17T2246Z", "position": "MED"},
                  {"name": "Simeone", "value": "5000000",
                   "observed_at": "2026-08-17T2246Z", "position": "DEL"}])
    rosters = {"miguel_autentico": ["Pablo Fornals", "Simeone"],
               "BurtonGM89": []}
    api_rows = [{"manager": "miguel_autentico", "player_name": "Pablo Fornals"},
                {"manager": "BurtonGM89", "player_name": "Simeone"}]

    plain = League(Config(me="miguel_autentico"), rosters, [], mkt)
    assert plain.owner[norm("Simeone")] == "miguel_autentico"

    lg2 = League(Config(me="miguel_autentico"), rosters, [], mkt,
                 api_teams=api_rows)
    assert lg2.owner[norm("Simeone")] == "BurtonGM89", lg2.owner
    assert lg2["BurtonGM89"].players == [norm("Simeone")], lg2["BurtonGM89"]
    assert any("simeone" in w.lower() for w in lg2.warnings), lg2.warnings

    lg3 = League(Config(me="miguel_autentico"), rosters, [], mkt, api_teams=[])
    assert lg3.owner[norm("Simeone")] == "miguel_autentico", lg3.owner

    # -- the app drops a player WITHOUT a feed sale: the ledger keeps him, and
    # the cash estimate prices him at the value he had WHEN HE LEFT, not now --
    hist = lambda n, *pts: [{"name": n, "value": v, "observed_at": at,
                             "position": "DEL"} for at, v in pts]
    mkt2 = Market(hist("Ghost", ("2026-08-17T2246Z", "8000000"),
                       ("2026-08-25T0600Z", "3000000"))
                  + hist("Seen", ("2026-08-17T2246Z", "9000000"),
                         ("2026-08-19T0600Z", "6000000"),
                         ("2026-08-25T0600Z", "2000000"))
                  + hist("Kept", ("2026-08-17T2246Z", "1000000")))
    tx = [{"date": "2026-08-12T22:24", "player": n, "player_id": i,
           "from": MARKET, "to": "rival", "price": pr}
          for n, i, pr in (("Ghost", "66", "20000000"),
                           ("Seen", "77", "10000000"),
                           ("Kept", "5", "1000000"))]
    rows = lambda snap, *pids: [{"observed_at": snap, "manager": "rival",
                                 "player_id": p} for p in pids]
    lg4 = League(Config(me="miguel_autentico", budget=100e6),
                 {"miguel_autentico": [], "rival": []}, tx, mkt2,
                 api_teams=[{"manager": "rival", "player_name": "Kept"}],
                 roster_history=rows("2026-08-17T2246Z", "5", "77")
                 + rows("2026-08-18T0600Z", "5", "77")
                 + rows("2026-08-19T0600Z", "5"))
    # Ghost was never in a roster: gone by the FIRST snapshot after his
    # purchase. Seen was listed twice: gone by the first snapshot without him.
    assert {k: v[1].strftime("%m-%dT%H%M") for k, v in lg4.dropped.items()} \
        == {"ghost": "08-17T2246", "seen": "08-19T0600"}, lg4.dropped
    c4 = lg4["rival"].cash
    # 100 - (20+10+1) + 8 (Ghost when gone, NOT the 3 he is worth now)
    #                 + 6 (Seen when gone, NOT the 2 he is worth now)
    assert c4.value == 83e6, (c4.value, c4.basis)
    assert "8.00M assuming the app paid Ghost's market value when he left" \
        in c4.basis and "gone by" in c4.basis, c4.basis
    assert gone_at([], "1", "m", None) is None      # no history: cannot say
    # Nothing dropped, nothing assumed -- the ordinary case adds no term.
    assert not lg2.dropped and "assuming the app paid" not in \
        lg2["BurtonGM89"].cash.basis, lg2.dropped


def _selftest_cash() -> None:
    cfg = Config(me="nobody", budget=100e6)
    rosters = {"me": ["a"], "rich": ["b"], "spent": ["c"]}
    txns = [
        {"date": "2026-08-11T21:24", "player": "x", "from": MARKET,
         "to": "rich", "price": "10000000"},
        {"date": "2026-08-11T21:42", "player": "b", "from": "rich",
         "to": MARKET, "price": "4000000"},
        {"date": "2026-08-12T21:24", "player": "y", "from": MARKET,
         "to": "spent", "price": "124560000"},
    ]
    lg = League(cfg, rosters, txns, None)

    assert lg["rich"].cash.value == 94e6, lg["rich"].cash.value
    assert lg["me"].cash.value == 100e6, lg["me"].cash.value

    assert lg["me"].cash.confidence == "estimated", lg["me"].cash.confidence

    assert "10.00M bought" in lg["rich"].cash.basis, lg["rich"].cash.basis
    assert "4.00M sold" in lg["rich"].cash.basis
    assert "= 94.00M" in lg["rich"].cash.basis

    over = lg["spent"]
    assert over.cash.value == 100e6 - 124.56e6, over.cash.value
    assert over.cash.confidence == "estimated", over.cash.confidence
    assert over.cash.overdrawn and not lg["rich"].cash.overdrawn
    assert "= -24.56M" in over.cash.basis, over.cash.basis
    assert over.max_bid == 0.0, over.max_bid
    assert any("overdrawn" in w for w in lg.warnings), lg.warnings
    assert not any("exceeds" in w for w in lg.warnings), lg.warnings

    now = datetime(2026, 8, 19, 12, 0, tzinfo=dt_timezone.utc)
    four_days = datetime(2026, 8, 15, 12, 0, tzinfo=dt_timezone.utc)
    assert allowance(four_days, now, 100000) == (400000.0, 4.0)
    assert allowance(now, now, 100000) == (0.0, 0.0)
    assert allowance(four_days, now, 0) == (0.0, 4.0)
    assert allowance(four_days - timedelta(hours=12), now, 100000) == (400000.0, 4.5)
    assert allowance(now, four_days, 100000) == (0.0, 0.0)
    assert allowance(None, now, 100000) == (0.0, 0.0)

    import tempfile as _tf
    with _tf.TemporaryDirectory() as d:
        f = pathlib.Path(d) / "r.txt"
        f.write_text("[me]\nalvaro garcia (Rayo)\npepelu\n", encoding="utf-8")
        got = read_rosters(f)["me"]
    assert got == ["alvaro garcia@rayo", "pepelu"], got

    assert flat_income(8906184.0, 100e6, 142.18e6, 49.82e6) == 8906184.0 - (
        100e6 - 142.18e6 + 49.82e6)
    assert round(flat_income(8906184.0, 100e6, 142.18e6, 49.82e6)) == 1266184
    assert flat_income(1.0, 100e6, 0.0, 0.0) == 0.0
    assert flat_income(None, 100e6, 0.0, 0.0) is None

    blind = League(Config(me="nobody", budget=0.0), {"z": ["q"]}, [], None)
    assert blind["z"].cash.value is None and blind["z"].max_bid is None
    assert not blind["z"].cash.overdrawn


def _selftest_identify() -> None:
    from ffcore.tidy import Market as _RealMarket
    _seen = "2026-08-01T0000Z"
    mkt = _RealMarket([
        {"name": "Fabio Cardoso", "team": "Alaves", "value": "925408",
         "observed_at": _seen},
        {"name": "Johnny Cardoso", "team": "Betis", "value": "6306919",
         "observed_at": _seen},
        {"name": "Dani Lorenzo", "team": "Betis", "value": "4000000",
         "observed_at": _seen},
        {"name": "Dani Martinez", "team": "Levante", "value": "500000",
         "observed_at": _seen}])
    owner = {"dani lorenzo": "alice", "johnny cardoso": "bob"}

    key, why = identify({"player": "Dani", "from": "alice", "to": MARKET},
                        owner, mkt)
    assert key == "dani lorenzo", (key, why)
    assert "alice" in why, why

    key, why = identify({"player": "Dani", "from": "bob", "to": MARKET},
                        owner, mkt)
    assert key == "dani" and why == "", (key, why)

    key, why = identify({"player": "Cardoso", "from": MARKET, "to": "alice",
                         "price": "949269", "date": "2026-08-11T21:24"},
                        owner, mkt)
    assert key == "fabio cardoso", (key, why)

    from ffcore.tidy import Market as _RealMarket
    _at = "2026-08-19T1639Z"
    twins2 = _RealMarket([
        {"ff_id": "867", "name": "Álvaro García", "team": "Rayo",
         "value": "20233300", "observed_at": _at},
        {"ff_id": "12993", "name": "Álvaro García", "team": "Villarreal",
         "value": "501929", "observed_at": _at}])
    owner2 = {"867": "alice"}
    key, why = identify({"player": "Álvaro García", "from": "alice",
                         "to": MARKET}, owner2, twins2)
    assert key == "867", (key, why)
    assert "alice" in why, why
    key, why = identify({"player": "Álvaro García", "from": "bob",
                         "to": MARKET}, owner2, twins2)
    assert key == norm("Álvaro García") and why == "", (key, why)
    assert sorted(twins2.latest()) == ["12993", "867"]

    key, why = identify({"player": "Cardoso", "from": MARKET, "to": "alice",
                         "price": "949269", "date": "2026-08-11T21:24"},
                        {}, mkt)
    assert key == "fabio cardoso", (key, why)
    assert "price" in why, why

    key, why = identify({"player": "Cardoso", "from": MARKET, "to": "alice",
                         "price": "3000000", "date": "2026-08-11T21:24"},
                        {}, mkt)
    assert key == "cardoso" and why == "", (key, why)

    key, why = identify({"player": "Johnny Cardoso", "from": "bob",
                         "to": MARKET}, owner, mkt)
    assert key == "johnny cardoso" and why == "", (key, why)

    key, why = identify({"player": "Nobody", "from": MARKET, "to": "alice"},
                        {}, None)
    assert key == "nobody" and why == "", (key, why)

    own2, warns, notes = replay({"alice": ["Dani Lorenzo"]},
                                [{"date": "2026-08-13T21:25", "player": "Dani",
                                  "from": "alice", "to": MARKET,
                                  "price": "3800000"}], mkt)
    assert own2 == {}, own2
    assert notes and "dani lorenzo" in notes[0], notes
    assert not warns, warns

    id_mkt = _RealMarket([
        {"name": "Raul Moro", "ff_id": "7870", "team": "Racing",
         "value": "2000000", "observed_at": _seen},
        {"name": "Alvaro Garcia", "ff_id": "12993", "team": "Villarreal",
         "value": "500000", "observed_at": _seen},
        {"name": "Alvaro Garcia", "ff_id": "867", "team": "Rayo",
         "value": "19000000", "observed_at": _seen}])
    id_owner = {}
    for mgr, names in {"laporta": ["Raul Moro"]}.items():
        for n in names:
            id_owner[_roster_key(n, id_mkt)] = mgr
    assert id_owner == {"7870": "laporta"}, id_owner
    assert _roster_key("alvaro garcia@rayo", id_mkt) == "867"
    assert _roster_key("alvaro garcia@villarreal", id_mkt) == "12993"
    assert _roster_key("Raul Moro", None) == "raul moro"
    assert _roster_key("7870", id_mkt) == "7870"
    assert _roster_key("7870", None) == "7870"
    assert _roster_key(" 7870 ", id_mkt) == "7870"
    assert _roster_key("Nobody At All", id_mkt) == "nobody at all"

    from ffcore.crosswalk import Crosswalk, Player

    xw_fern = Crosswalk({"16003": Player("16003", "Manu Fernandez",
                                         app_names={"Manuel Fernández"})}, {})
    empty_mkt = _RealMarket([{"name": "Manu Fernandez", "ff_id": "16003",
                              "team": "Celta", "value": "500000",
                              "observed_at": _seen}])
    assert _roster_key("Manuel Fernández", empty_mkt, xw_fern) == "16003"
    assert _roster_key("Manu Fernandez", empty_mkt, xw_fern) == "16003"
    assert _roster_key("Total Stranger", empty_mkt, xw_fern) == "total stranger"

    _, warns2, _ = replay({"alice": ["Dani Lorenzo"]},
                          [{"date": "2026-08-13T21:25", "player": "Xabi",
                            "from": "alice", "to": MARKET}], mkt)
    assert any("nobody was holding" in w for w in warns2), warns2

    old = [{"date": "2026-01-01T12:00", "player": "P", "from": MARKET,
            "to": "rival", "price": "10000000"}]
    plain = League(Config(me="me", budget=100e6), {"rival": []}, old, None)
    rich = League(Config(me="me", budget=100e6, daily_bonus=100000.0),
                  {"rival": []}, old, None)
    assert rich["rival"].cash.value > plain["rival"].cash.value, (
        rich["rival"].cash.value, plain["rival"].cash.value)
    assert "daily allowance" in rich["rival"].cash.basis, rich["rival"].cash
    def _mine(at, money="23596582"):
        return [{"manager": "me", "user_id": "1", "team_id": "1",
                 "team_money": money, "observed_at": at}]

    def _seen(at):
        return League(Config(me="me", daily_bonus=100000.0), {"me": []}, [],
                      Market([{"name": "P", "value": "1000000",
                               "observed_at": at, "position": "MED"}]),
                      api_teams=[{"manager": "me", "player_name": "P",
                                  "observed_at": at}],
                      standings=_mine(at))

    fresh_at = datetime.now(dt_timezone.utc).strftime("%Y-%m-%dT%H%MZ")
    assert abs(_seen(fresh_at)["me"].cash.value - 23596582.0) < 5000, \
        _seen(fresh_at)["me"].cash
    stale_at = (datetime.now(dt_timezone.utc)
                - timedelta(days=4)).strftime("%Y-%m-%dT%H%MZ")
    aged = _seen(stale_at)["me"].cash
    assert abs(aged.value - (23596582.0 + 400000.0)) < 5000, aged
    assert "4 days" in aged.basis, aged.basis


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
