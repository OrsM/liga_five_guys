"""
ffcore.render — turning keys back into something a person reads.

The join key is lossy on purpose (see ffcore.text): it folds accents, drops
apostrophes and lowercases everything, because that is what makes "N'Diaye"
and "NDiaye" the same player. Every report then has to undo as much of that as
it can before printing, and until this module existed report.py was the only
one that could — the rest either printed raw keys or imported a 2,300-line
report generator to borrow one function.

Display only. Nothing here is ever a key, and nothing here is reversible: a
name that came back from norm() has already lost its accents and no amount of
casing will bring them back.
"""

from __future__ import annotations

__all__ = ["PARTICLES", "title_name"]

# Name particles that stay lowercase when title-casing a folded name.
# "le"/"el"/"la" are deliberately absent: Le Normand and El Hilali are far
# more common in this league than "de la Fuente" losing its lowercase.
PARTICLES = {"de", "del", "van", "von", "der", "den", "di", "da", "dos",
             "do", "y", "bin", "ibn", "ter"}


def title_name(s: str) -> str:
    """market.csv hands us folded names; make them readable again.

    Accents survive (they're in the source string), particles stay lowercase,
    hyphenated parts are capitalised on both sides.

    A string that is ALREADY cased is left exactly as it is. That is the
    guard that lets this be called on anything: the app's own spelling, a
    manager's handle, a name that has been through here once already.
    """
    s = (s or "").strip()
    if not s or s != s.lower():
        return s  # already cased — leave it alone
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
    # A leading particle is still the start of the name, so it is capitalised.
    assert title_name("de la fuente") == "De La Fuente"
    assert title_name("ruiz-de galarreta") == "Ruiz-De Galarreta"
    assert title_name("Vini Jr.") == "Vini Jr."      # already cased
    assert title_name("SusoGattuso") == "SusoGattuso"
    assert title_name("") == "" and title_name(None) == ""
    # Idempotent: the report calls it on rows that may have been through it.
    once = title_name("omar el hilali")
    assert once == "Omar El Hilali" and title_name(once) == once
    print("ffcore.render self-test OK (9 cases)")


if __name__ == "__main__":
    _selftest()
