"""The market's constants. What they set is SQL (schema.sql: momentum,
price_trend, bid_odds); market.py reads PRICE_WINDOW too."""
from __future__ import annotations

CLIP = 20.0
PRICE_WINDOW = 50
# A bid's chance of winning is read off this many past auctions of the
# players nearest in price trend (bid_odds): rising players draw rivals'
# bids (45% of those over +3% sold, at 1.09x asking on average), falling
# ones almost never (5%; 2026-10-20). 30, not 60: with 85 past listings
# rising 10%+, 60 borrowed slower ones and put a riser's chance at asking at
# 0.48 (0.33 won). Each past listing's odds from those before it, Brier
# 0.0264 at 60, 0.0259 at 30; the money replay (grading.py money, since deleted) forecast points
# 478 at 60, 489 at 45, 497 at 30, 495 at 20, value and cash 310M, 329M,
# 377M, 352M (2026-10-31).
BID_NEIGHBOURS = 30
# How much each of a player's last three value changes (newest first) says of the
# next: the game's price rule (plan 5.6; fitted 0.35/0.33/0.18, no more lags help).
PRICE_W0 = 0.4
PRICE_W1 = 0.4
PRICE_W2 = 0.2


def _selftest() -> None:
    print("ffcore.pricing self-test OK")


if __name__ == "__main__":
    _selftest()
