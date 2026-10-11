"""The market's constants. What they set is SQL (schema.sql: momentum,
price_trend, premium); market.py reads PRICE_WINDOW too."""
from __future__ import annotations

CLIP = 20.0
PRICE_WINDOW = 50
BID_BEATS = 0.8


def _selftest() -> None:
    print("ffcore.pricing self-test OK")


if __name__ == "__main__":
    _selftest()
