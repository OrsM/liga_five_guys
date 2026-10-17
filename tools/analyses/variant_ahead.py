"""Is a variant's forecast better than live's past the next jornada? Both
backtests (grading.backtest, GRADE_AHEAD on) graded as runs of their own,
then paired_gain at each jornada ahead.

    FF_ROOT=<a copy of data> PYTHONPATH=src python tools/analyses/variant_ahead.py NAME

Define NAME first (grading.py variant NAME CONSTANT=VALUE). Writes two runs
(trigger 'variant:live', 'variant:NAME') into the copy's lfg.db.
"""
from __future__ import annotations

import sys
from contextlib import closing

from assemble import applied
from ffcore.db import connect, named, record
from ffcore.points import jornada_points
from grading import GRADE_AHEAD, backtest, forecast_rows


def graded(variant: str) -> int:
    """The backtest under variant, recorded as a run of grade_forecast rows."""
    with closing(connect()) as db, db:
        run = db.execute("INSERT INTO runs (started, as_of, trigger, commit_sha, ok)"
                         " VALUES ('', '', ?, '', 1)", ("variant:" + variant,)).lastrowid
    assert run is not None
    with applied(variant):
        fc = forecast_rows(backtest(GRADE_AHEAD), jornada_points())
    record([{"run_id": run, **r} for r in fc], "grade_forecast")
    print("%s: run %d, %d forecasts" % (variant, run, len(fc)), flush=True)
    return run


def main(name: str) -> None:
    old, new = graded("live"), graded(name)
    for ahead in range(GRADE_AHEAD + 1):
        cols, got = named("paired_gain", {"old": str(old), "new": str(new), "ahead": str(ahead)})
        for r in got:
            print("ahead %d  " % ahead + "  ".join("%s=%s" % kv for kv in zip(cols, r)))


if __name__ == "__main__":
    main(sys.argv[1])
