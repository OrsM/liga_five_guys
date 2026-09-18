
from __future__ import annotations

import sys


from ffcore import schema
from ffcore.crosswalk import Club, Crosswalk, Player
from ffcore.text import norm
from ffcore.tidy import (TIDY, latest_only, load_fixtures, newest,
                         load_lineups_latest, load_market, load_market_latest,
                         load_starters, narrow_by_club, read_csv, row_key,
                         shared_names)

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


def namesakes(market) -> list[tuple[str, list]]:
    seen: dict[str, set] = {}
    for r in market:
        key = norm(r.get("name"))
        if key:
            seen.setdefault(key, set()).add(schema.text(r, schema.MARKET.TEAM))
    return sorted((k, sorted(v)) for k, v in seen.items() if len(v) > 1)


def build_players(market, lineups, starters, api_rows, lg, clubs) -> dict:

    by_club = {c.ff_slug: c.club_id for c in clubs.values() if c.ff_slug}
    ff_to_market = {c.ff_slug: norm(c.market) for c in clubs.values()
                    if c.ff_slug and c.market}
    market_shared = shared_names(market)
    out: dict[str, Player] = {}
    club_of: dict[str, str] = {}
    for r in market:
        pid = row_key(r, market_shared)
        if not pid:
            continue
        out[pid] = Player(pid, schema.text(r, schema.MARKET.NAME),
                          norm(r.get("team")))
        club_of[pid] = norm(r.get("team"))

    shared: dict[str, list] = {}
    for p in out.values():
        shared.setdefault(norm(p.name), []).append(p)

    def by_name(name: str, team_slug: str = ""):
        hits = shared.get(norm(name)) or []
        if len(hits) == 1:
            return hits[0]
        want = ff_to_market.get((team_slug or "").strip())
        return narrow_by_club(hits, want, lambda p: club_of.get(p.player_id))

    for r in lineups:
        pid = by_name(r.get("player_name"), r.get("team_slug"))
        slug = schema.text(r, schema.LINEUPS.PLAYER_SLUG)
        if pid is None or not slug:
            continue
        wide = schema.text(r, schema.LINEUPS.SOURCE).startswith("futbol")
        if wide and not pid.ff_slug:
            pid.ff_slug = slug
        elif not wide and not pid.af_slug:
            pid.af_slug = slug
        if not pid.club_id and r.get("team_slug") in by_club:
            pid.club_id = by_club[r["team_slug"]]

    ff_index = {p.ff_slug: p for p in out.values() if p.ff_slug}
    for r in starters:
        slug = schema.text(r, schema.STARTERS.PLAYER_SLUG)
        if slug in ff_index:
            continue
        p = by_name(r.get("player_name"), r.get("team_slug"))
        if p is not None and slug and not p.ff_slug:
            p.ff_slug = slug

    index = latest_only(lg.market.rows) if lg and lg.market is not None else []
    known = Crosswalk.read(TIDY / "players.csv", TIDY / "clubs.csv")
    for r in api_rows:
        raw = schema.text(r, schema.API_TEAMS.PLAYER_NAME)
        if not raw:
            continue
        key = known.resolve_api(raw, schema.text(r, schema.API_TEAMS.MANAGER),
                                lg.market if lg else None,
                                lg.owner if lg else None,
                                index, r.get("market_value"),
                                r.get("player_name_full") or "",
                                r.get("player_id") or "")
        p = out.get(key) if key else None
        if p is None:
            p = by_name(raw)
        if p is None:
            continue
        for other in out.values():
            if other is not p:
                other.app_names = {n for n in other.app_names
                                   if norm(n) != norm(raw)}
        p.app_names.add(raw)
        pid = schema.text(r, schema.API_TEAMS.PLAYER_ID)
        if not pid:
            continue
        for other in out.values():
            if other is not p and other.app_id == pid:
                other.app_id = ""
        p.app_id = pid
    return out


def attach_bulk_app_ids(rows, players: dict) -> int:
    by_name: dict[str, list] = {}
    for p in players.values():
        by_name.setdefault(norm(p.name), []).append(p)
    matched = 0
    for r in rows:
        name = schema.text(r, schema.API_PLAYERS_ALL.PLAYER_NAME)
        pid = schema.text(r, schema.API_PLAYERS_ALL.PLAYER_ID)
        if not name or not pid:
            continue
        hits = by_name.get(norm(name)) or []
        if len(hits) != 1 or hits[0].app_id:
            continue
        hits[0].app_id = pid
        matched += 1
    return matched


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
    from ffcore.league import League

    market = load_market_latest()
    lineups = load_lineups_latest(source="")
    starters = load_starters()
    api_rows = (newest("api_teams.csv") + newest("api_market.csv")
                + newest("api_players.csv") + newest("api_players_all.csv"))
    elo_rows = read_csv(TIDY / "elo.csv")
    lg = League.load()

    market_ids = {schema.text(r, schema.MARKET.FF_ID) for r in load_market()
                  if schema.text(r, schema.MARKET.FF_ID)}

    clubs = build_clubs(market, lineups, elo_rows, load_fixtures())
    players = build_players(market, lineups, starters, api_rows, lg, clubs)
    understat_matched = build_understat_ids(
        read_csv(TIDY / "understat_players.csv"), players, clubs)

    fresh = Crosswalk(players, clubs)
    kept = Crosswalk.read(TIDY / PLAYERS, TIDY / CLUBS)
    doubled = {norm(p.name) for p in players.values()
               if "@" in p.player_id}
    dropped = [k for k in list(kept.players) if k in doubled]
    for k in dropped:
        del kept.players[k]
    kept.merge(fresh)

    live = {k: pl for k, pl in kept.players.items() if k in market_ids}
    by_name = {}
    for k, pl in live.items():
        by_name.setdefault(norm(pl.name), k)
    moved, dropped_ghosts = 0, []
    for k, pl in list(kept.players.items()):
        if k in market_ids:
            continue
        target = live.get(by_name.get(k, ""))
        if target is not None:
            for f in ("ff_slug", "af_slug", "app_id"):
                if getattr(pl, f) and not getattr(target, f):
                    setattr(target, f, getattr(pl, f))
                    moved += 1
            target.app_names |= pl.app_names
        dropped_ghosts.append(k)
        del kept.players[k]

    bulk_matched = attach_bulk_app_ids(
        read_csv(TIDY / "api_players_all.csv"), kept.players)

    kept._reindex()
    kept.write(TIDY / PLAYERS, TIDY / CLUBS)
    if dropped_ghosts:
        print("  dropped %d row(s) keyed the old way, after moving %d "
              "identifier(s) onto the live row"
              % (len(dropped_ghosts), moved))

    c = kept.coverage()
    print("wrote %s and %s" % (TIDY / PLAYERS, TIDY / CLUBS))
    if dropped:
        print("  dropped %d key(s) that two players answered to: %s"
              % (len(dropped), ", ".join(sorted(dropped))))
    print("%d players, %d clubs — %.0f%% carry a probable-XI slug, "
          "%.0f%% the second source's, %.0f%% an app id (%d off-market "
          "this run), %.0f%% an understat id (%d newly matched this run)"
          % (c["players"], c["clubs"], 100 * c["ff"], 100 * c["af"],
             100 * c["app"], bulk_matched, 100 * c["understat"],
             understat_matched))
    twins = namesakes(market)
    if twins:
        print("  %d name(s) belong to two players; the ids tell them apart: %s"
              % (len(twins), ", ".join(k for k, _ in twins)))

    clashes = kept.clashes()
    if clashes:
        print("  warn: an identifier two players claim identifies neither, "
              "and these are refused until it is resolved:")
        for idx, ids in clashes.items():
            print("    %-12s %s" % (idx, ", ".join(ids)))


def _selftest() -> None:
    twins = namesakes([{"name": "Álvaro García", "team": "Villarreal"},
                       {"name": "Alvaro Garcia", "team": "Rayo"},
                       {"name": "Pablo Fornals", "team": "Betis"},
                       {"name": "Pablo Fornals", "team": "Betis"}])
    assert twins == [("alvaro garcia", ["Rayo", "Villarreal"])], twins
    assert namesakes([{"name": "A", "team": "X"}, {"name": "A", "team": "X"}]) == []
    assert namesakes([]) == []
    assert namesakes([{"name": "", "team": "X"}, {"name": "", "team": "Y"}]) == []


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

    players = build_players(market, lineups, starters, [], None, clubs)
    xw = Crosswalk(players, clubs)
    assert xw.player(name="Alvaro Fernandez") == "alvaro fernandez"
    assert xw.player(ff_slug="alvaro-fdez") == "alvaro fernandez"
    assert xw.player(af_slug="af-alvaro") == "alvaro fernandez"
    assert xw.player(ff_slug="jonny-castro-ff") == "jonny castro"
    assert players["alvaro fernandez"].club_id == "espanyol"

    assert xw.player(app_id="9999") is None

    from ffcore.league import Config, League
    from ffcore.tidy import Market as _RealMarket

    bulk_mkt = _RealMarket([{"name": "Hugo Duro", "team": "Espanyol",
                             "value": "8534068",
                             "observed_at": "2026-08-01T0000Z"}])
    bulk_lg = League(Config(me="nobody", budget=100e6), {}, [], bulk_mkt)
    bulk_market_rows = [{"name": "Hugo Duro", "slug": "hugo-duro",
                        "team": "Espanyol"}]
    bulk_clubs = build_clubs(bulk_market_rows, [], [])
    bulk_players = build_players(
        bulk_market_rows, [], [],
        [{"player_name": "Hugo Duro", "market_value": "8534068",
          "player_id": "99999999"}],
        bulk_lg, bulk_clubs)
    assert bulk_players["hugo duro"].app_id == "99999999", bulk_players

    stale_market_rows = [{"name": "Alex Sancris", "slug": "alex-sancris",
                          "team": "Getafe"}]
    stale_clubs = build_clubs(stale_market_rows, [], [])
    stale_lg = League(Config(me="nobody", budget=100e6), {}, [],
                      _RealMarket([{"name": "Someone Else", "team": "Getafe",
                                   "value": "500000",
                                   "observed_at": "2026-08-01T0000Z"}]))
    stale_players = build_players(
        stale_market_rows, [], [],
        [{"player_name": "Alex Sancris", "market_value": "551012",
          "player_id": "11766"}],
        stale_lg, stale_clubs)
    assert stale_players["alex sancris"].app_id == "11766", stale_players

    ghost = Player("11766", "Alex Sancris", "getafe")
    ghost_players = {"11766": ghost}
    n = attach_bulk_app_ids(
        [{"player_name": "Álex Sancris", "player_id": "2778"}],
        ghost_players)
    assert n == 1, n
    assert ghost.app_id == "2778", ghost
    ghost.app_id = "already-known"
    assert attach_bulk_app_ids(
        [{"player_name": "Álex Sancris", "player_id": "2778"}],
        ghost_players) == 0
    assert ghost.app_id == "already-known", ghost
    twin_a, twin_b = Player("a", "Pablo Fornals", "betis"), \
                     Player("b", "Pablo Fornals", "villarreal")
    twins = {"a": twin_a, "b": twin_b}
    assert attach_bulk_app_ids(
        [{"player_name": "Pablo Fornals", "player_id": "999"}], twins) == 0
    assert twin_a.app_id == "" and twin_b.app_id == "", (twin_a, twin_b)

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
