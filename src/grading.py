from __future__ import annotations

import datetime as dt
import os
import sys
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from contextlib import closing
from pathlib import Path
from typing import Any, NamedTuple, TypeVar

import decision
import salegrade
import worth
from board import context, later_sale, replayed
from ffcore import auction, current, league, ledger, loader, price_fold, season_facts, state
from ffcore import observations as ob
from ffcore.clock import run_now
from ffcore.db import connect, record, replaying, rows, set_look, set_now, this_run
from ffcore.forecast_store import forecast
from ffcore.jornadas import round_locks
from ffcore.parse import snapshot_stamp, stamp
from salegrade import HORIZONS

__all__ = ["backtest", "superseded", "view_table", "record_grades"]

T = TypeVar("T")

GRADE_AHEAD = 4  # jornadas past the lock each replayed forecast is scored on
RATE = 0.6
SALES_FROM = "2026-08-25"


def before_locks() -> dict[dt.datetime, int]:
    """A minute before each finished jornada's lock, oldest first, and the
    jornada: where every replay of a lock stands."""
    locks = round_locks()
    return {locks[j] - dt.timedelta(minutes=1): j for j in sorted(done_jornadas() & set(locks), key=locks.__getitem__)}


def done_jornadas() -> set[int]:
    """The jornadas every match of which has a score (jornada_calendar's done):
    the ones a forecast can be graded on."""
    return {j for (j,) in rows("SELECT jornada FROM jornada_calendar WHERE state = 'done'")}


def lock_cells(i: int, ahead: int, done: set[int]) -> list[dict[str, Any]]:
    """The forecast at t (the cell contract) for the lock of jornada i and, with ahead, the next ones, each one
    done: a row a player and jornada, his expected points, the chance he plays and whether he is flagged
    (player_status: injured, doubtful, suspended)."""
    flagged = {k for (k,) in rows("SELECT player FROM player_status WHERE status IS NOT NULL")}
    return [{"jornada": j, "player": k, "predicted": e, "plays": p, "flagged": int(k in flagged)}
            for j, k, e, p in rows("SELECT jornada, player, e, p FROM cell WHERE jornada BETWEEN ? AND ?",
                                   (i, i + ahead)) if j in done]


def backtest(ahead: int = 0) -> list[dict[str, Any]]:
    """The forecast at each past lock (forecast_store.forecast makes it, the cell
    contract is read): a row (lock, jornada, player) for the jornada it locks
    and, with ahead, the next ones too, each one done. Graded in SQL
    (grade_forecast)."""
    done = done_jornadas()
    out = []
    with replaying():
        for t, i in before_locks().items():
            set_now(t)
            set_look(moment=forecast())
            out += [{"from_j": i, **c} for c in lock_cells(i, ahead, done)]
    return out


def opportunity_grade(top: int = 5, span: int = 4) -> list[dict[str, Any]]:
    done = done_jornadas()
    actual = {(k, j): x for k, j, x in rows("SELECT player, jornada, sum(pts) FROM scored GROUP BY player, jornada")}
    out = []
    with replaying():
        for c in decision.seen_cuts():
            set_now(snapshot_stamp(c))
            at = replayed()
            if at is None:
                continue
            inp = at.inputs
            pos, cells = decision.cells_of(inp)
            got = decision.opportunity_rows(inp)
            band = [(c.player, c.pos, c.asking if c.asking is not None else c.value) for c in inp.universe if c.player in pos]
            for rank, r in enumerate([r for r in got if r["status"] == "market" and r["gain"] > 0][:top], 1):
                span_j = [j for j in range(r["from_j"], r["from_j"] + span) if j in done]
                if not span_j or r["replaces"] is None:
                    continue
                gone, k = r["replaces"], r["player"]
                lost = sum(actual.get((gone, j), 0.0) for j in span_j)
                price = r["asking"]
                peers = [b for b, p, pr in band if p == r["pos"] and b != k and pr and abs(pr / price - 1) <= 0.3]
                out.append({
                    "cut": c, "player": k, "rank": rank, "n_peers": len(peers), "jornadas": len(span_j),
                    "forecast": sum(cells.get(j, {}).get(k, 0.0) - cells.get(j, {}).get(gone, 0.0) for j in span_j),
                    "realised": sum(actual.get((k, j), 0.0) for j in span_j) - lost,
                    "control": (sum(actual.get((b, j), 0.0) for b in peers for j in span_j) / len(peers) - lost) if peers else None})
    return out


def opportunity_report(got: list[dict[str, Any]]) -> str:
    import statistics as st

    def mean(xs: list[float]) -> float:
        return st.mean(xs) if xs else float("nan")

    def sem(xs: list[float]) -> float:
        return st.stdev(xs) / len(xs) ** 0.5 if len(xs) > 1 else float("nan")
    lines = ["%-15s %-4s %3s %5s %9s %9s %9s" % ("cut", "rank", "n", "jorn", "forecast", "realised", "control")]
    lines += ["%-15s %-4d %3d %5d %9.1f %9.1f %9s" % (g["cut"], g["rank"], g["n_peers"], g["jornadas"], g["forecast"], g["realised"],
                                                    "-" if g["control"] is None else "%.1f" % g["control"]) for g in got]
    both = [g for g in got if g["control"] is not None]
    diff = [g["realised"] - g["control"] for g in both]
    lines.append("cases %d (with a control %d), cuts %d: mean forecast %.1f, realised %.1f, control %.1f; realised less control %+.1f (se %.1f), "
                 "share beating control %.0f%%, realised above 0 in %.0f%%" % (
                     len(got), len(both), len({g["cut"] for g in got}), mean([g["forecast"] for g in got]),
                     mean([g["realised"] for g in got]), mean([g["control"] for g in both]), mean(diff), sem(diff),
                     100 * mean([float(d > 0) for d in diff]), 100 * mean([float(g["realised"] > 0) for g in got])))
    return "\n".join(lines)


# What the grade stage keeps, a set a run.
GRADED = ("grade_cell",)


def superseded(run: int, path: Path | None = None) -> None:
    """Drop the grades of earlier runs of the same code (runs.commit_sha):
    this run's, on as much data or more, stand for that code."""
    with closing(connect(path)) as db, db:
        for t in GRADED:
            db.execute("DELETE FROM %s WHERE run_id IN (SELECT e.run_id FROM runs e, runs r"
                       " WHERE r.run_id = ? AND e.commit_sha = r.commit_sha AND e.run_id < r.run_id)"
                       % t, (run,))


def graded(run: int, last: int, path: Path | None = None) -> bool:
    """Whether an earlier run of this run's code (runs.commit_sha) graded the
    past locks on every jornada done up to last: its grades stand, and
    nothing would change them."""
    with closing(connect(path)) as db:
        return db.execute("SELECT 1 FROM grade_cell g JOIN runs e ON e.run_id = g.run_id"
                          " JOIN runs r ON r.run_id = ? AND e.commit_sha = r.commit_sha AND e.run_id < r.run_id"
                          " GROUP BY g.run_id HAVING max(g.jornada) >= ? LIMIT 1", (run, last)).fetchone() is not None


class Results(NamedTuple):
    """The auction outcomes seen as of t: how many, how many of them sold, and when the newest closed."""
    n: int
    sold: int
    newest: str | None


def results_seen() -> Results:
    (row,) = rows("SELECT count(*) AS n, count(won) AS sold, max(close) AS newest FROM auction_results")
    return Results(*row)


def scored_at(run: int, path: Path | None = None) -> str | None:
    """When (runs.as_of) the latest earlier run of this run's code (runs.commit_sha) that recorded the auction scores
    (auction_brier) was made; None if none was."""
    with closing(connect(path)) as db:
        return db.execute("SELECT max(e.as_of) FROM auction_brier b JOIN runs e ON e.run_id = b.run_id"
                          " JOIN runs r ON r.run_id = ? AND e.commit_sha = r.commit_sha AND e.run_id < r.run_id",
                          (run,)).fetchone()[0]


def auctions_scored(run: int, now: Results) -> bool:
    """Whether an earlier run of this run's code scored the auctions on the outcomes seen now: its scores stand, as
    no outcome has come since."""
    then = scored_at(run)
    if then is None or not now.n:
        return False
    with replaying():
        set_now(snapshot_stamp(then))
        return results_seen() == now


def record_auction(run: int | None) -> dict[str, int]:
    with closing(connect()) as db:
        ls = auction.looks(db)
        live = auction.stored_points(db)
    back = auction.backtest(ls)
    cal = auction.calibration(back, "backtest:") + auction.calibration(live, "live:")
    brier = auction.briers(back, "backtest:") + auction.briers(live, "live:")
    if run is not None:
        record([{"run_id": run, **r} for r in cal], "auction_calibration")
        record([{"run_id": run, **r} for r in brier], "auction_brier")
    return {"auction_points": len(back), "auction_live": len(live), "auction_drift": sum(r["drift"] for r in cal)}


def record_grades() -> dict[str, int]:
    """The grade stage (run.py grade): replay each past lock with this code, and keep every player forecast against
    what then happened (one set a code: superseded), unless this code has graded them on every jornada done (graded);
    and score the auction forecast (record_auction), unless this code has on every auction closed (auctions_scored)."""
    done = done_jornadas()
    resolved = len(done)
    run = this_run()
    standing = run is not None and bool(done) and graded(run, max(done))
    scored = {} if run is not None and auctions_scored(run, results_seen()) else record_auction(run)
    record_sales()
    if standing:
        print("  grade: this code's grades stand (every jornada done graded)")
        return {"resolved": resolved, "forecasts": 0, **scored}
    fc = backtest(GRADE_AHEAD)
    if run is not None:
        record([{"run_id": run, **r} for r in fc], "grade_cell")
        superseded(run)
    forecasts = len({(r["from_j"], r["jornada"]) for r in fc})     # lock moments a jornada
    print("  grade: %d forecasts over %d jornadas" % (forecasts, resolved))
    return {"resolved": resolved, "forecasts": forecasts, **scored}


class Trade(NamedTuple):
    """A ledger row of mine: the player, when it happened (with its offset) and the amount."""
    player: str
    at: dt.datetime
    amount: float


class Season(NamedTuple):
    """What the grading reads of the world at the run's moment: my ledger rows (a row of mine is one whose who is me
    and that names a player), each jornada's lock in lock order, the jornadas done and each player's points by jornada
    played."""
    entries: tuple[ledger.LedgerEntry, ...]
    locks: dict[int, dt.datetime]
    done: set[int]
    scored: dict[str, dict[int, float]]


class MomentFacts(NamedTuple):
    """What the sale grading reads at one moment: each asked player's newest market value above 0, the players in the
    eleven the app had fielded, the players flagged (player_status with a status) and my players."""
    market: Mapping[str, float]
    eleven: frozenset[str]
    flagged: frozenset[str]
    squad: frozenset[str]


def season_at(loaded: loader.Loaded, world: state.World) -> Season:
    """The Season of the world (rules my_rows_name_a_player, locks_in_lock_order, scored_is_points_played)."""
    mine = tuple(e for e in ledger.ledger(world, loaded.identity) if e.who == loaded.league.me and e.player is not None)
    locks = {r.jornada: when for r in sorted(season_facts.round_locks(world), key=lambda r: (r.lock, r.jornada))
             if (when := snapshot_stamp(r.lock)) is not None}
    scored: dict[str, dict[int, float]] = {}
    for p in season_facts.player_jornadas(world, loaded.identity):
        if p.pts is not None:
            scored.setdefault(p.player, {})[p.jornada] = p.pts
    return Season(mine, locks, set(season_facts.jornadas_done(world)), scored)


def moments_facts(loaded: loader.Loaded, moments: Iterable[dt.datetime], read: Callable[[state.World], T]) -> dict[dt.datetime, T]:
    """read(world) for each moment, in one sweep over their minute-floored stamps; a moment's result is that of its
    stamp, the same alone, in any subset or order of moments, and unchanged by rows appended after it."""
    asked = list(moments)
    return dict(zip(asked, state.sweep(loaded.obs, [stamp(m) for m in asked], read)))


def market_values(history: Sequence[ob.Market], players: Iterable[str]) -> dict[str, float]:
    """Each asked player's newest value above 0 in the market rows (rule value_is_last_positive: a later 0 or an empty
    value does not hide it; a player with none is absent)."""
    want, out = set(players), {}
    for m in reversed(history):
        if not want:
            break
        if m.ff_id in want and m.value is not None and m.value > 0:
            out[m.ff_id] = m.value
            want.discard(m.ff_id)
    return out


def facts_at(loaded: loader.Loaded, world: state.World, players: Iterable[str]) -> MomentFacts:
    """The MomentFacts of the world, with the market values of the players."""
    return MomentFacts(
        market_values(price_fold.price_history(price_fold.price_state(world)), players),
        frozenset(a.player for a in current.app_lineup(world, loaded.identity) if a.player is not None),
        frozenset(s.player for s in current.player_status(world, loaded.identity) if s.status is not None),
        frozenset(s.player for s in current.squads(world, loaded.identity)
                  if s.manager == loaded.league.me and s.player is not None))


def facts_for(loaded: loader.Loaded, wanted: Mapping[dt.datetime, Collection[str]]) -> dict[dt.datetime, MomentFacts]:
    """The MomentFacts at each moment of wanted, with the market values of the players it names, in one sweep."""
    asked: dict[str, set[str]] = {}
    for moment, players in wanted.items():
        asked.setdefault(stamp(moment), set()).update(players)
    return moments_facts(loaded, wanted, lambda world: facts_at(loaded, world, asked[world.cut]))


MINUTE = dt.timedelta(minutes=1)


def wanted_moments(trades: Iterable[Trade], locks: Mapping[int, dt.datetime], done: set[int]) -> dict[dt.datetime, set[str]]:
    """The moments whose facts grading the sales needs, with the players whose value is asked there: a minute before
    each sale (his), the last lock of each horizon of a sale with a done jornada at its lock (his value), and a minute
    before the lock of every done jornada (no player)."""
    out: dict[dt.datetime, set[str]] = {locks[j] - MINUTE: set() for j in done & set(locks)}
    for t in trades:
        out.setdefault(t.at - MINUTE, set()).add(t.player)
        spans = horizon_jornadas(t.at, dict(locks), done)
        if spans["lock"]:
            for h in HORIZONS:
                out.setdefault(locks[max(spans[h])], set()).add(t.player)
    return out


def my_trades(season: Season, kind: str, since: str) -> list[Trade]:
    """My ledger rows of one kind (buy, sell) from since on (a text comparison of the row's at), oldest first."""
    found = [Trade(str(e.player), dt.datetime.fromisoformat(e.at), e.amount) for e in season.entries
             if e.kind == kind and e.at >= since]
    return sorted(found, key=lambda t: t.at)


def lock_after(at: dt.datetime, locks: Sequence[dt.datetime]) -> dt.datetime | None:
    """The first of the locks strictly after at, or None if none is."""
    return min((x for x in locks if x > at), default=None)


def buys_between(buys: Sequence[Trade], start: dt.datetime, end: dt.datetime | None) -> bool:
    """Whether a buy falls at or after start and before end (end None: no bound), so funds a sale made at start."""
    return any(start <= b.at and (end is None or b.at < end) for b in buys)


def sale_facts(sale: Trade, buys: Sequence[Trade], lock: dt.datetime | None,
               facts: Mapping[dt.datetime, MomentFacts]) -> salegrade.Sale:
    """The sale as the facts stood a minute before it (facts at that moment): his market value (the amount if none was
    seen), whether he was in the latest line-up seen, whether he was flagged, and whether a buy of mine fell before the
    lock (lock None: no bound)."""
    then = facts[sale.at - MINUTE]
    return salegrade.Sale(sale.player, sale.amount, then.market.get(sale.player) or sale.amount, sale.player in then.eleven,
                          sale.player in then.flagged, buys_between(buys, sale.at, lock))


class Sold(NamedTuple):
    """A sale of mine with the moment it was made."""
    at: dt.datetime
    sale: salegrade.Sale


def sales_since(season: Season, day: str, facts: Mapping[dt.datetime, MomentFacts]) -> list[Sold]:
    """Each of my sales from the day on (a date, YYYY-MM-DD), with its moment and as a salegrade.Sale read at that
    moment (sale_facts); funded_buy is a buy of mine between the sale and the next lock."""
    buys = my_trades(season, "buy", day)
    locks = sorted(season.locks.values())
    return [Sold(s.at, sale_facts(s, buys, lock_after(s.at, locks), facts)) for s in my_trades(season, "sell", day)]


def horizon_jornadas(at: dt.datetime, locks: dict[int, dt.datetime], done: set[int]) -> dict[str, frozenset[int]]:
    """Per horizon of salegrade.HORIZONS, the done jornadas a sale at at is graded on: lock is j0, the first jornada
    whose lock is after at; next4 is j0 to j0+3; season is every jornada from j0 on. Each set is inside done, and a
    set is empty when no jornada of its horizon is done (or none follows the sale)."""
    after = sorted((j for j in locks if locks[j] > at), key=locks.__getitem__)
    if not after:
        return {h: frozenset() for h in HORIZONS}
    j0 = after[0]
    spans = {"lock": {j0}, "next4": set(range(j0, j0 + 4)), "season": {j for j in locks if j >= j0}}
    return {h: frozenset(spans[h] & done) for h in HORIZONS}


def points(scored: dict[str, dict[int, float]], player: str, jornadas: Iterable[int]) -> float:
    """The points a player scored over the jornadas, from a Season's scored; a jornada he did not play adds 0."""
    got = scored.get(player, {})
    return sum(got.get(j, 0.0) for j in jornadas)


def benched_at(moment: dt.datetime, facts: Mapping[dt.datetime, MomentFacts]) -> set[str]:
    """The players on my squad at moment who were not in the eleven the app had fielded then (rule
    bench_is_squad_less_eleven)."""
    return set(facts[moment].squad - facts[moment].eleven)


def fielded_at_locks(locks: dict[int, dt.datetime], done: set[int],
                     facts: Mapping[dt.datetime, MomentFacts]) -> dict[int, set[str]]:
    """The eleven the app had fielded a minute before each done jornada's lock."""
    return {j: set(facts[locks[j] - MINUTE].eleven) for j in done & set(locks)}


def course(sold: Sold, locks: dict[int, dt.datetime], done: set[int], scored: dict[str, dict[int, float]],
           fielded: dict[int, set[str]], facts: Mapping[dt.datetime, MomentFacts]) -> salegrade.Course | None:
    """What followed the sale at each horizon of horizon_jornadas: what he scored (sold); what my players on the bench
    when he was sold (benched_at) scored in the jornadas they were fielded (fielded: the eleven at each lock), for an
    eleven sale only, else 0 (cover); and his latest market value at the lock of the horizon's last jornada
    (value_after, from facts; never the sale amount). None if no jornada is done at the lock horizon (then none is at
    any) or if no market value of his was seen by then."""
    spans = horizon_jornadas(sold.at, locks, done)
    if not spans["lock"]:
        return None
    bench = benched_at(sold.at - MINUTE, facts) if sold.sale.in_eleven else set()
    after: dict[str, float] = {}
    for h in HORIZONS:
        value = facts[locks[max(spans[h])]].market.get(sold.sale.player)
        if value is None:
            return None
        after[h] = value
    return salegrade.Course(
        sold={h: points(scored, sold.sale.player, spans[h]) for h in HORIZONS},
        cover={h: sum(points(scored, p, [j]) for j in spans[h] for p in bench & fielded[j]) for h in HORIZONS},
        value_after=after)


def graded_count(season: Season) -> int:
    """How many of my sales from SALES_FROM have a done jornada at their lock horizon, from the ledger alone."""
    return sum(1 for t in my_trades(season, "sell", SALES_FROM) if horizon_jornadas(t.at, season.locks, season.done)["lock"])


def graded_sales(sold: Sequence[Sold], locks: dict[int, dt.datetime], done: set[int], scored: dict[str, dict[int, float]],
                 fielded: dict[int, set[str]], facts: Mapping[dt.datetime, MomentFacts]) -> list[salegrade.Graded]:
    """The sales that have a course (course), each graded at RATE (salegrade.grade); a sale with no course is left out."""
    courses = [(s.sale, course(s, locks, done, scored, fielded, facts)) for s in sold]
    return [salegrade.grade(sale, got, RATE) for sale, got in courses if got is not None]


def read_storage() -> tuple[loader.Loaded, list[str]]:
    """The storage read once: the Loaded the sweeps run over and the cuts a replay runs at (loader.replay_cuts from SALES_FROM)."""
    with closing(connect()) as db:
        loaded = loader.load(db, league.load_config())
    return loaded, loader.replay_cuts(loaded.obs, SALES_FROM)


def season(loaded: loader.Loaded) -> Season:
    """The Season at the run's moment (clock.run_now, LFG_NOW when pinned)."""
    return state.sweep(loaded.obs, [stamp(run_now())], lambda world: season_at(loaded, world))[0]


def record_sales() -> None:
    """Each sale from SALES_FROM with a done jornada at its lock, graded at RATE (salegrade.grade) and summarised
    (salegrade.measure_values), is appended to measure_log as sale_grade with jornadas_done, unless the latest one
    logged has the same all.lock.n (graded_count) and jornadas_done; then no sale is read."""
    loaded, _ = read_storage()
    now = season(loaded)
    marks = {"all.lock.n": float(graded_count(now)), "jornadas_done": float(len(now.done))}
    latest = dict(rows("SELECT key, value FROM measure_latest WHERE measure = 'sale_grade'"))
    if all(latest.get(k) == v for k, v in marks.items()):
        return
    facts = facts_for(loaded, wanted_moments(my_trades(now, "sell", SALES_FROM), now.locks, now.done))
    fielded = fielded_at_locks(now.locks, now.done, facts)
    sold = sales_since(now, SALES_FROM, facts)
    values = salegrade.measure_values(salegrade.summary(graded_sales(sold, now.locks, now.done, now.scored, fielded, facts)))
    log_measure("sale_grade", values | {"jornadas_done": marks["jornadas_done"]})


class Cut(NamedTuple):
    """A replayed cut: the moment a sale made at it is read from (a minute after its snapshot), the context the rules
    read and the standing offers on my players."""
    at: dt.datetime
    ctx: worth.Context
    standings: Sequence[worth.Standing]


class Choice(NamedTuple):
    """A sale a rule would make: the player, the moment and the offer it accepts."""
    player: str
    at: dt.datetime
    amount: float


class RuleScore(NamedTuple):
    rule: str
    horizon: str
    n: int
    total_net: float
    per_million: float


def first_sales(cuts: Sequence[Cut], rules: Mapping[str, worth.Rule]) -> dict[str, list[Choice]]:
    """For each rule, in cut order, the sales it makes: an offer is accepted when the rule states a reserve and the offer
    reaches it. A rule sells a player at most once, at the first cut where it accepts an offer on him; a rule with no
    reserve sells nothing."""
    out: dict[str, list[Choice]] = {name: [] for name in rules}
    for cut in cuts:
        for name, rule in rules.items():
            gone = {c.player for c in out[name]}
            for standing in cut.standings:
                if standing.player in gone or standing.offer is None:
                    continue
                reserve = rule(standing, cut.ctx).reserve
                if reserve is not None and standing.offer >= reserve:
                    out[name].append(Choice(standing.player, cut.at, standing.offer))
                    gone.add(standing.player)
    return out


def score_rows(name: str, graded: Sequence[salegrade.Graded]) -> list[RuleScore]:
    """One RuleScore per horizon of salegrade.HORIZONS for the graded sales: their count, total net points and net
    points per million raised; n is 0 and the totals 0 when there are none."""
    return [RuleScore(name, r.horizon, r.n, r.mean_net * r.n, r.per_million)
            for r in salegrade.summary(graded) if r.case == "all"]


def replayed_cut(decided: decision.Decided | decision.Stale) -> Cut | None:
    """The Cut of a decision that has standing offers on my players (rule replayed_cut_needs_offers: its extras'
    offers), so the context and the later sale prices are those of the cut; None for a Stale cut or one with no offer."""
    at = None if isinstance(decided, decision.Stale) else snapshot_stamp(decided.cut)
    if isinstance(decided, decision.Stale) or at is None or not decided.extras.offers:
        return None
    standings = [worth.Standing(o.player, o.value, o.money, later_sale(decided.inputs, o.player, o.value))
                 for o in decided.extras.offers]
    return Cut(at + MINUTE, context(decided.inputs), standings)


def replayed_cuts(loaded: loader.Loaded, cuts: Sequence[str]) -> list[Cut]:
    """The Cut (replayed_cut) of each cut that has one, ascending, from one streamed replay: only the Cuts are kept."""
    out: list[Cut] = []

    def keep(_: str, decided: decision.Decided | decision.Stale) -> None:
        cut = replayed_cut(decided)
        if cut is not None:
            out.append(cut)
    decision.replay(loaded, cuts, keep)
    return out


def rule_scores(rules: Mapping[str, worth.Rule], since: str = SALES_FROM) -> list[RuleScore]:
    """On demand, never in the grade stage: "actual" (my real sales from since, sales_since) and each rule (first_sales
    over replayed_cuts at the replay cuts from since) as RuleScores, every sale graded by the same
    course at RATE (graded_sales)."""
    loaded, cuts = read_storage()
    now = season(loaded)
    chosen = first_sales(replayed_cuts(loaded, [c for c in cuts if c >= since and snapshot_stamp(c) is not None]), rules)
    made = {name: [Trade(c.player, c.at, c.amount) for c in got] for name, got in chosen.items()}
    facts = facts_for(loaded, wanted_moments([*my_trades(now, "sell", since), *(t for ts in made.values() for t in ts)], now.locks, now.done))
    fielded = fielded_at_locks(now.locks, now.done, facts)
    sold = {"actual": sales_since(now, since, facts)} | {
        name: [Sold(t.at, sale_facts(t, [], None, facts)) for t in ts] for name, ts in made.items()}
    return [row for name, found in sold.items()
            for row in score_rows(name, graded_sales(found, now.locks, now.done, now.scored, fielded, facts))]


def rule_report(scores: Sequence[RuleScore]) -> str:
    """A table, a line for each score: rule, horizon, n, total net points and net points per million. Every line is
    the same length."""
    head = ("rule", "horizon", "n", "net", "net/M")
    body = [(s.rule, s.horizon, str(s.n), "%.1f" % s.total_net, "%.2f" % s.per_million) for s in scores]
    widths = [max(len(r[i]) for r in [head, *body]) for i in range(len(head))]
    return "\n".join("  ".join(c.ljust(w) if i < 2 else c.rjust(w) for i, (c, w) in enumerate(zip(r, widths)))
                     for r in [head, *body])


def log_measure(measure: str, values: dict[str, float]) -> None:
    """The headline numbers of a measure appended to measure_log, beside
    the code's short commit (measure_latest shows each against the one
    before)."""
    import subprocess
    sha = subprocess.run(["git", "-C", str(Path(__file__).parent), "rev-parse", "--short", "HEAD"],
                         capture_output=True, text=True).stdout.strip() or "unknown"
    at = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    record([{"at": at, "commit_sha": sha, "measure": measure,
             "key": k, "value": v} for k, v in values.items()], "measure_log")


def accuracy(ahead: int = 5) -> list[tuple[Any, ...]]:
    """The forecast at each lock (backtest) graded as a run of its own
    (trigger 'accuracy'): at the next jornada and at ahead, the rows
    (grp, ahead, n, rmse, mae, bias, r, slope) of grade_accuracy, the star group first."""
    with closing(connect()) as db, db:
        run = db.execute("INSERT INTO runs (started, as_of, trigger, commit_sha, ok)"
                         " VALUES ('', '', 'accuracy', '', 1)").lastrowid
    assert run is not None
    record([{"run_id": run, **r} for r in backtest(ahead)], "grade_cell")
    got = [tuple(r) for r in rows("SELECT grp, ahead, n, rmse, mae, bias, r, slope FROM grade_accuracy"
                                  " WHERE run_id = ? AND ahead IN (0, ?) ORDER BY grp DESC, ahead", (run, ahead))]
    log_measure("accuracy", {"%s%s@%d" % ("" if g == "all" else "star_", k, a): v for g, a, n, rmse, mae, bias, r, slope in got
                             for k, v in (("n", n), ("rmse", rmse), ("mae", mae), ("bias", bias), ("r", r), ("slope", slope))})
    return got


def view_table(view: str, path: Path | None = None) -> str:
    """A view (grade_forecast) made a table of its columns in the
    current tables (tidy.tables_in), for its readers' tests to fill."""
    with closing(connect(path)) as db, db:
        cols = ['"%s" %s' % (r[1], r[2]) for r in db.execute('PRAGMA table_info("%s")' % view)]
        db.execute('DROP VIEW "%s"' % view)
        db.execute('CREATE TABLE "%s" (%s)' % (view, ", ".join(cols)))
    return view


def _selftest_measure_log() -> None:
    """measure_log keeps each run's headline numbers; measure_latest sets the latest beside the one before."""
    import tempfile
    from ffcore.tidy import tables_in
    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        log_measure("accuracy", {"share": 0.25})
        assert rows("SELECT measure, key, value, previous FROM measure_latest") == [("accuracy", "share", 0.25, None)]
        (sha,), = rows("SELECT commit_sha FROM measure_log")
        assert sha != "unknown" and 4 <= len(sha) <= 12, sha
        log_measure("accuracy", {"share": 0.5})
        assert rows("SELECT key, value, previous, change FROM measure_latest") == [("share", 0.5, 0.25, 0.25)]


def _selftest_stand() -> None:
    """With nothing new (every jornada graded, every auction scored) the grade stage enters neither replay; with a
    jornada or an auction new it enters that one."""
    entered: list[str] = []
    state = {"graded": True, "scored": True}

    def auction_scored(run: int | None) -> dict[str, int]:
        entered.append("auction")
        return {}

    def regraded(ahead: int = 0) -> list[dict[str, Any]]:
        entered.append("grades")
        return []
    stub: dict[str, Any] = {
        "done_jornadas": lambda: {1, 2}, "this_run": lambda: 5, "results_seen": lambda: Results(1, 1, "c"),
        "graded": lambda run, last: state["graded"], "auctions_scored": lambda run, now: state["scored"],
        "record_auction": auction_scored, "backtest": regraded, "record_sales": lambda: None,
        "record": lambda *a, **k: None, "superseded": lambda run: None}
    kept = {k: globals()[k] for k in stub}
    try:
        globals().update(stub)
        for g, a, want in ((True, True, []), (False, True, ["grades"]), (True, False, ["auction"]), (False, False, ["auction", "grades"])):
            state.update(graded=g, scored=a)
            entered.clear()
            record_grades()
            assert entered == want, (g, a, entered)
    finally:
        globals().update(kept)


def _selftest_window() -> None:
    """A buy funds a sale when it falls from the sale up to the next lock: inside it does; before the sale, at the
    lock or after it does not; with no lock after the sale, any later buy does."""
    hour = dt.timedelta(hours=1)
    lock = dt.datetime(2026, 9, 1, 12, tzinfo=dt.timezone.utc)
    sale = lock - dt.timedelta(days=1)

    def buy(at: dt.datetime) -> Trade:
        return Trade("x", at, 1.0)
    assert buys_between([buy(sale + hour)], sale, lock), "a buy inside the window funds the sale"
    assert not buys_between([buy(sale - hour)], sale, lock), "a buy before the sale does not"
    assert not buys_between([buy(lock)], sale, lock), "a buy exactly at the lock does not"
    assert not buys_between([buy(lock + hour)], sale, lock), "a buy after the lock does not"
    assert buys_between([buy(lock + hour)], sale, None), "with no lock after the sale, a later buy does"
    assert lock_after(sale, [lock]) == lock and lock_after(lock, [lock]) is None, \
        "the next lock is the first strictly after the sale; a sale after the last lock has none"


def _selftest_cover() -> None:
    """The cover of a sale counts a bench player only in the jornadas he was fielded: it is zero for a sale not from
    the eleven, never more than the bench's points in the horizon, and adds over disjoint horizons; his value after is
    that of the lock of each horizon's last jornada, never the sale amount, and a sale with none seen is not graded
    (rule bench_is_squad_less_eleven for who is on the bench)."""
    day = dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc)
    locks = {1: day + dt.timedelta(days=2), 2: day + dt.timedelta(days=9), 3: day + dt.timedelta(days=16)}
    scored = {"a": {1: 4.0, 2: 6.0, 3: 1.0}, "b": {1: 2.0, 2: 2.0, 3: 2.0}, "s": {1: 3.0, 2: 3.0, 3: 3.0}}
    fielded = {1: set("sc"), 2: set("ac"), 3: set("ab")}
    done = {1, 2, 3}
    sale = salegrade.Sale("s", 5e6, 5e6, True, False, False)

    def facts_with(values: dict[int, float]) -> dict[dt.datetime, MomentFacts]:
        out = {locks[j]: MomentFacts({"s": values[j]} if j in values else {}, frozenset(), frozenset(), frozenset()) for j in locks}
        out[day - MINUTE] = MomentFacts({}, frozenset("sc"), frozenset(), frozenset("sabc"))
        return out
    assert benched_at(day - MINUTE, facts_with({})) == {"a", "b"}, "my squad less the eleven fielded then"
    assert course(Sold(day, sale), locks, done, scored, fielded, facts_with({})) is None, "no value of his seen: not graded"
    facts = facts_with({1: 4e6, 2: 6e6, 3: 7e6})
    got = course(Sold(day, sale), locks, done, scored, fielded, facts)
    assert got is not None
    assert got.value_after == {"lock": 4e6, "next4": 7e6, "season": 7e6}, \
        "his value at the lock of each horizon's last jornada, never the sale amount"
    assert got.cover == {"lock": 0.0, "next4": 6.0 + 1.0 + 2.0, "season": 6.0 + 1.0 + 2.0}, \
        "a and b count only in the jornadas they were fielded (c was never on the bench)"
    assert got.sold["season"] == 9.0
    other = course(Sold(day, sale._replace(in_eleven=False)), locks, done, scored, fielded, facts)
    assert other is not None and not any(other.cover.values()), "a bench sale has no cover"
    assert course(Sold(locks[3], sale), locks, done, scored, fielded, facts) is None, "no jornada after the sale"


def _selftest_moment_facts() -> None:
    """Rules value_is_last_positive and moment_facts_are_as_of on a small league, and the invariants: the facts at a
    moment are those of any sweep over moments containing it and do not change when rows are appended after it."""
    from ffcore import forecast_facts
    t0, t1, t2, t3 = ("2026-09-0%dT1000Z" % d for d in (1, 2, 3, 4))
    ident = current.Identity({("s", "P2", "c"): "p2"}, {"a1": "p1", "a2": "p2", "a3": "p3"})

    def team(at: str, who: dict[str, str]) -> list[ob.ApiTeams]:
        return [ob.ApiTeams(at, "app", 1.0, 1.0, 0.0, a, "t", m, "", "", "", "pt" + a) for a, m in who.items()]

    def lineup(at: str, ids: str) -> list[ob.ApiLineup]:
        return [ob.ApiLineup(at, "app", None, None, a, "1", "", "", "") for a in ids.split()]
    tables: dict[str, list[Any]] = {
        "market": [ob.Market(t0, "laliga", 5.0, "p1", "MED", "c"), ob.Market(t1, "laliga", 7.0, "p1", "MED", "c"),
                   ob.Market(t1, "laliga", 3.0, "p2", "MED", "c"), ob.Market(t2, "laliga", 0.0, "p1", "MED", "c")],
        "api_teams": team(t0, {"a1": "me", "a2": "me", "a3": "me"}) + team(t2, {"a1": "me", "a2": "bob", "a3": "me"}),
        "api_lineup": lineup(t0, "a1 a2") + lineup(t2, "a1 a3"),
        "lineups": [ob.Lineups(t1, "futbolfantasy", 50.0, "s", "P2", "c", "injured", "")]}
    loaded = loader.Loaded(ob.of(tables), ident, ledger.League("me", 0.0), forecast_facts.knobs(), ())
    everyone = ["p1", "p2", "p3"]
    when = {name: snapshot_stamp(t) for name, t in (("t0", t0), ("t1", t1), ("t2", t2), ("t3", t3))}
    assert all(w is not None for w in when.values())
    moments = {k: v + dt.timedelta(seconds=30) for k, v in when.items() if v is not None}
    wanted = {m: everyone for m in moments.values()}
    got = facts_for(loaded, wanted)
    at = {k: got[m] for k, m in moments.items()}
    assert at["t0"] == MomentFacts({"p1": 5.0}, frozenset({"p1", "p2"}), frozenset(), frozenset({"p1", "p2", "p3"})), \
        "moment_facts_are_as_of: a minute floor, and no value, flag or line-up observed after it is seen"
    assert at["t1"].market == {"p1": 7.0, "p2": 3.0} and at["t1"].flagged == {"p2"}
    assert at["t3"].market == {"p1": 7.0, "p2": 3.0}, "value_is_last_positive: a later 0 does not hide the 7; p3 has none"
    assert at["t3"].eleven == {"p1", "p3"} and at["t3"].squad == {"p1", "p3"}
    assert benched_at(moments["t0"], got) == {"p3"} and benched_at(moments["t3"], got) == set(), "bench_is_squad_less_eleven"
    only = facts_for(loaded, {moments["t3"]: ["p2"]})[moments["t3"]]
    assert only.market == {"p2": 3.0}, "only the players asked"
    for order in (list(moments.values())[::-1], [moments["t2"], moments["t0"], moments["t2"]], [moments["t3"]]):
        again = facts_for(loaded, {m: everyone for m in order})
        assert all(again[m] == got[m] for m in order), "any subset or order of moments gives each the same facts"
    tables["market"].append(ob.Market("2026-09-09T1000Z", "laliga", 99.0, "p1", "MED", "c"))
    tables["api_lineup"] += lineup("2026-09-09T1000Z", "a2")
    tables["lineups"].append(ob.Lineups("2026-09-09T1000Z", "futbolfantasy", 50.0, "s", "P2", "c", "", ""))
    later = facts_for(loaded._replace(obs=ob.of(tables)), wanted)
    assert later == got, "rows appended after the moments leave their facts unchanged"
    assert market_values([ob.Market(t0, "laliga", 0.0, "p1", "MED", "c")], ["p1"]) == {}, "a value of 0 is none"


def _selftest_points() -> None:
    """A player's points over jornadas add over disjoint sets, and a jornada or a player with none adds 0."""
    scored = {"a": {1: 3.0, 2: -1.0, 4: 5.0}}
    assert points(scored, "a", {1, 2}) + points(scored, "a", {3, 4}) == points(scored, "a", {1, 2, 3, 4}) == 7.0
    assert points(scored, "a", set()) == points(scored, "nobody", {1, 2}) == 0.0


def _selftest_record_sales() -> None:
    """record_sales logs the sales' measure once for a given number of graded sales and jornadas done: a second call
    with nothing new adds no rows and reads no sale, a jornada done adds a set; a sale with no course is not graded."""
    import tempfile
    from ffcore.tidy import tables_in
    at = dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc)
    locks = {1: at + dt.timedelta(days=2), 2: at + dt.timedelta(days=9)}
    held: dict[str, Any] = {"done": {1}, "read": 0}
    entries = tuple(ledger.LedgerEntry(at.isoformat(), "sell", "me", None, "p", 5e6, None, "me", 0) for _ in range(2))
    sold = [Sold(at, salegrade.Sale("p", 5e6, 5e6, True, False, False))] * 2
    flat = {h: 1.0 for h in HORIZONS}
    ok = salegrade.Course(flat, flat, flat)

    def read(now: Season, day: str, facts: Mapping[dt.datetime, MomentFacts]) -> list[Sold]:
        held["read"] += 1
        return sold
    stub: dict[str, Any] = {
        "read_storage": lambda: (None, []), "season": lambda loaded: Season(entries, locks, held["done"], {}),
        "facts_for": lambda loaded, wanted: {}, "sales_since": read,
        "course": lambda s, locks, done, scored, fielded, facts: ok if len(done) else None,
        "fielded_at_locks": lambda locks, done, facts: {}}
    kept = {k: globals()[k] for k in stub}

    def logged() -> int:
        return rows("SELECT count(*) FROM measure_log WHERE measure = 'sale_grade'")[0][0]
    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        try:
            globals().update(stub)
            record_sales()
            first = logged()
            assert first > 0 and dict(rows("SELECT key, value FROM measure_latest"))["all.lock.n"] == 2
            record_sales()
            assert logged() == first and held["read"] == 1, "nothing new: no rows, no sale read"
            held["done"] = {1, 2}
            record_sales()
            assert logged() == 2 * first, "a jornada done: a new set"
        finally:
            globals().update(kept)


def _selftest_horizons() -> None:
    """A sale's horizons are the done jornadas from its next lock on: the lock is inside next4, next4 inside season,
    each inside done; a sale with no jornada after it, or none done at its lock, has no lock jornada."""
    day = dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc)
    locks = {j: day + dt.timedelta(days=7 * j) for j in range(1, 11)}
    for done in ({1, 2, 3, 4, 5, 6, 7, 8, 9, 10}, {2, 3, 4, 5, 6}, {3, 5, 9}, {1}, set()):
        for at in (day, day + dt.timedelta(days=30), day + dt.timedelta(days=80)):
            got = horizon_jornadas(at, locks, done)
            assert frozenset(got) == frozenset(HORIZONS)
            assert got["lock"] <= got["next4"] <= got["season"] <= frozenset(done), (done, at, got)
    got = horizon_jornadas(day + dt.timedelta(days=7), locks, {1, 2, 3, 4, 5, 6, 7, 8, 9, 10})
    assert got == {"lock": {2}, "next4": {2, 3, 4, 5}, "season": set(range(2, 11))}, got
    got = horizon_jornadas(day + dt.timedelta(days=7), locks, {2, 3, 4, 9})
    assert got == {"lock": {2}, "next4": {2, 3, 4}, "season": {2, 3, 4, 9}}, got
    assert not horizon_jornadas(day + dt.timedelta(days=7), locks, {3, 4})["lock"], "a sale whose lock is not done has no lock jornada"
    assert not any(horizon_jornadas(day + dt.timedelta(days=80), locks, set(locks)).values()), \
        "a sale after the last lock has no jornada ahead"


def _selftest_rule_scores() -> None:
    """A rule that states reserve 0 sells each offered player exactly once, one with no reserve sells nothing, a player
    offered at many cuts is sold at the first only; the report's lines are all the same length."""
    import itertools
    at = dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc)
    ctx = worth.Context({}, {}, 0.0)

    def rule(reserve: float | None) -> worth.Rule:
        return lambda standing, ctx: worth.Verdict(None, {}, reserve)
    cases: list[list[list[str]]] = [[["a"], ["a", "b"], ["b", "a", "c"]], [["x"]], [[]]]
    for offers in cases:
        cuts = [Cut(at + dt.timedelta(days=i), ctx, [worth.Standing(p, 1e6, 1e6 * (1 + i), 1e6) for p in ps])
                for i, ps in enumerate(offers)]
        got = first_sales(cuts, {"all": rule(0.0), "none": rule(None), "high": rule(1e12)})
        every = sorted(set(itertools.chain.from_iterable(offers)))
        assert sorted(c.player for c in got["all"]) == every, "each offered player is sold exactly once"
        assert not got["none"] and not got["high"], "no reserve, or none reached: nothing sold"
        for c in got["all"]:
            first = next(i for i, ps in enumerate(offers) if c.player in ps)
            assert c.at == cuts[first].at and c.amount == 1e6 * (1 + first), "sold at the first cut that offers him"
    flat = {h: 1.0 for h in HORIZONS}
    sale = salegrade.Sale("p", 2e6, 2e6, True, False, False)
    graded = [salegrade.grade(sale, salegrade.Course(flat, flat, flat), RATE)] * 3
    for h, row in zip(HORIZONS, score_rows("r", graded)):
        assert row.rule == "r" and row.horizon == h and row.n == 3 and abs(row.total_net - 3 * graded[0].net[h]) < 1e-9
    assert [r.n for r in score_rows("r", [])] == [0] * len(HORIZONS), "no sales: n is 0"
    table = rule_report(score_rows("a_long_rule_name", graded) + score_rows("r", []))
    assert len({len(line) for line in table.splitlines()}) == 1, "every line is the same length"


def _selftest_replayed_cuts() -> None:
    """A Cut for each decision with standing offers on my players, its standings those offers; a decision with none, or
    a Stale one, makes no Cut (rule replayed_cut_needs_offers), and the streamed replay gives the same Cuts."""
    from ffcore import forecast_facts
    from ffcore.decision_facts import _CUTS, _scratch
    obs, ident, lg = _scratch()
    loaded = loader.Loaded(obs, ident, lg, forecast_facts.knobs(), ())
    decided = decision.decisions(loaded, _CUTS)
    offered = [d for d in decided if isinstance(d, decision.Decided) and d.extras.offers]
    assert offered and any(isinstance(d, decision.Stale) for d in decided)
    got = [c for c in map(replayed_cut, decided) if c is not None]
    assert [stamp(c.at - MINUTE) for c in got] == [d.cut for d in offered], "a Cut a decision with offers, a minute on"
    assert [[s.player for s in c.standings] for c in got] == [[o.player for o in d.extras.offers] for d in offered]
    assert replayed_cuts(loaded, _CUTS) == got, "the streamed replay keeps the same Cuts"
    emptied = [d._replace(extras=d.extras._replace(offers=[])) if isinstance(d, decision.Decided) else d for d in decided]
    assert all(replayed_cut(d) is None for d in emptied), "no standing offer on my players: no Cut"
    assert replayed_cut(decision.Stale("old")) is None, "a Stale cut is skipped"


def _selftest() -> None:
    _selftest_replayed_cuts()
    _selftest_rule_scores()
    _selftest_measure_log()
    _selftest_horizons()
    _selftest_points()
    _selftest_cover()
    _selftest_moment_facts()
    _selftest_record_sales()
    import tempfile
    from ffcore.tidy import tables_in
    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        record([{"run_id": 1, "from_j": 7, "jornada": 8, "player": k, "predicted": e, "plays": 0.5, "flagged": fl}
                for k, e, fl in (("a", 3.0, 0), ("b", 1.0, 1))], "grade_cell")
        assert rows("SELECT from_j, jornada, player, predicted, plays, actual, flagged FROM grade_forecast"
                    " ORDER BY player") == [(7, 8, "a", 3.0, 0.5, 0.0, 0), (7, 8, "b", 1.0, 0.5, 0.0, 1)], \
            "a grade's cells, a row a player of the jornadas graded; no points scored is 0"

    from ffcore.fixtures import seed_league
    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        seed_league("me", 1e6, {"a": {"pos": "MED", "owner": "me"}, "b": {"pos": "DEF"}},
                    {1: {"a": (5.0, 0.8), "b": (2.0, 0.5)}, 2: {"a": (5.0, 0.8)}, 3: {"a": (5.0, 0.8)}})
        got = sorted((r["jornada"], r["player"], r["predicted"], r["plays"], r["flagged"])
                     for r in lock_cells(1, 1, {1, 2, 3}))
        assert got == [(1, "a", 4.0, 0.8, 0), (1, "b", 1.0, 0.5, 0), (2, "a", 4.0, 0.8, 0)], \
            "the cells at t (cell) of the lock's jornada and ahead more, each a jornada done"
        assert {r["jornada"] for r in lock_cells(1, 2, {1, 3})} == {1, 3}, "a jornada not done is not graded"

    with tempfile.TemporaryDirectory() as tmp, closing(connect(Path(tmp) / "lfg.db")) as db:
        path = Path(tmp) / "lfg.db"

        def run(commit: str) -> int:
            with db:
                cur = db.execute("INSERT INTO runs (started, as_of, trigger, commit_sha, ok)"
                                 " VALUES ('t', 't', 'service', ?, 1)", (commit,))
            assert cur.lastrowid is not None
            return cur.lastrowid
        old, other, now = run("a"), run("b"), run("a")
        for r in (old, other, now):
            record([{"run_id": r, "from_j": 7, "jornada": 7, "player": "p", "predicted": 1.0, "flagged": 0}],
                   "grade_cell", path)
        superseded(now, path)
        for t in GRADED:
            assert [r for r, in db.execute("SELECT run_id FROM %s ORDER BY run_id" % t)] in (
                [other, now], []), "the same code's earlier grade goes; another code's stays"
        later, elsewhere = run("a"), run("c")
        assert graded(later, 7, path) and not graded(later, 8, path) and not graded(elsewhere, 7, path), \
            "this code graded on every jornada done: nothing to grade again; a jornada more, or other code, is"
        with db:
            db.execute("UPDATE runs SET as_of = '2026-11-01T2210Z' WHERE run_id = ?", (old,))
            db.execute("INSERT INTO auction_brier VALUES (?, 'live:', 1, 0.1, 0.2)", (old,))
        assert scored_at(later, path) == "2026-11-01T2210Z" and scored_at(elsewhere, path) is None and scored_at(old, path) is None, \
            "the run of this code that scored the auctions, not another's, nor one that came after"
    _selftest_stand()
    _selftest_window()
    print("grading self-test OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        raise SystemExit(0)
    if sys.argv[1:2] == ["accuracy"]:
        # accuracy: predicted e against actual points, next jornada and five ahead, at every past lock
        # (records a run of trigger 'accuracy' in the database it reads)
        for g, a, n, rmse, mae, bias, r, slope in accuracy():
            print("%-4s ahead %d  n=%d  rmse=%.4f  mae=%.4f  bias=%+.4f  r=%.4f  slope=%.3f" % (g, a, n, rmse, mae, bias, r, slope), flush=True)
        os._exit(0)
    if sys.argv[1:2] == ["auction"]:
        with closing(connect()) as db:
            recs = auction.backtest_records(auction.looks(db))
        pts = auction.points(recs)
        print("Brier, expanding time-split over %d closed auctions (model vs the pooled distribution):" % len(recs))
        for b in auction.briers(pts, ""):
            print("  %-10s n %5d  model %.4f  pooled %.4f" % (b["scope"], b["n"], b["model"], b["pooled"]))
        print("calibration (stated chance against what happened):")
        for c in auction.calibration(pts, ""):
            print("  %-10s %-10s n %5d  stated %.3f  observed %.3f%s" % (c["scope"], c["bin"], c["n"], c["stated"], c["observed"], "  DRIFT" if c["drift"] else ""))
        print("the 20 largest auctions: ask, rival bids seen, the forecast at the ask / 1.05 / 1.10, what happened")
        for r in sorted(recs, key=lambda r: -r.ask)[:20]:
            what = "unsold" if r.sample.kind == "none" else "%s %.3fx" % ("we won" if r.sample.kind == "me" else "rival won", r.sample.ratio or 0)
            at = [auction.chance(r.curve, x) for x in (1.0, 1.05, 1.10)]
            print("  %s %-6s ask %6.1fM rival %d  %.2f / %.2f / %.2f  -> %s" % (r.close[:10], r.player, r.ask / 1e6, r.rival, at[0], at[1], at[2], what))
        raise SystemExit(0)
    if sys.argv[1:2] == ["price"]:
        # price: momentum's price forecasts against what happened, by horizon (rmse in %, with 'no change' and 'the last change again')
        for scope, h, n, rmse, none, again, predicted, actual in rows(
                "SELECT scope, h, n, rmse, rmse_none, rmse_again, predicted, actual FROM price_grade ORDER BY scope DESC, h"):
            print("%-5s h=%2d  n=%d  rmse=%.2f  none=%.2f  again=%.2f  predicted=%+.2f  actual=%+.2f" % (
                scope, h, n, rmse, none, again, predicted, actual), flush=True)
        os._exit(0)
    if sys.argv[1:2] == ["opportunities"]:
        print(opportunity_report(opportunity_grade()), flush=True)
        os._exit(0)
    if sys.argv[1:2] == ["rules"]:
        print(rule_report(rule_scores(worth.CHALLENGERS)), flush=True)
        os._exit(0)
    print("grading.py --selftest | accuracy | price | opportunities | rules")
    raise SystemExit(2)
