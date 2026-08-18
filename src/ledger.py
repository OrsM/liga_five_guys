"""
ledger.py — rebuild inputs/transactions.csv from the app's activity feed.

    python src/ledger.py            # show what would change
    python src/ledger.py --write    # write it
    python src/ledger.py --selftest

`inputs/transactions.csv` was the one input a human had to remember to update.
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
from ffcore.tidy import (input_path, load_api_activity,  # noqa: E402
                         load_api_players, load_api_teams)

FIELDS = ["date", "player", "from", "to", "price", "note"]

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
    """{app user id: manager handle}, from the squad feed."""
    return {r["user_id"]: r["manager"] for r in load_api_teams()
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


def existing() -> list[dict]:
    path = input_path("transactions.csv")
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as fh:
        return [r for r in csv.DictReader(
            ln for ln in fh if not ln.lstrip().startswith("#"))
            if r.get("date")]


def write(rows: list[dict], force: bool = False) -> str:
    """Replace the ledger, or refuse and say why.

    THE GUARD IS THE POINT. An empty feed is indistinguishable from a feed we
    could not read — an expired token, a 500, a sweep that skipped the API —
    and writing it would delete the entire transaction history of the season
    in a file that is then committed. So a build that produces fewer rows than
    the file already holds is refused unless asked twice.
    """
    path = input_path("transactions.csv")
    had = len(existing())
    if not rows:
        return "REFUSED: the feed produced no rows at all — nothing written."
    if len(rows) < had and not force:
        return ("REFUSED: would shrink the ledger from %d rows to %d. "
                "That is what a failed fetch looks like. Re-run with --force "
                "if the shrink is real." % (had, len(rows)))
    path.write_text(render(rows), encoding="utf-8")
    return "wrote %d rows to %s (was %d)" % (len(rows), path, had)


def _selftest() -> None:
    rows = [{"date": "2026-08-15T22:24", "player": "Fornals",
             "from": "market", "to": "me", "price": "1", "note": "from the app"}]
    body = render(rows)
    assert body.startswith("# GENERATED"), body[:40]
    assert "date,player,from,to,price,note" in body, body
    assert "2026-08-15T22:24,Fornals,market,me,1,from the app" in body, body

    # The header must be comment-only, so the existing reader (which strips
    # '#' lines) sees exactly the same shape it always did.
    assert all(ln.startswith("#") for ln in HEADER.splitlines()), HEADER

    # -- the guard ---------------------------------------------------------
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as d:
        os.environ["FF_INPUTS"] = d
        p = Path(d) / "transactions.csv"
        p.write_text(render(rows * 3))

        import ffcore.tidy as tidy
        real = tidy.input_path
        tidy.input_path = lambda n: Path(d) / n
        globals()["input_path"] = tidy.input_path
        try:
            assert len(existing()) == 3, existing()
            # An empty build never writes, whatever else is true.
            msg = write([])
            assert msg.startswith("REFUSED") and "no rows" in msg, msg
            assert len(existing()) == 3, "an empty build wrote anyway"
            # A shrink is refused by default…
            msg = write(rows)
            assert msg.startswith("REFUSED") and "shrink" in msg, msg
            assert len(existing()) == 3, "a shrink wrote anyway"
            # …and allowed when asked twice.
            msg = write(rows, force=True)
            assert msg.startswith("wrote 1 rows"), msg
            assert len(existing()) == 1, existing()
            # Growth is the normal case and needs no flag.
            assert write(rows * 5).startswith("wrote 5 rows")
        finally:
            tidy.input_path = real
            globals()["input_path"] = real
            os.environ.pop("FF_INPUTS", None)

    print("ledger.py self-test OK (12 cases)")


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
