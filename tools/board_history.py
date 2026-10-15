"""Backfill the boards published before the board was kept as rows.

    python tools/board_history.py       # every reports/decisions.json in git's history

decisions.json has been committed with each run since 2026-08-18, in
several shapes. Only the shape with a "do" list (since 2026-09-27) says
the same thing throughout (what to do, to whom, for how many points), so
that is what is kept, in board_history / board_history_moves, one board a
generated_at. Earlier ones stay in git. Safe to run again.
"""
from __future__ import annotations

import json
import subprocess
import sys
from contextlib import closing
from typing import Any

sys.path.insert(0, "src")
from ffcore.db import connect  # noqa: E402

Doc = dict[str, Any]


def rows_of(doc: Doc, commit: str) -> tuple[Doc, list[Doc]] | None:
    """A board's head and moves as rows, or None for a shape that is not the
    "do" one."""
    if not isinstance(doc.get("do"), list) or not doc.get("generated_at"):
        return None
    at = doc["generated_at"]
    head = {"generated_at": at, "commit_sha": commit, "plan_gain": doc.get("plan_gain"),
            "finish": doc.get("finish"), "p_win": doc.get("p_win")}
    moves = [{"generated_at": at, "ord": i, "what": d["what"], "name": d.get("name"),
              "gain": d.get("gain")} for i, d in enumerate(doc["do"]) if d.get("what")]
    return head, moves


def main() -> int:
    shas = subprocess.run(["git", "log", "--format=%h", "--", "reports/decisions.json"],
                          capture_output=True, text=True, check=True).stdout.split()
    kept = 0
    with closing(connect()) as db, db:
        for sha in shas:
            shown = subprocess.run(["git", "show", "%s:reports/decisions.json" % sha],
                                   capture_output=True, text=True)
            try:
                got = rows_of(json.loads(shown.stdout), sha) if shown.returncode == 0 else None
            except json.JSONDecodeError:
                got = None
            if got is None:
                continue
            head, moves = got
            if db.execute("INSERT OR IGNORE INTO board_history VALUES (:generated_at, :commit_sha,"
                          " :plan_gain, :finish, :p_win)", head).rowcount:
                db.executemany("INSERT OR IGNORE INTO board_history_moves VALUES"
                               " (:generated_at, :ord, :what, :name, :gain)", moves)
                kept += 1
    print("board_history: %d boards added" % kept)
    return 0


def _selftest() -> None:
    new = {"generated_at": "2026-09-27T09:47Z", "plan_gain": 24.0, "finish": 1.9, "p_win": 0.2,
           "do": [{"what": "field", "gain": 1.1}, {"what": "buy", "name": "Dani Olmo", "gain": 25.7},
                  {"gain": 3.0}]}
    head, moves = rows_of(new, "abc") or ({}, [])
    assert head == {"generated_at": "2026-09-27T09:47Z", "commit_sha": "abc", "plan_gain": 24.0,
                    "finish": 1.9, "p_win": 0.2}, head
    assert [(m["ord"], m["what"], m["name"], m["gain"]) for m in moves] == [
        (0, "field", None, 1.1), (1, "buy", "Dani Olmo", 25.7)], "a row with no action is not a move"
    assert rows_of({"generated_at": "2026-08-18T01:43Z", "rows": []}, "x") is None, "the old shape"
    assert rows_of({"do": []}, "x") is None, "no time, no board"
    import tempfile
    from pathlib import Path

    from ffcore.tidy import tables_in
    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        with closing(connect()) as db, db:
            for _ in range(2):
                db.execute("INSERT OR IGNORE INTO board_history VALUES (:generated_at, :commit_sha,"
                           " :plan_gain, :finish, :p_win)", head)
                db.executemany("INSERT OR IGNORE INTO board_history_moves VALUES"
                               " (:generated_at, :ord, :what, :name, :gain)", moves)
            assert db.execute("SELECT count(*) FROM board_history").fetchone()[0] == 1
            assert db.execute("SELECT count(*) FROM board_history_moves").fetchone()[0] == 2, "again adds nothing"
    print("board_history self-test OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        raise SystemExit(0)
    raise SystemExit(main())
