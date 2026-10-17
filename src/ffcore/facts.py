"""Facts that never change once made, kept so a question is a lookup.

A past day's prices are settled the moment the next day's first snapshot
arrives, so each player's daily value, his change since the day before and
his place in his series are written once (price_step) and read thereafter;
only the day of the cut is worked out on the spot (schema.sql: price_rows).
ensure() is what keeps them current: connect() calls it, it costs a lookup
when nothing is new, and a rebuild (~1s, about once a day) when a day has
closed or the SQL below has changed.
"""
from __future__ import annotations

import sqlite3
import zlib
from collections.abc import Callable

__all__ = ["ensure"]

# Each player's valued days (each day's last snapshot), numbered (n); the
# change in % on his previous valued day (NULL for the first, or after a
# value of 0); the number of changes so far (i) and the two before it (s1, s2),
# as price_series counts them. Closed days only: those before the latest day with a snapshot.
PRICE_STEP = """
DELETE FROM price_step;
INSERT INTO price_step (ff_id, n, day, observed_at, value, step, i, s1, s2)
WITH base AS (
    SELECT ff_id, observed_at, value, row_number() OVER w - 1 AS n,
           CASE WHEN lag(value) OVER w > 0 THEN 100 * (value / lag(value) OVER w - 1) END AS step
    FROM (SELECT ff_id, observed_at, value, rowid AS id FROM market
          WHERE observed_at IN (SELECT max(observed_at) FROM market GROUP BY substr(observed_at, 1, 10))
            AND observed_at < (SELECT substr(max(observed_at), 1, 10) FROM market)
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
# the longest any cut can use (price_hmax only grows, by at most one a day). Worked out with the
# clock put past the last day, so every closed day is in.
MOMENTUM_DAY = """
DELETE FROM momentum_day WHERE ckey = (SELECT ckey FROM k);
INSERT INTO momentum_day
SELECT (SELECT ckey FROM k), h, end_day, sum(x * y), sum(x * x) FROM momentum_pairs GROUP BY h, end_day
"""
FAR = "9999-12-31T2359Z"
PAD = 2

FACTS = {"price_step": PRICE_STEP}


def ensure(db: sqlite3.Connection, now: Callable[[], str] | None = None) -> None:
    """Bring each kept fact up to the data: nothing when it is current. now
    is the connection's clock, which is put back after the one fact that
    needs it pinned (momentum_day)."""
    if db.execute("SELECT 1 FROM sqlite_master WHERE name = 'facts_meta'").fetchone() is None:
        return                                  # a schema without them (a test's own)
    identity(db)
    latest = db.execute("SELECT substr(max(observed_at), 1, 10) FROM market").fetchone()[0] or ""
    _price_step(db, latest)
    if now is not None:
        _momentum_day(db, latest, now)


def identity(db: sqlite3.Connection) -> None:
    """ff_identity up to the registry and the rows it is read for: refilled
    when either changed (the crosswalk mirror calls it too)."""
    if db.execute("SELECT count(*) FROM sqlite_master WHERE name IN ('ff_identity_kept', 'facts_meta')"
                  ).fetchone()[0] < 2:
        return
    rows = db.execute("SELECT player_id, name, club_id, ff_slug, app_id FROM crosswalk ORDER BY 1").fetchall()
    through = "%08x:%d:%d" % (
        zlib.crc32(repr(rows).encode()),
        db.execute("SELECT coalesce(max(rowid), 0) FROM lineups").fetchone()[0],
        db.execute("SELECT coalesce(max(rowid), 0) FROM starters").fetchone()[0])
    version = "%08x" % zlib.crc32(db.execute(
        "SELECT sql FROM sqlite_master WHERE name = 'ff_identity_now'").fetchone()[0].encode())
    if db.execute("SELECT version, through FROM facts_meta WHERE name = 'ff_identity'").fetchone() == (version, through):
        return
    own = not db.in_transaction                 # else the caller's transaction holds it
    if own:
        db.execute("BEGIN IMMEDIATE")
    try:
        db.execute("DELETE FROM ff_identity_kept")
        db.execute("INSERT INTO ff_identity_kept SELECT * FROM ff_identity_now")
        db.execute("INSERT OR REPLACE INTO facts_meta VALUES ('ff_identity', ?, ?)", (version, through))
        if own:
            db.execute("COMMIT")
    except BaseException:
        if own:
            db.execute("ROLLBACK")
        raise


def _momentum_day(db: sqlite3.Connection, latest: str, now: Callable[[], str]) -> None:
    ckey = db.execute("SELECT ckey FROM k").fetchone()[0]
    if ckey is None or not latest:
        return                                  # no constants yet, or nothing to fit
    db.create_function("cut", 0, lambda: FAR)
    db.create_function("hpad", 0, lambda: PAD)      # a cut a day on can reach one horizon further
    try:
        name, version, through = "momentum_day:" + ckey, "%08x" % zlib.crc32((MOMENTUM_DAY + str(PAD)).encode()), latest
        if db.execute("SELECT version, through FROM facts_meta WHERE name = ?", (name,)).fetchone() == (version, through):
            return
        db.execute("BEGIN IMMEDIATE")
        try:
            if db.execute("SELECT version, through FROM facts_meta WHERE name = ?", (name,)).fetchone() != (version, through):
                for statement in MOMENTUM_DAY.split(";\n"):
                    if statement.strip():
                        db.execute(statement)
                db.execute("INSERT OR REPLACE INTO facts_meta VALUES (?, ?, ?)", (name, version, through))
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise
    finally:
        db.create_function("cut", 0, now)
        db.create_function("hpad", 0, lambda: 0)


def _price_step(db: sqlite3.Connection, latest: str) -> None:
    for name, sql in FACTS.items():
        version = "%08x" % zlib.crc32(sql.encode())
        got = db.execute("SELECT version, through FROM facts_meta WHERE name = ?", (name,)).fetchone()
        if got == (version, latest):
            continue
        db.execute("BEGIN IMMEDIATE")
        try:
            got = db.execute("SELECT version, through FROM facts_meta WHERE name = ?", (name,)).fetchone()
            if got != (version, latest):                  # another process may have done it
                for statement in sql.split(";\n"):
                    if statement.strip():
                        db.execute(statement)
                db.execute("INSERT OR REPLACE INTO facts_meta VALUES (?, ?, ?)", (name, version, latest))
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise


def _selftest() -> None:
    import tempfile
    from contextlib import closing
    from pathlib import Path

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
        def who() -> list[tuple[object, ...]]:
            with closing(connect()) as db:
                return db.execute("SELECT player_slug, player FROM ff_identity ORDER BY 1").fetchall()
        assert who() == []
        store_sql("lineups", [{"observed_at": "2026-08-10T0800Z", "source": "futbolfantasy",
                               "player_slug": "x", "player_name": "X", "team_slug": "t"}])
        with closing(connect()) as db, db:
            db.execute("INSERT INTO crosswalk (player_id, name, club_id, ff_slug, app_id) VALUES ('p1', 'X', 't', 'x', '')")
        assert who() == [("x", "p1")], "a new line-up row and a new registry are named at the next connect"
        with closing(connect()) as db, db:
            db.execute("UPDATE crosswalk SET ff_slug = 'y', name = 'Z'")
            identity(db)
        assert who() == [("x", None)], "a changed registry refills it in the caller's transaction"
    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        from ffcore.db import record, rows
        record([{"name": n, "value": v} for n, v in (("CLIP", 20.0), ("PRICE_W0", .4), ("PRICE_W1", .4), ("PRICE_W2", .2))],
               "model_constants")
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

            from ffcore.clock import set_now
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

            from ffcore.clock import set_now
            try:
                set_now(datetime.strptime(cut, "%Y-%m-%dT%H%MZ").replace(tzinfo=timezone.utc))
                return [tuple(r) for r in rows("SELECT ff_id, i, round(x, 9), value FROM price_series ORDER BY ff_id, i")]
            finally:
                set_now(None)
        assert series_at("2026-08-17T2100Z") == series_at("2026-08-29T0000Z") != [], \
            "a cut days after the data ends sees the last day, as it stood"
        assert series_at("2026-08-17T0900Z") != series_at("2026-08-17T2100Z"), "and a cut within a day sees that day so far (the morning's snapshot)"
        record([{"variant": "wide", "name": n, "value": v} for n, v in (("CLIP", 5.0), ("PRICE_W0", 1.0), ("PRICE_W1", 0.0), ("PRICE_W2", 0.0))],
               "model_constants")
        from ffcore.clock import set_variant
        try:
            set_variant("wide")
            with closing(connect()) as db:
                assert db.execute("SELECT count(DISTINCT ckey) FROM momentum_day").fetchone()[0] == 2, "each set of constants its own days"
            assert dict(rows("SELECT h, slope FROM momentum")).keys() == dict(rows(
                "SELECT h, sum(x * y) / sum(x * x) FROM momentum_pairs GROUP BY h HAVING sum(x * x) <> 0")).keys()
        finally:
            set_variant("live")
    print("ffcore.facts self-test OK")


if __name__ == "__main__":
    _selftest()
