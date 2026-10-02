from __future__ import annotations

from dataclasses import dataclass, field

from ffcore.action import Action

__all__ = ["Market", "LISTED_SELLER", "market_routes", "pending"]


@dataclass(frozen=True)
class Market:
    """Who the players are, what they cost and are worth, and your money.
    Tables are keyed by player and sparse: price only for players on sale,
    proceeds only for yours, my_bid only where your bid is pending, clause
    only for owned players whose release clause can be paid now (it moves
    the player at once, the money going to his owner). premium is what an
    auction bid must be, over the asking price, to win.
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
    clause: dict[str, float] = field(default_factory=dict)

    @property
    def locked_cash(self) -> float:
        return sum(self.my_bid.values())

    def left(self, acts=()) -> float:
        """Your cash once these moves are made."""
        return self.cash - sum(a.net for a in acts)

    def owed(self, acts=()) -> float:
        """What these moves leave to raise before the lock: a balance below
        zero at the matchday's start scores nothing."""
        return max(0.0, -self.left(acts))

    def short(self, a: Action, acts=()) -> float:
        """How much more than the cash these moves leave a move needs; at
        most 0 if it can be made. In debt there is nothing to spend, but
        a move that raises money can still be made."""
        return a.net - max(self.left(acts), 0.0)

    def burn(self, a: Action) -> float | None:
        """What a buy costs above the player's value; its cost is what you
        pay (for an auction, the bid it takes to win)."""
        if not a.buy:
            return 0.0
        val = self.value.get(a.buy)
        return None if val is None else max(0.0, a.cost - val)

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
    debt = Market(cash=-5e6)
    sale, buy = Action("sell", sell=("s",), proceeds=3e6), Action("buy", buy="b", cost=1e6)
    assert debt.owed() == 5e6 and debt.owed([sale]) == 2e6 and debt.left([sale]) == -2e6
    assert debt.short(sale) <= 0 < debt.short(buy), "in debt, only what raises money"
    assert Market(cash=2e6).short(buy, [sale]) == 1e6 - 5e6

    m = Market(value={"star": 5e6, "free": 4e6})
    assert m.burn(Action("buy", buy="star", cost=8e6)) == 3e6
    assert m.burn(Action("buy", buy="free", cost=4e6)) == 0.0
    assert m.burn(Action("buy", buy="free", cost=3e6)) == 0.0
    assert m.burn(Action("sell", sell=("bench",))) == 0.0
    assert m.burn(Action("buy", buy="mystery", cost=9e6)) is None

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
