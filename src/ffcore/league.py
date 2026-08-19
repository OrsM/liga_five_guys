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

An anchor is a balance somebody observed: the app's own `teamMoney` on this
sweep for you, or a line typed into inputs/cash.txt. For rivals there usually
is none — the API states `teamMoney` for the account that asks and null for
everyone else — so the fallback anchor is the whole starting budget, because
the draft deals the starting squad free.

`budget - (market value of the initial roster)` was tried and was WRONG: it
charged every manager for players they were given and put all four rivals tens
of millions under water. Nothing computes it any more; the method that did was
carried, uncalled, until 2026-08-19.

Confidence is one of:
    known      anchored on a balance somebody saw, plus exact ledger arithmetic
    estimated  anchored on the starting budget
    unknown    no budget configured, or no ledger coverage — value is None

Treat "estimated" as a ceiling rather than a balance: it ignores whatever
income the app has paid out and any deal that never appeared in the feed.

A balance can be NEGATIVE, and that is a position rather than an error. The
app lets a manager commit past the balance while the window is open; the
constraint is being solvent when the jornada locks. So an overdrawn manager
gets the negative number, the arithmetic that produced it in `basis`, and a
max_bid of zero — they must sell before they can buy again. Only a missing
budget produces "unknown", which is the one state that means "could outspend
you" and suppresses every bid ceiling downstream.
"""

from __future__ import annotations

import configparser
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import Path
from typing import NamedTuple

from ffcore.parse import money
from ffcore.text import norm, resolve
from ffcore.tidy import (Market, input_path, ledger_stamp, load_api_teams,
                         load_market, read_ledger, snapshot_stamp)

__all__ = ["MARKET", "Config", "load_config", "read_rosters", "identify",
           "read_api_balances", "owner_from_api", "api_key", "owner_drift",
           "allowance",
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
    "start_cross": "70",
    "shrink_k": "8",
    "daily_bonus": "0",
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
    start_cross: float = 70.0
    shrink_k: float = 8.0
    # The app pays a small allowance every day. It is not in the ledger and
    # never will be — the activity feed records deals, not gifts — so an
    # estimate built from budget minus purchases drifts further under the
    # truth with every day the season runs. Zero until configured, because a
    # bonus nobody has observed is not a number to invent.
    daily_bonus: float = 0.0


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
        start_cross=float(get("thresholds", "start_cross",
                              DEFAULTS["start_cross"])),
        shrink_k=float(get("thresholds", "shrink_k", DEFAULTS["shrink_k"])),
        # Read from [league], beside the budget it corrects, rather than from
        # [thresholds]: it is a fact about how the app pays, not a knob.
        daily_bonus=money(get("league", "daily_bonus",
                              DEFAULTS["daily_bonus"])) or 0.0,
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


def read_api_balances(rows=None) -> dict[str, tuple[float, str]]:
    """{manager: (balance, date)} straight from the league's own API.

    Only managers the API actually states a balance for, which today is you
    and nobody else — `teamMoney` comes back null for every other team. An
    empty string is NOT STATED and must never become 0.0, or a rival reads as
    broke and every bid ceiling built on it is wrong.

    Returns {} when the API has never been fetched, which is the state every
    caller has always handled: the typed anchors in cash.txt take over.
    """
    out: dict[str, tuple[float, str]] = {}
    for r in (load_api_teams() if rows is None else rows):
        handle = (r.get("manager") or "").strip()
        raw = (r.get("team_money") or "").strip()
        if not handle or not raw:
            continue
        try:
            value = float(raw)
        except ValueError:
            continue
        # THE WHOLE MOMENT, not the day, and parsed as UTC. This balance is
        # the app's answer as of the sweep, so every deal up to then is
        # already inside it. Truncating to a date makes every deal later the
        # same day look like it happened after the observation and subtracts
        # it from a number that already counted it — 23.60M reported as
        # 41.92M. And it must be snapshot_stamp, not ledger_stamp: the sweep
        # stamp ends in Z and means UTC, while ledger_stamp reads the ledger's
        # local wall-clock, so the wrong one is two hours out in summer.
        out[handle] = (value, snapshot_stamp(r.get("observed_at") or ""))
    return out


def allowance(since, now, daily_bonus: float) -> tuple[float, float]:
    """(euros the app has paid out since `since`, days it covers).

    THE ANCHOR IS THE START, WHATEVER LABELLED IT. This used to be applied
    only to ESTIMATED balances, on the reasoning that an observed balance
    already contains every bonus paid — true of the moment it was read, and
    false of every day after. The app's own reading is seconds old so it is
    owed ~0 either way; a line typed into cash.txt four days ago is owed four
    days, and without them it came back 0.40M light while still calling
    itself "known".

    No anchor is (0, 0) rather than a guess: the caller decides what to
    measure from. A `since` in the future pays nothing — a clock a little out
    is not a windfall.
    """
    if since is None or now is None:
        return 0.0, 0.0
    days = max(0.0, (now - since).total_seconds() / 86400.0)
    return days * (daily_bonus or 0.0), days


def _by_exact_value(raw_value, rows) -> str | None:
    """The one market player who has ever been worth exactly this, or None.

    EXACT, with no tolerance, on purpose. The join is only trustworthy because
    futbolfantasy publishes the same euro figure the app does; allow a euro of
    slack and it becomes a fuzzy match over a dense number line where several
    players sit within a rounding error of each other.

    ACROSS ALL OF HISTORY, though, not just the newest snapshot. api_teams is
    swept once a day and market.csv every run, so hours later the app's figure
    is one the market has already moved on from. Searching only the latest
    reading made this join work for half an hour and then quietly stop.

    Uniqueness is per PLAYER, not per row: the same player at the same value
    in thirty snapshots is one candidate, while two different players who have
    each been worth 500 at some point are two, and two is no answer.
    """
    try:
        want = float(raw_value)
    except (TypeError, ValueError):
        return None
    if not want:
        return None
    hits = set()
    for row in rows:
        try:
            if float(row.get("value")) == want:
                hits.add(norm(row.get("name")))
        except (TypeError, ValueError):
            continue
    return hits.pop() if len(hits) == 1 else None


def owner_from_api(rows: list[dict], market,
                   ledger_owner: dict | None = None) -> tuple[dict, list]:
    """({player key: manager}, unjoined names) from the app's own squads.

    Ownership WITHOUT a replay. `replay()` reconstructs who owns whom by
    reading a starting roster and applying every transaction ever typed, so it
    inherits every gap in that file; this is the app answering the question
    directly. No accumulation, so no accumulated drift.

    Keyed through `market.key_for`, which is the same resolution every other
    reader in this repo uses, because a key nothing else recognises is a
    player who quietly vanishes from the watchlist and the board. Unjoined
    names come back to be printed, never dropped — dropping one marks an owned
    player as a free agent, which is how you end up bidding for somebody a
    rival already has.

    `market` may be None (the `with_market=False` path), in which case the
    app's own spelling is used and the caller is on its own for joins.

    The row's `player_name_full` is passed through, because the app publishes
    two names per player and neither joins alone. See `api_key`.

    `ledger_owner` breaks the one tie the app creates for us. The app lists
    some players by surname alone — "Cardoso" with a Fabio and a Johnny in the
    market, "Llorente" with a Marcos and a Diego Javier — and `key_for`
    refuses to pick, correctly. But the ledger identified those players at
    purchase time, price check and all, so when exactly ONE candidate is
    already recorded against the SAME manager, the two sources agree and there
    is nothing left to guess. Disagreement, or two candidates on the same
    manager, stays unresolved.

    The join itself is `api_key()`. This is a loop over it that keeps only the
    manager; a caller who needs anything else off the row — the buyout clause,
    the app's points — calls that directly rather than re-deriving a weaker
    join of its own.
    """
    from ffcore.tidy import latest_only
    out, unjoined = {}, []
    index = latest_only(market.rows) if market is not None else []
    for r in rows:
        handle = (r.get("manager") or "").strip()
        raw = (r.get("player_name") or "").strip()
        if not handle or not raw:
            continue
        key = api_key(raw, handle, market, ledger_owner, index,
                      r.get("market_value"), r.get("player_name_full") or "")
        if key:
            out[key] = handle
        else:
            unjoined.append(raw)
    return out, unjoined


def api_key(raw: str, handle: str, market, ledger_owner: dict | None = None,
            index: list | None = None, market_value=None,
            full: str = "") -> str | None:
    """One API row's player, as a key the rest of the repo recognises.

    FOUR JOINS IN FALLING ORDER OF TRUST, and the order is the whole design:

      1. `market.key_for` on the app's nickname — the resolution every other
         reader uses.
      2. `market.key_for` again on the app's FULL name. The same resolution
         given a better string, which is why it outranks everything below it
         rather than being another kind of guess: the app publishes both, the
         shortened one is the nickname, and "Cardoso" spelled out is "Fábio
         Rafael Rodrigues Cardoso". It runs second and not first because the
         nickname is the better single guess — of 76 owned players, twelve
         join ONLY on it, their full name being a birth name nothing else
         uses ("Pepelu" is "José Luis García Vayá").
      3. the ledger breaking a tie, when the app gave a surname the market
         has two of and exactly one of them is already recorded against THIS
         manager.
      4. an EXACT market value, searched across all of history.

    None means unresolved, and unresolved must stay visible: a dropped row is
    an owned player reading as a free agent, or a rival's man who cannot be
    bought because nothing knows his clause belongs to anybody.

    `index` is the latest market snapshot, passed in when a caller is looping
    so it is not rebuilt per row; omit it and it is derived here.
    """
    from ffcore.tidy import latest_only
    raw = (raw or "").strip()
    if not raw:
        return None
    if market is None:
        return norm(raw) or None
    key = market.key_for(raw)
    if not key and (full or "").strip():
        key = market.key_for(full.strip())
    if not key and ledger_owner:
        if index is None:
            index = latest_only(market.rows)
        _rec, cands = resolve(raw, index)
        agreed = [norm(c.get("name")) for c in cands
                  if ledger_owner.get(norm(c.get("name"))) == handle]
        if len(agreed) == 1:
            key = agreed[0]
    if not key:
        # Last resort, and the strongest key of the three: an EXACT market
        # value, anywhere in the recorded history. Only when it identifies
        # exactly one player.
        key = _by_exact_value(market_value, market.rows)
    return key or None


def ledger_from_api(activity: list[dict], users: dict,
                    names: dict) -> list[dict]:
    """The transaction ledger, derived from the app's activity feed.

    The ledger was typed by hand after every deal, which made
    it the one input that could silently fall behind — and on 2026-08-17 it
    was three days behind, which is what made the report offer a 63.29M budget
    against a real 23.60M. A feed cannot forget.

    ONE THING THE FEED CANNOT SAY: who the counterparty was. Every row names
    `user1Id` and nobody else, and a manager-to-manager transfer does NOT
    appear as a paired buy and sell — checked against all 57 rows, and no two
    share a player and a moment. So a buy is written as coming from the pool
    and a sale as going to it, which is right for ownership and right for
    every premium (those need the price and the buyer, both of which are
    here), and wrong only for the narrative of who dealt with whom. The
    hand-typed file's `from`/`to` columns are kept in git history, where that
    detail survives for the rows that had it.

    A row whose player or manager cannot be named is DROPPED rather than
    written blank: a ledger row with no player joins to no market value, and
    would quietly distort the premium medians built on it.
    """
    out = []
    for r in sorted(activity, key=lambda x: x.get("at") or ""):
        kind = r.get("kind")
        if kind not in ("buy", "sell"):
            continue
        who = users.get(str(r.get("user_id") or ""))
        player = names.get(str(r.get("player_id") or ""))
        if not who or not player:
            continue
        out.append({
            # The ledger's own format: minutes, no offset. ledger_stamp reads
            # this and nothing wider.
            "date": (r.get("at") or "")[:16],
            "player": player,
            "from": MARKET if kind == "buy" else who,
            "to": who if kind == "buy" else MARKET,
            "price": str(r.get("amount") or ""),
            "note": "from the app",
        })
    return out


def owner_drift(ledger: dict, api: dict) -> list[str]:
    """Where the typed ledger and the app disagree, one line each.

    Silence on agreement. This exists to be READ, so it must not print the
    hundreds of rows the two agree about — only the ones where acting on the
    ledger would act on a false premise.

    An empty `api` yields nothing at all rather than "everything is wrong":
    no token, no claim.
    """
    if not api:
        return []
    out = []
    for key, held in sorted(ledger.items()):
        now = api.get(key)
        if now is None:
            out.append("**%s** — the ledger has him at %s; the app says "
                       "nobody in the league holds him." % (key, held))
        elif now != held:
            out.append("**%s** — the ledger has him at %s; the app says %s."
                       % (key, held, now))
    for key, now in sorted(api.items()):
        if key not in ledger:
            out.append("**%s** — the app has him at %s; the ledger has no "
                       "record of him." % (key, now))
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
    # The three terms of `value`, so a report can print the sum rather than
    # only the answer: base − bought + sold = value. `base` is the last
    # balance you observed when there is one and the starting budget when
    # there is not, and bought/sold count only the ledger rows AFTER that
    # anchor — which is why they are not Manager.spend and .proceeds. Printing
    # those two beside an anchored balance produced a row that did not add up.
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
        """Spent past the budget. A real state, not an input error.

        The app lets you commit beyond the balance while the window is open;
        what it will not allow is being under water when the jornada locks. So
        a negative balance is published as the negative number it is, with the
        arithmetic in `basis`, rather than hidden behind "unknown".
        """
        return self.value is not None and self.value < 0


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
        """Hard ceiling on their next bid. None when cash is unknown.

        Zero for an overdrawn manager, which is the honest read of a negative
        balance: they cannot buy again until they sell. It is not the same as
        None — unknown means "could outspend you", zero means "not today".
        """
        if self.cash.value is None:
            return None
        return max(0.0, self.cash.value)


class League:
    """Current state of the league, assembled from the three input files."""

    def __init__(self, cfg: Config, rosters, txns, market: Market | None,
                 api_teams=None):
        self.cfg = cfg
        self.rosters = rosters
        self.txns = txns
        self.market = market
        self.owner, self.warnings, self.resolved = replay(rosters, txns,
                                                          market)

        # THE APP OVERRULES THE LEDGER. `replay()` accumulates typed
        # transactions over a starting roster, so it inherits every row nobody
        # typed; the API states ownership outright. The replay still runs — it
        # produces the prices and premiums — but its ownership is superseded.
        #
        # An EMPTY feed changes nothing, and that is the case that matters: a
        # token expiring mid-season must degrade to the ledger, never announce
        # that nobody owns anybody.
        self.api_unjoined: list[str] = []
        self._api_teams = api_teams
        # No market, no override. The join needs `Market.key_for` to produce
        # keys the rest of the repo recognises; without one the app's own
        # spelling becomes the key and the squad silently stops matching the
        # checklist, the watchlist and the board. The cash anchor below is
        # unaffected — a balance needs no name.
        if api_teams and market is None:
            api_teams = None
        if api_teams:
            # Keyed through Market.key_for — the same resolution every other
            # reader uses. Keying on the app's own spelling looks right and is
            # not: the two sets never meet and every owned player reads as a
            # free agent, with the player COUNTS still correct, which is what
            # makes it convincing.
            api_owner, self.api_unjoined = owner_from_api(
                api_teams, market, ledger_owner=self.owner)
            if api_owner:
                self.warnings += owner_drift(self.owner, api_owner)
                for raw in self.api_unjoined:
                    self.warnings.append(
                        "**%s** — the app says he is owned, but no market row "
                        "matches the name, so he is missing from the board."
                        % raw)
                self.owner = api_owner

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
        # load_api_teams() is [] until the API has been swept, which is the
        # state every caller already handles: the ledger takes over.
        return cls(cfg, read_rosters(), read_ledger(), market,
                   api_teams=load_api_teams())

    def __getitem__(self, handle: str) -> Manager:
        return self.managers[handle]

    def __iter__(self):
        """Managers, you first, then alphabetical."""
        return iter(sorted(self.managers.values(),
                           key=lambda m: (m.handle != self.cfg.me, m.handle)))

    # -- cash ----------------------------------------------------------

    def _estimate_cash(self) -> None:
        balances = read_balances()
        me_balance = balances.pop("__me__", None)
        if me_balance:
            balances.setdefault(self.cfg.me, me_balance)
        # The app's own number wins over anything typed. It is still an
        # OBSERVED balance — the same kind cash.txt holds — just observed by
        # machine, to the euro, on every sweep, so it can never be the thing
        # that went stale. On 2026-08-17 the typed anchor was two days old and
        # the report offered 63.29M against a real 23.60M.
        #
        # Rivals are not in here: the API states `teamMoney` for the account
        # that asks and null for everyone else, so their estimate is untouched
        # and the `~` on it stays honest.
        for handle, (value, when) in read_api_balances(
                self._api_teams).items():
            balances[handle] = (value, when)

        for handle, mgr in self.managers.items():
            budget = self.cfg.budgets.get(handle, self.cfg.budget)
            anchor = balances.get(handle)

            if anchor:
                base, since_s = anchor
                # cash.txt hands us a typed date (local wall-clock);
                # read_api_balances hands us an already-parsed UTC moment.
                # Both are legitimate anchors and only the parsing differs.
                if isinstance(since_s, datetime):
                    since, from_app = since_s, True
                else:
                    since = ledger_stamp(since_s) if since_s else None
                    from_app = False
                conf = "known"
                basis = ("balance the app reported at %s"
                         % since.strftime("%Y-%m-%d %H:%M UTC")) if from_app \
                    else ("balance you recorded%s"
                          % (" on " + since_s if since_s else ""))
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

            bought = sold = 0.0
            counted = 0
            for t in self.txns:
                when = ledger_stamp(t.get("date", ""))
                if since and when and when <= since:
                    continue
                price = money(t.get("price")) or 0.0
                src = (t.get("from") or "").strip() or MARKET
                dst = (t.get("to") or "").strip() or MARKET
                if dst == handle:
                    bought += price
                    counted += 1
                if src == handle:
                    sold += price
                    counted += 1

            # THE DAILY ALLOWANCE, from the anchor to now. The feed cannot
            # see it, so without this a balance falls behind by the bonus
            # every day and a manager looks poorer — and therefore less able
            # to answer a clause — than he is. Only ever ADDS.
            #
            # EVERY ANCHOR, not only the estimated ones. An observed balance
            # contains every bonus paid up to the moment it was READ and none
            # of the ones paid since, so what matters is the anchor's age and
            # not its label. The app's own reading is seconds old and collects
            # nothing; the typed anchor in cash.txt was four days old and was
            # 0.40M light for exactly this reason, while calling itself known.
            start = since or min(
                (ledger_stamp(t.get("date", "")) for t in self.txns
                 if ledger_stamp(t.get("date", ""))), default=None)
            bonus, days = allowance(start, datetime.now(dt_timezone.utc),
                                    self.cfg.daily_bonus)

            value = base + sold - bought + bonus
            # The arithmetic, not just the answer. Every term is here so a
            # balance that looks wrong can be checked against the ledger
            # without re-deriving it, and so an overdrawn manager's position
            # can be sized at a glance.
            math = ("%s − %.2fM bought + %.2fM sold across %d ledger row(s)"
                    "%s = %.2fM"
                    % (basis, bought / 1e6, sold / 1e6, counted,
                       (" + %.2fM of daily allowance over %.0f days"
                        % (bonus / 1e6, days)) if bonus else "",
                       value / 1e6))
            if value < 0:
                # NOT an input error. Committing past the balance is allowed
                # while the window is open; the constraint is being solvent
                # when the jornada locks. This used to be reported as
                # "unknown" plus a warning, which threw away a real number and
                # made every rival's ceiling unreadable through rival_ceiling.
                self.warnings.append(
                    "%s is %.2fM overdrawn: %s. Going over the budget "
                    "mid-window is allowed; being overdrawn when the jornada "
                    "locks is not, so they must sell before they can buy "
                    "again. If the ledger is missing a sale of theirs, this "
                    "is stale rather than wrong."
                    % (handle, -value / 1e6, math))
            mgr.cash = Cash(value, conf, math, _now(), base, bought, sold)

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
start_cross   = 70    # rows per position
"""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "league.ini"
        p.write_text(ini, encoding="utf-8")
        cfg = load_config(str(p))

    assert cfg.me == "someone", cfg.me
    assert cfg.budget == 100_000_000, cfg.budget
    assert cfg.min_start == 60.0, cfg.min_start        # ';' inline comment
    assert cfg.start_cross == 70.0, cfg.start_cross    # '#' inline comment
    assert cfg.shrink_k == 8.0, cfg.shrink_k           # absent -> default
    # A key nothing reads any more must not stop the file loading: league.ini
    # still carrying `lambda_buffer` from before λ was retired is ignored, not
    # an error, because config files outlive the code that read them.
    assert load_config is not None
    assert not hasattr(cfg, "lambda_buffer"), "lambda_buffer should be gone"
    # THE SAME RETIREMENT, THREE MORE KEYS. `top_n_per_pos`, `keeper_start`
    # and `riser_pct` sized the watchlist and watch.py's alerts, both gone
    # with the board on 2026-08-18. A threshold the code still parses is a
    # knob the reader believes is connected to something.
    for gone in ("top_n_per_pos", "keeper_start", "riser_pct"):
        assert not hasattr(cfg, gone), gone

    # The real file must load, whatever is in it today.
    real = load_config()
    assert real.min_start is not None

    _selftest_cash()
    _selftest_identify()
    _selftest_api_owner()
    print("ffcore.league self-test OK (8 cases + cash + identify + api)")


def _selftest_api_owner() -> None:
    """Ownership straight from the app, and the drift it exposes."""
    # A real Market, because the join goes through key_for and testing it
    # against a hand-made dict is what let the key mismatch through the first
    # time: the counts were right and every key was wrong.
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

    # A name the market index does not carry is REPORTED, never dropped.
    # Dropping it would silently mark an owned player as a free agent, and the
    # watchlist would then offer you somebody a rival already has.
    owner, unjoined = owner_from_api(
        rows + [{"manager": "SusoGattuso", "player_name": "Nobody At All"}],
        players)
    assert unjoined == ["Nobody At All"], unjoined
    assert len(owner) == 2, owner

    # A row with no manager or no name is skipped, not turned into a key of "".
    owner, _ = owner_from_api([{"manager": "", "player_name": "Simeone"},
                               {"manager": "x", "player_name": ""}], players)
    assert owner == {}, owner

    # THE KEY MUST BE THE MARKET'S, not the app's spelling. The app writes
    # "Fornals" where the market writes "Pablo Fornals"; keyed on the former
    # the player is owned by nobody as far as every other reader can tell.
    owner, unjoined = owner_from_api(
        [{"manager": "miguel_autentico", "player_name": "Fornals"}], players)
    assert owner == {norm("Pablo Fornals"): "miguel_autentico"}, owner
    assert unjoined == [], unjoined

    # -- the surname the app shortens, and two players who answer to it -----
    # Real: the app lists "Cardoso" and the market has both Fabio and Johnny;
    # "Llorente" has Marcos and Diego Javier. `key_for` refuses to guess, and
    # is right to. But the LEDGER already identified them at purchase time,
    # price check and all, so if exactly one candidate is one this manager was
    # already recorded as holding, that is not a guess — it is agreement.
    two = Market([
        {"name": "Fabio Cardoso", "value": "925408", "observed_at": at,
         "position": "DEF"},
        {"name": "Johnny Cardoso", "value": "6310000", "observed_at": at,
         "position": "MED"}])
    ambiguous_row = [{"manager": "Magic Mike 333", "player_name": "Cardoso"}]

    # THE SAME JOIN, REACHABLE ON ITS OWN. owner_from_api throws away
    # everything on the row except the manager, and the buyout clause is on
    # that row: a caller who needs the price a rival's player can be taken at
    # has to resolve his name the same three ways or it silently prices a
    # different player. api_key() is that resolution, and owner_from_api is
    # a loop over it.
    led = {norm("Fabio Cardoso"): "Magic Mike 333"}
    assert api_key("Cardoso", "Magic Mike 333", two, led) == norm("Fabio Cardoso")
    assert api_key("Cardoso", "Magic Mike 333", two) is None
    assert api_key("Fabio Cardoso", "Magic Mike 333", two) == norm("Fabio Cardoso")
    assert api_key("", "Magic Mike 333", two) is None

    # THE FULL NAME SETTLES IT WITHOUT ANY OF THAT, when the app sends one.
    # It publishes `nickname` AND `name`, and the shortened one is the
    # nickname: "Cardoso" is "Fábio Rafael Rodrigues Cardoso", "Aimar" is
    # "Aimar Oroz", "Brahim" is "Brahim Díaz". This is a second pass through
    # the SAME key_for, so it ranks above both fallbacks below — it is the
    # market's own resolution given a better string, not a new kind of guess.
    assert api_key("Cardoso", "Magic Mike 333", two,
                   full="Fabio Cardoso") == norm("Fabio Cardoso")
    # And it must not override a nickname that already joined: twelve of the
    # 76 owned players join ONLY on the nickname, because the full name is a
    # birth name nothing else uses ("Pepelu" is "José Luis García Vayá").
    assert api_key("Fabio Cardoso", "Magic Mike 333", two,
                   full="Somebody Entirely Different") == norm("Fabio Cardoso")
    # An absent full name changes nothing at all.
    assert api_key("Cardoso", "Magic Mike 333", two, full="") is None
    owner, unjoined = owner_from_api(
        [{"manager": "Magic Mike 333", "player_name": "Cardoso",
          "player_name_full": "Fabio Cardoso"}], two)
    assert owner == {norm("Fabio Cardoso"): "Magic Mike 333"}, owner
    assert unjoined == [], unjoined
    # A full name that is ITSELF ambiguous resolves nothing, same rule.
    assert api_key("Cardoso", "Magic Mike 333", two, full="Cardoso") is None

    # With no ledger to lean on it stays unresolved — never a coin flip.
    owner, unjoined = owner_from_api(ambiguous_row, two)
    assert owner == {} and unjoined == ["Cardoso"], (owner, unjoined)

    # The ledger settles it, and only for the manager who actually holds him.
    led = {norm("Fabio Cardoso"): "Magic Mike 333"}
    owner, unjoined = owner_from_api(ambiguous_row, two, ledger_owner=led)
    assert owner == {norm("Fabio Cardoso"): "Magic Mike 333"}, owner
    assert unjoined == [], unjoined

    # A ledger that puts the candidate with a DIFFERENT manager settles
    # nothing: the app says Magic Mike holds a Cardoso, and the only Cardoso
    # the ledger knows belongs to someone else, so the two disagree and the
    # answer is "unresolved", not "believe the ledger".
    owner, unjoined = owner_from_api(
        ambiguous_row, two, ledger_owner={norm("Fabio Cardoso"): "BurtonGM89"})
    assert owner == {} and unjoined == ["Cardoso"], (owner, unjoined)

    # Both candidates on the same manager is still ambiguous — he owns two
    # Cardosos and the app named one of them.
    owner, unjoined = owner_from_api(
        ambiguous_row, two,
        ledger_owner={norm("Fabio Cardoso"): "Magic Mike 333",
                      norm("Johnny Cardoso"): "Magic Mike 333"})
    assert owner == {} and unjoined == ["Cardoso"], (owner, unjoined)

    # -- the name that shares nothing at all, joined on price --------------
    # Real: the app calls Jonny Castro "Jonny Otto" and Álvaro Fernández
    # "A. Ferllo". No substring, no initials, no candidates — `resolve` finds
    # nothing to be ambiguous about, so the ledger tie-break cannot help
    # either. But futbolfantasy's values match the app TO THE EURO (that is
    # why this repo does not need the API for pricing), so an exact value
    # match is a stronger identification than any spelling.
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

    # NEAR is not a match. A euro out is a different player, because the whole
    # strength of this key is that it is exact — a tolerance would turn it
    # into the fuzzy name join it exists to avoid.
    owner, unjoined = owner_from_api(
        [{"manager": "SusoGattuso", "player_name": "Jonny Otto",
          "market_value": "5602303"}], priced)
    assert owner == {} and unjoined == ["Jonny Otto"], (owner, unjoined)

    # THE VALUE MUST BE LOOKED FOR IN ALL OF HISTORY, not just the newest
    # snapshot. api_teams is swept daily and market.csv every run, so within
    # hours the two readings are from different moments and the app's figure
    # matches a value the market USED to publish. Keyed on the latest
    # snapshot alone, "A. Ferllo" joined at 22:46 and stopped joining at
    # 23:18, and the only visible symptom was xi.py complaining that a player
    # in the squad was not on the checklist.
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

    # A value two different players have held at different times is still
    # ambiguous — uniqueness is per PLAYER, not per row.
    shared = Market([
        {"name": "One", "value": "500", "observed_at": "2026-08-16T0000Z",
         "position": "DEF"},
        {"name": "Two", "value": "500", "observed_at": "2026-08-17T0000Z",
         "position": "DEF"}])
    owner, unjoined = owner_from_api(
        [{"manager": "me", "player_name": "Nobody", "market_value": "500"}],
        shared)
    assert owner == {} and unjoined == ["Nobody"], (owner, unjoined)

    # …but the SAME player at that value across many snapshots is one player,
    # not many, and must still resolve.
    repeated = Market([
        {"name": "One", "value": "500", "observed_at": "2026-08-16T0000Z",
         "position": "DEF"},
        {"name": "One", "value": "500", "observed_at": "2026-08-17T0000Z",
         "position": "DEF"}])
    owner, _ = owner_from_api(
        [{"manager": "me", "player_name": "Nobody", "market_value": "500"}],
        repeated)
    assert owner == {norm("One"): "me"}, owner

    # Two players sharing a value exactly settles nothing.
    twinned = Market([
        {"name": "A One", "value": "500", "observed_at": at,
         "position": "DEF"},
        {"name": "B Two", "value": "500", "observed_at": at,
         "position": "DEF"}])
    owner, unjoined = owner_from_api(
        [{"manager": "x", "player_name": "Unknown", "market_value": "500"}],
        twinned)
    assert owner == {} and unjoined == ["Unknown"], (owner, unjoined)

    # And the price must never override a name that DID resolve: the name is
    # the primary key and this is a fallback, not a competitor.
    owner, _ = owner_from_api(
        [{"manager": "x", "player_name": "Jonny Castro",
          "market_value": "9999999"}], priced)
    assert owner == {norm("Jonny Castro"): "x"}, owner

    _selftest_derived_ledger()


def _selftest_derived_ledger() -> None:
    """The ledger, rebuilt from the feed instead of typed."""
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

    # Oldest first, and the "joined" row is not a transaction.
    assert len(rows) == 2, rows
    assert rows[0]["date"] < rows[1]["date"], rows

    # A buy comes FROM the pool and TO the manager; a sell is the reverse.
    # The feed names only one side, so the other is always `market` — see the
    # docstring for why that is a real limit and not a shortcut.
    assert rows[0] == {"date": "2026-08-15T22:24", "player": "Fornals",
                       "from": MARKET, "to": "miguel_autentico",
                       "price": "58220110", "note": "from the app"}, rows[0]
    assert rows[1]["from"] == "miguel_autentico", rows[1]
    assert rows[1]["to"] == MARKET and rows[1]["player"] == "Hugo Duro"

    # The date is trimmed to the ledger's own minute format, not the feed's
    # ISO-with-offset, because ledger_stamp reads the former.
    assert rows[0]["date"] == "2026-08-15T22:24", rows[0]

    # A player nothing can name is DROPPED with the name reported, not written
    # as a blank: a ledger row with no player joins to no market value and
    # would silently distort every premium built on it.
    rows2 = ledger_from_api(
        feed + [{"at": "2026-08-16T10:00:00+02:00", "kind": "buy",
                 "user_id": "11881989", "player_id": "9999",
                 "amount": "1"}], users, names)
    assert len(rows2) == 2, rows2

    # An unknown user is likewise not guessed at.
    rows3 = ledger_from_api(
        [{"at": "2026-08-16T10:00:00+02:00", "kind": "buy",
          "user_id": "404", "player_id": "1337", "amount": "1"}],
        users, names)
    assert rows3 == [], rows3

    # Empty feed is an empty ledger, and the caller must never write that over
    # a good file — see the guard in the writer.
    assert ledger_from_api([], users, names) == []

    _selftest_anchor_is_current()


def _selftest_anchor_is_current() -> None:
    """The app's balance is NOW, so nothing before it may be applied twice.

    The bug this pins: the API anchor was stamped with a DATE, so every deal
    later the same day was subtracted from a balance that already reflected
    it. Real cash 23.60M was reported as 41.92M — wrong in the generous
    direction, which is the dangerous one for a thing that tells you what you
    can bid.
    """
    at = "2026-08-17T2318Z"
    mkt = Market([{"name": "P", "value": "1000000", "observed_at": at,
                   "position": "MED"}])
    # A deal EARLIER on the same day as the observation.
    txns = [{"date": "2026-08-17T22:24", "player": "P", "from": "market",
             "to": "me", "price": "10000000", "note": ""}]
    api_teams = [{"manager": "me", "player_name": "P", "user_id": "1",
                  "team_money": "23596582", "observed_at": at}]

    lg = League(Config(me="me"), {"me": []}, txns, mkt, api_teams=api_teams)
    got = lg["me"].cash.value
    assert got == 23596582.0, (
        "the app's balance is current; a deal it already includes was "
        "subtracted again — got %r" % got)
    assert lg["me"].cash.confidence == "known", lg["me"].cash

    # A deal AFTER the observation is a different matter and must still count:
    # the sweep ran, then something happened.
    later = txns + [{"date": "2026-08-18T09:00", "player": "P",
                     "from": "market", "to": "me", "price": "1000000",
                     "note": ""}]
    lg2 = League(Config(me="me"), {"me": []}, later, mkt, api_teams=api_teams)
    assert lg2["me"].cash.value == 23596582.0 - 1000000.0, lg2["me"].cash

    # -- no market, no override --------------------------------------------
    # WITHOUT a market there is no key_for and no value fallback, so the app's
    # own spelling would become the key — and every other reader keys on the
    # market's. xi.py loads with_market=False, and the result was a squad
    # holding "a ferllo" while the checklist, correctly, held "alvaro
    # fernandez": each file complained the other was wrong.
    #
    # Applying a differently-keyed ownership map is worse than not applying
    # one, so without a market the replay stands.
    api_odd = [{"manager": "me", "player_name": "A. Ferllo", "user_id": "1",
                "team_money": "23596582", "observed_at": at}]
    lg3 = League(Config(me="me"), {"me": ["P"]}, [], None,
                 api_teams=api_odd)
    assert norm("a ferllo") not in lg3.owner, lg3.owner
    assert lg3.owner.get(norm("P")) == "me", lg3.owner
    # The balance still comes through: it needs no name join at all.
    assert lg3["me"].cash.value == 23596582.0, lg3["me"].cash

    # -- the drift report --------------------------------------------------
    # The point of keeping BOTH: the ledger says one thing, the app says
    # another, and the difference is what is worth printing. On 2026-08-17 the
    # ledger had Magic Mike on 19 players and the app on 16.
    ledger = {norm("Pablo Fornals"): "miguel_autentico",
              norm("Simeone"): "SusoGattuso",              # wrong owner
              norm("Carl Starfelt"): "miguel_autentico"}   # already sold
    api = {norm("Pablo Fornals"): "miguel_autentico",
           norm("Simeone"): "BurtonGM89"}
    drift = owner_drift(ledger, api)
    assert any("simeone" in d.lower() and "SusoGattuso" in d
               and "BurtonGM89" in d for d in drift), drift
    assert any("starfelt" in d.lower() and "nobody" in d for d in drift), drift
    assert len(drift) == 2, drift
    # Agreement is silence. A drift report that lists matches is noise nobody
    # reads, and this one has to be read.
    assert owner_drift(api, api) == []
    # No API data is no claim of drift, NOT "the ledger is entirely wrong".
    assert owner_drift(ledger, {}) == []

    # -- League prefers the app, and says where it differed ----------------
    mkt = Market([{"name": "Pablo Fornals", "value": "10000000",
                   "observed_at": "2026-08-17T2246Z", "position": "MED"},
                  {"name": "Simeone", "value": "5000000",
                   "observed_at": "2026-08-17T2246Z", "position": "DEL"}])
    rosters = {"miguel_autentico": ["Pablo Fornals", "Simeone"],
               "BurtonGM89": []}
    api_rows = [{"manager": "miguel_autentico", "player_name": "Pablo Fornals"},
                {"manager": "BurtonGM89", "player_name": "Simeone"}]

    # Without the API, the roster stands — the behaviour that always existed.
    plain = League(Config(me="miguel_autentico"), rosters, [], mkt)
    assert plain.owner[norm("Simeone")] == "miguel_autentico"

    # With it, the app wins and Simeone moves.
    lg2 = League(Config(me="miguel_autentico"), rosters, [], mkt,
                 api_teams=api_rows)
    assert lg2.owner[norm("Simeone")] == "BurtonGM89", lg2.owner
    assert lg2["BurtonGM89"].players == [norm("Simeone")], lg2["BurtonGM89"]
    # …and the disagreement is on the record rather than quietly applied.
    assert any("simeone" in w.lower() for w in lg2.warnings), lg2.warnings

    # An empty feed must not empty the league. This is the dangerous failure:
    # a token that expired mid-season should degrade to the ledger, never
    # report that nobody owns anybody.
    lg3 = League(Config(me="miguel_autentico"), rosters, [], mkt, api_teams=[])
    assert lg3.owner[norm("Simeone")] == "miguel_autentico", lg3.owner


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

    # The math is shown, not just the answer: every term of it is in the
    # basis, so a balance can be checked against the ledger without
    # re-deriving it.
    assert "10.00M bought" in lg["rich"].cash.basis, lg["rich"].cash.basis
    assert "4.00M sold" in lg["rich"].cash.basis
    assert "= 94.00M" in lg["rich"].cash.basis

    # Overspend is a POSITION, not a broken input. Going past the budget is
    # allowed while the window is open, so the negative number is published
    # with its arithmetic instead of being hidden behind "unknown".
    over = lg["spent"]
    assert over.cash.value == 100e6 - 124.56e6, over.cash.value
    assert over.cash.confidence == "estimated", over.cash.confidence
    assert over.cash.overdrawn and not lg["rich"].cash.overdrawn
    assert "= -24.56M" in over.cash.basis, over.cash.basis
    # Zero, not None: they cannot buy until they sell, which is a fact about
    # today rather than an admission of ignorance. None still means "could
    # outspend you" and still suppresses every bid ceiling.
    assert over.max_bid == 0.0, over.max_bid
    assert any("overdrawn" in w for w in lg.warnings), lg.warnings
    assert not any("exceeds" in w for w in lg.warnings), lg.warnings

    # -- the allowance an OBSERVED anchor is still owed ---------------------
    # A typed balance contains every bonus paid up to the moment it was read
    # and NONE of the ones paid since. Suppressing the allowance for every
    # anchor labelled "known" was right for the app's own reading, which is
    # seconds old, and wrong for a line in cash.txt: on 2026-08-19 the typed
    # anchor of 2026-08-15 came back 0.40M light against the app's own figure
    # and reported it as "known" — a stale number wearing the label that means
    # observed. Days since the anchor x allowance is exactly that gap.
    now = datetime(2026, 8, 19, 12, 0, tzinfo=dt_timezone.utc)
    four_days = datetime(2026, 8, 15, 12, 0, tzinfo=dt_timezone.utc)
    assert allowance(four_days, now, 100000) == (400000.0, 4.0)
    # The app's own anchor is minutes old, so it is owed nothing worth
    # printing — which is why this could never be caught on the API path.
    assert allowance(now, now, 100000) == (0.0, 0.0)
    # No allowance configured is no allowance, and an anchor from the future
    # never pays one out.
    assert allowance(four_days, now, 0) == (0.0, 4.0)
    assert allowance(now, four_days, 100000) == (0.0, 0.0)
    # No anchor at all: the caller falls back to the first deal on record, so
    # this must say so rather than invent a start.
    assert allowance(None, now, 100000) == (0.0, 0.0)

    # A manager with no budget configured is still genuinely unknown.
    blind = League(Config(me="nobody", budget=0.0), {"z": ["q"]}, [], None)
    assert blind["z"].cash.value is None and blind["z"].max_bid is None
    assert not blind["z"].cash.overdrawn


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

    # -- the daily allowance -----------------------------------------------
    # The feed records deals, not gifts, so an estimate built from budget
    # minus purchases falls further under the truth every day the season runs
    # — and a rival who looks poorer than he is looks less able to answer a
    # clause than he is.
    old = [{"date": "2026-01-01T12:00", "player": "P", "from": MARKET,
            "to": "rival", "price": "10000000"}]
    plain = League(Config(me="me", budget=100e6), {"rival": []}, old, None)
    rich = League(Config(me="me", budget=100e6, daily_bonus=100000.0),
                  {"rival": []}, old, None)
    assert rich["rival"].cash.value > plain["rival"].cash.value, (
        rich["rival"].cash.value, plain["rival"].cash.value)
    # It is shown in the arithmetic, not folded silently into the total.
    assert "daily allowance" in rich["rival"].cash.basis, rich["rival"].cash
    # AN OBSERVED BALANCE ALREADY CONTAINS THE BONUSES PAID BEFORE IT WAS
    # READ, AND NONE OF THE ONES PAID SINCE. So a reading taken on this sweep
    # collects nothing — adding a season of allowance on top of a number the
    # app stated would double-count every bonus ever paid…
    def _mine(at, money="23596582"):
        return [{"manager": "me", "player_name": "P", "user_id": "1",
                 "team_money": money, "observed_at": at}]

    def _seen(at):
        return League(Config(me="me", daily_bonus=100000.0), {"me": []}, [],
                      Market([{"name": "P", "value": "1000000",
                               "observed_at": at, "position": "MED"}]),
                      api_teams=_mine(at))

    fresh_at = datetime.now(dt_timezone.utc).strftime("%Y-%m-%dT%H%MZ")
    assert abs(_seen(fresh_at)["me"].cash.value - 23596582.0) < 5000, \
        _seen(fresh_at)["me"].cash
    # …but a reading the sweep has not refreshed for days HAS fallen behind by
    # the allowance, and saying so is the whole point of tracking it. This is
    # the same shape as the stale Elo rating: the number does not announce its
    # own age, so the code has to.
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
