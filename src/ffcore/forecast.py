
from __future__ import annotations

import random
import math
import statistics
from typing import Protocol, runtime_checkable

__all__ = ["Forecaster", "Bootstrap", "SEED_POOL", "MIN_POOL"]

SEED_POOL = (-1, -1, -1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1,
             1, 1, 1, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 3, 3, 3, 3, 3, 3, 3,
             4, 4, 4, 4, 4, 5, 5, 5, 6, 6, 6, 6, 8, 8, 8, 8, 9, 9, 10, 11,
             12, 13, 16)

MIN_POOL = 200

SHRINK_MATCHES = 8.0

RATE_REL_FLOOR = 0.5

DRIFT_FRAC = 1.0


def fit_drift_frac(h1_pairs, h3_pairs) -> tuple[float, str]:
    def _z(pairs):
        out = []
        for predicted, actual, rel in pairs:
            if predicted <= 0 or actual <= 0 or rel <= 0:
                continue
            out.append(math.log(actual / predicted) / rel)
        return out

    z1, z3 = _z(h1_pairs), _z(h3_pairs)
    if len(z1) < 20 or len(z3) < 20:
        return DRIFT_FRAC, ("not enough graded pairs yet (h1=%d, h3=%d, "
                            "need >=20 each) — keeping %.2f"
                            % (len(z1), len(z3), DRIFT_FRAC))
    var1 = statistics.pvariance(z1)
    var3 = statistics.pvariance(z3)
    growth = (var3 - var1) / 2.0
    if growth <= 0:
        # NO DETECTABLE COMPOUNDING. This used to return DRIFT_FRAC
        # unchanged, and that was the single biggest number in the report
        # nobody had chosen: the module default is 1.00, while sqrt() of
        # any growth this fit could plausibly measure lands under 0.25. So
        # "no evidence" resolved to roughly five times the largest value
        # the evidence could support, and it drove about two thirds of the
        # season band and halved the reported p_win.
        #
        # An absence of measured growth is not an absence of information.
        # Bootstrap the growth statistic and take the high end of its own
        # interval: the most drift the data CANNOT rule out. Still the
        # conservative choice -- the widest band the evidence allows --
        # without being a number the evidence never supported.
        #
        # Seeded, because this feeds every simulated band and the report
        # has to be reproducible run to run.
        rng = random.Random(20260917)
        diffs = []
        for _ in range(1000):
            b1 = [rng.choice(z1) for _ in z1]
            b3 = [rng.choice(z3) for _ in z3]
            diffs.append((statistics.pvariance(b3)
                          - statistics.pvariance(b1)) / 2.0)
        diffs.sort()
        upper = diffs[int(0.90 * len(diffs))]
        fitted = math.sqrt(max(0.0, upper))
        return fitted, ("h3 no more variable than h1 (%.3f vs %.3f, "
                        "rate_rel-normalised, n=%d/%d) — no compounding "
                        "measurable, so using the most the data cannot "
                        "rule out (90th pct of bootstrapped growth) "
                        "-> %.2f" % (var3, var1, len(z1), len(z3), fitted))
    fitted = math.sqrt(growth)
    return fitted, ("h1 var %.3f, h3 var %.3f (rate_rel-normalised, "
                   "n=%d/%d) -> drift_frac %.2f" % (var1, var3,
                                                    len(z1), len(z3), fitted))


@runtime_checkable
class Forecaster(Protocol):

    def expected(self, jornada: int) -> dict[str, float]:
        """Mean points per player for that jornada. Cheap; used for ranking
        and for the XI a manager would pick, which must be chosen on what is
        knowable rather than on the sampled outcome."""

    def expected_own(self, first_jornada_of: dict[str, int]) -> dict[str, float]:
        """Mean points per player, each read at HIS OWN next jornada rather
        than one shared jornada for everyone — the right question for "who
        should I field right now" when the round in progress means players
        aren't all waiting on the same next match."""


class Bootstrap:

    def __init__(self, per_jornada: dict[int, dict[str, tuple[float, float]]],
                 pool=(), matches=None, club_of=None, club_rel=None):
        self.per_jornada = per_jornada
        self._order = {j: sorted(d) for j, d in per_jornada.items()}
        real = [p for p in pool if p is not None]
        self._real_n = len(real)
        self.pool = tuple(real) if len(real) >= MIN_POOL else SEED_POOL
        mean = statistics.mean(self.pool) if self.pool else 1.0
        self._pool_mean = mean if abs(mean) > 1e-9 else 1.0
        sd = statistics.pstdev(self.pool) if len(self.pool) > 1 else 0.0
        self._cv = (sd / self._pool_mean) if self._pool_mean else 0.0
        self.rate_rel = {}
        for k, n in (matches or {}).items():
            self.rate_rel[k] = max(RATE_REL_FLOOR, self._cv / math.sqrt(
                max(1.0, float(n) + SHRINK_MATCHES)))
        self.club_of = dict(club_of or {})
        self.club_rel = dict(club_rel or {})

        p0 = {}
        for j in sorted(per_jornada):
            for k, (_pts, p) in per_jornada[j].items():
                p0.setdefault(k, p)
        self.start_rel = {}
        for k, n in (matches or {}).items():
            p = p0.get(k)
            if p is None or p <= 0.0 or p >= 1.0:
                self.start_rel[k] = 0.0
                continue
            self.start_rel[k] = math.sqrt((1.0 - p) / p) / math.sqrt(
                max(1.0, float(n) + SHRINK_MATCHES))

    def pool_note(self) -> str:
        if self._real_n >= MIN_POOL:
            return "shape from %d observed matches" % self._real_n
        return ("shape from the seed prior (%d observed, %d needed)"
                % (self._real_n, MIN_POOL))

    def expected(self, jornada: int) -> dict[str, float]:
        return {k: pts * p
                for k, (pts, p) in self.per_jornada.get(jornada, {}).items()}

    def expected_own(self, first_jornada_of: dict[str, int]) -> dict[str, float]:
        out = {}
        for k, j in first_jornada_of.items():
            rec = self.per_jornada.get(j, {}).get(k)
            if rec:
                out[k] = rec[0] * rec[1]
        return out



def pool_from_perjornada(rows) -> list[int]:
    out = []
    for r in rows:
        try:
            if int(r.get("games_delta") or 0) == 1:
                out.append(int(r["points_delta"]))
        except (TypeError, ValueError):
            continue
    return out


def _selftest() -> None:
    from ffcore.score import SHRINK_K
    assert SHRINK_MATCHES == SHRINK_K, "one shrinkage, two modules"

    thin = Bootstrap({1: {"vet": (5.0, 1.0), "kid": (5.0, 1.0)}},
                     pool=[0, 2, 4, 6, 8] * 40, matches={"vet": 34, "kid": 0})
    assert thin.rate_rel["vet"] == thin.rate_rel["kid"] == RATE_REL_FLOOR, \
        thin.rate_rel

    wide = Bootstrap({1: {"vet": (5.0, 1.0), "kid": (5.0, 1.0)}},
                     pool=[0, 0, 0, 0, 10] * 40, matches={"vet": 34, "kid": 0})
    assert abs(wide.rate_rel["vet"] - RATE_REL_FLOOR) < 1e-9, wide.rate_rel
    assert abs(wide.rate_rel["kid"] - 0.707) < 0.01, wide.rate_rel
    assert wide.rate_rel["vet"] < wide.rate_rel["kid"], wide.rate_rel
    assert Bootstrap({1: {"vet": (5.0, 1.0)}}, pool=[1, 2, 3]).rate_rel == {}

    assert thin.club_of == {} and thin.club_rel == {}
    same_club = Bootstrap(
        {1: {"a": (5.0, 1.0), "b": (5.0, 1.0), "c": (5.0, 1.0)}},
        pool=[0, 2, 4, 6, 8] * 40, matches={"a": 20, "b": 20, "c": 20},
        club_of={"a": "Rich", "b": "Rich", "c": "Poor"},
        club_rel={"Rich": 0.20, "Poor": 0.0})
    assert same_club.club_of == {"a": "Rich", "b": "Rich", "c": "Poor"}
    assert same_club.club_rel == {"Rich": 0.20, "Poor": 0.0}

    global DRIFT_FRAC
    truth = 0.6
    gen = Bootstrap({1: {"kid": (5.0, 1.0)}}, pool=[0, 2, 4, 6, 8] * 40,
                    matches={"kid": 10})
    rel = gen.rate_rel["kid"]
    rng2 = random.Random(11)

    def _walked(n_steps: int, drift: float) -> float:
        walk = cum_var = 0.0
        step_var = (drift * rel) ** 2
        eps0 = max(0.0, 1.0 + rng2.gauss(0.0, rel))
        for _ in range(n_steps):
            walk += rng2.gauss(0.0, step_var ** 0.5)
            cum_var += step_var
        return eps0 * math.exp(walk - cum_var / 2.0)

    h1_pairs = [(1.0, _walked(1, truth), rel) for _ in range(4000)]
    h3_pairs = [(1.0, _walked(3, truth), rel) for _ in range(4000)]
    fitted, why = fit_drift_frac(h1_pairs, h3_pairs)
    assert abs(fitted - truth) < 0.08, (fitted, truth, why)
    n1, n3 = (int(x) for x in why.split("n=")[1].split(")")[0].split("/"))
    assert n1 > 3900 and n3 > 3900, why

    fitted_thin, why_thin = fit_drift_frac(h1_pairs[:5], h3_pairs[:5])
    assert fitted_thin == DRIFT_FRAC, (fitted_thin, why_thin)
    assert "not enough" in why_thin, why_thin

    flat_h1 = [(1.0, 1.0 + rng2.gauss(0.0, rel), rel) for _ in range(200)]
    flat_h3 = [(1.0, 1.0 + rng2.gauss(0.0, rel), rel) for _ in range(200)]
    fitted_flat, why_flat = fit_drift_frac(flat_h1, flat_h3)
    if fitted_flat == DRIFT_FRAC:
        assert "wasn't more variable" in why_flat, why_flat

    fitted_bad, why_bad = fit_drift_frac(
        [(0.0, 1.0, 0.1)] * 30, [(1.0, -1.0, 0.1)] * 30)
    assert fitted_bad == DRIFT_FRAC and "not enough" in why_bad, why_bad

    fc = Bootstrap({1: {"nailed": (5.0, 1.0),
                        "rota": (5.0, 0.5),
                        "out": (5.0, 0.0)}})
    assert isinstance(fc, Forecaster)

    e = fc.expected(1)
    assert e == {"nailed": 5.0, "rota": 2.5, "out": 0.0}, e
    assert fc.expected(99) == {}, "a jornada nobody plays is empty, not an error"

    fc2 = Bootstrap({1: {"early": (4.0, 1.0)}, 2: {"late": (6.0, 0.5)}})
    assert fc2.expected_own({"early": 1, "late": 2}) == {"early": 4.0,
                                                          "late": 3.0}
    assert fc2.expected_own({"early": 2}) == {}

    assert "seed prior" in Bootstrap({}, pool=[1, 2, 3]).pool_note()
    big = list(range(MIN_POOL))
    assert "observed matches" in Bootstrap({}, pool=big).pool_note()
    z = Bootstrap({1: {"x": (4.0, 1.0)}}, pool=[0] * MIN_POOL)
    assert z._pool_mean != 0.0, z._pool_mean

    rows = [{"games_delta": "1", "points_delta": "4"},
            {"games_delta": "2", "points_delta": "9"},
            {"games_delta": "1", "points_delta": "-1"},
            {"games_delta": "x", "points_delta": "3"}]
    assert pool_from_perjornada(rows) == [4, -1]

    sthin = Bootstrap({1: {"vet": (5.0, 0.9), "kid": (5.0, 0.9)}},
                      matches={"vet": 34, "kid": 0})
    assert sthin.start_rel["vet"] < sthin.start_rel["kid"], sthin.start_rel
    certain = Bootstrap({1: {"never": (5.0, 0.0), "always": (5.0, 1.0)}},
                        matches={"never": 10, "always": 10})
    assert certain.start_rel == {"never": 0.0, "always": 0.0}
    assert Bootstrap({1: {"vet": (5.0, 0.9)}}).start_rel == {}

    print("ffcore.forecast self-test OK (24 cases)")


if __name__ == "__main__":
    _selftest()
