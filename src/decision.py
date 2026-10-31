"""The decision as ffcore/decide.sql makes it: one universe's facts written
as its inputs, then its next move added until there is none. Every number
the plan rests on is a row there to read (dec_money, dec_options)."""
from __future__ import annotations

from contextlib import closing
from typing import Any

from ffcore.action import Action
from ffcore.db import connect, rows
from ffcore.forecast import expected_points
from ffcore.rules import DEBT_SHARE, MAX_SLOT, MIN_SLOT, SQUAD_MAX, position

from decide import Universe

__all__ = ["plan", "inputs"]

INPUTS = ("dec_player", "dec_xp", "dec_odds", "dec_money", "dec_shape", "dec_plan")


def inputs(u: Universe, lam: float | None = None) -> None:
    """The universe's squad, cash and market as the decision's inputs: your
    players and every player on the market you could buy (one the squad
    holds is its, not a buy: a replay's squad holds players listed now), each one's
    expected points a jornada left as yours (a stand-in fills no slot), his
    value once his price trend has run (Market.later over the longest horizon
    the fits reach: what he is worth as money), a buy's bids, your money, and
    lam: the money rate (money_rate) unless given."""
    m = u.market
    buys = [k for k in sorted(m.price) if k not in u.mine and u.acquire(k) is not None]
    h = len(m.carry)     # the longest horizon the price fits reach
    players = [(k, position(m.pos.get(k)) or u.mine.get(k, ""), 1, m.later(k, h), None, m.fetches(k))
               for k in sorted(u.mine) if not k.startswith("__phantom_")] \
        + [(k, position(m.pos.get(k)), 0, m.later(k, h) if k in m.value else m.price[k], m.price[k], None)
           for k in buys]
    keys = {p[0] for p in players}
    xp = [(k, j, expected_points(cell)) for j in u.state.jornadas
          for k, cell in u.forecaster.per_jornada.get(j, {}).items() if k in keys]
    if lam is None:
        (lam, ), = rows("SELECT coalesce(lam, 0) FROM money_rate")
    with closing(connect()) as db, db:
        for t in INPUTS:
            db.execute("DELETE FROM %s" % t)
        db.executemany("INSERT INTO dec_player VALUES (?, ?, ?, ?, ?, ?)", players)
        db.executemany("INSERT INTO dec_xp VALUES (?, ?, ?)", xp)
        db.executemany("INSERT INTO dec_odds VALUES (?, ?, ?)",
                       [(k, r, p) for k in buys for r, p in m.odds.get(k, ())])
        db.execute("INSERT INTO dec_money VALUES (?, ?, ?, ?, ?)",
                   (m.cash, sum(m.value.get(k, 0.0) for k in u.mine), lam, DEBT_SHARE, SQUAD_MAX))
        db.executemany("INSERT INTO dec_shape VALUES (?, ?, ?)",
                       [(p, MIN_SLOT[p], MAX_SLOT[p]) for p in MIN_SLOT])


def plan(u: Universe, lam: float | None = None, sales_only: bool = False) -> list[Action]:
    """The plan decide.sql makes for this universe, its moves in order: a
    buy at its bid, a sale at what it gets (a swap's buy, then its sale). With
    sales_only, only sales (what clearing a debt takes)."""
    inputs(u, lam)
    out: list[Action] = []
    with closing(connect()) as db, db:
        if sales_only:
            db.execute("DELETE FROM dec_player WHERE NOT mine")
        for step in range(SQUAD_MAX * 2):
            got = db.execute("SELECT b, s, bid, win, proceeds, worth FROM dec_next").fetchone()
            if got is None:
                break
            b, s, bid, win, proceeds, worth = got
            if b:
                db.execute("INSERT INTO dec_plan VALUES (?, 'buy', ?, ?, ?, ?)", (step, b, bid, win, worth))
            if s:
                db.execute("INSERT INTO dec_plan VALUES (?, 'sell', ?, ?, 1.0, ?)", (step, s, proceeds, worth))
            if b:
                out.append(Action("buy", buy=b, cost=bid))
            if s:
                out.append(Action("sell", sell=(s, ), proceeds=proceeds))
    return out


def _selftest() -> None:
    import tempfile
    from pathlib import Path

    from decide import Universe
    from ffcore.forecast import Bootstrap
    from ffcore.market import Market
    from ffcore.season import LeagueState
    from ffcore.tidy import tables_in

    js = [1, 2]
    squad = {"k": "POR", "d1": "DEF", "d2": "DEF", "d3": "DEF", "m1": "MED", "m2": "MED", "m3": "MED",
             "m4": "MED", "f1": "DEL", "f2": "DEL", "f3": "DEL", "bench": "DEF"}
    cells: dict[str, tuple[float, float]] = {k: (4.0, 1.0) for k in squad}
    cells.update({"m4": (2.0, 1.0), "bench": (1.0, 1.0), "star": (8.0, 1.0), "keeper": (3.0, 1.0)})

    def universe(cash: float, price: dict[str, float], odds: Any = None, value: Any = None) -> Universe:
        pos = {**squad, "star": "MED", "keeper": "POR"}
        return Universe(state=LeagueState({"me": dict(squad)}, js, "me"),
                        forecaster=Bootstrap({j: dict(cells) for j in js}),
                        market=Market(cash=cash, pos=pos, price=price, odds=odds or {},
                                      value=value or {**{k: 5e6 for k in squad}, **price}))

    def labels(acts: list[Action]) -> list[str]:
        return [a.label() for a in acts]

    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        # The eleven: k, d1-d3, m1-m3, f1 (each position's least), then the best three of the rest:
        # f2, f3 and m4 (2 points); bench (1) plays for nobody. 42 points a jornada.
        inputs(universe(10e6, {"star": 8e6}), lam=1.0)
        assert rows("SELECT gain, money FROM dec_options WHERE b = 'star' AND s = ''") == [(12.0, 0.0)], \
            "star (8) for m4 (2), two jornadas; bought at his value: no money lost"
        assert rows("SELECT jornada, count(*), total(xp) FROM dec_eleven WHERE b = '' AND s = ''"
                    " GROUP BY jornada") == [(1, 11, 42.0), (2, 11, 42.0)]
        assert labels(plan(universe(10e6, {"star": 8e6}), lam=1.0)) == ["buy star"]
        dear = {**{k: 5e6 for k in squad}, "star": 4e6}
        assert labels(plan(universe(10e6, {"star": 8e6}, value=dear), lam=1.0)) == ["buy star"], \
            "a 4M premium at 1 point a million: 12 - 4"
        assert plan(universe(10e6, {"star": 8e6}, value=dear), lam=3.0) == [], \
            "at 3 points a million the premium costs all he adds: 12 - 12 is not worth more than nothing"
        got = plan(universe(4e6, {"star": 8e6}), lam=1.0)
        assert [a.kind for a in got] == ["buy", "sell"] and got[1].sell[0] in ("bench", "m4"), \
            "4M short of a lock at zero: paid for by a sale of 5M of one he leaves out of the eleven"
        assert plan(universe(2e6, {"star": 8e6}), lam=1.0) == [], "no one sale pays for him"
        got = plan(universe(10e6, {"star": 8e6}, odds={"star": ((1.0, 0.25), (1.25, 1.0))}), lam=1.0)
        assert [(a.buy, a.cost) for a in got] == [("star", 10e6)], \
            "at asking a quarter of 12 (3); at 1.25 sure, 12 - 2 = 10: the sure bid"
        assert labels(plan(universe(30e6, {"star": 8e6, "keeper": 6e6}), lam=0.0)) == ["buy star"], \
            "a second keeper, worse than k, plays for nobody: money at nothing, still not bought"
        assert labels(plan(universe(-3e6, {}), lam=1.0)) == ["sell bench"], "in debt: the least bad sale"
        assert labels(plan(universe(-12e6, {}), lam=1.0)) == ["sell bench", "sell m4", "sell d1"], \
            "until clear: bench (0), m4 (-4: his place empty), then a slot each, by name"
        assert labels(plan(universe(10e6, {"star": 8e6}), lam=1.0, sales_only=True)) == [], \
            "sales only: no buy, and no sale worth more than nothing"
    print("decision self-test OK")


if __name__ == "__main__":
    _selftest()
