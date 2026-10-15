"""The line-up reminder: a run REMIND_HOURS before each jornada locks, and a
message only if the line-up set in the app is not your best eleven. The
views decide (schema.sql: lock_due, lineup_alert); this reads them for the
shell (lfg-lock, lfg-run).

    python src/remind.py due     # exit 0 if a reminder run is due, else 1
    python src/remind.py text    # what to tell you, empty if the line-up is fine
"""
from __future__ import annotations

import sys

from ffcore.db import rows

__all__ = ["REMIND_HOURS", "due", "text"]

REMIND_HOURS = 3.0


def due() -> str | None:
    """The lock (a stamp) a reminder run is due for: it is within
    REMIND_HOURS and no run has been asked for it yet."""
    got = rows("SELECT lock FROM lock_due")
    return got[0][0] if got else None


def text() -> str:
    """What to tell you about your line-up, as of the last run; empty if
    the one set in the app is your best eleven."""
    got = rows("SELECT text FROM lineup_alert")
    return got[0][0] if got else ""


def _selftest() -> None:
    import tempfile
    from datetime import datetime, timezone
    from pathlib import Path

    from ffcore.clock import set_now
    from ffcore.db import connect, record
    from ffcore.fixtures import lineup_board
    from ffcore.tidy import store_sql, tables_in

    def at(day: int, h: int, m: int = 0) -> datetime:
        return datetime(2026, 10, day, h, m, tzinfo=timezone.utc)

    def world(app: list[tuple[str, str]], ids: dict[str, str]) -> None:
        lineup_board(app, ids)
        record([{"name": "REMIND_HOURS", "value": REMIND_HOURS}], "model_constants")
        store_sql("matches", [
            {"observed_at": "2026-10-01T0000Z", "match_id": "m4", "jornada": "4", "home": "a",
             "away": "b", "kickoff": "2026-10-09T21:00:00+02:00"},
            {"observed_at": "2026-10-01T0000Z", "match_id": "m5", "jornada": "5", "home": "c",
             "away": "d", "kickoff": "2026-10-17T21:00:00+02:00"},
            {"observed_at": "2026-10-01T0000Z", "match_id": "m5b", "jornada": "5", "home": "e",
             "away": "f", "kickoff": "2026-10-16T21:00:00+02:00"}])

    fine = [("1070", "Ionut Radu"), ("2464", "Pepelu")]
    ids = {"ionut radu": "1070", "pepelu": "2464"}
    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        try:
            world(fine, ids)                       # jornada 5 locks 10-16 19:00 UTC, with its first match
            set_now(at(16, 15))
            assert due() is None, "four hours to the lock: not yet"
            set_now(at(16, 16, 30))
            assert due() == "2026-10-16T1900Z", "inside the window: a reminder run is due"
            assert text() == "", "the line-up set is the best eleven: nothing to say"
            with connect() as db:
                db.execute("INSERT INTO runs (started, as_of, trigger, commit_sha)"
                           " VALUES ('t', '2026-10-16T1631Z', 'reminder', 'c')")
            assert due() is None, "a run has been asked for this lock: once"
            set_now(at(16, 19, 30))
            assert due() is None, "the lock has passed"
        finally:
            set_now(None)

    cases = [
        (fine, ids, ""),
        ([("1070", "Ionut Radu"), ("77", "Bench Boy")], {"ionut radu": "1070"},
         "Change your line-up: field Pepelu for Bench Boy — locks in 2h30"),
        ([], {}, "Set your line-up: Ionut Radu, Pepelu — locks in 2h30"),
    ]
    for app, app_ids, want in cases:
        with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
            try:
                world(app, app_ids)
                set_now(at(16, 16, 30))
                assert text() == want, (text(), want)
            finally:
                set_now(None)

    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        try:
            world(fine, ids)
            with connect() as db:      # an earlier jornada's reminder does not count for this one
                db.execute("INSERT INTO runs (started, as_of, trigger, commit_sha)"
                           " VALUES ('t', '2026-10-09T1700Z', 'reminder', 'c')")
            set_now(at(16, 16, 30))
            assert due() == "2026-10-16T1900Z"
        finally:
            set_now(None)
    print("remind self-test OK")


def main(argv: list[str]) -> int:
    if argv == ["due"]:
        lock = due()
        print(lock or "")
        return 0 if lock else 1
    if argv == ["text"]:
        print(text())
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        _selftest()
        raise SystemExit(0)
    raise SystemExit(main(sys.argv[1:]))
