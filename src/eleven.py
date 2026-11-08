from __future__ import annotations

import itertools
import random
import sys
from collections import Counter
from collections.abc import Mapping
from typing import NamedTuple

from ffcore.rules import POSITIONS, SHAPES, XI_SIZE

__all__ = ["Swap", "Settled", "legal", "swap", "settle", "removal_cost"]

COUNTS = {tuple(shape[p] for p in POSITIONS) for shape in SHAPES}

Pos = Mapping[str, str]
Value = Mapping[str, float]


class Swap(NamedTuple):
    improves: bool
    replaces: str | None
    gain: float
    eleven_after: frozenset[str]


class Settled(NamedTuple):
    eleven: frozenset[str]
    bench: frozenset[str]
    swaps: tuple[tuple[str, str, float], ...]


def fits(count: Counter[str]) -> bool:
    return tuple(count[p] for p in POSITIONS) in COUNTS


def legal(eleven: frozenset[str], pos: Pos) -> bool:
    return len(eleven) == XI_SIZE and fits(Counter(pos.get(k, "") for k in eleven))


def swap(player: str, eleven: frozenset[str], pos: Pos, value: Value) -> Swap | None:
    if not legal(eleven, pos):
        return None
    if player in eleven:
        return Swap(False, None, 0.0, eleven)
    count = Counter(pos.get(k, "") for k in eleven)
    count[pos.get(player, "")] += 1
    out = [k for k in eleven if fits(count - Counter({pos[k]: 1}))]
    if not out:
        return Swap(False, None, 0.0, eleven)
    gone = min(out, key=lambda k: (value.get(k, 0.0), k))
    gain = value.get(player, 0.0) - value.get(gone, 0.0)
    return Swap(gain > 0, gone, gain, (eleven - {gone}) | {player} if gain > 0 else eleven)


def settle(eleven: frozenset[str], bench: frozenset[str], pos: Pos, value: Value) -> Settled | None:
    if not legal(eleven, pos):
        return None
    swaps: list[tuple[str, str, float]] = []
    while True:
        tries = [(s, b) for b in bench if (s := swap(b, eleven, pos, value)) is not None and s.improves]
        if not tries:
            return Settled(eleven, bench, tuple(swaps))
        s, b = max(tries, key=lambda t: (t[0].gain, t[1]))
        assert s.replaces is not None
        eleven, bench = s.eleven_after, (bench - {b}) | {s.replaces}
        swaps.append((b, s.replaces, s.gain))


def removal_cost(player: str, eleven: frozenset[str], bench: frozenset[str], pos: Pos, value: Value) -> float | None:
    if not legal(eleven, pos):
        return None
    if player not in eleven:
        return 0.0
    rest = eleven - {player}
    back = [b for b in bench if legal(rest | {b}, pos)]
    if not back:
        return None
    return value.get(player, 0.0) - max(value.get(b, 0.0) for b in back)


def total(eleven: frozenset[str], value: Value) -> float:
    return sum(value.get(k, 0.0) for k in eleven)


def best_total(squad: frozenset[str], pos: Pos, value: Value) -> float | None:
    best: float | None = None
    for pick in itertools.combinations(sorted(squad), XI_SIZE):
        chosen = frozenset(pick)
        if legal(chosen, pos):
            got = total(chosen, value)
            if best is None or got > best:
                best = got
    return best


def random_league(rng: random.Random, ties: bool) -> tuple[frozenset[str], frozenset[str], dict[str, str], dict[str, float]]:
    sizes = {"POR": rng.randint(1, 2), "DEF": rng.randint(3, 6), "MED": rng.randint(3, 6), "DEL": rng.randint(1, 4)}
    pos = {"%s%d" % (p, i): p for p, n in sizes.items() for i in range(n)}
    value = {k: float(rng.randint(0, 6)) if ties else rng.uniform(0, 10) for k in pos}
    shape = rng.choice(SHAPES)
    picked: set[str] = set()
    for p in POSITIONS:
        have = sorted(k for k in pos if pos[k] == p)
        if len(have) < shape[p]:
            return random_league(rng, ties)
        picked |= set(rng.sample(have, shape[p]))
    eleven = frozenset(picked)
    return eleven, frozenset(pos) - eleven, pos, value


def _selftest() -> None:
    rng = random.Random(3)
    wrong = 0
    for case in range(600):
        eleven, bench, pos, value = random_league(rng, ties=case % 2 == 0)
        assert legal(eleven, pos) and len(eleven) == XI_SIZE
        start = total(eleven, value)
        done = settle(eleven, bench, pos, value)
        assert done is not None and legal(done.eleven, pos) and done.eleven | done.bench == eleven | bench
        assert not done.eleven & done.bench and total(done.eleven, value) >= start - 1e-9
        assert abs(total(done.eleven, value) - start - sum(g for _, _, g in done.swaps)) < 1e-9
        again = settle(done.eleven, done.bench, pos, value)
        assert again is not None and again.swaps == (), "a settled eleven has nothing left to swap"
        assert all(not (s := swap(b, done.eleven, pos, value)) or not s.improves for b in done.bench)
        best = best_total(eleven | bench, pos, value)
        assert best is not None
        wrong += abs(best - total(done.eleven, value)) > 1e-9
        for b in bench:
            s = swap(b, eleven, pos, value)
            assert s is not None and legal(s.eleven_after, pos)
            assert s.improves == (s.gain > 0) and (s.replaces in eleven or s.replaces is None)
            if s.improves:
                assert abs(total(s.eleven_after, value) - start - s.gain) < 1e-9
                assert s.eleven_after - {b} | {s.replaces} == eleven, "reversing the swap restores the eleven"
        for k in eleven | bench:
            cost = removal_cost(k, eleven, bench, pos, value)
            if k in bench:
                assert cost == 0.0
            elif cost is not None:
                rest = best_total((eleven - {k}) | bench, pos, value)
                assert rest is not None and rest >= start - cost - 1e-9, "the one-swap cover is never better than the best eleven"
            else:
                assert not any(legal((eleven - {k}) | {b}, pos) for b in bench)
    assert wrong == 0, "settle matched the best legal eleven in every random squad (%d did not)" % wrong
    squad = {"k": "POR", "d1": "DEF", "d2": "DEF", "d3": "DEF", "d4": "DEF", "m1": "MED", "m2": "MED", "m3": "MED",
             "m4": "MED", "f1": "DEL", "f2": "DEL", "star": "DEL", "bench": "DEF", "kb": "POR"}
    value = {k: 4.0 for k in squad} | {"star": 9.0, "f2": 1.0, "bench": 2.0, "kb": 0.5}
    eleven = frozenset({"k", "d1", "d2", "d3", "d4", "m1", "m2", "m3", "m4", "f1", "f2"})
    got = swap("star", eleven, squad, value)
    assert got is not None and got.improves and got.replaces == "f2" and got.gain == 8.0, "a forward replaces the weakest forward"
    got = swap("star", frozenset(eleven - {"f2"} | {"bench"}), squad, value)
    assert got is not None and got.replaces == "bench", "a forward takes the slot a position with slack gives up"
    assert swap("star", frozenset(eleven - {"k"}), squad, value) is None, "no eleven"
    assert settle(frozenset(), frozenset(squad), squad, value) is None and removal_cost("k", frozenset(), frozenset(), squad, value) is None
    assert removal_cost("k", eleven, frozenset({"kb", "star"}), squad, value) == 3.5, "a keeper is covered by the keeper"
    assert removal_cost("k", eleven, frozenset({"star"}), squad, value) is None, "no keeper on the bench: no eleven"
    print("eleven self-test OK")


if __name__ == "__main__" and "--selftest" in sys.argv:
    _selftest()
