"""The run's SQLite file: what each run measured and concluded, for
Datasette and ask.py. Disposable: every table but runs is rebuilt from
data/raw, git or a replay (docs/plan-datasette.md)."""
from __future__ import annotations

import os
import sqlite3
from collections.abc import Mapping, Sequence
from contextlib import closing
from pathlib import Path
from typing import Any

from ffcore.clock import cut

__all__ = ["DB", "ROOT", "RUN_ENV", "SCHEMA", "connect", "query", "record", "rows", "this_run"]

ROOT = Path(os.environ.get("FF_ROOT", "./data"))
DB = ROOT / "lfg.db"  # tidy.tables_in moves it with the tables
SCHEMA = Path(__file__).with_name("schema.sql")
RUN_ENV = "LFG_RUN"


def connect(path: Path | None = None, schema: Path = SCHEMA) -> sqlite3.Connection:
    """The file at path (DB by default) in WAL mode, schema applied, and
    cut() the model's clock as a stamp, which the tables' as-of views read.
    The schema only adds (CREATE ... IF NOT EXISTS), so applying it again
    changes nothing."""
    path = path or DB
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.create_function("cut", 0, cut)
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(schema.read_text(encoding="utf-8"))
    return db


def query(sql: str, params: Sequence[object] | Mapping[str, object] = (),
          path: Path | None = None) -> tuple[list[str], list[Any]]:
    """A read: its columns, and its rows as tuples."""
    with closing(connect(path)) as db:
        cur = db.execute(sql, params)
        return [d[0] for d in cur.description or ()], cur.fetchall()


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
    import re
    from collections import Counter
    made = Counter(re.findall(r"CREATE (?:VIEW|TABLE IF NOT EXISTS) (\w+)",
                              SCHEMA.read_text(encoding="utf-8")))
    assert all(n == 1 for n in made.values()), \
        "schema.sql defines each table and view once: %s" % [k for k, n in made.items() if n > 1]
    print("ffcore.db self-test OK")


if __name__ == "__main__":
    _selftest()
