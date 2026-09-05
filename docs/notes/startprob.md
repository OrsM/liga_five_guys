# ffcore/startprob.py — design notes

Full historical rationale for the comment blocks that used to sit inline.
Source carries a short rule + a pointer here.

## The two sources, and why they aren't equals

futbolfantasy (FF, "wide") publishes a percentage for most of the league;
analiticafantasy (AF, "narrow") publishes on far fewer players. On the
first jornada actually played, AF was much the better of the two where it
spoke — Brier 0.089 against 0.195, on the 28 players both covered.

But it speaks SELECTIVELY: 82% of the players AF covered actually started,
against a base rate of 48%. It publishes about obvious starters. So it
can't replace the wider source, only sharpen it where it has an opinion —
a blend has to fall back rather than go blank.

**FF's own problem is calibration, not ignorance**, and it may be the
bigger half. Measured against the same four matches: everything FF called
at 70%+ started every single time, and its 50% bucket started 62% of the
time. Its numbers are editorial buckets, not probabilities — the fix isn't
to trust them less, it's to learn what they mean.

## Nothing here is a hand-picked constant

Both parameters (Platt-scaling intercept/slope, AF blend weight) are fitted
from confirmed line-ups every run, and the fit is used only if it beats the
raw source OUT OF SAMPLE (leave-one-out). That's the whole guard — no
minimum-sample number needs choosing; with too little data the fitted
model loses to the identity on held-out points and isn't used. `note()`
prints which happened.

**The baseline it has to beat is what the system actually does**, fallbacks
included: unlisted-with-no-number scores `NEUTRAL_START`, not-listed-at-all
scores `ABSENT_START`. An earlier version compared against a baseline that
predicted ZERO for anyone unlisted — since the narrow source covers mostly
obvious starters, that baseline was marked wrong on players it never had
an opinion about, and reported the fit improving Brier by 0.306 against a
raw score of ~0.19 (an improvement larger than the thing improved — the
shape of a rigged comparison, not a good model).

## `METHOD_VERSION`

Bumped whenever `fit()`/`observations()` changes what it optimises, not
what data it sees. `score.py`'s on-disk cache (`startcal.json`) keys
itself on this alongside the data fingerprint. Real bug, caught before it
shipped: the fingerprint used to be `(len(truth), cut)` alone, so Step 4
switching the Brier target from binary played/didn't to minutes-graded
changed nothing about the DATA — the stale binary-fitted coefficients
would have kept being read from disk forever, until `starters.csv`'s row
count or earliest `observed_at` happened to move. A methodology change
with no data change is exactly the case a data-only fingerprint can't
catch.

## Platt scaling — why two parameters, not one

`logit(p') = A + B*logit(p)`. A single sharpening exponent can only
steepen the curve about its middle, and the data's actual shape is steep
AND shifted — the wider source's 30% bucket started 12% of the time, its
70% bucket 96%. Fitted with the exponent alone, 30% was driven to 0.01 to
buy accuracy at the top, understating a rotation player by a factor of
twelve. The intercept is what lets the curve move without being forced
through the middle.

## `FLOOR`/`CEIL` clamp

Nothing is certain, and a Bernoulli at 0 or 1 says otherwise. Left alone
the fit drives confident buckets to a flat 1.00 (on four matches, everyone
called at 80% did start) — while the wider source's OWN 100% bucket
started 80% of the time. A player who "cannot miss" costs the simulator
nothing when he does, so the sampled season stops containing the thing
that decides leagues. The clamp is on the output only; the fit is free to
want more than this and simply doesn't get it.

## `Obs.started` — graded on minutes (Step 4, 2026-08-21)

Not binary played/didn't. `minutes_played(role, minute) / MATCH_LEN`,
clamped to [0, 1]. P(start) is used downstream as a straight multiplier on
points-per-minute (`Scorer.score`: `flat = rating.ppm * pct_used / 100.0`),
so the quantity actually being predicted was always closer to "how much of
the match will he play" than "was he in the printed eleven" — a starter
hooked at half-time and one who plays the full 90 used to score
identically against the fit, discarding exactly the signal that
determines whether the multiplier is right. The field stays named
`started` because it's still true at the extremes and every existing
reader already expects a value in [0, 1].

`Obs.group` (whose team sheet he was on) is the unit of evidence: a
manager picking an eleven is ONE decision, not twenty-two independent
ones — cross-validating over players counts each sheet 22 times and
reports a confidence four matches can't support.

## `Calibration.fit` — leave-one-TEAM-SHEET-out

Every observation is predicted by a model fitted without the whole
line-up it belongs to, and the fit has to beat the raw source on that
held-out score. Replaces an "at least N observations" constant, and fails
in the right direction — too little data means the fit doesn't
generalise, loses, and isn't used.

**The group is the point.** Held out one PLAYER at a time, the model gets
to see the other 21 names on the same sheet — most of the answer, since a
manager picks eleven (knowing who else started is knowing who didn't).
That version reported a fit improving Brier by 0.031 out of sample and
drove confident buckets to a near-threshold classifier off four matches.
Holding out the sheet asks the actual question being asked of it in the
week ahead — a line-up it has never seen.

## `observations()` — the join and the universe

**The join is the crosswalk's**, when one is passed — every feed's key for
a player resolves to the same id with no guessing. Without it, confirmed
line-ups and the wider source share a slug (169/182 match) while the
narrow source shares nothing and falls back to a folded name at 66%.

**The universe is the wider source's own list, plus anyone who turned out
to play** — not the matchday eighteen (drops everyone left out of the
squad, exactly who the absent fallback is right about; grading without
them made ABSENT_START look far too low and flattened every confident
call to compensate), and not the market's roster either (third site, join
loses most names, silently doubling a player as one prediction that
started and one that didn't).

## `_titular_rate`

How much of the match AF's wordless "named starter" actually played, on
average — graded on minutes, same as everything else `fit()` scores
against. Falls back to 0.9 rather than 1.0 with nothing seen: a call with
no evidence behind it must not enter a Bernoulli at certainty.
