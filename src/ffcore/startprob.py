
from __future__ import annotations

import math

__all__ = ["Obs", "Calibration", "observations", "af_prob", "METHOD_VERSION",
          "fit_start_fallbacks"]

METHOD_VERSION = 2

INTERCEPT = [round(-3.0 + 0.5 * i, 1) for i in range(13)]
SLOPE = [round(0.2 + 0.4 * i, 1) for i in range(15)]
WEIGHTS = [round(0.1 * i, 1) for i in range(11)]

FLOOR, CEIL = 0.01, 0.97


class Obs(tuple):
    __slots__ = ()

    def __new__(cls, ff, af, started, group=""):
        return super().__new__(cls, (ff, af, float(started), group))

    ff = property(lambda s: s[0])
    af = property(lambda s: s[1])
    started = property(lambda s: s[2])
    group = property(lambda s: s[3])


def af_prob(row, titular: float) -> float | None:
    if not row:
        return None
    pct = row.get("start_pct")
    if pct not in (None, ""):
        try:
            return min(1.0, max(0.0, float(pct) / 100.0))
        except (TypeError, ValueError):
            pass
    return titular if row.get("role") == "starter" else None


def _platt(p: float, alpha: float, beta: float) -> float:
    p = min(1.0 - 1e-6, max(1e-6, p))
    z = alpha + beta * math.log(p / (1.0 - p))
    q = 1.0 / (1.0 + math.exp(-max(-40.0, min(40.0, z))))
    return min(CEIL, max(FLOOR, q))


def _brier(model, obs) -> float:
    return sum((model(o) - o.started) ** 2 for o in obs) / len(obs)


class Calibration:

    def __init__(self, alpha=0.0, beta=1.0, weight=0.0, titular=0.9, n=0,
                 fitted=False, gain=0.0, why="", groups=0):
        self.alpha, self.beta = alpha, beta
        self.weight, self.titular = weight, titular
        self.n, self.fitted, self.gain, self.why = n, fitted, gain, why
        self.groups = groups

    def p(self, ff_pct, af=None) -> float:
        if ff_pct is None:
            base = None
        elif (self.alpha, self.beta) == (0.0, 1.0):
            base = ff_pct / 100.0
        else:
            base = _platt(ff_pct / 100.0, self.alpha, self.beta)
        q = af_prob(af, self.titular)
        if base is None:
            return 0.0 if q is None else q
        if q is None or not self.weight:
            return base
        return self.weight * q + (1.0 - self.weight) * base

    def note(self) -> str:
        if not self.n:
            return ("P(start) is futbolfantasy's own figure — no confirmed "
                    "line-up has been recorded yet to fit anything against.")
        if not self.fitted:
            return ("P(start) is futbolfantasy's own figure: on %d confirmed "
                    "starts the fitted version did not beat it out of sample "
                    "(%s)." % (self.n, self.why))
        return ("P(start) fitted on %d confirmed starts across %d team "
                "sheets: futbolfantasy recalibrated (logit %+.1f %+.1fx), "
                "blended %.0f%% with analiticafantasy where it has an opinion "
                "(a named starter counts %.0f%%). Brier improves %.3f on "
                "line-ups the fit had not seen."
                % (self.n, self.groups, self.alpha, self.beta,
                   100 * self.weight, 100 * self.titular, self.gain))

    @classmethod
    def fit(cls, obs) -> "Calibration":
        obs = [o for o in obs if o.ff is not None or o.af is not None]
        if len(obs) < 3:
            return cls(n=len(obs), why="not enough to hold one out")

        titular = _titular_rate(obs)

        def grid(train):
            best, arg = None, (0.0, 1.0, 0.0)
            for al in INTERCEPT:
                for be in SLOPE:
                    for w in WEIGHTS:
                        c = cls(al, be, w, titular)
                        sc = _brier(
                            lambda o: c.p(_pct(o.ff), None) if o.af is None
                            else c.p(_pct(o.ff), {"start_pct": o.af * 100}),
                            train)
                        if best is None or sc < best:
                            best, arg = sc, (al, be, w)
            return arg

        raw = cls(0.0, 1.0, 0.0, titular)
        groups = sorted({o.group for o in obs})
        if len(groups) < 2:
            return cls(n=len(obs), titular=titular,
                       why="only %d team sheet%s — nothing to hold out"
                           % (len(groups), "" if len(groups) == 1 else "s"))
        loo_fit = loo_raw = 0.0
        for g in groups:
            train = [o for o in obs if o.group != g]
            held = [o for o in obs if o.group == g]
            if not train:
                continue
            al, be, w = grid(train)
            c = cls(al, be, w, titular)
            for h in held:
                af_row = None if h.af is None else {"start_pct": h.af * 100}
                loo_fit += (c.p(_pct(h.ff), af_row) - h.started) ** 2
                loo_raw += (raw.p(_pct(h.ff), None) - h.started) ** 2
        loo_fit /= len(obs)
        loo_raw /= len(obs)

        if loo_fit >= loo_raw:
            return cls(n=len(obs), titular=titular,
                       why="Brier %.3f fitted vs %.3f raw" % (loo_fit, loo_raw))
        al, be, w = grid(obs)
        return cls(al, be, w, titular, n=len(obs), fitted=True,
                   gain=loo_raw - loo_fit, groups=len(groups))


def _pct(ff):
    return None if ff is None else ff * 100.0


def _titular_rate(obs) -> float:
    hits = [o for o in obs if o.af is not None and o.af >= 0.999]
    if not hits:
        return 0.9
    return min(0.99, max(0.5, sum(o.started for o in hits) / len(hits)))


def observations(lineups, starters, cut: str, roster=None,
                 neutral: float = 60.0, absent: float = 15.0,
                 xw=None) -> list[Obs]:
    from ffcore.second import resolve_second_source
    from ffcore.text import norm

    from ffcore.tidy import MATCH_LEN, minutes_played

    truth = [r for r in starters if r.get("role")]
    if not truth:
        return []
    last = max(r["observed_at"] for r in truth)
    truth = [r for r in truth if r["observed_at"] == last]
    teams = {r.get("team_slug") for r in truth}
    minutes_of = {r["player_slug"]: minutes_played(r["role"], r.get("minute"))
                 for r in truth}
    name_of = {r["player_slug"]: r.get("player_name", "") for r in truth}
    team_of = {r["player_slug"]: r.get("team_slug", "") for r in truth}

    wide: dict[str, dict] = {}
    narrow_rows = []
    for r in sorted((r for r in lineups
                     if r.get("observed_at", "") <= cut
                     and r.get("team_slug") in teams),
                    key=lambda r: r.get("observed_at", "")):
        if (r.get("source") or "").startswith("futbol"):
            wide[r.get("player_slug") or norm(r.get("player_name"))] = r
        else:
            narrow_rows.append(r)
    narrow = resolve_second_source(narrow_rows, xw)

    out = []
    for slug in sorted(set(wide) | set(truth and name_of)):
        row = wide.get(slug)
        fp = absent / 100.0
        if row is not None:
            fp = neutral / 100.0
            if (row.get("start_pct") or "") != "":
                try:
                    fp = float(row["start_pct"]) / 100.0
                except (TypeError, ValueError):
                    pass
        nm = (row or {}).get("player_name") or name_of.get(slug, "")
        af = narrow.get(norm(nm))
        if af is None and xw is not None:
            pid = xw.player(ff_slug=slug, name=nm)
            if pid:
                af = narrow.get(pid)
        graded = min(1.0, minutes_of.get(slug, 0.0) / MATCH_LEN)
        out.append(Obs(fp, af_prob(af, 1.0), graded,
                       (row or {}).get("team_slug") or team_of.get(slug, "")))
    return out


def fit_start_fallbacks(lineups, starters, cut: str,
                        neutral_default: float = 60.0,
                        absent_default: float = 15.0, k: float = 8.0,
                        xw=None) -> tuple[float, float, str]:
    obs = observations(lineups, starters, cut, neutral=neutral_default,
                       absent=absent_default, xw=xw)

    def shrink(default_pct, bucket):
        n = len(bucket)
        if n == 0:
            return default_pct, "no real observations yet, keeping %.0f%%" \
                % default_pct
        rate = sum(o.started for o in bucket) / n
        fitted = (k * default_pct / 100.0 + n * rate) / (k + n) * 100.0
        return fitted, ("%d real observations, %.0f%% actually started -> "
                       "%.1f%%" % (n, 100 * rate, fitted))

    neutral_bucket = [o for o in obs if abs(o.ff - neutral_default / 100) < 1e-9]
    absent_bucket = [o for o in obs if abs(o.ff - absent_default / 100) < 1e-9]
    neutral_pct, neutral_why = shrink(neutral_default, neutral_bucket)
    absent_pct, absent_why = shrink(absent_default, absent_bucket)
    return neutral_pct, absent_pct, ("neutral: %s; absent: %s"
                                    % (neutral_why, absent_why))


def _selftest() -> None:
    assert abs(_platt(0.5, 0.0, 1.0) - 0.5) < 1e-6
    assert abs(_platt(0.8, 0.0, 1.0) - 0.8) < 1e-6
    assert _platt(0.8, 0.0, 3.0) > 0.8
    assert _platt(0.2, 0.0, 3.0) < 0.2
    assert _platt(0.3, 1.5, 3.0) > _platt(0.3, 0.0, 3.0)
    assert _platt(0.999, 0.0, 5.8) <= CEIL
    assert _platt(0.001, 0.0, 5.8) >= FLOOR
    assert FLOOR <= _platt(1.0, 3.0, 5.8) <= CEIL
    assert FLOOR <= _platt(0.0, -3.0, 0.2) <= CEIL

    assert af_prob({"start_pct": "75"}, 0.9) == 0.75
    assert af_prob({"role": "starter"}, 0.93) == 0.93
    assert af_prob({"role": "sub"}, 0.9) is None
    assert af_prob(None, 0.9) is None

    raw = Calibration()
    assert raw.p(80.0) == 0.8
    assert raw.p(100.0) == 1.0 and raw.p(0.0) == 0.0
    assert raw.p(80.0, {"start_pct": "20"}) == 0.8
    assert raw.p(None) == 0.0
    assert "no confirmed line-up" in raw.note()

    assert Calibration(weight=0.5).p(None, {"start_pct": "40"}) == 0.4

    obs = [Obs(p, None, st, "sheet%d" % (i % 6))
           for i, (p, st) in enumerate(
               [(0.8, 1), (0.7, 1), (0.3, 0), (0.2, 0)] * 12)]
    cal = Calibration.fit(obs)
    assert cal.fitted, cal.note()
    assert cal.beta > 1.0, cal.beta
    assert cal.p(80.0) > 0.8, cal.p(80.0)
    assert "recalibrated" in cal.note() and "48 confirmed" in cal.note()
    assert "6 team sheets" in cal.note(), cal.note()

    noise = [Obs(0.5, None, i % 2, "sheet%d" % (i % 5)) for i in range(20)]
    assert not Calibration.fit(noise).fitted
    assert "did not beat it out of sample" in Calibration.fit(noise).note()

    assert not Calibration.fit([Obs(0.5, None, 1)]).fitted
    assert "hold one out" in Calibration.fit([Obs(0.5, None, 1)]).note()
    one = Calibration.fit([Obs(0.9, None, i < 11, "same") for i in range(22)])
    assert not one.fitted and "1 team sheet" in one.note(), one.note()

    good = [Obs(0.5, float(st), st, "sheet%d" % (i % 5))
            for i, st in enumerate([1, 0] * 15)]
    cg = Calibration.fit(good)
    assert cg.fitted and cg.weight > 0.5, (cg.weight, cg.note())

    assert abs(_titular_rate([Obs(0.5, 1.0, 1)] * 9 + [Obs(0.5, 1.0, 0)])
               - 0.9) < 1e-9
    assert _titular_rate([Obs(0.5, None, 1)]) == 0.9

    lineups = [
        {"observed_at": "A", "source": "futbolfantasy", "team_slug": "t",
         "player_slug": "starter-man", "player_name": "Starter Man",
         "start_pct": "80", "role": "starter"},
        {"observed_at": "A", "source": "analitica", "team_slug": "t",
         "player_slug": "af-starter-man", "player_name": "Starter Man",
         "start_pct": "", "role": "starter"},
        {"observed_at": "A", "source": "futbolfantasy", "team_slug": "t",
         "player_slug": "bench-man", "player_name": "Bench Man",
         "start_pct": "20", "role": "sub"},
        {"observed_at": "A", "source": "futbolfantasy", "team_slug": "t",
         "player_slug": "vague-man", "player_name": "Vague Man",
         "start_pct": "", "role": "sub"},
        {"observed_at": "Z", "source": "futbolfantasy", "team_slug": "t",
         "player_slug": "starter-man", "player_name": "Starter Man",
         "start_pct": "99", "role": "starter"},
        {"observed_at": "A", "source": "futbolfantasy", "team_slug": "other",
         "player_slug": "elsewhere", "player_name": "Elsewhere",
         "start_pct": "90", "role": "starter"},
    ]
    starters = [
        {"observed_at": "K", "team_slug": "t", "player_slug": "starter-man",
         "player_name": "Starter Man", "role": "starter"},
        {"observed_at": "K", "team_slug": "t", "player_slug": "bench-man",
         "player_name": "Bench Man", "role": "sub"},
        {"observed_at": "K", "team_slug": "t", "player_slug": "surprise-man",
         "player_name": "Surprise Man", "role": "starter"},
    ]
    got = observations(lineups, starters, cut="M")
    assert all(o.group == "t" for o in got), got
    by = {o.ff: o for o in got}
    assert len(got) == 4, got
    assert by[0.8].started == 1.0 and by[0.2].started == 0.0
    assert by[0.8].af == 1.0
    assert by[0.2].af is None
    assert abs(by[0.6].ff - 0.6) < 1e-9
    assert by[0.15].started == 1.0
    assert all(abs(o.ff - 0.99) > 1e-9 and abs(o.ff - 0.9) > 1e-9
               for o in got), got
    assert observations(lineups, [], cut="M") == []

    npct, apct, why = fit_start_fallbacks(lineups, starters, cut="M")
    assert abs(npct - (8 * 60 + 1 * 0) / 9) < 1e-9, (npct, why)
    assert abs(apct - (8 * 15 + 1 * 100) / 9) < 1e-9, (apct, why)
    assert "1 real observations" in why, why
    npct0, apct0, why0 = fit_start_fallbacks([], [], cut="M")
    assert npct0 == 60.0 and apct0 == 15.0, (npct0, apct0)
    assert "no real observations" in why0, why0

    class _XW:
        def player(self, **kw):
            if kw.get("af_slug") == "af-only-slug":
                return "starter man"
            if kw.get("ff_slug") == "starter-man":
                return "starter man"
            return None

    odd = [r for r in lineups if r["source"] == "futbolfantasy"] + [
        {"observed_at": "A", "source": "analitica", "team_slug": "t",
         "player_slug": "af-only-slug", "player_name": "S. Man",
         "start_pct": "75", "role": "starter"}]
    assert all(o.af is None for o in observations(odd, starters, cut="M"))
    got2 = observations(odd, starters, cut="M", xw=_XW())
    assert any(o.af == 0.75 for o in got2), got2

    graded_lineups = [
        {"observed_at": "A", "source": "futbolfantasy", "team_slug": "t",
         "player_slug": "hooked-early", "player_name": "Hooked Early",
         "start_pct": "90", "role": "starter"},
        {"observed_at": "A", "source": "futbolfantasy", "team_slug": "t",
         "player_slug": "heavy-sub", "player_name": "Heavy Sub",
         "start_pct": "10", "role": "sub"},
    ]
    graded_starters = [
        {"observed_at": "K", "team_slug": "t", "player_slug": "hooked-early",
         "player_name": "Hooked Early", "role": "starter", "minute": "45"},
        {"observed_at": "K", "team_slug": "t", "player_slug": "heavy-sub",
         "player_name": "Heavy Sub", "role": "sub", "minute": "45"},
    ]
    graded = {o.ff: o.started
             for o in observations(graded_lineups, graded_starters, cut="M")}
    assert abs(graded[0.9] - 0.5) < 1e-9, graded
    assert abs(graded[0.1] - 0.5) < 1e-9, graded

    print("ffcore.startprob self-test OK (54 cases)")


if __name__ == "__main__":
    _selftest()
