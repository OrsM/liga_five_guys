
from __future__ import annotations

import math
import statistics
from datetime import datetime
from typing import NamedTuple


from ffcore.parse import money
from ffcore.text import match_one, norm
from ffcore.tidy import kickoff_stamp

FIX_BAND = 0.12
HOME_EDGE = 0.04
MIN_HOME_EDGE_MATCHES = 50


def fit_home_edge(results_history: list[dict],
                  matches: list[dict] = ()) -> tuple[float, str]:
    home_g = away_g = n = 0
    for r in results_history:
        try:
            hg, ag = int(r["home_goals"]), int(r["away_goals"])
        except (KeyError, ValueError, TypeError):
            continue
        home_g += hg
        away_g += ag
        n += 1
    seen = set()
    for r in matches:
        score = (r.get("score") or "").strip()
        mid, jor = r.get("match_id"), r.get("jornada")
        if not score or (mid, jor) in seen:
            continue
        seen.add((mid, jor))
        try:
            hs, aws = (int(x) for x in score.split("-"))
        except ValueError:
            continue
        home_g += hs
        away_g += aws
        n += 1
    if n < MIN_HOME_EDGE_MATCHES or away_g <= 0:
        return HOME_EDGE, ("only %d real scored matches (need %d) — "
                          "keeping the %.2f default"
                          % (n, MIN_HOME_EDGE_MATCHES, HOME_EDGE))
    ratio = home_g / away_g
    edge = (ratio - 1) / (ratio + 1)
    return edge, ("fit from %d real matches (home %.2f, away %.2f "
                 "goals/match, ratio %.3f)" % (n, home_g / n, away_g / n,
                                               ratio))


class Match(NamedTuple):
    opponent: str
    home: bool
    kickoff: datetime
    atk_factor: float
    def_factor: float
    rank: int
    of: int
    basis: str = "value"
    gap: float | None = None


ELO_ALIASES = {"athletic": "Bilbao", "racing": "Santander"}


def elo_strength(market_teams, elo_rows) -> dict[str, float] | None:
    have = {}
    for r in elo_rows:
        club = (r.get("club") or "").strip()
        try:
            rating = float(r.get("elo"))
        except (TypeError, ValueError):
            continue
        if club:
            have[club] = rating
    if not have:
        return None
    out = {}
    for team in market_teams:
        club = match_team(team, list(have))
        if club is None:
            alias = ELO_ALIASES.get(norm(team))
            club = alias if alias in have else None
        if club is None:
            return None
        out[team] = have[club]
    return out


def team_strength(market: list[dict]) -> dict[str, float]:
    tot: dict[str, float] = {}
    for r in market:
        team = (r.get("team") or "").strip()
        if team:
            tot[team] = tot.get(team, 0.0) + (money(r.get("value")) or 0.0)
    return tot


MIN_AD_MATCHES = 10

XG_CLUB_PSEUDO_MATCHES = 10.0


def xg_club_attack(understat_rows, xw) -> dict[str, float]:
    from collections import defaultdict

    xg: dict[str, float] = defaultdict(float)
    mins: dict[str, float] = defaultdict(float)
    for r in understat_rows:
        if "F" not in (r.get("position") or ""):
            continue
        team = r.get("team_title") or ""
        if not team or "," in team:
            continue
        m = float(r.get("minutes") or 0)
        if m <= 0:
            continue
        xg[team] += float(r.get("xg") or 0) + float(r.get("xa") or 0)
        mins[team] += m
    rate = {t: xg[t] / mins[t] * 90 for t in xg if mins[t] > 0}
    if not rate:
        return {}
    league_avg = sum(rate.values()) / len(rate)
    if not league_avg:
        return {}
    out = {}
    for name, v in rate.items():
        cid = xw.club(name=name) if xw is not None else None
        slug = xw.clubs[cid].ff_slug if cid and cid in xw.clubs else None
        if slug:
            out[slug] = v / league_avg
    return out


def attack_defense(results: list[dict], teams,
                   xg_attack: dict[str, float] | None = None,
                   xg_pseudo: float = XG_CLUB_PSEUDO_MATCHES
                   ) -> dict[str, tuple[float, float]]:
    scored: dict[str, float] = {}
    conceded: dict[str, float] = {}
    played: dict[str, int] = {}
    total_goals, total_matches = 0.0, 0
    for r in results:
        home, away = (r.get("home") or "").strip(), (r.get("away") or "").strip()
        try:
            hg, ag = float(r["home_goals"]), float(r["away_goals"])
        except (TypeError, ValueError, KeyError):
            continue
        if home:
            scored[home] = scored.get(home, 0.0) + hg
            conceded[home] = conceded.get(home, 0.0) + ag
            played[home] = played.get(home, 0) + 1
        if away:
            scored[away] = scored.get(away, 0.0) + ag
            conceded[away] = conceded.get(away, 0.0) + hg
            played[away] = played.get(away, 0) + 1
        total_goals += hg + ag
        total_matches += 1
    if not total_matches:
        return {}
    league_avg = total_goals / (total_matches * 2)
    if not league_avg:
        return {}
    out = {}
    for t in teams:
        n = played.get(t, 0)
        if n < MIN_AD_MATCHES:
            continue
        atk = scored[t] / n / league_avg
        xg = xg_attack.get(t) if xg_attack else None
        if xg is not None:
            atk = (n * atk + xg_pseudo * xg) / (n + xg_pseudo)
        out[t] = (atk, conceded[t] / n / league_avg)
    return out


def club_volatility(results: list[dict], teams) -> dict[str, float]:
    involvement: dict[str, list[float]] = {}
    for r in results:
        home, away = (r.get("home") or "").strip(), (r.get("away") or "").strip()
        try:
            hg, ag = float(r["home_goals"]), float(r["away_goals"])
        except (TypeError, ValueError, KeyError):
            continue
        if home:
            involvement.setdefault(home, []).append(hg + ag)
        if away:
            involvement.setdefault(away, []).append(hg + ag)
    out = {}
    for t in teams:
        vals = involvement.get(t, [])
        n = len(vals)
        if n < MIN_AD_MATCHES:
            continue
        mean = statistics.mean(vals)
        if mean <= 0:
            continue
        cv = statistics.pstdev(vals) / mean
        out[t] = cv / math.sqrt(n)
    return out


def difficulty(strength: dict[str, float]) -> dict[str, tuple[float, int]]:
    order = sorted(strength, key=lambda t: -strength[t])
    n = len(order)
    out: dict[str, tuple[float, int]] = {}
    for i, team in enumerate(order):
        pos = 0.0 if n < 2 else (2.0 * i / (n - 1)) - 1.0
        out[team] = (1.0 + FIX_BAND * pos, i + 1)
    return out


def match_team(side: str, teams) -> str | None:
    return match_one(side, teams)


def fixture_board(market: list[dict], fixtures: list[dict],
                  now: datetime, elo_rows=None, xw=None,
                  results=None, understat_rows=None) -> dict[str, Match]:
    ratings = _difficulty_ratings(market, elo_rows, xw, results,
                                  understat_rows)
    teams = ratings.teams
    board: dict[str, Match] = {}

    for r in fixtures:
        when = kickoff_stamp(r.get("kickoff"))
        if not when or when <= now:
            continue
        for side, other, sid, oid, home in (
                (r.get("home"), r.get("away"), r.get("home_id"),
                 r.get("away_id"), True),
                (r.get("away"), r.get("home"), r.get("away_id"),
                 r.get("home_id"), False)):
            team = ratings.by_af.get((sid or "").strip()) or match_team(
                side or "", teams)
            if not team:
                continue
            prev = board.get(team)
            if prev and prev.kickoff <= when:
                continue
            opp = ratings.by_af.get((oid or "").strip()) or match_team(
                other or "", teams)
            board[team] = _match_for(ratings, team, opp, other or "?",
                                     home, when)
    return board


class _Ratings(NamedTuple):
    teams: list
    elo: dict | None
    diff: dict
    basis: str
    ad_by_slug: dict
    slug_of: dict
    by_af: dict


def _difficulty_ratings(market: list[dict], elo_rows=None, xw=None,
                        results=None, understat_rows=None) -> _Ratings:
    value = team_strength(market)
    teams = list(value)
    slug_of = {c.market: c.ff_slug for c in xw.clubs.values()
              if c.market and c.ff_slug} if xw is not None else {}
    xg_attack = (xg_club_attack(understat_rows, xw)
                if understat_rows and xw is not None else {})
    ad_by_slug = (attack_defense(results, list(slug_of.values()), xg_attack)
                 if results and slug_of else {})
    by_af = {}
    if xw is not None:
        for c in xw.clubs.values():
            if c.af_id and c.market:
                hit = match_team(c.market, teams)
                if hit:
                    by_af[c.af_id] = hit
    elo = elo_strength(teams, elo_rows) if elo_rows else None
    strength = elo if elo is not None else value
    basis = "elo" if elo is not None else "value"
    diff = difficulty(strength)
    return _Ratings(teams=teams, elo=elo, diff=diff, basis=basis,
                    ad_by_slug=ad_by_slug, slug_of=slug_of, by_af=by_af)


def _match_for(ratings: "_Ratings", team: str, opp: str, opp_name: str,
              home: bool, when: datetime) -> Match:
    base, rank = ratings.diff.get(opp, (1.0, 0)) if opp else (1.0, 0)
    edge = (1.0 + HOME_EDGE) if home else (1.0 - HOME_EDGE)
    gap = (ratings.elo[team] - ratings.elo[opp]
          if ratings.elo is not None and opp in ratings.elo else None)
    opp_ad = ratings.ad_by_slug.get(ratings.slug_of.get(opp)) if opp else None
    if opp_ad is not None:
        atk_base, def_base = opp_ad[1], 1.0 / opp_ad[0]
        row_basis = "attack_defense"
    else:
        atk_base = def_base = base
        row_basis = ratings.basis if opp else "none"
    return Match(opponent=opp_name, home=home, kickoff=when,
                atk_factor=atk_base * edge, def_factor=def_base * edge,
                rank=rank, of=len(ratings.teams), basis=row_basis, gap=gap)


def season_board(market: list[dict], matches: list[dict], jornadas,
                 now: datetime, elo_rows=None, xw=None,
                 results=None, understat_rows=None
                 ) -> dict[int, dict[str, Match]]:
    ratings = _difficulty_ratings(market, elo_rows, xw, results,
                                  understat_rows)
    teams = ratings.teams
    want = set(jornadas)
    board: dict[int, dict[str, Match]] = {j: {} for j in want}
    for r in matches:
        j = r.get("jornada") or ""
        if not j.isdigit() or int(j) not in want:
            continue
        j = int(j)
        for side, other, home in ((r.get("home"), r.get("away"), True),
                                  (r.get("away"), r.get("home"), False)):
            team = match_team(side or "", teams)
            if not team or team in board[j]:
                continue
            opp = match_team(other or "", teams)
            board[j][team] = _match_for(ratings, team, opp, other or "?",
                                        home, now)
    return board


def _selftest() -> None:
    mk = [{"team": "Rich", "value": "100.00M"},
          {"team": "Rich", "value": "100.00M"},
          {"team": "Mid", "value": "50.00M"},
          {"team": "Poor", "value": "10.00M"},
          {"team": "", "value": "999.00M"}]

    st = team_strength(mk)
    assert st == {"Rich": 200e6, "Mid": 50e6, "Poor": 10e6}, st
    assert "" not in st

    diff = difficulty(st)
    assert diff["Rich"][0] == 1.0 - FIX_BAND
    assert diff["Poor"][0] == 1.0 + FIX_BAND
    assert diff["Mid"][0] == 1.0
    assert diff["Rich"][1] == 1 and diff["Poor"][1] == 3
    assert difficulty({"Only": 1.0})["Only"] == (1.0, 1)

    hist_rows = [{"home_goals": "2", "away_goals": "1"}] * 60
    edge, why = fit_home_edge(hist_rows)
    assert abs(edge - (1 / 3)) < 1e-9, (edge, why)
    assert "fit from 60 real matches" in why, why

    dup_matches = [{"match_id": "m1", "jornada": "1", "score": "2-1"}] * 5
    edge2, why2 = fit_home_edge(hist_rows[:49], dup_matches)
    assert abs(edge2 - (1 / 3)) < 1e-9, (edge2, why2)
    assert "50 real matches" in why2, why2

    small_edge, small_why = fit_home_edge(hist_rows[:10])
    assert small_edge == HOME_EDGE, (small_edge, small_why)
    assert "keeping the" in small_why, small_why

    results = (
        [{"home": "Strong", "away": "Weak", "home_goals": "3", "away_goals": "0"},
         {"home": "Weak", "away": "Strong", "home_goals": "0", "away_goals": "2"}]
        * 5
        + [{"home": "Thin", "away": "Weak", "home_goals": "1", "away_goals": "1"}]
          * 9
    )
    ad = attack_defense(results, ["Strong", "Weak", "Thin"])
    assert set(ad) == {"Strong", "Weak"}, ad
    assert ad["Strong"][0] > 1.0 > ad["Weak"][0], ad
    assert ad["Weak"][1] > 1.0 > ad["Strong"][1], ad
    only = attack_defense(
        [{"home": "Strong", "away": "", "home_goals": "5", "away_goals": "0"}]
        * MIN_AD_MATCHES,
        ["Strong"])
    assert set(only) == {"Strong"}, only
    assert attack_defense([], ["Strong"]) == {}
    assert attack_defense([{"home": "A", "away": "B", "home_goals": "",
                            "away_goals": ""}], ["A", "B"]) == {}

    from ffcore.crosswalk import Crosswalk, Club

    xw3 = Crosswalk({}, {
        "strong club": Club("strong club", market="Strong", ff_slug="Strong"),
        "weak club": Club("weak club", market="Weak", ff_slug="Weak"),
    })
    und_rows = (
        [{"position": "F", "team_title": "Strong", "minutes": "900",
          "xg": "9", "xa": "0"}] * 3
        + [{"position": "F", "team_title": "Weak", "minutes": "900",
           "xg": "1", "xa": "0"}] * 3
        + [{"position": "D", "team_title": "Strong", "minutes": "900",
           "xg": "20", "xa": "0"}]
        + [{"position": "F", "team_title": "Strong,Weak",
           "minutes": "900", "xg": "5", "xa": "0"}]
    )
    xga = xg_club_attack(und_rows, xw3)
    assert xga["Strong"] > 1.0 > xga["Weak"], xga
    assert xg_club_attack([], xw3) == {}
    assert xg_club_attack(und_rows, None) == {}

    real_only = attack_defense(results, ["Strong", "Weak"])
    blended = attack_defense(results, ["Strong", "Weak"], xga)
    assert blended["Strong"][0] > 1.0 > blended["Weak"][0], blended
    assert blended["Strong"][0] != real_only["Strong"][0], \
        "xg_attack must move the number, not be silently ignored"
    assert blended["Strong"][1] == real_only["Strong"][1]
    assert blended["Weak"][1] == real_only["Weak"][1]
    partial = attack_defense(results, ["Strong", "Weak"], {"Strong": 2.0})
    assert partial["Weak"] == real_only["Weak"], partial
    heavy = attack_defense(results, ["Strong"], xga, xg_pseudo=1e6)
    light = attack_defense(results, ["Strong"], xga, xg_pseudo=1e-6)
    assert abs(heavy["Strong"][0] - xga["Strong"]) < 1e-3, heavy
    assert abs(light["Strong"][0] - real_only["Strong"][0]) < 1e-3, light

    steady = [{"home": "Steady", "away": "Weak", "home_goals": "1",
              "away_goals": "1"}] * MIN_AD_MATCHES
    wild = ([{"home": "Wild", "away": "Weak", "home_goals": "0",
             "away_goals": "0"}] * (MIN_AD_MATCHES // 2)
           + [{"home": "Wild", "away": "Weak", "home_goals": "8",
              "away_goals": "0"}] * (MIN_AD_MATCHES // 2))
    vol = club_volatility(steady + wild, ["Steady", "Wild", "Thin"])
    assert vol["Steady"] == 0.0, vol
    assert vol["Wild"] > 0.0, vol
    assert "Thin" not in vol, vol
    assert club_volatility([], ["Steady"]) == {}
    assert club_volatility(
        [{"home": "Empty", "away": "X", "home_goals": "0",
          "away_goals": "0"}] * MIN_AD_MATCHES, ["Empty"]) == {}

    teams = ["Celta", "Betis", "Atlético", "Real Madrid", "Real Sociedad"]
    assert match_team("Celta Vigo", teams) == "Celta"
    assert match_team("Real Betis", teams) == "Betis"
    assert match_team("Atletico Madrid", teams) == "Atlético"
    assert match_team("Real Madrid", teams) == "Real Madrid"
    assert match_team("Real Sociedad", teams) == "Real Sociedad"
    assert match_team("Nowhere FC", teams) is None
    assert match_team("", teams) is None
    assert match_team("Real", ["Real Madrid", "Real Sociedad"]) is None

    now = datetime.fromisoformat("2026-08-15T12:00:00+00:00")
    fx = [{"kickoff": "2026-08-14T19:00:00+00:00",
           "home": "Rich", "away": "Poor"},
          {"kickoff": "2026-08-20T19:00:00+00:00",
           "home": "Mid", "away": "Rich"},
          {"kickoff": "2026-08-16T19:00:00+00:00",
           "home": "Poor", "away": "Mid"}]
    board = fixture_board(mk, fx, now)

    assert board["Rich"].opponent == "Mid" and not board["Rich"].home
    assert board["Mid"].opponent == "Poor", board["Mid"]
    assert board["Mid"].kickoff.day == 16
    assert abs(board["Mid"].atk_factor
               - (1.0 + FIX_BAND) * (1.0 - HOME_EDGE)) < 1e-9
    assert board["Mid"].atk_factor == board["Mid"].def_factor
    assert abs(board["Poor"].atk_factor - (1.0 * (1.0 + HOME_EDGE))) < 1e-9
    assert board["Mid"].rank == 3 and board["Mid"].of == 3

    assert fixture_board(mk, [], now) == {}
    assert fixture_board(mk, [{"kickoff": "2026-08-20T19:00:00+00:00",
                               "home": "Nowhere FC",
                               "away": "Elsewhere FC"}], now) == {}
    solo = fixture_board(mk, [{"kickoff": "2026-08-20T19:00:00+00:00",
                               "home": "Mid", "away": "Nowhere FC"}], now)
    assert solo["Mid"].rank == 0
    assert abs(solo["Mid"].atk_factor - (1.0 + HOME_EDGE)) < 1e-9

    from ffcore.crosswalk import Club, Crosswalk

    xw2 = Crosswalk({}, {
        "rich": Club("rich", market="Rich", ff_slug="rich-slug"),
        "mid": Club("mid", market="Mid", ff_slug="mid-slug"),
        "poor": Club("poor", market="Poor", ff_slug="poor-slug"),
    })
    ad_results = [{"home": "rich-slug", "away": "x-slug", "home_goals": "3",
                  "away_goals": "5"}] * MIN_AD_MATCHES
    fx2 = [{"kickoff": "2026-08-20T19:00:00+00:00",
           "home": "Mid", "away": "Rich"}]
    real = fixture_board(mk, fx2, now, results=ad_results, xw=xw2)
    assert real["Mid"].basis == "attack_defense", real["Mid"]
    assert real["Mid"].def_factor != real["Mid"].atk_factor, real["Mid"]
    assert abs(real["Mid"].def_factor - (1.0 / 0.75) * (1.0 + HOME_EDGE)) \
        < 1e-9, real["Mid"]
    assert abs(real["Mid"].atk_factor - 1.25 * (1.0 + HOME_EDGE)) < 1e-9, \
        real["Mid"]
    assert real["Rich"].basis in ("value", "none")
    no_xw = fixture_board(mk, fx2, now, results=ad_results)
    assert no_xw["Mid"].basis != "attack_defense", no_xw["Mid"]

    elo = [{"club": "Poor", "elo": "1900"}, {"club": "Mid", "elo": "1700"},
           {"club": "Rich", "elo": "1500"}]
    st_elo = elo_strength(["Rich", "Mid", "Poor"], elo)
    assert st_elo == {"Rich": 1500.0, "Mid": 1700.0, "Poor": 1900.0}, st_elo
    assert difficulty(st_elo)["Poor"][1] == 1

    eb = fixture_board(mk, fx, now, elo)
    assert eb["Rich"].basis == "elo", eb["Rich"]
    assert eb["Mid"].rank == 1 and board["Mid"].rank == 3, (eb["Mid"], board)
    assert eb["Mid"].gap == 1700.0 - 1900.0, eb["Mid"]
    assert board["Mid"].gap is None and board["Mid"].basis == "value"

    assert elo_strength(["Rich", "Mid", "Poor"],
                        [{"club": "Poor", "elo": "1900"}]) is None
    assert fixture_board(mk, fx, now,
                         [{"club": "Poor", "elo": "1900"}])["Rich"].basis \
        == "value"
    assert elo_strength(["Rich"], [{"club": "Rich", "elo": ""}]) is None

    city = [{"club": "Bilbao", "elo": "1800"},
            {"club": "Santander", "elo": "1600"}]
    assert elo_strength(["Athletic", "Racing"], city) \
        == {"Athletic": 1800.0, "Racing": 1600.0}
    assert elo_strength(["Athletic", "Racing"],
                        [{"club": "Bilbao", "elo": "1800"}]) is None
    assert elo_strength(["Athletic"], [{"club": "Athletic", "elo": "1750"}]) \
        == {"Athletic": 1750.0}
    assert elo_strength(["Rich"], []) is None

    market_20 = ["Alavés", "Athletic", "Atlético", "Barcelona", "Betis",
                 "Celta", "Deportivo", "Elche", "Espanyol", "Getafe",
                 "Levante", "Málaga", "Osasuna", "Racing", "Rayo",
                 "Real Madrid", "Real Sociedad", "Sevilla", "Valencia",
                 "Villarreal"]
    elo_20 = ["Alaves", "Athletic Club", "Atlético", "Barcelona", "Betis",
              "Celta", "Depor", "Elche", "Espanyol", "Getafe", "Levante",
              "Malaga", "Osasuna", "Santander", "Rayo Vallecano",
              "Real Madrid", "Real Sociedad", "Sevilla", "Valencia",
              "Villarreal"]
    full = elo_strength(market_20, [{"club": c, "elo": str(1500 + i)}
                                    for i, c in enumerate(elo_20)])
    assert full is not None and len(full) == 20, full
    assert full["Racing"] == 1513.0 and full["Athletic"] == 1501.0, full
    assert elo_strength(market_20,
                        [{"club": "Bilbao" if c == "Athletic Club" else c,
                          "elo": str(1500 + i)}
                         for i, c in enumerate(elo_20)]) == full
    assert elo_strength(["Real"], [{"club": "Real Madrid", "elo": "2000"},
                                   {"club": "Real Sociedad", "elo": "1800"}]) \
        is None
    assert fixture_board(mk, fx, now, None) == board
    assert fixture_board(mk, fx, now, []) == board

    ms = [{"jornada": "1", "home": "Rich", "away": "Poor", "score": "2-0"},
         {"jornada": "2", "home": "Mid", "away": "Rich", "score": ""},
         {"jornada": "2", "home": "Poor", "away": "?", "score": ""},
         {"jornada": "3", "home": "Rich", "away": "Mid", "score": ""}]
    sb = season_board(mk, ms, [1, 2, 3], now)
    assert set(sb) == {1, 2, 3}, sb
    assert sb[1]["Rich"].opponent == "Poor" and sb[1]["Rich"].home
    assert sb[1]["Poor"].opponent == "Rich" and not sb[1]["Poor"].home
    assert sb[2]["Mid"].opponent == "Rich" and sb[2]["Mid"].home
    same_fixture = fixture_board(
        mk, [{"kickoff": "2026-08-20T19:00:00+00:00",
             "home": "Mid", "away": "Rich"}], now)
    assert sb[2]["Mid"].atk_factor == same_fixture["Mid"].atk_factor
    assert sb[2]["Mid"].def_factor == same_fixture["Mid"].def_factor
    assert sb[2]["Mid"].rank == same_fixture["Mid"].rank
    assert sb[2]["Poor"].opponent == "?" and sb[2]["Poor"].rank == 0
    assert sb[3]["Rich"].opponent == "Mid" and sb[3]["Rich"].home
    assert sb[1]["Rich"] != sb[3]["Rich"]

    assert 4 not in season_board(mk, ms, [1, 2, 3], now)
    assert season_board(mk, [], [1, 2], now) == {1: {}, 2: {}}

    sb_elo = season_board(mk, ms, [2], now, elo)
    assert sb_elo[2]["Mid"].basis == "elo", sb_elo[2]["Mid"]
    same_elo = fixture_board(
        mk, [{"kickoff": "2026-08-20T19:00:00+00:00",
             "home": "Mid", "away": "Rich"}], now, elo)
    assert sb_elo[2]["Mid"].rank == same_elo["Mid"].rank
    assert sb_elo[2]["Mid"].gap == same_elo["Mid"].gap

    print("ffcore.fixture self-test OK (81 cases)")


if __name__ == "__main__":
    _selftest()
