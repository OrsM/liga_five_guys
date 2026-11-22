"""Sell rules: how a standing offer on one of your players is judged. A rule takes the offer (Standing) and the windows of
jornadas the board reads (Context) and gives a Verdict: the key the offers are sorted by (reserve less offer: 0 or less
sells), the Reading of selling him (its points None when the squad without him has no legal eleven), and the reserve
price (None when the rule states none). LIVE is the rule the board runs (rent)."""
from __future__ import annotations

import bisect
import random
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import NamedTuple

import eleven
import hurdle
from ffcore.rules import SALE_ESTIMATE
import valuation
from valuation import Reading, Window

MAX_NIGHTS = 5
FORECAST_NIGHTS = 10
NO_LOSS = 1e-9
TAKE_RATIO = 1.0
TAKE_PERCENTILE = 0.5


class Standing(NamedTuple):
    """An offer on a player: his value, the offer (None: none), the later sale price (SALE_ESTIMATE x value x the price
    forecast's rise at its furthest horizon) and rise, the price forecast's rise at most hurdle.TREND_NIGHTS ahead (None: none)."""
    player: str
    value: float
    offer: float | None
    later: float
    rise: float | None = None


class OfferDistribution(NamedTuple):
    ratios: tuple[float, ...]


NO_OFFERS = OfferDistribution(())


class Context(NamedTuple):
    """What a rule reads: the positions, the Window a sale made now counts for, your cash, the offers seen and the
    rate (LAMBDA, hurdle.lam), and what the points are made of: your squad, each jornada's forecast per player and the locks."""
    pos: Mapping[str, str]
    window: Window
    cash: float
    offers: OfferDistribution = NO_OFFERS
    rate: float = hurdle.FALLBACK_LAMBDA
    squad: frozenset[str] = frozenset()
    cells: valuation.Cells = {}
    locks: Mapping[int, str] = {}

    def sale(self, player: str) -> Reading:
        """The Reading of selling the player now, over the window of the context."""
        return valuation.read(self.squad, valuation.selling(player), self.window, self.locks, self.pos, self.cells)

    def jornadas(self) -> int:
        """The jornadas a sale made now counts for (hurdle.remaining from the window's first to the last forecast)."""
        return hurdle.remaining(self.window.season[0], max(self.cells)) if self.window.season and self.cells else 0


class Verdict(NamedTuple):
    key: float | None
    sale: Reading
    reserve: float | None


Rule = Callable[[Standing, Context], Verdict]


def offer_ratio_distribution(ratios: Iterable[float]) -> OfferDistribution:
    """The distribution of offer over value ratios: the ratios (one per offer, at its first sighting, as
    current.offer_ratios gives them) sorted. Empty when there are none."""
    return OfferDistribution(tuple(sorted(ratios)))


def offer_percentile(dist: OfferDistribution, ratio: float) -> float | None:
    """The share of the offers in dist with a ratio at or below ratio: the empirical distribution function, in [0, 1],
    nondecreasing in ratio, 0 below every offer and 1 at or above the highest. None when dist is empty."""
    if not dist.ratios:
        return None
    return bisect.bisect_right(dist.ratios, ratio) / len(dist.ratios)


def offer_reserve_ratio(m: int, c: float, dist: OfferDistribution, hold: float) -> float:
    """The offer over value at which selling tonight and waiting are worth the same, with m nights left to wait, a cost c
    (a fraction of value) for each night waited and one offer a night drawn from dist, i.i.d. and expiring at the next
    close. hold is what the player is worth if no offer is taken (rho_0); rho_k = mean of max(X, rho_{k-1}) over dist
    minus c. m >= 0 and dist non-empty. Nondecreasing in m and nonincreasing in c when hold is at most the mean of dist
    less c; with hold 0, rho_1 is the mean of dist less c, and with every ratio 1.0 rho_m is 1 - c."""
    if m < 0 or not dist.ratios:
        raise ValueError("m must be >= 0 and dist non-empty")
    rho = hold
    for _ in range(m):
        rho = sum(max(x, rho) for x in dist.ratios) / len(dist.ratios) - c
    return rho


def drift_cost(standing: Standing) -> float:
    """The cost of a night of waiting, as a fraction of value: the price forecast's rise (later over SALE_ESTIMATE times
    value, less 1) spread over FORECAST_NIGHTS and negated, so a falling price costs and a rising one pays. 0 for a value
    that is not positive."""
    if standing.value <= 0:
        return 0.0
    return (1 - standing.later / (SALE_ESTIMATE * standing.value)) / FORECAST_NIGHTS


def waiting_reserve(standing: Standing, dist: OfferDistribution, m: int) -> float:
    """The offer, in money, at which taking it now and waiting for a draw of dist at each of the m closes left are worth the
    same: value times offer_reserve_ratio with the drift_cost of the standing and hold = later over value, never below later;
    later when dist is empty or the value is not positive."""
    if not dist.ratios or standing.value <= 0:
        return standing.later
    return max(standing.later, standing.value * offer_reserve_ratio(m, drift_cost(standing), dist, standing.later / standing.value))


def rent(standing: Standing, ctx: Context) -> Verdict:
    """The one sale rule: money and points at one rate. The reserve is hurdle.sale_price of the points the sale loses (over
    the season of ctx.window) over ctx.jornadas() at ctx.rate, with his value and his rise: the offer above which his
    yield is under the rate's bar (a bench player, who loses nothing, is under it at any offer his price trend allows).
    The key is reserve minus offer (0 or less sells); with no offer it is None. Where the sale has no Delta, key and
    reserve are None."""
    sale = ctx.sale(standing.player)
    if sale.points is None:
        return Verdict(None, sale, None)
    reserve = hurdle.sale_price(0.0 - sale.points, ctx.jornadas(), standing.value, standing.rise, ctx.rate)
    key = None if standing.offer is None else reserve - standing.offer
    return Verdict(key, sale, reserve)


LIVE: Rule = rent
CHALLENGERS: Mapping[str, Rule] = {"rent": rent}


def take_now(ratio: float | None, share: float | None, funds: bool) -> bool:
    """Whether an offer a rule sells to is taken tonight rather than left listed for the next nights' (waiting is free:
    the nightly offers are drawn afresh): its ratio over value is TAKE_RATIO or more, its percentile among the offers seen
    TAKE_PERCENTILE or more, or it funds a buy that closes tonight."""
    return funds or (ratio is not None and ratio >= TAKE_RATIO) or (share is not None and share >= TAKE_PERCENTILE)


def _histogram(counts: Sequence[int], low: float, width: float) -> OfferDistribution:
    return offer_ratio_distribution(low + width * (i + 0.5) for i, n in enumerate(counts) for _ in range(n))


def _waiting_payoff(m: int, c: float, dist: OfferDistribution, hold: float, shift: float, seed: int, runs: int) -> float:
    rng = random.Random(seed)
    ceilings = [offer_reserve_ratio(k, c, dist, hold) + shift for k in range(m)]
    total = 0.0
    for _ in range(runs):
        payoff = hold - c * m
        for night in range(m):
            x = rng.choice(dist.ratios)
            if x >= ceilings[m - night - 1]:
                payoff = x - c * (night + 1)
                break
        total += payoff
    return total / runs


def _selftest_option() -> None:
    study = _histogram((80, 80, 84, 82, 85, 72, 96, 71, 67, 33), 0.90, 0.021)
    flat = offer_ratio_distribution(0.9 + 0.2 * (i + 0.5) / 400 for i in range(400))
    mean = sum(study.ratios) / len(study.ratios)
    assert offer_ratio_distribution([1.1, 0.9, 1.0]).ratios == (0.9, 1.0, 1.1), "the distribution is sorted"
    for c in (0.0, 0.0017, 0.007):
        assert abs(offer_reserve_ratio(1, c, study, 0.0) - (mean - c)) < 1e-12, "one night: the mean offer less the cost"
        rhos = [offer_reserve_ratio(m, c, study, 0.0) for m in range(0, 12)]
        assert all(a <= b + 1e-12 for a, b in zip(rhos, rhos[1:])), "more nights never lowers the reserve"
    for m in (1, 3, 5, 7):
        costs = [offer_reserve_ratio(m, c, study, 0.0) for c in (0.0, 0.0017, 0.007, 0.02)]
        assert all(a >= b for a, b in zip(costs, costs[1:])), "a dearer night never raises the reserve"
    hand = offer_ratio_distribution([1.0, 0.9, 1.1, 1.0, 0.95])
    assert offer_percentile(NO_OFFERS, 1.0) is None, "no offers seen: no percentile"
    assert [offer_percentile(hand, x) for x in (0.8, 0.9, 0.95, 1.0, 1.05, 1.1, 1.2)] == [0.0, 0.2, 0.4, 0.8, 0.8, 1.0, 1.0], "the empirical distribution function"
    grid = [0.85 + 0.01 * i for i in range(40)]
    for dist in (hand, study, flat):
        shares = [offer_percentile(dist, x) or 0.0 for x in grid]
        assert all(0.0 <= g <= 1.0 for g in shares), "a share"
        assert all(a <= b for a, b in zip(shares, shares[1:])), "nondecreasing in the ratio"
        assert (offer_percentile(dist, min(dist.ratios)) or 0.0) > 0.0 and offer_percentile(dist, max(dist.ratios)) == 1.0
    point = offer_ratio_distribution([1.0] * 7)
    for m in (1, 2, 5):
        for c in (0.0, 0.0017, 0.01):
            assert abs(offer_reserve_ratio(m, c, point, 0.0) - (1.0 - c)) < 1e-12, "all ratios 1.0: 1 - c"
    assert offer_reserve_ratio(3, 0.0017, study, 0.0) >= 1.03 and offer_reserve_ratio(5, 0.0017, study, 0.0) >= 1.04
    v = 0.0
    for _ in range(5):
        v = (1 + v * v) / 2
    assert abs(offer_reserve_ratio(5, 0.0, flat, 0.0) - (0.9 + 0.2 * v)) < 0.005, "the flat band's closed form"
    try:
        offer_reserve_ratio(1, 0.0, NO_OFFERS, 0.0)
    except ValueError:
        pass
    else:
        raise AssertionError("no offers, no reserve")

    shelf = Standing("p", 1e7, None, 9.5e6)
    reserves = [waiting_reserve(shelf, study, m) for m in range(0, 8)]
    assert reserves[0] == shelf.later and all(a <= b + 1e-9 for a, b in zip(reserves, reserves[1:])), "more closes left never lowers the reserve"
    assert all(r >= shelf.later for r in reserves) and waiting_reserve(shelf, NO_OFFERS, 5) == shelf.later, "never below later; no offers seen: later"

    for m in (1, 3, 5):
        base = _waiting_payoff(m, 0.0017, study, 0.9, 0.0, 7, 20000)
        assert abs(base - offer_reserve_ratio(m, 0.0017, study, 0.9)) < 0.003, "the reserve is the optimal waiting value"
        for shift in (-0.02, -0.01, 0.01, 0.02):
            assert _waiting_payoff(m, 0.0017, study, 0.9, shift, 7, 20000) <= base + 0.001, "no other threshold waits better"

    value = 1e7
    held = _waiting_payoff(MAX_NIGHTS, 0.0, study, SALE_ESTIMATE, 0.0, 11, 20000)
    shape = {"POR": 1, "DEF": 4, "MED": 4, "DEL": 2}
    squad = {"%s%d" % (q, i): q for q, n in shape.items() for i in range(n)} | {"p": "DEF"}
    still = {1: {k: 1.0 for k in squad} | {"p": 0.0}}
    bench_ctx = Context(squad, Window((1,)), 0.0, study, squad=frozenset(squad), cells=still)
    stand = Standing("p", value, None, SALE_ESTIMATE * value)
    reserve = waiting_reserve(stand, bench_ctx.offers, MAX_NIGHTS)
    assert abs(reserve / value - held) < 0.003, "the waiting reserve is the Monte Carlo waiting value"
    assert waiting_reserve(stand, NO_OFFERS, MAX_NIGHTS) == stand.later, "no offers seen: the later price"
    for drift in (-0.2, 0.0, 0.2):
        s = Standing("p", value, 0.0, SALE_ESTIMATE * value * (1 + drift))
        assert waiting_reserve(s, bench_ctx.offers, MAX_NIGHTS) >= s.later, "a waiting reserve is never below the later price"
    for rise in (None, -0.1, 0.0, 0.1):
        bench = rent(stand._replace(offer=0.9 * value, rise=rise), bench_ctx)
        assert bench.key is not None and bench.key <= 0, "a bench player is sold at any offer near his value"
    assert Context({}, Window(), 0.0) == Context({}, Window(), 0.0, NO_OFFERS, hurdle.FALLBACK_LAMBDA), "the new fields default"
    for ratio, share, funds in [(1.0, None, False), (0.9, 0.5, False), (0.9, 0.1, True), (None, None, True)]:
        assert take_now(ratio, share, funds), "a high ratio, a high percentile or a buy to fund: taken tonight"
    assert not take_now(0.99, 0.49, False) and not take_now(None, None, False), "else left listed"


def _lost(player: str, ctx: Context) -> Mapping[str, float] | None:
    sale = ctx.sale(player)
    return None if sale.points is None else {n: 0.0 - v for n, v in sale.window.items() if v is not None}


def _cells_of(rng: random.Random, value: Mapping[str, float]) -> dict[int, dict[str, float]]:
    return {1: dict(value), 2: {k: v * rng.choice([0.0, 0.5, 1.0, 2.0]) for k, v in value.items()},
            3: {k: rng.choice([0.0, 1.0, 3.0, 5.0]) for k in value}}


def _context(squad: Iterable[str], pos: Mapping[str, str], cells: valuation.Cells) -> Context:
    return Context(pos, Window(sorted(cells)), 0.0, squad=frozenset(squad), cells=cells, locks={1: "2026-12-01T20:00Z"})


def _selftest_no_legal_eleven() -> None:
    shape = {"POR": 1, "DEF": 4, "MED": 4, "DEL": 2}
    pos = {"%s%d" % (p, i): p for p, n in shape.items() for i in range(n)}
    value = {k: 1.0 + i for i, k in enumerate(sorted(pos))}
    rng = random.Random(3)
    cells = _cells_of(rng, value)

    bare = _context(pos, pos, cells)
    for p in pos:
        v = LIVE(Standing(p, value[p], 1e6, value[p]), bare)
        assert _lost(p, bare) is None and v == Verdict(None, bare.sale(p), None) and bare.sale(p).points is None, "eleven players only: nobody has a quotable loss"
    extra = "DEF9"
    pos[extra], value[extra] = "DEF", 0.5
    cells = {j: c | {extra: 0.5} for j, c in cells.items()}
    backed = _context(pos, pos, cells)
    whole = sum(eleven.total(frozenset(k for k in pos if k != extra), c) for c in cells.values())
    for p in pos:
        got = _lost(p, backed)
        if eleven.points(backed.squad - {p}, pos, {}) is None:
            assert got is None, "no replacement fills the slot: no legal eleven"
        else:
            assert got is not None and all(0 <= x < whole for x in got.values()), "covered: costed as the swap, never the whole eleven"
    assert _lost("POR0", backed) is None, "the only keeper with no bench keeper: no legal eleven"
    assert _lost("DEF0", backed) is not None, "a starter of the benched position is covered"
    costs = [_lost(p, backed) for p in pos]
    assert len({tuple(c.values()) for c in costs if c is not None}) > 1, "covered rows differ per player"


def _played(squad: frozenset[str], pos: Mapping[str, str], cells: valuation.Cells) -> frozenset[str]:
    return frozenset().union(*(s.eleven for c in cells.values() if (s := eleven.best_eleven(squad, pos, c)) is not None))


def _selftest() -> None:
    for seed in range(40):
        for ties in (False, True):
            fielded, bench, pos, value = eleven.random_league(random.Random(seed), ties)
            squad = fielded | bench
            cells = _cells_of(random.Random(seed), value)
            ctx = _context(squad, pos, cells)
            played = _played(squad, pos, cells)
            for p in squad:
                got = _lost(p, ctx)
                if p not in played:
                    assert got is not None and not any(got.values()), "a player no jornada's eleven fields costs nothing"
                if got is None:
                    assert eleven.points(squad - {p}, pos, {}) is None
                    continue
                for n in valuation.READERS:
                    w = getattr(ctx.window, n)
                    lose = sum((eleven.best_total(squad, pos, cells[j]) or 0.0) - (eleven.best_total(squad - {p}, pos, cells[j]) or 0.0) for j in w)
                    assert abs(got[n] - lose) < 1e-9, "the loss is V(S) less V(S-p) in each jornada, summed"
                    assert got[n] >= -1e-9, "selling never gains points"
                assert got["lock"] <= got["next4"] + 1e-9 <= got["season"] + 2e-9, "a longer window loses at least as much"
            left = ctx.jornadas()
            doubled_ctx = _context(squad, pos, {j: {k: 2 * x for k, x in c.items()} for j, c in cells.items()})
            for rate in (0.02, 0.5) if seed < 8 else (hurdle.FALLBACK_LAMBDA,):
                rated = ctx._replace(rate=rate)
                for p in squad:
                    got = _lost(p, ctx)
                    for rise in (None, -0.1, 0.02)[:1 + 2 * (seed < 8)]:
                        s = Standing(p, value[p], 1e6 * (1 + seed), SALE_ESTIMATE * value[p], rise)
                        v = LIVE(s, rated)
                        assert v.sale == ctx.sale(p)
                        if got is None:
                            assert v == Verdict(None, ctx.sale(p), None), "no legal eleven without him: no reserve, no key"
                            continue
                        assert v.reserve is not None and v.key == v.reserve - (s.offer or 0.0), "the key is reserve less offer"
                        lost = got["season"]
                        assert v.reserve == hurdle.sale_price(lost, left, value[p], rise, rate), "a starter is sold iff his yield is under the bar"
                        for offer in (x for x in (0.5 * v.reserve, v.reserve, 2 * v.reserve) if x > 0):
                            own = (lost + hurdle.S * rate * left * hurdle.drift(rise) * value[p] / 1e6) / (left * offer / 1e6)
                            assert (v.reserve - offer <= 0) == (own <= rate / hurdle.MARGIN + 1e-9), "sell iff the cash out-yields him at the rate"
                        if seed < 8 and rise is None:
                            assert LIVE(s._replace(offer=None), rated).key is None, "no offer, no key"
                            doubled = LIVE(Standing(p, 2 * value[p], 2 * (s.offer or 0.0), 2 * s.later, rise), doubled_ctx._replace(rate=rate))
                            assert doubled.key is not None and abs(doubled.key - 2 * v.key) < 1e-3, "doubling prices and points doubles the key"
    assert set(CHALLENGERS) == {"rent"} and LIVE is rent

    def stub(standing: Standing, ctx: Context) -> Verdict:
        return Verdict(1.0, Reading(None, {}, ()), 2.0)
    rule: Rule = stub
    assert rule(Standing("x", 1.0, 1.0, 1.0), Context({}, Window(), 0.0)) == Verdict(1.0, Reading(None, {}, ()), 2.0)
    _selftest_option()
    _selftest_no_legal_eleven()
    print("worth self-test OK")


if __name__ == "__main__" and "--selftest" in sys.argv:
    _selftest()
