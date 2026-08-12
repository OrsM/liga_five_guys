"""
ffcore — the shared bottom of the stack.

Everything in here is pure: no file paths, no network, no reports. Modules
above it (common.py, report.py, squads.py, offers.py, bids.py, watch.py,
find_slug.py, rivals.py) may import ffcore; ffcore imports none of them.

    from ffcore import norm, money, ratio, pct100

Scripts reach it via the two-line preamble that squads.py and watch.py
already use:

    import os, sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

Still to move here, in this order: tidy.py (read_csv, latest_only,
parse_stamp, input_path, value_at), league.py (roster replay, cash),
score.py (shrunk ppm x P(start)), render.py (fmt_money, tables).
"""

from ffcore.parse import euros, money, pct100, ratio
from ffcore.text import fold, index_by, norm, resolve, tokens

__all__ = ["norm", "fold", "tokens", "resolve", "index_by",
           "money", "euros", "ratio", "pct100"]
