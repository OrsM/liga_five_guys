"""The forecast module's store and what it keeps (docs/notes/lfg.md, "Architecture"):
the moments forecast() writes, the price momentum read for a decision's odds, and the closed
days of the price series kept so a question is a lookup (price_step, momentum_day; keep())."""
from __future__ import annotations

import sqlite3
import zlib
from collections.abc import Callable, Sequence
from contextlib import closing
from pathlib import Path
from typing import NamedTuple

from ffcore.db import connect, named, rows

__all__ = ["keep", "migrate", "forecast", "price_momentum", "price_change", "seed_cells", "Prices"]

# Each player's valued days (each day's last snapshot: market_daily), numbered (n); the
# change in % on his previous valued day (NULL for the first, or after a
# value of 0); the number of changes so far (i) and the two before it (s1, s2),
# as price_series counts them. Closed days only: those before the latest day with a snapshot.
PRICE_STEP = """
DELETE FROM price_step;
INSERT INTO price_step (ff_id, n, day, observed_at, value, step, i, s1, s2)
WITH base AS (
    SELECT ff_id, observed_at, value, row_number() OVER w - 1 AS n,
           CASE WHEN lag(value) OVER w > 0 THEN 100 * (value / lag(value) OVER w - 1) END AS step
    FROM (SELECT ff_id, observed_at, value, id FROM market_daily
          WHERE observed_at < (SELECT substr(max(observed_at), 1, 10) FROM price_history)
            AND coalesce(ff_id, '') <> '' AND value IS NOT NULL)
    WINDOW w AS (PARTITION BY ff_id ORDER BY observed_at, id)),
changes AS (
    SELECT ff_id, n, row_number() OVER q - 1 AS i, lag(step, 1) OVER q AS s1, lag(step, 2) OVER q AS s2
    FROM base WHERE step IS NOT NULL WINDOW q AS (PARTITION BY ff_id ORDER BY n))
SELECT b.ff_id, b.n, substr(b.observed_at, 1, 10), b.observed_at, b.value, b.step, c.i, c.s1, c.s2
FROM base b LEFT JOIN changes c ON c.ff_id = b.ff_id AND c.n = b.n
"""

# What each closed day adds to the momentum fit, for one set of the constants
# price_series and momentum_pairs are made with (k.ckey), every horizon up to
# the longest any cut can use (price_hmax only grows, by at most one a day).
MOMENTUM_DAY = """
DELETE FROM momentum_day WHERE ckey = (SELECT ckey FROM k);
INSERT INTO momentum_day
SELECT (SELECT ckey FROM k), h, end_day, coalesce(sum(x * y), 0), sum(x * x)
FROM momentum_pairs GROUP BY h, end_day
"""
FAR = "9999-12-31T2359Z"
PAD = 2


def _make(db: sqlite3.Connection, name: str, sql: str, through: str, salt: str = "") -> None:
    """The kept fact name made by sql, unless the record says it is current (another process may have made it)."""
    version = "%08x" % zlib.crc32((sql + salt).encode())
    if db.execute("SELECT version, through FROM facts_meta WHERE name = ?", (name,)).fetchone() == (version, through):
        return
    db.execute("BEGIN IMMEDIATE")
    try:
        if db.execute("SELECT version, through FROM facts_meta WHERE name = ?", (name,)).fetchone() != (version, through):
            for statement in sql.split(";\n"):
                if statement.strip():
                    db.execute(statement)
            db.execute("INSERT OR REPLACE INTO facts_meta VALUES (?, ?, ?)", (name, version, through))
        db.execute("COMMIT")
    except BaseException:
        db.execute("ROLLBACK")
        raise


def keep(db: sqlite3.Connection, now: Callable[[], str]) -> None:
    """price_step and momentum_day up to the data: nothing when current. They are made with t put past
    the last day (the kept days are the data's, whatever t the connection looks at), and now (the
    connection's clock) is put back after."""
    ckey = db.execute("SELECT ckey FROM k").fetchone()[0]
    db.create_function("cut", 0, lambda: FAR)
    db.create_function("hpad", 0, lambda: PAD)      # a cut a day on can reach one horizon further
    try:
        latest = db.execute("SELECT substr(max(observed_at), 1, 10) FROM price_history").fetchone()[0] or ""
        _make(db, "price_step", PRICE_STEP, latest)
        if latest:                                  # else nothing to fit
            _make(db, "momentum_day:" + ckey, MOMENTUM_DAY, latest, str(PAD))
    finally:
        db.create_function("cut", 0, now)
        db.create_function("hpad", 0, lambda: 0)


def migrate(db: sqlite3.Connection) -> None:
    """The forecast's store as it was when a code hash keyed it (2026-11-04)
    made the SQL forecast's: its moments and cells kept, the fits and
    variants tables gone."""
    if "made" not in {r[1] for r in db.execute("PRAGMA table_info(forecast_moment)")}:
        return
    for (v,) in db.execute("SELECT name FROM sqlite_master WHERE type = 'view'").fetchall():
        db.execute('DROP VIEW "%s"' % v)
    for t in ("forecast_cell", "forecast_player"):
        db.execute("DELETE FROM %s WHERE moment NOT IN (SELECT moment FROM forecast_moment)" % t)
    db.executescript("""
        DROP TABLE IF EXISTS forecast_fit; DROP TABLE IF EXISTS forecast_variant;
        CREATE TABLE forecast_moment_new (moment INTEGER PRIMARY KEY, code TEXT NOT NULL DEFAULT 'sql',
                                          as_of TEXT NOT NULL, variant TEXT NOT NULL DEFAULT 'live');
        INSERT INTO forecast_moment_new SELECT moment, code, as_of, 'live' FROM forecast_moment;
        DROP TABLE forecast_moment; ALTER TABLE forecast_moment_new RENAME TO forecast_moment;""")


def forecast() -> int:
    """The forecast at t (forecast_now) made into a new moment, every
    player's cells and who is flagged (player_status); the moments of this
    cut and variant no run or grade points to dropped. The moment."""
    with closing(connect()) as db:
        cells = db.execute("SELECT player, jornada, pts, p, ppm, fixture, picked, fit FROM forecast_now").fetchall()
        flagged = dict(db.execute("SELECT player, status IS NOT NULL FROM player_status"))
        with db:
            old = "SELECT moment FROM forecast_moment WHERE as_of = cut() AND variant = variant() AND NOT pinned"
            for t in ("forecast_cell", "forecast_player", "forecast_moment"):
                db.execute("DELETE FROM %s WHERE moment IN (%s)" % (t, old))
            moment = db.execute("INSERT INTO forecast_moment (as_of, variant) VALUES (cut(), variant())").lastrowid
            assert moment is not None
            db.executemany("INSERT INTO forecast_cell VALUES (%d, ?, ?, ?, ?, ?, ?, ?, ?)" % moment, cells)
            db.executemany("INSERT INTO forecast_player VALUES (%d, ?, ?)" % moment,
                           [(k, flagged.get(k, 0)) for k in sorted({c[0] for c in cells})])
    return moment


def seed_cells(db: sqlite3.Connection, moment: int, cells: Sequence[tuple[str, int, float, float]]) -> None:
    """A moment's cells (player, jornada, pts, p) as given, the moment's earlier ones gone: what a test
    of a later module seeds."""
    db.execute("DELETE FROM forecast_cell WHERE moment = ?", (moment,))
    db.executemany("INSERT INTO forecast_cell (moment, player, jornada, pts, p) VALUES (%d, ?, ?, ?, ?)" % moment, cells)


class Prices(NamedTuple):
    """Momentum as schema.sql fits it: a slope per horizon, the share of a
    trend made after each update, each player's trend in %, and the horizon."""
    slope: dict[int, float]
    carry: tuple[float, ...]
    trend: dict[str, float]
    horizon: int


def price_momentum() -> Prices:
    """Momentum and the price trend at t (views: momentum, price_carry,
    price_trend), read."""
    slope: dict[int, float] = dict(rows("SELECT h, slope FROM momentum ORDER BY h"))
    return Prices(slope, tuple(v for (v,) in rows("SELECT carry FROM price_carry ORDER BY h")),
                  dict(rows("SELECT ff_id, trend FROM price_trend")), max(slope, default=1))


def price_change(day: str, h: int) -> dict[str, float | None]:
    """Each player's change in % over the h updates after his last one on or
    before day, all time (None where there are not h more: not known yet):
    what a replay looks back on."""
    return dict(named("price_change", {"day": day, "h": h})[1])


def _selftest_keep() -> None:
    import tempfile

    from ffcore.db import connect
    from ffcore.tidy import store_sql, tables_in

    def steps(db: sqlite3.Connection) -> list[tuple[object, ...]]:
        return db.execute("SELECT ff_id, n, day, round(step, 4), i FROM price_step ORDER BY ff_id, n").fetchall()

    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        with closing(connect()) as db:
            assert steps(db) == [], "no market: nothing kept"
        # a: 100, 110 (two snapshots that day, the later counts), 0 (floor), 50; b: seen once
        for at, vals in (("2026-08-10T0800Z", {"a": 100, "b": 7}), ("2026-08-11T0800Z", {"a": 105}),
                         ("2026-08-11T2000Z", {"a": 110}), ("2026-08-12T2000Z", {"a": 0}),
                         ("2026-08-13T2000Z", {"a": 50})):
            store_sql("market", [{"observed_at": at, "ff_id": k, "value": str(v)} for k, v in vals.items()])
            with closing(connect()) as db:
                kept = steps(db)
        assert kept == [("a", 0, "2026-08-10", None, None), ("a", 1, "2026-08-11", 10.0, 0),
                        ("a", 2, "2026-08-12", -100.0, 1), ("b", 0, "2026-08-10", None, None)], \
            "the later snapshot of a day counts; the 13th is not closed, so not kept yet"
        store_sql("market", [{"observed_at": "2026-08-14T0800Z", "ff_id": "a", "value": "60"}])
        with closing(connect()) as db:
            assert steps(db)[3] == ("a", 3, "2026-08-13", None, None), \
                "a change from a value of 0 is no change, and the 13th is kept once the 14th has begun"
        with closing(connect()) as db:
            before = db.execute("SELECT count(*) FROM price_step").fetchone()[0]
            db.execute("DELETE FROM facts_meta")
            db.execute("DELETE FROM price_step")
        with closing(connect()) as db:
            assert db.execute("SELECT count(*) FROM price_step").fetchone()[0] == before, "rebuilt when the record is gone"
    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        from datetime import datetime, timezone

        from ffcore.db import set_now
        for day in (10, 11, 12, 13):
            store_sql("market", [{"observed_at": "2026-08-%02dT2000Z" % day, "ff_id": "a", "value": str(100 + day)}])
        try:
            set_now(datetime(2026, 8, 11, 12, tzinfo=timezone.utc))      # a clock well before the data ends
            with closing(connect()) as db:
                assert [r[2] for r in steps(db)] == ["2026-08-10", "2026-08-11", "2026-08-12"], \
                    "the kept days are the data's, whatever t the connection looks at"
        finally:
            set_now(None)
    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        from ffcore.db import rows
        import random
        rng = random.Random(7)
        level = {k: 100.0 for k in ("up", "down", "wobble", "late", "spike")}
        for day in range(1, 18):                              # 17 days: one more horizon opens on the last
            prices: dict[str, float] = {}
            for k in level:
                level[k] *= 1 + rng.uniform(-0.06, 0.09)      # steps that differ, so a sum counted twice shows
                if not (k == "late" and day < 5):
                    prices[k] = level[k]
            store_sql("market", [{"observed_at": "2026-08-%02dT%s" % (day, h), "ff_id": k, "value": str(v * f)}
                                 for k, v in prices.items() for h, f in (("0800Z", 0.99), ("2000Z", 1.0))])
        with closing(connect()) as db:
            kept = db.execute("SELECT count(*) FROM momentum_day").fetchone()[0]
            assert kept > 0, "the days' sums are kept once the constants are known"
        for cut in ("2026-08-17T0900Z", "2026-08-17T2100Z", "2026-08-09T2100Z", "2026-08-03T0000Z"):
            from datetime import datetime, timezone

            from ffcore.db import set_now
            try:
                set_now(datetime.strptime(cut, "%Y-%m-%dT%H%MZ").replace(tzinfo=timezone.utc))
                kept_fit = dict(rows("SELECT h, slope FROM momentum"))
                direct = dict(rows("SELECT h, sum(x * y) / sum(x * x) FROM momentum_pairs GROUP BY h HAVING sum(x * x) <> 0"))
            finally:
                set_now(None)
            assert kept_fit.keys() == direct.keys() and all(abs(kept_fit[h] - direct[h]) < 1e-9 for h in direct), \
                (cut, kept_fit, direct)
        def series_at(cut: str) -> list[tuple[object, ...]]:
            from datetime import datetime, timezone

            from ffcore.db import set_now
            try:
                set_now(datetime.strptime(cut, "%Y-%m-%dT%H%MZ").replace(tzinfo=timezone.utc))
                return [tuple(r) for r in rows("SELECT ff_id, i, round(x, 9), value FROM price_series ORDER BY ff_id, i")]
            finally:
                set_now(None)
        assert series_at("2026-08-17T2100Z") == series_at("2026-08-29T0000Z") != [], \
            "a cut days after the data ends sees the last day, as it stood"
        assert series_at("2026-08-17T0900Z") != series_at("2026-08-17T2100Z"), "and a cut within a day sees that day so far (the morning's snapshot)"
        from ffcore.db import set_constants
        with set_constants(CLIP=5.0, PRICE_W0=1.0, PRICE_W1=0.0, PRICE_W2=0.0):
            with closing(connect()) as db:
                assert db.execute("SELECT count(DISTINCT ckey) FROM momentum_day").fetchone()[0] == 2, "each set of constants its own days"
            assert dict(rows("SELECT h, slope FROM momentum")).keys() == dict(rows(
                "SELECT h, sum(x * y) / sum(x * x) FROM momentum_pairs GROUP BY h HAVING sum(x * x) <> 0")).keys()

def _selftest_forecast() -> None:
    """schema.sql's forecast on fact rows: club_rating, forecast_rate,
    forecast_fit and the cell (forecast_now), and forecast() making it a
    moment."""
    import datetime as dt
    import tempfile

    from ffcore import forecast as model
    from ffcore.db import record, rows, set_now
    from ffcore.tidy import store_sql, tables_in
    from stats import shrink

    seen, kick = "2026-09-19T1000Z", {2: "2026-09-21T19:00:00+00:00", 3: "2026-09-28T19:00:00+00:00"}
    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        try:
            set_now(dt.datetime(2026, 9, 20, 12, tzinfo=dt.timezone.utc))
            store_sql("matches", [{"observed_at": seen, "match_id": "m%d%s" % (j, h), "jornada": str(j), "home": h,
                                   "away": w, "score": s, "kickoff": k} for j, h, w, s, k in [
                (1, "a", "b", "1-0", ""), (1, "c", "d", "0-0", ""),
                (2, "a", "b", "", kick[2]), (2, "c", "d", "2-2", ""),
                (3, "b", "a", "", kick[3]), (3, "d", "c", "", kick[3])]])
            # a scores 3 a match and concedes 1, b the other way; c and d none
            store_sql("results_history", [{"observed_at": seen, "season": "2526", "date": "d%d" % i,
                                           "home_name": "A", "away_name": "B", "home": "a", "away": "b",
                                           "home_goals": "3", "away_goals": "1"} for i in range(10)])
            store_sql("market", [{"observed_at": seen, "ff_id": k, "name": k, "club": c, "position": p, "value": "5000000"}
                                 for k, c, p in [("fwd", "a", "delantero"), ("back", "a", "defensa"),
                                                 ("hurt", "b", "delantero"), ("susp", "b", "delantero"),
                                                 ("dbt", "b", "delantero"), ("gone", "b", "delantero"),
                                                 ("cee", "c", "delantero"), ("coach", "a", "entrenador"),
                                                 ("nocl", "", "delantero")]])
            record([{"player_id": k, "ff_slug": k, "app_id": "9" if k == "gone" else ""}
                    for k in ("fwd", "back", "hurt", "susp", "dbt", "gone", "cee")], "crosswalk")
            store_sql("lineups", [{"observed_at": seen, "source": "futbolfantasy", "team_slug": c, "player_slug": k,
                                   "player_name": k, "start_pct": pct, "status": st, "note": note}
                                  for k, c, pct, st, note in [
                ("fwd", "a", "80", "ok", ""), ("back", "a", "", "ok", ""),
                ("hurt", "b", "0", "injured", "Lesión Desde 10/09 Baja hasta finales de septiembre"),
                ("susp", "b", "0", "suspended", ""), ("dbt", "b", "50", "doubt", "x Duda para la jornada 3")]])
            store_sql("api_players_all", [{"observed_at": seen, "player_id": "9", "player_status": "out_of_league"}])
            record([{"season": "2025-26", "ff_id": "fwd", "points": 150.0, "games": 30.0}], "season_points")

            mu = 2.0
            rating = {c: (a, d) for c, _n, a, d in rows("SELECT club, n, attack, defence FROM club_rating")}
            k = model.FIXTURE_K
            assert abs(rating["a"][0] - (30 + k * mu) / ((10 + k) * mu)) < 1e-12 \
                and abs(rating["b"][1] - (30 + k * mu) / ((10 + k) * mu)) < 1e-12, \
                "goals for and against a match over the league's, pulled toward it by FIXTURE_K matches"
            assert set(rating) == {"a", "b"}, "a club with no results has no rating"

            prior = dict(rows("SELECT player, prior FROM ppm_prior"))
            rate = {p: (ppm, pk) for p, ppm, pk in rows("SELECT player, ppm, picked FROM forecast_rate")}
            sk, pk = model.SHRINK_K, model.PICK_K
            assert abs(rate["fwd"][0] - shrink(shrink(prior["fwd"], sk, 150.0, 30.0), sk, 0.0, 0.0)) < 1e-12, \
                "his value's prior, pulled toward last season's 5 a match"
            assert rate["hurt"][0] == prior["hurt"], "no record at all: his value's"
            assert rate["fwd"][1] == 0.8 and rate["back"][1] == model.NEUTRAL_START / 100, \
                "his listing's start %; listed with none, NEUTRAL_START"
            assert rate["hurt"][1] == rate["dbt"][1] == model.NEUTRAL_START / 100, \
                "flagged: his listing speaks of the injury, not of selection"
            assert rate["cee"][1] == model.ABSENT_START / 100 and pk > 0, "not listed: ABSENT_START"

            cells = {(p, j): r for p, j, *r in rows("SELECT player, jornada, pts, p, ppm, fixture, picked, fit"
                                                     " FROM forecast_now")}
            assert set(cells) == {(p, j) for p in ("fwd", "back", "hurt", "susp", "dbt", "gone") for j in (2, 3)} \
                | {("cee", 3)}, "every jornada left his club plays; a coach, a player with no club: none"
            site = {("hurt", 2): 0.0, ("susp", 2): 0.0, ("dbt", 2): 0.5}   # flagged, with a start % for his next match
            for (p, j), (pts, pr, ppm, fix, picked, fit) in cells.items():
                assert abs(pts - max(0.0, ppm * fix)) < 1e-12, "points if he plays = ppm x fixture"
                assert abs(pr - site.get((p, j), picked * fit)) < 1e-12, \
                    "the chance he plays = picked x fit; for a flagged player's next match, the listing's start %, as it says"
            assert abs(cells["fwd", 2][3] - rating["b"][1]) < 1e-12, "an attacker: his opponent's defence"
            assert abs(cells["back", 2][3] - 1 / rating["b"][0]) < 1e-12, "a defender: 1 / its attack"
            assert cells["cee", 3][3] == 1.0, "an opponent with no rating: 1"
            fit = {(p, j): r[5] for (p, j), r in cells.items()}
            assert (fit["fwd", 2], fit["hurt", 2], fit["hurt", 3]) == (1.0, 0.0, 1.0), \
                "fit unless flagged; out until the 25th: not on the 21st, fit on the 28th"
            assert (fit["susp", 2], fit["susp", 3]) == (0.0, 1.0), "a suspension is his next match"
            assert (fit["dbt", 2], fit["dbt", 3]) == (0.0, 0.5), "a doubt for 3: out before it, half at it"
            assert fit["gone", 2] == fit["gone", 3] == 0.0, "left the league: out for good"

            first = forecast()
            assert rows("SELECT count(*) FROM forecast_cell WHERE moment = ?", (first, )) == [(len(cells), )]
            assert rows("SELECT player FROM forecast_player WHERE moment = ? AND flagged ORDER BY player",
                        (first, )) == [("dbt", ), ("gone", ), ("hurt", ), ("susp", )], "who was flagged then"
            with closing(connect()) as db, db:
                db.execute("UPDATE forecast_moment SET pinned = 1 WHERE moment = ?", (first, ))
            forecast()
            third = forecast()
            assert [m for m, in rows("SELECT moment FROM forecast_moment ORDER BY moment")] \
                == [first, third], "made again each time; the cut's earlier ones go but a run's, its record"
            assert rows("SELECT count(*) FROM forecast_cell WHERE moment NOT IN (SELECT moment FROM forecast_moment)") \
                == [(0, )]
        finally:
            set_now(None)


def _selftest_cells() -> None:
    """seed_cells puts the cells of a moment where the forecast keeps them (what a test of the decision seeds)."""
    import tempfile

    from ffcore.tidy import tables_in

    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        with closing(connect()) as db, db:
            seed_cells(db, 0, [("a", 1, 5.0, 0.8)])
            seed_cells(db, 0, [("b", 2, 3.0, 0.5)])
        assert rows("SELECT player, jornada, pts, p FROM forecast_cell WHERE moment = 0") == [("b", 2, 3.0, 0.5)], \
            "a moment's cells are replaced, not added to"


def _selftest() -> None:
    _selftest_keep()
    _selftest_forecast()
    _selftest_cells()
    print("ffcore.forecast_store self-test OK")


if __name__ == "__main__":
    _selftest()
