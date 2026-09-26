from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field, replace
from typing import NamedTuple

import numpy as np

from ffcore.text import norm
from ffcore.tidy import MATCH_LEN, minutes_played

__all__ = ["Obs", "Outcome", "Calibration", "calibrate", "fit", "outcomes",
           "observations", "af_prob", "fit_start_fallbacks", "NEUTRAL_START",
           "ABSENT_START"]

INTERCEPT = [round(-3.0 + 0.5 * i, 1) for i in range(13)]
SLOPE = [round(0.2 + 0.4 * i, 1) for i in range(15)]
WEIGHTS = [round(0.1 * i, 1) for i in range(11)]

FLOOR, CEIL = 0.01, 0.97
NEUTRAL_START = 60.0
ABSENT_START = 15.0
FALLBACK_K = 8.0


class Obs(NamedTuple):
    ff: float | None
    af: float | None
    started: float
    group: str = ""


def af_prob(row, titular: float) -> float | None:
    if not row:
        return None
    pct = row.get("start_pct")
    if pct not in (None, ""):
        try:
            return min(1.0, max(0.0, float(pct) / 100.0))
        except (TypeError, ValueError):
            pass
    return titular if row.get("role") == "starter" else None


def _platt(p: float, alpha: float, beta: float) -> float:
    p = min(1.0 - 1e-6, max(1e-6, p))
    z = alpha + beta * math.log(p / (1.0 - p))
    q = 1.0 / (1.0 + math.exp(-max(-40.0, min(40.0, z))))
    return min(CEIL, max(FLOOR, q))


@dataclass
class Calibration:
    alpha: float = 0.0
    beta: float = 1.0
    weight: float = 0.0
    titular: float = 0.9
    neutral_start: float = NEUTRAL_START
    absent_start: float = ABSENT_START
    lineup_k: float | None = None
    status_factor: dict[str, float] = field(default_factory=dict)

    def p(self, ff_pct, af=None) -> float:
        if ff_pct is None:
            base = None
        elif (self.alpha, self.beta) == (0.0, 1.0):
            base = ff_pct / 100.0
        else:
            base = _platt(ff_pct / 100.0, self.alpha, self.beta)
        q = af_prob(af, self.titular)
        if base is None:
            return 0.0 if q is None else q
        if q is None or not self.weight:
            return base
        return self.weight * q + (1.0 - self.weight) * base


def _titular_rate(obs) -> float:
    hits = [o for o in obs if o.af is not None and o.af >= 0.999]
    if not hits:
        return 0.9
    return min(0.99, max(0.5, sum(o.started for o in hits) / len(hits)))


def _grid_brier(obs) -> np.ndarray:
    ff = np.array([np.nan if o.ff is None else o.ff for o in obs])
    af = np.array([np.nan if o.af is None else o.af for o in obs])
    y = np.array([o.started for o in obs])
    al = np.array(INTERCEPT)[:, None, None, None]
    be = np.array(SLOPE)[None, :, None, None]
    w = np.array(WEIGHTS)[None, None, :, None]
    p = np.clip(ff, 1e-6, 1.0 - 1e-6)
    z = np.clip(al + be * np.log(p / (1.0 - p)), -40.0, 40.0)
    base = np.clip(1.0 / (1.0 + np.exp(-z)), FLOOR, CEIL)
    base = np.where((al == 0.0) & (be == 1.0), ff, base)
    blend = np.where(np.isnan(af) | (w == 0.0), base, w * af + (1.0 - w) * base)
    pred = np.where(np.isnan(ff), np.nan_to_num(af), blend)
    return ((pred - y) ** 2).mean(axis=-1)


def _best(obs, titular: float) -> Calibration:
    i, j, k = np.unravel_index(np.argmin(_grid_brier(obs)),
                               (len(INTERCEPT), len(SLOPE), len(WEIGHTS)))
    return Calibration(INTERCEPT[i], SLOPE[j], WEIGHTS[k], titular)


def _brier(c: Calibration, obs) -> float:
    return sum((c.p(None if o.ff is None else o.ff * 100.0,
                    None if o.af is None else {"start_pct": o.af * 100})
                - o.started) ** 2 for o in obs)


def fit(obs) -> Calibration:
    obs = [o for o in obs if o.ff is not None or o.af is not None]
    titular = _titular_rate(obs)
    raw = Calibration(titular=titular)
    groups = sorted({o.group for o in obs})
    if len(obs) < 3 or len(groups) < 2:
        return raw
    held_out = sum(_brier(_best([o for o in obs if o.group != g], titular),
                          [o for o in obs if o.group == g]) for g in groups)
    return _best(obs, titular) if held_out < _brier(raw, obs) else raw


class Outcome(NamedTuple):
    at: str
    jornada: int
    group: str
    who: str
    key: str | None
    listed: bool
    ff: float | None
    status: str
    af: float | None
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
    narrow: dict[str, list] = {}
    for r in sorted(lineups, key=lambda r: r.get("observed_at", "")):
        stamp = r.get("observed_at", "")
        if (r.get("source") or "").startswith("futbol"):
            slug = r.get("player_slug") or norm(r.get("player_name"))
            wide.setdefault(r.get("team_slug"), {}).setdefault(
                slug, []).append((stamp, r))
            continue
        for k in {xw.key_of(r) if xw else None,
                  norm(r.get("player_name") or r.get("player_slug") or "")}:
            if k:
                narrow.setdefault(k, []).append((stamp, r))

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
            seen = row or played
            key = xw.key_of(seen) if xw else None
            af = next((a for k in (key, norm(seen.get("player_name") or ""))
                       if k and (a := _last_before(narrow.get(k, []), cut))),
                      None)
            out.append(Outcome(
                cut, j, "%s:%s" % (match, team), "%s:%s" % (team, slug), key,
                row is not None,
                _pct(row) if row else None,
                (row.get("status") or "ok") if row else "",
                af_prob(af, 1.0), played is not None,
                minutes_played(played["role"], played.get("minute"))
                if played else 0.0))
    return sorted(out, key=lambda o: (o.at, o.group))


def _share(o: Outcome) -> float:
    return min(1.0, o.mins / MATCH_LEN)


def observations(outs: list[Outcome], neutral: float = NEUTRAL_START,
                 absent: float = ABSENT_START) -> list[Obs]:
    return [Obs(o.ff if o.ff is not None
                else (neutral if o.listed else absent) / 100.0,
                o.af, _share(o), o.group) for o in outs]


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


def calibrate(outs: list[Outcome], line_rows: list[dict]) -> Calibration:
    from ffcore.lineupweight import fit_lineup_weight, fit_status_factors

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

    assert af_prob({"start_pct": "75"}, 0.9) == 0.75
    assert af_prob({"role": "starter"}, 0.93) == 0.93
    assert af_prob({"role": "sub"}, 0.9) is None
    assert af_prob(None, 0.9) is None

    raw = Calibration()
    assert raw.p(80.0) == 0.8
    assert raw.p(100.0) == 1.0 and raw.p(0.0) == 0.0
    assert raw.p(80.0, {"start_pct": "20"}) == 0.8
    assert raw.p(None) == 0.0
    assert Calibration(weight=0.5).p(None, {"start_pct": "40"}) == 0.4

    sharp = [Obs(p, None, st, "sheet%d" % (i % 6))
             for i, (p, st) in enumerate([(0.8, 1), (0.7, 1), (0.3, 0),
                                          (0.2, 0)] * 12)]
    cal = fit(sharp)
    assert cal.beta > 1.0 and cal.p(80.0) > 0.8, cal
    noise = [Obs(0.5, None, i % 2, "sheet%d" % (i % 5)) for i in range(20)]
    for obs in (noise, [Obs(0.5, None, 1)],
                [Obs(0.9, None, i < 11, "same") for i in range(22)]):
        got = fit(obs)
        assert (got.alpha, got.beta, got.weight) == (0.0, 1.0, 0.0), got
    blended = fit([Obs(0.5, float(st), st, "sheet%d" % (i % 5))
                   for i, st in enumerate([1, 0] * 15)])
    assert blended.weight > 0.5, blended

    grid_obs = sharp + [Obs(None, 1.0, 1, "sheet1"), Obs(0.4, 0.0, 0, "sheet2")]
    brier = _grid_brier(grid_obs)
    for i, j, k in [(0, 0, 0), (6, 2, 0), (6, 2, 5), (3, 9, 10)]:
        c = Calibration(INTERCEPT[i], SLOPE[j], WEIGHTS[k])
        assert abs(brier[i, j, k] - _brier(c, grid_obs) / len(grid_obs)) < 1e-12

    assert abs(_titular_rate([Obs(0.5, 1.0, 1)] * 9 + [Obs(0.5, 1.0, 0)])
               - 0.9) < 1e-9
    assert _titular_rate([Obs(0.5, None, 1)]) == 0.9

    import datetime as dt
    from ffcore.crosswalk import Crosswalk, Player

    locks = {1: dt.datetime(2026, 8, 15, 19, 30, tzinfo=dt.timezone.utc)}
    before, after = "2026-08-14T1000Z", "2026-08-16T1000Z"
    lineups = [
        {"observed_at": before, "source": "futbolfantasy", "team_slug": "t",
         "player_slug": "starter-man", "player_name": "Starter Man",
         "start_pct": "80", "role": "starter"},
        {"observed_at": before, "source": "analitica", "team_slug": "t",
         "player_slug": "af-starter-man", "player_name": "Starter Man",
         "start_pct": "", "role": "starter"},
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
    assert (by["t:starter-man"].ff, by["t:starter-man"].af,
            by["t:starter-man"].mins) == (0.8, 1.0, 90.0), by["t:starter-man"]
    assert by["t:bench-man"].mins == 0.0 and by["t:bench-man"].af is None
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

    xw = Crosswalk({"starter man": Player("starter man", ff_slug="starter-man",
                                          af_slug="af-only-slug")})
    odd = [r for r in lineups if r["source"] == "futbolfantasy"] + [
        {"observed_at": before, "source": "analitica", "team_slug": "t",
         "player_slug": "af-only-slug", "player_name": "S. Man",
         "start_pct": "75", "role": "starter"}]
    plain = outcomes(odd, starters, locks, {"m1": 1}, None)
    assert all(o.af is None for o in plain)
    keyed = outcomes(odd, starters, locks, {"m1": 1}, xw)
    assert [o.af for o in keyed if o.key == "starter man"] == [0.75], keyed

    print("ffcore.startprob self-test OK")


if __name__ == "__main__":
    _selftest()
