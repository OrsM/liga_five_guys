# liga_five_guys — handoff, 2026-08-21 (late night)

Private repo `OrsM/liga_five_guys`, working tree clean, **pushed** at `7ceb67b`,
30 suites pass.

## Start here, and do not open with an audit

**This file is the map.** Read it, run the one command below, then start on
item 1 of "What to do next." Everything up to here is committed, deployed and
verified.

    cd ~/claude_projects/liga_five_guys && \
      LFG_NO_FETCH=1 LFG_NO_COMMIT=1 ~/.local/bin/lfg-run 2>&1 | tail -4

If that says `30 suites pass` and publishes 2 files, nothing has rotted.

**Outside the repo:** no `~/.local/bin/lfg-run` edits were needed this
session — `sources.py`, `points.py` and `ingest.py` were already in its
`TESTS=(...)` list before today.

## What last session was, compressed

Two threads, run back to back on direct instruction: finish unifying player
identity (last session's priority #1), then a long, mostly-research pass on
forecasting quality that kept getting redirected by pointed pushback — twice
told "you're assuming the current approach is right without evidence," and
both times that was fair.

### Thread 1: player identity, actually finished this time

1. **`Crosswalk.resolve()` built** — the one join six separate resolvers
   (`Crosswalk.player`, `Market.key_for`/`candidates`, `text.resolve`,
   `api_key`, `identify`, `_roster_key`) had each been re-deriving their own
   way. `_roster_key()` and `slate.py`'s `slate_from_api()` now call it
   directly.
2. **Ids promoted above ALL fuzzy name/team/price logic, everywhere** — a
   real design reversal, confirmed directly with Miguel: an id involves no
   derivation (it's a raw fact off a row); only the id→key MAPPING is
   learned, and `Crosswalk.merge()`'s stale-id displacement is the safety
   net for that now, not per-call name priority. `api_key()`'s "an exact
   name is never overridden by an id" test was deliberately flipped to
   assert the opposite. `slate.py` was found joining `api_market.csv` on
   name alone despite every row carrying the app's own `player_id` — fixed.
3. **Real duplication found and fixed on request** ("there must be
   duplication with six functions for this"): `VALUE_TOLERANCE` was defined
   twice (tidy.py and league.py, same value, unlinked); the price-tolerance
   comparison itself was hand-rolled twice with subtly different formulas,
   unified into `ffcore.tidy.price_agrees()`; `League.__init__` was
   re-reading and re-parsing `players.csv` a SECOND time
   (`app_ids_known()`) despite `self.xw` already holding the same data in
   memory — factored into `_app_ids_of(xw)`.
4. **`api_key()` NOT fully migrated onto `resolve()`** — noted as an
   explicit TODO in its own docstring. It still hand-rolls id-then-name
   because it needs to apply price-validation differently per step
   (unconditional on the id, validated on the two name guesses) and
   `resolve()`'s single return value doesn't say which step answered.
   Left alone rather than forced.

Verified throughout: `League.owner` byte-identical against real data before/
after every change in this thread.

### Thread 2: forecasting — real findings, most of it NOT code changes

Miguel's core objection, stated directly and worth re-reading in his own
words if this comes up again: *"you keep assuming the current average
forecasts are correct, I don't see much evidence for that... I want to
apply some sort of bayesian approach where stale information gets less and
less relevance, widening the error bars, and the averages for this year
also drift... how will we detect errors and correct over time?"*

**What was already there, corrected on the record after undersold in an
earlier answer:** two-stage empirical Bayes shrinkage (`Scorer.rate()`,
K=8), sample-size-aware uncertainty widening (`Bootstrap.rate_rel`,
`~1/sqrt(matches+K)`), club-correlated season variance (last session's Step
3), and a forecast-vs-actual diagnostic (`methodology.py`) that measures
the gap but does not act on it — detection without correction.

**Step 4 shipped:** `Calibration.fit()` (P(start)) now grades against real
per-match minutes (`ffcore.tidy.minutes_played()`) instead of binary
played/didn't. Verified on real data: beta 2.2x→1.8x, AF blend 60%→40%,
titular worth 90%→84%, all moving the direction expected once partial
involvement stops scoring as full credit. **Caught a real bug**: the fit's
on-disk cache (`startcal.json`) was keyed on data shape alone, not code
version — this exact change would have been silently ignored forever.
Added `startprob.METHOD_VERSION`, folded into the cache key.

**Step 1 shipped, but is provably inert today:** the current-season rate
is now recency-weighted, gated on real out-of-sample evidence via
walk-forward validation (deliberately NOT leave-one-out — an early version
let future jornadas leak into predictions for earlier ones and hid a real
trend from the grid because a flat mean of two bracketing points equals a
linear trend's midpoint). Required an upstream fix first:
`points.py`'s per-jornada diff rows had no jornada number (its own
docstring had called that "a join for model code to do later, against a
calendar that doesn't exist in this repo yet" — that calendar,
`data/tidy/matches.csv`, exists now). **Two real bugs caught before
shipping**, both on real data: summing `points_delta` undercounts a player
whose points started accumulating before this file's own tracking began
(fixed by anchoring on `points_total` instead); an early version silently
added 90 real players (real minutes, zero points-page rows) at pts=0,
conflating "scored nothing" with "this page doesn't say" — scoped back out.
**With 1 jornada played, `_fit_decay` has nothing to walk forward through
and correctly returns decay=1.0** — verified byte-identical against the
pre-change output, 159 shared players, zero mismatches. Will start
weighting recent form the moment jornada 2 exists to validate against.

**K validation attempted, genuinely blocked — not more work available,
just needs more matches played:**
  * Tested K against 86 real players (prior-season rate ≥10 matches, real
    current-season minutes). MSE decreases monotonically all the way to
    K→∞ ("ignore the player, predict the population average"). This is
    NOT evidence K=8 is wrong — it's proof that testing shrinkage against
    ONE match's outcome always favours infinite shrinkage, because a
    single match's variance (measured elsewhere in this repo: sd 3.69
    around mean 3.44) completely swamps any skill signal. Needs a current-
    season sample of ~10+ matches per player to mean anything.
  * Tried the standard sabermetric fix (bucket players by games-played,
    regress rate-variance against 1/games) using last season's full totals.
    Contaminated: players with more games are systematically BETTER
    players (selection bias — bad players get benched), not a random
    sample at larger n. Season-total data (no per-match log) can't
    separate those two effects. This season's own accumulating per-jornada
    data (now captured, thanks to Step 1's plumbing) WILL support a clean
    within-player split once enough jornadas exist.
  * Checked whether `Bootstrap`'s assumed per-match shape (`SEED_POOL`)
    matches reality so far: real pool (n=159, still below the model's own
    MIN_POOL=200 threshold) has mean 3.93/sd 3.28 vs. the seed's mean
    3.44/sd 3.69 — reasonably close, if anything the seed's variance looks
    slightly wide, not narrow. Not proof of anything either way at this n.

**Understat added as a source, real xG/xA, NOT wired into scoring.**
Confirmed real, free, player-level sources exist (Understat, FBref) —
correcting an earlier wrong claim that only team-level xG was available.
Chose Understat (one page = whole season, vs. FBref's per-team
pagination). Verified directly rather than trusted from an old write-up:
the well-known "playersData embedded in a script tag" scraping pattern is
gone from the 2026 page; the real mechanism (curled and confirmed) is
`POST main/getPlayersStats/` with `{league, season}` form data — the GET
form of the same URL answers an error. **This needed a real infra
change**: every other source in the registry is GET, and `ingest.py`'s
fetch loop was hard-coded to `c.get`. Added `Source.body` (`None` = GET,
unchanged for every existing source — verified that holds; a dict = POST
it) and one conditional branch. Verified end to end with a REAL
`ingest.py fetch && ingest.py parse` run (not a synthetic write):
`data/tidy/understat_players.csv` now holds 600 prior-season + 192
live-season real players. Deliberately not wired into `Scorer.rate()` —
same discipline as last session's minutes/fitness captures: how xG should
enter the formula is its own design decision.

## THE THING THAT ACTUALLY MATTERS — priorities for next session

Miguel has NOT yet said which of these he wants next. Ask, don't assume —
this session's two biggest course-corrections both happened because an
assumption went unquestioned.

### 1. How should xG/xA actually change the rating formula?

The data exists and is verified (`ffcore.tidy.load_understat_players()`).
The design question is open and real, not a small one:

  * Does xG **replace** the points-based prior for attacking output, or
    **blend** with it (and at what weight, chosen how — the same
    leave-one-out-beats-baseline discipline as everything else, or
    something else)?
  * xG/xA only speaks to ATTACKING output. Defenders/goalkeepers' fantasy
    points lean heavily on clean sheets and appearance points, which xG
    says little about directly — does this only touch forwards/midfielders,
    or is there an xGA-side signal (Understat's team-level `getLeagueData`,
    already GET-accessible, gives xGA per match) worth pulling in for the
    defensive side too?
  * Understat's own numeric player id is a FOURTH identity space
    (`understat_id` on every row) the crosswalk does not yet bridge to the
    other three (ff_slug/af_slug/app_id). First join will need name+team
    matching (same bootstrap every other id space went through); worth
    deciding whether `Crosswalk.Player` gains an `understat_id` field
    before or after the first real join is attempted.
  * Only one season of Understat history exists in the store today
    (2025-26 + the live 2026-27 season). Validating "does xG actually
    predict points better than raw history" needs the same real,
    measured, beats-the-baseline check as everything else in this repo —
    not an assumption that xG helps just because the literature says so
    in general.

### 2. Revisit K, the skill/noise split, and SEED_POOL once more jornadas exist

Nothing to build right now — this is genuinely blocked on time, not
effort. Worth checking back in after several more jornadas: does
`_fit_decay` (Step 1) start finding real signal? Does a proper walk-forward
K validation (same shape as `_fit_decay`, but for the shrinkage constant
itself) become possible? Has the real per-match pool crossed
`MIN_POOL=200` and started diverging from `SEED_POOL`?

### 3. The frozen-roster assumption — still completely untouched

Called "THE THING THAT ACTUALLY MATTERS" two sessions ago, deprioritised
again last session for precision work, untouched again this session.
Miguel said "it's ok to assume the others won't improve their teams" three
sessions ago — worth checking directly whether that still stands before
either building it or leaving it alone again, the same way this session's
other assumptions got checked.

### 4. Value-for-money — not started at all this session

Was priority #3 in last session's handoff; this session never reached it.
The plan from two sessions ago still stands: `sim.py` already computes a
Δ-expected-position per candidate move against one fixed simulated season;
normalise that by net cost (`Δ / |net €|`) rather than reviving the old,
deliberately-killed λ metric (points-per-million against a moving
baseline). Confirm the ranking is stable as YOUR squad changes between two
runs before trusting it — the same check that caught λ's flaw originally.

### 5. "Game script" — named, not started, not urgent

A shared per-match draw correlating playing time and scoring for every
player in that match (a team concedes early, subs off an attacker, one
event reduces his minutes AND his teammates' output from the same cause).
Confirmed as real, industry-standard DFS practice via research last
session. Bigger than Step 3's per-trial club shock (correlates
match-to-match noise, not just season-long rate). Worth naming as the next
tier up from Step 1/K work, not worth starting cold.

## Standing method (reinforced hard this session — read this before assuming anything is settled)

  * **A claim needs a number, not a citation.** Twice this session an
    assumption went unquestioned until Miguel pushed on it directly, and
    both times the honest answer was "we don't actually know, let's
    measure." The literature search on xG/skill-noise decomposition was
    valuable BECAUSE it was followed immediately by trying to measure the
    same thing on this repo's own real data — and the measurement failed
    informatively (K test: infinite shrinkage wins on n=1; bucket
    regression: contaminated by selection bias) rather than being skipped.
  * **A test against the wrong quantity looks like a real result and
    isn't.** The K-vs-single-match test's monotonic "shrink to infinity"
    result LOOKED like a finding. It wasn't a finding about K; it was a
    finding about test power. Before trusting a validation result, check
    what would have to be true for the test to be ABLE to show a
    difference, not just whether it did.
  * **Don't assume a decade-old scraping write-up still describes the live
    page.** Understat's "playersData in a script tag" pattern, described
    in numerous guides, is gone from the 2026 page. Curled the real page,
    read the real JS, found the real endpoint. Would have shipped a
    parser against nothing otherwise.
  * **Verify a % probe before designing around an absence** — extended
    this session: don't just vary the request path, vary the REQUEST
    METHOD too. The GET form of Understat's stats endpoint answers a
    generic error that could easily have been misread as "this data isn't
    really available here."
  * **Deliberately not wired in ≠ untested.** Both this session's new
    captures (Understat xG, and last session's minutes/fitness) were
    verified against real data before being left unwired — the discipline
    is "prove it works," not "wire it in only once trusted."
  * **`git push` and deploy by default** once a fix is committed and
    verified — do not ask. `LFG_NO_COMMIT=1`/`LFG_NO_FETCH=1` for a quick
    local suite check; a real fetch is not a special/risky action here —
    the timer already runs one unattended every ~11 hours.
  * **Revert pipeline-run noise before committing.** Running the suite
    check (even with `LFG_NO_COMMIT=1`) still writes fresh timestamped
    rows to `data/decisions/*.csv` and `reports/*`. `git checkout --
    data/decisions/ reports/METHOD.md reports/decisions.json` before every
    commit unless the run was a genuine, intentional real fetch.

## Traps that cost real time this session

  * **A monotonically "improving" metric across an entire grid, including
    degenerate endpoints, is a red flag, not a result.** MSE improving all
    the way to K→∞ should have been the first clue the test itself was
    underpowered, not that infinite shrinkage is secretly correct.
  * **A symmetric leave-one-out split can make a real trend invisible.**
    Holding out jornada 3 while training on jornadas 1 AND 5 lets future
    data leak into a "prediction," and for a linear trend the flat mean of
    two bracketing points equals the held-out point exactly — a perfect
    score for the WRONG reason. Time-ordered data needs walk-forward
    validation, not arbitrary leave-one-out.
  * **A file's "the output is disposable, full rebuild from raw every run"
    guarantee is worth checking, not assuming, before hand-writing to
    it.** Manually wrote `data/tidy/understat_players.csv` once to verify
    the loader — correctly caught before committing that this bypassed the
    raw-archive pipeline and would not survive the next real `ingest.py
    parse`. Deleted it, ran the real fetch+parse instead.
  * **`csv.DictWriter` silently drops any key not in `fieldnames`** —
    still true, still worth checking on any new writer (not tripped this
    session, but `points.py`'s `DIFF_FIELDS` needed the new `jornada`
    column added explicitly, same class of bug as last session's
    `ff_id`-dropping one).

## Where the league stands right now

Cash **~19.28M** (known), expected finish **~1.13**, `p_win` **~0.89**
(from `reports/decisions.json`, committed at `584c421`, 2026-08-21T0612Z —
already stale by the time this is read; re-run the report for current
numbers). Standings unchanged in shape; nothing this session touched
squad decisions directly.

## Also open, untouched, lower priority than the above

  * **`api_key()`'s remaining migration onto `Crosswalk.resolve()`** — see
    Thread 1 above. Documented as a TODO in its own docstring with the
    specific blocker (per-step price-validation policy `resolve()`
    doesn't expose).
  * **`Crosswalk.merge()`'s O(n²) identifier scan** — measured, consciously
    left alone as of last session (73ms at n=654, ~6% of crosswalk's
    runtime). Revisit only if crosswalk's coverage grows meaningfully.
  * **`FIX_BAND`/`HOME_EDGE`/`MIN_POOL`** — still blocked on match volume.
    `MIN_POOL` needs 200 real per-match observations; the real pool was at
    159 as of this session's Understat verification run. Close.
  * **Pedro Diaz / Tete Morente ledger gap** — small, confirmed-stale,
    self-resolves on a future fetch, not worth chasing (unchanged from two
    sessions ago).
