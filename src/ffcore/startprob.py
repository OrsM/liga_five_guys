from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, replace
from typing import NamedTuple

import numpy as np

from ffcore.parse import pct100
from ffcore.text import norm
from ffcore.points import minutes_played

__all__ = ["Obs", "Outcome", "Calibration", "StartOdds", "calibrate", "fit", "outcomes",
           "observations", "fit_start_fallbacks", "NEUTRAL_START",
           "ABSENT_START"]

INTERCEPT = [round(-3.0 + 0.5 * i, 1) for i in range(13)]
SLOPE = [round(0.2 + 0.4 * i, 1) for i in range(15)]

FLOOR, CEIL = 0.01, 0.97
NEUTRAL_START = 60.0
ABSENT_START = 15.0
FALLBACK_K = 8.0


class Obs(NamedTuple):
    ff: float | None
    started: float
    group: str = ""


def _platt(p: float, alpha: float, beta: float) -> float:
    p = min(1.0 - 1e-6, max(1e-6, p))
    z = alpha + beta * math.log(p / (1.0 - p))
    q = 1.0 / (1.0 + math.exp(-max(-40.0, min(40.0, z))))
    return min(CEIL, max(FLOOR, q))


@dataclass
class Calibration:
    alpha: float = 0.0
    beta: float = 1.0
    neutral_start: float = NEUTRAL_START
    absent_start: float = ABSENT_START

    def p(self, ff_pct) -> float:
        if ff_pct is None:
            return 0.0
        if (self.alpha, self.beta) == (0.0, 1.0):
            return ff_pct / 100.0
        return _platt(ff_pct / 100.0, self.alpha, self.beta)


class StartOdds:
    """What the probable-XI pages say about each player's next start: the
    listed start percentage, whether he is listed at all, and any injury or
    suspension, read through the calibration fitted to who really started."""

    def __init__(self, xi: list[dict], xw, cal: Calibration | None = None):
        self.cal = cal or Calibration()
        self.start_pct: dict[str, float] = {}
        self.listed: set[str] = set()
        self.status: dict[str, str] = {}
        for r in xi or []:
            key = xw.key_of(r) if xw else None
            if not key:
                continue
            self.listed.add(key)
            p = pct100(r.get("start_pct"))
            if p is not None and p >= 0:
                self.start_pct[key] = max(self.start_pct.get(key, 0.0), p)
            if r.get("status") and r["status"] != "ok":
                self.status[key] = r["status"]

    def p_now(self, key: str) -> float:
        pct = self.start_pct.get(key)
        return self.cal.p(pct if pct is not None else (
            self.cal.neutral_start if key in self.listed
            else self.cal.absent_start))

    def status_of(self, key: str) -> str:
        return self.status.get(key, "")


def _grid_brier(obs) -> np.ndarray:
    ff = np.array([o.ff for o in obs])
    y = np.array([o.started for o in obs])
    al = np.array(INTERCEPT)[:, None, None]
    be = np.array(SLOPE)[None, :, None]
    p = np.clip(ff, 1e-6, 1.0 - 1e-6)
    z = np.clip(al + be * np.log(p / (1.0 - p)), -40.0, 40.0)
    pred = np.clip(1.0 / (1.0 + np.exp(-z)), FLOOR, CEIL)
    pred = np.where((al == 0.0) & (be == 1.0), ff, pred)
    return ((pred - y) ** 2).mean(axis=-1)


def _best(obs) -> Calibration:
    i, j = np.unravel_index(np.argmin(_grid_brier(obs)),
                            (len(INTERCEPT), len(SLOPE)))
    return Calibration(INTERCEPT[i], SLOPE[j])


def _brier(c: Calibration, obs) -> float:
    return sum((c.p(o.ff * 100.0) - o.started) ** 2 for o in obs)


def fit(obs) -> Calibration:
    obs = [o for o in obs if o.ff is not None]
    raw = Calibration()
    groups = sorted({o.group for o in obs})
    if len(obs) < 3 or len(groups) < 2:
        return raw
    held_out = sum(_brier(_best([o for o in obs if o.group != g]),
                          [o for o in obs if o.group == g]) for g in groups)
    return _best(obs) if held_out < _brier(raw, obs) else raw


class Outcome(NamedTuple):
    at: str
    jornada: int
    group: str
    who: str
    key: str | None
    listed: bool
    ff: float | None
    status: str
    in_squad: bool
    mins: float


def _last_before(hist: list, cut: str):
    i = bisect.bisect_right(hist, cut, key=lambda t: t[0])
    return hist[i - 1][1] if i else None


def _pct(row) -> float | None:
    try:
        return float(row.get("start_pct")) / 100.0
    except (TypeError, ValueError):
        return None


def outcomes(lineups, starters, locks: dict, jornada_of: dict, xw
             ) -> list[Outcome]:
    squads: dict[tuple, dict[str, dict]] = {}
    for r in starters:
        if r.get("role") in ("starter", "sub") and r.get("player_slug"):
            squads.setdefault((r["match_id"], r["team_slug"]),
                              {})[r["player_slug"]] = r
    wide: dict[str, dict[str, list]] = {}
    for r in sorted(lineups, key=lambda r: r.get("observed_at", "")):
        slug = r.get("player_slug") or norm(r.get("player_name"))
        wide.setdefault(r.get("team_slug"), {}).setdefault(
            slug, []).append((r.get("observed_at", ""), r))

    out = []
    for (match, team), squad in sorted(squads.items()):
        j = jornada_of.get(match)
        if j not in locks:
            continue
        cut = locks[j].strftime("%Y-%m-%dT%H%MZ")
        before = {slug: row for slug, hist in wide.get(team, {}).items()
                  if (row := _last_before(hist, cut)) is not None}
        for slug in sorted(set(before) | set(squad)):
            row, played = before.get(slug), squad.get(slug)
            out.append(Outcome(
                cut, j, "%s:%s" % (match, team), "%s:%s" % (team, slug),
                xw.key_of(row or played) if xw else None,
                row is not None,
                _pct(row) if row else None,
                (row.get("status") or "ok") if row else "",
                played is not None,
                minutes_played(played["role"], played.get("minute"))
                if played else 0.0))
    return sorted(out, key=lambda o: (o.at, o.group))


def _played(o: Outcome) -> float:
    return 1.0 if o.mins > 0 else 0.0


def observations(outs: list[Outcome], neutral: float = NEUTRAL_START,
                 absent: float = ABSENT_START) -> list[Obs]:
    return [Obs(o.ff if o.ff is not None
                else (neutral if o.listed else absent) / 100.0,
                _played(o), o.group) for o in outs]


def _shrunk(default_pct: float, shares: list[float]) -> float:
    if not shares:
        return default_pct
    rate = sum(shares) / len(shares)
    return ((FALLBACK_K * default_pct / 100.0 + len(shares) * rate)
            / (FALLBACK_K + len(shares)) * 100.0)


def fit_start_fallbacks(outs: list[Outcome]) -> tuple[float, float]:
    return (_shrunk(NEUTRAL_START, [_played(o) for o in outs
                                    if o.listed and o.ff is None]),
            _shrunk(ABSENT_START, [_played(o) for o in outs if not o.listed]))


def calibrate(outs: list[Outcome]) -> Calibration:
    neutral, absent = fit_start_fallbacks(outs)
    return replace(fit(observations(outs, neutral, absent)),
                   neutral_start=neutral, absent_start=absent)


def _selftest() -> None:
    from ffcore.crosswalk import Crosswalk, Player

    xw = Crosswalk({"ana": Player("ana", "Ana"), "bo": Player("bo", "Bo")})
    odds = StartOdds([{"player_name": "Ana", "start_pct": "80", "status": "ok"},
                      {"player_name": "Ana", "start_pct": "90"},
                      {"player_name": "Bo", "start_pct": "", "status": "doubt"},
                      {"player_name": "Nobody", "start_pct": "99"}], xw)
    assert odds.p_now("ana") == 0.9, "the higher of two listings"
    assert odds.p_now("bo") == NEUTRAL_START / 100 and odds.status_of("bo") == "doubt"
    assert odds.p_now("cai") == ABSENT_START / 100 and odds.status_of("cai") == ""
    assert StartOdds([{"player_name": "Ana", "start_pct": "80"}], None).listed == set()

    assert abs(_platt(0.5, 0.0, 1.0) - 0.5) < 1e-6
    assert abs(_platt(0.8, 0.0, 1.0) - 0.8) < 1e-6
    assert _platt(0.8, 0.0, 3.0) > 0.8 and _platt(0.2, 0.0, 3.0) < 0.2
    assert _platt(0.3, 1.5, 3.0) > _platt(0.3, 0.0, 3.0)
    for p, al, be in [(0.999, 0.0, 5.8), (0.001, 0.0, 5.8), (1.0, 3.0, 5.8),
                      (0.0, -3.0, 0.2)]:
        assert FLOOR <= _platt(p, al, be) <= CEIL

    raw = Calibration()
    assert raw.p(80.0) == 0.8
    assert raw.p(100.0) == 1.0 and raw.p(0.0) == 0.0
    assert raw.p(None) == 0.0

    sharp = [Obs(p, st, "sheet%d" % (i % 6))
             for i, (p, st) in enumerate([(0.8, 1), (0.7, 1), (0.3, 0),
                                          (0.2, 0)] * 12)]
    cal = fit(sharp)
    assert cal.beta > 1.0 and cal.p(80.0) > 0.8, cal
    noise = [Obs(0.5, i % 2, "sheet%d" % (i % 5)) for i in range(20)]
    for obs in (noise, [Obs(0.5, 1)],
                [Obs(0.9, i < 11, "same") for i in range(22)]):
        got = fit(obs)
        assert (got.alpha, got.beta) == (0.0, 1.0), got

    grid_obs = sharp + [Obs(0.4, 0, "sheet2")]
    brier = _grid_brier(grid_obs)
    for i, j in [(0, 0), (6, 2), (3, 9)]:
        c = Calibration(INTERCEPT[i], SLOPE[j])
        assert abs(brier[i, j] - _brier(c, grid_obs) / len(grid_obs)) < 1e-12

    import datetime as dt

    locks = {1: dt.datetime(2026, 8, 15, 19, 30, tzinfo=dt.timezone.utc)}
    before, after = "2026-08-14T1000Z", "2026-08-16T1000Z"
    lineups = [
        {"observed_at": before, "source": "futbolfantasy", "team_slug": "t",
         "player_slug": "starter-man", "player_name": "Starter Man",
         "start_pct": "80", "role": "starter"},
        {"observed_at": before, "source": "futbolfantasy", "team_slug": "t",
         "player_slug": "bench-man", "player_name": "Bench Man",
         "start_pct": "20", "role": "sub"},
        {"observed_at": before, "source": "futbolfantasy", "team_slug": "t",
         "player_slug": "vague-man", "player_name": "Vague Man",
         "start_pct": "", "role": "sub"},
        {"observed_at": after, "source": "futbolfantasy", "team_slug": "t",
         "player_slug": "starter-man", "player_name": "Starter Man",
         "start_pct": "99", "role": "starter"},
        {"observed_at": before, "source": "futbolfantasy", "team_slug": "other",
         "player_slug": "elsewhere", "player_name": "Elsewhere",
         "start_pct": "90", "role": "starter"},
    ]
    starters = [
        {"match_id": "m1", "team_slug": "t", "player_slug": "starter-man",
         "player_name": "Starter Man", "role": "starter", "minute": ""},
        {"match_id": "m1", "team_slug": "t", "player_slug": "bench-man",
         "player_name": "Bench Man", "role": "sub", "minute": ""},
        {"match_id": "m1", "team_slug": "t", "player_slug": "surprise-man",
         "player_name": "Surprise Man", "role": "starter", "minute": "45"},
        {"match_id": "m9", "team_slug": "t", "player_slug": "starter-man",
         "player_name": "Starter Man", "role": "starter", "minute": ""},
    ]
    outs = outcomes(lineups, starters, locks, {"m1": 1, "m9": 9}, None)
    by = {o.who: o for o in outs}
    assert set(by) == {"t:starter-man", "t:bench-man", "t:vague-man",
                       "t:surprise-man"}, by
    assert (by["t:starter-man"].ff, by["t:starter-man"].mins) == (0.8, 90.0)
    assert by["t:bench-man"].mins == 0.0
    assert by["t:vague-man"].listed and by["t:vague-man"].ff is None
    assert not by["t:vague-man"].in_squad
    assert not by["t:surprise-man"].listed and by["t:surprise-man"].mins == 45.0
    assert outcomes(lineups, [], locks, {}, None) == []

    obs = {o.ff: o.started for o in observations(outs)}
    assert obs == {0.8: 1.0, 0.2: 0.0, 0.6: 0.0, 0.15: 1.0}, obs
    npct, apct = fit_start_fallbacks(outs)
    assert abs(npct - (8 * 60 + 1 * 0) / 9) < 1e-9, npct
    assert abs(apct - (8 * 15 + 1 * 100) / 9) < 1e-9, apct
    assert fit_start_fallbacks([]) == (60.0, 15.0)

    print("ffcore.startprob self-test OK")


if __name__ == "__main__":
    _selftest()
