"""The game's rules: positions, the formations the app allows, and what a
line-up role is worth in minutes. No data, no model: everything else may
import this."""
from __future__ import annotations

__all__ = ["POSITIONS", "slot", "MAX_SLOT", "FREE_FORMATIONS", "MATCH_LEN",
           "minutes_played"]

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

# The most of each position any formation fields.
MAX_SLOT = {"POR": 1, **dict(zip(POSITIONS[1:], map(max, zip(*FREE_FORMATIONS))))}


def slot(raw) -> str:
    """A position as the game writes it (portero, Centrocampista, MED...)
    as one of POSITIONS, or "" if it is none of them."""
    t = (raw or "").strip()
    return t if t in POSITIONS else _SLOT.get(t.lower(), "")

MATCH_LEN = 90.0


def minutes_played(role: str, raw_minute, match_len: float = MATCH_LEN) -> float:
    raw = (raw_minute or "").strip()
    if role == "starter":
        mins = float(raw) if raw else match_len
    elif role == "sub":
        mins = (match_len - float(raw)) if raw else 0.0
    else:
        return 0.0
    return max(0.0, mins)


def _selftest() -> None:
    assert minutes_played("starter", "") == 90.0
    assert minutes_played("starter", "64") == 64.0
    assert minutes_played("sub", "") == 0.0
    assert minutes_played("sub", "64") == 26.0
    assert minutes_played("coach", "") == 0.0
    assert minutes_played("starter", "0") == 0.0
    assert slot("Centrocampista") == slot("mediocampista") == slot("MED") == "MED"
    assert slot(None) == slot("") == slot("utillero") == "", "unknown is empty, never guessed"
    assert POSITIONS == ("POR", "DEF", "MED", "DEL")
    assert all(1 + d + m + f == 11 for d, m, f in FREE_FORMATIONS)
    assert MAX_SLOT == {"POR": 1, "DEF": 5, "MED": 5, "DEL": 3}, MAX_SLOT
    print("ffcore.rules self-test OK")


if __name__ == "__main__":
    _selftest()
