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
from ffcore.tidy import TIDY, latest_only, read_csv  # noqa: E402

PLAYERS = "players.csv"
CLUBS = "clubs.csv"


def build_clubs(market, lineups, elo_rows) -> dict:
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

    for slug in sorted({(r.get("team_slug") or "").strip() for r in lineups
                        if (r.get("team_slug") or "").strip()}):
        hit = match_team(slug, teams)
        if hit:
            clubs[norm(hit)].ff_slug = clubs[norm(hit)].ff_slug or slug
            clubs[norm(hit)].aliases.add(slug)

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


def build_players(market, lineups, starters, api_rows, lg, clubs) -> dict:
    """{player_id: Player} — every feed's key for every player it names."""
    from ffcore.league import api_key

    by_club = {c.ff_slug: c.club_id for c in clubs.values() if c.ff_slug}
    out: dict[str, Player] = {}
    for r in market:
        pid = norm(r.get("name"))
        if not pid:
            continue
        out[pid] = Player(pid, (r.get("name") or "").strip(),
                          norm(r.get("team")), (r.get("slug") or "").strip())

    # The two probable-XI feeds. Neither shares a slug space with the market
    # or with each other, so the name is the only way in — and it is done ONCE,
    # here, rather than in every module that wants a probability.
    for r in lineups:
        pid = out.get(norm(r.get("player_name")))
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
        p = out.get(norm(r.get("player_name")))
        if p is not None and slug and not p.ff_slug:
            p.ff_slug = slug

    # The app: integer ids and its own abbreviations. Resolved through the
    # three-step join in ffcore.league — market key, then the ledger breaking a
    # tie, then an exact market value across all of history.
    index = latest_only(lg.market.rows) if lg and lg.market is not None else []
    for r in api_rows:
        raw = (r.get("player_name") or "").strip()
        if not raw:
            continue
        key = api_key(raw, (r.get("manager") or "").strip(),
                      lg.market if lg else None, lg.owner if lg else None,
                      index, r.get("market_value"))
        p = out.get(key) if key else None
        if p is None:
            continue
        p.app_names.add(raw)
        if r.get("player_id") and not p.app_id:
            p.app_id = str(r["player_id"]).strip()
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

    clubs = build_clubs(market, lineups, elo_rows)
    players = build_players(market, lineups, starters, api_rows, lg, clubs)

    fresh = Crosswalk(players, clubs)
    kept = Crosswalk.read(TIDY / PLAYERS, TIDY / CLUBS)
    kept.merge(fresh)
    kept.write(TIDY / PLAYERS, TIDY / CLUBS)

    c = kept.coverage()
    print("wrote %s and %s" % (TIDY / PLAYERS, TIDY / CLUBS))
    print("%d players, %d clubs — %.0f%% carry a probable-XI slug, "
          "%.0f%% the second source's, %.0f%% an app id"
          % (c["players"], c["clubs"], 100 * c["ff"], 100 * c["af"],
             100 * c["app"]))


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

    players = build_players(market, lineups, starters, [], None, clubs)
    xw = Crosswalk(players, clubs)
    # THE ACCENT FOLDS AND THE SLUGS ATTACH. One player, three spellings and
    # two slug spaces, and every one of them arrives at the same key.
    assert xw.player(name="Alvaro Fernandez") == "alvaro fernandez"
    assert xw.player(ff_slug="alvaro-fdez") == "alvaro fernandez"
    assert xw.player(af_slug="af-alvaro") == "alvaro fernandez"
    assert xw.player(market_slug="alvaro-fernandez-m") == "alvaro fernandez"
    # A confirmed line-up names a player the probable-XI feed never listed, and
    # his slug is learned from it rather than lost.
    assert xw.player(ff_slug="jonny-castro-ff") == "jonny castro"
    # The club came off the market row, not off a guess.
    assert players["alvaro fernandez"].club_id == "espanyol"

    # An unlisted feed leaves a gap rather than a wrong answer.
    assert xw.player(app_id="9999") is None

    print("crosswalk self-test OK (8 cases)")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        raise SystemExit(0)
    main()
