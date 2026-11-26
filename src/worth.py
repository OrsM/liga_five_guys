"""Sell rules: how a standing offer on one of your players is judged. A rule takes the offer (Standing) and the windows of
jornadas the board reads (Context) and gives a Verdict: the key the offers are sorted by (reserve less offer: 0 or less
sells), the Reading of selling him (its points None when the squad without him has no legal eleven), and the reserve
price (None when the rule states none). LIVE is the rule the board runs (rent)."""
from __future__ import annotations

import bisect
import itertools
import random
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from functools import lru_cache
from typing import NamedTuple

import eleven
import hurdle
from ffcore.rules import OFFER_EXISTS, SALE_ESTIMATE
import valuation
from valuation import Reading, Window

MAX_NIGHTS = 5
NO_LOSS = 1e-9
TAKE_RATIO = 1.0
TAKE_PERCENTILE = 0.5


class Standing(NamedTuple):
    """An offer on a player: the player, his hurdle.Exit and the offer (None: none); value and later (hurdle.later) are
    the exit's."""
    player: str
    exit: hurdle.Exit
    offer: float | None = None

    @property
    def value(self) -> float:
        return self.exit.value

    @property
    def later(self) -> float:
        return hurdle.later(self.exit)


Payoffs = tuple[float, ...] | Callable[[int], tuple[float, ...]]


class OfferDistribution(NamedTuple):
    """The offer model of a sale: the offers over value seen, ascending (offer_ratio_distribution), and `exists` the chance a player listed when
    a close comes has an offer on it (the ratios are those of the offers that exist). Every sale reader takes both from here."""
    ratios: tuple[float, ...]
    exists: float = OFFER_EXISTS


NO_OFFERS = OfferDistribution(())


class Context(NamedTuple):
    """What a rule reads: the positions, the Window a sale made now counts for, your cash, the offers seen and the
    rate (LAMBDA, hurdle.lam), and what the points are made of: your squad, each jornada's forecast per player and the locks,
    and the closes left before the next lock (a draw of the offers each)."""
    pos: Mapping[str, str]
    window: Window
    cash: float
    offers: OfferDistribution = NO_OFFERS
    rate: float = hurdle.FALLBACK_LAMBDA
    squad: frozenset[str] = frozenset()
    cells: valuation.Cells = {}
    locks: Mapping[int, str] = {}
    closes: int = 0

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


def offer_ratio_distribution(ratios: Iterable[float], exists: float = OFFER_EXISTS) -> OfferDistribution:
    """The distribution of offer over value ratios: the ratios (one per offer, at its first sighting, as
    current.offer_ratios gives them) sorted, with the chance `exists` an offer exists. Empty when there are none."""
    return OfferDistribution(tuple(sorted(ratios)), exists)


def offer_percentile(dist: OfferDistribution, ratio: float) -> float | None:
    """The share of the offers in dist with a ratio at or below ratio: the empirical distribution function, in [0, 1],
    nondecreasing in ratio, 0 below every offer and 1 at or above the highest. None when dist is empty."""
    if not dist.ratios:
        return None
    return bisect.bisect_right(dist.ratios, ratio) / len(dist.ratios)


@lru_cache(maxsize=4)
def _tails(ratios: tuple[float, ...]) -> tuple[float, ...]:
    """The sums of ratios[i:] for each i from 0 to len(ratios), the last 0: ratios sorted ascending."""
    return tuple(itertools.accumulate(reversed(ratios), initial=0.0))[::-1]


def waiting_values(m: int, payoffs: Payoffs, hold: float, exists: float = 1.0) -> tuple[float, ...]:
    """The value of waiting with k draws still to come, for k = 0 to m: rho_0 = hold and rho_k = rho_(k-1) + `exists` x (mean of
    max(u, rho_(k-1)) over the `payoffs` u of the offers of a distribution, less rho_(k-1)): with probability 1 - `exists` no offer is drawn.
    `payoffs` are ascending, one per offer, not empty, the same at each draw or, given as a function of k, those of the draw taken with k draws
    still to come (itself counted). An offer is taken at the close with k draws after it when its payoff is at least rho_k; with the payoffs the
    offers over value it is the reserve ratio (offer_reserve_ratio), with those of a cover (the points a sale saves) the cover's. Nondecreasing
    in k when the payoffs are the same at each draw and hold is at most the mean payoff, and in `exists` when hold is at most the lowest payoff
    (which makes the last close accept any offer)."""
    if m < 0 or not (payoffs(1) if callable(payoffs) else payoffs):
        raise ValueError("m must be >= 0 and payoffs non-empty")
    rho = hold
    out = [rho]
    for k in range(1, m + 1):
        u = payoffs(k) if callable(payoffs) else payoffs
        tail = _tails(u)
        i = bisect.bisect_left(u, rho)
        rho += exists * ((rho * i + tail[i]) / len(u) - rho)
        out.append(rho)
    return tuple(out)


def offer_price(ratio: float, value: float, drift: float, night: int) -> float:
    """The offer of `ratio` over the value of a player worth `value` tonight whose value changes by `drift` (a fraction) a night, `night` nights
    from now (hurdle.drifted): the one place an offer is priced."""
    return ratio * hurdle.drifted(value, drift, night)


def offers_at(ratios: Sequence[float], value: float, drift: float, night: int) -> tuple[float, ...]:
    """The offers (offer) of each of `ratios` (ascending) at that night: ascending."""
    return tuple(offer_price(r, value, drift, night) for r in ratios)


def offer_reserve_ratio(m: int, drift: float, dist: OfferDistribution, hold: float) -> float:
    """The offer over tonight's value at which selling tonight and waiting are worth the same, with m nights left to wait and one offer a
    night drawn from dist, i.i.d., expiring at the next close, existing with probability dist.exists, and over the value as it drifts (`drift` a
    night, offers_at: the offer k draws before the end is of the night m + 1 - k from now). hold is what the player is worth if no offer is
    taken (rho_0); rho_k is waiting_values over those offers with no other cost of waiting. m >= 0 and dist non-empty. Nondecreasing in m (with
    hold at most the lowest offer) and in the drift."""
    if not dist.ratios:
        raise ValueError("m must be >= 0 and dist non-empty")
    return waiting_values(m, lambda k: offers_at(dist.ratios, 1.0, drift, m + 1 - k), hold, dist.exists)[m]


def stopped(exit: hurdle.Exit, dist: OfferDistribution, m: int) -> hurdle.Exit:
    """The exit with its reserve set to the offer, in money, at which taking it now and waiting for a draw of dist at each of
    the m closes left are worth the same: value times offer_reserve_ratio at the exit's hurdle.drift and hold =
    later over value, never below the later price; the later price when dist is empty or the value is not positive."""
    later = hurdle.later(exit)
    if not dist.ratios or exit.value <= 0:
        return exit._replace(reserve=later)
    return exit._replace(reserve=max(later, exit.value * offer_reserve_ratio(m, hurdle.drift(exit), dist, later / exit.value)))


def waiting_reserve(standing: Standing, dist: OfferDistribution, m: int) -> float:
    """The reserve of stopped(standing.exit, dist, m): what the standing player is worth waiting for, in money."""
    return stopped(standing.exit, dist, m).reserve


def sale_reserve(lost: float, n: int, rate: float, exit: hurdle.Exit, dist: OfferDistribution, closes: int) -> float:
    """The least offer, in euros, at which selling a player of this exit is worth it when the sale loses `lost` points over n
    jornadas at rate with `closes` closes left before the lock (hurdle.reserve of his Stake). A sale that loses points (more than
    NO_LOSS) is taken at W + drift: the offer above which his yield, drift included, is under the rate's bar. One that loses
    none is taken at that or at what waiting for the best offer of the closes left nets (stopped), whichever is more; a player with a
    hold (Exit.hold, the rise the buy credited) is kept to it (hurdle.holding): never under the reserve of his capped sale rise and not under what holding him is expected to fetch
    (hurdle.held), as the buy priced him. The hold's drift is credited over the closes left on top of that terminal value: optimistic by up to closes / HOLD_NIGHTS of the hold."""
    if lost > NO_LOSS:
        return hurdle.reserve(hurdle.stake(lost, n, rate, exit)._replace(proceeds=0.0))
    return max(hurdle.reserve(hurdle.stake(lost, n, rate, stopped(kept, dist, closes))) for kept in (exit, hurdle.holding(exit)))


def rent(standing: Standing, ctx: Context) -> Verdict:
    """The one sale rule: money and points at one rate. The reserve is sale_reserve of the points the sale loses (over the season of
    ctx.window) over ctx.jornadas() at ctx.rate, with his exit stopped at the ctx.closes left. The key is reserve minus offer (0 or
    less sells); with no offer it is None. Where the sale has no Delta, key and reserve are None."""
    sale = ctx.sale(standing.player)
    if sale.points is None:
        return Verdict(None, sale, None)
    reserve = sale_reserve(0.0 - sale.points, ctx.jornadas(), ctx.rate, standing.exit, ctx.offers, ctx.closes)
    return Verdict(None if standing.offer is None else reserve - standing.offer, sale, reserve)


LIVE: Rule = rent
CHALLENGERS: Mapping[str, Rule] = {"rent": rent}


def take_now(ratio: float | None, share: float | None, funds: bool) -> bool:
    """Whether an offer a rule sells to is taken tonight rather than left listed for the next nights' (waiting is free:
    the nightly offers are drawn afresh): its ratio over value is TAKE_RATIO or more, its percentile among the offers seen
    TAKE_PERCENTILE or more, or it funds a buy that closes tonight."""
    return funds or (ratio is not None and ratio >= TAKE_RATIO) or (share is not None and share >= TAKE_PERCENTILE)


def _histogram(counts: Sequence[int], low: float, width: float) -> OfferDistribution:
    return offer_ratio_distribution((low + width * (i + 0.5) for i, n in enumerate(counts) for _ in range(n)), 1.0)


def _waiting_payoff(m: int, drift: float, dist: OfferDistribution, hold: float, shift: float, seed: int, runs: int) -> float:
    """The mean, over simulated waits of m nights, of what the player fetches (over tonight's value) when he takes the first offer of a night
    that reaches the waiting value of the nights left, moved by `shift`."""
    rng = random.Random(seed)
    rho = waiting_values(m, lambda k: offers_at(dist.ratios, 1.0, drift, m + 1 - k), hold, dist.exists)
    total = 0.0
    for _ in range(runs):
        payoff = hold
        for night in range(1, m + 1):
            x = offer_price(rng.choice(dist.ratios), 1.0, drift, night)
            if rng.random() < dist.exists and x >= rho[m - night] + shift:
                payoff = x
                break
        total += payoff
    return total / runs


def _selftest_option() -> None:
    study = _histogram((80, 80, 84, 82, 85, 72, 96, 71, 67, 33), 0.90, 0.021)
    flat = offer_ratio_distribution((0.9 + 0.2 * (i + 0.5) / 400 for i in range(400)), 1.0)
    mean = sum(study.ratios) / len(study.ratios)
    assert offer_ratio_distribution([1.1, 0.9, 1.0]).ratios == (0.9, 1.0, 1.1), "the distribution is sorted"
    assert offer_ratio_distribution([1.0]).exists == OFFER_EXISTS and study.exists == 1.0, "the fitted existence is the default"
    for d in (0.0, -0.0017, -0.007, 0.004):
        assert abs(offer_reserve_ratio(1, d, study, 0.0) - mean * (1 + d)) < 1e-12, "one night: the mean offer at tomorrow's value"
        rhos = [offer_reserve_ratio(m, d, study, 0.0) for m in range(0, 12)]
        assert d < 0 or all(a <= b + 1e-12 for a, b in zip(rhos, rhos[1:])), "more nights never lowers the reserve of a price that does not fall"
    for m in (1, 3, 5, 7):
        falls = [offer_reserve_ratio(m, d, study, 0.0) for d in (-0.02, -0.007, -0.0017, 0.0, 0.004)]
        assert all(a <= b for a, b in zip(falls, falls[1:])), "a faster fall never raises the reserve"
        likely = [offer_reserve_ratio(m, 0.0, study._replace(exists=e), 0.5) for e in (0.0, 0.5, 0.93, 1.0)]
        assert all(a <= b + 1e-12 for a, b in zip(likely, likely[1:])), "offers that are likelier to exist never lower the reserve"
        assert abs(likely[0] - 0.5) < 1e-12, "no offer ever drawn: the hold"
    wild = random.Random(2)
    for _ in range(50):
        dist = offer_ratio_distribution((wild.uniform(0.7, 1.4) for _ in range(wild.randint(1, 60))), 1.0)
        d, hold, m = wild.uniform(-0.03, 0.03), wild.uniform(0.0, 1.5), wild.randint(0, 8)
        rho = hold
        for night in range(m, 0, -1):
            rho = sum(max(offer_price(x, 1.0, d, night), rho) for x in dist.ratios) / len(dist.ratios)
        assert abs(offer_reserve_ratio(m, d, dist, hold) - rho) < 1e-12, "the reserve is the plain recursion over the offers at their night's value"
    for _ in range(30):
        dist = offer_ratio_distribution((wild.uniform(0.7, 1.4) for _ in range(wild.randint(1, 40))), 1.0)
        hold, m, may = wild.uniform(0.0, 1.5), wild.randint(0, 6), wild.uniform(0.0, 1.0)
        plain = waiting_values(m, dist.ratios, hold)
        assert waiting_values(m, dist.ratios, hold, 1.0) == plain and waiting_values(m, lambda k: dist.ratios, hold, 1.0) == plain, "exists 1 and payoffs given by step are the plain recursion"
        assert waiting_values(m, dist.ratios, hold, 0.0) == (hold,) * (m + 1), "no offer is ever drawn: nothing to wait for"
        floor = min(hold, dist.ratios[0])
        assert all(a <= b + 1e-12 for a, b in zip(waiting_values(m, dist.ratios, floor, may), waiting_values(m, dist.ratios, floor))), "an offer that may not come is worth no more than one that does"
        assert all(b >= a - 1e-12 for a, b in zip(waiting_values(m, dist.ratios, 0.0), waiting_values(m, lambda k: tuple(x * (1 + 0.01 * k) for x in dist.ratios), 0.0))), "dearer offers at later draws never lower the value of waiting"
        assert offer_reserve_ratio(m, 0.0, dist._replace(exists=may), hold) == waiting_values(m, dist.ratios, hold, may)[m], "no drift: the recursion at the existence of the model"
    hand = offer_ratio_distribution([1.0, 0.9, 1.1, 1.0, 0.95])
    assert offer_percentile(NO_OFFERS, 1.0) is None, "no offers seen: no percentile"
    assert [offer_percentile(hand, x) for x in (0.8, 0.9, 0.95, 1.0, 1.05, 1.1, 1.2)] == [0.0, 0.2, 0.4, 0.8, 0.8, 1.0, 1.0], "the empirical distribution function"
    grid = [0.85 + 0.01 * i for i in range(40)]
    for dist in (hand, study, flat):
        shares = [offer_percentile(dist, x) or 0.0 for x in grid]
        assert all(0.0 <= g <= 1.0 for g in shares), "a share"
        assert all(a <= b for a, b in zip(shares, shares[1:])), "nondecreasing in the ratio"
        assert (offer_percentile(dist, min(dist.ratios)) or 0.0) > 0.0 and offer_percentile(dist, max(dist.ratios)) == 1.0
    point = offer_ratio_distribution([1.0] * 7, 1.0)
    for m in (1, 2, 5):
        for d in (0.0, -0.0017, -0.01):
            assert abs(offer_reserve_ratio(m, d, point, 0.0) - (1.0 + d)) < 1e-12, "all ratios 1.0, all existing: tomorrow's value (waiting only for the first draw)"
    assert offer_reserve_ratio(3, -0.0017, study, 0.0) >= 1.03 and offer_reserve_ratio(5, -0.0017, study, 0.0) >= 1.04
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

    shelf = Standing("p", hurdle.Exit(1e7, 10, 9.5e6 / hurdle.draw(1e7) - 1))
    reserves = [waiting_reserve(shelf, study, m) for m in range(0, 8)]
    assert reserves[0] == shelf.later and all(a <= b + 1e-9 for a, b in zip(reserves, reserves[1:])), "more closes left never lowers the reserve"
    assert all(r >= shelf.later for r in reserves) and waiting_reserve(shelf, NO_OFFERS, 5) == shelf.later, "never below later; no offers seen: later"

    for m in (1, 3, 5):
        base = _waiting_payoff(m, -0.0017, study, 0.9, 0.0, 7, 20000)
        assert abs(base - offer_reserve_ratio(m, -0.0017, study, 0.9)) < 0.003, "the reserve is the optimal waiting value"
        for shift in (-0.02, -0.01, 0.01, 0.02):
            assert _waiting_payoff(m, -0.0017, study, 0.9, shift, 7, 20000) <= base + 0.001, "no other threshold waits better"

    value = 1e7
    held = _waiting_payoff(MAX_NIGHTS, 0.0, study, SALE_ESTIMATE, 0.0, 11, 20000)
    shape = {"POR": 1, "DEF": 4, "MED": 4, "DEL": 2}
    squad = {"%s%d" % (q, i): q for q, n in shape.items() for i in range(n)} | {"p": "DEF"}
    still = {1: {k: 1.0 for k in squad} | {"p": 0.0}}
    bench_ctx = Context(squad, Window((1,)), 0.0, study, squad=frozenset(squad), cells=still)
    stand = Standing("p", hurdle.Exit(value))
    reserve = waiting_reserve(stand, bench_ctx.offers, MAX_NIGHTS)
    assert abs(reserve / value - held) < 0.003, "the waiting reserve is the Monte Carlo waiting value"
    assert waiting_reserve(stand, NO_OFFERS, MAX_NIGHTS) == stand.later, "no offers seen: the later price"
    for drift in (-0.2, 0.0, 0.2):
        s = Standing("p", hurdle.Exit(value, 10, drift), 0.0)
        assert waiting_reserve(s, bench_ctx.offers, MAX_NIGHTS) >= s.later, "a waiting reserve is never below the later price"
    waits = bench_ctx._replace(closes=MAX_NIGHTS)
    for rise in (-0.03, 0.0, 0.03):
        e = stand.exit._replace(rise=rise)
        for bid in (0.9 * value, 1.0 * value, 1.2 * value):
            sold = rent(stand._replace(exit=e, offer=bid), waits)
            assert sold.reserve == waiting_reserve(stand._replace(exit=e), waits.offers, MAX_NIGHTS), "a bench player's reserve is his waiting reserve"
            assert sold.key is not None and (sold.key <= 0) == (bid >= waiting_reserve(stand._replace(exit=e), waits.offers, MAX_NIGHTS)), "sold at or above it"
        idle = rent(stand._replace(exit=e, offer=1.0), bench_ctx)
        assert idle.reserve == hurdle.later(e) and waiting_reserve(stand._replace(exit=e), waits.offers, 0) == hurdle.later(e), "no closes left: the later price"
        assert hurdle.reserve(hurdle.stake(0.0, 1, waits.rate, stopped(e, waits.offers, MAX_NIGHTS))) == waiting_reserve(stand._replace(exit=e), waits.offers, MAX_NIGHTS), "sale reserve and buy proceeds read one exit"
    assert Context({}, Window(), 0.0) == Context({}, Window(), 0.0, NO_OFFERS, hurdle.FALLBACK_LAMBDA, closes=0), "the new fields default"
    for ratio, share, funds in [(1.0, None, False), (0.9, 0.5, False), (0.9, 0.1, True), (None, None, True)]:
        assert take_now(ratio, share, funds), "a high ratio, a high percentile or a buy to fund: taken tonight"
    assert not take_now(0.99, 0.49, False) and not take_now(None, None, False), "else left listed"


def _selftest_hold() -> None:
    study = _histogram((80, 80, 84, 82, 85, 72, 96, 71, 67, 33), 0.90, 0.021)
    rng = random.Random(5)
    for _ in range(300):
        value, rise, closes = rng.uniform(1e5, 3e7), rng.uniform(-0.03, 0.03), rng.randint(0, 6)
        n, rate = rng.randint(1, 30), rng.uniform(0.01, 0.1)
        e = hurdle.Exit(value, rng.randint(1, 7), rise)
        assert sale_reserve(0.0, n, rate, e, study, closes) == hurdle.reserve(hurdle.stake(0.0, n, rate, stopped(e, study, closes))), "no hold: the capped sale, stopped at the closes left"
        holds = sorted(rng.uniform(0.001, 0.25) for _ in range(4))
        reserves = [sale_reserve(0.0, n, rate, e._replace(hold=h), study, closes) for h in holds]
        assert all(a <= b + 1e-6 for a, b in zip(reserves, reserves[1:])), "a bigger hold never lowers the reserve"
        for h, r in zip(holds, reserves):
            held = e._replace(hold=h)
            assert r >= sale_reserve(0.0, n, rate, e, study, closes) - 1e-6, "a hold never lowers the reserve below the capped sale's"
            assert r >= hurdle.held(held) - 1e-6, "a held player is not sold under what holding him fetches: the buy's price and the sale's agree"
            assert sale_reserve(0.0, n, rate, held, study, closes + 1) >= r - 1e-6, "more closes left never lowers it"
            assert sale_reserve(0.0, n, rate, held, NO_OFFERS, closes) == max(hurdle.held(held), h * value, sale_reserve(0.0, n, rate, e, NO_OFFERS, closes)), "no offers seen: held or the capped sale's"
    shape = {"POR": 1, "DEF": 4, "MED": 4, "DEL": 2}
    squad = {"%s%d" % (q, i): q for q, n in shape.items() for i in range(n)} | {"p": "DEF"}
    bench = Context(squad, Window((1,)), 0.0, study, squad=frozenset(squad), cells={1: {k: 1.0 for k in squad} | {"p": 0.0}}, closes=3)
    riser = hurdle.Exit(1e7, 3, hurdle.TREND_CAP, hold=0.15)
    for offer in (1.0e7, hurdle.held(riser) * 0.99):
        key = rent(Standing("p", riser, offer), bench).key
        assert key is not None and key > 0, "a bench riser is held under what his hold fetches"
    top = rent(Standing("p", riser, 0.0), bench).reserve
    over = rent(Standing("p", riser, None if top is None else top * 1.01), bench).key
    assert top is not None and top >= hurdle.held(riser) and rent(Standing("p", riser, top), bench).key == 0.0 and over is not None and over < 0, "and sold at his reserve or over it"
    pts = hurdle.Exit(1e7, 3, 0.02, hold=0.1)
    for lost in (0.5, 3.0):
        assert sale_reserve(lost, 10, 0.045, pts, study, 4) == sale_reserve(lost, 10, 0.045, pts._replace(hold=0.0), study, 0) == hurdle.reserve(
            hurdle.stake(lost, 10, 0.045, pts)._replace(proceeds=0.0)), "a sale that loses points reads no hold, no waiting"


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
        v = LIVE(Standing(p, hurdle.Exit(value[p]), 1e6), bare)
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
    seen_plays = 0
    for seed in range(40):
        for ties in (False, True):
            fielded, bench, pos, value = eleven.random_league(random.Random(seed), ties)
            squad = fielded | bench
            cells = _cells_of(random.Random(seed), value)
            ctx = _context(squad, pos, cells)
            played = _played(squad, pos, cells)

            @lru_cache(maxsize=None)
            def oracle(sq: frozenset[str], j: int) -> float | None:
                return eleven.best_total(sq, pos, cells[j])
            for p in squad:
                got = _lost(p, ctx)
                if p not in played:
                    assert got is not None and not any(got.values()), "a player no jornada's eleven fields costs nothing"
                if got is None:
                    assert eleven.points(squad - {p}, pos, {}) is None
                    continue
                for n in valuation.READERS:
                    w = getattr(ctx.window, n)
                    lose = sum((oracle(squad, j) or 0.0) - (oracle(squad - {p}, j) or 0.0) for j in w)
                    assert abs(got[n] - lose) < 1e-9, "the loss is V(S) less V(S-p) in each jornada, summed"
                    assert got[n] >= -1e-9, "selling never gains points"
                assert got["lock"] <= got["next4"] + 1e-9 <= got["season"] + 2e-9, "a longer window loses at least as much"
            left = ctx.jornadas()
            doubled_ctx = _context(squad, pos, {j: {k: 2 * x for k, x in c.items()} for j, c in cells.items()})
            for rate in (0.02, 0.5) if seed < 8 else (hurdle.FALLBACK_LAMBDA,):
                rated = ctx._replace(rate=rate)
                for p in squad:
                    got = _lost(p, ctx)
                    for rise in (0.0, -0.1, 0.02)[:1 + 2 * (seed < 8)]:
                        s = Standing(p, hurdle.Exit(value[p], 10, rise), 1e6 * (1 + seed))
                        v = LIVE(s, rated)
                        assert v.sale == ctx.sale(p)
                        if got is None:
                            assert v == Verdict(None, ctx.sale(p), None), "no legal eleven without him: no reserve, no key"
                            continue
                        assert v.reserve is not None and v.key == v.reserve - (s.offer or 0.0), "the key is reserve less offer"
                        lost = got["season"]
                        waits = stopped(s.exit, rated.offers, rated.closes)
                        plays = lost > NO_LOSS
                        seen_plays += plays
                        assert v.reserve == hurdle.reserve(hurdle.stake(lost, left, rate, waits)._replace(proceeds=0.0 if plays else hurdle.proceeds(waits))), \
                            "a sale that loses points is taken at W plus drift, one that loses none also over his waiting reserve"
                        bar = max(lost * 1e6 / (left * rate) + rise * value[p], 0.0 if plays else hurdle.proceeds(waits))
                        for offer in (x for x in (0.5 * v.reserve, v.reserve, 2 * v.reserve) if x > 0):
                            assert (v.reserve - offer <= 0) == (offer >= bar - 1e-9), "sell iff the offer is at the bar"
                        if seed < 8 and rise == 0.0:
                            assert LIVE(s._replace(offer=None), rated).key is None, "no offer, no key"
                            doubled = LIVE(Standing(p, hurdle.Exit(2 * value[p], 10, rise), 2 * (s.offer or 0.0)), doubled_ctx._replace(rate=rate))
                            assert doubled.key is not None and abs(doubled.key - 2 * v.key) < 1e-3, "doubling prices and points doubles the key"
    assert seen_plays > 50, seen_plays
    assert set(CHALLENGERS) == {"rent"} and LIVE is rent

    def stub(standing: Standing, ctx: Context) -> Verdict:
        return Verdict(1.0, Reading(None, {}, ()), 2.0)
    rule: Rule = stub
    assert rule(Standing("x", hurdle.Exit(1.0), 1.0), Context({}, Window(), 0.0)) == Verdict(1.0, Reading(None, {}, ()), 2.0)
    _selftest_option()
    _selftest_hold()
    _selftest_no_legal_eleven()
    print("worth self-test OK")


if __name__ == "__main__" and "--selftest" in sys.argv:
    _selftest()
