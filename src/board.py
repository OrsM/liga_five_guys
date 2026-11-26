"""The board stage: decisions.json, the phone's copy of the board, from the league at t. Your cash and what you can
bid, your squad with its eleven, the players on the market who would add points to it (buys, most first, each with
the bid to make, the sales that fund it and the net points it adds to doing nothing: approach.approach), and each standing offer on your
players ranked by what taking it costs your eleven (valuation.read). The one line act.py takes is do: list your unlisted players at
their value."""
from __future__ import annotations

import datetime as dt
import json
import random
from itertools import combinations
import sys
from collections.abc import Callable, Iterable, Sequence
from typing import Any, NamedTuple

import decision
import eleven
import hurdle
import worth
from approach import Approach, Bidding, Club, Money, Offer, Option, Plan, Rounds, Side, approach, bidding, plan, pricing_of, replacement, secure
from ffcore import current, league, loader, price_fold, state
from ffcore import forecast as model, pricing, rules, tidy
from ffcore import observations as ob
from ffcore import records as rec
from ffcore.auction import Curve, chance
from ffcore.clock import MADRID, Clock, cut, shown
from ffcore.parse import snapshot_stamp
from ffcore.store import RunRecords
from ffcore.rules import POSITIONS, bid_limit
from health import Health
import valuation
from inflow import NONE, Inflow, inflow_of, risk_of
from valuation import READERS
from ffcore.tidy import REPORTS
from stats import percentile

__all__ = ["board", "main"]

Doc = dict[str, Any]
TOP_BUYS = 10
EVALUATED_BUYS = 10
GAIN_ONLY = "gain before sales"
UNFUNDED = "cannot be funded"
FUNDED_FLOOR = 0.5
EVALUATED_NOTE = "not evaluated: it is beyond the best listings by gain that are"
REASONS = {"no_curve": "no chance of winning is known for this auction", "no_win_chance": "no bid has the chance %.0f%% needed" % (100 * hurdle.WIN_FLOOR),
           "unfundable": "no sales fund even the asking price", "net_not_positive": "its funded net is not above the floor of %.1f points" % FUNDED_FLOOR,
           "not_evaluated": EVALUATED_NOTE}
TOP_TARGETS = 10
TOP_REPLACEMENTS = 8
EXAMINED_REPLACEMENTS = 40
SOLD_VALUE = -1e-6


def stamped(t: str) -> str:
    """A stamp with its colon (YYYY-MM-DDTHH:MMZ), from either form."""
    return t if ":" in t[11:] else t[:13] + ":" + t[13:]


def next_value(inp: decision.Inputs) -> dict[str, float]:
    _, cells = decision.cells_of(inp)
    return cells.get(min((x.jornada for x in inp.locks), default=min(cells, default=0)), {})


def squad_without(inp: decision.Inputs, sold: frozenset[str]) -> frozenset[str]:
    """Your squad less the players of sold (the sales the board makes before the lock): the squad that plays it."""
    return decision.squad_of(inp) - sold


def best_without(inp: decision.Inputs, sold: frozenset[str]) -> eleven.Settled | None:
    """The best eleven of squad_without(sold) for the next jornada and the swaps that reach it from the app's eleven
    (eleven.best_eleven; a player of sold the app fields is valued SOLD_VALUE, under any player's forecast, so the swaps
    take him out first); from eleven.build when the app has no legal eleven. None when no legal eleven is reached."""
    pos, _ = decision.cells_of(inp)
    e = next_value(inp)
    fielded = decision.lineup_of(inp, pos).eleven
    if fielded is None:
        return eleven.best_eleven(squad_without(inp, sold), pos, e)
    gone = fielded & sold
    return eleven.best_eleven(squad_without(inp, sold) | gone, pos, {**e, **{k: SOLD_VALUE for k in gone}}, fielded)


def squad_rows(inp: decision.Inputs, names: dict[str, str], sold: frozenset[str] = frozenset()) -> list[Doc]:
    """Your squad, a row each: xi in the app's lineup, best in the best eleven of squad_without(sold), sold in sold."""
    pos, _ = decision.cells_of(inp)
    mine = decision.squad_of(inp)
    e = next_value(inp)
    fielded = decision.lineup_of(inp, pos).eleven
    settled = best_without(inp, sold)
    best = settled.eleven if settled else frozenset()
    xi = fielded if fielded is not None else best
    order = sorted(mine, key=lambda k: (k not in xi, POSITIONS.index(pos[k]) if k in pos else len(POSITIONS), -e.get(k, 0.0), k))
    return [{"name": names.get(k, k), "pos": pos.get(k, ""), "next": e.get(k, 0.0), "xi": k in xi, "best": k in best,
             "sold": k in sold, "key": k} for k in order]


def advice(swaps: tuple[tuple[str, str, float], ...], names: dict[str, str]) -> list[Doc]:
    steps: list[list[Any]] = []
    for into, out, gain in swaps:
        for step in steps:
            if step[0] == out:
                step[0], step[2] = into, step[2] + gain
                break
        else:
            steps.append([into, out, gain])
    return [{"in": names.get(i, i), "out": names.get(o, o), "gain": g} for i, o, g in steps if g > 0]


def lineup_doc(inp: decision.Inputs, names: dict[str, str], sold: frozenset[str] = frozenset()) -> Doc:
    """The lineup of the squad as the app has it and the swaps that improve it among the squad that plays the lock
    (squad_without(sold)), so no advice brings in a player sold; points those of the app's eleven, a player sold scoring
    none; stale when the app's eleven is not that best eleven."""
    pos, _ = decision.cells_of(inp)
    e = next_value(inp)
    got = decision.lineup_of(inp, pos)
    settled = best_without(inp, sold)
    if got.eleven is None or settled is None:
        return {"eleven": None, "message": got.message or "the squad cannot field a legal eleven", "unmapped": list(got.unmapped),
                "points": None, "best": eleven.total(settled.eleven, e) if settled else None, "advice": [], "stale": True}
    slots = {s.player: s.slot for s in inp.lineup}
    order = sorted(got.eleven, key=lambda k: (POSITIONS.index(slots[k]) if slots[k] in POSITIONS else len(POSITIONS), -e.get(k, 0.0), k))
    return {"eleven": [names.get(k, k) for k in order], "message": "", "unmapped": list(got.unmapped),
            "points": eleven.total(got.eleven - sold, e), "best": eleven.total(settled.eleven, e), "advice": advice(settled.swaps, names),
            "stale": got.eleven != settled.eleven}


def context(inp: decision.Inputs, offers: worth.OfferDistribution = worth.NO_OFFERS, rate: float = hurdle.FALLBACK_LAMBDA,
            closes: int = 0) -> worth.Context:
    pos, cells = decision.cells_of(inp)
    locks = decision.lock_times(inp)
    return worth.Context(pos, decision.window_of(locks, cells, None), inp.money.cash, offers, rate,
                         decision.squad_of(inp), cells, locks, closes)


def standing(inp: decision.Inputs, player: str, value: float, offer: float | None) -> worth.Standing:
    """The offer on a player as the sale rules read it: his decision.exit_of."""
    return worth.Standing(player, decision.exit_of(inp, player, value), offer)


def offers_at(world: state.World) -> worth.OfferDistribution:
    """The distribution of offer over value (worth.offer_ratio_distribution) of the market offers the world has seen."""
    return worth.offer_ratio_distribution(x.ratio for x in current.offer_ratios(world))


def delta_doc(sale: valuation.Reading) -> Doc:
    """The Delta of a move as the document states it: points (the season), window (points over each of valuation.READERS)
    and gap (the jornadas the move leaves without a legal eleven); every number null when the squad after the move has none."""
    return {"points": sale.points, "window": dict(sale.window), "gap": list(sale.gap)}


def offer_lines(inp: decision.Inputs, names: dict[str, str], got: list[decision.Offer], known: dict[str, decision.Fact],
                offers: worth.OfferDistribution = worth.NO_OFFERS, rate: float = hurdle.FALLBACK_LAMBDA, closes: int = 0,
                at: Sequence[str] = ()) -> list[Doc]:
    """A sells row for each standing offer, judged by worth.LIVE at rate with `closes` left before the lock: verdict sell (the offer reaches the reserve),
    hold, or needs_replacement (no legal eleven without him), before settle; lost the points the sale loses (None: no
    Delta) as points_lost, rent the points the money earns at rate over the jornadas of the sale; by per_million (points lost per
    million raised), lowest first. `reserves` the least offer worth.waiting_reserve accepts until each of the moments `at` (untils_of: the closes, then the lock; the
    last with no draw after it)."""
    ctx = context(inp, offers, rate, closes)
    n = ctx.jornadas()
    out = []
    for offer in got:
        k, value, money = offer.player, offer.value, offer.money
        fact = known.get(k, decision.UNKNOWN)
        verdict = worth.LIVE(standing(inp, k, value, money), ctx)
        sale = verdict.sale
        lost = None if sale.points is None else 0.0 - sale.points
        ratio = money / value if value else None
        share = None if ratio is None else worth.offer_percentile(offers, ratio)
        name = names.get(k, k)
        out.append({
            "name": name, "pos": fact.pos or ctx.pos.get(k, ""), "what": "sell", "label": "sell " + name, "step": "take offer",
            "offer": money, "waiting": None, "trend": fact.trend, "plan": False, "amount": money, "done": False, "key": k,
            "gain": sale.points, "per": "season", "more": False, "detail": None, "trace": None, "flip": None,
            "ratio": ratio, "delta": delta_doc(sale),
            "feasible": sale.points is not None, "why_infeasible": None if sale.points is not None else "no_eleven",
            "replacement": None, "replacement_why_not": None,
            "offer_percentile": share, "offers_seen": len(offers.ratios),
            "per_million": None if lost is None or not money else lost / (money / 1e6),
            "verdict": "needs_replacement" if verdict.key is None else "sell" if verdict.key <= 0 else "hold",
            "points_lost": lost, "rent": rate * n * money / 1e6,
            "reserves": reserves_doc(at, [worth.waiting_reserve(standing(inp, k, value, money), offers, len(at) - 1 - j) for j in range(len(at))]),
            "facts": [["value", value, "money"], ["offer", money, "money"], ["offer over value", ratio, "ratio"],
                      ["offer percentile", share, "chance"], ["offers seen", len(offers.ratios), "count"],
                      *[["points, " + name, sale.window[name], "points"] for name in READERS]],
            "accepts": [], "act": []})
    return sorted(out, key=lambda d: (d["per_million"] is None, d["per_million"], d["key"]))


class Shown(NamedTuple):
    """A line of a sells row's facts: its label, value and kind."""
    label: str
    value: Any
    kind: str


class Stood(NamedTuple):
    """A standing offer of the game on one of my players: the player, the money and the app's id and creation stamp."""
    player: str
    money: float
    offer_id: str
    created_at: str


def stood_offers(held: Iterable[decision.Held], offers: Iterable[current.Offer]) -> list[Stood]:
    """The pending offers of the game on the players held (by player_team_id), each with its player."""
    team = {h.player_team: h.player for h in held}
    return [Stood(team[o.player_team_id], o.money, o.offer_id, o.created_at) for o in offers
            if o.status == "pending" and o.money is not None and o.player_team_id in team]


def offer_rows(at: str, sells: Sequence[Doc], stood: Iterable[Stood]) -> list[dict[str, Any]]:
    """The dec_offer rows of the cut: one per row of the sells document, in its order, every field the document's (the
    value from its facts, delta_next4 and delta_season its Delta's, fields whether the sale leaves a legal eleven, gap the jornadas it
    leaves without one joined by ';', funds the names of its "for" joined by ';') and the offer's id and
    creation stamp from the stood offer of that player and money (the lowest id first when several are alike)."""
    waiting: dict[tuple[str, float], list[Stood]] = {}
    for x in sorted(stood, key=lambda s: s.offer_id):
        waiting.setdefault((x.player, x.money), []).append(x)
    rows = []
    for sale in sells:
        given = waiting[(sale["key"], sale["offer"])].pop(0)
        value = next(f.value for f in map(lambda f: Shown(*f), sale["facts"]) if f.label == "value")
        delta = sale["delta"]
        rows.append({"cut": at, "player": sale["key"], "offer_id": given.offer_id, "created_at": given.created_at,
                     "offer": sale["offer"], "value": value, "ratio": sale["ratio"], "percentile": sale["offer_percentile"],
                     "offers_seen": sale["offers_seen"], "delta_next4": delta["window"]["next4"],
                     "delta_season": delta["points"], "fields": sale["feasible"], "gap": ";".join(map(str, delta["gap"])),
                     "funds": ";".join(sale["for"]), "trend": sale["trend"]})
    return rows


def not_listed_doc(club: Club, names: dict[str, str], sold: Iterable[str]) -> list[str]:
    """The names of the players among `sold` who are not listed (club.unlisted), in name order."""
    return sorted(names.get(k, k) for k in club.unlisted.intersection(sold))


def funding_doc(club: Club, names: dict[str, str], found: Approach | None, bid: float) -> Doc:
    """The funding of a bid as a document: what is sold tonight and after its result, the room, the cash and points either way, and
    `not_listed` the names sold after its result who are not listed (no offer is drawn for them)."""
    if found is None:
        return {"feasible": False, "short": None}
    if not found.feasible:
        return {"feasible": False, "short": found.short, "gain": found.gain}
    tonight = sorted(found.tonight, key=lambda k: (-club.offers[k], k))
    return {"feasible": True, "short": 0.0,
            "tonight": [{"name": names.get(k, k), "offer": club.offers[k],
                         "ratio": club.offers[k] / club.worth[k] if club.worth[k] else None} for k in tonight],
            "raises": found.raised, "margin": found.margin, "lock_margin": found.lock_margin,
            "lock_margin_low": found.lock_margin_low,
            "if_won": [names.get(k, k) for k in sorted(found.if_won)], "if_lost": [names.get(k, k) for k in sorted(found.if_lost)],
            "held": [names.get(k, k) for k in sorted(found.held)], "cash_if_lost": found.cash_if_lost,
            "not_listed": not_listed_doc(club, names, found.if_won | found.if_lost),
            "gain": found.gain, "points_lost_if_won": found.cost_won, "points_lost_if_lost": found.cost_lost, "net": found.net,
            "base": found.base}


def base_cost(found: Approach | None) -> float:
    """The points of the cheapest plan with no buy (`found` the approach with no player): what it sells to bring cash to 0
    or more costs; 0 when there is no such plan or no eleven."""
    return found.cost_lost if found is not None and found.feasible else 0.0


def plan_points(base: Approach | None, found: Plan | None, club: Club) -> float:
    """The points of the plan with no buy: the expected points of the secured plan (approach.secure) when cash is below 0 and it has
    options, else base_cost."""
    return found.cost if found is not None and club.cash < 0 and found.feasible and found.options else base_cost(base)


def plan_cost(base: Approach | None, found: Plan | None, club: Club) -> float:
    """The cost of the plan with no buy a buy is netted against, in the one objective: points lost less the money value of what its sales fetch
    over the players' expected offers (the secured plan's Option.net when cash is below 0 and it has options, else the base approach's)."""
    if found is not None and club.cash < 0 and found.feasible and found.options:
        return found.options[0].net
    return -base.net if base is not None and base.feasible else 0.0


def risk_doc(club: Club, cash: float) -> Doc:
    """The risk the credit due before the lock leaves for `cash` at the lock before it lands (inflow.risk_of): low_inflow, p_short,
    shortfall_if_low and below_points; {} when no credit is due."""
    if not club.inflow.jornadas:
        return {}
    return {"risk": risk_of(club.inflow, cash)._asdict()}


def risk_clause(club: Club, cash: float) -> str:
    """The words that tell the risk: when the credit due leaves cash short with some chance, the points under which the eleven leaves
    the lock cash below 0; "" otherwise."""
    if not club.inflow.jornadas or risk_of(club.inflow, cash).p_short <= 0.0:
        return ""
    return "; if the eleven scores under %.0f points the lock cash stays below 0; there is time to fix by hand" % (
        risk_of(club.inflow, cash).below_points)


def base_doc(club: Club, names: dict[str, str], base: Approach | None, found: Plan | None) -> Doc:
    """The funding document of the plan with no buy, with its cost in points, `cost`, the cash it starts from and, when a credit is due
    before the lock, its `risk` (risk_doc) on the cash the plan leaves before it. With cash below 0 and a secured plan (approach.secure)
    its sales, cost and cash replace the approach's: one plan in the document."""
    secured = found is not None and club.cash < 0 and found.feasible and bool(found.options)
    before = club.cash + found.raised if secured and found is not None else \
        club.cash if base is None or not base.feasible else base.cash_if_lost - club.inflow.expected
    doc = {**funding_doc(club, names, base, 0.0), "cost": plan_points(base, found, club), "cash": club.cash, **risk_doc(club, before)}
    if secured and found is not None and base is not None and base.feasible:
        waiting = [names.get(k, k) for k in sorted(found.sells - found.now)]
        doc |= {"tonight": [{"name": names.get(k, k), "offer": club.offers[k], "ratio": club.offers[k] / club.worth[k] if club.worth[k] else None}
                            for k in sorted(found.now, key=lambda k: (-club.offers[k], k))],
                "raises": sum(club.offers[k] for k in found.now), "if_won": waiting, "if_lost": waiting, "cash_if_lost": found.options[0].cash,
                "not_listed": not_listed_doc(club, names, found.sells - found.now),
                "held": [names.get(k, k) for k in sorted((frozenset(club.offers) & club.squad) - found.now)],
                "lock_margin": found.options[0].cash, "points_lost_if_won": found.cost, "points_lost_if_lost": found.cost, "net": -found.cost}
    return doc


class Stance(NamedTuple):
    """Where a buy's bid stands: the bid the line is on (yours once one is placed, else the advised) and its chance,
    the bid advised and its chance, the move and `to`, the amount a lower or a raise moves a placed bid to (else None).
    The move of a listing with no bid placed is bid, or pass (none advised: not a buy). The move of a bid placed is
    hurdle.stand's: hold, lower, withdraw or raise, read at the walk-away price cap (0 when the player has none). With no
    curve for his auction no chance is known (None) and nothing is advised: a bid placed is lower above cap, else hold,
    and a listing with none placed is unknown."""
    amount: float | None
    win: float | None
    advised: float | None
    advised_win: float | None
    move: str
    to: float | None = None


def millions(x: float) -> str:
    """An amount in euros in words, in millions to one decimal."""
    return "%.1f million" % (x / 1e6)


def over_walk_away(amount: float | None, cap: float | None) -> bool:
    """Whether a bid is above the walk-away price (False with no bid or no price)."""
    return amount is not None and cap is not None and amount > cap


def net_words(net: float | None, label: str | None) -> str:
    """The net of a bid in words: the expected points against the plan with no buy, the chance of winning counted, or its
    label (cannot be funded, gain only) when it has none."""
    return " Counting its chance of winning it nets %+.1f points against the plan with no buy." % net if net is not None else " Its net is not known (%s)." % label


def stance_why(at: Stance, cap: float | None, rate_yield: float | None, curve: Curve | None, asking: float, net: float | None = None,
               label: str | None = None) -> str | None:
    """The words for a bid placed, with the numbers: the amount and its chance, the walk-away price and the yield, then
    the move (hold, lower to an amount, raise to an amount or withdraw) with the expected surplus (chance x (walk-away
    price - amount)) behind it; a bid above the walk-away price ends with its `net` (or `label`) said; None for a listing with no bid placed."""
    if at.amount is None or at.move in ("bid", "pass", "unknown"):
        return None
    away = "no walk-away price" if cap is None else "a walk-away price of " + millions(cap)
    yld = "" if rate_yield is None else " and a yield of %.3f points per million per jornada" % rate_yield
    base = "Your bid of %s has %s" % (millions(at.amount), "an unknown chance" if at.win is None else "a %.0f%% chance" % (100 * at.win))
    base += ", against %s%s. " % (away, yld)
    tail = net_words(net, label) if over_walk_away(at.amount, cap) else ""
    if cap is None or curve is None or at.win is None:
        return base + ("Lower it: it is above the walk-away price." if at.move == "lower" else "Keep it: no chance is known for this auction.") + tail
    surplus = at.win * (cap - at.amount)
    own = "an expected surplus of %s" % millions(surplus)
    if at.move == "hold":
        return base + "It leaves %s: keep it." % own + tail
    if at.move == "withdraw":
        return base + "No bid of %s up has a positive expected surplus under that price (yours leaves %s): withdraw it." % (millions(asking), own) + tail
    assert at.to is not None
    there = chance(curve, at.to / asking)
    other = "a bid of %s has a %.0f%% chance and an expected surplus of %s" % (millions(at.to), 100 * there, millions(there * (cap - at.to)))
    return base + "Yours leaves %s; %s: %s it to %s." % (own, other, at.move, millions(at.to)) + tail


def priced(r: dict[str, Any]) -> Doc:
    """The reading of an opportunity row at its Stake: n (the jornadas of its window) and yield (points per million per jornada, None with no
    price)."""
    stake = r["stake"]
    return {"n": stake.n, "yield": r["per_million"] / stake.n if r["per_million"] is not None else None}


def outcomes(found: Approach) -> tuple[float, float]:
    """The points a funded bid adds to the plan with no buy if it is won and if it is lost (the net is their average at the chance of winning)."""
    return (round(found.gain - found.paid - (found.cost_won - found.credit_won - found.base), 9) + 0.0,
            round(-(found.cost_lost - found.credit_lost - found.base), 9) + 0.0)


def plan_line(b: Doc) -> str:
    """The funded plan of a buy line as one sentence: the bid and its chance, the sales that fund it tonight and after the result, the expected
    net, what it adds if won and costs if lost, and what it displaces."""
    f = b["funding"]
    tonight = "tonight " + ", ".join(t["name"] for t in f["tonight"]) if f["tonight"] else ""
    after = "; ".join(x for x in (", ".join(f["if_won"]) + " if won" if f["if_won"] else "", ", ".join(f["if_lost"]) + " if lost" if f["if_lost"] else "") if x)
    sells = ", then ".join(x for x in (tonight, after) if x) or "nobody (cash covers it)"
    return "buy %s at %s (%.0f%% to win), funded by selling %s: %+.1f points expected (%+.1f if won, %+.1f if lost); displaces %s." % (
        b["name"], millions(b["amount"]), 100 * b["win"], sells, b["net"], b["if_won_points"], b["if_lost_points"],
        ", ".join(b["displaces"]) or "nobody") + (
        " Cash at the lock %s if won, %s if lost; worst case %+.1f points." % (millions(f["lock_margin"]), millions(f["cash_if_lost"]),
                                                                          min(b["if_won_points"], b["if_lost_points"])))


def rejected_line(b: Doc) -> Doc:
    """The compact row of a candidate that is not a funded plan: who, why (a code and words), the net where it has one and the gain."""
    return {"name": b["name"], "key": b["key"], "pos": b["pos"], "asking": b["asking"], "gain": b["gain"], "net": b["net"], "reason": b["reason"],
            "why": REASONS[b["reason"]] + ("" if b["net"] is None else " (net %+.1f)" % b["net"])}


def displaced(names: dict[str, str], sold: Iterable[str], replaces: str | None) -> list[str]:
    """Who a funded plan displaces: the players it sells after the result, then the one of the eleven the buy takes the place of."""
    return [names.get(k, k) for k in sorted(sold)] + ([names.get(replaces, replaces)] if replaces else [])


def stance_of(r: dict[str, Any], curve: Curve | None, walk: float | None, best: Offer | None, advised: float | None, advised_win: float | None,
              fund: Callable[[float, float], Approach | None]) -> tuple[Stance, Approach | None]:
    """The Stance of a listing and the Approach it is shown with. With a bid placed it is hurdle.stand at the funded walk-away price (lower above it,
    hold with no curve), funded at the placed amount; otherwise the best Offer's bid when it is advised (bid), else pass."""
    placed = r["placed"]
    if placed is None:
        return Stance(best.amount if best else None, best.win if best else None, advised, advised_win, "bid" if advised is not None else "pass"), \
            best.found if best else None
    if curve is None:
        return Stance(placed, None, None, None, "lower" if walk is not None and placed > walk else "hold"), None
    win = chance(curve, placed / r["asking"])
    got = hurdle.stand(curve, r["asking"], placed, walk or 0.0)
    return Stance(placed, win, advised, advised_win, got.move, got.to), fund(placed, win)


def buy_lines(inp: decision.Inputs, names: dict[str, str], club: Club, got: list[dict[str, Any]], cost0: float, rate: float = hurdle.FALLBACK_LAMBDA
              ) -> tuple[list[Doc], list[Doc], list[Doc]]:
    """The buys of the board, the funded plans and the candidates left out. Every player on the market with an asking price and a gain (or a bid
    placed; one who adds no points is no candidate) is a candidate, best gain first; the first EVALUATED_BUYS get a Bidding (approach.bidding): the funded net of each bid of the auction
    curve against the plan with no buy (`cost0`, its cost in the one objective), the bid being the one of the greatest net and the walk-away price
    the one at which it is 0. A plan whose best net is above FUNDED_FLOOR is a funded plan (`plans`, by net, one `line` each); of them those whose
    is above FUNDED_FLOOR is advised (`buys`, with the bids placed first, a bid placed whatever its net: a move of hold, lower,
    withdraw or raise, a `why` in words, hurdle.stand at the funded walk-away price). Every other candidate is in `rejected` with its `reason` and net;
    none is dropped without being counted."""
    pos, cells = decision.cells_of(inp)
    locks = decision.lock_times(inp)
    curves = decision.curves_of(inp)
    seen = {a.player: a for a in inp.auctions}
    bids = {b.player: b.amount for b in inp.bids}
    sides: dict[tuple[frozenset[str], tuple[int, ...]], Side] = {}
    market = [r for r in got if r["status"] == "market" and r["asking"] is not None and (r["gain"] + r["profit"] > 0 or r["placed"] is not None)]
    mine = [r for r in market if r["placed"] is not None]
    wanted = mine + [r for r in market if r["placed"] is None]
    out: list[Doc] = []
    for i, r in enumerate(wanted):
        k, placed = r["player"], r["placed"]
        curve = curves.get(k)
        name = names.get(k, k)
        others = (len(bids) - (k in bids), sum(bids.values()) - bids.get(k, 0.0))
        rounds = Rounds(cells, decision.window_of(locks, cells, None), decision.window_of(locks, cells, r["close_at"]))
        money = Money(rate, len(decision.window_of(locks, cells, None)), None if r["profit"] > 0 else hurdle.draw(r.get("value") or r["asking"]))
        held_club = club._replace(pending=others)

        def fund(amount: float, win: float, r: dict[str, Any] = r, k: str = k, held_club: Club = held_club, rounds: Rounds = rounds,
                 money: Money = money) -> Approach | None:
            earns = hurdle.profit(r["stake"], amount)
            return approach(held_club, k, amount, win, rounds, cost0, sides, earns, money)
        trying = i < EVALUATED_BUYS and curve is not None
        bid = bidding(curve, r["asking"], fund, money.kappa) if trying and curve is not None else Bidding((), None, None)
        best = bid.best
        reason = ("no_curve" if curve is None else "not_evaluated" if not trying else "no_win_chance" if not any(p >= hurdle.WIN_FLOOR and ratio >= 1.0 for ratio, p in curve)
                  else "unfundable" if best is None else None)
        net = best.found.net if best is not None else None
        walk = bid.walk_away if best is not None else None
        clears = best is not None and net is not None and net > FUNDED_FLOOR
        if reason is None and not (net is not None and net > FUNDED_FLOOR):
            reason = "net_not_positive"
        advised = best.amount if best is not None and clears else None
        advised_win = best.win if best is not None and clears else None
        at, shown = stance_of(r, curve, walk, best, advised, advised_win, fund)
        amount, win = at.amount, at.win
        found = shown if shown is not None and shown.feasible else None
        funding = None if shown is None or amount is None else funding_doc(club, names, shown, amount)
        shown_net = found.net if found is not None else None
        label = None if shown_net is not None else UNFUNDED if shown is not None else GAIN_ONLY
        auction = seen.get(k)
        won_pts, lost_pts = outcomes(found) if found is not None else (None, None)
        out.append({
            "name": name, "pos": r["pos"], "what": "buy", "label": "buy " + name, "step": "placed" if placed is not None else "bid",
            "price": r["asking"], "asking": r["asking"], "amount": amount, "win": win, "advised": at.advised, "advised_win": at.advised_win,
            "move": at.move, "to": at.to,
            "why": stance_why(at, walk if walk is not None or placed is None or curve is None else 0.0,
                              r["per_million"] / r["stake"].n if r["per_million"] is not None else None, curve, r["asking"], shown_net, label),
            "plan": False, "done": placed is not None,
            "key": k, "gain": r["gain"], "net": shown_net, "net_label": label, "per": "season", "more": False, "detail": None, "trace": None, "flip": None,
            "replaces": names.get(r["replaces"], r["replaces"]), "per_million": r["per_million"], **priced(r), "walk_away": walk,
            "reason": reason, "if_won_points": won_pts, "if_lost_points": lost_pts,
            "displaces": [] if found is None else displaced(names, found.if_won, r["replaces"]),
            "price_next": r["price_next"], "price_lock": r["price_lock"],
            "facts": [["asking", r["asking"], "money"], ["walk-away price", walk, "money"], ["bid", amount, "money"],
                      ["chance to win", win, "chance"]]
            + ([["advised bid", at.advised, "money"], ["chance at the advised bid", at.advised_win, "chance"]] if placed is not None else [])
            + [["points added", r["gain"], "points"]]
            + ([["price expected after the hold", r["stake"].proceeds, "money"], ["points the hold earns", hurdle.profit(r["stake"], amount) if amount else 0.0, "points"]]
               if r["stake"].k < r["stake"].n and amount else []),
            "bids": auction.bids if auction else None,
            "auction": {"yours": bool(auction.mine), "listings": auction.n, "thin": bool(auction.thin), "cell": auction.cell}
            if auction else {"yours": False, "listings": 0, "thin": True, "cell": ""},
            "funding": funding, "accepts": [], "act": []})
    placed_lines = [b for b in out if b["done"]]
    free = [b for b in out if not b["done"]]
    funded_plans = sorted((b for b in free if b["reason"] is None), key=lambda b: (-b["net"], -b["gain"], b["name"]))
    for b in funded_plans:
        b["line"] = plan_line(b)
    advised_lines = funded_plans[:TOP_BUYS]
    left = [rejected_line(b) for b in free if b["reason"] is not None]
    lines = placed_lines + advised_lines
    overlapped(inp, lines, {r["player"]: r for r in got})
    plans = [{"name": b["name"], "key": b["key"], "pos": b["pos"], "asking": b["asking"], "bid": b["amount"], "win": b["win"], "walk_away": b["walk_away"],
              "net": b["net"], "gain": b["gain"], "if_won_points": b["if_won_points"],
              "if_lost_points": b["if_lost_points"], "worst": min(b["if_won_points"], b["if_lost_points"]), "displaces": b["displaces"],
              "cash_if_won": b["funding"]["lock_margin"], "cash_if_lost": b["funding"]["cash_if_lost"], "funding": b["funding"], "line": b["line"]}
             for b in funded_plans]
    return lines, plans, left


def overlapped(inp: decision.Inputs, lines: list[Doc], got: dict[str, dict[str, Any]]) -> None:
    """Sets `gain_with_better` on each buy line: the points he adds over his window to the squad plus the buys above him that are
    not holds; adds a fact when it is below his `gain`. The first line's is its own gain."""
    pos, cells = decision.cells_of(inp)
    squad = decision.squad_of(inp)
    above: frozenset[str] = frozenset()
    for b in lines:
        r = got[b["key"]]
        b["gain_with_better"] = valuation.Buyer(squad | above, r["window"], pos, cells).gain(b["key"])
        if b["gain_with_better"] < b["gain"]:
            b["facts"].append(["points added if the buys above are won too", b["gain_with_better"], "points"])
        if r["profit"] <= 0:
            above = above | {b["key"]}


def target_lines(names: dict[str, str], got: list[dict[str, Any]]) -> list[Doc]:
    return [{"name": names.get(r["player"], r["player"]), "pos": r["pos"], "status": r["status"], "gain": r["gain"],
             "asking": r["asking"], "bid": r["bid"], "win": r["win"], "placed": r["placed"],
             "replaces": names.get(r["replaces"], r["replaces"]),
             "per_million": r["per_million"], "price_next": r["price_next"], "price_lock": r["price_lock"], **priced(r),
             "available": r["status"] == "market"}
            for r in [r for r in got if r["gain"] > 0][:TOP_TARGETS]]


def list_line(inp: decision.Inputs, names: dict[str, str], extras: decision.Extras) -> list[Doc]:
    mine = decision.squad_of(inp)
    got = {h.player: h for h in extras.held}
    known = extras.known
    todo = sorted(mine - extras.listed, key=lambda k: (names.get(k, k), k))
    if not todo:
        return []
    label = "list " + ", ".join(names.get(k, k) for k in todo)
    return [{"name": ", ".join(names.get(k, k) for k in todo), "pos": "", "what": "list", "label": label, "step": "list",
             "amount": None, "done": False, "accepts": [None], "gain": None, "per": None, "facts": [], "more": False,
             "detail": None, "trace": None, "flip": None, "plan": False,
             "act": [{"player_team": got[k].player_team, "player": got[k].player_id or k, "market": None, "bid": None,
                      "pos": known.get(k, decision.UNKNOWN).pos, "value": got[k].value} for k in todo if k in got]}]


def replacement_row(inp: decision.Inputs, names: dict[str, str], club: Club, sold: str, r: dict[str, Any], offer: Offer) -> Doc:
    """The row of the pair that sells `sold` and buys the market row r, funded as found says: the buy's asking and bid and chance,
    the pair's Delta (valuation.read, the buy arriving at its close) and its net (Approach.net, the bid lost counted), the cash at
    the lock if the bid is won and the funding."""
    pos, cells = decision.cells_of(inp)
    locks = decision.lock_times(inp)
    k, found = r["player"], offer.found
    pair = valuation.read(club.squad, valuation.Move(frozenset({sold}), frozenset({k}), None, r["close_at"]), decision.window_of(locks, cells, None),
                          locks, pos, cells)
    return {"what": "replace", "name": names.get(k, k), "key": k, "pos": r["pos"], "sell": names.get(sold, sold),
            "asking": r["asking"], "bid": offer.amount, "win": offer.win, "gain": found.gain, "net": found.net, "delta": delta_doc(pair),
            "cash_after": found.lock_margin, "feasible": True, "why_infeasible": None,
            "facts": [["asking", r["asking"], "money"], ["bid", offer.amount, "money"], ["chance to win", offer.win, "chance"],
                      ["net points", found.net, "points"]],
            "funding": funding_doc(club, names, found, offer.amount)}


def replacement_rows(inp: decision.Inputs, names: dict[str, str], club: Club, got: Sequence[dict[str, Any]], sells: Sequence[Doc],
                     rate: float = hurdle.FALLBACK_LAMBDA) -> None:
    """Sets, on every row of sells whose sale leaves no legal eleven, "replacement": the best pair (replacement_row) that sells the
    player and buys a player of the market with a bid advised (his row of got) in the positions that make the squad legal again, the
    most net points (Approach.net, at the bid of the greatest net: approach.bidding) of those approach.replacement funds, among the TOP_REPLACEMENTS best forecast of the
    EXAMINED_REPLACEMENTS best forecast that fit; or None
    with "replacement_why_not" "no_candidate" (no such player on the market) or "no_cash" (none is funded)."""
    pos, cells = decision.cells_of(inp)
    locks = decision.lock_times(inp)
    bids = {b.player: b.amount for b in inp.bids}
    curves = decision.curves_of(inp)
    for sale in sells:
        if sale["feasible"]:
            continue
        sold = sale["key"]
        fits = [r for r in got if r["status"] == "market" and r["player"] in curves and r["asking"] is not None and r["player"] not in club.squad
                and eleven.points((club.squad - {sold}) | {r["player"]}, pos, {}) is not None]
        ahead = {r["player"]: sum(cells[j].get(r["player"], 0.0) for j in decision.window_of(locks, cells, r["close_at"])) for r in fits}
        best: tuple[float, str, dict[str, Any], Offer] | None = None
        funded_n = 0
        for r in sorted(fits, key=lambda r: (-ahead[r["player"]], r["player"]))[:EXAMINED_REPLACEMENTS]:
            k = r["player"]
            others = (len(bids) - (k in bids), sum(bids.values()) - bids.get(k, 0.0))
            rounds = Rounds(cells, decision.window_of(locks, cells, None), decision.window_of(locks, cells, r["close_at"]))
            money = Money(rate, len(decision.window_of(locks, cells, None)), hurdle.draw(r.get("value") or r["asking"]))

            def fund(amount: float, win: float, k: str = k, others: tuple[int, float] = others, rounds: Rounds = rounds, money: Money = money) -> Approach:
                return replacement(club._replace(pending=others), sold, k, amount, win, rounds, money)
            offer = bidding(curves[k], r["asking"], fund, money.kappa).best
            if offer is None:
                continue
            funded_n += 1
            if best is None or (offer.found.net, k) > best[:2]:
                best = (offer.found.net, k, r, offer)
            if funded_n == TOP_REPLACEMENTS:
                break
        if best is None:
            sale["replacement_why_not"] = "no_cash" if fits else "no_candidate"
        else:
            sale["replacement"] = replacement_row(inp, names, club, sold, best[2], best[3])


def funds(buys: list[Doc], sells: list[Doc]) -> None:
    for sale in sells:
        sale["for"] = [b["name"] for b in buys
                       if any(t["name"] == sale["name"] for t in (b.get("funding") or {}).get("tonight", []))]


def settle(sells: list[Doc], cover: frozenset[str], now: frozenset[str] = frozenset()) -> None:
    """The verdicts of the sells rows once the cash's uses are known (rows with "for" set by funds; cover the sales of the
    plan that brings cash to 0 at the lock, now those of them taken at their standing offer): a sale that loses points is a sell
    only when its cash has a use (it funds a buy tonight or is in cover), else hold; a sell not taken tonight (worth.take_now)
    is wait; a sale in now is a sell. Then sells first, by per_million."""
    for sale in sells:
        if sale["verdict"] == "sell" and (sale["points_lost"] or 0.0) > worth.NO_LOSS and not sale["for"] and sale["key"] not in cover:
            sale["verdict"] = "hold"
        if sale["verdict"] == "sell" and not worth.take_now(sale["ratio"], sale["offer_percentile"], bool(sale["for"])):
            sale["verdict"] = "wait"
        if sale["key"] in now and sale["verdict"] != "needs_replacement":
            sale["verdict"] = "sell"
    sells.sort(key=lambda d: (d["verdict"] != "sell", d["per_million"] is None, d["per_million"], d["key"]))


def credit_line(club: Club) -> Doc:
    """The "wait" line of cash below 0 that the credit due before the lock covers, at its expected: the credit expected and at its low
    case, when it lands and the jornadas it pays for, the risk it runs (risk_doc); nothing to sell."""
    inflow = club.inflow
    landing = snapshot_stamp(inflow.at) if inflow.at else None
    return {
        "what": "wait", "label": "wait for the credit of jornada " + ", ".join(map(str, inflow.jornadas)), "step": "wait", "needed": False,
        "n_closes": None, "expected_cover": [], "expected_raised": 0.0, "need": 0.0, "cost_points": 0.0, "last_close": None,
        "credit_expected": inflow.expected, "credit_low": inflow.low, "credit_at": inflow.at,
        "credit_jornadas": list(inflow.jornadas), **risk_doc(club, club.cash),
        "why": "cash %.0f is below 0 now; jornada%s %s credits about %.0f (%.0f at the low case) at %s, before the lock: "
               "nothing to sell%s" % (club.cash, "s" if len(inflow.jornadas) > 1 else "", ", ".join(map(str, inflow.jornadas)), inflow.expected,
                                      inflow.low, shown(landing) if landing else "", risk_clause(club, club.cash)),
        "accepts": [], "act": []}


WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def untils_of(clock: Clock) -> list[str]:
    """The moments an offer can be accepted until, as stamps (YYYY-MM-DDTHH:MMZ): each close left, then the lock (the offer created at the
    last close is shown on the lock's day); none when no lock is ahead."""
    return [stamped(c) for c in clock.closes] + ([stamped(clock.lock)] if clock.lock else [])


def reserves_doc(untils: Sequence[str], leasts: Sequence[float | None]) -> list[Doc]:
    """The reserve of a sells row: the least offer accepted until each moment (None: the offer is not taken)."""
    return [{"until": u, "least": x} for u, x in zip(untils, leasts)]


def local(stamp_text: str) -> dt.datetime:
    """A stamp as Madrid time."""
    when = snapshot_stamp(stamp_text)
    assert when is not None, stamp_text
    return when.astimezone(MADRID)


def day_text(when: dt.datetime) -> str:
    """The day as '25 Nov'."""
    return "%d %s" % (when.day, MONTHS[when.month - 1])


def clock_text(when: dt.datetime) -> str:
    """The time as '22:24'."""
    return when.strftime("%H:%M")


def lock_text(when: dt.datetime) -> str:
    """The lock as 'Fri 27 Nov 21:00'."""
    return "%s %s %s" % (WEEKDAYS[when.weekday()], day_text(when), clock_text(when))


def price_text(x: float) -> str:
    """An amount in millions to two decimals, as '30.62M'."""
    return "%.2fM" % (x / 1e6)


def terms(club: Club, option: Option, names: dict[str, str], untils: Sequence[str]) -> list[Doc]:
    """What `option` asks of each sale, by name: accept now (kind "accept", `least` the standing offer, `until` the first moment, when the
    offer expires) or wait (kind "wait", `reserves` the least offer accepted until each moment, none for today's standing offer)."""
    out = [{"name": names.get(k, k), "key": k, "kind": "accept", "least": club.offers[k], "until": untils[0] if untils else None}
           for k in sorted(option.now, key=lambda k: (-club.offers[k], k))]
    return out + [{"name": names.get(r.player, r.player), "key": r.player, "kind": "wait", "least": r.least[0],
                   "reserves": reserves_doc(untils, [None, *r.least]), "until": untils[-1] if untils else None} for r in option.wait]


def option_doc(club: Club, option: Option, names: dict[str, str], untils: Sequence[str]) -> Doc:
    """An Option as a document: `take_now` and `wait` (terms), `fallback` the names of the cheapest players sold for any cash still missing,
    points_lost and cash_at_lock the expectations, `worst` its points and chance."""
    moves = terms(club, option, names, untils)
    return {"take_now": [t for t in moves if t["kind"] == "accept"], "wait": [t for t in moves if t["kind"] == "wait"],
            "fallback": [names.get(k, k) for k in option.fallback], "points_lost": option.points, "cash_at_lock": option.cash,
            "worst": {"points": option.worst, "p": option.p_worst}}


def days_text(days: Sequence[dt.datetime]) -> str:
    """The days as '25 Nov', '25 and 26 Nov' or '24, 25 and 26 Nov' (the month of each when they differ)."""
    shown_days = [str(d.day) if d.month == days[-1].month else day_text(d) for d in days[:-1]] + [day_text(days[-1])]
    return shown_days[0] if len(shown_days) == 1 else "%s and %s" % (", ".join(shown_days[:-1]), shown_days[-1])


def waiting_days(closes: Sequence[str]) -> list[dt.datetime]:
    """The days the offers drawn at the closes are shown: the day after each close."""
    return [local(c) + dt.timedelta(days=1) for c in closes]


def waiting_text(name: str, standing: float | None, least: Sequence[float], closes: Sequence[str], lock: str) -> str:
    """The sentence of a player waited for: his standing offer is not taken, the new offers are accepted at their least on the days it
    applies (the offers before the last), and at the lock whatever the last is."""
    days, end = waiting_days(closes), local(lock)
    first = "Do NOT take %s's offer of %s today. " % (name, price_text(standing)) if standing is not None else "Wait for the offers of %s. " % name
    body = least[:-1]
    last = "on %s, before %s, accept whatever it is." % (day_text(days[-1]), clock_text(end))
    if not body:
        return first + last.capitalize()
    if max(body) - min(body) < 1.0:
        return first + "On %s accept his new offer if it is %s or more; %s" % (days_text(days[:len(body)]), price_text(body[0]), last)
    return first + "%s; %s" % ("; ".join("on %s accept his new offer if it is %s or more" % (day_text(d), price_text(x)) for d, x in zip(days, body)), last)


def waiting_why(least: Sequence[float], closes: Sequence[str]) -> str:
    """A waiting player's row in words: the least offer accepted on the days it applies, any offer on the last day before the lock."""
    days, body = waiting_days(closes), least[:-1]
    last = "any offer on %s before the lock" % day_text(days[-1])
    return last.capitalize() if not body else "accept a new offer of %s or more on %s, %s" % (
        price_text(max(body)), " or ".join(day_text(d) for d in days[:len(body)]), last)


def advice_text(club: Club, names: dict[str, str], best: Option, second: Option | None, clock: Clock, at: dt.datetime) -> str:
    """The cover in words for a reader: cash and lock, what to accept now (before which close), for each player waited for what to refuse
    and accept day by day, the fallback if cash is still short, the points expected and the worst case; the players of the plan not listed (club.unlisted) are named,
    no offer being drawn for them."""
    lock = local(clock.lock) if clock.lock else None
    today = at.astimezone(MADRID).date()
    out = ["Cash %.1fM%s." % (club.cash / 1e6, "; lock " + lock_text(lock) if lock else "")]
    step = 0
    for k in sorted(best.now, key=lambda k: (-club.offers[k], k)):
        step += 1
        close = local(clock.closes[0]) if clock.closes else None
        when = "tonight" if close is None or close.date() == today else "on " + day_text(close)
        out.append("%d) Accept %s's offer of %s before %s %s." % (step, names.get(k, k), price_text(club.offers[k]), clock_text(close) if close else "the close", when))
    for r in best.wait:
        step += 1
        out.append("%d) %s" % (step, waiting_text(names.get(r.player, r.player), club.offers.get(r.player), r.least, clock.closes, clock.lock or "")))
    if best.wait and best.fallback:
        step += 1
        first = best.fallback[0]
        out.append("%d) If cash is still below 0 after that, sell %s (offer %s), then, if still short, the cheapest other player." % (
            step, names.get(first, first), price_text(pricing_of(club)[first].now)))
    instead = ""
    if second is not None and second.now - best.now:
        instead = " (selling %s today instead: %.1f)" % (" and ".join(names.get(k, k) for k in sorted(second.now - best.now)), second.points)
    out.append("Expected: %.1f points lost%s. Worst case %.1f points, chance %.1f%%." % (best.points, instead, best.worst, 100.0 * best.p_worst))
    unlisted = sorted(club.unlisted & (frozenset(r.player for r in best.wait) | frozenset(best.fallback)))
    if unlisted:
        out.append("Not listed, so no offer is drawn at the close for: %s." % ", ".join(names.get(k, k) for k in unlisted))
    return " ".join(out)


def act_rows(club: Club, plan: Plan, sells: Sequence[Doc], names: dict[str, str], clock: Clock) -> None:
    """Set `action` on each sells row, for a lock ahead with cash below 0: what the user does with the offer by the recommended option (kind
    accept, `until` when the offer expires; wait, until the lock, the reserves in the row's `reserves`; fallback, sell him if the offers fall
    short; keep), and with it the row's `reserves`: the option's for the players it sells, the least each moment for a player accepted now."""
    best = plan.options[0] if plan.feasible and plan.options else None
    untils, clock_closes = untils_of(clock), clock.closes
    by = {t["key"]: t for t in terms(club, best, names, untils)} if best else {}
    for r in sells:
        t = by.get(r["key"])
        if t is not None:
            r["reserves"] = t["reserves"] if t["kind"] == "wait" else reserves_doc(untils[:1], [t["least"]])
            r["action"] = {"kind": t["kind"], "until": t["until"],
                           "why": "in the cover of the cash at the lock: accept the offer of %s before the close" % price_text(t["least"]) if t["kind"] == "accept"
                           else waiting_why(next(w.least for w in (best.wait if best else ()) if w.player == r["key"]), clock_closes)}
        elif r["verdict"] == "sell":
            r["action"] = {"kind": "accept", "until": untils[0] if untils else None, "why": "loses no points; his sale is not needed for cash"}
        elif best is not None and r["key"] in best.fallback:
            r["action"] = {"kind": "fallback", "until": untils[-1] if untils else None,
                           "why": "sell him if the offers fall short of the deficit: the cheapest next (%s points lost)" % ("none" if r["points_lost"] is None else "%.1f" % r["points_lost"])}
        else:
            lost = r["points_lost"]
            r["action"] = {"kind": "keep", "until": None,
                           "why": "no legal eleven without him; a replacement must be bought first" if lost is None
                           else "keeping him protects %.1f points and the cash at the lock is covered without him" % lost}


def cover_lines(names: dict[str, str], club: Club, plan: Plan | None, clock: Clock, sells: Sequence[Doc], at: dt.datetime) -> list[Doc]:
    """The lines of do that bring cash to 0 or more at the lock when it is below 0, from the plan (approach.plan, then approach.secure): one
    "cover" line first for the recommended option (`why` the advice in words, advice_text; `sells` its terms, `options` all the options,
    points_lost and cash_after its expectations, `worst` its worst case with its chance); a "sell" line for each of its sales taken now
    (plan.now), a copy of its row of sells marked "needed" with the points it loses (set_points_lost) and the cash after (set_cash_after), the
    largest offer first; then one "wait" line (n_closes, expected_cover the names waited for, need the money they must raise, cost_points,
    until the lock) when some sale waits for offers. One "short" line with the money the pool lacks when no plan exists. When the credit due
    before the lock (club.inflow, at its expected) brings cash to 0 or more with no sale, one "wait" line for the credit (credit_line). No
    lines when cash is 0 or more or no eleven is fielded."""
    if plan is None or club.cash >= 0:
        return []
    if plan.feasible and not plan.sells and club.inflow.jornadas:
        return [credit_line(club)]
    if not plan.feasible:
        why = "cash %.0f is below 0 and the offers lack %.0f" % (club.cash, plan.short) if plan.short \
            else "cash %.0f is below 0 and no set of the offers brings it to 0 and keeps an eleven" % club.cash
        return [{"what": "short", "label": "short of cash at the lock", "step": "list", "needed": True, "short": plan.short,
                 "why": why, "accepts": [], "act": []}]
    rows = {r["key"]: r for r in sells}
    untils = untils_of(clock)
    taken = sorted((r for k in plan.sells if k in plan.now and (r := rows.get(k)) is not None), key=lambda r: (-r["offer"], r["key"]))
    best = plan.options[0] if plan.options else Option(plan.now, (), (), plan.cost, club.cash + plan.raised + club.inflow.expected, plan.cost, 1.0)
    second = plan.options[1] if len(plan.options) > 1 else None
    before = club.cash + plan.raised
    now_names, later_names = [names.get(k, k) for k in sorted(plan.now)], [names.get(k, k) for k in sorted(plan.sells - plan.now)]
    why = "cash %.1fM; sells %s; expects %+.2fM at the lock%s" % (
        club.cash / 1e6, ", ".join([*("%s now" % n for n in now_names), *("%s later" % n for n in later_names)]), best.cash / 1e6, risk_clause(club, before))
    moves = terms(club, best, names, untils)
    head = {"what": "cover", "label": "cover the cash: " + ", ".join(t["name"] for t in moves), "step": "plan", "needed": True,
            "sells": moves, "options": [option_doc(club, o, names, untils) for o in plan.options], "points_lost": best.points, "cash": club.cash,
            "cash_after": best.cash, "worst": {"points": best.worst, "p": best.p_worst}, "lock": clock.lock,
            "why": advice_text(club, names, best, second, clock, at), "accepts": [], "act": []}
    out = [head] + [{**r, "needed": True, "why": why, "set_points_lost": plan.cost, "set_cash_after": best.cash, **risk_doc(club, before)} for r in taken]
    waiting = sorted(plan.sells - plan.now)
    if waiting and clock.lock:
        names_waiting = [names.get(k, k) for k in waiting]
        out.append({
            "what": "wait", "label": "wait for the offers of " + ", ".join(names_waiting), "step": "wait", "needed": True,
            "n_closes": clock.n_closes, "expected_cover": names_waiting, "until": stamped(clock.lock),
            "need": max(0.0, -(club.cash + club.inflow.expected + sum(club.offers[k] for k in plan.now))),
            "cost_points": plan.cost, **risk_doc(club, before),
            "why": "cash %.1fM; %s waits for the offers still to come (%d closes), each at its reserve or more, whatever it is at the lock%s" % (
                club.cash / 1e6, ", ".join(names_waiting), clock.n_closes, risk_clause(club, before)),
            "accepts": [], "act": []})
    return out


def market_at(read: str | None) -> str | None:
    return stamped(read) if read else None


def sources(health: Iterable[Health]) -> list[Doc]:
    """The sources block of the document: a line for each source health judges, by source."""
    return [{"src": x.src, "at": stamped(x.last_at), "hours": x.hours_since, "status": x.status} for x in sorted(health, key=lambda h: h.src)]


def auction_card() -> Doc:
    run = decision.latest_auction_run()
    if run is None:
        return {"brier": [], "drift": []}
    return {"brier": [{"scope": b.scope, "n": b.n, "model": b.model, "pooled": b.pooled} for b in decision.brier_by_scope(run)],
            "drift": [{"scope": d.scope, "bin": d.bin, "n": d.n, "stated": d.stated, "observed": d.observed}
                      for d in decision.drifting_bins(run)]}


def arrival_rows(got: Sequence[dict[str, Any]], arrivals: Sequence[str]) -> list[dict[str, Any]]:
    """The rows of got whose player is in arrivals, in the order of arrivals (best first), at most TOP_TARGETS."""
    row = {r["player"]: r for r in got}
    return [row[p] for p in arrivals if p in row][:TOP_TARGETS]


def club_at(inp: decision.Inputs, extras: decision.Extras) -> tuple[Club, Rounds]:
    """The Club of the inputs (squad, positions, worths, standing offers, cash, bids pending, the credit due, who is not listed) and the Rounds its sales
    are valued over (the jornadas the next lock counts for, one window for the sales and the buys)."""
    mine = decision.squad_of(inp)
    pos = {p.player: p.pos for p in inp.players if p.pos in POSITIONS}
    _, cells = decision.cells_of(inp)
    worth = {h.player: h.value for h in extras.held}
    steps = decision.rises_of(inp)
    club = Club(mine, pos, worth, {o.player: o.money for o in extras.offers}, inp.money.cash,
                inp.money.team_value, (len(inp.bids), sum(b.amount for b in inp.bids)), debt=inp.money.debt, inflow=inflow_of(inp.credits, mine, pos, cells),
                unlisted=mine - extras.listed,
                drift={k: hurdle.drift(decision.exit_of(inp, k, worth[k], steps.get(k, {}))) for k in mine if k in worth})
    window = decision.window_of(decision.lock_times(inp), cells, None)
    return club, Rounds(cells, window, window)


def board(inp: decision.Inputs, got: list[dict[str, Any]], arrivals: list[str],
          extras: decision.Extras, health: Iterable[Health], offers: worth.OfferDistribution = worth.NO_OFFERS,
          rate: float = hurdle.FALLBACK_LAMBDA) -> Doc:
    """The decisions document of the cut; extras are what the World gives beside the inputs, health the status of each
    source (health.parser_health over the loaded snapshots), offers the nightly offers seen by the cut (offers_at) and
    rate the exchange rate LAMBDA the buys and sales are read at (hurdle.lam), stated as `lambda`."""
    cash = inp.money.cash
    locks = [x.lock for x in inp.locks]
    bids = sum(b.amount for b in inp.bids)
    known = extras.known
    names = {k: fact.name for k, fact in known.items()}
    offered = extras.offers
    club, rounds = club_at(inp, extras)
    pos, cells = club.pos, rounds.cells
    alerts = [{"name": names.get(r["player"], r["player"]), "gain": r["gain"], "asking": r["asking"], "bid": r["bid"],
               "win": r["win"], "placed": r["placed"]} for r in arrival_rows(got, arrivals)]
    money = Money(rate, len(rounds.arrives))
    base = approach(club, None, 0.0, 1.0, rounds, money=money)
    clock = decision.clock_of(inp)
    side = Side(club.squad, pos, cells, rounds.arrives)
    found = plan(club, base, rounds, clock.n_closes, side, rate=rate)
    if found is not None and club.cash < 0:
        found = secure(club, found, side, offers, clock.n_closes, rate)
    buys, plans, rejected = buy_lines(inp, names, club, got, plan_cost(base, found, club), rate)
    sells = offer_lines(inp, names, offered, known, offers, rate, clock.n_closes, untils_of(clock))
    funds(buys, sells)
    replacement_rows(inp, names, club, got, sells, rate)
    settle(sells, found.sells if found is not None and found.feasible else frozenset(), found.now if found is not None else frozenset())
    at = snapshot_stamp(cut())
    assert at is not None
    owed = cover_lines(names, club, found, clock, sells, at) if locks else []
    if locks and club.cash < 0 and found is not None:
        act_rows(club, found, sells, names, clock)
    sold = (found.sells if found is not None and found.feasible else frozenset()) | {s["key"] for s in sells if s["verdict"] == "sell"}
    squad = squad_rows(inp, names, sold)
    return {
        "generated_at": stamped(cut()), "market_at": market_at(extras.market_read), "sources": sources(health),
        "lock_at": min(locks).replace("Z", ":00+00:00") if locks else None,
        "cash": cash, "cash_locked": bids, "can_bid": max(0.0, bid_limit(cash, club.team_value, club.debt) - bids),
        "ping": "on the market: " + ", ".join("%s (+%.0f points)" % (a["name"], a["gain"]) for a in alerts) if alerts else "",
        "do": owed + list_line(inp, names, extras), "lineup": lineup_doc(inp, names, sold), "buys": buys, "plans": plans, "rejected": rejected, "considered": len(rejected),
        "base": base_doc(club, names, base, found), "targets": target_lines(names, got), "lambda": rate,
        "alerts": alerts, "auction": auction_card(),
        "sells": sells,
        "squad": [{k: v for k, v in r.items() if k != "key"} for r in squad]}


# The constants a run is built with, by module: what params records as such.
NAMES: dict[str, tuple[str, ...]] = {
    "tidy": ("APP_FRESH_HOURS", ),
    "forecast": ("ABSENT_START", "FIXTURE_K", "MIN_POOL", "NEUTRAL_START", "PERSISTENT_SHARE", "PICK_K", "P_DECAY", "P_LIFT", "P_LISTING", "SHRINK_K"),
    "pricing": ("AUCTION_MIN", "CLIP", "PRICE_FOCUS", "PRICE_W0", "PRICE_W1", "PRICE_W2", "PRICE_WINDOW"),
    "rules": ("DEBT_SHARE", "MATCH_LEN"),
    "hurdle": ("FALLBACK_LAMBDA", "WIN_FLOOR", "POOL_DAYS", "MIN_DAYS", "TREND_CAP"),
}
MODULES: dict[str, Any] = {"tidy": tidy, "forecast": model, "pricing": pricing,
                           "rules": rules, "hurdle": hurdle}


def constants() -> dict[str, dict[str, float]]:
    """The constants in force now, by module (a variant applies its own)."""
    return {m: {n: getattr(MODULES[m], n) for n in names} for m, names in NAMES.items()}


def params(fits: Sequence[dict[str, Any]], ratios: Sequence[float],
           carry: Sequence[float]) -> list[dict[str, Any]]:
    """Every constant and fitted value a run is built with, one row each:
    (component, term, value, kind, n); fits are the forecast's, as fit_rows gives them."""
    return ([_param(m, name, v, "constant") for m, names in constants().items()
             for name, v in names.items()]
            + list(fits)
            + [_param("pricing.offer_ratios", t, percentile(ratios, p), n=len(ratios))
               for t, p in (("p10", 10), ("median", 50), ("p90", 90)) if ratios]
            + [_param("pricing.carry", str(h), v) for h, v in enumerate(carry, 1)])


def fit_rows(ratings: Iterable[Any]) -> list[dict[str, Any]]:
    """One params row for each club's attack and defence, clubs in club order; ratings have club, attack, defence."""
    return [_param("forecast.%s" % t, r.club, v) for r in sorted(ratings, key=lambda r: r.club)
            for t, v in (("attack", r.attack), ("defence", r.defence))]


def world_params(world: state.World, fc: Any) -> list[dict[str, Any]]:
    """params of the cut at the World: the forecast's club ratings (fc.rating), the offers over value
    (current.offer_ratios) and the share of a price trend made after each update (price_fold.price_carry, by horizon)."""
    carry = tuple(c.carry for c in sorted(price_fold.price_carry(price_fold.price_state(world)), key=lambda c: c.h))
    return params(fit_rows(fc.rating.values()), [r.ratio for r in current.offer_ratios(world)], carry)


class Moment(NamedTuple):
    """The cut's decision (Decided, or Stale with its reason) and the params it is made with ([] when stale)."""
    decided: decision.Decided | decision.Stale
    params: list[dict[str, Any]]


def moment_at(loaded: loader.Loaded, world: state.World) -> Moment:
    """decision.decision_at and, for a decision made, its params, from one World."""
    d = decision.decision_at(loaded, world)
    return Moment(d, [] if isinstance(d, decision.Stale) else world_params(world, d.fc))


def _param(component: str, term: str, value: float, kind: str = "fitted",
           n: int | None = None) -> dict[str, Any]:
    return {"component": component, "term": term, "value": float(value), "kind": kind, "n": n}


def main(records: RunRecords | None = None, obs: ob.Observations | None = None) -> None:
    """The board stage: decisions.json of the cut, and the run's params, opportunity and offer records added to records (the
    board is the run's: run it as run.py board); obs is the parse cache as parsed.read gives it, read here when None."""
    if records is None:
        raise SystemExit("board: the board is the run's: run it as run.py board")
    loaded = loader.load(league.load_config(), cut(), previous=loader.previous_opportunities(), obs=obs)
    [((d, rows), offers, standing)] = state.sweep(
        loaded.obs, [cut()], lambda world: (moment_at(loaded, world), offers_at(world), current.offers(world)))
    if isinstance(d, decision.Stale):
        raise SystemExit(d.reason)
    managers = len(d.seen.cash)
    left = d.rounds_left
    if managers < 2 or not left:
        print("board: nothing to decide (%d managers, %d jornadas left)" % (managers, len(left)))
        return
    _, cells = decision.cells_of(d.inputs)
    rate = hurdle.lam(loader.daily_opportunities(hurdle.since(cut()), cut()), cut(), max(cells, default=0))
    records.add("params", rows + [_param("hurdle", "lambda", rate)])
    got = decision.opportunity_rows(d.inputs, rate)
    records.add("dec_opportunity", [{"cut": cut(), **{c: r[c] for c in loader.OPPORTUNITY_COLUMNS if c != "cut"}} for r in got])
    doc = board(d.inputs, got, decision.arrivals(got, loaded.previous), d.extras, loader.health_at(loaded, cut()), offers, rate)
    records.add("dec_offer", offer_rows(cut(), doc["sells"], stood_offers(d.extras.held, standing)))
    REPORTS.mkdir(exist_ok=True)
    (REPORTS / "decisions.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("wrote %s" % (REPORTS / "decisions.json"))


def _selftest_params() -> None:
    rows = params([_param("forecast.attack", "a", 1.2), _param("forecast.defence", "a", 0.8)],
                  (0.9, 1.0, 1.1), (0.5, 1.0))
    got = {(r["component"], r["term"]): r for r in rows}
    assert len(got) == len(rows), "one row a parameter"
    assert {r["kind"] for r in rows} == {"fitted", "constant"}
    for key, value in [(("pricing.offer_ratios", "median"), 1.0),
                       (("pricing.carry", "2"), 1.0),
                       (("forecast.attack", "a"), 1.2),
                       (("forecast.defence", "a"), 0.8),
                       (("rules", "DEBT_SHARE"), rules.DEBT_SHARE)]:
        assert got[key]["value"] == value, (key, got.get(key))
    assert got[("pricing.offer_ratios", "median")]["n"] == 3
    assert got[("rules", "DEBT_SHARE")]["kind"] == "constant"


def _selftest_world() -> None:
    """The params and the extras read from a World are the selectors' own values, and any cut alone equals the cut inside
    a sweep."""
    from ffcore import forecast_facts
    from ffcore.decision_facts import _CUTS, _ST, _scratch
    obs, ident, lg = _scratch()
    loaded = loader.Loaded(obs, ident, lg, forecast_facts.knobs(), ())
    world = state.world_at(obs, _ST)
    got = moment_at(loaded, world)
    d = got.decided
    assert isinstance(d, decision.Decided) and got.params == world_params(world, d.fc)
    clubs = sorted(d.fc.rating)
    assert [r["term"] for r in got.params if r["component"] == "forecast.attack"] == clubs, "a row a club, in club order"
    assert [r["value"] for r in got.params if r["component"] == "forecast.defence"] == [d.fc.rating[c].defence for c in clubs]
    ratios = [r.ratio for r in current.offer_ratios(world)]
    assert [r for r in got.params if r["component"] == "pricing.offer_ratios"] == [
        r for r in params([], ratios, ()) if r["component"] == "pricing.offer_ratios"]
    carry = price_fold.price_carry(price_fold.price_state(world))
    assert [r["value"] for r in got.params if r["component"] == "pricing.carry"] == [c.carry for c in sorted(carry, key=lambda c: c.h)]
    assert state.sweep(obs, _CUTS, lambda w: moment_at(loaded, w)) == [moment_at(loaded, state.world_at(obs, c)) for c in _CUTS]
    assert isinstance(moment_at(loaded, state.world_at(obs, _CUTS[0])).decided, decision.Stale)
    assert moment_at(loaded, state.world_at(obs, _CUTS[0])).params == [], "no decision, no params"
    mine = decision.squad_of(d.inputs)
    hand = decision.Extras(dict(d.extras.known), frozenset(d.extras.held_names), list(d.extras.offers), list(d.extras.held),
                           frozenset(d.extras.listed), d.extras.market_read)
    names = {k: f.name for k, f in hand.known.items()}
    assert list_line(d.inputs, names, hand) == list_line(d.inputs, names, d.extras)
    listed_all = hand._replace(listed=frozenset(mine))
    assert list_line(d.inputs, names, listed_all) == [], "every player of yours listed: nothing to list"
    assert market_at(None) is None and market_at("2026-10-30T1809Z") == "2026-10-30T18:09Z"


def _inp_of(squad: dict[str, str], market: dict[str, str] | None = None) -> decision.Inputs:
    ks = sorted(squad) + sorted(market or {})
    pos = squad | (market or {})
    return decision.Inputs(decision.Money(0.0, 0.0, 0.0, 0, 0),
                           players=[decision.Player(k, pos[k], int(k in squad), None if k in squad else 1e6) for k in ks],
                           cells=[decision.Cell(k, j, 1.0 + i % 7 + j / 10) for i, k in enumerate(ks) for j in range(1, 11)])


def _sells_of(squad: dict[str, str], offered: Iterable[str]) -> list[Doc]:
    names = {k: k for k in squad}
    return offer_lines(_inp_of(squad), names, [decision.Offer(k, 5e6, 5e6 + 1e5 * i) for i, k in enumerate(offered)], {})


def _selftest_sells() -> None:
    shape = {"POR": 1, "DEF": 4, "MED": 4, "DEL": 2}
    squad = {"%s%d" % (p, i): p for p, n in shape.items() for i in range(n)}
    bare = _sells_of(squad, squad)
    assert len(bare) == len(squad) and all(r["gain"] is None and r["per_million"] is None for r in bare), \
        "eleven players only: no row carries a number"
    assert all(r["delta"]["points"] is None and set(r["delta"]["window"]) == set(READERS) and set(r["delta"]["window"].values()) == {None}
               and not r["feasible"] and r["why_infeasible"] == "no_eleven" for r in bare), \
        "a squad of exactly 11: every sale says no legal eleven, with no number and so never two equal ones"
    assert all(f[1] is None for r in bare for f in r["facts"] if f[2] == "points"), "no points in the facts of a sale with no Delta"
    assert not any(k in r for r in bare for k in ("note", "lost")), "the page reads fields, never prose"
    assert [r["key"] for r in bare] == sorted(squad), "rows with no figure: in key order"
    rows = [{"what": "sell", "key": r["key"], "delta": r["delta"], "feasible": r["feasible"], "offer": r["offer"], "facts": r["facts"],
             "ratio": r["ratio"], "offer_percentile": None, "offers_seen": 0, "trend": None, "for": []} for r in bare]
    made = offer_rows("cut", rows, [Stood(r["key"], r["offer"], "id" + r["key"], "c") for r in rows])
    assert all(m["delta_next4"] is None and m["delta_season"] is None and m["fields"] is False and m["gap"] for m in made), \
        "the record states no number and no legal eleven for a sale with none, and the jornadas it leaves without"
    backed = {**squad, "DEF9": "DEF", "MED9": "MED"}
    rows = _sells_of(backed, backed)
    figures = [r for r in rows if r["feasible"]]
    assert figures and rows[:len(figures)] == figures and not any(r["feasible"] for r in rows[len(figures):]), "figures first"
    assert all(r["per"] == "season" and r["gain"] == r["delta"]["points"] for r in figures), "one window in a row"
    assert len({tuple(r["delta"]["window"].values()) for r in figures}) > 1, "the figures differ per player"
    assert not next(r for r in rows if r["key"] == "POR0")["feasible"], "the only keeper with no bench keeper"
    for r in rows:
        d = r["delta"]
        assert r["feasible"] == (d["points"] is not None) and (r["why_infeasible"] is None) == r["feasible"], "typed and untyped agree"
        if d["points"] is None:
            assert d["gap"] and set(d["window"].values()) == {None}
            continue
        assert d["points"] == d["window"]["season"] == r["gain"] and d["gap"] == [] and all(v <= 1e-9 for v in d["window"].values())
    made = offer_rows("cut", [{**r, "for": []} for r in rows], [Stood(r["key"], r["offer"], "id" + r["key"], "c") for r in rows])
    assert [m["delta_season"] for m in made] == [r["delta"]["points"] for r in rows]
    assert [m["fields"] for m in made] == [r["feasible"] for r in rows]
    assert all(set(m) == set(rec.DecOffer._fields) for m in made)


def _selftest_settle() -> None:
    rng = random.Random(21)
    for _ in range(400):
        rows: list[Doc] = [{"key": "p%d" % i, "verdict": rng.choice(["sell", "hold", "needs_replacement"]), "points_lost": rng.choice([0.0, 2.0, None]),
                            "for": rng.choice([[], ["B"]]), "ratio": rng.choice([0.9, 1.0, 1.1, None]), "offer_percentile": rng.choice([0.1, 0.5, None]),
                            "per_million": rng.choice([None, 0.0, 1.0, 2.0])} for i in range(rng.randint(0, 8))]
        before = {r["key"]: r["verdict"] for r in rows}
        cover = frozenset(r["key"] for r in rows if rng.random() < 0.3)
        settle(rows, cover)
        for r in rows:
            assert r["verdict"] == before[r["key"]] or before[r["key"]] == "sell", "only a sell is settled"
            if r["verdict"] in ("sell", "wait") and (r["points_lost"] or 0.0) > 0:
                assert r["for"] or r["key"] in cover, "a sale that loses points is made only when its cash has a use"
            if r["verdict"] == "sell":
                assert worth.take_now(r["ratio"], r["offer_percentile"], bool(r["for"])), "a sell is taken tonight"
        assert [r["verdict"] == "sell" for r in rows] == sorted((r["verdict"] == "sell" for r in rows), reverse=True), "sells first"


def _selftest_replacements() -> None:
    shape = {"POR": 1, "DEF": 4, "MED": 4, "DEL": 2}
    squad = {"%s%d" % (p, i): p for p, n in shape.items() for i in range(n)}
    market = {"%s%d" % (p, i): p for p in shape for i in range(10, 12)}
    inp = _inp_of(squad, market)._replace(curves=[decision.CurvePoint(k, r, p) for k in sorted(market) for r, p in ((1.0, 0.5), (1.2, 0.8), (1.5, 1.0))])
    names = {k: k for k in squad | market}
    pos, cells = decision.cells_of(inp)
    got: list[dict[str, Any]] = [{"player": k, "pos": p, "status": "market", "asking": 1e6, "value": 1e6, "bid": 1.2e6, "win": 0.5 + 0.1 * (i % 4), "close_at": None,
           "stake": hurdle.stake(5.0, 26, 0.045, hurdle.Exit(1e6), 26), "profit": 0.0}
           for i, (k, p) in enumerate(sorted(market.items()))]
    offers = {k: 5e6 + 1e5 * i for i, k in enumerate(sorted(squad))}
    for cash, why in ((50e6, None), (-50e6, "no_cash")):
        club = Club(frozenset(squad), pos, {k: 4e6 for k in squad}, offers, cash, 1e8)
        rows = offer_lines(inp, names, [decision.Offer(k, 4e6, offers[k]) for k in sorted(squad)], {})
        replacement_rows(inp, names, club, got, rows, 0.0)
        for r in rows:
            assert r["feasible"] or r["replacement"] is not None or r["replacement_why_not"], "a number, a replacement or the reason why not"
            assert r["replacement_why_not"] == why and (r["replacement"] is None) == (why is not None)
            if why is not None:
                continue
            pair = r["replacement"]
            assert pair["sell"] == r["name"] and pair["feasible"] and pair["why_infeasible"] is None
            bought = pair["key"]
            assert eleven.points((frozenset(squad) - {r["key"]}) | {bought}, pos, {}) is not None, "the pair leaves a legal eleven"
            window = decision.window_of({}, cells, None)
            curves = decision.curves_of(inp)

            def net_of(g: dict[str, Any], r: Doc = r) -> float | None:
                def fund(amount: float, win: float) -> Approach:
                    return replacement(club, r["key"], g["player"], amount, win, Rounds(cells, window, window))
                offer = bidding(curves[g["player"]], 1e6, fund, 0.0).best
                return None if offer is None else offer.found.net
            best = max(x for g in got if eleven.points((frozenset(squad) - {r["key"]}) | {g["player"]}, pos, {}) is not None
                       and (x := net_of(g)) is not None)
            assert abs(pair["net"] - best) < 1e-9, "the pair is the best net of the candidates"
            assert pair["delta"]["points"] is not None and pair["funding"]["feasible"] and pair["cash_after"] >= -1e-6
    none = offer_lines(inp, names, [decision.Offer("POR0", 4e6, 4e6)], {})
    replacement_rows(inp, names, Club(frozenset(squad), pos, {k: 4e6 for k in squad}, {"POR0": 4e6}, 5e7, 1e8), [], none)
    assert none[0]["replacement"] is None and none[0]["replacement_why_not"] == "no_candidate", "no player of the market fits"


def _selftest_one_meaning() -> None:
    for seed in range(60):
        for ties in (False, True):
            fielded, bench, pos, value = eleven.random_league(random.Random(seed), ties)
            squad = fielded | bench
            cells = {1: value, 2: {k: 9.0 - v for k, v in value.items()}, 3: {k: float(i % 5) for i, k in enumerate(sorted(value))}}
            window = (1, 2, 3)
            side = Side(squad, pos, cells, window)
            for p in squad:
                sale = valuation.Move(frozenset([p]), frozenset(), None, None)
                got = valuation.delta(squad, sale, window, {}, pos, cells).points
                paid = side.cost(frozenset([p]))
                assert (got is None) == (paid is None), "no legal eleven without him: one meaning in worth and approach"
                if got is not None and paid is not None:
                    assert abs(paid + got) < 1e-9, "the memo's cost is the Delta of the sale, negated"


def _selftest_alerts() -> None:
    rows = [{"player": "r%d" % i, "gain": 50.0 - i} for i in range(TOP_TARGETS + 5)] + [{"player": "new", "gain": 0.5}]
    assert [r["player"] for r in arrival_rows(rows, ["new"])] == ["new"], "an arrival ranking low among all gains still alerts"
    assert arrival_rows(rows, []) == [], "no arrivals, no alerts"
    assert arrival_rows(rows, ["ghost"]) == [], "only rows of got alert"
    many = ["r%d" % i for i in range(TOP_TARGETS + 3)]
    assert [r["player"] for r in arrival_rows(rows, many)] == many[:TOP_TARGETS], "best first, capped"
    assert {r["player"] for r in arrival_rows(rows, ["new", "r2"])} <= {"new", "r2"}, "a non-arrival never alerts"


def _selftest_overlap() -> None:
    rng = random.Random(5)
    shape = {"POR": 1, "DEF": 4, "MED": 4, "DEL": 2}
    squad = {"%s%d" % (p, i): p for p, n in shape.items() for i in range(n)}
    market = {"%s%d" % (p, i): p for p in shape for i in range(10, 13)}
    falls = 0
    for _ in range(30):
        inp = _inp_of(squad, market)
        late = rng.choice(sorted(market))
        inp = inp._replace(universe=[decision.Candidate(k, p, "market", 1e6, 1e6) for k, p in market.items()],
                           locks=[decision.Lock(1, "2026-11-06T20:00Z"), decision.Lock(2, "2026-11-13T20:00Z")],
                           closes=[decision.Close(late, "2026-11-06T21:24Z")],
                           cells=[decision.Cell(c.player, c.jornada, rng.choice([0.0, 1.0, 3.0, 6.0, 9.0])) for c in inp.cells])
        rows = decision.opportunity_rows(inp)
        got = {r["player"]: r for r in rows}
        lines: list[Doc] = [{"key": r["player"], "gain": r["gain"], "facts": []} for r in rows]
        overlapped(inp, lines, got)
        if not lines:
            continue
        assert lines[0]["gain_with_better"] == lines[0]["gain"] and not lines[0]["facts"], "the first line is its own gain"
        pos, cells = decision.cells_of(inp)
        mine = decision.squad_of(inp)
        for i, b in enumerate(lines):
            above = frozenset(x["key"] for x in lines[:i])
            window = got[b["key"]]["window"]

            def value(extra: frozenset[str]) -> float:
                return sum(eleven.points(mine | extra, pos, cells[j]) or 0.0 for j in window)

            assert abs(b["gain_with_better"] - (value(above | {b["key"]}) - value(above))) < 1e-9, "the gain over the squad with the buys above, over his own window"
            assert b["gain_with_better"] >= -1e-9 and (b["gain_with_better"] < b["gain"]) == bool(b["facts"]), "a fact exactly when the gain falls"
            falls += bool(b["facts"])
    assert falls > 0, "an overlap was exercised"


def _selftest_placed() -> None:
    rng = random.Random(21)
    shape = {"POR": 2, "DEF": 5, "MED": 5, "DEL": 3}
    squad = {"%s%d" % (p, i): p for p, n in shape.items() for i in range(n)}
    market = {"%s%d" % (p, i): p for p in shape for i in range(10, 14)}
    names = {k: k for k in squad | market}
    ratios = (0.9, 1.0, 1.02, 1.05, 1.1, 1.2, 1.3, 1.5)
    moves: set[str] = set()
    reasons: set[str | None] = set()
    overs = nolabel = advised_n = 0
    for _ in range(40):
        keys = sorted(market)
        curves = [decision.CurvePoint(k, r, min(1.0, rng.choice([0.0, 0.1, 0.3]) + (r - 1.0) * rng.choice([0.5, 2.0, 5.0]))) for k in keys for r in ratios]
        mine = {k: rng.choice([10e6, 10.4e6, 13e6, 25e6]) for k in rng.sample(keys, rng.randint(0, len(keys)))}
        inp = _inp_of(squad, market)._replace(curves=curves, bids=[decision.Bid(k, a) for k, a in sorted(mine.items())])
        pos, cells = decision.cells_of(inp)
        offered = rng.sample(sorted(squad), 5)
        club = Club(frozenset(squad), pos, {k: 4e6 for k in squad}, {k: rng.choice([3.5e6, 4e6, 4.5e6]) for k in offered}, rng.choice([5e7, 0.0, -1e7]), 1e8)
        rate = rng.choice([0.0, 0.045, 0.1, 0.3])
        got: list[dict[str, Any]] = [{"player": k, "pos": market[k], "status": "market", "asking": 10e6, "value": rng.choice([10e6, 10e6, 6e6, 4.3e6]), "bid": None, "win": None, "close_at": None,
                "replaces": "POR0", "gain": rng.choice([0.0, 0.5, 3.0, 20.0, 60.0]), "per_million": None, "from_j": 1, "price_next": None,
                "price_lock": None, "placed": mine.get(k), "profit": 0.0, "window": decision.window_of({}, cells, None)} for k in keys]
        for r in got:
            r["stake"] = hurdle.stake(r["gain"], hurdle.remaining(1, max(cells)), rate or 0.045, hurdle.Exit(r["asking"]), 1)
        shown, plans, left = buy_lines(inp, names, club, got, 0.0, rate)
        assert [b["key"] for b in shown if b["done"]] == [b["key"] for b in shown[:len(mine)]], "a bid placed comes before the ranked rows"
        assert {b["key"] for b in shown if b["done"]} == set(mine), "a bid placed is always a row of the buys"
        free = [r["player"] for r in got if r["placed"] is None and r["gain"] > 0]
        assert sorted([p["key"] for p in plans] + [x["key"] for x in left]) == sorted(free), "every candidate is a funded plan or in the rejected, once: none dropped silently"
        assert all(x["reason"] in REASONS and x["why"] for x in left), "a rejected candidate says why"
        reasons |= {x["reason"] for x in left}
        assert all(p["net"] > FUNDED_FLOOR and p["line"] for p in plans), "a funded plan nets above the floor and is one line"
        assert [(-p["net"], -p["gain"], p["name"]) for p in plans] == sorted((-p["net"], -p["gain"], p["name"]) for p in plans), "ranked by net"
        assert [b["key"] for b in shown if not b["done"]] == [p["key"] for p in plans][:TOP_BUYS], "the buys are the funded plans, best first"
        advised_n += len(plans)
        assert all(p["walk_away"] is None or p["bid"] <= p["walk_away"] + 1e-6 or p["net"] >= 0 for p in plans), "a plan is never bid above its zero-net price while the net there is negative"
        curve_of = decision.curves_of(inp)
        for b in shown[:len(mine)]:
            cap = b["walk_away"] if b["walk_away"] is not None else 0.0
            want = hurdle.stand(curve_of[b["key"]], 10e6, mine[b["key"]], cap)
            assert (b["amount"], b["move"], b["to"]) == (mine[b["key"]], want.move, want.to)
            assert b["win"] == chance(curve_of[b["key"]], mine[b["key"]] / 10e6) and b["why"] and b["yield"] is None
            assert not any(w in b["why"].lower() for w in ("take", "sell it")), "no verdict word the page forbids"
            over = over_walk_away(b["amount"], cap)
            assert over == (("%+.1f" % b["net"] in b["why"]) if b["net"] is not None else ("(%s)" % b["net_label"] in b["why"])), "a bid over the walk-away says its net or why it has none"
            overs += over and b["net"] is not None
            nolabel += over and b["net"] is None
            moves.add(b["move"])
        assert all(b["why"] is None for b in shown[len(mine):]), "no words for a bid not placed"
    assert moves >= {"hold", "lower", "withdraw"}, moves
    assert overs > 0, "the net of a bid over the walk-away was exercised"
    assert nolabel > 0, "a bid over the walk-away with no net was exercised"
    assert {None, "net_not_positive"} <= reasons or {"net_not_positive", "unfundable"} <= reasons or len(reasons) >= 2, reasons
    assert advised_n > 0 and "net_not_positive" in reasons, "funded plans and a rejected net were exercised"


def _selftest_worked_case() -> None:
    """The case that opened #154: a listing whose asking price is over its W walk-away price (the old screen dropped it, so no plan was
    ever looked at) is funded by a sale after the result and listed with its net, bid and zero-net price."""
    shape = {"POR": 2, "DEF": 5, "MED": 5, "DEL": 3}
    squad = {"%s%d" % (p, i): p for p, n in shape.items() for i in range(n)}
    market = {"star": "DEL"}
    names = {k: k for k in squad | market}
    rows = {k: 4.0 for k in squad} | {"DEL0": 14.0, "DEL1": 13.0, "DEL2": 12.0, "MED0": 9.0, "star": 15.5}
    inp = _inp_of(squad, market)._replace(
        cells=[decision.Cell(k, j, v) for k, v in rows.items() for j in range(1, 11)],
        curves=[decision.CurvePoint("star", r, p) for r, p in ((1.0, 0.95), (1.05, 0.98), (1.1, 1.0), (1.3, 1.0))])
    pos, cells = decision.cells_of(inp)
    n, rate, asking = 10, 0.3, 40e6
    club = Club(frozenset(squad), pos, {k: 20e6 for k in squad}, {"DEL2": 20e6}, -2e6, 4e8)
    window = decision.window_of({}, cells, None)
    gain = valuation.Buyer(club.squad, window, pos, cells).gain("star")
    stake = hurdle.stake(gain, n, rate, hurdle.Exit(asking, 1, 0.0), n)
    assert hurdle.cap(stake) < asking, "the old screen would drop him: his walk-away price is under his asking price"
    row = {"player": "star", "pos": "DEL", "status": "market", "asking": asking, "value": asking, "bid": None, "win": None, "close_at": None, "replaces": "DEL2",
           "gain": gain, "per_million": gain / (asking / 1e6), "from_j": 1, "price_next": None, "price_lock": None, "placed": None, "profit": 0.0,
           "window": window, "stake": stake}
    shown, plans, left = buy_lines(inp, names, club, [row], 0.0, rate)
    assert left == [] and [p["key"] for p in plans] == ["star"], "he is a funded plan, not a count of the considered"
    (plan_,) = plans
    assert plan_["net"] > FUNDED_FLOOR and plan_["bid"] >= asking and plan_["walk_away"] > asking, "a net above the floor at the asking price and a zero-net price over it"
    assert plan_["funding"]["if_won"] or plan_["funding"]["tonight"], "his plan sells somebody"
    assert plan_["line"].startswith("buy star at") and "funded by selling" in plan_["line"]


def _selftest_cover() -> None:
    inp = decision.Inputs(decision.Money(0.0, 0.0, 0.0, 0, 0))
    rng = random.Random(14)
    shape = ["POR"] * 2 + ["DEF"] * 4 + ["MED"] * 4 + ["DEL"] * 3
    seen_take = seen_wait = seen_empty = seen_credit = 0
    for case in range(120):
        own = {"m%02d" % i: p for i, p in enumerate(shape)}
        points = {k: rng.choice([0.0, 0.0, 1.0, 2.0, 4.0]) for k in own}
        value = {k: rng.choice([2e6, 4e6, 6e6]) for k in own}
        offers = {k: value[k] * rng.choice([0.9, 1.0, 1.1]) for k in rng.sample(sorted(own), rng.randint(0, 7))}
        cash = rng.choice([-9e6, -5e6, -1e6, 0.0, 2e6])
        due = rng.choice([1e6, 3e6, 6e6, 12e6])
        spread = rng.choice([0.0, 2e6, 5e6])
        club = Club(frozenset(own), own, value, offers, cash, 5e7,
                    inflow=rng.choice([NONE, Inflow(due, max(0.0, due - spread), "2026-11-24T03:40Z", (13,), spread / 1.2816)]))
        low = club.inflow.expected
        n_closes = rng.choice([0, 1, 3, 6])
        clock = Clock("2026-11-27T2000Z", tuple("2026-11-%02dT2124Z" % (26 - n_closes + 1 + i) for i in range(n_closes)))
        dist = rng.choice([worth.NO_OFFERS, worth.offer_ratio_distribution([0.9, 0.95, 1.0, 1.0, 1.05, 1.1])])
        rounds = Rounds({1: points}, valuation.Window((1,)), valuation.Window((1,)))
        base_plan = plan(club, approach(club, None, 0.0, 1.0, rounds), rounds, n_closes, rate=hurdle.FALLBACK_LAMBDA)
        assert base_plan is not None
        sells = [{"what": "sell", "key": k, "name": k, "offer": offers[k], "verdict": "sell", "points_lost": 0.0,
                  "for": [k] if rng.random() < 0.15 else []} for k in sorted(offers)]
        found = secure(club, base_plan, Side(club.squad, own, {1: points}, (1,)), dist, n_closes) if cash < 0 else base_plan
        listed = frozenset(offers) | frozenset(rng.sample(sorted(own), rng.randint(0, len(own))))
        club = club._replace(unlisted=frozenset(own) - listed)
        at = dt.datetime(2026, 11, 26 - n_closes, 9, 0, tzinfo=dt.timezone.utc)
        rows = cover_lines({k: k for k in own}, club, found, clock, sells, at)
        if cash >= 0:
            assert rows == [], "cash 0 or more: no line, the board is unchanged"
            seen_empty += 1
            continue
        if not found.feasible:
            assert [r["what"] for r in rows] == ["short"] and rows[0]["short"] == found.short >= 0
            continue
        if cash + low >= 0:
            assert not found.sells and rows == [credit_line(club)] and club.inflow.jornadas, \
                "the credit due, expected, brings cash to 0 or more: nothing to sell, one line that says why"
            assert rows[0]["credit_expected"] >= -cash and rows[0]["credit_expected"] >= rows[0]["credit_low"] and not rows[0]["needed"]
            risk = rows[0]["risk"]
            assert 0.0 <= risk["p_short"] <= 1.0 and risk["shortfall_if_low"] == max(0.0, -cash - club.inflow.low) and risk["low_inflow"] == club.inflow.low
            assert (risk["shortfall_if_low"] > 0) <= ("stays below 0" in rows[0]["why"]), "a shortfall at the low case is told in words"
            seen_credit += 1
            continue
        side = Side(club.squad, own, {1: points}, (1,))
        head, *rest = rows
        assert head["what"] == "cover" and abs(head["points_lost"] - found.cost) < 1e-9, "the recommended option is the headline"
        takes_ = [r for r in rest if r["what"] == "sell"]
        waits = [r for r in rest if r["what"] == "wait"]
        assert len(waits) <= 1 and len(takes_) + len(waits) == len(rest)
        assert len(head["options"]) == len(found.options) and all(a["points_lost"] == b.points for a, b in zip(head["options"], found.options))
        assert found.sells >= found.now and found.now <= frozenset(offers)
        assert all(o["cash_at_lock"] >= -1e-6 for o in head["options"]), "every option leaves cash 0 or more at the lock in expectation"
        assert all(len(w["reserves"]) == n_closes + 1 and w["reserves"][0]["least"] is None and w["reserves"][-1]["until"] == "2026-11-27T20:00Z"
                   for o in head["options"] for w in o["wait"]), "a reserve for each offer still to come, none for today's, the last until the lock"
        assert head["why"].startswith("Cash ") and "Expected: " in head["why"], "the advice in words"
        if found.options:
            unlisted = (frozenset(r.player for r in found.options[0].wait) | frozenset(found.options[0].fallback)) - listed
            assert ("Not listed" in head["why"]) == bool(unlisted), "the advice names what is not listed"
        assert {r["key"] for r in takes_} == found.now and all(r["set_points_lost"] == found.cost for r in takes_)
        assert [r["offer"] for r in takes_] == sorted((r["offer"] for r in takes_), reverse=True), "the largest offer first"
        assert all(r["offer"] > 0 for r in takes_), "a sale that raises no cash is not in the cover"
        assert {t["key"] for t in head["sells"]} == (found.sells if found.options else found.now) and all(t["least"] >= 0 for t in head["sells"])
        assert all(t["until"] == (untils_of(clock)[0] if t["kind"] == "accept" else untils_of(clock)[-1]) for t in head["sells"])
        if n_closes == 0:
            assert not waits and {r["key"] for r in takes_} == found.sells <= frozenset(offers), "no close left: every sale of the plan is taken"
            pool = sorted(offers)
            owed = [c for m in range(len(pool) + 1) for b in combinations(pool, m)
                    if sum(offers[k] for k in b) >= -(cash + low) - 1e-6 and (c := side.cost(frozenset(b))) is not None]
            assert abs(base_plan.cost - min(owed)) < 1e-9, "at n_closes 0 the chosen set is the cheapest over the standing offers"
        for w in waits:
            assert n_closes >= 1 and w["n_closes"] == n_closes and w["until"] == "2026-11-27T20:00Z"
            assert w["cost_points"] == found.cost and w["need"] >= 0
            assert set(w["expected_cover"]) == found.sells - found.now
            seen_wait += 1
        seen_take += len(takes_)
    assert seen_take >= 5 and seen_wait >= 3 and seen_empty >= 5 and seen_credit >= 3, (seen_take, seen_wait, seen_empty, seen_credit)
    squad = {"%s%d" % (p, i): p for p, n in {"POR": 2, "DEF": 5, "MED": 5, "DEL": 3}.items() for i in range(n)}
    inp = _inp_of(squad)
    names = {k: k for k in squad}
    for case in range(20):
        rng = random.Random(case)
        sold = frozenset(rng.sample(sorted(squad), 3))
        formation = {"POR": 1, "DEF": 4, "MED": 4, "DEL": 2}
        fielded = [k for p, n in formation.items() for k in rng.sample(sorted(k for k in squad if squad[k] == p), n)]
        inp = inp._replace(lineup=[decision.Slot(k, k, squad[k], k) for k in fielded]) if case % 2 else _inp_of(squad)
        doc = lineup_doc(inp, names, sold)
        rows = squad_rows(inp, names, sold)
        assert not {a["in"] for a in doc["advice"]} & sold, "no advice brings in a player the plan sells"
        best = {r["key"] for r in rows if r["best"]}
        xi = {r["key"] for r in rows if r["xi"]}
        assert {r["key"] for r in rows if r["sold"]} == sold
        if eleven.points(squad_without(inp, sold), squad, {}) is not None:
            assert not best & sold, "a player sold is never in the best eleven"
        assert (xi - {a["out"] for a in doc["advice"]}) | {a["in"] for a in doc["advice"]} == best, "the advice takes the app's eleven to the best"
        assert doc["stale"] == (doc["eleven"] is None or xi != best), "stale iff the app has no eleven or not the best"
    assert lineup_doc(inp, names) == lineup_doc(inp, names, frozenset()), "no sales: the lineup of the squad"


def _selftest_not_listed() -> None:
    rng = random.Random(148)
    seen = 0
    for _ in range(40):
        own = {"m%02d" % i: p for i, p in enumerate(["POR"] * 2 + ["DEF"] * 5 + ["MED"] * 5 + ["DEL"] * 3)}
        pos = own | {"star": "DEL"}
        cells = {j: {k: rng.choice([0, 1, 2, 3, 5]) * 1.0 for k in own} | {"star": 18.0} for j in (1, 2, 3)}
        worth = {k: rng.choice([2e6, 4e6, 6e6]) for k in own} | {"star": 9e6}
        offers = {k: worth[k] * 1.0 for k in rng.sample(sorted(own), 5)}
        gone = frozenset(rng.sample(sorted(own), rng.randint(0, len(own))))
        club = Club(frozenset(own), pos, worth, offers, rng.choice([-5e6, 0.0, 4e6]), 5e7, unlisted=gone)
        rounds = Rounds(cells, valuation.Window((1, 2, 3)), valuation.Window((2, 3)))
        found = approach(club, "star", 9e6, 0.6, rounds)
        names = {k: k.upper() for k in own}
        doc = funding_doc(club, names, found, 9e6)
        if found is None or not found.feasible:
            continue
        sold_after = found.if_won | found.if_lost
        assert doc["not_listed"] == sorted(names[k] for k in sold_after & gone), "the funding names every sale after the result who is not listed"
        assert set(doc["not_listed"]) <= set(doc["if_won"]) | set(doc["if_lost"]), "and only those"
        assert not_listed_doc(club._replace(unlisted=frozenset()), names, sold_after) == [], "everyone listed: nothing said"
        seen += bool(doc["not_listed"])
    assert seen > 0, "the cases include a sale of a player not listed"


def _selftest() -> None:
    _selftest_cover()
    _selftest_not_listed()
    _selftest_params()
    _selftest_alerts()
    _selftest_sells()
    _selftest_settle()
    _selftest_replacements()
    _selftest_one_meaning()
    _selftest_world()
    assert stamped("2026-10-30T1809Z") == "2026-10-30T18:09Z"
    told = sources([Health("b", "2026-11-01T0900Z", 5, 10.0, 2.5, 1.0, "ok", "2026-11-01T0900Z"),
                    Health("a", "2026-10-31T0900Z", 5, 10.0, 26.0, 1.0, "stale", "2026-10-31T0900Z")])
    assert told == [{"src": "a", "at": "2026-10-31T09:00Z", "hours": 26.0, "status": "stale"},
                    {"src": "b", "at": "2026-11-01T09:00Z", "hours": 2.5, "status": "ok"}], "the sources block: by source, hours since the newest"
    buys: list[Doc] = [{"name": "Raphinha", "funding": {"tonight": [{"name": "Pepelu"}, {"name": "Alonso"}]}},
                       {"name": "Gueye", "funding": None}, {"name": "Nobody", "funding": {"feasible": False}}]
    sells: list[Doc] = [{"name": "Pepelu"}, {"name": "Ryan"}]
    funds(buys, sells)
    assert [s["for"] for s in sells] == [["Raphinha"], []], "an offer is taken tonight when a funded buy sells him tonight"
    money = decision.Money(0.0, 0.0, 0.0, 0, 0)
    assert standing(decision.Inputs(money), "p", 10.0, None).later == rules.SALE_ESTIMATE * 10.0, "no price row: the estimate"
    priced = decision.Inputs(money, prices=[decision.Price("p", 1, 10.5, 10.0), decision.Price("p", 3, 11.0, 10.0)])
    assert abs(standing(priced, "p", 10.0, None).later - rules.SALE_ESTIMATE * 10.0 * (1 + hurdle.TREND_CAP)) < 1e-9, "the price row furthest ahead, held within the cap"
    rng = random.Random(14)
    dist = worth.offer_ratio_distribution(rng.uniform(0.9, 1.1) for _ in range(40))
    for _ in range(300):
        value, rate, n = rng.uniform(1e6, 4e7), rng.choice([0.02, 0.045]), rng.choice([1, 9, 26])
        rows = [decision.Price("p", h, value * (1 + rng.uniform(-0.1, 0.1) * rng.choice([0, 1])), value) for h in rng.sample(range(1, 12), rng.randint(0, 5))]
        s = standing(decision.Inputs(money, prices=rows), "p", value, None)
        e = s.exit
        assert abs(s.later / hurdle.draw(value) - 1 - e.rise) < 1e-9 and abs(hurdle.drift(e) * e.nights - e.rise) < 1e-12 and abs(e.rise) <= hurdle.TREND_CAP + 1e-12, \
            "the buy side's later price, the waiting cost and the sale reserve read one capped rise over one horizon"
        points = rng.uniform(0, 20)
        assert worth.waiting_reserve(s, dist, 3) >= s.later
        flat = standing(decision.Inputs(money, prices=[decision.Price("p", h, value, value) for h in range(1, 6)]), "p", value, None)
        assert flat.exit == hurdle.Exit(value, 5, 0.0) and flat.later == hurdle.draw(value) and hurdle.drift(flat.exit) == 0.0, "no rise: today's draw"
        assert hurdle.reserve(hurdle.stake(points, n, rate, flat.exit)) == max(points * 1e6 / (n * rate) , hurdle.draw(value)), "no rise: no drift in the reserve"
    asked = [{"key": "p", "offer": 2.0, "ratio": 1.1, "offer_percentile": 0.8, "offers_seen": 5, "trend": None, "for": ["A", "B"],
              "feasible": True, "delta": {"points": 0.0, "window": {"lock": 0.0, "next4": 0.0, "season": 0.0}, "gap": []},
              "facts": [["value", 1.8, "money"], ["offer", 2.0, "money"]]},
             {"key": "p", "offer": 2.0, "ratio": None, "offer_percentile": None, "offers_seen": 0, "trend": 0.5, "for": [],
              "feasible": True, "delta": {"points": -3.0, "window": {"lock": -0.5, "next4": -1.0, "season": -3.0}, "gap": []},
              "facts": [["value", 0.0, "money"]]},
             {"key": "q", "offer": 3.0, "ratio": 1.0, "offer_percentile": 0.5, "offers_seen": 5, "trend": 1.0, "for": [],
              "feasible": False, "delta": {"points": None, "window": {"lock": None, "next4": None, "season": None}, "gap": [3, 4]},
              "facts": [["value", 3.0, "money"]]}]
    held = [decision.Held("p", "t1", None, 1.8), decision.Held("q", "t2", None, 3.0), decision.Held("r", "t3", None, 1.0)]
    pending = [current.Offer("20", "t1", "pending", 2.0, "c20", "", ""), current.Offer("10", "t1", "pending", 2.0, "c10", "", ""),
                current.Offer("30", "t2", "pending", 3.0, "c30", "", ""), current.Offer("40", "t3", "declined", 9.0, "c40", "", ""),
                current.Offer("50", "tx", "pending", 9.0, "c50", "", ""), current.Offer("60", "t3", "pending", None, "c60", "", "")]
    stood = stood_offers(held, pending)
    assert sorted(x.offer_id for x in stood) == ["10", "20", "30"], "pending, with money, on a player held"
    made = offer_rows("cut", asked, stood)
    assert [(m["player"], m["offer_id"], m["created_at"]) for m in made] == [("p", "10", "c10"), ("p", "20", "c20"), ("q", "30", "c30")]
    assert [m["offer_id"] for m in offer_rows("cut", asked, stood[::-1])] == ["10", "20", "30"], "the order of the standing offers does not matter"
    assert [(m["value"], m["funds"], m["delta_next4"], m["delta_season"], m["fields"], m["gap"]) for m in made] == [
        (1.8, "A;B", 0.0, 0.0, True, ""), (0.0, "", -1.0, -3.0, True, ""), (3.0, "", None, None, False, "3;4")]
    assert all(set(m) == set(rec.DecOffer._fields) for m in made), "a field for each field of the kind, no more"
    assert advice((("c", "b", 2.0), ("d", "c", 1.0), ("e", "x", 0.5)), {"d": "D"}) == [
        {"in": "D", "out": "b", "gain": 3.0}, {"in": "e", "out": "x", "gain": 0.5}], "a player swapped in and then out is one swap"
    curve: Curve = ((1.0, 0.2), (1.1, 0.6), (1.3, 1.0))
    asking, advised = 10e6, 11e6
    done = Approach(True, 0.0, frozenset(), frozenset(), frozenset(), frozenset(), 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 3.0)
    best = Offer(advised, chance(curve, advised / asking), done)
    walk = 12.5e6
    for placed in (None, 10e6, 10.5e6, 11e6, 12e6, 13e6):
        row = {"placed": placed, "asking": asking}
        blind, shown = stance_of(row, None, walk, None, None, None, lambda amount, win: done)
        assert (blind.advised, blind.advised_win, shown) == (None, None, None), "no curve: nothing advised, nothing funded"
        assert (blind.amount, blind.win) == (placed, None) and blind.move == ("pass" if placed is None else "lower" if placed > walk else "hold")
        at, shown = stance_of(row, curve, walk, best, advised, best.win, lambda amount, win: done)
        none, _ = stance_of(row, curve, walk, None, None, None, lambda amount, win: done)
        assert (at.advised, at.advised_win) == (advised, best.win), "the advised bid is said"
        if placed is None:
            assert none.move == "pass" and none.to is None and shown == done
            assert (at.amount, at.win, at.move) == (advised, best.win, "bid"), "no bid placed: the advised one"
            continue
        want = hurdle.stand(curve, asking, placed, walk)
        assert (at.amount, at.win, at.move, at.to) == (placed, chance(curve, placed / asking), want.move, want.to), "a bid placed is judged by the grid at the funded walk-away price"
        assert at.move in ("hold", "lower", "withdraw", "raise") and none.move == at.move
        assert (at.to is None) == (at.move in ("hold", "withdraw"))
        (line,) = target_lines({}, [{"player": "p", "pos": "MED", "status": "market", "gain": 1.0, "asking": asking, "bid": advised, "win": best.win, "placed": placed,
                                     "replaces": "x", "per_million": None, "price_next": None, "price_lock": None,
                                     "stake": hurdle.stake(1.0, 26, 0.045, hurdle.Exit(asking), 26)}])
        assert line["placed"] == placed, "a target with your bid on him says it beside the advised one"
    _selftest_overlap()
    _selftest_placed()
    _selftest_worked_case()
    print("board self-test OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        raise SystemExit(0)
    main()
