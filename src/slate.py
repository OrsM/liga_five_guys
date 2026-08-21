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

__all__ = ["read_slate", "slate_from_api"]


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

    print("slate self-test OK (15 cases)")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        raise SystemExit(0)
    print(__doc__.strip())
