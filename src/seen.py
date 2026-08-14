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

Ownership settles some of it for free (issue #26): the app deals free agents,
so a candidate somebody in the league already holds is not the player on offer.
Pass `lg.owner` to match() and a bare "Dani" with one Dani owned has exactly
one player it can be. Every such substitution is returned separately and
printed in the report, because this one is a guess and the ledger it leans on
can be out of date.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ffcore.text import norm, resolve  # noqa: E402
from ffcore.tidy import input_path  # noqa: E402


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


def match(names: list[str], players: dict,
          owner: dict | None = None) -> tuple[set, list, list, list]:
    """(keys on offer, unresolved, ambiguous, resolved by ownership).

    `players` is common.load_players(): norm(name) -> record. Unresolved and
    ambiguous names are returned so the report can show them — a name OCR
    mangled past recognition is a player you might otherwise think isn't on
    offer, which is the opposite of what the list is for.

    `owner` is ffcore.league's {key: manager}, and it settles ambiguity the
    string cannot (issue #26): a player the app is dealing is a player nobody
    in the league holds, so an owned candidate is not the one on offer. With
    one Dani owned, a bare "Dani" has exactly one player it can be.

    THE PRUNE IS ONLY AS GOOD AS THE LEDGER. Unlike the same trick inside
    replay(), where ownership is derived from the very rows being read, this
    leans on transactions.csv being up to date: a rival's unlogged purchase
    makes an owned player look free, and it can then be chosen over the right
    one. So every substitution is returned in `resolved` for the report to
    print, and an exact match is never overridden — being owned is a fact worth
    reporting, not grounds for picking somebody else.
    """
    rows = list(players.values())
    keys, unresolved, ambiguous, resolved = set(), [], [], []

    for raw in names:
        rec, candidates = resolve(raw, rows)
        if not rec and candidates and owner:
            free = [c for c in candidates if norm(c.get("name")) not in owner]
            if len(free) == 1:
                rec = free[0]
                resolved.append(
                    "**%s** → %s — the only one of %d candidates nobody owns"
                    % (raw, rec.get("name"), len(candidates)))
        if rec:
            keys.add(norm(rec.get("name")))
        elif candidates:
            ambiguous.append((raw, [c.get("name") for c in candidates[:5]]))
        else:
            unresolved.append(raw)

    return keys, unresolved, ambiguous, resolved


def read_slate(players, owner=None) -> tuple[set, list, list, list]:
    """(keys on offer, unresolved, ambiguous, resolved) from inputs/seen.txt.

    Absent file is the normal case — you only paste a slate when deciding, and
    every consumer treats an empty set as "no slate pasted" rather than as "an
    empty market". Lived in squads.py; here so report.py and rivals.py read
    the same slate rather than each growing a copy of the same six lines.

    `players` is any {key: record} whose records carry a "name", so it takes
    common.load_players() or a market row index equally. Pass `lg.owner` to get
    the ownership prune described in match().
    """
    path = input_path("seen.txt")
    if not path.exists():
        return set(), [], [], []
    return match(read_names(path.read_text(encoding="utf-8")), players, owner)


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
    keys, unres, amb, _ = match(
        ["Alvaro Valles", "Inigo Ruiz Galarreta", "Stole Dimitrievski"],
        players)
    assert keys == {norm("Álvaro Valles"), norm("Iñigo Ruiz de Galarreta"),
                    norm("Stole Dimitrievski")}, keys
    assert not unres and not amb, (unres, amb)

    # A first name matching two players is ambiguous, not a coin flip.
    keys2, unres2, amb2, res2 = match(["Dani"], players)
    assert keys2 == set(), keys2
    assert unres2 == []
    assert len(amb2) == 1 and amb2[0][0] == "Dani"
    assert sorted(amb2[0][1]) == ["Dani Lorenzo", "Dani Martínez"], amb2
    assert res2 == [], res2

    # Issue #26, the slate side: a player on offer is a player nobody owns, so
    # an owned candidate cannot be the one being dealt. One Dani owned leaves
    # exactly one it can be.
    owner = {norm("Dani Lorenzo"): "alice"}
    keys5, _, amb5, res5 = match(["Dani"], players, owner)
    assert keys5 == {norm("Dani Martínez")}, keys5
    assert amb5 == [], amb5
    assert len(res5) == 1 and "Dani Martínez" in res5[0], res5

    # Both owned: nothing survives the prune, so it stays ambiguous rather
    # than resolving to whichever happened to be listed first.
    both = {norm("Dani Lorenzo"): "alice", norm("Dani Martínez"): "bob"}
    keys6, _, amb6, res6 = match(["Dani"], players, both)
    assert keys6 == set() and res6 == [], (keys6, res6)
    assert len(amb6) == 1, amb6

    # An unambiguous name is not touched by ownership: an owned player on the
    # slate is worth reporting as owned, which the slate table does.
    keys7, _, _, res7 = match(["Alvaro Valles"], players,
                              {norm("Álvaro Valles"): "alice"})
    assert keys7 == {norm("Álvaro Valles")}, keys7
    assert res7 == [], res7

    # Mangled past recognition: reported, never silently absent.
    keys3, unres3, amb3, _ = match(["Xyzzy Nobody"], players)
    assert keys3 == set() and amb3 == []
    assert unres3 == ["Xyzzy Nobody"]

    # Case and stray punctuation from OCR don't matter.
    keys4, _, _, _ = match(["ÁLVARO VALLES", "stole dimitrievski."], players)
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

    print("seen self-test OK (22 cases)")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        raise SystemExit(0)
    print(__doc__.strip())
