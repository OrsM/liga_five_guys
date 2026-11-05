# Simplify: the step log (moved from simplify.md, 2026-11-05)

The dated steps, timings and estimates of the simplification, kept for the reasoning. The map is `../simplify.md`.

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
- Then (2026-11-02, speed no gate, the user's call): kept.py gone (every
  universe built from the data; the fits made again at their cut);
  dec_gain is f of each move's squad less f(S), every squad's eleven by
  rules.ELEVEN at once (dec_slots and the incremental bars gone, the
  backtest the same 560 at the same 4.5-5 min); sim_facts a view over seen
  (sim_runs.cut), the live database's 153 runs moved once
  (tools/migrate_sim_facts.py; backup data/lfg.db.bak-20261102-sim_facts),
  the live board identical before and after. Kept, measured: facts.py's
  price_step and ff_identity_kept (as views, 0.8 s and 0.6 s a read, read
  many times a universe).

## Step 8: one model layer (2026-11-02)

- A squad's points are SQL over one forecast view, `cell` (look's moment:
  p, e, v), through rules.ELEVEN: the decision (dec_gain), the standings
  (league_xi, league_season, league_standings, league_jornada: the old
  Standings and gain.py's private database gone; equal to them to 1e-15 on
  7 past universes and live), and your eleven next (next_cell, my_xi,
  look_players into sim_players; the backtest's phantom fields my_xi).
  Outlook, gain.py, Universe.part_played/first_jornada_of and the one-off
  tools/migrate_sim_facts.py gone. The live board's players identical
  (66 rows, 3e-14).
- Backtest unchanged: 560 vs 451, 124 moves, 7 lost; 15 min (9m46 before
  the standings move: a decision.inputs per look). Speed no gate here.
- Rollback note: the old code refuses a database the new has opened
  (dec_state lost cut); drop the dec_* tables first.

## Step 9: the league at t, items 1-4 of the plan by layer (2026-11-03, branch w-core)

- One t: the process's (db.LOOK: t, moment, me, path, t0; set_now and
  set_look write it, db.replaying puts it back), read by the views as
  cut() and the view look; no t in the file (a first try kept it in a
  table row, shared by every process on the live file: the watcher, the
  reminder, Datasette and the grade stage's replays would read each
  other's t). clock._NOW, on_reset and the jornadas' caches gone.
  Backtest identical (124, 7, 560 vs 451).
- My moves as an input: moves (path, at, kind, player, amount) and
  look.path/t0; my_squad, my_cash, my_bids. Day, acted, closed,
  with_squad, League, dec_mine, dec_state.cash, universe_when/_AT gone.
  my_cash adds the league's bonuses since t0 (the definition's "real
  non-move income"); Day added none. That alone moves the backtest to 129
  moves, 5 lost, 522 vs 451, value 327.9M: with the bonus term out it is
  124/7/560 again, every jornada's moves the same.
- One auction outcome: auction_results only closes by t; bid_odds and
  my_bids read it. A sale gets seen.fetches (in the backtest every sale
  came after the auctions, none with an offer: all at value, as before).
- seen made once a cut, variant and code (seen.code). Backtest 865 s cold
  (forecasts remade), 556 s warm (711 s before). A decision at a past t:
  the league 1.0 s made before (3.1 s not; the universe was 2.9 s), the
  plan 7.7 s as before (its ~25 dec_next steps: the next thing to speed).

