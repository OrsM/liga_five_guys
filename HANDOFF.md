# liga_five_guys — handoff, 2026-08-25 (overnight)

Two repos touched tonight: `liga_five_guys` (the model/pipeline) and
`~/claude_projects/website` (the phone-facing renderer, a *separate*
codebase — `src/pages/Fantasy.jsx`). Both pushed and deployed. Working tree
clean in both (test-run artifacts in `data/decisions/`, `reports/METHOD.md`
and `reports/decisions.json` are expected to show as modified after any
local suite check — see "Standing method" below). 30/30 suites pass in
`liga_five_guys`; 140/140 in `website`.

## Start here

    cd ~/claude_projects/liga_five_guys && \
      LFG_NO_FETCH=1 LFG_NO_COMMIT=1 ~/.local/bin/lfg-run 2>&1 | tail -4

Should say `30 suites pass`. Real numbers as of the last live run
(`reports/decisions.json`, committed at `0551bb0`, 2026-08-25T0052Z, a real
fetch, not stale): cash **5.04M**, squad value **241.08M**, expected finish
**1.83**, `p_win` **0.48** — moved a lot tonight (was 1.36 / 0.73 before the
fixture fix below), and the move is real, not a regression; see Thread 2.

## What tonight was, compressed

Ran across one long session, each thread starting from a specific challenge
to a specific number the report showed, not from a self-directed audit.

### Thread 1: the ladder's own bands were a second simulation

`sim.player_bands()` re-ran the whole Monte Carlo a second time at
`FINAL_TRIALS`, against the same seed and the same seasons `decide.rank()`'s
own final pass had already drawn. The draw does not depend on the squad —
measured, ~1.2s of drawing plus ~0.03s per squad scored — so the second pass
paid the 1.2s again for nothing. `rank()` now takes `extra`, Actions to
price riding along in the pass it was already running; `sim.band_acts()`
(what's left of `player_bands()`) only names the questions. Three
simulation passes per report became two, `sim.main()` 8.07s → 6.80s,
byte-identical numbers verified before trusting the speedup. Also unified
the band formula itself (`decide.paired()`/`decide.band()` — one
definition, `rank()` and the ladder both used to hand-roll the same
sorted-diff quantile).

### Thread 2: two "frozen for the season" bugs, both live in production

Told directly: *"you're telling me to sell him which I think is wrong so
pls check into this situation and find an overall fix."* Checked rather
than defended: `decide.load()` scored each player ONCE per run and handed
`Bootstrap` the identical `(points, p_start)` tuple for all 38 remaining
jornadas.

**P(start).** Robin Le Normand — suspended one match, 91.7% of this
season's real minutes otherwise — was priced at 27% to start EVERY
remaining jornada, including the 35 after his suspension ends, and
`dead_weight()` correctly (given that input) listed him as sellable for
zero points in a REAL published report. Fixed: `ffcore.score.Scored`
gained `pct_rest` (his season-standing rate, shrunk toward `NEUTRAL_START`,
not toward this week's status) alongside `pct_used` (unchanged, still
status-aware, but now scoped to only the immediate jornada).
`decide.next_then_rest()` builds Bootstrap's per-jornada dict from the two,
per player.

**Fixture difficulty**, same shape one layer down, found while explaining
the first fix and directly challenged: *"why not simulate them with the
best info we have instead of patching."* The schedule is published in
full — `matches.csv` already has every remaining jornada's matchup — so
this was not a data gap. `ffcore.fixture.season_board()` now answers "who
do you face, every remaining week" the way `fixture_board()` only ever
answered "who do you face next", sharing one Match-building core so the two
can't disagree about an identical fixture. **Verified centred** (factors
average 0.988/1.030 across 720 real pairs, matching the "centred on 1.0 by
construction" contract the underlying rating functions already claim) before
trusting the direction of a real, large swing: `expected_finish` 1.36 →
1.71 that run, `p_win` 73% → 54% — my own squad's clubs had mostly had an
easier-than-average jornada 3 (Betis 1.19 vs. a 0.98 season average),
which is exactly why the frozen model had been reading optimistic.

`sim.md`'s own "Not modelled" table said "P(start) is today's, held flat
over every remaining jornada" for as long as that was true — updated to
say what's actually still not modelled (an unannounced FUTURE absence, not
a known current one).

### Thread 3: a held player's own Season figure was a pure sale

Directly challenged again: *"why would I keep players with net negative
season value... this doesn't consider the relative money I could get from
them."* The KEEP/SELL rows priced "sell him, buy nothing" — a real number
(negative there already meant "selling costs you points," the argument FOR
keeping) but not the reinvestment-aware question a BUY row already answers.
`decide.best_swap_for(u, k, expected)`: the best target `k`'s own proceeds
plus cash reach, same cheap screen `candidates()` uses, scoped to one
funding player (`candidates()`'s own swap search dedupes to one funding
source per target, which crowds out every player who wasn't the winner —
cannot answer for every held player individually).

**Two real bugs caught by checking live data before shipping:**
  * The "already answered by a real row" skip started checking both the
    buy AND sell side of `rank()`'s survivors — dropped a squad member's
    own band whenever he happened to fund an unrelated top-ranked move (an
    XI "TAKE OFF" row went blank for exactly this). Reverted to buy-side
    only; a held player's key can never collide with a real row's buy
    side, so the broader check bought nothing.
  * `best_swap_for()` first compared `expected()` across EVERY position —
    put the one rostered goalkeeper on three different midfielders' bands.
    Real, honestly negative numbers (a broken squad shape costs points in
    the simulation), attached to a swap no manager would make. Fixed:
    same slot only.

### Thread 4: website (`~/claude_projects/website`, separate repo)

  * Dropped the warnings box by request — real, freshly recomputed data
    every run, but the same 2-3 sentences for weeks at a stretch since
    squad composition rarely changes; read as static even though it
    wasn't. `decisions.json` still carries the field, only the phone
    stopped drawing it.
  * Ladder/Standings tables had no `overflowX` on their wrapper — a
    6-column table on a 390px phone pushed the whole page wide (the
    "sliding right", mismatched header sizes Miguel screenshotted) instead
    of scrolling sideways. Same fix the markdown view's own `post` style
    already used.
  * KEEP/SELL rows now show a "vs X" note when the band prices a real swap
    (Thread 3) rather than a pure sale — the number alone doesn't say
    which question it's answering.

## Priorities for next session

Ask before assuming any of these — every thread tonight started from Miguel
challenging a specific number, not from a self-picked priority.

### 1. Fixture-difficulty coverage on the schedule join

`season_board()` joins `matches.csv`'s home/away names against the market's
club list via `match_team()` — no club id available in that file, unlike
`fixtures.csv`. Not measured tonight: how much of the 38-jornada schedule
actually joins for every player, vs. quietly falling back to the frozen
"next fixture" number for an unjoinable club. Worth a real coverage check
before assuming it's complete everywhere.

### 2. `best_swap_for()`'s reach vs. `candidates()`'s multi-sale funding

`best_swap_for()` funds a swap from `k`'s own proceeds plus cash only — it
does not consider funding a bigger upgrade with `k` plus additional
dead-weight sales the way `candidates()`'s multi-sale path does. Is a
single-player-funded swap the right scope for "what is HE worth", or should
a held player's band also explore "sell him AND some dead weight, buy
someone better"? Not obviously wrong as scoped (the question is specifically
about him), but worth asking rather than assuming.

### 3. Warnings — the underlying computation is still there, unused

`report.py`'s warning-generation (`SLOT_MIN` thin-slot check, unmodelled
players, stale feed) still runs and writes to `decisions.json`, just no
longer drawn. If it's genuinely dead weight now, it could be trimmed from
`report.py`/`sim.py` too — deliberately NOT done tonight, scope was "remove
the display."

### 4. Everything still open from the 2026-08-21 handoff

xG/xA formula design questions, K/shrinkage revalidation once more jornadas
exist, the frozen-roster assumption, "game script" correlation — none of
these were touched tonight. See git history (`HANDOFF.md` before this
commit) if picking one of these up; this file replaced that one rather than
carrying its detail forward, since tonight's threads were unrelated to all
of them.

## Standing method (tonight's version)

  * **A number the user pushes back on is worth checking, not defending.**
    Every one of tonight's three real fixes started with a direct
    challenge to a specific figure the report showed, not a self-directed
    audit — "check into this and find an overall fix," "why would I keep
    a player with negative value," "why not simulate with the best info we
    have instead of patching."
  * **Check the mechanism before trusting the direction of a big diff.**
    The fixture fix moved `p_win` by 19 points in one run. Before shipping
    it: confirmed the fixture factors are centred at ~1.0 across 720 real
    pairs (no systematic bias), then confirmed MY specific squad's clubs
    had an unusually easy jornada 3 relative to their season average —
    which is what explains the direction, not a coincidence or a bug.
  * **A broadened "already answered" check needs the same scrutiny as a
    narrowed one.** Checking both sides of an Action instead of one looked
    strictly more correct and silently dropped a real row's number. Ran
    the real report and read the diff before deciding a change was safe.
  * **One points scale does not mean one comparable pool.** `expected()`
    puts every position on the same number for `best_xi()`'s formation-slot
    purposes; a SQUAD-slot swap (`best_swap_for()`) needed same-slot
    filtering on top, which `candidates()`'s own cheap screen apparently
    also doesn't do explicitly — worth checking whether that's a live gap
    there too, not assumed clean because it wasn't today's bug.
  * **Avoid manual polling wait-loops on top of a harness that already
    notifies on completion.** Chained `until pgrep ...; sleep; done` loops
    around backgrounded commands tonight and it read to Miguel as one very
    long unclosing command. Just wait for the completion notification.
  * **`git push` and deploy by default** once a fix is committed and
    verified — do not ask, including across repos and including a real
    live fetch (`LFG_PUSH=1 ~/.local/bin/lfg-run`, no `LFG_NO_FETCH`): the
    timer already runs one unattended every night, a manual one is not a
    special risk.
  * **Revert pipeline-run noise before committing** — `git stash push -u
    -m "..." -- data/decisions reports/METHOD.md reports/decisions.json`
    around a `git pull --rebase`/push, then `git stash pop`, is the
    pattern used tonight to keep local test-run artifacts out of the
    commit without discarding them.

## Also open, untouched, lower priority

Carried forward unverified from 2026-08-21 — not re-checked tonight, threads
were unrelated:

  * `api_key()`'s remaining migration onto `Crosswalk.resolve()`.
  * `Crosswalk.merge()`'s O(n²) identifier scan (was 73ms at n=654 as of
    2026-08-21).
  * `FIX_BAND`/`HOME_EDGE`/`MIN_POOL` — still guesses/blocked on match
    volume as of 2026-08-21 (real pool was at 159 of 200 needed then).
  * Pedro Diaz / Tete Morente ledger gap — small, self-resolving.
