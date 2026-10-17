
from __future__ import annotations

import sys


from ffcore.parse import Rows, text
from ffcore.crosswalk import Crosswalk, Player, clashes
from ffcore.players import load_crosswalk
from ffcore.names import AppId, PlayerKey, app_id
from ffcore.text import tokens
from ffcore.tidy import current, history, table_path



def build_players(registry: dict[str, Player], market: Rows, api_rows: Rows
                  ) -> dict[str, Player]:
    """The registry with this market's players added and their app ids
    checked and attached: by value and by the words of their names."""
    out = dict(registry)
    for r in market:
        pid = PlayerKey(text(r, "ff_id"))
        if not pid:
            continue
        p = out.setdefault(pid, Player(pid))
        p.name = text(r, "name") or p.name
        p.club_id = r.get("club") or p.club_id
    value: dict[str, float | None] = {text(r, "ff_id"): r.get("value") for r in market}
    app = {app_id(r): r for r in api_rows}
    words = {pid: set(tokens(p.name)) for pid, p in out.items()}
    weak: set[str] = set()
    for key, p in out.items():
        row = app.get(p.app_id)
        if row is None:
            continue
        ratio = ((row.get("market_value") or 0) / v if (v := value.get(key)) else 1.0)
        related = {w[:4] for w in words[key]} & {
            w[:4] for w in tokens(row.get("player_name"))}
        if not (0.8 < ratio < 1.25 or (related and 0.5 < ratio < 2.0)):
            print("  app id %s (%s) dropped from %s: the app's name and value "
                  "disagree" % (p.app_id, row.get("player_name"), p.name))
            p.app_id = AppId("")
        elif not related:
            weak.add(key)

    held = {p.app_id for p in out.values() if p.app_id}
    for aid, r in app.items():
        theirs = r.get("market_value")
        if not aid or aid in held or not theirs:
            continue
        theirs_words = [set(tokens(n)) for n in (r.get("player_name"),
                                                 r.get("player_name_full"))
                        if n]
        hits = [pid for pid in value
                if (not out[pid].app_id or pid in weak) and value[pid]
                and 0.8 < theirs / value[pid] < 1.25
                and any(w and (w <= words[pid] or words[pid] <= w)
                        for w in theirs_words)]
        if len(hits) == 1:
            print("  app id %s (%s) attached to %s"
                  % (aid, r.get("player_name"), out[hits[0]].name))
            weak.discard(hits[0])
            held.discard(out[hits[0]].app_id)
            out[hits[0]].app_id = aid
            out[hits[0]].app_names.add(text(r, "player_name"))
            held.add(aid)
    return out


def attach_slugs(players: dict[str, Player], rows: Rows) -> None:
    """Each futbolfantasy slug to the player its rows resolve to (by name,
    or name and club, while the slug names nobody), if he has none."""
    for r in rows:
        slug, who = text(r, "player_slug"), r.get("player")
        if slug and who in players and not players[who].ff_slug:
            players[who].ff_slug = slug


def main() -> None:
    registry = load_crosswalk()
    players = build_players(registry.players, current("market"),
                            current("api_teams") + current("api_market")
                            + current("api_players_all"))
    xw = Crosswalk(players)
    xw.mirror()
    attach_slugs(players, [*history("lineups"), *current("starters")])
    xw.save(table_path("players"))

    c = xw.coverage()
    print("wrote %s: %d players (%d new) — %.0f%% carry a "
          "probable-XI slug, %.0f%% an app id"
          % (table_path("players"), c["players"],
             len(players) - len(registry.players),
             100 * c["ff"], 100 * c["app"]))
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
    players = build_players({}, market, [])
    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        Crosswalk(players).mirror()
        store_sql("lineups", [dict(r, observed_at="2026-08-01T0900Z") for r in lineups])
        store_sql("starters", [dict(r, observed_at="2026-08-01T0900Z", match_id="m")
                               for r in starters])
        attach_slugs(players, [*history("lineups"), *current("starters")])
    assert players["af"].ff_slug == "alvaro-fdez", players
    assert players["jc"].ff_slug == "jonny-castro-ff", "by name within the club"
    assert players["af"].club_id == "espanyol"

    twins = typed("market", [{"name": n, "club": c, "ff_id": i, "value": v} for n, c, i, v in [
        ("Pablo Fornals", "betis", "a", "5000000"),
        ("Pablo Fornals", "villarreal", "b", "9000000"),
        ("Fermin Lopez", "barcelona", "c", "100000000")]])
    api = typed("api_teams", [{"player_name": "Fermín", "player_id": "1715",
            "market_value": "108000000"},
           {"player_name": "Fer López", "player_id": "2929",
            "market_value": "15000000"},
           {"player_name": "Pablo Fornals", "player_id": "12",
            "market_value": "5100000"},
           {"player_name": "Fornals", "player_id": "99",
            "market_value": "40000000"}])
    got = build_players({"c": Player(PlayerKey("c"), app_id=AppId("2929")),
                         "gone": Player(PlayerKey("gone"), "Left The League")},
                        twins, api)
    assert {k: p.app_id for k, p in got.items()} == \
        {"a": "12", "b": "", "c": "1715", "gone": ""}, got
    assert got["c"].app_names == {"Fermín"}, got["c"]
    assert got["b"].name == "Pablo Fornals" and got["b"].club_id == "villarreal"

    print("crosswalk self-test OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        raise SystemExit(0)
    main()
