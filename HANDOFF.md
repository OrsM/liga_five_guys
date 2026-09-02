# liga_five_guys — handoff, 2026-09-02

Two repos touched: `liga_five_guys` (the model/pipeline, 13 commits) and
`~/claude_projects/website` (the phone renderer, `src/pages/Fantasy.jsx`,
3 commits, deployed live via `./deploy.sh`). Working tree clean (test-run
artifacts in `data/decisions/`, `reports/METHOD.md` and
`reports/decisions.json` are expected to show as modified after any local
suite check — `git checkout --` those same paths, see "Standing method").
145/145 `decide.py` self-test cases, 194/194 `sim.py`, plus
`ffcore.{forecast,season,score,market}` and `methodology.py`/`digest.py`
all green — pushed at `379a604` (liga_five_guys) / `7591928` (website).

## Start here

    cd ~/claude_projects/liga_five_guys && \
      uv run --frozen python src/decide.py --selftest && \
      uv run --frozen python src/sim.py --selftest

Real numbers as of the last live run (`reports/decisions.json`, committed
at `f0b3041`, 2026-09-02T1825Z): cash **28.80M**. BUY was empty this run
("free agents — none clear the bar today"); every real opportunity on the
board was a rival's own player, correctly split between RAID (clause,
real) and LISTED (his own choice, 0/119 ever).

| Manager | now | simulated | 10–90 | P(above) |
|---|--:|--:|--:|--:|
| BurtonGM89 | 125 | 1,482 | 1,103–1,919 | 49% |
| **miguel_autentico** | 135 | 1,474 | 1,104–1,890 | — |
| SusoGattuso | 126 | 1,420 | 1,078–1,812 | 55% |
| Magic Mike 333 | 132 | 1,283 | 909–1,725 | 69% |
| Albert Laporta | 53 | 1,233 | 876–1,632 | 73% |

Albert's row is real now, not a frozen 32-32 — see Thread 4.

## What today was, compressed

Every thread started from Miguel reading actual numbers on his phone and
asking why they looked wrong, or from a swarm review he asked for
directly — nothing here was self-directed audit for its own sake.

### Thread 1: the report/ranking layer, swarmed and fixed (`831911a`…`78d5eb7`)

Miguel: *"the raid should focus on the clause impacted ones... I want to
know if any free players are worth buying honestly"* plus *"why would
having a clause... be a good thing?"* led to a 4-fork parallel review of
`sim.py`/`decide.py`, then fixes:

  * **`best_swap_for()`, `market_percentile()`, `_move_rank_key()` were
    all price-blind in different ways** (`831911a`) — a KEEP row's "vs X"
    picked by raw points not value; the market-quality percentile mixed
    clause-route opportunities into a free-agent-only historical
    baseline; a `d_win > 0` move could lead the whole BUY table on
    win-probability alone even when its OWN premium-charged `d_pos` said
    it was a net loss after the clause markup. Fixed by requiring
    `d_pos > 0` alongside `d_win > 0` for the top tier, and by
    route-matching both sides of the percentile comparison.
  * **The phone's JSON still carried a `verdict` sentence deliberately
    cut from the markdown** (`2c35cb1`) — the same "fix lands in one
    renderer, not its sibling" shape this repo keeps finding. Dropped.
    Also collapsed `wait_routes()` from 5 calls/report to 2 and
    `market_model()` from 2 to 1.
  * **"Act today" was a cheap linear estimate wearing the same column
    header as the BUY table's real simulated number** (`fddb41b`) — now
    reuses `rank()`'s own top real `d_pts`/band directly.
  * **Clause-race risk research, not a guess** (`78d5eb7`) — Miguel
    described the race's real mechanics unprompted (denial value, the
    loser keeps his cash) and asked whether anyone modelling this kind of
    thing had already worked it out. It maps onto **N-player preemption
    games** in real-options economics: the loser's capital isn't
    destroyed, only the option is, so losing a race costs the NEXT row on
    the table, not the row's own figure — a fact already computed, no
    fabricated win-probability needed. Caveat rewritten to say this.

### Thread 2: the forecasting engine, swarmed — the most consequential bug of the day (`ff64aa9`, `e762901`)

Same pattern, one layer down: a direct request to swarm-review the layer
underneath the report led to a 4-fork review of
`forecast.py`/`score.py`/`startprob.py`/`season.py`/`market.py`.

  * **The drift "random walk" was redrawing its position from cumulative
    variance every jornada, not accumulating steps** (`ff64aa9`) — each
    jornada got the right MARGINAL spread but ZERO correlation with the
    jornada before it, undercutting the entire point of the feature
    (persistent bias should compound, not partially cancel under the
    CLT). Bug existed independently in BOTH `Bootstrap.rate_draw()` (pure
    Python) and `season._run_np()` (the numpy path, the ACTUAL production
    one). Fixed in both, verified with a new self-test that provably
    discriminates old-vs-fixed behavior (adjacent-jornada correlation
    >0.7 required; the old formula gave 0.41). Standings band visibly
    widened after the fix.
  * **Two independent formation-generators had diverged**
    (`season.legal_shapes()` vs `score.formations()`) — the free-tier
    case agreed by coincidence; `score.py`'s 5 premium formations violate
    the bounds `legal_shapes()` derives from. Dormant (nothing requests
    premium yet) but the exact "two authorities, kept in sync by luck"
    pattern this repo keeps finding. One authority now.

### Thread 3: free agents vs. a rival's own player — a real, separate ask (`a4e6d85`, `c250b80`)

Miguel: *"I want to know if any free players are worth buying honestly"*
— the BUY section mixed free-agent and rival-owned targets in one
value-ranked list with no way to tell "nothing free today" from "free
options exist, a bigger raid outranked them." Split into three sections,
same underlying sort, filtered not re-ranked: **BUY** (free agents, with
an explicit "none clear the bar today" when empty), **RAID** (a clause —
cannot be refused), **LISTED** (the owner's own choice — 0/119 real
deals in this league have ever gone that way, stated in the heading
itself). Checked against a real report mid-flight and caught that 3 RAID
rows were actually LISTED — fixed the same day.

### Thread 4: a degenerate squad silently froze its own forecast (`9ee9fdf`, `59076d6`)

Miguel: *"the forecast for Albert is absolutely unsustainable"* — his
standings row was a flat 32-32, 100% P(I finish above him). Root cause:
Albert's real squad has 2 DEF against `SLOT_MIN`'s 3 — `best_xi()`
correctly returns `[]`, scoring him zero forever. First fix (`9ee9fdf`)
just flagged it. Miguel pushed back: *"you're saying he's going to make
30 points and the rest are going to be worth a thousand... that's not
possible."* Proper fix (`59076d6`, his own proposed shape — "assume he'll
field the average player"): `decide.phantom_fill()`, called once in
`load()`, patches any squad short of `SLOT_MIN` with one synthetic
per-missing-slot entry valued at that position's real, per-jornada
AVERAGE across every actual scored player there — not an invented
number, not a specific real player (which would need hiding from the BUY
list). `illegal_squads()` kept as a safety net that should never fire now
— if it does, that's the real signal `phantom_fill()` itself broke.

### Thread 5: a funding chain could make itself illegal (`1ef018d`)

Miguel: *"something wrong in the report"* — Ali Houary's own KEEP-row
band read Season **-1282**, nearly an entire season. `best_swap_for()`'s
chain sold 4 players to fund one purchase; the result met every
position's own `SLOT_MIN` individually but totalled 10 players, one
short of `XI_SIZE` — the SAME "meets every bound, matches no real
formation" pathology as Thread 2/4, this time on a HYPOTHETICAL sale
chain. `_safe_to_sell()`'s docstring already claimed to guard this; it
only checked per-position counts. Fixed by also requiring the squad's
own running total stay ≥ `XI_SIZE`. Got the arithmetic wrong on the
first pass (demanded one player too many) — caught immediately by an
existing test for an ordinary single-swap. Also fixed a related
inefficiency: both funding chains used to spend "legality budget" on
$0-proceeds dead weight for no reason; both now skip it.

### Thread 6 (website repo): the report didn't fit a phone, in three different ways

Miguel: *"the report formatting is weird, table no longer fits in a
single screen width"* — three separate causes, found by actually
measuring in a headless browser at a real 390px viewport (Playwright,
installed ad hoc from cached browser binaries — no project skill existed
for this yet, worth building one) instead of guessing from character
counts:

  1. Markdown-side note text was verbose (`vs X — BUY row below reaches
     him via Y instead`) — shortened twice, ending at a single `*`
     marker with the explanation moved to prose.
  2. The "Where" column's race text (`"Magic Mike 333 · SusoGattuso can
     pay in ~1 day"`) was the REAL dominant width driver on ordinary
     rows, pre-dating that day entirely — `short_manager()` added,
     applied to both halves of the column; phrasing tightened to
     `today`/`~Nd`.
  3. The website's OWN font-size fix (Miguel's idea — "what if we make
     the pix smaller") revealed a THIRD bug once actually screenshotted:
     the browser's auto table-layout let the Season column's long note
     steal width from Player, wrapping "Ali Houary"/"Your eleven" to two
     lines. Fixed with `table-layout: fixed` and explicit column
     percentages, checked with a script scanning every element's
     `scrollWidth` against `clientWidth` — not just the table's own outer
     width, which is what let the wrapped-name bug slip through the
     first "looks fixed" report.

### Thread 7: one column glossary, not two drifted copies (`379a604`, website `7591928`)

Miguel, looking at the live page: *"do we need all that long long text?
shouldn't it go somewhere else?"* Checking surfaced real duplication:
`sim.py`'s `ladder()` carried the full column explanation in markdown
nothing publishes; the website's `Fantasy.jsx` carried its own
independently-worded rewrite, already drifted. `methodology.py`'s
`column_guide_lines()` already existed for exactly this (built
2026-08-22) but was never extended past the retired report.py-era tables
it was built for. Added "The ladder"/"The league table" sections there;
both renderers now link to it instead of carrying their own text.
Verified the link actually navigates and the linked content renders, not
just that the `<a>` tag exists.

## Priorities for next session

### 1. Clause-race risk still isn't priced into the ranking, deliberately

Thread 1's research (preemption games) explains why NOT to fabricate a
win-probability discount — but if real bidding/race-outcome data ever
becomes available (a rival visibly losing a race, logged), revisit
whether the caveat should become a real number.

### 2. `_safe_to_sell()`'s narrower residual gap

Documented in its own docstring: a squad can clear every position's
`SLOT_MIN` AND have `XI_SIZE` total players and STILL match no real
formation (no formation pairs DEF=3 with MED=3, for instance).
Deliberately not fixed — this function is called once per candidate
inside a tight chain-building loop, and a real `best_xi()` search per
candidate was judged not worth the cost for a case this narrow. If it is
ever actually hit (a funding chain producing a real report anomaly the
way Thread 5's did), that is the fix to reach for: call `best_xi()`
itself, the same principle `illegal_squads()`/`phantom_fill()` already
use.

### 3. The `chromium-cli`/Playwright pattern for this box has no project skill yet

Thread 6 needed a real browser to find real bugs (character-count math
was actively misleading twice). Playwright installed ad hoc from
`~/.cache/ms-playwright`'s cached binaries, driven with a
`--host-resolver-rules` trick to satisfy `Fantasy.jsx`'s hostname gate
and a temporary (reverted) `vite.config.js` `allowedHosts` edit to get
past Vite's own host check. Worth writing up as a proper
`/run-skill-generator` skill for this repo pair so the next session
doesn't rediscover it.

### 4. Everything still open from the 2026-08-29 handoff

The interactive what-if picker (raised then, not touched since — still
genuinely open, form factor still undecided: CLI flag vs. a live
phone-side endpoint). `api_key()`'s migration onto `Crosswalk.resolve()`,
`Crosswalk.merge()`'s O(n²) scan, `FIX_BAND`/`HOME_EDGE`/`MIN_POOL` still
guessed, Pedro Diaz/Tete Morente ledger gap — none touched, see git
history if picking one up.

## Standing method (carried forward + today's additions)

  * **A number Miguel pushes back on is worth checking, not defending —
    even (especially) the second time on the same area.** Most of
    today's threads were a direct response to something Miguel read and
    said looked wrong; the swarm reviews (Threads 1, 2) were the only
    self-directed ones, and both were requested outright, not assumed.
  * **Cosmetic fixes and structural fixes are not the same claim, and
    Miguel will catch the difference.** Caught directly this session,
    twice: relabeling/adding a caveat around an unchanged number is not
    the same as fixing the number, and a caveat is sometimes still the
    right call (no data to calibrate a real fix) — but say which one you
    did, explicitly, per item, rather than let a batch report imply they
    were all the same kind of fix.
  * **"Meets every individual bound, matches no real combination" is now
    a THREE-TIME-CONFIRMED failure class in this codebase** (formation
    legality, twice independently, plus the funding-chain total-count
    gap) — see `illegal_squads()`'s and `_safe_to_sell()`'s own
    docstrings. Any new code constructing or evaluating a hypothetical
    squad composition should call `best_xi()` itself, never re-derive a
    bounds/count approximation of legality, even for a "simple" check.
  * **When Miguel narrates the MECHANICS of a problem unprompted — not
    "what should I do" but "here's how this actually works" — he is
    usually pointing at a known class of problem worth researching, not
    asking for an invented heuristic.** Confirmed twice this session
    (clause-race → preemption games; "assume the average player" was
    already the right shape for the phantom-fill design, simpler than
    the specific-real-player alternative first considered).
  * **A "looks fixed" report needs the SAME rigor as the original bug
    report — re-measure, don't re-eyeball.** The Thread 6 width fix was
    declared done twice before it actually was; both times the mistake
    was checking the table's own outer `scrollWidth` (or a screenshot
    glance) instead of scanning every element inside it. The fix that
    actually held was a script checking `scrollWidth > clientWidth` on
    every element, run again after every subsequent change.
  * Everything from 2026-08-25/2026-08-29's lists still holds: check the
    mechanism before trusting a big diff's direction; verify a Monte
    Carlo-sensitive change against `uv run --frozen python`, never plain
    `python3` (no `numpy`, silently different RNG fallback); `git push`
    by default once verified; `git checkout -- data/decisions
    reports/METHOD.md reports/decisions.json` (or `git stash push -u`)
    to keep pipeline-run noise out of a commit.

## Also open, untouched, lower priority

Carried forward unverified — not re-checked this session:

  * `api_key()`'s remaining migration onto `Crosswalk.resolve()`.
  * `Crosswalk.merge()`'s O(n²) identifier scan.
  * `FIX_BAND`/`HOME_EDGE`/`MIN_POOL` — still guesses/blocked on match volume.
  * Pedro Diaz / Tete Morente / Abde Ezzalzouli ledger gaps — small,
    self-resolving, unrelated to Thread 4 (checked: none of the three are
    defenders, so none explain Albert's real squad gap).
