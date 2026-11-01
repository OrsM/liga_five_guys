from __future__ import annotations

import datetime as dt
import math
import os
import sqlite3
import sys
import time
from collections.abc import Callable
from contextlib import closing
from functools import cache
from pathlib import Path
from statistics import NormalDist
from typing import Any, NamedTuple


from ffcore.forecast import expected_points
from ffcore.gain import Standings, standings
from ffcore.jornadas import round_locks
from ffcore.points import done_jornadas, jornada_points
from ffcore.pricing import RESALE_MARGIN
from ffcore.rules import DEBT_SHARE

from assemble import applied, built, forecast_key, kept_built, player_forecast, price_change, price_momentum, universe
import decision
from decide import Action, Universe
from sim import with_squad
from ffcore.clock import set_now, snapshot_stamp, stamp
from ffcore.db import connect, query, record, rows, this_run
__all__ = ["backtest", "clean_windows", "calibration_rows", "sim_calibration", "phantom_season", "replay", "decision_rows", "decisions", "phantom_path", "resales", "superseded", "view_table",
           "record_grades"]

REPLAY_VARIANT = "live"
GRADE_AHEAD = 4  # jornadas past the lock each replayed forecast is scored on


_AT: dict[tuple[dt.datetime, frozenset[str]], Universe | None] = {}


def universe_when(t: dt.datetime, also: frozenset[str] = frozenset()) -> Universe | None:
    """The universe at t (None if no board could be built then), with the
    clock set to it, forecasting also these players (assemble.built). Built
    once however many of the replays ask: a universe costs ~10s cold."""
    set_now(t)
    if (t, also) not in _AT:
        built.cache_clear()
        try:
            _AT[t, also] = universe(also)
        except SystemExit as e:
            print("%s: no board (%s)" % (t.isoformat(), e), file=sys.stderr)
            _AT[t, also] = None
    return _AT[t, also]


def on_market_by(t: dt.datetime) -> frozenset[str]:
    """Every player listed on the game's market up to t: whom a replay's
    phantom can hold then (whatever he bought, he bought from it)."""
    cut = stamp(t)
    return frozenset(k for k, first in _first_listed().items() if first <= cut)


@cache
def _first_listed() -> dict[str, str]:
    """When each player was first on the market: one read for every t
    on_market_by is asked (2026-10-31: a read a t was 9 of a backtest's 34 s).
    His first listing (api_market, where buys come from) or his first value
    (market), whichever is earlier: 9 players were listed before their first
    value (2026-10-31)."""
    return dict(rows("SELECT player, min(first) FROM ("
                     " SELECT ff_id AS player, min(observed_at) AS first FROM market WHERE ff_id <> '' GROUP BY ff_id"
                     " UNION ALL SELECT a.player, min(t.observed_at) FROM api_market t"
                     " JOIN app_player a ON a.app_id = t.player_id GROUP BY a.player)"
                     " GROUP BY player"))


def _built_at(job: tuple[dt.datetime, frozenset[str]]) -> tuple[dt.datetime, Universe | None]:
    t, also = job
    return t, universe_when(t, also)


def warm(times: list[dt.datetime], also: Callable[[dt.datetime], frozenset[str]]) -> None:
    """The universe at each of these times built ahead, WARM_JOBS at a time
    in their own processes, each forecasting also(t): a phantom's decisions
    then wait on nothing but his plans. A universe depends on nothing he
    did as long as also(t) holds whoever he can hold (on_market_by)."""
    import multiprocessing
    jobs = []
    for t in times:
        if (t, also(t)) in _AT:
            continue
        set_now(t)
        if (hit := kept_built(also(t))) is not None:     # kept: read here, no process to send it back
            _AT[t, also(t)] = hit.universe
        else:
            jobs.append((t, also(t)))
    if not jobs:
        return
    with multiprocessing.get_context("fork").Pool(WARM_JOBS) as pool:
        for t, u in pool.imap_unordered(_built_at, jobs):
            _AT[t, also(t)] = u


WARM_JOBS = 4


def universe_at(jornada: int, locks: dict[int, dt.datetime]) -> Universe | None:
    """The universe a minute before this jornada locked (universe_when):
    decisions, phantom_season and sim_calibration all stand at the locks."""
    return universe_when(locks[jornada] - dt.timedelta(minutes=1))


def auction_closes() -> list[dt.datetime]:
    """When each past night's auctions on the game's market closed (the
    listings' expiry), oldest first: a day's buying and selling is decided
    before it."""
    now = dt.datetime.now(dt.timezone.utc)
    return [t for (c, ) in rows("SELECT DISTINCT expires_at FROM api_market WHERE seller = 'marketPlayerLeague'"
                                " AND coalesce(expires_at, '') <> '' ORDER BY 1")
            if (t := dt.datetime.fromisoformat(c)) <= now]


def backtest(ahead: int = 0) -> list[dict[str, Any]]:
    """The forecast at each past lock, made with this code if not made yet
    (player_forecast): its moment, for the jornada it locks and, with ahead,
    the next ones too, each one done. Graded in SQL (grade_forecast)."""
    locks = round_locks()
    done = done_jornadas()
    out = []
    try:
        for i in sorted(done & set(locks), key=lambda j: locks[j]):
            set_now(locks[i] - dt.timedelta(minutes=1))
            fc = player_forecast()
            out += [{"from_j": i, "jornada": j, "moment": fc.moment}
                    for j in range(i, i + ahead + 1) if j in done and j in fc.per_j]
    finally:
        set_now(None)
    return out


# The moments replay still owes the forecast as made now (:code, its
# :constants): those of a finished jornada it has not made.
TODO = """SELECT m.jornada, m.as_of FROM replay_moments m
WHERE m.jornada IN (SELECT jornada FROM jornadas_done)
  AND m.as_of NOT IN (SELECT as_of FROM forecast_moment WHERE code = :code AND constants = :constants AND made)
ORDER BY m.as_of"""


def replay(variant: str = REPLAY_VARIANT, shard: tuple[int, int] = (0, 1)) -> int:
    """Forecast the next jornada at every line-up snapshot before a played
    jornada's lock (replay_moments) with this code, under the variant's
    constants (assemble.applied), into the forecast's store
    (player_forecast), which the view replayed reads with what then
    happened. Resumes: only the moments TODO owes this code and these
    constants. shard (i, n) takes every nth moment."""
    with applied(variant):
        code, consts, _at = forecast_key()
        with closing(connect()) as db, db:
            db.execute("INSERT OR REPLACE INTO forecast_variant VALUES (?, ?, ?)", (variant, code, consts))
            todo = [(j, a) for j, a in db.execute(TODO, {"code": code, "constants": consts})][shard[0]::shard[1]]
        try:
            for j, at in todo:
                moment = snapshot_stamp(at)
                assert moment is not None
                set_now(moment)
                for attempt in range(5):
                    try:    # another shard connecting re-creates the views under this one
                        player_forecast()
                        break
                    except sqlite3.OperationalError as e:
                        if "schema has changed" not in str(e) or attempt == 4:
                            raise
                        time.sleep(1 + attempt)
                print("  replay %s j%d %s" % (at, j, variant), flush=True)
        finally:
            set_now(None)
    return len(todo)


def decisions() -> list[dict[str, Any]]:
    """Make each past lock's decision again with this code (decision.plan)
    and set every move it valued alone (dec_rows) against what its players
    then scored: the change in points it forecast (players got minus players
    sold) over the jornadas played since, and the change that happened; and
    the value its players were to gain (bought) or lose (sold) over the
    price horizon, in millions, against what they did."""
    locks, actual = round_locks(), jornada_points()
    done = done_jornadas()
    made: list[tuple[int, Universe, list[tuple[Any, ...]], int]] = []
    try:
        for i in sorted(done & set(locks), key=lambda j: locks[j]):
            u = universe_at(i, locks)
            if u is None:
                continue
            decision.plan(u)
            made.append((i, u, rows("SELECT kind, player, amount, in_plan FROM dec_rows"),
                         price_momentum().horizon))
    finally:
        set_now(None)
        built.cache_clear()
    out = []
    for i, u, moved, horizon in made:
        # what happened is read with the clock let go: as of the lock, no later price is seen
        rose = price_change(locks[i].strftime("%Y-%m-%d"), horizon)
        per_j, m = u.forecaster.per_jornada, u.market
        later = sorted(j for j in done if j >= i and j in per_j)

        def worth(a: Action, pct: Callable[[str], float | None]) -> float | None:
            """What the move's players were worth more (bought) or less
            (sold) after the horizon, in millions; None until it has passed."""
            got = {k: pct(k) for k in (*((a.buy, ) if a.buy else ()), *a.sell)}
            if any(v is None for v in got.values()):
                return None
            return (m.value.get(a.buy, 0.0) * (got.get(a.buy) or 0.0) - sum(
                m.value.get(k, 0.0) * (got[k] or 0.0) for k in a.sell)) / 100e6

        def change(a: Action, pts: Callable[[str, int], float]) -> float:
            return sum(pts(a.buy, j) for j in later) - sum(
                pts(k, j) for k in a.sell for j in later)
        for kind, k, amount, in_plan in moved:
            a = Action("buy", buy=k, cost=amount) if kind == "buy" else Action("sell", sell=(k, ), proceeds=amount)
            out.append({
                "lock": i, "jornadas": len(later), "move": a.label(u.market.names),
                "group": "plan" if in_plan else "left out",
                "kind": "sell" if not a.buy else "get",
                "chance": None,
                "pred": change(a, lambda k, j: expected_points(per_j[j].get(k))),
                "real": change(a, lambda k, j: actual.get((k, j), 0.0)),
                "value_pred": worth(a, lambda k: m.trend.get(k, 0.0)),
                "value_real": worth(a, lambda k: rose.get(k))})
    return out


def resales() -> list[dict[str, Any]]:
    """Every past night's buys to resell (schema.sql's waiting, kinds
    'resale' and 'flat') at the bid worth most in money (bid_odds: its
    chance times the resale over it), as the views priced them ten minutes before that
    night's auctions closed, set against what selling then paid: his last
    value before the next match locked, at the offer share (recent_offers)
    the view used. Every player on the game's market, not only those taken,
    so the bar can be read off it."""
    picks: list[dict[str, Any]] = []
    try:
        for close in auction_closes():
            c = close.isoformat()
            set_now(close - dt.timedelta(minutes=10))
            cols, got = query(
                "SELECT f.player, f.price * coalesce((SELECT o.ratio FROM bid_odds o WHERE o.player = f.player"
                " ORDER BY o.p_win * (r.later - f.price * o.ratio) DESC, o.ratio LIMIT 1), 1.0) AS cost,"
                " r.later AS resale,"
                " l.later AS flat, (SELECT kick FROM next_kickoff) AS lock,"
                " (SELECT nights FROM resale_nights) AS nights,"
                " (SELECT avg(ratio) FROM recent_offers) AS ratio"
                " FROM player_facts f JOIN waiting r ON r.player = f.player AND r.kind = 'resale'"
                " JOIN waiting l ON l.player = f.player AND l.kind = 'flat'")
            picks += [{"close": c, **dict(zip(cols, r))} for r in got]
    finally:
        set_now(None)
    out = []
    for pk in picks:
        if not pk["nights"]:
            continue
        last = rows("SELECT value FROM price_obs WHERE ff_id = ? AND observed_at < ?"
                    " ORDER BY observed_at DESC LIMIT 1", (pk["player"], pk["lock"]))
        if last:
            out.append({**pk, "real": pk["ratio"] * last[0][0]})
    return out


def auction_bars() -> dict[tuple[str, dt.datetime], float | None]:
    """What a bid had to reach at each past close for each player listed
    then, keyed by player and close (auction_results' bar, in money; None:
    no bid gets him)."""
    return {(p, dt.datetime.fromisoformat(c)): None if bar is None else bar * asking
            for p, c, asking, bar in rows("SELECT player, close, asking, bar FROM auction_results")}


# The runs a day the board is acted on, local time as the closes are
# (lfg.timer at 10:00, lfg-flip.timer at 21:45, Europe/Madrid): bids placed
# at one stand until the next, which sees them as yours.
# When the phantom acts on the board: once a day, before the night's close,
# where the bids it places are settled (2026-10-30: as good as twice a day,
# 10:00 and 21:45, at every move cost tried, and at half the time; the
# morning's moves the evening's run made again).
RUNS = ((21, 45),)


def run_times(closes: list[dt.datetime]) -> dict[dt.datetime, dt.datetime]:
    """Each run before a night's close (RUNS on its day), to that close."""
    return {r: c for c in closes for h, m in RUNS
            if (r := c.replace(hour=h, minute=m, second=0, microsecond=0)) < c}


class Day(NamedTuple):
    """The phantom between runs: his squad and cash, his bids in, and the
    sales the last run left for after the auctions (pending)."""
    squad: dict[str, str]
    cash: float
    bids: dict[str, float]
    pending: tuple[Action, ...] = ()


def acted(up: Universe, bids: dict[str, float], acts: list[Action]) -> tuple[Day, list[Action]]:
    """A run's plan, made: its buys bid at its amounts (a bid already in
    set to the plan's, one the plan does not make taken back: the plan
    weighs every bid, yours in or not), and its sales made at once, or, with
    any bid placed, left for after the auctions (they pay for those bids or
    make room for them: closed). The app refuses a bid that takes your bids
    in past your cash and the debt it allows, 20% of your squad's value. And
    the moves made."""
    m = up.market
    was, bids = {k: b for k, b in bids.items() if k in m.price}, {}
    made, sales = [], []
    for a in acts:
        if a.sell:
            sales.append(a)
            continue
        room = m.cash + DEBT_SHARE * sum(m.value.get(k, 0.0) for k in up.mine) - sum(bids.values())
        if a.cost > room:
            continue
        bids[a.buy] = a.cost
        made.append(a)
    if any(was.get(k) != b for k, b in bids.items()):
        return Day(dict(up.mine), m.cash, bids, tuple(sales)), made
    return Day(up.squad_after(*sales), m.left(sales), bids), made + sales


def closed(up: Universe, day: Day, bars: dict[tuple[str, dt.datetime], float | None],
           close: dt.datetime) -> tuple[Day, list[Action], int]:
    """The auctions closing: a bid on a player whose auction closes then
    (auction_bars) wins if it reaches what it had to and is paid, the player
    yours; otherwise it is lost. Bids on the others stand. The sales left for
    after the auctions are made if none was lost; with one lost, only those
    the money rule then needs (decision.plan over the sales alone: those
    clearing a debt at fewest points), the rest the next run decides again. The day after, the sales
    made and how many bids were lost."""
    closing = {k: b for k, b in day.bids.items() if (k, close) in bars}
    won = [k for k, b in closing.items() if (bar := bars[k, close]) is not None and b >= bar]
    after = with_squad(up, up.squad_after(*(Action("buy", buy=k) for k in won)),
                       up.market.cash - sum(closing[k] for k in won))
    sales = list(day.pending) if len(won) == len(closing) else decision.plan(after, sales_only=True)
    return (Day(after.squad_after(*sales), after.market.left(sales),
                {k: b for k, b in day.bids.items() if k not in closing}),
            sales, len(closing) - len(won))


def phantom_path(daily: bool = False) -> list[dict[str, Any]]:
    """The league's sixth manager: your squad and cash at the first lock,
    then the plan (every player he or the manager beside him holds
    forecast, though nobody may hold him now) this code makes for the squad
    and cash he has, made: at each lock (bids win at the model's price), or
    with daily at each run the board is acted on (run_times), his bids
    standing from one run to the next as yours do (acted) and settled at
    each close against what the real auction took (closed). At each lock his
    eleven picked from that lock's forecast from the squad he holds and
    scored on what they got (nothing in debt, the game's rule), beside a
    manager who never moves, picked the same way. One row a played jornada:
    both elevens' points and what the lock's forecast expected of them, the
    moves made since the last, the bids lost, his cash and his squad's worth
    after them."""
    set_now(None)
    locks, actual = round_locks(), jornada_points()
    played = {locks[j] - dt.timedelta(minutes=1): j for j in done_jornadas() & set(locks)}
    first = min(played, default=None)
    runs = run_times([c for c in auction_closes() if first is not None and c > first]) if daily else {}
    runs = {r: c for r, c in runs.items() if first is not None and r > first}
    closes = set(runs.values())
    bars = auction_bars() if daily else {}
    squad: dict[str, str] | None = None
    hold: dict[str, str] = {}
    bids: dict[str, float] = {}
    pending: tuple[Action, ...] = ()
    cash = held_cash = 0.0
    made: list[str] = []
    lost = 0
    last: Universe | None = None
    out = []
    ever: dict[dt.datetime, frozenset[str]] = {}

    def held(t: dt.datetime) -> frozenset[str]:
        """Whom the phantom can hold at t: who was listed by then, and the squad he started with."""
        if t not in ever:
            ever[t] = on_market_by(t) | frozenset(hold)
        return ever[t]
    try:
        for t in sorted(set(played) | set(runs) | closes):
            if squad is None:
                if t not in played or (start := universe_when(t)) is None:
                    continue
                squad, hold, cash = dict(start.mine), dict(start.mine), start.market.cash
                held_cash = cash
                warm([r for r in sorted(set(played) | set(runs)) if r > t], held)
            if t in closes:
                if last is not None:
                    day, sold, n = closed(with_squad(last, squad, cash), Day(squad, cash, bids, pending), bars, t)
                    squad, cash, bids, pending = day
                    made += [a.label(last.market.names) for a in sold]
                    lost += n
                if t not in played and t not in runs:
                    continue
            assert hold and frozenset(squad) | frozenset(bids) <= held(t), "the phantom holds a player never listed"
            u = universe_when(t, held(t) if hold else frozenset(squad))
            if u is None:
                continue
            up = with_squad(u, squad, cash, bids)
            if t in runs or not daily:
                acts = decision.plan(up)
                if daily:
                    (squad, cash, bids, pending), acts = acted(up, bids, acts)
                    up = with_squad(u, squad, cash, bids)
                else:
                    squad, cash = up.squad_after(*acts), up.market.left(acts)
                    up = up.after(*acts)
                made += [a.label(up.market.names) for a in acts]
                last = u
            if t in played:
                i = played[t]
                fielded = up.outlook.xi
                still = with_squad(u, hold, cash).outlook.xi
                out.append({"jornada": i, "phantom": sum(actual.get((k, i), 0.0) for k in fielded.players)
                            if cash >= 0 else 0.0,
                            "standpat": sum(actual.get((k, i), 0.0) for k in still.players),
                            "expected": sum(fielded.expected.get(k, 0.0) for k in fielded.players),
                            "standpat_expected": sum(still.expected.get(k, 0.0) for k in still.players),
                            "moves": "; ".join(made), "lost": lost, "cash": cash,
                            "value": sum(u.market.value.get(k, 0.0) for k in squad),
                            "standpat_money": held_cash + sum(u.market.value.get(k, 0.0) for k in hold),
                            "lam": u.lam})
                made, lost = [], 0
    finally:
        set_now(None)
        built.cache_clear()
    return out


def phantom_season() -> list[dict[str, Any]]:
    """The phantom manager deciding at each lock (phantom_path): what the
    grade stage keeps (phantom_jornadas: no bid is settled there, so no
    lost, and the forecasts are the grade's own business), its plans with
    variance weighed at nothing: the grade is the forecasts' and the
    moves', the plan's appetite for variance the backtest's (money)."""
    return [{k: v for k, v in r.items() if k not in ("lost", "expected", "standpat_expected", "standpat_money", "lam")}
            for r in phantom_path()]


def clean_windows(locks: dict[int, dt.datetime], kicks: list[tuple[int, dt.datetime]]
                  ) -> dict[int, tuple[dt.datetime, dt.datetime]]:
    """The span in which each jornada's points were scored, lock to the next
    lock: only where it is clean, that is every kickoff in it is this
    jornada's and all of this jornada's are in it (a jornada that plays on
    past another's lock, or the one still to play, has none)."""
    ordered = sorted(locks.values())
    out = {}
    for j, start in locks.items():
        later = [t for t in ordered if t > start]
        if not later:
            continue
        end = later[0]
        inside = [k for k in kicks if start <= k[1] < end]
        if all(kj == j for kj, _t in inside) and all(start <= t < end for kj, t in kicks if kj == j):
            out[j] = (start, end)
    return out


def calibration_rows(jornada: int, st: Standings, actual: dict[str, float],
                     me: str) -> list[dict[str, Any]]:
    """Where what happened falls in the forecast of one jornada (its normal:
    pit, the share below it), for each manager, and for you minus him (the
    managers' players are their own, so the difference's spread is both)."""
    def pit(mean: float, sd: float, x: float) -> float:
        if sd <= 0:
            return 1.0 if x > mean else 0.5 if x == mean else 0.0
        return NormalDist(mean, sd).cdf(x)
    out = []
    for m, (mean, sd) in st.dist.items():
        if m not in actual:
            continue
        row: dict[str, Any] = {"jornada": jornada, "manager": m, "actual": actual[m],
                               "mean": mean, "sd": sd, "pit": pit(mean, sd, actual[m]),
                               "diff_actual": None, "diff_mean": None, "diff_sd": None, "diff_pit": None}
        if m != me and me in st.dist and me in actual:
            dm, ds = st.dist[me][0] - mean, math.hypot(st.dist[me][1], sd)
            row.update(diff_actual=actual[me] - actual[m], diff_mean=dm, diff_sd=ds,
                       diff_pit=pit(dm, ds, actual[me] - actual[m]))
        out.append(row)
    return out


def sim_calibration() -> list[dict[str, Any]]:
    """The standings forecast held to account one jornada at a time: at each
    clean lock of a finished jornada, every manager's points that jornada
    (his best eleven of his squad then, the forecast then) against what he
    scored (his team_points over the window)."""
    locks = round_locks()
    with closing(connect()) as db:
        kicks = [(j, snapshot_stamp(k) or dt.datetime.min.replace(tzinfo=dt.timezone.utc))
                 for j, k in db.execute("SELECT jornada, kick FROM kickoffs")]

        def points(m: str, t: dt.datetime) -> float | None:
            got = db.execute("SELECT team_points FROM api_standings WHERE manager = ? AND observed_at <= ?"
                             " ORDER BY observed_at DESC LIMIT 1", (m, t.strftime("%Y-%m-%dT%H%MZ"))).fetchone()
            return None if got is None else float(got[0])
        out: list[dict[str, Any]] = []
        try:
            done = done_jornadas()
            for j, (start, end) in sorted(clean_windows(locks, kicks).items()):
                if j not in done:
                    continue
                u = universe_at(j, locks)
                if u is None:
                    continue
                st = standings(u.forecaster, {m: sq for m, sq in u.state.squads.items()
                                              if not m.startswith("__")}, [j], {}, u.me)
                actual = {}
                for m in st.dist:
                    a0, a1 = points(m, start), points(m, end)
                    if a0 is not None and a1 is not None:
                        actual[m] = a1 - a0
                out += calibration_rows(j, st, actual, u.me)
        finally:
            set_now(None)
            built.cache_clear()
    return out


def decision_rows(moves: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """decisions' moves as rows: group (a SQL word) becomes reached."""
    return [{"ord": i, **{k: v for k, v in m.items() if k != "group"}, "reached": m["group"]}
            for i, m in enumerate(moves)]


# What the grade stage keeps, a set a run.
GRADED = ("grade_moment", "grade_decisions", "phantom_jornadas", "sim_calibration")


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
        return db.execute("SELECT 1 FROM grade_moment g JOIN runs e ON e.run_id = g.run_id"
                          " JOIN runs r ON r.run_id = ? AND e.commit_sha = r.commit_sha AND e.run_id < r.run_id"
                          " GROUP BY g.run_id HAVING max(g.jornada) >= ? LIMIT 1", (run, last)).fetchone() is not None


def record_grades() -> dict[str, int]:
    """The grade stage (run.py grade): replay each past lock with this code,
    and keep every player forecast and every ranked move against what then
    happened (one set a code: superseded), unless this code has graded them
    on every jornada done (graded). The replay of every snapshot is not
    run here: monitoring, ~550 moments made again at each forecast change
    (grading.py replay JOBS)."""
    done = done_jornadas()
    resolved = len(done)
    run = this_run()
    if run is not None and done and graded(run, max(done)):
        print("  grade: this code's grades stand (every jornada done graded)")
        return {"resolved": resolved, "forecasts": 0, "moves": 0}
    _AT.clear()
    try:
        fc, moves = backtest(GRADE_AHEAD), decision_rows(decisions())
        if run is not None:
            record([{"run_id": run, **r} for r in fc], "grade_moment")
            record([{"run_id": run, **r} for r in moves], "grade_decisions")
            record([{"run_id": run, **r} for r in phantom_season()], "phantom_jornadas")
            record([{"run_id": run, **r} for r in sim_calibration()], "sim_calibration")
            superseded(run)
    finally:
        _AT.clear()
    print("  grade: %d forecasts, %d moves over %d jornadas" % (len(fc), len(moves), resolved))
    return {"resolved": resolved, "forecasts": len(fc), "moves": len(moves)}     # forecasts: lock moments a jornada


def view_table(view: str, path: Path | None = None) -> str:
    """A view (replayed, grade_forecast) made a table of its columns in the
    current tables (tidy.tables_in), for its readers' tests to fill."""
    with closing(connect(path)) as db, db:
        cols = ['"%s" %s' % (r[1], r[2]) for r in db.execute('PRAGMA table_info("%s")' % view)]
        db.execute('DROP VIEW "%s"' % view)
        db.execute('CREATE TABLE "%s" (%s)' % (view, ", ".join(cols)))
    return view


def _selftest_todo() -> None:
    """TODO on its inputs as tables: a finished jornada's moments this
    forecast (its code and constants) has not made."""
    import sqlite3
    db = sqlite3.connect(":memory:")
    db.executescript("""CREATE TABLE replay_moments (jornada, as_of, lock);
        CREATE TABLE jornadas_done (jornada); CREATE TABLE forecast_moment (code, constants, as_of, made);
        INSERT INTO replay_moments VALUES (2, 'a', ''), (2, 'b', ''), (2, 'c', ''), (3, 'd', ''), (9, 'e', '');
        INSERT INTO jornadas_done VALUES (2), (3);
        INSERT INTO forecast_moment VALUES ('c1', 'k1', 'a', 1), ('c1', 'k1', 'b', 0), ('c0', 'k1', 'c', 1),
                                           ('c1', 'k2', 'd', 1);""")
    assert [tuple(r) for r in db.execute(TODO, {"code": "c1", "constants": "k1"})] == [
        (2, "b"), (2, "c"), (3, "d")], \
        "made (a): done; begun but not made (b), another code's (c) or other constants' (d): owed; 9 unfinished"


def _selftest_phantom() -> None:
    """The phantom's day: bids placed at a run stand to the next and are
    settled at the close."""
    from dataclasses import replace
    from ffcore.fixtures import tiny_market_universe
    madrid = dt.timezone(dt.timedelta(hours=2))
    close = dt.datetime(2026, 10, 20, 22, 24, tzinfo=madrid)
    assert run_times([close]) == {close.replace(hour=21, minute=45): close}, "the evening's run, before the close"
    u = with_squad(tiny_market_universe(), dict(tiny_market_universe().mine), 5.5e6)
    buy, sale = Action("buy", buy="cand_free", cost=5e6), Action("sell", sell=("bench_m",), proceeds=3e6)
    day, made = acted(u, {}, [buy, sale])
    assert (day.bids, day.cash, "bench_m" in day.squad, day.pending, made) \
        == ({"cand_free": 5e6}, 5.5e6, True, (sale,), [buy]), \
        "a buy is a bid in, paid only if it wins; the sale beside it waits for the auction"
    alone, made = acted(u, {}, [sale])
    assert (alone.cash, "bench_m" in alone.squad, alone.pending, made) == (8.5e6, False, (), [sale]), \
        "a sale with no bid placed is made at once"
    held = with_squad(u, day.squad, day.cash, day.bids)
    assert held.market.my_bid == {"cand_free": 5e6}, "the next run sees it as your bid in"
    again, made = acted(held, day.bids, [Action("buy", buy="cand_free", cost=6e6), sale])
    assert (again.bids, again.pending) == ({"cand_free": 6e6}, (sale,)), \
        "a bid already in, raised to the plan's amount: a bid to win again, the sale beside it waits for it"
    same, made = acted(held, day.bids, [Action("buy", buy="cand_free", cost=5e6), sale])
    assert (same.bids, same.pending, made[-1:]) == ({"cand_free": 5e6}, (), [sale]), \
        "the plan keeps your bid as it is: no bid placed, the sale is made"
    assert acted(held, day.bids, [sale])[0].bids == {}, \
        "a bid in that the plan does not make is taken back: it would win beside the plan's"
    assert acted(held, day.bids, [Action("withdraw", sell=("cand_free",), proceeds=5e6)])[0].bids == {}, \
        "withdrawn"
    broke = with_squad(u, dict(u.mine), -1e6)
    assert acted(broke, {}, [buy])[0].bids == {}, "5M of bids on -1M and 1M of allowed debt: the app refuses it"
    gone = replace(held, market=replace(held.market, price={}))
    assert acted(gone, day.bids, [])[0].bids == {}, "off the market: the bid is gone"
    won, sold, n = closed(held, day, {("cand_free", close): 4.9e6}, close)
    assert ("cand_free" in won.squad, "bench_m" in won.squad, won.cash, won.bids, sold, n) \
        == (True, False, 3.5e6, {}, [sale], 0), "won: paid, yours, and the sale beside it made"
    lost, sold, n = closed(held, day, {("cand_free", close): 5.2e6}, close)
    assert ("cand_free" in lost.squad, "bench_m" in lost.squad, lost.cash, sold, n) \
        == (False, True, 5.5e6, [], 1), "lost: no charge, and the sale it needed not made"
    owing = with_squad(u, dict(u.mine), -1e6)
    _o, sold, _n = closed(owing, day._replace(cash=-1e6), {("cand_free", close): 5.2e6}, close)
    assert [a.sell for a in sold] in ([("bench_m",)], [("bench_k",)]), \
        "lost, in debt: one sale the money rule needs, of a bench player costing no points"
    assert closed(held, day, {}, close)[0].bids == {"cand_free": 5e6}, "his auction is not tonight: it stands"


def _selftest() -> None:
    _selftest_phantom()
    import tempfile
    from ffcore.tidy import store_sql, tables_in
    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        record([{"moment": 3, "code": "c", "constants": "k", "as_of": "t", "made": 1}], "forecast_moment")
        record([{"moment": 3, "player": k, "jornada": j, "pts": pts, "p": 0.5}
                for k, pts in (("a", 6.0), ("b", 2.0)) for j in (8, 9)], "forecast_cell")
        record([{"moment": 3, "player": "a", "flagged": 0}, {"moment": 3, "player": "b", "flagged": 1}],
               "forecast_player")
        record([{"run_id": 1, "from_j": 7, "jornada": 8, "moment": 3}], "grade_moment")
        assert rows("SELECT from_j, jornada, player, predicted, plays, actual, flagged FROM grade_forecast"
                    " ORDER BY player") == [(7, 8, "a", 3.0, 0.5, 0.0, 0), (7, 8, "b", 1.0, 0.5, 0.0, 1)], \
            "a grade's moment, a row a player of the jornadas graded; no points scored is 0"
    moves = decision_rows([{"lock": 7, "move": "buy X", "group": "plan", "kind": "get"},
                           {"lock": 7, "move": "sell Y", "group": "left out", "kind": "sell"}])
    assert [(m["ord"], m["reached"], "group" in m) for m in moves] \
        == [(0, "plan", False), (1, "left out", False)], moves

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
            record([{"run_id": r, "from_j": 7, "jornada": 7, "moment": 1}], "grade_moment", path)
            record([{"run_id": r, "ord": 0, "lock": 7, "move": "m", "reached": "plan", "kind": "get"}],
                   "grade_decisions", path)
        superseded(now, path)
        for t in GRADED:
            assert [r for r, in db.execute("SELECT run_id FROM %s ORDER BY run_id" % t)] in (
                [other, now], []), "the same code's earlier grade goes; another code's stays"
        later, elsewhere = run("a"), run("c")
        assert graded(later, 7, path) and not graded(later, 8, path) and not graded(elsewhere, 7, path), \
            "this code graded on every jornada done: nothing to grade again; a jornada more, or other code, is"


    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        try:
            set_now(dt.datetime(2026, 8, 30, tzinfo=dt.timezone.utc))
            store_sql("matches", [
                {"observed_at": "2026-08-01T0900Z", "match_id": "m%d" % j, "jornada": str(j),
                 "home": "h%d" % j, "away": "a%d" % j, "kickoff": k}
                for j, k in ((2, "2026-08-16T19:00:00+00:00"), (3, "2026-08-23T19:00:00+00:00"))])
            store_sql("lineups", [{"observed_at": at, "source": src, "team_slug": "h2", "player_slug": "p"}
                                  for at, src in (("2026-08-10T0800Z", "futbolfantasy"),
                                                  ("2026-08-10T0800Z", "futbolfantasy"),
                                                  ("2026-08-10T0800Z", "analitica"),
                                                  ("2026-08-16T1900Z", "futbolfantasy"),
                                                  ("2026-08-18T0800Z", "futbolfantasy"),
                                                  ("2026-08-23T1859Z", "futbolfantasy"),
                                                  ("2026-08-23T1900Z", "futbolfantasy"),
                                                  ("2026-08-29T0800Z", "futbolfantasy"))])
            got = [tuple(r) for r in connect().execute("SELECT jornada, as_of FROM replay_moments")]
            assert got == [(2, "2026-08-10T0800Z"), (3, "2026-08-16T1900Z"), (3, "2026-08-18T0800Z"),
                           (3, "2026-08-23T1859Z")], \
                "each snapshot (one a time, one source) belongs to the jornada next then: from the previous lock, before its own"
        finally:
            set_now(None)

    _selftest_todo()
    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        # squared errors: 1h out 1, 4; 2h out 9, 0; 30h out 16
        record([{"variant": "v", "as_of": "t%d" % (i // 2), "jornada": 2, "player": k, "predicted": pr,
                 "actual": ac, "flagged": fl, "lead_h": h}
                for i, (k, pr, ac, fl, h) in enumerate([("a", 1, 0, 0, 1.0), ("b", 0, 2, 1, 1.0),
                                                        ("a", 3, 0, 0, 2.0), ("b", 2, 2, 0, 2.0),
                                                        ("a", 4, 0, 0, 30.0)])], view_table("replayed"))
        lead = {r[0]: r for r in connect().execute(
            "SELECT lead, n, moments, rmse, flagged_rmse FROM replay_lead WHERE variant = 'v'")}
        assert lead["0-3h"][1:4] == (4, 2, (14 / 4) ** 0.5), lead
        assert lead["0-3h"][4] == 2.0, "the flagged one: (0 - 2)^2 = 4, rmse 2"
        assert lead["24-48h"][1:3] == (1, 1) and lead["24-48h"][3] == 4.0, lead

    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        # jornada 2, forecast 30h out (early) and 1h out (last); squared errors early / last:
        # a 4 / 0, b (flagged) 9 / 0, c 1 / 4; another jornada's only moment has nothing to be set against
        record([{"variant": "v", "as_of": at, "jornada": j, "player": k, "predicted": pr, "actual": ac,
                 "flagged": fl, "lead_h": h}
                for at, j, k, pr, ac, fl, h in [
                    ("e", 2, "a", 2, 0, 0, 30.0), ("l", 2, "a", 0, 0, 0, 1.0),
                    ("e", 2, "b", 4, 1, 1, 30.0), ("l", 2, "b", 1, 1, 1, 1.0),
                    ("e", 2, "c", 1, 0, 0, 30.0), ("l", 2, "c", 2, 0, 0, 1.0),
                    ("x", 3, "a", 9, 0, 0, 30.0)]], view_table("replayed"))
        gap = {(r[0], r[1]): r for r in connect().execute(
            "SELECT lead, scope, n, extra_mse, se, claim FROM replay_gap")}
        assert set(gap) == {("24-48h", "all"), ("24-48h", "flagged")}, \
            "the last moment is not set against itself, a jornada with one moment has no gap"
        se = (((4 - 10 / 3) ** 2 + (9 - 10 / 3) ** 2 + (-3 - 10 / 3) ** 2) / 2 / 3) ** 0.5
        assert gap["24-48h", "all"][2] == 3 and abs(gap["24-48h", "all"][3] - 10 / 3) < 1e-9 \
            and abs(gap["24-48h", "all"][4] - se) < 1e-9 and gap["24-48h", "all"][5] == "not established", gap
        assert gap["24-48h", "flagged"][2:4] == (1, 9.0) and gap["24-48h", "flagged"][5] == "too few"

    t0 = dt.datetime(2026, 8, 20, 19, tzinfo=dt.timezone.utc)

    def day(n: int) -> dt.datetime:
        return t0 + dt.timedelta(days=n)
    # jornada 2 locks day 0, 3 locks day 8, 6 locks day 14 and plays on; 4 locks day 15 with a j6 match inside
    locks = {2: day(0), 3: day(8), 6: day(14), 4: day(15), 5: day(22)}
    kicks = [(2, day(0)), (2, day(2)), (3, day(8)), (3, day(10)), (6, day(14)), (4, day(15)),
             (6, day(17)), (4, day(18)), (5, day(22))]
    assert clean_windows(locks, kicks) == {2: (day(0), day(8)), 3: (day(8), day(14))}, \
        "a jornada's points window runs to the next lock; 6 plays on inside 4's and 4 and 6 share theirs; 5 is last"

    assert clean_windows({1: day(0), 2: day(5)}, [(1, day(0)), (9, day(0)), (2, day(5))]) == {}, \
        "another jornada's match at the very kickoff of the lock is in the window too"
    fcst = Standings({"me": (55.0, 10.0), "riv": (45.0, 0.0)}, "me")
    cal = {r["manager"]: r for r in calibration_rows(7, fcst, {"me": 55.0, "riv": 100.0}, "me")}
    assert (cal["me"]["mean"], cal["me"]["pit"], cal["me"]["diff_pit"]) == (55.0, 0.5, None), cal["me"]
    assert cal["riv"]["pit"] == 1.0 and cal["riv"]["actual"] == 100.0, "above a forecast with no spread"
    assert (cal["riv"]["diff_actual"], cal["riv"]["diff_mean"], cal["riv"]["diff_sd"]) == (-45.0, 10.0, 10.0), \
        "me minus him: the spreads add"
    assert abs(cal["riv"]["diff_pit"] - NormalDist().cdf(-5.5)) < 1e-12, "-45 against +10, sd 10"
    assert abs(calibration_rows(7, fcst, {"me": 65.0}, "me")[0]["pit"] - NormalDist().cdf(1.0)) < 1e-12

    print("grading self-test OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        raise SystemExit(0)
    if sys.argv[1:2] == ["variant"]:
        # variant NAME CONSTANT=VALUE ...: define (or redefine) a challenger
        name, sets = sys.argv[2], [a.split("=", 1) for a in sys.argv[3:]]
        with closing(connect()) as db, db:
            db.execute("DELETE FROM variants WHERE variant = ?", (name,))
            db.executemany("INSERT INTO variants VALUES (?, ?, ?)", [(name, k, float(v)) for k, v in sets])
        raise SystemExit(0)
    if sys.argv[1:2] == ["replay"]:
        # replay [JOBS] [SHARD/OF] (LFG_VARIANT=name): JOBS processes side by side, each its share of the moments
        import subprocess
        jobs = int(sys.argv[2]) if len(sys.argv) > 2 else 1
        if len(sys.argv) > 3:
            i, n = map(int, sys.argv[3].split("/"))
            print("replayed %d moments" % replay(os.environ.get("LFG_VARIANT", REPLAY_VARIANT), (i, n)))
        else:
            procs = [subprocess.Popen([sys.executable, __file__, "replay", str(jobs), "%d/%d" % (i, jobs)])
                     for i in range(jobs)]
            raise SystemExit(max(p.wait() for p in procs))
        raise SystemExit(0)
    if sys.argv[1:2] == ["money"]:
        # money: the phantom deciding daily (decision.plan), beside standing still
        # LFG_VARIANT=name: the model as that variant sets it (grading.py variant NAME CONSTANT=VALUE)
        held = applied(os.environ.get("LFG_VARIANT", "live"))
        held.__enter__()
        got = phantom_path(daily=True)
        # The scoreboard: points, and money (cash and squad value) at the
        # last jornada's money rate (decide.sql money_rate), for both.
        lam = got[-1]["lam"] if got else 0.0
        print("%6s %5s %8s %8s %9s %9s %8s %8s" % ("moves", "lost", "points", "standpat",
                                                 "cash(M)", "value(M)", "score", "standpat"))
        print("%6d %5d %+8.0f %+8.0f %+9.1f %+9.1f %+8.0f %+8.0f" % (
            sum(len(g["moves"].split("; ")) for g in got if g["moves"]), sum(g["lost"] for g in got),
            sum(g["phantom"] for g in got), sum(g["standpat"] for g in got),
            got[-1]["cash"] / 1e6 if got else 0.0, got[-1]["value"] / 1e6 if got else 0.0,
            sum(g["phantom"] for g in got) + lam * (got[-1]["cash"] + got[-1]["value"]) / 1e6 if got else 0.0,
            sum(g["standpat"] for g in got) + lam * got[-1]["standpat_money"] / 1e6 if got else 0.0),
            flush=True)
        # Out without freeing the universes one object at a time: ~8 s of a
        # backtest's exit (2026-10-31), every file already closed and written.
        os._exit(0)
    if sys.argv[1:2] == ["resales"]:
        # resales: each past night's buys to resell, forecast against what selling paid
        got = resales()
        print("%d priced, over %d nights" % (len(got), len({g["close"] for g in got})))
        for name, keep in (("every player", lambda g: True),
                           ("forecast profit", lambda g: g["resale"] > g["cost"]),
                           ("over the margin", lambda g: g["resale"] >= g["cost"] * (1 + RESALE_MARGIN))):
            sel = [g for g in got if keep(g)]
            if sel:
                pnl = sorted(g["real"] - g["cost"] for g in sel)
                print("%-17s %4d  forecast %+6.0fk  real median %+6.0fk mean %+6.0fk  made money %3.0f%%" % (
                    name, len(sel), sum(g["resale"] - g["cost"] for g in sel) / len(sel) / 1e3,
                    pnl[len(pnl) // 2] / 1e3, sum(pnl) / len(pnl) / 1e3,
                    100 * sum(x > 0 for x in pnl) / len(pnl)))
        raise SystemExit(0)
    print("grading.py --selftest | money (LFG_VARIANT=name) | resales | replay [JOBS] (LFG_VARIANT=name) | variant NAME CONSTANT=VALUE ...")
    raise SystemExit(2)
