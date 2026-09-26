
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys


__all__ = ["commits_touching", "track_record", "replay_recommendations"]

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _show(sha: str, path: str) -> str | None:
    out = subprocess.run(["git", "show", f"{sha}:{path}"],
                         cwd=_ROOT, capture_output=True, text=True,
                         check=False)
    return out.stdout if out.returncode == 0 else None


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


def _actuals_index():
    import grading
    from ffcore.text import norm

    actuals = grading.load_actuals(window_days=None)
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


def _format_track_record(r: dict | None) -> str | None:
    if r is None or r["n"] < 5:
        return None
    return ("the report's #1 move has netted **%+.1f pts** on average over "
           "the %g days that followed (%d of its last %d calls, %.0f%% "
           "won)" % (r["mean_net"], HORIZON_DAYS, r["n"], r["total_episodes"],
                     100 * r["wins"] / r["n"]))


def track_record(min_days: float = 3.0) -> str | None:
    return _format_track_record(replay_recommendations(min_days))


def _replay_setup(path: str = "reports/decisions.json"):
    commits = commits_touching(path)
    if not commits:
        return None
    points_between, now = _actuals_index()
    if now is None:
        return None
    return commits, points_between, now


def _replay(pick, min_days: float) -> dict | None:
    setup = _replay_setup()
    if setup is None:
        return None
    commits, points_between, now = setup
    return _grade_episodes(_pick_episodes(commits, pick), points_between,
                           now, min_days)


def replay_recommendations(min_days: float = 3.0) -> dict | None:
    return _replay(lambda moves: moves[:1], min_days)


def _selftest() -> None:
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

    assert _format_track_record(None) is None
    assert _format_track_record({"n": 4, "total_episodes": 4, "mean_net": 9.0,
                                 "wins": 4}) is None
    got = _format_track_record({"n": 10, "total_episodes": 12,
                                "mean_net": 2.5, "wins": 6})
    assert got is not None and "+2.5 pts" in got and "10 of its last 12" in \
        got and "60%" in got, got

    print("backtest.py selftest OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        raise SystemExit(0)
