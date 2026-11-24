# One valuation of a move: V, Window, Delta (#139, #140, #52, #69; for Miguel's agreement, 2026-11-20)

## Status (2026-11-20)

Steps 1-6 are merged. The rest of the note is the design as agreed, before the corrections below.

- Step 1: `ea340384` (`eleven.points`, `valuation` with Move, squad_at, Value, Delta).
- Step 2: `6c215187` (`approach.Side` is a memo of `eleven.points`; no eleven is None, not INF).
- Step 3: `5708ef21` (one Window: lock, next4, season, from `rules.in_force`; the 8 is gone) and `726b239f` (3b: V per jornada; funding
  sales costed from the moment of the sale).
- Step 4: `0155fc81` (`valuation.read` is the one sale reading; `eleven.remove*`, `worth.lost_points`, INF gone; typed Delta on sells).
- Step 5: `c1eb8652` (facts and replacement pairs on sells; `dec_offer` v2; grading leaves out the made-up rows).
- Step 6: lfg.md rewritten for V and the strict rule.

Corrections found while implementing:
- Sign: a sale's Delta is 0 or less (the points it loses are `-Delta`); a bench sale is 0.
- `squad_at` takes the locks (`squad_at(squad, move, jornada, locks)`), since `rules.in_force` needs them.
- Inside `approach`, `gap` cannot be non-zero for a sale that fields an eleven after it (None is skipped), so gap is only read on the board's
  rows and in `replacement`, where a jornada with no eleven scores nothing.
- A buy's gain is never negative (V of a squad plus a player never falls).
- The board time moved from 10.1 s to about 10.6 s on a busier box, not 10.4 as written in section 5; accepted.
- `dec_offer` is not read by grading, so `grading.sound_offers` is exposed (and `health` prints its excluded count) but unconsumed.

Every name was checked against `src/` on main (2026-11-20).

## 0. Root cause

There is no single typed answer to "what is this transaction worth". The points effect of changing the squad has six routes:
`eleven.swap` (buy gain, `decision.opportunity_rows`), `eleven.remove` / `remove_all` / `removal_cost` (sale cost; `remove` is
read only by `worth.lost_points`, `remove_all` and `removal_cost` only by tests), `eleven.points_without` / `top_points`
(used by `approach.Side`), `approach.Side.cost_of` (its own bitmask DP, `INF` = no eleven) and `worth.lost_points` (its own
fallback: `remove` None becomes `eleven.total` of the whole eleven, the same number for every player of a squad of 11, #sells bug).
Windows are chosen in four places: `board.AHEAD = (4, 8, 0)` (via `decision.window_value(js[:n or len(js)])`), `worth.PER_MILLION_AHEAD = 8`
and the horizons it indexes, `window_value(where)` with `where` from `rules.in_force` (`opportunity_rows`, `board.buy_lines`),
and a fourth in grading (`grading.horizon_jornadas`: lock, next4, season). `board.offer_note` writes the numbers as prose and
the page strips phrases from it (a string contract; the page is not in this repo, I did not find its parser).
Issue 140 item 3 (funding sales costed in the buy's window, so a sale before the lock loses j13 unseen) and item 4 (eight buys each
against the same player, not additive) are the same defect: the thing valued is a transaction, not a row.

Rule (Miguel): everything freezes at the jornada's first kickoff, for every player (`Calendar.lock`); a player who joins after it cannot play
that jornada. Fewer than 11 players, or an illegal shape, scores nothing. Code agrees (`eleven.legal`; `approach.Side.fields`).
`docs/notes/lfg.md` section 1 says "an empty slot scores nothing": contradicted it, fixed in step 6.

## 1. One valuation

Let S be a squad, pos the positions, e_j(k) the forecast cell of player k for jornada j (`decision.cells_of`). For one jornada

    V_j(S) = max over legal elevens E within S of sum_{k in E} e_j(k)      or  None when no legal eleven exists

`legal` is a property of the position counts of S only (`eleven.fits`), so None does not depend on j or on the window.
Implementation: `eleven.points(S, pos, value)`, the max over `rules.SHAPES` of the top-k per position summed (this is
`eleven.build` then `eleven.total`, and `top_points` of `rank`; build is already exact per shape, `settle` adds nothing to a value).
The brief names `eleven.best_total`: that is the brute-force oracle (`itertools.combinations`, C(n,11); not in `__all__`, called by
selftests only). It stays as the oracle, never the production V.

A move is a set sold X and a set bought Y at times (`Move`). It counts for jornada j iff `rules.in_force(locks, j, at)`; so the squad
at the lock of j is `squad_at(S, move, j)`: S - X if only the sale is in force, S - X + Y if both are. Then

    Value(S, move, W) = sum_{j in W} V_j(squad_at(S, move, j))      (None read as 0 for the sum)
    Delta(move, W)    = Value(S, move, W) - Value(S, nothing, W)

sale loss = -Delta(sell X), buy gain = Delta(buy Y), transaction = Delta(sell X, buy Y): one call each, same function. `fields` is
legality of S - X + Y (the squad after everything has arrived); `gap` the jornadas of W scored 0 for lack of a legal eleven (the
squad of 11 that sells before the lock and buys after it loses the jornada in between: counted, not hidden).

Survivors and deletions:
- Public: `eleven.points` (V_j), `eleven.legal`, `eleven.best_eleven` (the explanation: who plays, the swaps), `eleven.swap`
  only as the private label "who he replaces" (its `gain` must equal the V difference, asserted in a test).
- Deleted: `eleven.remove`, `remove_all`, `removal_cost`, `Removed`, `points_without`; `worth.lost_points` (and the `Horizon`/`Context.horizons`
  it reads); `approach.Side.cost_of`'s INF protocol. `eleven.top_points` becomes private to `points`.
- Private memo: `approach.Side` stays only as a cache of V over subsets of one squad (the lane-sharing in `survivors` is its point; the
  search in `cover` and `approach` visits thousands of masks); it returns `Delta` pieces from `eleven.points`, with None where it
  returned `INF`. No second DP formula: the cache key is a function of the survivors, the value is `eleven.points`.
- Tests: `eleven._selftest` keeps the brute-force oracle but checks `points` (not `remove_all`) against `best_total` on random squads
  incl. ties and squads with no eleven; the `remove`/`remove_all` cost-equals-oracle assertions become "Delta(sell X) equals
  V(S) - best_total(S - X)". `approach._selftest` already brute-forces `approach`, `cover` and `settle` over `best_total`: unchanged in
  property, `Side.cost` assertions retargeted to the memo equalling `points`. Added properties: legality is window-independent;
  Delta of a union of sells >= sum of the single Deltas never holds in general (not asserted) but a bench sale is 0 in every window;
  `Delta(sell X, buy Y)` with Y bought in force at every jornada of W equals V(S-X+Y) - V(S) summed.

Per jornada, not per summed value. Today `window_value` sums e over the window and one eleven is picked for the sum; the eleven is
re-picked at each lock, so sum_j V_j >= V(sum_j e_j): the present figures understate a bench option and misstate a sale's cost.
Cost: |W| calls (<= ~25) instead of one, inside `approach`'s search. Decision D2.

## 2. One Window

`Window = tuple[int, ...]`: jornadas to lock ahead, from `rules.in_force`. It is not chosen per field. A transaction's window is
the jornadas with a lock after the moment the first of its moves is made, up to the season's end; the buy's own jornadas are those
in force at its auction close (a market buy closing after the lock does not score the jornada locking tonight; `from_j` today).
Nested prefixes of that one window are only ways to read it: `lock` (first jornada), `next4` (`GRADE_AHEAD`, `grading.horizon_jornadas`
agree on 4), `season` (whole). The decision value is `season`; `next4` and `lock` are shown for risk (forecast sd grows). `8` and
`PER_MILLION_AHEAD` go (nothing grades 8; `worth_rule` and `bench_option` already read `lost[0]` = season); `AHEAD`, `Horizon`,
`Context.horizons`, `board.context`'s loop and `decision.window_value(where)` callers collapse to `window(inp, move)`. `grading`'s
horizons keep their names and meaning (lock, next4, season) and use the same prefix function.
Targets (not on the market: no close) get the next auction close as `at`, so they use the buys' rule (140 item 2), labelled estimated.
Why a sale is valued from before the lock: a sale to an offer pays at once and leaves S - X for every jornada locking before
the replacement arrives; a sale only to cover cash at THIS lock is the same move with Y empty, and its jornadas are those from now.

## 3. The board's output contract

    Delta = {points: float | None, window: {first, lock, next4, season: float | None}, gap: [j...]}
    Row   = {kind: sell | buy | need | target, player(s), Delta, cash_after: float, feasible: bool,
             why_infeasible: null | "no_eleven" | "no_cash" | "no_room", replacement: Row | null, facts: [[label, value, kind]...]}

- `points` is the season Delta (the decision value); `window` the per-prefix split from the same per-jornada series.
- The offer note becomes `facts`, a list of `[label, value, kind]` (the form `board.offer_lines` already emits for its `facts`); `note`
  and `offer_note` are removed, `board.is_bench` becomes `Delta.points == 0 in every prefix`. The page reads fields, never prose.
- Sell with `fields` false: `points: null`, `feasible: false`, `why_infeasible: "no_eleven"`, shown as "needs a replacement" plus
  `replacement`: the best legal replacement transaction (sell X, buy y), y over the market in the positions that make S - X + y
  legal, ranked by the same net Delta (section 4), funded by `approach` (`Approach`: tonight's sales, margin, `lock_margin`).
  A squad of 11 therefore shows pairs, never an identical whole-eleven number.
- `do` / needed rows (`approach.settle`, `board.cover_lines`): the cheapest set raising -cash, valued by the transaction Delta
  (replacement bought included when X leaves no eleven), `cash_after` and `feasible` typed; `short` and `why` as `why_infeasible` + `facts`.
- `cover_lines` and `approach.settle` survive as procedures (cover, in `approach`, is the one search); only their cost function and
  window change. `dec_offer` rows: `lost4`, `lost8`, `lost_season`, `bench` become `delta_next4`, `delta_season`, `fields`, `gap`
  (nullable); `funds` stays.

## 4. U, #52, #69, #140

The bid objective of `bid-and-sell-formulation.md` (branch `form-review`), U(r) = W (G - Cwon - lam M) - (1 - W) Clost, uses these
and no other numbers: G = Delta(buy y; window of y), Cwon = -Delta(sell X_won) and Clost = -Delta(sell X_lost) with X chosen by
`cover`, all `Delta` as defined here; `Approach.gain`, `.cost_won`, `.cost_lost`, `.net` are exactly that pair of V differences
(with gap counted, so `cost_won` is no longer costed in the buy's window only: 140 item 3). U is the one place an optimisation
lives; this note changes its inputs, not its form. lam is `worth.r_estimate`'s rate, in points per million over the same `season`
window, so gains and sells are priced in one unit (D2 there). When y has no `win`, W = 1 only for a replacement shown at its ask.
#140 item 1: buys and targets are ranked by net = `Approach.net`, `Delta` of the pair; `gain` is the fallback when no funding doc is
computed (cheap rows with no sale need none: net = gain). Compute `approach` for the top N by gain (as `TOP_FUNDED`) and the
ranking is by net within that set; rows beyond it are labelled "gain before sales". #69: the bench verdict reads `Delta == 0`
over `season` (and `next4`), the reserve reads `Delta.points` of the sale; no other horizon.

## 5. Migration (small steps; each is green alone, merged under the run lock; scratch under ~/.cache, never data/ or store/)

Verify set V*: `tools/capped bash tools/selftests.sh`; `tools/capped ~/.local/bin/uv run --frozen python tools/frontiers.py` (0 crossings);
`tools/capped env PYTHONPATH=src ~/.local/bin/uv run --frozen python tools/golden.py --selftest`; `LFG_NOW=<pin> tools/lfg-dry-run board`
(decisions.json diffed, time not slower than 10.4 s).
1. `eleven.points` and the oracle selftest; `Delta`/`Value`/`squad_at` (new module `valuation.py` in the decision layer, imports `eleven`,
   `rules`). No caller changes. V*: byte-identical decisions.
2. `approach.Side` over `eleven.points`, INF to None; `cover`, `settle`, `approach` unchanged in output. V*: identical; profile `approach`.
3. `Window` from `rules.in_force`; `board.context` and `buy_lines` take it; `AHEAD`, `PER_MILLION_AHEAD`, `window_value(where)` callers go.
   Moves goldens (the 8 window is gone; funding sales are costed from the sale moment): `golden.py bless` with the reason.
4. Delete `worth.lost_points` (sell rules read `Delta`), `eleven.remove*`; sells rows get `Delta`, `fields`, `why_infeasible`. The
   `0.0 / whole eleven` fallback disappears here. Test: a squad of exactly 11 gives `points: null` for every sell, never equal values.
5. `facts` replace `note`; `offer_note`/`is_bench` deleted; `dec_offer` kind changed (new fields, nullable, schema version raised;
   old rows stay readable). Add `replacement` rows (pairs) from `approach`.
6. `lfg.md` rewritten for V and the strict rule; `bid-and-sell-formulation.md` section 1 points at this note (U reuses `Delta`).
Grading of stored rows: the `dec_offer` and `sale_score` rows written since 2026-11-20T19:40Z by runs with a squad of 11 and no
bench hold lost4 = lost8 = lost_season = the whole eleven for every player (fabricated, not a measure). `grading` excludes them by a
named constant (`FABRICATED_FROM = "2026-11-20T19:40Z"`) up to the first cut that carries `fields`, by predicate on the cut, never
by editing the store (principle: `store/` is read-only to agents); `health` prints how many rows were excluded. The replayed
`rule_scores` recompute from code, so the fix applies to them with no data change.

Not in scope: the forecast (#57), the auction curve (#49), the bid grid and `BID_TARGET` (#52 builds on this), accepting, listing or
bidding, SQUAD_MAX, the page's own code (it must read the new fields; coordinated, not edited here), rival squads, the sell-reserve
optimal stopping (#69).

## 6. Checked against the code (corrections to the brief)

- `eleven.best_total` is the brute-force oracle, not production; V is `build` + `total` (exact per shape).
- `approach.Side.cost_of` already is a V difference (`points - top_points(survivors)`), memoised; the duplication is the INF protocol
  and `worth.lost_points`/`eleven.remove`, not a second maths.
- `remove_all` and `removal_cost` have no production caller; `remove` has one (`worth.lost_points`).
- A fourth window source exists: `grading.horizon_jornadas`; `board.AHEAD` uses `js[:n]` of all cell jornadas, not `in_force`.
- The sale rules already price at `lost[0]` (season); only `points_per_million` (LIVE) uses 8. The 8 is the page's sort key.
- The page's parse of `note` could not be checked (not in this repo).
- Hidden assumption of today's code: one eleven for the whole window (summed e). Correct is one per jornada (D2).

## 7. Decisions for Miguel

1. Fix = one V, one Window, typed Delta rows with `facts`; delete `remove*`, `lost_points`, `AHEAD`, `PER_MILLION_AHEAD`. Recommend yes.
2. V per jornada (sum of V_j) rather than one eleven for the summed window. Recommend per jornada if `approach` stays within the 10.4 s
   board time (step 2 profile); else keep the summed window behind the same interface and record the understatement.
3. Decision horizon = season for all moves; `lock` and `next4` shown for risk. Recommend yes (drops 8).
4. A sale that leaves no legal eleven shows "needs a replacement" with the best pair; no per-row loss number. Recommend yes. Which
   replacement is "best": net Delta at U's bid (recommend), or lowest ask.
5. Targets valued from their next auction close, labelled estimated (140 item 2). Recommend yes.
6. Rank buys and targets by net where `approach` ran, gain labelled "before sales" elsewhere (140 item 1). Recommend yes.
7. Stored rows since 2026-11-20T19:40Z excluded by a named cutoff in grading until the first row with `fields`. Recommend yes.
