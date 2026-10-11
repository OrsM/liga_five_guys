from __future__ import annotations

import csv
import io
import os
import sqlite3
import sys
from contextlib import closing, contextmanager
from pathlib import Path
from types import MappingProxyType
from collections.abc import Callable, Hashable, Iterable, Iterator, Mapping, Sequence
from datetime import datetime
from typing import Any, NamedTuple, TypeVar

from ffcore import db as lfg
from ffcore.clock import run_now
from ffcore.db import ROOT
from ffcore.parse import count, money, pct100, ratio, snapshot_stamp

__all__ = ["ROOT", "TIDY", "SEASON", "REPORTS", "TABLES", "Table",
           "current", "history", "typed", "age_hours", "table_path", "tables_in",
           "input_path", "read_csv", "write_csv",
           "csv_string", "mtime_cached", "store_sql", "stored", "sql_tables",
           "drop_sql", "LINEUP_SOURCE"]

TIDY = ROOT / "tidy"
SEASON = ROOT / "season"
REPORTS = Path(os.environ.get("LFG_REPORTS", "reports"))


def table_path(name: str) -> Path:
    return TIDY / f"{name}.csv"


@contextmanager
def tables_in(path: Path) -> Iterator[Path]:
    """The tables, CSV and SQL (lfg.db), kept in path for a while."""
    global TIDY
    real, real_db = TIDY, lfg.DB
    TIDY, lfg.DB = Path(path), Path(path) / "lfg.db"
    try:
        yield TIDY
    finally:
        TIDY, lfg.DB = real, real_db


def input_path(name: str) -> Path:
    p = Path("inputs") / name
    return p if p.exists() else Path(name)


Stamp = tuple[int, int]
_READ_CACHE: dict[str, tuple[Stamp, list[dict[str, str]]]] = {}
T = TypeVar("T")


def _forget(path: str | Path) -> None:
    _READ_CACHE.pop(str(Path(path)), None)


def mtime_cached(path: str | Path, cache: dict[Any, tuple[Stamp, T]], key: Hashable,
                 build: Callable[..., T], *args: Any) -> T | None:
    """build(*args), kept until the file at path changes; None if it is gone."""
    path = Path(path)
    try:
        st = path.stat()
    except OSError:
        return None
    stamp = (st.st_mtime_ns, st.st_size)
    hit = cache.get(key)
    if hit is None or hit[0] != stamp:
        hit = (stamp, build(*args))
        cache[key] = hit
    return hit[1]


def _parse_csv(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open(encoding="utf-8") as fh:
        r = csv.reader(fh)
        fieldnames = next(r, [])
        return [dict(zip(fieldnames, map(sys.intern, row))) for row in r if row]


def read_csv(path: str | Path) -> list[Mapping[str, str]]:
    rows = mtime_cached(path, _READ_CACHE, str(Path(path)), _parse_csv, path)
    return [MappingProxyType(r) for r in (rows or [])]


@contextmanager
def _csv_dictwriter(path: Path, fieldnames: Sequence[str], mode: str,
                    **writer_kw: Any) -> Iterator[csv.DictWriter[str]]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open(mode, newline="", encoding="utf-8") as fh:
        yield csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore",
                             lineterminator="\n", **writer_kw)


def csv_string(rows: Iterable[Mapping[str, Any]], fieldnames: Sequence[str]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fieldnames, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def write_csv(path: str | Path, rows: Sequence[Mapping[str, Any]],
              fieldnames: Sequence[str] | None = None) -> None:
    path = Path(path)
    _forget(path)
    if not rows:
        return
    fieldnames = fieldnames or list(rows[0])
    with _csv_dictwriter(path, fieldnames, "w") as w:
        w.writeheader()
        w.writerows(rows)


class Table(NamedTuple):
    """How a table is kept, and its numeric columns with the parser each
    is read by: current() and history() hand them out parsed, so no
    reader parses a number. Kept in lfg.db, numbers typed once as they are
    stored, read through its current_ and history_ views."""
    snapshot: bool
    key: tuple[str, ...] = ()
    store: str = ""
    numbers: tuple[tuple[str, Callable[[object], Any]], ...] = ()


def _cols(parse: Callable[[object], Any], *cols: str
          ) -> tuple[tuple[str, Callable[[object], Any]], ...]:
    return tuple((c, parse) for c in cols)


TABLES: dict[str, Table] = {
    "market": Table(True, ("ff_id",), "daily",
                    _cols(money, "value", "delta_1d") + _cols(ratio, "delta_pct_1d")),
    "lineups": Table(True, ("source", "team_slug", "player_slug"), "daily",
                     _cols(pct100, "start_pct")),
    "matches": Table(False, ("match_id",), "once", _cols(count, "jornada")),
    "points": Table(False, ("season", "ff_id"), "once", _cols(ratio, "points", "games", "avg")),
    "api_teams": Table(True, numbers=_cols(money, "market_value", "buyout") + _cols(ratio, "points")),
    "api_standings": Table(True, numbers=_cols(count, "position", "previous_position")
                           + _cols(ratio, "team_points", "fixture_points")
                           + _cols(money, "team_value", "team_money")),
    "api_market": Table(True, numbers=_cols(money, "market_value", "sale_price", "bid_money")
                        + _cols(count, "bids")),
    "api_offers": Table(True, numbers=_cols(money, "money")),
    "api_lineup": Table(True, numbers=_cols(count, "week") + _cols(money, "market_value")
                        + _cols(ratio, "points")),
    "api_leagues": Table(True, numbers=_cols(count, "managers") + _cols(money, "money", "team_value")
                         + _cols(ratio, "team_points")),
    "api_players_all": Table(False, ("player_id",), "daily", _cols(money, "market_value")),
    "results_history": Table(False, ("season", "date", "home_name", "away_name"), "once",
                             _cols(ratio, "home_goals", "away_goals", "home_xg", "away_xg",
                                   "home_shots", "away_shots", "home_shots_on_target",
                                   "away_shots_on_target", "home_corners", "away_corners")),
    "starters": Table(False, ("match_id", "team_slug", "player_slug"), "once",
                      _cols(ratio, "minute")),
    "api_activity": Table(False, ("activity_id",), "once",
                          _cols(money, "amount") + _cols(count, "week")),
}




def _columns(db: sqlite3.Connection, name: str) -> list[str]:
    return [r[1] for r in db.execute('PRAGMA table_info("%s")' % name)]


def _views(name: str, cols: list[str], spec: Table) -> str:
    """The table as of cut(), as CSV current() and history() read it:
    history_ every row up to the cut, in the order stored; current_ for a
    snapshot table the last stamp of each source, for a keyed one the last
    row of each key (all of it non-empty), in the order keys appeared."""
    q = ", ".join('"%s"' % c for c in cols)
    hist = ('CREATE VIEW "history_{n}" AS SELECT {q} FROM "{n}"'
            ' WHERE observed_at <= (SELECT cut()) ORDER BY rowid')
    if spec.snapshot:
        # A daily table's day counts once it has closed: its last stamp
        # (anywhere in the table) is not after the cut.
        closed = ("" if spec.store != "daily" else
                  ' JOIN (SELECT substr(observed_at, 1, 10) AS day, max(observed_at) AS ends'
                  '  FROM "{n}" GROUP BY day) d ON d.day = substr(x.observed_at, 1, 10)'
                  '  AND d.ends <= (SELECT cut())')
        cur = ('CREATE VIEW "current_{n}" AS SELECT {tq} FROM "{n}" t JOIN'
               ' (SELECT x.source AS s, max(x.observed_at) AS last FROM "{n}" x' + closed +
               '  WHERE x.observed_at <= (SELECT cut()) GROUP BY x.source) m'
               ' ON t.source IS m.s AND t.observed_at = m.last'
               ' ORDER BY t.observed_at, t.rowid')
    else:
        k = ", ".join('"%s"' % c for c in spec.key)
        cur = ('CREATE VIEW "current_{n}" AS SELECT {q} FROM'
               ' (SELECT *, row_number() OVER (PARTITION BY {k}'
               '   ORDER BY observed_at DESC, rowid DESC) AS _last,'
               '  first_value(observed_at) OVER (PARTITION BY {k}'
               '   ORDER BY observed_at, rowid) AS _first_at,'
               '  first_value(rowid) OVER (PARTITION BY {k}'
               '   ORDER BY observed_at, rowid) AS _first_id'
               '  FROM "{n}" WHERE observed_at <= (SELECT cut()))'
               ' WHERE _last = 1 AND {nonempty} ORDER BY _first_at, _first_id')
    return ";\n".join([
        'DROP VIEW IF EXISTS "history_{n}"', 'DROP VIEW IF EXISTS "current_{n}"',
        hist, cur]).format(n=name, q=q, k=k if not spec.snapshot else "",
                           tq=", ".join('t."%s"' % c for c in cols),
                           nonempty=" AND ".join("coalesce(\"%s\", '') <> ''" % c
                                                 for c in spec.key) or "1") + ";"


def _kind(parse: Callable[[object], Any]) -> str:
    return "INTEGER" if parse is count else "REAL"


def store_sql(name: str, rows: Sequence[Mapping[str, Any]]) -> None:
    """Add rows to a table kept in lfg.db as ingest keeps it as CSV: a
    snapshot table every row, a "once" one only rows whose content differs
    from the key's last, a "daily" one a row per key and day (a later one
    replaces it in place); new columns are added, numbers typed, text
    stripped (so no reader trims)."""
    spec = spec_of(name)
    nums = dict(spec.numbers)
    fresh = [{k: v.strip() if isinstance(v, str) else v for k, v in r.items()} for r in rows]
    with closing(lfg.connect()) as db, db:
        db.execute('CREATE TABLE IF NOT EXISTS "%s" (observed_at TEXT NOT NULL,'
                   " source TEXT NOT NULL DEFAULT '')" % name)
        db.execute('CREATE INDEX IF NOT EXISTS "%s_at" ON "%s" (observed_at)' % (name, name))
        have = _columns(db, name)
        for c in dict.fromkeys([*nums, *(c for r in fresh for c in r)]):
            if c not in have:
                db.execute('ALTER TABLE "%s" ADD COLUMN "%s" %s' % (
                    name, c, _kind(nums[c]) if c in nums else "TEXT NOT NULL DEFAULT ''"))
                have.append(c)
        db.executescript(_views(name, have, spec))
        if spec.store == "once":
            from_sql = db.execute('SELECT * FROM "%s" ORDER BY rowid' % name)
            names = [d[0] for d in from_sql.description]
            latest = {tuple(r.get(c) or "" for c in spec.key): _content(r)
                      for r in (dict(zip(names, row)) for row in from_sql)}
            kept = []
            for r in typed(name, fresh):
                k = tuple(r.get(c) or "" for c in spec.key)
                if latest.get(k) != _content(r):
                    latest[k] = _content(r)
                    kept.append(r)
        elif spec.store == "daily":
            if spec.key:
                db.execute('CREATE INDEX IF NOT EXISTS "{n}_key" ON "{n}" ({k})'.format(
                    n=name, k=", ".join('"%s"' % c for c in spec.key)))
            kept = []
            batch: dict[tuple[Any, ...], int] = {}
            for r in typed(name, fresh):
                key = [r.get(c) or "" for c in spec.key]
                day = (*key, (r.get("observed_at") or "")[:10])
                if all(key) and day in batch:
                    kept[batch[day]] = r
                    continue
                vals = [r.get(c) if c in nums else (r.get(c) or "") for c in have]
                if all(key) and db.execute(
                        'UPDATE "{n}" SET {s} WHERE {w} AND substr(observed_at, 1, 10) = ?'.format(
                            n=name, s=", ".join('"%s" = ?' % c for c in have),
                            w=" AND ".join('"%s" = ?' % c for c in spec.key)),
                        [*vals, *key, (r.get("observed_at") or "")[:10]]).rowcount:
                    continue
                if all(key):
                    batch[day] = len(kept)
                kept.append(r)
        else:
            kept = typed(name, fresh)
        db.executemany(
            'INSERT INTO "%s" (%s) VALUES (%s)' % (
                name, ", ".join('"%s"' % c for c in have), ", ".join("?" * len(have))),
            [[r.get(c) if c in nums else (r.get(c) or "") for c in have] for r in kept])


def _content(r: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    """A row as it reads, but for when: what a keyed table keeps only when
    it changes."""
    return tuple(sorted((k, str(v)) for k, v in r.items()
                        if k != "observed_at" and v not in ("", None)))


def stored(name: str) -> int:
    """How many rows a table keeps."""
    try:
        got: int = lfg.rows('SELECT count(*) FROM "%s"' % name)[0][0]
        return got
    except sqlite3.OperationalError:
        return 0


def sql_tables() -> set[str]:
    """The tables lfg.db holds."""
    return {n for (n,) in lfg.rows("SELECT name FROM sqlite_master WHERE type = 'table'")}


def drop_sql(name: str) -> None:
    with closing(lfg.connect()) as db, db:
        db.executescript('DROP VIEW IF EXISTS "current_{n}"; DROP VIEW IF EXISTS "history_{n}";'
                         ' DROP TABLE IF EXISTS "{n}";'.format(n=name))


def _read_sql(view: str) -> list[dict[str, Any]]:
    try:
        cols, got = lfg.query('SELECT * FROM "%s"' % view)
        return [dict(zip(cols, r)) for r in got]
    except sqlite3.OperationalError:
        return []


def spec_of(name: str) -> Table:
    """How a table is kept: TABLES says, else a plain snapshot table."""
    return TABLES.get(name, Table(True))


def typed(name: str, rows: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Rows of a table as it is written (text) with its numeric columns
    parsed (Table.numbers): what current() and history() hand out."""
    numbers = TABLES[name].numbers if name in TABLES else ()
    return [MappingProxyType({**r, **{c: parse(r.get(c)) for c, parse in numbers if c in r}})
            for r in rows]


def history(name: str, source: str = "") -> list[Mapping[str, Any]]:
    """Every row of a table up to cut(), in the order stored."""
    return [MappingProxyType(r) for r in _read_sql("history_" + name)
            if not source or r.get("source") == source]


def current(name: str, source: str = "") -> list[dict[str, Any]]:
    """A table as of cut(): a snapshot table's last stamp per source (a
    daily one's last closed day), a keyed one's last row per key."""
    return [r for r in _read_sql("current_" + name)
            if not source or r.get("source") == source]


def age_hours(name: str, now: datetime | None = None) -> float | None:
    stamps = [r.get("observed_at", "") for r in current(name)]
    when = snapshot_stamp(max(stamps)) if stamps else None
    return None if when is None else ((now or run_now()) - when).total_seconds() / 3600


LINEUP_SOURCE = "futbolfantasy"


def _selftest_cache() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "t.csv"
        write_csv(p, [{"a": "1", "b": "x"}, {"a": "2", "b": "y"}])
        first = read_csv(p)
        assert [r["a"] for r in first] == ["1", "2"], first

        try:
            first[0]["a"] = "999"  # type: ignore[index]  # the point: it must refuse
            raise AssertionError("read_csv rows must be read-only")
        except TypeError:
            pass
        first.append({"a": "3", "b": "z"})
        assert [r["a"] for r in read_csv(p)] == ["1", "2"], read_csv(p)

        write_csv(p, [{"a": "7", "b": "q"}])
        assert [r["a"] for r in read_csv(p)] == ["7"], read_csv(p)

        assert read_csv(Path(tmp) / "nope.csv") == []


def _selftest_tables() -> None:
    import tempfile

    from ffcore.clock import set_now

    a, b, later = "2026-08-01T0900Z", "2026-08-02T0900Z", "2026-08-03T0900Z"
    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        try:
            store_sql("lineups", [
                {"observed_at": a, "source": "futbolfantasy", "player_name": "Ane"},
                {"observed_at": b, "source": "analitica", "player_name": "Ane"},
                {"observed_at": b, "source": "futbolfantasy", "player_name": "Bo"},
                {"observed_at": a, "source": "analitica", "player_name": "Cai"},
                {"observed_at": later, "source": "futbolfantasy",
                 "player_name": "Dan"}])
            store_sql("api_activity", [
                {"observed_at": a, "activity_id": "1", "value": "0"},
                {"observed_at": b, "activity_id": "1", "value": "1"},
                {"observed_at": a, "activity_id": "2", "value": "5"}])
            set_now(snapshot_stamp("2026-08-02T1200Z"))
            for name, source, every, now in [
                    ("lineups", "", 4, ["Ane", "Bo"]),
                    ("lineups", "analitica", 2, ["Ane"]),
                    ("lineups", "futbolfantasy", 2, ["Bo"]),
                    ("lineups", "nobody", 0, [])]:
                assert len(history(name, source)) == every, source
                assert sorted(r["player_name"] for r in current(name, source)) \
                    == now, source
            stats = {(r["activity_id"], r["value"]) for r in current("api_activity")}
            assert stats == {("1", "1"), ("2", "5")}, stats
            assert current("market") == [] and history("market") == []
            store_sql("matches", [
                {"observed_at": a, "match_id": "m1", "jornada": "8", "home": "x", "score": "2-0"},
                {"observed_at": a, "match_id": "m2", "jornada": "", "home": "y", "score": ""}])
            store_sql("api_market", [
                {"observed_at": b, "source": "laliga", "player_id": "7",
                 "sale_price": "5000000", "bids": "2", "bid_money": ""}])
            ms = {r["match_id"]: r for r in current("matches")}
            assert (ms["m1"]["jornada"], ms["m2"]["jornada"]) == (8, None), \
                "numbers are parsed once, where the table is read"
            assert ms["m1"]["home"] == "x" and ms["m1"]["score"] == "2-0", "text stays text"
            assert [r["jornada"] for r in history("matches")] == [8, None]
            mk = current("api_market")[0]
            assert (mk["sale_price"], mk["bids"], mk["bid_money"], mk["player_id"]) \
                == (5e6, 2, None, "7"), mk
            assert age_hours("market") is None
            age = age_hours("lineups")
            assert age is not None and abs(age - 3.0) < 1e-9
            set_now(snapshot_stamp("2026-08-01T1000Z"))
            assert stats != {(r["activity_id"], r["value"])
                             for r in current("api_activity")}
            set_now(snapshot_stamp("2026-08-04T0000Z"))
            assert [r["player_name"] for r in current("lineups", "futbolfantasy")] \
                == ["Dan"]
        finally:
            set_now(None)


def _selftest() -> None:
    _selftest_cache()
    _selftest_tables()

    print("ffcore.tidy self-test OK")


if __name__ == "__main__":
    _selftest()
