
from __future__ import annotations


def locked(until: dict, key: str, now) -> bool:
    when = until.get(key)
    return True if when is None else when > now


def burn(u, a) -> float | None:
    if not a.buy:
        return 0.0
    val = u.view("value").get(a.buy)
    if val is None:
        return None
    return max(0.0, a.cost - val)


def cash_price(reach) -> float | None:
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
        values: dict = field(default_factory=dict)
        rival_cash: dict = field(default_factory=dict)
        me: str = "me"

        def view(self, field_name):
            return self.values if field_name == "value" else {}

    import datetime as dt
    now = dt.datetime(2026, 8, 18, tzinfo=dt.timezone.utc)
    soon = dt.datetime(2026, 8, 24, tzinfo=dt.timezone.utc)
    past = dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc)
    assert locked({"x": soon}, "x", now) is True
    assert locked({"x": past}, "x", now) is False
    assert locked({}, "x", now) is True
    assert locked({"x": None}, "x", now) is True

    u = _FakeUniverse(values={"star": 5e6, "free": 4e6})
    assert burn(u, _FakeAction(buy="star", cost=8e6)) == 3e6
    assert burn(u, _FakeAction(buy="free", cost=4e6)) == 0.0
    assert burn(u, _FakeAction(buy="free", cost=3e6)) == 0.0
    assert burn(u, _FakeAction(sell=("bench",))) == 0.0
    assert burn(u, _FakeAction(buy="mystery", cost=9e6)) is None

    flat = [(0.0, 0.40), (5e6, 0.30), (12e6, 0.20)]
    assert cash_price(flat) == 0.0
    step = [(0.0, 0.40), (10e6, 0.50)]
    assert abs(cash_price(step) - 0.10 / 10.0) < 1e-12
    assert cash_price([]) is None and cash_price([(0.0, 0.4)]) is None

    u2 = _FakeUniverse(rival_cash={"riv": 4e6})
    steal = _FakeAction(kind="steal", buy="x", cost=10e6, victim="riv")
    assert respond(u2, steal, 3.0) == 3.0 * (4e6 + 10e6) / 1e6
    assert respond(u2, _FakeAction(buy="star", cost=1e6), 3.0) == 0.0
    assert respond(u2, steal, None) == 0.0
    broke = _FakeUniverse(rival_cash={"riv": 0.0})
    assert respond(broke, _FakeAction(kind="steal", buy="x", cost=0.0,
                                      victim="riv"), 3.0) == 0.0

    print("ffcore.pricing self-test OK (17 cases)")


if __name__ == "__main__":
    _selftest()
