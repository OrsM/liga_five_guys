from __future__ import annotations

import json
import math
import os
import random
import statistics
import sys
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, NamedTuple

import decision
import eleven
import valuation
from ffcore.rules import POSITIONS, XI_SIZE

__all__ = ["FILLER_MAX", "LEVEL_AHEAD", "Point", "Kappa", "Listing", "Level", "Market", "par", "kappa", "cheap", "listing_of",
           "replacement_level", "assess", "grade_path", "read_grade", "write_grade", "daily"]

FILLER_MAX = 3_000_000.0
LEVEL_AHEAD = valuation.NEXT_JORNADAS
LEVEL_HORIZONS = (LEVEL_AHEAD, 2 * LEVEL_AHEAD)

Cells = valuation.Cells
Pos = Mapping[str, str]
Forecast = Mapping[int, float]


class Point(NamedTuple):
    """A player's market price (millions) and his PAR (points over the window)."""
    price: float
    par: float


class Kappa(NamedTuple):
    """The price of a point, million per PAR, read off Points: `slope` the Theil-Sen slope (median of the slopes between all
    pairs of distinct PAR), the bracket `ols` (price on PAR), `reverse` (PAR on price, inverted) and `average` (sum of prices over
    sum of PAR), n the points and `resid_sd` the spread of price about the Theil-Sen line (median intercept)."""
    slope: float
    ols: float
    reverse: float
    average: float
    n: int
    resid_sd: float


class Listing(NamedTuple):
    """The players listed on the market at a cut for at most FILLER_MAX, each with his position and forecast by jornada."""
    cut: str
    pos: Mapping[str, str]
    forecast: Mapping[str, Forecast]


class Level(NamedTuple):
    """The replacement level of one position over cuts: `forecast` and `realised` points per jornada of the best listed cheap
    player by forecast over the horizon, `n` the cuts counted and `listed_per_day` the mean number of listed cheap players."""
    forecast: float
    realised: float
    n: int
    listed_per_day: float


class Market(NamedTuple):
    """What the market says at a cut: the PAR over the window of every squad, rival-owned and listed player (None when the squad
    has no legal eleven or no cheap player is listed at his position) and the Kappa of the prices against them (None when
    they do not determine a positive slope)."""
    par: Mapping[str, float | None]
    kappa: Kappa | None


def cheap(inp: decision.Inputs) -> dict[str, tuple[str, ...]]:
    """The players listed on the market at most FILLER_MAX, by position, in name order."""
    pos, _ = decision.cells_of(inp)
    found: dict[str, list[str]] = {p: [] for p in POSITIONS}
    for c in inp.universe:
        if c.status == "market" and c.asking is not None and c.asking <= FILLER_MAX and c.player in pos:
            found[pos[c.player]].append(c.player)
    return {p: tuple(sorted(v)) for p, v in found.items()}


def undominated(players: Iterable[str], window: Sequence[int], cells: Cells) -> list[str]:
    """The players no other of them matches or beats in every jornada of the window (the first by name among equals)."""
    row = {k: tuple(cells[j].get(k, 0.0) for j in window) for k in players}
    kept: list[str] = []
    for k in sorted(row, key=lambda k: (-sum(row[k]), k)):
        if not any(all(a >= b for a, b in zip(row[o], row[k])) for o in kept):
            kept.append(k)
    return kept


def par(squad: frozenset[str], players: Iterable[str], fillers: Mapping[str, Iterable[str]], window: Sequence[int],
        locks: Mapping[int, str], pos: Pos, cells: Cells) -> dict[str, float | None]:
    """The PAR of each player over the window: V(S) less V(S - i + f), summed per jornada by `valuation.value_of`, with S the squad
    plus the player and f the filler of his position (a player of `fillers` not in S) that leaves the most V. None when S has no
    legal eleven or no filler is listed for the position. A filler's own PAR is at most 0 against the best of them."""
    memo: dict[frozenset[str], float] = {}

    def v(s: frozenset[str]) -> float:
        if s not in memo:
            memo[s] = valuation.value_of(s, valuation.NOTHING, tuple(window), locks, pos, cells).points
        return memo[s]

    best: dict[tuple[str, ...], list[str]] = {}
    out: dict[str, float | None] = {}
    for i in players:
        whole = squad | {i}
        listed = [f for f in fillers.get(pos[i], ()) if f not in whole]
        if eleven.points(whole, pos, {}) is None or not listed:
            out[i] = None
            continue
        rest = whole - {i}
        key = tuple(listed)
        if key not in best:
            best[key] = undominated(listed, window, cells)
        out[i] = v(whole) - max(v(rest | {f}) for f in best[key])
    return out


def _slope(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    pairs = [(y2 - y1) / (x2 - x1) for k, (x1, y1) in enumerate(zip(xs, ys)) for x2, y2 in zip(xs[k + 1:], ys[k + 1:]) if x2 != x1]
    return statistics.median(pairs) if pairs else None


def kappa(points: Sequence[Point]) -> Kappa | None:
    """The Kappa of the points; None when fewer than 3, when the PAR are all equal, or when price and PAR do not rise together.
    Depends on the set of points, not their order. ols <= reverse whenever it exists."""
    n = len(points)
    xs, ys = [p.par for p in points], [p.price for p in points]
    mx, my = math.fsum(xs) / n if n else 0.0, math.fsum(ys) / n if n else 0.0
    sxx, syy = math.fsum((x - mx) ** 2 for x in xs), math.fsum((y - my) ** 2 for y in ys)
    sxy = math.fsum((x - mx) * (y - my) for x, y in zip(xs, ys))
    slope = _slope(xs, ys)
    if n < 3 or sxx <= 0.0 or sxy <= 0.0 or slope is None or math.fsum(xs) <= 0.0:
        return None
    a = statistics.median(y - slope * x for x, y in zip(xs, ys))
    resid = math.fsum((y - a - slope * x) ** 2 for x, y in zip(xs, ys))
    return Kappa(slope, sxy / sxx, syy / sxy, math.fsum(ys) / math.fsum(xs), n, math.sqrt(resid / (n - 2)))


def assess(inp: decision.Inputs, values: Mapping[str, float]) -> Market:
    """The Market at the inputs' cut over the one Window of the board (the jornadas from the next lock): PAR of my squad and of
    every player owned by a rival or listed on the market; the Kappa of `values` (euros, by player: their market value) against
    the PAR of the players with a value and a PAR of 0 or more."""
    pos, cells = decision.cells_of(inp)
    locks = decision.lock_times(inp)
    window = decision.window_of(locks, cells, None).season
    squad = decision.squad_of(inp)
    priced = [c.player for c in inp.universe if c.status != "unlisted" and c.player in pos]
    got = par(squad, sorted(squad) + sorted(priced), cheap(inp), window, locks, pos, cells)
    points = [Point(values[k] / 1e6, p) for k, p in sorted(got.items()) if p is not None and p >= 0.0 and values.get(k)]
    return Market(got, kappa(points))


def listing_of(cut: str, inp: decision.Inputs) -> Listing:
    """The Listing of the inputs made at the cut."""
    pos, cells = decision.cells_of(inp)
    found = {k for ks in cheap(inp).values() for k in ks}
    return Listing(cut, {k: pos[k] for k in found},
                   {k: {j: cells[j][k] for j in sorted(cells) if k in cells[j]} for k in found})


def daily(cuts: Iterable[str]) -> list[str]:
    """The last cut of each day, in order."""
    last: dict[str, str] = {}
    for c in sorted(cuts):
        last[c[:10]] = c
    return sorted(last.values())


def replacement_level(listings: Iterable[Listing], scored: Mapping[str, Mapping[int, float]], done: Iterable[int],
                      ahead: int = LEVEL_AHEAD) -> dict[str, Level]:
    """The Level of each position that has one: over the listings whose first `ahead` forecast jornadas are all done, the best
    listed player of the position by forecast over them, his forecast and what he scored (0 for a jornada he has no points in),
    per jornada, averaged over those listings."""
    over = set(done)
    got: dict[str, list[tuple[float, float, int]]] = {p: [] for p in POSITIONS}
    for lst in listings:
        window = sorted({j for f in lst.forecast.values() for j in f})[:ahead]
        if len(window) < ahead or not set(window) <= over:
            continue
        for p in POSITIONS:
            here = [k for k, q in lst.pos.items() if q == p]
            if not here:
                continue
            top = max(here, key=lambda k: (sum(lst.forecast[k].get(j, 0.0) for j in window), k))
            got[p].append((sum(lst.forecast[top].get(j, 0.0) for j in window) / ahead,
                           sum(scored.get(top, {}).get(j, 0.0) for j in window) / ahead, len(here)))
    return {p: Level(statistics.fmean(f for f, _, _ in rows), statistics.fmean(r for _, r, _ in rows), len(rows),
                     statistics.fmean(n for _, _, n in rows)) for p, rows in got.items() if rows}


def grade_path() -> Path:
    """Where tools/market-grade writes the Levels the board reads: market-grade.json in LFG_STATE_DIR (default
    ~/.local/state/lfg)."""
    return Path(os.environ.get("LFG_STATE_DIR") or Path.home() / ".local/state/lfg") / "market-grade.json"


def write_grade(levels: Mapping[str, Level], path: Path | None = None) -> None:
    """Write the Levels, a document by position of forecast, realised, n and listed_per_day."""
    target = path or grade_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({p: v._asdict() for p, v in levels.items()}, indent=1) + "\n", encoding="utf-8")


def read_grade(path: Path | None = None) -> dict[str, dict[str, Any]]:
    """The document write_grade wrote; empty when there is none."""
    target = path or grade_path()
    return json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}


def _league(rng: random.Random, jornadas: tuple[int, ...], keepers: bool = True) -> tuple[dict[str, str], dict[int, dict[str, float]]]:
    sizes = {"POR": rng.randint(3, 5) if keepers else 0, "DEF": rng.randint(8, 12), "MED": rng.randint(8, 12), "DEL": rng.randint(5, 8)}
    pos = {"%s%d" % (p, i): p for p, n in sizes.items() for i in range(n)}
    ties = rng.random() < 0.5
    return pos, {j: {k: float(rng.randint(0, 5)) if ties else rng.uniform(0, 9) for k in pos} for j in jornadas}


def _case(rng: random.Random, keepers: bool = True) -> tuple[dict[str, str], dict[int, dict[str, float]], frozenset[str], dict[str, tuple[str, ...]]]:
    pos, cells = _league(rng, JORNADAS, keepers)
    squad = frozenset(k for k in pos if rng.random() < 0.45)
    outside = sorted(set(pos) - squad)
    fillers = {p: tuple(k for k in outside if pos[k] == p and rng.random() < 0.5) for p in POSITIONS}
    return pos, cells, squad, fillers


JORNADAS = (5, 6, 7, 8)
LOCKS = {5: "2026-11-27T20:00Z", 6: "2026-12-04T20:00Z", 7: "2026-12-11T20:00Z", 8: "2026-12-18T20:00Z"}


def _v(s: frozenset[str], pos: Pos, cells: Cells) -> float:
    return valuation.value_of(s, valuation.NOTHING, JORNADAS, LOCKS, pos, cells).points


def _selftest_par(rng: random.Random) -> None:
    seen = {"starter": 0, "bench": 0, "market": 0, "none": 0}
    for case in range(120):
        pos, cells, squad, fillers = _case(rng, keepers=case % 6 != 0)
        outside = sorted(set(pos) - squad)
        got = par(squad, sorted(squad) + outside, fillers, JORNADAS, LOCKS, pos, cells)
        legal = eleven.points(squad, pos, {}) is not None
        for i, x in got.items():
            listed = [f for f in fillers[pos[i]] if f not in squad | {i}]
            whole = squad | {i}
            if not listed or eleven.points(whole, pos, {}) is None:
                assert x is None, "no legal eleven or no listed filler gives None, never a made-up level"
                seen["none"] += 1
                continue
            assert x is not None
            best = max(_v(whole - {i} | {f}, pos, cells) for f in listed)
            assert abs(x - (_v(whole, pos, cells) - best)) < 1e-9, "PAR is V(S) less V(S - i + best filler)"
            if i in squad:
                assert legal
                sale = valuation.delta(squad, valuation.selling(i), JORNADAS, LOCKS, pos, cells)
                credit = best - _v(squad - {i}, pos, cells)
                if sale.points is not None:
                    assert abs(x - (-sale.points - credit)) < 1e-9, "a squad player's PAR is the sale's loss less the filler's credit"
                    assert x <= -sale.points + 1e-9, "PAR never exceeds what the sale loses"
                seen["starter" if sale.points is not None and sale.points < -1e-9 else "bench"] += 1
            else:
                seen["market"] += 1
        if legal:
            for i in sorted(squad)[:3]:
                floor = {j: {**cells[j], i: min(cells[j][f] for f in fillers[pos[i]])} for j in JORNADAS} if fillers[pos[i]] else cells
                low = par(squad, [i], fillers, JORNADAS, LOCKS, pos, floor)[i]
                assert low is None or low <= 1e-9, "a player no better than the filler in any jornada has PAR 0 or less"
        names = sorted(pos)
        shuffled = names[:]
        rng.shuffle(shuffled)
        new = {k: "z%s_%s" % (n, k[:3]) for k, n in zip(names, shuffled)}
        again = par(frozenset(new[k] for k in squad), [new[k] for k in sorted(squad) + outside],
                    {p: [new[k] for k in ks] for p, ks in fillers.items()}, JORNADAS, LOCKS, {new[k]: p for k, p in pos.items()},
                    {j: {new[k]: e for k, e in c.items()} for j, c in cells.items()})
        assert all(again[new[k]] == got[k] for k in got), "PAR does not depend on the players' names"
    assert all(n > 10 for n in seen.values()), seen
    pos, cells = _league(rng, JORNADAS)
    ten = frozenset(sorted(pos)[:10])
    assert set(par(ten, sorted(ten), {"POR": ("POR4",), "DEF": ("DEF9",), "MED": ("MED9",), "DEL": ("DEL6",)}, JORNADAS, LOCKS, pos, cells).values()) == {None}


def _selftest_undominated(rng: random.Random) -> None:
    for _ in range(100):
        pos, cells = _league(rng, JORNADAS)
        pool = sorted(pos)[:12]
        kept = undominated(pool, JORNADAS, cells)
        assert set(kept) <= set(pool) and kept
        for k in pool:
            if k not in kept:
                assert any(all(cells[j][o] >= cells[j][k] for j in JORNADAS) for o in kept), "a dropped player is matched everywhere by a kept one"
        for a in kept:
            for b in kept:
                assert a == b or not all(cells[j][b] >= cells[j][a] for j in JORNADAS)


def _prices(rng: random.Random, k: float, n: int, noise: float) -> list[Point]:
    return [Point(k * x + rng.gauss(0.0, noise), x) for x in (rng.uniform(0.0, 60.0) for _ in range(n))]


def _selftest_kappa(rng: random.Random) -> None:
    for k in (0.3, 0.8, 1.4):
        for _ in range(10):
            pts = _prices(rng, k, 60, 1.0)
            got = kappa(pts)
            assert got is not None and abs(got.slope - k) < 0.06 and got.n == 60, "kappa recovers the slope the prices were made with"
            assert got.ols <= got.reverse + 1e-12, "the bracket is ordered"
            assert abs(got.resid_sd - 1.0) < 0.4
            shuffled = pts[:]
            rng.shuffle(shuffled)
            assert kappa(shuffled) == got, "kappa does not depend on the order of the players"
            doubled = kappa([Point(2 * p.price, p.par) for p in pts])
            assert doubled is not None and abs(doubled.slope - 2 * got.slope) < 1e-9, "kappa is homogeneous of degree 1 in prices"
            lifted = kappa([Point(p.price + 5.0, p.par) for p in pts])
            assert lifted is not None and abs(lifted.slope - got.slope) < 1e-9 and abs(lifted.ols - got.ols) < 1e-9, "a constant added to every price moves no slope"
    pts = _prices(rng, 0.8, 60, 1.0)
    star = kappa(pts + [Point(0.0, 400.0)])
    plain = kappa(pts)
    assert star is not None and plain is not None and abs(star.slope - plain.slope) < abs(star.ols - plain.ols), "one star moves the robust slope less than OLS"
    assert kappa([]) is None and kappa(pts[:2]) is None and kappa([Point(float(i), 5.0) for i in range(5)]) is None
    assert kappa([Point(10.0 - i, float(i)) for i in range(6)]) is None, "no positive relation, no price of a point"


def _selftest_level(rng: random.Random) -> None:
    jornadas = list(range(1, 13))
    for _ in range(40):
        count = rng.randint(2, 6)
        players = {"p%d" % i: POSITIONS[i % 4] for i in range(count * 4)}
        scored = {k: {j: float(rng.randint(0, 9)) for j in jornadas} for k in players}
        listings = [Listing("2026-10-%02d" % c, players, {k: {j: rng.uniform(0, 8) for j in jornadas[c:c + 8]} for k in players})
                    for c in range(1, 5)]
        done = list(range(1, 9))
        got = replacement_level(listings, scored, done)
        assert set(got) == set(POSITIONS)
        for p, lv in got.items():
            assert lv.n == len(listings) and abs(lv.listed_per_day - count) < 1e-9
            mean_forecast = statistics.fmean(sum(x.forecast[k].get(j, 0.0) for j in sorted(x.forecast[k])[:LEVEL_AHEAD]) / LEVEL_AHEAD
                                             for x in listings for k in x.pos if x.pos[k] == p)
            assert lv.forecast >= mean_forecast - 1e-9, "the best listed player is above the mean listed player"
        assert replacement_level(listings, scored, []) == {}, "a jornada not done cannot be graded"
        fewer = replacement_level(listings, scored, range(1, 6))
        assert all(fewer[p].n <= got[p].n for p in fewer)
        exact = {k: {j: listings[0].forecast[k].get(j, 0.0) for j in jornadas} for k in players}
        one = replacement_level(listings[:1], exact, done)
        assert all(abs(lv.forecast - lv.realised) < 1e-9 for lv in one.values()), "forecast equal to the outcome grades to no gap"
    assert daily(["2026-11-01T0900Z", "2026-11-01T1800Z", "2026-11-02T0100Z"]) == ["2026-11-01T1800Z", "2026-11-02T0100Z"]


def _selftest_assess() -> None:
    def player(i: int, p: str) -> str:
        return "%s%d" % (p, i)
    squad = {player(i, p): p for p, n in (("POR", 2), ("DEF", 5), ("MED", 5), ("DEL", 3)) for i in range(n)}
    rows = [decision.Player(k, p, 1, None) for k, p in squad.items()]
    universe = [decision.Candidate("cheapD", "DEF", "market", 1e6, 1e6), decision.Candidate("cheapD2", "DEF", "market", 1e6, 1e6), decision.Candidate("starD", "DEF", "market", 50e6, 50e6),
                decision.Candidate("cheapM", "MED", "market", 2e6, 2e6), decision.Candidate("rivalM", "MED", "rival", None, 9e6)]
    e = {k: 2.0 for k in squad} | {"cheapD": 3.0, "cheapD2": 1.0, "starD": 9.0, "cheapM": 1.0, "rivalM": 8.0}
    inp = decision.Inputs(decision.Money(0.0, 0.0, 0.0, 24, XI_SIZE), players=rows, universe=universe,
                          locks=[decision.Lock(j, t) for j, t in LOCKS.items()],
                          cells=[decision.Cell(k, j, v) for k, v in e.items() for j in JORNADAS])
    assert set(cheap(inp)["DEF"]) == {"cheapD", "cheapD2"} and cheap(inp)["DEL"] == ()
    got = assess(inp, {k: 1e6 * (1 + e[k]) for k in e}).par
    assert set(got) == set(squad) | {"cheapD", "cheapD2", "starD", "cheapM", "rivalM"}, "mine, rivals' and listed players are priced"
    assert got["cheapD2"] is not None and got["cheapD2"] <= 1e-9 and got["starD"] is not None and got["starD"] > 0
    assert got["DEL0"] is None, "no cheap forward is listed: no PAR, not an invented level"


def _selftest() -> None:
    rng = random.Random(41)
    _selftest_undominated(rng)
    _selftest_par(rng)
    _selftest_kappa(rng)
    _selftest_level(rng)
    _selftest_assess()
    print("market self-test OK")


if __name__ == "__main__" and "--selftest" in sys.argv:
    _selftest()
