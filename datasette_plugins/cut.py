"""The functions the views call, for Datasette's connections (ffcore.db.
functions). The views are read as of t, look's cut: the last run's."""
import sys
from pathlib import Path

from datasette import hookimpl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from ffcore.db import functions  # noqa: E402


@hookimpl
def prepare_connection(conn):  # type: ignore[no-untyped-def]
    functions(conn)
