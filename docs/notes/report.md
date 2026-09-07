# report.py — design notes

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
