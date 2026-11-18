"""The observations (ingest): every source table's rows as typed rows, read once and sorted by (observed_at,
rowid). The only place that reads storage for the sweep.

TABLES names the sources (the src_<name> tables of the database); each has a NamedTuple (its needed columns) and a
grain: () for a snapshot table (every fetch repeats the whole table) or the key columns of a table that keeps a
row per key, and the columns that with observed_at hold a row once (unique). read(db, names, until, latest) and of(rows) give an Observations: for each table its rows in (observed_at, rowid)
order, their stamps, and refolds, the number of rows that arrived with a stamp before an earlier row of their
source (storage order is not time order there, so a fold that already passed them must start again). Selectors:
rows_of, stamps_of, refolds_of, key_of. kept(table, rows), keeper(table) and newest(rows) are the rules parsed.read (the same
Observations from the parse cache) applies: the dedupe the tables do on insert, and the newest snapshot."""
from __future__ import annotations

import itertools
import operator
import sqlite3
from collections.abc import Callable, Iterable, Iterator, Mapping
from typing import Any, NamedTuple


class Market(NamedTuple):
    observed_at: str
    source: str
    value: float | None
    ff_id: str
    position: str
    club: str
    name: str = ""


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
    unique: tuple[str, ...]


TABLES: dict[str, Source] = {
    "market": Source(Market, (), ("source", "ff_id")),
    "lineups": Source(Lineups, (), ("source", "player_slug", "player_name", "team_slug")),
    "matches": Source(Matches, ("match_id",), ("match_id",)),
    "points": Source(Points, ("season", "ff_id"), ("season", "ff_id")),
    "api_teams": Source(ApiTeams, (), ("source", "team_id", "player_id")),
    "api_standings": Source(ApiStandings, (), ("source", "team_id")),
    "api_market": Source(ApiMarket, (), ("source", "market_id", "bid_id")),
    "api_offers": Source(ApiOffers, (), ("source", "player_team_id", "offer_id")),
    "api_lineup": Source(ApiLineup, (), ("source", "player_id")),
    "api_players_all": Source(ApiPlayersAll, ("player_id",), ("player_id",)),
    "results_history": Source(ResultsHistory, ("season", "date", "home_name", "away_name"),
                              ("season", "date", "home_name", "away_name")),
    "starters": Source(Starters, ("match_id", "team_slug", "player_slug"), ("match_id", "team_slug", "player_slug")),
    "api_activity": Source(ApiActivity, ("activity_id",), ("activity_id",)),
}


class Table(NamedTuple):
    rows: tuple[Any, ...]
    stamps: tuple[str, ...]
    refolds: int


EMPTY = Table((), (), 0)

POOL: dict[str, str] = {}


def pooled(values: Iterable[Any]) -> tuple[Any, ...]:
    """values with every str replaced by its one object in POOL (the pool is shared by every read): equal strings
    are the same object, the values are unchanged."""
    return tuple(POOL.setdefault(v, v) if type(v) is str else v for v in values)


class Observations(NamedTuple):
    tables: Mapping[str, Table]


def key_of(table: str, row: Any) -> tuple[str, ...]:
    """The grain key of a row of table (empty for a snapshot table)."""
    return tuple(getattr(row, c) for c in TABLES[table].grain)


class Stamped(NamedTuple):
    observed_at: str
    body: tuple[Any, ...]


class Part(NamedTuple):
    bodies: list[tuple[Any, ...]]
    marks: list[Any]
    distinct: bool


def _picker(table: str, cols: tuple[str, ...]) -> Callable[[tuple[Any, ...]], Any]:
    fields = TABLES[table].row._fields[1:]
    return operator.itemgetter(*(fields.index(c) for c in cols))


def part(table: str, bodies: list[tuple[Any, ...]]) -> Part:
    """The bodies of table (the fields after observed_at) with what keeper reads of them once: marks, the grain key of
    each body (its unique columns in a snapshot table), and whether no two marks are equal."""
    spec = TABLES[table]
    marks = list(map(_picker(table, spec.grain or spec.unique), bodies))
    return Part(bodies, marks, len(set(marks)) == len(marks))


def keeper(table: str) -> Callable[[str, Part], Iterable[tuple[Any, ...]]]:
    """A function that, given a stamp (not decreasing from call to call) and the next Part in storage order, returns the
    bodies of table that its storage keeps: a body is dropped when a kept body has the same unique columns (TABLES)
    and stamp, and, in a table with a grain, when it equals the last kept body of its grain key (None equals None). A
    body dropped is never the last of its key; one that changed, or came back to an earlier value after another, is
    kept. A Part that adds nothing new is answered without looking at its bodies one by one."""
    spec = TABLES[table]
    unique = _picker(table, spec.unique)
    grain = _picker(table, spec.grain) if spec.grain else None
    last: dict[Any, tuple[Any, ...]] = {}
    seen: set[Any] = set()
    at = None

    def keep(stamp: str, held: Part) -> Iterable[tuple[Any, ...]]:
        nonlocal at, seen
        if stamp != at:
            at, seen = stamp, set()
        if grain is None:
            if held.distinct and seen.isdisjoint(held.marks):
                seen.update(held.marks)
                return held.bodies
        elif list(map(last.get, held.marks)) == held.bodies:
            return ()
        out = []
        for body in held.bodies:
            u = unique(body)
            if u in seen:
                continue
            if grain is not None:
                k = grain(body)
                if last.get(k) == body:
                    continue
                last[k] = body
            seen.add(u)
            out.append(body)
        return out
    return keep


def kept(table: str, rows: Iterable[Stamped]) -> Iterator[Stamped]:
    """The rows of table that its storage keeps (keeper's rule), from rows in storage order with observed_at not
    decreasing (body: the fields after observed_at, in the order of the table's row type)."""
    keep = keeper(table)
    for at, group in itertools.groupby(rows, operator.attrgetter("observed_at")):
        for body in keep(at, part(table, [r.body for r in group])):
            yield Stamped(at, body)


def newest(rows: Iterable[Any]) -> list[Any]:
    """The rows whose observed_at is the greatest of their source's, in their order."""
    held = list(rows)
    top: dict[str, str] = {}
    for r in held:
        if r.observed_at > top.get(r.source, ""):
            top[r.source] = r.observed_at
    return [r for r in held if r.observed_at == top[r.source]]


def arranged(rows: Iterable[Any]) -> Table:
    """rows (in storage order) as a Table: stably sorted by observed_at; refolds counts the rows whose stamp
    is before an earlier row's of the same source."""
    held = list(rows)
    stamps = tuple(map(operator.attrgetter("observed_at"), held))
    if all(map(operator.le, stamps, stamps[1:])):
        return Table(tuple(held), stamps, 0)
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


def read(db: sqlite3.Connection, names: Iterable[str] = tuple(TABLES), until: str | None = None,
         latest: bool = False) -> Observations:
    """The named tables of db, rows with observed_at <= until (all when None), in one range scan each; every str
    field of every row is the pooled object (pooled), so a second read adds no second copy of a string. With latest,
    a table keeps only each source's newest snapshot in that range (the rows whose observed_at is the greatest of
    their source), as the whole range would leave in a fold that keeps the newest snapshot."""
    def scan(name: str) -> Iterable[Any]:
        row = TABLES[name].row
        bound = "" if until is None else " WHERE observed_at <= ?"
        keep = " WHERE (source, observed_at) IN (SELECT source, MAX(observed_at) FROM src_%s%s GROUP BY source)" % (
            name, bound) if latest else bound
        sql = "SELECT %s FROM src_%s%s ORDER BY rowid" % (", ".join(row._fields), name, keep)
        return map(row._make, map(pooled, db.execute(sql, () if until is None else (until,))))
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


def _selftest_parsed() -> None:
    """From a parsed fixture: a snapshot table keeps every fetch and drops a key repeated within one; a keyed table
    drops a repeated row, keeps a changed one, treats None as equal to None, and keeps a row that went back to an
    earlier value; until and latest bound the rows as read() does."""
    import json
    import tempfile
    from pathlib import Path

    from ffcore import parsed
    from ingest import fingerprint
    from sources import source

    def doc(page: str, name: str, rows: list[dict[str, str]]) -> tuple[str, str, list[dict[str, str]]]:
        ck = "%s:%s" % (page, name)
        return page, ck, rows

    def pt(ff: str, points: str, games: str = "") -> dict[str, str]:
        return {"source": "ff", "season": "2627", "ff_id": ff, "points": points, "games": games, "avg": ""}

    def mk(ff: str) -> dict[str, str]:
        return {"source": "ff", "ff_id": ff, "value": "", "position": "DEL", "club": "RMA", "name": " X "}
    one = doc("points", "one", [pt("p1", "10"), pt("p1", "10"), pt("p2", "7")])
    two = doc("points", "two", [pt("p1", "11"), pt("p2", "7")])
    fetch = doc("market", "m", [mk("a"), mk("a"), mk("b")])
    stamps = {"s1": [one, fetch], "s2": [one, fetch], "s3": [two, fetch], "s4": [one, fetch]}
    with tempfile.TemporaryDirectory() as tmp:
        paths = parsed.Paths(Path(tmp) / "parsed.jsonl", Path(tmp) / "snapindex.json")
        docs = {(page, ck): rows for entries in stamps.values() for page, ck, rows in entries}
        paths.cache.write_text("".join(
            json.dumps({"k": "%s:%s" % (ck, fingerprint(source(page).parse)), "r": rows}) + "\n"
            for (page, ck), rows in docs.items()), encoding="utf-8")
        paths.snaps.write_text(json.dumps({"snaps": {at: {"size": 0, "pages": {
            page: [ck, at] for page, ck, _rows in entries}} for at, entries in stamps.items()}}), encoding="utf-8")

        def seen(table: str, **kw: Any) -> list[tuple[str, str, float | None, float | None]]:
            return [(r.observed_at, r.ff_id, r.points, r.games)
                    for r in rows_of(parsed.read(paths, [table], **kw), table)]
        assert seen("points") == [("s1", "p1", 10.0, None), ("s1", "p2", 7.0, None),
                                  ("s3", "p1", 11.0, None), ("s4", "p1", 10.0, None)], \
            "repeats dropped, a change kept, None equals None, gone-and-back kept"
        assert seen("points", until="s3") == seen("points")[:3]
        assert seen("points", latest=True, until="s3") == [("s3", "p1", 11.0, None)], \
            "latest: only the newest snapshot's kept rows"
        market = rows_of(parsed.read(paths, ["market"]), "market")
        assert [(r.observed_at, r.ff_id) for r in market] == [(at, ff) for at in stamps for ff in "ab"], \
            "a snapshot keeps every fetch once per key"
        assert {r.name for r in market} == {"X"} and all(r.value is None for r in market), "text stripped, numbers typed"
        assert all(a.ff_id is b.ff_id for a, b in zip(market[::2], market[2::2])), "strings are pooled"
    twice = [Stamped("t", ("s", None, "a", "", "", "")), Stamped("t", ("s", 1.0, "a", "", "", ""))]
    assert [r.body[1] for r in kept("market", twice)] == [None], "the first of two rows with one unique key and stamp is kept"
    keep = keeper("points")
    rows = [("s", 1.0, 1.0, 1.0, "a", "", "", "", "2026"), ("s", 2.0, 1.0, 1.0, "b", "", "", "", "2026")]
    assert [list(keep(at, part("points", rows))) for at in ("t", "u")] == [rows, []], "a Part the table holds adds nothing"
    again = [Stamped(at, b) for at in ("t", "u") for b in rows]
    assert [r.body for r in kept("points", again)] == rows, "keeper over Parts and kept over rows give the same rows"


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
    snaps = [Market(at, source, 1.0, ff, "DEL", "RMA") for at, source, ff in
             [("01", "ff", "a"), ("02", "ff", "b"), ("02", "ff", "c"), ("03", "ff", "d"), ("01", "other", "e"),
              ("02", "other", "f")]]

    def newest(until: str | None) -> list[str]:
        return sorted(r.ff_id for r in rows_of(read(_database({"market": snaps}), ["market"], until, True), "market"))
    assert newest(None) == ["d", "f"] and newest("02") == ["b", "c", "f"] and newest("00") == []
    assert key_of("matches", Matches("t", "s", 1, "m1", "", "", "", "", "")) == ("m1",)
    assert key_of("market", stored[0]) == ()
    again = rows_of(read(_database({"market": stored}), ["market"]), "market")
    first = rows_of(obs, "market")
    assert [tuple(r) for r in again] == [tuple(r) for r in first], "pooling leaves every field value as stored"
    assert all(a.observed_at is b.observed_at and a.ff_id is b.ff_id and a.position is b.position
               for a, b in zip(again, first)), "rows read twice share their strings"
    for name, source in TABLES.items():
        assert set(source.grain) <= set(source.row._fields) and source.row._fields[:2] == ("observed_at", "source")
        assert refolds_of(read(_database({name: []}), [name])) == 0
    _selftest_parsed()
    print("ffcore.observations self-test OK")


if __name__ == "__main__":
    _selftest()
