"""Players you hold back from the plan (the page's "not this one"): the decision neither buys nor sells them until
you let go. The phone leaves the whole set in hold.json beside the board; lfg-watch applies it here
(python src/hold.py FILE) and re-plans."""
from __future__ import annotations

import json
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from ffcore.db import connect

__all__ = ["apply"]


def apply(db: sqlite3.Connection, keys: list[str], at: str) -> list[str]:
    """The hold table is exactly keys (those held before keep their stamp); returns them sorted."""
    keep = sorted(set(keys))
    db.execute("DELETE FROM hold WHERE player NOT IN (SELECT value FROM json_each(?))", (json.dumps(keep),))
    db.executemany("INSERT OR IGNORE INTO hold (player, since) VALUES (?, ?)", [(k, at) for k in keep])
    return keep


def main(path: str) -> None:
    keys = [str(k) for k in json.loads(Path(path).read_text(encoding="utf-8")).get("hold", [])]
    with closing(connect()) as db, db:
        got = apply(db, keys, datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    print("hold: %s" % (", ".join(got) or "none"))


def _selftest() -> None:
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE hold (player TEXT PRIMARY KEY, since TEXT NOT NULL)")
    assert apply(db, ["b", "a", "a"], "t1") == ["a", "b"]
    assert apply(db, ["b", "c"], "t2") == ["b", "c"]
    assert db.execute("SELECT player, since FROM hold ORDER BY player").fetchall() == [("b", "t1"), ("c", "t2")], \
        "a player let go is gone, one still held keeps his stamp, a new one gets this one"
    assert apply(db, [], "t3") == [] and db.execute("SELECT count(*) FROM hold").fetchone() == (0,), "let go of all"
    print("hold self-test OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        main(sys.argv[1])
