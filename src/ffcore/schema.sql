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

-- The names every other table's player key stands for, as of each run.
CREATE TABLE IF NOT EXISTS players (
    run_id INTEGER NOT NULL REFERENCES runs(run_id),
    player TEXT NOT NULL,
    name   TEXT NOT NULL,
    pos    TEXT,
    owner  TEXT,                 -- manager, or NULL
    PRIMARY KEY (run_id, player)
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
-- read as of cut()). League.load writes the two inputs no table holds: the
-- crosswalk's app id -> player key (its clash rule applied) and the config.
CREATE TABLE IF NOT EXISTS app_keys (
    app_id TEXT PRIMARY KEY,
    player TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS league_config (
    me     TEXT NOT NULL,
    budget REAL NOT NULL
);

DROP VIEW IF EXISTS users;
CREATE VIEW users AS
SELECT trim(user_id) AS user_id, trim(manager) AS manager FROM current_api_standings
WHERE trim(user_id) <> '' AND trim(manager) <> '' GROUP BY trim(user_id);

-- Who owns each player now.
DROP VIEW IF EXISTS owners;
CREATE VIEW owners AS
SELECT k.player, trim(t.manager) AS manager
FROM current_api_teams t JOIN app_keys k ON k.app_id = trim(t.player_id)
WHERE trim(t.manager) <> '' GROUP BY k.player;

-- The activity feed, each event once, with who paid (buyer: a buy's or a
-- clause's manager) and who was paid (seller: a sale's manager, a clause's
-- other); then the exits the squads show beyond those the feed records (a
-- sale by him, a clause paid to him), as sales at the player's last value.
-- Counted, not matched by time: squads and feed are stamped minutes apart.
DROP VIEW IF EXISTS ledger;
CREATE VIEW ledger AS
WITH feed AS (
    SELECT coalesce(a.at, '') AS at, coalesce(a.kind, '') AS kind,
           u.manager AS who, c.manager AS other, k.player, coalesce(a.amount, 0.0) AS amount
    FROM current_api_activity a
    LEFT JOIN users u ON u.user_id = trim(a.user_id)
    LEFT JOIN users c ON c.user_id = trim(a.counterparty)
    LEFT JOIN app_keys k ON k.app_id = trim(a.player_id)),
roles AS (
    SELECT *, CASE WHEN kind IN ('buy', 'transfer') THEN who END AS buyer,
              CASE kind WHEN 'sell' THEN who WHEN 'transfer' THEN other END AS seller
    FROM feed),
t AS MATERIALIZED (
    SELECT h.observed_at, k.player, trim(h.manager) AS manager,
           coalesce(h.market_value, 0.0) AS value
    FROM history_api_teams h JOIN app_keys k ON k.app_id = trim(h.player_id)
    WHERE trim(h.manager) <> ''),
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
         WHERE trim(s.manager) = cfg.me AND s.team_money <> 0 LIMIT 1),
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
