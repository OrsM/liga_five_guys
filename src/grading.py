from __future__ import annotations

import datetime as dt
import itertools
import math
import statistics
import json
import sqlite3
from contextlib import closing
import sys
from collections.abc import Callable
from typing import Any
from pathlib import Path

from ffcore.forecast import expected_points
from ffcore.schedule import expectations
from ffcore.jornadas import clock_history
from ffcore.points import scored
from ffcore.score import JornadaPoints, points_by_jornada

from assemble import built, fixture_ratings, scorer, universe
from decide import Action, board, verdict
from ffcore.clock import set_now
from ffcore.db import connect, record, this_run
from ffcore.pricing import Momentum, _ahead, grade, steps
from ffcore.tidy import LINEUP_SOURCE, current, history
__all__ = ["backtest", "compare", "decision_rows", "decisions", "due", "forecast_rows",
           "persistence", "record_grades", "score_forecast"]

TOP_N = 50
GRADE_AHEAD = 4  # jornadas past the lock each replayed forecast is scored on


def score_forecast(pred: dict[str, float], actual: JornadaPoints,
                   j: int) -> dict[str, Any]:
    errs = [p - actual.get((k, j), 0.0) for k, p in pred.items()]
    top = sorted(pred, key=lambda k: pred[k], reverse=True)[:TOP_N]
    return {"n": len(errs),
            "rmse": math.sqrt(sum(e * e for e in errs) / len(errs)) if errs else None,
            "bias": sum(errs) / len(errs) if errs else None,
            "top": (sum(actual.get((k, j), 0.0) for k in top) / len(top)
                    if top else None)}


def backtest(ahead: int = 0) -> list[dict[str, Any]]:
    """Rebuild the forecast at each past lock and score it against what was
    scored: the jornada it locks and, with ahead, the next ones too. Each row
    also scores the players listed injured, doubtful or suspended then."""
    locks = clock_history().round_locks
    actual = points_by_jornada(scored())
    done = {j for _k, j in actual}
    out = []
    try:
        for i in sorted(done & set(locks), key=lambda j: locks[j]):
            set_now(locks[i] - dt.timedelta(minutes=1))
            market = current("market")
            sc = scorer(market, current("lineups", LINEUP_SOURCE))
            per_j = expectations(sc, fixture_ratings(market), set(sc.lookup),
                                 current("matches")).per_j
            flagged = {k for k in sc.lookup if sc.starts.status_of(k)}
            for j in range(i, i + ahead + 1):
                if j not in done or j not in per_j:
                    continue
                pred = {k: expected_points(cell) for k, cell in per_j[j].items()}
                hurt = {k: v for k, v in pred.items() if k in flagged}
                out.append({"jornada": j, "from": i, "pred": pred, "hurt": sorted(hurt),
                            **score_forecast(pred, actual, j),
                            "flagged": score_forecast(hurt, actual, j)})
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
    locks = clock_history().round_locks
    actual = points_by_jornada(scored())
    done = {j for _k, j in actual}
    prices = steps(history("market"))
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
            h = Momentum(steps(history("market"))).horizon
            day = locks[i].strftime("%Y-%m-%d")

            def rose(k: str) -> float:
                s = prices.get(k, [])
                at = max((n for n, (d, _) in enumerate(s) if d <= day), default=None)
                return 0.0 if at is None or at + h >= len(s) else _ahead(s, at, h)

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
                    "value_real": worth(a, rose)})
    finally:
        set_now(None)
        built.cache_clear()
    return out


def forecast_rows(runs: list[dict[str, Any]], actual: JornadaPoints) -> list[dict[str, Any]]:
    """backtest's forecasts, one row a player: what was forecast at the
    lock (from_j) for a jornada, and what he scored (0 if nothing)."""
    return [{"from_j": r["from"], "jornada": r["jornada"], "player": k, "predicted": p,
             "actual": actual.get((k, r["jornada"]), 0.0), "flagged": int(k in r["hurt"])}
            for r in runs for k, p in r["pred"].items()]


def decision_rows(moves: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """decisions' moves as rows: group (a SQL word) becomes reached."""
    return [{"ord": i, **{k: v for k, v in m.items() if k != "group"}, "reached": m["group"]}
            for i, m in enumerate(moves)]


def due(db: sqlite3.Connection, run: int, resolved: float) -> bool:
    """Whether to grade: never graded, or the code (the run's commit) or the
    jornadas resolved have changed since the last grade."""
    now = db.execute("SELECT commit_sha FROM runs WHERE run_id = ?", (run,)).fetchone()
    last = db.execute(
        "SELECT r.commit_sha, m.value FROM runs r JOIN run_measures m USING (run_id)"
        " WHERE m.stage = 'grade' AND m.measure = 'resolved' AND r.ok = 1"
        " AND r.run_id < ? ORDER BY r.run_id DESC LIMIT 1", (run,)).fetchone()
    return last is None or now is None or (now[0], resolved) != (last[0], last[1])


def record_grades() -> dict[str, int]:
    """The grade stage (run.py grade): replay each past lock with this code,
    and keep every player forecast and every ranked move against what then
    happened; skipped when nothing is new (due)."""
    actual = points_by_jornada(scored())
    resolved = len({j for _k, j in actual})
    run = this_run()
    if run is not None:
        with closing(connect()) as db:
            if not due(db, run, resolved):
                print("  grade: same code, same %d jornadas: nothing new" % resolved)
                return {"resolved": resolved, "skipped": 1}
    fc, moves = forecast_rows(backtest(GRADE_AHEAD), actual), decision_rows(decisions())
    if run is not None:
        record([{"run_id": run, **r} for r in fc], "grade_forecast")
        record([{"run_id": run, **r} for r in moves], "grade_decisions")
    print("  grade: %d forecasts, %d moves over %d jornadas" % (len(fc), len(moves), resolved))
    return {"resolved": resolved, "forecasts": len(fc), "moves": len(moves)}


def persistence(preds: dict[int, dict[str, float]],
                actual: JornadaPoints) -> float:
    by: dict[str, list[tuple[float, float]]] = {}
    for j, pred in preds.items():
        for k, e in pred.items():
            by.setdefault(k, []).append((actual.get((k, j), 0.0) - e, e))
    bias = statistics.fmean(r for v in by.values() for r, _ in v) if by else 0.0
    num = den = 0.0
    for v in by.values():
        for (ra, ea), (rb, eb) in itertools.combinations(v, 2):
            num += (ra - bias) * (rb - bias)
            den += ea * eb
    return math.sqrt(max(0.0, num) / den) if den else 0.0


def compare(a: dict[str, dict[str, float]], b: dict[str, dict[str, float]]
            ) -> list[dict[str, Any]]:
    """Two backtests' next-jornada forecasts, jornada by jornada. Keys are
    'from>jornada' (or a bare jornada, from older files)."""
    actual = points_by_jornada(scored())
    def nxt(d: dict[str, dict[str, float]]) -> dict[int, dict[str, float]]:
        return {int(k.split(">")[-1]): v for k, v in d.items()
                if ">" not in k or len(set(k.split(">"))) == 1}
    na, nb = nxt(a), nxt(b)
    out = []
    for j in sorted(set(na) & set(nb)):
        keys = set(na[j]) & set(nb[j])
        pa = {k: na[j][k] for k in keys}
        pb = {k: nb[j][k] for k in keys}
        sa, sb = score_forecast(pa, actual, j), score_forecast(pb, actual, j)
        out.append({"jornada": j, "n": len(keys), "rmse": (sa["rmse"], sb["rmse"]),
                    "top": (sa["top"], sb["top"])})
    return out


def _selftest() -> None:
    same = {1: {"a": 2.0, "b": 2.0}, 2: {"a": 2.0, "b": 2.0}}
    assert persistence(same, {("a", 1): 3.0, ("a", 2): 3.0, ("b", 1): 1.0,
                              ("b", 2): 1.0}) == 0.5
    assert persistence(same, {("a", 1): 3.0, ("a", 2): 1.0, ("b", 1): 2.0,
                              ("b", 2): 2.0}) == 0.0

    got = score_forecast({"a": 3.0, "b": 1.0}, {("a", 1): 5.0}, 1)
    assert got["n"] == 2 and got["bias"] == -0.5, got
    assert abs(got["rmse"] - math.sqrt(2.5)) < 1e-12, got
    assert got["top"] == 2.5, got
    assert score_forecast({}, {}, 1)["rmse"] is None

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
        def run(commit: str, resolved: float | None) -> int:
            with db:
                cur = db.execute("INSERT INTO runs (started, as_of, trigger, commit_sha, ok)"
                                 " VALUES ('t', 't', 'service', ?, 1)", (commit,))
                if resolved is not None:
                    db.execute("INSERT INTO run_measures VALUES (?, 'grade', 'resolved', ?)",
                               (cur.lastrowid, resolved))
            assert cur.lastrowid is not None
            return cur.lastrowid
        assert due(db, run("a", None), 7), "never graded: due"
        run("a", 7)
        assert not due(db, run("a", None), 7), "same code, same jornadas: nothing new"
        assert due(db, run("a", None), 8), "a jornada resolved"
        assert due(db, run("b", None), 7), "new code"
        assert isinstance(db, sqlite3.Connection)

    print("grading self-test OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    elif "--backtest" in sys.argv:
        args = sys.argv[sys.argv.index("--backtest") + 1:]
        h = int(args[args.index("--ahead") + 1]) if "--ahead" in args else 0
        out = [x for i, x in enumerate(args) if not x.startswith("--")
               and not (i and args[i - 1] == "--ahead")]
        runs = backtest(ahead=h)
        first = [r for r in runs if r["jornada"] == r["from"]]
        for r in first:
            print("j%-2d n=%3d rmse %.3f bias %+.3f top%d %.2f"
                  % (r["jornada"], r["n"], r["rmse"], r["bias"], TOP_N, r["top"]))
        print("persistent share of a player's expectation: %.2f" % persistence(
            {r["jornada"]: r["pred"] for r in first}, points_by_jornada(scored())))
        for d in range(h + 1):
            rows = [r for r in runs if r["jornada"] - r["from"] == d]
            f = [r["flagged"] for r in rows if r["flagged"]["n"]]
            n = max(1, sum(x["n"] for x in f))
            print("%d ahead: %d forecasts, rmse %.3f; flagged players n=%d rmse %.3f bias %+.3f"
                  % (d, len(rows), statistics.fmean(r["rmse"] for r in rows),
                     sum(x["n"] for x in f),
                     math.sqrt(sum(x["rmse"] ** 2 * x["n"] for x in f) / n),
                     sum(x["bias"] * x["n"] for x in f) / n))
        if out:
            Path(out[0]).write_text(json.dumps(
                {"%d>%d" % (r["from"], r["jornada"]): r["pred"] for r in runs}),
                encoding="utf-8")
    elif "--decisions" in sys.argv:
        runs = decisions()
        for group in ("plan", "cleared", "rejected"):
            for kind in ("get", "sell"):
                rows = [r for r in runs if r["group"] == group and r["kind"] == kind]
                if not rows:
                    continue
                per = [(r["pred"] / r["jornadas"], r["real"] / r["jornadas"])
                       for r in rows if r["jornadas"]]
                print("%-8s %-4s n=%3d  per jornada: forecast %+.2f, happened %+.2f;"
                      " happened > 0 in %.0f%% (forecast chance %.0f%%);"
                      " value forecast %+.2fM, happened %+.2fM" % (
                          group, kind, len(rows),
                          statistics.fmean(f for f, _ in per), statistics.fmean(h for _, h in per),
                          100 * statistics.fmean(h > 0 for _, h in per),
                          100 * statistics.fmean(r["chance"] for r in rows),
                          statistics.fmean(r["value_pred"] for r in rows),
                          statistics.fmean(r["value_real"] for r in rows)))
        for r in runs:
            if r["group"] == "plan":
                print("  j%-2d %-45s forecast %+6.1f happened %+6.1f over %d;"
                      " value %+5.1fM happened %+5.1fM" % (
                    r["lock"], r["move"][:45], r["pred"], r["real"], r["jornadas"],
                    r["value_pred"], r["value_real"]))
    elif "--prices" in sys.argv:
        for h, g in grade(steps(history("market"))).items():
            print("%d update(s) ahead: n=%d  error %.2f%%  vs %.2f%% for "
                  "'no change'" % (h, g["n"], g["mae"], g["zero"]))
    elif "--compare" in sys.argv:
        i = sys.argv.index("--compare")
        a, b = (json.loads(Path(p).read_text(encoding="utf-8"))
                for p in sys.argv[i + 1:i + 3])
        rows = compare(a, b)
        for r in rows:
            print("j%-2d n=%3d rmse %.3f -> %.3f (%+.3f)  top%d %.2f -> %.2f (%+.2f)"
                  % (r["jornada"], r["n"], *r["rmse"], r["rmse"][1] - r["rmse"][0],
                     TOP_N, *r["top"], r["top"][1] - r["top"][0]))
        late = [r for r in rows if r["jornada"] > 1]
        print("jornadas 2+: rmse better in %d/%d, mean %+.3f; top better in %d/%d, mean %+.2f"
              % (sum(r["rmse"][1] < r["rmse"][0] for r in late), len(late),
                 sum(r["rmse"][1] - r["rmse"][0] for r in late) / max(1, len(late)),
                 sum(r["top"][1] > r["top"][0] for r in late), len(late),
                 sum(r["top"][1] - r["top"][0] for r in late) / max(1, len(late))))
