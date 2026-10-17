"""Where the data meets the model: the one place that reads the tables and
builds what the decision works on — the scorer, the market, the league
state and the forecast — so the model modules never read a table
themselves."""
from __future__ import annotations

import sys
from contextlib import closing, contextmanager
from functools import cache
from collections.abc import Iterator, Sequence
from typing import Any, NamedTuple

import decide
import remind
from decide import CONFIDENCE, Universe, _fieldable
from ffcore import fixture, forecast, pricing, rules, score, startprob
from ffcore.clock import set_variant
from ffcore import db as lfgdb, kept
from ffcore.db import connect, named, query, record, rows
from ffcore.fixture import Ratings, difficulty_ratings
from ffcore.forecast import Bootstrap
from ffcore.jornadas import rounds_ahead
from ffcore.league import League
from ffcore.market import Market
from ffcore.names import Name
from ffcore.parse import Rows
from ffcore.points import scored, season_record
from ffcore.schedule import PerJornada, expectations, phantom_fill
from ffcore.score import fit_promoted_discount, Scorer
from ffcore.season import LeagueState
from ffcore.startprob import Availability, Calibration, StartOdds, fit_availability
from ffcore.tidy import LINEUP_SOURCE, age_hours, current
from stats import percentile

__all__ = ["applied", "constants", "universe", "built", "forecast_cells", "params", "scorer", "fixture_ratings", "price_momentum",
           "price_change", "premium", "write_constants", "record_trend", "market_offer_ratios",
           "APP_FRESH_HOURS"]

APP_FRESH_HOURS = 14.4

# The constants a run is built with, by module: what params records as such,
# and what a variant may set (variants).
NAMES: dict[str, tuple[str, ...]] = {
    "assemble": ("APP_FRESH_HOURS",),
    "decide": ("CONFIDENCE",),
    "fixture": ("FIX_BAND", "MIN_AD_MATCHES"),
    "forecast": ("MIN_POOL", "PERSISTENT_SHARE"),
    "pricing": ("BID_BEATS", "CLIP", "PRICE_W0", "PRICE_W1", "PRICE_W2", "PRICE_WINDOW"),
    "remind": ("REMIND_HOURS",),
    "rules": ("MATCH_LEN",),
    "score": ("PPM_BANDS", "PROMOTED_DISCOUNT_K", "SHRINK_K"),
    "startprob": ("ABSENT_START", "AVAIL_K", "FALLBACK_K", "NEUTRAL_START", "PICK_K"),
}
MODULES: dict[str, Any] = {"assemble": sys.modules[__name__], "decide": decide, "fixture": fixture,
                           "forecast": forecast, "pricing": pricing, "remind": remind,
                           "rules": rules, "score": score, "startprob": startprob}


def constants() -> dict[str, dict[str, float]]:
    """The constants in force now, by module (a variant applies its own)."""
    return {m: {n: getattr(MODULES[m], n) for n in names} for m, names in NAMES.items()}


@contextmanager
def applied(variant: str) -> Iterator[None]:
    """The model as a variant sets it (variants): its constants on the
    modules that own them, the clock's variant for the views, all put back on
    leaving. A variant with no rows (but live) or a name that is no constant
    raises."""
    owner = {n: m for m, names in NAMES.items() for n in names}
    over = rows("SELECT name, value FROM variants WHERE variant = ? ORDER BY name", (variant,))
    if not over and variant != "live":
        raise ValueError("no variant %s (grading.py variant %s CONSTANT=VALUE)" % (variant, variant))
    unknown = [n for n, _v in over if n not in owner]
    if unknown:
        raise ValueError("variant %s sets %s, which no module has as a constant" % (variant, unknown))
    was = {n: getattr(MODULES[owner[n]], n) for n, _v in over}
    try:
        for n, v in over:
            setattr(MODULES[owner[n]], n, v)
        set_variant(variant)
        yield
    finally:
        for n, v in was.items():
            setattr(MODULES[owner[n]], n, v)
        set_variant("live")


def scorer(market: Rows, xi_rows: Rows) -> Scorer:
    """The scorer as of cut(), its fits read from the start and points views
    (written constants first: the views read them)."""
    write_constants()
    last_season = {k: {"pts": pts, "pj": pj} for k, pts, pj in rows("SELECT ff_id, pts, pj FROM last_season")}
    priors: dict[str, float] = dict(rows("SELECT player, prior FROM ppm_prior"))
    season = season_record()
    cal, = rows("SELECT alpha, beta, neutral_start, absent_start FROM start_calibration")
    return Scorer(
        market, StartOdds(xi_rows, Calibration(*cal),
                          fit_availability(rows("SELECT * FROM availability_tally")),
                          {k: (apps, squads) for k, (_pts, apps, squads) in season.items()},
                          dict(rows("SELECT player, pct FROM last_fit")), app_status(),
                          dict(rows("SELECT player, p FROM pick_later"))),
        last_season,
        current={k: {"pts": pts, "pj": apps} for k, (pts, apps, _n) in season.items()},
        promoted_discount=fit_promoted_discount(market, last_season, scored(), priors),
        priors=priors)


def app_status() -> dict[str, str]:
    """The LaLiga app's status for each player it lists."""
    return {k: r["player_status"] for r in current("api_players_all")
            if (k := r.get("player")) and r.get("player_status")}


def fixture_ratings(market: Rows) -> Ratings:
    return difficulty_ratings(market, current("results_history"))


def write_constants() -> None:
    """constants() into lfg.db under the variant in force, for the views."""
    with closing(connect()) as db, db:
        db.execute("DELETE FROM model_constants WHERE variant = variant()")
        db.executemany("INSERT INTO model_constants VALUES (variant(), ?, ?)",
                       [(n, v) for names in constants().values() for n, v in names.items()])


class Prices(NamedTuple):
    """Momentum as schema.sql fits it: a slope per horizon, the share of a
    trend made after each update, each player's trend in %, the horizon."""
    slope: dict[int, float]
    carry: tuple[float, ...]
    trend: dict[str, float]
    horizon: int


def record_trend() -> None:
    """Each player's price trend as of the model's cut, kept (price_trend_fit)
    for the views that read it; the momentum it rests on kept first."""
    with closing(connect()) as db, db:
        db.execute("DELETE FROM price_trend_fit WHERE as_of = cut()")
        db.execute("INSERT INTO price_trend_fit SELECT cut(), ff_id, trend FROM price_trend_now")


def fits_in_place() -> bool:
    """Do both fit tables hold the cut's rows (read past the kept answers:
    the file they were kept for may be another copy of the data)."""
    with closing(connect()) as db:
        return bool(db.execute(
            "SELECT (SELECT count(*) FROM momentum_fit WHERE as_of = cut()) > 0"
            " AND (SELECT count(*) FROM price_trend_fit WHERE as_of = cut()) > 0").fetchone()[0])


def price_momentum() -> Prices:
    """Fitted at the model's cut and kept (momentum_fit, price_trend_fit),
    then read."""
    if not (kept.written(lfgdb.DB, "fits") and fits_in_place()):
        with closing(connect()) as db, db:
            db.execute("DELETE FROM momentum_fit WHERE as_of = cut()")
            db.execute("INSERT INTO momentum_fit SELECT cut(), h, slope FROM momentum")
        record_trend()
        kept.mark(lfgdb.DB, "fits")
    slope: dict[int, float] = dict(rows("SELECT h, slope FROM fitted ORDER BY h"))
    return Prices(slope, tuple(v for (v,) in rows("SELECT carry FROM price_carry ORDER BY h")),
                  dict(rows("SELECT ff_id, trend FROM price_trend")), max(slope, default=1))


def price_change(day: str, h: int) -> dict[str, float]:
    """Each player's change in % over the h updates after his last one on or
    before day, all time (0 where there are not h more): what a replay
    looks back on."""
    return dict(named("price_change", {"day": day, "h": h})[1])


def market_facts() -> dict[str, dict[str, Any]]:
    """player_facts as of cut(), by player."""
    cols, got = query("SELECT * FROM player_facts")
    return {r[0]: dict(zip(cols[1:], r[1:])) for r in got}


def premium() -> float:
    """The bid over asking to win an auction: rivals' winning bids are mostly
    the boldest bidder's, 1.19x against your 1.00x (2026-10-04)."""
    got: float = rows("SELECT premium FROM premium")[0][0]
    return got


def market_offer_ratios() -> list[float]:
    """The game's nightly offers over value, each once, by when made."""
    return [r for (r,) in rows("SELECT ratio FROM offer_ratios")]


def params(cal: Calibration, avail: Availability, promoted: float, ratings: Ratings,
           premium: float, ratios: Sequence[float], carry: Sequence[float]) -> list[dict[str, Any]]:
    """Every constant and fitted value a run is built with, one row each:
    (component, term, value, kind, n)."""
    def row(component: str, term: str, value: float, kind: str = "fitted",
            n: int | None = None) -> dict[str, Any]:
        return {"component": component, "term": term, "value": float(value),
                "kind": kind, "n": n}
    return ([row(m, name, v, "constant") for m, names in constants().items()
             for name, v in names.items()]
            + [row("startprob.calibration", t, getattr(cal, t))
               for t in ("alpha", "beta", "neutral_start", "absent_start")]
            + [row("startprob.availability", b, v) for b, v in sorted(avail.level.items())]
            + [row("score.promoted_discount", "", promoted),
               row("pricing.premium", "", premium)]
            + [row("pricing.offer_ratios", t, percentile(ratios, p), n=len(ratios))
               for t, p in (("p10", 10), ("median", 50), ("p90", 90)) if ratios]
            + [row("pricing.carry", str(h), v) for h, v in enumerate(carry, 1)]
            + [row("fixture.difficulty", c, v) for c, v in sorted(ratings.diff.items())]
            + [row("fixture.%s" % t, c, ad[i]) for c, ad in sorted(ratings.ad.items())
               for i, t in enumerate(("scored", "conceded"))])


class Built(NamedTuple):
    universe: Universe
    params: list[dict[str, Any]]


def forecast_cells() -> tuple[PerJornada, set[str]]:
    """Every player's forecast cell by jornada as of cut(), and who is listed
    injured, doubtful or suspended then; a past cut's answers kept (kept.py)."""
    with kept.scope(lfgdb.DB, lfgdb.TABLES_SQL, repr(constants())):
        market = current("market")
        sc = scorer(market, current("lineups", LINEUP_SOURCE))
        per_j = expectations(sc, fixture_ratings(market), set(sc.lookup),
                             current("matches"), rounds_ahead()).per_j
        return per_j, {k for k in sc.lookup if sc.starts.status_of(k)}


def universe() -> Universe:
    return built().universe


@cache
def built() -> Built:
    """The universe, and the parameters it was built with."""
    with kept.scope(lfgdb.DB, lfgdb.TABLES_SQL, repr(constants())):
        return _built()


def _built() -> Built:
    age = age_hours("api_teams")
    if age is None or age > APP_FRESH_HOURS:
        raise SystemExit("the app's squads are %s old; no board is built from "
                         "them" % ("unknown" if age is None else "%.0fh" % age))
    lg = League.load()
    mkt_rows = current("market")
    sc = scorer(mkt_rows, current("lineups", LINEUP_SOURCE))
    me = lg.me
    m = current("matches")
    prices = price_momentum()          # first: it keeps the trend that market_facts and sale_odds read
    facts = market_facts()

    def column(c: str) -> dict[str, Any]:
        return {k: f[c] for k, f in facts.items() if f[c] is not None}
    pos = column("pos")
    price = column("price")
    squads = {mgr: {k: pos[k] for k in lg.squad(mgr) if k in pos}
              for mgr in lg.managers}
    ratings = fixture_ratings(mkt_rows)
    ex = expectations(sc, ratings, set(price).union(*squads.values()), m, rounds_ahead())
    per_j, first_jornada_of, rem, played = ex.per_j, ex.first_of, ex.rem, ex.played
    market = Market(
        cash=lg.cash[me], premium=premium(),
        name={k: Name(f["name"]) for k, f in facts.items()},
        pos=pos, price=price, route=column("route"), owner=column("owner"),
        paid=column("paid"), offer=column("offer"),
        value=column("value"), my_bid=column("my_bid"),
        trend=prices.trend, clause=column("clause"),
        fetch=dict(rows("SELECT player, fetches FROM sale_odds")))
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
        params(sc.starts.cal, sc.starts.avail, sc.promoted_discount, ratings, market.premium,
               market_offer_ratios(), prices.carry))


def _selftest() -> None:
    from ffcore.startprob import Availability, Calibration

    rows = params(Calibration(0.1, 0.9, 55.0, 12.0), Availability({"indefinite": 0.2}), 0.7,
                  Ratings({"a": 1.1}, {"a": (1.2, 0.8)}),
                  1.05, (0.9, 1.0, 1.1), (0.5, 1.0))
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
    _selftest_facts()
    _selftest_sale()
    _selftest_variants()
    print("assemble self-test OK")


def _selftest_variants() -> None:
    """A variant is a second set of constants, applied to the modules that
    own them and to the views, beside the live one."""
    import tempfile
    from pathlib import Path

    from ffcore import forecast, score, startprob
    from ffcore.tidy import tables_in

    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        record([{"variant": v, "name": n, "value": x} for v, n, x in (
            ("slow", "AVAIL_K", 2.0), ("slow", "SHRINK_K", 9.0), ("slow", "PERSISTENT_SHARE", 0.5),
            ("slow", "NEUTRAL_START", 33.0), ("typo", "AVIAL_K", 1.0))], "variants")
        tally = [("indefinite", 3.0, 4.0)]
        live_shrink = score.SHRINK_K
        live = startprob.fit_availability(tally).level["indefinite"]
        write_constants()
        assert rows("SELECT neutral_start FROM k") == [(60.0,)]
        with applied("slow"):
            assert constants()["startprob"]["AVAIL_K"] == 2.0 and constants()["score"]["SHRINK_K"] == 9.0
            assert startprob.fit_availability(tally).level["indefinite"] != live, \
                "the shrinkage a function defaults to follows the variant"
            assert forecast.Bootstrap({}).share == 0.5
            assert score.Scorer([], startprob.StartOdds([], startprob.Calibration(0, 1, 60, 15), startprob.Availability({}), {}, {}, {})
                                ).shrink_k == 9.0
            write_constants()
            assert rows("SELECT neutral_start FROM k") == [(33.0,)], "the views read the variant's row"
            assert rows("SELECT count(*) FROM model_constants WHERE variant = 'slow'") == [(len(
                [n for names in constants().values() for n in names]),)]
        assert startprob.AVAIL_K == 4.0 and score.SHRINK_K == live_shrink and forecast.PERSISTENT_SHARE == 0.08, \
            "restored on leaving"
        assert rows("SELECT neutral_start FROM k") == [(60.0,)], "the live rows were never touched"
        assert startprob.fit_availability(tally).level["indefinite"] == live
        try:
            with applied("typo"):
                raise AssertionError("a name that is no constant must not apply quietly")
        except ValueError as e:
            assert "AVIAL_K" in str(e)
        assert startprob.AVAIL_K == 4.0
        try:
            with applied("slwo"):
                raise AssertionError("a misspelt variant must not run as live")
        except ValueError as e:
            assert "no variant slwo" in str(e)
        with applied("live"):
            assert startprob.AVAIL_K == 4.0, "live has no rows and is the model as it stands"


def _selftest_sale() -> None:
    """schema.sql's nights_left and sale_odds: what selling a player with an
    offer standing on him gets, against the recursion they replace."""
    import tempfile
    from pathlib import Path

    from ffcore.clock import set_now
    from ffcore.db import record
    from ffcore.parse import snapshot_stamp
    from ffcore.tidy import store_sql, tables_in

    def at(n: int) -> str:
        return "2026-09-0%dT1000Z" % n

    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        try:
            set_now(snapshot_stamp("2026-09-04T1200Z"))
            record([{"me": "me", "budget": 100e6}], "league_config")
            record([{"name": "PRICE_WINDOW", "value": 3.0}, {"name": "CLIP", "value": 20.0},
                    {"name": "PRICE_W0", "value": 1.0}, {"name": "PRICE_W1", "value": 0.0},
                    {"name": "PRICE_W2", "value": 0.0}], "model_constants")
            record([{"player_id": "p1", "app_id": "1"}, {"player_id": "p2", "app_id": "2"}],
                   "crosswalk")
            store_sql("market", [{"observed_at": at(1), "ff_id": k, "name": k, "value": "10000000"}
                                 for k in ("p1", "p2")])
            store_sql("api_standings", [{"observed_at": at(1), "user_id": "1", "manager": "me"}])
            store_sql("matches", [{"observed_at": at(1), "match_id": "m", "jornada": "1",
                                   "home": "a", "away": "b", "kickoff": "2026-09-05T19:00:00+00:00"}])
            # past nightly offers, 0.8 / 0.9 / 1.0 / 1.1 of value: the last three count
            for n, money in enumerate(("8000000", "9000000", "10000000", "11000000"), 1):
                store_sql("api_teams", [{"observed_at": at(n), "player_id": "1", "manager": "me",
                                         "market_value": "10000000", "player_team_id": "t1"}])
                store_sql("api_offers", [{"observed_at": at(n), "player_team_id": "t1",
                                          "offer_id": "o%d" % n, "from_market": "true",
                                          "money": money, "status": "accepted",
                                          "created_at": "2026-09-0%dT09:00:00+02:00" % n}])
            store_sql("api_teams", [{"observed_at": at(3), "player_id": "2", "manager": "riv",
                                     "market_value": "10000000", "player_team_id": "t2"}])
            def sale(offer: str, expires: str = "2026-09-04T20:00:00+02:00"
                     ) -> dict[str, tuple[Any, ...]]:
                store_sql("api_offers", [{"observed_at": at(4), "player_team_id": "t1",
                                          "offer_id": "now", "status": "pending", "money": offer,
                                          "expires_at": expires}])
                return {r[0]: r for r in rows("SELECT player, offer, waiting, fetches FROM sale_odds")}
            assert rows("SELECT nights FROM nights_left") == [(0,)], "no offer standing: no nights"
            got = sale("10500000")
            assert rows("SELECT nights FROM nights_left") == [(2,)], \
                "the offer ends 09-04 18:00 UTC, the match locks 09-05 19:00: a day and an hour, 2 nights"
            assert set(got) == {"p1"}, "offers are only on your players"
            last = sum((0.9, 1.0, 1.1)) / 3 * 10e6     # the last 3 offers: the last night's is taken
            night_before = sum(max(x * 10e6, last) for x in (0.9, 1.0, 1.1)) / 3
            assert abs(got["p1"][2] - night_before) < 1e-6, got
            assert got["p1"][3] == max(10.5e6, night_before)

            # a rising player, momentum fitted: the offers to come are on the value he will have
            store_sql("market", [{"observed_at": at(n), "ff_id": k, "name": k, "value": v}
                                 for n, v in ((2, "10500000"), (3, "11025000")) for k in ("p1", "p2")])
            record([{"as_of": at(3), "h": 1, "slope": 2.0}, {"as_of": at(3), "h": 2, "slope": 8.0}],
                   "momentum_fit")
            assert rows("SELECT count(*) FROM price_trend") == [(0,)], "nothing kept until a run keeps it"
            record_trend()
            assert dict(rows("SELECT ff_id, trend FROM price_trend")) == dict(rows(
                "SELECT ff_id, trend FROM price_trend_now")), "kept as the view works it out"
            facts = dict(rows("SELECT ff_id, trend FROM price_trend"))
            carry = [c for (c,) in rows("SELECT carry FROM price_carry ORDER BY h")]
            assert facts["p1"] > 0 and carry == [0.25, 1.0], (facts, carry)
            value, trend, later = 11025000.0, facts["p1"], 0.0
            for night in (2, 1):
                then = value * (1 + trend / 100 * carry[min(night, len(carry)) - 1])
                later = sum(max(x * then, later) for x in (0.9, 1.0, 1.1)) / 3
            (waiting, fetches), = rows("SELECT waiting, fetches FROM sale_odds")
            assert abs(waiting - later) < 1e-6 and fetches == max(10.5e6, later), (waiting, later)
        finally:
            set_now(None)


def _selftest_facts() -> None:
    """schema.sql's player_facts, as market_facts reads it."""
    import tempfile
    from pathlib import Path

    from ffcore.clock import set_now
    from ffcore.db import record
    from ffcore.parse import snapshot_stamp
    from ffcore.tidy import store_sql, tables_in

    a = "2026-09-02T1000Z"
    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        try:
            set_now(snapshot_stamp("2026-09-03T0000Z"))
            record([{"me": "me", "budget": 100e6}], "league_config")
            record([{"player_id": k, "app_id": app, "ff_slug": slug} for k, app, slug in
                    [("p1", "1", ""), ("p2", "2", ""), ("p3", "3", ""), ("p4", "", "cai"),
                     ("p5", "5", "")]], "crosswalk")
            store_sql("market", [{"observed_at": a, "ff_id": k, "name": n, "position": pos,
                                  "value": v} for k, n, pos, v in
                                 [("p1", "ana uno", "defensa", "5000000"),
                                  ("p2", "Bo Dos", "delantero", "0"),
                                  ("p3", "Cy Tres", "portero", "3000000"),
                                  ("p5", "Di Cinco", "utillero", "1000000")]])
            store_sql("lineups", [{"observed_at": a, "source": "futbolfantasy", "team_slug": "c",
                                   "player_slug": "cai", "player_name": "Cai Cuatro"}])
            store_sql("api_standings", [{"observed_at": a, "user_id": u, "manager": m}
                                        for u, m in (("1", "me"), ("2", "riv"))])
            store_sql("api_teams", [{"observed_at": a, "player_id": app, "manager": m,
                                     "buyout": b, "player_team_id": pt} for app, m, b, pt in
                                    [("1", "me", "9000000", "t1"), ("3", "riv", "", "t3")]])
            store_sql("api_market", [{"observed_at": a, "player_id": app, "sale_price": price,
                                      "seller": seller, "bid_status": st, "bid_money": bid}
                                     for app, price, seller, st, bid in
                                     [("2", "2000000", "marketPlayerTeam", "pending", "2500000"),
                                      ("5", "1000000", "marketPlayerLeague", "", ""),
                                      ("3", "", "marketPlayerLeague", "pending", "")]])
            store_sql("api_offers", [{"observed_at": a, "player_team_id": pt, "status": st,
                                      "money": m, "offer_id": i} for pt, st, m, i in
                                     [("t1", "pending", "6000000", "o1"),
                                      ("t1", "pending", "5000000", "o2"),
                                      ("t1", "accepted", "9000000", "o3"),
                                      ("t3", "pending", "4000000", "o4")]])
            got = market_facts()
        finally:
            set_now(None)
    assert set(got) == {"p1", "p2", "p3", "p4", "p5"}, "every player the market or a line-up names"
    assert got["p1"] == {"name": "ana uno", "pos": "DEF", "value": 5e6, "owner": "me",
                         "clause": 9e6, "price": None, "route": None, "my_bid": None,
                         "offer": 6e6, "paid": None, "trend": None}, got["p1"]
    assert (got["p2"]["value"], got["p2"]["price"], got["p2"]["route"], got["p2"]["my_bid"]) \
        == (None, 2e6, "listed", 2.5e6), "a value of 0 is none; a manager's listing; your bid"
    assert (got["p5"]["route"], got["p5"]["pos"]) == ("free", "MED"), \
        "the game's own sale is free; a position the game does not field counts as MED"
    assert got["p3"]["offer"] is None and got["p3"]["clause"] is None \
        and got["p3"]["my_bid"] is None, "offers only on yours; no clause, no bid amount: none"
    assert (got["p4"]["name"], got["p4"]["pos"]) == ("Cai Cuatro", "MED"), got["p4"]


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

        from datetime import datetime, timezone

        from ffcore.clock import set_now
        try:
            set_now(datetime(2026, 8, 12, 12, tzinfo=timezone.utc))
            store_sql("market", [{"observed_at": at, "ff_id": "mid", "value": "100"}
                                 for at in ("2026-08-12T1000Z", "2026-08-12T1800Z")])
            assert [at for (at,) in rows("SELECT observed_at FROM price_obs WHERE ff_id = 'mid'")] \
                == ["2026-08-12T1000Z"], \
                "a day's value at the cut is the last snapshot up to it, not one taken after"
        finally:
            set_now(None)

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

    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        write_constants()
        for n, v in enumerate((100, 110, 110, 110)):
            store_sql("market", [{"observed_at": "2026-08-%02dT2359Z" % (10 + n), "ff_id": "x", "value": str(v)}])
        assert [round(x, 6) for (x,) in rows("SELECT x FROM price_series WHERE ff_id = 'x' ORDER BY i")] \
            == [10.0, 6.0, 2.0], \
            "a +10% step then two flat days: the signal is the last three steps, weighted .4 .4 .2 " \
            "(a missing one counts as the oldest he has: 0 + .4 * 10 + .2 * 10); a rise is felt for three updates"

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
