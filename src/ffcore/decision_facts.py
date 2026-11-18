"""The facts a decision reads at a cut, as one value computed from the World (seen_at): the SQL views player_facts,
my_squad and my_cash and the stored copies seen, seen_cash, seen_auction and seen_curve become selectors over a Seen.
Named rules, each tested:
  roster_is_the_universe: one PlayerFact per player_roster row, in its order.
  name_falls_back_to_key: an empty roster name is shown as the player key.
  zero_is_none: a value of 0, a buyout of 0, a zero offer or a zero bid is None, never 0.
  price_never_under_value: a listing's price is the larger of its sale price and its market value (0 when none); route
    'listed' for a manager's listing, else 'free'.
  one_sale_listing_per_player: a player with two sale listings takes the last of them in the listings' order.
  bid_is_pending_max: my_bid is the largest bid_money of the player's listings with bid_status 'pending' and a bid not 0.
  offer_is_pending_on_mine: offer is the largest pending non-zero offer on the player's team, shown only when the
    owner is league.me.
  owner_is_current_manager: owner is the manager of the player's squad row.
  paid_is_last_buy_to_owner: paid is what the owner last paid for the player (current.price_paid).
  trend_is_the_days_trend: trend is the player's price trend (price_fold.price_trend), None when he has none.
  clause_is_squad_buyout: clause is the buyout of the player's squad row, None with no row or a buyout of 0.
  open_listing_rival_excludes_own_bid: an open listing gives one SeenAuction; its rival is the bids seen less my own
    (mine 1 when I have a bid on it).
  my_squad_pos_default: a squad member with no position is 'MED'.
  my_cash_zero_without_row: my_cash is 0.0 when league.me has no cash row.
Contract: seen_at(world, identity, league) gives the Seen of the cut; it is the same alone or inside a sweep, unchanged by
rows observed after the cut, and reads no SQL."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable, NamedTuple

from ffcore import auction, auction_table, current, ledger, price_fold, state
from ffcore import observations as ob

TEAM_SELLER = "marketPlayerTeam"
DEFAULT_POS = "MED"


class PlayerFact(NamedTuple):
    player: str
    name: str
    pos: str | None
    value: float | None
    owner: str | None
    clause: float | None
    price: float | None
    route: str | None
    my_bid: float | None
    offer: float | None
    paid: float | None
    trend: float | None


class SquadMember(NamedTuple):
    player: str
    pos: str


class SeenAuction(NamedTuple):
    player: str
    ask: float
    close: str
    mins: float
    bids: int
    rival: int
    mine: int
    trend: float | None
    cell: str
    n: int
    thin: int


class SeenCurve(NamedTuple):
    player: str
    ratio: float
    p: float


class Seen(NamedTuple):
    cut: str
    facts: tuple[PlayerFact, ...]
    cash: tuple[ledger.Cash, ...]
    auctions: tuple[SeenAuction, ...]
    curves: tuple[SeenCurve, ...]


def _none_if_zero(x: float | None) -> float | None:
    return None if not x else x


def _sale_prices(listings: Iterable[current.Listing]) -> dict[str, tuple[float, str]]:
    out: dict[str, tuple[float, str]] = {}
    for r in listings:
        if r.player is not None and r.sale_price is not None and r.sale_price != 0:
            route = "listed" if r.seller == TEAM_SELLER else "free"
            out[r.player] = (max(r.sale_price, r.market_value or 0.0), route)
    return out


def _pending_bids(listings: Iterable[current.Listing]) -> dict[str, float]:
    out: dict[str, float] = {}
    for r in listings:
        if r.player is not None and r.bid_status == "pending" and r.bid_money is not None and r.bid_money != 0:
            out[r.player] = max(out.get(r.player, r.bid_money), r.bid_money)
    return out


def _pending_offers(world: state.World, squads: Iterable[current.Squad]) -> dict[str, float]:
    team_players: dict[str, list[str]] = defaultdict(list)
    for s in squads:
        if s.player is not None:
            team_players[s.player_team_id].append(s.player)
    out: dict[str, float] = {}
    for o in current.offers(world):
        if o.player_team_id != "" and o.status == "pending" and o.money is not None and o.money != 0:
            for p in team_players.get(o.player_team_id, ()):
                out[p] = max(out.get(p, o.money), o.money)
    return out


def player_facts(world: state.World, identity: current.Identity, league: ledger.League) -> tuple[PlayerFact, ...]:
    """One PlayerFact per roster row at the cut (the facts of player_facts; the rules are named in the docstring)."""
    squads = current.squads(world, identity)
    listings = current.listings(world, identity)
    sale = _sale_prices(listings)
    bids = _pending_bids(listings)
    offers = _pending_offers(world, squads)
    clause = {s.player: s.buyout for s in squads if s.player is not None and s.buyout is not None and s.buyout != 0}
    owner = {o.player: o.manager for o in current.owners(world, identity)}
    paid = {p.player: p.paid for p in current.price_paid(world, identity)}
    trend = {t.ff_id: t.trend for t in price_fold.price_trend(price_fold.price_state(world))}
    out = []
    for r in current.player_roster(world, identity):
        sold = sale.get(r.player)
        mine = owner.get(r.player)
        out.append(PlayerFact(
            r.player, r.name if r.name != "" else r.player, r.pos, _none_if_zero(r.value), mine,
            clause.get(r.player), sold[0] if sold else None, sold[1] if sold else None,
            _none_if_zero(bids.get(r.player)), offers.get(r.player) if mine == league.me else None,
            paid.get(r.player), trend.get(r.player)))
    return tuple(out)


def seen_at(world: state.World, identity: current.Identity, league: ledger.League) -> Seen:
    """The Seen of the cut: the player facts, each manager's cash, and the clearing-price forecast with its curves
    (auction.forecast of auction_table.TABLE over auction.open_looks_of)."""
    forecasts = auction.forecast(auction_table.TABLE, auction.open_looks_of(world, identity, league))
    auctions = tuple(SeenAuction(f.player, f.ask, f.close, f.mins, f.bids, f.rival, int(f.mine), f.trend, f.cell, f.n,
                                 int(f.thin)) for f in forecasts)
    curves = tuple(SeenCurve(f.player, ratio, p) for f in forecasts for ratio, p in f.curve)
    return Seen(world.cut, player_facts(world, identity, league), ledger.cash(world, identity, league), auctions, curves)


def my_squad(seen: Seen, league: ledger.League) -> tuple[SquadMember, ...]:
    """The players league.me owns at the cut, each with his position (DEFAULT_POS when none)."""
    return tuple(SquadMember(f.player, f.pos or DEFAULT_POS) for f in seen.facts if f.owner == league.me)


def my_cash(seen: Seen, league: ledger.League) -> float:
    """league.me's cash at the cut, 0.0 when he has no cash row."""
    return next((c.cash for c in seen.cash if c.manager == league.me), 0.0)


_CUTS = ["2026-08-18T1700Z", "2026-08-18T1800Z", "2026-08-18T1900Z"]
_ST = "2026-08-18T1800Z"
_LATER = "2026-08-18T1930Z"
_END = "2026-08-18T20:00:00+00:00"
_APP = {"a1": "p1", "a2": "p2", "a3": "p3", "a4": "p4", "a5": "p5"}


def _listing(pid: str, mid: str, seller: str, sale: float | None, mv: float | None, bid: float | None, status: str,
             bid_id: str = "", stamp: str = _ST) -> ob.ApiMarket:
    return ob.ApiMarket(stamp, "laliga", mv, sale, bid, None, pid, mid, "", "", seller, "", "", _END, bid_id, status)


def _team(pid: str, manager: str, buyout: float | None, ptid: str, stamp: str = _ST) -> ob.ApiTeams:
    return ob.ApiTeams(stamp, "laliga", 0.0, buyout, 0.0, pid, "", manager, "", "", "", ptid)


def _offer(ptid: str, oid: str, money: float | None, status: str, stamp: str = _ST) -> ob.ApiOffers:
    return ob.ApiOffers(stamp, "laliga", money, ptid, oid, status, stamp, "", "true")


def _standing(team: str, uid: str, manager: str, money: float | None, stamp: str = _ST) -> ob.ApiStandings:
    return ob.ApiStandings(stamp, "laliga", 1, 0.0, 0.0, 0.0, money, team, uid, manager)


def _buy(stamp: str, aid: str, pid: str, amount: float | None, at: str, uid: str) -> ob.ApiActivity:
    return ob.ApiActivity(stamp, "laliga", amount, pid, aid, at, "buy", uid, "")


def _tables() -> dict[str, list[Any]]:
    league = "marketPlayerLeague"
    tables: dict[str, list[Any]] = {name: [] for name in ob.TABLES}
    tables["market"] = [
        ob.Market(_ST, "laliga", 9.0, "p1", "DEL", "RMA", "Ana"),
        ob.Market(_ST, "laliga", 0.0, "p2", "POR", "BAR", ""),
        ob.Market(_ST, "laliga", 15.0, "p3", "MED", "ATM", "Bo"),
        ob.Market(_ST, "laliga", 5.0, "p4", "DEL", "VAL", "Cy"),
        ob.Market(_ST, "laliga", 0.0, "p5", "Tercero", "SEV", "Di"),
        ob.Market(_ST, "laliga", 4.0, "p6", "Entrenador", "BAR", "Ed")]
    tables["api_teams"] = [_team("a1", "ann", 0.0, "t1"), _team("a2", "bob", 5.0, "t2"),
                           _team("a3", "ann", 15.0, "t3"), _team("a5", "cat", 0.0, "t5")]
    tables["api_standings"] = [_standing("t-ann", "u1", "ann", 40.0), _standing("t-bob", "u2", "bob", 0.0),
                               _standing("t-x", "u3", "", 0.0)]
    tables["api_offers"] = [_offer("t3", "o1", 6.0, "pending"), _offer("t3", "o2", 2.0, "pending"),
                            _offer("t3", "o3", 9.0, "declined"), _offer("t3", "o4", 0.0, "pending"),
                            _offer("t1", "o5", 0.0, "pending"), _offer("t2", "o6", 5.0, "pending"),
                            _offer("", "o7", 4.0, "pending"), _offer("t5", "o8", 0.0, "pending")]
    tables["api_market"] = [
        _listing("a1", "m1", TEAM_SELLER, 12.0, 9.0, 4.0, "pending", "b1"),
        _listing("a2", "m2", league, 3.0, None, None, ""),
        _listing("a2", "m3", league, 4.0, None, None, ""),
        _listing("a4", "m4", league, 3.0, 5.0, 2.0, "pending", "b4"),
        _listing("a4", "m5", league, 0.0, None, 6.0, "pending", "b5"),
        _listing("a4", "m6", league, None, None, 20.0, "won", "b6"),
        _listing("a9", "m7", league, 1.0, None, 3.0, "pending", "b7")]
    tables["api_activity"] = [_buy(_ST, "e1", "a3", 12.0, "2026-08-18T17:50:00+00:00", "u1"),
                              _buy(_ST, "e2", "a2", 2.0, "2026-08-18T17:55:00+00:00", "u2"),
                              _buy(_ST, "e3", "a2", 50.0, "2026-08-18T17:58:00+00:00", "u1")]
    return tables


def _scratch() -> tuple[ob.Observations, current.Identity, ledger.League]:
    return ob.of(_tables()), current.Identity({}, _APP), ledger.League("ann", 200.0)


def _view(world: state.World, identity: current.Identity, league: ledger.League) -> Seen:
    return seen_at(world, identity, league)


def _selftest() -> None:
    """Each named rule holds on the scratch league; later rows leave a cut unchanged, any subset or order of cuts gives
    each cut the same facts, and a cut alone equals the cut inside a sweep."""
    obs, ident, league = _scratch()
    alone = {c: _view(state.world_at(obs, c), ident, league) for c in _CUTS}

    def read(w: state.World) -> Seen:
        return _view(w, ident, league)
    assert state.sweep(obs, _CUTS, read) == [alone[c] for c in _CUTS]
    for order in (_CUTS[::-1], [_CUTS[1], _CUTS[0], _CUTS[1]], [_CUTS[-1]]):
        assert state.sweep(obs, order, read) == [alone[c] for c in order]
    later = _tables()
    later["api_market"].append(_listing("a1", "m8", TEAM_SELLER, 13.0, 9.0, 50.0, "pending", "b8", _LATER))
    later["api_offers"].append(_offer("t3", "o9", 30.0, "pending", _LATER))
    later["api_standings"].append(_standing("t-ann", "u1", "ann", 99.0, _LATER))
    later_obs = ob.of(later)
    assert all(_view(state.world_at(later_obs, c), ident, league) == alone[c] for c in _CUTS)

    assert alone[_CUTS[0]].facts == () and alone[_CUTS[0]].auctions == (), "no rows before the first observation"
    seen = alone[_ST]
    w = state.world_at(obs, _ST)
    facts = {f.player: f for f in seen.facts}
    assert seen.cut == _ST
    assert [f.player for f in seen.facts] == [r.player for r in current.player_roster(w, ident)], (
        "roster_is_the_universe: the coach p6 is not on the roster")
    assert facts["p1"].name == "Ana" and facts["p2"].name == "p2", "name_falls_back_to_key"
    assert facts["p2"].value is None and facts["p5"].value is None and facts["p5"].clause is None, (
        "zero_is_none: a value of 0 and a buyout of 0")
    assert facts["p1"].offer is None, "zero_is_none: a zero offer is no offer"
    assert (facts["p1"].price, facts["p1"].route) == (12.0, "listed"), "price_never_under_value: a manager's listing"
    assert (facts["p4"].price, facts["p4"].route) == (5.0, "free"), "price_never_under_value: the market value is larger"
    assert facts["p2"].price == 4.0, "one_sale_listing_per_player: the last sale listing wins"
    assert facts["p4"].my_bid == 6.0 and facts["p1"].my_bid == 4.0 and facts["p2"].my_bid is None, (
        "bid_is_pending_max: the largest pending bid; the sale listing with no sale and the won bid are not")
    assert facts["p3"].offer == 6.0, "offer_is_pending_on_mine: the largest pending offer of my squad"
    assert facts["p2"].offer is None, "offer_is_pending_on_mine: shown only on my players"
    assert (facts["p2"].clause, facts["p3"].clause, facts["p4"].clause) == (5.0, 15.0, None), \
        "clause_is_squad_buyout: the buyout of his squad row, none without one"
    assert facts["p3"].owner == "ann" and facts["p4"].owner is None and facts["p5"].owner == "cat", (
        "owner_is_current_manager")
    assert facts["p3"].paid == 12.0 and facts["p2"].paid == 2.0 and facts["p1"].paid is None, (
        "paid_is_last_buy_to_owner: bob's buy of p2 counts, ann's buy of p2 does not")
    trends = {t.ff_id: t.trend for t in price_fold.price_trend(price_fold.price_state(w))}
    assert {p: f.trend for p, f in facts.items()} == {p: trends.get(p) for p in facts}, "trend_is_the_days_trend"
    assert facts["p5"].pos == "MED" and facts["p1"].pos == "DEL", "unknown position is MED"
    assert my_squad(seen, league) == (SquadMember("p1", "DEL"), SquadMember("p3", "MED")), "my_squad_pos_default"
    assert sum(1 for f in seen.facts if f.owner == league.me) == len(my_squad(seen, league))
    assert my_cash(seen, league) == 40.0 and my_cash(seen, ledger.League("zed", 200.0)) == 0.0, (
        "my_cash_zero_without_row")
    assert {c.manager for c in seen.cash} == {u.manager for u in current.users(w)}, "cash covers exactly the users"
    assert {c.player for c in seen.curves} <= {a.player for a in seen.auctions} and seen.auctions, (
        "every curve point has its auction")
    listed = [ob.ApiMarket(_ST, "laliga", 1.0, 100.0, 100.0, 1, "a8", "m8", "", "", "marketPlayerLeague", "", "", _END, "b8", "pending"),
              ob.ApiMarket(_ST, "laliga", 1.0, 100.0, None, 1, "a9", "m9", "", "", "marketPlayerLeague", "", "", _END, "", ""),
              ob.ApiMarket(_ST, "laliga", 1.0, 100.0, None, 1, "a4", "m4", "", "", "marketPlayerLeague", "", "",
                           "2026-08-18T17:30:00+00:00", "", "")]
    forecast = {a.player: a for a in seen_at(state.world_at(ob.of({"api_market": listed}), _ST),
                                              current.Identity({}, {"a8": "p8", "a9": "p9", "a4": "p4"}), league).auctions}
    assert forecast.keys() == {"p8", "p9"}, "open_listing_rival_excludes_own_bid: a forecast for each open listing only"
    assert [(forecast[p].bids, forecast[p].rival, forecast[p].mine) for p in ("p8", "p9")] == [(1, 0, 1), (1, 1, 0)], \
        "open_listing_rival_excludes_own_bid: a bid seen is a rival's unless it is mine"
    print("ffcore.decision_facts self-test OK")


if __name__ == "__main__":
    _selftest()
