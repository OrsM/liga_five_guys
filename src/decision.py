"""The decision as ffcore/decide.sql makes it: one universe's facts written
as its inputs, then its next move added until there is none. Every number
the plan rests on is a row there to read (dec_money, dec_options)."""
from __future__ import annotations

from contextlib import closing
from typing import Any

from ffcore.action import Action
from ffcore.db import connect, rows
from ffcore.forecast import expected_points
from ffcore.rules import DEBT_SHARE, MAX_SLOT, MIN_SLOT, SQUAD_MAX, XI_SIZE, position

from decide import Universe

__all__ = ["plan", "inputs", "after_auctions"]

INPUTS = ("dec_state", "dec_mine", "dec_player", "dec_odds", "dec_money", "dec_xp", "dec_shape", "dec_plan",
          "dec_start")


def inputs(u: Universe, buys: bool = True) -> None:
    """The universe's squad and cash as the decision's inputs, beside the
    market as seen at its cut (decide.sql dec_player_seen, dec_odds_seen,
    dec_money_seen),
    and each player's expected points a jornada left as yours (a stand-in
    fills no slot). The offers seen count only on the squad seen (a
    replay's has none, sim.with_squad). A universe made by hand (no cut)
    has its market written as seen first."""
    m = u.market
    with closing(connect()) as db, db:
        for t in INPUTS:
            db.execute("DELETE FROM %s" % t)
        if not u.cut:
            _seen_by_hand(db, u)
        db.execute("INSERT INTO dec_state VALUES (?, variant(), ?, ?, ?, ?, ?, ?)",
                   (u.cut, m.cash, int(bool(m.fetch)), int(buys), DEBT_SHARE, SQUAD_MAX, XI_SIZE))
        db.executemany("INSERT INTO dec_mine VALUES (?, ?)",
                       [(k, position(m.pos.get(k)) or p) for k, p in sorted(u.mine.items())
                        if not k.startswith("__phantom_")])
        for t in ("dec_player", "dec_odds", "dec_money"):
            db.execute("INSERT INTO %s SELECT * FROM %s_seen" % (t, t))
        keys = {k for (k, ) in db.execute("SELECT player FROM dec_player")}
        # the universe's cells: its forecast as used (forecast_used), read once when it was built
        db.executemany("INSERT INTO dec_xp VALUES (?, ?, ?)",
                       [(k, j, expected_points(cell)) for j in u.state.jornadas
                        for k, cell in u.forecaster.per_jornada.get(j, {}).items() if k in keys])
        db.executemany("INSERT INTO dec_shape VALUES (?, ?, ?)",
                       [(p, MIN_SLOT[p], MAX_SLOT[p]) for p in MIN_SLOT])


def _seen_by_hand(db: Any, u: Universe) -> None:
    """A self-test's market, as assemble.see writes one seen (cut '')."""
    m = u.market
    db.execute("DELETE FROM seen WHERE cut = ''")
    db.execute("DELETE FROM seen_odds WHERE cut = ''")
    db.executemany("INSERT INTO seen VALUES ('', variant(), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   [(k, m.shown(k), m.pos.get(k), m.owner.get(k), m.value.get(k), m.trend.get(k), m.paid.get(k),
                     m.offer.get(k), m.clause.get(k), m.price.get(k), m.route.get(k), m.my_bid.get(k), m.fetch.get(k))
                    for k in sorted(set(m.name) | set(m.pos) | set(m.price) | set(u.mine))])
    db.executemany("INSERT INTO seen_odds VALUES ('', variant(), ?, ?, ?)",
                   [(k, r, p) for k, odds in m.odds.items() for r, p in odds])


def after_auctions() -> frozenset[str]:
    """The last plan's sales to make after the auctions (decide.sql dec_when)."""
    return frozenset(k for (k,) in rows("SELECT player FROM dec_when WHERE after"))


def plan(u: Universe, sales_only: bool = False) -> list[Action]:
    """The plan decide.sql makes for this universe, its moves in order: a
    buy at its bid, a sale at what it gets (a swap's buy, then its sale). With
    sales_only, only sales (what clearing a debt takes). Every single move
    as valued before the first is kept beside it (dec_start, dec_rows)."""
    inputs(u, buys=not sales_only)
    out: list[Action] = []
    with closing(connect()) as db, db:
        db.execute("INSERT INTO dec_start SELECT b, s, bid, win, proceeds, gain, worth FROM dec_options"
                   " WHERE b = '' OR s = ''")
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

    def universe(cash: float, price: dict[str, float], odds: Any = None, extra: int = 0) -> Universe:
        """The squad (and extra bench players scoring nothing), and price on the market."""
        sq = {**squad, **{"x%02d" % i: "DEF" for i in range(extra)}}
        cs = {**cells, **{"x%02d" % i: (0.0, 1.0) for i in range(extra)}}
        return Universe(state=LeagueState({"me": sq}, js, "me"),
                        forecaster=Bootstrap({j: dict(cs) for j in js}),
                        market=Market(cash=cash, pos={**sq, "star": "MED", "keeper": "POR"}, price=price,
                                      odds=odds or {}, value={**{k: 5e6 for k in sq}, **price}))

    def labels(acts: list[Action]) -> list[str]:
        return [a.label() for a in acts]

    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        # The eleven: k, d1-d3, m1-m3, f1 (each position's least), then the best three of the rest:
        # f2, f3 and m4 (2 points); bench (1) plays for nobody. 42 points a jornada.
        inputs(universe(10e6, {"star": 8e6}))
        assert rows("SELECT gain, worth FROM dec_options WHERE b = 'star' AND s = ''") == [(12.0, 12.0)], \
            "star (8) for m4 (2), two jornadas: his points, and nothing for his price"
        assert rows("SELECT jornada, count(*), total(xp) FROM dec_eleven"
                    " GROUP BY jornada") == [(1, 11, 42.0), (2, 11, 42.0)]
        assert labels(plan(universe(10e6, {"star": 8e6}))) == ["buy star"]
        assert labels(plan(universe(10e6, {"star": 10e6}))) == ["buy star"], \
            "his price is a limit, not a cost: within cash, the points decide"
        got = plan(universe(4e6, {"star": 8e6}))
        assert [a.kind for a in got] == ["buy", "sell"] and got[1].sell[0] in ("bench", "m4"), \
            "4M short of a lock at zero: paid for by a sale of 5M of one he leaves out of the eleven"
        assert after_auctions() == set(got[1].sell), "a sale beside a bid: made once the bid is won"
        assert plan(universe(2e6, {"star": 8e6})) == [], "no one sale pays for him"
        assert rows("SELECT amount, win, in_plan FROM dec_rows WHERE player = 'star'") == [(8e6, 1.0, 0)], \
            "one the plan leaves out, at the lowest bid offered"
        got = plan(universe(10e6, {"star": 8e6}, odds={"star": ((1.0, 0.25), (1.25, 1.0), (1.3, 1.0))}))
        assert [(a.buy, a.cost) for a in got] == [("star", 10e6)], \
            "at asking a quarter of 12 (3); at 1.25 sure (12): the sure bid, the least of those as sure"
        assert labels(plan(universe(30e6, {"star": 8e6, "keeper": 6e6}))) == ["buy star"], \
            "a second keeper, worse than k, plays for nobody: not bought, however much cash"
        plan(universe(4e6, {"star": 8e6}, odds={"star": ((1.0, 0.25), (1.25, 1.0))}))
        assert rows("SELECT kind, player, amount, win, gain, in_plan FROM dec_rows"
                    " WHERE player IN ('star', 'bench', 'k') ORDER BY player") == [
            ("sell", "bench", 5e6, 1.0, 0.0, 1), ("sell", "k", 5e6, 1.0, -8.0, 0),
            ("buy", "star", 8e6, 0.25, 12.0, 1)], \
            "each move alone from the squad as it is, a buy at the plan's bid (at 4M and a sale, his asking)"
        full = plan(universe(30e6, {"star": 8e6}, extra=SQUAD_MAX - len(squad)))
        assert [a.kind for a in full] == ["buy", "sell"] and (full[1].sell[0] == "bench"
                                                               or full[1].sell[0].startswith("x")), \
            "a full squad: a sale of one who plays for nobody makes room for him"
        assert plan(universe(30e6, {}, extra=SQUAD_MAX - len(squad))) == [], \
            "a player who scores nothing is not sold for his money: money is no goal"
        assert labels(plan(universe(-3e6, {}))) == ["sell bench"], "in debt: the least bad sale"
        assert after_auctions() == set(), "no bid: the sale at once"
        assert labels(plan(universe(-12e6, {}))) == ["sell bench", "sell m4", "sell d1"], \
            "until clear: bench (0), m4 (-4: his place empty), then a slot each, by name"
        assert labels(plan(universe(10e6, {"star": 8e6}), sales_only=True)) == [], \
            "sales only: no buy, and no sale worth more than nothing"
    print("decision self-test OK")


if __name__ == "__main__":
    _selftest()
