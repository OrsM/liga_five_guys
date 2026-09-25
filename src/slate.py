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

import sys


from ffcore.text import norm

__all__ = ["read_slate", "slate_from_api", "comparison_rows",
          "comparison_table"]


def slate_from_api(rows: list[dict], market, xw=None) -> tuple[set, list]:
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
    if rows is None:
        from ffcore.tidy import load_api
        rows = load_api("market")
    return slate_from_api(rows, market, xw)


def comparison_rows(u, bands=None) -> list[dict]:
    import decide
    from ffcore.render import title_name

    mine = set(u.state.squads.get(u.me, {}))
    fc = u.player_forecasts()
    out = []
    for k, price in u.view("price").items():
        if k in mine:
            continue
        f = fc.get(k, {})
        par, par_lo, par_hi = f.get("par"), None, None
        b = (bands or {}).get(k)
        if b is not None:
            par, par_lo, par_hi, _act, _mean = b
        out.append({
            "key": k, "name": title_name(u.view("name").get(k, k)),
            "pos": u.view("pos").get(k, ""), "price": price,
            "season_pts": f.get("season_pts"), "next_pts": f.get("next_pts"),
            "par": par, "par_lo": par_lo, "par_hi": par_hi,
            "value": decide.value_rate(par, price),
            "simulated": b is not None,
        })
    out.sort(key=lambda r: (r["value"] is None, -(r["value"] or 0.0)))
    return out


def comparison_table(rows: list[dict]) -> list[str]:
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

    keys, unres = slate_from_api(
        rows + [{"player_name": "Nobody At All"}], market)
    assert unres == ["Nobody At All"] and len(keys) == 2, (keys, unres)

    keys, unres = slate_from_api([{"player_name": "Fornals"}], market)
    assert keys == {norm("Pablo Fornals")} and unres == [], (keys, unres)

    assert slate_from_api([{"player_name": ""}], market) == (set(), [])

    assert norm("Stole Dimitrievski") in slate_from_api(rows, market)[0]


    assert read_slate(market, rows=[]) == (set(), [])

    from ffcore.crosswalk import Crosswalk, Player
    xw = Crosswalk({"pablo fornals": Player("pablo fornals", "Pablo Fornals",
                                            app_id="1337")})
    keys, unres = slate_from_api(
        [{"player_name": "Nickname Nothing Joins On", "player_id": "1337"}],
        market, xw=xw)
    assert keys == {"pablo fornals"} and unres == [], (keys, unres)
    keys, unres = slate_from_api(
        [{"player_name": "Fornals", "player_id": "9999"}], market, xw=xw)
    assert keys == {norm("Pablo Fornals")} and unres == [], (keys, unres)

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
    assert keys == {"cheap", "rich"}, keys
    by_key = {r["key"]: r for r in rows}
    assert by_key["cheap"]["season_pts"] == 6.0, by_key["cheap"]
    assert by_key["rich"]["season_pts"] == 16.0, by_key["rich"]
    assert by_key["cheap"]["par"] == 2.0, by_key["cheap"]
    assert by_key["rich"]["par"] == 12.0, by_key["rich"]
    assert by_key["cheap"]["par_lo"] is None
    assert rows[0]["key"] == "cheap", rows

    md = comparison_table(rows)
    assert any(l.startswith("| Cheap") for l in md), md

    b = {"cheap": (2.5, 1.0, 4.0, Action("buy", buy="cheap", cost=2e6), 2.4)}
    rows2 = comparison_rows(cu, bands=b)
    by_key2 = {r["key"]: r for r in rows2}
    assert (by_key2["cheap"]["par"], by_key2["cheap"]["par_lo"],
           by_key2["cheap"]["par_hi"]) == (2.5, 1.0, 4.0), by_key2["cheap"]
    assert by_key2["rich"]["par_lo"] is None, by_key2["rich"]
    md2 = comparison_table(rows2)
    assert any("1–4" in l for l in md2), md2

    assert comparison_table([]) == []

    cu_listed = Universe(
        state=LeagueState(cu_sq, [1, 2], "me"),
        forecaster=Bootstrap(cu_per), cash=0.0, me="me",
        players={"me_a": cp(5.0, price=3e6),
                "cheap": cp(5.0, price=2e6, name="cheap"),
                "rich": cp(5.0, price=20e6, name="rich")})
    rows3 = comparison_rows(cu_listed)
    assert {r["key"] for r in rows3} == {"cheap", "rich"}, rows3

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
