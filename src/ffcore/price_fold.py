"""Price series, momentum and the price trend as a fold over the market snapshots (forecast): what the SQL views
price_history .. price_grade and the kept price_step / momentum_day say, from the observed market rows alone.

One fold, "price", registered with ffcore.state: its state holds each day's closing snapshot of the market, each
player's closed days (price_step), his changes with the signal x each carries (price_series), and per horizon
h <= level the sums momentum fits on, per closing day and cumulative (momentum_day). Everything else is a
selector over that state at the cut, a function of the rows with observed_at <= cut and nothing else: the day of
the cut is the day of the last snapshot, its rows are live (not closed) and only worked out against the closed
days. Momentum has no window: all-time sums over the closed days, with the live day's pairs added. The constants
(pricing.CLIP, PRICE_W0..2, PRICE_FOCUS) are those standing when the sweep starts.

Rules copied from the SQL, each tested:
  last_snapshot_of_a_day: a day closes on the rows of its last snapshot; earlier ones that day are dropped.
  step_needs_positive_prev: a value's change is None for a player's first day or after a value of 0.
  signal_falls_back: a missing older change counts as the next newer one (x = w0*c + w1*(c1|c) + w2*(c2|c1|c)).
  live_rows_not_chained: two rows of one player in the live snapshot are each worked out against the last closed
    day, while two in a closed snapshot chain.
  zero_start_no_y: a pair whose start value is 0 has no change (SQL NULL): it adds x*x, nothing to x*y.
  slope_none_on_zero_sxx: a cumulative slope with sum x*x 0 is None, and a trend with no slope is 0.
  hmax_median: the longest horizon is a quarter of the median count of changes over players (closed or live; the
    live rows of one player add), at least 1.
  grade_days: price_grade fits from the day a quarter through the distinct change days.
Selectors: price_state; the views by their names (price_history .. price_grade) over a PriceState;
price_momentum_of is forecast_store.price_momentum() from the state."""
from __future__ import annotations

import bisect
import math
import random
from collections import defaultdict
from datetime import date, timedelta
from collections.abc import Callable, Iterable
from typing import Any, NamedTuple, TypeVar, cast

from ffcore import observations as ob
from ffcore import pricing
from ffcore import state as st
from ffcore.forecast_store import Prices

T = TypeVar("T")
GRADE_HORIZONS = (1, 2, 3, 5, 7, 10)


class Constants(NamedTuple):
    clip: float
    w0: float
    w1: float
    w2: float
    focus: float


class Step(NamedTuple):
    ff_id: str
    n: int
    day: str
    observed_at: str
    value: float
    step: float | None
    i: int | None
    s1: float | None
    s2: float | None


class Change(NamedTuple):
    ff_id: str
    observed_at: str
    day: str
    step: float
    x: float
    i: int
    value: float


class Obs(NamedTuple):
    ff_id: str
    observed_at: str
    value: float
    first_at: str
    step: float | None


class Sums(NamedTuple):
    sxy: float
    sxx: float
    fxy: float
    fxx: float
    fyy: float
    fn: int


class DayFit(NamedTuple):
    h: int
    end_day: str
    sxy: float
    sxx: float
    fxy: float
    fxx: float
    fyy: float
    fn: int


class Ahead(NamedTuple):
    ff_id: str
    i: int
    day: str
    x: float
    value: float
    h: int
    end_at: str
    end_day: str
    change: float | None


class Pair(NamedTuple):
    h: int
    ff_id: str
    i: int
    end_day: str
    x: float
    focus: bool
    y: float | None


class HorizonSums(NamedTuple):
    h: int
    sums: Sums


class Fit(NamedTuple):
    h: int
    slope: float
    rmse: float | None


class Carry(NamedTuple):
    h: int
    carry: float


class DaySlope(NamedTuple):
    day: str
    slope: float | None


class Trend(NamedTuple):
    ff_id: str
    day: str
    trend: float


class PlayerTrend(NamedTuple):
    ff_id: str
    trend: float


class Expected(NamedTuple):
    ff_id: str
    h: int
    day: str
    value: float
    expected_value: float
    spread: float | None


class Grade(NamedTuple):
    scope: str
    h: int
    n: int
    rmse: float | None
    rmse_none: float | None
    rmse_again: float | None
    mae: float | None
    mae_none: float | None
    predicted: float | None
    actual: float | None


class PriceState:
    """The state of the "price" fold; read only through the selectors of this module."""

    def __init__(self) -> None:
        self.k = Constants(float(pricing.CLIP), float(pricing.PRICE_W0), float(pricing.PRICE_W1),
                           float(pricing.PRICE_W2), float(pricing.PRICE_FOCUS))
        self.history: list[ob.Market] = []
        self.stamp = ""
        self.day = ""
        self.pending: list[ob.Market] = []
        self.daily: list[ob.Market] = []
        self.steps: dict[str, list[Step]] = defaultdict(list)
        self.changes: dict[str, list[Change]] = defaultdict(list)
        self.level = 0
        self.closed_changes: list[list[tuple[str, int]]] = []
        self.days: dict[int, list[DayFit]] = defaultdict(list)
        self.cum: dict[int, list[Sums]] = defaultdict(list)
        self.memo: dict[str, Any] = {}


def _clip(v: float, lim: float) -> float:
    return -lim if v < -lim else lim if v > lim else v


def _signal(k: Constants, step: float, s1: float | None, s2: float | None) -> float:
    c = _clip(step, k.clip)
    c1 = None if s1 is None else _clip(s1, k.clip)
    c2 = None if s2 is None else _clip(s2, k.clip)
    older = c if c1 is None else c1
    return k.w0 * c + k.w1 * older + k.w2 * (older if c2 is None else c2)


def _add_pairs(acc: list[list[float]], k: Constants, ch: list[Change], j: int, value: float, lo: int, top: int) -> None:
    """The pairs ending in value at a player's change number j (ch[:j] are the starts), lo <= h <= top, added to
    acc: row h - 1 holds sxy, sxx, fxy, fxx, fyy, fn of horizon h; acc grows to the longest h reached (each h up
    to it has a row)."""
    m = min(j, top)
    while len(acc) < m:
        acc.append([0.0, 0.0, 0.0, 0.0, 0.0, 0])
    clip, focus = k.clip, k.focus
    for h in range(lo, m + 1):
        a = ch[j - h]
        s = acc[h - 1]
        x = a.x
        s[1] += x * x
        av = a.value
        if av == 0:
            continue
        y = 100 * (value / av - 1)
        lim = clip * h
        y = -lim if y < -lim else lim if y > lim else y
        s[0] += x * y
        if av >= focus:
            s[2] += x * y
            s[3] += x * x
            s[4] += y * y
            s[5] += 1


def _plus(a: Sums, v: list[float]) -> Sums:
    return Sums(a.sxy + v[0], a.sxx + v[1], a.fxy + v[2], a.fxx + v[3], a.fyy + v[4], a.fn + int(v[5]))


def _quarter_median(counts: Iterable[int]) -> int:
    """A quarter of the median of counts (the mean of the two middle ones, truncated, when even), at least 1."""
    ns = sorted(counts)
    m = len(ns)
    if not m:
        return 1
    return max(1, int((ns[(m + 1) // 2 - 1] + ns[(m + 2) // 2 - 1]) / 2) // 4)


def _fit_days(s: PriceState, acc: list[list[float]], day: str, lo: int) -> None:
    for h in range(lo, len(acc) + 1):
        v = acc[h - 1]
        s.days[h].append(DayFit(h, day, v[0], v[1], v[2], v[3], v[4], int(v[5])))
        before = s.cum[h][-1] if s.cum[h] else Sums(0.0, 0.0, 0.0, 0.0, 0.0, 0)
        s.cum[h].append(_plus(before, v))


def _widen(s: PriceState, top: int) -> None:
    """Hold the per-day and cumulative sums of every horizon up to top, worked out from the closed days (a no-op
    when s.level >= top)."""
    lo = s.level + 1
    if top < lo:
        return
    for made in s.closed_changes:
        acc: list[list[float]] = []
        for ff, j in made:
            ch = s.changes[ff]
            _add_pairs(acc, s.k, ch, j, ch[j].value, lo, top)
        if made:
            _fit_days(s, acc, s.changes[made[0][0]][made[0][1]].day, lo)
    s.level = top


def _close(s: PriceState, rows: list[ob.Market]) -> None:
    s.daily.extend(rows)
    acc: list[list[float]] = []
    made: list[tuple[str, int]] = []
    day = rows[0].observed_at[:10]
    for r in _valued(rows):
        closed = s.steps[r.ff_id]
        prev = closed[-1].value if closed else None
        step = 100 * (r.value / prev - 1) if prev is not None and prev > 0 else None
        if step is None:
            closed.append(Step(r.ff_id, len(closed), day, r.observed_at, r.value, None, None, None, None))
            continue
        ch = s.changes[r.ff_id]
        s1 = ch[-1].step if ch else None
        s2 = ch[-2].step if len(ch) > 1 else None
        _add_pairs(acc, s.k, ch, len(ch), r.value, 1, s.level)
        made.append((r.ff_id, len(ch)))
        closed.append(Step(r.ff_id, len(closed), day, r.observed_at, r.value, step, len(ch), s1, s2))
        ch.append(Change(r.ff_id, r.observed_at, day, step, _signal(s.k, step, s1, s2), len(ch), r.value))
    _fit_days(s, acc, day, 1)
    s.closed_changes.append(made)
    _widen(s, _quarter_median(len(s.changes.get(ff, ())) for ff in s.steps))


def _step(s: PriceState, row: ob.Market) -> PriceState:
    s.history.append(row)
    s.memo.clear()
    if row.observed_at != s.stamp:
        day = row.observed_at[:10]
        if s.pending and day != s.day:
            _close(s, s.pending)
        s.pending = []
        s.stamp, s.day = row.observed_at, day
    s.pending.append(row)
    return s


FOLD = st.register(st.Fold("price", "market", PriceState, _step))


def _held(f: Callable[[PriceState], T]) -> Callable[[PriceState], T]:
    """f as a selector remembered until the next row folds in (its value must not be changed by a caller)."""
    def held(s: PriceState) -> T:
        if f.__name__ not in s.memo:
            s.memo[f.__name__] = f(s)
        return cast(T, s.memo[f.__name__])
    held.__name__ = f.__name__
    held.__doc__ = f.__doc__
    return held


def price_state(world: st.World) -> PriceState:
    """The state of the price fold in world."""
    return cast(PriceState, st.fact(world, "price"))


def price_history(s: PriceState) -> list[ob.Market]:
    """Every market row up to the cut, in the order stored."""
    return s.history


def market_daily(s: PriceState) -> list[ob.Market]:
    """The rows of each day's last snapshot up to the cut (the cut's day's included)."""
    return s.daily + s.pending


def price_day_now(s: PriceState) -> str:
    """The day of the last snapshot up to the cut ('' when none)."""
    return s.day


class Valued(NamedTuple):
    ff_id: str
    observed_at: str
    value: float


def _valued(rows: list[ob.Market]) -> list[Valued]:
    return [Valued(r.ff_id, r.observed_at, r.value) for r in rows if r.ff_id and r.value is not None]


def price_obs(s: PriceState) -> list[Obs]:
    """Every valued day of each player up to the cut (the live day chained), with the change since the previous."""
    out = [Obs(x.ff_id, x.observed_at, x.value, ch[0].observed_at, x.step)
           for ch in s.steps.values() for x in ch]
    last = {ff: ch[-1].value for ff, ch in s.steps.items()}
    first = {ff: ch[0].observed_at for ff, ch in s.steps.items()}
    for r in _valued(s.pending):
        prev = last.get(r.ff_id)
        out.append(Obs(r.ff_id, r.observed_at, r.value, first.setdefault(r.ff_id, r.observed_at),
                       100 * (r.value / prev - 1) if prev is not None and prev > 0 else None))
        last[r.ff_id] = r.value
    return out


def price_step(s: PriceState) -> list[Step]:
    """The closed days of every player (what the kept price_step table holds as of the cut)."""
    return [x for ch in s.steps.values() for x in ch]


def price_past(s: PriceState) -> list[Step]:
    """The closed days before the cut's day."""
    return price_step(s)


def momentum_day(s: PriceState) -> list[DayFit]:
    """What each closed day added to the fit, per horizon h <= hmax, for the days with pairs (horizons beyond hmax
    are held only as far as earlier cuts needed them, so they are not part of the result)."""
    top = price_hmax(s)
    return [d for h, ds in s.days.items() if h <= top for d in ds]


@_held
def price_live(s: PriceState) -> list[Step]:
    """The cut's day: each valued row of the last snapshot against its player's last closed day."""
    out = []
    for r in _valued(s.pending):
        closed = s.steps.get(r.ff_id, [])
        ch = s.changes.get(r.ff_id, [])
        pv = closed[-1].value if closed else None
        if pv is not None and pv > 0:
            out.append(Step(r.ff_id, len(closed), s.day, r.observed_at, r.value, 100 * (r.value / pv - 1), len(ch),
                            ch[-1].step if ch else None, ch[-2].step if len(ch) > 1 else None))
        else:
            out.append(Step(r.ff_id, len(closed), s.day, r.observed_at, r.value, None, None, None, None))
    return out


@_held
def _live_changes(s: PriceState) -> list[Change]:
    return [Change(x.ff_id, x.observed_at, x.day, x.step, _signal(s.k, x.step, x.s1, x.s2), x.i, x.value)
            for x in price_live(s) if x.step is not None and x.i is not None]


def price_series(s: PriceState) -> list[Change]:
    """Every change up to the cut, closed and live, with its signal x."""
    return [c for ch in s.changes.values() for c in ch] + _live_changes(s)


@_held
def price_hmax(s: PriceState) -> int:
    """The longest horizon fitted: a quarter of the median number of changes over players, at least 1."""
    lens: dict[str, int] = {ff: len(s.changes.get(ff, ())) for ff in s.steps}
    for x in price_live(s):
        lens[x.ff_id] = lens.get(x.ff_id, 0) + (x.step is not None)
    top = _quarter_median(lens.values())
    _widen(s, top)
    return top


def _ends(s: PriceState) -> list[tuple[list[Change], Change]]:
    """Each change, closed or live, with its player's closed changes (the starts a pair looks back on)."""
    return [(ch, c) for ch in s.changes.values() for c in ch] + [(s.changes.get(c.ff_id, []), c) for c in _live_changes(s)]


def price_ahead(s: PriceState) -> list[Ahead]:
    """Each player's change over the h changes after his i-th (closed), ending at each later change, h <= hmax."""
    top = price_hmax(s)
    out = []
    for ch, b in _ends(s):
        for h in range(1, min(b.i, top) + 1):
            a = ch[b.i - h]
            out.append(Ahead(a.ff_id, a.i, a.day, a.x, a.value, h, b.observed_at, b.day,
                             100 * (b.value / a.value - 1) if a.value != 0 else None))
    return out


def momentum_pairs(s: PriceState) -> list[Pair]:
    """price_ahead with the move clipped at CLIP an update (y) and whether the player was worth PRICE_FOCUS."""
    return [Pair(p.h, p.ff_id, p.i, p.end_day, p.x, p.value >= s.k.focus,
                 None if p.change is None else _clip(p.change, s.k.clip * p.h)) for p in price_ahead(s)]


@_held
def momentum_sums(s: PriceState) -> list[HorizonSums]:
    """The fit's sums per horizon h <= hmax: the closed days' and the live day's pairs."""
    top = price_hmax(s)
    live: list[list[float]] = []
    for c in _live_changes(s):
        _add_pairs(live, s.k, s.changes.get(c.ff_id, []), c.i, c.value, 1, top)
    out = []
    for h in range(1, top + 1):
        if not s.cum.get(h) and h > len(live):
            continue
        base = s.cum[h][-1] if s.cum.get(h) else Sums(0.0, 0.0, 0.0, 0.0, 0.0, 0)
        add = live[h - 1] if h <= len(live) else [0.0, 0.0, 0.0, 0.0, 0.0, 0]
        out.append(HorizonSums(h, _plus(base, add)))
    return out


def momentum(s: PriceState) -> list[Fit]:
    """One least-squares slope through the origin per horizon and the spread of a change around it for the focus
    players (None where there are none); horizons with no x*x are absent."""
    out = []
    for h, m in momentum_sums(s):
        if m.sxx == 0:
            continue
        slope = m.sxy / m.sxx
        rmse = math.sqrt(max(m.fyy - 2 * m.sxy / m.sxx * m.fxy + m.sxy * m.sxy / (m.sxx * m.sxx) * m.fxx, 0) / m.fn) \
            if m.fn > 0 else None
        out.append(Fit(h, slope, rmse))
    return out


def price_carry(s: PriceState) -> list[Carry]:
    """The share of a trend (its move over the longest horizon fitted) made after each update, by horizon."""
    fits = momentum(s)
    if not fits or fits[-1].slope == 0:
        return []
    return [Carry(f.h, f.slope / fits[-1].slope) for f in fits]


def price_slope(s: PriceState) -> list[DaySlope]:
    """The slope at the longest horizon fitted, each closed day with pairs (the fit on the pairs ended by it) and
    the cut's day (momentum's)."""
    fits = momentum(s)
    if not fits:
        return []
    top = fits[-1]
    out = [DaySlope(d.end_day, c.sxy / c.sxx if c.sxx != 0 else None)
           for d, c in zip(s.days.get(top.h, ()), s.cum.get(top.h, ()))]
    return out + [DaySlope(s.day, top.slope)]


def price_trend_daily(s: PriceState) -> list[Trend]:
    """Each change's value change in % over the horizon: the slope of its day (0 with none) times its signal x."""
    slope = {d.day: d.slope for d in price_slope(s)}
    return [Trend(c.ff_id, c.day, (slope.get(c.day) or 0.0) * c.x) for c in price_series(s)]


def price_trend(s: PriceState) -> list[PlayerTrend]:
    """The trend of each player whose last change is the cut's day."""
    fits = momentum(s)
    slope = fits[-1].slope if fits else 0.0
    return [PlayerTrend(c.ff_id, slope * c.x) for c in _live_changes(s)]


def _julian(day: str) -> float:
    return date.fromisoformat(day).toordinal() + 1721424.5


def price_forecast(s: PriceState, next_lock: float | None) -> list[Expected]:
    """Each player's expected value night by night from the cut's day to the next lock (a Julian day, None when
    unknown), at most the longest horizon: h updates on, value, expected value and spread."""
    top = price_hmax(s)
    nights = max(1, min(top, top if next_lock is None else math.ceil(next_lock - _julian(s.day))))
    fits = [f for f in momentum(s) if f.h <= nights]
    out = []
    for c in _live_changes(s):
        for f in fits:
            out.append(Expected(c.ff_id, f.h, (date.fromisoformat(s.day) + timedelta(days=f.h)).isoformat(), c.value,
                                c.value * (1 + f.slope * c.x / 100),
                                c.value * f.rmse / 100 if c.value >= s.k.focus and f.rmse is not None else None))
    return out


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def _root(xs: list[float]) -> float | None:
    m = _mean(xs)
    return None if m is None else math.sqrt(m)


def price_grade(s: PriceState) -> list[Grade]:
    """Momentum's forecast of a change over h updates, each day refitted on the pairs ended by it (from the day a
    quarter through the change days), against what happened: per scope (focus, all) and h in GRADE_HORIZONS."""
    top = price_hmax(s)
    changes = price_series(s)
    days = sorted({c.day for c in changes})
    first = days[len(days) // 4] if days else ""
    pred: list[tuple[int, float, float, float | None, float]] = []
    ends_of = {h: [d.end_day for d in s.days.get(h, [])] for h in GRADE_HORIZONS}
    for ch, b in _ends(s):
        for h in GRADE_HORIZONS:
            if h > min(b.i, top):
                continue
            a = ch[b.i - h]
            if a.day < first:
                continue
            at = bisect.bisect_right(ends_of[h], a.day) - 1
            if at < 0 or s.cum[h][at].sxx == 0:
                continue
            fit = s.cum[h][at]
            act = 100 * (b.value / a.value - 1) if a.value != 0 else None
            p = fit.sxy / fit.sxx * a.x
            again = h * _clip(a.step, s.k.clip)
            pred.append((h, a.value, p, act, again))
    out = []
    for scope in ("focus", "all"):
        for h in GRADE_HORIZONS:
            rows = [r for r in pred if r[0] == h and (scope == "all" or r[1] >= s.k.focus)]
            if not rows:
                continue
            both = [(p, act, again) for _, _, p, act, again in rows if act is not None]
            actual = [act for _, _, _, act, _ in rows if act is not None]
            out.append(Grade(scope, h, len(rows), _root([(p - act) ** 2 for p, act, _ in both]),
                             _root([a * a for a in actual]), _root([(g - act) ** 2 for _, act, g in both]),
                             _mean([abs(p - act) for p, act, _ in both]), _mean([abs(a) for a in actual]),
                             _mean([p for p, _, _ in both]), _mean(actual)))
    return out


def price_momentum_of(s: PriceState) -> Prices:
    """forecast_store.price_momentum() as of the state's cut: slope per horizon, carry, each live player's trend
    and the horizon."""
    slope = {f.h: f.slope for f in momentum(s)}
    return Prices(slope, tuple(c.carry for c in price_carry(s)), {t.ff_id: t.trend for t in price_trend(s)},
                  max(slope, default=1))


def everything(s: PriceState, next_lock: float | None = None) -> dict[str, Any]:
    """Every selector's rows in a canonical (sorted) form: what the invariants compare."""
    return {name: sorted(rows, key=repr) for name, rows in (
        ("history", price_history(s)), ("daily", market_daily(s)), ("obs", price_obs(s)), ("step", price_step(s)),
        ("day", momentum_day(s)), ("live", price_live(s)), ("series", price_series(s)), ("ahead", price_ahead(s)),
        ("pairs", momentum_pairs(s)), ("sums", momentum_sums(s)), ("momentum", momentum(s)),
        ("carry", price_carry(s)), ("slope", price_slope(s)), ("trend_daily", price_trend_daily(s)),
        ("trend", price_trend(s)), ("forecast", price_forecast(s, next_lock)), ("grade", price_grade(s)))} \
        | {"hmax": price_hmax(s), "now": price_day_now(s)}


def _market(rng: random.Random) -> list[ob.Market]:
    """Eight players over 40 days of two snapshots each, one value 0, one duplicated row, moves of both signs."""
    value = {"p%d" % i: 5e6 * (i + 1) for i in range(8)}
    rows: list[ob.Market] = []
    for d in range(40):
        day = (date(2026, 9, 1) + timedelta(days=d)).isoformat()
        for hour in ("08", "20"):
            at = "%sT%s00Z" % (day, hour)
            for ff in sorted(value):
                if rng.random() < 0.8:
                    value[ff] = max(300000.0, value[ff] * (1 + rng.uniform(-0.05, 0.08)))
                if ff == "p3" and d == 20:
                    value[ff] = 0.0
                rows.append(ob.Market(at, "", value[ff], ff, "DEL", "RMA"))
            rows.append(ob.Market(at, "", None, "p9", "DEL", "RMA"))
            rows.append(ob.Market(at, "", 1.0, "", "DEL", "RMA"))
    rows.append(ob.Market("2026-10-10T2000Z", "", 6e6, "p1", "DEL", "RMA"))
    return rows


def _selftest() -> None:
    """Later rows leave a cut unchanged; any subset or order of cuts gives each cut the same facts; one cut alone
    equals the cut inside a sweep; the incremental sums equal a recomputation from the pairs; the named SQL
    rules hold on small cases."""
    rows = _market(random.Random(7))
    obs = ob.of({"market": rows})
    only = {"price": FOLD}

    def read(w: st.World) -> Any:
        return everything(price_state(w))
    cuts = ["2026-09-01T0900Z", "2026-09-10T0800Z", "2026-09-30T2100Z", "2026-10-09T2000Z", "2026-10-10T0000Z"]
    alone = {c: st.world_at(obs, c, only) for c in cuts}
    inside = dict(zip(cuts, st.sweep(obs, cuts, read, only)))
    assert all(read(alone[c]) == inside[c] for c in cuts)
    for order in (cuts[::-1], [cuts[3], cuts[0], cuts[3]], [cuts[2]]):
        assert st.sweep(obs, order, read, only) == [inside[c] for c in order]
    later = ob.of({"market": rows + [ob.Market("2026-10-12T0800Z", "", 9e6, "p1", "DEL", "RMA"),
                                      ob.Market("2026-10-13T0800Z", "", 1e6, "p2", "DEL", "RMA")]})
    assert [read(st.world_at(later, c, only)) for c in cuts] == [inside[c] for c in cuts]

    s = price_state(alone["2026-10-09T2000Z"])
    assert price_day_now(s) == "2026-10-09" and price_hmax(s) >= 1
    by_h: dict[int, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for p in momentum_pairs(s):
        if p.h <= price_hmax(s):
            by_h[p.h][1] += p.x * p.x
            by_h[p.h][0] += p.x * p.y if p.y is not None else 0.0
    sums = {h.h: h.sums for h in momentum_sums(s)}
    assert set(sums) == set(by_h)
    for h, (sxy, sxx) in by_h.items():
        assert math.isclose(sums[h].sxy, sxy, rel_tol=1e-9, abs_tol=1e-9) and math.isclose(sums[h].sxx, sxx, rel_tol=1e-9)
    assert [f.h for f in momentum(s)] == sorted(f.h for f in momentum(s))
    assert {t.ff_id for t in price_trend(s)} <= {t.ff_id for t in price_trend_daily(s) if t.day == "2026-10-09"}
    assert price_momentum_of(s).horizon == max(f.h for f in momentum(s))

    def mk(at: str, v: dict[str, float | None]) -> list[ob.Market]:
        return [ob.Market(at, "", x, ff, "DEL", "RMA") for ff, x in v.items()]
    small = mk("2026-08-10T0800Z", {"a": 100.0, "b": 7.0}) + mk("2026-08-11T0800Z", {"a": 105.0}) \
        + mk("2026-08-11T2000Z", {"a": 110.0}) + mk("2026-08-12T2000Z", {"a": 0.0}) \
        + mk("2026-08-13T2000Z", {"a": 50.0}) + mk("2026-08-14T0800Z", {"a": 60.0})
    w = price_state(st.world_at(ob.of({"market": small}), "2026-08-14T0900Z", only))
    got = [(x.ff_id, x.n, x.day, None if x.step is None else round(x.step, 4), x.i) for x in price_step(w)]
    assert got == [("a", 0, "2026-08-10", None, None), ("a", 1, "2026-08-11", 10.0, 0), ("a", 2, "2026-08-12", -100.0, 1),
                   ("a", 3, "2026-08-13", None, None), ("b", 0, "2026-08-10", None, None)], \
        "last_snapshot_of_a_day, step_needs_positive_prev"
    assert [round(c.x, 9) for c in price_series(w)[:2]] == [4.0 + 4.0 + 2.0, 0.4 * -20 + 0.4 * 10 + 0.2 * 10], "signal_falls_back"
    dup = small + mk("2026-08-14T0800Z", {"a": 70.0})
    live = price_live(price_state(st.world_at(ob.of({"market": dup}), "2026-08-14T0900Z", only)))
    assert [(round(x.step or 0, 4), x.i) for x in live if x.ff_id == "a"] == [(20.0, 2), (40.0, 2)], "live_rows_not_chained"
    zero = mk("2026-08-10T0800Z", {"a": 100.0}) + mk("2026-08-11T0800Z", {"a": 0.0}) + mk("2026-08-12T0800Z", {"a": 50.0}) \
        + mk("2026-08-13T0800Z", {"a": 10.0})
    zw = price_state(st.world_at(ob.of({"market": zero}), "2026-08-13T0900Z", only))
    assert momentum_pairs(zw)[0].y is None and momentum_sums(zw)[0].sums.sxy == 0.0 and momentum_sums(zw)[0].sums.sxx > 0, "zero_start_no_y"

    def after(rows_: list[ob.Market], cut: str) -> PriceState:
        return price_state(st.world_at(ob.of({"market": rows_}), cut, only))

    def daily(values: list[float], ff: str = "a") -> list[ob.Market]:
        return [r for i, v in enumerate(values) for r in mk("2026-09-%02dT0800Z" % (i + 1), {ff: v})]
    d = ["2026-09-%02d" % n for n in range(1, 7)]
    z = after(daily([100.0, 100.0, 100.0, 110.0, 121.0, 133.0]), "2026-09-06T0900Z")
    assert price_hmax(z) == 1
    got_slope = price_slope(z)
    assert [(x.day, x.slope) for x in got_slope[:2]] == [(d[2], None), (d[3], None)], "slope_none_on_zero_sxx"
    assert got_slope[2].day == d[4] and math.isclose(got_slope[2].slope or 0.0, (4 * 10) / (4 * 4)) \
        and (got_slope[3].day, got_slope[3].slope) == (d[5], momentum(z)[-1].slope), "slope_none_on_zero_sxx"
    t4 = after(daily([100.0, 110.0, 121.0, 133.0]), "2026-09-04T0900Z")
    trend = {x.day: x.trend for x in price_trend_daily(t4)}
    x1 = 0.4 * 10 + 0.4 * 10 + 0.2 * 10
    assert trend[d[1]] == 0.0 and math.isclose(trend[d[2]], 1.0 * x1), "slope_none_on_zero_sxx"
    flat = after(daily([100.0, 100.0, 100.0, 100.0]), "2026-09-04T0900Z")
    assert momentum(flat) == [] and price_series(flat) and all(x.trend == 0.0 for x in price_trend_daily(flat)), \
        "slope_none_on_zero_sxx"

    def counted(counts: list[int]) -> PriceState:
        rows_: list[ob.Market] = []
        for n in range(max(counts, default=0) + 1):
            day = "2026-09-%02d" % (21 + n)
            rows_ += mk(day + "T0800Z", {"q%d" % i: 100.0 * 1.07 ** (n - (max(counts) - c))
                                         for i, c in enumerate(counts) if max(counts) - n <= c})
        return after(rows_, "2026-09-%02dT0900Z" % (21 + max(counts, default=0)))
    assert price_hmax(price_state(st.world_at(ob.of({"market": []}), "2026-09-21T0900Z", only))) == 1, "hmax_median"
    for counts, want in (([3], 1), ([8, 8, 8, 8], 2), ([7, 8], 1), ([16, 4], 2), ([7, 7], 1), ([8, 8], 2)):
        assert price_hmax(counted(counts)) == want, "hmax_median"
    c8 = counted([8, 8])
    assert [len(c8.changes[ff]) for ff in ("q0", "q1")] == [7, 7], "hmax_median"

    g = after(daily([1.1 ** n * 100 for n in range(12)]), "2026-09-12T0900Z")
    ch = price_series(g)
    gdays = sorted({c.day for c in ch})

    def graded(index: int) -> int:
        return sum(1 for b in range(1, len(ch)) if ch[b - 1].day >= gdays[index])
    n_all = [r.n for r in price_grade(g) if r.scope == "all" and r.h == 1]
    assert len(gdays) == 11 and n_all == [graded(11 // 4)] == [8] and graded(len(gdays) // 3) == 7, "grade_days"

    cuts_ = ["2026-%s-%02dT1200Z" % (("09", n) if n <= 30 else ("10", n - 30)) for n in range(2, 41)]
    swept = st.sweep(obs, cuts_, lambda w_: (price_hmax(price_state(w_)), price_state(w_).level,
                                             max(price_state(w_).days, default=0), read(w_)), only)
    seen = [x[0] for x in swept]
    assert seen[-1] > seen[0] and len(set(seen)) >= 3
    for i, (hm, level, widest, facts) in enumerate(swept):
        assert widest <= level <= max(seen[:i + 1]), "level bounded by the horizons in use"
        assert facts == read(st.world_at(obs, cuts_[i], only)), "a cut alone equals the cut inside a sweep"
    print("ffcore.price_fold self-test OK")


if __name__ == "__main__":
    _selftest()
