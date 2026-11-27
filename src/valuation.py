from __future__ import annotations

import random
import sys
from collections.abc import Iterable, Mapping
from typing import NamedTuple

import eleven
from ffcore.rules import XI_SIZE, in_force

__all__ = ["Window", "READERS", "NEXT_JORNADAS", "window_at", "Move", "Value", "Delta", "Cells", "NOTHING", "selling", "squad_at", "value_of", "delta", "Reading", "read", "Buyer", "JOINT_MAX", "Memo", "expected_gain"]

Cells = Mapping[int, Mapping[str, float]]

NEXT_JORNADAS = 4
READERS = ("lock", "next4", "season")


class Window(tuple[int, ...]):
    """The jornadas, in order, that a move counts for (those `rules.in_force` keeps for the moment it is made). The one
    window of a move: `lock` is its first jornada, `next4` its first NEXT_JORNADAS, `season` all of it, each a Window
    inside the next. The decision value of a move is read over `season`; the others are shown for risk."""

    @property
    def lock(self) -> Window:
        return Window(self[:1])

    @property
    def next4(self) -> Window:
        return Window(self[:NEXT_JORNADAS])

    @property
    def season(self) -> Window:
        return self


def window_at(locks: Mapping[int, str], jornadas: Iterable[int], at: str | None) -> Window:
    """The Window of a move made at `at` (the game clock, None: unknown) over `jornadas`: those of them
    `rules.in_force` counts, in order."""
    held = dict(locks)
    return Window(sorted(j for j in jornadas if in_force(held, j, at)))


class Move(NamedTuple):
    """Players sold and players bought, each side with the game-clock moment it is made (None: unknown)."""
    sold: frozenset[str]
    bought: frozenset[str]
    sold_at: str | None
    bought_at: str | None


class Value(NamedTuple):
    """The points of a squad over a window: the best legal eleven's points of each jornada summed (a jornada with no
    legal eleven scores 0), and those jornadas, in the window's order."""
    points: float
    gap: tuple[int, ...]


class Delta(NamedTuple):
    """What a move is worth over a window: the Value after it less the Value without it, None when the squad after
    the whole move has no legal eleven; gap is the jornadas of the window the move leaves without one."""
    points: float | None
    gap: tuple[int, ...]


NOTHING = Move(frozenset(), frozenset(), None, None)


def selling(*players: str) -> Move:
    """The Move that sells the players now (no moment known) and buys nobody."""
    return Move(frozenset(players), frozenset(), None, None)


def squad_at(squad: frozenset[str], move: Move, jornada: int, locks: Mapping[int, str]) -> frozenset[str]:
    """The squad that plays a jornada: `squad` less the players sold and plus the players bought, each side only when
    `rules.in_force` counts its moment for that jornada."""
    sold = move.sold if in_force(dict(locks), jornada, move.sold_at) else frozenset()
    bought = move.bought if in_force(dict(locks), jornada, move.bought_at) else frozenset()
    return (squad - sold) | bought


def value_of(squad: frozenset[str], move: Move, window: tuple[int, ...], locks: Mapping[int, str],
             pos: Mapping[str, str], cells: Cells) -> Value:
    """The Value of the squad after the move over the window; `cells` gives each jornada's forecast per player."""
    seen = [(j, eleven.points(squad_at(squad, move, j, locks), pos, cells[j])) for j in window]
    return Value(sum(v for _, v in seen if v is not None), tuple(j for j, v in seen if v is None))


def delta(squad: frozenset[str], move: Move, window: tuple[int, ...], locks: Mapping[int, str],
          pos: Mapping[str, str], cells: Cells) -> Delta:
    """The Delta of the move over the window. Its points are None exactly when the squad after the whole move (sold
    gone, bought arrived) has no legal eleven, whatever the window; a bench sale is 0 in every window."""
    after = value_of(squad, move, window, locks, pos, cells)
    before = value_of(squad, NOTHING, window, locks, pos, cells)
    whole = eleven.points((squad - move.sold) | move.bought, pos, {})
    return Delta(None if whole is None else after.points - before.points, after.gap)


class Reading(NamedTuple):
    """The Delta of a move over a Window as the board states it: `points` over the whole window (the decision value), `window`
    the same Delta over each of READERS (lock, next4, season, each a prefix of the one Window), `gap` the jornadas of the whole
    window the move leaves without a legal eleven. Every number is None exactly when the squad after the whole move has no
    legal eleven."""
    points: float | None
    window: Mapping[str, float | None]
    gap: tuple[int, ...]


def read(squad: frozenset[str], move: Move, window: Window, locks: Mapping[int, str],
         pos: Mapping[str, str], cells: Cells) -> Reading:
    """The Reading of the move over the Window: `delta` over each of READERS, `points` that of the season."""
    got = {name: delta(squad, move, getattr(window, name), locks, pos, cells) for name in READERS}
    return Reading(got["season"].points, {name: d.points for name, d in got.items()}, got["season"].gap)


class Buyer:
    """The gain of buying one player who arrives before every jornada of the window, asked of many players over one squad:
    the Delta of that move, summed over the window. The squad must field a legal eleven. V_j of the squad plus a player
    never falls as his forecast rises, so each (jornada, position) remembers the highest forecast known to add nothing and
    a player no better there is not valued again."""

    def __init__(self, squad: frozenset[str], window: tuple[int, ...], pos: Mapping[str, str], cells: Cells):
        self.squad, self.window, self.pos, self.cells = squad, window, pos, cells
        self.base = {j: eleven.points(squad, pos, cells[j]) or 0.0 for j in window}
        self.nothing: dict[tuple[int, str], float] = {}

    def gain(self, player: str) -> float:
        total = 0.0
        position = self.pos[player]
        for j in self.window:
            e = self.cells[j].get(player, 0.0)
            if e <= self.nothing.get((j, position), float("-inf")):
                continue
            got = (eleven.points(self.squad | {player}, self.pos, self.cells[j]) or 0.0) - self.base[j]
            if got > 0.0:
                total += got
            else:
                self.nothing[(j, position)] = e
        return total


JOINT_MAX = 7


class Memo:
    """What expected_gain remembers over one squad, positions and cells: the Buyer of each (squad plus a set of buys, window) and each gain asked of it."""

    def __init__(self) -> None:
        self.buyers: dict[tuple[frozenset[str], tuple[int, ...]], Buyer] = {}
        self.gains: dict[tuple[frozenset[str], tuple[int, ...], str], float] = {}


def expected_gain(squad: frozenset[str], player: str, chosen: Mapping[str, float], window: tuple[int, ...], pos: Mapping[str, str], cells: Cells,
                  memo: Memo | None = None) -> float:
    """The points `player` adds over `window` to the squad plus the buys of `chosen` (player -> chance it is won, each won independently), averaged
    over which are won: the sum over the subsets W of chosen of p(W) x Buyer(squad + W, window).gain(player), no second valuation. With no chosen
    it is Buyer(squad, window).gain; every chance 1 it is the gain over squad plus all of them, every chance 0 the gain alone. It never exceeds
    the gain alone and never rises as `chosen` grows (the value of a squad is submodular), nor the gain of a player already in it: 0.
    Bounded: only the JOINT_MAX likeliest of `chosen` (ties by key) are held, so at most 2^JOINT_MAX Buyers; the rest are ignored, which
    can only overstate the gain."""
    held = sorted(((min(1.0, w), k) for k, w in chosen.items() if w > 0.0 and k != player and k not in squad), key=lambda x: (-x[0], x[1]))[:JOINT_MAX]
    seen = Memo() if memo is None else memo
    total = 0.0
    for mask in range(1 << len(held)):
        p = 1.0
        won: set[str] = set()
        for i, (w, k) in enumerate(held):
            if mask >> i & 1:
                p, won = p * w, won | {k}
            else:
                p *= 1.0 - w
        if p <= 0.0:
            continue
        base = frozenset(won)
        if (base, window, player) not in seen.gains:
            if (base, window) not in seen.buyers:
                seen.buyers[(base, window)] = Buyer(squad | base, window, pos, cells)
            seen.gains[(base, window, player)] = seen.buyers[(base, window)].gain(player)
        total += p * seen.gains[(base, window, player)]
    return total


def _league(rng: random.Random, ties: bool, jornadas: tuple[int, ...]) -> tuple[dict[str, str], dict[int, dict[str, float]]]:
    sizes = {"POR": rng.randint(1, 3), "DEF": rng.randint(4, 7), "MED": rng.randint(4, 7), "DEL": rng.randint(2, 5)}
    pos = {"%s%d" % (p, i): p for p, n in sizes.items() for i in range(n)}
    return pos, {j: {k: float(rng.randint(0, 5)) if ties else rng.uniform(0, 9) for k in pos} for j in jornadas}


def _selftest_read(rng: random.Random) -> None:
    locks = {5: "2026-11-27T20:00Z", 6: "2026-12-04T20:00Z", 7: "2026-12-11T20:00Z", 8: "2026-12-18T20:00Z", 9: "2026-12-25T20:00Z",
             10: "2027-01-01T20:00Z"}
    window = window_at(locks, locks, "2026-11-20T10:00Z")
    for case in range(120):
        pos, cells = _league(rng, case % 2 == 0, tuple(locks))
        squad = frozenset(rng.sample(sorted(pos), rng.randint(XI_SIZE, len(pos))))
        sale = selling(*rng.sample(sorted(squad), rng.randint(1, 3)))
        got = read(squad, sale, window, locks, pos, cells)
        assert set(got.window) == set(READERS) and got.points == got.window["season"]
        for name in READERS:
            alone = delta(squad, sale, getattr(window, name), locks, pos, cells)
            assert alone.points == got.window[name], "a reading's split is the Delta over each prefix of the one window"
        assert (got.points is None) == (eleven.points(squad - sale.sold, pos, {}) is None) == all(v is None for v in got.window.values())
        if got.points is not None:
            lock, next4, season = (got.window[n] for n in READERS)
            assert lock is not None and next4 is not None and season is not None
            assert got.gap == () and lock >= next4 - 1e-9 >= season - 2e-9, "a longer window loses at least as much"
        else:
            assert got.gap == tuple(window)
    while True:
        pos, cells = _league(rng, False, tuple(locks))
        exact = frozenset(rng.sample(sorted(pos), XI_SIZE))
        if eleven.points(exact, pos, {}) is not None:
            break
    rows = [read(exact, selling(k), window, locks, pos, cells) for k in sorted(exact)]
    assert all(r.points is None and set(r.window.values()) == {None} for r in rows), "a squad of exactly 11 quotes no sale"


def _selftest_window() -> None:
    locks = {5: "2026-11-27T20:00Z", 6: "2026-12-04T20:00Z", 7: "2026-12-11T20:00Z"}
    every = range(1, 12)
    for at in (None, "2026-11-20T10:00Z", "2026-11-27T20:00Z", "2026-11-30T10:00Z", "2026-12-20T10:00Z"):
        w = window_at(locks, every, at)
        assert list(w) == sorted(w) and all(in_force(dict(locks), j, at) for j in w) and not any(
            in_force(dict(locks), j, at) for j in every if j not in w), "a window is exactly the jornadas in force"
        assert w.season == w and set(w.lock) <= set(w.next4) <= set(w.season)
        assert len(w.next4) == min(NEXT_JORNADAS, len(w)) and len(w.lock) == min(1, len(w))
        assert w.lock == w[:1] and w.next4 == w[:NEXT_JORNADAS]
    early, late = window_at(locks, every, "2026-11-20T10:00Z"), window_at(locks, every, "2026-11-30T10:00Z")
    assert early[0] == 5 and late[0] == 6, "a move made after a lock does not count for it"
    assert window_at({}, (), None) == () and window_at(locks, (1, 2), None) == ()


def _selftest_joint(rng: random.Random) -> None:
    window = (5, 6, 7)

    def value(squad: frozenset[str], pos: Mapping[str, str], cells: Cells) -> float:
        return sum(eleven.points(squad, pos, cells[j]) or 0.0 for j in window)

    def worth_of(squad: frozenset[str], chosen: Mapping[str, float], pos: Mapping[str, str], cells: Cells) -> float:
        """F(S) by brute force: the expected value of the squad plus the buys won, less the squad's, over every subset."""
        keys = sorted(chosen)
        total = 0.0
        for mask in range(1 << len(keys)):
            p, won = 1.0, set()
            for i, k in enumerate(keys):
                p *= chosen[k] if mask >> i & 1 else 1.0 - chosen[k]
                if mask >> i & 1:
                    won.add(k)
            total += p * (value(squad | won, pos, cells) - value(squad, pos, cells))
        return total

    seen = {"covered": 0, "cases": 0}
    for case in range(260):
        pos, cells = _league(rng, case % 2 == 0, window)
        extra = {"Y%d" % i: rng.choice(["POR", "DEF", "MED", "DEL"]) for i in range(6)}
        pos = pos | extra
        cells = {j: row | {k: float(rng.randint(0, 5)) if case % 2 == 0 else rng.uniform(0, 9) for k in extra} for j, row in cells.items()}
        squad = frozenset(rng.sample(sorted(pos), rng.randint(XI_SIZE, len(pos) - 4)))
        if eleven.points(squad, pos, {}) is None:
            continue
        outside = sorted(set(pos) - squad)
        picks = rng.sample(outside, min(len(outside), rng.randint(2, 5)))
        w = {k: rng.choice([0.0, 0.25, 0.5, 0.9, 1.0]) for k in picks}
        k = picks[0]
        rest = {j: x for j, x in w.items() if j != k}
        got = expected_gain(squad, k, rest, window, pos, cells)
        alone = Buyer(squad, window, pos, cells).gain(k)
        seen["cases"] += 1
        assert expected_gain(squad, k, {}, window, pos, cells) == alone, "with nothing chosen it is the Buyer's gain"
        assert -1e-9 <= got <= alone + 1e-9, "monotone and bounded by the gain alone"
        assert abs(expected_gain(squad, k, {j: 0.0 for j in rest}, window, pos, cells) - alone) < 1e-9, "w = 0: the gain alone"
        full = {j: 1.0 for j in rest}
        assert abs(expected_gain(squad, k, full, window, pos, cells) - (value(squad | set(rest) | {k}, pos, cells) - value(squad | set(rest), pos, cells))) < 1e-9, \
            "w = 1: the plain eleven difference over the squad plus the buys"
        assert abs(expected_gain(squad, k, w, window, pos, cells) - expected_gain(squad, k, rest, window, pos, cells)) < 1e-9, "a player is not conditioned on himself"
        direct = worth_of(squad, w, pos, cells)
        for order in (list(picks), picks[::-1], rng.sample(picks, len(picks))):
            held: dict[str, float] = {}
            chain = 0.0
            for j in order:
                chain += w[j] * expected_gain(squad, j, held, window, pos, cells)
                held[j] = w[j]
            assert abs(chain - direct) < 1e-9, "the chain of marginals telescopes to the value of the set, in any order"
        assert direct <= sum(w[j] * Buyer(squad, window, pos, cells).gain(j) for j in picks) + 1e-9, "a set is worth at most the sum of its buys alone"
        smaller = {j: x for j, x in list(rest.items())[:max(0, len(rest) - 1)]}
        assert expected_gain(squad, k, {j: 1.0 for j in rest}, window, pos, cells) <= expected_gain(squad, k, {j: 1.0 for j in smaller}, window, pos, cells) + 1e-9, \
            "submodular: more buys held never raise the gain"
        for scale in (0.5, 3.0):
            scaled = {j: {m: scale * x for m, x in row.items()} for j, row in cells.items()}
            assert abs(expected_gain(squad, k, rest, window, pos, scaled) - scale * got) < 1e-9 * (1 + scale * got), "forecasts scaled scale the gain"
        memo = Memo()
        assert abs(expected_gain(squad, k, rest, window, pos, cells, memo) - got) < 1e-9 and abs(expected_gain(squad, k, rest, window, pos, cells, memo) - got) < 1e-9, \
            "a remembered gain is the same"
    assert seen["cases"] > 100
    pos = {"POR0": "POR", "POR1": "POR", "POR2": "POR", **{"%s%d" % (p, i): p for p, n in {"DEF": 4, "MED": 4, "DEL": 2}.items() for i in range(n)}}
    cells = {j: {k: 5.0 for k in pos} | {"POR0": 1.0, "POR1": 7.0, "POR2": 4.0} for j in window}
    squad = frozenset(k for k in pos if k not in ("POR1", "POR2"))
    assert abs(expected_gain(squad, "POR2", {"POR1": 1.0}, window, pos, cells)) < 1e-9, "a keeper the other chosen keeper already out-scores adds nothing: covered"
    assert abs(expected_gain(squad, "POR2", {"POR1": 0.5}, window, pos, cells) - 0.5 * 3.0 * len(window)) < 1e-9, "and half of his gain when the other is won half the time"
    assert abs(expected_gain(squad, "POR1", {"POR2": 1.0}, window, pos, cells) - 3.0 * len(window)) < 1e-9, "the better keeper still adds his margin over the other"
    many = {"%s%d" % ("X", i): 0.5 for i in range(JOINT_MAX + 3)}
    big = dict(pos) | {k: "DEL" for k in many}
    cells_big = {j: dict(cells[j]) | {k: float(i) for i, k in enumerate(many)} for j in window}
    keep = expected_gain(squad, "POR1", many, window, big, cells_big)
    assert abs(keep - expected_gain(squad, "POR1", {}, window, big, cells_big)) < 1e-9, "a bounded set of held buys still answers"


def _selftest() -> None:
    rng = random.Random(23)
    locks = {5: "2026-11-27T20:00Z", 6: "2026-12-04T20:00Z", 7: "2026-12-11T20:00Z"}
    early, mid, late = "2026-11-26T10:00Z", "2026-11-30T10:00Z", "2026-12-20T10:00Z"
    window = tuple(sorted(locks))
    benched = 0
    for case in range(300):
        pos, cells = _league(rng, case % 2 == 0, window)
        squad = frozenset(rng.sample(sorted(pos), rng.randint(XI_SIZE, len(pos))))
        v = {j: eleven.points(squad, pos, cells[j]) for j in window}
        if any(x is None for x in v.values()):
            continue
        base = value_of(squad, NOTHING, window, locks, pos, cells)
        assert abs(base.points - sum(x or 0.0 for x in v.values())) < 1e-9 and base.gap == ()
        gone = frozenset(rng.sample(sorted(squad), rng.randint(1, 3)))
        sale = Move(gone, frozenset(), early, None)
        got = delta(squad, sale, window, locks, pos, cells)
        rest = eleven.points(squad - gone, pos, {})
        assert (got.points is None) == (rest is None), "a sale has a quotable Delta exactly when an eleven remains"
        if got.points is None:
            assert got.gap == window
        else:
            want = sum((eleven.points(squad - gone, pos, cells[j]) or 0.0) - (v[j] or 0.0) for j in window)
            assert abs(got.points - want) < 1e-9, "Delta of a sale is V(S-X) less V(S), summed over the window"
            assert got.points <= 1e-9 and got.gap == ()
        for when in (mid, late, None):
            other = delta(squad, Move(gone, frozenset(), when, None), window, locks, pos, cells)
            assert (other.points is None) == (got.points is None), "legality does not depend on when or over what window"
        for shorter in (window[:1], window[:2], window[1:]):
            assert (delta(squad, sale, shorter, locks, pos, cells).points is None) == (got.points is None)
        played = [eleven.best_eleven(squad, pos, cells[j]) for j in window]
        sat = frozenset(squad) - frozenset().union(*(p.eleven for p in played if p is not None))
        for k in sorted(sat)[:2]:
            for when in (early, mid, late, None):
                assert delta(squad, Move(frozenset({k}), frozenset(), when, None), window, locks, pos, cells).points == 0.0, \
                    "a bench sale is worth 0 in every window"
            benched += 1
        buy = frozenset(sorted(set(pos) - squad)[:2])
        both = delta(squad, Move(gone, buy, early, early), window, locks, pos, cells)
        whole = eleven.points((squad - gone) | buy, pos, {})
        assert (both.points is None) == (whole is None) and both.gap == (() if whole is not None else window)
        if both.points is not None:
            want = sum((eleven.points((squad - gone) | buy, pos, cells[j]) or 0.0) - (v[j] or 0.0) for j in window)
            assert abs(both.points - want) < 1e-9, "a move in force at every jornada is V(S-X+Y) less V(S), summed"
        late_buy = delta(squad, Move(gone, buy, early, late), window, locks, pos, cells)
        assert (late_buy.points is None) == (whole is None), "legality is that of the squad after everything has arrived"
        assert late_buy.gap == (() if rest is not None else window), "no eleven in between exactly when the sale alone leaves none"
    assert benched > 20, "the bench-sale property was exercised"
    for case in range(100):
        pos, cells = _league(rng, False, window)
        players = sorted(pos)
        for _ in range(20):
            squad = frozenset(rng.sample(players, XI_SIZE))
            if eleven.points(squad, pos, {}) is not None:
                for k in squad:
                    assert delta(squad, Move(frozenset({k}), frozenset(), early, None), window, locks, pos, cells).points is None, \
                        "a squad of exactly 11 has no eleven after any single sale"
                break
    for case in range(60):
        pos, cells = _league(rng, case % 2 == 0, window)
        squad = frozenset(rng.sample(sorted(pos), rng.randint(XI_SIZE, max(XI_SIZE, len(pos) - 1))))
        if eleven.points(squad, pos, {}) is None:
            continue
        buyer = Buyer(squad, window, pos, cells)
        outside = sorted(set(pos) - squad)
        for k in outside + outside[::-1]:
            bought = delta(squad, Move(frozenset(), frozenset({k}), None, early), window, locks, pos, cells).points
            assert bought is not None and abs(buyer.gain(k) - bought) < 1e-9, "the Buyer's gain is the Delta of the buy, whatever the order asked"
            assert bought >= -1e-9, "a buy never loses points"
    here = frozenset("ab")
    assert squad_at(here, Move(frozenset("a"), frozenset("c"), early, late), 5, locks) == frozenset("b"), "only the sale is in force"
    assert squad_at(here, Move(frozenset("a"), frozenset("c"), early, early), 5, locks) == frozenset("bc")
    assert squad_at(here, NOTHING, 5, locks) == here
    _selftest_window()
    _selftest_read(rng)
    _selftest_joint(rng)
    print("valuation self-test OK")


if __name__ == "__main__" and "--selftest" in sys.argv:
    _selftest()
