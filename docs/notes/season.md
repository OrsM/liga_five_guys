# ffcore/season.py — design notes

Long-form rationale relocated out of inline comments 2026-09-05, to cut
comment volume in the source file without losing the record.

## legal_shapes()/formations() divergence, fixed

`legal_shapes()` used to re-derive legal formations from `SLOT_MIN`/
`MAX_SLOT` bounds independently of `ffcore.score.formations()`'s own list.
Found 2026-09-01 (swarm review): bounds-derivation correctly reproduced the
7 free-tier shapes by coincidence, but a since-deleted `PREMIUM_FORMATIONS`
list (score.py) once violated those same bounds — e.g. (4, 6, 0) fielded
6 midfielders, over `MAX_SLOT["MED"]=5`. `SLOT_MIN`/`MAX_SLOT` describe the
free tier only, so a bounds-derived list could never reproduce a premium
shape set even if extended to try — two independent authorities that could
only ever agree by coincidence. Fixed by making `legal_shapes()` a thin
wrapper over `score.formations()`'s own list. (The premium flag itself was
later deleted outright, 2026-09-05, as unused plumbing — nothing anywhere
ever called `formations(premium=True)`.)

## `_run_np()`: the vectorized mirror, kept in sync by hand

`_run_np()` is a SEPARATE, numpy-vectorized reimplementation of
`ffcore.forecast.Bootstrap.rate_draw()`/`start_draw()`'s draw logic, not a
caller of them — the whole draw is embarrassingly rectangular (one coin +
one resample per player per trial; twenty million Python calls become two
numpy calls), so a process-pool-free vectorized path is worth having, but
it means every fix to the pure-Python formula needs a **deliberate, manual
mirror here** — nothing enforces the two stay in sync. They already
drifted apart once (the drift-walk bug below) and both files' self-tests
now separately pin the same invariants (e.g. `adjacent_corr > 0.7`) as the
tripwire.

**Not the same numbers as the Python path** — a different generator draws
a different sample from the same distributions; statistically equivalent,
not identical, and the report says so where it matters.

**Stream layout** (each independent source of "wrong" gets its own numpy
`default_rng` seed component, so none of them share bits with each other
for no reason):
- `[seed, 7919]` — the rate's own per-trial error (`eps0`)
- `[seed, 7920]` — the club shock (`shock`), only when `club_rel` is non-empty
- `[seed, 7921]` — the rate walk's per-jornada steps (`drng`)
- `[seed, 7927]` — start-probability's own per-trial error (`seps0`)
- `[seed, 7928]` — the start walk's per-jornada steps (`sdrng`)
- `[seed, j]` — the jornada's own outcome draw (unchanged, pre-existing)

**Club correlation** mirrors `rate_draw()`'s: the club shock is drawn once
per trial (does NOT drift — a season-long club-quality surprise is a
separate, smaller concern from per-player rate uncertainty) and applied
multiplicatively alongside the individual shock. Verified bit-for-bit
unchanged p_win on pinned data before this was wired up, which a real
widening of the bands could not produce by chance across thousands of
trials — confirms this numpy path is the one actually taken in production
whenever numpy is installed (it is).

**The drift-walk bug, fixed alongside `rate_draw()`'s identical bug,
2026-09-01:** this used to draw `drift` fresh every jornada from
`sqrt(cum_var)` — correct MARGINAL spread per jornada, zero correlation
between adjacent jornadas within a trial. Fixed by accumulating `walk`
(shape `(trials, keys)`, one realized path per trial — `cum_var` stays 1D
since the variance *schedule* is the same across trials, only the
realized path differs) inside the jornada loop instead of redrawing.
Log-normal form (`exp(walk - cum_var/2)`, not `clip(1+drift, 0)`) for the
same reason as `rate_draw()`: a clip is asymmetric and biases the mean
upward as the walk widens (caught while tuning DRIFT_FRAC, not a rounding
error). Scoped impact: `decide.rank()`'s paired BUY-ranking trials mostly
cancel this; the standings section's band/p_win/expected_finish is what
actually widens.

`start_draw()`'s mirror is additive/logit and already mean-zero, so it
carries no `cum_var` — same simplification as the pure-Python path.

A jornada still advances the walk step even when it has no scoring rows
(`order` has no keys for it) — skipping the step would make the walk's
width depend on which weeks happen to have zero scoring rows, an artifact
of the calendar rather than real time passing.

## `_antithetic_normal()` — variance reduction, not a model change

The standings section's headline p_win/expected-finish was a single
un-paired Monte Carlo draw, measured 2026-08-31 to swing roughly ±7 points
run-to-run at `FINAL_TRIALS=3000` — `decide.rank()`'s BUY/RAID ranking
already avoids this for PAIRED comparisons via `simulate_many`'s
common-random-numbers trick (one season, every candidate scored against
it), but nothing did the equivalent for the single, un-paired headline
number.

Antithetic variates: every Gaussian source in `_run_np()` (`eps0`, the
club shock, and both per-jornada walk steps — everything drawn via
`standard_normal`) is mirrored, trial `i` against trial `i + trials//2`
getting exactly opposite draws. Each trial's own marginal is untouched
(still standard normal), so this changes nothing about what is being
modelled — it only makes half the trials cancel the other half's
first-order noise once summed into a season total, the textbook use case
for antithetic sampling. The one draw NOT paired is the final per-jornada
Bernoulli-threshold-plus-pool-index draw (`rng.random`/`rng.integers`) —
pairing a discrete pool-index draw isn't a straightforward mirror, and
the persistent, season-long walks were the more promising target since
they're what compounds across 38 jornadas rather than washing out under
the CLT the way independent per-jornada noise already does.

Measured on real data (2026-09-16), not assumed: at `FINAL_TRIALS=3000`,
30 repeated runs each way, p_win's run-to-run sd went 0.0095 -> 0.0069 (a
27% tightening) and expected-finish's sd went 0.0189 -> 0.0152 (20%), with
the MEANS unchanged (p_win 0.5642 both ways; finish 2.178 vs 2.174) — a
real reduction in noise, not a shift in the answer. `_score_many()`
(`decide.py`, both the screening and final passes) now always requests
it; `simulate`/`simulate_many`/`_run_np` default to `antithetic=False` so
every existing self-test's exact-reproducibility assertions
(`same seed, same season`) are untouched — antithetic pairing is still
exactly reproducible for a given seed, it draws a DIFFERENT (lower-
variance) sample from the same distributions, same as the numpy-vs-Python
path is "statistically equivalent, not identical" above.
