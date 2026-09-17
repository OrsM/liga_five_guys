
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
    v = row.get(col) or ""
    v = v.strip() if isinstance(v, str) else str(v).strip()
    return v if v else default


def num(row, col: str, default=None):
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
    v = num(row, col, default=None)
    if v is None:
        return default
    try:
        return int(v)
    except (TypeError, ValueError, OverflowError):
        return default


def flag(row, col: str, default: bool = False) -> bool:
    v = row.get(col)
    if not isinstance(v, str):
        return default
    s = v.strip().lower()
    if s in ("true", "1"):
        return True
    if s in ("false", "0"):
        return False
    return default



class MATCHES:
    OBSERVED_AT = "observed_at"
    SOURCE = "source"
    MATCH_ID = "match_id"
    PATH = "path"
    JORNADA = "jornada"
    HOME = "home"
    AWAY = "away"
    SCORE = "score"


class STARTERS:
    OBSERVED_AT = "observed_at"
    SOURCE = "source"
    MATCH_ID = "match_id"
    TEAM_SLUG = "team_slug"
    PLAYER_NAME = "player_name"
    PLAYER_SLUG = "player_slug"
    ROLE = "role"
    MINUTE = "minute"


class MARKET:
    OBSERVED_AT = "observed_at"
    FF_ID = "ff_id"
    NAME = "name"
    POSITION = "position"
    TEAM_ID = "team_id"
    TEAM = "team"
    VALUE = "value"
    DELTA_1D = "delta_1d"
    DELTA_PCT_1D = "delta_pct_1d"


class LINEUPS:
    OBSERVED_AT = "observed_at"
    SOURCE = "source"
    TEAM_SLUG = "team_slug"
    PLAYER_NAME = "player_name"
    PLAYER_SLUG = "player_slug"
    ROLE = "role"
    START_PCT = "start_pct"
    STATUS = "status"
    NOTE = "note"


class API_STATS:
    OBSERVED_AT = "observed_at"
    SOURCE = "source"
    PLAYER_ID = "player_id"
    WEEK = "week"
    STAT = "stat"
    VALUE = "value"
    POINTS = "points"


class PERJORNADA:
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



class PLAYERS:
    PLAYER_ID = "player_id"
    NAME = "name"
    CLUB_ID = "club_id"
    FF_SLUG = "ff_slug"
    AF_SLUG = "af_slug"
    APP_ID = "app_id"
    UNDERSTAT_ID = "understat_id"
    APP_NAMES = "app_names"


class CLUBS:
    CLUB_ID = "club_id"
    MARKET = "market"
    FF_SLUG = "ff_slug"
    ELO = "elo"
    MARKET_ID = "market_id"
    AF_ID = "af_id"
    ALIASES = "aliases"


class FIXTURES:
    OBSERVED_AT = "observed_at"
    SOURCE = "source"
    MATCH_ID = "match_id"
    KICKOFF = "kickoff"
    HOME = "home"
    AWAY = "away"
    HOME_ID = "home_id"
    AWAY_ID = "away_id"


class ELO:
    OBSERVED_AT = "observed_at"
    SOURCE = "source"
    CLUB = "club"
    ELO = "elo"


class UNDERSTAT_PLAYERS:
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
    OBSERVED_AT = "observed_at"
    SOURCE = "source"
    MARKET_ID = "market_id"
    PLAYER_ID = "player_id"
    PLAYER_NAME = "player_name"
    PLAYER_NAME_FULL = "player_name_full"
    POSITION_ID = "position_id"
    MARKET_VALUE = "market_value"
    SALE_PRICE = "sale_price"
    BIDS = "bids"
    SELLER = "seller"
    STATUS = "status"
    PLAYER_STATUS = "player_status"
    SHIELDED = "shielded"
    EXPIRES_AT = "expires_at"
    BID_ID = "bid_id"
    BID_MONEY = "bid_money"
    BID_STATUS = "bid_status"


class API_PLAYERS:
    OBSERVED_AT = "observed_at"
    SOURCE = "source"
    TEAM_ID = "team_id"
    PLAYER_ID = "player_id"
    PLAYER_NAME = "player_name"
    PLAYER_NAME_FULL = "player_name_full"
    POSITION_ID = "position_id"
    MARKET_VALUE = "market_value"


class API_TEAMS:
    OBSERVED_AT = "observed_at"
    SOURCE = "source"
    TEAM_ID = "team_id"
    MANAGER = "manager"
    PLAYER_ID = "player_id"
    PLAYER_NAME = "player_name"
    PLAYER_NAME_FULL = "player_name_full"
    POSITION_ID = "position_id"
    MARKET_VALUE = "market_value"
    POINTS = "points"
    BUYOUT = "buyout"
    BUYOUT_UNTIL = "buyout_until"
    PLAYER_STATUS = "player_status"
    PLAYER_TEAM_ID = "player_team_id"


class FEEDS:
    OBSERVED_AT = "observed_at"
    PAGE = "page"
    STATUS = "status"
    SECONDS = "seconds"


class TRANSACTIONS:
    DATE = "date"
    PLAYER = "player"
    PLAYER_ID = "player_id"
    FROM_ = "from"
    TO_ = "to"
    PRICE = "price"
    NOTE = "note"


class API_ACTIVITY:
    OBSERVED_AT = "observed_at"
    SOURCE = "source"
    ACTIVITY_ID = "activity_id"
    AT = "at"
    KIND = "kind"
    USER_ID = "user_id"
    PLAYER_ID = "player_id"
    AMOUNT = "amount"
    WEEK = "week"


class API_LINEUP:
    OBSERVED_AT = "observed_at"
    SOURCE = "source"
    WEEK = "week"
    SLOT = "slot"
    FORMATION = "formation"
    PLAYER_ID = "player_id"
    PLAYER_NAME = "player_name"
    PLAYER_NAME_FULL = "player_name_full"
    POSITION_ID = "position_id"
    MARKET_VALUE = "market_value"
    SNAPSHOT_AT = "snapshot_at"
    POINTS = "points"


class API_OFFERS:
    OBSERVED_AT = "observed_at"
    SOURCE = "source"
    PLAYER_TEAM_ID = "player_team_id"
    OFFER_ID = "offer_id"
    MONEY = "money"
    STATUS = "status"
    CREATED_AT = "created_at"
    EXPIRES_AT = "expires_at"
    FROM_MARKET = "from_market"


class API_PLAYERS_ALL:
    OBSERVED_AT = "observed_at"
    SOURCE = "source"
    TEAM_ID = "team_id"
    PLAYER_ID = "player_id"
    PLAYER_NAME = "player_name"
    PLAYER_NAME_FULL = "player_name_full"
    POSITION_ID = "position_id"
    MARKET_VALUE = "market_value"
    PLAYER_STATUS = "player_status"


class API_STANDINGS:
    OBSERVED_AT = "observed_at"
    SOURCE = "source"
    TEAM_ID = "team_id"
    USER_ID = "user_id"
    MANAGER = "manager"
    POSITION = "position"
    PREVIOUS_POSITION = "previous_position"
    TEAM_POINTS = "team_points"
    FIXTURE_POINTS = "fixture_points"
    TEAM_VALUE = "team_value"
    TEAM_MONEY = "team_money"
    BANNED = "banned"
    STARTING_WEEK = "starting_week"


class RESULTS_HISTORY:
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



def _selftest() -> None:
    n = 0

    n += 1
    assert text({"name": " Lamine Yamal "}, "name") == "Lamine Yamal"
    n += 1
    assert text({}, "name") == ""
    n += 1
    assert text({"name": None}, "name") == ""
    n += 1
    assert text({"name": "   "}, "name") == ""
    n += 1
    assert text({}, "name", default="?") == "?"
    n += 1
    assert text({"name": None}, "name", default="?") == "?"
    n += 1
    assert text({"name": "   "}, "name", default="?") == "?"

    n += 1
    assert num({"points": "4.5"}, "points") == 4.5
    n += 1
    assert num({"points": " 4.5 "}, "points") == 4.5
    n += 1
    assert num({}, "points") is None
    n += 1
    assert num({"points": ""}, "points") is None
    n += 1
    assert num({"points": None}, "points") is None
    n += 1
    assert num({"points": "abc"}, "points") is None
    n += 1
    assert num({}, "points", default=0.0) == 0.0
    n += 1
    assert num({"points": "abc"}, "points", default=-1.0) == -1.0

    n += 1
    assert whole({"jornada": "12.0"}, "jornada") == 12
    n += 1
    assert whole({"jornada": "12"}, "jornada") == 12
    n += 1
    assert whole({"jornada": "12.9"}, "jornada") == 12
    n += 1
    assert whole({}, "jornada") is None
    n += 1
    assert whole({"jornada": ""}, "jornada") is None
    n += 1
    assert whole({"jornada": None}, "jornada") is None
    n += 1
    assert whole({"jornada": "abc"}, "jornada") is None
    n += 1
    assert whole({"jornada": "abc"}, "jornada", default=0) == 0

    n += 1
    assert flag({"shielded": "true"}, "shielded") is True
    n += 1
    assert flag({"shielded": "TRUE"}, "shielded") is True
    n += 1
    assert flag({"shielded": "false"}, "shielded") is False
    n += 1
    assert flag({"home": "1"}, "home") is True
    n += 1
    assert flag({"home": "0"}, "home") is False
    n += 1
    assert flag({"shielded": ""}, "shielded") is False
    n += 1
    assert flag({"shielded": ""}, "shielded", default=True) is True
    n += 1
    assert flag({}, "shielded") is False
    n += 1
    assert flag({"shielded": None}, "shielded") is False
    n += 1
    assert flag({"shielded": "yes"}, "shielded") is False

    root = Path(os.environ.get("FF_ROOT", "./data"))

    def header(name: str) -> set[str]:
        with open(root / "tidy" / name, newline="") as f:
            for row in csv.reader(f):
                if row and not row[0].startswith("#"):
                    return set(row)
        return set()

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

    print(f"ffcore.schema self-test OK ({n} cases)")


if __name__ == "__main__":
    _selftest()
