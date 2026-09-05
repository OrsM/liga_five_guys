"""
decide.py — every move you could make, ranked by whether it wins the league.

    python src/decide.py             # the table
    python src/decide.py --selftest

ONE QUESTION, ASKED OF EVERY ACTION: if I did this, how much does P(finishing
above each rival) move? Buy, sell, swap and steal are the same question with
different arguments, so there is one ranking and no verdict vocabulary.

WHAT THIS REPLACES. Points per million, value over replacement, the line, the
basket, Watch/pass/Cover/Hold, MAX_SLOT and THIN were all proxies for that
question, each with its own threshold, and twice this month two of them
contradicted each other in the same table. A simulation answers it directly.

THE STEAL IS WHY THIS MATTERS. Every rival player carries a buyout clause, so
cash can take him outright — and doing so REMOVES HIM FROM THEIR SQUAD. One
move both raises your total and lowers theirs, which is worth roughly twice
what the same player is worth from the free pool, and no per-player rate can
express it because the value depends on whose he is. 62 of the 75 players you
can buy today are somebody's.

COMMON RANDOM NUMBERS. Every option is simulated against the SAME seed, so the
seasons are identical and the difference between two options is the squads
rather than the weather. Without it a one-point edge is invisible under a
±120-point band and you would need tens of thousands of trials to see it;
with it, a few hundred will rank correctly.

WHAT IT CANNOT SEE, and each makes a hold look worse than it is:

  * Cash has option value — a better player appears next cycle — and nothing
    here models future markets, so holding cash scores zero rather than
    something. A standalone sale can therefore never look good.
  * Rivals do not respond. A steal that guts BurtonGM89 assumes he does not
    simply buy someone back.
"""

from __future__ import annotations

import csv
import datetime as dt
import itertools
import os
import sys
from dataclasses import dataclass, field, replace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ffcore.forecast import Bootstrap, pool_from_perjornada  # noqa: E402
from ffcore.league import MARKET, api_key  # noqa: E402
from ffcore.parse import fmt_money  # noqa: E402
from ffcore.score import SLOT, SLOT_MIN, _calibrated  # noqa: E402
from ffcore.text import norm  # noqa: E402
from ffcore.season import (LeagueState, XI_SIZE, best_xi,  # noqa: E402
                           simulate_many)
from ffcore.tidy import (run_now,  # noqa: E402
                         TIDY, SEASON, latest_only, load_api_market,  # noqa: E402
                         last_api_standings, load_api_offers, load_api_teams,
                         load_players)

__all__ = ["Action", "candidates", "rank", "Universe",
          "pending_sent", "pending_received"]

# Screening runs at a fraction of the final trial count. With common random
# numbers the RANKING settles long before the levels do, so this buys an order
# of magnitude of speed and costs only precision on options that lose anyway.
SCREEN_TRIALS = 250
# 3000 checked against 100-1500 for false precision — ranking (paired) is
# stable across all of them; p_win/expected_finish (levels) are not, and
# sim.trailing() thresholds those at 0.5. Do not lower without re-checking.
# Why: docs/notes/decide.md#trial-counts-screen_trials--final_trials
FINAL_TRIALS = 3000
KEEP = 12          # how many survive screening and get the full count

# A RELIABLE FLOOR ON TOP OF KEEP, NOT INSTEAD OF IT — 0/119 real deals in
# this league have ever been manager-to-manager, so an unreliable "listed"
# candidate must not crowd every reliable one out of the raw-gain-screened
# sample. _top_up() enforces this, additive, never displacing anything.
# Why: docs/notes/decide.md#keep_reliable_min--reliable-candidates-always-reach-the-full-pass
KEEP_RELIABLE_MIN = 6

# SAME MECHANISM, DIFFERENT AXIS: an efficient-but-modest candidate (small
# gain, tiny cost) can't win top-KEEP's raw-gain sort either, so it would
# never reach the full simulation at all. Topped up the same additive way.
# Why: docs/notes/decide.md#keep_value_min--efficient-but-modest-candidates-also-reach-the-full-pass
KEEP_VALUE_MIN = 4


@dataclass(frozen=True)
class Action:
    """One move. `sell` is empty for a purchase out of the balance.

    `sell` is a TUPLE because funding is not always one man. A target you
    cannot reach on cash plus one spare is not unaffordable — it is affordable
    by selling the men who never play, and there is no reason the table should
    omit that move. Keeping it a single string made the staircase in the value
    of cash invisible: every step of it needs more than one sale.
    """
    kind: str          # "buy" | "steal" | "swap" | "steal-swap" | "sell"
    buy: str = ""
    sell: tuple[str, ...] = ()
    cost: float = 0.0        # what leaves the balance
    proceeds: float = 0.0    # what a sale raises
    victim: str = ""         # the rival a steal takes from

    def __post_init__(self):
        # One man is the common case and callers pass him as a bare string.
        if isinstance(self.sell, str):
            object.__setattr__(self, "sell",
                               (self.sell,) if self.sell else ())

    @property
    def net(self) -> float:
        return self.cost - self.proceeds

    def label(self, names: dict[str, str] | None = None) -> str:
        # "steal X from Y" is reserved for paying a clause, which is the only
        # transaction that takes a man off somebody against their will. A
        # market purchase says "buy", however he came to be on the market.
        """The move in words. `names` swaps join keys for readable names —
        the report needs that and the terminal does not, and the grammar of a
        move is written here so there is only one of it."""
        def show(k):
            return (names or {}).get(k, k)
        sold = " + ".join(show(k) for k in self.sell)
        if self.kind == "sell":
            return "sell %s" % sold
        who = "clause %s from %s" % (show(self.buy), self.victim) \
            if self.victim else "buy %s" % show(self.buy)
        return who + (" · sell %s" % sold if sold else "")


@dataclass
class Universe:
    """Everything the decision needs, and nothing else.

    89 players get scored, not 643: the five squads plus the free agents on
    offer. A player nobody can field and nobody can buy cannot change a
    decision today, and pretending otherwise is most of why this repo grew a
    watchlist nobody read.
    """
    state: LeagueState
    forecaster: Bootstrap
    pos: dict[str, str]
    price: dict[str, float]      # what it costs ME to acquire him
    proceeds: dict[str, float]   # what selling him raises
    owner: dict[str, str]
    cash: float
    me: str
    # What the app says he is WORTH, for everyone in the universe. Distinct
    # from `price`, which is what it costs ME to get him: a free agent asks
    # about his value, a buyout clause runs a median 1.52x it, and the
    # difference between the two is money that never comes back.
    value: dict[str, float] = field(default_factory=dict)
    # Expected points for EVERY player the market prices, not only the 89 the
    # simulation needs — expected() returning 0.0 for an unscored player is
    # indistinguishable from worthless, and once scored Lamine Yamal that way.
    # Why: docs/notes/decide.md#universe-fields
    market_exp: dict[str, float] = field(default_factory=dict)
    # P(he starts) as ONE number — futbolfantasy's reading recalibrated
    # against confirmed line-ups and blended with analiticafantasy where it
    # has an opinion (ffcore.startprob). Printing "80/100" made the reader do
    # the weighting; the weighting is fitted, so it should be done once and
    # the answer shown. It is the same figure the forecast already multiplies
    # by, so the table and the simulation cannot disagree about him.
    start: dict[str, float] = field(default_factory=dict)
    # When each clause becomes payable again. A transfer locks it for about a
    # week and the app says until when; absent means locked, never open.
    clause_until: dict = field(default_factory=dict)
    # Every player's buyout clause, mine included — the app publishes them for
    # the whole league. What it costs ANYBODY to take ANYBODY, which is what a
    # rival needs to be able to answer back.
    clause: dict[str, float] = field(default_factory=dict)
    # HOW you would get each player: "free" (app dealing a free agent),
    # "listed" (owner's own sale, can refuse — see market_routes()), or
    # "clause" (only route is his buyout; the only one that's a real raid).
    # Why: docs/notes/decide.md#universe-fields
    route: dict[str, str] = field(default_factory=dict)
    # How many other managers are already bidding on a "listed" row — 0 for
    # a "free"/"clause" entry, since neither is a contest. What makes a
    # "listed" price a real number to plan around rather than a done deal.
    bids: dict[str, int] = field(default_factory=dict)
    # What each rival could spend. Estimates, and mostly negative: on the day
    # the response was modelled every one of them was overdrawn and could not
    # buy a soul until I paid one of their clauses.
    rival_cash: dict[str, float] = field(default_factory=dict)
    # The app's own daily allowance, `daily_bonus` in inputs/league.ini — the
    # one income every manager has that the activity feed cannot see (it
    # records deals, not gifts). Already inside `rival_cash` as an accrual
    # from each anchor (ffcore.league._estimate_cash); carried here as the
    # RATE, which is the thing a forward estimate needs and a level cannot
    # give — see days_to_afford().
    daily_bonus: float = 0.0
    # Each manager's own realised transaction behaviour, from the real
    # ledger. See rival_tempo(): this is the per-rival read that the
    # league-wide "listed never converts" prior (KEEP_RELIABLE_MIN) turned
    # out not to support, and that money DOES support.
    tempo: dict[str, dict] = field(default_factory=dict)
    # Provenance, for the report to print rather than for anything to act on:
    # a round part-played and how much of it is left, and any club or player
    # the app names in a way nothing else could join.
    part_played: dict[int, set[str]] = field(default_factory=dict)
    unjoined: list[str] = field(default_factory=list)
    # The source's own spelling, for display. Never a key: the keys are what
    # every dict here is keyed by, and they have already lost their accents.
    name: dict[str, str] = field(default_factory=dict)
    # How P(start) was arrived at — fitted against confirmed line-ups, or the
    # source's own figure. Printed, never inferred: it is the input everything
    # here rests on, and it does not look any different when it changes.
    start_note: str = ""
    # What a million euros has been worth in places, and how that was arrived
    # at. Set by the report, printed by it — see sim.cash_price_history().
    cash_note: str = ""
    # WHAT `cash` ABOVE ALREADY HAS SUBTRACTED — a pending bid of yours,
    # summed. Not read by anything that decides reach (that already
    # happened, in `cash` itself); carried so the report can say WHY cash
    # is short of the raw balance instead of leaving the reader to wonder.
    locked_cash: float = 0.0
    # A player YOU HOLD with a real pending offer on him, and the larger of
    # what he might raise. NOT read by anything that prices a sale — that
    # already happened, in `proceeds` — this is for the one thing a real
    # number cannot decide for you: whether accepting is worth doing. See
    # sim.ladder_rows()'s SAVE branch, the one reader that needs to know a
    # gap was closed by money you do not have yet, not money you do.
    received_offers: dict[str, float] = field(default_factory=dict)


def choosable(u) -> int:
    """A jornada whose eleven you can still pick, for judging a signing by.

    NOT the round in progress. Its eleven is locked and the players whose
    clubs have kicked off are absent from it, so the team it describes is
    whatever happens to be left — and the weakest man in that team can be a
    reserve scoring nothing. Anything at all then clears the bar a signing has
    to beat. Measured on 2026-08-18: the bar off jornada 1 was 0.00 and off
    jornada 2 was 2.73, and every journeyman in the league sat in between.
    """
    for j in u.state.jornadas:
        if j not in u.part_played:
            return j
    return u.state.jornadas[0] if u.state.jornadas else 0


def current_xi(u, who: str | None = None) -> tuple[dict[str, float], set[str]]:
    """(exp, xi) — this round's expected points and the best legal eleven
    `who` (default u.me) could field from them, right now.

    THE ONE COMPUTATION of "what is my/a rival's current best eleven worth" —
    seven call sites across sim.py and this module used to rebuild this pair
    by hand. `exp` is the SAME dict regardless of `who`; only the squad it's
    read against differs.
    Why: docs/notes/decide.md#current_xi--one-computation-seven-old-copies
    """
    exp = u.forecaster.expected(choosable(u))
    xi = set(best_xi(u.state.squads.get(who or u.me, {}), exp))
    return exp, xi


def xi_bar(exp: dict[str, float], xi) -> float:
    """The weakest man in an eleven — the number a signing has to clear.

    ONE FLAT NUMBER ACROSS ALL FOUR SLOTS — deliberately, not a missing
    per-position feature. A per-position bar would drop a candidate who
    helps by RESHAPING the XI (pushing one slot's count up, another's
    down) rather than beating his own slot's replacement level. This is
    the loosest SOUND screen — it lets a few uphelpful candidates through
    to the simulation (which then correctly prices them near-zero), but
    a stricter per-position bar would silently drop a real move instead.
    Why: docs/notes/decide.md#xi_bar--why-the-bar-is-flat-across-all-four-slots
    """
    return min((exp.get(k, 0.0) for k in xi), default=0.0)


def _squad_depth(mine_squad: dict[str, str]) -> dict[str, int]:
    """How many of each slot the squad currently carries."""
    depth: dict[str, int] = {}
    for slot in mine_squad.values():
        depth[slot] = depth.get(slot, 0) + 1
    return depth


def _safe_to_sell(u: Universe, k: str, depth: dict[str, int]) -> bool:
    """Would selling `k` still leave a legal shape fieldable?

    SLOT_MIN is a hard per-position floor, checked against the CURRENT
    squad (`depth`, mutated by the caller as sales are chosen). NOT ENOUGH
    ON ITS OWN, though: SLOT_MIN's floors sum to 8 but XI_SIZE is 11, so a
    squad can clear every position's minimum individually and still lack
    enough PLAYERS, TOTAL, to fill any real formation — `sum(depth.values())
    < XI_SIZE` catches that. Threshold is `XI_SIZE`, not `XI_SIZE - 1`:
    every caller's chain ends in exactly one buy that restores a player, so
    what matters is depth BEFORE this sale.
    Why: docs/notes/decide.md#_safe_to_sell--per-position-minimums-arent-enough-on-their-own
    """
    slot = u.pos.get(k, "MED")
    if sum(depth.values()) < XI_SIZE:
        return False
    return depth.get(slot, 0) - 1 >= SLOT_MIN.get(slot, 0)


def _weak_starters(u: Universe, xi: set[str], bar_exp: dict[str, float],
                    depth: dict[str, int], exclude: str = ""
                    ) -> list[tuple[str, float]]:
    """Fielded players, weakest first, whose sale wouldn't break a shape.

    DEAD WEIGHT ISN'T THE ONLY THING WORTH SELLING. It is the only thing
    that costs nothing to sell — a starter's sale costs real points on the
    pitch, which is exactly why these are tried only after dead weight
    runs out (see candidates()) or not at all until dead weight plus cash
    both fall short (see best_swap_for()). Whether a given starter is
    actually worth selling is the simulation's call, not this function's —
    this only says which sales are LEGAL to propose.
    """
    out = [(k, u.proceeds.get(k, 0.0)) for k in xi
           if k != exclude and _safe_to_sell(u, k, depth)]
    return sorted(out, key=lambda kv: bar_exp.get(kv[0], 0.0))


def candidates(u: Universe, expected: dict[str, float],
               budget: float | None = None) -> list[Action]:
    """Every affordable move, pruned to the ones that could plausibly help.

    The prune is on EXPECTED points, not on the simulation: it is a filter for
    what to simulate, so it only has to be roughly right, and it turns
    thousands of combinations into dozens. A candidate who would not make your
    eleven on expectation will not make it on a draw either.
    """
    cash = u.cash if budget is None else budget
    mine_squad = u.state.squads.get(u.me, {})
    mine = set(mine_squad)
    # The eleven the signing has to beat is one you can still pick — see
    # choosable(), which current_xi() already calls. `expected` may be any
    # round; the BAR never comes off a locked one, so a choosable() that
    # comes back with nothing (no jornada left to pick at all) falls back
    # to the round passed in rather than a bar of zero that clears nothing.
    bar_exp, xi = current_xi(u)
    if not bar_exp:
        bar_exp = expected
        xi = set(best_xi(u.state.squads[u.me], bar_exp))
    bar = xi_bar(bar_exp, xi)
    # Funding is not just bench: a starter is a legal sale too if SLOT_MIN
    # allows it (whether he's WORTH selling is the simulation's call).
    depth0 = _squad_depth(mine_squad)
    spare = sorted((k for k in mine if _safe_to_sell(u, k, depth0)),
                   key=lambda k: bar_exp.get(k, 0.0))
    # Dead weight (never starts, costs nothing to sell) tried first, biggest
    # first; weak starters (SLOT_MIN-safe) fill in once dead weight runs out.
    free = sorted(dead_weight(u), key=lambda kv: -kv[1]) \
        + _weak_starters(u, xi, bar_exp, dict(depth0))

    out: list[Action] = []
    for c, price in sorted(u.price.items(), key=lambda kv: kv[1]):
        if c in mine or bar_exp.get(c, 0.0) <= bar:
            continue
        victim = u.owner.get(c, "")
        # A raid is paying a clause; buying off a listing is an ordinary
        # purchase — measured denial value of a listing is zero.
        raid = bool(victim and victim != u.me
                    and u.route.get(c, "market") == "clause")
        kind = "clause" if raid else "buy"
        swap = kind + "-swap" if raid else "swap"
        if price <= cash:
            out.append(Action(kind, buy=c, cost=price,
                              victim=victim if raid else ""))
        # Funded by a sale: only the cheapest few spares are worth trying.
        for s in spare[:6]:
            got = u.proceeds.get(s, 0.0)
            if price <= cash + got:
                out.append(Action(swap, buy=c, sell=s, cost=price,
                                  proceeds=got,
                                  victim=victim if raid else ""))
        # Out of reach on cash + any ONE spare: the fewest sales that cover
        # it, dead weight then weak starters, depth re-checked as chosen.
        # Triggered on the REAL cash, not `budget` (which only widens what's
        # EMITTED) — keying the trigger to an unlimited budget once made
        # every target look cash-reachable and silently dropped every
        # multi-sale move.
        if price > u.cash + max((u.proceeds.get(s, 0.0) for s in spare),
                                default=0.0):
            sold, got = [], 0.0
            depth = dict(depth0)
            for k, raises in free:
                if price <= u.cash + got:
                    break
                # A sale raising $0 spends legality budget for nothing.
                if raises <= 0:
                    continue
                if not _safe_to_sell(u, k, depth):
                    continue
                depth[u.pos.get(k, "MED")] -= 1
                sold.append(k)
                got += raises
            if sold and price <= cash + got:
                out.append(Action(swap, buy=c, sell=tuple(sorted(sold)),
                                  cost=price, proceeds=got,
                                  victim=victim if raid else ""))
    return out


def locked(until: dict, key: str, now) -> bool:
    """Is his clause unpayable right now?

    A TRANSFER LOCKS A CLAUSE. The app publishes the moment it reopens, and
    until then no amount of money will take him — on 2026-08-18 that was every
    single rival player in this league, which meant the entire steal half of
    the report consisted of moves the app would have refused.

    A MISSING DATE COUNTS AS LOCKED. Not stated is not "available": treating
    an absent field as open is precisely how this was invisible in the first
    place, and the cost of being wrong is a table full of moves you cannot
    make. A free agent has no clause and never reaches here.
    """
    when = until.get(key)
    return True if when is None else when > now


def burn(u, a: Action) -> float | None:
    """Wealth a move destroys: what it costs, less what you end up holding.

    A FREE AGENT BURNS NOTHING. He asks about his market value, so you swap
    cash for an asset worth the same and can swap back tomorrow. A BUYOUT
    CLAUSE BURNS THE PREMIUM: it runs a median 1.52x market value in this
    league, from 1.00 to 2.65, and the app will only ever pay you the value
    back. That gap is gone for good.

    Never negative — buying under the odds does not pay you, it just does not
    charge you — and None when the value is unknown, because assuming no
    premium is exactly the error this exists to correct.
    """
    if not a.buy:
        return 0.0
    val = u.value.get(a.buy)
    if val is None:
        return None
    return max(0.0, a.cost - val)


def cash_price(reach) -> float | None:
    """Places per million, measured off `reach` = [(extra cash needed, Δpos)].

    THE PRICE OF MONEY IS NOT A CHOICE AND NOT A RATE CARD — it is read off
    what more money would actually buy you today, which is why every target
    gets screened and not only the affordable ones. The curve is a STAIRCASE:
    flat, because the best move you can already afford stays the best move,
    then a step when the balance clears the price of somebody better, then
    flat again. Averaged across the reachable range it comes out small, and
    that is the honest answer rather than a defect — a premium costs you
    points only if the money had somewhere better to go.

    None when there is nothing to measure against. Never zero for that: zero
    is a measurement meaning "more money buys nothing", and it is a real and
    common answer that must not be confused with "unknown".
    """
    pts = sorted((max(0.0, c), d) for c, d in reach)
    if len(pts) < 2 or pts[-1][0] <= 0:
        return None
    best_now = max((d for c, d in pts if c <= 0.0), default=None)
    if best_now is None:
        return None
    best_any = max(d for _, d in pts)
    span = max(c for c, _ in pts) / 1e6
    return max(0.0, (best_any - best_now) / span) if span else None


def respond(u, a: Action, after: dict) -> Action | None:
    """The best single answer the manager you just paid can make, or None.

    A CLAUSE PAYS THE OWNER — confirmed by Miguel against the app on
    2026-08-18, and not observable here: no clause purchase has ever happened
    in this league, so the activity feed has never had one to record. Paying
    one does not merely subtract a player from a rival — it hands him the
    money, and on the day this was written that was
    the difference between a league where nobody could act and one where the
    manager I am racing was the richest in it. Every rival was overdrawn; I
    was the only one who could buy anybody. A steal ends both of those facts
    at once, and scoring it without the answer priced a duel as an execution.

    HE PICKS ON EXPECTATION, like every other manager in this simulation: the
    acquisition that most improves his own eleven, within what he can now
    spend. One ply, and one move — he is not given a plan, only a reply.

    None when he still cannot afford anything, which is an answer too: it is
    what makes a cheap steal genuinely cheap.

    A market purchase gets no response, and the asymmetry is the point. Money
    paid to the app leaves the league; money paid for a clause changes sides.
    """
    if not a.victim or a.victim == u.me:
        return None
    budget = max(0.0, u.rival_cash.get(a.victim, 0.0)) + a.cost
    squad = after.get(a.victim, {})
    exp = u.forecaster.expected(u.state.jornadas[0]) if u.state.jornadas else {}
    base = sum(exp.get(k, 0.0) for k in best_xi(squad, exp))

    now = run_now()
    best, gain = None, 0.0
    for k, price in u.clause.items():
        if locked(u.clause_until, k, now):
            continue
        # NOT THE MAN YOU JUST TOOK. Left in, the best answer is nearly always
        # to buy him straight back at the same price — but a clause is reset
        # by the transfer that triggers it, and this repo has never observed
        # one to know at what. Excluding him is the conservative reading: it
        # makes the response weaker, not stronger, so it cannot manufacture
        # the conclusion it is here to test.
        if price > budget or k in squad or k == a.buy:
            continue
        holder = next((m for m, sq in after.items() if k in sq), "")
        if not holder or holder == a.victim:
            continue
        trial = dict(squad)
        trial[k] = u.pos.get(k, "MED")
        got = sum(exp.get(x, 0.0) for x in best_xi(trial, exp)) - base
        if got > gain:
            best, gain = Action("steal", buy=k, cost=price,
                                victim=holder), got
    return best


def rival_tempo(txns, now=None) -> dict[str, dict]:
    """Each manager's OWN realised transaction behaviour, from the real ledger.

    `{handle: {"buys", "sells", "bought", "sold", "days", "sell_rate",
    "idle"}}` — counts, euros, ledger span in days, gross sale proceeds per
    day, days since that manager's last deal.

    GROSS proceeds per day, not net cash flow — net is negative for every
    manager here (they're all still deploying a starting budget spent once),
    so it would predict everyone going infinitely broke. `days` is the span
    of the WHOLE ledger, not each manager's own first-to-last, so an idle
    manager's near-zero rate isn't hidden behind a shorter denominator.
    Why: docs/notes/decide.md#rival_tempo--gross-proceeds-per-day-not-net-cash-flow
    """
    from ffcore.tidy import ledger_stamp

    rows = [(ledger_stamp(t.get("date", "")), t) for t in txns]
    stamps = [s for s, _ in rows if s]
    if not stamps:
        return {}
    span = max((max(stamps) - min(stamps)).total_seconds() / 86400.0, 1.0)
    end = now or max(stamps)
    out: dict[str, dict] = {}

    def rec(h):
        return out.setdefault(h, {"buys": 0, "sells": 0, "bought": 0.0,
                                  "sold": 0.0, "days": span,
                                  "sell_rate": 0.0, "idle": None,
                                  "last": None})
    for when, t in rows:
        price = float(t.get("price") or 0)
        src = (t.get("from") or "").strip()
        dst = (t.get("to") or "").strip()
        for h, side in ((dst, "buy"), (src, "sell")):
            if not h or h == MARKET:
                continue
            r = rec(h)
            if side == "buy":
                r["buys"] += 1
                r["bought"] += price
            else:
                r["sells"] += 1
                r["sold"] += price
            if when and (r["last"] is None or when > r["last"]):
                r["last"] = when
    for r in out.values():
        r["sell_rate"] = r["sold"] / span
        if r["last"] is not None:
            r["idle"] = max(0.0, (end - r["last"]).total_seconds() / 86400.0)
    return out


def days_to_afford(cash, price: float, daily_bonus: float,
                   sell_rate: float = 0.0, ceiling=None) -> int | None:
    """Roughly how many days until a manager on `cash` could put `price`
    together. 0 if he already can; None if nothing says he ever could.

    `cash` is measured for me, estimated for a rival. `daily_bonus` is a
    configured fact (inputs/league.ini). `sell_rate` is measured, per rival,
    off his own realised gross sale proceeds (rival_tempo()). The
    COMBINATION — that he keeps raising money at his past rate while the
    allowance accrues — is the one guess this makes; it says nothing about
    whether he WANTS this player (see ffcore.bid.demand_summary() for that).
    Allowance-only was tried first and understates badly for a manager who's
    been actively selling. `ceiling` (his cash + his whole squad's value)
    caps the answer at None past it — he can't sell more than he holds.
    Why: docs/notes/decide.md#days_to_afford--measured-vs-guessed
    """
    if cash is None:
        return None
    if cash >= price:
        return 0
    if ceiling is not None and price > ceiling:
        return None
    rate = max(0.0, daily_bonus) + max(0.0, sell_rate)
    if rate <= 0:
        return None
    return int(-(-(price - cash) // rate))          # ceil, without math


def contest(u, key: str) -> list[tuple[str, int]]:
    """`[(manager, days), ...]` — who else could pay `key`'s own price, and
    how soon, soonest first. `[]` when nobody ever could.

    A clause is instant and cannot be refused by anybody — so a target
    sitting at a payable clause isn't an option Miguel owns, it's a thing
    the first solvent manager takes. Clause targets only, deliberately: a
    free-agent or listed row is a BID that can lose, and `Universe.bids`
    (the app's own numberOfBids) is the real contest signal there. The
    owner is not a contender for his own player and neither am I.
    Why: docs/notes/decide.md#contest--clause-targets-only-deliberately
    """
    if u.route.get(key) != "clause":
        return []
    price = u.price.get(key)
    if price is None:
        return []
    owner = u.owner.get(key, "")
    out = []
    for m in u.state.squads:
        if m == u.me or m == owner:
            continue
        tempo = u.tempo.get(m, {})
        # HIS WHOLE SQUAD IS THE WALL — see days_to_afford()'s `ceiling`.
        # Market value, not clause value: what a sale pays out at.
        ceiling = u.rival_cash.get(m, 0.0) + sum(
            u.value.get(k, 0.0) for k in u.state.squads.get(m, {}))
        d = days_to_afford(u.rival_cash.get(m), price, u.daily_bonus,
                           tempo.get("sell_rate", 0.0), ceiling)
        if d is not None:
            out.append((m, d))
    return sorted(out, key=lambda t: (t[1], t[0]))


def dead_weight(u) -> list[tuple[str, float]]:
    """[(player, what he raises)] for everyone in my squad who never starts.

    A man who makes none of the remaining elevens contributes nothing on the
    pitch, so selling him costs nothing and any offer is a gain — the one
    verdict reachable without valuing cash.

    Checked against every jornada you can still PICK, not every jornada
    left: a round already in progress has its eleven locked to a smaller
    pool (clubs that have kicked off drop out), so a man who only starts
    THERE isn't being fielded by any decision still open to you. Does not
    rank these against each other or say what to hold out for — see
    candidates(), where they pay for moves nothing else can reach.
    """
    mine = u.state.squads.get(u.me, {})
    choosable = [j for j in u.state.jornadas if j not in u.part_played] \
        or list(u.state.jornadas)
    starts: set[str] = set()
    for j in choosable:
        starts.update(best_xi(mine, u.forecaster.expected(j)))
    return sorted(((k, u.proceeds.get(k, 0.0)) for k in mine
                   if k not in starts),
                  key=lambda kv: -kv[1])


def apply(u: Universe, a: Action) -> dict[str, dict[str, str]]:
    """The squads as they would be after `a`. Pure — nothing is mutated."""
    sq = {m: dict(s) for m, s in u.state.squads.items()}
    for gone in a.sell:
        sq[u.me].pop(gone, None)
    if a.buy:
        # A steal removes him from his owner. This is the whole point.
        for m in sq:
            sq[m].pop(a.buy, None)
        sq[u.me][a.buy] = u.pos.get(a.buy, "MED")
    return sq




def _score_many(u: Universe, many: list, trials: int, seed: int):
    """Every candidate squad against ONE set of seasons. Same numbers."""
    return simulate_many(
        [LeagueState(squads=sq, jornadas=u.state.jornadas, me=u.me,
                     carried=u.state.carried) for sq in many],
        u.forecaster, trials=trials, seed=seed)


def paired(after, base, me) -> list[float]:
    """The per-trial difference `after` minus `base`, sorted.

    PAIRED, WITHIN THE SAME SEASONS — trial n with the move against trial n
    without it, so the difference is the squads rather than the weather. See
    rank()'s own note for what that buys: recalibrating P(start) on
    2026-08-18 moved a row's P(win) by 48 points and its paired figures by
    six.

    Sorted because every reader of this is a quantile of it (band() below,
    and rank()'s "helps", which only counts signs). Empty when the two
    Standings disagree about how many trials were run, which zip() makes
    silent — the callers all treat empty as "no answer", not as zero.
    """
    return sorted(x - y for x, y in zip(after.totals.get(me, []),
                                        base.totals.get(me, [])))


def band(pairs) -> tuple[float, float, float]:
    """(median, 10th, 90th) of paired()'s output — ONE definition of a band.

    rank() (a ranked move's d_pts/pts_lo/pts_hi) and sim.ladder_rows() (a
    squad member's or a candidate's own season band) print the same three
    numbers off the same paired differences, and each used to compute them
    from its own copy of these three index expressions. Two spellings of one
    quantile is how they come to disagree about what "the band" means.

    (0, 0, 0) for no pairs: nothing was simulated, so there is no spread to
    report, and every caller renders that as the no-change row it is.
    """
    if not pairs:
        return (0.0, 0.0, 0.0)
    return (pairs[len(pairs) // 2], pairs[int(0.1 * len(pairs))],
            pairs[int(0.9 * len(pairs))])


def best_swap_for(u: Universe, k: str, expected: dict[str, float]
                  ) -> Action | None:
    """The best real upgrade `k`'s sale funds — sell him, buy the
    highest-expected target his proceeds, your cash, AND (if that still
    falls short) the same dead-weight-then-weak-starters chain
    candidates() draws on can reach. None when nothing does.

    A DIFFERENT QUESTION from candidates()'s own swap search, which dedupes
    to one funding source per target and so can't answer for every held
    player individually — this asks "what would it take" scoped to ONE
    funding player. SAME SLOT ONLY: `expected()` puts every position on one
    points scale, but a SQUAD slot funding a swap is not a FORMATION slot —
    found 2026-08-25 when this once suggested selling bench players for the
    board's one goalkeeper, a real but meaningless number.
    Why: docs/notes/decide.md#best_swap_for--vs-ranks-own-funder-and-the-same-slot-fix
    """
    mine = u.state.squads.get(u.me, {})
    base_budget = u.cash + u.proceeds.get(k, 0.0)
    my_exp = expected.get(k, 0.0)
    slot = u.pos.get(k)

    # The extra chain: dead weight first, then weak starters, SLOT_MIN-safe,
    # k already counted as gone (never rides in this chain — see below).
    # Built once, walked once: extra_names[i]/extra_running[i] is "sell the
    # first i+1 of these, raise this much", a lookup not a fresh walk.
    depth = _squad_depth(mine)
    if slot in depth:
        depth[slot] -= 1
    xi_now = set(best_xi(mine, expected))
    # k is already the primary sale (in base_budget); dead_weight() doesn't
    # know to exclude him since he can be bench (never in any choosable XI).
    chain = [(p, r) for p, r in sorted(dead_weight(u), key=lambda kv: -kv[1])
            if p != k] + _weak_starters(u, xi_now, expected, dict(depth),
                                        exclude=k)
    extra_names: list[str] = []
    extra_running: list[float] = []
    got = 0.0
    for s, raises in chain:
        # Same guard as candidates()'s own chain: a $0 sale spends nothing.
        if raises <= 0:
            continue
        if not _safe_to_sell(u, s, depth):
            continue
        depth[u.pos.get(s, "MED")] -= 1
        got += raises
        extra_names.append(s)
        extra_running.append(got)
    max_budget = base_budget + got

    # Every affordable upgrade, not just a running max — collected first so
    # the pick below can weigh cost, not just grab the highest raw expected.
    # Before this (2026-08-31), the highest-expected target the budget
    # reached could be a heavily marked-up clause raid over a materially
    # cheaper target giving up almost none of the gain — the same
    # price-blindness sim._move_rank_key() already rejects for BUY rows.
    candidates_found = [(expected.get(c, 0.0), price, c)
                        for c, price in u.price.items()
                        if c != k and c not in mine and u.pos.get(c) == slot
                        and price <= max_budget
                        and expected.get(c, 0.0) > my_exp]
    best_c, best_exp, best_price = None, my_exp, None
    if candidates_found:
        # Same tolerance as sim.VALUE_TOLERANCE (kept as a local literal to
        # avoid a decide<->sim import cycle — sim.py imports decide already).
        best_gain = max(e for e, _, _ in candidates_found) - my_exp
        floor = 0.90 * best_gain
        near_best = [t for t in candidates_found if t[0] - my_exp >= floor]
        best_exp, best_price, best_c = min(near_best, key=lambda t: t[1])
    if best_c is None:
        return None
    if best_price <= base_budget:
        sell, proceeds = (k,), u.proceeds.get(k, 0.0)
    else:
        n = next(i for i, g in enumerate(extra_running)
                if g >= best_price - base_budget) + 1
        sell = tuple(sorted((k, *extra_names[:n])))
        proceeds = u.proceeds.get(k, 0.0) + extra_running[n - 1]
    best_sell, best_proceeds = sell, proceeds
    victim = u.owner.get(best_c, "")
    raid = bool(victim and victim != u.me
               and u.route.get(best_c, "market") == "clause")
    return Action("clause-swap" if raid else "swap", buy=best_c,
                 sell=best_sell, cost=best_price, proceeds=best_proceeds,
                 victim=victim if raid else "")


def _top_up(top: list[tuple], screened: list[tuple], ok, rank_key,
           minimum: int) -> list[tuple]:
    """Ensure at least `minimum` of `top` satisfy `ok(d, a)`, adding more
    from `screened` (best-`rank_key`-first) on top of `top` — never
    displacing anything already there, never adding a key `top` already
    holds. Shared bookkeeping behind both KEEP_RELIABLE_MIN and
    KEEP_VALUE_MIN, which differ only in `ok`/`rank_key`.
    """
    kept = {a.buy or a.sell for _, a in top}
    have = sum(1 for d, a in top if ok(d, a))
    if have >= minimum:
        return top
    more = sorted((t for t in screened
                   if (t[1].buy or t[1].sell) not in kept and ok(*t)),
                  key=rank_key)
    return top + more[:minimum - have]


def value_rate(pts, cost) -> float | None:
    """Season points per million of a GENUINE positive cost, or None.

    Shared by rank() (a BUY row's net spend) and sim.ladder()/
    ladder_rows() (a SAVE row's shortfall) — the same "is the price worth
    it" question, only ever answered when there is a real price: `cost`
    <= 0 means nothing to divide by (a funded sale that pays for itself,
    or a degenerate shortfall), and the raw points figure beside this one
    already says whether that is worth doing.
    """
    if pts is None or cost is None or cost <= 0:
        return None
    return pts / (cost / 1e6)


def rank(u: Universe, acts: list[Action], seed: int = 1,
         price=None, extra: list[tuple[str, Action]] = ()) -> tuple:
    """Screen wide and cheap, then re-run the survivors properly.

    Returns `(rows, base, measured, bands)`. Rows carry the change in
    expected finishing position and in P(above) each rival.

    `extra` is `[(key, Action), ...]`, scored in the SAME final pass (the
    draw doesn't depend on the squad, so a second pass would pay the
    ~1.2s of drawing again for nothing) and come back as `bands`,
    `{key: (median, lo, hi, action)}` — `key` given explicitly because a
    held player's own swap (see best_swap_for()) sells him, not the man
    bought, and an Action alone can't say which side a caller meant. A
    `key` already answered by a real BUY row is dropped from `extra` — its
    own band, off the squad the victim's response leaves behind, answers
    better than a bare swap would.

    `acts` may contain moves you CANNOT afford today — screening them is
    how the price of cash gets measured (see cash_price()).

    `price` is places per million; given one, every move is CHARGED for the
    wealth its clause destroys. Without one, today's own measurement is used.
    Why: docs/notes/decide.md#rank--screening-top-up-and-value
    """
    # ONE DRAW PASS FOR THE WHOLE SCREEN, same seed for every option — see
    # ffcore.season.simulate_many; the ranked numbers are unchanged.
    screen = _score_many(u, [u.state.squads] + [apply(u, a) for a in acts],
                         SCREEN_TRIALS, seed)
    base_s, rest = screen[0], screen[1:]
    screened, reach = [], []
    for a, r in zip(acts, rest):
        d = base_s.expected_position() - r.expected_position()
        reach.append((a.cost - a.proceeds - u.cash, d))
        if a.cost <= u.cash + a.proceeds:
            screened.append((d, a))
    measured = cash_price(reach)
    lam = price if price is not None else measured

    # One row per target: 4 funding variants of one signing screen
    # identically (selling dead weight changes nothing on the pitch), so
    # keeping all 4 wastes the final pass. Ties break toward spending less.
    pick: dict[str, tuple] = {}
    for d, a in screened:
        k = a.buy or a.sell
        cur = pick.get(k)
        if cur is None or (d, -a.net) > (cur[0], -cur[1].net):
            pick[k] = (d, a)
    screened = sorted(pick.values(), key=lambda t: (-t[0], t[1].net))

    # ...and one for the survivors, at the full count.
    top = screened[:KEEP]
    # Top up with reliable candidates (KEEP_RELIABLE_MIN) — additive, never
    # displacing a listed candidate that made top-KEEP honestly.
    top = _top_up(top, screened,
                 ok=lambda d, a: u.route.get(a.buy, "free") != "listed",
                 rank_key=lambda t: -t[0], minimum=KEEP_RELIABLE_MIN)
    # Top up with the most efficient candidates (KEEP_VALUE_MIN), same
    # additive shape, independent axis. "Efficient" is RELATIVE — the best
    # few by ratio among genuine-gain-and-spend candidates, computed once,
    # up front, rather than "d>0 and net>0" (which top-KEEP already
    # satisfies for nearly every candidate, making the top-up a no-op).
    ratio = lambda t: t[0] / (t[1].net / 1e6)                    # noqa: E731
    best_value = {a.buy or a.sell for _, a in
                 sorted((t for t in screened if t[0] > 0 and t[1].net > 0),
                        key=lambda t: -ratio(t))[:KEEP_VALUE_MIN]}
    top = _top_up(top, screened,
                 ok=lambda d, a: (a.buy or a.sell) in best_value,
                 rank_key=lambda t: -ratio(t), minimum=KEEP_VALUE_MIN)
    keep = [a for _, a in top]
    answers, afters = [], []
    for a in keep:
        after = apply(u, a)
        # He answers before the season is played — a clause pays the owner,
        # who can respond with that money, so this isn't a pure subtraction.
        ans = respond(u, a, after)
        if ans is not None:
            after = {m: dict(sq) for m, sq in after.items()}
            for m in after:
                after[m].pop(ans.buy, None)
            after[a.victim][ans.buy] = u.pos.get(ans.buy, "MED")
        answers.append(ans)
        afters.append(after)
    # Anything `extra` asks about a player already answered by a real ranked
    # row is dropped here. Buy side ONLY, deliberately — checking the sell
    # side too once dropped Jon Moncayola's own OUT-row band for the
    # unrelated reason that he happened to fund somebody else's top move.
    answered = {a.buy for a in keep if a.buy}
    rest = [(k, a) for k, a in extra if k not in answered]
    final = _score_many(u, [u.state.squads] + afters
                        + [apply(u, a) for _k, a in rest], FINAL_TRIALS, seed)
    base, scored = final[0], final[1:len(afters) + 1]
    bands = {k: (*band(paired(r, base, u.me)), a)
            for (k, a), r in zip(rest, final[len(afters) + 1:])}
    rivals = [m for m in u.state.squads if m != u.me]
    out = []
    for a, ans, r in zip(keep, answers, scored):
        b_ = burn(u, a)
        charge = 0.0 if (lam is None or b_ is None) else lam * b_ / 1e6
        gross = base.expected_position() - r.expected_position()
        # PAIRED, WITHIN THE SAME SEASONS — see paired()'s own docstring,
        # which is where this used to be spelled out and where the band
        # quantiles below used to be spelled a second time.
        pairs = paired(r, base, u.me)
        d_pts, lo, hi = band(pairs)
        out.append({
            "action": a,
            "helps": (sum(1 for d in pairs if d > 0) / len(pairs)
                      if pairs else 0.0),
            "d_pts": d_pts,
            "pts_lo": lo,
            "pts_hi": hi,
            "d_pos": gross - charge,
            "gross": gross,
            "burn": b_,
            "charge": charge,
            "answer": ans,
            "d_win": r.position().get(1, 0.0) - base.position().get(1, 0.0),
            "d_beat": {v: r.beat(v) - base.beat(v) for v in rivals},
            "mean": r.mean(u.me),
            # VALUE FOR MONEY: season points per million actually paid.
            # `d_pts`, not `d_pos` — the table's own unit is season points.
            # Only defined for a genuine spend (net > 0): a sale raising
            # more than it costs needs no rate, it's just obviously worth
            # doing. Already points-over-replacement (no second `value_vor`
            # needed) because `d_pts` is a paired marginal whose "with"
            # side re-picks best_xi() over every legal shape — replacement
            # level computed per candidate, not assumed per slot. NOT the
            # old λ (retired 2026-08-17), which measured against a fixed
            # current-eleven baseline that moved under it.
            # Why: docs/notes/decide.md#rank--screening-top-up-and-value
            "value": value_rate(d_pts, a.net),
        })
    rows = sorted(out, key=lambda d: (-d["d_pos"], d["action"].net))
    return rows, base, measured, bands


def rounds_left(matches, teams) -> tuple[list[int], dict[int, set[str]], list]:
    """(jornadas still to come, who has already played one, unjoined clubs).

    A jornada with every score in is finished and isn't simulated. One with
    SOME scores in (the August case) pays twice if simulated whole — see
    docs/notes/decide.md#rounds_left--a-jornada-with-some-scores-in-still-counts.
    So the round stays and the clubs inside it that are done drop out.
    `teams` is the market's own club spellings (see club_key()). Still
    doesn't model: the eleven for a round in progress is already LOCKED,
    and the simulator re-picks it from whoever's left.
    """
    js = {r["jornada"] for r in matches if (r.get("jornada") or "").isdigit()}
    finished = {j for j in js
                if all(r.get("score") for r in matches if r["jornada"] == j)}
    rem = sorted(int(j) for j in js - finished)

    played: dict[int, set[str]] = {}
    unjoined: list[str] = []
    for r in matches:
        j = r.get("jornada") or ""
        if not j.isdigit() or int(j) not in rem or not r.get("score"):
            continue
        for side in (r.get("home"), r.get("away")):
            club = club_key(side, teams)
            if not club:
                if side and side not in unjoined:
                    unjoined.append(side)
                continue
            played.setdefault(int(j), set()).add(club)
    return rem, played, unjoined


def next_then_rest(base: dict, base_rest: dict, rem: list[int],
                   played: dict[int, set[str]], club: dict[str, str]
                   ) -> dict[int, dict]:
    """Bootstrap's own `per_jornada` — `base` for a player's FIRST
    remaining jornada, `base_rest` for every one after it.

    `base` carries this week's editorial reading (a suspension, a knock) —
    real news about the one game it was published for, not "he plays in
    March" too. "First remaining jornada" is PER PLAYER: a partial round
    mid-sweep drops a player from `rem[0]` once his own club has played it
    (see rounds_left()), so his true next jornada is wherever `played`
    first shows his club clear.
    Why: docs/notes/decide.md#next_then_rest--apply_fixtures
    """
    first_seen: set[str] = set()
    out: dict[int, dict] = {}
    for j in rem:
        this_j = ({k: v for k, v in base.items()
                  if club.get(k) not in played[j]}
                 if j in played else base)
        layer = {}
        for k, v in this_j.items():
            if k in first_seen:
                layer[k] = base_rest.get(k, v)
            else:
                first_seen.add(k)
                layer[k] = v
        out[j] = layer
    return out


def apply_fixtures(per_jornada: dict[int, dict], sboard: dict[int, dict],
                   club: dict[str, str], pos: dict[str, str],
                   ppm_of: dict[str, float]) -> dict[int, dict]:
    """`per_jornada`, with the POINTS half repriced against THAT jornada's
    real opponent (season_board()) instead of the single next-fixture
    factor `base`/`base_rest` were built with — the schedule is published
    for the whole season, so pricing jornada 20 off jornada 3's opponent
    was just not having asked. P(start) is untouched — a different
    question next_then_rest() already answers. A player season_board()
    has no Match for keeps his frozen next-fixture number.
    Why: docs/notes/decide.md#next_then_rest--apply_fixtures
    """
    out: dict[int, dict] = {}
    for j, layer in per_jornada.items():
        board_j = sboard.get(j, {})
        new_layer = {}
        for k, (pts, p) in layer.items():
            m = board_j.get(club.get(k, ""))
            if m is None or k not in ppm_of:
                new_layer[k] = (pts, p)
                continue
            fix = (m.def_factor if pos.get(k) in ("POR", "DEF")
                  else m.atk_factor)
            new_layer[k] = (max(0.0, ppm_of[k] * fix), p)
        out[j] = new_layer
    return out


def phantom_fill(squads: dict[str, dict[str, str]], per_jornada: dict[int, dict],
                 pos: dict[str, str]
                 ) -> tuple[dict[str, dict[str, str]], dict[int, dict]]:
    """Squads and per_jornada, with one AVERAGE-PLAYER-AT-THE-POSITION
    phantom added per position any manager is short of SLOT_MIN in.

    Without this, a squad short one SLOT_MIN position can't fill ANY legal
    formation — best_xi() returns [], scoring zero every remaining jornada
    with zero variance. The phantom is an AVERAGE, not a specific player or
    invented number — a real player's key would drift day to day and could
    double as a real candidate — computed off the same real per-jornada
    data (points and P(start)) every other player at that position already
    carries. No `matches` entry (the same "no evidence, no widening" rule
    applied to a brand-new player). Keyed `__phantom_<manager>_<slot>_<n>`,
    a form no real player id can take.
    Why: docs/notes/decide.md#phantom_fill--why-a-short-squad-gets-a-phantom-and-why-its-an-average
    """
    from ffcore.score import SLOT_MIN

    squads = {m: dict(sq) for m, sq in squads.items()}
    per_jornada = {j: dict(layer) for j, layer in per_jornada.items()}
    # ONE AVERAGE PER (jornada, position), computed once off every REAL
    # scored player at that position — not per manager, so five managers
    # all short the same position share the identical, real, jornada-
    # varying number, exactly as if a league-average man had filled in.
    avg: dict[int, dict[str, tuple[float, float]]] = {}
    for j, layer in per_jornada.items():
        by_pos: dict[str, list[tuple[float, float]]] = {}
        for k, (pts, p) in layer.items():
            s = pos.get(k)
            if s:
                by_pos.setdefault(s, []).append((pts, p))
        avg[j] = {s: (sum(v[0] for v in vs) / len(vs),
                     sum(v[1] for v in vs) / len(vs))
                 for s, vs in by_pos.items() if vs}

    for m, sq in squads.items():
        counts: dict[str, int] = {}
        for slot in sq.values():
            counts[slot] = counts.get(slot, 0) + 1
        for s, n in SLOT_MIN.items():
            short = n - counts.get(s, 0)
            for i in range(short):
                key = "__phantom_%s_%s_%d" % (m, s, i)
                sq[key] = s
                for j in per_jornada:
                    if s in avg.get(j, {}):
                        per_jornada[j][key] = avg[j][s]
    return squads, per_jornada


def club_key(raw, teams, xw=None) -> str:
    """One club, one key, whichever page spelled it — or "" if it will not
    place.

    Three sources name clubs three ways: the market says "Rayo", the fixture
    page "rayo-vallecano", and the probable-XI page files most players under
    the first and a handful under the second. Folding the case and the
    punctuation is not enough, because "rayo" and "rayo vallecano" are still
    two strings.

    THE CROSSWALK ANSWERS THIS when there is one — clubs.csv holds every
    spelling against one id, resolved once. Without it, the fallback is the
    same `match_team` the fixture board uses, against the market's list.

    "" for a name nothing can place, and "" is never equal to a club: an
    unplaceable club must not accidentally compare equal to another one.
    """
    if xw is not None:
        hit = xw.club(ff_slug=raw, name=raw)
        if hit:
            return hit
    from ffcore.fixture import match_team
    hit = match_team(raw or "", teams)
    return norm(hit) if hit else ""


# WHICH SELLER VALUE IS A FREE AGENT, so a new one added by the app defaults
# to the safe reading (the app dealing him) rather than silently starting to
# treat every row as a contested rival listing — see market_routes()'s own
# docstring for why the two are not the same transaction.
LISTED_SELLER = "marketPlayerTeam"


def market_routes(mkt: list[dict], key_of) -> tuple[dict[str, float],
                                                    dict[str, str],
                                                    dict[str, int]]:
    """(price, route, bids) from api_market.csv's own rows.

    `seller` has always said which is which: `marketPlayerLeague` is the app
    dealing a free agent, `marketPlayerTeam` is a manager's own listing —
    not the same transaction, the same reason a clause and an ordinary buy
    aren't (only one has a real owner who can say no).
    `key_of(row)` is handed in (not imported) so this stays testable on
    synthetic rows.
    """
    price: dict[str, float] = {}
    route: dict[str, str] = {}
    bids: dict[str, int] = {}
    for r in mkt:
        k = key_of(r)
        if not k or not r.get("sale_price"):
            continue
        price[k] = float(r["sale_price"])
        route[k] = "listed" if r.get("seller") == LISTED_SELLER else "free"
        bids[k] = int(r.get("bids") or 0)
    return price, route, bids


def pending_sent(mkt: list[dict]) -> float:
    """Money already gone against a bid of yours still pending, summed.

    The app holds it against the bid until it is accepted, rejected or
    withdrawn — it is not free to spend again on something else today,
    however the raw balance reads. Read off the same `bid` field a sent
    offer is shown from (see sources.parse_api_market's own note on why
    that field is always YOUR bid, never anyone else's) — one field, one
    reading, so the number here and the one on screen cannot disagree.
    """
    return sum(float(r["bid_money"]) for r in mkt
              if (r.get("bid_status") or "") == "pending" and r.get("bid_money"))


def pending_received(offers: list[dict], pt_to_key: dict[str, str]
                     ) -> dict[str, float]:
    """{player you hold: the largest pending offer on him}, or {}.

    A REAL PENDING OFFER BEATS A GUESS. What load() otherwise prices a sale
    at is the market's own valuation — an estimate nothing has tested — and
    a real bid sitting on a player you have actually listed is ground truth
    for at least that much. The caller applies it as a FLOOR on `proceeds`,
    never an overwrite: another bidder could still beat it before you act.

    `pt_to_key` joins the API's own ownership-record id to this repo's key
    — see load()'s own note on why that join is built once, off api_teams,
    and handed in rather than re-derived here.
    """
    out: dict[str, float] = {}
    for r in offers:
        if (r.get("status") or "") != "pending":
            continue
        k = pt_to_key.get(r.get("player_team_id") or "")
        money = float(r.get("money") or 0)
        if not k or not money:
            continue
        out[k] = max(out.get(k, 0.0), money)
    return out


def offer_combos(u: Universe) -> list[tuple[str, Action]]:
    """The minimal combinations of real pending offers that clear an
    overdraft, as `extra` for rank() — only fires when cash is actually
    negative (the jornada won't lock overdrawn, so something must be
    accepted). MINIMAL covers only, subset-sum sense: a combo with room to
    spare when a smaller one already clears it never appears. Pure sell,
    no rebuy priced — conflating the two is what Action's own `net` keeps
    apart. Keyed "OFFERS:a|b" since no single key can stand for a combo.
    """
    mine = u.state.squads.get(u.me, {})
    offers = {k: v for k, v in u.received_offers.items()
             if k in mine and v > 0}
    deficit = -u.cash
    if deficit <= 0 or not offers:
        return []
    names = sorted(offers)
    covers: list[tuple[str, ...]] = []
    for r in range(1, len(names) + 1):
        for combo in itertools.combinations(names, r):
            cs = set(combo)
            if any(set(c) <= cs for c in covers):
                continue
            if sum(offers[k] for k in combo) >= deficit:
                covers.append(combo)
    return [("OFFERS:" + "|".join(combo),
            Action("sell", sell=combo,
                  proceeds=sum(offers[k] for k in combo)))
           for combo in covers]


def load(trials_pool=None) -> Universe:
    """Assemble the universe from the store. The only IO in this module."""
    # The run's one model — the same League and the same Scorer report.py
    # describes. See ffcore/model.py.
    from ffcore.model import session
    _m = session()
    lg, sc = _m.lg, _m.sc
    players = load_players()

    m = latest_only(list(csv.DictReader(open(TIDY / "matches.csv"))))
    # The market's spelling of every club, and only the market's: it is the
    # canonical side of the join in club_key().
    mkt_teams = sorted({(r.get("team") or "").strip()
                        for r in (lg.market.latest().values()
                                  if lg.market is not None else [])
                        if (r.get("team") or "").strip()})
    rem, played, unjoined_clubs = rounds_left(m, mkt_teams)

    teams = load_api_teams()
    mkt = load_api_market()
    # Ownership is League's, not re-derived — a second, weaker join here
    # would mean rival players nobody can be recognized as owning.
    owner = dict(lg.owner)
    me = lg.cfg.me

    squads = {mgr: {k: SLOT[(players[k].get("pos") or "").lower()]
                    for k in lg.squad(mgr)
                    if k in players
                    and (players[k].get("pos") or "").lower() in SLOT}
              for mgr in lg.managers}

    # What it costs ME — see market_routes() for the free/listed/clause
    # split. Both sides join through ffcore.league.api_key on the market's
    # spelling; a clause on an unresolvable name is a rival's player who
    # silently cannot be bought at all.
    index = latest_only(lg.market.rows) if lg.market is not None else []
    price, route, bids = market_routes(
        mkt, lambda r: api_key(r["player_name"], "", lg.market, owner,
                               index, r.get("market_value")))
    now = run_now()
    clause_until: dict = {}
    # The app's own ownership-record id -> this repo's key, built in the
    # one loop that already resolves a key for every api_teams row rather
    # than re-resolving the same rows a second time for one more field.
    pt_to_key: dict[str, str] = {}
    for r in teams:
        k = api_key(r["player_name"], r["manager"], lg.market, owner, index,
                    r.get("market_value"))
        if not k:
            continue
        if r.get("player_team_id"):
            pt_to_key[r["player_team_id"]] = k
        raw = (r.get("buyout_until") or "").strip()
        if raw:
            try:
                clause_until[k] = dt.datetime.fromisoformat(raw)
            except ValueError:
                pass
        # A clause you cannot pay is not a price — the app refuses outright.
        if r["manager"] == me or not (r.get("buyout") or "").strip():
            continue
        if locked(clause_until, k, now):
            continue
        if k not in price:
            route[k] = "clause"
        price.setdefault(k, float(r["buyout"]))

    proceeds = {k: float((players[k] or {}).get("value") or 0)
                for k in squads.get(me, {})}
    received_offers = pending_received(load_api_offers(), pt_to_key)
    for k, money in received_offers.items():
        if k in proceeds:
            proceeds[k] = max(proceeds[k], money)
    # Every clause, mine included — a rival can't answer back without them.
    clause: dict[str, float] = {}
    for r in teams:
        if not (r.get("buyout") or "").strip():
            continue
        k = api_key(r["player_name"], r["manager"], lg.market, owner, index,
                    r.get("market_value"))
        if k:
            clause.setdefault(k, float(r["buyout"]))
    rival_cash = {h: (lg[h].cash.value or 0.0) for h in lg.managers
                  if h != me}
    # What the app says everyone is worth — the figure a sale pays out at,
    # see burn() for the gap between this and a buyout clause.
    value = {k: float((v or {}).get("value") or 0) for k, v in players.items()
             if (v or {}).get("value")}

    pos, base, base_rest = {}, {}, {}
    # A display name for every player the index knows, not just those in
    # the universe — a key with no name here prints as a raw number.
    name = {k: (rec.get("name") or k) for k, rec in players.items()}
    universe = set(price) | {k for s in squads.values() for k in s}
    # Scored once per player, kept rather than re-derived for `matches`.
    scored: dict[str, object] = {}
    for k in universe:
        rec = players.get(k)
        if not rec:
            continue
        pos[k] = SLOT.get((rec.get("pos") or "").lower(), "MED")
        row = sc.row_for(k)
        s = sc.score(row) if row else None
        scored[k] = s
        base[k] = ((max(0.0, s.ppm * s.fix), min(1.0, (s.pct_used or 0) / 100))
                   if s else (2.0, 0.5))
        # Same pair, one jornada later — only the START side differs (a
        # rate this thin has no more evidence by jornada 10 than jornada 3,
        # but P(start) does once he has current-season minutes).
        base_rest[k] = ((max(0.0, s.ppm * s.fix),
                        min(1.0, (s.pct_rest or 0) / 100))
                       if s else (2.0, 0.5))

    # Everyone the market prices, scored the same way — about the players
    # NOT in the simulation's universe (what might come up later).
    market_exp: dict[str, float] = {}
    start: dict[str, float] = {}
    for k, rec in players.items():
        row = sc.row_for(k)
        sc_ = sc.score(row) if row else None
        if sc_ is not None:
            start[k] = min(1.0, (sc_.pct_used or 0) / 100)
            market_exp[k] = max(0.0, sc_.ppm * sc_.fix) * start[k]

    pool = pool_from_perjornada(
        csv.DictReader(open(SEASON / "live" / "perjornada_2026-27.csv")))
    # A round in progress carries only players who haven't played it yet —
    # everyone else's real points are already in `carried` (rounds_left()).
    club = {k: club_key(players[k].get("team"), mkt_teams)
            for k in base if k in players}
    # How many matches each rate rests on, so a thin record widens the
    # season the forecaster draws instead of passing as a fact.
    matches = {}
    for k in base:
        s_ = scored.get(k)
        if s_ is not None:
            matches[k] = s_.pj
    # Club-correlated season uncertainty (club_volatility()). `club` is
    # keyed on the market's spelling; results_history.csv is keyed on
    # ff_slug, translated through ffcore.crosswalk — norm(c.market), not
    # raw, to match club_key()'s own fallback convention.
    from ffcore.fixture import club_volatility, season_board
    from ffcore.tidy import load_elo, load_results_history, \
        load_understat_players
    slug_of = {norm(c.market): c.ff_slug for c in lg.xw.clubs.values()
              if c.market and c.ff_slug} if lg.xw is not None else {}
    club_of_slug = {k: slug_of[v] for k, v in club.items() if v in slug_of}
    # One read of results_history.csv — club_volatility() and season_board()
    # both want it and it can't have changed between them.
    results_hist = load_results_history()
    club_rel = club_volatility(results_hist, list(slug_of.values()))
    # The whole remaining schedule, fitted once for `rem`. Keys normalised
    # to match `club`'s own convention (club_key() always returns norm(...))
    # — season_board() itself is keyed on the market's raw spelling.
    sboard = {j: {norm(team): m for team, m in layer.items()}
             for j, layer in season_board(
                 _m.market, m, rem, now, load_elo(), xw=lg.xw,
                 results=results_hist,
                 understat_rows=load_understat_players("2025")).items()}
    ppm_of = {k: s.ppm for k, s in scored.items() if s}
    per_j = apply_fixtures(
        next_then_rest(base, base_rest, rem, played, club),
        sboard, club, pos, ppm_of)
    # A squad short a position can't be simulated at all (see phantom_fill())
    # — patched once here so every downstream reader gets the same fix.
    squads, per_j = phantom_fill(squads, per_j, pos)
    fc = Bootstrap(per_j, pool=pool, matches=matches,
                  club_of=club_of_slug, club_rel=club_rel)

    # What everybody has already scored, off the league table — not the
    # gated reader: this is history, incomplete rather than wrong; the gate
    # belongs on the balance beside it (read_api_balances applies it).
    carried = {}
    for r in last_api_standings():
        if r.get("manager"):
            carried.setdefault(r["manager"], float(r.get("team_points") or 0))
    # Same cash estimator league.md and rival_cash already use — a second,
    # independent read of the raw balance once left the headline quoting a
    # stale figure from a feed everything else had refused.
    # Why: docs/notes/decide.md#load--misc-join-notes
    raw_cash = lg[me].cash.value or 0.0
    locked_cash = pending_sent(mkt)
    cash = raw_cash - locked_cash

    return Universe(
        state=LeagueState(squads, rem, me, carried), forecaster=fc, pos=pos,
        price=price, proceeds=proceeds, owner=owner, cash=cash, me=me,
        value=value, market_exp=market_exp, start=start, clause=clause,
        route=route,
        rival_cash=rival_cash,
        # The rate and the behaviour beside the level rival_cash already
        # holds — see days_to_afford().
        daily_bonus=lg.cfg.daily_bonus, tempo=rival_tempo(lg.txns),
        clause_until=clause_until, bids=bids,
        part_played=played, name=name, start_note=_calibrated()[0].note(),
        unjoined=list(unjoined_clubs) + list(lg.api_unjoined),
        locked_cash=locked_cash, received_offers=received_offers)


def _selftest() -> None:
    from ffcore.forecast import Bootstrap as B

    # -- phantom_fill(): a squad short a position gets ONE average-player
    # stand-in per missing slot, not frozen at zero for the rest of the
    # season -- 2026-09-01, Miguel: "the forecast for Albert is
    # absolutely unsustainable... that's not possible unless he never
    # again connects to the app" ------------------------------------
    ph_sq = {"m": {"d1": "DEF", "d2": "DEF", "x1": "MED", "x2": "MED",
                   "x3": "MED", "p1": "POR", "f1": "DEL"}}   # 2 DEF, short 1
    ph_pos = {"d1": "DEF", "d2": "DEF", "other_def": "DEF",
              "x1": "MED", "x2": "MED", "x3": "MED", "p1": "POR", "f1": "DEL"}
    ph_per = {1: {"d1": (4.0, 1.0), "d2": (2.0, 0.5),
                  "other_def": (6.0, 0.5), "x1": (3.0, 1.0)}}
    new_sq, new_per = phantom_fill(ph_sq, ph_per, ph_pos)
    phantom_keys = [k for k in new_sq["m"] if k.startswith("__phantom_")]
    assert len(phantom_keys) == 1, phantom_keys          # short exactly 1 DEF
    pk = phantom_keys[0]
    assert new_sq["m"][pk] == "DEF", new_sq["m"]
    # THE AVERAGE, NOT A GUESS: every REAL scored DEF's own real jornada-1
    # (points, p_start), averaged — (4.0+2.0+6.0)/3 pts,
    # (1.0+0.5+0.5)/3 p_start.
    assert new_per[1][pk] == (4.0, (1.0 + 0.5 + 0.5) / 3), new_per[1][pk]
    # RETURNS COPIES — a caller still holding the originals sees them
    # untouched, so "was he short before the patch" stays answerable.
    assert "__phantom_m_DEF_0" not in ph_sq["m"], ph_sq
    assert pk not in ph_per[1], ph_per[1]
    # A LEGAL SQUAD COMES BACK UNCHANGED — no phantom invented where
    # nothing is missing.
    legal_sq = {"m2": {"p1": "POR", "d1": "DEF", "d2": "DEF", "d3": "DEF",
                       "x1": "MED", "x2": "MED", "x3": "MED", "f1": "DEL"}}
    same_sq, _ = phantom_fill(legal_sq, ph_per, ph_pos)
    assert same_sq == legal_sq, same_sq

    sq = {"k": "POR", **{f"d{i}": "DEF" for i in range(1, 5)},
          **{f"m{i}": "MED" for i in range(1, 6)}, "f1": "DEL", "bench": "MED"}
    mine = {f"me_{k}": v for k, v in sq.items()}
    theirs = {f"th_{k}": v for k, v in sq.items()}
    allk = list(mine) + list(theirs) + ["star", "dud"]
    per = {1: {k: (3.0, 1.0) for k in allk}}
    per[1]["star"] = (12.0, 1.0)
    per[1]["dud"] = (0.2, 1.0)
    per[1]["me_bench"] = (0.5, 1.0)
    per[1]["th_m1"] = (6.0, 1.0)      # worth taking off a rival

    u = Universe(
        state=LeagueState({"me": dict(mine), "riv": dict(theirs)}, [1], "me"),
        forecaster=B(per), pos={**{k: v for k, v in mine.items()},
                                **{k: v for k, v in theirs.items()},
                                "star": "MED", "dud": "MED"},
        price={"star": 10e6, "dud": 1e6, "th_m1": 5e6},
        route={"th_m1": "clause"},
        proceeds={"me_bench": 8e6}, owner={"th_m1": "riv"},
        cash=12e6, me="me")
    exp = u.forecaster.expected(1)

    # -- current_xi / xi_bar: the one computation seven call sites used to
    # each rebuild by hand ---------------------------------------------
    cxi_exp, cxi = current_xi(u)
    assert cxi_exp == u.forecaster.expected(choosable(u)), cxi_exp
    # me_bench (rate 0.5) is the weakest of the 12 — never picked over the
    # other 11 real starters, so it must not be in the eleven.
    assert "me_bench" not in cxi, cxi
    assert len(cxi) == 11, cxi
    # A different manager: a DIFFERENT eleven, same exp dict — the whole
    # reason exp is not recomputed per manager.
    riv_exp, riv_xi = current_xi(u, who="riv")
    assert riv_exp is cxi_exp or riv_exp == cxi_exp, (riv_exp, cxi_exp)
    assert riv_xi != cxi, (riv_xi, cxi)
    assert "th_bench" not in riv_xi, riv_xi
    # xi_bar: the weakest man IN the eleven, not the weakest man overall —
    # me_bench (0.5) is weaker than everyone in cxi, but it is not IN cxi,
    # so it must not set the bar.
    bar = xi_bar(cxi_exp, cxi)
    assert bar == min(cxi_exp.get(k, 0.0) for k in cxi), bar
    assert bar > 0.5, bar
    # No eleven at all: the bar is 0.0, not a crash.
    assert xi_bar(cxi_exp, set()) == 0.0

    acts = candidates(u, exp)
    names = {a.buy for a in acts}
    # A player worse than the weakest man you field is not a candidate.
    assert "dud" not in names, names
    assert "star" in names, names
    # A rival's player reachable ONLY through his clause is marked a raid;
    # one he has LISTED himself is an ordinary purchase, whoever owns him,
    # because taking him denies nobody anything they were not already selling.
    u.route["th_m1"] = "clause"
    acts = candidates(u, exp)
    assert any(a.kind.startswith("clause") and a.buy == "th_m1"
               for a in acts), [a.kind for a in acts]
    u.route["th_m1"] = "listed"
    listed = candidates(u, exp)
    got = [a for a in listed if a.buy == "th_m1"]
    assert got and all(a.kind.startswith("buy") or a.kind == "swap"
                       for a in got), [a.kind for a in got]
    assert all(not a.victim for a in got), got
    u.route["th_m1"] = "clause"
    acts = candidates(u, exp)
    assert all(a.cost <= u.cash + a.proceeds for a in acts), acts

    # apply() is pure and a steal takes him OFF the rival.
    a = next(x for x in acts if x.buy == "th_m1" and not x.sell)
    after = apply(u, a)
    assert "th_m1" not in after["riv"], after["riv"]
    assert "th_m1" in after["me"]
    assert "th_m1" in u.state.squads["riv"], "apply must not mutate"

    # -- market_routes: a free agent is not a rival's listed player --------
    # api_market.csv's own `seller` column already says which is which
    # (marketPlayerLeague = the app dealing a free agent, marketPlayerTeam =
    # a manager listing one of theirs) — this repo has known that since
    # slate.py, but decide.py labelled every row "market" regardless,
    # collapsing "nobody can refuse this" into the same bucket as "an owner
    # who might not sell, and other managers may already be bidding."
    mkt_rows = [
        {"player_name": "Free Agent", "sale_price": "5000000",
         "seller": "marketPlayerLeague", "bids": "0"},
        {"player_name": "Listed Rival", "sale_price": "8000000",
         "seller": "marketPlayerTeam", "bids": "2"},
        # No sale_price at all: not on offer, not priced, not routed.
        {"player_name": "Not Priced", "sale_price": "",
         "seller": "marketPlayerLeague"},
        # Resolves to no key: silently skipped, not a crash.
        {"player_name": "Unjoinable", "sale_price": "1000000",
         "seller": "marketPlayerTeam"},
    ]
    key_of = {"Free Agent": "free_agent", "Listed Rival": "listed_rival",
             "Not Priced": "not_priced"}.get
    price, route, bids = market_routes(
        mkt_rows, lambda r: key_of((r.get("player_name") or "")))
    assert price == {"free_agent": 5000000.0, "listed_rival": 8000000.0}, price
    assert route == {"free_agent": "free", "listed_rival": "listed"}, route
    assert bids == {"free_agent": 0, "listed_rival": 2}, bids
    assert "not_priced" not in route and "not_priced" not in price
    # An unrecognised seller value defaults to "free" — the app dealing it
    # is the ordinary case, and a new discriminator value should not
    # silently start reading every row as a contested rival listing.
    unknown_seller = [{"player_name": "Free Agent", "sale_price": "1",
                       "seller": "something_new"}]
    _, r2, _ = market_routes(unknown_seller, lambda r: "free_agent")
    assert r2 == {"free_agent": "free"}, r2

    # -- pending_sent: a bid of yours is money already gone -----------------
    mkt_bids = [
        {"bid_status": "pending", "bid_money": "5600000"},
        {"bid_status": "pending", "bid_money": "6795815"},
        {"bid_status": "", "bid_money": ""},                # no bid here
        {"bid_status": "accepted", "bid_money": "2000000"}, # settled, not held
        {"bid_status": "pending", "bid_money": ""},         # unreachable shape
    ]
    assert pending_sent(mkt_bids) == 5600000.0 + 6795815.0, pending_sent(mkt_bids)
    assert pending_sent([]) == 0.0

    # -- pending_received: a real offer beats a guess, and only as a floor --
    p2k = {"pt1": "me_a", "pt2": "me_b"}
    offers = [
        {"player_team_id": "pt1", "status": "pending", "money": "6795815"},
        # A second, smaller pending offer on the SAME player: the larger
        # one is what he could actually raise, not the first one seen.
        {"player_team_id": "pt1", "status": "pending", "money": "1000000"},
        {"player_team_id": "pt2", "status": "accepted", "money": "9000000"},
        # No offer at all — the placeholder row parse_api_offer emits so the
        # table stays stamped. Not pending, so it prices nothing.
        {"player_team_id": "pt2", "status": "", "money": ""},
        # A playerTeamId nothing in the squad joins to (sold since, or a
        # rival's — should never happen, given offer_sources() only ever
        # asks for your own, but a join failing silently beats a KeyError).
        {"player_team_id": "unknown", "status": "pending", "money": "1"},
    ]
    got = pending_received(offers, p2k)
    assert got == {"me_a": 6795815.0}, got     # pt2's only offer was accepted
    assert pending_received([], p2k) == {}
    assert pending_received(offers, {}) == {}   # nothing to join to

    # -- offer_combos: minimal covers of a negative balance ------------------
    uoc = Universe(state=LeagueState({"me": {"a": "MED", "b": "MED",
                                             "c": "MED", "d": "MED"}},
                                     jornadas=[1], me="me", carried={}),
                  forecaster=None, pos={}, price={}, proceeds={}, owner={},
                  cash=-10_000_000.0, me="me",
                  received_offers={"a": 4_000_000.0, "b": 4_000_000.0,
                                   "c": 9_000_000.0, "d": 3_000_000.0})
    got = {k: a.sell for k, a in offer_combos(uoc)}
    # No single man clears 10M alone (c, the biggest, is 9M). Every pair
    # with c does (a+c=13M, b+c=13M, c+d=12M); a+b (8M) and a/b+d (7M)
    # do not, so the one triple that does — a+b+d=11M — is ALSO minimal:
    # no two of {a,b,d} covers on their own, so it is not a superset of
    # any cover already found. a+b+c, a+c+d, b+c+d are real covers too,
    # but each is a superset of a pair already found — dropped, not a
    # false choice.
    assert set(got.values()) == {("a", "c"), ("b", "c"), ("c", "d"),
                                 ("a", "b", "d")}, got
    # A held player with an offer, funding NOTHING else — apply() already
    # proves a pure sell is legal; this proves the Action built here is one.
    for a in dict(offer_combos(uoc)).values():
        assert a.buy == "" and a.kind == "sell"
    assert sum(a.proceeds for a in dict(offer_combos(uoc)).values()
              if a.sell == ("c", "d")) == 12_000_000.0
    # A non-negative balance has nothing to cover, offers or not.
    upos = replace(uoc, cash=0.0)
    assert offer_combos(upos) == []
    # A real deficit but no real offers — nothing to accept, only to sell.
    uno = replace(uoc, received_offers={})
    assert offer_combos(uno) == []
    # An offer on a player who left the squad since (sold, or a stale
    # join) prices nothing — only a HELD man's offer counts.
    ugone = replace(uoc, received_offers={**uoc.received_offers,
                                          "gone": 50_000_000.0})
    assert "gone" not in {p for a in dict(offer_combos(ugone)).values()
                          for p in a.sell}

    # -- funding a move with MORE THAN ONE sale ----------------------------
    # The table silently omitted every move that needed two. A target you
    # cannot reach on cash plus one spare is not unaffordable — it is
    # affordable by selling the men who never play, and those cost nothing on
    # the pitch by construction. This is the staircase in the value of cash,
    # expressed as rows rather than as a second table about money: on the day
    # it was written, three dead-weight sales raised 21.16M, which cleared
    # Giuliano Simeone's 44.65M clause by 109K.
    u3 = Universe(
        state=LeagueState({"me": dict(mine), "riv": dict(theirs)}, [1], "me"),
        forecaster=B(per), pos={**u.pos, "dear": "MED"},
        price={"dear": 20e6},
        # Three spares, none of whom ever start: the sixth midfielder, the
        # seventh, and a second keeper.
        proceeds={"me_bench": 8e6, "me_spare2": 5e6, "me_spare3": 4e6},
        owner={}, cash=4e6, me="me")
    u3.state.squads["me"]["me_spare2"] = "MED"
    u3.state.squads["me"]["me_spare3"] = "POR"
    per3 = {1: dict(per[1])}
    per3[1].update({"dear": (11.0, 1.0), "me_spare2": (0.4, 1.0),
                    "me_spare3": (0.3, 1.0)})
    u3.forecaster = B(per3)
    acts3 = candidates(u3, u3.forecaster.expected(1))
    multi = [a for a in acts3 if a.buy == "dear" and len(a.sell) > 1]
    assert multi, "a move needing two sales must still be on the table"
    # ...AND STILL ON IT WHEN THE BUDGET IS LIFTED to measure the frontier. A
    # budget that also decided whether a target needs more than one sale made
    # every target look reachable on cash alone, and the multi-sale moves —
    # including the best one on the board — stopped being generated.
    wide = candidates(u3, u3.forecaster.expected(1), budget=float("inf"))
    assert any(a.buy == "dear" and len(a.sell) > 1 for a in wide), wide
    a3 = min(multi, key=lambda a: len(a.sell))
    # Sell the FEWEST men that cover it: 4 + 8 + 5 = 17 is short of 20, so all
    # three go; the greedy takes the biggest first so it never sells four to
    # do the job of three.
    assert set(a3.sell) == {"me_bench", "me_spare2", "me_spare3"}, a3.sell
    assert a3.proceeds == 17e6 and a3.cost == 20e6
    assert a3.net == 3e6
    # Never a man who plays: the eleven is the point of the exercise.
    assert not any(k.startswith("me_d") or k.startswith("me_m")
                   for k in a3.sell if k != "me_bench")
    # apply() drops every one of them.
    after3 = apply(u3, a3)
    assert not (set(a3.sell) & set(after3["me"])), after3["me"]
    assert "dear" in after3["me"]
    # ...and it reads as one sentence, not three rows.
    assert a3.label() == "buy dear · sell me_bench + me_spare2 + me_spare3", \
        a3.label()

    # A swap removes the sold man and adds the bought one.
    sw = next(x for x in acts if x.buy == "star" and x.sell == ("me_bench",))
    af = apply(u, sw)
    assert "me_bench" not in af["me"] and "star" in af["me"]

    rows, base, _lam, _b = rank(u, acts)
    assert rows, "something should be worth doing"
    top = rows[0]
    # The paired pair: how often it helps, and by how much, in the same
    # seasons. A move that adds a twelve-point player to an eleven of threes
    # helps in nearly all of them.
    assert 0.5 < top["helps"] <= 1.0, top["helps"]
    assert top["d_pts"] > 0, top["d_pts"]
    assert top["pts_lo"] <= top["d_pts"] <= top["pts_hi"]
    # Signing a 12-point player into an eleven of 3s must improve your
    # position, and the table must be sorted by that.
    assert top["d_pos"] > 0, top
    assert [r["d_pos"] for r in rows] == sorted(
        (r["d_pos"] for r in rows), reverse=True)
    assert set(top["d_beat"]) == {"riv"}

    # VALUE FOR MONEY: points per million ACTUALLY PAID, only for a genuine
    # spend (net > 0) — the formula itself, checked against the row it came
    # from, not just "it exists".
    spend = next(r for r in rows if r["action"].net > 0)
    assert abs(spend["value"] - spend["d_pts"] / (spend["action"].net / 1e6)
              ) < 1e-9, spend
    # A pure sale (net <= 0) gets no ratio — see rank()'s own note on why
    # dividing by a non-positive net would blow up or read backwards.
    sale = next((r for r in rows if r["action"].net <= 0), None)
    if sale is not None:
        assert sale["value"] is None, sale

    # -- `value` IS ALREADY POINTS OVER POSITION REPLACEMENT LEVEL ---------
    # The standing proposal this pins down (raised again 2026-08-31): add a
    # second `value_vor` = points over the position's replacement level,
    # per euro, because `value` supposedly compares a candidate in
    # isolation and cannot tell that a cheap defender is scarce and a cheap
    # forward is not. It can. `d_pts` is a paired marginal off a re-picked
    # best_xi(), so the replacement is computed per candidate rather than
    # assumed per slot. Two candidates on IDENTICAL expected points and an
    # IDENTICAL price, one into a thin slot and one into a deep one, must
    # therefore NOT come out equal — which also means the fixture that
    # proposal wants ("same `value`, different scarcity") cannot be built:
    # equal `value` here IS the simulation saying they are worth the same.
    vsq = {"me_k": "POR",
           **{"me_d%d" % i: "DEF" for i in range(1, 6)},
           **{"me_m%d" % i: "MED" for i in range(1, 7)},
           "me_f1": "DEL"}
    vth = {"th_" + k[3:]: v for k, v in vsq.items()}
    vrate = {k: (5.0 if v == "MED" else 3.0) for k, v in vsq.items()}
    vrate["me_f1"] = 1.0                 # the only forward, and a weak one
    vrate.update({k: 3.0 for k in vth})
    vper = {1: {k: (r, 1.0) for k, r in vrate.items()}}
    vper[1]["thin_del"] = (8.0, 1.0)     # 8.0 into a slot replacing 1.0
    vper[1]["deep_med"] = (8.0, 1.0)     # 8.0 into a slot replacing 5.0
    uvor = Universe(
        state=LeagueState({"me": dict(vsq), "riv": dict(vth)}, [1], "me"),
        forecaster=B(vper),
        pos={**vsq, **vth, "thin_del": "DEL", "deep_med": "MED"},
        price={"thin_del": 5e6, "deep_med": 5e6},
        route={"thin_del": "free", "deep_med": "free"},
        proceeds={}, owner={}, cash=6e6, me="me")
    vexp, vxi = current_xi(uvor)
    # The fixture is what it claims: ONE flat bar, set by the weak forward,
    # while the two slots' own replacement levels are 1.0 and 5.0 — the
    # position-specific spread VORP exists to notice.
    assert xi_bar(vexp, vxi) == 1.0, xi_bar(vexp, vxi)
    assert min(vexp[k] for k in vxi if uvor.pos[k] == "DEL") == 1.0, vxi
    assert min(vexp[k] for k in vxi if uvor.pos[k] == "MED") == 5.0, vxi
    vrows, _vb, _vl, _vbd = rank(
        uvor, [Action("buy", buy="thin_del", cost=5e6),
               Action("buy", buy="deep_med", cost=5e6)])
    vby = {r["action"].buy: r for r in vrows}
    assert vby["thin_del"]["action"].net == vby["deep_med"]["action"].net
    # Same points, same price, and the thin slot is worth MULTIPLES of the
    # deep one. Measured 3.2x; asserted at 2x so the numpy and numpy-less
    # RNG paths both hold it, the margin 24d2a8b's own value fixture needed.
    assert vby["thin_del"]["d_pts"] > 2 * vby["deep_med"]["d_pts"] > 0, vby
    assert vby["thin_del"]["value"] > 2 * vby["deep_med"]["value"] > 0, vby

    # ...and a PER-POSITION bar would be UNSOUND as a screen, which is why
    # xi_bar() stays flat — see its own note. 1-5-4-1 with a weak fifth
    # defender: a midfielder at 2.0 sits below MED's own replacement level
    # of 4.0, so a position-specific screen drops him, and he starts
    # anyway by reshaping to 1-4-5-1. Pure best_xi(), no Monte Carlo, so
    # this one is exact under both runtimes.
    bsq = {"me_k": "POR", "me_d1": "DEF", "me_d2": "DEF", "me_d3": "DEF",
           "me_d4": "DEF", "me_d5": "DEF", "me_m1": "MED", "me_m2": "MED",
           "me_m3": "MED", "me_m4": "MED", "me_f1": "DEL"}
    bexp = {"me_k": 3.0, "me_d1": 3.0, "me_d2": 3.0, "me_d3": 3.0,
            "me_d4": 3.0, "me_d5": 1.0, "me_m1": 4.0, "me_m2": 4.0,
            "me_m3": 4.0, "me_m4": 4.0, "me_f1": 3.0, "cand": 2.0}
    bxi = set(best_xi(bsq, bexp))
    assert bxi == set(bsq), bxi                    # eleven men, all field
    assert xi_bar(bexp, bxi) == 1.0                # the weak fifth defender
    assert min(bexp[k] for k in bxi if bsq[k] == "MED") == 4.0
    bsq2 = {**bsq, "cand": "MED"}
    bxi2 = set(best_xi(bsq2, bexp))
    # Clears the flat bar (2.0 > 1.0), fails his own slot's (2.0 < 4.0),
    # and plays — the fifth defender is the man who comes out for him.
    assert "cand" in bxi2 and "me_d5" not in bxi2, bxi2
    assert sum(1 for k in bxi2 if bsq2[k] == "DEF") == 4, bxi2
    assert sum(bexp[k] for k in bxi2) - sum(bexp[k] for k in bxi) == 1.0

    # THE STEAL IS WORTH MORE THAN THE SAME PLAYER FROM THE POOL. Compared
    # like for like — same points, same price, same funding — taking him off a
    # rival beats buying an equivalent free agent, because it moves both
    # totals. This is the property no per-player rate can represent.
    per2 = {1: dict(per[1])}
    per2[1]["free_x"] = (9.0, 1.0)
    per2[1]["th_m1"] = (9.0, 1.0)
    u2 = Universe(
        state=LeagueState({"me": dict(mine), "riv": dict(theirs)}, [1], "me"),
        forecaster=B(per2),
        pos={**u.pos, "free_x": "MED", "th_m1": "MED"},
        price={"free_x": 5e6, "th_m1": 5e6}, route={"th_m1": "clause"},
        proceeds={},
        owner={"th_m1": "riv"}, cash=6e6, me="me")
    got, _, _, _ = rank(u2, [Action("buy", buy="free_x", cost=5e6),
                          Action("clause", buy="th_m1", cost=5e6,
                                 victim="riv")])
    by = {r["action"].buy: r["d_pos"] for r in got}
    assert by["th_m1"] > by["free_x"], by

    # -- _top_up: the shared "ensure at least N satisfy `ok`, on top, never
    # displacing" mechanic, tested on its own before any caller wires it in
    # ------------------------------------------------------------------
    top_a = [(9.0, Action("buy", buy="a", cost=1e6)),
             (8.0, Action("buy", buy="b", cost=1e6))]
    screened_a = top_a + [(7.0, Action("buy", buy="c", cost=1e6)),
                          (1.0, Action("buy", buy="ok1", cost=1e6)),
                          (0.5, Action("buy", buy="ok2", cost=1e6)),
                          (0.1, Action("buy", buy="bad", cost=1e6))]
    ok = lambda d, a: a.buy in ("ok1", "ok2", "bad")             # noqa: E731
    topped = _top_up(top_a, screened_a, ok,
                     rank_key=lambda t: -t[0], minimum=2)
    keys = [a.buy for _, a in topped]
    # both already-kept rows survive untouched, in place, and exactly the
    # two best `ok` rows (by rank_key, not screening order) get added ON
    # TOP — "bad" (also `ok`) is left out once the minimum is met.
    assert keys == ["a", "b", "ok1", "ok2"], keys
    # already at the minimum: no-op, returns `top` as-is (same objects,
    # nothing appended even if `screened` has other `ok` rows available).
    already_enough = _top_up(top_a, screened_a, lambda d, a: True,
                             rank_key=lambda t: -t[0], minimum=2)
    assert already_enough == top_a, already_enough
    # a row already in `top` is never duplicated by the top-up even if it
    # also satisfies `ok`.
    dup_check = _top_up([(9.0, Action("buy", buy="a", cost=1e6))],
                        [(9.0, Action("buy", buy="a", cost=1e6)),
                         (5.0, Action("buy", buy="b", cost=1e6))],
                        lambda d, a: True, rank_key=lambda t: -t[0],
                        minimum=2)
    assert [a.buy for _, a in dup_check] == ["a", "b"], dup_check
    # `ok` sees the screened gain `d`, not just the action — needed by a
    # caller like KEEP_VALUE_MIN's "genuine gain" check, which the action
    # alone cannot answer (gain lives in `d`, not on the Action).
    gain_aware = _top_up([], screened_a, lambda d, a: d > 0.5,
                         rank_key=lambda t: -t[0], minimum=10)
    assert [a.buy for _, a in gain_aware] == ["a", "b", "c", "ok1"], \
        gain_aware

    # -- KEEP_RELIABLE_MIN: a wall of "listed" candidates that screen well
    # does not crowd a smaller but reliable one out of the full-precision
    # pass ------------------------------------------------------------
    per5 = {1: dict(per[1])}
    acts5 = []
    for i in range(15):     # all bigger than any reliable candidate below
        key = "listed%d" % i
        per5[1][key] = (10.0 - i * 0.1, 1.0)
        acts5.append(Action("buy", buy=key, cost=1e6))
    for i in range(3):      # smaller than every listed one — no raw top-12
        key = "reliable%d" % i
        per5[1][key] = (2.0, 1.0)
        acts5.append(Action("buy", buy=key, cost=1e6))
    route5 = {"listed%d" % i: "listed" for i in range(15)}
    route5.update({"reliable%d" % i: "free" for i in range(3)})
    u5 = Universe(
        state=LeagueState({"me": dict(mine), "riv": dict(theirs)}, [1], "me"),
        forecaster=B(per5),
        pos={**u.pos, **{a.buy: "MED" for a in acts5}},
        price={a.buy: 1e6 for a in acts5}, route=route5,
        proceeds={}, owner={}, cash=100e6, me="me")
    rows5, *_ = rank(u5, acts5)
    kept5 = {r["action"].buy for r in rows5}
    # All 15 "listed" candidates screen ahead of all 3 reliable ones, so the
    # natural top-KEEP=12 is entirely "listed" — the 3 reliable ones only
    # get in because KEEP_RELIABLE_MIN tops the pass up, ON TOP of the 12,
    # not instead of any of them: 18 candidates in, 15 kept (12 + 3), none
    # of the top-12 listed ones dropped to make room.
    assert all(("reliable%d" % i) in kept5 for i in range(3)), kept5
    assert len(kept5) == 15, kept5
    assert sum(1 for k in kept5 if k.startswith("listed")) == 12, kept5

    # -- KEEP_VALUE_MIN: a wall of expensive-but-big candidates does not
    # crowd cheap-but-efficient ones out of the full-precision pass ------
    per6 = {1: dict(per[1])}
    acts6 = []
    for i in range(15):     # big raw gain, but €20M each — poor ratio
        key = "big%d" % i
        per6[1][key] = (10.0 - i * 0.1, 1.0)
        acts6.append(Action("buy", buy=key, cost=20e6))
    for i in range(3):      # a clear, unambiguous gain, but €10k — excellent
        key = "eff%d" % i   # ratio. Rate 6.0 (not e.g. 3.2, barely above the
        per6[1][key] = (6.0, 1.0)   # ~3.0 bar): too thin a margin made this
        acts6.append(Action("buy", buy=key, cost=1e4))  # flaky across
        # backends — screening's Monte Carlo noise put one candidate's `d`
        # on either side of zero depending on which RNG ran it (numpy vs.
        # the pure-Python fallback used when numpy is absent), found by
        # running under this repo's real `uv run --frozen python` (which
        # has numpy) after `python3` alone (which does not) had passed
        # every time — same seed, different backend, different answer on a
        # genuinely borderline margin. A wide, unambiguous gain removes the
        # ambiguity instead of chasing a specific backend's numbers.
    # a candidate below the current XI bar: no genuine gain, so however
    # tiny its cost, it must never be topped up on "ratio" alone.
    per6[1]["sham"] = (0.1, 1.0)
    acts6.append(Action("buy", buy="sham", cost=1e3))
    u6 = Universe(
        state=LeagueState({"me": dict(mine), "riv": dict(theirs)}, [1], "me"),
        forecaster=B(per6),
        pos={**u.pos, **{a.buy: "MED" for a in acts6}},
        price={a.buy: a.cost for a in acts6}, route={},
        proceeds={}, owner={}, cash=1000e6, me="me")
    rows6, *_ = rank(u6, acts6)
    kept6 = {r["action"].buy for r in rows6}
    # natural top-KEEP=12 is entirely "big" (raw gain 10.0..8.7 all beat
    # 3.2), the 3 "eff" candidates only get in via KEEP_VALUE_MIN's top-up,
    # on top of the 12 — none of the top-12 "big" ones displaced.
    assert all(("eff%d" % i) in kept6 for i in range(3)), kept6
    assert sum(1 for k in kept6 if k.startswith("big")) == 12, kept6
    assert "sham" not in kept6, kept6

    # -- what a clause burns, and what that is worth in places -------------
    # A free agent asks about what he is worth, so buying one destroys
    # nothing: you hold an asset you could sell back for the money. A buyout
    # clause runs a median 1.52x market value in this league, and that premium
    # never comes back — you pay 1.52V for something the app will pay you V
    # for. The simulation counts the cash leaving and cannot see the wealth
    # going, because it scores money at zero.
    u4 = Universe(
        state=LeagueState({"me": dict(mine), "riv": dict(theirs)}, [1], "me"),
        forecaster=B(per), pos=dict(u.pos), price={"th_m1": 8e6},
        value={"th_m1": 5e6}, proceeds={}, owner={"th_m1": "riv"},
        cash=20e6, me="me")
    assert burn(u4, Action("steal", buy="th_m1", cost=8e6)) == 3e6
    # A free agent at his market value burns nothing...
    u4.value["free"] = 4e6
    assert burn(u4, Action("buy", buy="free", cost=4e6)) == 0.0
    # ...and a bargain is not a negative cost, it is zero: the app does not
    # pay you for buying well, it just does not punish you.
    assert burn(u4, Action("buy", buy="free", cost=3e6)) == 0.0
    # A sale burns nothing either — the app pays market value.
    assert burn(u4, Action("sell", sell="me_bench")) == 0.0
    # A player nothing knows the value of cannot be priced, and an unknown is
    # not a zero: assuming no premium is exactly the error being fixed.
    assert burn(u4, Action("steal", buy="mystery", cost=9e6)) is None

    # THE PRICE OF CASH, measured rather than chosen. Screen every target,
    # affordable or not, and the frontier of "best Delta pos reachable for
    # this much extra" gives places per euro. Flat here: the cheap option is
    # already the best one, so more money buys nothing.
    flat = [(0.0, 0.40), (5e6, 0.30), (12e6, 0.20)]
    assert cash_price(flat) == 0.0
    # A step: 10M more reaches +0.10 that nothing cheaper does.
    step = [(0.0, 0.40), (10e6, 0.50)]
    assert abs(cash_price(step) - 0.10 / 10.0) < 1e-12
    # Nothing to measure is None, never a zero that would silently mean free.
    assert cash_price([]) is None and cash_price([(0.0, 0.4)]) is None

    # -- the rival answers back --------------------------------------------
    # A CLAUSE PAYS THE OWNER. That is not a detail: it means a steal hands
    # the manager you are racing the money to retaliate with, and on the day
    # this was written every rival was overdrawn and could not buy anybody at
    # all until I paid one. The simulation scored the retaliation at zero,
    # which made a steal look like pure subtraction from a rival who had no
    # way to respond, when it is closer to an exchange on terms he chooses.
    riv_squad = {f"th_{k}": v for k, v in sq.items()}
    # One of mine is worth having, so his answer is worth making. Without a
    # player who would actually improve his eleven there is no response to
    # test — only a budget he has no use for.
    per5 = {1: dict(per[1])}
    per5[1]["me_m1"] = (7.0, 1.0)
    u5 = Universe(
        state=LeagueState({"me": dict(mine), "riv": dict(riv_squad)}, [1],
                          "me"),
        forecaster=B(per5), pos={**u.pos, "star": "MED"},
        price={"th_m1": 10e6}, value={"th_m1": 6e6, "me_m1": 4e6},
        clause={"me_m1": 6e6, "th_m1": 10e6},
        # Payable: a clause locked by a recent transfer is no answer at all,
        # and an absent date counts as locked.
        clause_until={"me_m1": dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc),
                      "th_m1": dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc)},
        proceeds={}, owner={"th_m1": "riv"}, cash=12e6, me="me",
        rival_cash={"riv": 0.0})
    steal = Action("steal", buy="th_m1", cost=10e6, victim="riv")
    # He was broke; the clause leaves him holding 10M, which reaches a man of
    # mine priced at 6M — so his best answer is to take one straight back.
    ans = respond(u5, steal, apply(u5, steal))
    assert ans is not None, "he can afford an answer and should give one"
    assert ans.buy == "me_m1" and ans.victim == "me", ans
    # He does not simply buy back the man just taken. Left available, that is
    # nearly always his best answer — but the transfer resets a clause and
    # nothing here has ever observed one to know at what.
    assert ans.buy != "th_m1", ans
    # AND HE CANNOT ANSWER WITH A LOCKED CLAUSE EITHER. The rule binds both
    # ways round, or the response would be free to make moves the app refuses
    # exactly as the ranking used to.
    u5.clause_until = {}
    assert respond(u5, steal, apply(u5, steal)) is None
    # A move that hands him nothing leaves him where he was: broke.
    assert respond(u5, Action("buy", buy="star", cost=1e6),
                   u5.state.squads) is None
    # ...and so does a steal he cannot do anything with.
    poor = Universe(
        state=LeagueState({"me": dict(mine), "riv": dict(riv_squad)}, [1],
                          "me"),
        forecaster=B(per5), pos=dict(u5.pos), price={"th_m1": 1e6},
        value={"th_m1": 1e6}, clause={"me_m1": 90e6}, proceeds={},
        clause_until={"me_m1": dt.datetime(2020, 1, 1,
                                           tzinfo=dt.timezone.utc)},
        owner={"th_m1": "riv"}, cash=12e6, me="me", rival_cash={"riv": 0.0})
    cheap = Action("steal", buy="th_m1", cost=1e6, victim="riv")
    assert respond(poor, cheap, apply(poor, cheap)) is None

    # -- the bar is a round you can still pick -----------------------------
    # THE ELEVEN A SIGNING HAS TO BEAT must be the one you would actually
    # field. Measured against a round already in progress it is not: the
    # players whose clubs have kicked off are out of it, so the eleven is
    # whatever is left, the weakest man in it can be a reserve scoring
    # nothing, and every journeyman in the league clears the bar. On the day
    # this was found the bar off jornada 1 was 0.00 and off jornada 2 was
    # 2.73, and the candidate list was inflated by everyone in between.
    half = Universe(
        state=LeagueState({"me": dict(mine), "riv": dict(theirs)}, [1, 2],
                          "me", ),
        forecaster=B({1: {"me_k": (0.1, 1.0), "dud": (1.0, 1.0)},
                      2: {**{k: (5.0, 1.0) for k in mine}, "dud": (1.0, 1.0)}}),
        pos={**u.pos, "dud": "MED"}, price={"dud": 1e6}, proceeds={},
        owner={}, cash=50e6, me="me")
    half.part_played = {1: {"somewhere"}}
    # Off the locked round the bar is 0.1 and the journeyman clears it; off a
    # round you can still pick it is 5.0 and he does not.
    assert not any(a.buy == "dud"
                   for a in candidates(half, half.forecaster.expected(2)))

    # -- a clause you cannot pay is not a price ----------------------------
    # A transfer LOCKS the clause for about a week, and on the day this was
    # found every one of the 76 rival players in the league was locked. The
    # whole steal side of the report was ranking moves the app would refuse —
    # which is exactly what it looked like from the outside, and why it was
    # queried. A lock is not a discount and not a reason to rank him lower: he
    # is simply not for sale, and the honest table says when he will be.
    now = dt.datetime(2026, 8, 18, tzinfo=dt.timezone.utc)
    soon = dt.datetime(2026, 8, 24, tzinfo=dt.timezone.utc)
    past = dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc)
    assert locked({"th_m1": soon}, "th_m1", now) is True
    assert locked({"th_m1": past}, "th_m1", now) is False
    # No date is NOT STATED, and not-stated must not become "buyable": the
    # feed omitting a field is the case that silently reopens the bug.
    assert locked({}, "th_m1", now) is True
    assert locked({"th_m1": None}, "th_m1", now) is True

    assert Action("clause", buy="X", victim="R").label() == "clause X from R"
    assert Action("swap", buy="X", sell="Y").label() == "buy X · sell Y"
    # A bare string is still accepted, because one man is the common case.
    assert Action("swap", buy="X", sell="Y").sell == ("Y",)
    assert Action("buy", buy="X").sell == ()
    # The grammar of a move is written once. A report that spelled it out
    # again to swap the keys for names would be a second place for "steal"
    # and "sell" to drift apart from each other.
    assert Action("clause", buy="x", victim="R").label({"x": "Xavi"}) \
        == "clause Xavi from R"
    assert Action("sell", sell="y").label({"y": "Yuri"}) == "sell Yuri"
    assert Action("swap", buy="x", sell="y").label({"x": "Xavi"}) \
        == "buy Xavi · sell y"

    # -- a round already half played ---------------------------------------
    # THE CASE THAT ACTUALLY EXISTS IN AUGUST, and the one that pays twice. A
    # jornada is not finished, so it is still ahead — but four of its ten
    # matches have been played, the app has already banked those points into
    # the carried total, and simulating them again credits them a second time.
    teams = ["Alavés", "Getafe", "Celta Vigo", "Osasuna", "Rayo"]
    ms = [{"jornada": "1", "home": "alaves", "away": "getafe", "score": "3-0"},
          {"jornada": "1", "home": "celta", "away": "osasuna", "score": ""},
          {"jornada": "2", "home": "alaves", "away": "celta", "score": ""},
          {"jornada": "3", "home": "alaves", "away": "getafe", "score": "1-1"},
          {"jornada": "", "home": "alaves", "away": "celta", "score": ""}]
    rem, done, unjoined = rounds_left(ms, teams)
    # J1 is still ahead (six matches to come); J3 is finished and is not.
    assert rem == [1, 2], rem
    # ...and within J1, these two clubs have nothing left to give.
    assert done == {1: {"alaves", "getafe"}}, done
    assert unjoined == [], unjoined

    # ONE CLUB, TWO SPELLINGS, and this is not hypothetical: the market calls
    # them "Rayo", the fixture page "rayo-vallecano", and the probable-XI page
    # files twenty-eight players under one and one player under the other. The
    # two sides of this join have to land on the same key or the round-in-
    # progress exclusion silently covers one player and misses the rest, which
    # is the double count back under a different name. Both sides go through
    # the MARKET's list of clubs, which is the one canonical spelling there is.
    assert club_key("rayo-vallecano", teams) == "rayo"

    # THE CROSSWALK ANSWERS FIRST when there is one, because it resolved this
    # once with every feed in front of it instead of guessing per call.
    class _XW:
        def club(self, **kw):
            return "rayo" if "vallecano" in str(kw.values()).lower() else None

    assert club_key("Rayo Vallecano", [], xw=_XW()) == "rayo"
    # ...and the fallback still works where the table has nothing.
    assert club_key("celta", teams, xw=_XW()) == "celta vigo"
    assert club_key("Rayo", teams) == "rayo"
    assert club_key("celta", teams) == "celta vigo"
    # No club, or one nothing can place, is not "some club" — it is nothing,
    # and nothing is never equal to a club that has played.
    assert club_key("zzz-united", teams) == ""
    assert club_key("", teams) == ""

    # A club the fixture page spells in a way the market does not is NOT
    # silently treated as unplayed — that is the double count coming back
    # under a name nobody prints. It comes back to be reported.
    _r, _d, un = rounds_left(
        [{"jornada": "1", "home": "zzz-united", "away": "getafe",
          "score": "1-0"},
         {"jornada": "1", "home": "celta", "away": "osasuna", "score": ""}],
        teams)
    assert un == ["zzz-united"], un
    assert _d == {1: {"getafe"}}, _d

    # -- next_then_rest: this week's status answers for ONE jornada ---------
    # A suspended man's club has NOT played J1 (he is in J1's dict, base
    # applies), and a normal man's has (dropped from J1 via `played`, so his
    # first appearance — base — is J2).
    rem2, played2 = [1, 2, 3], {1: {"alaves"}}
    base2 = {"susp": (5.0, 0.05), "normal": (4.0, 0.9)}
    rest2 = {"susp": (5.0, 0.9), "normal": (4.0, 0.9)}
    club2 = {"susp": "getafe", "normal": "alaves"}
    pj = next_then_rest(base2, rest2, rem2, played2, club2)
    assert pj[1] == {"susp": base2["susp"]}, pj[1]
    assert "normal" not in pj[1], pj[1]
    assert pj[2] == {"susp": rest2["susp"], "normal": base2["normal"]}, pj[2]
    assert pj[3] == {"susp": rest2["susp"], "normal": rest2["normal"]}, pj[3]

    # No played-club filtering at all (an ordinary full jornada): first
    # remaining jornada gets `base` for everyone, every later one `rest`.
    pj2 = next_then_rest(base2, rest2, [1, 2], {}, {})
    assert pj2[1] == base2 and pj2[2] == rest2, pj2

    # -- apply_fixtures: the REAL opponent, per jornada, not the frozen one -
    from ffcore.fixture import Match as _M

    easy_m = _M(opponent="Easy", home=True, kickoff=None,
               atk_factor=1.2, def_factor=1.1, rank=3, of=3)
    hard_m = _M(opponent="Hard", home=False, kickoff=None,
               atk_factor=0.8, def_factor=0.7, rank=1, of=3)
    pj3 = {1: {"del": (10.0, 0.9), "por": (5.0, 0.9), "ghost": (3.0, 0.5)},
          2: {"del": (10.0, 0.9), "por": (5.0, 0.9)}}
    board = {1: {"myclub": easy_m}, 2: {"myclub": hard_m}}
    club3 = {"del": "myclub", "por": "myclub", "ghost": "unjoinable"}
    pos3 = {"del": "DEL", "por": "POR"}
    ppm3 = {"del": 8.0, "por": 4.0}
    out = apply_fixtures(pj3, board, club3, pos3, ppm3)
    # A DELANTERO prices off the opponent's DEFENSE (atk_factor); a PORTERO
    # off the opponent's ATTACK (def_factor) — the same split score.py's
    # own fix_factor already makes, read here for a jornada rather than
    # for today.
    assert out[1]["del"] == (8.0 * 1.2, 0.9), out[1]["del"]
    assert out[1]["por"] == (4.0 * 1.1, 0.9), out[1]["por"]
    # A genuinely different fixture the following jornada gives a
    # genuinely different price — the whole point.
    assert out[2]["del"] == (8.0 * 0.8, 0.9), out[2]["del"]
    assert out[1]["del"] != out[2]["del"], (out[1]["del"], out[2]["del"])
    # P(start) is NEVER touched by this — same 0.9 before and after.
    assert out[1]["del"][1] == pj3[1]["del"][1] == 0.9
    # No Match for his club that jornada: the ORIGINAL (frozen) pts survive
    # rather than being zeroed or dropped.
    assert out[1]["ghost"] == (3.0, 0.5), out[1]["ghost"]

    # -- best_swap_for: a held player's REAL value, not a pure sale ---------
    from ffcore.forecast import Bootstrap as B2

    per_sw = {1: {"me_k": (2.0, 1.0), "me_star": (12.0, 1.0),
                  "cheap_up": (5.0, 1.0), "rich_up": (5.0, 1.0),
                  "too_rich": (20.0, 1.0), "worse": (1.0, 1.0),
                  "riv_up": (6.0, 1.0)}}
    u_sw = Universe(
        state=LeagueState({"me": {"me_k": "MED", "me_star": "MED"},
                           "riv": {"riv_up": "MED"}}, [1], "me"),
        forecaster=B2(per_sw),
        pos={"me_k": "MED", "me_star": "MED", "cheap_up": "MED",
            "rich_up": "MED", "too_rich": "MED", "worse": "MED",
            "riv_up": "MED"},
        price={"cheap_up": 3e6, "rich_up": 3e6, "too_rich": 50e6,
              "worse": 1e6, "riv_up": 6e6},
        route={"riv_up": "clause"}, owner={"riv_up": "riv"},
        proceeds={"me_k": 2e6}, cash=3e6, me="me")
    exp_sw = u_sw.forecaster.expected(1)

    # me_k (exp 2.0) has a real, affordable upgrade: cheap_up and rich_up
    # tie at exp 5.0, price 3e6 each, budget cash(3e6)+proceeds(2e6)=5e6 —
    # TIE BREAKS TOWARD THE CHEAPER ONE, but both cost the same here, so
    # either is a legal answer; what matters is it is NOT "worse" (below
    # his own exp) and NOT "too_rich" (unaffordable) and NOT "riv_up"
    # (exp 6.0 would win outright if affordable, but 4e6 > his 5e6 budget).
    got = best_swap_for(u_sw, "me_k", exp_sw)
    assert got is not None and got.buy in ("cheap_up", "rich_up"), got
    assert got.sell == ("me_k",) and got.cost <= 5e6, got
    assert got.proceeds == 2e6, got
    assert got.kind == "swap", got

    # me_star (exp 12.0) already beats everything on the board — no swap,
    # not a swap to something worse.
    assert best_swap_for(u_sw, "me_star", exp_sw) is None

    # A CLAUSE TARGET IS TAGGED, same as candidates()'s own raid logic —
    # give me_k a bigger budget so riv_up (exp 6.0, the best on the board)
    # becomes reachable and wins outright.
    u_sw2 = Universe(
        state=u_sw.state, forecaster=u_sw.forecaster, pos=u_sw.pos,
        price=u_sw.price, route=u_sw.route, owner=u_sw.owner,
        proceeds={"me_k": 2e6}, cash=10e6, me="me")
    raided = best_swap_for(u_sw2, "me_k", exp_sw)
    assert raided.buy == "riv_up" and raided.kind == "clause-swap", raided
    assert raided.victim == "riv", raided

    # SAME SLOT ONLY — a cheap, high-expected DEL is not a real answer to
    # "what should me_k (MED) become", the same "one points scale, one
    # formation, but a squad SLOT is not a formation slot" case that put
    # a goalkeeper on three different midfielders' bands on 2026-08-25.
    per_slot = dict(per_sw)
    per_slot[1] = {**per_sw[1], "wrong_slot": (50.0, 1.0)}
    u_sw3 = Universe(
        state=u_sw.state, forecaster=B2(per_slot),
        pos={**u_sw.pos, "wrong_slot": "DEL"},
        price={**u_sw.price, "wrong_slot": 1e6},
        route=u_sw.route, owner=u_sw.owner,
        proceeds={"me_k": 2e6}, cash=10e6, me="me")
    exp_slot = u_sw3.forecaster.expected(1)
    got3 = best_swap_for(u_sw3, "me_k", exp_slot)
    assert got3.buy == "riv_up", got3     # not "wrong_slot", despite exp 50

    # -- funding widened to include starters, not just bench dead weight
    # (2026-08-29) -----------------------------------------------------------
    from ffcore.forecast import Bootstrap as B3

    sq3 = {"me_k": "POR", "me_d1": "DEF", "me_d2": "DEF", "me_d3": "DEF",
          "me_d4": "DEF", "me_m1": "MED", "me_m2": "MED", "me_m3": "MED",
          "me_m4": "MED", "me_m5": "MED", "me_f1": "DEL"}
    per3 = {1: {k: (3.0, 1.0) for k in sq3}}
    # k and f1 sit at the SLOT_MIN floor (1 keeper, 1 forward) — given the
    # LOWEST expected points here on purpose, so the widened pool's own
    # ascending sort would try them FIRST if the SLOT_MIN guard were not
    # there. d1/m1/m2 are the next-weakest, safely above their own floors
    # (DEF depth 4 > min 3, MED depth 5 > min 3).
    per3[1]["me_k"] = (0.1, 1.0)
    per3[1]["me_f1"] = (0.2, 1.0)
    per3[1]["me_d1"] = (2.5, 1.0)
    per3[1]["me_m1"] = (2.6, 1.0)
    per3[1]["me_m2"] = (2.7, 1.0)
    per3[1]["mid_up"] = (6.0, 1.0)
    per3[1]["mid_big"] = (8.0, 1.0)
    u3 = Universe(
        state=LeagueState({"me": dict(sq3)}, [1], "me"),
        forecaster=B3(per3),
        pos={**sq3, "mid_up": "MED", "mid_big": "DEL"},
        price={"mid_up": 14e6, "mid_big": 19e6},
        proceeds={"me_d1": 5e6, "me_m1": 4e6, "me_m2": 4e6},
        owner={}, cash=10e6, me="me")
    exp3 = u3.forecaster.expected(1)

    assert dead_weight(u3) == [], dead_weight(u3)   # no bench: nothing free

    acts3 = candidates(u3, exp3)
    # SLOT_MIN GUARD: the sole keeper and sole forward are never offered as
    # a seller, even though they were deliberately given the lowest exp of
    # anyone — selling either would leave no legal shape at all.
    assert not any("me_k" in a.sell for a in acts3), \
        [a for a in acts3 if "me_k" in a.sell]
    assert not any("me_f1" in a.sell for a in acts3), \
        [a for a in acts3 if "me_f1" in a.sell]

    # mid_up (14M): out of reach on cash (10M) alone, in reach on cash plus
    # ONE weak starter (me_d1, 5M) — the widened spare pool, not dead
    # weight (there is none), funds it.
    up_rows = [a for a in acts3 if a.buy == "mid_up"]
    assert up_rows, acts3
    assert any(a.sell == ("me_d1",) for a in up_rows), up_rows
    assert not any(not a.sell for a in up_rows), up_rows   # not cash-alone

    # mid_big (19M): out of reach on cash plus ANY single spare (best single
    # is me_d1 at 15M) — needs two REAL sales. A SEPARATE, PADDED squad
    # (sq3 plus one spare DEF) for this one check only — sq3 itself stays
    # at exactly 11 (T0=11 can NEVER survive 2 sales for 1 buy: 11-2+1=10,
    # under XI_SIZE — see _safe_to_sell()'s own note) and the
    # best_swap_for() test right after this one depends on sq3's own
    # DEF-at-the-floor arithmetic, which extra padding here would have
    # thrown off if done in place.
    sq3_pad = {**sq3, "me_pad": "DEF"}
    per3_pad = {1: {**per3[1], "me_pad": (0.05, 1.0)}}   # weakest on the
    # board, $0 proceeds — real dead weight (`dead_weight()` below), but
    # SKIPPED outright by the funding chain for raising nothing
    # (2026-09-01), so it spends none of the one extra body's legality
    # headroom — the combo below is exactly the two REAL sales that close
    # the 9M gap, not a wasted "free" one riding in front of them.
    u3_pad = Universe(
        state=LeagueState({"me": dict(sq3_pad)}, [1], "me"),
        forecaster=B3(per3_pad),
        pos={**sq3_pad, "mid_up": "MED", "mid_big": "DEL"},
        price={"mid_up": 14e6, "mid_big": 19e6},
        proceeds={"me_d1": 5e6, "me_m1": 4e6, "me_m2": 4e6},
        owner={}, cash=10e6, me="me")
    assert dead_weight(u3_pad) == [("me_pad", 0.0)], dead_weight(u3_pad)
    big_rows = [a for a in candidates(u3_pad, u3_pad.forecaster.expected(1))
               if a.buy == "mid_big"]
    assert big_rows, big_rows
    combo = next((a for a in big_rows if len(a.sell) >= 2), None)
    assert combo is not None, big_rows
    assert set(combo.sell) <= {"me_d1", "me_m1", "me_m2"}, combo
    assert combo.proceeds >= 9e6, combo   # d1+m1 = 9M, exactly what 19M needs
    assert "me_k" not in combo.sell and "me_f1" not in combo.sell, combo
    assert "me_pad" not in combo.sell, combo

    # -- best_swap_for(): the same widened chain, so a held player's own
    # band stops answering a narrower question than the main table --------
    # A SEPARATE, PADDED squad again (two extra MED this time, not DEF —
    # the whole point of this test is DEF landing EXACTLY at its floor
    # once me_d2 leaves, so padding DEF here would blunt the very guard
    # being tested). me_d2's own sale plus BOTH weak midfielders (3 sells,
    # 1 buy) needs T0 >= 13 to stay legal (11 - 3 + 1 = 9, under XI_SIZE
    # otherwise) — sq3 itself (T0=11) stays untouched, same reasoning as
    # the mid_big block above. Both padding players are skipped outright
    # by the chain (raise $0), so each contributes pure headroom without
    # ever being sold itself, and without changing which REAL players
    # fund the purchase.
    sq3b = {**sq3, "me_pad2": "MED", "me_pad3": "MED"}
    per3b = {**per3[1], "riv_target": (7.0, 1.0),
            "me_pad2": (0.05, 1.0), "me_pad3": (0.05, 1.0)}
    state3b = LeagueState({"me": dict(sq3b)}, [1], "me")
    u3b = Universe(
        state=state3b, forecaster=B3({1: per3b}),
        pos={**sq3b, "riv_target": "DEF"},
        price={"riv_target": 14e6},
        proceeds={"me_d1": 5e6, "me_m1": 4e6, "me_m2": 4e6},
        owner={}, cash=4e6, me="me")
    exp3b = u3b.forecaster.expected(1)
    # me_d2's own sale (no proceeds set -> 0.0) plus cash (4M) alone, and
    # the ONLY other extra funding available (me_m1/me_m2, 4M each — me_d1
    # is guarded off once me_d2 himself leaves, DEF drops to the SLOT_MIN
    # floor) tops out at 12M — still short of 14M. The OLD best_swap_for,
    # scoped to k's own proceeds + cash only, would ALSO have said None
    # here, so this is the honest "no answer" floor, not a regression.
    assert best_swap_for(u3b, "me_d2", exp3b) is None

    # Give him a real proceeds figure — still short of 14M with cash alone,
    # and even his own sale plus BOTH weak midfielders is needed to reach it.
    u3c = Universe(
        state=state3b, forecaster=B3({1: per3b}),
        pos={**sq3b, "riv_target": "DEF"},
        price={"riv_target": 14e6},
        proceeds={"me_d1": 5e6, "me_m1": 4e6, "me_m2": 4e6, "me_d2": 3e6},
        owner={}, cash=4e6, me="me")
    exp3c = u3c.forecaster.expected(1)
    got3c = best_swap_for(u3c, "me_d2", exp3c)
    assert got3c is not None and got3c.buy == "riv_target", got3c
    assert "me_d2" in got3c.sell, got3c
    assert len(got3c.sell) > 1, got3c        # his own sale alone isn't enough
    assert "me_k" not in got3c.sell and "me_f1" not in got3c.sell, got3c
    assert "me_pad2" not in got3c.sell, got3c   # $0, skipped outright
    assert "me_pad3" not in got3c.sell, got3c
    assert got3c.proceeds == sum(u3c.proceeds.get(p, 0.0)
                                 for p in got3c.sell), got3c

    # -- value_rate: the shared primitive, on its own -----------------------
    assert value_rate(120.0, 14.13e6) is not None
    assert abs(value_rate(120.0, 14.13e6) - 120.0 / 14.13) < 1e-9
    assert value_rate(120.0, 0.0) is None       # nothing to divide by
    assert value_rate(120.0, -5e6) is None       # a net-negative "cost" is not one
    assert value_rate(None, 5e6) is None
    assert value_rate(0.0, 5e6) == 0.0           # a real price, zero return: 0, not None

    # -- rival_tempo: each manager's OWN realised behaviour ----------------
    # A SYNTHETIC LEDGER IN THE REAL FILE'S SHAPE — one side of every row is
    # the pool, exactly as ledger.py writes it. Two rivals with deliberately
    # different behaviour over the same ten days: `busy` sells four times for
    # 40M, `hoarder` sells once for 1M and has not moved since day one. This
    # is the distinction the league-wide prior cannot make and this can.
    txns_t = [
        {"date": "2026-08-01T10:00", "from": "market", "to": "hoarder",
         "price": "5000000"},
        {"date": "2026-08-01T11:00", "from": "hoarder", "to": "market",
         "price": "1000000"},
        {"date": "2026-08-02T10:00", "from": "busy", "to": "market",
         "price": "10000000"},
        {"date": "2026-08-04T10:00", "from": "busy", "to": "market",
         "price": "10000000"},
        {"date": "2026-08-07T10:00", "from": "busy", "to": "market",
         "price": "10000000"},
        {"date": "2026-08-11T10:00", "from": "busy", "to": "market",
         "price": "10000000"},
        {"date": "2026-08-11T10:00", "from": "market", "to": "busy",
         "price": "30000000"},
    ]
    tp = rival_tempo(txns_t)
    assert set(tp) == {"hoarder", "busy"}, tp     # "market" is never a manager
    assert tp["busy"]["sells"] == 4 and tp["busy"]["buys"] == 1, tp["busy"]
    assert tp["busy"]["sold"] == 40e6, tp["busy"]
    assert tp["hoarder"]["sells"] == 1 and tp["hoarder"]["buys"] == 1
    # ONE DENOMINATOR FOR EVERYONE — the whole ledger's span (10 days), not
    # each manager's own first-to-last. Scored on his own span the hoarder's
    # single sale would read as 1M/day over one hour, i.e. faster than the
    # rival who actually raised 40M.
    assert tp["busy"]["days"] == tp["hoarder"]["days"] == 10.0, tp
    assert abs(tp["busy"]["sell_rate"] - 4e6) < 1e-6, tp["busy"]
    assert abs(tp["hoarder"]["sell_rate"] - 0.1e6) < 1e-6, tp["hoarder"]
    assert tp["busy"]["sell_rate"] > 10 * tp["hoarder"]["sell_rate"]
    # Idle: days since that manager's own last deal, off the ledger's end.
    assert tp["busy"]["idle"] == 0.0, tp["busy"]
    assert abs(tp["hoarder"]["idle"] - 9.958333) < 1e-4, tp["hoarder"]
    assert rival_tempo([]) == {}                  # no ledger, no claims

    # -- days_to_afford: the forward estimate, and its edges ----------------
    # Already solvent for the price: today, not "0.0 days from now".
    assert days_to_afford(30e6, 20e6, 1e5, 1e6) == 0
    # On the allowance alone: 10M short at 100K a day is 100 days.
    assert days_to_afford(0.0, 10e6, 1e5, 0.0) == 100
    # THE SAME MANAGER WITH A MEASURED SALE RATE IS AN ORDER OF MAGNITUDE
    # NEARER — this is the whole reason sell_rate is in the model, and the
    # real reading it was built from (Albert Laporta: 450 days on the
    # allowance, 8 days on his own realised rate) has exactly this shape.
    assert days_to_afford(0.0, 10e6, 1e5, 4e6) == 3
    # Overdrawn is a real state, not unknown — the arithmetic just starts
    # further back.
    assert days_to_afford(-45e6, 10e6, 1e5, 11.9e6) == 5
    # Ceiling: he cannot sell more than he holds, so past the wall the
    # answer is "never", not an enormous number of days.
    assert days_to_afford(0.0, 500e6, 1e5, 4e6, ceiling=100e6) is None
    assert days_to_afford(0.0, 50e6, 1e5, 4e6, ceiling=100e6) == 13
    # No rate at all and short: never. Not zero, not a division by zero.
    assert days_to_afford(0.0, 10e6, 0.0, 0.0) is None
    # Unknown cash stays unknown — never silently read as broke.
    assert days_to_afford(None, 10e6, 1e5, 4e6) is None

    # -- contest(): who else can take him, and when ------------------------
    # rich can pay today; slow needs to sell for it; broke's whole squad
    # plus his cash cannot reach the price at all, so he is never a threat.
    per_c = {1: {k: (3.0, 1.0) for k in
                 ("me_a", "prize", "slow_a", "rich_a", "broke_a")}}
    u_c = Universe(
        state=LeagueState({"me": {"me_a": "MED"}, "own": {"prize": "MED"},
                           "slow": {"slow_a": "MED"},
                           "rich": {"rich_a": "MED"},
                           "broke": {"broke_a": "MED"}}, [1], "me"),
        forecaster=B3(per_c),
        pos={"prize": "MED"}, proceeds={},
        price={"prize": 20e6, "listed_one": 20e6},
        route={"prize": "clause", "listed_one": "listed"},
        owner={"prize": "own"}, cash=50e6, me="me",
        value={"slow_a": 30e6, "rich_a": 30e6, "broke_a": 1e6},
        daily_bonus=1e5,
        # `own` IS DELIBERATELY RICH ENOUGH TO PAY TODAY, so his absence
        # below is the exclusion doing work rather than him failing the
        # arithmetic anyway.
        rival_cash={"own": 25e6, "slow": 0.0, "rich": 25e6, "broke": 0.0},
        tempo={"own": {"sell_rate": 2e6}, "slow": {"sell_rate": 2e6},
               "rich": {"sell_rate": 2e6}, "broke": {"sell_rate": 2e6}})
    got_c = contest(u_c, "prize")
    # THE OWNER IS NOT A CONTENDER FOR HIS OWN PLAYER, and neither am I.
    assert "own" not in dict(got_c) and "me" not in dict(got_c), got_c
    # broke's ceiling is 0 + 1M < 20M: never, so he is absent entirely
    # rather than carried as a very large number of days.
    assert "broke" not in dict(got_c), got_c
    assert dict(got_c)["rich"] == 0, got_c        # 25M in hand, today
    assert dict(got_c)["slow"] == 10, got_c       # 20M at 2.1M a day
    assert got_c[0][0] == "rich", got_c           # soonest first
    # CLAUSE TARGETS ONLY — a listed row is a bid that can lose and this
    # function has no model of that; Universe.bids is the signal there.
    assert contest(u_c, "listed_one") == [], contest(u_c, "listed_one")
    assert contest(u_c, "nobody") == []

    print("decide self-test OK (145 cases)")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        raise SystemExit(0)
    u = load()
    exp = u.forecaster.expected(u.state.jornadas[0])
    acts = candidates(u, exp, budget=float("inf"))
    print("%d jornadas left · cash %s · %d players acquirable · %d actions"
          % (len(u.state.jornadas), fmt_money(u.cash), len(u.price), len(acts)))
    print(u.forecaster.pool_note())
    rows, base, _lam, _b = rank(u, acts)
    print("\nnow: expected position %.2f · P(win) %.0f%%"
          % (base.expected_position(), 100 * base.position().get(1, 0)))
    rivals = [m for m in u.state.squads if m != u.me]
    print("\n%-52s %6s %7s %10s   %s"
          % ("do this", "Δpos", "Δwin", "net €", "biggest gain vs"))
    for r in rows[:8]:
        a = r["action"]
        who = max(rivals, key=lambda v: r["d_beat"][v])
        print("%-52s %+6.3f %+6.1f%% %10s   %s %+.0f%%"
              % (a.label()[:52], r["d_pos"], 100 * r["d_win"],
                 fmt_money(-a.net), who[:16], 100 * r["d_beat"][who]))
