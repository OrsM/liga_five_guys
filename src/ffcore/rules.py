"""The game's rules: positions, the formations the app allows, and what a
line-up role is worth in minutes. No data, no model: everything else may
import this."""
from __future__ import annotations

__all__ = ["SLOT", "SLOT_LABEL", "SLOT_MIN", "MAX_SLOT", "FREE_FORMATIONS",
           "MATCH_LEN", "minutes_played"]

SLOT = {
    "portero": "POR",
    "defensa": "DEF",
    "mediocampista": "MED",
    "centrocampista": "MED",
    "delantero": "DEL",
}

SLOT_LABEL = {"POR": "portero", "DEF": "defensa", "MED": "mediocampista",
              "DEL": "delantero"}

SLOT_MIN = {"POR": 1, "DEF": 3, "MED": 3, "DEL": 1}

MAX_SLOT = {"POR": 1, "DEF": 5, "MED": 5, "DEL": 3}

FREE_FORMATIONS = [(5, 4, 1), (5, 3, 2), (4, 5, 1), (4, 4, 2), (4, 3, 3),
                   (3, 5, 2), (3, 4, 3)]

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
    assert SLOT["centrocampista"] == SLOT["mediocampista"] == "MED"
    assert all(1 + d + m + f == 11 for d, m, f in FREE_FORMATIONS)
    assert all(SLOT_MIN[s] <= MAX_SLOT[s] for s in MAX_SLOT)
    print("ffcore.rules self-test OK")


if __name__ == "__main__":
    _selftest()
