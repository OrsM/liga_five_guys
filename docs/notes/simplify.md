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

## Modules and contracts (2026-11-04; the frame every change is judged in)

Five modules. Each reads the facts and earlier modules' contracts only, owns its
own tables and views, and is judged by its own test and measure. The money
replay is deleted (2026-11-04): one season path swung about ±19 points a
jornada and ranked nothing. The decision's measure is `grading.py ceiling`: at
each past lock, the real points of the best eleven for standing still (floor),
the plan on the forecast (ours) and the same plan_rows on real points
(ceiling); share = (ours - floor) / (ceiling - floor). Its seeded end-to-end
test (grading.py _selftest_ceiling) is the smoke test of the wiring.

| # | Module | Reads | Its contract (what the next reads) | Test | Measure |
|---|---|---|---|---|---|
| 1 | Ingest | raw pages, the API | facts: player_club, player_value, player_status, player_points, club_fixture, owners, cash, listings, offers, auction closes; all timestamped | fixed raw → expected rows; one row per key; no nulls in keys | parser health |
| 2 | Forecast | facts at t (nothing after t) | cell(player, jornada, pts, p) | seeded facts → cells | accuracy against real points (RMSE, r), next jornada and five ahead |
| 3 | Decision | cell, the league at t (squads, cash, market, bid odds), moves | plan(step, kind, player, amount, win, worth), kept per run with each step's order (ord), cash after it, when it is due and why (schedule), the blockers on two futures (nothing done, plan done) and what skipping each step leaves | seeded league → plan | `grading.py ceiling` (floor, ours, ceiling on real points, a lock at a time); expected gap: the plan's eleven's e against standing pat's; checks: cash ≥ 0 at the lock, squad ≤ SQUAD_MAX, a legal eleven |
| 4 | Settlement | (removed 2026-11-05: nothing read it; when a step is due is the Decision's `schedule`, and what happened is ingest's `auction_results`) | | | |
| 5 | Board | plan, standings, moves | the page (board_json) | snapshot | none: correctness only |

The same modules run live and in the replay; only t and the moves differ.

Every contract is a SQL table or view. Ingest's output, the forecast, settlement and the board are SQL. The decision's search (the best eleven for each candidate squad, every set of buys with the sales that pay for it) is one pure Python function: it reads its input rows (cell, squads, cash, market, bid odds) and writes plan rows. It holds no copy of a table, and its test seeds rows. Python is also left for fetching and parsing pages, and for driving the app.

**Rules.**
- A module reads ingest's facts, t and earlier modules' contracts; never a later module, never a scraped table (only ingest reads those).
- A test seeds its module's inputs as rows. No Python mirror of a table (Universe, Market, LeagueState, Bootstrap).
- A change to a module is judged by that module's measure. Old output is a score to beat, not to reproduce.
- A tool checks the frontiers: it reads each view's dependencies from SQLite and fails on a read past the frontier.

**Exports** (the contracts, enforced by tools/frontiers.py, which tools/selftests.sh runs: it fails on a read of another module's name not listed here, in views and in src/*.py production code; no exemptions (grading is the last module; assemble, sim, db, facts and fixtures implement several modules and are none's); `python tools/frontiers.py --table` regenerates this table):

| Module | Exports (its contract) |
|---|---|
| ingest | app_lineup, appearances, auction_results, cash, club_fixture, club_rating, jornada_calendar, kickoffs, league_config, lineup_snapshots, listing_close, listing_ends, listings, look, next_lock, offer_ratios, offers, owners, player_club, player_roster, player_status, player_value, price_history, price_paid, scored, season_points, season_record, squads, standings |
| forecast | cell, forecast_e, price_carry, price_trend, price_trend_daily, standin_cell |
| decision | decision_blocker, decision_facts, decision_option, decision_plan, decision_players, decision_run, decision_sale, decision_skip, decision_standings, decision_waiting, league_jornada |
| board | board_json |
| grading | (none yet) |


**Board and grading read contracts (2026-11-04, wave 2).** The root cause: the board was built on the replay's stand-in tables (sim_*), a later module's, and on decision internals (bid_odds, recent_offers, nights_left, waiting). The design:
- The board needs, for the last run: the plan, each move alone (what the plan was chosen from), the players the run looked at with the facts it saw, what waiting is worth, the standings forecast, what the plan leaves (finish, chance of first, cash, what more to bid), and the offer nights left. It needs no live view of the decision.
- The decision exports all of it as its plan-detail contract, a run each, written once by `decision.record(run)` (the only place that reads the decision's internals for it): decision_plan (step, kind, player, amount, win, worth, after), decision_option (every move alone, plus asking_win, the chance of winning at his asking price, and beats, the share of the game's recent offers the offer standing on him beats), decision_players (start, next, season, mine, xi), decision_facts (the facts the run saw: a view over `seen` at the run's cut), decision_waiting, decision_standings (every manager's season if you do nothing) and decision_run (cut, finish_after, p_first_after, cash_after, can_bid, nights, first). sim_players/sim_standings/sim_waiting became decision_*; sim_runs keeps only the run's forecast moment, the rest of it moved to decision_run (db._renamed/_kept move a live file once).
- The replay produces the same contracts at an earlier t instead of private tables the board reads: grading sets t (set_now) and calls the same `decision.plan`/`decision.options`, reads `cell`, `standings`, `scored`, `jornada_calendar`, `player_roster`, `price_trend`, `league_jornada` (exported), and keeps its own records of what it read: grade_cell (a lock's cell contract, a row a player and jornada: replaces grade_moment, which pointed into the forecast's private store) and replay_cell (the next jornada's cells at each line-up snapshot, lineup_snapshots, an ingest fact view; replaces the forecast moments `replayed` read). `grade_forecast` and `replayed` are views over those and `scored`.
- Not a contract, so not exported: bid_odds, recent_offers, nights_left, waiting, league_standings, league_season (the decision's internals; league_standings is the decision's, it computes p_first_after, and left the board's names in tools/frontiers.py).
- Left in other slices: forecast (run_forecast reads sim_runs.moment), decision (4), assemble, db, facts, fixtures (listed below).

**Frontiers crossed today (to fix, one module at a time):**
- Decision reads ingest: done (2026-11-04). player_facts, nights_left, bid_odds, league_season read ingest's fact views (player_roster, listings, offers, squads, standings); offer_ratios is ingest's. tools/frontiers.py lists none.
- Decision's tests: done (2026-11-04). decision._selftest seeds rows (plan_rows on rows, seed_league); no Universe, Market, LeagueState or Bootstrap remains in src or tools. The expected gap is decision.expected_gap.
- Forecast and decision crossings: done (2026-11-04). run_forecast was a run's forecast (grading's): now sim_forecast. league_xi/season/jornada read the persistent share through const() (not score_spread) and a position's stand-in cell, part of the cell contract (standin_cell exported). The expected gap is in `grading.py ceiling`'s headline (measure_log key expected_gap).
- Settlement decides: on a lost bid, grading.py drops the paired sales and runs decision.plan(sales_only=True) (grading.py:292-297). That sold Agoume, Moncayola, and Maffeo the night he was bought.
- The rest: the frontier check list below.

**Frontier check (2026-11-04, no exemptions; the exports above cut to the note's contracts)** (tools/frontiers.py, `python tools/frontiers.py` prints this list, red until it is empty). The rule: a module may read the facts, t (look) and any earlier module's contract; it may not read a later module, nor a scraped table (api_*, current_*, market, lineups...) from outside ingest; a file of no one module (assemble, sim, db, facts, fixtures) reads exports only. Grouped by the reading module, file:line. 77 crossings before wave 2's board slice; now 28 (forecast 1, decision 4, assemble.py 8, db.py 2, facts.py 12, fixtures.py 1; ingest, settlement, board, grading and sim.py 0). The list below is the state before that slice:

forecast (1):
- src/ffcore/schema.sql:590 view run_forecast reads sim_runs (grading, a later module)

decision (9):
- src/ffcore/decide.sql:230 view league_xi reads standin_cell (forecast, not exported)
- src/ffcore/decide.sql:236 view league_xi reads standin_cell (forecast, not exported)
- src/ffcore/decide.sql:246 view league_season reads score_spread (forecast, not exported)
- src/ffcore/decide.sql:289 view league_jornada reads score_spread (forecast, not exported)
- src/ffcore/schema.sql:1023 view bid_odds reads price_trend_fit (forecast, not exported)
- src/ffcore/schema.sql:1027 view bid_odds reads price_trend_fit (forecast, not exported)
- src/ffcore/schema.sql:1033 view bid_odds reads price_trend (forecast, not exported)
- src/ffcore/schema.sql:675 view waiting reads price_carry (forecast, not exported)
- src/ffcore/schema.sql:733 view player_facts reads price_trend (forecast, not exported)

board (26):
- src/ffcore/decide.sql:259 view league_standings reads league_season (decision, not exported)
- src/ffcore/decide.sql:278 view league_standings reads my_cash (decision, not exported)
- src/ffcore/decide.sql:280 view league_standings reads seen_cash (decision, not exported)
- src/ffcore/schema.sql:1493 view board_run reads sim_runs (grading, a later module)
- src/ffcore/schema.sql:1503 view board_players reads sim_facts (grading, a later module)
- src/ffcore/schema.sql:1503 view board_players reads sim_players (grading, a later module)
- src/ffcore/schema.sql:1515 view board_buys reads decision_option (decision, not exported)
- src/ffcore/schema.sql:1516 view board_buys reads sim_facts (grading, a later module)
- src/ffcore/schema.sql:1528 view board_mine reads recent_offers (decision, not exported)
- src/ffcore/schema.sql:1529 view board_mine reads nights_left (decision, not exported)
- src/ffcore/schema.sql:1539 view board_mine reads decision_option (decision, not exported)
- src/ffcore/schema.sql:1540 view board_mine reads sim_waiting (grading, a later module)
- src/ffcore/schema.sql:1599 view board_league reads sim_standings (grading, a later module)
- src/ffcore/schema.sql:1620 view lineup_gap reads app_lineup (ingest, not exported)
- src/ffcore/schema.sql:1624 view lineup_gap reads app_player (ingest, not exported)
- src/ffcore/schema.sql:1625 view lineup_gap reads name_player (ingest, not exported)
- src/ffcore/schema.sql:1626 view lineup_gap reads name_player (ingest, not exported)
- src/ffcore/schema.sql:1717 view board_rows reads sim_facts (grading, a later module)
- src/ffcore/schema.sql:1753 view board_head reads sim_facts (grading, a later module)
- src/ffcore/schema.sql:1755 view board_head reads sim_runs (grading, a later module)
- src/ffcore/schema.sql:1756 view board_head reads sim_runs (grading, a later module)
- src/ffcore/schema.sql:1758 view board_head reads sim_runs (grading, a later module)
- src/ffcore/schema.sql:1760 view board_head reads sim_runs (grading, a later module)
- src/ffcore/schema.sql:1761 view board_head reads sim_facts (grading, a later module)
- src/ffcore/schema.sql:1819 view board_json reads bid_odds (decision, not exported)
- src/ffcore/schema.sql:1833 view board_json reads sim_facts (grading, a later module)

grading (19):
- src/ffcore/schema.sql:209 view sim_facts reads seen (decision, not exported)
- src/ffcore/schema.sql:354 view grade_forecast reads forecast_moment (forecast, not exported)
- src/ffcore/schema.sql:355 view grade_forecast reads forecast_e (forecast, not exported)
- src/ffcore/schema.sql:356 view grade_forecast reads forecast_player (forecast, not exported)
- src/ffcore/schema.sql:388 view replay_moments reads lineups (ingest, a scraped table)
- src/ffcore/schema.sql:390 view replay_moments reads round_locks (ingest, not exported)
- src/ffcore/schema.sql:409 view replayed reads forecast_moment (forecast, not exported)
- src/ffcore/schema.sql:411 view replayed reads forecast_e (forecast, not exported)
- src/ffcore/schema.sql:412 view replayed reads forecast_player (forecast, not exported)
- src/ffcore/schema.sql:414 view replayed reads jornadas_done (ingest, not exported)
- src/grading.py:129 reads dec_rows (decision, not exported)
- src/grading.py:131 reads dec_rows (decision, not exported)
- src/grading.py:134 reads seen (decision, not exported)
- src/grading.py:256 reads api_standings (ingest, a scraped table)
- src/grading.py:264 reads league_jornada (decision, not exported)
- src/grading.py:60 reads player_jornadas (ingest, not exported)
- src/grading.py:66 reads jornadas_done (ingest, not exported)
- src/grading.py:79 reads forecast_cell (forecast, not exported)
- src/grading.py:93 reads jornadas_done (ingest, not exported)

no module: assemble.py (13):
- src/assemble.py:101 reads fitted (forecast, not exported)
- src/assemble.py:102 reads price_carry (forecast, not exported)
- src/assemble.py:103 reads price_trend (forecast, not exported)
- src/assemble.py:124 reads seen (decision, not exported)
- src/assemble.py:131 reads player_facts (decision, not exported)
- src/assemble.py:131 reads sale_odds (decision, not exported)
- src/assemble.py:133 reads bid_odds (decision, not exported)
- src/assemble.py:176 reads forecast_now (forecast, not exported)
- src/assemble.py:179 reads forecast_moment (forecast, not exported)
- src/assemble.py:180 reads sim_runs (grading, not exported)
- src/assemble.py:181 reads grade_moment (grading, not exported)
- src/assemble.py:79 reads price_trend_now (forecast, not exported)
- src/assemble.py:99 reads momentum (forecast, not exported)

no module: db.py (3):
- src/ffcore/db.py:285 reads grade_moment (grading, not exported)
- src/ffcore/db.py:285 reads sim_runs (grading, not exported)
- src/ffcore/db.py:292 reads forecast_moment (forecast, not exported)

no module: facts.py (12):
- src/ffcore/facts.py:24 reads price_step (forecast, not exported)
- src/ffcore/facts.py:29 reads market (ingest, a scraped table)
- src/ffcore/facts.py:30 reads market (ingest, a scraped table)
- src/ffcore/facts.py:31 reads market (ingest, a scraped table)
- src/ffcore/facts.py:46 reads momentum_day (forecast, not exported)
- src/ffcore/facts.py:49 reads momentum_pairs (forecast, not exported)
- src/ffcore/facts.py:64 reads market (ingest, a scraped table)
- src/ffcore/facts.py:76 reads crosswalk (ingest, not exported)
- src/ffcore/facts.py:79 reads lineups (ingest, a scraped table)
- src/ffcore/facts.py:80 reads starters (ingest, a scraped table)
- src/ffcore/facts.py:89 reads ff_identity_kept (ingest, not exported)
- src/ffcore/facts.py:90 reads ff_identity_now (ingest, not exported)

no module: fixtures.py (1):
- src/ffcore/fixtures.py:32 reads forecast_cell (forecast, not exported)

no module: sim.py (6):
- src/sim.py:26 reads league_standings (board, not exported)
- src/sim.py:31 reads dec_when (decision, not exported)
- src/sim.py:471 reads look_players (decision, not exported)
- src/sim.py:473 reads dec_rows (decision, not exported)
- src/sim.py:476 reads dec_cash (decision, not exported)
- src/sim.py:478 reads waiting (decision, not exported)
**Forecast (2026-11-04)**: its crossings are fixed (late_arrivals, standin_cell, market_daily, price_day_now, price_rows read ingest's facts; auction_results no longer reads the forecast). The price trend and momentum are views at t (no kept momentum_fit / price_trend_fit; bid_odds reads price_trend_daily, a past close's trend); a cell's e and v are worked out once (cell_pool; stand-ins are its rows with no player).

**Order.** (1) The frontier check, to get the full list. (2) Settlement: own its rule and fix the lost-bid sell-off. (3) Decision: tests on rows, the expected gap as its measure. (4) Forecast: tests on rows. (5) Ingest: facts as tables at fetch. Each step is done by one agent with a short brief, and is finished when its module's test and measure pass and its old copy is deleted.

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
| 1 | t, the moment | league at t | one t, an argument | Done (2026-11-04): `look` is a view over cut(); there is one t. |
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
5. Forecast: SQL over a few facts at t (schema.sql), made into
   forecast_cell once a decision (no code hash). What it reads, and its home:

   | Attribute | Fact it comes from | Its one home |
   |---|---|---|
   | club, position, name | futbolfantasy market at t | player_club |
   | value | futbolfantasy market at t | player_value |
   | points, appearances, squads this season | points totals, starters | player_jornadas -> season_record |
   | last season's points, matches | season_points | last_season |
   | start %, listed, status, note | futbolfantasy line-ups at t | player_status |
   | left the league | the app's players at t | player_status |
   | opponent, kickoff, jornadas left | fixtures at t | club_fixture |
   | goals for and against | results_history at t | club_rating |
   | SHRINK_K, PICK_K, NEUTRAL_START, ABSENT_START, FIXTURE_K | module constants, read by const(NAME) | k |

   The forecast: forecast_ppm (value prior -> last season -> this season,
   each a shrink), forecast_picked (his listing, or neutral if flagged,
   pulled toward his record), forecast_fit (his prognosis read literally,
   prognosis() the one Python helper: the note's regex), the fixture (the
   opponent's goals against for attackers, 1 / its goals for for
   defenders, each shrunk toward the league's), and forecast_now, the cell:
   pts = max(0, ppm x fixture), p = picked x fit.
   Measured (2026-11-04, test copy, 10 locks j2-11, player-jornadas both
   forecast): next jornada rmse 2.806 vs the Python's 2.800, r 0.591 both;
   five ahead rmse 2.929 vs 2.903, r 0.529 vs 0.541. Backtest money: 192
   moves, 4 lost, +476 vs +497 standing (the Python: 145, 5, +578).
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

## Recommender and the plan (inside Decision)

Two files, one decision, each with its own job (2026-11-06).

- `src/recommender.py` owns the candidates. In: `read_inputs()` rows (dec_player, dec_odds, cell). Out:
  `Cands(bids, alone)`: each player on offer and his bids as (bid, win chance), and every bid valued alone from the
  squad as it is (the board's rows; worth = win x gain). It picks nothing and decides nothing about money.
- `decision.plan_rows(inp)` chooses the squad to hold at the lock. It tries every set of buys (at most three, each
  worth something alone, from the recommender's bids), and for each the sales that pay for it (`decision.cover`: the
  sets of sales that bring the cash at the lock to zero or more and free the room, the few that lose the fewest
  points; never "the one that covers it alone"). Every mix is judged once, by `worth_on` (the one objective;
  `expected_gap` and `evaluate` are the same), and the best is the plan; standing pat is one of the choices (in
  debt, it is not). A set of buys cannot gain more than its buys do alone, which prunes the sets. A sale that leaves
  no legal eleven (`decision.fieldable`, the check `after` makes too) is no option: if nothing clears the debt,
  the debt is the blocker. Money is no goal.
- The test is exact: `decision._selftest_search` compares the plan with every mix of any buys and up to three
  sales on seeded random leagues (feasible, at least standing pat, equal to the best). Two bounds remain, both on
  the live squad and neither in that test: at most three buys, and cover shortlists the ten sellable players who
  lose the fewest points per euro; check them against the wider search on real inputs before raising either.

*The dated step log (steps 1–9, timings, estimates) is in `archive/simplify-log.md`.*
