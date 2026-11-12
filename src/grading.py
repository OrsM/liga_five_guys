from __future__ import annotations

import datetime as dt
import os
import sys
from contextlib import closing
from pathlib import Path
from typing import Any

import decision
from assemble import built, forecast
from ffcore import auction
from ffcore.db import connect, record, replaying, rows, set_look, set_now, this_run
from ffcore.jornadas import round_locks
from ffcore.parse import snapshot_stamp

__all__ = ["backtest", "superseded", "view_table", "record_grades"]

GRADE_AHEAD = 4  # jornadas past the lock each replayed forecast is scored on


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


def opportunity_grade(top: int = 5, span: int = 4) -> list[dict[str, Any]]:
    done = done_jornadas()
    actual = {(k, j): x for k, j, x in rows("SELECT player, jornada, sum(pts) FROM scored GROUP BY player, jornada")}
    out = []
    with replaying():
        for c in decision.seen_cuts():
            set_now(snapshot_stamp(c))
            try:
                built()
            except SystemExit:
                continue
            inp = decision.read_inputs()
            pos, cells = decision.cells_of(inp)
            got = decision.opportunity_rows(inp)
            band = [(k, p, asking if asking is not None else value) for k, p, _, asking, value in inp["dec_universe"] if k in pos]
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


def newest_result() -> float | None:
    """When the newest auction with an outcome closed (a Julian day), as of t; None if there is none."""
    (got,), = rows("SELECT max(julianday(close)) FROM auction_results")
    return got


def scored_at(run: int, path: Path | None = None) -> str | None:
    """When (runs.as_of) the latest earlier run of this run's code (runs.commit_sha) that recorded the auction scores
    (auction_brier) was made; None if none was."""
    with closing(connect(path)) as db:
        return db.execute("SELECT max(e.as_of) FROM auction_brier b JOIN runs e ON e.run_id = b.run_id"
                          " JOIN runs r ON r.run_id = ? AND e.commit_sha = r.commit_sha AND e.run_id < r.run_id",
                          (run,)).fetchone()[0]


def auctions_scored(run: int, newest: float | None) -> bool:
    """Whether an earlier run of this run's code scored the auctions with the newest outcome as it is now (newest):
    its scores stand, and no auction closed since would change them."""
    then = scored_at(run)
    if then is None or newest is None:
        return False
    with replaying():
        set_now(snapshot_stamp(then))
        return newest_result() == newest


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
    scored = {} if run is not None and auctions_scored(run, newest_result()) else record_auction(run)
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
        "done_jornadas": lambda: {1, 2}, "this_run": lambda: 5, "newest_result": lambda: 1.0,
        "graded": lambda run, last: state["graded"], "auctions_scored": lambda run, newest: state["scored"],
        "record_auction": auction_scored, "backtest": regraded,
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


def _selftest() -> None:
    _selftest_measure_log()
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
    print("grading.py --selftest | accuracy | price | opportunities")
    raise SystemExit(2)
