from __future__ import annotations

import datetime as dt
import itertools
import math
import statistics
import json
import sys
from pathlib import Path

from ffcore.schedule import expectations
from ffcore.jornadas import clock_history
from ffcore.points import scored

from assemble import fixture_ratings, scorer
from ffcore.clock import set_now
from ffcore.pricing import grade, steps
from ffcore.tidy import LINEUP_SOURCE, current, history
__all__ = ["backtest", "compare", "persistence", "score_forecast"]

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
            "rmse": math.sqrt(sum(e * e for e in errs) / len(errs)) if errs else None,
            "bias": sum(errs) / len(errs) if errs else None,
            "top": (sum(actual.get((k, j), 0.0) for k in top) / len(top)
                    if top else None)}


def backtest(ahead: int = 0) -> list[dict]:
    """Rebuild the forecast at each past lock and score it against what was
    scored: the jornada it locks and, with ahead, the next ones too. Each row
    also scores the players listed injured, doubtful or suspended then."""
    locks = clock_history().round_locks
    actual = _jornada_points()
    done = {j for _k, j in actual}
    out = []
    try:
        for i in sorted(done & set(locks), key=locks.get):
            set_now(locks[i] - dt.timedelta(minutes=1))
            market = current("market")
            sc = scorer(market, current("lineups", LINEUP_SOURCE))
            per_j = expectations(sc, fixture_ratings(market), set(sc.lookup),
                                 current("matches"))[0]
            flagged = {k for k in sc.lookup if sc.starts.status_of(k)}
            for j in range(i, i + ahead + 1):
                if j not in done or j not in per_j:
                    continue
                pred = {k: pts * p for k, (pts, p) in per_j[j].items()}
                hurt = {k: v for k, v in pred.items() if k in flagged}
                out.append({"jornada": j, "from": i, "pred": pred,
                            **score_forecast(pred, actual, j),
                            "flagged": score_forecast(hurt, actual, j)})
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
    """Two backtests' next-jornada forecasts, jornada by jornada. Keys are
    'from>jornada' (or a bare jornada, from older files)."""
    actual = _jornada_points()
    def nxt(d):
        return {int(k.split(">")[-1]): v for k, v in d.items()
                if ">" not in k or len(set(k.split(">"))) == 1}
    a, b = nxt(a), nxt(b)
    out = []
    for j in sorted(set(a) & set(b)):
        keys = set(a[j]) & set(b[j])
        pa = {k: a[j][k] for k in keys}
        pb = {k: b[j][k] for k in keys}
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
            {r["jornada"]: r["pred"] for r in first}, _jornada_points()))
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
