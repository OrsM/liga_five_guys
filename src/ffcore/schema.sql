-- data/lfg.db, applied on every connect (ffcore/db.py). Only adds: a
-- changed table is dropped by hand and rebuilt (docs/plan-datasette.md).

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

-- The decision a run made (decision.plan, ffcore/decide.sql): every single
-- move valued from the squad as it is (dec_rows), and the plan's moves in
-- order (dec_plan; a swap is a buy and a sale of one step), in points.
CREATE TABLE IF NOT EXISTS decision_option (
    run_id  INTEGER NOT NULL REFERENCES runs(run_id),
    kind    TEXT NOT NULL,           -- buy, sell
    player  TEXT NOT NULL,
    amount  REAL,                    -- the bid, or what the sale gets
    win     REAL NOT NULL,
    gain    REAL NOT NULL,           -- points
    worth   REAL NOT NULL,
    in_plan INTEGER NOT NULL,
    PRIMARY KEY (run_id, kind, player)
);
CREATE TABLE IF NOT EXISTS decision_plan (
    run_id INTEGER NOT NULL REFERENCES runs(run_id),
    step   INTEGER NOT NULL,
    kind   TEXT NOT NULL,
    player TEXT NOT NULL,
    amount REAL NOT NULL,
    win    REAL NOT NULL,
    worth  REAL NOT NULL,            -- the step's, in points
    after  INTEGER NOT NULL DEFAULT 0, -- a sale made after the auctions (decide.sql dec_when)
    PRIMARY KEY (run_id, step, kind)
);

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

-- What the simulation and the plan search decided, a run each (sim.main):
-- what the board views add the market's and the league's facts to.
-- Each manager's season if you do nothing: mean points, 10th and 90th
-- percentile, expected final position, the chance of first, and the
-- chance he finishes above you (NULL for you).
CREATE TABLE IF NOT EXISTS sim_standings (
    run_id   INTEGER NOT NULL REFERENCES runs(run_id),
    manager  TEXT NOT NULL,
    me       INTEGER NOT NULL,
    now      REAL,                 -- points so far
    mean     REAL,
    lo       REAL,
    hi       REAL,
    finish   REAL,
    p_first  REAL,
    p_above  REAL,
    cash     REAL,                 -- yours from the app, a rival's estimated from the feed
    PRIMARY KEY (run_id, manager)
);

-- Your squad (mine), the market and the plan's buys: the chance he plays
-- next, expected points next and over the season, in the best eleven now (xi).
CREATE TABLE IF NOT EXISTS sim_players (
    run_id      INTEGER NOT NULL REFERENCES runs(run_id),
    player      TEXT NOT NULL,
    start       REAL,
    next        REAL,
    season      REAL,
    mine        INTEGER NOT NULL,
    xi          INTEGER NOT NULL,
    PRIMARY KEY (run_id, player)
);

-- The market and the league as a universe was built on them, a cut's and
-- a variant's: player_facts with what a sale of each gets (sale_odds), and
-- each buy's bids and their chance (bid_odds). Written by assemble.see,
-- read by Market, the decision (decide.sql) and a run's facts.
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

-- What the model looks at, one row, the process's own (ffcore.db.LOOK,
-- never the file's): t, the one moment every view is read as of (cut(): a
-- run's start, or each past time a replay stands at, set_now), the market
-- as seen then (seen at that cut), the forecast of a moment (forecast_used)
-- and who you are (assemble.built). Your squad and cash at t are those at
-- t0 with your moves since (a path's: path, t0, a stamp; live, none: t0 is
-- t and the plan's moves are decide.sql's dec_plan).
DROP VIEW IF EXISTS look;
CREATE VIEW look AS
SELECT cut() AS cut, variant() AS variant, moment() AS moment, me() AS me, path() AS path, t0() AS t0;

-- A path's moves, each at its t (a stamp): a bid on a player of the game's
-- market at its amount, one taken back (withdraw), a sale at what it gets.
-- What you would have done (a replay's phantom, grading.phantom_path) is
-- an input beside the facts, never a changed world: the league at t reads
-- look's path's moves in (t0, t] (my_bids, my_squad, my_cash).
CREATE TABLE IF NOT EXISTS moves (
    path   TEXT NOT NULL,
    at     TEXT NOT NULL,
    kind   TEXT NOT NULL,            -- bid, withdraw, sell
    player TEXT NOT NULL,
    amount REAL,
    PRIMARY KEY (path, at, kind, player)
) WITHOUT ROWID;

-- The path's moves in (t0, t] (look; none live).
DROP VIEW IF EXISTS my_moves;
CREATE VIEW my_moves AS
SELECT m.at, m.kind, m.player, m.amount FROM look l JOIN moves m ON m.path = l.path
WHERE l.t0 <> '' AND m.at > l.t0 AND m.at <= l.cut;

-- Each bid of the path's, and the auction it is settled in: his first
-- close on the game's market after it (auction_results: closed by t; close,
-- a stamp), and whether it won there (it reaches what that auction took:
-- bar x asking; nothing reaches none). A bid taken back or bid again
-- before its close is no bid. won NULL: his auction is still to close at
-- t, the bid stands.
DROP VIEW IF EXISTS my_bids;
CREATE VIEW my_bids AS
WITH m AS MATERIALIZED (SELECT * FROM my_moves),
r AS MATERIALIZED (SELECT player, closed AS close, asking, bar FROM auction_results
                   WHERE seller = 'marketPlayerLeague' AND player IN (SELECT player FROM m WHERE kind = 'bid')),
b AS (SELECT m.at, m.player, m.amount, (SELECT min(r.close) FROM r WHERE r.player = m.player AND r.close > m.at) AS close
      FROM m WHERE m.kind = 'bid')
SELECT b.at, b.player, b.amount, b.close,
       CASE WHEN b.close IS NOT NULL THEN coalesce(b.amount >= r.bar * r.asking, 0) END AS won
FROM b LEFT JOIN r ON r.player = b.player AND r.close = b.close
WHERE NOT EXISTS (SELECT 1 FROM m n WHERE n.player = b.player AND n.kind IN ('bid', 'withdraw')
                  AND n.at > b.at AND (b.close IS NULL OR n.at < b.close));

-- Your squad at t: whom you held at t0 (seen then), and whom the path has
-- bought (bids won by t) or sold since, by the last of them; each at his
-- position as seen at t (else at t0).
DROP VIEW IF EXISTS my_squad;
CREATE VIEW my_squad AS
WITH l AS (SELECT cut, variant, me, CASE WHEN t0 = '' THEN cut ELSE t0 END AS t0 FROM look),
start AS (SELECT s.player FROM l JOIN seen s ON s.cut = l.t0 AND s.variant = l.variant AND s.owner = l.me),
ev AS (SELECT player, at AS t, 0 AS held FROM my_moves WHERE kind = 'sell'
       UNION ALL SELECT player, close, 1 FROM my_bids WHERE won),
last AS (SELECT player, held FROM (SELECT player, held, row_number() OVER (PARTITION BY player ORDER BY t DESC) AS n
                                   FROM ev) WHERE n = 1),
k AS (SELECT player FROM start WHERE player NOT IN (SELECT player FROM last)
      UNION SELECT player FROM last WHERE held)
SELECT k.player, coalesce(n.pos, o.pos, 'MED') AS pos
FROM k CROSS JOIN l
LEFT JOIN seen n ON n.cut = l.cut AND n.variant = l.variant AND n.player = k.player
LEFT JOIN seen o ON o.cut = l.t0 AND o.variant = l.variant AND o.player = k.player;

-- Your cash at t: as seen at t0 (seen_cash), less the bids the path won by
-- t, plus its sales and the league's bonuses to you since (the ledger's:
-- the money the game pays whatever you do).
DROP VIEW IF EXISTS my_cash;
CREATE VIEW my_cash AS
WITH l AS (SELECT cut, variant, me, t0 FROM look)
SELECT coalesce((SELECT c.cash FROM seen_cash c WHERE c.cut = CASE WHEN l.t0 = '' THEN l.cut ELSE l.t0 END
                 AND c.variant = l.variant AND c.manager = l.me), 0.0)
       - (SELECT total(amount) FROM my_bids WHERE won)
       + (SELECT total(amount) FROM my_moves WHERE kind = 'sell')
       + CASE WHEN l.t0 = '' THEN 0.0 ELSE (
             SELECT total(g.amount) FROM ledger g WHERE g.kind = 'bonus' AND g.who = l.me
             AND julianday(g.at) > stamp_jd(l.t0) AND julianday(g.at) <= stamp_jd(l.cut)) END AS cash
FROM l;

-- What waiting to sell was worth as the run saw it (waiting as of its cut),
-- a row a player of yours with an offer standing (kind 'sale'): the board's sales read it.
CREATE TABLE IF NOT EXISTS sim_waiting (
    run_id INTEGER NOT NULL REFERENCES runs(run_id),
    player TEXT NOT NULL,
    kind   TEXT NOT NULL,
    later  REAL,
    PRIMARY KEY (run_id, player, kind)
);

-- The forecast moment the run was built on (forecast_cell keeps it while a
-- run points to it), and your chance of first and expected finish once the
-- plan is made, every bid won (Universe.standings on its squad).
CREATE TABLE IF NOT EXISTS sim_runs (
    run_id        INTEGER PRIMARY KEY REFERENCES runs(run_id),
    moment        INTEGER REFERENCES forecast_moment(moment),
    cut           TEXT,              -- the market as seen (seen)
    p_first_after REAL,
    finish_after  REAL,
    cash_after    REAL,
    can_bid       REAL
);

-- The market's and the league's facts as each run saw them (seen at its
-- cut), every player: the board shows these, and a past run's board is
-- these and its decisions. fetches is what a sale of one of yours gets
-- (the offer or what waiting is worth, else his value; NULL if not yours).
DROP VIEW IF EXISTS sim_facts;
CREATE VIEW sim_facts AS
SELECT r.run_id, s.player, name_shown(s.name) AS name, coalesce(s.pos, '') AS pos, s.value, s.trend, s.paid,
       s.offer, s.clause, s.price, s.route, s.my_bid,
       CASE WHEN p.mine THEN coalesce(s.fetches, s.value, 0.0) END AS fetches
FROM sim_runs r JOIN seen s ON s.cut = r.cut AND s.variant = 'live'
LEFT JOIN sim_players p ON p.run_id = r.run_id AND p.player = s.player;

-- grading, when run.py grade finds something new: each past lock forecast
-- with the run's code (grading.backtest), the moment of it graded on each
-- jornada done up to GRADE_AHEAD past the lock (forecast_cell keeps it
-- while a grade points to it).
CREATE TABLE IF NOT EXISTS grade_moment (
    run_id  INTEGER NOT NULL REFERENCES runs(run_id),
    from_j  INTEGER NOT NULL,      -- the lock forecast from
    jornada INTEGER NOT NULL,
    moment  INTEGER NOT NULL REFERENCES forecast_moment(moment),
    PRIMARY KEY (run_id, from_j, jornada)
);

-- The forecast (forecast_now) as a decision at t reads it, made into a
-- moment each time (assemble.forecast): every player's cell a jornada and
-- its factors, and who was flagged then. code says what made a moment kept
-- from before ('sql' for every one made now); a moment no run or grade
-- points to is dropped once its cut and variant are made again.
CREATE TABLE IF NOT EXISTS forecast_moment (
    moment  INTEGER PRIMARY KEY,
    code    TEXT NOT NULL DEFAULT 'sql',
    as_of   TEXT NOT NULL,
    variant TEXT NOT NULL DEFAULT 'live'
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

-- The standings forecast held to account (grading.sim_calibration): each
-- manager's points in a clean jornada against the forecast of it
-- at the lock (pit: where he fell, 0..1; calibrated is uniform), and you
-- minus him the same way (diff_*), which is what who-finishes-above uses.
CREATE TABLE IF NOT EXISTS sim_calibration (
    run_id      INTEGER NOT NULL REFERENCES runs(run_id),
    jornada     INTEGER NOT NULL,
    manager     TEXT NOT NULL,
    actual      REAL NOT NULL,
    mean        REAL NOT NULL,
    sd          REAL NOT NULL,
    pit         REAL NOT NULL,
    diff_actual REAL,
    diff_mean   REAL,
    diff_sd     REAL,
    diff_pit    REAL,
    PRIMARY KEY (run_id, jornada, manager)
);


-- The phantom manager (grading.phantom_season): the points of his eleven
-- each jornada, beside the same squad never moved, what he did, and his
-- cash and his squad's worth once he had.
CREATE TABLE IF NOT EXISTS phantom_jornadas (
    run_id   INTEGER NOT NULL REFERENCES runs(run_id),
    jornada  INTEGER NOT NULL,
    phantom  REAL NOT NULL,
    standpat REAL NOT NULL,
    moves    TEXT NOT NULL,
    cash     REAL NOT NULL,
    value    REAL,
    PRIMARY KEY (run_id, jornada)
);

-- The boards published before the board was kept as rows, from git
-- (tools/board_history.py): head and moves, one board a generated_at.
CREATE TABLE IF NOT EXISTS board_history (
    generated_at TEXT PRIMARY KEY,
    commit_sha   TEXT NOT NULL,
    plan_gain    REAL,
    finish       REAL,
    p_win        REAL
);
CREATE TABLE IF NOT EXISTS board_history_moves (
    generated_at TEXT NOT NULL REFERENCES board_history(generated_at),
    ord          INTEGER NOT NULL,
    what         TEXT NOT NULL,    -- field, buy, sell
    name         TEXT,
    gain         REAL,
    PRIMARY KEY (generated_at, ord)
);

-- Every move each replayed board ranked, against what its players then did.
CREATE TABLE IF NOT EXISTS grade_decisions (
    run_id     INTEGER NOT NULL REFERENCES runs(run_id),
    ord        INTEGER NOT NULL,
    lock       INTEGER NOT NULL,   -- the jornada whose lock it was ranked at
    move       TEXT NOT NULL,
    reached    TEXT NOT NULL,      -- plan, left out (cleared, rejected before 2026-10-28)
    kind       TEXT NOT NULL,      -- get, sell
    chance     REAL,               -- chance it leaves you better off (ffcore.gain)
    jornadas   INTEGER,            -- jornadas played since
    pred       REAL,               -- points change forecast over them
    real       REAL,               -- points change that happened
    value_pred REAL,               -- value change forecast, millions
    value_real REAL,
    PRIMARY KEY (run_id, ord)
);

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
SELECT g.run_id, g.from_j, g.jornada, c.player, c.pts * c.p AS predicted, coalesce(a.pts, 0.0) AS actual,
       coalesce(f.flagged, 0) AS flagged, CASE WHEN m.code <> 'graded before 2026-10-17' THEN c.p END AS plays
FROM grade_moment g JOIN forecast_moment m ON m.moment = g.moment
JOIN forecast_cell c ON c.moment = g.moment AND c.jornada = g.jornada
LEFT JOIN forecast_player f ON f.moment = g.moment AND f.player = c.player
LEFT JOIN pts a ON a.player = c.player AND a.jornada = g.jornada;

DROP VIEW IF EXISTS grade_error;
CREATE VIEW grade_error AS
SELECT run_id, from_j, jornada, jornada - from_j AS ahead, count(*) AS n,
       sqrt(avg((predicted - actual) * (predicted - actual))) AS rmse,
       avg(predicted - actual) AS bias,
       (SELECT avg(actual) FROM (SELECT x.actual FROM grade_forecast x
                                 WHERE x.run_id = g.run_id AND x.from_j = g.from_j AND x.jornada = g.jornada
                                 ORDER BY x.predicted DESC, x.player LIMIT 50)) AS top50,
       sum(flagged) AS flagged_n,
       sqrt(avg(CASE WHEN flagged = 1 THEN (predicted - actual) * (predicted - actual) END))
           AS flagged_rmse,
       avg(CASE WHEN flagged = 1 THEN predicted - actual END) AS flagged_bias
FROM grade_forecast g GROUP BY run_id, from_j, jornada;

-- The moments to replay at: each line-up snapshot (one a time) belongs to
-- the jornada that was next then, from the previous lock to its own.
DROP VIEW IF EXISTS replay_moments;
CREATE VIEW replay_moments AS
WITH snaps AS MATERIALIZED (SELECT DISTINCT observed_at AS at FROM lineups
                            WHERE source = 'futbolfantasy' AND observed_at <= (SELECT cut())),
w AS (SELECT jornada, lock, coalesce(lag(lock) OVER (ORDER BY lock), '') AS opens FROM round_locks)
SELECT w.jornada, s.at AS as_of, w.lock FROM w JOIN snaps s ON s.at >= w.opens AND s.at < w.lock
ORDER BY w.lock, s.at;

-- The forecast of the next jornada at every replay moment, for each variant
-- as last forecast (its latest moment there; grading.replay makes them), a
-- row a player, with what he scored (0 if nothing) and how many hours
-- before the lock: how far from the lock the error is acceptable. The same
-- outcomes as grade_forecast, forecast again under the information of each
-- moment, so it says how early a forecast is good, not which model is
-- (paired_gain).
DROP VIEW IF EXISTS replayed;
CREATE VIEW replayed AS
WITH pts AS (SELECT player, jornada, sum(pts) AS pts FROM scored GROUP BY player, jornada)
SELECT m.variant, r.as_of, r.jornada, c.player, c.pts * c.p AS predicted,
       coalesce(a.pts, 0.0) AS actual, f.flagged,
       24 * (stamp_jd(r.lock)
           - stamp_jd(r.as_of))
           AS lead_h
FROM (SELECT variant, as_of, max(moment) AS moment FROM forecast_moment WHERE code = 'sql' GROUP BY variant, as_of) m
JOIN replay_moments r ON r.as_of = m.as_of
JOIN forecast_cell c ON c.moment = m.moment AND c.jornada = r.jornada
JOIN forecast_player f ON f.moment = m.moment AND f.player = c.player
LEFT JOIN pts a ON a.player = c.player AND a.jornada = r.jornada
WHERE r.jornada IN (SELECT jornada FROM jornadas_done);

-- How far from the lock a replayed forecast was made, in buckets.
DROP VIEW IF EXISTS lead_buckets;
CREATE VIEW lead_buckets AS
SELECT 0 AS from_h, 3 AS to_h, '0-3h' AS lead UNION ALL SELECT 3, 12, '3-12h'
UNION ALL SELECT 12, 24, '12-24h' UNION ALL SELECT 24, 48, '24-48h'
UNION ALL SELECT 48, 96, '48-96h' UNION ALL SELECT 96, 1e9, '96h+';

-- Replayed error against how far from the lock: per variant and lead
-- bucket, the outcomes, the moments, rmse and the flagged players' rmse.
-- Buckets hold different jornadas, so compare them with replay_gap.
DROP VIEW IF EXISTS replay_lead;
CREATE VIEW replay_lead AS
SELECT variant, b.from_h, b.lead,
       count(*) AS n, count(DISTINCT as_of) AS moments,
       sqrt(avg((predicted - actual) * (predicted - actual))) AS rmse,
       avg(predicted - actual) AS bias,
       sqrt(avg(CASE WHEN flagged = 1 THEN (predicted - actual) * (predicted - actual) END)) AS flagged_rmse
FROM replayed f JOIN lead_buckets b ON f.lead_h >= b.from_h AND f.lead_h < b.to_h
GROUP BY variant, b.from_h, b.lead;

-- What forecasting earlier costs: the same player-jornadas forecast at each
-- lead and at the jornada's last replayed moment; the mean squared error
-- earlier minus last (positive: earlier is worse) with its interval (claim).
DROP VIEW IF EXISTS replay_gap;
CREATE VIEW replay_gap AS
WITH fin AS (SELECT variant, jornada, max(as_of) AS at FROM replayed GROUP BY variant, jornada),
d AS (SELECT f.variant, f.lead_h, f.flagged,
             (f.predicted - f.actual) * (f.predicted - f.actual)
           - (g.predicted - g.actual) * (g.predicted - g.actual) AS d
      FROM replayed f JOIN fin ON fin.variant = f.variant AND fin.jornada = f.jornada
      JOIN replayed g ON g.variant = f.variant AND g.jornada = f.jornada
                            AND g.as_of = fin.at AND g.player = f.player
      WHERE f.as_of < fin.at),
s AS (SELECT d.variant, b.from_h, b.lead, sc.scope, count(*) AS n, avg(d.d) AS m,
             (sum(d.d * d.d) - count(*) * avg(d.d) * avg(d.d)) / (count(*) - 1) AS var
      FROM d JOIN lead_buckets b ON d.lead_h >= b.from_h AND d.lead_h < b.to_h
      JOIN (SELECT 'all' AS scope UNION ALL SELECT 'flagged') sc ON sc.scope = 'all' OR d.flagged = 1
      GROUP BY d.variant, b.from_h, b.lead, sc.scope),
t AS (SELECT *, sqrt(max(var, 0) / n) AS se FROM s)
SELECT variant, from_h, lead, scope, n, m AS extra_mse, se,
       lo95(m, se) AS lo, hi95(m, se) AS hi, claim(m, se) AS claim
FROM t;


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

-- The players on the game's market who could not play a jornada's match
-- for whoever buys them: his auction closes after his club's kickoff in it
-- (of a jornada under way or to come). Nobody holds them, so for everyone
-- they play none of it.
DROP VIEW IF EXISTS late_arrivals;
CREATE VIEW late_arrivals AS
WITH listed AS (SELECT player, max(julianday(expires_at)) AS close FROM current_api_market
                WHERE seller = 'marketPlayerLeague' AND player IS NOT NULL AND sale_price <> 0
                  AND coalesce(expires_at, '') <> ''
                GROUP BY player)
SELECT DISTINCT l.player, k.jornada
FROM listed l JOIN player_club m ON m.player = l.player AND m.club IS NOT NULL
JOIN kickoffs k ON m.club IN (k.home, k.away)
JOIN jornada_calendar c ON c.jornada = k.jornada AND c.state IN ('under way', 'ahead')
WHERE k.t < l.close;

-- Each moment's forecast (forecast_cell) as a decision made at t uses
-- it: points if he plays, and the chance he plays, none in a jornada he
-- would join after his club's match (late_arrivals). The universe reads
-- its cells here, and the model the moment it looks at (cell).
-- Each run's forecast, every cell with factors (ffcore.schedule.Factors):
-- points = ppm x fixture, plays = picked x fit, expected from the two.
DROP VIEW IF EXISTS run_forecast;
CREATE VIEW run_forecast AS
SELECT r.run_id, c.jornada, c.player, c.ppm, c.fixture, c.picked, c.fit,
       max(0.0, c.ppm * c.fixture) AS points, c.picked * c.fit AS plays,
       max(0.0, c.ppm * c.fixture) * c.picked * c.fit AS expected
FROM sim_runs r JOIN forecast_cell c ON c.moment = r.moment WHERE c.ppm IS NOT NULL;

DROP VIEW IF EXISTS forecast_used;
CREATE VIEW forecast_used AS
SELECT c.moment, c.player, c.jornada, c.pts, CASE WHEN l.player IS NULL THEN c.p ELSE 0.0 END AS p
FROM forecast_cell c LEFT JOIN late_arrivals l ON l.player = c.player AND l.jornada = c.jornada;

-- How a jornada's points spread about a cell's: m2, the mean square of a
-- single game's points over its mean squared (the season's games scored
-- so far, or the seed pool before MIN_POOL of them), and the share of a
-- player's form that persists from one jornada to the next (share).
DROP VIEW IF EXISTS score_spread;
CREATE VIEW score_spread AS
WITH c AS (SELECT (SELECT value FROM model_constants WHERE name = 'MIN_POOL' AND variant = variant()) AS min_pool,
                  (SELECT value FROM model_constants WHERE name = 'PERSISTENT_SHARE' AND variant = variant()) AS share),
played AS MATERIALIZED (SELECT pts AS x FROM scored WHERE games = 1 AND pts IS NOT NULL),
pool AS (SELECT x FROM played WHERE (SELECT count(*) FROM played) >= (SELECT min_pool FROM c)
         UNION ALL
         SELECT x FROM (/* forecast.SEED_POOL */) WHERE (SELECT count(*) FROM played) < (SELECT min_pool FROM c)),
m AS (SELECT avg(x * x) AS sq, avg(x) AS mean FROM pool)
SELECT m.sq / (CASE WHEN abs(m.mean) > 1e-9 THEN m.mean * m.mean ELSE 1.0 END) AS m2, c.share FROM m, c;

-- Each player's forecast for each jornada left, as the model looks (look's
-- moment: its cells are the jornadas left at its cut): the chance he plays
-- (p), his expected points (e) and their variance (v: p pts^2 m2 - e^2, the game drawn from the pool
-- scaled to his points if he plays).
DROP VIEW IF EXISTS cell;
CREATE VIEW cell AS
SELECT f.jornada, f.player, f.p, f.pts * f.p AS e, f.p * f.pts * f.pts * w.m2 - (f.pts * f.p) * (f.pts * f.p) AS v
FROM look l JOIN forecast_used f ON f.moment = l.moment CROSS JOIN score_spread w;

-- Each position's stand-in each jornada left: the average of the points if
-- he plays and of the chance he plays of the players of that position in a
-- squad or on the market, as seen; his e and v as a cell's.
DROP VIEW IF EXISTS standin_cell;
CREATE VIEW standin_cell AS
WITH a AS (SELECT f.jornada, s.pos, avg(f.pts) AS pts, avg(f.p) AS p
           FROM look l JOIN forecast_used f ON f.moment = l.moment
           JOIN seen s ON s.cut = l.cut AND s.variant = l.variant AND s.player = f.player
           WHERE s.pos IS NOT NULL AND (s.price IS NOT NULL OR s.owner IS NOT NULL)
           GROUP BY f.jornada, s.pos)
SELECT a.jornada, a.pos, a.pts * a.p AS e, a.p * a.pts * a.pts * w.m2 - (a.pts * a.p) * (a.pts * a.p) AS v
FROM a CROSS JOIN score_spread w;

-- The game's nightly offers still to come before the lock (next_lock): one
-- as each standing offer expires, then one a day (0 with none standing),
-- and when the first of them comes (first, a julian day).
DROP VIEW IF EXISTS nights_left;
CREATE VIEW nights_left AS
WITH e AS (SELECT max(julianday(expires_at)) AS ends FROM current_api_offers
           WHERE status = 'pending' AND expires_at <> ''),
l AS (SELECT t AS lock FROM next_lock)
SELECT CASE WHEN ends IS NOT NULL AND lock > ends THEN CAST(lock - ends AS INTEGER) + 1 ELSE 0 END AS nights,
       ends AS first
FROM e, l;

-- When each manager's listing on the game's market ends, as of t (t, a
-- julian day): a listing runs three days, and the game offers for a player
-- at each close only while he is listed.
DROP VIEW IF EXISTS listing_ends;
CREATE VIEW listing_ends AS
SELECT player, max(expires_at) AS ends, max(julianday(expires_at)) AS t
FROM current_api_market
WHERE player IS NOT NULL AND seller = 'marketPlayerTeam' AND expires_at <> ''
GROUP BY player;

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
-- owner paid, his price trend. What Market is built from (assemble) and
-- the board shows.
DROP VIEW IF EXISTS player_facts;
CREATE VIEW player_facts AS
WITH who AS (
    SELECT ff_id AS player, trim(name) AS name, position, value FROM current_market
    WHERE ff_id <> '' AND lower(coalesce(position, '')) <> 'entrenador'
    UNION ALL
    SELECT player, trim(player_name), '', NULL FROM current_lineups
    WHERE source = 'futbolfantasy' AND player IS NOT NULL
      AND player NOT IN (SELECT ff_id FROM current_market)),
sale AS (SELECT player, max(sale_price, coalesce(market_value, 0)) AS price,
                CASE seller WHEN 'marketPlayerTeam' THEN 'listed' ELSE 'free' END AS route
         FROM current_api_market WHERE player IS NOT NULL AND sale_price <> 0),
bid AS (SELECT player, max(bid_money) AS my_bid FROM current_api_market
        WHERE player IS NOT NULL AND bid_status = 'pending' AND bid_money <> 0
        GROUP BY player),
offer AS (SELECT t.player, max(o.money) AS offer FROM current_api_offers o
          JOIN current_api_teams t ON t.player_team_id = o.player_team_id
          WHERE o.player_team_id <> '' AND o.status = 'pending' AND o.money <> 0
            AND t.player IS NOT NULL
          GROUP BY t.player),
clause AS (SELECT player, buyout AS clause FROM current_api_teams
           WHERE player IS NOT NULL AND buyout <> 0)
SELECT w.player, coalesce(nullif(w.name, ''), w.player) AS name,
       position(upper(w.position)) AS pos, nullif(w.value, 0) AS value,
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
SELECT *, rowid AS id FROM market
WHERE observed_at IN (SELECT max(observed_at) FROM market WHERE observed_at <= (SELECT cut())
                      GROUP BY substr(observed_at, 1, 10));

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

-- The constants a run is built with (assemble.constants), for the views,
-- per variant: live, or a challenger's (variants) while it is replayed.
CREATE TABLE IF NOT EXISTS model_constants (
    variant TEXT NOT NULL DEFAULT 'live',
    name    TEXT NOT NULL,
    value   REAL NOT NULL,
    PRIMARY KEY (variant, name)
);

-- A challenger: the constants it sets differently from live's.
CREATE TABLE IF NOT EXISTS variants (
    variant TEXT NOT NULL,
    name    TEXT NOT NULL,
    value   REAL NOT NULL,
    PRIMARY KEY (variant, name)
);

-- The constants the views use, one row: each named once, here.
DROP VIEW IF EXISTS k;
CREATE VIEW k AS SELECT
    (SELECT value FROM model_constants WHERE name = 'CLIP' AND variant = (SELECT variant())) AS clip,
    (SELECT value FROM model_constants WHERE name = 'PRICE_WINDOW' AND variant = (SELECT variant())) AS price_window,
    (SELECT value FROM model_constants WHERE name = 'BID_NEIGHBOURS' AND variant = (SELECT variant())) AS bid_neighbours,
    (SELECT value FROM model_constants WHERE name = 'PRICE_W0' AND variant = (SELECT variant())) AS price_w0,
    (SELECT value FROM model_constants WHERE name = 'PRICE_W1' AND variant = (SELECT variant())) AS price_w1,
    (SELECT value FROM model_constants WHERE name = 'PRICE_W2' AND variant = (SELECT variant())) AS price_w2,
    (SELECT value FROM model_constants WHERE name = 'MATCH_LEN' AND variant = (SELECT variant())) AS match_len,
    (SELECT value FROM model_constants WHERE name = 'NEUTRAL_START' AND variant = (SELECT variant())) AS neutral_start,
    (SELECT value FROM model_constants WHERE name = 'ABSENT_START' AND variant = (SELECT variant())) AS absent_start,
    (SELECT value FROM model_constants WHERE name = 'SHRINK_K' AND variant = (SELECT variant())) AS shrink_k,
    (SELECT value FROM model_constants WHERE name = 'FIXTURE_K' AND variant = (SELECT variant())) AS fixture_k,
    (SELECT value FROM model_constants WHERE name = 'PICK_K' AND variant = (SELECT variant())) AS pick_k,
    (SELECT value FROM model_constants WHERE name = 'REMIND_HOURS' AND variant = (SELECT variant())) AS remind_hours,
    -- what price_series and momentum_pairs are made with: the key of momentum_day's rows
    (SELECT printf('%.9g|%.9g|%.9g|%.9g', c.value, w0.value, w1.value, w2.value)
     FROM model_constants c, model_constants w0, model_constants w1, model_constants w2
     WHERE c.name = 'CLIP' AND w0.name = 'PRICE_W0' AND w1.name = 'PRICE_W1' AND w2.name = 'PRICE_W2'
       AND c.variant = w0.variant AND c.variant = w1.variant AND c.variant = w2.variant
       AND c.variant = (SELECT variant())) AS ckey;

-- Every valued day of every player as of t: the closed days kept in
-- price_step, and the day of the cut (the last snapshot of it up to the cut)
-- worked out against the day before.
-- The last day with a market snapshot as of t: the one day price_step
-- does not hold for it (the days before are closed).
DROP VIEW IF EXISTS price_day_now;
CREATE VIEW price_day_now AS
SELECT coalesce(substr(max(observed_at), 1, 10), '') AS d FROM market WHERE observed_at <= (SELECT cut());

DROP VIEW IF EXISTS price_rows;
CREATE VIEW price_rows AS
WITH cutday AS (SELECT d FROM price_day_now),
latest AS (SELECT ff_id, max(n) AS n, max(CASE WHEN step IS NOT NULL THEN n END) AS ln
           FROM price_step WHERE day < (SELECT d FROM cutday) GROUP BY ff_id),
seed AS (SELECT a.ff_id, a.n, a.value, l.i AS i0, l.step AS last_step, l.s1 AS last_s1
         FROM latest z JOIN price_step a ON a.ff_id = z.ff_id AND a.n = z.n
         LEFT JOIN price_step l ON l.ff_id = z.ff_id AND l.n = z.ln),
live AS (SELECT m.ff_id, m.observed_at, m.value, s.n AS n0, s.value AS pv, s.i0, s.last_step, s.last_s1
         FROM market m LEFT JOIN seed s ON s.ff_id = m.ff_id
         WHERE m.observed_at = (SELECT max(observed_at) FROM market
                                WHERE observed_at >= (SELECT d FROM cutday) AND observed_at <= (SELECT cut()))
           AND coalesce(m.ff_id, '') <> '' AND m.value IS NOT NULL)
SELECT ff_id, n, day, observed_at, value, step, i, s1, s2 FROM price_step WHERE day < (SELECT d FROM cutday)
UNION ALL
SELECT ff_id, coalesce(n0 + 1, 0), substr(observed_at, 1, 10), observed_at, value,
       CASE WHEN pv > 0 THEN 100 * (value / pv - 1) END,
       CASE WHEN pv > 0 THEN coalesce(i0, -1) + 1 END,
       CASE WHEN pv > 0 THEN last_step END, CASE WHEN pv > 0 THEN last_s1 END
FROM live;

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
WITH lens AS (SELECT count(step) AS n FROM price_rows GROUP BY ff_id),
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
WITH s AS MATERIALIZED (SELECT * FROM price_series)
SELECT a.ff_id, a.i, a.day, a.x, hs.h, b.observed_at AS end_at,
       substr(b.observed_at, 1, 10) AS end_day, 100 * (b.value / a.value - 1) AS change
FROM s b JOIN horizons hs ON hs.h <= (SELECT hmax FROM price_hmax) + hpad()
JOIN s a ON a.ff_id = b.ff_id AND a.i = b.i - hs.h;

-- Momentum, as of t: how much of a price change carries into the next
-- h (up to price_hmax), the move over h clipped at CLIP an update.
DROP VIEW IF EXISTS momentum_pairs;
CREATE VIEW momentum_pairs AS
SELECT p.h, p.ff_id, p.i, p.end_day, p.x,
       max(-k.clip * p.h, min(k.clip * p.h, p.change)) AS y
FROM price_ahead p, k WHERE p.end_at <= (SELECT cut());

-- What each closed day adds to the momentum fit, per horizon: the sums of x*y
-- and x*x of the pairs that end that day, written once (facts.MOMENTUM_DAY)
-- for the constants they were made with (ckey).
CREATE TABLE IF NOT EXISTS momentum_day (
    ckey    TEXT NOT NULL,
    h       INTEGER NOT NULL,
    end_day TEXT NOT NULL,
    sxy     REAL NOT NULL,
    sxx     REAL NOT NULL,
    PRIMARY KEY (ckey, h, end_day)
) WITHOUT ROWID;

-- The momentum fit's sums per horizon, as of t: the closed days kept in
-- momentum_day, and the pairs ending on the day of the cut.
DROP VIEW IF EXISTS momentum_sums;
CREATE VIEW momentum_sums AS
WITH parts AS (
    SELECT h, sxy, sxx FROM momentum_day
    WHERE ckey = (SELECT ckey FROM k) AND end_day < (SELECT d FROM price_day_now)
      AND h <= (SELECT hmax FROM price_hmax)
    UNION ALL
    SELECT h, sum(x * y), sum(x * x) FROM momentum_pairs
    WHERE end_day = (SELECT d FROM price_day_now) GROUP BY h)
SELECT h, sum(sxy) AS sxy, sum(sxx) AS sxx FROM parts GROUP BY h;

-- One least-squares slope through the origin per horizon.
DROP VIEW IF EXISTS momentum;
CREATE VIEW momentum AS
SELECT h, sxy / sxx AS slope
FROM momentum_sums WHERE sxx <> 0;

-- Momentum as fitted at a cut, kept: fitting costs seconds, and carry and
-- trend read it (assemble.price_momentum fits at the model's cut; Datasette
-- reads the last fit at or before its own).
CREATE TABLE IF NOT EXISTS momentum_fit (
    as_of TEXT NOT NULL,
    h     INTEGER NOT NULL,
    slope REAL NOT NULL,
    PRIMARY KEY (as_of, h)
);
DROP VIEW IF EXISTS fitted;
CREATE VIEW fitted AS
SELECT h, slope FROM momentum_fit
WHERE as_of = (SELECT max(as_of) FROM momentum_fit WHERE as_of <= (SELECT cut()));

-- The share of a trend (its move over the horizon, the longest h fitted)
-- already made after each update.
DROP VIEW IF EXISTS price_carry;
CREATE VIEW price_carry AS
SELECT m.h, m.slope / e.slope AS carry
FROM fitted m, (SELECT slope FROM fitted ORDER BY h DESC LIMIT 1) e
WHERE e.slope <> 0 ORDER BY m.h;

-- Each player's value change in % over the horizon, for those whose last
-- change is the newest day's: as long as you would hold him. Working it out
-- walks every player's whole price history (~1s), and four things read it a
-- run, so a run works it out once and keeps it (assemble.record_trend); what
-- reads it gets the last one kept at or before t, as momentum_fit does.
DROP VIEW IF EXISTS price_trend_now;
CREATE VIEW price_trend_now AS
WITH s AS (SELECT coalesce((SELECT slope FROM fitted ORDER BY h DESC LIMIT 1), 0.0) AS slope),
last AS (SELECT ff_id, day, x FROM (
             SELECT ff_id, day, x, row_number() OVER (PARTITION BY ff_id ORDER BY i DESC) AS r
             FROM price_series WHERE observed_at <= (SELECT cut()))
         WHERE r = 1)
SELECT last.ff_id, s.slope * last.x AS trend
FROM last, s WHERE last.day = (SELECT max(day) FROM last);

CREATE TABLE IF NOT EXISTS price_trend_fit (
    as_of TEXT NOT NULL,
    ff_id TEXT NOT NULL,
    trend REAL NOT NULL,
    PRIMARY KEY (as_of, ff_id)
) WITHOUT ROWID;
DROP VIEW IF EXISTS price_trend;
CREATE VIEW price_trend AS
SELECT ff_id, trend FROM price_trend_fit
WHERE as_of = (SELECT max(as_of) FROM price_trend_fit WHERE as_of <= (SELECT cut()));

-- How each listing's auction went, once it has closed (by t): every
-- listing on the market (the game's or a manager's) by player and close
-- (closed, as a stamp), its asking price at its last sighting and his
-- price trend then (the last kept at or before its close), and the buy
-- made within ten minutes of its close if one was: what the winner paid
-- (won; NULL: nobody bought) and whether it was you. bar is what a bid had
-- to reach, over asking: the winner's bid; asking where nobody bought or
-- the winner was you (the bids you beat are not seen); NULL for a
-- manager's listing (no one's deal with him is seen). The one auction
-- outcome: what bids win now (bid_odds) and a path's bids (my_bids) both
-- read it, and no bar before its close. An outcome is the close's, however
-- late the feed tells of it.
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
             WHERE m.seller IN ('marketPlayerLeague', 'marketPlayerTeam')
               AND coalesce(m.expires_at, '') <> '' AND coalesce(m.sale_price, 0) > 0)
         WHERE pick = 1),
buys AS (SELECT player_id AS pid, julianday(at) AS t, amount, user_id IN (SELECT uid FROM mine) AS me
         FROM (SELECT player_id, at, amount, user_id,
                      row_number() OVER (PARTITION BY activity_id ORDER BY rowid DESC) AS last
               FROM api_activity WHERE kind = 'buy' AND activity_id <> '')
         WHERE last = 1),
won AS (SELECT * FROM (
            SELECT e.*, b.amount AS won, b.me,
                   row_number() OVER (PARTITION BY e.pid, e.close ORDER BY b.amount DESC) AS top
            FROM ends e LEFT JOIN buys b ON b.pid = e.pid AND abs(b.t - e.close) * 86400 < 600)
        WHERE top = 1),
fits AS (SELECT DISTINCT as_of FROM price_trend_fit)
SELECT w.player, w.at AS close, strftime('%Y-%m-%dT%H%MZ', w.at) AS closed, w.asking, w.seller, w.won, w.me AS by_me,
       CASE WHEN w.seller <> 'marketPlayerLeague' THEN NULL
            WHEN w.won IS NULL OR w.me THEN 1.0 ELSE w.won / w.asking END AS bar,
       (SELECT t.trend FROM price_trend_fit t
        WHERE t.ff_id = w.player
          AND t.as_of = (SELECT max(as_of) FROM fits WHERE as_of <= strftime('%Y-%m-%dT%H%MZ', w.at)))
       AS trend
FROM won w WHERE strftime('%Y-%m-%dT%H%MZ', w.at) <= (SELECT cut());

-- What a bid wins on the game's market now, for each player listed there:
-- his BID_NEIGHBOURS past listings closest to him in price trend (before
-- t, none you won: what you beat is not seen), and for each bid over
-- asking that reached one of their bars (and asking itself) the share it
-- would have won (p_win; 1 with no past to go on).
DROP VIEW IF EXISTS bid_odds;
CREATE VIEW bid_odds AS
WITH past AS MATERIALIZED (
    SELECT trend, bar FROM auction_results
    WHERE bar IS NOT NULL AND trend IS NOT NULL AND NOT coalesce(by_me, 0)),
now AS (SELECT DISTINCT s.player, coalesce(t.trend, 0.0) AS trend
        FROM current_api_market s LEFT JOIN price_trend t ON t.ff_id = s.player
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
-- no constants set): what an offer to come is drawn from.
DROP VIEW IF EXISTS recent_offers;
CREATE VIEW recent_offers AS
SELECT ratio FROM offer_ratios ORDER BY created_at DESC, ratio DESC
LIMIT coalesce((SELECT CAST(price_window AS INTEGER) FROM k), -1);

-- The jornada calendar, as of t. Each fixture's kickoff as last seen
-- (a played match's page drops it) and its jornada, as a UTC stamp (as
-- observed_at is), and as a julian day (t).
DROP VIEW IF EXISTS kickoffs;
CREATE VIEW kickoffs AS
SELECT home, away, jornada, kick, t FROM (
    SELECT home, away, jornada, strftime('%Y-%m-%dT%H%MZ', kickoff) AS kick, julianday(kickoff) AS t,
           row_number() OVER (PARTITION BY home, away ORDER BY observed_at DESC, rowid DESC) AS n
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
-- is not known); not the first fetch's, which is the season so far. id
-- is the points row's: the order they were stored.
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
           (SELECT g.jornada FROM g WHERE g.club = coalesce(c.club, '*') AND g.at <= p.at
            ORDER BY g.at DESC, g.jornada DESC LIMIT 1) AS jornada
    FROM p, s LEFT JOIN c ON c.ff_id = p.ff_id
    WHERE p.at > s.first AND NOT (p.points IS p.points0 AND p.games IS p.games0))
SELECT player, jornada, pts, games, at, id FROM credited WHERE jornada IS NOT NULL;

-- Who was in each matchday squad (starters' starters and subs) and his
-- minutes: a starter the whole match unless he came off, a sub none
-- unless he came on.
DROP VIEW IF EXISTS squad_minutes;
CREATE VIEW squad_minutes AS
SELECT s.match_id, s.team_slug, s.player_slug, s.player, mj.jornada,
       max(0.0, CASE s.role WHEN 'starter' THEN coalesce(s.minute, k.match_len)
                            ELSE coalesce(k.match_len - s.minute, 0.0) END) AS mins
FROM current_starters s JOIN match_jornada mj USING (match_id), k
WHERE s.role IN ('starter', 'sub') AND s.player_slug <> '';

-- Each scoring player's jornadas: his points (scored) and minutes.
DROP VIEW IF EXISTS player_jornadas;
CREATE VIEW player_jornadas AS
WITH s AS MATERIALIZED (SELECT player, jornada, pts FROM scored)
SELECT player, jornada, sum(pts) AS pts, sum(mins) AS mins FROM (
    SELECT player, jornada, pts, 0.0 AS mins FROM s
    UNION ALL
    SELECT player, jornada, 0.0, mins FROM squad_minutes
    WHERE player IN (SELECT player FROM s))
GROUP BY player, jornada;

-- His season so far: points, appearances, and matchday squads (jornadas
-- he scored or was in the squad); and pick, the chance he is picked if fit
-- after his next match: that record (appearances over matchday squads)
-- pulled toward NEUTRAL_START, which is worth PICK_K matches. In one view,
-- so the forecast derives the season once a moment.
DROP VIEW IF EXISTS season_record;
CREATE VIEW season_record AS
WITH r AS (SELECT player, sum(pts) AS pts, sum(mins > 0) * 1.0 AS apps, count(*) * 1.0 AS squads
           FROM player_jornadas GROUP BY player)
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
                     row_number() OVER (PARTITION BY ff_id ORDER BY observed_at DESC, rowid DESC) AS n
              FROM market WHERE ff_id <> '')
SELECT l.ff_id AS player_id, l.name, l.club AS club_id, coalesce(a.app_id, '') AS app_id
FROM last l LEFT JOIN app_links a ON a.player = l.ff_id WHERE l.n = 1;

-- The jornadas every match of which has a score (jornada_calendar): what a
-- forecast is graded on (one under way, or with a match postponed, is not).
DROP VIEW IF EXISTS jornadas_done;
CREATE VIEW jornadas_done AS
SELECT jornada FROM jornada_calendar WHERE state = 'done' ORDER BY jornada;

-- Last season's points per player (ingest.baseline writes it; an empty
-- table is restored from the archived page, raw/season=<season>.tar.xz),
-- and the latest season's.
CREATE TABLE IF NOT EXISTS season_points (
    season TEXT NOT NULL,
    ff_id  TEXT NOT NULL,
    points REAL NOT NULL,
    games  REAL NOT NULL,
    PRIMARY KEY (season, ff_id)
);
DROP VIEW IF EXISTS last_season;
CREATE VIEW last_season AS
SELECT ff_id, points AS pts, games AS pj FROM season_points
WHERE season = (SELECT max(season) FROM season_points);

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

-- The forecast, as SQL over those.

-- Each player's rates: ppm, his points per match, what his value says
-- (ppm_prior) pulled toward last season's points per match, then toward
-- this season's, each by SHRINK_K matches; and picked, the chance he is
-- picked if fit, his listing's start % (NEUTRAL_START if listed with none
-- or flagged, whose listing speaks of the injury; ABSENT_START if not
-- listed) pulled toward his appearances over his matchday squads by PICK_K.
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
       shrink(CASE WHEN s.status IS NOT NULL OR (s.listed AND s.pct IS NULL) THEN k.neutral_start
                   WHEN s.listed THEN s.pct ELSE k.absent_start END / 100.0,
              k.pick_k, coalesce(r.apps, 0.0), coalesce(r.squads, 0.0)) AS picked
FROM c CROSS JOIN k LEFT JOIN p USING (player) LEFT JOIN last_season l ON l.ff_id = c.player
LEFT JOIN r USING (player) LEFT JOIN s USING (player);

-- The chance he is fit each jornada his club has left: 1 unless flagged;
-- if flagged, his prognosis (prognosis()) read literally against the
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
x AS (SELECT c.player, f.jornada, rt.ppm, rt.picked, ft.fit,
             coalesce(CASE WHEN c.pos IN ('POR', 'DEF') THEN 1.0 / o.attack ELSE o.defence END, 1.0) AS fixture
      FROM c JOIN f ON f.club = c.club JOIN rt USING (player)
      JOIN ft ON ft.player = c.player AND ft.jornada = f.jornada
      LEFT JOIN o ON o.club = f.opp)
SELECT player, jornada, max(0.0, ppm * fixture) AS pts, picked * fit AS p, ppm, fixture, picked, fit FROM x;


-- The board: the last run's decisions and the facts it saw (sim_*,
-- decision_option, decision_plan), the app's line-up and the next kickoff
-- as of t.
-- board_json is decisions.json, the phone's copy; sim.main writes it from
-- here. Names, not keys: the board is what the phone shows.
DROP VIEW IF EXISTS board_run;
CREATE VIEW board_run AS SELECT max(run_id) AS run_id FROM sim_runs;

-- Each player the last run decided about, with his facts and name as shown;
-- if yours, what selling him raises and whether that takes tonight's offer.
DROP VIEW IF EXISTS board_players;
CREATE VIEW board_players AS
SELECT p.*, coalesce(f.name, p.player) AS name, coalesce(f.pos, '') AS pos,
       f.value, f.trend, f.paid, f.offer, f.clause, f.price, f.route, f.my_bid,
       CASE WHEN p.mine THEN coalesce(f.fetches, 0.0) END AS raises,
       coalesce(f.fetches = f.offer, 0) AS takes
FROM sim_players p JOIN board_run USING (run_id) LEFT JOIN sim_facts f USING (run_id, player);

-- Every buy the last run valued (decision_option: each player on the
-- market you could get), with what the run saw of him: what he is listed at
-- (asking), the bid (the plan's, or the lowest his odds offer) and its
-- chance of winning (win), your bid if it is in, what you pay (cost), his
-- value and trend, his points alone, and whether the plan buys him.
DROP VIEW IF EXISTS board_buys;
CREATE VIEW board_buys AS
SELECT o.player, coalesce(s.name, o.player) AS name, coalesce(s.pos, '') AS pos,
       coalesce(s.price, o.amount) AS asking, o.amount AS bid, s.my_bid, o.amount AS cost,
       s.value, s.trend, o.gain, o.worth, o.in_plan, o.win
FROM decision_option o JOIN board_run USING (run_id)
LEFT JOIN sim_facts s ON s.run_id = o.run_id AND s.player = o.player
WHERE o.kind = 'buy';

-- Each of your players: his facts (board_players), whether he is listed,
-- the game's offer standing on him and the share of its recent offers
-- (recent_offers), as a share of value, it beats; what selling him alone
-- does (decision_option: points, worth), whether the plan sells
-- him, and what turning the offer down is worth (waiting: the best of the
-- game's offers still to come before the lock, sim_waiting).
-- relist_after: when his listing ends, if before the last of those offers.
DROP VIEW IF EXISTS board_mine;
CREATE VIEW board_mine AS
WITH r AS (SELECT ratio FROM recent_offers),
n AS (SELECT nights, first FROM nights_left)
SELECT b.player, b.name, b.pos, b.value, b.paid, b.trend, b.offer, b.raises, b.takes,
       b.route IS 'listed' AS listed,
       CASE WHEN b.offer IS NOT NULL AND b.value > 0
            THEN (SELECT avg(r.ratio < b.offer / b.value) FROM r) END AS beats,
       f.gain, f.worth, coalesce(f.in_plan, 0) AS sell,
       w.later AS waiting,
       CASE WHEN b.route IS 'listed' AND l.t < n.first + n.nights - 1 THEN l.ends END AS relist_after,
       n.nights
FROM board_players b JOIN board_run USING (run_id) CROSS JOIN n
LEFT JOIN decision_option f ON f.run_id = b.run_id AND f.kind = 'sell' AND f.player = b.player
LEFT JOIN sim_waiting w ON w.run_id = b.run_id AND w.player = b.player AND w.kind = 'sale'
LEFT JOIN listing_ends l ON l.player = b.player
WHERE b.mine;

-- Each buy (board_buys) and each of your players (board_mine) with what the
-- board says of him, for the plan's
-- line on him and his row in the rankings alike: a buy's asking price,
-- your bid, how many bid, the chance to win, his points and trend; a
-- sale's value, the offer standing (when not taken), how it ranks among
-- the game's recent offers, what waiting is worth and how many offers come
-- before the lock, his points and trend.
DROP VIEW IF EXISTS board_buy_facts;
CREATE VIEW board_buy_facts AS
SELECT b.*,
       (SELECT json_group_array(json_array(l, v, u) ORDER BY n) FROM (
                SELECT 0 AS n, 'listed at' AS l, b.asking AS v, 'money' AS u
                UNION ALL SELECT 0.4, 'your bid', b.my_bid, 'money' WHERE b.my_bid <> b.cost
                UNION ALL SELECT 0.45, 'bids', CASE WHEN n.bids = 0 THEN 'none yet'
                                                   WHEN b.my_bid IS NULL THEN CAST(n.bids AS TEXT)
                                                   WHEN n.bids = 1 THEN 'only yours'
                                                   ELSE n.bids || ', yours among them' END, 'count'
                    FROM (SELECT CAST(max(m.bids) AS INTEGER) AS bids FROM current_api_market m
                          WHERE m.player = b.player AND m.bids <> '') n WHERE n.bids IS NOT NULL
                UNION ALL SELECT 0.5, 'chance to win', round(b.win, 3), 'chance'
                UNION ALL SELECT 3, 'points', b.gain, 'points'
                UNION ALL SELECT 4, '', b.trend, 'trend')
            WHERE v IS NOT NULL) AS facts
FROM board_buys b;

DROP VIEW IF EXISTS board_sell_facts;
CREATE VIEW board_sell_facts AS
SELECT s.*,
       (SELECT json_group_array(json_array(l, v, u) ORDER BY n) FROM (
                SELECT 0 AS n, 'value' AS l, s.value AS v, 'money' AS u
                UNION ALL SELECT 1, 'offer', CASE WHEN NOT s.takes THEN s.offer END, 'money'
                UNION ALL SELECT 2, 'the game''s recent offers', round(s.beats, 3), 'beats'
                UNION ALL SELECT 2.2, 'waiting is worth', s.waiting, 'money'
                    WHERE s.offer IS NOT NULL AND s.nights > 0
                UNION ALL SELECT 2.4, 'more offers before the lock', s.nights, 'count'
                    WHERE s.offer IS NOT NULL
                UNION ALL SELECT 3, 'points', s.gain, 'points'
                UNION ALL SELECT 4, '', s.trend, 'trend')
            WHERE v IS NOT NULL) AS facts
FROM board_mine s;

-- Your players a rival can take now by paying their clause (any can: a sale
-- to the game pays at once, so cash is no limit), most valuable first.
DROP VIEW IF EXISTS board_clauses;
CREATE VIEW board_clauses AS
SELECT row_number() OVER (ORDER BY -season, player) - 1 AS ord, name, pos, clause
FROM board_players WHERE mine AND clause IS NOT NULL;

-- Every manager's season if you do nothing, most points first; a rival's
-- cash is estimated from the feed.
DROP VIEW IF EXISTS board_league;
CREATE VIEW board_league AS
SELECT row_number() OVER (ORDER BY -s.mean, s.manager) - 1 AS ord, s.manager, s.me, s.now,
       s.mean, s.lo, s.hi, coalesce(s.cash, 0.0) AS cash, NOT s.me AS estimated, s.p_above,
       s.finish, s.p_first
FROM sim_standings s JOIN board_run USING (run_id);

-- The eleven to field at the lock: the best of your squad now, each with his
-- place in your team, as the app lines him up. The line-up reminder and the
-- board's field line both read it.
DROP VIEW IF EXISTS board_eleven;
CREATE VIEW board_eleven AS
SELECT p.player, p.name, p.pos, p.next FROM board_players p WHERE p.xi;

-- Your line-up in the app against the best eleven of your squad now
-- (sim_players.xi): a line-up is of players you hold, so a buy is in it only
-- once he is yours, at the run after his auction closes. The app's line-up
-- is its players, each by its app id else by the name one player has
-- (identity.sql), and counts only if every one is yours and there are as many as the eleven;
-- otherwise it is unknown (legal 0) and the whole eleven is to set. 'in'
-- are the eleven's players not in it, 'out' (i, its order in the app) those
-- in it that are not in the eleven.
DROP VIEW IF EXISTS lineup_gap;
CREATE VIEW lineup_gap AS
WITH t AS MATERIALIZED (
    SELECT rowid AS i, player_id, name_key(player_name) AS short, name_key(player_name_full) AS full
    FROM api_lineup
    WHERE (source, observed_at) IN (SELECT source, max(observed_at) FROM api_lineup
                                    WHERE observed_at <= (SELECT cut()) GROUP BY source)),
bp AS MATERIALIZED (SELECT * FROM board_players),
app AS MATERIALIZED (
    SELECT t.i, coalesce(a.player, n.player, f.player) AS player
    FROM t LEFT JOIN app_player a ON a.app_id = t.player_id
    LEFT JOIN name_player n ON n.name_key = t.short
    LEFT JOIN name_player f ON f.name_key = t.full),
fielded AS (SELECT i, player FROM app
            WHERE NOT EXISTS (SELECT 1 FROM app a LEFT JOIN bp b
                              ON b.player = a.player AND b.mine WHERE b.player IS NULL)),
legal AS (SELECT (SELECT count(*) FROM fielded) > 0
                 AND (SELECT count(*) FROM fielded) = (SELECT count(*) FROM bp WHERE xi) AS ok)
SELECT 'in' AS side, NULL AS i, e.player, e.name, e.next, l.ok AS legal
FROM bp e, legal l
WHERE e.xi AND (NOT l.ok OR e.player NOT IN (SELECT player FROM fielded))
UNION ALL
SELECT 'out', f.i, b.player, b.name, b.next, l.ok
FROM fielded f JOIN bp b USING (player), legal l
WHERE l.ok AND f.player NOT IN (SELECT player FROM bp WHERE xi);

-- The players of each side of lineup_gap as named in one line: those to
-- field, the best first, and those to take out, as the app has them.
DROP VIEW IF EXISTS lineup_names;
CREATE VIEW lineup_names AS
WITH gap AS MATERIALIZED (SELECT * FROM lineup_gap)
SELECT 'in' AS side, group_concat(name, ', ' ORDER BY -next, player) AS names FROM gap WHERE side = 'in'
UNION ALL
SELECT 'out', group_concat(name, ', ' ORDER BY i) FROM gap WHERE side = 'out';

-- The plan's moves, in its order (a swap's buy, then its sale), and what
-- each spends (a sale's is negative).
DROP VIEW IF EXISTS board_plan;
CREATE VIEW board_plan AS
SELECT row_number() OVER (ORDER BY p.step, p.kind) - 1 AS ord, p.step, p.kind, p.player, p.amount,
       CASE p.kind WHEN 'buy' THEN p.amount ELSE -p.amount END AS net, p.win, p.worth, p.after
FROM decision_plan p JOIN board_run USING (run_id);

-- Every line of the board, each once: what to do now, each with the step
-- it takes in the app and the amount that step names, whether it is done,
-- its points (over the season, or next for the line-up) and the numbers
-- that made it (facts: [label, value, unit], none without a value). The
-- plan is the one judgement: no line shows a move it does not make.
--   The line-up the plan wants; the plan's moves in its order (a buy:
--   bid up to the plan's bid, yours shown if it is another, or yours is
--   in at it); each bid of yours the plan does not make, to withdraw (it
--   would win beside the plan's: the plan weighs every bid, yours or not);
--   (a sale: after the auctions when the plan bids (dec_when: once you
--   know they are won, to the game at his value), else take tonight's
--   offer, or wait for what waiting is worth, or it is listed, or list
--   it); your players not listed, rising first: the game
--   offers only for those listed, listing costs nothing and a listed player
--   still plays for you; and those whose listing ends before the last
--   offer before the lock, to relist once it does.
DROP VIEW IF EXISTS board_rows;
CREATE VIEW board_rows AS
WITH gap AS MATERIALIZED (SELECT * FROM lineup_gap),
bm AS MATERIALIZED (SELECT * FROM board_mine),
ins AS (SELECT * FROM gap WHERE side = 'in'),
outs AS (SELECT * FROM gap WHERE side = 'out'),
legal AS (SELECT coalesce(max(legal), 0) AS ok FROM gap),
field AS (
    SELECT -1 AS ord, 'field' AS what,
           'field ' || (SELECT names FROM lineup_names WHERE side = 'in') AS label,
           NULL AS name, NULL AS pos,
           CASE WHEN legal.ok THEN NULL ELSE 'check line-up' END AS step, NULL AS amount,
           0 AS done,
           CASE WHEN legal.ok THEN (SELECT total(next) FROM ins) - (SELECT total(next) FROM outs) END AS gain,
           'next' AS per,
           (SELECT json_group_array(json_array('bench', bench, 'names')) FROM (
                SELECT group_concat(name, ', ' ORDER BY i) AS bench FROM outs
                WHERE player NOT IN (SELECT player FROM bm WHERE sell)) WHERE bench IS NOT NULL) AS facts,
           (SELECT json_group_array(player ORDER BY -next, player) FROM board_eleven) AS players
    FROM legal
    WHERE EXISTS (SELECT 1 FROM ins) OR EXISTS (SELECT 1 FROM outs)),
buys AS (
    SELECT p.ord, 'buy', 'buy ' || b.name, b.name, b.pos,
           CASE WHEN b.my_bid = b.cost THEN 'bid in' ELSE 'bid up to' END, b.cost, coalesce(b.my_bid = b.cost, 0),
           b.worth, 'season',
           b.facts,
           json_array(b.player)
    FROM board_buy_facts b JOIN board_plan p ON p.kind = 'buy' AND p.player = b.player
    WHERE b.in_plan),
sells AS (
    SELECT p.ord, 'sell',
           'sell ' || s.name, s.name, s.pos,
           CASE WHEN p.after THEN 'sell after the auctions'
                WHEN s.takes THEN 'take offer' WHEN s.offer IS NOT NULL THEN 'wait'
                WHEN s.listed THEN 'listed' ELSE 'list' END,
           CASE WHEN p.after THEN s.value WHEN s.offer IS NOT NULL THEN s.raises END,
           s.listed AND NOT s.takes AND NOT p.after, s.worth, 'season',
           s.facts,
           json_array(s.player)
    FROM board_sell_facts s JOIN board_plan p ON p.kind = 'sell' AND p.player = s.player
    WHERE s.sell),
pulled AS (
    SELECT 900 + row_number() OVER (ORDER BY f.player), 'withdraw', 'withdraw bid on ' || f.name, f.name, f.pos,
           'withdraw bid', f.my_bid, 0, NULL, 'season', '[]', json_array(f.player)
    FROM sim_facts f JOIN board_run USING (run_id)
    WHERE f.my_bid > 0 AND f.player NOT IN (SELECT player FROM board_plan WHERE kind = 'buy')),
unlisted AS (
    SELECT 2000, 'list',
           'list ' || group_concat(name, ', ' ORDER BY -coalesce(trend, 0.0), player), NULL, NULL,
           NULL, NULL, 0, NULL, 'season',
           (SELECT json_group_array(json_array('rising', rising, 'names')) FROM (
                SELECT group_concat(name, ', ' ORDER BY -trend, player) AS rising
                FROM bm WHERE NOT listed AND NOT sell AND trend > 0) WHERE rising IS NOT NULL),
           json_group_array(player)
    FROM bm WHERE NOT listed AND NOT sell HAVING count(*) > 0),
relist AS (
    SELECT 1999, 'relist',
           'relist ' || group_concat(name, ', ' ORDER BY relist_after, -coalesce(trend, 0.0), player), NULL, NULL,
           NULL, NULL, 0, NULL, 'season',
           json_array(json_array('once the listing ends', min(relist_after), 'time')),
           json_group_array(player)
    FROM bm WHERE relist_after IS NOT NULL HAVING count(*) > 0)
SELECT row_number() OVER (ORDER BY ord) - 1 AS ord, what, label, name,
       pos, step, amount, done, gain, per, facts, players
FROM (SELECT * FROM field UNION ALL SELECT * FROM buys UNION ALL SELECT * FROM pulled UNION ALL SELECT * FROM sells
      UNION ALL SELECT * FROM unlisted UNION ALL SELECT * FROM relist);

-- The board's headline: when the next jornada locks (next_lock), your money, where you
-- are expected to finish and your chance of first, now and once the plan
-- is made (every bid won), your season's points band, what the plan
-- leaves you, what more you could bid after
-- it and your bids it does not make (into the debt the game allows, to
-- clear by selling before the lock), and what is still to do.
DROP VIEW IF EXISTS board_head;
CREATE VIEW board_head AS
WITH me AS (SELECT * FROM board_league WHERE me),
lock AS (SELECT lock AS kick FROM next_lock)
SELECT (SELECT run_id FROM board_run) AS run_id,
       substr((SELECT cut()), 1, 13) || ':' || substr((SELECT cut()), 14) AS generated_at,
       (SELECT substr(kick, 1, 13) || ':' || substr(kick, 14, 2) || ':00+00:00' FROM lock) AS lock_at,
       me.cash, (SELECT total(my_bid) FROM sim_facts JOIN board_run USING (run_id)) AS cash_locked,
       round(me.finish, 2) AS finish, round(me.p_first, 3) AS p_win,
       (SELECT round(finish_after, 2) FROM sim_runs JOIN board_run USING (run_id)) AS finish_after,
       (SELECT round(p_first_after, 3) FROM sim_runs JOIN board_run USING (run_id)) AS p_win_after,
       me.lo AS band_lo, me.hi AS band_hi,
       CASE WHEN EXISTS (SELECT 1 FROM board_plan) THEN (SELECT cash_after FROM sim_runs JOIN board_run USING (run_id)) END
           AS cash_after,
       (SELECT max(0.0, can_bid) FROM sim_runs JOIN board_run USING (run_id))
       - (SELECT total(f.my_bid) FROM sim_facts f JOIN board_run USING (run_id)
          WHERE f.my_bid > 0 AND f.player NOT IN (SELECT player FROM board_plan WHERE kind = 'buy')) AS can_bid,
       (SELECT group_concat(label, '; ' ORDER BY ord) FROM board_rows
        WHERE NOT done) AS ping
FROM me;

-- decisions.json: the board as one JSON document, the phone's copy: its
-- lines (board_rows), as the phone draws them; a bid's also at its asking
-- price, with the chance of winning at each (bid_odds; sure with no past);
-- and the two rankings each move of the plan is chosen from, valued alone
-- from the squad as it is (decision_option.gain, the eleven's points
-- week by week): every buy that adds points, most first, and every sale,
-- the fewest points lost first (more: past the first three of each and not
-- the plan's, to fold away); each a line as the plan's are, to accept on
-- the phone (a bid; the game's offer on him taken, or he listed). A move of the plan is drawn in its ranking,
-- as the plan's line says it (ranked, on that line of do).
DROP VIEW IF EXISTS board_json;
CREATE VIEW board_json AS
WITH br AS MATERIALIZED (SELECT * FROM board_rows),
rows AS (
    SELECT 'do' AS list, ord, what, label, name, pos, step, amount, done, gain, per, facts, players,
           NULL AS money, NULL AS waiting, NULL AS trend, NULL AS plan, what IN ('buy', 'sell') AS fold
    FROM br
    UNION ALL
    SELECT 'buys', row_number() OVER (ORDER BY -b.gain, b.player), 'buy', coalesce(p.label, 'buy ' || b.name), b.name, b.pos,
           coalesce(p.step, CASE WHEN b.my_bid = b.cost THEN 'bid in' ELSE 'bid up to' END),
           coalesce(p.amount, b.cost), coalesce(p.done, b.my_bid = b.cost, 0),
           b.gain, 'season', b.facts, json_array(b.player), b.asking, NULL, b.trend, b.in_plan,
           row_number() OVER (ORDER BY -b.gain, b.player) > 3 AND NOT b.in_plan
    FROM board_buy_facts b
    LEFT JOIN br p ON p.what = 'buy' AND p.players = json_array(b.player)
    WHERE b.gain > 0
    UNION ALL
    SELECT 'sells', row_number() OVER (ORDER BY -s.gain, -s.raises, s.player),
           CASE WHEN s.listed OR p.label IS NOT NULL THEN 'sell' ELSE 'list' END,
           coalesce(p.label, CASE WHEN s.listed THEN 'sell ' ELSE 'list ' END || s.name),
           s.name, s.pos, coalesce(p.step, CASE WHEN s.takes THEN 'take offer' WHEN s.offer IS NOT NULL THEN 'wait' END),
           CASE WHEN p.label IS NOT NULL THEN p.amount WHEN s.takes THEN s.offer ELSE s.waiting END, coalesce(p.done, 0),
           s.gain, 'season', s.facts, json_array(s.player), s.offer, s.waiting, s.trend, s.sell,
           row_number() OVER (ORDER BY -s.gain, -s.raises, s.player) > 3 AND NOT s.sell
    FROM board_sell_facts s
    LEFT JOIN br p ON p.what = 'sell' AND p.players = json_array(s.player)),
lines AS MATERIALIZED (SELECT list, ord, json(CASE list WHEN 'do' THEN json_remove(o, '$.price', '$.offer', '$.waiting', '$.trend', '$.plan')
                                      WHEN 'buys' THEN json_remove(o, '$.waiting') ELSE o END) AS o
          FROM (SELECT list, ord, json(CASE WHEN what = 'field'
                                             THEN json_remove(o, '$.name', '$.pos', '$.asking', '$.win', '$.asking_win')
                                             WHEN what <> 'buy' THEN json_remove(o, '$.asking', '$.win', '$.asking_win')
                                             ELSE o END) AS o
               FROM (SELECT r.list, r.ord, r.what, json_object(
                         'name', r.name, 'pos', r.pos, 'what', r.what, 'label', r.label, 'step', r.step,
                         CASE r.list WHEN 'buys' THEN 'price' ELSE 'offer' END, r.money, 'waiting', r.waiting, 'trend', r.trend,
                         'plan', json(CASE WHEN r.plan THEN 'true' ELSE 'false' END),
                         'amount', r.amount, 'done', json(CASE WHEN r.done THEN 'true' ELSE 'false' END),
                         'gain', r.gain, 'per', r.per, 'facts', json(coalesce(r.facts, '[]')),
                         CASE r.list WHEN 'do' THEN 'ranked' ELSE 'more' END,
                         json(CASE WHEN r.fold THEN 'true' ELSE 'false' END),
                         'asking', b.asking, 'win', round(b.win, 3),
                         'asking_win', CASE WHEN b.player IS NOT NULL THEN round(coalesce(
                             (SELECT o.p_win FROM bid_odds o WHERE o.player = b.player AND o.ratio = 1.0), 1.0), 3)
                         END,
                         -- what the line is accepted at on the phone (act.py does it): a bid at
                         -- its amount or his asking price, an offer to take at its amount, a
                         -- withdrawal, listing or line-up as it is (null); nothing else.
                         'accepts', json(CASE WHEN r.what = 'buy' AND b.asking IS NOT NULL AND b.asking <> r.amount
                                                  THEN json_array(b.asking, r.amount)
                                              WHEN r.what = 'buy' OR (r.what = 'sell' AND r.step = 'take offer')
                                                  THEN json_array(r.amount)
                                              WHEN r.what IN ('withdraw', 'list', 'field') THEN json_array(NULL)
                                              ELSE '[]' END),
                         'act', (SELECT json_group_array(json_object(
                                     'player_team', t.player_team_id, 'player', coalesce(t.player_id, m.player_id),
                                     'market', m.market_id, 'bid', m.bid_id, 'pos', f.pos, 'value', f.value))
                                 FROM json_each(r.players) e LEFT JOIN sim_facts f ON f.run_id = (SELECT run_id FROM board_run) AND f.player = e.value
                                 LEFT JOIN current_api_teams t ON t.player = e.value
                                 LEFT JOIN current_api_market m ON m.player = e.value)) AS o
                     FROM rows r LEFT JOIN board_buys b ON r.what = 'buy'
                                                AND b.player = json_extract(r.players, '$[0]'))))
SELECT json_object(
    'generated_at', h.generated_at, 'lock_at', h.lock_at,
    'cash', h.cash, 'cash_locked', h.cash_locked, 'finish', h.finish, 'p_win', h.p_win,
    'finish_after', h.finish_after, 'p_win_after', h.p_win_after,
    'band', json_array(h.band_lo, h.band_hi),
    'do', (SELECT json_group_array(json(o) ORDER BY ord) FROM lines WHERE list = 'do'),
    'cash_after', h.cash_after, 'can_bid', h.can_bid, 'ping', coalesce(h.ping, ''),
    'buys', (SELECT json_group_array(json(o) ORDER BY ord) FROM lines WHERE list = 'buys'),
    'sells', (SELECT json_group_array(json(o) ORDER BY ord) FROM lines WHERE list = 'sells'),
    'exposed', (SELECT json_group_array(json_object('name', name, 'pos', pos, 'clause', clause)
                                        ORDER BY ord) FROM board_clauses),
    'standings', (SELECT json_group_array(json_object(
                      'manager', manager, 'me', json(CASE WHEN me THEN 'true' ELSE 'false' END),
                      'now', now, 'lo', lo, 'hi', hi, 'cash', cash,
                      'estimated', json(CASE WHEN estimated THEN 'true' ELSE 'false' END),
                      'p_above', p_above) ORDER BY ord) FROM board_league)) AS json
FROM board_head h;

-- A run to remind you of your line-up is due once the next jornada's lock is
-- within REMIND_HOURS, and only once: not if a run was asked for it
-- (trigger 'reminder', as_of the window's opening or later).
DROP VIEW IF EXISTS lock_due;
CREATE VIEW lock_due AS
WITH w AS (
    SELECT lock, strftime('%Y-%m-%dT%H%MZ', stamp_jd(lock) - (SELECT remind_hours FROM k) / 24.0) AS opens
    FROM next_lock WHERE lock IS NOT NULL)
SELECT lock FROM w
WHERE opens <= (SELECT cut())
  AND NOT EXISTS (SELECT 1 FROM runs WHERE trigger = 'reminder' AND as_of >= w.opens);

-- What to tell you about your line-up before the next lock, from the last
-- run: set it, if the app's is not one I can match, or change it to your
-- best eleven. No row when the line-up set is that eleven.
DROP VIEW IF EXISTS lineup_alert;
CREATE VIEW lineup_alert AS
WITH g AS (SELECT * FROM lineup_gap),
ins AS (SELECT names FROM lineup_names WHERE side = 'in'),
outs AS (SELECT names FROM lineup_names WHERE side = 'out'),
left AS (SELECT CAST(round((
             stamp_jd(lock)
             - stamp_jd((SELECT cut()))
         ) * 1440) AS INTEGER) AS minutes FROM next_lock)
SELECT CASE WHEN (SELECT max(legal) FROM g)
            THEN 'Change your line-up: field ' || ins.names || ' for ' || outs.names
            ELSE 'Set your line-up: ' || ins.names END
       || ' — locks in ' || (left.minutes / 60) || 'h' || printf('%02d', left.minutes % 60) AS text
FROM ins, outs, left WHERE EXISTS (SELECT 1 FROM g);

-- How much of a player's miss (actual less forecast, the bias taken out)
-- carries from one jornada to the next, over the squares of what was
-- expected: what forecast.PERSISTENT_SHARE is checked against. One row a
-- graded run; the next-jornada forecast of every player (from_j = jornada).
DROP VIEW IF EXISTS persistence_by_run;
CREATE VIEW persistence_by_run AS
WITH g AS MATERIALIZED (SELECT run_id, player, jornada, actual - predicted AS r, predicted AS e
                        FROM grade_forecast WHERE from_j = jornada),
b AS (SELECT run_id, avg(r) AS bias FROM g GROUP BY run_id),
pairs AS (SELECT x.run_id, sum((x.r - b.bias) * (y.r - b.bias)) AS num, sum(x.e * y.e) AS den
          FROM g x JOIN g y ON y.run_id = x.run_id AND x.player = y.player AND x.jornada < y.jornada
          JOIN b ON b.run_id = x.run_id GROUP BY x.run_id)
SELECT run_id, CASE WHEN den > 0 THEN round(sqrt(max(0.0, num) / den), 3) ELSE 0.0 END AS persistent_share
FROM pairs;
