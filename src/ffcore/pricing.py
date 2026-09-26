
from __future__ import annotations


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
    from ffcore.action import Action
    from ffcore.fixtures import players_from_flat, tiny_universe

    u = tiny_universe(players=players_from_flat(value={"star": 5e6, "free": 4e6}))
    assert burn(u, Action("buy", buy="star", cost=8e6)) == 3e6
    assert burn(u, Action("buy", buy="free", cost=4e6)) == 0.0
    assert burn(u, Action("buy", buy="free", cost=3e6)) == 0.0
    assert burn(u, Action("sell", sell=("bench",))) == 0.0
    assert burn(u, Action("buy", buy="mystery", cost=9e6)) is None

    flat = [(0.0, 0.40), (5e6, 0.30), (12e6, 0.20)]
    assert cash_price(flat) == 0.0
    step = [(0.0, 0.40), (10e6, 0.50)]
    assert abs(cash_price(step) - 0.10 / 10.0) < 1e-12
    assert cash_price([]) is None and cash_price([(0.0, 0.4)]) is None

    u2 = tiny_universe(rival_cash={"riv": 4e6})
    steal = Action("steal", buy="x", cost=10e6, victim="riv")
    assert respond(u2, steal, 3.0) == 3.0 * (4e6 + 10e6) / 1e6
    assert respond(u2, Action("buy", buy="star", cost=1e6), 3.0) == 0.0
    assert respond(u2, steal, None) == 0.0
    broke = tiny_universe(rival_cash={"riv": 0.0})
    assert respond(broke, Action("steal", buy="x", cost=0.0,
                                      victim="riv"), 3.0) == 0.0

    print("ffcore.pricing self-test OK")


if __name__ == "__main__":
    _selftest()
