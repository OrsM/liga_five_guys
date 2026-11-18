-- What earlier versions made and nothing reads now: a database made by them drops it here.
DROP VIEW IF EXISTS formation;
DROP VIEW IF EXISTS slot_bounds;
DROP VIEW IF EXISTS board_blocker_word;
DROP VIEW IF EXISTS board_blockers;
DROP VIEW IF EXISTS board_buy_facts;
DROP VIEW IF EXISTS board_buys;
DROP VIEW IF EXISTS board_clauses;
DROP VIEW IF EXISTS board_eleven;
DROP VIEW IF EXISTS board_flip;
DROP VIEW IF EXISTS board_head;
DROP VIEW IF EXISTS board_json;
DROP VIEW IF EXISTS board_league;
DROP VIEW IF EXISTS board_mine;
DROP VIEW IF EXISTS board_plan;
DROP VIEW IF EXISTS board_players;
DROP VIEW IF EXISTS board_rows;
DROP VIEW IF EXISTS board_run;
DROP VIEW IF EXISTS board_sell_facts;
DROP VIEW IF EXISTS board_skip;
DROP VIEW IF EXISTS board_squad;
DROP VIEW IF EXISTS board_step;
DROP VIEW IF EXISTS board_trace;
DROP VIEW IF EXISTS dec_rows;
DROP VIEW IF EXISTS dec_squad;
DROP VIEW IF EXISTS decision_facts;
DROP VIEW IF EXISTS grade_error;
DROP VIEW IF EXISTS league_jornada;
DROP VIEW IF EXISTS league_season;
DROP VIEW IF EXISTS league_squad;
DROP VIEW IF EXISTS league_standings;
DROP VIEW IF EXISTS league_xi;
DROP VIEW IF EXISTS lineup_alert;
DROP VIEW IF EXISTS lineup_gap;
DROP VIEW IF EXISTS lineup_names;
DROP VIEW IF EXISTS listing_ends;
DROP VIEW IF EXISTS lock_due;
DROP VIEW IF EXISTS lock_record;
DROP VIEW IF EXISTS look_players;
DROP VIEW IF EXISTS my_xi;
DROP VIEW IF EXISTS next_cell;
DROP VIEW IF EXISTS offer_spread;
DROP VIEW IF EXISTS persistence_by_run;
DROP VIEW IF EXISTS sim_forecast;
DROP VIEW IF EXISTS standin_cell;
DROP TABLE IF EXISTS board_history;
DROP TABLE IF EXISTS dec_hold;
DROP TABLE IF EXISTS dec_mine;
DROP TABLE IF EXISTS dec_shape;
DROP TABLE IF EXISTS dec_xp;
DROP TABLE IF EXISTS moves;
DROP TABLE IF EXISTS phantom_jornadas;
DROP TABLE IF EXISTS season_points;
DROP TABLE IF EXISTS board_history_moves;
DROP TABLE IF EXISTS board_xi;
DROP TABLE IF EXISTS dec_plan;
DROP TABLE IF EXISTS dec_start;
DROP TABLE IF EXISTS decision_blocker;
DROP TABLE IF EXISTS decision_flip;
DROP TABLE IF EXISTS decision_option;
DROP TABLE IF EXISTS decision_plan;
DROP TABLE IF EXISTS decision_players;
DROP TABLE IF EXISTS decision_run;
DROP TABLE IF EXISTS decision_sale;
DROP TABLE IF EXISTS decision_skip;
DROP TABLE IF EXISTS decision_standings;
DROP TABLE IF EXISTS decision_waiting;
DROP TABLE IF EXISTS grade_decisions;
DROP TABLE IF EXISTS sim_calibration;
DROP TABLE IF EXISTS sim_runs;
DROP TABLE IF EXISTS seen_odds;
DROP TABLE IF EXISTS dec_odds;
DROP VIEW IF EXISTS bid_odds;
DROP TABLE IF EXISTS forecast_variant;
DROP VIEW IF EXISTS replay_gap;
DROP VIEW IF EXISTS replay_lead;
DROP VIEW IF EXISTS lead_buckets;
DROP VIEW IF EXISTS replayed;
DROP VIEW IF EXISTS replay_moments;
DROP VIEW IF EXISTS lineup_snapshots;
DROP TABLE IF EXISTS replay_cell;
DROP TABLE IF EXISTS variants;
DROP VIEW IF EXISTS nights_left;
DROP VIEW IF EXISTS waiting;
DROP VIEW IF EXISTS sale_odds;
DROP TABLE IF EXISTS model_constants;
DROP TABLE IF EXISTS momentum_fit;
DROP TABLE IF EXISTS price_trend_fit;
DROP VIEW IF EXISTS fitted;
DROP VIEW IF EXISTS price_trend_now;
DROP VIEW IF EXISTS recent_offers;
DROP VIEW IF EXISTS start_calibration;
DROP VIEW IF EXISTS availability_tally;
DROP VIEW IF EXISTS last_fit;
DROP VIEW IF EXISTS fit_rate;
DROP VIEW IF EXISTS start_outcomes;
DROP VIEW IF EXISTS forecast_ppm;
DROP VIEW IF EXISTS forecast_picked;
DROP VIEW IF EXISTS dec_odds_seen;
DROP VIEW IF EXISTS dec_offer_seen;
DROP TABLE IF EXISTS dec_offer;
DROP INDEX IF EXISTS market_observed;

-- removed with the sweep (#80): an existing database drops them on its next connect
DROP VIEW IF EXISTS app_lineup;
DROP VIEW IF EXISTS appearances;
DROP VIEW IF EXISTS auction_looks;
DROP VIEW IF EXISTS auction_results;
DROP VIEW IF EXISTS bid_changes;
DROP VIEW IF EXISTS blind_results;
DROP VIEW IF EXISTS cash;
DROP VIEW IF EXISTS cell;
DROP VIEW IF EXISTS cell_pool;
DROP VIEW IF EXISTS cell_total;
DROP VIEW IF EXISTS club_fixture;
DROP VIEW IF EXISTS club_games;
DROP VIEW IF EXISTS club_rating;
DROP VIEW IF EXISTS clubs_played;
DROP VIEW IF EXISTS dec_arrivals;
DROP VIEW IF EXISTS price_rows;
DROP VIEW IF EXISTS dec_auction_seen;
DROP VIEW IF EXISTS dec_bid_seen;
DROP VIEW IF EXISTS dec_close_seen;
DROP VIEW IF EXISTS dec_curve_seen;
DROP VIEW IF EXISTS dec_lineup_seen;
DROP VIEW IF EXISTS dec_lock_seen;
DROP VIEW IF EXISTS dec_money_seen;
DROP VIEW IF EXISTS dec_opportunities;
DROP VIEW IF EXISTS dec_player_seen;
DROP VIEW IF EXISTS dec_targets;
DROP VIEW IF EXISTS feed_ledger;
DROP VIEW IF EXISTS forecast_e;
DROP VIEW IF EXISTS forecast_fit;
DROP VIEW IF EXISTS forecast_now;
DROP VIEW IF EXISTS forecast_rate;
DROP VIEW IF EXISTS forecast_used;
DROP VIEW IF EXISTS grade_accuracy;
DROP VIEW IF EXISTS grade_forecast;
DROP VIEW IF EXISTS jornada_calendar;
DROP VIEW IF EXISTS jornadas_done;
DROP VIEW IF EXISTS k;
DROP VIEW IF EXISTS kickoffs;
DROP VIEW IF EXISTS last_season;
DROP VIEW IF EXISTS late_arrivals;
DROP VIEW IF EXISTS ledger;
DROP VIEW IF EXISTS listing_close;
DROP VIEW IF EXISTS listings;
DROP VIEW IF EXISTS look;
DROP VIEW IF EXISTS market_daily;
DROP VIEW IF EXISTS market_read;
DROP VIEW IF EXISTS match_jornada;
DROP VIEW IF EXISTS momentum;
DROP VIEW IF EXISTS momentum_pairs;
DROP VIEW IF EXISTS momentum_sums;
DROP VIEW IF EXISTS my_cash;
DROP VIEW IF EXISTS my_squad;
DROP VIEW IF EXISTS next_lock;
DROP VIEW IF EXISTS offer_ratios;
DROP VIEW IF EXISTS offers;
DROP VIEW IF EXISTS owners;
DROP VIEW IF EXISTS player_club;
DROP VIEW IF EXISTS player_facts;
DROP VIEW IF EXISTS player_jornadas;
DROP VIEW IF EXISTS player_roster;
DROP VIEW IF EXISTS player_status;
DROP VIEW IF EXISTS player_value;
DROP VIEW IF EXISTS ppm_prior;
DROP VIEW IF EXISTS price_ahead;
DROP VIEW IF EXISTS price_before;
DROP VIEW IF EXISTS price_carry;
DROP VIEW IF EXISTS price_day_now;
DROP VIEW IF EXISTS price_forecast;
DROP VIEW IF EXISTS price_grade;
DROP VIEW IF EXISTS price_history;
DROP VIEW IF EXISTS price_hmax;
DROP VIEW IF EXISTS price_live;
DROP VIEW IF EXISTS price_obs;
DROP VIEW IF EXISTS price_open;
DROP VIEW IF EXISTS price_paid;
DROP VIEW IF EXISTS price_past;
DROP VIEW IF EXISTS price_series;
DROP VIEW IF EXISTS price_slope;
DROP VIEW IF EXISTS price_trend;
DROP VIEW IF EXISTS price_trend_daily;
DROP VIEW IF EXISTS round_locks;
DROP VIEW IF EXISTS rounds_ahead;
DROP VIEW IF EXISTS score_spread;
DROP VIEW IF EXISTS scored;
DROP VIEW IF EXISTS season_record;
DROP VIEW IF EXISTS squad_minutes;
DROP VIEW IF EXISTS squads;
DROP VIEW IF EXISTS standings;
DROP VIEW IF EXISTS users;
DROP TABLE IF EXISTS seen;
DROP TABLE IF EXISTS seen_cash;
DROP TABLE IF EXISTS seen_auction;
DROP TABLE IF EXISTS seen_curve;
DROP TABLE IF EXISTS forecast_moment;
DROP TABLE IF EXISTS forecast_player;
DROP TABLE IF EXISTS forecast_cell;
DROP TABLE IF EXISTS price_step;
DROP TABLE IF EXISTS momentum_day;
DROP TABLE IF EXISTS horizons;
DROP TABLE IF EXISTS dec_state;
DROP TABLE IF EXISTS dec_player;
DROP TABLE IF EXISTS dec_curve;
DROP TABLE IF EXISTS dec_auction;
DROP TABLE IF EXISTS dec_bid;
DROP TABLE IF EXISTS dec_close;
DROP TABLE IF EXISTS dec_lock;
DROP TABLE IF EXISTS dec_lineup;
DROP TABLE IF EXISTS dec_money;
DROP TABLE IF EXISTS league_config;

-- data/lfg.db, applied on every connect (ffcore/db.py). Only adds: a
-- changed table is dropped by hand and rebuilt (docs/notes/lfg.md).

-- One row per run of src/run.py. finished and ok stay NULL while it runs.
CREATE TABLE IF NOT EXISTS runs (
    run_id     INTEGER PRIMARY KEY,
    started    TEXT NOT NULL,     -- wall clock, UTC
    as_of      TEXT NOT NULL,     -- the clock the model read (LFG_NOW when pinned)
    trigger    TEXT NOT NULL,     -- LFG_TRIGGER: service, button
    commit_sha TEXT NOT NULL,     -- the last commit that changed the code
    finished   TEXT,
    ok         INTEGER,
    peak_mb    REAL
);

-- A stage's seconds, and every count it returned.
CREATE TABLE IF NOT EXISTS run_measures (
    run_id  INTEGER NOT NULL REFERENCES runs(run_id),
    stage   TEXT NOT NULL,
    measure TEXT NOT NULL,
    value   REAL NOT NULL,
    PRIMARY KEY (run_id, stage, measure)
);

-- The headline numbers of `grading.py accuracy`, kept a run at a time:
-- measure ('accuracy'), key (rmse@5, star_rmse@5...), the
-- code's short commit. (commit_sha: COMMIT is an SQL keyword.)
CREATE TABLE IF NOT EXISTS measure_log (
    at         TEXT NOT NULL,     -- wall clock, UTC
    commit_sha TEXT NOT NULL,
    measure    TEXT NOT NULL,
    key        TEXT NOT NULL,
    value      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS measure_log_key ON measure_log (measure, key, at);

-- Each measure's latest value beside the one before it.
DROP VIEW IF EXISTS measure_latest;
CREATE VIEW measure_latest AS
WITH r AS (SELECT *, row_number() OVER (PARTITION BY measure, key ORDER BY at DESC, rowid DESC) AS n FROM measure_log)
SELECT a.measure, a.key, a.value, a.at, a.commit_sha, p.value AS previous, p.at AS previous_at,
       p.commit_sha AS previous_commit, a.value - p.value AS change
FROM r a LEFT JOIN r p ON p.measure = a.measure AND p.key = a.key AND p.n = 2
WHERE a.n = 1;

-- Every constant and fitted value a run was built with (assemble.params),
-- broom's tidy(): drift and "what changed" are queries over this.
CREATE TABLE IF NOT EXISTS params (
    run_id    INTEGER NOT NULL REFERENCES runs(run_id),
    component TEXT NOT NULL,     -- module, or module.fit
    term      TEXT NOT NULL,     -- '' for a single value
    value     REAL NOT NULL,
    kind      TEXT NOT NULL,     -- constant, fitted
    n         INTEGER,           -- observations behind it, where known
    PRIMARY KEY (run_id, component, term)
);

-- How the forecast did, each run: calibration of the stated chance against what happened (by scope and bin of the
-- stated chance; drift flags a gap the counts cannot explain) and the Brier score against the pooled distribution.
CREATE TABLE IF NOT EXISTS auction_calibration (
    run_id   INTEGER NOT NULL,
    scope    TEXT NOT NULL,
    bin      TEXT NOT NULL,
    n        INTEGER NOT NULL,
    stated   REAL NOT NULL,
    observed REAL NOT NULL,
    drift    INTEGER NOT NULL,
    PRIMARY KEY (run_id, scope, bin)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS auction_brier (
    run_id INTEGER NOT NULL,
    scope  TEXT NOT NULL,
    n      INTEGER NOT NULL,
    model  REAL NOT NULL,
    pooled REAL NOT NULL,
    PRIMARY KEY (run_id, scope)
) WITHOUT ROWID;

-- grading, when run.py grade finds something new: each past lock's forecast
-- (the cell contract, read at the lock by grading.backtest), a row a player and
-- jornada done up to GRADE_AHEAD past the lock: what was expected of him (e),
-- his chance of playing (NULL in grades kept before 2026-10-17), and whether he
-- was listed injured, doubtful or suspended then (player_status).
CREATE TABLE IF NOT EXISTS grade_cell (
    run_id    INTEGER NOT NULL REFERENCES runs(run_id),
    from_j    INTEGER NOT NULL,      -- the lock forecast from
    jornada   INTEGER NOT NULL,
    player    TEXT NOT NULL,
    predicted REAL NOT NULL,
    plays     REAL,
    flagged   INTEGER NOT NULL,
    PRIMARY KEY (run_id, from_j, jornada, player)
) WITHOUT ROWID;


-- parser_health reads a source's newest snapshots by time alone.
CREATE INDEX IF NOT EXISTS lineups_observed ON src_lineups (observed_at);

-- Is each source still arriving, and whole? Per source that stores a whole
-- snapshot each fetch (not the keyed tables, which keep only changes) (a page that parsed to nothing stores no snapshot at all),
-- its newest snapshot against the 20 before it: stale when it is later than
-- three of the usual gaps (and 6 hours), thin when under half the usual
-- rows. last_good is the newest snapshot of the usual size: where to start
-- looking (src/health.py tells you).
DROP VIEW IF EXISTS parser_health;
CREATE VIEW parser_health AS
WITH snaps AS (/* health.RECENT */),
r AS (SELECT *, row_number() OVER (PARTITION BY src ORDER BY at DESC) AS k, stamp_jd(at) AS jd FROM snaps),
usual AS (SELECT src, avg(n) AS usual_rows, (max(jd) - min(jd)) * 24 / (count(*) - 1) AS gap_h
          FROM r WHERE k BETWEEN 2 AND 21 GROUP BY src HAVING count(*) >= 5),
now AS (SELECT julianday(substr((SELECT cut()), 1, 10) || 'T' || substr((SELECT cut()), 12, 2) || ':' || substr((SELECT cut()), 14, 2) || 'Z') AS jd)
SELECT l.src, l.at AS last_at, l.n AS rows, round(u.usual_rows, 1) AS usual_rows,
       round((now.jd - l.jd) * 24, 1) AS hours_since, round(u.gap_h, 1) AS usual_gap_h,
       CASE WHEN (now.jd - l.jd) * 24 > max(6.0, 3 * u.gap_h) THEN 'stale'
            WHEN l.n < 0.5 * u.usual_rows THEN 'thin' ELSE 'ok' END AS status,
       (SELECT max(g.at) FROM (/* health.SNAPSHOTS */) g WHERE g.src = l.src AND g.n >= 0.5 * u.usual_rows) AS last_good
FROM r l JOIN usual u USING (src), now WHERE l.k = 1;

-- What facts.ensure has kept, and through which day. A changed schema makes
-- them again (this runs only when the schema text changes), so a changed
-- column or view never reads a fact made the old way.
CREATE TABLE IF NOT EXISTS facts_meta (
    name    TEXT PRIMARY KEY,
    version TEXT NOT NULL,
    through TEXT NOT NULL
);
DELETE FROM facts_meta;

-- Each app id's player: the futbolfantasy player whose value it equalled
-- at the most market snapshots (an all-players row holds until his next),
-- then the most name stems shared (stems_meet); each app id's unique best,
-- then per player the unique best. Over all rows, not as of t.
CREATE INDEX IF NOT EXISTS market_by_value ON src_market (value);
DROP VIEW IF EXISTS app_links;
CREATE VIEW app_links AS
WITH snap AS (SELECT observed_at, player_id AS aid, market_value AS v, player_name AS n1, player_name_full AS n2
              FROM src_api_teams WHERE market_value > 0
              UNION ALL SELECT observed_at, player_id, market_value, player_name, player_name_full
              FROM src_api_market WHERE market_value > 0),
held AS (SELECT * FROM (SELECT player_id AS aid, market_value AS v, player_name AS n1, player_name_full AS n2,
                               observed_at AS since,
                               coalesce(lead(observed_at) OVER (PARTITION BY player_id ORDER BY observed_at, rowid),
                                        '9999') AS until
                        FROM src_api_players_all) WHERE v > 0),
ff AS (SELECT observed_at, ff_id, value AS v, name FROM src_market WHERE ff_id <> '' AND value > 0),
met AS MATERIALIZED (SELECT a.aid, f.ff_id, f.observed_at AS at, a.n1, a.n2, f.name
        FROM snap a JOIN ff f ON f.observed_at = a.observed_at AND f.v = a.v
        UNION ALL
        SELECT h.aid, f.ff_id, f.observed_at, h.n1, h.n2, f.name
        FROM held h JOIN ff f ON f.v = h.v AND f.observed_at >= h.since AND f.observed_at < h.until),
pairs AS (SELECT m.aid, m.ff_id, count(DISTINCT m.at) AS eq, n.named
          FROM met m JOIN (SELECT aid, ff_id, max(stems_meet(n1, n2, name)) AS named
                           FROM (SELECT DISTINCT aid, ff_id, n1, n2, name FROM met) GROUP BY aid, ff_id) n
            USING (aid, ff_id)
          GROUP BY m.aid, m.ff_id),
a AS (SELECT *, rank() OVER (PARTITION BY aid ORDER BY eq DESC, named DESC) AS ra,
             count(*) OVER (PARTITION BY aid, eq, named) AS tie
      FROM pairs WHERE named > 0),
f AS (SELECT aid, ff_id, rank() OVER (PARTITION BY ff_id ORDER BY eq DESC, named DESC) AS rf,
             count(*) OVER (PARTITION BY ff_id, eq, named) AS tie
      FROM a WHERE ra = 1 AND tie = 1)
SELECT aid AS app_id, ff_id AS player FROM f WHERE rf = 1 AND tie = 1;

-- The registry as the data has it: every futbolfantasy player ever in the
-- market, by his latest row's name and club, with his app id (app_links).
-- The crosswalk stage writes it into crosswalk and adds the slugs.
DROP VIEW IF EXISTS registry;
CREATE VIEW registry AS
WITH last AS (SELECT ff_id, trim(name) AS name, club,
                     /* newest ff_id */ AS n
              FROM src_market WHERE ff_id <> '')
SELECT l.ff_id AS player_id, l.name, l.club AS club_id, coalesce(a.app_id, '') AS app_id
FROM last l LEFT JOIN app_links a ON a.player = l.ff_id WHERE l.n = 1;
