
from __future__ import annotations

import sys


from ffcore.parse import text
from ffcore.crosswalk import Crosswalk, Player
from ffcore.names import AppId, Name, app_id, row_key
from ffcore.text import tokens
from ffcore.tidy import current, history, TIDY

PLAYERS = "players.csv"


def group_by_name(players) -> dict[str, list]:
    out: dict[str, list] = {}
    for p in players:
        out.setdefault(Name(p.name).key, []).append(p)
    return out


def _named(named: dict, name: str, club: str = ""):
    hits = named.get(Name(name).key) or []
    if len(hits) != 1 and club:
        hits = [p for p in hits if p.club_id == club]
    return hits[0] if len(hits) == 1 else None


def build_players(registry: dict, market, lineups, api_rows) -> dict:
    out = dict(registry)
    for r in market:
        pid = row_key(r)
        if not pid:
            continue
        p = out.setdefault(pid, Player(pid))
        p.name = text(r, "name") or p.name
        p.club_id = r.get("club") or p.club_id
    named = group_by_name(out.values())

    for r in lineups:
        slug = text(r, "player_slug")
        p = _named(named, r.get("player_name"), r.get("team_slug") or "")
        if p is not None and slug and not p.ff_slug:
            p.ff_slug = slug

    value = {row_key(r): r.get("value") for r in market}
    app = {app_id(r): r for r in api_rows}
    words = {pid: set(tokens(p.name)) for pid, p in out.items()}
    weak = set()
    for pid, p in out.items():
        r = app.get(p.app_id)
        if r is None:
            continue
        ratio = ((r.get("market_value") or 0) / value[pid]
                 if value.get(pid) else 1.0)
        related = {w[:4] for w in words[pid]} & {
            w[:4] for w in tokens(r.get("player_name"))}
        if not (0.8 < ratio < 1.25 or (related and 0.5 < ratio < 2.0)):
            print("  app id %s (%s) dropped from %s: the app's name and value "
                  "disagree" % (p.app_id, r.get("player_name"), p.name))
            p.app_id = ""
        elif not related:
            weak.add(pid)

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


def main() -> None:
    market = current("market")
    lineups = history("lineups") + current("starters")
    registry = Crosswalk.read(TIDY / PLAYERS)
    players = build_players(
        registry.players, market, lineups,
        current("api_teams") + current("api_market")
        + current("api_players_all"))
    xw = Crosswalk(players)
    xw.write(TIDY / PLAYERS)

    c = xw.coverage()
    print("wrote %s: %d players (%d new) — %.0f%% carry a "
          "probable-XI slug, %.0f%% an app id"
          % (TIDY / PLAYERS, c["players"],
             len(players) - len(registry.players),
             100 * c["ff"], 100 * c["app"]))
    for idx, ids in xw.clashes().items():
        print("  warn: %s claimed by two players, refused until resolved: %s"
              % (idx, ", ".join(ids)))


def _selftest() -> None:
    from ffcore.tidy import typed
    market = typed("market", [{"name": "Álvaro Fernández", "team": "Espanyol",
               "club": "espanyol"},
              {"name": "Jonny Castro", "team": "Alavés", "club": "alaves"}])
    lineups = typed("lineups", [
        {"source": "futbolfantasy", "team_slug": "espanyol",
         "player_name": "Álvaro Fernández", "player_slug": "alvaro-fdez"},
    ])
    starters = typed("starters", [{"team_slug": "alaves", "player_name": "Jonny Castro",
                 "player_slug": "jonny-castro-ff", "role": "starter"}])
    players = build_players({}, market, lineups + starters, [])
    xw = Crosswalk(players)
    assert xw.player(name="Alvaro Fernandez") == "alvaro fernandez"
    assert xw.player(ff_slug="alvaro-fdez") == "alvaro fernandez"
    assert xw.player(ff_slug="jonny-castro-ff") == "jonny castro"
    assert xw.player(app_id=AppId("9999")) is None
    assert players["alvaro fernandez"].club_id == "espanyol"

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
    got = build_players({"c": Player("c", app_id="2929"),
                         "gone": Player("gone", "Left The League")},
                        twins, [], api)
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
