"""The forecast's constants (schema.sql's forecast views read them through
model_constants, as k)."""
from __future__ import annotations

__all__ = ["SEED_POOL", "MIN_POOL", "PERSISTENT_SHARE",
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
