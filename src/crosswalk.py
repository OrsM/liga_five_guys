"""
crosswalk.py — build the player and club tables, once, for everything else.

    python src/crosswalk.py            # writes data/tidy/players.csv, clubs.csv
    python src/crosswalk.py --selftest

RESOLUTION HAPPENS HERE AND NOWHERE ELSE. Every feed names players and clubs
its own way and none of the slug namespaces overlap, so somebody has to do the
fuzzy work. Until this existed everybody did, separately, on whatever subset of
the evidence they happened to have loaded — which is how decide.py came to hide
five rival players behind a weaker join than the one already in league.py.

Run after `ingest.py parse` and before anything that reads a player: it is a
view over the tidy store, and it is cheap.

IT MERGES, IT DOES NOT REBUILD. A player the API named once and never again is
nameable forever after the run that saw him; a feed that skips a sweep erases
nothing. Coverage is printed every run, because a crosswalk nobody measures is
one that quietly stops covering a feed the day its format changes.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ffcore.crosswalk import Club, Crosswalk, Player  # noqa: E402
from ffcore.text import norm  # noqa: E402
from ffcore.tidy import (TIDY, latest_only, read_csv,  # noqa: E402
                         row_key, shared_names)

PLAYERS = "players.csv"
CLUBS = "clubs.csv"


def build_clubs(market, lineups, elo_rows, fixtures=()) -> dict:
    """{club_id: Club}, keyed on the market's spelling folded.

    The market is the canonical side because it is the one every price in this
    repo is quoted in. The fixture page's slug and Club Elo's city name hang
    off it as aliases — including the two Elo insists on naming after the city
    rather than the club, which used to be a constant inside fixture.py.
    """
    from ffcore.fixture import ELO_ALIASES, match_team

    teams = sorted({(r.get("team") or "").strip() for r in market
                    if (r.get("team") or "").strip()})
    clubs = {norm(t): Club(norm(t), t) for t in teams}

    # futbolfantasy states its own club id on every market row (data-equipo),
    # so the club a price belongs to is an id, not a spelling.
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

    # analiticafantasy states its club id on both crests of every fixture.
    # Learned ONCE, by the name match, and written down — after which the
    # fixture board joins on the id and never on "Celta" against "Celta Vigo".
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
    """[(key, [the clubs that share it])] — every key that is two players.

    THE KEY IS A NORMALISED NAME, and a name is not unique. LaLiga fields an
    Álvaro García at Villarreal and another at Rayo; to this repo they are one
    player, with one price history built out of both of their rows, and
    whichever row a lookup reaches first decides what he is worth. On
    2026-08-19 that was 4 keys of 647, one of them owned — SusoGattuso's
    Álvaro García, 19.76M or 0.50M depending on which of them answered.

    THEY ARE KEPT APART NOW. Since 2026-08-20 a shared name is keyed
    `name@club` — by ffcore.tidy.Market, by the player index, by the scorer's
    lookup and here, all from one rule so the four cannot disagree. This list
    is what that rule fired on, which is worth printing for the same reason
    the freshness lights are: a silent mechanism is one nobody checks.

    What still needs a human is the roster file, the one place a name is
    typed: write `alvaro garcia (Rayo)` there and the club is folded into the
    key. A shared name without one names either man.
    """
    seen: dict[str, set] = {}
    for r in market:
        key = norm(r.get("name"))
        if key:
            seen.setdefault(key, set()).add((r.get("team") or "").strip())
    return sorted((k, sorted(v)) for k, v in seen.items() if len(v) > 1)


def build_players(market, lineups, starters, api_rows, lg, clubs) -> dict:
    """{player_id: Player} — every feed's key for every player it names."""
    from ffcore.league import api_key, app_ids_known

    by_club = {c.ff_slug: c.club_id for c in clubs.values() if c.ff_slug}
    # ff_slug -> the club key the market index uses, so a probable-XI row can
    # say WHICH of two men of one name it means.
    ff_to_market = {c.ff_slug: norm(c.market) for c in clubs.values()
                    if c.ff_slug and c.market}
    # ONE RULE FOR THE KEY, shared with ffcore.tidy so the market index, the
    # player index, the scorer and this table cannot disagree about which
    # names belong to two men.
    market_shared = shared_names(market)
    out: dict[str, Player] = {}
    club_of: dict[str, str] = {}
    for r in market:
        # THE MARKET'S OWN KEY, not norm(name). Two players share a name in
        # this league and keying on the name alone kept one Player record for
        # the pair — whichever row came last won, and the other man's club,
        # slug and price history were simply gone.
        pid = row_key(r, market_shared)
        if not pid:
            continue
        out[pid] = Player(pid, (r.get("name") or "").strip(),
                          norm(r.get("team")))
        # Kept beside the record because Player.club_id is overwritten with a
        # real club id further down, and the market's own spelling is what a
        # probable-XI row has to be matched against.
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
        if not want:
            return None
        found = [p for p in hits if club_of.get(p.player_id) == want]
        return found[0] if len(found) == 1 else None

    # The two probable-XI feeds. Neither shares a slug space with the market
    # or with each other, so the name is the only way in — and it is done ONCE,
    # here, rather than in every module that wants a probability.
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

    # Confirmed line-ups come off the same site as the wider probable-XI feed,
    # so they join to it by slug at 93% — far better than either joins to the
    # market by name. Anyone still unmatched is tried on the name.
    ff_index = {p.ff_slug: p for p in out.values() if p.ff_slug}
    for r in starters:
        slug = (r.get("player_slug") or "").strip()
        if slug in ff_index:
            continue
        p = by_name(r.get("player_name"), r.get("team_slug"))
        if p is not None and slug and not p.ff_slug:
            p.ff_slug = slug

    # The app: integer ids and its own abbreviations. Resolved through the
    # three-step join in ffcore.league — market key, then the ledger breaking a
    # tie, then an exact market value across all of history.
    index = latest_only(lg.market.rows) if lg and lg.market is not None else []
    # WHAT THE LAST BUILD RESOLVED, read back in. This table merges rather
    # than rebuilds, so an id resolved on any past sweep stays resolved — and
    # the build that produced it is exactly the caller that should not have
    # to work it out again. On the first ever run this is {}.
    known = app_ids_known()
    for r in api_rows:
        raw = (r.get("player_name") or "").strip()
        if not raw:
            continue
        key = api_key(raw, (r.get("manager") or "").strip(),
                      lg.market if lg else None, lg.owner if lg else None,
                      index, r.get("market_value"),
                      r.get("player_name_full") or "",
                      known, r.get("player_id") or "")
        p = out.get(key) if key else None
        if p is None:
            continue
        # The app's display name belongs to the player the app hangs it on,
        # for the same reason its id does. Isaac Romero kept the alias
        # "C. Romero" after losing the id to Carlos, and app_name is the key
        # sim.py's market model looks players up by.
        for other in out.values():
            if other is not p:
                other.app_names = {n for n in other.app_names
                                   if norm(n) != norm(raw)}
        p.app_names.add(raw)
        pid = str(r.get("player_id") or "").strip()
        if not pid:
            continue
        # THE APP'S OWN ROW IS THE AUTHORITY FOR THE APP'S OWN ID, and it
        # takes the id OFF whoever held it before. `not p.app_id` alone only
        # ever added: when a name join wrote 2614 onto Isaac Romero and the
        # fixed join later gave 2614 to Carlos, both kept it and the index
        # answered with whichever it saw last. A stale claim outliving the
        # bug that made it is how a fix fails to take.
        for other in out.values():
            if other is not p and other.app_id == pid:
                other.app_id = ""
        p.app_id = pid
    return out


def main() -> None:
    from ffcore.league import League

    market = latest_only(read_csv(TIDY / "market.csv"))
    lineups = latest_only(read_csv(TIDY / "lineups.csv"))
    starters = read_csv(TIDY / "starters.csv")
    api_rows = (read_csv(TIDY / "api_teams.csv")
                + read_csv(TIDY / "api_market.csv")
                + read_csv(TIDY / "api_players.csv"))
    elo_rows = read_csv(TIDY / "elo.csv")
    lg = League.load()

    # Every id the market has ever published — a player who left is still a
    # real row, and keying on "in today's market" would drop him each sweep.
    market_ids = {r["ff_id"] for r in read_csv(TIDY / "market.csv")
                  if (r.get("ff_id") or "").strip()}

    clubs = build_clubs(market, lineups, elo_rows,
                        latest_only(read_csv(TIDY / "fixtures.csv")))
    players = build_players(market, lineups, starters, api_rows, lg, clubs)

    fresh = Crosswalk(players, clubs)
    kept = Crosswalk.read(TIDY / PLAYERS, TIDY / CLUBS)
    # A KEY THAT IS NOW KNOWN TO BE TWO PLAYERS IS DROPPED. This table merges
    # rather than rebuilds, which is what makes an id resolved once stay
    # resolved — and it is also what kept the bare `alvaro garcia` alive after
    # the pair were split into name@club. It carried the app id of one of
    # them, so the ledger went on joining a rival's 20.23M defender to a key
    # that means either man. Only ever drops a key the market itself now says
    # is ambiguous; nothing else about the merge changes.
    doubled = {norm(p.name) for p in players.values()
               if "@" in p.player_id}
    dropped = [k for k in list(kept.players) if k in doubled]
    for k in dropped:
        del kept.players[k]
    kept.merge(fresh)

    # ONE ROW PER PLAYER. This table merges rather than rebuilds, so when the
    # key changed from a normalised name to the site's own id it did not
    # replace the old rows — it kept them, and 654 players became 1,311. The
    # name-keyed half is unreachable: nothing looks a player up by name any
    # more. What it still held was identifiers learned before the change,
    # mostly analiticafantasy's, so they are moved onto the live row first
    # and only then is the ghost dropped. Matched on the ghost's own key,
    # which IS the normalised name the live row carries.
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
          "%.0f%% the second source's, %.0f%% an app id"
          % (c["players"], c["clubs"], 100 * c["ff"], 100 * c["af"],
             100 * c["app"]))
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

    print("crosswalk self-test OK (12 cases)")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        raise SystemExit(0)
    main()
