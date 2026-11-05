"""The game's rules: positions, the formations the app allows, and what a
line-up role is worth in minutes. No data, no model: everything else may
import this."""
from __future__ import annotations

__all__ = ["XI_SIZE", "DEBT_SHARE", "bid_limit", "SQUAD_MAX", "CLAUSE_BUYS", "MANAGER_TRADES", "Squad", "Squads", "POSITIONS", "slot", "position", "MAX_SLOT", "MIN_SLOT", "FREE_FORMATIONS", "SHAPES", "ELEVEN", "best_eleven", "FORMATIONS",
           "MATCH_LEN"]

POSITIONS = ("POR", "DEF", "MED", "DEL")

Squad = dict[str, str]     # player: position
Squads = dict[str, Squad]  # manager: squad

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

# The most and the least of each position any formation fields: the
# formations are every count between them that makes eleven (_selftest).
MAX_SLOT = {"POR": 1, **dict(zip(POSITIONS[1:], map(max, zip(*FREE_FORMATIONS))))}
MIN_SLOT = {"POR": 1, **dict(zip(POSITIONS[1:], map(min, zip(*FREE_FORMATIONS))))}



def slot(raw: str | None) -> str:
    """A position as the game writes it (portero, Centrocampista, MED...)
    as one of POSITIONS, or "" if it is none of them."""
    t = (raw or "").strip()
    return t if t in POSITIONS else _SLOT.get(t.lower(), "")


def position(raw: str | None) -> str:
    """slot(raw), or MED when there is none: where a player of unknown
    position is counted."""
    return slot(raw) or "MED"

MATCH_LEN = 90.0
XI_SIZE = 11  # players a side fields

# The eleven, as SQL (the CTEs after a WITH that defines pick(id, jornada,
# player, slot, ord, e) and bounds(slot, lo, hi), MIN_SLOT and MAX_SLOT):
# for each id and jornada, each position's lo best, then the best of the
# rest within each position's hi, to eleven; xi(id, jornada, player, slot,
# e). As the formations are every count between lo and hi that makes eleven,
# this is the best eleven of any formation; short of a position, its empty
# slot scores nothing (the game's rule). Level players: the lower ord plays.
# decide.sql's elevens all are this.
ELEVEN = """
ranked AS MATERIALIZED (
    SELECT p.*, b.lo, b.hi,
           row_number() OVER (PARTITION BY p.id, p.jornada, p.slot ORDER BY p.e DESC, p.ord) AS n
    FROM pick p JOIN bounds b USING (slot)),
rest AS (SELECT *, row_number() OVER (PARTITION BY id, jornada ORDER BY e DESC, ord) AS m
         FROM ranked WHERE n > lo AND n <= hi),
xi AS MATERIALIZED (
    SELECT id, jornada, player, slot, e FROM ranked WHERE n <= lo
    UNION ALL
    SELECT id, jornada, player, slot, e FROM rest WHERE m <= %d - (SELECT sum(lo) FROM bounds))""" % XI_SIZE

def best_eleven(by_pos: dict[str, list[tuple[float, str]]]) -> float:
    """ELEVEN's points in Python, for the decision's search: by_pos is each
    position's players as (-e, ord), best first; each position's MIN_SLOT
    best, then the best of the rest within MAX_SLOT to XI_SIZE (level: the
    lower ord plays, as the tuple sort has it)."""
    total, rest = 0.0, []
    for p in POSITIONS:
        total += sum(-e for e, _ in by_pos[p][:MIN_SLOT[p]])
        rest += by_pos[p][MIN_SLOT[p]:MAX_SLOT[p]]
    return total + sum(-e for e, _ in sorted(rest)[:XI_SIZE - sum(MIN_SLOT.values())])


# The formations as SQL rows (f, pos, n), f their order above: what the
# views read where they name rules.FORMATIONS (db.connect puts it in).
FORMATIONS = " UNION ALL ".join("SELECT %d AS f, '%s' AS pos, %d AS n" % (i, p, n)
                                for i, shape in enumerate(SHAPES) for p, n in shape.items())

DEBT_SHARE = 0.2  # bids may take you this share of your squad's value into debt (cash at the lock must be 0 or more)


def bid_limit(cash: float, team_value: float, debt: float = DEBT_SHARE) -> float:
    """What bids can total: your cash and the debt the game allows (debt, a share of your team's value)."""
    return cash + debt * team_value
# Players a squad may hold, its bids in counted: the app refuses a bid past
# it ("Bid or offer for more players is not allowed", 23 and a bid, 2026-10-30).
SQUAD_MAX = 24

# What the plan considers: our choices, not the game's rules, each lifted
# here, in one place, if the league turns nasty. The code each one narrows
# fails loudly when it is lifted (NotImplementedError), where the plan must
# first learn the move, rather than go on silently without it.
CLAUSE_BUYS = False     # a rival's player bought by paying his clause: you do not shoot first
MANAGER_TRADES = False  # offers to or from another manager: only the game's market and its offers


def _selftest() -> None:
    for cash in (-30e6, 0.0, 12e6):      # bids reach past your cash by the debt share of the team, never less, and grow with it
        assert bid_limit(cash, 0.0) == cash and bid_limit(cash, 100e6) == cash + DEBT_SHARE * 100e6
        assert bid_limit(cash, 50e6) < bid_limit(cash, 60e6) and bid_limit(cash, 50e6, 0.0) == cash
    assert slot("Centrocampista") == slot("mediocampista") == slot("MED") == "MED"
    assert slot(None) == slot("") == slot("utillero") == "", "unknown is empty, never guessed"
    assert POSITIONS == ("POR", "DEF", "MED", "DEL")
    assert all(1 + d + m + f == 11 for d, m, f in FREE_FORMATIONS)
    assert MAX_SLOT == {"POR": 1, "DEF": 5, "MED": 5, "DEL": 3}, MAX_SLOT
    assert MIN_SLOT == {"POR": 1, "DEF": 3, "MED": 3, "DEL": 1}, MIN_SLOT
    import itertools
    box = [dict(zip(POSITIONS, n)) for n in itertools.product(*(range(MIN_SLOT[p], MAX_SLOT[p] + 1)
                                                                for p in POSITIONS)) if sum(n) == XI_SIZE]
    assert sorted(map(sorted, map(dict.items, box))) == sorted(map(sorted, map(dict.items, SHAPES))), \
        "the formations are every count of each position between its least and most that makes eleven"

    import sqlite3

    def eleven(squad: dict[str, str], value: dict[str, float]) -> set[str]:
        db = sqlite3.connect(":memory:")
        db.execute("CREATE TABLE pick (id, jornada, player, slot, ord, e)")
        db.execute("CREATE TABLE bounds (slot, lo, hi)")
        db.executemany("INSERT INTO pick VALUES (1, 0, ?, ?, ?, ?)",
                       [(k, p, k, value.get(k, 0.0)) for k, p in squad.items()])
        db.executemany("INSERT INTO bounds VALUES (?, ?, ?)", [(p, MIN_SLOT[p], MAX_SLOT[p]) for p in POSITIONS])
        return {k for k, in db.execute("WITH " + ELEVEN + "\nSELECT player FROM xi")}
    squad = {"k": "POR", **{f"d{i}": "DEF" for i in range(1, 6)}, **{f"m{i}": "MED" for i in range(1, 6)},
             **{f"f{i}": "DEL" for i in range(1, 4)}}
    val = {**{k: 1.0 for k in squad}, "f1": 9.0, "f2": 9.0, "f3": 9.0}
    assert len(eleven(squad, val)) == XI_SIZE and {"f1", "f2", "f3"} <= eleven(squad, val)
    assert eleven({"d1": "DEF", "m1": "MED"}, {"d1": 1.0, "m1": 2.0}) == {"d1", "m1"}, \
        "short of a formation: who it has plays, an empty slot scores nothing (the game's rule)"
    assert eleven(squad, {**val, "d4": 5.0, "d5": 5.0, "m4": 5.0, "m5": 5.0}) == {
        "k", "d1", "d4", "d5", "m4", "m5", "m1", "f1", "f2", "f3", "d2"}, \
        "each position's least best first (POR 1, DEF 3, MED 3, DEL 1), then the best of the rest within the most"
    tie = {"POR0": "POR", **{f"DEF{i}": "DEF" for i in range(3)}, **{f"MED{i}": "MED" for i in range(3)},
           "DEL0": "DEL", "DEL1": "DEL", "DEL2": "DEL", "DEF4": "DEF", "MED3": "MED"}
    level = eleven(tie, {**{k: 3.0 for k in tie}, "DEF4": 2.0, "MED3": 2.0})
    assert "DEF4" in level and "MED3" not in level, "level players for the last place: the lower ord plays"
    import random
    rng = random.Random(7)
    for n in range(60):   # SQL and Python pick the same eleven: seeded squads, short and long, with ties
        sq = {f"p{i:02d}": rng.choice(POSITIONS) for i in range(rng.choice([0, 1, 5, 11, 16, 24]))}
        if n % 3 == 0:
            sq = {k: ("DEF" if p == "MED" else p) for k, p in sq.items()}   # short of a position
        val = {k: float(rng.randint(0, 3)) for k in sq}   # few levels: many ties
        by_pos = {p: sorted((-val[k], k) for k in sq if sq[k] == p) for p in POSITIONS}
        got = eleven(sq, val)
        assert abs(best_eleven(by_pos) - sum(val[k] for k in got)) < 1e-9, (sq, val)
    print("ffcore.rules self-test OK")


if __name__ == "__main__":
    _selftest()
