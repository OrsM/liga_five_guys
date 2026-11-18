"""The world as of a cut (ingest): the composition of independent per-fact folds over the observations.

A Fold is one fact's: the table it reads, start() its empty state, and step(state, row) -> state, applied to
the table's rows in (observed_at, rowid) order. A step may update its state in place. register(fold) adds it
(one fold per fact name); a new fact is a new module that registers, nothing here is edited. A World is the
cut and each fact's state. sweep(obs, cuts, read) folds forward once through the sorted cuts and returns
read(world) for each cut in the order given: the result at a cut is the same alone, in any subset, in any
order, and does not change when rows with observed_at after it are appended. world_at(obs, cut) is the
World at one cut (its states are its own). Rows with observed_at <= cut count, as the current_* views do.

The first facts: latest_<table> for every table, the rows the table's current_* view holds: each source's
newest snapshot (snapshot tables) or each key's newest row, a key with an empty part excluded (keyed tables).
Selectors: fact, latest."""
from __future__ import annotations

import bisect
from collections.abc import Callable, Iterable, Mapping
from typing import Any, NamedTuple, TypeVar

from ffcore import observations as ob

T = TypeVar("T")


class Fold(NamedTuple):
    fact: str
    table: str
    start: Callable[[], Any]
    step: Callable[[Any, Any], Any]


class World(NamedTuple):
    cut: str
    facts: Mapping[str, Any]


FOLDS: dict[str, Fold] = {}


def register(fold: Fold) -> Fold:
    """fold, added to FOLDS: one per fact name."""
    assert fold.fact not in FOLDS, "fact %s is registered twice" % fold.fact
    FOLDS[fold.fact] = fold
    return fold


def fact(world: World, name: str) -> Any:
    """The state of fact name in world."""
    return world.facts[name]


def sweep(obs: ob.Observations, cuts: Iterable[str], read: Callable[[World], T],
          folds: Mapping[str, Fold] | None = None) -> list[T]:
    """read(world at c) for each cut c in cuts, in the order given; the folds (all registered by default) run
    forward once over the sorted distinct cuts."""
    folds = FOLDS if folds is None else folds
    states = {name: f.start() for name, f in folds.items()}
    done = dict.fromkeys(folds, 0)
    out: dict[str, T] = {}
    asked = list(cuts)
    for cut in sorted(set(asked)):
        for name, f in folds.items():
            rows, stamps = ob.rows_of(obs, f.table), ob.stamps_of(obs, f.table)
            end = bisect.bisect_right(stamps, cut)
            for row in rows[done[name]:end]:
                states[name] = f.step(states[name], row)
            done[name] = max(done[name], end)
        out[cut] = read(World(cut, states))
    return [out[c] for c in asked]


def world_at(obs: ob.Observations, cut: str, folds: Mapping[str, Fold] | None = None) -> World:
    """The World at cut: each fold's state after the rows with observed_at <= cut."""
    return sweep(obs, [cut], lambda w: World(w.cut, dict(w.facts)), folds)[0]


def latest_fact(table: str) -> str:
    """The name of the fact holding the rows of table's current_* view."""
    return "latest_" + table


def _snapshot_step(state: dict[str, Any], row: Any) -> dict[str, Any]:
    held = state.get(row.source)
    if held is None or held[0] != row.observed_at:
        state[row.source] = (row.observed_at, [row])
    else:
        held[1].append(row)
    return state


def _keyed_step(table: str) -> Callable[[dict[Any, Any], Any], dict[Any, Any]]:
    def step(state: dict[Any, Any], row: Any) -> dict[Any, Any]:
        key = ob.key_of(table, row)
        if "" not in key:
            state[key] = row
        return state
    return step


def latest(world: World, table: str) -> tuple[Any, ...]:
    """The rows of table's current_* view at the world's cut (order unspecified)."""
    state = fact(world, latest_fact(table))
    if ob.TABLES[table].grain:
        return tuple(state.values())
    return tuple(r for _, rows in state.values() for r in rows)


for _table, _source in ob.TABLES.items():
    register(Fold(latest_fact(_table), _table, dict, _keyed_step(_table) if _source.grain else _snapshot_step))


def _selftest() -> None:
    """Appending rows after a cut leaves the world at it unchanged; any subset or order of cuts gives each
    cut the same world; a cut alone equals the cut inside a sweep; a snapshot table holds each source's
    newest snapshot only and a keyed table each key's newest row; out-of-order input is counted."""
    def mk(at: str, ff: str, v: float, source: str = "ff") -> ob.Market:
        return ob.Market(at, source, v, ff, "DEL", "RMA")

    def mt(at: str, mid: str, score: str) -> ob.Matches:
        return ob.Matches(at, "s", 1, mid, "", "a", "b", score, "")
    market = [mk("01", "a", 1), mk("01", "b", 2), mk("02", "a", 3), mk("02", "b", 4), mk("03", "a", 5),
              mk("02", "z", 9, "other")]
    matches = [mt("01", "m1", ""), mt("02", "m1", "1-0"), mt("02", "", "x"), mt("03", "m2", "")]
    obs = ob.of({"market": market, "matches": matches})
    only = {n: f for n, f in FOLDS.items() if n in ("latest_market", "latest_matches")}

    def snap(w: World) -> Any:
        return w.cut, sorted(latest(w, "market")), sorted(latest(w, "matches"))
    cuts = ["00", "01", "02", "03", "04"]
    alone = {c: world_at(obs, c, only) for c in cuts}
    assert {c: snap(w) for c, w in alone.items()} == {c: s for c, s in zip(cuts, sweep(obs, cuts, snap, only))}
    for order in (cuts[::-1], ["02", "00", "03", "02"], ["03"], ["01", "03"]):
        assert sweep(obs, order, snap, only) == [snap(alone[c]) for c in order]
    later = ob.of({"market": market + [mk("04", "a", 7), mk("05", "q", 1)], "matches": matches + [mt("04", "m1", "2-0")]})
    assert all(snap(world_at(later, c, only)) == snap(alone[c]) for c in cuts[:4])
    assert snap(alone["02"])[1] == sorted([mk("02", "a", 3), mk("02", "b", 4), mk("02", "z", 9, "other")])
    assert snap(alone["03"])[1] == sorted([mk("03", "a", 5), mk("02", "z", 9, "other")])
    assert snap(alone["03"])[2] == sorted([mt("02", "m1", "1-0"), mt("03", "m2", "")])
    assert snap(alone["00"])[1:] == ([], [])
    assert ob.refolds_of(obs) == 0 and ob.refolds_of(ob.of({"market": market[::-1]})) > 0
    try:
        register(FOLDS["latest_market"])
    except AssertionError:
        pass
    else:
        raise AssertionError("a fact registered twice")
    print("ffcore.state self-test OK")


if __name__ == "__main__":
    _selftest()
