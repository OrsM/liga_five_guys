# liga_five_guys: everything we know, once (2026-11-06)

Merged on 2026-11-06 from `simplify.md`, `model.md`, `plan.md`, `gain.md`, `plan-datasette.md`, `next-steps.md` and the working
agreements in memory. Each section says where it came from `[source]`. Nothing is deleted yet: the last section lists
what this draft leaves out and why. Review it; what you cut here is cut for good once the old files go.

**The aim:** finish first in the league. The tool is a decision aid: it ranks moves, builds a plan, and a phone page
shows it. You act on the account; the tool never does.

---

## 1. The rules the plan lives under [model]

- Points go to the owner only if **cash ≥ 0 at the lock** and with a **full legal eleven** (one of `rules.SHAPES`: one
  keeper, 3–5 defenders, 3–5 midfielders, 1–3 forwards). Fewer than 11 players, or a shape that is not legal, scores
  nothing for that jornada: there is no partial eleven (`eleven.legal`, `eleven.points` is `None` then).
- **Everything freezes at the start of the jornada: its first kickoff of any match, the same moment for every player**
  (`season_facts.jornada_calendar`'s `lock`, the one owner). A move counts for the jornadas that lock after it is made
  (`rules.in_force`); a player who joins after the lock cannot play that jornada, one sold after it still scores it. Cash
  must be ≥ 0 at each lock, counting the moves made by it; if not, the whole jornada scores 0. [simplify, memory]
- The objective is expected points (not the chance of finishing first). Placed bids can be raised, lowered or withdrawn.
- A bid may total cash plus `DEBT_SHARE` (20%) of team value (`rules.bid_limit`): a second check, on a value that
  moves daily. Bids are checked against it when placed.
- The squad holds `SQUAD_MAX` = 24, **bids counted** (the app refuses more).
- The game offers only for **listed** players, one offer at each daily close; a game offer expires at the next close; a rival's direct offer lasts 48 h, and every pending offer counts
  (`decision_facts` takes the highest).
  Unlisted players have no sale until listed (list before the last close before the lock, or there is none).
- A buy whose auction closes after the jornada's first kickoff plays none of that jornada (`late_arrivals`, from the same lock).
- Clauses are paid at once; no protection after a transfer; one shield a matchday. Clause buys of rivals' players are
  not used and not priced for now. Not modelled: rivals taking your players (exposure), `buyout_until`; the shield flag
  (`playerTeam.isShielded`) is dropped by the parser and was never true in 302 sampled rows.

**Clocks.** Price changes once a day at the first morning snapshot (verified on 20,498 of 20,498 player-days).
Offers are created at the daily close, 22:24 CET; auctions close 22:24:00. Forecast, fieldable and points change per
jornada; the **lock** is the checkpoint.

**Moves and when they take effect** (verified in the data):

| Move | Window ends | Effect |
|---|---|---|
| bid | the auction closes | won: cash leaves and the player joins within one snapshot of the close |
| sale to an offer | the offer expires (the next close) | pays at once |
| list | now | the first offer comes at the next close |
| field | the lock | at the lock |

Not established: whether the game ever buys an unlisted player itself, a bid's tie-breaks, whether a line-up edit
after the lock is possible. Each jornada pays 100,000 x the team's points about 04:40 Madrid after its last match (section 3b);
the further +100k or +200k credits on most days are a daily bonus claimed by hand in the app, not a model input.

## 2. What the machine models, and the measured facts [model, plan-datasette]

**State, derived and never stored twice:** the squad (owner = me), cash (the ledger's fold), the eleven (the best legal
eleven of the fieldable squad), a manager's standing (points of his squad). "Now" is an argument (the cut), a
backtest is the same expression at an earlier t, "what if I had moved otherwise" is a different input, not a changed
world (after SICP). [simplify]

**Offers (my listed players, 25 Aug – 5 Nov):**
- Every listing leads to an offer, one at each close while listed. (A first reading, "49% of nights", counted 3,425
  empty polls; retracted.)
- offer / value: mean 0.997, sd 0.057 (0.87–1.12), noise around 1.0, not player-specific.
- Next night's best offer against this night's: +0.006 of value, sd 0.073: waiting for a better offer is worth nothing
  on average; its risk is the amount, not whether one comes.

**Price rule** (34k player-days): a smoothed trend of the last three updates (weights .4/.4/.2), not the last one.
Ownership, recent points and value level add nothing. MAE h=1 0.73 → 0.58. [plan-datasette 5.6]

**Forecast:** each player's cell per jornada (`pts` if he plays, chance `p`) and his season; beats a plain average
(error 2.78 vs 3.05, r 0.58 vs 0.50). `SHRINK_K` 4 → 8 (better in 4 of 4 jornadas; p_win moves with the plan
unchanged). Availability constants: nothing to gain (tenth of a percent). The Python tier is not where the time is: do
not port it to SQL for speed.

**Rivals:** they buy what the model likes (117 purchases, forecast at the 0.80 percentile) but do not outscore their
static squads (−3.6 a jornada): no drift to fit; competition for targets is already in the bid-premium rule.
Difference between you and a rival is calibrated (z sd 0.94); single totals are over-dispersed (a jornada moves every
squad together). `p_win` itself is not validated (one season, one outcome). [plan-datasette 5.7, 6.1]

**Evidence limits:** the evidence is the outcomes (player points in a jornada: ~4,655 next-jornada outcomes over 7
jornadas, +~700 a week), not the number of snapshots replayed. A 0.01 rmse difference is not resolvable (paired 95%
interval about ±0.18 in squared error). Only the flagged-player gain is established (+0.289, n=601). Every claim is
made with a paired interval or not made. [plan-datasette 5.1a]

## 3. Architecture: five layers, one sweep [simplify]

Each layer reads the facts and earlier layers' exports only, and is judged by its own test and measure.

| # | Layer | Contract | Measure |
|---|---|---|---|
| 1 | Ingest | observations: every `src_*` row typed and read once; the facts of a cut (squads, cash, ledger, listings, offers, auction outcomes, prices, calendar) | parser health |
| 2 | Forecast | `forecast_facts.cell(player, jornada, pts, p)` and the price fold, from the facts at a cut | RMSE and r, next jornada and five ahead |
| 3 | Decision | the opportunities, the sales and the funding of a buy at a cut; the win chance is the fixed table `ffcore/auction_table.py` | checks: cash >= 0 at the lock, squad <= cap, legal eleven |
| 4 | Board | `decisions.json` (the phone's JSON) | correctness only |
| 5 | Grading | replays the same sweep at past cuts | its own |

**The sweep.** `loader.load` reads storage once (observations, identity, league, knobs, earlier opportunity records)
into a `Loaded`. `state.world_at` (or `state.sweep` over many cuts) folds the observations as of a cut into a `World`: each fact group registers its own fold
(`state.register`) in its module, and a fact is a selector over the World. `decision.decisions(loaded, cuts)` (also
`replay`, `decision_at`) decides at each cut; a live run is one cut. Where the facts live, one module per group:
`current`, `season_facts`, `ledger`, `auction_facts` (ingest), `price_fold`, `forecast_facts` (forecast),
`decision_facts`, `decision`, `auction` (decision), `board`, `grading`/`salegrade`. Replay cuts are the app's snapshot
stamps (`loader.replay_cuts(obs, since)`). The sweep is the one place a derived fact is computed: no SQL view
computes one, and a fact is never stored twice.

**No SQL (#80, step 6):** the raw observations are the parse cache (`data/tidy/parsed.jsonl`, `snapindex.json`, read by
`ffcore/parsed.py`) and the records (`runs`, `run_measures`, `params`, `measure_log`, `grade_cell`, `auction_calibration`,
`auction_brier`, `dec_opportunity`) are the record store (`store/`, `ffcore/store.py`). `tools/peek.py` looks at a store. Nothing in the code opens a database: the old `data/lfg.db` is an archive.

**Rules.** A layer reads ingest's facts, the cut and earlier layers' exports; never a later layer, never a raw table.
A test seeds its module's inputs as rows (no Python mirror of a table). A change is judged by its layer's measure; old
output is a score to beat, not to reproduce. `tools/frontiers.py` (run by `tools/gate`) checks the import graph:
a module may not import a later layer; `python tools/frontiers.py --table` prints the layers.

**The auction table.** The win chance of a bid is a fixed `Model` in `ffcore/auction_table.py`, fitted once over every
closed auction with the fixed cuts `auction.FIXED_CUTS`. It is regenerated only by `tools/auction_table.py`, and the
goldens are blessed in the same commit (it is an input of every golden decision).

**Checks.** `tools/gate --changed [BASE]` per commit (ruff, frontiers, mypy and the suites that import a changed module; full when it
cannot map) and `tools/gate --full` before a merge (ruff, frontiers, mypy and one suite per module with a top-level `_selftest`
under `src` and `tools`, in one 4-wide pool; the count is whatever it prints); run it bare, it takes the lock and the memory cap and prints the flock wait,
the run time and the peak memory. `tools/golden.py` (`--selftest` on the fixture cuts, `--live check` on the 5 live cuts of a parse
cache; neither is in the gate; a change to a golden file is `golden.py bless`, with the reason in the commit);
`tools/lfg-rules` (the sale-rule table over every cut, on a scratch copy of the parse cache, about 3 min);
`tools/lfg-dry-run --compare <LFG_NOW>...` (this tree against a cached baseline of main on each pinned cut: field diff of
`decisions.json` and both board times, about 25 s for two cuts). The board must not get slower: it took 10.1 s before the one valuation (V per
jornada) and about 10.6 s after it, on a busier box, which was accepted; a slowdown past that is a regression to profile before
commit. Time one run per cut and repeat only if the difference is above 0.3 s; the lock wait is reported apart from the run, a slow run
behind another job is not a slow change. Every other heavy job goes through `tools/locked-run CMD` (the lock and a memory-capped
systemd scope, `LFG_CAP` to change the 1500M default), at most two at once. The git tag `oracle-sql` is the last SQL-view implementation,
kept as the historic proof; it is not run any more.

**The dictionary:** each concept has one home; a concept with two homes, a layer reaching past the one below, or a
concept held as mutable state is a fault. Layers: facts -> forecast -> decision -> board/grading. The parse cache
rebuilt from `data/raw`, the record store beside `data/`; `tools/peek.py` looks things up. Money is whole euros as INTEGER. Pages stay one renderer
(`Fantasy.jsx`).

## 3b. The bonus credit (#139, #140, 2026-11-22)

After each jornada the game pays every manager `inflow.BONUS_PER_POINT` (100,000) times the points of his eleven's jornada score, at
04:40 Madrid on the morning after the jornada's last match (10 of the 12 credits seen fit; jornadas 1 and 6, with postponed matches, were
paid earlier than their last kickoff, so the rule undercounts there), and it is part of cash once credited. `inflow.Inflow` is the
credit due after the cut and by the next lock: its expected (points so far from the standings' `fixture_points` plus the forecast of the
fielded eleven's players whose club kicks off after the cut), its low case (10th percentile; spread `JORNADA_SD` = 12 points over a whole
jornada, scaled by the square root of the eleven's share still to play) and `inflow.risk_of`. `Club.inflow` enters `rules.cash_at_lock`:
a plan is feasible when cash at the lock is 0 or more at the EXPECTED credit; the board flags the risk (`risk` on `base` and the `do`
lines: low_inflow, p_short, shortfall_if_low, below_points) and says it in the `why`.

### The cash cover under a lock (#146, #139)

With cash below 0 and a lock ahead the first `do` line is the `cover` (`approach.secure` on the cheapest plan `approach.plan`): the
sales, each with its terms (accept now at the standing offer before the next close, or wait and accept at least `floor` by the last
close), `p_ok` the chance cash at the lock is 0 or more and `at_stake` the points the eleven scores in the jornada that locks, all of
which a lock with cash below 0 loses. A sale waiting for an offer is drawn once at SALE_ESTIMATE times worth with SALE_SD of worth for
spread, the offers independent (a lower bound: the closes before the last give more draws); a standing offer taken now is certain; the
credit is normal with its own spread. A standing offer is taken now when that raises the chance. The set grows by the cheapest cover of
0 to 3 spreads of margin when that lowers points lost plus `at_stake` times the chance of failing. Every sells row then carries an
`action` (accept, wait or keep, with why). With cash 0 or more nothing changes.

## 4. The board: cash, offers, funding a buy (2026-11-08)

The long-horizon planner, the season simulation (`sim.py`, p_win, standings), the plan board and the line-up reminder
were removed on 2026-11-08 (tag `pre-chop-2026-11-08` has them). What is left is what a decision needs:

- **`board.py`** (stage `board`): `decisions.json` from the league at t: `cash`, `can_bid` (cash plus 20% of team
  value, less pending bids: `rules.bid_limit`), `lock_at`, the squad with its best eleven for the next jornada (`board.best_without`:
  the squad less the plan's sales and the rule's sells, reached by swaps from the app's eleven; the `lineup` advice is those
  swaps, never naming a sold player, and `lineup.stale` says the app's eleven is not that best), `do`
  (one `list` line for your players not yet on the market, which `act.py` does from the phone), `sells` (each standing
  offer on your players, judged by `worth.rent` at LAMBDA: `verdict` sell (the offer reaches `reserve`, `hurdle.reserve` of
  the player's `hurdle.Stake`: (W + rise x value) / K, K = 0.85, his yield, the points the sale loses plus LAMBDA x n x his
  expected price change over his Exit's horizon (the price forecast's furthest row, capped at +-3%), per million of the offer
  per jornada, being under K x LAMBDA; and for a sale that loses no points at least the proceeds of his Exit stopped at the
  closes left (`worth.stopped`: what waiting for the best offer is worth); a sale that loses points also needs a use for the
  cash, a buy it funds tonight or the lock cover), wait (a sell whose offer is under 1.0 of value and the 0.5 percentile
  and funds nothing tonight: waiting is free), hold, or needs_replacement; `points_lost`, `rent` (the points the money
  earns at LAMBDA over n), sells first, then by the points the sale loses per million it raises; each row typed: `delta`
  (`points` over the season, `window` over lock, next4 and season, `gap` the jornadas left without a legal eleven; all
  null when the squad after the sale has no legal eleven), `feasible`, `why_infeasible` (`"no_eleven"`), `facts` (a list of
  `[label, value, kind]`, the page reads fields never prose), and `replacement` for a sale that leaves no eleven: the best
  pair (sell him, buy a player of the market) by `Approach.net`, funded by `approach.replacement`, or null with
  `replacement_why_not` (`no_cash`, `no_candidate`); a sale's Delta is 0 or less), and
  `buys` (`decision.opportunity_rows`: the players on the market who add points to your best eleven, most first; a listing is
  bought by `hurdle.bid`, the most win-weighted surplus under the walk-away price `hurdle.cap` of the row's `stake`, K x P / (n x LAMBDA), K = 0.85 (no surplus,
  no bid: the same band K as the sale's) (`walk_away`, `yield`, `n` on
  each row; a listing with no bid and none placed is counted in `considered`; a placed bid above the walk-away is `over`);
  LAMBDA is `hurdle.lam`, stated as `lambda`; the first evaluated buys carry `funding` from `approach.approach`:
  the sales tonight, the margin under the bid limit, the sales if won, the cash if lost, the points the sales cost, the net gain). `finish` and `band` are
  placeholders the page still reads.
- **`eleven.py`** (pure, no database): the eleven is fixed, never searched by formation. `points(squad, pos, value)` is V,
  the points of the best legal eleven of one jornada (the best shape's top players of each position summed), `None` when
  no legal eleven exists (fewer than 11 players or no shape fits; that depends on the positions alone, never on value).
  `best_eleven` builds who plays (`build`) and settles it (`settle`: a bench player who improves it swaps in, until none
  does); `swap` is the label "does this player improve my eleven, for whom, by how much". `best_total` is the brute-force
  oracle over every eleven, used by the selftests only. Nothing else values a squad: the old `remove`, `remove_all`,
  `removal_cost` and `worth.lost_points` are gone.
- **`valuation.py`** (decision layer): the one valuation of a move (`docs/notes/one-valuation-of-a-move.md`). A `Move` is the
  players sold and bought, each side with the game-clock moment it is made; `squad_at(squad, move, jornada, locks)` is the
  squad that plays a jornada (each side only if `rules.in_force` counts its moment); `Value` is V summed over a `Window`
  with `gap` the jornadas that have no legal eleven (they score 0); `Delta` is the Value after the move less the Value
  without it (`value_of`, `delta`). A `Window` is the jornadas a move counts for, from `rules.in_force`; its readers
  `lock`, `next4` (`NEXT_JORNADAS` = 4) and `season` are prefixes of that one window, and the decision value is the
  season. `read` gives a `Reading` (`points`, `window` over `READERS`, `gap`) and is the one reading of a sale;
  `Buyer.gain` the gain of buying one player over one squad. V is taken per jornada (the eleven is re-picked at each
  lock), not once for the summed forecast. Checked against brute force.
- **`approach.approach(club, player, bid, win, rounds)`**: how to get a buy: the sales tonight on the standing offers (they raise
  cash and the debt limit, `rules.bid_margin`), the sales after the auction by the lock (`rules.cash_at_lock`; the price of a
  player with no offer is `SALE_ESTIMATE` x his value, and the lock margin is also shown with those sales `SALE_SD` lower),
  the offers to hold, the cash left if the bid is lost, and the net gain: the win-weighted points the buy adds less the points
  the sales cost (`Approach.gain`, `cost_won`, `cost_lost`, `net`, all V differences from `eleven.points`). `Rounds` holds
  the forecast cells, `now` (the jornadas a sale made now counts for) and `arrives` (those the won buy counts for; `before`
  is the difference): a sale tonight is gone from the squad in every jornada of `now`, so a sale before the lock that is
  covered only after it costs the jornada between. `approach.Side` is only a memo of V per jornada over subsets of one
  squad (a set of players is a bitmask); a set that leaves no legal eleven costs `None`, and `approach` skips it. No cap on
  the sales; squad max counts pending bids; a legal eleven always remains; checked against brute force on small leagues.
  `approach.replacement(club, sold, player, bid, win, rounds)` is the pair that sells a player whose sale leaves no eleven
  and buys one: a jornada with no legal eleven scores nothing and the cost counts it (`Side.forgone`).
- **`decision.inputs_at`**: the league at a cut as the typed `Inputs` and the cells; `cells_of`, `lock_times`, `window_of`
  (the Window of a buy at its close), `window_value` and `opportunity_rows`
  (each market player by `valuation.Buyer`); `decision_at`, `replay` and `decisions` run it at one cut or over many. Nothing else of the old decision survives.
- **Taps:** only `list` is done from the phone (`act.py`; an already-listed player, error 030.01.52, counts as listed).
- **Listing is automatic:** `lfg-list` (timer: every 5 min and 22:19 Madrid) runs `act.py ensure`, which lists every squad player without a listing (read from the app's `teams` and `market`; one POST `market/sell` each, at max(value, the app's now)) and logs to `~/.local/state/lfg/list.log` only when it listed or refused something; the board's `list` line then appears only when that has failed.
- **`dec_offer`** (the record of each standing offer at a run's cut, version 2): `delta_next4`, `delta_season` (the
  sale's Delta, 0 for a bench player, null when no eleven remains), `fields` (the squad after the sale fields a legal eleven),
  `gap`; version 1 rows (`lost4`, `lost_season`) are read as v2 through `records`. Grading leaves out, by
  `grading.sound_offers`, the rows without `fields` from `grading.FABRICATED_FROM` (2026-11-20T19:40Z, when a squad of 11
  with no bench was given the whole eleven as every player's loss) until the first row that has `fields`; the store is
  not edited, and `health` prints the number excluded. Grading does not read `dec_offer` yet, so `sound_offers` is
  exposed and unconsumed.
- **Not true any more:** the horizon of 8 jornadas (`board.AHEAD`, `worth.PER_MILLION_AHEAD`), the sale note strings
  (`offer_note`), the `INF` cost, `eleven.remove*`, `worth.lost_points`, the recommender, `plan_rows`, `worth_on`, the ledger and sale due times, `sim_*` and
  `decision_*` tables, the hold, the line-up reminder (`remind.py`, `lock_due`, `lineup_alert`); since 2026-11-22 (one
  exchange rate, `hurdle.py`): `rules.BID_TARGET` and `auction.bid_for` (the 0.90-chance bid), `worth.points_per_million`,
  the r06 rules, `bench_option`, `STOPGAP_RATE`, `r_estimate`, `best_yield`, `grading.buys_at`, `market.py` (PAR, kappa,
  the graded replacement level), `tools/market-grade` and the `market` key of decisions.json.

Sections 5, 7 and 8 describe the planner removed on 2026-11-08; they are kept as the record of why it was built.

## 5. The reasoning behind the plan [plan, gain]

Kept because it explains why the code is shaped this way; the code that implemented it was deleted.

- A player is worth what he adds to the eleven: F(S+p) − F(S), value over replacement, with values interacting (a
  second keeper adds almost nothing, a keeper to a squad without one a whole slot). So moves are valued as a set
  against the slots, never one at a time and summed.
- **Money has a price:** maximise F(S) + V(cash left) subject to cash ≥ 0 at the lock. With the budget priced by λ
  (the shadow price, points per million), a buy is worth it if its points over replacement beat λ × cost; a sale if the
  points lost are under λ × what it raises. λ moves daily: high in debt, low with cash and nothing worth buying.
  Earlier stand-ins for λ (`MONEY_RATE`, the 70% bar, `cheapest_cover`) were deleted. What stays a judgement is V, the
  value of money at the horizon's end.
- **A bid is a lottery:** worth = P(b) × (value over replacement − λ × b); the best bid is below what he is worth by
  an amount the odds set (shading). Several bids are a portfolio: the budget must hold on every outcome.
- **Chance of first:** maximising it is a variance sweep (Hunter, Vielma & Zaman); tried and removed 2026-10-31
  (κ = 0 alone scored best).
- **Nights before the lock** are planned by re-solving at every run (rolling horizon), not by one program over scenarios;
  revisit only if the backtest shows money left on the table between runs.
- **Closed-form gain:** a move's gain has mean Σa and variance `(1+share²)Σv + share²Σa²`; the chance is the normal's.
  Standings use the same normal. Checked against 3000 simulated seasons: same verdict on 140 moves; p_win within 0.007.
  Both share the assumptions (independent players, one persistent error each), so neither fixes p_win's overconfidence.
- **Tried and not taken:** a buy funded by several sales costed one by one (476 vs 560: the sum flatters); consolidating
  the bench into a star wins no points on these forecasts; a phantom for every manager (rivals cannot all win the same
  bids); the exact integer program (deleted); porting the forecast to SQL for speed.

## 6. Open model questions (from the stress test) [model]

1. A sale to an offer cannot follow the auctions the same night. A funding sale is on tonight's standing offer (known
   amount, but a player gone for nobody if the bid is lost) or on the next close's offer (certain to come, amount ≈
   value, sd 0.057, so the lock's cash check rests on that ratio). `approach.prices` takes the standing offer, else `SALE_ESTIMATE` of his worth.
   Open: price the lock's risk from the offer ratio's spread, as a constraint, not a second objective.
2. Unlisted players have no sale until listed.

## 7. The list: what to do, in order [next-steps]

Rule for each: the page decides nothing; any new number is a column from Decision.

**Now: what wins the league**
1. The plan block as one compact table (one row per move: action and price, points, "plan without it" column with red
   blocker, bid chips, facts behind a tap; head as one dense line). Points only: chance of first
   per move was considered and dropped 2026-11-06 (too complex, forecasts too noisy). Check with `shot`.
2. ~~Confirm sheet (U9)~~ done 2026-11-06: the confirm says points and cash after, from the board's numbers.

**Then: make the plan more right**
4. ~~Offer-lapse risk (6.1)~~ decided 2026-11-06: accepted, no haircut (a 5.7% wobble on one small sale is below forecast noise). Revisit only if a lock actually fails on it.
5. ~~Search caps~~ measured 2026-11-06 with `_selftest_search(n=1000, seed=7, strict=False)` (3 min): the plan misses the best mix in 1.6% of random leagues, by 1.1 points on average (max 3.0). Left as they are. Board price view dropped 2026-11-06: it would lengthen a page already too long, and its numbers are on the plan rows.
6. Relist against wait; unlisted players' proceeds in the plan.

**Then: know whether it worked**
7. Lost bids told (U7), the cash path against the debt limit (U8), rivals ranked by chance above you (U10), one ping
   with the next deadline (U11), board age against the close (U12), history of bids won/lost (U13).

**Housekeeping, when idle**
8. Recommender cut-offs from data; pin legacy forecast moments; sibling worktrees (`act`, `squad-cap`, `fast-plan`,
   `fast-int`, `move-cost` dirty; `position-baseline` has two parked October commits); stale agent patches in
   `~/.local/share/lfg-stale-patches/`; `late_arrivals` redundancy; grade-stage speed (~250 s).

**Done:** per-row `detail`; offer expiry; the plan in order with `cash_then` and due; blockers; deadline strip; one
`show` formatter; the eleven as a pitch; one `Move` component; the plan on the first phone screen;
the `READ` list so the live plan sees locks, closes and offers.

**Yours, not engineering:** account actions (bids, accepting offers, listing players: the game only offers for listed
players). Market closes daily 22:24 CET.

## 8. The 2026-11-01 "bugs found" list, checked against the code (2026-11-06)

- **Fixed 2026-11-06:** `lfg-watch` took `rerun.json` and `hold.json` with `cat && rm`, so a tap between the two was lost;
  it now `mv`s first (accept was already atomic).
- **Gone from the code, so dropped:** `lineup_alert` vs `field_basis` (`field_basis` no longer exists); money over
  `hmax` vs `MONEY_H` (`MONEY_H` no longer exists; the money replay was deleted); `p_now` behind a jornada (no longer
  in `src`); the backtest never offering listed players (`acquire` is gone with the money replay); `board_json` joining
  a buy's asking and win by name (rows now carry `key`).
- **Fixed earlier, per the notes:** a second league overwriting the first; ruff never run; the backtest's forecast
  differing from the board's; `replay_forecast` keyed by variant not code.

## 9. Working agreements [memory]

- Test and commit as we go; push after a green commit; deploy only through `./deploy.sh` on its own.
- Diagnose the structural problem first (root causes); once a plan is approved, carry it through.
- No duplication: extend the production piece and audit diffs for second copies; one formatter (`show`); no ad-hoc
  scripts with their own matching.
- Design the math before code: state the formulation and agree it before coding or testing.
- Profile before commit: time the board/backtest; slowdowns are regressions.
- Decide on what you optimise: one place, one objective; others only read it.
- Old code is not the spec: design from needs; old numbers are a score to beat.
- The ceiling measure is a smoke test, not a gate: gate planner changes on tests and invariants.
- Untested changes do not go live; the backtest is a noisy smoke test.
- Look for established solutions first (prior art) for common problems.
- Clean up scratch copies (store copies, patches) once used; never leave them in `data/`.
- Property tests; flag duplication preemptively.
- Do not touch the account (bids, accepting offers) without your explicit go. Live runs may be triggered freely.

---

## 10. What this draft leaves out, and why (your call on each)

| From | Left out | Reason |
|---|---|---|
| `simplify.md` | the 140-line "frontiers crossed" list, grouped by file:line | a snapshot of 2026-11-04 ("the state before that slice"); `tools/frontiers.py` prints the live list |
| `simplify.md` | the 19-row "one home" concept table and "plan by layer" | mostly done (rows 1, 3–8 closed 2026-11-04); the principle is kept in §3 |
| `simplify.md` | the forecast attribute table and its measured SQL-vs-Python comparison | implementation detail; lives in `schema.sql` |
| `simplify.md` | "Dead or idle (cleared)" | history of deletions |
| `simplify.md` | the module exports table | regenerated by `tools/frontiers.py --table` |
| `plan.md` | the October design, the 2026-10-28 prototype result, the order list with old backtest numbers | superseded; the reasoning is kept in §5 |
| `gain.md` | the golden-board effects of the SQL port | history; the formula and its check are in §5 |
| `plan-datasette.md` | all phase/slice step logs (S0–S6, 2.x, 5.x, 6.x), timings, golden board counts | done and in git history; findings that still matter are in §2 |
| `plan-datasette.md` | "Later" list: decision journal, never-recommend list, weekly league recap, rival bid prediction | ideas, not agreed work; add to §7 if you want any |
| `plan-datasette.md` | 5.4 champion/challenger, 5.2 phantom coach details | unbuilt / parked (memory has the phantom note) |
| `archive/simplify-log.md` | everything | already an archive |
| memory | kids' games, bandkit, RetroArch, Luanti | unrelated to this project |

**Decision (mine, reverse any):** every row of this table is cut. The six old docs are deleted once you have read
this one; they stay in git history. The "Later" ideas are not in the list because nobody agreed to them.
