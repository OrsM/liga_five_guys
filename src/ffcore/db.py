"""The run's SQLite file: what each run measured and concluded, for
Datasette and ask.py. Disposable: every table but runs is rebuilt from
data/raw, git or a replay (docs/plan-datasette.md)."""
from __future__ import annotations

import fcntl
import json
import os
import re
import sqlite3
import time
import zlib
from datetime import datetime, timezone
from collections.abc import Callable, Mapping, Sequence
from contextlib import closing
from pathlib import Path
from typing import Any

from ffcore.clock import cut, variant
from ffcore.names import Name
from ffcore.rules import position
from ffcore.startprob import platt, prognosis_bucket

__all__ = ["DB", "IDENTITY", "META", "ROOT", "TABLES_SQL", "named", "params", "queries", "RUN_ENV", "SCHEMA", "connect", "functions", "query",
           "record", "rows", "this_run"]

ROOT = Path(os.environ.get("FF_ROOT", "./data"))
DB = ROOT / "lfg.db"  # tidy.tables_in moves it with the tables
SCHEMA = Path(__file__).with_name("schema.sql")
IDENTITY = Path(__file__).with_name("identity.sql")
TABLES_SQL = Path(__file__).with_name("tables.sql")
RUN_ENV = "LFG_RUN"
_APPLIED: set[tuple[Path, Path]] = set()  # (file, schema) this process has applied


Z95 = 1.96   # standard errors either side of an estimate: the 95% interval


def lo95(gain: float | None, se: float | None) -> float | None:
    return None if gain is None or se is None else gain - Z95 * se


def hi95(gain: float | None, se: float | None) -> float | None:
    return None if gain is None or se is None else gain + Z95 * se


def claim(gain: float | None, se: float | None) -> str:
    """What an estimate with its standard error establishes: positive or
    negative when the 95% interval excludes zero, else not established; too
    few when there is no spread to judge it by."""
    lo, hi = lo95(gain, se), hi95(gain, se)
    if lo is None or hi is None:
        return "too few"
    return "positive" if lo > 0 else "negative" if hi < 0 else "not established"


def functions(db: sqlite3.Connection, now: Callable[[], str] = cut) -> None:
    """The functions the views call: cut() (now, as a stamp: the model's
    clock unless told otherwise), variant() (the constants in force), name_key() and name_shown() (Name's),
    platt() and prognosis_bucket() (startprob's), position() (rules'),
    lo95(), hi95() and claim() (an estimate and its standard error)."""
    db.create_function("cut", 0, now)
    db.create_function("variant", 0, variant)
    db.create_function("name_key", 1, lambda s: Name(s).key, deterministic=True)
    db.create_function("name_shown", 1, lambda s: Name(s).shown, deterministic=True)
    for fn in (lo95, hi95, claim):
        db.create_function(fn.__name__, 2, fn, deterministic=True)
    db.create_function("platt", 3, platt, deterministic=True)
    db.create_function("position", 1, position, deterministic=True)
    db.create_function("prognosis_bucket", 6, prognosis_bucket, deterministic=True)


def connect(path: Path | None = None, schema: Path = SCHEMA) -> sqlite3.Connection:
    """The file at path (DB by default) in WAL mode, schema applied, and
    cut() the model's clock as a stamp, which the tables' as-of views read.
    The schema is applied when the file does not carry its version (it
    recreates every view, 0.17s), one process at a time: a process that finds
    it applied leaves the views other processes are using alone."""
    path = path or DB
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    functions(db)
    key = (path.resolve(), schema)
    if key not in _APPLIED:
        with open(path.with_name(path.name + ".schema.lock"), "w") as held:
            fcntl.flock(held, fcntl.LOCK_EX)    # one process recreates the views at a time
            texts = [sql.read_text(encoding="utf-8") for sql in (IDENTITY, TABLES_SQL, schema)]
            version = zlib.crc32("\0".join(texts).encode()) & 0x7FFFFFFF
            if db.execute("PRAGMA user_version").fetchone()[0] != version:   # else it is applied: leave the views
                db.execute("PRAGMA journal_mode=WAL")                       # other processes are using
                for sql in texts:
                    db.executescript(sql)
                db.execute("PRAGMA user_version = %d" % version)
        _APPLIED.add(key)
    return db


def query(sql: str, params: Sequence[object] | Mapping[str, object] = (),
          path: Path | None = None) -> tuple[list[str], list[Any]]:
    """A read: its columns, and its rows as tuples."""
    with closing(connect(path)) as db:
        cur = db.execute(sql, params)
        return [d[0] for d in cur.description or ()], cur.fetchall()


META = Path(__file__).resolve().parent.parent.parent / "datasette.json"


def queries(meta: Path = META) -> dict[str, dict[str, str]]:
    """The saved queries (datasette.json), by name: each its sql and title."""
    got: dict[str, dict[str, str]] = json.loads(meta.read_text(encoding="utf-8"))[
        "databases"]["lfg"]["queries"]
    return got


def params(sql: str) -> list[str]:
    return sorted(set(re.findall(r"(?<![:\w]):([A-Za-z_]\w*)", sql)))


def named(name: str, given: Mapping[str, object] | None = None, path: Path | None = None,
          meta: Path = META) -> tuple[list[str], list[Any]]:
    """A saved query run as Datasette runs it: a param not given is '' (an
    empty box), every param text (so the query casts what it needs)."""
    sql = queries(meta)[name]["sql"]
    return query(sql, {p: "" if (given or {}).get(p) is None else str((given or {})[p])
                       for p in params(sql)}, path)


SLOW_S = 8.0   # Datasette's sql_time_limit_ms is 10000 (lfg-datasette.service): nearer than this and a busy day times it out


def timed(path: Path | None = None, meta: Path = META, limit: float = SLOW_S
          ) -> list[tuple[str, float, bool]]:
    """Each saved query run with every parameter empty, as Datasette opens
    it: (name, seconds, within limit). One over the limit is cut off at it,
    not waited for."""
    out = []
    for name, q in queries(meta).items():
        with closing(connect(path)) as db:
            started = time.monotonic()
            db.set_progress_handler(lambda: int(time.monotonic() - started > limit), 100_000)
            try:
                db.execute(q["sql"], {p: "" for p in params(q["sql"])}).fetchall()
            except sqlite3.OperationalError as e:
                if "interrupted" not in str(e):
                    raise
            took = time.monotonic() - started
            out.append((name, took, took <= limit))
    return out


def time_queries(path: Path | None = None, meta: Path = META, limit: float = SLOW_S
                 ) -> dict[str, float]:
    """The run.py queries stage: each saved query timed on the real database
    (timed), recorded with the stage as its seconds, and `over`, how many are
    near Datasette's limit (named in the log). Once a day: not again within 20
    hours of a run that timed them."""
    last = rows("SELECT max(r.started) FROM run_measures m JOIN runs r USING (run_id)"
                " WHERE m.stage = 'queries'", path=path)[0][0]
    if last and (datetime.now(timezone.utc) - datetime.fromisoformat(last.replace("Z", "+00:00"))
                 ).total_seconds() < 20 * 3600:
        return {"skipped": 1}
    got = timed(path, meta, limit)
    for name, secs, ok in got:
        if not ok:
            print("  SLOW saved query %s: %.1fs (Datasette cuts at 10s)" % (name, secs))
    return {**{name: secs for name, secs, _ in got}, "over": sum(not ok for _, _, ok in got)}


def rows(sql: str, params: Sequence[object] | Mapping[str, object] = (),
         path: Path | None = None) -> list[Any]:
    return query(sql, params, path)[1]


def this_run() -> int | None:
    """The run src/run.py is recording (LFG_RUN), or None outside one."""
    got = os.environ.get(RUN_ENV, "")
    return int(got) if got else None


def record(rows: Sequence[Mapping[str, object]], table: str,
           path: Path | None = None, schema: Path = SCHEMA) -> None:
    """Insert rows into table, each a mapping of column to value; a column
    a row lacks is NULL."""
    if not rows:
        return
    cols = list(dict.fromkeys(c for r in rows for c in r))
    sql = 'INSERT INTO "%s" (%s) VALUES (%s)' % (
        table, ", ".join('"%s"' % c for c in cols), ", ".join("?" * len(cols)))
    with closing(connect(path, schema)) as db, db:
        db.executemany(sql, [[r.get(c) for c in cols] for r in rows])


def _selftest() -> None:
    import tempfile
    from contextlib import closing

    with tempfile.TemporaryDirectory() as tmp:
        schema, path = Path(tmp) / "s.sql", Path(tmp) / "x.db"
        schema.write_text("CREATE VIEW IF NOT EXISTS one AS SELECT 1 AS n;")
        connect(path, schema).close()
        with closing(sqlite3.connect(path)) as raw:
            raw.execute("CREATE VIEW marker AS SELECT 1")     # a view another process is using
        _APPLIED.clear()
        connect(path, schema).close()
        with closing(sqlite3.connect(path)) as raw:
            assert raw.execute("SELECT count(*) FROM sqlite_master WHERE name = 'marker'").fetchone()[0] == 1, \
                "a file already at this schema version is left as it is"
        schema.write_text("CREATE VIEW IF NOT EXISTS one AS SELECT 2 AS n; DROP VIEW IF EXISTS marker;")
        _APPLIED.clear()
        connect(path, schema).close()
        with closing(sqlite3.connect(path)) as raw:
            assert raw.execute("SELECT count(*) FROM sqlite_master WHERE name = 'marker'").fetchone()[0] == 0, \
                "a changed schema is applied"

    mem = sqlite3.connect(":memory:")
    functions(mem)
    assert mem.execute("SELECT lo95(1.0, 0.5), hi95(1.0, 0.5), claim(1.0, 0.5), claim(1.0, 0.6),"
                       " claim(-1.0, 0.5), claim(0.0, 1.0), claim(1.0, NULL), lo95(NULL, 1)").fetchone() \
        == (1.0 - 0.98, 1.0 + 0.98, "positive", "not established", "negative",
            "not established", "too few", None), \
        "1.96 standard errors: 1.0 +- 0.98 excludes 0; +- 1.176 does not; no spread, no claim"
    with tempfile.TemporaryDirectory() as tmp:
        meta, path = Path(tmp) / "datasette.json", Path(tmp) / "lfg.db"
        meta.write_text(json.dumps({"databases": {"lfg": {"queries": {
            "quick": {"sql": "SELECT 1"},
            "slow": {"sql": "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n"
                            " WHERE i < 100000000) SELECT count(*) FROM n"}}}}}))
        got = {name: (ok, secs) for name, secs, ok in timed(path, meta, limit=0.3)}
        assert got["quick"][0] and not got["slow"][0], got
        assert got["slow"][1] < 3.0, "a query over the limit is cut off, not waited for"

        first = time_queries(path, meta, limit=0.3)
        assert set(first) == {"quick", "slow", "over"} and first["over"] == 1, first
        with closing(connect(path)) as c, c:     # a run recorded those timings just now
            c.execute("INSERT INTO runs (run_id, started, as_of, trigger, commit_sha)"
                      " VALUES (1, ?, 't', 'service', 'c')",
                      (datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),))
            c.execute("INSERT INTO run_measures VALUES (1, 'queries', 'quick', 0.0)")
        assert time_queries(path, meta, limit=0.3) == {"skipped": 1}, "once a day"
        with closing(connect(path)) as c, c:
            c.execute("UPDATE runs SET started = '2020-01-01T00:00:00Z'")
        assert "quick" in time_queries(path, meta, limit=0.3), "and again the day after"

    with tempfile.TemporaryDirectory() as tmp:
        schema = Path(tmp) / "schema.sql"
        schema.write_text("CREATE TABLE IF NOT EXISTS t (k TEXT PRIMARY KEY);\n")
        path = Path(tmp) / "sub" / "lfg.db"
        with connect(path, schema) as db:
            db.execute("INSERT INTO t VALUES ('a')")
        assert path.exists(), "the file and its directory are made on first use"
        writer = connect(path, schema)
        assert writer.execute("PRAGMA journal_mode").fetchone()[0] == "wal", \
            "WAL, so Datasette reads while a run writes"
        assert writer.execute("SELECT k FROM t").fetchall() == [("a",)], \
            "the schema applies again without touching what is there"
        writer.execute("INSERT INTO t VALUES ('b')")
        reader = connect(path, schema)
        assert reader.execute("SELECT count(*) FROM t").fetchone()[0] == 1, \
            "a reader sees only what is committed"
        writer.commit()
        assert reader.execute("SELECT count(*) FROM t").fetchone()[0] == 2
        writer.close()
        reader.close()
        connect(Path(tmp) / "real.db").close()

        record([], "t", path, schema)
        record([{"k": "c"}, {"k": "d"}], "t", path, schema)
        with closing(connect(path, schema)) as db:
            assert [k for (k,) in db.execute("SELECT k FROM t ORDER BY k")] \
                == ["a", "b", "c", "d"], "record inserts dict rows by column name"
            db.execute("CREATE TABLE u (k TEXT, v TEXT)")
            db.commit()
        record([{"k": "e"}, {"k": "f", "v": "x"}], "u", path, schema)
        with closing(connect(path, schema)) as db:
            assert db.execute("SELECT * FROM u ORDER BY k").fetchall() \
                == [("e", None), ("f", "x")], "a key a row lacks is NULL"

    import os
    for env, want in [("", None), ("12", 12)]:
        os.environ[RUN_ENV] = env
        assert this_run() == want, (env, this_run())
    os.environ.pop(RUN_ENV)
    assert this_run() is None, "outside run.py there is no run"
    assert rows("SELECT name_key(' Iñigo  VICENTE '), name_shown('nico van gaal')",
                path=Path(tempfile.mkdtemp()) / "f.db") == [("inigo vicente", "Nico van Gaal")], \
        "names are compared and shown in SQL by the same rules as Name"
    import re
    from collections import Counter
    made = Counter(re.findall(r"CREATE (?:VIEW|TABLE IF NOT EXISTS) (\w+)",
                              SCHEMA.read_text(encoding="utf-8")))
    assert all(n == 1 for n in made.values()), \
        "schema.sql defines each table and view once: %s" % [k for k, n in made.items() if n > 1]
    with tempfile.TemporaryDirectory() as tmp:
        once = Path(tmp) / "schema.sql"
        once.write_text("CREATE TABLE IF NOT EXISTS n (k INTEGER);"
                        " INSERT INTO n VALUES (1);\n")
        for _ in range(3):
            connect(Path(tmp) / "x.db", once).close()
        with closing(connect(Path(tmp) / "x.db", once)) as db:
            assert db.execute("SELECT count(*) FROM n").fetchone()[0] == 1, \
                "the schema is applied once a file and process, not every connect"
    print("ffcore.db self-test OK")


if __name__ == "__main__":
    _selftest()
