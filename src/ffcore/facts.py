"""Keeping what never changes once made, so a question is a lookup.

ensure() is what keeps each module's kept facts current: connect() calls it, it costs a
lookup when nothing is new, and a rebuild when a day has closed or
the SQL that makes them has changed. It reads nothing itself: each module keeps its own
(ingest: ff_identity_kept, crosswalk.identity; forecast: price_step and momentum_day,
forecast_store.keep), under the one record, facts_meta.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Callable

__all__ = ["ensure"]


def ensure(db: sqlite3.Connection, now: Callable[[], str]) -> None:
    """Bring each module's kept facts up to the data: nothing when current. now is the
    connection's clock, which keep puts back after the one fact that needs it pinned."""
    if db.execute("SELECT 1 FROM sqlite_master WHERE name = 'facts_meta'").fetchone() is None:
        return                                  # a schema without them (a test's own)
    import crosswalk                            # not at the top: both import ffcore.db, which imports this
    from ffcore import forecast_store
    crosswalk.identity(db)
    forecast_store.keep(db, now)
