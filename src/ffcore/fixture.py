
from __future__ import annotations

from typing import NamedTuple


from ffcore.parse import money

FIX_BAND = 0.12


class Match(NamedTuple):
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


def difficulty(strength: dict[str, float]) -> dict[str, float]:
    order = sorted(strength, key=lambda t: -strength[t])
    n = len(order)
    return {team: 1.0 + FIX_BAND * (0.0 if n < 2 else 2.0 * i / (n - 1) - 1.0)
            for i, team in enumerate(order)}


class _Ratings(NamedTuple):
    diff: dict
    ad: dict


def difficulty_ratings(market: list[dict], results=None) -> _Ratings:
    value = team_strength(market)
    return _Ratings(diff=difficulty(value),
                    ad=attack_defense(results, list(value)) if results else {})


def _match_for(ratings: "_Ratings", opp: str) -> Match:
    base = ratings.diff.get(opp, 1.0) if opp else 1.0
    opp_ad = ratings.ad.get(opp) if opp else None
    return Match(*((opp_ad[1], 1.0 / opp_ad[0]) if opp_ad is not None
                   else (base, base)))


def season_board(ratings: "_Ratings", matches: list[dict], jornadas
                 ) -> dict[int, dict[str, Match]]:
    board: dict[int, dict[str, Match]] = {j: {} for j in set(jornadas)}
    for r in matches:
        j = r.get("jornada") or ""
        if not j.isdigit() or int(j) not in board:
            continue
        for team, opp in ((r.get("home"), r.get("away")),
                          (r.get("away"), r.get("home"))):
            if team in ratings.diff and team not in board[int(j)]:
                board[int(j)][team] = _match_for(ratings, opp)
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

    ms = [{"jornada": "1", "home": "Rich", "away": "Poor", "score": "2-0"},
          {"jornada": "2", "home": "Mid", "away": "Rich", "score": ""},
          {"jornada": "2", "home": "Poor", "away": "", "score": ""},
          {"jornada": "3", "home": "Poor", "away": "Mid", "score": ""}]
    sb = season_board(difficulty_ratings(mk), ms, [1, 2, 3])
    assert set(sb) == {1, 2, 3} and 4 not in sb, sb
    assert sb[2]["Mid"] == Match((1.0 - FIX_BAND),
                                 (1.0 - FIX_BAND)), sb[2]
    assert sb[3]["Mid"].atk_factor == (1.0 + FIX_BAND)
    assert sb[2]["Poor"] == Match(1.0, 1.0), sb[2]
    assert season_board(difficulty_ratings(mk), [], [1, 2]) == {1: {}, 2: {}}

    ad_results = [{"home": "Rich", "away": "x", "home_goals": "3",
                   "away_goals": "5"}] * MIN_AD_MATCHES
    real = season_board(difficulty_ratings(mk, results=ad_results), ms, [2])[2]
    assert abs(real["Mid"].def_factor - (1.0 / 0.75)) < 1e-9
    assert abs(real["Mid"].atk_factor - 1.25) < 1e-9
    assert season_board(difficulty_ratings(mk, []), ms, [2]) == \
        season_board(difficulty_ratings(mk), ms, [2])

    print("ffcore.fixture self-test OK")


if __name__ == "__main__":
    _selftest()
