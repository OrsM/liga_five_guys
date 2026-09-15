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

- [ ] **1. Scrape/ingest.** Close remaining low-priority items from the
  2026-09-14 audit: `sign_calendar`/`sign_af_fixtures` still bypass the
  `_css()` cache (consistency nit, not correctness). Verify graceful
  degradation when a source's HTML shape changes (the "matched no known
  markup" warning path already exists — confirm it's sufficient).
- [ ] **2. Tidy/identity.** Already the cleanest layer per the audit —
  verify only, don't rebuild.
- [ ] **3. Player objects + forecast observability.** Give this layer a
  "why is this number X" answer path — the gap this session's Lejeune
  investigation exposed (no existing way to trace a forecast back to its
  inputs without a fresh ad hoc script each time). Keep `gap_signal.py`'s
  parked hypothesis alive as one command, not lost history.
- [ ] **4. Decision engine.** THE ONE THAT DIRECTLY SERVES THE SUCCESS
  CRITERION. Split `decide.py` (2,206 lines — candidate generation /
  funding / ranking tangled together) along its real seams. Fix the
  funding-noise bug: re-screen near-tied funding variants at real trial
  counts before picking one to show; don't treat a currently-fielded
  starter as interchangeable with bench dead weight. Build
  `decide.py explain <player>` — trace a real recommendation's numbers
  in one command.
- [ ] **5. Buy/sell decision tree.** Formalize the cash-need logic
  (need cash outright / have spare / need a specific sale to fund X) as
  explicit, testable branches instead of logic implicit in ranking order.
- [ ] **6. Rival-raid prioritization.** One best raid per opponent, by
  harm-to-them + economic sense — a scoped new feature on the now-clearer
  decision engine.
- [ ] **7. Close-out.** Re-run the duplication audit against the new
  shape. Update docs. Retire this plan doc into the session log below.

## Session log

**2026-09-15** — Plan written. Starting Session 1.
