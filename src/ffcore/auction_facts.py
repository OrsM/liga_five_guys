"""The auctions' outcomes and your bids (ingest): the SQL views auction_results and bid_changes as selectors over a
World, each fact group with its own fold. An outcome is a predicate on (cut, newest feed stamp) over the listings' ends
and the buys, evaluated at the read: it is not a prefix, so no list of results is kept.
Named rules, each tested:
  end_is_newest_snapshot_first_row: per (player_id, close) the End is the first qualifying row of the newest
    observed_at; a later row of the same observed_at does not replace it, a later observed_at does.
  end_needs_asking_and_close: only rows with seller league or team, a parseable non-empty expires_at and a sale_price
    above 0 count; an unparseable expires_at never reaches the output.
  unmapped_player_dropped: a result whose player_id the crosswalk does not name is absent; a bid change for such a
    player stays, with player None.
  buy_is_last_row_of_its_activity: per activity_id the last kind-buy row wins; rows without activity_id or of
    another kind are no buy.
  buy_window_is_ten_minutes: a buy belongs to an end if its player_id matches and abs(t - close) * 86400 < 600.
  top_buy_is_highest_amount: of several buys in the window the highest amount, None amounts last; a tie is not
    specified.
  bar_rules: bar is None for a manager's listing (seller team); 1.0 when nobody bought or the winner is you; else
    won / asking.
  closed_is_utc_stamp_of_close: closed is the UTC stamp of the close; close is the raw text, offset kept.
  outcome_waits_for_feed: a result is present only if closed <= cut and the newest feed stamp is at least
    close + 600.0 / 86400; with no feed rows there are no results.
  mine_is_as_of_standings: your user_ids are those of the standings rows up to the cut whose manager is league.me
    (the SQL reads the standings of all time; the two agree whenever your user_id never changes).
  bid_first_row_is_a_change: the first observation of a bid_id is a change (was None); a row with an unchanged
    bid_money is not; a change to or from None is; rows with an empty bid_id are no bid.
  bid_change_carries_close: a BidChange's close is the raw expires_at of the listing it was seen on.
  look_needs_game_listing: a look is a listing of the league seller with a sale_price above 0 and a non-empty
    expires_at; a manager's listing is no look.
  look_joins_result_by_raw_close: a look's outcome is matched on the raw expires_at text, not the instant: another
    spelling of the same instant does not match.
  closed_look_needs_its_outcome: a look whose close is at or before the cut and has no outcome is absent; a look
    closing after the cut is open (is_open True, won None); a look with an unparseable close is never kept.
  rival_excludes_own_bid: rival = bids - mine, with bids 0 when None.
  mins_before_close: minutes from the look's stamp to its close, times 1440.0.
Contract: auction_results(world, identity, league) gives one AuctionResult per listing whose outcome is seen at the cut;
bid_changes(world, identity) one BidChange per change of one of your bids; auction_looks(world, identity, league) one
LookRow per look observed by the cut; each cut's result is the same alone, in any subset or order of cuts, and
unchanged by rows stamped after it."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, NamedTuple

from ffcore import current
from ffcore import observations as ob
from ffcore import state
from ffcore.ledger import League
from ffcore.season_facts import julian_day, stamp_jd, utc_stamp

LEAGUE_SELLER = "marketPlayerLeague"
LISTING_SELLERS = (LEAGUE_SELLER, "marketPlayerTeam")
WINDOW_SECONDS = 600
DAY_SECONDS = 86400.0


class End(NamedTuple):
    player_id: str
    close: float
    at: str
    asking: float
    seller: str
    observed_at: str


class Buy(NamedTuple):
    player_id: str
    t: float | None
    amount: float | None
    user_id: str


class RawChange(NamedTuple):
    bid_id: str
    player_id: str
    close: str
    observed_at: str
    was: float | None
    now: float | None


class FeedSeen:
    def __init__(self) -> None:
        self.at = ""


class BidState:
    def __init__(self) -> None:
        self.last: dict[str, float | None] = {}
        self.changes: list[RawChange] = []


class AuctionResult(NamedTuple):
    player: str
    close: str
    closed: str
    asking: float
    seller: str
    won: float | None
    by_me: bool | None
    bar: float | None


class BidChange(NamedTuple):
    bid_id: str
    player: str | None
    close: str
    observed_at: str
    was: float | None
    now: float | None


class LookRow(NamedTuple):
    player: str
    close: str
    look: str
    mins: float
    ask: float
    bids: int
    mine: bool
    rival: int
    my_bid: float | None
    won: float | None
    by_me: bool | None
    is_open: bool


MARKET_ENDS = "market_ends"
BUYS = "buys"
FEED_SEEN = "feed_seen"
USER_IDS = "user_ids"
BID_CHANGES = "bid_changes"
LOOKS = "looks"
AUCTION_FACTS = (MARKET_ENDS, BUYS, FEED_SEEN, USER_IDS, BID_CHANGES, LOOKS)


def _end_step(ends: dict[tuple[str, float], End], row: ob.ApiMarket) -> dict[tuple[str, float], End]:
    close = julian_day(row.expires_at)
    if (row.seller in LISTING_SELLERS and close is not None and row.sale_price is not None and row.sale_price > 0):
        key = (row.player_id, close)
        held = ends.get(key)
        if held is None or row.observed_at > held.observed_at:
            ends[key] = End(row.player_id, close, row.expires_at, row.sale_price, row.seller, row.observed_at)
    return ends


def _buy_step(buys: dict[str, Buy], row: ob.ApiActivity) -> dict[str, Buy]:
    if row.kind == "buy" and row.activity_id != "":
        buys[row.activity_id] = Buy(row.player_id, julian_day(row.at), row.amount, row.user_id)
    return buys


def _feed_step(seen: FeedSeen, row: ob.ApiActivity) -> FeedSeen:
    seen.at = max(seen.at, row.observed_at)
    return seen


def _user_step(users: dict[str, set[str]], row: ob.ApiStandings) -> dict[str, set[str]]:
    if row.manager != "" and row.user_id != "":
        users.setdefault(row.manager, set()).add(row.user_id)
    return users


def _bid_step(bids: BidState, row: ob.ApiMarket) -> BidState:
    if row.bid_id != "" and (row.bid_id not in bids.last or bids.last[row.bid_id] != row.bid_money):
        bids.changes.append(RawChange(row.bid_id, row.player_id, row.expires_at, row.observed_at,
                                      bids.last.get(row.bid_id), row.bid_money))
    if row.bid_id != "":
        bids.last[row.bid_id] = row.bid_money
    return bids


state.register(state.Fold(MARKET_ENDS, "api_market", dict, _end_step))
state.register(state.Fold(BUYS, "api_activity", dict, _buy_step))
state.register(state.Fold(FEED_SEEN, "api_activity", FeedSeen, _feed_step))
state.register(state.Fold(USER_IDS, "api_standings", dict, _user_step))
def _look_step(looks: list[ob.ApiMarket], row: ob.ApiMarket) -> list[ob.ApiMarket]:
    if row.seller == LEAGUE_SELLER and row.sale_price is not None and row.sale_price > 0 and row.expires_at != "":
        looks.append(row)
    return looks


state.register(state.Fold(BID_CHANGES, "api_market", BidState, _bid_step))
state.register(state.Fold(LOOKS, "api_market", list, _look_step))


def _buys_by_player(world: state.World) -> dict[str, list[Buy]]:
    out: dict[str, list[Buy]] = defaultdict(list)
    for b in state.fact(world, BUYS).values():
        if b.t is not None:
            out[b.player_id].append(b)
    return out


def _top_buy(end: End, buys: list[Buy]) -> Buy | None:
    inside = [b for b in buys if b.t is not None and abs(b.t - end.close) * DAY_SECONDS < WINDOW_SECONDS]
    if not inside:
        return None
    return max(inside, key=lambda b: (b.amount is not None, b.amount if b.amount is not None else 0.0))


def _bar(end: End, top: Buy | None, by_me: bool | None, league: bool) -> float | None:
    if not league:
        return None
    if top is None or top.amount is None or by_me:
        return 1.0
    return top.amount / end.asking


def auction_results(world: state.World, identity: current.Identity, league: League) -> tuple[AuctionResult, ...]:
    """One result per listing closed by the cut whose outcome the feed has been seen ten minutes past (the rules
    end_is_newest_snapshot_first_row to outcome_waits_for_feed and mine_is_as_of_standings)."""
    seen = stamp_jd(state.fact(world, FEED_SEEN).at)
    if seen is None:
        return ()
    mine = state.fact(world, USER_IDS).get(league.me, set())
    buys = _buys_by_player(world)
    out = []
    for end in state.fact(world, MARKET_ENDS).values():
        player = identity.app.get(end.player_id)
        closed = utc_stamp(end.at)
        if player is None or closed is None or closed > world.cut or seen < end.close + WINDOW_SECONDS / DAY_SECONDS:
            continue
        top = _top_buy(end, buys.get(end.player_id, []))
        by_me = None if top is None else top.user_id in mine
        out.append(AuctionResult(player, end.at, closed, end.asking, end.seller,
                                 None if top is None else top.amount, by_me,
                                 _bar(end, top, by_me, end.seller == LEAGUE_SELLER)))
    return tuple(out)


def bid_changes(world: state.World, identity: current.Identity) -> tuple[BidChange, ...]:
    """Every change to one of your bids at the cut, in the order observed (bid_first_row_is_a_change,
    unmapped_player_dropped)."""
    return tuple(BidChange(c.bid_id, identity.app.get(c.player_id), c.close, c.observed_at, c.was, c.now)
                 for c in state.fact(world, BID_CHANGES).changes)


def auction_looks(world: state.World, identity: current.Identity, league: League) -> tuple[LookRow, ...]:
    """One row per league listing observed by the cut, with its outcome if the cut has one (the rules
    look_needs_game_listing to mins_before_close and unmapped_player_dropped)."""
    outcomes: dict[tuple[str, str], list[AuctionResult]] = defaultdict(list)
    for r in auction_results(world, identity, league):
        if r.seller == LEAGUE_SELLER:
            outcomes[(r.player, r.close)].append(r)
    cut = stamp_jd(world.cut)
    out = []
    for row in state.fact(world, LOOKS):
        player = identity.app.get(row.player_id)
        close = julian_day(row.expires_at)
        seen = stamp_jd(row.observed_at)
        if player is None or close is None or seen is None:
            continue
        held = outcomes.get((player, row.expires_at), [])
        if not held and not (cut is not None and close > cut):
            continue
        mine = row.bid_id != ""
        bids = row.bids or 0
        outcome: list[AuctionResult | None] = [*held] if held else [None]
        for res in outcome:
            out.append(LookRow(player, row.expires_at, row.observed_at, (close - seen) * 1440.0, row.sale_price or 0.0,
                               bids, mine, bids - mine, row.bid_money,
                               None if res is None else res.won, None if res is None else res.by_me, res is None))
    return tuple(out)


_APP = {"a1": "p1", "a2": "p2", "a3": "p3"}
_ME = League("ann", 100.0)
_SOON = "2026-08-18T20:24:00+02:00"
_C1 = "2026-08-18T22:24:00+02:00"
_C1_UTC = "2026-08-18T2024Z"


def _stamp(hhmm: str, day: str = "2026-08-18") -> str:
    return "%sT%sZ" % (day, hhmm)


def _listing(hhmm: str, pid: str, seller: str, close: str, price: float | None, bid_id: str = "",
             bid_money: float | None = None) -> ob.ApiMarket:
    return ob.ApiMarket(_stamp(hhmm), "laliga", 1.0, price, bid_money, None, pid, "m", "", "", seller, "", "", close,
                        bid_id, "")


def _buy(hhmm: str, aid: str, uid: str, pid: str, amount: float | None, at: str, kind: str = "buy") -> ob.ApiActivity:
    return ob.ApiActivity(_stamp(hhmm), "laliga", amount, pid, aid, at, kind, uid, "")


def _standing(hhmm: str, uid: str, manager: str) -> ob.ApiStandings:
    return ob.ApiStandings(_stamp(hhmm), "laliga", 1, 0.0, 0.0, 0.0, 0.0, "t" + uid, uid, manager)


def _tables() -> dict[str, list[Any]]:
    team = "marketPlayerTeam"
    lg = LEAGUE_SELLER
    past = "2026-08-18T19:00:00+00:00"
    return {
        "api_market": [
            _listing("1800", "a1", lg, _C1, 10.0),
            _listing("1800", "a2", team, past, 5.0),
            _listing("1800", "a9", lg, past, 7.0, "b9", 3.0),
            _listing("1800", "a3", lg, "2026-08-18T18:00:00+00:00", 6.0),
            _listing("1800", "a3", "other", "2026-08-18T17:00:00+00:00", 6.0),
            _listing("1800", "a3", lg, "not a time", 6.0),
            _listing("1800", "a3", lg, "2026-08-18T17:30:00+00:00", 0.0),
            _listing("1800", "a3", lg, "2026-08-18T17:30:00+00:00", None),
            _listing("1900", "a1", lg, _C1, 11.0, "b1", 12.0),
            _listing("1900", "a1", lg, _C1, 12.0, "b1", 12.0),
            _listing("1930", "a1", lg, _C1, 11.0, "b1", 15.0),
            _listing("2000", "a1", lg, _C1, 13.0, "b1", 15.0),
            _listing("2030", "a1", lg, _C1, 15.0, "b1", None),
            _listing("2030", "a1", lg, _C1, 16.0, "b1", None),
            _listing("2040", "a3", lg, "2026-08-19T20:00:00+00:00", 9.0, "b1", 20.0)],
        "api_activity": [
            _buy("2027", "e1", "u2", "a1", 20.0, "2026-08-18T20:27:00+00:00"),
            _buy("2029", "e1", "u2", "a1", 22.0, "2026-08-18T20:27:00+00:00"),
            _buy("2029", "", "u2", "a1", 99.0, "2026-08-18T20:27:00+00:00"),
            _buy("2029", "e2", "u2", "a1", 98.0, "2026-08-18T20:27:00+00:00", "sell"),
            _buy("2030", "e3", "u2", "a1", 40.0, "2026-08-18T20:34:00+00:00"),
            _buy("2040", "e4", "u2", "a1", 30.0, "2026-08-18T20:40:00+00:00"),
            _buy("2045", "e5", "u1", "a1", 25.0, "2026-08-18T20:30:00+00:00")],
        "api_standings": [_standing("1800", "u2", "bob"), _standing("2042", "u1", "ann"),
                          _standing("2042", "u9", "")],
    }


def _scratch() -> tuple[ob.Observations, current.Identity, League]:
    return ob.of(_tables()), current.Identity({}, _APP), _ME


def _view(world: state.World, identity: current.Identity, league: League) -> tuple[str, Any, Any, Any]:
    return world.cut, auction_results(world, identity, league), bid_changes(world, identity), auction_looks(
        world, identity, league)


_CUTS = ["2026-08-17T0000Z"] + [_stamp(h) for h in ("1800", "1900", "1930", "2000", "2030", "2035", "2040", "2043",
                                                    "2045", "2100")]


def _results_at(tables: dict[str, list[Any]], cut: str, league: League = _ME) -> tuple[AuctionResult, ...]:
    world = state.world_at(ob.of(tables), cut, {n: state.FOLDS[n] for n in AUCTION_FACTS})
    return auction_results(world, current.Identity({}, _APP), league)


def _look_tables() -> dict[str, list[Any]]:
    lg = LEAGUE_SELLER
    close = "2026-08-18T10:00:00+00:00"
    return {
        "api_market": [
            _listing("0900", "a1", lg, close, 10.0)._replace(bids=2),
            _listing("0930", "a1", lg, close, 10.0)._replace(bids=3),
            _listing("0900", "a2", "marketPlayerTeam", close, 5.0),
            _listing("0905", "a1", lg, close, 0.0),
            _listing("0906", "a1", lg, close, None),
            _listing("0907", "a1", lg, "", 4.0),
            _listing("0908", "a1", lg, "not a time", 6.0),
            _listing("0909", "a9", lg, close, 7.0),
            _listing("0910", "a1", lg, "2026-08-18T11:00:00+00:00", 9.0, "b7", 9.0)._replace(bids=3),
            _listing("0912", "a1", lg, "2026-08-18T12:00:00+02:00", 8.0)],
        "api_activity": [_buy("1015", "e1", "u2", "a1", 12.0, "2026-08-18T10:02:00+00:00")],
        "api_standings": [],
    }


def _looks_at(tables: dict[str, list[Any]], cut: str) -> tuple[LookRow, ...]:
    world = state.world_at(ob.of(tables), cut, {n: state.FOLDS[n] for n in AUCTION_FACTS})
    return auction_looks(world, current.Identity({}, _APP), _ME)


def _selftest() -> None:
    """Each named rule holds on the scratch league; later rows leave a cut unchanged, any subset or order of cuts gives
    each cut the same facts, and a cut alone equals the cut inside a sweep."""
    obs, ident, league = _scratch()
    only = {n: state.FOLDS[n] for n in AUCTION_FACTS}
    alone = {c: _view(state.world_at(obs, c, only), ident, league) for c in _CUTS}

    def read(w: state.World) -> Any:
        return _view(w, ident, league)
    assert state.sweep(obs, _CUTS, read, only) == [alone[c] for c in _CUTS]
    for order in (_CUTS[::-1], [_CUTS[7], _CUTS[3], _CUTS[7]], [_CUTS[-1]], [_CUTS[2], _CUTS[8]]):
        assert state.sweep(obs, order, read, only) == [alone[c] for c in order]
    later = _tables()
    later["api_market"] += [_listing("2200", "a1", LEAGUE_SELLER, _C1, 99.0, "b1", 77.0),
                            _listing("2200", "a2", "marketPlayerTeam", "2026-08-18T19:00:00+00:00", 1.0)]
    later["api_activity"].append(_buy("2200", "e8", "u1", "a2", 50.0, "2026-08-18T19:02:00+00:00"))
    later["api_standings"].append(_standing("2200", "u7", "ann"))
    later_obs = ob.of(later)
    assert all(_view(state.world_at(later_obs, c, only), ident, league) == alone[c] for c in _CUTS)

    for c in _CUTS:
        _, results, _, _ = alone[c]
        assert all(r.closed <= c for r in results)
        assert all((r.bar is None) == (r.seller != LEAGUE_SELLER) for r in results)

    by = {c: {r.player: r for r in alone[c][1]} for c in _CUTS}
    assert by[_stamp("2030")].keys() == {"p2", "p3"} and by[_stamp("2035")].keys() == {"p2", "p3"}, (
        "outcome_waits_for_feed: p1 closed 20:24Z, the feed reaches 20:30Z only")
    assert by[_stamp("2040")].keys() == {"p1", "p2", "p3"}, "outcome_waits_for_feed: present at the later cut"
    assert by[_stamp("1930")] == {} and by[_CUTS[0]] == {}, "no feed rows: no results"
    p1_early, p1_late = by[_stamp("2040")]["p1"], by[_stamp("2045")]["p1"]
    assert p1_early.won == 22.0 and p1_late.won == 25.0, "non-prefix: a buy seen later changes won"
    assert (p1_early.by_me, p1_late.by_me) == (False, True)

    p1 = p1_early
    assert p1.asking == 15.0, "end_is_newest_snapshot_first_row: newest observed_at, its first row"
    assert (p1.close, p1.closed) == (_C1, _C1_UTC), "closed_is_utc_stamp_of_close"
    assert p1.won == 22.0, "buy_is_last_row_of_its_activity: e1 is 22, rows without id or of kind sell are no buy"
    assert abs(p1.bar - 22.0 / 15.0) < 1e-12 and p1_late.bar == 1.0, "bar_rules"
    assert by[_stamp("2040")]["p2"].bar is None and by[_stamp("2040")]["p2"].seller == "marketPlayerTeam"
    p3 = by[_stamp("2040")]["p3"]
    assert (p3.won, p3.by_me, p3.bar) == (None, None, 1.0), "bar_rules: nobody bought"
    assert not any(r.close in ("not a time", "2026-08-18T17:30:00+00:00") or r.seller == "other"
                   for r in alone[_stamp("2100")][1]), "end_needs_asking_and_close"
    assert len(alone[_stamp("2100")][1]) == 3
    assert "p9" not in by[_stamp("2100")] and all(r.player in _APP.values() for r in alone[_stamp("2100")][1]), (
        "unmapped_player_dropped")

    assert by[_stamp("2040")]["p1"].by_me is False and all(
        r.by_me is not True for c in _CUTS if c < _stamp("2043") for r in alone[c][1]), "mine_is_as_of_standings"
    standings_late = _tables()
    standings_late["api_standings"] = [_standing("2100", "u1", "ann")]
    assert _results_at(standings_late, _stamp("2045"))[0].by_me is False
    assert _results_at(standings_late, _stamp("2100"))[0].by_me is True
    assert _results_at(standings_late, _stamp("2100"), League("zed", 0.0))[0].by_me is False

    base: dict[str, list[Any]] = {"api_market": [_listing("1800", "a1", LEAGUE_SELLER, "2026-08-18T10:00:00+00:00", 10.0)],
            "api_activity": [_buy("1800", "w0", "u2", "a1", 1.0, "2026-08-18T10:09:59+00:00"),
                             _buy("1800", "w1", "u2", "a1", 5.0, "2026-08-18T09:50:01+00:00"),
                             _buy("1800", "w2", "u2", "a1", 7.0, "2026-08-18T10:10:00+00:00"),
                             _buy("1800", "w3", "u2", "a2", 9.0, "2026-08-18T10:00:00+00:00")],
            "api_standings": []}
    top = _results_at(base, _stamp("1800"))
    assert [r.won for r in top] == [5.0], "buy_window_is_ten_minutes: exactly ten minutes and other players are out"
    base["api_activity"].append(_buy("1800", "w4", "u2", "a1", None, "2026-08-18T10:00:00+00:00"))
    base["api_activity"].append(_buy("1800", "w5", "u2", "a1", 6.0, "2026-08-18T10:03:00+00:00"))
    assert [r.won for r in _results_at(base, _stamp("1800"))] == [6.0], "top_buy_is_highest_amount: None last"
    only_none = {**base, "api_activity": [_buy("1800", "w4", "u2", "a1", None, "2026-08-18T10:00:00+00:00")]}
    assert [(r.won, r.bar) for r in _results_at(only_none, _stamp("1800"))] == [(None, 1.0)]

    changes = alone[_stamp("2100")][2]
    assert [(c.bid_id, c.player, c.was, c.now) for c in changes] == [
        ("b9", None, None, 3.0), ("b1", "p1", None, 12.0), ("b1", "p1", 12.0, 15.0), ("b1", "p1", 15.0, None),
        ("b1", "p3", None, 20.0)], "bid_first_row_is_a_change"
    assert not any(c.bid_id == "" for c in changes) and len(changes) == 5
    assert {(c.player, c.close) for c in changes if c.bid_id == "b1"} == {("p1", _C1), ("p3", "2026-08-19T20:00:00+00:00")}, \
        "bid_change_carries_close: a change has the listing's raw expires_at"

    lt = _look_tables()
    mid = {(r.player, r.look): r for r in _looks_at(lt, "2026-08-18T0945Z")}
    assert mid.keys() == {("p1", _stamp("0900")), ("p1", _stamp("0910")), ("p1", _stamp("0912")), ("p1", _stamp("0930"))}, (
        "look_needs_game_listing: the manager's, zero, empty, unparseable and unmapped listings are no look")
    assert all(r.is_open and r.won is None and r.by_me is None for r in mid.values())
    assert mid[("p1", _stamp("0900"))].rival == 2 and mid[("p1", _stamp("0930"))].rival == 3, (
        "rival_excludes_own_bid: no bid of ours, the rivals are the bids")
    own = mid[("p1", _stamp("0910"))]
    assert own.mine and own.rival == 2 and own.my_bid == 9.0, "rival_excludes_own_bid: our bid is not a rival"
    assert abs(mid[("p1", _stamp("0900"))].mins - 60.0) < 1e-6 and abs(mid[("p1", _stamp("0930"))].mins - 30.0) < 1e-6, (
        "mins_before_close: from the look's stamp to the close")

    assert {(r.player, r.look) for r in _looks_at(lt, "2026-08-18T1005Z")} == {("p1", _stamp("0910"))}, (
        "closed_look_needs_its_outcome: closed at 10:00 with no outcome yet, so absent; open looks stay")
    late = {(r.player, r.look): r for r in _looks_at(lt, "2026-08-18T1015Z")}
    assert late.keys() == {("p1", _stamp("0900")), ("p1", _stamp("0910")), ("p1", _stamp("0930"))}, (
        "look_joins_result_by_raw_close: the look with the other spelling of 10:00Z has no outcome, so it is absent")
    for key in (("p1", _stamp("0900")), ("p1", _stamp("0930"))):
        assert late[key].won == 12.0 and late[key].by_me is False and late[key].is_open is False, (
            "closed_look_needs_its_outcome: a closed look takes its outcome, non-prefix with the cut at 10:05")
    assert late[("p1", _stamp("0910"))].is_open is True and late[("p1", _stamp("0910"))].won is None
    print("ffcore.auction_facts self-test OK")


if __name__ == "__main__":
    _selftest()
