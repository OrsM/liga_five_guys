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
-- Why the plan left a move out costs a joint simulation: ask.py why.
CREATE TABLE IF NOT EXISTS funnel (
    run_id      INTEGER NOT NULL REFERENCES runs(run_id),
    rank        INTEGER NOT NULL,
    kind        TEXT NOT NULL,     -- buy, sell, swap
    buy         TEXT,              -- player key
    sell        TEXT,              -- player keys, comma-separated
    label       TEXT NOT NULL,     -- Action.label
    cost        REAL NOT NULL,
    proceeds    REAL NOT NULL,
    gain        REAL NOT NULL,     -- median season points against doing nothing
    p_better    REAL NOT NULL,     -- share of seasons better off
    per_million REAL NOT NULL,
    verdict     TEXT,              -- why it fails the bar; NULL if it clears it
    in_plan     INTEGER NOT NULL,
    PRIMARY KEY (run_id, rank)
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
    PRIMARY KEY (run_id, from_j, jornada, player)
);

-- The forecast of the next jornada replayed at every line-up snapshot
-- (grading.replay), for one variant of the model: how far from the lock the
-- error is acceptable. The same outcomes as grade_forecast, forecast again
-- under the information of each moment, so it says how early a forecast is
-- good, not which model is (paired_gain).
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

-- The phantom manager (grading.phantom_season): the points of his eleven
-- each jornada, beside the same squad never moved, and what he did.
CREATE TABLE IF NOT EXISTS phantom_jornadas (
    run_id   INTEGER NOT NULL REFERENCES runs(run_id),
    jornada  INTEGER NOT NULL,
    phantom  REAL NOT NULL,
    standpat REAL NOT NULL,
    moves    TEXT NOT NULL,
    cash     REAL NOT NULL,
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
    chance     REAL,               -- share of seasons better off
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
-- flagged then.
DROP VIEW IF EXISTS grade_error;
CREATE VIEW grade_error AS
WITH ranked AS (
    SELECT *, row_number() OVER (PARTITION BY run_id, from_j, jornada
                                 ORDER BY predicted DESC, player) AS rk
    FROM grade_forecast)
SELECT run_id, from_j, jornada, jornada - from_j AS ahead, count(*) AS n,
       sqrt(avg((predicted - actual) * (predicted - actual))) AS rmse,
       avg(predicted - actual) AS bias,
       avg(CASE WHEN rk <= 50 THEN actual END) AS top50,
       sum(flagged) AS flagged_n,
       sqrt(avg(CASE WHEN flagged = 1 THEN (predicted - actual) * (predicted - actual) END))
           AS flagged_rmse,
       avg(CASE WHEN flagged = 1 THEN predicted - actual END) AS flagged_bias
FROM ranked GROUP BY run_id, from_j, jornada;

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
d AS (SELECT f.variant, b.from_h, b.lead, f.flagged,
             (f.predicted - f.actual) * (f.predicted - f.actual)
           - (g.predicted - g.actual) * (g.predicted - g.actual) AS d
      FROM replay_forecast f JOIN fin ON fin.variant = f.variant AND fin.jornada = f.jornada
      JOIN replay_forecast g ON g.variant = f.variant AND g.jornada = f.jornada
                            AND g.as_of = fin.at AND g.player = f.player
      JOIN lead_buckets b ON f.lead_h >= b.from_h AND f.lead_h < b.to_h
      WHERE f.as_of < fin.at),
s AS (SELECT variant, from_h, lead, 'all' AS scope, count(*) AS n, avg(d) AS m,
             (sum(d * d) - count(*) * avg(d) * avg(d)) / (count(*) - 1) AS var
      FROM d GROUP BY variant, from_h, lead
      UNION ALL
      SELECT variant, from_h, lead, 'flagged', count(*), avg(d),
             (sum(d * d) - count(*) * avg(d) * avg(d)) / (count(*) - 1)
      FROM d WHERE flagged = 1 GROUP BY variant, from_h, lead),
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
flow AS (
    SELECT u.manager, cfg.budget
           - coalesce((SELECT sum(amount) FROM ledger WHERE buyer = u.manager), 0.0)
           + coalesce((SELECT sum(amount) FROM ledger WHERE seller = u.manager), 0.0)
           + coalesce((SELECT sum(amount) FROM ledger WHERE who = u.manager AND kind = 'bonus'), 0.0)
           AS feed
    FROM (SELECT DISTINCT manager FROM users) u, cfg),
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
-- match's kickoff (NULL once none is left to play).
DROP VIEW IF EXISTS next_kickoff;
CREATE VIEW next_kickoff AS SELECT min(kick) AS kick FROM kickoffs WHERE kick > (SELECT cut());

-- The game's nightly offers still to come before the next match locks: one
-- as each standing offer expires, then one a day (0 with none standing).
DROP VIEW IF EXISTS nights_left;
CREATE VIEW nights_left AS
WITH e AS (SELECT max(julianday(expires_at)) AS ends FROM current_api_offers
           WHERE status = 'pending' AND expires_at <> ''),
l AS (SELECT julianday(substr(kick, 1, 13) || ':' || substr(kick, 14, 2) || 'Z') AS lock
      FROM next_kickoff)
SELECT CASE WHEN ends IS NOT NULL AND lock > ends THEN CAST(lock - ends AS INTEGER) + 1 ELSE 0 END AS nights
FROM e, l;

-- What selling a player with an offer standing on him gets: the offer, or
-- what turning it down is worth if each night's offer to come is then
-- taken only when it beats waiting on (the last before the lock always),
-- whichever is more. An offer to come is a past one's share of value
-- (offer_ratios, the last PRICE_WINDOW) of the value he will have that
-- night: his value moved by his trend, the share carry says is made by
-- then (price_carry). Waiting is NULL without a value or past offers.
DROP VIEW IF EXISTS sale_odds;
CREATE VIEW sale_odds AS
WITH RECURSIVE
r AS MATERIALIZED (SELECT ratio FROM offer_ratios ORDER BY created_at DESC, ratio DESC
                   LIMIT (SELECT CAST(price_window AS INTEGER) FROM k)),
c AS MATERIALIZED (SELECT h, carry FROM price_carry),
s AS MATERIALIZED (SELECT player, offer, value, coalesce(trend, 0.0) AS trend
                   FROM player_facts WHERE offer IS NOT NULL),
w(player, night, later) AS (
    SELECT player, (SELECT nights FROM nights_left), 0.0 FROM s
    WHERE value IS NOT NULL AND EXISTS (SELECT 1 FROM r)
  UNION ALL
    SELECT w.player, w.night - 1,
           (SELECT avg(max(r.ratio * s.value * (1 + s.trend / 100 * coalesce(
                    (SELECT carry FROM c WHERE h = min(w.night, (SELECT max(h) FROM c))), 1.0)),
                    w.later)) FROM r)
    FROM w JOIN s USING (player) WHERE w.night > 0)
SELECT s.player, s.offer, w.later AS waiting, max(s.offer, coalesce(w.later, 0.0)) AS fetches
FROM s LEFT JOIN w ON w.player = s.player AND w.night = 0;

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

-- The market as it closed each day: the rows of each day's last snapshot
-- (each snapshot holds the whole market). Values move once a day; the
-- change from one day's to the next is a step. As of cut(): today's row is
-- the last snapshot up to it, as the run at that moment saw it.
DROP VIEW IF EXISTS market_daily;
CREATE VIEW market_daily AS
SELECT *, rowid AS id FROM market
WHERE observed_at IN (SELECT max(observed_at) FROM market WHERE observed_at <= (SELECT cut())
                      GROUP BY substr(observed_at, 1, 10));

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
    (SELECT value FROM model_constants WHERE name = 'BID_BEATS' AND variant = (SELECT variant())) AS bid_beats,
    (SELECT value FROM model_constants WHERE name = 'PRICE_W0' AND variant = (SELECT variant())) AS price_w0,
    (SELECT value FROM model_constants WHERE name = 'PRICE_W1' AND variant = (SELECT variant())) AS price_w1,
    (SELECT value FROM model_constants WHERE name = 'PRICE_W2' AND variant = (SELECT variant())) AS price_w2,
    (SELECT value FROM model_constants WHERE name = 'MATCH_LEN' AND variant = (SELECT variant())) AS match_len,
    (SELECT value FROM model_constants WHERE name = 'NEUTRAL_START' AND variant = (SELECT variant())) AS neutral_start,
    (SELECT value FROM model_constants WHERE name = 'ABSENT_START' AND variant = (SELECT variant())) AS absent_start,
    (SELECT value FROM model_constants WHERE name = 'FALLBACK_K' AND variant = (SELECT variant())) AS fallback_k,
    (SELECT value FROM model_constants WHERE name = 'REMIND_HOURS' AND variant = (SELECT variant())) AS remind_hours;

-- Each player's changes, all time, numbered (i), each clipped at CLIP; x is
-- the signal momentum fits on: his last three clipped changes weighted
-- PRICE_W0..2 (a missing one counts as the oldest he has), with the value each ends at.
DROP VIEW IF EXISTS price_series;
CREATE VIEW price_series AS
SELECT ff_id, observed_at, substr(observed_at, 1, 10) AS day, step,
       price_w0 * c + price_w1 * coalesce(lag(c, 1) OVER p, c)
                    + price_w2 * coalesce(lag(c, 2) OVER p, coalesce(lag(c, 1) OVER p, c)) AS x, i, value
FROM (SELECT *, max(-k.clip, min(k.clip, step)) AS c,
             row_number() OVER (PARTITION BY ff_id ORDER BY observed_at) - 1 AS i
      FROM price_obs, k WHERE step IS NOT NULL)
WINDOW p AS (PARTITION BY ff_id ORDER BY observed_at);

-- The longest horizon momentum fits, as of cut(): a quarter of the median
-- player's number of changes (players with none count), at least 1.
DROP VIEW IF EXISTS price_hmax;
CREATE VIEW price_hmax AS
WITH lens AS (SELECT count(step) AS n FROM price_obs WHERE observed_at <= (SELECT cut()) GROUP BY ff_id),
ranked AS (SELECT n, row_number() OVER (ORDER BY n) AS r, count(*) OVER () AS m FROM lens)
SELECT coalesce((SELECT max(1, CAST(avg(n) AS INTEGER) / 4) FROM ranked
                 WHERE r IN ((m + 1) / 2, (m + 2) / 2)), 1) AS hmax;

-- A player's change in % over the h changes after his i-th (all time, h up
-- to price_hmax): what momentum fits, the backtest scores and a replay
-- looks back on. The changes compound to the ratio of the values (no value
-- is ever 0: the game's floor is 300k).
DROP VIEW IF EXISTS price_ahead;
CREATE VIEW price_ahead AS
WITH RECURSIVE
s AS MATERIALIZED (SELECT * FROM price_series),
hs(h) AS (SELECT 1 UNION ALL SELECT h + 1 FROM hs WHERE h < (SELECT hmax FROM price_hmax))
SELECT a.ff_id, a.i, a.day, a.x, hs.h, b.observed_at AS end_at,
       substr(b.observed_at, 1, 10) AS end_day, 100 * (b.value / a.value - 1) AS change
FROM s a JOIN hs JOIN s b ON b.ff_id = a.ff_id AND b.i = a.i + hs.h;

-- Momentum, as of cut(): how much of a price change carries into the next
-- h (up to price_hmax), the move over h clipped at CLIP an update.
DROP VIEW IF EXISTS momentum_pairs;
CREATE VIEW momentum_pairs AS
SELECT p.h, p.ff_id, p.i, p.end_day, p.x,
       max(-k.clip * p.h, min(k.clip * p.h, p.change)) AS y
FROM price_ahead p, k WHERE p.end_at <= (SELECT cut());

-- One least-squares slope through the origin per horizon.
DROP VIEW IF EXISTS momentum;
CREATE VIEW momentum AS
SELECT h, sum(x * y) / sum(x * x) AS slope FROM momentum_pairs
GROUP BY h HAVING sum(x * x) <> 0;

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
-- change is the newest day's: as long as you would hold him.
DROP VIEW IF EXISTS price_trend;
CREATE VIEW price_trend AS
WITH s AS (SELECT coalesce((SELECT slope FROM fitted ORDER BY h DESC LIMIT 1), 0.0) AS slope),
last AS (SELECT ff_id, day, x FROM (
             SELECT ff_id, day, x, row_number() OVER (PARTITION BY ff_id ORDER BY i DESC) AS r
             FROM price_series WHERE observed_at <= (SELECT cut()))
         WHERE r = 1)
SELECT last.ff_id, s.slope * last.x AS trend
FROM last, s WHERE last.day = (SELECT max(day) FROM last);

-- Your own winning bids over the asking price: each of your buys, oldest
-- first, set against the league listing of that player that closed within
-- ten minutes of it (its last sighting; the first such listing seen).
DROP VIEW IF EXISTS auction_ratios;
CREATE VIEW auction_ratios AS
WITH mine AS (SELECT DISTINCT s.user_id AS uid FROM current_api_standings s, league_config c
              WHERE s.manager = c.me),
buys AS (SELECT a.at, a.player_id AS pid, a.amount,
                row_number() OVER (ORDER BY a.at) AS n
         FROM current_api_activity a WHERE a.kind = 'buy' AND a.user_id IN (SELECT uid FROM mine)),
ends AS (SELECT pid, close, sale_price, first_id FROM (
             SELECT player_id AS pid, julianday(expires_at) AS close, sale_price,
                    row_number() OVER k AS pick,
                    min(rowid) OVER (PARTITION BY player_id, julianday(expires_at)) AS first_id
             FROM api_market
             WHERE observed_at <= (SELECT cut()) AND seller = 'marketPlayerLeague'
               AND coalesce(expires_at, '') <> ''
             WINDOW k AS (PARTITION BY player_id, julianday(expires_at)
                          ORDER BY observed_at DESC, rowid))
         WHERE pick = 1),
matched AS (SELECT b.n, b.amount / e.sale_price AS ratio,
                   row_number() OVER (PARTITION BY b.n ORDER BY e.first_id) AS k
            FROM buys b JOIN ends e ON e.pid = b.pid
             AND abs(julianday(b.at) - e.close) * 86400 < 600 AND coalesce(e.sale_price, 0) > 0)
SELECT n, ratio FROM matched WHERE k = 1 ORDER BY n;

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

-- The bid over asking at or above BID_BEATS of your last PRICE_WINDOW
-- winning bids (1.0 with none).
DROP VIEW IF EXISTS premium;
CREATE VIEW premium AS
WITH recent AS (SELECT ratio FROM auction_ratios ORDER BY n DESC
                LIMIT (SELECT CAST(price_window AS INTEGER) FROM k)),
r AS (SELECT ratio, row_number() OVER (ORDER BY ratio) - 1 AS i, count(*) OVER () AS cnt
      FROM recent)
SELECT coalesce((SELECT ratio FROM r, k WHERE i = min(cnt - 1, CAST(k.bid_beats * cnt AS INTEGER))),
                1.0) AS premium;

-- The jornada calendar, as of cut(). Each fixture's kickoff as last seen
-- (a played match's page drops it) and its jornada, as a UTC stamp (as
-- observed_at is).
DROP VIEW IF EXISTS kickoffs;
CREATE VIEW kickoffs AS
SELECT home, away, jornada, kick FROM (
    SELECT home, away, jornada, strftime('%Y-%m-%dT%H%MZ', kickoff) AS kick,
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

-- When each jornada locks: its first kickoff. None for a jornada with a
-- blind result (its first kickoff is not known).
DROP VIEW IF EXISTS round_locks;
CREATE VIEW round_locks AS
SELECT jornada, min(kick) AS lock FROM kickoffs
WHERE jornada NOT IN (SELECT jornada FROM blind_results)
GROUP BY jornada;

-- When each jornada's next match still to play kicks off (a postponed
-- match dates the rest of its jornada): what the season ahead is
-- ordered and dated by.
DROP VIEW IF EXISTS rounds_ahead;
CREATE VIEW rounds_ahead AS
SELECT k.jornada, min(k.kick) AS next FROM kickoffs k
WHERE NOT EXISTS (SELECT 1 FROM current_matches m
                  WHERE m.home = k.home AND m.away = k.away AND m.score <> '')
GROUP BY k.jornada;

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

-- Your squad: goalkeepers to forwards, then most expected next first.
DROP VIEW IF EXISTS board_holdings;
CREATE VIEW board_holdings AS
SELECT row_number() OVER (ORDER BY CASE pos WHEN 'POR' THEN 0 WHEN 'DEF' THEN 1
           WHEN 'MED' THEN 2 WHEN 'DEL' THEN 3 ELSE 9 END, -next, player) - 1 AS ord,
       name, pos, xi, start, next, season, value, trend, paid, per_million, offer
FROM board_players WHERE mine;

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

-- The plan's moves, in its order, each as a line of the board: the step it
-- takes in the app and the amount that step names, whether it is done, its
-- points over the season and the numbers that made it (facts: [label,
-- value, unit], none without a value). A buy: bid up to its cost, or your
-- bid is in. A sale: take tonight's offer, or wait for what waiting is
-- worth (board_players.raises), or it is listed, or list it.
DROP VIEW IF EXISTS board_plan;
CREATE VIEW board_plan AS
SELECT p.ord, f.label, f.gain, f.per_million, f.p_better, f.cost,
       CASE WHEN f.buy IS NOT NULL THEN 'buy' ELSE 'sell' END AS what,
       coalesce(f.buy, substr(f.sell || ',', 1, instr(f.sell || ',', ',') - 1)) AS player,
       f.buy, f.sell, f.cost - f.proceeds AS net
FROM sim_plan p JOIN board_run USING (run_id)
JOIN funnel f ON f.run_id = p.run_id AND f.rank = p.rank;

DROP VIEW IF EXISTS board_moves;
CREATE VIEW board_moves AS
WITH sold AS (SELECT b.player FROM board_players b JOIN board_plan p
              ON instr(',' || p.sell || ',', ',' || b.player || ',') > 0),
ins AS (SELECT * FROM lineup_gap WHERE basis = 'plan' AND side = 'in'),
outs AS (SELECT * FROM lineup_gap WHERE basis = 'plan' AND side = 'out'),
legal AS (SELECT coalesce(max(legal), 0) AS ok FROM lineup_gap WHERE basis = 'plan'),
field AS (
    SELECT -1 AS ord, 'field' AS what,
           'field ' || (SELECT group_concat(name, ', ' ORDER BY -next, player) FROM ins) AS label,
           NULL AS name, NULL AS pos,
           CASE WHEN legal.ok THEN NULL ELSE 'check line-up' END AS step, NULL AS amount,
           0 AS done,
           CASE WHEN legal.ok THEN (SELECT total(next) FROM ins) - (SELECT total(next) FROM outs) END AS gain,
           'next' AS per,
           (SELECT json_group_array(json_array('bench', bench, 'names')) FROM (
                SELECT group_concat(name, ', ' ORDER BY i) AS bench FROM outs
                WHERE player NOT IN (SELECT player FROM sold)) WHERE bench IS NOT NULL) AS facts
    FROM legal
    WHERE EXISTS (SELECT 1 FROM ins) OR EXISTS (SELECT 1 FROM outs)),
moves AS (
    SELECT p.ord, p.what, p.label, b.name, b.pos,
           CASE WHEN p.what = 'buy' THEN CASE WHEN b.my_bid IS NULL THEN 'bid up to' ELSE 'bid in' END
                WHEN b.takes THEN 'take offer' WHEN b.offer IS NOT NULL THEN 'wait'
                WHEN b.route = 'listed' THEN 'listed' ELSE 'list' END AS step,
           CASE WHEN p.what = 'buy' THEN coalesce(b.my_bid, p.cost)
                WHEN b.offer IS NOT NULL THEN b.raises END AS amount,
           CASE WHEN p.what = 'buy' THEN b.my_bid IS NOT NULL
                ELSE b.route IS 'listed' AND NOT b.takes END AS done,
           p.gain, 'season' AS per,
           (SELECT json_group_array(json_array(l, v, u) ORDER BY n) FROM (
                SELECT 0 AS n, 'asking' AS l, coalesce(b.price, p.cost) AS v, 'money' AS u
                WHERE p.what = 'buy'
                UNION ALL SELECT 1, '', p.per_million, 'per_million' WHERE p.what = 'buy'
                UNION ALL SELECT 2, 'better off in', round(p.p_better, 3), 'seasons' WHERE p.what = 'buy'
                UNION ALL SELECT 3, '', b.trend, 'trend' WHERE p.what = 'buy'
                UNION ALL SELECT 0, 'raises', b.raises, 'money' WHERE p.what = 'sell'
                UNION ALL SELECT 1, '', p.per_million, 'per_million' WHERE p.what = 'sell'
                UNION ALL SELECT 2, 'offer', CASE WHEN NOT b.takes THEN b.offer END, 'money'
                WHERE p.what = 'sell')
            WHERE v IS NOT NULL) AS facts
    FROM board_plan p LEFT JOIN board_players b USING (player))
SELECT row_number() OVER (ORDER BY ord) - 1 AS ord, what, label, name, pos, step, amount, done,
       gain, per, facts
FROM (SELECT * FROM field UNION ALL SELECT * FROM moves);

-- The board's headline: when the next match locks, your money, where you
-- are expected to finish and your chance of first, your season's points
-- band, what the plan gains and leaves you, and its moves not yet done.
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
       CASE WHEN EXISTS (SELECT 1 FROM board_plan)
            THEN me.cash - (SELECT total(net) FROM board_plan) END AS cash_after,
       (SELECT group_concat(label, '; ' ORDER BY ord) FROM board_moves WHERE NOT done) AS ping
FROM me;

-- decisions.json: the board as one JSON document, the phone's copy.
DROP VIEW IF EXISTS board_json;
CREATE VIEW board_json AS
SELECT json_object(
    'generated_at', h.generated_at, 'lock_at', h.lock_at,
    'cash', h.cash, 'cash_locked', h.cash_locked, 'finish', h.finish, 'p_win', h.p_win,
    'band', json_array(h.band_lo, h.band_hi),
    'do', (SELECT json_group_array(json(CASE WHEN what = 'field'
                                             THEN json_remove(o, '$.name', '$.pos') ELSE o END)
                                   ORDER BY ord)
           FROM (SELECT ord, what, json_object(
                     'name', name, 'pos', pos, 'what', what, 'label', label, 'step', step,
                     'amount', amount, 'done', json(CASE WHEN done THEN 'true' ELSE 'false' END),
                     'gain', gain, 'per', per, 'facts', json(coalesce(facts, '[]'))) AS o
                 FROM board_moves)),
    'plan_gain', h.plan_gain, 'cash_after', h.cash_after, 'ping', coalesce(h.ping, ''),
    'exposed', (SELECT json_group_array(json_object('name', name, 'pos', pos, 'clause', clause)
                                        ORDER BY ord) FROM board_clauses),
    'squad', (SELECT json_group_array(json_object(
                  'name', name, 'pos', pos, 'xi', json(CASE WHEN xi THEN 'true' ELSE 'false' END),
                  'start', start, 'next', next, 'season', season, 'value', value,
                  'trend', trend, 'paid', paid, 'per_million', per_million, 'offer', offer)
                  ORDER BY ord) FROM board_holdings),
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
