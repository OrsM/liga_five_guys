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

-- Every move the board ranked (decide.board's rows), in board order: the
-- funnel's candidates, rank and verdict, and whether the plan took it.
-- Why the plan left a move out costs a joint scoring: ask.py why.
CREATE TABLE IF NOT EXISTS funnel (
    run_id      INTEGER NOT NULL REFERENCES runs(run_id),
    rank        INTEGER NOT NULL,
    kind        TEXT NOT NULL,     -- buy, sell, swap
    buy         TEXT,              -- player key
    sell        TEXT,              -- player keys, comma-separated
    label       TEXT NOT NULL,     -- Action.label
    cost        REAL NOT NULL,
    proceeds    REAL NOT NULL,
    gain        REAL NOT NULL,     -- season points against doing nothing, its money at MONEY_RATE
    p_better    REAL NOT NULL,     -- chance it leaves you better off (ffcore.gain, Market.money)
    per_million REAL NOT NULL,
    verdict     TEXT,              -- why it fails the bar; NULL if it clears it
    in_plan     INTEGER NOT NULL,
    money       REAL,              -- what it does to your money MONEY_H updates on, millions (Move.money)
    win         REAL,              -- a buy's chance its bid wins (Move.win)
    alone       INTEGER,           -- whether it can be made by itself (Move.alone)
    PRIMARY KEY (run_id, rank)
);

-- In debt, every sale of yours as ranked from your squad as it is (Board.owed):
-- what the sales clearing the debt were chosen from. The funnel ranks the
-- rest from the squad after those sales.
CREATE TABLE IF NOT EXISTS debt_sales (
    run_id      INTEGER NOT NULL REFERENCES runs(run_id),
    rank        INTEGER NOT NULL,
    sell        TEXT NOT NULL,     -- player key
    label       TEXT NOT NULL,     -- Action.label
    proceeds    REAL NOT NULL,
    gain        REAL NOT NULL,     -- as funnel.gain
    money       REAL,              -- as funnel.money
    per_million REAL NOT NULL,
    in_plan     INTEGER NOT NULL,
    PRIMARY KEY (run_id, rank)
);

-- Each plan a run made and its headline: the board's own (profile
-- 'steady': CONFIDENCE, MONEY_RATE; its moves are sim_plan's) and the bold
-- one (decide.bold), what it gains, the cash it leaves you at the lock and
-- your chance of first once it is made. The bold plan's moves are
-- sim_plans'.
CREATE TABLE IF NOT EXISTS sim_plan_heads (
    run_id      INTEGER NOT NULL REFERENCES runs(run_id),
    profile     TEXT NOT NULL,
    gain        REAL NOT NULL,
    cash_after  REAL NOT NULL,
    p_first     REAL NOT NULL,
    PRIMARY KEY (run_id, profile)
);
CREATE TABLE IF NOT EXISTS sim_plans (
    run_id      INTEGER NOT NULL REFERENCES runs(run_id),
    profile     TEXT NOT NULL,
    ord         INTEGER NOT NULL,
    kind        TEXT NOT NULL,
    label       TEXT NOT NULL,     -- Action.label
    cost        REAL NOT NULL,
    proceeds    REAL NOT NULL,
    gain        REAL NOT NULL,     -- as funnel.gain, at the profile's money rate
    p_better    REAL NOT NULL,
    PRIMARY KEY (run_id, profile, ord)
);

-- Every forecast cell with factors (ffcore.schedule.Factors): points =
-- ppm x fixture, plays = picked x fit, expected from the two. Squads and
-- the market only, ~2.8k rows a run.
CREATE TABLE IF NOT EXISTS forecast (
    run_id   INTEGER NOT NULL REFERENCES runs(run_id),
    jornada  INTEGER NOT NULL,
    player   TEXT NOT NULL,
    ppm      REAL NOT NULL,
    fixture  REAL NOT NULL,
    picked   REAL NOT NULL,
    fit      REAL NOT NULL,
    points   REAL NOT NULL,
    plays    REAL NOT NULL,
    expected REAL NOT NULL,
    PRIMARY KEY (run_id, jornada, player)
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

-- The board as a table each list was, before the board became views
-- (S6b): gone, its runs in git (reports/decisions.json).
DROP TABLE IF EXISTS boards;
DROP TABLE IF EXISTS board_do;
DROP TABLE IF EXISTS board_squad;
DROP TABLE IF EXISTS board_standings;
DROP TABLE IF EXISTS board_exposed;

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

-- Your squad (mine) and the plan's buys: the chance he plays next, expected
-- points next and over the season, in the best eleven now (xi) and once
-- the plan is made (plan_xi); if yours, the points his sale costs per million.
CREATE TABLE IF NOT EXISTS sim_players (
    run_id      INTEGER NOT NULL REFERENCES runs(run_id),
    player      TEXT NOT NULL,
    start       REAL,
    next        REAL,
    season      REAL,
    mine        INTEGER NOT NULL,
    xi          INTEGER NOT NULL,
    plan_xi     INTEGER NOT NULL,
    per_million REAL,
    PRIMARY KEY (run_id, player)
);

-- The market's and the league's facts as the run saw them (player_facts and
-- sale_odds as of its cut), every player: the board shows these, and a
-- past run's board is these and its decisions. fetches is what a sale gets
-- (NULL with no offer standing on him). sim_facts_now is what is inserted.
CREATE TABLE IF NOT EXISTS sim_facts (
    run_id  INTEGER NOT NULL REFERENCES runs(run_id),
    player  TEXT NOT NULL,
    name    TEXT NOT NULL,
    pos     TEXT NOT NULL,
    value   REAL,
    trend   REAL,
    paid    REAL,
    offer   REAL,
    clause  REAL,
    price   REAL,
    route   TEXT,
    my_bid  REAL,
    fetches REAL,
    PRIMARY KEY (run_id, player)
);

-- What waiting to sell was worth as the run saw it (waiting as of its cut),
-- a row a player and kind: the board's buys to resell read it.
CREATE TABLE IF NOT EXISTS sim_waiting (
    run_id INTEGER NOT NULL REFERENCES runs(run_id),
    player TEXT NOT NULL,
    kind   TEXT NOT NULL,
    later  REAL,
    PRIMARY KEY (run_id, player, kind)
);

-- The plan's moves in order, each by its funnel rank.
CREATE TABLE IF NOT EXISTS sim_plan (
    run_id INTEGER NOT NULL REFERENCES runs(run_id),
    ord    INTEGER NOT NULL,
    rank   INTEGER NOT NULL,
    PRIMARY KEY (run_id, ord)
);

-- What the plan gains, its moves made together.
CREATE TABLE IF NOT EXISTS sim_runs (
    run_id    INTEGER PRIMARY KEY REFERENCES runs(run_id),
    plan_gain REAL
);

-- grading, when run.py grade finds something new: each past lock replayed
-- with the run's code. A player forecast a row (backtest, GRADE_AHEAD
-- jornadas past each lock); error and calibration are queries over it.
CREATE TABLE IF NOT EXISTS grade_forecast (
    run_id    INTEGER NOT NULL REFERENCES runs(run_id),
    from_j    INTEGER NOT NULL,    -- the lock forecast from
    jornada   INTEGER NOT NULL,
    player    TEXT NOT NULL,
    predicted REAL NOT NULL,
    actual    REAL NOT NULL,       -- 0 if he scored nothing
    flagged   INTEGER NOT NULL,    -- injured, doubtful or suspended then
    plays     REAL,                -- his chance of playing (NULL in runs graded before 2026-10-17)
    PRIMARY KEY (run_id, from_j, jornada, player)
);

-- The forecast of the next jornada replayed at every line-up snapshot
-- (grading.replay), for one variant of the model: how far from the lock the
-- error is acceptable. The same outcomes as grade_forecast, forecast again
-- under the information of each moment, so it says how early a forecast is
-- good, not which model is (paired_gain).
-- The moments replay has tried for a variant, kept or found empty
-- (grading.TODO): so a moment that forecasts nobody is not tried again.
CREATE TABLE IF NOT EXISTS replay_tried (
    variant TEXT NOT NULL,
    as_of   TEXT NOT NULL,
    PRIMARY KEY (variant, as_of)
);

CREATE TABLE IF NOT EXISTS replay_forecast (
    variant   TEXT NOT NULL,
    as_of     TEXT NOT NULL,       -- the snapshot replayed
    jornada   INTEGER NOT NULL,    -- the next one then
    player    TEXT NOT NULL,
    predicted REAL NOT NULL,
    actual    REAL NOT NULL,       -- 0 if he scored nothing
    flagged   INTEGER NOT NULL,    -- injured, doubtful or suspended then
    lead_h    REAL NOT NULL,       -- hours from the snapshot to the lock
    PRIMARY KEY (variant, as_of, jornada, player)
) WITHOUT ROWID;

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
r AS (SELECT *, row_number() OVER (PARTITION BY src ORDER BY at DESC) AS k, julianday(substr(at, 1, 10) || 'T' || substr(at, 12, 2) || ':' || substr(at, 14, 2) || 'Z') AS jd FROM snaps),
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

-- Each rival's board for a run (sim.rivals): the funnel with him as "me".
-- His cash is our estimate and his pending bids are invisible to us.
CREATE TABLE IF NOT EXISTS rival_boards (
    run_id    INTEGER NOT NULL REFERENCES runs(run_id),
    manager   TEXT NOT NULL,
    cash      REAL NOT NULL,
    plan_gain REAL NOT NULL,       -- points his plan gains over the season
    PRIMARY KEY (run_id, manager)
);
CREATE TABLE IF NOT EXISTS rival_moves (
    run_id   INTEGER NOT NULL REFERENCES runs(run_id),
    manager  TEXT NOT NULL,
    ord      INTEGER NOT NULL,
    label    TEXT NOT NULL,
    kind     TEXT NOT NULL,
    gain     REAL,
    p_better REAL,
    cost     REAL,
    proceeds REAL,
    PRIMARY KEY (run_id, manager, ord)
);

-- Every move each replayed board ranked, against what its players then did.
CREATE TABLE IF NOT EXISTS grade_decisions (
    run_id     INTEGER NOT NULL REFERENCES runs(run_id),
    ord        INTEGER NOT NULL,
    lock       INTEGER NOT NULL,   -- the jornada whose lock it was ranked at
    move       TEXT NOT NULL,
    reached    TEXT NOT NULL,      -- plan, cleared, rejected
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
FROM replay_forecast f JOIN lead_buckets b ON f.lead_h >= b.from_h AND f.lead_h < b.to_h
GROUP BY variant, b.from_h, b.lead;

-- What forecasting earlier costs: the same player-jornadas forecast at each
-- lead and at the jornada's last replayed moment; the mean squared error
-- earlier minus last (positive: earlier is worse) with its interval (claim).
DROP VIEW IF EXISTS replay_gap;
CREATE VIEW replay_gap AS
WITH fin AS (SELECT variant, jornada, max(as_of) AS at FROM replay_forecast GROUP BY variant, jornada),
d AS (SELECT f.variant, f.lead_h, f.flagged,
             (f.predicted - f.actual) * (f.predicted - f.actual)
           - (g.predicted - g.actual) * (g.predicted - g.actual) AS d
      FROM replay_forecast f JOIN fin ON fin.variant = f.variant AND fin.jornada = f.jornada
      JOIN replay_forecast g ON g.variant = f.variant AND g.jornada = f.jornada
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
-- read as of cut(), each row with its player). League writes the one
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

-- When the next match kicks off after cut(): each player locks at his own
-- match's kickoff, of the jornada under way or one to come (jornada_calendar;
-- NULL once none is left to play).
DROP VIEW IF EXISTS behind_jornadas;
DROP VIEW IF EXISTS next_kickoff;
CREATE VIEW next_kickoff AS
SELECT min(k.kick) AS kick, min(k.t) AS t FROM kickoffs k JOIN jornada_calendar c USING (jornada)
WHERE k.kick > (SELECT cut()) AND c.state IN ('under way', 'ahead');

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
FROM listed l JOIN current_market m ON m.ff_id = l.player AND m.club <> ''
JOIN kickoffs k ON m.club IN (k.home, k.away)
JOIN jornada_calendar c ON c.jornada = k.jornada AND c.state IN ('under way', 'ahead')
WHERE k.t < l.close;

-- The game's nightly offers still to come before the next match locks: one
-- as each standing offer expires, then one a day (0 with none standing),
-- and when the first of them comes (first, a julian day).
DROP VIEW IF EXISTS nights_left;
CREATE VIEW nights_left AS
WITH e AS (SELECT max(julianday(expires_at)) AS ends FROM current_api_offers
           WHERE status = 'pending' AND expires_at <> ''),
l AS (SELECT t AS lock FROM next_kickoff)
SELECT CASE WHEN ends IS NOT NULL AND lock > ends THEN CAST(lock - ends AS INTEGER) + 1 ELSE 0 END AS nights,
       ends AS first
FROM e, l;

-- When each manager's listing on the game's market ends, as of cut() (t, a
-- julian day): a listing runs three days, and the game offers for a player
-- at each close only while he is listed.
DROP VIEW IF EXISTS listing_ends;
CREATE VIEW listing_ends AS
SELECT player, max(expires_at) AS ends, max(julianday(expires_at)) AS t
FROM current_api_market
WHERE player IS NOT NULL AND seller = 'marketPlayerTeam' AND expires_at <> ''
GROUP BY player;

-- The nights the game will offer for a player bought at tonight's auctions
-- before the next match locks: one a day after the auctions close (0 with
-- none open).
DROP VIEW IF EXISTS resale_nights;
CREATE VIEW resale_nights AS
WITH e AS (SELECT max(julianday(expires_at)) AS ends FROM current_api_market
           WHERE seller = 'marketPlayerLeague' AND coalesce(expires_at, '') <> ''),
l AS (SELECT t AS lock FROM next_kickoff)
SELECT CASE WHEN ends IS NOT NULL AND lock > ends THEN CAST(lock - ends AS INTEGER) ELSE 0 END AS nights
FROM e, l;

-- What waiting to sell is worth, over the nights before the next match
-- locks: each night's offer to come taken only when it beats waiting on
-- (the last before the lock always). An offer to come is a past one's
-- share of value (recent_offers) of the value he will
-- have that night: his value moved by his trend, the share carry says is
-- made by then (price_carry). For a player of yours with an offer standing
-- (kind 'sale', nights_left), and for one on the game's market bought
-- tonight and sold before the lock (kind 'resale', resale_nights; 'flat',
-- the same with his value standing still). NULL without a value or past offers.
DROP VIEW IF EXISTS waiting;
CREATE VIEW waiting AS
WITH RECURSIVE
r AS MATERIALIZED (SELECT ratio FROM recent_offers),
c AS MATERIALIZED (SELECT h, carry FROM price_carry),
s AS MATERIALIZED (
    SELECT player, 'sale' AS kind, value, coalesce(trend, 0.0) AS trend,
           (SELECT nights FROM nights_left) AS nights
    FROM player_facts WHERE offer IS NOT NULL
  UNION ALL
    SELECT player, kind, value, CASE kind WHEN 'flat' THEN 0.0 ELSE coalesce(trend, 0.0) END,
           (SELECT nights FROM resale_nights)
    FROM player_facts, (SELECT 'resale' AS kind UNION ALL SELECT 'flat')
    WHERE route = 'free' AND owner IS NULL AND price IS NOT NULL),
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

-- Every player the market or a futbolfantasy line-up names, as of cut():
-- his name and position, what he is worth (market value; 0 is none),
-- whether he is on sale (price; route: listed by a manager, else free,
-- the game's own), your pending bid, his owner and release clause (any can
-- be paid now), the best offer standing on him if he is yours, what his
-- owner paid, his price trend. What Market is built from (assemble) and
-- the board shows.
DROP VIEW IF EXISTS player_facts;
CREATE VIEW player_facts AS
WITH who AS (
    SELECT ff_id AS player, trim(name) AS name, position, value FROM current_market
    WHERE ff_id <> ''
    UNION ALL
    SELECT player, trim(player_name), '', NULL FROM current_lineups
    WHERE source = 'futbolfantasy' AND player IS NOT NULL
      AND player NOT IN (SELECT ff_id FROM current_market)),
sale AS (SELECT player, sale_price AS price,
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
-- change from one day's to the next is a step. As of cut(): today's row is
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
DROP TABLE IF EXISTS constants;
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
    (SELECT value FROM model_constants WHERE name = 'GOOD_OFFER' AND variant = (SELECT variant())) AS good_offer,
    (SELECT value FROM model_constants WHERE name = 'MONEY_RATE' AND variant = (SELECT variant())) AS money_rate,
    (SELECT value FROM model_constants WHERE name = 'RESALE_MARGIN' AND variant = (SELECT variant())) AS resale_margin,
    (SELECT value FROM model_constants WHERE name = 'MATCH_LEN' AND variant = (SELECT variant())) AS match_len,
    (SELECT value FROM model_constants WHERE name = 'NEUTRAL_START' AND variant = (SELECT variant())) AS neutral_start,
    (SELECT value FROM model_constants WHERE name = 'ABSENT_START' AND variant = (SELECT variant())) AS absent_start,
    (SELECT value FROM model_constants WHERE name = 'FALLBACK_K' AND variant = (SELECT variant())) AS fallback_k,
    (SELECT value FROM model_constants WHERE name = 'PICK_K' AND variant = (SELECT variant())) AS pick_k,
    (SELECT value FROM model_constants WHERE name = 'PPM_BANDS' AND variant = (SELECT variant())) AS ppm_bands,
    (SELECT value FROM model_constants WHERE name = 'REMIND_HOURS' AND variant = (SELECT variant())) AS remind_hours,
    -- what price_series and momentum_pairs are made with: the key of momentum_day's rows
    (SELECT printf('%.9g|%.9g|%.9g|%.9g', c.value, w0.value, w1.value, w2.value)
     FROM model_constants c, model_constants w0, model_constants w1, model_constants w2
     WHERE c.name = 'CLIP' AND w0.name = 'PRICE_W0' AND w1.name = 'PRICE_W1' AND w2.name = 'PRICE_W2'
       AND c.variant = w0.variant AND c.variant = w1.variant AND c.variant = w2.variant
       AND c.variant = (SELECT variant())) AS ckey;

-- Every valued day of every player as of cut(): the closed days kept in
-- price_step, and the day of the cut (the last snapshot of it up to the cut)
-- worked out against the day before.
-- The last day with a market snapshot as of cut(): the one day price_step
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

-- The longest horizon momentum fits, as of cut(): a quarter of the median
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

-- Momentum, as of cut(): how much of a price change carries into the next
-- h (up to price_hmax), the move over h clipped at CLIP an update.
DROP VIEW IF EXISTS momentum_pairs;
CREATE VIEW momentum_pairs AS
SELECT p.h, p.ff_id, p.i, p.end_day, p.x,
       max(-k.clip * p.h, min(k.clip * p.h, p.change)) AS y
FROM price_ahead p, k WHERE p.end_at <= (SELECT cut());

-- What each closed day adds to the momentum fit, per horizon: the sums of x*y
-- and x*x over the pairs that end that day, written once (facts.MOMENTUM_DAY)
-- for the constants they were made with (ckey).
CREATE TABLE IF NOT EXISTS momentum_day (
    ckey    TEXT NOT NULL,
    h       INTEGER NOT NULL,
    end_day TEXT NOT NULL,
    sxy     REAL NOT NULL,
    sxx     REAL NOT NULL,
    PRIMARY KEY (ckey, h, end_day)
) WITHOUT ROWID;

-- One least-squares slope through the origin per horizon: the closed days
-- kept in momentum_day, and the pairs ending on the day of the cut.
DROP VIEW IF EXISTS momentum;
CREATE VIEW momentum AS
WITH parts AS (
    SELECT h, sxy, sxx FROM momentum_day
    WHERE ckey = (SELECT ckey FROM k) AND end_day < (SELECT d FROM price_day_now)
      AND h <= (SELECT hmax FROM price_hmax)
    UNION ALL
    SELECT h, sum(x * y), sum(x * x) FROM momentum_pairs
    WHERE end_day = (SELECT d FROM price_day_now) GROUP BY h)
SELECT h, sum(sxy) / sum(sxx) AS slope FROM parts GROUP BY h HAVING sum(sxx) <> 0;

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

-- How far a value forecast is off, as of cut(): per horizon, the root mean
-- square of momentum's error over every pair it is fitted on (in % of
-- value, each move clipped as the fit clips it). Prices have fat tails: it
-- is well over the mean absolute error. Kept a run (price_error_fit), as
-- momentum is: it walks every pair (~5s).
DROP VIEW IF EXISTS price_error_now;
CREATE VIEW price_error_now AS
SELECT p.h, sqrt(avg((m.slope * p.x - p.y) * (m.slope * p.x - p.y))) AS rmse
FROM momentum_pairs p JOIN fitted m USING (h) GROUP BY p.h;

CREATE TABLE IF NOT EXISTS price_error_fit (
    as_of TEXT NOT NULL,
    h     INTEGER NOT NULL,
    rmse  REAL NOT NULL,
    PRIMARY KEY (as_of, h)
);
DROP VIEW IF EXISTS price_error;
CREATE VIEW price_error AS
SELECT h, rmse FROM price_error_fit
WHERE as_of = (SELECT max(as_of) FROM price_error_fit WHERE as_of <= (SELECT cut()));

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
-- reads it gets the last one kept at or before cut(), as momentum_fit does.
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

-- How each listing's auction went: every listing on the market (the game's
-- or a manager's) by player and close, its asking price at its last
-- sighting and his price trend then (the last kept at or before its
-- close), and the buy made within ten minutes of its close if one was:
-- what the winner paid (won; NULL: nobody bought) and whether it was you.
-- bar is what a bid had to reach, over asking: the winner's bid; asking
-- where nobody bought or the winner was you (the bids you beat are not
-- seen); NULL for a manager's listing (no one's deal with him is seen).
-- Ground truth for the replays, so it reads every sighting whatever cut().
DROP VIEW IF EXISTS auction_ratios;
DROP VIEW IF EXISTS premium;
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
SELECT w.player, w.at AS close, w.asking, w.seller, w.won, w.me AS by_me,
       CASE WHEN w.seller <> 'marketPlayerLeague' THEN NULL
            WHEN w.won IS NULL OR w.me THEN 1.0 ELSE w.won / w.asking END AS bar,
       (SELECT t.trend FROM price_trend_fit t
        WHERE t.ff_id = w.player
          AND t.as_of = (SELECT max(as_of) FROM fits WHERE as_of <= strftime('%Y-%m-%dT%H%MZ', w.at)))
       AS trend
FROM won w;

-- What a bid wins on the game's market now, for each player listed there:
-- his BID_NEIGHBOURS past listings closest to him in price trend (before
-- cut(), none you won: what you beat is not seen), and for each bid over
-- asking that reached one of their bars (and asking itself) the share it
-- would have won (p_win; 1 with no past to go on).
DROP VIEW IF EXISTS bid_odds;
CREATE VIEW bid_odds AS
WITH past AS MATERIALIZED (
    SELECT trend, bar FROM auction_results
    WHERE bar IS NOT NULL AND trend IS NOT NULL AND NOT coalesce(by_me, 0)
      AND strftime('%Y-%m-%dT%H%MZ', close) <= (SELECT cut())),
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

-- The jornada calendar, as of cut(). Each fixture's kickoff as last seen
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

-- The jornada calendar as of cut(), one row a jornada, what everything
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
c AS (SELECT ff_id, nullif(club, '') AS club FROM current_market WHERE ff_id <> '' GROUP BY ff_id),
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
SELECT player, jornada, sum(pts) AS pts, sum(mins) AS mins FROM (
    SELECT player, jornada, pts, 0.0 AS mins FROM scored
    UNION ALL
    SELECT player, jornada, 0.0, mins FROM squad_minutes
    WHERE player IN (SELECT player FROM scored))
GROUP BY player, jornada;

-- His season so far: points, appearances, and matchday squads (jornadas
-- he scored or was in the squad).
DROP VIEW IF EXISTS season_record;
CREATE VIEW season_record AS
SELECT player, sum(pts) AS pts, sum(mins > 0) * 1.0 AS apps, count(*) * 1.0 AS squads
FROM player_jornadas GROUP BY player;

-- Each app id's player: the futbolfantasy player whose value it equalled
-- at the most market snapshots (an all-players row holds until his next),
-- then the most name stems shared (stems_meet); each app id's unique best,
-- then per player the unique best. Over all rows, not as of cut().
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

-- What a player's points per match is pulled toward: the median of last
-- season's (10+ matches) in his position, or with PPM_BANDS in his
-- position and band of value (that many, cheapest first, as of cut());
-- a band with none, his position's; no position, everyone's.
DROP VIEW IF EXISTS ppm_prior;
CREATE VIEW ppm_prior AS
WITH m AS (SELECT ff_id AS player, slot(position) AS pos,
                  ntile(max(1, CAST((SELECT ppm_bands FROM k) AS INTEGER)))
                      OVER (ORDER BY value, ff_id) AS band
           FROM current_market WHERE ff_id <> ''),
s AS (SELECT m.pos, m.band, l.pts / l.pj AS ppm FROM m JOIN last_season l ON l.ff_id = m.player
      WHERE m.pos <> '' AND l.pj >= 10),
b AS (SELECT pos, band, median(ppm) AS prior FROM s GROUP BY pos, band),
p AS (SELECT pos, median(ppm) AS prior FROM s GROUP BY pos)
SELECT m.player, coalesce(b.prior, p.prior, (SELECT median(ppm) FROM s)) AS prior
FROM m LEFT JOIN b USING (pos, band) LEFT JOIN p USING (pos);

-- The chance he is picked if fit, after his next match: his record this
-- season (appearances over matchday squads) pulled toward NEUTRAL_START,
-- which is worth PICK_K matches. No squads, no row.
DROP VIEW IF EXISTS pick_prior;
DROP VIEW IF EXISTS pick_later;
CREATE VIEW pick_later AS
SELECT player, (k.pick_k * k.neutral_start / 100.0 + apps) / (k.pick_k + squads) AS p
FROM season_record, k WHERE squads > 0;

-- Starts: each matchday squad (starters) against the club's last
-- futbolfantasy listing before the jornada locked. Each player in either,
-- with his listing (start % as a share, status, note) and his minutes:
-- what selection and availability are fitted on.
DROP VIEW IF EXISTS start_outcomes;
CREATE VIEW start_outcomes AS
WITH games AS MATERIALIZED (
    SELECT DISTINCT s.match_id, s.team_slug, s.jornada, r.lock,
           (SELECT max(observed_at) FROM lineups l
            WHERE l.team_slug = s.team_slug AND l.observed_at <= r.lock) AS listed_at
    FROM squad_minutes s JOIN round_locks r USING (jornada)),
listed AS MATERIALIZED (
    SELECT g.match_id, l.team_slug, l.player_slug, l.start_pct, l.status, l.note, f.player
    FROM games g CROSS JOIN lineups l ON l.team_slug = g.team_slug AND l.observed_at = g.listed_at
    LEFT JOIN ff_identity f ON f.player_slug = l.player_slug AND f.player_name = l.player_name
         AND f.team_slug = l.team_slug),
squad AS MATERIALIZED (SELECT * FROM squad_minutes WHERE match_id IN (SELECT match_id FROM games))
SELECT g.lock AS at, g.jornada, g.match_id || ':' || g.team_slug AS grp, k.player_slug,
       coalesce(li.player, sq.player) AS player, li.player_slug IS NOT NULL AS listed,
       li.start_pct / 100.0 AS ff,
       CASE WHEN li.player_slug IS NULL THEN '' ELSE coalesce(nullif(li.status, ''), 'ok') END AS status,
       coalesce(li.note, '') AS note, coalesce(sq.mins, 0.0) AS mins
FROM (SELECT match_id, team_slug, player_slug FROM listed
      UNION SELECT match_id, team_slug, player_slug FROM squad) k
JOIN games g USING (match_id, team_slug)
LEFT JOIN listed li USING (match_id, team_slug, player_slug)
LEFT JOIN squad sq USING (match_id, team_slug, player_slug);

-- How often each player played when listed fit (3 such listings or more):
-- what the chance he is fit under a prognosis is measured against.
DROP VIEW IF EXISTS fit_rate;
CREATE VIEW fit_rate AS
SELECT player, avg(mins > 0) AS rate FROM start_outcomes
WHERE player <> '' AND listed AND status = 'ok'
GROUP BY player HAVING count(*) >= 3;

-- Each player's last start % while listed fit: his selection before a flag.
DROP VIEW IF EXISTS last_fit;
CREATE VIEW last_fit AS
SELECT player, pct FROM (
    SELECT player, 100 * ff AS pct, row_number() OVER (PARTITION BY player ORDER BY at DESC, grp DESC) AS n
    FROM start_outcomes WHERE player <> '' AND listed AND status = 'ok' AND ff IS NOT NULL)
WHERE n = 1;

-- Each prognosis, as listed at one lock, against whether the player played
-- in that jornada and each later one, by where the later one falls against
-- it (prognosis_bucket: startprob.bucket): played, and the plays his fit
-- rate expects. Availability shrinks played / expected toward the bucket's
-- literal reading.
DROP VIEW IF EXISTS availability_tally;
CREATE VIEW availability_tally AS
WITH o AS MATERIALIZED (
    SELECT player, jornada, at, status, note, mins > 0 AS played FROM (
        SELECT *, row_number() OVER (PARTITION BY player, jornada ORDER BY at DESC, grp DESC) AS n
        FROM start_outcomes WHERE player <> '')
    WHERE n = 1),
pairs AS (
    SELECT prognosis_bucket(i.status, i.note, i.at, j.jornada, j.at, i.jornada) AS bucket,
           j.played, f.rate
    FROM o i JOIN fit_rate f USING (player) JOIN o j ON j.player = i.player AND j.jornada >= i.jornada
    WHERE i.status NOT IN ('', 'ok'))
SELECT bucket, sum(played) AS played, sum(rate) AS expected FROM pairs
WHERE bucket IS NOT NULL GROUP BY bucket;

-- What a start % means for selection: a Platt curve (alpha + beta x its
-- log-odds), the grid point with least squared error on fit players'
-- outcomes, kept only if it beats the raw % held out a squad at a time
-- (fitted without that squad, scored on it); else the raw %. A player
-- listed with no % counts as neutral_start, one not listed as absent_start:
-- each the share of such players who played, shrunk toward the constant.
DROP VIEW IF EXISTS start_calibration;
CREATE VIEW start_calibration AS
WITH RECURSIVE n(v) AS (SELECT 0 UNION ALL SELECT v + 1 FROM n WHERE v < 14),
o AS MATERIALIZED (SELECT grp, listed, ff, mins > 0 AS y FROM start_outcomes
                   WHERE status IN ('', 'ok')),
fall AS MATERIALIZED (
    SELECT 100 * (k.fallback_k * k.neutral_start / 100 + vague_played) / (k.fallback_k + vague) AS neutral,
           100 * (k.fallback_k * k.absent_start / 100 + absent_played) / (k.fallback_k + absent) AS absent
    FROM k, (SELECT count(*) FILTER (WHERE listed AND ff IS NULL) AS vague,
                    coalesce(sum(y) FILTER (WHERE listed AND ff IS NULL), 0) AS vague_played,
                    count(*) FILTER (WHERE NOT listed) AS absent,
                    coalesce(sum(y) FILTER (WHERE NOT listed), 0) AS absent_played FROM o)),
obs AS MATERIALIZED (
    SELECT grp, coalesce(ff, CASE WHEN listed THEN neutral ELSE absent END / 100) AS x,
           count(*) AS n, sum(y) AS ys
    FROM o, fall GROUP BY grp, x),
grid AS (SELECT a.v AS i, b.v AS j, round(-3.0 + 0.5 * a.v, 1) AS alpha,
                round(0.2 + 0.4 * b.v, 1) AS beta
         FROM n a, n b WHERE a.v <= 12),
pred AS MATERIALIZED (
    SELECT i, j, alpha, beta, x, CASE WHEN alpha = 0 AND beta = 1 THEN x ELSE platt(x, alpha, beta) END AS p
    FROM grid, (SELECT DISTINCT x FROM obs)),
sse AS MATERIALIZED (
    SELECT i, j, grp, sum(n * p * p - 2 * p * ys + ys) AS e
    FROM obs JOIN pred USING (x) GROUP BY i, j, grp),
total AS MATERIALIZED (SELECT i, j, sum(e) AS e FROM sse GROUP BY i, j),
held AS (
    SELECT grp, e FROM (
        SELECT s.grp, s.e, row_number() OVER (PARTITION BY s.grp ORDER BY t.e - s.e, s.i, s.j) AS n
        FROM sse s JOIN total t USING (i, j))
    WHERE n = 1),
best AS (SELECT alpha, beta FROM total JOIN grid USING (i, j) ORDER BY e, i, j LIMIT 1),
fits AS (
    SELECT (SELECT sum(n) FROM obs) >= 3 AND (SELECT count(DISTINCT grp) FROM obs) >= 2
           AND (SELECT sum(e) FROM held) < (SELECT e FROM total JOIN grid USING (i, j)
                                            WHERE alpha = 0 AND beta = 1) AS better)
SELECT CASE WHEN better THEN best.alpha ELSE 0.0 END AS alpha,
       CASE WHEN better THEN best.beta ELSE 1.0 END AS beta,
       fall.neutral AS neutral_start, fall.absent AS absent_start
FROM fits, fall LEFT JOIN best;

-- What a run records of the facts (sim_facts): every player_facts column
-- with his name as shown, and what a sale of him gets.
DROP VIEW IF EXISTS sim_facts_now;
CREATE VIEW sim_facts_now AS
SELECT f.player, coalesce(name_shown(f.name), f.player) AS name, f.pos, f.value, f.trend, f.paid,
       f.offer, f.clause, f.price, f.route, f.my_bid, o.fetches
FROM player_facts f LEFT JOIN sale_odds o USING (player);

-- The board: the last run's decisions and the facts it saw (sim_*, funnel),
-- the app's line-up and the next kickoff as of cut().
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
       CASE WHEN p.mine THEN coalesce(f.fetches, f.value, 0.0) END AS raises,
       coalesce(f.fetches = f.offer, 0) AS takes
FROM sim_players p JOIN board_run USING (run_id) LEFT JOIN sim_facts f USING (run_id, player);

-- Every buy the last run ranked (the funnel: each player on the market you
-- could get), with what the run saw of him: what he is listed at (asking),
-- the bid it takes and its chance of winning (win), your bid if it is in,
-- what you pay (cost: yours, else the bid it takes), his value and trend, what the
-- funnel made of him, and what selling him on before the lock would get
-- (sim_waiting: resale, and flat, his value standing still).
DROP VIEW IF EXISTS board_market;
DROP VIEW IF EXISTS board_buys;
CREATE VIEW board_buys AS
SELECT f.rank, f.buy AS player, coalesce(s.name, f.buy) AS name, coalesce(s.pos, '') AS pos,
       coalesce(s.price, f.cost) AS asking, f.cost AS bid, s.my_bid, coalesce(s.my_bid, f.cost) AS cost,
       s.value, s.trend,
       f.gain, f.p_better, f.per_million, f.verdict, f.in_plan, f.money, f.win,
       r.later AS resale, l.later AS flat
FROM funnel f JOIN board_run USING (run_id)
LEFT JOIN sim_facts s ON s.run_id = f.run_id AND s.player = f.buy
LEFT JOIN sim_waiting r ON r.run_id = f.run_id AND r.player = f.buy AND r.kind = 'resale'
LEFT JOIN sim_waiting l ON l.run_id = f.run_id AND l.player = f.buy AND l.kind = 'flat'
WHERE f.kind = 'buy';

-- Each of your players: his facts (board_players), whether he is listed,
-- the game's offer standing on him and the share of its recent offers
-- (recent_offers), as a share of value, it beats; what selling him
-- does to your season (the funnel's sale), whether that sale can be made
-- by itself (alone: your side can be fielded without him), whether the
-- plan sells him, and what turning the offer down is worth (waiting: the
-- best of the game's offers still to come before the lock, sim_waiting).
-- relist_after: when his listing ends, if before the last of those offers.
-- A sale the plan makes that loses by itself (its gain, or no side left to
-- field) pays only with the plan's buys at auction (with_bids: their names and the
-- chance all are won, bids_p). They close as the next offer comes, so if
-- one comes before the lock the sale can wait for them; wait_bids is what
-- waiting is worth in points against taking tonight's offer: if won, the
-- money waiting loses (at MONEY_RATE), if lost, the sale not made.
DROP VIEW IF EXISTS board_holdings;
DROP VIEW IF EXISTS board_offers;
DROP VIEW IF EXISTS board_mine;
CREATE VIEW board_mine AS
WITH r AS (SELECT ratio FROM recent_offers),
n AS (SELECT nights, first FROM nights_left),
bids AS (SELECT exp(total(ln(max(u.win, 1e-9)))) AS p, count(*) AS n,
                group_concat(coalesce(s.name, u.buy), ', ' ORDER BY p.ord) AS names
         FROM board_plan p JOIN board_run x JOIN funnel u ON u.run_id = x.run_id AND u.rank = p.rank
         LEFT JOIN sim_facts s ON s.run_id = u.run_id AND s.player = u.buy
         WHERE u.kind = 'buy' AND u.win < 1),
m AS (SELECT b.player, b.name, b.pos, b.value, b.paid, b.trend, b.offer, b.raises, b.takes,
             b.route IS 'listed' AS listed,
             CASE WHEN b.offer IS NOT NULL AND b.value > 0
                  THEN (SELECT avg(r.ratio < b.offer / b.value) FROM r) END AS beats,
             f.rank, f.gain, f.money, f.per_million, coalesce(f.in_plan, 0) AS sell, f.alone,
             w.later AS waiting,
             CASE WHEN b.route IS 'listed' AND l.t < n.first + n.nights - 1 THEN l.ends END AS relist_after,
             coalesce(f.in_plan AND (f.gain <= 0 OR NOT f.alone) AND bids.n > 0, 0) AS with_bids,
             bids.p AS bids_p, bids.names AS bids_names, n.nights
      FROM board_players b JOIN board_run USING (run_id) CROSS JOIN n CROSS JOIN bids
      LEFT JOIN funnel f ON f.run_id = b.run_id AND f.kind = 'sell' AND f.sell = b.player
      LEFT JOIN sim_waiting w ON w.run_id = b.run_id AND w.player = b.player AND w.kind = 'sale'
      LEFT JOIN listing_ends l ON l.player = b.player
      WHERE b.mine)
SELECT m.*,
       CASE WHEN with_bids AND nights > 0 AND offer IS NOT NULL
            THEN bids_p * -(SELECT money_rate FROM k) * (offer - coalesce(waiting, 0.0)) / 1e6
                 + (1 - bids_p) * -gain END AS wait_bids
FROM m;

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

-- Your line-up in the app against the best eleven: of your squad now
-- (basis 'now', sim_players.xi) and of the squad once the plan is made
-- ('plan', plan_xi). The app's line-up is its players, each by its player
-- else by name within your squad, and counts only if every one is yours
-- and there are as many as the eleven; otherwise it is unknown (legal 0)
-- and the whole eleven is to set. 'in' are the eleven's players not in it,
-- 'out' (i, its order in the app) those in it that are not in the eleven.
DROP VIEW IF EXISTS lineup_gap;
CREATE VIEW lineup_gap AS
WITH app AS (
    SELECT t.rowid AS i, coalesce(a.player, (
               SELECT b.player FROM board_players b WHERE b.mine
               AND name_key(b.name) IN (name_key(t.player_name), name_key(t.player_name_full))
               ORDER BY b.player LIMIT 1)) AS player
    FROM api_lineup t LEFT JOIN app_player a ON a.app_id = t.player_id
    WHERE (t.source, t.observed_at) IN (SELECT source, max(observed_at) FROM api_lineup
                                        WHERE observed_at <= (SELECT cut()) GROUP BY source)),
fielded AS (SELECT i, player FROM app
            WHERE NOT EXISTS (SELECT 1 FROM app a LEFT JOIN board_players b
                              ON b.player = a.player AND b.mine WHERE b.player IS NULL)),
bases(basis) AS (VALUES ('now'), ('plan')),
eleven AS (SELECT b.basis, p.player, p.name, p.next FROM bases b
           JOIN board_players p ON (b.basis = 'now' AND p.xi) OR (b.basis = 'plan' AND p.plan_xi)),
legal AS (SELECT basis, (SELECT count(*) FROM fielded) > 0
                 AND (SELECT count(*) FROM fielded) = (SELECT count(*) FROM eleven e WHERE e.basis = bases.basis)
                 AS ok FROM bases)
SELECT e.basis, 'in' AS side, NULL AS i, e.player, e.name, e.next, l.ok AS legal
FROM eleven e JOIN legal l USING (basis)
WHERE NOT l.ok OR e.player NOT IN (SELECT player FROM fielded)
UNION ALL
SELECT l.basis, 'out', f.i, b.player, b.name, b.next, l.ok
FROM fielded f JOIN board_players b USING (player), legal l
WHERE l.ok AND f.player NOT IN (SELECT player FROM eleven e WHERE e.basis = l.basis);

-- The plan's moves, in its order, each by its funnel rank, and what it
-- spends (a sale's is negative).
DROP VIEW IF EXISTS board_plan;
CREATE VIEW board_plan AS
SELECT p.ord, f.rank, f.kind, f.cost - f.proceeds AS net
FROM sim_plan p JOIN board_run USING (run_id)
JOIN funnel f ON f.run_id = p.run_id AND f.rank = p.rank;

-- Your cash once the plan is made, how far below zero the game lets
-- bids take you before the lock (20% of your team value), and your bids
-- in that the plan does not make (held: money no other bid can use).
DROP VIEW IF EXISTS board_money;
CREATE VIEW board_money AS
SELECT b.cash - (SELECT total(net) FROM board_plan) AS left, 0.2 * s.team_value AS debt,
       (SELECT total(f.my_bid) FROM sim_facts f JOIN board_run USING (run_id)
        WHERE f.my_bid > 0 AND f.player NOT IN (
            SELECT u.buy FROM board_plan p JOIN board_run r JOIN funnel u
            ON u.run_id = r.run_id AND u.rank = p.rank WHERE u.buy IS NOT NULL)) AS held
FROM board_league b JOIN current_api_standings s USING (manager) WHERE b.me;

-- Players on the game's market worth buying tonight to sell before the
-- next match locks, of those the plan does not buy: what selling him on
-- gets (board_buys.resale) over the bid it takes (or yours, if it is in),
-- when that beats the bid by RESALE_MARGIN, most profit first. Each is
-- taken if, with those taken before it, the bids stay within your cash
-- after the plan plus the debt the game allows, less your bids in on
-- players neither the plan nor this list buys, and, every price standing
-- still (flat), the sales would still leave you at least zero at the lock.
DROP VIEW IF EXISTS board_resale;
CREATE VIEW board_resale AS
WITH pick AS MATERIALIZED (
    SELECT b.*, b.resale - b.cost AS profit FROM board_buys b
    WHERE NOT b.in_plan AND b.resale >= b.cost * (1 + (SELECT resale_margin FROM k))),
ranked AS MATERIALIZED (
    SELECT p.*, row_number() OVER (ORDER BY p.profit DESC, p.player) AS n,
           m.left + m.debt - m.held + (SELECT total(my_bid) FROM pick) AS can_spend, m.left AS can_lose
    FROM pick p, board_money m),
took(n, spent, lost, taken) AS (
    SELECT 0, 0.0, 0.0, 0
  UNION ALL
    SELECT r.n,
           t.spent + iif(t.spent + r.cost <= r.can_spend AND t.lost + r.cost - r.flat <= r.can_lose, r.cost, 0),
           t.lost + iif(t.spent + r.cost <= r.can_spend AND t.lost + r.cost - r.flat <= r.can_lose, r.cost - r.flat, 0),
           t.spent + r.cost <= r.can_spend AND t.lost + r.cost - r.flat <= r.can_lose
    FROM took t JOIN ranked r ON r.n = t.n + 1)
SELECT row_number() OVER (ORDER BY n) - 1 AS ord, player, profit
FROM ranked JOIN took USING (n) WHERE taken;

-- Every line of the board, each once: what to do now ('do') and what to
-- consider ('consider'), each with the step it takes in the app and the
-- amount that step names, whether it is done, its points (over the season,
-- or next for the line-up) and the numbers that made it (facts: [label,
-- value, unit], none without a value).
--   do: the line-up the plan wants; the plan's moves in its order (a buy:
--   bid up to its bid, or yours is in; a sale: take tonight's offer, or
--   wait for what waiting is worth, or sell after the auctions when it pays
--   only with the plan's buys and waiting for them is worth more
--   (board_mine.wait_bids), or it is listed, or list it); the buys to
--   resell (board_resale); your players not listed, rising first: the game
--   offers only for those listed, listing costs nothing and a listed player
--   still plays for you; and those whose listing ends before the last
--   offer before the lock, to relist once it does.
--   consider: each buy that clears the bar alone but is not in the plan
--   (short by what the bid is over your cash after it), and each sale the
--   plan does not make of a listed player whose offer tonight beats what
--   waiting is worth and GOOD_OFFER of the game's recent offers.
DROP VIEW IF EXISTS board_moves;
DROP VIEW IF EXISTS board_rows;
CREATE VIEW board_rows AS
WITH ins AS (SELECT * FROM lineup_gap WHERE basis = 'plan' AND side = 'in'),
outs AS (SELECT * FROM lineup_gap WHERE basis = 'plan' AND side = 'out'),
legal AS (SELECT coalesce(max(legal), 0) AS ok FROM lineup_gap WHERE basis = 'plan'),
m AS (SELECT * FROM board_money),
field AS (
    SELECT 'do' AS section, -1 AS ord, 'field' AS what,
           'field ' || (SELECT group_concat(name, ', ' ORDER BY -next, player) FROM ins) AS label,
           NULL AS name, NULL AS pos,
           CASE WHEN legal.ok THEN NULL ELSE 'check line-up' END AS step, NULL AS amount,
           0 AS done,
           CASE WHEN legal.ok THEN (SELECT total(next) FROM ins) - (SELECT total(next) FROM outs) END AS gain,
           'next' AS per,
           (SELECT json_group_array(json_array('bench', bench, 'names')) FROM (
                SELECT group_concat(name, ', ' ORDER BY i) AS bench FROM outs
                WHERE player NOT IN (SELECT player FROM board_mine WHERE sell)) WHERE bench IS NOT NULL) AS facts
    FROM legal
    WHERE EXISTS (SELECT 1 FROM ins) OR EXISTS (SELECT 1 FROM outs)),
buys AS (
    SELECT CASE WHEN b.in_plan OR r.player IS NOT NULL THEN 'do' ELSE 'consider' END,
           coalesce(p.ord, 1000 + r.ord, b.rank), CASE WHEN r.player IS NULL THEN 'buy' ELSE 'resale' END,
           CASE WHEN r.player IS NULL THEN 'buy ' ELSE 'buy to resell ' END || b.name, b.name, b.pos,
           CASE WHEN b.my_bid IS NULL THEN 'bid up to' ELSE 'bid in' END, b.cost, b.my_bid IS NOT NULL,
           CASE WHEN r.player IS NULL THEN b.gain END, 'season',
           (SELECT json_group_array(json_array(l, v, u) ORDER BY n) FROM (
                SELECT 0 AS n, 'listed at' AS l, b.asking AS v, 'money' AS u
                UNION ALL SELECT 0.5, 'chance to win', round(b.win, 3), 'chance' WHERE b.my_bid IS NULL
                UNION ALL SELECT 1, 'sell before the lock for', b.resale, 'money' WHERE r.player IS NOT NULL
                UNION ALL SELECT 2, 'profit', r.profit, 'money'
                UNION ALL SELECT 3, 'chance of helping', round(b.p_better, 3), 'chance' WHERE r.player IS NULL
                UNION ALL SELECT 4, '', b.trend, 'trend'
                UNION ALL SELECT 5, 'money', 1e6 * b.money, 'money' WHERE r.player IS NULL
                UNION ALL SELECT 6, '', b.per_million, 'per_million' WHERE r.player IS NULL
                UNION ALL SELECT 7, 'short by', b.bid - max(m.left, 0.0), 'money'
                WHERE r.player IS NULL AND NOT b.in_plan AND b.bid > m.left)
            WHERE v IS NOT NULL)
    FROM board_buys b LEFT JOIN board_plan p ON p.rank = b.rank
    LEFT JOIN board_resale r USING (player), m
    WHERE b.in_plan OR b.verdict IS NULL OR r.player IS NOT NULL),
sells AS (
    SELECT CASE WHEN s.sell THEN 'do' ELSE 'consider' END, coalesce(p.ord, s.rank), 'sell',
           'sell ' || s.name, s.name, s.pos,
           CASE WHEN s.takes AND s.wait_bids > 0 THEN 'sell after the auctions'
                WHEN s.takes THEN 'take offer' WHEN s.offer IS NOT NULL THEN 'wait'
                WHEN s.listed THEN 'listed' ELSE 'list' END,
           CASE WHEN s.takes AND s.wait_bids > 0 THEN s.waiting WHEN s.offer IS NOT NULL THEN s.raises END,
           s.listed AND (NOT s.takes OR coalesce(s.wait_bids > 0, 0)), s.gain, 'season',
           (SELECT json_group_array(json_array(l, v, u) ORDER BY n) FROM (
                SELECT 0 AS n, 'value' AS l, s.value AS v, 'money' AS u
                UNION ALL SELECT 1, 'offer', CASE WHEN NOT s.takes THEN s.offer END, 'money'
                UNION ALL SELECT 2, 'the game''s recent offers', round(s.beats, 3), 'beats'
                UNION ALL SELECT 2.2, 'waiting is worth', s.waiting, 'money'
                    WHERE s.offer IS NOT NULL AND s.nights > 0
                UNION ALL SELECT 2.4, 'more offers before the lock', s.nights, 'count'
                    WHERE s.offer IS NOT NULL
                UNION ALL SELECT 2.6, 'only pays if you win', s.bids_names, 'names' WHERE s.with_bids
                UNION ALL SELECT 2.7, 'chance of winning them', round(s.bids_p, 3), 'chance' WHERE s.with_bids
                UNION ALL SELECT 2.8, 'waiting for them', s.wait_bids, 'points' WHERE s.takes
                UNION ALL SELECT 3, '', s.trend, 'trend'
                UNION ALL SELECT 4, 'money', 1e6 * s.money, 'money'
                UNION ALL SELECT 5, '', s.per_million, 'per_million')
            WHERE v IS NOT NULL)
    FROM board_mine s LEFT JOIN board_plan p ON p.rank = s.rank
    WHERE s.sell OR (s.listed AND s.takes AND s.alone
                     AND s.beats >= (SELECT good_offer FROM k))),
unlisted AS (
    SELECT 'do', 2000, 'list',
           'list ' || group_concat(name, ', ' ORDER BY -coalesce(trend, 0.0), player), NULL, NULL,
           NULL, NULL, 0, NULL, 'season',
           (SELECT json_group_array(json_array('rising', rising, 'names')) FROM (
                SELECT group_concat(name, ', ' ORDER BY -trend, player) AS rising
                FROM board_mine WHERE NOT listed AND NOT sell AND trend > 0) WHERE rising IS NOT NULL)
    FROM board_mine WHERE NOT listed AND NOT sell HAVING count(*) > 0),
relist AS (
    SELECT 'do', 1999, 'relist',
           'relist ' || group_concat(name, ', ' ORDER BY relist_after, -coalesce(trend, 0.0), player), NULL, NULL,
           NULL, NULL, 0, NULL, 'season',
           json_array(json_array('once the listing ends', min(relist_after), 'time'))
    FROM board_mine WHERE relist_after IS NOT NULL HAVING count(*) > 0)
SELECT section, row_number() OVER (PARTITION BY section ORDER BY ord) - 1 AS ord, what, label, name,
       pos, step, amount, done, gain, per, facts
FROM (SELECT * FROM field UNION ALL SELECT * FROM buys UNION ALL SELECT * FROM sells
      UNION ALL SELECT * FROM unlisted UNION ALL SELECT * FROM relist);

-- The board's headline: when the next match locks, your money, where you
-- are expected to finish and your chance of first, your season's points
-- band, what the plan gains and leaves you, what more you could bid after
-- it and your bids it does not make (into the debt the game allows, to
-- clear by selling before the lock), and what is still to do.
DROP VIEW IF EXISTS board_head;
CREATE VIEW board_head AS
WITH me AS (SELECT * FROM board_league WHERE me),
lock AS (SELECT kick FROM next_kickoff)
SELECT (SELECT run_id FROM board_run) AS run_id,
       substr(cut(), 1, 13) || ':' || substr(cut(), 14) AS generated_at,
       (SELECT substr(kick, 1, 13) || ':' || substr(kick, 14, 2) || ':00+00:00' FROM lock) AS lock_at,
       me.cash, (SELECT total(my_bid) FROM sim_facts JOIN board_run USING (run_id)) AS cash_locked,
       round(me.finish, 2) AS finish, round(me.p_first, 3) AS p_win,
       me.lo AS band_lo, me.hi AS band_hi,
       (SELECT plan_gain FROM sim_runs JOIN board_run USING (run_id)) AS plan_gain,
       CASE WHEN EXISTS (SELECT 1 FROM board_plan) THEN (SELECT left FROM board_money) END AS cash_after,
       (SELECT max(0.0, left + debt) - held FROM board_money) AS can_bid,
       (SELECT group_concat(label, '; ' ORDER BY ord) FROM board_rows
        WHERE section = 'do' AND NOT done) AS ping
FROM me;

-- Each plan the last run made (sim_plan_heads): what it gains, the cash it
-- leaves you at the lock and your chance of first once made, and its
-- moves in order (the steady plan's from the funnel, the others' from
-- sim_plans), each with its points and whether the other plan makes it too.
DROP VIEW IF EXISTS board_plans;
CREATE VIEW board_plans AS
WITH moves AS (
    SELECT 'steady' AS profile, p.ord, f.label, f.gain
    FROM sim_plan p JOIN board_run USING (run_id) JOIN funnel f ON f.run_id = p.run_id AND f.rank = p.rank
    UNION ALL
    SELECT a.profile, a.ord, a.label, a.gain FROM sim_plans a JOIN board_run USING (run_id))
SELECT h.profile, h.gain, h.cash_after, h.p_first,
       (SELECT json_group_array(json_object(
                   'label', m.label, 'gain', m.gain,
                   'shared', json(CASE WHEN EXISTS (SELECT 1 FROM moves o WHERE o.profile <> m.profile
                                                    AND o.label = m.label) THEN 'true' ELSE 'false' END))
               ORDER BY m.ord)
        FROM moves m WHERE m.profile = h.profile) AS moves
FROM sim_plan_heads h JOIN board_run USING (run_id);

-- decisions.json: the board as one JSON document, the phone's copy: its
-- lines (board_rows) by section, as the phone draws them.
DROP VIEW IF EXISTS board_json;
CREATE VIEW board_json AS
WITH lines AS (SELECT section, ord, json(CASE WHEN what = 'field' THEN json_remove(o, '$.name', '$.pos')
                                             ELSE o END) AS o
               FROM (SELECT section, ord, what, json_object(
                         'name', name, 'pos', pos, 'what', what, 'label', label, 'step', step,
                         'amount', amount, 'done', json(CASE WHEN done THEN 'true' ELSE 'false' END),
                         'gain', gain, 'per', per, 'facts', json(coalesce(facts, '[]'))) AS o
                     FROM board_rows))
SELECT json_object(
    'generated_at', h.generated_at, 'lock_at', h.lock_at,
    'cash', h.cash, 'cash_locked', h.cash_locked, 'finish', h.finish, 'p_win', h.p_win,
    'band', json_array(h.band_lo, h.band_hi),
    'do', (SELECT json_group_array(json(o) ORDER BY ord) FROM lines WHERE section = 'do'),
    'consider', (SELECT json_group_array(json(o) ORDER BY ord) FROM lines WHERE section = 'consider'),
    'plan_gain', h.plan_gain, 'cash_after', h.cash_after, 'can_bid', h.can_bid, 'ping', coalesce(h.ping, ''),
    'plans', (SELECT json_group_array(json_object('profile', profile, 'gain', gain, 'cash_after', cash_after,
                                                  'p_first', p_first, 'moves', json(moves))
                                      ORDER BY profile <> 'steady', profile) FROM board_plans),
    'exposed', (SELECT json_group_array(json_object('name', name, 'pos', pos, 'clause', clause)
                                        ORDER BY ord) FROM board_clauses),
    'standings', (SELECT json_group_array(json_object(
                      'manager', manager, 'me', json(CASE WHEN me THEN 'true' ELSE 'false' END),
                      'now', now, 'mean', mean, 'lo', lo, 'hi', hi, 'cash', cash,
                      'estimated', json(CASE WHEN estimated THEN 'true' ELSE 'false' END),
                      'p_above', p_above) ORDER BY ord) FROM board_league)) AS json
FROM board_head h;

-- The next jornada to lock after cut(): its first kickoff (round_locks).
DROP VIEW IF EXISTS next_round_lock;
CREATE VIEW next_round_lock AS SELECT min(lock) AS lock FROM round_locks WHERE lock > (SELECT cut());

-- A run to remind you of your line-up is due once the next jornada's lock is
-- within REMIND_HOURS, and only once: not if a run was asked for it
-- (trigger 'reminder', as_of the window's opening or later).
DROP VIEW IF EXISTS lock_due;
CREATE VIEW lock_due AS
WITH w AS (
    SELECT lock, strftime('%Y-%m-%dT%H%MZ', datetime(
               substr(lock, 1, 10) || ' ' || substr(lock, 12, 2) || ':' || substr(lock, 14, 2),
               '-' || (SELECT remind_hours FROM k) || ' hours')) AS opens
    FROM next_round_lock WHERE lock IS NOT NULL)
SELECT lock FROM w
WHERE opens <= (SELECT cut())
  AND NOT EXISTS (SELECT 1 FROM runs WHERE trigger = 'reminder' AND as_of >= w.opens);

-- What to tell you about your line-up before the next lock, from the last
-- run: set it, if the app's is not one I can match, or change it to your
-- best eleven. No row when the line-up set is that eleven.
DROP VIEW IF EXISTS lineup_alert;
CREATE VIEW lineup_alert AS
WITH g AS (SELECT * FROM lineup_gap WHERE basis = 'now'),
ins AS (SELECT group_concat(name, ', ' ORDER BY -next, player) AS names FROM g WHERE side = 'in'),
outs AS (SELECT group_concat(name, ', ' ORDER BY i) AS names FROM g WHERE side = 'out'),
left AS (SELECT CAST(round((
             julianday(substr(lock, 1, 10) || 'T' || substr(lock, 12, 2) || ':' || substr(lock, 14, 2) || 'Z')
             - julianday(substr(cut(), 1, 10) || 'T' || substr(cut(), 12, 2) || ':' || substr(cut(), 14, 2) || 'Z')
         ) * 1440) AS INTEGER) AS minutes FROM next_round_lock)
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
