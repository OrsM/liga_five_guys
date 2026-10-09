
from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any
from dataclasses import dataclass, field

from ffcore.names import AppId, Name, PlayerKey
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

    def __init__(self, players: Mapping[str, Player] | None = None) -> None:
        self.players: dict[str, Player] = dict(players or {})
        self._reindex()


    def _reindex(self) -> None:
        self._by_ff: dict[str, PlayerKey] = {}
        self._by_app: dict[str, PlayerKey] = {}
        self._clash: dict[str, set[str]] = {}
        names: dict[str, set[str]] = {}
        for p in self.players.values():
            if p.name:
                names.setdefault(Name(p.name).key, set()).add(p.player_id)
            for idx, key, label in (
                    (self._by_ff, p.ff_slug, "ff_slug"),
                    (self._by_app, p.app_id, "app_id")):
                if not key:
                    continue
                if key in idx and idx[key] != p.player_id:
                    self._clash.setdefault(label, set()).add(key)
                idx[key] = PlayerKey(p.player_id)
        for label, keys in self._clash.items():
            idx = {"ff_slug": self._by_ff, "app_id": self._by_app}[label]
            for k in keys:
                idx.pop(k, None)
        self._by_name = {n: PlayerKey(next(iter(ids))) for n, ids in names.items()
                         if len(ids) == 1}

    def clashes(self) -> dict[str, list[str]]:
        return {k: sorted(v) for k, v in sorted(self._clash.items()) if v}

    def player(self, *, name: str | None = None, ff_slug: str | None = None,
               app_id: AppId | None = None) -> PlayerKey | None:
        """Our key for a player, by futbolfantasy slug, app id or name."""
        for key, idx in ((ff_slug, self._by_ff), (app_id, self._by_app)):
            if key and key in idx:
                return idx[key]
        k = Name(name).key
        if k in self.players:
            return PlayerKey(k)
        return self._by_name.get(k)

    def key_of(self, r: Mapping[str, Any]) -> PlayerKey | None:
        fid = (r.get("ff_id") or "").strip()
        if fid in self.players:
            return PlayerKey(fid)
        return self.player(ff_slug=(r.get("player_slug") or "").strip() or None,
                           name=r.get("player_name_full")
                           or r.get("player_name"))


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


def _selftest() -> None:
    xw = Crosswalk({
        "alvaro fernandez": Player(
            PlayerKey("alvaro fernandez"), "Alvaro Fernandez", "espanyol",
            "alvaro-fernandez", AppId("2101"),
            app_names={"A. Ferllo"}),
        "jonny castro": Player(PlayerKey("jonny castro"), "Jonny Castro", "alaves",
                               ff_slug="jonny-castro",
                               app_names={"Jonny Otto"}),
    })

    assert xw.player(name="Alvaro Fernandez") == xw.player(ff_slug="alvaro-fernandez") \
        == xw.player(app_id=AppId("2101")) == "alvaro fernandez"
    assert xw.player(name="Álvaro Fernández") == "alvaro fernandez"
    assert xw.player(ff_slug="who-is-this") is None
    assert xw.player() is None

    clash = Crosswalk({
        "carlos romero": Player(PlayerKey("carlos romero"), app_id=AppId("2614")),
        "isaac romero": Player(PlayerKey("isaac romero"), app_id=AppId("2614"))})
    assert clash.player(app_id=AppId("2614")) is None
    assert clash.clashes() == {"app_id": ["2614"]}, clash.clashes()
    solo = Crosswalk({"carlos romero": Player(PlayerKey("carlos romero"), app_id=AppId("2614")),
                      "isaac romero": Player(PlayerKey("isaac romero"))})
    assert solo.player(app_id=AppId("2614")) == "carlos romero"
    assert solo.clashes() == {}


    import tempfile
    with tempfile.TemporaryDirectory() as d:
        pp = os.path.join(d, "p.csv")
        xw.write(pp)
        again = Crosswalk.read(pp)
        assert again.players["alvaro fernandez"].app_names == {"A. Ferllo"}
        assert again.player(ff_slug="jonny-castro") == "jonny castro"
        assert set(again.players) == set(xw.players)
        assert Crosswalk.read(os.path.join(d, "nope.csv")).players == {}

    cov = xw.coverage()
    assert cov["players"] == 2
    assert cov["ff"] == 1.0, cov

    named = Crosswalk({
        "867": Player(PlayerKey("867"), "Álvaro García", ff_slug="alvaro-garcia"),
        "12993": Player(PlayerKey("12993"), "Álvaro García"),
        "132": Player(PlayerKey("132"), "Sergio Canales")})
    rows = [
        ({"player_slug": "alvaro-garcia", "player_name": "x"}, "867"),
        ({"player_slug": "gone", "player_name": "Álvaro García"}, None),
        ({"ff_id": "132", "player_name": "whoever"}, "132"),
        ({"ff_id": "999", "player_name": "Canales",
          "player_name_full": "Sergio Canales"}, "132"),
    ]
    for r, expected in rows:
        assert named.key_of(r) == expected, (r, named.key_of(r))

    print("ffcore.crosswalk self-test OK")


if __name__ == "__main__":
    _selftest()
