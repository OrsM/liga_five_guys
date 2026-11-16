"""The observations (ingest): every source table's rows as typed rows, read once and sorted by (observed_at,
rowid). The only place that reads storage for the sweep.

TABLES names the sources (the src_<name> tables of the database); each has a NamedTuple (its needed columns) and a
grain: () for a snapshot table (every fetch repeats the whole table) or the key columns of a table that keeps a
row per key. read(db, until) and of(rows) give an Observations: for each table its rows in (observed_at, rowid)
order, their stamps, and refolds, the number of rows that arrived with a stamp before an earlier row of their
source (storage order is not time order there, so a fold that already passed them must start again). Selectors:
rows_of, stamps_of, refolds_of, key_of."""
from __future__ import annotations

import operator
import sqlite3
from collections.abc import Iterable, Mapping
from typing import Any, NamedTuple


class Market(NamedTuple):
    observed_at: str
    source: str
    value: float | None
    ff_id: str
    position: str
    club: str


class Lineups(NamedTuple):
    observed_at: str
    source: str
    start_pct: float | None
    player_slug: str
    player_name: str
    team_slug: str
    status: str
    note: str


class Matches(NamedTuple):
    observed_at: str
    source: str
    jornada: int | None
    match_id: str
    path: str
    home: str
    away: str
    score: str
    kickoff: str


class Points(NamedTuple):
    observed_at: str
    source: str
    points: float | None
    games: float | None
    avg: float | None
    ff_id: str
    player_name: str
    player_name_full: str
    team: str
    season: str


class ApiTeams(NamedTuple):
    observed_at: str
    source: str
    market_value: float | None
    buyout: float | None
    points: float | None
    player_id: str
    team_id: str
    manager: str
    player_name: str
    player_name_full: str
    player_status: str
    player_team_id: str


class ApiStandings(NamedTuple):
    observed_at: str
    source: str
    position: int | None
    team_points: float | None
    fixture_points: float | None
    team_value: float | None
    team_money: float | None
    team_id: str
    user_id: str
    manager: str


class ApiMarket(NamedTuple):
    observed_at: str
    source: str
    market_value: float | None
    sale_price: float | None
    bid_money: float | None
    bids: int | None
    player_id: str
    market_id: str
    player_name: str
    player_name_full: str
    seller: str
    status: str
    player_status: str
    expires_at: str
    bid_id: str
    bid_status: str


class ApiOffers(NamedTuple):
    observed_at: str
    source: str
    money: float | None
    player_team_id: str
    offer_id: str
    status: str
    created_at: str
    expires_at: str
    from_market: str


class ApiLineup(NamedTuple):
    observed_at: str
    source: str
    market_value: float | None
    points: float | None
    player_id: str
    slot: str
    formation: str
    player_name: str
    player_name_full: str


class ApiPlayersAll(NamedTuple):
    observed_at: str
    source: str
    market_value: float | None
    player_id: str
    team_id: str
    player_name: str
    player_name_full: str
    player_status: str


class ResultsHistory(NamedTuple):
    observed_at: str
    source: str
    home_goals: float | None
    away_goals: float | None
    season: str
    date: str
    home_name: str
    away_name: str
    home: str
    away: str


class Starters(NamedTuple):
    observed_at: str
    source: str
    minute: float | None
    player_slug: str
    player_name: str
    team_slug: str
    match_id: str
    role: str


class ApiActivity(NamedTuple):
    observed_at: str
    source: str
    amount: float | None
    player_id: str
    activity_id: str
    at: str
    kind: str
    user_id: str
    counterparty: str


class Source(NamedTuple):
    row: Any
    grain: tuple[str, ...]


TABLES: dict[str, Source] = {
    "market": Source(Market, ()),
    "lineups": Source(Lineups, ()),
    "matches": Source(Matches, ("match_id",)),
    "points": Source(Points, ("season", "ff_id")),
    "api_teams": Source(ApiTeams, ()),
    "api_standings": Source(ApiStandings, ()),
    "api_market": Source(ApiMarket, ()),
    "api_offers": Source(ApiOffers, ()),
    "api_lineup": Source(ApiLineup, ()),
    "api_players_all": Source(ApiPlayersAll, ("player_id",)),
    "results_history": Source(ResultsHistory, ("season", "date", "home_name", "away_name")),
    "starters": Source(Starters, ("match_id", "team_slug", "player_slug")),
    "api_activity": Source(ApiActivity, ("activity_id",)),
}


class Table(NamedTuple):
    rows: tuple[Any, ...]
    stamps: tuple[str, ...]
    refolds: int


EMPTY = Table((), (), 0)


class Observations(NamedTuple):
    tables: Mapping[str, Table]


def key_of(table: str, row: Any) -> tuple[str, ...]:
    """The grain key of a row of table (empty for a snapshot table)."""
    return tuple(getattr(row, c) for c in TABLES[table].grain)


def arranged(rows: Iterable[Any]) -> Table:
    """rows (in storage order) as a Table: stably sorted by observed_at; refolds counts the rows whose stamp
    is before an earlier row's of the same source."""
    held = list(rows)
    last: dict[str, str] = {}
    refolds = 0
    for r in held:
        if r.observed_at < last.get(r.source, ""):
            refolds += 1
        else:
            last[r.source] = r.observed_at
    held.sort(key=operator.attrgetter("observed_at"))
    return Table(tuple(held), tuple(r.observed_at for r in held), refolds)


def of(tables: Mapping[str, Iterable[Any]]) -> Observations:
    """The Observations of rows given per table name, each in storage order."""
    return Observations({name: arranged(rows) for name, rows in tables.items()})


def read(db: sqlite3.Connection, names: Iterable[str] = tuple(TABLES), until: str | None = None) -> Observations:
    """The named tables of db, rows with observed_at <= until (all when None), in one range scan each."""
    def scan(name: str) -> Iterable[Any]:
        row = TABLES[name].row
        sql = "SELECT %s FROM src_%s%s ORDER BY rowid" % (
            ", ".join(row._fields), name, "" if until is None else " WHERE observed_at <= ?")
        return map(row._make, db.execute(sql, () if until is None else (until,)))
    return of({name: scan(name) for name in names})


def rows_of(obs: Observations, table: str) -> tuple[Any, ...]:
    """The rows of table in (observed_at, rowid) order (none for a table not read)."""
    return obs.tables.get(table, EMPTY).rows


def stamps_of(obs: Observations, table: str) -> tuple[str, ...]:
    """The observed_at of each row of rows_of(obs, table), in the same order."""
    return obs.tables.get(table, EMPTY).stamps


def refolds_of(obs: Observations) -> int:
    """How many rows, over all tables, arrived out of time order."""
    return sum(t.refolds for t in obs.tables.values())


def _database(rows: Mapping[str, Iterable[Any]]) -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    for name, held in rows.items():
        fields = TABLES[name].row._fields
        db.execute("CREATE TABLE src_%s (%s)" % (name, ", ".join(fields)))
        db.executemany("INSERT INTO src_%s VALUES (%s)" % (name, ", ".join("?" * len(fields))), list(held))
    return db


def _selftest() -> None:
    """Rows come back in (observed_at, rowid) order whatever the storage order; refolds counts exactly the rows
    behind their source's clock; until bounds the scan; every table's fields are the stored columns."""
    def m(at: str, ff: str, source: str = "ff") -> Market:
        return Market(at, source, 1.0, ff, "DEL", "RMA")
    stored = [m("2026-11-02", "a"), m("2026-11-01", "b"), m("2026-11-02", "c"), m("2026-11-01", "d", "other"),
              m("2026-11-03", "e")]
    obs = read(_database({"market": stored}), ["market"])
    assert [r.ff_id for r in rows_of(obs, "market")] == ["b", "d", "a", "c", "e"]
    assert list(stamps_of(obs, "market")) == sorted(stamps_of(obs, "market"))
    assert refolds_of(obs) == 1
    assert refolds_of(of({"market": sorted(stored, key=lambda r: r.observed_at)})) == 0
    assert refolds_of(of({"market": stored[::-1]})) >= 1
    assert [r.ff_id for r in rows_of(read(_database({"market": stored}), ["market"], "2026-11-01"), "market")] == ["b", "d"]
    assert key_of("matches", Matches("t", "s", 1, "m1", "", "", "", "", "")) == ("m1",)
    assert key_of("market", stored[0]) == ()
    for name, source in TABLES.items():
        assert set(source.grain) <= set(source.row._fields) and source.row._fields[:2] == ("observed_at", "source")
        assert refolds_of(read(_database({name: []}), [name])) == 0
    print("ffcore.observations self-test OK")


if __name__ == "__main__":
    _selftest()
