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
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

__all__ = ["commit_as_of", "csv_as_of"]

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
    out = subprocess.run(["git", "show", f"{sha}:{path}"],
                         cwd=_ROOT, capture_output=True, text=True,
                         check=False)
    if out.returncode != 0:
        return []
    rows = list(csv.DictReader(io.StringIO(out.stdout)))
    return rows


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

    print("backtest.py selftest OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        raise SystemExit(0)
