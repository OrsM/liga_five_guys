"""The decision as ffcore/decide.sql makes it: the league at t (schema.sql
look, my_squad, my_cash) made into its inputs, then its next move added
until there is none. Every number the plan rests on is a row there to read
(dec_money, dec_start)."""
from __future__ import annotations

import json
import random
import sqlite3
from contextlib import closing
from collections import Counter
from functools import lru_cache
from itertools import combinations, product
from typing import Any, Callable, NamedTuple

from ffcore.db import connect, rows
from ffcore.rules import DEBT_SHARE, bid_limit, in_force, POSITIONS, SHAPES, SQUAD_MAX, XI_SIZE
from recommender import by_jornada, candidates, cells_of

__all__ = ["in_groups", "schedule", "stamp_plan", "plan", "plan_rows", "after", "evaluate", "flips", "skip_rows", "inputs", "read_inputs", "worth_on", "measure", "expected_gap", "options", "record", "KEEP_PLAN", "READ"]

INPUTS = ("dec_hold", "dec_state", "dec_player", "dec_odds", "dec_money", "dec_bid", "dec_close", "dec_lock", "dec_offer", "dec_plan", "dec_start")


def inputs() -> None:
    """What the decision looks at, the league at t (look: the market seen,
    the forecast, your squad and cash then), made once a decision
    (decide.sql dec_player_seen, dec_odds_seen, dec_money_seen, dec_bid_seen)."""
    with closing(connect()) as db, db:
        for t in INPUTS:
            db.execute("DELETE FROM %s" % t)
        db.execute("INSERT INTO dec_state VALUES (?, ?, ?)", (DEBT_SHARE, SQUAD_MAX, XI_SIZE))
        for t in ("dec_hold", "dec_player", "dec_odds", "dec_money", "dec_bid", "dec_close", "dec_lock", "dec_offer"):
            db.execute("INSERT INTO %s SELECT * FROM %s_seen" % (t, t))


def sale_sets(lost: Callable[[frozenset[str]], float], sellable: dict[str, float], room: int,
              pool: int = 10, size: int = 4) -> list[tuple[frozenset[str], float]]:
    """The sets of sales that free room places, fewest points lost first, each with what it brings (lost(R): the
    points the squad loses without R, infinite if no eleven can be fielded; sellable: player -> what his sale brings).
    Of the sellable, the pool that lose the fewest points per euro, one at a time (those that bring nothing only when
    room is wanted); every set of up to size of them. One list for every bid on the same buys."""
    ranked = sorted((k for k, got in sellable.items() if got > 0 or room > 0),
                    key=lambda k: (lost(frozenset([k])) / max(sellable[k], 1.0), k))[:pool]
    sets = [(frozenset(R), sum(sellable[k] for k in R)) for n in range(max(room, 0), size + 1)
            for R in combinations(ranked, n)]
    return sorted((x for x in sets if lost(x[0]) < float("inf")), key=lambda x: (lost(x[0]), len(x[0]), sorted(x[0])))


def cover(sets: list[tuple[frozenset[str], float]], need: float, top: int = 2) -> list[frozenset[str]]:
    """The top sets of sales (from sale_sets) that raise need, the fewest points lost first. Which sales pay for a
    buy is a choice among sets, never the first that is enough."""
    return [R for R, got in sets if got >= need - 1e-6][:top]


def plan_rows(inp: dict[str, list[tuple[Any, ...]]]) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    """The plan as decide.sql's header states it, one pure function of the rows inputs() writes (dec_player,
    dec_odds, dec_money; dec_bid is for after) and the cells (cell: player, jornada, e): (dec_plan rows, dec_start rows).
    The squad to hold at the lock is the best of every choice of buys (the recommender's bids, a few at most), each
    with the sales that pay for it (cover: the cash at the lock at zero or more, room in the squad, the fewest
    points lost), every mix judged once, as the plan is, by worth_on. Standing pat is a choice; in debt it is not.
    Money is no goal: a sale is only ever a cost of a buy or of a debt."""
    (cash, team_value, debt, squad_max, _), = inp["dec_money"]
    pos, cells = cells_of(inp)
    mine = {k: proceeds or 0.0 for k, _, m, _, proceeds in inp["dec_player"] if m}
    cands = candidates(inp)
    e = {(k, j): x for k, j, x in inp["cell"]}
    js = sorted({j for _, j in e})
    limit = bid_limit(cash, team_value, debt)
    held = {k for k, in inp.get("dec_hold", [])}     # players you hold back: neither bought nor sold
    sellable = {k: got for k, got in mine.items() if k not in held}

    locks, closes, until = dict(inp.get("dec_lock", [])), dict(inp.get("dec_close", [])), dict(inp.get("dec_offer", []))
    soon = min(locks.values(), default=None)

    @lru_cache(maxsize=None)
    def pts(squad: frozenset[str]) -> dict[int, float]:
        return by_jornada(pos, cells, set(squad))

    def span(squad: frozenset[str], due: str | None) -> float:
        """The squad's points in the jornadas that lock after due: what a move made then can change."""
        return sum(v for j, v in pts(squad).items() if in_force(locks, j, due))

    def sale_when(buys: list[tuple[str, float, float]]) -> Due:
        due = [closes.get(b) or soon for b, *_ in buys]
        return sale_due(cash, [(bid, d) for (_, bid, _), d in zip(buys, due)], max((d for d in due if d), default=soon), soon)

    def lose(squad: frozenset[str], base: Due) -> Callable[[frozenset[str]], float]:
        """lost(R) for cover: the points the squad loses without R, each sale in the jornadas it reaches (made at
        base, or when its offer lapses); no eleven to field is a loss without end."""
        have = Counter(pos[k] for k in squad if k in pos)

        def lost(R: frozenset[str]) -> float:
            if not can_field(have - Counter(pos[k] for k in R if k in pos)):
                return float("inf")
            due = {k: lapses(base, until.get(k)).due for k in R}
            return sum(v - pts(squad - frozenset(k for k in R if in_force(locks, j, due[k])))[j] for j, v in pts(squad).items())
        return lost

    def total(steps: list[tuple[Any, ...]]) -> float:
        return worth_on(inp, steps, e, js, pts)[1]

    def moves(buys: list[tuple[str, float, float]], sold: frozenset[str]) -> list[tuple[Any, ...]]:
        """The steps of one choice from the squad as it is: each buy (player, bid, win) a step; as many sales as the
        squad lacks room beside the buys (made once the bid is won, the least lost first), the rest each a step of
        their own. A step's worth is left to the chosen plan: its marginal by worth_on, the one measure."""
        here = frozenset(mine) | {k for k, *_ in buys}
        base = sale_when(buys)
        order = sorted(sold, key=lambda k: (span(here, base.due) - span(here - {k}, base.due), k))
        room = max(len(here) - squad_max, 0)
        steps: list[tuple[Any, ...]] = []
        for n, (k, bid, win) in enumerate(buys):
            steps.append((n, "buy", k, bid, win, 0.0))
        for n, s in enumerate(order):
            steps.append((n if n < room else len(buys) + n - room, "sell", s, mine[s], 1.0, 0.0))
        return steps

    # The squad to hold: a set of buys (at most `most`, each from the recommender's bids and worth something alone),
    # the sales that pay for them (cover), the whole mix judged once by worth_on. A mix cannot gain more than its
    # buys do alone (a sale never raises the eleven, and a buy adds less once another is in), which prunes the sets.
    most = 3
    sets_for = lru_cache(maxsize=None)(lambda here, due: sale_sets(lru_cache(maxsize=None)(lose(here, due)), sellable, len(here) - squad_max))
    worth = {(a, bid, win): w for a, s, bid, win, _, _, w in cands.alone if a}
    alone = {b: max(w for (a, *_), w in worth.items() if a == b) for b in cands.bids}
    wanted = sorted((b for b in alone if alone[b] > 0 and b not in held), key=lambda b: (-alone[b], b))
    floor = total([])
    best: tuple[float, list[tuple[Any, ...]]] | None = (0.0, []) if cash >= 0 else None  # standing pat, unless in debt
    for n in range(most + 1):
        for B in combinations(wanted, n):
            if best and sum(alone[b] for b in B) <= best[0] + 1e-9:
                continue
            here = frozenset(mine) | set(B)
            for bids in product(*(cands.bids[b] for b in B)):
                spend = sum(bid for bid, _ in bids)
                buys = [(b, bid, win) for b, (bid, win) in zip(B, bids)]
                if spend > limit or best and sum(worth[buy] for buy in buys) <= best[0] + 1e-9:
                    continue
                for R in cover(sets_for(here, sale_when(buys)), spend - cash):
                    steps = moves(buys, R)
                    gain = total(steps) - floor
                    if best is None or gain > best[0] + 1e-9:
                        best = (gain, steps)
    steps = best[1] if best else []
    whole = total(steps)
    steps = [(*step[:5], whole - total(without(steps, step[1], step[2]))) for step in steps]    # each its marginal in the plan
    return steps, cands.alone


def worth_on(inp: dict[str, list[tuple[Any, ...]]], steps: list[tuple[Any, ...]], real: dict[tuple[str, int], float],
             js: list[int], score: Callable[[frozenset[str]], dict[int, float]] | None = None) -> tuple[float, float]:
    """The measure: the real points (real: (player, jornada) -> points, 0 if
    he did not play) of the best eleven of the squad over the jornadas js:
    as it stands (inp's dec_player), and once the plan's steps are made,
    each step's change counted at its win chance as the plan's worth does; a sale made before its bid's result is
    known (to an offer that lapses with the auction) is made whether the bid is won or lost. A step counts only for the jornadas
    that lock after it is made (rules.in_force: your eleven is frozen at the lock). score: a squad's best eleven in
    each jornada, if the caller has it (and has it cached); else made here from real."""
    pos = {k: p for k, p, *_ in inp["dec_player"] if p in POSITIONS}
    squad = frozenset(k for k, _, m, *_ in inp["dec_player"] if m)
    locks = dict(inp.get("dec_lock", []))
    due = schedule(inp, steps)
    played = {j: {k: real.get((k, j), 0.0) for k in pos} for j in js}     # 0 if he did not play

    @lru_cache(maxsize=None)
    def points(sq: frozenset[str]) -> dict[int, float]:
        return score(sq) if score else by_jornada(pos, played, set(sq))
    squads = {j: squad for j in js}
    total = floor = sum(points(squad)[j] for j in js)
    for group in in_groups(inp, steps):
        win, before = 1.0, {j: points(sq)[j] for j, sq in squads.items()}
        won, lost = dict(squads), dict(squads)
        for step, kind, k, _, w, _ in group:
            if kind == "buy":
                win = 1.0 if w is None else w
            for j in js:
                if in_force(locks, j, due[step, kind].due):
                    won[j] = won[j] | {k} if kind == "buy" else won[j] - {k}
                    if kind == "sell" and due[step, kind].why == OFFER_EXPIRES:     # made before the bid's result is known
                        lost[j] = lost[j] - {k}
        total += sum(win * (points(won[j])[j] - before[j]) + (1 - win) * (points(lost[j])[j] - before[j]) for j in js)
        squads = won
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


class Due(NamedTuple):
    due: str | None     # when it must be done, as the game's clock (YYYY-MM-DDTHH:MMZ); None: not known
    why: str            # what sets it: 'auction closes', 'after the auctions', 'before the lock'


AFTER_AUCTIONS = "after the auctions"
OFFER_EXPIRES = "offer expires"


def sale_due(cash: float, buys: list[tuple[float, str | None]], closes_last: str | None, lock: str | None) -> Due:
    """When a sale is due. With bids: after the last of their auctions, once you know they are won (a sale made for
    a bid then lost is a player gone for nobody; the game still buys him at his value after the auctions), unless
    the bids closing by the next lock leave the cash below zero there: then by the lock. With no bid: by the lock.
    buys: each bid with when its auction closes."""
    if buys and (lock is None or cash - sum(a for a, due in buys if due is not None and due <= lock) >= 0):
        return Due(closes_last, AFTER_AUCTIONS)
    return Due(lock, "before the lock")


def lapses(due: Due, until: str | None) -> Due:
    """A sale's due once its offer lapses (until): by then, if that is no later; an offer is gone at the close, so a
    sale to it cannot follow the auctions that close with it."""
    return Due(until, OFFER_EXPIRES) if until and (due.due is None or until <= due.due) else due


def schedule(inp: dict[str, list[tuple[Any, ...]]], steps: list[tuple[Any, ...]]) -> dict[tuple[int, str], Due]:
    """When each step of the plan is due, by (step, kind). A buy: when his auction closes. A sale: sale_due. inp:
    dec_close (player, close), dec_lock (jornada, lock), dec_offer (player, until: when the offer a sale
    takes lapses) and dec_money, each as it is known."""
    closes = dict(inp.get("dec_close", []))
    lock = min((at for _, at in inp.get("dec_lock", [])), default=None)
    buys = {k: (a, Due(closes.get(k) or lock, "auction closes" if closes.get(k) else "before the lock"))
            for _, kind, k, a, *_ in steps if kind == "buy"}
    last = max((d.due for _, d in buys.values() if d.due), default=lock)
    sell = sale_due(inp["dec_money"][0][0] if inp.get("dec_money") else 0.0, [(a, d.due) for a, d in buys.values()], last, lock)
    until = dict(inp.get("dec_offer", []))
    return {(step, kind): buys[k][1] if kind == "buy" else lapses(sell, until.get(k)) for step, kind, k, *_ in steps}


def in_order(inp: dict[str, list[tuple[Any, ...]]], steps: list[tuple[Any, ...]]) -> list[tuple[Any, ...]]:
    """The plan's steps in their order (decision_plan.ord): soonest due first, a buy before a sale, then by step
    (a swap's pair: the buy and the sale that funds it share a step)."""
    due = schedule(inp, steps)
    return sorted(steps, key=lambda s: (due[s[0], s[1]].due or "~", s[1], s[0]))


def in_groups(inp: dict[str, list[tuple[Any, ...]]], steps: list[tuple[Any, ...]]) -> list[list[tuple[Any, ...]]]:
    """The plan's steps grouped by step (a buy and the sale that funds it), the groups in the plan's order: each
    where its first move comes in in_order."""
    ordered = in_order(inp, steps)
    return [[s for s in ordered if s[0] == step] for step in dict.fromkeys(s[0] for s in ordered)]


def can_field(count: Counter[str]) -> bool:
    """Whether some formation can be fielded from so many players of each position."""
    return any(all(count[p] >= n for p, n in shape.items()) for shape in SHAPES)


def fieldable(pos: dict[str, str], squad: set[str] | frozenset[str]) -> bool:
    """Whether some formation can be fielded from the squad (pos: each player's position)."""
    return can_field(Counter(pos[k] for k in squad if k in pos))


class After(NamedTuple):
    cash_then: list[float]                      # the cash each step leaves, in the plan's order
    cash: float                                 # once every step is made
    squad: set[str]                             # the same
    blockers: list[tuple[str, float | None]]    # (blocker, amount), what stops you


def after(inp: dict[str, list[tuple[Any, ...]]], steps: list[tuple[Any, ...]], won: dict[str, float] | None = None) -> After:
    """The cash and squad once steps (dec_plan rows) are made, every buy won, and what stops you. won: bids placed
    (player -> amount) taken as won beside the steps' buys, one the steps buy too counted once. cash_then: the cash
    each step leaves running from today's, in the plan's order (step, a buy before a sale). Blockers (blocker,
    amount): 'debt' (cash below zero at a jornada's lock, counting the steps made by it, or once all are made: the
    matchday scores 0; amount the lowest), 'eleven' (no formation can be fielded from the squad then, or if every bid is lost after the sales made before the result), 'squad'
    (players over the cap once all are made, amount how many)."""
    (cash, _, _, squad_max, _), = inp["dec_money"]
    pos = {k: p for k, p, *_ in inp["dec_player"]}
    closes, locks = dict(inp.get("dec_close", [])), dict(inp.get("dec_lock", []))
    soon = min(locks.values(), default=None)
    squad = {k for k, _, m, *_ in inp["dec_player"] if m}
    due = schedule(inp, steps)
    # every move as (when, buy?, player, amount): the plan's steps, then the bids placed beside them
    moves = [(due[step, kind].due, kind == "buy", k, amount) for step, kind, k, amount, *_ in in_order(inp, steps)]
    moves += [(closes.get(k) or soon, True, k, amount) for k, amount in (won or {}).items()
              if not any(m[1] and m[2] == k for m in moves)]

    def position(lock: str | None = None) -> tuple[float, set[str]]:
        """Cash and squad with the moves made by the lock (all of them if none)."""
        here, sq = cash, set(squad)
        for when, buy, k, amount in moves:
            if lock is None or when is None or when <= lock:
                here += -amount if buy else amount
                sq = sq | {k} if buy else sq - {k}
        return here, sq

    then, here = [], cash
    for _, kind, _, amount, *_ in in_order(inp, steps):
        here += -amount if kind == "buy" else amount
        then.append(here)
    final = position()
    seen = [position(lock) for lock in sorted(locks.values())] + [final]
    low = min(c for c, _ in seen)
    out: list[tuple[str, float | None]] = []
    if low < 0:
        out.append(("debt", low))
    sure = {k for step, kind, k, *_ in steps if kind == "sell" and due[step, kind].why == OFFER_EXPIRES}
    if not all(fieldable(pos, sq) for _, sq in seen) or not fieldable(pos, squad - sure):   # or every bid lost
        out.append(("eleven", None))
    if len(final[1]) > squad_max:
        out.append(("squad", float(len(final[1]) - squad_max)))
    return After(then, final[0], final[1], out)


def without(steps: list[tuple[Any, ...]], kind: str, player: str) -> list[tuple[Any, ...]]:
    """The plan less one move: a buy goes with the sale that shares its step (it made room or paid for him); a sale
    goes alone."""
    step = next(s[0] for s in steps if (s[1], s[2]) == (kind, player))
    return [s for s in steps if (s[0] != step if kind == "buy" else (s[1], s[2]) != (kind, player))]


def skip_rows(inp: dict[str, list[tuple[Any, ...]]], steps: list[tuple[Any, ...]]) -> list[tuple[str, str, str, float | None]]:
    """What skipping each step of the plan leaves: the rest of it as written, no placed bid (the board withdraws
    those), as rows (player, kind, blocker, amount), one a blocker, or blocker 'none' (amount None) for none.
    A buy goes with the sale that shares its step; a sale alone is skipped alone."""
    out: list[tuple[str, str, str, float | None]] = []
    for step, kind, k, *_ in steps:
        left = after(inp, without(steps, kind, k)).blockers or [("none", None)]
        out += [(k, kind, blocker, amount) for blocker, amount in left]
    return out


class Evaluation(NamedTuple):
    cash_then: list[float]                      # the cash each move leaves, in the plan's order
    cash: float                                 # at the lock
    blockers: list[tuple[str, float | None]]    # what stops you there
    points: float                               # the season's, against standing pat (expected_gap)


def evaluate(inp: dict[str, list[tuple[Any, ...]]], moves: list[tuple[Any, ...]]) -> Evaluation:
    """What a chosen set of moves (dec_plan rows: step, kind, player, amount, win, worth) leaves: the cash and what
    stops you (after) and its points (expected_gap), for any mix, the plan's or not. The one place a mix is judged."""
    made = after(inp, moves)
    return Evaluation(made.cash_then, made.cash, made.blockers, expected_gap(inp, moves))


class Flip(NamedTuple):
    player: str
    kind: str
    evaluation: Evaluation


def flips(inp: dict[str, list[tuple[Any, ...]]], steps: list[tuple[Any, ...]],
          options: list[tuple[Any, ...]]) -> list[Flip]:
    """What flipping each single move leaves, from the plan as it stands (evaluate): a move in the plan is taken out
    (without), one not in it is added at its amount and chance. options: (kind, player, amount, win, in_plan)."""
    out = []
    for kind, k, amount, win, in_plan in options:
        mix = without(steps, kind, k) if in_plan else [*steps, (max((s[0] for s in steps), default=-1) + 1, kind, k, amount, win, 0.0)]
        out.append(Flip(k, kind, evaluate(inp, mix)))
    return out


def _write(steps: list[tuple[Any, ...]], start: list[tuple[Any, ...]]) -> None:
    with closing(connect()) as db, db:
        for t in ("dec_plan", "dec_start"):
            db.execute("DELETE FROM %s" % t)
        db.executemany("INSERT INTO dec_plan VALUES (?, ?, ?, ?, ?, ?)", steps)
        db.executemany("INSERT INTO dec_start VALUES (?, ?, ?, ?, ?, ?, ?)", start)


def read_inputs() -> dict[str, list[tuple[Any, ...]]]:
    """inputs() made, and read back as plan_rows takes them."""
    inputs()
    return read_made()


# What plan_rows reads: every input inputs() makes (not the plan it writes, the state it fixes) and the cells.
READ = tuple(t for t in INPUTS if t not in ("dec_state", "dec_plan", "dec_start")) + ("cell",)


def read_made(only: tuple[str, ...] = READ) -> dict[str, list[tuple[Any, ...]]]:
    """The rows inputs() wrote, as plan_rows takes them (only: just those tables; the cells cost a forecast)."""
    with closing(connect()) as db:
        inp = {t: db.execute("SELECT * FROM %s" % t).fetchall() for t in only if t != "cell"}
        if "cell" in only:
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
# The plan as the run keeps it: dec_plan's moves (the order, ord, cash_then, due and why are written by record).
KEEP_PLAN = ("INSERT INTO decision_plan (run_id, step, kind, player, amount, win, worth)"
             " SELECT ?, step, kind, player, amount, win, worth FROM dec_plan")
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
)
# The run's row, once after() has the cash and the bids are known (cash_after, can_bid).
KEEP_RUN = ("INSERT INTO decision_run (run_id, cut, p_first_after, finish_after, cash_after, can_bid, nights, first,"
            " offer_mean, offer_sd)"
            " SELECT ?, cut(), s.p_first, s.finish, ?, ?, n.nights, n.first, o.mean, o.sd"
            " FROM league_standings s, nights_left n, offer_spread o WHERE s.me")


def stamp_plan(db: sqlite3.Connection, run: int, inp: dict[str, list[tuple[Any, ...]]], steps: list[tuple[Any, ...]],
               cash_then: list[float]) -> None:
    """What the run keeps beside each step of its plan (KEEP_PLAN wrote the moves): its place in the plan's order, the
    cash it leaves (cash_then, after()'s, in that order), when it is due and what sets it (schedule)."""
    due = schedule(inp, steps)
    db.executemany("UPDATE decision_plan SET cash_then = ?, ord = ?, due = ?, why = ? WHERE run_id = ? AND step = ? AND kind = ?",
                   [(c, i, *due[step, kind], run, step, kind)
                    for i, (c, (step, kind, *_)) in enumerate(zip(cash_then, in_order(inp, steps)))])


class SaleAssessment(NamedTuple):
    made: float | None            # the offer less what you paid for him
    z: float | None               # the offer as a share of value, in standard deviations from the recent offers' mean
    p_better: float | None        # the chance a better offer comes before the lock: one a night, independent
    waiting_less: float | None    # what waiting is worth less the offer (negative: take it)


def assess_sale(offer: float, value: float | None, paid: float | None, beats: float | None, nights: int | None,
                waiting: float | None, mean: float | None, sd: float | None) -> SaleAssessment:
    """What an offer on one of yours is worth, read against what you paid and the recent offers (offer_spread).
    Information the board shows: the plan's choice of take or wait is made in the plan, by the same waiting."""
    return SaleAssessment(
        None if paid is None else offer - paid,
        (offer / value - mean) / sd if value and mean is not None and sd else None,
        1 - beats ** nights if beats is not None and nights else None,
        waiting - offer if waiting is not None and nights else None)


# Your players with an offer standing, and what the assessment reads of each.
SALES = ("SELECT o.player, f.offer, f.value, f.paid, o.beats, r.nights, w.later, r.offer_mean, r.offer_sd"
         " FROM decision_option o JOIN player_facts f ON f.player = o.player JOIN decision_run r ON r.run_id = o.run_id"
         " LEFT JOIN decision_waiting w ON w.run_id = o.run_id AND w.player = o.player AND w.kind = 'sale'"
         " WHERE o.run_id = ? AND o.kind = 'sell' AND f.offer IS NOT NULL")


def record(run: int) -> list[tuple[Any, ...]]:
    """The decision at t (inputs, then plan) kept as run's rows: the standings forecast if you do nothing, the
    plan, its options, the players it looked at, waiting, and what the plan leaves (your chance of first and
    finish once every bid is won, cash, what more you can bid, the offer nights left). The plan's steps."""
    inputs()
    with closing(connect()) as db, db:
        db.execute(STANDINGS, (run,))
    acts = plan()
    inp = read_made()
    (cash, team_value, debt, _, _), = inp["dec_money"]
    placed = dict(inp["dec_bid"])
    planned = after(inp, acts)
    bids = sum(a for _, kind, _, a, *_ in acts if kind == "buy")
    unplanned = sum(a for k, a in placed.items() if not any(kind == "buy" and p == k for _, kind, p, *_ in acts))
    # What more you can bid: the game takes bids up to your cash and the debt it allows (DEBT_SHARE of your team's
    # value); sales do not count, made after the auctions or not at all; a placed bid the plan does not make
    # stands (the board says withdraw it) and holds its money.
    room = max(0.0, bid_limit(cash, team_value, debt) - bids - unplanned)
    with closing(connect()) as db, db:
        for sql in KEEP:
            db.execute(sql, (run,))
        db.execute(KEEP_RUN, (run, planned.cash, room))
        stamp_plan(db, run, inp, acts, planned.cash_then)
        db.executemany("INSERT INTO decision_sale (run_id, player, offer, paid, made, z, p_better, waiting_less)"
                       " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                       [(run, k, offer, paid, *assess_sale(offer, value, paid, beats, nights, waiting, mean, sd))
                        for k, offer, value, paid, beats, nights, waiting, mean, sd in db.execute(SALES, (run,))])
        db.executemany("INSERT INTO decision_blocker (run_id, future, blocker, amount) VALUES (?, ?, ?, ?)",
                       [(run, future, *b) for future, found in (("nothing", after(inp, [], placed).blockers), ("plan", planned.blockers))
                        for b in found])
        db.executemany("INSERT INTO decision_flip (run_id, player, kind, cash, points, blockers) VALUES (?, ?, ?, ?, ?, ?)",
                       [(run, f.player, f.kind, f.evaluation.cash, f.evaluation.points,
                         json.dumps([b for b, _ in f.evaluation.blockers]))
                        for f in [Flip("", "plan", evaluate(inp, acts)),
                                  *flips(inp, acts, db.execute("SELECT kind, player, amount, win, in_plan FROM dec_rows").fetchall())]])
        db.executemany("INSERT INTO decision_skip (run_id, player, kind, blocker, amount) VALUES (?, ?, ?, ?, ?)",
                       [(run, *r) for r in skip_rows(inp, acts)])
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

    def after_auctions(steps: list[tuple[Any, ...]]) -> set[str]:
        """The plan's sales to make after the auctions (schedule)."""
        due = schedule({}, steps)
        return {k for step, kind, k, *_ in steps if due[step, kind].why == AFTER_AUCTIONS}

    def sold(steps: list[tuple[Any, ...]]) -> set[str]:
        return {k for _, kind, k, *_ in steps if kind == "sell"}

    # A sale assessed: offer 1.1M on value 1.0M, paid 0.8M; the recent offers' mean 1.0, sd 0.05; he beats 75% of
    # them, two nights left, waiting worth 1.0M.
    a = assess_sale(1.1e6, 1e6, 0.8e6, 0.75, 2, 1.0e6, 1.0, 0.05)
    assert (a.made, a.waiting_less) == (0.3e6, -0.1e6) and a.z is not None and abs(a.z - 2.0) < 1e-9 and a.p_better == 1 - 0.75 ** 2, a
    nothing = assess_sale(1.1e6, 1e6, None, None, 0, None, None, None)
    assert nothing == SaleAssessment(None, None, None, None), \
        "no purchase price, no offers to rank against, no night left, nothing to wait for: nothing to say"

    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        assert rows("SELECT mean, sd FROM offer_spread") == [(None, None)], "no offer seen: nothing to read one against"
        for n, money in enumerate(("8000000", "9000000", "10000000"), 1):      # 0.8, 0.9, 1.0 of value
            store_sql("api_teams", [{"observed_at": "2026-09-0%dT1000Z" % n, "player_id": "1", "manager": "me",
                                     "market_value": "10000000", "player_team_id": "t1"}])
            store_sql("api_offers", [{"observed_at": "2026-09-0%dT1000Z" % n, "player_team_id": "t1", "offer_id": "o%d" % n,
                                      "from_market": "true", "money": money, "status": "accepted",
                                      "created_at": "2026-09-0%dT09:00:00+02:00" % n}])
        (mean, sd), = rows("SELECT mean, sd FROM offer_spread")
        assert abs(mean - 0.9) < 1e-9 and abs(sd - math.sqrt(0.02 / 3)) < 1e-9, \
            "the offers seen as shares of value: their mean and (population) standard deviation"
        # The eleven: k, d1-d3, m1-m3, f1 (each position's least), then the best three of the rest:
        # f2, f3 and m4 (2 points); bench (1) plays for nobody. 42 points a jornada.
        steps, start = plan_rows(league(10e6, {"star": 8e6}))
        assert [(g, w) for b, s, _, _, _, g, w in start if (b, s) == ("star", "")] == [(12.0, 12.0)], \
            "star (8) for m4 (2), two jornadas: his points, and nothing for his price"
        assert labels(steps) == ["buy star"]
        assert expected_gap(league(10e6, {"star": 8e6})) == 12.0, "the plan's eleven against standing pat's: 8 for 2, twice"
        assert expected_gap(league(2e6, {"star": 8e6})) == 12.0, "6M short: two sales out of the eleven (bench, m4) pay for him"
        assert expected_gap(league(2e6, {"star": 20e6})) == 0.0, "his bid over cash and the debt allowed (14M): no move"
        got, _ = plan_rows(league(10e6, {"star": 10e6}, odds={"star": ((0.7, 0.5),)}))
        assert [(k, a, w) for _, _, k, a, w, _ in got] == [("star", 7e6, 0.5)], \
            "the plan buys at the bid and chance the odds offer, not at the market's asking"
        assert labels(plan_rows(league(10e6, {"star": 10e6}))[0]) == ["buy star"], \
            "his price is a limit, not a cost: within cash, the points decide"
        got, _ = plan_rows(league(4e6, {"star": 8e6}))
        assert kinds(got) == ["buy", "sell"] and sold(got) <= {"bench", "m4"}, \
            "4M short of a lock at zero: paid for by a sale of 5M of one he leaves out of the eleven"
        assert after_auctions(got) == sold(got), "a sale beside a bid: made once the bid is won"
        assert after(league(4e6, {"star": 8e6}), got).cash_then == [-4e6, 1e6], \
            "each step of the plan leaves this cash, running from today's: 4M, less the 8M bid, plus the 5M sale"
        _write(*plan_rows(league(2e6, {"star": 20e6})))
        assert rows("SELECT amount, win, in_plan FROM dec_rows WHERE player = 'star'") == [(20e6, 1.0, 0)], \
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
            ("buy", "star", 10e6, 1.0, 12.0, 1)], \
            "each move alone from the squad as it is, a buy at the plan's bid (the sure one, two sales pay for it)"
        full, _ = plan_rows(league(30e6, {"star": 8e6}, extra=SQUAD_MAX - len(squad)))
        assert kinds(full) == ["buy", "sell"] and (sold(full) == {"bench"} or sold(full) <= {
            "x%02d" % i for i in range(SQUAD_MAX)}), \
            "a full squad: a sale of one who plays for nobody makes room for him"
        assert plan_rows(league(30e6, {}, extra=SQUAD_MAX - len(squad)))[0] == [], \
            "a player who scores nothing is not sold for his money: money is no goal"
        assert labels(plan_rows(league(-3e6, {}))[0]) == ["sell bench"], "in debt: the least bad sale"
        assert after_auctions(plan_rows(league(-3e6, {}))[0]) == set(), "no bid: the sale before the lock"
        assert plan_rows(league(-12e6, {}))[0] == [], \
            "12M in debt, and any three sales leave no eleven to field: no sale, the debt is the blocker"
        assert labels(plan_rows(league(-12e6, {}, extra=3))[0]) == ["sell bench", "sell x00", "sell x01"], \
            "until clear, the fewest points lost (all three play for nobody), in order by name"
        sales_only = league(10e6, {"star": 8e6})
        sales_only["dec_player"] = [r for r in sales_only["dec_player"] if r[2]]
        assert plan_rows(sales_only)[0] == [], "sales only: no buy, and no sale worth more than nothing"

        # Any mix is judged by evaluate: the plan's own mix gives the plan's cash and points; none leaves today's.
        mixed = league(4e6, {"star": 8e6})
        full_plan, _ = plan_rows(mixed)
        got_all, got_none = evaluate(mixed, full_plan), evaluate(mixed, [])
        assert (got_all.cash, got_all.cash_then, got_all.points) == (after(mixed, full_plan).cash, [-4e6, 1e6],
                                                                       expected_gap(mixed, full_plan)), \
            "the plan's mix: the cash and points the plan itself leaves"
        assert (got_none.cash, got_none.cash_then, got_none.points, got_none.blockers) == (4e6, [], 0.0, []), "no move: today's"
        flipped = {(f.kind, f.player): f.evaluation for f in flips(mixed, full_plan, [
            ("buy", "star", 8e6, 1.0, 1), ("sell", "k", 5e6, 1.0, 0), ("sell", "m4", 5e6, 1.0, 0)])}
        assert (flipped["buy", "star"].cash, flipped["buy", "star"].points) == (9e6, 0.0), \
            "flip a buy of the plan: without him, the sale (bench: plays for nobody, own step) still stands: 4M + 5M"
        assert flipped["sell", "k"].cash == got_all.cash + 5e6 and flipped["sell", "k"].points == got_all.points - 8.0 \
            and flipped["sell", "k"].blockers == [("eleven", None)], \
            "flip a sale not in the plan: the only keeper (4 a jornada, two): his 5M comes in, 8 points go, and no eleven can be fielded"
        assert flipped["sell", "m4"].blockers == [] and flipped["sell", "m4"].cash == got_all.cash + 5e6 \
            and flipped["sell", "m4"].points == got_all.points, "a sale that plays for nobody (star takes his place)"
        buy_only = [m for m in full_plan if m[1] == "buy"]
        assert evaluate(mixed, buy_only).cash == -4e6 and ("debt", -4e6) in evaluate(mixed, buy_only).blockers, \
            "the bid without its funding sale: in debt at the lock, said"
        assert evaluate(mixed, buy_only).points == got_all.points, "his points are the same with or without the sale that funds him"

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
        (s,), = rows("SELECT const('PERSISTENT_SHARE')")
        for m in ("me", "riv"):
            (ev, vs, e2), = rows("SELECT total(e), total(v), total(e * e) FROM league_xi WHERE manager = ? AND jornada = 1", (m, ))
            (sd,), = rows("SELECT sd FROM league_jornada WHERE manager = ? AND jornada = 1", (m, ))
            assert vs > 0 and abs(sd - math.sqrt((1 + s * s) * vs + s * s * e2)) < 1e-9, \
                "a jornada's variance: (1 + share^2) x the cells' + share^2 x the squares of their e (the persistent share)"
    _selftest_blockers()
    _selftest_ledger()
    _selftest_search()
    _selftest_offers()
    print("decision self-test OK")


def _selftest_offers() -> None:
    """recent_offers: the last PRICE_WINDOW offers, a night's batch (offers sharing a timestamp) whole or not at all,
    so which of a batch's offers the cut keeps never depends on their size."""
    import tempfile
    from pathlib import Path

    from ffcore.db import set_constants
    from ffcore.tidy import store_sql, tables_in

    nights = {"2026-09-01": (0.80, 0.85, 0.90, 0.95), "2026-09-02": (1.00, 1.05, 1.10, 1.15)}     # shares of a 10M value
    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        for day, ratios in nights.items():
            store_sql("api_teams", [{"observed_at": day + "T1000Z", "player_id": str(n), "manager": "me",
                                     "market_value": "10000000", "player_team_id": "%s-%d" % (day, n)} for n in range(len(ratios))])
            store_sql("api_offers", [{"observed_at": day + "T1000Z", "player_team_id": "%s-%d" % (day, n), "offer_id": "%s-%d" % (day, n),
                                      "from_market": "true", "money": str(int(r * 1e7)), "status": "pending",
                                      "created_at": day + "T09:00:00+02:00"} for n, r in enumerate(ratios)])
        every = sorted(r for rs in nights.values() for r in rs)
        for window in (1.0, 2.0, 5.0, 6.0):    # a cut inside a batch keeps the whole batch, the newer ones first
            with set_constants(PRICE_WINDOW=window):
                got = sorted(r for (r,) in rows("SELECT ratio FROM recent_offers"))
            kept = sorted(nights["2026-09-02"] + (nights["2026-09-01"] if window > 4 else ()))
            assert got == kept, "a window of %s offers: %s, whole nights only" % (window, got)
        with set_constants(PRICE_WINDOW=100.0):
            assert sorted(r for (r,) in rows("SELECT ratio FROM recent_offers")) == every, "fewer offers than the window: all"
    print("offers self-test OK")


def _selftest_ledger() -> None:
    """Your eleven is frozen at a jornada's lock: a move counts for the jornadas that lock after it is made.
    Jornada 1 locks tonight, jornada 2 next week; the star's auction closes after tonight's lock."""
    lock1, lock2, late = "2026-11-06T20:00Z", "2026-11-13T20:00Z", "2026-11-06T21:24Z"
    layout = {"k": "POR", "d1": "DEF", "d2": "DEF", "d3": "DEF", "m1": "MED", "m2": "MED", "m3": "MED", "m4": "MED",
              "f1": "DEL", "f2": "DEL", "f3": "DEL"}
    e = {**{k: 1.0 if p != "DEF" else 4.0 for k, p in layout.items()}, "z_now": 9.0, "a_later": 9.0, "star": 8.0}
    gone = {("z_now", 2), ("a_later", 1)}        # z_now plays tonight only, a_later next week only
    sq = {**layout, "z_now": "DEF", "a_later": "DEF"}
    inp: dict[str, list[tuple[Any, ...]]] = {
        "dec_player": [(k, p, 1, None, 5e6 if p == "DEF" else 0.5e6) for k, p in sq.items()] + [("star", "MED", 0, 5e6, None)],
        "dec_odds": [], "dec_bid": [], "dec_money": [(0.2e6, 5e6 * len(sq), DEBT_SHARE, SQUAD_MAX, XI_SIZE)],
        "dec_lock": [(1, lock1), (2, lock2)], "dec_close": [("star", late)],
        "cell": [(k, j, 0.0 if (k, j) in gone else v) for j in (1, 2) for k, v in e.items()]}
    real = {(k, j): v for k, j, v in inp["cell"]}
    buy_star, sell = (0, "buy", "star", 5e6, 1.0, 0.0), lambda k: (0, "sell", k, 5e6, 1.0, 0.0)
    floor, ours = worth_on(inp, [buy_star], real, [1, 2])
    assert ours - floor == 7.0, "a buy closing after tonight's lock counts for jornada 2 only: 8 for a 1"
    assert worth_on(inp, [buy_star, sell("z_now")], real, [1, 2])[1] - floor == 7.0, \
        "a sale after the auctions keeps him for tonight; he scores nothing next week: free"
    assert worth_on(inp, [buy_star, sell("a_later")], real, [1, 2])[1] - floor < 0.0, "next week's scorer: the sale costs"
    flat = {k: v for k, v in inp.items() if k != "dec_lock"}
    assert worth_on(flat, [buy_star, sell("z_now")], real, [1, 2])[1] - floor < 7.0, \
        "without the locks every move counts everywhere: his sale costs tonight's points too"
    steps, _ = plan_rows(inp)
    assert [(kind, k) for _, kind, k, *_ in steps] == [("buy", "star"), ("sell", "z_now")], \
        "the buy is paid for by the sale that costs the fewest points in the jornadas it reaches"
    held = plan_rows({**inp, "dec_hold": [("z_now",)]})[0]
    assert [kind for _, kind, *_ in held] == ["buy", "sell"] and "z_now" not in {k for _, _, k, *_ in held}, \
        "a player you hold back is not sold: the buy is paid for by another sale"
    assert plan_rows({**inp, "dec_hold": [("star",)]})[0] == [], "nor is he bought: standing pat"
    due = schedule(inp, steps)
    assert due[0, "buy"].due == late and due[1, "sell"] == Due(late, AFTER_AUCTIONS), "cash at the lock is fine: sell after"
    assert after(inp, steps).blockers == [] and after(inp, [buy_star]).blockers == [("debt", 0.2e6 - 5e6)], \
        "bought and not paid for: owed once the auction is won, not at tonight's lock"
    sale = [buy_star, (1, "sell", "z_now", 5e6, 1.0, 0.0)]
    lapsing = {**inp, "dec_offer": [("z_now", late)]}
    assert schedule(lapsing, sale)[1, "sell"] == Due(late, OFFER_EXPIRES) and schedule(inp, sale)[1, "sell"].why == AFTER_AUCTIONS, \
        "a sale to an offer that lapses at the close is due by then, not after the auctions that close with it"
    early = {**inp, "dec_offer": [("z_now", "2026-11-06T18:00Z")]}
    assert worth_on(early, sale, real, [1, 2])[1] < worth_on(inp, sale, real, [1, 2])[1], \
        "an offer that lapses before tonight's lock: the sale is made before it, and costs tonight's points"
    half = [(0, "buy", "star", 5e6, 0.5, 0.0), (0, "sell", "a_later", 5e6, 1.0, 0.0)]
    offered = {**inp, "dec_offer": [("a_later", late)]}
    assert worth_on(offered, half, real, [1, 2])[1] - worth_on(inp, half, real, [1, 2])[1] == -4.0, \
        "a sale to an offer lapsing at the close is made whether the bid is won or lost: half the time for nobody (8 x 0.5)"
    swap = [(0, "buy", "star", 5e6, 1.0, 0.0), (0, "sell", "k", 5e6, 1.0, 0.0)]
    keeper = [("star", "POR", m, a, pr) if k == "star" else (k, p, m, a, pr) for k, p, m, a, pr in inp["dec_player"]]
    assert after({**inp, "dec_player": keeper}, swap).blockers == [], "a keeper bought for the keeper sold, after the auction: fine"
    assert after({**inp, "dec_player": keeper, "dec_offer": [("k", late)]}, swap).blockers == [("eleven", None)], \
        "sold to an offer that lapses with the auction: a lost bid leaves no keeper, said"
    tight = {**inp, "dec_close": [("star", "2026-11-05T22:24Z")]}
    assert schedule(tight, [buy_star, sell("z_now")])[0, "sell"] == Due(lock1, "before the lock") and \
        after(tight, [buy_star]).blockers == [("debt", 0.2e6 - 5e6)], \
        "a bid closing before the lock is charged to it: the sale that pays for it is due by the lock"
    print("ledger self-test OK")


def _selftest_search(n: int = 12, seed: int = 1, strict: bool = True) -> list[float]:
    """The loop against every mix there is, on seeded random leagues (12 players, 2 or 3 on offer, offers of 0.5M
    # to 25M, cash short or not): the plan is feasible (cash at the lock, a legal eleven, the bid limit), gains
    # at least what standing pat does, and is the best of all the mixes of up to three sales and any buys. The
    # loop is a search, not a proof: about one league in seventy has a better mix (two sales it does not shortlist)."""
    rng = random.Random(seed)
    misses: list[float] = []     # strict off: the points the plan leaves on each league it does not solve (the search's own measure)
    for _ in range(n):
        layout = ["POR"] + ["DEF"] * 4 + ["MED"] * 4 + ["DEL"] * 3
        own = {"m%02d" % i: p for i, p in enumerate(layout)}
        offer = {"c%d" % i: rng.choice(["DEF", "MED", "DEL"]) for i in range(rng.randint(2, 3))}
        e = {k: rng.choice([0, 0.5, 1, 2, 3, 4, 5, 6]) * 1.0 for k in own} | {k: rng.choice([3, 5, 7, 9, 12]) * 1.0 for k in offer}
        got = {k: rng.choice([0.5, 2, 4, 6, 9, 14, 25]) * 1e6 for k in own}
        ask = {k: rng.choice([4, 8, 12, 18]) * 1e6 for k in offer}
        cash = rng.choice([0, 1, 3, -2]) * 1e6
        inp: dict[str, list[tuple[Any, ...]]] = {"dec_player": [(k, p, 1, None, got[k]) for k, p in own.items()] + [(k, p, 0, ask[k], None) for k, p in offer.items()],
               "dec_odds": [], "dec_money": [(cash, sum(got.values()), DEBT_SHARE, SQUAD_MAX, XI_SIZE)],
               "cell": [(k, j, e[k]) for j in (1, 2, 3) for k in e]}
        if rng.random() < 0.6:    # half of the leagues with locks: jornada 1 locks tonight, the auctions close either side
            inp["dec_lock"] = [(1, "2026-11-06T20:00Z"), (2, "2026-11-13T20:00Z")]
            inp["dec_close"] = [(k, rng.choice(["2026-11-06T18:00Z", "2026-11-06T21:24Z"])) for k in offer]

        def mix(buys: tuple[str, ...], sells: tuple[str, ...]) -> list[tuple[Any, ...]]:
            return [(n, "buy" if n < len(buys) else "sell", k, (ask if n < len(buys) else got)[k], 1.0, 0.0)
                    for n, k in enumerate(buys + sells)]

        def feasible(moves: list[tuple[Any, ...]]) -> bool:
            made = after(inp, moves)
            return made.cash >= 0 and not made.blockers and sum(m[3] for m in moves if m[1] == "buy") <= cash + DEBT_SHARE * sum(got.values())

        best = max((expected_gap(inp, m) for nb in range(len(offer) + 1) for B in combinations(offer, nb)
                    for ns in range(4) for R in combinations(own, ns) if feasible(m := mix(B, R))), default=0.0)
        plan_steps, _ = plan_rows(inp)
        gap = max(best, 0.0) - expected_gap(inp, plan_steps)
        if strict:
            for step in plan_steps:     # a step's worth is its marginal in the plan: the plan's less the plan without it
                assert abs(step[5] - (expected_gap(inp, plan_steps) - expected_gap(inp, without(plan_steps, step[1], step[2])))) < 1e-9, \
                    "each step's worth is what the plan loses without it, from the one measure"
            assert (feasible(plan_steps) or best == 0.0) and abs(gap) < 1e-6, \
                "the plan is feasible and as good as the best mix of any buys and up to three sales"
        elif gap > 1e-6:
            misses.append(gap)
    if strict:
        print("search self-test OK")
    return misses


def _selftest_blockers() -> None:
    """after(): the cash each step leaves, the cash and squad at the lock, and what stops you; the same on the
    futures record() asks (nothing, the plan, the plan less one step)."""
    squad = {"k": "POR", "d1": "DEF", "d2": "DEF", "d3": "DEF", "m1": "MED", "m2": "MED", "m3": "MED",
             "m4": "MED", "f1": "DEL", "f2": "DEL", "f3": "DEL", "bench": "DEF"}

    def inp(cash: float, squad_max: int = 24) -> dict[str, list[tuple[Any, ...]]]:
        return {"dec_player": [(k, p, 1, None, 5e6) for k, p in squad.items()] + [("star", "MED", 0, 8e6, None)],
                "dec_odds": [], "cell": [], "dec_bid": [], "dec_money": [(cash, 60e6, 0.2, squad_max, 11)]}

    def sell(k: str, step: int = 0) -> tuple[Any, ...]:
        return (step, "sell", k, 5e6, 1.0, 0.0)

    def buy(amount: float = 10e6, step: int = 0) -> tuple[Any, ...]:
        return (step, "buy", "star", amount, 1.0, 1.0)

    quiet: list[tuple[str, float]] = []
    assert after(inp(5e6), []).blockers == quiet, "cash, no bids, no plan: nothing stops you"
    got = after(inp(5e6), [])
    assert (got.cash_then, got.cash, got.squad) == ([], 5e6, set(squad)), "no step: today's cash and squad"
    debt = [("debt", -3e6)]
    assert after(inp(-3e6), []).blockers == debt, "in debt, and a plan that does not clear it"
    got = after(inp(-3e6), [sell("bench")])
    assert got.blockers == quiet and got.cash == 2e6 and got.squad == set(squad) - {"bench"}, "the plan's sale clears it"
    got = after(inp(4e6), [], {"star": 10e6})
    assert got.blockers == [("debt", -6e6)] and got.squad == set(squad) | {"star"} and got.cash_then == [], \
        "a bid placed and won, nothing sold: debt; it is no step, so no running cash"
    got = after(inp(4e6), [buy(10e6)], {"star": 10e6})
    assert got.blockers == [("debt", -6e6)] and got.cash == -6e6, "a bid the plan keeps, placed too: counted once"
    got = after(inp(4e6), [buy(10e6), sell("bench")])
    assert got.cash_then == [-6e6, -1e6] and got.blockers == [("debt", -1e6)], \
        "running cash: 4M, less the 10M bid, plus the 5M sale"
    got = after(inp(4e6), [sell("bench", 0), buy(10e6, 1), sell("m4", 1)], {"x": 1e6})
    assert got.cash_then == [-6e6, -1e6, 4e6], \
        "in the plan's order (with a bid, every sale after the auctions: the buy first, then by step), won bids not among them"
    assert after(inp(5e6), [sell("k")]).blockers == [("eleven", None)], "the plan sells the only keeper: no eleven"
    assert after(inp(5e6, squad_max=12), [], {"star": 1e6}).blockers == [("squad", 1.0)], \
        "a won bid on a full squad: one over"
    assert after(inp(5e6, squad_max=12), [buy(1e6)]).blockers == [("squad", 1.0)], \
        "a buy on a full squad, with no sale to make room: one over"
    assert after(inp(5e6, squad_max=12), [buy(1e6), sell("bench", 0)]).blockers == quiet, "its paired sale makes the room"

    # What skipping a step leaves.
    def skips(i: dict[str, list[tuple[Any, ...]]], steps: list[tuple[Any, ...]]) -> dict[tuple[str, str], list[tuple[str, float | None]]]:
        out: dict[tuple[str, str], list[tuple[str, float | None]]] = {}
        for k, kind, blocker, amount in skip_rows(i, steps):
            out.setdefault((k, kind), []).append((blocker, amount))
        return out
    swap = [buy(8e6), sell("bench")]
    assert skips(inp(4e6), swap) == {("star", "buy"): [("none", None)], ("bench", "sell"): [("debt", -4e6)]}, \
        "4M cash, 8M bid, 5M sale: skip the buy and its sale goes too (nothing left); skip the sale and 4M is owed"
    assert skips(inp(-3e6), [sell("bench")]) == {("bench", "sell"): [("debt", -3e6)]}, "a sale that clears a debt"
    assert skips(inp(20e6), [sell("bench")]) == {("bench", "sell"): [("none", None)]}, \
        "a sale of one who scores nothing, cash ample: skipping it leaves nothing"
    own = [buy(8e6, 0), sell("m4", 1)]
    assert skips(inp(-2e6), own) == {("star", "buy"): [("none", None)], ("m4", "sell"): [("debt", -10e6)]}, \
        "a funding sale stands alone: skip it and the buy is unpaid; skip the buy and the sale alone clears the debt"

    # The schedule: a buy is due when his auction closes, a sale beside a bid after the last auction, one with no bid
    # by the lock; the plan's order follows it, whatever the step numbers. Property over every permutation of closes.
    lock = "2026-11-06T20:00Z"
    swap = [(0, "buy", "a", 5e6, 1.0, 1.0), (0, "sell", "x", 5e6, 1.0, 0.0), (1, "buy", "b", 4e6, 1.0, 1.0)]
    for ca, cb in (("2026-11-04T22:24Z", "2026-11-05T22:24Z"), ("2026-11-05T22:24Z", "2026-11-04T22:24Z")):
        sched: dict[str, list[tuple[Any, ...]]] = {"dec_close": [("a", ca), ("b", cb)], "dec_lock": [(1, lock)],
                                                   "dec_money": [(20e6, 0.0, 0.0, SQUAD_MAX, XI_SIZE)]}
        due = schedule(sched, swap)
        assert due[0, "buy"] == Due(ca, "auction closes") and due[1, "buy"] == Due(cb, "auction closes")
        assert due[0, "sell"] == Due(max(ca, cb), AFTER_AUCTIONS), "a sale: after the last auction, once the bids are won"
        order = [(k, kind) for _, kind, k, *_ in in_order(sched, swap)]
        assert order[:2] == sorted([("a", "buy"), ("b", "buy")], key=lambda x: {"a": ca, "b": cb}[x[0]]) and order[2] == ("x", "sell"), order
    assert schedule({"dec_lock": [(1, lock)]}, [(0, "sell", "x", 1e6, 1.0, 0.0)])[0, "sell"] == Due(lock, "before the lock")
    # The sale beside a bid waits for the auctions unless the bids closing by the lock leave the cash short there.
    late = "2026-11-06T21:24Z"
    for cash, close, want in ((0.2e6, late, Due(late, AFTER_AUCTIONS)), (0.2e6, "2026-11-05T22:24Z", Due(lock, "before the lock")),
                              (9e6, "2026-11-05T22:24Z", Due("2026-11-05T22:24Z", AFTER_AUCTIONS))):
        due = schedule({"dec_close": [("a", close)], "dec_lock": [(1, lock)], "dec_money": [(cash, 0.0, 0.0, SQUAD_MAX, XI_SIZE)]},
                       [(0, "buy", "a", 5e6, 1.0, 1.0), (0, "sell", "x", 5e6, 1.0, 0.0)])
        assert due[0, "sell"] == want, (cash, close, due[0, "sell"])
    assert schedule({}, swap)[0, "buy"] == Due(None, "before the lock"), "nothing known: no time, not a guess"
    assert [k for _, _, k, *_ in in_order({}, swap)] == ["a", "b", "x"], "no times: the buys by step, then the sale made after them"

    # The groups: every step once, a pair together, the groups in in_order's order, for any closes.
    for ca, cb in (("2026-11-04T22:24Z", "2026-11-05T22:24Z"), ("2026-11-05T22:24Z", "2026-11-04T22:24Z")):
        sched2: dict[str, list[tuple[Any, ...]]] = {"dec_close": [("a", ca), ("b", cb)], "dec_lock": [(1, lock)],
                                                    "dec_money": [(20e6, 0.0, 0.0, SQUAD_MAX, XI_SIZE)]}
        groups = in_groups(sched2, swap)
        assert sorted(s for g in groups for s in g) == sorted(swap) and all(len({s[0] for s in g}) == 1 for g in groups)
        firsts = [in_order(sched2, swap).index(g[0]) for g in groups]
        assert firsts == sorted(firsts), "the groups follow the plan's order"
        assert [g[0][0] for g in groups] == ([0, 1] if ca < cb else [1, 0]), "the earlier close first, whatever the step numbers"

    # Property: after(plan).cash is the cash plan_rows' fund() ended on, in several leagues.
    cells = {k: 4.0 for k in squad} | {"m4": 2.0, "bench": 1.0, "star": 8.0, "keeper": 3.0}
    for cash, extra, price in ((10e6, 0, 8e6), (4e6, 0, 8e6), (-3e6, 0, 8e6), (-12e6, 0, 20e6), (30e6, 12, 8e6), (2e6, 0, 20e6)):
        sq = {**squad, **{"x%02d" % i: "DEF" for i in range(extra)}}
        pos = {**sq, "star": "MED", "keeper": "POR"}
        rows_in: dict[str, list[tuple[Any, ...]]] = {"dec_player": [(k, pos[k], 1, None, 5e6) for k in sq] + [("star", "MED", 0, price, None), ("keeper", "POR", 0, 6e6, None)],
                   "dec_odds": [], "dec_bid": [], "dec_money": [(cash, 5e6 * len(sq), DEBT_SHARE, SQUAD_MAX, XI_SIZE)],
                   "cell": [(k, j, cells.get(k, 0.0)) for j in (1, 2) for k in pos if k in cells or k.startswith("x")]}
        steps, _ = plan_rows(rows_in)
        free = cash - sum(a if kind == "buy" else -a for _, kind, _, a, *_ in steps)
        res = after(rows_in, steps)
        assert res.cash == free, ("after().cash is fund()'s free", cash, steps)
        ordered = in_order(rows_in, steps)
        assert res.cash_then[-1:] == ([free] if steps else []) and len(res.cash_then) == len(steps), ordered


if __name__ == "__main__":
    _selftest()
