"""The functions the views call, for Datasette's connections (ffcore.db.
functions): cut() is now, read afresh each time (this process's own t, not
the run's), and the moment and you the last run looked at (sim_runs,
league_config), for the views that read them (look)."""
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from datasette import hookimpl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from ffcore.db import functions  # noqa: E402
from ffcore.parse import stamp  # noqa: E402


@hookimpl
def prepare_connection(conn):  # type: ignore[no-untyped-def]
    functions(conn, now=lambda: stamp(datetime.now(timezone.utc)))
    try:
        moment, me = conn.execute("SELECT (SELECT moment FROM sim_runs ORDER BY run_id DESC LIMIT 1),"
                                  " (SELECT me FROM league_config)").fetchone()
    except sqlite3.Error:       # a file without them yet
        return
    conn.create_function("moment", 0, lambda: moment or 0)
    conn.create_function("me", 0, lambda: me or "")
