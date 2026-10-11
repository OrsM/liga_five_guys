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

from ffcore.db import connect, query

META = Path(__file__).resolve().parent.parent / "datasette.json"


def queries(meta: Path = META) -> dict[str, dict[str, str]]:
    """The saved queries, by name: each has its sql and a title."""
    got: dict[str, dict[str, str]] = json.loads(meta.read_text(encoding="utf-8"))[
        "databases"]["lfg"]["queries"]
    return got


def params(sql: str) -> list[str]:
    return sorted(set(re.findall(r"(?<![:\w]):([A-Za-z_]\w*)", sql)))


def run_params(sql: str, given: dict[str, str]) -> dict[str, str]:
    """Every param of sql: as given, else '', which is what Datasette sends
    for a box left empty (so a query defaults with nullif(:p, ''))."""
    return {p: given.get(p, "") for p in params(sql)}


def run(name: str, given: dict[str, str], path: Path | None = None, meta: Path = META
        ) -> tuple[list[str], list[tuple[Any, ...]]]:
    """A saved query's columns and rows, run as Datasette runs it."""
    sql = queries(meta)[name]["sql"]
    return query(sql, run_params(sql, given), path)


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
    import tempfile
    from ffcore.tidy import store_sql, tables_in

    with tempfile.TemporaryDirectory() as tmp:
        meta = Path(tmp) / "datasette.json"
        meta.write_text(json.dumps({"databases": {"lfg": {"queries": {
            "big": {"title": "runs over a size",
                    "sql": "SELECT run_id FROM runs WHERE peak_mb > coalesce(nullif(:mb, ''), 0)"
                           " ORDER BY run_id"}}}}}))
        db = Path(tmp) / "lfg.db"
        with closing(connect(db)) as c, c:
            c.executemany("INSERT INTO runs (started, as_of, trigger, commit_sha, peak_mb)"
                          " VALUES ('t', 't', 'service', 'a', ?)", [(100.0,), (300.0,)])
        assert queries(meta) == {"big": queries(meta)["big"]} and "sql" in queries(meta)["big"]
        assert run("big", {"mb": "200"}, db, meta) == (["run_id"], [(2,)])
        assert run("big", {}, db, meta) == (["run_id"], [(1,), (2,)])
        empty = sqlite3.connect(":memory:").execute("SELECT :p", run_params("SELECT :p", {}))
        assert empty.fetchone() == ("",), "a param not given is '', as Datasette sends it"
    with tempfile.TemporaryDirectory() as tmp2, tables_in(Path(tmp2)):
        row = {"observed_at": "2026-01-01T0000Z"}
        store_sql("api_activity", [dict(row, activity_id="1", at="", kind="", user_id="",
                                        counterparty="", player_id="")])
        store_sql("api_standings", [dict(row, user_id="", manager="")])
        store_sql("api_teams", [dict(row, manager="", player_id="", player_team_id="")])
        store_sql("market", [dict(row, ff_id="1", value="1")])
        store_sql("api_market", [dict(row, seller="", player_id="", expires_at="")])
        store_sql("api_offers", [dict(row, player_team_id="", offer_id="", created_at="",
                                      from_market="")])
        _compiles(Path(tmp2) / "lfg.db")
    print("q.py self-test OK")


def _compiles(path: Path) -> None:
    """Every saved query compiles against path (EXPLAIN), as Datasette runs it."""
    with closing(connect(path)) as c:
        for name, q in queries().items():
            try:
                c.execute("EXPLAIN " + q["sql"], run_params(q["sql"], {}))
            except Exception as e:
                raise AssertionError("datasette.json query %s: %s" % (name, e)) from e


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        _selftest()
        raise SystemExit(0)
    raise SystemExit(main(sys.argv[1:]))
