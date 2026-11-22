"""The one exchange rate between forecast points and money, and the tests that read it. A rate (LAMBDA, `lam`) is
points per million per jornada remaining. A move's P is the points above the next man up over its window of n jornadas
(Buyer.gain for a buy, the sale's lost points for a sale); its yield is P / (n x price in millions). A listing is bought
iff its yield reaches margin(price) x LAMBDA; a sale is taken iff the player's yield, his expected price change
counted at the rate, is under LAMBDA / MARGIN (sale_price); the walk-away price is S x P / (n x LAMBDA) and the bid
the win-weighted surplus under it at its best (bid)."""
from __future__ import annotations

import random
import statistics
import sys
from collections.abc import Iterable
from datetime import timedelta
from typing import NamedTuple

from ffcore.auction import Curve
from ffcore.loader import Opportunity
from ffcore.parse import snapshot_stamp, stamp

POOL_DAYS = 28
MIN_DAYS = 5
BAND = (8, 14)
SINCE = "2026-11-21T0517Z"
FALLBACK_LAMBDA = 0.045
S = 0.85
MARGIN = 1.15
CHEAP = 5e6
CHEAP_MARGIN = 1.5
WIN_FLOOR = 0.25
TREND_NIGHTS = 7
TREND_CAP = 0.03


def remaining(first: int, last: int) -> int:
    """The jornadas from first to last, both counted; 0 when first is after last."""
    return max(0, last - first + 1)


def since(now: str) -> str:
    """The first cut the rate at now reads: POOL_DAYS days before now, never before SINCE (the first cut whose
    opportunity gains are season V differences)."""
    at = snapshot_stamp(now)
    if at is None:
        raise ValueError("now %r is not a stamp" % now)
    return max(SINCE, stamp(at - timedelta(days=POOL_DAYS)))


def lam(history: Iterable[Opportunity], now: str, last: int) -> float:
    """LAMBDA at now: over the cuts from since(now) to before now, for each day (the date of the cut) the newest cut's
    rows with a per_million, one per player, ranked by gain (then player); those ranked BAND[0] to BAND[1] give
    per_million / remaining(from_j, last); the median of them all. FALLBACK_LAMBDA when they come from fewer than
    MIN_DAYS days. Positive whenever the median is."""
    first = since(now)
    newest: dict[str, str] = {}
    rows: dict[str, dict[str, Opportunity]] = {}
    for r in history:
        if first <= r.cut < now and r.per_million is not None and remaining(r.from_j, last) > 0:
            newest[r.cut[:10]] = max(newest.get(r.cut[:10], r.cut), r.cut)
            rows.setdefault(r.cut, {})[r.player] = r
    if len(newest) < MIN_DAYS:
        return FALLBACK_LAMBDA
    band = [r for cut in newest.values() for r in sorted(rows[cut].values(), key=lambda r: (-r.gain, r.player))[BAND[0] - 1:BAND[1]]]
    return statistics.median((r.per_million or 0.0) / remaining(r.from_j, last) for r in band)


def yield_of(points: float, n: int, price: float) -> float:
    """Points per million per jornada: points over n jornadas, price in euros. n and price positive."""
    return points / (n * price / 1e6)


def margin(price: float) -> float:
    """The factor of LAMBDA a buy's yield must reach: CHEAP_MARGIN under CHEAP euros, MARGIN otherwise."""
    return CHEAP_MARGIN if price < CHEAP else MARGIN


def buys(points: float, n: int, price: float, rate: float) -> bool:
    """Whether a listing at price whose P is points over n jornadas is bought at rate: its yield reaches margin(price)
    times rate."""
    return yield_of(points, n, price) >= margin(price) * rate


def walk_away(points: float, n: int, rate: float) -> float:
    """The most worth paying, in euros, for P points over n jornadas at rate: S x points / (n x rate) millions."""
    return S * points * 1e6 / (n * rate)


class Bid(NamedTuple):
    amount: float
    win: float


def bid(curve: Curve, asking: float, points: float, n: int, rate: float) -> Bid | None:
    """The bid on a listing at asking whose P is points over n jornadas, at rate: over the curve's points (ratio over
    asking, chance) with a ratio of 1 or more, the one with the most chance x (walk_away - asking x ratio), the lowest
    ratio on ties; None when the listing is not bought (buys), when that most is not positive or when its chance is
    under WIN_FLOOR. A bid is never above the walk-away price nor under the asking price."""
    if not buys(points, n, asking, rate):
        return None
    cap = walk_away(points, n, rate)
    best: tuple[float, float, float] | None = None
    for r, p in curve:
        if r < 1.0:
            continue
        score = p * (cap - asking * r)
        if best is None or score > best[0]:
            best = (score, r, p)
    if best is None or best[0] <= 0 or best[2] < WIN_FLOOR:
        return None
    return Bid(asking * best[1], best[2])


def drift(rise: float | None) -> float:
    """The expected price change over value TREND_NIGHTS nights ahead at most, held within +-TREND_CAP; 0 with none."""
    return 0.0 if rise is None else max(-TREND_CAP, min(TREND_CAP, rise))


def sale_price(points: float, n: int, value: float, rise: float | None, rate: float) -> float:
    """The offer, in euros, at and above which selling a player whose sale loses points over n jornadas beats keeping him at
    rate: his yield, the points plus S x rate x n x his expected price change (drift(rise) x value, in millions),
    over n x the offer, is under rate / MARGIN. Nondecreasing in points and rise, nonincreasing in rate."""
    return MARGIN * (points * 1e6 / (n * rate) + S * drift(rise) * value)


def _row(cut: str, player: str, gain: float, per_million: float | None, from_j: int = 13) -> Opportunity:
    return Opportunity(cut, player, "DEF", "rival", None, None, from_j, None, gain, per_million, None, None, None, None, None)


def _selftest_lam() -> None:
    rng = random.Random(4)
    last = 38
    days = ["2026-11-%02d" % d for d in range(21, 31)]
    history = [_row("%sT%02d00Z" % (d, h), "p%02d" % i, rng.uniform(0, 90), rng.choice([None, rng.uniform(0.1, 3.0)]), rng.choice([13, 14]))
               for d in days for h in (9, 21) for i in range(30)]
    now = "2026-12-01T0900Z"
    got = lam(history, now, last)
    newest = {d: "%sT2100Z" % d for d in days}
    band = [r for c in newest.values()
            for r in sorted((r for r in history if r.cut == c and r.per_million is not None), key=lambda r: (-r.gain, r.player))[7:14]]
    assert abs(got - statistics.median((r.per_million or 0.0) / (last - r.from_j + 1) for r in band)) < 1e-12, "the median of the band's yields"
    assert lam([], now, last) == FALLBACK_LAMBDA, "no history: the fallback"
    few = [r for r in history if r.cut[:10] in days[:MIN_DAYS - 1]]
    assert lam(few, now, last) == FALLBACK_LAMBDA, "fewer than MIN_DAYS days: the fallback"
    assert lam(history + [_row(now, "zz", 99.0, 50.0)], now, last) == got, "a cut at now or after is not read"
    old = [r._replace(cut="2026-11-%02dT1200Z" % d, per_million=99.0, gain=999.0) for d in (18, 19, 20) for r in history[:30]]
    assert lam(history + old, now, last) == got, "a cut before SINCE is not read"
    assert lam(history + history, now, last) == got, "a run repeated at a cut reads the same"
    scaled = [r._replace(gain=2 * r.gain) for r in history]
    assert lam(scaled, now, last) == got, "doubling every price and every gain (per_million unchanged) leaves the rate"
    assert since("2027-02-01T0000Z") == "2027-01-04T0000Z" and since("2026-11-22T0906Z") == SINCE


def _selftest_buy() -> None:
    rng = random.Random(9)
    ratios = (1.0, 1.02, 1.05, 1.1, 1.2, 1.3, 1.5)
    seen_bid = seen_none = 0
    for _ in range(3000):
        curve = tuple((r, min(1.0, rng.choice([0.0, 0.1, 0.3]) + (r - 1.0) * rng.choice([0.5, 2.0, 5.0]))) for r in (0.9, *ratios))
        asking = rng.choice([0.6e6, 1.5e6, 8e6, 40e6])
        n = rng.choice([1, 5, 25, 26])
        rate = rng.choice([0.02, 0.045, 0.1])
        points = rng.uniform(0, 3) * n * rate * asking / 1e6
        got = bid(curve, asking, points, n, rate)
        cap = walk_away(points, n, rate)
        if not buys(points, n, asking, rate):
            assert got is None, "no buy under the margin"
            assert yield_of(points, n, asking) < margin(asking) * rate
        if got is None:
            seen_none += 1
        else:
            seen_bid += 1
            assert asking - 1e-6 <= got.amount <= cap + 1e-6, "between the asking and the walk-away price"
            assert got.win >= WIN_FLOOR and got.amount / asking in ratios
            mine = got.win * (cap - got.amount)
            assert all(mine >= p * (cap - asking * r) - 1e-6 for r, p in curve if r >= 1.0), "the best surplus of the grid"
            more = bid(curve, asking, points * 1.5, n, rate)
            assert more is not None and more.amount >= got.amount - 1e-6, "more points never lower the bid"
            twice = bid(curve, 2 * asking, 2 * points, n, rate)
            assert twice is not None and abs(twice.amount - 2 * got.amount) < 1e-3 and twice.win == got.win, "scale-invariant"
        assert buys(2 * points, n, 2 * asking, rate) == buys(points, n, asking, rate)
        assert abs(walk_away(2 * points, n, rate) - 2 * cap) < 1e-3, "the walk-away price scales with the points"
        assert walk_away(points + 1, n, rate) > cap and walk_away(points + 1, n, 2 * rate) < walk_away(points + 1, n, rate)
    assert seen_bid > 100 and seen_none > 100, (seen_bid, seen_none)
    flat = ((1.0, 1.0), (1.1, 1.0))
    one = bid(flat, 1e7, 10.0, 25, 0.02)
    assert one == Bid(1e7, 1.0), "a sure win at the asking price bids the asking price"
    assert bid(((1.0, 0.1), (1.1, 0.2)), 1e7, 10.0, 25, 0.02) is None, "a chance under WIN_FLOOR: no bid"
    assert walk_away(25 * 0.02 * 10, 25, 0.02) == S * 10e6, "P worth exactly the rate on 10M: S x 10M"
    assert margin(4.9e6) == CHEAP_MARGIN and margin(5e6) == MARGIN


def _selftest_sale() -> None:
    rng = random.Random(5)
    for _ in range(2000):
        n, rate, value = rng.choice([1, 13, 26]), rng.choice([0.02, 0.045]), rng.choice([1e6, 2e7])
        points, rise = rng.uniform(0, 50), rng.choice([None, -0.2, -0.01, 0.0, 0.01, 0.2])
        at = sale_price(points, n, value, rise, rate)
        for offer in (0.5 * at, 0.99 * at, 1.01 * at, 2 * at):
            if offer <= 0:
                continue
            own = (points + S * rate * n * drift(rise) * value / 1e6) / (n * offer / 1e6)
            assert (offer >= at) == (own <= rate / MARGIN + 1e-12), "sell iff his yield is under the rate's bar"
        assert sale_price(points + 1, n, value, rise, rate) > at and sale_price(points + 1, n, value, rise, 2 * rate) < sale_price(points + 1, n, value, rise, rate)
        assert abs(sale_price(2 * points, n, 2 * value, rise, rate) - 2 * at) < 1e-3, "scale-invariant"
        assert abs(drift(rise)) <= TREND_CAP
    assert sale_price(0.0, 5, 1e6, None, 0.045) == 0.0, "no points, no trend: any offer"


def _selftest() -> None:
    _selftest_lam()
    _selftest_buy()
    _selftest_sale()
    print("hurdle self-test OK")


if __name__ == "__main__" and "--selftest" in sys.argv:
    _selftest()
