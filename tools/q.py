"""Run a saved query of datasette.json against data/lfg.db: the same SQL
Datasette serves.

    python tools/q.py                     # the queries, with what each answers
    python tools/q.py NAME [param=value]  # one, as a table
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from typing import Any

from ffcore.db import DB

META = Path(__file__).resolve().parent.parent / "datasette.json"


def queries(meta: Path = META) -> dict[str, dict[str, str]]:
    """The saved queries, by name: each has its sql and a title."""
    got: dict[str, dict[str, str]] = json.loads(meta.read_text(encoding="utf-8"))[
        "databases"]["lfg"]["queries"]
    return got


def params(sql: str) -> list[str]:
    return sorted(set(re.findall(r"(?<![:\w]):([A-Za-z_]\w*)", sql)))


def run(name: str, given: dict[str, str], path: Path = DB, meta: Path = META
        ) -> tuple[list[str], list[tuple[Any, ...]]]:
    """A saved query's columns and rows; a param not given is NULL, as in
    Datasette when its box is left empty."""
    sql = queries(meta)[name]["sql"]
    with closing(sqlite3.connect("file:%s?mode=ro" % path, uri=True)) as db:
        cur = db.execute(sql, {p: given.get(p) or None for p in params(sql)})
        return [d[0] for d in cur.description], cur.fetchall()


def _cell(v: Any) -> str:
    return "" if v is None else "%.3f" % v if isinstance(v, float) else str(v)


def main(argv: list[str]) -> int:
    if not argv:
        for name, q in queries().items():
            print("%-22s %s%s" % (name, q.get("title", ""), "".join(
                "  :" + p for p in params(q["sql"]))))
        return 0
    name, given = argv[0], dict(a.split("=", 1) for a in argv[1:])
    cols, rows = run(name, given)
    cells = [cols] + [[_cell(v) for v in r] for r in rows]
    width = [max(len(r[i]) for r in cells) for i in range(len(cols))]
    for r in cells:
        print("  ".join(c.rjust(w) for c, w in zip(r, width)))
    return 0


def _selftest() -> None:
    import json
    import tempfile
    from contextlib import closing
    from pathlib import Path

    from ffcore.db import SCHEMA, connect

    with tempfile.TemporaryDirectory() as tmp:
        meta = Path(tmp) / "datasette.json"
        meta.write_text(json.dumps({"databases": {"lfg": {"queries": {
            "big": {"title": "runs over a size",
                    "sql": "SELECT run_id FROM runs WHERE peak_mb > coalesce(:mb, 0)"
                           " ORDER BY run_id"}}}}}))
        db = Path(tmp) / "lfg.db"
        with closing(connect(db)) as c, c:
            c.executemany("INSERT INTO runs (started, as_of, trigger, commit_sha, peak_mb)"
                          " VALUES ('t', 't', 'service', 'a', ?)", [(100.0,), (300.0,)])
        assert queries(meta) == {"big": queries(meta)["big"]} and "sql" in queries(meta)["big"]
        assert run("big", {"mb": "200"}, db, meta) == (["run_id"], [(2,)])
        assert run("big", {}, db, meta) == (["run_id"], [(1,), (2,)]), "a missing param is NULL"
    with closing(connect(Path(tmp + "-empty.db"))) as c:
        for name, q in queries().items():
            try:
                c.execute("EXPLAIN " + q["sql"], {p: None for p in params(q["sql"])})
            except Exception as e:
                raise AssertionError("datasette.json query %s: %s" % (name, e)) from e
    Path(tmp + "-empty.db").unlink()
    assert SCHEMA.exists()
    print("q.py self-test OK")


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        _selftest()
        raise SystemExit(0)
    raise SystemExit(main(sys.argv[1:]))
