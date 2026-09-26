
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

__all__ = ["norm", "tokens", "resolve", "index_by", "match_one"]

_TO_SPACE = str.maketrans({".": " ", "-": " ", "_": " ", "/": " ", ",": " "})
_DELETE = str.maketrans({"'": "", "\u2019": "", "`": "", "\u00b4": ""})

_WS = re.compile(r"\s+")


@lru_cache(maxsize=None)
def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower().translate(_DELETE).translate(_TO_SPACE)
    return _WS.sub(" ", s).strip()


def norm(s) -> str:
    if s is None:
        return ""
    return _norm(s if type(s) is str else str(s))


def tokens(s) -> list[str]:
    return [t for t in norm(s).split() if len(t) > 1]


def index_by(rows, key="name") -> dict:
    return {norm(r.get(key)): r for r in rows if norm(r.get(key))}


def resolve(query, rows, key="name", index=None):
    q = norm(query)
    if not q:
        return None, []

    idx = index if index is not None else index_by(rows, key)
    if q in idx:
        return idx[q], []

    subs = [r for r in rows if (" %s " % q) in (" %s " % norm(r.get(key)))]
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


def match_one(side, candidates) -> str | None:
    q = norm(side)
    if not q:
        return None
    exact = [c for c in candidates if norm(c) == q]
    if exact:
        return exact[0]
    hits = [c for c in candidates if norm(c) and (norm(c) in q or q in norm(c))]
    return hits[0] if len(hits) == 1 else None



def _selftest() -> None:
    rows = [{"name": "Isaac Romero"}, {"name": "Cristian Romero"},
            {"name": "Carlos Romero"}, {"name": "Lamine Yamal"},
            {"name": "Álvaro Fernández"}]

    assert resolve("Lamine Yamal", rows)[0]["name"] == "Lamine Yamal"
    assert resolve("lamine yamal", rows)[0]["name"] == "Lamine Yamal"
    assert resolve("Alvaro Fernandez", rows)[0]["name"] == "Álvaro Fernández"

    assert resolve("Yamal", rows)[0]["name"] == "Lamine Yamal"

    row, cands = resolve("C. Romero", rows)
    assert row is None, row
    assert sorted(r["name"] for r in cands) == ["Carlos Romero",
                                                "Cristian Romero",
                                                "Isaac Romero"], cands

    row, cands = resolve("Romero", rows)
    assert row is None and len(cands) == 3, (row, cands)

    assert resolve("Haaland", rows) == (None, [])
    assert resolve("", rows) == (None, [])

    assert tokens("C. Romero") == ["romero"]
    assert index_by(rows)["lamine yamal"]["name"] == "Lamine Yamal"

    teams = ["Celta Vigo", "Real Betis", "Real Madrid"]
    assert match_one("Celta Vigo", teams) == "Celta Vigo"
    assert match_one("Celta", teams) == "Celta Vigo"
    assert match_one("Betis", teams) == "Real Betis"
    assert match_one("Real", teams) is None
    assert match_one("Sevilla", teams) is None
    assert match_one("", teams) is None

    print("ffcore.text self-test OK (19 cases)")


if __name__ == "__main__":
    _selftest()
