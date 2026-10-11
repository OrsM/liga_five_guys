"""The functions the views call, for Datasette's connections (ffcore.db.
functions): cut() is now, read afresh each time, not ffcore.clock.cut,
which keeps the first time it is asked for the life of the process."""
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
