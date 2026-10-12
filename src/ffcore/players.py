"""The crosswalk of ids between the sources (the registry, players.csv)."""
from __future__ import annotations

from pathlib import Path

from ffcore.tidy import Stamp, mtime_cached, table_path

from ffcore.crosswalk import Crosswalk
__all__ = ["load_crosswalk"]


_XW_CACHE: dict[str, tuple[Stamp, Crosswalk]] = {}


def _read(path: Path) -> Crosswalk:
    xw = Crosswalk.read(path)
    xw.mirror()
    return xw


def load_crosswalk() -> Crosswalk:
    """players.csv as a Crosswalk, empty if there is none yet; each time it
    is read anew it is mirrored into lfg.db, so a replay or a rebuilt file
    joins the same registry."""
    path = table_path("players")
    return mtime_cached(path, _XW_CACHE, str(path), _read, path) or Crosswalk()


def _selftest() -> None:
    import tempfile
    from pathlib import Path

    from ffcore.crosswalk import PLAYER_COLS
    from ffcore.tidy import tables_in, write_csv


    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        try:
                assert load_crosswalk().players == {}
                write_csv(table_path("players"),
                         [{"player_id": "a", "name": "A", "club_id": "c"}],
                         PLAYER_COLS)
                xw1 = load_crosswalk()
                from ffcore.db import rows
                assert rows("SELECT count(*) FROM crosswalk")[0][0] == len(xw1.players), \
                    "loading the registry mirrors it into lfg.db"
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
