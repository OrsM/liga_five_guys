from __future__ import annotations

import datetime as dt
import math
import os
import sqlite3
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import closing
from pathlib import Path
from statistics import NormalDist
from typing import Any


from ffcore.jornadas import round_locks, rounds

from assemble import applied, built, forecast, price_change, price_momentum
import decision
from ffcore.db import connect, record, replaying, rows, set_look, set_now, this_run
from ffcore.parse import snapshot_stamp
__all__ = ["backtest", "clean_windows", "calibration_rows", "sim_calibration", "replay", "decision_rows", "decisions", "superseded", "view_table",
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


def jornada_points() -> dict[tuple[str, int], float]:
    """Each player's points in each jornada he scored in (scored)."""
    return {(k, j): pts for k, j, pts in rows("SELECT player, jornada, sum(pts) FROM scored GROUP BY player, jornada")}


def done_jornadas() -> set[int]:
    """The jornadas every match of which has a score (jornada_calendar's done):
    the ones a forecast can be graded on."""
    return {j for (j,) in rows("SELECT jornada FROM jornada_calendar WHERE state = 'done'")}


def lock_cells(i: int, ahead: int, done: set[int]) -> list[dict[str, Any]]:
    """The forecast at t (the cell contract) for the lock of jornada i and, with ahead, the next ones, each one
    done: a row a player and jornada, his expected points, the chance he plays and whether he is flagged
    (player_status: injured, doubtful, suspended)."""
    return [{"jornada": j, "player": k, "predicted": e, "plays": p, "flagged": int(f)}
            for j, k, e, p, f in rows(
                "SELECT c.jornada, c.player, c.e, c.p, EXISTS (SELECT 1 FROM player_status s"
                " WHERE s.player = c.player AND s.status IS NOT NULL) FROM cell c WHERE c.jornada BETWEEN ? AND ?",
                (i, i + ahead)) if j in done]


def backtest(ahead: int = 0) -> list[dict[str, Any]]:
    """The forecast at each past lock (assemble.forecast makes it, the cell
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


def keep_replayed(at: str, jornada: int) -> None:
    """The cell contract at t, the next jornada's, into replay_cell for this variant and moment (the line-up
    snapshot at), replacing what an earlier replay left there."""
    with closing(connect()) as db, db:
        db.execute("DELETE FROM replay_cell WHERE variant = variant() AND as_of = ?", (at, ))
        db.execute("INSERT INTO replay_cell SELECT variant(), ?, c.jornada, c.player, c.e, EXISTS ("
                   "SELECT 1 FROM player_status s WHERE s.player = c.player AND s.status IS NOT NULL)"
                   " FROM cell c WHERE c.jornada = ?", (at, jornada))


def replay(variant: str = REPLAY_VARIANT, shard: tuple[int, int] = (0, 1)) -> int:
    """Forecast the next jornada at every line-up snapshot before a played
    jornada's lock (replay_moments) under the variant's constants
    (assemble.applied), into replay_cell (keep_replayed), which the view
    replayed reads with what then happened. shard (i, n) takes every nth
    moment."""
    with applied(variant):
        todo = [tuple(r) for r in rows("SELECT jornada, as_of FROM replay_moments"
                                       " WHERE jornada IN (SELECT jornada FROM jornada_calendar WHERE state = 'done')"
                                       " ORDER BY as_of")
                ][shard[0]::shard[1]]
        with replaying():
            for j, at in todo:
                moment = snapshot_stamp(at)
                assert moment is not None
                set_now(moment)
                for attempt in range(5):
                    try:    # another shard connecting re-creates the views under this one
                        set_look(moment=forecast())
                        keep_replayed(at, j)
                        break
                    except sqlite3.OperationalError as e:
                        if "schema has changed" not in str(e) or attempt == 4:
                            raise
                        time.sleep(1 + attempt)
                print("  replay %s j%d %s" % (at, j, variant), flush=True)
    return len(todo)


def label(kind: str, k: str, names: dict[str, str]) -> str:
    """A move as the grades say it."""
    return "%s %s" % (kind, names.get(k, k))


def decisions() -> list[dict[str, Any]]:
    """Make each past lock's plan again with this code (decision.plan_rows) and set each of its steps, as the
    plan values it (decision.marginals: the plan's worth less the plan without the step, by worth_on), against
    the same on what its players then scored: the points the step was forecast to add over the jornadas played
    since, and what it added; and the value its player was to gain (bought) or lose (sold) over the price
    horizon, in millions, against what he did."""
    locks, actual = round_locks(), jornada_points()
    done = done_jornadas()
    made = []
    for i in at_locks():
        inp = decision.read_inputs()
        steps, _ = decision.plan_rows(inp)
        e = {(k, j): x for k, j, x in inp["cell"]}
        seen = {k: (name, value or 0.0, trend or 0.0) for k, name, value, trend in rows(
            "SELECT r.player, name_shown(coalesce(nullif(r.name, ''), r.player)), r.value, t.trend"
            " FROM player_roster r LEFT JOIN price_trend t ON t.ff_id = r.player")}
        made.append((i, inp, steps, e, seen, set(rounds().rem), price_momentum().horizon))
    out = []
    for i, inp, steps, e, seen, rem, horizon in made:
        # what happened is read with t put back: as of the lock, no later price is seen
        rose = price_change(locks[i].strftime("%Y-%m-%d"), horizon)
        later = sorted(j for j in done if j >= i and j in rem)
        names = {k: f[0] for k, f in seen.items()}
        forecast = decision.marginals(inp, steps, e, later)
        happened = decision.marginals(inp, steps, actual, later)

        def worth(kind: str, k: str, pct: Callable[[str], float | None]) -> float | None:
            """What the step's player was worth more (bought) or less
            (sold) after the horizon, in millions; None until it has passed."""
            got = pct(k)
            if got is None:
                return None
            value = seen[k][1] if k in seen else 0.0
            return (1 if kind == "buy" else -1) * value * got / 100e6
        for (_, kind, k, *_), pred, real in zip(steps, forecast, happened):
            out.append({
                "lock": i, "jornadas": len(later), "move": label(kind, k, names),
                "group": "plan", "kind": "sell" if kind == "sell" else "get",
                "chance": None, "pred": pred, "real": real,
                "value_pred": worth(kind, k, lambda k: seen[k][2] if k in seen else 0.0),
                "value_real": worth(kind, k, lambda k: rose.get(k))})
    return out


def ceiling() -> list[dict[str, Any]]:
    """The decision's measure at each past lock (before_locks), on the real
    points of every jornada played from that lock to the last with real
    points (and, as next_*, of those up to the next lock only): floor, the squad you
    had at t as it was; ours, the plan (decision.plan_rows) on the forecast;
    ceiling, the same plan_rows with every cell's e the player's real points
    in the window's jornadas (0 if he did not play, 0 after the window), the same constraints and bid odds.
    All three scored by decision.worth_on. gap: decision.expected_gap at the lock (the plan's e over
    standing pat's, on the forecast)."""
    real = jornada_points()
    locks, done = before_locks(), sorted(done_jornadas())
    out = []
    for j in at_locks():
        later = [i for i in locks.values() if i > j]
        js = [i for i in done if i >= j]
        nxt = [i for i in js if not later or i < min(later)]
        inp = decision.read_inputs()
        out.append({"jornada": j, "gap": decision.expected_gap(inp), **decision.measure(inp, real, js),
                    **{"next_" + k: v for k, v in decision.measure(inp, real, nxt).items()}})
    return sorted(out, key=lambda g: g["jornada"])


def ceiling_report(got: list[dict[str, Any]]) -> str:
    """A line a lock and the season's total: share = (ours - floor) / (ceiling - floor),
    over every played jornada from the lock, and next = the same over the next jornada's span only."""
    def share(f: float, o: float, c: float) -> str:
        return "%6.2f" % ((o - f) / (c - f)) if c > f else "     -"
    def cols(g: dict[str, float], pre: str) -> tuple[float, float, float]:
        return g[pre + "floor"], g[pre + "ours"], g[pre + "ceiling"]
    lines = ["j%-3s %7.1f %7.1f %7.1f %s %s" % (g["jornada"], *cols(g, ""), share(*cols(g, "")),
                                                share(*cols(g, "next_"))) for g in got]
    tot = {pre + k: sum(g[pre + k] for g in got) for pre in ("", "next_") for k in ("floor", "ours", "ceiling")}
    return "\n".join(["%-4s %7s %7s %7s %6s %6s" % ("", "floor", "ours", "ceiling", "share", "next")] + lines
                     + ["%-4s %7.1f %7.1f %7.1f %s %s" % ("all", *cols(tot, ""), share(*cols(tot, "")), share(*cols(tot, "next_")))])


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


def points_at(t: dt.datetime) -> dict[str, float]:
    """Each manager's team points as the standings (the ingest contract) stood at t; t is left set."""
    set_now(t)
    return {m: p for m, p in rows("SELECT manager, CAST(team_points AS REAL) FROM standings") if p is not None}


def sim_calibration() -> list[dict[str, Any]]:
    """The standings forecast held to account one jornada at a time: at each
    clean lock of a finished jornada, every manager's points that jornada
    (his best eleven of his squad then, the forecast then: league_jornada)
    against what he scored (his team_points over the window, standings)."""
    locks = round_locks()
    with closing(connect()) as db:
        kicks = [(j, snapshot_stamp(k) or dt.datetime.min.replace(tzinfo=dt.timezone.utc))
                 for j, k in db.execute("SELECT jornada, kick FROM kickoffs")]
    out: list[dict[str, Any]] = []
    windows = clean_windows(locks, kicks)
    for j in at_locks(set(windows)):
        start, end = windows[j]
        decision.inputs()
        dist = {m: (mean, sd) for m, mean, sd in rows("SELECT manager, mean, sd FROM league_jornada"
                                                      " WHERE jornada = ?", (j, ))}
        me = rows("SELECT me FROM look")[0][0]
        a0, a1 = points_at(start), points_at(end)     # t moves on to the window's ends; the next lock sets it again
        actual = {m: a1[m] - a0[m] for m in dist if m in a0 and m in a1}
        out += calibration_rows(j, dist, actual, me)
    return out


def decision_rows(moves: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """decisions' moves as rows: group (a SQL word) becomes reached."""
    return [{"ord": i, **{k: v for k, v in m.items() if k != "group"}, "reached": m["group"]}
            for i, m in enumerate(moves)]


# What the grade stage keeps, a set a run.
GRADED = ("grade_cell", "grade_decisions", "sim_calibration")


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
    fc, moves = backtest(GRADE_AHEAD), decision_rows(decisions())
    if run is not None:
        record([{"run_id": run, **r} for r in fc], "grade_cell")
        record([{"run_id": run, **r} for r in moves], "grade_decisions")
        record([{"run_id": run, **r} for r in sim_calibration()], "sim_calibration")
        superseded(run)
    forecasts = len({(r["from_j"], r["jornada"]) for r in fc})     # lock moments a jornada
    print("  grade: %d forecasts, %d moves over %d jornadas" % (forecasts, len(moves), resolved))
    return {"resolved": resolved, "forecasts": forecasts, "moves": len(moves)}


def log_measure(measure: str, values: dict[str, float]) -> None:
    """The headline numbers of a measure appended to measure_log, beside
    the code's short commit (measure_latest shows each against the one
    before). A variant's measure is named measure:variant."""
    import subprocess
    sha = subprocess.run(["git", "-C", str(Path(__file__).parent), "rev-parse", "--short", "HEAD"],
                         capture_output=True, text=True).stdout.strip() or "unknown"
    var = os.environ.get("LFG_VARIANT", "live")
    at = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    record([{"at": at, "commit_sha": sha, "measure": measure if var == "live" else "%s:%s" % (measure, var),
             "key": k, "value": v} for k, v in values.items()], "measure_log")


def ceiling_headline(got: list[dict[str, Any]]) -> dict[str, float]:
    """The season's totals of ceiling(): floor, ours, ceiling and the shares
    (all jornadas from each lock, and next only), and the decision's expected gap (the locks' summed)."""
    tot = {pre + k: sum(g[pre + k] for g in got) for pre in ("", "next_") for k in ("floor", "ours", "ceiling")}
    share = {pre + "share": (tot[pre + "ours"] - tot[pre + "floor"]) / (tot[pre + "ceiling"] - tot[pre + "floor"])
             for pre in ("", "next_") if tot[pre + "ceiling"] > tot[pre + "floor"]}
    return {**tot, **share, "expected_gap": sum(g["gap"] for g in got)}


def accuracy(ahead: int = 5) -> list[tuple[Any, ...]]:
    """The forecast at each lock (backtest) graded as a run of its own
    (trigger 'accuracy'): at the next jornada and at ahead, the rows
    (ahead, n, rmse, mae, r) of grade_accuracy."""
    with closing(connect()) as db, db:
        run = db.execute("INSERT INTO runs (started, as_of, trigger, commit_sha, ok)"
                         " VALUES ('', '', 'accuracy', '', 1)").lastrowid
    assert run is not None
    record([{"run_id": run, **r} for r in backtest(ahead)], "grade_cell")
    got = [tuple(r) for r in rows("SELECT ahead, n, rmse, mae, r FROM grade_accuracy"
                                  " WHERE run_id = ? AND ahead IN (0, ?) ORDER BY ahead", (run, ahead))]
    log_measure("accuracy", {"%s@%d" % (k, a): v for a, n, rmse, mae, r in got
                             for k, v in (("n", n), ("rmse", rmse), ("mae", mae), ("r", r))})
    return got


def view_table(view: str, path: Path | None = None) -> str:
    """A view (replayed, grade_forecast) made a table of its columns in the
    current tables (tidy.tables_in), for its readers' tests to fill."""
    with closing(connect(path)) as db, db:
        cols = ['"%s" %s' % (r[1], r[2]) for r in db.execute('PRAGMA table_info("%s")' % view)]
        db.execute('DROP VIEW "%s"' % view)
        db.execute('CREATE TABLE "%s" (%s)' % (view, ", ".join(cols)))
    return view


def _selftest_ceiling() -> None:
    """A tiny league at two locks, seeded as assemble writes it, through
    decision.read_inputs, plan_rows and the ceiling's measure: floor <= ours
    <= ceiling, and a report line each."""
    import tempfile
    from ffcore.fixtures import seed_league
    from ffcore.tidy import tables_in
    squad = {"k": "POR", "d1": "DEF", "d2": "DEF", "d3": "DEF", "m1": "MED", "m2": "MED", "m3": "MED",
             "m4": "MED", "f1": "DEL", "f2": "DEL", "f3": "DEL", "bench": "DEF"}
    e = {**{k: 4.0 for k in squad}, "m4": 2.0, "bench": 1.0}
    market = {"star": ("MED", 8e6), "keeper": ("POR", 6e6)}
    real = {**{(k, j): 4.0 for k in squad for j in (1, 2)}, ("star", 1): 10.0, ("star", 2): 4.0, ("keeper", 2): 9.0, ("m4", 1): 2.0}
    got: list[dict[str, float]] = []
    two: dict[str, float] = {}
    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        for j, (t, forecast_star) in enumerate((("2026-08-20", 8.0), ("2026-08-27", 3.0)), 1):
            set_now(dt.datetime.fromisoformat(t + "T17:59:00+00:00"))
            seed_league("me", 10e6,
                        {**{k: {"pos": p, "owner": "me", "value": 5e6} for k, p in squad.items()},
                         **{k: {"pos": p, "price": a} for k, (p, a) in market.items()}},
                        {i: {**{k: (x, 1.0) for k, x in e.items()}, "star": (forecast_star, 1.0), "keeper": (3.0, 1.0)}
                         for i in range(j, 3)})
            inp = decision.read_inputs()
            got.append({"jornada": j, "gap": decision.expected_gap(inp), **decision.measure(inp, real, [j]),
                        **{"next_" + k: v for k, v in decision.measure(inp, real, [j]).items()}})
            if j == 1:
                two = decision.measure(inp, real, [1, 2])
    for g in got:
        assert g["floor"] <= g["ours"] <= g["ceiling"], g
    assert [(g["floor"], g["ours"], g["ceiling"]) for g in got] == [(44.0, 50.0, 50.0), (44.0, 44.0, 49.0)], got
    assert ceiling_report(got).splitlines()[-1].endswith("0.55   0.55"), ceiling_report(got)
    assert [g["gap"] for g in got] == [12.0, 1.0], \
        "the plan's expected gain on the forecast: star (8) for m4 (2) in two jornadas; star (3) for m4 (2) in one"
    assert ceiling_headline(got)["expected_gap"] == 13.0, "the season's total of the locks' expected gaps"
    # a two-jornada window: the ceiling counts both jornadas' real points, none after
    assert (two["floor"], two["ours"], two["ceiling"]) == (88.0, 94.0, 99.0), two


def _selftest_measure_log() -> None:
    """measure_log keeps each run's headline numbers; measure_latest sets the latest beside the one before."""
    import tempfile
    from ffcore.tidy import tables_in
    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        one = {"floor": 1.0, "ours": 3.0, "ceiling": 5.0, "gap": 2.0}
        assert ceiling_headline([{**one, **{"next_" + k: v for k, v in one.items()}}])["share"] == 0.5
        log_measure("ceiling", ceiling_headline([{**one, **{"next_" + k: v for k, v in one.items()}}]))
        assert rows("SELECT value FROM measure_latest WHERE measure = 'ceiling' AND key = 'expected_gap'") == [(2.0, )]
        with closing(connect()) as db, db:
            db.execute("DELETE FROM measure_log")
        log_measure("ceiling", {"share": 0.25})
        assert rows("SELECT measure, key, value, previous FROM measure_latest") == [("ceiling", "share", 0.25, None)]
        (sha,), = rows("SELECT commit_sha FROM measure_log")
        assert sha != "unknown" and 4 <= len(sha) <= 12, sha
        log_measure("ceiling", {"share": 0.5})
        assert rows("SELECT key, value, previous, change FROM measure_latest") == [("share", 0.5, 0.25, 0.25)]


def locks_report() -> str:
    """How each jornada played went (lock_record): the chance of first and the finish the last decision before its
    lock saw, with its plan; whether anything could still stop you then; what you scored (a zero is a matchday
    lost). The league's real criterion, jornada after jornada; the totals say how many were lost."""
    def pct(x: float | None) -> str:
        return "   -" if x is None else "%3.0f%%" % (100 * x)
    got = rows("SELECT jornada, p_first, p_first_after, finish, finish_after, exposed, scored, lost FROM lock_record ORDER BY lock")
    lines = ["jornada  first  with plan  finish  with plan  exposed  scored"]
    lines += ["%7d  %s  %s  %6s  %9s  %7s  %6s%s" % (j, pct(p), pct(pa), "-" if f is None else "%.2f" % f,
                                                  "-" if fa is None else "%.2f" % fa, "-" if p is None else str(ex),
                                                  "-" if sc is None else "%.0f" % sc, "   LOST" if lost else "")
              for j, p, pa, f, fa, ex, sc, lost in got]
    lines.append("lost matchdays: %d of %d; exposed at the last decision: %d" % (
        sum(bool(r[7]) for r in got), len(got), sum(1 for r in got if r[1] is not None and r[5])))
    return "\n".join(lines)


def _selftest_locks() -> None:
    """lock_record: for each jornada played, the last decision before its lock (not a later one), what could still
    stop you there, and what you scored before the next lock (the last seen)."""
    import re
    import sqlite3
    from ffcore.db import stamp_jd
    sql = Path(__file__).parent / "ffcore" / "schema.sql"
    view = re.search(r"CREATE VIEW lock_record AS\n(.*?);\n", sql.read_text(), re.S)
    assert view
    db = sqlite3.connect(":memory:")
    db.create_function("stamp_jd", 1, stamp_jd)
    db.create_function("cut", 0, lambda: "2026-11-30T0000Z")
    db.executescript("""
        CREATE TABLE round_locks (jornada, lock);
        CREATE TABLE decision_run (run_id, cut, p_first_after, finish_after);
        CREATE TABLE decision_standings (run_id, manager, me, p_first, finish);
        CREATE TABLE decision_blocker (run_id, future, blocker, amount);
        CREATE TABLE api_standings (observed_at, manager, fixture_points);
    """)
    db.executemany("INSERT INTO round_locks VALUES (?, ?)", [(1, "2026-11-06T2000Z"), (2, "2026-11-13T2000Z"), (3, "2026-11-20T2000Z")])
    db.executemany("INSERT INTO decision_run VALUES (?, ?, ?, ?)", [(1, "2026-11-05T1000Z", 0.2, 3.0), (2, "2026-11-06T0900Z", 0.3, 2.0),
                                                                   (3, "2026-11-07T0900Z", 0.9, 1.0)])
    db.executemany("INSERT INTO decision_standings VALUES (?, 'me', 1, ?, ?)", [(1, 0.1, 3.5), (2, 0.15, 2.5), (3, 0.5, 1.5)])
    db.execute("INSERT INTO decision_blocker VALUES (2, 'nothing', 'debt', -1.0)")
    db.executemany("INSERT INTO api_standings VALUES (?, 'me', ?)", [("2026-11-10T1000Z", 40.0), ("2026-11-12T1000Z", 50.0),
                                                                    ("2026-11-14T1000Z", 0.0), ("2026-11-19T1000Z", 0.0)])
    got = db.execute("SELECT jornada, run_id, p_first, p_first_after, exposed, scored, lost FROM (%s) ORDER BY jornada" % view.group(1)).fetchall()
    assert got == [(1, 2, 0.15, 0.3, 1, 50.0, 0), (2, 3, 0.5, 0.9, 0, 0.0, 1)], \
        "jornada 1: the decision of 09:00 that morning (not the one before or the day after), exposed once, 50 scored; jornada 2: a zero is a lost matchday"


def _selftest() -> None:
    _selftest_locks()
    _selftest_ceiling()
    _selftest_measure_log()
    import tempfile
    from ffcore.tidy import store_sql, tables_in
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
        keep_replayed("t1", 1)
        keep_replayed("t1", 1)
        keep_replayed("t2", 2)
        assert rows("SELECT variant, as_of, jornada, player, predicted, flagged FROM replay_cell ORDER BY as_of, player") \
            == [("live", "t1", 1, "a", 4.0, 0), ("live", "t1", 1, "b", 1.0, 0), ("live", "t2", 2, "a", 4.0, 0)], \
            "the next jornada's cells at each replay moment, once a variant and moment however often it is replayed"

    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        store_sql("api_standings", [{"observed_at": at, "manager": m, "team_points": pts}
                                    for at, m, pts in (("2026-08-01T0000Z", "me", "10"), ("2026-08-01T0000Z", "riv", "12"),
                                                       ("2026-08-09T0000Z", "me", "25"), ("2026-08-09T0000Z", "riv", "14"))])
        assert points_at(dt.datetime(2026, 8, 5, tzinfo=dt.timezone.utc)) == {"me": 10.0, "riv": 12.0}
        assert points_at(dt.datetime(2026, 8, 10, tzinfo=dt.timezone.utc)) == {"me": 25.0, "riv": 14.0}, \
            "each manager's points as the standings stood at t (the contract, not the scraped table)"
        set_now(None)
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
            record([{"run_id": r, "from_j": 7, "jornada": 7, "player": "p", "predicted": 1.0, "flagged": 0}],
                   "grade_cell", path)
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
                 "home": "h%d" % j, "away": "a%d" % j, "kickoff": k, "score": "1-0" if j == 2 else ""}
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
            assert [r[0] for r in connect().execute("SELECT at FROM lineup_snapshots ORDER BY at")] == [
                "2026-08-10T0800Z", "2026-08-16T1900Z", "2026-08-18T0800Z", "2026-08-23T1859Z", "2026-08-23T1900Z",
                "2026-08-29T0800Z"], "the line-up facts' moments: one a time, the futbolfantasy source's"
            record([{"variant": "live", "as_of": "2026-08-10T0800Z", "jornada": 2, "player": "p", "predicted": 3.0,
                     "flagged": 1}], "replay_cell")
            got = [tuple(r) for r in connect().execute(
                "SELECT variant, as_of, jornada, player, predicted, actual, flagged, round(lead_h, 6) FROM replayed")]
            assert got == [("live", "2026-08-10T0800Z", 2, "p", 3.0, 0.0, 1, 155.0)], \
                ("a replayed forecast against the hours before the lock it was made", got)
        finally:
            set_now(None)

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
    if sys.argv[1:2] == ["accuracy"]:
        # accuracy: predicted e against actual points, next jornada and five ahead, at every past lock
        # (records a run of trigger 'accuracy' in the database it reads)
        held = applied(os.environ.get("LFG_VARIANT", "live"))
        held.__enter__()
        for a, n, rmse, mae, r in accuracy():
            print("ahead %d  n=%d  rmse=%.4f  mae=%.4f  r=%.4f" % (a, n, rmse, mae, r), flush=True)
        os._exit(0)
    if sys.argv[1:2] == ["ceiling"]:
        # ceiling: at each past lock, standing still, this code's plan and the plan on real points
        # LFG_VARIANT=name: the model as that variant sets it (grading.py variant NAME CONSTANT=VALUE)
        held = applied(os.environ.get("LFG_VARIANT", "live"))
        held.__enter__()
        got = ceiling()
        print(ceiling_report(got), flush=True)
        log_measure("ceiling", ceiling_headline(got))
        # Out without freeing what it made one object at a time: ~8 s of a
        # backtest's exit (2026-10-31), every file already closed and written.
        os._exit(0)
    if sys.argv[1:2] == ["locks"]:
        print(locks_report())
        raise SystemExit(0)
    print("grading.py --selftest | accuracy | locks | ceiling (LFG_VARIANT=name) | replay [JOBS] (LFG_VARIANT=name) | variant NAME CONSTANT=VALUE ...")
    raise SystemExit(2)
