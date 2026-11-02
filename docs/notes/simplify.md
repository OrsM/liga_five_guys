# Simplify: the map (2026-10-31)

Seven read-only surveys of the code (decision layer, money, replays and
caches, forecast, ingestion and identity, operations and the phone, tools,
tests and docs), joined. The aim: the decision as a few SQL views the board
reads, one copy of each fact, and nothing the board does not need.

## What stays

- The forecast (Python): each player's cell a jornada (points if he plays,
  chance he plays) and his season. It beats a plain average: error 2.78 vs
  3.05, correlation 0.58 vs 0.50 (grade_forecast, jornadas 3-10). Most of
  its steps are arithmetic that could be SQL (Scorer.rate's two shrinks,
  detect_promoted, fit_availability, Calibration.p, difficulty,
  attack_defense, season(), Outlook.season); only the program and the
  standings integration need Python.
- Since 20fda37e every run writes every market player's season forecast to
  sim_players beside sim_facts (value, price, route): the inputs of an SQL
  decision.

## One fact, many copies

| Fact | Copies |
|---|---|
| What money is worth | gone (2026-11-01): the decision is points only, money a limit (cash at the lock, the debt rule); money_rate, lam and player_facts.ahead deleted |
| The eleven | gain.ELEVEN (gain.py:30-44, via Outlook.xi), the program's own formation LP (decide.py:271-315), board_eleven / field_basis / lineup_gap (schema.sql:1466-1506); shortfall vs _fieldable |
| Debt room, 20% of team value | once (2026-11-01): decide.sql dec_room; a run keeps it (sim_runs.cash_after, can_bid), board_head reads it; board_money gone |
| A sale's proceeds with no offer | once (2026-11-01): Market.fetches, stored as sim_facts.fetches, the board's raises |
| Selling to clear a debt | grading.closed via program (board_resale deleted); when a sale is made: decide.sql dec_when, board and backtest |
| Resale picks | deleted (2026-11-01): never shown on 60 boards; a buy is listed and sold like any other |
| The accept rule for a board line | once: board_json accepts, for the plan's lines and the rankings' alike; act.py and the server match a line by label and an accepted amount |
| The board itself | Python Board + sim docs, then SQL board_json, then server notify text, then Fantasy.jsx formatting (two number formatters in one file) |
| The line-up message | once (2026-11-01): lineup_names, read by the field line and lineup_alert |
| Shrink toward a prior | once (2026-11-01): stats.shrink, SQL's shrink() |
| Matches played | scored.games vs season_record.apps |
| The forecast at a past moment | forecast_cells (backtest, replay) and _built (board, decisions), built twice a lock with different results |
| A past moment replayed | phantom_path, phantom_season, decisions, replay, backtest, sim_calibration |
| Caches | kept answers, kept built universes, built @cache, grading._AT, _first_listed, facts (price_step, momentum_day), fit tables (momentum_fit, price_error_fit, price_trend_fit) + the "fits" marker; momentum is held four ways |
| Player identity | crosswalk / ff_identity views, attach_slugs' second pass, app_links value matching; lineup_gap by app id else name_player (2026-11-01); a match path's clubs by club_slug |
| A player's value at one instant | market, api_market, api_players_all, api_teams |
| Explaining the board | ask.py (reruns the program), q.py / datasette board views |

## Bugs found on the way

1. The backtest's forecast is not the board's: forecast_cells skips late
   arrivals and phantom_fill (assemble.py:261-269 vs 315-372).
2. replay_forecast is keyed by variant, not code: after a change the live
   replay keeps the old code's forecasts for ever (grading.py:148-165).
3. The line-up reminder and the board's Field line can name different
   elevens before the night's auctions (lineup_alert 'now' vs field_basis
   'plan').
4. lfg-watch reads then deletes accept.json in two steps: a press between
   them is lost; the server's queue write is read-modify-write.
5. board_json joins a buy's asking and win by name, not player.
6. league_sources keys (api_market, api_teams, api_activity_N) carry no
   league id: a second league overwrites the first. It now refuses a
   second league (2026-11-01).
7. Grading values money over hmax updates, the plan over MONEY_H (7).
8. p_now may land on a jornada already behind (schedule.py:72-86 vs
   assemble.py:356-360). Possible: rem orders a jornada by any club's next
   kickoff, so a postponed match can sort before the club's real next one.
   None on the data (2026-11-01: no jornada behind); it moves only the first
   jornada's chance. Left until one shows.
9. ruff is configured and never run; the auth.py suite may test nothing.
   ruff now runs in selftests (2026-11-01).
10. Players a manager lists for sale (route 'listed') are never offered:
    acquire() drops every owned player. Letting them in needs the backtest
    to settle such a sale (a seller's choice, not the game's auction): tried
    2026-10-31, it read them as free wins (351 moves, 180 points).

## Step 1 done (2026-10-31, branch simplify)

- Bug 1 was narrower than written: the two builders gave identical cells
  for every player (jornadas 8-10 checked) but late arrivals, whose chance
  the board sets to 0. That is about owning him, not about him: it stays
  on the board's side. One builder now (assemble.player_forecast), its
  moments kept in forecast_moment / forecast_player / forecast_cell /
  forecast_fit, keyed by the forecast's own code (src less NOT_FORECAST)
  and its constants (FORECAST_MODULES); another code's moments are deleted
  once the new one makes one. Cells, factors and fits match the old
  builders exactly at every lock checked.
- Bug 2: the replay reads that store (view replayed, forecast_variant);
  replay_forecast and replay_tried dropped. The replay of every snapshot
  is off the run (grading.py replay JOBS): ~550 moments made again at each
  forecast change.
- Bug 3: one eleven for the field line and the reminder, the squad's now
  (board_eleven); field_basis gone. sim_players.plan_xi is written and
  read by no view: it goes with the program (step 4).
- Bug 4: a file a press (server accept/, renamed in whole), taken by
  renaming into taken/ (tools/lfg-watch), the old accept.json too until
  the phone runs the new server.
- Bug 5: board_json joins a buy line's asking and odds by player.
- Measured: backtest money 8 7, 506 vs 451 standing, 25 moves, as main on
  the same data (not the 516 / 22 of an older copy). perf.py 17.7 s to
  10.6 s. A cold universe is now 4.1 s of which the forecast is 0.14 s:
  the rest is the market's per-cut views (price_momentum 1.4, League 0.7,
  bid_odds 0.6, market_facts 0.5). After a decision edit the backtest
  stays ~50 s until those are kept the same way.
- Next: the forecast's history views (start_calibration, season_record,
  pick_later, availability_tally, ppm_prior, scored, last_fit: 2.2 s a
  moment) for all moments in one pass; then the market's.

## Step 4 done (2026-10-31, branch simplify, not live)

- The decision is decide.sql's, on the board and in every replay; the
  program, its scoring and funnel, MONEY_RATE, TIE, Board.lam, the rival
  boards, ask.py, funnel.mmd and the money variance (Market.money, MONEY_H,
  price_error) are gone. One debt constant (rules.DEBT_SHARE).
- money_rate is the slope of points a game on value (0.060 points a game a
  million), not the ratio of means (0.255): a player worth nothing scores
  about 3 a game. The ratio had the SQL rule ahead (2961 vs 2757); on the
  slope the program leads, 1098 vs 1058, all of it the program's luck
  (+28 points over its forecast; both forecast 478.1) and 6M of money.
- A move's points come from the eleven now by the slots it changes
  (dec_slots: a sale's replacement, a buy's bar), equal to building every
  squad's eleven on three dates checked. Backtest 3.6 min to 31 s warm, a
  board decision 0.24-0.41 s (the program's 0.6 s).
- Still slower than the program's backtest (17 s): the rule makes 224 moves
  (92 of 121 buys sold again), each run about ten steps.

## Step 5: one copy of each fact (2026-11-01)

The rule: a fact is stored once, by what produces it (ingest's tables, the
Python forecast's forecast_cell, a run's decisions); a run records
references (its cut, its moment) and what it decided, never a copy of what
it read; everything else is a view; Python reads views and works out no
rule a view has. Each copy found, and what became of it:

- forecast, a run's forecast cells: the run's moment (sim_runs.moment),
  run_forecast a view; a retired code's moments kept while a run points to
  them. plan_gain: the board sums decision_plan.
- grade_forecast, 1.26M rows of which 84k distinct (runs of one forecast
  code kept identical copies): grade_moment, the moments graded, and a view
  over forecast_cell and today's scores. Old grades: 528 locks into 64
  moments. Monitoring queries 1-2 s slower (forecast_error 1.4 to 3.0 s).
- Market.later and Market.carry, the value-after-trend rule in Python:
  player_facts.ahead.
- acted's debt room: the decision's (dec_next) alone.
- Forecast.per_j and flagged, a second read of the cells _built reads as
  used: the record is its moment.
- The past-lock loop four times over: before_locks and at_locks.
- built()'s @cache (keyed without the clock) and _first_listed's: gone;
  each is made once where needed.
- Five views nothing reads.

Kept, and why:

- sim_facts, sim_waiting: the one evaluation of player_facts, sale_odds
  and waiting at the run's cut, which the universe read and the board
  shows. As views the board would work them out again (1-2 s a publish,
  and sim 8-10 s to 20 s): a stored evaluation, not a second rule.
- dec_* (decide.sql's inputs) and gain.py's cell table: a universe loaded
  for SQL. A replay's universe is a hypothetical squad at a past moment;
  reading forecast_used directly doubled the backtest (64 s vs 32 s).
- Kept universes (on disk) and _AT (in memory): a past universe built once
  for every replay and run that asks; _AT is how the warm pool hands its
  universes back.
- The accept rule: already once, board_json's accepts, which act.py, the
  website's server and its page read.

## Step 6: the rankings and the last copies (2026-11-01)

- The board ranks every buy that adds points and every sale (decision_option.gain,
  the eleven week by week over the formations), the plan's marked; each line
  is acted on like a plan line (bid, take an offer, list).
- One phantom (daily); stamps parsed once (stamp_jd); the copies in the table
  above. Backtest money unchanged: 560 points vs 451 standing, 124 moves,
  7 lost; 30 s warm, 4 min cold. Board 1.09 s to 1.02 s.
- Not done: the forecast's history views in one pass (2.2 s a moment, only
  when a forecast change is replayed).
- Kept: the fetch's double parse (~2 s a run); momentum and price_trend_now's
  caches (0.8 s and 0.55 s a board without).

## Dead or idle (cleared 2026-11-01)

- Gone: lfg-publish's markdown branch and its comments; .runtime/; the
  zero-byte lfg.db at the root; data/tidy parsed_points.jsonl,
  startcal.json, .parse_spill/; stale scratch copies under ~/.cache;
  .claude/RESUME.md; the fixture's plan_gain and backup and the page's
  p_win_after (website); five views nothing read; the board's plan_gain
  and standings[].mean; the README's funnel, program, ask.py, at_risk,
  open_clauses, Universe.bid and grading flags; diagrams regenerated.
- Not dead after all: the constants no view reads are what a variant sets
  and a run records (assemble.NAMES), and Datasette's constants query reads
  them from model_constants. The verdict column and KAPPAS were gone already.

## Where the time goes

- A run: 110-120 s. Self-tests and mypy every run (beside the fetch), the
  queries stage timing all 33 saved queries (12-18 s, monitoring only),
  sim 24-30 s, rivals 3 s (a board a rival), crosswalk 7 s, grade 1.5 s
  (34 s after a code change). Two full runs with notifications each evening
  (21:45 and 22:35); a reminder is a full run.
- Every fetched page is parsed twice (page_sig, then parse).
- A backtest: 16 s warm (10 of them solving the program), ~27 s after a
  code change, ~5 min after a view change (79 universes rebuilt).

## Estimates

- Code: the decision layer (decide.py 865 lines, half tests), sim.py's
  funnel and rival boards, grading's decision replays, ask.py/uml.py's
  funnel explanations, the dead pieces above and the tests pinning them:
  about 2,000-2,500 lines fewer net of ~15,000, plus ~150 lines of SQL.
- Speed: a board decision 0.4-1.5 s to ~0.05 s; a run ~115 s to ~60 s
  (no queries stage, no rival boards, no program, self-tests off the run
  path); a backtest of a decision rule seconds once each past day's forecast
  is a stored table; of a forecast change still ~5 min.

## Step 7: SQL is the model, step 1 (2026-11-02)

- The market and the league as a universe is built on them are stored
  once a cut, by SQL (seen, seen_odds: player_facts, sale_odds, bid_odds);
  Market, the decision's inputs (dec_player_seen, dec_odds_seen,
  dec_money_seen, made into dec_player/dec_odds/dec_money once a decision)
  and a run's sim_facts read them. Python writes only the squad and cash
  (dec_state, dec_mine) and the forecast (dec_xp: reading forecast_used
  directly doubled the backtest). Market.fetches and Market.takes gone.
- The decision read straight from the views: 0.9-1.5 s a plan; made once a
  decision, 0.33-0.46 s, as before.
- Backtest unchanged: 560 vs 451, 124 moves, 7 lost; 35 s warm, 3.3 min
  cold. Board 2.0 s before and after (same data).
- Next: the board's views read seen (no sim_facts), then the backtest's
  universes (kept pickles) and Universe, then the tests out of the modules.
