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
from statistics import NormalDist
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any

from datetime import datetime

from ffcore.clock import run_now, variant
from ffcore.facts import ensure
from ffcore.names import Name
from ffcore.parse import snapshot_stamp, stamp
from stats import shrink
from ffcore.forecast import SEED_POOL
from ffcore.rules import ELEVEN, FORMATIONS, position, slot
from ffcore.text import stems_meet
from ffcore.startprob import platt, prognosis_bucket

__all__ = ["DB", "DECIDE_SQL", "IDENTITY", "META", "ROOT", "TABLES_SQL", "named", "params", "queries", "RUN_ENV", "SCHEMA", "connect", "cut",
           "functions", "query", "record", "replaying", "rows", "set_look", "set_now", "this_run", "LOOK"]

ROOT = Path(os.environ.get("FF_ROOT", "./data"))
DB = ROOT / "lfg.db"  # tidy.tables_in moves it with the tables
SCHEMA = Path(__file__).with_name("schema.sql")
IDENTITY = Path(__file__).with_name("identity.sql")
TABLES_SQL = Path(__file__).with_name("tables.sql")
DECIDE_SQL = Path(__file__).with_name("decide.sql")     # the decision: its views read schema.sql's
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


def _quantile(sorted_values: list[float], p: float) -> float:
    """Linear between ranks (numpy's default)."""
    pos = (len(sorted_values) - 1) * p
    lo = int(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


class _Median:
    def __init__(self) -> None:
        self.values: list[float] = []

    def step(self, x: Any) -> None:
        if x is not None:
            self.values.append(float(x))

    def finalize(self) -> float | None:
        return _quantile(sorted(self.values), 0.5) if self.values else None


def stamp_jd(s: str | None) -> float | None:
    """A stamp (2026-11-01T2224Z, or with seconds or a zone as snapshot_stamp
    reads them) as julianday() gives it, None if it is not one."""
    t = snapshot_stamp(s) if s else None
    return None if t is None else t.timestamp() / 86400.0 + 2440587.5


# What the model looks at, this process's (never the file's: every process
# on it, a run, a replay, the watcher, Datasette, has its own): t, the one
# moment every view is read as of (a run's start, or a replay's past time:
# set_now), the forecast moment, who you are, and the path whose moves the
# league at t holds since t0 ('': live, none). The views read them as
# cut(), moment(), me(), path(), t0() and the view look.
LOOK: dict[str, Any] = {"t": None, "moment": 0, "me": "", "path": "", "t0": ""}


def cut() -> str:
    """t as a stamp: the process's run start (the wall clock when first
    asked, clock.run_now) unless set_now set it."""
    if LOOK["t"] is None:
        LOOK["t"] = run_now()
    return stamp(LOOK["t"])


def set_now(when: datetime | None) -> None:
    """Set t: a replay's past time, or None for the wall clock now."""
    LOOK["t"] = when or run_now()


def set_look(**what: Any) -> None:
    """The forecast moment, you, and the path (and its t0) the views read."""
    assert set(what) <= set(LOOK) - {"t"}, what
    LOOK.update(what)


@contextmanager
def replaying() -> Iterator[None]:
    """What the views look at put back as it was once a replay that moves t
    or the path is done."""
    was = dict(LOOK)
    try:
        yield
    finally:
        LOOK.update(was)


def functions(db: sqlite3.Connection, now: Callable[[], str] = cut) -> None:
    """The functions the views call: cut() (t as a stamp: the process's,
    unless told otherwise), moment(), me(), path() and t0() (LOOK's),
    stamp_jd() (a stamp as a Julian day), variant() (the constants in force), name_key() and name_shown() (Name's),
    platt() and prognosis_bucket() (startprob's), shrink() (stats', NULL as SQL arithmetic gives it), position() (rules'), ncdf() and ninv() (the standard normal's cdf and its inverse),
    lo95(), hi95() and claim() (an estimate and its standard error) and
    the aggregate median()."""
    db.create_function("cut", 0, now)
    for k in ("moment", "me", "path", "t0"):
        db.create_function(k, 0, lambda k=k: LOOK[k])
    db.create_function("stamp_jd", 1, stamp_jd, deterministic=True)
    db.create_function("variant", 0, variant)
    db.create_function("hpad", 0, lambda: 0)
    db.create_function("name_key", 1, lambda s: Name(s).key, deterministic=True)
    db.create_function("name_shown", 1, lambda s: Name(s).shown, deterministic=True)
    db.create_aggregate("median", 1, _Median)  # type: ignore[arg-type]  # the stubs only allow int results
    for fn in (lo95, hi95, claim):
        db.create_function(fn.__name__, 2, fn, deterministic=True)
    db.create_function("platt", 3, platt, deterministic=True)
    db.create_function("shrink", 4, lambda prior, k, total, n: None if None in (prior, k, total, n) or k + n == 0
                       else shrink(prior, k, total, n), deterministic=True)
    db.create_function("ncdf", 1, NormalDist().cdf, deterministic=True)
    db.create_function("ninv", 1, NormalDist().inv_cdf, deterministic=True)
    db.create_function("position", 1, position, deterministic=True)
    db.create_function("slot", 1, slot, deterministic=True)
    db.create_function("stems_meet", 3, stems_meet, deterministic=True)
    db.create_function("prognosis_bucket", 6, prognosis_bucket, deterministic=True)


def fit_columns(db: sqlite3.Connection, texts: Sequence[str]) -> None:
    """The columns these schema texts give a table that db's copy of it lacks,
    added, and those it has that they no longer give, dropped (CREATE TABLE
    IF NOT EXISTS leaves a kept table as it was made; a drop drops every
    view first, a view the texts no longer make with it). A column a table
    cannot gain (NOT NULL without a default) raises."""
    with closing(sqlite3.connect(":memory:")) as want:
        functions(want)
        for sql in texts:
            want.executescript(sql)
        for (t,) in want.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall():
            have = {r[1] for r in db.execute('PRAGMA table_info("%s")' % t)}
            if not have:
                continue
            gone = have - {r[1] for r in want.execute('PRAGMA table_info("%s")' % t)}
            if gone:    # a view on the column would stop the drop: the texts make every view again
                for (v,) in db.execute("SELECT name FROM sqlite_master WHERE type = 'view'").fetchall():
                    db.execute('DROP VIEW "%s"' % v)
            for col in sorted(gone):
                db.execute('ALTER TABLE "%s" DROP COLUMN "%s"' % (t, col))
            for _cid, col, typ, notnull, dflt, _pk in want.execute('PRAGMA table_info("%s")' % t).fetchall():
                if col in have:
                    continue
                if notnull and dflt is None:
                    raise ValueError("%s.%s is NOT NULL with no default: a kept table cannot gain it"
                                     % (t, col))
                db.execute('ALTER TABLE "%s" ADD COLUMN "%s" %s%s%s' % (
                    t, col, typ, " NOT NULL" if notnull else "",
                    "" if dflt is None else " DEFAULT %s" % dflt))


# The Python constants the SQL names, put in where it names them.
PUT = {"/* rules.ELEVEN */": ELEVEN, "/* rules.FORMATIONS */": FORMATIONS,
       "/* forecast.SEED_POOL */": " UNION ALL ".join("SELECT %d AS x" % x for x in SEED_POOL)}


def _put(sql: str) -> str:
    for name, text in PUT.items():
        sql = sql.replace(name, text)
    return sql


def connect(path: Path | None = None, schema: Path = SCHEMA) -> sqlite3.Connection:
    """The file at path (DB by default) in WAL mode, schema applied, and
    cut() the process's t, which the as-of views read. The schema is applied when the file does not carry its version (it
    recreates every view, 0.17s), one process at a time: a process that finds
    it applied leaves the views other processes are using alone."""
    path = path or DB
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=120)     # a backtest builds its universes in several processes, each writing its fits
    functions(db)
    db.execute("PRAGMA synchronous = NORMAL")      # WAL: a commit need not wait for the disk
    db.execute("PRAGMA temp_store = MEMORY")
    key = (path.resolve(), schema)
    if key not in _APPLIED:
        with open(path.with_name(path.name + ".schema.lock"), "w") as held:
            fcntl.flock(held, fcntl.LOCK_EX)    # one process recreates the views at a time
            texts = [_put(sql.read_text(encoding="utf-8"))
                     for sql in (IDENTITY, TABLES_SQL, schema) + ((DECIDE_SQL, ) if schema == SCHEMA else ())]
            version = zlib.crc32("\0".join(texts).encode()) & 0x7FFFFFFF
            if db.execute("PRAGMA user_version").fetchone()[0] != version:   # else it is applied: leave the views
                db.execute("PRAGMA journal_mode=WAL")                       # other processes are using
                if db.execute("SELECT 1 FROM sqlite_master WHERE name = 'look' AND type = 'table'").fetchone():
                    db.execute("DROP TABLE look")                           # a view of LOOK now
                fit_columns(db, texts)
                for sql in texts:
                    db.executescript(sql)
                db.execute("PRAGMA user_version = %d" % version)
        _APPLIED.add(key)
    ensure(db, cut)
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

        schema.write_text("CREATE TABLE IF NOT EXISTS kept (a INTEGER NOT NULL);")
        _APPLIED.clear()
        with closing(connect(path, schema)) as db, db:
            db.execute("INSERT INTO kept VALUES (1)")
        schema.write_text("CREATE TABLE IF NOT EXISTS kept (a INTEGER NOT NULL, b REAL, c TEXT NOT NULL DEFAULT '');"
                          " CREATE VIEW IF NOT EXISTS kb AS SELECT b, c FROM kept;")
        _APPLIED.clear()
        with closing(connect(path, schema)) as db:
            assert db.execute("SELECT a, b, c FROM kept").fetchall() == [(1, None, "")], \
                "a kept table gains the schema's new columns, its rows kept"
        schema.write_text("CREATE TABLE IF NOT EXISTS kept (a INTEGER NOT NULL, b REAL, c TEXT NOT NULL DEFAULT '',"
                          " d INTEGER NOT NULL);")
        _APPLIED.clear()
        try:
            connect(path, schema)
        except ValueError:
            pass
        else:
            raise AssertionError("a NOT NULL column with no default cannot be added to kept rows")
        schema.write_text("CREATE TABLE IF NOT EXISTS kept (a INTEGER NOT NULL, c TEXT NOT NULL DEFAULT '');"
                          " DROP VIEW IF EXISTS kb;")
        _APPLIED.clear()
        with closing(connect(path, schema)) as db:
            assert db.execute("SELECT * FROM kept").fetchall() == [(1, "")], \
                "a column the schema no longer gives is dropped, the rows kept"

    mem = sqlite3.connect(":memory:")
    functions(mem)
    mem.execute("CREATE TABLE t (x REAL, y REAL)")
    mem.executemany("INSERT INTO t VALUES (?, ?)", [(1, 2), (2, 4), (3, 6), (4, 8), (None, 9), (100, 1)])
    assert mem.execute("SELECT median(x), median(x) FROM t WHERE x < 10").fetchone() == (2.5, 2.5), "even count: the middle two"
    assert mem.execute("SELECT median(x) FROM t").fetchone() == (3.0,), "odd count, nulls ignored"
    assert mem.execute("SELECT median(x) FROM t WHERE x > 1000").fetchone() == (None,)
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
