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

import os
import random
from dataclasses import dataclass, field

from ffcore.score import MAX_SLOT, formations

__all__ = ["LeagueState", "Standings", "simulate",
           "simulate_many", "best_xi"]

XI_SIZE = 11


def legal_shapes() -> list[dict[str, int]]:
    """Every formation the app allows, as counts per position.

    ffcore.score.formations()'s OWN LIST, converted to dicts — not
    re-derived from SLOT_MIN/MAX_SLOT bounds the way this used to work.
    Found 2026-09-01 (swarm review of the forecasting engine): bounds-
    derivation correctly reproduces these 7 shapes by coincidence, but a
    now-deleted PREMIUM_FORMATIONS list once violated those same bounds —
    e.g. (4, 6, 0) fielded 6 midfielders, over MAX_SLOT["MED"]=5. One
    authority (score.formations()) instead of two that could only ever
    agree by coincidence.
    """
    return [{"POR": 1, "DEF": d, "MED": m, "DEL": f}
           for d, m, f in formations()]


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

    # PREFIX SUMS, ONCE PER SLOT. Every shape only ever wants a slot's best-N
    # players, so summing that prefix fresh per shape (SHAPES is 7 long, so
    # this used to run up to 7x per call) repeated the same partial sums.
    # `prefix[slot][n]` is the value of that slot's best n players — a plain
    # running total, computed once here and looked up O(1) below.
    prefix: dict[str, list[float]] = {}
    for slot, have in by_slot.items():
        acc, ps = 0.0, [0.0]
        for k in have:
            acc += value.get(k, 0.0)
            ps.append(acc)
        prefix[slot] = ps

    best, best_total = [], None
    for shape in SHAPES:
        picked, total, ok = [], 0.0, True
        for slot, n in shape.items():
            have = by_slot.get(slot, [])
            if len(have) < n:
                ok = False
                break
            picked += have[:n]
            total += prefix[slot][n]
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


def _chunk(args):
    """One worker's slice of the trials. Module level, so it can be pickled."""
    states, forecaster, lo, hi, seed = args
    return _run(states, forecaster, range(lo, hi), seed)


def simulate_many(states: list, forecaster, trials: int = 2000,
                  seed: int = 0, workers: int | None = None) -> list:
    """Play one set of seasons and score EVERY candidate squad against it.

    THE DRAWS DO NOT DEPEND ON THE SQUADS. Same seed, same forecaster, same
    jornadas — so every call to simulate() was redrawing an identical season
    and throwing it away. Ranking twelve options at three thousand trials over
    thirty-eight jornadas meant eight million draws where a hundred thousand
    would do, and the draw is the expensive part: a dict of ninety players,
    two rng calls each.

    Drawing once per (trial, jornada) and scoring all the states against it is
    exactly the same arithmetic in a different order, which is why the
    self-test asserts the numbers are IDENTICAL rather than close.

    Returns one Standings per state, in order.
    """
    if not states:
        return []
    # ACROSS CORES, AND EXACTLY. Every trial seeds its own rng from the trial
    # NUMBER, so splitting the range over processes gives the same seasons in
    # the same order — the self-test asserts the totals are identical, not
    # close. Only worth the pickling above a few hundred trials.
    # Arrays first: one vectorised pass beats four processes doing it a
    # number at a time, and it needs no agreement between them.
    if workers is None:
        fast = _run_np(states, forecaster, trials, seed)
        if fast is not None:
            return [Standings(totals=tot, me=st.me)
                    for tot, st in zip(fast, states)]

    n = workers if workers is not None else (os.cpu_count() or 1)
    if n > 1 and trials >= 400 and len(states) > 1:
        import concurrent.futures as cf

        edges = [trials * i // n for i in range(n + 1)]
        jobs = [(states, forecaster, edges[i], edges[i + 1], seed)
                for i in range(n) if edges[i] < edges[i + 1]]
        parts = []
        try:
            with cf.ProcessPoolExecutor(max_workers=len(jobs)) as ex:
                parts = list(ex.map(_chunk, jobs))
        except Exception:                                   # noqa: BLE001
            # A box that cannot fork is a slow report, not a failed one.
            parts = []
        if parts:
            out = []
            for i, st in enumerate(states):
                merged = {m: [v for p in parts for v in p[i][m]]
                          for m in st.squads}
                out.append(Standings(totals=merged, me=st.me))
            return out
    return [Standings(totals=tot, me=st.me)
            for tot, st in zip(_run(states, forecaster, range(trials), seed),
                               states)]


def _run_np(states: list, forecaster, trials: int, seed: int):
    """The same seasons, drawn as arrays. None if numpy is not installed.

    A SEPARATE, hand-synced mirror of ffcore.forecast.Bootstrap's draw
    logic (not a caller of it) — vectorizing collapses ~20M Python calls
    per run into a couple of numpy calls. NOT the same numbers as the
    Python path (a different generator, statistically equivalent only).
    The generator is seeded from (seed, jornada), so a jornada's matrix is
    the same however the work is divided across processes.
    Full design notes + the rng stream layout: docs/notes/season.md#_run_np-the-vectorized-mirror-kept-in-sync-by-hand
    """
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
    # rate_rel/club_rel/etc mirror ffcore.forecast.Bootstrap's own fields —
    # see docs/notes/season.md#_run_np-the-vectorized-mirror-kept-in-sync-by-hand
    # for the rng stream layout and why the club shock does not drift.
    rel = getattr(forecaster, "rate_rel", None) or {}
    club_of = getattr(forecaster, "club_of", None) or {}
    club_rel = getattr(forecaster, "club_rel", None) or {}
    all_keys = sorted({k for ks in order.values() for k in ks} & set(rel)) \
        if rel else []
    eps0 = shared = drng = cum_var = walk = None
    if all_keys:
        from ffcore.forecast import DRIFT_FRAC

        rrng = np.random.default_rng([seed, 7919])
        sd = np.array([rel[k] for k in all_keys], dtype=float)
        eps0 = np.clip(
            1.0 + rrng.standard_normal((trials, len(all_keys))) * sd,
            0.0, None)
        clubs = sorted(club_rel)
        if clubs:
            # Own rng stream (seed component 7920): independent of the
            # individual draw, not the same bits reused twice.
            crng = np.random.default_rng([seed, 7920])
            csd = np.array([club_rel[c] for c in clubs], dtype=float)
            shock = np.clip(
                1.0 + crng.standard_normal((trials, len(clubs))) * csd,
                0.0, None)
            shock_of = {c: shock[:, i] for i, c in enumerate(clubs)}
            ones = np.ones(trials)
            shared = np.stack(
                [shock_of.get(club_of.get(k, ""), ones) for k in all_keys],
                axis=1)
        # Own stream (7921), drawn incrementally inside the jornada loop
        # below — a random walk's steps must be independent draws IN
        # ORDER, not one big draw sliced afterward.
        drng = np.random.default_rng([seed, 7921])
        step_sd = DRIFT_FRAC * sd
        # cum_var stays 1D (per key, the variance SCHEDULE is the same
        # across trials); walk is (trials, keys) — each trial realizes
        # its own path. See docs/notes/season.md for the accumulation fix.
        cum_var = np.zeros(len(all_keys))
        walk = np.zeros((trials, len(all_keys)))

    # start_rel's own error: same shape as the rate's, own stream
    # (7927+, additive on logit(p) not multiplicative on a rate). No club
    # term here, same reason start_draw() has none.
    srel = getattr(forecaster, "start_rel", None) or {}
    all_start_keys = sorted(
        {k for ks in order.values() for k in ks} & set(srel)) if srel else []
    seps0 = sdrng = None
    if all_start_keys:
        from ffcore.forecast import DRIFT_FRAC as _DF

        srrng = np.random.default_rng([seed, 7927])
        ssd = np.array([srel[k] for k in all_start_keys], dtype=float)
        seps0 = srrng.standard_normal((trials, len(all_start_keys))) * ssd
        sdrng = np.random.default_rng([seed, 7928])
        step_sd_s = _DF * ssd
        # No cum_var_s: additive and mean-zero already, unlike the
        # log-normal rate walk above — just the accumulated position.
        walk_s = np.zeros((trials, len(all_start_keys)))  # per trial too

    managers = [list(st.squads) for st in states]
    totals = [{m: np.full(trials, float(st.carried.get(m, 0.0)))
               for m in ms} for st, ms in zip(states, managers)]

    # The eleven depends only on expectations, so it is picked once per state
    # per jornada — not once per state per jornada per pass through the draw.
    # Left inside the loop it was two and a half thousand shape searches.
    #
    # AND ONCE PER SQUAD, not once per state. The candidate options differ by
    # one squad each: my own, or the one rival a steal takes a player from.
    # The other four managers' elevens are the same search repeated for every
    # option on the table — 15,519 shape searches for about 1,900 distinct
    # questions. Keyed on the squad itself so a state that shares one gets the
    # answer, which is exactly the condition under which the answer is shared.
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
        # One walk step per jornada in the remaining calendar, taken BEFORE
        # the "nothing to score" skip below (a jornada with no keys still
        # elapses — skipping its step would tie the walk's width to which
        # weeks happen to have zero scoring rows). Accumulated in place,
        # not redrawn from cum_var — see docs/notes/season.md for the bug
        # this fixed and why the log-normal form avoids a mean-inflating
        # clip.
        if all_keys:
            step_var = step_sd ** 2
            walk += drng.standard_normal((trials, len(all_keys))) \
                * step_sd
            cum_var += step_var
            walked = np.exp(walk - cum_var / 2.0)
            individual = eps0 * walked
            m = individual * shared if shared is not None else individual
            rate_mult = {k: m[:, i] for i, k in enumerate(all_keys)}
        # Same walk, on P(start) — logit-additive, own stream (7927/7928).
        start_shift = {}
        if all_start_keys:
            walk_s += sdrng.standard_normal((trials, len(all_start_keys))) \
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
        # (trials, players) of rate multipliers, 1.0 where nothing says how
        # thin the evidence is — same shape as the draw, so it multiplies
        # into it without a loop.
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


def _run(states: list, forecaster, which, seed: int) -> list:
    """{manager: [total per trial]} per state, for the trials in `which`."""
    managers = [list(st.squads) for st in states]
    idx = list(which)
    totals = [{m: [float(st.carried.get(m, 0.0))] * len(idx) for m in ms}
              for st, ms in zip(states, managers)]

    # The eleven each manager fields depends only on expectations, so it is
    # the same in every trial and is picked once per state rather than
    # `trials` times.
    xis = []
    for st in states:
        per = {}
        for j in st.jornadas:
            exp = forecaster.expected(j)
            per[j] = {m: best_xi(sq, exp) for m, sq in st.squads.items()}
        xis.append(per)

    # SCORE THE FIRST STATE, THEN THE DIFFERENCES. A candidate squad is the
    # base squad with two or three men moved, and every other manager is
    # untouched — so summing eleven names for five managers for every one of
    # thirteen states repeats work that cannot have changed. Each state keeps
    # only who ENTERED and who LEFT each eleven relative to state zero, which
    # is typically four names against fifty-five.
    jornadas = states[0].jornadas
    deltas = []
    for i in range(1, len(states)):
        per = {}
        for j in jornadas:
            for m in managers[i]:
                was = set(xis[0][j].get(m, ()))
                now = set(xis[i][j].get(m, ()))
                if was != now:
                    per[(j, m)] = (tuple(now - was), tuple(was - now))
        deltas.append(per)

    rate_of = getattr(forecaster, "rate_draw", None)
    start_of = getattr(forecaster, "start_draw", None)
    for n, t in enumerate(idx):
        rng = random.Random(seed * 1_000_003 + t)
        # ONE RATE-DRIFT WALK PER TRIAL, before the jornadas — a rate
        # estimated wrong is wrong all season, but the FURTHER a jornada is
        # the more it could ALSO have drifted since (see
        # ffcore.forecast.Bootstrap.rate_draw's own docstring and
        # DRIFT_FRAC's note on why). Passing `jornadas` gets one dict PER
        # jornada rather than one flat dict for the whole season; a
        # forecaster with no rate_draw at all still runs with no rate
        # uncertainty. Drawn from its own generator so adding it does not
        # shift the match-to-match numbers underneath it.
        rates_by_j = (rate_of(random.Random(seed * 7919 + t), jornadas)
                     if rate_of else None)
        # SAME IDEA, A SEPARATE STREAM (seed*7927, not 7919) — start_draw's
        # own walk must not share bits with rate_draw's, or "the rate was
        # wrong high" and "he starts more than expected" would move
        # together for no reason grounded in anything.
        starts_by_j = (start_of(random.Random(seed * 7927 + t), jornadas)
                       if start_of else None)
        for j in jornadas:
            rates = rates_by_j.get(j) if rates_by_j else None
            starts = starts_by_j.get(j) if starts_by_j else None
            drawn = forecaster.draw(j, rng, rates, starts)
            get = drawn.get
            base = {}
            for m in managers[0]:
                v = sum(get(k, 0.0) for k in xis[0][j][m])
                base[m] = v
                totals[0][m][n] += v
            for i in range(1, len(states)):
                tot, per = totals[i], deltas[i - 1]
                for m in managers[i]:
                    v = base.get(m, 0.0)
                    ch = per.get((j, m))
                    if ch is not None:
                        v += sum(get(k, 0.0) for k in ch[0])
                        v -= sum(get(k, 0.0) for k in ch[1])
                    tot[m][n] += v

    return totals


def simulate(state: LeagueState, forecaster, trials: int = 2000,
             seed: int = 0) -> Standings:
    """Play the remaining jornadas `trials` times.

    THE SAME SEED IS THE SAME SEASON for every call, which is what makes two
    candidate squads comparable: the difference between them is then the
    squads, not the weather. Comparing a buy against a hold on independently
    drawn seasons would bury a one-point edge under a ±75-point spread.

    One state through simulate_many, so the two cannot drift apart: they held
    a copy each of the same loop, and the copies had to be asserted equal.
    """
    return simulate_many([state], forecaster, trials=trials, seed=seed)[0]


def _selftest() -> None:
    import statistics

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

    # -- club-correlated variance actually widens a real season sim --------
    # A squad entirely from ONE club should show MORE season-total spread
    # once that club's players share a strong correlated shock than when
    # they draw independently — this is the actual claim ffcore.forecast's
    # club_rel exists for, checked through simulate() itself (whichever of
    # _run/_run_np is active), not just at the Bootstrap unit level.
    # KEYED ON a/b's PREFIXED NAMES ("a_k", "b_d1", ...), not sq's bare
    # ones — rate_rel is looked up by the SAME key per_jornada uses, and a
    # mismatch here means an empty intersection in _run_np's `all_keys`,
    # silently applying NO rate uncertainty to either run and making them
    # look identical for a reason that has nothing to do with correlation.
    # Caught exactly that way once already, before fixing it here.
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
    # The MEAN barely moves — this widens the band, it does not bias it.
    assert abs(statistics.mean(corr_res.totals["A"])
              - statistics.mean(base_res.totals["A"])) < 5.0
    # Squad B, uncorrelated in both runs, is a control: its own spread
    # should not have moved just because A's forecaster changed.
    assert abs(statistics.pstdev(base_res.totals["B"])
              - statistics.pstdev(corr_res.totals["B"])) < 1e-6
    # Reproducible with the feature on, the same as without it.
    corr_res2 = simulate(st, Bootstrap(per, matches=matches,
                                       club_of=club_of_a,
                                       club_rel={"OneClub": 0.6}),
                         trials=1500, seed=11)
    assert corr_res.totals == corr_res2.totals

    # -- one draw, many squads ---------------------------------------------
    # IDENTICAL, not close. simulate_many is the same arithmetic in a
    # different order — draw the season once, score every candidate against
    # it — so anything but an exact match means the reordering changed the
    # rng stream, which is the one thing it must not do.
    b2 = {f"b_{k}": v for k, v in sq.items()}
    alt = LeagueState(squads={"A": dict(a), "B": dict(b2)}, jornadas=[1],
                      me="A")
    one = simulate(st, Bootstrap(per), trials=150, seed=9)
    two = simulate(alt, Bootstrap(per), trials=150, seed=9)
    both = simulate_many([st, alt], Bootstrap(per), trials=150, seed=9)
    assert both[0].totals == one.totals, "draw order changed"
    assert both[1].totals == two.totals, "draw order changed"
    assert simulate_many([], Bootstrap(per), trials=10) == []

    # A state with a manager the base does not have is scored from scratch,
    # not silently given the base's total for a manager that is not there.
    solo = LeagueState(squads={"A": dict(a), "C": dict(b2)}, jornadas=[1],
                       me="A")
    got = simulate_many([st, solo], Bootstrap(per), trials=120, seed=3)
    want = simulate(solo, Bootstrap(per), trials=120, seed=3)
    assert got[1].totals == want.totals, "a new manager must be scored fully"

    # ACROSS CORES, AND EXACTLY. Every trial seeds its own rng from the trial
    # number, so splitting the range over processes must give the same
    # seasons in the same order — identical, not close.
    serial = simulate_many([st, alt], Bootstrap(per), trials=500, seed=2,
                           workers=1)
    forked = simulate_many([st, alt], Bootstrap(per), trials=500, seed=2,
                           workers=4)
    assert [x.totals for x in serial] == [x.totals for x in forked], \
        "splitting the trials changed the seasons"

    # -- rate DRIFT actually widens a real season sim, mean unbiased --------
    # Ten jornadas, not one — the walk needs distance to have anything to
    # grow over. Same squads as the club-correlation check above, but no
    # club_rel here: isolating the drift mechanism on its own.
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
    # THE MEAN MUST NOT MOVE — the exact bug caught and fixed while tuning
    # DRIFT_FRAC (a clip(1+drift, 0) formulation biased it upward the
    # wider the walk got). A generous tolerance against real Monte Carlo
    # noise at 1500 trials, not against a systematic shift.
    flat_mean = statistics.mean(flat_res.totals["A"])
    drift_mean = statistics.mean(drift_res.totals["A"])
    assert abs(drift_mean - flat_mean) / flat_mean < 0.03, \
        (flat_mean, drift_mean)

    # -- start_rel DRIFT actually widens a real season sim, isolated from
    # the points-rate mechanism above -------------------------------------
    # A CONSTANT pool (cv=0, so rate_rel is 0.0 for everyone regardless of
    # n) with real_n >= MIN_POOL so it is actually used rather than falling
    # back to the seed prior — the points side is neutralised without
    # touching start_rel, which depends on p and n, not on the points pool
    # at all. p=0.7, not 1.0: start_rel's own guard zeroes it for a
    # certain reading, so the mechanism needs something to be uncertain
    # ABOUT.
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
    # Same mean-preservation claim as the points side: a wider walk on
    # WHETHER he plays must not, itself, change how often he plays on
    # average — sigmoid(logit(p) + N(0, sd)) is not mean-preserving in p
    # the way exp(drift - var/2) is in a rate, so this is a real check,
    # not a restatement of the formula.
    sflat_mean = statistics.mean(sflat_res.totals["A"])
    sdrift_mean = statistics.mean(sdrift_res.totals["A"])
    assert abs(sdrift_mean - sflat_mean) / sflat_mean < 0.05, \
        (sflat_mean, sdrift_mean)

    print("ffcore.season self-test OK (34 cases)")


if __name__ == "__main__":
    _selftest()
