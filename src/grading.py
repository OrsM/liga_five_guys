from __future__ import annotations

import datetime as dt
import os
import sqlite3
import sys
import time
from collections.abc import Callable
from dataclasses import replace
from contextlib import closing
from pathlib import Path
from typing import Any

import numpy as np

from ffcore.forecast import expected_points
from ffcore.schedule import expectations
from ffcore.season import simulate
from ffcore.jornadas import round_locks, rounds_ahead
from ffcore.points import done_jornadas, jornada_points

from assemble import applied, built, fixture_ratings, price_change, price_momentum, scorer, universe
from decide import Action, Universe, board, verdict
from sim import with_squad
from ffcore.clock import set_now, snapshot_stamp
from ffcore.db import connect, named, record, this_run
from ffcore.tidy import LINEUP_SOURCE, current
__all__ = ["backtest", "clean_windows", "calibration_rows", "sim_calibration", "phantom_season", "replay", "replay_rows", "decision_rows", "decisions", "due", "forecast_rows",
           "record_grades"]

REPLAY_VARIANT = "live"
GRADE_AHEAD = 4  # jornadas past the lock each replayed forecast is scored on


_AT: dict[int, Universe | None] = {}


def universe_at(jornada: int, locks: dict[int, dt.datetime]) -> Universe | None:
    """The universe a minute before this jornada locked (None if no board
    could be built then), with the clock set to it. Built once however many
    of the replays ask: decisions, phantom_season and sim_calibration all
    stand at the same locks, and a universe costs ~10s."""
    set_now(locks[jornada] - dt.timedelta(minutes=1))
    if jornada not in _AT:
        built.cache_clear()
        try:
            _AT[jornada] = universe()
        except SystemExit as e:
            print("j%d: no board (%s)" % (jornada, e), file=sys.stderr)
            _AT[jornada] = None
    return _AT[jornada]


def backtest(ahead: int = 0) -> list[dict[str, Any]]:
    """Rebuild the forecast at each past lock: every player's forecast cell
    (points if he plays, chance he plays) for the jornada it locks and, with
    ahead, the next ones too, and who was listed injured, doubtful or
    suspended then. Scored in SQL (grade_error)."""
    locks = round_locks()
    done = done_jornadas()
    out = []
    try:
        for i in sorted(done & set(locks), key=lambda j: locks[j]):
            set_now(locks[i] - dt.timedelta(minutes=1))
            market = current("market")
            sc = scorer(market, current("lineups", LINEUP_SOURCE))
            per_j = expectations(sc, fixture_ratings(market), set(sc.lookup),
                                 current("matches"), rounds_ahead()).per_j
            flagged = {k for k in sc.lookup if sc.starts.status_of(k)}
            for j in range(i, i + ahead + 1):
                if j not in done or j not in per_j:
                    continue
                out.append({"jornada": j, "from": i, "pred": dict(per_j[j]),
                            "hurt": sorted(k for k in per_j[j] if k in flagged)})
    finally:
        set_now(None)
    return out


def replay_rows(variant: str, as_of: str, jornada: int, lead_h: float, pred: dict[str, float],
                hurt: set[str], actual: dict[tuple[str, int], float]) -> list[dict[str, Any]]:
    """One moment's forecast of a jornada, a row a player, with what he scored."""
    return [{"variant": variant, "as_of": as_of, "jornada": jornada, "player": k, "predicted": p,
             "actual": actual.get((k, jornada), 0.0), "flagged": int(k in hurt), "lead_h": lead_h}
            for k, p in pred.items()]


def replay(variant: str = REPLAY_VARIANT, shard: tuple[int, int] = (0, 1)) -> int:
    """Forecast the next jornada at every line-up snapshot before a played
    jornada's lock (replay_moments) with this code, and keep it
    (replay_forecast) against what then happened, under the variant's
    constants (assemble.applied). Resumes: a moment already kept for the
    variant is skipped. shard (i, n) takes every nth moment."""
    locks, actual = round_locks(), jornada_points()
    played = done_jornadas()
    with closing(connect()) as db:
        have = {a for (a,) in db.execute("SELECT DISTINCT as_of FROM replay_forecast WHERE variant = ?", (variant,))}
        todo = [(j, a) for j, a, _l in db.execute("SELECT jornada, as_of, lock FROM replay_moments")
                if j in played and a not in have][shard[0]::shard[1]]
    kept = 0
    held = applied(variant)
    held.__enter__()

    def moment_rows(j: int, at: str) -> list[dict[str, Any]]:
        moment = snapshot_stamp(at)
        assert moment is not None
        set_now(moment)
        market = current("market")
        sc = scorer(market, current("lineups", LINEUP_SOURCE))
        per_j = expectations(sc, fixture_ratings(market), set(sc.lookup),
                             current("matches"), rounds_ahead()).per_j
        if j not in per_j:
            return []
        hurt = {k for k in sc.lookup if sc.starts.status_of(k)}
        pred = {k: expected_points(cell) for k, cell in per_j[j].items()}
        return replay_rows(variant, at, j, (locks[j] - moment).total_seconds() / 3600, pred, hurt, actual)
    try:
        for j, at in todo:
            for attempt in range(5):
                try:    # another shard connecting re-creates the views under this one
                    out = moment_rows(j, at)
                    break
                except sqlite3.OperationalError as e:
                    if "schema has changed" not in str(e) or attempt == 4:
                        raise
                    time.sleep(1 + attempt)
            record(out, "replay_forecast")
            kept += bool(out)
            print("  replay %s j%d %s: %d players" % (at, j, variant, len(out)), flush=True)
    finally:
        set_now(None)
        held.__exit__(None, None, None)
    return kept


def decisions() -> list[dict[str, Any]]:
    """Rebuild each past lock's board with this code and set every ranked
    move against what its players then scored: the change in points it
    forecast (players got minus players sold) over the jornadas played
    since, and the change that happened; and the value its players were
    to gain (bought) or lose (sold) over the price horizon, in millions,
    against what they did."""
    locks, actual = round_locks(), jornada_points()
    done = done_jornadas()
    out = []
    try:
        for i in sorted(done & set(locks), key=lambda j: locks[j]):
            u = universe_at(i, locks)
            if u is None:
                continue
            b, per_j, m = board(u), u.forecaster.per_jornada, u.market
            later = sorted(j for j in done if j >= i and j in per_j)
            rose = price_change(locks[i].strftime("%Y-%m-%d"), price_momentum().horizon)

            def worth(a: Action, pct: Callable[[str], float]) -> float:
                return (m.value.get(a.buy, 0.0) * pct(a.buy) - sum(
                    m.value.get(k, 0.0) * pct(k) for k in a.sell)) / 100e6

            def change(a: Action, pts: Callable[[str, int], float]) -> float:
                return sum(pts(a.buy, j) for j in later) - sum(
                    pts(k, j) for k in a.sell for j in later)
            for r in b.rows:
                a = r.action
                out.append({
                    "lock": i, "jornadas": len(later), "move": a.label(u.market.names),
                    "group": "plan" if r in b.plan else
                             "cleared" if verdict(r) is None else "rejected",
                    "kind": "sell" if not a.buy else "get",
                    "chance": r.p_better,
                    "pred": change(a, lambda k, j: expected_points(per_j[j].get(k))),
                    "real": change(a, lambda k, j: actual.get((k, j), 0.0)),
                    "value_pred": worth(a, lambda k: m.trend.get(k, 0.0)),
                    "value_real": worth(a, lambda k: rose.get(k, 0.0))})
    finally:
        set_now(None)
        built.cache_clear()
    return out


def phantom_season() -> list[dict[str, Any]]:
    """The league's sixth manager: your squad at the first lock, then at each
    lock the plan this code makes for the squad and cash he has (bids win at
    the model's price, rivals do not react), his eleven picked from that
    lock's forecast and scored on what they got; beside a manager who never
    moves, picked the same way. One row a played jornada."""
    locks, actual = round_locks(), jornada_points()
    done = done_jornadas()
    squad: dict[str, str] | None = None
    hold: dict[str, str] = {}
    cash = 0.0
    out = []
    try:
        for i in sorted(done & set(locks), key=lambda j: locks[j]):
            u = universe_at(i, locks)
            if u is None:
                continue
            if squad is None:
                squad, hold, cash = dict(u.mine), dict(u.mine), u.market.cash
            up = with_squad(u, squad, cash)
            acts = [r.action for r in board(up).plan]
            fielded = up.after(*acts).outlook.xi.players
            still = with_squad(u, hold, cash).outlook.xi.players
            out.append({"jornada": i, "phantom": sum(actual.get((k, i), 0.0) for k in fielded),
                        "standpat": sum(actual.get((k, i), 0.0) for k in still),
                        "moves": "; ".join(a.label(up.market.names) for a in acts),
                        "cash": up.market.left(acts)})
            squad, cash = up.squad_after(*acts), up.market.left(acts)
    finally:
        set_now(None)
        built.cache_clear()
    return out


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


def calibration_rows(jornada: int, draws: dict[str, np.ndarray[Any, Any]], actual: dict[str, float],
                     me: str) -> list[dict[str, Any]]:
    """Where what happened falls in the simulation's draws of one jornada,
    for each manager, and for you minus him (the draws share their trials, so
    what the league as a whole does cancels, as it does in who finishes above)."""
    def pit(v: np.ndarray[Any, Any], x: float) -> float:
        return float((v < x).mean() + 0.5 * (v == x).mean())
    out = []
    for m, v in draws.items():
        if m not in actual:
            continue
        row: dict[str, Any] = {"jornada": jornada, "manager": m, "actual": actual[m],
                               "mean": float(v.mean()), "sd": float(v.std(ddof=1)), "pit": pit(v, actual[m]),
                               "diff_actual": None, "diff_mean": None, "diff_sd": None, "diff_pit": None}
        if m != me and me in draws and me in actual:
            d = draws[me] - v
            row.update(diff_actual=actual[me] - actual[m], diff_mean=float(d.mean()),
                       diff_sd=float(d.std(ddof=1)), diff_pit=pit(d, actual[me] - actual[m]))
        out.append(row)
    return out


def sim_calibration() -> list[dict[str, Any]]:
    """The season simulation held to account one jornada at a time: at each
    clean lock, every manager's points that jornada drawn from the squads
    then (his best eleven, the forecast then) against what he scored (his
    team_points over the window)."""
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
            for j, (start, end) in sorted(clean_windows(locks, kicks).items()):
                u = universe_at(j, locks)
                if u is None:
                    continue
                st = replace(u.state, jornadas=[j], carried={}, fielded={})
                totals = simulate(st, u.forecaster, trials=4000, seed=1).totals
                actual = {}
                for m in totals:
                    a0, a1 = points(m, start), points(m, end)
                    if a0 is not None and a1 is not None:
                        actual[m] = a1 - a0
                out += calibration_rows(j, {m: np.array(v) for m, v in totals.items()
                                            if not m.startswith("__")}, actual, u.me)
        finally:
            set_now(None)
            built.cache_clear()
    return out


def forecast_rows(runs: list[dict[str, Any]], actual: dict[tuple[str, int], float]) -> list[dict[str, Any]]:
    """backtest's forecast cells, one row a player: what was forecast at the
    lock (from_j) for a jornada and his chance of playing, and what he
    scored (0 if nothing)."""
    return [{"from_j": r["from"], "jornada": r["jornada"], "player": k,
             "predicted": expected_points(cell), "plays": cell[1],
             "actual": actual.get((k, r["jornada"]), 0.0), "flagged": int(k in r["hurt"])}
            for r in runs for k, cell in r["pred"].items()]


def decision_rows(moves: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """decisions' moves as rows: group (a SQL word) becomes reached."""
    return [{"ord": i, **{k: v for k, v in m.items() if k != "group"}, "reached": m["group"]}
            for i, m in enumerate(moves)]


def due(run: int, resolved: float, path: Path | None = None) -> bool:
    """Whether to grade: never graded, or the code (the run's commit) or the
    jornadas resolved have changed since the last grade (grade_due)."""
    now, graded, then = named("grade_due", {"run": run}, path)[1][0]
    return graded is None or now is None or (now, resolved) != (graded, then)


def record_grades() -> dict[str, int]:
    """The grade stage (run.py grade): replay each past lock with this code,
    and keep every player forecast and every ranked move against what then
    happened; skipped when nothing is new (due)."""
    actual = jornada_points()
    resolved = len(done_jornadas())
    run = this_run()
    if run is not None and not due(run, resolved):
        print("  grade: same code, same %d jornadas: nothing new" % resolved)
        return {"resolved": resolved, "skipped": 1}
    _AT.clear()
    try:
        fc, moves = forecast_rows(backtest(GRADE_AHEAD), actual), decision_rows(decisions())
        if run is not None:
            record([{"run_id": run, **r} for r in fc], "grade_forecast")
            record([{"run_id": run, **r} for r in moves], "grade_decisions")
            record([{"run_id": run, **r} for r in phantom_season()], "phantom_jornadas")
            record([{"run_id": run, **r} for r in sim_calibration()], "sim_calibration")
    finally:
        _AT.clear()
    print("  grade: %d forecasts, %d moves over %d jornadas" % (len(fc), len(moves), resolved))
    return {"resolved": resolved, "forecasts": len(fc), "moves": len(moves)}


def _selftest() -> None:
    bt = [{"from": 7, "jornada": 8, "pred": {"a": (6.0, 0.5), "b": (2.0, 0.5)}, "hurt": ["b"]}]
    assert forecast_rows(bt, {("a", 8): 5.0}) == [
        {"from_j": 7, "jornada": 8, "player": "a", "predicted": 3.0, "plays": 0.5, "actual": 5.0, "flagged": 0},
        {"from_j": 7, "jornada": 8, "player": "b", "predicted": 1.0, "plays": 0.5, "actual": 0.0, "flagged": 1}], \
        "one row a player forecast, with his chance of playing; no points scored is 0"
    moves = decision_rows([{"lock": 7, "move": "buy X", "group": "plan", "kind": "get"},
                           {"lock": 7, "move": "sell Y", "group": "rejected", "kind": "sell"}])
    assert [(m["ord"], m["reached"], "group" in m) for m in moves] \
        == [(0, "plan", False), (1, "rejected", False)], moves

    import tempfile
    from ffcore.tidy import store_sql, tables_in
    with tempfile.TemporaryDirectory() as tmp, closing(connect(Path(tmp) / "lfg.db")) as db:
        path = Path(tmp) / "lfg.db"
        def run(commit: str, resolved: float | None) -> int:
            with db:
                cur = db.execute("INSERT INTO runs (started, as_of, trigger, commit_sha, ok)"
                                 " VALUES ('t', 't', 'service', ?, 1)", (commit,))
                if resolved is not None:
                    db.execute("INSERT INTO run_measures VALUES (?, 'grade', 'resolved', ?)",
                               (cur.lastrowid, resolved))
            assert cur.lastrowid is not None
            return cur.lastrowid
        assert due(run("a", None), 7, path), "never graded: due"
        run("a", 7)
        assert not due(run("a", None), 7, path), "same code, same jornadas: nothing new"
        assert due(run("a", None), 8, path), "a jornada resolved"
        assert due(run("b", None), 7, path), "new code"


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

    rr = replay_rows("v", "2026-08-10T0800Z", 2, 57.0, {"a": 3.0, "b": 1.0}, {"b"}, {("a", 2): 5.0})
    assert rr == [
        {"variant": "v", "as_of": "2026-08-10T0800Z", "jornada": 2, "player": "a", "predicted": 3.0,
         "actual": 5.0, "flagged": 0, "lead_h": 57.0},
        {"variant": "v", "as_of": "2026-08-10T0800Z", "jornada": 2, "player": "b", "predicted": 1.0,
         "actual": 0.0, "flagged": 1, "lead_h": 57.0}], "one row a player; no points is 0"
    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        # squared errors: 1h out 1, 4; 2h out 9, 0; 30h out 16
        record([{"variant": "v", "as_of": "t%d" % (i // 2), "jornada": 2, "player": k, "predicted": pr,
                 "actual": ac, "flagged": fl, "lead_h": h}
                for i, (k, pr, ac, fl, h) in enumerate([("a", 1, 0, 0, 1.0), ("b", 0, 2, 1, 1.0),
                                                        ("a", 3, 0, 0, 2.0), ("b", 2, 2, 0, 2.0),
                                                        ("a", 4, 0, 0, 30.0)])], "replay_forecast")
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
                    ("x", 3, "a", 9, 0, 0, 30.0)]], "replay_forecast")
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
    draws = {"me": np.array([40.0, 50.0, 60.0, 70.0]), "riv": np.array([30.0, 40.0, 50.0, 60.0])}
    cal = {r["manager"]: r for r in calibration_rows(7, draws, {"me": 55.0, "riv": 100.0}, "me")}
    assert (cal["me"]["mean"], cal["me"]["pit"], cal["me"]["diff_pit"]) == (55.0, 0.5, None), cal["me"]
    assert cal["riv"]["pit"] == 1.0 and cal["riv"]["actual"] == 100.0, "above every draw"
    assert (cal["riv"]["diff_actual"], cal["riv"]["diff_mean"], cal["riv"]["diff_sd"]) == (-45.0, 10.0, 0.0), \
        "me minus him: the draws share trials, so a constant 10 apart has no spread"
    assert cal["riv"]["diff_pit"] == 0.0, "-45 is below every draw of +10"
    assert calibration_rows(7, draws, {"me": 50.0}, "me")[0]["pit"] == 0.375, "a draw equal to it counts half"
    assert abs(cal["me"]["sd"] - float(np.std([40, 50, 60, 70], ddof=1))) < 1e-9

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
    print("grading.py --selftest | replay [JOBS] (LFG_VARIANT=name) | variant NAME CONSTANT=VALUE ...")
    raise SystemExit(2)
