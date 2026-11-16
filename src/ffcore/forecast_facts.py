"""The forecast's facts (forecast), grown one at a time: the prior a player's points per match are pulled toward before
his own records. Selectors: ppm_prior. Reads only what the ingest layer exports (current.player_club, current.player_value,
season_facts.last_season); no fold of its own.

ppm_prior: one Prior a player_club row (the position's least-squares line of last season's points per match on ln(value),
at his value; the median of all fitted points when his position has no line).

Named rules, each tested:
  fit_needs_value_and_ten_matches     a last-season row joins the fit only if its player has a value above 0, a position
                                      other than "" and pj >= 10.
  position_line_needs_three           per position the line is least squares only if it has at least 3 rows and
                                      avg(x*x) > avg(x)*avg(x); else its slope is 0.0 (the position's mean y).
  no_value_means_position_mean        a player with no value has x NULL, counted as the position's mean x: his prior is the
                                      position's mean y.
  never_below_zero_else_null_median   the prior is max(line, 0.0); a player whose position has no fit (none of the position
                                      qualified, or position "") has a NULL line and gets the median y of all fitted rows.
  last_season_with_null_points        a qualifying row with no points is an error, not a skipped row."""
from __future__ import annotations

import math
import random
import statistics
from collections import defaultdict
from collections.abc import Iterable
from typing import Any, NamedTuple

from ffcore import observations as ob
from ffcore import state
from ffcore.current import player_club, player_value
from ffcore.season_facts import last_season

MIN_MATCHES = 10
MIN_FIT = 3


class Prior(NamedTuple):
    player: str
    prior: float | None


class _Point(NamedTuple):
    pos: str
    x: float
    y: float


class _Fit(NamedTuple):
    mean_y: float
    mean_x: float
    slope: float


def _fitted_points(world: state.World) -> tuple[_Point, ...]:
    pos = {c.player: c.pos for c in player_club(world)}
    value = {v.player: v.value for v in player_value(world)}
    out = []
    for r in last_season(world):
        if r.pj is None or r.pj < MIN_MATCHES or pos.get(r.ff_id, "") == "" or r.ff_id not in value:
            continue
        if r.pts is None:
            raise ValueError("last_season_with_null_points: %s has %s matches and no points" % (r.ff_id, r.pj))
        out.append(_Point(pos[r.ff_id], math.log(value[r.ff_id]), r.pts / r.pj))
    return tuple(out)


def _fit_by_position(points: Iterable[_Point]) -> dict[str, _Fit]:
    by_pos: dict[str, list[_Point]] = defaultdict(list)
    for p in points:
        by_pos[p.pos].append(p)
    out = {}
    for pos, rows in by_pos.items():
        n = len(rows)
        mx = sum(r.x for r in rows) / n
        my = sum(r.y for r in rows) / n
        xx = sum(r.x * r.x for r in rows) / n
        xy = sum(r.x * r.y for r in rows) / n
        slope = (xy - mx * my) / (xx - mx * mx) if n >= MIN_FIT and xx > mx * mx else 0.0
        out[pos] = _Fit(my, mx, slope)
    return out


def ppm_prior(world: state.World) -> tuple[Prior, ...]:
    """One Prior per current.player_club row, in no particular order (rules as in the module docstring)."""
    points = _fitted_points(world)
    fits = _fit_by_position(points)
    value = {v.player: v.value for v in player_value(world)}
    median = statistics.median(p.y for p in points) if points else None
    out = []
    for c in player_club(world):
        fit = fits.get(c.pos)
        if fit is None:
            out.append(Prior(c.player, median))
            continue
        x = value.get(c.player)
        dx = 0.0 if x is None else math.log(x) - fit.mean_x
        out.append(Prior(c.player, max(fit.mean_y + fit.slope * dx, 0.0)))
    return tuple(out)


D0, D1, D2, D3, D4, D5 = ("2026-08-13T0000Z", "2026-08-14T1000Z", "2026-08-16T1000Z", "2026-08-20T1000Z",
                          "2026-08-23T1000Z", "2026-08-30T1000Z")
CUTS = (D0, D1, D2, D3, D4)
FIT_Y = {"q1": 4.0, "q2": 3.0, "q3": 2.5, "qn": 1.0, "r1": 2.0, "r2": 4.0,
         "m1": 6.0, "m2": 3.0, "m3": 1.0, "m4": 0.0, "c1": 5.0}
LINE_VALUES = {"f1": 10.0, "f2": 100.0, "f3": 1000.0}


def _mk(at: str, ff: str, value: float | None, position: str, club: str) -> ob.Market:
    return ob.Market(at, "s", value, ff, position, club)


def _p(at: str, ff: str, points: float | None, games: float, season: str) -> ob.Points:
    return ob.Points(at, "s", points, games, 0.0, ff, "", "", "", season)


def _league(at: str, por: tuple[float, float], q2: float) -> list[ob.Market]:
    return [_mk(at, "q1", 10.0, "DEL", "A"), _mk(at, "q2", q2, "DEL", "B"), _mk(at, "q3", 1000.0, "DEL", "C"),
            _mk(at, "qn", None, "DEL", "D"), _mk(at, "r1", por[0], "POR", "E"), _mk(at, "r2", por[1], "POR", "F"),
            _mk(at, "m1", 10.0, "MED", "G"), _mk(at, "m2", 100.0, "MED", "H"), _mk(at, "m3", 1000.0, "MED", "I"),
            _mk(at, "m4", 1e12, "MED", "J"), _mk(at, "f1", 10.0, "DEF", "K"), _mk(at, "f2", 100.0, "DEF", "L"),
            _mk(at, "f3", 1000.0, "DEF", "M"), _mk(at, "c1", 3.0, "Entrenador", "N")]


def _tables(por: tuple[float, float] = (5.0, 20.0)) -> dict[str, list[Any]]:
    y = dict(FIT_Y)
    y.update({ff: 1.0 + 0.5 * math.log(v) for ff, v in LINE_VALUES.items()})
    points = [_p(D1, ff, 10.0 * v, 10.0, "25") for ff, v in y.items()] + [_p(D1, "q1", 3.0, 3.0, "26")]
    return {"market": _league(D1, por, 100.0) + _league(D3, por, 120.0), "points": points}


def _scratch() -> ob.Observations:
    return ob.of(_tables())


def _view(world: state.World) -> Any:
    return (world.cut, ppm_prior(world))


def _priors(world: state.World) -> dict[str, float]:
    out = {}
    for p in ppm_prior(world):
        assert p.prior is not None, p.player
        out[p.player] = p.prior
    return out


def _selftest() -> None:
    """The named rules on a small league; later rows leave a cut unchanged, any subset or order of cuts gives each
    the same view, and a cut alone equals the cut inside a sweep."""
    obs = _scratch()
    views = {c: _view(state.world_at(obs, c)) for c in CUTS}
    assert state.sweep(obs, CUTS, _view) == [views[c] for c in CUTS]
    rng = random.Random(7)
    for _ in range(8):
        picked = [rng.choice(CUTS) for _ in range(rng.randint(1, 8))]
        assert state.sweep(obs, picked, _view) == [views[c] for c in picked]
    later = _tables()
    later["points"].append(_p(D5, "q2", 1.0, 10.0, "25"))
    assert all(_view(state.world_at(ob.of(later), c)) == views[c] for c in CUTS)

    assert ppm_prior(state.world_at(obs, D0)) == () and len(views[D1][1]) == len(set(p.player for p in views[D1][1]))
    priors = _priors(state.world_at(obs, D1))
    assert set(priors) == {c.player for c in player_club(state.world_at(obs, D1))}

    for ff, v in LINE_VALUES.items():
        assert abs(priors[ff] - (1.0 + 0.5 * math.log(v))) < 1e-9, "a perfect line is reproduced"
    assert priors["r1"] == priors["r2"] == 3.0, "two qualifying players: the position's mean"
    assert abs(priors["qn"] - (4.0 + 3.0 + 2.5) / 3) < 1e-12, "no value: the position's mean (his own 10 matches are not fitted)"
    assert priors["m4"] == 0.0 and priors["m1"] > 0.0, "a line below zero is 0.0"
    assert all(p >= 0.0 for p in priors.values() if p is not None)
    fitted = [FIT_Y[ff] for ff in FIT_Y if ff not in ("qn", "c1")] + [1.0 + 0.5 * math.log(v) for v in LINE_VALUES.values()]
    assert abs(priors["c1"] - statistics.median(fitted)) < 1e-12, "a coach: the median of all fitted points"

    with_new = _tables()
    with_new["market"] += [_mk(D1, "pn", 7.0, "DEL", "P"), _mk(D3, "pn", 7.0, "DEL", "P")]
    with_new["points"].append(_p(D1, "pn", 9.0, 9.0, "25"))
    grown = _priors(state.world_at(ob.of(with_new), D1))
    assert "pn" in grown and all(grown[k] == priors[k] for k in priors), "a last-season row of 9 matches is not fitted"

    other = _priors(state.world_at(ob.of(_tables(por=(50.0, 200.0))), D1))
    assert all(other[k] == priors[k] for k in priors if k not in ("r1", "r2")), "another position's data is not used"

    broken = _tables()
    broken["points"].append(_p(D1, "q3", None, 10.0, "25"))
    try:
        ppm_prior(state.world_at(ob.of(broken), D1))
    except ValueError:
        pass
    else:
        raise AssertionError("a qualifying row with no points must not be skipped")
    print("ffcore.forecast_facts self-test OK")


if __name__ == "__main__":
    _selftest()
