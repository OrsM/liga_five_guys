"""The players table the decision reads: market and probable-XI rows merged
per player, and the crosswalk of ids between the sources."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from ffcore.parse import Rows
from ffcore.names import Name, row_key
from ffcore.tidy import LINEUP_SOURCE, Stamp, current, mtime_cached, table_path

from ffcore.crosswalk import Crosswalk
__all__ = ["load_crosswalk", "load_players", "MARKET_FIELDS", "XI_FIELDS"]


_XW_CACHE: dict[str, tuple[Stamp, Crosswalk]] = {}


def load_crosswalk() -> Crosswalk:
    """players.csv as a Crosswalk, empty if there is none yet."""
    path = table_path("players")
    return mtime_cached(path, _XW_CACHE, str(path), Crosswalk.read, path) or Crosswalk()


MARKET_FIELDS = [("team", "team"), ("club", "club"), ("pos", "position"),
                 ("value", "value"), ("delta_1d", "delta_1d")]


XI_FIELDS = [("club", "team_slug"), ("start", "start_pct"), ("status", "status")]


def _merge(players: dict[str, dict[str, Any]], rows: Rows,
           key_of: Callable[[Mapping[str, Any]], str | None], name_col: str,
           fields: list[tuple[str, str]]) -> dict[str, dict[str, Any]]:
    for r in rows:
        key = key_of(r)
        if not key:
            continue
        rec = players.setdefault(key, {})
        rec.setdefault("name", (r.get(name_col) or "").strip())
        for field, col in fields:
            val = r.get(col)
            if isinstance(val, str):
                val = val.strip()
            if field not in rec and val not in (None, ""):
                rec[field] = val
    return players


def load_players() -> dict[str, dict[str, Any]]:
    market, xi = current("market"), current("lineups", LINEUP_SOURCE)
    if not market and not xi:
        raise SystemExit("no rows in %s — run `ingest.py parse` first" % table_path("players").parent)
    xw = load_crosswalk()
    players = _merge({}, market, row_key, "name", MARKET_FIELDS)
    return _merge(players, xi, xw.key_of,
                  "player_name", XI_FIELDS)


def _selftest() -> None:
    import tempfile
    from pathlib import Path

    from ffcore.crosswalk import PLAYER_COLS
    from ffcore.tidy import tables_in, typed, write_csv


    mkt = typed("market", [{"name": "Ane Aldea", "team": "Alavés", "position": "defensa",
            "value": "2.050.000", "delta_1d": "-12.000"},
           {"name": "Bo Bidal", "team": "Betis", "position": "delantero",
            "value": "", "delta_1d": "0"}])
    xi = typed("lineups", [{"player_name": "Ane Aldea", "team_slug": "alaves",
           "start_pct": "0.72", "status": "doubt"},
          {"player_name": "Cai Coro", "team_slug": "celta",
           "start_pct": "85", "status": "ok"}])
    p = _merge(_merge({}, mkt, row_key, "name", MARKET_FIELDS), xi,
               lambda r: Name(r["player_name"]).key, "player_name", XI_FIELDS)

    a = p["ane aldea"]
    assert a["value"] == 2050000.0 and a["delta_1d"] == -12000.0
    assert a["pos"] == "defensa" and a["start"] == 72.0 and a["status"] == "doubt"
    assert a["team"] == "Alavés" and a["name"] == "Ane Aldea"

    assert "value" not in p["bo bidal"] and p["bo bidal"]["delta_1d"] == 0.0
    assert "start" not in p["bo bidal"]

    c = p["cai coro"]
    assert c["name"] == "Cai Coro" and c["club"] == "celta" and c["start"] == 85.0
    assert "value" not in c

    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        try:
                assert load_crosswalk().players == {}
                write_csv(table_path("players"),
                         [{"player_id": "a", "name": "A", "club_id": "c"}],
                         PLAYER_COLS)
                xw1 = load_crosswalk()
                assert xw1.players["a"].name == "A", xw1.players
                assert load_crosswalk() is xw1

                write_csv(table_path("players"),
                         [{"player_id": "a", "name": "Renamed", "club_id": "c"}],
                         PLAYER_COLS)
                xw2 = load_crosswalk()
                assert xw2 is not xw1 and xw2.players["a"].name == "Renamed", \
                    xw2.players
        finally:
            _XW_CACHE.clear()
    print("ffcore.players self-test OK")


if __name__ == "__main__":
    _selftest()
