"""Players you hold back from the plan (the page's "not this one"): the decision neither buys nor sells them until
you let go. The phone leaves its changes in hold.json beside the board; lfg-watch applies them here
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


def apply(db: sqlite3.Connection, changes: list[dict[str, object]], at: str) -> list[str]:
    """The changes the page asked for, in order ({key, hold}: hold him back or let him go); the players held now, sorted.
    One already held keeps his stamp."""
    for c in changes:
        if c["hold"]:
            db.execute("INSERT OR IGNORE INTO hold (player, since) VALUES (?, ?)", (str(c["key"]), at))
        else:
            db.execute("DELETE FROM hold WHERE player = ?", (str(c["key"]),))
    return [k for k, in db.execute("SELECT player FROM hold ORDER BY player")]


def main(path: str) -> None:
    changes = json.loads(Path(path).read_text(encoding="utf-8")).get("changes", [])
    with closing(connect()) as db, db:
        got = apply(db, changes, datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    print("hold: %s" % (", ".join(got) or "none"))


def _selftest() -> None:
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE hold (player TEXT PRIMARY KEY, since TEXT NOT NULL)")
    on, off = (lambda k: {"key": k, "hold": True}), (lambda k: {"key": k, "hold": False})
    assert apply(db, [on("b"), on("a"), on("a")], "t1") == ["a", "b"]
    assert apply(db, [on("b"), on("c"), off("a")], "t2") == ["b", "c"]
    assert db.execute("SELECT player, since FROM hold ORDER BY player").fetchall() == [("b", "t1"), ("c", "t2")], \
        "a player let go is gone, one still held keeps his stamp, a new one gets this one"
    assert apply(db, [on("d"), off("d")], "t3") == ["b", "c"], "in the order asked: held, then let go"
    assert apply(db, [off("zzz")], "t4") == ["b", "c"], "letting go of one not held changes nothing"
    print("hold self-test OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        main(sys.argv[1])
