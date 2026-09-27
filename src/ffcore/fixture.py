
from __future__ import annotations

import math
import statistics
from datetime import datetime
from typing import NamedTuple


from ffcore.parse import money
from ffcore.tidy import kickoff_stamp

FIX_BAND = 0.12
HOME_EDGE = 0.04
MIN_HOME_EDGE_MATCHES = 50


def fit_home_edge(results_history: list[dict],
                  matches: list[dict] = ()) -> float:
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
        return HOME_EDGE
    ratio = home_g / away_g
    return (ratio - 1) / (ratio + 1)


class Match(NamedTuple):
    opponent: str
    home: bool
    kickoff: datetime
    atk_factor: float
    def_factor: float


def team_strength(market: list[dict]) -> dict[str, float]:
    tot: dict[str, float] = {}
    for r in market:
        if r.get("club"):
            tot[r["club"]] = tot.get(r["club"], 0.0) + (money(r.get("value"))
                                                         or 0.0)
    return tot


MIN_AD_MATCHES = 10

def _match_goals(results: list[dict]):
    for r in results:
        home, away = (r.get("home") or "").strip(), (r.get("away") or "").strip()
        try:
            hg, ag = float(r["home_goals"]), float(r["away_goals"])
        except (TypeError, ValueError, KeyError):
            continue
        yield home, away, hg, ag


def attack_defense(results: list[dict], teams
                   ) -> dict[str, tuple[float, float]]:
    scored: dict[str, float] = {}
    conceded: dict[str, float] = {}
    played: dict[str, int] = {}
    total_goals, total_matches = 0.0, 0
    for home, away, hg, ag in _match_goals(results):
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
        out[t] = (scored[t] / n / league_avg, conceded[t] / n / league_avg)
    return out


def club_volatility(results: list[dict], teams) -> dict[str, float]:
    involvement: dict[str, list[float]] = {}
    for home, away, hg, ag in _match_goals(results):
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


def difficulty(strength: dict[str, float]) -> dict[str, float]:
    order = sorted(strength, key=lambda t: -strength[t])
    n = len(order)
    return {team: 1.0 + FIX_BAND * (0.0 if n < 2 else 2.0 * i / (n - 1) - 1.0)
            for i, team in enumerate(order)}


def fixture_board(ratings: "_Ratings", fixtures: list[dict],
                  now: datetime) -> dict[str, Match]:
    board: dict[str, Match] = {}
    for r in fixtures:
        when = kickoff_stamp(r.get("kickoff"))
        if not when or when <= now:
            continue
        for team, opp, opp_name, home in (
                (r.get("home"), r.get("away"), r.get("away_name"), True),
                (r.get("away"), r.get("home"), r.get("home_name"), False)):
            prev = board.get(team)
            if team not in ratings.diff or (prev and prev.kickoff <= when):
                continue
            board[team] = _match_for(ratings, team, opp, opp_name or "?",
                                     home, when)
    return board


class _Ratings(NamedTuple):
    diff: dict
    ad: dict
    home_edge: float


def difficulty_ratings(market: list[dict], results=None,
                       home_edge: float = HOME_EDGE) -> _Ratings:
    value = team_strength(market)
    return _Ratings(diff=difficulty(value),
                    ad=attack_defense(results, list(value)) if results else {},
                    home_edge=home_edge)


def _match_for(ratings: "_Ratings", team: str, opp: str, opp_name: str,
              home: bool, when: datetime) -> Match:
    base = ratings.diff.get(opp, 1.0) if opp else 1.0
    edge = 1.0 + (ratings.home_edge if home else -ratings.home_edge)
    opp_ad = ratings.ad.get(opp) if opp else None
    atk_base, def_base = ((opp_ad[1], 1.0 / opp_ad[0]) if opp_ad is not None
                          else (base, base))
    return Match(opponent=opp_name, home=home, kickoff=when,
                 atk_factor=atk_base * edge, def_factor=def_base * edge)


def season_board(ratings: "_Ratings", matches: list[dict], jornadas,
                 now: datetime) -> dict[int, dict[str, Match]]:
    board: dict[int, dict[str, Match]] = {j: {} for j in set(jornadas)}
    for r in matches:
        j = r.get("jornada") or ""
        if not j.isdigit() or int(j) not in board:
            continue
        for team, opp, home in ((r.get("home"), r.get("away"), True),
                                (r.get("away"), r.get("home"), False)):
            if team in ratings.diff and team not in board[int(j)]:
                board[int(j)][team] = _match_for(ratings, team, opp, opp or "?",
                                                 home, now)
    return board


def _selftest() -> None:
    mk = [{"club": "Rich", "value": "100.00M"},
          {"club": "Rich", "value": "100.00M"},
          {"club": "Mid", "value": "50.00M"},
          {"club": "Poor", "value": "10.00M"},
          {"club": "", "value": "999.00M"}]

    st = team_strength(mk)
    assert st == {"Rich": 200e6, "Mid": 50e6, "Poor": 10e6}, st
    assert "" not in st

    assert difficulty(st) == {"Rich": 1.0 - FIX_BAND, "Mid": 1.0,
                              "Poor": 1.0 + FIX_BAND}
    assert difficulty({"Only": 1.0}) == {"Only": 1.0}

    hist_rows = [{"home_goals": "2", "away_goals": "1"}] * 60
    assert abs(fit_home_edge(hist_rows) - 1 / 3) < 1e-9
    once = (98 / 52 - 1) / (98 / 52 + 1)
    dup_matches = [{"match_id": "m1", "jornada": "1", "score": "0-3"}] * 5
    assert abs(fit_home_edge(hist_rows[:49], dup_matches) - once) < 1e-9
    assert fit_home_edge(hist_rows[:10]) == HOME_EDGE

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

    now = datetime.fromisoformat("2026-08-15T12:00:00+00:00")
    fx = [{"kickoff": k, "home": h, "away": a, "home_name": h, "away_name": a}
          for k, h, a in [("2026-08-14T19:00:00+00:00", "Rich", "Poor"),
                          ("2026-08-20T19:00:00+00:00", "Mid", "Rich"),
                          ("2026-08-16T19:00:00+00:00", "Poor", "Mid")]]
    board = fixture_board(difficulty_ratings(mk), fx, now)

    assert board["Rich"].opponent == "Mid" and not board["Rich"].home
    assert board["Mid"].opponent == "Poor", board["Mid"]
    assert board["Mid"].kickoff.day == 16
    assert abs(board["Mid"].atk_factor
               - (1.0 + FIX_BAND) * (1.0 - HOME_EDGE)) < 1e-9
    assert board["Mid"].atk_factor == board["Mid"].def_factor
    assert abs(board["Poor"].atk_factor - (1.0 * (1.0 + HOME_EDGE))) < 1e-9

    assert fixture_board(difficulty_ratings(mk), [], now) == {}
    solo = fixture_board(difficulty_ratings(mk), [{
        "kickoff": "2026-08-20T19:00:00+00:00", "home": "Mid", "away": ""}], now)
    assert abs(solo["Mid"].atk_factor - (1.0 + HOME_EDGE)) < 1e-9

    ad_results = [{"home": "Rich", "away": "x", "home_goals": "3",
                  "away_goals": "5"}] * MIN_AD_MATCHES
    fx2 = [{"kickoff": "2026-08-20T19:00:00+00:00",
           "home": "Mid", "away": "Rich"}]
    real = fixture_board(difficulty_ratings(mk, results=ad_results), fx2,
                         now)
    assert real["Mid"].def_factor != real["Mid"].atk_factor, real["Mid"]
    assert abs(real["Mid"].def_factor - (1.0 / 0.75) * (1.0 + HOME_EDGE)) \
        < 1e-9, real["Mid"]
    assert abs(real["Mid"].atk_factor - 1.25 * (1.0 + HOME_EDGE)) < 1e-9, \
        real["Mid"]

    assert fixture_board(difficulty_ratings(mk, []), fx, now) == board

    ms = [{"jornada": "1", "home": "Rich", "away": "Poor", "score": "2-0"},
         {"jornada": "2", "home": "Mid", "away": "Rich", "score": ""},
         {"jornada": "2", "home": "Poor", "away": "?", "score": ""},
         {"jornada": "3", "home": "Rich", "away": "Mid", "score": ""}]
    sb = season_board(difficulty_ratings(mk), ms, [1, 2, 3], now)
    assert set(sb) == {1, 2, 3}, sb
    assert sb[1]["Rich"].opponent == "Poor" and sb[1]["Rich"].home
    assert sb[1]["Poor"].opponent == "Rich" and not sb[1]["Poor"].home
    assert sb[2]["Mid"].opponent == "Rich" and sb[2]["Mid"].home
    same_fixture = fixture_board(
        difficulty_ratings(mk), [{"kickoff": "2026-08-20T19:00:00+00:00",
             "home": "Mid", "away": "Rich"}], now)
    assert sb[2]["Mid"].atk_factor == same_fixture["Mid"].atk_factor
    assert sb[2]["Mid"].def_factor == same_fixture["Mid"].def_factor
    assert sb[2]["Poor"].opponent == "?"
    assert sb[3]["Rich"].opponent == "Mid" and sb[3]["Rich"].home
    assert sb[1]["Rich"] != sb[3]["Rich"]

    assert 4 not in season_board(difficulty_ratings(mk), ms, [1, 2, 3], now)
    assert season_board(difficulty_ratings(mk), [], [1, 2], now) == {
        1: {}, 2: {}}

    print("ffcore.fixture self-test OK")


if __name__ == "__main__":
    _selftest()
