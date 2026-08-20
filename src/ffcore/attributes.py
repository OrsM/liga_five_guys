"""
ffcore.attributes — one resolved fact per player, from however many sources
speak to it.

    fit = resolve_fitness(status_by_key, player_status_by_key)
    fit["isaac romero"].state    -> "doubt"
    fit["isaac romero"].agree    -> False
    fit["isaac romero"].app_state -> "ok"

PILOT: fitness. Two independent readers exist for the same fact and, until
now, only one was used. `status` (futbolfantasy's editorial "Estado
físico"/"Sancionados"/"No disponibles" panels) drove report.py's whole
Fitness section. `player_status` — the app's OWN operator-stated
availability, from api_market/api_teams — was parsed, written to two CSVs,
and read by nothing. Not blended, not cross-checked: if the two disagreed,
nothing would have noticed.

WHY A RESOLVER AND NOT A THIRD COLUMN. Bolting `player_status` onto the
report as a second, separate line would still leave every caller comparing
the two by hand. This is the general shape instead: each attribute has ONE
function that takes every source's reading and returns ONE resolved fact per
player, with disagreement KEPT rather than picked — a caller that wants to
flag a mismatch can, and a caller that just wants the best single answer
still gets one. Adding a third fitness source later means adding it to this
one function, not re-deriving the join everywhere the fact is used.

THE VOCABULARY IS FF's, not a merged one. It is the finer of the two — the
app has no "unavailable" state of its own — and unifying on the coarser
vocabulary would silently drop a distinction FF's readers can make and the
app's cannot.
"""

from __future__ import annotations

from typing import NamedTuple

__all__ = ["Fitness", "resolve_fitness"]

# The app's own words for the states FF's panel also has a word for, so
# "doubt" (FF) and "doubtful" (app) compare as agreement rather than as a
# false conflict, and "ok" (app) lines up with "" (FF's fine/not-stated).
_APP_TO_FF = {"doubtful": "doubt", "ok": ""}


class Fitness(NamedTuple):
    """One player's resolved fitness read.

    `state` is FF's word for it — "" means fine, otherwise one of
    doubt/injured/suspended/unavailable, the same vocabulary
    ffcore.score.Scored.status already uses, so callers do not need a
    translation step.

    `agree` is False exactly when the app HAS a reading and it does not
    match, once both are on one vocabulary. True when the app has no
    reading at all — silence is not disagreement, it is coverage the app
    does not have (it does not cover every player FF's panel does).

    `app_state` is what the app said, RAW and unaligned, kept even on
    agreement so a caller that wants the app's own word for it (rather than
    FF's) still has it. None means the app has no reading for him.
    """
    state: str
    agree: bool
    app_state: str | None


def resolve_fitness(status_by_key: dict[str, str],
                    player_status_by_key: dict[str, str]) -> dict[str, Fitness]:
    """{player key: Fitness}, over every key `status_by_key` names.

    `status_by_key` is expected to be exhaustive over its population (every
    squad member gets an entry, "" for fine, per ffcore.score.Scored.status)
    — this resolves fitness for THAT population, not for whoever the app
    happens to also cover. `player_status_by_key` may be missing a player
    entirely; that reads as "the app has no opinion," never as "the app
    says fine."
    """
    out = {}
    for key, ff in status_by_key.items():
        raw = player_status_by_key.get(key)
        aligned = _APP_TO_FF.get(raw, raw) if raw is not None else None
        agree = aligned is None or aligned == ff
        out[key] = Fitness(ff, agree, raw)
    return out


def _selftest() -> None:
    # -- agreement, on FF's vocabulary and on the app's ---------------------
    fit = resolve_fitness({"a": "", "b": "doubt", "c": "injured"},
                          {"a": "ok", "b": "doubtful", "c": "injured"})
    assert fit["a"] == ("", True, "ok"), fit["a"]
    assert fit["b"] == ("doubt", True, "doubtful"), fit["b"]
    assert fit["c"] == ("injured", True, "injured"), fit["c"]

    # -- a real disagreement, kept rather than picked ------------------------
    fit = resolve_fitness({"d": ""}, {"d": "injured"})
    assert fit["d"] == ("", False, "injured"), fit["d"]
    fit = resolve_fitness({"e": "doubt"}, {"e": "ok"})
    assert fit["e"] == ("doubt", False, "ok"), fit["e"]

    # -- silence is not disagreement -----------------------------------------
    # The app does not cover every player FF's panel does; a player it says
    # nothing about must not read as a conflict, or as the app vouching for
    # him.
    fit = resolve_fitness({"f": "doubt"}, {})
    assert fit["f"] == ("doubt", True, None), fit["f"]

    # -- "unavailable" has no app equivalent, so it can only ever agree by --
    # -- the app staying silent, never by the app confirming it -------------
    fit = resolve_fitness({"g": "unavailable"}, {"g": "ok"})
    assert fit["g"].agree is False, fit["g"]

    # -- an empty population resolves to nothing, not an error --------------
    assert resolve_fitness({}, {"h": "injured"}) == {}

    print("ffcore.attributes self-test OK (%d cases)" % 8)


if __name__ == "__main__":
    _selftest()
