"""
fixture.py — who each team plays next, and how much that should move a forecast.

    board = fixture_board(market_rows, fixture_rows, now)
    m = board.get("Celta")      # Match(opponent="Osasuna", home=True, ...)
    ppm * m.def_factor * pct/100   # a defender's/keeper's expectation
    ppm * m.atk_factor * pct/100   # a midfielder's/forward's expectation

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

THE TWO CONSTANTS BELOW ARE GUESSES, when they are used at all. They were
not fitted because nothing had been played yet — the same reason the two
probable-XI sources are printed side by side instead of blended — and are
deliberately small, so a wrong guess costs a fraction of a point rather
than reordering an eleven.

PER-POSITION SENSITIVITY, ADDED: a clean sheet is opponent-ATTACK-driven, a
goal opponent-DEFENSE-driven, and attack_defense() now says so from real
match results (data/tidy/results_history.csv, football-data.co.uk) instead
of a single squad-value rank applied to every position alike. FIX_BAND and
the rank-based `difficulty()` are still what a club falls back to when it
has fewer than MIN_AD_MATCHES of real results (freshly promoted, most
often) — a per-CLUB fallback, not per-league, so one thin-history side does
not send the whole board back to the guess the way one Elo gap would.

A team with no fixture ahead — or a player with no team — gets both factors
at 1.0 and says so. Never a guess: a missing fixture must not silently
become an easy one.

Run `python src/ffcore/fixture.py` to execute the self-test below.
"""

from __future__ import annotations

import math
import os
import statistics
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
    # TWO FACTORS, NOT ONE: a clean sheet is driven by the opponent's ATTACK,
    # a goal by the opponent's DEFENSE — the same number priced both until
    # attack_defense() existed to tell them apart. def_factor is for
    # POR/DEF, atk_factor for MED/DEL; ffcore.score.Scored.score picks
    # between them by slot.
    atk_factor: float      # multiply a MED/DEL's pts/match by this
    def_factor: float      # multiply a POR/DEF's pts/match by this
    rank: int              # opponent's rank, 1 = strongest
    of: int                # out of how many ranked teams
    basis: str = "value"   # what ranked them: "attack_defense", "elo", "value"
    gap: float | None = None   # raw Elo difference, you minus opponent


# Club Elo names some Spanish clubs after their CITY where the market names
# the club: Racing de Santander is "Santander". They share no substring with
# the market's spelling, so `match_team` cannot bridge them and never should
# be taught to guess across a gap that wide.
#
# This is not cosmetic. `elo_strength` refuses partial coverage on purpose, so
# these alone sent all twenty clubs back to squad-value rank — Elo was
# scraped, parsed, stored, and then not used, with the reports saying only
# that coverage was incomplete. An alias each is the whole fix.
#
# "Bilbao" IS KEPT THOUGH NOTHING SERVES IT TODAY. The CSV API spelled Athletic
# that way; the country page this now reads spells it "Athletic Club", which
# `match_team` joins on its own. An alias is only consulted after the ordinary
# join has failed, so a spelling that never arrives costs nothing — and the
# day one of these sources goes back to the other spelling, twenty clubs do
# not silently drop to squad value again.
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


# Matches of real history before a club's fitted attack/defense rating is
# trusted over the squad-value/Elo rank fallback. Same number ffcore.score's
# _priors() uses for "enough of a record to mean something" — not tuned
# separately, just borrowed, so there is one answer to "how much history is
# enough" rather than two that could drift apart.
MIN_AD_MATCHES = 10

# HOW MUCH ONE SEASON'S BOTTOM-UP XG CLUB RATING (xg_club_attack) IS WORTH,
# in the same pseudo-match currency attack_defense() already blends real
# matches with (see MIN_AD_MATCHES, ffcore.score.SHRINK_K) — A JUDGMENT
# CALL, NOT A FIT, same status as DRIFT_FRAC. Measured in-sample only
# (xg_club_attack() against attack_defense()'s own real-goals attack, both
# 2025-26): Pearson r=0.884, n=20 clubs — real, not noise, but the same
# season's own goals built both numbers, so this cannot yet say xG PREDICTS
# better than the answer it correlates with. One divergence worth naming:
# Sevilla ranked 10th of 20 on real goals, 19th on this rating — real
# over-performance last season, or a goal-scoring midfielder this
# position gate excludes (see xg_club_attack()'s own note), not knowable
# from here. A genuine held-out test needs either match-level team xG
# (results_history.csv carries it — 7 rows so far this season, nowhere
# near the ~200 MIN_AD_MATCHES-per-club would need) or a second paired
# season of Understat coverage (one exists today). Until one of those
# exists, this stays a round number in the same order of magnitude as
# this repo's other pseudo-match constants rather than a measured weight.
XG_CLUB_PSEUDO_MATCHES = 10.0


def xg_club_attack(understat_rows, xw) -> dict[str, float]:
    """{ff_slug: relative xG+xA attacking rate}, 1.0-centred the same way
    attack_defense()'s own attack/defense numbers are.

    BOTTOM-UP FROM INDIVIDUAL PLAYERS, because no team-level xG source is
    usable yet — see XG_CLUB_PSEUDO_MATCHES's own note. Forwards and
    attacking mids only, the same position gate ffcore.score's xG blend
    already uses and for the same measured reason: an attacking metric
    says nothing about a defender's or goalkeeper's fantasy points.

    A row whose `team_title` contains a comma is a player Understat folds
    two clubs' worth of one season into a single string (a mid-season
    move) — dropped rather than split, since the season-aggregate endpoint
    carries no date to split it on.
    """
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
    """{team: (attack, defense)}, from real match results — goals scored and
    conceded, home and away pooled, relative to the league's own average.

    `xg_attack`, when given (xg_club_attack()'s own output), blends into
    the ATTACK half only — DEFENSE stays real-goals-only, because nothing
    here has a bottom-up xG signal for it (that would need real match-
    level xG-against, not summed player output; see XG_CLUB_PSEUDO_
    MATCHES's own note). Weighted by real matches played against a fixed
    pseudo-match count for the xG side, the same shrink-toward-a-prior
    shape SHRINK_K already uses — so a club with few real matches (near
    MIN_AD_MATCHES) leans on the xG rating more than an established one
    with 50+ does.

    attack > 1.0 scores more than an average team that season; defense > 1.0
    CONCEDES more than an average team (worse defense), < 1.0 fewer (better).
    Both centred on 1.0 by construction, the same centring difficulty()'s
    rank-based factor uses — which is what makes blending the two per team
    safe below, unlike elo_strength()'s refusal to mix scales.

    PER-TEAM COVERAGE, NOT ALL-OR-NOTHING. elo_strength() refuses the whole
    board if even one club lacks an Elo rating, because Elo and squad value
    are two INCOMPARABLE scales and a board mixing them client-team-by-team
    would be worse than either alone. A club with MIN_AD_MATCHES fewer
    real results (freshly promoted, most often) simply has no entry here;
    the caller falls back to the rank-based factor for that one club and
    nothing else, since both answers already live on the same 1.0-centred
    scale.

    Only clubs in `teams` are returned — this repo's current twenty, since a
    club that cannot be anyone's opponent this season is not worth carrying.
    """
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
    league_avg = total_goals / (total_matches * 2)   # per team, per match
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
    """{team: rel} — how uncertain THIS CLUB'S OWN attack_defense() rating
    is, as a fraction, from how much its match-to-match goal involvement
    (scored + conceded, one number per match) actually varied.

    THE SAME SHAPE ffcore.score._priors()/ffcore.forecast.Bootstrap's
    rate_rel ALREADY USES for a PLAYER's rate: standard error of a mean
    over n observations is the per-observation spread over root n, so a
    rating fit from more matches is trusted more, one that has barely
    settled is trusted less. Not a new idea, the same one at club scale
    — un-netted against player-level rate_rel deliberately: there is no
    clean way to say how much of a player's existing individual
    uncertainty already reflects his club's shared risk and how much is
    truly his own, so this is meant to be ADDED as an extra source of
    correlated uncertainty, not to replace or shrink what already exists.
    Errs toward wider bands, which is the documented gap this exists to
    narrow, rather than toward a redistribution that could get the split
    wrong in either direction.

    GOALS INVOLVEMENT (scored + conceded), NOT SCORED ALONE — a club's bad
    week usually shows up both ways at once (their attack goes quiet AND
    their defense leaks), and one number keeps the shared per-trial shock
    (below) simple: every one of a club's players, attacker or defender,
    inherits the same "this club had a wild/quiet stretch" draw. Splitting
    attack-volatility from defense-volatility separately is a real
    refinement this does not attempt.

    Same MIN_AD_MATCHES threshold and per-team (not all-or-nothing)
    coverage as attack_defense() — see its own note on why that is safe.
    """
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
                  now: datetime, elo_rows=None, xw=None,
                  results=None, understat_rows=None) -> dict[str, Match]:
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
    summed squad value otherwise; `results` (ffcore.tidy.load_results_history)
    fits a real attack/defense rating PER OPPONENT from real match results,
    falling back to the elo-or-value rank for whichever clubs
    attack_defense() refuses (thin history — see MIN_AD_MATCHES). Every
    Match says which basis it got, and carries the raw Elo gap when there
    was one. `understat_rows`, when given, blends a bottom-up xG attacking
    rating into attack_defense()'s own ATTACK half — see
    XG_CLUB_PSEUDO_MATCHES's own note on why that weight is a judgment call.
    """
    value = team_strength(market)
    teams = list(value)
    # results_history.csv IS KEYED ON ff_slug ("real-madrid"), not the
    # market's own spelling ("Real Madrid") `teams` holds — two different
    # namespaces this repo already tracks the mapping between, on
    # ffcore.crosswalk.Club. Without translating through it, attack_defense()
    # was being asked "does 'Real Madrid' have 10 results?" against a table
    # that only ever says "real-madrid" — zero matches, basis="elo" for
    # every single team, silently, despite the data being right there.
    slug_of = {c.market: c.ff_slug for c in xw.clubs.values()
              if c.market and c.ff_slug} if xw is not None else {}
    xg_attack = (xg_club_attack(understat_rows, xw)
                if understat_rows and xw is not None else {})
    ad_by_slug = (attack_defense(results, list(slug_of.values()), xg_attack)
                 if results and slug_of else {})
    # THE FIXTURE PAGE STATES ITS OWN CLUB ID on both crests of every match,
    # and the crosswalk has been told which club each one is. Joining on it
    # means "Celta" and "Celta Vigo" stop being a substring puzzle that can
    # return two candidates and answer with neither. The name match stays for
    # a club the table has not learned yet, and for the fixtures recorded
    # before the id was extracted.
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
            team = by_af.get((sid or "").strip()) or match_team(side or "",
                                                                teams)
            if not team:
                continue
            prev = board.get(team)
            if prev and prev.kickoff <= when:
                continue
            opp = by_af.get((oid or "").strip()) or match_team(other or "",
                                                               teams)
            base, rank = diff.get(opp, (1.0, 0)) if opp else (1.0, 0)
            edge = (1.0 + HOME_EDGE) if home else (1.0 - HOME_EDGE)
            gap = (elo[team] - elo[opp]
                   if elo is not None and opp in elo else None)
            # FITTED WHERE THE DATA SUPPORTS IT, the rank-based base
            # otherwise — per opponent, not all-or-nothing (see
            # attack_defense()'s own note on why that is safe here and not
            # for Elo). def_factor answers to the opponent's ATTACK (harder
            # to keep a clean sheet against a side that scores a lot);
            # atk_factor to the opponent's DEFENSE (easier to score against
            # a side that concedes a lot).
            opp_ad = ad_by_slug.get(slug_of.get(opp)) if opp else None
            if opp_ad is not None:
                atk_base, def_base = opp_ad[1], 1.0 / opp_ad[0]
                row_basis = "attack_defense"
            else:
                atk_base = def_base = base
                row_basis = basis if opp else "none"
            board[team] = Match(opponent=other or "?", home=home,
                                kickoff=when,
                                atk_factor=atk_base * edge,
                                def_factor=def_base * edge,
                                rank=rank, of=len(teams),
                                basis=row_basis, gap=gap)
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

    # -- attack_defense -------------------------------------------------
    # A tiny 3-team league, MIN_AD_MATCHES results each for Strong and Weak,
    # one short for Thin — Strong scores a lot and concedes little, Weak the
    # opposite, Thin has a real record but not enough of one to trust.
    results = (
        [{"home": "Strong", "away": "Weak", "home_goals": "3", "away_goals": "0"},
         {"home": "Weak", "away": "Strong", "home_goals": "0", "away_goals": "2"}]
        * 5     # 10 matches each for Strong and Weak
        + [{"home": "Thin", "away": "Weak", "home_goals": "1", "away_goals": "1"}]
          * 9   # 9 matches for Thin: one short of MIN_AD_MATCHES
    )
    ad = attack_defense(results, ["Strong", "Weak", "Thin"])
    assert set(ad) == {"Strong", "Weak"}, ad     # Thin refused, not guessed
    # League average goals/team/match: (3+0+0+2)*5/(20*2) + (1+1)*9/(18*2)
    # — easier to just check the ORDERING, which is what this exists for.
    assert ad["Strong"][0] > 1.0 > ad["Weak"][0], ad          # attack
    assert ad["Weak"][1] > 1.0 > ad["Strong"][1], ad           # defense
    # An unresolved side ("" — a club not in this repo's current twenty) is
    # simply not accumulated for — the match's goals still count toward the
    # league average (via `total_goals`/`total_matches`), but there is no
    # "" entry to return since `teams` never asks for one.
    only = attack_defense(
        [{"home": "Strong", "away": "", "home_goals": "5", "away_goals": "0"}]
        * MIN_AD_MATCHES,
        ["Strong"])
    assert set(only) == {"Strong"}, only
    assert attack_defense([], ["Strong"]) == {}
    # A row with no goals at all (a parse gap) is skipped, not zero.
    assert attack_defense([{"home": "A", "away": "B", "home_goals": "",
                            "away_goals": ""}], ["A", "B"]) == {}

    # -- xg_club_attack ---------------------------------------------------
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
        # Not a forward: excluded from the position gate.
        + [{"position": "D", "team_title": "Strong", "minutes": "900",
           "xg": "20", "xa": "0"}]
        # A mid-season move: the comma marks it, dropped rather than split.
        + [{"position": "F", "team_title": "Strong,Weak",
           "minutes": "900", "xg": "5", "xa": "0"}]
    )
    xga = xg_club_attack(und_rows, xw3)
    assert xga["Strong"] > 1.0 > xga["Weak"], xga
    assert xg_club_attack([], xw3) == {}
    # No crosswalk: cannot resolve a name to a slug, so nothing comes back —
    # not a crash, not a guess at the mapping.
    assert xg_club_attack(und_rows, None) == {}

    # -- attack_defense blended with xg_club_attack ------------------------
    # Same Strong/Weak results as above, MIN_AD_MATCHES each. Real goals
    # alone already say Strong > 1.0 > Weak; the xG side agrees in
    # direction here, so the blended number should still order the same
    # way, and MOVE relative to the real-goals-only figure (this is the
    # actual claim — the xG side is not a no-op once supplied).
    real_only = attack_defense(results, ["Strong", "Weak"])
    blended = attack_defense(results, ["Strong", "Weak"], xga)
    assert blended["Strong"][0] > 1.0 > blended["Weak"][0], blended
    assert blended["Strong"][0] != real_only["Strong"][0], \
        "xg_attack must move the number, not be silently ignored"
    # DEFENSE IS UNTOUCHED — no bottom-up xG-against signal exists yet
    # (see the function's own docstring), so blending in an attack rating
    # must not, as a side effect, change the defense half at all.
    assert blended["Strong"][1] == real_only["Strong"][1]
    assert blended["Weak"][1] == real_only["Weak"][1]
    # A club with no xg_attack entry (not covered by Understat, e.g. a
    # newly-promoted side) is a no-op — real goals are the whole answer,
    # same as when xg_attack is omitted entirely.
    partial = attack_defense(results, ["Strong", "Weak"], {"Strong": 2.0})
    assert partial["Weak"] == real_only["Weak"], partial
    # A HUGE pseudo-match count should pull the blended figure close to
    # the xG reading itself; a TINY one should barely move it from real
    # goals — the shrink-toward-a-prior shape this borrows from SHRINK_K.
    heavy = attack_defense(results, ["Strong"], xga, xg_pseudo=1e6)
    light = attack_defense(results, ["Strong"], xga, xg_pseudo=1e-6)
    assert abs(heavy["Strong"][0] - xga["Strong"]) < 1e-3, heavy
    assert abs(light["Strong"][0] - real_only["Strong"][0]) < 1e-3, light

    # -- club_volatility ------------------------------------------------
    # Steady always scores/concedes 2 total goals a match — pstdev 0, so
    # rel is exactly 0, not merely small. Wild swings from 0 to 8, real
    # spread, real rel.
    steady = [{"home": "Steady", "away": "Weak", "home_goals": "1",
              "away_goals": "1"}] * MIN_AD_MATCHES
    wild = ([{"home": "Wild", "away": "Weak", "home_goals": "0",
             "away_goals": "0"}] * (MIN_AD_MATCHES // 2)
           + [{"home": "Wild", "away": "Weak", "home_goals": "8",
              "away_goals": "0"}] * (MIN_AD_MATCHES // 2))
    vol = club_volatility(steady + wild, ["Steady", "Wild", "Thin"])
    assert vol["Steady"] == 0.0, vol
    assert vol["Wild"] > 0.0, vol
    assert "Thin" not in vol, vol           # under MIN_AD_MATCHES, refused
    assert club_volatility([], ["Steady"]) == {}
    # A team that never scores or concedes (mean involvement 0) is a
    # divide-by-zero avoided, not a crash.
    assert club_volatility(
        [{"home": "Empty", "away": "X", "home_goals": "0",
          "away_goals": "0"}] * MIN_AD_MATCHES, ["Empty"]) == {}

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
    # Away at the poorest team: easiest opponent, minus the away edge. No
    # `results` passed, so atk_factor and def_factor both fall back to the
    # same rank-based number.
    assert abs(board["Mid"].atk_factor
               - (1.0 + FIX_BAND) * (1.0 - HOME_EDGE)) < 1e-9
    assert board["Mid"].atk_factor == board["Mid"].def_factor
    # Home to the middle team is the away trip's mirror.
    assert abs(board["Poor"].atk_factor - (1.0 * (1.0 + HOME_EDGE))) < 1e-9
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
    assert abs(solo["Mid"].atk_factor - (1.0 + HOME_EDGE)) < 1e-9

    # -- fixture_board with real results: fitted per opponent, rank-based
    # -- fallback for whoever attack_defense() refuses --------------------
    # results_history.csv IS KEYED ON ff_slug, not the market's own
    # spelling — "rich-slug" here, deliberately DIFFERENT from "Rich", to
    # exercise the actual translation rather than have it pass by both
    # sides accidentally being the same string. Missed exactly this once
    # already: a first version left `xw` out of this test, so the mismatch
    # it was supposed to catch didn't get caught until real market data
    # (title case) met real results_history.csv data (slugs) and every
    # single team fell back to "elo" silently.
    from ffcore.crosswalk import Club, Crosswalk

    xw2 = Crosswalk({}, {
        "rich": Club("rich", market="Rich", ff_slug="rich-slug"),
        "mid": Club("mid", market="Mid", ff_slug="mid-slug"),
        "poor": Club("poor", market="Poor", ff_slug="poor-slug"),
    })
    # Rich has MIN_AD_MATCHES of real results: weak attack (0.75x league
    # average) and a leaky defense (1.25x average conceded) — hand-computed
    # from a 3-5 scoreline repeated ten times, league average 4.0 goals.
    ad_results = [{"home": "rich-slug", "away": "x-slug", "home_goals": "3",
                  "away_goals": "5"}] * MIN_AD_MATCHES
    fx2 = [{"kickoff": "2026-08-20T19:00:00+00:00",
           "home": "Mid", "away": "Rich"}]
    real = fixture_board(mk, fx2, now, results=ad_results, xw=xw2)
    assert real["Mid"].basis == "attack_defense", real["Mid"]
    # def_factor answers to Rich's ATTACK (0.75, weak): 1/0.75 * home edge.
    # atk_factor answers to Rich's DEFENSE (1.25, leaky): 1.25 * home edge.
    # THE TWO NUMBERS MUST DIFFER — that is the entire point of splitting
    # them — and each must match its own hand-computed value, not just
    # "some fitted number".
    assert real["Mid"].def_factor != real["Mid"].atk_factor, real["Mid"]
    assert abs(real["Mid"].def_factor - (1.0 / 0.75) * (1.0 + HOME_EDGE)) \
        < 1e-9, real["Mid"]
    assert abs(real["Mid"].atk_factor - 1.25 * (1.0 + HOME_EDGE)) < 1e-9, \
        real["Mid"]
    # Rich itself faces "X", which attack_defense() never rated (never the
    # home or away side of a match Rich wasn't in) — falls back to rank.
    assert real["Rich"].basis in ("value", "none")
    # No crosswalk at all: cannot translate market spelling to ff_slug, so
    # results are unusable and everyone falls back — not a crash, not a
    # silent wrong answer, the same behaviour as no results at all.
    no_xw = fixture_board(mk, fx2, now, results=ad_results)
    assert no_xw["Mid"].basis != "attack_defense", no_xw["Mid"]

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

    # THE WHOLE LEAGUE, in the two spellings that actually meet: the market's
    # names on the left, Club Elo's country page on the right. Elo is only
    # ever used when all twenty join, so this is the test that says whether it
    # is used at all — and it is a join between two sites that rename clubs
    # without telling anybody.
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
    # And the spelling the dead CSV API used still joins, because the alias
    # for it is still there.
    assert elo_strength(market_20,
                        [{"club": "Bilbao" if c == "Athletic Club" else c,
                          "elo": str(1500 + i)}
                         for i, c in enumerate(elo_20)]) == full
    # Two Elo clubs matching one market name is never a pick, so the board
    # falls back rather than guessing which Real is which.
    assert elo_strength(["Real"], [{"club": "Real Madrid", "elo": "2000"},
                                   {"club": "Real Sociedad", "elo": "1800"}]) \
        is None
    # No Elo at all is exactly today: the board is unchanged, byte for byte.
    assert fixture_board(mk, fx, now, None) == board
    assert fixture_board(mk, fx, now, []) == board

    print("ffcore.fixture self-test OK (70 cases)")


if __name__ == "__main__":
    _selftest()
