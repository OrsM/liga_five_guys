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

__all__ = ["MARKET", "Config", "load_config", "read_rosters", "identify",
           "replay", "Cash", "Manager", "League"]

# Reserved counterparty in the ledger: the free-agent pool.
MARKET = "market"

# A price further than this factor from a candidate's value is not that player.
# The widest premium in the ledger is +21.6% and the app's own price swings a
# tenth either way, so a factor of two is slack rather than a tuned threshold —
# it is here to separate a 0.9M player from a 6.3M one, not to judge a bid.
PLAUSIBLE = 2.0

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


def identify(t: dict, owner: dict, market=None) -> tuple[str, str]:
    """(player key, why) for one ledger row — issue #26.

    The counterparty is evidence about who the player is. A sale names someone
    that manager was holding; a purchase from the market names someone nobody
    held. Either one prunes a candidate list that the name alone leaves
    ambiguous, which is the manual step the ledger's own notes record: "price
    confirms Fabio not Johnny".

    Three prunes, applied to the candidates ffcore.text.resolve() hands back
    rather than replacing it — an exact name is returned untouched and is never
    second-guessed:

      1. Sold by a manager -> he was in that manager's squad at the time.
      2. Bought from the market -> nobody in the league held him.
      3. Priced -> the price has to be within a factor of PLAUSIBLE of his
         value at the time. Two players who share a surname rarely share a
         price bracket.

    Returns (norm(raw), "") unless exactly one candidate survives, so an
    unresolved name still lands in unmatched() and a wrong player is never
    invented. `why` is non-empty only when a substitution was made, and every
    caller is expected to report it: this guesses, so it has to say so.
    """
    key = norm(t["player"])
    rows = market.latest() if market is not None else {}
    if not rows or key in rows:
        return key, ""

    from ffcore.text import resolve
    rec, cands = resolve(t["player"], list(rows.values()))
    if rec:
        return norm(rec.get("name")), "matched %s" % rec.get("name")
    if not cands:
        return key, ""

    src = (t.get("from") or "").strip() or MARKET
    dst = (t.get("to") or "").strip() or MARKET
    why = ""

    if src != MARKET:
        kept = [c for c in cands if owner.get(norm(c.get("name"))) == src]
        if kept:
            cands, why = kept, "held by %s at the time" % src
    elif dst != MARKET:
        kept = [c for c in cands if norm(c.get("name")) not in owner]
        if kept:
            cands, why = kept, "the only one nobody owned"

    price = money(t.get("price"))
    when = ledger_stamp(t.get("date", ""))
    if len(cands) > 1 and price and when:
        kept = []
        for c in cands:
            v = market.at(c.get("name"), when)
            if v and v.value and 1 / PLAUSIBLE <= price / v.value <= PLAUSIBLE:
                kept.append(c)
        if kept:
            cands = kept
            why = ("price fits only his value" if len(kept) == 1
                   else why)

    if len(cands) != 1:
        return key, ""
    return norm(cands[0].get("name")), why


def replay(rosters: dict[str, list[str]], txns: list[dict], market=None):
    """initial rosters + full ledger = current ownership.

    Every transaction is replayed, oldest first, so identify() can use the
    ownership state as it stood when the row happened rather than as it stands
    now. If a row contradicts the state it lands on, that is a gap in the
    ledger and gets warned about rather than silently absorbed.

    Returns (owner, warnings, resolved). `resolved` is every name identify()
    substituted, so the reports can show what was guessed and on what grounds.
    """
    owner: dict[str, str] = {}
    for mgr, names in rosters.items():
        for n in names:
            owner[norm(n)] = mgr
    warnings: list[str] = []
    resolved: list[str] = []
    for t in txns:
        key, why = identify(t, owner, market)
        if why:
            resolved.append("%s: %s → %s (%s)" % (
                t.get("date", "?"), t["player"], key, why))
        src = (t.get("from") or "").strip() or MARKET
        dst = (t.get("to") or "").strip() or MARKET
        if src != MARKET and owner.get(key) not in (src, None):
            warnings.append("%s: %s was not owned by %s" % (
                t.get("date", "?"), t["player"], src))
        elif src != MARKET and owner.get(key) is None:
            # Used to pass in silence: the key was absent, so the pop below did
            # nothing and the sale left no trace. Either a purchase is missing
            # from the ledger or the name is spelled differently from the
            # roster — both are worth a line, and neither is visible any other
            # way (issue #26).
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
        self.owner, self.warnings, self.resolved = replay(rosters, txns,
                                                          market)

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
                if not budget:
                    mgr.cash = Cash(None, "unknown",
                                    "no balance recorded and no budget "
                                    "configured", "")
                    continue
                # The starting roster is dealt free, so the whole budget is
                # the anchor. Charging it against roster value put every
                # rival tens of millions under water.
                base, since, conf = budget, None, "estimated"
                basis = "%.0fM starting budget" % (budget / 1e6)

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
            if value < 0:
                # Impossible in the app, so an input is wrong: unrecorded
                # sales, income the ledger never sees, or a bigger starting
                # balance than configured. Publishing it as a number is worse
                # than admitting ignorance, because max_bid used to clamp it
                # to 0 and the report then called the manager unable to
                # escalate. Keep the arithmetic in the basis so the size of
                # the discrepancy stays visible.
                self.warnings.append(
                    "%s: net spend exceeds the %.0fM budget by %.2fM — "
                    "unrecorded sales, or they started with more. Cash "
                    "reported as unknown; ask before assuming they are "
                    "broke." % (handle, budget / 1e6, -value / 1e6))
                mgr.cash = Cash(None, "unknown",
                                "%s, then %d ledger row(s), which overdraws "
                                "it by %.2fM" % (basis, counted, -value / 1e6),
                                _now())
                continue
            mgr.cash = Cash(value, conf,
                            "%s, then %d ledger row(s)" % (basis, counted),
                            _now())

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

    _selftest_cash()
    _selftest_identify()
    print("ffcore.league self-test OK (7 cases + cash + identify)")


def _selftest_cash() -> None:
    """Cash from a free starting squad.

    The draft hands out a randomised roster at no cost plus a separate cash
    balance, so the anchor is the whole budget. Anchoring on
    budget - roster_value charged everyone for players they were given and
    put all four rivals tens of millions under water.
    """
    cfg = Config(me="nobody", budget=100e6)   # not a handle in cash.txt
    rosters = {"me": ["a"], "rich": ["b"], "spent": ["c"]}
    txns = [
        # rich buys 10M, sells 4M -> 100 - 10 + 4 = 94M
        {"date": "2026-08-11T21:24", "player": "x", "from": MARKET,
         "to": "rich", "price": "10000000"},
        {"date": "2026-08-11T21:42", "player": "b", "from": "rich",
         "to": MARKET, "price": "4000000"},
        # spent outruns the budget: a bound, not a balance
        {"date": "2026-08-12T21:24", "player": "y", "from": MARKET,
         "to": "spent", "price": "124560000"},
    ]
    lg = League(cfg, rosters, txns, None)

    assert lg["rich"].cash.value == 94e6, lg["rich"].cash.value
    assert lg["me"].cash.value == 100e6, lg["me"].cash.value

    # An untouched manager is at the full budget, not at "unknown" — the old
    # code needed a priceable market to say anything at all.
    assert lg["me"].cash.confidence == "estimated", lg["me"].cash.confidence

    # Overspend must not silently clamp to a confident zero: that is what
    # told the report four rivals "cannot escalate".
    over = lg["spent"]
    assert over.cash.value is None, over.cash.value
    assert over.cash.confidence == "unknown", over.cash.confidence
    assert over.max_bid is None, over.max_bid
    assert any("exceeds" in w for w in lg.warnings), lg.warnings


def _selftest_identify() -> None:
    """Issue #26: who the counterparty was narrows who the player can be.

    A sale names a player that manager was holding, and a purchase from the
    market names one nobody held. Both prune a candidate list that string
    matching alone leaves ambiguous.
    """
    class _Val(NamedTuple):
        value: float
        lag_h: float

    class _Market:
        """Two Cardosos, an order of magnitude apart — the real collision."""
        vals = {"fabio cardoso": 925_408.0, "johnny cardoso": 6_306_919.0,
                "dani lorenzo": 4_000_000.0, "dani martinez": 500_000.0}

        def at(self, name, when):
            v = self.vals.get(norm(name))
            return None if v is None else _Val(v, 2.0)

        def latest(self):
            return {k: {"name": k} for k in self.vals}

    mkt = _Market()
    owner = {"dani lorenzo": "alice", "johnny cardoso": "bob"}

    # A sale by alice can only be a player alice was holding, so the two Danis
    # collapse to one even though the name matches both.
    key, why = identify({"player": "Dani", "from": "alice", "to": MARKET},
                        owner, mkt)
    assert key == "dani lorenzo", (key, why)
    assert "alice" in why, why

    # The same string, sold by bob, is not a player bob holds: no guess, and
    # the row is left alone for the warning to pick up.
    key, why = identify({"player": "Dani", "from": "bob", "to": MARKET},
                        owner, mkt)
    assert key == "dani" and why == "", (key, why)

    # A purchase from the market cannot be a player somebody already owns, so
    # ownership alone settles the Cardosos: johnny is bob's.
    key, why = identify({"player": "Cardoso", "from": MARKET, "to": "alice",
                         "price": "949269", "date": "2026-08-11T21:24"},
                        owner, mkt)
    assert key == "fabio cardoso", (key, why)

    # Price settles it too, and on its own: with neither owned, 949,269 is
    # +2.6% on Fabio and -84.9% on Johnny. This is the hand-written
    # "price confirms Fabio not Johnny" note in the ledger, automated.
    key, why = identify({"player": "Cardoso", "from": MARKET, "to": "alice",
                         "price": "949269", "date": "2026-08-11T21:24"},
                        {}, mkt)
    assert key == "fabio cardoso", (key, why)
    assert "price" in why, why

    # A price that fits both candidates proves nothing, so nothing is chosen.
    key, why = identify({"player": "Cardoso", "from": MARKET, "to": "alice",
                         "price": "3000000", "date": "2026-08-11T21:24"},
                        {}, mkt)
    assert key == "cardoso" and why == "", (key, why)

    # An exact name is never second-guessed, whoever is holding it.
    key, why = identify({"player": "Johnny Cardoso", "from": "bob",
                         "to": MARKET}, owner, mkt)
    assert key == "johnny cardoso" and why == "", (key, why)

    # No market and no ownership to work with: unchanged, never invented.
    key, why = identify({"player": "Nobody", "from": MARKET, "to": "alice"},
                        {}, None)
    assert key == "nobody" and why == "", (key, why)

    # -- replay uses it, and says so ---------------------------------------
    # The ledger says alice sold "Dani". Ownership moves off the right key, so
    # the squad is emptied rather than left holding a player who was sold.
    own2, warns, notes = replay({"alice": ["Dani Lorenzo"]},
                                [{"date": "2026-08-13T21:25", "player": "Dani",
                                  "from": "alice", "to": MARKET,
                                  "price": "3800000"}], mkt)
    assert own2 == {}, own2
    assert notes and "dani lorenzo" in notes[0], notes
    assert not warns, warns

    # A sale of a player nobody is recorded as holding used to pass in
    # silence: the key simply wasn't in the map, so the pop did nothing.
    _, warns2, _ = replay({"alice": ["Dani Lorenzo"]},
                          [{"date": "2026-08-13T21:25", "player": "Xabi",
                            "from": "alice", "to": MARKET}], mkt)
    assert any("nobody was holding" in w for w in warns2), warns2


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
