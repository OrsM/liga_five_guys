
from __future__ import annotations

import random
import sys


from ffcore.tidy import (read_csv, TIDY, SEASON, load_crosswalk,
                         load_matches, load_starters, load_perjornada)
from ffcore.score import _per_jornada_current
from stats import percentile


def _jornada_dates(matches: list[dict], starters: list[dict]) -> dict[int, str]:
    jor_of_match: dict[str, int] = {}
    for m in matches:
        mid = (m.get("match_id") or "").strip()
        if mid and mid not in jor_of_match:
            try:
                jor_of_match[mid] = int(m["jornada"])
            except (TypeError, ValueError):
                continue
    earliest: dict[str, str] = {}
    for r in starters:
        mid, ts = r.get("match_id"), r.get("observed_at") or ""
        if mid and (mid not in earliest or ts < earliest[mid]):
            earliest[mid] = ts
    out: dict[int, str] = {}
    for mid, jor in jor_of_match.items():
        ts = earliest.get(mid)
        if ts:
            out.setdefault(jor, ts)
    return out


def _status_history(lineups: list[dict]) -> dict[str, list[tuple[str, str]]]:
    out: dict[str, list[tuple[str, str]]] = {}
    for r in lineups:
        slug = (r.get("player_slug") or "").strip()
        if slug:
            out.setdefault(slug, []).append(
                (r.get("observed_at") or "", r.get("status") or "ok"))
    for v in out.values():
        v.sort()
    return out


def _status_around(hist: list[tuple[str, str]], approx_date: str,
                   window: int = 3) -> set[str]:
    idx = 0
    for i, (ts, _st) in enumerate(hist):
        if ts <= approx_date:
            idx = i
    lo, hi = max(0, idx - window), min(len(hist), idx + window + 1)
    return {st for _ts, st in hist[lo:hi]}


def gap_cases(by_key, jornada_dates, status_hist, slug_of_key
             ) -> tuple[list[tuple[str, float, float]], int, int]:
    cases = []
    excluded_confounded = excluded_no_status = 0
    for key, pj in by_key.items():
        jors = sorted(pj)
        for i in range(1, len(jors) - 1):
            j = jors[i]
            _pts, mins = pj[j]
            _prev_pts, prev_mins = pj[jors[i - 1]]
            if not (mins == 0 and prev_mins > 0):
                continue
            for j2 in jors[i + 1:]:
                pts2, mins2 = pj[j2]
                if mins2 <= 0:
                    continue
                pre = [pj[jj] for jj in jors[:i] if pj[jj][1] > 0]
                if len(pre) < 2:
                    break
                pre_ppm = sum(p for p, _m in pre) / len(pre)
                approx_date = jornada_dates.get(j, "")
                slug = slug_of_key.get(key)
                hist = status_hist.get(slug) if slug else None
                if not approx_date or hist is None:
                    excluded_no_status = excluded_no_status + 1
                    break
                statuses = _status_around(hist, approx_date)
                if statuses - {"ok"}:
                    excluded_confounded += 1
                    break
                cases.append((key, pre_ppm, pts2))
                break
    return cases, excluded_confounded, excluded_no_status


def leave_one_out(cases: list[tuple[str, float, float]]) -> dict:
    diffs = [pts2 - pre for _key, pre, pts2 in cases]
    n = len(cases)
    if n < 2:
        return {}
    ab_base = ab_adj = sq_base = sq_adj = 0.0
    sq_diffs = []
    for i, (_key, pre, actual) in enumerate(cases):
        others = diffs[:i] + diffs[i + 1:]
        loo = sum(others) / len(others)
        e_base, e_adj = pre - actual, pre + loo - actual
        ab_base += abs(e_base)
        ab_adj += abs(e_adj)
        sq_base += e_base ** 2
        sq_adj += e_adj ** 2
        sq_diffs.append(e_adj ** 2 - e_base ** 2)
    rng = random.Random(0)
    boot = [sum(rng.choices(sq_diffs, k=n)) / n for _ in range(2000)]
    return {"n": n, "mae_base": ab_base / n, "mae_adj": ab_adj / n,
           "mse_base": sq_base / n, "mse_adj": sq_adj / n,
           "mse_gap_lo": percentile(boot, 5), "mse_gap_hi": percentile(boot, 95)}


def run() -> None:
    xw = load_crosswalk()
    starters = load_starters()
    matches = load_matches()
    lineups = read_csv(TIDY / "lineups.csv")
    files = sorted((SEASON / "live").glob("perjornada_*.csv"))
    if not files or xw is None:
        print("no data yet")
        return
    perj = load_perjornada()

    by_key = _per_jornada_current(starters, perj, matches, xw)
    jornada_dates = _jornada_dates(matches, starters)
    status_hist = _status_history(lineups)
    slug_of_key = {k: p.ff_slug for k, p in xw.players.items() if p.ff_slug}

    cases, excl_conf, excl_nodata = gap_cases(
        by_key, jornada_dates, status_hist, slug_of_key)
    print(f"kept (status=ok around the gap): {len(cases)}")
    print(f"excluded (real status flag near the gap): {excl_conf}")
    print(f"excluded (no status history to check): {excl_nodata}")
    if not cases:
        return
    diffs = [pts2 - pre for _k, pre, pts2 in cases]
    print(f"paired mean diff (return - pre_ppm): {sum(diffs)/len(diffs):+.2f}")

    loo = leave_one_out(cases)
    if loo:
        print(f"leave-one-out MAE: no-adjustment {loo['mae_base']:.3f}, "
             f"with adjustment {loo['mae_adj']:.3f}")
        print(f"leave-one-out MSE: no-adjustment {loo['mse_base']:.3f}, "
             f"with adjustment {loo['mse_adj']:.3f}, "
             f"gap 90% CI [{loo['mse_gap_lo']:+.3f}, {loo['mse_gap_hi']:+.3f}]")
        clears = loo["mse_gap_hi"] < 0
        print("CLEARS the out-of-sample bar" if clears
             else "does NOT clear the out-of-sample bar yet — leave parked")


def _selftest() -> None:
    by_key = {
        "A": {1: (6.0, 90.0), 2: (5.0, 90.0), 3: (0.0, 0.0), 4: (2.0, 90.0)},
        "B": {1: (4.0, 90.0), 2: (4.0, 90.0), 3: (4.0, 90.0)},
    }
    jornada_dates = {1: "2026-01-01", 2: "2026-01-08", 3: "2026-01-15",
                     4: "2026-01-22"}
    status_hist = {"a-slug": [("2026-01-01T00Z", "ok"),
                              ("2026-01-15T00Z", "ok")],
                  "b-slug": [("2026-01-01T00Z", "ok")]}
    slug_of_key = {"A": "a-slug", "B": "b-slug"}

    cases, excl_conf, excl_nodata = gap_cases(
        by_key, jornada_dates, status_hist, slug_of_key)
    assert len(cases) == 1, cases
    assert cases[0][0] == "A"
    assert abs(cases[0][1] - 5.5) < 1e-9, cases
    assert cases[0][2] == 2.0, cases
    assert excl_conf == 0 and excl_nodata == 0

    flagged = dict(status_hist)
    flagged["a-slug"] = [("2026-01-01T00Z", "ok"),
                         ("2026-01-15T00Z", "injured")]
    cases2, excl_conf2, _ = gap_cases(
        by_key, jornada_dates, flagged, slug_of_key)
    assert cases2 == [], cases2
    assert excl_conf2 == 1

    assert leave_one_out([("A", 5.0, 2.0)]) == {}
    two = [("A", 5.0, 2.0), ("B", 4.0, 4.5)]
    out = leave_one_out(two)
    assert out["n"] == 2
    assert out["mae_base"] > 0

    print("gap_signal.py selftest OK (7 cases)")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        run()
