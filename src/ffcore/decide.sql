-- The decision (docs/notes/plan.md; the simplify map, step 3), every number
-- in points, each a measured fact, a game rule, or the backtest's to settle:
--
--   lam           what a million is worth: what a million more in value
--                 adds to a player's points a game (players with 3+ games),
--                 for each jornada left (money_rate)
--   f(S)          the points of squad S's best legal eleven, summed over
--                 the jornadas left, each player at his forecast cell times
--                 whether he is yours by then (dec_xp)
--   a buy p at b  win(b) x (f(S + p) - f(S) - lam x (b - value_p) / 1M),
--                 value his once his price trend has run (pricing's fits)
--   a sale q      f(S - q) - f(S) + lam x (proceeds_q - value_q) / 1M
--   a swap        a buy with the sale that pays for it, its points on the
--                 squad they make together
--
-- The plan: the best move worth more than nothing, added, and again (each
-- move valued on the squad and cash the plan has made so far), while the
-- squad stays within SQUAD_MAX counting every bid won, cash at the lock
-- stays at zero or more, and bids within cash and the game's debt. In debt
-- (a bid won and its sale not made), the least bad sale, and again, until
-- clear. src/decision.py writes the inputs and adds the moves.

-- The inputs, one decision's (decision.py inputs): the players it can
-- move, yours (mine) and the market's (asking), his position, his value
-- (money he is worth) and what a sale of him gets; each one's expected
-- points a jornada left as yours; each buy's bids and their chance of
-- winning; your cash, squad value, the money rate, the game's debt share
-- and squad cap; the formations' bounds.
CREATE TABLE IF NOT EXISTS dec_player (
    player   TEXT PRIMARY KEY,
    pos      TEXT NOT NULL,
    mine     INTEGER NOT NULL,
    value    REAL NOT NULL,          -- his value once his price trend has run
    asking   REAL,
    proceeds REAL
);
CREATE TABLE IF NOT EXISTS dec_xp (
    player  TEXT NOT NULL,
    jornada INTEGER NOT NULL,
    xp      REAL NOT NULL,
    PRIMARY KEY (player, jornada)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS dec_odds (
    player TEXT NOT NULL,
    ratio  REAL NOT NULL,
    p_win  REAL NOT NULL,
    PRIMARY KEY (player, ratio)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS dec_money (
    cash       REAL NOT NULL,
    team_value REAL NOT NULL,
    lam        REAL NOT NULL,
    debt       REAL NOT NULL,
    squad_max  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS dec_shape (
    pos TEXT PRIMARY KEY,
    lo  INTEGER NOT NULL,
    hi  INTEGER NOT NULL
);
-- The moves the plan has made so far, in order (a swap is a buy and a sale
-- of one step).
CREATE TABLE IF NOT EXISTS dec_plan (
    step   INTEGER NOT NULL,
    kind   TEXT NOT NULL,            -- buy, sell
    player TEXT NOT NULL,
    amount REAL NOT NULL,            -- the bid, or what the sale gets
    win    REAL NOT NULL,
    worth  REAL NOT NULL,            -- the step's, in points
    PRIMARY KEY (step, kind)
);

-- What a million is worth, in points: per_game, the points a game a
-- million more in value adds (the least-squares slope of points a game on
-- value, players with 3+ games), times the jornadas left. Not mean points
-- over mean value: a player worth nothing still scores about 3 a game, and
-- that ratio priced a million 4 times over (2026-10-31: 0.255 vs 0.060).
DROP VIEW IF EXISTS money_rate;
CREATE VIEW money_rate AS
WITH x AS (SELECT s.pts * 1.0 / s.apps AS y, f.value / 1e6 AS v
           FROM season_record s JOIN current_market f ON f.ff_id = s.player
           WHERE s.apps >= 3 AND f.value > 0),
r AS (SELECT (count(*) * sum(v * y) - sum(v) * sum(y)) / (count(*) * sum(v * v) - sum(v) * sum(v)) AS per_game
      FROM x),
j AS (SELECT count(*) AS left FROM jornada_calendar WHERE state <> 'done')
SELECT r.per_game, j.left AS jornadas, r.per_game * j.left AS lam FROM r, j;

-- The squad once the plan's moves are made, every bid won.
DROP VIEW IF EXISTS dec_squad;
CREATE VIEW dec_squad AS
SELECT player, pos FROM dec_player
WHERE mine AND player NOT IN (SELECT player FROM dec_plan WHERE kind = 'sell')
UNION ALL
SELECT p.player, p.pos FROM dec_plan d JOIN dec_player p USING (player) WHERE d.kind = 'buy';

-- Cash at the lock once the plan's moves are made, every bid won; the bids
-- it places.
DROP VIEW IF EXISTS dec_cash;
CREATE VIEW dec_cash AS
SELECT m.cash - coalesce((SELECT total(CASE kind WHEN 'buy' THEN amount ELSE -amount END) FROM dec_plan), 0) AS free,
       coalesce((SELECT total(amount) FROM dec_plan WHERE kind = 'buy'), 0) AS bids
FROM dec_money m;

-- Each squad a next move would make, by the buy (b) and the sale (s) it
-- holds ('' for none): the squad as it is, each buy, each sale of a player
-- held now, and each buy with each such sale where his highest bid is more
-- than the cash left (a sale is what pays for him).
DROP VIEW IF EXISTS dec_variant;
CREATE VIEW dec_variant AS
WITH sq AS MATERIALIZED (SELECT * FROM dec_squad),
buys AS (SELECT p.player AS b, p.asking * coalesce((SELECT max(ratio) FROM dec_odds o WHERE o.player = p.player), 1.0)
                AS top
         FROM dec_player p WHERE p.asking IS NOT NULL AND p.player NOT IN (SELECT player FROM sq)),
sells AS (SELECT player AS s FROM sq WHERE player IN (SELECT player FROM dec_player WHERE mine)),
keys AS (SELECT '' AS b, '' AS s UNION ALL SELECT b, '' FROM buys UNION ALL SELECT '', s FROM sells
         UNION ALL SELECT b, s FROM buys, sells WHERE top > (SELECT free FROM dec_cash))
SELECT k.b, k.s, q.player, q.pos FROM keys k, sq q WHERE q.player <> k.s
UNION ALL
SELECT k.b, k.s, p.player, p.pos FROM keys k JOIN dec_player p ON p.player = k.b;

-- Each such squad's best legal eleven each jornada: each position's lo
-- best, then the best of the rest within each position's hi, to eleven.
-- The formations are every count between lo and hi that makes eleven, so
-- this is the best of any formation. A slot nobody can fill scores nothing.
DROP VIEW IF EXISTS dec_eleven;
CREATE VIEW dec_eleven AS
WITH r AS (SELECT v.b, v.s, x.jornada, v.player, v.pos, x.xp,
                  row_number() OVER (PARTITION BY v.b, v.s, x.jornada, v.pos ORDER BY x.xp DESC, v.player) AS n
           FROM dec_variant v JOIN dec_xp x USING (player)),
c AS (SELECT r.*, h.lo, h.hi FROM r JOIN dec_shape h USING (pos)),
e AS (SELECT c.*, row_number() OVER (PARTITION BY b, s, jornada ORDER BY xp DESC, player) AS m
      FROM c WHERE n > lo AND n <= hi)
SELECT b, s, jornada, player, xp FROM c WHERE n <= lo
UNION ALL
SELECT b, s, jornada, player, xp FROM e WHERE m <= 11 - (SELECT sum(lo) FROM dec_shape);

-- Each next move's points on the squad it makes: f(squad) - f(as it is).
DROP VIEW IF EXISTS dec_gain;
CREATE VIEW dec_gain AS
WITH v AS MATERIALIZED (SELECT * FROM dec_variant),
f AS MATERIALIZED (SELECT b, s, total(xp) AS pts FROM dec_eleven GROUP BY b, s),
base AS (SELECT coalesce((SELECT pts FROM f WHERE b = '' AND s = ''), 0) AS pts)
SELECT k.b, k.s, coalesce(f.pts, 0) - base.pts AS gain
FROM (SELECT DISTINCT b, s FROM v) k LEFT JOIN f USING (b, s), base WHERE k.b <> '' OR k.s <> '';

-- Every next move, at each bid its odds offer (at asking, sure, with
-- none), worth its points and its money at lam, a bid's by its chance of
-- winning; with what it does to the squad's size and the cash at the lock.
DROP VIEW IF EXISTS dec_options;
CREATE VIEW dec_options AS
WITH m AS (SELECT * FROM dec_money),
bid AS (SELECT p.player, p.value, p.asking * coalesce(o.ratio, 1.0) AS amount, coalesce(o.p_win, 1.0) AS win
        FROM dec_player p LEFT JOIN dec_odds o USING (player) WHERE p.asking IS NOT NULL),
sale AS (SELECT player, value, proceeds FROM dec_player WHERE mine)
SELECT g.b, g.s, b.amount AS bid, b.win, sa.proceeds,
       g.gain, (SELECT lam FROM m) * ((coalesce(sa.proceeds, 0) - coalesce(sa.value, 0))
                                       - (coalesce(b.amount, 0) - coalesce(b.value, 0))) / 1e6 AS money,
       coalesce(b.win, 1.0) * (g.gain + (SELECT lam FROM m) * ((coalesce(sa.proceeds, 0) - coalesce(sa.value, 0))
                                                              - (coalesce(b.amount, 0) - coalesce(b.value, 0))) / 1e6)
           AS worth,
       (g.b <> '') - (g.s <> '') AS size,
       coalesce(sa.proceeds, 0) - coalesce(b.amount, 0) AS cash
FROM dec_gain g LEFT JOIN bid b ON b.player = g.b LEFT JOIN sale sa ON sa.player = g.s
WHERE g.b = '' OR b.player IS NOT NULL;

-- The plan's next move: the best worth more than nothing that keeps the
-- squad within its cap, cash at the lock at zero or more and bids within
-- cash and the debt; in debt, the least bad sale. None: the plan is made.
DROP VIEW IF EXISTS dec_next;
CREATE VIEW dec_next AS
WITH n AS (SELECT count(*) AS size FROM dec_squad)
SELECT o.* FROM dec_options o, dec_cash c, dec_money m, n
WHERE CASE WHEN c.free < 0 THEN o.b = '' AND o.s <> ''
           ELSE o.worth > 0
                AND n.size + o.size <= m.squad_max
                AND c.free + o.cash >= 0
                AND c.bids + coalesce(o.bid, 0) <= m.cash + m.debt * m.team_value END
ORDER BY o.worth DESC, o.b, o.s, o.bid
LIMIT 1;
