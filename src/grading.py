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
from ffcore.db import connect, record, replaying, rows, set_now, this_run
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
    """Each player's points in each jornada he scored in (player_jornadas)."""
    return {(k, j): pts for k, j, pts in rows("SELECT player, jornada, pts FROM player_jornadas")}


def done_jornadas() -> set[int]:
    """The jornadas every match of which has a score (jornadas_done): the
    ones a forecast can be graded on."""
    return {j for (j,) in rows("SELECT jornada FROM jornadas_done")}


def backtest(ahead: int = 0) -> list[dict[str, Any]]:
    """The forecast at each past lock (assemble.forecast): its moment, for
    the jornada it locks and, with ahead, the next ones too, each one done.
    Graded in SQL (grade_forecast)."""
    done = done_jornadas()
    out = []
    with replaying():
        for t, i in before_locks().items():
            set_now(t)
            moment = forecast()
            have = {j for (j, ) in rows("SELECT DISTINCT jornada FROM forecast_cell WHERE moment = ?", (moment, ))}
            out += [{"from_j": i, "jornada": j, "moment": moment}
                    for j in range(i, i + ahead + 1) if j in done and j in have]
    return out


def replay(variant: str = REPLAY_VARIANT, shard: tuple[int, int] = (0, 1)) -> int:
    """Forecast the next jornada at every line-up snapshot before a played
    jornada's lock (replay_moments) under the variant's constants
    (assemble.applied), into the forecast's store (assemble.forecast),
    which the view replayed reads with what then happened. shard (i, n)
    takes every nth moment."""
    with applied(variant):
        todo = [tuple(r) for r in rows("SELECT jornada, as_of FROM replay_moments"
                                       " WHERE jornada IN (SELECT jornada FROM jornadas_done) ORDER BY as_of")
                ][shard[0]::shard[1]]
        with replaying():
            for j, at in todo:
                moment = snapshot_stamp(at)
                assert moment is not None
                set_now(moment)
                for attempt in range(5):
                    try:    # another shard connecting re-creates the views under this one
                        forecast()
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

        def worth(kind: str, k: str, pct: Callable[[str], float | None]) -> float | None:
            """What the move's player was worth more (bought) or less
            (sold) after the horizon, in millions; None until it has passed."""
            got = pct(k)
            if got is None:
                return None
            value = seen[k][1] if k in seen else 0.0
            return (1 if kind == "buy" else -1) * value * got / 100e6

        def change(kind: str, k: str, pts: Callable[[str, int], float]) -> float:
            return (1 if kind == "buy" else -1) * sum(pts(k, j) for j in later)
        for kind, k, amount, in_plan in moved:
            out.append({
                "lock": i, "jornadas": len(later), "move": label(kind, k, names),
                "group": "plan" if in_plan else "left out",
                "kind": "sell" if kind == "sell" else "get",
                "chance": None,
                "pred": change(kind, k, lambda k, j: e.get((k, j), 0.0)),
                "real": change(kind, k, lambda k, j: actual.get((k, j), 0.0)),
                "value_pred": worth(kind, k, lambda k: seen[k][2] if k in seen else 0.0),
                "value_real": worth(kind, k, lambda k: rose.get(k))})
    return out


def ceiling() -> list[dict[str, Any]]:
    """The decision's measure at each past lock (before_locks), on the real
    points of the jornadas from that lock to the next: floor, the squad you
    had at t as it was; ours, the plan (decision.plan_rows) on the forecast;
    ceiling, the same plan_rows with every cell's e the player's real points
    in that jornada (0 if he did not play), the same constraints and bid odds.
    All three scored by decision.worth_on."""
    real = jornada_points()
    locks, done = before_locks(), sorted(done_jornadas())
    out = []
    for j in at_locks():
        later = [i for i in locks.values() if i > j]
        js = [i for i in done if i >= j and (not later or i < min(later))]
        inp = decision.read_inputs()
        out.append({"jornada": j, **decision.measure(inp, real, js)})
    return sorted(out, key=lambda g: g["jornada"])


def ceiling_report(got: list[dict[str, Any]]) -> str:
    """A line a lock and the season's total: share = (ours - floor) / (ceiling - floor)."""
    def share(f: float, o: float, c: float) -> str:
        return "%6.2f" % ((o - f) / (c - f)) if c > f else "     -"
    lines = ["j%-3s %7.1f %7.1f %7.1f %s" % (g["jornada"], g["floor"], g["ours"], g["ceiling"],
                                             share(g["floor"], g["ours"], g["ceiling"])) for g in got]
    f, o, c = (sum(g[k] for g in got) for k in ("floor", "ours", "ceiling"))
    return "\n".join(["%-4s %7s %7s %7s %6s" % ("", "floor", "ours", "ceiling", "share")] + lines
                     + ["%-4s %7.1f %7.1f %7.1f %s" % ("all", f, o, c, share(f, o, c))])


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
GRADED = ("grade_moment", "grade_decisions", "sim_calibration")


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
    fc, moves = backtest(GRADE_AHEAD), decision_rows(decisions())
    if run is not None:
        record([{"run_id": run, **r} for r in fc], "grade_moment")
        record([{"run_id": run, **r} for r in moves], "grade_decisions")
        record([{"run_id": run, **r} for r in sim_calibration()], "sim_calibration")
        superseded(run)
    print("  grade: %d forecasts, %d moves over %d jornadas" % (len(fc), len(moves), resolved))
    return {"resolved": resolved, "forecasts": len(fc), "moves": len(moves)}     # forecasts: lock moments a jornada


def accuracy(ahead: int = 5) -> list[tuple[Any, ...]]:
    """The forecast at each lock (backtest) graded as a run of its own
    (trigger 'accuracy'): at the next jornada and at ahead, the rows
    (ahead, n, rmse, mae, r) of grade_accuracy."""
    with closing(connect()) as db, db:
        run = db.execute("INSERT INTO runs (started, as_of, trigger, commit_sha, ok)"
                         " VALUES ('', '', 'accuracy', '', 1)").lastrowid
    assert run is not None
    record([{"run_id": run, **r} for r in backtest(ahead)], "grade_moment")
    return [tuple(r) for r in rows("SELECT ahead, n, rmse, mae, r FROM grade_accuracy"
                                   " WHERE run_id = ? AND ahead IN (0, ?) ORDER BY ahead", (run, ahead))]


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
    real = {**{(k, j): 4.0 for k in squad for j in (1, 2)}, ("star", 1): 10.0, ("keeper", 2): 9.0, ("m4", 1): 2.0}
    got = []
    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        for j, (t, forecast_star) in enumerate((("2026-08-20", 8.0), ("2026-08-27", 3.0)), 1):
            set_now(dt.datetime.fromisoformat(t + "T17:59:00+00:00"))
            seed_league("me", 10e6,
                        {**{k: {"pos": p, "owner": "me", "value": 5e6} for k, p in squad.items()},
                         **{k: {"pos": p, "price": a} for k, (p, a) in market.items()}},
                        {j: {**{k: (x, 1.0) for k, x in e.items()}, "star": (forecast_star, 1.0), "keeper": (3.0, 1.0)}})
            got.append({"jornada": j, **decision.measure(decision.read_inputs(), real, [j])})
    for g in got:
        assert g["floor"] <= g["ours"] <= g["ceiling"], g
    assert [(g["floor"], g["ours"], g["ceiling"]) for g in got] == [(44.0, 50.0, 50.0), (44.0, 44.0, 49.0)], got
    assert ceiling_report(got).splitlines()[-1].endswith("0.55"), ceiling_report(got)


def _selftest() -> None:
    _selftest_ceiling()
    import tempfile
    from ffcore.tidy import store_sql, tables_in
    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        record([{"moment": 3, "code": "c", "as_of": "t"}], "forecast_moment")
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
        print(ceiling_report(ceiling()), flush=True)
        # Out without freeing what it made one object at a time: ~8 s of a
        # backtest's exit (2026-10-31), every file already closed and written.
        os._exit(0)
    print("grading.py --selftest | accuracy | ceiling (LFG_VARIANT=name) | replay [JOBS] (LFG_VARIANT=name) | variant NAME CONSTANT=VALUE ...")
    raise SystemExit(2)
