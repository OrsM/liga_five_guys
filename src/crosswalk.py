
from __future__ import annotations

import sys


from ffcore import schema
from ffcore.crosswalk import Club, Crosswalk, Player
from ffcore.parse import money
from ffcore.text import norm, tokens
from ffcore.tidy import (TIDY, load_fixtures, load_lineups, newest,
                         load_market_latest, read_csv, row_key)

PLAYERS = "players.csv"
CLUBS = "clubs.csv"


def build_clubs(market, lineups, elo_rows, fixtures=()) -> dict:
    from ffcore.fixture import ELO_ALIASES, match_team

    teams = sorted({schema.text(r, schema.MARKET.TEAM) for r in market
                    if schema.text(r, schema.MARKET.TEAM)})
    clubs = {norm(t): Club(norm(t), t) for t in teams}

    for r in market:
        t = schema.text(r, schema.MARKET.TEAM)
        tid = schema.text(r, schema.MARKET.TEAM_ID)
        if t and tid and norm(t) in clubs:
            clubs[norm(t)].market_id = clubs[norm(t)].market_id or tid

    for slug in sorted({schema.text(r, schema.LINEUPS.TEAM_SLUG) for r in lineups
                        if schema.text(r, schema.LINEUPS.TEAM_SLUG)}):
        hit = match_team(slug, teams)
        if hit:
            clubs[norm(hit)].ff_slug = clubs[norm(hit)].ff_slug or slug
            clubs[norm(hit)].aliases.add(slug)

    for r in fixtures or []:
        for side, col in (("home", "home_id"), ("away", "away_id")):
            nm = schema.text(r, side)
            aid = schema.text(r, col)
            if not nm or not aid:
                continue
            hit = match_team(nm, teams)
            if hit and not clubs[norm(hit)].af_id:
                clubs[norm(hit)].af_id = aid
            if hit:
                clubs[norm(hit)].aliases.add(nm)

    for r in elo_rows or []:
        club = schema.text(r, schema.ELO.CLUB)
        if not club:
            continue
        hit = match_team(club, teams)
        if hit is None:
            alias = {v: k for k, v in ELO_ALIASES.items()}.get(club)
            hit = next((t for t in teams if norm(t) == alias), None)
        if hit:
            clubs[norm(hit)].elo = club
            clubs[norm(hit)].aliases.add(club)
    return clubs


def group_by_name(players) -> dict[str, list]:
    out: dict[str, list] = {}
    for p in players:
        out.setdefault(norm(p.name), []).append(p)
    return out


def _named(named: dict, name: str, club: str = ""):
    hits = named.get(norm(name)) or []
    if len(hits) != 1 and club:
        hits = [p for p in hits if p.club_id == club]
    return hits[0] if len(hits) == 1 else None


def build_players(registry: dict, market, lineups, api_rows, clubs) -> dict:
    club_of_slug = {c.ff_slug: c.club_id for c in clubs.values() if c.ff_slug}
    out = dict(registry)
    for r in market:
        pid = row_key(r)
        if not pid:
            continue
        p = out.setdefault(pid, Player(pid))
        p.name = schema.text(r, schema.MARKET.NAME) or p.name
        p.club_id = norm(r.get("team")) or p.club_id
    named = group_by_name(out.values())

    for r in lineups:
        slug = schema.text(r, schema.LINEUPS.PLAYER_SLUG)
        p = _named(named, r.get("player_name"),
                   club_of_slug.get(r.get("team_slug") or "", ""))
        field = ("af_slug" if schema.text(r, schema.LINEUPS.SOURCE)
                 == "analitica" else "ff_slug")
        if p is not None and slug and not getattr(p, field):
            setattr(p, field, slug)

    value = {row_key(r): money(r.get("value")) for r in market}
    app = {schema.text(r, schema.API_TEAMS.PLAYER_ID): r for r in api_rows}
    words = {pid: set(tokens(p.name)) for pid, p in out.items()}
    weak = set()
    for pid, p in out.items():
        r = app.get(p.app_id)
        if r is None:
            continue
        ratio = ((money(r.get("market_value")) or 0) / value[pid]
                 if value.get(pid) else 1.0)
        related = {w[:4] for w in words[pid]} & {
            w[:4] for w in tokens(r.get("player_name"))}
        if not (0.8 < ratio < 1.25 or (related and 0.5 < ratio < 2.0)):
            p.app_id = ""
        elif not related:
            weak.add(pid)

    held = {p.app_id for p in out.values() if p.app_id}
    for app_id, r in app.items():
        theirs = money(r.get("market_value"))
        if not app_id or app_id in held or not theirs:
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
            weak.discard(hits[0])
            held.discard(out[hits[0]].app_id)
            out[hits[0]].app_id = app_id
            out[hits[0]].app_names.add(schema.text(r, schema.API_TEAMS.PLAYER_NAME))
            held.add(app_id)
    return out


def build_understat_ids(rows, players: dict, clubs: dict) -> int:
    from ffcore.fixture import match_team
    from ffcore.text import resolve as text_resolve

    market_teams = sorted({c.market for c in clubs.values() if c.market})
    by_club: dict[str, list] = {}
    for p in players.values():
        by_club.setdefault(p.club_id, []).append(p)

    best: dict[str, dict] = {}
    for r in rows:
        uid = schema.text(r, schema.UNDERSTAT_PLAYERS.UNDERSTAT_ID)
        if not uid:
            continue
        prev = best.get(uid)
        if prev is None or (r.get("season") or "") >= (prev.get("season") or ""):
            best[uid] = r

    matched = 0
    for uid, r in best.items():
        team = match_team(r.get("team_title") or "", market_teams)
        if not team:
            continue
        candidates = by_club.get(norm(team), [])
        if not candidates:
            continue
        wrapped = [{"name": p.name, "_p": p} for p in candidates]
        hit, _cands = text_resolve(r.get("player_name") or "", wrapped)
        if hit is not None and not hit["_p"].understat_id:
            hit["_p"].understat_id = uid
            matched += 1
    return matched


def main() -> None:
    market = load_market_latest()
    lineups = load_lineups("") + list(read_csv(TIDY / "starters.csv"))
    registry = Crosswalk.read(TIDY / PLAYERS, TIDY / CLUBS)
    clubs = build_clubs(market, lineups, read_csv(TIDY / "elo.csv"),
                        load_fixtures())
    players = build_players(
        registry.players, market, lineups,
        newest("api_teams.csv") + newest("api_market.csv")
        + newest("api_players.csv") + newest("api_players_all.csv"), clubs)
    understat_matched = build_understat_ids(
        read_csv(TIDY / "understat_players.csv"), players, clubs)
    xw = Crosswalk(players, clubs)
    xw.write(TIDY / PLAYERS, TIDY / CLUBS)

    c = xw.coverage()
    print("wrote %s and %s: %d players (%d new), %d clubs — %.0f%% carry a "
          "probable-XI slug, %.0f%% the second source's, %.0f%% an app id, "
          "%.0f%% an understat id (%d newly matched this run)"
          % (TIDY / PLAYERS, TIDY / CLUBS, c["players"],
             len(players) - len(registry.players), c["clubs"],
             100 * c["ff"], 100 * c["af"], 100 * c["app"],
             100 * c["understat"], understat_matched))
    for idx, ids in xw.clashes().items():
        print("  warn: %s claimed by two players, refused until resolved: %s"
              % (idx, ", ".join(ids)))


def _selftest() -> None:
    market = [{"name": "Álvaro Fernández", "slug": "alvaro-fernandez-m",
               "team": "Espanyol"},
              {"name": "Jonny Castro", "slug": "jonny-castro-m",
               "team": "Alavés"}]
    lineups = [
        {"source": "futbolfantasy", "team_slug": "espanyol",
         "player_name": "Álvaro Fernández", "player_slug": "alvaro-fdez"},
        {"source": "analitica", "team_slug": "espanyol",
         "player_name": "Alvaro Fernandez", "player_slug": "af-alvaro"},
    ]
    starters = [{"team_slug": "alaves", "player_name": "Jonny Castro",
                 "player_slug": "jonny-castro-ff", "role": "starter"}]
    elo = [{"club": "Bilbao", "elo": "1800"}]

    clubs = build_clubs(market, lineups, elo)
    assert set(clubs) == {"espanyol", "alaves"}, clubs
    assert clubs["espanyol"].ff_slug == "espanyol"

    players = build_players({}, market, lineups + starters, [], clubs)
    xw = Crosswalk(players, clubs)
    for ids, want in [({"name": "Alvaro Fernandez"}, "alvaro fernandez"),
                      ({"ff_slug": "alvaro-fdez"}, "alvaro fernandez"),
                      ({"af_slug": "af-alvaro"}, "alvaro fernandez"),
                      ({"ff_slug": "jonny-castro-ff"}, "jonny castro"),
                      ({"app_id": "9999"}, None)]:
        assert xw.player(**ids) == want, (ids, xw.player(**ids))
    assert players["alvaro fernandez"].club_id == "espanyol"

    twins = [{"name": "Pablo Fornals", "team": "Betis", "ff_id": "a",
              "value": "5000000"},
             {"name": "Pablo Fornals", "team": "Villarreal", "ff_id": "b",
              "value": "9000000"},
             {"name": "Fermin Lopez", "team": "Barcelona", "ff_id": "c",
              "value": "100000000"}]
    api = [{"player_name": "Fermín", "player_id": "1715",
            "market_value": "108000000"},
           {"player_name": "Fer López", "player_id": "2929",
            "market_value": "15000000"},
           {"player_name": "Pablo Fornals", "player_id": "12",
            "market_value": "5100000"},
           {"player_name": "Fornals", "player_id": "99",
            "market_value": "40000000"}]
    got = build_players({"c": Player("c", app_id="2929"),
                         "gone": Player("gone", "Left The League")},
                        twins, [], api, build_clubs(twins, [], []))
    assert {k: p.app_id for k, p in got.items()} == \
        {"a": "12", "b": "", "c": "1715", "gone": ""}, got
    assert got["c"].app_names == {"Fermín"}, got["c"]
    assert got["b"].name == "Pablo Fornals" and got["b"].club_id == "villarreal"

    understat = [
        {"understat_id": "701", "player_name": "Alvaro Fernandez",
         "team_title": "Espanyol", "season": "2025"},
        {"understat_id": "999", "player_name": "Nobody Real",
         "team_title": "Nowhere FC", "season": "2025"},
    ]
    matched = build_understat_ids(understat, players, clubs)
    assert matched == 1, matched
    assert players["alvaro fernandez"].understat_id == "701"
    assert xw.player(understat_id="701") is None
    assert Crosswalk(players, clubs).player(understat_id="701") \
        == "alvaro fernandez"
    matched2 = build_understat_ids(
        [{"understat_id": "999999", "player_name": "Alvaro Fernandez",
          "team_title": "Espanyol", "season": "2025"}], players, clubs)
    assert matched2 == 0
    assert players["alvaro fernandez"].understat_id == "701"

    print("crosswalk self-test OK (15 cases)")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        raise SystemExit(0)
    main()
