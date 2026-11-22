"""The bonus the game credits after each jornada (decision layer): every manager receives BONUS_PER_POINT times the points of his
eleven's jornada score, at CREDIT_AT in Madrid on the morning after the jornada's last match, and the credit is part of cash
once it lands. Contract: `credit_at` gives that moment; `inflow_of` gives the Inflow of the credits due before a lock: what
they pay in expectation and at the low case (the 10th percentile of a normal whose spread is JORNADA_SD points over a whole
jornada, scaled by the square root of the share of the eleven's expected points still to be scored, the credits independent;
what is already scored is known, so the spread shrinks as the jornada is played); `risk_of` says how likely the credit is to
fall short of what a cash balance needs.
Credits and their points are the caller's: `Credit` names what a jornada's credit needs."""
from __future__ import annotations

import sys
from collections.abc import Iterable, Mapping
from datetime import datetime, time, timedelta, timezone
from math import erf, sqrt
from typing import NamedTuple
from zoneinfo import ZoneInfo

import eleven
from ffcore.parse import snapshot_stamp

__all__ = ["BONUS_PER_POINT", "JORNADA_SD", "LOW_Z", "CREDIT_AT", "Credit", "Inflow", "NONE", "Risk", "credit_at", "points_of", "inflow_of", "risk_of"]

BONUS_PER_POINT = 100_000.0
CREDIT_AT = time(4, 40)
MADRID = ZoneInfo("Europe/Madrid")
JORNADA_SD = 12.0
LOW_Z = 1.2816


class Credit(NamedTuple):
    """The credit of one jornada: `at` when it lands (UTC, YYYY-MM-DDTHH:MMZ), `scored` the points the team has in the jornada so
    far, `left` the players of the squad whose club has a match still to kick off in it."""
    jornada: int
    at: str
    scored: float
    left: tuple[str, ...]


class Inflow(NamedTuple):
    """The bonus to be credited before a lock: `expected` and `low` (the 10th percentile), in money; `at` the last moment a credit
    lands and `jornadas` those credited (None and empty when there is none), `sd` the standard deviation of the money."""
    expected: float
    low: float
    at: str | None
    jornadas: tuple[int, ...]
    sd: float = 0.0


NONE = Inflow(0.0, 0.0, None, ())


def credit_at(last_kick: str) -> str:
    """The moment the credit of a jornada whose last match kicks off at `last_kick` (a snapshot stamp) lands: CREDIT_AT in Madrid on
    the day after that kickoff's Madrid date, as YYYY-MM-DDTHH:MMZ in UTC."""
    kick = snapshot_stamp(last_kick)
    assert kick is not None, last_kick
    day = kick.astimezone(MADRID).date() + timedelta(days=1)
    return datetime.combine(day, CREDIT_AT, MADRID).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


class Points(NamedTuple):
    """What a credit's jornada is expected to score: the `expected` points and the `var` of the part still to be scored."""
    expected: float
    var: float


def points_of(credit: Credit, squad: frozenset[str], pos: Mapping[str, str], cells: Mapping[str, float]) -> Points:
    """The Points of the credit's jornada for the eleven of `squad` that `cells` (the jornada's forecast) picks: `scored` plus the
    forecast of those of it with a match left; the variance is JORNADA_SD squared over the share of the eleven's forecast left."""
    best = eleven.best_eleven(squad, pos, cells)
    if best is None:
        return Points(credit.scored, 0.0)
    total = sum(cells.get(k, 0.0) for k in best.eleven)
    rest = sum(cells.get(k, 0.0) for k in best.eleven if k in credit.left)
    share = min(1.0, max(0.0, rest / total)) if total > 0 else 0.0
    return Points(credit.scored + rest, JORNADA_SD ** 2 * share)


def inflow_of(credits: Iterable[Credit], squad: frozenset[str], pos: Mapping[str, str],
              cells: Mapping[int, Mapping[str, float]]) -> Inflow:
    """The Inflow of the credits, each valued on the eleven of `squad` in its jornada (`points_of`): NONE when there are none; the low
    case is the expected less LOW_Z standard deviations of the sum, within 0 and the expected (a credit has never been seen
    to take money)."""
    got = [(c, points_of(c, squad, pos, cells.get(c.jornada, {}))) for c in credits]
    if not got:
        return NONE
    expected = BONUS_PER_POINT * sum(p.expected for _, p in got)
    spread = BONUS_PER_POINT * LOW_Z * sqrt(sum(p.var for _, p in got))
    return Inflow(expected, max(0.0, expected - spread), max(c.at for c, _ in got), tuple(sorted(c.jornada for c, _ in got)),
                  BONUS_PER_POINT * sqrt(sum(p.var for _, p in got)))


class Risk(NamedTuple):
    """What the credit leaves open for a cash balance before it: `low_inflow` the credit at its low case, `p_short` the chance it
    does not bring cash to 0 or more, `shortfall_if_low` the money cash still lacks at the low case (0 or more), `below_points`
    the points the eleven must reach, over the credited jornadas, for cash to be 0 or more."""
    low_inflow: float
    p_short: float
    shortfall_if_low: float
    below_points: float


def risk_of(inflow: Inflow, cash: float) -> Risk:
    """The Risk of `inflow` for the cash at the lock before it lands (the credit normal, a spread of 0 a certainty). Nonincreasing
    in `cash`, and in the points already scored when only that changes."""
    need = -cash
    if inflow.sd > 0:
        p = 0.5 * (1.0 + erf((need - inflow.expected) / (inflow.sd * sqrt(2.0))))
    else:
        p = 1.0 if need > inflow.expected else 0.0
    return Risk(inflow.low, p, max(0.0, need - inflow.low), max(0.0, need) / BONUS_PER_POINT)


def _selftest() -> None:
    for kick in ("2026-11-23T2000Z", "2026-03-28T2100Z", "2026-10-25T0000Z", "2026-10-24T2330Z", "2026-11-08T2000Z"):
        at = snapshot_stamp(credit_at(kick))
        assert at is not None
        local = at.astimezone(MADRID)
        then = snapshot_stamp(kick)
        assert then is not None
        assert local.time() == CREDIT_AT and local.date() == then.astimezone(MADRID).date() + timedelta(days=1) and at > then, \
            "the morning after the kickoff's Madrid day, at CREDIT_AT Madrid, across the clock change"
    assert credit_at("2026-11-23T2000Z") == "2026-11-24T03:40Z" and credit_at("2026-08-24T1930Z") == "2026-08-25T02:40Z"
    squad = frozenset("m%02d" % i for i in range(14))
    pos = {k: p for k, p in zip(sorted(squad), ["POR", "POR"] + ["DEF"] * 5 + ["MED"] * 5 + ["DEL"] * 2)}
    cells = {13: {k: 1.0 + i % 4 for i, k in enumerate(sorted(squad))}, 14: {k: 2.0 for k in squad}}
    assert inflow_of((), squad, pos, cells) == NONE and NONE.expected == NONE.low == 0.0
    everyone = tuple(sorted(squad))
    done = Credit(13, "2026-11-24T03:40Z", 40.0, ())
    got = inflow_of([done], squad, pos, cells)
    assert got.expected == got.low == 40.0 * BONUS_PER_POINT and got.jornadas == (13,) and got.at == done.at, "no match left: no spread"
    stretch = []
    for scored in (0.0, 10.0, 25.0):
        for left in ((), everyone[:5], everyone):
            c = Credit(13, done.at, scored, left)
            x = inflow_of([c], squad, pos, cells)
            assert 0.0 <= x.low <= x.expected + 1e-9
            stretch.append((scored, len(left), x))
    for scored, n, x in stretch:
        assert all(y.expected >= x.expected - 1e-9 for s2, n2, y in stretch if s2 >= scored and n2 == n), "more scored never pays less"
        assert all(x.expected - x.low <= y.expected - y.low + 1e-9 for _, n2, y in stretch if n2 >= n and _ == scored), \
            "more of the eleven left never narrows the spread"
    both = inflow_of([done, Credit(14, "2026-12-01T03:40Z", 0.0, everyone)], squad, pos, cells)
    assert both.expected > got.expected and both.jornadas == (13, 14) and both.at == "2026-12-01T03:40Z"
    later = inflow_of([Credit(14, "x", 0.0, everyone)], squad, pos, cells)
    assert abs((both.expected - both.low) - (later.expected - later.low)) < 1e-6, "a credit with nothing left adds no spread"
    for scored in (0.0, 10.0, 20.0, 30.0):
        for cash in (-6e6, -2e6, 0.0, 1e6):
            here = risk_of(inflow_of([Credit(13, "x", scored, everyone[:6])], squad, pos, cells), cash)
            more = risk_of(inflow_of([Credit(13, "x", scored + 5.0, everyone[:6])], squad, pos, cells), cash)
            richer = risk_of(inflow_of([Credit(13, "x", scored, everyone[:6])], squad, pos, cells), cash + 1e6)
            assert 0.0 <= here.p_short <= 1.0 and here.shortfall_if_low >= 0.0
            assert more.p_short <= here.p_short + 1e-12 and more.shortfall_if_low <= here.shortfall_if_low + 1e-9, "more points known: less risk"
            assert richer.p_short <= here.p_short + 1e-12 and richer.shortfall_if_low <= here.shortfall_if_low + 1e-9, "more cash: less risk"
    sure = risk_of(inflow_of([done], squad, pos, cells), -done.scored * BONUS_PER_POINT + 1.0)
    assert sure.p_short == 0.0 and sure.shortfall_if_low == 0.0, "a credit with nothing left to score is a certainty"
    short = frozenset(sorted(squad)[:5])
    assert inflow_of([done], short, pos, cells).expected == 40.0 * BONUS_PER_POINT, "no legal eleven: only what is scored"
    print("inflow self-test OK")


if __name__ == "__main__" and "--selftest" in sys.argv:
    _selftest()
