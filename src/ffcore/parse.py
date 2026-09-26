
from __future__ import annotations

import re

__all__ = ["money", "ratio", "pct100", "fmt_money", "fmt_pct", "grouped_sums"]

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


def grouped_sums(items, key_of, *value_fns) -> dict:
    sums: dict = {}
    for item in items:
        acc = sums.setdefault(key_of(item), [0.0] * len(value_fns))
        for i, fn in enumerate(value_fns):
            acc[i] += fn(item)
    return {k: tuple(v) for k, v in sums.items()}


def fmt_money(v) -> str:
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
