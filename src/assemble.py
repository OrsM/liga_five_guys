"""Where the data meets the model: the one place that reads the tables and
builds what the decision works on — the scorer, the market, the league
state and the forecast — so the model modules never read a table
themselves."""
from __future__ import annotations

from functools import cache
from statistics import median

from decide import PRICE_WINDOW, Universe, _fieldable, premium_to_beat
from ffcore.clock import run_now
from ffcore.fixture import difficulty_ratings
from ffcore.forecast import Bootstrap
from ffcore.jornadas import clock_history, jornada_of_match, load_deadline
from ffcore.league import League
from ffcore.market import Market, market_routes, pending
from ffcore.parse import num, ratio, text
from ffcore.players import load_crosswalk, load_players
from ffcore.points import scored
from ffcore.pricing import auction_ratios, steps, trend
from ffcore.schedule import expectations, phantom_fill
from ffcore.score import fit_promoted_discount, per_jornada_current, Scorer, totals
from ffcore.rules import SLOT
from ffcore.season import LeagueState
from ffcore.startprob import StartOdds, calibrate, outcomes
from ffcore.tidy import (DECISIONS, LINEUP_SOURCE, SEASON, age_hours, current,
                         history, read_csv)

__all__ = ["universe", "scorer", "fixture_ratings", "cash_price_history",
           "PRICE_LOG", "APP_FRESH_HOURS"]

APP_FRESH_HOURS = 14.4
PRICE_LOG = "cash_price_log.csv"


def scorer(market: list[dict], xi_rows: list[dict]) -> Scorer:
    xw = load_crosswalk()
    files = sorted(SEASON.glob("points_*.csv"))
    last_season = {r["ff_id"]: {"pts": ratio(r.get("points")) or 0.0,
                                "pj": ratio(r.get("games")) or 0.0}
                   for r in (read_csv(files[-1]) if files else ())
                   if r.get("ff_id")}
    played = scored()
    by_key = per_jornada_current(current("starters"), played,
                                 jornada_of_match(), xw)
    outs = outcomes(history("lineups", LINEUP_SOURCE), current("starters"),
                    clock_history().round_locks, jornada_of_match(), xw)
    return Scorer(
        market, StartOdds(xi_rows, xw, calibrate(outs)), last_season,
        current={k: dict(zip(("pts", "pj", "start_rate", "start_n"),
                             totals(jd)))
                 for k, jd in by_key.items()},
        promoted_discount=fit_promoted_discount(market, last_season, played))


def fixture_ratings(market: list[dict]):
    return difficulty_ratings(market, current("results_history"))


def _pos_of(raw: str) -> str:
    mapped = SLOT.get(raw.lower())
    if mapped:
        return mapped
    return raw if raw in ("POR", "DEF", "MED", "DEL") else "MED"


def cash_price_history() -> float | None:
    seen = [x for r in read_csv(DECISIONS / PRICE_LOG)
            if (x := num(r, "places_per_million")) is not None]
    return median(seen[-PRICE_WINDOW:]) if seen else None


def _updates_to_lock() -> int:
    deadline = load_deadline()
    hours = (deadline - run_now()).total_seconds() / 3600 if deadline else 24.0
    return max(1, round(hours / 24))


def _premium(me: str) -> float:
    mine = {text(r, "user_id") for r in current("api_standings")
            if text(r, "manager") == me}
    return premium_to_beat(auction_ratios(history("api_market"), sorted(
        (a for a in current("api_activity")
         if a["kind"] == "buy" and a["user_id"] not in mine),
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

    teams, mkt = ([dict(r, key=lg.key_of_app(text(r, "player_id")))
                   for r in current(name)] for name in ("api_teams", "api_market"))
    price, route = market_routes(mkt)
    pt_to_key = {r["player_team_id"]: r["key"] for r in teams
                 if r["key"] and r.get("player_team_id")}

    value = {k: rec["value"] for k, rec in players.items() if rec.get("value")}
    received_offers = pending(
        [dict(r, key=pt_to_key.get(r.get("player_team_id") or ""))
         for r in current("api_offers")], "status", "money")
    proceeds = {k: max(value.get(k, 0.0), received_offers.get(k, 0.0))
                for k in lg.squad(me)}
    pos = {k: _pos_of((rec.get("pos") or "").upper())
           for k, rec in players.items()}
    squads = {mgr: {k: pos[k] for k in lg.squad(mgr) if k in pos}
              for mgr in lg.managers}
    per_j, first_jornada_of, _rates, rem, played = expectations(
        sc, fixture_ratings(mkt_rows), set(price).union(*squads.values()), m)
    market = Market(
        cash=lg.cash[me], lam=cash_price_history(), premium=_premium(me),
        name={k: rec.get("name") or k for k, rec in players.items()},
        pos=pos,
        price={k: v for k, v in price.items() if k in players},
        route={k: v for k, v in route.items() if k in players},
        owner={k: v for k, v in lg.owner.items() if k in players},
        value={k: v for k, v in value.items() if k in players},
        proceeds={k: v for k, v in proceeds.items() if k in players},
        my_bid=pending(mkt, "bid_status", "bid_money"),
        trend=trend(steps(history("market")), _updates_to_lock()))
    squads, per_j = phantom_fill(squads, per_j, pos)
    assert all(_fieldable(sq) for sq in squads.values()), squads
    if rem:
        first_jornada_of.update({k: rem[0] for layer in per_j.values()
                                 for k in layer if k.startswith("__phantom_")})

    fc = Bootstrap(per_j, pool=[s.pts for s in scored() if s.games == 1])

    carried = {r["manager"]: num(r, "team_points", default=0.0)
               for r in lg.standings if r.get("manager")}
    return Universe(
        state=LeagueState(squads, rem, me, carried), forecaster=fc,
        market=market,
        rival_cash={h: v for h, v in lg.cash.items() if h != me},
        part_played=played, first_jornada_of=first_jornada_of)
