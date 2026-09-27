"""What to fetch, and which parser reads each page. The parsers live with
their provider: ffcore.futbolfantasy, ffcore.footballdata, ffcore.laliga_api."""
from __future__ import annotations

from functools import lru_cache

from ffcore.footballdata import fd_sources
from ffcore.futbolfantasy import (CAL_KEY, FF_CAL_URL, MARKET_URL, POINTS_URL,
                                  TEAM_URL, TEAMS, match_source,
                                  parse_calendar, parse_market, parse_points,
                                  parse_team, played_sources)
from ffcore.laliga_api import (API_LEAGUES_KEY, API_LEAGUES_URL,
                               league_sources, API_PLAYERS_ALL_URL, api_source,
                               offer_source, parse_api_leagues,
                               parse_api_players_all)
from ffcore.source import Source

__all__ = ["Source", "sources", "source_for"]


def _played_matches(calendar_html: str, _context: dict) -> list[Source]:
    return played_sources(calendar_html)


def _league_pages(leagues_json: str, _context: dict) -> list[Source]:
    return league_sources(leagues_json)


@lru_cache(maxsize=None)
def sources(enabled_only: bool = True) -> list[Source]:
    out = [
        Source("market", "market", MARKET_URL, parse_market),
        Source("points", "points", POINTS_URL, parse_points),
    ]
    out += [Source(f"team_{s}", "lineups", TEAM_URL.format(slug=s),
                   parse_team, cadence="twice_daily")
            for s in TEAMS]
    out += fd_sources()
    out += [Source(CAL_KEY, "matches", FF_CAL_URL, parse_calendar,
                   cadence="daily", follow=_played_matches)]
    out += [Source(API_LEAGUES_KEY, "api_leagues", API_LEAGUES_URL,
                   parse_api_leagues, auth=True, follow=_league_pages)]
    out += [Source("api_players_all", "api_players_all", API_PLAYERS_ALL_URL,
                   parse_api_players_all,
                   cadence="daily", auth=True)]
    return [s for s in out if s.enabled or not enabled_only]


def source_for(key: str) -> Source | None:
    for s in sources(enabled_only=False):
        if s.key == key:
            return s
    return (match_source(key) or api_source(key)
            or offer_source(key))


def _selftest() -> None:
    from ffcore.footballdata import FD_SEASONS_BACK, _FD_CUR, parse_fd_results
    from ffcore.futbolfantasy import (_CAL_FIXTURE, _FIXTURE, _MARKET_FIXTURE,
                                      _POINTS_FIXTURE, parse_starters)
    from ffcore.laliga_api import (_API_LEAGUES_FIXTURE, _API_PLAYERS_ALL_FIXTURE,
                                   parse_api_market,
                                   parse_api_offer)

    assert source_for("fd_2627").parse is parse_fd_results
    for k in ("api_market", "api_teams", "api_activity_0", "api_activity_1"):
        assert source_for(k) is not None, k
        assert source_for(k).table == ("api_activity"
                                       if "activity" in k else k), k
    assert source_for("api_market").parse is parse_api_market

    assert not any(s.auth for s in sources() if not s.key.startswith("api_"))
    assert source_for("api_players_all").parse is parse_api_players_all
    assert source_for("api_players_all").table == "api_players_all"
    assert source_for("api_players_all").cadence == "daily"
    assert source_for("api_players_all").auth is True
    assert source_for("api_offer_24338726").parse is parse_api_offer
    assert source_for("api_offer_24338726").table == "api_offers"
    assert source_for("match_22421-alaves-getafe").parse is parse_starters
    assert source_for("api_lineup_38").table == "api_lineup"

    cal = source_for(CAL_KEY).follow(_CAL_FIXTURE, {})
    assert [s.key for s in cal] == [s.key for s in played_sources(_CAL_FIXTURE)] \
        and cal, "the calendar follows to the matches it says were played"
    lgs = source_for(API_LEAGUES_KEY).follow(_API_LEAGUES_FIXTURE, {})
    assert [s.key for s in lgs] == [s.key for s in league_sources(
        _API_LEAGUES_FIXTURE)] and lgs, "the leagues list follows to its pages"
    assert all(s.follow is None for s in sources()
               if s.key not in (CAL_KEY, API_LEAGUES_KEY))

    reg = sources()
    assert len(reg) == 5 + len(TEAMS) + FD_SEASONS_BACK + 1 == 29, len(reg)
    assert {s.cadence for s in reg if s.key.startswith("team_")} \
        == {"twice_daily"}
    assert {s.cadence for s in reg if s.key in ("market", "points")} \
        == {"every_run"}
    assert {s.key for s in reg} >= {"market", "points", "team_barcelona"}
    assert len({s.key for s in reg}) == len(reg)
    assert {s.table for s in reg} == {"market", "points", "lineups",
                                      "matches",
                                      "api_leagues", "results_history",
                                      "api_players_all"}
    assert source_for("team_celta").parse is parse_team
    assert source_for("gone") is None

    samples = {"market": _MARKET_FIXTURE, "points": _POINTS_FIXTURE,
               CAL_KEY: _CAL_FIXTURE, API_LEAGUES_KEY: _API_LEAGUES_FIXTURE,
               "api_players_all": _API_PLAYERS_ALL_FIXTURE}
    for s in fd_sources():
        samples[s.key] = _FD_CUR
    for s in reg:
        html = samples.get(s.key, _FIXTURE)
        assert s.parse(html, "2026-01-01T0000Z", s.key), s.key

    print("sources.py self-test OK")


if __name__ == "__main__":
    _selftest()
