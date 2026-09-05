# ffcore/forecast.py — design notes

Long-form rationale relocated out of inline comments 2026-09-05, to cut
comment volume in the source file without losing the record. Nothing here
is new; it is the same facts, dates and numbers that used to sit next to
the code, condensed and organized by topic.

## DRIFT_FRAC calibration history

`DRIFT_FRAC` controls how much a player's rate can drift per jornada that
passes, as a fraction of his own `rate_rel` (not a flat number — a less
predictable player also drifts more).

**Why it exists.** `Bootstrap.rate_draw()` used to draw a rate's error ONCE
per trial and hold it flat for jornada 3 and jornada 35 alike — real for
"my rate estimate could be biased," wrong for "my rate could also have
DRIFTED by then." A squad's true relative strength is not a fixed unknown
constant for 38 rounds (transfers, injuries, form). Measured on this
repo's own 4 seasons of actual La Liga results (2026-08-21): a club's
cumulative table points after jornada 1 correlated with its FINAL points
at r = 0.315, 0.668, -0.084, 1.000 (n=10, unreliable) — often far from
1.0, one season effectively zero.

**The magnitude is a judgment call, not a fit.** That La Liga correlation
is table points aggregated across 19 opponents; DRIFT_FRAC is per-player
rate uncertainty diluted across ~11-16 largely-independent squad players —
no clean unit conversion between the two. Measured directly instead, by
sweeping DRIFT_FRAC against this repo's own real squad data (2026-08-21,
one jornada played) and watching what it does to `p_win`:

| DRIFT_FRAC | width | p_win |
|---|--:|--:|
| 0.00 | 400 | 0.895 (today's flat-for-the-season behaviour) |
| 0.50 | 424 | 0.863 |
| 1.00 | 539 | 0.737 (shipped) |
| 1.50 | 739 | 0.621 |
| 2.00 | 1026 | 0.555 |
| 3.00 | 1692 | 0.485 |

1.0 was picked as a round number landing in a materially more humble zone
without erasing the real, measured squad-quality gap entirely (this
repo's manager squad is real money, 220M+, not noise) — not for hitting
any specific target p_win.

**Tried to pin it down further (2026-08-21).** Two real anchors on
`results_history.csv` gave opposite steers on the exact magnitude:
jornada-1-vs-final club table points correlate weakly (r = 0.11, 0.26,
0.45); season-to-season club table points correlate strongly (r = 0.71,
0.88). Neither converts cleanly into a per-player weekly drift — chasing
exact precision here is a dead end, since no real-data quantity in this
repo measures within-season drift directly.

**That ambiguity wasn't a reason to do nothing.** Every published
win-probability model checked (FiveThirtyEight's NBA/NHL/MLB methodology,
2026-08-21) is far more humble than 70%+ about a single-outcome
full-season question this early, regardless of which anchor above you
trust. This repo's squads are not a blowout gap (220M+ vs 220M-ish), so
there's no version of "trust the early gap fully" defensible at 70%+
this many jornadas out. DRIFT_FRAC moved 1.0 → 2.0 on that basis
(p_win 0.724 → 0.555 on live data, 2026-08-21) — near a coin flip while
still nodding to the measured squad-value gap (erasing it outright would
be DRIFT_FRAC ~3.0, p_win 0.476, claiming more certainty of no edge than
the data supports either).

**Reverted to 1.0 the next day** (`6cefe65`, 2026-08-22): the
season-long win-probability debate turned out mostly orthogonal to the
actual decision engine (`decide.rank()` is a paired comparison against
the SAME simulated seasons, so absolute uncertainty mostly cancels there)
— it only ever affected the standings section's headline number. A
`sim.py` caveat about this constant hardcoded the string "DRIFT_FRAC=2.0"
regardless of what actually ran, so it kept citing the pre-revert value
for over a week — fixed 2026-08-31 to read the live value.

**The re-tuning bar this note originally set was checked, 2026-08-31, and
turned out to be the wrong bar.** It used to say "revisit downward once
reports/METHOD.md's own 'Forecast vs actual' table has n=15-20+ rows." It
now has n=39. The table cannot grade this constant AT ANY ROW COUNT — a
structural reason, not a thin-sample one:

- DRIFT_FRAC is entirely about how uncertainty GROWS WITH HORIZON — it adds
  `cum_var = (DRIFT_FRAC * rate_rel)^2` per jornada and nothing at horizon
  zero. Every pair the table can hold is at horizon ONE: `points.py`'s
  per-jornada diff only emits `games_delta` of 0 or 1 (checked: 729 ones,
  29 zeros, nothing else) and `methodology.pair()` drops the zeros. There's
  no horizon variation to fit a growth rate to — more jornadas just add
  more rows at the same single horizon.
- At horizon one the drift term is buried anyway. The real pool's own
  coefficient of variation is 0.973 (729 matches) against `rate_rel`'s
  median ±17%, so one jornada of walk at DRIFT_FRAC=1.0 is 2.7% of a
  player-match's predictive variance and 1.4% of its RMSE. An RMSE
  estimated on n pairs is good to about 1/sqrt(2n) — ±11% at n=39.
  Separating 1.0 from 0.0 needs ~5,200 pairs (~350 jornadas, nine
  seasons); separating 1.0 from 2.0 needs ~680 (~45 jornadas). Not a
  wait — a dead end.

What the table does say, recorded rather than leaned on: realised RMSE
3.95 points per player-match, against a modelled 3.68 at DRIFT_FRAC=1.0
(3.58 at 0.0, 3.96 at 2.0). If anything the model is slightly narrow at
one jornada, arguing against tightening — but that's 7% on a measurement
good to 11%, a direction, not a finding.

**Re-run on real data, 2026-08-31** (3 jornadas played, 36 left, 729
observed matches, same baseline-squads-only pass):

| DRIFT_FRAC | 10-90 width | p_win | E[finish] |
|---|--:|--:|--:|
| 0.00 | 312 | 0.196 | 2.38 |
| 0.50 | 329 | 0.202 | 2.39 |
| 1.00 | 403 | 0.222 | 2.39 (shipped) |
| 1.50 | 544 | 0.252 | 2.35 |
| 2.00 | 728 | 0.266 | 2.32 |
| 3.00 | 1027 | 0.287 | 2.25 |

**The sign flipped**, worth noting for anyone re-reading the 2026-08-21
argument: then, this manager's squad was AHEAD, so widening pulled p_win
DOWN toward a coin flip (0.895 → 0.485) and "be more humble" read as
"lower p_win." He is now behind, so widening pushes p_win UP toward the
same coin flip (0.196 → 0.287). The mechanism does the same thing in both
worlds; the DIRECTION of the original argument was an artifact of the
standings on the day it was written, not a rule that "wider means less
confident."

**Still 1.0, deliberately.** Nothing measured moves it: the only real-data
check this repo can run is structurally blind to it, and the correction
the 2026-08-21 note argued for (don't claim 70%+ this early) is already
satisfied at 1.0 from where the table stands. What would unblock a real
fit is a horizon ladder — predictions logged h jornadas out and graded at
several different h — a change to what `data/decisions/squad_log.csv`
records, not a matter of waiting for rows to accumulate.

## The drift-walk rate_draw()/start_draw() bug and fix

Found 2026-09-01 (swarm review of the forecasting engine). `rate_draw()`'s
walk (and `season.py`'s independent numpy mirror, `_run_np()`) used to
redraw `drift = rng.gauss(0, sqrt(cum_var[k]))` fresh every jornada. That
gives each jornada the correct MARGINAL spread (a sum of independent steps
has that variance) but ZERO correlation between adjacent jornadas within
the same trial — jornada 12 and jornada 13's drift were independently
redrawn, sharing none of the same walk. A real random walk doesn't do
that: consecutive positions share almost all of their history, differing
by one step.

Effect: summing a trial's points over a season, independent per-jornada
noise partially cancels under the CLT, UNDERSTATING exactly the
persistent, compounding variance the feature exists to add. Scoped
impact: `decide.rank()`'s BUY ranking runs PAIRED trials (with the move,
without it, same draws) so this mostly cancels there — the standings
section's p_win/expected_finish/band is what actually widens once fixed.

Fixed by accumulating independent per-jornada STEPS into a running
position (`walk[k] +=` inside the jornada loop) instead of redrawing from
cumulative variance each time. `rate_draw()`'s log-normal form
(`exp(walk - cum_var/2)`, not `clip(1 + drift, 0)`) keeps the mean at 1.0
however wide the walk gets — clipping a wide gaussian at zero is
asymmetric (floors the negative tail, leaves the positive tail
unbounded), which biased the mean upward the wider the walk got (measured
while tuning DRIFT_FRAC: mean inflated ~1780 → ~3150 points at a wide
setting, nothing to do with real uncertainty). `start_draw()`'s walk is
additive on a logit shift and already mean-zero, so it needed no matching
cum_var bookkeeping.

The self-test's `adjacent_corr > 0.7` assertion (jornadas 1 vs 2) is the
one the old, redraw-per-jornada code would have failed; `distant_corr`
(jornada 1 vs 22) checks the correlation weakens with distance, as a real
walk should.

## rate_draw()/start_draw() — two independent sources of "wrong"

A player's own rate can be off (`rate_rel`, now plus its own drift via the
walk above), and separately his whole CLUB can be having a stronger or
weaker season than its own `attack_defense()` rating expects (`club_rel`
— see `ffcore.fixture.club_volatility`). The second is what makes two
players of the same club move together in a trial instead of
independently — see the module docstring's "the baseline ignores that
correlation." A player with no `club_of` entry (club too thin on history,
see `MIN_AD_MATCHES`) draws exactly as before: `shared=1.0` is a no-op,
not a guess. The club shock itself does NOT drift — one per trial, same as
before; a season-long club-quality surprise is a separate, smaller
concern from "how far can I trust today's player rating."

`start_draw()` has no equivalent club term: a club-wide rotation shock (a
new manager who plays everybody less) is real but a second, smaller
effect on top of "how far can this one man's own start rate be trusted,"
and stacking an unfitted guess on an unfitted guess buys confusion, not
accuracy. Left for later, same as `rate_draw()`'s own club term was until
`club_volatility()` existed to measure it.

Both draw club shocks / players in a fixed sorted order, always — the rng
is shared across the whole round, so which call lands on which key
decides its value, and dict iteration order is not something two
processes are guaranteed to agree on (`__init__`'s own `self._order`
exists for the same reason).
