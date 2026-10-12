"""The run's SQLite file: what each run measured and concluded, for
Datasette and ask.py. Disposable: every table but runs is rebuilt from
data/raw, git or a replay (docs/plan-datasette.md)."""
from __future__ import annotations

import json
import os
import re
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from contextlib import closing
from pathlib import Path
from typing import Any

from ffcore.clock import cut
from ffcore.names import Name

__all__ = ["DB", "IDENTITY", "META", "ROOT", "TABLES_SQL", "named", "params", "queries", "RUN_ENV", "SCHEMA", "connect", "functions", "query",
           "record", "rows", "this_run"]

ROOT = Path(os.environ.get("FF_ROOT", "./data"))
DB = ROOT / "lfg.db"  # tidy.tables_in moves it with the tables
SCHEMA = Path(__file__).with_name("schema.sql")
IDENTITY = Path(__file__).with_name("identity.sql")
TABLES_SQL = Path(__file__).with_name("tables.sql")
RUN_ENV = "LFG_RUN"
_APPLIED: set[tuple[Path, Path]] = set()  # (file, schema) this process has applied


def functions(db: sqlite3.Connection, now: Callable[[], str] = cut) -> None:
    """The functions the views call: cut() (now, as a stamp: the model's
    clock unless told otherwise), name_key() and name_shown() (Name's)."""
    db.create_function("cut", 0, now)
    db.create_function("name_key", 1, lambda s: Name(s).key, deterministic=True)
    db.create_function("name_shown", 1, lambda s: Name(s).shown, deterministic=True)


def connect(path: Path | None = None, schema: Path = SCHEMA) -> sqlite3.Connection:
    """The file at path (DB by default) in WAL mode, schema applied, and
    cut() the model's clock as a stamp, which the tables' as-of views read.
    The schema is applied once a file and process (it recreates every view,
    0.17s); it only adds (CREATE ... IF NOT EXISTS), so applying it again
    changes nothing."""
    path = path or DB
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    functions(db)
    key = (path.resolve(), schema)
    if key not in _APPLIED:
        db.execute("PRAGMA journal_mode=WAL")
        for sql in (IDENTITY, TABLES_SQL, schema):
            db.executescript(sql.read_text(encoding="utf-8"))
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
