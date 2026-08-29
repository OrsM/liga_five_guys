# liga_five_guys — handoff, 2026-08-29

One repo touched today: `liga_five_guys` (the model/pipeline). The phone
renderer (`~/claude_projects/website`, `src/pages/Fantasy.jsx`) was not
touched. Working tree clean (test-run artifacts in `data/decisions/`,
`reports/METHOD.md` and `reports/decisions.json` are expected to show as
modified after any local suite check — restore them the same way as ever,
see "Standing method"). 103/103 `decide.py` self-test cases, 143/143
`sim.py` — pushed at `24d2a8b`.

## Start here

    cd ~/claude_projects/liga_five_guys && \
      python src/decide.py --selftest && python src/sim.py --selftest

Real numbers as of the last live run (`reports/decisions.json`, committed
at `3f0c22d`, 2026-08-29T1400Z): cash **-2.64M** (balance 3.30M − 5.94M
locked in 3 pending bids, all expiring tonight 22:24), squad value
**256.42M**, expected finish **2.26**, `p_win` **0.30**. Verdict that run:
**wait for the clauses** — 21 players' release clauses open today, worth
+194 season points against +189 for the best buy available right now.

## What today was, compressed

Two threads, both starting from Miguel pushing back on what the report
was actually doing, not from a self-directed audit.

### Thread 1: multi-sale funding (shipped 2026-08-28) is real but idle

Miguel: *"I feel like this report is not going in the direction we
discussed... we were looking at options where I say what if I sold these
players to buy this other one etc. and I don't feel that this is being
captured."* Checked rather than defended: `decide.candidates()`'s
multi-sale funding (`b74accb`, yesterday) is live and correct — re-ran it
against real data, 109 candidates, 0 needing more than one sale. Traced
why: today's most expensive realistic target is ~€11.4M, and selling Omar
El Hilali alone covers it. The feature works; today's market just doesn't
need it. Not a bug, but genuinely surfaced by asking rather than assuming
green tests meant the feature was doing anything on a given day.

### Thread 2: ranking never optimized for value-for-money

Miguel, pressed further: *"we make no effort to optimize value per
euro."* Checked and found it half-true: `sim._best()` already re-picks the
single "Do this" headline by value-for-money (`VALUE_TOLERANCE`), but (a)
`decide.rank()`'s screening (`KEEP=12` + yesterday's `KEEP_RELIABLE_MIN`)
is keyed purely on raw gain, so an efficient-but-modest candidate could be
screened out before the expensive pass ever scores it, and (b)
`sim.payload()`'s `moves` table — what the phone actually shows — sorted
by raw gain only, with zero use of the already-computed `value` field.
Researched best practice before building (fractional-knapsack: pure
ratio-greedy alone is a known trap; fantasy-sports value-based drafting:
screen on value-over-replacement, THEN rank survivors by efficiency) —
Miguel confirmed "bar on gain, then rank by value," explicitly not pure
ratio and explicitly not the status quo.

**Shipped** (`24d2a8b`, test-driven — every new fixture confirmed to fail
before its fix landed):
  * `decide._top_up()` — one shared "ensure N candidates satisfying X reach
    the final pass, on top, never displacing" helper, replacing
    `KEEP_RELIABLE_MIN`'s bespoke inline block (refactored, verified
    behavior-preserving against its own pre-existing test) and used again
    for the new `KEEP_VALUE_MIN`.
  * `KEEP_VALUE_MIN = 4` — tops up the screening survivors with the
    best-by-efficiency candidates (genuine gain, genuine spend), computed
    as a precomputed top-N-by-ratio SET, not a loose predicate — a first
    draft used `d > 0 and net > 0` as `ok`, which is satisfied by nearly
    every ordinary buy candidate and made the top-up a silent no-op.
  * `sim.payload()`'s `moves` sort — bar at `MOVES_VALUE_FLOOR` (25% of
    this run's best `d_pos`), rank above it by `value` (points/€M),
    preserve the win-probability tier unchanged. Also exposed `value` in
    the JSON itself — computed by `rank()` for every row since 2026-08-21
    but never reaching the phone report before now.
  * `sim._best()`: `pool[0]` → `max(pool, key=d_pos)` — it implicitly
    assumed `rows` arrived sorted by raw gain, true only by accident.

**A false unification caught before shipping:** planned to merge
`value_rate()`'s "genuine spend" guard with the new top-up's "genuine gain
AND spend" check into one shared predicate. Would have broken an existing,
correct test (`value_rate(0.0, 5e6) == 0.0` — `value_rate`'s only real
guard is `cost <= 0`, gain sign is irrelevant to it). Dropped the forced
merge rather than "fix" a passing test to match a wrong assumption.

**A real test flakiness, found by verifying against the actual production
runtime, not just the dev shell:** the `KEEP_VALUE_MIN` fixture passed
every time under plain `python3` (no `numpy` installed on this box —
falls back to a different RNG path) and failed under `uv run --frozen
python` (this repo's real runtime, `numpy` 2.5.2 present) — same seed,
different backend, a genuinely borderline gain margin landing on
different sides of zero. Fixed by widening the margin, not by chasing one
backend's numbers. **Lesson: `python3 script.py --selftest` alone is not
sufficient verification for a change with any Monte Carlo sensitivity —
run it through the actual `lfg-run`/`uv` path too before trusting green.**

**Verified against real cached data** (`LFG_NO_FETCH=1 LFG_NO_COMMIT=1
lfg-run`): headline, `expected_finish`, `p_win` all byte-identical to
before the change. Every real candidate today is self-funding (net ≤ 0,
selling Omar El Hilali raises more than any real target costs), so
`value` is `None` throughout and there was nothing for the new ranking to
differentiate — same "the mechanism is real, today's data doesn't
exercise it" shape as Thread 1.

## Priorities for next session

### 1. The interactive what-if picker

The actual next ask, raised directly by Miguel after Thread 1/2: *"what
would it look like"* to name specific players to sell and ask what that
unlocks, rather than only ever seeing combos the system already decided
to try automatically. Explicitly out of scope for today's change (kept it
scoped to the ranking fix). Not yet designed in detail — a real planning
session (Explore + Plan agents, like today's) should happen before writing
code, but the shape discussed:

  * `decide.candidates(u, expected, budget=...)` already accepts an
    explicit budget override — the hook this needs already exists. A
    what-if query is: apply the named sale(s) to a COPY of the squad
    (same shape as `apply()` does for a real `Action`), recompute
    `cash + proceeds` as the budget, recompute `current_xi()`/`bar` off
    the resulting squad (selling a starter changes who's in the XI, not
    just the cash), then run `candidates()`/`rank()` fresh against that
    hypothetical `Universe`.
  * **Form factor is the open question, not the logic.** Miguel's entire
    workflow is on his phone, checking the same static, precomputed
    `decisions.json` twice a day — a CLI flag (`decide.py --what-if
    "sell X, sell Y"`) is cheap to build (new entry point over existing
    machinery, no new infra) but only usable at a terminal. A phone-side
    picker matches where he'd actually use it, but the phone app currently
    renders a STATIC precomputed JSON — no live backend to query on
    demand — so that path needs actual new infrastructure (an endpoint
    that runs `candidates()`/`rank()` on request), not just a new
    rendering of existing data. Ask Miguel which matters more before
    designing further: something usable today from wherever he is, or
    getting the core query logic right first and deciding where to expose
    it after.
  * Whatever ships, it needs the SAME rigor as today's change:
    test-driven, verified against real cached data via the actual `uv`
    runtime (not just `python3`), and any Monte Carlo-sensitive fixture
    given a wide enough margin to not flake across backends.

### 2. Everything still open from the 2026-08-25 handoff

Fixture-difficulty coverage on the schedule join, `best_swap_for()`'s
funding-chain reach (partially addressed by `b74accb`'s widened funding —
worth re-checking whether the gap that priority named is now closed or
just narrowed), the orphaned warnings computation, and everything still
open from 2026-08-21 (xG/xA formula design, K/shrinkage revalidation,
frozen-roster assumption, game-script correlation) — none touched this
session, see git history for detail if picking one up.

## Standing method (carried forward + today's additions)

  * **A number Miguel pushes back on is worth checking, not defending** —
    both of today's threads started this way, same as every fix in the
    2026-08-25 handoff.
  * **Verify a Monte Carlo-sensitive change against the REAL runtime, not
    just whichever Python happens to be on PATH.** This box's plain
    `python3` lacks `numpy` and silently takes a different RNG fallback
    than `uv run --frozen python` (the actual production path, `numpy`
    2.5.2). A fixture that passes under one and not the other is not
    "flaky" — it's under-margined; widen it rather than chase a specific
    backend's numbers, and confirm stability under BOTH before trusting a
    fixture, not just the one that happened to be handy while writing it.
  * **A "shared guard" is only real if the tests agree it's the same
    guard.** Before merging two conditions into one shared predicate,
    check what each one is ACTUALLY tested to do — `value_rate()`'s
    guard turned out to be spend-only (`value_rate(0.0, 5e6) == 0.0` is a
    real, intentional, tested case), not gain-and-spend, and forcing them
    together would have silently broken a correct test to satisfy an
    assumption made before reading the test.
  * **A predicate that's "true for almost everything" makes a top-up a
    no-op, even with correct code.** `KEEP_VALUE_MIN`'s first draft
    (`d > 0 and net > 0`) was logically defensible but practically always
    satisfied by the natural top-KEEP already, so `have >= minimum` from
    the start, every time — caught by writing the integration test before
    trusting the mechanism, not by the unit test of `_top_up()` alone
    (which only tests the bookkeeping, not whether a given `ok` is a
    useful bookkeeping input).
  * Everything from 2026-08-25's list still holds: check the mechanism
    before trusting a big diff's direction; a broadened "already answered"
    check needs the same scrutiny as a narrowed one; one points scale does
    not imply one comparable pool; no manual polling wait-loops on top of
    a harness that already notifies; `git push` by default once verified;
    `git stash push -u -m "..." -- data/decisions reports/METHOD.md
    reports/decisions.json` (stash, work, pop) or `git checkout --` those
    same paths (used today, simpler when nothing else needs the working
    copy in between) to keep pipeline-run noise out of a commit.

## Also open, untouched, lower priority

Carried forward unverified — not re-checked this session:

  * `api_key()`'s remaining migration onto `Crosswalk.resolve()`.
  * `Crosswalk.merge()`'s O(n²) identifier scan (was 73ms at n=654 as of
    2026-08-21).
  * `FIX_BAND`/`HOME_EDGE`/`MIN_POOL` — still guesses/blocked on match
    volume as of 2026-08-21.
  * Pedro Diaz / Tete Morente ledger gap — small, self-resolving.
