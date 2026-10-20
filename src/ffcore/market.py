from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field, replace

from ffcore.action import Action
from ffcore.names import Name

__all__ = ["Market"]


@dataclass(frozen=True)
class Market:
    """Who the players are, what they cost and are worth, and your money.
    Tables are keyed by player and sparse: price only for players on sale,
    my_bid only where your bid is pending, clause
    only for owned players whose release clause can be paid now (it moves
    the player at once, the money going to his owner), paid only for
    players their owner bought, offer only for yours with an offer standing
    and fetch for those: what a sale gets (schema.sql's sale_odds: the offer
    or what waiting is worth, whichever is more). odds, for each player on
    the game's market, the bids over his asking price to choose from and
    each one's chance of winning his auction (schema.sql's bid_odds),
    lowest first. carry is the share
    of a trend made after each price update and error how far a value
    forecast is off then (rmse, % of value); offer_sd the spread of the
    game's offers as a share of value.
    No points: what anyone will score is the outlook's business."""
    cash: float = 0.0
    name: dict[str, Name] = field(default_factory=dict)
    pos: dict[str, str] = field(default_factory=dict)
    price: dict[str, float] = field(default_factory=dict)
    route: dict[str, str] = field(default_factory=dict)
    owner: dict[str, str] = field(default_factory=dict)
    value: dict[str, float] = field(default_factory=dict)
    trend: dict[str, float] = field(default_factory=dict)
    my_bid: dict[str, float] = field(default_factory=dict)
    clause: dict[str, float] = field(default_factory=dict)
    paid: dict[str, float] = field(default_factory=dict)
    offer: dict[str, float] = field(default_factory=dict)
    fetch: dict[str, float] = field(default_factory=dict)
    carry: tuple[float, ...] = ()
    error: tuple[float, ...] = ()
    offer_sd: float = 0.0
    odds: dict[str, tuple[tuple[float, float], ...]] = field(default_factory=dict)

    def shown(self, k: str) -> str:
        """How the board writes him: his Name, or his key without one."""
        n = self.name.get(k)
        return n.shown if n else k

    @property
    def names(self) -> dict[str, str]:
        """Every player as shown, for Action.label."""
        return {k: n.shown for k, n in self.name.items()}

    def fetches(self, k: str) -> float:
        """What selling him gets: with an offer standing, fetch; else his value."""
        return self.fetch[k] if k in self.fetch else self.value.get(k, 0.0)

    def takes(self, k: str) -> bool:
        """Whether a sale takes his standing offer tonight rather than wait."""
        return k in self.offer and self.fetches(k) == self.offer[k]

    def left(self, acts: Iterable[Action] = ()) -> float:
        """Your cash once these moves are made."""
        return self.cash - sum(a.net for a in acts)

    def later(self, k: str, h: int) -> float:
        """His value h price updates on: his trend, the share carry says is
        made by then (his value now with no carry or h of 0)."""
        v = self.value.get(k, 0.0)
        if not self.carry or h <= 0:
            return v
        return v * (1 + self.trend.get(k, 0.0) / 100 * self.carry[min(h, len(self.carry)) - 1])

    def money(self, acts: Iterable[Action], h: int) -> tuple[float, float]:
        """What these moves do to your money h price updates on, in millions:
        its mean and variance. A buy pays its cost for a player then worth
        later(); a sale gets its proceeds for players who would then have
        been worth later(). Each player's value h updates on is off by error
        (rmse), and a sale with no offer standing gets the game's offer, off
        his value by offer_sd."""
        sd = self.error[min(h, len(self.error)) - 1] / 100 if self.error and h > 0 else 0.0
        mean = var = 0.0
        for a in acts:
            if a.buy:
                mean += self.later(a.buy, h) - a.cost
                var += (self.value.get(a.buy, 0.0) * sd) ** 2
            if a.sell:
                mean += a.proceeds - sum(self.later(k, h) for k in a.sell)
                var += sum((self.value.get(k, 0.0) * sd) ** 2
                           + (0.0 if k in self.offer else (self.value.get(k, 0.0) * self.offer_sd) ** 2)
                           for k in a.sell)
        return mean / 1e6, var / 1e12


def _selftest() -> None:
    odds = Market(value={"s": 10e6}, offer={"s": 10.5e6}, fetch={"s": 10.5e6})
    low = replace(odds, offer={"s": 10.1e6}, fetch={"s": 10.2e6})
    assert odds.takes("s") and not low.takes("s"), "waiting is worth more than this offer"
    assert not odds.takes("nobody")
    assert odds.fetches("s") == 10.5e6 and low.fetches("s") == 10.2e6, \
        "a sale gets tonight's offer or what waiting is worth, whichever is more"
    assert Market(value={"s": 4e6}).fetches("s") == 4e6, "no offer yet: his value"
    assert Market().fetches("s") == 0.0

    debt = Market(cash=-5e6)
    sale, buy = Action("sell", sell=("s",), proceeds=3e6), Action("buy", buy="b", cost=1e6)
    assert debt.left([sale]) == -2e6
    assert Market(cash=2e6).left([buy, sale]) == 2e6 - 1e6 + 3e6


    moves = Market(value={"b": 10e6, "s": 20e6, "o": 5e6}, trend={"b": 10.0, "s": -20.0},
                   carry=(0.5, 1.0), error=(5.0, 10.0), offer_sd=0.05, offer={"o": 5.1e6})
    assert moves.later("b", 1) == 10.5e6 and moves.later("b", 9) == 11e6 and moves.later("b", 0) == 10e6
    assert Market(value={"b": 10e6}, trend={"b": 10.0}).later("b", 3) == 10e6, "no carry: as he is"
    buy, sell = Action("buy", buy="b", cost=10.2e6), Action("sell", sell=("s",), proceeds=19.8e6)
    m, v = moves.money([buy], 2)
    assert abs(m - 0.8) < 1e-9 and abs(v - 1.0) < 1e-9, "worth 11M for 10.2M; 10% of 10M off"
    m, v = moves.money([sell], 2)
    assert abs(m - 3.8) < 1e-9 and abs(v - (4.0 + 1.0)) < 1e-9, \
        "19.8M for one who would be worth 16M; off by 10% of 20M, and by the offers' 5%"
    m, v = moves.money([Action("sell", sell=("o",), proceeds=5.1e6)], 2)
    assert abs(m - 0.1) < 1e-9 and abs(v - 0.25) < 1e-9, "an offer standing: no spread on what it gets"
    assert moves.money([], 2) == (0.0, 0.0)

    named = Market(name={"k": Name("iñigo vicente")})
    assert named.shown("k") == "Iñigo Vicente" and named.shown("unknown") == "unknown", \
        "a player is shown by his Name, or his key when he has none"
    assert named.names == {"k": "Iñigo Vicente"}
    try:
        Market().prceeds  # type: ignore[attr-defined]  # noqa: B018  # the point: it must fail
    except AttributeError:
        pass
    else:
        raise AssertionError("a misspelt table must fail, not read as empty")
    print("ffcore.market self-test OK")


if __name__ == "__main__":
    _selftest()
