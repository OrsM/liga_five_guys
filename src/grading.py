from __future__ import annotations

import datetime as dt
import math
import os
import sys
from collections.abc import Callable, Iterator
from contextlib import closing
from pathlib import Path
from statistics import NormalDist
from typing import Any


from ffcore.jornadas import round_locks, rounds
from ffcore.points import done_jornadas, jornada_points

from assemble import applied, built, forecast_key, player_forecast, price_change, price_momentum
import decision
from decide import Action
from ffcore.db import connect, record, replaying, rows, set_now, this_run
from ffcore.parse import snapshot_stamp, stamp
__all__ = ["backtest", "clean_windows", "calibration_rows", "sim_calibration", "phantom_season", "replay", "decision_rows", "decisions", "phantom_path", "superseded", "view_table",
           "record_grades"]

REPLAY_VARIANT = "live"
GRADE_AHEAD = 4  # jornadas past the lock each replayed forecast is scored on


def at(t: dt.datetime) -> int | None:
    """t set (set_now) and the league at t made for a decision there
    (assemble.built): its forecast moment, None if no board could be made
    then."""
    set_now(t)
    try:
        return built().moment
    except SystemExit as e:
        print("%s: no board (%s)" % (t.isoformat(), e), file=sys.stderr)
        return None


def before_locks() -> dict[dt.datetime, int]:
    """A minute before each finished jornada's lock, oldest first, and the
    jornada: where every replay of a lock stands."""
    locks = round_locks()
    return {locks[j] - dt.timedelta(minutes=1): j for j in sorted(done_jornadas() & set(locks), key=locks.__getitem__)}


def at_locks(only: set[int] | None = None) -> Iterator[int]:
    """Each finished jornada (of only, if given) where the league could be
    made a minute before its lock, with t there (at); t put back after:
    decisions and sim_calibration stand there."""
    with replaying():
        for t, j in before_locks().items():
            if (only is None or j in only) and at(t) is not None:
                yield j


def auction_closes() -> list[dt.datetime]:
    """When each night's auctions on the game's market closed by t (the
    listings' expiry), oldest first: a day's buying and selling is decided
    before it."""
    return [dt.datetime.fromisoformat(c) for (c, ) in rows(
        "SELECT DISTINCT expires_at FROM api_market WHERE seller = 'marketPlayerLeague'"
        " AND coalesce(expires_at, '') <> '' AND julianday(expires_at) <= stamp_jd((SELECT cut FROM look))"
        " ORDER BY 1")]


def backtest(ahead: int = 0) -> list[dict[str, Any]]:
    """The forecast at each past lock, made with this code if not made yet
    (player_forecast): its moment, for the jornada it locks and, with ahead,
    the next ones too, each one done. Graded in SQL (grade_forecast)."""
    done = done_jornadas()
    out = []
    with replaying():
        for t, i in before_locks().items():
            set_now(t)
            fc = player_forecast()
            out += [{"from_j": i, "jornada": j, "moment": fc.moment}
                    for j in range(i, i + ahead + 1) if j in done and j in fc.jornadas]
    return out


# The moments replay still owes the forecast as made now (:code, its
# :constants): those of a finished jornada it has not made.
TODO = """SELECT m.jornada, m.as_of FROM replay_moments m
WHERE m.jornada IN (SELECT jornada FROM jornadas_done)
  AND m.as_of NOT IN (SELECT as_of FROM forecast_moment WHERE code = :code AND constants = :constants AND made)
ORDER BY m.as_of"""


def replay(variant: str = REPLAY_VARIANT) -> int:
    """Forecast the next jornada at every line-up snapshot before a played
    jornada's lock (replay_moments) with this code, under the variant's
    constants (assemble.applied), into the forecast's store
    (player_forecast), which the view replayed reads with what then
    happened. Resumes: only the moments TODO owes this code and these
    constants. One process at a time: t is the file's (look)."""
    with applied(variant):
        code, consts, _at = forecast_key()
        with closing(connect()) as db, db:
            db.execute("INSERT OR REPLACE INTO forecast_variant VALUES (?, ?, ?)", (variant, code, consts))
            todo = [(j, a) for j, a in db.execute(TODO, {"code": code, "constants": consts})]
        with replaying():
            for j, at in todo:
                moment = snapshot_stamp(at)
                assert moment is not None
                set_now(moment)
                player_forecast()
                print("  replay %s j%d %s" % (at, j, variant), flush=True)
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
    made = []
    for i in at_locks():
        decision.plan()
        moved = rows("SELECT kind, player, amount, in_plan FROM dec_rows")
        e = {(k, j): x for k, j, x in rows("SELECT player, jornada, e FROM cell"
                                           " WHERE player IN (SELECT player FROM dec_rows)")}
        seen = {k: (name, value or 0.0, trend or 0.0) for k, name, value, trend in rows(
            "SELECT s.player, name_shown(s.name), s.value, s.trend FROM look l"
            " JOIN seen s ON s.cut = l.cut AND s.variant = l.variant")}
        made.append((i, moved, e, seen, set(rounds().rem), price_momentum().horizon))
    out = []
    for i, moved, e, seen, rem, horizon in made:
        # what happened is read with t put back: as of the lock, no later price is seen
        rose = price_change(locks[i].strftime("%Y-%m-%d"), horizon)
        later = sorted(j for j in done if j >= i and j in rem)
        names = {k: f[0] for k, f in seen.items()}

        def worth(a: Action, pct: Callable[[str], float | None]) -> float | None:
            """What the move's players were worth more (bought) or less
            (sold) after the horizon, in millions; None until it has passed."""
            got = {k: pct(k) for k in (*((a.buy, ) if a.buy else ()), *a.sell)}
            if any(v is None for v in got.values()):
                return None
            value = {k: f[1] for k, f in seen.items()}
            return (value.get(a.buy, 0.0) * (got.get(a.buy) or 0.0) - sum(
                value.get(k, 0.0) * (got[k] or 0.0) for k in a.sell)) / 100e6

        def change(a: Action, pts: Callable[[str, int], float]) -> float:
            return sum(pts(a.buy, j) for j in later) - sum(
                pts(k, j) for k in a.sell for j in later)
        for kind, k, amount, in_plan in moved:
            a = Action("buy", buy=k, cost=amount) if kind == "buy" else Action("sell", sell=(k, ), proceeds=amount)
            out.append({
                "lock": i, "jornadas": len(later), "move": a.label(names),
                "group": "plan" if in_plan else "left out",
                "kind": "sell" if not a.buy else "get",
                "chance": None,
                "pred": change(a, lambda k, j: e.get((k, j), 0.0)),
                "real": change(a, lambda k, j: actual.get((k, j), 0.0)),
                "value_pred": worth(a, lambda k: seen[k][2] if k in seen else 0.0),
                "value_real": worth(a, lambda k: rose.get(k))})
    return out


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


# The paths the phantom's replay reads the league at t as (look.path): his
# moves, and none (the manager who never moves).
PHANTOM, STANDPAT = "phantom", "standpat"


def follow(path: str, t0: str = "") -> None:
    """The league at t read as path's (look: its moves since t0); '': live,
    your own squad and cash at t."""
    with closing(connect()) as db, db:
        db.execute("UPDATE look SET path = ?, t0 = ?", (path, t0))


def move(path: str, t: dt.datetime, kind: str, player: str, amount: float | None) -> None:
    with closing(connect()) as db, db:
        db.execute("INSERT OR REPLACE INTO moves VALUES (?, ?, ?, ?, ?)", (path, stamp(t), kind, player, amount))


def phantom_path() -> list[dict[str, Any]]:
    """The league's sixth manager: your squad and cash at the first lock,
    then his own moves (the path PHANTOM's, rows the league at t reads:
    schema.sql my_squad, my_cash), as this code's plan makes them at each
    run the board is acted on (run_times), at t there: its bids placed and
    those it no longer makes taken back, its sales made at once, but those
    beside a bid, which are made at the close if every bid closing then won
    (else only what clearing a debt then takes: the plan, sales only, at the
    close). A bid is settled at its auction's close by what that auction took
    (my_bids). At each lock his eleven (my_xi) scored on what they got
    (nothing in debt, the game's rule), beside a manager who never moves
    (STANDPAT), picked the same way. One row a played jornada: both elevens'
    points, the moves made since the last, the bids lost, his cash and his
    squad's worth after them. Up to t, put back after, and the paths
    left as made (moves) but not followed."""
    with replaying():
        try:
            return _phantom_path()
        finally:
            follow("")


def _phantom_path() -> list[dict[str, Any]]:
    actual = jornada_points()
    played = before_locks()
    first = min(played, default=None)
    if first is None:
        return []
    runs = {r: c for r, c in run_times([c for c in auction_closes() if c > first]).items() if r > first}
    closes = set(runs.values())
    with closing(connect()) as db, db:
        db.execute("DELETE FROM moves WHERE path IN (?, ?)", (PHANTOM, STANDPAT))
    start = ""
    made: list[str] = []
    pending: list[Action] = []      # the last run's sales beside its bids
    lost = 0
    out = []

    def names() -> dict[str, str]:
        """Each player's name as last seen by t."""
        return dict(rows("SELECT s.player, name_shown(s.name) FROM look l JOIN seen s"
                         " ON s.cut = (SELECT max(cut) FROM seen WHERE cut <= l.cut) AND s.variant = l.variant"))

    def sell(t: dt.datetime, sales: list[Action]) -> None:
        for a in sales:
            move(PHANTOM, t, "sell", a.sell[0], a.proceeds)
        made.extend(a.label(names()) for a in sales)

    def held() -> tuple[list[str], float, float]:
        """His eleven next, cash and squad's value, as of t."""
        (cash, value), = rows("SELECT (SELECT cash FROM my_cash), (SELECT total(s.value) FROM my_squad m"
                              " JOIN look l JOIN seen s ON s.cut = l.cut AND s.variant = l.variant"
                              " AND s.player = m.player)")
        return [k for k, in rows("SELECT player FROM my_xi")], cash, value

    for t in sorted(set(played) | set(runs) | closes):
        ready = False
        if not start:
            if t not in played or at(t) is None:
                continue
            start, ready = stamp(t), True
            follow(PHANTOM, start)
        if t in closes:
            set_now(t)
            settled = rows("SELECT won FROM my_bids WHERE close = ?", (stamp(t), ))
            n = sum(not won for won, in settled)
            if not n:
                sell(t, pending)
            elif at(t) is not None:
                sell(t, decision.plan(sales_only=True))
            pending, lost = [], lost + n
            if t not in played and t not in runs:
                continue
        if not ready and at(t) is None:
            continue
        if t in runs:
            acts = decision.plan()
            after = decision.after_auctions()
            bids = {a.buy: a.cost for a in acts if a.buy}
            for (k, ) in rows("SELECT player FROM my_bids WHERE won IS NULL"):
                if k not in bids:
                    move(PHANTOM, t, "withdraw", k, None)
            for k, b in bids.items():
                move(PHANTOM, t, "bid", k, b)
            pending = [a for a in acts if a.sell and set(a.sell) & after]
            made.extend(a.label(names()) for a in acts if a.buy)
            sell(t, [a for a in acts if a.sell and not set(a.sell) & after])
        if t in played:
            xi, cash, value = held()
            follow(STANDPAT, start)
            still, still_cash, still_value = held()
            follow(PHANTOM, start)
            out.append({"jornada": played[t],
                        "phantom": sum(actual.get((k, played[t]), 0.0) for k in xi) if cash >= 0 else 0.0,
                        "standpat": sum(actual.get((k, played[t]), 0.0) for k in still),
                        "moves": "; ".join(made), "lost": lost, "cash": cash, "value": value,
                        "standpat_money": still_cash + still_value})
            made, lost = [], 0
    return out


def phantom_season() -> list[dict[str, Any]]:
    """The phantom (phantom_path) as the grade stage keeps it
    (phantom_jornadas): the forecasts are the grade's own business."""
    return [{k: v for k, v in r.items() if k not in ("lost", "standpat_money")}
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


def calibration_rows(jornada: int, dist: dict[str, tuple[float, float]], actual: dict[str, float],
                     me: str) -> list[dict[str, Any]]:
    """Where what happened falls in the forecast of one jornada (its normal:
    pit, the share below it), for each manager, and for you minus him (the
    managers' players are their own, so the difference's spread is both)."""
    def pit(mean: float, sd: float, x: float) -> float:
        if sd <= 0:
            return 1.0 if x > mean else 0.5 if x == mean else 0.0
        return NormalDist(mean, sd).cdf(x)
    out = []
    for m, (mean, sd) in dist.items():
        if m not in actual:
            continue
        row: dict[str, Any] = {"jornada": jornada, "manager": m, "actual": actual[m],
                               "mean": mean, "sd": sd, "pit": pit(mean, sd, actual[m]),
                               "diff_actual": None, "diff_mean": None, "diff_sd": None, "diff_pit": None}
        if m != me and me in dist and me in actual:
            dm, ds = dist[me][0] - mean, math.hypot(dist[me][1], sd)
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
        windows = clean_windows(locks, kicks)
        for j in at_locks(set(windows)):
            start, end = windows[j]
            decision.inputs()
            dist = {m: (mean, sd) for m, mean, sd in rows("SELECT manager, mean, sd FROM league_jornada"
                                                          " WHERE jornada = ?", (j, ))}
            actual = {}
            for m in dist:
                a0, a1 = points(m, start), points(m, end)
                if a0 is not None and a1 is not None:
                    actual[m] = a1 - a0
            out += calibration_rows(j, dist, actual, rows("SELECT me FROM look")[0][0])
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
    (grading.py replay)."""
    done = done_jornadas()
    resolved = len(done)
    run = this_run()
    if run is not None and done and graded(run, max(done)):
        print("  grade: this code's grades stand (every jornada done graded)")
        return {"resolved": resolved, "forecasts": 0, "moves": 0}
    fc, moves = backtest(GRADE_AHEAD), decision_rows(decisions())
    if run is not None:
        record([{"run_id": run, **r} for r in fc], "grade_moment")
        record([{"run_id": run, **r} for r in moves], "grade_decisions")
        record([{"run_id": run, **r} for r in phantom_season()], "phantom_jornadas")
        record([{"run_id": run, **r} for r in sim_calibration()], "sim_calibration")
        superseded(run)
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
    """The phantom's league at t (schema.sql my_bids, my_squad, my_cash):
    his squad and cash at t0 and his moves since, his bids settled at their
    auction's close by what it took."""
    import tempfile
    from ffcore.tidy import store_sql, tables_in

    madrid = dt.timezone(dt.timedelta(hours=2))
    close = dt.datetime(2026, 10, 20, 22, 24, tzinfo=madrid)
    run, t0 = close.replace(hour=21, minute=45), stamp(close - dt.timedelta(days=2))
    assert run_times([close]) == {run: close}, "the evening's run, before the close"
    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        a = "2026-10-17T1000Z"
        store_sql("api_standings", [{"observed_at": a, "user_id": "1", "manager": "me"},
                                    {"observed_at": a, "user_id": "2", "manager": "riv"}])
        store_sql("api_market", [{"observed_at": a, "seller": "marketPlayerLeague", "player_id": i,
                                  "expires_at": close.isoformat(), "sale_price": "5000000"} for i in ("7", "8", "9")])
        store_sql("api_activity", [
            {"observed_at": a, "activity_id": "x", "kind": "buy", "user_id": "2", "player_id": "7",
             "at": "2026-10-20T22:24:10+02:00", "amount": "6000000", "counterparty": ""},
            {"observed_at": a, "activity_id": "z", "kind": "buy", "user_id": "2", "player_id": "8",
             "at": "2026-10-20T22:24:10+02:00", "amount": "5800000", "counterparty": ""},
            {"observed_at": a, "activity_id": "y", "kind": "bonus", "user_id": "1", "player_id": "",
             "at": "2026-10-19T04:40:00+02:00", "amount": "2000000", "counterparty": ""}])
        with closing(connect()) as db, db:
            db.execute("INSERT INTO league_config VALUES ('me', 100e6)")
            db.executemany("INSERT INTO crosswalk (player_id, app_id) VALUES (?, ?)",
                           [("cand", "7"), ("cheap", "8"), ("gone", "9")])
            db.executemany("INSERT INTO seen (cut, variant, player, name, pos, owner) VALUES (?, 'live', ?, ?, ?, ?)",
                           [(t0, k, k, pos, "me") for k, pos in (("k", "POR"), ("bench", "DEF"))])
            db.execute("INSERT INTO seen_cash VALUES (?, 'live', 'me', 10e6)", (t0, ))
            db.execute("UPDATE look SET me = 'me', path = 'p', t0 = ?", (t0, ))
            db.executemany("INSERT INTO moves VALUES ('p', ?, ?, ?, ?)", [
                (stamp(run), "bid", "cand", 6.5e6), (stamp(run), "bid", "cheap", 5.5e6), (stamp(run), "sell", "bench", 3e6),
                (stamp(run - dt.timedelta(days=1)), "bid", "gone", 9e6),
                (stamp(run), "withdraw", "gone", None)])

        def state() -> tuple[list[tuple[Any, ...]], list[str], float]:
            return (rows("SELECT player, won FROM my_bids ORDER BY player"),
                    [k for k, in rows("SELECT player FROM my_squad ORDER BY player")],
                    rows("SELECT cash FROM my_cash")[0][0])
        set_now(run)
        assert state() == ([("cand", None), ("cheap", None)], ["k"], 10e6 + 2e6 + 3e6), \
            "before the close his bids stand, unpaid; the sale made, and the league's bonus since t0 his"
        set_now(close)
        assert state() == ([("cand", 1), ("cheap", 0)], ["cand", "k"], 10e6 + 2e6 + 3e6 - 6.5e6), \
            ("at the close, what each auction took (6M, 5.8M): his 6.5M wins, paid, and he is his; 5.5M is lost;"
             " a bid taken back before its close is none")
        with closing(connect()) as db, db:
            db.execute("UPDATE look SET path = 'other'")
        assert state() == ([], ["bench", "k"], 12e6), "another path: its own moves (none), the same facts"
        with closing(connect()) as db, db:
            db.execute("UPDATE look SET path = '', t0 = ''")
        assert state()[0] == [], "live: no path's moves"


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
    fcst = {"me": (55.0, 10.0), "riv": (45.0, 0.0)}
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
        # replay (LFG_VARIANT=name)
        set_now(None)
        print("replayed %d moments" % replay(os.environ.get("LFG_VARIANT", REPLAY_VARIANT)))
        raise SystemExit(0)
    if sys.argv[1:2] == ["money"]:
        # money: the phantom deciding daily (decision.plan), beside standing still
        # LFG_VARIANT=name: the model as that variant sets it (grading.py variant NAME CONSTANT=VALUE)
        held = applied(os.environ.get("LFG_VARIANT", "live"))
        held.__enter__()
        set_now(None)
        got = phantom_path()
        # The scoreboard: points, for both, what finishing first is won on;
        # and the cash and squad value the phantom ends with, and standing still.
        print("%6s %5s %8s %8s %9s %9s %9s" % ("moves", "lost", "points", "standpat",
                                              "cash(M)", "value(M)", "standpat(M)"))
        print("%6d %5d %+8.0f %+8.0f %+9.1f %+9.1f %+9.1f" % (
            sum(len(g["moves"].split("; ")) for g in got if g["moves"]), sum(g["lost"] for g in got),
            sum(g["phantom"] for g in got), sum(g["standpat"] for g in got),
            got[-1]["cash"] / 1e6 if got else 0.0, got[-1]["value"] / 1e6 if got else 0.0,
            got[-1]["standpat_money"] / 1e6 if got else 0.0), flush=True)
        # Out without freeing what it made one object at a time: ~8 s of a
        # backtest's exit (2026-10-31), every file already closed and written.
        os._exit(0)
    print("grading.py --selftest | money (LFG_VARIANT=name) | replay (LFG_VARIANT=name) | variant NAME CONSTANT=VALUE ...")
    raise SystemExit(2)
