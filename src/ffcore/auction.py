from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from math import sqrt
from typing import Any, NamedTuple, Sequence

from ffcore import pricing

__all__ = ["Look", "Sample", "Model", "Forecast", "RATIOS", "sample", "curve", "fit", "predict", "forecast", "chance", "bid_for",
           "looks", "write", "Record", "backtest_records", "points", "backtest", "stored_points", "calibration", "briers"]

RATIOS = (0.5, 0.6, 0.7, 0.8, 0.9) + tuple(round(0.95 + 0.01 * i, 2) for i in range(51))
ASK_LEVELS = (0.5, 0.75, 0.9)
TREND_LEVELS = (1 / 3, 2 / 3)
DECISION_MINS = 39.0
SCORE_RATIOS = (1.0, 1.05, 1.10, 1.15)
BINS = ((0.0, 0.5), (0.5, 0.8), (0.8, 0.9), (0.9, 0.95), (0.95, 0.99), (0.99, 1.01))
DRIFT_MIN = 20
DRIFT_SLACK = 0.02
EPS = 1e-9

Curve = tuple[tuple[float, float], ...]
Key = tuple[int, ...]


class Look(NamedTuple):
    player: str
    close: str
    look: str
    mins: float
    ask: float
    bids: int
    mine: bool
    rival: int
    trend: float | None
    my_bid: float | None
    won: float | None
    by_me: bool
    is_open: bool


class Sample(NamedTuple):
    kind: str
    ratio: float | None


class Model(NamedTuple):
    ask_cuts: tuple[float, ...]
    trend_cuts: tuple[float, ...]
    cells: dict[Key, tuple[int, Curve]]


class Forecast(NamedTuple):
    player: str
    close: str
    ask: float
    mins: float
    bids: int
    rival: int
    mine: bool
    trend: float | None
    cell: str
    n: int
    thin: bool
    curve: Curve


def sample(look: Look) -> Sample:
    if look.by_me and look.won is not None:
        return Sample("me", look.won / look.ask)
    if look.won is None:
        return Sample("none", None)
    return Sample("rival", look.won / look.ask)


def curve(samples: Sequence[Sample], floor: float = 1.0) -> Curve:
    out: list[tuple[float, float]] = []
    top = 0.0
    for r in RATIOS:
        num = den = 0
        for s in samples:
            assert s.kind == "none" or s.ratio is not None
            if s.kind == "me":
                if s.ratio is not None and s.ratio <= r + EPS:
                    num += 1
                    den += 1
            else:
                den += 1
                if s.kind == "none":
                    num += r >= floor - EPS
                elif s.ratio is not None:
                    num += s.ratio <= r + EPS
        top = max(top, num / den if den else top)
        out.append((r, top))
    return tuple(out)


def quantiles(xs: Sequence[float], levels: Sequence[float]) -> tuple[float, ...]:
    ordered = sorted(xs)
    if not ordered:
        return ()
    return tuple(ordered[min(int(q * len(ordered)), len(ordered) - 1)] for q in levels)


def keys(cuts: tuple[Sequence[float], Sequence[float]], x: Look) -> list[Key]:
    ask_cuts, trend_cuts = cuts
    r = min(x.rival, 1)
    a = bisect_right(ask_cuts, x.ask)
    t = bisect_right(trend_cuts, x.trend) if x.trend is not None else len(trend_cuts) // 2
    return [(r, a, t), (r, a), (r,), ()]


def fit(ls: Sequence[Look]) -> Model:
    closed = sorted((x for x in ls if not x.is_open), key=lambda x: x.look)
    last: dict[tuple[str, str], float] = {}
    for x in closed:
        last[(x.player, x.close)] = x.ask
    ask_cuts = quantiles(list(last.values()), ASK_LEVELS)
    floor = min([1.0] + [s.ratio for s in map(sample, closed) if s.ratio is not None])
    trend_cuts = quantiles([x.trend for x in closed if x.trend is not None], TREND_LEVELS)
    held: dict[Key, dict[tuple[str, str], Sample]] = defaultdict(dict)
    for x in closed:
        s = sample(x)
        for k in keys((ask_cuts, trend_cuts), x):
            held[k][(x.player, x.close)] = s
    return Model(ask_cuts, trend_cuts, {k: (len(v), curve(list(v.values()), floor)) for k, v in held.items()})


def predict(model: Model, x: Look) -> Forecast:
    chain = keys((model.ask_cuts, model.trend_cuts), x)
    chosen = next((k for k in chain if model.cells.get(k, (0, ()))[0] >= pricing.AUCTION_MIN), None)
    key = chosen if chosen is not None else ()
    n, cv = model.cells.get(key, (0, tuple((r, 1.0 if r >= 1.0 - EPS else 0.0) for r in RATIOS)))
    return Forecast(x.player, x.close, x.ask, x.mins, x.bids, x.rival, x.mine, x.trend, "/".join(map(str, key)) or "all", n,
                    chosen is None, cv)


def forecast(model: Model, ls: Sequence[Look]) -> list[Forecast]:
    newest: dict[tuple[str, str], Look] = {}
    for x in sorted((x for x in ls if x.is_open), key=lambda x: x.look):
        newest[(x.player, x.close)] = x
    return [predict(model, x) for x in newest.values()]


def chance(cv: Curve, ratio: float) -> float:
    if ratio <= cv[0][0]:
        return cv[0][1]
    if ratio >= cv[-1][0]:
        return cv[-1][1]
    i = bisect_right([r for r, _ in cv], ratio)
    (r0, p0), (r1, p1) = cv[i - 1], cv[i]
    return p0 + (p1 - p0) * (ratio - r0) / (r1 - r0)


def bid_for(cv: Curve, target: float) -> tuple[float, float]:
    return next(((r, p) for r, p in cv if r >= 1.0 - EPS and p >= target - EPS), cv[-1])


def looks(db: Any) -> list[Look]:
    series: dict[str, tuple[list[str], list[float]]] = defaultdict(lambda: ([], []))
    for player, day, trend in db.execute("SELECT ff_id, day, trend FROM price_trend_daily ORDER BY ff_id, day"):
        series[player][0].append(day)
        series[player][1].append(trend)

    def trend_at(player: str, look: str) -> float | None:
        days, values = series.get(player, ([], []))
        i = bisect_right(days, look[:10]) - 1
        return values[i] if i >= 0 else None

    return [Look(p, c, lk, m, a, b, bool(mi), rv, trend_at(p, lk), mb, w, bool(bm), bool(o)) for p, c, lk, m, a, b, mi, rv, mb, w, bm, o in db.execute(
        "SELECT player, close, look, mins, ask, bids, mine, rival, my_bid, won, by_me, is_open"
        " FROM auction_looks ORDER BY look")]


def write(db: Any, cut: str) -> None:
    ls = looks(db)
    for f in forecast(fit(ls), ls):
        db.execute("INSERT INTO seen_auction VALUES (?, variant(), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   (cut, f.player, f.ask, f.close, f.mins, f.bids, f.rival, f.mine, f.trend, f.cell, f.n, f.thin))
        db.executemany("INSERT INTO seen_curve VALUES (?, variant(), ?, ?, ?)", [(cut, f.player, r, p) for r, p in f.curve])


class Point(NamedTuple):
    scope: tuple[str, ...]
    stated: float
    pooled: float
    won: bool


def observed(s: Sample, ratio: float) -> bool | None:
    if s.kind == "none":
        return ratio >= 1.0 - EPS
    assert s.ratio is not None
    if s.kind == "me":
        return True if s.ratio <= ratio + EPS else None
    return s.ratio <= ratio + EPS


def scopes(big: bool, rival: int, mine: bool) -> tuple[str, ...]:
    return ("all", "big" if big else "small", "rival" if rival else "norival") + (("ours",) if mine else ())


class Record(NamedTuple):
    player: str
    close: str
    ask: float
    rival: int
    mine: bool
    big: bool
    my_bid: float | None
    sample: Sample
    curve: Curve
    pooled: Curve


def backtest_records(ls: Sequence[Look], blocks: int = 8) -> list[Record]:
    by: dict[tuple[str, str], list[Look]] = defaultdict(list)
    for x in ls:
        if not x.is_open:
            by[(x.player, x.close)].append(x)
    days = sorted({k[1][:10] for k in by})
    if len(days) < 4:
        return []
    start = max(2, len(days) * 3 // 10)
    edges = sorted({start + (len(days) - start) * i // blocks for i in range(blocks + 1)})
    out: list[Record] = []
    for lo, hi in zip(edges, edges[1:]):
        first, until = days[lo], days[hi] if hi < len(days) else "9999"
        model = fit([x for k, v in by.items() if k[1][:10] < first for x in v])
        pooled = model.cells.get((), (0, ()))[1]
        if not pooled:
            continue
        for k, v in by.items():
            if not first <= k[1][:10] < until:
                continue
            d = max((x for x in v if x.mins >= DECISION_MINS), key=lambda x: x.look, default=None)
            if d is None:
                continue
            big = bool(model.ask_cuts) and d.ask >= model.ask_cuts[-1]
            out.append(Record(d.player, d.close, d.ask, d.rival, d.mine, big, d.my_bid, sample(max(v, key=lambda x: x.look)),
                              predict(model, d).curve, pooled))
    return out


def points(records: Sequence[Record]) -> list[Point]:
    out: list[Point] = []
    for r in records:
        for ratio in SCORE_RATIOS:
            w = observed(r.sample, ratio)
            if w is not None:
                out.append(Point(scopes(r.big, r.rival, r.mine), chance(r.curve, ratio), chance(r.pooled, ratio), w))
        if r.mine and r.my_bid and r.sample.kind != "none":
            ratio = r.my_bid / r.ask
            out.append(Point(("ours",), chance(r.curve, ratio), chance(r.pooled, ratio), r.sample.kind == "me"))
    return out


def backtest(ls: Sequence[Look], blocks: int = 8) -> list[Point]:
    return points(backtest_records(ls, blocks))


def stored_points(db: Any) -> list[Point]:
    cuts: dict[tuple[str, str], tuple[str, float, int, bool]] = {}
    for cut, player, close, ask, rival, mine, mins in db.execute(
            "SELECT a.cut, a.player, a.close, a.ask, a.rival, a.mine, a.mins FROM seen_auction a"
            " JOIN auction_results r ON r.player = a.player AND r.close = a.close AND r.seller = 'marketPlayerLeague'"
            " WHERE a.variant = variant() AND a.mins >= ? ORDER BY a.cut", (DECISION_MINS,)):
        cuts[(player, close)] = (cut, ask, rival, bool(mine))
    out: list[Point] = []
    for (player, close), (cut, ask, rival, mine) in cuts.items():
        cv = tuple((r, p) for r, p in db.execute(
            "SELECT ratio, p FROM seen_curve WHERE cut = ? AND variant = variant() AND player = ? ORDER BY ratio", (cut, player)))
        row = db.execute("SELECT won, by_me FROM auction_results WHERE player = ? AND close = ?", (player, close)).fetchone()
        if not cv or row is None:
            continue
        s = sample(Look(player, close, cut, 0.0, ask, 0, mine, rival, None, None, row[0], bool(row[1]), False))
        for r in SCORE_RATIOS:
            w = observed(s, r)
            if w is not None:
                out.append(Point(scopes(False, rival, mine), chance(cv, r), chance(cv, r), w))
    return out


def calibration(points: Sequence[Point], prefix: str) -> list[dict[str, Any]]:
    out = []
    for scope in sorted({s for p in points for s in p.scope}):
        ps = [p for p in points if scope in p.scope]
        for lo, hi in BINS:
            b = [p for p in ps if lo <= p.stated < hi]
            if not b:
                continue
            stated, obs = sum(p.stated for p in b) / len(b), sum(p.won for p in b) / len(b)
            gap = abs(obs - stated) > 2 * sqrt(max(stated * (1 - stated), 0.01) / len(b)) + DRIFT_SLACK
            out.append({"scope": prefix + scope, "bin": "%.2f-%.2f" % (lo, min(hi, 1.0)), "n": len(b), "stated": stated,
                        "observed": obs, "drift": int(len(b) >= DRIFT_MIN and gap)})
    return out


def briers(points: Sequence[Point], prefix: str) -> list[dict[str, Any]]:
    out = []
    for scope in sorted({s for p in points for s in p.scope}):
        ps = [p for p in points if scope in p.scope]
        out.append({"scope": prefix + scope, "n": len(ps), "model": sum((p.stated - p.won) ** 2 for p in ps) / len(ps),
                    "pooled": sum((p.pooled - p.won) ** 2 for p in ps) / len(ps)})
    return out


def _selftest() -> None:
    import random

    flat = tuple((r, min(1.0, max(0.0, (r - 0.95) / 0.5))) for r in RATIOS)
    assert chance(flat, 0.5) == 0.0 and chance(flat, 2.0) == 1.0 and abs(chance(flat, 1.0) - 0.1) < 1e-9 \
        and abs(chance(flat, 1.005) - 0.11) < 1e-9, "the chance is read off the curve, linear between its points"
    assert bid_for(flat, 0.5) == (1.2, 0.5) and bid_for(flat, 2.0) == flat[-1], "the least bid that reaches the target; else the likeliest"

    c = curve([Sample("none", None)] * 3 + [Sample("rival", 1.1), Sample("rival", 0.98), Sample("me", 1.05), Sample("me", 0.99)])
    at = dict(c)
    assert abs(at[0.97] - 0.0) < 1e-9, "unsold wins from the lowest price any sale cleared at; nothing below it"
    at = dict(curve([Sample("none", None)] * 3 + [Sample("rival", 1.1), Sample("rival", 0.98), Sample("me", 1.05), Sample("me", 0.99)], 0.98))
    assert abs(at[0.97]) < 1e-9 and abs(at[0.98] - 4 / 5) < 1e-9 and abs(at[1.0] - 5 / 6) < 1e-9 and abs(at[1.05] - 6 / 7) < 1e-9, \
        "a rival who paid under the ask counts below it; our win counts from its bid up, and not before"
    assert all(c[i][1] <= c[i + 1][1] for i in range(len(c) - 1)), "monotone"
    assert curve([Sample("none", None)] * 5)[-1][1] == 1.0

    rng = random.Random(1)
    ls: list[Look] = []
    for i in range(240):
        rival = int(i % 4 == 0)
        big = i % 3 == 0
        ask = 40e6 if big else 2e6
        sold = rival or (big and rng.random() < 0.25)
        won = ask * (1.1 if rival else 1.02) if sold else None
        for m in (600.0, 39.0):
            ls.append(Look("p%d" % i, "2026-10-%02dT22:24:00+02:00" % (1 + i // 10), "2026-10-%02dT%04dZ" % (1 + i // 10, 2000 + (m == 39) * 45),
                           m, ask, rival, False, rival, float(i % 7), None, won, False, False))
    model = fit(ls)
    quiet = predict(model, Look("n", "c", "l", 39.0, 2e6, 0, False, 0, 3.0, None, None, False, True))
    rivalry = predict(model, Look("r", "c", "l", 39.0, 2e6, 1, False, 1, 3.0, None, None, False, True))
    assert dict(quiet.curve)[1.0] > 0.95 and dict(rivalry.curve)[1.0] < 0.05 and dict(rivalry.curve)[1.1] > 0.95, \
        "a rival bid seen moves the chance at the ask from sure to none"
    assert not quiet.thin and quiet.n >= pricing.AUCTION_MIN
    thin = predict(fit(ls[:20]), Look("n", "c", "l", 39.0, 2e6, 0, False, 0, 3.0, None, None, False, True))
    assert thin.thin and thin.cell == "all", "too few listings: the pooled curve, flagged thin"
    pts = backtest(ls)
    assert pts and all(0.0 <= p.stated <= 1.0 for p in pts)
    cal = calibration([Point(("all",), 0.97, 0.9, i < 5) for i in range(40)], "t:")
    assert cal and cal[0]["drift"] == 1, "stated 0.97, observed 0.125 over 40: drift"
    assert calibration([Point(("all",), 0.97, 0.9, True)] * 10, "t:")[0]["drift"] == 0, "too few to call it drift"
    print("ffcore.auction self-test OK")


if __name__ == "__main__":
    _selftest()
