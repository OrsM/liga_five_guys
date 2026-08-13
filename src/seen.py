"""
seen.py — turn a messy OCR'd list of names into a set of player keys.

    python src/seen.py --selftest

The watchlist ranks everyone nobody owns, but the app only deals a limited
slate each cycle, so most of it isn't buyable today. Reading the slate off
your phone with OCR closes that gap: paste the names in, and the watchlist
marks which ones are actually on offer.

Names only — never prices. Values are already scraped to the euro, and the
minimum legal bid *is* the market value, so the only thing OCR needs to tell
us is *who*. An OCR'd price could silently disagree with a correct one we
already hold, and vision models are known to return a plausible number rather
than fail.

OCR output is expected to be bad. ffcore.text.resolve() does exact, then
substring, then all-tokens matching, and hands back candidates rather than
guessing between them — a wrong player costs real money, so ambiguity is
reported, never resolved silently.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ffcore.text import norm, resolve  # noqa: E402


def read_names(text: str) -> list[str]:
    """One name per line, or comma-separated. '#' comments ignored.

    Both shapes because iOS Live Text copies a screenshot column as lines,
    while the workflow input arrives as one comma-separated string.

    Comments are dropped per line BEFORE splitting on commas. A comment
    holding a date — '# slate 13/08, 15:00' — would otherwise be split into
    two fragments and reported as two unmatched players.
    """
    out = []
    for line in (text or "").splitlines():
        line = line.split("#")[0]
        for part in line.split(","):
            part = part.strip()
            if part:
                out.append(part)
    return out


def match(names: list[str], players: dict) -> tuple[set, list, list]:
    """(keys on offer, unresolved, ambiguous).

    `players` is common.load_players(): norm(name) -> record. Unresolved and
    ambiguous names are returned so the report can show them — a name OCR
    mangled past recognition is a player you might otherwise think isn't on
    offer, which is the opposite of what the list is for.
    """
    rows = list(players.values())
    keys, unresolved, ambiguous = set(), [], []

    for raw in names:
        rec, candidates = resolve(raw, rows)
        if rec:
            keys.add(norm(rec.get("name")))
        elif candidates:
            ambiguous.append((raw, [c.get("name") for c in candidates[:5]]))
        else:
            unresolved.append(raw)

    return keys, unresolved, ambiguous


def _selftest() -> None:
    players = {
        norm("Álvaro Valles"): {"name": "Álvaro Valles", "team": "Betis"},
        norm("Iñigo Ruiz de Galarreta"): {"name": "Iñigo Ruiz de Galarreta",
                                          "team": "Mallorca"},
        norm("Stole Dimitrievski"): {"name": "Stole Dimitrievski",
                                     "team": "Valencia"},
        norm("Dani Martínez"): {"name": "Dani Martínez", "team": "Rayo"},
        norm("Dani Lorenzo"): {"name": "Dani Lorenzo", "team": "Betis"},
    }

    # OCR drops accents and loses short words. Both must still resolve.
    keys, unres, amb = match(
        ["Alvaro Valles", "Inigo Ruiz Galarreta", "Stole Dimitrievski"],
        players)
    assert keys == {norm("Álvaro Valles"), norm("Iñigo Ruiz de Galarreta"),
                    norm("Stole Dimitrievski")}, keys
    assert not unres and not amb, (unres, amb)

    # A first name matching two players is ambiguous, not a coin flip.
    keys2, unres2, amb2 = match(["Dani"], players)
    assert keys2 == set(), keys2
    assert unres2 == []
    assert len(amb2) == 1 and amb2[0][0] == "Dani"
    assert sorted(amb2[0][1]) == ["Dani Lorenzo", "Dani Martínez"], amb2

    # Mangled past recognition: reported, never silently absent.
    keys3, unres3, amb3 = match(["Xyzzy Nobody"], players)
    assert keys3 == set() and amb3 == []
    assert unres3 == ["Xyzzy Nobody"]

    # Case and stray punctuation from OCR don't matter.
    keys4, _, _ = match(["ÁLVARO VALLES", "stole dimitrievski."], players)
    assert len(keys4) == 2, keys4

    # Both input shapes: newline-separated and comma-separated.
    assert read_names("Alvaro Valles\nStole Dimitrievski") == \
        ["Alvaro Valles", "Stole Dimitrievski"]
    assert read_names("Alvaro Valles, Stole Dimitrievski") == \
        ["Alvaro Valles", "Stole Dimitrievski"]
    assert read_names("Alvaro Valles  # 31.9M\n\n# slate 14:00\n") == \
        ["Alvaro Valles"]
    # A comma inside a comment is not a name separator: the comment goes
    # first, or its date fragments get reported as missing players.
    assert read_names("# pasted 13/08, 15:00\nAlvaro Valles") == \
        ["Alvaro Valles"]
    assert read_names("") == []

    print("seen self-test OK (13 cases)")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        raise SystemExit(0)
    print(__doc__.strip())
