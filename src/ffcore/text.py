"""
ffcore.text — the join key, and nothing else.

Name is the only join this project has: the app, futbolfantasy's market page,
its points page and your own ledger all spell players differently, and none of
them carry an id you can rely on across sources.

Before this module there were two normalisers. common.norm() stripped dots,
hyphens and apostrophes; the old fold() in report/offers/bids stripped only
accents. So:

    "N'Diaye"       norm -> ndiaye        fold -> n'diaye
    "C. Dominguez"  norm -> c dominguez   fold -> c. dominguez

squads.py keyed ownership with the first and offers.py matched with the
second, which means any name carrying an apostrophe or an initial silently
failed to join between the two halves of the repo. There is now one function.

    norm()     the key. Use it for every dict keyed by player.
    tokens()   norm() split into words worth matching on.
    resolve()  the shared fuzzy lookup: exact, then substring, then tokens.

norm() is lossy on purpose — it folds ñ to n and drops apostrophes. It is a
key, never a display string: keep the original text for anything a human
reads.
"""

from __future__ import annotations

import re
import unicodedata

__all__ = ["norm", "fold", "tokens", "resolve", "index_by"]

# Punctuation that separates words -> space. Apostrophes are deleted outright
# rather than spaced, so "N'Diaye" and "NDiaye" land on the same key.
_TO_SPACE = str.maketrans({".": " ", "-": " ", "_": " ", "/": " ", ",": " "})
_DELETE = str.maketrans({"'": "", "\u2019": "", "`": "", "\u00b4": ""})

_WS = re.compile(r"\s+")


def norm(s) -> str:
    """Accent-insensitive, case-insensitive, punctuation-insensitive key."""
    if s is None:
        return ""
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower().translate(_DELETE).translate(_TO_SPACE)
    return _WS.sub(" ", s).strip()


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


def resolve(query, rows, key="name"):
    """Find one row for a human-typed name.

    Returns (row, candidates). Exactly one of them is meaningful:
      * (row, [])          resolved
      * (None, [a, b, c])  ambiguous — show these to the user
      * (None, [])         no match

    Three passes, narrowest first: exact key, substring, then all-tokens
    subset. Nothing here guesses between candidates; ambiguity is handed back
    for a human to settle, because a wrong player silently costs money.
    """
    q = norm(query)
    if not q:
        return None, []

    idx = index_by(rows, key)
    if q in idx:
        return idx[q], []

    subs = [r for r in rows if q in norm(r.get(key))]
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
