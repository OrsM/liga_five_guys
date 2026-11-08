# liga_five_guys: everything we know, once (2026-11-06)

Merged on 2026-11-06 from `simplify.md`, `model.md`, `plan.md`, `gain.md`, `plan-datasette.md`, `next-steps.md` and the working
agreements in memory. Each section says where it came from `[source]`. Nothing is deleted yet: the last section lists
what this draft leaves out and why. Review it; what you cut here is cut for good once the old files go.

**The aim:** finish first in the league. The tool is a decision aid: it ranks moves, builds a plan, and a phone page
shows it. You act on the account; the tool never does.

---

## 1. The rules the plan lives under [model]

- Points go to the owner only if **cash ≥ 0 at the lock** and with a **legal eleven** (one of `rules.SHAPES`: one
  keeper, 3–5 defenders, 3–5 midfielders, 1–3 forwards; an empty slot scores nothing).
- **The eleven is frozen at the jornada's lock, for every player.** A move counts for the jornadas that lock after it
  is made (`rules.in_force`). Cash must be ≥ 0 at each lock, counting the moves made by it. [simplify, memory]
- A bid may total cash plus `DEBT_SHARE` (20%) of team value (`rules.bid_limit`): a second check, on a value that
  moves daily. Bids are checked against it when placed.
- The squad holds `SQUAD_MAX` = 24, **bids counted** (the app refuses more).
- The game offers only for **listed** players, one offer at each daily close; an offer expires at the next close.
  Unlisted players have no sale until listed (list before the last close before the lock, or there is none).
- A buy whose auction closes after his club's kickoff plays none of that jornada (`late_arrivals`).
- Clauses are paid at once; no protection after a transfer; one shield a matchday. Not modelled: rivals taking your
  players (exposure), `buyout_until`, `shielded`.

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
after the lock is possible, what the 100k cash bumps are, bonus income.

## 2. What the machine models, and the measured facts [model, plan-datasette]

**State, derived and never stored twice:** the squad (owner = me), cash (the ledger's fold), the eleven (the best legal
eleven of the fieldable squad), a manager's standing (points of his squad). "Now" is an argument (`look.cut`), a
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

## 3. Architecture: five modules, one decision [simplify]

Each module reads the facts and earlier modules' contracts only, owns its tables and views, and is judged by its own
test and measure. Every contract is a SQL table or view; the decision's search is one pure Python function that reads
rows and writes plan rows.

| # | Module | Contract | Measure |
|---|---|---|---|
| 1 | Ingest | facts: player_club/value/status/points, club_fixture, owners, cash, listings, offers, auction closes (timestamped) | parser health |
| 2 | Forecast | `cell(player, jornada, pts, p)` from facts at t | RMSE and r, next jornada and five ahead |
| 3 | Decision | the plan, kept per run: order, cash after, `due`/`why`, blockers on two futures, what skipping each step leaves | checks: cash ≥ 0 at the lock, squad ≤ cap, legal eleven |
| 4 | Board | `board_json` (the phone's JSON) | correctness only |
| 5 | Grading | replays the same views at a past t | its own |

**Rules.** A module reads ingest's facts, t and earlier contracts; never a later module, never a scraped table. A test
seeds its module's inputs as rows (no Python mirror of a table). A change is judged by its module's measure; old
output is a score to beat, not to reproduce. `tools/frontiers.py` (run by `tools/selftests.sh`) enforces the exports;
`python tools/frontiers.py --table` regenerates them.

**The dictionary:** each concept has one home; a concept with two homes, a layer reaching past the one below, or a
concept held as mutable state is a fault. Layers: facts → league at t → forecast → decision → grade/board.

**Storage.** SQL owns the facts and every calculation across many rows; Python owns only parsing HTML, the season
simulation and the plan search. One SQLite file `data/lfg.db`, rebuilt from `data/raw`; Datasette on it; saved queries
in `datasette.json`, `tools/q.py` runs them in a terminal. Money is whole euros as INTEGER; SQLite SUM is compensated;
golden compares decisions exactly and floats with `isclose`. Pages stay one renderer (`Fantasy.jsx`).

## 4. The board: cash, offers, funding a buy (2026-11-08)

The long-horizon planner, the season simulation (`sim.py`, p_win, standings), the plan board and the line-up reminder
were removed on 2026-11-08 (tag `pre-chop-2026-11-08` has them). What is left is what a decision needs:

- **`board.py`** (stage `board`): `decisions.json` from the league at t: `cash`, `can_bid` (cash plus 20% of team
  value, less pending bids: `rules.bid_limit`), `lock_at`, the squad with its best eleven for the next jornada, `do`
  (one `list` line for your players not yet on the market, which `act.py` does from the phone), `sells` (each standing
  offer on your players ranked by the points the best eleven loses per million it raises: next 4, next 8, season), and
  `buys` (`decision.opportunities`: the players on the market who add points to your best eleven, most first; the bid is
  `decision.recommended`, the least bid as likely to win as any; the first three carry `funding` from `approach.approach`:
  the sales tonight, the margin under the bid limit, the sales if won, the cash if lost, the points the sales cost, the net gain). `finish` and `band` are
  placeholders the page still reads.
- **`eleven.py`** (pure, no database): the eleven is fixed, never searched by formation. `best_eleven` builds the best eleven of
  a squad by a value (a player's e over the jornadas that count) and settles it (`settle`: a bench player who improves it
  swaps in, until none does); `swap` is "does this player improve my eleven, for whom, by how much"; `remove`/`remove_all`
  is what selling costs (the cover from the bench, then settled); `removal_cost` the one-swap cover. A squad with no legal
  eleven answers `None`. Checked against brute force over every eleven.
- **`approach.approach(club, player, bid, win, value)`**: how to get a buy: the sales tonight on the standing offers (they raise
  cash and the debt limit, `rules.bid_margin`), the sales after the auction by the lock (`rules.cash_at_lock`; the price of a
  player with no offer is `SALE_ESTIMATE` x his value, and the lock margin is also shown with those sales `SALE_SD` lower),
  the offers to hold, the cash left if the bid is lost, and the net gain: the win-weighted points the buy adds less the points
  the sales cost (by `eleven.remove_all`). No cap on the sales; squad max counts pending bids; a legal eleven always remains;
  checked against brute force on small leagues.
- **`decision.read_inputs`**: the league at t as `dec_*` rows and the cells; `cells_of`, `window_value` and `opportunities`
  (each market player by `eleven.swap`). Nothing else of the old decision survives.
- **Taps:** only `list` is done from the phone (`act.py`; an already-listed player, error 030.01.52, counts as listed).
- **Not true any more:** the recommender, `plan_rows`, `worth_on`, the ledger and sale due times, `sim_*` and
  `decision_*` tables, the hold, the line-up reminder (`remind.py`, `lock_due`, `lineup_alert`).

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
   value, sd 0.057, so the lock's cash check rests on that ratio). `proceeds = max(offer, waiting)` merges the two.
   Open: price the lock's risk from the offer ratio's spread, as a constraint, not a second objective.
2. Unlisted players have no sale until listed.
3. The cap and the debt limit count pending bids; `after()` checks the cap on won players only, at the end.
4. Squad after moves is written four times (plan loop, `after`, recommender, `dec_squad`); running cash twice, tied by a
   property test.
5. `nights_left` is one aggregate for all sales; per-player expiry (`offers.expires_at`) must replace it.
6. No epsilon on `free < 0`.

## 7. The list: what to do, in order [next-steps]

Rule for each: the page decides nothing; any new number is a column from Decision.

**Now: what wins the league**
1. The plan block as one compact table (one row per move: action and price, points, "plan without it" column with red
   blocker, bid chips, facts behind a tap; head as one dense line). Points only: chance of first
   per move was considered and dropped 2026-11-06 (too complex, forecasts too noisy). Check with `shot`.
2. ~~Confirm sheet (U9)~~ done 2026-11-06: the confirm says points and cash after, from the board's numbers.

**Then: make the plan more right**
4. ~~Offer-lapse risk (6.1)~~ decided 2026-11-06: accepted, no haircut (a 5.7% wobble on one small sale is below forecast noise). Revisit only if a lock actually fails on it. Lost bids and the cap are already handled in `worth_on` and `after`.
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
- Clean up scratch copies (DB copies, patches) once used; never leave them in `data/`.
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
