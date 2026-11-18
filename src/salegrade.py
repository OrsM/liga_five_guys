"""Grades a sale, real or hypothetical, by the points it realised against holding the player.

A sale whose in_eleven is None (a rival's: only the manager's eleven total is observable, never who is in it) has no
net. Its proxy is the two readings (readings): benched, net is the cash term alone; in the eleven, the cash term plus
the points the player's place would have cost, where cover is the position's average and so the points term is a bound.
The proxy cannot say whether the sale was funded, whether he was in the eleven, what offers were declined or whether
the manager held cover; it can say how the price moved against the sale and the bound on the points."""

import itertools
import math
import sys
from collections.abc import Mapping, Sequence
from typing import NamedTuple

import numpy as np

HORIZONS = ("lock", "next4", "season")
BOOTSTRAP_DRAWS = 2000
BOOTSTRAP_SEED = 131
BOOTSTRAP_TAIL = 2.5


class Sale(NamedTuple):
    player: str
    amount: float
    value: float
    in_eleven: bool | None
    flagged: bool
    funded_buy: bool


class Course(NamedTuple):
    sold: Mapping[str, float]
    cover: Mapping[str, float]
    value_after: Mapping[str, float]


class Graded(NamedTuple):
    sale: Sale
    net: Mapping[str, float]


class Row(NamedTuple):
    case: str
    horizon: str
    n: int
    mean_net: float
    per_million: float


class Score(NamedTuple):
    case: str
    horizon: str
    n: int
    total_net: float
    per_million: float
    loo_min: float
    loo_max: float
    bootstrap_lo: float
    bootstrap_hi: float


class Readings(NamedTuple):
    benched: Graded
    fielded: Graded


def net(sale: Sale, course: Course, rate: float, horizon: str) -> float:
    """Realised net points of a sale at a horizon, with rate points per million euros. Linear in rate. The eleven
    term (cover minus sold) counts only if the player was in the eleven; the cash term is the sale amount less the
    value he had at the horizon's end. A sale with in_eleven None has no net (ValueError): grade its readings."""
    if sale.in_eleven is None:
        raise ValueError("in_eleven is unknown: grade the readings of the sale")
    eleven = course.cover[horizon] - course.sold[horizon] if sale.in_eleven else 0.0
    return eleven + rate * (sale.amount - course.value_after[horizon]) / 1e6


def cases(sale: Sale) -> frozenset[str]:
    """The cases a sale belongs to: exactly one of funds_buy or no_buy, plus flagged, above_value, and bench or eleven when
    in_eleven is known, as they hold."""
    found = {"funds_buy" if sale.funded_buy else "no_buy"}
    if sale.flagged:
        found.add("flagged")
    if sale.in_eleven is not None:
        found.add("eleven" if sale.in_eleven else "bench")
    if sale.amount > sale.value:
        found.add("above_value")
    return frozenset(found)


def grade(sale: Sale, course: Course, rate: float) -> Graded:
    """The sale with its net points at every horizon in HORIZONS."""
    return Graded(sale, {h: net(sale, course, rate, h) for h in HORIZONS})


def readings(sale: Sale, course: Course, rate: float) -> Readings:
    """The sale graded on the same course as benched and as in the eleven, whatever its in_eleven: the proxy of a sale
    whose eleven membership is unknown. Linear in rate, and the two differ only by the points term."""
    return Readings(grade(sale._replace(in_eleven=False), course, rate), grade(sale._replace(in_eleven=True), course, rate))


def summary(graded: Sequence[Graded]) -> list[Row]:
    """One Row per horizon for "all" and then each case present, cases sorted. n counts the sales with the case;
    mean_net is their mean net; per_million is their total net over their total amount in millions. A sale appears
    in every case it has."""
    groups: dict[str, list[Graded]] = {"all": list(graded)}
    for g in graded:
        for case in cases(g.sale):
            groups.setdefault(case, []).append(g)
    names = ["all", *sorted(name for name in groups if name != "all")]
    return [_row(name, groups[name], h) for name in names for h in HORIZONS]


def _row(case: str, members: Sequence[Graded], horizon: str) -> Row:
    total = sum(g.net[horizon] for g in members)
    millions = sum(g.sale.amount for g in members) / 1e6
    n = len(members)
    return Row(case, horizon, n, total / n if n else 0.0, total / millions if millions else 0.0)


def score(case: str, members: Sequence[Graded], horizon: str) -> Score:
    """The members' total net at the horizon and per million of amount, the total with one sale left out (its min and
    max) and a bootstrap interval of the total (BOOTSTRAP_DRAWS resamples of the nets under BOOTSTRAP_SEED, the
    BOOTSTRAP_TAIL and 100 less BOOTSTRAP_TAIL percentiles). The same members in any order, or the same sale graded
    twice, give the same Score; no members gives zeros."""
    nets = sorted(g.net[horizon] for g in members)
    if not nets:
        return Score(case, horizon, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    total = math.fsum(nets)
    millions = math.fsum(g.sale.amount for g in members) / 1e6
    draws = np.random.default_rng(BOOTSTRAP_SEED).integers(0, len(nets), (BOOTSTRAP_DRAWS, len(nets)))
    lo, hi = np.percentile(np.asarray(nets)[draws].sum(axis=1), [BOOTSTRAP_TAIL, 100 - BOOTSTRAP_TAIL])
    return Score(case, horizon, len(nets), total, total / millions if millions else 0.0,
                 min(total - x for x in nets), max(total - x for x in nets), float(lo), float(hi))


def scores(graded: Sequence[Graded], wanted: Sequence[str] = ("all", "bench", "eleven")) -> list[Score]:
    """One Score per horizon for each wanted case ("all" is every sale; another case, the sales with it), in the order of
    wanted."""
    return [score(case, graded if case == "all" else [g for g in graded if case in cases(g.sale)], h)
            for case in wanted for h in HORIZONS]


def proxy_scores(proxied: Sequence[Readings]) -> list[Score]:
    """One Score per horizon for the benched readings ("bench-proxy": the cash term alone) and for the fielded ones
    ("eleven-proxy bound": cash plus the position-average points term)."""
    return [score(case, [getattr(r, side) for r in proxied], h)
            for case, side in (("bench-proxy", "benched"), ("eleven-proxy bound", "fielded")) for h in HORIZONS]


def measure_values(rows: Sequence[Row]) -> dict[str, float]:
    """For each Row three keys: "<case>.<horizon>.n", ".mean" and ".per_million". Keys are distinct across rows."""
    pairs = []
    for r in rows:
        stem = f"{r.case}.{r.horizon}"
        pairs += [(f"{stem}.n", float(r.n)), (f"{stem}.mean", r.mean_net), (f"{stem}.per_million", r.per_million)]
    return dict(pairs)


def _flat(x: float) -> dict[str, float]:
    return {h: x for h in HORIZONS}


def _selftest() -> None:
    tol = 1e-9
    course_a = Course(
        sold={"lock": 5.0, "next4": 9.0, "season": 40.0},
        cover={"lock": 3.0, "next4": 12.0, "season": 30.0},
        value_after={"lock": 1e6, "next4": 3e6, "season": 0.5e6},
    )
    course_b = Course(sold=_flat(0.0), cover=_flat(0.0), value_after=_flat(0.5e6))
    pairs = [
        (Sale("a", 2e6, 1.5e6, True, False, True), course_a),
        (Sale("b", 0.5e6, 1e6, False, True, False), course_b),
        (Sale("c", 4e6, 4e6, True, True, False), course_a),
    ]

    for sale, course in pairs:
        for h in HORIZONS:
            assert abs((net(sale, course, 10.0, h) - net(sale, course, 0.0, h))
                       - 10.0 * (sale.amount - course.value_after[h]) / 1e6) < tol, "net is linear in rate"

    bench = Sale("x", 2e6, 2e6, False, False, False)
    bench_course = Course(sold=_flat(7.0), cover=_flat(3.0), value_after=_flat(2e6))
    for rate, h in itertools.product((0.0, 1.0, 50.0), HORIZONS):
        assert abs(net(bench, bench_course, rate, h)) < tol, "benched sale at its value nets 0"

    even = Sale("y", 2e6, 2e6, True, False, False)
    even_course = Course(sold=_flat(7.0), cover=_flat(7.0), value_after=_flat(2e6))
    for rate, h in itertools.product((0.0, 1.0, 50.0), HORIZONS):
        assert abs(net(even, even_course, rate, h)) < tol, "in-eleven sale with cover == sold at value nets 0"

    for sale, course in pairs:
        higher = sale._replace(amount=sale.amount + 1e6)
        for h in HORIZONS:
            assert net(higher, course, 5.0, h) > net(sale, course, 5.0, h), "a higher amount nets more"

    for in_eleven, flagged, funded, amount in itertools.product((True, False), (True, False), (True, False),
                                                               (0.5e6, 1e6, 2e6)):
        s = Sale("z", amount, 1e6, in_eleven, flagged, funded)
        c = cases(s)
        assert ("funds_buy" in c) != ("no_buy" in c), "exactly one buy case"
        assert ("bench" in c) == (not in_eleven), "bench iff not in the eleven"
        assert ("flagged" in c) == flagged, "flagged case as flagged"
        assert ("above_value" in c) == (amount > 1e6), "above_value case as amount exceeds value"

    graded = [grade(s, c, 5.0) for s, c in pairs]
    rows = summary(graded)
    by = {(r.case, r.horizon): r for r in rows}
    present = {case for g in graded for case in cases(g.sale)}
    assert len(rows) == len(HORIZONS) * (1 + len(present)), "one row per horizon for all and each case"
    for h in HORIZONS:
        for case in ["all", *present]:
            members = graded if case == "all" else [g for g in graded if case in cases(g.sale)]
            total = sum(g.net[h] for g in members)
            millions = sum(g.sale.amount for g in members) / 1e6
            r = by[(case, h)]
            assert r.n == len(members), "n counts the sales with the case"
            assert abs(r.mean_net - total / len(members)) < tol, "mean_net is the mean net"
            assert abs(r.per_million - total / millions) < tol, "per_million is total net over total millions"

    values = measure_values(rows)
    assert len(values) == 3 * len(rows), "three keys per row, no duplicates"
    for r in rows:
        stem = f"{r.case}.{r.horizon}"
        assert values[f"{stem}.n"] == float(r.n)
        assert values[f"{stem}.mean"] == r.mean_net
        assert values[f"{stem}.per_million"] == r.per_million

    unknown = Sale("u", 3e6, 2e6, None, False, False)
    try:
        net(unknown, course_a, 1.0, "lock")
    except ValueError:
        pass
    else:
        raise AssertionError("a sale with unknown eleven membership has no net")
    assert not {"bench", "eleven"} & cases(unknown), "neither bench nor eleven when unknown"
    for sale, course in pairs:
        read = readings(sale._replace(in_eleven=None), course, 0.6)
        for h in HORIZONS:
            cash = 0.6 * (sale.amount - course.value_after[h]) / 1e6
            assert abs(read.benched.net[h] - cash) < tol, "benched reading is the cash term alone"
            assert abs(read.fielded.net[h] - cash - (course.cover[h] - course.sold[h])) < tol, "fielded adds the points term"

    nets = [(5.0, 1e6), (-3.0, 2e6), (0.5, 1e6), (12.0, 4e6), (-1.0, 3e6)]
    members = [Graded(Sale("q", amount, amount, True, False, False), _flat(x)) for x, amount in nets]
    base = score("c", members, "lock")
    total = sum(x for x, _ in nets)
    assert base.n == 5 and abs(base.total_net - total) < tol and abs(base.per_million - total / 11.0) < tol
    assert abs(base.loo_min - (total - 12.0)) < tol and abs(base.loo_max - (total + 3.0)) < tol, "leave one out"
    assert base.bootstrap_lo <= base.total_net <= base.bootstrap_hi
    for order in (members[::-1], members[2:] + members[:2], members + members[:0]):
        assert score("c", order, "lock") == base, "order does not matter"
    assert score("c", members, "lock") == base, "the bootstrap seed is fixed"
    assert score("c", [], "lock") == Score("c", "lock", 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    got = scores(graded)
    assert [(s.case, s.horizon) for s in got] == [(c, h) for c in ("all", "bench", "eleven") for h in HORIZONS]
    assert [s.n for s in got if s.case == "bench"] == [sum("bench" in cases(g.sale) for g in graded)] * 3
    proxied = [readings(s._replace(in_eleven=None), c, 0.6) for s, c in pairs]
    prox = proxy_scores(proxied)
    assert [s.case for s in prox] == ["bench-proxy"] * 3 + ["eleven-proxy bound"] * 3 and all(s.n == len(pairs) for s in prox)
    print("salegrade self-test OK")


if __name__ == "__main__" and "--selftest" in sys.argv:
    _selftest()
