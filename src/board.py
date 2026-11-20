"""The board stage: decisions.json, the phone's copy of the board, from the league at t. Your cash and what you can
bid, your squad with its eleven, the players on the market who would add points to it (buys, most first, each with
the bid to make and, for the first few, the sales that fund it: approach.approach), and each standing offer on your
players ranked by what taking it costs your eleven (eleven.remove). The one line act.py takes is do: list your unlisted players at
their value."""
from __future__ import annotations

import json
import random
import sys
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, NamedTuple

import decision
import eleven
import worth
from approach import Approach, Club, Rounds, Side, approach, settle
from ffcore import current, league, loader, price_fold, state
from ffcore import forecast as model, pricing, rules, tidy
from ffcore import observations as ob
from ffcore import records as rec
from ffcore.auction import Curve, chance
from ffcore.clock import cut
from ffcore.store import RunRecords
from ffcore.rules import POSITIONS, bid_limit
from health import Health
import valuation
from valuation import READERS
from ffcore.tidy import REPORTS
from stats import percentile

__all__ = ["board", "main"]

Doc = dict[str, Any]
TOP_BUYS = 10
TOP_FUNDED = 3
TOP_TARGETS = 10


def stamped(t: str) -> str:
    return t[:13] + ":" + t[13:]


def next_value(inp: decision.Inputs) -> dict[str, float]:
    _, cells = decision.cells_of(inp)
    return cells.get(min((x.jornada for x in inp.locks), default=min(cells, default=0)), {})


def squad_rows(inp: decision.Inputs, names: dict[str, str]) -> list[Doc]:
    pos, _ = decision.cells_of(inp)
    mine = decision.squad_of(inp)
    e = next_value(inp)
    fielded = decision.lineup_of(inp, pos).eleven
    settled = eleven.best_eleven(mine, pos, e, fielded)
    best = settled.eleven if settled else frozenset()
    xi = fielded if fielded is not None else best
    order = sorted(mine, key=lambda k: (k not in xi, POSITIONS.index(pos[k]) if k in pos else len(POSITIONS), -e.get(k, 0.0), k))
    return [{"name": names.get(k, k), "pos": pos.get(k, ""), "next": e.get(k, 0.0), "xi": k in xi, "best": k in best,
             "sold": False, "key": k} for k in order]


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


def lineup_doc(inp: decision.Inputs, names: dict[str, str]) -> Doc:
    pos, _ = decision.cells_of(inp)
    mine = decision.squad_of(inp)
    e = next_value(inp)
    got = decision.lineup_of(inp, pos)
    settled = eleven.best_eleven(mine, pos, e, got.eleven)
    if got.eleven is None or settled is None:
        return {"eleven": None, "message": got.message or "the squad cannot field a legal eleven", "unmapped": list(got.unmapped),
                "points": None, "best": eleven.total(settled.eleven, e) if settled else None, "advice": []}
    slots = {s.player: s.slot for s in inp.lineup}
    order = sorted(got.eleven, key=lambda k: (POSITIONS.index(slots[k]) if slots[k] in POSITIONS else len(POSITIONS), -e.get(k, 0.0), k))
    return {"eleven": [names.get(k, k) for k in order], "message": "", "unmapped": list(got.unmapped),
            "points": eleven.total(got.eleven, e), "best": eleven.total(settled.eleven, e), "advice": advice(settled.swaps, names)}


def context(inp: decision.Inputs, offers: worth.OfferDistribution = worth.NO_OFFERS, rate: float = worth.STOPGAP_RATE) -> worth.Context:
    pos, cells = decision.cells_of(inp)
    locks = decision.lock_times(inp)
    window = decision.window_of(locks, cells, None)
    return worth.Context(pos, {name: getattr(window, name) for name in READERS}, inp.money.cash, offers, rate,
                         decision.squad_of(inp), cells, locks)


def later_sale(inp: decision.Inputs, player: str, value: float) -> float:
    rows = [x for x in inp.prices if x.player == player]
    if not rows:
        return rules.SALE_ESTIMATE * value
    return rules.SALE_ESTIMATE * value * (1 + decision.rise(max(rows, key=lambda x: x.h)))


def offers_at(world: state.World) -> worth.OfferDistribution:
    """The distribution of offer over value (worth.offer_ratio_distribution) of the market offers the world has seen."""
    return worth.offer_ratio_distribution(x.ratio for x in current.offer_ratios(world))


def is_bench(lost: Mapping[Any, float] | None) -> bool:
    """True when the eleven loses nothing at any window (the lost points of each window, keyed as worth.lost_points or as
    a sells row's "lost"). False when
    lost is None: a player the eleven cannot do without is not a bench player."""
    return lost is not None and not any(lost.values())


def offer_note(ratio: float | None, offers: worth.OfferDistribution, lost: Mapping[str, float] | None) -> str:
    """One line of facts about a standing offer: its ratio over value; where it stands among the nightly offers seen
    (offers.ratios, none: no such clause; n shown); what the eleven loses by it (lost by window as worth.lost_points gives
    it: 0 at every window is a bench player; None says there is no legal eleven without him, a replacement must be bought
    first). No advice."""
    share = None if ratio is None else worth.offer_percentile(offers, ratio)
    parts = [] if ratio is None else ["offer %.2f x value" % ratio]
    if share is not None:
        parts.append("at or above %.0f%% of the %d nightly offers seen" % (100 * share, len(offers.ratios)))
    if lost is None:
        parts.append("there is no legal eleven without him: a replacement must be bought first")
    elif is_bench(lost):
        parts.append("the eleven loses 0 points (bench)")
    else:
        parts.append("the eleven loses %.1f points over the next 4 jornadas, %.1f over the season"
                     % (lost["next4"], lost["season"]))
    return "; ".join(parts)


def offer_lines(inp: decision.Inputs, names: dict[str, str], got: list[decision.Offer], known: dict[str, decision.Fact],
                offers: worth.OfferDistribution = worth.NO_OFFERS) -> list[Doc]:
    ctx = context(inp)
    out = []
    for offer in got:
        k, value, money = offer.player, offer.value, offer.money
        fact = known.get(k, decision.UNKNOWN)
        verdict = worth.LIVE(worth.Standing(k, value, money, later_sale(inp, k, value)), ctx)
        lost = verdict.lost
        ratio = money / value if value else None
        name = names.get(k, k)
        out.append({
            "name": name, "pos": fact.pos or ctx.pos.get(k, ""), "what": "sell", "label": "sell " + name, "step": "take offer",
            "offer": money, "waiting": None, "trend": fact.trend, "plan": False, "amount": money, "done": False, "key": k,
            "gain": None if lost is None else 0.0 - lost["season"], "per": "season", "more": False, "detail": None, "trace": None, "flip": None,
            "ratio": ratio, "lost": None if lost is None else dict(lost),
            "offer_percentile": None if ratio is None else worth.offer_percentile(offers, ratio), "offers_seen": len(offers.ratios),
            "note": offer_note(ratio, offers, lost),
            "per_million": verdict.key,
            "facts": [["value", value, "money"], ["offer", money, "money"], ["offer over value", ratio, "ratio"],
                      ["points lost, next 4", None if lost is None else lost["next4"], "points"],
                      ["season", None if lost is None else lost["season"], "points"]],
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
    value from its facts, bench as is_bench says it, funds the names of its "for" joined by ';') and the offer's id and
    creation stamp from the stood offer of that player and money (the lowest id first when several are alike)."""
    waiting: dict[tuple[str, float], list[Stood]] = {}
    for x in sorted(stood, key=lambda s: s.offer_id):
        waiting.setdefault((x.player, x.money), []).append(x)
    rows = []
    for sale in sells:
        given = waiting[(sale["key"], sale["offer"])].pop(0)
        value = next(f.value for f in map(lambda f: Shown(*f), sale["facts"]) if f.label == "value")
        lost = sale["lost"]
        rows.append({"cut": at, "player": sale["key"], "offer_id": given.offer_id, "created_at": given.created_at,
                     "offer": sale["offer"], "value": value, "ratio": sale["ratio"], "percentile": sale["offer_percentile"],
                     "offers_seen": sale["offers_seen"], "lost4": None if lost is None else lost["next4"],
                     "lost8": None, "lost_season": None if lost is None else lost["season"],
                     "funds": ";".join(sale["for"]), "bench": is_bench(lost),
                     "trend": sale["trend"]})
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
            "gain": found.gain, "points_lost_if_won": found.cost_won, "points_lost_if_lost": found.cost_lost, "net": found.net}


class Stance(NamedTuple):
    """Where a buy's bid stands: the bid the line is on (yours once one is placed, else the advised) and its chance,
    the bid advised and its chance, and the move: bid (none placed), raise (the advised is over yours) or hold. A bid
    placed is never replaced by a lower one: lowering it is not advised. With no curve for his auction nothing is
    advised and no chance is known (None): the move is hold on a bid placed, else unknown."""
    amount: float | None
    win: float | None
    advised: float | None
    advised_win: float | None
    move: str


def stance(r: dict[str, Any], curve: Curve | None) -> Stance:
    placed, advised, advised_win = r["placed"], r["bid"], r["win"]
    if curve is None or advised is None:
        return Stance(placed, None, None, None, "unknown" if placed is None else "hold")
    if placed is None:
        return Stance(advised, advised_win, advised, advised_win, "bid")
    return Stance(placed, chance(curve, placed / r["asking"]), advised, advised_win, "raise" if advised > placed else "hold")


def buy_lines(inp: decision.Inputs, names: dict[str, str], club: Club, got: list[dict[str, Any]]) -> list[Doc]:
    pos, cells = decision.cells_of(inp)
    locks = decision.lock_times(inp)
    curves = decision.curves_of(inp)
    seen = {a.player: a for a in inp.auctions}
    bids = {b.player: b.amount for b in inp.bids}
    out = []
    for i, r in enumerate([r for r in got if r["status"] == "market" and r["gain"] > 0][:TOP_BUYS]):
        k, placed = r["player"], r["placed"]
        at = stance(r, curves.get(k))
        amount, win = at.amount, at.win
        auction = seen.get(k)
        others = (len(bids) - (k in bids), sum(bids.values()) - bids.get(k, 0.0))
        rounds = Rounds(cells, decision.window_of(locks, cells, None), decision.window_of(locks, cells, r["close_at"]))
        funding = funding_doc(club, names, approach(club._replace(pending=others), k, amount, win, rounds), amount) \
            if i < TOP_FUNDED and amount is not None and win is not None else None
        name = names.get(k, k)
        out.append({
            "name": name, "pos": r["pos"], "what": "buy", "label": "buy " + name, "step": "placed" if placed is not None else "bid",
            "price": r["asking"], "asking": r["asking"], "amount": amount, "win": win, "advised": at.advised, "advised_win": at.advised_win,
            "move": at.move, "plan": False, "done": placed is not None,
            "key": k, "gain": r["gain"], "per": "season", "more": False, "detail": None, "trace": None, "flip": None,
            "replaces": names.get(r["replaces"], r["replaces"]), "per_million": r["per_million"],
            "price_next": r["price_next"], "price_lock": r["price_lock"],
            "facts": [["asking", r["asking"], "money"], ["bid", amount, "money"], ["chance to win", win, "chance"]]
            + ([["advised bid", at.advised, "money"], ["chance at the advised bid", at.advised_win, "chance"]] if placed is not None else [])
            + [["points added", r["gain"], "points"]],
            "bids": auction.bids if auction else None,
            "auction": {"yours": bool(auction.mine), "listings": auction.n, "thin": bool(auction.thin), "cell": auction.cell}
            if auction else {"yours": False, "listings": 0, "thin": True, "cell": ""},
            "funding": funding, "accepts": [], "act": []})
    return out


def target_lines(names: dict[str, str], got: list[dict[str, Any]]) -> list[Doc]:
    return [{"name": names.get(r["player"], r["player"]), "pos": r["pos"], "status": r["status"], "gain": r["gain"],
             "asking": r["asking"], "bid": r["bid"], "win": r["win"], "placed": r["placed"],
             "replaces": names.get(r["replaces"], r["replaces"]),
             "per_million": r["per_million"], "price_next": r["price_next"], "price_lock": r["price_lock"],
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


def funds(buys: list[Doc], sells: list[Doc]) -> None:
    for sale in sells:
        sale["for"] = [b["name"] for b in buys
                       if any(t["name"] == sale["name"] for t in (b.get("funding") or {}).get("tonight", []))]


def cover_lines(club: Club, cells: valuation.Cells, window: Iterable[int], sells: Sequence[Doc]) -> list[Doc]:
    """The lines of do that bring cash to 0 or more at the lock when it is below 0 (approach.settle): one "sell" line for
    each offer to take, a copy of its row of sells marked "needed" with the points the whole set loses (set_points_lost) and the cash after it
    (set_cash_after), not one sale's,
    or one "short" line with the money the offers lack. No lines when cash is 0 or more or no eleven is fielded."""
    got = settle(club, cells, window)
    if got is None or club.cash >= 0:
        return []
    if not got.feasible:
        why = "cash %.0f is below 0 and the standing offers lack %.0f" % (club.cash, got.short) if got.short \
            else "cash %.0f is below 0 and no set of the standing offers brings it to 0 and keeps an eleven" % club.cash
        return [{"what": "short", "label": "short of cash at the lock", "step": "list", "needed": True, "short": got.short,
                 "why": why, "accepts": [], "act": []}]
    why = "cash %.0f is below 0: these offers bring it to %.0f at the lock, losing %.1f points" % (club.cash, club.cash + got.raised, got.lost)
    return [{**sale, "needed": True, "why": why, "set_points_lost": got.lost, "set_cash_after": club.cash + got.raised}
            for sale in sells if sale["key"] in got.sells]


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
          extras: decision.Extras, health: Iterable[Health], offers: worth.OfferDistribution = worth.NO_OFFERS) -> Doc:
    """The decisions document of the cut; extras are what the World gives beside the inputs, health the status of each
    source (health.parser_health over the loaded snapshots) and offers the nightly offers seen by the cut (offers_at)."""
    cash, team_value, debt = inp.money.cash, inp.money.team_value, inp.money.debt
    locks = [x.lock for x in inp.locks]
    bids = sum(b.amount for b in inp.bids)
    known = extras.known
    names = {k: fact.name for k, fact in known.items()}
    squad = squad_rows(inp, names)
    mine = decision.squad_of(inp)
    offered = extras.offers
    pos = {p.player: p.pos for p in inp.players if p.pos in POSITIONS}
    club = Club(mine, pos, {h.player: h.value for h in extras.held},
                {o.player: o.money for o in offered}, cash, team_value, (len(inp.bids), bids), debt=debt)
    alerts = [{"name": names.get(r["player"], r["player"]), "gain": r["gain"], "asking": r["asking"], "bid": r["bid"],
               "win": r["win"], "placed": r["placed"]} for r in arrival_rows(got, arrivals)]
    buys = buy_lines(inp, names, club, got)
    sells = offer_lines(inp, names, offered, known, offers)
    funds(buys, sells)
    _, cells = decision.cells_of(inp)
    owed = cover_lines(club, cells, decision.window_of(decision.lock_times(inp), cells, None).season, sells) if locks else []
    return {
        "generated_at": stamped(cut()), "market_at": market_at(extras.market_read), "sources": sources(health),
        "lock_at": min(locks).replace("Z", ":00+00:00") if locks else None,
        "cash": cash, "cash_locked": bids, "can_bid": max(0.0, bid_limit(cash, team_value, debt) - bids),
        "ping": "on the market: " + ", ".join("%s (+%.0f points)" % (a["name"], a["gain"]) for a in alerts) if alerts else "",
        "do": owed + list_line(inp, names, extras), "lineup": lineup_doc(inp, names), "buys": buys, "targets": target_lines(names, got),
        "alerts": alerts, "auction": auction_card(),
        "sells": sells,
        "squad": [{k: v for k, v in r.items() if k != "key"} for r in squad]}


# The constants a run is built with, by module: what params records as such.
NAMES: dict[str, tuple[str, ...]] = {
    "tidy": ("APP_FRESH_HOURS", ),
    "forecast": ("ABSENT_START", "FIXTURE_K", "MIN_POOL", "NEUTRAL_START", "PERSISTENT_SHARE", "PICK_K", "P_DECAY", "P_LIFT", "P_LISTING", "SHRINK_K"),
    "pricing": ("AUCTION_MIN", "CLIP", "PRICE_FOCUS", "PRICE_W0", "PRICE_W1", "PRICE_W2", "PRICE_WINDOW"),
    "rules": ("DEBT_SHARE", "MATCH_LEN"),
}
MODULES: dict[str, Any] = {"tidy": tidy, "forecast": model, "pricing": pricing,
                           "rules": rules}


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
    records.add("params", rows)
    got = decision.opportunity_rows(d.inputs)
    records.add("dec_opportunity", [{"cut": cut(), **r} for r in got])
    doc = board(d.inputs, got, decision.arrivals(got, loaded.previous), d.extras, loader.health_at(loaded, cut()), offers)
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


def _sells_of(squad: dict[str, str], offered: Iterable[str]) -> list[Doc]:
    ks = sorted(squad)
    inp = decision.Inputs(decision.Money(0.0, 0.0, 0.0, 0, 0), players=[decision.Player(k, squad[k], 1, None) for k in ks],
                          cells=[decision.Cell(k, j, 1.0 + i % 7 + j / 10) for i, k in enumerate(ks) for j in range(1, 11)])
    names = {k: k for k in ks}
    return offer_lines(inp, names, [decision.Offer(k, 5e6, 5e6 + 1e5 * i) for i, k in enumerate(offered)], {})


def _selftest_sells() -> None:
    shape = {"POR": 1, "DEF": 4, "MED": 4, "DEL": 2}
    squad = {"%s%d" % (p, i): p for p, n in shape.items() for i in range(n)}
    bare = _sells_of(squad, squad)
    whole = eleven.total(frozenset(squad), {k: sum(1.0 + i % 7 + j / 10 for j in range(1, 11)) for i, k in enumerate(sorted(squad))})
    assert len(bare) == len(squad) and all(r["lost"] is None and r["gain"] is None and r["per_million"] is None for r in bare), \
        "eleven players only: no row carries a number"
    assert all("no legal eleven without him" in r["note"] and "bench" not in r["note"] for r in bare)
    assert all(v is None for r in bare for f in r["facts"] if f[0] in ("points lost, next 4", "season") for v in [f[1]])
    assert all(r["lost"] != dict.fromkeys(READERS, whole) for r in bare), "never the whole eleven as one player's loss"
    assert [r["key"] for r in bare] == sorted(squad), "rows with no figure: in key order"
    rows = [{"what": "sell", "key": r["key"], "lost": r["lost"], "offer": r["offer"], "facts": r["facts"], "ratio": r["ratio"],
             "offer_percentile": None, "offers_seen": 0, "trend": None, "for": []} for r in bare]
    made = offer_rows("cut", rows, [Stood(r["key"], r["offer"], "id" + r["key"], "c") for r in rows])
    assert all(m["lost4"] is None and m["lost8"] is None and m["lost_season"] is None and not m["bench"] for m in made), \
        "the record states no number and no bench for a sale with no legal eleven"
    backed = {**squad, "DEF9": "DEF", "MED9": "MED"}
    rows = _sells_of(backed, backed)
    figures = [r for r in rows if r["lost"] is not None]
    assert figures and rows[:len(figures)] == figures and all(r["lost"] is None for r in rows[len(figures):]), "figures first"
    assert all(r["per"] == "season" and r["gain"] == -r["lost"]["season"] for r in figures), "one window in a row"
    assert len({tuple(r["lost"].values()) for r in figures}) > 1, "the figures differ per player"
    assert next(r for r in rows if r["key"] == "POR0")["lost"] is None, "the only keeper with no bench keeper"


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


def _selftest() -> None:
    _selftest_params()
    _selftest_alerts()
    _selftest_sells()
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
    own = {"m%02d" % i: p for i, p in enumerate(["POR"] * 2 + ["DEF"] * 5 + ["MED"] * 5 + ["DEL"] * 3)}
    worth_of = {k: 4e6 for k in own}
    points = {k: float(i % 4) for i, k in enumerate(sorted(own))}
    points |= {"m00": 0.0, "m01": 0.0}
    rows: list[Doc] = [{"what": "sell", "key": k, "name": k, "offer": 3e6} for k in ("m00", "m05", "m09", "m14")]
    for cash in (-5e6, -1e6, 0.0, 2e6):
        club = Club(frozenset(own), own, worth_of, {r["key"]: r["offer"] for r in rows}, cash, 5e7)
        lines = cover_lines(club, {1: points}, (1,), rows)
        if cash >= 0:
            assert lines == [], "cash 0 or more: no line, the board is unchanged"
            continue
        assert lines and all(x["needed"] and x["what"] == "sell" for x in lines)
        assert cash + sum(x["offer"] for x in lines) >= 0 and all(x["set_cash_after"] == lines[0]["set_cash_after"] >= 0 for x in lines)
        assert lines[0]["key"] == "m00", "a bench player is sold first"
    short = cover_lines(Club(frozenset(own), own, worth_of, {"m00": 1e6}, -5e6, 5e7), {1: points}, (1,), rows[:1])
    assert [x["what"] for x in short] == ["short"] and short[0]["short"] == 4e6, "offers that cannot cover say the shortfall"
    money = decision.Money(0.0, 0.0, 0.0, 0, 0)
    assert later_sale(decision.Inputs(money), "p", 10.0) == rules.SALE_ESTIMATE * 10.0, "no price row: the estimate"
    priced = decision.Inputs(money, prices=[decision.Price("p", 1, 10.5, 10.0), decision.Price("p", 3, 11.0, 10.0)])
    assert abs(later_sale(priced, "p", 10.0) - rules.SALE_ESTIMATE * 10.0 * 1.1) < 1e-9, "the price row furthest ahead: 10% above"
    seen = worth.offer_ratio_distribution([0.9, 0.95, 1.0, 1.05, 1.1])
    bench_note = offer_note(1.05, seen, {"lock": 0.0, "next4": 0.0, "season": 0.0})
    field_note = offer_note(1.05, seen, {"lock": 3.2, "next4": 3.2, "season": 9.8})
    bare_note = offer_note(1.05, worth.NO_OFFERS, {"lock": 0.0, "next4": 0.0, "season": 0.0})
    assert "1.05" in bench_note and "80%" in bench_note and "5 nightly offers" in bench_note and bench_note.endswith("0 points (bench)")
    assert "bench" not in field_note and "3.2" in field_note and "9.8" in field_note and "80%" in field_note
    assert "nightly" not in bare_note and "bench" in bare_note, "no offers seen: no percentile clause"
    assert offer_note(None, seen, {"lock": 0.0, "next4": 0.0, "season": 0.0}) == "the eleven loses 0 points (bench)", "no value: no ratio clause"
    none_note = offer_note(1.05, seen, None)
    assert "no legal eleven without him" in none_note and "bench" not in none_note and "loses" not in none_note, "no legal eleven: said so, no number"
    assert not is_bench(None) and is_bench({"lock": 0.0, "next4": 0.0, "season": 0.0}) and not is_bench({"next4": 0.0, "season": 0.1})
    for text in (bench_note, field_note, bare_note, none_note):
        assert not any(w in text.lower() for w in ("take", "hold", "sell", "accept", "should", "reserve")), "facts, no advice"
    asked = [{"key": "p", "offer": 2.0, "ratio": 1.1, "offer_percentile": 0.8, "offers_seen": 5, "trend": None, "for": ["A", "B"],
              "lost": {"next4": 0.0, "season": 0.0}, "facts": [["value", 1.8, "money"], ["offer", 2.0, "money"]]},
             {"key": "p", "offer": 2.0, "ratio": None, "offer_percentile": None, "offers_seen": 0, "trend": 0.5, "for": [],
              "lost": {"next4": 1.0, "season": 3.0}, "facts": [["value", 0.0, "money"]]},
             {"key": "q", "offer": 3.0, "ratio": 1.0, "offer_percentile": 0.5, "offers_seen": 5, "trend": 1.0, "for": [],
              "lost": {"next4": 0.0, "season": 0.5}, "facts": [["value", 3.0, "money"]]}]
    held = [decision.Held("p", "t1", None, 1.8), decision.Held("q", "t2", None, 3.0), decision.Held("r", "t3", None, 1.0)]
    standing = [current.Offer("20", "t1", "pending", 2.0, "c20", "", ""), current.Offer("10", "t1", "pending", 2.0, "c10", "", ""),
                current.Offer("30", "t2", "pending", 3.0, "c30", "", ""), current.Offer("40", "t3", "declined", 9.0, "c40", "", ""),
                current.Offer("50", "tx", "pending", 9.0, "c50", "", ""), current.Offer("60", "t3", "pending", None, "c60", "", "")]
    stood = stood_offers(held, standing)
    assert sorted(x.offer_id for x in stood) == ["10", "20", "30"], "pending, with money, on a player held"
    made = offer_rows("cut", asked, stood)
    assert [(m["player"], m["offer_id"], m["created_at"]) for m in made] == [("p", "10", "c10"), ("p", "20", "c20"), ("q", "30", "c30")]
    assert [m["offer_id"] for m in offer_rows("cut", asked, stood[::-1])] == ["10", "20", "30"], "the order of the standing offers does not matter"
    assert [(m["value"], m["funds"], m["bench"]) for m in made] == [(1.8, "A;B", True), (0.0, "", False), (3.0, "", False)]
    assert all(set(m) == set(rec.DecOffer._fields) for m in made), "a field for each field of the kind, no more"
    assert advice((("c", "b", 2.0), ("d", "c", 1.0), ("e", "x", 0.5)), {"d": "D"}) == [
        {"in": "D", "out": "b", "gain": 3.0}, {"in": "e", "out": "x", "gain": 0.5}], "a player swapped in and then out is one swap"
    curve: Curve = ((1.0, 0.2), (1.1, 0.6), (1.3, 1.0))
    asking, advised = 10e6, 11e6

    def row(placed: float | None) -> dict[str, Any]:
        return {"player": "p", "pos": "MED", "status": "market", "gain": 1.0, "asking": asking, "bid": advised,
                "win": chance(curve, advised / asking), "placed": placed, "replaces": "x", "per_million": None,
                "price_next": None, "price_lock": None}
    for placed in (None, 10e6, 10.5e6, 11e6, 12e6, 13e6):
        blind = stance({**row(placed), "bid": None, "win": None}, None)
        assert (blind.amount, blind.win, blind.advised, blind.advised_win) == (placed, None, None, None), "no curve: no chance, nothing advised"
        assert blind.move == ("unknown" if placed is None else "hold")
        at = stance(row(placed), curve)
        assert (at.advised, at.advised_win) == (advised, chance(curve, advised / asking)), "the advised bid is always said"
        if placed is None:
            assert (at.amount, at.win, at.move) == (advised, at.advised_win, "bid"), "no bid placed: the advised one"
            continue
        assert at.amount == placed and at.win == chance(curve, placed / asking), "a bid placed is the line's, at its own chance"
        assert at.move == ("raise" if advised > placed else "hold"), "the only move from a bid placed is up"
        assert at.win is not None and at.advised_win is not None
        assert at.move == "hold" or at.advised_win >= at.win, "a raise never lowers the chance"
        (line,) = target_lines({}, [row(placed)])
        assert line["placed"] == placed, "a target with your bid on him says it beside the advised one"
    print("board self-test OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        raise SystemExit(0)
    main()
