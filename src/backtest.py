"""
backtest.py — the time machine Stage 3 of the forecast-first rebuild plan
needs: what did the tidy store actually look like at a past point in time.

    python src/backtest.py --selftest

WHY GIT HISTORY, NOT THE RAW ARCHIVE. `data/raw/dt=*.tar.xz` holds every
sweep's raw HTML, which is the more complete record — but using it means
re-running sources.py's parsers, ffcore.crosswalk's join, and everything
downstream, for every historical point a comparison wants to check. This
repo already commits `data/tidy/*.csv` after every real run (`lfg-run`'s
own commit step): the ALREADY-PARSED, ALREADY-CROSSWALKED state, one git
commit per sweep, for free. `git show <commit>:data/tidy/X.csv` is a
CSV's real content as of that moment — no re-parsing, no re-joining,
just reading text out of git the same way `read_csv()` reads it off disk.

NO HINDSIGHT, BY CONSTRUCTION — the whole reason a backtest is worth
doing rather than just trusting the model. `commit_as_of(when, path)`
only ever looks at commits at or before `when`; asking it about a
tidy table's state five minutes before a jornada locked can never see a
row that arrived six minutes later, because that row's commit does not
exist yet at `when`. This is the property a live forecast has for free
(it cannot read the future) and a naive replay could easily lose (running
today's code against `git show HEAD:...` and pretending that was "the
data as of last month").
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

__all__ = ["commit_as_of", "csv_as_of", "commits_touching",
          "replay_recommendations"]

# The repo root — git commands run from here regardless of the caller's
# own working directory, the same reason run.py sets PYTHONPATH=src rather
# than relying on an assumed cwd.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def commit_as_of(when: dt.datetime, path: str) -> str | None:
    """The most recent commit that touched `path` at or before `when`, or
    None when no such commit exists (before the repo started tracking it,
    or `path` was never committed at all).

    ONE GIT CALL, `--before` DOES THE COMPARISON — not fetched-then-
    filtered in Python, which would need every commit's own timestamp
    pulled across just to throw most of them away. `--before` is git's own
    commit-time comparison, exactly the semantics wanted here.
    """
    out = subprocess.run(
        ["git", "log", "--format=%H", "--before", when.isoformat(), "-1",
         "--", path],
        cwd=_ROOT, capture_output=True, text=True, check=False)
    sha = out.stdout.strip()
    return sha or None


def _show(sha: str, path: str) -> str | None:
    """`git show <sha>:<path>`'s raw text, or None — the one place both
    `csv_as_of()` and `replay_recommendations()` reach into git's object
    store, so there is exactly one subprocess incantation to get right.
    """
    out = subprocess.run(["git", "show", f"{sha}:{path}"],
                         cwd=_ROOT, capture_output=True, text=True,
                         check=False)
    return out.stdout if out.returncode == 0 else None


def csv_as_of(when: dt.datetime, path: str) -> list[dict]:
    """`read_csv(path)`'s own rows, but as they stood at `when` — [] when
    the path didn't exist yet at that point, the same "missing is empty,
    not an error" contract `ffcore.tidy.read_csv` already uses.

    NOT `ffcore.tidy.read_csv()`'s CACHE — a deliberately separate path.
    That cache is keyed on the file's CURRENT (mtime, size) on disk; a
    historical git blob has neither, and reusing the same cache dict would
    risk a live run's fresh read colliding with a backtest's historical
    one under a path-only key that was never meant to carry a time
    dimension. Keeping this its own small reader, uncached, is the same
    call `read_csv_frozen()`'s own docstring makes for a different reason
    (an isolation the shared cache cannot give safely) rather than a
    missed reuse.
    Why: docs/notes/backtest.md#csv_as_of--not-read_csvs-cache-a-deliberately-separate-path
    """
    sha = commit_as_of(when, path)
    if sha is None:
        return []
    text = _show(sha, path)
    return list(csv.DictReader(io.StringIO(text))) if text is not None else []


def commits_touching(path: str) -> list[tuple[str, dt.datetime]]:
    """[(sha, commit time)] for every commit that touched `path`, OLDEST
    FIRST — `--follow` so a rename in the file's history (this repo's own
    `reports/decisions.json` predecessor was a different name before
    `cc61b84`) doesn't silently truncate the record.
    """
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


def naive_value_baseline(golden: list[dict]) -> dict | None:
    """Does the rate forecast beat a REAL, historically-reconstructed
    alternate approach — not just a flat constant (rate_baseline_check()'s
    own, weaker question)?

    Stage 3's first genuine "candidate approach" comparison: a naive
    predictor built from a player's REAL market value as of just before
    the jornada locked (`csv_as_of()`, no hindsight — the value used is
    exactly what a manager could have seen at that moment, never a later
    reading). Scaled by one fitted constant (mean actual / mean value
    over the same sample) so it's compared in the same units as points,
    the same one-parameter-fit idea `rate_baseline_check()`'s own
    constant-mean guess already uses — a genuinely different predictor
    (market value, not our own rate model), not the same number under a
    new name.

    `golden` is `methodology.golden_rows()`'s own output — only rows with
    a real rate outcome (`predicted_rate` is not None) are used. `None`
    when there's nothing to compare (mirrors baseline_check()'s own
    contract).
    """
    import methodology as M
    from ffcore.text import norm

    checked = [r for r in golden if r.get("predicted_rate") is not None]
    if not checked:
        return None
    matches = M.read_csv(M.TIDY / "matches.csv")
    fixtures = M.read_csv(M.TIDY / "fixtures.csv")
    locks = M.jornada_locks(matches, fixtures)

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
    """Does the rate forecast beat a SECOND, genuinely different real
    candidate — recent on-field form, no shrinkage, no market value, no
    fixture — the "recency-only" approach the forecast-first rebuild plan
    named as still untested after `naive_value_baseline()`.

    THE PREDICTOR: a player's own mean per-match rate over his last
    `window` real, ALREADY-PLAYED jornadas — ordered by real lock time
    (`methodology.lock_order()`, the same fix `lagged_pair()` needed for
    the same reason: a rescheduled fixture can lock jornada 6 before
    jornada 4) — using ONLY jornadas strictly before the one being
    predicted. No git history needed here (unlike `naive_value_baseline()`
    and its market-value read): `methodology.load_actuals()` already
    carries the player's own real per-jornada points, in order, for free.

    A player with fewer than 1 prior played jornada has no recency
    estimate at all yet (a true cold start) and is skipped — same "not
    enough evidence" honesty as everywhere else in this codebase, not a
    guessed rate.

    `golden` is `methodology.golden_rows()`'s own output, same contract as
    `naive_value_baseline()`: only rows with a real rate outcome are used,
    `None` when there's nothing to compare.
    """
    import methodology as M
    from ffcore.text import norm

    checked = [r for r in golden if r.get("predicted_rate") is not None]
    if not checked:
        return None
    matches = M.read_csv(M.TIDY / "matches.csv")
    fixtures = M.read_csv(M.TIDY / "fixtures.csv")
    locks = M.jornada_locks(matches, fixtures)
    order = M.lock_order(locks)
    pos = {j: i for i, j in enumerate(order)}

    actuals, _label = M.load_actuals()
    # {norm name: {lock-order position: real per-match rate that jornada}}
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
    """(real_points_since(name, since), now) — the real per-jornada points
    ledger every replay function below grades against, built once so
    comparing two picking rules never risks reading two different slices
    of history by accident.
    """
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

    def real_points_since(name: str, since: dt.datetime) -> float:
        return sum(p for when, p in by_player.get(norm(name), [])
                   if when >= since)

    return real_points_since, now


def _pick_episodes(commits, pick) -> list[dict]:
    """One episode per distinct (buy, sell) pair `pick(moves)` returns,
    counted at the FIRST day that pair appeared — see
    `replay_recommendations()`'s own note on why per-commit would drown
    real signal in the same advice repeated across many runs.
    """
    episodes = []
    last_pair = None
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
        chosen = pick(moves)
        if chosen is None:
            continue
        buy, sell = chosen.get("buy"), chosen.get("sell")
        if not buy or not sell:
            continue
        pair = (buy, sell)
        if pair == last_pair:
            continue
        last_pair = pair
        episodes.append({"when": when, "buy": buy, "sell": sell,
                         "kind": chosen.get("kind"), "label": chosen.get("label")})
    return episodes


def _grade_episodes(episodes, real_points_since, now, min_days) -> dict | None:
    """`episodes` -> the same summary shape every replay function returns,
    graded on real points scored strictly after each episode's own day.
    """
    resolved = []
    for ep in episodes:
        if (now - ep["when"]).days < min_days:
            continue
        buy_pts = real_points_since(ep["buy"], ep["when"])
        sell_pts = sum(real_points_since(nm.strip(), ep["when"])
                      for nm in ep["sell"].split(" + "))
        resolved.append({**ep, "buy_pts": buy_pts, "sell_pts": sell_pts,
                         "net": buy_pts - sell_pts})
    if not resolved:
        return None
    n = len(resolved)
    total_net = sum(r["net"] for r in resolved)
    wins = sum(1 for r in resolved if r["net"] > 0)
    return {"n": n, "total_episodes": len(episodes),
           "too_fresh": len(episodes) - n, "total_net": total_net,
           "mean_net": total_net / n, "wins": wins, "resolved": resolved}


def replay_recommendations(min_days: float = 3.0) -> dict | None:
    """Did following the DECISION LAYER's own real recommendations, on the
    real days it made them, actually gain real points — not "is the
    forecast accurate" (everything else in this file), but "would the
    advice have won."

    Miguel, 2026-09-06, after two forecast-accuracy backtests and a
    DRIFT_FRAC fit already both showed nothing to change: "we have all the
    historical data and the outputs from the previous model, let's compare
    and do a better version to win this league, enough waiting." This is
    that comparison — no more forecast-ingredient tests, the actual
    top-ranked "buy X, sell Y" call, replayed against what really happened.

    THE SOURCE: `reports/decisions.json`'s own `moves[0]` — the single
    highest-ranked recommendation BY MEAN d_pos, exactly what a manager
    reading the report that day would have acted on — across all 200+ real
    commits since 2026-08-18 (`commits_touching()`, oldest first, real
    dates, no hindsight: each day's advice is graded only on points scored
    AFTER that day, never before). See `replay_percentile_rank()` for the
    same replay with a DIFFERENT picking rule, off the same commit history.

    `min_days`: an episode needs real runway to say anything — one
    recommended yesterday has barely had a chance to be right or wrong yet.
    Excluded, not scored as a loss; the summary states how many were too
    fresh to grade.

    Returns None when there's nothing to replay at all (no commit history,
    real points history, or no episode has cleared `min_days` yet).
    """
    commits = commits_touching("reports/decisions.json")
    if not commits:
        return None
    real_points_since, now = _actuals_index()
    if now is None:
        return None
    episodes = _pick_episodes(commits, lambda moves: moves[0])
    return _grade_episodes(episodes, real_points_since, now, min_days)


def replay_percentile_rank(min_days: float = 3.0) -> dict | None:
    """The SAME replay as `replay_recommendations()`, one thing changed:
    the picking rule. Instead of the mean-ranked `moves[0]`, pick whichever
    ALREADY-SCREENED candidate in that same day's `moves` list has the best
    `pts_lo` — the 10th percentile of its own paired trial distribution
    (`ffcore.season.band()`'s own definition), already computed and
    already sitting in every historical `reports/decisions.json`, no new
    simulation needed.

    WHY THIS ANSWERS "would ranking by a safer quantile have done better,
    up til now" (Miguel, 2026-09-06) WITHOUT waiting or rebuilding
    anything: `pts_lo` is naturally lower for a move whose case leans on
    distant, DRIFT_FRAC-widened jornadas than for one whose case is mostly
    near-term and solid, even at an identical mean — exactly the "prefer
    safer, nearer-term-loaded gains" instinct behind the question, with no
    invented time-discount constant, reusing uncertainty machinery already
    validated this session (rate_rel, club_rel, DRIFT_FRAC).

    A REAL, DISCLOSED LIMIT: this can only ever pick among candidates the
    OLD mean-based screen already let through into that day's `moves` list
    — it cannot resurrect a candidate the old screen discarded outright for
    a poor mean despite a possibly-strong pts_lo. A genuinely fair test of
    percentile-first screening (not just percentile-first RE-ranking of an
    already mean-screened shortlist) would need the full historical
    Universe reconstruction Stage 3's plan flagged as still unbuilt. This
    is the honest, immediately-available partial answer, not the complete
    one.
    """
    commits = commits_touching("reports/decisions.json")
    if not commits:
        return None
    real_points_since, now = _actuals_index()
    if now is None:
        return None

    def pick_by_ptslo(moves):
        candidates = [m for m in moves if m.get("pts_lo") is not None
                     and m.get("buy") and m.get("sell")]
        return max(candidates, key=lambda m: m["pts_lo"]) if candidates else None

    episodes = _pick_episodes(commits, pick_by_ptslo)
    return _grade_episodes(episodes, real_points_since, now, min_days)


def _selftest() -> None:
    # -- commit_as_of: a real path in this real repo, checked against git's
    # own log rather than assumed --------------------------------------
    # market.csv has been committed on every real sweep since 2026-08-11 —
    # any point after that should find SOME commit.
    recent = commit_as_of(dt.datetime.now(dt.timezone.utc), "data/tidy/market.csv")
    assert recent is not None, "no commit found for market.csv at all"

    # Before this repo existed: no commit, not a crash and not a guess.
    ancient = commit_as_of(dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc),
                           "data/tidy/market.csv")
    assert ancient is None, ancient

    # A path that has never been committed: also None, same contract.
    never = commit_as_of(dt.datetime.now(dt.timezone.utc),
                         "data/tidy/this_file_does_not_exist.csv")
    assert never is None, never

    # -- csv_as_of: NO HINDSIGHT — the property that makes this a real
    # backtest rather than a rewritten one -------------------------------
    # Ask for market.csv "as of" a moment in the past; the answer must be
    # a real, non-empty table (real data existed by then), and asking
    # again for a LATER moment must never come back with FEWER rows for a
    # monotonically-growing snapshot log — a real regression here would
    # mean the "before" cutoff is silently looking into the future.
    early = dt.datetime(2026, 8, 12, tzinfo=dt.timezone.utc)
    later = dt.datetime(2026, 8, 20, tzinfo=dt.timezone.utc)
    rows_early = csv_as_of(early, "data/tidy/market.csv")
    rows_later = csv_as_of(later, "data/tidy/market.csv")
    assert rows_early, "expected real market rows by 2026-08-12"
    assert rows_later, "expected real market rows by 2026-08-20"
    assert "observed_at" in rows_early[0], rows_early[0]

    # A genuinely nonexistent path: [] not an exception — read_csv()'s own
    # "missing is empty" contract, matched here on purpose.
    assert csv_as_of(later, "data/tidy/nope_never_existed.csv") == []

    # -- naive_value_baseline(): the real thing Stage 3 was for — does the
    # rate forecast beat a GENUINELY DIFFERENT, historically-reconstructed
    # predictor, not just a flat constant. Real data only (the whole point
    # is whether this repo's own git history actually resolves real
    # historical market values for real golden rows) -----------------------
    import methodology as M
    assert naive_value_baseline([]) is None
    golden = M.golden_rows()
    result = naive_value_baseline(golden)
    checked = [r for r in golden if r.get("predicted_rate") is not None]
    if checked:
        assert result is not None, "expected real historical values to resolve"
        assert result["n"] > 0, result
        assert result["k"] > 0, result        # value and points both positive
        print(f"  naive_value_baseline(): n={result['n']}, ours "
             f"{result['ours_mae']:.2f} MAE vs market-value-scaled "
             f"{result['naive_mae']:.2f} MAE")

    # -- recency_only_baseline(): the SECOND candidate approach the plan
    # named as still untested — recent on-field form, no shrinkage, no
    # market value at all. Real data only, same reasoning as above --------
    assert recency_only_baseline([]) is None
    rresult = recency_only_baseline(golden)
    if checked:
        # Not asserted non-None: a real cold-start season (every checked
        # player's own FIRST played jornada) could legitimately resolve
        # nothing yet — that's the honest "not enough history" case, not
        # a bug, so only check the shape when it did resolve.
        if rresult is not None:
            assert rresult["n"] > 0, rresult
            print(f"  recency_only_baseline(): n={rresult['n']}, ours "
                 f"{rresult['ours_mae']:.2f} MAE vs last-{rresult['window']} "
                 f"recency {rresult['naive_mae']:.2f} MAE")
        else:
            print("  recency_only_baseline(): no player yet has a prior "
                 "played jornada to build a recency estimate from")

    # -- commits_touching(): oldest-first, real commit times -----------------
    ct = commits_touching("reports/decisions.json")
    assert ct, "expected real decisions.json history"
    times = [w for _, w in ct]
    assert times == sorted(times), "must be oldest-first"
    assert commits_touching("nope/never/existed.json") == []

    # -- replay_recommendations(): the real question Miguel actually asked —
    # not "is the forecast accurate", "would following the advice have won" —
    assert replay_recommendations(min_days=1e9) is None    # nothing that stale
    rec = replay_recommendations()
    if rec is not None:
        assert rec["n"] > 0, rec
        assert rec["n"] + rec["too_fresh"] == rec["total_episodes"], rec
        assert 0 <= rec["wins"] <= rec["n"], rec
        print(f"  replay_recommendations(): {rec['n']} graded episodes "
             f"({rec['too_fresh']} too fresh to grade yet), {rec['wins']}/"
             f"{rec['n']} net positive, total net {rec['total_net']:+.1f} "
             f"real pts, mean {rec['mean_net']:+.1f} pts/episode")

    # -- replay_percentile_rank(): the SAME replay, one picking rule changed
    # -- does ranking by pts_lo (10th pctile) instead of moves[0] (mean)
    # actually do better against real history? -----------------------------
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
            better = "BEATS" if prec["total_net"] > rec["total_net"] \
                else "LOSES TO" if prec["total_net"] < rec["total_net"] \
                else "TIES"
            print(f"  -> percentile-rank {better} mean-rank on real history "
                 f"({prec['total_net']:+.1f} vs {rec['total_net']:+.1f} pts)")

    print("backtest.py selftest OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        raise SystemExit(0)
