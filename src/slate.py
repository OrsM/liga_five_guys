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

__all__ = ["read_slate", "slate_from_api", "slate_prices"]


def slate_from_api(rows: list[dict], market) -> tuple[set, list]:
    """(player keys on offer, names that would not join).

    Keyed through `market.key_for`, the resolution every other reader uses: a
    key nothing else recognises is a player who vanishes from the board. So an
    unjoinable name is REPORTED, never dropped — dropped, it renders as "not
    on offer", which is the bug this replaced.

    No ownership prune. A player being owned is not evidence against his being
    on offer: that is exactly what `marketPlayerTeam` is.
    """
    keys, unresolved = set(), []
    for r in rows:
        raw = (r.get("player_name") or "").strip()
        if not raw:
            continue
        key = market.key_for(raw) if market is not None else norm(raw)
        if key:
            keys.add(key)
        else:
            unresolved.append(raw)
    return keys, unresolved


def slate_prices(rows: list[dict], market) -> dict:
    """{player key: {sale_price, bids, seller, expires_at}}.

    `bids` is None when the feed did not state one (manager-listed players
    carry null). None must never become 0: that reads as "nobody is bidding"
    rather than "we do not know".
    """
    out = {}
    for r in rows:
        raw = (r.get("player_name") or "").strip()
        if not raw:
            continue
        key = market.key_for(raw) if market is not None else norm(raw)
        if not key:
            continue
        try:
            price = float(r.get("sale_price") or 0) or None
        except ValueError:
            price = None
        bids = (r.get("bids") or "").strip()
        out[key] = {"sale_price": price,
                    "bids": int(bids) if bids.isdigit() else None,
                    "seller": r.get("seller") or "",
                    "expires_at": r.get("expires_at") or ""}
    return out


def read_slate(market, rows=None) -> tuple[set, list]:
    """The live slate: (keys on offer, unjoined names).

    An empty feed is "no slate" — every caller treats that as "do not filter",
    not "the market is empty".
    """
    if rows is None:
        from ffcore.tidy import load_api_market
        rows = load_api_market()
    return slate_from_api(rows, market)


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

    px = slate_prices(rows, market)
    assert px[norm("Álvaro Valles")]["sale_price"] == 31900000.0
    assert px[norm("Stole Dimitrievski")]["bids"] == 2
    assert px[norm("Stole Dimitrievski")]["seller"] == "marketPlayerTeam"
    # Null bids is NOT STATED, never zero.
    assert slate_prices([{"player_name": "Fornals", "bids": ""}],
                        market)[norm("Pablo Fornals")]["bids"] is None
    # An unjoinable name carries no price row either — a price keyed on a name
    # nothing recognises would never be read and would look like a gap.
    assert slate_prices([{"player_name": "Nobody"}], market) == {}

    # No feed is no slate.
    assert read_slate(market, rows=[]) == (set(), [])

    print("slate self-test OK (13 cases)")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        raise SystemExit(0)
    print(__doc__.strip())
