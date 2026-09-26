
from __future__ import annotations

from dataclasses import dataclass, field

from ffcore.score import MAX_SLOT, formations, _xi_search

__all__ = ["LeagueState", "Standings", "simulate",
           "simulate_many", "best_xi"]

XI_SIZE = 11


SHAPES = [{"POR": 1, "DEF": d, "MED": m, "DEL": f}
         for d, m, f in formations()]


def best_xi(squad: dict[str, str], value: dict[str, float]) -> list[str]:
    by_slot: dict[str, list[tuple]] = {}
    for k, slot in squad.items():
        by_slot.setdefault(slot, []).append((k, value.get(k, 0.0)))
    for slot in by_slot:
        by_slot[slot].sort(key=lambda kv: -kv[1])

    got = _xi_search(by_slot, SHAPES)
    return got[2] if got else []


@dataclass
class LeagueState:
    squads: dict[str, dict[str, str]]
    jornadas: list[int]
    me: str = ""
    carried: dict[str, float] = field(default_factory=dict)


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
        from stats import percentile
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


def _antithetic_normal(rng, shape: tuple):
    import numpy as np

    trials = shape[0]
    half = trials // 2
    if half == 0:
        return rng.standard_normal(shape)
    z = rng.standard_normal((half,) + shape[1:])
    parts = [z, -z]
    if trials % 2:
        parts.append(rng.standard_normal((1,) + shape[1:]))
    return np.concatenate(parts, axis=0)


def simulate_many(states: list, forecaster, trials: int = 2000,
                  seed: int = 0, antithetic: bool = False) -> list:
    if not states:
        return []
    fast = _run_np(states, forecaster, trials, seed, antithetic)
    return [Standings(totals=tot, me=st.me)
            for tot, st in zip(fast, states)]


def _run_np(states: list, forecaster, trials: int, seed: int,
           antithetic: bool = False):
    try:
        import numpy as np
    except ImportError:
        return None

    order = getattr(forecaster, "_order", None)
    per_j = getattr(forecaster, "per_jornada", None)
    pool = getattr(forecaster, "pool", None)
    mean = getattr(forecaster, "_pool_mean", None)
    if order is None or per_j is None or not pool:
        return None

    pool_a = np.asarray(pool, dtype=float)
    rel = getattr(forecaster, "rate_rel", None) or {}
    club_of = getattr(forecaster, "club_of", None) or {}
    club_rel = getattr(forecaster, "club_rel", None) or {}
    all_keys = sorted({k for ks in order.values() for k in ks} & set(rel)) \
        if rel else []
    draw_normal = _antithetic_normal if antithetic else \
        (lambda rng, shape: rng.standard_normal(shape))
    eps0 = shared = drng = cum_var = walk = None
    if all_keys:
        from ffcore.forecast import DRIFT_FRAC

        rrng = np.random.default_rng([seed, 7919])
        sd = np.array([rel[k] for k in all_keys], dtype=float)
        eps0 = np.clip(
            1.0 + draw_normal(rrng, (trials, len(all_keys))) * sd,
            0.0, None)
        clubs = sorted(club_rel)
        if clubs:
            crng = np.random.default_rng([seed, 7920])
            csd = np.array([club_rel[c] for c in clubs], dtype=float)
            shock = np.clip(
                1.0 + draw_normal(crng, (trials, len(clubs))) * csd,
                0.0, None)
            shock_of = {c: shock[:, i] for i, c in enumerate(clubs)}
            ones = np.ones(trials)
            shared = np.stack(
                [shock_of.get(club_of.get(k, ""), ones) for k in all_keys],
                axis=1)
        drng = np.random.default_rng([seed, 7921])
        step_sd = DRIFT_FRAC * sd
        cum_var = np.zeros(len(all_keys))
        walk = np.zeros((trials, len(all_keys)))

    srel = getattr(forecaster, "start_rel", None) or {}
    all_start_keys = sorted(
        {k for ks in order.values() for k in ks} & set(srel)) if srel else []
    seps0 = sdrng = None
    if all_start_keys:
        from ffcore.forecast import DRIFT_FRAC as _DF

        srrng = np.random.default_rng([seed, 7927])
        ssd = np.array([srel[k] for k in all_start_keys], dtype=float)
        seps0 = draw_normal(srrng, (trials, len(all_start_keys))) * ssd
        sdrng = np.random.default_rng([seed, 7928])
        step_sd_s = _DF * ssd
        walk_s = np.zeros((trials, len(all_start_keys)))

    managers = [list(st.squads) for st in states]
    totals = [{m: np.full(trials, float(st.carried.get(m, 0.0)))
               for m in ms} for st, ms in zip(states, managers)]

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
                k = (j, tuple(sorted(sq.items())))
                got = xi_memo.get(k)
                if got is None:
                    got = xi_memo[k] = best_xi(sq, exp_by_j[j])
                row[m] = got
            per_state[j] = row
        xis.append(per_state)

    rate_mult = {}
    for j in states[0].jornadas:
        if all_keys:
            step_var = step_sd ** 2
            walk += draw_normal(drng, (trials, len(all_keys))) \
                * step_sd
            cum_var += step_var
            walked = np.exp(walk - cum_var / 2.0)
            individual = eps0 * walked
            m = individual * shared if shared is not None else individual
            rate_mult = {k: m[:, i] for i, k in enumerate(all_keys)}
        start_shift = {}
        if all_start_keys:
            walk_s += draw_normal(sdrng, (trials, len(all_start_keys))) \
                * step_sd_s
            shifted = seps0 + walk_s
            start_shift = {k: shifted[:, i]
                           for i, k in enumerate(all_start_keys)}
        keys = order.get(j, [])
        if not keys:
            continue
        at = {k: i for i, k in enumerate(keys)}
        per = per_j[j]
        pts = np.array([per[k][0] for k in keys], dtype=float)
        p = np.array([per[k][1] for k in keys], dtype=float)
        rng = np.random.default_rng([seed, j])
        scale = np.ones((trials, len(keys))) if not rate_mult else np.stack(
            [rate_mult[k] if k in rate_mult else np.ones(trials)
             for k in keys], axis=1)
        if start_shift:
            from ffcore.startprob import FLOOR, CEIL

            shift = np.stack(
                [start_shift[k] if k in start_shift else np.zeros(trials)
                 for k in keys], axis=1)
            p_clip = np.clip(p, 1e-6, 1.0 - 1e-6)
            logit_p = np.log(p_clip / (1.0 - p_clip))
            p_trial = 1.0 / (1.0 + np.exp(-(logit_p[None, :] + shift)))
            p_trial = np.clip(p_trial, FLOOR, CEIL)
        else:
            p_trial = p[None, :]
        drawn = np.where(rng.random((trials, len(keys))) < p_trial,
                         pool_a[rng.integers(0, len(pool_a),
                                             (trials, len(keys)))]
                         * (pts / mean) * scale, 0.0)
        for i in range(len(states)):
            for m in managers[i]:
                idx = [at[k] for k in xis[i][j][m] if k in at]
                if idx:
                    totals[i][m] += drawn[:, idx].sum(axis=1)
    return [{m: v.tolist() for m, v in tot.items()} for tot in totals]


def simulate(state: LeagueState, forecaster, trials: int = 2000,
             seed: int = 0, antithetic: bool = False) -> Standings:
    return simulate_many([state], forecaster, trials=trials, seed=seed,
                         antithetic=antithetic)[0]


def _selftest() -> None:
    import statistics

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

    tied = Standings(totals={"A": [5.0], "B": [5.0]}, me="A")
    assert tied.beat("B") == 0.5

    r1 = simulate(st, Bootstrap(per), trials=200, seed=4)
    r2 = simulate(st, Bootstrap(per), trials=200, seed=4)
    assert r1.totals == r2.totals

    matches = {k: 20 for k in list(a) + list(b)}
    club_of_a = {f"a_{k}": "OneClub" for k in sq}
    baseline_fc = Bootstrap(per, matches=matches)
    correlated_fc = Bootstrap(per, matches=matches, club_of=club_of_a,
                              club_rel={"OneClub": 0.6})
    base_res = simulate(st, baseline_fc, trials=1500, seed=11)
    corr_res = simulate(st, correlated_fc, trials=1500, seed=11)
    base_sd = statistics.pstdev(base_res.totals["A"])
    corr_sd = statistics.pstdev(corr_res.totals["A"])
    assert corr_sd > base_sd, (base_sd, corr_sd)
    assert abs(statistics.mean(corr_res.totals["A"])
              - statistics.mean(base_res.totals["A"])) < 5.0
    assert abs(statistics.pstdev(base_res.totals["B"])
              - statistics.pstdev(corr_res.totals["B"])) < 1e-6
    corr_res2 = simulate(st, Bootstrap(per, matches=matches,
                                       club_of=club_of_a,
                                       club_rel={"OneClub": 0.6}),
                         trials=1500, seed=11)
    assert corr_res.totals == corr_res2.totals

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

    import ffcore.forecast as forecast

    many_j = list(range(1, 11))
    per10 = {j: {k: (3.0, 1.0) for k in list(a) + list(b)} for j in many_j}
    st10 = LeagueState(squads={"A": a, "B": b}, jornadas=many_j, me="A")
    matches10 = {k: 20 for k in list(a) + list(b)}
    was_drift = forecast.DRIFT_FRAC
    try:
        forecast.DRIFT_FRAC = 0.0
        flat_res = simulate(st10, Bootstrap(per10, matches=matches10),
                            trials=1500, seed=13)
        forecast.DRIFT_FRAC = 1.0
        drift_res = simulate(st10, Bootstrap(per10, matches=matches10),
                             trials=1500, seed=13)
    finally:
        forecast.DRIFT_FRAC = was_drift
    flat_sd = statistics.pstdev(flat_res.totals["A"])
    drift_sd = statistics.pstdev(drift_res.totals["A"])
    assert drift_sd > flat_sd, (flat_sd, drift_sd)
    flat_mean = statistics.mean(flat_res.totals["A"])
    drift_mean = statistics.mean(drift_res.totals["A"])
    assert abs(drift_mean - flat_mean) / flat_mean < 0.03, \
        (flat_mean, drift_mean)

    from ffcore.forecast import MIN_POOL

    per10_s = {j: {k: (3.0, 0.7) for k in list(a) + list(b)} for j in many_j}
    const_pool = [3] * (MIN_POOL + 50)
    was_drift = forecast.DRIFT_FRAC
    try:
        forecast.DRIFT_FRAC = 0.0
        sflat_res = simulate(
            st10, Bootstrap(per10_s, pool=const_pool, matches=matches10),
            trials=1500, seed=17)
        forecast.DRIFT_FRAC = 1.0
        sdrift_res = simulate(
            st10, Bootstrap(per10_s, pool=const_pool, matches=matches10),
            trials=1500, seed=17)
    finally:
        forecast.DRIFT_FRAC = was_drift
    sflat_sd = statistics.pstdev(sflat_res.totals["A"])
    sdrift_sd = statistics.pstdev(sdrift_res.totals["A"])
    assert sdrift_sd > sflat_sd, (sflat_sd, sdrift_sd)
    sflat_mean = statistics.mean(sflat_res.totals["A"])
    sdrift_mean = statistics.mean(sdrift_res.totals["A"])
    assert abs(sdrift_mean - sflat_mean) / sflat_mean < 0.05, \
        (sflat_mean, sdrift_mean)

    print("ffcore.season self-test OK (34 cases)")


if __name__ == "__main__":
    _selftest()
