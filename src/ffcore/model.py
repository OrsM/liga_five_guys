
from __future__ import annotations

from dataclasses import dataclass


from ffcore.league import League
from ffcore.score import build
from ffcore.tidy import load_market_latest, load_lineups_latest, run_now

__all__ = ["Session", "session", "reset"]


@dataclass
class Session:
    lg: League
    sc: object
    market: list
    xi_rows: list
    hist_label: str = ""
    cur_label: str = ""


_CACHE: list = []


def session() -> Session:
    if not _CACHE:
        market = load_market_latest()
        xi_rows = load_lineups_latest()
        lg = League.load()
        sc, (hist, cur) = build(market, xi_rows, run_now(),
                                shrink_k=lg.cfg.shrink_k if lg else 8.0)
        _CACHE.append(Session(lg, sc, market, xi_rows, hist, cur))
    return _CACHE[0]


def reset() -> None:
    _CACHE.clear()


def _selftest() -> None:
    a, b = session(), session()
    assert a is b
    assert a.lg is b.lg and a.sc is b.sc
    assert a.market and a.xi_rows
    stamps = {r.get("observed_at") for r in a.market}
    assert len(stamps) == 1, stamps
    reset()
    assert session() is not a
    print("ffcore.model self-test OK (6 cases)")


if __name__ == "__main__":
    _selftest()
