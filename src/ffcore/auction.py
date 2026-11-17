from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from math import sqrt
from typing import Any, Mapping, NamedTuple, Sequence

from ffcore import auction_facts as af
from ffcore import current, ledger, price_fold, pricing
from ffcore import observations as ob
from ffcore import state as st
from ffcore.db import stamp_jd
from ffcore.season_facts import julian_day

__all__ = ["Look", "Sample", "Model", "Forecast", "RATIOS", "sample", "curve", "fit", "predict", "forecast", "chance", "bid_for",
           "looks", "looks_of", "open_looks_of", "FIXED_CUTS", "write", "Record", "backtest_records", "points", "backtest", "stored_points", "live_points", "calibration", "briers"]

RATIOS = (0.5, 0.6, 0.7, 0.8, 0.9) + tuple(round(0.95 + 0.01 * i, 2) for i in range(51))
ASK_LEVELS = (0.5, 0.75, 0.9)
TREND_LEVELS = (1 / 3, 2 / 3)
DECISION_MINS = 39.0
SCORE_RATIOS = (1.0, 1.05, 1.10, 1.15)
BINS = ((0.0, 0.5), (0.5, 0.8), (0.8, 0.9), (0.9, 0.95), (0.95, 0.99), (0.99, 1.01))
DRIFT_MIN = 20
DRIFT_SLACK = 0.02
EPS = 1e-9
ASK_CUTS = (2e6, 10e6, 30e6)
TREND_CUTS = (-10.0, 0.0)

Curve = tuple[tuple[float, float], ...]
Key = tuple[int, ...]
Cuts = tuple[tuple[float, ...], tuple[float, ...]]
FIXED_CUTS: Cuts = (ASK_CUTS, TREND_CUTS)


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


def fit(ls: Sequence[Look], cuts: Cuts | None = None) -> Model:
    """The Model of the closed looks of ls: the cells of keys over the ask and trend cuts, each the curve of the samples
    held there. cuts are the quantiles of the closed looks unless given, then used as they are."""
    closed = sorted((x for x in ls if not x.is_open), key=lambda x: x.look)
    last: dict[tuple[str, str], float] = {}
    for x in closed:
        last[(x.player, x.close)] = x.ask
    ask_cuts = quantiles(list(last.values()), ASK_LEVELS) if cuts is None else cuts[0]
    floor = min([1.0] + [s.ratio for s in map(sample, closed) if s.ratio is not None])
    trend_cuts = quantiles([x.trend for x in closed if x.trend is not None], TREND_LEVELS) if cuts is None else cuts[1]
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


def looks_of(world: st.World, identity: current.Identity, league: ledger.League) -> list[Look]:
    """The looks of auction_facts.auction_looks sorted by look, each with the price trend of its day or the day before
    (trend_is_last_day_on_or_before_look; None before the player's first day), bools as bool."""
    trend_at = price_fold.price_trend_at(price_fold.price_state(world))
    rows = [Look(r.player, r.close, r.look, r.mins, r.ask, r.bids, r.mine, r.rival, trend_at(r.player, r.look), r.my_bid,
                 r.won, bool(r.by_me), r.is_open) for r in af.auction_looks(world, identity, league)]
    return sorted(rows, key=lambda x: x.look)


def open_looks_of(world: st.World, identity: current.Identity, league: ledger.League) -> list[Look]:
    """The newest look of each auction still open at the cut, as looks_of gives it (same fields, trend included), sorted
    by look; no look is built for a closed auction. Open means the close is after the cut (and so has no outcome)."""
    cut = stamp_jd(world.cut)
    newest: dict[tuple[str, str], tuple[ob.ApiMarket, float]] = {}
    for row in st.fact(world, af.LOOKS):
        ends, seen = julian_day(row.expires_at), stamp_jd(row.observed_at)
        player = identity.app.get(row.player_id)
        if player is None or ends is None or seen is None or cut is None or ends <= cut:
            continue
        key = (player, row.expires_at)
        if key not in newest or newest[key][0].observed_at <= row.observed_at:
            newest[key] = (row, (ends - seen) * 1440.0)
    trend_at = price_fold.price_trend_at(price_fold.price_state(world))
    out = []
    for (player, close), (row, mins) in newest.items():
        mine, bids = row.bid_id != "", row.bids or 0
        out.append(Look(player, close, row.observed_at, mins, row.sale_price or 0.0, bids, mine, bids - mine,
                        trend_at(player, row.observed_at), row.bid_money, None, False, True))
    return sorted(out, key=lambda x: x.look)


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


def live_points(forecasts: Mapping[str, Sequence[Forecast]], results: Sequence[af.AuctionResult]) -> list[Point]:
    """The Points of the stated chance of each decision against what happened. forecasts are each cut's Forecasts; of
    a listing (player, close) with a result of the league's own sale, the last cut (ascending) whose forecast has mins at
    least DECISION_MINS gives the stated curve (rule live_point_is_last_look_before_decision_mins), scored at the
    SCORE_RATIOS against that result's sample; a listing with no such result or no such cut gives none."""
    sold = {(r.player, r.close): r for r in results if r.seller == af.LEAGUE_SELLER}
    last: dict[tuple[str, str], tuple[str, Forecast]] = {}
    for cut in sorted(forecasts):
        for f in forecasts[cut]:
            if f.mins >= DECISION_MINS and (f.player, f.close) in sold:
                last[(f.player, f.close)] = (cut, f)
    out: list[Point] = []
    for key, (cut, f) in last.items():
        r = sold[key]
        s = sample(Look(f.player, f.close, cut, 0.0, f.ask, 0, f.mine, f.rival, None, None, r.won, bool(r.by_me), False))
        for ratio in SCORE_RATIOS:
            w = observed(s, ratio)
            if w is not None:
                out.append(Point(scopes(False, f.rival, f.mine), chance(f.curve, ratio), chance(f.curve, ratio), w))
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

    mkt = [ob.Market(at, "", x, "a", "DEL", "RMA") for at, x in (
        ("2026-08-10T0800Z", 100.0), ("2026-08-11T0800Z", 105.0), ("2026-08-11T2000Z", 110.0),
        ("2026-08-12T2000Z", 0.0), ("2026-08-13T2000Z", 50.0), ("2026-08-14T0800Z", 60.0))]
    listed = [ob.ApiMarket(at, "laliga", None, 20.0, None, None, "a1", "m", "", "", "marketPlayerLeague", "", "",
                           "2026-08-20T20:00:00+00:00", "", "") for at in ("2026-08-09T1200Z", "2026-08-11T1200Z")]
    priced = st.world_at(ob.of({"market": mkt, "api_market": listed, "api_activity": [], "api_standings": []}),
                         "2026-08-14T0900Z", {n: st.FOLDS[n] for n in ("price", *af.AUCTION_FACTS)})
    daily = sorted((t.day, t.trend) for t in price_fold.price_trend_daily(price_fold.price_state(priced)))

    def last_on_or_before(day: str) -> float | None:
        return max(((d, v) for d, v in daily if d <= day), default=(None, None))[1]
    got = {x.look: x.trend for x in looks_of(priced, current.Identity({}, {"a1": "a"}), ledger.League("ann", 100.0))}
    assert got == {"2026-08-09T1200Z": last_on_or_before("2026-08-09"), "2026-08-11T1200Z": last_on_or_before("2026-08-11")}
    ident, lg = current.Identity({}, {"a1": "a"}), ledger.League("ann", 100.0)
    newest = {(x.player, x.close): x for x in looks_of(priced, ident, lg) if x.is_open}
    assert open_looks_of(priced, ident, lg) == sorted(newest.values(), key=lambda x: x.look) and newest, \
        "open_looks_of: the newest look of each open auction, as looks_of gives it"
    assert open_looks_of(st.world_at(ob.of({"market": mkt, "api_market": listed, "api_activity": [], "api_standings": []}),
                                      "2026-08-25T0900Z", {n: st.FOLDS[n] for n in ("price", *af.AUCTION_FACTS)}), ident, lg) == [], \
        "a close at or before the cut is no open auction"
    def forecast_at(mins: float, chance_at_ask: float, player: str = "a") -> Forecast:
        cv = tuple((r, chance_at_ask if r < 1.1 else 1.0) for r in RATIOS)
        return Forecast(player, "c1", 10.0, mins, 1, 1, False, None, "all", 50, False, cv)

    def result(player: str, won: float | None, seller: str = af.LEAGUE_SELLER, by_me: bool | None = False) -> af.AuctionResult:
        return af.AuctionResult(player, "c1", "c1", 10.0, seller, won, by_me, None)
    staged = {"c1": [forecast_at(600.0, 0.9), forecast_at(50.0, 0.8, "z")], "c2": [forecast_at(39.0, 0.2)], "c3": [forecast_at(38.0, 0.7)],
              "c0": [forecast_at(120.0, 0.6)]}
    paid = live_points(staged, [result("a", 11.0), result("z", None, af.LISTING_SELLERS[1]), result("n", 11.0)])
    assert [(p.scope, p.stated, p.won) for p in paid] == [(scopes(False, 1, False), 0.2, False)] * 2 + [(scopes(False, 1, False), 1.0, True)] * 2, \
        "live_point_is_last_look_before_decision_mins: the cut with 39 minutes, not 38 or the earlier ones; no league result, no point"
    assert [p.stated for p in live_points({"c1": [forecast_at(600.0, 0.9)]}, [result("a", 11.0)])] == [0.9] * 2 + [1.0] * 2
    assert live_points({}, [result("a", 11.0)]) == [] and live_points(staged, []) == [] and live_points({"c3": staged["c3"]}, [result("a", 11.0)]) == []
    assert live_points(dict(reversed(staged.items())), [result("a", 11.0)]) == live_points(staged, [result("a", 11.0)]), "cuts are taken in ascending order"
    unsold = live_points({"c1": [forecast_at(600.0, 0.9)]}, [result("a", None)])
    assert unsold and all(p.won == (r >= 1.0 - EPS) for p, r in zip(unsold, SCORE_RATIOS)), "an unsold listing is won at the ask and above"
    fixed = fit(ls, FIXED_CUTS)
    assert (fixed.ask_cuts, fixed.trend_cuts) == FIXED_CUTS and all(k[1] <= len(ASK_CUTS) and k[2] <= len(TREND_CUTS) for k in fixed.cells if len(k) == 3), \
        "fit with cuts bands the looks by those cuts, whatever their quantiles are"
    assert last_on_or_before("2026-08-11") is not None, "trend_is_last_day_on_or_before_look: the look's own day counts"
    print("ffcore.auction self-test OK")


if __name__ == "__main__":
    _selftest()
