"""What a squad of yours gains over another in season points, worked out in
SQL rather than simulated: each forecast cell's mean and spread (the
Bootstrap's draw: plays with chance p, then a real single-match score scaled
to his points, times his persistent rate), your best eleven of each squad
each jornada as best_xi picks it, and the change in sum with its spread. A
move's chance of helping is the normal's at that mean and spread.

Why: the per-candidate simulation drew 3000 seasons for every squad the
board tried; on 140 past moves this agreed with it on every verdict
(p_better within 0.01, its noise): docs/notes/gain.md.
"""
from __future__ import annotations

import math
import sqlite3
import statistics

from ffcore.forecast import Bootstrap
from ffcore.rules import SHAPES, Squad

__all__ = ["gains"]

SCHEMA = """
CREATE TABLE cell (jornada INTEGER, player TEXT, e REAL, m REAL, v REAL,
                   PRIMARY KEY (jornada, player)) WITHOUT ROWID;
CREATE TABLE shape (shape INTEGER, slot TEXT, n INTEGER);
CREATE TABLE scored (jornada INTEGER PRIMARY KEY);
CREATE TABLE squad (id INTEGER, ord INTEGER, player TEXT, slot TEXT);
"""

# Each squad's eleven a jornada (best_xi: the formation whose top n a slot
# by expected points sum most, the first on a tie; a formation fits when
# every slot has its nth player), then each squad but 0
# against squad 0: its mean change, and its variance (each player's match
# noise, and his persistent rate, which moves all his jornadas together).
GAIN = """
WITH ranked AS MATERIALIZED (
    SELECT s.id, j.jornada, s.player, s.slot, row_number() OVER w AS r,
           sum(coalesce(c.e, 0.0)) OVER (w ROWS UNBOUNDED PRECEDING) AS top
    FROM squad s CROSS JOIN scored j LEFT JOIN cell c ON c.jornada = j.jornada AND c.player = s.player
    WINDOW w AS (PARTITION BY s.id, j.jornada, s.slot ORDER BY coalesce(c.e, 0.0) DESC, s.ord)),
totals AS (
    SELECT x.id, x.jornada, sh.shape, sum(x.top) AS total
    FROM shape sh JOIN ranked x ON x.slot = sh.slot AND x.r = sh.n
    GROUP BY x.id, x.jornada, sh.shape
    HAVING count(*) = (SELECT count(*) FROM shape t WHERE t.shape = sh.shape)),
best AS MATERIALIZED (SELECT id, jornada, shape FROM (
             SELECT *, row_number() OVER (PARTITION BY id, jornada ORDER BY total DESC, shape) AS b
             FROM totals) WHERE b = 1),
xi AS MATERIALIZED (SELECT x.id, x.jornada, x.player FROM best b JOIN shape sh ON sh.shape = b.shape
       JOIN ranked x ON x.id = b.id AND x.jornada = b.jornada AND x.slot = sh.slot AND x.r <= sh.n),
d AS (SELECT id, jornada, player, sum(w) AS w FROM (
          SELECT id, jornada, player, 1 AS w FROM xi WHERE id > 0
          UNION ALL
          SELECT o.id, x.jornada, x.player, -1 FROM xi x
          JOIN (SELECT DISTINCT id FROM squad WHERE id > 0) o WHERE x.id = 0)
      GROUP BY id, jornada, player HAVING sum(w) <> 0),
per AS (SELECT d.id, d.player, sum(d.w * c.m) AS a, sum(d.w * d.w * c.v) AS v
        FROM d JOIN cell c USING (jornada, player) GROUP BY d.id, d.player),
g AS (SELECT id, sum(a) AS mean, (1 + :s2) * sum(v) + :s2 * sum(a * a) AS var FROM per GROUP BY id)
SELECT o.id, coalesce(g.mean, 0.0), normal_above(coalesce(g.mean, 0.0), coalesce(g.var, 0.0))
FROM (SELECT DISTINCT id FROM squad WHERE id > 0) o LEFT JOIN g USING (id) ORDER BY o.id
"""

_DB: list[tuple[Bootstrap, sqlite3.Connection]] = []


def normal_above(mean: float, var: float) -> float:
    """P(X > 0) for X normal; no spread, 1 if the mean is above 0, else 0."""
    if var <= 1e-12:
        return 1.0 if mean > 1e-9 else 0.0
    return 0.5 * (1.0 + math.erf(mean / math.sqrt(2.0 * var)))


def _db(fc: Bootstrap) -> sqlite3.Connection:
    """The forecaster's cells as SQL, kept for it (a board asks hundreds of times)."""
    if _DB and _DB[0][0] is fc:
        return _DB[0][1]
    db = sqlite3.connect(":memory:")
    db.create_function("normal_above", 2, normal_above, deterministic=True)
    db.executescript(SCHEMA)
    m2 = statistics.fmean(x * x for x in fc.pool) / fc.pool_mean ** 2
    db.executemany("INSERT INTO cell VALUES (?, ?, ?, ?, ?)",
                   ((j, k, pts * p, pts * p, p * pts * pts * m2 - (p * pts) ** 2)
                    for j, cells in fc.per_jornada.items() for k, (pts, p) in cells.items()))
    db.executemany("INSERT INTO shape VALUES (?, ?, ?)",
                   [(i, slot, n) for i, s in enumerate(SHAPES) for slot, n in s.items()])
    _DB[:] = [(fc, db)]
    return db


def gains(fc: Bootstrap, jornadas: list[int], base: Squad, squads: list[Squad]
          ) -> list[tuple[float, float]]:
    """Each squad against base over these jornadas: (expected change in
    season points, chance it is more)."""
    if not squads:
        return []
    db = _db(fc)
    with db:
        db.execute("DELETE FROM squad")
        db.execute("DELETE FROM scored")
        db.executemany("INSERT INTO scored VALUES (?)", [(j,) for j in sorted(set(jornadas))])
        db.executemany("INSERT INTO squad VALUES (?, ?, ?, ?)",
                       [(i, o, k, slot) for i, sq in enumerate([base, *squads])
                        for o, (k, slot) in enumerate(sq.items())])
        got = db.execute(GAIN, {"s2": fc.share ** 2}).fetchall()
    assert [i for i, _m, _p in got] == list(range(1, len(squads) + 1))
    return [(m, p) for _i, m, p in got]


def _selftest() -> None:
    from ffcore.season import LeagueState, best_xi, simulate

    xi = {"k": "POR", **{"d%d" % i: "DEF" for i in range(4)}, **{"m%d" % i: "MED" for i in range(4)},
          "f0": "DEL", "f1": "DEL"}
    per = {j: {k: (4.0, 0.9) for k in xi} for j in (1, 2, 3)}
    for j in per:
        per[j].update({"bench": (1.0, 0.9), "star": (9.0, 0.8), "f1": (3.0, 0.9)})
    fc = Bootstrap(per, pool=[0, 1, 2, 2, 3, 5, 8, 12] * 30)
    base = {**xi, "bench": "MED"}
    buy_star = {**base, "star": "DEL"}
    sell_bench = {k: v for k, v in base.items() if k != "bench"}
    (g_star, p_star), (g_bench, p_bench) = gains(fc, [1, 2, 3], base, [buy_star, sell_bench])
    assert abs(g_star - 3 * (9.0 * 0.8 - 3.0 * 0.9)) < 1e-9, "the star takes f1's place three times"
    assert (g_bench, p_bench) == (0.0, 0.0), "a bench player's sale changes no eleven: no gain, never better"
    picked = best_xi(buy_star, {k: c[0] * c[1] for k, c in per[1].items()})
    assert "star" in picked and "f1" not in picked, "as best_xi picks"
    sim = [simulate(LeagueState({"me": sq}, [1, 2, 3], "me"), fc, trials=6000, seed=2).totals["me"]
           for sq in (base, buy_star)]
    better = sum(b > a + 1e-9 for a, b in zip(*sim)) / 6000
    assert abs(better - p_star) < 0.03, (better, p_star)
    assert gains(fc, [2, 3], base, [buy_star])[0][0] == g_star * 2 / 3, "only the jornadas asked"
    short = {"k": "POR", "d0": "DEF"}
    assert gains(fc, [1], short, [short]) == [(0.0, 0.0)], "no eleven either way: nothing"
    print("ffcore.gain self-test OK")


if __name__ == "__main__":
    _selftest()
