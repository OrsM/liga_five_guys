"""Query results kept for a past cut: the model is a function of the data up
to the cut, the schema and the constants, so a replay that has been built
before reads its views' answers back instead of deriving them again (a
board's universe is a dozen views of 0.2-0.6 s each). Only a cut that was
set (a replay, not the live run), only inside scope(). The key is the cut,
the schema version, the constants and what each raw table held up to the
cut, so a changed schema, constant or fetch is a miss, never a stale hit.
The store is a file beside lfg.db that can be deleted at any time."""
from __future__ import annotations

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

KEEP = 6000                     # entries; the oldest made go beyond it
_BASE: list[str] = []           # the key of the scope in force
_CON: list[sqlite3.Connection] = []     # its store, open for the scope
_RAW = re.compile(r"CREATE TABLE IF NOT EXISTS (\w+)")


def _store(db: Path) -> Path:
    """Beside the database, or LFG_KEPT (a path; "off" keeps nothing: a check
    that the views read nothing after the cut must not read its own answers)."""
    return Path(os.environ["LFG_KEPT"]) if os.environ.get("LFG_KEPT") else db.with_name(db.name + ".kept")


def _open(db: Path) -> sqlite3.Connection:
    con = sqlite3.connect(_store(db))
    con.execute("CREATE TABLE IF NOT EXISTS kept (key TEXT PRIMARY KEY, made REAL, val BLOB) WITHOUT ROWID")
    return con


def _fingerprint(lfg: sqlite3.Connection, tables: Sequence[str], cut: str) -> str:
    """What each raw table held up to the cut, and the registry."""
    parts = []
    for t in tables:
        parts.append("%s:%s" % (t, lfg.execute(
            'SELECT count(*) || "/" || coalesce(max(rowid), 0) FROM "%s" WHERE observed_at <= ?' % t, (cut,)).fetchone()[0]))
    parts.append(repr(lfg.execute("SELECT * FROM crosswalk ORDER BY 1").fetchall()))
    parts.append(repr(lfg.execute("SELECT * FROM league_config").fetchall()))
    return hashlib.sha1("|".join(parts).encode()).hexdigest()


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
        _BASE[:] = ["|".join((cut, clock.variant(), str(lfg.execute("PRAGMA user_version").fetchone()[0]),
                              hashlib.sha1(constants.encode()).hexdigest(), _fingerprint(lfg, tables, cut)))]
    _CON[:] = [_open(db)]
    try:
        yield
    finally:
        _BASE.clear()
        _CON.pop().close()


def _key(name: str, params: object = ()) -> str:
    return hashlib.sha1((_BASE[0] + "\0" + name + "\0" + repr(params)).encode()).hexdigest()


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
        if con.execute("SELECT count(*) FROM kept").fetchone()[0] > KEEP:
            con.execute("DELETE FROM kept WHERE key IN (SELECT key FROM kept ORDER BY made LIMIT ?)", (KEEP // 6,))


def written(db: Path, name: str) -> bool:
    """Is what the named write left in lfg.db this scope's? One write a
    name and cut is held, the last, since a later variant overwrites it."""
    return bool(_BASE) and _latest(db, name) == _BASE[0]


def _latest(db: Path, name: str) -> str | None:
    got = _CON[0].execute("SELECT val FROM kept WHERE key = ?", ("write|%s|%s" % (name, clock.cut()),)).fetchone()
    return pickle.loads(got[0]) if got else None


def mark(db: Path, name: str) -> None:
    if _BASE:
        with _CON[0] as con:
            con.execute("INSERT OR REPLACE INTO kept VALUES (?, ?, ?)",
                        ("write|%s|%s" % (name, clock.cut()), time.time(), pickle.dumps(_BASE[0])))


def _selftest() -> None:
    import tempfile
    from datetime import datetime, timezone
    with tempfile.TemporaryDirectory() as tmp:
        db, raw = Path(tmp) / "x.db", Path(tmp) / "raw.sql"
        raw.write_text("CREATE TABLE IF NOT EXISTS t (observed_at TEXT, v);\n"
                       "CREATE TABLE IF NOT EXISTS u (v);")
        with closing(sqlite3.connect(db)) as con, con:
            con.executescript("CREATE TABLE t (observed_at TEXT, v); CREATE TABLE u (v);"
                              "CREATE TABLE crosswalk (a); CREATE TABLE league_config (a);")
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
        finally:
            clock.set_now(None)


if __name__ == "__main__":
    _selftest()
    print("ffcore.kept self-test OK")
