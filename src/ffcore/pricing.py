"""
ffcore/pricing.py — what a move costs beyond its sticker price, and what
a rival can answer back with.

Split out of decide.py 2026-09-15 (rationalization plan session 4): four
functions with no calls into anything else in decide.py — pure data
lookups and arithmetic on an Action/Universe, not decision logic. Kept
here rather than folded into candidates()/rank() (which still live in
decide.py, tangled with current_xi()/player_forecasts() in a way that
would circular-import this module) specifically because they don't need
any of that — a clean, checkable seam.
"""

from __future__ import annotations


def locked(until: dict, key: str, now) -> bool:
    """Is his clause unpayable right now?

    A TRANSFER LOCKS A CLAUSE until the app's own reopen date. A MISSING
    DATE COUNTS AS LOCKED, not available — an absent field is not
    evidence a clause is payable. A free agent has no clause and never
    reaches here.
    """
    when = until.get(key)
    return True if when is None else when > now


def burn(u, a) -> float | None:
    """Wealth a move destroys: what it costs, less what you end up holding.

    A FREE AGENT BURNS NOTHING — market value in, market value out. A
    BUYOUT CLAUSE BURNS THE PREMIUM over market value, gone for good.
    Never negative; None when the value is unknown (never assume zero
    premium).
    """
    if not a.buy:
        return 0.0
    val = u.value_view.get(a.buy)
    if val is None:
        return None
    return max(0.0, a.cost - val)


def cash_price(reach) -> float | None:
    """Places per million, measured off `reach` = [(extra cash needed, Δpos)].

    Read off what more money would actually buy today, not a rate card —
    every target is screened, not only the affordable ones. The curve is
    a STAIRCASE (flat until the balance clears the next better price,
    then a step), so averaged over the reachable range it comes out
    small; that's the honest answer, not a defect.

    None when there is nothing to measure against. Never zero for that —
    zero is a real answer ("more money buys nothing"), distinct from
    unknown.
    """
    pts = sorted((max(0.0, c), d) for c, d in reach)
    if len(pts) < 2 or pts[-1][0] <= 0:
        return None
    best_now = max((d for c, d in pts if c <= 0.0), default=None)
    if best_now is None:
        return None
    best_any = max(d for _, d in pts)
    span = max(c for c, _ in pts) / 1e6
    return max(0.0, (best_any - best_now) / span) if span else None


def respond(u, a, rate: float | None) -> float:
    """Season points the manager you just paid buys back, on AVERAGE, or 0.0.

    A CLAUSE PAYS THE OWNER, not the app — a steal hands the victim cash
    he can reinvest, so scoring the steal without crediting that
    overstates it.

    THE AVERAGE HE BUYS, NOT THE BEST HE COULD FIND — `rate` is
    `rank()`'s own `lam` (points per million, same screening pass
    pricing everything else this run), never a search for the single
    best clausable replacement: an unbounded search can walk off with a
    THIRD manager's own player to fund the response, breaking a squad
    that was never part of the trade. An average touches only the two
    sides of the actual trade, mirroring `value_rate()`'s own
    replacement-level logic on the buy side.

    0.0 when there is nothing to spend (no victim, or a market purchase
    — money paid to the app leaves the league, money paid for a clause
    changes sides) or nothing is known to spend it at (`rate` is None).
    """
    if not a.victim or a.victim == u.me or not rate:
        return 0.0
    budget = max(0.0, u.rival_cash.get(a.victim, 0.0)) + a.cost
    return rate * budget / 1e6


def _selftest() -> None:
    from dataclasses import dataclass, field

    @dataclass
    class _FakeAction:
        kind: str = "buy"
        buy: str = ""
        sell: tuple = ()
        cost: float = 0.0
        victim: str = ""

    @dataclass
    class _FakeUniverse:
        value_view: dict = field(default_factory=dict)
        rival_cash: dict = field(default_factory=dict)
        me: str = "me"

    # -- locked(): a missing or None date counts as locked, not available --
    import datetime as dt
    now = dt.datetime(2026, 8, 18, tzinfo=dt.timezone.utc)
    soon = dt.datetime(2026, 8, 24, tzinfo=dt.timezone.utc)
    past = dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc)
    assert locked({"x": soon}, "x", now) is True
    assert locked({"x": past}, "x", now) is False
    assert locked({}, "x", now) is True
    assert locked({"x": None}, "x", now) is True

    # -- burn(): a clause burns the premium, a free agent burns nothing ----
    u = _FakeUniverse(value_view={"star": 5e6, "free": 4e6})
    assert burn(u, _FakeAction(buy="star", cost=8e6)) == 3e6
    assert burn(u, _FakeAction(buy="free", cost=4e6)) == 0.0
    assert burn(u, _FakeAction(buy="free", cost=3e6)) == 0.0  # a bargain, not negative
    assert burn(u, _FakeAction(sell=("bench",))) == 0.0       # a sale burns nothing
    assert burn(u, _FakeAction(buy="mystery", cost=9e6)) is None  # unknown != zero

    # -- cash_price(): the staircase, flat until it steps --------------------
    flat = [(0.0, 0.40), (5e6, 0.30), (12e6, 0.20)]
    assert cash_price(flat) == 0.0
    step = [(0.0, 0.40), (10e6, 0.50)]
    assert abs(cash_price(step) - 0.10 / 10.0) < 1e-12
    assert cash_price([]) is None and cash_price([(0.0, 0.4)]) is None

    # -- respond(): the average he buys, not the best he could find --------
    u2 = _FakeUniverse(rival_cash={"riv": 4e6})
    steal = _FakeAction(kind="steal", buy="x", cost=10e6, victim="riv")
    assert respond(u2, steal, 3.0) == 3.0 * (4e6 + 10e6) / 1e6
    assert respond(u2, _FakeAction(buy="star", cost=1e6), 3.0) == 0.0  # no victim
    assert respond(u2, steal, None) == 0.0                             # no known rate
    broke = _FakeUniverse(rival_cash={"riv": 0.0})
    assert respond(broke, _FakeAction(kind="steal", buy="x", cost=0.0,
                                      victim="riv"), 3.0) == 0.0

    print("ffcore.pricing self-test OK (17 cases)")


if __name__ == "__main__":
    _selftest()
