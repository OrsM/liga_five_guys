from __future__ import annotations

import contextlib
import datetime as dt
import math
import os
import sys
from collections import Counter
from collections.abc import Callable, Collection, Iterable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any, NamedTuple, TypeVar

import numpy as np
import numpy.typing as npt

import decision
import hurdle
import salegrade
import valuation
import worth
from board import context, stamped, standing
from ffcore import auction, auction_facts, auction_table, current, forecast_facts, league, ledger, loader, price_fold, season_facts, state, store
from ffcore import observations as ob
from ffcore.clock import run_now
from ffcore import records as rec
from ffcore.store import RunRecords
from ffcore.parse import snapshot_stamp, stamp
from salegrade import HORIZONS

__all__ = ["FABRICATED_FROM", "backtest", "fabricated", "graded", "latest_values", "record_grades", "scored_at", "sound_offers", "standing_grades"]

T = TypeVar("T")



def folds_of(names: Iterable[str]) -> dict[str, state.Fold]:
    """The registered folds named, by name."""
    return {name: state.FOLDS[name] for name in names}


RESULT_FOLDS = folds_of((auction_facts.MARKET_ENDS, auction_facts.BUYS, auction_facts.FEED_SEEN, auction_facts.USER_IDS))
AUCTION_FOLDS = folds_of(("price", *auction_facts.AUCTION_FACTS))
PRICE_FOLDS = folds_of(("price",))
SEASON_FOLDS = folds_of((*ledger.LEDGER_FACTS, "kickoffs", "first_results", "points_changes", "latest_market",
                         "latest_matches", "latest_starters"))
LOCK_FOLDS = folds_of(("kickoffs", "first_results", "points_changes", "season_last", "latest_market", "latest_matches",
                       "latest_starters", "latest_api_market", "latest_api_players_all", "latest_api_teams",
                       "latest_lineups", "latest_results_history"))
MOMENT_FOLDS = folds_of(("price", "latest_api_lineup", "latest_api_players_all", "latest_api_teams", "latest_lineups"))

GRADE_AHEAD = valuation.NEXT_JORNADAS  # jornadas past the lock each replayed forecast is scored on
RATE = 0.6
SALES_FROM = "2026-08-25"
FABRICATED_FROM = "2026-11-20T19:40Z"


class GradeCell(NamedTuple):
    """The forecast graded at the lock of from_j: what was expected of a player in a jornada (predicted), his chance of
    playing and whether he was flagged (status not None); a grade_cell record without its run."""
    from_j: int
    jornada: int
    player: str
    predicted: float
    plays: float | None
    flagged: int


class LockForecast(NamedTuple):
    """The forecast at a moment as grading reads it: the cell of every player and jornada left, and the players flagged
    (a PlayerStatus with a status)."""
    cells: tuple[forecast_facts.Cell, ...]
    flagged: frozenset[str]


def lock_forecast(loaded: loader.Loaded, world: state.World) -> LockForecast:
    """The LockForecast of the world: forecast_facts.cell of its inputs, and who has a status (rule flagged_is_status_not_null)."""
    inp = forecast_facts.inputs(world, loaded.identity, loaded.knobs)
    return LockForecast(forecast_facts.cell(inp), frozenset(k for k, s in inp.status.items() if s.status is not None))


def lock_moments(now: Season) -> dict[int, dt.datetime]:
    """Where the forecast of each done jornada's lock is read, by jornada in lock order: a minute before the lock (rule
    lock_forecast_is_a_minute_before)."""
    return {j: when - MINUTE for j, when in now.locks.items() if j in now.done}


def lock_cells(i: int, ahead: int, done: set[int], fc: LockForecast) -> list[GradeCell]:
    """The GradeCells of the forecast at the lock of jornada i: a row for each player and jornada from i to i + ahead
    that is done; flagged is 1 for a player in fc.flagged."""
    return [GradeCell(i, c.jornada, c.player, c.e, c.p, int(c.player in fc.flagged))
            for c in fc.cells if i <= c.jornada <= i + ahead and c.jornada in done]


def backtest(loaded: loader.Loaded, now: Season, ahead: int = 0) -> list[GradeCell]:
    """The forecast at each past lock (lock_moments, read in one sweep) as GradeCells (lock_cells) for the jornada it
    locks and, with ahead, the next ones, each one done; in lock order."""
    moments = lock_moments(now)
    got = moments_facts(loaded, moments.values(), lambda world: lock_forecast(loaded, world), LOCK_FOLDS)
    return [c for j, moment in moments.items() for c in lock_cells(j, ahead, now.done, got[moment])]


class OpportunityGrade(NamedTuple):
    """An opportunity of a cut graded: the market player, his rank among the cut's gains, the peers (same position, ask
    within 30%) and jornadas compared, the forecast gain over the player he replaces, what was realised and the mean of
    the peers' realised (None with no peer)."""
    cut: str
    player: str
    rank: int
    n_peers: int
    jornadas: int
    forecast: float
    realised: float
    control: float | None


def graded_opportunities(cut: str, inp: decision.Inputs, now: Season, top: int, span: int) -> list[OpportunityGrade]:
    """The first top market opportunities with a gain of the decision's inputs, each over the span jornadas from its
    from_j that are done (none if no one is, or it replaces no one), against the points scored (Season.scored)."""
    pos, cells = decision.cells_of(inp)
    got = decision.opportunity_rows(inp)
    band = [(c.player, c.pos, c.asking if c.asking is not None else c.value) for c in inp.universe if c.player in pos]
    out = []
    for rank, r in enumerate([r for r in got if r["status"] == "market" and r["gain"] > 0][:top], 1):
        span_j = [j for j in range(r["from_j"], r["from_j"] + span) if j in now.done]
        if not span_j or r["replaces"] is None:
            continue
        gone, k = r["replaces"], r["player"]
        lost = points(now.scored, gone, span_j)
        peers = [b for b, p, pr in band if p == r["pos"] and b != k and pr and abs(pr / r["asking"] - 1) <= 0.3]
        out.append(OpportunityGrade(
            cut, k, rank, len(peers), len(span_j),
            sum(cells.get(j, {}).get(k, 0.0) - cells.get(j, {}).get(gone, 0.0) for j in span_j),
            points(now.scored, k, span_j) - lost,
            sum(points(now.scored, b, span_j) for b in peers) / len(peers) - lost if peers else None))
    return out


def opportunity_grade(loaded: loader.Loaded, now: Season, cuts: Sequence[str], top: int = 5, span: int = 4) -> list[OpportunityGrade]:
    """graded_opportunities of the decision at each cut that has one (not Stale), ascending, from one streamed replay."""
    out: list[OpportunityGrade] = []

    def keep(cut: str, decided: decision.Decided | decision.Stale) -> None:
        if isinstance(decided, decision.Decided):
            out.extend(graded_opportunities(cut, decided.inputs, now, top, span))
    decision.replay(loaded, cuts, keep)
    return out


def opportunity_report(got: Sequence[OpportunityGrade]) -> str:
    """A table, a line for each OpportunityGrade, and a summary line: cases, the means of forecast, realised and control,
    realised less control with its standard error, and the shares beating control and above 0."""
    import statistics as st

    def mean(xs: list[float]) -> float:
        return st.mean(xs) if xs else float("nan")

    def sem(xs: list[float]) -> float:
        return st.stdev(xs) / len(xs) ** 0.5 if len(xs) > 1 else float("nan")
    lines = ["%-15s %-4s %3s %5s %9s %9s %9s" % ("cut", "rank", "n", "jorn", "forecast", "realised", "control")]
    lines += ["%-15s %-4d %3d %5d %9.1f %9.1f %9s" % (g.cut, g.rank, g.n_peers, g.jornadas, g.forecast, g.realised,
                                                    "-" if g.control is None else "%.1f" % g.control) for g in got]
    both = [(g.realised, g.control) for g in got if g.control is not None]
    diff = [realised - control for realised, control in both]
    lines.append("cases %d (with a control %d), cuts %d: mean forecast %.1f, realised %.1f, control %.1f; realised less control %+.1f (se %.1f), "
                 "share beating control %.0f%%, realised above 0 in %.0f%%" % (
                     len(got), len(both), len({g.cut for g in got}), mean([g.forecast for g in got]),
                     mean([g.realised for g in got]), mean([control for _, control in both]), mean(diff), sem(diff),
                     100 * mean([float(d > 0) for d in diff]), 100 * mean([float(g.realised > 0) for g in got])))
    return "\n".join(lines)


def standing_grades(sha: str) -> npt.NDArray[Any]:
    """The grade_cell array that stands for the code sha (rule grades_stand_from_the_latest_graded_run): that of its
    latest published run with any cell, an earlier run's of the same code being superseded; empty when it has none."""
    for run in sorted((r for r in store.runs() if r.commit_sha == sha), key=lambda r: r.run_id, reverse=True):
        cells = store.grade_cells(run.run_id)
        if len(cells):
            return cells
    return np.empty(0, dtype=rec.GRADE_CELL_DTYPE)


def graded(sha: str, last: int) -> bool:
    """Whether a published run of the code sha graded the past locks on every jornada done up to last (rule
    graded_covers_the_jornadas_done: the jornada of its standing grades reaches last): its grades stand, and nothing
    would change them."""
    cells = standing_grades(sha)
    return bool(len(cells)) and int(cells["jornada"].max()) >= last


class Results(NamedTuple):
    """The auction outcomes seen as of t: how many, how many of them sold, and when the newest closed."""
    n: int
    sold: int
    newest: str | None


def summarised(results: Iterable[auction_facts.AuctionResult]) -> Results:
    """The Results of auction results: n all of them, sold those with a won, newest the largest raw close text (None with
    none; rule results_count_sold_newest)."""
    held = list(results)
    return Results(len(held), sum(r.won is not None for r in held), max((r.close for r in held), default=None))


def results_seen(loaded: loader.Loaded, moment: dt.datetime) -> Results:
    """The Results (summarised) of auction_facts.auction_results in the world of the moment's minute, folding only the
    facts auction_results reads (RESULT_FOLDS)."""
    return state.sweep(loaded.obs, [stamp(moment)], lambda world: summarised(
        auction_facts.auction_results(world, loaded.identity, loaded.league)), RESULT_FOLDS)[0]


def scored_at(sha: str) -> str | None:
    """When (as_of) the latest published run of the code sha that recorded the auction scores (an auction_brier row) was
    made (rule scored_at_is_the_latest_scoring_run); None if none was."""
    scoring = {b.run_id for b in store.records("auction_brier")}
    return max((r.as_of for r in store.runs() if r.commit_sha == sha and r.run_id in scoring), default=None)


def auctions_scored(sha: str, loaded: loader.Loaded, now: Results) -> bool:
    """Whether an earlier run of the code sha scored the auctions on the outcomes seen now: its scores stand, as
    no outcome has come since (the results as they stood at that run's cut equal now)."""
    then = scored_at(sha)
    if then is None or not now.n:
        return False
    when = snapshot_stamp(then)
    return when is not None and results_seen(loaded, when) == now


class CutRead(NamedTuple):
    """What the auction scoring reads at one cut: the forecast of the open auctions and, at the last cut only, the looks
    and the results."""
    forecast: list[auction.Forecast]
    looks: list[auction.Look]
    results: tuple[auction_facts.AuctionResult, ...]


class AuctionReads(NamedTuple):
    """What the auction scoring reads of the sweep: each cut's forecast and the looks and results at the last cut."""
    forecasts: dict[str, list[auction.Forecast]]
    looks: list[auction.Look]
    results: tuple[auction_facts.AuctionResult, ...]


def auction_reads(loaded: loader.Loaded, cuts: Sequence[str], moment: dt.datetime) -> AuctionReads:
    """In one sweep over the cuts not after the moment's minute and that minute, auction.forecast of the fixed table
    over each cut's open looks, and at the moment's minute its looks (auction.looks_of) and results; only the folds
    the auction facts need are run (AUCTION_FOLDS)."""
    last = stamp(moment)
    asked = sorted({*(c for c in cuts if c <= last), last})

    def read(world: state.World) -> CutRead:
        forecast = auction.forecast(auction_table.TABLE, auction.open_looks_of(world, loaded.identity, loaded.league))
        if world.cut != last:
            return CutRead(forecast, [], ())
        return CutRead(forecast, auction.looks_of(world, loaded.identity, loaded.league),
                       auction_facts.auction_results(world, loaded.identity, loaded.league))
    got = state.sweep(loaded.obs, asked, read, AUCTION_FOLDS)
    return AuctionReads({c: r.forecast for c, r in zip(asked, got)}, got[-1].looks, got[-1].results)


def record_auction(records: RunRecords, loaded: loader.Loaded, cuts: Sequence[str]) -> dict[str, int]:
    """Scores the auction forecast: backtest (an expanding fit over the closed looks) and live (the stated chance of
    each decision, auction_reads' forecasts of the fixed table at the cuts, against the results); calibration and Brier
    scores of both are added to the run's records, and their sizes and drifts returned."""
    reads = auction_reads(loaded, cuts, run_now())
    live = auction.live_points(reads.forecasts, reads.results)
    back = auction.backtest(reads.looks)
    cal = auction.calibration(back, "backtest:") + auction.calibration(live, "live:")
    brier = auction.briers(back, "backtest:") + auction.briers(live, "live:")
    records.add("auction_calibration", cal)
    records.add("auction_brier", brier)
    return {"auction_points": len(back), "auction_live": len(live), "auction_drift": sum(r["drift"] for r in cal)}


def record_grades(records: RunRecords, obs: ob.Observations | None = None) -> dict[str, int]:
    """The grade stage (run.py grade): replay each past lock with this code, and add every player forecast against
    what then happened to the run's records (one set a code stands: standing_grades), unless this code has graded them on
    every jornada done (graded); and score the auction forecast (record_auction), unless this code has on every auction
    closed (auctions_scored). obs is the parse cache as parsed.read gives it, read here when None."""
    loaded, cuts = read_storage(obs)
    moment = run_now()
    now = season(loaded)
    resolved = len(now.done)
    sha = records.commit_sha
    standing = bool(now.done) and graded(sha, max(now.done))
    scored = {} if auctions_scored(sha, loaded, results_seen(loaded, moment)) else record_auction(records, loaded, cuts)
    record_sales(loaded, records)
    if standing:
        print("  grade: this code's grades stand (every jornada done graded)")
        return {"resolved": resolved, "forecasts": 0, **scored}
    fc = backtest(loaded, now, GRADE_AHEAD)
    records.add("grade_cell", (c._asdict() for c in fc))
    forecasts = len({(c.from_j, c.jornada) for c in fc})
    print("  grade: %d forecasts over %d jornadas" % (forecasts, resolved))
    return {"resolved": resolved, "forecasts": forecasts, **scored}


ME = "miguel"
SIDES = {"buy": ("buy",), "sell": ("sell", "transfer")}


class Trade(NamedTuple):
    """A ledger row of a manager: the player, when it happened (with its offset), the amount and the manager (the buyer
    of a buy, the seller of a sale)."""
    player: str
    at: dt.datetime
    amount: float
    manager: str


class Season(NamedTuple):
    """What the grading reads of the world at the run's moment: every manager's ledger rows that name a player, which
    manager is me, each jornada's lock in lock order, the jornadas done, each player's points by jornada played, each
    player's position and the mean points of the players of a position that scored in a jornada (rule
    position_mean_counts_appearances)."""
    entries: tuple[ledger.LedgerEntry, ...]
    me: str
    locks: dict[int, dt.datetime]
    done: set[int]
    scored: dict[str, dict[int, float]]
    positions: Mapping[str, str]
    means: Mapping[tuple[str, int], float]


class MomentFacts(NamedTuple):
    """What the sale grading reads at one moment: each asked player's newest market value above 0, the players in the
    eleven the app had fielded, the players flagged (player_status with a status) and my players."""
    market: Mapping[str, float]
    eleven: frozenset[str]
    flagged: frozenset[str]
    squad: frozenset[str]


def position_means(scored: Mapping[str, Mapping[int, float]], positions: Mapping[str, str]) -> dict[tuple[str, int], float]:
    """For each (position, jornada) with a player of that position who scored in it, the mean of their points; a player
    with no position counts nowhere."""
    found: dict[tuple[str, int], list[float]] = {}
    for player, by_jornada in scored.items():
        if player in positions:
            for j, pts in by_jornada.items():
                found.setdefault((positions[player], j), []).append(pts)
    return {key: math.fsum(pts) / len(pts) for key, pts in found.items()}


def season_at(loaded: loader.Loaded, world: state.World) -> Season:
    """The Season of the world (rules ledger_rows_name_a_player, locks_in_lock_order, scored_is_points_played,
    position_mean_counts_appearances)."""
    rows = tuple(e for e in ledger.ledger(world, loaded.identity) if e.player is not None)
    locks = {r.jornada: when for r in sorted(season_facts.round_locks(world), key=lambda r: (r.lock, r.jornada))
             if (when := snapshot_stamp(r.lock)) is not None}
    scored: dict[str, dict[int, float]] = {}
    for p in season_facts.player_jornadas(world, loaded.identity):
        if p.pts is not None:
            scored.setdefault(p.player, {})[p.jornada] = p.pts
    positions = {c.player: c.pos for c in current.player_club(world) if c.pos}
    return Season(rows, loaded.league.me, locks, set(season_facts.jornadas_done(world)), scored, positions,
                  position_means(scored, positions))


def moments_facts(loaded: loader.Loaded, moments: Iterable[dt.datetime], read: Callable[[state.World], T],
                  folds: Mapping[str, state.Fold]) -> dict[dt.datetime, T]:
    """read(world) for each moment, in one sweep of the folds over their minute-floored stamps; a moment's result is that
    of its stamp, the same alone, in any subset or order of moments, and unchanged by rows appended after it."""
    asked = list(moments)
    return dict(zip(asked, state.sweep(loaded.obs, [stamp(m) for m in asked], read, folds)))


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
    return moments_facts(loaded, wanted, lambda world: facts_at(loaded, world, asked[world.cut]), MOMENT_FOLDS)


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


def trades(season: Season, side: str, since: str, who: str | None = None) -> list[Trade]:
    """The ledger rows of a side (SIDES: buy, or sell for a sale to the game or to a manager) from since on (a text
    comparison of the row's at), oldest first, each with its manager (the buyer, or the seller); only the rows of who
    (the manager's name) when given."""
    found = [Trade(str(e.player), dt.datetime.fromisoformat(e.at), e.amount, str(e.buyer if side == "buy" else e.seller))
             for e in season.entries if e.kind in SIDES[side] and e.at >= since]
    return sorted((t for t in found if who in (None, t.manager)), key=lambda t: (t.at, t.player))


def lock_after(at: dt.datetime, locks: Sequence[dt.datetime]) -> dt.datetime | None:
    """The first of the locks strictly after at, or None if none is."""
    return min((x for x in locks if x > at), default=None)


def buys_between(buys: Sequence[Trade], start: dt.datetime, end: dt.datetime | None) -> bool:
    """Whether a buy falls at or after start and before end (end None: no bound), so funds a sale made at start."""
    return any(start <= b.at and (end is None or b.at < end) for b in buys)


def sale_facts(sale: Trade, buys: Sequence[Trade], lock: dt.datetime | None,
               facts: Mapping[dt.datetime, MomentFacts]) -> salegrade.Sale:
    """The sale as the facts stood a minute before it (facts at that moment): his market value (the amount if none was
    seen), whether he was in my latest line-up seen, whether he was flagged, and whether a buy fell before the lock (lock
    None: no bound). The line-up is mine: the caller states in_eleven None for a sale of another manager's."""
    then = facts[sale.at - MINUTE]
    return salegrade.Sale(sale.player, sale.amount, then.market.get(sale.player) or sale.amount, sale.player in then.eleven,
                          sale.player in then.flagged, buys_between(buys, sale.at, lock))


class Sold(NamedTuple):
    """A sale with the moment it was made and who made it: ME, or m1, m2, ... for the other managers (the name never
    leaves the Season)."""
    at: dt.datetime
    sale: salegrade.Sale
    who: str


def aliases(sells: Iterable[Trade], me: str) -> dict[str, str]:
    """A label for each manager of the sales: ME for me, then m1, m2, ... for the others in order of most sales (a tie
    by name)."""
    count = Counter(t.manager for t in sells if t.manager != me)
    return {me: ME} | {m: "m%d" % i for i, m in enumerate(sorted(count, key=lambda m: (-count[m], m)), 1)}


def sold_since(season: Season, day: str, facts: Mapping[dt.datetime, MomentFacts]) -> list[Sold]:
    """Every manager's sales from the day on (a date, YYYY-MM-DD), oldest first, each with its moment, its label (aliases)
    and as a salegrade.Sale read at that moment (sale_facts); funded_buy is a buy of the same manager between the sale
    and the next lock. in_eleven is known for my sales only, else None."""
    sells = trades(season, "sell", day)
    label = aliases(sells, season.me)
    buys = trades(season, "buy", day)
    locks = sorted(season.locks.values())
    out = []
    for s in sells:
        sale = sale_facts(s, [b for b in buys if b.manager == s.manager], lock_after(s.at, locks), facts)
        out.append(Sold(s.at, sale if s.manager == season.me else sale._replace(in_eleven=None), label[s.manager]))
    return out


def horizon_jornadas(at: dt.datetime, locks: dict[int, dt.datetime], done: set[int]) -> dict[str, frozenset[int]]:
    """Per horizon of salegrade.HORIZONS, the done jornadas a sale at at is graded on: the horizons are the lock, next4 and
    season readers of the Window of the jornadas whose lock is after at. Each set is inside done, and a
    set is empty when no jornada of its horizon is done (or none follows the sale)."""
    window = valuation.Window(sorted(j for j in locks if locks[j] > at))
    spans = {name: set(getattr(window, name)) for name in valuation.READERS}
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


def course(sold: Sold, season: Season, fielded: dict[int, set[str]], facts: Mapping[dt.datetime, MomentFacts]) -> salegrade.Course | None:
    """What followed the sale at each horizon of horizon_jornadas: what he scored (sold); for an eleven sale what my
    players on the bench when he was sold (benched_at) scored in the jornadas they were fielded (fielded: the eleven at
    each lock), for a sale of unknown eleven membership the position's mean points over the horizon (Season.means), for a
    bench sale 0 (cover); and his latest market value at the lock of the horizon's last jornada (value_after, from facts;
    never the sale amount). None if no jornada is done at the lock horizon (then none is at any), if no market value of
    his was seen by then, or if his position is unknown and so is his membership."""
    spans = horizon_jornadas(sold.at, season.locks, season.done)
    if not spans["lock"]:
        return None
    player = sold.sale.player
    if sold.sale.in_eleven is None:
        position = season.positions.get(player)
        if position is None:
            return None
        cover = {h: math.fsum(season.means.get((position, j), 0.0) for j in spans[h]) for h in HORIZONS}
    else:
        bench = benched_at(sold.at - MINUTE, facts) if sold.sale.in_eleven else set()
        cover = {h: sum(points(season.scored, p, [j]) for j in spans[h] for p in bench & fielded[j]) for h in HORIZONS}
    after: dict[str, float] = {}
    for h in HORIZONS:
        value = facts[season.locks[max(spans[h])]].market.get(player)
        if value is None:
            return None
        after[h] = value
    return salegrade.Course({h: points(season.scored, player, spans[h]) for h in HORIZONS}, cover, after)


def graded_count(season: Season) -> int:
    """How many of my sales from SALES_FROM have a done jornada at their lock horizon, from the ledger alone."""
    return sum(1 for t in trades(season, "sell", SALES_FROM, season.me) if horizon_jornadas(t.at, season.locks, season.done)["lock"])


class Followed(NamedTuple):
    """A sale with the course that followed it."""
    sale: salegrade.Sale
    course: salegrade.Course


def followed(sold: Sequence[Sold], season: Season, fielded: dict[int, set[str]],
             facts: Mapping[dt.datetime, MomentFacts]) -> list[Followed]:
    """The sales that have a course (course); a sale with none is left out."""
    courses = [(s.sale, course(s, season, fielded, facts)) for s in sold]
    return [Followed(sale, got) for sale, got in courses if got is not None]


def graded_sales(sold: Sequence[Sold], season: Season, fielded: dict[int, set[str]],
                 facts: Mapping[dt.datetime, MomentFacts]) -> list[salegrade.Graded]:
    """The sales that have a course, each graded at RATE (salegrade.grade); their in_eleven is known."""
    return [salegrade.grade(f.sale, f.course, RATE) for f in followed(sold, season, fielded, facts)]


def proxied_sales(sold: Sequence[Sold], season: Season, fielded: dict[int, set[str]],
                  facts: Mapping[dt.datetime, MomentFacts]) -> list[salegrade.Readings]:
    """The sales that have a course once their eleven membership is taken as unknown (a sale of mine too), each as its
    two readings at RATE (salegrade.readings): benched and in the eleven."""
    unknown = [s._replace(sale=s.sale._replace(in_eleven=None)) for s in sold]
    return [salegrade.readings(f.sale, f.course, RATE) for f in followed(unknown, season, fielded, facts)]


class Scored(NamedTuple):
    """A Score of a group of sales: miguel, a rival (m1, m2, ...), rivals, league, or a rule."""
    group: str
    score: salegrade.Score


def known_scores(group: str, sold: Sequence[Sold], season: Season, fielded: dict[int, set[str]],
                 facts: Mapping[dt.datetime, MomentFacts]) -> list[Scored]:
    """The Scores (salegrade.scores: all, bench, eleven) of the group's sales, whose eleven membership is known."""
    return [Scored(group, x) for x in salegrade.scores(graded_sales(sold, season, fielded, facts))]


def league_scores(sold: Sequence[Sold], season: Season, fielded: dict[int, set[str]],
                  facts: Mapping[dt.datetime, MomentFacts]) -> list[Scored]:
    """The proxy Scores (salegrade.proxy_scores) of each rival, m1 on, then of all rivals and of the league (every
    manager's sales, mine too, taken as unknown)."""
    rivals = [s for s in sold if s.who != ME]
    groups = {who: [s for s in rivals if s.who == who] for who in sorted({s.who for s in rivals})}
    groups |= {"rivals": rivals, "league": list(sold)}
    return [Scored(group, x) for group, members in groups.items()
            for x in salegrade.proxy_scores(proxied_sales(members, season, fielded, facts))]


def sale_scores(sold: Sequence[Sold], season: Season, fielded: dict[int, set[str]],
                facts: Mapping[dt.datetime, MomentFacts]) -> list[Scored]:
    """The Scores of my sales (known_scores, group miguel) then of the league's (league_scores): the sale_score records."""
    mine = [s for s in sold if s.who == ME]
    return [*known_scores(ME, mine, season, fielded, facts), *league_scores(sold, season, fielded, facts)]


def fabricated(cut: str, first_fielded: str | None) -> bool:
    """Whether a dec_offer row of the cut (a stamp, 2026-11-20T1940Z) with no `fields` states a made-up loss: the cut is at or after
    FABRICATED_FROM and before first_fielded, the first cut of a row that carries `fields` (None: no row does yet)."""
    return stamped(cut) >= FABRICATED_FROM and (first_fielded is None or cut < first_fielded)


def sound_offers(rows: Iterable[rec.DecOffer]) -> tuple[list[rec.DecOffer], int]:
    """The dec_offer rows to grade on and the number left out: a row without `fields` that `fabricated` says is made up, by its
    cut, the first row with `fields` being the earliest of the rows given. A row with `fields` is always kept. The store is not
    edited."""
    held = list(rows)
    first = min((r.cut for r in held if r.fields is not None), default=None)
    kept = [r for r in held if r.fields is not None or not fabricated(r.cut, first)]
    return kept, len(held) - len(kept)


def read_storage(obs: ob.Observations | None = None) -> tuple[loader.Loaded, list[str]]:
    """The storage read once (obs when given: the parse cache as parsed.read gives it): the Loaded the sweeps run over and
    the cuts a replay runs at (loader.replay_cuts from SALES_FROM)."""
    loaded = loader.load(league.load_config(), obs=obs)
    return loaded, loader.replay_cuts(loaded.obs, SALES_FROM)


def season(loaded: loader.Loaded) -> Season:
    """The Season at the run's moment (clock.run_now, LFG_NOW when pinned), folding only the folds season_at reads
    (SEASON_FOLDS)."""
    return state.sweep(loaded.obs, [stamp(run_now())], lambda world: season_at(loaded, world), SEASON_FOLDS)[0]


def latest_values(measure: str) -> dict[str, float]:
    """The newest logged value of each key of the measure (records.latest over the measure_log of every published run)."""
    newest = rec.latest((r for r in store.records("measure_log") if r.measure == measure), lambda r: r.key)
    return {pair.last.key: pair.last.value for pair in newest.values()}


def record_sales(loaded: loader.Loaded, records: RunRecords) -> None:
    """Every manager's sales from SALES_FROM with a done jornada at their lock, graded at RATE, are added to the run's
    records as sale_score (sale_scores: my sales by case, then each rival's, all rivals' and the league's by proxy), whatever
    the run before held; and my sales summarised (salegrade.measure_values) are added to the measure_log as sale_grade with
    jornadas_done, unless the latest one logged has the same all.lock.n (graded_count) and jornadas_done. loaded is the
    storage the caller has read (read_storage)."""
    now = season(loaded)
    facts = facts_for(loaded, wanted_moments(trades(now, "sell", SALES_FROM), now.locks, now.done))
    fielded = fielded_at_locks(now.locks, now.done, facts)
    sold = sold_since(now, SALES_FROM, facts)
    records.add("sale_score", [{"group": x.group, **x.score._asdict()} for x in sale_scores(sold, now, fielded, facts)])
    marks = {"all.lock.n": float(graded_count(now)), "jornadas_done": float(len(now.done))}
    latest = latest_values("sale_grade")
    if all(latest.get(k) == v for k, v in marks.items()):
        return
    mine = [s for s in sold if s.who == ME]
    values = salegrade.measure_values(salegrade.summary(graded_sales(mine, now, fielded, facts)))
    log_measure(records, "sale_grade", values | {"jornadas_done": marks["jornadas_done"]})


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


def first_sales(cuts: Sequence[Cut], rules: Mapping[str, worth.Rule]) -> dict[str, list[Choice]]:
    """For each rule, in cut order, the sales it makes: an offer is accepted when the rule states a reserve and the offer
    reaches it. A rule sells a player at most once, at the first cut where it accepts an offer on him; a rule with no
    reserve sells nothing."""
    out: dict[str, list[Choice]] = {name: [] for name in rules}
    for cut in cuts:
        for name, rule in rules.items():
            gone = {c.player for c in out[name]}
            for stood in cut.standings:
                if stood.player in gone or stood.offer is None:
                    continue
                reserve = rule(stood, cut.ctx).reserve
                if reserve is not None and stood.offer >= reserve:
                    out[name].append(Choice(stood.player, cut.at, stood.offer))
                    gone.add(stood.player)
    return out


def offers_at(world: state.World) -> worth.OfferDistribution:
    """The distribution of offer over value (worth.offer_ratio_distribution) of the market offers the world has seen: each
    was created before it was first observed, so none is created after the cut."""
    return worth.offer_ratio_distribution(x.ratio for x in current.offer_ratios(world))


def replayed_cut(decided: decision.Decided | decision.Stale, offers: worth.OfferDistribution = worth.NO_OFFERS,
                 rate: float = hurdle.FALLBACK_LAMBDA) -> Cut | None:
    """The Cut of a decision that has standing offers on my players (rule replayed_cut_needs_offers: its extras'
    offers), so the context (with the offers distribution and exchange rate given) and the later sale prices are those of
    the cut; None for a Stale cut or one with no offer."""
    at = None if isinstance(decided, decision.Stale) else snapshot_stamp(decided.cut)
    if isinstance(decided, decision.Stale) or at is None or not decided.extras.offers:
        return None
    standings = [standing(decided.inputs, o.player, o.value, o.money) for o in decided.extras.offers]
    return Cut(at + MINUTE, context(decided.inputs, offers, rate, decision.clock_of(decided.inputs, decided.cut).n_closes), standings)


def replayed_cuts(loaded: loader.Loaded, cuts: Sequence[str]) -> list[Cut]:
    """The Cut (replayed_cut) of each cut that has one, ascending, from one streamed replay, each with the offers seen by
    its cut (offers_at) and the exchange rate at it (hurdle.lam over the opportunity records kept before it); only the
    Cuts are kept."""
    out: list[Cut] = []
    history = loader.daily_opportunities(hurdle.SINCE, max(cuts, default=hurdle.SINCE))

    def keep(world: state.World) -> None:
        decided = decision.decision_at(loaded, world)
        if isinstance(decided, decision.Stale):
            return
        _, cells = decision.cells_of(decided.inputs)
        cut = replayed_cut(decided, offers_at(world), hurdle.lam(history, world.cut, max(cells, default=0)))
        if cut is not None:
            out.append(cut)
    state.sweep(loaded.obs, cuts, keep)
    return out


class RuleScores(NamedTuple):
    """What rule_scores grades: the Scores, and the sales behind them by group (ME and each rule), first sale of a player
    first."""
    scores: list[Scored]
    graded: dict[str, list[salegrade.Graded]]


def rule_scores(rules: Mapping[str, worth.Rule], since: str = SALES_FROM) -> RuleScores:
    """On demand, never in the grade stage: my real sales from since (group miguel) and each rule's (first_sales over
    replayed_cuts at the replay cuts from since) as Scores by case, then the league's proxy Scores (league_scores), with
    the graded sales of me and of each rule; every sale graded by the same course at RATE."""
    loaded, cuts = read_storage()
    now = season(loaded)
    chosen = first_sales(replayed_cuts(loaded, [c for c in cuts if c >= since and snapshot_stamp(c) is not None]), rules)
    made = {name: [Trade(c.player, c.at, c.amount, now.me) for c in got] for name, got in chosen.items()}
    facts = facts_for(loaded, wanted_moments([*trades(now, "sell", since), *(t for ts in made.values() for t in ts)], now.locks, now.done))
    fielded = fielded_at_locks(now.locks, now.done, facts)
    sold = sold_since(now, since, facts)
    ruled = {name: [Sold(t.at, sale_facts(t, [], None, facts), ME) for t in ts] for name, ts in made.items()}
    groups = {ME: [s for s in sold if s.who == ME], **ruled}
    return RuleScores([*(x for group, members in groups.items() for x in known_scores(group, members, now, fielded, facts)),
                       *league_scores(sold, now, fielded, facts)],
                      {group: graded_sales(members, now, fielded, facts) for group, members in groups.items()})


PAIRED_RULE = "rent"
PAIRED_CASE = "bench"


def paired_report(graded: Mapping[str, Sequence[salegrade.Graded]]) -> str:
    """The paired comparison of PAIRED_RULE with my sales on the PAIRED_CASE subset (salegrade.paired, per horizon: the
    total difference D with n_U, its pieces and its bootstrap 90% interval), each side's total per million raised, and the
    gate verdict line (salegrade.gate)."""
    rule, mine = (salegrade.first_per_player([g for g in graded[who] if PAIRED_CASE in salegrade.cases(g.sale)]) for who in (PAIRED_RULE, ME))
    pairs = [salegrade.paired(rule, mine, h) for h in HORIZONS]
    sides = {who: [salegrade.score(PAIRED_CASE, side, h) for h in HORIZONS] for who, side in ((PAIRED_RULE, rule), (ME, mine))}
    lines = ["PAIRED %s minus %s, %s sales only, first sale of each player, net 0 where a side did not sell him" % (
        PAIRED_RULE, ME, PAIRED_CASE)]
    for p in pairs:
        lines.append("  D(%-6s) %+8.1f  n_U %d  both %d: %+.1f  only %s %d: %+.1f  only %s %d: %+.1f  bootstrap 90%% %.1f..%.1f" % (
            p.horizon, p.total, p.n, p.both.n, p.both.net, PAIRED_RULE, p.only_rule.n, p.only_rule.net, ME, p.only_other.n,
            p.only_other.net, p.low, p.high))
    for who, scored in sides.items():
        lines.append("  %-12s n %d  per million raised  %s" % (who, scored[0].n, "  ".join("%s %.3f" % (s.horizon, s.per_million) for s in scored)))
    verdict = salegrade.gate(pairs, sides[PAIRED_RULE], sides[ME])
    lines.append("GATE %s: %s" % ("PASS" if verdict.passed else "FAIL", "; ".join(verdict.reasons)))
    return "\n".join(lines)


def rule_report(scores: Sequence[Scored]) -> str:
    """A table, a line for each Score: group, case, horizon, n, total net points, net points per million, the total
    with one sale left out (min..max) and its bootstrap interval. Every line is the same length."""
    head = ("group", "case", "horizon", "n", "net", "net/M", "leave-one-out", "bootstrap 95%")
    body = [(x.group, x.score.case, x.score.horizon, str(x.score.n), "%.1f" % x.score.total_net, "%.2f" % x.score.per_million,
             "%.1f..%.1f" % (x.score.loo_min, x.score.loo_max), "%.1f..%.1f" % (x.score.bootstrap_lo, x.score.bootstrap_hi))
            for x in scores]
    widths = [max(len(r[i]) for r in [head, *body]) for i in range(len(head))]
    return "\n".join("  ".join(c.ljust(w) if i < 3 else c.rjust(w) for i, (c, w) in enumerate(zip(r, widths)))
                     for r in [head, *body])


def log_measure(records: RunRecords, measure: str, values: dict[str, float]) -> None:
    """The headline numbers of a measure added to the run's measure_log, beside the code's short commit (latest_values
    gives each key's newest)."""
    import subprocess
    sha = subprocess.run(["git", "-C", str(Path(__file__).parent), "rev-parse", "--short", "HEAD"],
                         capture_output=True, text=True).stdout.strip() or "unknown"
    at = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    records.add("measure_log", [{"at": at, "commit_sha": sha, "measure": measure, "key": k, "value": v} for k, v in values.items()])


class GradeRow(NamedTuple):
    """A graded forecast, a player a row: a GradeCell with the points he scored in the jornada (0.0 with none; rule
    actual_is_zero_without_points)."""
    from_j: int
    jornada: int
    player: str
    predicted: float
    plays: float | None
    actual: float
    flagged: int


class Accuracy(NamedTuple):
    """The forecast's accuracy for a group (grp: all, or star: predicted at 4 or more; rule star_is_predicted_four_or_more)
    at a distance (ahead: jornadas past the lock): n, RMSE, MAE, bias (predicted less actual), the correlation r and the
    slope of actual on predicted; r and slope are None where the variance makes them undefined."""
    grp: str
    ahead: int
    n: int
    rmse: float
    mae: float
    bias: float
    r: float | None
    slope: float | None


STAR = 4.0


def grade_rows(cells: Iterable[GradeCell], scored: Mapping[str, Mapping[int, float]]) -> list[GradeRow]:
    """Each GradeCell against what its player scored in its jornada (points of a Season's scored)."""
    return [GradeRow(c.from_j, c.jornada, c.player, c.predicted, c.plays, scored.get(c.player, {}).get(c.jornada, 0.0), c.flagged)
            for c in cells]


def accuracy_of(grp: str, ahead: int, graded_rows: Sequence[GradeRow]) -> Accuracy:
    """The Accuracy of rows, predicted against actual, as the sums of their squares and products say it."""
    xs, ys = [r.predicted for r in graded_rows], [r.actual for r in graded_rows]
    n = len(xs)

    def mean(vs: Iterable[float]) -> float:
        return math.fsum(vs) / n
    mx, my = mean(xs), mean(ys)
    cov = mean(x * y for x, y in zip(xs, ys)) - mx * my
    vx, vy = mean(x * x for x in xs) - mx * mx, mean(y * y for y in ys) - my * my
    spread = vx * vy
    return Accuracy(grp, ahead, n, math.sqrt(mean((x - y) * (x - y) for x, y in zip(xs, ys))), mean(abs(x - y) for x, y in zip(xs, ys)),
                    mean(x - y for x, y in zip(xs, ys)), cov / math.sqrt(spread) if spread > 0 else None, cov / vx if vx != 0 else None)


def accuracy_rows(graded_rows: Iterable[GradeRow]) -> list[Accuracy]:
    """The Accuracy of the all group and of the star group (predicted at least STAR) at each ahead (jornada less from_j)
    that has a row, ordered star first, then by ahead."""
    groups: dict[tuple[str, int], list[GradeRow]] = {}
    for r in graded_rows:
        groups.setdefault(("all", r.jornada - r.from_j), []).append(r)
        if r.predicted >= STAR:
            groups.setdefault(("star", r.jornada - r.from_j), []).append(r)
    return sorted((accuracy_of(grp, ahead, got) for (grp, ahead), got in groups.items()), key=lambda a: (a.grp != "star", a.ahead))


def accuracy(ahead: int = 5) -> list[Accuracy]:
    """The forecast at each lock (backtest) graded as a run of its own (trigger 'accuracy', published with its cells as
    grade_cell records and its numbers as measure_log): the Accuracy at the next jornada and at ahead, the star group first."""
    loaded, _ = read_storage()
    now = season(loaded)
    cells = backtest(loaded, now, ahead)
    records = RunRecords.after_latest("")
    records.add("grade_cell", [c._asdict() for c in cells])
    got = [a for a in accuracy_rows(grade_rows(cells, now.scored)) if a.ahead in (0, ahead)]
    log_measure(records, "accuracy", {"%s%s@%d" % ("" if a.grp == "all" else "star_", k, a.ahead): v for a in got
                                      for k, v in (("n", a.n), ("rmse", a.rmse), ("mae", a.mae), ("bias", a.bias), ("r", a.r), ("slope", a.slope))
                                      if v is not None})
    records.publish(rec.Run(records.run, "", "", "accuracy", "", None, 1, None))
    return got


def price_grades(loaded: loader.Loaded) -> list[price_fold.Grade]:
    """price_fold.price_grade of the price state at the run's moment (folding only the price fold, PRICE_FOLDS), ordered by
    scope descending and h."""
    got = state.sweep(loaded.obs, [stamp(run_now())], lambda world: price_fold.price_grade(price_fold.price_state(world)), PRICE_FOLDS)[0]
    return sorted(sorted(got, key=lambda g: g.h), key=lambda g: g.scope, reverse=True)


def _season(entries: tuple[ledger.LedgerEntry, ...] = (), locks: dict[int, dt.datetime] | None = None, done: set[int] | None = None,
            scored: dict[str, dict[int, float]] | None = None, positions: Mapping[str, str] | None = None,
            means: Mapping[tuple[str, int], float] | None = None) -> Season:
    return Season(entries, "me", locks or {}, done or set(), scored or {}, positions or {}, means or {})


@contextlib.contextmanager
def _selftest_scratch_store() -> Iterator[None]:
    """A store under a scratch root (FF_ROOT) for the block, the environment restored after."""
    import tempfile
    kept = os.environ.get("FF_ROOT")
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["FF_ROOT"] = str(Path(tmp) / "data")
        try:
            yield
        finally:
            store.clear()
            if kept is None:
                os.environ.pop("FF_ROOT")
            else:
                os.environ["FF_ROOT"] = kept


def _published(run: int, sha: str, as_of: str = "t", **kinds: Any) -> None:
    """Publish run (of the code sha, made at as_of) with the kinds' rows, each a list of mappings."""
    records = RunRecords(run, sha)
    for kind, rows in kinds.items():
        records.add(kind, rows)
    records.publish(rec.Run(run, "t", as_of, "service", sha, "t", 1, 1.0))


def _selftest_measure_log() -> None:
    """Rule measure_latest_reader: latest_values gives, of the measure_log of the published runs, the newest value of each
    key of the measure asked, and only of that measure."""
    with _selftest_scratch_store():
        first = RunRecords(1, "a")
        log_measure(first, "accuracy", {"share": 0.25})
        (logged,) = first.rows("measure_log")
        assert logged.commit_sha != "unknown" and 4 <= len(logged.commit_sha) <= 12, logged
        assert latest_values("accuracy") == {}, "nothing published, nothing read"
        first.publish(rec.Run(1, "t", "t", "service", "a", "t", 1, 1.0))
        assert latest_values("accuracy") == {"share": 0.25}
        second = RunRecords(2, "a")
        log_measure(second, "accuracy", {"share": 0.5, "n": 3.0})
        log_measure(second, "sale_grade", {"share": 9.0})
        second.publish(rec.Run(2, "t", "t", "service", "a", "t", 1, 1.0))
        assert latest_values("accuracy") == {"share": 0.5, "n": 3.0} and latest_values("sale_grade") == {"share": 9.0}, \
            "the newest value of each key, of the measure asked"


def _selftest_stand() -> None:
    """With nothing new (every jornada graded, every auction scored) the grade stage enters neither replay; with a
    jornada or an auction new it enters that one."""
    entered: list[str] = []
    held = {"graded": True, "scored": True}

    def auction_scored(records: RunRecords, loaded: Any, cuts: Sequence[str]) -> dict[str, int]:
        entered.append("auction")
        return {}

    def regraded(loaded: Any, now: Season, ahead: int = 0) -> list[GradeCell]:
        entered.append("grades")
        return []
    stub: dict[str, Any] = {
        "read_storage": lambda obs=None: (None, []), "season": lambda loaded: _season(done={1, 2}),
        "results_seen": lambda loaded, moment: Results(1, 1, "c"),
        "graded": lambda sha, last: held["graded"], "auctions_scored": lambda sha, loaded, now: held["scored"],
        "record_auction": auction_scored, "backtest": regraded, "record_sales": lambda loaded, records: None}
    kept = {k: globals()[k] for k in stub}
    try:
        globals().update(stub)
        for g, a, want in ((True, True, []), (False, True, ["grades"]), (True, False, ["auction"]), (False, False, ["auction", "grades"])):
            held.update(graded=g, scored=a)
            entered.clear()
            record_grades(RunRecords(5, "c"))
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
        return Trade("x", at, 1.0, "me")
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
    now = _season(locks=locks, done=done, scored=scored, positions={"s": "MED"}, means={("MED", 1): 2.0, ("MED", 2): 5.0})
    assert course(Sold(day, sale, ME), now, fielded, facts_with({})) is None, "no value of his seen: not graded"
    facts = facts_with({1: 4e6, 2: 6e6, 3: 7e6})
    got = course(Sold(day, sale, ME), now, fielded, facts)
    assert got is not None
    assert got.value_after == {"lock": 4e6, "next4": 7e6, "season": 7e6}, \
        "his value at the lock of each horizon's last jornada, never the sale amount"
    assert got.cover == {"lock": 0.0, "next4": 6.0 + 1.0 + 2.0, "season": 6.0 + 1.0 + 2.0}, \
        "a and b count only in the jornadas they were fielded (c was never on the bench)"
    assert got.sold["season"] == 9.0
    other = course(Sold(day, sale._replace(in_eleven=False), ME), now, fielded, facts)
    assert other is not None and not any(other.cover.values()), "a bench sale has no cover"
    assert course(Sold(locks[3], sale, ME), now, fielded, facts) is None, "no jornada after the sale"
    unknown = sale._replace(in_eleven=None)
    proxy = course(Sold(day, unknown, "m1"), now, fielded, facts)
    assert proxy is not None and proxy.cover == {"lock": 2.0, "next4": 7.0, "season": 7.0}, \
        "an unknown sale's cover is the position's mean points over the horizon's jornadas"
    assert course(Sold(day, unknown, "m1"), now._replace(positions={}), fielded, facts) is None, "no position: not graded"


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
    """record_sales adds the sale scores to every run, the same rows when nothing is new, and logs the sales' measure
    once for a given number of graded sales and jornadas done: a second call adds no measure rows, a jornada done adds a
    set; a sale with no course is not graded."""
    at = dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc)
    locks = {1: at + dt.timedelta(days=2), 2: at + dt.timedelta(days=9)}
    held: dict[str, Any] = {"done": {1}}
    storage: Any = None
    entries = tuple(ledger.LedgerEntry(at.isoformat(), "sell", "me", None, "p", 5e6, None, "me", 0) for _ in range(2))
    sale = salegrade.Sale("p", 5e6, 5e6, True, False, False)
    sold = [Sold(at, sale, ME)] * 2 + [Sold(at, sale._replace(in_eleven=None), "m1")]
    flat = {h: 1.0 for h in HORIZONS}
    ok = salegrade.Course(flat, flat, flat)
    stub: dict[str, Any] = {
        "season": lambda loaded: _season(entries, locks, held["done"], positions={"p": "MED"}),
        "facts_for": lambda loaded, wanted: {}, "sold_since": lambda now, day, facts: sold,
        "course": lambda s, now, fielded, facts: ok if len(now.done) else None,
        "fielded_at_locks": lambda locks, done, facts: {}}
    kept = {k: globals()[k] for k in stub}

    def logged(run: int) -> tuple[int, list[Any]]:
        records = RunRecords(run, "a")
        record_sales(storage, records)
        records.publish(rec.Run(run, "t", "t", "service", "a", "t", 1, 1.0))
        return len(records.rows("measure_log")), records.rows("sale_score")
    with _selftest_scratch_store():
        try:
            globals().update(stub)
            first, scores = logged(1)
            assert first > 0 and latest_values("sale_grade")["all.lock.n"] == 2
            assert {r.group for r in scores} == {ME, "m1", "rivals", "league"} and all(r.run_id == 1 for r in scores)
            again, rows = logged(2)
            assert again == 0 and [r._replace(run_id=1) for r in rows] == scores, "nothing new: no measure rows, the same scores"
            held["done"] = {1, 2}
            assert logged(3)[0] == first, "a jornada done: a new set"
        finally:
            globals().update(kept)


def _selftest_league() -> None:
    """Every manager's sales are built by one function and graded by the same grader: each sale of every manager (to the game
    or to another manager) is there once, the labels hide the names (me first, the others by sale count), my eleven
    membership is known and a rival's is not, a buy funds only its own manager's sale, the league group is every sale, and
    the scores do not change with the order of the sales or by grading them twice."""
    at = dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc)
    lock = {1: at + dt.timedelta(days=3), 2: at + dt.timedelta(days=10)}

    def entry(day: int, kind: str, who: str, other: str | None, player: str, amount: float, buyer: str | None, seller: str | None) -> ledger.LedgerEntry:
        return ledger.LedgerEntry((at + dt.timedelta(days=day)).isoformat(), kind, who, other, player, amount, buyer, seller, 0)
    rows = (entry(0, "sell", "me", None, "a", 5e6, None, "me"), entry(0, "sell", "bob", None, "b", 6e6, None, "bob"),
            entry(1, "sell", "cat", None, "c", 4e6, None, "cat"), entry(1, "transfer", "cat", "bob", "d", 3e6, "cat", "bob"),
            entry(1, "sell", "bob", None, "e", 2e6, None, "bob"), entry(1, "buy", "bob", None, "f", 9e6, "bob", None),
            entry(-9, "sell", "bob", None, "g", 1e6, None, "bob"))
    now = _season(rows, lock, {1, 2}, {p: {1: 3.0, 2: 1.0} for p in "abcdef"}, dict.fromkeys("abcdef", "DEF"), {("DEF", 1): 1.0, ("DEF", 2): 2.0})
    got = trades(now, "sell", "2026-08-25")
    assert sorted((t.player, t.manager) for t in got) == [("a", "me"), ("b", "bob"), ("c", "cat"), ("d", "bob"), ("e", "bob")], \
        "a sale to the game and a transfer are each a sale of the seller, nothing before the day"
    assert [t.player for t in trades(now, "buy", "2026-08-25")] == ["f"] and not trades(now, "sell", "2026-08-25", "nobody")
    assert aliases(got, "me") == {"me": ME, "bob": "m1", "cat": "m2"}, "me first, the others by most sales"
    market = {"a": 5e6, "b": 6e6, "c": 4e6, "d": 3e6, "e": 2e6}
    facts = {t.at - MINUTE: MomentFacts(market, frozenset("a"), frozenset(), frozenset("a")) for t in got}
    facts |= {when: MomentFacts({p: 4e6 for p in "abcde"}, frozenset(), frozenset(), frozenset()) for when in lock.values()}
    sold = sold_since(now, "2026-08-25", facts)
    assert sorted((s.who, s.sale.player) for s in sold) == [("m1", "b"), ("m1", "d"), ("m1", "e"), ("m2", "c"), (ME, "a")]
    assert all((s.sale.in_eleven is None) == (s.who != ME) for s in sold) and next(s for s in sold if s.who == ME).sale.in_eleven
    assert next(s for s in sold if s.sale.player == "b").sale.funded_buy and not next(s for s in sold if s.sale.player == "c").sale.funded_buy, \
        "bob's buy funds bob's sale, not cat's"
    fielded = {1: set("a"), 2: set("a")}
    base = sale_scores(sold, now, fielded, facts)
    assert {x.group for x in base} == {ME, "m1", "m2", "rivals", "league"}
    by = {(x.group, x.score.case, x.score.horizon): x.score for x in base}
    assert by[(ME, "all", "lock")].n == 1 and by[("rivals", "bench-proxy", "lock")].n == 4 and by[("league", "bench-proxy", "lock")].n == 5, \
        "rivals' sales, and the league's with mine"
    for h in HORIZONS:
        assert by[("league", "eleven-proxy bound", h)].total_net != by[("league", "bench-proxy", h)].total_net
    assert sale_scores(sold, now, fielded, facts) == base, "grading twice gives the same rows"
    assert sale_scores(sold[::-1], now, fielded, facts) == base, "the order of the sales does not matter"
    assert sale_scores(sold + sold[:0], now._replace(positions={}), fielded, facts) != base, "a rival with no position is left out"
    assert position_means({"a": {1: 4.0}, "b": {1: 2.0, 2: 6.0}, "c": {1: 9.0}}, {"a": "X", "b": "X"}) == {("X", 1): 3.0, ("X", 2): 6.0}, \
        "the position's mean over the players who scored; a player with no position counts nowhere"


def _selftest_fabricated() -> None:
    def row(cut: str, fields: bool | None) -> rec.DecOffer:
        return rec.DecOffer(cut, "p", "1", "c", 1.0, 1.0, None, None, 0, 0.0, 0.0, fields, None if fields is None else "", "", None)
    assert stamped("2026-11-20T1940Z") == FABRICATED_FROM
    for first in (None, "2026-11-21T0900Z"):
        assert not fabricated("2026-11-20T1939Z", first) and fabricated("2026-11-20T1940Z", first), "from FABRICATED_FROM on"
        assert not fabricated("2026-10-01T0900Z", first), "before it nothing is excluded"
    assert fabricated("2026-11-21T0859Z", "2026-11-21T0900Z") and not fabricated("2026-11-21T0900Z", "2026-11-21T0900Z"), \
        "up to the first cut that carries fields"
    rows = [row("2026-11-19T1000Z", None), row("2026-11-20T1940Z", None), row("2026-11-20T2100Z", None), row("2026-11-21T0900Z", True),
            row("2026-11-21T0930Z", None), row("2026-11-21T1000Z", False)]
    kept, left = sound_offers(rows)
    assert left == 2 and kept == [rows[0], rows[3], rows[4], rows[5]], "the made-up rows between the two bounds are left out, those with fields never"
    assert sound_offers(rows[::-1])[0] == kept[::-1] and sound_offers([])[1] == 0, "the order of the rows does not matter"
    again, more = sound_offers(kept)
    assert again == kept and more == 0, "a second pass leaves nothing out"
    assert sound_offers(rows[:3])[1] == 2 and sound_offers(rows[:1])[1] == 0, "no row with fields yet: all from the bound on"


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
    ctx = worth.Context({}, valuation.Window(), 0.0)

    def rule(reserve: float | None) -> worth.Rule:
        return lambda standing, ctx: worth.Verdict(None, valuation.Reading(None, {}, ()), reserve)
    cases: list[list[list[str]]] = [[["a"], ["a", "b"], ["b", "a", "c"]], [["x"]], [[]]]
    for offers in cases:
        cuts = [Cut(at + dt.timedelta(days=i), ctx, [worth.Standing(p, hurdle.Exit(1e6), 1e6 * (1 + i)) for p in ps])
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
    scored = [Scored("r", x) for x in salegrade.scores(graded)]
    for h, row in zip(HORIZONS, [x.score for x in scored if x.score.case == "all"]):
        assert row.horizon == h and row.n == 3 and abs(row.total_net - 3 * graded[0].net[h]) < 1e-9
    empty = [Scored("a_long_rule_name", x) for x in salegrade.scores([])]
    assert all(x.score.n == 0 for x in empty), "no sales: n is 0"
    table = rule_report(scored + empty)
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
    streamed = replayed_cuts(loaded, _CUTS)
    assert [(c.at, c.standings) for c in streamed] == [(c.at, c.standings) for c in got], "the streamed replay keeps the same Cuts"
    assert all(c.ctx.window == d.ctx.window and c.ctx.rate > 0 for c, d in zip(streamed, got)), "the rate is positive"
    offered_at = [len(c.ctx.offers.ratios) for c in streamed]
    assert offered_at == sorted(offered_at), "offers are only added as the cuts go on: none from after a cut"
    with_offers = replayed_cut(offered[0], worth.offer_ratio_distribution([0.9, 1.1]), 0.25)
    assert with_offers is not None and with_offers.ctx.offers.ratios == (0.9, 1.1) and with_offers.ctx.rate == 0.25
    emptied = [d._replace(extras=d.extras._replace(offers=[])) if isinstance(d, decision.Decided) else d for d in decided]
    assert all(replayed_cut(d) is None for d in emptied), "no standing offer on my players: no Cut"
    assert replayed_cut(decision.Stale("old")) is None, "a Stale cut is skipped"


def _selftest_lock_forecast() -> None:
    """Rules lock_forecast_is_a_minute_before and flagged_is_status_not_null: the forecast graded for a lock is that of
    the world a minute before it, for the done jornadas from the lock's on up to ahead, and rows stamped at the lock are
    not seen; flagged players are those whose PlayerStatus has a status."""
    from ffcore.forecast_facts import _p, _tables
    tables = _tables()
    obs = ob.of(tables)
    loaded = loader.Loaded(obs, forecast_facts.IDENTITY, ledger.League("me", 0.0), forecast_facts.knobs(), ())
    lock = snapshot_stamp(forecast_facts.D3)
    assert lock is not None
    lock += MINUTE
    now = _season(locks={1: lock, 2: lock + dt.timedelta(days=7), 3: lock + dt.timedelta(days=14)}, done={1, 2})
    assert lock_moments(now) == {1: lock - MINUTE, 2: lock + dt.timedelta(days=7) - MINUTE}, \
        "a minute before the lock of each done jornada, and none for a jornada not done"
    got = backtest(loaded, now, 1)
    world = state.world_at(obs, stamp(lock - MINUTE))
    fc = lock_forecast(loaded, world)
    assert [c.from_j for c in got] == sorted(c.from_j for c in got) and got, "in lock order"
    assert [c for c in got if c.from_j == 1] == lock_cells(1, 1, now.done, fc), "the forecast of the world a minute before the lock"
    assert {c.jornada for c in got} <= {1, 2} and all(c.from_j <= c.jornada <= c.from_j + 1 for c in got)
    assert {c.jornada for c in backtest(loaded, now, 0)} <= {1, 2} and all(c.jornada == c.from_j for c in backtest(loaded, now, 0)), \
        "ahead 0 grades the jornada the lock is for"
    later = {**tables, "points": [*tables["points"], _p(stamp(lock), "q2", 50.0, 10.0, "26"),
                                  _p(stamp(lock), "q1", 50.0, 10.0, "26")]}
    after = state.world_at(ob.of(later), stamp(lock))
    assert lock_forecast(loaded, after) != lock_forecast(loaded, state.world_at(obs, stamp(lock))), "rows at the lock do change its forecast"
    assert [c for c in backtest(loaded._replace(obs=ob.of(later)), now, 1) if c.from_j == 1] == [c for c in got if c.from_j == 1], \
        "rows stamped at the lock are not seen"
    inp = forecast_facts.inputs(world, loaded.identity, loaded.knobs)
    assert fc.flagged == {k for k, x in inp.status.items() if x.status is not None} and fc.flagged, "flagged is status not None"
    assert any(x.status is None for x in inp.status.values()), "a player with no status is in the status rows and not flagged"
    assert all(c.flagged == int(c.player in fc.flagged) for c in got if c.from_j == 1)
    assert {c.jornada for c in lock_cells(1, 1, {2}, fc)} <= {2}, "a jornada not done is not graded"


def _selftest_fold_sets() -> None:
    """Each sweep of grading folds exactly the facts its reader reads: over the same rows the reader gives the same on the
    named folds as on all of them, and with any one of the named folds left out it reads a fact that is not there."""
    from ffcore.auction_facts import _scratch
    from ffcore.forecast_facts import _tables
    obs = ob.of(_tables())
    loaded = loader.Loaded(obs, forecast_facts.IDENTITY, ledger.League("me", 0.0), forecast_facts.knobs(), ())
    feed, identity, league = _scratch()
    readers = {
        "season": (obs, "market", SEASON_FOLDS, lambda world: season_at(loaded, world)),
        "lock": (obs, "market", LOCK_FOLDS, lambda world: lock_forecast(loaded, world)),
        "moment": (obs, "market", MOMENT_FOLDS, lambda world: facts_at(loaded, world, ["p1"])),
        "results": (feed, "api_market", RESULT_FOLDS, lambda world: summarised(
            auction_facts.auction_results(world, identity, league)))}
    for name, (rows, kind, named, read) in readers.items():
        end = max(stamp(snapshot_stamp(at) or dt.datetime.min) for at in ob.stamps_of(rows, kind))
        assert state.sweep(rows, [end], read, named) == state.sweep(rows, [end], read), name
        for dropped in named:
            rest = {n: f for n, f in named.items() if n != dropped}
            try:
                state.sweep(rows, [end], read, rest)
            except KeyError:
                continue
            raise AssertionError("%s does not read %s" % (name, dropped))


def _selftest_grades() -> None:
    """Rules actual_is_zero_without_points and star_is_predicted_four_or_more, and accuracy_rows against statistics:
    a player with no scored row has actual 0.0; star rows are those predicted at 4 or more; the figures are those of
    the definitions, a perfect forecast has rmse 0, r 1 and slope 1, and a constant forecast has no r or slope."""
    import random
    import statistics as stats
    cells = [GradeCell(7, 7 + i % 3, "p%d" % (i % 5), 0.5 * i, 0.5 if i % 2 else None, i % 2) for i in range(14)]
    scored = {"p1": {8: 3.0}, "p2": {9: 5.0, 7: 1.0}}
    got = grade_rows(cells, scored)
    assert [(g.from_j, g.jornada, g.player, g.predicted, g.plays, g.flagged) for g in got] == [
        (c.from_j, c.jornada, c.player, c.predicted, c.plays, c.flagged) for c in cells]
    assert all(g.actual == scored.get(g.player, {}).get(g.jornada, 0.0) for g in got) and any(g.actual == 0.0 for g in got) \
        and any(g.actual > 0 for g in got), "no scored row: actual 0.0"
    rng = random.Random(3)
    rows_ = [GradeRow(5, 5 + rng.randrange(4), "p%d" % i, rng.uniform(0, 8), None, rng.uniform(-2, 12), 0) for i in range(80)]
    by = {(a.grp, a.ahead): a for a in accuracy_rows(rows_)}
    assert [(a.grp, a.ahead) for a in accuracy_rows(rows_)] == sorted(by, key=lambda k: (k[0] != "star", k[1])), "star first, by ahead"
    for (grp, ahead), a in by.items():
        mine = [r for r in rows_ if r.jornada - r.from_j == ahead and (grp == "all" or r.predicted >= 4)]
        xs, ys = [r.predicted for r in mine], [r.actual for r in mine]
        assert a.n == len(mine) and abs(a.rmse - math.sqrt(stats.fmean((x - y) ** 2 for x, y in zip(xs, ys)))) < 1e-9
        assert abs(a.mae - stats.fmean(abs(x - y) for x, y in zip(xs, ys))) < 1e-9 and abs(a.bias - stats.fmean(x - y for x, y in zip(xs, ys))) < 1e-9
        assert a.r is not None and abs(a.r - stats.correlation(xs, ys)) < 1e-9 and a.slope is not None
        assert abs(a.slope - stats.linear_regression(xs, ys).slope) < 1e-9
        assert (grp == "star") <= all(x >= 4 for x in xs), "star_is_predicted_four_or_more"
    assert {a.n for a in by.values() if a.grp == "all"} == {sum(r.jornada - r.from_j == k for r in rows_) for k in range(4)}
    perfect = accuracy_rows([GradeRow(1, 1 + i % 2, "p", float(i), None, float(i), 0) for i in range(6)])
    assert all(a.rmse == 0.0 and a.bias == 0.0 for a in perfect)
    exact = accuracy_of("all", 0, [GradeRow(1, 1, "p", float(i), None, float(i), 0) for i in range(6)])
    assert exact.rmse == 0.0 and abs((exact.r or 0) - 1.0) < 1e-9 and abs((exact.slope or 0) - 1.0) < 1e-9
    flat = accuracy_of("all", 0, [GradeRow(1, 1, "p", 2.0, None, float(i), 0) for i in range(6)])
    assert flat.r is None and flat.slope is None, "no variance in the forecast: no correlation or slope"
    assert accuracy_rows([]) == [] and not [a for a in accuracy_rows([GradeRow(1, 1, "p", 3.9, None, 1.0, 0)]) if a.grp == "star"]


def _selftest_results() -> None:
    """Rule results_count_sold_newest: n counts all results, sold those with a won, newest is the largest raw close text
    (None for none); results_seen is that of the world a minute floor of the moment."""
    def result(close: str, won: float | None) -> auction_facts.AuctionResult:
        return auction_facts.AuctionResult("p", close, close, 1.0, auction_facts.LEAGUE_SELLER, won, None, None)
    assert summarised([]) == Results(0, 0, None)
    got = summarised([result("2026-08-18T10:00:00+00:00", None), result("2026-08-18T09:00:00+02:00", 5.0),
                      result("2026-08-17T23:00:00+00:00", 0.0)])
    assert got == Results(3, 2, "2026-08-18T10:00:00+00:00"), "newest is the largest close text, not the latest time"
    from ffcore.auction_facts import _CUTS, _scratch
    obs, ident, lg = _scratch()
    loaded = loader.Loaded(obs, ident, lg, forecast_facts.knobs(), ())
    for cut in _CUTS:
        moment = snapshot_stamp(cut)
        assert moment is not None
        world = state.sweep(obs, [cut], lambda w: w)[0]
        assert results_seen(loaded, moment + dt.timedelta(seconds=30)) == summarised(auction_facts.auction_results(world, ident, lg))
    assert results_seen(loaded, dt.datetime(2026, 8, 17, tzinfo=dt.timezone.utc)) == Results(0, 0, None)


def _selftest_opportunities() -> None:
    """The opportunities graded are the first top market ones with a gain: forecast is the gain over the player replaced
    on the span jornadas done, realised the points scored there, the control the peers' (same position, asking within
    30%) mean realised, None with no peer; a span with no jornada done grades nothing; a Stale cut grades nothing."""
    squad = {"k": "POR", "d1": "DEF", "d2": "DEF", "d3": "DEF", "m1": "MED", "m2": "MED", "m3": "MED", "m4": "MED",
             "f1": "DEL", "f2": "DEL", "f3": "DEL"}
    e = {**{k: 4.0 for k in squad}, "m4": 2.0, "star": 8.0, "near": 3.0, "far": 9.0}
    market = {"star": 8e6, "near": 8.4e6, "far": 20e6}
    inp = decision.Inputs(
        decision.Money(0.0, 0.0, 0.0, decision.SQUAD_MAX, decision.XI_SIZE), players=[decision.Player(k, p, 1, None) for k, p in squad.items()]
        + [decision.Player(k, "MED", 0, a) for k, a in market.items()],
        universe=[decision.Candidate(k, "MED", "market", a, a) for k, a in market.items()],
        cells=[decision.Cell(k, j, v) for k, v in e.items() for j in (1, 2)],
        locks=[decision.Lock(1, "2026-11-06T20:00Z"), decision.Lock(2, "2026-11-13T20:00Z")])
    now = _season(done={1, 2}, scored={"star": {1: 10.0, 2: 6.0}, "near": {1: 4.0, 2: 2.0}, "m4": {1: 1.0, 2: 1.0}, "far": {1: 20.0}})
    got = {g.player: g for g in graded_opportunities("c", inp, now, 5, 4)}
    assert sorted(g.rank for g in got.values()) == [1, 2, 3], "ranked among the cut's gains"
    star = got["star"]
    assert (star.jornadas, star.n_peers, star.forecast, star.realised, star.control) == (2, 1, 12.0, 14.0, 6.0 - 2.0), star
    assert got["far"].rank == 1 and got["far"].control is None, "most points first; no peer within 30% of his asking: no control"
    assert got["near"].control == 16.0 - 2.0 and got["near"].realised == 6.0 - 2.0
    assert {g.player for g in graded_opportunities("c", inp, now, 1, 4)} == {min(got.values(), key=lambda g: g.rank).player}, "only the top ones"
    assert [g.jornadas for g in graded_opportunities("c", inp, now._replace(done={2}), 5, 4)] == [1] * 3, "only jornadas done"
    assert graded_opportunities("c", inp, now._replace(done={9}), 5, 4) == [], "no jornada done in the span: nothing graded"
    assert [g.jornadas for g in graded_opportunities("c", inp, now, 5, 1)] == [1] * 3, "span limits the jornadas"
    alone = inp._replace(universe=[decision.Candidate("star", "MED", "market", 8e6, 8e6)])
    assert graded_opportunities("c", alone, now, 5, 4)[0].control is None, "no peer: no control"
    from ffcore.decision_facts import _CUTS, _scratch
    obs, ident, lg = _scratch()
    loaded = loader.Loaded(obs, ident, lg, forecast_facts.knobs(), ())
    decided = decision.decisions(loaded, _CUTS)
    assert any(isinstance(d, decision.Stale) for d in decided)
    want = [g for d in decided if isinstance(d, decision.Decided) for g in graded_opportunities(d.cut, d.inputs, now, 5, 4)]
    assert opportunity_grade(loaded, now, _CUTS) == want, "the streamed replay grades each cut that has a decision"


def _selftest_auction_reads() -> None:
    """auction_reads gives the forecast of the fixed table at each cut not after the moment and, at the moment's
    minute, the looks and the results; live scores are the live_points of those; a cut after the moment is not read."""
    from ffcore.decision_facts import _CUTS, _scratch
    obs, ident, lg = _scratch()
    loaded = loader.Loaded(obs, ident, lg, forecast_facts.knobs(), ())
    cuts = _CUTS
    moment = snapshot_stamp(cuts[1])
    assert moment is not None
    got = auction_reads(loaded, cuts, moment + dt.timedelta(seconds=40))
    assert sorted(got.forecasts) == [c for c in cuts if c <= cuts[1]], "the cuts not after the moment, and the moment"
    world = state.world_at(obs, cuts[1])
    assert got.looks == auction.looks_of(world, ident, lg) and got.results == auction_facts.auction_results(world, ident, lg)
    for cut in got.forecasts:
        assert got.forecasts[cut] == auction.forecast(auction_table.TABLE, auction.open_looks_of(state.world_at(obs, cut), ident, lg)), \
            "a cut alone gives the same forecast as inside the sweep"
    live = auction.live_points(got.forecasts, got.results)
    assert all(0.0 <= p.stated <= 1.0 and p.stated == p.pooled for p in live)


def _selftest_standing() -> None:
    """The ports of the SQL joins over the runs of a code (commit_sha): grades_stand_from_the_latest_graded_run (an earlier
    run's grades of the same code are superseded, another code's stay), graded_covers_the_jornadas_done and
    scored_at_is_the_latest_scoring_run (the latest published run of this code that scored, not another's)."""
    def cell(jornada: int) -> dict[str, Any]:
        return {"from_j": 7, "jornada": jornada, "player": "p", "predicted": 1.0, "plays": None, "flagged": 0}
    brier = {"scope": "live:", "n": 1, "model": 0.1, "pooled": 0.2}
    with _selftest_scratch_store():
        assert len(standing_grades("a")) == 0 and not graded("a", 1) and scored_at("a") is None, "no run, nothing stands"
        _published(1, "a", "2026-11-01T2210Z", grade_cell=[cell(7)], auction_brier=[brier])
        _published(2, "b", "2026-11-02T0000Z", grade_cell=[cell(9)])
        _published(3, "a", "2026-11-03T0000Z", grade_cell=[cell(7), cell(8)])
        _published(4, "a", "2026-11-04T0000Z")
        assert sorted(int(j) for j in standing_grades("a")["jornada"]) == [7, 8] and [int(j) for j in standing_grades("b")["jornada"]] == [9], \
            "grades_stand_from_the_latest_graded_run: the latest run of the code with cells; a run without cells supersedes nothing"
        assert graded("a", 8) and not graded("a", 9) and graded("b", 9) and not graded("c", 1), \
            "graded_covers_the_jornadas_done: this code graded up to the last jornada done; a jornada more, or other code, is not"
        assert scored_at("a") == "2026-11-01T2210Z" and scored_at("b") is None and scored_at("c") is None, \
            "scored_at_is_the_latest_scoring_run: the run of this code that scored the auctions, not another's"
        _published(5, "a", "2026-11-05T0000Z", auction_brier=[brier])
        assert scored_at("a") == "2026-11-05T0000Z", "the latest of the runs that scored"


def _selftest() -> None:
    _selftest_replayed_cuts()
    _selftest_rule_scores()
    _selftest_measure_log()
    _selftest_horizons()
    _selftest_fabricated()
    _selftest_points()
    _selftest_cover()
    _selftest_moment_facts()
    _selftest_record_sales()
    _selftest_league()
    _selftest_lock_forecast()
    _selftest_fold_sets()
    _selftest_grades()
    _selftest_results()
    _selftest_opportunities()
    _selftest_auction_reads()
    _selftest_standing()
    _selftest_stand()
    _selftest_window()
    print("grading self-test OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        raise SystemExit(0)
    if sys.argv[1:2] == ["accuracy"]:
        # accuracy: predicted e against actual points, next jornada and five ahead, at every past lock
        # (publishes a run of trigger 'accuracy' to the store)
        for a in accuracy():
            print("%-4s ahead %d  n=%d  rmse=%.4f  mae=%.4f  bias=%+.4f  r=%.4f  slope=%.3f" % (a.grp, a.ahead, a.n, a.rmse, a.mae, a.bias, a.r or 0, a.slope or 0), flush=True)
        os._exit(0)
    if sys.argv[1:2] == ["auction"]:
        loaded, _ = read_storage()
        recs = auction.backtest_records(state.sweep(loaded.obs, [stamp(run_now())], lambda world: auction.looks_of(
            world, loaded.identity, loaded.league), AUCTION_FOLDS)[0])
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
        loaded, _ = read_storage()
        for g in price_grades(loaded):
            fig = ["-" if v is None else "%.2f" % v for v in (g.rmse, g.rmse_none, g.rmse_again)]
            fig += ["-" if v is None else "%+.2f" % v for v in (g.predicted, g.actual)]
            print("%-5s h=%2d  n=%d  rmse=%s  none=%s  again=%s  predicted=%s  actual=%s" % (g.scope, g.h, g.n, fig[0], fig[1], fig[2], fig[3], fig[4]), flush=True)
        os._exit(0)
    if sys.argv[1:2] == ["opportunities"]:
        loaded, cuts = read_storage()
        print(opportunity_report(opportunity_grade(loaded, season(loaded), [c for c in cuts if snapshot_stamp(c) is not None])), flush=True)
        os._exit(0)
    if sys.argv[1:2] == ["rules"]:
        got = rule_scores(worth.CHALLENGERS)
        print(rule_report(got.scores), flush=True)
        print(paired_report(got.graded), flush=True)
        os._exit(0)
    print("grading.py --selftest | accuracy | price | opportunities | rules")
    raise SystemExit(2)
