"""The decision's inputs: the league at t (schema.sql look, my_squad, my_cash) made into the rows the board and
approach.approach read (decide.sql dec_*), and the players on the market who would improve your eleven.
decision_at(loaded, world) is the decision at a cut as one value computed from the World, live and replay alike; the
board's extras (Extras) are selectors over the same World.
Named rules, each tested:
  stale_app_makes_no_decision: fresh is None only when the newest api_teams snapshot is at most APP_FRESH_HOURS before
    the cut; no api_teams at all is stale ("unknown"); decision_at gives Stale(reason) for a stale cut.
  known_name_is_shown_name: Fact.name is Name's shown form of the stored name, not the raw text.
  offer_needs_pending_money: Extras.offers: status 'pending', money not 0, on a player of held_names, value the
    squad row's market_value (0.0 when the squad row has none, as Held's).
  market_read_is_newest_api_market_stamp: market_read is None when there is no market row at the cut.
  arrivals_need_a_previous_run: arrivals computes from the fresh opportunities and the records of earlier cuts, at every
    cut (the SQL dec_arrivals read only the cut's own kept run); with no earlier record there is none, and a player
    who was 'market' at the newest earlier cut is not an arrival."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping, NamedTuple, Sequence

import eleven
from ffcore import current, forecast_facts, jornadas, price_fold, season_facts, state, store
from ffcore.current import AppLineup, Identity, PlayerClub
from ffcore.decision_facts import DEFAULT_POS, PlayerFact, Seen, SeenAuction, my_cash, my_squad, seen_at
from ffcore.ledger import Cash, League
from ffcore.loader import Loaded, Opportunity
from ffcore.names import Name
from ffcore.parse import snapshot_stamp
from ffcore.tidy import APP_FRESH_HOURS
from ffcore.price_fold import Expected
from ffcore.records import AuctionBrier, AuctionCalibration
from ffcore.season_facts import ListingClose, RoundLock
from ffcore.auction import Curve, bid_for
from ffcore.rules import BID_TARGET, DEBT_SHARE, POSITIONS, SQUAD_MAX, XI_SIZE, in_force

__all__ = ["Inputs", "squad_of", "Lineup", "lineup_of", "cells_of", "window_value", "curves_of", "opportunity_rows",
           "Fact", "UNKNOWN", "Offer", "Held",
           "Extras", "extras_at", "Stale", "Decided", "fresh", "arrivals", "decision_at", "decisions",
           "latest_auction_run", "brier_by_scope", "drifting_bins"]


class Player(NamedTuple):
    player: str
    pos: str
    mine: int
    asking: float | None


class CurvePoint(NamedTuple):
    player: str
    ratio: float
    p: float


class Auction(NamedTuple):
    player: str
    ask: float
    mins: float
    bids: int
    rival: int
    mine: int
    trend: float | None
    cell: str
    n: int
    thin: int


class Money(NamedTuple):
    cash: float
    team_value: float
    debt: float
    squad_max: int
    xi_size: int


class Bid(NamedTuple):
    player: str
    amount: float


class Close(NamedTuple):
    player: str
    close: str


class Lock(NamedTuple):
    jornada: int
    lock: str


class Slot(NamedTuple):
    app_id: str
    player: str | None
    slot: str
    name: str


class Cell(NamedTuple):
    player: str
    jornada: int
    e: float


class Candidate(NamedTuple):
    player: str
    pos: str
    status: str
    asking: float | None
    value: float | None


class Price(NamedTuple):
    player: str
    h: int
    expected_value: float
    value: float


class Inputs(NamedTuple):
    """The league at t as the decision reads it: your money, a sequence of named rows a dec_* table, the cells
    (player, jornada, e), every player who is not yours with his status (market, rival, unlisted), and each player's
    price forecast."""
    money: Money
    players: Sequence[Player] = ()
    curves: Sequence[CurvePoint] = ()
    auctions: Sequence[Auction] = ()
    bids: Sequence[Bid] = ()
    closes: Sequence[Close] = ()
    locks: Sequence[Lock] = ()
    lineup: Sequence[Slot] = ()
    cells: Sequence[Cell] = ()
    universe: Sequence[Candidate] = ()
    prices: Sequence[Price] = ()


def julian_stamp(t: float) -> str:
    """SQLite's strftime('%Y-%m-%dT%H:%MZ', t) of a julian day: UTC, rounded to the millisecond, minutes truncated."""
    unix_ms = int(t * 86400000.0 + 0.5) - 210866760000000
    return datetime.fromtimestamp(unix_ms // 60000 * 60, timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def colon_stamp(stamp: str) -> str:
    """'2026-08-30T1700Z' as '2026-08-30T17:00Z'."""
    return stamp[:13] + ":" + stamp[13:]


def players_of(seen: Seen, league: League) -> list[Player]:
    """The players the decision can move (rule market_player_needs_price_and_no_owner): each of mine once, with mine 1
    and no asking price, then every other player with a price and no owner at his price."""
    squad = my_squad(seen, league)
    held = {m.player for m in squad}
    return [Player(m.player, m.pos, 1, None) for m in squad] + [
        Player(f.player, f.pos or DEFAULT_POS, 0, f.price) for f in seen.facts
        if f.price is not None and f.owner is None and f.player not in held]


def curve_points_of(seen: Seen) -> list[CurvePoint]:
    """The bid-ratio win-chance points of the seen curves."""
    return [CurvePoint(c.player, c.ratio, c.p) for c in seen.curves]


def auctions_of(seen: Seen) -> list[Auction]:
    """The seen auctions without their close (rule auction_drops_close); mine and thin stay ints."""
    return [Auction(a.player, a.ask, a.mins, a.bids, a.rival, a.mine, a.trend, a.cell, a.n, a.thin) for a in seen.auctions]


def closes_of(listing_close: Iterable[ListingClose]) -> list[Close]:
    """When each listed player's auction closes, as julian_stamp reads it (rule close_stamp_has_a_colon)."""
    return [Close(c.player, julian_stamp(c.t)) for c in listing_close if c.t is not None]


def locks_of(rounds: Iterable[RoundLock], cut: str) -> list[Lock]:
    """The locks strictly after the cut, in colon_stamp form (rule lock_after_cut_only)."""
    return [Lock(r.jornada, colon_stamp(r.lock)) for r in rounds if r.lock > cut]


def bids_of(seen: Seen) -> list[Bid]:
    """My standing bids (rule bid_positive_only)."""
    return [Bid(f.player, f.my_bid) for f in seen.facts if f.my_bid is not None and f.my_bid > 0]


def slots_of(rows: Iterable[AppLineup]) -> list[Slot]:
    """The app's fielded eleven as Slots."""
    return [Slot(r.app_id, r.player, r.slot, r.name) for r in rows]


def money_of(seen: Seen, league: League) -> Money:
    """My cash, the total value of my squad's seen rows (rule team_value_is_total), and the rules' debt share, squad cap
    and eleven size."""
    mine = {m.player for m in my_squad(seen, league)}
    value = sum((f.value for f in seen.facts if f.player in mine and f.value is not None), 0.0)
    return Money(my_cash(seen, league), value, DEBT_SHARE, SQUAD_MAX, XI_SIZE)


def universe_of(seen: Seen, clubs: Iterable[PlayerClub], league: League) -> list[Candidate]:
    """Every seen player who is not mine, with his status market (no owner, a price), unlisted (no owner) or rival, and
    his club's position, else the fact's, else 'MED' (rule universe_excludes_mine)."""
    club_pos = {c.player: c.pos for c in clubs}
    return [Candidate(f.player, club_pos.get(f.player) or f.pos or DEFAULT_POS,
                      ("market" if f.price is not None else "unlisted") if f.owner is None else "rival", f.price, f.value)
            for f in seen.facts if f.owner is None or f.owner != league.me]


def prices_of(expected: Iterable[Expected]) -> list[Price]:
    """Each expected price row under the decision's names (rule price_forecast_rows_by_name)."""
    return [Price(e.ff_id, e.h, e.expected_value, e.value) for e in expected]


def inputs_at(world: state.World, identity: Identity, league: League, seen: Seen, fc: forecast_facts.Inputs) -> Inputs:
    """The decision's inputs at the world's cut: a pure function of the world, the seen facts and the forecast inputs,
    each fact computed once."""
    cells = [Cell(c.player, c.jornada, c.e) for c in forecast_facts.cell(fc)]
    return Inputs(
        money_of(seen, league), players=players_of(seen, league), curves=curve_points_of(seen), auctions=auctions_of(seen),
        bids=bids_of(seen), closes=closes_of(season_facts.listing_close(world, identity)),
        locks=locks_of(season_facts.round_locks(world), world.cut), lineup=slots_of(current.app_lineup(world, identity)),
        cells=cells, universe=universe_of(seen, fc.clubs, league),
        prices=prices_of(price_fold.price_forecast(price_fold.price_state(world), season_facts.next_lock(world).t)))


def squad_of(inp: Inputs) -> frozenset[str]:
    return frozenset(p.player for p in inp.players if p.mine)


class Lineup(NamedTuple):
    eleven: frozenset[str] | None
    message: str
    unmapped: tuple[str, ...]


def lineup_of(inp: Inputs, pos: dict[str, str]) -> Lineup:
    mine = squad_of(inp)
    unmapped = tuple(sorted(s.name for s in inp.lineup if s.player is None))
    fielded = frozenset(s.player for s in inp.lineup if s.player is not None)
    if not inp.lineup:
        return Lineup(None, "the app has no line-up for you: you don't have an eleven", unmapped)
    if unmapped:
        return Lineup(None, "the app's line-up has players we cannot match: " + ", ".join(unmapped), unmapped)
    if not fielded <= mine:
        return Lineup(None, "the app fields a player who is no longer yours", unmapped)
    if not eleven.legal(fielded, pos):
        return Lineup(None, "the app's line-up is not a legal eleven (%d players)" % len(fielded), unmapped)
    return Lineup(fielded, "", unmapped)


def cells_of(inp: Inputs) -> tuple[dict[str, str], dict[int, dict[str, float]]]:
    pos = {p.player: p.pos for p in inp.players if p.pos in POSITIONS}
    pos |= {c.player: c.pos for c in inp.universe if c.pos in POSITIONS}
    cells: dict[int, dict[str, float]] = {}
    for c in inp.cells:
        if c.player in pos:
            cells.setdefault(c.jornada, {})[c.player] = c.e
    return pos, cells


def window_value(pos: dict[str, str], cells: dict[int, dict[str, float]], where: frozenset[int]) -> dict[str, float]:
    return {k: sum(cells[j].get(k, 0.0) for j in where) for k in pos}


def curves_of(inp: Inputs) -> dict[str, Curve]:
    out: dict[str, list[tuple[float, float]]] = {}
    for c in sorted(inp.curves):
        out.setdefault(c.player, []).append((c.ratio, c.p))
    return {k: tuple(v) for k, v in out.items()}


def rise(x: Price) -> float:
    return x.expected_value / x.value - 1


def opportunity_rows(inp: Inputs) -> list[dict[str, Any]]:
    pos, cells = cells_of(inp)
    mine = squad_of(inp)
    curves = curves_of(inp)
    locks = {x.jornada: x.lock for x in inp.locks}
    closes = {x.player: x.close for x in inp.closes}
    placed = {x.player: x.amount for x in inp.bids}
    soon = min(locks.values(), default=None)
    ahead: dict[str, dict[int, Price]] = {}
    for x in inp.prices:
        if x.value:
            ahead.setdefault(x.player, {})[x.h] = x
    fielded = lineup_of(inp, pos).eleven
    seen: dict[frozenset[int], tuple[dict[str, float], eleven.Settled | None]] = {}
    out = []
    for c in inp.universe:
        k, status, asking, value = c.player, c.status, c.asking, c.value
        if k not in pos or not any(k in at for at in cells.values()):
            continue
        close = closes.get(k) if status == "market" else None
        where = frozenset(j for j in cells if in_force(locks, j, close or soon))
        if not where:
            continue
        if where not in seen:
            v = window_value(pos, cells, where)
            seen[where] = (v, eleven.best_eleven(mine, pos, v, fielded))
        v, settled = seen[where]
        if settled is None:
            continue
        got = eleven.swap(k, settled.eleven, pos, v)
        if got is None:
            continue
        price = asking if asking is not None else value
        steps = ahead.get(k, {})
        bid: float | None = None
        win: float | None = None
        if status == "market" and asking is not None and k in curves:
            ratio, win = bid_for(curves[k], BID_TARGET)
            bid = asking * ratio
        out.append({
            "player": k, "pos": pos[k], "status": status, "asking": asking, "close_at": close, "from_j": min(where),
            "replaces": got.replaces, "gain": got.gain, "per_million": got.gain / (price / 1e6) if price else None,
            "price_next": rise(steps[1]) if 1 in steps else None,
            "price_lock": rise(steps[max(steps)]) if steps else None,
            "bid": bid, "win": win, "placed": placed.get(k)})
    return sorted(out, key=lambda o: (-o["gain"], o["player"]))


# What the board shows beside the decision: facts about players, the offers on your players, who is listed, how the
# auction forecast has done. Each selector is named for what it returns.

class Fact(NamedTuple):
    name: str
    pos: str | None
    trend: float | None


UNKNOWN = Fact("", None, None)


class Offer(NamedTuple):
    player: str
    value: float
    money: float


class Held(NamedTuple):
    player: str
    player_team: str
    player_id: str | None
    value: float


def latest_auction_run() -> int | None:
    """The run that last measured the auction forecast (the greatest run_id with an auction_brier row), None if none has."""
    return max((b.run_id for b in store.records("auction_brier")), default=None)


def brier_by_scope(run: int) -> list[AuctionBrier]:
    """The run's auction_brier rows by scope."""
    return sorted(store.records("auction_brier", run), key=lambda b: b.scope)


def drifting_bins(run: int) -> list[AuctionCalibration]:
    """The run's auction_calibration rows that drift, by scope then bin."""
    return sorted((c for c in store.records("auction_calibration", run) if c.drift == 1), key=lambda c: (c.scope, c.bin))


class Extras(NamedTuple):
    """What the board reads beside the inputs, at the cut: the facts about players, your players' names in the squads,
    the offers on them, who is held and who is listed, and the stamp of the market read."""
    known: dict[str, Fact]
    held_names: frozenset[str]
    offers: list[Offer]
    held: list[Held]
    listed: frozenset[str]
    market_read: str | None


class Stale(NamedTuple):
    reason: str


class Decided(NamedTuple):
    """The decision at a cut: the seen facts, the forecast's inputs, the decision's inputs, the extras and the jornadas
    left (jornadas.rounds_at's rem)."""
    cut: str
    seen: Seen
    fc: forecast_facts.Inputs
    inputs: Inputs
    extras: Extras
    rounds_left: list[int]


def fresh(world: state.World) -> str | None:
    """None when the newest api_teams snapshot at the cut is at most APP_FRESH_HOURS old (rule
    stale_app_makes_no_decision), else the reason no decision is made."""
    stamps = [r.observed_at for r in state.latest(world, "api_teams")]
    when, at = (snapshot_stamp(max(stamps)) if stamps else None), snapshot_stamp(world.cut)
    age = None if when is None or at is None else (at - when).total_seconds() / 3600
    if age is not None and age <= APP_FRESH_HOURS:
        return None
    return "the app's squads are %s old; no board is built from them" % ("unknown" if age is None else "%.0fh" % age)


def extras_at(world: state.World, identity: Identity, league: League, seen: Seen) -> Extras:
    """The extras of the cut, over the World and its seen facts (rules known_name_is_shown_name,
    offer_needs_pending_money, market_read_is_newest_api_market_stamp)."""
    squads = current.squads(world, identity)
    names = frozenset(s.player for s in squads if s.manager == league.me and s.player is not None)
    pending = [o for o in current.offers(world) if o.status == "pending" and o.money is not None and o.money != 0]
    stamps = [r.observed_at for r in state.latest(world, "api_market")]
    return Extras(
        {f.player: Fact(Name(f.name).shown, f.pos, f.trend) for f in seen.facts}, names,
        sorted((Offer(s.player, s.market_value or 0.0, o.money) for o in pending for s in squads
                if s.player_team_id == o.player_team_id and s.player in names and o.money is not None),
               key=lambda x: (x.player, x.money)),
        sorted((Held(s.player, s.player_team_id, s.player_id, s.market_value or 0.0) for s in squads if s.player in names),
               key=lambda x: (x.player, x.player_team)),
        frozenset(x.player for x in current.listings(world, identity) if x.player is not None),
        max(stamps) if stamps else None)


def arrivals(got: Sequence[Mapping[str, Any]], previous: Sequence[Opportunity]) -> list[str]:
    """The players on the market with a gain, best first (gain, then name), who were not on the market at the newest
    cut of previous (the records of the cuts before this one); none when there is no record (rule
    arrivals_need_a_previous_run)."""
    if not previous:
        return []
    last = max(o.cut for o in previous)
    on_market = {o.player for o in previous if o.cut == last and o.status == "market"}
    ranked = sorted((r for r in got if r["gain"] > 0), key=lambda r: (-r["gain"], r["player"]))
    return [r["player"] for r in ranked if r["status"] == "market" and r["player"] not in on_market]


def decision_at(loaded: Loaded, world: state.World) -> Decided | Stale:
    """The decision at the world's cut: Stale(reason) when fresh gives one, else the seen facts, the forecast inputs,
    the inputs, the extras and the jornadas left, each built once."""
    reason = fresh(world)
    if reason is not None:
        return Stale(reason)
    seen = seen_at(world, loaded.identity, loaded.league)
    fc = forecast_facts.inputs(world, loaded.identity, loaded.knobs)
    return Decided(world.cut, seen, fc, inputs_at(world, loaded.identity, loaded.league, seen, fc),
                   extras_at(world, loaded.identity, loaded.league, seen), jornadas.rounds_at(world).rem)


def replay(loaded: Loaded, cuts: Iterable[str], consume: Callable[[str, Decided | Stale], None]) -> None:
    """consume(cut, decision_at that cut) once for each distinct cut of cuts, ascending, in one sweep of the loaded
    observations; nothing is kept, so memory does not grow with the cuts. Returns None."""
    state.sweep(loaded.obs, cuts, lambda world: consume(world.cut, decision_at(loaded, world)))


def decisions(loaded: Loaded, cuts: Sequence[str]) -> list[Decided | Stale]:
    """decision_at for each cut, in the order given: replay collected."""
    got: dict[str, Decided | Stale] = {}
    replay(loaded, cuts, got.__setitem__)
    return [got[c] for c in cuts]


_NO_MONEY = Money(0.0, 0.0, 0.0, SQUAD_MAX, XI_SIZE)


def _selftest_opportunities() -> None:
    squad = {"k": "POR", "d1": "DEF", "d2": "DEF", "d3": "DEF", "m1": "MED", "m2": "MED", "m3": "MED",
             "m4": "MED", "f1": "DEL", "f2": "DEL", "f3": "DEL"}
    e = {**{k: 4.0 for k in squad}, "m4": 2.0, "star": 8.0, "good": 5.0, "keeper": 3.0, "late": 9.0, "rival": 7.0, "free": 1.0}
    kinds = {"star": "MED", "good": "MED", "keeper": "POR", "late": "MED", "rival": "MED", "free": "MED"}
    inp = Inputs(
        _NO_MONEY,
        players=[Player(k, p, 1, None) for k, p in squad.items()]
        + [Player(k, kinds[k], 0, a) for k, a in (("star", 8e6), ("good", 5e6), ("keeper", 3e6), ("late", 6e6))],
        universe=[Candidate("star", "MED", "market", 8e6, 8e6), Candidate("good", "MED", "market", 5e6, 5e6),
                  Candidate("keeper", "POR", "market", 3e6, 3e6), Candidate("late", "MED", "market", 6e6, 6e6),
                  Candidate("rival", "MED", "rival", None, 9e6), Candidate("free", "MED", "unlisted", None, 1e6)],
        curves=[CurvePoint("star", 1.0, 0.25), CurvePoint("star", 1.25, 1.0), CurvePoint("star", 1.3, 1.0),
                CurvePoint("good", 1.0, 0.5), CurvePoint("good", 1.2, 0.7)],
        bids=[Bid("good", 5.5e6)],
        prices=[Price("star", 1, 10.5e6, 10e6), Price("star", 3, 11e6, 10e6)],
        cells=[Cell(k, j, v) for k, v in e.items() for j in (1, 2)],
        locks=[Lock(1, "2026-11-06T20:00Z"), Lock(2, "2026-11-13T20:00Z")],
        closes=[Close("late", "2026-11-06T21:24Z")])
    got = {r["player"]: r for r in opportunity_rows(inp)}
    assert got["star"]["gain"] == 12.0 and got["star"]["replaces"] == "m4", "star for m4 is 6 a jornada"
    assert (got["star"]["bid"], got["star"]["win"]) == (10e6, 1.0), "the least bid that reaches the target chance"
    assert got["good"]["gain"] == 6.0 and got["good"]["placed"] == 5.5e6, "a bid placed is kept"
    assert (got["good"]["bid"], got["good"]["win"]) == (6e6, 0.7), "none reaches the target: the likeliest"
    assert got["late"]["gain"] > 0 and (got["late"]["bid"], got["late"]["win"]) == (None, None), \
        "on the market with no curve: his gain is said, no bid or chance is made up"
    assert got["keeper"]["gain"] == -2.0 and got["keeper"]["replaces"] == "k", "a worse keeper would replace the keeper, for less"
    assert got["late"]["gain"] == 7.0 and got["late"]["from_j"] == 2, "an auction closing after jornada 1's lock counts from jornada 2: your eleven is frozen at the lock"
    assert got["rival"]["status"] == "rival" and got["rival"]["bid"] is None and got["rival"]["gain"] == 10.0, "a target is judged without being on sale"
    assert got["free"]["status"] == "unlisted" and got["free"]["gain"] < 0
    assert abs(got["star"]["price_next"] - 0.05) < 1e-12 and abs(got["star"]["price_lock"] - 0.10) < 1e-12 and got["good"]["price_next"] is None
    assert got["star"]["per_million"] == 12.0 / 8.0, "points a million of his asking price"
    assert [r["player"] for r in opportunity_rows(inp)][0] == "star", "most points first"
    short = inp._replace(players=[p for p in inp.players if p.player != "k"])
    assert opportunity_rows(short) == [], "no eleven: no opportunities"


def _selftest_lineup() -> None:
    squad = {"k": "POR", "d1": "DEF", "d2": "DEF", "d3": "DEF", "d4": "DEF", "m1": "MED", "m2": "MED", "m3": "MED",
             "f1": "DEL", "f2": "DEL", "f3": "DEL", "bench": "DEF", "kb": "POR"}
    fielded = ["k", "d1", "d2", "d3", "d4", "m1", "m2", "m3", "f1", "f2", "f3"]
    slots = {k: squad[k] for k in squad}

    def lineup(names: list[str], extra: list[Slot] | None = None) -> Inputs:
        return Inputs(_NO_MONEY, players=[Player(k, p, 1, None) for k, p in squad.items()],
                      lineup=[Slot(str(i), k, slots[k], k) for i, k in enumerate(names)] + (extra or []))
    pos = dict(squad)
    got = lineup_of(lineup(fielded), pos)
    assert got.eleven == frozenset(fielded) and got.message == "" and got.unmapped == ()
    none = lineup_of(lineup([]), pos)
    assert none.eleven is None and "don't have an eleven" in none.message, "no line-up in the app: you don't have an eleven"
    unmatched = lineup_of(lineup(fielded[:10], [Slot("99", None, "DEL", "Ghost")]), pos)
    assert unmatched.eleven is None and unmatched.unmapped == ("Ghost",), "an app id we cannot match is named, never dropped"
    short = lineup_of(lineup(fielded[:10]), pos)
    assert short.eleven is None and "not a legal eleven" in short.message
    gone = lineup(fielded)._replace(players=[Player(k, p, int(k != "f3"), None) for k, p in squad.items()])
    assert lineup_of(gone, pos).eleven is None and "no longer yours" in lineup_of(gone, pos).message
    flat = {k: 4.0 for k in squad}
    swapped = ["k", "d1", "d2", "d3", "bench", "m1", "m2", "m3", "f1", "f2", "f3"]
    value = flat | {"d4": 6.0, "bench": 1.0}
    best = eleven.best_eleven(frozenset(squad), pos, value, lineup_of(lineup(swapped), pos).eleven)
    assert best is not None and "d4" in best.eleven and best.swaps == (("d4", "bench", 5.0),), "a bench player who beats a fielded one is the advice"
    top = eleven.best_eleven(frozenset(squad), pos, value)
    assert top is not None and eleven.total(top.eleven, value) == eleven.total(best.eleven, value), "settling from the app's eleven reaches the best"


def _fact(player: str, pos: str | None = "MED", value: float | None = None, owner: str | None = None,
          price: float | None = None, my_bid: float | None = None) -> PlayerFact:
    return PlayerFact(player, player, pos, value, owner, None, price, None, my_bid, None, None, None)


def _selftest_builders() -> None:
    """One assertion per named rule of the builders, on hand-built Seen rows."""
    me = League("me", 100.0)
    facts = (_fact("mine1", "DEL", 5.0, "me"), _fact("mine2", None, None, "me", 3.0), _fact("mkt", "POR", 2.0, None, 4.0),
             _fact("free", "DEF", 1.0), _fact("rival", "MED", 6.0, "other", 9.0, 2.0), _fact("bid0", "MED", 1.0, None, 1.0, 0.0),
             _fact("nopos", None, 1.0, None, 2.0))
    seen = Seen("2026-08-18T1800Z", facts, (Cash("me", 7.5),), (), ())
    got = players_of(seen, me)
    assert got == [Player("mine1", "DEL", 1, None), Player("mine2", "MED", 1, None), Player("mkt", "POR", 0, 4.0),
                   Player("bid0", "MED", 0, 1.0), Player("nopos", "MED", 0, 2.0)], "market_player_needs_price_and_no_owner"
    assert len({p.player for p in got}) == len(got)
    assert julian_stamp(2460000.5) == "2023-02-25T00:00Z" and julian_stamp(2460000.5 + 59.9 / 86400) == "2023-02-25T00:00Z", (
        "close_stamp_has_a_colon: seconds truncate, never round")
    assert julian_stamp(2460000.5 + 3 * 3600 / 86400 + 125 / 86400) == "2023-02-25T03:02Z"
    assert colon_stamp("2026-08-30T1700Z") == "2026-08-30T17:00Z"
    cut = "2026-08-30T1700Z"
    assert locks_of([RoundLock(1, "2026-08-30T1659Z"), RoundLock(2, cut), RoundLock(3, "2026-08-30T1701Z")], cut) == [
        Lock(3, "2026-08-30T17:01Z")], "lock_after_cut_only"
    assert bids_of(Seen(cut, (_fact("a", my_bid=0.0), _fact("b", my_bid=None), _fact("c", my_bid=2.5)), (), (), ())) == [
        Bid("c", 2.5)], "bid_positive_only"
    assert money_of(seen, me) == Money(7.5, 5.0, DEBT_SHARE, SQUAD_MAX, XI_SIZE), "team_value_is_total: None ignored"
    assert money_of(Seen(cut, (), (), (), ()), me).team_value == 0.0 and isinstance(money_of(seen, me).team_value, float)
    clubs = [PlayerClub("mkt", "BAR", "DEL"), PlayerClub("free", None, ""), PlayerClub("nopos", None, "")]
    assert universe_of(seen, clubs, me) == [
        Candidate("mkt", "DEL", "market", 4.0, 2.0), Candidate("free", "DEF", "unlisted", None, 1.0),
        Candidate("rival", "MED", "rival", 9.0, 6.0), Candidate("bid0", "MED", "market", 1.0, 1.0),
        Candidate("nopos", "MED", "market", 2.0, 1.0)], "universe_excludes_mine: club pos, else the fact's, else MED"
    auction = SeenAuction("mkt", 4.0, "2026-08-18T20:00Z", 30.0, 2, 1, 1, None, "c", 5, 0)
    (a,) = auctions_of(Seen(cut, (), (), (auction,), ()))
    assert "close" not in Auction._fields and a == Auction("mkt", 4.0, 30.0, 2, 1, 1, None, "c", 5, 0) and (
        type(a.mine), type(a.thin)) == (int, int), "auction_drops_close"
    assert prices_of([Expected("p", 3, "2026-08-19", 5.0, 5.5, None)]) == [Price("p", 3, 5.5, 5.0)], (
        "price_forecast_rows_by_name")


def _selftest_inputs_at() -> None:
    """Appended later rows leave a cut's inputs unchanged, any subset or order of cuts gives each cut the same inputs,
    and a cut alone equals the cut inside a sweep."""
    from ffcore.decision_facts import _CUTS, _scratch, _tables, _listing, _offer, _standing, _LATER, seen_at
    from ffcore import observations as ob
    obs, ident, league = _scratch()
    knobs = forecast_facts.knobs()

    def read(w: state.World) -> Inputs:
        seen = seen_at(w, ident, league)
        return inputs_at(w, ident, league, seen, forecast_facts.inputs(w, ident, knobs))
    alone = {c: read(state.world_at(obs, c)) for c in _CUTS}
    assert state.sweep(obs, _CUTS, read) == [alone[c] for c in _CUTS]
    for order in (_CUTS[::-1], [_CUTS[1], _CUTS[0], _CUTS[1]], [_CUTS[-1]]):
        assert state.sweep(obs, order, read) == [alone[c] for c in order]
    later = _tables()
    later["api_market"].append(_listing("a1", "m8", "marketPlayerTeam", 13.0, 9.0, 50.0, "pending", "b8", _LATER))
    later["api_offers"].append(_offer("t3", "o9", 30.0, "pending", _LATER))
    later["api_standings"].append(_standing("t-ann", "u1", "ann", 99.0, _LATER))
    later_obs = ob.of(later)
    assert all(read(state.world_at(later_obs, c)) == alone[c] for c in _CUTS)
    assert any(alone[c].players for c in _CUTS)


def _selftest_decision_at() -> None:
    """The three invariants of decision_at on the scratch league, and one assertion per named rule."""
    from ffcore import observations as ob
    from ffcore.decision_facts import _CUTS, _LATER, _ST, _listing, _offer, _scratch, _standing, _tables, TEAM_SELLER
    obs, ident, league = _scratch()
    loaded = Loaded(obs, ident, league, forecast_facts.knobs(), ())
    alone = {c: decision_at(loaded, state.world_at(obs, c)) for c in _CUTS}
    assert decisions(loaded, _CUTS) == [alone[c] for c in _CUTS], "decisions is decision_at cut by cut"
    for order in (_CUTS[::-1], [_CUTS[1], _CUTS[0], _CUTS[1]], [_CUTS[-1]]):
        assert decisions(loaded, order) == [alone[c] for c in order]
    seen_by_replay: dict[str, Decided | Stale] = {}
    replay(loaded, _CUTS[::-1], seen_by_replay.__setitem__)
    assert seen_by_replay == alone, "replay shows consume each cut's decision, whatever the order"
    later = _tables()
    later["api_market"].append(_listing("a1", "m8", TEAM_SELLER, 13.0, 9.0, 50.0, "pending", "b8", _LATER))
    later["api_offers"].append(_offer("t3", "o9", 30.0, "pending", _LATER))
    later["api_standings"].append(_standing("t-ann", "u1", "ann", 99.0, _LATER))
    later_loaded = loaded._replace(obs=ob.of(later))
    assert all(decision_at(later_loaded, state.world_at(later_loaded.obs, c)) == alone[c] for c in _CUTS)
    after: dict[str, Decided | Stale] = {}
    replay(later_loaded, _CUTS, after.__setitem__)
    assert after == alone, "rows appended after a cut do not change what consume saw at it"

    first = alone[_CUTS[0]]
    assert isinstance(first, Stale) and "unknown" in first.reason, "no api_teams: stale"
    decided = alone[_ST]
    assert isinstance(decided, Decided) and decided.cut == _ST and decided.inputs.players and decided.rounds_left == []
    assert decided.inputs == inputs_at(state.world_at(obs, _ST), ident, league, decided.seen, decided.fc)
    edge = {"2026-08-19T0824Z": True, "2026-08-19T0825Z": False}
    for c, ok in edge.items():
        w = state.world_at(obs, c)
        assert (fresh(w) is None) == ok == isinstance(decision_at(loaded, w), Decided), "stale_app_makes_no_decision"
    assert "14h old" in (fresh(state.world_at(obs, "2026-08-19T0825Z")) or "")

    w = state.world_at(obs, _ST)
    ex = decided.extras
    squads = current.squads(w, ident)
    assert ex.held_names == frozenset({"p1", "p3"}) == squad_names(squads, league.me)
    value = {s.player: s.market_value or 0.0 for s in squads}
    assert ex.offers == [Offer("p3", value["p3"], 2.0), Offer("p3", value["p3"], 6.0)], (
        "offer_needs_pending_money: pending, not 0, on mine; not declined, not bob's, not the unmatched")
    assert [h.player for h in ex.held] == ["p1", "p3"] and all(h.value == value[h.player] for h in ex.held)
    assert ex.listed == frozenset({"p1", "p2", "p4"}) and ex.market_read == _ST, "market_read_is_newest_api_market_stamp"
    assert alone[_CUTS[2]].extras.market_read == _ST  # type: ignore[union-attr]
    assert extras_at(state.world_at(obs, _CUTS[0]), ident, league, decided.seen).market_read is None
    assert ex.known["p1"] == Fact("Ana", "DEL", decided.seen.facts[0].trend)

    tables = _tables()
    tables["market"][2] = ob.Market(_ST, "laliga", 15.0, "p3", "MED", "ATM", "nico van gaal")
    named = ob.of(tables)
    w3 = state.world_at(named, _ST)
    seen3 = seen_at(w3, ident, league)
    shown = extras_at(w3, ident, league, seen3).known["p3"].name
    assert shown == Name("nico van gaal").shown != "nico van gaal", "known_name_is_shown_name"


def squad_names(squads: Iterable[current.Squad], me: str) -> frozenset[str]:
    return frozenset(s.player for s in squads if s.manager == me and s.player is not None)


def _selftest_arrivals_at() -> None:
    def rec(cut: str, player: str, status: str, gain: float = 1.0) -> Opportunity:
        return Opportunity(cut, player, "MED", status, None, None, 1, None, gain, None, None, None, None, None, None)

    def row(player: str, status: str, gain: float) -> dict[str, Any]:
        return {"player": player, "status": status, "gain": gain}
    got = [row("c", "market", 3.0), row("a", "market", 5.0), row("b", "market", 5.0), row("z", "market", 0.0),
           row("r", "rival", 9.0), row("old", "market", 2.0)]
    assert arrivals(got, []) == [], "arrivals_need_a_previous_run: no record"
    assert arrivals(got, [rec("1", "old", "market")]) == ["a", "b", "c"], "best first, ties by name, gain above 0"
    assert arrivals(got, [rec("1", "a", "market"), rec("2", "x", "market")]) == ["a", "b", "c", "old"], (
        "only the newest earlier cut counts")
    assert arrivals(got, [rec("2", "a", "market"), rec("2", "r", "market"), rec("2", "b", "rival"), rec("1", "c", "market")]) == [
        "b", "c", "old"], "market then, not an arrival; rival or older then, an arrival"


def _selftest() -> None:
    _selftest_builders()
    _selftest_inputs_at()
    _selftest_opportunities()
    _selftest_lineup()
    _selftest_decision_at()
    _selftest_arrivals_at()
    print("decision self-test OK")


if __name__ == "__main__":
    _selftest()
