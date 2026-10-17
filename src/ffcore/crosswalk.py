from __future__ import annotations

from contextlib import closing
from collections.abc import Mapping
from dataclasses import dataclass

from ffcore import db as lfg
from ffcore.facts import identity
from ffcore.names import AppId, PlayerKey

__all__ = ["Player", "Crosswalk", "PLAYER_COLS"]

PLAYER_COLS = ["player_id", "name", "club_id", "ff_slug", "app_id"]


@dataclass
class Player:
    player_id: PlayerKey
    name: str = ""
    club_id: str = ""
    ff_slug: str = ""
    app_id: AppId = AppId("")

    def row(self) -> dict[str, str]:
        return {"player_id": self.player_id, "name": self.name, "club_id": self.club_id,
                "ff_slug": self.ff_slug, "app_id": self.app_id}


class Crosswalk:
    """The registry: who each player is (lfg.db's crosswalk, which the
    crosswalk stage rebuilds from the data each run). What an id or a name
    resolves to is identity.sql's: each table's rows read with their player."""

    def __init__(self, players: Mapping[str, Player] | None = None) -> None:
        self.players: dict[str, Player] = dict(players or {})

    def mirror(self) -> None:
        """These players into lfg.db's crosswalk, for the views that join it."""
        with closing(lfg.connect()) as db, db:
            db.execute("DELETE FROM crosswalk")
            db.executemany("INSERT INTO crosswalk (%s) VALUES (%s)" % (", ".join(PLAYER_COLS), ", ".join("?" * len(PLAYER_COLS))),
                           [tuple(p.row()[c] for c in PLAYER_COLS) for p in self.players.values()])
            identity(db)

    def coverage(self) -> dict[str, float]:
        n = len(self.players) or 1
        return {"players": len(self.players),
                "ff": sum(1 for p in self.players.values() if p.ff_slug) / n,
                "app": sum(1 for p in self.players.values() if p.app_id) / n}


def clashes() -> dict[str, list[str]]:
    """The ids two players in the mirrored registry claim, refused until resolved."""
    out: dict[str, list[str]] = {}
    for idx, value in lfg.rows("SELECT id, value FROM id_clashes ORDER BY id, value"):
        out.setdefault(idx, []).append(value)
    return out


def _selftest() -> None:
    import tempfile
    from pathlib import Path
    from ffcore.tidy import current, store_sql, tables_in

    xw = Crosswalk({
        "af": Player(PlayerKey("af"), "Alvaro Fernandez", "espanyol", "alvaro-fernandez",
                     AppId("2101")),
        "jc": Player(PlayerKey("jc"), "Jonny Castro", "alaves", ff_slug="jonny-castro"),
        "cr": Player(PlayerKey("cr"), "Carlos Romero", app_id=AppId("2614")),
        "ir": Player(PlayerKey("ir"), "Isaac Romero", app_id=AppId("2614")),
        "11": Player(PlayerKey("11"), "Álvaro García", "betis"),
        "12": Player(PlayerKey("12"), "Alvaro Garcia", "rayo")})
    at = "2026-08-01T0900Z"
    with tempfile.TemporaryDirectory() as d, tables_in(Path(d)):
        xw.mirror()
        store_sql("lineups", [dict(observed_at=at, player_slug=slug, player_name=name,
                                   team_slug=club) for slug, name, club in [
            ("alvaro-fernandez", "x", "espanyol"), ("", "Álvaro Fernández", ""),
            ("who-is-this", "", ""), ("", "Álvaro García", ""), ("", "Alvaro Garcia", "rayo")]])
        got = [r["player"] for r in current("lineups")]
        assert got == ["af", "af", None, None, "12"], \
            "slug, else a name one player has, else a name one player of the club has"
        store_sql("api_teams", [dict(observed_at=at, player_id=a, manager="m")
                                for a in ("2101", "2614", "9999")])
        assert [r["player"] for r in current("api_teams")] == ["af", None, None], \
            "an app id two players claim names nobody"
        assert clashes() == {"app_id": ["2614"]}, clashes()

    cov = xw.coverage()
    assert cov["players"] == 6 and abs(cov["ff"] - 2 / 6) < 1e-12, cov

    print("ffcore.crosswalk self-test OK")


if __name__ == "__main__":
    _selftest()
