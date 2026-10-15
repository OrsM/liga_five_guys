from __future__ import annotations

import datetime as dt
import os
import sqlite3
import sys
import time
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Any

from ffcore.forecast import expected_points
from ffcore.schedule import expectations
from ffcore.jornadas import round_locks, rounds_ahead
from ffcore.points import jornada_points

from assemble import applied, built, fixture_ratings, price_change, price_momentum, scorer, universe
from decide import Action, board, verdict
from sim import with_squad
from ffcore.clock import set_now, snapshot_stamp
from ffcore.db import connect, named, record, this_run
from ffcore.tidy import LINEUP_SOURCE, current
__all__ = ["backtest", "phantom_season", "replay", "replay_rows", "decision_rows", "decisions", "due", "forecast_rows",
           "record_grades"]

REPLAY_VARIANT = "live"
GRADE_AHEAD = 4  # jornadas past the lock each replayed forecast is scored on


def backtest(ahead: int = 0) -> list[dict[str, Any]]:
    """Rebuild the forecast at each past lock: every player's expected points
    for the jornada it locks and, with ahead, the next ones too, and who was
    listed injured, doubtful or suspended then. Scored in SQL (grade_error)."""
    locks, actual = round_locks(), jornada_points()
    done = {j for _k, j in actual}
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
                pred = {k: expected_points(cell) for k, cell in per_j[j].items()}
                out.append({"jornada": j, "from": i, "pred": pred,
                            "hurt": sorted(k for k in pred if k in flagged)})
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
    played = {j for _k, j in actual}
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
    done = {j for _k, j in actual}
    out = []
    try:
        for i in sorted(done & set(locks), key=lambda j: locks[j]):
            set_now(locks[i] - dt.timedelta(minutes=1))
            built.cache_clear()
            try:
                u = universe()
            except SystemExit as e:
                print("j%d: no board (%s)" % (i, e), file=sys.stderr)
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
    done = {j for _k, j in actual}
    squad: dict[str, str] | None = None
    hold: dict[str, str] = {}
    cash = 0.0
    out = []
    try:
        for i in sorted(done & set(locks), key=lambda j: locks[j]):
            set_now(locks[i] - dt.timedelta(minutes=1))
            built.cache_clear()
            try:
                u = universe()
            except SystemExit as e:
                print("j%d: no board (%s)" % (i, e), file=sys.stderr)
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


def forecast_rows(runs: list[dict[str, Any]], actual: dict[tuple[str, int], float]) -> list[dict[str, Any]]:
    """backtest's forecasts, one row a player: what was forecast at the
    lock (from_j) for a jornada, and what he scored (0 if nothing)."""
    return [{"from_j": r["from"], "jornada": r["jornada"], "player": k, "predicted": p,
             "actual": actual.get((k, r["jornada"]), 0.0), "flagged": int(k in r["hurt"])}
            for r in runs for k, p in r["pred"].items()]


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
    resolved = len({j for _k, j in actual})
    run = this_run()
    if run is not None and not due(run, resolved):
        print("  grade: same code, same %d jornadas: nothing new" % resolved)
        return {"resolved": resolved, "skipped": 1}
    fc, moves = forecast_rows(backtest(GRADE_AHEAD), actual), decision_rows(decisions())
    if run is not None:
        record([{"run_id": run, **r} for r in fc], "grade_forecast")
        record([{"run_id": run, **r} for r in moves], "grade_decisions")
        record([{"run_id": run, **r} for r in phantom_season()], "phantom_jornadas")
    print("  grade: %d forecasts, %d moves over %d jornadas" % (len(fc), len(moves), resolved))
    return {"resolved": resolved, "forecasts": len(fc), "moves": len(moves)}


def _selftest() -> None:
    bt = [{"from": 7, "jornada": 8, "pred": {"a": 3.0, "b": 1.0}, "hurt": ["b"]}]
    assert forecast_rows(bt, {("a", 8): 5.0}) == [
        {"from_j": 7, "jornada": 8, "player": "a", "predicted": 3.0, "actual": 5.0, "flagged": 0},
        {"from_j": 7, "jornada": 8, "player": "b", "predicted": 1.0, "actual": 0.0, "flagged": 1}], \
        "one row a player forecast; no points scored is 0, as score_forecast has it"
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
