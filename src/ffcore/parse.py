"""
ffcore.parse — numbers, parsed by what the field MEANS.

A dot is a thousands separator in the app ("2.050.000" is two million) and a
decimal point on futbolfantasy ("2.37" is two point three seven). No parser
can tell those apart from the string alone, so this module does not try:
you choose money() or ratio() by which field you are reading, and the caller
records that choice once, at the point it names the column.

fmt_money() and fmt_pct() are the way back out, for report tables. They live
here so a euro prints the same in every report — rivals.py carried its own
byte-identical copy of fmt_money before this.

The four parsers that existed before this module all got a case wrong:

    "2.050.000"   common._num -> None        (every ledger price!)
    "2.37"        bids.num    -> 237.0
    "700.000"     offers.num  -> ValueError -> 0.0
    "1.5M"        report.num  -> 0.0

    money("2.050.000") -> 2050000.0     ratio("2.37") -> 2.37

Known ambiguity, left deliberately: money("44.550") is 44550, not 44.55.
Three-digit groups after a dot are read as thousands. That is right for every
euro field in this repo; it is wrong for futbolfantasy's "valor/punto" column,
which is a ratio — so parse that one with ratio() if you ever ingest it.

Run `python src/ffcore/parse.py` to execute the self-test below.
"""

from __future__ import annotations

import re

__all__ = ["money", "ratio", "pct100", "fmt_money", "fmt_pct"]

# 1.234 / 1.234.567 — dot-grouped thousands, at least one full group of three.
_DOT_GROUPED = re.compile(r"\d{1,3}(?:\.\d{3})+$")
_CLEAN = str.maketrans({"\u00a0": "", " ": "", "\u202f": ""})


def _strip(v) -> tuple[str, bool]:
    """Return (bare digits-and-separators, negative?) or ("", False)."""
    if v is None:
        return "", False
    t = str(v).strip().translate(_CLEAN)
    t = t.replace("\u20ac", "").replace("EUR", "").replace("eur", "")
    if not t:
        return "", False
    neg = t.startswith("-") or (t.startswith("(") and t.endswith(")"))
    t = t.lstrip("+-").strip("()")
    return t, neg


def _degroup(t: str) -> str:
    """Normalise separators to a bare float string.

    Both present  -> the rightmost one is the decimal separator.
    Commas only   -> one comma is a European decimal; several are thousands.
    Dots only     -> full groups of three are thousands, otherwise a decimal.
    """
    dot, com = "." in t, "," in t
    if dot and com:
        if t.rfind(",") > t.rfind("."):
            return t.replace(".", "").replace(",", ".")
        return t.replace(",", "")
    if com:
        return t.replace(",", "") if t.count(",") > 1 else t.replace(",", ".")
    if dot and _DOT_GROUPED.fullmatch(t):
        return t.replace(".", "")
    return t


def money(v):
    """Euro amount -> float, or None if it isn't one.

    Handles "49.991.863€", "35.276.000", "6892898", "1.5M", "700K",
    "-468693", "(468693)". Empty and unparseable both give None, so a missing
    price never quietly becomes zero.
    """
    t, neg = _strip(v)
    if not t:
        return None
    mult = 1.0
    if t[-1:].upper() in ("M", "K"):
        mult = 1e6 if t[-1].upper() == "M" else 1e3
        t = t[:-1]
    try:
        x = float(_degroup(t)) * mult
    except ValueError:
        return None
    return -x if neg else x


def ratio(v):
    """A number whose dot IS a decimal point: averages, percentages, deltas.

    "4,59" -> 4.59, "72%" -> 72.0, "2.37" -> 2.37. Never regroups thousands,
    which is exactly the mistake bids.num() made on percentages.
    """
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
    """Start probability on a 0-100 scale, whichever scale it arrived on.

    Sources publish either 0-1 or 0-100. Anything in [0, 1] is scaled up,
    which does misread a genuine 1% as 100% — the same trade the old code
    made, and the right one: a true 1% starter is indistinguishable from
    noise anyway, while a 1.0 misread as 1% would bench a nailed-on starter.
    """
    x = ratio(v)
    if x is None:
        return None
    return x * 100.0 if 0.0 <= x <= 1.0 else x


def fmt_money(v) -> str:
    """A euro amount as a report cell. None prints as an em dash, never 0."""
    if v is None:
        return "—"
    if abs(v) >= 1e6:
        return "%.2fM" % (v / 1e6)
    return "%.0fK" % (v / 1e3)


def fmt_pct(v) -> str:
    return "—" if v is None else "%.0f%%" % v


def _selftest() -> None:
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
    for raw, want in fmt_cases.items():
        got = fmt_money(raw)
        assert got == want, f"fmt_money({raw!r}) -> {got!r}, wanted {want!r}"

    for raw, want in {72.0: "72%", 0.0: "0%", 95.5: "96%", None: "—"}.items():
        got = fmt_pct(raw)
        assert got == want, f"fmt_pct({raw!r}) -> {got!r}, wanted {want!r}"

    print("ffcore.parse self-test OK "
          f"({len(cases_money) + len(cases_ratio) + 5 + len(fmt_cases) + 4} "
          "cases)")


if __name__ == "__main__":
    _selftest()
