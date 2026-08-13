"""
xi.py — record the XI you actually fielded, by naming who you benched.

    python src/xi.py                 # log today's XI
    python src/xi.py --selftest

You own 12 players and field 11, so the shortest honest input is the bench:

    inputs/bench.txt
        pepelu

Everything else is derived. Your squad comes from the ledger, so the XI is
squad minus bench — you never retype eleven names, and a name that is no
longer yours is caught instead of silently logged.

Why a bench file and not an XI file: the XI is the longer list and the one
that changes most, so typing it is where mistakes and staleness live. The
bench is one or two names. When you sell a player the bench file usually
stays correct, and when it doesn't, this refuses to guess.

Appends one immutable row per run to data/decisions/xi_fielded.csv. Nothing
is ever edited: a second run on the same day appends a second row, and the
latest row for a date is the XI that stood. That is what makes the log
scoreable later — the app never shows you your own history.

Not xi_log.csv: that file has a different schema (formation, index_total) and
holds report.py's best-XI *suggestion*. What you were advised and what you
fielded are two different facts, and scoring needs both kept apart.
"""

from __future__ import annotations

import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ffcore.league import League  # noqa: E402
from ffcore.text import norm  # noqa: E402
from ffcore.tidy import DECISIONS, append_csv, input_path  # noqa: E402

FIELDS = ["logged_at", "n_xi", "xi", "bench", "warnings"]

# 11 on the pitch. Fewer means the app would have auto-filled someone and the
# log would not match what actually played.
XI_SIZE = 11


def read_bench(text: str) -> list[str]:
    """One name per line. '#' comments and blank lines ignored."""
    out = []
    for line in (text or "").splitlines():
        line = line.split("#")[0].strip()
        if line:
            out.append(line)
    return out


def pick_xi(squad: list[str], bench_names: list[str]):
    """(xi, benched, warnings) — squad minus bench, keyed by norm().

    Squad keys come from the ledger and bench names from your typing, so both
    sides are normalised before subtracting. A bench name you no longer own is
    a warning, never a silent drop: it means the file is stale and the XI
    below it cannot be trusted.
    """
    keys = {norm(s) for s in squad}
    warnings, benched = [], []

    for raw in bench_names:
        k = norm(raw)
        if k in keys:
            keys.discard(k)
            benched.append(k)
        else:
            warnings.append("benched '%s' is not in your squad" % raw)

    xi = sorted(keys)
    if len(xi) != XI_SIZE:
        warnings.append("%d players for an XI of %d — bench %d more"
                        % (len(xi), XI_SIZE, len(xi) - XI_SIZE))
    return xi, sorted(benched), warnings


def main() -> None:
    lg = League.load(with_market=False)
    squad = lg.squad(lg.cfg.me)

    path = input_path("bench.txt")
    bench_names = read_bench(path.read_text(encoding="utf-8")
                             if path.exists() else "")

    xi, benched, warnings = pick_xi(squad, bench_names)

    append_csv(DECISIONS / "xi_fielded.csv", [{
        "logged_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H%MZ"),
        "n_xi": len(xi),
        "xi": "|".join(xi),
        "bench": "|".join(benched),
        "warnings": "; ".join(warnings),
    }], FIELDS)

    print("logged XI of %d (bench: %s)" % (len(xi), ", ".join(benched) or "—"))
    for w in warnings:
        print("  warning:", w)


def _selftest() -> None:
    squad = ["Alvaro Fernandez", "Beñat Turrientes", "Carl Starfelt",
             "Dani Lorenzo", "Igor Zubeldia", "Iñigo Ruiz de Galarreta",
             "Iñigo Vicente", "Jon Moncayola", "Omar El Hilali", "Pepelu",
             "Robin Le Normand", "Ruben Garcia"]

    # The real case: 12 owned, bench one, field eleven.
    xi, benched, warns = pick_xi(squad, ["pepelu"])
    assert len(xi) == 11, xi
    assert benched == ["pepelu"]
    assert not warns, warns
    assert "pepelu" not in xi

    # Accents survive the round trip whichever way you spell it. Typing the
    # accent is the case that separates norm() from a plain .lower(): the
    # ledger key is folded, so an unfolded 'ñ' would miss and the player would
    # be logged as fielded when you benched him.
    assert norm("Beñat Turrientes") in xi
    for typed in ("Benat Turrientes", "Beñat Turrientes"):
        xi2, benched2, warns2 = pick_xi(squad, [typed])
        assert benched2 == [norm("Beñat Turrientes")], (typed, benched2)
        assert not warns2, (typed, warns2)

    # A stale bench name is reported, not absorbed.
    xi3, benched3, warns3 = pick_xi(squad, ["someone i sold"])
    assert benched3 == []
    assert any("not in your squad" in w for w in warns3), warns3
    # ...and it leaves 12, so the count warning fires too.
    assert any("bench 1 more" in w for w in warns3), warns3

    # Benching nobody is wrong and says so.
    _, _, warns4 = pick_xi(squad, [])
    assert any("12 players for an XI of 11" in w for w in warns4), warns4

    # Comments and blanks are not player names.
    assert read_bench("pepelu\n\n# injured\n  \n") == ["pepelu"]
    assert read_bench("pepelu  # sitting\n") == ["pepelu"]
    assert read_bench("") == []

    print("xi self-test OK (13 cases)")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        raise SystemExit(0)
    main()
