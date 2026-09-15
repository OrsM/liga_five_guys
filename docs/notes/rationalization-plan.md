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
- [x] **4. Decision engine.** THE ONE THAT DIRECTLY SERVES THE SUCCESS
  CRITERION. **Funding-noise bug fixed and verified (f70ad6c)** —
  `rank()` no longer treats selling a currently-fielded starter as
  interchangeable with selling bench dead weight; confirmed across 5
  seeds that the recommended funding source now varies only among real
  bench players.

  **`decide.py` split five ways**, each verified (self-tests + full
  pipeline) before committing: `ffcore/schedule.py` (per-player jornada
  scheduling, e927f79), `ffcore/pricing.py` (locked/burn/cash_price/
  respond, e57ce85), `ffcore/action.py` (the Action dataclass — a
  dependency-free leaf, needed first to avoid a circular import), `ffcore/
  candidates.py` (candidates/dead_weight/overdraft_fix/apply/
  offer_combos — reaches back into decide.py's remaining core via a
  lazy `from decide import ...` inside the function body, the same
  precedented pattern methodology.py's `_fc()` already used), and
  `ffcore/par.py` (value_rate/player_forecasts, no lazy import needed —
  pure w.r.t. a Universe instance). **decide.py: 2,206 → 1,303 lines
  (41% smaller).**

  Deliberately NOT split further: `Universe`, `current_xi`/`xi_bar`/
  `route_kind`/`_fieldable`, `load()`, `rank()` stay — this is decide.py's
  real core (the data, the queries over it, the ranking engine), not
  leftover bloat. Fragmenting it further would relocate complexity
  without separating a genuine concern.

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
- [~] **5. Buy/sell decision tree.** The three real funding paths (cash
  outright / one spare sale / genuinely unreachable) already existed —
  `candidates()` generates the first two, `sim.ladder_rows()`'s SAVE/PASS
  split reports the third. Found and fixed a real disagreement between
  them (3504e1c): the "unreachable" threshold summed every dead-weight
  player's proceeds, but `candidates()` only ever funds with ONE spare —
  a target reachable only by summing bench sales was silently dropped
  from every group (not shown as buyable, not shown as short). Fixed
  with one shared `ffcore.candidates.max_spare_proceeds()`/
  `fieldable_spares()`, proven with a synthetic case before fixing.
  Still open: nothing currently makes this tree's THREE branches
  visible/explicit as a named concept a reader could point to — the
  correctness is real now, but "decision tree" is still implicit across
  two files. Lower priority than the correctness fix already landed.
- [ ] **6. Rival-raid prioritization.** One best raid per opponent, by
  harm-to-them + economic sense — a scoped new feature on the now-clearer
  decision engine.
- [ ] **7. Close-out.** Re-run the duplication audit against the new
  shape. Update docs. Retire this plan doc into the session log below.

## Session log

**2026-09-15** — Plan written. Session 1 closed (3cee3ef). Sessions 2-3
verified clean, no changes needed. Session 4 fully closed: the funding-
noise bug fixed and verified against the real Brugué/Vicente case
(f70ad6c), and `decide.py` split five ways (schedule, pricing, action,
candidates, par — 2,206 → 1,303 lines, 41% smaller), each cut verified
with self-tests and the full pipeline before committing. Resolved the
real circular-import risk flagged at the end of the prior round using a
precedented lazy-import pattern (methodology.py's `_fc()` already did
this for decide.load()) rather than avoiding the split. `explain` tool
stays parked (see above).

Session 4 status: DONE. `decide.py`'s remaining content (Universe,
current_xi/xi_bar/route_kind/_fieldable, load(), rank()) is its real
core, deliberately not split further.

Session 5: found and fixed a real disagreement between candidates()'s
funding logic and sim.ladder_rows()'s reachability check — a genuinely
good target reachable only by summing multiple bench sales was silently
invisible in the report (3504e1c). Also fixed a dangling doc reference
from Session 4's own funding-noise fix (never actually got its promised
docs/notes/decide.md section until now).

Next: Session 6 (rival-raid prioritization) is the natural next piece —
Session 5's remaining item (making the 3-branch funding tree an
explicit, named concept rather than implicit-but-now-correct logic) is
real but lower priority than a live correctness bug, and reasonable to
revisit later or skip.
