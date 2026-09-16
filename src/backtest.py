
from __future__ import annotations

import csv
import datetime as dt
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile


import stats

__all__ = ["commit_as_of", "csv_as_of", "commits_touching",
          "replay_recommendations", "replay_percentile_rank",
          "replay_ladder_percentile", "compare_arms",
          "screen_audit_episode", "replay_screen_misses"]

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def commit_as_of(when: dt.datetime, path: str) -> str | None:
    out = subprocess.run(
        ["git", "log", "--format=%H", "--before", when.isoformat(), "-1",
         "--", path],
        cwd=_ROOT, capture_output=True, text=True, check=False)
    sha = out.stdout.strip()
    return sha or None


def _show(sha: str, path: str) -> str | None:
    out = subprocess.run(["git", "show", f"{sha}:{path}"],
                         cwd=_ROOT, capture_output=True, text=True,
                         check=False)
    return out.stdout if out.returncode == 0 else None


def csv_as_of(when: dt.datetime, path: str) -> list[dict]:
    sha = commit_as_of(when, path)
    if sha is None:
        return []
    text = _show(sha, path)
    return list(csv.DictReader(io.StringIO(text))) if text is not None else []


def commits_touching(path: str) -> list[tuple[str, dt.datetime]]:
    out = subprocess.run(
        ["git", "log", "--format=%H|%cI", "--follow", "--reverse", "--", path],
        cwd=_ROOT, capture_output=True, text=True, check=False)
    commits = []
    for line in out.stdout.splitlines():
        sha, _, when = line.partition("|")
        try:
            commits.append((sha, dt.datetime.fromisoformat(when)))
        except ValueError:
            continue
    return commits



NEAR_MISS_FRAC = 0.85

_SCREEN_AUDIT_SCRIPT = """
import json, sys
import decide

u = decide.load()
bar_exp, xi = u.current_xi
if not bar_exp:
    print(json.dumps({{"error": "no xi"}}))
    sys.exit(0)
bar = u.xi_bar
mine = set(u.state.squads.get(u.me, {{}}))
near = []
for c, price in u.price_view.items():
    if c in mine or price > u.cash:
        continue
    exp = bar_exp.get(c, 0.0)
    if exp <= bar and exp >= bar * {frac} and u.route_kind(c) != "listed":
        near.append((c, price, exp))

if not near:
    print(json.dumps({{"near_miss_count": 0}}))
    sys.exit(0)

acts = [decide.Action("buy", buy=c, cost=price) for c, price, _exp in near]
rows, base, measured, bands = u.rank(acts)
best = None
for r in rows:
    if best is None or r["pts_lo"] > best["pts_lo"]:
        best = {{"buy": r["action"].buy, "pts_lo": r["pts_lo"],
                "d_pos": r["d_pos"]}}
print(json.dumps({{"near_miss_count": len(near), "best": best}}))
"""


def screen_audit_episode(sha: str, when: str, near_miss_frac: float = NEAR_MISS_FRAC,
                         timeout: float = 90.0) -> dict:
    tmp = tempfile.mkdtemp(prefix="lfg_screen_audit_")
    try:
        wt = subprocess.run(
            ["git", "worktree", "add", "--detach", "--force", tmp, sha],
            cwd=_ROOT, capture_output=True, text=True, check=False)
        if wt.returncode != 0:
            return {"sha": sha, "error": "worktree add failed",
                   "detail": wt.stderr.strip()[-500:]}
        script = _SCREEN_AUDIT_SCRIPT.format(frac=near_miss_frac)
        try:
            proc = subprocess.run([sys.executable, "-c", script], cwd=tmp,
                                  capture_output=True, text=True,
                                  timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            return {"sha": sha, "error": "audit script timed out"}
        if proc.returncode != 0:
            return {"sha": sha, "error": "audit script failed",
                   "detail": proc.stderr.strip()[-500:]}
        try:
            result = json.loads(proc.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError):
            return {"sha": sha, "error": "bad script output",
                   "detail": proc.stdout.strip()[-500:]}
        if result.get("error"):
            return {"sha": sha, "error": result["error"]}
        n = result.get("near_miss_count", 0)
        if not n or not result.get("best"):
            return {"sha": sha, "when": when, "near_miss_count": n,
                   "near_miss_best": None, "actual_best_pts_lo": None,
                   "beat_actual": None}
        actual_pts_lo = None
        text = _show(sha, "reports/decisions.json")
        if text:
            try:
                moves = json.loads(text).get("moves") or []
                if moves and moves[0].get("pts_lo") is not None:
                    actual_pts_lo = moves[0]["pts_lo"]
            except (ValueError, TypeError):
                pass
        beat = (actual_pts_lo is not None
               and result["best"]["pts_lo"] > actual_pts_lo)
        return {"sha": sha, "when": when, "near_miss_count": n,
               "near_miss_best": result["best"],
               "actual_best_pts_lo": actual_pts_lo, "beat_actual": beat}
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", tmp],
                       cwd=_ROOT, capture_output=True, text=True, check=False)
        shutil.rmtree(tmp, ignore_errors=True)


def replay_screen_misses(sample_every: int = 10,
                         near_miss_frac: float = NEAR_MISS_FRAC) -> dict:
    commits = commits_touching("reports/decisions.json")
    sampled = commits[::max(1, sample_every)]
    results = [screen_audit_episode(sha, when.isoformat(), near_miss_frac)
              for sha, when in sampled]
    valid = [r for r in results if "error" not in r]
    beats = [r for r in valid if r.get("beat_actual")]
    return {"sampled": len(sampled), "valid": len(valid),
           "errors": len(results) - len(valid), "beats": len(beats),
           "results": results}


def naive_value_baseline(golden: list[dict]) -> dict | None:
    import methodology as M
    from ffcore.text import norm
    from ffcore.tidy import load_matches

    checked = [r for r in golden if r.get("predicted_rate") is not None]
    if not checked:
        return None
    matches = load_matches()
    locks = M.clock_history().round_locks

    resolved = []
    for r in checked:
        lock = locks.get(r["jornada"])
        if lock is None:
            continue
        hist_market = csv_as_of(lock, "data/tidy/market.csv")
        row = next((m for m in hist_market
                   if norm(m.get("name", "")) == norm(r["player"])), None)
        if row is None:
            continue
        try:
            val = float(row["value"])
        except (KeyError, ValueError):
            continue
        resolved.append((val, r["actual_points"], r["predicted_rate"]))

    if not resolved:
        return None
    n = len(resolved)
    mean_val = sum(v for v, _, _ in resolved) / n
    mean_act = sum(a for _, a, _ in resolved) / n
    k = mean_act / mean_val if mean_val else 0.0
    naive_mae = sum(abs(k * v - a) for v, a, _ in resolved) / n
    ours_mae = sum(abs(p - a) for _, a, p in resolved) / n
    return {"n": n, "k": k, "naive_mae": naive_mae, "ours_mae": ours_mae}


def recency_only_baseline(golden: list[dict], window: int = 3) -> dict | None:
    import methodology as M
    from ffcore.text import norm
    from ffcore.tidy import load_matches

    checked = [r for r in golden if r.get("predicted_rate") is not None]
    if not checked:
        return None
    matches = load_matches()
    clock = M.clock_history()
    locks, order = clock.round_locks, clock.order
    pos = {j: i for i, j in enumerate(order)}

    actuals, _label = M.load_actuals()
    by_player: dict[str, dict[int, float]] = {}
    for a in actuals:
        if a["games_delta"] < 1:
            continue
        i = pos.get(a.get("jornada"))
        if i is None:
            continue
        by_player.setdefault(norm(a["name"]), {})[i] = \
            a["points_delta"] / a["games_delta"]

    resolved = []
    for r in checked:
        i = pos.get(r["jornada"])
        if i is None:
            continue
        hist = by_player.get(norm(r["player"]), {})
        prior = sorted((idx, rate) for idx, rate in hist.items() if idx < i)
        if not prior:
            continue
        recent = [rate for _, rate in prior[-window:]]
        pred = sum(recent) / len(recent)
        resolved.append((pred, r["actual_points"], r["predicted_rate"]))

    if not resolved:
        return None
    n = len(resolved)
    naive_mae = sum(abs(p - a) for p, a, _ in resolved) / n
    ours_mae = sum(abs(o - a) for _, a, o in resolved) / n
    return {"n": n, "window": window, "naive_mae": naive_mae,
           "ours_mae": ours_mae}


def _actuals_index():
    import methodology as M
    from ffcore.text import norm

    actuals, _label = M.load_actuals(window_days=None)
    if not actuals:
        return None, None
    now = max(a["from_dt"] for a in actuals)
    by_player: dict[str, list[tuple[dt.datetime, float]]] = {}
    for a in actuals:
        if a["games_delta"] < 1:
            continue
        for k in a["keys"]:
            by_player.setdefault(k, []).append((a["from_dt"], a["points_delta"]))

    def points_between(name: str, since: dt.datetime,
                       until: dt.datetime | None = None) -> float:
        return sum(p for when, p in by_player.get(norm(name), [])
                   if when >= since and (until is None or when <= until))

    return points_between, now


def _pick_episodes(commits, pick) -> list[dict]:
    episodes = []
    last_pair_by_slot: dict[int, tuple] = {}
    for sha, when in commits:
        text = _show(sha, "reports/decisions.json")
        if text is None:
            continue
        try:
            data = json.loads(text)
        except (ValueError, TypeError):
            continue
        moves = data.get("moves") or []
        if not moves:
            continue
        for slot, chosen in enumerate(pick(moves)):
            buy, sell = chosen.get("buy"), chosen.get("sell")
            if not buy or not sell:
                continue
            pair = (buy, sell)
            if last_pair_by_slot.get(slot) == pair:
                continue
            last_pair_by_slot[slot] = pair
            episodes.append({"when": when, "buy": buy, "sell": sell,
                             "kind": chosen.get("kind"),
                             "label": chosen.get("label"), "slot": slot})
    return episodes


HORIZON_DAYS = 10.0


def _grade_episodes(episodes, points_between, now, min_days=None,
                    horizon_days: float = HORIZON_DAYS) -> dict | None:
    need = max(horizon_days, min_days or 0)
    resolved = []
    for ep in episodes:
        if (now - ep["when"]).days < need:
            continue
        until = ep["when"] + dt.timedelta(days=horizon_days)
        buy_pts = points_between(ep["buy"], ep["when"], until)
        sell_pts = sum(points_between(nm.strip(), ep["when"], until)
                      for nm in ep["sell"].split(" + "))
        resolved.append({**ep, "buy_pts": buy_pts, "sell_pts": sell_pts,
                         "net": buy_pts - sell_pts})
    if not resolved:
        return None
    n = len(resolved)
    nets = [r["net"] for r in resolved]
    total_net = sum(nets)
    wins = sum(1 for r in resolved if r["net"] > 0)
    return {"n": n, "total_episodes": len(episodes),
           "too_fresh": len(episodes) - n, "total_net": total_net,
           "mean_net": total_net / n, "wins": wins, "resolved": resolved,
           "nets": nets, "horizon_days": horizon_days}


def compare_arms(a: dict, b: dict, a_name: str, b_name: str) -> str:
    gap = stats.bootstrap_gap(a["nets"], b["nets"])
    lines = [f"{a_name}: {a['n']} eps, mean {a['mean_net']:+.1f} pts/ep "
             f"(total {a['total_net']:+.1f})",
             f"{b_name}: {b['n']} eps, mean {b['mean_net']:+.1f} pts/ep "
             f"(total {b['total_net']:+.1f})"]
    if gap is None:
        lines.append("-> not enough data on one side to compare")
    elif gap["beats"]:
        lines.append(f"-> {a_name} BEATS {b_name} (90% CI on the per-episode "
                     f"gap: {gap['lo']:+.1f} to {gap['hi']:+.1f} pts, "
                     "excludes zero)")
    else:
        lines.append(f"-> no significant difference (90% CI on the "
                     f"per-episode gap: {gap['lo']:+.1f} to {gap['hi']:+.1f} "
                     "pts, straddles zero)")
    return "\n  ".join(lines)


def replay_recommendations(min_days: float = 3.0) -> dict | None:
    commits = commits_touching("reports/decisions.json")
    if not commits:
        return None
    points_between, now = _actuals_index()
    if now is None:
        return None
    episodes = _pick_episodes(commits, lambda moves: moves[:1])
    return _grade_episodes(episodes, points_between, now, min_days)


def replay_percentile_rank(min_days: float = 3.0) -> dict | None:
    commits = commits_touching("reports/decisions.json")
    if not commits:
        return None
    points_between, now = _actuals_index()
    if now is None:
        return None

    def pick_by_ptslo(moves):
        candidates = [m for m in moves if m.get("pts_lo") is not None
                     and m.get("buy") and m.get("sell")]
        return [max(candidates, key=lambda m: m["pts_lo"])] if candidates else []

    episodes = _pick_episodes(commits, pick_by_ptslo)
    return _grade_episodes(episodes, points_between, now, min_days)


def replay_ladder_percentile(topn: int = 3, min_days: float = 3.0) -> dict:
    commits = commits_touching("reports/decisions.json")
    points_between, now = _actuals_index()
    if not commits or now is None:
        return {"current": None, "percentile": None}

    def pick_current(moves):
        return [m for m in moves[:topn] if m.get("buy") and m.get("sell")]

    def pick_pctile(moves):
        candidates = [m for m in moves if m.get("pts_lo") is not None
                     and m.get("buy") and m.get("sell")]
        return sorted(candidates, key=lambda m: -m["pts_lo"])[:topn]

    cur_eps = _pick_episodes(commits, pick_current)
    pct_eps = _pick_episodes(commits, pick_pctile)
    return {"current": _grade_episodes(cur_eps, points_between, now, min_days),
           "percentile": _grade_episodes(pct_eps, points_between, now, min_days)}


def _selftest() -> None:
    recent = commit_as_of(dt.datetime.now(dt.timezone.utc), "data/tidy/market.csv")
    assert recent is not None, "no commit found for market.csv at all"

    ancient = commit_as_of(dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc),
                           "data/tidy/market.csv")
    assert ancient is None, ancient

    never = commit_as_of(dt.datetime.now(dt.timezone.utc),
                         "data/tidy/this_file_does_not_exist.csv")
    assert never is None, never

    early = dt.datetime(2026, 8, 12, tzinfo=dt.timezone.utc)
    later = dt.datetime(2026, 8, 20, tzinfo=dt.timezone.utc)
    rows_early = csv_as_of(early, "data/tidy/market.csv")
    rows_later = csv_as_of(later, "data/tidy/market.csv")
    assert rows_early, "expected real market rows by 2026-08-12"
    assert rows_later, "expected real market rows by 2026-08-20"
    assert "observed_at" in rows_early[0], rows_early[0]

    assert csv_as_of(later, "data/tidy/nope_never_existed.csv") == []

    import methodology as M
    assert naive_value_baseline([]) is None
    golden = M.golden_rows()
    result = naive_value_baseline(golden)
    checked = [r for r in golden if r.get("predicted_rate") is not None]
    if checked:
        assert result is not None, "expected real historical values to resolve"
        assert result["n"] > 0, result
        assert result["k"] > 0, result
        print(f"  naive_value_baseline(): n={result['n']}, ours "
             f"{result['ours_mae']:.2f} MAE vs market-value-scaled "
             f"{result['naive_mae']:.2f} MAE")

    assert recency_only_baseline([]) is None
    rresult = recency_only_baseline(golden)
    if checked:
        if rresult is not None:
            assert rresult["n"] > 0, rresult
            print(f"  recency_only_baseline(): n={rresult['n']}, ours "
                 f"{rresult['ours_mae']:.2f} MAE vs last-{rresult['window']} "
                 f"recency {rresult['naive_mae']:.2f} MAE")
        else:
            print("  recency_only_baseline(): no player yet has a prior "
                 "played jornada to build a recency estimate from")

    ct = commits_touching("reports/decisions.json")
    assert ct, "expected real decisions.json history"
    times = [w for _, w in ct]
    assert times == sorted(times), "must be oldest-first"
    assert commits_touching("nope/never/existed.json") == []

    assert replay_recommendations(min_days=1e9) is None
    rec = replay_recommendations()
    if rec is not None:
        assert rec["n"] > 0, rec
        assert rec["n"] + rec["too_fresh"] == rec["total_episodes"], rec
        assert 0 <= rec["wins"] <= rec["n"], rec
        print(f"  replay_recommendations(): {rec['n']} graded episodes "
             f"({rec['too_fresh']} too fresh to grade yet), {rec['wins']}/"
             f"{rec['n']} net positive, total net {rec['total_net']:+.1f} "
             f"real pts, mean {rec['mean_net']:+.1f} pts/episode")

    assert replay_percentile_rank(min_days=1e9) is None
    prec = replay_percentile_rank()
    if prec is not None:
        assert prec["n"] > 0, prec
        assert prec["n"] + prec["too_fresh"] == prec["total_episodes"], prec
        assert 0 <= prec["wins"] <= prec["n"], prec
        print(f"  replay_percentile_rank(): {prec['n']} graded episodes "
             f"({prec['too_fresh']} too fresh), {prec['wins']}/{prec['n']} "
             f"net positive, total net {prec['total_net']:+.1f} real pts, "
             f"mean {prec['mean_net']:+.1f} pts/episode")
        if rec is not None:
            print("  " + compare_arms(prec, rec, "percentile-rank",
                                      "mean-rank"))

    empty = replay_ladder_percentile(topn=3, min_days=1e9)
    assert empty == {"current": None, "percentile": None}, empty
    ladder = replay_ladder_percentile(topn=3)
    assert set(ladder) == {"current", "percentile"}, ladder
    cur, pct = ladder["current"], ladder["percentile"]
    if cur is not None and pct is not None:
        assert cur["n"] > 0 and pct["n"] > 0, ladder
        if cur["total_net"] < 0:
            print(f"  ** the SHIPPED top-{3} ladder's own real historical "
                 f"net is NEGATIVE: {cur['total_net']:+.1f} pts over "
                 f"{cur['n']} episodes (mean {cur['mean_net']:+.1f}/ep) **")
        print(f"  replay_ladder_percentile(top3), horizon="
             f"{cur['horizon_days']:.0f}d:")
        print("  " + compare_arms(pct, cur, "percentile", "current"))

    print("backtest.py selftest OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        raise SystemExit(0)
