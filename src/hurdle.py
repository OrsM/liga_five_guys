"""The one exchange rate between forecast points and money, and the tests that read it. A rate (LAMBDA, `lam`) is
points per million per jornada remaining. A move's P is the points above the next man up over its window of n jornadas
(Buyer.gain for a buy, the sale's lost points for a sale); W = P / (n x LAMBDA) is its worth in millions. One band K
around W, and one Exit, what the player fetches when sold: the Stake of a move has two edges, the most worth paying
(cap) and the least worth taking (reserve), each the larger of what his points are worth (W, inside the band) and what
his Exit proceeds (a flip: bought to be sold, the capital tied across the locks the hold crosses charged). A buy bids
the win-weighted surplus under its cap at its best (bid); a bid already placed is held, lowered, withdrawn or raised by
the same surplus (stand)."""
from __future__ import annotations

import random
import statistics
import sys
from collections.abc import Iterable
from datetime import timedelta
from typing import NamedTuple

from ffcore.auction import Curve, chance
from ffcore.loader import Opportunity
from ffcore.parse import snapshot_stamp, stamp
from ffcore.rules import SALE_ESTIMATE

POOL_DAYS = 28
MIN_DAYS = 5
BAND = (8, 14)
SINCE = "2026-11-21T0517Z"
FALLBACK_LAMBDA = 0.045
K = 0.85
WIN_FLOOR = 0.25
TREND_CAP = 0.03
HOLD_NIGHTS = 14
HOLD_BOUND = 0.5
HOLD_EDGE = 0.5
HOLD_VALUE = 30e6


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


class Bid(NamedTuple):
    amount: float
    win: float


def best_on_grid(curve: Curve, asking: float, cap: float) -> tuple[float, float, float] | None:
    """The (surplus, ratio, chance) of the curve's point with a ratio of 1 or more that has the most chance x
    (cap - asking x ratio), the lowest ratio on ties; None when the curve has no such point."""
    best: tuple[float, float, float] | None = None
    for r, p in curve:
        if r < 1.0:
            continue
        score = p * (cap - asking * r)
        if best is None or score > best[0]:
            best = (score, r, p)
    return best


def bid(curve: Curve, asking: float, stake: Stake) -> Bid | None:
    """The bid on a listing at asking whose Stake is stake: the best_on_grid point under cap(stake); None when its surplus
    is not positive (a listing whose cap is under its asking price, as one of a player with no points and no proceeds
    above it) or when its chance is under WIN_FLOOR. A bid is never above the cap nor under the asking price."""
    best = best_on_grid(curve, asking, cap(stake))
    if best is None or best[0] <= 0 or best[2] < WIN_FLOOR:
        return None
    return Bid(asking * best[1], best[2])


class Stand(NamedTuple):
    """What to do with a bid placed: move (hold, lower, withdraw or raise), to (the amount to move it to: the
    best_on_grid amount for lower and raise, None otherwise), surplus (chance x (walk-away - placed) at the placed
    amount) and to_surplus and to_win (at `to`; None without one)."""
    move: str
    to: float | None
    surplus: float
    to_surplus: float | None
    to_win: float | None


def stand(curve: Curve, asking: float, placed: float, cap: float) -> Stand:
    """The move for a bid placed at `placed` on a listing at asking with walk-away price cap. withdraw iff no point of
    the grid has a positive surplus. Otherwise lower (to the best_on_grid amount) when placed is above cap, and raise
    (to it) when the placed surplus is not positive and the best amount is above placed, or when that amount is above
    the grid point at or over placed and has a surplus strictly above the placed one; else hold: a placed bid at or
    under cap with a positive surplus is never lowered. The surplus is read at the placed amount's own chance."""
    here = chance(curve, placed / asking) * (cap - placed)
    best = best_on_grid(curve, asking, cap)
    if best is None or best[0] <= 0:
        return Stand("withdraw", None, here, None, None)
    top, ratio, win = best
    to = asking * ratio
    over = [r for r, _ in curve if r >= 1.0 and asking * r >= placed]
    if placed > cap or (here <= 0 and to <= placed):
        return Stand("lower", to, here, top, win)
    if here <= 0 or (top > here and over and to > asking * min(over)):
        return Stand("raise", to, here, top, win)
    return Stand("hold", None, here, None, None)


class Exit(NamedTuple):
    """What a player fetches when sold later, read from the price forecast: his value, the horizon in nights of its
    furthest row (1 with none), the rise at that horizon, a fraction of value (0 with none), the reserve, in euros: the
    value of waiting for the best offer of the closes left (worth.stopped; 0 until it is set), and the hold, the rise
    credited to holding him (0 when none is: see held_rise). One horizon and one rise for a sale: a night's rise is rise /
    nights; a hold has its own, over HOLD_NIGHTS."""
    value: float
    nights: int = 1
    rise: float = 0.0
    reserve: float = 0.0
    hold: float = 0.0


def held_rise(value: float, fitted: float) -> float:
    """The rise credited to holding a player of this value whose price fold fits `fitted` (a fraction) over HOLD_NIGHTS: HOLD_EDGE
    of the fitted rise within 0 and HOLD_BOUND (the fit is calibrated up to that), 0 above HOLD_VALUE (nothing measured there).
    Nondecreasing in the fitted rise, never negative, never above HOLD_EDGE x HOLD_BOUND."""
    return 0.0 if value > HOLD_VALUE else HOLD_EDGE * max(0.0, min(HOLD_BOUND, fitted))


def draw(value: float) -> float:
    """The expected offer, in euros, drawn for a player of this value on a night: SALE_ESTIMATE times value."""
    return SALE_ESTIMATE * value


def later(exit: Exit) -> float:
    """The expected sale price of the exit after its horizon: draw(value) x (1 + rise)."""
    return draw(exit.value) * (1 + exit.rise)


def held(exit: Exit) -> float:
    """What holding the exit's player is expected to fetch, in euros: draw(value) x (1 + hold). Nondecreasing in the hold."""
    return draw(exit.value) * (1 + exit.hold)


def in_points(money: float, n: int, rate: float) -> float:
    """The points a sum of money is worth over n jornadas at rate: money x n x rate in millions (W = P / (n x rate) read the
    other way). Linear in money, zero at zero."""
    return money / 1e6 * n * rate


def wait_cost(exit: Exit) -> float:
    """The cost of a night of waiting, as a fraction of value: the exit's rise spread over its nights and negated, so a
    falling price costs and a rising one pays. 0 for a value that is not positive."""
    return 0.0 if exit.value <= 0 else -exit.rise / exit.nights


def proceeds(exit: Exit) -> float:
    """What selling the exit is expected to net, in euros: the later price, or the reserve when that is more. Nondecreasing
    in the rise and in the reserve."""
    return max(later(exit), exit.reserve)


class Stake(NamedTuple):
    """A player's stake in a move: its points over n jornadas (1 or more) at rate (W = points x 1e6 / (n x rate) euros is
    what they are worth), the proceeds of his exit in euros, the drift (his exit's rise x value, euros) and k the jornadas
    the capital of a flip is tied for (a hold across a lock; at most n)."""
    points: float
    rate: float
    n: int
    proceeds: float
    drift: float
    k: int


def stake(points: float, n: int, rate: float, exit: Exit, k: int = 0) -> Stake:
    """The Stake of a move of P points over n jornadas at rate, of a player with this exit, held across k jornadas."""
    return Stake(points, rate, n, proceeds(exit), exit.rise * exit.value, k)


def hold_stake(points: float, n: int, rate: float, exit: Exit) -> Stake:
    """The Stake of a buy to hold that cash funds, whose player fetches held(exit) after the hold: as stake with that for the proceeds, the
    drift 0 (a buy reads no sale reserve) and the capital free (k = 0: cash that is left at the lock is worth nothing else). Its cap is
    never under the cap of stake(points, n, rate, exit, n), the points alone."""
    return Stake(points, rate, n, held(exit), 0.0, 0)


def profit(stake: Stake, amount: float) -> float:
    """The points a hold earns beyond his own: in_points of the proceeds less the share k / n of them that tied the capital up, less
    the bid of amount, less the stake's own points, never negative. 0 for a stake whose capital is tied the whole window (k = n:
    the points alone); nonincreasing in amount and in k; 0 at any amount at or over cap(stake)."""
    if stake.k >= stake.n:
        return 0.0
    return max(0.0, in_points(max(0.0, (1 - stake.k / stake.n) * stake.proceeds - amount), stake.n, stake.rate) - stake.points)


def cap(stake: Stake) -> float:
    """The most worth paying, in euros: the larger of K x W and the proceeds less the share k / n of them that tied the
    capital up. Nonnegative with no negative points; nondecreasing in points and in the exit's rise, nonincreasing in the
    rate and in k, scale-invariant, and at most reserve(stake). With k = n it is K x W: a buy for his points alone."""
    return max(K * stake.points * 1e6 / (stake.n * stake.rate), (1 - stake.k / stake.n) * stake.proceeds)


def reserve(stake: Stake) -> float:
    """The least worth taking, in euros: the larger of (W + drift) / K, what his points are worth and the price change
    forgone by selling now inside the band, and the proceeds. Nondecreasing in points and in the exit's rise, nonincreasing
    in the rate, scale-invariant, and at least cap(stake)."""
    return max((stake.points * 1e6 / (stake.n * stake.rate) + stake.drift) / K, stake.proceeds)


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
        e = Exit(asking, rng.choice([1, 7]), rng.choice([0.0, 0.01, 0.03]), rng.choice([0.0, 1.03 * asking]))
        k = rng.choice([0, 1, n])
        s = stake(points, n, rate, e, k)
        got = bid(curve, asking, s)
        top = cap(s)
        if got is None:
            seen_none += 1
        else:
            seen_bid += 1
            assert asking - 1e-6 <= got.amount <= top + 1e-6, "between the asking and the cap"
            assert got.win >= WIN_FLOOR and got.amount / asking in ratios
            mine = got.win * (top - got.amount)
            assert all(mine >= p * (top - asking * r) - 1e-6 for r, p in curve if r >= 1.0), "the best surplus of the grid"
            dearer = bid(curve, asking, stake(points, n, 2 * rate, e, k))
            assert dearer is None or dearer.amount <= got.amount + 1e-6, "a dearer rate never raises the bid"
            more = bid(curve, asking, stake(points * 1.5, n, rate, e, k))
            assert more is not None and more.amount >= got.amount - 1e-6, "more points never lower the bid"
            twice = bid(curve, 2 * asking, stake(2 * points, n, rate, Exit(2 * asking, e.nights, e.rise, 2 * e.reserve), k))
            assert twice is not None and abs(twice.amount - 2 * got.amount) < 1e-3 and twice.win == got.win, "scale-invariant"
    assert seen_bid > 100 and seen_none > 100, (seen_bid, seen_none)
    flat = ((1.0, 1.0), (1.1, 1.0))
    assert bid(flat, 1e7, stake(10.0, 25, 0.02, Exit(1e7), 25)) == Bid(1e7, 1.0), "a sure win at the asking price bids the asking price"
    assert bid(((1.0, 0.1), (1.1, 0.2)), 1e7, stake(10.0, 25, 0.02, Exit(1e7), 25)) is None, "a chance under WIN_FLOOR: no bid"
    assert cap(stake(25 * 0.02 * 10, 25, 0.02, Exit(0.0), 25)) == K * 10e6, "P worth exactly the rate on 10M: K x 10M"
    assert bid(flat, 1e7, stake(0.0, 25, 0.02, Exit(1e7), 25)) is None, "no points, capital tied the whole window: never bought"


def _selftest_stake() -> None:
    rng = random.Random(31)
    for _ in range(3000):
        value, n, rate = rng.uniform(1e5, 5e7), rng.choice([1, 2, 13, 26]), rng.choice([0.02, 0.045, 0.1])
        points = rng.choice([0.0, rng.uniform(0, 3) * n * rate * value / 1e6])
        e = Exit(value, rng.choice([1, 7, 10]), rng.uniform(-TREND_CAP, TREND_CAP), rng.choice([0.0, rng.uniform(0.9, 1.1) * value]))
        k = rng.choice([0, 1, n])
        s = stake(points, n, rate, e, k)
        assert cap(s) >= 0 and cap(s) <= reserve(s) + 1e-6, "a cap is never negative and never over the reserve"
        assert s.proceeds >= later(e) - 1e-9 and proceeds(e._replace(reserve=0.0)) == later(e), "the proceeds are never under the later price"
        assert cap(stake(points + 1, n, rate, e, k)) >= cap(s) and reserve(stake(points + 1, n, rate, e, k)) >= reserve(s), "monotone in points"
        richer = e._replace(rise=e.rise + 0.01)
        assert cap(stake(points, n, rate, richer, k)) >= cap(s) and reserve(stake(points, n, rate, richer, k)) >= reserve(s), "monotone in drift"
        assert cap(stake(points + 1, n, 2 * rate, e, k)) <= cap(stake(points + 1, n, rate, e, k)), "non-increasing in the rate"
        assert reserve(stake(points + 1, n, 2 * rate, e, k)) <= reserve(stake(points + 1, n, rate, e, k))
        assert cap(stake(points, n, rate, e, min(n, k + 1))) <= cap(s) + 1e-9, "a longer tie never raises the cap"
        twice = stake(2 * points, n, rate, Exit(2 * value, e.nights, e.rise, 2 * e.reserve), k)
        assert abs(cap(twice) - 2 * cap(s)) < 1e-3 and abs(reserve(twice) - 2 * reserve(s)) < 1e-3, "scale-invariant"
        tied = stake(points, n, rate, e, n)
        assert cap(tied) == K * points * 1e6 / (n * rate), "capital tied the whole window: the points alone, K x W"
        assert cap(stake(0.0, n, rate, e, n)) == 0.0 and cap(stake(0.0, n, rate, Exit(value, 1, 0.0), n)) == 0.0, "no points, no free capital: cap 0"
        free = stake(points, n, rate, e, 0)
        assert cap(free) == max(K * points * 1e6 / (n * rate), free.proceeds), "capital free between locks: the proceeds"
        assert reserve(stake(points, n, rate, e, 1)) == reserve(free), "the reserve does not read k"
        assert reserve(s) >= (points * 1e6 / (n * rate) + e.rise * value) / K - 1e-6, "the price change forgone stays on W"
    assert cap(stake(0.0, 5, 0.045, Exit(1e6), 0)) == draw(1e6), "free capital and nothing else: his draw"


def _selftest_hold() -> None:
    rng = random.Random(77)
    for _ in range(3000):
        value, n, rate = rng.uniform(1e5, 5e7), rng.choice([1, 2, 13, 26]), rng.choice([0.02, 0.045, 0.1])
        fitted = rng.uniform(-0.5, 1.5)
        h = held_rise(value, fitted)
        assert 0.0 <= h <= HOLD_EDGE * HOLD_BOUND and (value > HOLD_VALUE or fitted <= 0) <= (h == 0.0), "never negative, never above the bound; none for a fall or a dear player"
        assert held_rise(value, fitted + 0.1) >= h and held_rise(value, HOLD_BOUND + 1.0) == held_rise(value, HOLD_BOUND), "nondecreasing, flat above the bound"
        e = Exit(value, rng.choice([1, 7]), rng.uniform(-TREND_CAP, TREND_CAP), rng.choice([0.0, value]), h)
        assert abs(held(e) - draw(value) * (1 + h)) < 1e-6 and held(e._replace(hold=h + 0.1)) >= held(e) and held(e) >= draw(value)
        points = rng.choice([0.0, rng.uniform(0, 3) * n * rate * value / 1e6])
        s = hold_stake(points, n, rate, e)
        tied = stake(points, n, rate, e, n)
        assert cap(s) >= cap(tied) - 1e-6, "a hold never lowers the cap of the points alone"
        assert cap(hold_stake(points, n, 2 * rate, e)) <= cap(s) + 1e-6, "nonincreasing in the rate"
        assert abs(cap(hold_stake(2 * points, n, rate, Exit(2 * value, e.nights, e.rise, 2 * e.reserve, h))) - 2 * cap(s)) < 1e-3, "scale-invariant"
        amount = rng.uniform(0.5, 1.5) * value
        assert profit(s, amount) >= 0 and profit(s, amount * 1.1) <= profit(s, amount) and profit(tied, amount) == 0.0, "never negative, falls with the bid, none when capital is tied"
        assert profit(s, max(cap(s), amount)) == 0.0 and profit(s._replace(k=min(n, s.k + 1)), amount) <= profit(s, amount) + 1e-9, "nothing at or over the cap; nonincreasing in k"
        assert abs(in_points(2 * amount, n, rate) - 2 * in_points(amount, n, rate)) < 1e-9 and in_points(0.0, n, rate) == 0.0
    assert cap(hold_stake(0.0, 25, 0.045, Exit(1e6, hold=0.1))) == held(Exit(1e6, hold=0.1)), "no points: the hold's proceeds, the capital being free"


def _selftest_sale() -> None:
    rng = random.Random(5)
    for _ in range(2000):
        n, rate, value = rng.choice([1, 13, 26]), rng.choice([0.02, 0.045]), rng.choice([1e6, 2e7])
        points, nights, rise = rng.uniform(0, 50), rng.choice([1, 7, 10]), rng.choice([0.0, -0.03, -0.01, 0.01, 0.03])
        e = Exit(value, nights, rise)
        s = stake(points, n, rate, e)
        worth = points * 1e6 / (n * rate)
        assert abs(reserve(s) - max((worth + rise * value) / K, proceeds(e))) < 1e-6, "the band: W plus the drift over K, or the exit's proceeds"
        assert cap(stake(points, n, rate, Exit(value), n)) * reserve(stake(points, n, rate, Exit(0.0))) - worth * worth < 1e-3 * (1 + worth * worth), "buy edge x sell edge is W squared"
        assert reserve(stake(points + 1, n, rate, e)) >= reserve(s) and reserve(stake(points, n, rate, e._replace(rise=rise + 0.01))) >= reserve(s)
        assert reserve(stake(points, n, rate, e._replace(nights=2 * nights))) == reserve(s), "the nights do not enter the reserve"
    assert reserve(stake(0.0, 5, 0.045, Exit(1e6))) == draw(1e6), "no points: the exit's proceeds"


def _selftest_exit() -> None:
    rng = random.Random(21)
    for _ in range(2000):
        value, nights, rise = rng.uniform(1e5, 5e7), rng.randint(1, 12), rng.uniform(-0.3, 0.3)
        e = Exit(value, nights, rise)
        assert abs(later(e) / draw(value) - 1 - rise) < 1e-9, "the later price reads the exit's rise"
        assert abs(wait_cost(e) * nights + rise) < 1e-12, "the night's cost is the same rise spread over the same nights"
        assert abs(later(e._replace(value=2 * value)) - 2 * later(e)) < 1e-3 and abs(wait_cost(e._replace(value=2 * value)) - wait_cost(e)) < 1e-12
        flat = Exit(value, nights)
        assert later(flat) == draw(value) and wait_cost(flat) == 0.0, "no rise: the draw, and waiting is free"
        assert proceeds(flat) == draw(value) and proceeds(e._replace(reserve=2 * value)) == max(2 * value, later(e))
    assert wait_cost(Exit(0.0, 10, 0.5)) == 0.0


def _selftest_stand() -> None:
    rng = random.Random(12)
    seen: dict[str, int] = {}
    for _ in range(4000):
        grid = (1.0, 1.02, 1.05, 1.1, 1.2, 1.3, 1.5)
        curve = tuple((r, min(1.0, rng.choice([0.0, 0.1, 0.3]) + (r - 1.0) * rng.choice([0.5, 2.0, 5.0]))) for r in (0.9, *grid))
        asking = rng.choice([0.6e6, 8e6, 40e6])
        cap = asking * rng.uniform(0.7, 1.8)
        placed = asking * rng.choice([1.0, 1.05, 1.07, 1.2, 1.4, 1.6, 2.0])
        got = stand(curve, asking, placed, cap)
        seen[got.move] = seen.get(got.move, 0) + 1
        scores = [(p * (cap - asking * r), asking * r) for r, p in curve if r >= 1.0]
        top = max(s for s, _ in scores)
        assert (got.move == "withdraw") == (top <= 0), "withdraw iff no bid of the grid has a positive surplus"
        assert abs(got.surplus - chance(curve, placed / asking) * (cap - placed)) < 1e-6
        if got.move == "withdraw":
            assert got.to is None and got.to_surplus is None
            continue
        if got.move in ("lower", "raise"):
            assert got.to is not None and got.to_surplus is not None and abs(got.to_surplus - top) < 1e-6, "to is the grid's own argmax"
            assert got.to <= cap + 1e-6 and (got.to < placed) == (got.move == "lower")
        if got.move == "raise":
            assert got.to_surplus is not None and got.to_surplus > got.surplus, "a raise is to a strictly higher surplus"
        if got.move == "hold":
            assert placed <= cap + 1e-6 and got.surplus > 0 and got.to is None, "a hold is at or under the walk-away with a positive surplus"
        if placed > cap:
            assert got.move == "lower", "above the walk-away price with a bid worth making: lower"
        if got.surplus > 0 and any(abs(placed - a) < 1e-6 and s >= top - 1e-9 for s, a in scores):
            assert got.move == "hold", "at the best amount: hold"
    assert all(seen.get(m, 0) > 50 for m in ("hold", "lower", "withdraw", "raise")), seen
    flat = ((1.0, 0.5), (1.1, 1.0))
    assert stand(flat, 10e6, 10e6, 10e6) == Stand("withdraw", None, 0.0, None, None)
    assert stand(flat, 10e6, 13e6, 12e6).move == "lower" and stand(flat, 10e6, 13e6, 12e6).to == 10e6


def _selftest() -> None:
    _selftest_stand()
    _selftest_lam()
    _selftest_buy()
    _selftest_stake()
    _selftest_sale()
    _selftest_hold()
    _selftest_exit()
    print("hurdle self-test OK")


if __name__ == "__main__" and "--selftest" in sys.argv:
    _selftest()
