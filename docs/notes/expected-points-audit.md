# Expected-points audit — two modelling decisions (2026-09-16)

Read-only audit for concept 7 of `rationalization-2026-09-16.md`'s ledger
("Expected points... 5 formulas, graded by a 6th") and Wave 4 Step 1. No
code changed. Measured on today's real market (660 priced rows, run
`LFG_NOW=2026-09-16T1200Z`, `session()`'s cached `Scorer`).

## Question 1 — `Scored.score` vs `PlayerDerived.market_exp`

**Two sentences:** `Scorer.score()` (`score.py:1172`) and `build_profiles()`
(`profile.py:280`, via `status_adjusted()`) apply `status_multiplier()`
through different projections of the same product — one scales `score`
and `flat` directly, the other scales `pts` or `p_start` depending on the
status. On real data they land on the **same number anyway**, because
multiplication is commutative: this is a real duplication (two formulas
to maintain, one bug surface) but it is **not** a live numeric divergence.

### The algebra

`Scorer.score()`:

    flat  = ppm * pct_used/100
    score = flat * fix
    mult  = status_multiplier(status)
    score *= mult          # = ppm * pct_used/100 * fix * mult
    flat  *= mult

`build_profiles()` via `status_adjusted(ppm*fix, pct_used/100, status)`:

    mult = status_multiplier(status)
    if status in OUT_STATUSES:   pts, p_start*mult   # -> (ppm*fix) * (pct_used/100 * mult)
    else:                        pts*mult, p_start    # -> (ppm*fix*mult) * (pct_used/100)
    market_exp = pts_adj * p_start_adj

Both branches multiply the same four factors (`ppm`, `fix`, `pct_used/100`,
`mult`) together — `status_adjusted` just decides which *name* the `mult`
attaches to before the multiplication happens, and the product doesn't
care. `pct_used` here is `Scored.pct_used`, which is captured **before**
`Scorer.score()` applies `mult` — so both formulas start from the same
unadjusted `pct_used`. The clamps (`max(0.0, ...)`, `min(1.0, ...)`) in
`build_profiles()` are the only place they *could* diverge, and neither
fires on real data (`fix_factor` is always a positive ratio; `pct_used` is
a weighted average of two figures already in `[0, 100]`).

### Measured (660 scored market rows)

| status | n | max \|score − market_exp\| | mean \|diff\| |
|---|--:|--:|--:|
| (none) | 592 | 0.0 | 0.0 |
| doubt | 22 | 0.0 | 0.0 |
| injured | 43 | 0.0 | 0.0 |
| suspended | 2 | 0.0 | 0.0 |
| unavailable | 1 | 0.0 | 0.0 |
| **all** | **660** | **1.78e-15** (float rounding) | **1.29e-16** |

Zero rows differ by more than float noise. **Every ordering question the
task asked for is therefore moot on today's data**: the current best XI
does not change, the ranked BUY/RAID list does not change, because
`Scored.score` (which `squad_pool`/`pick_xi`/`bid.py`/the report actually
consume) and `PlayerDerived.market_exp` (which `Universe.market_exp` and
`par.py` consume) are the same number to 15 decimal places for all 660
players, including all 68 flagged `injured`/`doubt`/`suspended`.

### What *does* diverge, and why it doesn't matter today

`PlayerDerived.pts_now` (`= pts_adj`, the status-adjusted "points if he
plays") is genuinely different from anything `Scorer.score()` exposes: for
`OUT_STATUSES` it stays the player's **full, unmultiplied** rate (`status_
adjusted` only zeroes `p_start` for that branch), whereas `Scored.flat` is
zeroed. `to_bootstrap_input()` (`profile.py:139`) feeds `(pts_now,
start_p)` to `Bootstrap` as a pair, not as `pts_now * start_p` — so if
`Bootstrap` ever samples a Bernoulli(`start_p`) and scores `pts_now` on a
hit, an `OUT` player's simulated points-if-drawn-to-play stay at his
healthy rate. In expectation this still averages to zero (`start_p` is
0), so the **mean** matches `market_exp`/`score`; only the **variance**
of a near-zero-probability event could differ, and it's inert in practice
because `start_p = 0` means the branch is never drawn. Checked; not
pursued further — it is the ledger's "5th formula" (`schedule.py:129`) and
"6th" (`methodology.py:173`) that are out of this audit's scope.

### Recommendation (Q1) — confidence: high

Consolidate anyway, per Wave 4's plan, but **not because of a live bug**:
today's numbers agree, verified algebraically and against all 660 real
rows. The value of `ffcore/expect.py` here is maintenance, not
correctness — two independently-edited formulas that happen to agree today
are one refactor away from silently disagreeing (e.g. if `OUT_STATUSES`
grows a status that isn't symmetric under multiplication, or if a future
edit clips `pct_used` above 100). If Miguel wants to spend Wave 4 effort
elsewhere, this item can wait; it is not costing anything right now.

---

## Question 2 — `row_key(rec, ())` vs `norm(name)` inside `Scorer.score()`

**Two sentences:** `self.status`/`self.start_pct`/`self.listed` are keyed
by the market's own `row_key` convention (ff_id, falling back to name),
which is correct — `score()`'s `key` variable matches how `Scorer.__init__`
built those three dicts. But `self.current` (recent-minutes: `start_rate`/
`start_n`) is keyed by `norm(name)` **by design** (`_current_from_
perjornada()`'s own docstring: *"Keyed through the crosswalk to norm(market
name), the key Scorer.rate() uses"*) — so looking it up with `row_key`
instead of `norm(name)` doesn't fix a name-collision bug, it silently
disables the whole feature for almost the entire market.

### The two keys, measured (660 named market rows)

| | count |
|---|--:|
| rows where `row_key(rec, shared) != norm(name)` | 660 / 660 |
| rows with an `ff_id` at all | 660 / 660 |
| `len(sc.current)` (players with a recent-minutes reading) | 447 |
| `self.current` hit under `norm(name)` | 441 / 660 (67%) |
| `self.current` hit under `row_key` | **0 / 660** |
| rows where the hit/miss flips between the two keys | 441 / 660 |

Every priced row today carries an `ff_id` (per `tidy.md`'s own claim that
it's present on all 44,912 historical rows), so `row_key` returns the raw
numeric id (`"1059"`, `"17161"`, ...) for all 660 of them — never a
`norm(name)`-shaped string. `self.current`'s keys are exclusively
`norm(name)`-shaped, because that's what `_current_from_perjornada()`
builds. The two key spaces simply don't intersect. This is not the "two
men share a name" scenario the `score()` comment at line 1133-1135 was
guarding against — that's a real but much smaller problem:

| | count |
|---|--:|
| `norm(name)` values shared by 2+ distinct `ff_id`s in today's market | 3 (`iker munoz`, `pablo garcia`, `alvaro garcia`) |

So `norm(name)` mis-attributes recent-minutes history for 3 players out of
660 (0.45%) today. Using `row_key` instead "fixes" those 3 but breaks the
lookup outright for the other 438 who currently benefit from it.

### Which failure mode is it — skipped, or applied to the wrong player?

**Skipped, not misapplied.** Confirmed both by the key-space measurement
above (0 hits under `row_key`, meaning nothing could be silently
misattributed — there's simply no match) and by temporarily unifying the
lookup and rerunning the full pipeline (see below): under the unified key,
every player's `pct_used` collapses to a flat, round, editorial-calibration
number (50%, 70%, 30%, 80%, 95%, ...) instead of the varied, blended
figures the baseline shows (54%, 76%, 0%, 31%, 77%, 53%, 81%, ...) — the
signature of the recent-minutes blend never firing (`start_n` stuck at 0
for everyone) rather than firing on the wrong person.

### Downstream effect (measured — temporary edit, reverted after)

Per the task's instructions, `src/ffcore/score.py:1158` was changed from
`self.current.get(norm(rec.get("name", "")))` to `self.current.get(key)`
in this worktree only, the pipeline (`report xi methodology sim digest`,
pinned `LFG_NOW=2026-09-16T1200Z`) was run into a scratch directory, and
its output was diffed against `tools/baseline/` (recorded from unmodified
`HEAD`, not overwritten). **`git checkout -- src/` was then run and `git
status --short` confirmed clean** — no source change survives this audit.

`decisions.json` / `sim.md` diff, baseline vs. unified key:

| | baseline (today's shipped code) | unified to `row_key` |
|---|---|---|
| expected_finish | 2.63 | 2.62 |
| p_win | 25.5% | 26.0% |
| considered moves | 405 | 450 |
| band (10-90) | 689–2,488 | 681–2,543 |

**XI membership changes** — `Carl Starfelt` (baseline: fielded, no ladder
row because "XI — no change") is replaced by `Yeray Alvarez` (baseline:
`keep`/bench) under the unified key.

**BUY list changes** — `Diego Javier Llorente` newly clears the buy bar
(absent from baseline's BUY table, which reads "none clear the bar
today").

**RAID list reorders** — baseline top-3: `Unai Lopez`, `Marc Bernal`,
`Lorenzo Amatucci`. Unified top-3: `Alex Berenguer`, `Unai Lopez`, `Kike
Salas`. `Marc Bernal` and `Lorenzo Amatucci` drop off the raid list
entirely; `Alex Berenguer` and `Kike Salas` are new.

**`alerts.md` flips**, exactly as the task described:

    baseline:  "Overdrawn 21.17M — no safe dead-weight sale covers it;
                needs a manual look before the jornada locks."
    unified:   "Overdrawn 21.17M — sell Omar El Hilali (+€16.6M),
                Ali Houary (+€1.1M) clears most of it, still €3.5M
                short (no further safe dead-weight sale)."

Full ladder-group diff (name → baseline group → unified group):

| player | baseline | unified |
|---|---|---|
| Carl Starfelt | (in XI, no ladder row) | out |
| Yeray Alvarez | keep | in |
| Omar El Hilali | keep | sell |
| Ali Houary | keep | sell |
| Marc Bernal | raid | (drops off) |
| Lorenzo Amatucci | raid | (drops off) |
| Alex Berenguer | (not listed) | raid |
| Kike Salas | (not listed) | raid |
| Diego Javier Llorente | (not listed) | buy |
| Pablo Gavi | (not listed) | save |

### Recommendation (Q2) — confidence: high, but explicitly a finding to confirm, not a decision made

Keep `norm(name)` for the `self.current` lookup specifically — i.e. leave
`score.py:1158` as it is today. The evidence:

- `self.current`'s key space is `norm(name)`-only, by the loader's own
  documented design; `row_key` cannot address a single entry in it today
  (0/660).
- Unifying to `row_key` doesn't correct a misattribution, it **deletes a
  working feature** (real recent-minutes evidence blended into next-
  jornada start probability) for 441/660 players (67% of the market),
  visibly flattening every percentage to its pre-blend editorial value.
- The name-collision risk `row_key` would actually fix here is real but
  small: 3 players today (`iker munoz`, `pablo garcia`, `alvaro garcia`),
  0.45% of the market.
- The correct fix for the 3-player collision, if it's worth fixing, is not
  "swap the lookup key" — it's rekeying `_current_from_perjornada()`'s
  output dict to `row_key`'s convention (ff_id-first) so `self.current`,
  `self.status`, and `self.start_pct` all speak the same key, and then
  looking all three up by the same `key`. That is a different, larger
  change than the one-line swap a previous agent tried and reverted, and
  it is out of scope for this audit (it changes `_current_from_perjornada`,
  `_per_jornada_current`, and `Scorer.rate()`'s own `key = norm(name)` at
  `score.py:1072`, not just the one line at `score.py:1158`).

This confirms the previous agent's revert was correct, and explains *why*
in a way the raw diff alone didn't: the one-line unification does not
converge two views of the same fact, it removes one dictionary's only
usable key.

---

## Gaps

Neither question's downstream trace extends into `ffcore/par.py`'s or
`ffcore/bid.py`'s own consumption in isolation — both were exercised
indirectly through the full `report/xi/methodology/sim/digest` pipeline
run above (real code paths, not a synthetic call), which is the same
grading the repo's own `tools/regress.sh` uses, so the moves/ladder/alert
diffs above are the real, whole-pipeline effect, not an approximation of
it.

## Anything unanticipated

- Q1's premise — that `Scored.score` and `PlayerDerived.market_exp`
  numerically disagree — does not hold on real data. The duplication is
  real (two formulas, `status_adjusted()`'s own docstring says so) but the
  *values* it worried about turn out to be mathematically forced to agree
  by commutativity. Worth stating plainly since the ledger and Wave 4 plan
  both assumed a live divergence.
- `score()`'s `key` (`row_key(rec, ())`) passes an **empty** `shared` set
  to `row_key`, not the real `shared_names(market)` set `Scorer.__init__`
  computed for `self.lookup`. For a market row with no `ff_id` at all
  whose name collides with another player's, `score()`'s own `key` would
  never get the `"name@club"` disambiguated form that `self.status`/
  `self.start_pct` were actually indexed under — a second, narrower key
  mismatch inside the same function, on rows with no `ff_id`. No row in
  today's market lacks an `ff_id` (660/660 have one), so it does not fire
  today; flagging it because it's the same shape of bug as Question 2 and
  sits three lines above it.
