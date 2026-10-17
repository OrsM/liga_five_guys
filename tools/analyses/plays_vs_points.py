"""Where a forecast misses past the next jornada: the chance he plays, or the
points when he does. Each past lock's backtest cells (points if he plays,
chance he plays) against whether he played (squad_minutes) and what he
scored, by value quarter (value now).

    FF_ROOT=<a copy of data> PYTHONPATH=src python tools/analyses/plays_vs_points.py
"""
from __future__ import annotations

import datetime as dt
from contextlib import closing

from assemble import fixture_ratings, scorer
from ffcore.clock import set_now
from ffcore.db import connect, record
from ffcore.jornadas import round_locks, rounds_ahead
from ffcore.points import jornada_points
from ffcore.schedule import expectations
from ffcore.tidy import LINEUP_SOURCE, current
from grading import GRADE_AHEAD


def cells() -> list[dict[str, object]]:
    """Every backtest cell 1 to GRADE_AHEAD jornadas past its lock."""
    locks, actual = round_locks(), jornada_points()
    done = {j for _k, j in actual}
    out: list[dict[str, object]] = []
    try:
        for i in sorted(done & set(locks), key=lambda j: locks[j]):
            set_now(locks[i] - dt.timedelta(minutes=1))
            market = current("market")
            sc = scorer(market, current("lineups", LINEUP_SOURCE))
            per_j = expectations(sc, fixture_ratings(market), set(sc.lookup),
                                 current("matches"), rounds_ahead()).per_j
            out += [{"from_j": i, "jornada": j, "player": k, "pts": c[0], "p": c[1],
                     "actual": actual.get((k, j), 0.0)}
                    for j in range(i + 1, i + GRADE_AHEAD + 1) if j in done and j in per_j
                    for k, c in per_j[j].items()]
    finally:
        set_now(None)
    return out


SPLIT = """
WITH v AS (SELECT ff_id AS player, ntile(4) OVER (ORDER BY value) AS q FROM current_market),
played AS (SELECT DISTINCT player, jornada FROM squad_minutes WHERE mins > 0 AND player IS NOT NULL),
c AS (SELECT c.*, v.q, p.player IS NOT NULL AS went FROM probe_cells c JOIN v USING (player)
      LEFT JOIN played p USING (player, jornada))
SELECT q, count(*) AS n, round(avg(p), 3) AS p_forecast, round(avg(went), 3) AS p_played,
       round(sum(CASE WHEN went THEN pts END) / sum(went), 2) AS pts_forecast_when_played,
       round(sum(CASE WHEN went THEN actual END) / sum(went), 2) AS pts_scored_when_played
FROM c GROUP BY q ORDER BY q"""


if __name__ == "__main__":
    rows = cells()
    with closing(connect()) as db, db:
        db.execute("DROP TABLE IF EXISTS probe_cells")
        db.execute("CREATE TABLE probe_cells (from_j, jornada, player, pts, p, actual)")
    record(rows, "probe_cells")
    with closing(connect()) as db:
        cur = db.execute(SPLIT)
        print("  ".join(d[0] for d in cur.description))
        for r in cur:
            print("  ".join(str(x) for x in r))
