# liga_five_guys — handoff, 2026-08-20 (evening)

Private repo `OrsM/liga_five_guys`, working tree clean, **pushed** at `e988144`,
29 suites pass.

## Start here, and do not open with an audit

**This file is the map.** Read it, run the one command below, then start on item 1
of "What to do next." Everything up to here is committed, deployed and verified —
a session that re-derives it has spent its first twenty tool calls on something
already paid for.

    cd ~/claude_projects/liga_five_guys && \
      LFG_NO_FETCH=1 LFG_NO_COMMIT=1 ~/.local/bin/lfg-run 2>&1 | tail -4

If that says `29 suites pass` and publishes 2 files, nothing has rotted.

**Outside the repo:** `~/.local/bin/lfg-run` was edited today to register
`ffcore/model.py`'s suite (28 → 29). That file is not tracked by git — if this box
is ever rebuilt from the repo alone, re-add `ffcore/model.py` to the `TESTS=(...)`
list near the top, or the run will report 28 and be wrong.

## What today was: identifiers, not names

A long session moved every join in the repo from a normalised NAME to the
identifier each source actually publishes — the market page's `data-id`, the
app's `player_id`, the points page's click-handler id, club ids on both fixture
sources. Four comments in the code asserted an identifier didn't exist; all four
were wrong, found by opening the raw page rather than re-reading the code. Full
account, with the measurements: https://claude.ai/code/artifact/36a202e6-978e-4d16-a2af-b4cc28214afa

Consequence for you: the C. Romero bug (a wrong join that priced a 45.7M
purchase against a 6.2M player) is fixed, ledger joins are 66/66 instead of
60/66, and a corrected id now displaces a stale one instead of both surviving
forever. Three of my own regressions along the way were caught by MEASURING
outputs, not by reading code — that discipline is the reason to keep doing this
the same way: control run, treatment run, diff `decisions.json` on a **pinned
clock** (`LFG_NOW=2026-08-20T0700Z ...`, see `ffcore/model.py`'s docstring),
understand every line that moves before committing.

## THE THING THAT ACTUALLY MATTERS — READ THIS BEFORE TOUCHING CODE

Miguel looked at `p_win: 0.92` on the first day of a 5-manager league and said,
correctly, that it doesn't pass a smell test. **He was right, and it is not a
bug in the arithmetic — it is what the simulation is actually simulating.**

`ffcore/season.simulate()` plays out all 38 remaining jornadas with EVERY
manager's squad frozen exactly as it stands today. No manager — you or any
rival — ever makes a transfer, in any of the 2,000 trials. It compounds day-one
squad value across an entire season and calls the result "P(win)." The
per-rival `p_above` figures ARE internally consistent with that assumption (I
checked the arithmetic by hand against the season bands) — the assumption
itself is the problem, not the code computing it.

Why it exists: `simulate_many`'s docstring is explicit — it was built so that
comparing "buy this player" against "hold" replays the SAME random season for
both squads, which is exactly right for a one-week decision (`decide.py`'s
buy/wait ranking). It was never designed to answer "what's my probability of
winning the league," and reusing it for that silently assumes the other four
managers never touch their squads again. That is the whole gap between 92% and
believable.

A second, smaller effect stacks on top: `Bootstrap.rate_draw` draws each
player's season-long rate uncertainty INDEPENDENTLY. A real club's slump moves
every one of its players' returns together; the model doesn't have that
correlation, so it understates variance further. That's `MIN_POOL = 200` in
`ffcore/forecast.py`, currently at 96 real match observations — the seed pool
covers the gap and the note says so (`pool_note()`), but the correlation itself
isn't modelled yet either way. Real, but the smaller of the two effects — do it
second.

### What to do about it, in order

1. **Relabel `p_win` before doing anything else.** It currently reads as a
   forecast; it is a squad-quality sanity check under a frozen-roster
   assumption. Cheap, honest, stops the number misleading anyone while the real
   fix gets built. Touches `sim.py`'s render text and `reports/METHOD.md`'s
   wording — no model change, so it should be byte-identical on
   `decisions.json` except the label itself.
2. **Give the season simulation a transfer policy — the real fix.** Each
   trial, each jornada, each manager (rivals included) makes a small number of
   plausible swaps toward their best-value pool. `sim.py` already has a market
   model for "wait vs act" (`Offers.fit` in `ffcore/market.py`) — the shape of
   the mechanism to reuse for rivals is there, the season loop isn't wired to
   call it per-jornada per-manager yet. This is genuinely more work than
   anything else on this list; expect it to be its own session.
3. **Club correlation, once the match pool clears 200.** Second-order next to
   #2. `MIN_POOL` in `ffcore/forecast.py` — check the pool size before starting
   here; if it's not close, note it and move on rather than half-building it.

If you only do one thing this session, do #1 — it costs an hour and stops the
number lying in the meantime.

## Standing method (keep doing this)

- **Measure before believing.** A claim read off the code is not a finding —
  every real defect found today was caught by running the code and diffing
  outputs, not by reading comments. Several comments this session were
  confidently wrong.
- **Control vs treatment, one store, clock pinned.** `LFG_NOW=<stamp>` on both
  runs, or an in-between fetch reads as a fake regression — this cost real time
  twice today (once looked like a 7-point P(win) swing, once like 3M of cash
  appearing).
- **TDD against the module's own `_selftest()`.** Add the failing assertion
  first, watch it fail, then fix. No pytest, no test directory.
- **One and only one implementation per key operation.** If you find a second
  place computing the same fact, that is the bug, not a style complaint — see
  the artifact above for the pattern (methods own state that can be wrong,
  free functions stay free).
- **Do not hardcode a decision rule.** If a rule is needed, the metric is
  wrong. `FIX_BAND`, `HOME_EDGE`, `DOUBT_FACTOR` are still unfitted guesses
  sitting in `ffcore/fixture.py` / `sim.py` — n=1 per bucket, lower priority
  than the transfer-policy work above but real.
- **No prose in the reports. Tables.**
- **`git push` and deploy by default** once a fix is committed and verified —
  do not ask.

## Where the league stands right now

Cash **19.18M**, squad **220.04M**, formation not yet reported (early season),
expected finish **1.09**, `p_win` **0.92** (see above — read this number as
"if nobody transfers again," not as a forecast). Season band 1,592–1,954 pts.
Standings (1 jornada played):

| manager | now | projected mean | cash | p(I beat them) |
|---|---|---|---|---|
| **miguel_autentico (me)** | 31 | 1,772 | 19.18M | — |
| SusoGattuso | 26 | 1,482 | 5.33M | 93.7% |
| BurtonGM89 | 28 | 1,383 | 19.08M | 97.7% |
| Magic Mike 333 | 15 | 1,239 | 2.38M | 99.7% |
| Albert Laporta | 10 | 1,133 | 1,278 | 100% |

## Also open, untouched today

- **Buy-below-floor as a live warning.** A purchase can never legally price
  below the player's value — cheapest, sharpest bad-join detector there is,
  and it's how the C. Romero bug actually surfaced this session. Exists only
  as a manual query today (`ffcore/bid.py`'s `usable()` path is where it
  belongs).
- **`api_stats` and `player_status`, fetched and unread.** 882 rows of
  `api_stats` include `mins_played` — P(start) is currently graded binary
  (played/didn't), not by minutes. `player_status` is the app's own fitness
  read, sitting next to two editorial scrapes it's never been checked against.
  Straight accuracy upgrade, no plumbing needed, data's already in
  `data/tidy/`.
- **Render/store boundary, mostly closed, not locked in.** `ffcore/model.py`
  now holds the one League + one Scorer per run (`session()`); `report.py` and
  `sim.py` point at it. Remaining reads in `report.py` (log dedup,
  `load_deadline`) are legitimately per-stage. What's missing is a guard test
  asserting no renderer imports `ffcore.tidy` except `methodology.py` (which
  legitimately describes the store). Low priority next to #1/#2 above — code
  hygiene, not model accuracy.

## Traps that cost real time — do not rediscover these

- **"THE SOURCE DOES NOT PUBLISH IT" IS A GUESS UNTIL PROBED.** Four comments
  this session asserted an identifier didn't exist. All four were wrong.
- **Pinning the clock is not pinning the store.** A fetch landing between two
  runs looks exactly like a regression.
- **A half-migrated key looks exactly like a model change.** `score()` moved
  to id keys while `second` (the analiticafantasy blend) stayed name-keyed for
  one commit — every player silently lost the second opinion, and every
  calibrated P(start) moved. Caught by diffing one player's inputs, not by
  reading.
- **A merge-not-rebuild table can silently double.** The crosswalk kept 657
  name-keyed ghost rows alive after the key changed to ids, because `merge()`
  never subtracts by design. Fixed (`548fdb1`) — the pattern is worth
  remembering if another table's key ever changes shape.
- **A purchase PRICE is not a VALUE.** It's the value plus whatever it took to
  win. A join that treats them as interchangeable prefers the wrong man and
  produces an impossible buy (below the floor) — which is exactly the signal
  the open "buy-below-floor warning" item above is meant to catch by default.
