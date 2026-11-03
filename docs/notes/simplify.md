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

## The dictionary (2026-11-03; replaces "one fact, many copies")

After SICP: state is a function of time, not a mutated object. The league
at t = f(facts, t); "now" is an argument (look.cut), never a clock someone
sets; a backtest is the same expression at an earlier t; "what if I had
moved otherwise" is a different input (my own moves added to the facts),
not a changed world. Each concept is named once, as a view, in one layer,
and each layer reads only the named views of the layer below:

    facts         crawled rows, timestamped (Python: crawl, parse)
    league at t   who owns whom, cash, market, jornadas, auctions
    forecast      each player each jornada (Python: the fit; SQL the rest)
    decision      the plan
    grade, board  how good a move was; what to show

A concept with two homes, a layer reaching past the one below, or a
concept held as mutable state is a fault. Verified by six read-only
audits (2026-11-03):

| # | Concept | Layer | The one home | Copies and crossings today |
|---|---|---|---|---|
| 1 | t, the moment | league at t | one t, an argument | cut() a process global (clock.py _NOW/_SET, db.py functions); look.cut a second t; views of one decision read both (decide.sql seen via look.cut, jornada_calendar and cash via cut()); set_now per date in grading; datetime.now in grading.auction_closes, run_now in age_hours |
| 2 | Who a player is | facts | identity.sql views | Python Crosswalk/Player rewrites the crosswalk table; ff_identity_kept cache; text.resolve fuzzy clubs; lineup_gap, auction_results, grading re-match by hand |
| 3 | Who owns whom | league at t | owners view at t | seen.owner (stored at 91 of 233 cuts: history before owners were kept), League.owner/squad, LeagueState.squads, Universe.mine, Market.owner, dec_mine, grading Day / with_squad |
| 4 | Cash | league at t | cash view at t | League.cash, Market.cash/left, dec_state, dec_money, Day cash; League() writes league_config on every build |
| 5 | The market | league at t | seen at a cut | seen -> Market dicts -> seen (_by_hand); board_buy_facts, board_json, late_arrivals, listing_ends, grading read api_market directly; sim.report swaps views for tables |
| 6 | What a sale fetches | league at t | seen.fetches | the backtest blanks offers (with_squad) and credits value; sim_waiting copy |
| 7 | Did a bid win | league at t | one cut-bound auction outcome | auction_results ignores the cut; grading.closed / auction_bars read future bars; my_bid three ways |
| 8 | Jornadas and locks | league at t | jornada_calendar | @cache round_locks/rounds_ahead/rounds (reset by the clock); LeagueState.jornadas; before_locks / run_times (21:45 hard-coded) |
| 9 | A player's forecast | forecast | forecast_cell -> cell | pts*p written five times (expected_points, cell, standin_cell, grade_forecast, replayed); Bootstrap.per_jornada; Factors.cell vs run_forecast |
| 10 | Price trend | forecast | views at t | kept fits read "last kept at or before t", so a past t depends on which builds ran; refit and rewritten every build; read four routes; momentum_day built by flipping the clock |
| 11 | Season points, matches played | facts | scored -> season_record | points and season_points; "played" three ways (page games, scored.games, apps), mixed in assemble's pj |
| 12 | The best eleven | decision | rules.ELEVEN | one (my_xi's pick differs: next jornada only) |
| 13 | A stand-in | decision | league_squad | one; reads me from the plan's dec_squad |
| 14 | A game's spread | forecast | score_spread | one; the variance expression twice (cell, standin_cell) |
| 15 | Standings | grade, board | league_standings | sim_standings (a run's record, fine); league_season reads api_standings raw |
| 16 | A move, the plan | decision | dec_next -> dec_plan | Action; dec_start; the loop needed (each step depends on the last) but small |
| 17 | How good a move was | grade, board | the same views at past t | six or seven replay drivers (at_locks/decisions, backtest, replay, phantom_path, variant_ahead, sql.py --at); only sql.py --at is "same views at past t" |
| 18 | A fake league for tests | (tests) | rows in the fact tables | about 20 hand-built Universe/Market/Bootstrap/LeagueState objects, _by_hand, sim.report's swaps |
| 19 | Explaining the board | grade, board | board views, datasette | the board reaches down to api_* and league-at-t views; several grade tables read only by datasette |

What a run told you (sim_runs, decision_plan, decision_option, sim_standings,
sim_players) is a fact of its time, not a copy: kept.

Rows 1 and 3-8 are one concept, the league at t, built twice: as SQL at a
cut and as Python (League, Market, LeagueState, Universe, the backtest's
Day). "What if I had moved otherwise" is held as a mutated Day instead of
an input. That is most of the copies, the 10 min backtest and its
self-graded 560.

## The plan by layer (2026-11-03)

Bottom up; each item one concept to one home, the copies deleted in the
same step.

1. t: one t. cut() reads the single t a run or a replay sets; look.cut is
   it; no other clock in a view or in grading.
2. My moves as an input: the squad and cash at t are the facts at t plus
   my moves (the plan's, the phantom's) as rows. Replaces dec_mine,
   dec_state's cash, Day, with_squad, League, Market, LeagueState,
   Universe's state.
3. Auction and sale outcomes at t: one cut-bound auction outcome; a sale
   at seen's fetches at t.
4. One replay: the backtest is the same views at each past t, the
   phantom's moves its input; the other replay drivers go or become a
   view over it.
5. Forecast: pts*p once (cell); price fits a function of t, not of which
   builds ran.
6. Facts: identity in SQL only; one "played"; derived tables out of facts.
7. Decision and tests: the plan loop with no Action; tests seed fact rows.

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
