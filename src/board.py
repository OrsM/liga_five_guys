"""The board stage: decisions.json, the phone's copy of the board, from the league at t. Your cash and what you can
bid, your squad with its eleven, the players on the market who would add points to it (buys, most first, each with
the bid to make, the sales that fund it and the net points it adds to doing nothing: approach.approach), and each standing offer on your
players ranked by what taking it costs your eleven (valuation.read). The one line act.py takes is do: list your unlisted players at
their value."""
from __future__ import annotations

import json
import random
from itertools import combinations
import sys
from collections.abc import Iterable, Sequence
from typing import Any, NamedTuple

import decision
import eleven
import hurdle
import worth
from approach import Approach, Club, Plan, Rounds, Side, approach, plan, prices, replacement
from ffcore import current, league, loader, price_fold, state
from ffcore import forecast as model, pricing, rules, tidy
from ffcore import observations as ob
from ffcore import records as rec
from ffcore.auction import Curve, chance
from ffcore.clock import Clock, clock_at, cut, run_now, shown
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
TOP_TARGETS = 10
TOP_REPLACEMENTS = 8
EXAMINED_REPLACEMENTS = 40
SOLD_VALUE = -1e-6


def stamped(t: str) -> str:
    return t[:13] + ":" + t[13:]


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


def context(inp: decision.Inputs, offers: worth.OfferDistribution = worth.NO_OFFERS, rate: float = hurdle.FALLBACK_LAMBDA) -> worth.Context:
    pos, cells = decision.cells_of(inp)
    locks = decision.lock_times(inp)
    return worth.Context(pos, decision.window_of(locks, cells, None), inp.money.cash, offers, rate,
                         decision.squad_of(inp), cells, locks)


def later_sale(inp: decision.Inputs, player: str, value: float) -> float:
    rows = [x for x in inp.prices if x.player == player]
    if not rows:
        return rules.SALE_ESTIMATE * value
    return rules.SALE_ESTIMATE * value * (1 + decision.rise(max(rows, key=lambda x: x.h)))


def standing(inp: decision.Inputs, player: str, value: float, offer: float | None) -> worth.Standing:
    """The offer on a player as the sale rules read it: later_sale, and the price forecast's rise at its furthest horizon
    within hurdle.TREND_NIGHTS (None with no such row)."""
    near = [x for x in inp.prices if x.player == player and x.h <= hurdle.TREND_NIGHTS and x.value]
    return worth.Standing(player, value, offer, later_sale(inp, player, value),
                          decision.rise(max(near, key=lambda x: x.h)) if near else None)


def offers_at(world: state.World) -> worth.OfferDistribution:
    """The distribution of offer over value (worth.offer_ratio_distribution) of the market offers the world has seen."""
    return worth.offer_ratio_distribution(x.ratio for x in current.offer_ratios(world))


def delta_doc(sale: valuation.Reading) -> Doc:
    """The Delta of a move as the document states it: points (the season), window (points over each of valuation.READERS)
    and gap (the jornadas the move leaves without a legal eleven); every number null when the squad after the move has none."""
    return {"points": sale.points, "window": dict(sale.window), "gap": list(sale.gap)}


def offer_lines(inp: decision.Inputs, names: dict[str, str], got: list[decision.Offer], known: dict[str, decision.Fact],
                offers: worth.OfferDistribution = worth.NO_OFFERS, rate: float = hurdle.FALLBACK_LAMBDA) -> list[Doc]:
    """A sells row for each standing offer, judged by worth.LIVE at rate: verdict sell (the offer reaches the reserve),
    hold, or needs_replacement (no legal eleven without him), before settle; lost the points the sale loses (None: no
    Delta) as points_lost, reserve, rent the points the money earns at rate over the jornadas of the sale; by per_million (points lost per
    million raised), lowest first."""
    ctx = context(inp, offers, rate)
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
            "points_lost": lost, "reserve": verdict.reserve, "rent": rate * n * money / 1e6,
            "facts": [["value", value, "money"], ["offer", money, "money"], ["offer over value", ratio, "ratio"],
                      ["offer percentile", share, "chance"], ["offers seen", len(offers.ratios), "count"],
                      ["sale price", verdict.reserve, "money"],
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


def funding_doc(club: Club, names: dict[str, str], found: Approach | None, bid: float) -> Doc:
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
            "gain": found.gain, "points_lost_if_won": found.cost_won, "points_lost_if_lost": found.cost_lost, "net": found.net,
            "base": found.base}


def base_cost(found: Approach | None) -> float:
    """The points of the cheapest plan with no buy (`found` the approach with no player): what it sells to bring cash to 0
    or more costs; 0 when there is no such plan or no eleven."""
    return found.cost_lost if found is not None and found.feasible else 0.0


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


def base_doc(club: Club, names: dict[str, str], found: Approach | None) -> Doc:
    """The funding document of the plan with no buy (`cash_if_lost` the cash it leaves), with its cost in points, `cost`, the cash it
    starts from and, when a credit is due before the lock, its `risk` (risk_doc) on the cash the plan leaves before it."""
    before = club.cash if found is None or not found.feasible else found.cash_if_lost - club.inflow.expected
    return {**funding_doc(club, names, found, 0.0), "cost": base_cost(found), "cash": club.cash, **risk_doc(club, before)}


def ranked(lines: Sequence[Doc]) -> tuple[list[Doc], int]:
    """The buys to show and the count of those considered and left out: the lines with a net above 0, by net then gain then
    name, then those with no net (not evaluated, or unfundable) by gain, at most TOP_BUYS; the count is of the lines with a net of 0 or
    less, which are not shown."""
    valued = [b for b in lines if b["net"] is not None]
    kept = sorted((b for b in valued if b["net"] > 0), key=lambda b: (-b["net"], -b["gain"], b["name"]))
    rest = sorted((b for b in lines if b["net"] is None), key=lambda b: (-b["gain"], b["name"]))
    return (kept + rest)[:TOP_BUYS], len(valued) - len(kept)


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


def stance(r: dict[str, Any], curve: Curve | None, cap: float | None) -> Stance:
    placed, advised, advised_win = r["placed"], r["bid"], r["win"]
    if curve is None:
        move = "unknown" if placed is None else "lower" if cap is not None and placed > cap else "hold"
        return Stance(placed, None, None, None, move)
    if placed is None:
        return Stance(advised, advised_win, advised, advised_win, "pass" if advised is None else "bid")
    got = hurdle.stand(curve, r["asking"], placed, cap or 0.0)
    return Stance(placed, chance(curve, placed / r["asking"]), advised, advised_win, got.move, got.to)


def millions(x: float) -> str:
    """An amount in euros in words, in millions to one decimal."""
    return "%.1f million" % (x / 1e6)


def stance_why(at: Stance, cap: float | None, rate_yield: float | None, curve: Curve | None, asking: float) -> str | None:
    """The words for a bid placed, with the numbers: the amount and its chance, the walk-away price and the yield, then
    the move (hold, lower to an amount, raise to an amount or withdraw) with the expected surplus (chance x (walk-away
    price - amount)) behind it; None for a listing with no bid placed."""
    if at.amount is None or at.move in ("bid", "pass", "unknown"):
        return None
    away = "no walk-away price" if cap is None else "a walk-away price of " + millions(cap)
    yld = "" if rate_yield is None else " and a yield of %.3f points per million per jornada" % rate_yield
    base = "Your bid of %s has %s" % (millions(at.amount), "an unknown chance" if at.win is None else "a %.0f%% chance" % (100 * at.win))
    base += ", against %s%s. " % (away, yld)
    if cap is None or curve is None or at.win is None:
        return base + ("Lower it: it is above the walk-away price." if at.move == "lower" else "Keep it: no chance is known for this auction.")
    surplus = at.win * (cap - at.amount)
    own = "an expected surplus of %s" % millions(surplus)
    if at.move == "hold":
        return base + "It leaves %s: keep it." % own
    if at.move == "withdraw":
        return base + "No bid of %s up has a positive expected surplus under that price (yours leaves %s): withdraw it." % (millions(asking), own)
    assert at.to is not None
    there = chance(curve, at.to / asking)
    other = "a bid of %s has a %.0f%% chance and an expected surplus of %s" % (millions(at.to), 100 * there, millions(there * (cap - at.to)))
    return base + "Yours leaves %s; %s: %s it to %s." % (own, other, at.move, millions(at.to))


def priced(r: dict[str, Any], rate: float, last: int) -> Doc:
    """The rate's reading of an opportunity row: n (the jornadas of its window), yield (points per million per jornada,
    None with no price) and walk_away (the most worth paying, hurdle.walk_away)."""
    n = hurdle.remaining(r["from_j"], last)
    return {"n": n, "yield": r["per_million"] / n if r["per_million"] is not None and n else None,
            "walk_away": hurdle.walk_away(r["gain"], n, rate) if n else None}


def buy_lines(inp: decision.Inputs, names: dict[str, str], club: Club, got: list[dict[str, Any]], base: Approach | None,
              rate: float = hurdle.FALLBACK_LAMBDA) -> tuple[list[Doc], int]:
    """The buys of the board and the count considered and left out. The players on the market with an asking price and a
    gain, best gain first, each read at rate (priced); those the rate passes (move pass: no bid advised and none placed) are
    counted, not shown. A bid placed is never counted out: its line (a move of hold, lower, withdraw or raise, a `why` in
    words) is first, whatever its gain or net, even on a player with no gain. Of the lines the first EVALUATED_BUYS with a
    bid get an Approach and a net against the plan with no buy (base); the rest a net None, labelled GAIN_ONLY (UNFUNDED
    when no sales fund the bid). The others are ranked."""
    pos, cells = decision.cells_of(inp)
    locks = decision.lock_times(inp)
    curves = decision.curves_of(inp)
    seen = {a.player: a for a in inp.auctions}
    bids = {b.player: b.amount for b in inp.bids}
    cost0 = base_cost(base)
    last = max(cells, default=0)
    read = {r["player"]: priced(r, rate, last) for r in got
            if r["status"] == "market" and r["asking"] is not None and (r["gain"] > 0 or r["placed"] is not None)}
    stood = {k: stance(r, curves.get(k), read[k]["walk_away"]) for r in got if (k := r["player"]) in read}
    passed = sum(1 for at in stood.values() if at.move == "pass")
    out = []
    mine = [r for r in got if r["player"] in read and r["placed"] is not None]
    for i, r in enumerate(mine + [r for r in got if r["player"] in read and r["placed"] is None and stood[r["player"]].move != "pass"]):
        k, placed = r["player"], r["placed"]
        at = stood[k]
        amount, win = at.amount, at.win
        auction = seen.get(k)
        others = (len(bids) - (k in bids), sum(bids.values()) - bids.get(k, 0.0))
        rounds = Rounds(cells, decision.window_of(locks, cells, None), decision.window_of(locks, cells, r["close_at"]))
        found = approach(club._replace(pending=others), k, amount, win, rounds, cost0) \
            if i < EVALUATED_BUYS and amount is not None and win is not None else None
        funding = None if found is None or amount is None else funding_doc(club, names, found, amount)
        net = found.net if found is not None and found.feasible else None
        label = None if net is not None else UNFUNDED if found is not None else GAIN_ONLY
        name = names.get(k, k)
        out.append({
            "name": name, "pos": r["pos"], "what": "buy", "label": "buy " + name, "step": "placed" if placed is not None else "bid",
            "price": r["asking"], "asking": r["asking"], "amount": amount, "win": win, "advised": at.advised, "advised_win": at.advised_win,
            "move": at.move, "to": at.to, "why": stance_why(at, read[k]["walk_away"], read[k]["yield"], curves.get(k), r["asking"]), "plan": False, "done": placed is not None,
            "key": k, "gain": r["gain"], "net": net, "net_label": label, "per": "season", "more": False, "detail": None, "trace": None, "flip": None,
            "replaces": names.get(r["replaces"], r["replaces"]), "per_million": r["per_million"], **read[k],
            "price_next": r["price_next"], "price_lock": r["price_lock"],
            "facts": [["asking", r["asking"], "money"], ["walk-away price", read[k]["walk_away"], "money"], ["bid", amount, "money"],
                      ["chance to win", win, "chance"]]
            + ([["advised bid", at.advised, "money"], ["chance at the advised bid", at.advised_win, "chance"]] if placed is not None else [])
            + [["points added", r["gain"], "points"]],
            "bids": auction.bids if auction else None,
            "auction": {"yours": bool(auction.mine), "listings": auction.n, "thin": bool(auction.thin), "cell": auction.cell}
            if auction else {"yours": False, "listings": 0, "thin": True, "cell": ""},
            "funding": funding, "accepts": [], "act": []})
    placed_lines = [b for b in out if b["done"]]
    shown, left = ranked([b for b in out if not b["done"]])
    return placed_lines + shown, left + passed


def target_lines(names: dict[str, str], got: list[dict[str, Any]], rate: float = hurdle.FALLBACK_LAMBDA, last: int = 0) -> list[Doc]:
    return [{"name": names.get(r["player"], r["player"]), "pos": r["pos"], "status": r["status"], "gain": r["gain"],
             "asking": r["asking"], "bid": r["bid"], "win": r["win"], "placed": r["placed"],
             "replaces": names.get(r["replaces"], r["replaces"]),
             "per_million": r["per_million"], "price_next": r["price_next"], "price_lock": r["price_lock"], **priced(r, rate, last),
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


def replacement_row(inp: decision.Inputs, names: dict[str, str], club: Club, sold: str, r: dict[str, Any], found: Approach) -> Doc:
    """The row of the pair that sells `sold` and buys the market row r, funded as found says: the buy's asking and bid and chance,
    the pair's Delta (valuation.read, the buy arriving at its close) and its net (Approach.net, the bid lost counted), the cash at
    the lock if the bid is won and the funding."""
    pos, cells = decision.cells_of(inp)
    locks = decision.lock_times(inp)
    k = r["player"]
    pair = valuation.read(club.squad, valuation.Move(frozenset({sold}), frozenset({k}), None, r["close_at"]), decision.window_of(locks, cells, None),
                          locks, pos, cells)
    return {"what": "replace", "name": names.get(k, k), "key": k, "pos": r["pos"], "sell": names.get(sold, sold),
            "asking": r["asking"], "bid": r["bid"], "win": r["win"], "gain": found.gain, "net": found.net, "delta": delta_doc(pair),
            "cash_after": found.lock_margin, "feasible": True, "why_infeasible": None,
            "facts": [["asking", r["asking"], "money"], ["bid", r["bid"], "money"], ["chance to win", r["win"], "chance"],
                      ["net points", found.net, "points"]],
            "funding": funding_doc(club, names, found, r["bid"])}


def replacement_rows(inp: decision.Inputs, names: dict[str, str], club: Club, got: Sequence[dict[str, Any]], sells: Sequence[Doc]) -> None:
    """Sets, on every row of sells whose sale leaves no legal eleven, "replacement": the best pair (replacement_row) that sells the
    player and buys a player of the market with a bid advised (his row of got) in the positions that make the squad legal again, the
    most net points (Approach.net) of those approach.replacement funds, among the TOP_REPLACEMENTS best forecast of the
    EXAMINED_REPLACEMENTS best forecast that fit; or None with "replacement_why_not" "no_candidate" (no such player on the market)
    or "no_cash" (none is funded)."""
    pos, cells = decision.cells_of(inp)
    locks = decision.lock_times(inp)
    bids = {b.player: b.amount for b in inp.bids}
    for sale in sells:
        if sale["feasible"]:
            continue
        sold = sale["key"]
        fits = [r for r in got if r["status"] == "market" and r["bid"] is not None and r["win"] is not None and r["player"] not in club.squad
                and eleven.points((club.squad - {sold}) | {r["player"]}, pos, {}) is not None]
        ahead = {r["player"]: sum(cells[j].get(r["player"], 0.0) for j in decision.window_of(locks, cells, r["close_at"])) for r in fits}
        best: tuple[float, str, dict[str, Any], Approach] | None = None
        funded_n = 0
        for r in sorted(fits, key=lambda r: (-ahead[r["player"]], r["player"]))[:EXAMINED_REPLACEMENTS]:
            k = r["player"]
            others = (len(bids) - (k in bids), sum(bids.values()) - bids.get(k, 0.0))
            rounds = Rounds(cells, decision.window_of(locks, cells, None), decision.window_of(locks, cells, r["close_at"]))
            found = replacement(club._replace(pending=others), sold, k, r["bid"], r["win"], rounds)
            if not found.feasible:
                continue
            funded_n += 1
            if best is None or (found.net, k) > best[:2]:
                best = (found.net, k, r, found)
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


def settle(sells: list[Doc], cover: frozenset[str]) -> None:
    """The verdicts of the sells rows once the cash's uses are known (rows with "for" set by funds; cover the sales of the
    plan that brings cash to 0 at the lock): a sale that loses points is a sell only when its cash has a use (it funds a
    buy tonight or is in cover), else hold; a sell not taken tonight (worth.take_now) is wait. Then sells first, by
    per_million."""
    for sale in sells:
        if sale["verdict"] == "sell" and (sale["points_lost"] or 0.0) > worth.NO_LOSS and not sale["for"] and sale["key"] not in cover:
            sale["verdict"] = "hold"
        if sale["verdict"] == "sell" and not worth.take_now(sale["ratio"], sale["offer_percentile"], bool(sale["for"])):
            sale["verdict"] = "wait"
    sells.sort(key=lambda d: (d["verdict"] != "sell", d["per_million"] is None, d["per_million"], d["key"]))


def takes(inp: decision.Inputs, club: Club, clock: Clock, sale: Doc, dist: worth.OfferDistribution) -> bool:
    """Whether the offer of the row `sale`, a sale of the plan, is taken now: when no close is left before the lock, when he funds a bid
    tonight, else when it is at or above worth.waiting_reserve with the clock's closes left."""
    k = sale["key"]
    return clock.n_closes == 0 or bool(sale["for"]) \
        or sale["offer"] >= worth.waiting_reserve(standing(inp, k, club.worth[k], sale["offer"]), dist, clock.n_closes)


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


def cover_lines(inp: decision.Inputs, names: dict[str, str], club: Club, plan: Plan | None, clock: Clock, sells: Sequence[Doc],
                listed: frozenset[str], dist: worth.OfferDistribution) -> list[Doc]:
    """The lines of do that bring cash to 0 or more at the lock when it is below 0, from the plan with no buy (approach.plan): a
    "sell" line for each of its sales with a standing offer that `takes` says to take now, a copy of its row of sells marked "needed"
    with the points the plan loses (set_points_lost) and the cash after the plan (set_cash_after); then one "wait" line (n_closes, expected_cover
    the names of the sales still to draw, expected_raised their expected proceeds, need the money they must raise, cost_points the plan's,
    last_close the latest close before the lock, why) when some sale is not taken and every one of them is listed; the unlisted ones
    are the "list" line's. One "short" line with the money the pool lacks when no plan exists. When the credit due before the lock
    (club.inflow, at its low case) brings cash to 0 or more with no sale, one "wait" line for the credit (credit_line). No lines when
    cash is 0 or more or no eleven is fielded."""
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
    taken = [r for k in sorted(plan.sells) if (r := rows.get(k)) is not None and takes(inp, club, clock, r, dist)]
    cash_after = club.cash + plan.raised + club.inflow.expected
    credited = " (the credit due, %.0f expected, counted)" % club.inflow.expected if club.inflow.jornadas else ""
    before = club.cash + plan.raised
    why = "cash %.0f is below 0: the cheapest plan sells %s, bringing it to %.0f at the lock%s, losing %.1f points%s" % (
        club.cash, ", ".join(names.get(k, k) for k in sorted(plan.sells)), cash_after, credited, plan.cost, risk_clause(club, before))
    out = [{**r, "needed": True, "why": why, "set_points_lost": plan.cost, "set_cash_after": cash_after, **risk_doc(club, before)} for r in taken]
    waiting = sorted(plan.sells - {r["key"] for r in taken})
    if waiting and clock.last_close and set(waiting) <= listed:
        need = max(0.0, -(club.cash + club.inflow.expected + sum(r["offer"] for r in taken)))
        close = snapshot_stamp(clock.last_close)
        names_waiting = [names.get(k, k) for k in waiting]
        out.append({
            "what": "wait", "label": "wait for the offers of " + ", ".join(names_waiting), "step": "wait", "needed": True,
            "n_closes": clock.n_closes, "expected_cover": names_waiting,
            "expected_raised": sum(club.offers.get(k, rules.SALE_ESTIMATE * club.worth[k]) for k in waiting), "need": need,
            "cost_points": plan.cost, "last_close": stamped(clock.last_close), **risk_doc(club, before),
            "why": "cash %.0f is below 0 and the lock is %d closes away (the last %s): the cheapest plan (%.1f points) sells %s to the offers "
                   "still to come; nothing is taken now%s" % (club.cash, clock.n_closes, shown(close) if close else "", plan.cost,
                                                              ", ".join(names_waiting), risk_clause(club, before)),
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


def board(inp: decision.Inputs, got: list[dict[str, Any]], arrivals: list[str],
          extras: decision.Extras, health: Iterable[Health], offers: worth.OfferDistribution = worth.NO_OFFERS,
          rate: float = hurdle.FALLBACK_LAMBDA) -> Doc:
    """The decisions document of the cut; extras are what the World gives beside the inputs, health the status of each
    source (health.parser_health over the loaded snapshots), offers the nightly offers seen by the cut (offers_at) and
    rate the exchange rate LAMBDA the buys and sales are read at (hurdle.lam), stated as `lambda`."""
    cash, team_value, debt = inp.money.cash, inp.money.team_value, inp.money.debt
    locks = [x.lock for x in inp.locks]
    bids = sum(b.amount for b in inp.bids)
    known = extras.known
    names = {k: fact.name for k, fact in known.items()}
    mine = decision.squad_of(inp)
    offered = extras.offers
    pos = {p.player: p.pos for p in inp.players if p.pos in POSITIONS}
    _, cells = decision.cells_of(inp)
    club = Club(mine, pos, {h.player: h.value for h in extras.held},
                {o.player: o.money for o in offered}, cash, team_value, (len(inp.bids), bids), debt=debt,
                inflow=inflow_of(inp.credits, mine, pos, cells))
    alerts = [{"name": names.get(r["player"], r["player"]), "gain": r["gain"], "asking": r["asking"], "bid": r["bid"],
               "win": r["win"], "placed": r["placed"]} for r in arrival_rows(got, arrivals)]
    window = decision.window_of(decision.lock_times(inp), cells, None)
    base = approach(club, None, 0.0, 1.0, Rounds(cells, window, window))
    buys, considered = buy_lines(inp, names, club, got, base, rate)
    sells = offer_lines(inp, names, offered, known, offers, rate)
    funds(buys, sells)
    replacement_rows(inp, names, club, got, sells)
    clock = clock_at(snapshot_stamp(cut()) or run_now(), locks)
    found = plan(club, base, Rounds(cells, window, window), clock.n_closes)
    settle(sells, found.sells if found is not None and found.feasible else frozenset())
    owed = cover_lines(inp, names, club, found, clock, sells, extras.listed, offers) if locks else []
    sold = (found.sells if found is not None and found.feasible else frozenset()) | {s["key"] for s in sells if s["verdict"] == "sell"}
    squad = squad_rows(inp, names, sold)
    return {
        "generated_at": stamped(cut()), "market_at": market_at(extras.market_read), "sources": sources(health),
        "lock_at": min(locks).replace("Z", ":00+00:00") if locks else None,
        "cash": cash, "cash_locked": bids, "can_bid": max(0.0, bid_limit(cash, team_value, debt) - bids),
        "ping": "on the market: " + ", ".join("%s (+%.0f points)" % (a["name"], a["gain"]) for a in alerts) if alerts else "",
        "do": owed + list_line(inp, names, extras), "lineup": lineup_doc(inp, names, sold), "buys": buys, "considered": considered,
        "base": base_doc(club, names, base), "targets": target_lines(names, got, rate, max(cells, default=0)), "lambda": rate,
        "alerts": alerts, "auction": auction_card(),
        "sells": sells,
        "squad": [{k: v for k, v in r.items() if k != "key"} for r in squad]}


# The constants a run is built with, by module: what params records as such.
NAMES: dict[str, tuple[str, ...]] = {
    "tidy": ("APP_FRESH_HOURS", ),
    "forecast": ("ABSENT_START", "FIXTURE_K", "MIN_POOL", "NEUTRAL_START", "PERSISTENT_SHARE", "PICK_K", "P_DECAY", "P_LIFT", "P_LISTING", "SHRINK_K"),
    "pricing": ("AUCTION_MIN", "CLIP", "PRICE_FOCUS", "PRICE_W0", "PRICE_W1", "PRICE_W2", "PRICE_WINDOW"),
    "rules": ("DEBT_SHARE", "MATCH_LEN"),
    "hurdle": ("FALLBACK_LAMBDA", "S", "MARGIN", "CHEAP", "CHEAP_MARGIN", "WIN_FLOOR", "POOL_DAYS", "MIN_DAYS", "TREND_NIGHTS", "TREND_CAP"),
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
    records.add("dec_opportunity", [{"cut": cut(), **r} for r in got])
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
    inp = _inp_of(squad, market)
    names = {k: k for k in squad | market}
    pos, cells = decision.cells_of(inp)
    got: list[dict[str, Any]] = [{"player": k, "pos": p, "status": "market", "asking": 1e6, "bid": 1.2e6, "win": 0.5 + 0.1 * (i % 4), "close_at": None}
           for i, (k, p) in enumerate(sorted(market.items()))]
    offers = {k: 5e6 + 1e5 * i for i, k in enumerate(sorted(squad))}
    for cash, why in ((50e6, None), (-50e6, "no_cash")):
        club = Club(frozenset(squad), pos, {k: 4e6 for k in squad}, offers, cash, 1e8)
        rows = offer_lines(inp, names, [decision.Offer(k, 4e6, offers[k]) for k in sorted(squad)], {})
        replacement_rows(inp, names, club, got, rows)
        for r in rows:
            assert r["feasible"] or r["replacement"] is not None or r["replacement_why_not"], "a number, a replacement or the reason why not"
            assert r["replacement_why_not"] == why and (r["replacement"] is None) == (why is not None)
            if why is not None:
                continue
            pair = r["replacement"]
            assert pair["sell"] == r["name"] and pair["feasible"] and pair["why_infeasible"] is None
            bought = pair["key"]
            assert eleven.points((frozenset(squad) - {r["key"]}) | {bought}, pos, {}) is not None, "the pair leaves a legal eleven"
            best = max((replacement(club, r["key"], g["player"], g["bid"], g["win"], Rounds(cells, decision.window_of({}, cells, None),
                                                                                      decision.window_of({}, cells, None))).net
                        for g in got if eleven.points((frozenset(squad) - {r["key"]}) | {g["player"]}, pos, {}) is not None))
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


def _selftest_buys() -> None:
    rng = random.Random(8)
    for _ in range(60):
        lines: list[Doc] = [{"name": "p%02d" % i, "gain": rng.choice([0.5, 2.0, 9.0, 40.0]),
                             "net": rng.choice([None, -3.0, 0.0, 0.4, 2.0, 7.5, 30.0])} for i in range(rng.randint(0, 14))]
        shown, considered = ranked(lines)
        valued = [b for b in lines if b["net"] is not None]
        assert considered == sum(1 for b in valued if b["net"] <= 0), "the count is of the evaluated buys left out"
        assert all(b["net"] is None or b["net"] > 0 for b in shown), "no buy with a net of 0 or less is shown"
        have = [b for b in shown if b["net"] is not None]
        assert [(-b["net"], -b["gain"], b["name"]) for b in have] == sorted((-b["net"], -b["gain"], b["name"]) for b in have), "ranked by net"
        assert shown[:len(have)] == have and not any(b["net"] is not None for b in shown[len(have):]), "those with no net come after"
        assert len(shown) <= TOP_BUYS and {b["name"] for b in shown} <= {b["name"] for b in lines}
        if len(lines) <= TOP_BUYS:
            assert len(shown) + considered == len(lines), "every line is shown or counted"


def _selftest_placed() -> None:
    rng = random.Random(21)
    shape = {"POR": 1, "DEF": 4, "MED": 4, "DEL": 2}
    squad = {"%s%d" % (p, i): p for p, n in shape.items() for i in range(n)}
    market = {"%s%d" % (p, i): p for p in shape for i in range(10, 14)}
    names = {k: k for k in squad | market}
    ratios = (0.9, 1.0, 1.02, 1.05, 1.1, 1.2, 1.3, 1.5)
    moves: set[str] = set()
    for _ in range(40):
        keys = sorted(market)
        curves = [decision.CurvePoint(k, r, min(1.0, rng.choice([0.0, 0.1, 0.3]) + (r - 1.0) * rng.choice([0.5, 2.0, 5.0]))) for k in keys for r in ratios]
        mine = {k: rng.choice([10e6, 10.4e6, 13e6, 25e6]) for k in rng.sample(keys, rng.randint(0, len(keys)))}
        inp = _inp_of(squad, market)._replace(curves=curves, bids=[decision.Bid(k, a) for k, a in sorted(mine.items())])
        pos, cells = decision.cells_of(inp)
        club = Club(frozenset(squad), pos, {k: 4e6 for k in squad}, {}, 5e7, 1e8)
        rate = rng.choice([0.045, 0.1, 0.3])
        got = [{"player": k, "pos": market[k], "status": "market", "asking": 10e6, "bid": None, "win": None, "close_at": None, "replaces": "POR0",
                "gain": rng.choice([0.0, 0.5, 3.0, 20.0, 60.0]), "per_million": None, "from_j": 1, "price_next": None, "price_lock": None,
                "placed": mine.get(k)} for k in keys]
        shown, considered = buy_lines(inp, names, club, got, None, rate)
        assert [b["key"] for b in shown if b["done"]] == [b["key"] for b in shown[:len(mine)]], "a bid placed comes before the ranked rows"
        assert {b["key"] for b in shown if b["done"]} == set(mine), "a bid placed is always a row of the buys"
        curve_of = decision.curves_of(inp)
        last = max(cells)
        for b in shown[:len(mine)]:
            n = hurdle.remaining(1, last)
            cap = hurdle.walk_away(b["gain"], n, rate)
            want = hurdle.stand(curve_of[b["key"]], 10e6, mine[b["key"]], cap)
            assert (b["amount"], b["move"], b["to"], b["walk_away"]) == (mine[b["key"]], want.move, want.to, cap)
            assert b["win"] == chance(curve_of[b["key"]], mine[b["key"]] / 10e6) and b["why"] and b["yield"] is None
            assert not any(w in b["why"].lower() for w in ("take", "sell it")), "no verdict word the page forbids"
            moves.add(b["move"])
        assert all(b["why"] is None for b in shown[len(mine):]), "no words for a bid not placed"
        assert considered + len(shown) - len(mine) <= len(market) - len(mine), "the count has no bid placed in it"
    assert moves >= {"hold", "lower", "withdraw"}, moves


def _selftest_cover() -> None:
    inp = decision.Inputs(decision.Money(0.0, 0.0, 0.0, 0, 0))
    rng = random.Random(14)
    shape = ["POR"] * 2 + ["DEF"] * 4 + ["MED"] * 4 + ["DEL"] * 3
    seen_take = seen_wait = seen_empty = seen_unlisted = seen_credit = 0
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
        clock = Clock(n_closes, "2026-11-27T2000Z", "2026-11-26T2124Z" if n_closes else None)
        listed = frozenset(offers) | frozenset(rng.sample(sorted(own), rng.randint(0, len(own))))
        dist = rng.choice([worth.NO_OFFERS, worth.offer_ratio_distribution([0.9, 0.95, 1.0, 1.0, 1.05, 1.1])])
        rounds = Rounds({1: points}, valuation.Window((1,)), valuation.Window((1,)))
        found = plan(club, approach(club, None, 0.0, 1.0, rounds), rounds, n_closes)
        assert found is not None
        sells = [{"what": "sell", "key": k, "name": k, "offer": offers[k], "for": [k] if rng.random() < 0.15 else []} for k in sorted(offers)]
        rows = cover_lines(inp, {k: k for k in own}, club, found, clock, sells, listed, dist)
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
        price = prices(club, False)
        takes_ = [r for r in rows if r["what"] == "sell"]
        waits = [r for r in rows if r["what"] == "wait"]
        assert len(waits) <= 1 and len(takes_) + len(waits) == len(rows)
        assert all(r["key"] in found.sells and r["key"] in offers and r["set_cash_after"] >= -1e-6 for r in takes_), \
            "a take is a sale of the plan with a standing offer and leaves cash 0 or more"
        assert all(r["set_points_lost"] == found.cost for r in takes_)
        if n_closes >= 1:
            assert all(r["for"] or r["offer"] >= worth.waiting_reserve(standing(inp, r["key"], value[r["key"]], r["offer"]),
                                                                     dist, n_closes) for r in takes_), "with closes left an offer is taken only at or above the reserve, or to fund a bid"
        if n_closes == 0:
            assert not waits and {r["key"] for r in takes_} == found.sells <= frozenset(offers), "no close left: every sale of the plan is taken"
            pool = sorted(offers)
            owed = [c for m in range(len(pool) + 1) for b in combinations(pool, m)
                    if sum(offers[k] for k in b) >= -(cash + low) - 1e-6 and (c := side.cost(frozenset(b))) is not None]
            assert abs(found.cost - min(owed)) < 1e-9, "at n_closes 0 the chosen set is the cheapest over the standing offers"
        for w in waits:
            assert n_closes >= 1 and w["n_closes"] == n_closes and w["last_close"] == "2026-11-26T21:24Z"
            assert w["cost_points"] == found.cost and w["need"] >= 0
            members = {k for k in own if k in w["expected_cover"]}
            assert members <= listed and members <= found.sells - {r["key"] for r in takes_}, "a wait names members that are listed"
            assert abs(w["expected_raised"] - sum(offers.get(k, rules.SALE_ESTIMATE * value[k]) for k in members)) < 1e-6
            seen_wait += 1
        if n_closes >= 1 and found.sells - listed:
            assert not waits, "a plan member not listed is not counted as a cover"
            seen_unlisted += 1
        if n_closes >= 1:
            pool = sorted(listed)
            for m in range(0, 4):
                for b in combinations(pool, m):
                    if sum(price[k] for k in b) >= -(cash + low) - 1e-6 and (c := side.cost(frozenset(b))) is not None:
                        assert c >= found.cost - 1e-9, "the plan is no dearer than any cover whose members are all listed"
        seen_take += len(takes_)
    assert seen_take >= 10 and seen_wait >= 5 and seen_empty >= 5 and seen_unlisted >= 3 and seen_credit >= 3, (seen_take, seen_wait, seen_empty, seen_unlisted, seen_credit)
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


def _selftest() -> None:
    _selftest_cover()
    _selftest_buys()
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
    assert later_sale(decision.Inputs(money), "p", 10.0) == rules.SALE_ESTIMATE * 10.0, "no price row: the estimate"
    priced = decision.Inputs(money, prices=[decision.Price("p", 1, 10.5, 10.0), decision.Price("p", 3, 11.0, 10.0)])
    assert abs(later_sale(priced, "p", 10.0) - rules.SALE_ESTIMATE * 10.0 * 1.1) < 1e-9, "the price row furthest ahead: 10% above"
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
    standing = [current.Offer("20", "t1", "pending", 2.0, "c20", "", ""), current.Offer("10", "t1", "pending", 2.0, "c10", "", ""),
                current.Offer("30", "t2", "pending", 3.0, "c30", "", ""), current.Offer("40", "t3", "declined", 9.0, "c40", "", ""),
                current.Offer("50", "tx", "pending", 9.0, "c50", "", ""), current.Offer("60", "t3", "pending", None, "c60", "", "")]
    stood = stood_offers(held, standing)
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

    def row(placed: float | None) -> dict[str, Any]:
        return {"player": "p", "pos": "MED", "status": "market", "gain": 1.0, "asking": asking, "bid": advised,
                "win": chance(curve, advised / asking), "placed": placed, "replaces": "x", "per_million": None,
                "price_next": None, "price_lock": None, "from_j": 1}
    cap = 12.5e6
    for placed in (None, 10e6, 10.5e6, 11e6, 12e6, 13e6):
        blind = stance({**row(placed), "bid": None, "win": None}, None, cap)
        assert (blind.amount, blind.win, blind.advised, blind.advised_win) == (placed, None, None, None), "no curve: no chance, nothing advised"
        assert blind.move == ("unknown" if placed is None else "lower" if placed > cap else "hold")
        at = stance(row(placed), curve, cap)
        none = stance({**row(placed), "bid": None, "win": None}, curve, cap)
        assert (at.advised, at.advised_win) == (advised, chance(curve, advised / asking)), "the advised bid is always said"
        if placed is None:
            assert none.move == "pass" and none.to is None
            assert (at.amount, at.win, at.move) == (advised, at.advised_win, "bid"), "no bid placed: the advised one"
            continue
        assert at.amount == placed and at.win == chance(curve, placed / asking), "a bid placed is the line's, at its own chance"
        assert at.move in ("hold", "lower", "withdraw", "raise") and none.move == at.move, "a bid placed is judged by the grid, not by the advice"
        assert (at.to is None) == (at.move in ("hold", "withdraw"))
        (line,) = target_lines({}, [row(placed)], 0.045, 26)
        assert line["placed"] == placed, "a target with your bid on him says it beside the advised one"
    _selftest_placed()
    print("board self-test OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        raise SystemExit(0)
    main()
