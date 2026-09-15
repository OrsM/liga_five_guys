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
from dataclasses import InitVar, dataclass, field, replace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ffcore import forecast as _forecast  # noqa: E402
from ffcore.forecast import Bootstrap, pool_from_perjornada  # noqa: E402
import methodology as _methodology  # noqa: E402
from stats import percentile  # noqa: E402
from ffcore.crosswalk import club_key, Crosswalk  # noqa: E402
from ffcore.league import MARKET  # noqa: E402
from ffcore.parse import fmt_money  # noqa: E402
from ffcore.schedule import (rounds_left, next_then_rest,  # noqa: E402
                             first_jornada_per_player, apply_fixtures,
                             phantom_topup, phantom_fill)
from ffcore.profile import (PlayerProfile, UNSCORED_DEFAULT,  # noqa: E402
                            build_profiles)
from ffcore.score import SLOT, SLOT_MIN, _calibrated  # noqa: E402
from ffcore.text import norm  # noqa: E402
from ffcore.season import (LeagueState, XI_SIZE, best_xi,  # noqa: E402
                           simulate_many)
from ffcore.tidy import (run_now,  # noqa: E402
                         TIDY, SEASON, latest_only, load_api_market,  # noqa: E402
                         last_api_standings, load_api_offers, load_api_teams,
                         load_players, market_routes, pending_sent,
                         bought_price, pending_received)

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

    Per-player facts (pos, price, proceeds, owner, value, market_exp,
    start, clause, clause_until, route, bids, name) live on `players`
    only — a dict[str, PlayerProfile] built by ffcore.profile — and are
    exposed below as read-only attributes computed from it in
    __post_init__. There is exactly one place each fact is stored.

    pos/price/proceeds/owner/... may also be passed directly to the
    constructor (a flat dict per fact, as before) for callers that don't
    have PlayerProfile objects on hand; when `players` isn't given, these
    are used to synthesize one. decide.load() always passes `players=`.
    """
    state: LeagueState
    forecaster: Bootstrap
    cash: float
    me: str
    players: dict[str, PlayerProfile] = field(default_factory=dict)
    bought: dict[str, float] = field(default_factory=dict)
    rival_cash: dict[str, float] = field(default_factory=dict)
    part_played: dict[int, set[str]] = field(default_factory=dict)
    # {key: his own first jornada still ahead of him}, the same mapping
    # apply_fixtures() uses to place a status override — current_xi() reuses
    # it rather than a second, coarser "one jornada for everyone" guess.
    # Empty (a caller with no per-player schedule to hand) degrades to that
    # coarser reading, never an error.
    first_jornada_of: dict[str, int] = field(default_factory=dict)
    unjoined: list[str] = field(default_factory=list)
    start_note: str = ""
    cash_note: str = ""
    locked_cash: float = 0.0
    received_offers: dict[str, float] = field(default_factory=dict)

    pos: InitVar[dict | None] = None
    price: InitVar[dict | None] = None
    proceeds: InitVar[dict | None] = None
    owner: InitVar[dict | None] = None
    value: InitVar[dict | None] = None
    market_exp: InitVar[dict | None] = None
    start: InitVar[dict | None] = None
    clause: InitVar[dict | None] = None
    clause_until: InitVar[dict | None] = None
    route: InitVar[dict | None] = None
    bids: InitVar[dict | None] = None
    name: InitVar[dict | None] = None

    def __post_init__(self, pos, price, proceeds, owner, value, market_exp,
                      start, clause, clause_until, route, bids, name):
        if not self.players and any(x is not None for x in
                (pos, price, proceeds, owner, value, market_exp, start,
                 clause, clause_until, route, bids, name)):
            self.players = _synthetic_profiles(
                pos=pos, price=price, proceeds=proceeds, owner=owner,
                value=value, market_exp=market_exp, start=start,
                clause=clause, clause_until=clause_until, route=route,
                bids=bids, name=name)
        self.pos = {k: _pos_of(p.current.pos) for k, p in self.players.items()
                   if p.current.pos}
        self.price = {k: p.current.price for k, p in self.players.items()
                     if p.current.price is not None}
        self.proceeds = {k: p.current.proceeds for k, p in self.players.items()
                         if p.current.proceeds is not None}
        self.owner = {k: p.current.owner for k, p in self.players.items()
                     if p.current.owner}
        self.value = {k: p.current.value for k, p in self.players.items()
                     if p.current.value is not None}
        self.market_exp = {k: p.derived.market_exp
                           for k, p in self.players.items()
                           if p.derived.market_exp is not None}
        self.start = {k: p.derived.start_p for k, p in self.players.items()
                     if p.derived.start_p is not None}
        self.clause = {k: p.current.clause for k, p in self.players.items()
                      if p.current.clause is not None}
        self.clause_until = {k: p.current.clause_until
                             for k, p in self.players.items()
                             if p.current.clause_until is not None}
        self.route = {k: p.current.route for k, p in self.players.items()
                     if p.current.route}
        self.bids = {k: p.current.bids for k, p in self.players.items()
                    if p.current.bids is not None}
        self.name = {k: p.identity.name for k, p in self.players.items()}


def _pos_of(raw: str) -> str:
    """SLOT abbreviation (DEL/MED/DEF/POR) for a PlayerCurrent.pos value.

    Accepts either the raw source word (e.g. "DELANTERO") or an
    already-abbreviated value (passed straight through) — "MED" if
    neither matches.
    """
    mapped = SLOT.get(raw.lower())
    if mapped:
        return mapped
    return raw if raw in ("POR", "DEF", "MED", "DEL") else "MED"


def _synthetic_profiles(pos=None, price=None, proceeds=None, owner=None,
                        value=None, market_exp=None, start=None, clause=None,
                        clause_until=None, route=None, bids=None, name=None
                        ) -> dict[str, PlayerProfile]:
    """PlayerProfile per key across the given flat dicts, for constructing
    a Universe without a real ffcore.profile.build_profiles() pass.
    """
    from ffcore.crosswalk import Player
    from ffcore.profile import (PlayerCurrent, PlayerHistory,
                                PlayerDerived, PlayerProfile as _PP)
    keys = (set(pos or {}) | set(price or {}) | set(proceeds or {})
           | set(owner or {}) | set(value or {}) | set(market_exp or {})
           | set(start or {}) | set(clause or {}) | set(clause_until or {})
           | set(route or {}) | set(bids or {}) | set(name or {}))
    out = {}
    for k in keys:
        out[k] = _PP(
            identity=Player(player_id=k, name=(name or {}).get(k, k)),
            current=PlayerCurrent(
                pos=(pos or {}).get(k, ""),
                listed=k in (price or {}),
                price=(price or {}).get(k),
                proceeds=(proceeds or {}).get(k),
                owner=(owner or {}).get(k),
                value=(value or {}).get(k),
                clause=(clause or {}).get(k),
                clause_until=(clause_until or {}).get(k),
                route=(route or {}).get(k),
                bids=(bids or {}).get(k),
            ),
            history=PlayerHistory(),
            derived=PlayerDerived(
                market_exp=(market_exp or {}).get(k),
                start_p=(start or {}).get(k),
            ),
        )
    return out


def current_xi(u, who: str | None = None) -> tuple[dict[str, float], set[str]]:
    """(exp, xi) — expected points and the best legal eleven `who` (default
    u.me) could field from them, right now.

    Each player is read at HIS OWN next jornada (u.first_jornada_of), not
    one shared jornada picked for everyone — a round in progress locks some
    clubs before others, and pricing everyone off the one round where
    NOBODY has played yet would either thin the XI with players whose real
    match already happened, or (the bug this replaced) skip a suspended
    man's own next match entirely and price him as if he'd already served it.
    Falls back to the first jornada nobody has played at all when no
    per-player schedule was given (u.first_jornada_of empty) — a synthetic
    Universe with no part_played to reason about, not a second real policy.

    THE ONE COMPUTATION of "what is my/a rival's current best eleven worth" —
    seven call sites across sim.py and this module used to rebuild this pair
    by hand. `exp` is the SAME dict regardless of `who`; only the squad it's
    read against differs.
    Why: docs/notes/decide.md#current_xi--one-computation-seven-old-copies
    """
    if u.first_jornada_of:
        exp = u.forecaster.expected_own(u.first_jornada_of)
    else:
        j = next((j for j in u.state.jornadas if j not in u.part_played),
                 u.state.jornadas[0] if u.state.jornadas else 0)
        exp = u.forecaster.expected(j)
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


def route_kind(u: Universe, k: str) -> str:
    """"mine" | "free" | "raid" | "listed", from u.owner/u.route alone —
    no simulation. The one function that classifies ownership; route
    every caller through it rather than re-deriving the check.
    Why: docs/notes/decide.md#route_kind--the-one-place-ownership-is-classified
    """
    if k in u.state.squads.get(u.me, {}):
        return "mine"
    owner = u.owner.get(k)
    if not owner or owner == u.me:
        return "free"
    return "raid" if u.route.get(k, "market") == "clause" else "listed"


def _fieldable(squad: dict[str, str]) -> bool:
    """Could SOME real formation be fielded from this squad's shape?

    COUNTS ONLY — no player identities, no simulation, not even
    `best_xi()`. A real formation is (1 POR, d DEF, m MED, l DEL) for
    one of `ffcore.score.formations()`'s 7 tuples; legal when the squad
    holds at least that many of each. Clearing every position's
    SLOT_MIN and total XI_SIZE individually is NOT sufficient (e.g. two
    goalkeepers in an exactly-11 squad clears both counts but fields no
    formation) — this is the one real existence check.
    Why: docs/notes/decide.md#_fieldable--the-one-squad-legality-check
    """
    from ffcore.score import formations
    depth: dict[str, int] = {}
    for slot in squad.values():
        depth[slot] = depth.get(slot, 0) + 1
    if depth.get("POR", 0) < 1:
        return False
    return any(depth.get("DEF", 0) >= d and depth.get("MED", 0) >= m
              and depth.get("DEL", 0) >= n for d, m, n in formations())


def candidates(u: Universe, expected: dict[str, float],
               budget: float | None = None) -> list[Action]:
    """Every affordable move, pruned to the ones that could plausibly help.

    The prune is on EXPECTED points, not on the simulation: it is a filter for
    what to simulate, so it only has to be roughly right, and it turns
    thousands of combinations into dozens. A candidate who would not make your
    eleven on expectation will not make it on a draw either.

    FUNDED BY CASH, OR BY SELLING EXACTLY ONE SPARE PLAYER — deliberately
    not a multi-sale chain, which risks leaving the squad short a legal
    XI. A genuinely 2-sale-only move stops appearing; that's the trade.
    """
    cash = u.cash if budget is None else budget
    mine_squad = u.state.squads.get(u.me, {})
    mine = set(mine_squad)
    # The eleven the signing has to beat is current_xi()'s own reading — a
    # bar that comes back empty (no jornada left to pick at all) falls back
    # to the round passed in rather than a bar of zero that clears nothing.
    bar_exp, xi = current_xi(u)
    if not bar_exp:
        bar_exp = expected
        xi = set(best_xi(u.state.squads[u.me], bar_exp))
    bar = xi_bar(bar_exp, xi)
    # EVERY spare: selling him ALONE still leaves a fieldable shape — tried
    # as a funder for every target, not just a cheap-by-some-metric top few.
    # An earlier cut to the "best" 6 by raw points, then by value-per-euro,
    # each missed the same real case a different way: a good player who is
    # genuinely redundant (a second keeper, MAX_SLOT["POR"]=1) can rank
    # above enough of the squad by ANY single-number heuristic to fall
    # outside a fixed-size cut, however that heuristic is defined — the cut
    # itself was the bug, not which ranking fed it. Measured: trying every
    # spare against every target costs ~0.5s more per report (candidates()
    # generating a few hundred more Actions for rank()'s already-cheap
    # SCREEN_TRIALS pass to discard) — not the thousands-of-combinations
    # explosion a cap was guarding against.
    # Ordered by value above LEAGUE replacement PER EURO he'd raise
    # (player_forecasts()'s own PAR, a fixed league-wide baseline, over
    # value_rate()'s cost normalisation) purely so a reader scanning
    # generated Actions sees the most plausible funders first; every one
    # of them still reaches rank()'s real simulation regardless of order.
    # _fieldable() is the one hard constraint (job 1: never propose an
    # illegal squad); value-per-euro only orders the search (job 2), it
    # doesn't gate it — see the Why: below for the ad hoc carve-out this
    # replaced when the two jobs were tangled into one cutoff.
    # Why: docs/notes/decide.md#optimize-for-competent-play-warn-dont-model-for-incompetent-play
    fieldable_spare = [k for k in mine if _fieldable(
        {p: s for p, s in mine_squad.items() if p != k})]
    par_of = {k: v["par"] for k, v in player_forecasts(u).items()}

    def _spare_rank(k):
        vr = value_rate(par_of.get(k, 0.0), u.proceeds.get(k, 0.0))
        # No proceeds means nothing to fund with regardless of how little
        # he's worth keeping — ranks last, never first.
        return (vr is None, vr if vr is not None else 0.0)

    spare = sorted(fieldable_spare, key=_spare_rank)

    out: list[Action] = []
    for c, price in sorted(u.price.items(), key=lambda kv: kv[1]):
        if c in mine or bar_exp.get(c, 0.0) <= bar:
            continue
        # Never propose a listed target (owned, not a clause) as a move —
        # 0/119 real deals in this league have ever been a rival's own
        # choice to sell to a manager. route_kind() is the one place this
        # gets decided.
        kind_ = route_kind(u, c)
        if kind_ == "listed":
            continue
        raid = kind_ == "raid"
        victim = u.owner.get(c, "") if raid else ""
        kind = "clause" if raid else "buy"
        swap = kind + "-swap" if raid else "swap"
        if price <= cash:
            out.append(Action(kind, buy=c, cost=price, victim=victim))
        # Funded by a sale: every spare is tried (spare's own docstring
        # above), worst-value-per-euro first.
        for s in spare:
            got = u.proceeds.get(s, 0.0)
            if price <= cash + got:
                out.append(Action(swap, buy=c, sell=s, cost=price,
                                  proceeds=got, victim=victim))
    return out


def locked(until: dict, key: str, now) -> bool:
    """Is his clause unpayable right now?

    A TRANSFER LOCKS A CLAUSE until the app's own reopen date. A MISSING
    DATE COUNTS AS LOCKED, not available — an absent field is not
    evidence a clause is payable. A free agent has no clause and never
    reaches here.
    """
    when = until.get(key)
    return True if when is None else when > now


def burn(u, a: Action) -> float | None:
    """Wealth a move destroys: what it costs, less what you end up holding.

    A FREE AGENT BURNS NOTHING — market value in, market value out. A
    BUYOUT CLAUSE BURNS THE PREMIUM over market value, gone for good.
    Never negative; None when the value is unknown (never assume zero
    premium).
    """
    if not a.buy:
        return 0.0
    val = u.value.get(a.buy)
    if val is None:
        return None
    return max(0.0, a.cost - val)


def cash_price(reach) -> float | None:
    """Places per million, measured off `reach` = [(extra cash needed, Δpos)].

    Read off what more money would actually buy today, not a rate card —
    every target is screened, not only the affordable ones. The curve is
    a STAIRCASE (flat until the balance clears the next better price,
    then a step), so averaged over the reachable range it comes out
    small; that's the honest answer, not a defect.

    None when there is nothing to measure against. Never zero for that —
    zero is a real answer ("more money buys nothing"), distinct from
    unknown.
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


def respond(u, a: Action, rate: float | None) -> float:
    """Season points the manager you just paid buys back, on AVERAGE, or 0.0.

    A CLAUSE PAYS THE OWNER, not the app — a steal hands the victim cash
    he can reinvest, so scoring the steal without crediting that
    overstates it.

    THE AVERAGE HE BUYS, NOT THE BEST HE COULD FIND — `rate` is
    `rank()`'s own `lam` (points per million, same screening pass
    pricing everything else this run), never a search for the single
    best clausable replacement: an unbounded search can walk off with a
    THIRD manager's own player to fund the response, breaking a squad
    that was never part of the trade. An average touches only the two
    sides of the actual trade, mirroring `value_rate()`'s own
    replacement-level logic on the buy side.

    0.0 when there is nothing to spend (no victim, or a market purchase
    — money paid to the app leaves the league, money paid for a clause
    changes sides) or nothing is known to spend it at (`rate` is None).
    """
    if not a.victim or a.victim == u.me or not rate:
        return 0.0
    budget = max(0.0, u.rival_cash.get(a.victim, 0.0)) + a.cost
    return rate * budget / 1e6


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


def overdraft_fix(u: Universe) -> tuple[list[tuple[str, float]], float]:
    """([(player, proceeds)] to sell, still-short amount) to clear an
    overdraft — [], 0.0 when not overdrawn at all.

    DEAD WEIGHT ONLY, DELIBERATELY — never a real starter, matching
    candidates()'s own refusal of multi-sale funding chains. Reaches
    only into dead_weight() (proceeds with zero points cost), and
    re-checks _fieldable() on the cumulative result after every sale,
    stopping rather than pushing through an unsafe one. A shortfall dead
    weight alone can't cover is returned honestly (second element > 0),
    never covered by selling a real starter.
    """
    if u.cash >= 0:
        return [], 0.0
    need = -u.cash
    mine = dict(u.state.squads.get(u.me, {}))
    picked: list[tuple[str, float]] = []
    raised = 0.0
    for k, proceeds in dead_weight(u):
        if raised >= need:
            break
        trial = {p: s for p, s in mine.items() if p != k}
        if not _fieldable(trial):
            continue
        mine = trial
        picked.append((k, proceeds))
        raised += proceeds
    return picked, max(0.0, need - raised)


def apply(u: Universe, a: Action) -> dict[str, dict[str, str]]:
    """The squads as they would be after `a`. Pure — nothing is mutated.

    TOPPED UP TO SLOT_MIN, WHOEVER THE TRADE LEAVES SHORT — not just
    mine. A raid's victim never agreed to sell and gets no pre-check;
    left short, his simulated season scores zero every remaining
    jornada (no legal XI). See phantom_topup() for how this patches it
    without inventing new forecaster data.
    """
    sq = {m: dict(s) for m, s in u.state.squads.items()}
    for gone in a.sell:
        sq[u.me].pop(gone, None)
    if a.buy:
        # A steal removes him from his owner. This is the whole point.
        for m in sq:
            sq[m].pop(a.buy, None)
        sq[u.me][a.buy] = u.pos.get(a.buy, "MED")
    return {m: phantom_topup(s) for m, s in sq.items()}




def _score_many(u: Universe, many: list, trials: int, seed: int):
    """Every candidate squad against ONE set of seasons. Same numbers."""
    return simulate_many(
        [LeagueState(squads=sq, jornadas=u.state.jornadas, me=u.me,
                     carried=u.state.carried) for sq in many],
        u.forecaster, trials=trials, seed=seed)


def paired(after, base, me) -> list[float]:
    """The per-trial difference `after` minus `base`, sorted.

    PAIRED, WITHIN THE SAME SEASONS — trial n with the move against
    trial n without it, so the difference is the squads, not the
    weather. Sorted because every reader is a quantile of it (band()
    below, rank()'s "helps"). Empty when the two Standings disagree on
    trial count (zip() makes this silent) — callers treat empty as "no
    answer", not zero.
    """
    return sorted(x - y for x, y in zip(after.totals.get(me, []),
                                        base.totals.get(me, [])))


def band(pairs) -> tuple[float, float, float]:
    """(median, 10th, 90th) of paired()'s output — ONE definition of a
    band, shared by rank() and sim.ladder_rows() so the two cannot
    disagree about what "the band" means. Uses stats.percentile()
    rather than hand-indexing the sorted list. (0, 0, 0) for no pairs —
    nothing simulated, no spread to report.
    """
    if not pairs:
        return (0.0, 0.0, 0.0)
    return (percentile(pairs, 50), percentile(pairs, 10),
            percentile(pairs, 90))


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


def player_forecasts(u: Universe) -> dict[str, dict]:
    """{key: {"season_pts", "next_pts", "par", "pj"}} for every player in
    u.players — the full pool, not gated on being listed (see slate.py
    for the market-restricted report view).

    TWO-TIER: u.forecaster is only simulated for the 89-player universe
    (five squads + market candidates), so season_pts sums
    forecaster.expected(j) per remaining jornada for those, and falls
    back to u.market_exp[k] held flat across the same count for
    everyone else — a cruder approximation, flagged via "simulated".

    "par" is ffcore.score.vor() against the LEAGUE's own replacement level
    (the score of the last man the league can start at that slot, pooled
    across every squad plus market candidates) — not "MY squad's weakest
    option", which answers "does he play Saturday" and goes stale the
    moment a transfer changes what my own weakest option is. Restored
    2026-09-13: this used to be exactly that squad-relative stand-in,
    which score.py's own replacement()/vor() (a fixed, league-wide
    baseline) were built to replace but got deleted with an old report
    format on 2026-08-19 and never re-wired here.
    Why: docs/notes/score.md#replacement-level--why-not-λ
    """
    from ffcore.score import replacement as _replacement, squad_pool, vor

    jornadas = u.state.jornadas
    n_rem = len(jornadas)
    sim_season: dict[str, float] = {}
    sim_next: dict[str, float] = {}
    for i, j in enumerate(jornadas):
        exp = u.forecaster.expected(j)
        for k, pts in exp.items():
            sim_season[k] = sim_season.get(k, 0.0) + pts
        if i == 0:
            sim_next = exp

    # Pooled across every squad plus market candidates — a rival's player
    # still occupies one of the league's starting slots, which is exactly
    # what makes a position scarce.
    wide_pool = squad_pool(
        {"key": k, "slot": u.pos.get(k, ""), "score": pts}
        for k, pts in sim_season.items() if u.pos.get(k))
    repl = _replacement(wide_pool, len(u.state.squads)) if u.state.squads \
        else {}

    out = {}
    for k, p in u.players.items():
        in_sim = k in sim_season
        season_pts = sim_season.get(k) if in_sim \
            else u.market_exp.get(k, 0.0) * n_rem
        next_pts = sim_next.get(k) if in_sim else u.market_exp.get(k, 0.0)
        slot = u.pos.get(k)
        out[k] = {
            "season_pts": season_pts,
            "next_pts": next_pts,
            "par": vor({"slot": slot, "score": season_pts}, repl),
            "pj": p.derived.pj,
            "simulated": in_sim,
        }
    return out


def rank(u: Universe, acts: list[Action], seed: int = 1,
         price=None, extra: list[tuple[str, Action]] = ()) -> tuple:
    """Screen wide and cheap, then re-run the survivors properly.

    Returns `(rows, base, measured, bands)`. Rows carry the change in
    expected finishing position and in P(above) each rival.

    `extra` is `[(key, Action), ...]`, scored in the SAME final pass (a
    second pass would redraw the same seasons for nothing) and returned
    as `bands`, `{key: (median, lo, hi, action)}` — `key` given
    explicitly since a pure-sale Action can't say by itself which side
    a caller meant. A `key` already answered by a real BUY row is
    dropped from `extra` — its own band answers better than a bare swap.

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
        # POINTS, not an expected-position swing — reads `.totals`
        # directly rather than paying for expected_position()'s extra
        # per-trial rival comparison.
        d, _lo, _hi = band(paired(r, base_s, u.me))
        reach.append((a.cost - a.proceeds - u.cash, d))
        if a.cost <= u.cash + a.proceeds:
            screened.append((d, a))
    measured = cash_price(reach)
    lam = price if price is not None else measured

    # One row per target: 4 funding variants of one signing screen
    # identically ONLY WHEN the sale is real dead weight — "selling dead
    # weight changes nothing on the pitch" is false the moment the spare
    # sold is a man in the eleven you'd actually field right now, and
    # SCREEN_TRIALS is too few draws to trust a close margin between "sell
    # my starter" and "sell my bench" (measured: the ranking between them
    # flips run to run at this trial count). A funding variant that keeps
    # today's XI intact is preferred outright over one that doesn't,
    # before points are compared at all; ties within a tier still break on
    # points, then spend. This can't be skipped by spending more trials
    # here — the fix is not asking the noisy number to settle a question
    # it was never precise enough to answer.
    # Why: docs/notes/decide.md#rank--funding-variant-noise
    _, cur_xi = current_xi(u)
    def _touches_xi(a) -> bool:
        return any(s in cur_xi for s in a.sell)

    pick: dict[str, tuple] = {}
    for d, a in screened:
        k = a.buy or a.sell
        cur = pick.get(k)
        key = (not _touches_xi(a), d, -a.net)
        if cur is None or key > (not _touches_xi(cur[1]), cur[0], -cur[1].net):
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
    # A clause pays the owner, who can respond with that money — not a
    # pure subtraction. Computed off `lam` rather than inside the
    # per-candidate squad; `afters` never differs from a plain apply().
    bonuses = [respond(u, a, lam) for a in keep]
    afters = [apply(u, a) for a in keep]
    # Anything `extra` asks about a player already answered by a real
    # ranked row is dropped. Buy side only, deliberately — the sell side
    # can be the funder of an unrelated top move.
    answered = {a.buy for a in keep if a.buy}
    rest = [(k, a) for k, a in extra if k not in answered]
    final = _score_many(u, [u.state.squads] + afters
                        + [apply(u, a) for _k, a in rest], FINAL_TRIALS, seed)
    base, scored = final[0], final[1:len(afters) + 1]
    # THE VICTIM'S REPLY LANDS ON HIS OWN TOTAL, AFTER THE DRAW — a flat
    # points add, same in every trial, standing in for money spent at the
    # going average rate over the season rather than one simulated transfer.
    # No squad is touched, so no third manager can be caught in it.
    for a, r, bonus in zip(keep, scored, bonuses):
        if bonus and a.victim in r.totals:
            r.totals[a.victim] = [x + bonus for x in r.totals[a.victim]]
    bands = {k: (*band(paired(r, base, u.me)), a)
            for (k, a), r in zip(rest, final[len(afters) + 1:])}
    rivals = [m for m in u.state.squads if m != u.me]
    out = []
    for a, r in zip(keep, scored):
        b_ = burn(u, a)
        charge = 0.0 if (lam is None or b_ is None) else lam * b_ / 1e6
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
            # THE ONE headline number now: season points, net of what the
            # cash burned could otherwise have bought (`charge`, itself in
            # points per rank()'s own `lam` — see cash_price()). Used to be
            # expected-position swing (`d_pos`/`gross`, via
            # Standings.expected_position()) — dropped 2026-09-12, Miguel:
            # "P_win is an output of points, we should focus on points."
            # Position/win swings are still shown (`d_win`/`d_beat` below)
            # but no longer decide the order or the eligibility bar.
            "net_pts": d_pts - charge,
            "burn": b_,
            "charge": charge,
            # No specific reply to name — payload()/the ladder treat
            # None as "no answer to show".
            "answer": None,
            "d_win": r.position().get(1, 0.0) - base.position().get(1, 0.0),
            "d_beat": {v: r.beat(v) - base.beat(v) for v in rivals},
            "mean": r.mean(u.me),
            # Season points per million actually paid, only defined for
            # a genuine spend (net > 0). Already points-over-replacement
            # since `d_pts` is a paired marginal whose "with" side
            # re-picks best_xi() over every legal shape.
            # Why: docs/notes/decide.md#rank--screening-top-up-and-value
            "value": value_rate(d_pts, a.net),
        })
    rows = sorted(out, key=lambda d: (-d["net_pts"], d["action"].net))
    return rows, base, measured, bands


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


_LOAD_CACHE: Universe | None = None


def load(trials_pool=None) -> Universe:
    """Assemble the universe from the store. The only IO in this module.

    MEMOIZED FOR THE PROCESS — pure w.r.t. the tidy store (`trials_pool`
    is accepted but unused), and a store read can't change mid-run, so
    every stage in run.py's single-interpreter chain shares one call.
    Why: docs/notes/decide.md#load--memoized-for-the-process
    """
    global _LOAD_CACHE
    if _LOAD_CACHE is not None:
        return _LOAD_CACHE
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
    # split. Both sides join through Crosswalk.resolve_api() on the market's
    # spelling; a clause on an unresolvable name is a rival's player who
    # silently cannot be bought at all.
    xw = lg.xw or Crosswalk()
    index = latest_only(lg.market.rows) if lg.market is not None else []
    price, route, bids = market_routes(
        mkt, lambda r: xw.resolve_api(r["player_name"], "", lg.market, owner,
                                      index, r.get("market_value")))
    now = run_now()
    clause_until: dict = {}
    # The app's own ownership-record id -> this repo's key, built in the
    # one loop that already resolves a key for every api_teams row rather
    # than re-resolving the same rows a second time for one more field.
    pt_to_key: dict[str, str] = {}
    for r in teams:
        k = xw.resolve_api(r["player_name"], r["manager"], lg.market, owner,
                           index, r.get("market_value"))
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
        k = xw.resolve_api(r["player_name"], r["manager"], lg.market, owner,
                           index, r.get("market_value"))
        if k:
            clause.setdefault(k, float(r["buyout"]))
    rival_cash = {h: (lg[h].cash.value or 0.0) for h in lg.managers
                  if h != me}
    # What the app says everyone is worth — the figure a sale pays out at,
    # see burn() for the gap between this and a buyout clause.
    value = {k: float((v or {}).get("value") or 0) for k, v in players.items()
             if (v or {}).get("value")}

    # A display name for every player the index knows, not just those in
    # the universe — a key with no name here prints as a raw number.
    name = {k: (rec.get("name") or k) for k, rec in players.items()}
    universe = set(price) | {k for s in squads.values() for k in s}

    # ONE profile per player, the full pool, no market/ownership gate.
    # `market_keyed` carries every market/ownership/ledger fact already
    # computed above so build_profiles() doesn't re-derive any of it —
    # PlayerCurrent is the one place that holds the result.
    # The season's own file, found by globbing rather than a hardcoded
    # label — a hardcoded "2026-27" here once meant this would silently
    # start reading nothing (or crash outright) the moment the season
    # rolled over, the same bug scout.py's own hand-rolled reader had.
    # methodology.load_actuals() derives the label the same way.
    _pj_files = sorted((SEASON / "live").glob("perjornada_*.csv"))
    perjornada_rows = (list(csv.DictReader(open(_pj_files[-1])))
                       if _pj_files else [])
    # Real per-match data (mins played, goals, cards) for whichever ~118
    # players have been on one of this league's 5 squads — api_teams's
    # embedded lastStats, not a full-pool source (see
    # ffcore.profile._match_stats_history's own docstring for why not).
    stats_path = TIDY / "api_stats.csv"
    match_stats_rows = (list(csv.DictReader(open(stats_path)))
                        if stats_path.exists() else [])
    mk_keys = (set(price) | set(owner) | set(value) | set(clause)
              | set(clause_until) | set(route) | set(bids) | set(proceeds))
    market_keyed = {k: {"listed": k in price, "price": price.get(k),
                        "owner": owner.get(k), "value": value.get(k),
                        "clause": clause.get(k),
                        "clause_until": clause_until.get(k),
                        "route": route.get(k), "bids": bids.get(k),
                        "proceeds": proceeds.get(k)}
                    for k in mk_keys}
    profiles = build_profiles(players, sc, perjornada_rows, xw=lg.xw,
                              match_stats_rows=match_stats_rows,
                              match_rows=m,
                              market_keyed=market_keyed)

    # apply_fixtures() below needs `pos` as a plain arg; Universe computes
    # its own copy from `players=profiles` (see _pos_of()).
    pos = {k: _pos_of(p.current.pos) for k, p in profiles.items()}

    base, base_rest = {}, {}
    # Scored once per player, kept rather than re-derived for `matches`.
    scored: dict[str, object] = {}
    for k in universe:
        p = profiles.get(k)
        scored[k] = p.derived.scored if p else None
        # to_bootstrap_input() owns the (pts, p_start) reshaping — same
        # points side both jornada views, only the start side differs (a
        # rate this thin has no more evidence by jornada 10 than jornada 3,
        # but P(start) does once he has current-season minutes) — and the
        # neutral (2.0, 0.5) default for an unscored player, in one place
        # instead of duplicated inline here.
        base[k], base_rest[k] = (p.to_bootstrap_input() if p
                                 else (UNSCORED_DEFAULT, UNSCORED_DEFAULT))

    pool = pool_from_perjornada(perjornada_rows)
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
    from ffcore import fixture as _fixture
    from ffcore.fixture import club_volatility, fit_home_edge, season_board
    from ffcore.tidy import load_elo, load_results_history, \
        load_understat_players
    slug_of = {norm(c.market): c.ff_slug for c in lg.xw.clubs.values()
              if c.market and c.ff_slug} if lg.xw is not None else {}
    club_of_slug = {k: slug_of[v] for k, v in club.items() if v in slug_of}
    # One read of results_history.csv — club_volatility() and season_board()
    # both want it and it can't have changed between them.
    results_hist = load_results_history()
    club_rel = club_volatility(results_hist, list(slug_of.values()))
    # Mutates the module attribute BEFORE season_board() builds this
    # run's Match objects — _match_for() reads HOME_EDGE live off the
    # module each call, so this must land before the board, not after.
    _fixture.HOME_EDGE, _home_edge_why = fit_home_edge(results_hist, m)
    # The whole remaining schedule, fitted once for `rem`. Keys normalised
    # to match `club`'s own convention (club_key() always returns norm(...))
    # — season_board() itself is keyed on the market's raw spelling.
    sboard = {j: {norm(team): m for team, m in layer.items()}
             for j, layer in season_board(
                 _m.market, m, rem, now, load_elo(), xw=lg.xw,
                 results=results_hist,
                 understat_rows=load_understat_players("2025")).items()}
    ppm_of = {k: s.ppm for k, s in scored.items() if s}
    status_of = {k: s.status for k, s in scored.items() if s}
    first_jornada_of = first_jornada_per_player(base, rem, played, club)
    per_j = apply_fixtures(
        next_then_rest(base, base_rest, rem, played, club),
        sboard, club, pos, ppm_of, status_of=status_of,
        first_jornada_of=first_jornada_of)
    # A squad short a position can't be simulated at all (see phantom_fill())
    # — patched once here so every downstream reader gets the same fix.
    squads, per_j = phantom_fill(squads, per_j, pos)
    # phantom_fill() clears every position's SLOT_MIN, which is NOT the
    # same guarantee as a real formation existing (two keepers in an
    # exactly-11 squad clears both counts, fields nothing — _fieldable()'s
    # own docstring) — asserted, not warned: this is a hard invariant of
    # phantom_fill() itself, not a real-world state (a rival's squad) we
    # optimize around or warn the user about.
    # Why: docs/notes/decide.md#optimize-for-competent-play-warn-dont-model-for-incompetent-play
    for _m, _sq in squads.items():
        assert _fieldable(_sq), (_m, _sq)
    # Phantoms are synthetic averages with no real fixture of their own —
    # available from the first remaining jornada, same as current_xi()'s
    # fallback reading for a player nothing else says otherwise about.
    if rem:
        phantom_keys = {k for layer in per_j.values() for k in layer
                        if k.startswith("__phantom_")}
        for k in phantom_keys:
            first_jornada_of.setdefault(k, rem[0])
    # Mutates the module attribute, not a local — rate_draw()/start_draw()
    # re-import DRIFT_FRAC fresh from the module on every call.
    _forecast.DRIFT_FRAC, _drift_why = _methodology.drift_frac_from_history()
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

    _LOAD_CACHE = Universe(
        state=LeagueState(squads, rem, me, carried), forecaster=fc,
        cash=cash, me=me, players=profiles,
        rival_cash=rival_cash,
        part_played=played, first_jornada_of=first_jornada_of,
        start_note=_calibrated()[0].note(),
        unjoined=list(unjoined_clubs) + list(lg.api_unjoined),
        locked_cash=locked_cash, received_offers=received_offers,
        bought=bought_price(lg.txns, lg.xw))
    return _LOAD_CACHE


def _selftest() -> None:
    from ffcore.forecast import Bootstrap as B

    # phantom_fill()/phantom_topup() themselves are ffcore.schedule's own
    # self-test now (split out 2026-09-15) — this fixture is the minimal
    # shape `apply()`'s own test below still needs.
    ph_pos = {"d1": "DEF", "d2": "DEF", "other_def": "DEF",
              "x1": "MED", "x2": "MED", "x3": "MED", "p1": "POR", "f1": "DEL"}

    # -- apply(): the raid's real victim is topped up too, not just mine ---
    # 2026-09-09, auditing a report recommendation: `apply()` moves a
    # player off a real owner with nothing checking he still clears
    # SLOT_MIN — 5 of that day's 119 real raid candidates would have left
    # the victim short.
    thin_riv = {"d1": "DEF", "d2": "DEF", "star": "DEF",  # exactly SLOT_MIN=3
               "x1": "MED", "x2": "MED", "x3": "MED", "p1": "POR", "f1": "DEL"}
    u_thin = Universe(
        state=LeagueState({"me": {}, "riv": dict(thin_riv)}, [1], "me"),
        forecaster=B({1: {}}), pos={**ph_pos, "star": "DEF"}, price={},
        proceeds={}, owner={"star": "riv"}, cash=0.0, me="me")
    # He has exactly SLOT_MIN=3 DEF; raiding one of them (not his spare) —
    raided = apply(u_thin, Action("steal", buy="star", cost=1e6,
                                  victim="riv"))
    riv_after = raided["riv"]
    assert "star" not in riv_after, riv_after
    def_count = sum(1 for s in riv_after.values() if s == "DEF")
    assert def_count == 3, riv_after           # 2 real + 1 phantom, not 2
    assert any(k.startswith("__phantom_DEF_") for k in riv_after), riv_after

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
    # No per-player schedule given: falls back to the first jornada nobody
    # has played at all.
    fallback_j = next((j for j in u.state.jornadas if j not in u.part_played),
                      u.state.jornadas[0] if u.state.jornadas else 0)
    assert cxi_exp == u.forecaster.expected(fallback_j), cxi_exp
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

    # -- overdraft_fix(): dead weight only, never a multi-sale chain into
    # real starters — the exact bug class candidates() itself already
    # refuses (2026-09-06, Miguel: cash management going silent while
    # overdrawn) ------------------------------------------------------
    assert overdraft_fix(u) == ([], 0.0), "not overdrawn: nothing to fix"
    # me_bench (proceeds 8e6) is real dead weight in THIS fixture — never
    # in cxi, confirmed above. A small overdraft it alone covers:
    u_small = replace(u, cash=-3e6)
    sells, short = overdraft_fix(u_small)
    assert sells == [("me_bench", 8e6)] and short == 0.0, (sells, short)
    # A bigger overdraft than any dead weight raises: covers what it can,
    # says honestly how much is still short — never reaches for a real
    # starter to close the gap.
    u_big = replace(u, cash=-50e6)
    sells2, short2 = overdraft_fix(u_big)
    assert sells2 == [("me_bench", 8e6)], sells2
    assert short2 == 50e6 - 8e6, short2
    # A rival's player reachable ONLY through his clause is marked a raid;
    # one he has LISTED himself is never proposed at all — 0/119 real deals
    # in this league have ever been a rival's own choice to sell to a
    # manager, and Miguel has repeatedly asked that the report never
    # emphasize a rival-owned player unless it's a raid he cannot refuse.
    # Why: docs/notes/decide.md#candidates--listed-targets-are-never-proposed
    u.route["th_m1"] = "clause"
    acts = candidates(u, exp)
    assert any(a.kind.startswith("clause") and a.buy == "th_m1"
               for a in acts), [a.kind for a in acts]
    u.route["th_m1"] = "listed"
    listed = candidates(u, exp)
    assert not any(a.buy == "th_m1" for a in listed), \
        [a.kind for a in listed if a.buy == "th_m1"]
    u.route["th_m1"] = "clause"
    acts = candidates(u, exp)
    assert all(a.cost <= u.cash + a.proceeds for a in acts), acts

    # apply() is pure and a steal takes him OFF the rival.
    a = next(x for x in acts if x.buy == "th_m1" and not x.sell)
    after = apply(u, a)
    assert "th_m1" not in after["riv"], after["riv"]
    assert "th_m1" in after["me"]
    assert "th_m1" in u.state.squads["riv"], "apply must not mutate"

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

    # -- a move needing TWO OR MORE sales never appears (2026-09-06) -------
    # Funding chains (2+ sales for one buy) were cut outright, not
    # re-patched, after being the direct cause of two catastrophic squad-
    # legality bugs (`_fieldable`'s own docstring). A target only reachable
    # by selling more than one man simply does not appear on the table —
    # the honest cost of the simpler, safer design — checked here so a
    # future change can't quietly bring the chain back.
    u3 = Universe(
        state=LeagueState({"me": dict(mine), "riv": dict(theirs)}, [1], "me"),
        forecaster=B(per), pos={**u.pos, "dear": "MED"},
        price={"dear": 20e6},
        proceeds={"me_bench": 8e6, "me_spare2": 5e6, "me_spare3": 4e6},
        owner={}, cash=4e6, me="me")
    u3.state.squads["me"]["me_spare2"] = "MED"
    u3.state.squads["me"]["me_spare3"] = "POR"
    per3 = {1: dict(per[1])}
    per3[1].update({"dear": (11.0, 1.0), "me_spare2": (0.4, 1.0),
                    "me_spare3": (0.3, 1.0)})
    u3.forecaster = B(per3)
    acts3 = candidates(u3, u3.forecaster.expected(1))
    assert not any(a.buy == "dear" and len(a.sell) > 1 for a in acts3), \
        [a for a in acts3 if a.buy == "dear"]
    # Not reachable on cash (4M) + any ONE spare (best is 8M) either —
    # 20M needs at least two, so "dear" is simply absent, not present
    # with a wrong sale count.
    assert not any(a.buy == "dear" for a in acts3), acts3

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
    # Signing a 12-point player into an eleven of 3s must gain season points,
    # and the table must be sorted by that.
    assert top["net_pts"] > 0, top
    assert [r["net_pts"] for r in rows] == sorted(
        (r["net_pts"] for r in rows), reverse=True)
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

    # `value` is already points over position replacement level: `d_pts` is a
    # paired marginal off a re-picked best_xi(), so two candidates on
    # identical expected points and price, one into a thin slot and one into
    # a deep one, do NOT come out equal — no separate value_vor needed.
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

    # A STEAL AND AN EQUIVALENT FREE AGENT NOW TIE ON net_pts, DELIBERATELY.
    # Ranking used to favour the clause buy here because it moved a RIVAL's
    # total too (expected_position() is a competitive, all-managers metric);
    # net_pts only ever reads `me`'s own paired total (see paired()), so a
    # steal's rival-denial value no longer earns a ranking bonus. Miguel,
    # 2026-09-12, asked directly and chose this: "drop it — points only."
    # d_win/d_beat still SHOW the rival-denial effect on the row: it's
    # visible, just not part of what decides order any more.
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
    by = {r["action"].buy: r["net_pts"] for r in got}
    assert abs(by["th_m1"] - by["free_x"]) < 1e-9, by
    # But the rival-denial effect is still THERE, just not decisive: only
    # the clause buy moves a rival's own beat-probability.
    d_beat = {r["action"].buy: r["d_beat"]["riv"] for r in got}
    assert d_beat["th_m1"] > d_beat["free_x"] > 0.0, d_beat

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
        key = "eff%d" % i   # ratio. Rate 6.0, well clear of the ~3.0 bar —
        per6[1][key] = (6.0, 1.0)   # a thin margin here is flaky across RNG
        acts6.append(Action("buy", buy=key, cost=1e4))  # backends (numpy vs.
        # pure-Python fallback), so this stays wide and unambiguous.
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
    # A clause pays the owner, so a steal hands the victim money to retaliate
    # with — not pure subtraction from a rival who can't respond.
    #
    # respond() uses average value for his money, not a search for his single
    # best reply — a search has no floor on what it costs whoever he takes
    # FROM, and can walk off with an unrelated third manager's own player,
    # collapsing that manager's season instead of anything the raid itself did.
    u5 = Universe(
        state=LeagueState({"me": dict(mine), "riv": {}}, [1], "me"),
        forecaster=B(per), pos=dict(u.pos), price={},
        proceeds={}, owner={}, cash=12e6, me="me", rival_cash={"riv": 4e6})
    steal = Action("steal", buy="th_m1", cost=10e6, victim="riv")
    # 3 points per million, from wherever `rank()` measured it today — his
    # reply is worth that rate against his OWN money: the 4M he already had
    # plus the 10M clause just paid him, times the rate, in points.
    assert respond(u5, steal, 3.0) == 3.0 * (4e6 + 10e6) / 1e6
    # No victim, no reply — a market purchase leaves the league, it does not
    # change hands, so nobody answers it.
    assert respond(u5, Action("buy", buy="star", cost=1e6), 3.0) == 0.0
    # Nothing known about the going rate is not a free reply either — the
    # same reading `charge` gives `lam is None` in rank() itself.
    assert respond(u5, steal, None) == 0.0
    # And a real rate against no money and no payout is genuinely worth
    # nothing, not an error.
    broke = replace(u5, rival_cash={"riv": 0.0})
    assert respond(broke, Action("steal", buy="th_m1", cost=0.0,
                                 victim="riv"), 3.0) == 0.0

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

    # -- _fieldable: the one squad-legality check, counts only -------------
    # A real 4-4-2 shape: POR1/DEF4/MED4/DEL2.
    ok_squad = {"k": "POR", "d1": "DEF", "d2": "DEF", "d3": "DEF",
               "d4": "DEF", "m1": "MED", "m2": "MED", "m3": "MED",
               "m4": "MED", "f1": "DEL", "f2": "DEL"}
    assert _fieldable(ok_squad), ok_squad
    # No goalkeeper at all: illegal regardless of everything else.
    assert not _fieldable({k: v for k, v in ok_squad.items() if k != "k"})
    # THE EXACT BUG: two goalkeepers, POR2/DEF4/MED4/DEL1 (11 total) — every
    # position clears SLOT_MIN, total is XI_SIZE, and it's still illegal —
    # no real formation fields two keepers. This is the shape that read
    # Season -1282 (Ali Houary) and -1286 (Alvaro Mantilla) on real reports
    # before `_fieldable()` replaced `_safe_to_sell()`'s bounds heuristic.
    two_keepers = {k: v for k, v in ok_squad.items() if k != "f2"}
    two_keepers["k2"] = "POR"
    assert not _fieldable(two_keepers), two_keepers
    # Extra bench depth doesn't matter — legality only asks whether ENOUGH
    # exists per position, never "too much".
    assert _fieldable({**ok_squad, "d5": "DEF", "m5": "MED"})

    # -- candidates(): funded by cash or exactly ONE sale, never a chain ---
    # A REAL-SIZED squad (12: 1 spare beyond the 11 a 4-4-2 needs) — selling
    # anyone from an exactly-11 squad drops it below XI_SIZE, illegal by
    # construction, so a genuine spare needs headroom above 11 to exist at
    # all (the same fact phantom_fill()/load() guarantee for a real report).
    per_cd = {1: {"me_k": (2.0, 1.0), "me_d1": (3.0, 1.0), "me_d2": (3.0, 1.0),
                "me_d3": (3.0, 1.0), "me_d4": (3.0, 1.0),
                "me_m1": (3.0, 1.0), "me_m2": (3.0, 1.0), "me_m3": (3.0, 1.0),
                "me_m4": (3.0, 1.0), "me_f1": (5.0, 1.0), "me_f2": (4.0, 1.0),
                "me_f3": (0.1, 1.0),   # weakest DEL, the real spare
                "target": (9.0, 1.0)}}
    sq_cd = {"me_k": "POR", "me_d1": "DEF", "me_d2": "DEF", "me_d3": "DEF",
           "me_d4": "DEF", "me_m1": "MED", "me_m2": "MED", "me_m3": "MED",
           "me_m4": "MED", "me_f1": "DEL", "me_f2": "DEL", "me_f3": "DEL"}
    from ffcore.forecast import Bootstrap as BCD
    u_cd = Universe(
        state=LeagueState({"me": dict(sq_cd)}, [1], "me"),
        forecaster=BCD(per_cd), pos={**sq_cd, "target": "DEL"},
        price={"target": 5e6}, proceeds={"me_f3": 5e6}, owner={},
        cash=0.0, me="me")
    exp_cd = u_cd.forecaster.expected(1)
    acts_cd = candidates(u_cd, exp_cd)
    # Reachable by selling the one real spare (me_f3, 5M) alone — the
    # squad holds 12, one more than a 4-4-2's 11, so this sale still
    # leaves a legal shape.
    assert any(a.buy == "target" and a.sell == ("me_f3",) for a in acts_cd), \
        acts_cd
    # me_k (the only POR) is never offered as a spare — selling him leaves
    # no goalkeeper at all, illegal on its own, no chain needed to see it.
    # Neither is any starting DEF/MED — selling one drops that position
    # below what a 4-4-2/4-3-3/etc. needs alongside the other three.
    assert not any(k in a.sell for a in acts_cd
                  for k in ("me_k", "me_d1", "me_d2", "me_d3", "me_d4",
                           "me_m1", "me_m2", "me_m3", "me_m4")), \
        [a for a in acts_cd if a.sell]

    # -- value_rate: the shared primitive, on its own -----------------------
    assert value_rate(120.0, 14.13e6) is not None
    assert abs(value_rate(120.0, 14.13e6) - 120.0 / 14.13) < 1e-9
    assert value_rate(120.0, 0.0) is None       # nothing to divide by
    assert value_rate(120.0, -5e6) is None       # a net-negative "cost" is not one
    assert value_rate(None, 5e6) is None
    assert value_rate(0.0, 5e6) == 0.0           # a real price, zero return: 0, not None

    # -- player_forecasts: full pool, two-tier, replacement-relative --------
    from ffcore.crosswalk import Player
    from ffcore.profile import (PlayerProfile,
                                PlayerCurrent, PlayerHistory, PlayerDerived)

    def mk_profile(pj, pos="MED", market_exp=None):
        return PlayerProfile(
            identity=Player(player_id="x"), current=PlayerCurrent(pos=pos),
            history=PlayerHistory(),
            derived=PlayerDerived(pj=pj, market_exp=market_exp))

    # "me" holds two MED: me_a (weak, replacement baseline) and me_b
    # (strong). "cand" is a market candidate, simulated. "unsimmed" is a
    # full-pool-only player nobody's squad holds and nothing has listed —
    # exactly the case market_exp has to carry alone.
    pf_sq = {"me": {"me_a": "MED", "me_b": "MED"}}
    pf_per = {1: {"me_a": (2.0, 1.0), "me_b": (5.0, 1.0), "cand": (4.0, 1.0)},
             2: {"me_a": (2.0, 1.0), "me_b": (5.0, 1.0), "cand": (4.0, 1.0)}}
    pf_u = Universe(
        state=LeagueState(pf_sq, [1, 2], "me"),
        forecaster=Bootstrap(pf_per), cash=0.0, me="me",
        players={"me_a": mk_profile(20.0), "me_b": mk_profile(15.0),
                "cand": mk_profile(8.0),
                "unsimmed": mk_profile(1.0, "DEL", market_exp=3.0)})
    fc_out = player_forecasts(pf_u)

    # me_a and me_b: simulated, season = 2 jornadas summed.
    assert fc_out["me_a"]["season_pts"] == 4.0, fc_out["me_a"]
    assert fc_out["me_a"]["next_pts"] == 2.0, fc_out["me_a"]
    assert fc_out["me_a"]["simulated"] is True
    assert fc_out["me_b"]["season_pts"] == 10.0, fc_out["me_b"]

    # Replacement at MED = the WEAKER of me's two MEDs (me_a, 4.0 season
    # pts) — cand's PAR is his own season total minus that baseline.
    assert fc_out["cand"]["season_pts"] == 8.0, fc_out["cand"]
    assert fc_out["cand"]["par"] == 8.0 - 4.0, fc_out["cand"]
    # me_a against himself: PAR is 0, he IS the baseline he'd replace.
    assert fc_out["me_a"]["par"] == 0.0, fc_out["me_a"]
    # me_b's PAR uses the SAME baseline (me_a) — a squad's replacement
    # level does not move just because you are asking about its own star.
    assert fc_out["me_b"]["par"] == 10.0 - 4.0, fc_out["me_b"]

    # "unsimmed": not in u.forecaster at all — falls back to
    # market_exp * n_remaining_jornadas (3.0 * 2), flagged un-simulated.
    assert fc_out["unsimmed"]["season_pts"] == 6.0, fc_out["unsimmed"]
    assert fc_out["unsimmed"]["next_pts"] == 3.0, fc_out["unsimmed"]
    assert fc_out["unsimmed"]["simulated"] is False
    # DEL replacement: no DEL in my squad at all -> baseline 0.0, so PAR
    # is just his own season total.
    assert fc_out["unsimmed"]["par"] == 6.0, fc_out["unsimmed"]

    # pj passes straight through from derived — the existing SE-shrinkage
    # evidence count, not a new statistic.
    assert fc_out["cand"]["pj"] == 8.0, fc_out["cand"]

    print("decide self-test OK (150 cases)")


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
    print("\n%-52s %7s %7s %10s   %s"
          % ("do this", "net pts", "Δwin", "net €", "biggest gain vs"))
    for r in rows[:8]:
        a = r["action"]
        who = max(rivals, key=lambda v: r["d_beat"][v])
        print("%-52s %+7.1f %+6.1f%% %10s   %s %+.0f%%"
              % (a.label()[:52], r["net_pts"], 100 * r["d_win"],
                 fmt_money(-a.net), who[:16], 100 * r["d_beat"][who]))
