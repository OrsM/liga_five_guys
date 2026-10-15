
from __future__ import annotations

import os
from contextlib import closing
from collections.abc import Iterable, Mapping
from pathlib import Path
from dataclasses import dataclass, field

from ffcore import db as lfg
from ffcore.facts import identity
from ffcore.names import AppId, PlayerKey
from ffcore.tidy import read_csv, write_csv

__all__ = ["Player", "Crosswalk", "PLAYER_COLS"]

PLAYER_COLS = ["player_id", "name", "club_id", "ff_slug", "app_id",
               "app_names"]


def _join(vals: Iterable[str]) -> str:
    return "|".join(sorted({v for v in vals if v}))


def _split(cell: str | None) -> set[str]:
    return {v for v in (cell or "").split("|") if v}


@dataclass
class Player:
    player_id: PlayerKey
    name: str = ""
    club_id: str = ""
    ff_slug: str = ""
    app_id: AppId = AppId("")
    app_names: set[str] = field(default_factory=set)

    def row(self) -> dict[str, str]:
        return {"player_id": self.player_id, "name": self.name,
                "club_id": self.club_id,
                "ff_slug": self.ff_slug, "app_id": self.app_id,
                "app_names": _join(self.app_names)}


class Crosswalk:
    """The registry: who each player is (players.csv). What an id or a name
    resolves to is identity.sql's, on the registry mirrored into lfg.db:
    each table's rows read with their player."""

    def __init__(self, players: Mapping[str, Player] | None = None) -> None:
        self.players: dict[str, Player] = dict(players or {})

    def mirror(self) -> None:
        """These players into lfg.db's crosswalk, for the views that join it."""
        with closing(lfg.connect()) as db, db:
            db.execute("DELETE FROM crosswalk")
            db.executemany("INSERT INTO crosswalk VALUES (?, ?, ?, ?, ?, ?)",
                           [tuple(p.row()[c] for c in PLAYER_COLS) for p in self.players.values()])
            identity(db)

    def coverage(self) -> dict[str, float]:
        n = len(self.players) or 1
        return {"players": len(self.players),
                "ff": sum(1 for p in self.players.values() if p.ff_slug) / n,
                "app": sum(1 for p in self.players.values() if p.app_id) / n}

    @classmethod
    def read(cls, players_path: str | Path) -> Crosswalk:
        players = {}
        for r in read_csv(players_path):
            pid = r.get("player_id")
            if pid:
                players[pid] = Player(
                    PlayerKey(pid), r.get("name", ""), r.get("club_id", ""),
                    r.get("ff_slug", ""), AppId(r.get("app_id", "")),
                    _split(r.get("app_names")))
        return cls(players)

    def write(self, players_path: str | Path) -> None:
        write_csv(players_path, [p.row() for p in sorted(
            self.players.values(), key=lambda p: p.player_id)], PLAYER_COLS)


def clashes() -> dict[str, list[str]]:
    """The ids two players in the mirrored registry claim, refused until resolved."""
    out: dict[str, list[str]] = {}
    for idx, value in lfg.rows("SELECT id, value FROM id_clashes ORDER BY id, value"):
        out.setdefault(idx, []).append(value)
    return out


def _selftest() -> None:
    import tempfile
    from ffcore.tidy import current, store_sql, tables_in

    xw = Crosswalk({
        "af": Player(PlayerKey("af"), "Alvaro Fernandez", "espanyol", "alvaro-fernandez",
                     AppId("2101"), app_names={"A. Ferllo"}),
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

        pp = os.path.join(d, "p.csv")
        xw.write(pp)
        again = Crosswalk.read(pp)
        assert again.players["af"].app_names == {"A. Ferllo"}
        assert set(again.players) == set(xw.players)
        assert Crosswalk.read(os.path.join(d, "nope.csv")).players == {}
    cov = xw.coverage()
    assert cov["players"] == 6 and abs(cov["ff"] - 2 / 6) < 1e-12, cov

    print("ffcore.crosswalk self-test OK")


if __name__ == "__main__":
    _selftest()
