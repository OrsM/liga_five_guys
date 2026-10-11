"""cut() for Datasette's connections: the as-of views (current_*, history_*,
and what reads them) are read as of now, as a run without LFG_NOW reads
them (ffcore.clock.cut)."""
from datetime import datetime, timezone

from datasette import hookimpl


@hookimpl
def prepare_connection(conn):  # type: ignore[no-untyped-def]
    conn.create_function(
        "cut", 0, lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%MZ"))
