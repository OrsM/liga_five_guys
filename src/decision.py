"""The decision's inputs: the league at t (schema.sql look, my_squad, my_cash) made into the rows the board and
approach.approach read (decide.sql dec_*), and the players on the market who would improve your eleven."""
from __future__ import annotations

from contextlib import closing
from typing import Any, NamedTuple, Sequence

import eleven
from ffcore.db import connect, record, rows
from ffcore.auction import Curve, bid_for
from ffcore.rules import BID_TARGET, DEBT_SHARE, POSITIONS, SQUAD_MAX, XI_SIZE, in_force

__all__ = ["inputs", "Inputs", "read_inputs", "squad_of", "Lineup", "lineup_of", "cells_of", "window_value", "curves_of", "opportunity_rows", "keep_opportunities", "seen_cuts", "read_arrivals",
           "Fact", "UNKNOWN", "player_facts", "Offer", "standing_offers", "Held", "held", "listed", "market_read_at",
           "Source", "source_health", "Brier", "Drift", "latest_auction_run", "brier_by_scope", "drifting_bins", "manager_count"]

INPUTS = ("dec_state", "dec_player", "dec_curve", "dec_auction", "dec_money", "dec_bid", "dec_close", "dec_lock", "dec_lineup")


def inputs() -> None:
    """What the decision looks at, the league at t (look: the market seen, the forecast, your squad and cash then),
    made once a decision (decide.sql dec_player_seen, dec_curve_seen, dec_money_seen, dec_bid_seen)."""
    with closing(connect()) as db, db:
        for t in INPUTS:
            db.execute("DELETE FROM %s" % t)
        db.execute("INSERT INTO dec_state VALUES (?, ?, ?)", (DEBT_SHARE, SQUAD_MAX, XI_SIZE))
        for t in INPUTS[1:]:
            db.execute("INSERT INTO %s SELECT * FROM %s_seen" % (t, t))


class Player(NamedTuple):
    player: str
    pos: str
    mine: int
    asking: float | None
    proceeds: float | None


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


def read_inputs() -> Inputs:
    """inputs() made, and read back, each row under its columns' names."""
    inputs()
    with closing(connect()) as db:
        def table(kind: Any, name: str) -> list[Any]:
            return [kind(*r) for r in db.execute("SELECT %s FROM %s" % (", ".join(kind._fields), name))]
        (money,) = table(Money, "dec_money")
        return Inputs(
            money, players=table(Player, "dec_player"), curves=table(CurvePoint, "dec_curve"), auctions=table(Auction, "dec_auction"),
            bids=table(Bid, "dec_bid"), closes=table(Close, "dec_close"), locks=table(Lock, "dec_lock"),
            lineup=table(Slot, "dec_lineup"), cells=table(Cell, "cell"),
            universe=[Candidate(*r) for r in db.execute(
                "SELECT s.player, coalesce(nullif(c.pos, ''), s.pos, 'MED') AS pos,"
                " CASE WHEN s.owner IS NULL AND s.price IS NOT NULL THEN 'market' WHEN s.owner IS NULL THEN 'unlisted'"
                " ELSE 'rival' END AS status, s.price AS asking, s.value"
                " FROM seen s JOIN look l ON s.cut = l.cut AND s.variant = l.variant LEFT JOIN player_club c ON c.player = s.player"
                " WHERE s.owner IS NULL OR s.owner <> l.me")],
            prices=[Price(*r) for r in db.execute("SELECT ff_id AS player, h, expected_value, value FROM price_forecast")])


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
        if status == "market" and asking is not None:
            ratio, win = bid_for(curves[k], BID_TARGET) if k in curves else (1.0, 1.0)
            bid = asking * ratio
        out.append({
            "player": k, "pos": pos[k], "status": status, "asking": asking, "close_at": close, "from_j": min(where),
            "replaces": got.replaces, "gain": got.gain, "per_million": got.gain / (price / 1e6) if price else None,
            "price_next": rise(steps[1]) if 1 in steps else None,
            "price_lock": rise(steps[max(steps)]) if steps else None,
            "bid": bid, "win": win, "placed": placed.get(k)})
    return sorted(out, key=lambda o: (-o["gain"], o["player"]))


def keep_opportunities(cut: str, got: list[dict[str, Any]]) -> None:
    with closing(connect()) as db, db:
        db.execute("DELETE FROM dec_opportunity WHERE cut = ?", (cut,))
    record([{"cut": cut, **r} for r in got], "dec_opportunity")


def seen_cuts() -> list[str]:
    with closing(connect()) as db:
        return [c for (c,) in db.execute("SELECT DISTINCT cut FROM seen ORDER BY cut")]


def read_arrivals() -> list[str]:
    with closing(connect()) as db:
        return [k for (k,) in db.execute("SELECT player FROM dec_arrivals ORDER BY rank").fetchall()]


# What the board shows beside the decision: facts about players, the offers on your players, who is listed, how fresh
# each source is, how the auction forecast has done. Each selector is named for what it returns.

class Fact(NamedTuple):
    name: str
    pos: str | None
    trend: float | None


UNKNOWN = Fact("", None, None)


def player_facts() -> dict[str, Fact]:
    return {k: Fact(name, pos, trend) for k, name, pos, trend in rows("SELECT player, name_shown(name), pos, trend FROM player_facts")}


class Offer(NamedTuple):
    player: str
    value: float
    money: float


def standing_offers(mine: frozenset[str]) -> list[Offer]:
    """The pending offers, money not nil, on those of your players."""
    return [Offer(*r) for r in rows(
        "SELECT t.player, t.market_value, o.money FROM offers o"
        " JOIN squads t ON t.player_team_id = o.player_team_id"
        " WHERE o.status = 'pending' AND o.money <> 0 AND t.player IN (%s)" % ",".join("?" * len(mine)), sorted(mine))]


class Held(NamedTuple):
    player: str
    player_team: str
    player_id: str | None
    value: float


def held(mine: frozenset[str]) -> list[Held]:
    """Those of your players in the squads table, with the ids the app knows them by and their market value."""
    return [Held(*r) for r in rows(
        "SELECT t.player, t.player_team_id, t.player_id, t.market_value FROM squads t"
        " WHERE t.player IN (%s)" % ",".join("?" * len(mine)), sorted(mine))]


def listed() -> frozenset[str]:
    return frozenset(k for (k,) in rows("SELECT player FROM listings WHERE player IS NOT NULL"))


def market_read_at() -> str | None:
    """The stamp of the market read at t, if there is one."""
    (at,), = rows("SELECT at FROM market_read")
    return at or None


class Source(NamedTuple):
    src: str
    last_at: str
    hours: float
    status: str


def source_health() -> list[Source]:
    return [Source(*r) for r in rows("SELECT src, last_at, hours_since, status FROM parser_health ORDER BY src")]


class Brier(NamedTuple):
    scope: str
    n: int
    model: float
    pooled: float


class Drift(NamedTuple):
    scope: str
    bin: str
    n: int
    stated: float
    observed: float


def latest_auction_run() -> int | None:
    """The run that last measured the auction forecast (auction_brier), None if none has."""
    (run,), = rows("SELECT max(run_id) FROM auction_brier")
    return run


def brier_by_scope(run: int) -> list[Brier]:
    return [Brier(*r) for r in rows("SELECT scope, n, model, pooled FROM auction_brier WHERE run_id = ? ORDER BY scope", (run,))]


def drifting_bins(run: int) -> list[Drift]:
    return [Drift(*r) for r in rows(
        "SELECT scope, bin, n, stated, observed FROM auction_calibration WHERE run_id = ? AND drift = 1 ORDER BY scope, bin", (run,))]


def manager_count() -> int:
    (n,), = rows("SELECT count(*) FROM cash")
    return int(n)


_NO_MONEY = Money(0.0, 0.0, 0.0, SQUAD_MAX, XI_SIZE)


def _selftest_opportunities() -> None:
    squad = {"k": "POR", "d1": "DEF", "d2": "DEF", "d3": "DEF", "m1": "MED", "m2": "MED", "m3": "MED",
             "m4": "MED", "f1": "DEL", "f2": "DEL", "f3": "DEL"}
    e = {**{k: 4.0 for k in squad}, "m4": 2.0, "star": 8.0, "good": 5.0, "keeper": 3.0, "late": 9.0, "rival": 7.0, "free": 1.0}
    kinds = {"star": "MED", "good": "MED", "keeper": "POR", "late": "MED", "rival": "MED", "free": "MED"}
    inp = Inputs(
        _NO_MONEY,
        players=[Player(k, p, 1, None, 5e6) for k, p in squad.items()]
        + [Player(k, kinds[k], 0, a, None) for k, a in (("star", 8e6), ("good", 5e6), ("keeper", 3e6), ("late", 6e6))],
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
        return Inputs(_NO_MONEY, players=[Player(k, p, 1, None, 1e6) for k, p in squad.items()],
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
    gone = lineup(fielded)._replace(players=[Player(k, p, int(k != "f3"), None, 1e6) for k, p in squad.items()])
    assert lineup_of(gone, pos).eleven is None and "no longer yours" in lineup_of(gone, pos).message
    flat = {k: 4.0 for k in squad}
    swapped = ["k", "d1", "d2", "d3", "bench", "m1", "m2", "m3", "f1", "f2", "f3"]
    value = flat | {"d4": 6.0, "bench": 1.0}
    best = eleven.best_eleven(frozenset(squad), pos, value, lineup_of(lineup(swapped), pos).eleven)
    assert best is not None and "d4" in best.eleven and best.swaps == (("d4", "bench", 5.0),), "a bench player who beats a fielded one is the advice"
    top = eleven.best_eleven(frozenset(squad), pos, value)
    assert top is not None and eleven.total(top.eleven, value) == eleven.total(best.eleven, value), "settling from the app's eleven reaches the best"


def _selftest_arrivals() -> None:
    import tempfile
    from pathlib import Path

    from ffcore.db import cut, set_now
    from ffcore.parse import snapshot_stamp
    from ffcore.tidy import tables_in

    def row(k: str, status: str, gain: float) -> dict[str, Any]:
        return {"player": k, "pos": "MED", "status": status, "asking": 1e6 if status == "market" else None, "close_at": None,
                "from_j": 1, "replaces": "x", "gain": gain, "per_million": None, "price_next": None, "price_lock": None,
                "bid": None, "win": None, "placed": None}

    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        try:
            set_now(snapshot_stamp("2026-09-03T0000Z"))
            keep_opportunities(cut(), [row("a", "market", 5.0), row("b", "rival", 4.0)])
            assert read_arrivals() == [], "the first night has no night before it"
            set_now(snapshot_stamp("2026-09-04T0000Z"))
            keep_opportunities(cut(), [row("a", "market", 5.0), row("b", "market", 4.0), row("c", "unlisted", 3.0)])
            assert read_arrivals() == ["b"], "b is on the market now and was not last night; a was"
            keep_opportunities(cut(), [row("b", "market", 4.0)])
            assert read_arrivals() == ["b"], "the same night kept again replaces it"
            set_now(snapshot_stamp("2026-09-05T0000Z"))
            keep_opportunities(cut(), [row("b", "market", 4.0), row("a", "market", 0.0)])
            assert read_arrivals() == [], "b stayed on the market; a no longer adds points"
        finally:
            set_now(None)


def _selftest() -> None:
    _selftest_opportunities()
    _selftest_lineup()
    _selftest_arrivals()
    print("decision self-test OK")


if __name__ == "__main__":
    _selftest()
