"""
ffcore.season — play the rest of the league out, many times.

    st  = LeagueState(squads, fixtures_remaining)
    res = simulate(st, forecaster, trials=2000)
    res.beat("BurtonGM89")     -> P(I finish above him)
    res.position("me")         -> {1: 0.08, 2: 0.19, ...}

WHY A SIMULATION AND NOT A RATE. Every metric this repo has tried — points per
million, value over replacement, an exchange rate for cash — was a proxy for
one question: does this move me up the table. With five managers and their
complete squads visible through the API, that question can be answered
directly instead of approximated, and the answer arrives as a distribution
rather than a number, which is the only honest form for it.

It also removes a whole class of bug. Marginal values are SUBMODULAR: sell two
bench midfielders and the pair is worth less than the sum of the two, because
removing the first raises the second. Any table of independently-computed
marginals is therefore only valid for acting on one row, and this repo printed
four such rows at once. A simulation of the squad you would actually hold
cannot make that mistake — there is nothing to add up.

MANAGERS PICK ON expected() AND SCORE ON draw(). Choosing the eleven from the
sampled outcome would hand everyone perfect foresight and make every squad
look far better than it is; the gap between the two is most of what fantasy
football actually is.

WHAT IT DOES NOT MODEL, and each of these makes it optimistic:

  * Rivals never transfer. They will, and they will improve, so a lead here
    decays slower than in reality.
  * Nobody is injured mid-season beyond what today's P(start) already says.
  * Teammates score independently. Two defenders of one club share a clean
    sheet, so a concentrated squad really has more variance than this shows —
    see the note in ffcore.forecast about why `draw` takes a whole jornada.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from ffcore.score import MAX_SLOT, SLOT_MIN

__all__ = ["LeagueState", "Standings", "simulate", "best_xi"]

XI_SIZE = 11


def legal_shapes() -> list[dict[str, int]]:
    """Every formation the app allows, as counts per position.

    Derived from the same SLOT_MIN/MAX_SLOT the scorer uses rather than typed
    out again: one keeper, and defenders/midfielders/forwards inside their
    bounds adding to eleven. A shape list that disagreed with the scorer's
    would let the simulator field an XI the app would refuse.
    """
    out = []
    for d in range(SLOT_MIN["DEF"], MAX_SLOT["DEF"] + 1):
        for m in range(SLOT_MIN["MED"], MAX_SLOT["MED"] + 1):
            f = XI_SIZE - 1 - d - m
            if SLOT_MIN["DEL"] <= f <= MAX_SLOT["DEL"]:
                out.append({"POR": 1, "DEF": d, "MED": m, "DEL": f})
    return out


SHAPES = legal_shapes()


def best_xi(squad: dict[str, str], value: dict[str, float]) -> list[str]:
    """The eleven worth most under `value`, over every legal shape.

    `squad` is {player key: position}. Greedy within a shape is exact, because
    positions do not interact once the shape is fixed: take the best N of each
    slot. So this is a search over shapes, not over players, and there are
    only a few dozen shapes.

    THIS IS WHERE "you can change your layout" LIVES. A squad with four good
    forwards and three good midfielders is worth what its best shape is worth,
    not what some default 4-4-2 would be — and a position you are thin in
    costs you exactly the difference between the shapes you can and cannot
    field, with no threshold to configure.
    """
    by_slot: dict[str, list[str]] = {}
    for k, slot in squad.items():
        by_slot.setdefault(slot, []).append(k)
    for slot in by_slot:
        by_slot[slot].sort(key=lambda k: -value.get(k, 0.0))

    best, best_total = [], None
    for shape in SHAPES:
        picked, total, ok = [], 0.0, True
        for slot, n in shape.items():
            have = by_slot.get(slot, [])
            if len(have) < n:
                ok = False
                break
            picked += have[:n]
            total += sum(value.get(k, 0.0) for k in have[:n])
        if ok and (best_total is None or total > best_total):
            best, best_total = picked, total
    return best


@dataclass
class LeagueState:
    """Who owns whom, and what is left to play.

    squads: {manager: {player key: position}}
    jornadas: the rounds still to come, in order
    """
    squads: dict[str, dict[str, str]]
    jornadas: list[int]
    me: str = ""
    # Points already on the board. The league does not start from zero, and a
    # rival seven ahead needs seven fewer to beat you — small against a ±120
    # season band, but it is the difference between simulating this league and
    # simulating a hypothetical one.
    carried: dict[str, float] = field(default_factory=dict)


@dataclass
class Standings:
    """Simulated final totals, one row per trial per manager."""
    totals: dict[str, list[float]] = field(default_factory=dict)
    me: str = ""

    @property
    def trials(self) -> int:
        return len(next(iter(self.totals.values()))) if self.totals else 0

    def mean(self, manager: str) -> float:
        v = self.totals.get(manager) or [0.0]
        return sum(v) / len(v)

    def band(self, manager: str, lo=0.1, hi=0.9) -> tuple[float, float]:
        """Central interval. The number people actually need, because a mean
        with no spread beside it reads as a prediction."""
        v = sorted(self.totals.get(manager) or [0.0])
        n = len(v)
        return v[max(0, int(lo * n))], v[min(n - 1, int(hi * n))]

    def beat(self, rival: str, manager: str = "") -> float:
        """P(manager finishes strictly above rival). Ties count as halves —
        a dead heat is not a win and pretending otherwise flatters you."""
        me = manager or self.me
        a, b = self.totals.get(me), self.totals.get(rival)
        if not a or not b:
            return 0.0
        wins = sum((x > y) + 0.5 * (x == y) for x, y in zip(a, b))
        return wins / len(a)

    def position(self, manager: str = "") -> dict[int, float]:
        """{final position: probability}."""
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


def simulate(state: LeagueState, forecaster, trials: int = 2000,
             seed: int = 0) -> Standings:
    """Play the remaining jornadas `trials` times.

    THE SAME SEED IS THE SAME SEASON for every call, which is what makes two
    candidate squads comparable: the difference between them is then the
    squads, not the weather. Comparing a buy against a hold on independently
    drawn seasons would bury a one-point edge under a ±75-point spread.
    """
    managers = list(state.squads)
    totals = {m: [float(state.carried.get(m, 0.0))] * trials
              for m in managers}

    # The eleven each manager fields depends only on expectations, so it is
    # the same in every trial and is picked once rather than `trials` times.
    xis: dict[int, dict[str, list[str]]] = {}
    for j in state.jornadas:
        exp = forecaster.expected(j)
        xis[j] = {m: best_xi(sq, exp) for m, sq in state.squads.items()}

    for t in range(trials):
        rng = random.Random(seed * 1_000_003 + t)
        for j in state.jornadas:
            drawn = forecaster.draw(j, rng)
            for m in managers:
                totals[m][t] += sum(drawn.get(k, 0.0) for k in xis[j][m])

    return Standings(totals=totals, me=state.me)


def _selftest() -> None:
    from ffcore.forecast import Bootstrap

    # -- shapes -------------------------------------------------------------
    assert all(sum(s.values()) == XI_SIZE for s in SHAPES), SHAPES
    assert {"POR": 1, "DEF": 4, "MED": 4, "DEL": 2} in SHAPES
    assert {"POR": 1, "DEF": 5, "MED": 4, "DEL": 1} in SHAPES
    # 6 defenders is not a formation the app will accept.
    assert not any(s["DEF"] > MAX_SLOT["DEF"] for s in SHAPES)

    # -- best_xi ------------------------------------------------------------
    squad = {"k": "POR", "d1": "DEF", "d2": "DEF", "d3": "DEF", "d4": "DEF",
             "d5": "DEF", "m1": "MED", "m2": "MED", "m3": "MED", "m4": "MED",
             "m5": "MED", "f1": "DEL", "f2": "DEL", "f3": "DEL"}
    val = {k: 1.0 for k in squad}
    val.update({"f1": 9.0, "f2": 9.0, "f3": 9.0})
    xi = best_xi(squad, val)
    assert len(xi) == XI_SIZE, xi
    # THE SHAPE BENDS TO THE SQUAD: three good forwards means a shape that
    # starts three, without anybody configuring a preference.
    assert {"f1", "f2", "f3"} <= set(xi), xi

    # Too few keepers and there is no legal eleven at all — reported as empty
    # rather than as a ten-man team that would silently score less.
    assert best_xi({"d1": "DEF"}, {"d1": 1.0}) == []

    # -- simulate -----------------------------------------------------------
    # Two managers, identical squads, one jornada: a coin flip by construction.
    sq = {"k": "POR", **{f"d{i}": "DEF" for i in range(1, 6)},
          **{f"m{i}": "MED" for i in range(1, 6)}, "f1": "DEL"}
    a = {f"a_{k}": v for k, v in sq.items()}
    b = {f"b_{k}": v for k, v in sq.items()}
    per = {1: {k: (3.0, 1.0) for k in list(a) + list(b)}}
    st = LeagueState(squads={"A": a, "B": b}, jornadas=[1], me="A")
    res = simulate(st, Bootstrap(per), trials=600, seed=1)
    assert res.trials == 600
    assert 0.35 < res.beat("B") < 0.65, res.beat("B")
    # Eleven players at 3.0 each.
    assert abs(res.mean("A") - 33.0) / 33.0 < 0.1, res.mean("A")

    # A strictly better squad wins nearly always, and the band says so.
    per2 = {1: {k: ((9.0 if k.startswith("a_") else 1.0), 1.0)
                for k in list(a) + list(b)}}
    res2 = simulate(st, Bootstrap(per2), trials=600, seed=1)
    assert res2.beat("B") > 0.95, res2.beat("B")
    lo, hi = res2.band("A")
    assert lo < res2.mean("A") < hi, (lo, res2.mean("A"), hi)
    assert lo > res2.band("B")[1], "the bands should not overlap here"

    # position() is a distribution and sums to one.
    p = res2.position("A")
    assert abs(sum(p.values()) - 1.0) < 1e-9, p
    assert p.get(1, 0) > 0.95, p
    assert 1.0 <= res2.expected_position("A") < 1.1

    # POINTS ALREADY SCORED COUNT. A rival who cannot be caught should not
    # come out at 50%.
    far = LeagueState(squads={"A": a, "B": b}, jornadas=[1], me="A",
                      carried={"A": 0.0, "B": 500.0})
    rf = simulate(far, Bootstrap(per), trials=200, seed=1)
    assert rf.beat("B") == 0.0, rf.beat("B")
    assert rf.mean("B") - rf.mean("A") > 400

    # A tie is half a win, not a win.
    tied = Standings(totals={"A": [5.0], "B": [5.0]}, me="A")
    assert tied.beat("B") == 0.5

    # SAME SEED, SAME SEASON — the property that makes two options comparable.
    r1 = simulate(st, Bootstrap(per), trials=200, seed=4)
    r2 = simulate(st, Bootstrap(per), trials=200, seed=4)
    assert r1.totals == r2.totals

    print("ffcore.season self-test OK (22 cases)")


if __name__ == "__main__":
    _selftest()
