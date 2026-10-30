"""How long a board takes, against the last time it was measured: run it
before a commit, and a slowdown shows up like a failing test.

    python tools/perf.py          # time each date, compare with tools/perf.json; exit 1 past SLOWER
    python tools/perf.py --save   # the same, and keep these times as the ones to compare with

Each date's universe is built cold (LFG_KEPT=off: nothing read back from
kept.py), then its board (decide.board, every kappa), on the dry-run copy
of the data (FF_ROOT, as the backtests use it). The backtest is about 140
of these, so a second here is two minutes there (2026-10-30: backtests had
crept from minutes to over an hour, commit by commit, unseen).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import time
from pathlib import Path

os.environ["LFG_KEPT"] = "off"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import decide  # noqa: E402
import grading  # noqa: E402

DATES = (dt.datetime(2026, 8, 25, 8, 0, tzinfo=dt.timezone.utc),
         dt.datetime(2026, 9, 20, 8, 0, tzinfo=dt.timezone.utc),
         dt.datetime(2026, 10, 20, 8, 0, tzinfo=dt.timezone.utc))
SAVED = Path(__file__).with_name("perf.json")
# A total this much over the saved one fails: room for a busy machine, not
# for a regression.
SLOWER = 1.3


def measure() -> dict[str, dict[str, float]]:
    """Each date's seconds: its universe and its board."""
    out = {}
    for t in DATES:
        s = time.perf_counter()
        u = grading.universe_when(t)
        built = time.perf_counter() - s
        assert u is not None, t
        s = time.perf_counter()
        decide.board(u)
        out[t.date().isoformat()] = {"universe": round(built, 2), "board": round(time.perf_counter() - s, 2)}
    return out


def main() -> int:
    got = measure()
    was = json.loads(SAVED.read_text()) if SAVED.exists() else {}
    total = sum(sum(v.values()) for v in got.values())
    before = sum(sum(v.values()) for v in was.values()) if was else None
    for d, v in got.items():
        w = was.get(d, {})
        print("%s  universe %5.2fs (was %s)  board %5.2fs (was %s)" % (
            d, v["universe"], w.get("universe", "-"), v["board"], w.get("board", "-")))
    print("total %.1fs, was %s" % (total, "%.1fs" % before if before else "not measured"))
    if "--save" in sys.argv:
        SAVED.write_text(json.dumps(got, indent=1) + "\n")
        return 0
    if before and total > SLOWER * before:
        print("SLOWER: %.0f%% over the saved times" % (100 * (total / before - 1)))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
