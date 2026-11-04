"""Settlement (docs/notes/simplify.md, module 4): a plan and the auction
closes make moves(path, at, kind, player, amount). The rule is one SQL view,
settlement_moves (ffcore/schema.sql); this file is its test, which seeds
rows."""
from __future__ import annotations

from typing import Any


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
        # the feed is seen after the close: only then is an auction's result known
        store_sql("api_activity", [{"observed_at": "2026-09-02T2100Z", "activity_id": i, "kind": "buy", "user_id": u, "player_id": p,
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


def _selftest_search() -> None:
    """The contract against a plan the real decision search makes (decision.plan on a seeded league), not
    plan rows typed by hand: a swap won settles as its buy at the price paid and its own sale at the close;
    the same swap lost settles to nothing; in debt with no bid the sale is made at once."""
    import tempfile
    from contextlib import closing
    from pathlib import Path

    import decision
    from ffcore.db import connect, rows, set_now
    from ffcore.fixtures import seed_league
    from ffcore.parse import snapshot_stamp
    from ffcore.tidy import store_sql, tables_in

    squad = {"k": "POR", "d1": "DEF", "d2": "DEF", "d3": "DEF", "m1": "MED", "m2": "MED", "m3": "MED",
             "m4": "MED", "f1": "DEL", "f2": "DEL", "f3": "DEL", "bench": "DEF"}
    e = {**{k: 4.0 for k in squad}, "m4": 2.0, "bench": 1.0}
    as_of, close, stamp = "2026-09-02T1000Z", "2026-09-02T22:24:00+02:00", "2026-09-02T2024Z"

    def moves(cash: float, price: dict[str, float], won: str | None) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
        """The real plan on a league holding `cash` with `price` asked on the market, and the moves it settles to
        once its auction closes (won: 'me', 'riv', or None: the feed has not told)."""
        with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
            set_now(snapshot_stamp(as_of))
            try:
                seed_league("me", cash,
                            {**{k: {"pos": p, "owner": "me", "value": 5e6} for k, p in squad.items()},
                             **{k: {"pos": "MED", "price": a} for k, a in price.items()}},
                            {j: {**{k: (x, 1.0) for k, x in e.items()}, **{k: (8.0, 1.0) for k in price}} for j in (1, 2)})
                steps = decision.plan()
                store_sql("api_market", [{"observed_at": as_of, "seller": "marketPlayerLeague", "player_id": k,
                                          "expires_at": close, "sale_price": str(int(a))} for k, a in price.items()])
                store_sql("api_standings", [{"observed_at": as_of, "user_id": "1", "manager": "me"},
                                            {"observed_at": as_of, "user_id": "2", "manager": "riv"}])
                if won:
                    store_sql("api_activity", [{"observed_at": "2026-09-02T2100Z", "activity_id": "1", "kind": "buy",
                                                "user_id": "1" if won == "me" else "2", "player_id": k,
                                                "at": "2026-09-02T22:24:10+02:00", "amount": str(int(a) + 500000),
                                                "counterparty": ""} for k, a in price.items()])
                with closing(connect()) as db, db:
                    db.execute("INSERT INTO league_config VALUES ('me', 100.0)")
                    db.execute("INSERT INTO runs (run_id, started, as_of, trigger, commit_sha) VALUES (1, 't', ?, 'test', 'c')",
                               (as_of,))
                    db.executemany("INSERT INTO decision_plan (run_id, step, kind, player, amount, win, worth)"
                                   " VALUES (1, ?, ?, ?, ?, ?, ?)", steps)
                set_now(snapshot_stamp("2026-09-03T0800Z"))
                got = rows("SELECT path, at, kind, player, amount FROM settlement_moves ORDER BY at, kind, player")
            finally:
                set_now(None)
        return steps, got

    # 4M short of a bid of 8M: the search pays for him with a sale of one it leaves out of its eleven
    steps, got = moves(4e6, {"star": 8e6}, "me")
    (sale,) = [k for _, kind, k, *_ in steps if kind == "sell"]
    assert [(kind, k) for _, kind, k, *_ in steps] == [("buy", "star"), ("sell", sale)] and sale in ("bench", "m4"), steps
    assert got == [("run 1", stamp, "buy", "star", 8.5e6), ("run 1", stamp, "sell", sale, 5e6)], \
        "won: the buy at the price paid, its own sale at the close: %s" % got
    assert moves(4e6, {"star": 8e6}, "riv")[1] == [], "lost: no buy, and no sale (the plan's sale paid for a bid not won)"
    assert moves(4e6, {"star": 8e6}, None)[1] == [], "not closed (nor told): nothing yet"
    steps, got = moves(-3e6, {}, None)
    assert [(kind, k) for _, kind, k, *_ in steps] == [("sell", "bench")] and got == [("run 1", as_of, "sell", "bench", 5e6)], \
        "in debt, no bid: the lone sale is made at the run's own time: %s" % got


if __name__ == "__main__":
    _selftest()
    _selftest_search()
