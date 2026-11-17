"""The decision's snapshot of the league at t: the market and the league as seen at a cut
(docs/notes/lfg.md, "Architecture")."""
from __future__ import annotations

from contextlib import closing

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
