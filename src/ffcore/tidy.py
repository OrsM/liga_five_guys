from __future__ import annotations

import csv
import io
import os
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from types import MappingProxyType
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from typing import Any

from ffcore import db as lfg
from ffcore.db import ROOT, source_table
from ffcore.parse import count, money, pct100, ratio, snapshot_stamp

__all__ = ["ROOT", "TIDY", "REPORTS",
           "current", "history", "typed", "age_hours", "tables_in",
           "input_path", "csv_string", "store_sql", "stored",
           "clear_sql", "NUMBERS", "LINEUP_SOURCE"]

TIDY = ROOT / "tidy"
REPORTS = Path(os.environ.get("LFG_REPORTS", "reports"))


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


def csv_string(rows: Iterable[Mapping[str, Any]], fieldnames: Sequence[str]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fieldnames, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def _cols(parse: Callable[[object], Any], *cols: str
          ) -> tuple[tuple[str, Callable[[object], Any]], ...]:
    return tuple((c, parse) for c in cols)


# Each table's numeric columns and the parser each is read by: stored typed,
# so no reader parses a number. The tables themselves are tables.sql's.
NUMBERS: dict[str, tuple[tuple[str, Callable[[object], Any]], ...]] = {
    "market": _cols(money, "value"),
    "lineups": _cols(pct100, "start_pct"),
    "matches": _cols(count, "jornada"),
    "points": _cols(ratio, "points", "games", "avg"),
    "api_teams": _cols(money, "market_value", "buyout") + _cols(ratio, "points"),
    "api_standings": _cols(count, "position")
                           + _cols(ratio, "team_points", "fixture_points")
                           + _cols(money, "team_value", "team_money"),
    "api_market": _cols(money, "market_value", "sale_price", "bid_money")
                        + _cols(count, "bids"),
    "api_offers": _cols(money, "money"),
    "api_lineup": _cols(money, "market_value")
                        + _cols(ratio, "points"),
    "api_leagues": _cols(count, "managers") + _cols(money, "money", "team_value")
                         + _cols(ratio, "team_points"),
    "api_players_all": _cols(money, "market_value"),
    "results_history": _cols(ratio, "home_goals", "away_goals"),
    "starters": _cols(ratio, "minute"),
    "api_activity": _cols(money, "amount"),
}


def _columns(db: sqlite3.Connection, name: str) -> list[str]:
    return [r[1] for r in db.execute('PRAGMA table_info("%s")' % source_table(name))]


def store_sql(name: str, rows: Sequence[Mapping[str, Any]]) -> None:
    """Add rows to a table tables.sql declares: numbers typed, text stripped
    (so no reader trims). A keyed table's trigger skips a row its key's last
    already says, and a row its unique key already holds is skipped; a column
    the table does not declare is reported, not kept."""
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
            "INSERT OR IGNORE INTO %s (%s) VALUES (%s)" % (source_table(name), ", ".join(cols), ", ".join("?" * len(cols))),
            [[r.get(c) if c in nums else (r.get(c) or "") for c in cols] for r in fresh])


def stored(name: str) -> int:
    """How many rows a table keeps."""
    try:
        got: int = lfg.rows('SELECT count(*) FROM "%s"' % source_table(name))[0][0]
        return got
    except sqlite3.OperationalError:
        return 0


def clear_sql(name: str) -> None:
    """Empty a table, for a parse that starts over."""
    with closing(lfg.connect()) as db, db:
        db.execute("DELETE FROM %s" % source_table(name))


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
    """Every row of a table up to t, in the order stored."""
    return [MappingProxyType(r) for r in _read_sql("history_" + name)
            if not source or r.get("source") == source]


def current(name: str, source: str = "") -> list[dict[str, Any]]:
    """A table as of t: a snapshot table's last stamp per source (a
    daily one's last closed day), a keyed one's last row per key."""
    return [r for r in _read_sql("current_" + name)
            if not source or r.get("source") == source]


def age_hours(name: str) -> float | None:
    """How old a table's last row is at t, in hours."""
    stamps = [r.get("observed_at", "") for r in current(name)]
    when, t = snapshot_stamp(max(stamps)) if stamps else None, snapshot_stamp(lfg.cut())
    return None if when is None or t is None else (t - when).total_seconds() / 3600


LINEUP_SOURCE = "futbolfantasy"


def _selftest_tables() -> None:
    import tempfile

    from ffcore.db import set_now
    from ffcore.db import rows

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
            assert current("market") == []
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
            assert rows("SELECT activity_id, kind FROM src_api_activity ORDER BY rowid") == [
                ("1", "0"), ("1", "1"), ("2", "5"), ("9", "2"), ("9", "3"), ("9", "2")], \
                "a keyed row is kept once, and again only when it changes (back included)"
            store_sql("market", [{"observed_at": b, "ff_id": "x", "value": "5"}] * 2
                      + [{"observed_at": b, "ff_id": "y", "value": "5"}])
            store_sql("market", [{"observed_at": b, "ff_id": "x", "value": "6"}])
            assert rows("SELECT ff_id, value FROM src_market ORDER BY rowid") == [("x", 5.0), ("y", 5.0)], \
                "a snapshot holds a key once: the same (source, observed_at, ff_id) again is skipped, a stored fetch is not stored twice"
        finally:
            set_now(None)


def _selftest() -> None:
    _selftest_tables()

    print("ffcore.tidy self-test OK")


if __name__ == "__main__":
    _selftest()
