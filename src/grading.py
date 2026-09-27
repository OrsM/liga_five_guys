from __future__ import annotations

import datetime as dt
import itertools
import math
import statistics
import json
import sys
from pathlib import Path

from ffcore.parse import text
from ffcore.text import norm
from ffcore.schedule import expectations
from ffcore.tidy import (DECISIONS, Scored, append_csv, clock_history, current,
                         read_csv, run_now, scored, snapshot_stamp)

__all__ = ["log_predictions", "load_actuals", "load_predictions", "pair",
           "current_mae", "graded_history"]

WINDOW_DAYS = 21
PREDICTIONS = DECISIONS / "squad_log.csv"
PREDICTION_COLS = ["observed_at", "ff_id", "player", "score", "pts", "pj"]


def log_predictions(sc) -> None:
    observed = sc.market[0]["observed_at"] if sc.market else ""
    if not observed or observed in {r.get("observed_at")
                                    for r in read_csv(PREDICTIONS)}:
        return
    per_j, first_of, rates, _rem, _played = expectations(
        sc, set(sc.lookup), current("matches"))
    append_csv(PREDICTIONS, [
        {"observed_at": observed, "ff_id": k,
         "player": sc.lookup[k].get("name", ""),
         "score": "%.3f" % (per_j[j][k][0] * per_j[j][k][1]),
         "pts": "%.3f" % per_j[j][k][0], "pj": "%.1f" % rates[k].pj}
        for k, j in first_of.items()], PREDICTION_COLS)


def load_actuals(window_days: int | None = WINDOW_DAYS) -> list[Scored]:
    cutoff = ("" if window_days is None else (
        run_now() - dt.timedelta(days=window_days)).strftime("%Y-%m-%dT%H%MZ"))
    return [s for s in scored() if s.at >= cutoff]


def load_predictions() -> dict[str, list[tuple[dt.datetime, dict]]]:
    per: dict[str, list] = {}
    for r in read_csv(PREDICTIONS):
        key = text(r, "ff_id") or norm(r.get("player", ""))
        when = snapshot_stamp(r.get("observed_at", ""))
        try:
            fac = {"score": float(r["score"])}
        except (KeyError, ValueError, TypeError):
            continue
        if not key or when is None:
            continue
        try:
            fac["pts"] = float(r["pts"]) if r.get("pts") else (
                float(r["ppm"]) * float(r.get("fix") or 1.0))
        except (KeyError, ValueError, TypeError):
            fac["pts"] = None
        try:
            fac["pj"] = float(r["pj"])
        except (KeyError, ValueError, TypeError):
            fac["pj"] = None
        per.setdefault(key, []).append((when, fac))
    for v in per.values():
        v.sort(key=lambda t: t[0])
    return per


def _claim(keys, preds, cutoff: dt.datetime) -> dict | None:
    for k in keys:
        before = [fac for when, fac in preds.get(k, ()) if when < cutoff]
        if before:
            return before[-1]
    return None


def _graded_row(a: Scored, per_match: float, **extra) -> dict:
    predicted = per_match * a.games
    return {"key": a.key, "predicted": predicted, "actual": a.pts,
            "per_match": per_match, "matches": a.games,
            "err": predicted - a.pts, "jornada": a.jornada, **extra}


def pair(actuals: list[dict], preds, locks: dict[int, dt.datetime]
         ) -> list[dict]:
    out = []
    for a in actuals:
        lock = locks.get(a.jornada)
        fac = (_claim([a.key], preds, lock)
               if a.games >= 1 and lock is not None else None)
        if fac is not None:
            out.append(_graded_row(a, fac["score"]))
    return out


def graded_history() -> tuple[dict, list[dict], dict]:
    return (clock_history().round_locks, load_actuals(), load_predictions())


def current_mae(actuals: list[dict], preds,
                locks: dict[int, dt.datetime]) -> float | None:
    pairs = pair(actuals, preds, locks)
    return (sum(abs(p["err"]) / p["matches"] for p in pairs) / len(pairs)
            if pairs else None)


TOP_N = 50


def _jornada_points() -> dict[tuple, float]:
    out: dict[tuple, float] = {}
    for s in scored():
        out[s.key, s.jornada] = out.get((s.key, s.jornada), 0.0) + s.pts
    return out


def score_forecast(pred: dict[str, float], actual: dict[tuple, float],
                   j: int) -> dict:
    errs = [p - actual.get((k, j), 0.0) for k, p in pred.items()]
    top = sorted(pred, key=pred.get, reverse=True)[:TOP_N]
    return {"n": len(errs),
            "mae": sum(abs(e) for e in errs) / len(errs) if errs else None,
            "bias": sum(errs) / len(errs) if errs else None,
            "top": (sum(actual.get((k, j), 0.0) for k in top) / len(top)
                    if top else None)}


def backtest() -> list[dict]:
    from ffcore.score import build
    from ffcore.tidy import LINEUP_SOURCE, set_now

    locks = clock_history().round_locks
    actual = _jornada_points()
    logged = load_predictions()
    out = []
    try:
        for j in sorted({j for _k, j in actual} & set(locks), key=locks.get):
            set_now(locks[j] - dt.timedelta(minutes=1))
            market = current("market")
            sc = build(market, current("lineups", LINEUP_SOURCE), run_now())
            per_j = expectations(sc, set(sc.lookup), current("matches"))[0]
            pred = {k: pts * p for k, (pts, p) in per_j.get(j, {}).items()}
            then = {k: fac["score"] for k in pred
                    if (fac := _claim([k], logged, locks[j])) is not None}
            out.append({"jornada": j, "pred": pred,
                        **score_forecast(pred, actual, j),
                        "logged": score_forecast(then, actual, j)})
    finally:
        set_now(None)
    return out


def persistence(preds: dict[int, dict[str, float]],
                actual: dict[tuple, float]) -> float:
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


def compare(a: dict[str, dict], b: dict[str, dict]) -> list[dict]:
    actual = _jornada_points()
    out = []
    for j in sorted(set(a) & set(b), key=int):
        keys = set(a[j]) & set(b[j])
        pa = {k: a[j][k] for k in keys}
        pb = {k: b[j][k] for k in keys}
        sa, sb = score_forecast(pa, actual, int(j)), score_forecast(pb, actual, int(j))
        out.append({"jornada": int(j), "n": len(keys), "mae": (sa["mae"], sb["mae"]),
                    "top": (sa["top"], sb["top"])})
    return out


def _selftest() -> None:
    t0 = dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc)
    day = dt.timedelta(days=1)
    preds = {"7": [(t0, {"score": 4.0, "pts": 6.0, "pj": 10.0}),
                   (t0 + 5 * day, {"score": 6.0, "pts": 6.0,
                                   "pj": 12.0})]}
    actuals = [
        Scored("7", 2, 10.0, 2.0, "t"), Scored("7", 3, 3.0, 1.0, "t"),
        Scored("x", 3, 3.0, 1.0, "t"), Scored("7", 3, 0.0, 0.0, "t"),
        Scored("7", 1, 5.0, 1.0, "t")]
    locks = {1: t0 + 2 * day, 2: t0 + 4 * day, 3: t0 + 8 * day}
    got = pair(actuals, preds, locks)
    assert [(g["predicted"], g["err"]) for g in got] == [
        (8.0, -2.0), (6.0, 3.0), (4.0, -1.0)], got
    assert pair(actuals, preds, {}) == []
    same = {1: {"a": 2.0, "b": 2.0}, 2: {"a": 2.0, "b": 2.0}}
    assert persistence(same, {("a", 1): 3.0, ("a", 2): 3.0, ("b", 1): 1.0,
                              ("b", 2): 1.0}) == 0.5
    assert persistence(same, {("a", 1): 3.0, ("a", 2): 1.0, ("b", 1): 2.0,
                              ("b", 2): 2.0}) == 0.0

    got = score_forecast({"a": 3.0, "b": 1.0}, {("a", 1): 5.0}, 1)
    assert (got["n"], got["mae"], got["bias"]) == (2, 1.5, -0.5), got
    assert got["top"] == 2.5, got
    assert score_forecast({}, {}, 1)["mae"] is None

    print("grading self-test OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    elif "--backtest" in sys.argv:
        runs = backtest()
        for r in runs:
            g = r["logged"]
            print("j%-2d n=%3d mae %.3f bias %+.3f top%d %.2f | logged n=%3d mae %s"
                  % (r["jornada"], r["n"], r["mae"], r["bias"], TOP_N, r["top"],
                     g["n"], "%.3f" % g["mae"] if g["mae"] is not None else "-"))
        print("persistent share of a player's expectation: %.2f" % persistence(
            {r["jornada"]: r["pred"] for r in runs}, _jornada_points()))
        rest = sys.argv[sys.argv.index("--backtest") + 1:]
        if rest:
            Path(rest[0]).write_text(json.dumps(
                {str(r["jornada"]): r["pred"] for r in runs}), encoding="utf-8")
    elif "--compare" in sys.argv:
        i = sys.argv.index("--compare")
        a, b = (json.loads(Path(p).read_text(encoding="utf-8"))
                for p in sys.argv[i + 1:i + 3])
        rows = compare(a, b)
        for r in rows:
            print("j%-2d n=%3d mae %.3f -> %.3f (%+.3f)  top%d %.2f -> %.2f (%+.2f)"
                  % (r["jornada"], r["n"], *r["mae"], r["mae"][1] - r["mae"][0],
                     TOP_N, *r["top"], r["top"][1] - r["top"][0]))
        late = [r for r in rows if r["jornada"] > 1]
        print("jornadas 2+: mae better in %d/%d, mean %+.3f; top better in %d/%d, mean %+.2f"
              % (sum(r["mae"][1] < r["mae"][0] for r in late), len(late),
                 sum(r["mae"][1] - r["mae"][0] for r in late) / max(1, len(late)),
                 sum(r["top"][1] > r["top"][0] for r in late), len(late),
                 sum(r["top"][1] - r["top"][0] for r in late) / max(1, len(late))))
