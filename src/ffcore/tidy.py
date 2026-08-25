"""
ffcore.tidy — reading what ff_ingest wrote, and asking it about the past.

Paths, CSV IO and timestamp parsing were copy-pasted across report.py,
offers.py, find_slug.py and history.py. They are here once.

The part that matters is Market: an index over EVERY snapshot in
market.csv, not just the newest one. Three of the five things rivals.py
needs are questions about the past —

    what was this player worth when that transaction happened?   at()
    what did his value do in the fortnight after?                series()
    how stale is the reading I just used?                        Valuation.lag_h

— and none of them can be answered from latest_only(), which is all the
current code ever looks at.

TIMEZONES, the trap this module exists to close. ff_ingest stamps snapshots
in UTC ("2026-08-12T2100Z"). The app's Activity feed, and therefore every
date in the ledger, is Europe/Madrid wall-clock with no offset
written down ("2026-08-12T21:24"). In August that is two hours apart. Compare
them naively and a purchase gets matched to a snapshot taken two hours after
it, which is exactly the direction that makes an overpay look like a bargain.
Ledger strings go through ledger_stamp(); snapshot strings through
snapshot_stamp(); both come back as aware UTC.
"""

from __future__ import annotations

import csv
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import NamedTuple

from ffcore.parse import money, pct100
from ffcore.text import index_by, norm, resolve

__all__ = ["ROOT", "TIDY", "SEASON", "DECISIONS", "REPORTS", "PARTS", "MADRID",
           "input_path", "read_csv", "write_csv", "append_csv", "widen_csv",
           "write_lines", "snapshot_stamp", "ledger_stamp", "latest_only", "snapshots",
           "Market", "Valuation", "VALUE_TOLERANCE", "price_agrees",
           "load_market", "load_lineups",
           "shared_names", "row_key", "run_now", "load_crosswalk",
           "load_players", "read_ledger", "LEDGER", "load_deadline", "LINEUP_SOURCE",
           "pick_source", "load_fixtures", "next_kickoff", "kickoff_stamp",
           "load_elo", "load_results_history", "load_understat_players",
           "MATCH_LEN", "minutes_played", "fresh_only", "DAILY_FRESH_DAYS",
           "EVERY_RUN_FRESH_DAYS", "stale_feeds",
           "GATED_API", "age_phrase", "last_api_standings",
           "load_api_lineup"]

ROOT = Path(os.environ.get("FF_ROOT", "./data"))
TIDY = ROOT / "tidy"
SEASON = ROOT / "season"
DECISIONS = ROOT / "decisions"
# WHAT THE SITE GETS, and nothing else. reports/ held six markdown files and a
# JSON; two of them were published and four existed only to be stitched into
# those two, while a fifth was the same content as the board in another
# rendering. A directory called "reports" that holds four things nobody reads
# is four more places a number can appear and disagree with itself.
REPORTS = Path("reports")

# The render fragments the appendix is stitched from. Build artifacts, under
# .runtime/ with the rest of them, untracked and unpublished — they are how
# METHOD.md is made, not something to read.
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


# One parse per file per process, keyed on what the file IS rather than on
# what it is called.
#
# The chain runs in one interpreter now (src/run.py), and in that interpreter
# market.csv was parsed sixteen times and lineups.csv eleven — 2.9 of ten
# seconds spent turning the same 2.8 MB into the same 32,515 dicts, because
# every stage that wants a player asks for the whole table.
#
# CALLERS GET THEIR OWN DICTS. Handing back the cached rows would be faster
# again and would be a silent-corruption bug waiting for the first caller that
# writes to a row: this file has sixty-one read sites and auditing them all is
# exactly the kind of proof that goes stale the next time somebody adds one.
# The copy costs a sixth of the parse, so safety here is nearly free.
#
# Invalidated two ways, deliberately overlapping: the key carries the file's
# mtime and size, and every writer below drops its own path. The key alone
# would be enough except in the case nobody thinks about — a rewrite inside
# one clock tick that happens to land on the same length.
_READ_CACHE: dict[str, tuple] = {}


def _forget(path) -> None:
    _READ_CACHE.pop(str(Path(path)), None)


def read_csv(path) -> list[dict]:
    """Rows as dicts. Missing file is empty, not an error — a report that
    hasn't been fed yet should say so, not crash.

    THE COPY ON THE WAY OUT IS THE CACHE'S ONLY ISOLATION, not an oversight
    to be optimised away. `_READ_CACHE` keeps the parsed rows keyed by
    (mtime, size), so every caller after the first would otherwise be handed
    the SAME dict objects — and one caller setting a field would rewrite
    what a later, unrelated caller reads, silently and only on the second
    read. `latest_only()`, `fresh_only()` and `pick_source()` all filter
    without copying, so those aliases run deep.

    It is not free and the number is known: measured 2026-08-24 over one sim
    stage, 43 calls across 13 files cost 1.52s, of which about 1.0s is the
    unavoidable first parse of each file and about 0.5s is this copy —
    market.csv and lineups.csv are ~85K rows each and read three times
    apiece, at ~0.067s a copy. A run of that stage under a mutation detector
    found no caller mutating a handed-out row, so the copy is defensive
    rather than currently load-bearing; that was ONE stage of ten, which is
    why it stays. Dropping it needs the same check over the whole pipeline —
    ingest, crosswalk and sources are where a transform-in-place would
    plausibly live — not this evidence.
    """
    path = Path(path)
    try:
        st = path.stat()
    except OSError:
        return []
    hit = _READ_CACHE.get(str(path))
    if hit is None or hit[0] != (st.st_mtime_ns, st.st_size):
        with path.open(encoding="utf-8") as fh:
            hit = ((st.st_mtime_ns, st.st_size), list(csv.DictReader(fh)))
        _READ_CACHE[str(path)] = hit
    return [dict(r) for r in hit[1]]


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
    """Add columns to an existing log, in place. True if the file was rewritten.

    append_csv writes the header once, so a caller that grows its column list
    would otherwise append rows WIDER than the header. csv.DictReader silently
    drops the overflow, which means the new columns would look empty forever
    instead of failing. This rewrites the old rows with the new header and an
    empty string for what was never recorded — history keeps its own shape, and
    a blank cell honestly says "this run did not measure that".

    Only ever widens. A column that disappeared from `fieldnames` is kept, so
    an old reader still works and no recorded number is ever destroyed.
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
    """Append, writing the header only when creating the file.

    For the decision logs — squad_log.csv, and rival_log.csv when rivals.py
    lands. Estimates made today are not reconstructable later, which is the
    whole reason they get written down as they are made.
    """
    path = Path(path)
    _forget(path)
    if not rows:
        return
    fieldnames = fieldnames or list(rows[0])
    fresh = not path.exists()
    if not fresh:
        # The FILE's header wins, not the caller's list. Appending in a
        # different order than the header would misalign every value in the
        # row, and nothing downstream would notice. Use widen_csv() first to
        # add a column; a key not in the header is dropped, not shifted in.
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
    """The next lock as aware UTC, or None — always the next kickoff.

    Shared rather than copied: report.py stamps hours_to_lock into
    squad_log.csv and xi.py stamps it into xi_fielded.csv, and two readings of
    the same deadline that disagree would silently mis-order the two logs
    against each other.

    The next kickoff in data/tidy/fixtures.csv IS the whole deadline, not a
    floor: the app locks the lineup once per jornada, so a player whose own
    match is on Sunday is already frozen at Friday's kickoff (verified in-app,
    2026-08-16, issue #28).

    THE TYPED FALLBACK IS GONE. inputs/deadline.txt was read when no fixture
    was available, and it was wrong the moment it expired and stayed wrong
    until somebody noticed — which is exactly what happened: the file held a
    lapsed date and the report read it as "deadline passed". A fixture list
    that cannot answer should say None, and the report should say it does not
    know, rather than substitute a number that is wrong in a way nothing can
    detect. `with_source=True` still returns (when, "fixtures"|"none") so the
    report can say so.
    """
    when = next_kickoff()
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

def _digits_to_dt(s: str, tz):
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


# How long a DAILY feed may go unanswered before its last reading stops being
# today's. The sweep runs twice a day, so missing both of a day's sweeps is not
# a cadence — it is a feed that has stopped. src/methodology.py prints "stale"
# off this same number, so the table and the refusal below cannot disagree.
DAILY_FRESH_DAYS = 1.05

# The same bound for a feed swept EVERY RUN. It is not half a day, however
# obvious that looks: lfg.timer fires at 00:40 and 11:40 local, so the two
# legs are 11h and 13h, and RandomizedDelaySec puts another 5 minutes on
# either. A feed answering every single sweep is 13h10m old at its oldest, and
# 0.5 would have called that a dead feed every night. 0.6 clears the longest
# healthy leg by an hour and still catches a missed sweep well inside the day.
EVERY_RUN_FRESH_DAYS = 0.6


_NOW: list = []


def run_now() -> datetime:
    """The instant this RUN is describing. Sampled once per process.

    NOT named `now`: fresh_only and stale_feeds both take a `now` argument,
    and a module function of that name is shadowed exactly where it is most
    needed.

    Twenty-five call sites asked the clock themselves, so one report could
    stamp 23:52 while the document explaining it stamped 23:53, and the cash
    a rival was credited grew between the stage that scored him and the stage
    that printed him. None of that was ever a wrong answer, but it made the
    outputs undiffable: re-running unchanged code moved nine fields, so
    "nothing moved" could not be asserted about a change, only eyeballed
    around the noise. run.py executes every reporting stage in ONE
    interpreter, so one sample here is one instant for the whole report.

    NOT for the sweep. ingest.py fetches over minutes and observed_at must be
    when a page was actually asked for — it keeps the live clock, and it runs
    in its own process anyway.

    LFG_NOW pins it, which is what makes an output diff mean something: run
    the pipeline twice over one store with the clock held and the two reports
    are byte-identical, so anything that moves is the change under test and
    not the eleven seconds between runs. It is a measuring tool — the timer
    and every real run leave it unset.
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

    THE REFUSAL IS NOT THE WHOLE JOB. A gate hands the reader [], and []
    reads as "nothing there": with the market feed three days old the report
    said "market 0th percentile, a poor week" and printed no BUY list at all
    — an emptiness presented as a finding. Measured, by ageing the store three
    days and generating. So the reason has to be sayable, and this says it.

    A table that has NEVER been written is absent from the result. It has not
    gone quiet; it has never spoken, and the callers already have a sentence
    for that ("add a token", "the ledger takes over").
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


def load_understat_players(season: str = "") -> list[dict]:
    """Player-level xG/xA (sources.parse_understat_players), the newest
    reading for each (season, understat_id) pair.

    NOT wired into forecasting yet — captured deliberately unused, the same
    shape starters.csv's per-match minutes and ffcore.attributes.
    resolve_fitness() were the session they were added: verified against
    real data before anything downstream depends on it, not the same day
    it starts flowing.

    `season` filters to one Understat season label ("2026" for 2026/2027);
    "" (default) returns every season on record — a caller comparing a
    player's prior-season xG rate to his current one wants both at once.
    """
    rows = read_csv(TIDY / "understat_players.csv")
    if season:
        rows = [r for r in rows if r.get("season") == season]
    latest: dict[tuple, dict] = {}
    for r in sorted(rows, key=lambda r: r.get("observed_at", "")):
        key = (r.get("season"), r.get("understat_id"))
        if key[1]:
            latest[key] = r
    return list(latest.values())


# Approximated, not measured: stoppage time is not on the page a starter's
# off-minute comes from, so a man who plays the whole match is credited this
# rather than the 94 or so he may actually have been on for. The same small
# error for everyone uncredited with a substitution, so it does not distort
# ranking between them — a constant offset, not noise.
MATCH_LEN = 90.0


def minutes_played(role: str, raw_minute, match_len: float = MATCH_LEN) -> float:
    """One player's minutes in one match, from starters.csv's own columns.

    THE SAME COLUMN READ IN OPPOSITE DIRECTIONS, and that is the whole rule.
    A STARTER's `minute` is when he came OFF — blank means he played the
    whole match. A SUB's `minute` is when he came ON — blank means he never
    did. `ffcore.score._per_jornada_current` (this season's rate, keyed
    through the crosswalk) and `ffcore.startprob.observations` (a graded
    per-match label for P(start)'s own fit) both need exactly this, and
    used to each carry their own copy of it.

    0.0 for any other role (a coach row, say) — not an error, just nobody
    on the pitch.
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
    """The first kickoff still ahead of us, or None if we cannot tell.

    None covers three cases that must all fall back rather than guess: no
    fixtures file yet, an unparseable kickoff, and every listed match already
    started (their page drops a match once it is under way, so a stale file
    goes quiet rather than stale-and-confident).
    """
    now = now or run_now()
    ahead = [k for k in (kickoff_stamp(r.get("kickoff"))
                         for r in load_fixtures()) if k and k > now]
    return min(ahead) if ahead else None


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
    market, xi = latest_only(load_market()), latest_only(load_lineups())
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

    THE ONE PLACE THIS REPO DECIDES TWO PRICES ARE THE SAME PRICE — reused
    by `Market._by_price` (picking among several candidates who share a
    name) and by `ffcore.league._priced_like` (checking a name join against
    the app's own stated figure), which used to each carry a separate
    implementation of this same comparison.

    False on a missing or zero figure: an absent number is not evidence
    either way, and callers that want "silent means agree" say so
    themselves rather than this function guessing it for them.
    """
    if not a or not b:
        return False
    return abs(a - b) <= tolerance * max(a, b)


def _club(row: dict) -> str:
    """The club a market row belongs to, normalised — "" when it says none."""
    return norm(row.get("team") or "")


def shared_names(rows) -> set:
    """The names in these market rows that belong to more than one player.

    THREE INDEXES KEY THE SAME ROWS — Market here, the Scorer's lookup, and
    the crosswalk — and they have to agree about this or a squad key from one
    misses in the next. Three of 651 names on 2026-08-19.

    Decided by TODAY'S market, whatever you hand it: latest_only is applied
    here rather than trusted from the caller. It used to be the caller's job
    and the callers did not agree — Market and the crosswalk filtered, and
    decide.py passed the whole history, which shares a name between a man who
    left and a man who arrived. latest_only is idempotent, so a caller that
    already filtered pays one max() for the guarantee.
    """
    clubs: dict[str, set] = {}
    for r in latest_only(rows):
        n = norm(r.get("name"))
        if n:
            clubs.setdefault(n, set()).add(_club(r))
    return {n for n, c in clubs.items() if len(c) > 1}


def row_key(row: dict, shared: set) -> str:
    """The key one market row belongs under: the site's id for him.

    THE SOURCE PUBLISHES ONE AND IT WAS NOT BEING READ. Every player element
    on the market page carries data-id; the parser took data-nombre and
    derived a slug from the anchor, so the repo keyed players by name and
    then had to invent name@club for the three that two men share. On the
    44,912 rows of market history data-id is present on every one and gives
    666 distinct players — including 16975 and 11362, the two Iker Muñoz.

    Falls back to the name for a row with no id, which is what a
    hand-written fixture and the oldest snapshots look like.
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
        # The fuzzy answers, kept. The same handful of unresolvable spellings
        # are asked about again and again — once per feed row, per run.
        self._resolved: dict[str, str | None] = {}
        self._by_key: dict[str, list[tuple[datetime, dict]]] = {}
        # A NAME IS NOT A PLAYER. Two men called Álvaro García, one at Rayo
        # worth 20.23M and one at Villarreal worth 0.50M, shared a key and
        # therefore shared a price history — and a lookup got whichever the
        # index happened to hand back. The name alone stays the key for the
        # 648 players who have it to themselves; the three who do not get the
        # club welded on, so the two histories cannot mix.
        # WHO IS SHARED IS DECIDED BY TODAY'S MARKET, not by all of history.
        # The other two indexes — load_players and the Scorer — see only the
        # newest snapshot, and a key one of them splits while another merges
        # is a lookup that silently misses. Over history five names were ever
        # shared; three of them are two players today, and the other two are
        # one player and a man who has left.
        latest = latest_only(rows)
        # name -> the KEYS that answer to it. Two men of one name are two
        # keys, and there is nothing to invent: the site's id already tells
        # them apart. This used to be name -> clubs, so that "name@club"
        # could be assembled as a key of its own — a key the source never
        # issued, existing only because the id beside the name went unread.
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

        THE ONE PRODUCER OF MARKET CANDIDATES, and it answers in this index's
        own keys. Callers with evidence of their own — who held him, which
        way the deal went, what was paid — prune the list and must not have
        to reconstruct a key to do it.

        It exists because they were calling ffcore.text.resolve() over a raw
        list of rows instead. That list's exact-name index holds ONE row per
        name, so the two Álvaro Garcías arrived as a confident single match
        rather than as the ambiguity they are, and the answer was then keyed
        norm(name) — "alvaro garcia", which this index does not contain,
        because a shared name is keyed on club. A guess and an unusable key
        in one step.
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
    # THE BUG THIS EXISTS FOR: a feed that stops answering leaves its last
    # rows in the tidy store, and every reader downstream treats them as
    # today's. Club Elo died on 2026-08-17 and the fixture board went on
    # ranking twenty clubs by ratings from before the jornada for two days,
    # because all twenty still joined. Age is the only thing that says so.
    now = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)
    day_old = [{"observed_at": "2026-08-18T2246Z", "club": "Barcelona"}]
    two_days = [{"observed_at": "2026-08-17T2246Z", "club": "Barcelona"}]
    # ...and the functions that DEFAULT a `now` take the run's instant too.
    # These read `now = now or <clock>`, so left to themselves they each
    # sampled again — the freshness gate could disagree with the timestamp
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

    # A gated feed that has gone quiet must be SAYABLE, not merely refused.
    # Refusing it in silence turns "I cannot see the market" into "there is
    # nothing to buy, a poor week" — measured, on the store aged three days.
    # POINTS ALREADY SCORED ARE NOT A SNAPSHOT. The gate above throws away
    # the whole standings row when the feed goes quiet, and the row carries
    # two different kinds of fact: a balance, which must be today's, and a
    # season-to-date total, which only grows. Gating both zeroed every
    # manager's points and simulated the rest of the season from 0 — a wrong
    # number where a slightly old one was available.
    assert last_api_standings() != [] or read_csv(TIDY / "api_standings.csv") == []

    quiet = stale_feeds(now=stale)
    assert set(quiet) == set(GATED_API), quiet
    assert min(quiet.values()) > 365 * 70
    # A table nothing has ever written is not "stale" — it never answered,
    # and the caller that degrades to the ledger says so differently.
    assert "api_nothing" not in stale_feeds(now=stale, names=("api_nothing",))

    # -- a name is not a player ---------------------------------------------
    # THE OLDEST KNOWN WRONG NUMBER IN THE REPO. The key was a normalised
    # name, and LaLiga fields an Álvaro García at Villarreal worth 0.50M and
    # another at Rayo worth 20.23M. To this index they were ONE player with
    # one price history built out of both, and whichever row a lookup reached
    # first decided what a squad was worth. One of the three collisions on
    # 2026-08-19 was a player somebody owned.
    # THE SITE ISSUES AN ID PER PLAYER and always did — data-id, in the same
    # element as data-nombre. Reading it is what makes the two of them two,
    # so the fixture carries it exactly as the page does.
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

    # A GUESS IS SETTLED BY THE PRICE; AN EXACT NAME IS NEVER OVERRULED.
    # The app writes "C. Romero" and the market holds three Romeros, so
    # resolve() rightly refuses. The caller holding the 45.74M that was paid
    # can do better than refuse: one of the three agrees with it and the
    # others are nowhere near. Same evidence and same tolerance _pick already
    # trusts for two men of one name — applied only where the alternative is
    # no answer at all.
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

    print("ffcore.tidy self-test OK (47 cases)")


if __name__ == "__main__":
    _selftest()
