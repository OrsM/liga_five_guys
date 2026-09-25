
from __future__ import annotations

import csv
import io
import sys


from ffcore.league import ledger_from_api
from ffcore.tidy import (LEDGER, load_api, load_api_activity,
                         load_api_players)

FIELDS = ["date", "player", "player_id", "from", "to", "price", "note"]

HEADER = """\
# GENERATED — do not hand-edit. Rebuilt from the app's activity feed by
# src/ledger.py on every run; anything typed here is overwritten.
#
# `from`/`to` name the pool as one side of every deal — the counterparty in
# a manager-to-manager transfer cannot be recovered from the feed. Ownership,
# prices and premiums are unaffected.
"""


def user_map() -> dict:
    return {r["user_id"]: r["manager"] for r in load_api("standings")
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
    old_row = [{"date": "2026-08-01T10:00", "player": "Someone",
                "from": "market", "to": "me", "price": "2",
                "note": "typed"}]
    assert "2026-08-01T10:00,Someone,,market,me,2,typed" in render(old_row)

    assert all(ln.startswith("#") for ln in HEADER.splitlines()), HEADER

    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "transactions.csv"
        p.write_text(render(rows * 3))
        assert len(existing(p)) == 3, existing(p)
        msg = write([], path=p)
        assert msg.startswith("REFUSED") and "no rows" in msg, msg
        assert len(existing(p)) == 3, "an empty build wrote anyway"
        msg = write(rows, path=p)
        assert msg.startswith("REFUSED") and "shrink" in msg, msg
        assert len(existing(p)) == 3, "a shrink wrote anyway"
        msg = write(rows, force=True, path=p)
        assert msg.startswith("wrote 1 rows"), msg
        assert len(existing(p)) == 1, existing(p)
        assert write(rows * 5, path=p).startswith("wrote 5 rows")
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
