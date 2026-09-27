from __future__ import annotations

import bisect
import itertools
import math
import statistics as st
from dataclasses import dataclass, field, replace
from typing import NamedTuple

import numpy as np

from ffcore.text import norm
from ffcore.tidy import MATCH_LEN, minutes_played

__all__ = ["Obs", "Outcome", "Calibration", "calibrate", "fit", "outcomes",
           "observations", "fit_start_fallbacks", "NEUTRAL_START",
           "ABSENT_START", "line_rows"]

INTERCEPT = [round(-3.0 + 0.5 * i, 1) for i in range(13)]
SLOPE = [round(0.2 + 0.4 * i, 1) for i in range(15)]

FLOOR, CEIL = 0.01, 0.97
NEUTRAL_START = 60.0
ABSENT_START = 15.0
FALLBACK_K = 8.0
GRID = (0.5, 1.0, 2.0, 3.0, 4.0, 6.0, 8.0, 12.0)
PRIOR_90 = 4.0
WARMUP = 3
MIN_ROWS = 300
REGULAR = 0.7
MIN_STATUS_ROWS = 20


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
    lineup_k: float | None = None
    status_factor: dict[str, float] = field(default_factory=dict)

    def p(self, ff_pct) -> float:
        if ff_pct is None:
            return 0.0
        if (self.alpha, self.beta) == (0.0, 1.0):
            return ff_pct / 100.0
        return _platt(ff_pct / 100.0, self.alpha, self.beta)


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


def _share(o: Outcome) -> float:
    return min(1.0, o.mins / MATCH_LEN)


def observations(outs: list[Outcome], neutral: float = NEUTRAL_START,
                 absent: float = ABSENT_START) -> list[Obs]:
    return [Obs(o.ff if o.ff is not None
                else (neutral if o.listed else absent) / 100.0,
                _share(o), o.group) for o in outs]


def _shrunk(default_pct: float, shares: list[float]) -> float:
    if not shares:
        return default_pct
    rate = sum(shares) / len(shares)
    return ((FALLBACK_K * default_pct / 100.0 + len(shares) * rate)
            / (FALLBACK_K + len(shares)) * 100.0)


def fit_start_fallbacks(outs: list[Outcome]) -> tuple[float, float]:
    return (_shrunk(NEUTRAL_START, [_share(o) for o in outs
                                    if o.listed and o.ff is None]),
            _shrunk(ABSENT_START, [_share(o) for o in outs if not o.listed]))


def line_rows(outs, pos: dict, points: dict) -> list[dict]:
    return [{"key": o.key, "pos": pos[o.key], "j": o.jornada, "line": o.ff,
             "share": _share(o), "mins": o.mins,
             "pts": points.get((o.key, o.jornada), 0.0)}
            for o in outs if o.in_squad and o.key and pos.get(o.key)]


def pairs(data):
    mine, league, out = {}, {}, []
    for r in data:
        a = mine.setdefault(r["key"], [0.0, 0.0, []])
        g = league.setdefault(r["pos"], [0.0, 0.0])
        if r["j"] >= WARMUP and r["line"] is not None and a[2] and g[1]:
            prior = g[0] / (g[1] / MATCH_LEN)
            rate = (PRIOR_90 * prior + a[0]) / (PRIOR_90 + a[1] / MATCH_LEN)
            out.append((r, rate, r["line"], sum(a[2]), len(a[2])))
        a[0] += r["pts"]
        a[1] += r["mins"]
        a[2].append(r["share"])
        g[0] += r["pts"]
        g[1] += r["mins"]
    return out


def mse(sample, k):
    return st.mean((r["pts"] - rate * (k * line + tot) / (k + n)) ** 2
                   for r, rate, line, tot, n in sample)


def fit_lineup_weight(data) -> float | None:
    p = pairs(data)
    if len(p) < MIN_ROWS:
        return None
    train = p[:int(0.6 * len(p))]
    return min(GRID, key=lambda k: mse(train, k))


def fit_status_factors(outs) -> dict[str, float]:
    earlier: dict[str, list[float]] = {}
    seen: dict[str, list[float]] = {}
    for _g, group in itertools.groupby(outs, key=lambda o: (o.at, o.group)):
        group = list(group)
        for o in group:
            hist = earlier.get(o.who, [])
            if o.listed and len(hist) >= 2 and st.mean(hist) >= REGULAR:
                seen.setdefault(o.status, []).append(_share(o))
        for o in group:
            if o.in_squad:
                earlier.setdefault(o.who, []).append(_share(o))
    base = st.mean(seen["ok"]) if seen.get("ok") else 0.0
    return {f: st.mean(v) / base for f, v in seen.items()
            if base > 0 and f != "ok" and len(v) >= MIN_STATUS_ROWS}


def calibrate(outs: list[Outcome], line_rows: list[dict]) -> Calibration:
    neutral, absent = fit_start_fallbacks(outs)
    return replace(fit(observations(outs, neutral, absent)),
                   neutral_start=neutral, absent_start=absent,
                   lineup_k=fit_lineup_weight(line_rows),
                   status_factor=fit_status_factors(outs))


def _selftest() -> None:
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
    assert obs == {0.8: 1.0, 0.2: 0.0, 0.6: 0.0, 0.15: 0.5}, obs
    npct, apct = fit_start_fallbacks(outs)
    assert abs(npct - (8 * 60 + 1 * 0) / 9) < 1e-9, npct
    assert abs(apct - (8 * 15 + 1 * 50) / 9) < 1e-9, apct
    assert fit_start_fallbacks([]) == (60.0, 15.0)

    data, steady = [], []
    for i in range(60):
        for j in range(1, 9):
            for out, share, line in ((data, (i + j) % 2, (i + j) % 2),
                                     (steady, 1.0, (i * j) % 2)):
                out.append({"key": "p%d" % i, "pos": "MED", "j": j,
                            "at": "2026-09-%02dT1200Z" % j, "line": float(line),
                            "share": float(share), "mins": 90.0 * share,
                            "pts": 5.0 * share})
    data.sort(key=lambda r: r["at"])
    steady.sort(key=lambda r: r["at"])
    assert fit_lineup_weight(data) == max(GRID)
    assert fit_lineup_weight(steady) == min(GRID)
    assert fit_lineup_weight(data[:20]) is None
    assert not pairs(data[:1])

    outs = []
    for m in range(1, 9):
        for i in range(50):
            flag = ("injured" if i < 25 and m > 3 else "doubt" if i < 40 and
                    m > 3 and i % 2 else "ok")
            plays = flag == "ok" or (flag == "doubt" and m % 2)
            outs.append(Outcome("2026-09-%02dT0800Z" % m, m, "%d:t" % m,
                                "t:p%d" % i, None, True, None, flag,
                                bool(plays), 90.0 if plays else 0.0))
    got = fit_status_factors(outs)
    assert got["injured"] < 0.05 and 0.3 < got["doubt"] < 0.7, got
    assert fit_status_factors(outs[:10]) == {}

    assert line_rows([Outcome("t", 3, "g", "t:a", "k1", True, 0.8, "ok",
                              True, 45.0),
                      Outcome("t", 3, "g", "t:b", "k2", True, 0.8, "ok",
                              False, 0.0)],
                     {"k1": "MED", "k2": "MED"}, {("k1", 3): 4.0}) == [
        {"key": "k1", "pos": "MED", "j": 3, "line": 0.8, "share": 0.5,
         "mins": 45.0, "pts": 4.0}]
    print("ffcore.startprob self-test OK")


if __name__ == "__main__":
    _selftest()
