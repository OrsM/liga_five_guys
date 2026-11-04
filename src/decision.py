"""The decision as ffcore/decide.sql makes it: the league at t (schema.sql
look, my_squad, my_cash) made into its inputs, then its next move added
until there is none. Every number the plan rests on is a row there to read
(dec_money, dec_options)."""
from __future__ import annotations

from contextlib import closing
from typing import Any

from ffcore.action import Action
from ffcore.db import connect, rows, set_look
from ffcore.rules import DEBT_SHARE, SQUAD_MAX, XI_SIZE

from assemble import write_constants
from decide import Universe

__all__ = ["plan", "inputs", "after_auctions"]

INPUTS = ("dec_state", "dec_player", "dec_odds", "dec_money", "dec_plan", "dec_start")


def inputs(u: Universe | None = None, buys: bool = True) -> None:
    """What the decision looks at, the league at t (look: the market seen,
    the forecast, your squad and cash then), made once a decision
    (decide.sql dec_player_seen, dec_odds_seen, dec_money_seen); buys, or
    sales only. A league made by hand (u, a self-test's) is written first
    as assemble writes the league at t (_by_hand)."""
    if u is not None:
        write_constants()   # the model's constants the views read, as a forecast writes them
    with closing(connect()) as db, db:
        for t in INPUTS:
            db.execute("DELETE FROM %s" % t)
        if u is not None:
            _by_hand(db, u)
        db.execute("INSERT INTO dec_state VALUES (?, ?, ?, ?)", (int(buys), DEBT_SHARE, SQUAD_MAX, XI_SIZE))
        for t in ("dec_player", "dec_odds", "dec_money"):
            db.execute("INSERT INTO %s SELECT * FROM %s_seen" % (t, t))


def _by_hand(db: Any, u: Universe) -> None:
    """A self-test's league at t, as assemble writes it: seen and seen_cash
    at t (your squad yours there), forecast_cell at moment 0, and look on
    them, with no path."""
    m = u.market
    owner = {**{k: mgr for mgr, sq in u.state.squads.items() for k in sq}, **m.owner}
    pos = {**{k: p for sq in u.state.squads.values() for k, p in sq.items()}, **m.pos}
    set_look(moment=0, me=u.me, path="", t0="")
    for t in ("seen", "seen_cash", "seen_odds"):
        db.execute("DELETE FROM %s WHERE cut = (SELECT cut()) AND variant = variant()" % t)
    db.execute("DELETE FROM forecast_cell WHERE moment = 0")
    db.executemany("INSERT INTO seen VALUES ((SELECT cut()), variant(), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '')",
                   [(k, m.shown(k), pos.get(k), owner.get(k), m.value.get(k), m.trend.get(k), m.paid.get(k),
                     m.offer.get(k), m.clause.get(k), m.price.get(k), m.route.get(k), m.my_bid.get(k), m.fetch.get(k))
                    for k in sorted(set(m.name) | set(m.pos) | set(m.price) | set(u.mine) | set(owner))])
    db.execute("INSERT INTO seen_cash VALUES ((SELECT cut()), variant(), ?, ?)", (u.me, m.cash))
    db.executemany("INSERT INTO seen_odds VALUES ((SELECT cut()), variant(), ?, ?, ?)",
                   [(k, r, p) for k, odds in m.odds.items() for r, p in odds])
    db.executemany("INSERT INTO forecast_cell (moment, player, jornada, pts, p) VALUES (0, ?, ?, ?, ?)",
                   [(k, j, pts, p) for j in u.state.jornadas
                    for k, (pts, p) in u.forecaster.per_jornada.get(j, {}).items()])


def after_auctions() -> frozenset[str]:
    """The last plan's sales to make after the auctions (decide.sql dec_when)."""
    return frozenset(k for (k,) in rows("SELECT player FROM dec_when WHERE after"))


def plan(u: Universe | None = None, sales_only: bool = False) -> list[Action]:
    """The plan decide.sql makes at t (or for a league by hand, u), its
    moves in order: a buy at its bid, a sale at what it gets (a swap's buy,
    then its sale). With sales_only, only sales (what clearing a debt
    takes). Every single move as valued before the first is kept beside it
    (dec_start, dec_rows)."""
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
    import math
    import tempfile
    from statistics import NormalDist
    from pathlib import Path

    from decide import Universe
    from ffcore.forecast import Bootstrap
    from ffcore.market import Market
    from ffcore.season import LeagueState
    from ffcore.tidy import store_sql, tables_in

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

        # The standings: you (42 a jornada) and a rival of eight at 2 each, short of 5-4-1 by two
        # defenders and a midfielder: stand-ins at the average of each position's players (19/7, 20/7),
        # and 10 points banked.
        riv = {"rk": "POR", **{"rd%d" % i: "DEF" for i in range(3)}, **{"rm%d" % i: "MED" for i in range(3)},
               "rf": "DEL"}
        league = Universe(state=LeagueState({"me": squad, "riv": riv}, js, "me"),
                          forecaster=Bootstrap({j: {**cells, **{k: (2.0, 1.0) for k in riv}} for j in js}),
                          market=Market(cash=10e6, pos={**squad, **riv}, value={k: 5e6 for k in squad}))
        store_sql("api_standings", [{"observed_at": "2026-08-01T0000Z", "manager": "riv", "team_points": "10"}])
        inputs(league)
        st = {m: r for m, *r in rows("SELECT manager, mean, lo, hi, finish, p_first, p_above FROM league_standings")}
        assert abs(st["me"][0] - 2 * 42) < 1e-9 and abs(st["riv"][0] - (10 + 2 * (8 * 2 + 2 * 19 / 7 + 20 / 7))) < 1e-9, \
            "each one's eleven, a stand-in for each player short, and the points banked"
        (mean, lo, hi, finish, first, _), (r_mean, _, r_hi, _, r_first, r_above) = st["me"], st["riv"]
        z = NormalDist().inv_cdf(0.9)
        assert lo < mean < hi and abs(r_above - NormalDist().cdf(
            (r_mean - mean) / math.hypot((hi - mean) / z, (r_hi - r_mean) / z))) < 1e-12, \
            "his chance of ending above you: the difference of two normals"
        assert abs(finish - (1 + r_above)) < 1e-12 and abs(first - (1 - r_above)) < 1e-3 \
            and abs(first + r_first - 1) < 1e-3, "your finish and chance of first, against him alone"
    print("decision self-test OK")


if __name__ == "__main__":
    _selftest()
