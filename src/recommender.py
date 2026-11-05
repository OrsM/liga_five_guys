"""Recommender: which players on offer are worth a bid, and at what prices.

The contract with the rest of the code is two things.
  In:  the decision's rows (read_inputs): dec_player (player, pos, mine, asking, proceeds),
       dec_odds (player, ratio, win chance), dec_money (cash, team value, debt share, squad max, xi),
       cell (player, jornada, e).
  Out: Recs(picks, alone). picks: Rec(player, bid, win, gain, worth, drop) in the order to bid;
       drop: the player of yours to sell to make room when the squad is full, else "".
       alone: (b, s, bid, win, proceeds, gain, worth) for every bid of every candidate, each valued
       alone from the squad as it is (the board's rows).
It decides nothing about money beyond the debt limit on bids and nothing about sales; the plan funds.

The rule is a loop over the purchases. Candidates: every player on offer not in the squad. Value:
gain = the best legal eleven's points over the jornadas with him, less without; worth = win x gain.
Per candidate, the bid of most worth that is still within the debt limit (the lowest of ties). Order:
worth per euro bid, then name. Take the first that passes the thresholds (worth above nothing; bid
within what the debt allows less the bids placed; room in the squad, or a drop), add him to the
squad, value the rest again as the eleven changed; until none passes."""
from __future__ import annotations

from typing import Any, NamedTuple

from ffcore.rules import POSITIONS, best_eleven

Rows = dict[str, list[tuple[Any, ...]]]


class Rec(NamedTuple):
    player: str
    bid: float
    win: float
    gain: float
    worth: float
    drop: str


class Recs(NamedTuple):
    picks: list[Rec]
    alone: list[tuple[Any, ...]]


def cells_of(inp: Rows) -> tuple[dict[str, str], dict[int, dict[str, float]]]:
    """(position of each player, e of each player by jornada)."""
    pos = {k: p for k, p, *_ in inp["dec_player"] if p in POSITIONS}
    cells: dict[int, dict[str, float]] = {}
    for k, j, e in inp["cell"]:
        if k in pos:
            cells.setdefault(j, {})[k] = e
    return pos, cells


def points(pos: dict[str, str], cells: dict[int, dict[str, float]], squad: set[str],
           js: set[int] | None = None) -> float:
    """The best legal eleven's points over the jornadas (js: only these) of a squad."""
    return sum(best_eleven({p: sorted((-e[k], k) for k in squad if k in e and pos[k] == p) for p in POSITIONS})
               for j, e in cells.items() if js is None or j in js)


def recommend(inp: Rows) -> Recs:
    (cash, team_value, debt, squad_max, _), = inp["dec_money"]
    pos, cells = cells_of(inp)
    mine = {k: proceeds for k, _, m, _, proceeds in inp["dec_player"] if m}
    ratios: dict[str, list[tuple[float, float]]] = {}
    for k, r, w in sorted(inp["dec_odds"]):
        ratios.setdefault(k, []).append((r, w))
    bids = {k: [(a * r, w) for r, w in ratios.get(k, [(1.0, 1.0)])]
            for k, _, _, a, _ in inp["dec_player"] if a is not None and k in pos}
    where = {k: {j for j, e in cells.items() if k in e} for k in pos}
    limit = cash + debt * team_value

    squad, placed = set(mine), 0.0
    picks: list[Rec] = []
    alone: list[tuple[Any, ...]] = []

    def value(b: str, drop: str) -> Rec | None:
        """b's best bid within what is left of the debt limit, valued from the squad as it is."""
        full = len(squad) >= squad_max
        if full and not drop:
            return None
        js = where[b] | (where[drop] if full else set())
        gain = (points(pos, cells, (squad - {drop} if full else squad) | {b}, js) - points(pos, cells, squad, js))
        fits = [(win * gain, -bid, bid, win) for bid, win in bids[b] if bid <= limit - placed]
        if not fits:
            return None
        worth, _, bid, win = max(fits)
        return Rec(b, bid, win, gain, worth, drop if full else "") if worth > 0 else None

    def drop_now() -> str:
        here = points(pos, cells, squad)
        loss = {s: here - points(pos, cells, squad - {s}) for s in sorted(squad & set(mine))}
        return min(loss, key=lambda s: (loss[s], s)) if loss else ""

    for b in sorted(bids):
        g = points(pos, cells, squad | {b}, where[b]) - points(pos, cells, squad, where[b])
        alone += [(b, "", bid, win, None, g, win * g) for bid, win in bids[b]]
    # Lazy: adding a player never raises another's gain, so a stale ratio is an upper bound; only the
    # best is valued again, and taken once it still beats the next stale one.
    stale = {}
    drop = drop_now()
    for b in sorted(bids):
        if b not in squad and (r := value(b, drop)):
            stale[b] = r.worth / max(r.bid, 1.0)
    while stale and len(picks) < squad_max:
        b = min(stale, key=lambda k: (-stale[k], k))
        r = value(b, drop)
        if r is None:
            del stale[b]
            continue
        ratio = r.worth / max(r.bid, 1.0)
        stale[b] = ratio
        if any(v > ratio or (v == ratio and k < b) for k, v in stale.items() if k != b):
            continue
        picks.append(r)
        del stale[b]
        squad.add(b)
        if r.drop:
            squad.discard(r.drop)
        placed += r.bid
        drop = drop_now()
    return Recs(picks, alone)


def _selftest() -> None:
    """Seeded rows -> the recommendations: one jornada, a squad of 11 (5M each; 4 points, m4 2)."""
    from ffcore.rules import DEBT_SHARE, SQUAD_MAX, XI_SIZE

    squad = {"k": "POR", "d1": "DEF", "d2": "DEF", "d3": "DEF", "m1": "MED", "m2": "MED", "m3": "MED",
             "m4": "MED", "f1": "DEL", "f2": "DEL", "f3": "DEL"}
    e = {**{k: 4.0 for k in squad}, "m4": 2.0, "star": 8.0, "good": 5.0, "keeper": 3.0, "pricey": 9.0}
    kinds = {"star": "MED", "good": "MED", "keeper": "POR", "pricey": "MED"}

    def rows(cash: float, ask: dict[str, float], odds: list[tuple[str, float, float]] | None = None,
             extra: int = 0) -> Rows:
        sq = {**squad, **{"x%02d" % i: "DEF" for i in range(extra)}}
        return {"dec_player": [(k, p, 1, None, 5e6) for k, p in sq.items()]
                              + [(k, kinds[k], 0, a, None) for k, a in ask.items()],
                "dec_odds": list(odds or []), "dec_money": [(cash, 5e6 * len(sq), DEBT_SHARE, SQUAD_MAX, XI_SIZE)],
                "cell": [(k, 1, v) for k, v in e.items() if k in sq or k in ask]}

    def names(r: Recs) -> list[str]:
        return [p.player for p in r.picks]

    assert names(recommend(rows(10e6, {"star": 8e6}))) == ["star"]
    assert names(recommend(rows(10e6, {"keeper": 3e6}))) == [], "a second keeper plays for nobody: worth nothing"
    got = recommend(rows(30e6, {"star": 8e6, "good": 5e6}))
    assert [(p.player, p.gain) for p in got.picks] == [("star", 6.0), ("good", 1.0)], \
        "star replaces m4 (+6), good then a 4-pointer in a 3-5-2 (+1): valued again once star is in"
    # per euro: star 6 points for 8M, good 3 for 1M: good first, then star adds only what is left (6-3=3)
    got = recommend(rows(30e6, {"star": 8e6, "good": 1e6}))
    assert names(got) == ["good", "star"] and [round(p.gain, 6) for p in got.picks] == [3.0, 4.0], \
        "ordered by worth per euro, the rest valued again once he is in"
    assert names(recommend(rows(2e6, {"pricey": 20e6}))) == [], "a bid over cash and the debt (2M + 11M): not a bid"
    r = recommend(rows(10e6, {"star": 8e6}, odds=[("star", 1.0, 0.25), ("star", 1.25, 1.0), ("star", 1.3, 1.0)]))
    assert [(p.bid, p.win) for p in r.picks] == [(10e6, 1.0)], "the bid of most worth, the lowest of ties"
    assert [(a[2], a[3], a[5], a[6]) for a in r.alone] == [(8e6, 0.25, 6.0, 1.5), (10e6, 1.0, 6.0, 6.0),
                                                           (10.4e6, 1.0, 6.0, 6.0)], \
        "alone: every bid of every candidate from the squad as it is"
    full = recommend(rows(30e6, {"star": 8e6}, extra=SQUAD_MAX - len(squad)))
    assert [(p.player, p.drop) for p in full.picks] == [("star", "x00")], \
        "a full squad: a drop who plays for nobody makes room (the first by name of those that lose nothing)"
    print("recommender self-test OK")


if __name__ == "__main__":
    _selftest()
