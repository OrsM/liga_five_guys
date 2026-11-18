from __future__ import annotations

import csv
import functools
import io
import os
from pathlib import Path
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any

from ffcore.parse import count, money, pct100, ratio

__all__ = ["ROOT", "TIDY", "REPORTS",
           "cell", "APP_FRESH_HOURS", "input_path", "csv_string", "NUMBERS"]

ROOT = Path(os.environ.get("FF_ROOT", "./data"))
TIDY = ROOT / "tidy"
REPORTS = Path(os.environ.get("LFG_REPORTS", "reports"))


def input_path(name: str) -> Path:
    p = Path("inputs") / name
    return p if p.exists() else Path(name)


def csv_string(rows: Iterable[Mapping[str, Any]], fieldnames: Sequence[str]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fieldnames, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def _cols(parse: Callable[[object], Any], *cols: str
          ) -> tuple[tuple[str, Callable[[object], Any]], ...]:
    return tuple((c, parse) for c in cols)


NUMBERS: dict[str, tuple[tuple[str, Callable[[object], Any]], ...]] = {
    "market": _cols(money, "value"),
    "lineups": _cols(pct100, "start_pct"),
    "matches": _cols(count, "jornada"),
    "points": _cols(ratio, "points", "games", "avg"),
    "api_teams": _cols(money, "market_value", "buyout") + _cols(ratio, "points"),
    "api_standings": _cols(count, "position")
                           + _cols(ratio, "team_points", "fixture_points")
                           + _cols(money, "team_value", "team_money"),
    "api_market": _cols(money, "market_value", "sale_price", "bid_money")
                        + _cols(count, "bids"),
    "api_offers": _cols(money, "money"),
    "api_lineup": _cols(money, "market_value")
                        + _cols(ratio, "points"),
    "api_leagues": _cols(count, "managers") + _cols(money, "money", "team_value")
                         + _cols(ratio, "team_points"),
    "api_players_all": _cols(money, "market_value"),
    "results_history": _cols(ratio, "home_goals", "away_goals"),
    "starters": _cols(ratio, "minute"),
    "api_activity": _cols(money, "amount"),
}


def cell(name: str, column: str) -> Callable[[Any], Any]:
    """What table name stores for a raw value of column (a document's, as parse emitted it; None when the row has
    none): a numeric column's value parsed (NUMBERS) after the strip, any other column's text stripped, and empty when
    missing. Equal raw values give equal results; columns stored alike get the same function."""
    parse = dict(NUMBERS.get(name, ())).get(column)
    return _text_cell if parse is None else _number_cell(parse)


def _text_cell(raw: Any) -> Any:
    return _stripped(raw) or ""


@functools.cache
def _number_cell(parse: Callable[[object], Any]) -> Callable[[Any], Any]:
    return lambda raw: parse(_stripped(raw))


def _stripped(value: Any) -> Any:
    return value.strip() if isinstance(value, str) else value


APP_FRESH_HOURS = 14.4


def _selftest() -> None:
    assert cell("market", "value")(" 5000000 ") == 5e6 == cell("api_teams", "market_value")("5000000"), \
        "a number is parsed after the strip, alike in every column that stores one so"
    assert cell("matches", "jornada")("8") == 8 and cell("matches", "jornada")("") is None, "a missing number is None"
    assert cell("matches", "home")(" x ") == "x" and cell("matches", "home")(None) == "", "text is stripped, missing is empty"
    print("ffcore.tidy self-test OK")


if __name__ == "__main__":
    _selftest()
