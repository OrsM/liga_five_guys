from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import defaultdict
from math import exp, sqrt
from typing import Any, Callable, Mapping, NamedTuple, Sequence

from ffcore import auction_facts as af
from ffcore import current, ledger, price_fold, pricing
from ffcore import observations as ob
from ffcore import state as st
from ffcore.season_facts import julian_day, stamp_jd

__all__ = ["Look", "Sample", "Model", "Forecast", "RATIOS", "sample", "fit", "predict", "forecast", "chance", "momentum_class", "RISER",
           "looks_of", "open_looks_of", "Record", "backtest_records", "points", "backtest", "live_points", "calibration", "briers"]

RATIOS = (0.5, 0.6, 0.7, 0.8, 0.9) + tuple(round(0.95 + 0.01 * i, 2) for i in range(51))
DECISION_MINS = 39.0
SCORE_RATIOS = (1.0, 1.05, 1.10, 1.15)
BINS = ((0.0, 0.5), (0.5, 0.8), (0.8, 0.9), (0.9, 0.95), (0.95, 0.99), (0.99, 1.01))
DRIFT_MIN = 20
DRIFT_SLACK = 0.02
EPS = 1e-9
ASK_CUTS = (2e6, 10e6, 30e6)
MOMENTUM_CUTS = (0.0, 0.04, 0.08)
RISER = len(MOMENTUM_CUTS)
ANY = -1
POOLED: Key = (ANY, ANY, ANY)
DEMAND_MIN = 15
SPREAD = 0.5
RECENCY_DAYS = 40.0
EM_ROUNDS = 20

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
    withdrawn: bool = False
    momentum: float | None = None


class Sample(NamedTuple):
    kind: str
    ratio: float | None


class Model(NamedTuple):
    """The win chance W(b | x) = (1 - q) + q F(b): `demand` holds q, the chance some rival bids, by Key (visible, momentum class,
    ask band) and its prefixes, each with the listings it rests on; `top` holds F, the distribution function of the top rival
    bid over the ask given one bids, on RATIOS, by Key (momentum class,) and (); floor is the lowest ratio a sale cleared at,
    from which an unsold listing is won."""
    ask_cuts: tuple[float, ...]
    momentum_cuts: tuple[float, ...]
    floor: float
    demand: dict[Key, tuple[int, float]]
    top: dict[Key, tuple[int, Curve]]


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
    momentum: float | None = None


def at_decision(mins: float) -> bool:
    """Whether a look with this many minutes to close is at or before the look the decision is taken at, DECISION_MINS."""
    return mins >= DECISION_MINS


def sample(look: Look) -> Sample:
    if look.by_me and look.won is not None:
        return Sample("me", look.won / look.ask)
    if look.won is None:
        return Sample("none", None)
    return Sample("rival", look.won / look.ask)


def momentum_class(cuts: Sequence[float], momentum: float | None) -> int:
    """The class of a momentum: the number of cuts it reaches (RISER with the default cuts when it is the top); an unknown one
    is in the class of a momentum of 0."""
    return bisect_right(cuts, 0.0 if momentum is None else momentum)


def demand_keys(cuts: tuple[Sequence[float], Sequence[float]], x: Look) -> list[Key]:
    """The Keys of the cells of demand that x belongs to, the finest first: (visible, momentum class, ask band), then without the
    band, without the momentum class, with only the visibility, and with nothing (ANY stands for a dropped part)."""
    ask_cuts, momentum_cuts = cuts
    v, m, a = min(x.rival, 1), momentum_class(momentum_cuts, x.momentum), bisect_right(ask_cuts, x.ask)
    return [(v, m, a), (v, m, ANY), (v, ANY, ANY), POOLED]


def top_keys(model_cuts: Sequence[float], x: Look) -> list[Key]:
    """The Keys of the distributions of the top rival bid that x belongs to, the finest first."""
    return [(momentum_class(model_cuts, x.momentum),), ()]


def ratio_index(ratio: float) -> int:
    """The index in RATIOS of the first point at or over ratio; len(RATIOS) for a ratio above them all."""
    return bisect_left(RATIOS, ratio - EPS)


def recency(closes: Sequence[str]) -> list[float]:
    """The weight exp(-age / RECENCY_DAYS) of each close, the age in days behind the newest of them."""
    days = [julian_day(c) or 0.0 for c in closes]
    newest = max(days, default=0.0)
    return [exp(-(newest - d) / RECENCY_DAYS) for d in days]


def pick(size: Callable[[Key], int], keys: Sequence[Key], least: int) -> Key:
    """The first of keys whose cell rests on at least `least` listings (size of the cell), else the last."""
    return next((k for k in keys if size(k) >= least), keys[-1])


def cumulative(mass: Sequence[float]) -> Curve:
    """The distribution function of mass (one weight per RATIOS point and one above the last): a point per ratio with the
    share of the mass at or under it, so a curve ends under 1 when mass lies above the last ratio."""
    total = sum(mass)
    run, out = 0.0, []
    for r, m in zip(RATIOS, mass):
        run += m
        out.append((r, run / total if total else 0.0))
    return tuple(out)


class Case(NamedTuple):
    """One closed listing for fit: the cells it falls in (demand, top), the weight of its age, its outcome and whether a rival
    bid was seen at its decision look."""
    demand: Key
    top: Key
    weight: float
    sample: Sample
    seen: bool


def fit(ls: Sequence[Look]) -> Model:
    """The Model of the closed looks of ls with at_decision mins, those of a listing with a withdrawn bid of ours left out; one
    listing (player, close) is the features of its last such look and its outcome. Each listing weighs exp(-age / RECENCY_DAYS)
    (age behind the newest close). q (demand) and F (top) are fitted by EM_ROUNDS rounds of EM, from F spread over the ratios
    at or over the lowest sale by SPREAD, to the likelihood in which: a rival win at R is
    q f(R), unsold 1 - q, our win at b (1 - q) + q F(b) (q F(b) when a rival bid was seen). Our wins are left-censored, never
    counted as won above b. A cell rests on the listings that fall in it; the cuts are ASK_CUTS and MOMENTUM_CUTS."""
    closed = sorted((x for x in ls if not x.is_open and not x.withdrawn and at_decision(x.mins)), key=lambda x: x.look)
    newest: dict[tuple[str, str], Look] = {}
    for x in closed:
        newest[(x.player, x.close)] = x
    looks = list(newest.values())
    weights = recency([x.close for x in looks])
    cuts = (ASK_CUTS, MOMENTUM_CUTS)
    chains = [(demand_keys(cuts, x), top_keys(MOMENTUM_CUTS, x)) for x in looks]
    asked: dict[Key, int] = defaultdict(int)
    bid_on: dict[Key, int] = defaultdict(int)
    for (d, t), x in zip(chains, looks):
        for k in d:
            asked[k] += 1
        for k in t if sample(x).kind != "none" else ():
            bid_on[k] += 1
    cases = [Case(pick(lambda k: asked[k], d, DEMAND_MIN), pick(lambda k: bid_on[k], t, pricing.AUCTION_MIN), w, sample(x), x.rival > 0)
             for (d, t), x, w in zip(chains, looks, weights)]
    floor = min([1.0] + [c.sample.ratio for c in cases if c.sample.ratio is not None])
    size = len(RATIOS) + 1
    open_to = [1.0 if r >= floor - EPS else 0.0 for r in RATIOS] + [1.0]
    mass: dict[Key, list[float]] = defaultdict(lambda: [SPREAD * o / sum(open_to) for o in open_to])
    for c, (_, t) in zip(cases, chains):
        if c.sample.kind == "rival" and c.sample.ratio is not None:
            for k in t:
                mass[k][ratio_index(c.sample.ratio)] += c.weight
    q = {k: 1.0 for k in asked}
    for _ in range(EM_ROUNDS):
        num: dict[Key, float] = defaultdict(float)
        den: dict[Key, float] = defaultdict(float)
        grown: dict[Key, list[float]] = defaultdict(lambda: [0.0] * size)
        for c, (d, t) in zip(cases, chains):
            exists, where = 0.0, [0.0] * size
            if c.sample.ratio is not None and sum(mass[c.top]) > 0:
                f = mass[c.top]
                if c.sample.kind == "rival":
                    exists, where[ratio_index(c.sample.ratio)] = 1.0, 1.0
                else:
                    upto = min(ratio_index(c.sample.ratio), size - 1)
                    below = sum(f[:upto + 1])
                    share = below / sum(f)
                    p = q[c.demand]
                    exists = 1.0 if c.seen else p * share / ((1 - p) + p * share) if (1 - p) + p * share > 0 else 0.0
                    where[:upto + 1] = [m / below if below else 0.0 for m in f[:upto + 1]]
            for k in d:
                num[k] += c.weight * exists
                den[k] += c.weight
            if exists:
                for k in t:
                    for i, w in enumerate(where):
                        grown[k][i] += c.weight * exists * w
        q = {k: num[k] / den[k] if den[k] else 1.0 for k in q}
        mass = grown if grown else mass
    top = {k: (bid_on[k], cumulative(m)) for k, m in mass.items() if sum(m) > 0}
    return Model(ASK_CUTS, MOMENTUM_CUTS, floor, {k: (asked[k], p) for k, p in q.items()}, top)


def curve_of(model: Model, demand_key: Key, top_key: Key) -> Curve:
    """The win chance (1 - q) + q F(r) at each ratio of RATIOS (the first term only from the model's floor), q of the cell
    demand_key and F of the cell top_key; an unsold-only curve (sure from the floor) when the model has no such cell."""
    q = model.demand.get(demand_key, (0, 0.0))[1]
    f = model.top.get(top_key, (0, ()))[1]
    return tuple((r, (1 - q) * (r >= model.floor - EPS) + q * (f[i][1] if f else 0.0)) for i, r in enumerate(RATIOS))


def predict(model: Model, x: Look) -> Forecast:
    """The Forecast of x: the curve of the finest cell of demand and of top with at least pricing.AUCTION_MIN listings (the
    pooled one when none has, thin), n and cell those of demand."""
    chain = demand_keys((model.ask_cuts, model.momentum_cuts), x)
    key = pick(lambda k: model.demand.get(k, (0, 0.0))[0], chain, DEMAND_MIN)
    top = pick(lambda k: model.top.get(k, (0, ()))[0], top_keys(model.momentum_cuts, x), pricing.AUCTION_MIN)
    chosen = model.demand.get(key, (0, 0.0))[0] >= DEMAND_MIN
    cell = "/".join("*" if k == ANY else str(k) for k in key) if key != POOLED else "all"
    return Forecast(x.player, x.close, x.ask, x.mins, x.bids, x.rival, x.mine, x.trend, cell,
                    model.demand.get(key, (0, 0.0))[0], not chosen, curve_of(model, key, top), x.momentum)


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


def looks_of(world: st.World, identity: current.Identity, league: ledger.League) -> list[Look]:
    """The looks of auction_facts.auction_looks sorted by look, each with the price trend of its day or the day before
    (trend_is_last_day_on_or_before_look; None before the player's first day) and the momentum of the player at the look
    (price_fold.price_change_at), bools as bool."""
    state = price_fold.price_state(world)
    trend_at, change_at = price_fold.price_trend_at(state), price_fold.price_change_at(state)
    rows = [Look(r.player, r.close, r.look, r.mins, r.ask, r.bids, r.mine, r.rival, trend_at(r.player, r.look), r.my_bid,
                 r.won, bool(r.by_me), r.is_open, r.withdrawn, change_at(r.player, r.look)) for r in af.auction_looks(world, identity, league)]
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
    state = price_fold.price_state(world)
    trend_at, change_at = price_fold.price_trend_at(state), price_fold.price_change_at(state)
    out = []
    for (player, close), (row, mins) in newest.items():
        mine, bids = row.bid_id != "", row.bids or 0
        out.append(Look(player, close, row.observed_at, mins, row.sale_price or 0.0, bids, mine, bids - mine,
                        trend_at(player, row.observed_at), row.bid_money, None, False, True, False, change_at(player, row.observed_at)))
    return sorted(out, key=lambda x: x.look)


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


def riser_scope(riser: bool) -> str:
    """The scope name of the momentum class: riser or rest."""
    return "riser" if riser else "rest"


def scopes(big: bool, rival: int, mine: bool, riser: bool | None = None) -> tuple[str, ...]:
    """The scopes a point is read in: all, big or small ask, rival bid seen or not, ours when we bid, and (when known) riser or
    rest by the momentum class."""
    return ("all", "big" if big else "small", "rival" if rival else "norival") + (("ours",) if mine else ()) + (
        () if riser is None else (riser_scope(riser),))


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
    riser: bool = False


def backtest_records(ls: Sequence[Look], blocks: int = 8) -> list[Record]:
    by: dict[tuple[str, str], list[Look]] = defaultdict(list)
    for x in ls:
        if not x.is_open and not x.withdrawn:
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
        if not model.demand:
            continue
        pooled = curve_of(model, POOLED, ())
        for k, v in by.items():
            if not first <= k[1][:10] < until:
                continue
            d = max((x for x in v if at_decision(x.mins)), key=lambda x: x.look, default=None)
            if d is None:
                continue
            big = d.ask >= model.ask_cuts[-1]
            riser = momentum_class(model.momentum_cuts, d.momentum) == RISER
            out.append(Record(d.player, d.close, d.ask, d.rival, d.mine, big, d.my_bid, sample(max(v, key=lambda x: x.look)),
                              predict(model, d).curve, pooled, riser))
    return out


def points(records: Sequence[Record]) -> list[Point]:
    out: list[Point] = []
    for r in records:
        for ratio in SCORE_RATIOS:
            w = observed(r.sample, ratio)
            if w is not None:
                out.append(Point(scopes(r.big, r.rival, r.mine, r.riser), chance(r.curve, ratio), chance(r.pooled, ratio), w))
        if r.mine and r.my_bid and r.sample.kind != "none":
            ratio = r.my_bid / r.ask
            out.append(Point(("ours", riser_scope(r.riser)), chance(r.curve, ratio), chance(r.pooled, ratio), r.sample.kind == "me"))
    return out


def backtest(ls: Sequence[Look], blocks: int = 8) -> list[Point]:
    return points(backtest_records(ls, blocks))


def live_points(forecasts: Mapping[str, Sequence[Forecast]], results: Sequence[af.AuctionResult]) -> list[Point]:
    """The Points of the stated chance of each decision against what happened. forecasts are each cut's Forecasts; of
    a listing (player, close) with a result of the league's own sale, the last cut (ascending) whose forecast has mins at
    least DECISION_MINS gives the stated curve (rule live_point_is_last_look_before_decision_mins), scored at the
    SCORE_RATIOS against that result's sample; a listing with no such result or no such cut gives none."""
    sold = {(r.player, r.close): r for r in results if r.seller == af.LEAGUE_SELLER}
    last: dict[tuple[str, str], tuple[str, Forecast]] = {}
    for cut in sorted(forecasts):
        for f in forecasts[cut]:
            if at_decision(f.mins) and (f.player, f.close) in sold:
                last[(f.player, f.close)] = (cut, f)
    out: list[Point] = []
    for key, (cut, f) in last.items():
        r = sold[key]
        s = sample(Look(f.player, f.close, cut, 0.0, f.ask, 0, f.mine, f.rival, None, None, r.won, bool(r.by_me), False))
        for ratio in SCORE_RATIOS:
            w = observed(s, ratio)
            if w is not None:
                out.append(Point(scopes(False, f.rival, f.mine, momentum_class(MOMENTUM_CUTS, f.momentum) == RISER), chance(f.curve, ratio), chance(f.curve, ratio), w))
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
    flat = tuple((r, min(1.0, max(0.0, (r - 0.95) / 0.5))) for r in RATIOS)
    assert chance(flat, 0.5) == 0.0 and chance(flat, 2.0) == 1.0 and abs(chance(flat, 1.0) - 0.1) < 1e-9 \
        and abs(chance(flat, 1.005) - 0.11) < 1e-9, "the chance is read off the curve, linear between its points"

    assert [momentum_class(MOMENTUM_CUTS, m) for m in (-0.5, 0.0, 0.039, 0.04, 0.079, 0.08, 2.0)] == [0, 1, 1, 2, 2, RISER, RISER] \
        and momentum_class(MOMENTUM_CUTS, None) == 1, "a momentum is in the class of the cuts it reaches; an unknown one is in the first cut's"

    def listing(i: int, close: str, momentum: float, seen: int, won: float | None, by_me: bool = False) -> list[Look]:
        return [Look("p%d" % i, close, "%sT%04dZ" % (close[:10], 2000 + (m == 39) * 45), m, 2e6, seen, False, seen, None, None, won, by_me,
                     False, False, momentum) for m in (600.0, 39.0)]

    def regime(day: str, riser_bid: float, n: int = 120, ours: int = 0) -> list[Look]:
        out: list[Look] = []
        for i in range(n):
            close = "2026-%s-%02dT22:24:00+02:00" % (day, 1 + i % 20)
            kind = i % 4
            if kind == 0:
                out += listing(i, close, 0.0, 1, 2e6 * 1.1)
            elif kind == 1:
                out += listing(i, close, 0.12, 0, 2e6 * riser_bid)
            else:
                out += listing(i, close, 0.0, 0, None)
        for i in range(ours):
            out += listing(n + i, "2026-%s-%02dT21:00:00+02:00" % (day, 1 + i % 20), 0.12, 0, 2e6 * 1.05, True)
        return out
    ls = regime("10", 1.1)

    def look_of(momentum: float, seen: int) -> Look:
        return Look("n", "c", "l", 39.0, 2e6, seen, False, seen, None, None, None, False, True, False, momentum)
    model = fit(ls)
    quiet, riser, rivalry = (predict(model, look_of(0.0, 0)), predict(model, look_of(0.12, 0)), predict(model, look_of(0.0, 1)))
    assert dict(quiet.curve)[1.0] > 0.95 and dict(rivalry.curve)[1.0] < 0.05 and dict(rivalry.curve)[1.1] > 0.95, \
        "a rival bid seen moves the chance at the ask from sure to none"
    assert dict(riser.curve)[1.0] < 0.1 < 0.9 < dict(riser.curve)[1.1], "a riser draws a rival bid however few rivals are seen: the chance at the ask is low"
    assert all(0.0 <= a[1] <= b[1] <= 1.0 for f in (quiet, riser, rivalry) for a, b in zip(f.curve, f.curve[1:])), \
        "a chance is between 0 and 1 and never falls as the bid rises"
    assert not quiet.thin and quiet.n >= DEMAND_MIN
    thin = predict(fit(ls[:20]), look_of(0.0, 0))
    assert thin.thin and thin.cell == "all", "too few listings: the pooled curve, flagged thin"
    late = [x._replace(mins=m, look=x.look + "z", ask=x.ask * 3, won=None) for x in ls for m in (20.0, 5.0)]
    assert fit(ls + late) == fit(ls) and fit(late + ls) == fit(ls), \
        "looks with fewer minutes than DECISION_MINS are stored but never change the fit"
    shifted = regime("09", 1.3) + regime("11", 1.02)
    assert dict(predict(fit(shifted), look_of(0.12, 0)).curve)[1.05] > 0.5 > dict(predict(fit(regime("09", 1.3)), look_of(0.12, 0)).curve)[1.05], \
        "a recent regime outweighs an older one: rivals who paid 1.02 lately, 1.3 before"
    won = regime("10", 1.1, ours=40)
    again = predict(fit(won), look_of(0.12, 0)).curve
    assert all(a[1] >= b[1] - 1e-9 for a, b in zip(again, riser.curve)) and dict(again)[1.05] > dict(riser.curve)[1.05], \
        "our wins at 1.05 are left-censored: a rival bid below 1.05, so they only raise the chance at and above it, never lower it"
    assert dict(again)[1.0] < 0.5, "our wins at 1.05 are not wins above the ask: the chance at the ask is not raised to sure"
    pts = backtest(ls)
    assert pts and all(0.0 <= p.stated <= 1.0 for p in pts) and {"riser", "rest"} <= {s for p in pts for s in p.scope}
    pulled = [x._replace(player="w" + x.player, withdrawn=True, won=None, ask=x.ask * 2, mine=True, my_bid=x.ask) for x in ls]
    assert fit(ls + pulled) == fit(ls) and backtest(ls + pulled) == pts, \
        "a listing with a withdrawn bid of ours is no sample: neither the fit nor the backtest sees it"
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
    assert [(p.scope, p.stated, p.won) for p in paid] == [(scopes(False, 1, False, False), 0.2, False)] * 2 + [(scopes(False, 1, False, False), 1.0, True)] * 2, \
        "live_point_is_last_look_before_decision_mins: the cut with 39 minutes, not 38 or the earlier ones; no league result, no point"
    assert [p.stated for p in live_points({"c1": [forecast_at(600.0, 0.9)]}, [result("a", 11.0)])] == [0.9] * 2 + [1.0] * 2
    assert all("riser" in p.scope and "rest" not in p.scope for p in live_points({"c1": [forecast_at(600.0, 0.9)._replace(momentum=0.2)]}, [result("a", 11.0)])), \
        "a live point is in the riser scope when the forecast's momentum is in the top class"
    assert live_points({}, [result("a", 11.0)]) == [] and live_points(staged, []) == [] and live_points({"c3": staged["c3"]}, [result("a", 11.0)]) == []
    assert live_points(dict(reversed(staged.items())), [result("a", 11.0)]) == live_points(staged, [result("a", 11.0)]), "cuts are taken in ascending order"
    unsold = live_points({"c1": [forecast_at(600.0, 0.9)]}, [result("a", None)])
    assert unsold and all(p.won == (r >= 1.0 - EPS) for p, r in zip(unsold, SCORE_RATIOS)), "an unsold listing is won at the ask and above"
    assert (model.ask_cuts, model.momentum_cuts) == (ASK_CUTS, MOMENTUM_CUTS) and all(
        k[1] <= RISER and k[2] <= len(ASK_CUTS) for k in model.demand), "the cells are banded by the fixed cuts"
    assert last_on_or_before("2026-08-11") is not None, "trend_is_last_day_on_or_before_look: the look's own day counts"
    print("ffcore.auction self-test OK")


if __name__ == "__main__":
    _selftest()
