"""Rows seeded by hand for the self-tests: a league at t as assemble writes it
(seed_league), the board's line-up inputs (lineup_board)."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

__all__ = ["seed_league", "lineup_board"]

PLAYER_COLUMNS = ("name", "pos", "owner", "value", "trend", "paid", "offer", "clause", "price", "route",
                  "my_bid", "fetches")


def seed_league(me: str, cash: float, players: Mapping[str, Mapping[str, Any]],
                cells: Mapping[int, Mapping[str, tuple[float, float]]],
                odds: Mapping[str, Any] | None = None) -> None:
    """Into the current tables (tidy.tables_in), a league at t as assemble
    writes it: seen (a row a player, PLAYER_COLUMNS; name defaults to his
    key, your squad is those he owns by `me`), your cash (seen_cash),
    forecast_cell at moment 0 (cells: jornada -> player -> (points if he
    plays, chance he plays)), bid odds (player -> ((ratio, p_win), ...)),
    and look on them, with no path."""
    from contextlib import closing

    from assemble import write_constants
    from ffcore.db import connect, set_look

    write_constants()   # the model's constants the views read, as a forecast writes them
    set_look(moment=0, me=me, path="", t0="")
    with closing(connect()) as db, db:
        for t in ("seen", "seen_cash", "seen_odds"):
            db.execute("DELETE FROM %s WHERE cut = (SELECT cut()) AND variant = variant()" % t)
        db.execute("DELETE FROM forecast_cell WHERE moment = 0")
        db.executemany("INSERT INTO seen VALUES ((SELECT cut()), variant(), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '')",
                       [(k, p.get("name", k), *(p.get(c) for c in PLAYER_COLUMNS[1:])) for k, p in sorted(players.items())])
        db.execute("INSERT INTO seen_cash VALUES ((SELECT cut()), variant(), ?, ?)", (me, cash))
        db.executemany("INSERT INTO seen_odds VALUES ((SELECT cut()), variant(), ?, ?, ?)",
                       [(k, r, p) for k, o in (odds or {}).items() for r, p in o])
        db.executemany("INSERT INTO forecast_cell (moment, player, jornada, pts, p) VALUES (0, ?, ?, ?, ?)",
                       [(k, j, pts, p) for j, per in cells.items() for k, (pts, p) in per.items()])


def lineup_board(app: list[tuple[str, str]], ids: dict[str, str]) -> None:
    """Into the current tables (tidy.tables_in), the rows the board views
    read: one run whose best eleven now is ionut radu and pepelu (cai coro,
    better, is on the market; bench boy is yours and not in it); and the
    app's line-up as (app id, name) rows; the registry holds every player,
    these with their app ids."""
    from contextlib import closing

    from ffcore.db import connect
    from ffcore.tidy import store_sql

    with closing(connect()) as db, db:
        db.execute("INSERT INTO runs (run_id, started, as_of, trigger, commit_sha)"
                   " VALUES (1, 't', 't', 'test', 'c')")
        db.execute("INSERT INTO sim_runs (run_id, cut) VALUES (1, 't')")
        db.executemany("INSERT INTO sim_players (run_id, player, mine, xi, next)"
                       " VALUES (1, ?, ?, ?, ?)",
                       [("ionut radu", 1, 1, 3.0), ("pepelu", 1, 1, 2.0),
                        ("cai coro", 0, 0, 5.0), ("bench boy", 1, 0, 0.5)])
        db.executemany("INSERT INTO seen (cut, variant, player, name) VALUES ('t', 'live', ?, ?)",
                       [("ionut radu", "Ionut Radu"), ("pepelu", "Pepelu"),
                        ("cai coro", "Cai Coro"), ("bench boy", "Bench Boy")])
        db.executemany("INSERT INTO crosswalk (player_id, name, app_id) VALUES (?, ?, ?)",
                       [(k, k, ids.get(k, "")) for k in dict.fromkeys(
                           ["ionut radu", "pepelu", "cai coro", "bench boy", *ids])])
    store_sql("api_lineup", [{"observed_at": "2026-08-01T0000Z", "player_id": i,
                              "player_name": n, "player_name_full": n} for i, n in app])


def _selftest() -> None:
    import tempfile
    from pathlib import Path

    from ffcore.db import rows
    from ffcore.tidy import tables_in

    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        seed_league("me", 7e6, {"a": {"pos": "POR", "owner": "me", "value": 1e6}, "b": {"pos": "DEF", "price": 2e6}},
                    {1: {"a": (5.0, 0.8)}}, odds={"b": ((1.0, 0.5),)})
        assert rows("SELECT player, name, owner, price FROM seen ORDER BY player") == [
            ("a", "a", "me", None), ("b", "b", None, 2e6)]
        assert rows("SELECT player FROM my_squad") == [("a",)] and rows("SELECT cash FROM my_cash") == [(7e6,)]
        assert rows("SELECT player, jornada, pts, p FROM forecast_cell") == [("a", 1, 5.0, 0.8)]
    print("ffcore.fixtures self-test OK")


if __name__ == "__main__":
    _selftest()
