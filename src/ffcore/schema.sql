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

-- The board as decisions.json has it (sim.report), one run each: the
-- headline in boards, each list a row an entry. Names, not keys: the
-- board is what the phone shows.
CREATE TABLE IF NOT EXISTS boards (
    run_id       INTEGER PRIMARY KEY REFERENCES runs(run_id),
    generated_at TEXT NOT NULL,
    lock_at      TEXT,
    cash         REAL,
    cash_locked  REAL,
    finish       REAL,             -- expected final position
    p_win        REAL,
    band_lo      REAL,             -- your season points, 10th and 90th percentile
    band_hi      REAL,
    plan_gain    REAL,
    cash_after   REAL,
    ping         TEXT
);

CREATE TABLE IF NOT EXISTS board_do (
    run_id INTEGER NOT NULL REFERENCES runs(run_id),
    ord    INTEGER NOT NULL,
    what   TEXT NOT NULL,          -- field, buy, sell
    label  TEXT NOT NULL,
    name   TEXT,
    pos    TEXT,
    step   TEXT,                   -- what to do in the app
    amount REAL,
    done   INTEGER NOT NULL,
    gain   REAL,
    per    TEXT,
    facts  TEXT NOT NULL,          -- JSON: [name, value, unit]
    PRIMARY KEY (run_id, ord)
);

CREATE TABLE IF NOT EXISTS board_squad (
    run_id      INTEGER NOT NULL REFERENCES runs(run_id),
    ord         INTEGER NOT NULL,
    name        TEXT NOT NULL,
    pos         TEXT,
    xi          INTEGER,
    start       REAL,
    next        REAL,
    season      REAL,
    value       REAL,
    trend       REAL,
    paid        REAL,
    per_million REAL,
    offer       REAL,
    PRIMARY KEY (run_id, ord)
);

CREATE TABLE IF NOT EXISTS board_standings (
    run_id    INTEGER NOT NULL REFERENCES runs(run_id),
    ord       INTEGER NOT NULL,
    manager   TEXT NOT NULL,
    me        INTEGER NOT NULL,
    now       REAL,
    mean      REAL,
    lo        REAL,
    hi        REAL,
    cash      REAL,
    estimated INTEGER,
    p_above   REAL,
    PRIMARY KEY (run_id, ord)
);

CREATE TABLE IF NOT EXISTS board_exposed (
    run_id INTEGER NOT NULL REFERENCES runs(run_id),
    ord    INTEGER NOT NULL,
    name   TEXT NOT NULL,
    pos    TEXT,
    clause REAL,
    PRIMARY KEY (run_id, ord)
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

-- The market as it closed each day: the rows of each day's last snapshot
-- (each snapshot holds the whole market). Values move once a day; the
-- change from one day's to the next is a step.
DROP VIEW IF EXISTS market_daily;
CREATE VIEW market_daily AS
SELECT *, rowid AS id FROM market
WHERE observed_at IN (SELECT max(observed_at) FROM market GROUP BY substr(observed_at, 1, 10));

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

-- The constants a run is built with (assemble.CONSTANTS), for the views.
CREATE TABLE IF NOT EXISTS constants (
    name  TEXT PRIMARY KEY,
    value REAL NOT NULL
);

-- The constants the views use, one row: each named once, here.
DROP VIEW IF EXISTS k;
CREATE VIEW k AS SELECT
    (SELECT value FROM constants WHERE name = 'CLIP') AS clip,
    (SELECT value FROM constants WHERE name = 'PRICE_WINDOW') AS price_window,
    (SELECT value FROM constants WHERE name = 'BID_BEATS') AS bid_beats;

-- Each player's changes, all time, numbered (i), clipped at CLIP (x: so a
-- long rise is not cut short), with the value each ends at.
DROP VIEW IF EXISTS price_series;
CREATE VIEW price_series AS
SELECT ff_id, observed_at, substr(observed_at, 1, 10) AS day, step,
       max(-k.clip, min(k.clip, step)) AS x, i, value
FROM (SELECT *, row_number() OVER (PARTITION BY ff_id ORDER BY observed_at) - 1 AS i
      FROM price_obs WHERE step IS NOT NULL), k;

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
