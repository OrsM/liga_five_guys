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

__all__ = ["keep", "forecast", "pin", "price_momentum", "price_carry", "price_change", "seed_cells", "Prices",
           "market_offer_ratios", "club_ratings", "ClubRating"]

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
SELECT (SELECT ckey FROM k), h, end_day, coalesce(sum(x * y), 0), sum(x * x),
       coalesce(sum(CASE WHEN focus THEN x * y END), 0), coalesce(sum(CASE WHEN focus THEN x * x END), 0),
       coalesce(sum(CASE WHEN focus THEN y * y END), 0), coalesce(sum(focus), 0)
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
    """price_step and momentum_day up to the data: nothing when current. The connection's clock
    (now) is put back after."""
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


def pin(moment: int) -> None:
    """The moment kept: a later forecast of the same cut and variant does not drop it (a run's board points to it)."""
    with closing(connect()) as db, db:
        db.execute("UPDATE forecast_moment SET pinned = 1 WHERE moment = ?", (moment, ))


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


def price_carry() -> tuple[float, ...]:
    """The share of a trend made after each update, by horizon (view: price_carry)."""
    return tuple(v for (v,) in rows("SELECT carry FROM price_carry ORDER BY h"))


def market_offer_ratios() -> list[float]:
    """The game's nightly offers over value, each once, by when made."""
    return [r for (r,) in rows("SELECT ratio FROM offer_ratios")]


class ClubRating(NamedTuple):
    club: str
    attack: float
    defence: float


def club_ratings() -> list[ClubRating]:
    """Each club's attack and defence at t (view: club_rating)."""
    return [ClubRating(*r) for r in rows("SELECT club, attack, defence FROM club_rating ORDER BY club")]


def price_momentum() -> Prices:
    """Momentum and the price trend at t (views: momentum, price_carry,
    price_trend), read."""
    slope: dict[int, float] = dict(rows("SELECT h, slope FROM momentum ORDER BY h"))
    return Prices(slope, price_carry(),
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
            store_sql("points", [{"observed_at": seen, "season": "2025-26", "ff_id": "fwd", "points": 150.0, "games": 30.0},
                                 {"observed_at": seen, "season": "2026-27", "ff_id": "fwd", "points": 0.0, "games": 0.0}])

            mu = 2.0
            rating = {c: (a, d) for c, _n, a, d in rows("SELECT club, n, attack, defence FROM club_rating")}
            k = model.FIXTURE_K
            assert abs(rating["a"][0] - (30 + k * mu) / ((10 + k) * mu)) < 1e-12 \
                and abs(rating["b"][1] - (30 + k * mu) / ((10 + k) * mu)) < 1e-12, \
                "goals for and against a match over the league's, pulled toward it by FIXTURE_K matches"
            assert set(rating) == {"a", "b"}, "a club with no results has no rating"

            prior = dict(rows("SELECT player, prior FROM ppm_prior"))
            rate = {p: (ppm, pk, rec) for p, ppm, pk, rec in rows("SELECT player, ppm, picked, record FROM forecast_rate")}
            sk, pk = model.SHRINK_K, model.PICK_K
            assert abs(rate["fwd"][0] - shrink(shrink(prior["fwd"], sk, 150.0, 30.0), sk, 0.0, 0.0)) < 1e-12, \
                "his value's prior, pulled toward last season's 5 a match"
            assert rate["hurt"][0] == prior["hurt"], "no record at all: his value's"
            assert rate["fwd"][1] == 0.8 and rate["back"][1] == model.NEUTRAL_START / 100, \
                "his listing's start %; listed with none, NEUTRAL_START"
            assert (rate["hurt"][1], rate["susp"][1], rate["dbt"][1]) == (0.0, 0.0, 0.5), \
                "his listing's start %, injured, suspended or doubtful or not"
            assert rate["cee"][1] == model.ABSENT_START / 100 and pk > 0, "not listed: ABSENT_START"

            cells = {(p, j): r for p, j, *r in rows("SELECT player, jornada, pts, p, ppm, fixture, picked, fit"
                                                     " FROM forecast_now")}
            assert set(cells) == {(p, j) for p in ("fwd", "back", "hurt", "susp", "dbt", "gone") for j in (2, 3)} \
                | {("cee", 3)}, "every jornada left his club plays; a coach, a player with no club: none"
            says = {"fwd": 0.8, "hurt": 0.0, "susp": 0.0, "dbt": 0.5}      # the listed start %, whatever his status
            club_start = {"a": 2, "b": 2, "c": 3}
            club = {"fwd": "a", "back": "a", "hurt": "b", "susp": "b", "dbt": "b", "gone": "b", "cee": "c"}
            for (p, j), (pts, pr, ppm, fix, picked, fit) in cells.items():
                assert abs(pts - max(0.0, ppm * fix)) < 1e-12, "points if he plays = ppm x fixture"
                w = model.P_LISTING * model.P_DECAY ** (j - club_start[club[p]])
                q = w * rate[p][1] * (1.0 if p in says else fit) + (1 - w) * rate[p][2] * fit
                assert abs(pr - (q + model.P_LIFT * q * (1 - q))) < 1e-12, \
                    "the chance he plays is his listing's start % (or picked) blended with his record, the listing's share" \
                    " P_LISTING falling by P_DECAY a jornada, his record x fit, lifted by P_LIFT"
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
            pin(first)
            forecast()
            third = forecast()
            assert [m for m, in rows("SELECT moment FROM forecast_moment ORDER BY moment")] \
                == [first, third], "made again each time; the cut's earlier ones go but a run's, its record"
            assert rows("SELECT count(*) FROM forecast_cell WHERE moment NOT IN (SELECT moment FROM forecast_moment)") \
                == [(0, )]
        finally:
            set_now(None)


def _selftest_prior() -> None:
    """schema.sql's last_season and ppm_prior on points, player_club
    and player_value as tables: in his position, the least-squares line of
    last season's points per match (10+ matches) on log value, at his value;
    a position with fewer than three, its mean; no position, everyone's
    median."""
    import math
    import sqlite3
    from ffcore.db import SCHEMA, functions

    db = sqlite3.connect(":memory:")
    functions(db)
    db.execute("CREATE TABLE src_points (observed_at, season, ff_id, points, games)")
    db.execute("CREATE TABLE player_club (player, pos)")
    db.execute("CREATE TABLE player_value (player, value)")
    db.executemany("INSERT INTO src_points VALUES ('2026-01-01T0000Z', ?, ?, ?, ?)", [
        ("2024-25", "a", 999.0, 30.0),                        # an older season: not read
        ("2025-26", "a", 60.0, 30.0), ("2025-26", "b", 90.0, 30.0),
        ("2025-26", "c", 150.0, 30.0), ("2025-26", "d", 210.0, 30.0),
        ("2025-26", "e", 300.0, 9.0), ("2025-26", "f", 40.0, 10.0),
        ("2026-27", "a", 5.0, 1.0)])                          # the season under way: not last season
    market = [("a", "DEF", 1e6), ("b", "DEF", 2e6), ("c", "DEF", 50e6), ("d", "DEF", 60e6),
              ("e", "DEF", 70e6), ("f", "POR", 3e6), ("x", "", 5e6), ("z", "DEF", None)]
    db.executemany("INSERT INTO player_club VALUES (?, ?)", [(k, p) for k, p, _v in market])
    db.executemany("INSERT INTO player_value VALUES (?, ?)", [(k, v) for k, _p, v in market if v])
    sql = SCHEMA.read_text()
    for view in ("last_season", "ppm_prior"):
        i = sql.index("CREATE VIEW %s AS" % view)
        db.execute(sql[i:sql.index(";\n", i)])
    got = dict(db.execute("SELECT player, prior FROM ppm_prior"))
    xs, ys = [math.log(v) for v in (1e6, 2e6, 50e6, 60e6)], [2.0, 3.0, 5.0, 7.0]
    mx, my = sum(xs) / 4, sum(ys) / 4
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
    line = {k: my + slope * (math.log(v) - mx) for k, v in (("a", 1e6), ("d", 60e6), ("e", 70e6))}
    assert all(abs(got[k] - line[k]) < 1e-9 for k in line), \
        ("DEF: the line on log value through a-d (e's 9 matches are too few), at each one's value", got, line)
    assert got["f"] == 4.0, "POR: one player, his mean"
    assert got["x"] == 4.0, "no position: the median of everyone's (2, 3, 4, 5, 7)"
    assert got["z"] == my, "no value: his position's mean"


def _selftest_points() -> None:
    """schema.sql's scored and season_record: each change in a player's
    season total by the jornada of his club's last match by then, and his
    season so far (points, appearances, squads, pick)."""
    import tempfile
    from ffcore import forecast as model
    from ffcore.db import record

    from ffcore.db import set_now
    from ffcore.parse import snapshot_stamp
    from ffcore.tidy import store_sql, tables_in

    a, b, later = "2026-08-01T0900Z", "2026-08-02T0900Z", "2026-08-03T0900Z"
    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        try:
            set_now(snapshot_stamp("2026-08-04T0000Z"))
            store_sql("market", [
                {"observed_at": a, "ff_id": "1", "club": "x"},
                {"observed_at": a, "ff_id": "2", "club": "y"},
                {"observed_at": a, "ff_id": "3", "club": "w"}, {"observed_at": a, "ff_id": "4", "club": "w"}])
            store_sql("matches", [
                {"observed_at": a, "match_id": "m1", "jornada": "1", "home": "x",
                 "away": "y", "score": "", "kickoff": "2026-08-01T10:00:00+00:00"},
                {"observed_at": later, "match_id": "m2", "jornada": "2",
                 "home": "x", "away": "z", "score": "1-0", "kickoff": ""},
                {"observed_at": a, "match_id": "m3", "jornada": "1", "home": "w",
                 "away": "v", "score": "", "kickoff": "2026-08-01T10:00:00+00:00"}])
            store_sql("points", [
                {"observed_at": a, "season": "2025-26", "ff_id": "1",
                 "points": "90", "games": "30"},
                *({"observed_at": a, "season": "2026-27", "ff_id": k,
                   "points": "0", "games": "0"} for k in "12"),
                # the first fetch of the season is the season so far: one game is that game's (3 played jornada 1),
                # more is no one jornada's (4 started the tool mid-season)
                {"observed_at": a, "season": "2026-27", "ff_id": "3", "points": "4", "games": "1"},
                {"observed_at": a, "season": "2026-27", "ff_id": "4", "points": "60", "games": "20"},
                {"observed_at": b, "season": "2026-27", "ff_id": "1",
                 "points": "5", "games": "1"},
                {"observed_at": later, "season": "2026-27", "ff_id": "1",
                 "points": "12", "games": "2"},
                {"observed_at": later, "season": "2026-27", "ff_id": "2",
                 "points": "3", "games": "1"}])
            assert set(rows("SELECT player, jornada, pts, at FROM scored")) == {
                ("1", 1, 5.0, b), ("1", 2, 7.0, later), ("2", 1, 3.0, later), ("3", 1, 4.0, a)}
            record([{"player_id": k, "ff_slug": slug} for k, slug in
                    [("1", "uno"), ("2", "dos"), ("3", "tres")]], "crosswalk")
            store_sql("starters", [
                {"observed_at": a, "match_id": "m1", "team_slug": "x", "player_slug": p,
                 "player_name": n, "role": r, "minute": m}
                for p, n, r, m in [("uno", "1", "starter", ""), ("dos", "2", "sub", "70"),
                                   ("tres", "3", "sub", ""), ("uno-coach", "1", "coach", "")]] + [
                {"observed_at": later, "match_id": "m2", "team_slug": "x", "player_slug": "uno",
                 "player_name": "1", "role": "starter", "minute": "45"}])
            assert set(rows("SELECT player, jornada FROM appearances")) == {("1", 1), ("1", 2), ("2", 1)}, \
                "started or came on, not an unused sub"
            got = {k: r for k, *r in rows("SELECT player, pts, apps, squads, pick FROM season_record")}
            assert {k: tuple(r[:3]) for k, r in got.items()} == {"1": (12.0, 2.0, 2.0), "2": (3.0, 1.0, 1.0), "3": (4.0, 0.0, 1.0)}, \
                "a starter the match unless he came off, a sub from when he came on"
            assert abs(got["1"][3] - (model.PICK_K * model.NEUTRAL_START / 100 + 2) / (model.PICK_K + 2)) < 1e-12, \
                "his record pulled toward NEUTRAL_START, worth PICK_K matches"
            # a listed start %, unflagged, is the chance he is picked as it says, whatever his record (he has started both)
            store_sql("lineups", [{"observed_at": later, "source": "futbolfantasy", "team_slug": "x", "player_slug": "uno",
                                   "player_name": "1", "start_pct": "10", "status": "ok", "note": ""}])
            assert dict(rows("SELECT player, picked FROM forecast_rate")).get("1") == 0.10, \
                "the listing's start %, not pulled toward his record of selection"
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
    _selftest_prior()
    _selftest_points()
    print("ffcore.forecast_store self-test OK")


if __name__ == "__main__":
    _selftest()
