"""Grades a sale, real or hypothetical, by the points it realised against holding the player."""

import itertools
import sys
from collections.abc import Mapping, Sequence
from typing import NamedTuple

HORIZONS = ("lock", "next4", "season")


class Sale(NamedTuple):
    player: str
    amount: float
    value: float
    in_eleven: bool
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


def net(sale: Sale, course: Course, rate: float, horizon: str) -> float:
    """Realised net points of a sale at a horizon, with rate points per million euros. Linear in rate. The eleven
    term (cover minus sold) counts only if the player was in the eleven; the cash term is the sale amount less the
    value he had at the horizon's end."""
    eleven = course.cover[horizon] - course.sold[horizon] if sale.in_eleven else 0.0
    return eleven + rate * (sale.amount - course.value_after[horizon]) / 1e6


def cases(sale: Sale) -> frozenset[str]:
    """The cases a sale belongs to: exactly one of funds_buy or no_buy, plus flagged, bench and above_value as they hold."""
    found = {"funds_buy" if sale.funded_buy else "no_buy"}
    if sale.flagged:
        found.add("flagged")
    if not sale.in_eleven:
        found.add("bench")
    if sale.amount > sale.value:
        found.add("above_value")
    return frozenset(found)


def grade(sale: Sale, course: Course, rate: float) -> Graded:
    """The sale with its net points at every horizon in HORIZONS."""
    return Graded(sale, {h: net(sale, course, rate, h) for h in HORIZONS})


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
    print("salegrade self-test OK")


if __name__ == "__main__" and "--selftest" in sys.argv:
    _selftest()
