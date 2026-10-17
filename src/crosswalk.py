
from __future__ import annotations

import sys


from ffcore.parse import Rows, text
from ffcore.crosswalk import Crosswalk, Player, clashes
from ffcore.names import AppId, PlayerKey
from ffcore.db import rows
from ffcore.tidy import current, history



def registry() -> dict[str, Player]:
    """Every player as the data has it (schema.sql registry): name, club
    and app id, no slugs yet."""
    return {pid: Player(PlayerKey(pid), name, club, app_id=AppId(aid))
            for pid, name, club, aid in rows("SELECT player_id, name, club_id, app_id FROM registry")}


def attach_slugs(players: dict[str, Player], rows: Rows) -> None:
    """Each futbolfantasy slug to the player its rows resolve to (by name,
    or name and club, while the slug names nobody), if he has none."""
    for r in rows:
        slug, who = text(r, "player_slug"), r.get("player")
        if slug and who in players and not players[who].ff_slug:
            players[who].ff_slug = slug


def main() -> None:
    players = registry()
    xw = Crosswalk(players)
    xw.mirror()
    attach_slugs(players, [*history("lineups"), *current("starters")])
    xw.mirror()

    c = xw.coverage()
    print("registry: %d players — %.0f%% carry a probable-XI slug, %.0f%% an app id"
          % (c["players"], 100 * c["ff"], 100 * c["app"]))
    for idx, ids in clashes().items():
        print("  warn: %s claimed by two players, refused until resolved: %s"
              % (idx, ", ".join(ids)))


def _selftest() -> None:
    from ffcore.tidy import typed
    market = typed("market", [{"ff_id": "af", "name": "Álvaro Fernández", "team": "Espanyol",
               "club": "espanyol"},
              {"ff_id": "jc", "name": "Jonny Castro", "team": "Alavés", "club": "alaves"}])
    lineups = typed("lineups", [
        {"source": "futbolfantasy", "team_slug": "espanyol",
         "player_name": "Álvaro Fernández", "player_slug": "alvaro-fdez"},
    ])
    starters = typed("starters", [{"team_slug": "alaves", "player_name": "Jonny Castro",
                 "player_slug": "jonny-castro-ff", "role": "starter"}])
    import tempfile
    from pathlib import Path
    from ffcore.tidy import store_sql, tables_in
    players = {k: Player(PlayerKey(k), text(r, "name"), r.get("club") or "")
               for r in market for k in [text(r, "ff_id")]}
    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        Crosswalk(players).mirror()
        store_sql("lineups", [dict(r, observed_at="2026-08-01T0900Z") for r in lineups])
        store_sql("starters", [dict(r, observed_at="2026-08-01T0900Z", match_id="m")
                               for r in starters])
        attach_slugs(players, [*history("lineups"), *current("starters")])
    assert players["af"].ff_slug == "alvaro-fdez", players
    assert players["jc"].ff_slug == "jonny-castro-ff", "by name within the club"
    assert players["af"].club_id == "espanyol"

    a, b = "2026-08-01T0900Z", "2026-08-02T0900Z"
    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        store_sql("market", [{"observed_at": at, "ff_id": i, "name": n, "club": c, "value": v}
                             for at, i, n, c, v in [
            (a, "jc", "Jonny Castro", "celta", "19798425"), (b, "jc", "Jonny Castro", "alaves", "19798425"),
            (a, "lz", "Sergio Jose Lozano", "eibar", "560396"), (a, "ja", "Jose Angel Lopez", "espanyol", "560396"),
            (a, "pf", "Pablo Fornals", "betis", "5000000"), (a, "gone", "Left Early", "x", "1000000")]])
        store_sql("api_teams", [{"observed_at": at, "player_id": i, "player_name": n, "market_value": v,
                                 "manager": "m"} for at, i, n, v in [
            (a, "2552", "Jonny Otto", "19798425"), (b, "2552", "Jonny Otto", "19798425"),
            (a, "3178", "Lozano", "560396"), (a, "3172", "Jose Angel", "560396"),
            (a, "99", "Fornals", "40000000")]])
        c = "2026-08-03T0900Z"
        store_sql("market", [{"observed_at": at, "ff_id": "bo", "name": "Cesar Bonafe", "club": "elche",
                              "value": "1057681"} for at in (a, b, c)]
                  + [{"observed_at": at, "ff_id": "bn", "name": "Bono Bonafe", "club": "x", "value": "1057681"}
                     for at in (a,)])
        store_sql("api_players_all", [{"observed_at": a, "player_id": "3279", "player_name": "Bonafé",
                                       "market_value": "1057681"}])
        got = registry()
    assert {k: p.app_id for k, p in got.items()} == {
        "jc": "2552", "lz": "3178", "ja": "3172", "pf": "", "gone": "", "bo": "3279", "bn": ""}, \
        ("same value, names sharing a stem; at one value, the most stems shared wins; an all-players"
         " row holds until his next, so it meets every market snapshot in between", got)
    assert (got["jc"].name, got["jc"].club_id) == ("Jonny Castro", "alaves"), \
        "his latest market row; one who left keeps his"

    print("crosswalk self-test OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        raise SystemExit(0)
    main()
