
from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from functools import lru_cache
from typing import Any

__all__ = ["norm", "tokens", "resolve", "index_by"]

_TO_SPACE = str.maketrans({".": " ", "-": " ", "_": " ", "/": " ", ",": " "})
_DELETE = str.maketrans({"'": "", "\u2019": "", "`": "", "\u00b4": ""})

_WS = re.compile(r"\s+")


@lru_cache(maxsize=None)
def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower().translate(_DELETE).translate(_TO_SPACE)
    return _WS.sub(" ", s).strip()


def norm(s: object) -> str:
    if s is None:
        return ""
    return _norm(s if type(s) is str else str(s))


def tokens(s: object) -> list[str]:
    return [t for t in norm(s).split() if len(t) > 1]


def index_by(rows: Iterable[Mapping[str, Any]], key: str = "name"
             ) -> dict[str, Mapping[str, Any]]:
    return {norm(r.get(key)): r for r in rows if norm(r.get(key))}


def resolve(query: object, rows: Sequence[Mapping[str, Any]], key: str = "name",
            index: Mapping[str, Mapping[str, Any]] | None = None
            ) -> tuple[Mapping[str, Any] | None, list[Mapping[str, Any]]]:
    """The row whose key the query names, else (None, the rows it might
    mean): exact (accents, case and punctuation never count), then as whole
    words, then every word of it, then one name inside the other."""
    q = norm(query)
    if not q:
        return None, []

    idx = index if index is not None else index_by(rows, key)
    if q in idx:
        return idx[q], []

    toks = tokens(query)
    stages = [lambda c: (" %s " % q) in (" %s " % c),
              lambda c: bool(toks) and all(t in c for t in toks),
              lambda c: bool(c) and (c in q or q in c)]
    for named in stages:
        hits = [r for r in rows if named(norm(r.get(key)))]
        if hits:
            return (hits[0], []) if len(hits) == 1 else (None, hits)
    return None, []


def _selftest() -> None:

    rows = [{"name": "Isaac Romero"}, {"name": "Cristian Romero"},
            {"name": "Carlos Romero"}, {"name": "Lamine Yamal"},
            {"name": "Álvaro Fernández"}]

    assert (resolve("Lamine Yamal", rows)[0] or {})["name"] == "Lamine Yamal"
    assert (resolve("lamine yamal", rows)[0] or {})["name"] == "Lamine Yamal"
    assert (resolve("Alvaro Fernandez", rows)[0] or {})["name"] == "Álvaro Fernández"

    assert (resolve("Yamal", rows)[0] or {})["name"] == "Lamine Yamal"

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

    teams = [{"name": t} for t in ("Celta Vigo", "Real Betis", "Real Madrid", "atletico")]
    def club(q: str) -> str | None:
        row, _ = resolve(q, teams)
        return row["name"] if row else None
    assert club("Celta Vigo") == "Celta Vigo" and club("Celta") == "Celta Vigo"
    assert club("Betis") == "Real Betis"
    assert club("Real") is None and club("Sevilla") is None and club("") is None
    assert club("Atlético de Madrid") == "atletico", "a name that contains the candidate's"

    print("ffcore.text self-test OK")


if __name__ == "__main__":
    _selftest()
