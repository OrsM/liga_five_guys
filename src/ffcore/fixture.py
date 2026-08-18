"""
fixture.py — who each team plays next, and how much that should move a forecast.

    board = fixture_board(market_rows, fixture_rows, now)
    m = board.get("Celta")      # Match(opponent="Osasuna", home=True, ...)
    ppm * m.factor * pct/100    # this round's expectation

Until this existed, `pts/m x P(start)` was OPPONENT-BLIND: it valued Celta at
home to Elche exactly as it valued Celta away at Real Madrid. That was the
largest structural error left in the model, and it is the one the fixtures
scrape was added to fix.

WHAT DIFFICULTY IS MEASURED FROM, and why it is a rank. The only team-level
signal in this repo is the app's own valuation, summed over each squad. That
scale is CONVEX: Real Madrid's squad is 4.6x the median squad, Elche's is
0.46x. Taken as a ratio — even a square-rooted one — it produces a 3x swing
between the easiest and hardest fixture, which is nonsense. Facing Real Madrid
does not halve a defender's points; it costs him something like a fifth of a
clean sheet. So teams are RANKED by squad value and the rank is mapped onto a
narrow band. Rank is also robust to the thing value is worst at: one 100M
signing moving a whole squad's total.

THE TWO CONSTANTS BELOW ARE GUESSES. They are not fitted, because nothing has
been played yet — this is the same reason the two probable-XI sources are
printed side by side instead of blended. They are deliberately small, so a
wrong guess costs a fraction of a point rather than reordering an eleven, and
they are named in methodology.md so the monitor can grade them against
realised points once jornadas exist. When it can, FIX_BAND is the first number
to re-fit, and per-position sensitivity is the first thing to add: a clean
sheet is far more opponent-driven than a forward's goal, and this model cannot
yet say so because no goals data is scraped.

A team with no fixture ahead — or a player with no team — gets factor 1.0 and
says so. Never a guess: a missing fixture must not silently become an easy one.

Run `python src/ffcore/fixture.py` to execute the self-test below.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from typing import NamedTuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ffcore.parse import money  # noqa: E402
from ffcore.text import norm  # noqa: E402
from ffcore.tidy import kickoff_stamp  # noqa: E402

# +/- this much from a median opponent, hardest to easiest. A guess. See above.
FIX_BAND = 0.12
# Home is worth this much on top, away the same off it. Also a guess.
HOME_EDGE = 0.04


class Match(NamedTuple):
    opponent: str          # the opponent, as the fixture page spells it
    home: bool
    kickoff: datetime
    factor: float          # multiply pts/match by this
    rank: int              # opponent's rank, 1 = strongest
    of: int                # out of how many ranked teams
    basis: str = "value"   # what ranked them: "elo" or "value"
    gap: float | None = None   # raw Elo difference, you minus opponent


# Club Elo names two Spanish clubs after their CITY where the market names the
# club: Athletic Club is "Bilbao" and Racing de Santander is "Santander". They
# share no substring with the market's spelling, so `match_team` cannot bridge
# them and never should be taught to guess across a gap that wide.
#
# This is not cosmetic. `elo_strength` refuses partial coverage on purpose, so
# these two alone sent all twenty clubs back to squad-value rank — Elo was
# scraped, parsed, stored, and then not used, with the reports saying only
# that coverage was incomplete. Two aliases is the whole fix.
ELO_ALIASES = {"athletic": "Bilbao", "racing": "Santander"}


def elo_strength(market_teams, elo_rows) -> dict[str, float] | None:
    """{market team: Elo rating}, or None unless every team joins.

    PARTIAL COVERAGE IS REFUSED. A board where half the teams are ranked by
    Elo and half by squad value is not a ranking — the two scales have nothing
    to do with each other, and the mixture would be silently wrong in the
    middle of the table where most of the league lives. One unjoinable club
    sends the whole board back to squad value, which is the behaviour that was
    there before Elo existed.

    `elo_rows` is the latest Elo snapshot: rows with `club` and `elo`. Only
    Spanish top-flight rows should reach here — the ratings file is worldwide,
    and Elche ranking above Bayern is not a fixture.
    """
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
            # The city-named clubs, and only after the ordinary join has had
            # its go — an alias must never shadow a name that matched.
            alias = ELO_ALIASES.get(norm(team))
            club = alias if alias in have else None
        if club is None:
            return None
        out[team] = have[club]
    return out


def team_strength(market: list[dict]) -> dict[str, float]:
    """{team: summed squad value}. Rows with no team are ignored, not pooled
    into a phantom twenty-first club."""
    tot: dict[str, float] = {}
    for r in market:
        team = (r.get("team") or "").strip()
        if team:
            tot[team] = tot.get(team, 0.0) + (money(r.get("value")) or 0.0)
    return tot


def difficulty(strength: dict[str, float]) -> dict[str, tuple[float, int]]:
    """{team: (factor if you FACE them away from home, rank)}.

    Rank 1 is the richest squad and the hardest opponent. The middle of the
    table maps to 1.0 whether the league has 20 teams or 3, so this is safe on
    a partial market snapshot.
    """
    order = sorted(strength, key=lambda t: -strength[t])
    n = len(order)
    out: dict[str, tuple[float, int]] = {}
    for i, team in enumerate(order):
        # i=0 (richest) -> -1, i=n-1 (poorest) -> +1, middle -> 0.
        pos = 0.0 if n < 2 else (2.0 * i / (n - 1)) - 1.0
        out[team] = (1.0 + FIX_BAND * pos, i + 1)
    return out


def match_team(side: str, teams) -> str | None:
    """The market's name for a fixture-page side, or None.

    The two pages spell clubs differently — `Celta` and `Celta Vigo`, `Betis`
    and `Real Betis` — and neither publishes an id the other uses. Exact
    first, then substring either way. Two candidates is None, never a pick:
    the same rule the player join follows.
    """
    q = norm(side)
    if not q:
        return None
    exact = [t for t in teams if norm(t) == q]
    if exact:
        return exact[0]
    hits = [t for t in teams if norm(t) and (norm(t) in q or q in norm(t))]
    return hits[0] if len(hits) == 1 else None


def fixture_board(market: list[dict], fixtures: list[dict],
                  now: datetime, elo_rows=None) -> dict[str, Match]:
    """{market team: its next Match}, for every team with one ahead of `now`.

    "Next" is the earliest kickoff still ahead, which is the right question
    even when it belongs to a later round: J1 2026-27 runs 15-27 August while
    J2 runs 20-24, so several teams play J2 before their postponed J1.

    This is the match his rating is adjusted for, NOT the deadline to field
    him. The app locks the whole lineup once per jornada (verified in-app,
    2026-08-16), so the deadline is one moment for everybody and lives in
    tidy.load_deadline(); difficulty stays per player, because the opponent
    he faces is his own.

    `elo_rows` ranks the teams by Club Elo when it covers all of them, and by
    summed squad value otherwise. Every Match says which, and carries the raw
    Elo gap when there was one — the band below is a guess, and re-fitting it
    later against a continuous rating means having logged the rating, not the
    rank it was turned into.
    """
    value = team_strength(market)
    teams = list(value)
    elo = elo_strength(teams, elo_rows) if elo_rows else None
    strength = elo if elo is not None else value
    basis = "elo" if elo is not None else "value"
    diff = difficulty(strength)
    board: dict[str, Match] = {}

    for r in fixtures:
        when = kickoff_stamp(r.get("kickoff"))
        if not when or when <= now:
            continue
        for side, other, home in ((r.get("home"), r.get("away"), True),
                                  (r.get("away"), r.get("home"), False)):
            team = match_team(side or "", teams)
            if not team:
                continue
            prev = board.get(team)
            if prev and prev.kickoff <= when:
                continue
            opp = match_team(other or "", teams)
            base, rank = diff.get(opp, (1.0, 0)) if opp else (1.0, 0)
            edge = (1.0 + HOME_EDGE) if home else (1.0 - HOME_EDGE)
            gap = (elo[team] - elo[opp]
                   if elo is not None and opp in elo else None)
            board[team] = Match(opponent=other or "?", home=home,
                                kickoff=when, factor=base * edge,
                                rank=rank, of=len(teams),
                                basis=basis if opp else "none", gap=gap)
    return board


def _selftest() -> None:
    mk = [{"team": "Rich", "value": "100.00M"},
          {"team": "Rich", "value": "100.00M"},
          {"team": "Mid", "value": "50.00M"},
          {"team": "Poor", "value": "10.00M"},
          {"team": "", "value": "999.00M"}]      # no team: ignored entirely

    st = team_strength(mk)
    assert st == {"Rich": 200e6, "Mid": 50e6, "Poor": 10e6}, st
    assert "" not in st                          # no phantom club

    diff = difficulty(st)
    # Richest is the hardest opponent, poorest the easiest, middle neutral.
    assert diff["Rich"][0] == 1.0 - FIX_BAND
    assert diff["Poor"][0] == 1.0 + FIX_BAND
    assert diff["Mid"][0] == 1.0
    assert diff["Rich"][1] == 1 and diff["Poor"][1] == 3
    # One team alone is neutral, not divide-by-zero.
    assert difficulty({"Only": 1.0})["Only"] == (1.0, 1)

    # The two pages spell clubs differently and still join.
    teams = ["Celta", "Betis", "Atlético", "Real Madrid", "Real Sociedad"]
    assert match_team("Celta Vigo", teams) == "Celta"
    assert match_team("Real Betis", teams) == "Betis"
    assert match_team("Atletico Madrid", teams) == "Atlético"   # accents fold
    assert match_team("Real Madrid", teams) == "Real Madrid"     # exact wins
    assert match_team("Real Sociedad", teams) == "Real Sociedad"
    assert match_team("Nowhere FC", teams) is None
    assert match_team("", teams) is None
    # Two candidates is never a pick.
    assert match_team("Real", ["Real Madrid", "Real Sociedad"]) is None

    now = datetime.fromisoformat("2026-08-15T12:00:00+00:00")
    fx = [{"kickoff": "2026-08-14T19:00:00+00:00",
           "home": "Rich", "away": "Poor"},           # already played
          {"kickoff": "2026-08-20T19:00:00+00:00",
           "home": "Mid", "away": "Rich"},
          {"kickoff": "2026-08-16T19:00:00+00:00",
           "home": "Poor", "away": "Mid"}]
    board = fixture_board(mk, fx, now)

    # A played fixture is not the next one.
    assert board["Rich"].opponent == "Mid" and not board["Rich"].home
    # Mid plays Poor on the 16th, before Rich on the 20th: earliest wins, and
    # it does so regardless of the order the rows arrive in.
    assert board["Mid"].opponent == "Poor", board["Mid"]
    assert board["Mid"].kickoff.day == 16
    # Away at the poorest team: easiest opponent, minus the away edge.
    assert abs(board["Mid"].factor
               - (1.0 + FIX_BAND) * (1.0 - HOME_EDGE)) < 1e-9
    # Home to the middle team is the away trip's mirror.
    assert abs(board["Poor"].factor - (1.0 * (1.0 + HOME_EDGE))) < 1e-9
    # Rank is carried so the report can say WHY, not just how much.
    assert board["Mid"].rank == 3 and board["Mid"].of == 3

    # A team with nothing ahead of it is absent, not neutral-by-default: the
    # caller has to decide what to print, and cannot mistake it for an easy
    # fixture.
    assert fixture_board(mk, [], now) == {}
    # An unjoinable side is skipped rather than guessed at.
    assert fixture_board(mk, [{"kickoff": "2026-08-20T19:00:00+00:00",
                               "home": "Nowhere FC",
                               "away": "Elsewhere FC"}], now) == {}
    # An opponent the market does not price leaves the factor neutral, and the
    # rank says 0 so the report can mark it unknown rather than average.
    solo = fixture_board(mk, [{"kickoff": "2026-08-20T19:00:00+00:00",
                               "home": "Mid", "away": "Nowhere FC"}], now)
    assert solo["Mid"].rank == 0
    assert abs(solo["Mid"].factor - (1.0 + HOME_EDGE)) < 1e-9

    # -- Club Elo, when it covers the whole league -------------------------
    # Elo disagrees with the wallet: Poor is the strongest side on the pitch
    # and Rich the weakest. That reordering is the whole reason to prefer a
    # rating over a valuation, so the board has to follow it.
    elo = [{"club": "Poor", "elo": "1900"}, {"club": "Mid", "elo": "1700"},
           {"club": "Rich", "elo": "1500"}]
    st_elo = elo_strength(["Rich", "Mid", "Poor"], elo)
    assert st_elo == {"Rich": 1500.0, "Mid": 1700.0, "Poor": 1900.0}, st_elo
    assert difficulty(st_elo)["Poor"][1] == 1        # strongest, hardest

    eb = fixture_board(mk, fx, now, elo)
    assert eb["Rich"].basis == "elo", eb["Rich"]
    # Away at Mid, who is now the MIDDLE side by Elo as well: the factor is the
    # away edge alone. By squad value Mid was also middle, so the check that
    # bites is the rank, which has flipped.
    assert eb["Mid"].rank == 1 and board["Mid"].rank == 3, (eb["Mid"], board)
    # The raw gap is carried so the band can be re-fitted later against a
    # continuous rating instead of the rank it was flattened into.
    assert eb["Mid"].gap == 1700.0 - 1900.0, eb["Mid"]
    assert board["Mid"].gap is None and board["Mid"].basis == "value"

    # ONE UNJOINABLE CLUB SENDS THE WHOLE BOARD BACK TO VALUE. Half a league
    # ranked by Elo and half by wallet is not a ranking, and the mixture would
    # be silently wrong in the middle of the table where most teams live.
    assert elo_strength(["Rich", "Mid", "Poor"],
                        [{"club": "Poor", "elo": "1900"}]) is None
    assert fixture_board(mk, fx, now,
                         [{"club": "Poor", "elo": "1900"}])["Rich"].basis \
        == "value"
    # A rating that will not parse is not a rating.
    assert elo_strength(["Rich"], [{"club": "Rich", "elo": ""}]) is None

    # -- the two clubs Club Elo names after their city ---------------------
    # Real: the live 2026-08-17 file spells these "Bilbao" and "Santander",
    # sharing no substring with the market's "Athletic" and "Racing". They
    # cost the whole league its Elo ranking until this existed.
    city = [{"club": "Bilbao", "elo": "1800"},
            {"club": "Santander", "elo": "1600"}]
    assert elo_strength(["Athletic", "Racing"], city) \
        == {"Athletic": 1800.0, "Racing": 1600.0}
    # An alias must not rescue a club Elo genuinely does not carry, or the
    # partial-coverage refusal stops meaning anything.
    assert elo_strength(["Athletic", "Racing"],
                        [{"club": "Bilbao", "elo": "1800"}]) is None
    # …and must never shadow a name that joined on its own merits. If Elo ever
    # renamed Bilbao to Athletic, the ordinary join wins and the alias is
    # simply unused.
    assert elo_strength(["Athletic"], [{"club": "Athletic", "elo": "1750"}]) \
        == {"Athletic": 1750.0}
    assert elo_strength(["Rich"], []) is None
    # Two Elo clubs matching one market name is never a pick, so the board
    # falls back rather than guessing which Real is which.
    assert elo_strength(["Real"], [{"club": "Real Madrid", "elo": "2000"},
                                   {"club": "Real Sociedad", "elo": "1800"}]) \
        is None
    # No Elo at all is exactly today: the board is unchanged, byte for byte.
    assert fixture_board(mk, fx, now, None) == board
    assert fixture_board(mk, fx, now, []) == board

    print("ffcore.fixture self-test OK (36 cases)")


if __name__ == "__main__":
    _selftest()
