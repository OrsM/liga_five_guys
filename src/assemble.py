"""Where the data meets the model: the one place that reads the tables and
builds what the decision works on — the scorer, the market, the league
state and the forecast — so the model modules never read a table
themselves."""
from __future__ import annotations

from contextlib import closing
from functools import cache
from typing import Any, NamedTuple

from decide import CONFIDENCE, FINAL_TRIALS, Universe, _fieldable
from ffcore.db import connect, named, rows
from ffcore.fixture import FIX_BAND, MIN_AD_MATCHES, Ratings, difficulty_ratings
from ffcore.forecast import MIN_POOL, PERSISTENT_SHARE, Bootstrap
from ffcore.jornadas import clock_history, jornada_of_match, load_deadline
from ffcore.league import League
from ffcore.market import Market, market_routes, pending
from ffcore.names import Name
from ffcore.parse import Rows, kickoff_stamp
from ffcore.players import load_players
from ffcore.points import scored
from ffcore.pricing import BID_BEATS, CLIP, PRICE_WINDOW
from ffcore.schedule import expectations, phantom_fill
from ffcore.score import (PROMOTED_DISCOUNT_K, SHRINK_K, fit_promoted_discount,
                          per_jornada_current, Scorer, totals)
from ffcore.rules import position
from ffcore.season import LeagueState
from ffcore.startprob import (AVAIL_K, FALLBACK_K, PICK_K, Availability, Calibration,
                              StartOdds, calibrate, fit_availability, last_fit_listing,
                              outcomes)
from ffcore.tidy import (LINEUP_SOURCE, SEASON, age_hours, current,
                         history, read_csv, typed)
from stats import percentile

__all__ = ["universe", "built", "params", "scorer", "fixture_ratings", "price_momentum",
           "price_change", "premium", "write_constants", "market_offer_ratios",
           "APP_FRESH_HOURS"]

APP_FRESH_HOURS = 14.4

# The constants a run is built with, by module: what params records as such.
CONSTANTS: dict[str, dict[str, float]] = {
    "assemble": {"APP_FRESH_HOURS": APP_FRESH_HOURS},
    "decide": {"CONFIDENCE": CONFIDENCE, "FINAL_TRIALS": FINAL_TRIALS},
    "fixture": {"FIX_BAND": FIX_BAND, "MIN_AD_MATCHES": MIN_AD_MATCHES},
    "forecast": {"MIN_POOL": MIN_POOL, "PERSISTENT_SHARE": PERSISTENT_SHARE},
    "pricing": {"BID_BEATS": BID_BEATS, "CLIP": CLIP, "PRICE_WINDOW": PRICE_WINDOW},
    "score": {"PROMOTED_DISCOUNT_K": PROMOTED_DISCOUNT_K, "SHRINK_K": SHRINK_K},
    "startprob": {"AVAIL_K": AVAIL_K, "FALLBACK_K": FALLBACK_K, "PICK_K": PICK_K},
}


def scorer(market: Rows, xi_rows: Rows) -> Scorer:
    files = sorted(SEASON.glob("points_*.csv"))
    last_season = {r["ff_id"]: {"pts": r.get("points") or 0.0,
                                "pj": r.get("games") or 0.0}
                   for r in (typed("points", read_csv(files[-1])) if files else ())
                   if r.get("ff_id")}
    played = scored()
    by_key = per_jornada_current(current("starters"), played, jornada_of_match())
    outs = outcomes(history("lineups", LINEUP_SOURCE), current("starters"),
                    clock_history().round_locks, jornada_of_match())
    season = {k: totals(jd) for k, jd in by_key.items()}
    record = {k: (apps, squads) for k, (_pts, apps, squads) in season.items()}
    return Scorer(
        market, StartOdds(xi_rows, calibrate(outs),
                          fit_availability(outs), record,
                          last_fit_listing(outs), app_status()),
        last_season,
        current={k: {"pts": pts, "pj": apps} for k, (pts, apps, _n) in season.items()},
        promoted_discount=fit_promoted_discount(market, last_season, played))


def app_status() -> dict[str, str]:
    """The LaLiga app's status for each player it lists."""
    return {k: r["player_status"] for r in current("api_players_all")
            if (k := r.get("player")) and r.get("player_status")}


def open_clauses(teams: Rows) -> dict[str, float]:
    """Each owned player's release clause: any can be paid now. Checked
    against the league's own clause buys: each paid exactly the listed
    clause, to the owner, and two came before the player's buyout_until
    (Luismi Cruz, Yamal), so that date protects no one."""
    return {r["key"]: amount for r in teams
            if r.get("key") and (amount := r.get("buyout"))}


def fixture_ratings(market: Rows) -> Ratings:
    return difficulty_ratings(market, current("results_history"))


def _nights_left(offers: Rows) -> int:
    """The game's nightly offers still to come before the lock: one as each
    standing offer expires, then one a day."""
    ends = [when for r in offers if r.get("status") == "pending"
            and (when := kickoff_stamp(r.get("expires_at")))]
    lock = load_deadline()
    if not ends or lock is None:
        return 0
    days = (lock - max(ends)).total_seconds() / 86400
    return max(0, int(days) + 1) if days > 0 else 0


def write_constants() -> None:
    """CONSTANTS into lfg.db, for the views that use them."""
    with closing(connect()) as db, db:
        db.execute("DELETE FROM constants")
        db.executemany("INSERT INTO constants VALUES (?, ?)",
                       [(n, v) for names in CONSTANTS.values() for n, v in names.items()])


class Prices(NamedTuple):
    """Momentum as schema.sql fits it: a slope per horizon, the share of a
    trend made after each update, each player's trend in %, the horizon."""
    slope: dict[int, float]
    carry: tuple[float, ...]
    trend: dict[str, float]
    horizon: int


def price_momentum() -> Prices:
    """Fitted at the model's cut and kept (momentum_fit), then read."""
    with closing(connect()) as db, db:
        db.execute("DELETE FROM momentum_fit WHERE as_of = cut()")
        db.execute("INSERT INTO momentum_fit SELECT cut(), h, slope FROM momentum")
    slope: dict[int, float] = dict(rows("SELECT h, slope FROM fitted ORDER BY h"))
    return Prices(slope, tuple(v for (v,) in rows("SELECT carry FROM price_carry ORDER BY h")),
                  dict(rows("SELECT ff_id, trend FROM price_trend")), max(slope, default=1))


def price_change(day: str, h: int) -> dict[str, float]:
    """Each player's change in % over the h updates after his last one on or
    before day, all time (0 where there are not h more): what a replay
    looks back on."""
    return dict(named("price_change", {"day": day, "h": h})[1])


def premium() -> float:
    """The bid over asking to win an auction: rivals' winning bids are mostly
    the boldest bidder's, 1.19x against your 1.00x (2026-10-04)."""
    got: float = rows("SELECT premium FROM premium")[0][0]
    return got


def market_offer_ratios() -> list[float]:
    """The game's nightly offers over value, each once, by when made."""
    return [r for (r,) in rows("SELECT ratio FROM offer_ratios")]


def params(cal: Calibration, avail: Availability, promoted: float, ratings: Ratings,
           market: Market) -> list[dict[str, Any]]:
    """Every constant and fitted value a run is built with, one row each:
    (component, term, value, kind, n)."""
    def row(component: str, term: str, value: float, kind: str = "fitted",
            n: int | None = None) -> dict[str, Any]:
        return {"component": component, "term": term, "value": float(value),
                "kind": kind, "n": n}
    ratios = list(market.offer_ratios)
    return ([row(m, name, v, "constant") for m, names in CONSTANTS.items()
             for name, v in names.items()]
            + [row("startprob.calibration", t, getattr(cal, t))
               for t in ("alpha", "beta", "neutral_start", "absent_start")]
            + [row("startprob.availability", b, v) for b, v in sorted(avail.level.items())]
            + [row("score.promoted_discount", "", promoted),
               row("pricing.premium", "", market.premium)]
            + [row("pricing.offer_ratios", t, percentile(ratios, p), n=len(ratios))
               for t, p in (("p10", 10), ("median", 50), ("p90", 90)) if ratios]
            + [row("pricing.carry", str(h), v) for h, v in enumerate(market.carry, 1)]
            + [row("fixture.difficulty", c, v) for c, v in sorted(ratings.diff.items())]
            + [row("fixture.%s" % t, c, ad[i]) for c, ad in sorted(ratings.ad.items())
               for i, t in enumerate(("scored", "conceded"))])


class Built(NamedTuple):
    universe: Universe
    params: list[dict[str, Any]]


def universe() -> Universe:
    return built().universe


@cache
def built() -> Built:
    """The universe, and the parameters it was built with."""
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

    teams, mkt = ([dict(r, key=r.get("player"))
                   for r in current(name)] for name in ("api_teams", "api_market"))
    price, route = market_routes(mkt)
    pt_to_key = {r["player_team_id"]: r["key"] for r in teams
                 if r["key"] and r.get("player_team_id")}

    value = {k: rec["value"] for k, rec in players.items() if rec.get("value")}
    offers = current("api_offers")
    received_offers = pending(
        [dict(r, key=pt_to_key.get(r.get("player_team_id") or ""))
         for r in offers], "status", "money")
    pos = {k: position((rec.get("pos") or "").upper())
           for k, rec in players.items()}
    squads = {mgr: {k: pos[k] for k in lg.squad(mgr) if k in pos}
              for mgr in lg.managers}
    ratings = fixture_ratings(mkt_rows)
    ex = expectations(sc, ratings, set(price).union(*squads.values()), m)
    per_j, first_jornada_of, rem, played = ex.per_j, ex.first_of, ex.rem, ex.played
    write_constants()
    prices = price_momentum()
    market = Market(
        cash=lg.cash[me], premium=premium(),
        name={k: Name(rec.get("name") or k) for k, rec in players.items()},
        pos=pos,
        price={k: v for k, v in price.items() if k in players},
        route={k: v for k, v in route.items() if k in players},
        owner={k: v for k, v in lg.owner.items() if k in players},
        paid={k: v for k, v in lg.paid.items() if k in players},
        offer={k: v for k, v in received_offers.items() if k in lg.squad(me)},
        offer_ratios=tuple(market_offer_ratios()),
        nights_left=_nights_left(offers),
        value={k: v for k, v in value.items() if k in players},
        my_bid=pending(mkt, "bid_status", "bid_money"),
        trend=prices.trend, carry=prices.carry,
        clause={k: v for k, v in open_clauses(teams).items() if k in players})
    squads, per_j = phantom_fill(squads, per_j, pos)
    assert all(_fieldable(sq) for sq in squads.values()), squads
    if rem:
        first_jornada_of.update({k: rem[0] for layer in per_j.values()
                                 for k in layer if k.startswith("__phantom_")})

    fc = Bootstrap(per_j, pool=[s.pts for s in scored() if s.games == 1])

    carried = {r["manager"]: r.get("team_points") or 0.0
               for r in lg.standings if r.get("manager")}
    return Built(Universe(
        state=LeagueState(squads, rem, me, carried), forecaster=fc,
        market=market,
        rival_cash={h: v for h, v in lg.cash.items() if h != me},
        part_played=played, first_jornada_of=first_jornada_of, factors=ex.factors),
        params(sc.starts.cal, sc.starts.avail, sc.promoted_discount, ratings, market))


def _selftest() -> None:
    from ffcore.startprob import Availability, Calibration

    rows = params(Calibration(0.1, 0.9, 55.0, 12.0), Availability({"indefinite": 0.2}), 0.7,
                  Ratings({"a": 1.1}, {"a": (1.2, 0.8)}),
                  Market(premium=1.05, offer_ratios=(0.9, 1.0, 1.1), carry=(0.5, 1.0)))
    got = {(r["component"], r["term"]): r for r in rows}
    assert len(got) == len(rows), "one row a parameter"
    assert {r["kind"] for r in rows} == {"fitted", "constant"}
    for key, value in [(("startprob.calibration", "alpha"), 0.1),
                       (("startprob.calibration", "absent_start"), 12.0),
                       (("startprob.availability", "indefinite"), 0.2),
                       (("startprob.availability", "doubt_for:at"), 0.5),
                       (("score.promoted_discount", ""), 0.7),
                       (("pricing.premium", ""), 1.05),
                       (("pricing.offer_ratios", "median"), 1.0),
                       (("pricing.carry", "2"), 1.0),
                       (("fixture.difficulty", "a"), 1.1),
                       (("fixture.scored", "a"), 1.2),
                       (("fixture.conceded", "a"), 0.8),
                       (("decide", "CONFIDENCE"), CONFIDENCE)]:
        assert got[key]["value"] == value, (key, got.get(key))
    assert got[("pricing.offer_ratios", "median")]["n"] == 3
    assert got[("decide", "CONFIDENCE")]["kind"] == "constant"
    assert got[("startprob.availability", "doubt_for:at")]["kind"] == "fitted", \
        "a bucket with no data keeps its prior, and is still what the run used"
    _selftest_market()
    print("assemble self-test OK")


def _selftest_market() -> None:
    """The market's SQL (schema.sql: price_obs, auction_ratios,
    offer_ratios, momentum, premium; datasette.json: price_forecast_error)
    as assemble reads it."""
    import json
    import tempfile
    from pathlib import Path

    from ffcore.db import connect
    from ffcore.tidy import store_sql, tables_in

    days = ["2026-08-%02d" % d for d in range(10, 13)]
    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        for n, d in enumerate(days):
            store_sql("market", [{"observed_at": d + "T2359Z", "ff_id": k, "value": v}
                                 for k, v in (("up", str(100 * 1.05 ** n)), ("flat", "100"),
                                              ("once", "50" if n == 0 else ""))])
        pts = [(k, at[:10], step) for k, at, step in rows(
            "SELECT ff_id, observed_at, step FROM price_obs ORDER BY first_at, first_id, observed_at")]
        assert {k for k, _d, _s in pts} == {"up", "flat", "once"} and \
            [s for k, _d, s in pts if k == "once"] == [None], \
            "a player seen once has no change, and still counts"
        ups = [(d, s) for k, d, s in pts if k == "up" and s is not None]
        assert ups[0][0] == "2026-08-11" and abs(ups[0][1] - 5.0) < 1e-9, ups
        assert [(d, s) for k, d, s in pts if k == "flat"] == [
            ("2026-08-10", None), ("2026-08-11", 0.0), ("2026-08-12", 0.0)]

        close, a = "2026-09-02T22:24:00+02:00", "2026-09-02T1000Z"
        store_sql("api_market", [
            {"observed_at": a, "seller": "marketPlayerLeague", "player_id": "7",
             "expires_at": close, "sale_price": "10000000"},
            {"observed_at": a, "seller": "marketPlayerTeam", "player_id": "8",
             "expires_at": close, "sale_price": "10000000"}])
        store_sql("api_standings", [{"observed_at": a, "user_id": "1", "manager": "me"},
                                    {"observed_at": a, "user_id": "2", "manager": "riv"}])
        store_sql("api_activity", [
            {"observed_at": a, "activity_id": i, "kind": "buy", "user_id": u, "player_id": p,
             "at": at, "amount": amt, "counterparty": ""}
            for i, u, p, at, amt in [("1", "1", "7", "2026-09-02T22:24:10+02:00", "10400000"),
                                     ("2", "1", "8", "2026-09-02T22:24:10+02:00", "99"),
                                     ("3", "1", "7", "2026-09-05T10:00:00+02:00", "1"),
                                     ("4", "2", "7", "2026-09-02T22:24:20+02:00", "12000000")]])
        with closing(connect()) as db, db:
            db.execute("INSERT INTO league_config VALUES ('me', 100.0)")
        assert [r for (r,) in rows("SELECT ratio FROM auction_ratios ORDER BY n")] == [1.04], \
            "your buys at the close of a league listing, over its price; not a rival's"

        store_sql("api_teams", [{"observed_at": a, "player_team_id": "9", "market_value": "10",
                                 "manager": "", "player_id": ""}])
        offer = {"observed_at": a, "player_team_id": "9", "from_market": "true",
                 "money": "11", "offer_id": "o", "created_at": "c"}
        store_sql("api_offers", [offer, offer, dict(offer, from_market="false", money="12",
                                                    offer_id="m")])
        assert market_offer_ratios() == [1.1], "the game's offers, each once, over value"

    def prices(series: dict[str, list[float]]) -> Prices:
        """The momentum views over these values, one a day from 2026-08-10."""
        with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
            write_constants()
            for n in range(max(len(v) for v in series.values())):
                store_sql("market", [{"observed_at": "2026-08-%02dT2359Z" % (10 + n), "ff_id": k,
                                      "value": str(v[n])} for k, v in series.items() if n < len(v)])
            return price_momentum()

    by = {k + str(i): [100 * g ** n for n in range(20)]
          for k, g in (("up", 1.05), ("down", 0.98), ("flat", 1.0)) for i in range(12)}
    mo = prices(by)
    assert set(mo.slope) == {1, 2, 3, 4}, "a quarter of the median series: 19 changes -> 4"
    up = prices({k: v for k, v in by.items() if k.startswith("up")})
    assert abs(up.slope[3] * 5.0 - (1.05 ** 3 - 1) * 100) < 1e-6, up.slope
    assert mo.horizon == 4
    assert mo.carry[-1] == 1.0 and 0 < mo.carry[0] < mo.carry[1], mo.carry
    assert mo.trend["up0"] > 0 > mo.trend["down0"] and mo.trend["flat0"] == 0.0, mo.trend
    assert prices({"once": [100.0]}).trend == {}
    rise = prices({"r%d" % i: [100 * 1.15 ** n for n in range(13)] for i in range(3)})
    assert abs(rise.slope[2] * 15.0 - (1.15 ** 2 - 1) * 100) < 1e-6, \
        "a rise over several updates is not clipped as if it were one"
    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        write_constants()
        for n in range(20):
            store_sql("market", [{"observed_at": "2026-08-%02dT2359Z" % (10 + n), "ff_id": k,
                                  "value": str(v[n])} for k, v in by.items()])
        meta = Path(__file__).resolve().parent.parent / "datasette.json"
        with closing(connect()) as db:
            graded = {h: (n, mae, zero) for h, n, mae, zero in db.execute(
                json.loads(meta.read_text())["databases"]["lfg"]["queries"]
                ["price_forecast_error"]["sql"])}
        assert graded[1][1] < graded[1][2] and graded[3][0] > 0, graded

    for ratios, want in [([1.0] * 5 + [1.3] * 5, 1.3), ([1.0] * 9 + [1.3], 1.0), ([], 1.0)]:
        with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
            write_constants()
            at = "2026-09-0%dT1000Z"
            store_sql("api_standings", [{"observed_at": at % 1, "user_id": "1", "manager": "me"}])
            store_sql("api_market", [
                {"observed_at": at % 1, "seller": "marketPlayerLeague", "player_id": str(i),
                 "expires_at": "2026-09-02T22:%02d:00+02:00" % i, "sale_price": "100"}
                for i in range(len(ratios))] or [{"observed_at": at % 1, "seller": "", "player_id": "",
                                                  "expires_at": "", "sale_price": ""}])
            store_sql("api_activity", [
                {"observed_at": at % 1, "activity_id": str(i), "kind": "buy", "user_id": "1",
                 "player_id": str(i), "at": "2026-09-02T22:%02d:00+02:00" % i,
                 "amount": str(100 * r), "counterparty": ""} for i, r in enumerate(ratios)]
                or [{"observed_at": at % 1, "activity_id": "x", "kind": "", "user_id": "",
                     "player_id": "", "at": "", "counterparty": ""}])
            with closing(connect()) as db, db:
                db.execute("INSERT INTO league_config VALUES ('me', 100.0)")
            assert abs(premium() - want) < 1e-12, (ratios, premium())


if __name__ == "__main__":
    _selftest()
