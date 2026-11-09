"""The decision's inputs: the league at t (schema.sql look, my_squad, my_cash) made into the rows the board and
approach.approach read (decide.sql dec_*), and the players on the market who would improve your eleven."""
from __future__ import annotations

from contextlib import closing
from typing import Any, NamedTuple

import eleven
from ffcore.db import connect, record
from ffcore.rules import DEBT_SHARE, POSITIONS, SQUAD_MAX, XI_SIZE, bid_to_use, in_force

__all__ = ["inputs", "read_inputs", "Lineup", "lineup_of", "cells_of", "window_value", "win_at", "opportunity_rows", "keep_opportunities", "seen_cuts", "read_arrivals"]

INPUTS = ("dec_state", "dec_player", "dec_odds", "dec_money", "dec_bid", "dec_close", "dec_lock", "dec_offer", "dec_lineup")


def inputs() -> None:
    """What the decision looks at, the league at t (look: the market seen, the forecast, your squad and cash then),
    made once a decision (decide.sql dec_player_seen, dec_odds_seen, dec_money_seen, dec_bid_seen)."""
    with closing(connect()) as db, db:
        for t in INPUTS:
            db.execute("DELETE FROM %s" % t)
        db.execute("INSERT INTO dec_state VALUES (?, ?, ?)", (DEBT_SHARE, SQUAD_MAX, XI_SIZE))
        for t in INPUTS[1:]:
            db.execute("INSERT INTO %s SELECT * FROM %s_seen" % (t, t))


def read_inputs() -> dict[str, list[tuple[Any, ...]]]:
    """inputs() made, and read back: every table of it, the cells (player, jornada, e), every player who is not yours
    with his status (market, rival, unlisted), and each player's price forecast."""
    inputs()
    with closing(connect()) as db:
        inp = {t: db.execute("SELECT * FROM %s" % t).fetchall() for t in INPUTS[1:]}
        inp["cell"] = db.execute("SELECT player, jornada, e FROM cell").fetchall()
        inp["dec_universe"] = db.execute(
            "SELECT s.player, coalesce(nullif(c.pos, ''), s.pos, 'MED'),"
            " CASE WHEN s.owner IS NULL AND s.price IS NOT NULL THEN 'market' WHEN s.owner IS NULL THEN 'unlisted'"
            " ELSE 'rival' END, s.price, s.value"
            " FROM seen s JOIN look l ON s.cut = l.cut AND s.variant = l.variant LEFT JOIN player_club c ON c.player = s.player"
            " WHERE s.owner IS NULL OR s.owner <> l.me").fetchall()
        inp["price"] = db.execute("SELECT ff_id, h, expected_value, value FROM price_forecast").fetchall()
    return inp


class Lineup(NamedTuple):
    eleven: frozenset[str] | None
    message: str
    unmapped: tuple[str, ...]


def lineup_of(inp: dict[str, list[tuple[Any, ...]]], pos: dict[str, str]) -> Lineup:
    rows_ = inp["dec_lineup"]
    mine = frozenset(k for k, _, m, *_ in inp["dec_player"] if m)
    unmapped = tuple(sorted(name for _, player, _, name in rows_ if player is None))
    fielded = frozenset(player for _, player, _, _ in rows_ if player is not None)
    if not rows_:
        return Lineup(None, "the app has no line-up for you: you don't have an eleven", unmapped)
    if unmapped:
        return Lineup(None, "the app's line-up has players we cannot match: " + ", ".join(unmapped), unmapped)
    if not fielded <= mine:
        return Lineup(None, "the app fields a player who is no longer yours", unmapped)
    if not eleven.legal(fielded, pos):
        return Lineup(None, "the app's line-up is not a legal eleven (%d players)" % len(fielded), unmapped)
    return Lineup(fielded, "", unmapped)


def cells_of(inp: dict[str, list[tuple[Any, ...]]]) -> tuple[dict[str, str], dict[int, dict[str, float]]]:
    pos = {k: p for k, p, *_ in inp["dec_player"] if p in POSITIONS}
    pos |= {k: p for k, p, *_ in inp.get("dec_universe", []) if p in POSITIONS}
    cells: dict[int, dict[str, float]] = {}
    for k, j, e in inp["cell"]:
        if k in pos:
            cells.setdefault(j, {})[k] = e
    return pos, cells


def window_value(pos: dict[str, str], cells: dict[int, dict[str, float]], where: frozenset[int]) -> dict[str, float]:
    return {k: sum(cells[j].get(k, 0.0) for j in where) for k in pos}


def win_at(asking: float, odds: list[tuple[float, float]], amount: float) -> float:
    below = [(r, w) for r, w in sorted(odds) if asking * r <= amount + 1e-6]
    return below[-1][1] if below else (min(odds)[1] if odds else 1.0)


def opportunity_rows(inp: dict[str, list[tuple[Any, ...]]]) -> list[dict[str, Any]]:
    pos, cells = cells_of(inp)
    mine = frozenset(k for k, _, m, *_ in inp["dec_player"] if m)
    odds: dict[str, list[tuple[float, float]]] = {}
    for k, r, w in sorted(inp["dec_odds"]):
        odds.setdefault(k, []).append((r, w))
    locks, closes, placed = dict(inp["dec_lock"]), dict(inp["dec_close"]), dict(inp["dec_bid"])
    soon = min(locks.values(), default=None)
    ahead: dict[str, dict[int, tuple[float, float]]] = {}
    for k, h, expected, value in inp["price"]:
        if value:
            ahead.setdefault(k, {})[h] = (expected, value)
    fielded = lineup_of(inp, pos).eleven
    seen: dict[frozenset[int], tuple[dict[str, float], eleven.Settled | None]] = {}
    out = []
    for k, _, status, asking, value in inp["dec_universe"]:
        if k not in pos or not any(k in c for c in cells.values()):
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
            bid, win = bid_to_use(asking, odds.get(k, []))
        out.append({
            "player": k, "pos": pos[k], "status": status, "asking": asking, "close_at": close, "from_j": min(where),
            "replaces": got.replaces, "gain": got.gain, "per_million": got.gain / (price / 1e6) if price else None,
            "price_next": steps[1][0] / steps[1][1] - 1 if 1 in steps else None,
            "price_lock": steps[max(steps)][0] / steps[max(steps)][1] - 1 if steps else None,
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


def _selftest_opportunities() -> None:
    squad = {"k": "POR", "d1": "DEF", "d2": "DEF", "d3": "DEF", "m1": "MED", "m2": "MED", "m3": "MED",
             "m4": "MED", "f1": "DEL", "f2": "DEL", "f3": "DEL"}
    e = {**{k: 4.0 for k in squad}, "m4": 2.0, "star": 8.0, "good": 5.0, "keeper": 3.0, "late": 9.0, "rival": 7.0, "free": 1.0}
    kinds = {"star": "MED", "good": "MED", "keeper": "POR", "late": "MED", "rival": "MED", "free": "MED"}
    inp: dict[str, list[tuple[Any, ...]]] = {
        "dec_player": [(k, p, 1, None, 5e6) for k, p in squad.items()]
                      + [(k, kinds[k], 0, a, None) for k, a in (("star", 8e6), ("good", 5e6), ("keeper", 3e6), ("late", 6e6))],
        "dec_universe": [("star", "MED", "market", 8e6, 8e6), ("good", "MED", "market", 5e6, 5e6),
                         ("keeper", "POR", "market", 3e6, 3e6), ("late", "MED", "market", 6e6, 6e6),
                         ("rival", "MED", "rival", None, 9e6), ("free", "MED", "unlisted", None, 1e6)],
        "dec_odds": [("star", 1.0, 0.25), ("star", 1.25, 1.0), ("star", 1.3, 1.0), ("good", 1.0, 0.5), ("good", 1.2, 0.7)],
        "dec_bid": [("good", 5.5e6)],
        "price": [("star", 1, 10.5e6, 10e6), ("star", 3, 11e6, 10e6)],
        "cell": [(k, j, v) for k, v in e.items() for j in (1, 2)],
        "dec_lock": [(1, "2026-11-06T20:00Z"), (2, "2026-11-13T20:00Z")],
        "dec_close": [("late", "2026-11-06T21:24Z")], "dec_lineup": []}
    got = {r["player"]: r for r in opportunity_rows(inp)}
    assert got["star"]["gain"] == 12.0 and got["star"]["replaces"] == "m4", "star for m4 is 6 a jornada"
    assert (got["star"]["bid"], got["star"]["win"]) == (10e6, 1.0), "the least bid that reaches the target chance"
    assert got["good"]["gain"] == 6.0 and got["good"]["placed"] == 5.5e6, "a bid placed is kept"
    assert win_at(5e6, [(1.0, 0.5), (1.2, 0.7)], 5.5e6) == 0.5 and win_at(5e6, [(1.0, 0.5), (1.2, 0.7)], 6e6) == 0.7
    assert got["keeper"]["gain"] == -2.0 and got["keeper"]["replaces"] == "k", "a worse keeper would replace the keeper, for less"
    assert got["late"]["gain"] == 7.0 and got["late"]["from_j"] == 2, "an auction closing after jornada 1's lock counts from jornada 2: your eleven is frozen at the lock"
    assert got["rival"]["status"] == "rival" and got["rival"]["bid"] is None and got["rival"]["gain"] == 10.0, "a target is judged without being on sale"
    assert got["free"]["status"] == "unlisted" and got["free"]["gain"] < 0
    assert abs(got["star"]["price_next"] - 0.05) < 1e-12 and abs(got["star"]["price_lock"] - 0.10) < 1e-12 and got["good"]["price_next"] is None
    assert got["star"]["per_million"] == 12.0 / 8.0, "points a million of his asking price"
    assert [r["player"] for r in opportunity_rows(inp)][0] == "star", "most points first"
    assert bid_to_use(10.0, [(1.0, 0.5), (1.1, 0.95), (1.2, 0.99)]) == (11.0, 0.95)
    assert bid_to_use(10.0, [(1.0, 0.5), (1.1, 0.7)]) == (11.0, 0.7), "none reaches the target: the likeliest"
    assert bid_to_use(10.0, []) == (10.0, 1.0) and bid_to_use(10.0, [(0.9, 1.0)]) == (10.0, 1.0), "never below the asking price"
    short = dict(inp, dec_player=[row for row in inp["dec_player"] if row[0] != "k"])
    assert opportunity_rows(short) == [], "no eleven: no opportunities"


def _selftest_lineup() -> None:
    squad = {"k": "POR", "d1": "DEF", "d2": "DEF", "d3": "DEF", "d4": "DEF", "m1": "MED", "m2": "MED", "m3": "MED",
             "f1": "DEL", "f2": "DEL", "f3": "DEL", "bench": "DEF", "kb": "POR"}
    fielded = ["k", "d1", "d2", "d3", "d4", "m1", "m2", "m3", "f1", "f2", "f3"]
    slots = {k: squad[k] for k in squad}

    def lineup(names: list[str], extra: list[tuple[Any, ...]] | None = None) -> dict[str, list[tuple[Any, ...]]]:
        return {"dec_player": [(k, p, 1, None, 1e6) for k, p in squad.items()],
                "dec_lineup": [(str(i), k, slots[k], k) for i, k in enumerate(names)] + (extra or [])}
    pos = dict(squad)
    got = lineup_of(lineup(fielded), pos)
    assert got.eleven == frozenset(fielded) and got.message == "" and got.unmapped == ()
    none = lineup_of(lineup([]), pos)
    assert none.eleven is None and "don't have an eleven" in none.message, "no line-up in the app: you don't have an eleven"
    unmatched = lineup_of(lineup(fielded[:10], [("99", None, "DEL", "Ghost")]), pos)
    assert unmatched.eleven is None and unmatched.unmapped == ("Ghost",), "an app id we cannot match is named, never dropped"
    short = lineup_of(lineup(fielded[:10]), pos)
    assert short.eleven is None and "not a legal eleven" in short.message
    gone = dict(lineup(fielded), dec_player=[(k, p, int(k != "f3"), None, 1e6) for k, p in squad.items()])
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
