"""
ffcore — the shared bottom of the stack.

Everything here is small and reusable: text keys, number parsing, file and
snapshot access. Modules above it (common.py, report.py, squads.py,
offers.py, watch.py, find_slug.py, rivals.py) may import ffcore;
ffcore imports none of them.

    from ffcore import norm, money, ratio, pct100
    from ffcore.tidy import Market, read_ledger, ledger_stamp

Scripts reach it via the two-line preamble that squads.py and watch.py
already use:

    import os, sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ffcore.tidy is imported lazily rather than re-exported here: it touches the
filesystem at import time to resolve FF_ROOT, and ffcore.text / ffcore.parse
must stay usable without a data directory present.

Still to move here, in this order: league.py (roster replay, cash),
score.py (shrunk ppm x P(start)), render.py (fmt_money, table helpers).
"""

from ffcore.parse import euros, money, pct100, ratio
from ffcore.text import fold, index_by, norm, resolve, tokens

__all__ = ["norm", "fold", "tokens", "resolve", "index_by",
           "money", "euros", "ratio", "pct100"]
