from __future__ import annotations

from functools import cached_property
from typing import Mapping, NamedTuple

from ffcore.forecast import Bootstrap
from ffcore.season import LeagueState, best_xi

__all__ = ["Outlook", "XI"]


class XI(NamedTuple):
    expected: dict[str, float]
    players: set[str]

    def ranked(self) -> list[str]:
        return sorted(self.players, key=lambda k: (-self.expected.get(k, 0.0), k))


class Outlook:
    """What each player is expected to score, and the eleven that makes the
    most of it. Points only: what anything costs is the market's business."""

    def __init__(self, state: LeagueState, forecaster: Bootstrap,
                 pos: Mapping[str, str] | None = None,
                 part_played: Mapping[int, set[str]] | None = None,
                 first_jornada_of: Mapping[str, int] | None = None):
        self.state, self.forecaster = state, forecaster
        self.pos = pos or {}
        self.part_played = part_played or {}
        self.first_jornada_of = first_jornada_of or {}
        self._totals: dict[frozenset, float] = {}

    @cached_property
    def next_up(self) -> dict[str, tuple[float, float]]:
        per_j = self.forecaster.per_jornada
        if not self.first_jornada_of:
            j = next((j for j in self.state.jornadas
                      if j not in self.part_played),
                     self.state.jornadas[0] if self.state.jornadas else 0)
            return dict(per_j.get(j, {}))
        return {k: per_j[j][k] for k, j in self.first_jornada_of.items()
                if k in per_j.get(j, {})}

    @cached_property
    def xi(self) -> XI:
        exp = {k: pts * p for k, (pts, p) in self.next_up.items()}
        return XI(exp, set(best_xi(self.state.squads.get(self.state.me, {}), exp)))

    @cached_property
    def season(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for exp in self._expected.values():
            for k, pts in exp.items():
                out[k] = out.get(k, 0.0) + pts
        return out

    @cached_property
    def _expected(self) -> dict[int, dict[str, float]]:
        return {j: self.forecaster.expected(j) for j in self.state.jornadas}

    def total(self, squad: Mapping[str, str]) -> float:
        """What this squad's best eleven is expected to score in the
        jornadas still to start (a move made now changes nothing in one
        under way). A player is worth what he changes it by: his points
        above whoever would play instead."""
        key = frozenset(squad.items())
        if key not in self._totals:
            sq = dict(squad)
            self._totals[key] = sum(sum(e.get(k, 0.0) for k in best_xi(sq, e))
                                    for j, e in self._expected.items()
                                    if j not in self.part_played)
        return self._totals[key]


def _selftest() -> None:
    from ffcore.forecast import Bootstrap

    squad = {"gk": "POR", **{f"d{i}": "DEF" for i in range(4)},
             **{f"m{i}": "MED" for i in range(5)}, "f": "DEL", "bench": "MED"}
    per = {j: {**{k: (3.0, 1.0) for k in squad}, "bench": (1.0, 1.0),
               "star": (9.0, 0.5)} for j in (1, 2)}
    o = Outlook(LeagueState({"me": squad}, [1, 2], "me"), Bootstrap(per))
    assert o.xi is o.xi, "cached_property must not recompute"
    assert len(o.xi.players) == 11 and "bench" not in o.xi.players, o.xi
    exp, players = o.xi
    assert exp["star"] == 4.5 and players == o.xi.players
    ranked = XI({"a": 1.0, "b": 3.0, "c": 3.0}, {"c", "a", "b"}).ranked()
    assert ranked == ["b", "c", "a"], ranked
    assert o.season["m0"] == 6.0 and o.season["star"] == 9.0, o.season
    assert o.total(squad) == 2 * 33.0, o.total(squad)
    with_star = {**{k: v for k, v in squad.items() if k != "m0"}, "star": "MED"}
    assert o.total(with_star) - o.total(squad) == 2 * (4.5 - 3.0), \
        "a player is worth his points over the one he displaces"
    assert o.total({k: v for k, v in squad.items() if k != "m0"}) - o.total(squad) == -2 * 2.0, \
        "sold, the bench man plays instead"

    played = Outlook(LeagueState({"me": squad}, [1, 2], "me"), Bootstrap(per),
                     part_played={1: {"x"}})
    assert played.next_up == per[2]
    firsts = Outlook(LeagueState({"me": squad}, [1, 2], "me"), Bootstrap(per),
                     first_jornada_of={"star": 2})
    assert firsts.next_up == {"star": (9.0, 0.5)}, firsts.next_up

    empty = Outlook(LeagueState({}, [], "me"), Bootstrap({}))
    assert empty.xi == XI({}, set()) and empty.total({}) == 0.0
    print("ffcore.outlook self-test OK")


if __name__ == "__main__":
    _selftest()
