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
import os
import sys
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ffcore.forecast import Bootstrap, pool_from_perjornada  # noqa: E402
from ffcore.league import api_key  # noqa: E402
from ffcore.parse import fmt_money  # noqa: E402
from ffcore.score import SLOT, _calibrated  # noqa: E402
from ffcore.text import norm  # noqa: E402
from ffcore.season import (LeagueState, best_xi,  # noqa: E402
                           simulate_many)
from ffcore.tidy import (run_now,  # noqa: E402
                         TIDY, SEASON, latest_only, load_api_market,  # noqa: E402
                         last_api_standings, load_api_teams,
                         load_players)

__all__ = ["Action", "candidates", "rank", "Universe"]

# Screening runs at a fraction of the final trial count. With common random
# numbers the RANKING settles long before the levels do, so this buys an order
# of magnitude of speed and costs only precision on options that lose anyway.
SCREEN_TRIALS = 250
FINAL_TRIALS = 3000
KEEP = 12          # how many survive screening and get the full count


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
    # HOW you would get each player: "market" if he is on the market and can
    # simply be bought, "clause" if the only route is paying his buyout. They
    # are different transactions and only one of them is a raid — calling a
    # market purchase a steal implies a denial benefit that, measured, is
    # zero, and it is what made a table of ordinary buys read as a raiding
    # plan. On 2026-08-18 every acquirable player was route "market" and not
    # one clause in the league was payable.
    route: dict[str, str] = field(default_factory=dict)
    # What each rival could spend. Estimates, and mostly negative: on the day
    # the response was modelled every one of them was overdrawn and could not
    # buy a soul until I paid one of their clauses.
    rival_cash: dict[str, float] = field(default_factory=dict)
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


def candidates(u: Universe, expected: dict[str, float],
               budget: float | None = None) -> list[Action]:
    """Every affordable move, pruned to the ones that could plausibly help.

    The prune is on EXPECTED points, not on the simulation: it is a filter for
    what to simulate, so it only has to be roughly right, and it turns
    thousands of combinations into dozens. A candidate who would not make your
    eleven on expectation will not make it on a draw either.
    """
    cash = u.cash if budget is None else budget
    mine = set(u.state.squads.get(u.me, {}))
    # The eleven the signing has to beat is one you can still pick — see
    # choosable(). `expected` may be any round; the BAR never comes off a
    # locked one.
    bar_exp = u.forecaster.expected(choosable(u)) or expected
    xi = set(best_xi(u.state.squads[u.me], bar_exp))
    # The weakest man in the current eleven is the bar a signing has to clear.
    bar = min((bar_exp.get(k, 0.0) for k in xi), default=0.0)
    spare = sorted((k for k in mine if k not in xi),
                   key=lambda k: bar_exp.get(k, 0.0))
    # DEAD WEIGHT PAYS FOR THINGS. These never make the eleven, so selling
    # them costs nothing on the pitch and the only question is what the money
    # then buys. Biggest first, so the greedy below never sells four men to do
    # the work of three.
    free = sorted(dead_weight(u), key=lambda kv: -kv[1])

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
        # Funded by a sale: only the cheapest few spares are worth trying,
        # because selling a man you field to buy one you also field is a swap
        # the simulation will price at roughly nothing.
        for s in spare[:4]:
            got = u.proceeds.get(s, 0.0)
            if price <= cash + got:
                out.append(Action(swap, buy=c, sell=s, cost=price,
                                  proceeds=got,
                                  victim=victim if raid else ""))
        # Out of reach on cash plus any ONE spare, but not out of reach: the
        # fewest dead-weight sales that cover it. One combination per target
        # rather than every subset — they are all worth the same on the pitch,
        # so the only thing to choose between them is how few men leave.
        # THE TRIGGER IS THE REAL BALANCE, not the budget. `budget` widens
        # what gets EMITTED, so the frontier can be measured off targets you
        # cannot afford; whether one sale is enough to reach a man is a fact
        # about the money you actually have. Keyed to the budget instead, an
        # unlimited one made every target reachable on cash alone and the
        # multi-sale moves stopped being generated at all — which silently
        # removed the best move on the board.
        if price > u.cash + max((u.proceeds.get(s, 0.0) for s in spare),
                                default=0.0):
            sold, got = [], 0.0
            for k, raises in free:
                if price <= u.cash + got:
                    break
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
         price=None) -> tuple:
    """Screen wide and cheap, then re-run the survivors properly.

    Returned rows carry the change in expected finishing position and in
    P(above) each rival — the second is what you act on when one rival is the
    one you are actually racing.

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
    keep = [a for _, a in screened[:KEEP]]
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
    final = _score_many(u, [u.state.squads] + afters, FINAL_TRIALS, seed)
    base, scored = final[0], final[1:]
    rivals = [m for m in u.state.squads if m != u.me]
    out = []
    for a, ans, r in zip(keep, answers, scored):
        b_ = burn(u, a)
        charge = 0.0 if (lam is None or b_ is None) else lam * b_ / 1e6
        gross = base.expected_position() - r.expected_position()
        # PAIRED, WITHIN THE SAME SEASONS. Every option runs against the
        # same seed, so for each trial there is a total with the move and a
        # total without, and the difference is the squads rather than the
        # weather. That difference is what survives a change of model: on
        # 2026-08-18 recalibrating P(start) moved a row's P(win) by 48 points
        # and its paired figures by six, which is the difference between a
        # number you can act on and one you cannot.
        mine, was = r.totals.get(u.me, []), base.totals.get(u.me, [])
        pairs = sorted(x - y for x, y in zip(mine, was))
        d_pts = pairs[len(pairs) // 2] if pairs else 0.0
        out.append({
            "action": a,
            "helps": (sum(1 for d in pairs if d > 0) / len(pairs)
                      if pairs else 0.0),
            "d_pts": d_pts,
            "pts_lo": pairs[int(0.1 * len(pairs))] if pairs else 0.0,
            "pts_hi": pairs[int(0.9 * len(pairs))] if pairs else 0.0,
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
            "value": value_rate(d_pts, a.net),
        })
    rows = sorted(out, key=lambda d: (-d["d_pos"], d["action"].net))
    return rows, base, measured


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
    # see the module docstring.
    #
    # Both sides join through ffcore.league.api_key, keyed on the market's
    # spelling like everything else. The clause is ON the api_teams row, so a
    # name that will not resolve is not a missing price — it is a rival's
    # player who silently cannot be bought at all.
    index = latest_only(lg.market.rows) if lg.market is not None else []
    price: dict[str, float] = {}
    route: dict[str, str] = {}
    for r in mkt:
        k = api_key(r["player_name"], "", lg.market, owner, index,
                    r.get("market_value"))
        if k and r.get("sale_price"):
            price[k] = float(r["sale_price"])
            route[k] = "market"
    now = run_now()
    clause_until: dict = {}
    for r in teams:
        k = api_key(r["player_name"], r["manager"], lg.market, owner, index,
                    r.get("market_value"))
        if not k:
            continue
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

    pos, base = {}, {}
    # A DISPLAY NAME FOR EVERY PLAYER THE INDEX KNOWS, not just the ones in
    # the universe. Keys are the site's ids now, so a key that reaches the
    # renderer without a name in this map is printed as a number — which is
    # what "best players nobody is offering" did: those players are by
    # definition neither owned nor priced, so the universe never held them.
    name = {k: (rec.get("name") or k) for k, rec in players.items()}
    universe = set(price) | {k for s in squads.values() for k in s}
    for k in universe:
        rec = players.get(k)
        if not rec:
            continue
        pos[k] = SLOT.get((rec.get("pos") or "").lower(), "MED")
        row = sc.row_for(k)
        s = sc.score(row) if row else None
        base[k] = ((max(0.0, s.ppm * s.fix), min(1.0, (s.pct_used or 0) / 100))
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
        row = sc.row_for(k)
        s_ = sc.score(row) if row else None
        if s_ is not None:
            matches[k] = s_.pj
    # CLUB-CORRELATED SEASON UNCERTAINTY — ffcore.fixture.club_volatility().
    # `club` above is keyed on the MARKET's own spelling (club_key's
    # canonical side); results_history.csv, and so club_volatility(), is
    # keyed on ff_slug — the exact mismatch fixture_board() already had to
    # be fixed for once, translated the same way, through ffcore.crosswalk.
    from ffcore.fixture import club_volatility
    from ffcore.tidy import load_results_history
    # norm(c.market), not c.market raw: club_key()'s fallback path (used
    # above to build `club`) always returns norm(match_team(...)) — a
    # second mismatch of the same kind fixture_board() had, caught the
    # same way, by checking the real join actually landed on real data
    # rather than trusting that passing an xw through was enough.
    slug_of = {norm(c.market): c.ff_slug for c in lg.xw.clubs.values()
              if c.market and c.ff_slug} if lg.xw is not None else {}
    club_of_slug = {k: slug_of[v] for k, v in club.items() if v in slug_of}
    club_rel = club_volatility(load_results_history(), list(slug_of.values()))
    fc = Bootstrap({j: ({k: v for k, v in base.items()
                         if club.get(k) not in played[j]}
                        if j in played else base)
                    for j in rem}, pool=pool, matches=matches,
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
    cash = lg[me].cash.value or 0.0

    return Universe(
        state=LeagueState(squads, rem, me, carried), forecaster=fc, pos=pos,
        price=price, proceeds=proceeds, owner=owner, cash=cash, me=me,
        value=value, market_exp=market_exp, start=start, clause=clause,
        route=route,
        rival_cash=rival_cash,
        clause_until=clause_until,
        part_played=played, name=name, start_note=_calibrated()[0].note(),
        unjoined=list(unjoined_clubs) + list(lg.api_unjoined))


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

    acts = candidates(u, exp)
    names = {a.buy for a in acts}
    # A player worse than the weakest man you field is not a candidate.
    assert "dud" not in names, names
    assert "star" in names, names
    # A rival's player reachable ONLY through his clause is marked a raid;
    # one sitting on the market is an ordinary purchase, whoever owns him,
    # because taking him denies nobody anything they were not already selling.
    u.route["th_m1"] = "clause"
    acts = candidates(u, exp)
    assert any(a.kind.startswith("clause") and a.buy == "th_m1"
               for a in acts), [a.kind for a in acts]
    u.route["th_m1"] = "market"
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

    rows, base, _lam = rank(u, acts)
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
    got, _, _ = rank(u2, [Action("buy", buy="free_x", cost=5e6),
                          Action("clause", buy="th_m1", cost=5e6,
                                 victim="riv")])
    by = {r["action"].buy: r["d_pos"] for r in got}
    assert by["th_m1"] > by["free_x"], by

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

    # -- value_rate: the shared primitive, on its own -----------------------
    assert value_rate(120.0, 14.13e6) is not None
    assert abs(value_rate(120.0, 14.13e6) - 120.0 / 14.13) < 1e-9
    assert value_rate(120.0, 0.0) is None       # nothing to divide by
    assert value_rate(120.0, -5e6) is None       # a net-negative "cost" is not one
    assert value_rate(None, 5e6) is None
    assert value_rate(0.0, 5e6) == 0.0           # a real price, zero return: 0, not None

    print("decide self-test OK (71 cases)")


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
    rows, base, _lam = rank(u, acts)
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
