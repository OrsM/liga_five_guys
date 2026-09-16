
from __future__ import annotations

__all__ = ["PARTICLES", "title_name"]

PARTICLES = {"de", "del", "van", "von", "der", "den", "di", "da", "dos",
             "do", "y", "bin", "ibn", "ter"}


def title_name(s: str) -> str:
    s = (s or "").strip()
    if not s or s != s.lower():
        return s
    words = []
    for i, w in enumerate(s.split()):
        if i and w in PARTICLES:
            words.append(w)
        else:
            words.append("-".join(p[:1].upper() + p[1:] for p in w.split("-")))
    return " ".join(words)


def _selftest() -> None:
    assert title_name("marcos alonso") == "Marcos Alonso"
    assert title_name("nico van gaal") == "Nico van Gaal"
    assert title_name("de la fuente") == "De La Fuente"
    assert title_name("ruiz-de galarreta") == "Ruiz-De Galarreta"
    assert title_name("Vini Jr.") == "Vini Jr."
    assert title_name("SusoGattuso") == "SusoGattuso"
    assert title_name("") == "" and title_name(None) == ""
    once = title_name("omar el hilali")
    assert once == "Omar El Hilali" and title_name(once) == once
    print("ffcore.render self-test OK (9 cases)")


if __name__ == "__main__":
    _selftest()
