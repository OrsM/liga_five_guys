# Plan: one SQLite file, served by Datasette

Agreed 2026-10-11. Each step is one commit, red test first, pushed and
deployed when green.

## Shape

- **Box**: fetch, parse, sim; writes `data/lfg.db`. Datasette serves it at
  `lfg.lemonworlds.com` (behind the `*.lemonworlds.com` Access wildcard).
  Run again and the live log live here.
- **Phone**: `lfg-publish` still pushes `decisions.json` every run, so the
  last board survives the box being down. `Fantasy.jsx` stays the one
  renderer; its numbers link into Datasette (as `Band.jsx` does).
- **One query, three readers**: each question is one saved query in
  `datasette.json`; Datasette, `ask.py` and the board run that text.
- **`lfg.db` is disposable**: every table is rebuildable from `data/raw`,
  git or a replay, except `runs`. No backup. Tables change by adding; a
  changed table is dropped and rebuilt.

## Decisions

| Question | Answer | Why |
|---|---|---|
| Forecast retention | every run | measured 2026-10-11: 91 players x 32 jornadas = 2.8k rows a run (the forecaster covers squads and the market, not all 729), ~3M rows and ~200MB a season; no pruning code |
| Funnel retention | every run | small |
| Telegram ping | stays on the phone | it fires only after a successful publish and links to the phone page |
| Report commits | keep | they are the board history and its backup (8KB, 2s a run) |
| Tidy storage to SQLite | last, only if the line count after phases 1-3 justifies it | parse is 1.4s since `2b39510`; only deletions argue for it |

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
- `golden.py` copies tidy and runs only `sim`; before phase 4 it must freeze
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
- 2.2 `forecast`, a column per factor; `ask.py forecast` reads it (same
  output), its computation deleted.
- 2.3 `board`; `decisions.json` exported from it (`golden.py check` same).
- 2.4 `grading` writes backtest and replayed decisions as tables.
- 2.5 board history imported from git.
- 2.6 "why did it change": diff of the last two runs' funnel and forecast,
  naming the factor that moved; the ping carries the line.

**Phase 3: Datasette, and the run controls off the phone**
- 3.1 `lfg-datasette.service`, tunnel route, first saved queries (timings,
  why, forecast). Outside curl gives 302.
- 3.2 `Fantasy.jsx` links into rows.
- 3.3 Run again: a writable canned query; `lfg-watch` polls the file.
  Deletes the ssh poll, `rerun.json`, `/api/fantasy-rerun`.
- 3.4 Live log page. Deletes the `run.json` push loop, `/api/fantasy-run`,
  the console in `Fantasy.jsx`.
- 3.5 Alerts: a saved query flagged as an alert pings when it returns rows.
  First: your app line-up is not the best XI hours before the lock.
- 3.6 Saved queries, one a step: rival profiles, unmatched crosswalk,
  constants as measured, README rules as checks, bench points lost,
  row -> raw page.

**Phase 4 (optional): tidy storage**
- 4.0 `golden.py` freezes raw and runs `parse sim`.
- 4.1 One table a step, `api_activity` first, the daily tables last.
- 4.2 The three side files become tables.
- 4.3 Delete the CSV machinery nothing uses.

**Phase 5: experiments, each a module of its own; keep what gets used**
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
