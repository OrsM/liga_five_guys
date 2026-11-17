"""Ask the database anything, read-only: raw src_* storage, its current_* views, the records and the crosswalk, as of any
moment. Derived facts are not here: the sweep computes them (ffcore/state.py).

    python tools/sql.py "SELECT * FROM parser_health"
    python tools/sql.py --at 2026-09-27T0934Z "SELECT count(*) FROM current_market"
    python tools/sql.py "SELECT player_id, name_key(name) FROM crosswalk LIMIT 5"

The same functions as Datasette (cut(), variant(), name_key(), name_shown(), stems_meet()); --at sets t (cut(),
this process's) to a past stamp, so every current_* view reads as it did then. Nothing can be written."""
from __future__ import annotations

import sqlite3
import sys
from contextlib import closing
from typing import Any

from ffcore.clock import set_variant
from ffcore.db import connect, set_now
from ffcore.parse import snapshot_stamp
from q import table


def ask(sql: str, at: str = "", variant: str = "live") -> tuple[list[str], list[tuple[Any, ...]]]:
    """sql's columns and rows, as of the stamp at (now if empty), under variant."""
    when = snapshot_stamp(at) if at else None
    if at and when is None:
        raise SystemExit("--at %r is not a stamp (2026-09-27T0934Z)" % at)
    set_variant(variant)
    try:
        with closing(connect()) as db:
            if when is not None:
                set_now(when)
            db.execute("PRAGMA query_only = 1")
            cur = db.execute(sql)
            return [d[0] for d in cur.description or ()], [tuple(r) for r in cur.fetchall()]
    finally:
        set_variant("live")
        set_now(None)


def main(argv: list[str]) -> int:
    at, variant, words = "", "live", []
    it = iter(argv)
    for a in it:
        if a == "--at":
            at = next(it, "")
        elif a == "--variant":
            variant = next(it, "live")
        else:
            words.append(a)
    if not words:
        print(__doc__)
        return 2
    try:
        cols, rows = ask(" ".join(words), at, variant)
    except sqlite3.Error as e:
        print("sql: %s" % e, file=sys.stderr)
        return 1
    print(table(cols, rows))
    return 0


def _selftest() -> None:
    import tempfile
    from pathlib import Path

    from ffcore.tidy import store_sql, tables_in

    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        store_sql("market", [{"observed_at": at, "ff_id": "p", "value": "1"}
                             for at in ("2026-09-01T1000Z", "2026-09-02T1000Z")])
        assert ask("SELECT count(*) FROM current_market")[1] == [(1,)]
        assert ask("SELECT observed_at FROM current_market", at="2026-09-01T1200Z")[1] == [("2026-09-01T1000Z",)], \
            "--at: the views as they stood then"
        assert ask("SELECT cut()")[1][0][0] != "2026-09-01T1200Z", "t is put back afterwards"
        for bad in ("DELETE FROM src_market", "CREATE TABLE x (a)", "INSERT INTO src_market (observed_at) VALUES ('t')"):
            try:
                ask(bad)
            except sqlite3.Error:
                continue
            raise AssertionError("must not write: " + bad)
        assert ask("SELECT variant()", variant="slow")[1] == [("slow",)]
        try:
            ask("SELECT 1", at="yesterday")
        except SystemExit:
            pass
        else:
            raise AssertionError("a bad stamp is refused")
    print("sql.py self-test OK")


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        _selftest()
        raise SystemExit(0)
    raise SystemExit(main(sys.argv[1:]))
