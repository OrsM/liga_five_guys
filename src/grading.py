from __future__ import annotations

import datetime as dt
import sys
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from typing import Any

from ffcore.forecast import expected_points
from ffcore.schedule import expectations
from ffcore.jornadas import round_locks, rounds_ahead
from ffcore.points import jornada_points

from assemble import built, fixture_ratings, price_change, price_momentum, scorer, universe
from decide import Action, board, verdict
from ffcore.clock import set_now
from ffcore.db import connect, named, record, this_run
from ffcore.tidy import LINEUP_SOURCE, current
__all__ = ["backtest", "decision_rows", "decisions", "due", "forecast_rows",
           "record_grades"]

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

    print("grading self-test OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
