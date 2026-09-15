# Codebase rationalization plan — started 2026-09-15

## Success criterion (the user's own words)

> Whatever is coming out of the players I should buy when we're done makes
> sense as an economical decision.

Every session below is judged against this, not against a line-count or
function-count target. Fewer functions is not the goal — it was
considered and explicitly rejected as a primary metric (see session log
2026-09-15): it can be gamed by cramming logic into fewer, more tangled
functions, which is worse for the actual goal (fast, correct answers to
"why did the model recommend this").

## Context: this is hardening, not a rewrite

The proposed 5-stage architecture (scrape → tidy → player objects →
forecast → decide/report) is already this codebase's real shape
(`sources.py`/`ingest.py` → `ffcore/tidy.py`/`ffcore/crosswalk.py` →
`ffcore/profile.py` → `ffcore/forecast.py`/`ffcore/score.py` →
`decide.py`/`sim.py`). A full rewrite was considered and rejected: it
would discard a system already validated against real outcomes, for no
architectural gain the plan below doesn't already capture.

A same-day duplication audit (docs/notes/duplication-audit-2026-09-14.md)
already fixed 13 real issues. This plan continues that work, session by
session, plus fixes the funding-noise bug found 2026-09-15 (the specific
bug that violates the success criterion above: `decide.rank()` picks
which spare funds a buy using a noisy 250-trial screen and never
re-checks that choice at the reliable 3000-trial pass, and treats a
currently-STARTING player as interchangeable with real bench dead
weight).

## Sessions

- [x] **1. Scrape/ingest.** `sign_calendar`/`sign_af_fixtures` now route
  through `_css()` (commit 3cee3ef). Graceful-degradation path
  ("matched no known markup" warning) confirmed already sufficient —
  no further action.
- [x] **2. Tidy/identity.** Verified — `ffcore.tidy`/`crosswalk.py`/
  `ffcore.crosswalk` self-tests all clean. Confirmed already the
  cleanest layer per the audit; no changes needed.
- [x] **3. Player objects + forecast.** `ffcore.profile`/`ffcore.forecast`/
  `ffcore.score` self-tests all clean. Observability work parked (see
  Session 4's note) — `gap_signal.py` already keeps the one live
  hypothesis re-runnable.
- [~] **4. Decision engine.** THE ONE THAT DIRECTLY SERVES THE SUCCESS
  CRITERION. **Funding-noise bug fixed and verified (commit f70ad6c)** —
  `rank()` no longer treats selling a currently-fielded starter as
  interchangeable with selling bench dead weight; confirmed across 5
  seeds that the recommended funding source now varies only among real
  bench players. Still open: split `decide.py` (2,206 lines — candidate
  generation / funding / ranking tangled together) along its real seams.

  **`explain` tool: parked, not scheduled.** Deliberately deferred —
  building an observability feature before the codebase is simplified is
  itself a step away from simplification, and revisiting it once
  `decide.py` is actually split makes it cheap (a trace output on
  already-clean functions, not a new subsystem) rather than something
  worth designing carefully now. If revisited: build it AS a `trace=`
  output on `rank()`/`candidates()` themselves, never a separate script
  that calls them — a sibling script risks becoming a second,
  independently-drifting implementation, exactly the failure class this
  whole plan exists to remove. Why: session log 2026-09-15.
- [ ] **5. Buy/sell decision tree.** Formalize the cash-need logic
  (need cash outright / have spare / need a specific sale to fund X) as
  explicit, testable branches instead of logic implicit in ranking order.
- [ ] **6. Rival-raid prioritization.** One best raid per opponent, by
  harm-to-them + economic sense — a scoped new feature on the now-clearer
  decision engine.
- [ ] **7. Close-out.** Re-run the duplication audit against the new
  shape. Update docs. Retire this plan doc into the session log below.

## Session log

**2026-09-15** — Plan written. Session 1 closed (3cee3ef). Sessions 2-3
verified clean, no changes needed. Session 4's core bug fixed and
verified against the real Brugué/Vicente case (f70ad6c). `decide.py`
split twice: `ffcore/schedule.py` (per-player jornada scheduling,
e927f79) and `ffcore/pricing.py` (locked/burn/cash_price/respond,
e57ce85) — 2,206 → 1,688 lines, every self-test and the full pipeline
verified clean after each cut. `explain` tool stays parked (see above).

Remaining in `decide.py`: `candidates()`/`dead_weight()`/
`overdraft_fix()`/`apply()`/`offer_combos()` (funding/candidate
generation — tangled with `current_xi()`/`player_forecasts()`, a real
circular-import risk if split naively) and `rank()`'s own group
(`_score_many`/`paired`/`band`/`_top_up`/`value_rate`/
`player_forecasts`/`rank` — the ranking/simulation glue). Both groups
are more interdependent than the two already split; the next cut needs
the same dependency-mapping care as this one, not a rush.

Next: Session 5 (buy/sell decision tree) or finish Session 4's split —
either is reasonable to start fresh.
