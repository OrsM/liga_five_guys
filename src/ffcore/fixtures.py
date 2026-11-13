"""Rows seeded by hand for the self-tests: a league at t as board.built writes it
(seed_league)."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

__all__ = ["seed_league"]

PLAYER_COLUMNS = ("name", "pos", "owner", "value", "trend", "paid", "offer", "clause", "price", "route",
                  "my_bid")


def seed_league(me: str, cash: float, players: Mapping[str, Mapping[str, Any]],
                cells: Mapping[int, Mapping[str, tuple[float, float]]],
                odds: Mapping[str, Any] | None = None) -> None:
    """Into the current tables (tidy.tables_in), a league at t as board.built
    writes it: seen (a row a player, PLAYER_COLUMNS; name defaults to his
    key, your squad is those he owns by `me`), your cash (seen_cash),
    forecast_cell at moment 0 (cells: jornada -> player -> (points if he
    plays, chance he plays)), clearing-price curves (player -> ((ratio, p), ...)),
    and look on them."""
    from contextlib import closing

    from ffcore.db import connect, set_look
    from ffcore.forecast_store import seed_cells
    from ffcore.tidy import store_sql

    set_look(moment=0, me=me)
    with closing(connect()) as db, db:
        for t in ("seen", "seen_cash", "seen_auction", "seen_curve"):
            db.execute("DELETE FROM %s WHERE cut = (SELECT cut()) AND variant = variant()" % t)
        db.executemany("INSERT INTO seen VALUES ((SELECT cut()), variant(), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '')",
                       [(k, p.get("name", k), *(p.get(c) for c in PLAYER_COLUMNS[1:])) for k, p in sorted(players.items())])
        db.execute("INSERT INTO seen_cash VALUES ((SELECT cut()), variant(), ?, ?)", (me, cash))
        db.executemany("INSERT INTO seen_curve VALUES ((SELECT cut()), variant(), ?, ?, ?)",
                       [(k, r, p) for k, o in (odds or {}).items() for r, p in o])
        seed_cells(db, 0, [(k, j, pts, p) for j, per in cells.items() for k, (pts, p) in per.items()])
        # the ingest facts under them, as the app and the market page give them: a position, a squad
        db.executemany("INSERT OR REPLACE INTO crosswalk (player_id, app_id) VALUES (?, ?)", [(k, k) for k in players])
    store_sql("market", [{"observed_at": "2026-08-01T0000Z", "source": "test", "ff_id": k, "name": p.get("name", k),
                          "position": p.get("pos") or ""} for k, p in players.items()])
    store_sql("api_teams", [{"observed_at": "2026-08-01T0000Z", "source": "test", "player_id": k, "manager": p["owner"]}
                            for k, p in players.items() if p.get("owner")])


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
