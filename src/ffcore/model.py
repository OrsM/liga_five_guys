"""
ffcore/model.py — the model, built once per run.

WHY THIS EXISTS. report.py built a League and a Scorer; decide.load() built
another League and another Scorer; both then described the same squad on two
surfaces. They were not even given the same input — report scored today's
market, decide scored every snapshot ever recorded — so the two could reach
different answers about the same player, and on 2026-08-20 they did: a bug in
the Scorer's name index could only be true for one of them.

Two model passes is not redundancy, it is two models. One of them is wrong
whenever they differ and nothing says which. So there is one, memoised for
the life of the process, and run.py executes every stage in one process.

    from ffcore.model import session
    m = session()
    m.lg, m.sc, m.market, m.xi_rows

The full market history bought the model nothing — measured on 2026-08-20,
of the 70 players owned across the league, zero needed a snapshot older than
today's to resolve. It cost 41,642 rows of lookup building on every call.

What it DID do was put 12 players back in the buyable pool who are not in
the game any more: Ferran Torres last listed 2026-08-14, Nahuel Molina
2026-08-12, and a stale id for Moussa Diarra, who is in today's market under
a different one. A player the market does not list cannot be bought, so
scoring him as a candidate is not extra coverage, it is a wrong answer.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ffcore.league import League          # noqa: E402
from ffcore.score import build            # noqa: E402
from ffcore.tidy import load_market_latest, load_lineups_latest, run_now  # noqa: E402

__all__ = ["Session", "session", "reset"]


@dataclass
class Session:
    """One League, one Scorer, and the rows they were built from."""
    lg: League
    sc: object
    market: list
    xi_rows: list
    hist_label: str = ""
    cur_label: str = ""


_CACHE: list = []


def session() -> Session:
    """The run's model. Built on first ask, handed back after that."""
    if not _CACHE:
        market = load_market_latest()
        xi_rows = load_lineups_latest()
        lg = League.load()
        sc, (hist, cur) = build(market, xi_rows, run_now(),
                                shrink_k=lg.cfg.shrink_k if lg else 8.0)
        _CACHE.append(Session(lg, sc, market, xi_rows, hist, cur))
    return _CACHE[0]


def reset() -> None:
    """Drop the cached model. For self-tests that change the store."""
    _CACHE.clear()


def _selftest() -> None:
    # ONE PASS. Asked twice, the same objects come back — that is the whole
    # guarantee, and it is what stops two surfaces describing two models.
    a, b = session(), session()
    assert a is b
    assert a.lg is b.lg and a.sc is b.sc
    assert a.market and a.xi_rows
    # Built on TODAY's market, not on every snapshot ever recorded.
    stamps = {r.get("observed_at") for r in a.market}
    assert len(stamps) == 1, stamps
    reset()
    assert session() is not a
    print("ffcore.model self-test OK (6 cases)")


if __name__ == "__main__":
    _selftest()
