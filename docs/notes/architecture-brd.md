# Architecture BRD — treating today's code as the MVP

Written 2026-09-17, after two refactoring attempts that both missed.

## Why this document exists rather than another refactoring plan

Two attempts have now been made. The 2026-09-15 one shrank src/ by 1,021
lines; the 2026-09-16 one GREW it by 1,696. Neither made the codebase
easier to change, which was the actual goal both times.

They failed the same way: both started from "what looks duplicated?" and
worked bottom-up. Bottom-up cannot tell a wrapper from a tool, tidying
from progress, or a split from an improvement. The 2026-09-15 plan split
decide.py five ways and reported "2,206 -> 1,303 lines, 41% smaller" as
the result. The split was measured in lines and the lines went down, but
the five new modules could only work by importing decide.py back at
runtime, so nothing became independently understandable.

So: state the properties the architecture must have, measure the gap,
and close it one stage at a time. The MVP is the current code. It works,
it is validated against real outcomes, and most of it is good.

## What the MVP already gets right — do not regress these

- **The five-stage shape is correct** (scrape -> identity -> forecast ->
  decide -> report). This document does not propose a new shape. It
  proposes enforcing the one that already exists.
- **Functions are small.** Median production function is 9 lines, p90 is
  34. This is not a codebase of thousand-line monsters.
- **The comments are the asset.** They cite real incidents: the
  double-counting bug in `_per_jornada_current`, the stale-deadline
  near-miss, why `latest_only` is forbidden on api_stats. That knowledge
  is not recoverable from the code and is worth more than the code.
- **The forecasting experiment harness** (`backtest_predictor`,
  `walk_forward_compare`, `golden_dataset`, `log_experiment`) is
  deliberate tooling, built on request, and has no callers BETWEEN
  experiments. It was nearly deleted as dead code on 2026-09-17. It is
  not dead.
- **The self-tests are test DATA, not boilerplate.** An attempt to
  collapse 1,537 lines of forecast self-test found nothing safely
  removable: `Bootstrap(per)` is already shorter than any fixture
  builder call, and the odd shapes ARE the tests.

## The measured problem

Not size. Not duplication. **Dependency direction and discoverability.**

| symptom | measured 2026-09-17 |
|---|---|
| upward imports (a lower layer importing a higher one) | **24**, of which **17 are lazy** |
| lazy imports across the decision layer | **56** |
| `ffcore/*` modules importing `decide.py` at runtime | **3** (candidates, par, fixtures) |
| operations with two live entry points | **6** (xi_bar, route_kind, dead_weight, candidates, player_forecasts, rank) |
| bare names meaning different things in different modules | **19** |
| distinct functions whose name contains `load` | **35** |

A lazy import is invisible to anything reading a file's header, so a
module's real dependencies cannot be seen without reading every function
body. That is the concrete reason an agent — or a person — cannot tell
what is going on. `ffcore/candidates.py` imports `decide` five times,
every one inside a function.

## Requirements

**R1 — Dependency direction is one-way and enforced.**
Every module declares a layer. A module may import from its own layer or
below, never above. Today: 24 violations. Target: 0, with a gate that
fails the build.

**R2 — A module's dependencies are visible at the top of its file.**
Lazy imports exist here to break cycles that R1 removes. Once R1 holds,
a lazy import should be rare enough to need a comment explaining itself.
Today: 56 in the decision layer. Target: only where genuinely justified.

**R3 — One operation, one entry point.**
Not one name — one way to invoke it. `u.route_kind(k)` and
`route_kind(u, k)` must not both exist. Today: 6 operations with two.
Target: 0.

**R4 — A per-player fact is declared in exactly one place.**
Achieved for the Universe flat dicts (2026-09-16). Must not regress.

**R5 — Adding one forecasting variable touches one or two files.**
This is the requirement the whole exercise exists for and the only one
stated as an outcome rather than a count. It should be measured by doing
it, not by inspection: pick a real candidate signal from api_stats.csv's
untried stats, wire it, and count the files touched.

**R6 — Every module runs its own self-test standalone.**
Already true. Must not regress.

## Non-requirements — explicitly rejected

- **A line-count target.** Tried on 2026-09-16 as the primary metric; it
  is gameable and it measured the wrong thing. The size ratchet in
  tools/concepts.sh stays as a guard against unnoticed growth, not as a
  goal.
- **Fewer functions.** Rejected in the 2026-09-15 plan for the right
  reason: it is satisfied by cramming logic into fewer, more tangled
  functions.
- **A rewrite.** The MVP is validated against real outcomes. Nothing
  here proposes discarding it.
- **Renaming to resolve collisions.** Considered 2026-09-17 and
  rejected: where two names cover one implementation, renaming hides the
  duplication instead of removing it.

## Stages

Each stage is independently valuable, independently revertible, and
gated. A stage that cannot meet its gate is REVERTED, not carried. That
discipline is what both previous attempts lacked.

Every stage keeps the three existing gates green: `tools/selftests.sh`,
`tools/regress.sh --check` (byte-identical), `tools/concepts.sh`.

- **Stage 0 — declare the layers.** A table of module -> layer, and a
  gate that counts upward imports. No code changes. Establishes the
  baseline (24) and makes every later stage measurable.
  *Done when:* the gate exists, reports 24, and fails if that rises.

- **Stage 1 — the store stops reaching up.** `ffcore/tidy.py` (L1)
  currently imports `ffcore/fixture.py` (L3) and `ffcore/crosswalk.py`
  (L2). The store is the foundation; nothing it does should need
  forecasting.
  *Done when:* tidy.py has zero upward imports.

- **Stage 2 — forecasting stops importing reporting.** `ffcore/score.py`
  and `ffcore/season.py` (L3) import `stats.py` (L5) six times between
  them. Almost certainly a small statistics helper in the wrong place.
  *Done when:* no L3 module imports an L5 one.

- **Stage 3 — break the decide/ffcore cycle.** THE BIG ONE, and it needs
  a human decision first, because there are three defensible answers:
  (a) `Universe` moves down into `ffcore/`, (b) the Wave-4 modules move
  back into `decide.py`, (c) `candidates`/`par` take the fields they
  need instead of a `Universe`. Do not start until one is chosen.
  *Done when:* no `ffcore/*` module imports `decide`.

- **Stage 4 — collapse the six dual entry points.** Only after Stage 3:
  most of them exist BECAUSE of the cycle, and some may disappear with
  it.
  *Done when:* R3 holds, with a ratchet.

- **Stage 5 — prove R5.** Wire one real candidate signal end to end and
  count the files touched. If the answer is still six, Stages 1-4 did
  not achieve the goal and this document is wrong.

## How to know it worked

Not by the line count. By Stage 5: pick a signal, wire it, count files.
That number is the whole point, and it is the only measurement here that
cannot be gamed by moving code around.
