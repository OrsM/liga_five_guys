"""
ledger.py — rebuild data/tidy/transactions.csv from the app's activity feed.

    python src/ledger.py            # show what would change
    python src/ledger.py --write    # write it
    python src/ledger.py --selftest

The ledger was the one input a human had to remember to update.
On 2026-08-17 it was three days behind and the report offered a 63.29M budget
against a real 23.60M. It was never wrong, only late — which for a decision
system is the same thing.

WHAT IS LOST: the feed names one side of a deal (`user1Id`) and nothing else,
and a manager-to-manager transfer is not a paired buy and sell — verified, no
two of 57 rows share a player and a moment. So every buy is written as coming
from the pool and every sale as going to it: exact for ownership, prices and
premiums; lossy only for who dealt with whom.

Still committed, deliberately — the per-run diff is a better audit trail than
a file retyped from memory.
"""

from __future__ import annotations

import csv
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ffcore.league import ledger_from_api  # noqa: E402
from ffcore.tidy import (LEDGER, load_api_activity,  # noqa: E402
                         load_api_players, load_api_standings)

FIELDS = ["date", "player", "player_id", "from", "to", "price", "note"]

HEADER = """\
# GENERATED — do not hand-edit. Rebuilt from the app's activity feed by
# src/ledger.py on every run; anything typed here is overwritten.
#
# This was a hand-maintained file until 2026-08-18. It fell three days behind
# and the report offered a budget 39.69M larger than the real one, so it is
# derived now. Git history is the audit trail.
#
# `from`/`to` name the pool as one side of every deal: the feed reports only
# the manager who acted, and a manager-to-manager transfer does not appear as
# a paired buy and sell, so the counterparty genuinely cannot be recovered.
# Ownership, prices and premiums are unaffected.
"""


def user_map() -> dict:
    """{app user id: manager handle}, from the league table.

    A team's id is a fact about the team, so it comes off api_standings —
    five rows — rather than off seventy-six player rows carrying the same
    five pairs.
    """
    return {r["user_id"]: r["manager"] for r in load_api_standings()
            if r.get("user_id") and r.get("manager")}


def build() -> list[dict]:
    return ledger_from_api(load_api_activity(), user_map(), load_api_players())


def render(rows: list[dict]) -> str:
    buf = io.StringIO()
    buf.write(HEADER)
    w = csv.DictWriter(buf, fieldnames=FIELDS, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def existing(path=LEDGER) -> list[dict]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as fh:
        return [r for r in csv.DictReader(
            ln for ln in fh if not ln.lstrip().startswith("#"))
            if r.get("date")]


def write(rows: list[dict], force: bool = False, path=LEDGER) -> str:
    """Replace the ledger, or refuse and say why.

    THE GUARD IS THE POINT. An empty feed is indistinguishable from a feed we
    could not read — an expired token, a 500, a sweep that skipped the API —
    and writing it would delete the entire transaction history of the season
    in a file that is then committed. So a build that produces fewer rows than
    the file already holds is refused unless asked twice.
    """
    had = len(existing(path))
    if not rows:
        return "REFUSED: the feed produced no rows at all — nothing written."
    if len(rows) < had and not force:
        return ("REFUSED: would shrink the ledger from %d rows to %d. "
                "That is what a failed fetch looks like. Re-run with --force "
                "if the shrink is real." % (had, len(rows)))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(rows), encoding="utf-8")
    return "wrote %d rows to %s (was %d)" % (len(rows), path, had)


def _selftest() -> None:
    rows = [{"date": "2026-08-15T22:24", "player": "Fornals",
             "player_id": "1337", "from": "market", "to": "me",
             "price": "1", "note": "from the app"}]
    body = render(rows)
    assert body.startswith("# GENERATED"), body[:40]
    assert "date,player,player_id,from,to,price,note" in body, body
    assert "2026-08-15T22:24,Fornals,1337,market,me,1,from the app" in body, body
    # A row from before the feed carried ids still writes, with the column
    # blank — git history holds hand-typed rows and they are not rewritten.
    old_row = [{"date": "2026-08-01T10:00", "player": "Someone",
                "from": "market", "to": "me", "price": "2",
                "note": "typed"}]
    assert "2026-08-01T10:00,Someone,,market,me,2,typed" in render(old_row)

    # The header must be comment-only, so the existing reader (which strips
    # '#' lines) sees exactly the same shape it always did.
    assert all(ln.startswith("#") for ln in HEADER.splitlines()), HEADER

    # -- the guard ---------------------------------------------------------
    # Against a real file in a temp directory, and by ARGUMENT rather than by
    # monkeypatching the path resolver: the guard is about what is on disk, so
    # a test that fakes the disk is testing something else. This used to swap
    # out ffcore.tidy.input_path and set an FF_INPUTS nothing ever read.
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "transactions.csv"
        p.write_text(render(rows * 3))
        assert len(existing(p)) == 3, existing(p)
        # An empty build never writes, whatever else is true.
        msg = write([], path=p)
        assert msg.startswith("REFUSED") and "no rows" in msg, msg
        assert len(existing(p)) == 3, "an empty build wrote anyway"
        # A shrink is refused by default…
        msg = write(rows, path=p)
        assert msg.startswith("REFUSED") and "shrink" in msg, msg
        assert len(existing(p)) == 3, "a shrink wrote anyway"
        # …and allowed when asked twice.
        msg = write(rows, force=True, path=p)
        assert msg.startswith("wrote 1 rows"), msg
        assert len(existing(p)) == 1, existing(p)
        # Growth is the normal case and needs no flag.
        assert write(rows * 5, path=p).startswith("wrote 5 rows")
        # A ledger that has never been written is not a shrink.
        fresh = Path(d) / "new" / "transactions.csv"
        assert existing(fresh) == []
        assert write(rows, path=fresh).startswith("wrote 1 rows")
        assert len(existing(fresh)) == 1

    print("ledger.py self-test OK (15 cases)")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        raise SystemExit(0)
    built = build()
    if "--write" in sys.argv:
        print(write(built, force="--force" in sys.argv))
    else:
        have = len(existing())
        print("feed would produce %d ledger rows; the file has %d."
              % (len(built), have))
        print("Run with --write to replace it.")
