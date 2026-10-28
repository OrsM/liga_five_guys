"""Season points in SQL: each forecast cell's mean and variance as the
Bootstrap defines them, each squad's eleven a jornada (ELEVEN), and their
sums. Why not simulated: docs/notes/gain.md."""
from __future__ import annotations

import math
from collections.abc import Callable
import sqlite3
import statistics
from dataclasses import dataclass
from statistics import NormalDist

from ffcore.forecast import Bootstrap
from ffcore.rules import SHAPES, Squad, Squads

__all__ = ["Standings", "cell_variance", "eleven", "gains", "standings"]

SCHEMA = """
CREATE TABLE cell (jornada INTEGER, player TEXT, e REAL, m REAL, v REAL,
                   PRIMARY KEY (jornada, player)) WITHOUT ROWID;
CREATE TABLE shape (shape INTEGER, slot TEXT, n INTEGER);
CREATE TABLE scored (jornada INTEGER PRIMARY KEY);
CREATE TABLE squad (id INTEGER, ord INTEGER, player TEXT, slot TEXT);
CREATE TABLE valued (player TEXT PRIMARY KEY, e REAL) WITHOUT ROWID;
"""

# The eleven rule, per (id, jornada) of pick: the formation whose top n a
# slot by e sum most; formations within 1e-9 tie (the first wins), players
# level in a slot keep the squad's order.
ELEVEN = """
ranked AS MATERIALIZED (
    SELECT id, jornada, player, slot, row_number() OVER w AS r,
           sum(e) OVER (w ROWS UNBOUNDED PRECEDING) AS top
    FROM pick WINDOW w AS (PARTITION BY id, jornada, slot ORDER BY e DESC, ord)),
totals AS (
    SELECT x.id, x.jornada, sh.shape, sum(x.top) AS total
    FROM shape sh JOIN ranked x ON x.slot = sh.slot AND x.r = sh.n
    GROUP BY x.id, x.jornada, sh.shape
    HAVING count(*) = (SELECT count(*) FROM shape t WHERE t.shape = sh.shape)),
best AS MATERIALIZED (SELECT id, jornada, shape FROM (
             SELECT *, row_number() OVER (PARTITION BY id, jornada ORDER BY round(total, 9) DESC, shape) AS b
             FROM totals) WHERE b = 1),
xi AS MATERIALIZED (SELECT x.id, x.jornada, x.player FROM best b JOIN shape sh ON sh.shape = b.shape
       JOIN ranked x ON x.id = b.id AND x.jornada = b.jornada AND x.slot = sh.slot AND x.r <= sh.n)"""

XI = "WITH pick AS (SELECT 1 AS id, 0 AS jornada, s.player, s.slot, s.ord, coalesce(v.e, 0.0) AS e" \
     " FROM squad s LEFT JOIN valued v USING (player))," + ELEVEN + "\nSELECT player FROM xi"

# Each squad (id > 0) against squad 0 over the jornadas scored: mean and
# variance of the change, a player's persistent rate shared by his jornadas.
GAIN = """
WITH pick AS (
    SELECT s.id, j.jornada, s.player, s.slot, s.ord, coalesce(c.e, 0.0) AS e
    FROM squad s CROSS JOIN scored j LEFT JOIN cell c ON c.jornada = j.jornada AND c.player = s.player),""" + ELEVEN + """,
d AS (SELECT id, jornada, player, sum(w) AS w FROM (
          SELECT id, jornada, player, 1 AS w FROM xi WHERE id > 0
          UNION ALL
          SELECT o.id, x.jornada, x.player, -1 FROM xi x
          JOIN (SELECT DISTINCT id FROM squad WHERE id > 0) o WHERE x.id = 0)
      GROUP BY id, jornada, player HAVING sum(w) <> 0),
per AS (SELECT d.id, d.player, sum(d.w * c.m) AS a, sum(d.w * d.w * c.v) AS v
        FROM d JOIN cell c USING (jornada, player) GROUP BY d.id, d.player),
g AS (SELECT id, sum(a) AS mean, (1 + :s2) * sum(v) + :s2 * sum(a * a) AS var FROM per GROUP BY id)
SELECT o.id, coalesce(g.mean, 0.0), coalesce(g.var, 0.0),
       normal_above(coalesce(g.mean, 0.0), coalesce(g.var, 0.0))
FROM (SELECT DISTINCT id FROM squad WHERE id > 0) o LEFT JOIN g USING (id) ORDER BY o.id
"""

_CON: list[sqlite3.Connection] = []
_CELLS: list[Bootstrap] = []      # whose cells are loaded


def normal_above(mean: float, var: float) -> float:
    """P(X > 0) for X normal; no spread, 1 if the mean is above 0, else 0."""
    if var <= 1e-12:
        return 1.0 if mean > 1e-9 else 0.0
    return 1.0 - NormalDist(mean, math.sqrt(var)).cdf(0.0)


def _con() -> sqlite3.Connection:
    if not _CON:
        db = sqlite3.connect(":memory:")
        db.create_function("normal_above", 2, normal_above, deterministic=True)
        db.executescript(SCHEMA)
        db.executemany("INSERT INTO shape VALUES (?, ?, ?)",
                       [(i, slot, n) for i, s in enumerate(SHAPES) for slot, n in s.items()])
        _CON.append(db)
    return _CON[0]


def cell_variance(fc: Bootstrap) -> Callable[[float, float], float]:
    """A forecast cell's match variance as the Bootstrap draws it (points if
    he plays pts, chance p): p*pts^2*E[pool^2]/mean(pool)^2 - (p*pts)^2."""
    m2 = statistics.fmean(x * x for x in fc.pool) / fc.pool_mean ** 2
    return lambda pts, p: p * pts * pts * m2 - (p * pts) ** 2


def _db(fc: Bootstrap) -> sqlite3.Connection:
    """The connection, with this forecaster's cells loaded."""
    db = _con()
    if not _CELLS or _CELLS[0] is not fc:
        var = cell_variance(fc)
        with db:
            db.execute("DELETE FROM cell")
            db.executemany("INSERT INTO cell VALUES (?, ?, ?, ?, ?)",
                           ((j, k, pts * p, pts * p, var(pts, p))
                            for j, cells in fc.per_jornada.items() for k, (pts, p) in cells.items()))
        _CELLS[:] = [fc]
    return db


def eleven(squad: Squad, value: dict[str, float]) -> list[str]:
    """Squad's (player: slot) eleven by value (missing: 0), ELEVEN; [] if none fits."""
    db = _con()
    with db:
        db.execute("DELETE FROM squad")
        db.execute("DELETE FROM valued")
        db.executemany("INSERT INTO squad VALUES (1, ?, ?, ?)",
                       [(o, k, slot) for o, (k, slot) in enumerate(squad.items())])
        db.executemany("INSERT INTO valued VALUES (?, ?)", [(k, value[k]) for k in squad if k in value])
        return [k for k, in db.execute(XI)]


def gains(fc: Bootstrap, jornadas: list[int], base: Squad, squads: list[Squad]
          ) -> list[tuple[float, float]]:
    """Each squad against base over jornadas: (mean change, P(change > 0))."""
    return [(m, p) for m, _v, p in moments(fc, jornadas, base, squads)]


def moments(fc: Bootstrap, jornadas: list[int], base: Squad, squads: list[Squad]
             ) -> list[tuple[float, float, float]]:
    """Each squad against base over jornadas: (mean, variance, P(> 0))."""
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
    assert [r[0] for r in got] == list(range(1, len(squads) + 1))
    return [(m, v, p) for _i, m, v, p in got]


@dataclass(frozen=True)
class Standings:
    """Each manager's final points as a normal: (mean incl. points banked, sd)."""
    dist: dict[str, tuple[float, float]]
    me: str = ""

    def mean(self, manager: str) -> float:
        return self.dist.get(manager, (0.0, 0.0))[0]

    def band(self, manager: str, lo: float = 0.1, hi: float = 0.9) -> tuple[float, float]:
        mu, sd = self.dist.get(manager, (0.0, 0.0))
        if sd <= 0:
            return mu, mu
        z = NormalDist()
        return mu + sd * z.inv_cdf(lo), mu + sd * z.inv_cdf(hi)

    def beat(self, rival: str, manager: str = "") -> float:
        """P(manager, default you, ends above rival); level counts half."""
        me = manager or self.me
        if me not in self.dist or rival not in self.dist:
            return 0.0
        (a, sa), (b, sb) = self.dist[me], self.dist[rival]
        if sa * sa + sb * sb <= 1e-12 and abs(a - b) <= 1e-9:
            return 0.5
        return normal_above(a - b, sa * sa + sb * sb)

    def position(self, manager: str = "") -> dict[int, float]:
        """P(each final position: 1 + those strictly above), integrated over his total."""
        me = manager or self.me
        if me not in self.dist:
            return {}
        mu, sd = self.dist[me]
        others = [v for m, v in self.dist.items() if m != me]
        z = NormalDist()
        grid = [(mu, 1.0)] if sd <= 0 else [
            (mu + sd * x / 25.0, z.pdf(x / 25.0)) for x in range(-200, 201)]
        total = sum(w for _t, w in grid)
        out = [0.0] * (len(others) + 1)
        for t, w in grid:
            ways = [1.0]
            for o_mu, o_sd in others:
                q = (1.0 if o_mu > t else 0.0) if o_sd <= 0 else 1.0 - z.cdf((t - o_mu) / o_sd)
                ways = [a * (1 - q) + b * q for a, b in zip([*ways, 0.0], [0.0, *ways])]
            out = [o + w / total * c for o, c in zip(out, ways)]
        return {k + 1: p for k, p in enumerate(out) if p > 0}

    def expected_position(self, manager: str = "") -> float:
        me = manager or self.me
        return 1.0 + sum(self.beat(me, o) for o in self.dist if o != me) if me in self.dist else 0.0


def standings(fc: Bootstrap, squads: Squads, jornadas: list[int], carried: dict[str, float],
              me: str) -> Standings:
    """Every manager's final points, his squad fielding every jornada left."""
    mans = list(squads)
    got = moments(fc, jornadas, {}, [squads[m] for m in mans])
    return Standings({m: (mean + carried.get(m, 0.0), math.sqrt(max(var, 0.0)))
                      for m, (mean, var, _p) in zip(mans, got)}, me)


def _selftest() -> None:
    from ffcore.rules import XI_SIZE

    squad = {"k": "POR", "d1": "DEF", "d2": "DEF", "d3": "DEF", "d4": "DEF",
             "d5": "DEF", "m1": "MED", "m2": "MED", "m3": "MED", "m4": "MED",
             "m5": "MED", "f1": "DEL", "f2": "DEL", "f3": "DEL"}
    val = {k: 1.0 for k in squad}
    val.update({"f1": 9.0, "f2": 9.0, "f3": 9.0})
    got = eleven(squad, val)
    assert len(got) == XI_SIZE and {"f1", "f2", "f3"} <= set(got), got
    assert eleven({"d1": "DEF"}, {"d1": 1.0}) == [], "no formation fits: no eleven"
    # 5-4-1 (DEF4 at 2.0) and 4-5-1 (MED3 at 2.0) sum the same, added in
    # different orders: found by checking against the old best_xi
    tv = {"DEF4": 2.0, "DEF0": 8.491227695871778, "MED0": 2.5, "MED2": 7.411348877403508, "MED5": 0.0,
          "POR0": 2.5, "DEF2": 2.0, "POR1": 2.5, "DEF6": 1.0, "DEL0": 3.0, "MED4": 3.0, "DEF5": 2.5,
          "MED1": 2.0, "DEF1": 2.5, "MED3": 2.0, "DEF3": 2.10641317928871}
    tie = {k: k[:3] for k in tv}
    assert "DEF4" in eleven(tie, tv) and "MED3" not in eleven(tie, tv), \
        "a tie between formations is the first (5-4-1), whatever the rounding"

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
    picked = eleven(buy_star, {k: c[0] * c[1] for k, c in per[1].items()})
    assert "star" in picked and "f1" not in picked, "the eleven gains scores is eleven's"
    # the star for f1 in three jornadas: match noise of both, and each one's rate across the three
    m2 = statistics.fmean(x * x for x in fc.pool) / fc.pool_mean ** 2
    def noise(pts: float, p: float) -> float:
        return p * pts * pts * m2 - (p * pts) ** 2
    s2 = fc.share ** 2
    var = (1 + s2) * 3 * (noise(9.0, 0.8) + noise(3.0, 0.9)) + s2 * ((3 * 7.2) ** 2 + (3 * 2.7) ** 2)
    assert abs(p_star - (1 - NormalDist(g_star, math.sqrt(var)).cdf(0))) < 1e-12, p_star
    assert gains(fc, [2, 3], base, [buy_star])[0][0] == g_star * 2 / 3, "only the jornadas asked"
    short = {"k": "POR", "d0": "DEF"}
    assert gains(fc, [1], short, [short]) == [(0.0, 0.0)], "no eleven either way: nothing"
    riv = {k.upper(): v for k, v in xi.items()}
    per2 = {j: {**per[j], **{k.upper(): (2.0, 0.9) for k in xi}} for j in per}
    fc2 = Bootstrap(per2, pool=fc.pool)
    st = standings(fc2, {"me": base, "riv": riv}, [1, 2, 3], {"riv": 10.0}, "me")
    assert abs(st.mean("me") - 3 * sum(per[1][k][0] * per[1][k][1] for k in xi)) < 1e-9
    assert abs(st.mean("riv") - (10.0 + 3 * 11 * 1.8)) < 1e-9, "his points carried count"
    (mu, sd), (rmu, rsd) = st.dist["me"], st.dist["riv"]
    assert abs(st.beat("riv") - (1 - NormalDist(mu - rmu, math.hypot(sd, rsd)).cdf(0))) < 1e-12
    lo, hi = st.band("me")
    assert lo < st.mean("me") < hi
    pos = st.position()
    assert abs(sum(pos.values()) - 1) < 1e-6 and abs(pos[1] - st.beat("riv")) < 1e-3, pos
    assert abs(st.expected_position() - (1 + st.beat("me", "riv"))) < 1e-12
    level = Standings({"a": (5.0, 0.0), "b": (5.0, 0.0)}, "a")
    assert level.beat("b") == 0.5 and level.position() == {1: 1.0}, "level: half, and nobody above"
    print("ffcore.gain self-test OK")


if __name__ == "__main__":
    _selftest()
