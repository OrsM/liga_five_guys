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
from ffcore.season import (LeagueState, best_xi,  # noqa: E402
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
FINAL_TRIALS = 3000
KEEP = 12          # how many survive screening and get the full count

# A RELIABLE FLOOR, ON TOP OF KEEP, NOT INSTEAD OF IT. Checked against this
# league's real transaction history 2026-08-29 (data/tidy/transactions.csv):
# 108 recorded transactions, all 108 "from the app" — zero manager-to-manager
# sales, ever. Re-checked 2026-08-31 at 119 rows and it still holds, this time
# PROVEN rather than read off the ledger's own `from`/`to` columns, which
# cannot show a counterparty at all (ledger.py's own note: the feed names one
# manager per row, so every deal is written against the pool by construction —
# reading "0 manager-to-manager" off those columns would be reading the
# writer's own lossiness back as a finding). The real check replays ownership
# from inputs/rosters_initial.txt forward over every api_activity row: 119
# deals, zero buys of a player somebody already held, zero sells by anybody
# but the holder, and the two sell→buy pairs on the same player are 4 and 10
# days apart — sold to the app, later re-bought from it, not a transfer.
#
# AND IT IS THE SAME FOR ALL FOUR RIVALS, which is a real negative result and
# is why there is no per-rival version of this constant. Attributing every
# `marketPlayerTeam` row in api_market.csv to whoever owned that player at
# that moment gives each rival's own listing history: Albert Laporta 20
# players listed, BurtonGM89 19, SusoGattuso 18, Magic Mike 333 zero, ever.
# Of those listings 44–55% did eventually leave the squad — but every one of
# them left TO THE APP, so the conversion rate that matters here (a listing
# that becomes a sale TO ANOTHER MANAGER) is 0/57 pooled and 0 for each rival
# individually. There is nothing to split them on: the three who list are
# within binomial noise of each other on the rate that CAN be measured
# (±11pp at n≈20), and the fourth has never given the question a data point.
# So the league-wide prior below stays league-wide. What IS differentiated per
# rival is how fast each can raise money — see rival_tempo() and
# days_to_afford(), which is where the per-rival read went instead.
#
# A "listed" candidate (Universe.route: a rival's own sale, who
# can simply not sell) screens on the same raw gain as a "free"/"clause" one
# that is actually guaranteed to go through, and on this box's real data
# listed candidates filled 10 of KEEP's 12 slots — a free-agent move outside
# the raw top 12 never reached the full FINAL_TRIALS pass at all, not even to
# be shown with a real band in the report table, because listed candidates
# that will likely never happen crowded it out of the SAMPLE, not just out of
# the final recommendation. This tops up `keep` with the best-screened
# reliable candidates not already in it, until at least this many are in the
# final pass — added on top of the natural top-KEEP, never displacing
# anything from it, so a real listed opportunity stays fully visible too.
KEEP_RELIABLE_MIN = 6

# AN EFFICIENT BUT MODEST CANDIDATE, VIA THE SAME MECHANISM AS
# KEEP_RELIABLE_MIN (_top_up(), below) — `screened` is sorted by raw gain,
# so `top = screened[:KEEP]` structurally cannot contain a candidate that
# gains little but costs almost nothing: on a day the top-12 are all
# €5-20M moves for 0.1-0.3 places, a €200k move worth 0.05 places never
# reaches the FINAL_TRIALS pass at all, so nothing downstream — not the
# report table, not sim._best()'s own value-for-money re-pick — can ever
# find it. It is not ranked low; it is not scored. This tops up `top` with
# the best-screened-by-efficiency candidates not already in it, same
# non-destructive shape as KEEP_RELIABLE_MIN: on top, never displacing a
# naturally-kept row. Efficiency here is the cheap screening pass's own
# `d` (expected-position delta, not season points) per net euro — a proxy
# for the real `value` FINAL_TRIALS computes later, good enough to decide
# who earns the expensive pass.
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
    # simulation needs. The simulation scores who could be in a squad; asking
    # what the market might deal you next is a question about the other five
    # hundred, and `expected()` returning 0.0 for a player it was never given
    # is indistinguishable from him being worthless. That is not hypothetical:
    # it made Lamine Yamal and Vinicius Junior score zero, and produced the
    # finding that four players in the whole league could improve the eleven.
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
    # HOW you would get each player: "free" if a free agent the app itself is
    # dealing, "listed" if he is owned and a manager has put him up for sale
    # (a real owner who can simply not sell, and other managers may already
    # be bidding — see market_routes()), "clause" if the only route is
    # paying his buyout. Three different transactions and only "clause" is a
    # raid in the sense that pays the owner — calling a market purchase a
    # steal implies a denial benefit that, measured, is zero, and it is what
    # made a table of ordinary buys read as a raiding plan. On 2026-08-18
    # every acquirable player was route "market" (this field's old,
    # undifferentiated third value) and not one clause was payable; "free"
    # vs "listed" split out 2026-08-22, once it was measured that MOST of a
    # day's "market" rows are usually a rival's own listing, not a free
    # agent (28 of 41 the day the feed's own seller field was first read).
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

    THE ONE COMPUTATION EVERY "what is my/a rival's current best eleven
    worth" QUESTION IN THIS REPO USED TO REBUILD BY HAND — found duplicated
    across xi_note(), fielded_shape(), ladder_rows(), wait_routes(),
    _xi_total(), _shape_now() (all sim.py) and candidates() (this module),
    each with its own `exp = u.forecaster.expected(choosable(u)); xi =
    best_xi(squad, exp)` pair. All deterministic — no randomness, so no
    trial-to-trial drift risk the way ladder()'s old duplicate band
    simulation had — but still the same "no single owner"
    shape: seven implementations of one fact, kept in sync by hand rather
    than by construction, is exactly the mess a future change to best_xi()'s
    tie-breaking (or choosable()'s own jornada pick) would fall through.
    `exp` is the SAME dict regardless of `who` — only the squad it is read
    against differs — so a caller wanting several managers' bands calls
    this once per manager, not once for `exp` and again per squad.
    """
    exp = u.forecaster.expected(choosable(u))
    xi = set(best_xi(u.state.squads.get(who or u.me, {}), exp))
    return exp, xi


def xi_bar(exp: dict[str, float], xi) -> float:
    """The weakest man in an eleven — the number a signing has to clear.

    One line, but the same line was hand-written at every current_xi()
    call site that needed it (candidates(), wait_routes(), ladder_rows())
    before this existed, which is how "min(..., default=0.0)" is exactly
    the kind of detail that drifts silently if one copy is edited and the
    others are not.

    ONE FLAT NUMBER ACROSS ALL FOUR SLOTS, AND THAT IS THE CORRECT SCREEN
    RATHER THAN A MISSING FEATURE. Value-over-replacement theory says the
    bar ought to be position-specific — the worst starting defender is not
    the worst starting forward — and on the real board those bars are
    genuinely far apart (measured 2026-08-31: POR 5.82, DEF 2.69, MED
    2.54, DEL 3.62). A per-position bar would still be WRONG here, and the
    reason is the one season.py's best_xi() docstring already gives: YOU
    CAN CHANGE YOUR LAYOUT. A candidate below his own slot's bar enters
    the eleven by pushing that slot's count up and some other slot's down,
    so he helps the moment he beats the worst starter ANYWHERE — which is
    exactly this minimum. Screening him against his own slot's bar drops a
    man who would have played. The self-test carries the exact shape: a
    1-5-4-1 with a weak fifth defender, where a midfielder at 2.0 is below
    MED's own replacement level of 4.0 and still gains a point on the
    pitch by reshaping to 1-4-5-1 and taking that defender out.

    So the flat bar is the LOOSEST SOUND screen, and it errs the safe way.
    It lets through candidates who cannot actually help — 4 of 19 real
    targets on 2026-08-31, two of them keepers, since you only ever field
    one — and the simulation behind candidates() then prices those at
    roughly nothing (the day's keeper clause: -0.54 season points). That
    is a few wasted screening slots. The other error would be a lost move.
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

    SLOT_MIN is a hard floor per position — one keeper, at least three
    defenders and three midfielders, at least one forward — not a
    threshold to tune. Below it `best_xi()` cannot complete ANY shape
    (season.py:97), so the simulation would correctly price the result as
    ruinous, but there is no reason to spend a screening pass finding
    that out. `depth` is checked against the CURRENT squad, mutated by
    the caller as sales are chosen, so a chain that would sell two men
    from an already-thin position is caught even when either alone is
    safe.
    """
    slot = u.pos.get(k, "MED")
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
    # FUNDING IS NOT JUST BENCH ANY MORE. A starter is a legal sale too, and
    # the only reason to leave him out of the pool is if the squad cannot
    # spare him (SLOT_MIN) — whether he is WORTH selling is what the
    # simulation this feeds is for. Weakest expected contributor first,
    # whichever regime (bench or XI) he happens to be in.
    depth0 = _squad_depth(mine_squad)
    spare = sorted((k for k in mine if _safe_to_sell(u, k, depth0)),
                   key=lambda k: bar_exp.get(k, 0.0))
    # DEAD WEIGHT PAYS FOR THINGS FIRST. These never make the eleven, so
    # selling them costs nothing on the pitch — tried before any starter
    # sale, which does cost something. Biggest first, so the greedy below
    # never sells four men to do the work of three. Weak starters (SLOT_MIN-
    # safe, weakest first) fill in only once dead weight runs out.
    free = sorted(dead_weight(u), key=lambda kv: -kv[1]) \
        + _weak_starters(u, xi, bar_exp, dict(depth0))

    out: list[Action] = []
    for c, price in sorted(u.price.items(), key=lambda kv: kv[1]):
        if c in mine or bar_exp.get(c, 0.0) <= bar:
            continue
        victim = u.owner.get(c, "")
        # A RAID IS PAYING A CLAUSE. Buying a man off the market is a
        # purchase, whoever happens to own him — and the measured denial value
        # of doing so was zero, because the managers listing players are not
        # the one being raced. Labelling those "steal" implied a benefit that
        # is not there and made an ordinary shopping list read as a raid.
        raid = bool(victim and victim != u.me
                    and u.route.get(c, "market") == "clause")
        kind = "clause" if raid else "buy"
        swap = kind + "-swap" if raid else "swap"
        if price <= cash:
            out.append(Action(kind, buy=c, cost=price,
                              victim=victim if raid else ""))
        # Funded by a sale: only the cheapest few spares are worth trying —
        # bench or a weak starter, now — because selling a man you field to
        # buy one you also field is a swap the simulation will price at
        # roughly nothing. Six, not four: the pool is bigger than bench-only
        # was, so a couple more are worth a look.
        for s in spare[:6]:
            got = u.proceeds.get(s, 0.0)
            if price <= cash + got:
                out.append(Action(swap, buy=c, sell=s, cost=price,
                                  proceeds=got,
                                  victim=victim if raid else ""))
        # Out of reach on cash plus any ONE spare, but not out of reach: the
        # fewest sales that cover it, dead weight first, weak starters after.
        # One combination per target rather than every subset — they are all
        # worth the same on the pitch, so the only thing to choose between
        # them is how few men leave. Depth is tracked and re-checked as sales
        # are chosen — two starters from the same thin position can each be
        # individually safe and still not be safe TOGETHER.
        # THE TRIGGER IS THE REAL BALANCE, not the budget. `budget` widens
        # what gets EMITTED, so the frontier can be measured off targets you
        # cannot afford; whether the sales on hand are enough to reach a man
        # is a fact about the money you actually have. Keyed to the budget
        # instead, an unlimited one made every target reachable on cash alone
        # and the multi-sale moves stopped being generated at all — which
        # silently removed the best move on the board.
        if price > u.cash + max((u.proceeds.get(s, 0.0) for s in spare),
                                default=0.0):
            sold, got = [], 0.0
            depth = dict(depth0)
            for k, raises in free:
                if price <= u.cash + got:
                    break
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
    "idle"}}` — counts, euros, the span of the ledger in days, gross sale
    proceeds per day, and days since that manager's last deal of any kind.

    WHY GROSS PROCEEDS PER DAY AND NOT NET CASH FLOW. Net is negative for
    every manager in this league (measured 2026-08-31: −4.5M to −8.2M a day
    each) because they are all still deploying a starting budget that only
    gets spent once, so extrapolating it forward predicts everyone going
    infinitely broke — a rate that cannot continue is not a trajectory. The
    question days_to_afford() actually asks is "how fast has this manager
    demonstrated he can PUT MONEY TOGETHER", and gross sale proceeds per day
    is exactly that, measured, with no assumption about what he then does
    with it.

    ONE SIDE OF EVERY ROW IS THE POOL, by construction — see ledger.py's own
    header. That costs nothing here: a manager's own buys and sells are
    exactly the rows naming him, and who the counterparty was does not
    change how much money moved.

    `days` is the span of the WHOLE ledger, not each manager's own first-to-
    last: a manager who has done nothing for a week has a real rate of
    nearly zero and dividing by his own shorter span would hide that. It is
    the same denominator for everyone, which is what makes the four numbers
    comparable at all.
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

    WHAT IS MEASURED AND WHAT IS GUESSED, stated the way DRIFT_FRAC's own
    comment states it, because this is an estimate and reads like a fact:

      * `cash` — MEASURED for me (the app states `teamMoney` for the account
        that asks), ESTIMATED for a rival: the starting budget less every
        ledger row plus the accrued allowance. It carries a `~` everywhere it
        is printed and it can be a whole unseen sale wrong.
      * `daily_bonus` — a CONFIGURED FACT off inputs/league.ini (100K here),
        the one income the activity feed cannot see.
      * `sell_rate` — MEASURED, per rival, off that rival's own realised
        gross sale proceeds per day (rival_tempo()). It is a rate he has
        actually run, not a capacity anybody has assumed for him.
      * The COMBINATION — that he keeps raising money at his own past rate
        while the allowance accrues — is the GUESS, and it is the whole
        model. There is no attempt to say whether he WANTS this player.
        ffcore.bid.demand_summary() already answers that, as a snapshot of
        who can pay TODAY; this answers the orthogonal question it cannot,
        which is when the ones who cannot pay today start being able to.

    ALLOWANCE-ONLY WAS TRIED FIRST AND IS WRONG HERE, which is why
    `sell_rate` exists at all. On the allowance alone, Albert Laporta
    (−45.02M on 2026-08-31) needs 450 days to reach a zero balance and would
    be reported as no threat to anything for over a year — while the ledger
    shows him raising 86.9M across six sales in the preceding seven days. A
    bound nobody can act on, printed as a number, is worse than no number.

    `ceiling` is a hard cap on what he could ever reach — his cash plus what
    his whole squad is worth. Past it the answer is None (never), not a very
    large number of days: a manager cannot sell more than he holds, and the
    rate above would otherwise extrapolate straight through that wall.
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

    THE RACE, WHICH THE BOARD OTHERWISE DOES NOT PRICE. A clause is instant
    and cannot be refused — by me OR by anybody else — so a target sitting
    at a payable clause is not a thing I own the option on, it is a thing
    the first solvent manager takes. That changes what to do with a target
    in opposite directions depending on one number nothing here printed
    before: if the nearest rival is a month away there is no race and the
    money is better kept for a cycle this simulation cannot value (see
    "Cash scores zero"), and if he is two days away then waiting IS the
    decision, made by default.

    CLAUSE TARGETS ONLY, deliberately. A clause price is published, applies
    to everybody alike, and cannot be refused, so "can he pay it" is the
    whole of "can he take him". A free-agent or listed row is a BID that can
    lose, and what it costs to win one is a fact about behaviour this
    function has no model of — `Universe.bids` (the app's own
    `numberOfBids`) is the real observed contest signal there, and it is
    already carried.

    THE OWNER IS NOT A CONTENDER for his own player and neither am I: this
    is who else could take him out from under me, which is a question about
    the other three.
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
    pitch whatever else happens, so selling him costs nothing and any offer is
    a gain. It is the one verdict this system still makes, and the only one
    reachable without valuing cash — every other Buy/Sell/Hold/Watch string
    was a proxy for "does this move me up the table", which is now a column.

    Checked against every jornada you can still PICK, which is not the same as
    every jornada left. A round already in progress has its eleven locked, and
    it fields a different one from the rest because the players whose clubs
    have kicked off are out of it — so a man who starts only there is not
    being fielded by any decision still open to you. On the day this was
    written that was Dani Lorenzo, in one jornada of thirty-eight, and the old
    board had him right: sixth midfielder, spare for the rest of the season.
    If NO choosable round is left, the locked one is all there is and second-
    guessing it helps nobody.

    What it deliberately does NOT do is rank them against each other or say
    what to hold out for. That needs the option value of cash, and nothing
    here models next cycle's market — but it DOES fund things: see
    candidates(), where these pay for the moves nothing else can reach.
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
    candidates() draws on can reach. None when nothing does: the honest
    answer for a man with no affordable upgrade is "keep him", not a
    swap that does not exist.

    WIDENED FUNDING, 2026-08-29. Used to stop at k's own proceeds plus
    cash — a real gap, flagged the night before: it answered "what is he
    worth alone" when the question this exists for is "what would it
    take", the same one candidates() answers for the rest of the board.
    Extra sales (never k himself, SLOT_MIN-safe, see _weak_starters())
    only get proposed once his own sale plus cash isn't enough — a target
    reachable on his own is never funded by more men than it needs to be.

    THIS IS THE QUESTION A HELD PLAYER'S BAND SHOULD ANSWER and a pure
    sale (sell him, buy nothing) is not it — see sim.band_acts()'s own
    note on why "what does losing him cost, with no plan for the money"
    understated a bench player who funds a real replacement. It is a
    DIFFERENT question from candidates()'s own swap search, which asks
    "what is the single best move on the whole board" and dedupes to one
    funding source per target — that crowds out every player who was not
    the WINNING funding source for whichever target won, so reusing its
    output cannot answer for every held player individually, only for
    the lucky few. Same cheap, deterministic screening candidates() uses
    (expected points, not a simulation — see its own docstring for why
    that is enough for a screen), scoped to one funding player instead
    of the whole squad.

    Ties break toward the cheaper target — cash left over is worth
    something a simulation run today cannot price, the same reasoning
    rank()'s own dedup step uses.

    SAME SLOT ONLY. `expected()` puts every position on one points scale
    — that is what lets best_xi() compare a keeper against a forward when
    it fills a formation, and candidates()'s own screen already trusts it
    that way for "would this new man even make the eleven". But a SQUAD
    slot is not a formation slot: replacing a MED with a POR does not
    field an extra keeper, it leaves the squad short a midfielder, and
    the real simulation prices that shape correctly — found 2026-08-25
    when three different bench players' "best real alternative" all came
    back as the one goalkeeper on the board, each one a real, honestly
    negative number (the simulation ballast for a broken squad shape is
    real) attached to a swap no manager would ever make. The band was
    right; the swap it was pointing at was not a question worth asking.
    """
    mine = u.state.squads.get(u.me, {})
    base_budget = u.cash + u.proceeds.get(k, 0.0)
    my_exp = expected.get(k, 0.0)
    slot = u.pos.get(k)

    # THE EXTRA CHAIN: dead weight first (free on the pitch), then other
    # weak starters, SLOT_MIN-safe, k already counted as gone. Built once,
    # walked once — `extra_names[i]`/`extra_running[i]` is "sell the first
    # i+1 of these, raise this much", so any target's minimal extra sale
    # is a lookup, not a fresh walk.
    depth = _squad_depth(mine)
    if slot in depth:
        depth[slot] -= 1
    xi_now = set(best_xi(mine, expected))
    # k HIMSELF NEVER RIDES IN THIS CHAIN — he is already the primary sale,
    # accounted for in base_budget, and dead_weight() does not know to
    # exclude him (it tags anyone never in ANY choosable XI, which k can be
    # if he is bench).
    chain = [(p, r) for p, r in sorted(dead_weight(u), key=lambda kv: -kv[1])
            if p != k] + _weak_starters(u, xi_now, expected, dict(depth),
                                        exclude=k)
    extra_names: list[str] = []
    extra_running: list[float] = []
    got = 0.0
    for s, raises in chain:
        if not _safe_to_sell(u, s, depth):
            continue
        depth[u.pos.get(s, "MED")] -= 1
        got += raises
        extra_names.append(s)
        extra_running.append(got)
    max_budget = base_budget + got

    best_c, best_exp, best_price, best_sell, best_proceeds = \
        None, my_exp, None, (k,), u.proceeds.get(k, 0.0)
    for c, price in u.price.items():
        if c == k or c in mine or u.pos.get(c) != slot or price > max_budget:
            continue
        e = expected.get(c, 0.0)
        if not (e > best_exp or (e == best_exp and best_c is not None
                                 and price < best_price)):
            continue
        if price <= base_budget:
            sell, proceeds = (k,), u.proceeds.get(k, 0.0)
        else:
            n = next(i for i, g in enumerate(extra_running)
                    if g >= price - base_budget) + 1
            sell = tuple(sorted((k, *extra_names[:n])))
            proceeds = u.proceeds.get(k, 0.0) + extra_running[n - 1]
        best_c, best_exp, best_price = c, e, price
        best_sell, best_proceeds = sell, proceeds
    if best_c is None:
        return None
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
    holds. `ok` takes `(d, a)` — the screened gain alongside the action —
    because a qualifying condition may need the gain (KEEP_VALUE_MIN's
    "genuine gain") as well as the action itself (KEEP_RELIABLE_MIN's
    route lookup, which ignores `d`).

    THE SHARED SHAPE BEHIND EVERY "raw screening alone can crowd a real
    axis out" RULE. KEEP_RELIABLE_MIN (guaranteed-to-go-through candidates
    losing to bigger-but-unreliable ones on raw gain) and KEEP_VALUE_MIN
    (efficient-but-modest candidates losing to expensive-but-bigger ones)
    are the same mechanic with a different `ok`/`rank_key` — this owns only
    the "top up, don't replace, don't duplicate" bookkeeping; each caller
    decides what qualifies and how to rank the qualifiers.
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

    Returns `(rows, base, measured, bands)`.

    Returned rows carry the change in expected finishing position and in
    P(above) each rival — the second is what you act on when one rival is the
    one you are actually racing.

    `extra` is `[(key, Action), ...]`, scored IN THE SAME FINAL PASS and
    come back as `bands`, `{key: (median, lo, hi, action)}` — the ladder's
    own "what is this one man really worth" question (see
    sim.band_acts()), `key` given explicitly rather than read off the
    Action because the two disagree exactly when the answer matters most:
    `key` for a held player's OWN swap (sell him, buy his best reachable
    upgrade — see best_swap_for()) is the man SOLD, not the one bought,
    and an Action alone cannot say which side a caller meant. It rides
    along rather than running second because THE DRAW DOES NOT DEPEND ON
    THE SQUAD: at 3000 trials the pass costs about 1.2s of drawing plus
    0.03s per squad scored (measured 2026-08-24), so a second pass paid
    the 1.2s again for nothing. Same seed, same trials, so the numbers
    are the numbers a separate pass gave.

    A `key` rank() is ALREADY returning a real BUY row for is dropped:
    that row's own pts_lo/pts_hi answer the question better, off the
    squad the victim's response leaves behind rather than a bare swap.
    The buy side ONLY — see the skip's own note on why checking the sell
    side too once dropped a held player's band for a reason that had
    nothing to do with his own row. The caller cannot make that cut
    itself — which moves survive screening is this function's own
    answer — which is why `extra` is handed over whole.

    `acts` may contain moves you CANNOT afford today. They are screened and
    then dropped, and the reason is that screening them is how the price of
    cash gets measured: the frontier of "best Δpos reachable for this much
    extra" is exactly the question "what is a million worth", and it falls out
    of a pass that was happening anyway. See cash_price().

    `price` is places per million, smoothed across runs by the caller. Given
    one, every move is CHARGED for the wealth its clause destroys — that
    premium is money that never comes back, and a table ranked on points alone
    treats it as free. Without one, today's own measurement is used.
    """
    # ONE DRAW PASS FOR THE WHOLE SCREEN. Every option is scored against the
    # same seed, so simulate() was redrawing an identical season for each of
    # them and throwing it away — eight million draws where a hundred thousand
    # do. See ffcore.season.simulate_many; the numbers are unchanged.
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

    # ONE ROW PER TARGET, chosen here rather than after the expensive pass.
    # Four funding variants of one signing screen identically — selling a man
    # who never makes the eleven changes nothing on the pitch, only in the
    # balance — so keeping all four wastes the final budget on duplicates and
    # crowds out genuinely different options. Ties break toward spending
    # less: same outcome, more cash left for a cycle this cannot value.
    pick: dict[str, tuple] = {}
    for d, a in screened:
        k = a.buy or a.sell
        cur = pick.get(k)
        if cur is None or (d, -a.net) > (cur[0], -cur[1].net):
            pick[k] = (d, a)
    screened = sorted(pick.values(), key=lambda t: (-t[0], t[1].net))

    # ...and one for the survivors, at the full count.
    top = screened[:KEEP]
    # TOP UP WITH RELIABLE CANDIDATES, ON TOP OF `top`, via _top_up() — see
    # its own docstring and KEEP_RELIABLE_MIN's. A "listed" target (a
    # rival's own sale) screens on the same raw gain as a guaranteed
    # free/clause one, so the natural top-KEEP can end up almost entirely
    # listed; this adds the best-screened reliable candidates NOT already
    # in `top`, so they reach the full-precision pass too, without
    # removing a single listed one that made it there honestly.
    top = _top_up(top, screened,
                 ok=lambda d, a: u.route.get(a.buy, "free") != "listed",
                 rank_key=lambda t: -t[0], minimum=KEEP_RELIABLE_MIN)
    # TOP UP WITH THE MOST EFFICIENT CANDIDATES, ALSO VIA _top_up() — see
    # KEEP_VALUE_MIN's own note. Independent of the reliability top-up just
    # above: a candidate can be both listed AND efficient, or reliable AND
    # inefficient — the two ask different questions and both add, neither
    # replacing the other's picks nor the natural top-KEEP's.
    #
    # `ok` CANNOT BE "d > 0 and a.net > 0" — nearly every ordinary buy
    # candidate satisfies that (found while writing this: `have` came out
    # >= KEEP_VALUE_MIN from the natural top-KEEP alone, every time, making
    # the top-up a silent no-op regardless of how inefficient the top-KEEP
    # actually was). "Efficient" is inherently RELATIVE — the best few by
    # ratio among everything screened, genuine-gain-and-spend candidates
    # only — so that set is computed once, up front, and `ok` just asks
    # membership in it.
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
        # HE ANSWERS BEFORE THE SEASON IS PLAYED. Scoring the position the
        # instant after my move prices a duel as an execution — and a clause
        # pays the owner, so the move that looks most like subtraction is the
        # one that funds the subtraction back.
        ans = respond(u, a, after)
        if ans is not None:
            after = {m: dict(sq) for m, sq in after.items()}
            for m in after:
                after[m].pop(ans.buy, None)
            after[a.victim][ans.buy] = u.pos.get(ans.buy, "MED")
        answers.append(ans)
        afters.append(after)
    # RIDING ALONG IN THE SAME PASS — see the docstring. Anything `extra`
    # asks about a player ALREADY ANSWERED by a real ranked row is dropped
    # here, where `keep` is known, rather than by a caller guessing at it.
    #
    # THE BUY SIDE ONLY, DELIBERATELY — not `a.sell` too. A held player
    # can turn up as the SELL side of some unrelated top-ranked money move
    # (he funds somebody else's swap) without that move answering the
    # question his OWN ladder row is asking: "what does the season cost
    # if HE specifically changes" (comes off the XI, gets sold, gets kept)
    # is a different question from "is this the single best move on the
    # board", and conflating them dropped a real player's own band for no
    # reason connected to his row — found 2026-08-25 when Jon Moncayola's
    # OUT row went blank because he happened to fund the top-ranked clause
    # move. `a.buy`, by construction, can never collide with a HELD
    # player's own key (candidates() never buys someone already owned),
    # so this only ever fires for the case it was built for: a CANDIDATE
    # already shown as a real BUY row.
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
            # VALUE FOR MONEY: season points gained per million ACTUALLY
            # PAID — the question this table never answered before,
            # "can I pay the price for the incremental points, or is the
            # price too high for what it buys". `d_pts`, NOT `d_pos`: the
            # table's own primary unit is season points, and the reader
            # asking this question is asking about points, not the more
            # abstract position statistic screening uses internally.
            #
            # NOT THE OLD λ, on purpose — λ (retired 2026-08-17, c8c4032)
            # measured points-of-XI-index per million against YOUR CURRENT
            # ELEVEN off a ladder built from the whole unowned pool: the
            # baseline moved (the same player was worth a different λ on
            # consecutive days for reasons that had nothing to do with
            # him), and the ladder priced a market nobody could actually
            # shop in. This is neither: `d_pts` comes from `rank()`'s own
            # paired Monte Carlo — the SAME simulated seasons, with the
            # move and without it — which is what "PAIRED, WITHIN THE SAME
            # SEASONS" a few lines up already exists to make robust to a
            # changing model. Normalising an already-grounded number by
            # its cost is not the same mistake as normalising by an
            # ungrounded one.
            #
            # ONLY DEFINED FOR A GENUINE SPEND (net > 0). A sale that
            # raises MORE than it costs (net <= 0) is not "how many points
            # per million" — it is free money plus points, which needs no
            # rate to justify: it is obviously worth doing if d_pts > 0
            # and obviously not if d_pts < 0, and dividing by a near-zero
            # or negative net would either blow up or invert the sign
            # into something that reads backwards.
            #
            # AND IT IS ALREADY POINTS OVER POSITION REPLACEMENT LEVEL, so
            # there is no second `value_vor` beside it and there should not
            # be one (asked again 2026-08-31). `d_pts` is a PAIRED MARGINAL
            # — the same simulated seasons with the move and without it —
            # and the "with" side re-picks best_xi() over every legal
            # shape. A signing is therefore already scored against exactly
            # the man he displaces at his own position, in the formation
            # you would actually field once he arrives: replacement level
            # COMPUTED, not assumed, and re-derived per candidate rather
            # than fixed per slot. Measured in the self-test below: two
            # candidates on the SAME expected points and the SAME price,
            # one into a thin slot and one into a deep one, come out 3.2x
            # apart in `d_pts` and so in `value`.
            #
            # A static per-position baseline would be strictly WORSE than
            # this, not merely redundant, because it cannot see the reshape
            # (xi_bar()'s own note carries the counterexample) and because
            # it reintroduces the one thing decide.py exists to remove: a
            # rate standing in for the question, with its own baseline to
            # drift. Both module docstrings already retired "value over
            # replacement" by name for that reason. Nothing here is
            # scarcity-blind; `value` is a ratio of a scarcity-aware
            # numerator to the price actually paid.
            "value": value_rate(d_pts, a.net),
        })
    rows = sorted(out, key=lambda d: (-d["d_pos"], d["action"].net))
    return rows, base, measured, bands


def rounds_left(matches, teams) -> tuple[list[int], dict[int, set[str]], list]:
    """(jornadas still to come, who has already played one, unjoined clubs).

    A jornada with every score in is finished and is not simulated. A jornada
    with SOME scores in is the August case, and it is the one that pays twice:
    it is still ahead, so the simulator plays it — while the app has already
    banked the played matches into the carried total. Simulating those clubs
    again credits their points a second time, and NOT equally: on the day this
    was found, four of ten J1 matches were in, and it was handing BurtonGM89
    20.3 phantom points a round against my 7.8.

    So the round stays, and the clubs inside it that are done drop out. Their
    real points are already carried; what is left of the round is what has not
    kicked off.

    `teams` is the MARKET's list of clubs and the clubs come back as
    `club_key` keys, which is what the players are keyed by too — see the note
    there about the club with two spellings. A club that will not join comes
    back in `unjoined` rather than being assumed unplayed, because assumed-
    unplayed is exactly the double count this exists to remove, wearing a
    different name.

    What it still does not model: the eleven for a round in progress is
    ALREADY LOCKED, and the simulator re-picks it from whoever is left. That
    flatters everybody by letting them field a team they can no longer field,
    for one round out of thirty-eight.
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

    THE ONLY MATCH THIS WEEK'S STATUS FLAG CAN ACTUALLY SPEAK FOR. `base`
    carries this week's editorial reading (a suspension, a knock) blended
    in — real news about the one game it was published for. Handing that
    SAME reading to every remaining jornada of the season, which is what
    this repo did before, was reading a "he plays Sunday" answer as also
    "he plays in March" — see ffcore.score.Scored.pct_rest's own note for
    the case that found this, and cost.

    "First remaining jornada" is PER PLAYER, not one global jornada: a
    partial round mid-sweep drops a player from `rem[0]` once his own club
    has already played it (see rounds_left()'s own note on why), so his
    true next jornada is wherever he first appears here — which `played`
    already answers, one club at a time.
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
    real opponent — ffcore.fixture.season_board()'s own answer to "who do
    you face in jornada 20", not the single next-fixture factor `base`/
    `base_rest` were built with. THE SCHEDULE IS PUBLISHED for the whole
    season, so a forecast that prices jornada 20 off jornada 3's opponent
    is not a modelling limit, it is not having asked — see season_board()'s
    own docstring for why fitting the difficulty ratings once and reading
    them for every jornada is the same cost this repo already pays once.

    P(start) — the tuple's OTHER half — is untouched here: season_board()
    answers "who do you face", not "will you play", a different question
    next_then_rest() already answers as well as this repo's data allows
    (no future-dated editorial P(start) exists, only next week's).

    A player season_board() has no Match for in that jornada (an
    unjoinable club, or a jornada the schedule join missed) keeps whatever
    pts `per_jornada` already carried for him — the frozen, next-fixture
    number, worse than a real one and far better than zero.
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

    `seller` HAS ALWAYS SAID WHICH IS WHICH — `marketPlayerLeague` is the app
    dealing a free agent, `marketPlayerTeam` is a manager listing one of
    theirs (slate.py has documented this since the feed was added) — but
    every row here used to be labelled "market" regardless, collapsing "the
    app deals him, nobody can refuse" into the same bucket as "an owner who
    might simply not sell, and other managers may already be bidding
    (`numberOfBids`)." Those are not the same transaction, the same reason a
    clause and an ordinary buy are not: only one of them has a real owner
    who can say no.

    `key_of(row)` resolves a raw market row to this repo's own player key —
    the same join `load()` used inline before this was pulled out, handed in
    rather than imported so this stays testable on synthetic rows.
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
    overdraft, as `extra` for rank() — sim.band_acts()'s own question,
    only for the balance rather than for one man.

    ONLY WHEN CASH IS ACTUALLY NEGATIVE. A real offer already floors
    `proceeds` for anyone holding one (see received_offers's own note) —
    that is priced in whether or not you accept. What a real number
    cannot decide for you is whether accepting one *now*, ahead of the
    market, is worth it, and that question only has a forcing answer
    when the balance itself is overdrawn: the jornada will not lock with
    it negative (ffcore.league's own note), so something must be
    accepted, and this asks which.

    MINIMAL COVERS ONLY, in the subset-sum sense: a combination that
    clears the deficit with room to spare when a smaller one already
    does adds a second sale for nothing, so it never appears — every
    combo returned drops below the deficit with any one player removed
    from it. `itertools.combinations` over a HANDFUL of offers (this is
    never the whole squad, only the men with a real bid pending) so the
    2**n scan this runs is over single digits, not the market.

    A pure sell — no replacement bought — because the question here is
    "does this clear the overdraft", not "what should I buy with it";
    a combo that also priced a rebuy would be answering both at once
    and conflating them is exactly what Action's own `net` was built to
    keep apart. Keyed "OFFERS:a|b" rather than by player, since no
    single held player's key can stand for a combination.
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
    # OWNERSHIP IS League's, NOT RE-DERIVED HERE. It has already resolved the
    # app's own spelling three ways (ffcore.league.api_key), and a second,
    # weaker join in this module is not a second opinion — it is five rival
    # players who cannot be stolen because nothing knows whose they are.
    owner = dict(lg.owner)
    me = lg.cfg.me

    squads = {mgr: {k: SLOT[(players[k].get("pos") or "").lower()]
                    for k in lg.squad(mgr)
                    if k in players
                    and (players[k].get("pos") or "").lower() in SLOT}
              for mgr in lg.managers}

    # What it costs ME. A clause is instant and cannot be refused; a market
    # row is a bid that can lose, and that difference is not priced here —
    # see the module docstring. WITHIN "market", see market_routes()'s own
    # docstring for the further split it draws between a free agent and a
    # rival's own player put up for sale.
    #
    # Both sides join through ffcore.league.api_key, keyed on the market's
    # spelling like everything else. The clause is ON the api_teams row, so a
    # name that will not resolve is not a missing price — it is a rival's
    # player who silently cannot be bought at all.
    index = latest_only(lg.market.rows) if lg.market is not None else []
    price, route, bids = market_routes(
        mkt, lambda r: api_key(r["player_name"], "", lg.market, owner,
                               index, r.get("market_value")))
    now = run_now()
    clause_until: dict = {}
    # THE APP'S OWN OWNERSHIP-RECORD ID -> this repo's key. Built here
    # because this is already the one loop that resolves a key for every
    # api_teams row; offers.py's join needs nothing api_key() does not
    # already do, and a second resolution of the same rows for one more
    # field is the mistake ffcore.league.owner_from_api was written to stop.
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
        # A CLAUSE YOU CANNOT PAY IS NOT A PRICE. He is not cheap-but-risky or
        # worth ranking lower; the app will refuse the transaction outright, so
        # he does not belong in the set of things you could do today.
        if r["manager"] == me or not (r.get("buyout") or "").strip():
            continue
        if locked(clause_until, k, now):
            continue
        if k not in price:
            route[k] = "clause"
        price.setdefault(k, float(r["buyout"]))

    proceeds = {k: float((players[k] or {}).get("value") or 0)
                for k in squads.get(me, {})}
    # A REAL PENDING OFFER BEATS A GUESS — see received_offers()'s own note.
    received_offers = pending_received(load_api_offers(), pt_to_key)
    for k, money in received_offers.items():
        if k in proceeds:
            proceeds[k] = max(proceeds[k], money)
    # EVERY clause, mine included. The app publishes the whole league's, and a
    # rival cannot answer back without them.
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
    # What the app says everyone is worth. The market's own figure, which is
    # the one a sale pays out at — see burn(), where the gap between this and
    # a buyout clause is the wealth a steal destroys.
    value = {k: float((v or {}).get("value") or 0) for k, v in players.items()
             if (v or {}).get("value")}

    pos, base, base_rest = {}, {}, {}
    # A DISPLAY NAME FOR EVERY PLAYER THE INDEX KNOWS, not just the ones in
    # the universe. Keys are the site's ids now, so a key that reaches the
    # renderer without a name in this map is printed as a number — which is
    # what "best players nobody is offering" did: those players are by
    # definition neither owned nor priced, so the universe never held them.
    name = {k: (rec.get("name") or k) for k, rec in players.items()}
    universe = set(price) | {k for s in squads.values() for k in s}
    # SCORED ONCE PER PLAYER, kept rather than re-derived a few lines down
    # for `matches` — sc.score(row) was being called a second time for the
    # same row to read one more field off the same Scored.
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
        # THE SAME PAIR, ONE JORNADA LATER — see ffcore.score.Scored.pct_rest
        # for why this cannot just be `base` again. Only the START side
        # differs; a rate this thin has no more evidence about jornada 10
        # than about jornada 3, but P(start) does, once he has any
        # current-season minutes at all — see that field's own note.
        base_rest[k] = ((max(0.0, s.ppm * s.fix),
                        min(1.0, (s.pct_rest or 0) / 100))
                       if s else (2.0, 0.5))

    # Everyone the market prices, scored the same way — for the question of
    # what might come up later, which is about the players NOT in the
    # simulation's universe.
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
    # A round in progress carries only the players who have not played it yet.
    # Everyone else scored their real points hours ago and they are in
    # `carried` — see rounds_left().
    club = {k: club_key(players[k].get("team"), mkt_teams)
            for k in base if k in players}
    # HOW MANY MATCHES EACH RATE RESTS ON, handed to the forecaster so a
    # thin record widens the season it draws instead of passing as a fact.
    matches = {}
    for k in base:
        s_ = scored.get(k)
        if s_ is not None:
            matches[k] = s_.pj
    # CLUB-CORRELATED SEASON UNCERTAINTY — ffcore.fixture.club_volatility().
    # `club` above is keyed on the MARKET's own spelling (club_key's
    # canonical side); results_history.csv, and so club_volatility(), is
    # keyed on ff_slug — the exact mismatch fixture_board() already had to
    # be fixed for once, translated the same way, through ffcore.crosswalk.
    from ffcore.fixture import club_volatility, season_board
    from ffcore.tidy import load_elo, load_results_history, \
        load_understat_players
    # norm(c.market), not c.market raw: club_key()'s fallback path (used
    # above to build `club`) always returns norm(match_team(...)) — a
    # second mismatch of the same kind fixture_board() had, caught the
    # same way, by checking the real join actually landed on real data
    # rather than trusting that passing an xw through was enough.
    slug_of = {norm(c.market): c.ff_slug for c in lg.xw.clubs.values()
              if c.market and c.ff_slug} if lg.xw is not None else {}
    club_of_slug = {k: slug_of[v] for k, v in club.items() if v in slug_of}
    # ONE READ OF results_history.csv, not two — club_volatility() and
    # season_board() both want it and it cannot have changed between them.
    results_hist = load_results_history()
    club_rel = club_volatility(results_hist, list(slug_of.values()))
    # THE WHOLE REMAINING SCHEDULE, not just next — ratings fitted once
    # for `rem`, the exact jornadas about to be simulated. Same market,
    # xw, results and understat inputs ffcore.score.build() already fit
    # the "next fixture" board from, so a player's jornada-3 factor here
    # and his `s.fix` above (season_board()'s own `board_j` for rem[0])
    # agree rather than being two answers from two fits.
    # NORMALISED KEYS, matching `club`'s own convention (club_key() always
    # returns norm(...)) — season_board() itself is keyed on the market's
    # raw spelling ("Atlético"), the same as fixture_board()'s board, and
    # without this every lookup below misses silently: club_key() never
    # returns an accented, title-cased string, so `club.get(k) in board_j`
    # was false for every player, every jornada.
    sboard = {j: {norm(team): m for team, m in layer.items()}
             for j, layer in season_board(
                 _m.market, m, rem, now, load_elo(), xw=lg.xw,
                 results=results_hist,
                 understat_rows=load_understat_players("2025")).items()}
    ppm_of = {k: s.ppm for k, s in scored.items() if s}
    fc = Bootstrap(apply_fixtures(
                       next_then_rest(base, base_rest, rem, played, club),
                       sboard, club, pos, ppm_of),
                  pool=pool, matches=matches,
                  club_of=club_of_slug, club_rel=club_rel)

    # What everybody has already scored, off the league table — five rows at
    # the grain the fact belongs to, rather than the first of each manager's
    # fourteen player rows.
    # NOT the gated reader: what everyone has already scored is a history,
    # and the last reading of it is incomplete rather than wrong. The gate
    # belongs on the balance beside it, which read_api_balances applies.
    carried = {}
    for r in last_api_standings():
        if r.get("manager"):
            carried.setdefault(r["manager"], float(r.get("team_points") or 0))
    # THE SAME CASH AS EVERY OTHER MANAGER'S, out of the same estimator that
    # writes league.md — rival_cash three hundred lines up already reads it
    # this way. This used to be a second, independent read of the raw
    # api_leagues balance, and two copies of one fact in two places is how a
    # number gets corrected in one and not the other: the gate on the API
    # tables moved league.md's cash and left the headline quoting a
    # three-day-old balance from a feed everything else had refused. The
    # estimator also accrues the daily allowance since the anchor, which the
    # raw balance cannot, and states its own confidence.
    raw_cash = lg[me].cash.value or 0.0
    locked_cash = pending_sent(mkt)
    cash = raw_cash - locked_cash

    return Universe(
        state=LeagueState(squads, rem, me, carried), forecaster=fc, pos=pos,
        price=price, proceeds=proceeds, owner=owner, cash=cash, me=me,
        value=value, market_exp=market_exp, start=start, clause=clause,
        route=route,
        rival_cash=rival_cash,
        # THE RATE AND THE BEHAVIOUR, beside the level `rival_cash` already
        # holds — see days_to_afford(). `lg.txns` is the replayed ledger the
        # balances above were built from, so the two cannot describe two
        # different transaction histories.
        daily_bonus=lg.cfg.daily_bonus, tempo=rival_tempo(lg.txns),
        clause_until=clause_until, bids=bids,
        part_played=played, name=name, start_note=_calibrated()[0].note(),
        unjoined=list(unjoined_clubs) + list(lg.api_unjoined),
        locked_cash=locked_cash, received_offers=received_offers)


def _selftest() -> None:
    from ffcore.forecast import Bootstrap as B

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
    # is me_d1 at 15M) — needs two, and the widened multi-sale chain (no
    # dead weight to draw on here) reaches for weak starters instead of
    # giving up.
    big_rows = [a for a in acts3 if a.buy == "mid_big"]
    assert big_rows, acts3
    combo = next((a for a in big_rows if len(a.sell) >= 2), None)
    assert combo is not None, big_rows
    assert set(combo.sell) <= {"me_d1", "me_m1", "me_m2"}, combo
    assert combo.proceeds >= 9e6, combo   # d1+m1 = 9M, exactly what 19M needs
    assert "me_k" not in combo.sell and "me_f1" not in combo.sell, combo

    # -- best_swap_for(): the same widened chain, so a held player's own
    # band stops answering a narrower question than the main table --------
    per3b = dict(per3[1])
    per3b["riv_target"] = (7.0, 1.0)
    u3b = Universe(
        state=u3.state, forecaster=B3({1: per3b}),
        pos={**u3.pos, "riv_target": "DEF"},
        price={**u3.price, "riv_target": 14e6},
        proceeds=u3.proceeds, owner={}, cash=4e6, me="me")
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
        state=u3.state, forecaster=B3({1: per3b}),
        pos={**u3.pos, "riv_target": "DEF"},
        price={**u3.price, "riv_target": 14e6},
        proceeds={**u3.proceeds, "me_d2": 3e6}, owner={}, cash=4e6, me="me")
    exp3c = u3c.forecaster.expected(1)
    got3c = best_swap_for(u3c, "me_d2", exp3c)
    assert got3c is not None and got3c.buy == "riv_target", got3c
    assert "me_d2" in got3c.sell, got3c
    assert len(got3c.sell) > 1, got3c        # his own sale alone isn't enough
    assert "me_k" not in got3c.sell and "me_f1" not in got3c.sell, got3c
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
