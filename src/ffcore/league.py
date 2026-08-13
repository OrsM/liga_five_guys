"""
ffcore.league — who owns whom, what it cost them, and what they can still spend.

Lifted out of squads.py (read_initial / apply_transactions) and extended with
the thing the app will not show you: an estimate of each rival's remaining
cash. That number is the single biggest piece of leverage available, because
it is a hard ceiling on what they can bid tomorrow.

    from ffcore.league import League
    lg = League.load()
    lg.owner[norm("raphinha")]        -> "Magic Mike 333"
    lg["Magic Mike 333"].cash.value   -> ~12.7M, with a confidence label

HOW CASH IS ESTIMATED, and why it is a range and not a number.

The app publishes no balances, so this reconstructs them:

    cash = anchor_balance - buys_since_anchor + sales_since_anchor

An anchor is a balance you actually observed, in inputs/cash.txt. For
yourself you always have one. For rivals you usually don't, so the fallback
anchor is `budget - (market value of their initial roster at the first
snapshot)`, which assumes they paid roughly value for their starting squad.
That assumption is wrong in both directions — draft prices aren't market
values — so those estimates are labelled "estimated" and every consumer is
expected to show the label, not just the number.

Confidence is one of:
    known      anchored on a balance you saw, plus exact ledger arithmetic
    estimated  anchored on budget minus initial roster value
    unknown    no budget configured, or no ledger coverage — value is None

Treat "estimated" as a ceiling rather than a balance: it ignores whatever
income the app has paid out and any deal that never appeared in the feed.
"""

from __future__ import annotations

import configparser
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

from ffcore.parse import money
from ffcore.text import norm
from ffcore.tidy import (Market, input_path, ledger_stamp, load_market,
                         read_ledger, snapshot_stamp)

__all__ = ["MARKET", "Config", "load_config", "read_rosters", "replay",
           "Cash", "Manager", "League"]

# Reserved counterparty in the ledger: the free-agent pool.
MARKET = "market"

DEFAULTS = {
    "me": "miguel_autentico",
    "budget": "100000000",
    "min_start": "60",
    "top_n_per_pos": "8",
    "start_cross": "70",
    "keeper_start": "80",
    "riser_pct": "2",
    "shrink_k": "8",
}


@dataclass
class Config:
    """inputs/league.ini, with defaults for everything.

    Thresholds used to be constants scattered across squads.py, watch.py and
    report.py, several of them stale after the league grew from three
    managers to five. One file now, so adding a sixth manager touches no code.
    """
    me: str = DEFAULTS["me"]
    budget: float = float(DEFAULTS["budget"])
    budgets: dict = field(default_factory=dict)   # per-manager override
    min_start: float = 60.0
    top_n_per_pos: int = 8
    start_cross: float = 70.0
    keeper_start: float = 80.0
    riser_pct: float = 2.0
    shrink_k: float = 8.0


def load_config(name: str = "league.ini") -> Config:
    path = input_path(name)
    # Trailing comments are stripped: every value here is coerced to a number,
    # and read_rosters/read_balances already accept '#' on the same line.
    cp = configparser.ConfigParser(inline_comment_prefixes=(";", "#"))
    cp.optionxform = str          # manager handles are case-sensitive
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
        top_n_per_pos=int(get("thresholds", "top_n_per_pos",
                              DEFAULTS["top_n_per_pos"])),
        start_cross=float(get("thresholds", "start_cross",
                              DEFAULTS["start_cross"])),
        keeper_start=float(get("thresholds", "keeper_start",
                               DEFAULTS["keeper_start"])),
        riser_pct=float(get("thresholds", "riser_pct", DEFAULTS["riser_pct"])),
        shrink_k=float(get("thresholds", "shrink_k", DEFAULTS["shrink_k"])),
    )
    if cp.has_section("budget"):
        for handle, amount in cp.items("budget"):
            v = money(amount)
            if v is not None:
                cfg.budgets[handle] = v
    return cfg


def read_rosters(name: str = "rosters_initial.txt") -> dict[str, list[str]]:
    """[manager] sections, one player name per line, # for comments."""
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
                rosters[current].append(line)
    return rosters


def read_balances(name: str = "cash.txt") -> dict[str, tuple[float, str]]:
    """{manager: (balance, date)} from inputs/cash.txt.

    Two accepted line shapes, so report.py's existing one-balance file keeps
    working untouched:

        12500000  2026-08-11                 -> belongs to config.me
        BurtonGM89  8000000  2026-08-12      -> a rival let something slip

    Record a rival's balance whenever you see one. One observed number turns
    their whole cash estimate from "estimated" to "known".
    """
    path = input_path(name)
    if not path.exists():
        return {}
    out = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if money(parts[0]) is not None:          # bare balance -> me
            out["__me__"] = (money(parts[0]),
                             parts[1] if len(parts) > 1 else "")
            continue
        # handle may contain spaces: the balance is the last numeric token
        nums = [i for i, p in enumerate(parts) if money(p) is not None]
        if not nums:
            continue
        i = nums[0]
        handle = " ".join(parts[:i]).strip()
        out[handle] = (money(parts[i]),
                       parts[i + 1] if len(parts) > i + 1 else "")
    return out


def replay(rosters: dict[str, list[str]], txns: list[dict]):
    """initial rosters + full ledger = current ownership.

    Every transaction is replayed, oldest first. If a row contradicts the
    state it lands on, that is a gap in the ledger and gets warned about
    rather than silently absorbed.
    """
    owner: dict[str, str] = {}
    for mgr, names in rosters.items():
        for n in names:
            owner[norm(n)] = mgr
    warnings: list[str] = []
    for t in txns:
        key = norm(t["player"])
        src = (t.get("from") or "").strip() or MARKET
        dst = (t.get("to") or "").strip() or MARKET
        if src != MARKET and owner.get(key) not in (src, None):
            warnings.append("%s: %s was not owned by %s" % (
                t.get("date", "?"), t["player"], src))
        if dst == MARKET:
            owner.pop(key, None)
        else:
            if src == MARKET and owner.get(key) is not None:
                warnings.append("%s: %s bought from market but already "
                                "owned by %s — missing a sale?"
                                % (t.get("date", "?"), t["player"],
                                   owner[key]))
            owner[key] = dst
    return owner, warnings


class Cash(NamedTuple):
    value: float | None
    confidence: str           # known | estimated | unknown
    basis: str                # one line of provenance, for the report
    as_of: str

    def label(self) -> str:
        if self.value is None:
            return "—"
        v = ("%.2fM" % (self.value / 1e6) if abs(self.value) >= 1e6
             else "%.0fK" % (self.value / 1e3))
        return v if self.confidence == "known" else "~" + v


@dataclass
class Manager:
    handle: str
    players: list = field(default_factory=list)     # normalised keys
    buys: list = field(default_factory=list)        # ledger rows
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
        """Hard ceiling on their next bid. None when cash is unknown."""
        if self.cash.value is None:
            return None
        return max(0.0, self.cash.value)


class League:
    """Current state of the league, assembled from the three input files."""

    def __init__(self, cfg: Config, rosters, txns, market: Market | None):
        self.cfg = cfg
        self.rosters = rosters
        self.txns = txns
        self.market = market
        self.owner, self.warnings = replay(rosters, txns)

        self.managers: dict[str, Manager] = {
            h: Manager(h) for h in rosters
        }
        for key, mgr in self.owner.items():
            self.managers.setdefault(mgr, Manager(mgr)).players.append(key)
        for t in txns:
            src = (t.get("from") or "").strip() or MARKET
            dst = (t.get("to") or "").strip() or MARKET
            if dst != MARKET:
                self.managers.setdefault(dst, Manager(dst)).buys.append(t)
            if src != MARKET:
                self.managers.setdefault(src, Manager(src)).sales.append(t)

        self._estimate_cash()

    # -- construction --------------------------------------------------

    @classmethod
    def load(cls, with_market: bool = True) -> "League":
        cfg = load_config()
        market = Market(load_market()) if with_market else None
        return cls(cfg, read_rosters(), read_ledger(), market)

    def __getitem__(self, handle: str) -> Manager:
        return self.managers[handle]

    def __iter__(self):
        """Managers, you first, then alphabetical."""
        return iter(sorted(self.managers.values(),
                           key=lambda m: (m.handle != self.cfg.me, m.handle)))

    # -- cash ----------------------------------------------------------

    def _initial_value(self, handle: str):
        """Market value of a manager's starting roster at the first snapshot.

        None if the market data doesn't cover enough of it — better to admit
        no estimate than to anchor on a squad we only priced half of.
        """
        if not self.market or not len(self.market):
            return None
        names = self.rosters.get(handle) or []
        if not names:
            return None
        first = min((snapshot_stamp(r.get("observed_at", ""))
                     for r in self.market.rows
                     if snapshot_stamp(r.get("observed_at", ""))), default=None)
        if first is None:
            return None
        vals = [self.market.at(n, first) for n in names]
        got = [v.value for v in vals if v]
        if len(got) < max(1, int(0.8 * len(names))):
            return None
        return sum(got)

    def _estimate_cash(self) -> None:
        balances = read_balances()
        me_balance = balances.pop("__me__", None)
        if me_balance:
            balances.setdefault(self.cfg.me, me_balance)

        for handle, mgr in self.managers.items():
            budget = self.cfg.budgets.get(handle, self.cfg.budget)
            anchor = balances.get(handle)

            if anchor:
                base, since_s = anchor
                since = ledger_stamp(since_s) if since_s else None
                conf = "known"
                basis = "balance you recorded%s" % (
                    " on " + since_s if since_s else "")
            else:
                initial = self._initial_value(handle)
                if not budget or initial is None:
                    mgr.cash = Cash(None, "unknown",
                                    "no balance recorded and initial squad "
                                    "not priceable", "")
                    continue
                base, since, conf = budget - initial, None, "estimated"
                basis = ("%.0fM budget less %.1fM of starting squad at the "
                         "first snapshot" % (budget / 1e6, initial / 1e6))

            delta, counted = 0.0, 0
            for t in self.txns:
                when = ledger_stamp(t.get("date", ""))
                if since and when and when <= since:
                    continue
                price = money(t.get("price")) or 0.0
                src = (t.get("from") or "").strip() or MARKET
                dst = (t.get("to") or "").strip() or MARKET
                if dst == handle:
                    delta -= price
                    counted += 1
                if src == handle:
                    delta += price
                    counted += 1

            value = base + delta
            mgr.cash = Cash(value, conf,
                            "%s, then %d ledger row(s)" % (basis, counted),
                            _now())
            if value < 0:
                # Not possible in the app, so one of the inputs is wrong.
                # Most often the anchor: a date with no time is read as
                # midnight, so a balance you actually checked AFTER the
                # 21:24 market resolution gets the same day's purchases
                # subtracted from it a second time.
                self.warnings.append(
                    "%s: cash estimate is negative (%.2fM) — %s. Check the "
                    "anchor time in cash.txt, or a missing sale in the "
                    "ledger." % (handle, value / 1e6, basis))

    # -- views ---------------------------------------------------------

    def squad(self, handle: str) -> list[str]:
        return sorted(k for k, m in self.owner.items() if m == handle)

    def unowned(self, keys) -> list[str]:
        """Which of these player keys nobody in the league holds."""
        return [k for k in keys if k not in self.owner]

    def unmatched(self, known_keys) -> list[str]:
        """Owned names absent from data/tidy — spelling to fix with
        find_slug.py. These are also why a naive free-agent count can go
        negative."""
        return sorted(k for k in self.owner if k not in known_keys)


def _now() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")


def _selftest() -> None:
    """Config parsing. Every value in league.ini arrives as a string and gets
    coerced, so a comment style the parser doesn't strip is a crash, not a
    bad default."""
    import tempfile

    ini = """
[league]
me = someone
budget = 100.000.000

[thresholds]
min_start     = 60    ; watchlist floor
top_n_per_pos = 8     # rows per position
start_cross   = 70
"""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "league.ini"
        p.write_text(ini, encoding="utf-8")
        cfg = load_config(str(p))

    assert cfg.me == "someone", cfg.me
    assert cfg.budget == 100_000_000, cfg.budget
    assert cfg.min_start == 60.0, cfg.min_start        # ';' inline comment
    assert cfg.top_n_per_pos == 8, cfg.top_n_per_pos   # '#' inline comment
    assert cfg.start_cross == 70.0, cfg.start_cross    # no comment
    assert cfg.keeper_start == 80.0, cfg.keeper_start  # absent -> default

    # The real file must load, whatever is in it today.
    real = load_config()
    assert real.min_start is not None

    print("ffcore.league self-test OK (7 cases)")


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
