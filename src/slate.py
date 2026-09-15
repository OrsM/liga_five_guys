"""
slate.py — what is on offer in the league right now.

    python src/slate.py --selftest

Replaced seen.py (OCR'd market screenshots) once the league API started
publishing the market directly. The feed is strictly better: 41 rows against a
screenshot's dozen, it distinguishes app-dealt free agents from players a
manager has listed, and it carries a bid count. See the README for why the OCR
fallback was deleted rather than kept.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ffcore.text import norm  # noqa: E402

__all__ = ["read_slate", "slate_from_api", "comparison_rows",
          "comparison_table"]


def slate_from_api(rows: list[dict], market, xw=None) -> tuple[set, list]:
    """(player keys on offer, names that would not join).

    THE ROW'S OWN `player_id` FIRST, when a crosswalk is given to translate
    it — api_market.csv carries the app's own id on every row, and this used
    to ignore it and join on `player_name` alone, the exact "fuzzy name
    instead of the id the source already handed us" gap `Crosswalk.resolve()`
    exists to close. Falls back to `market.key_for` (the resolution every
    other reader uses) when there is no crosswalk, no id, or the id is one
    the crosswalk has not learned — a key nothing recognises is a player who
    vanishes from the board, so an unjoinable name is REPORTED, never
    dropped, either way.

    No ownership prune. A player being owned is not evidence against his being
    on offer: that is exactly what `marketPlayerTeam` is.
    """
    keys, unresolved = set(), []
    for r in rows:
        raw = (r.get("player_name") or "").strip()
        if not raw:
            continue
        if xw is not None:
            key = xw.resolve(raw, hint_app_id=r.get("player_id") or "",
                             market=market)
        else:
            key = market.key_for(raw) if market is not None else norm(raw)
        if key:
            keys.add(key)
        else:
            unresolved.append(raw)
    return keys, unresolved


def read_slate(market, rows=None, xw=None) -> tuple[set, list]:
    """The live slate: (keys on offer, unjoined names).

    An empty feed is "no slate" — every caller treats that as "do not filter",
    not "the market is empty".
    """
    if rows is None:
        from ffcore.tidy import load_api_market
        rows = load_api_market()
    return slate_from_api(rows, market, xw)


def comparison_rows(u, bands=None) -> list[dict]:
    """Every listed player YOU COULD BUY: season points, next-jornada
    points, and points above replacement (median, with a real season band
    from `bands` where one was computed).

    NOT your own squad, even a player of yours currently listed for sale —
    this table answers "is he worth buying", and a man already on your
    squad is never a buy candidate for you; the ladder's own SELL/KEEP
    rows are where his own listing belongs.

    `bands`, when given, is decide.rank()'s own `bands` dict — pts_lo/
    pts_hi for the same PAR figure decide.player_forecasts() reports as
    a point estimate. A key not in `bands` gets `par_lo`/`par_hi` = None,
    not a fabricated band.
    """
    import decide
    from ffcore.render import title_name

    mine = set(u.state.squads.get(u.me, {}))
    fc = u.player_forecasts()
    out = []
    for k, price in u.price.items():
        if k in mine:
            continue
        f = fc.get(k, {})
        par, par_lo, par_hi = f.get("par"), None, None
        b = (bands or {}).get(k)
        if b is not None:
            par, par_lo, par_hi, _act = b
        out.append({
            "key": k, "name": title_name(u.name.get(k, k)),
            "pos": u.pos.get(k, ""), "price": price,
            "season_pts": f.get("season_pts"), "next_pts": f.get("next_pts"),
            "par": par, "par_lo": par_lo, "par_hi": par_hi,
            "value": decide.value_rate(par, price),
            "simulated": b is not None,
        })
    out.sort(key=lambda r: (r["value"] is None, -(r["value"] or 0.0)))
    return out


def comparison_table(rows: list[dict]) -> list[str]:
    """Markdown: listed players actually worth a look, ranked by PAR per
    million spent — not every listed player.

    PRINTS ONLY par > 0 (genuinely above the league's own replacement
    level at his slot), then ONE summary line for the rest, rather than
    a full table trailing off through dozens of 0.0-and-negative rows a
    reader has to scan past to find the handful that matter. `rows` is
    already sorted by value_rate descending, so this is a straight cut,
    not a re-sort.
    """
    from ffcore.parse import fmt_money

    if not rows:
        return []

    def num(v, fmt="%.1f"):
        return fmt % v if v is not None else "—"

    worth_a_look = [r for r in rows if (r["par"] or 0.0) > 0.0]
    skipped = len(rows) - len(worth_a_look)

    from methodology import PAR_DEFINITION

    out = ["## Every listed player, compared", "",
          "PAR = " + PAR_DEFINITION + ". Comparable across "
          "positions on that basis; a good player can still show a modest "
          "PAR if his position is deep league-wide. Parenthesised range is "
          "a real simulated band where one was run; a plain figure is the "
          "point estimate. Only players ABOVE replacement are listed — see "
          "the note below the table for the rest.",
          "",
          "| Player | Pos | Price | Season | Next | PAR | pts/€M |",
          "|---|---|---:|---:|---:|---:|---:|"]
    for r in worth_a_look:
        par_cell = num(r["par"])
        if r["par_lo"] is not None:
            par_cell += " (%s–%s)" % (num(r["par_lo"], "%.0f"),
                                          num(r["par_hi"], "%.0f"))
        out.append("| %s | %s | %s | %s | %s | %s | %s |" % (
            r["name"], r["pos"], fmt_money(r["price"]),
            num(r["season_pts"]), num(r["next_pts"]), par_cell,
            num(r["value"], "%.2f")))
    out.append("")
    if skipped:
        out += [f"_{skipped} more listed player{'s' if skipped != 1 else ''} "
                "at or below the league's own replacement level at their "
                "slot, not worth a look today — a free agent nobody wants "
                "yet, or a rival's clause on a squad player he outgrew._",
                ""]
    return out


def _selftest() -> None:
    from ffcore.tidy import Market

    at = "2026-08-17T2246Z"
    market = Market([
        {"name": "Álvaro Valles", "value": "31900000", "observed_at": at,
         "position": "POR"},
        {"name": "Stole Dimitrievski", "value": "5000000", "observed_at": at,
         "position": "POR"},
        {"name": "Pablo Fornals", "value": "58300000", "observed_at": at,
         "position": "MED"}])

    rows = [{"player_name": "Álvaro Valles", "sale_price": "31900000",
             "bids": "0", "seller": "marketPlayerLeague"},
            {"player_name": "Stole Dimitrievski", "sale_price": "5000000",
             "bids": "2", "seller": "marketPlayerTeam"}]

    keys, unres = slate_from_api(rows, market)
    assert keys == {norm("Álvaro Valles"), norm("Stole Dimitrievski")}, keys
    assert unres == [], unres

    # A name the market does not carry is reported, never dropped.
    keys, unres = slate_from_api(
        rows + [{"player_name": "Nobody At All"}], market)
    assert unres == ["Nobody At All"] and len(keys) == 2, (keys, unres)

    # The app's shorter spelling still joins, because key_for resolves it —
    # this is the whole reason the join does not go through norm() directly.
    keys, unres = slate_from_api([{"player_name": "Fornals"}], market)
    assert keys == {norm("Pablo Fornals")} and unres == [], (keys, unres)

    # A row with no name is skipped rather than becoming an empty key.
    assert slate_from_api([{"player_name": ""}], market) == (set(), [])

    # An owned player his manager has listed is ON OFFER. There is no
    # ownership prune here and there must not be one.
    assert norm("Stole Dimitrievski") in slate_from_api(rows, market)[0]


    # No feed is no slate.
    assert read_slate(market, rows=[]) == (set(), [])

    # THE APP'S OWN player_id, WHEN A CROSSWALK IS GIVEN TO TRANSLATE IT —
    # api_market.csv carries this on every row and it went unread. Even a
    # name the market cannot join at all still resolves through the id.
    from ffcore.crosswalk import Crosswalk, Player
    xw = Crosswalk({"pablo fornals": Player("pablo fornals", "Pablo Fornals",
                                            app_id="1337")})
    keys, unres = slate_from_api(
        [{"player_name": "Nickname Nothing Joins On", "player_id": "1337"}],
        market, xw=xw)
    assert keys == {"pablo fornals"} and unres == [], (keys, unres)
    # An id the crosswalk has never seen falls back to the name join.
    keys, unres = slate_from_api(
        [{"player_name": "Fornals", "player_id": "9999"}], market, xw=xw)
    assert keys == {norm("Pablo Fornals")} and unres == [], (keys, unres)

    # -- comparison_rows/comparison_table: every listed player, ranked by
    # PAR per million ----------------------------------------------------
    from decide import Universe, Action
    from ffcore.forecast import Bootstrap
    from ffcore.season import LeagueState
    from ffcore.crosswalk import Player
    from ffcore.profile import (PlayerProfile, PlayerCurrent,
                                PlayerHistory, PlayerDerived)

    def cp(pj, pos="MED", price=5e6, name=""):
        return PlayerProfile(
            identity=Player(player_id="x", name=name),
            current=PlayerCurrent(pos=pos, price=price, listed=price is not None),
            history=PlayerHistory(), derived=PlayerDerived(pj=pj))

    cu_sq = {"me": {"me_a": "MED"}}
    cu_per = {1: {"me_a": (2.0, 1.0), "cheap": (3.0, 1.0), "rich": (8.0, 1.0)},
             2: {"me_a": (2.0, 1.0), "cheap": (3.0, 1.0), "rich": (8.0, 1.0)}}
    cu = Universe(
        state=LeagueState(cu_sq, [1, 2], "me"),
        forecaster=Bootstrap(cu_per), cash=0.0, me="me",
        players={"me_a": cp(5.0, price=None),
                "cheap": cp(5.0, price=2e6, name="cheap"),
                "rich": cp(5.0, price=20e6, name="rich")})
    rows = comparison_rows(cu)
    keys = {r["key"] for r in rows}
    assert keys == {"cheap", "rich"}, keys   # "me_a" isn't listed at all
    by_key = {r["key"]: r for r in rows}
    assert by_key["cheap"]["season_pts"] == 6.0, by_key["cheap"]
    assert by_key["rich"]["season_pts"] == 16.0, by_key["rich"]
    # LEAGUE-wide replacement, not "my own squad's weakest" — one manager,
    # one legal MED rung (starters_per_slot()["MED"]=4.0 x 1 squad = 4,
    # beyond the 3-player pool), so replacement is the pool's OWN worst,
    # me_a at 4.0 — coincidentally who "my squad's weakest" would also
    # have been here, since he's my only MED. PAR 2.0 and 12.0 either way.
    assert by_key["cheap"]["par"] == 2.0, by_key["cheap"]
    assert by_key["rich"]["par"] == 12.0, by_key["rich"]
    assert by_key["cheap"]["par_lo"] is None   # no band given
    # "cheap" wins on points-per-million despite the smaller PAR: 2/2 = 1.0
    # against rich's 12/20 = 0.6.
    assert rows[0]["key"] == "cheap", rows

    md = comparison_table(rows)
    assert any(l.startswith("| Cheap") for l in md), md

    # A real band, from decide.rank(), overrides the point estimate and
    # carries its own range — a key with no band keeps the plain figure.
    b = {"cheap": (2.5, 1.0, 4.0, Action("buy", buy="cheap", cost=2e6))}
    rows2 = comparison_rows(cu, bands=b)
    by_key2 = {r["key"]: r for r in rows2}
    assert (by_key2["cheap"]["par"], by_key2["cheap"]["par_lo"],
           by_key2["cheap"]["par_hi"]) == (2.5, 1.0, 4.0), by_key2["cheap"]
    assert by_key2["rich"]["par_lo"] is None, by_key2["rich"]
    md2 = comparison_table(rows2)
    assert any("1–4" in l for l in md2), md2

    assert comparison_table([]) == []

    # -- a player of MINE, even one I've listed, is not a buy candidate ----
    cu_listed = Universe(
        state=LeagueState(cu_sq, [1, 2], "me"),
        forecaster=Bootstrap(cu_per), cash=0.0, me="me",
        players={"me_a": cp(5.0, price=3e6),   # listed for sale -- still mine
                "cheap": cp(5.0, price=2e6, name="cheap"),
                "rich": cp(5.0, price=20e6, name="rich")})
    rows3 = comparison_rows(cu_listed)
    assert {r["key"] for r in rows3} == {"cheap", "rich"}, rows3

    # -- the long non-competitive tail collapses to one summary line -------
    dud_rows = [{"key": "dud%d" % i, "name": "Dud %d" % i, "pos": "MED",
                "price": 1e6, "season_pts": 1.0, "next_pts": 0.5,
                "par": 0.0, "par_lo": None, "par_hi": None, "value": 0.0}
               for i in range(3)]
    md3 = comparison_table([rows[0]] + dud_rows)
    assert any(l.startswith("| Cheap") for l in md3), md3
    assert not any(l.startswith("| Dud") for l in md3), md3
    assert any("3 more listed players" in l for l in md3), md3

    print("slate self-test OK (26 cases)")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        raise SystemExit(0)
    print(__doc__.strip())
