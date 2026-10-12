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
    (SELECT value FROM constants WHERE name = 'BID_BEATS') AS bid_beats,
    (SELECT value FROM constants WHERE name = 'MATCH_LEN') AS match_len,
    (SELECT value FROM constants WHERE name = 'NEUTRAL_START') AS neutral_start,
    (SELECT value FROM constants WHERE name = 'ABSENT_START') AS absent_start,
    (SELECT value FROM constants WHERE name = 'FALLBACK_K') AS fallback_k;

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
