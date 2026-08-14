"""
points.py — this season's points, from the snapshots you already take.

ff_ingest saves points.html.gz in every twice-daily snapshot, and has since
day one. Nothing read them until now. This turns every one of them into two
files per season label:

    data/season/live/running_<label>.csv     running totals, kept snapshots only
    data/season/live/perjornada_<label>.csv  what changed between kept snapshots

Like ff_ingest.parse, it is a full rebuild from raw on every run: fix the
parser and every past snapshot is repaired. Both outputs are disposable.

**Kept** means the totals actually moved. Points only change after matches,
so of ~14 snapshots a week perhaps two carry news; the rest are identical and
are dropped rather than written. That keeps the running file at roughly one
row per player per jornada rather than fourteen.

The per-jornada file is the diff between consecutive kept snapshots. When a
player's games went up by exactly 1, `points_delta` is what he scored in that
match — the training row Phase 1 needs. A player who first appears mid-season
diffs against zero, which is correct: whatever he has, he earned since the
last kept snapshot.

Two deliberate limits:

  * No jornada numbers. The interval between two kept stamps identifies the
    matches involved; mapping stamps to jornada ids is a join for model code
    to do later, against a calendar that doesn't exist in this repo yet.
    Guessing here would just be a second copy of that logic to keep honest.
  * report.py does NOT read data/season/live/ — deliberately, and this module
    keeps it that way. A two-jornada sample must not start driving your XI
    (see history.py's docstring). Blending live totals into the Scorer is its
    own change, made on purpose, not a side effect of tidying.

The season label comes from each snapshot's own HTML (the page's season
selector), so the day futbolfantasy flips the default from 2025/26 to the new
season, the new label simply starts its own pair of files. The pre-flip
snapshots all collapse into one kept row of last season's final totals —
harmless, and a nice check that dedupe works.

Nothing else imports this. Deps: lxml (via history.parse), stdlib otherwise.

    python src/points.py              # rebuild data/season/live/ from raw
    python src/points.py --selftest   # pure-logic checks, no deps, no IO
"""

from __future__ import annotations

import gzip
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ffcore.text import norm  # noqa: E402
from ffcore.tidy import ROOT, SEASON, write_csv  # noqa: E402

RAW = ROOT / "raw"
LIVE = SEASON / "live"

RUN_FIELDS = ["observed_at", "season", "player_name", "player_name_full",
              "team", "points", "games", "avg"]
DIFF_FIELDS = ["from_stamp", "to_stamp", "season", "player_name",
               "player_name_full", "team", "points_delta", "games_delta",
               "points_total", "games_total"]


# ---------------------------------------------------------------------------
# pure logic — selftested below, no parsing, no IO
# ---------------------------------------------------------------------------

def totals(rows: list[dict]) -> dict[str, tuple[float, float]]:
    """{key: (points, games)} — the comparable core of one snapshot."""
    out = {}
    for r in rows:
        key = norm(r.get("player_name_full") or r.get("player_name") or "")
        if key:
            out[key] = (float(r["points"]), float(r["games"]))
    return out


def keep_changed(seq: list[tuple[str, list[dict]]]) -> list[tuple[str, list[dict]]]:
    """Drop snapshots whose totals are identical to the previous kept one.

    `seq` is [(stamp, rows)] in time order. The first is always kept.
    """
    kept, prev = [], None
    for stamp, rows in seq:
        cur = totals(rows)
        if cur != prev:
            kept.append((stamp, rows))
            prev = cur
    return kept


def diff(prev_rows: list[dict], cur_rows: list[dict],
         from_stamp: str, to_stamp: str, season: str) -> list[dict]:
    """Per-player deltas between two kept snapshots.

    Emits only players whose points or games moved. A player absent from the
    earlier snapshot diffs against (0, 0).
    """
    prev = totals(prev_rows)
    out = []
    for r in cur_rows:
        key = norm(r.get("player_name_full") or r.get("player_name") or "")
        if not key:
            continue
        pts, pj = float(r["points"]), float(r["games"])
        p0, j0 = prev.get(key, (0.0, 0.0))
        if pts == p0 and pj == j0:
            continue
        out.append({
            "from_stamp": from_stamp, "to_stamp": to_stamp, "season": season,
            "player_name": r.get("player_name", ""),
            "player_name_full": r.get("player_name_full", ""),
            "team": r.get("team", ""),
            "points_delta": f"{pts - p0:g}",
            "games_delta": f"{pj - j0:g}",
            "points_total": f"{pts:g}",
            "games_total": f"{pj:g}",
        })
    return out


# ---------------------------------------------------------------------------
# rebuild from raw
# ---------------------------------------------------------------------------

def load_snapshots() -> dict[str, list[tuple[str, list[dict]]]]:
    """{season label: [(stamp, rows)]} from every dt=* snapshot, in order.

    history.parse and history.season_label are imported lazily so --selftest
    needs neither lxml nor httpx.
    """
    from history import parse, season_label

    by_label: dict[str, list[tuple[str, list[dict]]]] = {}
    for snap in sorted(RAW.glob("dt=*")):
        f = snap / "points.html.gz"
        if not f.exists():
            continue
        stamp = snap.name.removeprefix("dt=")
        try:
            html = gzip.open(f, "rt", encoding="utf-8").read()
            rows = parse(html)
        except Exception as e:
            # One bad page must not lose the rest of the run.
            print(f"  warn: {snap.name}/points: {type(e).__name__}: {e}")
            continue
        if not rows:
            print(f"  warn: {snap.name}/points parsed to 0 rows — "
                  f"markup changed? Raw is kept; fix parse and re-run.")
            continue
        by_label.setdefault(season_label(html), []).append((stamp, rows))
    return by_label


def main() -> None:
    by_label = load_snapshots()
    if not by_label:
        sys.exit("no points.html.gz found under data/raw/dt=*/ — "
                 "run ff_ingest.py fetch first")

    for label, seq in sorted(by_label.items()):
        kept = keep_changed(seq)

        running = []
        for stamp, rows in kept:
            for r in rows:
                running.append({"observed_at": stamp, "season": label, **r})

        deltas = []
        for (s0, r0), (s1, r1) in zip(kept, kept[1:]):
            deltas.append(diff(r0, r1, s0, s1, label))

        LIVE.mkdir(parents=True, exist_ok=True)
        write_csv(LIVE / f"running_{label}.csv", running, RUN_FIELDS)
        flat = [row for d in deltas for row in d]
        write_csv(LIVE / f"perjornada_{label}.csv", flat, DIFF_FIELDS)

        moved = sum(1 for d in deltas if d)
        print(f"{label}: {len(seq)} snapshots -> {len(kept)} kept, "
              f"{moved} interval(s) with movement, "
              f"{len(flat)} per-jornada rows")

    print(f"wrote {LIVE}/ — report.py does not read this folder, on purpose.")


# ---------------------------------------------------------------------------
# selftest — pure logic only
# ---------------------------------------------------------------------------

def _selftest() -> None:
    def row(full, pts, pj, short=None, team="X"):
        return {"player_name": short or full, "player_name_full": full,
                "team": team, "points": str(pts), "games": str(pj),
                "avg": ""}

    a = [row("Ane Aldea", 0, 0), row("Bo Bidal", 0, 0)]
    b = [row("Ane Aldea", 0, 0), row("Bo Bidal", 0, 0)]      # identical
    c = [row("Ane Aldea", 8, 1), row("Bo Bidal", 0, 0)]      # Ane played
    d = [row("Ane Aldea", 8, 1), row("Bo Bidal", 3, 1),      # Bo played,
         row("Cai Coro", 5, 1)]                               # Cai appeared

    kept = keep_changed([("t0", a), ("t1", b), ("t2", c), ("t3", d)])
    assert [s for s, _ in kept] == ["t0", "t2", "t3"], kept

    d1 = diff(a, c, "t0", "t2", "s")
    assert len(d1) == 1 and d1[0]["player_name_full"] == "Ane Aldea"
    assert d1[0]["points_delta"] == "8" and d1[0]["games_delta"] == "1"

    d2 = diff(c, d, "t2", "t3", "s")
    got = {r["player_name_full"]: r["points_delta"] for r in d2}
    assert got == {"Bo Bidal": "3", "Cai Coro": "5"}, got   # Ane unchanged

    # A mid-season first appearance diffs against zero, not an error.
    assert next(r for r in d2 if r["player_name_full"] == "Cai Coro"
                )["games_delta"] == "1"

    print("points.py selftest OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        main()
