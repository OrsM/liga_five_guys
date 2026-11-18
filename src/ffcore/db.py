"""The run's SQLite file (the observations and the crosswalk, for Datasette and ask.py; disposable: rebuilt from
data/raw, git or a replay, docs/notes/lfg.md) and the run's context: t (set_now, cut) and RunRecords, the records the
run collects and publishes once to the store."""
from __future__ import annotations

import datetime as dt
import fcntl
import importlib
import json
import os
import re
import sqlite3
import time
import zlib
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any

from datetime import datetime

from ffcore.clock import run_now, variant
from ffcore.names import Name
from ffcore.parse import snapshot_stamp, stamp, year_for
from ffcore import store
from ffcore.records import KINDS, Run
from ffcore.text import stems_meet

__all__ = ["DB", "DECIDE_SQL", "IDENTITY", "META", "ROOT", "TABLES_SQL", "named", "params", "queries", "SCHEMA", "RunRecords", "connect", "cut",
           "functions", "query", "rows", "set_now", "LOOK"]

ROOT = Path(os.environ.get("FF_ROOT", "./data"))
DB = ROOT / "lfg.db"  # tidy.tables_in moves it with the tables
SCHEMA = Path(__file__).with_name("schema.sql")
IDENTITY = Path(__file__).with_name("identity.sql")
TABLES_SQL = Path(__file__).with_name("tables.sql")
DECIDE_SQL = Path(__file__).with_name("decide.sql")     # the decision: its views read schema.sql's
SOURCE = ("market", "lineups", "matches", "points", "api_teams", "api_standings", "api_market", "api_offers",
          "api_lineup", "api_leagues", "api_players_all", "results_history", "starters", "api_activity")


def source_table(name: str) -> str:
    return "src_" + name


def migrate(db: sqlite3.Connection) -> None:
    """A database whose source tables have the names before their layer's prefix (src_): renamed, their
    views and triggers dropped first."""
    old = [t for t in SOURCE if db.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (t,)).fetchone()]
    if not old:
        return
    for (v,) in db.execute("SELECT name FROM sqlite_master WHERE type = 'view'").fetchall():
        db.execute('DROP VIEW "%s"' % v)
    for t in old:
        for (trigger,) in db.execute("SELECT name FROM sqlite_master WHERE type = 'trigger' AND tbl_name = ?", (t,)).fetchall():
            db.execute('DROP TRIGGER "%s"' % trigger)
        db.execute('ALTER TABLE "%s" RENAME TO "%s"' % (t, source_table(t)))
_APPLIED: set[tuple[Path, Path]] = set()  # (file, schema) this process has applied


def stamp_jd(s: str | None) -> float | None:
    """A stamp (2026-11-01T2224Z, or with seconds or a zone as snapshot_stamp
    reads them) as julianday() gives it, None if it is not one."""
    t = snapshot_stamp(s) if s else None
    return None if t is None else t.timestamp() / 86400.0 + 2440587.5


MONTHS = {m: i + 1 for i, m in enumerate(
    "enero febrero marzo abril mayo junio julio agosto septiembre octubre noviembre diciembre".split())}
PART = {"principios": 5, "princpios": 5, "mediados": 15, "finales": 25}
_UNTIL = re.compile(r"Baja (?:hasta|hsata) (?:(%s) de )?(%s)(?:-\w+)?(?: (\d{4}))?"
                    % ("|".join(PART), "|".join(MONTHS)))
_SAYS = [(re.compile(r"Duda para la jornada (\d+)"), "doubt"),
         (re.compile(r"Baja confirmada para la jornada (\d+)"), "out"),
         (re.compile(r"Disponible para la jornada (\d+)"), "back")]
# A flagged player whose note names no jornada: his status, of his next match.
_STATUS = {"doubt": "doubt", "suspended": "out", "unavailable": "out", "injured": "out"}


def prognosis(status: str | None, note: str | None, seen_at: str | None) -> str | None:
    """What a flagged player's listing (player_status) says, as JSON for
    forecast_fit: {"kind": doubt, out (up to and in j) or back (from j),
    "j": a jornada, or null for his next match}, {"kind": "until", "until":
    a date}, or {"kind": "never"} (left the league, or out indefinitely);
    None if he is fit or it says nothing. futbolfantasy's note is read
    first ('Duda para la jornada 8', 'Baja hasta mediados de octubre': a
    month already past is next year's), else the status."""
    if status in (None, "", "ok"):
        return None
    note = note or ""
    if status == "gone" or "Baja indefinida" in note:
        return json.dumps({"kind": "never"})
    for pat, kind in _SAYS:
        if m := pat.search(note):
            return json.dumps({"kind": kind, "j": int(m.group(1))})
    seen = snapshot_stamp(seen_at) if seen_at else None
    if seen and (m := _UNTIL.search(note)):
        month = MONTHS[m.group(2)]
        year = int(m.group(3)) if m.group(3) else year_for(month, seen.date(), seen.month - 1)
        return json.dumps({"kind": "until", "until": dt.date(year, month, PART.get(m.group(1), 15)).isoformat()})
    return json.dumps({"kind": _STATUS[status], "j": None}) if status in _STATUS else None


# What the model looks at, this process's (never the file's: every process
# on it, a run, a replay, the watcher, Datasette, has its own): t, the one
# moment every view is read as of (a run's start, or a replay's past time:
# set_now). The views read it as cut().
LOOK: dict[str, Any] = {"t": None}


def cut() -> str:
    """t as a stamp: the process's run start (the wall clock when first
    asked, clock.run_now) unless set_now set it."""
    if LOOK["t"] is None:
        LOOK["t"] = run_now()
    return stamp(LOOK["t"])


def set_now(when: datetime | None) -> None:
    """Set t: a replay's past time, or None for the wall clock now."""
    LOOK["t"] = when or run_now()


# The model's constants are module attributes (forecast, pricing, rules); a variant (assemble.applied) or a test is just set_constants.
CONSTANT_MODULES = ("ffcore.forecast", "ffcore.pricing", "ffcore.rules", "ffcore.tidy")


def _owner(name: str) -> Any:
    for module in CONSTANT_MODULES:
        owner = importlib.import_module(module)
        if hasattr(owner, name):
            return owner
    raise KeyError("no module has the constant %s" % name)


@contextmanager
def set_constants(**values: float) -> Iterator[None]:
    """Constants set on the modules that own them, put back on leaving."""
    was = {n: getattr(_owner(n), n) for n in values}
    try:
        for n, v in values.items():
            setattr(_owner(n), n, v)
        yield
    finally:
        for n, v in was.items():
            setattr(_owner(n), n, v)


def functions(db: sqlite3.Connection, now: Callable[[], str] = cut) -> None:
    """The functions the views call: cut() (t as a stamp: the process's,
    unless told otherwise), stamp_jd() (a stamp as a Julian day), variant() (the variant in force), name_key() and
    name_shown() (Name's) and stems_meet() (text's)."""
    db.create_function("cut", 0, now)
    db.create_function("stamp_jd", 1, stamp_jd, deterministic=True)
    db.create_function("variant", 0, variant)
    db.create_function("name_key", 1, lambda s: Name(s).key, deterministic=True)
    db.create_function("name_shown", 1, lambda s: Name(s).shown, deterministic=True)
    db.create_function("stems_meet", 3, stems_meet, deterministic=True)


def fit_columns(db: sqlite3.Connection, texts: Sequence[str]) -> None:
    """The columns these schema texts give a table that db's copy of it lacks,
    added, and those it has that they no longer give, dropped. A column a table
    cannot gain (NOT NULL without a default) raises."""
    with closing(sqlite3.connect(":memory:")) as want:
        functions(want)
        for sql in texts:
            want.executescript(sql)
        for (t,) in want.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall():
            have = {r[1] for r in db.execute('PRAGMA table_info("%s")' % t)}
            if not have:
                continue
            wanted = want.execute('PRAGMA table_info("%s")' % t).fetchall()
            gone = have - {r[1] for r in wanted}
            if gone or {r[1] for r in wanted} - have:    # a trigger on the old columns would stop the drop, or miss the new
                for (trigger,) in db.execute("SELECT name FROM sqlite_master WHERE type = 'trigger' AND tbl_name = ?", (t,)).fetchall():
                    db.execute('DROP TRIGGER "%s"' % trigger)
            if gone:    # a view on the column would stop the drop: the texts make every view again
                for (v,) in db.execute("SELECT name FROM sqlite_master WHERE type = 'view'").fetchall():
                    db.execute('DROP VIEW "%s"' % v)
            for col in sorted(gone):
                db.execute('ALTER TABLE "%s" DROP COLUMN "%s"' % (t, col))
            for _cid, col, typ, notnull, dflt, _pk in wanted:
                if col in have:
                    continue
                if notnull and dflt is None:
                    raise ValueError("%s.%s is NOT NULL with no default: a kept table cannot gain it"
                                     % (t, col))
                db.execute('ALTER TABLE "%s" ADD COLUMN "%s" %s%s%s' % (
                    t, col, typ, " NOT NULL" if notnull else "",
                    "" if dflt is None else " DEFAULT %s" % dflt))


# The Python constants the SQL names, put in where it names them.
HEALTH_SOURCES = ("market", "lineups", "api_standings", "api_lineup", "api_teams", "api_leagues", "api_market", "api_offers")
HEALTH_USUAL = 21


def _snapshots(recent: bool) -> str:
    """The rows each snapshot of each source parser_health watches holds, as of t: all of them, or only the
    HEALTH_USUAL newest of each (all that its status reads)."""
    def arm(src: str) -> str:
        table = source_table(src)
        newest = ("AND observed_at >= (SELECT min(at) FROM (SELECT DISTINCT observed_at AS at FROM %s"
                  " WHERE observed_at <= (SELECT cut()) ORDER BY at DESC LIMIT %d))" % (table, HEALTH_USUAL)) if recent else ""
        return ("SELECT '%s' AS src, observed_at AS at, count(*) AS n FROM %s WHERE observed_at <= (SELECT cut()) %s"
                " GROUP BY observed_at" % (src, table, newest))
    return " UNION ALL ".join(arm(src) for src in HEALTH_SOURCES)


PUT = {"/* health.SNAPSHOTS */": _snapshots(False), "/* health.RECENT */": _snapshots(True)}


# "Each source's last snapshot as of t", said once: `WHERE /* latest TABLE */`
# on a table aliased t. "A key's newest row", said once: `/* newest KEY */` is
# its row_number() (n = 1 is the newest), where rows are ordered as they came.
_LATEST = re.compile(r"/\* latest (\w+) \*/")
_NEWEST = re.compile(r"/\* newest ([^*]+) \*/")


def _put(sql: str) -> str:
    for name, text in PUT.items():
        sql = sql.replace(name, text)
    sql = _NEWEST.sub(lambda m: "row_number() OVER (PARTITION BY %s ORDER BY observed_at DESC, rowid DESC)" % m[1], sql)
    return _LATEST.sub(lambda m: _latest_of(source_table(m[1]) if m[1] in SOURCE else m[1]), sql)


def _latest_of(table: str) -> str:
    """The predicate on t: its (source, observed_at) is its source's newest at or before cut(). One
    seek per source on the (source, observed_at) index, not a walk of it."""
    return ("(t.source, t.observed_at) IN (WITH RECURSIVE s(source) AS (SELECT min(source) FROM {0} UNION ALL "
            "SELECT (SELECT min(source) FROM {0} WHERE source > s.source) FROM s WHERE source IS NOT NULL) "
            "SELECT source, (SELECT max(observed_at) FROM {0} WHERE source = s.source "
            "AND observed_at <= (SELECT cut())) FROM s WHERE source IS NOT NULL)").format(table)


def connect(path: Path | None = None, schema: Path = SCHEMA) -> sqlite3.Connection:
    """The file at path (DB by default) in WAL mode, schema applied, and
    cut() the process's t, which the as-of views read. The schema is applied when the file
    does not carry its version, one process at a time: a process that finds it applied leaves
    the views other processes are using alone."""
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
                migrate(db)
                fit_columns(db, texts)
                for sql in texts:
                    db.executescript(sql)
                db.execute("PRAGMA user_version = %d" % version)
        _APPLIED.add(key)
    import crosswalk                            # not at the top: it imports this module
    crosswalk.identity(db)
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


class RunRecords:
    """The records of one run, collected in memory and published once. add(kind, rows) keeps the rows of a kind in the
    order given, a mapping lacking run_id taking this run's (a kind's row type is ffcore.records.KINDS); rows(kind)
    returns what was added; publish(run) writes the Run row and every kind added as one store directory
    (store.publish), so a run that does not reach it leaves nothing. commit_sha is the code the run is."""

    def __init__(self, run: int, commit_sha: str) -> None:
        self.run = run
        self.commit_sha = commit_sha
        self._kinds: dict[str, list[Any]] = {}

    @classmethod
    def after_latest(cls, commit_sha: str) -> "RunRecords":
        """The RunRecords of the run after the latest published one (called under the run lock)."""
        latest = store.latest_run()
        return cls((latest.run_id if latest else 0) + 1, commit_sha)

    def add(self, kind: str, rows: Iterable[Any]) -> None:
        row = KINDS[kind].row
        owned = {"run_id": self.run} if "run_id" in row._fields else {}
        self._kinds.setdefault(kind, []).extend(r if isinstance(r, row) else row(**owned, **r) for r in rows)

    def rows(self, kind: str) -> list[Any]:
        return list(self._kinds.get(kind, ()))

    def publish(self, run: Run) -> Path:
        return store.publish(run, **self._kinds)


def _selftest_run_records() -> None:
    import tempfile

    from ffcore.records import Param

    with tempfile.TemporaryDirectory() as tmp:
        kept = os.environ.get("FF_ROOT")
        os.environ["FF_ROOT"] = str(Path(tmp) / "data")
        try:
            assert RunRecords.after_latest("x").run == 1
            got = RunRecords(3, "abc")
            got.add("params", [{"component": "c", "term": "t", "value": 1.0, "kind": "fitted", "n": None}, Param(3, "d", "", 2.0, "constant", 4)])
            got.add("dec_opportunity", [])
            assert [(p.run_id, p.component) for p in got.rows("params")] == [(3, "c"), (3, "d")] and got.rows("auction_brier") == [], \
                "a mapping takes the run's id; rows come back in the order added"
            assert store.runs() == [], "nothing is visible before publish"
            run = Run(3, "t", "a", "service", "abc", "f", 1, 1.0)
            got.publish(run)
            assert store.runs() == [run] and list(store.records("params", 3)) == got.rows("params"), "published once, whole"
            assert RunRecords.after_latest("x").run == 4, "the run after the latest published"
            try:
                got.add("params", [{"component": "c"}])
            except TypeError:
                pass
            else:
                raise AssertionError("a row lacking a field is refused")
        finally:
            store.clear()
            if kept is None:
                os.environ.pop("FF_ROOT")
            else:
                os.environ["FF_ROOT"] = kept


def _selftest() -> None:
    import tempfile
    from contextlib import closing

    with tempfile.TemporaryDirectory() as tmp:
        schema, path = Path(tmp) / "s.sql", Path(tmp) / "x.db"
        schema.write_text("CREATE VIEW IF NOT EXISTS one AS SELECT 1 AS n;")
        connect(path, schema).close()
        with closing(connect(Path(tmp) / "empty.db")) as fresh:
            assert fresh.execute("SELECT count(*) FROM facts_meta WHERE name <> 'ff_identity'").fetchone()[0] == 0, \
                "connect_runs_without_the_stores: an empty root opens with the schema applied and nothing kept but the identity"
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
                          " CREATE VIEW IF NOT EXISTS kb AS SELECT b, c FROM kept;"
                          " CREATE TRIGGER IF NOT EXISTS kept_same BEFORE INSERT ON kept"
                          " WHEN EXISTS (SELECT 1 FROM kept o WHERE o.a IS NEW.a AND o.b IS NEW.b) BEGIN SELECT RAISE(IGNORE); END;")
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
                          " DROP VIEW IF EXISTS kb;"
                          " CREATE TRIGGER IF NOT EXISTS kept_same BEFORE INSERT ON kept"
                          " WHEN EXISTS (SELECT 1 FROM kept o WHERE o.a IS NEW.a AND o.c IS NEW.c) BEGIN SELECT RAISE(IGNORE); END;")
        _APPLIED.clear()
        with closing(connect(path, schema)) as db, db:
            assert db.execute("SELECT * FROM kept").fetchall() == [(1, "")], \
                "a column the schema no longer gives is dropped, the rows kept, its trigger made again on the new columns"
            db.execute("INSERT INTO kept VALUES (1, '')")
            assert db.execute("SELECT count(*) FROM kept").fetchone() == (1,), "the new trigger skips a row its key already has"

    def says(status: str, note: str = "", seen: str = "2026-09-28T1000Z") -> Any:
        got = prognosis(status, note, seen)
        return got and json.loads(got)
    assert says("doubt", "Lesión Desde 13/09 (15 días) Duda para la jornada 8") == {"kind": "doubt", "j": 8}
    assert says("injured", "x Baja confirmada para la jornada 9") == {"kind": "out", "j": 9}
    assert says("injured", "x Disponible para la jornada 10") == {"kind": "back", "j": 10}
    assert says("injured", "x Baja hasta mediados de octubre") == {"kind": "until", "until": "2026-10-15"}
    assert says("injured", "x Baja hasta enero") == {"kind": "until", "until": "2027-01-15"}, \
        "a month already past is next year's"
    assert says("injured", "x Baja hasta finales de enero 2028") == {"kind": "until", "until": "2028-01-25"}
    assert says("injured", "x Baja hsata principios de noviembre") == {"kind": "until", "until": "2026-11-05"}, \
        "the site's own typo"
    assert says("injured", "x Baja indefinida") == says("gone") == {"kind": "never"}, "out for good"
    assert says("suspended", "Sancionado") == {"kind": "out", "j": None}, "a status alone: his next match"
    assert says("doubt") == {"kind": "doubt", "j": None}
    assert says("ok", "Duda para la jornada 9") is None and says("", "") is None, "fit: nothing"
    assert says("other") is None, "a status that says nothing of playing"

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


    with tempfile.TemporaryDirectory() as tmp:
        assert rows("SELECT name_key(' Iñigo  VICENTE '), name_shown('nico van gaal')",
                    path=Path(tmp) / "f.db") == [("inigo vicente", "Nico van Gaal")], \
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
    import random
    rng = random.Random(7)
    with closing(sqlite3.connect(":memory:")) as db:
        cutoff = [0]
        db.create_function("cut", 0, lambda: cutoff[0])
        db.execute("CREATE TABLE x (source TEXT NOT NULL, observed_at INTEGER NOT NULL, v INTEGER)")
        db.execute("CREATE INDEX x_at ON x (source, observed_at)")
        db.executemany("INSERT INTO x VALUES (?, ?, ?)", [(rng.choice("abcde"), rng.randrange(50), i) for i in range(300)])
        for cutoff[0] in (-1, 0, 7, 25, 49, 99):
            plain = db.execute("SELECT v FROM x t WHERE (t.source, t.observed_at) IN (SELECT source, max(observed_at) "
                               "FROM x WHERE observed_at <= (SELECT cut()) GROUP BY source) ORDER BY v").fetchall()
            loose = db.execute("SELECT v FROM x t WHERE %s ORDER BY v" % _latest_of("x")).fetchall()
            assert loose == plain, "each source's newest rows at or before cut(): the same as the grouped maximum"
    _selftest_run_records()
    print("ffcore.db self-test OK")


if __name__ == "__main__":
    _selftest()
