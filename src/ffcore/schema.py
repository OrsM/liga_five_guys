"""
ffcore.schema — the store's field names and field-read idioms, in one place.

Every table here is a `csv.DictReader` row: a plain `dict[str, str]`, or
`str | None` where a source library hands back an empty cell as `None`
instead of `""`. Reading one has always meant the same three-line dance
at the call site — `(r.get(col) or "").strip()` for text, a bare
`try/except (TypeError, ValueError)` around `float()`/`int()` for numbers —
copied by hand 116 times across 15 files before this module existed.
`text()`/`num()`/`whole()`/`flag()` are that dance, written once.

NUMBER MEANING IS NOT HERE. `ffcore.parse` already owns the two currency
readings this store actually needs — `money()` (a dot groups thousands)
vs `ratio()` (a dot is a decimal point) — because a bare `float()` cannot
tell "2.050.000" from "2.37" and guessing wrong silently misprices a
player. `num()` below is for the plain numeric columns that carry no such
ambiguity (`points`, `value` as returned as a raw wire number rather than
free text — check the source before assuming). Where a market or
percentage column is being read, use `ffcore.parse.money()` /
`ffcore.parse.ratio()` / `ffcore.parse.pct100()` instead, not this.

This module is a dependency-free leaf: no import of another ffcore module,
so any of them (including ones `ffcore.parse` itself does not want to
depend on) can use it without a cycle risk. Nothing calls it yet — it is
being introduced ahead of the callers that will migrate to it.
"""

from __future__ import annotations

import csv
import os
from pathlib import Path

__all__ = [
    "text", "num", "whole", "flag",
    "MATCHES", "STARTERS", "MARKET", "LINEUPS", "API_STATS", "PERJORNADA",
    "PLAYERS", "CLUBS", "FIXTURES", "ELO", "UNDERSTAT_PLAYERS",
    "API_MARKET", "API_PLAYERS", "API_TEAMS", "FEEDS", "TRANSACTIONS",
    "API_ACTIVITY", "API_LINEUP", "API_OFFERS", "API_PLAYERS_ALL",
    "API_STANDINGS", "RESULTS_HISTORY",
]


def text(row, col: str, default: str = "") -> str:
    """The store's universal string read: trimmed, never None.

    A missing key, a `None` cell (some sources hand back JSON `null`,
    which `csv.DictReader` never produces but hand-built row dicts in this
    codebase sometimes carry before they hit disk), and an all-whitespace
    cell are the same "nothing here" — `row.get(col) or ""` collapses all
    three before `.strip()` runs, which a bare `.strip()` on `None` would
    raise on.
    """
    v = row.get(col) or ""
    v = v.strip() if isinstance(v, str) else str(v).strip()
    return v if v else default


def num(row, col: str, default=None):
    """A plain numeric column -> float, or `default` if it isn't one.

    For columns with no thousands/decimal ambiguity — `points`, `week`,
    a delta already carried as a wire number. A column priced in euros or
    expressed as a percentage/ratio has that ambiguity; read it with
    `ffcore.parse.money()` / `.ratio()` / `.pct100()` instead, not this.

    Never raises: missing, empty, `None`, and unparseable all fall through
    to `default`, matching every hand-written `try/except (TypeError,
    ValueError)` this replaces.
    """
    v = row.get(col)
    if v is None:
        return default
    s = v.strip() if isinstance(v, str) else v
    if s == "":
        return default
    try:
        return float(s)
    except (TypeError, ValueError):
        return default


def whole(row, col: str, default=None):
    """Like `num()`, but int — via `float()` first.

    `int("12.0")` raises; every whole-number column in this store still
    occasionally arrives float-shaped (a `jornada` or `week` written out by
    something that treats all numbers as floats). Going through `float()`
    first reads "12.0" the same as "12", matching what a hand-rolled
    `int(float(x))` at the call site already assumed.
    """
    v = num(row, col, default=None)
    if v is None:
        return default
    try:
        return int(v)
    except (TypeError, ValueError, OverflowError):
        return default


def flag(row, col: str, default: bool = False) -> bool:
    """A boolean-ish column -> bool.

    The store's own writers (see `sources.py`'s `shielded`/`from_market`
    columns) spell a bool as `str(bool(x)).lower()` — "true" or "false" —
    with "" for "never observed" rather than a false value, so "" must NOT
    read as `False` when the caller wants to distinguish "known false"
    from "unknown" (it does read as `default` here, same as every other
    missing case in this module; a caller needing the three-way split
    should read the raw text instead of calling `flag()`).

    One other spelling is in live use: `methodology.py` reads a `home`
    flag on an internally-built row (not a stored CSV column) as `"1"`.
    Both are accepted here, case-insensitively, since both are real:
    "true" and "1" read as `True`; everything else, including "false",
    "0", "", and missing, reads as `default` when `default` is False or
    as the literal boolean otherwise. No table in this store spells a
    bool "yes", so that spelling is deliberately NOT accepted — adding it
    on spec would accept input the data never produces and could hide a
    genuinely malformed column instead of failing loud.
    """
    v = row.get(col)
    if not isinstance(v, str):
        return default
    s = v.strip().lower()
    if s in ("true", "1"):
        return True
    if s in ("false", "0"):
        return False
    return default


# ---------------------------------------------------------------------------
# Column-name constants, one group per tidy table. Copied verbatim from the
# CSV headers on disk (2026-09-16) — do not add a column here that the file
# does not actually have.

class MATCHES:
    """data/tidy/matches.csv — 380 real matches, 198 copies each (rescan)."""
    OBSERVED_AT = "observed_at"
    SOURCE = "source"
    MATCH_ID = "match_id"
    PATH = "path"
    JORNADA = "jornada"
    HOME = "home"
    AWAY = "away"
    SCORE = "score"


class STARTERS:
    """data/tidy/starters.csv — keyed (match_id, player); 2,512 real keys."""
    OBSERVED_AT = "observed_at"
    SOURCE = "source"
    MATCH_ID = "match_id"
    TEAM_SLUG = "team_slug"
    PLAYER_NAME = "player_name"
    PLAYER_SLUG = "player_slug"
    ROLE = "role"
    MINUTE = "minute"


class MARKET:
    """data/tidy/market.csv — the live listing/valuation feed, keyed ff_id.

    Not the same table as API_MARKET below despite the similar name: this
    one is the app's public valuation feed (money-shaped VALUE/DELTA_1D —
    read with `ffcore.parse.money()`, and DELTA_PCT_1D with `.ratio()` or
    `.pct100()`), keyed by `ff_id`. API_MARKET is the authenticated
    per-listing feed (bids, seller, expiry), keyed by its own `market_id`
    — a listing id, unrelated to CLUBS.MARKET_ID below, which is a
    crosswalk id for a *club* on a different external site. Three
    same-ish names, three different things; do not conflate them.
    """
    OBSERVED_AT = "observed_at"
    FF_ID = "ff_id"
    NAME = "name"
    POSITION = "position"
    TEAM_ID = "team_id"
    TEAM = "team"
    VALUE = "value"          # money-shaped — ffcore.parse.money()
    DELTA_1D = "delta_1d"    # money-shaped — ffcore.parse.money()
    DELTA_PCT_1D = "delta_pct_1d"  # ratio-shaped — ffcore.parse.ratio()/pct100()


class LINEUPS:
    """data/tidy/lineups.csv — pre-match start-probability feed.

    Same PLAYER_NAME/PLAYER_SLUG/ROLE column names as STARTERS above, but
    a different moment: STARTERS is what actually happened (post-match,
    with MINUTE played); LINEUPS is a pre-match prediction (START_PCT,
    STATUS). STATUS here is player FITNESS ("ok"/"doubt"/"suspended"/
    "injured"/"unavailable", "" for "not stated") — the same concept as
    API_MARKET/API_TEAMS/API_PLAYERS_ALL's PLAYER_STATUS column below,
    spelled differently only because this table has no separate listing-
    or offer-status column to disambiguate from. Do not confuse either
    with API_MARKET.STATUS (a market LISTING's state) or API_OFFERS.STATUS
    (an OFFER's state) or FEEDS.STATUS (a scrape's success/failure) — five
    tables, five "status" columns, five different things.
    """
    OBSERVED_AT = "observed_at"
    SOURCE = "source"
    TEAM_SLUG = "team_slug"
    PLAYER_NAME = "player_name"
    PLAYER_SLUG = "player_slug"
    ROLE = "role"
    START_PCT = "start_pct"  # percentage-shaped if read as text — ffcore.parse.pct100()
    STATUS = "status"
    NOTE = "note"


class API_STATS:
    """data/tidy/api_stats.csv — keyed (player_id, week, stat); NOT
    latest_only-safe (98% of keys live only in older snapshots)."""
    OBSERVED_AT = "observed_at"
    SOURCE = "source"
    PLAYER_ID = "player_id"
    WEEK = "week"
    STAT = "stat"
    VALUE = "value"
    POINTS = "points"


class PERJORNADA:
    """data/season/live/perjornada_*.csv — full history required; points
    are diffed over time, so only the newest FILE (not row) is "current"."""
    FROM_STAMP = "from_stamp"
    TO_STAMP = "to_stamp"
    SEASON = "season"
    FF_ID = "ff_id"
    PLAYER_NAME = "player_name"
    PLAYER_NAME_FULL = "player_name_full"
    TEAM = "team"
    POINTS_DELTA = "points_delta"
    GAMES_DELTA = "games_delta"
    POINTS_TOTAL = "points_total"
    GAMES_TOTAL = "games_total"
    JORNADA = "jornada"


# Wave-2 tables: named in the rationalization plan's own concept ledger as
# read by production code with no constants, so migrating their call sites
# would otherwise hand-write the column names right back. Added after the
# first six once that gap was reported; same verbatim-from-header rule.

class PLAYERS:
    """data/tidy/players.csv — the identity crosswalk row per player: one
    line tying together every site's id/slug for the same person."""
    PLAYER_ID = "player_id"
    NAME = "name"
    CLUB_ID = "club_id"
    FF_SLUG = "ff_slug"
    AF_SLUG = "af_slug"
    APP_ID = "app_id"
    UNDERSTAT_ID = "understat_id"
    APP_NAMES = "app_names"


class CLUBS:
    """data/tidy/clubs.csv — the identity crosswalk row per club.

    MARKET_ID here is a crosswalk id for this club on the external
    valuation site — NOT a listing id. Unrelated to API_MARKET.MARKET_ID
    below (a per-listing id) despite the identical column name; see the
    note on MARKET above for the third lookalike.
    """
    CLUB_ID = "club_id"
    MARKET = "market"
    FF_SLUG = "ff_slug"
    ELO = "elo"
    MARKET_ID = "market_id"
    AF_ID = "af_id"
    ALIASES = "aliases"


class FIXTURES:
    """data/tidy/fixtures.csv — the schedule: kickoff times for matches not
    yet played. MATCHES above is the same match_id space, but observed
    results; FIXTURES is the future half `tidy.JornadaClock` is built from."""
    OBSERVED_AT = "observed_at"
    SOURCE = "source"
    MATCH_ID = "match_id"
    KICKOFF = "kickoff"
    HOME = "home"
    AWAY = "away"
    HOME_ID = "home_id"
    AWAY_ID = "away_id"


class ELO:
    """data/tidy/elo.csv — a club's rating over time, snapshotted per
    observation. CLUBS.ELO above is that same club's rating at crosswalk-
    build time (one column on the identity row); this table is the history
    the crosswalk value was itself read from at some point."""
    OBSERVED_AT = "observed_at"
    SOURCE = "source"
    CLUB = "club"
    ELO = "elo"


class UNDERSTAT_PLAYERS:
    """data/tidy/understat_players.csv — understat.com's per-season shot
    and xG/xA feed, one row per player per season snapshot."""
    OBSERVED_AT = "observed_at"
    SOURCE = "source"
    SEASON = "season"
    UNDERSTAT_ID = "understat_id"
    PLAYER_NAME = "player_name"
    TEAM_TITLE = "team_title"
    TEAM = "team"
    POSITION = "position"
    GAMES = "games"
    MINUTES = "minutes"
    GOALS = "goals"
    ASSISTS = "assists"
    XG = "xg"
    XA = "xa"
    NPG = "npg"
    NPXG = "npxg"
    SHOTS = "shots"
    KEY_PASSES = "key_passes"


class API_MARKET:
    """data/tidy/api_market.csv — the authenticated per-listing feed (bids,
    seller, expiry), keyed by its own MARKET_ID (a listing id — see the
    note on MARKET above for why this is not the same id space as
    MARKET.FF_ID or CLUBS.MARKET_ID despite all three sharing the word).

    STATUS is the LISTING's state (e.g. on_sale); PLAYER_STATUS is the
    player's fitness, spelled the same way LINEUPS.STATUS is — see the
    note there. MARKET_VALUE/SALE_PRICE/BID_MONEY are money-shaped —
    ffcore.parse.money().
    """
    OBSERVED_AT = "observed_at"
    SOURCE = "source"
    MARKET_ID = "market_id"
    PLAYER_ID = "player_id"
    PLAYER_NAME = "player_name"
    PLAYER_NAME_FULL = "player_name_full"
    POSITION_ID = "position_id"
    MARKET_VALUE = "market_value"  # money-shaped — ffcore.parse.money()
    SALE_PRICE = "sale_price"      # money-shaped — ffcore.parse.money()
    BIDS = "bids"
    SELLER = "seller"
    STATUS = "status"
    PLAYER_STATUS = "player_status"
    SHIELDED = "shielded"          # bool-ish, see flag()
    EXPIRES_AT = "expires_at"
    BID_ID = "bid_id"
    BID_MONEY = "bid_money"        # money-shaped — ffcore.parse.money()
    BID_STATUS = "bid_status"


class API_PLAYERS:
    """data/tidy/api_players.csv — one team's authenticated roster snapshot
    (market_value, no points) — see API_PLAYERS_ALL below for the
    every-team version and API_LINEUP for the per-week-slot version."""
    OBSERVED_AT = "observed_at"
    SOURCE = "source"
    TEAM_ID = "team_id"
    PLAYER_ID = "player_id"
    PLAYER_NAME = "player_name"
    PLAYER_NAME_FULL = "player_name_full"
    POSITION_ID = "position_id"
    MARKET_VALUE = "market_value"  # money-shaped — ffcore.parse.money()


class API_TEAMS:
    """data/tidy/api_teams.csv — one manager's own squad, roster rows
    carrying league POINTS and a buyout clause — distinct from
    API_STANDINGS below, which is one row per manager, not per player."""
    OBSERVED_AT = "observed_at"
    SOURCE = "source"
    TEAM_ID = "team_id"
    MANAGER = "manager"
    PLAYER_ID = "player_id"
    PLAYER_NAME = "player_name"
    PLAYER_NAME_FULL = "player_name_full"
    POSITION_ID = "position_id"
    MARKET_VALUE = "market_value"  # money-shaped — ffcore.parse.money()
    POINTS = "points"
    BUYOUT = "buyout"              # money-shaped — ffcore.parse.money()
    BUYOUT_UNTIL = "buyout_until"
    PLAYER_STATUS = "player_status"
    PLAYER_TEAM_ID = "player_team_id"


class FEEDS:
    """data/tidy/feeds.csv — one row per request per sweep, so staleness
    of a scrape is visible even after it starts failing silently upstream.
    STATUS here is the FETCH's own success/failure, unrelated to any of
    the player- or listing-status columns noted above."""
    OBSERVED_AT = "observed_at"
    PAGE = "page"
    STATUS = "status"
    SECONDS = "seconds"


class TRANSACTIONS:
    """data/tidy/transactions.csv — the ledger `src/ledger.py` rebuilds
    from the app's activity feed on every run; file starts with `#`
    comment lines (not read by csv.DictReader as data) before the real
    header. FROM/TO name the pool as one side of every deal — the
    counterparty in a manager-to-manager transfer can't be recovered from
    the feed, per the file's own header comment. FROM and TO collide with
    Python keywords as bare names, hence the trailing underscore here.
    """
    DATE = "date"
    PLAYER = "player"
    PLAYER_ID = "player_id"
    FROM_ = "from"
    TO_ = "to"
    PRICE = "price"  # money-shaped — ffcore.parse.money()
    NOTE = "note"


class API_ACTIVITY:
    """data/tidy/api_activity.csv — the raw authenticated activity feed
    TRANSACTIONS above is rebuilt from; AT is the event's own timestamp,
    separate from OBSERVED_AT (when this row was scraped)."""
    OBSERVED_AT = "observed_at"
    SOURCE = "source"
    ACTIVITY_ID = "activity_id"
    AT = "at"
    KIND = "kind"
    USER_ID = "user_id"
    PLAYER_ID = "player_id"
    AMOUNT = "amount"  # money-shaped — ffcore.parse.money()
    WEEK = "week"


class API_LINEUP:
    """data/tidy/api_lineup.csv — one row per (week, formation slot):
    which player started where and the POINTS that slot scored. SNAPSHOT_AT
    is when the app computed this, separate from OBSERVED_AT (scrape time)."""
    OBSERVED_AT = "observed_at"
    SOURCE = "source"
    WEEK = "week"
    SLOT = "slot"
    FORMATION = "formation"
    PLAYER_ID = "player_id"
    PLAYER_NAME = "player_name"
    PLAYER_NAME_FULL = "player_name_full"
    POSITION_ID = "position_id"
    MARKET_VALUE = "market_value"  # money-shaped — ffcore.parse.money()
    SNAPSHOT_AT = "snapshot_at"
    POINTS = "points"


class API_OFFERS:
    """data/tidy/api_offers.csv — pending/settled bids against a listing,
    one dedicated call per player (an all-empty row is itself the reading
    of "nothing pending" — see the load-bearing comment in sources.py next
    to where this row shape is built). STATUS is the OFFER's own state
    (e.g. "pending"), not the listing's (API_MARKET.STATUS) or the
    player's fitness (API_MARKET.PLAYER_STATUS)."""
    OBSERVED_AT = "observed_at"
    SOURCE = "source"
    PLAYER_TEAM_ID = "player_team_id"
    OFFER_ID = "offer_id"
    MONEY = "money"  # money-shaped — ffcore.parse.money()
    STATUS = "status"
    CREATED_AT = "created_at"
    EXPIRES_AT = "expires_at"
    FROM_MARKET = "from_market"  # bool-ish, see flag()


class API_PLAYERS_ALL:
    """data/tidy/api_players_all.csv — every team's authenticated roster
    in one sweep (API_PLAYERS above is one team at a time); carries
    PLAYER_STATUS but not the per-slot POINTS that API_LINEUP has."""
    OBSERVED_AT = "observed_at"
    SOURCE = "source"
    TEAM_ID = "team_id"
    PLAYER_ID = "player_id"
    PLAYER_NAME = "player_name"
    PLAYER_NAME_FULL = "player_name_full"
    POSITION_ID = "position_id"
    MARKET_VALUE = "market_value"  # money-shaped — ffcore.parse.money()
    PLAYER_STATUS = "player_status"


class API_STANDINGS:
    """data/tidy/api_standings.csv — the league table, one row per manager
    per snapshot (API_TEAMS above is one row per PLAYER on a manager's
    squad — do not confuse the two POSITION-flavoured columns: this
    table's POSITION is the manager's league rank, not a football
    position)."""
    OBSERVED_AT = "observed_at"
    SOURCE = "source"
    TEAM_ID = "team_id"
    USER_ID = "user_id"
    MANAGER = "manager"
    POSITION = "position"
    PREVIOUS_POSITION = "previous_position"
    TEAM_POINTS = "team_points"
    FIXTURE_POINTS = "fixture_points"
    TEAM_VALUE = "team_value"  # money-shaped — ffcore.parse.money()
    TEAM_MONEY = "team_money"  # money-shaped — ffcore.parse.money()
    BANNED = "banned"          # bool-ish, see flag()
    STARTING_WEEK = "starting_week"


class RESULTS_HISTORY:
    """data/tidy/results_history.csv — final match stats (score, xG, shots,
    corners) for completed matches, keyed by team names rather than
    MATCHES.MATCH_ID — a different join key from every other match table
    here, so joining this to MATCHES/FIXTURES goes through name matching
    (ffcore.text), not match_id. HOME_NAME/AWAY_NAME are the full name as
    the odds source spelled it; HOME/AWAY are that name's slug (falls back
    to HOME_NAME/AWAY_NAME itself when no slug was derived — see
    sources.py's `r["home"] or r["home_name"]`), not a football home/away
    flag and not the same values as MATCHES.HOME/AWAY."""
    OBSERVED_AT = "observed_at"
    SOURCE = "source"
    SEASON = "season"
    DATE = "date"
    HOME_NAME = "home_name"
    AWAY_NAME = "away_name"
    HOME = "home"
    AWAY = "away"
    HOME_GOALS = "home_goals"
    AWAY_GOALS = "away_goals"
    HOME_XG = "home_xg"
    AWAY_XG = "away_xg"
    HOME_SHOTS = "home_shots"
    AWAY_SHOTS = "away_shots"
    HOME_SHOTS_ON_TARGET = "home_shots_on_target"
    AWAY_SHOTS_ON_TARGET = "away_shots_on_target"
    HOME_CORNERS = "home_corners"
    AWAY_CORNERS = "away_corners"


# ---------------------------------------------------------------------------

def _selftest() -> None:
    n = 0

    # text(): the three "nothing here" shapes all collapse to default.
    n += 1; assert text({"name": " Lamine Yamal "}, "name") == "Lamine Yamal"
    n += 1; assert text({}, "name") == ""
    n += 1; assert text({"name": None}, "name") == ""
    n += 1; assert text({"name": "   "}, "name") == ""
    n += 1; assert text({}, "name", default="?") == "?"
    n += 1; assert text({"name": None}, "name", default="?") == "?"
    n += 1; assert text({"name": "   "}, "name", default="?") == "?"

    # num(): missing, empty, None, unparseable all give default; never raises.
    n += 1; assert num({"points": "4.5"}, "points") == 4.5
    n += 1; assert num({"points": " 4.5 "}, "points") == 4.5
    n += 1; assert num({}, "points") is None
    n += 1; assert num({"points": ""}, "points") is None
    n += 1; assert num({"points": None}, "points") is None
    n += 1; assert num({"points": "abc"}, "points") is None
    n += 1; assert num({}, "points", default=0.0) == 0.0
    n += 1; assert num({"points": "abc"}, "points", default=-1.0) == -1.0

    # whole(): float-shaped strings coerce like int(float(x)); "abc" -> default.
    n += 1; assert whole({"jornada": "12.0"}, "jornada") == 12
    n += 1; assert whole({"jornada": "12"}, "jornada") == 12
    n += 1; assert whole({"jornada": "12.9"}, "jornada") == 12
    n += 1; assert whole({}, "jornada") is None
    n += 1; assert whole({"jornada": ""}, "jornada") is None
    n += 1; assert whole({"jornada": None}, "jornada") is None
    n += 1; assert whole({"jornada": "abc"}, "jornada") is None
    n += 1; assert whole({"jornada": "abc"}, "jornada", default=0) == 0

    # flag(): the store's own spellings ("true"/"false" from str(bool).lower(),
    # plus methodology.py's "1"), case-insensitive; everything else -> default.
    n += 1; assert flag({"shielded": "true"}, "shielded") is True
    n += 1; assert flag({"shielded": "TRUE"}, "shielded") is True
    n += 1; assert flag({"shielded": "false"}, "shielded") is False
    n += 1; assert flag({"home": "1"}, "home") is True
    n += 1; assert flag({"home": "0"}, "home") is False
    n += 1; assert flag({"shielded": ""}, "shielded") is False
    n += 1; assert flag({"shielded": ""}, "shielded", default=True) is True
    n += 1; assert flag({}, "shielded") is False
    n += 1; assert flag({"shielded": None}, "shielded") is False
    n += 1; assert flag({"shielded": "yes"}, "shielded") is False  # not a real spelling

    # Column constants against the real headers on disk, not against
    # themselves — a renamed column in the CSV should break this test,
    # the way a hardcoded string literal here never would.
    root = Path(os.environ.get("FF_ROOT", "./data"))

    def header(name: str) -> set[str]:
        """First real (non `#`-comment) header row of a tidy CSV, as a set."""
        with open(root / "tidy" / name, newline="") as f:
            for row in csv.reader(f):
                if row and not row[0].startswith("#"):
                    return set(row)
        return set()

    # Every table with a plain data/tidy/<name>.csv file (PERJORNADA is
    # excluded: it lives under season/live/perjornada_*.csv, a glob of
    # dated files, not one fixed path). Every public string attribute on
    # each class is checked, not a hand-picked representative — a class
    # with one column checked and the rest unchecked is exactly the "test
    # asserts a string against itself" failure mode this exists to avoid.
    tables = [
        (MATCHES, "matches.csv"), (STARTERS, "starters.csv"),
        (MARKET, "market.csv"), (LINEUPS, "lineups.csv"),
        (API_STATS, "api_stats.csv"), (PLAYERS, "players.csv"),
        (CLUBS, "clubs.csv"), (FIXTURES, "fixtures.csv"),
        (ELO, "elo.csv"), (UNDERSTAT_PLAYERS, "understat_players.csv"),
        (API_MARKET, "api_market.csv"), (API_PLAYERS, "api_players.csv"),
        (API_TEAMS, "api_teams.csv"), (FEEDS, "feeds.csv"),
        (TRANSACTIONS, "transactions.csv"),
        (API_ACTIVITY, "api_activity.csv"), (API_LINEUP, "api_lineup.csv"),
        (API_OFFERS, "api_offers.csv"),
        (API_PLAYERS_ALL, "api_players_all.csv"),
        (API_STANDINGS, "api_standings.csv"),
        (RESULTS_HISTORY, "results_history.csv"),
    ]
    if (root / "tidy" / "matches.csv").exists():
        for cls, fname in tables:
            hdr = header(fname)
            for attr, col in vars(cls).items():
                if attr.startswith("_") or not isinstance(col, str):
                    continue
                n += 1
                assert col in hdr, \
                    f"{fname}: {cls.__name__}.{attr}={col!r} not in real header {hdr}"
    # else: running outside the repo checkout (no data/tidy/) — the
    # hand-fed dict cases above still ran; skipping this block rather than
    # failing lets the module's own unit behaviour be checked standalone.

    print(f"ffcore.schema self-test OK ({n} cases)")


if __name__ == "__main__":
    _selftest()
