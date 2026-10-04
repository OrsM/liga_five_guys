from __future__ import annotations

from dataclasses import dataclass, field, replace

from ffcore.action import Action
from ffcore.pricing import PRICE_WINDOW

__all__ = ["Market", "LISTED_SELLER", "market_routes", "pending"]


@dataclass(frozen=True)
class Market:
    """Who the players are, what they cost and are worth, and your money.
    Tables are keyed by player and sparse: price only for players on sale,
    my_bid only where your bid is pending, clause
    only for owned players whose release clause can be paid now (it moves
    the player at once, the money going to his owner), paid only for
    players their owner bought, offer only for yours with an offer standing
    (fetches: what a sale gets). offer_ratios are the game's
    past nightly offers over value, nights_left the offers still to come
    before the lock, carry the share of a trend made after each update
    (night). premium is what an
    auction bid must be, over the asking price, to win.
    No points: what anyone will score is the outlook's business."""
    cash: float = 0.0
    premium: float = 1.0
    name: dict[str, str] = field(default_factory=dict)
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
    offer_ratios: tuple[float, ...] = ()
    nights_left: int = 0
    carry: tuple[float, ...] = ()

    @property
    def locked_cash(self) -> float:
        return sum(self.my_bid.values())

    def fetches(self, k: str) -> float:
        """What selling him gets: the offer standing on him or what waiting
        for a later one is worth, whichever is more; with no offer yet, his
        value."""
        return max(self.offer[k], self.waiting(k) or 0.0) if k in self.offer \
            else self.value.get(k, 0.0)

    def _value_on(self, k: str, night: int) -> float:
        done = self.carry[min(night, len(self.carry)) - 1] if self.carry else 1.0
        return self.value[k] * (1 + self.trend.get(k, 0.0) / 100 * done)

    def waiting(self, k: str) -> float | None:
        """What turning down his standing offer is worth on average, if each
        night's offer to come is then taken only when it beats waiting on,
        and the last one before the lock always: None without an offer."""
        recent = self.offer_ratios[-PRICE_WINDOW:]
        if not self.offer.get(k) or not self.value.get(k) or not recent:
            return None
        later = 0.0
        for night in range(self.nights_left, 0, -1):
            then = self._value_on(k, night)
            later = sum(max(x * then, later) for x in recent) / len(recent)
        return later

    def takes(self, k: str) -> bool:
        """Whether a sale takes his standing offer tonight rather than wait."""
        return k in self.offer and self.fetches(k) == self.offer[k]

    def left(self, acts=()) -> float:
        """Your cash once these moves are made."""
        return self.cash - sum(a.net for a in acts)


LISTED_SELLER = "marketPlayerTeam"


def market_routes(mkt: list[dict]) -> tuple[dict[str, float], dict[str, str]]:
    price: dict[str, float] = {}
    route: dict[str, str] = {}
    for r in mkt:
        k = r["key"]
        if not k or not r.get("sale_price"):
            continue
        price[k] = float(r["sale_price"])
        route[k] = "listed" if r.get("seller") == LISTED_SELLER else "free"
    return price, route


def pending(rows, status_field: str, money_field: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for r in rows:
        if (r.get(status_field) or "") != "pending":
            continue
        amt = float(r.get(money_field) or 0)
        if not amt:
            continue
        k = r["key"]
        if k:
            out[k] = max(out.get(k, 0.0), amt)
    return out


def _selftest() -> None:
    odds = Market(value={"s": 10e6}, offer={"s": 10.5e6}, nights_left=2,
                  offer_ratios=(0.9, 1.0, 1.0, 1.1))
    rising = replace(odds, trend={"s": 20.0}, carry=(0.5, 1.0))
    assert rising.waiting("s") > odds.waiting("s"), "a rising value makes the offers to come dearer"
    assert abs(odds.waiting("s") - (1.0 + 1.0 + 1.0 + 1.1) / 4 * 10e6) < 1, \
        "the last night's offer is taken; the night before's only above it"
    low = replace(odds, offer={"s": 10.1e6})
    assert odds.takes("s") and not low.takes("s"), (odds.waiting("s"), low.offer)
    assert replace(low, nights_left=1).takes("s"), "one night left, worth 10M on average"
    assert replace(low, nights_left=0).takes("s"), "no night left: take it"
    assert odds.waiting("nobody") is None and not odds.takes("nobody")
    assert odds.fetches("s") == 10.5e6 and low.fetches("s") == low.waiting("s"), \
        "a sale gets tonight's offer or what waiting is worth, whichever is more"
    assert Market(value={"s": 4e6}).fetches("s") == 4e6, "no offer yet: his value"

    debt = Market(cash=-5e6)
    sale, buy = Action("sell", sell=("s",), proceeds=3e6), Action("buy", buy="b", cost=1e6)
    assert debt.left([sale]) == -2e6
    assert Market(cash=2e6).left([buy, sale]) == 2e6 - 1e6 + 3e6


    assert Market(my_bid={"a": 1e6, "b": 2.5e6}).locked_cash == 3.5e6
    assert Market().locked_cash == 0.0
    try:
        Market().prceeds  # noqa: B018
    except AttributeError:
        pass
    else:
        raise AssertionError("a misspelt table must fail, not read as empty")
    mkt_rows = [
        {"player_name": "Free Agent", "sale_price": "5000000",
         "seller": "marketPlayerLeague", "bids": "0"},
        {"player_name": "Listed Rival", "sale_price": "8000000",
         "seller": "marketPlayerTeam", "bids": "2"},
        {"player_name": "Not Priced", "sale_price": "",
         "seller": "marketPlayerLeague"},
        {"player_name": "Unjoinable", "sale_price": "1000000",
         "seller": "marketPlayerTeam"},
    ]
    for r, k in zip(mkt_rows, ["free_agent", "listed_rival", "not_priced",
                               None]):
        r["key"] = k
    price, route = market_routes(mkt_rows)
    assert price == {"free_agent": 5000000.0, "listed_rival": 8000000.0}, price
    assert route == {"free_agent": "free", "listed_rival": "listed"}, route
    assert "not_priced" not in route and "not_priced" not in price
    unknown_seller = [{"player_name": "Free Agent", "sale_price": "1",
                       "seller": "something_new", "key": "free_agent"}]
    _, r2 = market_routes(unknown_seller)
    assert r2 == {"free_agent": "free"}, r2

    mkt_bids = [
        {"player_name": "A", "bid_status": "pending", "bid_money": "5600000"},
        {"player_name": "B", "bid_status": "pending", "bid_money": "6795815"},
        {"player_name": "C", "bid_status": "", "bid_money": ""},
        {"player_name": "D", "bid_status": "accepted", "bid_money": "2000000"},
        {"player_name": "E", "bid_status": "pending", "bid_money": ""},
        {"player_name": "A", "bid_status": "pending", "bid_money": "5100000"},
        {"player_name": "", "bid_status": "pending", "bid_money": "9000000"},
    ]
    sent = pending([dict(r, key=r["player_name"]) for r in mkt_bids],
                   "bid_status", "bid_money")
    assert sent == {"A": 5600000.0, "B": 6795815.0}, sent
    assert pending([], "bid_status", "bid_money") == {}
    offers = [{"key": k, "status": st, "money": m} for k, st, m in [
        ("me_a", "pending", "6795815"), ("me_a", "pending", "1000000"),
        ("me_b", "accepted", "9000000"), ("me_b", "", ""),
        (None, "pending", "1")]]
    assert pending(offers, "status", "money") == {"me_a": 6795815.0}
    print("ffcore.market self-test OK")


if __name__ == "__main__":
    _selftest()
