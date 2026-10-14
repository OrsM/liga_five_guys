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
from typing import Any, TypeVar

from ffcore import db as lfg
from ffcore.clock import run_now
from ffcore.db import ROOT
from ffcore.parse import count, money, pct100, ratio, snapshot_stamp

__all__ = ["ROOT", "TIDY", "SEASON", "REPORTS",
           "current", "history", "typed", "age_hours", "table_path", "tables_in",
           "input_path", "read_csv", "write_csv",
           "csv_string", "mtime_cached", "store_sql", "stored",
           "clear_sql", "NUMBERS", "LINEUP_SOURCE"]

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


def _cols(parse: Callable[[object], Any], *cols: str
          ) -> tuple[tuple[str, Callable[[object], Any]], ...]:
    return tuple((c, parse) for c in cols)


# Each table's numeric columns and the parser each is read by: stored typed,
# so no reader parses a number. The tables themselves are tables.sql's.
NUMBERS: dict[str, tuple[tuple[str, Callable[[object], Any]], ...]] = {
    "market": _cols(money, "value", "delta_1d") + _cols(ratio, "delta_pct_1d"),
    "lineups": _cols(pct100, "start_pct"),
    "matches": _cols(count, "jornada"),
    "points": _cols(ratio, "points", "games", "avg"),
    "api_teams": _cols(money, "market_value", "buyout") + _cols(ratio, "points"),
    "api_standings": _cols(count, "position", "previous_position")
                           + _cols(ratio, "team_points", "fixture_points")
                           + _cols(money, "team_value", "team_money"),
    "api_market": _cols(money, "market_value", "sale_price", "bid_money")
                        + _cols(count, "bids"),
    "api_offers": _cols(money, "money"),
    "api_lineup": _cols(count, "week") + _cols(money, "market_value")
                        + _cols(ratio, "points"),
    "api_leagues": _cols(count, "managers") + _cols(money, "money", "team_value")
                         + _cols(ratio, "team_points"),
    "api_players_all": _cols(money, "market_value"),
    "results_history": _cols(ratio, "home_goals", "away_goals", "home_xg", "away_xg",
                                   "home_shots", "away_shots", "home_shots_on_target",
                                   "away_shots_on_target", "home_corners", "away_corners"),
    "starters": _cols(ratio, "minute"),
    "api_activity": _cols(money, "amount") + _cols(count, "week"),
}


def _columns(db: sqlite3.Connection, name: str) -> list[str]:
    return [r[1] for r in db.execute('PRAGMA table_info("%s")' % name)]


def store_sql(name: str, rows: Sequence[Mapping[str, Any]]) -> None:
    """Add rows to a table tables.sql declares: numbers typed, text stripped
    (so no reader trims). A keyed table's trigger skips a row its key's last
    already says; a column the table does not declare is reported, not kept."""
    nums = dict(NUMBERS.get(name, ()))
    fresh = typed(name, [{k: v.strip() if isinstance(v, str) else v for k, v in r.items()}
                         for r in rows])
    with closing(lfg.connect()) as db, db:
        cols = _columns(db, name)
        if not cols:
            raise ValueError("%s is not a table in tables.sql" % name)
        extra = sorted({c for r in fresh for c in r} - set(cols))
        if extra:
            print("  warn: %s: columns not in tables.sql, not kept: %s" % (name, ", ".join(extra)))
        db.executemany(
            "INSERT INTO %s (%s) VALUES (%s)" % (name, ", ".join(cols), ", ".join("?" * len(cols))),
            [[r.get(c) if c in nums else (r.get(c) or "") for c in cols] for r in fresh])


def stored(name: str) -> int:
    """How many rows a table keeps."""
    try:
        got: int = lfg.rows('SELECT count(*) FROM "%s"' % name)[0][0]
        return got
    except sqlite3.OperationalError:
        return 0


def clear_sql(name: str) -> None:
    """Empty a table, for a parse that starts over."""
    with closing(lfg.connect()) as db, db:
        db.execute("DELETE FROM %s" % name)


def _read_sql(view: str) -> list[dict[str, Any]]:
    """A view's rows; none if its table has never been stored."""
    try:
        cols, got = lfg.query('SELECT * FROM "%s"' % view)
    except sqlite3.OperationalError as e:
        if "no such table" in str(e):
            return []
        raise
    return [dict(zip(cols, r)) for r in got]


def typed(name: str, rows: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Rows of a table as it is written (text) with its numeric columns
    parsed (NUMBERS): what is stored."""
    numbers = NUMBERS.get(name, ())
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
                {"observed_at": a, "activity_id": "1", "kind": "0"},
                {"observed_at": b, "activity_id": "1", "kind": "1"},
                {"observed_at": a, "activity_id": "2", "kind": "5"}])
            set_now(snapshot_stamp("2026-08-02T1200Z"))
            for name, source, every, now in [
                    ("lineups", "", 4, ["Ane", "Bo"]),
                    ("lineups", "analitica", 2, ["Ane"]),
                    ("lineups", "futbolfantasy", 2, ["Bo"]),
                    ("lineups", "nobody", 0, [])]:
                assert len(history(name, source)) == every, source
                assert sorted(r["player_name"] for r in current(name, source)) \
                    == now, source
            stats = {(r["activity_id"], r["kind"]) for r in current("api_activity")}
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
            assert stats != {(r["activity_id"], r["kind"])
                             for r in current("api_activity")}
            set_now(snapshot_stamp("2026-08-04T0000Z"))
            assert [r["player_name"] for r in current("lineups", "futbolfantasy")] \
                == ["Dan"]
            store_sql("api_activity", [
                {"observed_at": later, "activity_id": "1", "kind": "1"},
                {"observed_at": later, "activity_id": "9", "kind": "2"}])
            store_sql("api_activity", [
                {"observed_at": "2026-08-03T1000Z", "activity_id": "9", "kind": "3"},
                {"observed_at": "2026-08-03T1100Z", "activity_id": "9", "kind": "2"}])
            assert [(r["activity_id"], r["kind"]) for r in history("api_activity")] == [
                ("1", "0"), ("1", "1"), ("2", "5"), ("9", "2"), ("9", "3"), ("9", "2")], \
                "a keyed row is kept once, and again only when it changes (back included)"
        finally:
            set_now(None)


def _selftest() -> None:
    _selftest_cache()
    _selftest_tables()

    print("ffcore.tidy self-test OK")


if __name__ == "__main__":
    _selftest()
