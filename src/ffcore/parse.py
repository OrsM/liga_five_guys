
from __future__ import annotations

import re
from datetime import datetime, timezone
from functools import lru_cache

__all__ = ["money", "ratio", "pct100", "fmt_money", "text", "num", "whole",
           "flag", "snapshot_stamp", "kickoff_stamp"]

_DOT_GROUPED = re.compile(r"\d{1,3}(?:\.\d{3})+$")
_CLEAN = str.maketrans({"\u00a0": "", " ": "", "\u202f": ""})


def _strip(v) -> tuple[str, bool]:
    if v is None:
        return "", False
    t = str(v).strip().translate(_CLEAN)
    t = t.replace("\u20ac", "").replace("EUR", "").replace("eur", "")
    if not t:
        return "", False
    neg = t.startswith("-") or (t.startswith("(") and t.endswith(")"))
    t = t.lstrip("+-").strip("()")
    return t, neg


def money(v):
    t, neg = _strip(v)
    if not t:
        return None
    mult = 1.0
    if t[-1:].upper() in ("M", "K"):
        mult = 1e6 if t[-1].upper() == "M" else 1e3
        t = t[:-1]
    dot, com = "." in t, "," in t
    if dot and com:
        degrouped = (t.replace(".", "").replace(",", ".")
                    if t.rfind(",") > t.rfind(".") else t.replace(",", ""))
    elif com:
        degrouped = t.replace(",", "") if t.count(",") > 1 else t.replace(",", ".")
    elif dot and _DOT_GROUPED.fullmatch(t):
        degrouped = t.replace(".", "")
    else:
        degrouped = t
    try:
        x = float(degrouped) * mult
    except ValueError:
        return None
    return -x if neg else x


def ratio(v):
    t, neg = _strip(v)
    if not t:
        return None
    t = t.replace("%", "").replace(",", ".")
    if t in {"-", "\u2014", ".", ""}:
        return None
    try:
        x = float(t)
    except ValueError:
        return None
    return -x if neg else x


def pct100(v):
    x = ratio(v)
    if x is None:
        return None
    return x * 100.0 if 0.0 <= x <= 1.0 else x


def fmt_money(v) -> str:
    if v is None:
        return "—"
    if abs(v) >= 1e6:
        return "%.2fM" % (v / 1e6)
    return "%.0fK" % (v / 1e3)


def text(row, col: str, default: str = "") -> str:
    v = row.get(col) or ""
    v = v.strip() if isinstance(v, str) else str(v).strip()
    return v if v else default


def num(row, col: str, default=None):
    v = row.get(col)
    if v is None:
        return default
    s = v.strip() if isinstance(v, str) else v
    if s == "":
        return default
    try:
        return float(s)
    except (TypeError, ValueError):
        return default


def whole(row, col: str, default=None):
    v = num(row, col, default=None)
    if v is None:
        return default
    try:
        return int(v)
    except (TypeError, ValueError, OverflowError):
        return default


def flag(row, col: str, default: bool = False) -> bool:
    v = row.get(col)
    if not isinstance(v, str):
        return default
    s = v.strip().lower()
    if s in ("true", "1"):
        return True
    if s in ("false", "0"):
        return False
    return default


@lru_cache(maxsize=4096)
def _digits_to_dt(s: str, tz):
    digits = re.sub(r"\D", "", s or "")
    if len(digits) < 8:
        return None
    try:
        return datetime(
            int(digits[:4]), int(digits[4:6]), int(digits[6:8]),
            int(digits[8:10]) if len(digits) >= 10 else 0,
            int(digits[10:12]) if len(digits) >= 12 else 0,
            tzinfo=tz)
    except ValueError:
        return None


def year_for(month: int, seen, start: int) -> int:
    """The year of a month named on a page seen on `seen`, read inside the
    twelve months that begin at month `start`: July for a season's fixtures,
    last month for a date still to come."""
    return seen.year - (seen.month < start) + (month < start)


def snapshot_stamp(s: str):
    return _digits_to_dt(s, timezone.utc)


def kickoff_stamp(s: str | None):
    try:
        when = datetime.fromisoformat((s or "").strip())
    except ValueError:
        return None
    return (when.replace(tzinfo=timezone.utc) if when.tzinfo is None
            else when.astimezone(timezone.utc))


def _selftest() -> None:
    from datetime import datetime, timezone

    assert kickoff_stamp("2026-08-15T19:30:00+00:00") == datetime(
        2026, 8, 15, 19, 30, tzinfo=timezone.utc)
    assert kickoff_stamp("2026-08-15T21:30:00+02:00") == datetime(
        2026, 8, 15, 19, 30, tzinfo=timezone.utc)
    assert kickoff_stamp("2026-08-15T19:30:00") == datetime(
        2026, 8, 15, 19, 30, tzinfo=timezone.utc)
    assert kickoff_stamp("") is None and kickoff_stamp("soon") is None

    row = {"s": "  x ", "blank": " ", "n": "3.5", "i": "4.0", "bad": "x",
           "t": "True", "f": "0", "num": 7}
    for fn, col, default, want in [
            (text, "s", "", "x"), (text, "blank", "d", "d"),
            (text, "missing", "", ""), (text, "num", "", "7"),
            (num, "n", None, 3.5), (num, "blank", 0.0, 0.0),
            (num, "bad", None, None), (num, "num", None, 7.0),
            (whole, "i", None, 4), (whole, "bad", -1, -1),
            (flag, "t", False, True), (flag, "f", True, False),
            (flag, "bad", True, True)]:
        assert fn(row, col, default) == want, (fn.__name__, col, want)
    cases_money = {
        "2.050.000": 2050000, "35.276.000": 35276000, "700.000": 700000,
        "49.991.863\u20ac": 49991863, "6892898": 6892898, "80.000.000": 80000000,
        "1.5M": 1500000, "700K": 700000, "-468693": -468693,
        "(468693)": -468693, "-12345.0": -12345.0, "1.234,56": 1234.56,
        "1,234,567": 1234567, "": None, None: None, "n/a": None,
    }
    for raw, want in cases_money.items():
        got = money(raw)
        assert got == want or (want is None and got is None), \
            f"money({raw!r}) -> {got!r}, wanted {want!r}"

    cases_ratio = {"2.37": 2.37, "4,59": 4.59, "72%": 72.0, "-3.5": -3.5,
                   "\u2014": None, "": None}
    for raw, want in cases_ratio.items():
        got = ratio(raw)
        assert got == want or (want is None and got is None), \
            f"ratio({raw!r}) -> {got!r}, wanted {want!r}"

    for raw, want in {"0.72": 72.0, "72": 72.0, "1": 100.0, "0": 0.0,
                      "95.5": 95.5}.items():
        got = pct100(raw)
        assert got == want, f"pct100({raw!r}) -> {got!r}, wanted {want!r}"

    fmt_cases = {2050000.0: "2.05M", 700000.0: "700K", -468693.0: "-469K",
                 0.0: "0K", None: "—"}
    for amount, shown in fmt_cases.items():
        assert fmt_money(amount) == shown, f"fmt_money({amount!r}), wanted {shown!r}"

    print("ffcore.parse self-test OK")


if __name__ == "__main__":
    _selftest()
