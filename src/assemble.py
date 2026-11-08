"""Where the data meets the model: the one place that reads the tables and
builds what the decision works on — the scorer, the market, the league
state and the forecast — so the model modules never read a table
themselves."""
from __future__ import annotations

import hashlib
import sys
from contextlib import closing
from pathlib import Path
from collections.abc import Sequence
from typing import Any, NamedTuple

from ffcore import forecast as model, pricing, rules
from ffcore.db import connect, query, record, rows, set_constants, set_look
from ffcore.forecast_store import Prices, forecast, price_change, price_momentum
from ffcore.seen import see as see_at
from ffcore.league import write_config
from ffcore.tidy import age_hours
from stats import percentile

__all__ = ["constants", "built", "forecast", "params", "price_momentum",
           "price_change", "market_offer_ratios",
           "APP_FRESH_HOURS"]

APP_FRESH_HOURS = 14.4

# The constants a run is built with, by module: what params records as such.
NAMES: dict[str, tuple[str, ...]] = {
    "assemble": ("APP_FRESH_HOURS", ),
    "forecast": ("ABSENT_START", "FIXTURE_K", "MIN_POOL", "NEUTRAL_START", "PERSISTENT_SHARE", "PICK_K", "P_DECAY", "P_LIFT", "P_LISTING", "SHRINK_K"),
    "pricing": ("BID_NEIGHBOURS", "CLIP", "PRICE_W0", "PRICE_W1", "PRICE_W2", "PRICE_WINDOW"),
    "rules": ("DEBT_SHARE", "MATCH_LEN"),
}
MODULES: dict[str, Any] = {"assemble": sys.modules[__name__], "forecast": model, "pricing": pricing,
                           "rules": rules}


def constants() -> dict[str, dict[str, float]]:
    """The constants in force now, by module (a variant applies its own)."""
    return {m: {n: getattr(MODULES[m], n) for n in names} for m, names in NAMES.items()}


def see() -> str:
    """The market and the league as of t into seen, seen_cash and seen_odds (the decision's
    snapshot, seen.see), made once a cut, variant and code: the key is the SQL's version and
    every constant, so another schema or a variant redefined makes them again, never reads a
    stale one. The cut."""
    with closing(connect()) as db:
        version, = db.execute("PRAGMA user_version").fetchone()
    return see_at(hashlib.sha1(repr((version, sorted((m, sorted(c.items())) for m, c in constants().items())))
                               .encode()).hexdigest()[:12])


def market_offer_ratios() -> list[float]:
    """The game's nightly offers over value, each once, by when made."""
    return [r for (r,) in rows("SELECT ratio FROM offer_ratios")]


def params(fits: Sequence[dict[str, Any]], ratios: Sequence[float],
           carry: Sequence[float]) -> list[dict[str, Any]]:
    """Every constant and fitted value a run is built with, one row each:
    (component, term, value, kind, n); the forecast's fits (forecast_fits)."""
    return ([_param(m, name, v, "constant") for m, names in constants().items()
             for name, v in names.items()]
            + list(fits)
            + [_param("pricing.offer_ratios", t, percentile(ratios, p), n=len(ratios))
               for t, p in (("p10", 10), ("median", 50), ("p90", 90)) if ratios]
            + [_param("pricing.carry", str(h), v) for h, v in enumerate(carry, 1)])


def forecast_fits() -> list[dict[str, Any]]:
    """What the forecast fits at t, one params row each: each club's
    attack and defence (club_rating)."""
    return [_param("forecast.%s" % t, c, v) for c, a, d in rows("SELECT club, attack, defence FROM club_rating ORDER BY club")
            for t, v in (("attack", a), ("defence", d))]


def _param(component: str, term: str, value: float, kind: str = "fitted",
           n: int | None = None) -> dict[str, Any]:
    return {"component": component, "term": term, "value": float(value), "kind": kind, "n": n}


class Built(NamedTuple):
    params: list[dict[str, Any]]
    moment: int


def built() -> Built:
    """The league at t made for a decision (look): the market and the
    league seen then (see), the forecast at t (forecast), its moment and you
    in look; and the parameters it was made with. None made from squads
    older than APP_FRESH_HOURS."""
    age = age_hours("api_teams")
    if age is None or age > APP_FRESH_HOURS:
        raise SystemExit("the app's squads are %s old; no board is built from "
                         "them" % ("unknown" if age is None else "%.0fh" % age))
    me = write_config()
    prices = price_momentum()
    see()
    moment = forecast()
    set_look(me=me, moment=moment)
    return Built(params(forecast_fits(), market_offer_ratios(), prices.carry), moment)


def _selftest() -> None:
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
    _selftest_market()
    _selftest_facts()
    _selftest_sale()
    _selftest_prior()
    _selftest_points()
    print("assemble self-test OK")



def _selftest_price_at_t(by: dict[str, list[float]]) -> None:
    """The price trend is a view at t: a past t reads the same whichever
    builds ran, and the same as a database that ended there; no kept fit."""
    import tempfile
    from datetime import datetime, timezone
    from pathlib import Path

    from ffcore.db import set_now
    from ffcore.tidy import store_sql, tables_in

    def seed(days: int) -> None:
        for n in range(days):
            store_sql("market", [{"observed_at": "2026-08-%02dT2359Z" % (10 + n), "ff_id": k,
                                  "value": str(v[n])} for k, v in by.items()])

    def look_at(day: int) -> tuple[list[Any], list[Any], list[Any]]:
        set_now(datetime(2026, 8, 10 + day, 23, 59, 30, tzinfo=timezone.utc))
        return (rows("SELECT h, slope FROM momentum ORDER BY h"), rows("SELECT ff_id, trend FROM price_trend ORDER BY 1"),
                rows("SELECT h, carry FROM price_carry ORDER BY h"))

    try:
        with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
            seed(20)
            assert rows("SELECT name FROM sqlite_master WHERE name IN ('momentum_fit', 'price_trend_fit', 'fitted')") == [], \
                "no kept fit: the trend and momentum are views at t"
            early = look_at(12)
            late = look_at(19)
            assert look_at(12) == early and early != late, "a past t does not depend on what was read since"
            assert early[1] and early[0], early
        with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
            seed(13)
            assert look_at(12) == early, "the same as a database that ended at t"
    finally:
        set_now(None)


def _selftest_prior() -> None:
    """schema.sql's last_season and ppm_prior on season_points, player_club
    and player_value as tables: in his position, the least-squares line of
    last season's points per match (10+ matches) on log value, at his value;
    a position with fewer than three, its mean; no position, everyone's
    median."""
    import math
    import sqlite3
    from ffcore.db import SCHEMA, functions

    db = sqlite3.connect(":memory:")
    functions(db)
    db.execute("CREATE TABLE season_points (season, ff_id, points, games)")
    db.execute("CREATE TABLE player_club (player, pos)")
    db.execute("CREATE TABLE player_value (player, value)")
    db.executemany("INSERT INTO season_points VALUES (?, ?, ?, ?)", [
        ("2024-25", "a", 999.0, 30.0),                        # an older season: not read
        ("2025-26", "a", 60.0, 30.0), ("2025-26", "b", 90.0, 30.0),
        ("2025-26", "c", 150.0, 30.0), ("2025-26", "d", 210.0, 30.0),
        ("2025-26", "e", 300.0, 9.0), ("2025-26", "f", 40.0, 10.0)])
    market = [("a", "DEF", 1e6), ("b", "DEF", 2e6), ("c", "DEF", 50e6), ("d", "DEF", 60e6),
              ("e", "DEF", 70e6), ("f", "POR", 3e6), ("x", "", 5e6), ("z", "DEF", None)]
    db.executemany("INSERT INTO player_club VALUES (?, ?)", [(k, p) for k, p, _v in market])
    db.executemany("INSERT INTO player_value VALUES (?, ?)", [(k, v) for k, _p, v in market if v])
    sql = SCHEMA.read_text()
    for view in ("last_season", "ppm_prior"):
        i = sql.index("CREATE VIEW %s AS" % view)
        db.execute(sql[i:sql.index(";\n", i)])
    got = dict(db.execute("SELECT player, prior FROM ppm_prior"))
    xs, ys = [math.log(v) for v in (1e6, 2e6, 50e6, 60e6)], [2.0, 3.0, 5.0, 7.0]
    mx, my = sum(xs) / 4, sum(ys) / 4
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
    line = {k: my + slope * (math.log(v) - mx) for k, v in (("a", 1e6), ("d", 60e6), ("e", 70e6))}
    assert all(abs(got[k] - line[k]) < 1e-9 for k in line), \
        ("DEF: the line on log value through a-d (e's 9 matches are too few), at each one's value", got, line)
    assert got["f"] == 4.0, "POR: one player, his mean"
    assert got["x"] == 4.0, "no position: the median of everyone's (2, 3, 4, 5, 7)"
    assert got["z"] == my, "no value: his position's mean"


def _selftest_points() -> None:
    """schema.sql's scored and season_record: each change in a player's
    season total by the jornada of his club's last match by then, and his
    season so far (points, appearances, squads, pick)."""
    import tempfile

    from ffcore.db import set_now
    from ffcore.parse import snapshot_stamp
    from ffcore.tidy import store_sql, tables_in

    a, b, later = "2026-08-01T0900Z", "2026-08-02T0900Z", "2026-08-03T0900Z"
    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        try:
            set_now(snapshot_stamp("2026-08-04T0000Z"))
            store_sql("market", [
                {"observed_at": a, "ff_id": "1", "club": "x"},
                {"observed_at": a, "ff_id": "2", "club": "y"},
                {"observed_at": a, "ff_id": "3", "club": "w"}, {"observed_at": a, "ff_id": "4", "club": "w"}])
            store_sql("matches", [
                {"observed_at": a, "match_id": "m1", "jornada": "1", "home": "x",
                 "away": "y", "score": "", "kickoff": "2026-08-01T10:00:00+00:00"},
                {"observed_at": later, "match_id": "m2", "jornada": "2",
                 "home": "x", "away": "z", "score": "1-0", "kickoff": ""},
                {"observed_at": a, "match_id": "m3", "jornada": "1", "home": "w",
                 "away": "v", "score": "", "kickoff": "2026-08-01T10:00:00+00:00"}])
            store_sql("points", [
                {"observed_at": a, "season": "2025-26", "ff_id": "1",
                 "points": "90", "games": "30"},
                *({"observed_at": a, "season": "2026-27", "ff_id": k,
                   "points": "0", "games": "0"} for k in "12"),
                # the first fetch of the season is the season so far: one game is that game's (3 played jornada 1),
                # more is no one jornada's (4 started the tool mid-season)
                {"observed_at": a, "season": "2026-27", "ff_id": "3", "points": "4", "games": "1"},
                {"observed_at": a, "season": "2026-27", "ff_id": "4", "points": "60", "games": "20"},
                {"observed_at": b, "season": "2026-27", "ff_id": "1",
                 "points": "5", "games": "1"},
                {"observed_at": later, "season": "2026-27", "ff_id": "1",
                 "points": "12", "games": "2"},
                {"observed_at": later, "season": "2026-27", "ff_id": "2",
                 "points": "3", "games": "1"}])
            assert set(rows("SELECT player, jornada, pts, at FROM scored")) == {
                ("1", 1, 5.0, b), ("1", 2, 7.0, later), ("2", 1, 3.0, later), ("3", 1, 4.0, a)}
            record([{"player_id": k, "ff_slug": slug} for k, slug in
                    [("1", "uno"), ("2", "dos"), ("3", "tres")]], "crosswalk")
            store_sql("starters", [
                {"observed_at": a, "match_id": "m1", "team_slug": "x", "player_slug": p,
                 "player_name": n, "role": r, "minute": m}
                for p, n, r, m in [("uno", "1", "starter", ""), ("dos", "2", "sub", "70"),
                                   ("tres", "3", "sub", ""), ("uno-coach", "1", "coach", "")]] + [
                {"observed_at": later, "match_id": "m2", "team_slug": "x", "player_slug": "uno",
                 "player_name": "1", "role": "starter", "minute": "45"}])
            assert set(rows("SELECT player, jornada FROM appearances")) == {("1", 1), ("1", 2), ("2", 1)}, \
                "started or came on, not an unused sub"
            got = {k: r for k, *r in rows("SELECT player, pts, apps, squads, pick FROM season_record")}
            assert {k: tuple(r[:3]) for k, r in got.items()} == {"1": (12.0, 2.0, 2.0), "2": (3.0, 1.0, 1.0), "3": (4.0, 0.0, 1.0)}, \
                "a starter the match unless he came off, a sub from when he came on"
            assert abs(got["1"][3] - (model.PICK_K * model.NEUTRAL_START / 100 + 2) / (model.PICK_K + 2)) < 1e-12, \
                "his record pulled toward NEUTRAL_START, worth PICK_K matches"
            # a listed start %, unflagged, is the chance he is picked as it says, whatever his record (he has started both)
            store_sql("lineups", [{"observed_at": later, "source": "futbolfantasy", "team_slug": "x", "player_slug": "uno",
                                   "player_name": "1", "start_pct": "10", "status": "ok", "note": ""}])
            assert dict(rows("SELECT player, picked FROM forecast_rate")).get("1") == 0.10, \
                "the listing's start %, not pulled toward his record of selection"
        finally:
            set_now(None)


def _selftest_sale() -> None:
    """schema.sql's nights_left and sale_odds: what selling a player with an
    offer standing on him gets, against the recursion they replace."""
    import tempfile
    from pathlib import Path

    from ffcore.db import set_now
    from ffcore.db import record
    from ffcore.parse import snapshot_stamp
    from ffcore.tidy import store_sql, tables_in

    def at(n: int) -> str:
        return "2026-09-0%dT1000Z" % n

    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)), set_constants(
            PRICE_WINDOW=3.0, CLIP=20.0, PRICE_W0=1.0, PRICE_W1=0.0, PRICE_W2=0.0):
        try:
            set_now(snapshot_stamp("2026-09-04T1200Z"))
            record([{"me": "me", "budget": 100e6}], "league_config")
            record([{"player_id": "p1", "app_id": "1"}, {"player_id": "p2", "app_id": "2"}],
                   "crosswalk")
            store_sql("market", [{"observed_at": at(1), "ff_id": k, "name": k, "value": "10000000"}
                                 for k in ("p1", "p2", "p3")])
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
                                 for n, v in ((2, "10500000"), (3, "11025000")) for k in ("p1", "p2", "p3")])
            facts = dict(rows("SELECT ff_id, trend FROM price_trend"))
            carry = [c for (c,) in rows("SELECT carry FROM price_carry ORDER BY h")]
            assert facts["p1"] > 0 and carry == [1.0], (facts, carry)
            value, trend, later = 11025000.0, facts["p1"], 0.0
            for night in (2, 1):
                then = value * (1 + trend / 100 * carry[min(night, len(carry)) - 1])
                later = sum(max(x * then, later) for x in (0.9, 1.0, 1.1)) / 3
            (waiting, fetches), = rows("SELECT waiting, fetches FROM sale_odds")
            assert abs(waiting - later) < 1e-6 and fetches == max(10.5e6, later), (waiting, later)

            assert [k for k, _kind in rows("SELECT player, kind FROM waiting")] == ["p1"], \
                "waiting is a sale's: yours, with an offer standing"
            assert [r[0] for r in rows("SELECT player FROM sale_odds")] == ["p1"]
        finally:
            set_now(None)


def _selftest_facts() -> None:
    """schema.sql's player_facts, as seen at a cut (see)."""
    import tempfile
    from pathlib import Path

    from ffcore.db import set_now
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
                     ("p5", "5", ""), ("p6", "6", ""), ("p7", "7", "")]], "crosswalk")
            store_sql("market", [{"observed_at": a, "ff_id": k, "name": n, "position": pos,
                                  "value": v} for k, n, pos, v in
                                 [("p1", "ana uno", "defensa", "5000000"),
                                  ("p2", "Bo Dos", "delantero", "0"),
                                  ("p3", "Cy Tres", "portero", "3000000"),
                                  ("p5", "Di Cinco", "utillero", "1000000"),
                                  ("p6", "Ed Seis", "entrenador", "600000"),
                                  ("p7", "Fa Siete", "defensa", "590000")]])
            store_sql("lineups", [{"observed_at": a, "source": "futbolfantasy", "team_slug": "c",
                                   "player_slug": "cai", "player_name": "Cai Cuatro"}])
            store_sql("api_standings", [{"observed_at": a, "user_id": u, "manager": m}
                                        for u, m in (("1", "me"), ("2", "riv"))])
            store_sql("api_teams", [{"observed_at": a, "player_id": app, "manager": m,
                                     "buyout": b, "player_team_id": pt} for app, m, b, pt in
                                    [("1", "me", "9000000", "t1"), ("3", "riv", "", "t3")]])
            store_sql("api_market", [{"observed_at": a, "player_id": app, "sale_price": price,
                                      "seller": seller, "bid_status": st, "bid_money": bid,
                                      "market_value": mv}
                                     for app, price, seller, st, bid, mv in
                                     [("2", "2000000", "marketPlayerTeam", "pending", "2500000", ""),
                                      ("5", "1000000", "marketPlayerLeague", "", "", ""),
                                      ("3", "", "marketPlayerLeague", "pending", "", ""),
                                      ("6", "615000", "marketPlayerLeague", "", "", "614800"),
                                      ("7", "589371", "marketPlayerLeague", "", "", "589995")]])
            store_sql("api_offers", [{"observed_at": a, "player_team_id": pt, "status": st,
                                      "money": m, "offer_id": i} for pt, st, m, i in
                                     [("t1", "pending", "6000000", "o1"),
                                      ("t1", "pending", "5000000", "o2"),
                                      ("t1", "accepted", "9000000", "o3"),
                                      ("t3", "pending", "4000000", "o4")]])
            c = see()
            cols, seen = query("SELECT player, name, pos, owner, value, trend, paid, offer, clause, price, route,"
                               " my_bid, fetches FROM seen WHERE cut = ?", (c, ))
            got = {r[0]: dict(zip(cols[1:], r[1:])) for r in seen}
            with closing(connect()) as db, db:
                db.execute("UPDATE seen SET value = -1 WHERE player = 'p1'")
            see()
            assert rows("SELECT value FROM seen WHERE player = 'p1'") == [(-1, )], \
                "made once a cut and code: read, not made again"
            from unittest import mock
            with mock.patch(__name__ + ".constants", lambda: {"x": {"Y": 1.0}}):
                see()
            assert rows("SELECT value FROM seen WHERE player = 'p1'") == [(5e6, )], "other constants make it again"
        finally:
            set_now(None)
    assert set(got) == {"p1", "p2", "p3", "p4", "p5", "p7"}, \
        "every player the market or a line-up names, but a coach: no slot of the eleven is his (and your league has none)"
    assert got["p7"]["price"] == 589995, \
        "listed under his value: the game takes no bid under it (2026-10-29, D. Martinez), so that is what he costs"
    assert got["p1"] == {"name": "ana uno", "pos": "DEF", "value": 5e6, "owner": "me",
                         "clause": 9e6, "price": None, "route": None, "my_bid": None,
                         "offer": 6e6, "paid": None, "trend": None, "fetches": 6e6}, got["p1"]
    assert (got["p2"]["value"], got["p2"]["price"], got["p2"]["route"], got["p2"]["my_bid"]) \
        == (None, 2e6, "listed", 2.5e6), "a value of 0 is none; a manager's listing; your bid"
    assert (got["p5"]["route"], got["p5"]["pos"]) == ("free", "MED"), \
        "the game's own sale is free; a position the game does not field counts as MED"
    assert got["p3"]["offer"] is None and got["p3"]["clause"] is None \
        and got["p3"]["my_bid"] is None, "offers only on yours; no clause, no bid amount: none"
    assert (got["p4"]["name"], got["p4"]["pos"]) == ("Cai Cuatro", "MED"), got["p4"]


def _selftest_market() -> None:
    """The market's SQL (schema.sql: price_obs, auction_results,
    offer_ratios, momentum, bid_odds; datasette.json: price_forecast_error)
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

        from ffcore.db import set_now
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
            {"observed_at": "2026-09-02T2100Z", "activity_id": i, "kind": "buy", "user_id": u, "player_id": p,
             "at": at, "amount": amt, "counterparty": ""}
            for i, u, p, at, amt in [("1", "1", "7", "2026-09-02T22:24:10+02:00", "10400000"),
                                     ("2", "1", "8", "2026-09-02T22:24:10+02:00", "99"),
                                     ("3", "1", "7", "2026-09-05T10:00:00+02:00", "1"),
                                     ("4", "2", "7", "2026-09-02T22:24:20+02:00", "12000000")]])
        with closing(connect()) as db, db:
            db.execute("INSERT INTO league_config VALUES ('me', 100.0)")
            db.executemany("INSERT INTO crosswalk (player_id, app_id) VALUES (?, ?)", [("p7", "7"), ("p8", "8")])
        assert rows("SELECT player, asking, won, by_me, bar FROM auction_results ORDER BY player") == [
            ("p7", 10e6, 12e6, 0, 1.2), ("p8", 10e6, 99.0, 1, None)], \
            "the highest buy at a listing's close is what a bid had to reach (not a buy days later); " \
            "a manager's listing is never reached"
        from ffcore.db import set_now
        from ffcore.parse import snapshot_stamp
        try:
            set_now(snapshot_stamp("2026-09-02T2000Z"))
            assert rows("SELECT count(*) FROM auction_results") == [(0, )], "no auction's outcome before its close"
            set_now(snapshot_stamp("2026-09-02T2024Z"))
            assert rows("SELECT count(*) FROM auction_results") == [(0, )], \
                "closed, but the feed has not yet been seen past it: the buy is not known at t"
            set_now(snapshot_stamp("2026-09-02T2100Z"))
            assert rows("SELECT count(*) FROM auction_results") == [(2, )], "the feed seen ten minutes past the close"
        finally:
            set_now(None)

        store_sql("api_teams", [{"observed_at": a, "player_team_id": "9", "market_value": "10",
                                 "manager": "", "player_id": ""}])
        offer = {"observed_at": a, "player_team_id": "9", "from_market": "true",
                 "money": "11", "offer_id": "o", "created_at": "c"}
        store_sql("api_offers", [offer, offer, dict(offer, from_market="false", money="12",
                                                    offer_id="m")])
        assert market_offer_ratios() == [1.1], "the game's offers, each once, over value"

    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        for n, v in enumerate((100, 110, 110, 110)):
            store_sql("market", [{"observed_at": "2026-08-%02dT2359Z" % (10 + n), "ff_id": "x", "value": str(v)}])
        assert [round(x, 6) for (x,) in rows("SELECT x FROM price_series WHERE ff_id = 'x' ORDER BY i")] \
            == [10.0, 6.0, 2.0], \
            "a +10% step then two flat days: the signal is the last three steps, weighted .4 .4 .2 " \
            "(a missing one counts as the oldest he has: 0 + .4 * 10 + .2 * 10); a rise is felt for three updates"

    def prices(series: dict[str, list[float]]) -> Prices:
        """The momentum views over these values, one a day from 2026-08-10."""
        with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
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
    _selftest_price_at_t(by)
    rise = prices({"r%d" % i: [100 * 1.15 ** n for n in range(13)] for i in range(3)})
    assert abs(rise.slope[2] * 15.0 - (1.15 ** 2 - 1) * 100) < 1e-6, \
        "a rise over several updates is not clipped as if it were one"
    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        for n in range(20):
            store_sql("market", [{"observed_at": "2026-08-%02dT2359Z" % (10 + n), "ff_id": k,
                                  "value": str(v[n])} for k, v in by.items()])
        meta = Path(__file__).resolve().parent.parent / "datasette.json"
        with closing(connect()) as db:
            graded = {h: (n, mae, zero) for h, n, mae, zero in db.execute(
                json.loads(meta.read_text())["databases"]["lfg"]["queries"]
                ["price_forecast_error"]["sql"])}
        assert graded[1][1] < graded[1][2] and graded[3][0] > 0, graded

    def odds(past: list[tuple[float, float | None, bool]]) -> dict[str, tuple[tuple[float, float], ...]]:
        """bid_odds for a player rising 5% listed now, after these past
        listings at 100 (trend, the winning bid or None, whether it was
        yours), BID_NEIGHBOURS of 3."""
        with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)), set_constants(BID_NEIGHBOURS=3):
            at = "2026-09-0%dT1000Z"
            listed = [(str(i), "2026-09-02T22:%02d:00+02:00" % i, str(i)) for i in range(len(past))]
            store_sql("api_standings", [{"observed_at": at % 1, "user_id": "1", "manager": "me"},
                                        {"observed_at": at % 1, "user_id": "2", "manager": "riv"}])
            # each listed player changed t% a day for three days before his close (08-30 .. 09-02): trend t
            # (momentum fits slope 1); the player listed now rises 5% a day to 09-05
            store_sql("market", [{"observed_at": "2026-%s-%02dT1000Z" % (("08", 30 + n) if n < 2 else ("09", n - 1)),
                                  "ff_id": "p" + i, "value": str(100 * (1 + t / 100) ** n)}
                                 for (i, _c, _), (t, _w, _m) in zip(listed, past) for n in range(4)]
                      + [{"observed_at": "2026-%s-%02dT1000Z" % (("08", 30 + n) if n < 2 else ("09", n - 1)),
                          "ff_id": "p99", "value": str(100 * 1.05 ** n)} for n in range(7)])
            store_sql("api_market", [{"observed_at": at % 1, "seller": "marketPlayerLeague", "player_id": i,
                                      "expires_at": close, "sale_price": "100"} for i, close, _ in listed])
            store_sql("api_market", [{"observed_at": at % 5, "seller": "marketPlayerLeague", "player_id": "99",
                                      "expires_at": "2026-09-05T22:24:00+02:00", "sale_price": "100"}])
            store_sql("api_activity", [
                {"observed_at": at % 3, "activity_id": i, "kind": "buy", "user_id": "1" if mine else "2",
                 "player_id": i, "at": close, "amount": str(won), "counterparty": ""}
                for (i, close, _), (_t, won, mine) in zip(listed, past) if won is not None]
                or [{"observed_at": at % 3, "activity_id": "x", "kind": "", "user_id": "",
                     "player_id": "", "at": "", "counterparty": ""}])
            with closing(connect()) as db, db:
                db.execute("INSERT INTO league_config VALUES ('me', 100.0)")
                db.executemany("INSERT INTO crosswalk (player_id, app_id) VALUES (?, ?)",
                               [("p" + i, i) for i, _c, _ in listed] + [("p99", "99")])
            try:
                set_now(datetime(2026, 9, 5, 12, tzinfo=timezone.utc))
                c = see()
                out: dict[str, tuple[tuple[float, float], ...]] = {}
                for k, ratio, p in rows("SELECT player, ratio, p_win FROM seen_odds WHERE cut = ?"
                                        " ORDER BY player, ratio", (c, )):
                    out[k] = (*out.get(k, ()), (ratio, p))
                return out
            finally:
                set_now(None)

    got = odds([(4.0, 110, False), (6.0, 130, False), (5.0, None, False), (-3.0, None, False)])
    assert got == {"p99": ((1.0, 1 / 3), (1.1, 2 / 3), (1.3, 1.0))}, \
        ("the three nearest in trend: one unsold (asking wins), rivals won at 1.1x and 1.3x; "
         "the falling player's unsold listing is not near", got)
    assert odds([(5.0, 150, True), (5.0, None, False)]) == {"p99": ((1.0, 1.0), )}, \
        "a listing you won says nothing of what rivals bid: left out"
    assert odds([]) == {"p99": ((1.0, 1.0), )}, "no past auctions: asking"

if __name__ == "__main__":
    _selftest()
