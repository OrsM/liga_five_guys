
from __future__ import annotations

from dataclasses import dataclass, field

from ffcore.rules import MAX_SLOT, SHAPES, XI_SIZE, Squad, Squads

__all__ = ["LeagueState", "best_xi"]


def best_xi(squad: Squad, value: dict[str, float]) -> list[str]:
    by_slot: dict[str, list[tuple[float, str]]] = {}
    for k, slot in squad.items():
        by_slot.setdefault(slot, []).append((value.get(k, 0.0), k))
    for rows in by_slot.values():
        rows.sort(key=lambda vk: -vk[0])
    best = None
    for shape in SHAPES:
        if all(len(by_slot.get(slot, ())) >= n for slot, n in shape.items()):
            picked = [vk for slot, n in shape.items() for vk in by_slot[slot][:n]]
            total = sum(v for v, _k in picked)
            if best is None or total > best[0]:
                best = (total, [k for _v, k in picked])
    return best[1] if best else []


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

    squad = {"k": "POR", "d1": "DEF", "d2": "DEF", "d3": "DEF", "d4": "DEF",
             "d5": "DEF", "m1": "MED", "m2": "MED", "m3": "MED", "m4": "MED",
             "m5": "MED", "f1": "DEL", "f2": "DEL", "f3": "DEL"}
    val = {k: 1.0 for k in squad}
    val.update({"f1": 9.0, "f2": 9.0, "f3": 9.0})
    xi = best_xi(squad, val)
    assert len(xi) == XI_SIZE, xi
    assert {"f1", "f2", "f3"} <= set(xi), xi

    assert best_xi({"d1": "DEF"}, {"d1": 1.0}) == []

    print("ffcore.season self-test OK")


if __name__ == "__main__":
    _selftest()
