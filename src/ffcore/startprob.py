"""
ffcore.startprob — P(he starts), from two sources that disagree, fitted.

    cal = Calibration.fit(observations(lineups, starters, cut))
    cal.p(ff_pct=80.0, af=af_row)     -> 0.94
    cal.note()                        -> what it fitted, on how much

TWO SOURCES, AND THEY ARE NOT EQUALS. futbolfantasy publishes a percentage for
most of the league; analiticafantasy publishes on far fewer players, and on the
first jornada that has actually been played it was much the better of the two
where it spoke — Brier 0.089 against 0.195, on the 28 players both covered.

But it speaks SELECTIVELY, and that is the thing to hold on to: 82% of the
players AF covered actually started, against a base rate of 48%. It publishes
about obvious starters. So it cannot replace the wider source, only sharpen it
where it has an opinion, and a blend has to fall back rather than go blank.

FF's OWN PROBLEM IS CALIBRATION, NOT IGNORANCE, and it may be the bigger half.
Measured against the same four matches, everything it called at 70% or above
started every single time, and its 50% bucket started 62% of the time. Its
numbers are editorial buckets rather than probabilities — the README has said
so for a fortnight — so the fix is not to trust them less, it is to learn what
they mean.

NOTHING HERE IS A CONSTANT SOMEBODY CHOSE. Both parameters are fitted from
confirmed line-ups every run and both are reported, and the fit is only used if
it beats the raw source OUT OF SAMPLE, leave-one-out. That is the guard, and it
needs no minimum-sample number to be picked: with too little data the fitted
model loses to the identity on held-out points and is not used. `note()` prints
which happened, so the report can never imply a fit that did not take.

THE BASELINE IT HAS TO BEAT IS WHAT THE SYSTEM ACTUALLY DOES, fallbacks and
all: a player the wider source lists without a number is scored at
NEUTRAL_START, one it does not list at all at ABSENT_START. The first version
of this compared against a baseline that predicted ZERO for anyone unlisted —
and since the narrow source covers mostly obvious starters, the baseline was
marked wrong on players it never had an opinion about. It reported the fit
improving Brier by 0.306 against a raw score of about 0.19: an improvement
larger than the thing being improved, which is the shape of a rigged
comparison rather than of a good model.
"""

from __future__ import annotations

import math

__all__ = ["Obs", "Calibration", "observations", "af_prob"]

# The exponent grid for FF's recalibration and the weight grid for AF. Coarse
# on purpose: the data cannot resolve finer, and a grid is auditable where a
# solver's answer is not.
# PLATT SCALING: logit(p') = A + B*logit(p). Two parameters, because one is
# not enough. A single sharpening exponent can only steepen the curve about
# its middle, and the shape the data actually has is steep AND shifted — the
# wider source's 30% bucket started 12% of the time and its 70% bucket 96%.
# Fitted with the exponent alone, 30% was driven to 0.01 to buy accuracy at
# the top, understating a rotation player by a factor of twelve. The intercept
# is what lets the curve move without being forced through the middle.
INTERCEPT = [round(-3.0 + 0.5 * i, 1) for i in range(13)]   # -3.0 .. 3.0
SLOPE = [round(0.2 + 0.4 * i, 1) for i in range(15)]        # 0.2 .. 5.8
WEIGHTS = [round(0.1 * i, 1) for i in range(11)]            # 0.0 .. 1.0

# NOTHING IS CERTAIN, and a Bernoulli at 0 or 1 says otherwise. Left alone the
# fit drives the confident buckets to a flat 1.00, because on four matches
# everything called at 80% did start — while the wider source's OWN 100%
# bucket started 80% of the time. A player who cannot miss costs the simulator
# nothing when he does, so the sampled season stops containing the thing that
# actually decides leagues. The clamp is on the output only; the fit is free
# to want more than this and simply does not get it.
FLOOR, CEIL = 0.01, 0.97


class Obs(tuple):
    """(ff probability, af probability or None, did he start, whose team).

    `ff` is what the scorer WOULD USE — the published percentage, or the
    neutral/absent fallback — never None, so the baseline this is measured
    against is the live behaviour rather than a strawman.
    """
    __slots__ = ()

    def __new__(cls, ff, af, started, group=""):
        return super().__new__(cls, (ff, af, float(started), group))

    ff = property(lambda s: s[0])
    af = property(lambda s: s[1])
    started = property(lambda s: s[2])
    # WHOSE TEAM SHEET HE WAS ON, and it is the unit of evidence. A manager
    # picking an eleven is ONE decision, not twenty-two independent ones:
    # eleven players start because the other eleven do not. Cross-validating
    # over players counts each sheet twenty-two times and reports a confidence
    # four matches cannot support.
    group = property(lambda s: s[3])


def af_prob(row, titular: float) -> float | None:
    """AF's read as a probability, or None if it has no opinion.

    It publishes two different things and they are not interchangeable: a
    percentage, which IS a probability, and a named starting eleven, which is
    a final call with no number attached. `titular` is what that call has been
    WORTH historically — fitted, never assumed to be 100%, because a named
    starter who is rested costs a Bernoulli draw at p=1 everything.
    """
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
    """sigmoid(alpha + beta * logit(p)). (0, 1) is the identity."""
    p = min(1.0 - 1e-6, max(1e-6, p))
    z = alpha + beta * math.log(p / (1.0 - p))
    q = 1.0 / (1.0 + math.exp(-max(-40.0, min(40.0, z))))
    return min(CEIL, max(FLOOR, q))


def _brier(model, obs) -> float:
    return sum((model(o) - o.started) ** 2 for o in obs) / len(obs)


class Calibration:
    """What the two sources are worth, fitted from confirmed line-ups."""

    def __init__(self, alpha=0.0, beta=1.0, weight=0.0, titular=0.9, n=0,
                 fitted=False, gain=0.0, why="", groups=0):
        self.alpha, self.beta = alpha, beta
        self.weight, self.titular = weight, titular
        self.n, self.fitted, self.gain, self.why = n, fitted, gain, why
        self.groups = groups

    # -- use ---------------------------------------------------------------
    def p(self, ff_pct, af=None) -> float:
        """P(he starts). `ff_pct` is 0-100, already including the fallback the
        scorer applies; `af` is AF's raw row, or None."""
        # IDENTITY PARAMETERS ARE THE EXACT IDENTITY, clamp and all. The
        # floor and ceiling exist to stop a fitted curve asserting certainty;
        # applied to an unfitted one they would quietly move every score in
        # the repo, which is not a calibration, it is a silent edit. The test
        # is on the PARAMETERS rather than on whether a fit was accepted,
        # because the grid search scores candidates that have not been
        # accepted yet and they have to behave like themselves.
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

    # -- fit ---------------------------------------------------------------
    @classmethod
    def fit(cls, obs) -> "Calibration":
        """Fit both parameters, and USE THEM ONLY IF THEY EARN IT.

        Leave-one-TEAM-SHEET-out: every observation is predicted by a model
        fitted without the whole line-up it belongs to, and the fitted family
        has to beat the raw source on that held-out score. This is the whole
        guard. It replaces the "at least N observations" constant that would
        otherwise have to be chosen, and it fails in the right direction —
        with too little data the fit does not generalise, loses, and is not
        used.

        THE GROUP IS THE POINT. Held out one player at a time, the model gets
        to see the other twenty-one names on the same sheet, which is most of
        the answer: a manager picks eleven, so knowing who else started is
        knowing who did not. That version reported a fit improving Brier by
        0.031 out of sample and drove the confident buckets to a near-
        threshold classifier off four matches. Holding out the sheet asks the
        question that is actually being asked of it in the week ahead — a
        line-up it has never seen.
        """
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
    """How often AF's wordless "named starter" actually started.

    Falls back to 0.9 rather than 1.0 when nothing has been seen: a call with
    no evidence behind it must not enter a Bernoulli at certainty.
    """
    hits = [o for o in obs if o.af is not None and o.af >= 0.999]
    if not hits:
        return 0.9
    return min(0.99, max(0.5, sum(o.started for o in hits) / len(hits)))


def observations(lineups, starters, cut: str, roster=None,
                 neutral: float = 60.0, absent: float = 15.0,
                 xw=None) -> list[Obs]:
    """Predictions made before `cut`, joined to who actually started.

    Only the clubs a confirmed line-up exists for: everyone else has not
    played, and scoring a prediction against a match that has not happened is
    how a grader flatters itself.

    THE JOIN IS THE CROSSWALK'S when one is passed, and that is the whole
    point of having one: every feed's key for a player resolves to the same id
    without this module doing any guessing. Without it, confirmed line-ups and
    the wider source share a slug (169 of 182 match) while the narrow source
    shares nothing with anybody and falls back to a folded name at 66% — the
    join this repo warns about everywhere.

    THE UNIVERSE IS THE WIDER SOURCE'S OWN LIST, plus anyone who turned out to
    play. Not the matchday eighteen — that drops everyone left out of the
    squad, who are exactly the players the absent fallback is RIGHT about, and
    grading without them made ABSENT_START look far too low and flattened
    every confident call to compensate. Not the market's roster either: those
    names come from a third site and the join loses most of them, which
    silently doubles a player up as one prediction that started and one that
    did not.
    """
    from ffcore.text import norm

    truth = [r for r in starters if r.get("role")]
    if not truth:
        return []
    last = max(r["observed_at"] for r in truth)
    truth = [r for r in truth if r["observed_at"] == last]
    teams = {r.get("team_slug") for r in truth}
    started = {r["player_slug"] for r in truth if r["role"] == "starter"}
    name_of = {r["player_slug"]: r.get("player_name", "") for r in truth}
    team_of = {r["player_slug"]: r.get("team_slug", "") for r in truth}

    def ident(r):
        """One player, however this feed spells him."""
        if xw is not None:
            hit = xw.player(ff_slug=r.get("player_slug"),
                            af_slug=r.get("player_slug"),
                            name=r.get("player_name"))
            if hit:
                return hit
        return None

    wide: dict[str, dict] = {}
    narrow: dict[str, dict] = {}
    for r in sorted((r for r in lineups
                     if r.get("observed_at", "") <= cut
                     and r.get("team_slug") in teams),
                    key=lambda r: r.get("observed_at", "")):
        if (r.get("source") or "").startswith("futbol"):
            wide[r.get("player_slug") or norm(r.get("player_name"))] = r
        else:
            narrow[ident(r) or norm(r.get("player_name"))] = r

    out = []
    for slug in sorted(set(wide) | set(truth and name_of)):
        row = wide.get(slug)
        # Exactly what the scorer would use for him today, fallbacks and all.
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
        out.append(Obs(fp, af_prob(af, 1.0),
                       slug in started,
                       (row or {}).get("team_slug") or team_of.get(slug, "")))
    return out


def _selftest() -> None:
    # -- the shape ---------------------------------------------------------
    assert abs(_platt(0.5, 0.0, 1.0) - 0.5) < 1e-6
    assert abs(_platt(0.8, 0.0, 1.0) - 0.8) < 1e-6     # (0, 1) is the identity
    assert _platt(0.8, 0.0, 3.0) > 0.8                 # steeper at the top
    assert _platt(0.2, 0.0, 3.0) < 0.2                 # ...and at the bottom
    # THE INTERCEPT IS WHY THERE ARE TWO. A curve that can only steepen about
    # its middle has to wreck one end to fit the other; this one can move.
    assert _platt(0.3, 1.5, 3.0) > _platt(0.3, 0.0, 3.0)
    # NOBODY IS CERTAIN TO PLAY. However hard the fit pushes, the answer stays
    # inside the interval — a Bernoulli at 1 makes a rested starter cost the
    # simulator nothing, and the source's own 100% bucket started 80% of the
    # time on the first jornada that was played.
    assert _platt(0.999, 0.0, 5.8) <= CEIL
    assert _platt(0.001, 0.0, 5.8) >= FLOOR
    assert FLOOR <= _platt(1.0, 3.0, 5.8) <= CEIL
    assert FLOOR <= _platt(0.0, -3.0, 0.2) <= CEIL

    # -- AF's two units, which are not interchangeable ---------------------
    assert af_prob({"start_pct": "75"}, 0.9) == 0.75
    # A named starter is a call, not a 100%. It is worth what it has been
    # worth, which is fitted — entering it at certainty makes a rested starter
    # cost everything.
    assert af_prob({"role": "starter"}, 0.93) == 0.93
    assert af_prob({"role": "sub"}, 0.9) is None       # no opinion, not zero
    assert af_prob(None, 0.9) is None

    # -- a calibration that has fitted nothing is the raw source -----------
    raw = Calibration()
    assert raw.p(80.0) == 0.8
    # ...including at the ends, where the fitted clamp would otherwise bite.
    assert raw.p(100.0) == 1.0 and raw.p(0.0) == 0.0
    assert raw.p(80.0, {"start_pct": "20"}) == 0.8     # weight 0 ignores AF
    assert raw.p(None) == 0.0
    assert "no confirmed line-up" in raw.note()

    # AF alone still answers when the wider source is silent about him.
    assert Calibration(weight=0.5).p(None, {"start_pct": "40"}) == 0.4

    # -- the fit -----------------------------------------------------------
    # A source that is systematically under-confident: everyone it calls at
    # 70% or better starts, everyone at 30% or less does not. The fit should
    # sharpen it, and say so.
    obs = [Obs(p, None, st, "sheet%d" % (i % 6))
           for i, (p, st) in enumerate(
               [(0.8, 1), (0.7, 1), (0.3, 0), (0.2, 0)] * 12)]
    cal = Calibration.fit(obs)
    assert cal.fitted, cal.note()
    assert cal.beta > 1.0, cal.beta
    assert cal.p(80.0) > 0.8, cal.p(80.0)
    assert "recalibrated" in cal.note() and "48 confirmed" in cal.note()
    assert "6 team sheets" in cal.note(), cal.note()

    # NOISE MUST NOT FIT. Coin flips carry no signal, so the held-out score
    # cannot improve and the raw source stands — this is the guard that
    # replaces choosing a minimum sample size.
    noise = [Obs(0.5, None, i % 2, "sheet%d" % (i % 5)) for i in range(20)]
    assert not Calibration.fit(noise).fitted
    assert "did not beat it out of sample" in Calibration.fit(noise).note()

    # Too little to hold anything out is honest about why.
    assert not Calibration.fit([Obs(0.5, None, 1)]).fitted
    assert "hold one out" in Calibration.fit([Obs(0.5, None, 1)]).note()
    # ONE TEAM SHEET IS ONE OBSERVATION, whatever it holds. Twenty-two names
    # off a single line-up cannot validate anything, and saying so is the
    # difference between a guard and a formality.
    one = Calibration.fit([Obs(0.9, None, i < 11, "same") for i in range(22)])
    assert not one.fitted and "1 team sheet" in one.note(), one.note()

    # A second source that is simply right should be leaned on.
    good = [Obs(0.5, float(st), st, "sheet%d" % (i % 5))
            for i, st in enumerate([1, 0] * 15)]
    cg = Calibration.fit(good)
    assert cg.fitted and cg.weight > 0.5, (cg.weight, cg.note())

    # ...and its wordless call is measured, not assumed.
    assert abs(_titular_rate([Obs(0.5, 1.0, 1)] * 9 + [Obs(0.5, 1.0, 0)])
               - 0.9) < 1e-9
    assert _titular_rate([Obs(0.5, None, 1)]) == 0.9   # nothing seen

    # -- the join ----------------------------------------------------------
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
        # Listed with no percentage at all: the neutral fallback, which is
        # what the scorer would give him.
        {"observed_at": "A", "source": "futbolfantasy", "team_slug": "t",
         "player_slug": "vague-man", "player_name": "Vague Man",
         "start_pct": "", "role": "sub"},
        # After the cut: a prediction made once the teams were out is not a
        # prediction, and grading against it would flatter the source.
        {"observed_at": "Z", "source": "futbolfantasy", "team_slug": "t",
         "player_slug": "starter-man", "player_name": "Starter Man",
         "start_pct": "99", "role": "starter"},
        # A club with no confirmed line-up has not played.
        {"observed_at": "A", "source": "futbolfantasy", "team_slug": "other",
         "player_slug": "elsewhere", "player_name": "Elsewhere",
         "start_pct": "90", "role": "starter"},
    ]
    starters = [
        {"observed_at": "K", "team_slug": "t", "player_slug": "starter-man",
         "player_name": "Starter Man", "role": "starter"},
        {"observed_at": "K", "team_slug": "t", "player_slug": "bench-man",
         "player_name": "Bench Man", "role": "sub"},
        # Played, and the wider source never listed him: the absent fallback,
        # and a real observation rather than one to drop.
        {"observed_at": "K", "team_slug": "t", "player_slug": "surprise-man",
         "player_name": "Surprise Man", "role": "starter"},
    ]
    got = observations(lineups, starters, cut="M")
    assert all(o.group == "t" for o in got), got   # the sheet he was on
    by = {o.ff: o for o in got}
    assert len(got) == 4, got            # 3 listed + 1 who only turned up
    assert by[0.8].started == 1.0 and by[0.2].started == 0.0
    assert by[0.8].af == 1.0             # the narrow source's named starter
    assert by[0.2].af is None
    assert abs(by[0.6].ff - 0.6) < 1e-9  # listed, no number -> neutral
    assert by[0.15].started == 1.0       # never listed, and he started
    # Nothing from a club that has not played, and nothing from after the cut.
    assert all(abs(o.ff - 0.99) > 1e-9 and abs(o.ff - 0.9) > 1e-9
               for o in got), got
    assert observations(lineups, [], cut="M") == []

    # THE CROSSWALK MAKES THE NARROW SOURCE'S JOIN EXACT. It shares no slug
    # with anybody, so without one it is matched on a folded name at 66% — and
    # the third of it that misses is a source silently having no opinion.
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
    # The narrow source spells him differently, so the name join finds nothing.
    assert all(o.af is None for o in observations(odd, starters, cut="M"))
    # Through the crosswalk it lands on the right player.
    got2 = observations(odd, starters, cut="M", xw=_XW())
    assert any(o.af == 0.75 for o in got2), got2

    print("ffcore.startprob self-test OK (48 cases)")


if __name__ == "__main__":
    _selftest()
