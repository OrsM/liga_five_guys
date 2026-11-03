
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

__all__ = ["norm", "tokens", "stems_meet"]

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


def stems_meet(app_name: object, app_full: object, ff_name: object) -> int:
    """How many first-four-letter stems the app's names and futbolfantasy's
    share ("Jonny Otto" and "Jonny Castro": 1)."""
    theirs = {w[:4] for w in tokens(app_name)} | {w[:4] for w in tokens(app_full)}
    return len(theirs & {w[:4] for w in tokens(ff_name)})


def _selftest() -> None:

    assert tokens("C. Romero") == ["romero"]
    assert norm("Álvaro  Fernández-O'Neil") == "alvaro fernandez oneil"
    assert stems_meet("Jonny Otto", "", "Jonny Castro") == 1

    print("ffcore.text self-test OK")


if __name__ == "__main__":
    _selftest()
