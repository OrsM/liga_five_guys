"""The decision as ffcore/decide.sql makes it: the league at t (schema.sql
look, my_squad, my_cash) made into its inputs, then its next move added
until there is none. Every number the plan rests on is a row there to read
(dec_money, dec_start)."""
from __future__ import annotations

from contextlib import closing
from typing import Any

from ffcore.db import connect, rows
from ffcore.rules import DEBT_SHARE, POSITIONS, SQUAD_MAX, XI_SIZE, best_eleven

__all__ = ["plan", "plan_rows", "inputs", "read_inputs", "worth_on", "measure", "expected_gap", "options", "record", "KEEP_PLAN"]

INPUTS = ("dec_state", "dec_player", "dec_odds", "dec_money", "dec_plan", "dec_start")


def inputs() -> None:
    """What the decision looks at, the league at t (look: the market seen,
    the forecast, your squad and cash then), made once a decision
    (decide.sql dec_player_seen, dec_odds_seen, dec_money_seen)."""
    with closing(connect()) as db, db:
        for t in INPUTS:
            db.execute("DELETE FROM %s" % t)
        db.execute("INSERT INTO dec_state VALUES (?, ?, ?)", (DEBT_SHARE, SQUAD_MAX, XI_SIZE))
        for t in ("dec_player", "dec_odds", "dec_money"):
            db.execute("INSERT INTO %s SELECT * FROM %s_seen" % (t, t))


def plan_rows(inp: dict[str, list[tuple[Any, ...]]]) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    """The plan as decide.sql's header states it, one pure function of the
    rows inputs() writes (dec_player, dec_odds, dec_money) and the cells
    (cell: player, jornada, e): (dec_plan rows, dec_start rows).
    gain of a move = the best legal eleven (rules.ELEVEN) of the squad it
    makes, less the squad's as it is, summed over the jornadas; worth =
    win x gain. Greedy: the best move worth more than nothing that keeps the
    squad within its cap, cash at the lock at zero or more and the bid within
    cash and the debt; in debt (cash at the lock below zero), the least bad
    sale; again, until there is none. A swap pairs a buy with a sale only
    where his top bid is more than the cash left or the squad is full."""
    (cash, team_value, debt, squad_max, _), = inp["dec_money"]
    pos = {k: p for k, p, *_ in inp["dec_player"] if p in POSITIONS}
    mine = {k: proceeds for k, _, m, _, proceeds in inp["dec_player"] if m}
    ratios: dict[str, list[tuple[float, float]]] = {}
    for k, r, w in sorted(inp["dec_odds"]):
        ratios.setdefault(k, []).append((r, w))
    bids = {k: [(a * r, w) for r, w in ratios.get(k, [(1.0, 1.0)])]
            for k, _, _, a, _ in inp["dec_player"] if a is not None}
    cells: dict[int, dict[str, float]] = {}
    for k, j, e in inp["cell"]:
        if k in pos:
            cells.setdefault(j, {})[k] = e

    squad, steps, start = set(mine), [], []
    free, placed = cash, 0.0
    for step in range(squad_max * 2):
        base = {j: {p: sorted((-e[k], k) for k in squad if k in e and pos[k] == p) for p in POSITIONS}
                for j, e in cells.items()}
        here = {j: best_eleven(d) for j, d in base.items()}

        def gain(b: str, s: str) -> float:
            """The squad less s and plus b, over this squad's, over the jornadas."""
            g = 0.0
            for j, e in cells.items():
                if b in e or s in e:
                    d = dict(base[j])
                    if s in e:
                        d[pos[s]] = [x for x in d[pos[s]] if x[1] != s]
                    if b in e:
                        d[pos[b]] = sorted(d[pos[b]] + [(-e[b], b)])
                    g += best_eleven(d) - here[j]
            return g

        def options(b: str, s: str, g: float) -> list[tuple[Any, ...]]:
            return [(b, s, bid, win, mine.get(s), g, (1.0 if win is None else win) * g)
                    for bid, win in bids.get(b, [(None, None)])]

        def fits(b: str, s: str, bid: float | None, proceeds: float | None) -> bool:
            return (len(squad) + (b != "") - (s != "") <= squad_max and free + (proceeds or 0) - (bid or 0) >= 0
                    and (bid or 0) <= cash + debt * team_value - placed)

        sales = [s for s in sorted(squad) if s in mine]
        solo = [options("", s, gain("", s)) for s in sales]
        buys = [] if free < 0 and step else [b for b in sorted(bids) if b not in squad]
        solo += [options(b, "", gain(b, "")) for b in buys]
        if not step:
            start = [o for opts in solo for o in opts]
        swaps = [] if free < 0 else [
            o for opts in solo for b, *_ in opts[:1] if b and opts[0][5] > 0
            and (max(a for a, _ in bids[b]) > free or len(squad) >= squad_max)
            for s in sales if any(fits(b, s, a, mine[s]) for a, _ in bids[b]) for o in options(b, s, gain(b, s))]
        got = [o for opts in solo for o in opts] + swaps
        got = [o for o in got if (o[0] == "" and o[1] != "") if free < 0] if free < 0 else [
            o for o in got if o[6] > 0 and fits(o[0], o[1], o[2], o[4])]
        if not got:
            break
        b, s, bid, win, proceeds, _, worth = min(got, key=lambda o: (-o[6], o[0], o[1], o[2] or 0))
        if b:
            steps.append((step, "buy", b, bid, win, worth))
            squad.add(b)
            free -= bid
            placed += bid
        if s:
            steps.append((step, "sell", s, proceeds, 1.0, worth))
            squad.discard(s)
            free += proceeds
    return steps, start


def worth_on(inp: dict[str, list[tuple[Any, ...]]], steps: list[tuple[Any, ...]], real: dict[tuple[str, int], float],
             js: list[int]) -> tuple[float, float]:
    """The measure: the real points (real: (player, jornada) -> points, 0 if
    he did not play) of the best eleven of the squad over the jornadas js:
    as it stands (inp's dec_player), and once the plan's steps are made,
    each step's change counted at its win chance as the plan's worth does."""
    pos = {k: p for k, p, *_ in inp["dec_player"] if p in POSITIONS}
    squad = {k for k, _, m, *_ in inp["dec_player"] if m}

    def points(sq: set[str]) -> float:
        return sum(best_eleven({p: sorted((-real.get((k, j), 0.0), k) for k in sq if pos.get(k) == p) for p in POSITIONS})
                   for j in js)
    floor = here = points(squad)
    total = floor
    for step in sorted({s[0] for s in steps}):
        win = 1.0
        for _, kind, k, _, w, _ in (s for s in steps if s[0] == step):
            if kind == "buy":
                squad.add(k)
                win = 1.0 if w is None else w
            else:
                squad.discard(k)
        now = points(squad)
        total += win * (now - here)
        here = now
    return floor, total


def measure(inp: dict[str, list[tuple[Any, ...]]], real: dict[tuple[str, int], float], js: list[int]) -> dict[str, float]:
    """floor, ours and ceiling at one lock: worth_on of standing still, of
    plan_rows(inp), and of plan_rows on inp with each cell's e the real
    points in the jornadas js (0 if he did not play; 0 in any later jornada,
    not yet known)."""
    floor, ours = worth_on(inp, plan_rows(inp)[0], real, js)
    best = {**inp, "cell": [(k, j, real.get((k, j), 0.0) if j in js else 0.0) for k, j, _ in inp["cell"]]}
    return {"floor": floor, "ours": ours, "ceiling": worth_on(inp, plan_rows(best)[0], real, js)[1]}


def expected_gap(inp: dict[str, list[tuple[Any, ...]]], steps: list[tuple[Any, ...]] | None = None) -> float:
    """The plan's expected gain on the forecast: the best eleven's e (cell)
    over every jornada once the plan's steps are made (each at its win
    chance), less standing pat's. worth_on with e for the real points; the
    plan's (steps) is plan_rows(inp)'s unless given."""
    e = {(k, j): x for k, j, x in inp["cell"]}
    floor, ours = worth_on(inp, plan_rows(inp)[0] if steps is None else steps, e, sorted({j for _, j in e}))
    return ours - floor


def _write(steps: list[tuple[Any, ...]], start: list[tuple[Any, ...]]) -> None:
    with closing(connect()) as db, db:
        for t in ("dec_plan", "dec_start"):
            db.execute("DELETE FROM %s" % t)
        db.executemany("INSERT INTO dec_plan VALUES (?, ?, ?, ?, ?, ?)", steps)
        db.executemany("INSERT INTO dec_start VALUES (?, ?, ?, ?, ?, ?, ?)", start)


def read_inputs() -> dict[str, list[tuple[Any, ...]]]:
    """inputs() made, and read back as plan_rows takes them."""
    inputs()
    with closing(connect()) as db:
        inp = {t: db.execute("SELECT * FROM %s" % t).fetchall() for t in ("dec_player", "dec_odds", "dec_money")}
        inp["cell"] = db.execute("SELECT player, jornada, e FROM cell").fetchall()
    return inp


def plan() -> list[tuple[Any, ...]]:
    """The plan at t, its moves in order as dec_plan rows (step, kind,
    player, amount, win, worth): a buy at its bid, a sale at what it gets (a
    swap's buy, then its sale). Written to dec_plan, and every single move as
    valued before the first beside it (dec_start, dec_rows)."""
    steps, start = plan_rows(read_inputs())
    _write(steps, start)
    return steps


def options() -> list[tuple[Any, ...]]:
    """Every single move valued alone from the squad as it is (dec_rows) after plan(): kind, player, amount, in_plan."""
    return [tuple(r) for r in rows("SELECT kind, player, amount, in_plan FROM dec_rows")]


# What the decision keeps of a run, the board's plan-detail contract (schema.sql decision_*): the season if you
# do nothing (before the plan), then the plan, every move alone, who it looked at, what waiting is worth, and
# what the plan leaves.
STANDINGS = ("INSERT INTO decision_standings (run_id, manager, me, now, mean, lo, hi, finish, p_first, p_above, cash)"
             " SELECT ?, manager, me, now, mean, lo, hi, finish, p_first, p_above, cash FROM league_standings")
# The plan as the run keeps it: dec_plan's moves, each sale with when it is made (dec_when).
KEEP_PLAN = ("INSERT INTO decision_plan (run_id, step, kind, player, amount, win, worth, after)"
             " SELECT ?, step, kind, player, amount, win, worth, after FROM dec_when")
KEEP = (
    "INSERT INTO decision_players (run_id, player, start, next, season, mine, xi)"
    " SELECT ?, player, start, next, season, mine, xi FROM look_players",
    "INSERT INTO decision_option (run_id, kind, player, amount, win, gain, worth, in_plan, asking_win, beats)"
    " SELECT ?, d.kind, d.player, d.amount, d.win, d.gain, d.worth, d.in_plan,"
    " CASE WHEN d.kind = 'buy' THEN coalesce((SELECT o.p_win FROM bid_odds o WHERE o.player = d.player"
    "                                         AND o.ratio = 1.0), 1.0) END,"
    " CASE WHEN d.kind = 'sell' AND f.offer IS NOT NULL AND f.value > 0"
    "      THEN (SELECT avg(r.ratio < f.offer / f.value) FROM recent_offers r) END"
    " FROM dec_rows d LEFT JOIN player_facts f ON f.player = d.player",
    KEEP_PLAN,
    "INSERT INTO decision_waiting SELECT ?, player, kind, later FROM waiting",
    "INSERT INTO decision_run (run_id, cut, p_first_after, finish_after, cash_after, can_bid, nights, first)"
    " SELECT ?, cut(), s.p_first, s.finish, c.free, r.room, n.nights, n.first"
    " FROM dec_cash c, dec_room r, league_standings s, nights_left n WHERE s.me",
)


def record(run: int) -> list[tuple[Any, ...]]:
    """The decision at t (inputs, then plan) kept as run's rows: the standings forecast if you do nothing, the
    plan, its options, the players it looked at, waiting, and what the plan leaves (your chance of first and
    finish once every bid is won, cash, what more you can bid, the offer nights left). The plan's steps."""
    inputs()
    with closing(connect()) as db, db:
        db.execute(STANDINGS, (run,))
    acts = plan()
    with closing(connect()) as db, db:
        for sql in KEEP:
            db.execute(sql, (run,))
    return acts


def _selftest() -> None:
    import math
    import tempfile
    from statistics import NormalDist
    from pathlib import Path

    from ffcore.fixtures import seed_league
    from ffcore.tidy import store_sql, tables_in

    js = [1, 2]
    squad = {"k": "POR", "d1": "DEF", "d2": "DEF", "d3": "DEF", "m1": "MED", "m2": "MED", "m3": "MED",
             "m4": "MED", "f1": "DEL", "f2": "DEL", "f3": "DEL", "bench": "DEF"}
    cells: dict[str, float] = {k: 4.0 for k in squad}
    cells.update({"m4": 2.0, "bench": 1.0, "star": 8.0, "keeper": 3.0})

    def league(cash: float, price: dict[str, float], odds: dict[str, Any] | None = None,
               extra: int = 0) -> dict[str, list[tuple[Any, ...]]]:
        """The decision's input rows: the squad (5M each, and extra bench players scoring nothing),
        a price on the market, two jornadas of cells (each a sure player)."""
        sq = {**squad, **{"x%02d" % i: "DEF" for i in range(extra)}}
        e = {**cells, **{"x%02d" % i: 0.0 for i in range(extra)}}
        pos = {**sq, "star": "MED", "keeper": "POR"}
        return {"dec_player": [(k, pos[k], 1, None, 5e6) for k in sq]
                              + [(k, pos[k], 0, a, None) for k, a in price.items()],
                "dec_odds": [(k, r, p) for k, o in (odds or {}).items() for r, p in o],
                "dec_money": [(cash, 5e6 * len(sq), DEBT_SHARE, SQUAD_MAX, XI_SIZE)],
                "cell": [(k, j, e[k]) for j in js for k in pos if k in e]}

    def kinds(steps: list[tuple[Any, ...]]) -> list[str]:
        return [kind for _, kind, *_ in steps]

    def labels(steps: list[tuple[Any, ...]]) -> list[str]:
        return ["%s %s" % (kind, k) for _, kind, k, *_ in steps]

    def after_auctions() -> set[str]:
        """The last plan's sales to make after the auctions (decide.sql dec_when)."""
        return {k for (k,) in rows("SELECT player FROM dec_when WHERE after")}

    def sold(steps: list[tuple[Any, ...]]) -> set[str]:
        return {k for _, kind, k, *_ in steps if kind == "sell"}

    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        # The eleven: k, d1-d3, m1-m3, f1 (each position's least), then the best three of the rest:
        # f2, f3 and m4 (2 points); bench (1) plays for nobody. 42 points a jornada.
        steps, start = plan_rows(league(10e6, {"star": 8e6}))
        assert [(g, w) for b, s, _, _, _, g, w in start if (b, s) == ("star", "")] == [(12.0, 12.0)], \
            "star (8) for m4 (2), two jornadas: his points, and nothing for his price"
        assert labels(steps) == ["buy star"]
        assert expected_gap(league(10e6, {"star": 8e6})) == 12.0, "the plan's eleven against standing pat's: 8 for 2, twice"
        assert expected_gap(league(2e6, {"star": 8e6})) == 0.0, "no move: no gap"
        assert labels(plan_rows(league(10e6, {"star": 10e6}))[0]) == ["buy star"], \
            "his price is a limit, not a cost: within cash, the points decide"
        got, _ = plan_rows(league(4e6, {"star": 8e6}))
        assert kinds(got) == ["buy", "sell"] and sold(got) <= {"bench", "m4"}, \
            "4M short of a lock at zero: paid for by a sale of 5M of one he leaves out of the eleven"
        _write(*plan_rows(league(4e6, {"star": 8e6})))
        assert after_auctions() == sold(got), "a sale beside a bid: made once the bid is won"
        assert plan_rows(league(2e6, {"star": 8e6}))[0] == [], "no one sale pays for him"
        _write(*plan_rows(league(2e6, {"star": 8e6})))
        assert rows("SELECT amount, win, in_plan FROM dec_rows WHERE player = 'star'") == [(8e6, 1.0, 0)], \
            "one the plan leaves out, at the lowest bid offered"
        got, _ = plan_rows(league(10e6, {"star": 8e6}, odds={"star": ((1.0, 0.25), (1.25, 1.0), (1.3, 1.0))}))
        assert [(k, a) for _, _, k, a, *_ in got] == [("star", 10e6)], \
            "at asking a quarter of 12 (3); at 1.25 sure (12): the sure bid, the least of those as sure"
        assert labels(plan_rows(league(30e6, {"star": 8e6, "keeper": 6e6}))[0]) == ["buy star"], \
            "a second keeper, worse than k, plays for nobody: not bought, however much cash"
        _write(*plan_rows(league(4e6, {"star": 8e6}, odds={"star": ((1.0, 0.25), (1.25, 1.0))})))
        assert rows("SELECT kind, player, amount, win, gain, in_plan FROM dec_rows"
                    " WHERE player IN ('star', 'bench', 'k') ORDER BY player") == [
            ("sell", "bench", 5e6, 1.0, 0.0, 1), ("sell", "k", 5e6, 1.0, -8.0, 0),
            ("buy", "star", 8e6, 0.25, 12.0, 1)], \
            "each move alone from the squad as it is, a buy at the plan's bid (at 4M and a sale, his asking)"
        full, _ = plan_rows(league(30e6, {"star": 8e6}, extra=SQUAD_MAX - len(squad)))
        assert kinds(full) == ["buy", "sell"] and (sold(full) == {"bench"} or sold(full) <= {
            "x%02d" % i for i in range(SQUAD_MAX)}), \
            "a full squad: a sale of one who plays for nobody makes room for him"
        assert plan_rows(league(30e6, {}, extra=SQUAD_MAX - len(squad)))[0] == [], \
            "a player who scores nothing is not sold for his money: money is no goal"
        assert labels(plan_rows(league(-3e6, {}))[0]) == ["sell bench"], "in debt: the least bad sale"
        _write(*plan_rows(league(-3e6, {})))
        assert after_auctions() == set(), "no bid: the sale at once"
        assert labels(plan_rows(league(-12e6, {}))[0]) == ["sell bench", "sell m4", "sell d1"], \
            "until clear: bench (0), m4 (-4: his place empty), then a slot each, by name"
        sales_only = league(10e6, {"star": 8e6})
        sales_only["dec_player"] = [r for r in sales_only["dec_player"] if r[2]]
        assert plan_rows(sales_only)[0] == [], "sales only: no buy, and no sale worth more than nothing"

        # The standings: you (42 a jornada) and a rival of eight at 2 each, short of 5-4-1 by two
        # defenders and a midfielder: stand-ins at the average of each position's players (19/7, 20/7),
        # and 10 points banked.
        riv = {"rk": "POR", **{"rd%d" % i: "DEF" for i in range(3)}, **{"rm%d" % i: "MED" for i in range(3)},
               "rf": "DEL"}
        seed_league("me", 10e6,
                    {**{k: {"pos": p, "owner": "me", "value": 5e6} for k, p in squad.items()},
                     **{k: {"pos": p, "owner": "riv"} for k, p in riv.items()}},
                    {j: {**{k: (e, 1.0) for k, e in cells.items()}, **{k: (2.0, 1.0) for k in riv}} for j in js})
        store_sql("api_standings", [{"observed_at": "2026-08-01T0000Z", "manager": "riv", "team_points": "10"}])
        inputs()
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
