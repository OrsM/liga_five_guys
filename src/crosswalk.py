"""
crosswalk.py — build the player and club tables, once, for everything else.

    python src/crosswalk.py            # writes data/tidy/players.csv, clubs.csv
    python src/crosswalk.py --selftest

Resolution happens here, nowhere else: every feed names players/clubs
differently with no slug overlap between them, so this is the one
fuzzy-matching pass everything else reads from. Run after `ingest.py
parse`, a cheap view over the tidy store.

Merges rather than rebuilds — a player named once stays nameable, a
feed that skips a sweep erases nothing.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ffcore.crosswalk import Club, Crosswalk, Player  # noqa: E402
from ffcore.text import norm  # noqa: E402
from ffcore.tidy import (TIDY, latest_only, narrow_by_club, read_csv,  # noqa: E402
                         row_key, shared_names)

PLAYERS = "players.csv"
CLUBS = "clubs.csv"


def build_clubs(market, lineups, elo_rows, fixtures=()) -> dict:
    """{club_id: Club}, keyed on the market's spelling folded — the
    canonical side, since every price is quoted in it. Fixture slugs and
    Club Elo's city names hang off it as aliases.
    """
    from ffcore.fixture import ELO_ALIASES, match_team

    teams = sorted({(r.get("team") or "").strip() for r in market
                    if (r.get("team") or "").strip()})
    clubs = {norm(t): Club(norm(t), t) for t in teams}

    # futbolfantasy's own club id (data-equipo) is on every market row.
    for r in market:
        t = (r.get("team") or "").strip()
        tid = (r.get("team_id") or "").strip()
        if t and tid and norm(t) in clubs:
            clubs[norm(t)].market_id = clubs[norm(t)].market_id or tid

    for slug in sorted({(r.get("team_slug") or "").strip() for r in lineups
                        if (r.get("team_slug") or "").strip()}):
        hit = match_team(slug, teams)
        if hit:
            clubs[norm(hit)].ff_slug = clubs[norm(hit)].ff_slug or slug
            clubs[norm(hit)].aliases.add(slug)

    # analiticafantasy's club id, on both crests of every fixture — learned
    # once by name, then joined on the id from here on.
    for r in fixtures or []:
        for side, col in (("home", "home_id"), ("away", "away_id")):
            nm = (r.get(side) or "").strip()
            aid = (r.get(col) or "").strip()
            if not nm or not aid:
                continue
            hit = match_team(nm, teams)
            if hit and not clubs[norm(hit)].af_id:
                clubs[norm(hit)].af_id = aid
            if hit:
                clubs[norm(hit)].aliases.add(nm)

    for r in elo_rows or []:
        club = (r.get("club") or "").strip()
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
    """[(key, [the clubs that share it])] — every normalised name two
    players share. A shared name is keyed `name@club` (ffcore.tidy.Market
    and the scorer's lookup use the same rule) so the two never collide;
    this list is what that rule fired on. The roster file still needs a
    human to write `alvaro garcia (Rayo)` there to fold the club in.
    """
    seen: dict[str, set] = {}
    for r in market:
        key = norm(r.get("name"))
        if key:
            seen.setdefault(key, set()).add((r.get("team") or "").strip())
    return sorted((k, sorted(v)) for k, v in seen.items() if len(v) > 1)


def build_players(market, lineups, starters, api_rows, lg, clubs) -> dict:
    """{player_id: Player} — every feed's key for every player it names."""

    by_club = {c.ff_slug: c.club_id for c in clubs.values() if c.ff_slug}
    # ff_slug -> the club key the market index uses, so a probable-XI row
    # can say which of two men of one name it means.
    ff_to_market = {c.ff_slug: norm(c.market) for c in clubs.values()
                    if c.ff_slug and c.market}
    # Shared with ffcore.tidy so the market index, player index, scorer,
    # and this table use one rule for which names belong to two men.
    market_shared = shared_names(market)
    out: dict[str, Player] = {}
    club_of: dict[str, str] = {}
    for r in market:
        # row_key(), not norm(name) — keying on the name alone would keep
        # one Player record for two players sharing it.
        pid = row_key(r, market_shared)
        if not pid:
            continue
        out[pid] = Player(pid, (r.get("name") or "").strip(),
                          norm(r.get("team")))
        # Player.club_id gets overwritten with a real club id below; this
        # keeps the market's own spelling for matching a probable-XI row.
        club_of[pid] = norm(r.get("team"))

    shared: dict[str, list] = {}
    for p in out.values():
        shared.setdefault(norm(p.name), []).append(p)

    def by_name(name: str, team_slug: str = ""):
        """The Player a feed row means, or None when the name is two men and
        the row cannot say which."""
        hits = shared.get(norm(name)) or []
        if len(hits) == 1:
            return hits[0]
        want = ff_to_market.get((team_slug or "").strip())
        return narrow_by_club(hits, want, lambda p: club_of.get(p.player_id))

    # The two probable-XI feeds share no slug space with the market or
    # each other, so name is the only way in.
    for r in lineups:
        pid = by_name(r.get("player_name"), r.get("team_slug"))
        slug = (r.get("player_slug") or "").strip()
        if pid is None or not slug:
            continue
        wide = (r.get("source") or "").startswith("futbol")
        if wide and not pid.ff_slug:
            pid.ff_slug = slug
        elif not wide and not pid.af_slug:
            pid.af_slug = slug
        if not pid.club_id and r.get("team_slug") in by_club:
            pid.club_id = by_club[r["team_slug"]]

    # Confirmed line-ups join the wider probable-XI feed by slug; anyone
    # still unmatched is tried on the name.
    ff_index = {p.ff_slug: p for p in out.values() if p.ff_slug}
    for r in starters:
        slug = (r.get("player_slug") or "").strip()
        if slug in ff_index:
            continue
        p = by_name(r.get("player_name"), r.get("team_slug"))
        if p is not None and slug and not p.ff_slug:
            p.ff_slug = slug

    # The app: integer ids and its own abbreviations, resolved through
    # Crosswalk.resolve_api()'s five-step join (id, market key, full name,
    # ledger tie-break, exact market value across history).
    index = latest_only(lg.market.rows) if lg and lg.market is not None else []
    # An id resolved on a past sweep stays resolved (merge, not rebuild).
    # Empty on the first ever run — players.csv doesn't exist yet.
    known = Crosswalk.read(TIDY / "players.csv", TIDY / "clubs.csv")
    for r in api_rows:
        raw = (r.get("player_name") or "").strip()
        if not raw:
            continue
        key = known.resolve_api(raw, (r.get("manager") or "").strip(),
                                lg.market if lg else None,
                                lg.owner if lg else None,
                                index, r.get("market_value"),
                                r.get("player_name_full") or "",
                                r.get("player_id") or "")
        p = out.get(key) if key else None
        if p is None:
            # resolve_api()'s market.key_for() only searches the live market
            # snapshot — a player merely gone quiet there still resolves
            # by name in this function's own `out` (built from full
            # market history). Same by_name() the lineups above use.
            p = by_name(raw)
        if p is None:
            continue
        # The app's display name moves with the player, same as its id.
        for other in out.values():
            if other is not p:
                other.app_names = {n for n in other.app_names
                                   if norm(n) != norm(raw)}
        p.app_names.add(raw)
        pid = str(r.get("player_id") or "").strip()
        if not pid:
            continue
        # The app's own row is the authority for its own id — takes it off
        # whoever held it before, not just adds it (`not p.app_id` alone
        # would leave a stale claim alive after a join gets corrected).
        for other in out.values():
            if other is not p and other.app_id == pid:
                other.app_id = ""
        p.app_id = pid
    return out


def attach_bulk_app_ids(rows, players: dict) -> int:
    """Fill app_id from the bulk player list onto an EXISTING crosswalk
    entry, by name — no market-freshness gate, since the bulk list names
    every competition player regardless of recent activity (unlike
    build_players()'s own join, gated on `market`'s live snapshot).

    Only ever writes a blank; a name two players share is left for the
    market join (which has a price to break the tie), not guessed here.
    """
    by_name: dict[str, list] = {}
    for p in players.values():
        by_name.setdefault(norm(p.name), []).append(p)
    matched = 0
    for r in rows:
        name = (r.get("player_name") or "").strip()
        pid = (r.get("player_id") or "").strip()
        if not name or not pid:
            continue
        hits = by_name.get(norm(name)) or []
        if len(hits) != 1 or hits[0].app_id:
            continue
        hits[0].app_id = pid
        matched += 1
    return matched


def build_understat_ids(rows, players: dict, clubs: dict) -> int:
    """Join Understat's own numeric id onto the crosswalk by name + team —
    understat_id shares no namespace with ff_slug/af_slug/app_id. Scoped
    to one club at a time (a bare name across the whole league would
    confuse every Fernandez in LaLiga), then ffcore.text.resolve()'s
    usual three-pass narrowing within that roster.

    Only ever writes a blank; a name collision within one club stays
    ambiguous rather than picked at random. Returns the count matched.
    """
    from ffcore.fixture import match_team
    from ffcore.text import resolve as text_resolve

    market_teams = sorted({c.market for c in clubs.values() if c.market})
    by_club: dict[str, list] = {}
    for p in players.values():
        by_club.setdefault(p.club_id, []).append(p)

    # One row per Understat player, preferring the live season's spelling
    # and club over last season's (a summer transfer moves him).
    best: dict[str, dict] = {}
    for r in rows:
        uid = (r.get("understat_id") or "").strip()
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
        # text.resolve() wants dict-like rows; wrap rather than duplicate
        # its three-pass narrowing here as an eighth resolver.
        wrapped = [{"name": p.name, "_p": p} for p in candidates]
        hit, _cands = text_resolve(r.get("player_name") or "", wrapped)
        if hit is not None and not hit["_p"].understat_id:
            hit["_p"].understat_id = uid
            matched += 1
    return matched


def main() -> None:
    from ffcore.league import League

    market = latest_only(read_csv(TIDY / "market.csv"))
    lineups = latest_only(read_csv(TIDY / "lineups.csv"))
    starters = read_csv(TIDY / "starters.csv")
    # latest_only PER TABLE, not on the concatenation — api_teams/
    # api_market are swept on different schedules, so a combined-list
    # latest_only would drop every api_teams row not sharing the overall
    # newest stamp.
    api_rows = (latest_only(read_csv(TIDY / "api_teams.csv"))
                + latest_only(read_csv(TIDY / "api_market.csv"))
                + latest_only(read_csv(TIDY / "api_players.csv"))
                # Every competition player, not just ones this league has
                # transacted — no "manager" field, which resolve_api() tolerates.
                # Why: docs/notes/league.md#the-bulk-player-list-and-app_id-with-no-manager
                + latest_only(read_csv(TIDY / "api_players_all.csv")))
    elo_rows = read_csv(TIDY / "elo.csv")
    lg = League.load()

    # Every id the market has ever published, not just today's — a player
    # who left the market stays a real row.
    market_ids = {r["ff_id"] for r in read_csv(TIDY / "market.csv")
                  if (r.get("ff_id") or "").strip()}

    clubs = build_clubs(market, lineups, elo_rows,
                        latest_only(read_csv(TIDY / "fixtures.csv")))
    players = build_players(market, lineups, starters, api_rows, lg, clubs)
    understat_matched = build_understat_ids(
        read_csv(TIDY / "understat_players.csv"), players, clubs)

    fresh = Crosswalk(players, clubs)
    kept = Crosswalk.read(TIDY / PLAYERS, TIDY / CLUBS)
    # A key now known to mean two players is dropped — merging alone
    # would keep a stale bare-name key alive after a split into name@club.
    doubled = {norm(p.name) for p in players.values()
               if "@" in p.player_id}
    dropped = [k for k in list(kept.players) if k in doubled]
    for k in dropped:
        del kept.players[k]
    kept.merge(fresh)

    # One row per player. Merging kept the old name-keyed rows after the
    # key changed to the site's own id, leaving unreachable ghost rows
    # that still hold identifiers learned before the change — move those
    # onto the live row, keyed by the ghost's normalised name, then drop it.
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

    # Every player in the competition, not just today's market — closes the
    # app_id gap for anyone who has gone quiet on the market since whichever
    # past run last saw him fresh (build_players()'s own join needs him in
    # THIS run's market argument, which is latest-only). Why:
    # docs/notes/league.md#the-bulk-player-list-and-app_id-with-no-manager
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
    # A SHARED NAME IS NO LONGER A PROBLEM — the site issues an id per player
    # and this table keys on it, so the two Álvaro Garcías are two rows and
    # always were two players. Printed as a fact rather than a warning.
    twins = namesakes(market)
    if twins:
        print("  %d name(s) belong to two players; the ids tell them apart: %s"
              % (len(twins), ", ".join(k for k, _ in twins)))

    # WHAT IS ACTUALLY WORTH WARNING ABOUT: two players claiming one
    # identifier. That means a join that wrote an id down was wrong and its
    # row is still in the table — the state that had app_id 2614 on both
    # Carlos and Isaac Romero, answering by whichever was reindexed last.
    clashes = kept.clashes()
    if clashes:
        print("  warn: an identifier two players claim identifies neither, "
              "and these are refused until it is resolved:")
        for idx, ids in clashes.items():
            print("    %-12s %s" % (idx, ", ".join(ids)))


def _selftest() -> None:
    # -- one name, two players ---------------------------------------------
    # The key is a normalised name and a name is not unique: LaLiga fields an
    # Álvaro García at Villarreal and another at Rayo, worth 0.50M and 19.76M.
    # This cannot resolve them — it says so, which is the whole point.
    twins = namesakes([{"name": "Álvaro García", "team": "Villarreal"},
                       {"name": "Alvaro Garcia", "team": "Rayo"},
                       {"name": "Pablo Fornals", "team": "Betis"},
                       {"name": "Pablo Fornals", "team": "Betis"}])
    assert twins == [("alvaro garcia", ["Rayo", "Villarreal"])], twins
    # The same player in the same club twice is a repeated row, not a clash.
    assert namesakes([{"name": "A", "team": "X"}, {"name": "A", "team": "X"}]) == []
    assert namesakes([]) == []
    # A row with no name cannot collide with anything.
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
    # THE ACCENT FOLDS AND THE SLUGS ATTACH. One player, three spellings and
    # two slug spaces, and every one of them arrives at the same key.
    assert xw.player(name="Alvaro Fernandez") == "alvaro fernandez"
    assert xw.player(ff_slug="alvaro-fdez") == "alvaro fernandez"
    assert xw.player(af_slug="af-alvaro") == "alvaro fernandez"
    # A confirmed line-up names a player the probable-XI feed never listed, and
    # his slug is learned from it rather than lost.
    assert xw.player(ff_slug="jonny-castro-ff") == "jonny castro"
    # The club came off the market row, not off a guess.
    assert players["alvaro fernandez"].club_id == "espanyol"

    # An unlisted feed leaves a gap rather than a wrong answer.
    assert xw.player(app_id="9999") is None

    # -- the bulk player-list feeds app_id too, with no manager at all -----
    # api_player_N/api_teams/api_activity rows all carry a manager, because
    # they only ever name a player THIS league happened to transact — the
    # bulk list names every player in the competition and has no manager to
    # name, since it isn't anyone's squad. resolve_api()'s ledger-tie-break step
    # is the only one that needs a handle, and it is a fallback tried after
    # name+value already succeed, so an empty handle must resolve exactly
    # the same as a normal transaction row would. Why:
    # docs/notes/league.md#the-bulk-player-list-and-app_id-with-no-manager
    from ffcore.league import Config, League
    from ffcore.tidy import Market as _RealMarket

    bulk_mkt = _RealMarket([{"name": "Hugo Duro", "team": "Espanyol",
                             "value": "8534068",
                             "observed_at": "2026-08-01T0000Z"}])
    bulk_lg = League(Config(me="nobody", budget=100e6), {}, [], bulk_mkt)
    bulk_market_rows = [{"name": "Hugo Duro", "slug": "hugo-duro",
                        "team": "Espanyol"}]
    bulk_clubs = build_clubs(bulk_market_rows, [], [])
    # A made-up id, deliberately not a real one — build_players() reads the
    # actual players.csv off disk, and a real id already claimed by some
    # other real player there would resolve to THAT player first (step 1 of
    # resolve_api(), checked ahead of the name join), which is correct behaviour
    # but makes a real id a trap for a fixture that wants a clean first-time
    # resolution.
    bulk_players = build_players(
        bulk_market_rows, [], [],
        [{"player_name": "Hugo Duro", "market_value": "8534068",
          "player_id": "99999999"}],   # no "manager" key at all
        bulk_lg, bulk_clubs)
    assert bulk_players["hugo duro"].app_id == "99999999", bulk_players

    # -- a player stale on the live market still resolves by name ----------
    # Real case, found 2026-09-12 while measuring the bulk list's coverage:
    # Álex Sancris (ff_id 11766) has not appeared in market.csv since
    # 2026-08-25 — Market.key_for() only searches latest_rows(), so a
    # player who has simply gone quiet on the market (not renamed, not
    # ambiguous, just stale) comes back None from resolve_api() even though his
    # name is a clean, unique match in THIS function's own `out` — built
    # from the full market history, not just the live snapshot. by_name()
    # already exists for exactly this lookup (lineups use it above); this
    # is that same fallback, one more time, for api_rows.
    stale_market_rows = [{"name": "Alex Sancris", "slug": "alex-sancris",
                          "team": "Getafe"}]
    stale_clubs = build_clubs(stale_market_rows, [], [])
    # lg.market deliberately does NOT carry this player — simulates him
    # having dropped out of the live/latest market index.
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

    # -- attach_bulk_app_ids: by name, independent of market freshness -----
    # The above (`by_name` inside build_players()) still needs the player
    # in THIS run's `market` argument at all — and main() passes it
    # latest_only(market.csv), so a player who has dropped off the market
    # entirely (not stale-but-listed, gone) never gets a `Player` in `out`
    # in the first place. He still lives on in `kept.players` (this table
    # merges, it does not rebuild — a player once seen stays), just with
    # no app_id, because the bulk source did not exist on whichever past
    # run last saw him fresh. This attaches straight onto the KEPT crosswalk,
    # with no market/freshness gate at all — the bulk list needs none.
    ghost = Player("11766", "Alex Sancris", "getafe")
    ghost_players = {"11766": ghost}
    n = attach_bulk_app_ids(
        [{"player_name": "Álex Sancris", "player_id": "2778"}],
        ghost_players)
    assert n == 1, n
    assert ghost.app_id == "2778", ghost
    # Never overwrites an app_id a real transaction already resolved.
    ghost.app_id = "already-known"
    assert attach_bulk_app_ids(
        [{"player_name": "Álex Sancris", "player_id": "2778"}],
        ghost_players) == 0
    assert ghost.app_id == "already-known", ghost
    # Two players sharing a name is left alone, not guessed at.
    twin_a, twin_b = Player("a", "Pablo Fornals", "betis"), \
                     Player("b", "Pablo Fornals", "villarreal")
    twins = {"a": twin_a, "b": twin_b}
    assert attach_bulk_app_ids(
        [{"player_name": "Pablo Fornals", "player_id": "999"}], twins) == 0
    assert twin_a.app_id == "" and twin_b.app_id == "", (twin_a, twin_b)

    # -- build_understat_ids: name+team, the same bootstrap every other feed
    # went through, and the same refuse-rather-than-guess on ambiguity -------
    understat = [
        {"understat_id": "701", "player_name": "Alvaro Fernandez",
         "team_title": "Espanyol", "season": "2025"},
        # A row for a club this market does not have is skipped, not guessed
        # onto the nearest name.
        {"understat_id": "999", "player_name": "Nobody Real",
         "team_title": "Nowhere FC", "season": "2025"},
    ]
    matched = build_understat_ids(understat, players, clubs)
    assert matched == 1, matched
    assert players["alvaro fernandez"].understat_id == "701"
    assert xw.player(understat_id="701") is None  # xw was built before the join
    assert Crosswalk(players, clubs).player(understat_id="701") \
        == "alvaro fernandez"
    # A player already joined is never re-guessed on a later sweep.
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
