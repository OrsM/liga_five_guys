# ffcore/score.py — design notes

Full historical rationale for the comment blocks that used to sit inline.
Source carries a short rule + a pointer here; this file carries the dates,
the measured numbers, and the rejected alternatives.

## SHRINK_K calibration

`SHRINK_K = 8.0` (pseudo-matches of prior weight, used at both shrink
stages: last season toward the positional median, then this season toward
that). Checked 2026-08-21 against what published win-probability models
actually rely on (FiveThirtyEight's NBA/NHL/MLB methodology) for "how much
should an early lead be trusted" — confirmed it's the same mechanism: a
one-time, measured revert-to-mean fraction on the prior, not a forward
drift term.

Fitted for real 2026-08-31 (the prior note said blocked on MIN_POOL=200
matches; that cleared a week earlier — 729 real matches in
`data/season/live/perjornada_2026-27.csv`). Two out-of-sample tests, both
against the same store the scorer itself reads:

- **A. Cross-season**: predict each of 534 real 2026-27 per-match scores
  (for players with a 2025-26 record) from the K-shrunk last-season rate.
  Training and scored samples are different seasons — no leakage.
- **B. Walk-forward within 2026-27**: both stages, one shared K, predicting
  each jornada from a player's earlier ones only (n=271). Walk-forward
  rather than leave-one-out for the same reason `_fit_decay()` uses it —
  leave-one-out lets a later jornada leak into a "prediction" for an
  earlier one.

| K | A: MSE | B: MSE |
|---|---|---|
| 0 | 15.514 | 26.264 |
| 4 | 15.287 | 16.597 |
| 6 | 15.279 ← | 16.382 |
| 8 | 15.285 (shipped) | 16.296 (shipped) |
| 16 | 15.365 | 16.237 ← |
| 32 | 15.555 | 16.326 |
| ∞ (prior alone) | 16.572 | 17.122 |

Both tests agree shrinking helps a lot and disagree on how much, in
opposite directions — A's optimum is K=6, B's is K=16, straddling the
shipped 8. The basin is flat: running 8.0 instead of each test's own
optimum costs +0.04% MSE on A, +0.37% on B. A cluster bootstrap over
PLAYERS (4000 resamples, whole players resampled since one player's
matches aren't independent) puts 90% of A's argmin between K=1–20, 90% of
B's between K=5–48. 8.0 sits inside both.

**So the number does not move, its description does.** This is no longer
"a round number nobody checked" — it's a value 729 real matches can't
distinguish from the out-of-sample optimum, a weaker (and truer) claim
than "fitted to 8.0". What would actually move it is splitting the two
stages (A and B genuinely want different Ks, forced to share one here) —
but B's n=271 is too thin to justify that today. Revisit at a completed
season.

## Current-season blend units: minutes, not appearances

`matches_now` in the current-season blend is MINUTES, not an appearance
count (see `_current_from_perjornada`). A 10-minute cameo and a full 90
were weighted identically before this — the same distortion the prior-side
shrinkage already exists to correct, silently reintroduced on the live
side.

## The fixture factor: score vs flat

`score` carries the fixture (opponent, home/away, from `ffcore.fixture`);
`flat` is the same arithmetic without it. Both are returned because they
answer different questions: you FIELD for one round (fixture belongs), you
BUY for months (it doesn't). A bid sized on a kind fixture is a bid for a
fixture, not for a player.

## What the ranking index cannot know

Three things, surfaced rather than hidden:
- Promoted-side players have no top-flight record and fall back to the
  positional prior (median top-flight starter, which flatters them) —
  marked `assumed` and discounted (`PROMOTED_DISCOUNT = 0.70`). Promotion
  is detected from the data, not hardcoded.
- A player absent from the probable-XI page is not the same as one listed
  with no percentage — first gets `ABSENT_START`, second `NEUTRAL_START`.
- Nothing here is checked against reality yet — log the inputs alongside
  every recommendation and score once jornadas exist.

## xG/xA precision-weighted blend

Mechanism: Tango/Lichtman/Dolphin's precision-weighted blend (*The Book*,
ch.4's clutch-skill estimate) — not another ad hoc SHRINK_K stage.

**Why a new mechanism, not another shrink stage:** this repo's own check
(2026-08-21) found xG a *worse* fit to actual fantasy points than raw
goals+assists (r=0.275 vs 0.381) — unsurprising, points reward the result,
not the process. But fit-to-outcome is the wrong test for whether xG is
useful; the right one is year-over-year STICKINESS (does the same
player's rate in one season predict his rate in another — a stat that
repeats is measuring skill, one that doesn't is measuring luck). Measured
on this repo's own data (same player across two seasons, n=85, last
season 450+min, this season 30+min): goals/90 r=0.169, xG/90 r=0.222,
G+A/90 r=0.306, xG+xA/90 r=0.303 — xG alone IS stickier than raw goals
alone; folding in assists roughly closes the gap. This is the DIPS pattern
(McCracken: strikeout/walk rate repeats far better than ERA) applied to
this repo's own numbers rather than assumed from the literature.

**Position-gated, measured not assumed:** restricted to forwards/attacking
mids (Understat's own "F" tag) because the same check showed the *opposite*
sign for every other position (n=46, corr(xG+xA, early points) = -0.169) —
a defender's/keeper's points come from clean sheets and defensive actions,
which an attacking metric says nothing about. The defensive analogue (xGA,
Understat's team-level `getLeagueData`, already GET-accessible) is real
and not yet captured; see the 2026-08-21 handoff.

**Wired in with variance derived from what exists today**, not guessed or
deferred: `ffcore.startprob.Calibration.fit()`'s cache-on-fingerprint
already grades and refits a live parameter against reality as it arrives;
`_xg_stickiness_boost()` is built the same way, so it strengthens on its
own as more paired Understat seasons accumulate.

**The two fitted numbers, neither hand-picked:** `_xg_points_fit()`
converts xG+xA/90 into this scoring system's points/match via OLS on last
season's real (xG, ppm) pairs. `_xg_stickiness_boost()` turns measured
year-over-year correlation into pseudo-matches, via the same
reliability = n/(n+K) relationship SHRINK_K assumes — a stickier stat
implies a smaller effective K. The apples-to-apples comparison is G+A/90
vs xG+xA/90 (both include assists), not goals alone vs xG+xA, which would
unfairly favor xG. On that fair comparison the two are close to tied
(2026-08-21: r=0.306 vs r=0.303, boost≈0.99). Below a floor of paired
players this refuses and returns boost=1.0.

`Scorer.rate()` folds this in as a THIRD weighted term alongside the prior
and current season — the original two-term formula is the special case
with no xG term (weight = pseudo-matches: K for the prior, real matches
for current season).

## `_precision_blend` — The Book's worked example

(mean, variance) for independent estimates of ONE quantity, combined by
inverse variance. Reproduces *The Book*'s own worked example (ch.4,
clutch skill): measured clutch skill +.100 (wOBA) over 100 clutch PA,
sampling uncertainty .055; population's own clutch-skill spread .000 ±
.006. Weighted 1/variance: +.001 — almost entirely the prior, because 100
PA is a sliver of evidence next to the population's tightly-known spread.
Reproduced in this module's self-test. An estimate with variance ≤ 0 is
skipped (0 would claim infinite precision, which no real measurement
here has); `None` back means nothing usable was offered, never a
fabricated answer.

## `_xg_points_fit` — units conversion

Last season's real points-per-match as a linear function of last season's
xG+xA per 90 (same position gate as `load_understat_current`). A unit of
xG is only worth whatever THIS scoring system actually pays for the
goals/assists it tends to produce — nothing in the literature knows that;
this repo's own pairing
(`data/season/points_2025-26.csv` × `data/tidy/understat_players.csv`)
does. Below 10 paired players this refuses (slope 0, intercept 0) rather
than fit a line through noise.

## `_xg_stickiness_boost` — year-over-year reliability

How many raw current-season matches one xG-informed match is worth,
derived from measured year-over-year stability (no crosswalk needed —
both seasons carry Understat's own `understat_id`, exact pairing). *The
Book*'s logic (ch.2-4: a stat's reliability IS how much it repeats for the
same player across independent samples) — reliability relates to needed
shrinkage the same way SHRINK_K does, `reliability = n/(n+K)`. Comparing
the K implied by xG+xA/90's year-over-year correlation against the K
implied by raw G+A/90's own gives a real, self-updating ratio. (Goals+
assists, not goals alone — `xg90` everywhere in this module is xG+xA, so
its fair raw counterpart is G+A; an earlier version compared goals alone
against xG+xA and understated the raw side.)

Self-correcting, not frozen: recomputed from whatever
`understat_players.csv` holds when called (no cache, unlike
`Calibration.fit()`, since it costs microseconds against ~200 rows).

Below 30 paired players (this repo's own count as of 2026-08-21: 85, well
past the floor, but a fresh store could start thinner) this refuses and
returns (1.0, why) — an xG match trusted no more than a raw one. Clipped
to [0.5, 3.0]: a single noisy correlation swing shouldn't let one xG match
outweigh six raw ones, or count for half of one.

## `_per_jornada_current` — the join, and the points_total anchor

The join points.py's own docstring called "model code to do later":
starters.csv's minutes are keyed by match_id, matches.csv translates that
to a jornada number, perjornada.csv's points now carry their own `jornada`
column (`points.match_jornadas()`, stamped from when a match's score was
first seen in the tidy store's history — the closest thing to a calendar
this repo has without a kickoff-date feed in a matching id space). A
jornada absent from either side is dropped rather than guessed.

**Anchored on `points_total`, not summed from `points_delta`** — a real
bug, caught on real data: `points.py`'s `diff()` never emits a row for the
very FIRST kept snapshot (nothing precedes it to diff against), so a
player who already had points on the board by then has that baseline in
no delta at all. Measured: Abde Rebbach's one row said `points_delta=7`,
`points_total=11` — the missing 4 is whatever he had before this file's
own history starts. Summing deltas alone would have under-rated him
forever; `points_total` is the page's own cumulative figure and carries
no such gap. Last write for a jornada wins (points.py writes rows
chronologically) — a correction row overwrites the running total, not
adds to it twice.

**The universe is the points-page's own**, not everyone starters.csv ever
names — checked on real data and deliberately narrower than a plain union:
90 players with real starters.csv minutes carry NO row on the points page
at all (verified zero, not a join failure). Whether that silence means
"scored exactly zero" or "this page doesn't track him" isn't knowable —
same NEUTRAL_START/ABSENT_START distinction this repo already draws for
the other source. Including him at pts=0 would shrink a possibly-real
season toward zero on a guess; leaving him out keeps him on last season's
rate (matches the pre-2026-08-21 universe exactly).

## `_weighted_totals` / `_weighted_start` — recency weighting

Most recent jornada with a row weighs 1; one back weighs `decay`; two back
`decay**2`; decay=1.0 collapses to an exact flat sum (today's cumulative
behaviour, no special case). `_weighted_start`'s rate lives in [0, 1]
(share of a jornada, `Σ(w · min(1, minutes/90)) / Σw`) and can stand in
for a start probability directly — a silent jornada (0 minutes) pulls the
rate down; a jornada nobody has a row for yet doesn't enter the sum.

## `_fit_decay` — walk-forward validation

Recency weighting earns its use ONLY if it beats the flat average out of
sample — same discipline `Calibration.fit()` uses for P(start)`.

**Walk-forward, not leave-one-out** — deliberately not the pattern
`Calibration.fit()` uses (holding out a team SHEET, order irrelevant,
since sheets have no time-order that matters). Jornadas do: predicting
jornada 3 from jornadas 1 and 5 is hindsight, not a forecast — scored that
way once, it handed a run of monotonically increasing jornadas a training
set with FUTURE data on both sides of the "predicted" point, so decay
could never show an edge (the flat mean of two symmetric bracketing
points already equals a linear trend's midpoint) no matter how real the
trend was. Here jornada J is only ever predicted from jornadas strictly
before it — exactly like the live report the week before a new one is
played.

The test: for each player's jornadas in order (from the second onward),
predict per-match rate from everything strictly earlier (weighted by each
decay candidate), score against what he actually returned (points per
match while actually on the pitch — a jornada he didn't feature in
contributes to neither side). Needs at least one player with 2+ distinct
jornadas; at the time of writing the whole sample is on 1, so this always
returns decay=1.0 correctly (no evidence recency weighting would help
yet).

## `_current_from_perjornada` — why not points_*.csv

`data/season/live/perjornada_*.csv`, not `points_<season>.csv` (which
`load_points()` also checks): that file snapshots the points PAGE, which
reads empty until J1 is fully played (see this module's opening
docstring). `perjornada_*.csv` is written every run from snapshots already
taken (points.py) and has real numbers from the first confirmed match —
the actual live source, not a hypothetical future one.

**Keyed through the crosswalk, twice** — once to join perjornada's own
`ff_id` (IS the crosswalk's player_id directly, verified 5/5) to a
canonical player, again to translate back into `norm(market name)`,
because that's what `Scorer.rate()` actually looks `self.current` up by.
Going name-to-name directly (the first version) meant perjornada's own two
name columns and starters.csv's short form were three different spellings
of the same person — agreeing by luck on some players, silently giving
others zero minutes.

`"pj"` is minutes, not an appearance count (same fix as `matches_now`
above, applied at the live source).

**Recency-weighted now, not a flat season average** — Step 1 of the
2026-08-21 forecasting plan. Both `pts` and `pj` are rebuilt per jornada
and combined with a decay chosen the same way `Calibration.fit()` chooses
its parameters (used only if it beats flat, walking forward). With one
jornada on record (this repo's state at time of writing), this is a flat
sum — verified byte-identical against the pre-change output. It starts
weighting recent form the moment there's evidence it helps, not before.

## `load_points` — the two-file prior

PRIOR: the newest `data/season/points_*.csv` (last season's completed
totals, written once a year by `ingest.py baseline` on season flip).
CURRENT: this season's live per-jornada tracker (see above).

The historical two-points-files shape (this season's own `points_*.csv` as
"current", shrinking into an even older prior) is kept for the day
`baseline` runs again at THIS season's actual close; perjornada takes
priority whenever it has data.

**Two files, not one, on the PRIOR side specifically.** The newest
`points_*.csv` is what a naive read would call "this season" — reading
only the newest (what report.py and rivals.py each did, in their own copy
of this function, before this module existed) was a bug waiting for the
season to roll over: the moment `points_2026-27.csv` appears as a
completed-season snapshot, every rating would rebuild from that one file
and the actual prior would vanish.

## `build()` — one model per run

`report.py` and `rivals.py` must score with identical arithmetic — the
whole reason this module was lifted out of report.py; they previously
held a copy each of the points loader and neither knew about the fixture
board, so a comparison between your squad and a rival's would have been
between two different models. `calibrate` fits P(start) against confirmed
line-ups (costs a few seconds) and turns itself off with nothing played or
a fit that loses on unseen line-ups.

## `_calibrated` — caching and the fingerprint bug

Cached per-process: the fit cross-validates over every team sheet on
record and costs a few seconds, and `build` is called more than once in
some runs with an answer that can't change between calls. The cut is the
first confirmed line-up seen — anything published after that may already
be the team sheet rather than a forecast of it, and grading a forecast
against itself is how a model marks its own homework.

On disk, keyed by what it was fitted on (`TIDY/startcal.json`) — the fit
costs ~6s in EVERY process, and the chain runs several. A changed
fingerprint refits; nothing else does. **`METHOD_VERSION` is part of that
evidence, not just the data** — real bug, caught before it shipped: the
fingerprint used to be data-only, so Step 4 changing what `fit()`
optimises (binary played/didn't → minutes-graded) touched no confirmed
line-up and no cut, and the stale binary-fitted coefficients would have
kept being read off disk forever.

## `Scorer.__init__` — key joins

- **The same key the market index uses.** Keyed on `norm(name)` alone,
  this held one row for the two Álvaro Garcías — a squad correctly naming
  the Rayo one scored blank, because the only row filed under that name
  was the Villarreal one.
- **`ff_slug` → market key.** The team pages DO publish player links
  (`/jugadores/<slug>`, 153 on one page) — an earlier comment said they
  didn't, so the one identifier both files share went unused and the join
  ran on names. By slug, 497/512 XI rows reach a player, none ambiguous;
  by name, 494 do and three name two men.
- **Second source, keyed by identifier like everything else** — this used
  to be keyed by name while `score()` looked players up by id, so every
  player silently lost the second source and every calibrated P(start)
  moved with it. These rows carry the same name-slug the probable-XI pages
  do: 247/274 reach the crosswalk's `ff_slug`, none reach `af_slug`.

## `Scorer.score` — the P(start) blend

- **Scaling by P(start) prices a non-start at zero** — only right if a
  benched player can't be covered from the bench. The free tier has no
  auto-substitution (verified in-app 2026-08-16, issue #28), so it's
  right: a benched starter costs his whole score. If auto-subs ever
  arrive, this multiplication is the line to change.
- **The source's figure is not a probability until graded.** `raw` is the
  page's figure (or the fallback); `pct_used` is what that's been WORTH
  against confirmed line-ups, blended with the second source where it has
  an opinion. Until a jornada is played, calibration is the identity and
  they're the same number.
- **Blended against real recent minutes**, same K-shrink stage as the
  points side (`self.current`'s `start_rate`/`start_n`). Editorial P(start)
  is real news (this week's team talk, a fresh knock) that minutes history
  can't know, so it's the WHOLE answer until a player has actually
  featured; once he has, real recent minutes pull the number toward what's
  happening.
- **`pct_used` answers for the NEXT jornada only** — the only match this
  week's editorial page and status flag describe. `Bootstrap.__init__`
  used to be handed this same number for the WHOLE remaining season
  (`decide.load()` reused one `base` dict per jornada), so a player
  suspended for one match read as ~unlikely to start for the other 37
  too, and a rested player never recovered in the forecast. On 2026-08-25
  this priced a first-choice centre-back (91.7% of the season's minutes,
  one card suspension) at 27% for the rest of his season, and had
  `decide.dead_weight()` list him as sellable for zero points.
  **`pct_rest`** is what jornadas AFTER the next one get instead: his own
  recency-weighted minutes share, shrunk toward `NEUTRAL_START` (not
  toward this week's status-tainted reading) by the same `SHRINK_K`.
  Falls back to `pct_used` when he has no current-season minutes at all
  (a debutant's forecast can't know more about jornada 10 than jornada 3).
- **A clean sheet is opponent-attack-driven, a goal opponent-defense-
  driven** — the whole reason `Match` carries two factors. An unrecognised
  slot gets the attacking number rather than crashing (attacking is the
  larger group).

## replacement level — why not λ

Pricing a player by what YOUR eleven loses without him answers one
question (does he play Saturday) and is wrong for every other, because the
answer changes as you act — sell one midfielder and every other
midfielder's number goes stale, so the ranking stops being a list of
decisions.

Fixed baseline instead (value-based drafting's answer): value is what a
player is worth ABOVE THE LEVEL THE MARKET SUPPLIES FREE at his position —
the rung where the league runs out of starters. Five managers starting
four defenders each makes the 20th-best defender replaceable by anyone,
and nothing above that depends on your own eleven. Not the positional
AVERAGE (far higher, would price most of the league negative and hide real
scarcity); not value-weighted (drags the bar toward whoever is expensive).
The rung is a MEAN over legal shapes, since 3-4-3 and 5-4-1 start different
numbers of defenders — keeps the eleven adding to eleven while letting a
position only some formations use price as scarcer.

## `formations()` / the now-deleted premium flag

A `premium: bool` parameter (plus `PREMIUM_FORMATIONS`, and `pick_xi()`'s
own matching parameter) used to exist for the rare rival on a paid
subscription. Deleted 2026-09-05 as dead plumbing — nothing anywhere ever
called it with `premium=True` — not as a feature decision. Add it back the
same way if a caller genuinely needs it.
