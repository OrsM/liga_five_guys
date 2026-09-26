# Holistic map and target design — 2026-09-26

Measured on `src/` at 207df25: 20,371 lines, of which ~6,400 are inline
self-tests; product code has **803 function-like units** (532 top-level,
96 methods, 45 nested, 130 lambdas). That count, not lines, is the
complexity measure this redesign is judged by.

## What the system is for

One sentence: every few hours, fetch the league's and the football
world's state, forecast each player's points, simulate the season, and
tell me which transfers to make.

## Jobs, and where each one lives today

| # | Job | Today | Problem |
|---|-----|-------|---------|
| 1 | Fetch pages | `ingest.fetch`, `sources.Source` registry | fine in shape |
| 2 | Detect a page changed | 20+ `sign_*` digests in `sources.py`, one per parser | second copy of every parser's knowledge |
| 3 | Parse pages to rows | `parse_*` in `sources.py` | fine in shape |
| 4 | Turn every stored page into tidy tables | `ingest.parse` + snapindex + parse cache keyed on `parser_sig` (hashes its own source) + tail-append mode + spill-to-disk + cgroup-sized process pool + 3 private CSV writers | a cache/perf subsystem larger than the job |
| 5 | Know who a row is (identity) | `Crosswalk.player`, `Crosswalk.resolve` (9 params), `Crosswalk.key_of`, `Market.key_for/candidates/_by_price`, `tidy._merge` ladder, `league.identify/_roster_key/txn_key`, `crosswalk.build_players.by_name`, `text.resolve`, `_priced_like`, `_value_index` | every source already carries a typed id: 213/213 ledger rows, 80/80 api_teams, 52/53 api_market resolve by app_id alone. Name-matching belongs only in the crosswalk build |
| 6 | Who owns whom, and cash | `League`: rosters file + name-matched replay + API override + drift warnings + cash estimate | ledger is 100% generated from the app with player ids; owners come from api_teams; replay/identify/rosters are a fallback for a world that no longer exists |
| 7 | Load tidy tables | `tidy.py`: `load`, `newest`, `load_api`, `load_lineups(_latest)`, `load_market_frozen/latest`, `load_api_stats`, `load_understat_players`, 5 separate mtime caches | one loader per table with its own caching idiom |
| 8 | Represent a player | tidy `players` dict, `Scorer.lookup`, `PlayerProfile`, `Universe` views, crosswalk `Player` | five shapes of one thing |
| 9 | Fit model parameters | `fit_*` spread over score/forecast/fixture/startprob/lineupweight/methodology; results stored by **mutating module globals** (`DRIFT_FRAC`, `RATE_REL_FLOOR`, `HOME_EDGE`, `STATUS_FACTOR`, `NEUTRAL_START`, `ABSENT_START`) | order-dependent hidden state |
| 10 | Forecast and simulate | `Scorer` → `build_profiles` → `decide.load` (168-line blob) → `schedule` → `Bootstrap` → `season` | `decide.load` mixes identity, pricing, fitting, profiles |
| 11 | Decide | `Universe` methods + `worth_doing`/`best_move` | now in one place (63e5cc9) |
| 12 | Grade the model against reality | `methodology.py` (1,200 product lines), `backtest.py`, `gap_signal.py`, `tools/*walkforward*`, `tools/replay_scorecard.py` | one job in five places |
| 13 | Render reports | `sim.py`, `squads.py`, `methodology.py`, `scout.py`, `slate.py`, `report.py`, `flip.py`, `digest.py` | ~100 hand-built markdown table lines, no shared renderer |

## Target design

1. **Identity: resolve once, at crosswalk build; look up everywhere else.**
   The crosswalk build matches every source's ids (ff_id, ff/af slug,
   app_id, understat_id) onto the canonical key, by name+club, once.
   Everything downstream is `xw.key(source_id)` — a dict lookup. Delete
   `Crosswalk.resolve`, `attach_market`, `_priced_like`, `_value_index`,
   `Market.key_for/candidates/_by_price`, `identify`, `_roster_key`,
   `txn_key`, `_merge`'s ladder, `text.resolve` where it only served these.
2. **League from the app.** Owners from api_teams, transactions from the
   activity feed, my cash from standings, rivals' cash estimated from the
   feed. Delete rosters replay, `owner_drift`, `read_rosters`,
   `read_balances` and the cash.txt fallback if nothing else needs them.
3. **Parse without the cache subsystem**, if a cold full parse is fast
   enough (measuring). Change detection keeps one generic digest of the
   parsed rows instead of 20 hand-written `sign_*` functions.
4. **One table loader** in tidy: `table(name, view="all"|"newest"|"fresh")`
   with one cache.
5. **Fitted parameters are values, not global mutations**: fit once into
   a `Params` object passed where used.
6. **One player record** carried from load to decide.
7. **One grading module** for everything that compares forecasts or
   recommendations with what happened.
8. **One markdown table renderer.**

Order: 1 → 2 (they share the deletions), then 3, 4, 5/6, 7, 8.

## Status

- **1 Identity — done** (8068c3d, 1082eba). players.csv is an append-only
  registry; app ids are validated against the app's own name and value
  every build, and every change is printed. Corrected 9 wrong ids.
- **2 League from the app — done** (8068c3d). rosters_initial.txt and
  cash.txt deleted.
- **3 Parse — done** (fbf12b4). One chunked append path; parser version is
  a hash of sources.py; one page_sig replaces 20 sign_* digests; points is
  a table. Measured: cold parse 5m30s / 412s CPU, peak 558MB under the
  service's MemoryMax=750M; +30 snapshots incremental in 34s; all 19 tables
  identical to the old code, row for row.
- **4 One table loader — done.** `table(name)` for every row and
  `newest(name)` for the latest snapshot, one mtime cache.
- **5 Fitted parameters — done** (eda1944). They live on their owners
  (`Calibration`, `Bootstrap`, the boards' `home_edge`); no module globals.
- **6 One player record — done as far as load** (86efa39). `decide.load()`
  is the one assembly point: it builds the League and Scorer, derives
  squads, clubs and positions from the profiles, and the Universe carries
  `lg`/`sc`. ffcore/model.py deleted. `score.build` (43a53a4) keys every
  model input by player id — the name join had given five players a
  namesake's history — builds each input once, and shares one set of
  difficulty ratings between the fixture and season boards.
- **7 One grading module — done.** grading.py replaced methodology.py.
  Graded matching tries the id before names (f9b10f0); it was a set, so
  results changed with the hash seed.
- **8 Markdown renderer — superseded.** The reports that needed it were
  deleted (4cfdcd3, 1257f4a); decisions.json is the report.

Running count (all of src, functions incl. methods/nested/lambdas and
selftests): 655 functions / 13,937 lines at 0e49bff → 639 / 13,409 at
86efa39 → 619 / 12,940 at 43a53a4.

Next: `sim.py`'s ladder/payload and its nested `cell` helper, then
`report.main`.
