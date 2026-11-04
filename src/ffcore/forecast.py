"""The forecast's constants (schema.sql's forecast views read them through
model_constants, as k), and a by-hand forecast for the decision's tests."""
from __future__ import annotations

__all__ = ["Bootstrap", "Cell", "PerJornada", "expected_points", "SEED_POOL", "MIN_POOL", "PERSISTENT_SHARE",
           "SHRINK_K", "PICK_K", "NEUTRAL_START", "ABSENT_START", "FIXTURE_K"]

SHRINK_K = 16.0       # matches a prior of points per match is worth (forecast_ppm)
PICK_K = 4.0          # matches his listing is worth against his record of selection (forecast_picked)
NEUTRAL_START = 60.0  # the start % of a player listed with none, or flagged
ABSENT_START = 15.0   # the start % of a player not listed
FIXTURE_K = 10.0      # matches at the league's rate a club's goals for and against are pulled toward (club_rating)

SEED_POOL = (-1, -1, -1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1,
             1, 1, 1, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 3, 3, 3, 3, 3, 3, 3,
             4, 4, 4, 4, 4, 5, 5, 5, 6, 6, 6, 6, 8, 8, 8, 8, 9, 9, 10, 11,
             12, 13, 16)

MIN_POOL = 200

PERSISTENT_SHARE = 0.08


# A player's forecast for a jornada: (points if he plays, chance he plays).
Cell = tuple[float, float]
PerJornada = dict[int, dict[str, Cell]]


def expected_points(cell: Cell | None) -> float:
    """A forecast cell (points if he plays, chance he plays) as expected
    points; no cell is none."""
    return cell[0] * cell[1] if cell else 0.0


class Bootstrap:

    def __init__(self, per_jornada: PerJornada) -> None:
        self.per_jornada = per_jornada

    def expected(self, jornada: int) -> dict[str, float]:
        return {k: expected_points(cell)
                for k, cell in self.per_jornada.get(jornada, {}).items()}


def _selftest() -> None:
    fc = Bootstrap({1: {"nailed": (5.0, 1.0), "rota": (5.0, 0.5),
                        "out": (5.0, 0.0)}})
    assert fc.expected(1) == {"nailed": 5.0, "rota": 2.5, "out": 0.0}
    assert expected_points((5.0, 0.5)) == 2.5 and expected_points(None) == 0.0, \
        "a cell (points if he plays, chance he plays) is worth their product"
    assert fc.expected(99) == {}, "a jornada nobody plays is empty, not an error"
    print("ffcore.forecast self-test OK")


if __name__ == "__main__":
    _selftest()
