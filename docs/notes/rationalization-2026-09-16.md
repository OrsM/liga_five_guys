# Concept rationalization — swarm execution plan (2026-09-16)

Supersedes nothing: `rationalization-plan.md` (2026-09-15) hunted duplicated
FUNCTIONS and largely finished. This plan targets duplicated CONCEPTS — the
same idea re-derived in different words, which no clone detector sees.

## Evidence this is the right target

AST clone detection over all of `src/` (identifiers and constants erased,
plus sliding 3-5 statement windows) found exactly TWO exact function-clone
pairs, both trivial (`ingest.py:182/664`, `ingest.py:190/672`). Repeated
statement blocks were almost entirely inside `_selftest` bodies.

There is almost no copy-paste. Every item below is a concept with more than
one home.

## The concept ledger

| # | Concept | One home should be | Re-derived at |
|---|---|---|---|
| 1 | Player identity | `ffcore/crosswalk.py` | 9 entry points: `norm()` 221 calls, `resolve_api()` 34, `Market.key_for()` 25, `Crosswalk.resolve()` 26, `xw.player()` 27, `league.identify()` 18, `row_key()` 13, `_roster_key()` 13, `Market.key_of()` 6, `narrow_by_club()` 3 |
| 2 | The player record | `ffcore/profile.py` `PlayerProfile` | 5 shapes; 12 field names spelled 8x in `decide.py` alone; ~100 uses of flat `u.pos`/`u.price` vs 33 of `u.players` |
| 3 | Store schema | (nothing — no schema module) | `'name'` literal in 17 files, `'player_name'` 13, `'observed_at'` 12, `'ff_id'` 11; 116 `(r.get(X) or "").strip()`; 36 hand-written coercion try/excepts |
| 4 | The store | `ffcore/tidy.py` | no loader for the 4 most-read tables; `matches.csv` opened at 13 sites, `starters.csv` 7, `api_stats.csv` 2 |
| 5 | "What is current" | `latest_only`/`latest_snapshot` | decided per call site: of 13 `matches.csv` reads, 2 apply `latest_only`, 10 are silent, 1 documents the choice (`points.py:227`) |
| 6 | Time / jornada / lock | `tidy.JornadaClock` | built 11x across 5 files (`methodology.py` alone: 259, 306, 597, 1385, 1922, 1943); `match_id -> jornada` written 4x on 3 different keys |
| 7 | Expected points | (nothing) | 5 places express it — `score.py:1172`, `profile.py:280`, `profile.py:144`, `schedule.py:129`, `methodology.py:263`; graded by a 6th (`methodology.py:173`). **Corrected 2026-09-16:** the first two do NOT disagree — same four factors, multiplication is commutative; measured max difference 1.78e-15 across 660 rows. Duplicated expression, not divergence. `profile.py:144`'s projection difference IS real and load-bearing, for the pair Bootstrap consumes separately. See `expected-points-audit.md`. |
| 8 | Fitted parameters | (nothing) | `HOME_EDGE`/`DRIFT_FRAC`/`RATE_REL_FLOOR` as mutated module globals |

Amplifiers: 29% of `src/` (7,651 / 26,400 lines) is `_selftest` with no shared
fixture builder — `Universe` hand-built 25x, `LeagueState` 29x, `Bootstrap`
51x. And there is no rendering concept: markdown tables are hand-built with
`|` concatenation in 6 files.

## Measured facts the plan depends on (verified 2026-09-16, do not re-litigate)

- `matches.csv`: 75,240 rows, 380 real matches (198 copies each).
  `latest_only` keeps 380/380 — **loses nothing**. Safe.
- `starters.csv`: 180,372 rows, 2,512 real (match, player) keys.
  `latest_only` keeps 2,512/2,512 — **loses nothing**. Safe. (~72x waste today.)
- `api_stats.csv`: 11,190 rows, 10,857 real keys, latest snapshot holds 232.
  `latest_only` would **LOSE 10,625 keys (98%)**. FORBIDDEN. Needs
  latest-row-per-key instead.
- `perjornada_*.csv`: full history is required (points are diffed over time).
- The pipeline is **byte-identical across runs** when `LFG_NOW` is pinned.
  Verified: two full runs, `diff -rq` clean, 19.7s each.
- Self-test suite: 29 modules, ~20s, 4-way parallel.

## The two gates

Every task is graded by these and nothing else.

    # GATE A — self-tests (29 suites, ~20s)
    cd $REPO && PYTHONPATH=src FF_ROOT=./data \
      bash tools/selftests.sh

    # GATE B — byte-identical pipeline output (~20s)
    cd $REPO && bash tools/regress.sh --check

GATE B pins `LFG_NOW`, redirects `LFG_REPORTS`/`LFG_PARTS`/`LFG_ALERTS`/
`LFG_WARNINGS` to a temp dir, diffs against `tools/baseline/`, and restores
`data/decisions/` afterward (four log CSVs are appended to by every run).

**Unless a task is explicitly marked BEHAVIOUR-CHANGING, GATE B must be
byte-identical. A diff is a failure, not a judgement call.**

## Rules for every agent

1. **You own the files listed in your task and no others.** Do not edit a
   file another task owns, even to fix something obviously wrong in it —
   report it instead.
2. **No design decisions.** If the task does not say what to do, stop and
   report. Do not guess, do not "improve while you're in there".
3. **Both gates must pass before you report done.** Paste the actual output.
4. **Do not touch** `data/`, `reports/`, `.runtime/`, `~/.local/bin/lfg-run`,
   or any `docs/notes/*.md` except the one your task names.
5. **Preserve every `Why:` comment and docstring rationale.** If you move
   code, the comment moves with it. This repo's comments are load-bearing.
6. Report: files changed, both gate outputs, and anything you noticed but
   did not touch.

## Waves

Parallelism is limited by FILE OWNERSHIP, not by concept. Two tasks may run
concurrently only if their owned-file sets are disjoint.

    WAVE 0  [1 agent,  sequential]  build the gates
       |
    WAVE 1  [3 agents, PARALLEL]    pure additions, no existing file touched
       |
    WAVE 2  [6 agents, PARALLEL]    migrate call sites, partitioned by file
       |
    WAVE 3  [1 + 4 + 1, mixed]      collapse the player record
       |
    WAVE 4  [1 agent,  sequential]  BEHAVIOUR-CHANGING — needs sign-off
       |
    WAVE 5  [2 agents, PARALLEL]    renderers + fitted params

---

### WAVE 0 — the gates (1 agent, opus or sonnet, blocks everything)

**Owns:** `tools/` (new dir), `.gitignore`
**Task:** create `tools/selftests.sh` (port the 29-suite list from
`~/.local/bin/lfg-run`, read-only — copy it, do not edit lfg-run) and
`tools/regress.sh` with `--record` / `--check`. Record the baseline from
current HEAD into `tools/baseline/`.
**Acceptance:** `--record` then `--check` passes twice in a row;
`git status --short` clean after each (data/decisions restored).

---

### WAVE 1 — pure additions (3 agents, PARALLEL, sonnet)

No existing file is touched by any of these. Zero conflict by construction.
GATE B is trivially byte-identical (nothing calls the new code yet).

**1A — `src/ffcore/schema.py`** (new file, owns only itself)
Typed field readers replacing the 116 `(r.get(X) or "").strip()` idioms:
`text(row, col)`, `num(row, col, default=None)`, `whole(row, col,
default=None)` (int), each returning the default rather than raising.
Plus per-table column-name constants for `matches`, `starters`, `market`,
`lineups`, `api_stats`, `perjornada`. Own `_selftest` covering empty
string, missing column, `None`, and unparseable values.
*Do not migrate any caller.*

**1B — `src/ffcore/fixtures.py`** (new file, owns only itself)
Shared test fixtures: `tiny_universe(**overrides)`, `tiny_profile(...)`,
`tiny_state(...)`, `tiny_bootstrap(...)`. Read the existing hand-built
fixtures in `decide.py`, `sim.py`, `slate.py`, `ffcore/par.py`,
`ffcore/candidates.py`, `ffcore/pricing.py`, `ffcore/season.py` to learn
the shapes — READ ONLY, change none of them. Defaults must produce a
Universe that `_fieldable()` accepts. Own `_selftest`.
*Do not migrate any caller.*

**1C — `src/ffcore/tidy.py`** (owns this one file exclusively)
Add, without migrating any caller:
- `load_matches()` -> `latest_only`. 380 rows. Safe, verified.
- `load_matches_history()` -> full history (for `points.match_jornadas`).
- `load_starters()` -> `latest_only`. 2,512 keys. Safe, verified.
- `load_perjornada()` -> globs `season/live/perjornada_*.csv`, newest file,
  FULL rows (points are diffed over time).
- `load_api_stats()` -> latest row per `(player_id, week, stat)` across ALL
  snapshots. **`latest_only` here is FORBIDDEN — it loses 98% of keys.**
  Add the dedup primitive next to `latest_only` and document why this table
  differs.
- `clock()` -> a process-memoized `JornadaClock`, built once from
  `load_matches()` + `load_fixtures()`.
- `jornada_of_match()` -> `{match_id: jornada}`, process-memoized,
  first-write-wins (matches `score.py:538`'s existing semantics).
Extend `tidy.py`'s `_selftest`. Add a `Why:` section to `docs/notes/tidy.md`.

---

### WAVE 2 — migrate call sites (6 agents, PARALLEL, sonnet)

Each agent replaces, **in its own files only**: direct CSV opens with the
Wave-1C loaders, hand-built `JornadaClock`/`jornada_of` maps with
`tidy.clock()`/`tidy.jornada_of_match()`, and `(r.get(X) or "").strip()`
idioms with `ffcore.schema` readers.

| task | owns | notable |
|---|---|---|
| 2A | `methodology.py` | 6 `JornadaClock` builds, 5 `matches.csv` reads |
| 2B | `ffcore/score.py` | 4 `matches`, 4 `starters`, 1 `api_stats`; also fix the two-keys-for-one-player split at `score.py:1133` vs `:1149` **only if** GATE B stays identical — if it diffs, REPORT, do not keep |
| 2C | `backtest.py`, `gap_signal.py`, `points.py`, `scout.py` | `points.py:227` must keep FULL history — use `load_matches_history()` |
| 2D | `crosswalk.py`, `ffcore/league.py` | 9 table reads in `crosswalk.main()` |
| 2E | `decide.py` | the 3 raw reads in `load()`; also collapse the two `for r in teams:` loops (`decide.py:634` and `:665`) into one pass |
| 2F | `sources.py` | schema readers only (20 idioms); do NOT touch parse/sign logic |

**All six are byte-identical-or-fail.** 2B's optional item is the one place
an agent may find a real diff — it reports rather than decides.

---

### WAVE 3 — collapse the player record (mixed)

**3A [1 agent, sequential]** — `decide.py`. Add read accessors backed by
`u.players` (e.g. `u.p(k)` returning the `PlayerProfile`, plus
`u.price_of(k)` style helpers matching current `.get()` defaults exactly).
**Keep the flat dicts in place.** Byte-identical.

**3B-3E [4 agents, PARALLEL]** — migrate `u.pos`/`u.price`/... reads to the
accessors, partitioned by file:
- 3B: `sim.py`
- 3C: `slate.py`, `ffcore/par.py`, `ffcore/pricing.py`
- 3D: `ffcore/candidates.py`, `ffcore/bid.py`
- 3E: `report.py`, `backtest.py`, `squads.py`
Byte-identical each.

**3F [1 agent, sequential]** — `decide.py`: delete the `InitVar` block, the
12 `__post_init__` projections, and `_synthetic_profiles()`. Migrate the 25
hand-built `Universe(...)` fixtures to Wave-1B's `tiny_universe()`.
Byte-identical.

---

### WAVE 4 — expected points (1 agent, SEQUENTIAL, BEHAVIOUR-CHANGING)

**Requires sign-off before any code changes.**

`Scored.score` (`score.py:1172`) and `PlayerDerived.market_exp`
(`profile.py:280`) both mean "expected points next jornada" and **disagree
whenever a player is injured, suspended or doubtful** — they apply
`status_multiplier` through different projections. `status_adjusted()`'s own
docstring concedes this.

**Step 1 (agent, read-only):** produce a report — for every player in today's
market, both numbers side by side, and which decisions change if each is
used. Write to `docs/notes/expected-points-audit.md`. **Change no code.**

**Step 2 (human decision, Miguel):** pick which projection is correct.

**Step 3 (agent):** create `ffcore/expect.py` as the single formula; make
`score.py`, `profile.py`, `schedule.py` and `methodology.py` call it, so the
grader grades what the decision path produces. GATE B will diff — the diff
must match what Step 1 predicted, and is reviewed line by line.

---

### WAVE 5 — renderers and fitted params (2 agents, PARALLEL)

**5A — `sim.py`** — BEHAVIOUR-CHANGING (one known, intended fix).
`ladder_rows()` and `payload()` each independently apply the same three
rules (par floor, one-raid-per-victim, sort key) **in different orders**:
`ladder_rows:401-405` floors then dedups; `payload:1067-1078` dedups then
floors. For a victim whose best raid fails the par floor, the markdown shows
his second-best raid and the JSON shows none. Extract one
`moves(u, rows)` both renderers consume. GATE B will diff exactly where the
two previously disagreed; that diff is the fix. Nothing else may change.

**5B — `decide.py`, `ffcore/forecast.py`, `ffcore/season.py`** — a `Fit`
dataclass (`value`, `why`) replacing the three mutated module globals and
the hand-written save/restore in `season.py:571-580, 606-612`. Also fix the
dead comment at `decide.py:805` (it cites `rate_draw()`/`start_draw()`,
deleted 2026-09-16). Byte-identical.

---

## Deliberately NOT in scope

- `ffcore/forecast.py`'s math, `ffcore/fixture.py`'s Elo/Match work, and
  `sources.py`'s per-site exclusion rules. Intricate and load-bearing; the
  2026-09-15 plan was right to refuse to collapse them.
- Concept 1 (identity, 9 entry points). Real, but every one of the nine has
  a genuinely different input shape. Collapsing it needs a design pass, not
  a migration. Revisit after Wave 3.
- A shared markdown table builder. Real duplication, lowest payoff, and it
  touches every renderer at once. Revisit after Wave 5.

## Stopping points

Waves 0-2 alone remove the `matches`/`starters` rescans, give every table one
loader with a settled currency rule, and retire 116 hand-written field reads
— at zero behaviour change. That is a complete, shippable outcome if the
rest is never done.
