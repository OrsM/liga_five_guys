"""Sell rules: how a standing offer on one of your players is judged. A rule takes the offer (Standing) and the windows of
jornadas the board reads (Context) and gives a Verdict: the key the offers are sorted by, the points your eleven loses
over each window (None when the eleven has no legal replacement for him), and the reserve price (None when the rule states
none). LIVE is the rule the board runs."""
from __future__ import annotations

import bisect
import datetime as dt
import random
import statistics
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import NamedTuple

import eleven
from ffcore.rules import SALE_ESTIMATE
import valuation
from valuation import READERS, Window

STOPGAP_RATE = 0.6
MAX_NIGHTS = 5
FORECAST_NIGHTS = 10
RATE_DAYS = 7
NO_LOSS = 1e-9


class Standing(NamedTuple):
    player: str
    value: float
    offer: float | None
    later: float


class OfferDistribution(NamedTuple):
    ratios: tuple[float, ...]


NO_OFFERS = OfferDistribution(())


class Context(NamedTuple):
    """What a rule reads: the positions, the window of each reader (valuation.READERS), your cash, the offers seen and the
    rate, and what the points are made of: your squad, each jornada's forecast per player and the locks."""
    pos: Mapping[str, str]
    horizons: Mapping[str, Window]
    cash: float
    offers: OfferDistribution = NO_OFFERS
    rate: float = STOPGAP_RATE
    squad: frozenset[str] = frozenset()
    cells: valuation.Cells = {}
    locks: Mapping[int, str] = {}


class Verdict(NamedTuple):
    key: float | None
    lost: Mapping[str, float] | None
    reserve: float | None


Rule = Callable[[Standing, Context], Verdict]


def lost_points(player: str, ctx: Context) -> Mapping[str, float] | None:
    """The points your squad loses by selling player now, for each window of ctx.horizons: valuation.delta of the sale,
    the eleven picked afresh in each jornada, negated (0 for a player no jornada's eleven needs). Zero where the squad
    fields no eleven. None when the squad without him fields none: his sale has no points loss to quote."""
    if eleven.points(ctx.squad, ctx.pos, {}) is None:
        return {n: 0.0 for n in ctx.horizons}
    sale = valuation.Move(frozenset({player}), frozenset(), None, None)
    out: dict[str, float] = {}
    for n, window in ctx.horizons.items():
        got = valuation.delta(ctx.squad, sale, window, ctx.locks, ctx.pos, ctx.cells).points
        if got is None:
            return None
        out[n] = 0.0 - got
    return out


def points_per_million(standing: Standing, ctx: Context) -> Verdict:
    """Ranks an offer by the points lost over the season per million raised: None when the offer is None or 0 or
    when lost_points is None. States no reserve price."""
    lost = lost_points(standing.player, ctx)
    key = None if lost is None or not standing.offer else lost["season"] / (standing.offer / 1e6)
    return Verdict(key, lost, None)


LIVE: Rule = points_per_million


def reserve_price(lost_season: float, rate: float, later: float) -> float:
    """The offer, in euros, at which selling and keeping are worth the same: later plus the points lost, converted at rate
    points per million. rate must be positive. Never below later when lost_season is not negative."""
    return later + lost_season * 1e6 / rate


def worth_rule(rate: float, *, trend: bool) -> Rule:
    """A sell rule at rate points per million. The key is reserve minus offer (negative means sell); the reserve uses the
    expected later sale price, which is standing.later when trend is set and SALE_ESTIMATE times the value otherwise.
    With no offer the key is None and the reserve is still stated; where lost_points is None both are None."""
    if not rate > 0:
        raise ValueError("rate must be > 0")

    def rule(standing: Standing, ctx: Context) -> Verdict:
        lost = lost_points(standing.player, ctx)
        if lost is None:
            return Verdict(None, None, None)
        later = standing.later if trend else SALE_ESTIMATE * standing.value
        reserve = reserve_price(lost["season"], rate, later)
        key = None if standing.offer is None else reserve - standing.offer
        return Verdict(key, lost, reserve)

    return rule


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


class Buy(NamedTuple):
    gain: float
    bid: float


class CutBuys(NamedTuple):
    at: dt.datetime
    buys: Sequence[Buy]


def best_yield(buys: Iterable[Buy]) -> float | None:
    """The highest gain points per million bid over the buys with a positive gain and a positive bid (gain in points,
    bid in euros); None when there is none."""
    ys = [b.gain / (b.bid / 1e6) for b in buys if b.gain > 0 and b.bid > 0]
    return max(ys) if ys else None


def r_estimate(cuts: Iterable[CutBuys], now: dt.datetime, carry: float = STOPGAP_RATE) -> float:
    """The exchange rate in points per million at now: the median of best_yield over the cuts in the RATE_DAYS days up to
    now that have a buy; carry when none has. carry must be positive; the result is positive."""
    ys = [y for c in cuts if now - dt.timedelta(days=RATE_DAYS) < c.at <= now if (y := best_yield(c.buys)) is not None]
    return statistics.median(ys) if ys else carry


def drift_cost(standing: Standing) -> float:
    """The cost of a night of waiting, as a fraction of value: the price forecast's rise (later over SALE_ESTIMATE times
    value, less 1) spread over FORECAST_NIGHTS and negated, so a falling price costs and a rising one pays. 0 for a value
    that is not positive."""
    if standing.value <= 0:
        return 0.0
    return (1 - standing.later / (SALE_ESTIMATE * standing.value)) / FORECAST_NIGHTS


def bench_option(standing: Standing, ctx: Context) -> Verdict:
    """The reserve of the offers' option value. A player the eleven loses points without in any window (the most over
    ctx.horizons) gets reserve_price at ctx.rate of that most; no verdict of the board reads it. A bench player (loses
    nothing in any window, so no jornada needs him: m = MAX_NIGHTS nights of free waiting) gets value times
    offer_reserve_ratio over ctx.offers with the drift_cost of the standing and hold = later over value, never below later;
    with no ctx.offers, later. The key is reserve minus offer (negative means sell); with no offer it is None. Where
    lost_points is None key and reserve are None."""
    lost = lost_points(standing.player, ctx)
    if lost is None:
        return Verdict(None, None, None)
    most = max(lost.values(), default=0.0)
    if most > NO_LOSS:
        reserve = reserve_price(most, ctx.rate, standing.later)
    elif not ctx.offers.ratios or standing.value <= 0:
        reserve = standing.later
    else:
        ratio = offer_reserve_ratio(MAX_NIGHTS, drift_cost(standing), ctx.offers, standing.later / standing.value)
        reserve = max(standing.later, standing.value * ratio)
    key = None if standing.offer is None else reserve - standing.offer
    return Verdict(key, lost, reserve)


CHALLENGERS: Mapping[str, Rule] = {
    "r06": worth_rule(STOPGAP_RATE, trend=False),
    "r06_trend": worth_rule(STOPGAP_RATE, trend=True),
    "bench_option": bench_option,
}


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

    for m in (1, 3, 5):
        base = _waiting_payoff(m, 0.0017, study, 0.9, 0.0, 7, 20000)
        assert abs(base - offer_reserve_ratio(m, 0.0017, study, 0.9)) < 0.003, "the reserve is the optimal waiting value"
        for shift in (-0.02, -0.01, 0.01, 0.02):
            assert _waiting_payoff(m, 0.0017, study, 0.9, shift, 7, 20000) <= base + 0.001, "no other threshold waits better"

    value = 1e7
    now = dt.datetime(2026, 11, 18, tzinfo=dt.timezone.utc)
    held = _waiting_payoff(MAX_NIGHTS, 0.0, study, SALE_ESTIMATE, 0.0, 11, 20000)
    bench_ctx = Context({}, {}, 0.0, study)
    stand = Standing("p", value, None, SALE_ESTIMATE * value)
    reserve = bench_option(stand, bench_ctx).reserve
    assert reserve is not None and abs(reserve / value - held) < 0.003, "the rule's reserve is the Monte Carlo waiting value"
    for gap in (0.004, -0.004):
        got = bench_option(stand._replace(offer=value * (held + gap)), bench_ctx)
        assert got.key is not None and (got.key <= 0) == (gap > 0), "the verdict is the Monte Carlo verdict"
    assert bench_option(stand, bench_ctx._replace(offers=NO_OFFERS)).reserve == stand.later, "no offers seen: the later price"
    for drift in (-0.2, 0.0, 0.2):
        s = Standing("p", value, 0.0, SALE_ESTIMATE * value * (1 + drift))
        assert (bench_option(s, bench_ctx).reserve or 0.0) >= s.later, "a bench reserve is never below the later price"
    assert Context({}, {}, 0.0) == Context({}, {}, 0.0, NO_OFFERS, STOPGAP_RATE), "the new fields default"

    cuts = [CutBuys(now - dt.timedelta(days=d), [Buy(g, b) for g, b in buys]) for d, buys in
            ((1, [(10.0, 10e6), (6.0, 5e6)]), (2, []), (3, [(4.0, 8e6)]), (4, [(0.0, 1e6), (-1.0, 1e6)]), (6, [(9.0, 3e6)]), (9, [(90.0, 1e6)]))]
    assert best_yield([Buy(10.0, 10e6), Buy(6.0, 5e6)]) == 1.2 and best_yield([Buy(0.0, 1e6), Buy(3.0, 0.0)]) is None
    assert r_estimate(cuts, now) == statistics.median([1.2, 0.5, 3.0]), "the median of the 7 days' best yields"
    assert r_estimate([], now, 0.4) == 0.4 and r_estimate(cuts[-1:], now) == STOPGAP_RATE, "no buy: carry the last rate"
    assert r_estimate(cuts, now) > 0


def _cells_of(rng: random.Random, value: Mapping[str, float]) -> dict[int, dict[str, float]]:
    return {1: dict(value), 2: {k: v * rng.choice([0.0, 0.5, 1.0, 2.0]) for k, v in value.items()},
            3: {k: rng.choice([0.0, 1.0, 3.0, 5.0]) for k in value}}


def _context(squad: Iterable[str], pos: Mapping[str, str], cells: valuation.Cells) -> Context:
    season = Window(sorted(cells))
    return Context(pos, {n: getattr(season, n) for n in READERS}, 0.0, squad=frozenset(squad), cells=cells, locks={1: "2026-12-01T20:00Z"})


def _selftest_no_legal_eleven() -> None:
    shape = {"POR": 1, "DEF": 4, "MED": 4, "DEL": 2}
    pos = {"%s%d" % (p, i): p for p, n in shape.items() for i in range(n)}
    value = {k: 1.0 + i for i, k in enumerate(sorted(pos))}
    rng = random.Random(3)
    cells = _cells_of(rng, value)

    bare = _context(pos, pos, cells)
    for p in pos:
        v = LIVE(Standing(p, value[p], 1e6, value[p]), bare)
        assert lost_points(p, bare) is None and v == Verdict(None, None, None), "eleven players only: nobody has a quotable loss"
        assert worth_rule(1.0, trend=False)(Standing(p, value[p], 1e6, value[p]), bare) == Verdict(None, None, None)
        assert bench_option(Standing(p, value[p], 1e6, value[p]), bare) == Verdict(None, None, None)
    extra = "DEF9"
    pos[extra], value[extra] = "DEF", 0.5
    cells = {j: c | {extra: 0.5} for j, c in cells.items()}
    backed = _context(pos, pos, cells)
    whole = sum(eleven.total(frozenset(k for k in pos if k != extra), c) for c in cells.values())
    for p in pos:
        got = lost_points(p, backed)
        if eleven.points(backed.squad - {p}, pos, {}) is None:
            assert got is None, "no replacement fills the slot: no legal eleven"
        else:
            assert got is not None and all(0 <= x < whole for x in got.values()), "covered: costed as the swap, never the whole eleven"
    assert lost_points("POR0", backed) is None, "the only keeper with no bench keeper: no legal eleven"
    assert lost_points("DEF0", backed) is not None, "a starter of the benched position is covered"
    costs = [lost_points(p, backed) for p in pos]
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
                got = lost_points(p, ctx)
                if p not in played:
                    assert got is not None and not any(got.values()), "a player no jornada's eleven fields costs nothing"
                if got is None:
                    assert eleven.points(squad - {p}, pos, {}) is None
                    continue
                for n, w in ctx.horizons.items():
                    lose = sum((eleven.best_total(squad, pos, cells[j]) or 0.0) - (eleven.best_total(squad - {p}, pos, cells[j]) or 0.0) for j in w)
                    assert abs(got[n] - lose) < 1e-9, "the loss is V(S) less V(S-p) in each jornada, summed"
                    assert got[n] >= -1e-9, "selling never gains points"
                assert got["lock"] <= got["next4"] + 1e-9 <= got["season"] + 2e-9, "a longer window loses at least as much"
            for p in squad:
                offer = 1e6 * (1 + seed)
                s = Standing(p, value[p], offer, value[p])
                v = LIVE(s, ctx)
                assert v.lost == lost_points(p, ctx)
                assert v.reserve is None, "the baseline states no reserve"
                if v.lost is None:
                    assert v.key is None, "no legal eleven without him: no key"
                    continue
                per_million = v.lost["season"] / (offer / 1e6)
                assert v.key == per_million, "key is points lost per million"
                half = LIVE(s._replace(offer=2 * offer), ctx).key
                assert half is not None and abs(half - per_million / 2) < 1e-9, "key halves when the offer doubles"
                assert LIVE(s._replace(offer=None), ctx).key is None and LIVE(s._replace(offer=0.0), ctx).key is None

            for rate in (0.3, STOPGAP_RATE, 2.0):
                plain = worth_rule(rate, trend=False)
                trended = worth_rule(rate, trend=True)
                for p in squad:
                    a = SALE_ESTIMATE * value[p]
                    got = lost_points(p, ctx)
                    if got is None:
                        assert plain(Standing(p, value[p], 1e6, a), ctx) == Verdict(None, None, None), "no legal eleven: no reserve, no key"
                        continue
                    lost = got["season"]
                    for offer in (1e6 * (1 + seed), 3e6 * (1 + seed)):
                        s = Standing(p, value[p], offer, a)
                        v = plain(s, ctx)
                        assert v.reserve is not None and v.reserve >= a - 1e-9, "reserve is at least the later sale price"
                        assert abs(v.reserve - reserve_price(lost, rate, a)) < 1e-6, "reserve is reserve_price"
                        assert (offer >= v.reserve) == (v.key is not None and v.key <= 0), "sell iff offer reaches reserve"
                        assert trended(s._replace(later=a), ctx) == v, "trend equals plain when later is the plain estimate"
                    assert plain(Standing(p, value[p], None, a), ctx).key is None, "no offer, no key"
                    if p not in played:
                        assert plain(s, ctx).reserve == a, "a player no eleven fields has the later sale price as reserve"
                    assert reserve_price(lost, rate, a) <= reserve_price(lost + 1.0, rate, a), "reserve rises with points lost"
                    assert reserve_price(lost, rate, a) >= reserve_price(lost, 2 * rate, a) - 1e-9, "reserve falls as rate rises"

    for bad in (0.0, -1.0):
        try:
            worth_rule(bad, trend=False)
        except ValueError:
            pass
        else:
            raise AssertionError("a rate that is not positive is refused")
    assert set(CHALLENGERS) == {"r06", "r06_trend", "bench_option"}
    assert LIVE is points_per_million

    def stub(standing: Standing, ctx: Context) -> Verdict:
        return Verdict(1.0, {}, 2.0)
    rule: Rule = stub
    assert rule(Standing("x", 1.0, 1.0, 1.0), Context({}, {}, 0.0)) == Verdict(1.0, {}, 2.0)
    _selftest_option()
    _selftest_no_legal_eleven()
    print("worth self-test OK")


if __name__ == "__main__" and "--selftest" in sys.argv:
    _selftest()
