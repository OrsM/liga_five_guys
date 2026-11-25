"""The decision's inputs: the league at t (my squad, my cash) made into the rows the board and
approach.approach read, and the players on the market who would improve your eleven.
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

import random
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping, NamedTuple, Sequence

import eleven
from ffcore import current, forecast_facts, jornadas, price_fold, season_facts, state, store
from ffcore.current import AppLineup, Identity, PlayerClub
from ffcore.decision_facts import DEFAULT_POS, PlayerFact, Seen, SeenAuction, my_cash, my_squad, seen_at
from ffcore.ledger import Cash, League
from ffcore.loader import Loaded, Opportunity
from ffcore.names import Name
from ffcore.clock import Clock, clock_at, cut
from ffcore.parse import snapshot_stamp
from ffcore.tidy import APP_FRESH_HOURS
from ffcore.price_fold import Expected
from ffcore.records import AuctionBrier, AuctionCalibration
from ffcore.season_facts import ListingClose, RoundLock
from ffcore.auction import Curve
from ffcore.rules import DEBT_SHARE, POSITIONS, SQUAD_MAX, XI_SIZE, cash_at_lock
import hurdle
import valuation
from inflow import Credit, credit_at, inflow_of
from valuation import Window, window_at

__all__ = ["Inputs", "squad_of", "Lineup", "lineup_of", "cells_of", "lock_times", "window_of", "next_close", "arrival", "window_value", "curves_of", "clock_of", "opportunity_rows",
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
    price forecast (rows up to the longer of the next lock and hurdle.HOLD_NIGHTS; `nights` the nights to that lock, None: every
    row is a sale's), and the bonus credits that land before the next lock (inflow.Credit)."""
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
    credits: Sequence[Credit] = ()
    nights: int | None = None


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


def credits_from(cut: str, lock: str, started: Mapping[int, str], last: Mapping[int, str], unknown: Iterable[int],
                 later: Mapping[int, frozenset[str]], scored: float) -> list[Credit]:
    """The Credits of the jornadas whose credit lands after the cut and by the lock: of the `started` jornadas (each its lock, at or
    before the cut) those with a `last` kickoff known and not in `unknown` (a fixture left with no kickoff), at inflow.credit_at that
    kickoff, strictly after the cut (a stamp, as colon_stamp writes it) and at most the lock. `scored` is the points the team has in
    the jornada with the latest lock (0 for any other); `later[j]` the players with a match in j to kick off."""
    now, held = colon_stamp(cut), set(unknown)
    latest = max(started, key=lambda j: (started[j], j), default=None)
    ahead = ((j, credit_at(last[j])) for j in sorted(started) if j in last and j not in held)
    return [Credit(j, at, scored if j == latest else 0.0, tuple(sorted(later.get(j, ())))) for j, at in ahead if now < at <= lock]


def credits_of(world: state.World, league: League, fc: forecast_facts.Inputs, squad: frozenset[str], locks: Sequence[Lock]) -> list[Credit]:
    """The bonus credits that land after the cut and by the first of `locks` (none when there is none), credits_from the World's
    calendar, kickoffs and standings: `scored` the fixture points of your team in the standings (0 when the app has none), `later`
    the players of `squad` whose club has a match in the jornada that kicks off after the cut (the results of matches played are not in
    the data for hours; the points of a match under way are in `scored`)."""
    if not locks:
        return []
    last: dict[int, str] = {}
    for kick in season_facts.kickoffs(world):
        last[kick.jornada] = max(last.get(kick.jornada, kick.kick), kick.kick)
    club = {c.player: c.club for c in fc.clubs}
    coming: dict[int, set[str]] = {}
    for k in squad:
        for f in fc.fixtures.get(club.get(k) or "", ()):
            if f.kick is not None and f.kick > world.cut:
                coming.setdefault(f.jornada, set()).add(k)
    started = {c.jornada: c.lock for c in season_facts.jornada_calendar(world) if c.lock is not None and c.lock <= world.cut}
    points = [s.fixture_points for s in current.standings(world) if s.manager == league.me and s.fixture_points is not None]
    return credits_from(world.cut, min(x.lock for x in locks), started, last, {f.jornada for fs in fc.fixtures.values() for f in fs if f.kick is None},
                        {j: frozenset(ks) for j, ks in coming.items()}, points[0] if points else 0.0)


def inputs_at(world: state.World, identity: Identity, league: League, seen: Seen, fc: forecast_facts.Inputs) -> Inputs:
    """The decision's inputs at the world's cut: a pure function of the world, the seen facts and the forecast inputs,
    each fact computed once."""
    cells = [Cell(c.player, c.jornada, c.e) for c in forecast_facts.cell(fc)]
    locks = locks_of(season_facts.round_locks(world), world.cut)
    prices_at, next_lock = price_fold.price_state(world), season_facts.next_lock(world).t
    return Inputs(
        money_of(seen, league), players=players_of(seen, league), curves=curve_points_of(seen), auctions=auctions_of(seen),
        bids=bids_of(seen), closes=closes_of(season_facts.listing_close(world, identity)),
        locks=locks, lineup=slots_of(current.app_lineup(world, identity)),
        cells=cells, universe=universe_of(seen, fc.clubs, league),
        prices=prices_of(price_fold.price_forecast(prices_at, next_lock, hurdle.HOLD_NIGHTS)),
        credits=credits_of(world, league, fc, frozenset(m.player for m in my_squad(seen, league)), locks),
        nights=price_fold.price_nights(prices_at, next_lock))


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


def lock_times(inp: Inputs) -> dict[int, str]:
    return {x.jornada: x.lock for x in inp.locks}


def arrival(locks: dict[int, str], close: str | None) -> str | None:
    """The moment a player bought arrives: the close given, or when none (close None) the first lock (None when no lock is ahead)."""
    return close or min(locks.values(), default=None)


def next_close(inp: Inputs) -> str | None:
    """The earliest auction close ahead among the listings on the market (None when none is listed): no player who is
    not on the market yet can arrive before it."""
    return min((x.close for x in inp.closes), default=None)


def window_of(locks: dict[int, str], cells: dict[int, dict[str, float]], close: str | None) -> Window:
    """The Window of a player bought at close, or of one with no auction (close None) bought as soon as the next lock
    allows; over the jornadas the forecast has."""
    return window_at(locks, cells, arrival(locks, close))


def window_value(pos: dict[str, str], cells: dict[int, dict[str, float]], where: Iterable[int]) -> dict[str, float]:
    """Each player's forecast summed over the jornadas `where`; 0 for a jornada the player has no cell in."""
    gathered = tuple(where)
    return {k: sum(cells[j].get(k, 0.0) for j in gathered) for k in pos}


def curves_of(inp: Inputs) -> dict[str, Curve]:
    out: dict[str, list[tuple[float, float]]] = {}
    for c in sorted(inp.curves):
        out.setdefault(c.player, []).append((c.ratio, c.p))
    return {k: tuple(v) for k, v in out.items()}


def rises_of(inp: Inputs) -> dict[str, dict[int, float]]:
    """The price forecast's rise (expected value over value, less 1), as a fraction, by player and horizon in nights, of the
    rows with a value. The one place inp.prices is read."""
    out: dict[str, dict[int, float]] = {}
    for x in inp.prices:
        if x.value:
            out.setdefault(x.player, {})[x.h] = x.expected_value / x.value - 1
    return out


def sale_rises(inp: Inputs, steps: Mapping[int, float]) -> dict[int, float]:
    """The rows of steps (one player's rise by horizon, rises_of) a sale reads: those up to the nights to the next lock (every
    one when the inputs have none)."""
    return {h: r for h, r in steps.items() if inp.nights is None or h <= inp.nights}


def exit_of(inp: Inputs, player: str, value: float, steps: Mapping[int, float] | None = None) -> hurdle.Exit:
    """The hurdle.Exit of a player of this value: the sale's furthest horizon in nights (sale_rises) and its rise there held
    within +-hurdle.TREND_CAP (a flat Exit of one night with no forecast), and the hold, hurdle.held_rise of the rise at
    the furthest horizon within hurdle.HOLD_NIGHTS. `steps` is the player's rises_of row when the caller has it. The only reader of the
    price forecast."""
    steps = rises_of(inp).get(player, {}) if steps is None else steps
    sale = sale_rises(inp, steps)
    near = [h for h in steps if h <= hurdle.HOLD_NIGHTS]
    hold = hurdle.held_rise(value, steps[max(near)]) if near else 0.0
    if not sale:
        return hurdle.Exit(value, hold=hold)
    far = max(sale)
    return hurdle.Exit(value, far, max(-hurdle.TREND_CAP, min(hurdle.TREND_CAP, sale[far])), hold=hold)


class Funds(NamedTuple):
    """What a day's holds draw on: the cash that is left at the lock (rules.cash_at_lock of the bids placed and the expected
    credit) and the squad slots left (the squad maximum less the squad and the bids pending)."""
    cash: float
    slots: int


def funds_of(inp: Inputs) -> Funds:
    """The Funds of the inputs: the cash at the lock after every bid placed is won, and the free slots, none counting
    below 0."""
    mine = squad_of(inp)
    pos, cells = cells_of(inp)
    credit = inflow_of(inp.credits, mine, pos, cells).expected
    return Funds(cash_at_lock(inp.money.cash, 0.0, sum(b.amount for b in inp.bids), 0.0, credit),
                 max(0, inp.money.squad_max - len(mine) - len(inp.bids)))


class Hold(NamedTuple):
    """A listing that may be bought to hold: its hurdle.hold_stake, the bid hurdle.bid makes under it and the money that bid is
    expected to earn (the chance of winning x (the price expected after the hold - the bid))."""
    stake: hurdle.Stake
    bid: hurdle.Bid
    expected: float


def held_bids(funds: Funds, wanted: Mapping[str, tuple[float, float, bool]]) -> frozenset[str]:
    """The players whose hold is funded: of `wanted` (player: the expected profit, the bid, whether it is already placed), best
    profit first (then player), each that leaves cash at the lock 0 or more and a slot (a bid already placed took one and its
    amount), cash and slots taken as they go."""
    taken: set[str] = set()
    cash, slots = funds
    for k, (profit, amount, placed) in sorted(wanted.items(), key=lambda kv: (-kv[1][0], kv[0])):
        claim = 0.0 if placed else amount
        if cash_at_lock(cash, 0.0, claim, 0.0) >= 0 and (placed or slots > 0):
            taken.add(k)
            cash, slots = cash - claim, slots - (not placed)
    return frozenset(taken)


def clock_of(inp: Inputs, at: str | None = None) -> Clock:
    """The Clock (ffcore.clock.clock_at) at the stamp `at` (the cut when None) over the locks of the inputs: the closes strictly
    after it and before the next lock."""
    when = snapshot_stamp(at or cut())
    return clock_at(when, lock_times(inp).values()) if when is not None else Clock(None)


def opportunity_rows(inp: Inputs, rate: float = hurdle.FALLBACK_LAMBDA) -> list[dict[str, Any]]:
    """Each player of the universe who would play in the eleven of his window (Buyer.gain the points he adds), with his yield
    per million over the season (per_million), his hurdle.Stake over his window at rate (`stake`) and, for a listing with an
    auction curve, the bid hurdle.bid makes on it (bid and win None when there is none). A buy is for his points alone, capital
    tied the whole window, unless the listing is a hold the cash funds (held_bids: his price forecast credits a rise, cash at the
    lock and a squad slot are left): then his Stake is hurdle.hold_stake and `profit` the points his expected proceeds less the bid
    are worth (0 for any other row). A row's `window` is read from his auction's close; a player not yet on the market arrives no sooner than the
    next auction close (next_close). Most points plus profit first."""
    pos, cells = cells_of(inp)
    last = max(cells, default=0)
    mine = squad_of(inp)
    curves = curves_of(inp)
    locks = lock_times(inp)
    closes = {x.player: x.close for x in inp.closes}
    soonest = next_close(inp)
    placed = {x.player: x.amount for x in inp.bids}
    ahead = rises_of(inp)
    fielded = lineup_of(inp, pos).eleven
    seen: dict[Window, tuple[dict[str, float], eleven.Settled | None, valuation.Buyer]] = {}
    out = []
    holds: dict[str, Hold] = {}
    for c in inp.universe:
        k, status, asking, value = c.player, c.status, c.asking, c.value
        if k not in pos or not any(k in at for at in cells.values()):
            continue
        close = closes.get(k) if status == "market" else None
        where = window_of(locks, cells, close if status == "market" else soonest)
        if not where:
            continue
        if where not in seen:
            v = window_value(pos, cells, where.season)
            seen[where] = (v, eleven.best_eleven(mine, pos, v, fielded), valuation.Buyer(mine, where, pos, cells))
        v, settled, buyer = seen[where]
        if settled is None:
            continue
        got = eleven.swap(k, settled.eleven, pos, v)
        if got is None:
            continue
        gain = buyer.gain(k)
        price = asking if asking is not None else value
        steps = sale_rises(inp, ahead.get(k, {}))
        bid: float | None = None
        win: float | None = None
        n = hurdle.remaining(where.lock[0], last)
        stake = hurdle.stake(gain, n, rate, hurdle.Exit(value or 0.0), n)
        if status == "market" and asking is not None and k in curves:
            exit = exit_of(inp, k, value or 0.0, ahead.get(k, {}))
            if exit.hold > 0:
                held = hurdle.hold_stake(gain, n, rate, exit)
                free = hurdle.bid(curves[k], asking, held)
                if free is not None:
                    holds[k] = Hold(held, free, free.win * (held.proceeds - free.amount))
            made = hurdle.bid(curves[k], asking, stake)
            bid, win = (made.amount, made.win) if made is not None else (None, None)
        out.append({
            "player": k, "pos": pos[k], "status": status, "asking": asking, "close_at": close, "window": where, "from_j": where.lock[0],
            "replaces": got.replaces, "gain": gain, "per_million": gain / (price / 1e6) if price else None,
            "price_next": steps[1] if 1 in steps else None,
            "price_lock": steps[max(steps)] if steps else None,
            "bid": bid, "win": win, "placed": placed.get(k), "stake": stake, "profit": 0.0})
    funded = held_bids(funds_of(inp), {k: (h.expected, h.bid.amount, k in placed) for k, h in holds.items()})
    for r in out:
        if r["player"] in funded:
            hold = holds[r["player"]]
            r["stake"], r["bid"], r["win"] = hold.stake, hold.bid.amount, hold.bid.win
            r["profit"] = hurdle.profit(hold.stake, hold.bid.amount)
    return sorted(out, key=lambda o: (-(o["gain"] + o["profit"]), o["player"]))


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
    assert (got["star"]["bid"], got["star"]["win"]) == (10e6, 1.0), "the best win-weighted surplus under the walk-away price"
    assert got["good"]["gain"] == 6.0 and got["good"]["placed"] == 5.5e6, "a bid placed is kept"
    assert (got["good"]["bid"], got["good"]["win"]) == (6e6, 0.7), "a surer win is worth the dearer bid"
    for rate in (0.01, 0.1, 0.5, 1.0):
        for r in opportunity_rows(inp, rate):
            n = hurdle.remaining(r["from_j"], 2)
            if r["bid"] is not None:
                assert r["asking"] <= r["bid"] <= hurdle.cap(r["stake"]) == hurdle.K * r["gain"] * 1e6 / (n * rate), \
                    "a bid only on a buy, never above the walk-away price K x W"
    assert all(r["bid"] is None for r in opportunity_rows(inp, 1.0)), "at a dear enough rate nothing is bought"
    assert got["late"]["gain"] > 0 and (got["late"]["bid"], got["late"]["win"]) == (None, None), \
        "on the market with no curve: his gain is said, no bid or chance is made up"
    assert got["keeper"]["gain"] == 0.0 and got["keeper"]["replaces"] == "k", "a worse keeper would replace the keeper and adds nothing"
    assert got["late"]["gain"] == 7.0 and got["late"]["from_j"] == 2, "an auction closing after jornada 1's lock counts from jornada 2: your eleven is frozen at the lock"
    assert got["rival"]["status"] == "rival" and got["rival"]["bid"] is None, "a target is judged without being on sale"
    assert got["rival"]["gain"] == 5.0 and got["rival"]["from_j"] == 2, \
        "a target arrives at the next auction close at the earliest, here after jornada 1's lock: it cannot score jornada 1"
    early = {r["player"]: r for r in opportunity_rows(inp._replace(closes=[Close("late", "2026-11-05T21:24Z")]))}
    unknown = {r["player"]: r for r in opportunity_rows(inp._replace(closes=[]))}
    assert early["rival"]["gain"] == unknown["rival"]["gain"] == 10.0 and early["rival"]["from_j"] == unknown["rival"]["from_j"] == 1, \
        "a next close before the lock, or none known (the next lock then): the jornada that locks counts"
    assert got["rival"]["from_j"] == got["late"]["from_j"], "a target and a buy closing at the next close share a window"
    assert got["free"]["status"] == "unlisted" and got["free"]["gain"] == 0.0 and all(r["gain"] >= 0 for r in got.values()), "a gain is never negative"
    assert abs(got["star"]["price_next"] - 0.05) < 1e-12 and abs(got["star"]["price_lock"] - 0.10) < 1e-12 and got["good"]["price_next"] is None
    assert got["star"]["per_million"] == 12.0 / 8.0, "points a million of his asking price"
    assert [r["player"] for r in opportunity_rows(inp)][0] == "star", "most points first"
    short = inp._replace(players=[p for p in inp.players if p.player != "k"])
    assert opportunity_rows(short) == [], "no eleven: no opportunities"


def _holds_inputs(cash: float, risers: Mapping[str, float], squad_max: int = SQUAD_MAX, rise: float = 0.02, placed: Mapping[str, float] | None = None) -> Inputs:
    squad = {"k": "POR", "d1": "DEF", "d2": "DEF", "d3": "DEF", "m1": "MED", "m2": "MED", "m3": "MED", "m4": "MED", "f1": "DEL", "f2": "DEL", "f3": "DEL"}
    jornadas = range(1, 27)
    rows = [Candidate(k, "MED", "market", a, a) for k, a in risers.items()]
    return Inputs(
        Money(cash, 1e8, DEBT_SHARE, squad_max, XI_SIZE),
        players=[Player(k, p, 1, None) for k, p in squad.items()] + [Player(k, "MED", 0, a) for k, a in risers.items()],
        universe=rows,
        curves=[CurvePoint(k, r, p) for k in risers for r, p in ((1.0, 0.4), (1.05, 0.8), (1.1, 1.0), (1.2, 1.0))],
        bids=[Bid(k, a) for k, a in (placed or {}).items()],
        cells=[Cell(k, j, 4.0) for k in squad for j in jornadas] + [Cell(k, j, 1.0) for k in risers for j in jornadas],
        locks=[Lock(j, "2026-11-%02dT20:00Z" % (6 + 7 * (j - 1))) for j in jornadas if j < 4],
        closes=[Close(k, "2026-11-06T21:24Z") for k in risers],
        prices=[Price(k, h, a * (1 + rise * h), a) for k, a in risers.items() for h in range(1, hurdle.HOLD_NIGHTS + 1)], nights=2)


def _selftest_holds() -> None:
    risers = {"r1": 1e6, "r2": 2e6, "r3": 3e6}
    got = {r["player"]: r for r in opportunity_rows(_holds_inputs(1e7, risers))}
    for k, r in got.items():
        e = exit_of(_holds_inputs(1e7, risers), k, risers[k])
        assert r["gain"] == 0.0 and r["bid"] is not None and r["profit"] > 0 and r["asking"] <= r["bid"] <= hurdle.cap(r["stake"]) <= hurdle.held(e), \
            "a riser the cash funds is bought to hold: a bid on no points, under the price he is expected to fetch"
        assert r["stake"].k < r["stake"].n and r["profit"] == hurdle.profit(r["stake"], r["bid"])
    ranked = [r["player"] for r in opportunity_rows(_holds_inputs(1e7, risers))]
    assert ranked == sorted(risers, key=lambda k: (-got[k]["profit"], k)), "most points plus profit first"
    for cash in (-1e6, 0.0):
        broke = opportunity_rows(_holds_inputs(cash, risers))
        assert all(r["bid"] is None and r["profit"] == 0.0 and r["stake"].k == r["stake"].n for r in broke), "no cash at the lock: capital is tied, no bid on no points"
    for cash in (0.0, 2e6, 4e6, 6.5e6, 1e7):
        spent = [r for r in opportunity_rows(_holds_inputs(cash, risers)) if r["profit"] > 0]
        assert sum(r["bid"] for r in spent) <= cash + 1e-6, "the holds together leave cash at the lock 0 or more"
    for room in (0, 1, 2, 3):
        taken = [r for r in opportunity_rows(_holds_inputs(1e8, risers, 11 + room)) if r["profit"] > 0]
        assert len(taken) == min(room, len(risers)), "a hold needs a free slot, and the slots are shared"
    pending = opportunity_rows(_holds_inputs(1e7, risers, 12, placed={"r1": 1.1e6}))
    assert sum(r["profit"] > 0 for r in pending) == 1, "a bid placed holds its slot: the one slot left is taken by a hold, not by two"
    flat = opportunity_rows(_holds_inputs(1e7, risers, rise=0.0)) + opportunity_rows(_holds_inputs(1e7, risers, rise=-0.02))
    assert all(r["bid"] is None and r["profit"] == 0.0 for r in flat), "no rise, no hold"
    weak = opportunity_rows(_holds_inputs(1e7, risers, rise=0.0002))
    assert all(r["profit"] == 0.0 for r in weak), "a rise the haircut eats is not bought"
    rich = opportunity_rows(_holds_inputs(1e7, {"big": 4e7}))
    assert all(r["profit"] == 0.0 for r in rich), "a value above HOLD_VALUE has no measured rise"
    rng = random.Random(8)
    for _ in range(400):
        wanted = {"p%d" % i: (rng.uniform(0, 5), rng.uniform(1, 9), rng.random() < 0.3) for i in range(rng.randint(0, 6))}
        funds = Funds(rng.uniform(-3, 20), rng.randint(0, 4))
        funded = held_bids(funds, wanted)
        assert sum(wanted[k][1] for k in funded if not wanted[k][2]) <= max(0.0, funds.cash) + 1e-9, "the holds taken fit the cash"
        assert sum(not wanted[k][2] for k in funded) <= funds.slots
        assert held_bids(funds, {k: (2 * v[0], v[1], v[2]) for k, v in wanted.items()}) == funded, "only the order of profits counts"


def _selftest_exit_reads() -> None:
    rows = [Price("p", h, 100.0 * (1 + 0.03 * h), 100.0) for h in range(1, hurdle.HOLD_NIGHTS + 3)]
    base = Inputs(Money(0.0, 0.0, 0.0, 0, 0), prices=rows)
    wide = exit_of(base, "p", 100.0)
    assert wide.nights == hurdle.HOLD_NIGHTS + 2 and wide.rise == hurdle.TREND_CAP and abs(wide.hold - hurdle.HOLD_EDGE * 0.03 * hurdle.HOLD_NIGHTS) < 1e-12, "no nights: every row is the sale's; the hold reads HOLD_NIGHTS"
    near = exit_of(base._replace(nights=2), "p", 100.0)
    assert near.nights == 2 and near.rise == hurdle.TREND_CAP and near.hold == wide.hold, "the sale stops at the lock, the hold does not"
    assert sale_rises(base._replace(nights=2), rises_of(base)["p"]).keys() == {1, 2}
    short = exit_of(Inputs(Money(0.0, 0.0, 0.0, 0, 0), prices=rows[:3]), "p", 100.0)
    assert abs(short.hold - hurdle.HOLD_EDGE * 0.09) < 1e-12, "with fewer nights than HOLD_NIGHTS: the furthest row"
    assert exit_of(base, "q", 100.0) == hurdle.Exit(100.0) and exit_of(base, "p", 5e7).hold == 0.0 and exit_of(base, "p", 5e7).rise == hurdle.TREND_CAP


def _selftest_gain_is_value() -> None:
    rng = random.Random(7)
    seen = improving = 0
    for case in range(300):
        fielded, bench, pos, value = eleven.random_league(rng, case % 2 == 0)
        squad = fielded | bench
        newcomer = "new"
        pos = {**pos, newcomer: rng.choice(["POR", "DEF", "MED", "DEL"])}
        value = {**value, newcomer: rng.choice([0, 1, 2, 3, 4, 5, 8]) * 1.0}
        settled = eleven.best_eleven(squad, pos, value)
        if settled is None:
            continue
        cells = {1: value}
        got = valuation.delta(squad, valuation.Move(frozenset(), frozenset({newcomer}), None, None), (1,), {}, pos, cells).points
        label = eleven.swap(newcomer, settled.eleven, pos, value)
        assert got is not None and label is not None
        assert abs(got - max(label.gain, 0.0)) < 1e-9, "the swap that names who he replaces gains exactly what the Delta of the buy says"
        seen += 1
        improving += label.improves
    assert seen > 200 and improving > 20, "the property was exercised"
    for case in range(100):
        fielded, bench, pos, value = eleven.random_league(rng, False)
        squad = fielded | bench
        cells = {j: {k: rng.uniform(0, 9) for k in pos} for j in (1, 2, 3)}
        per_jornada = sum(eleven.points(squad, pos, cells[j]) or 0.0 for j in cells)
        one_eleven = eleven.points(squad, pos, window_value(pos, cells, tuple(cells))) or 0.0
        assert per_jornada >= one_eleven - 1e-9, "an eleven picked afresh in each jornada is worth at least one picked for the sum"


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


def _selftest_credits() -> None:
    cut, lock = "2026-11-21T2037Z", "2026-11-27T20:00Z"
    started = {12: "2026-11-06T2000Z", 13: "2026-11-20T2000Z"}
    last = {12: "2026-11-08T2000Z", 13: "2026-11-23T2000Z", 14: "2026-11-30T2000Z"}
    later = {13: frozenset({"a", "b"})}
    got = credits_from(cut, lock, started, last, (), later, 21.0)
    assert [(c.jornada, c.at, c.scored, c.left) for c in got] == [(13, "2026-11-24T03:40Z", 21.0, ("a", "b"))], \
        "a credit after the cut and by the lock: the jornada in progress, with its points so far and the players still to play"
    assert credits_from(cut, lock, started, last, (13,), later, 21.0) == [], "a fixture left with no kickoff: the end is unknown, no credit"
    assert credits_from(cut, "2026-11-24T03:39Z", started, last, (), later, 21.0) == [], "a credit after the lock is not counted"
    assert len(credits_from(cut, "2026-11-24T03:40Z", started, last, (), later, 21.0)) == 1, "one landing at the lock is"
    assert credits_from("2026-11-24T0340Z", lock, started, last, (), later, 21.0) == [], "one landed by the cut is in the cash already"
    assert credits_from(cut, lock, {}, last, (), later, 21.0) == [] and credits_from(cut, lock, started, {}, (), later, 21.0) == []
    ends_after = credits_from("2026-11-26T2000Z", lock, {**started, 14: "2026-11-27T2000Z"}, {**last, 14: "2026-11-27T2000Z"}, (), {}, 0.0)
    assert ends_after == [], "a jornada that has not started has no credit; one that starts at the lock ends after it"
    both = credits_from(cut, "2026-12-30T20:00Z", {**started, 14: "2026-11-27T2000Z"}, last, (), {}, 9.0)
    assert [(c.jornada, c.scored) for c in both] == [(13, 0.0), (14, 9.0)], "the points so far are those of the jornada with the latest lock"
    for wide in ("2026-12-01T00:00Z", "2027-01-01T00:00Z"):
        assert 13 in [c.jornada for c in credits_from(cut, wide, started, last, (), later, 1.0)], "a later lock never drops a credit"


def _selftest_one_reader() -> None:
    import ast
    from pathlib import Path
    src = Path(__file__).resolve().parent
    reads: dict[str, int] = {}
    for f in sorted(src.rglob("*.py")):
        for n in ast.walk(ast.parse(f.read_text())):
            if isinstance(n, ast.Attribute) and n.attr == "prices" and isinstance(n.ctx, ast.Load):
                base = n.value.id if isinstance(n.value, ast.Name) else n.value.attr if isinstance(n.value, ast.Attribute) else ""
                if base in ("inp", "inputs"):
                    reads[f.name] = reads.get(f.name, 0) + 1
    assert reads == {"decision.py": 1}, "the price forecast is read in one place: %s" % reads
    rng = random.Random(8)
    for _ in range(300):
        value = rng.uniform(1e5, 5e7)
        rows = [Price("p", h, value * (1 + rng.uniform(-0.2, 0.2)), value) for h in rng.sample(range(1, 12), rng.randint(0, 6))]
        e = exit_of(Inputs(Money(0.0, 0.0, 0.0, 0, 0), prices=rows + [Price("q", 1, 2.0, 1.0)]), "p", value)
        by = {x.h: x.expected_value / x.value - 1 for x in rows}
        assert e.value == value and (e.nights, e.rise) == ((max(by), max(-hurdle.TREND_CAP, min(hurdle.TREND_CAP, by[max(by)]))) if by else (1, 0.0)), \
            "one horizon: the furthest row's, its rise held within TREND_CAP"


def _selftest() -> None:
    _selftest_one_reader()
    _selftest_builders()
    _selftest_credits()
    _selftest_inputs_at()
    _selftest_opportunities()
    _selftest_holds()
    _selftest_exit_reads()
    _selftest_gain_is_value()
    _selftest_lineup()
    _selftest_decision_at()
    _selftest_arrivals_at()
    print("decision self-test OK")


if __name__ == "__main__":
    _selftest()
