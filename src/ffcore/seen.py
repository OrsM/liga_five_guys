"""The decision's snapshot of the league at t: the market and the league as seen at a cut
(docs/notes/lfg.md, "Architecture")."""
from __future__ import annotations

from contextlib import closing
from pathlib import Path

from ffcore import auction
from ffcore.db import connect, cut

__all__ = ["see"]


def see(key: str) -> str:
    """The market and the league as of t into seen, seen_cash and seen_auction / seen_curve (views player_facts,
    cash, auction_looks): what a decision at t reads. Made once a cut, variant and key (the
    caller's stamp of the SQL and constants it was made with)."""
    with closing(connect()) as db, db:
        c = cut()
        if db.execute("SELECT 1 FROM seen WHERE cut = ? AND variant = variant() AND code = ? LIMIT 1",
                      (c, key)).fetchone():
            return c
        for t in ("seen", "seen_cash", "seen_auction", "seen_curve"):
            db.execute("DELETE FROM %s WHERE cut = ? AND variant = variant()" % t, (c, ))
        db.execute("INSERT INTO seen SELECT ?, variant(), f.player, f.name, f.pos, f.owner, f.value, f.trend, f.paid,"
                   " f.offer, f.clause, f.price, f.route, f.my_bid, ?"
                   " FROM player_facts f", (c, key))
        db.execute("INSERT INTO seen_cash SELECT ?, variant(), manager, cash FROM cash", (c, ))
        auction.write(db, c)
    return c


def _selftest() -> None:
    import tempfile

    from ffcore.db import connect, record, rows, set_now
    from ffcore.parse import snapshot_stamp
    from ffcore.tidy import store_sql, tables_in

    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        try:
            set_now(snapshot_stamp("2026-09-03T0000Z"))
            record([{"me": "me", "budget": 100e6}], "league_config")
            record([{"player_id": "p1", "app_id": "1"}], "crosswalk")
            store_sql("market", [{"observed_at": "2026-09-02T1000Z", "ff_id": "p1", "name": "Ana",
                                  "position": "defensa", "value": "5000000"}])
            c = see("a")
            assert rows("SELECT player, value FROM seen") == [("p1", 5e6)], "the market as seen at the cut"
            with closing(connect()) as db, db:
                db.execute("UPDATE seen SET value = -1")
            assert see("a") == c and rows("SELECT value FROM seen") == [(-1,)], \
                "made once a cut and key: read, not made again"
            see("b")
            assert rows("SELECT value FROM seen") == [(5e6,)], "another key makes it again"
        finally:
            set_now(None)
    print("ffcore.seen self-test OK")


if __name__ == "__main__":
    _selftest()
