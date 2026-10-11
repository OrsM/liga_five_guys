"""cut() for Datasette's connections: the as-of views (current_*, history_*,
and what reads them) are read as of now. Not ffcore.clock.cut, which keeps
the first time it is asked for the life of the process."""
import sys
from datetime import datetime, timezone
from pathlib import Path

from datasette import hookimpl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from ffcore.parse import stamp  # noqa: E402


@hookimpl
def prepare_connection(conn):  # type: ignore[no-untyped-def]
    conn.create_function("cut", 0, lambda: stamp(datetime.now(timezone.utc)))
