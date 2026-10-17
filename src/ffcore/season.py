
from __future__ import annotations

from dataclasses import dataclass, field

from ffcore.rules import MAX_SLOT, SHAPES, XI_SIZE, Squads

__all__ = ["LeagueState"]


@dataclass
class LeagueState:
    """The squads, the jornadas left and the points banked."""
    squads: Squads
    jornadas: list[int]
    me: str = ""
    carried: dict[str, float] = field(default_factory=dict)


def _selftest() -> None:
    assert all(sum(s.values()) == XI_SIZE for s in SHAPES), SHAPES
    assert {"POR": 1, "DEF": 4, "MED": 4, "DEL": 2} in SHAPES
    assert {"POR": 1, "DEF": 5, "MED": 4, "DEL": 1} in SHAPES
    assert not any(s["DEF"] > MAX_SLOT["DEF"] for s in SHAPES)

    print("ffcore.season self-test OK")


if __name__ == "__main__":
    _selftest()
