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

Cash is reconstructed (the app publishes no balances), not read:

    cash = anchor_balance - buys_since_anchor + sales_since_anchor

Confidence is `known` (anchored on an observed balance), `estimated`
(anchored on the starting budget), or `unknown` (no budget/no ledger
coverage — value is None). A NEGATIVE balance is a real, overdrawn position,
not an error. Why: docs/notes/league.md#cash-estimation-model
"""

from __future__ import annotations

import configparser
import pathlib
import re
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import Path
from typing import NamedTuple

from ffcore.parse import money
from ffcore.text import norm
from ffcore.tidy import (load_crosswalk,  # noqa: E402
                         run_now,  # noqa: E402
                         Market, input_path, ledger_stamp,
                         load_api_standings, load_api_teams,
                         load_market_frozen, price_agrees, read_ledger,
                         snapshot_stamp)

__all__ = ["MARKET", "Config", "load_config", "read_rosters", "identify",
           "read_api_balances", "owner_from_api", "api_key", "owner_drift",
           "app_ids_known", "app_fielded", "flat_income",
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
    return cfg


def read_rosters(name: str = "rosters_initial.txt") -> dict[str, list[str]]:
    """[manager] sections, one player name per line, # for comments.

    A NAME TWO PLAYERS ANSWER TO NEEDS A CLUB, and this file is the one place
    a human still types a name. Write it as `alvaro garcia (Rayo)` and the
    club is folded into the key the rest of the repo uses — the same
    `name@club` the market index builds for a shared name. Without it the
    line names either man and the app's own ownership silently disagrees.
    """
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


# "alvaro garcia (Rayo)" — the club a shared name needs, in the one file a
# human still writes.
_ROSTER_CLUB = re.compile(r"^(.*?)\s*\(([^)]+)\)\s*$")


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

    Reads the STANDINGS, which is where a team's balance belongs and where it
    now lives — it used to be copied onto all fourteen of that manager's
    player rows.
    """
    out: dict[str, tuple[float, str]] = {}
    for r in (load_api_standings() if rows is None else rows):
        handle = (r.get("manager") or "").strip()
        raw = (r.get("team_money") or "").strip()
        if not handle or not raw:
            continue
        try:
            value = float(raw)
        except ValueError:
            continue
        # The whole moment (UTC), not the day — else a same-day deal after
        # the sweep double-subtracts. snapshot_stamp, not ledger_stamp: the
        # sweep stamp is UTC, the ledger's is local wall-clock.
        out[handle] = (value, snapshot_stamp(r.get("observed_at") or ""))
    return out


def flat_income(observed, budget: float, bought: float, sold: float):
    """What the app has paid the account beyond its transfers, or None.

    Measured on your own row (the one account with an observed balance) and
    credited to rivals equally, on the assumption the app pays everyone
    alike. Never negative — see docs/notes/league.md#rival-cash-income-measurement-flat_income
    for why (a real balance-vs-ledger gap and the number it corrected).
    """
    if observed is None:
        return None
    return max(0.0, observed - (budget + sold - bought))


def allowance(since, now, daily_bonus: float) -> tuple[float, float]:
    """(euros the app has paid out since `since`, days it covers).

    Applies to EVERY anchor by age, not just estimated ones — an observed
    balance is only current the moment it's read. `(0, 0)` with no anchor,
    never a guess. Why: docs/notes/league.md#the-daily-allowance-backfill-allowance
    """
    if since is None or now is None:
        return 0.0, 0.0
    days = max(0.0, (now - since).total_seconds() / 86400.0)
    return days * (daily_bonus or 0.0), days


def _by_exact_value(raw_value, market) -> str | None:
    """The one market player who has ever been worth exactly this, or None.

    Exact match (no tolerance), searched across all recorded history (not
    just the newest snapshot), unique per PLAYER not per row. Why:
    docs/notes/league.md#exact-value-join-as-a-last-resort-_by_exact_value
    """
    try:
        want = float(raw_value)
    except (TypeError, ValueError):
        return None
    if not want:
        return None
    # IN THE MARKET'S OWN KEYS. This answered norm(name), which was the key
    # only for as long as the market keyed on names — and a key the index
    # does not contain resolves nowhere, which reads downstream as a player
    # nobody owns rather than as a join that missed.
    hits = set()
    for row in market.rows:
        try:
            if float(row.get("value")) == want:
                hits.add(market.key_of(row))
        except (TypeError, ValueError):
            continue
    hits.discard("")
    return hits.pop() if len(hits) == 1 else None


def _app_ids_of(xw) -> dict:
    """{the app's player id: this repo's key}, read off an already-loaded
    Crosswalk. The one place this dict is ever built, shared by
    `app_ids_known()` (which loads its own Crosswalk, for a caller with none
    to hand) and `League.__init__` (which already has `self.xw` in memory
    and used to re-read and re-parse players.csv a second time to get here).
    """
    if xw is None:
        return {}
    return {p.app_id: p.player_id for p in xw.players.values() if p.app_id}


def app_ids_known() -> dict:
    """{the app's player id: this repo's key}, from the crosswalk, or {}.

    A deliberate one-way cache: players.csv (built by api_key/crosswalk.py)
    is read back here so an id resolved once stays resolved, instead of every
    run re-deriving it from scratch and sometimes failing. {} on a cold start
    is not an error. For a caller with no Crosswalk already in memory —
    League.__init__ calls `_app_ids_of(self.xw)` directly to avoid a second
    disk read. Why: docs/notes/league.md#the-app_ids-table--a-one-way-additive-cache
    """
    from ffcore.crosswalk import Crosswalk
    from ffcore.tidy import TIDY

    return _app_ids_of(Crosswalk.read(TIDY / "players.csv", TIDY / "clubs.csv"))


def owner_from_api(rows: list[dict], market, ledger_owner: dict | None = None,
                   app_ids: dict | None = None) -> tuple[dict, list]:
    """({player key: manager}, unjoined names) from the app's own squads.

    Ownership WITHOUT a replay (no accumulated drift — the app answers
    directly). Keyed through `market.key_for`, the same resolution every
    reader uses. Unjoined names are reported, never dropped. `market=None`
    (with_market=False) falls back to the app's own spelling. `ledger_owner`
    breaks the one tie the app creates (a surname two players share) using
    the ledger's own purchase-time record. Loop over `api_key()` — see its
    docstring for the join order. Why:
    docs/notes/league.md#ownership-from-the-app-api-owner_from_api
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
                      r.get("market_value"), r.get("player_name_full") or "",
                      app_ids, r.get("player_id") or "")
        if key:
            out[key] = handle
        else:
            unjoined.append(raw)
    return out, unjoined


def _priced_like(key: str, raw: str, market_value, index) -> bool:
    """Does the market price this player roughly the way the app does?

    Checks a GUESS from key_for, never an exact name match (an exact name is
    trusted outright). Reuses tidy.price_agrees()'s tolerance rather than a
    second copy of it. True whenever either side is silent — an absent
    number disproves nothing. Why:
    docs/notes/league.md#price-as-a-name-join-sanity-check-_priced_like
    """
    if not key or key == norm(raw):
        return True                       # the market carries this very name
    if market_value in (None, ""):
        return True
    try:
        theirs = float(str(market_value).strip())
    except (TypeError, ValueError):
        return True
    ours = next((money(r.get("value")) for r in (index or [])
                 if norm(r.get("name")) == key), None)
    if not ours:
        return True
    return price_agrees(theirs, ours)


def app_fielded(squad, names: dict, rows=None, ids=None) -> list[str]:
    """The eleven the APP says you are fielding, as this repo's keys, or [].

    Reads /v1/competition/1/teams/{team}/lineup/week/{n} — replaces the old
    hand-ticked inputs/lineup.txt checklist. ALL OR NOTHING: one unresolved
    row returns [] so the caller falls back to the marks, rather than one man
    silently reading as "put him on" (advice to redo a change already made).
    Why: docs/notes/league.md#app_fielded--the-apps-own-lineup
    """
    from ffcore.tidy import load_api_lineup

    rows = load_api_lineup() if rows is None else rows
    ids = app_ids_known() if ids is None else ids
    squad = set(squad)
    by_name = {norm(names.get(k, k)): k for k in squad}
    out = []
    for r in rows or []:
        key = ids.get((r.get("player_id") or "").strip())
        if key is None:
            for field in ("player_name", "player_name_full"):
                key = by_name.get(norm(r.get(field) or ""))
                if key:
                    break
        if not key or key not in squad:
            return []
        out.append(key)
    return out


def api_key(raw: str, handle: str, market, ledger_owner: dict | None = None,
            index: list | None = None, market_value=None,
            full: str = "", app_ids: dict | None = None,
            app_id: str = "") -> str | None:
    """One API row's player, as a key the rest of the repo recognises.

    Five-step chain, id first always: (1) app_ids lookup, (2) key_for on the
    app's nickname, (3) key_for on the full name, (4) the ledger breaking a
    surname tie, (5) an exact market-value match. `index` is the latest
    market snapshot (derived here if omitted, passed in when a caller is
    looping). None means unresolved — must stay visible, never guessed.
    TODO: steps 1-3 still duplicate Crosswalk.resolve(); not merged yet, see
    the note at the link below for why. Why (join order, the TODO, the
    concrete cases each step exists for):
    docs/notes/league.md#api_key--the-resolution-order-and-why
    """
    from ffcore.tidy import latest_only
    raw = (raw or "").strip()
    if not raw:
        return None
    if market is None:
        return norm(raw) or None
    if index is None:
        index = latest_only(market.rows)
    key = None
    if app_ids and (app_id or "").strip():
        key = app_ids.get(app_id.strip())
        # Still checked against the price when one is stated: cheap, and
        # true (never blocking) whenever the row is silent about it.
        if not _priced_like(key, "", market_value, index):
            key = None
    # Each NAME join is price-checked (see _priced_like) — a wrong-value
    # match falls through rather than confidently seating the wrong man in a
    # rival's squad (real case: two Álvaro Garcías, 20.23M vs 0.50M).
    if not key:
        key = market.key_for(raw, value=market_value)
        if not _priced_like(key, raw, market_value, index):
            key = None
    if not key and (full or "").strip():
        key = market.key_for(full.strip(), value=market_value)
        if not _priced_like(key, full, market_value, index):
            key = None
    if not key and ledger_owner:
        # Same producer, same keys — the ledger's owner map is keyed the way
        # the market keys players, so a candidate must be too.
        _got, cands = market.candidates(raw)
        agreed = [c for c in cands if ledger_owner.get(c) == handle]
        if len(agreed) == 1:
            key = agreed[0]
    if not key:
        # Last resort, and the strongest key of the three: an EXACT market
        # value, anywhere in the recorded history. Only when it identifies
        # exactly one player.
        key = _by_exact_value(market_value, market)
    return key or None


def ledger_from_api(activity: list[dict], users: dict,
                    names: dict) -> list[dict]:
    """The transaction ledger, derived from the app's activity feed.

    Replaces hand-typing (which fell behind — a real 3-day gap once cost a
    report 40M of accuracy). The feed cannot say who the counterparty was
    (every row names one user only, and no manager-to-manager transfer ever
    pairs a buy+sell in it), so a buy is written as from-the-pool and a sale
    as to-the-pool — right for ownership and premiums, silent only on who
    dealt with whom. A row with no nameable player/manager is DROPPED, not
    blanked. Why: docs/notes/league.md#ledger-reconstruction-from-the-apps-activity-feed-ledger_from_api
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
            # Carried, not thrown away — a display-name-only row re-derives
            # identity by fuzzy match later, which is how "C. Romero" once
            # became the wrong Romero.
            "player_id": str(r.get("player_id") or ""),
            "from": MARKET if kind == "buy" else who,
            "to": who if kind == "buy" else MARKET,
            "price": str(r.get("amount") or ""),
            "note": "from the app",
        })
    return out


def owner_drift(ledger: dict, api: dict, names=None) -> list[str]:
    """Where the typed ledger and the app disagree, one line each.

    Silence on agreement. This exists to be READ, so it must not print the
    hundreds of rows the two agree about — only the ones where acting on the
    ledger would act on a false premise.

    An empty `api` yields nothing at all rather than "everything is wrong":
    no token, no claim.
    """
    if not api:
        return []
    # KEYS ARE IDS NOW, AND A WARNING IS FOR A HUMAN. `names` maps key ->
    # display name; without it this printed "**9931** — the app has him at
    # Magic Mike 333", which names nobody. The key is still what the two
    # sides are compared on.
    def _who(k):
        return (names or {}).get(k) or k

    out = []
    for key, held in sorted(ledger.items()):
        now = api.get(key)
        if now is None:
            out.append("**%s** — the ledger has him at %s; the app says "
                       "nobody in the league holds him." % (_who(key), held))
        elif now != held:
            out.append("**%s** — the ledger has him at %s; the app says %s."
                       % (_who(key), held, now))
    for key, now in sorted(api.items()):
        if key not in ledger:
            out.append("**%s** — the app has him at %s; the ledger has no "
                       "record of him." % (_who(key), now))
    return out


def identify(t: dict, owner: dict, market=None, xw=None) -> tuple[str, str]:
    """(player key, why) for one ledger row — issue #26.

    Three prunes on Market.candidates()'s own list (never replacing it — an
    exact name is never second-guessed): (1) sold -> was in that manager's
    squad, (2) bought from market -> nobody held him, (3) priced -> within a
    factor of PLAUSIBLE of his value then. Returns (norm(raw), "") unless
    exactly one candidate survives. `why` non-empty only when a substitution
    was made — every caller must report it. Why:
    docs/notes/league.md#identify--counterparty-pruning-for-a-ledger-row-issue-26
    """
    key = norm(t["player"])
    # AN IDENTIFIER FIRST, AND THEN IT IS NOT A GUESS AT ALL. Everything
    # below is string matching with a counterparty and a price to prune it;
    # this is the app telling you which player the row is about. It is only
    # skipped when the feed did not carry one (the hand-typed rows in git
    # history) or the crosswalk has never seen it.
    pid = str(t.get("player_id") or "").strip()
    if pid and xw is not None:
        got = xw.player(app_id=pid)
        if got:
            return got, ""
    if market is None:
        return key, ""

    # Candidates come from the market, in the market's own keys. Asking
    # ffcore.text.resolve() over a raw row list instead lost a shared name to
    # an index that holds one row per name, and keyed the answer norm(name) —
    # a key this market does not contain. See Market.candidates.
    got, cands = market.candidates(t["player"])
    if got is not None:
        return (got, "" if got == key else "matched %s" % got)
    if not cands:
        return key, ""

    src = (t.get("from") or "").strip() or MARKET
    dst = (t.get("to") or "").strip() or MARKET
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
    """The canonical key for one rosters_initial.txt line.

    A thin wrapper over Crosswalk.resolve() (see its own docstring for the
    step order) — read_rosters() folds "alvaro garcia (Rayo)" into
    "alvaro garcia@rayo", split back apart here so the club reaches
    resolve()'s hint_club. Falls back to norm(raw) when resolve() has
    nothing (no crosswalk, or genuinely unplaceable). Why:
    docs/notes/league.md#_roster_key--migrating-onto-crosswalkresolve
    """
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
            owner[_roster_key(n, market, xw)] = mgr
    warnings: list[str] = []
    resolved: list[str] = []
    for t in txns:
        key, why = identify(t, owner, market, xw)
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
                 api_teams=None, standings=None, xw=None):
        self.cfg = cfg
        self.rosters = rosters
        self.txns = txns
        self.market = market
        self.xw = xw
        self.owner, self.warnings, self.resolved = replay(rosters, txns,
                                                          market, xw)

        # The app overrules the ledger's replay (superseding, not discarding
        # it — replay still produces prices/premiums). An empty feed changes
        # nothing (degrade to the ledger, never claim nobody owns anybody).
        # Why: docs/notes/league.md#replay-and-leagueinit--the-app-overrules-the-ledger
        self.api_unjoined: list[str] = []
        self._api_teams = api_teams
        # None means "read the store"; [] means the sweep returned no table
        # — must degrade to cash.txt, not to zeroes.
        self._standings = standings
        # No market, no override — Market.key_for is what makes the app's
        # ownership keys match the rest of the repo's.
        if api_teams and market is None:
            api_teams = None
        if api_teams:
            api_owner, self.api_unjoined = owner_from_api(
                api_teams, market, ledger_owner=self.owner,
                app_ids=_app_ids_of(self.xw))
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
        market = Market(load_market_frozen()) if with_market else None
        # load_api_teams() is [] until the API has been swept, which is the
        # state every caller already handles: the ledger takes over.
        return cls(cfg, read_rosters(), read_ledger(), market,
                   api_teams=load_api_teams(),
                   standings=load_api_standings(), xw=load_crosswalk())

    def txn_key(self, t: dict) -> str | None:
        """The player key one ledger row is about, or None.

        THE ONE PLACE A LEDGER ROW BECOMES A PLAYER. deals() used to take
        t["player"] and match the string all over again, which is a second
        answer to a question replay() had already answered — and the two
        could differ, because only one of them had the counterparty and the
        id to work with.
        """
        pid = str(t.get("player_id") or "").strip()
        if pid and self.xw is not None:
            got = self.xw.player(app_id=pid)
            if got:
                return got
        key, _why = identify(t, self.owner, self.market, self.xw)
        return key or None

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
        # The app's own reading wins over anything typed — it's an observed
        # balance too, just never stale (unlike a typed anchor). Rivals
        # aren't here: the API states teamMoney for you only. Why:
        # docs/notes/league.md#_estimate_cash--anchor--arithmetic-shown-in-full
        for handle, (value, when) in read_api_balances(
                self._standings).items():
            balances[handle] = (value, when)

        # What the app has paid beyond transfers — see flat_income() — from
        # the one account that states a balance, credited to every manager
        # with none.
        paid = None
        me_anchor = balances.get(self.cfg.me)
        if me_anchor and isinstance(me_anchor[1], datetime) and self.cfg.budget:
            b, sd = 0.0, 0.0
            for t in self.txns:
                price = money(t.get("price")) or 0.0
                if (t.get("to") or "").strip() == self.cfg.me:
                    b += price
                if (t.get("from") or "").strip() == self.cfg.me:
                    sd += price
            paid = flat_income(me_anchor[0], self.cfg.budget, b, sd)

        for handle, mgr in self.managers.items():
            # One budget for everyone — a per-manager override sat unused in
            # league.ini for a year and was removed.
            budget = self.cfg.budget
            anchor = balances.get(handle)

            if anchor:
                base, since_s = anchor
                # cash.txt: a typed local date. read_api_balances: an
                # already-parsed UTC moment. Same anchor, different parsing.
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
                # the anchor (not budget-minus-roster-value — see the doc).
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

            # The daily allowance, by the anchor's AGE not its label (every
            # anchor is owed it, only ADDS). Why:
            # docs/notes/league.md#the-daily-allowance-backfill-allowance
            if since is None and paid is not None:
                # A rival with no anchor: your own measured `paid` (flat_income)
                # is a better estimate than guessing how long the app has paid.
                bonus, days = paid, None
            else:
                start = since or min(
                    (ledger_stamp(t.get("date", "")) for t in self.txns
                     if ledger_stamp(t.get("date", ""))), default=None)
                bonus, days = allowance(start, run_now(),
                                        self.cfg.daily_bonus)

            value = base + sold - bought + bonus
            # Every term, not just the answer — checkable against the ledger
            # without re-deriving it.
            math = ("%s − %.2fM bought + %.2fM sold across %d ledger row(s)"
                    "%s = %.2fM"
                    % (basis, bought / 1e6, sold / 1e6, counted,
                       ((" + %.2fM of daily allowance over %.0f days"
                         % (bonus / 1e6, days)) if days is not None
                        else (" + %.2fM the app has paid you since the season "
                              "began, which it pays everyone" % (bonus / 1e6)))
                       if bonus else "",
                       value / 1e6))
            if value < 0:
                # A real, overdrawn position, not an input error — see the
                # module docstring.
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


    def unmatched(self, known_keys) -> list[str]:
        """Owned names absent from data/tidy — spelling to fix with
        find_slug.py. These are also why a naive free-agent count can go
        negative."""
        return sorted(k for k in self.owner if k not in known_keys)


def _now() -> str:
    return run_now().astimezone().strftime("%Y-%m-%d %H:%M")


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

    # -- the price the app puts on him, as a check on the NAME -------------
    # REAL, AND IT WAS PUTTING THE WRONG PLAYER IN A RIVAL'S SQUAD. The app
    # calls Carlos Romero "C. Romero"; the market carries Carlos, Isaac and
    # Iván Romero, and key_for confidently returned ISAAC. Nothing about the
    # name could tell — but the app states his value, 43.24M against Isaac's
    # 6.15M, and the two feeds price the same player to within a fifth of a
    # percent. A name join that disagrees about the money by six hundred
    # percent has found a different man.
    twins = Market([
        {"name": "Carlos Romero", "value": "43240000", "observed_at": at,
         "position": "DEF"},
        {"name": "Isaac Romero", "value": "6150000", "observed_at": at,
         "position": "DEL"}])
    # Left to the name alone this picks one of them and cannot say which.
    # With the price on the row it can: the nickname's answer is checked, and
    # rejected, and the full name is tried and agrees.
    assert api_key("C. Romero", "BurtonGM89", twins,
                   market_value="43244323",
                   full="Carlos Romero") == norm("Carlos Romero")
    # A join nothing contradicts stands: no value stated, no check possible.
    assert api_key("Isaac Romero", "BurtonGM89", twins) == norm("Isaac Romero")
    assert api_key("Isaac Romero", "BurtonGM89", twins,
                   market_value="6150000") == norm("Isaac Romero")
    # AND AN EXACT NAME IS NEVER OVERRULED BY THE MONEY. The market carrying
    # that very spelling is the strongest evidence there is; if the app's
    # price disagrees, something else is wrong and quietly reassigning the
    # player is the worst available answer.
    assert api_key("Isaac Romero", "BurtonGM89", twins,
                   market_value="43244323") == norm("Isaac Romero")
    # THE TOLERANCE IS NOT A TUNED NUMBER. Across the 70 owned players that
    # joined on 2026-08-19 the two sources agreed to within 0.2% in every
    # case, and the one wrong join was out by 603% — three thousand times the
    # worst true disagreement. Anything between the two works; the prices are
    # read minutes apart and drift a little, so it is not zero.
    assert api_key("Isaac Romero", "BurtonGM89", twins,
                   market_value="6160000") == norm("Isaac Romero")
    # AND THE NICKNAME ALONE IS NOW ENOUGH, without the full name beside it.
    # This asserted None until 2026-08-20, and the None was a limitation
    # being written down as an intention: resolve() would answer ISAAC on a
    # substring that crossed a word boundary, _priced_like would reject him,
    # and with no full name, no app id and no ledger owner there was nothing
    # left to try. The boundary fix makes resolve() refuse instead of
    # guessing, and key_for then asks the price which of the candidates it
    # is — the same evidence this function already hands in, for this exact
    # reason. Carlos at 43.24M against a stated 43.24M; Isaac at 6.15M is not
    # close. Note what has NOT changed: nobody picks the cheaper of two
    # strangers, and a price agreeing with two of them still resolves to
    # neither.
    assert api_key("C. Romero", "BurtonGM89", twins,
                   market_value="43244323") == norm("Carlos Romero")

    # -- the id the crosswalk already resolved, once, and wrote down -------
    # REAL, AND STILL COSTING A PLAYER. The app calls Jonny Castro "Jonny
    # Otto" and his full name "Jonathan Castro Otto"; no spelling joins, and
    # the value tier finds nothing because the app's figure for him appears
    # nowhere in futbolfantasy's history — so the claim that the two match to
    # the euro does not hold for every player. Meanwhile players.csv HAS had
    # him since some earlier sweep resolved a market row: app_id 2552 ->
    # jonny castro. This join re-derived that from scratch every run and
    # failed, while the table that solved it sat unread beside it.
    #
    # It is an EXACT id, checked FIRST now: no derivation on the id itself,
    # only on the table's app_id -> key mapping, and Crosswalk.merge()'s
    # stale-id displacement is what protects that now, not join order here.
    lone = Market([{"name": "Jonny Castro", "value": "5602302",
                    "observed_at": at, "position": "DEF"}])
    assert api_key("Jonny Otto", "SusoGattuso", lone) is None
    assert api_key("Jonny Otto", "SusoGattuso", lone,
                   app_ids={"2552": norm("Jonny Castro")},
                   app_id="2552") == norm("Jonny Castro")
    # An id the table has never seen changes nothing.
    assert api_key("Jonny Otto", "SusoGattuso", lone,
                   app_ids={"9999": "somebody"}, app_id="2552") is None
    # NO TABLE IS NOT A FAILURE. players.csv is built BY this join, so on a
    # cold start it does not exist and every caller must degrade to what it
    # did before — additive only, never a dependency.
    assert api_key("Fornals", "miguel_autentico", players,
                   app_ids=None) == norm("Pablo Fornals")
    # AND THE ID NOW WINS OVER A NAME THAT WOULD OTHERWISE JOIN CLEANLY —
    # an intentional reversal (2026-08-21) of the priority this docstring
    # used to describe. The app row states an id; the id involves no
    # guessing, so it is trusted over the name outright, the same call
    # Crosswalk.resolve() makes for every caller.
    assert api_key("Jonny Castro", "SusoGattuso", lone,
                   app_ids={"2552": "someone else"},
                   app_id="2552") == "someone else"
    # And the loop passes the row's id through.
    owner, unjoined = owner_from_api(
        [{"manager": "SusoGattuso", "player_name": "Jonny Otto",
          "player_id": "2552"}], lone, app_ids={"2552": norm("Jonny Castro")})
    assert owner == {norm("Jonny Castro"): "SusoGattuso"}, owner
    assert unjoined == [], unjoined

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
                       "player_id": "1337",
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
    api_teams = [{"manager": "me", "player_name": "P", "observed_at": at}]
    # The balance is a fact about the TEAM and comes in at that grain now.
    table = [{"manager": "me", "user_id": "1", "team_id": "1",
              "team_money": "23596582", "observed_at": at}]

    lg = League(Config(me="me"), {"me": []}, txns, mkt, api_teams=api_teams,
                standings=table)
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
    lg2 = League(Config(me="me"), {"me": []}, later, mkt,
                 api_teams=api_teams, standings=table)
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
    api_odd = [{"manager": "me", "player_name": "A. Ferllo", "observed_at": at}]
    lg3 = League(Config(me="me"), {"me": ["P"]}, [], None,
                 api_teams=api_odd, standings=table)
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

    # -- a roster line can name the club, and must when the name is two men --
    import tempfile as _tf
    with _tf.TemporaryDirectory() as d:
        f = pathlib.Path(d) / "r.txt"
        f.write_text("[me]\nalvaro garcia (Rayo)\npepelu\n", encoding="utf-8")
        got = read_rosters(f)["me"]
    # THE ONE FILE A HUMAN STILL TYPES A NAME INTO, and a name is not a
    # player: two Álvaro Garcías play in this league, 20.23M at Rayo and
    # 0.50M at Villarreal. The club in brackets becomes the same name@club
    # key the market index uses, so the roster and the app agree about which
    # man a rival owns instead of disagreeing in a warning.
    assert got == ["alvaro garcia@rayo", "pepelu"], got

    # -- what the app pays, measured on the one account that states a balance
    # THE FEED CANNOT SEE INCOME. Crediting rivals a bonus per day since the
    # first transfer was a guess at both the rate and the start, and it was
    # short: on 2026-08-19 the account's own balance implied 1.27M of income
    # while eight days of allowance came to 0.80M. Whatever the app is paying
    # — a daily award, a prize for a jornada — it pays it to everyone, so the
    # honest figure to credit a rival is the one your own balance proves.
    assert flat_income(8906184.0, 100e6, 142.18e6, 49.82e6) == 8906184.0 - (
        100e6 - 142.18e6 + 49.82e6)
    assert round(flat_income(8906184.0, 100e6, 142.18e6, 49.82e6)) == 1266184
    # Income only. A balance BELOW what the ledger explains is a ledger that
    # has missed a purchase, not the app taking money back, and inventing a
    # negative award would spread that error across four rivals.
    assert flat_income(1.0, 100e6, 0.0, 0.0) == 0.0
    # Nothing observed is no evidence, not zero evidence.
    assert flat_income(None, 100e6, 0.0, 0.0) is None

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
    # A REAL Market, not a stand-in. The stand-in had its own at() and its
    # own latest(), so it agreed with the real index only for as long as
    # somebody kept them in step — and it was the stand-in's row list, one
    # row per name, that hid the shared-name bug below from this very test.
    from ffcore.tidy import Market as _RealMarket
    _seen = "2026-08-01T0000Z"
    mkt = _RealMarket([
        # Two Cardosos, an order of magnitude apart — the real collision.
        {"name": "Fabio Cardoso", "team": "Alaves", "value": "925408",
         "observed_at": _seen},
        {"name": "Johnny Cardoso", "team": "Betis", "value": "6306919",
         "observed_at": _seen},
        {"name": "Dani Lorenzo", "team": "Betis", "value": "4000000",
         "observed_at": _seen},
        {"name": "Dani Martinez", "team": "Levante", "value": "500000",
         "observed_at": _seen}])
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

    # A NAME TWO MEN SHARE MUST COME BACK AS TWO CANDIDATES, AND THE KEY
    # MUST BE THE MARKET'S OWN. This resolved over a raw list of rows, whose
    # exact-match index holds one row per NAME — so of the two Álvaro Garcías
    # it silently kept whichever was last and never reached the pruning at
    # all. Worse, it then keyed the answer norm(name): "alvaro garcia", which
    # is not a key the market has, because the market splits shared names on
    # club. A key that exists nowhere misses every lookup downstream.
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
    # Nobody's man, so nothing prunes: the row is left alone rather than
    # assigned to whichever of them the index happened to hold.
    key, why = identify({"player": "Álvaro García", "from": "bob",
                         "to": MARKET}, owner2, twins2)
    assert key == norm("Álvaro García") and why == "", (key, why)
    # The two men are two KEYS now, and they are the site's own ids — there
    # is no name@club to construct because there is no collision to resolve.
    assert sorted(twins2.latest()) == ["12993", "867"]

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

    # -- _roster_key: an untransacted roster player still gets the
    # -- market's OWN key (usually numeric), not a bare norm() ------------
    # Real market rows carry ff_id on every one of them (row_key()'s own
    # docstring: 44,912 checked, all present) — a hand-fixture without it
    # falls back to a name key and would not have caught this. This one
    # has it, on purpose.
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
    # NOT "raul moro" — the market's own numeric id, the same key everything
    # else in this repo (identify(), the crosswalk, api_key()) resolves him
    # to. A plain norm() key here is exactly what left him permanently
    # unreconciled and reading as unowned the moment the app feed a report
    # relies on for the OVERWRITE (League.__init__) is down.
    assert id_owner == {"7870": "laporta"}, id_owner
    # THE SHARED-NAME CASE, disambiguated by club — read_rosters() already
    # folds "Alvaro Garcia (Rayo)" into "alvaro garcia@rayo" before this
    # ever sees it; the club has to reach key_for()'s own disambiguator or
    # the ambiguous name resolves to neither man.
    assert _roster_key("alvaro garcia@rayo", id_mkt) == "867"
    assert _roster_key("alvaro garcia@villarreal", id_mkt) == "12993"
    # No market at all: falls back to norm(), same as always, rather than
    # crashing or dropping the entry.
    assert _roster_key("Raul Moro", None) == "raul moro"
    # THE COMMON CASE NOW: a bare id, verbatim, no resolution at all — not
    # even a market lookup. This is the whole point of the 2026-08-21
    # migration to an id-per-line file: nothing left to drift.
    assert _roster_key("7870", id_mkt) == "7870"
    assert _roster_key("7870", None) == "7870"
    assert _roster_key(" 7870 ", id_mkt) == "7870"
    # A name the market has never heard of: still norm(), not lost.
    assert _roster_key("Nobody At All", id_mkt) == "nobody at all"

    # -- _roster_key: the market's CURRENT spelling has moved on, but the
    # -- crosswalk still holds the roster's spelling as an app_name -------
    # "Manuel Fernández", typed at the season's start, is nobody's
    # key_for() match once the market shows him as "Manu Fernandez" — real
    # case, 2026-08-21. xw.player(app_name=...) is what identify() itself
    # would have resolved this same string to, via the app's own activity
    # feed; a roster entry deserves the same answer.
    from ffcore.crosswalk import Crosswalk, Player

    xw_fern = Crosswalk({"16003": Player("16003", "Manu Fernandez",
                                         app_names={"Manuel Fernández"})}, {})
    empty_mkt = _RealMarket([{"name": "Manu Fernandez", "ff_id": "16003",
                              "team": "Celta", "value": "500000",
                              "observed_at": _seen}])
    assert _roster_key("Manuel Fernández", empty_mkt, xw_fern) == "16003"
    # key_for() still wins when BOTH would answer — the market's current
    # spelling is the trustworthy side of this join everywhere else in the
    # repo, checked first for a reason.
    assert _roster_key("Manu Fernandez", empty_mkt, xw_fern) == "16003"
    # Neither market nor app_name has heard of him: still norm(), not lost.
    assert _roster_key("Total Stranger", empty_mkt, xw_fern) == "total stranger"

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
