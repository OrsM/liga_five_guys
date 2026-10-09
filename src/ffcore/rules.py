"""The game's rules: positions, the formations the app allows, and what a
line-up role is worth in minutes. No data, no model: everything else may
import this."""
from __future__ import annotations

__all__ = ["POSITIONS", "slot", "MAX_SLOT", "FREE_FORMATIONS", "SHAPES", "shortfall",
           "MATCH_LEN", "minutes_played"]

POSITIONS = ("POR", "DEF", "MED", "DEL")

_SLOT = {
    "portero": "POR",
    "defensa": "DEF",
    "mediocampista": "MED",
    "centrocampista": "MED",
    "delantero": "DEL",
}

FREE_FORMATIONS = [(5, 4, 1), (5, 3, 2), (4, 5, 1), (4, 4, 2), (4, 3, 3),
                   (3, 5, 2), (3, 4, 3)]

# Each formation as the players it fields by position.
SHAPES = [dict(zip(POSITIONS, (1, d, m, f))) for d, m, f in FREE_FORMATIONS]

# The most of each position any formation fields.
MAX_SLOT = {"POR": 1, **dict(zip(POSITIONS[1:], map(max, zip(*FREE_FORMATIONS))))}


def shortfall(squad: dict[str, str]) -> dict[str, int]:
    """The players a squad (player: position) is short of the nearest
    formation, by position: empty when it can field one. On a tie, the
    first formation in FREE_FORMATIONS."""
    have = {p: 0 for p in POSITIONS}
    for p in squad.values():
        have[p] = have.get(p, 0) + 1
    return min(({p: n - have[p] for p, n in shape.items() if n > have[p]} for shape in SHAPES),
               key=lambda short: sum(short.values()))


def slot(raw) -> str:
    """A position as the game writes it (portero, Centrocampista, MED...)
    as one of POSITIONS, or "" if it is none of them."""
    t = (raw or "").strip()
    return t if t in POSITIONS else _SLOT.get(t.lower(), "")

MATCH_LEN = 90.0


def minutes_played(role: str, minute: float | None, match_len: float = MATCH_LEN) -> float:
    """Minutes a starter or sub played, from the minute he came off or on
    (None: the whole match for a starter, none for a sub)."""
    if role == "starter":
        mins = match_len if minute is None else minute
    elif role == "sub":
        mins = 0.0 if minute is None else match_len - minute
    else:
        return 0.0
    return max(0.0, mins)


def _selftest() -> None:
    assert minutes_played("starter", None) == 90.0
    assert minutes_played("starter", 64.0) == 64.0
    assert minutes_played("sub", None) == 0.0
    assert minutes_played("sub", 64.0) == 26.0
    assert minutes_played("coach", None) == 0.0
    assert minutes_played("starter", 0.0) == 0.0
    assert slot("Centrocampista") == slot("mediocampista") == slot("MED") == "MED"
    assert slot(None) == slot("") == slot("utillero") == "", "unknown is empty, never guessed"
    assert POSITIONS == ("POR", "DEF", "MED", "DEL")
    assert all(1 + d + m + f == 11 for d, m, f in FREE_FORMATIONS)
    assert MAX_SLOT == {"POR": 1, "DEF": 5, "MED": 5, "DEL": 3}, MAX_SLOT
    four_four_two = {"k": "POR", **{f"d{i}": "DEF" for i in range(4)},
                     **{f"m{i}": "MED" for i in range(4)}, "f1": "DEL", "f2": "DEL"}
    assert shortfall(four_four_two) == {}
    assert shortfall({k: p for k, p in four_four_two.items() if k != "k"}) == {"POR": 1}
    two_backs = {"k": "POR", "d0": "DEF", "d1": "DEF", **{f"m{i}": "MED" for i in range(5)},
                 "f0": "DEL", "f1": "DEL", "f2": "DEL"}
    assert shortfall(two_backs) == {"DEF": 1}, "the nearest formation, not the first"
    assert shortfall({}) == {"POR": 1, "DEF": 5, "MED": 4, "DEL": 1}, \
        "an empty squad: the first of the formations short of all eleven"
    print("ffcore.rules self-test OK")


if __name__ == "__main__":
    _selftest()
