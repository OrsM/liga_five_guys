from __future__ import annotations

from dataclasses import dataclass, field, replace

from ffcore.action import Action

__all__ = ["Market"]


@dataclass(frozen=True)
class Market:
    """Who the players are, what they cost and are worth, and your money.
    Tables are keyed by player and sparse: price only for players on sale,
    proceeds only for yours, my_bid only where your bid is pending.
    No points: what anyone will score is the outlook's business."""
    cash: float = 0.0
    lam: float | None = None
    premium: float = 1.0
    name: dict[str, str] = field(default_factory=dict)
    pos: dict[str, str] = field(default_factory=dict)
    price: dict[str, float] = field(default_factory=dict)
    route: dict[str, str] = field(default_factory=dict)
    owner: dict[str, str] = field(default_factory=dict)
    value: dict[str, float] = field(default_factory=dict)
    proceeds: dict[str, float] = field(default_factory=dict)
    trend: dict[str, float] = field(default_factory=dict)
    my_bid: dict[str, float] = field(default_factory=dict)

    @property
    def locked_cash(self) -> float:
        return sum(self.my_bid.values())

    def burn(self, a: Action) -> float | None:
        if not a.buy:
            return 0.0
        val = self.value.get(a.buy)
        if val is None:
            return None
        return max(0.0, a.cost * self.premium - val)

    def cash_pts(self, a: Action, lam: float | None = None) -> float:
        lam = self.lam if lam is None else lam
        if not lam:
            return 0.0
        value, trend, proceeds = self.value, self.trend, self.proceeds
        money = -(self.burn(a) or 0.0)
        if a.buy:
            money += value.get(a.buy, 0.0) * trend.get(a.buy, 0.0) / 100
        for k in a.sell:
            v = value.get(k, proceeds.get(k, 0.0))
            money += proceeds.get(k, 0.0) - v * (1 + trend.get(k, 0.0) / 100)
        return lam * money / 1e6


def _selftest() -> None:
    m = Market(value={"star": 5e6, "free": 4e6})
    assert m.burn(Action("buy", buy="star", cost=8e6)) == 3e6
    assert m.burn(Action("buy", buy="free", cost=4e6)) == 0.0
    assert m.burn(Action("buy", buy="free", cost=3e6)) == 0.0
    assert m.burn(Action("sell", sell=("bench",))) == 0.0
    assert m.burn(Action("buy", buy="mystery", cost=9e6)) is None
    m = replace(m, premium=1.1)
    assert abs(m.burn(Action("buy", buy="free", cost=4e6)) - 0.4e6) < 1e-6

    assert m.cash_pts(Action("buy", buy="free", cost=4e6)) == 0.0, "no lam, no cash"
    riser = Market(lam=2.0, value={"r": 10e6}, trend={"r": 5.0})
    assert abs(riser.cash_pts(Action("buy", buy="r", cost=10e6)) - 1.0) < 1e-9
    seller = Market(lam=1.0, value={"s": 4e6}, proceeds={"s": 4e6},
                    trend={"s": -25.0})
    assert abs(seller.cash_pts(Action("sell", sell=("s",), proceeds=4e6)) - 1.0) < 1e-9

    assert Market(my_bid={"a": 1e6, "b": 2.5e6}).locked_cash == 3.5e6
    assert Market().locked_cash == 0.0
    try:
        Market().prceeds  # noqa: B018
    except AttributeError:
        pass
    else:
        raise AssertionError("a misspelt table must fail, not read as empty")
    print("ffcore.market self-test OK")


if __name__ == "__main__":
    _selftest()
