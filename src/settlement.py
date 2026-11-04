"""Settlement (docs/notes/simplify.md, module 4): a plan and the auction
closes make moves(path, at, kind, player, amount). The rule is one SQL view,
settlement_moves (ffcore/schema.sql); this file is its test, which seeds
rows."""
from __future__ import annotations


def _selftest() -> None:
    import tempfile
    from contextlib import closing
    from pathlib import Path

    from ffcore.db import connect, rows, set_now
    from ffcore.parse import snapshot_stamp
    from ffcore.tidy import store_sql, tables_in

    run = at = "2026-09-02T1000Z"
    close = "2026-09-02T22:24:00+02:00"      # 2026-09-02T2024Z
    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        # Listings 7, 8, 9 close; you win 7 and 9, a rival 8. 10 closes a week later.
        store_sql("api_market", [{"observed_at": at, "seller": "marketPlayerLeague", "player_id": i,
                                  "expires_at": close, "sale_price": "10000000"} for i in "789"] +
                                [{"observed_at": at, "seller": "marketPlayerLeague", "player_id": "10",
                                  "expires_at": "2026-09-09T22:24:00+02:00", "sale_price": "10000000"}])
        store_sql("api_standings", [{"observed_at": at, "user_id": "1", "manager": "me"},
                                    {"observed_at": at, "user_id": "2", "manager": "riv"}])
        store_sql("api_activity", [{"observed_at": at, "activity_id": i, "kind": "buy", "user_id": u, "player_id": p,
                                    "at": "2026-09-02T22:24:10+02:00", "amount": a, "counterparty": ""}
                                   for i, u, p, a in [("1", "1", "7", "10500000"), ("2", "2", "8", "12000000"),
                                                      ("3", "1", "9", "11000000")]])
        with closing(connect()) as db, db:
            db.execute("INSERT INTO league_config VALUES ('me', 100.0)")
            db.executemany("INSERT INTO crosswalk (player_id, app_id) VALUES (?, ?)",
                           [("won", "7"), ("lost", "8"), ("also won", "9"), ("waits", "10")])
            db.execute("INSERT INTO runs (run_id, started, as_of, trigger, commit_sha) VALUES (1, 't', ?, 'test', 'c')",
                       (run,))
            # The plan: a swap that wins, a swap that loses (maffeo is sold to pay for it), a lone buy that wins,
            # a swap whose auction has not closed, and a lone sale.
            db.executemany("INSERT INTO decision_plan (run_id, step, kind, player, amount, win, worth)"
                           " VALUES (1, ?, ?, ?, ?, 1, 0)",
                           [(0, "buy", "won", 10e6), (0, "sell", "a", 4e6),
                            (1, "buy", "lost", 10e6), (1, "sell", "maffeo", 5e6),
                            (2, "buy", "also won", 11e6),
                            (3, "buy", "waits", 10e6), (3, "sell", "d", 3e6),
                            (4, "sell", "lone", 2e6)])
        try:
            set_now(snapshot_stamp("2026-09-03T0800Z"))
            got = rows("SELECT path, at, kind, player, amount FROM settlement_moves ORDER BY at, kind, player")
        finally:
            set_now(None)
    p, c = "run 1", "2026-09-02T2024Z"
    assert got == [(p, run, "sell", "lone", 2e6),
                   (p, c, "buy", "also won", 11e6), (p, c, "buy", "won", 10.5e6), (p, c, "sell", "a", 4e6)], \
        "won bids make their buys and their own paired sale; a lost bid (maffeo's) makes no sale, nor a bid " \
        "not yet closed (d's); a lone sale is made at once: %s" % got
    print("settlement self-test OK")


if __name__ == "__main__":
    _selftest()
