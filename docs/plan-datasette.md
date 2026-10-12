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
- S6 The board as a saved query; the phone's JSON its export.
- Then the old phase 3 rest: tunnel route, `Fantasy.jsx` links, Run again
  and the live log on the box, alerts, more saved queries.

**Phase 5: experiments, each a module of its own; keep what gets used**
- 5.0 Only if a question SQL cannot answer needs it: the model takes its
  fits as one input, loadable from a run's `params`, one fit at a time.
  Then data-vs-fit attribution (today's data, September's fits).
- 5.1 Replay: the board, funnel and forecast at any past snapshot, today's
  code, clock pinned (~400 snapshots x ~8s sim, once; then one a run).
  Tagged by code version so replays and live runs sit side by side.
- 5.2 Phantom coach: a sixth manager that does what the board says, from
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
