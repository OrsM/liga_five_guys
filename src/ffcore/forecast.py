"""The forecast's constants, and set_constants, which sets any constant of the model's modules for a variant or a test."""
from __future__ import annotations

import importlib
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

__all__ = ["set_constants", "SEED_POOL", "MIN_POOL", "PERSISTENT_SHARE",
           "SHRINK_K", "PICK_K", "NEUTRAL_START", "ABSENT_START", "FIXTURE_K", "P_LIFT", "P_LISTING", "P_DECAY"]

SHRINK_K = 16.0       # matches a prior of points per match is worth (forecast_ppm)
PICK_K = 4.0          # matches his listing is worth against his record of selection (forecast_picked)
NEUTRAL_START = 60.0  # the start % of a player listed with none, or flagged
ABSENT_START = 2.0    # the start % of a player not listed (measured: 1.8% of 4192 graded cells played)
P_LIFT = 0.5         # share of the room above his chance to play added to it, p + P_LIFT p (1 - p) (measured: a start % understates scoring, subs score)
P_LISTING = 0.8       # the share of his chance to play that his listing's start % sets the next jornada, the rest his record of selection
P_DECAY = 0.9         # the share of that left a jornada on
FIXTURE_K = 10.0      # matches at the league's rate a club's goals for and against are pulled toward (club_rating)

SEED_POOL = (-1, -1, -1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1,
             1, 1, 1, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 3, 3, 3, 3, 3, 3, 3,
             4, 4, 4, 4, 4, 5, 5, 5, 6, 6, 6, 6, 8, 8, 8, 8, 9, 9, 10, 11,
             12, 13, 16)

MIN_POOL = 200

PERSISTENT_SHARE = 0.08


CONSTANT_MODULES = ("ffcore.forecast", "ffcore.pricing", "ffcore.rules", "ffcore.tidy")


def _owner(name: str) -> Any:
    for module in CONSTANT_MODULES:
        owner = importlib.import_module(module)
        if hasattr(owner, name):
            return owner
    raise KeyError("no module has the constant %s" % name)


@contextmanager
def set_constants(**values: float) -> Iterator[None]:
    """Constants set on the modules that own them (CONSTANT_MODULES), put back on leaving; a name no module has is a
    KeyError."""
    was = {n: getattr(_owner(n), n) for n in values}
    try:
        for n, v in values.items():
            setattr(_owner(n), n, v)
        yield
    finally:
        for n, v in was.items():
            setattr(_owner(n), n, v)
