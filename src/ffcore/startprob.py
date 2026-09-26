from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import NamedTuple

import numpy as np

__all__ = ["Obs", "Calibration", "calibrate", "fit", "observations",
           "af_prob", "fit_start_fallbacks", "NEUTRAL_START", "ABSENT_START"]

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


def observations(lineups, starters, cut: str, neutral: float = NEUTRAL_START,
                 absent: float = ABSENT_START, xw=None) -> list[Obs]:
    from ffcore.text import norm
    from ffcore.tidy import MATCH_LEN, minutes_played

    truth = [r for r in starters if r.get("role")]
    if not truth:
        return []
    last = max(r["observed_at"] for r in truth)
    truth = [r for r in truth if r["observed_at"] == last]
    teams = {r.get("team_slug") for r in truth}
    truth_of = {r["player_slug"]: r for r in truth}

    wide: dict[str, dict] = {}
    narrow: dict[str, dict] = {}
    for r in sorted((r for r in lineups
                     if r.get("observed_at", "") <= cut
                     and r.get("team_slug") in teams),
                    key=lambda r: r.get("observed_at", "")):
        if (r.get("source") or "").startswith("futbol"):
            wide[r.get("player_slug") or norm(r.get("player_name"))] = r
            continue
        key = (xw.key_of(r) if xw else None) or norm(
            r.get("player_name") or r.get("player_slug") or "")
        if key:
            narrow[key] = r

    out = []
    for slug in sorted(set(wide) | set(truth_of)):
        row = wide.get(slug)
        seen = truth_of.get(slug) or {}
        fp = absent / 100.0
        if row is not None:
            fp = neutral / 100.0
            try:
                fp = float(row.get("start_pct")) / 100.0
            except (TypeError, ValueError):
                pass
        af = narrow.get(norm((row or {}).get("player_name")
                             or seen.get("player_name", "")))
        if af is None and xw is not None:
            af = narrow.get(xw.key_of(row or seen))
        graded = min(1.0, minutes_played(seen.get("role"), seen.get("minute"))
                     / MATCH_LEN)
        out.append(Obs(fp, af_prob(af, 1.0), graded,
                       (row or {}).get("team_slug") or seen.get("team_slug", "")))
    return out


def _shrunk(default_pct: float, bucket) -> float:
    if not bucket:
        return default_pct
    rate = sum(o.started for o in bucket) / len(bucket)
    return ((FALLBACK_K * default_pct / 100.0 + len(bucket) * rate)
            / (FALLBACK_K + len(bucket)) * 100.0)


def fit_start_fallbacks(lineups, starters, cut: str, xw=None
                        ) -> tuple[float, float]:
    obs = observations(lineups, starters, cut, xw=xw)
    return (_shrunk(NEUTRAL_START, [o for o in obs
                                    if abs(o.ff - NEUTRAL_START / 100) < 1e-9]),
            _shrunk(ABSENT_START, [o for o in obs
                                   if abs(o.ff - ABSENT_START / 100) < 1e-9]))


def calibrate(lineups, second, starters, xw) -> Calibration:
    from ffcore.lineupweight import fit_lineup_weight, fit_status_factors

    both = lineups + second
    cut = min((r.get("observed_at", "") for r in starters), default="")
    cal = Calibration()
    if cut:
        neutral, absent = fit_start_fallbacks(both, starters, cut, xw=xw)
        cal = replace(fit(observations(both, starters, cut, neutral, absent, xw)),
                      neutral_start=neutral, absent_start=absent)
    return replace(cal, lineup_k=fit_lineup_weight(),
                   status_factor=fit_status_factors())


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

    lineups = [
        {"observed_at": "A", "source": "futbolfantasy", "team_slug": "t",
         "player_slug": "starter-man", "player_name": "Starter Man",
         "start_pct": "80", "role": "starter"},
        {"observed_at": "A", "source": "analitica", "team_slug": "t",
         "player_slug": "af-starter-man", "player_name": "Starter Man",
         "start_pct": "", "role": "starter"},
        {"observed_at": "A", "source": "futbolfantasy", "team_slug": "t",
         "player_slug": "bench-man", "player_name": "Bench Man",
         "start_pct": "20", "role": "sub"},
        {"observed_at": "A", "source": "futbolfantasy", "team_slug": "t",
         "player_slug": "vague-man", "player_name": "Vague Man",
         "start_pct": "", "role": "sub"},
        {"observed_at": "Z", "source": "futbolfantasy", "team_slug": "t",
         "player_slug": "starter-man", "player_name": "Starter Man",
         "start_pct": "99", "role": "starter"},
        {"observed_at": "A", "source": "futbolfantasy", "team_slug": "other",
         "player_slug": "elsewhere", "player_name": "Elsewhere",
         "start_pct": "90", "role": "starter"},
    ]
    starters = [
        {"observed_at": "K", "team_slug": "t", "player_slug": "starter-man",
         "player_name": "Starter Man", "role": "starter"},
        {"observed_at": "K", "team_slug": "t", "player_slug": "bench-man",
         "player_name": "Bench Man", "role": "sub"},
        {"observed_at": "K", "team_slug": "t", "player_slug": "surprise-man",
         "player_name": "Surprise Man", "role": "starter"},
    ]
    got = observations(lineups, starters, cut="M")
    by = {o.ff: o for o in got}
    assert len(got) == 4 and all(o.group == "t" for o in got), got
    assert by[0.8].started == 1.0 and by[0.2].started == 0.0
    assert by[0.8].af == 1.0 and by[0.2].af is None
    assert abs(by[0.6].ff - 0.6) < 1e-9 and by[0.15].started == 1.0
    assert observations(lineups, [], cut="M") == []

    npct, apct = fit_start_fallbacks(lineups, starters, cut="M")
    assert abs(npct - (8 * 60 + 1 * 0) / 9) < 1e-9, npct
    assert abs(apct - (8 * 15 + 1 * 100) / 9) < 1e-9, apct
    assert fit_start_fallbacks([], [], cut="M") == (60.0, 15.0)

    from ffcore.crosswalk import Crosswalk, Player
    xw = Crosswalk({"starter man": Player("starter man", ff_slug="starter-man",
                                          af_slug="af-only-slug")})
    odd = [r for r in lineups if r["source"] == "futbolfantasy"] + [
        {"observed_at": "A", "source": "analitica", "team_slug": "t",
         "player_slug": "af-only-slug", "player_name": "S. Man",
         "start_pct": "75", "role": "starter"}]
    assert all(o.af is None for o in observations(odd, starters, cut="M"))
    assert any(o.af == 0.75 for o in observations(odd, starters, cut="M", xw=xw))

    graded = {o.ff: o.started for o in observations(
        [{"observed_at": "A", "source": "futbolfantasy", "team_slug": "t",
          "player_slug": "hooked-early", "player_name": "Hooked Early",
          "start_pct": "90", "role": "starter"},
         {"observed_at": "A", "source": "futbolfantasy", "team_slug": "t",
          "player_slug": "heavy-sub", "player_name": "Heavy Sub",
          "start_pct": "10", "role": "sub"}],
        [{"observed_at": "K", "team_slug": "t", "player_slug": "hooked-early",
          "player_name": "Hooked Early", "role": "starter", "minute": "45"},
         {"observed_at": "K", "team_slug": "t", "player_slug": "heavy-sub",
          "player_name": "Heavy Sub", "role": "sub", "minute": "45"}],
        cut="M")}
    assert abs(graded[0.9] - 0.5) < 1e-9 and abs(graded[0.1] - 0.5) < 1e-9

    print("ffcore.startprob self-test OK")


if __name__ == "__main__":
    _selftest()
