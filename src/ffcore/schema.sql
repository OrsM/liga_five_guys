-- What earlier versions made and nothing reads now: a database made by them drops it here.
DROP VIEW IF EXISTS app_lineup;
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

-- The market and the league as a universe was built on them, a cut's and
-- a variant's: player_facts with what a sale of each gets (sale_odds), and
-- each buy's bids and their chance (bid_odds). Written by assemble.see,
-- read by the decision (decide.sql) and a run's facts.
CREATE TABLE IF NOT EXISTS seen (
    cut     TEXT NOT NULL,
    variant TEXT NOT NULL,
    player  TEXT NOT NULL,
    name    TEXT NOT NULL,
    pos     TEXT,
    owner   TEXT,
    value   REAL,
    trend   REAL,
    paid    REAL,
    offer   REAL,
    clause  REAL,
    price   REAL,
    route   TEXT,
    my_bid  REAL,
    fetches REAL,
    code    TEXT NOT NULL DEFAULT '',   -- what made it (assemble.see); '': by hand
    PRIMARY KEY (cut, variant, player)
) WITHOUT ROWID;
-- Every manager's cash as seen at a cut (the cash view then): where your
-- cash at t starts from (my_cash).
CREATE TABLE IF NOT EXISTS seen_cash (
    cut     TEXT NOT NULL,
    variant TEXT NOT NULL,
    manager TEXT NOT NULL,
    cash    REAL NOT NULL,
    PRIMARY KEY (cut, variant, manager)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS seen_odds (
    cut     TEXT NOT NULL,
    variant TEXT NOT NULL,
    player  TEXT NOT NULL,
    ratio   REAL NOT NULL,
    p_win   REAL NOT NULL,
    PRIMARY KEY (cut, variant, player, ratio)
) WITHOUT ROWID;

-- What the model looks at, as t: the cut, the variant's constants, the
-- forecast moment and who you are (assemble.built); the league as seen then
-- (seen at that cut) and the forecast of a moment (forecast_used).
DROP VIEW IF EXISTS look;
CREATE VIEW look AS
SELECT cut() AS cut, variant() AS variant, moment() AS moment, me() AS me;

-- Your squad at t: whom you owned as seen then, at his position there.
DROP VIEW IF EXISTS my_squad;
CREATE VIEW my_squad AS
SELECT s.player, coalesce(s.pos, 'MED') AS pos
FROM look l JOIN seen s ON s.cut = l.cut AND s.variant = l.variant AND s.owner = l.me;

-- Your cash at t, as seen then (seen_cash).
DROP VIEW IF EXISTS my_cash;
CREATE VIEW my_cash AS
SELECT coalesce((SELECT c.cash FROM look l JOIN seen_cash c ON c.cut = l.cut AND c.variant = l.variant
                 AND c.manager = l.me), 0.0) AS cash;

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

-- The forecast (forecast_now) as a decision at t reads it, made into a
-- moment each time (assemble.forecast): every player's cell a jornada and
-- its factors, and who was flagged then. code says what made a moment kept
-- from before ('sql' for every one made now); a moment no run or grade
-- points to is dropped once its cut and variant are made again.
CREATE TABLE IF NOT EXISTS forecast_moment (
    moment  INTEGER PRIMARY KEY,
    code    TEXT NOT NULL DEFAULT 'sql',
    as_of   TEXT NOT NULL,
    variant TEXT NOT NULL DEFAULT 'live',
    pinned  INTEGER NOT NULL DEFAULT 0   -- 1: a run keeps it (set by whoever records a run on it); forecast() never drops one
);

CREATE TABLE IF NOT EXISTS forecast_player (
    moment  INTEGER NOT NULL REFERENCES forecast_moment(moment),
    player  TEXT NOT NULL,
    flagged INTEGER NOT NULL,
    PRIMARY KEY (moment, player)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS forecast_cell (
    moment  INTEGER NOT NULL REFERENCES forecast_moment(moment),
    player  TEXT NOT NULL,
    jornada INTEGER NOT NULL,
    pts     REAL NOT NULL,
    p       REAL NOT NULL,
    ppm     REAL,
    fixture REAL,
    picked  REAL,
    fit     REAL,
    PRIMARY KEY (moment, player, jornada)
) WITHOUT ROWID;
DROP TABLE IF EXISTS forecast_variant;

-- Is each source still arriving, and whole? Per source that stores a whole
-- snapshot each fetch (not the keyed tables, which keep only changes) (a page that parsed to nothing stores no snapshot at all),
-- its newest snapshot against the 20 before it: stale when it is later than
-- three of the usual gaps (and 6 hours), thin when under half the usual
-- rows. last_good is the newest snapshot of the usual size: where to start
-- looking (src/health.py tells you).
DROP VIEW IF EXISTS market_read;
CREATE VIEW market_read AS SELECT max(observed_at) AS at FROM api_market WHERE observed_at <= (SELECT cut());

DROP VIEW IF EXISTS parser_health;
CREATE VIEW parser_health AS
WITH snaps AS (
  SELECT 'market' AS src, observed_at AS at, count(*) AS n FROM market WHERE observed_at <= (SELECT cut()) GROUP BY observed_at
  UNION ALL SELECT 'lineups' AS src, observed_at AS at, count(*) AS n FROM lineups WHERE observed_at <= (SELECT cut()) GROUP BY observed_at
  UNION ALL SELECT 'api_standings' AS src, observed_at AS at, count(*) AS n FROM api_standings WHERE observed_at <= (SELECT cut()) GROUP BY observed_at
  UNION ALL SELECT 'api_lineup' AS src, observed_at AS at, count(*) AS n FROM api_lineup WHERE observed_at <= (SELECT cut()) GROUP BY observed_at
  UNION ALL SELECT 'api_teams' AS src, observed_at AS at, count(*) AS n FROM api_teams WHERE observed_at <= (SELECT cut()) GROUP BY observed_at
  UNION ALL SELECT 'api_leagues' AS src, observed_at AS at, count(*) AS n FROM api_leagues WHERE observed_at <= (SELECT cut()) GROUP BY observed_at),
r AS (SELECT *, row_number() OVER (PARTITION BY src ORDER BY at DESC) AS k, stamp_jd(at) AS jd FROM snaps),
usual AS (SELECT src, avg(n) AS usual_rows, (max(jd) - min(jd)) * 24 / (count(*) - 1) AS gap_h
          FROM r WHERE k BETWEEN 2 AND 21 GROUP BY src HAVING count(*) >= 5),
now AS (SELECT julianday(substr((SELECT cut()), 1, 10) || 'T' || substr((SELECT cut()), 12, 2) || ':' || substr((SELECT cut()), 14, 2) || 'Z') AS jd)
SELECT l.src, l.at AS last_at, l.n AS rows, round(u.usual_rows, 1) AS usual_rows,
       round((now.jd - l.jd) * 24, 1) AS hours_since, round(u.gap_h, 1) AS usual_gap_h,
       CASE WHEN (now.jd - l.jd) * 24 > max(6.0, 3 * u.gap_h) THEN 'stale'
            WHEN l.n < 0.5 * u.usual_rows THEN 'thin' ELSE 'ok' END AS status,
       (SELECT max(g.at) FROM r g WHERE g.src = l.src AND g.n >= 0.5 * u.usual_rows) AS last_good
FROM r l JOIN usual u USING (src), now WHERE l.k = 1;

-- Views are recreated on every connect: a changed one replaces the old.

-- Each graded lock and jornada: forecast error over every player forecast,
-- what the 50 forecast highest then scored, and the same for the players
-- flagged then. No window over the table: a run asked for (WHERE run_id)
-- reads only its rows.
-- A grade's forecasts, a player a row: what was expected of him at the
-- lock (from_j) for a jornada, against what he scored (0 if nothing); his
-- chance of playing (NULL in grades kept before 2026-10-17), and whether he
-- was listed injured, doubtful or suspended then. Error and calibration
-- are queries over it.
DROP VIEW IF EXISTS grade_forecast;
CREATE VIEW grade_forecast AS
WITH pts AS (SELECT player, jornada, sum(pts) AS pts FROM scored GROUP BY player, jornada)
SELECT g.run_id, g.from_j, g.jornada, g.player, g.predicted, coalesce(a.pts, 0.0) AS actual,
       g.flagged, g.plays
FROM grade_cell g LEFT JOIN pts a ON a.player = g.player AND a.jornada = g.jornada;

-- A grade's accuracy at each distance (ahead: jornadas past the lock) for the
-- players who could be in a winning eleven (grp 'star': forecast e of 4 or
-- more) and for every player forecast (grp 'all'): n, RMSE, MAE, bias
-- (predicted less actual), the correlation r and the slope of actual
-- points on predicted. grading.py accuracy prints it.
DROP VIEW IF EXISTS grade_accuracy;
CREATE VIEW grade_accuracy AS
WITH g AS (SELECT run_id, jornada - from_j AS ahead, predicted AS x, actual AS y FROM grade_forecast),
u AS (SELECT 'all' AS grp, * FROM g UNION ALL SELECT 'star', * FROM g WHERE x >= 4)
SELECT run_id, grp, ahead, count(*) AS n, sqrt(avg((x - y) * (x - y))) AS rmse, avg(abs(x - y)) AS mae,
       avg(x - y) AS bias,
       (avg(x * y) - avg(x) * avg(y))
       / sqrt((avg(x * x) - avg(x) * avg(x)) * (avg(y * y) - avg(y) * avg(y))) AS r,
       (avg(x * y) - avg(x) * avg(y)) / (avg(x * x) - avg(x) * avg(x)) AS slope
FROM u GROUP BY run_id, grp, ahead;


DROP VIEW IF EXISTS replay_gap;
DROP VIEW IF EXISTS replay_lead;
DROP VIEW IF EXISTS lead_buckets;
DROP VIEW IF EXISTS replayed;
DROP VIEW IF EXISTS replay_moments;
DROP VIEW IF EXISTS lineup_snapshots;
DROP TABLE IF EXISTS replay_cell;
DROP TABLE IF EXISTS variants;

-- The league's money, as SQL over the app's tables (current_/history_ views
-- read as of t, each row with its player). League writes the one
-- input no table holds, the config.
CREATE TABLE IF NOT EXISTS league_config (
    me     TEXT NOT NULL,
    budget REAL NOT NULL
);

DROP VIEW IF EXISTS users;
CREATE VIEW users AS
SELECT user_id AS user_id, manager AS manager FROM current_api_standings
WHERE user_id <> '' AND manager <> '' GROUP BY user_id;

-- Who owns each player now.
DROP VIEW IF EXISTS owners;
CREATE VIEW owners AS
SELECT player, manager FROM current_api_teams
WHERE manager <> '' AND player IS NOT NULL GROUP BY player;

-- The activity feed, each event once, with who paid (buyer: a buy's or a
-- clause's manager) and who was paid (seller: a sale's manager, a clause's
-- other); then the exits the squads show beyond those the feed records (a
-- sale by him, a clause paid to him), as sales at the player's last value.
-- Counted, not matched by time: squads and feed are stamped minutes apart.
DROP VIEW IF EXISTS ledger;
CREATE VIEW ledger AS
WITH feed AS (
    SELECT coalesce(a.at, '') AS at, coalesce(a.kind, '') AS kind,
           u.manager AS who, c.manager AS other, a.player, coalesce(a.amount, 0.0) AS amount
    FROM current_api_activity a
    LEFT JOIN users u ON u.user_id = a.user_id
    LEFT JOIN users c ON c.user_id = a.counterparty),
roles AS (
    SELECT *, CASE WHEN kind IN ('buy', 'transfer') THEN who END AS buyer,
              CASE kind WHEN 'sell' THEN who WHEN 'transfer' THEN other END AS seller
    FROM feed),
t AS MATERIALIZED (
    SELECT h.observed_at, h.player, h.manager AS manager,
           coalesce(h.market_value, 0.0) AS value
    FROM history_api_teams h
    WHERE h.manager <> '' AND h.player IS NOT NULL),
s AS (SELECT observed_at AS a, lead(observed_at) OVER (ORDER BY observed_at) AS b
      FROM (SELECT DISTINCT observed_at FROM t)),
shown AS (
    SELECT ta.manager AS who, ta.player, s.b AS at, ta.value,
           row_number() OVER (PARTITION BY ta.manager, ta.player ORDER BY s.b) AS n
    FROM t ta JOIN s ON s.a = ta.observed_at AND s.b IS NOT NULL
    WHERE EXISTS (SELECT 1 FROM t m WHERE m.observed_at = s.b AND m.manager = ta.manager)
      AND NOT EXISTS (SELECT 1 FROM t tb WHERE tb.observed_at = s.b
                      AND tb.player = ta.player AND tb.manager = ta.manager)),
recorded AS (SELECT seller, player, count(*) AS n FROM roles
             WHERE seller IS NOT NULL GROUP BY seller, player)
SELECT at, kind, who, other, player, amount, buyer, seller, 0 AS inferred FROM roles
UNION ALL
SELECT shown.at, 'sell', shown.who, NULL, shown.player, shown.value, NULL, shown.who, 1
FROM shown LEFT JOIN recorded r ON r.seller = shown.who AND r.player = shown.player
WHERE shown.n > coalesce(r.n, 0);

-- Each manager's cash: the budget, less buys and clauses paid, plus sales,
-- clauses received and bonuses; plus, for everyone, the income the feed
-- never records (your real balance less the same sum for you).
DROP VIEW IF EXISTS cash;
CREATE VIEW cash AS
WITH cfg AS (SELECT me, budget FROM league_config),
l AS MATERIALIZED (SELECT buyer, seller, who, kind, amount FROM ledger),
paid AS (SELECT buyer AS manager, sum(amount) AS n FROM l WHERE buyer IS NOT NULL GROUP BY buyer),
got AS (SELECT seller AS manager, sum(amount) AS n FROM l WHERE seller IS NOT NULL GROUP BY seller),
bonus AS (SELECT who AS manager, sum(amount) AS n FROM l WHERE kind = 'bonus' GROUP BY who),
flow AS (
    SELECT u.manager, cfg.budget - coalesce(paid.n, 0.0) + coalesce(got.n, 0.0) + coalesce(bonus.n, 0.0) AS feed
    FROM (SELECT DISTINCT manager FROM users) u, cfg
    LEFT JOIN paid ON paid.manager = u.manager LEFT JOIN got ON got.manager = u.manager
    LEFT JOIN bonus ON bonus.manager = u.manager),
mine AS (SELECT s.team_money AS money FROM current_api_standings s, cfg
         WHERE s.manager = cfg.me AND s.team_money <> 0 LIMIT 1),
gap AS (SELECT coalesce((SELECT (SELECT money FROM mine) - f.feed
                          FROM flow f, cfg WHERE f.manager = cfg.me), 0.0) AS untracked)
SELECT f.manager,
       CASE WHEN f.manager = cfg.me AND (SELECT money FROM mine) IS NOT NULL
            THEN (SELECT money FROM mine) ELSE f.feed + gap.untracked END AS cash
FROM flow f, cfg, gap;

-- What each owned player's owner paid for him: his last buy or clause to
-- that manager. Players from the starting squad have none.
DROP VIEW IF EXISTS price_paid;
CREATE VIEW price_paid AS
SELECT player, amount AS paid FROM (
    SELECT l.player, l.amount,
           row_number() OVER (PARTITION BY l.player ORDER BY l.at DESC) AS last
    FROM ledger l JOIN owners o ON o.player = l.player AND o.manager = l.buyer)
WHERE last = 1;

-- The lock: the next jornada to lock after t, at its first kickoff
-- (round_locks), as a stamp and a julian day (t); NULL once none is left.
-- One for everything: the board, the nights before it, the reminder. A
-- jornada under way locks nothing the market's moves reach (bought tonight,
-- a player plays none of what is left of it: late_arrivals).
DROP VIEW IF EXISTS next_lock;
CREATE VIEW next_lock AS
SELECT min(lock) AS lock,
       stamp_jd(min(lock)) AS t
FROM round_locks WHERE lock > (SELECT cut());

-- When each player on the game's market has his auction close (the last close listed): the fact the forecast's
-- late arrivals and the decision's schedule both read.
DROP VIEW IF EXISTS listing_close;
CREATE VIEW listing_close AS
SELECT player, max(julianday(expires_at)) AS t FROM listings
WHERE seller = 'marketPlayerLeague' AND player IS NOT NULL AND sale_price <> 0 AND coalesce(expires_at, '') <> ''
GROUP BY player;

-- The players on the game's market who could not play a jornada's match
-- for whoever buys them: his auction closes after his club's kickoff in it
-- (of a jornada under way or to come). Nobody holds them, so for everyone
-- they play none of it.
DROP VIEW IF EXISTS late_arrivals;
CREATE VIEW late_arrivals AS
SELECT DISTINCT l.player, k.jornada
FROM listing_close l JOIN player_club m ON m.player = l.player AND m.club IS NOT NULL
JOIN kickoffs k ON m.club IN (k.home, k.away)
JOIN jornada_calendar c ON c.jornada = k.jornada AND c.state IN ('under way', 'ahead')
WHERE k.t < l.t;

-- Each moment's forecast (forecast_cell) as a decision made at t uses
-- it: points if he plays, and the chance he plays, none in a jornada he
-- would join after his club's match (late_arrivals). The universe reads
-- its cells here, and the model the moment it looks at (cell).
-- A cell's expected points, e = pts x p, said once; every reader takes e from here.
DROP VIEW IF EXISTS forecast_e;
CREATE VIEW forecast_e AS SELECT moment, player, jornada, pts, p, ppm, fixture, picked, fit, pts * p AS e FROM forecast_cell;

DROP VIEW IF EXISTS forecast_used;
CREATE VIEW forecast_used AS
SELECT c.moment, c.player, c.jornada, c.pts, CASE WHEN l.player IS NULL THEN c.p ELSE 0.0 END AS p,
       CASE WHEN l.player IS NULL THEN c.e ELSE 0.0 END AS e
FROM forecast_e c LEFT JOIN late_arrivals l ON l.player = c.player AND l.jornada = c.jornada;

-- How a jornada's points spread about a cell's: m2, the mean square of a
-- single game's points over its mean squared (the season's games scored
-- so far, or the seed pool before MIN_POOL of them), and the share of a
-- player's form that persists from one jornada to the next (share).
DROP VIEW IF EXISTS score_spread;
CREATE VIEW score_spread AS
WITH c AS (SELECT const('MIN_POOL') AS min_pool,
                  const('PERSISTENT_SHARE') AS share),
played AS MATERIALIZED (SELECT sum(s.pts) AS x FROM scored s JOIN appearances a ON a.player = s.player AND a.jornada = s.jornada
                        GROUP BY s.player, s.jornada),
pool AS (SELECT x FROM played WHERE (SELECT count(*) FROM played) >= (SELECT min_pool FROM c)
         UNION ALL
         SELECT x FROM (/* forecast.SEED_POOL */) WHERE (SELECT count(*) FROM played) < (SELECT min_pool FROM c)),
m AS (SELECT avg(x * x) AS sq, avg(x) AS mean FROM pool)
SELECT m.sq / (CASE WHEN abs(m.mean) > 1e-9 THEN m.mean * m.mean ELSE 1.0 END) AS m2, c.share FROM m, c;

-- Each cell as the model looks (look's moment: its cells are the jornadas
-- left at its cut): a player's, and each position's stand-in, the average of
-- the points if he plays and of the chance he plays of the players of that
-- position in a squad or on the game's market at t (squads, listings), the
-- product of the averages being his own e. p is the chance he plays, e his
-- expected points and v their variance (p pts^2 m2 - e^2: the game drawn
-- from the pool scaled to his points if he plays). The one place e and v are
-- worked out for a cell as decided on (forecast_e: for a stored moment).
DROP VIEW IF EXISTS cell_pool;
CREATE VIEW cell_pool AS
WITH held AS (SELECT player FROM squads UNION SELECT player FROM listings),
a AS (SELECT f.jornada, c.pos, avg(f.pts) AS pts, avg(f.p) AS p
           FROM look l JOIN forecast_used f ON f.moment = l.moment
           JOIN held h ON h.player = f.player JOIN player_club c ON c.player = f.player
           WHERE c.pos <> ''
           GROUP BY f.jornada, c.pos),
u AS (SELECT f.jornada, f.player, NULL AS pos, f.pts, f.p, f.e FROM look l JOIN forecast_used f ON f.moment = l.moment
      UNION ALL
      SELECT jornada, NULL, pos, pts, p, pts * p FROM a)
SELECT u.jornada, u.player, u.pos, u.p, u.e, u.p * u.pts * u.pts * w.m2 - u.e * u.e AS v
FROM u CROSS JOIN score_spread w;

-- Each player's forecast for each jornada left.
DROP VIEW IF EXISTS cell;
CREATE VIEW cell AS SELECT jornada, player, p, e, v FROM cell_pool WHERE player IS NOT NULL;

-- Each player's forecast over the jornadas left: his expected points, their variance (each jornada's, summed)
-- and how many jornadas.
DROP VIEW IF EXISTS cell_total;
CREATE VIEW cell_total AS SELECT player, sum(e) AS e, sum(v) AS v, count(*) AS jornadas FROM cell GROUP BY player;

-- The game's nightly offers still to come before the lock (next_lock): one
-- as each standing offer expires, then one a day (0 with none standing),
-- and when the first of them comes (first, a julian day).
DROP VIEW IF EXISTS nights_left;
CREATE VIEW nights_left AS
WITH e AS (SELECT max(julianday(expires_at)) AS ends FROM offers
           WHERE status = 'pending' AND expires_at <> ''),
l AS (SELECT t AS lock FROM next_lock)
SELECT CASE WHEN ends IS NOT NULL AND lock > ends THEN CAST(lock - ends AS INTEGER) + 1 ELSE 0 END AS nights,
       ends AS first
FROM e, l;

-- What waiting to sell a player of yours with an offer standing is worth,
-- over the nights before the lock (next_lock, nights_left): each night's
-- offer to come taken only when it beats waiting on (the last before the
-- lock always). An offer to come is a past one's share of value
-- (recent_offers) of the value he will have that night: his value moved by
-- his trend, the share carry says is made by then (price_carry). NULL
-- without a value or past offers.
DROP VIEW IF EXISTS waiting;
CREATE VIEW waiting AS
WITH RECURSIVE
r AS MATERIALIZED (SELECT ratio FROM recent_offers),
c AS MATERIALIZED (SELECT h, carry FROM price_carry),
f AS MATERIALIZED (SELECT player, value, trend, offer FROM player_facts),
s AS MATERIALIZED (
    SELECT player, 'sale' AS kind, value, coalesce(trend, 0.0) AS trend,
           (SELECT nights FROM nights_left) AS nights
    FROM f WHERE offer IS NOT NULL),
w(player, kind, night, later) AS (
    SELECT player, kind, nights, 0.0 FROM s
    WHERE value IS NOT NULL AND EXISTS (SELECT 1 FROM r)
  UNION ALL
    SELECT w.player, w.kind, w.night - 1,
           (SELECT avg(max(r.ratio * s.value * (1 + s.trend / 100 * coalesce(
                    (SELECT carry FROM c WHERE h = min(w.night, (SELECT max(h) FROM c))), 1.0)),
                    w.later)) FROM r)
    FROM w JOIN s USING (player, kind) WHERE w.night > 0)
SELECT player, kind, later FROM w WHERE night = 0;

-- What selling a player with an offer standing on him gets: the offer, or
-- what turning it down is worth (waiting), whichever is more.
DROP VIEW IF EXISTS sale_odds;
CREATE VIEW sale_odds AS
SELECT f.player, f.offer, w.later AS waiting, max(f.offer, coalesce(w.later, 0.0)) AS fetches
FROM player_facts f LEFT JOIN waiting w ON w.player = f.player AND w.kind = 'sale'
WHERE f.offer IS NOT NULL;

-- Every player the market or a futbolfantasy line-up names, as of t,
-- but coaches (no slot of the eleven, and the league has none: "Coach is not
-- enabled", 2026-10-29): his name and position, what he is worth (market
-- value; 0 is none), whether he is on sale (price, never under his value:
-- the game takes no bid under it; route: listed by a manager, else free,
-- the game's own), your pending bid, his owner and release clause (any can
-- be paid now), the best offer standing on him if he is yours, what his
-- owner paid, his price trend. What the market is built from (assemble) and
-- the board shows.
DROP VIEW IF EXISTS player_facts;
CREATE VIEW player_facts AS
WITH who AS (SELECT player, name, pos, value FROM player_roster),
sale AS (SELECT player, max(sale_price, coalesce(market_value, 0)) AS price,
                CASE seller WHEN 'marketPlayerTeam' THEN 'listed' ELSE 'free' END AS route
         FROM listings WHERE player IS NOT NULL AND sale_price <> 0),
bid AS (SELECT player, max(bid_money) AS my_bid FROM listings
        WHERE player IS NOT NULL AND bid_status = 'pending' AND bid_money <> 0
        GROUP BY player),
offer AS (SELECT t.player, max(o.money) AS offer FROM offers o
          JOIN squads t ON t.player_team_id = o.player_team_id
          WHERE o.player_team_id <> '' AND o.status = 'pending' AND o.money <> 0
            AND t.player IS NOT NULL
          GROUP BY t.player),
clause AS (SELECT player, buyout AS clause FROM squads
           WHERE player IS NOT NULL AND buyout <> 0)
SELECT w.player, coalesce(nullif(w.name, ''), w.player) AS name,
       w.pos, nullif(w.value, 0) AS value,
       o.manager AS owner, c.clause, s.price, s.route, b.my_bid,
       CASE WHEN o.manager = (SELECT me FROM league_config) THEN f.offer END AS offer,
       p.paid, t.trend
FROM who w LEFT JOIN owners o USING (player) LEFT JOIN clause c USING (player)
LEFT JOIN sale s USING (player) LEFT JOIN bid b USING (player)
LEFT JOIN offer f USING (player) LEFT JOIN price_paid p USING (player)
LEFT JOIN price_trend t ON t.ff_id = w.player;

-- market_daily picks a few snapshots out of all of them by time alone.
CREATE INDEX IF NOT EXISTS market_observed ON market (observed_at);

-- The market as it closed each day: the rows of each day's last snapshot
-- (each snapshot holds the whole market). Values move once a day; the
-- change from one day's to the next is a step. As of t: today's row is
-- the last snapshot up to it, as the run at that moment saw it.
DROP VIEW IF EXISTS market_daily;
CREATE VIEW market_daily AS
SELECT * FROM price_history
WHERE observed_at IN (SELECT max(observed_at) FROM price_history GROUP BY substr(observed_at, 1, 10));

-- What facts.ensure has kept, and through which day. A changed schema makes
-- them again (this runs only when the schema text changes), so a changed
-- column or view never reads a fact made the old way.
CREATE TABLE IF NOT EXISTS facts_meta (
    name    TEXT PRIMARY KEY,
    version TEXT NOT NULL,
    through TEXT NOT NULL
);
DELETE FROM facts_meta;
DROP TABLE IF EXISTS price_step;
DROP TABLE IF EXISTS momentum_day;

-- Each player's closed days of market value, written once (facts.PRICE_STEP):
-- n counts his valued days, step is the change in % on the day before (NULL
-- for his first, or after a value of 0), i counts his changes so far and s1,
-- s2 are the two before this one.
CREATE TABLE IF NOT EXISTS price_step (
    ff_id       TEXT NOT NULL,
    n           INTEGER NOT NULL,
    day         TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    value       REAL NOT NULL,
    step        REAL,
    i           INTEGER,
    s1          REAL,
    s2          REAL,
    PRIMARY KEY (ff_id, n)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS price_step_day ON price_step (day, ff_id);

-- Every valued day of each player's market value, all time, with the
-- change since his previous one in % (NULL for his first, or after a value
-- of 0) and where he first appears. The views below cut it.
DROP VIEW IF EXISTS price_obs;
CREATE VIEW price_obs AS
SELECT ff_id, observed_at, value, id, first_at, first_id,
       CASE WHEN prev > 0 THEN 100 * (value / prev - 1) END AS step
FROM (SELECT ff_id, observed_at, value, id,
             lag(value) OVER p AS prev,
             first_value(observed_at) OVER p AS first_at,
             first_value(id) OVER p AS first_id
      FROM market_daily WHERE coalesce(ff_id, '') <> '' AND value IS NOT NULL
      WINDOW p AS (PARTITION BY ff_id ORDER BY observed_at, id));

-- (model_constants, once a table of the constants per variant, is gone: the views read const(NAME), the module's value.)
DROP TABLE IF EXISTS model_constants;

-- The constants the views use, one row: each named once, here.
DROP VIEW IF EXISTS k;
CREATE VIEW k AS SELECT
    const('CLIP') AS clip,
    const('PRICE_WINDOW') AS price_window,
    const('BID_NEIGHBOURS') AS bid_neighbours,
    const('PRICE_W0') AS price_w0,
    const('PRICE_W1') AS price_w1,
    const('PRICE_W2') AS price_w2,
    const('PRICE_FOCUS') AS price_focus,
    const('MATCH_LEN') AS match_len,
    const('NEUTRAL_START') AS neutral_start,
    const('ABSENT_START') AS absent_start,
    const('SHRINK_K') AS shrink_k,
    const('FIXTURE_K') AS fixture_k,
    const('PICK_K') AS pick_k,
    const('P_LIFT') AS p_lift,
    const('P_LISTING') AS p_listing,
    const('P_DECAY') AS p_decay,
    -- what price_series and momentum_pairs are made with: the key of momentum_day's rows
    printf('%.9g|%.9g|%.9g|%.9g|%.9g', const('CLIP'), const('PRICE_W0'), const('PRICE_W1'), const('PRICE_W2'), const('PRICE_FOCUS')) AS ckey;

-- Every valued day of every player as of t: the closed days kept in
-- price_step, and the day of the cut (the last snapshot of it up to the cut)
-- worked out against the day before.
-- The last day with a market snapshot as of t: the one day price_step
-- does not hold for it (the days before are closed).
DROP VIEW IF EXISTS price_day_now;
CREATE VIEW price_day_now AS
SELECT coalesce(substr(max(observed_at), 1, 10), '') AS d FROM price_history;

DROP VIEW IF EXISTS price_live;
CREATE VIEW price_live AS
WITH cutday AS (SELECT d FROM price_day_now),
latest AS (SELECT ff_id, max(n) AS n, max(CASE WHEN step IS NOT NULL THEN n END) AS ln
           FROM price_step WHERE day < (SELECT d FROM cutday) GROUP BY ff_id),
seed AS (SELECT a.ff_id, a.n, a.value, l.i AS i0, l.step AS last_step, l.s1 AS last_s1
         FROM latest z JOIN price_step a ON a.ff_id = z.ff_id AND a.n = z.n
         LEFT JOIN price_step l ON l.ff_id = z.ff_id AND l.n = z.ln),
live AS (SELECT m.ff_id, m.observed_at, m.value, s.n AS n0, s.value AS pv, s.i0, s.last_step, s.last_s1
         FROM price_history m LEFT JOIN seed s ON s.ff_id = m.ff_id
         WHERE m.observed_at = (SELECT max(observed_at) FROM price_history
                                WHERE observed_at >= (SELECT d FROM cutday))
           AND coalesce(m.ff_id, '') <> '' AND m.value IS NOT NULL)
SELECT ff_id, coalesce(n0 + 1, 0) AS n, substr(observed_at, 1, 10) AS day, observed_at, value,
       CASE WHEN pv > 0 THEN 100 * (value / pv - 1) END AS step,
       CASE WHEN pv > 0 THEN coalesce(i0, -1) + 1 END AS i,
       CASE WHEN pv > 0 THEN last_step END AS s1, CASE WHEN pv > 0 THEN last_s1 END AS s2
FROM live;

DROP VIEW IF EXISTS price_rows;
CREATE VIEW price_rows AS
SELECT ff_id, n, day, observed_at, value, step, i, s1, s2 FROM price_step WHERE day < (SELECT d FROM price_day_now)
UNION ALL
SELECT ff_id, n, day, observed_at, value, step, i, s1, s2 FROM price_live;

-- Each player's changes, all time, numbered (i), each clipped at CLIP; x is
-- the signal momentum fits on: his last three clipped changes weighted
-- PRICE_W0..2 (a missing one counts as the oldest he has), with the value each ends at.
DROP VIEW IF EXISTS price_series;
CREATE VIEW price_series AS
SELECT ff_id, observed_at, day, step,
       price_w0 * c + price_w1 * coalesce(c1, c) + price_w2 * coalesce(c2, coalesce(c1, c)) AS x, i, value
FROM (SELECT ff_id, observed_at, day, step, i, value, k.price_w0, k.price_w1, k.price_w2,
             max(-k.clip, min(k.clip, step)) AS c, max(-k.clip, min(k.clip, s1)) AS c1,
             max(-k.clip, min(k.clip, s2)) AS c2
      FROM price_rows, k WHERE step IS NOT NULL);

-- The longest horizon momentum fits, as of t: a quarter of the median
-- player's number of changes (players with none count), at least 1.
DROP VIEW IF EXISTS price_hmax;
CREATE VIEW price_hmax AS
WITH lens AS (SELECT ff_id, sum(n) AS n FROM (
                  SELECT ff_id, count(step) AS n FROM price_step WHERE day < (SELECT d FROM price_day_now) GROUP BY ff_id
                  UNION ALL SELECT ff_id, step IS NOT NULL FROM price_live) GROUP BY ff_id),
ranked AS (SELECT n, row_number() OVER (ORDER BY n) AS r, count(*) OVER () AS m FROM lens)
SELECT coalesce((SELECT max(1, CAST(avg(n) AS INTEGER) / 4) FROM ranked
                 WHERE r IN ((m + 1) / 2, (m + 2) / 2)), 1) AS hmax;

-- A player's change in % over the h changes after his i-th (all time, h up
-- to price_hmax; hpad() is 0 but while momentum_day is made): what momentum fits, the backtest scores and a replay
-- looks back on. The changes compound to the ratio of the values (no value
-- is ever 0: the game's floor is 300k).
-- The horizons a price change is looked at over, 1 to 200 updates.
CREATE TABLE IF NOT EXISTS horizons (h INTEGER PRIMARY KEY);
INSERT OR IGNORE INTO horizons
WITH RECURSIVE n(h) AS (SELECT 1 UNION ALL SELECT h + 1 FROM n WHERE h < 200) SELECT h FROM n;

DROP VIEW IF EXISTS price_ahead;
CREATE VIEW price_ahead AS
SELECT a.ff_id, a.i, a.day, a.x, a.value, hs.h, b.observed_at AS end_at,
       b.day AS end_day, 100 * (b.value / a.value - 1) AS change
FROM price_series b JOIN horizons hs ON hs.h <= (SELECT hmax FROM price_hmax) + hpad()
JOIN price_series a ON a.ff_id = b.ff_id AND a.i = b.i - hs.h;

-- Momentum, as of t: how much of a price change carries into the next
-- h (up to price_hmax), the move over h clipped at CLIP an update.
DROP VIEW IF EXISTS momentum_pairs;
CREATE VIEW momentum_pairs AS
SELECT p.h, p.ff_id, p.i, p.end_day, p.x, p.value >= k.price_focus AS focus,
       max(-k.clip * p.h, min(k.clip * p.h, p.change)) AS y
FROM price_ahead p, k;

-- What each closed day adds to the momentum fit, per horizon: the sums of x*y
-- and x*x of the pairs that end that day, and of x*y, x*x, y*y and the count
-- of those from players worth PRICE_FOCUS or more (f...), written once
-- (facts.MOMENTUM_DAY) for the constants they were made with (ckey).
CREATE TABLE IF NOT EXISTS momentum_day (
    ckey    TEXT NOT NULL,
    h       INTEGER NOT NULL,
    end_day TEXT NOT NULL,
    sxy     REAL NOT NULL,
    sxx     REAL NOT NULL,
    fxy     REAL NOT NULL DEFAULT 0,
    fxx     REAL NOT NULL DEFAULT 0,
    fyy     REAL NOT NULL DEFAULT 0,
    fn      INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (ckey, h, end_day)
) WITHOUT ROWID;

-- The momentum fit's sums per horizon, as of t: the closed days kept in
-- momentum_day, and the pairs ending on the day of the cut.
DROP VIEW IF EXISTS momentum_sums;
CREATE VIEW momentum_sums AS
WITH parts AS (
    SELECT h, sxy, sxx, fxy, fxx, fyy, fn FROM momentum_day
    WHERE ckey = (SELECT ckey FROM k) AND end_day < (SELECT d FROM price_day_now)
      AND h <= (SELECT hmax FROM price_hmax)
    UNION ALL
    SELECT h, sum(x * y), sum(x * x), coalesce(sum(CASE WHEN focus THEN x * y END), 0),
           coalesce(sum(CASE WHEN focus THEN x * x END), 0), coalesce(sum(CASE WHEN focus THEN y * y END), 0),
           coalesce(sum(focus), 0) FROM momentum_pairs
    WHERE end_day = (SELECT d FROM price_day_now) GROUP BY h)
SELECT h, sum(sxy) AS sxy, sum(sxx) AS sxx, sum(fxy) AS fxy, sum(fxx) AS fxx, sum(fyy) AS fyy, sum(fn) AS fn
FROM parts GROUP BY h;

-- One least-squares slope through the origin per horizon, and the spread
-- (rmse, in %) of a change over h updates around it for players worth
-- PRICE_FOCUS or more (NULL where there are none).
DROP VIEW IF EXISTS momentum;
CREATE VIEW momentum AS
SELECT h, sxy / sxx AS slope,
       CASE WHEN fn > 0 THEN sqrt(max(fyy - 2 * sxy / sxx * fxy + sxy * sxy / (sxx * sxx) * fxx, 0) / fn) END AS rmse
FROM momentum_sums WHERE sxx <> 0;

DROP TABLE IF EXISTS momentum_fit;
DROP TABLE IF EXISTS price_trend_fit;
DROP VIEW IF EXISTS fitted;
DROP VIEW IF EXISTS price_trend_now;

-- The share of a trend (its move over the horizon, the longest h fitted)
-- already made after each update.
DROP VIEW IF EXISTS price_carry;
CREATE VIEW price_carry AS
WITH m AS MATERIALIZED (SELECT h, slope FROM momentum)
SELECT m.h, m.slope / e.slope AS carry
FROM m, (SELECT slope FROM m ORDER BY h DESC LIMIT 1) e
WHERE e.slope <> 0 ORDER BY m.h;

-- The slope at the longest horizon, each day up to t: a closed day's is the
-- fit on the pairs that ended by it (the sums momentum_day keeps), the day
-- of the cut's is momentum's. A day's fit is a function of the facts to that
-- day, not of which builds ran.
DROP VIEW IF EXISTS price_slope;
CREATE VIEW price_slope AS
WITH top AS MATERIALIZED (SELECT h, slope FROM momentum ORDER BY h DESC LIMIT 1)
SELECT end_day AS day, sum(sxy) OVER w / sum(sxx) OVER w AS slope
FROM momentum_day WHERE ckey = (SELECT ckey FROM k) AND h = (SELECT h FROM top)
  AND end_day < (SELECT d FROM price_day_now)
WINDOW w AS (ORDER BY end_day)
UNION ALL
SELECT (SELECT d FROM price_day_now), slope FROM top;

-- Each player's value change in % over the horizon (the slope times the
-- signal x of his changes) on each day he changed: as long as you would hold
-- him. As of t, a function of the facts to t.
DROP VIEW IF EXISTS price_trend_daily;
CREATE VIEW price_trend_daily AS
SELECT s.ff_id, s.day, coalesce(l.slope, 0.0) * s.x AS trend
FROM price_series s LEFT JOIN price_slope l ON l.day = s.day;

-- Each player's trend as of t: those whose last change is the newest day's
-- (every player in the day's snapshot has one).
DROP VIEW IF EXISTS price_trend;
CREATE VIEW price_trend AS
SELECT ff_id, trend FROM price_trend_daily WHERE day = (SELECT d FROM price_day_now);

-- Each player's expected value, night by night from the day of the cut to the
-- next lock (at most the longest horizon fitted): h updates on, the value then
-- and its spread (one rmse of the fit at that h, as a value; NULL for a player
-- worth less than PRICE_FOCUS, whom it is not measured for). The signal is
-- his last three changes (price_series.x), the slope is momentum's.
DROP VIEW IF EXISTS price_forecast;
CREATE VIEW price_forecast AS
WITH nights AS (
    SELECT max(1, min((SELECT hmax FROM price_hmax),
                      coalesce(CAST(ceil((SELECT t FROM next_lock) - julianday((SELECT d FROM price_day_now))) AS INTEGER),
                               (SELECT hmax FROM price_hmax)))) AS n),
now AS (SELECT ff_id, x, value FROM price_series WHERE day = (SELECT d FROM price_day_now))
SELECT n.ff_id, m.h, date((SELECT d FROM price_day_now), '+' || m.h || ' days') AS day, n.value,
       n.value * (1 + m.slope * n.x / 100) AS expected_value,
       CASE WHEN n.value >= k.price_focus THEN n.value * m.rmse / 100 END AS spread
FROM now n JOIN momentum m ON m.h <= (SELECT n FROM nights), k;

-- Momentum's forecasts of a change over h updates, each day refitted on what
-- was known then (the fit from the third day on), against what happened,
-- by horizon: for players worth PRICE_FOCUS or more on the day, and for all;
-- rmse and mean abs error in %, with 'no change' and 'the last change
-- again every update' to beat.
DROP VIEW IF EXISTS price_grade;
CREATE VIEW price_grade AS
WITH days AS MATERIALIZED (SELECT day, row_number() OVER (ORDER BY day) - 1 AS r, count(*) OVER () AS m
                           FROM (SELECT DISTINCT day FROM price_series WHERE observed_at <= (SELECT cut()))),
daily AS MATERIALIZED (SELECT h, end_day, sxy, sxx FROM momentum_day WHERE ckey = (SELECT ckey FROM k)),
fit AS (SELECT d.day, p.h, sum(p.sxy) / sum(p.sxx) AS slope
        FROM days d JOIN daily p ON p.end_day <= d.day WHERE d.r >= d.m / 4
        GROUP BY d.day, p.h HAVING sum(p.sxx) <> 0),
pred AS (SELECT a.h, s.value, f.slope * a.x AS p, a.change AS act, a.h * max(-k.clip, min(k.clip, s.step)) AS again
         FROM price_ahead a JOIN fit f ON f.day = a.day AND f.h = a.h
         JOIN price_series s ON s.ff_id = a.ff_id AND s.i = a.i, k
         WHERE a.end_at <= (SELECT cut()) AND a.h IN (1, 2, 3, 5, 7, 10)),
scoped AS (SELECT 'focus' AS scope, * FROM pred WHERE value >= (SELECT price_focus FROM k)
           UNION ALL SELECT 'all', * FROM pred)
SELECT scope, h, count(*) AS n,
       sqrt(avg((p - act) * (p - act))) AS rmse, sqrt(avg(act * act)) AS rmse_none,
       sqrt(avg((again - act) * (again - act))) AS rmse_again,
       avg(abs(p - act)) AS mae, avg(abs(act)) AS mae_none, avg(p) AS predicted, avg(act) AS actual
FROM scoped GROUP BY scope, h;

-- How each listing's auction went, once it has closed (by t): every
-- listing on the market (the game's or a manager's) by player and close
-- (closed, as a stamp), its asking price at its last sighting, and the buy
-- made within ten minutes of its close if one was: what the winner paid
-- (won; NULL: nobody bought) and whether it was you. bar is what a bid had
-- to reach, over asking: the winner's bid; asking where nobody bought or
-- the winner was you (the bids you beat are not seen); NULL for a
-- manager's listing (no one's deal with him is seen). The one auction
-- outcome: what bids win now (bid_odds) and a path's bids (my_bids) both
-- read it, and no bar before its close. Cut-bound: only what was observed
-- by t counts, and an outcome appears once the activity feed has been seen
-- ten minutes past the close (the window its buy is looked for in), so a
-- past t never sees a result the feed told of later.
DROP VIEW IF EXISTS auction_results;
CREATE VIEW auction_results AS
WITH mine AS (SELECT DISTINCT s.user_id AS uid FROM api_standings s, league_config c
              WHERE s.manager = c.me),
ends AS (SELECT pid, player, close, at, asking, seller FROM (
             SELECT m.player_id AS pid, a.player, julianday(m.expires_at) AS close,
                    m.expires_at AS at, m.sale_price AS asking, m.seller,
                    row_number() OVER (PARTITION BY m.player_id, julianday(m.expires_at)
                                       ORDER BY m.observed_at DESC, m.rowid) AS pick
             FROM api_market m JOIN app_player a ON a.app_id = m.player_id
             WHERE m.observed_at <= (SELECT cut()) AND m.seller IN ('marketPlayerLeague', 'marketPlayerTeam')
               AND coalesce(m.expires_at, '') <> '' AND coalesce(m.sale_price, 0) > 0)
         WHERE pick = 1),
buys AS (SELECT player_id AS pid, julianday(at) AS t, amount, user_id IN (SELECT uid FROM mine) AS me
         FROM (SELECT player_id, at, amount, user_id,
                      row_number() OVER (PARTITION BY activity_id ORDER BY rowid DESC) AS last
               FROM api_activity WHERE kind = 'buy' AND activity_id <> '' AND observed_at <= (SELECT cut()))
         WHERE last = 1),
won AS (SELECT * FROM (
            SELECT e.*, b.amount AS won, b.me,
                   row_number() OVER (PARTITION BY e.pid, e.close ORDER BY b.amount DESC) AS top
            FROM ends e LEFT JOIN buys b ON b.pid = e.pid AND abs(b.t - e.close) * 86400 < 600)
        WHERE top = 1)
SELECT w.player, w.at AS close, strftime('%Y-%m-%dT%H%MZ', w.at) AS closed, w.asking, w.seller, w.won, w.me AS by_me,
       CASE WHEN w.seller <> 'marketPlayerLeague' THEN NULL
            WHEN w.won IS NULL OR w.me THEN 1.0 ELSE w.won / w.asking END AS bar
FROM won w WHERE strftime('%Y-%m-%dT%H%MZ', w.at) <= (SELECT cut())
  AND stamp_jd((SELECT max(observed_at) FROM api_activity WHERE observed_at <= (SELECT cut())))
      >= w.close + 600.0 / 86400;

-- What a bid wins on the game's market now, for each player listed there:
-- his BID_NEIGHBOURS past listings closest to him in price trend (the
-- trend each had on the last day with a change up to its close; before
-- t, none you won: what you beat is not seen), and for each bid over
-- asking that reached one of their bars (and asking itself) the share it
-- would have won (p_win; 1 with no past to go on).
DROP VIEW IF EXISTS bid_odds;
CREATE VIEW bid_odds AS
WITH hist AS MATERIALIZED (SELECT ff_id, day, trend FROM price_trend_daily),
days AS MATERIALIZED (SELECT DISTINCT day FROM hist),
closed AS MATERIALIZED (
    SELECT player, bar, by_me, (SELECT max(day) FROM days WHERE day <= substr(closed, 1, 10)) AS day
    FROM auction_results),
past AS MATERIALIZED (
    SELECT h.trend, c.bar FROM closed c JOIN hist h ON h.ff_id = c.player AND h.day = c.day
    WHERE c.bar IS NOT NULL AND NOT coalesce(c.by_me, 0)),
now AS (SELECT DISTINCT s.player, coalesce(t.trend, 0.0) AS trend
        FROM listings s LEFT JOIN price_trend t ON t.ff_id = s.player
        WHERE s.seller = 'marketPlayerLeague' AND s.player IS NOT NULL AND s.sale_price <> 0),
near AS MATERIALIZED (
    SELECT player, bar FROM (
        SELECT n.player, p.bar,
               row_number() OVER (PARTITION BY n.player ORDER BY abs(p.trend - n.trend), p.bar) AS r
        FROM now n, past p)
    WHERE r <= (SELECT CAST(bid_neighbours AS INTEGER) FROM k)),
levels AS (SELECT player, max(bar, 1.0) AS ratio FROM near UNION SELECT player, 1.0 FROM now)
SELECT l.player, l.ratio,
       coalesce((SELECT avg(n.bar <= l.ratio) FROM near n WHERE n.player = l.player), 1.0) AS p_win
FROM levels l;

-- The game's nightly offers for listed players, each once (its first
-- sighting), over the player's value when it was made; by when made.
DROP VIEW IF EXISTS offer_ratios;
CREATE VIEW offer_ratios AS
WITH v AS (SELECT observed_at, player_team_id, market_value FROM (
               SELECT observed_at, player_team_id, coalesce(market_value, 0.0) AS market_value,
                      row_number() OVER (PARTITION BY observed_at, player_team_id
                                         ORDER BY rowid DESC) AS last
               FROM api_teams WHERE observed_at <= (SELECT cut()))
           WHERE last = 1),
o AS (SELECT coalesce(o.created_at, '') AS created_at, o.money / v.market_value AS ratio,
             row_number() OVER (PARTITION BY o.offer_id ORDER BY o.rowid) AS first
      FROM api_offers o JOIN v ON v.observed_at = o.observed_at
       AND v.player_team_id IS o.player_team_id
      WHERE o.observed_at <= (SELECT cut()) AND o.from_market = 'true' AND o.money <> 0
        AND v.market_value <> 0 AND coalesce(o.offer_id, '') <> '')
SELECT created_at, ratio FROM o WHERE first = 1 ORDER BY created_at, ratio;

-- The game's last PRICE_WINDOW offers as shares of value (every one with
-- no constants set), a night's batch (offers sharing a timestamp) whole: a cut
-- inside one would keep its highest or lowest by the order. What an offer to
-- come is drawn from.
DROP VIEW IF EXISTS recent_offers;
CREATE VIEW recent_offers AS
SELECT ratio FROM offer_ratios
WHERE created_at >= CASE WHEN (SELECT price_window FROM k) < 1 THEN ''
    ELSE coalesce((SELECT created_at FROM offer_ratios ORDER BY created_at DESC
                   LIMIT 1 OFFSET (SELECT CAST(price_window AS INTEGER) - 1 FROM k)), '') END;

-- The jornada calendar, as of t. Each fixture's kickoff as last seen
-- (a played match's page drops it) and its jornada, as a UTC stamp (as
-- observed_at is), and as a julian day (t).
DROP VIEW IF EXISTS kickoffs;
CREATE VIEW kickoffs AS
SELECT home, away, jornada, kick, t FROM (
    SELECT home, away, jornada, strftime('%Y-%m-%dT%H%MZ', kickoff) AS kick, julianday(kickoff) AS t,
           /* newest home, away */ AS n
    FROM matches
    WHERE observed_at <= (SELECT cut()) AND jornada IS NOT NULL
      AND strftime('%Y-%m-%dT%H%MZ', kickoff) IS NOT NULL)
WHERE n = 1;

-- Results seen for a fixture no kickoff was ever seen for (jornada 1's,
-- scraped after it was played), with when each was first seen.
DROP VIEW IF EXISTS blind_results;
CREATE VIEW blind_results AS
SELECT home, away, jornada, min(observed_at) AS seen FROM matches m
WHERE observed_at <= (SELECT cut()) AND score <> '' AND jornada IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM kickoffs k WHERE k.home = m.home AND k.away = m.away)
GROUP BY home, away;

-- The jornada calendar as of t, one row a jornada, what everything
-- about locks and jornadas left reads: when it locks (its first kickoff;
-- NULL with a blind result, its first kickoff not known), when its next
-- match still to play kicks off (NULL with none), and its state, by its
-- first kickoff known:
--   done       every match has a score;
--   behind     locked before the last jornada to lock, a match still to
--              play (put back): its elevens were set long before, so it
--              locks nothing and no line-up is picked for it;
--   under way  the last to lock, a match still to play;
--   ahead      not locked yet.
DROP VIEW IF EXISTS jornada_calendar;
CREATE VIEW jornada_calendar AS
WITH games AS (SELECT m.jornada, k.kick, m.score <> '' AS played
               FROM current_matches m LEFT JOIN kickoffs k ON k.home = m.home AND k.away = m.away
               WHERE m.jornada IS NOT NULL),
j AS (SELECT jornada, min(kick) AS start,
             CASE WHEN jornada NOT IN (SELECT jornada FROM blind_results) THEN min(kick) END AS lock,
             min(CASE WHEN NOT played THEN kick END) AS next, sum(NOT played) AS left
      FROM games GROUP BY jornada),
last AS (SELECT max(start) AS start FROM j WHERE start <= (SELECT cut()))
SELECT j.jornada, j.lock, j.next,
       CASE WHEN j.left = 0 THEN 'done'
            WHEN j.start < last.start THEN 'behind'
            WHEN j.start <= (SELECT cut()) THEN 'under way'
            ELSE 'ahead' END AS state
FROM j, last;

-- The clubs that have played in each jornada not done: whose players a
-- jornada left has no match for.
DROP VIEW IF EXISTS clubs_played;
CREATE VIEW clubs_played AS
WITH p AS (SELECT m.jornada, m.home, m.away FROM current_matches m JOIN jornada_calendar j USING (jornada)
           WHERE j.state <> 'done' AND m.score <> '')
SELECT jornada, home AS club FROM p WHERE home <> ''
UNION SELECT jornada, away FROM p WHERE away <> '';

-- When each jornada locks (jornada_calendar; one with no lock known left out).
DROP VIEW IF EXISTS round_locks;
CREATE VIEW round_locks AS
SELECT jornada, lock FROM jornada_calendar WHERE lock IS NOT NULL;

-- When each jornada not done next plays (jornada_calendar; a postponed
-- match dates the rest of its jornada): what the season ahead is ordered
-- and dated by.
DROP VIEW IF EXISTS rounds_ahead;
CREATE VIEW rounds_ahead AS
SELECT jornada, next FROM jornada_calendar WHERE next IS NOT NULL;

DROP VIEW IF EXISTS match_jornada;
CREATE VIEW match_jornada AS
SELECT match_id, jornada FROM current_matches WHERE jornada IS NOT NULL;

-- When each club's matches count as played, to credit points to: the
-- kickoff, else when the result was first seen.
DROP VIEW IF EXISTS club_games;
CREATE VIEW club_games AS
WITH g AS (SELECT home, away, jornada, kick AS at FROM kickoffs
           UNION ALL SELECT home, away, jornada, seen FROM blind_results)
SELECT home AS club, jornada, at FROM g WHERE home <> ''
UNION ALL SELECT away, jornada, at FROM g WHERE away <> '';

-- Points: each change in a player's points total this season, credited
-- to the jornada of his club's last match by then (any club's, if his club
-- is not known); not the first fetch's, which is the season so far, unless
-- it holds a single game (the season's first, a jornada's). id is the points
-- row's: the order they were stored.
DROP VIEW IF EXISTS scored;
CREATE VIEW scored AS
WITH s AS (SELECT max(season) AS season, min(observed_at) AS first FROM points
           WHERE observed_at <= (SELECT cut())
             AND season = (SELECT max(season) FROM points WHERE observed_at <= (SELECT cut()))),
p AS (SELECT ff_id, observed_at AS at, points, games, points.rowid AS id,
             lag(points, 1, 0.0) OVER w AS points0, lag(games, 1, 0.0) OVER w AS games0
      FROM points, s
      WHERE observed_at <= (SELECT cut()) AND points.season = s.season AND ff_id <> ''
      WINDOW w AS (PARTITION BY ff_id ORDER BY observed_at, points.rowid)),
c AS (SELECT player AS ff_id, club FROM player_club),
g AS MATERIALIZED (SELECT club, jornada, at FROM club_games
                   UNION ALL SELECT '*', jornada, at FROM club_games),
credited AS (
    SELECT p.ff_id AS player, p.at, p.id, p.points - p.points0 AS pts, p.games - p.games0 AS games,
           CASE WHEN p.at = s.first     -- a single game seen at the first fetch: his club's first jornada
                THEN (SELECT min(g.jornada) FROM g WHERE g.club = coalesce(c.club, '*'))
                ELSE (SELECT g.jornada FROM g WHERE g.club = coalesce(c.club, '*') AND g.at <= p.at
                      ORDER BY g.at DESC, g.jornada DESC LIMIT 1) END AS jornada
    FROM p, s LEFT JOIN c ON c.ff_id = p.ff_id
    WHERE (p.at > s.first OR p.games <= 1) AND NOT (p.points IS p.points0 AND p.games IS p.games0))
SELECT player, jornada, pts, at, id FROM credited WHERE jornada IS NOT NULL;

-- Who was in each matchday squad (starters and subs) and his minutes: a
-- starter the whole match unless he came off, a sub none unless he came
-- on (appearances keeps those with some).
DROP VIEW IF EXISTS squad_minutes;
CREATE VIEW squad_minutes AS
SELECT s.match_id, s.team_slug, s.player_slug, s.player, mj.jornada,
       max(0.0, CASE s.role WHEN 'starter' THEN coalesce(s.minute, k.match_len)
                            ELSE coalesce(k.match_len - s.minute, 0.0) END) AS mins
FROM current_starters s JOIN match_jornada mj USING (match_id), k
WHERE s.role IN ('starter', 'sub') AND s.player_slug <> '';

-- Each scoring player's jornadas (scored or in the matchday squad) and his
-- points.
DROP VIEW IF EXISTS player_jornadas;
CREATE VIEW player_jornadas AS
WITH s AS MATERIALIZED (SELECT player, jornada, pts FROM scored)
SELECT player, jornada, sum(pts) AS pts FROM (
    SELECT player, jornada, pts FROM s
    UNION ALL
    SELECT player, jornada, 0.0 FROM squad_minutes
    WHERE player IN (SELECT player FROM s))
GROUP BY player, jornada;

-- His season so far: points, appearances, and matchday squads (jornadas
-- he scored or was in the squad); and pick, the chance he is picked if fit
-- after his next match: that record (appearances over matchday squads)
-- pulled toward NEUTRAL_START, which is worth PICK_K matches. In one view,
-- so the forecast derives the season once a moment.
DROP VIEW IF EXISTS season_record;
CREATE VIEW season_record AS
WITH r AS (SELECT p.player, sum(p.pts) AS pts, count(a.jornada) * 1.0 AS apps, count(*) * 1.0 AS squads
           FROM player_jornadas p LEFT JOIN appearances a ON a.player = p.player AND a.jornada = p.jornada
           GROUP BY p.player)
SELECT r.*, shrink(k.neutral_start / 100.0, k.pick_k, apps, squads) AS pick FROM r, k;

-- Each app id's player: the futbolfantasy player whose value it equalled
-- at the most market snapshots (an all-players row holds until his next),
-- then the most name stems shared (stems_meet); each app id's unique best,
-- then per player the unique best. Over all rows, not as of t.
CREATE INDEX IF NOT EXISTS market_by_value ON market (value);
DROP VIEW IF EXISTS app_links;
CREATE VIEW app_links AS
WITH snap AS (SELECT observed_at, player_id AS aid, market_value AS v, player_name AS n1, player_name_full AS n2
              FROM api_teams WHERE market_value > 0
              UNION ALL SELECT observed_at, player_id, market_value, player_name, player_name_full
              FROM api_market WHERE market_value > 0),
held AS (SELECT * FROM (SELECT player_id AS aid, market_value AS v, player_name AS n1, player_name_full AS n2,
                               observed_at AS since,
                               coalesce(lead(observed_at) OVER (PARTITION BY player_id ORDER BY observed_at, rowid),
                                        '9999') AS until
                        FROM api_players_all) WHERE v > 0),
ff AS (SELECT observed_at, ff_id, value AS v, name FROM market WHERE ff_id <> '' AND value > 0),
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
              FROM market WHERE ff_id <> '')
SELECT l.ff_id AS player_id, l.name, l.club AS club_id, coalesce(a.app_id, '') AS app_id
FROM last l LEFT JOIN app_links a ON a.player = l.ff_id WHERE l.n = 1;

-- The jornadas every match of which has a score (jornada_calendar): what a
-- forecast is graded on (one under way, or with a match postponed, is not).
DROP VIEW IF EXISTS jornadas_done;
CREATE VIEW jornadas_done AS
SELECT jornada FROM jornada_calendar WHERE state = 'done' ORDER BY jornada;

-- Last season's points per player and pj, the games the page gives: the
-- one count of matches played that is not appearances, for no line-ups of
-- last season are kept. It is points' own rows of the season before the
-- latest one as of t, each player's last.
DROP VIEW IF EXISTS last_season;
CREATE VIEW last_season AS
SELECT p.ff_id, p.points AS pts, p.games AS pj FROM points p
WHERE p.observed_at <= (SELECT cut())
  AND p.season = (SELECT max(season) FROM points WHERE observed_at <= (SELECT cut())
                  AND season < (SELECT max(season) FROM points WHERE observed_at <= (SELECT cut())))
  AND p.rowid = (SELECT max(rowid) FROM points x WHERE x.season = p.season AND x.ff_id = p.ff_id AND x.observed_at <= (SELECT cut()));

-- What a player's points per match is pulled toward (forecast_ppm): what
-- his price says, the least-squares line of last season's points per match
-- (10+ matches) on log value in his position, at his value (never below
-- 0); a position with fewer than three, or a player with no value, his
-- position's mean; no position, everyone's median.
DROP VIEW IF EXISTS ppm_prior;
CREATE VIEW ppm_prior AS
WITH m AS (SELECT c.player, c.pos, ln(v.value) AS x FROM player_club c LEFT JOIN player_value v USING (player)),
s AS (SELECT m.pos, m.x, l.pts / l.pj AS y FROM m JOIN last_season l ON l.ff_id = m.player
      WHERE m.pos <> '' AND m.x IS NOT NULL AND l.pj >= 10),
f AS (SELECT pos, avg(y) AS my, avg(x) AS mx,
             CASE WHEN count(x) >= 3 AND avg(x * x) > avg(x) * avg(x)
                  THEN (avg(x * y) - avg(x) * avg(y)) / (avg(x * x) - avg(x) * avg(x)) ELSE 0.0 END AS b
      FROM s GROUP BY pos)
SELECT m.player, coalesce(max(f.my + f.b * coalesce(m.x - f.mx, 0.0), 0.0),
                          (SELECT median(y) FROM s)) AS prior
FROM m LEFT JOIN f USING (pos);

-- The forecast's facts at t, one home each, keyed by player (or club) and
-- jornada.

-- Players and clubs: each player on futbolfantasy's market at t, his name,
-- club and position (slot: '' for a coach, who fills no slot).
DROP VIEW IF EXISTS start_calibration;
DROP VIEW IF EXISTS availability_tally;
DROP VIEW IF EXISTS last_fit;
DROP VIEW IF EXISTS fit_rate;
DROP VIEW IF EXISTS start_outcomes;
DROP VIEW IF EXISTS player_club;
CREATE VIEW player_club AS
SELECT ff_id AS player, name, nullif(club, '') AS club, slot(position) AS pos
FROM current_market WHERE ff_id <> '' GROUP BY ff_id;

-- Players and values: his value on the market at t (none at 0).
DROP VIEW IF EXISTS player_value;
CREATE VIEW player_value AS
SELECT ff_id AS player, value FROM current_market WHERE ff_id <> '' AND value > 0 GROUP BY ff_id;

-- Players and appearances: each jornada a player started or came on in
-- (the line-ups; one home for "matches played"). The jornadas of a player
-- scored in or squadded in without one are not.
DROP VIEW IF EXISTS appearances;
CREATE VIEW appearances AS
SELECT DISTINCT player, jornada FROM squad_minutes WHERE player IS NOT NULL AND mins > 0;

-- Players and expected status at t: futbolfantasy's last listing of him
-- (start %, when, status and note if flagged: not ok), or the app's word
-- that he has left the league (status 'gone'). One row a player listed or gone.
DROP VIEW IF EXISTS player_status;
CREATE VIEW player_status AS
WITH l AS (SELECT player, start_pct, observed_at, status, note,
                  row_number() OVER (PARTITION BY player ORDER BY status NOT IN ('', 'ok') DESC, start_pct DESC) AS n
           FROM current_lineups WHERE source = 'futbolfantasy' AND player IS NOT NULL),
gone AS (SELECT DISTINCT player FROM current_api_players_all WHERE player IS NOT NULL AND player_status = 'out_of_league')
SELECT coalesce(l.player, g.player) AS player, l.player IS NOT NULL AS listed, l.start_pct AS pct, l.observed_at AS at,
       CASE WHEN g.player IS NOT NULL THEN 'gone' WHEN l.status NOT IN ('', 'ok') THEN l.status END AS status,
       CASE WHEN g.player IS NULL THEN l.note ELSE '' END AS note
FROM (SELECT * FROM l WHERE n = 1) l FULL JOIN gone g ON g.player = l.player;

-- Clubs' fixtures: each club's match still to play in each jornada not
-- done, its opponent and kickoff (a stamp; NULL if none is known).
DROP VIEW IF EXISTS club_fixture;
CREATE VIEW club_fixture AS
WITH m AS (SELECT m.jornada, m.home, m.away, k.kick FROM current_matches m
           JOIN jornada_calendar c ON c.jornada = m.jornada AND c.state <> 'done'
           LEFT JOIN kickoffs k ON k.home = m.home AND k.away = m.away
           WHERE m.score = '')
SELECT jornada, home AS club, away AS opp, kick FROM m WHERE home <> ''
UNION SELECT jornada, away, home, kick FROM m WHERE away <> '';

-- Clubs' goals at t (results_history, every season): for (attack) and
-- against (defence) a match, over the league's, each pulled toward 1 by
-- FIXTURE_K matches at the league's rate.
DROP VIEW IF EXISTS club_rating;
CREATE VIEW club_rating AS
WITH g AS (SELECT home AS club, home_goals AS gf, away_goals AS ga FROM current_results_history
           WHERE home <> '' AND home_goals IS NOT NULL AND away_goals IS NOT NULL
           UNION ALL SELECT away, away_goals, home_goals FROM current_results_history
           WHERE away <> '' AND home_goals IS NOT NULL AND away_goals IS NOT NULL),
mu AS (SELECT avg(gf) AS mu FROM g)
SELECT club, count(*) AS n, (sum(gf) + k.fixture_k * mu) / ((count(*) + k.fixture_k) * mu) AS attack,
       (sum(ga) + k.fixture_k * mu) / ((count(*) + k.fixture_k) * mu) AS defence
FROM g, mu, k WHERE mu > 0 GROUP BY club;

-- Players on futbolfantasy's market at t, and those only on its line-ups: his
-- name, position (pos: the position's slot, '' on a line-up only, none for a
-- coach) and value. One row a player; what player_facts starts from.
DROP VIEW IF EXISTS player_roster;
CREATE VIEW player_roster AS
WITH who AS (
    SELECT ff_id AS player, trim(name) AS name, position, value FROM current_market
    WHERE ff_id <> '' AND lower(coalesce(position, '')) <> 'entrenador'
    UNION ALL
    SELECT player, trim(player_name), '', NULL FROM current_lineups
    WHERE source = 'futbolfantasy' AND player IS NOT NULL
      AND player NOT IN (SELECT ff_id FROM current_market))
SELECT player, name, position(upper(position)) AS pos, value FROM who;

-- The game's market at t: each listing (the game's: seller marketPlayerLeague,
-- a manager's: marketPlayerTeam) with its asking price, value, close, the bids
-- on it, and your bid (bid_money, bid_status) if any.
DROP VIEW IF EXISTS listings;
CREATE VIEW listings AS
SELECT player, player_id, market_id, bid_id, seller, expires_at, sale_price, market_value, bids, bid_money, bid_status
FROM current_api_market;

-- The game's nightly offers standing at t, for your listed players (by
-- player_team_id, as squads has them).
DROP VIEW IF EXISTS offers;
CREATE VIEW offers AS
SELECT offer_id, player_team_id, status, money, created_at, expires_at, from_market FROM current_api_offers;

-- Every squad at t: who holds each player, his value and release clause (buyout).
DROP VIEW IF EXISTS squads;
CREATE VIEW squads AS
SELECT player, player_id, player_team_id, manager, buyout, market_value FROM current_api_teams;

-- The league's standings at t: each manager's points, value and cash.
DROP VIEW IF EXISTS standings;
CREATE VIEW standings AS
SELECT user_id, manager, position, team_points, fixture_points, team_value, team_money FROM current_api_standings;

-- Every snapshot of futbolfantasy's market up to t: a player's value at each,
-- id the order they were kept in.
DROP VIEW IF EXISTS price_history;
CREATE VIEW price_history AS
SELECT rowid AS id, observed_at, ff_id, name, position, club, value FROM market
WHERE observed_at <= (SELECT cut());

-- The forecast, as SQL over those.

-- Each player's rates: ppm, his points per match, what his value says
-- (ppm_prior) pulled toward last season's points per match, then toward
-- this season's, each by SHRINK_K matches; and picked, the chance he is
-- picked: his listing's start %, as the site says, whatever his status.
-- With no % (listed with none, injured or not, or not listed) it is
-- NEUTRAL_START (ABSENT_START if not listed) pulled toward his appearances
-- over his matchday squads by PICK_K. Measured: the listing beats the pull.
DROP VIEW IF EXISTS forecast_ppm;
DROP VIEW IF EXISTS forecast_picked;
DROP VIEW IF EXISTS forecast_rate;
CREATE VIEW forecast_rate AS
WITH c AS MATERIALIZED (SELECT player FROM player_club),
r AS MATERIALIZED (SELECT * FROM season_record),
p AS MATERIALIZED (SELECT * FROM ppm_prior),
s AS MATERIALIZED (SELECT * FROM player_status)
SELECT c.player,
       shrink(shrink(coalesce(p.prior, 0.0), k.shrink_k, coalesce(l.pts, 0.0), coalesce(l.pj, 0.0)),
              k.shrink_k, coalesce(r.pts, 0.0), coalesce(r.apps, 0.0)) AS ppm,
       coalesce(s.pct / 100.0, shrink(CASE WHEN s.listed OR s.status IS NOT NULL THEN k.neutral_start ELSE k.absent_start END / 100.0,
                                      k.pick_k, coalesce(r.apps, 0.0), coalesce(r.squads, 0.0))) AS picked,
       shrink(CASE WHEN s.listed OR s.status IS NOT NULL THEN k.neutral_start ELSE k.absent_start END / 100.0,
              k.pick_k, coalesce(r.apps, 0.0), coalesce(r.squads, 0.0)) AS record,
       s.pct IS NOT NULL AS listed_pct
FROM c CROSS JOIN k LEFT JOIN p USING (player) LEFT JOIN last_season l ON l.ff_id = c.player
LEFT JOIN r USING (player) LEFT JOIN s USING (player);

-- The chance he is fit each jornada his club has left: 1 unless he has a status
-- (injured, doubtful, suspended); if so, his prognosis (prognosis()) read literally against the
-- jornada (a jornada not named: his club's next) or his match's date: out
-- 0, a doubt 0.5, back 1.
DROP VIEW IF EXISTS forecast_fit;
CREATE VIEW forecast_fit AS
WITH g AS MATERIALIZED (SELECT player, prognosis(status, note, at) AS g FROM player_status WHERE status IS NOT NULL),
f AS MATERIALIZED (SELECT * FROM club_fixture),
nx AS MATERIALIZED (SELECT club, jornada AS nx FROM (SELECT club, jornada, row_number() OVER
           (PARTITION BY club ORDER BY kick IS NULL, kick, jornada) AS n FROM f) WHERE n = 1),
c AS MATERIALIZED (SELECT player, club FROM player_club WHERE club IS NOT NULL),
x AS (SELECT c.player, f.jornada, f.kick, json_extract(g.g, '$.kind') AS kind, json_extract(g.g, '$.until') AS until,
             coalesce(json_extract(g.g, '$.j'), nx.nx) AS j
      FROM c JOIN f ON f.club = c.club JOIN nx ON nx.club = c.club
      LEFT JOIN g ON g.player = c.player)
SELECT player, jornada, CASE kind
    WHEN 'never' THEN 0.0
    WHEN 'until' THEN CASE WHEN kick IS NULL OR substr(kick, 1, 10) >= until THEN 1.0 ELSE 0.0 END
    WHEN 'doubt' THEN CASE WHEN jornada < j THEN 0.0 WHEN jornada = j THEN 0.5 ELSE 1.0 END
    WHEN 'out' THEN CASE WHEN jornada <= j THEN 0.0 ELSE 1.0 END
    WHEN 'back' THEN CASE WHEN jornada < j THEN 0.0 ELSE 1.0 END
    ELSE 1.0 END AS fit
FROM x;

-- Each player's cell each jornada his club has left (a coach, or a player
-- with no club, has none): the fixture (an attacker's opponent's defence, a
-- goalkeeper's or defender's 1 / its attack; 1 with no rating), points if
-- he plays max(0, ppm x fixture), the chance he plays picked x fit.
DROP VIEW IF EXISTS forecast_now;
CREATE VIEW forecast_now AS
WITH c AS MATERIALIZED (SELECT player, club, pos FROM player_club WHERE pos <> '' AND club IS NOT NULL),
f AS MATERIALIZED (SELECT * FROM club_fixture),
o AS MATERIALIZED (SELECT * FROM club_rating),
rt AS MATERIALIZED (SELECT * FROM forecast_rate),
ft AS MATERIALIZED (SELECT * FROM forecast_fit),
x AS (SELECT c.player, f.jornada, rt.ppm, rt.picked, rt.record, rt.listed_pct, ft.fit,
             f.jornada - min(f.jornada) OVER (PARTITION BY c.club) AS h,
             coalesce(CASE WHEN c.pos IN ('POR', 'DEF') THEN 1.0 / o.attack ELSE o.defence END, 1.0) AS fixture
      FROM c JOIN f ON f.club = c.club JOIN rt USING (player)
      JOIN ft ON ft.player = c.player AND ft.jornada = f.jornada
      LEFT JOIN o ON o.club = f.opp)
SELECT player, jornada, max(0.0, ppm * fixture) AS pts,
       min(1.0, q + p_lift * q * (1.0 - q)) AS p, ppm, fixture, picked, fit
FROM (SELECT y.*, k.p_lift, y.w * y.picked * CASE WHEN y.listed_pct THEN 1.0 ELSE y.fit END + (1.0 - y.w) * y.record * y.fit AS q
      FROM (SELECT x.*, k.p_listing * pow(k.p_decay, x.h) AS w FROM x, k) y, k);
