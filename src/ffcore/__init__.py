"""
ffcore — the shared bottom of the stack.

Everything here is small and reusable: text keys, number parsing, file and
snapshot access. Modules above it (report.py, squads.py, rivals.py, slate.py,
ledger.py) may import ffcore; ffcore imports none of them.

    from ffcore import norm, money, ratio, pct100
    from ffcore.tidy import Market, read_ledger, ledger_stamp

Scripts reach it via the two-line preamble every script above uses:

    import os, sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ffcore.tidy is imported lazily rather than re-exported here: it touches the
filesystem at import time to resolve FF_ROOT, and ffcore.text / ffcore.parse
must stay usable without a data directory present.

league.py, score.py, bid.py and fixture.py have since moved here too. The
one thing that has NOT is a render module — fmt_money lives in parse.py next
to the parser it inverts, and the table helpers stayed in report.py because
only report.py builds tables.
"""

from ffcore.parse import money, pct100, ratio
from ffcore.text import index_by, norm, resolve, tokens

__all__ = ["norm", "tokens", "resolve", "index_by",
           "money", "ratio", "pct100"]
