"""Query results kept for a past cut: the model is a function of the data up
to the cut, the schema and the constants, so a replay that has been built
before reads its views' answers back instead of deriving them again (a
board's universe is a dozen views of 0.2-0.6 s each). Only a cut that was
set (a replay, not the live run), only inside scope(). The key is the cut,
the definitions each answer reads (deps), the constants the views read (not the variant's name:
two variants alike to the views share their answers) and what each raw
table held up to the cut, so a changed schema, constant or fetch is a
miss, never a stale hit.
The store is a file beside lfg.db that can be deleted at any time."""
from __future__ import annotations

import bisect
import hashlib
import os
import pickle
import re
import sqlite3
import time
from collections.abc import Iterator, Sequence
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any

from ffcore import clock

# Entries; the oldest made go beyond it. A daily backtest keeps ~580 a
# run (~20 kB each) at ~140 runs: below that it evicts its own answers
# before the next backtest can read them (2026-10-30).
KEEP = 150000
_PUTS = [0]                     # entries put since the store was last counted
_BASE: list[str] = []           # the key of the scope in force
_CON: list[sqlite3.Connection] = []     # its store, open for the scope
_RAW = re.compile(r"CREATE TABLE IF NOT EXISTS (\w+)")


def _store(db: Path) -> Path:
    """Beside the database, or LFG_KEPT (a path; "off" keeps nothing: a check
    that the views read nothing after the cut must not read its own answers)."""
    return Path(os.environ["LFG_KEPT"]) if os.environ.get("LFG_KEPT") else db.with_name(db.name + ".kept")


def _open(db: Path) -> sqlite3.Connection:
    con = sqlite3.connect(_store(db), timeout=120)     # a backtest's builds write it from several processes
    # A cache, deletable at any time: no wait on the disk at each of the
    # ~80,000 answers a cold backtest keeps, and readers beside the writer.
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA synchronous = OFF")
    con.execute("CREATE TABLE IF NOT EXISTS kept (key TEXT PRIMARY KEY, made REAL, val BLOB) WITHOUT ROWID")
    return con


def _fingerprint(lfg: sqlite3.Connection, tables: Sequence[str], cut: str, db: Path) -> str:
    """What each raw table held up to the cut (its rows' count and last
    rowid), the registry, and last season's points (season_points: no
    observed_at, refilled whole)."""
    parts = []
    for t in tables:
        stamps, counts, last = _snapshots(lfg, db, t)
        i = bisect.bisect_right(stamps, cut)
        parts.append("%s:%d/%d" % (t, counts[i - 1] if i else 0, last[i - 1] if i else 0))
    parts.append(repr(lfg.execute("SELECT * FROM crosswalk ORDER BY 1").fetchall()))
    parts.append(repr(lfg.execute("SELECT * FROM league_config").fetchall()))
    parts.append(repr(lfg.execute("SELECT * FROM season_points ORDER BY 1, 2").fetchall()))
    return hashlib.sha1("|".join(parts).encode()).hexdigest()


_SNAPS: dict[tuple[str, str], tuple[int, list[str], list[int], list[int]]] = {}


def _snapshots(lfg: sqlite3.Connection, db: Path, table: str) -> tuple[list[str], list[int], list[int]]:
    """A raw table's observed_at stamps in order, with the count of rows and
    the last rowid up to each: read once a process while no row is added
    (rowids only grow), so each cut's fingerprint is a lookup (2026-10-31:
    counting at each cut was most of the 0.16 s a kept universe took to find)."""
    top = lfg.execute('SELECT coalesce(max(rowid), 0) FROM "%s"' % table).fetchone()[0]
    key = (str(db), table)
    if key not in _SNAPS or _SNAPS[key][0] != top:
        stamps, counts, last, n, high = [], [], [], 0, 0
        for at, k, m in lfg.execute('SELECT observed_at, count(*), max(rowid) FROM "%s" WHERE observed_at IS NOT NULL'
                                    ' GROUP BY observed_at ORDER BY observed_at' % table):
            n, high = n + k, max(high, m)
            stamps.append(at)
            counts.append(n)
            last.append(high)
        _SNAPS[key] = (top, stamps, counts, last)
    return _SNAPS[key][1:]


@contextmanager
def scope(db: Path, raw_sql: Path, constants: str) -> Iterator[None]:
    """Keep and read results while a past cut is built. constants is the
    model's constants as text, which the views read from Python."""
    if not clock.pinned() or _BASE or os.environ.get("LFG_KEPT") == "off":
        yield
        return
    with closing(sqlite3.connect(db)) as lfg:
        cut = clock.cut()
        tables = [t for t in _RAW.findall(raw_sql.read_text(encoding="utf-8"))
                  if any(c[1] == "observed_at" for c in lfg.execute('PRAGMA table_info("%s")' % t))]
        _BASE[:] = ["|".join((cut, hashlib.sha1(constants.encode()).hexdigest(), _fingerprint(lfg, tables, cut, db)))]
        _DEFS.clear()
        _DEFS.update(dict(lfg.execute("SELECT name, sql FROM sqlite_master WHERE type IN ('view', 'table')"
                                      " AND sql IS NOT NULL")))
        _DEPS.clear()
    _CON[:] = [_open(db)]
    try:
        yield
    finally:
        _BASE.clear()
        _CON.pop().close()


_DEFS: dict[str, str] = {}      # each view's and table's definition, as lfg.db holds them
_DEPS: dict[str, str] = {}      # each statement's: the definitions it reads, hashed
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def deps(sql: str) -> str:
    """The definitions sql reads, through every view it reads, hashed: what
    its answer depends on of the schema. A view changed elsewhere leaves it
    a hit (2026-10-30: keyed on the whole schema's version, any view's edit
    made every universe of a backtest cold again)."""
    if sql not in _DEPS:
        seen: set[str] = set()
        todo = [n for n in set(_NAME.findall(sql)) if n in _DEFS]
        while todo:
            n = todo.pop()
            if n not in seen:
                seen.add(n)
                todo.extend(m for m in set(_NAME.findall(_DEFS[n])) if m in _DEFS and m not in seen)
        _DEPS[sql] = hashlib.sha1("\0".join(_DEFS[n] for n in sorted(seen)).encode()).hexdigest()
    return _DEPS[sql]


def _key(name: str, params: object = ()) -> str:
    return hashlib.sha1((_BASE[0] + "\0" + deps(name) + "\0" + name + "\0" + repr(params)).encode()).hexdigest()


def get(db: Path, sql: str, params: object) -> Any:
    if not _BASE:
        return None
    got = _CON[0].execute("SELECT val FROM kept WHERE key = ?", (_key(sql, params),)).fetchone()
    return None if got is None else pickle.loads(got[0])


def put(db: Path, sql: str, params: object, value: Any) -> None:
    if not _BASE:
        return
    with _CON[0] as con:
        con.execute("INSERT OR REPLACE INTO kept VALUES (?, ?, ?)",
                    (_key(sql, params), time.time(), pickle.dumps(value)))
        _PUTS[0] += 1
        if _PUTS[0] % 500 == 0 and con.execute("SELECT count(*) FROM kept").fetchone()[0] > KEEP:
            con.execute("DELETE FROM kept WHERE key IN (SELECT key FROM kept ORDER BY made LIMIT ?)", (KEEP // 6,))


def written(db: Path, name: str, sql: str = "") -> bool:
    """Is what the named write left in lfg.db this scope's, made by sql as
    it reads now (deps)? One write a name and cut is held, the last, since
    a later variant overwrites it."""
    return bool(_BASE) and _latest(db, name) == _BASE[0] + deps(sql)


def _latest(db: Path, name: str) -> str | None:
    got = _CON[0].execute("SELECT val FROM kept WHERE key = ?", ("write|%s|%s" % (name, clock.cut()),)).fetchone()
    return pickle.loads(got[0]) if got else None


def mark(db: Path, name: str, sql: str = "") -> None:
    if _BASE:
        with _CON[0] as con:
            con.execute("INSERT OR REPLACE INTO kept VALUES (?, ?, ?)",
                        ("write|%s|%s" % (name, clock.cut()), time.time(), pickle.dumps(_BASE[0] + deps(sql))))


def _selftest() -> None:
    import tempfile
    from datetime import datetime, timezone
    with tempfile.TemporaryDirectory() as tmp:
        db, raw = Path(tmp) / "x.db", Path(tmp) / "raw.sql"
        raw.write_text("CREATE TABLE IF NOT EXISTS t (observed_at TEXT, v);\n"
                       "CREATE TABLE IF NOT EXISTS u (v);")
        with closing(sqlite3.connect(db)) as con, con:
            con.executescript("CREATE TABLE t (observed_at TEXT, v); CREATE TABLE u (v);"
                              "CREATE TABLE crosswalk (a); CREATE TABLE league_config (a);"
                              "CREATE TABLE season_points (season, ff_id, points, games);")
            con.execute("INSERT INTO t VALUES ('2026-08-01T0000Z', 1), ('2026-09-01T0000Z', 2)")
        with scope(db, raw, "A=1"):
            assert get(db, "q", ()) is None, "outside a pinned cut nothing is kept"
        clock.set_now(datetime(2026, 8, 15, tzinfo=timezone.utc))
        try:
            with scope(db, raw, "A=1"):
                assert get(db, "q", ()) is None
                put(db, "q", (), [(1.5, None)])
                assert get(db, "q", ()) == [(1.5, None)]
                assert not written(db, "fit")
                mark(db, "fit")
                assert written(db, "fit")
            assert get(db, "q", ()) is None, "outside the scope nothing is read"
            with scope(db, raw, "A=2"):
                assert get(db, "q", ()) is None, "a constant changed: a miss"
                assert not written(db, "fit")
            with closing(sqlite3.connect(db)) as con, con:
                con.execute("INSERT INTO t VALUES ('2026-09-02T0000Z', 3)")
            with scope(db, raw, "A=1"):
                assert get(db, "q", ()) == [(1.5, None)], "a later fetch is not in this cut"
            with closing(sqlite3.connect(db)) as con, con:
                con.execute("INSERT INTO t VALUES ('2026-08-10T0000Z', 3)")
            with scope(db, raw, "A=1"):
                assert get(db, "q", ()) is None, "a row at or before the cut changed: a miss"
                put(db, "q", (), [(2.5, None)])
            with closing(sqlite3.connect(db)) as con, con:
                con.execute("INSERT INTO season_points VALUES ('2025-26', '1', 9, 9)")
            with scope(db, raw, "A=1"):
                assert get(db, "q", ()) is None, "last season's points changed: a miss"
            with closing(sqlite3.connect(db)) as con, con:
                con.executescript("CREATE VIEW inner_v AS SELECT v FROM t; CREATE VIEW outer_v AS SELECT v FROM inner_v;"
                                  "CREATE VIEW other_v AS SELECT 1;")
            with scope(db, raw, "A=1"):
                put(db, "SELECT * FROM outer_v", (), [(1,)])
            with closing(sqlite3.connect(db)) as con, con:
                con.executescript("DROP VIEW other_v; CREATE VIEW other_v AS SELECT 2;")
            with scope(db, raw, "A=1"):
                assert get(db, "SELECT * FROM outer_v", ()) == [(1,)], "a view it does not read changed: still a hit"
            with closing(sqlite3.connect(db)) as con, con:
                con.executescript("DROP VIEW inner_v; CREATE VIEW inner_v AS SELECT v + 1 AS v FROM t;")
            with scope(db, raw, "A=1"):
                assert get(db, "SELECT * FROM outer_v", ()) is None, "a view it reads through another changed: a miss"
        finally:
            clock.set_now(None)


if __name__ == "__main__":
    _selftest()
    print("ffcore.kept self-test OK")
