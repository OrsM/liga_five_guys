"""
ffcore — the shared bottom of the stack: text keys, number parsing, file
and snapshot access. Modules above it may import ffcore; ffcore imports
none of them.

    from ffcore import norm, money, ratio, pct100
    from ffcore.tidy import Market, read_ledger, ledger_stamp

Scripts reach it via:

    import os, sys

ffcore.tidy is imported lazily, not re-exported here: it touches the
filesystem at import time to resolve FF_ROOT, and ffcore.text/parse must
stay usable without a data directory present.
"""

from ffcore.parse import money, pct100, ratio
from ffcore.text import index_by, norm, resolve, tokens

__all__ = ["norm", "tokens", "resolve", "index_by",
           "money", "ratio", "pct100"]
