
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ffcore.rules import FREE_FORMATIONS, MAX_SLOT
from stats import percentile

__all__ = ["LeagueState", "Standings", "expected_totals", "simulate",
           "simulate_many", "best_xi"]

XI_SIZE = 11
SHAPES = [{"POR": 1, "DEF": d, "MED": m, "DEL": f} for d, m, f in FREE_FORMATIONS]


def best_xi(squad: dict[str, str], value: dict[str, float]) -> list[str]:
    by_slot: dict[str, list[float]] = {}
    for k, slot in squad.items():
        by_slot.setdefault(slot, []).append((value.get(k, 0.0), k))
    for rows in by_slot.values():
        rows.sort(key=lambda vk: -vk[0])
    best = None
    for shape in SHAPES:
        if all(len(by_slot.get(slot, ())) >= n for slot, n in shape.items()):
            picked = [vk for slot, n in shape.items() for vk in by_slot[slot][:n]]
            total = sum(v for v, _k in picked)
            if best is None or total > best[0]:
                best = (total, [k for _v, k in picked])
    return best[1] if best else []


@dataclass
class LeagueState:
    """The squads, the jornadas left and the points banked. A jornada under
    way is played by the squads that fielded it (fielded), whatever moves
    made since did to squads."""
    squads: dict[str, dict[str, str]]
    jornadas: list[int]
    me: str = ""
    carried: dict[str, float] = field(default_factory=dict)
    fielded: dict[int, dict[str, dict[str, str]]] = field(default_factory=dict)


@dataclass
class Standings:
    totals: dict[str, list[float]] = field(default_factory=dict)
    me: str = ""

    @property
    def trials(self) -> int:
        return len(next(iter(self.totals.values()))) if self.totals else 0

    def mean(self, manager: str) -> float:
        v = self.totals.get(manager) or [0.0]
        return sum(v) / len(v)

    def band(self, manager: str, lo=0.1, hi=0.9) -> tuple[float, float]:
        v = self.totals.get(manager) or [0.0]
        return percentile(v, lo * 100), percentile(v, hi * 100)

    def beat(self, rival: str, manager: str = "") -> float:
        me = manager or self.me
        a, b = self.totals.get(me), self.totals.get(rival)
        if not a or not b:
            return 0.0
        wins = sum((x > y) + 0.5 * (x == y) for x, y in zip(a, b))
        return wins / len(a)

    def position(self, manager: str = "") -> dict[int, float]:
        me = manager or self.me
        if me not in self.totals:
            return {}
        others = [m for m in self.totals if m != me]
        out: dict[int, float] = {}
        for i in range(self.trials):
            mine = self.totals[me][i]
            rank = 1 + sum(1 for o in others if self.totals[o][i] > mine)
            out[rank] = out.get(rank, 0) + 1
        return {k: v / self.trials for k, v in sorted(out.items())}

    def expected_position(self, manager: str = "") -> float:
        return sum(k * p for k, p in self.position(manager).items())


def simulate_many(states: list, forecaster, trials: int = 2000,
                  seed: int = 0) -> list:
    if not states:
        return []
    fast = _run_np(states, forecaster, trials, seed)
    return [Standings(totals=tot, me=st.me)
            for tot, st in zip(fast, states)]


def draws(forecaster, jornadas, trials: int, seed: int):

    pool = np.asarray(forecaster.pool, dtype=float)
    everyone = sorted({k for j in jornadas for k in forecaster.order(j)})
    col = {k: i for i, k in enumerate(everyone)}
    rate = np.clip(1.0 + forecaster.share * np.random.default_rng(
        [seed, 7919]).standard_normal((trials, len(everyone))), 0.0, None)
    for j in jornadas:
        keys = forecaster.order(j)
        if not keys:
            continue
        per = forecaster.per_jornada[j]
        pts = np.array([per[k][0] for k in keys], dtype=float)
        p = np.array([per[k][1] for k in keys], dtype=float)
        rng = np.random.default_rng([seed, j])
        yield j, keys, np.where(
            rng.random((trials, len(keys))) < p[None, :],
            pool[rng.integers(0, len(pool), (trials, len(keys)))]
            * (pts / forecaster.pool_mean) * rate[:, [col[k] for k in keys]], 0.0)


def _elevens(states: list, forecaster):
    """Each state's eleven per manager and jornada: the best by expected
    points, as the manager would field them."""
    exp_by_j = {}
    xi_memo: dict = {}
    xis = []
    for st in states:
        per_state = {}
        for j in st.jornadas:
            if j not in exp_by_j:
                exp_by_j[j] = forecaster.expected(j)
            row = {}
            for m, sq in st.squads.items():
                sq = st.fielded.get(j, {}).get(m, sq)
                k = (j, tuple(sorted(sq.items())))
                got = xi_memo.get(k)
                if got is None:
                    got = xi_memo[k] = best_xi(sq, exp_by_j[j])
                row[m] = got
            per_state[j] = row
        xis.append(per_state)
    return xis, exp_by_j


def expected_totals(states: list, forecaster) -> list[dict[str, float]]:
    """What each manager is expected to finish on: the mean of the seasons
    simulate_many draws, worked out exactly rather than sampled (the same
    elevens, each player's expected points)."""
    xis, exp_by_j = _elevens(states, forecaster)
    return [{m: st.carried.get(m, 0.0) + sum(
                sum(exp_by_j[j].get(k, 0.0) for k in xi[j][m]) for j in st.jornadas)
             for m in st.squads} for st, xi in zip(states, xis)]


def _run_np(states: list, forecaster, trials: int, seed: int):

    managers = [list(st.squads) for st in states]
    totals = [{m: np.full(trials, float(st.carried.get(m, 0.0)))
               for m in ms} for st, ms in zip(states, managers)]
    xis, _exp = _elevens(states, forecaster)
    for j, keys, drawn in draws(forecaster, states[0].jornadas, trials, seed):
        at = {k: i for i, k in enumerate(keys)}
        for i in range(len(states)):
            for m in managers[i]:
                idx = [at[k] for k in xis[i][j][m] if k in at]
                if idx:
                    totals[i][m] += drawn[:, idx].sum(axis=1)
    return [{m: v.tolist() for m, v in tot.items()} for tot in totals]


def simulate(state: LeagueState, forecaster, trials: int = 2000,
             seed: int = 0) -> Standings:
    return simulate_many([state], forecaster, trials=trials, seed=seed)[0]


def _selftest() -> None:

    from ffcore.forecast import Bootstrap

    assert all(sum(s.values()) == XI_SIZE for s in SHAPES), SHAPES
    assert {"POR": 1, "DEF": 4, "MED": 4, "DEL": 2} in SHAPES
    assert {"POR": 1, "DEF": 5, "MED": 4, "DEL": 1} in SHAPES
    assert not any(s["DEF"] > MAX_SLOT["DEF"] for s in SHAPES)

    squad = {"k": "POR", "d1": "DEF", "d2": "DEF", "d3": "DEF", "d4": "DEF",
             "d5": "DEF", "m1": "MED", "m2": "MED", "m3": "MED", "m4": "MED",
             "m5": "MED", "f1": "DEL", "f2": "DEL", "f3": "DEL"}
    val = {k: 1.0 for k in squad}
    val.update({"f1": 9.0, "f2": 9.0, "f3": 9.0})
    xi = best_xi(squad, val)
    assert len(xi) == XI_SIZE, xi
    assert {"f1", "f2", "f3"} <= set(xi), xi

    assert best_xi({"d1": "DEF"}, {"d1": 1.0}) == []

    sq = {"k": "POR", **{f"d{i}": "DEF" for i in range(1, 6)},
          **{f"m{i}": "MED" for i in range(1, 6)}, "f1": "DEL"}
    a = {f"a_{k}": v for k, v in sq.items()}
    b = {f"b_{k}": v for k, v in sq.items()}
    per = {1: {k: (3.0, 1.0) for k in list(a) + list(b)}}
    st = LeagueState(squads={"A": a, "B": b}, jornadas=[1], me="A")
    res = simulate(st, Bootstrap(per), trials=600, seed=1)
    assert res.trials == 600
    assert 0.35 < res.beat("B") < 0.65, res.beat("B")
    assert abs(res.mean("A") - 33.0) / 33.0 < 0.1, res.mean("A")

    per2 = {1: {k: ((9.0 if k.startswith("a_") else 1.0), 1.0)
                for k in list(a) + list(b)}}
    res2 = simulate(st, Bootstrap(per2), trials=600, seed=1)
    assert res2.beat("B") > 0.95, res2.beat("B")
    lo, hi = res2.band("A")
    assert lo < res2.mean("A") < hi, (lo, res2.mean("A"), hi)
    assert lo > res2.band("B")[1], "the bands should not overlap here"

    p = res2.position("A")
    assert abs(sum(p.values()) - 1.0) < 1e-9, p
    assert p.get(1, 0) > 0.95, p
    assert 1.0 <= res2.expected_position("A") < 1.1

    far = LeagueState(squads={"A": a, "B": b}, jornadas=[1], me="A",
                      carried={"A": 0.0, "B": 500.0})
    rf = simulate(far, Bootstrap(per), trials=200, seed=1)
    assert rf.beat("B") == 0.0, rf.beat("B")
    assert rf.mean("B") - rf.mean("A") > 400

    mixed = {1: {k: ((6.0, 0.5) if k.endswith("1") else (3.0, 0.9))
                 for k in list(a) + list(b)}, 2: per2[1]}
    two_j = LeagueState(squads={"A": a, "B": b}, jornadas=[1, 2], me="A",
                        carried={"A": 10.0})
    exact = expected_totals([two_j], Bootstrap(mixed))[0]
    sampled = simulate(two_j, Bootstrap(mixed), trials=20000, seed=5)
    assert all(abs(sampled.mean(m) - exact[m]) / exact[m] < 0.01 for m in exact), \
        ("the exact mean is the simulation's", exact, sampled.mean("A"), sampled.mean("B"))

    swapped = {**{k: v for k, v in a.items() if k != "a_m1"}, "b_m1": "MED"}
    under_way = LeagueState(squads={"A": swapped, "B": b}, jornadas=[1, 2], me="A",
                            fielded={1: {"A": a}})
    as_was = LeagueState(squads={"A": a, "B": b}, jornadas=[1, 2], me="A")
    moved = LeagueState(squads={"A": swapped, "B": b}, jornadas=[1, 2], me="A")
    ew, ea, em = expected_totals([under_way, as_was, moved], Bootstrap(mixed))
    assert ew["A"] - ea["A"] == (em["A"] - ea["A"]) / 2, \
        "a move changes the jornada still to start, not the one under way"

    tied = Standings(totals={"A": [5.0], "B": [5.0]}, me="A")
    assert tied.beat("B") == 0.5

    r1 = simulate(st, Bootstrap(per), trials=200, seed=4)
    r2 = simulate(st, Bootstrap(per), trials=200, seed=4)
    assert r1.totals == r2.totals

    b2 = {f"b_{k}": v for k, v in sq.items()}
    alt = LeagueState(squads={"A": dict(a), "B": dict(b2)}, jornadas=[1],
                      me="A")
    one = simulate(st, Bootstrap(per), trials=150, seed=9)
    two = simulate(alt, Bootstrap(per), trials=150, seed=9)
    both = simulate_many([st, alt], Bootstrap(per), trials=150, seed=9)
    assert both[0].totals == one.totals, "draw order changed"
    assert both[1].totals == two.totals, "draw order changed"
    assert simulate_many([], Bootstrap(per), trials=10) == []

    solo = LeagueState(squads={"A": dict(a), "C": dict(b2)}, jornadas=[1],
                       me="A")
    got = simulate_many([st, solo], Bootstrap(per), trials=120, seed=3)
    want = simulate(solo, Bootstrap(per), trials=120, seed=3)
    assert got[1].totals == want.totals, "a new manager must be scored fully"

    again1 = simulate_many([st, alt], Bootstrap(per), trials=500, seed=2)
    again2 = simulate_many([st, alt], Bootstrap(per), trials=500, seed=2)
    assert [x.totals for x in again1] == [x.totals for x in again2], \
        "same seed, same seasons"


    print("ffcore.season self-test OK")


if __name__ == "__main__":
    _selftest()
