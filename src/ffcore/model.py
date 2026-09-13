"""
ffcore/model.py — one League and one Scorer, built once per run, built
from TODAY's market only (not the full snapshot history — an older
snapshot can list a player no longer in the game as buyable).

    from ffcore.model import session
    m = session()
    m.lg, m.sc, m.market, m.xi_rows

Memoised for the life of the process so every stage describes the same
squad — two independent builds could (and once did) disagree about the
same player with nothing to say which one was right.
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
