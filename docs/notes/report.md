# report.py — design notes

## market_wide_log — every player gets a forecast on record

`log_squad()` used to write one row per snapshot per SQUAD player only
(~15-30 players) — but the model already scores every priced player in
the market every run (that's the only way BUY/RAID candidates exist at
all: `decide.py`'s `per_j`/`Bootstrap` covers all 731). Only the RECORD
of it was squad-scoped, which meant every fit that grades our own past
predictions against reality — `drift_frac_from_history()`'s `DRIFT_FRAC`
fit, `current_mae()`, the "our forecast" row in the start-probability
Brier table — was bottlenecked on ~30 players' worth of history no
matter how broad the underlying model actually was (Miguel, 2026-09-16:
"every player should have a forecast and every player's forecast should
inform our decisions... I can't believe we're uncovering this now").

Checked before assuming it was a data-coverage problem, not just a
logging one: `market.csv` (2026-08-11) and `points.csv`/`starters.csv`
(2026-08-16) — the tables that actually feed a player's rate and start
probability — all predate this fix by weeks, so the model was never
short of market-wide data to predict FROM. It just never kept a record
of what it predicted for anyone outside the squad.

Fixed by scoring `sc.score_squad(all_keys)` for every `ff_id` in
`m.market` (same shared `Scorer` the squad rows already use — this is
not a second pricing pass, everyone is already priced for the BUY/RAID
scan; this just keeps what was already computed), deduped against the
squad rows already logged, and appended with `picked=0` (never in
`chosen`, since only squad players can be). Verified in isolation (a
temp `DECISIONS` path, not the real log): 660 rows / 657 unique players
per snapshot, up from 15; the real `report.py --selftest` and full
pipeline runtime were both unaffected (~8s either way, since the
scoring itself was already happening).

`row_for()`'s lookup is keyed by `ff_id`, not by name — the first
attempt at this used `market`'s `name` field directly and silently
resolved zero players (`sc.lookup.get(name)` never matches an id-keyed
dict); caught by checking the actual logged row count rather than
trusting the code ran because it didn't error.

## score_h3 — the short-horizon figure

Forecast-first rebuild plan's Stage 5, added 2026-09-06 (`db1dfec`). A
season-long calibration check (`methodology.py`'s "Forecast vs actual"
table) only resolves once most of the season has played out — far too
slow a feedback loop to catch a miscalibration early, which is exactly
what let a real 95%+ early-season win probability go uncorrected for
weeks earlier this session. `score_h3` is the early-warning fix: a
3-jornada-ahead figure logged alongside the season-long one, cheap
enough to grade every few weeks instead of waiting for the season to
mostly resolve.

`score` (jornada+1) already has a real fixture factor; the next 2 rounds
have no fixture drawn yet at log time, so `score_h3` uses `ppm *
pct_rest` (the same "rest of season" rate/start blend `Scored` already
carries) with no fixture adjustment for those two — an honest
approximation, not a second Monte Carlo simulation. Graded 3 jornadas
later against the real cumulative points over the same window, once
enough real data exists to sum — a genuine time blocker (needs 3 real
jornadas to pass), not a build one, unlike `ffcore.forecast.DRIFT_FRAC`'s
own horizon-ladder question, which turned out solvable the same day by
mining lead-time differences already sitting in `squad_log.csv`'s
history instead of waiting (see forecast.md's own note on
`drift_frac_from_history()`).
