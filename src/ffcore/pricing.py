"""The market's constants. What they set is SQL (schema.sql: momentum,
price_trend, bid_odds); market.py reads PRICE_WINDOW too."""
from __future__ import annotations

CLIP = 20.0
PRICE_WINDOW = 50
# A bid's chance of winning is read off this many past auctions of the
# players nearest in price trend (bid_odds): rising players draw rivals'
# bids (45% of those over +3% sold, at 1.09x asking on average), falling
# ones almost never (5%; 2026-10-20).
BID_NEIGHBOURS = 60
# How much each of a player's last three value changes (newest first) says of the
# next: the game's price rule (plan 5.6; fitted 0.35/0.33/0.18, no more lags help).
PRICE_W0 = 0.4
PRICE_W1 = 0.4
PRICE_W2 = 0.2
# A buy to resell is shown when its forecast sale beats the bid by this share:
# past nights' picks over 5% made money 76% of the time, those under it
# lost on average (grading.py resales, 2026-10-19).
RESALE_MARGIN = 0.05
# How far ahead a move's money is counted, in price updates (about a day
# each): what a player bought will be worth then, and one sold would have
# been. grading.py money replays the choices.
MONEY_H = 7


def _selftest() -> None:
    print("ffcore.pricing self-test OK")


if __name__ == "__main__":
    _selftest()
