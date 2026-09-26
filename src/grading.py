from __future__ import annotations

import datetime as dt
import math
import statistics
import sys

from ffcore.parse import text
from ffcore.text import norm
from ffcore.tidy import (DECISIONS, SEASON, append_csv, clock_history,
                         lock_order, read_csv, run_now, snapshot_stamp)

__all__ = ["log_predictions", "load_actuals", "load_predictions", "pair",
           "lagged_pair",
           "current_mae", "drift_frac_from_history", "fit_rate_rel_floor",
           "graded_history"]

WINDOW_DAYS = 21
PREDICTIONS = DECISIONS / "squad_log.csv"
PREDICTION_COLS = ["observed_at", "ff_id", "player", "score", "ppm", "fix",
                   "pj"]


def log_predictions(sc) -> None:
    observed = sc.market[0]["observed_at"] if sc.market else ""
    if not observed or observed in {r.get("observed_at")
                                    for r in read_csv(PREDICTIONS)}:
        return
    append_csv(PREDICTIONS, [
        {"observed_at": observed, "ff_id": s.key, "player": s.name,
         "score": "%.3f" % s.score, "ppm": "%.3f" % s.ppm,
         "fix": "%.3f" % s.fix, "pj": "%.1f" % s.pj}
        for s in (sc.score(r) for r in sc.lookup.values())], PREDICTION_COLS)


def load_actuals(window_days: int | None = WINDOW_DAYS) -> list[dict]:
    files = sorted((SEASON / "live").glob("perjornada_*.csv"))
    if not files:
        return []
    cutoff = (run_now() - dt.timedelta(days=window_days)
              if window_days is not None
              else dt.datetime.min.replace(tzinfo=dt.timezone.utc))
    rows = []
    for r in read_csv(files[-1]):
        try:
            from_dt = snapshot_stamp(r["from_stamp"])
            to_dt = snapshot_stamp(r["to_stamp"])
            games = float(r["games_delta"] or 0)
            points = float(r["points_delta"] or 0)
        except (KeyError, ValueError, TypeError):
            continue
        if to_dt is None or from_dt is None or to_dt < cutoff:
            continue
        full, short = r.get("player_name_full", ""), r.get("player_name", "")
        jor = r.get("jornada", "")
        rows.append({"name": full or short,
                     "keys": [k for k in dict.fromkeys(
                         (r.get("ff_id", ""), norm(full), norm(short))) if k],
                     "from_dt": from_dt, "points_delta": points,
                     "games_delta": games,
                     "jornada": int(jor) if jor else None})
    return rows


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
        for col in ("fix", "ppm", "pj"):
            try:
                fac[col] = float(r[col])
            except (KeyError, ValueError, TypeError):
                fac[col] = None
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


def _graded_row(a: dict, per_match: float, **extra) -> dict:
    predicted = per_match * a["games_delta"]
    return {"name": a["name"], "predicted": predicted,
            "actual": a["points_delta"], "per_match": per_match,
            "matches": a["games_delta"], "err": predicted - a["points_delta"],
            "jornada": a.get("jornada"), **extra}


def pair(actuals: list[dict], preds) -> list[dict]:
    out = []
    for a in actuals:
        fac = _claim(a["keys"], preds, a["from_dt"]) \
            if a["games_delta"] >= 1 else None
        if fac is not None:
            out.append(_graded_row(a, fac["score"], fix=fac.get("fix")))
    return out


def _conditional(fac: dict) -> float:
    return (fac.get("ppm") or 0.0) * (fac.get("fix") or 1.0)


def lagged_pair(actuals: list[dict], preds, locks: dict[int, dt.datetime],
                lag: int) -> list[dict]:
    order = lock_order(locks)
    pos = {j: i for i, j in enumerate(order)}
    out = []
    for a in actuals:
        i = pos.get(a.get("jornada"))
        if a["games_delta"] < 1 or i is None or i - lag < 0:
            continue
        fac = _claim(a["keys"], preds, locks[order[i - lag]])
        if fac is not None:
            out.append(_graded_row(a, _conditional(fac), pj=fac.get("pj")))
    return out


def graded_history() -> tuple[dict, list[dict], dict]:
    return (clock_history().round_locks, load_actuals(), load_predictions())


def current_mae(actuals: list[dict], preds) -> float | None:
    pairs = pair(actuals, preds)
    return (sum(abs(p["err"]) / p["matches"] for p in pairs) / len(pairs)
            if pairs else None)


def _z_variance(rels, floor: float) -> float:
    return statistics.pvariance([math.log(a / p) / max(floor, rel)
                                 for rel, p, a in rels])


def fit_rate_rel_floor(pool, history: tuple, min_pairs: int = 30) -> float:
    from ffcore.forecast import RATE_REL_FLOOR, SHRINK_MATCHES

    real = [p for p in pool if p is not None]
    mean = statistics.mean(real) if len(real) >= 50 else 0.0
    if abs(mean) < 1e-9:
        return RATE_REL_FLOOR
    cv = statistics.pstdev(real) / mean
    locks, actuals, preds = history
    rels = [(cv / max(1.0, g["pj"] + SHRINK_MATCHES) ** 0.5, g["predicted"],
             g["actual"])
            for g in lagged_pair(actuals, preds, locks, 0)
            if g.get("pj") is not None and g["predicted"] > 0
            and g["actual"] > 0]
    if len(rels) < min_pairs:
        return RATE_REL_FLOOR
    return min((0.10 + 0.05 * i for i in range(19)),
               key=lambda f: abs(_z_variance(rels, f) - 2.0))


def drift_frac_from_history(history: tuple, lag1: int = 1,
                            lag3: int = 3) -> float:
    from ffcore.forecast import fit_drift_frac

    locks, actuals, preds = history
    h1 = lagged_pair(actuals, preds, locks, lag1)
    h3 = lagged_pair(actuals, preds, locks, lag3)
    ratios = [p["actual"] / p["predicted"] for p in h1 if p["predicted"] > 0]
    pooled = statistics.pstdev(ratios) if len(ratios) >= 5 else 0.0
    if pooled <= 0:
        return 1.0
    return fit_drift_frac([(p["predicted"], p["actual"], pooled) for p in h1],
                          [(p["predicted"], p["actual"], pooled) for p in h3])


def _selftest() -> None:
    t0 = dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc)
    day = dt.timedelta(days=1)
    preds = {"7": [(t0, {"score": 4.0, "ppm": 5.0, "fix": 1.2, "pj": 10.0}),
                   (t0 + 5 * day, {"score": 6.0, "ppm": 6.0, "fix": 1.0,
                                   "pj": 12.0})]}
    actuals = [
        {"name": "A", "keys": ["7"], "from_dt": t0 + 3 * day,
         "points_delta": 10.0, "games_delta": 2.0, "jornada": 2},
        {"name": "A", "keys": ["7"], "from_dt": t0 + 9 * day,
         "points_delta": 3.0, "games_delta": 1.0, "jornada": 3},
        {"name": "B", "keys": ["x"], "from_dt": t0 + 9 * day,
         "points_delta": 3.0, "games_delta": 1.0, "jornada": 3},
        {"name": "A", "keys": ["7"], "from_dt": t0 + 9 * day,
         "points_delta": 0.0, "games_delta": 0.0, "jornada": 3},
        {"name": "A", "keys": ["7"], "from_dt": t0,
         "points_delta": 5.0, "games_delta": 1.0, "jornada": 1}]
    got = pair(actuals, preds)
    assert [(g["predicted"], g["err"], g["fix"]) for g in got] == [
        (8.0, -2.0, 1.2), (6.0, 3.0, 1.0)], got

    locks = {1: t0 + 2 * day, 2: t0 + 4 * day, 3: t0 + 8 * day}
    lag0 = lagged_pair(actuals, preds, locks, 0)
    assert [(g["jornada"], g["per_match"], g["pj"]) for g in lag0] == [
        (2, 6.0, 10.0), (3, 6.0, 12.0), (1, 6.0, 10.0)], lag0
    lag1 = lagged_pair(actuals, preds, locks, 1)
    assert [(g["jornada"], g["pj"]) for g in lag1] == [(2, 10.0), (3, 10.0)], \
        lag1
    assert lagged_pair(actuals, preds, locks, 3) == []

    from ffcore.forecast import RATE_REL_FLOOR
    assert drift_frac_from_history((locks, actuals, preds)) == 1.0
    assert fit_rate_rel_floor([3] * 10, (locks, actuals, preds)) == RATE_REL_FLOOR

    print("grading self-test OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
