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

## `_run_np()` — the vectorized mirror, kept in sync by hand

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
