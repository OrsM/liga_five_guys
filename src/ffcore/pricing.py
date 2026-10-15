"""The market's constants. What they set is SQL (schema.sql: momentum,
price_trend, premium); market.py reads PRICE_WINDOW too."""
from __future__ import annotations

CLIP = 20.0
PRICE_WINDOW = 50
BID_BEATS = 0.8
# How much each of a player's last three value changes (newest first) says of the
# next: the game's price rule (plan 5.6; fitted 0.35/0.33/0.18, no more lags help).
PRICE_W0 = 0.4
PRICE_W1 = 0.4
PRICE_W2 = 0.2


def _selftest() -> None:
    print("ffcore.pricing self-test OK")


if __name__ == "__main__":
    _selftest()
