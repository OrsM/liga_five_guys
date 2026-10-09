"""Where the data meets the model: the one place that reads the tables and
builds what the decision works on — the scorer, the market, the league
state and the forecast — so the model modules never read a table
themselves."""
from __future__ import annotations

from datetime import datetime
from functools import cache

from decide import Universe, _fieldable
from ffcore.crosswalk import Crosswalk
from ffcore.fixture import Ratings, difficulty_ratings
from ffcore.forecast import Bootstrap
from ffcore.jornadas import clock_history, jornada_of_match, load_deadline
from ffcore.league import League
from ffcore.market import Market, market_routes, pending
from ffcore.names import Name, app_id
from ffcore.parse import Rows, text
from ffcore.players import load_crosswalk, load_players
from ffcore.points import scored
from ffcore.pricing import (Momentum, auction_ratios, offer_ratios,
                            premium_to_beat, steps)
from ffcore.schedule import expectations, phantom_fill
from ffcore.score import fit_promoted_discount, per_jornada_current, Scorer, totals
from ffcore.rules import slot
from ffcore.season import LeagueState
from ffcore.startprob import (StartOdds, calibrate, fit_availability,
                              last_fit_listing, outcomes)
from ffcore.tidy import (LINEUP_SOURCE, SEASON, age_hours, current,
                         history, read_csv, typed)

__all__ = ["universe", "scorer", "fixture_ratings", "APP_FRESH_HOURS"]

APP_FRESH_HOURS = 14.4


def scorer(market: Rows, xi_rows: Rows) -> Scorer:
    xw = load_crosswalk()
    files = sorted(SEASON.glob("points_*.csv"))
    last_season = {r["ff_id"]: {"pts": r.get("points") or 0.0,
                                "pj": r.get("games") or 0.0}
                   for r in (typed("points", read_csv(files[-1])) if files else ())
                   if r.get("ff_id")}
    played = scored()
    by_key = per_jornada_current(current("starters"), played,
                                 jornada_of_match(), xw)
    outs = outcomes(history("lineups", LINEUP_SOURCE), current("starters"),
                    clock_history().round_locks, jornada_of_match(), xw)
    season = {k: totals(jd) for k, jd in by_key.items()}
    record = {k: (apps, squads) for k, (_pts, apps, squads) in season.items()}
    return Scorer(
        market, StartOdds(xi_rows, xw, calibrate(outs),
                          fit_availability(outs), record,
                          last_fit_listing(outs), app_status(xw)),
        last_season,
        current={k: {"pts": pts, "pj": apps} for k, (pts, apps, _n) in season.items()},
        promoted_discount=fit_promoted_discount(market, last_season, played))


def app_status(xw: Crosswalk) -> dict[str, str]:
    """The LaLiga app's status for each player it lists."""
    return {k: r["player_status"] for r in current("api_players_all")
            if (k := xw.player(app_id=app_id(r))) and r.get("player_status")}


def open_clauses(teams: Rows) -> dict[str, float]:
    """Each owned player's release clause: any can be paid now. Checked
    against the league's own clause buys: each paid exactly the listed
    clause, to the owner, and two came before the player's buyout_until
    (Luismi Cruz, Yamal), so that date protects no one."""
    return {r["key"]: amount for r in teams
            if r.get("key") and (amount := r.get("buyout"))}


def fixture_ratings(market: Rows) -> Ratings:
    return difficulty_ratings(market, current("results_history"))


def _pos_of(raw: str) -> str:
    return slot(raw) or "MED"


def _nights_left(offers: Rows) -> int:
    """The game's nightly offers still to come before the lock: one as each
    standing offer expires, then one a day."""
    ends = [datetime.fromisoformat(r["expires_at"]) for r in offers
            if r.get("status") == "pending" and r.get("expires_at")]
    lock = load_deadline()
    if not ends or lock is None:
        return 0
    days = (lock - max(ends)).total_seconds() / 86400
    return max(0, int(days) + 1) if days > 0 else 0


def _premium(me: str) -> float:
    """The bid over asking that your own auctions were won at: rivals'
    winning bids are mostly the boldest bidder's, 1.19x against your 1.00x
    (2026-10-04)."""
    mine = {text(r, "user_id") for r in current("api_standings")
            if text(r, "manager") == me}
    return premium_to_beat(auction_ratios(history("api_market"), sorted(
        (a for a in current("api_activity")
         if a["kind"] == "buy" and a["user_id"] in mine),
        key=lambda a: a["at"])))


@cache
def universe() -> Universe:
    age = age_hours("api_teams")
    if age is None or age > APP_FRESH_HOURS:
        raise SystemExit("the app's squads are %s old; no board is built from "
                         "them" % ("unknown" if age is None else "%.0fh" % age))
    lg = League.load()
    mkt_rows = current("market")
    sc = scorer(mkt_rows, current("lineups", LINEUP_SOURCE))
    me = lg.me
    players = load_players()
    m = current("matches")

    teams, mkt = ([dict(r, key=lg.key_of_app(app_id(r)))
                   for r in current(name)] for name in ("api_teams", "api_market"))
    price, route = market_routes(mkt)
    pt_to_key = {r["player_team_id"]: r["key"] for r in teams
                 if r["key"] and r.get("player_team_id")}

    value = {k: rec["value"] for k, rec in players.items() if rec.get("value")}
    offers = current("api_offers")
    received_offers = pending(
        [dict(r, key=pt_to_key.get(r.get("player_team_id") or ""))
         for r in offers], "status", "money")
    pos = {k: _pos_of((rec.get("pos") or "").upper())
           for k, rec in players.items()}
    squads = {mgr: {k: pos[k] for k in lg.squad(mgr) if k in pos}
              for mgr in lg.managers}
    per_j, first_jornada_of, _rates, rem, played = expectations(
        sc, fixture_ratings(mkt_rows), set(price).union(*squads.values()), m)
    prices = Momentum(steps(history("market")))
    market = Market(
        cash=lg.cash[me], premium=_premium(me),
        name={k: Name(rec.get("name") or k) for k, rec in players.items()},
        pos=pos,
        price={k: v for k, v in price.items() if k in players},
        route={k: v for k, v in route.items() if k in players},
        owner={k: v for k, v in lg.owner.items() if k in players},
        paid={k: v for k, v in lg.paid.items() if k in players},
        offer={k: v for k, v in received_offers.items() if k in lg.squad(me)},
        offer_ratios=tuple(offer_ratios(history("api_offers"), history("api_teams"))),
        nights_left=_nights_left(offers),
        value={k: v for k, v in value.items() if k in players},
        my_bid=pending(mkt, "bid_status", "bid_money"),
        trend=prices.trend(), carry=prices.carry,
        clause={k: v for k, v in open_clauses(teams).items() if k in players})
    squads, per_j = phantom_fill(squads, per_j, pos)
    assert all(_fieldable(sq) for sq in squads.values()), squads
    if rem:
        first_jornada_of.update({k: rem[0] for layer in per_j.values()
                                 for k in layer if k.startswith("__phantom_")})

    fc = Bootstrap(per_j, pool=[s.pts for s in scored() if s.games == 1])

    carried = {r["manager"]: r.get("team_points") or 0.0
               for r in lg.standings if r.get("manager")}
    return Universe(
        state=LeagueState(squads, rem, me, carried), forecaster=fc,
        market=market,
        rival_cash={h: v for h, v in lg.cash.items() if h != me},
        part_played=played, first_jornada_of=first_jornada_of)
