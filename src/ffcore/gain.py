"""A squad's eleven by value (rules.ELEVEN), for Outlook: what the board
shows as your eleven next. The season's points and the standings are
decide.sql's (league_xi, league_standings; why not simulated:
docs/notes/gain.md)."""
from __future__ import annotations

import sqlite3

from ffcore.rules import ELEVEN, MAX_SLOT, MIN_SLOT, Squad

__all__ = ["eleven"]

SCHEMA = """
CREATE TABLE bounds (slot TEXT PRIMARY KEY, lo INTEGER, hi INTEGER);
CREATE TABLE squad (id INTEGER, ord INTEGER, player TEXT, slot TEXT);
CREATE TABLE valued (player TEXT PRIMARY KEY, e REAL) WITHOUT ROWID;
"""

XI = "WITH pick AS (SELECT 1 AS id, 0 AS jornada, s.player, s.slot, s.ord, coalesce(v.e, 0.0) AS e" \
     " FROM squad s LEFT JOIN valued v USING (player))," + ELEVEN + "\nSELECT player FROM xi"

_CON: list[sqlite3.Connection] = []


def _con() -> sqlite3.Connection:
    if not _CON:
        db = sqlite3.connect(":memory:")
        db.executescript(SCHEMA)
        db.executemany("INSERT INTO bounds VALUES (?, ?, ?)", [(p, MIN_SLOT[p], MAX_SLOT[p]) for p in MIN_SLOT])
        _CON.append(db)
    return _CON[0]


def eleven(squad: Squad, value: dict[str, float]) -> list[str]:
    """Squad's (player: slot) eleven by value (missing: 0), rules.ELEVEN."""
    db = _con()
    with db:
        db.execute("DELETE FROM squad")
        db.execute("DELETE FROM valued")
        db.executemany("INSERT INTO squad VALUES (1, ?, ?, ?)",
                       [(o, k, slot) for o, (k, slot) in enumerate(squad.items())])
        db.executemany("INSERT INTO valued VALUES (?, ?)", [(k, value[k]) for k in squad if k in value])
        return [k for k, in db.execute(XI)]


def _selftest() -> None:
    from ffcore.rules import XI_SIZE

    squad = {"k": "POR", "d1": "DEF", "d2": "DEF", "d3": "DEF", "d4": "DEF",
             "d5": "DEF", "m1": "MED", "m2": "MED", "m3": "MED", "m4": "MED",
             "m5": "MED", "f1": "DEL", "f2": "DEL", "f3": "DEL"}
    val = {k: 1.0 for k in squad}
    val.update({"f1": 9.0, "f2": 9.0, "f3": 9.0})
    got = eleven(squad, val)
    assert len(got) == XI_SIZE and {"f1", "f2", "f3"} <= set(got), got
    assert set(eleven({"d1": "DEF", "m1": "MED"}, {"d1": 1.0, "m1": 2.0})) == {"d1", "m1"}, \
        "short of a formation: who it has plays, an empty slot scores nothing (the game's rule)"
    assert set(eleven(squad, {**val, "d4": 5.0, "d5": 5.0, "m4": 5.0, "m5": 5.0})) == {
        "k", "d1", "d4", "d5", "m4", "m5", "m1", "f1", "f2", "f3", "d2"}, \
        "each position's least best first (POR 1, DEF 3, MED 3, DEL 1), then the best of the rest within the most"
    # 5-4-1 (DEF4 at 2.0) and 4-5-1 (MED3 at 2.0) sum the same: level, the
    # earlier in the squad plays
    tv = {"DEF4": 2.0, "DEF0": 8.491227695871778, "MED0": 2.5, "MED2": 7.411348877403508, "MED5": 0.0,
          "POR0": 2.5, "DEF2": 2.0, "POR1": 2.5, "DEF6": 1.0, "DEL0": 3.0, "MED4": 3.0, "DEF5": 2.5,
          "MED1": 2.0, "DEF1": 2.5, "MED3": 2.0, "DEF3": 2.10641317928871}
    tie = {k: k[:3] for k in tv}
    assert "DEF4" in eleven(tie, tv) and "MED3" not in eleven(tie, tv), \
        "level players: the earlier in the squad"

    print("ffcore.gain self-test OK")


if __name__ == "__main__":
    _selftest()
