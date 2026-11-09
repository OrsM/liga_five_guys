"""The game's rules: positions, the formations the app allows, and what a
line-up role is worth in minutes. No data, no model: everything else may
import this."""
from __future__ import annotations

__all__ = ["XI_SIZE", "DEBT_SHARE", "BID_TARGET", "BIG_ASK", "BIG_WIN", "win_cap", "bid_to_use", "SALE_ESTIMATE", "SALE_SD", "bid_limit", "bid_margin", "cash_at_lock", "SQUAD_MAX", "CLAUSE_BUYS", "MANAGER_TRADES", "Squad", "Squads", "POSITIONS", "slot", "position", "FREE_FORMATIONS", "SHAPES", "MATCH_LEN"]

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

DEBT_SHARE = 0.2  # bids may take you this share of your squad's value into debt (cash at the lock must be 0 or more)


def bid_limit(cash: float, team_value: float, debt: float = DEBT_SHARE) -> float:
    """What bids can total: your cash and the debt the game allows (debt, a share of your team's value)."""
    return cash + debt * team_value


BID_TARGET = 0.90
BIG_ASK = 50e6
BIG_WIN = ((1.0, 0.77), (1.05, 0.90), (1.10, 0.96), (1.15, 0.98))


def win_cap(asking: float, ratio: float, w: float) -> float:
    caps = [cap for r, cap in BIG_WIN if ratio >= r - 1e-9]
    return min(w, caps[-1]) if asking >= BIG_ASK and caps else w


def bid_to_use(asking: float, odds: list[tuple[float, float]], target: float = BID_TARGET) -> tuple[float, float]:
    ok = sorted((r, win_cap(asking, r, w)) for r, w in odds if r >= 1.0)
    if not ok:
        return asking, 1.0
    for r, w in ok:
        if w >= target:
            return asking * r, w
    r, w = max(ok, key=lambda o: (o[1], -o[0]))
    return asking * r, w


SALE_ESTIMATE = 0.997  # what a player with no standing offer is expected to sell at, over his value (the nightly offers' mean)
SALE_SD = 0.057        # the spread of those offers over value


def bid_margin(cash: float, raised: float, team_value: float, sold_worth: float, bid: float, pending: float = 0.0,
               debt: float = DEBT_SHARE) -> float:
    """What is left of the bid limit once sales raise cash by raised and take sold_worth off the team's value, and the
    bid and the bids already placed (pending) are counted: 0 or more, the app takes the bid."""
    return bid_limit(cash + raised, team_value - sold_worth, debt) - pending - bid


def cash_at_lock(cash: float, raised: float, bid: float, later: float) -> float:
    """Cash at the lock after tonight's sales (raised), the bid won (bid) and the sales made by then (later): 0 or more."""
    return cash + raised - bid + later


def in_force(locks: dict[int, str], jornada: int, due: str | None) -> bool:
    """Whether a move made at due counts for a jornada: your eleven is frozen at its lock, so only a move made by
    then does (the game's clock, YYYY-MM-DDTHH:MMZ). locks: each jornada still to lock, its lock. A jornada before
    the first of them has locked already: nothing counts. One after the last has no lock known: every move counts,
    and with no lock known at all, so does every move. A move with no time known counts wherever a lock is ahead."""
    if not locks:
        return True
    if jornada < min(locks):
        return False
    return jornada not in locks or due is None or due <= locks[jornada]


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
    assert win_cap(30e6, 1.0, 0.95) == 0.95 and win_cap(60e6, 1.0, 0.95) == 0.77 and win_cap(60e6, 1.2, 1.0) == 0.98 \
        and win_cap(60e6, 1.0, 0.5) == 0.5, "a big ask's win chance is capped at what such asks observed; small asks and lower chances stay"
    assert bid_to_use(174.4e6, [(1.0, 0.933), (1.05, 0.962), (1.062, 1.0)]) == (174.4e6 * 1.05, 0.90), \
        "a big ask's bid reaches the target by the capped chance: 1.05 at 0.90, not 1.0 at 0.93"
    locks = {3: "2026-11-06T20:00Z", 4: "2026-11-13T20:00Z"}
    assert [in_force(locks, j, "2026-11-06T21:24Z") for j in (2, 3, 4, 5)] == [False, False, True, True], \
        "a buy closing after tonight's lock: not for the locked jornada, for the next; a locked one before it never"
    assert [in_force(locks, j, "2026-11-06T20:00Z") for j in (3, 4)] == [True, True], "due at the lock still counts"
    assert in_force(locks, 3, None) and in_force({}, 3, "2026-11-06T21:24Z"), "no time or no lock known: counts"
    assert slot("Centrocampista") == slot("mediocampista") == slot("MED") == "MED"
    assert slot(None) == slot("") == slot("utillero") == "", "unknown is empty, never guessed"
    assert POSITIONS == ("POR", "DEF", "MED", "DEL")
    assert all(1 + d + m + f == 11 for d, m, f in FREE_FORMATIONS)
    assert bid_margin(-50e6, 100e6, 400e6, 100e6, 150e6) == bid_limit(50e6, 300e6) - 150e6 and bid_margin(0.0, 0.0, 0.0, 0.0, 5.0) == -5.0
    assert bid_margin(10e6, 0.0, 100e6, 0.0, 5e6, pending=3e6) == 10e6 + DEBT_SHARE * 100e6 - 8e6, "pending bids count against the limit"
    assert cash_at_lock(-5.0, 20.0, 12.0, 0.0) == 3.0 and cash_at_lock(-5.0, 20.0, 12.0, 4.0) == 7.0
    print("ffcore.rules self-test OK")


if __name__ == "__main__":
    _selftest()
