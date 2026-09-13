"""
ffcore.text — the join key, and nothing else.

Name is the only join this project has: sources spell players
differently and none carry an id reliable across all of them.

    norm()     the key. Use it for every dict keyed by player.
    tokens()   norm() split into words worth matching on.
    resolve()  the shared fuzzy lookup: exact, then substring, then tokens.

norm() is lossy on purpose — folds ñ to n, drops apostrophes. It's a
key, never a display string; keep the original text for anything a
human reads.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

__all__ = ["norm", "fold", "tokens", "resolve", "index_by"]

# Punctuation that separates words -> space. Apostrophes are deleted outright
# rather than spaced, so "N'Diaye" and "NDiaye" land on the same key.
_TO_SPACE = str.maketrans({".": " ", "-": " ", "_": " ", "/": " ", ",": " "})
_DELETE = str.maketrans({"'": "", "\u2019": "", "`": "", "\u00b4": ""})

_WS = re.compile(r"\s+")


# Memoised: this is the hottest function in the repo (a pure function of
# a string, called hundreds of thousands of times per report on a few
# thousand distinct names). Unbounded on purpose — the key space is
# names, bounded by the league, and every process here is a batch job.
@lru_cache(maxsize=None)
def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower().translate(_DELETE).translate(_TO_SPACE)
    return _WS.sub(" ", s).strip()


def norm(s) -> str:
    """Accent-insensitive, case-insensitive, punctuation-insensitive key."""
    if s is None:
        return ""
    return _norm(s if type(s) is str else str(s))


def tokens(s) -> list[str]:
    """Words worth matching on — single letters are dropped.

    The app abbreviates first names ("C. Dominguez"); the CSVs spell them out.
    A one-letter token can never disambiguate, so it is noise in a subset
    match and is discarded here rather than at every call site.
    """
    return [t for t in norm(s).split() if len(t) > 1]


def index_by(rows, key="name") -> dict:
    """{norm(row[key]): row}. Later rows win, matching dict semantics."""
    return {norm(r.get(key)): r for r in rows if norm(r.get(key))}


def _contains_words(haystack: str, needle: str) -> bool:
    """Is `needle` in `haystack` as whole words?

    A raw `in` matched across a word boundary: "c romero" is inside
    "isaa|c romero|", so the app's "C. Romero" resolved to Isaac Romero and
    reported no ambiguity. Both strings are already normalised to
    space-separated words, so padding with spaces is the whole test.
    """
    if not needle:
        return False
    return (" %s " % needle) in (" %s " % haystack)


def resolve(query, rows, key="name", index=None):
    """Find one row for a human-typed name.

    Returns (row, candidates). Exactly one of them is meaningful:
      * (row, [])          resolved
      * (None, [a, b, c])  ambiguous — show these to the user
      * (None, [])         no match

    Three passes, narrowest first: exact key, substring, then all-tokens
    subset. Nothing here guesses between candidates; ambiguity is handed back
    for a human to settle, because a wrong player silently costs money.

    `index`, if given, must be exactly `index_by(rows, key)` for these
    rows — pass it when calling resolve() on the same rows repeatedly, to
    avoid rebuilding an identical dict every call. Not checked here.
    """
    q = norm(query)
    if not q:
        return None, []

    idx = index if index is not None else index_by(rows, key)
    if q in idx:
        return idx[q], []

    subs = [r for r in rows if _contains_words(norm(r.get(key)), q)]
    if len(subs) == 1:
        return subs[0], []
    if subs:
        return None, subs

    toks = tokens(query)
    if toks:
        hits = [r for r in rows
                if all(t in norm(r.get(key)) for t in toks)]
        if len(hits) == 1:
            return hits[0], []
        if hits:
            return None, hits

    return None, []


# ---------------------------------------------------------------------------

def _selftest() -> None:
    rows = [{"name": "Isaac Romero"}, {"name": "Cristian Romero"},
            {"name": "Carlos Romero"}, {"name": "Lamine Yamal"},
            {"name": "Álvaro Fernández"}]

    # An exact name is the strongest evidence there is and nothing overrules it.
    assert resolve("Lamine Yamal", rows)[0]["name"] == "Lamine Yamal"
    assert resolve("lamine yamal", rows)[0]["name"] == "Lamine Yamal"
    assert resolve("Alvaro Fernandez", rows)[0]["name"] == "Álvaro Fernández"

    # A surname on its own is a substring match, and one man has it.
    assert resolve("Yamal", rows)[0]["name"] == "Lamine Yamal"

    # THE SUBSTRING PASS MUST NOT MATCH ACROSS A WORD BOUNDARY — "c romero"
    # is inside "isaa|c romero|", which once resolved the app's abbreviated
    # "C. Romero" straight to Isaac Romero with no ambiguity reported (a
    # real, costly mispriced purchase). tokens() drops the initial as
    # noise, so all three Romeros come back as candidates and nothing is
    # returned — refusing is correct here; a caller holding the price or
    # club can settle it.
    row, cands = resolve("C. Romero", rows)
    assert row is None, row
    assert sorted(r["name"] for r in cands) == ["Carlos Romero",
                                                "Cristian Romero",
                                                "Isaac Romero"], cands

    # Ambiguity is handed back, never guessed between.
    row, cands = resolve("Romero", rows)
    assert row is None and len(cands) == 3, (row, cands)

    # A query matching nothing is not a near miss.
    assert resolve("Haaland", rows) == (None, [])
    assert resolve("", rows) == (None, [])

    # tokens() drops single letters, which is why the initial cannot rescue
    # the match on its own.
    assert tokens("C. Romero") == ["romero"]
    assert index_by(rows)["lamine yamal"]["name"] == "Lamine Yamal"

    print("ffcore.text self-test OK (13 cases)")


if __name__ == "__main__":
    _selftest()
