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
| What money is worth | MONEY_RATE (decide.py:29), k.money_rate (schema.sql:725), Board.lam (LP dual, decide.py:221), Market.money / later (market.py:69-95), the waiting view (schema.sql:568-594), board_rows and board_mine.wait_bids re-deriving points - rate x money (schema.sql:1438, 1594, 1624), why_sell (datasette.json) |
| The eleven | gain.ELEVEN (gain.py:30-44, via Outlook.xi), the program's own formation LP (decide.py:271-315), board_eleven / field_basis / lineup_gap (schema.sql:1466-1506); shortfall vs _fieldable |
| Debt room, 20% of team value | grading.acted (grading.py:326-335), board_money (schema.sql:1517-1532), board_resale.can_spend, board_head.can_bid; not a constant; the program plans with no debt at all |
| A sale's proceeds with no offer | value (market.py:59), recent offer ratio (waiting, schema.sql:586-588), avg ratio (grading.py:270, 283) |
| Selling to clear a debt | grading.closed via program, board_resale greedy, why_sell, cash_cliffs |
| Resale picks | grading.resales (cost = a bid) vs board_resale (cost = asking), one RESALE_MARGIN |
| The accept rule for a board line | board_json accepts (SQL), server/index.js:160-169, act.py:200-220 (+ kind/ACTS), Fantasy.jsx BUTTON (which win goes with which amount, also in SQL) |
| The board itself | Python Board + sim docs, then SQL board_json, then server notify text, then Fantasy.jsx formatting (two number formatters in one file) |
| The line-up message | lineup_alert (basis 'now'), board_rows field line (basis 'plan' before the last auction), board_head.ping |
| Shrink toward a prior | stats.shrink, pick_later and start_calibration in SQL, a test restating it |
| Matches played | scored.games vs season_record.apps |
| The forecast at a past moment | forecast_cells (backtest, replay) and _built (board, decisions), built twice a lock with different results |
| A past moment replayed | phantom_path, phantom_season, decisions, replay, backtest, sim_calibration |
| Caches | kept answers, kept built universes, built @cache, grading._AT, _first_listed, facts (price_step, momentum_day), fit tables (momentum_fit, price_error_fit, price_trend_fit) + the "fits" marker; momentum is held four ways |
| Player identity | crosswalk / ff_identity views, attach_slugs' second pass, app_links value matching, lineup_gap matching by name inline; club names matched three ways |
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
   league id: a second league overwrites the first.
7. Grading values money over hmax updates, the plan over MONEY_H (7).
8. p_now may land on a jornada already behind (schedule.py:72-86 vs
   assemble.py:356-360); unconfirmed.
9. ruff is configured and never run; the auth.py suite may test nothing.
10. Players a manager lists for sale (route 'listed') are never offered:
    acquire() drops every owned player. Letting them in needs the backtest
    to settle such a sale (a seller's choice, not the game's auction): tried
    2026-10-31, it read them as free wins (351 moves, 180 points).

## Dead or idle

- lfg-publish's markdown reports branch and its comments; .runtime/ and
  src/.runtime/ (September); the zero-byte lfg.db at the root; data/tidy
  parsed_points.jsonl, startcal.json, .parse_spill/; scratch copies under
  ~/.cache; .claude/RESUME.md; the fixture's `backup` key in
  fantasyBoard.test.js; schema.sql's verdict column; KAPPAS mentions
  (tools/perf.py:8).
- Views nobody reads: current_api_lineup, names, history_points,
  history_matches, history_market, history_api_activity.
- Constants written to model_constants that no view reads (SHRINK_K,
  AVAIL_K, PROMOTED_*, FIX_BAND, MIN_POOL, PERSISTENT_SHARE, ...);
  PROMOTED_DISCOUNT cannot be varied.
- Board fields nothing reads on the phone: plan_gain, standings[].mean.
- README: grading flags that do not exist, at_risk, Universe.bid,
  open_clauses, "seasons are simulated", the module layout; stale diagrams
  (funnel.mmd, modules.mmd).

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
