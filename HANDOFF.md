# liga_five_guys — handoff, 2026-08-21 (night)

Private repo `OrsM/liga_five_guys`, working tree clean, **pushed** at `7de1d2e`,
30 suites pass.

## Start here, and do not open with an audit

**This file is the map.** Read it, run the one command below, then start on
item 1 of "What to do next." Everything up to here is committed, deployed and
verified.

    cd ~/claude_projects/liga_five_guys && \
      LFG_NO_FETCH=1 LFG_NO_COMMIT=1 ~/.local/bin/lfg-run 2>&1 | tail -4

If that says `30 suites pass` and publishes 2 files, nothing has rotted.

**Outside the repo, again:** `~/.local/bin/lfg-run` is not tracked by git and
was edited twice this session to register two new suites (`ffcore/model.py`
was already there; `ffcore/attributes.py` was added 2026-08-20). If this box
is ever rebuilt from the repo alone, re-add both to the `TESTS=(...)` list
near the top or the run will under-report and be wrong.

## What last session was, compressed

A very long session that started as "improve the forecast" and became mostly
identity-join hardening, because every forecasting improvement kept tripping
over the same class of bug. In order:

1. **Ingest/crosswalk speed** — two real money-shots, both from the same
   anti-pattern (rebuilding a lookup index from scratch on every call instead
   of once): crosswalk 6.2s → 1.2s, parse 4.47s → 3.0s. Verified
   byte-identical output before/after both times.
2. **Two new data-capture pieces**, deliberately not wired into forecasting
   the same day they were built: real per-match minutes (`starters.csv`'s
   `minute` column, from `sources.parse_starters`), and
   `ffcore/attributes.py`'s `resolve_fitness()` — the app's own fitness read
   cross-checked against the editorial one, surfaced as a report warning.
3. **`football-data.co.uk` added as a source** — real match results + this
   season's xG, `data/tidy/results_history.csv`, `sources.FD_ALIASES`/
   `fd_sources()`. Team-level, 20 clubs, not player-level.
4. **The forecasting-precision plan, Steps 1–3 of 4, all shipped:**
   - Step 1: this season's actual results are now blended into the rate at
     all (`self.current` was permanently empty before — a genuine, separate
     bug found while scoping the "just weight by minutes" task), weighted by
     real minutes instead of raw appearance count
     (`ffcore.score._current_from_perjornada`/`_current_minutes`).
   - Step 2: the single squad-value/Elo fixture factor is now split into
     `atk_factor`/`def_factor`, fitted from real results
     (`ffcore.fixture.attack_defense`), falling back per-club to the old
     rank-based number for the few clubs with too little history.
   - Step 3: `Bootstrap.rate_draw()` and `season.py`'s numpy fast path both
     now draw a shared per-club shock (`ffcore.fixture.club_volatility`) on
     top of each player's individual uncertainty, so a squad concentrated in
     one club shows realistically wider variance instead of averaging it
     away. Verified on real pinned data: season band widened 388 → 407
     points, `p_win` barely moved (0.965 → 0.964) — wider, not biased.
   - **Step 4 (P(start) graded on minutes instead of binary) is NOT done.**
     See "what to do next," #2.
5. **Three real identity bugs found and fixed, each bigger than it first
   looked, each found by checking real output rather than trusting a passing
   self-test:**
   - `ffcore/league.py`'s `replay()` seeded initial-roster ownership with a
     bare `norm(name)` key while the market assigns almost every player a
     numeric one. Silent today only because `League.__init__` overwrites it
     with the app's own live data whenever `api_teams` answers. Measured
     impact once actually checked: **143 warnings → 5** — this was not a
     cosmetic 8-player issue, it was corrupting `identify()`'s counterparty
     check for roughly a third of this league's transaction history.
   - One residual case (Manuel Fernández) needed a second path
     (`xw.player(app_name=...)`) because the market's *current* spelling had
     moved on from what the roster was typed against (`Manuel Fernández` →
     `Manu Fernandez`). 5 → 3.
   - **The actual fix, once Miguel pushed on why the file was name-keyed at
     all: `rosters_initial.txt` migrated to id-per-line.** Verified the
     migration itself changed nothing — `League.owner`, all 70 entries,
     byte-for-byte identical before/after.
   - Same class of bug, swept for elsewhere on request: `data/season/
     points_2025-26.csv` (the frozen prior-season snapshot) had the identical
     shape. Root cause was one layer upstream of expected —
     `ingest.baseline()`'s CSV writer was silently dropping the `ff_id`
     column `parse_points()` already extracted. Regenerated from the raw
     snapshot `baseline()` already preserves (no network call needed); 757 of
     757 rows now carry a real id. Measured impact today: **zero** — no
     name has drifted between last season and this one yet — but the exact
     failure this closes off already happened once this session, in a
     different file.

Full detail on any of the above, with the actual before/after numbers, is in
git log — every commit message this session was written to be read on its
own, with what was measured, not just what was changed.

## THE THING THAT ACTUALLY MATTERS — Miguel's three priorities for next session

He was explicit, in this order of framing (not necessarily priority):

### 1. A single, unique join / player-identification approach

**He is right to be frustrated, and it is not fixed yet — only patched, three
times, in three different call sites.** Right now there are at least six
separate, independently-written player-identity resolvers in this repo, each
solving basically the same problem its own way:

  * `ffcore.crosswalk.Crosswalk.player(name=, ff_slug=, af_slug=, app_id=,
    app_name=)` — the central id table's own lookup.
  * `ffcore.tidy.row_key(row, shared)` — id-first, `name@club` fallback, for
    a raw market row.
  * `ffcore.tidy.Market.key_for(name, team, value)` — exact/shared-name/
    price-tiebreak resolution against the market.
  * `ffcore.text.resolve(query, rows, key)` — generic fuzzy string match
    (exact → substring → token), used by several of the above and by
    `ffcore.second`/`methodology.py` independently.
  * `ffcore.league.api_key()` — a five-step chain specifically for the
    app's own API rows (nickname, full name, crosswalk app_id, ledger
    tie-break, price match).
  * `ffcore.league.identify()` — a different chain for ledger transaction
    rows (crosswalk app_id first, then market candidates pruned by
    counterparty/price).
  * `ffcore.league._roster_key()` — the one built this session, for roster
    lines specifically (market.key_for, then app_name, then norm fallback).

**Every single identity bug found this session — and there were at least
five, across fixture.py, decide.py, and league.py — was the same root cause:
two of these resolvers (or a resolver and a raw namespace like `ff_slug` vs
the market's display spelling) disagreeing about what a "key" looks like.**
Fixing them one call site at a time works, but it is guaranteed to keep
finding new ones, because there is no single place that owns the answer to
"given whatever a source calls a player, what is his one key" — six different
functions each have a *plausible* answer, and they were built at different
times against different assumptions about what the canonical key even is
(sometimes a normalised name, sometimes `name@club`, almost always numeric
today but not by any single rule).

**What this session learned that a redesign should start from:**
  * The canonical key IS numeric today for the overwhelming majority of
    players (`row_key()`'s own docstring: 44,912 rows checked, `ff_id`
    present on every one). `name@club` is a real but much rarer fallback
    (three players in this league, currently).
  * A display name is NOT a stable identifier across a season — proven,
    not theorised (Manuel Fernández → Manu Fernandez, mid-season).
  * The crosswalk (`players.csv`) already IS the one table meant to answer
    this — the six resolvers above all exist because callers each grew a
    slightly different way of reaching it (or of falling back when it does
    not yet know a player) rather than there being one function every
    caller could call unconditionally.

**A concrete first step, not a full redesign in one sitting:** write ONE
function — `ffcore.crosswalk.resolve(raw, *, hint_club=None, hint_id=None,
hint_price=None, market=None) -> str | None` or similar — that every one of
the six above becomes a thin wrapper around (or is deleted in favour of).
Order of trust inside it should be argued from what's already been learned:
an exact id beats everything; a market-current exact name beats a fuzzy
match; a club or price hint prunes ambiguity; a stale/app_name alias is the
last resort before refusing. Build it with the SAME discipline as
`attack_defense()`/`club_volatility()` this session — one function, verified
against real data before wiring it into a single caller, then migrate
callers one at a time with a byte-identical before/after check each time
(exactly the `League.owner` diff this session used for the roster
migration) — not a big-bang rewrite.

### 2. A massive improvement to the forecasting approach

Three real things stacked here, roughly in order of how contained each is:

  * **Step 4, unstarted:** `ffcore/startprob.py`'s `Calibration.fit()` still
    grades P(start) as binary played/didn't. The sample-size objection that
    blocked this three sessions ago is resolved — `starters.csv`'s `minute`
    column now gives real per-match minutes for the whole 274-player
    match-day universe. Redesigning the Brier objective to score against
    minutes (or a bucketed version of it) instead of a 0/1 label is the
    smallest, most contained piece of unfinished forecasting work.
  * **The frozen-roster assumption is STILL completely untouched.** This was
    called "THE THING THAT ACTUALLY MATTERS" several sessions ago and has
    not been revisited once since — everything this session and the one
    before it improved the SHAPE of the distribution (Steps 1–3), not the
    assumption that no manager, including rivals, ever transfers again for
    the rest of the season. Miguel explicitly deprioritised this relative to
    precision work earlier this session ("it's ok to assume the others won't
    improve their teams") — worth checking with him directly whether that
    still stands before either building it or leaving it alone again.
  * **The "game script" idea, discussed and deliberately deferred:** a
    shared per-MATCH draw that determines playing time and scoring for
    every player in that match jointly (a team concedes early, subs off an
    attacker, and that one event reduces his minutes AND his teammates'
    output from the same cause) is real, industry-standard DFS-simulator
    practice, confirmed via search — but explicitly bigger than Step 3's
    per-trial club shock, which only correlates the SEASON-LONG rate, not
    match-to-match noise inside `Bootstrap.draw()`. Worth naming as the next
    tier up, not worth starting cold.

### 3. A value-for-money angle

**New territory — nothing this session touched price-normalised ranking.**
The history matters here: this repo already had one (λ, points-per-million
against your current eleven) and killed it deliberately, for two documented
reasons — it was measured against a MOVING baseline (your own squad), so
improving your team retroactively made past λ verdicts look wrong; and it
was blind to a rival's OWNED players entirely (couldn't price a clause
steal), which `sim.py`'s whole-squad simulation fixed by construction.
**Reviving "value for money" without re-introducing either flaw** likely
means: `sim.py` already computes a Δ-expected-position (or Δ-points) per
candidate move, against the one fixed simulated season — normalise THAT by
net cost (`Δ / |net €|`), rather than reviving a standalone points-per-euro
score measured against a squad that keeps changing under it. The board that
did this before is gone; the number sim.py already produces every run is not
the same shape and does not have λ's structural problem, but confirm that by
checking what happens to the ranking as YOUR squad changes between two runs
before trusting it, the same way λ's flaw was originally caught.

## Standing method (keep doing this — reinforced hard this session)

  * **Measure before believing, still true of your own recent commits, not
    just old code.** The biggest catches this session were checking output
    from code THIS session had already unit-tested and believed correct:
    `_roster_key()` passed its own test and was still wrong for 60+ players
    until the drift-warning COUNT was actually diffed; `rate_draw()`'s club
    correlation passed its own unit test and was completely inert in
    production until `p_win` was compared bit-for-bit before/after and
    found unchanged.
  * **A passing self-test proves the function is right, not that it's
    reachable.** `ffcore.season._run_np()` is a SEPARATE reimplementation of
    `Bootstrap.rate_draw()`'s math for when numpy is installed — not a
    caller of it. Fixing and testing one path left the other, the one
    actually used in production, completely untouched. Before declaring a
    fix done, check which code path production actually takes, not just
    which function you edited.
  * **A test that uses the same spelling/namespace on both sides of a join
    cannot catch a namespace mismatch.** Every identity bug this session
    passed its own test right up until real data (which uses inconsistent
    namespaces BY NATURE — that's what the bug is) was checked. When testing
    a join, deliberately use two DIFFERENT strings for the two sides, the
    way the final `fixture_board()` test in this session does — matching
    strings on both sides is the single easiest way to write a test that
    cannot fail.
  * **Control vs treatment, one store, clock pinned**, still the standing
    rule — `LFG_NOW=<stamp>` on both runs. Used repeatedly this session to
    separate "my fix changed the number" from "the data moved between two
    runs."
  * **`git push` and deploy by default** once a fix is committed and
    verified — do not ask. `LFG_NO_COMMIT=1`/`LFG_NO_FETCH=1` for a quick
    local suite check; drop both for the real end-of-task run.
  * **When told to fix one thing, check for the same class of thing
    elsewhere before declaring done** — this is literally how the
    points_2025-26.csv fix was found this session, on direct request, and it
    is worth doing unprompted next time a bug's root cause looks structural
    rather than local.

## Traps that cost real time this session — do not rediscover these

  * **Two things can be "the same player" and still be two different
    STRINGS in two different NAMESPACES** (market display name vs `ff_slug`
    vs crosswalk numeric id vs a normalised name). This bit fixture.py,
    decide.py, and league.py separately, THIS session, after already
    knowing about it from crosswalk work sessions earlier. It will keep
    happening until priority #1 above is actually done.
  * **`market.key_for()`'s `team` param expects the SAME club spelling
    convention the market itself uses** — passing a slug or an
    already-normalised club name silently fails to disambiguate rather than
    erroring.
  * **A file's own docstring claiming something is derived/automatic is not
    proof it is** — `rosters_initial.txt` said "reconstructed by rolling the
    ledger backwards," which was true, and STILL left it name-keyed, because
    the reconstruction preserved whatever representation it was fed rather
    than resolving to a canonical id at the point of writing.
  * **`csv.DictWriter` silently drops any key not in `fieldnames`** — this
    is exactly how `ff_id` vanished from `points_2025-26.csv` despite the
    parser already extracting it. Check the WRITER's fieldnames list, not
    just the parser, when a field that should be there is missing from a
    file.

## Where the league stands right now

Cash **19.18M**, squad **220.60M**, expected finish **1.07**, `p_win`
**0.935** (season band 1,618–2,029 — wider than a session ago, by design:
see Step 3). Standings unchanged in shape from before; re-run the report for
current numbers, these are already a few hours stale by the time you read
this.

## Also open, untouched, lower priority than the three above

  * **`Crosswalk.merge()`'s O(n²) identifier scan** — measured (73ms at
    n=654, ~6% of crosswalk's now-1.2s runtime), consciously left alone: not
    worth the correctness risk of a rewrite in the one piece of code that
    exists because of two previous identity bugs. Revisit only if
    crosswalk's coverage grows meaningfully past today's 654 players.
  * **The recurring `report` stage slowdown after a real fetch** (0.6-0.8s
    on `LFG_NO_FETCH` runs vs 14-15s after an actual fetch, seen 3-4 times
    this session) — noticed, never chased. Possibly cold-disk-cache after a
    large write; possibly something else. Worth a profile if it keeps
    showing up.
  * **`FIX_BAND`/`HOME_EDGE`/`MIN_POOL`** — still blocked on match volume,
    not code. `MIN_POOL` needs 200 real observations, was at ~96 a few
    sessions ago; check where it stands before assuming it's still blocking.
  * **Pedro Diaz / Tete Morente** — a small, confirmed-stale ledger gap (2
    players, "ledger says owned, app says free agent," neither in the
    original roster so it's a missing sale in the activity feed, not a code
    bug). Self-resolves on a future fetch; not worth chasing further.
