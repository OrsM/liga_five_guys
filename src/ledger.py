
from __future__ import annotations

import sys


from ffcore.league import ledger_from_api
from ffcore.tidy import (LEDGER, csv_string, load_api, load_api_activity,
                         load_api_players, read_csv)

FIELDS = ["date", "player", "player_id", "from", "to", "price", "note"]

def build() -> list[dict]:
    users = {r["user_id"]: r["manager"] for r in load_api("standings")
            if r.get("user_id") and r.get("manager")}
    return ledger_from_api(load_api_activity(), users, load_api_players())


def existing(path=LEDGER) -> list[dict]:
    return [r for r in read_csv(path) if r.get("date")]


def write(rows: list[dict], force: bool = False, path=LEDGER) -> str:
    had = len(existing(path))
    if not rows:
        return "REFUSED: the feed produced no rows at all — nothing written."
    if len(rows) < had and not force:
        return ("REFUSED: would shrink the ledger from %d rows to %d. "
                "That is what a failed fetch looks like. Re-run with --force "
                "if the shrink is real." % (had, len(rows)))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(csv_string(rows, FIELDS), encoding="utf-8")
    return "wrote %d rows to %s (was %d)" % (len(rows), path, had)


def _selftest() -> None:
    rows = [{"date": "2026-08-15T22:24", "player": "Fornals",
             "player_id": "1337", "from": "market", "to": "me",
             "price": "1", "note": "from the app"}]
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "transactions.csv"
        p.write_text(csv_string(rows * 3, FIELDS))
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

    print("ledger.py self-test OK (11 cases)")


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
