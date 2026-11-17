"""The forecast module's store and what it keeps (docs/notes/lfg.md, "Architecture"):
the moments forecast() writes, the price momentum read for a decision's odds, and the closed
days of the price series kept so a question is a lookup (price_step, momentum_day; keep())."""
from __future__ import annotations

import sqlite3
import zlib
from collections.abc import Callable, Sequence
from contextlib import closing
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
