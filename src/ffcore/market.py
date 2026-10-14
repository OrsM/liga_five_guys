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
    or what waiting is worth, whichever is more). premium is what an
    auction bid must be, over the asking price, to win.
    No points: what anyone will score is the outlook's business."""
    cash: float = 0.0
    premium: float = 1.0
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

    def shown(self, k: str) -> str:
        """How the board writes him: his Name, or his key without one."""
        n = self.name.get(k)
        return n.shown if n else k

    @property
    def names(self) -> dict[str, str]:
        """Every player as shown, for Action.label."""
        return {k: n.shown for k, n in self.name.items()}

    @property
    def locked_cash(self) -> float:
        return sum(self.my_bid.values())

    def fetches(self, k: str) -> float:
        """What selling him gets: with an offer standing, fetch; else his value."""
        return self.fetch[k] if k in self.fetch else self.value.get(k, 0.0)

    def takes(self, k: str) -> bool:
        """Whether a sale takes his standing offer tonight rather than wait."""
        return k in self.offer and self.fetches(k) == self.offer[k]

    def left(self, acts: Iterable[Action] = ()) -> float:
        """Your cash once these moves are made."""
        return self.cash - sum(a.net for a in acts)


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


    named = Market(name={"k": Name("iñigo vicente")})
    assert named.shown("k") == "Iñigo Vicente" and named.shown("unknown") == "unknown", \
        "a player is shown by his Name, or his key when he has none"
    assert named.names == {"k": "Iñigo Vicente"}
    assert Market(my_bid={"a": 1e6, "b": 2.5e6}).locked_cash == 3.5e6
    assert Market().locked_cash == 0.0
    try:
        Market().prceeds  # type: ignore[attr-defined]  # noqa: B018  # the point: it must fail
    except AttributeError:
        pass
    else:
        raise AssertionError("a misspelt table must fail, not read as empty")
    print("ffcore.market self-test OK")


if __name__ == "__main__":
    _selftest()
