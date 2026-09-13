"""
ffcore.tidy — reading what ff_ingest wrote, and asking it about the past.

Paths, CSV IO and timestamp parsing, consolidated out of report.py/offers.py/
find_slug.py/history.py. `Market` is the part that matters: an index over
EVERY snapshot in market.csv, not just the newest, answering questions
`latest_only()` cannot —

    what was this player worth when that transaction happened?   at()
    what did his value do in the fortnight after?                series()
    how stale is the reading I just used?                        Valuation.lag_h

TIMEZONES: ledger dates are Madrid wall-clock, snapshot stamps are UTC, two
hours apart in summer — `ledger_stamp()`/`snapshot_stamp()` both return
aware UTC. Why: docs/notes/tidy.md
"""

from __future__ import annotations

import csv
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import NamedTuple

from ffcore.parse import money, pct100
from ffcore.text import index_by, norm, resolve

__all__ = ["ROOT", "TIDY", "SEASON", "DECISIONS", "REPORTS", "PARTS", "MADRID",
           "input_path", "read_csv", "write_csv", "append_csv", "widen_csv",
           "write_lines", "snapshot_stamp", "ledger_stamp", "latest_only", "snapshots",
           "Market", "Valuation", "VALUE_TOLERANCE", "price_agrees",
           "load_market", "load_market_frozen", "load_lineups",
           "shared_names", "row_key", "run_now", "load_crosswalk",
           "load_players", "read_ledger", "LEDGER", "load_deadline", "LINEUP_SOURCE",
           "pick_source", "load_fixtures", "next_kickoff", "kickoff_stamp",
           "load_elo", "load_results_history", "load_understat_players",
           "MATCH_LEN", "minutes_played", "fresh_only", "DAILY_FRESH_DAYS",
           "EVERY_RUN_FRESH_DAYS", "stale_feeds",
           "GATED_API", "age_phrase", "last_api_standings",
           "load_api_lineup", "market_routes", "pending_sent", "bought_price",
           "pending_received", "LISTED_SELLER", "team_slug_of", "match_locks",
           "jornada_locks", "lock_order"]

ROOT = Path(os.environ.get("FF_ROOT", "./data"))
TIDY = ROOT / "tidy"
SEASON = ROOT / "season"
DECISIONS = ROOT / "decisions"
# WHAT THE SITE GETS, and nothing else — reports/ holds only what's published.
# LFG_REPORTS (like FF_ROOT/LFG_PARTS below) lets a test/profiling run point
# every writable path at a scratch directory instead of the tracked tree.
REPORTS = Path(os.environ.get("LFG_REPORTS", "reports"))

# Render fragments the appendix is stitched from — build artifacts under
# .runtime/, untracked and unpublished.
PARTS = Path(os.environ.get("LFG_PARTS", ".runtime/parts"))


def _madrid():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("Europe/Madrid")
    except Exception:                                    # pragma: no cover
        return timezone(timedelta(hours=2))  # CEST, good enough Mar-Oct


MADRID = _madrid()


# ---------------------------------------------------------------------------
# files
# ---------------------------------------------------------------------------

def input_path(name: str) -> Path:
    """Locate an editable input file. Prefers inputs/<name>; falls back to
    the repo root so a half-finished move doesn't break the run."""
    p = Path("inputs") / name
    return p if p.exists() else Path(name)


# One parse per file per process, keyed on (mtime, size).
# Why: docs/notes/tidy.md
_READ_CACHE: dict[str, tuple] = {}


def _forget(path) -> None:
    _READ_CACHE.pop(str(Path(path)), None)


def read_csv(path) -> list[dict]:
    """Rows as dicts. Missing file is empty, not an error.

    Cached per (mtime, size); callers get their own dict copy each call.
    Cell values are interned at parse time. Uses csv.reader+zip rather
    than DictReader (faster; no ragged-row handling needed here).
    Why: docs/notes/tidy.md#read_csv--the-parse-cache-its-isolation-and-interning
    """
    path = Path(path)
    try:
        st = path.stat()
    except OSError:
        return []
    hit = _READ_CACHE.get(str(path))
    if hit is None or hit[0] != (st.st_mtime_ns, st.st_size):
        with path.open(encoding="utf-8") as fh:
            r = csv.reader(fh)
            try:
                fieldnames = next(r)
            except StopIteration:
                fieldnames = []
            intern = sys.intern
            rows = [dict(zip(fieldnames, map(intern, row)))
                    for row in r if row]
        hit = ((st.st_mtime_ns, st.st_size), rows)
        _READ_CACHE[str(path)] = hit
    return [dict(r) for r in hit[1]]


def read_csv_frozen(path) -> list:
    """Like read_csv(), but hands back the cached rows themselves, each
    wrapped in MappingProxyType rather than copied — for a caller (Market,
    via ffcore.model's Session) that holds the result for the rest of the
    process and never writes to a row. A write attempt is a loud TypeError.
    Why: docs/notes/tidy.md#read_csv_frozen--the-uncopied-path-for-one-long-lived-caller
    """
    path = Path(path)
    try:
        st = path.stat()
    except OSError:
        return []
    hit = _READ_CACHE.get(str(path))
    if hit is None or hit[0] != (st.st_mtime_ns, st.st_size):
        read_csv(path)
        hit = _READ_CACHE[str(path)]
    return [MappingProxyType(r) for r in hit[1]]


def write_csv(path, rows, fieldnames=None) -> None:
    path = Path(path)
    _forget(path)
    if not rows:
        return
    fieldnames = fieldnames or list(rows[0])
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore",
                           lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def widen_csv(path, fieldnames) -> bool:
    """Add columns to an existing log, in place. True if the file was
    rewritten. Rewrites old rows with the new header and "" for what was
    never recorded, so a grown column list doesn't append rows wider than
    the header (which DictReader would silently truncate). Only ever
    widens — a column dropped from `fieldnames` is kept.
    """
    path = Path(path)
    if not path.exists():
        return False
    with path.open(encoding="utf-8") as fh:
        r = csv.DictReader(fh)
        have = list(r.fieldnames or [])
        if not [c for c in fieldnames if c not in have]:
            return False
        rows = list(r)
    cols = have + [c for c in fieldnames if c not in have]
    write_csv(path, [{c: row.get(c, "") for c in cols} for row in rows], cols)
    return True


def append_csv(path, rows, fieldnames=None) -> None:
    """Append, writing the header only when creating the file — for
    decision logs (squad_log.csv, etc.) whose estimates can't be
    reconstructed later.
    """
    path = Path(path)
    _forget(path)
    if not rows:
        return
    fieldnames = fieldnames or list(rows[0])
    fresh = not path.exists()
    if not fresh:
        # The file's header wins, not the caller's — appending out of
        # order would misalign values with nothing to notice. Use
        # widen_csv() first to add a column.
        with path.open(encoding="utf-8") as fh:
            fieldnames = list(csv.DictReader(fh).fieldnames or fieldnames)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore",
                           restval="", lineterminator="\n")
        if fresh:
            w.writeheader()
        w.writerows(rows)


def load_deadline(with_source: bool = False):
    """The next JORNADA lock as aware UTC, or None — the earliest still-ahead
    entry in jornada_locks(), since the app locks the whole round at its
    first kickoff (Sunday's player is already frozen at Friday's kickoff).

    NOT next_kickoff(): a round already under way can still have its own
    later matches listed in fixtures.csv, staggered days apart (a TV
    reschedule, or a jornada whose matches simply spread over a long
    weekend) — those are leftovers of an already-locked round, not a fresh
    deadline. Using next_kickoff() here reported a lock minutes away for a
    match that was really just one of jornada 5's remaining fixtures, days
    after jornada 5 itself had locked (2026-09-13).

    No typed fallback: an unanswerable fixture/match list says None, never a
    wrong substitute. `with_source=True` returns (when, "fixtures"|"none").
    Why: docs/notes/tidy.md#load_deadline--the-fixture-list-is-the-deadline-no-typed-fallback
    """
    now = run_now()
    locks = jornada_locks(read_csv(TIDY / "matches.csv"), load_fixtures())
    ahead = [t for t in locks.values() if t > now]
    when = min(ahead) if ahead else None
    return (when, "fixtures" if when else "none") if with_source else when


def write_lines(path, lines) -> None:
    """Write a markdown report. Every report script had this inline."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    print("wrote %s" % path)


# ---------------------------------------------------------------------------
# time
# ---------------------------------------------------------------------------

@lru_cache(maxsize=4096)
def _digits_to_dt(s: str, tz):
    # Cached: a snapshot log's observed_at column is a few hundred distinct
    # stamps repeated over tens of thousands of rows.
    digits = re.sub(r"\D", "", s or "")
    if len(digits) < 8:
        return None
    try:
        return datetime(
            int(digits[:4]), int(digits[4:6]), int(digits[6:8]),
            int(digits[8:10]) if len(digits) >= 10 else 0,
            int(digits[10:12]) if len(digits) >= 12 else 0,
            tzinfo=tz)
    except ValueError:
        return None


def snapshot_stamp(s: str):
    """observed_at -> aware UTC. Tolerates 2026-08-12T2100Z, ...T21:00Z,
    and a bare date."""
    return _digits_to_dt(s, timezone.utc)


def ledger_stamp(s: str):
    """A ledger date -> aware UTC.

    The string is Madrid wall-clock because that is what the app displayed
    when you copied it. Read as UTC it would be two hours early all summer.
    """
    local = _digits_to_dt(s, MADRID)
    return local.astimezone(timezone.utc) if local else None


# ---------------------------------------------------------------------------
# snapshots
# ---------------------------------------------------------------------------

def latest_only(rows: list[dict]) -> list[dict]:
    """Just the newest snapshot. Correct for 'what can I buy now', wrong for
    anything historical — use Market for that."""
    if not rows:
        return []
    newest = max(r.get("observed_at", "") for r in rows)
    return [r for r in rows if r.get("observed_at") == newest]


def latest_snapshot(path, keep=None) -> list[dict]:
    """`latest_only(read_csv(path))`, in one bounded-memory forward pass —
    a caller that only wants "now" shouldn't materialise the whole
    multi-decade history to get it. Bypasses the read cache deliberately
    (a filtered slice under the whole file's cache key would mislead a
    later full-history reader). `keep(row)` filters BEFORE the
    newest-stamp comparison, for "newest from one source" rather than
    across all of them.
    Why: docs/notes/tidy.md#latest_snapshot--one-forward-pass-bounded-memory
    """
    path = Path(path)
    try:
        fh = path.open(encoding="utf-8")
    except OSError:
        return []
    with fh:
        r = csv.reader(fh)
        try:
            fieldnames = next(r)
        except StopIteration:
            fieldnames = []
        newest = ""
        kept: list[dict] = []
        # Raw csv.reader + zip, not DictReader — still has to walk the
        # whole file to find the newest stamp, so per-row overhead matters
        # even though memory is what's being saved.
        for raw in r:
            if not raw:
                continue
            row = dict(zip(fieldnames, raw))
            if keep is not None and not keep(row):
                continue
            stamp = row.get("observed_at", "")
            if stamp > newest:
                newest, kept = stamp, [row]
            elif stamp == newest:
                kept.append(row)
        return kept


# Caches the result, not the file — load_market_latest()/load_lineups_latest()
# are each called several times per run.py process for an answer that can't
# change mid-run. `cache_key` disambiguates callers that pass a fresh `keep`
# lambda every call; same mtime+size invalidation as read_csv()'s cache.
# Why: docs/notes/tidy.md#_cached_latest_snapshot--cache-the-small-result-not-the-whole-file
_LATEST_SNAPSHOT_CACHE: dict[tuple, tuple] = {}


def _cached_latest_snapshot(path, keep=None, cache_key=None) -> list[dict]:
    path = Path(path)
    try:
        st = path.stat()
    except OSError:
        return []
    stamp = (st.st_mtime_ns, st.st_size)
    key = (str(path), cache_key)
    hit = _LATEST_SNAPSHOT_CACHE.get(key)
    if hit is None or hit[0] != stamp:
        hit = (stamp, latest_snapshot(path, keep=keep))
        _LATEST_SNAPSHOT_CACHE[key] = hit
    return [dict(r) for r in hit[1]]


# How long a DAILY feed may go unanswered before its last reading stops being
# today's. The sweep runs twice a day, so missing both of a day's sweeps is not
# a cadence — it is a feed that has stopped. src/methodology.py prints "stale"
# off this same number, so the table and the refusal below cannot disagree.
DAILY_FRESH_DAYS = 1.05

# Not 0.5, however obvious that looks — lfg.timer's two legs run 11h/13h
# apart, so a healthy feed is 13h10m old at its oldest. Why:
# docs/notes/tidy.md#gated-api-feeds--one-shared-reason-not-five-copies-of-it
EVERY_RUN_FRESH_DAYS = 0.6


_NOW: list = []


def run_now() -> datetime:
    """The instant this RUN is describing. Sampled once per process, so
    every stage stamps the same instant (was 25 call sites asking the clock
    separately, which made two runs of unchanged code produce undiffable
    output). Not named `now` — that's shadowed by fresh_only/stale_feeds'
    own `now` parameter. Not for the sweep (ingest.py keeps the live clock).
    `LFG_NOW` pins it for a byte-identical before/after diff — a measuring
    tool, unset in every real run. Why: docs/notes/tidy.md#run_now--one-clock-per-run
    """
    if not _NOW:
        pinned = os.environ.get("LFG_NOW", "").strip()
        _NOW.append(snapshot_stamp(pinned) if pinned
                    else datetime.now(timezone.utc))
    return _NOW[0]


def fresh_only(rows: list[dict], max_age_days: float, now=None) -> list[dict]:
    """`rows` if the newest of them is recent enough, [] if it is not.

    A FEED THAT STOPS ANSWERING DOES NOT LOOK BROKEN ANYWHERE. Its last rows
    stay in the tidy store, they still parse, they still join, and every
    reader treats them as current. Nothing but the stamp can tell the
    difference, so the stamp is checked here rather than trusted by each
    caller in turn.

    A reading in the future is not stale: a clock a few minutes out is a
    machine problem, and throwing away good data over it would be worse than
    the skew.
    """
    if not rows:
        return []
    when = snapshot_stamp(max(r.get("observed_at", "") for r in rows))
    if when is None:
        return []
    now = now or run_now()
    return rows if (now - when).total_seconds() <= max_age_days * 86400 else []


def snapshots(rows: list[dict]) -> list[str]:
    """Distinct observed_at values, oldest first."""
    return sorted({r.get("observed_at", "") for r in rows if r.get("observed_at")})


def load_market() -> list[dict]:
    return read_csv(TIDY / "market.csv")


def load_market_frozen() -> list:
    """`load_market()`, uncopied — see read_csv_frozen(). For Market's own
    constructor, the one caller verified never to write into a row."""
    return read_csv_frozen(TIDY / "market.csv")


def load_market_latest() -> list[dict]:
    """`latest_only(load_market())`, in the low-memory way — see
    latest_snapshot()."""
    return _cached_latest_snapshot(TIDY / "market.csv")


# Which probable-XI site the reports are built on. The lineups table can hold
# several; every reader gets exactly one, chosen here, because two sites that
# disagree about a starter must not be silently averaged or racing on file
# order. Compare them against outcomes before changing this.
LINEUP_SOURCE = "futbolfantasy"


def load_lineups(source: str = LINEUP_SOURCE) -> list[dict]:
    """The lineups table, one source only.

    The filter is here rather than in Scorer so that no reader can forget it:
    a caller that skipped it would get one player's row twice, from two sites,
    with different start percentages. Pass source="" to read every row — for
    comparing sources, which is the one job that wants them all.
    """
    return pick_source(read_csv(TIDY / "lineups.csv"), source)


def load_lineups_latest(source: str = LINEUP_SOURCE) -> list[dict]:
    """`latest_only(load_lineups(source))`, in the low-memory way — see
    latest_snapshot(). The source filter runs BEFORE the newest-stamp
    comparison, so "newest" means newest row from this one source, same as
    `latest_only(pick_source(...))` would answer."""
    keep = (lambda r: r.get("source") == source) if source else None
    return _cached_latest_snapshot(TIDY / "lineups.csv", keep=keep,
                                   cache_key=source)


def pick_source(rows: list[dict], source: str) -> list[dict]:
    """One source's rows. An empty `source` means all of them."""
    return rows if not source else [r for r in rows
                                    if r.get("source") == source]


# ---------------------------------------------------------------------------
# fixtures — what makes the deadline derivable
# ---------------------------------------------------------------------------

def kickoff_stamp(s: str):
    """A published kickoff -> aware UTC, or None.

    fromisoformat, not this module's digit parser: the value comes from
    someone else's page with an explicit offset on the end, and the digit
    parser would read a future "+02:00" as if it were UTC — an error of
    exactly the size that makes a locked squad look editable.
    """
    try:
        when = datetime.fromisoformat((s or "").strip())
    except ValueError:
        return None
    return (when.replace(tzinfo=timezone.utc) if when.tzinfo is None
            else when.astimezone(timezone.utc))


def load_elo(now=None) -> list[dict]:
    """The newest Club Elo reading, or [] until one has been fetched — and []
    again once the newest one is too old to be about today's teams.

    [] is not a failure and callers must not treat it as one: ffcore.fixture
    falls back to squad value, which is what ranked the teams before this
    existed.

    THE GATE IS THE POINT. Club Elo's API host died on 2026-08-17 and this
    returned the same twenty ratings for two days; they covered every club, so
    `elo_strength` succeeded and the fixture board ranked the league on form
    from before the jornada. Falling back to the wallet is a worse ranking and
    an honest one. This is the ONE place ratings are loaded — score.py builds
    the board for your squad and every rival's from this call — so the gate
    belongs here and not in the readers.
    """
    return fresh_only(latest_only(read_csv(TIDY / "elo.csv")),
                      DAILY_FRESH_DAYS, now)


# The API tables that are a SNAPSHOT of now, and so are gated above. Listed
# once, because both the refusal and the sentence that explains it have to be
# about the same set of feeds.
GATED_API = ("api_teams", "api_market", "api_standings",
             "api_lineup", "api_offers")


def age_phrase(days: float) -> str:
    """How old, in the coarsest unit that is still true.

    Minutes below the hour, because "fetched 0 hours ago" is what a table
    printed for every page in a sweep that had just finished.
    """
    def unit(n: float, word: str) -> str:
        return "%.0f %s%s" % (n, word, "" if round(n) == 1 else "s")

    if days >= 2:
        return unit(days, "day")
    if days * 24 >= 1:
        return unit(days * 24, "hour")
    return unit(max(1, round(days * 1440)), "minute")


def stale_feeds(now=None, names=GATED_API) -> dict[str, float]:
    """{table: how many days old} for each gated feed that has gone quiet.

    The refusal ([] from a gated loader) isn't the whole job — [] reads
    downstream as "nothing there", not "this feed went quiet", so this
    names WHICH feed and how old. A table never written is absent, not
    "quiet" (callers already have a sentence for that). Why:
    docs/notes/tidy.md#gated-api-feeds--one-shared-reason-not-five-copies-of-it
    """
    now = now or run_now()
    out = {}
    for name in names:
        rows = read_csv(TIDY / f"{name}.csv")
        when = snapshot_stamp(max((r.get("observed_at", "") for r in rows),
                                  default=""))
        if when is None:
            continue
        age = (now - when).total_seconds() / 86400.0
        if age > EVERY_RUN_FRESH_DAYS:
            out[name] = age
    return out


def load_api_teams(now=None) -> list[dict]:
    """The newest squad reading from the league's own API, or [].

    One row per player per squad. [] means the API has not answered recently
    enough to be about today's squads — never fetched, no token, or the sweep
    has been failing — and every caller must degrade to the ledger rather than
    treat it as an empty league.

    GATED, for the reason load_elo is. A dead token does not empty this file;
    the tidy store keeps the last good reading for ever, so the failure mode
    is a squad that is three days old, joins perfectly, and prices a market
    that has moved. That is the stale Elo rating and the stale cash anchor a
    third time, and three is where it stops being a coincidence.
    """
    return fresh_only(latest_only(read_csv(TIDY / "api_teams.csv")),
                      EVERY_RUN_FRESH_DAYS, now)


def _activity_order(r: dict):
    """(when it happened, which one) — a total order over the feed.

    The app stamps a whole day's deals with the same minute, so sorting on the
    stamp alone leaves ties to be broken by whatever order the rows happened
    to be read in. That was invisible while the store held a fresh copy of the
    feed per sweep and is not now, and an arbitrary order in a file this repo
    commits is a diff every run that means nothing. The id is the app's own
    sequence, so it breaks the tie chronologically — read as an integer,
    because as text "15676725" sorts before "9629986".
    """
    raw = (r.get("activity_id") or "").strip()
    return (r.get("at") or "", int(raw) if raw.isdigit() else 0)


def load_api_activity() -> list[dict]:
    """The league's transaction feed, oldest first, or [].

    Sorted by the app's own timestamp rather than by observation, because this
    is a history: the order that matters is the order the deals happened.

    NOT latest_only, and that is load-bearing. It used to be, and it worked
    only because the feed republished every event on every sweep and the store
    kept every copy — so "the newest snapshot" happened to contain the whole
    season. That storage was quadratic and is gone (sources.STORE_ONCE), which
    makes this file what it always described itself as: an event log, read
    whole. Reading only the newest rows here would now hand the ledger the
    handful of deals done since the last sweep and delete the rest of the
    season from it.
    """
    return sorted(read_csv(TIDY / "api_activity.csv"), key=_activity_order)


def load_api_market(now=None) -> list[dict]:
    """What is on offer in the league right now, or [].

    "Right now" is the whole claim, so it is gated: a listing that expired
    yesterday is not an opportunity, and a bid count from a stale sweep is a
    number about a market that has since closed.
    """
    return fresh_only(latest_only(read_csv(TIDY / "api_market.csv")),
                      EVERY_RUN_FRESH_DAYS, now)


def load_api_standings(now=None) -> list[dict]:
    """The newest league table from the app, one row per team, or [].

    Gated on freshness: this row carries your BALANCE, and league.py anchors
    the cash estimate on it in preference to anything typed. An anchor that
    calls itself observed while being three days old is the exact bug the
    allowance fix was about, with the app in the typist's chair.

    Position, points, squad value and — for your account alone — the balance.
    This used to ride on every player row in api_teams: five managers' worth
    of team facts repeated 76 times a sweep. A fact about a team belongs at
    the grain of a team, which is also what makes the season's standings
    readable without deduplicating a player table.
    """
    return fresh_only(latest_only(read_csv(TIDY / "api_standings.csv")),
                      EVERY_RUN_FRESH_DAYS, now)


def last_api_standings() -> list[dict]:
    """The newest league table there is, however old.

    FOR THE COLUMNS THAT ARE HISTORY, and only those: points scored and
    position reached only ever grow, so a three-day-old reading of them is
    incomplete rather than wrong. The balance on the same row is not like
    that and must come through the gated reader — read_api_balances does.

    This exists because gating the whole row zeroed `carried` and made the
    season simulation project every manager from nought, which is a worse
    answer than the stale one it replaced.
    """
    return latest_only(read_csv(TIDY / "api_standings.csv"))


def load_api_lineup(now=None) -> list[dict]:
    """The eleven you have fielded, as the app holds it, or [].

    ONE ROW PER MAN, with the slot he is in and the formation the app itself
    states. Gated on freshness like the other snapshots: a lineup from three
    days ago is a lineup for a round already played, and reading it as
    "what you are fielding" is how a change list would tell you to take off a
    man you have already taken off.

    [] means the API has not answered recently enough, and there is no second
    source: inputs/lineup.txt held this by hand until 2026-08-19 and was
    deleted, because the only runs that ever read it were runs where a sale
    had already made it wrong.
    """
    return fresh_only(latest_only(read_csv(TIDY / "api_lineup.csv")),
                      EVERY_RUN_FRESH_DAYS, now)


def load_api_offers(now=None) -> list[dict]:
    """Who wants to buy a player you have listed, right now, or [].

    One row per player YOU HOLD, even when nothing is pending for him — see
    sources.parse_api_offer's own note on why a placeholder row is what
    keeps this gate meaningful for a table this sparse. A row with an empty
    `status` is that placeholder, not an offer; callers filtering for
    `status == "pending"` never need to know the difference exists.

    Gated exactly like the other API tables: an offer read three days ago
    is not an offer today, and treating it as one would show a shortfall
    closed by money that may have already been withdrawn or accepted.
    """
    return fresh_only(latest_only(read_csv(TIDY / "api_offers.csv")),
                      EVERY_RUN_FRESH_DAYS, now)



def load_api_players() -> dict[str, str]:
    """{player id: the app's name for him} — the feed's missing half.

    NOT latest_only: this is a lookup table that only ever grows, and a player
    sold weeks ago is exactly the one the activity feed still mentions and
    nothing else can name. Keeping only the newest snapshot's rows would throw
    away the names this table exists to hold.
    """
    out = {}
    for r in read_csv(TIDY / "api_players.csv"):
        if r.get("player_id") and r.get("player_name"):
            out[r["player_id"]] = r["player_name"]
    return out


def load_crosswalk():
    """The identifier table, or None before the crosswalk stage has run.

    None rather than an empty Crosswalk: "I have no id table" and "the id
    table knows nothing" lead to the same fallbacks today, but only one of
    them is a state worth seeing in a warning.
    """
    from ffcore.crosswalk import Crosswalk
    path = TIDY / "players.csv"
    if not path.exists():
        return None
    return Crosswalk.read(path, TIDY / "clubs.csv")


def load_fixtures() -> list[dict]:
    """The newest fixtures reading, earliest kickoff first."""
    rows = latest_only(read_csv(TIDY / "fixtures.csv"))
    return sorted(rows, key=lambda r: r.get("kickoff") or "")


def load_results_history() -> list[dict]:
    """Every match football-data.co.uk has ever recorded that this repo has
    fetched — several seasons, not a latest snapshot, so NOT latest_only():
    a result does not go stale and does not get superseded by a later one,
    it just accumulates. STORE_ONCE (sources.STORE_ONCE) already keeps this
    table from duplicating itself run over run.
    """
    return read_csv(TIDY / "results_history.csv")


_UNDERSTAT_CACHE: dict[tuple, tuple] = {}


def load_understat_players(season: str = "") -> list[dict]:
    """Player-level xG/xA (sources.parse_understat_players), the newest
    reading for each (season, understat_id) pair.

    Not wired into forecasting yet — captured deliberately unused, verified
    against real data first. `season` filters to one label ("2026" for
    2026/2027); "" returns every season (for prior-vs-current comparisons).
    Cached per (season, mtime, size) — score.build() otherwise re-filters/
    re-sorts 39,606 rows up to 5x per session for an unchanged answer. Why:
    docs/notes/tidy.md#load_understat_players--per-season-cache
    """
    path = TIDY / "understat_players.csv"
    try:
        st = path.stat()
    except OSError:
        return []
    key = (season, st.st_mtime_ns, st.st_size)
    hit = _UNDERSTAT_CACHE.get(key)
    if hit is None:
        rows = read_csv(path)
        if season:
            rows = [r for r in rows if r.get("season") == season]
        latest: dict[tuple, dict] = {}
        for r in sorted(rows, key=lambda r: r.get("observed_at", "")):
            k = (r.get("season"), r.get("understat_id"))
            if k[1]:
                latest[k] = r
        hit = tuple(latest.values())
        _UNDERSTAT_CACHE[key] = hit
    return [dict(r) for r in hit]


# Approximated, not measured: stoppage time is not on the page a starter's
# off-minute comes from, so a man who plays the whole match is credited this
# rather than the 94 or so he may actually have been on for. The same small
# error for everyone uncredited with a substitution, so it does not distort
# ranking between them — a constant offset, not noise.
MATCH_LEN = 90.0


def minutes_played(role: str, raw_minute, match_len: float = MATCH_LEN) -> float:
    """One player's minutes in one match, from starters.csv's own columns.

    The same column read in opposite directions: a STARTER's `minute` is
    when he came OFF (blank = whole match); a SUB's is when he came ON
    (blank = never). 0.0 for any other role. Why:
    docs/notes/tidy.md#minutes_played--one-column-read-in-opposite-directions
    """
    raw = (raw_minute or "").strip()
    if role == "starter":
        mins = float(raw) if raw else match_len
    elif role == "sub":
        mins = (match_len - float(raw)) if raw else 0.0
    else:
        return 0.0
    return max(0.0, mins)


def next_kickoff(now=None):
    """The first kickoff of ANY listed match still ahead of us, or None if we
    cannot tell — NOT the transfer deadline (see jornada_locks() for that):
    a jornada that has already started can still have later matches of its
    own listed here, staggered days apart.

    None covers three cases that must all fall back rather than guess: no
    fixtures file yet, an unparseable kickoff, and every listed match already
    started (their page drops a match once it is under way, so a stale file
    goes quiet rather than stale-and-confident).
    """
    now = now or run_now()
    ahead = [k for k in (kickoff_stamp(r.get("kickoff"))
                         for r in load_fixtures()) if k and k > now]
    return min(ahead) if ahead else None


def team_slug_of(side: str, slugs) -> str | None:
    """Our team slug for a fixture-page side name, or None.

    "Racing Santander" -> "racing", "Real Betis" -> "betis". The two sites
    spell clubs differently and neither publishes an id the other uses, so this
    is the same exact-then-substring, two-candidates-is-nothing rule the
    fixture board joins on — reused rather than reimplemented.
    """
    from ffcore.fixture import match_team

    spelled = {s.replace("-", " "): s for s in slugs}
    hit = match_team(side, list(spelled))
    return spelled.get(hit) if hit else None


def match_locks(matches: list[dict],
                fixtures: list[dict]) -> dict[tuple[int, str], datetime]:
    """{(jornada, team_slug): that TEAM's own kickoff in that jornada}.

    Jornada membership (matches.csv) is fixed; kickoff date isn't, and a
    TV reschedule can defer one fixture days past its round's other
    kickoffs. A caller needing "when did THIS PLAYER's match lock" must
    key on his own team, not the round.
    Why: docs/notes/methodology.md#match_locks--one-fixture-can-be-deferred-out-of-its-own-jornada
    """
    jornada_of: dict[tuple[str, str], int] = {}
    for m in matches:
        try:
            jornada_of[(m["home"], m["away"])] = int(m["jornada"])
        except (KeyError, ValueError, TypeError):
            continue
    slugs = {s for pair_ in jornada_of for s in pair_}

    locks: dict[tuple[int, str], datetime] = {}
    for f in fixtures:
        when = kickoff_stamp(f.get("kickoff"))
        home = team_slug_of(f.get("home") or "", slugs)
        away = team_slug_of(f.get("away") or "", slugs)
        jor = jornada_of.get((home, away))
        if when is None or jor is None:
            continue
        for team in (home, away):
            key = (jor, team)
            if key not in locks or when < locks[key]:
                locks[key] = when
    return locks


def jornada_locks(matches: list[dict],
                  fixtures: list[dict]) -> dict[int, datetime]:
    """{jornada: earliest kickoff observed in it} — the ROUND's own lock:
    the app locks the whole lineup once per jornada, at its first kickoff,
    however much later a TV reschedule pushes some of its other matches
    (verified in-app — see ffcore.fixture.fixture_board()'s own docstring).
    A player-level cutoff for GRADING how much team news was available
    must use match_locks() instead, keyed on his own team's later kickoff.
    Derived from match_locks() (the min across that jornada's teams), not
    a second independent join over the same fixtures.
    """
    locks: dict[int, datetime] = {}
    for (jor, _team), when in match_locks(matches, fixtures).items():
        if jor not in locks or when < locks[jor]:
            locks[jor] = when
    return locks


def lock_order(locks: dict[int, datetime]) -> list[int]:
    """Jornadas ordered by when they actually locked, not by number — a
    rescheduled fixture can lock jornada 6 before jornada 4.
    """
    return [j for j, _ in sorted(locks.items(), key=lambda kv: kv[1])]


# Which tidy column feeds which report field, and how to read it. Named
# explicitly, per source: the old common.py guessed from a list of thirty
# candidate header names against every CSV in data/tidy, so a renamed column
# went missing quietly instead of failing where you could see it.
MARKET_FIELDS = [("team", "team", None), ("pos", "position", None),
                 ("value", "value", money), ("delta_1d", "delta_1d", money)]
XI_FIELDS = [("team", "team_slug", None), ("start", "start_pct", pct100),
             ("status", "status", None)]


def _merge(players: dict, rows: list[dict], name_col: str, fields,
           shared=(), club_of=None, by_ff_slug=None) -> dict:
    """Fold one source's rows into the player index. First writer of a field
    keeps it, so market's `team` beats the XI page's `team_slug` and a
    duplicated name inside one snapshot doesn't flap.

    `shared` are the names two players answer to. A row carrying one of them
    is filed under name@club — and a row from a source that cannot say which
    club is DROPPED rather than folded into one of them, which is what used
    to put a Villarreal reserve's price on a rival's 20M defender.
    """
    for r in rows:
        # ONE KEYING RULE, THE SAME ONE THE MARKET INDEX USES. This built its
        # own — norm(name), then name@club for the shared ones — so the two
        # agreed only for as long as somebody kept them in step. row_key
        # answers with the site's own id where the row carries one.
        key = row_key(r, shared) if r.get("ff_id") else ""
        if not key:
            # A source with no id of the market's kind: the probable-XI pages
            # key players by name-slug, a different namespace entirely (zero
            # of 512 overlap the market's numeric ids), so the crosswalk is
            # what carries one to the other.
            key = (by_ff_slug or {}).get(
                norm(r.get("player_slug") or "")) or ""
        if not key:
            key = norm(r.get(name_col))
            if key in shared:
                club = _club(r) or (club_of or {}).get(
                    norm(r.get("team_slug") or ""), "")
                if not club:
                    continue
                key = "%s@%s" % (key, club)
        if not key:
            continue
        rec = players.setdefault(key, {})
        rec.setdefault("name", (r.get(name_col) or "").strip())
        for field, col, parse in fields:
            if field in rec:
                continue
            raw = r.get(col)
            if raw in (None, ""):
                continue
            val = parse(raw) if parse else str(raw).strip()
            # A field that won't parse is left unset rather than set to None,
            # so fmt_money prints an em dash instead of a fake zero.
            if val is not None:
                rec[field] = val
    return players


def load_players() -> dict[str, dict]:
    """{normalised name: {name, team, pos, value, delta_1d, start, status}}.

    The NEWEST snapshot of each source, and only that. common.py used to
    take the newest non-empty value per field across all snapshots, which
    kept a player who had left the market alive forever on his last recorded
    value — and, worse, kept a stale `start` for anyone missing from the
    latest XI read. On the 29 snapshots stored when this changed, the two
    agreed on all 655 current players and differed only by five departed
    ones, none of which reached any report.
    """
    market, xi = load_market_latest(), _cached_latest_snapshot(TIDY / "lineups.csv")
    if not market and not xi:
        raise SystemExit("no rows in %s — run `ingest.py parse` first" % TIDY)
    shared = shared_names(market)
    # The probable-XI feeds name a club by slug, the market by name; the
    # crosswalk holds both, so it is what lets an XI row for a shared name
    # find the right man.
    club_of = {}
    for c in read_csv(TIDY / "clubs.csv"):
        if c.get("ff_slug") and c.get("market"):
            club_of[norm(c["ff_slug"])] = norm(c["market"])
    # ff_slug -> the market key, so an XI row reaches the same player the
    # market row does without either of them going through a name.
    xw = load_crosswalk()
    by_ff_slug = {}
    if xw is not None:
        for pl in xw.players.values():
            if pl.ff_slug:
                by_ff_slug[norm(pl.ff_slug)] = pl.player_id
    players: dict[str, dict] = {}
    _merge(players, market, "name", MARKET_FIELDS, shared, club_of)
    _merge(players, xi, "player_name", XI_FIELDS, shared, club_of,
           by_ff_slug=by_ff_slug)
    return players


# Every market operation of the season, rebuilt from the app's activity feed
# by src/ledger.py. IT LIVES IN data/ BECAUSE NOBODY TYPES IT ANY MORE. It sat
# in inputs/ for as long as a human had to append a row after every deal;
# ledger.py took that job on 2026-08-18 and the file stayed where it was,
# which left the one directory a human is asked to maintain holding a file
# that overwrites anything typed into it on the next run.
LEDGER = TIDY / "transactions.csv"


def read_ledger(path=LEDGER) -> list[dict]:
    """The ledger, comments stripped, oldest first.

    The file carries its own documentation as # lines below the header, and
    a row whose player field is blank is a stray comma, not a transaction.
    """
    path = Path(path)
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        lines = [ln for ln in fh if not ln.lstrip().startswith("#")]
    rows = [r for r in csv.DictReader(lines)
            if (r.get("player") or "").strip()
            and not r["player"].lstrip().startswith("#")]
    rows.sort(key=lambda r: (r.get("date") or ""))
    return rows


class Valuation(NamedTuple):
    """A value reading, with enough context to distrust it.

    lag_h is hours between the snapshot and the moment asked about. A large
    lag doesn't invalidate the number, but a premium computed against a
    two-day-old value is a weaker claim than one against a two-hour-old
    value, and the report should be able to say which it has.
    """
    value: float
    observed_at: str
    lag_h: float
    name: str


# How far apart two readings of one player's value may be and still be the
# same player. Across 70 owned players the two sources agreed to within
# 0.2%, and the one wrong join was out by 603% — three thousand times the
# worst true disagreement, so anything between the two works.
#
# ONE CONSTANT, NOT TWO. ffcore.league used to define its own copy of this
# exact figure, on the same evidence, tuned nowhere but drifting silently
# possible everywhere: a change made to one would not touch the other.
VALUE_TOLERANCE = 0.05


def price_agrees(a, b, tolerance: float = VALUE_TOLERANCE) -> bool:
    """Are these two euro figures close enough to be the same player's price?

    The one place this repo decides two prices are the same price — reused
    by Market._by_price and ffcore.league._priced_like, which used to each
    carry a separate copy. False (not a guess) on a missing or zero figure.
    Why (the tolerance evidence): docs/notes/tidy.md#price_agrees--shared_names--row_key--one-tolerance-one-id
    """
    if not a or not b:
        return False
    return abs(a - b) <= tolerance * max(a, b)


def _club(row: dict) -> str:
    """The club a market row belongs to, normalised — "" when it says none."""
    return norm(row.get("team") or "")


def shared_names(rows) -> set:
    """The names in these market rows that belong to more than one player.

    Three indexes key the same rows (Market, the Scorer, the crosswalk) and
    must agree, so `latest_only` is applied HERE rather than trusted from
    the caller — callers used to disagree about it. Why:
    docs/notes/tidy.md#price_agrees--shared_names--row_key--one-tolerance-one-id
    """
    clubs: dict[str, set] = {}
    for r in latest_only(rows):
        n = norm(r.get("name"))
        if n:
            clubs.setdefault(n, set()).add(_club(r))
    return {n for n, c in clubs.items() if len(c) > 1}


def row_key(row: dict, shared: set) -> str:
    """The key one market row belongs under: the site's id for him.

    The source publishes an id (data-id) on every row — present and unique
    across all 44,912 rows of market history — so it's used ahead of the
    old name/name@club scheme. Falls back to the name for a row with none
    (a hand-written fixture, or the oldest snapshots). Why:
    docs/notes/tidy.md#price_agrees--shared_names--row_key--one-tolerance-one-id
    """
    fid = (row.get("ff_id") or "").strip()
    if fid:
        return fid
    n = norm(row.get("name"))
    return "%s@%s" % (n, _club(row)) if n in shared else n


class Market:
    """Every market snapshot, indexed by normalised name.

        m = Market(load_market())
        v = m.at("raphinha", ledger_stamp("2026-08-12T21:24"))
        if v and v.lag_h < 24:
            premium = price / v.value - 1
    """

    def __init__(self, rows: list[dict]):
        self.rows = rows
        self._latest: list | None = None
        self._name_idx: dict | None = None
        # The fuzzy answers, kept — the same unresolvable spellings are
        # asked about once per feed row, per run.
        self._resolved: dict[str, str | None] = {}
        self._by_key: dict[str, list[tuple[datetime, dict]]] = {}
        # A name is not a player (two Álvaro Garcías, one key, one shared
        # price history) — shared names get the club welded on, decided by
        # TODAY's market so every index (this one, load_players, the Scorer)
        # agrees. Why: docs/notes/tidy.md#marketkey_for--candidates--the-shared-name-refusal-and-why
        latest = latest_only(rows)
        # name -> the KEYS that answer to it (the site's own ids, not an
        # invented name@club).
        self._by_name: dict[str, list] = {}
        for r in latest:
            n = norm(r.get("name"))
            k = row_key(r, ())
            if n and k:
                self._by_name.setdefault(n, [])
                if k not in self._by_name[n]:
                    self._by_name[n].append(k)
        self._shared = {n for n, ks in self._by_name.items() if len(ks) > 1}
        for r in rows:
            key = self.key_of(r)
            when = snapshot_stamp(r.get("observed_at", ""))
            if not key or when is None:
                continue
            self._by_key.setdefault(key, []).append((when, r))
        for hist in self._by_key.values():
            hist.sort(key=lambda t: t[0])

    def key_of(self, row: dict) -> str:
        """The key a market row belongs under — its name, or name@club.

        Public because the crosswalk and the scorer key the same rows and all
        three indexes have to agree: one keeping both Álvaro Garcías apart
        while another merges them is worse than either doing it alone.
        """
        return row_key(row, self._shared)

    def __len__(self) -> int:
        return len(self._by_key)

    def latest_rows(self) -> list:
        """The newest snapshot, computed ONCE.

        key_for() rebuilt this on every lookup — latest_only over every row
        ever recorded, twenty-nine thousand of them, for each name that did
        not resolve exactly. Building the crosswalk called it twelve hundred
        times and spent twenty-three seconds inside norm(), three million
        calls of it. Nothing about the answer changes between lookups.
        """
        if self._latest is None:
            self._latest = latest_only(self.rows)
        return self._latest

    def _name_index(self) -> dict:
        """{norm(name): row} over latest_rows(), built ONCE.

        resolve() was rebuilding this identical dict on every call — 5,146
        times over the same 654 rows to resolve one run's crosswalk, 81% of
        its runtime. The rows never change between calls; nor should the
        index built from them.
        """
        if self._name_idx is None:
            self._name_idx = index_by(self.latest_rows(), "name")
        return self._name_idx

    def key_for(self, name, team: str = "", value=None):
        """Key for a human-typed name, or None if it doesn't resolve uniquely.

        Substring and initials are handled by ffcore.text. A name TWO players
        share resolves only when something says which: the club, or the price
        somebody else put on him. Without one of those it returns None — the
        state every caller already handles — because answering with either
        man is the wrong number this exists to stop.
        """
        k = norm(name)
        if k in self._shared:
            return self._pick(k, team, value)
        if k in self._by_key:
            return k
        # The memo holds the no-evidence answer only. Cached before the price
        # is consulted, a refusal would be handed back to the caller that
        # brought the evidence to settle it.
        if value is None and k in self._resolved:
            return self._resolved[k]
        row, cands = resolve(name, self.latest_rows(), index=self._name_index())
        if row is not None:
            got = self.key_of(row)
            self._resolved[k] = got
            return got
        # AN EXACT NAME IS NEVER OVERRULED — this runs only where resolve()
        # has already refused, which is the definition of a guess. The app
        # abbreviates first names ("C. Romero"), so a candidate list and the
        # price that was paid are often all there is, and refusing when the
        # money names one of them unambiguously is throwing evidence away.
        if cands and value is not None:
            return self._by_price(
                {self.key_of(r): r.get("value") for r in cands}, value)
        if value is None:
            self._resolved[k] = None
        return None

    def candidates(self, name) -> tuple:
        """(key, []) resolved · (None, [keys]) ambiguous · (None, []) no match.

        The one producer of market candidates, in this index's own keys —
        so a caller with its own evidence (who held him, what was paid) can
        prune without reconstructing a key. Why (the raw-resolve() bug this
        replaced): docs/notes/tidy.md#marketkey_for--candidates--the-shared-name-refusal-and-why
        """
        k = norm(name)
        if k in self._shared:
            return None, [key for key in self._by_name.get(k, [])
                          if key in self._by_key]
        if k in self._by_key:
            return k, []
        row, cands = resolve(name, self.latest_rows(), index=self._name_index())
        if row is not None:
            return self.key_of(row), []
        return None, [self.key_of(r) for r in cands]

    def _pick(self, shared: str, team: str, value):
        """Which of the men sharing this name, by club or by price."""
        keys = [k for k in self._by_name.get(shared, []) if k in self._by_key]
        if team:
            want = _club({"team": team})
            hit = [k for k in keys
                   if _club(self._by_key[k][-1][1]) == want]
            return hit[0] if len(hit) == 1 else None
        return self._by_price(
            {k: (self._by_key[k][-1][1]).get("value") for k in keys}, value)

    def _by_price(self, values: dict, value) -> str | None:
        """The one key in `values` whose price agrees, or None.

        `price_agrees()` is THE ONE PLACE THIS REPO DECIDES THAT TWO PRICES
        ARE THE SAME PRICE. The price is an independent identifier and the
        men it separates are not close — the pair that started this differ
        by forty times. Two agreeing keys settle nothing and neither does
        none, because the point of asking is to get one answer or no
        answer, never a preference between two.
        """
        if value is None:
            return None
        val = money(value) if isinstance(value, str) else float(value)
        if not val:
            return None
        hits = [k for k, raw in values.items() if price_agrees(money(raw), val)]
        return hits[0] if len(hits) == 1 else None

    def latest(self) -> dict[str, dict]:
        """{key: newest row} — the 'what exists today' view."""
        return {k: hist[-1][1] for k, hist in self._by_key.items() if hist}

    def at(self, name, when: datetime | None) -> Valuation | None:
        """Value from the last snapshot at or before `when`.

        Falls back to the earliest snapshot if the moment predates the data —
        common early in the season, when the ledger reaches further back than
        the ingest does. lag_h goes negative there, which is the signal that
        the reading is an extrapolation backwards and the premium built on
        it should be treated as indicative only.
        """
        key = self.key_for(name)
        if not key or when is None:
            return None
        hist = self._by_key.get(key) or []
        if not hist:
            return None
        prior = [(t, r) for t, r in hist if t <= when]
        t, r = prior[-1] if prior else hist[0]
        val = money(r.get("value"))
        if val is None:
            return None
        return Valuation(val, r.get("observed_at", ""),
                         (when - t).total_seconds() / 3600.0,
                         r.get("name", name))

    def series(self, name) -> list[tuple[datetime, float]]:
        """[(when, value)] oldest first — the input to post-buy drift."""
        key = self.key_for(name)
        out = []
        for t, r in self._by_key.get(key, []) if key else []:
            v = money(r.get("value"))
            if v is not None:
                out.append((t, v))
        return out

    def drift(self, name, since: datetime | None, days: float):
        """Value change from `since` to `since + days`, as (abs, pct).

        Returns None until a snapshot that late exists, so a horizon the data
        cannot yet support reads as blank rather than as zero drift.
        """
        if since is None:
            return None
        base = self.at(name, since)
        if not base:
            return None
        target = since + timedelta(days=days)
        later = [(t, v) for t, v in self.series(name) if t >= target]
        if not later:
            return None
        _, v = later[0]
        return v - base.value, (v / base.value - 1) * 100.0 if base.value else None


# WHICH SELLER VALUE IS A FREE AGENT, so a new one added by the app defaults
# to the safe reading (the app dealing him) rather than silently starting to
# treat every row as a contested rival listing — see market_routes()'s own
# docstring for why the two are not the same transaction.
LISTED_SELLER = "marketPlayerTeam"


def market_routes(mkt: list[dict], key_of) -> tuple[dict[str, float],
                                                    dict[str, str],
                                                    dict[str, int]]:
    """(price, route, bids) from api_market.csv's own rows.

    `seller` has always said which is which: `marketPlayerLeague` is the app
    dealing a free agent, `marketPlayerTeam` is a manager's own listing —
    not the same transaction, the same reason a clause and an ordinary buy
    aren't (only one has a real owner who can say no).
    `key_of(row)` is handed in (not imported) so this stays testable on
    synthetic rows.
    """
    price: dict[str, float] = {}
    route: dict[str, str] = {}
    bids: dict[str, int] = {}
    for r in mkt:
        k = key_of(r)
        if not k or not r.get("sale_price"):
            continue
        price[k] = float(r["sale_price"])
        route[k] = "listed" if r.get("seller") == LISTED_SELLER else "free"
        bids[k] = int(r.get("bids") or 0)
    return price, route, bids


def pending_sent(mkt: list[dict]) -> float:
    """Money already gone against a bid of yours still pending, summed.

    The app holds it against the bid until accepted, rejected or
    withdrawn — not free to spend today, however the raw balance reads.
    Reads the same `bid_money` field a sent offer displays from.
    """
    return sum(float(r["bid_money"]) for r in mkt
              if (r.get("bid_status") or "") == "pending" and r.get("bid_money"))


def bought_price(txns: list[dict], xw) -> dict[str, float]:
    """{key: what his CURRENT owner actually paid for him}, from the ledger.

    REPLAYED OLDEST FIRST — a player sold and re-bought gets the LATER
    price. `to == "market"` (a sale back to the app) is skipped, not
    recorded as a price of zero. Joined through the crosswalk's app_id
    (transactions.csv carries the ledger's LaLiga id); unplaced players
    are skipped, not guessed.
    """
    out: dict[str, float] = {}
    for t in txns:
        to = (t.get("to") or "").strip()
        if not to or to == "market":
            continue
        price = (t.get("price") or "").strip()
        if not price:
            continue
        key = xw.player(app_id=(t.get("player_id") or "").strip()) if xw \
            else None
        if not key:
            continue
        try:
            out[key] = float(price)
        except ValueError:
            continue
    return out


def pending_received(offers: list[dict], pt_to_key: dict[str, str]
                     ) -> dict[str, float]:
    """{player you hold: the largest pending offer on him}, or {}.

    A REAL PENDING OFFER BEATS A GUESS. What a caller otherwise prices a
    sale at is the market's own valuation — an estimate nothing has
    tested — and a real bid sitting on a player you have actually listed is
    ground truth for at least that much. Meant to be applied as a FLOOR on
    `proceeds`, never an overwrite: another bidder could still beat it
    before you act.

    `pt_to_key` joins the API's own ownership-record id to this repo's key
    — built once, off api_teams, and handed in rather than re-derived here.
    """
    out: dict[str, float] = {}
    for r in offers:
        if (r.get("status") or "") != "pending":
            continue
        k = pt_to_key.get(r.get("player_team_id") or "")
        money = float(r.get("money") or 0)
        if not k or not money:
            continue
        out[k] = max(out.get(k, 0.0), money)
    return out


# ---------------------------------------------------------------------------
# selftest — the pure parts only: no filesystem, no clock
# ---------------------------------------------------------------------------

def _selftest_cache() -> None:
    """The read cache must be invisible: same rows, and never anyone else's.

    Two guarantees, and the second is the one that would rot quietly. A caller
    that writes to a row it was handed must not change what the next caller
    reads — there are sixty-one read sites and any of them may start doing
    that tomorrow.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "t.csv"
        write_csv(p, [{"a": "1", "b": "x"}, {"a": "2", "b": "y"}])
        first = read_csv(p)
        assert [r["a"] for r in first] == ["1", "2"], first

        # Mutating what you were handed changes nothing for anyone else.
        first[0]["a"] = "999"
        first.append({"a": "3", "b": "z"})
        assert [r["a"] for r in read_csv(p)] == ["1", "2"], read_csv(p)

        # A rewrite is seen, whatever the clock did.
        write_csv(p, [{"a": "7", "b": "q"}])
        assert [r["a"] for r in read_csv(p)] == ["7"], read_csv(p)

        # ...and so is an append.
        append_csv(p, [{"a": "8", "b": "r"}])
        assert [r["a"] for r in read_csv(p)] == ["7", "8"], read_csv(p)

        # A file that is not there is empty, not an error, cache or no cache.
        assert read_csv(Path(tmp) / "nope.csv") == []


def _selftest() -> None:
    _selftest_cache()
    rows = [{"observed_at": "t1", "name": "A"}, {"observed_at": "t2",
            "name": "B"}, {"observed_at": "t2", "name": "C"}]
    assert [r["name"] for r in latest_only(rows)] == ["B", "C"]
    assert latest_only([]) == []
    assert snapshots(rows) == ["t1", "t2"]

    # -- a reading that is too old is not a reading -------------------------
    # A feed that stops answering leaves its last rows in the tidy store;
    # every reader downstream must treat them as stale, not today's.
    now = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)
    day_old = [{"observed_at": "2026-08-18T2246Z", "club": "Barcelona"}]
    two_days = [{"observed_at": "2026-08-17T2246Z", "club": "Barcelona"}]
    # Functions that default `now` must read `now or <clock>` once, not
    # re-sample per call, or the gate could disagree with a timestamp
    # printed beside it in the same document.
    import inspect as _inspect
    for _fn in (fresh_only, stale_feeds):
        _src = _inspect.getsource(_fn)
        assert "now or run_now()" in _src, _fn.__name__

    # ONE INSTANT PER RUN. Asked twice, it must not have moved.
    assert run_now() is run_now()
    assert run_now().tzinfo is timezone.utc
    # Pinned, it is whatever was asked for — the whole point being that two
    # runs over one store then produce byte-identical reports.
    _NOW.clear()
    os.environ["LFG_NOW"] = "2026-08-20T0900Z"
    assert run_now() == snapshot_stamp("2026-08-20T0900Z")
    del os.environ["LFG_NOW"]
    _NOW.clear()
    assert run_now().year >= 2026

    assert fresh_only(day_old, DAILY_FRESH_DAYS, now) == day_old
    assert fresh_only(two_days, DAILY_FRESH_DAYS, now) == []
    # The boundary is a day and a bit — 25.2 hours — because the sweep runs
    # twice a day and a daily source is allowed to answer the later one.
    assert fresh_only([{"observed_at": "2026-08-18T1030Z"}],
                      DAILY_FRESH_DAYS, now) == []
    assert fresh_only([{"observed_at": "2026-08-18T1100Z"}],
                      DAILY_FRESH_DAYS, now) != []
    # A stamp nothing can read is not evidence of freshness.
    assert fresh_only([{"observed_at": "whenever"}], DAILY_FRESH_DAYS, now) == []
    assert fresh_only([{}], DAILY_FRESH_DAYS, now) == []
    assert fresh_only([], DAILY_FRESH_DAYS, now) == []
    # A clock skew that puts the reading in the future is not staleness.
    assert fresh_only([{"observed_at": "2026-08-19T2300Z"}],
                      DAILY_FRESH_DAYS, now) != []

    assert age_phrase(3.2) == "3 days" and age_phrase(0.5) == "12 hours"
    assert age_phrase(0.01) == "14 minutes" and age_phrase(0.0) == "1 minute"
    assert age_phrase(1 / 24) == "1 hour", age_phrase(1 / 24)

    # -- the same gate, on the feeds swept every run -----------------------
    # The bound is the TIMER's longest healthy leg, not half a day. lfg.timer
    # fires at 00:40 and 11:40 local with up to 5 minutes of jitter, so a feed
    # that answers every sweep is still 13h10m old just before the overnight
    # run — and 0.5 days would have called that "stale" and thrown it away.
    # Measured on the store: the largest gap between consecutive api_teams
    # snapshots over 21 sweeps was 13.0 hours, and no sweep was missed.
    assert EVERY_RUN_FRESH_DAYS * 24 > 13 + 10 / 60
    # It must still be inside a day, or a feed that missed BOTH of a day's
    # sweeps would read as current.
    assert EVERY_RUN_FRESH_DAYS < 1.0
    healthy = [{"observed_at": "2026-08-18T2300Z"}]      # 13.0h before `now`
    missed = [{"observed_at": "2026-08-18T1100Z"}]       # a sweep skipped
    assert fresh_only(healthy, EVERY_RUN_FRESH_DAYS, now) == healthy
    assert fresh_only(missed, EVERY_RUN_FRESH_DAYS, now) == []

    # And the loaders are gated, not just the constant. A reading from the
    # far past is no answer at all, whatever the store happens to hold.
    stale = datetime(2099, 1, 1, tzinfo=timezone.utc)
    assert load_api_teams(now=stale) == []
    assert load_api_market(now=stale) == []
    assert load_api_standings(now=stale) == []
    assert load_api_offers(now=stale) == []

    # points already scored are not a snapshot: standings carries a balance
    # (must be today's) and a season-to-date total (only grows) — gating
    # both on a quiet feed would zero every manager's points.
    assert last_api_standings() != [] or read_csv(TIDY / "api_standings.csv") == []

    quiet = stale_feeds(now=stale)
    assert set(quiet) == set(GATED_API), quiet
    assert min(quiet.values()) > 365 * 70
    # A table nothing has ever written is not "stale" — it never answered.
    assert "api_nothing" not in stale_feeds(now=stale, names=("api_nothing",))

    # -- a name is not a player: two same-named players, two ids ------------
    tw = [{"ff_id": "867", "name": "Álvaro García", "team": "Rayo",
           "value": "20233300", "observed_at": "2026-08-19T1639Z"},
          {"ff_id": "12993", "name": "Álvaro García", "team": "Villarreal",
           "value": "501929", "observed_at": "2026-08-19T1639Z"},
          {"ff_id": "5001", "name": "Pepelu", "team": "Valencia",
           "value": "7669774", "observed_at": "2026-08-19T1639Z"}]
    tm = Market(tw)
    # Two men, two keys, two price histories — and the keys are the site's.
    assert len(tm) == 3, len(tm)
    assert sorted(tm.latest()) == ["12993", "5001", "867"], sorted(tm.latest())
    rayo, villa = tm.key_for("Álvaro García", team="Rayo"), \
        tm.key_for("Álvaro García", team="Villarreal")
    assert (rayo, villa) == ("867", "12993"), (rayo, villa)
    assert tm.at(rayo, snapshot_stamp("2026-08-19T1700Z")).value == 20233300.0
    assert tm.at(villa, snapshot_stamp("2026-08-19T1700Z")).value == 501929.0

    # ASKED WITHOUT A DISCRIMINATOR, IT REFUSES. Returning either one is the
    # bug; None is a caller that has to say which, and every caller of this
    # already handles an unresolved name.
    assert tm.key_for("Álvaro García") is None
    assert tm.key_for("alvaro garcia") is None

    # The app's own price tells them apart when the club is not to hand — the
    # same evidence api_key already trusts, and it is not close: these two
    # differ by forty times.
    assert tm.key_for("Álvaro García", value=20233300) == rayo
    assert tm.key_for("Álvaro García", value=501929) == villa
    # A price that matches neither resolves to neither.
    assert tm.key_for("Álvaro García", value=9e6) is None
    # A club nobody of that name plays for is not a near miss.
    assert tm.key_for("Álvaro García", team="Elche") is None

    # AND NOTHING ELSE MOVES. A name only one man has keeps the key it always
    # had, which is what keeps the ledger, the crosswalk and every stored
    # decision readable.
    assert tm.key_for("Pepelu") == "5001"
    assert "5001" in tm.latest()
    # AND NO name@club KEY IS INVENTED ANY MORE. It never came from the
    # source; it was the repo's own answer to a collision the source does
    # not have.
    assert not [k for k in tm.latest() if "@" in k]

    # WHO IS SHARED IS DECIDED BY TODAY'S MARKET, WHATEVER YOU HAND IT.
    # This was prose in the docstring and a parameter in the signature, so
    # three callers each computed the set themselves off different rows:
    # Market and the crosswalk passed latest_only, decide.py passed the whole
    # 41,642-row history. A man who left in July and a man who arrived in
    # August then shared a name in ONE index and not the others, and a squad
    # naming either missed the lookup and scored blank. The rule belongs in
    # the function, not in the callers.
    gone = [{"name": "Iker Munoz", "team": "Osasuna", "value": "1000000",
             "observed_at": "2026-07-01T1000Z"},
            {"name": "Iker Munoz", "team": "Getafe", "value": "2000000",
             "observed_at": "2026-07-01T1000Z"},
            {"name": "Iker Munoz", "team": "Getafe", "value": "2100000",
             "observed_at": "2026-08-19T1639Z"}]
    assert shared_names(gone) == set(), shared_names(gone)
    assert shared_names(latest_only(gone)) == shared_names(gone)
    assert row_key(gone[-1], shared_names(gone)) == norm("Iker Munoz")

    # An ambiguous abbreviated name ("C. Romero" vs. three Romeros) can be
    # settled by a price that agrees with only one candidate; an exact name
    # is never overruled by price.
    rom = [{"name": "Isaac Romero", "team": "Sevilla", "value": "6023939",
            "observed_at": "2026-08-19T1639Z"},
           {"name": "Cristian Romero", "team": "Atletico", "value": "47546565",
            "observed_at": "2026-08-19T1639Z"},
           {"name": "Carlos Romero", "team": "Espanyol", "value": "42510131",
            "observed_at": "2026-08-19T1639Z"}]
    rm = Market(rom)
    assert rm.key_for("C. Romero") is None
    assert rm.key_for("C. Romero", value=45739000) == norm("Cristian Romero")
    # A price agreeing with two of them settles nothing.
    assert rm.key_for("C. Romero", value=45000000) is None
    # A price agreeing with none of them settles nothing either.
    assert rm.key_for("C. Romero", value=1000) is None
    # AND THE PRICE NEVER OVERRULES A NAME. Isaac is named exactly, so he is
    # the answer whatever money is waved at it.
    assert rm.key_for("Isaac Romero", value=47546565) == norm("Isaac Romero")
    # Refusing is still cached as a refusal, not as the priced answer.
    assert rm.key_for("C. Romero") is None

    # candidates() answers in the market's own keys, and a shared name is
    # two candidates rather than a confident wrong one.
    assert tm.candidates("Pepelu") == ("5001", [])
    got, cands = tm.candidates("Álvaro García")
    assert got is None and sorted(cands) == ["12993", "867"], cands
    assert rm.candidates("C. Romero")[0] is None
    assert sorted(rm.candidates("C. Romero")[1]) == [
        norm("Carlos Romero"), norm("Cristian Romero"),
        norm("Isaac Romero")]
    assert tm.candidates("Nobody At All") == (None, [])

    # at() takes no price. THE CALLER'S MONEY IS NOT A VALUE unless the
    # caller is quoting a value: api_key is handed the app's own figure for
    # the player and key_for can trust it, but a PURCHASE price carries the
    # premium on top and agreeing with it to 5% is not identity evidence.
    assert rm.at("C. Romero", snapshot_stamp("2026-08-19T1700Z")) is None

    mkt = [{"name": "Ane Aldea", "team": "Alavés", "position": "defensa",
            "value": "2.050.000", "delta_1d": "-12.000"},
           {"name": "Bo Bidal", "team": "Betis", "position": "delantero",
            "value": "", "delta_1d": "0"}]
    xi = [{"player_name": "Ane Aldea", "team_slug": "alaves",
           "start_pct": "0.72", "status": "doubt"},
          {"player_name": "Cai Coro", "team_slug": "celta",
           "start_pct": "85", "status": "ok"}]
    p = _merge(_merge({}, mkt, "name", MARKET_FIELDS), xi,
               "player_name", XI_FIELDS)

    a = p["ane aldea"]
    assert a["value"] == 2050000.0 and a["delta_1d"] == -12000.0
    assert a["pos"] == "defensa" and a["start"] == 72.0 and a["status"] == "doubt"
    # market's display name and team win over the XI page's slug
    assert a["team"] == "Alavés" and a["name"] == "Ane Aldea"

    # An empty cell leaves the field unset, so fmt_money prints "—" not "0K".
    assert "value" not in p["bo bidal"] and p["bo bidal"]["delta_1d"] == 0.0
    # ...and a start_pct nobody published is absent, not zero.
    assert "start" not in p["bo bidal"]

    # XI-only player: name and team come from the XI page.
    c = p["cai coro"]
    assert c["name"] == "Cai Coro" and c["team"] == "celta" and c["start"] == 85.0
    assert "value" not in c

    both = [{"source": "futbolfantasy", "player_name": "Ane"},
            {"source": "analitica", "player_name": "Ane"},
            {"player_name": "Bo"}]                      # pre-source-column row
    assert [r["source"] for r in pick_source(both, "analitica")] == ["analitica"]
    assert len(pick_source(both, "futbolfantasy")) == 1
    assert pick_source(both, "") == both                 # "" means all sources
    assert pick_source(both, "nobody") == []             # a source not stored

    # -- kickoffs ----------------------------------------------------------
    # THE TRAP this parser exists to avoid: an offset that is not UTC. The
    # digit parser used for snapshot stamps would read "21:30+02:00" as 21:30
    # UTC — two hours late, which turns a locked squad into an editable one.
    assert kickoff_stamp("2026-08-15T19:30:00+00:00") == datetime(
        2026, 8, 15, 19, 30, tzinfo=timezone.utc)
    assert kickoff_stamp("2026-08-15T21:30:00+02:00") == datetime(
        2026, 8, 15, 19, 30, tzinfo=timezone.utc)
    # No offset at all is read as UTC, which is what they publish today.
    assert kickoff_stamp("2026-08-15T19:30:00") == datetime(
        2026, 8, 15, 19, 30, tzinfo=timezone.utc)
    assert kickoff_stamp("") is None and kickoff_stamp("soon") is None

    # -- growing a decision log a column ----------------------------------
    # The one case here that touches a disk, in a temp directory, because the
    # thing being tested IS the file: these two functions rewrite and extend
    # append-only logs that cannot be reconstructed if they go wrong.
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        log = Path(tmp) / "log.csv"
        append_csv(log, [{"a": "1", "b": "2"}], ["a", "b"])

        # A caller that reorders its column list must NOT shift the values.
        append_csv(log, [{"a": "3", "b": "4"}], ["b", "a"])
        assert read_csv(log)[1] == {"a": "3", "b": "4"}

        # An unknown column is dropped rather than appended past the header,
        # which is what used to make a row wider than the file.
        append_csv(log, [{"a": "5", "b": "6", "c": "7"}], ["a", "b", "c"])
        assert read_csv(log)[2] == {"a": "5", "b": "6"}

        # Widen: old rows gain an empty cell, and no recorded value moves.
        assert widen_csv(log, ["a", "b", "c"]) is True
        rows = read_csv(log)
        assert [r["c"] for r in rows] == ["", "", ""]
        assert [r["a"] for r in rows] == ["1", "3", "5"]
        # Idempotent — every run calls it, only the first one rewrites.
        assert widen_csv(log, ["a", "b", "c"]) is False
        # A column the caller stopped sending is KEPT, never dropped.
        assert widen_csv(log, ["a"]) is False
        assert "b" in read_csv(log)[0]
        # And now the new column actually lands.
        append_csv(log, [{"a": "8", "b": "9", "c": "10"}], ["a", "b", "c"])
        assert read_csv(log)[3]["c"] == "10"
        # A file that does not exist yet is not a migration.
        assert widen_csv(Path(tmp) / "nope.csv", ["a"]) is False

    # -- minutes_played(): the one column, read in opposite directions ------
    assert minutes_played("starter", "") == 90.0          # played the whole match
    assert minutes_played("starter", "64") == 64.0         # subbed off at 64'
    assert minutes_played("sub", "") == 0.0                 # never came on
    assert minutes_played("sub", "64") == 26.0              # came on at 64', played 26
    assert minutes_played("coach", "") == 0.0               # nobody on the pitch
    assert minutes_played("starter", "0") == 0.0            # subbed off at kickoff

    # -- market_routes: a free agent is not a rival's listed player --------
    # api_market.csv's own `seller` column already says which is which
    # (marketPlayerLeague = the app dealing a free agent, marketPlayerTeam =
    # a manager listing one of theirs) — worth telling apart: one nobody can
    # refuse, the other has a real owner who might not sell.
    mkt_rows = [
        {"player_name": "Free Agent", "sale_price": "5000000",
         "seller": "marketPlayerLeague", "bids": "0"},
        {"player_name": "Listed Rival", "sale_price": "8000000",
         "seller": "marketPlayerTeam", "bids": "2"},
        # No sale_price at all: not on offer, not priced, not routed.
        {"player_name": "Not Priced", "sale_price": "",
         "seller": "marketPlayerLeague"},
        # Resolves to no key: silently skipped, not a crash.
        {"player_name": "Unjoinable", "sale_price": "1000000",
         "seller": "marketPlayerTeam"},
    ]
    key_of = {"Free Agent": "free_agent", "Listed Rival": "listed_rival",
             "Not Priced": "not_priced"}.get
    price, route, bids = market_routes(
        mkt_rows, lambda r: key_of((r.get("player_name") or "")))
    assert price == {"free_agent": 5000000.0, "listed_rival": 8000000.0}, price
    assert route == {"free_agent": "free", "listed_rival": "listed"}, route
    assert bids == {"free_agent": 0, "listed_rival": 2}, bids
    assert "not_priced" not in route and "not_priced" not in price
    # An unrecognised seller value defaults to "free" — the app dealing it
    # is the ordinary case, and a new discriminator value should not
    # silently start reading every row as a contested rival listing.
    unknown_seller = [{"player_name": "Free Agent", "sale_price": "1",
                       "seller": "something_new"}]
    _, r2, _ = market_routes(unknown_seller, lambda r: "free_agent")
    assert r2 == {"free_agent": "free"}, r2

    # -- pending_sent: a bid of yours is money already gone -----------------
    mkt_bids = [
        {"bid_status": "pending", "bid_money": "5600000"},
        {"bid_status": "pending", "bid_money": "6795815"},
        {"bid_status": "", "bid_money": ""},                # no bid here
        {"bid_status": "accepted", "bid_money": "2000000"}, # settled, not held
        {"bid_status": "pending", "bid_money": ""},         # unreachable shape
    ]
    assert pending_sent(mkt_bids) == 5600000.0 + 6795815.0, pending_sent(mkt_bids)
    assert pending_sent([]) == 0.0

    # -- pending_received: a real offer beats a guess, and only as a floor --
    p2k = {"pt1": "me_a", "pt2": "me_b"}
    offers = [
        {"player_team_id": "pt1", "status": "pending", "money": "6795815"},
        # A second, smaller pending offer on the SAME player: the larger
        # one is what he could actually raise, not the first one seen.
        {"player_team_id": "pt1", "status": "pending", "money": "1000000"},
        {"player_team_id": "pt2", "status": "accepted", "money": "9000000"},
        # No offer at all — the placeholder row parse_api_offer emits so the
        # table stays stamped. Not pending, so it prices nothing.
        {"player_team_id": "pt2", "status": "", "money": ""},
        # A playerTeamId nothing in the squad joins to (sold since, or a
        # rival's — should never happen, given offer_sources() only ever
        # asks for your own, but a join failing silently beats a KeyError).
        {"player_team_id": "unknown", "status": "pending", "money": "1"},
    ]
    got = pending_received(offers, p2k)
    assert got == {"me_a": 6795815.0}, got     # pt2's only offer was accepted
    assert pending_received([], p2k) == {}
    assert pending_received(offers, {}) == {}   # nothing to join to

    # -- bought_price: what the CURRENT owner actually paid, from the ledger -
    from ffcore.crosswalk import Crosswalk, Player
    bp_xw = Crosswalk(players={
        "steady": Player(player_id="steady", app_id="101"),
        "flip": Player(player_id="flip", app_id="102"),
    })
    bp_txns = [
        {"date": "2026-08-11", "player": "Steady", "player_id": "101",
         "from": "market", "to": "me", "price": "5000000"},
        {"date": "2026-08-12", "player": "Flip", "player_id": "102",
         "from": "market", "to": "riv", "price": "3000000"},
        # Sold back to the app, then re-bought by ME at a different price —
        # the LATER price wins, matching who holds him now.
        {"date": "2026-08-20", "player": "Flip", "player_id": "102",
         "from": "riv", "to": "market", "price": "4000000"},
        {"date": "2026-08-21", "player": "Flip", "player_id": "102",
         "from": "market", "to": "me", "price": "4500000"},
        # No crosswalk entry for this app_id — skipped, not guessed.
        {"date": "2026-08-13", "player": "Nobody", "player_id": "999",
         "from": "market", "to": "me", "price": "1"},
        # A blank price (a stray row) skips rather than crashing on float().
        {"date": "2026-08-14", "player": "Steady", "player_id": "101",
         "from": "market", "to": "me", "price": ""},
    ]
    bp = bought_price(bp_txns, bp_xw)
    assert bp == {"steady": 5000000.0, "flip": 4500000.0}, bp
    assert bought_price([], bp_xw) == {}
    assert bought_price(bp_txns, None) == {}    # no crosswalk, nothing to join

    # -- jornada_locks: the ROUND's lock, not the next kickoff of anything --
    # A round already under way can still list its own later, staggered
    # matches in fixtures.csv — those are leftovers of an already-locked
    # jornada, not a fresh deadline (see load_deadline()'s own docstring
    # for the real case this fixes: a 2026-09-13 report read a jornada-5
    # leftover kickoff minutes away as "the deadline" days after jornada 5
    # itself had actually locked).
    jl_matches = [{"match_id": "1", "jornada": "1", "home": "alaves",
                  "away": "getafe", "score": "3-0"},
                 {"match_id": "2", "jornada": "1", "home": "espanyol",
                  "away": "levante", "score": "1-0"},
                 {"match_id": "9", "jornada": "2", "home": "rayo-vallecano",
                  "away": "alaves", "score": "2-2"}]
    jl_fixtures = [{"kickoff": "2026-08-16T17:00:00+00:00", "home": "Espanyol",
                   "away": "Levante"},
                  {"kickoff": "2026-08-15T19:30:00+00:00", "home": "Alaves",
                   "away": "Getafe"}]
    assert team_slug_of("Racing Santander", {"racing", "real-madrid"}) \
        == "racing"
    assert team_slug_of("Real Betis", {"betis", "real-sociedad"}) == "betis"
    assert team_slug_of("Nowhere FC", {"racing"}) is None
    jl = jornada_locks(jl_matches, jl_fixtures)
    # The round locks at its EARLIEST kickoff, not each match's own: the app
    # locks the whole lineup once, so Sunday's starter is already frozen.
    assert list(jl) == [1] and jl[1].day == 15, jl
    assert 2 not in jl                           # no kickoff observed for it
    # match_locks() disagrees on purpose: each fixture locks its OWN two
    # teams at its OWN kickoff, not both grouped under the round's earliest.
    ml = match_locks(jl_matches, jl_fixtures)
    assert ml[(1, "alaves")] == jl[1]             # alaves was the earliest
    assert ml[(1, "espanyol")] > jl[1]            # levante/espanyol, later
    assert lock_order({3: datetime(2026, 9, 3, tzinfo=timezone.utc),
                       1: datetime(2026, 8, 15, tzinfo=timezone.utc),
                       2: datetime(2026, 8, 20, tzinfo=timezone.utc)}) \
        == [1, 2, 3]

    print("ffcore.tidy self-test OK (65 cases)")


if __name__ == "__main__":
    _selftest()
