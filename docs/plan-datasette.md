# Plan: one SQLite file, served by Datasette

Agreed 2026-10-11. Each step is one commit, red test first, pushed and
deployed when green.

## End state

**SQL owns the facts and every calculation across many rows; Python owns
only what SQL cannot do: parsing HTML, the season simulation and the plan
search.** (Miguel, 2026-10-11, after "are we using SQL or papering over
previous code?": the first tables only added a write path.)

```
data/raw tarballs
   └─ Python parsers ──INSERT──▶ observation tables (append-only, observed_at)
                                    │
                     SQL views (a .sql file each, :cut for "as of"): current_*,
                     ledger, cash, price_paid, offer stats, premium, momentum,
                     points by jornada, start outcomes, availability counts
                                    │
   Python model ◀── reads views ── shrinkage, calibration grid, season sim, plan
        └──INSERT──▶ funnel, forecast, params, boards, grade_*
                                    │
                     saved queries (the same .sql files): Datasette, ask.py,
                     tools/q.py, the phone's board JSON, grading, drift, alerts
```

- **A slice ships only if it deletes Python**: move one data family into
  SQL, its arithmetic into views, delete the Python, `golden.py check` the
  same. Lines counted after every slice.
- **Equality**: SQLite's SUM is compensated (Kahan-Babuska-Neumaier since
  3.43; the box has 3.46.1), Python sums with `math.fsum`, money is whole
  euros stored as INTEGER, and golden compares decisions exactly and floats
  with `math.isclose`.
- **Box and phone**: Datasette serves `lfg.db` at `lfg.lemonworlds.com`
  (behind the Access wildcard); Run again and the live log move there.
  `lfg-publish` still pushes the board JSON each run, so the last board
  survives the box being down; `Fantasy.jsx` stays the one renderer and
  links into Datasette (as `Band.jsx` does).
- **broom**: `runs` is glance, `params` tidy, `funnel`/`forecast` augment.
- **`lfg.db` is rebuilt from `data/raw`** plus the committed `players.csv`
  and `points.csv`, git and replays; only `runs` history is not. No backup.

## Decisions

| Question | Answer | Why |
|---|---|---|
| Forecast retention | every run | measured 2026-10-11: 91 players x 32 jornadas = 2.8k rows a run (the forecaster covers squads and the market, not all 729), ~3M rows and ~200MB a season; no pruning code |
| Funnel retention | every run | small |
| Telegram ping | stays on the phone | it fires only after a successful publish and links to the phone page |
| Report commits | keep | they are the board history and its backup (8KB, 2s a run) |
| Tidy storage to SQLite | first: the views need their inputs in SQL | the end state above; it was "optional, last" until 2026-10-11 |

## Assumptions that were wrong

- `p_win` cannot be calibrated: one season, one outcome. Calibrate the
  per-jornada points forecasts instead (729 per jornada).
- Board history has 445 versions in ~10 shapes; only cash, `p_win`,
  finish, band and standings are in nearly all. Import those plus the raw
  JSON.
- `ask.py why`'s plan step (`fate`) runs joint simulations on demand;
  storing it for every ranked move costs sim time. Store steps 1-3 only.
- SQLite views take no parameters: `current`'s cut is a `:cut` saved query.
- `players.csv` and `points.csv` are state no rebuild recovers; they stay
  committed files.
- `golden.py` copies tidy and runs only `sim`; S0 makes it freeze
  `data/raw` and run `parse sim` (a cold parse is ~5 minutes).
- The only `sqlite3` CLI on the box is Android's: bash steps log through a
  Python helper.
- Verified: Datasette 0.65.5 has writable canned queries and `pages/`
  templates.

## Steps

**Phase 1: the file and the runs**
- 1.1 (done) `ffcore/db.py` opens `data/lfg.db` (WAL), applies `ffcore/schema.sql`.
- 1.2 (done) `runs`: stage times, rows parsed, peak memory, commit, trigger.

**Phase 2: what the model concluded**
- 2.1 (done) `funnel`, steps 1-3: 25 rows a run, sim time within noise.
- 2.2 (done) `forecast`: `schedule.Factors` built once, the cell its
  product; `ask.py forecast` prints the same objects (output identical,
  `golden.py check` 16 boards the same).
- 2.3 (done) `params`, write-only: every constant and fitted value a run used
  (calibration, availability, fallbacks, promoted discount, premium, offer
  ratios, momentum, fixture ratings), one list in `assemble`.
- 2.4 (done) `boards` and `board_do`/`_squad`/`_standings`/`_exposed`, written
  from the same `report()` document as `decisions.json` (rebuilding the JSON
  from rows would gain nothing).
- 2.5 (done) `grade_forecast`, `grade_decisions`: `run.py grade` after each
  report, skipped unless the commit or the resolved jornadas changed.
- 2.6 (after S1) board history imported from git.
- 2.7 (after S1) "why did it change": a saved query over the last two
  runs' funnel, forecast and params; the ping carries the line.

**Slices: SQL replaces Python, one data family at a time**
- S0 (done) `golden.py` freezes `data/raw` (+ the committed tidy state) and runs
  `parse sim`; floats compared with `math.isclose`.
- S1 (done; grading.py 324 -> 195 lines, SQL equal to the last bit) Datasette on `lfg.db` (loopback first), saved queries in the repo's
  `datasette.json`, `tools/q.py` runs them in a terminal, a `players`
  table for names; the grading reports become saved queries and their
  Python goes.
  Learned: Datasette sends an empty box as '' (queries default with
  `nullif(:p, '')`, and q.py sends '' too); a self-join on a CTE needs
  `MATERIALIZED` (48s -> 0.02s; Datasette stops at 5s).
- S2 (done; code +55 lines until S5 removes the CSV path; golden same; cash and paid identical on the box) Ledger: `api_activity` into SQL; ledger, rival cash, price paid as
  views; `league.py`'s versions go.
  Learned: as-of is one SQL function, cut(), registered per connection
  (Python: the model's clock; Datasette: a plugin), so views stay plain
  views; the parse cache makes a storage move a 30s re-parse, not 5 min.
- S3 Market history: `market`, `api_market`, `api_offers`; price steps,
  momentum (slope through the origin per horizon), premium, offer ratios
  as views; `pricing.py`'s versions go.
- S-ID (done; golden same; 0 of 1187 line-up rows resolve differently)
  Identity: one rule per identity question. Found 2026-10-11: "which
  player does this name mean" was answered three ways (Crosswalk._by_name:
  nobody; crosswalk.py _named: the club breaks the tie; league.app_fielded,
  within your squad: the last one wins; players._merge goes through
  Crosswalk), and
  row_key mixes ff_ids and names in one key column. (Before S4, which is
  mostly slug and name resolution.) The
  registry is a `crosswalk` table (`players.csv` its committed export, and
  what a rebuilt lfg.db loads); app id -> player, slug -> player and name ->
  player are views, each clash rule once; every fact view joins through
  them. `name_key()` (text.norm) is a SQL function like `cut()`. Goes:
  League's `app_keys`, the per-run `players` table, Crosswalk's indexes and
  clash bookkeeping, the Python resolution at 31 call sites as their tables
  move. Stays Python: the fuzzy app-id match (name tokens + value ratio),
  reading and writing the table. (Miguel, 2026-10-11: "on the IDs I feel we
  are regressing".)
- S4a+S5 (done; code lines 6277 -> 6128; golden same, points rebuilt from
  raw alone) every parsed table in lfg.db; the CSV store and Python
  current/history go; store_cases.json keeps the CSV store's behaviour as
  the SQL store's test. Learned: removing an ignore list for files still
  on disk let the report commit take them (fixed: data/tidy/*.csv ignored
  but players.csv).
- Simplify (done, e70fbfc; Miguel: "you are just porting to SQL"): a slice
  may change boards when each change is explained and the backtest is no
  worse. Storage is two rules (snapshot: every fetch; keyed: a row again only
  when it changes), declared in tables.sql with hand-written views and
  triggers; rows carry their player (identity.sql: app_player, ff_identity);
  Crosswalk is the registry only; row_key gone; logic queries are named.
  Backtest: forecast error the same, flagged players 1.433 -> 1.168. Cost:
  lfg.db 33 -> 86MB, a universe 6.1 -> 9.7s. Code lines 6128 -> 5989.
- S4b (done) Points and line-ups as views (schema.sql): the calendar
  (`kickoffs`, `round_locks`, `rounds_ahead`), `scored`, `squad_minutes`,
  `season_record`, `start_outcomes`, `fit_rate`, `last_fit`,
  `availability_tally`, `start_calibration` (the Platt grid search, held
  out a squad at a time, in one pass instead of one per squad). Goes:
  JornadaClock and its two clocks, outcomes, the Python fits, the
  per-jornada points and minutes sums. Python keeps reading a note
  (`prognosis_bucket`, `platt`: SQL functions). Golden: 11 of 16 boards the
  same; 5 differ only by the deadline, now the next kickoff (each player
  locks at his own match; before, a played match's kickoff vanishing made
  it that, or the next round, by accident) and on 08-30 the sale odds the
  deadline sets. Scorer fits identical on today's data. A universe 10.1 ->
  7.9s; code lines 9346 -> 8985.
  Then (simplify): a squad is set against the club's last listing before
  the lock, not each player's last; 21% of "listed" outcomes were players
  the page had dropped (Nikola Maras, last listed 08-13, counted listed
  fit at 0% in jornada 8). Backtest: forecast rmse 2.826 -> 2.822 next
  jornada (better at every horizon), flagged players flat, the replayed
  boards' moves identical. Listed-with-no-% now means 53% to start, not
  15% (the ghosts had dragged it down).
- `what_changed` and `cash_cliffs` (done): the first answers "why did the board
  move" between any two runs, one jornada, as expected points split exactly
  into points-when-he-plays, selection and fitness (plan 2.7; last night to
  now the next jornada changed for one player: Berenguer, back fit, +4.79).
  The second began as "which players will a manager in debt sell", ranked
  by season points per million, and failed against the one squad whose
  answer is known (mine): it picked Natan, 155 points, where the plan's
  sales cost 35, because a player on the bench next week can start later
  and no SQL proxy reproduces the simulation's replacement effects. It now
  says only what the data supports: each manager's debt, his squad's
  value, and the fewest sales that could clear it. Which players go needs
  rivals acting in the simulation (Phase 5). Mutation-tested: two tests
  had to be strengthened before the deliberately broken queries failed.
- Query time guard (done; found 2026-10-14 when bench_points took 138s on the
  real database, 1.2s once its snapshot lookups were narrowed, and
  price_forecast_error was already 5.4s against Datasette's 5s): the
  `queries` stage times every saved query once a day, records each beside
  the stage seconds and names any within 20% of Datasette's limit (now 10s);
  `q.py --time` does it by hand. Datasette applies the schema at start now
  (a view added by a merge was missing there until the next run). Real fix
  for price_forecast_error when the guard trips: keep price_ahead's rows
  per day (534k rows to build, 1.3s, grows with the season).
- Saved queries that earn their place (done; each answers a question a
  decision turns on, and a README-rules check was left out for not doing so):
  `unmatched_players` (who the crosswalk cannot join: 29 players with
  neither id, 86 with no line-up page), `constants_vs_measured`
  (PERSISTENT_SHARE 0.08 set, 0.087 measured; CONFIDENCE 0.70 set, 0.719
  measured over 32 moves), `bench_points` (what sat on the bench each
  jornada: 10 points in jornadas 4 and 6), `rivals` (each manager's cash,
  squad value, finish and last week's trades). The persistence measure is a
  view (`persistence_by_run`) both it and `persistence` read; it was 0.000
  before the listings fix (run 35) and is 0.087 since S6b. Tested against
  rows worked out by hand; two queries broken on purpose to see the tests
  fail.
- S6 (done) The board as views; the phone's JSON its export. S6a: the run
  records what it decides (`sim_standings`, `sim_players`, `sim_plan`,
  `sim_runs`). S6b1: `player_facts` (every market fact per player, one
  view; Market is built from it; load_players, market_routes, pending,
  open_clauses gone). S6b2: `board_*` views and `board_json`; sim.main
  writes decisions.json from it; report() and the five board tables gone.
  Golden: 16 of 16 the same (S6b1 moved two boards: a player the app
  lists for sale but futbolfantasy does not know is no longer forecast,
  which shifts the simulation's draws). Left for S6c: sale odds
  (`Market.fetches`) and `at_risk` still decide in Python.
- Then the old phase 3 rest: tunnel route, `Fantasy.jsx` links, Run again
  and the live log on the box, alerts, more saved queries.

**Phase 5: experiments, each a module of its own; keep what gets used**
- 5.0 Only if a question SQL cannot answer needs it: the model takes its
  fits as one input, loadable from a run's `params`, one fit at a time.
  Then data-vs-fit attribution (today's data, September's fits).
- 5.1 Replay: the board, funnel and forecast at any past snapshot, today's
  code, clock pinned (~400 snapshots x ~8s sim, once; then one a run).
  Tagged by code version so replays and live runs sit side by side.
  Superseded in scope by 5.1a below, which is what we measured first.

### 5.1a The backtest, planned from measurements (2026-10-14)

What was assumed, and what measuring said:

- *"Replaying at all ~400 snapshots gives ~56x the evidence."* It does not.
  The evidence is the **outcomes**: a player's points in a jornada. Next-
  jornada forecasts have **4,655 of them over 7 jornadas** (601 flagged
  ones), +~700 a week. The 434 line-up snapshots re-forecast the *same*
  outcomes under different information. They cannot say whether a model is
  better; they can say how forecasts respond to news and how early they are
  good.
- *"Differences of 0.01 rmse are real."* They are not resolvable. Paired,
  over 4,636 outcomes, the 95% interval on a mean squared-error gain is
  about +-0.18 (rmse about 0.03). Run 7 (before the SQL work) against run 61:
  gain +0.149, interval -0.041..+0.322 (includes 0), so **the next-jornada
  improvement overall is not established**; the **flagged-player gain is**
  (+0.289, +0.078..+0.574, n=601). Every other delta quoted in this plan
  (2.838 -> 2.826, the slips at longer horizons) is inside noise. Likewise
  CONFIDENCE 0.70 against 0.719 over 32 moves, and PERSISTENT_SHARE 0.08
  against 0.087 (no interval yet): "matches" is all they support.
- *"Porting the model inputs (ratings, rates, cells) to SQL would speed up
  replay."* One replayed moment costs: market rows 1.2s, scorer fits 2.1s
  (already SQL views), momentum fit 3.0s (already SQL), **fixture ratings
  0.02s, forecast cells 0.09s** (the Python), whole universe 10s. The
  Python tier is not where the time is; do not port it for speed. A
  forecast-only replay needs the scorer and cells, ~3.5s a moment.

So the backtest is built for the questions the data *can* answer:

1. **Paired comparison with an interval (done: `paired_gain`; next jornada = `ahead` 0).** `compare_grades`
   gives rmse by jornada and no interval. Add the paired mean squared-error
   difference of two runs (or variants) with its standard error, all, flagged
   and not flagged. Every later claim is made with this or not made.
2. **Leakage test (gate for 3). Done 2026-10-14.** `tools/golden.py leak`
   builds each frozen board again on a copy of the data with every row
   observed (or fitted) after its time deleted, and compares. First run: 12
   of 16 boards differed (mostly `trend`, in two cases the plan itself: a
   sale step, cash_after). Cause: `market_daily` took each day's last
   snapshot over all time, so at a cut part-way through a day the
   replay used a snapshot taken after it and lost that day's own, which the
   live run at that moment had. Fixed (as of `cut()`); 16 of 16 now the same
   (2m36s). Run it after any change to a view; a new table with a time
   column must carry `observed_at` or `as_of` to be cut.
3. **Forecast-only replay at every snapshot. Done 2026-10-15.**
   `python src/grading.py replay [JOBS]` forecasts the next jornada at
   every line-up snapshot before a played jornada's lock (view
   `replay_moments`: 302 moments over jornadas 2-8) and keeps it in
   `replay_forecast` (resumable; 3 jobs, ~8 minutes cold; variant label
   `live` until step 4). Views `replay_lead` (error by lead) and
   `replay_gap` (what forecasting earlier costs against the jornada's last
   moment, with an interval), saved queries `replay_by_lead` and
   `replay_gap`. First result, mean squared error earlier minus last
   (rmse^2 is about 8): 3-12h +0.15, 12-24h +0.18, 24-48h +0.20, 48-96h
   +0.26, 96h+ +0.57. Most of what the last four days add is in the first
   of them. The intervals count a player's repeats across moments as
   independent, so they are narrower than the truth: read the pattern, not
   an edge. Raw rmse by lead is flat (2.79-2.85) because buckets hold
   different jornadas; only the paired gap says anything. Also found:
   concurrent processes recreating the views on a fresh file raced
   (`connect()` now takes a file lock while it applies the schema).
4. **Variants as data. Done 2026-10-15.** `variants(variant, name, value)`
   holds a challenger's constants (`python src/grading.py variant NAME
   CONST=VALUE ...`); `model_constants(variant, name, value)` holds the
   constants a run was built with, and the views read the row of
   `variant()` (the clock's, beside `cut()`), so a challenger replays beside
   the live run without touching its rows. `assemble.applied(name)` puts a
   variant's values on the modules that own them (a name that is no constant
   raises) and restores them; three defaults that bound a constant at import
   (fit_availability, Scorer, Bootstrap) now read it at call time. Replay a
   challenger with `LFG_VARIANT=name python src/grading.py replay 3`
   (~5 minutes), judge it with the saved query `variant_gap` (one moment a
   jornada, `hours` before the lock, so outcomes are not repeated).
   Results at 24h before the lock, mean squared-error gain over live (positive:
   better): AVAIL_K 2 -0.002 (nothing, and exactly 0 for unflagged players:
   it touches only flagged ones, so the variant applies where it should);
   PICK_K 1 -0.013, n.s.; SHRINK_K 2 -0.172 (worse), 8 +0.071 (+0.009..+0.132),
   16 +0.059 (n.s.). The direction is consistent (more shrinkage toward the
   prior helps up to about 8) but the best gain is ~1% of the squared error
   and the one interval that excludes 0 is one of four tried: a candidate to
   watch over more jornadas, not a change. Also: `connect()` applies the
   schema only when the file does not carry its version (a crc in
   `user_version`), so processes starting together no longer recreate views
   under each other.
5. **Only if (3) is too slow:** per-snapshot caches for the scorer fits and
   per-day for `price_ahead` (also the fix for `price_forecast_error`).

Not planned, with the reason: replaying the plan search at every snapshot
(18s each, ~2h a variant, and decisions have only 7 locks of outcomes
anyway; price outcomes already have a daily series in `price_forecast_error`);
porting ratings, rates and cells to SQL (measured: nothing to gain).

Gates: (2) must pass before any replay result is believed; (3) is judged
by whether the error-against-lead-time curve exists and (1) puts an
interval on any variant gap; stop at (1)+(2) if nothing in (3) would change
a decision.
- 5.2 (done 2026-10-15: saved query `phantom_coach`, the graded plan moves each
  once, those your ledger shows made (same buy/sell of the player at or after
  the lock) set aside: 7 made, 14 unmade, +119 points; still an upper bound,
  as moves interact and bids may not win) Phantom coach: a sixth manager that does what the board says, from
  jornada 1, scored in the real standings. Assumes its bids win at the
  model's price and rivals do not react to it: it grades the advice, not an
  alternative season.
- 5.3 Every manager's board: the same funnel with `me` set to each rival
  (`me` is already a parameter end to end). Their cash is our estimate and
  their pending bids are invisible. Then: what each rival should have done,
  what they did, and their phantom beside them.
- 5.4 Champion and challenger: a model change runs beside the live one,
  both scored as jornadas resolve; it replaces the live one only on a win.
- 5.5 Parser self-report: a page parsing to zero rows opens a session with
  the raw page, the parser and its last good page, which proposes a fix.
- 5.6 The game's price rule, fitted from every value and nightly offer
  since August; judged by the backtest.
- 5.7 Rivals who act in the season sim, fitted from the ledger. A model
  change: whiteboard first.

The scraped data is close to public (Miguel, 2026-10-11): sharing a
league-facing view is not ruled out.

## Later, once the above is used

Each commit's effect on the board and the backtest; constants tried side by
side as tagged backtest variants; a never-recommend list; a decision
journal; a weekly league recap of public facts; rival bid prediction (check
the 314 ledger rows carry signal first).
