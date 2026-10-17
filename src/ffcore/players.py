"""The crosswalk of ids between the sources: the registry, lfg.db's crosswalk."""
from __future__ import annotations

from ffcore.crosswalk import Crosswalk
from ffcore.tidy import table_path

__all__ = ["load_crosswalk"]


def load_crosswalk() -> Crosswalk:
    """The registry (Crosswalk.load; players.csv is its export)."""
    return Crosswalk.load(table_path("players"))


def _selftest() -> None:
    import tempfile
    from pathlib import Path

    from ffcore.crosswalk import PLAYER_COLS, Player, PlayerKey
    from ffcore.db import rows
    from ffcore.tidy import tables_in, write_csv

    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        assert load_crosswalk().players == {}, "no table, no export: empty"
        write_csv(table_path("players"), [{"player_id": "a", "name": "A", "club_id": "c"}], PLAYER_COLS)
        assert load_crosswalk().players["a"].name == "A", "an empty table is restored from the export"
        assert rows("SELECT player_id, name FROM crosswalk") == [("a", "A")]
        write_csv(table_path("players"), [{"player_id": "a", "name": "Stale", "club_id": "c"}], PLAYER_COLS)
        assert load_crosswalk().players["a"].name == "A", "the table owns the registry: the export is not read"
        xw = load_crosswalk()
        xw.players["b"] = Player(PlayerKey("b"), "B")
        xw.save(table_path("players"))
        assert rows("SELECT player_id FROM crosswalk ORDER BY 1") == [("a",), ("b",)]
        assert "B" in table_path("players").read_text(), "saving writes the export too"
    print("ffcore.players self-test OK")


if __name__ == "__main__":
    _selftest()
