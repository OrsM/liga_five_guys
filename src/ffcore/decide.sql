-- The decision (docs/notes/plan.md; the simplify map, step 3), every number
-- in points, each a measured fact, a game rule, or the backtest's to settle:
--
--   lam           what a million is worth: what a million more in value
--                 adds to a player's points a game (players with 3+ games),
--                 for each jornada left (money_rate)
--   f(S)          the points of squad S's best legal eleven, summed over
--                 the jornadas left, each player at his forecast cell times
--                 whether he is yours by then (dec_xp); a move's, from the
--                 eleven now by the slots it changes (dec_slots, dec_gain)
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

-- Every single move valued from the squad as it is, before the plan's
-- first (dec_options then: a buy at each bid, a sale): what the board shows
-- beside the plan, and what the plan's moves are graded by.
CREATE TABLE IF NOT EXISTS dec_start (
    b        TEXT NOT NULL,
    s        TEXT NOT NULL,
    bid      REAL,
    win      REAL,
    proceeds REAL,
    gain     REAL NOT NULL,          -- points
    money    REAL NOT NULL,          -- points: money gone or made, at lam
    worth    REAL NOT NULL
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

-- Each next move, by the buy (b) and the sale (s) it holds ('' for none):
-- each buy, each sale of a player held now, and each buy with each such sale
-- where his highest bid is more than the cash left (a sale is what pays for
-- him).
DROP VIEW IF EXISTS dec_variant;
CREATE VIEW dec_variant AS
WITH sq AS MATERIALIZED (SELECT * FROM dec_squad),
buys AS (SELECT p.player AS b, p.asking * coalesce((SELECT max(ratio) FROM dec_odds o WHERE o.player = p.player), 1.0)
                AS top
         FROM dec_player p WHERE p.asking IS NOT NULL AND p.player NOT IN (SELECT player FROM sq)),
sells AS (SELECT player AS s FROM sq WHERE player IN (SELECT player FROM dec_player WHERE mine))
SELECT b, '' AS s FROM buys UNION ALL SELECT '', s FROM sells
UNION ALL SELECT b, s FROM buys, sells WHERE top > (SELECT free FROM dec_cash);

-- The squad's best legal eleven each jornada: each position's lo best, then
-- the best of the rest within each position's hi, to eleven. The formations
-- are every count between lo and hi that makes eleven, so this is the best
-- of any formation. A slot nobody can fill scores nothing.
DROP VIEW IF EXISTS dec_eleven;
CREATE VIEW dec_eleven AS
WITH r AS (SELECT x.jornada, q.player, q.pos, x.xp,
                  row_number() OVER (PARTITION BY x.jornada, q.pos ORDER BY x.xp DESC, q.player) AS n
           FROM dec_squad q JOIN dec_xp x USING (player)),
c AS (SELECT r.*, h.lo, h.hi FROM r JOIN dec_shape h USING (pos)),
e AS (SELECT c.*, row_number() OVER (PARTITION BY jornada ORDER BY xp DESC, player) AS m
      FROM c WHERE n > lo AND n <= hi)
SELECT jornada, player, pos, xp FROM c WHERE n <= lo
UNION ALL
SELECT jornada, player, pos, xp FROM e WHERE m <= 11 - (SELECT sum(lo) FROM dec_shape);

-- Each position, each jornada, in the eleven now: how many play (cnt), the
-- weakest (low, lowp) and next weakest (low2) of them, the best of the
-- position on the bench (bench), and fill, max(cnt, lo) summed over the
-- positions. Eleven players can play together when no position has more
-- than hi and fill is no more than eleven: so a starter can make way for a
-- player of another position only while his own stays above lo and the
-- other's below hi.
DROP VIEW IF EXISTS dec_slots;
CREATE VIEW dec_slots AS
WITH xi AS MATERIALIZED (SELECT * FROM dec_eleven),
w AS (SELECT jornada, pos, count(*) AS cnt,
             max(CASE k WHEN 1 THEN xp END) AS low, max(CASE k WHEN 1 THEN player END) AS lowp,
             max(CASE k WHEN 2 THEN xp END) AS low2
      FROM (SELECT *, row_number() OVER (PARTITION BY jornada, pos ORDER BY xp, player DESC) AS k FROM xi)
      GROUP BY jornada, pos),
bn AS (SELECT x.jornada, q.pos, max(x.xp) AS bench
       FROM dec_squad q JOIN dec_xp x USING (player)
       WHERE NOT EXISTS (SELECT 1 FROM xi WHERE xi.jornada = x.jornada AND xi.player = q.player)
       GROUP BY x.jornada, q.pos)
SELECT j.jornada, h.pos, h.lo, h.hi, coalesce(w.cnt, 0) AS cnt, w.low, w.lowp, w.low2, bn.bench,
       sum(max(coalesce(w.cnt, 0), h.lo)) OVER (PARTITION BY j.jornada) AS fill
FROM (SELECT DISTINCT jornada FROM dec_xp) j CROSS JOIN dec_shape h
LEFT JOIN w ON w.jornada = j.jornada AND w.pos = h.pos
LEFT JOIN bn ON bn.jornada = j.jornada AND bn.pos = h.pos;

-- Each next move's points over the jornadas left, from the eleven now (no
-- squad's eleven built again: a move changes it by at most one player each
-- way, the formations being a matroid's bases), a jornada at a time:
--   a sale     a starter leaves; his replacement is the best on the bench
--              whose position can legally take his slot, or nobody: it
--              costs his points less his replacement's (nothing if he does
--              not play)
--   a buy      against the eleven that leaves, his bar: the weakest starter
--              whose slot he can legally take (his own position's, or
--              another's while his has room), or an empty slot (0); he adds
--              his points over the bar, or nothing below it
DROP VIEW IF EXISTS dec_gain;
CREATE VIEW dec_gain AS
WITH v AS MATERIALIZED (SELECT * FROM dec_variant),
st AS MATERIALIZED (SELECT * FROM dec_slots),
sales AS (SELECT DISTINCT s FROM v),
starts AS (SELECT d.s, x.jornada, p.pos, x.xp FROM sales d JOIN dec_player p ON p.player = d.s
           JOIN dec_xp x ON x.player = d.s
           WHERE EXISTS (SELECT 1 FROM dec_eleven e WHERE e.jornada = x.jornada AND e.player = d.s)),
-- a sale: what each jornada he starts in loses (out) and the position of his replacement (r)
rm AS MATERIALIZED (
    SELECT s, jornada, q, out, r FROM (
        SELECT c.*, row_number() OVER (PARTITION BY s, jornada ORDER BY out DESC, r IS NULL, r) AS k FROM (
            SELECT a.s, a.jornada, a.pos AS q, -a.xp AS out, NULL AS r FROM starts a
            UNION ALL
            SELECT a.s, a.jornada, a.pos, g.bench - a.xp, g.pos
            FROM starts a JOIN st g ON g.jornada = a.jornada AND g.bench IS NOT NULL
            JOIN st o ON o.jornada = a.jornada AND o.pos = a.pos
            WHERE g.pos = a.pos
               OR (g.cnt + 1 <= g.hi AND g.fill - (o.cnt > o.lo) + (g.cnt >= g.lo) <= 11)) c)
    WHERE k = 1),
-- the slots once he has left: each position's count and weakest starter (low1), fill1
st1 AS MATERIALIZED (
    SELECT d.s, t.jornada, t.pos, t.lo, t.hi,
           t.cnt - (t.pos IS m.q) + (t.pos IS m.r) AS cnt1,
           nullif(min(coalesce(CASE WHEN t.pos IS m.q AND d.s = t.lowp THEN t.low2 ELSE t.low END, 1e9),
                      coalesce(CASE WHEN t.pos IS m.r THEN t.bench END, 1e9)), 1e9) AS low1
    FROM sales d CROSS JOIN st t
    LEFT JOIN rm m ON m.s = d.s AND m.jornada = t.jornada),
f1 AS MATERIALIZED (SELECT s, jornada, sum(max(cnt1, lo)) AS fill1 FROM st1 GROUP BY s, jornada),
-- a buy: his bar each jornada in the slots the sale leaves
bar AS (
    SELECT v.b, v.s, x.jornada, x.xp, min(
        CASE WHEN p.cnt1 + 1 <= p.hi AND f.fill1 + (p.cnt1 >= p.lo) <= 11 THEN 0.0 ELSE 1e9 END,
        coalesce((SELECT min(o.low1) FROM st1 o
                  WHERE o.s = v.s AND o.jornada = x.jornada AND o.cnt1 >= 1
                    AND (o.pos = p.pos OR (p.cnt1 + 1 <= p.hi
                                           AND f.fill1 - (o.cnt1 > o.lo) + (p.cnt1 >= p.lo) <= 11))), 1e9)) AS bar
    FROM v JOIN dec_player bp ON bp.player = v.b JOIN dec_xp x ON x.player = v.b
    JOIN st1 p ON p.s = v.s AND p.jornada = x.jornada AND p.pos = bp.pos
    JOIN f1 f ON f.s = v.s AND f.jornada = x.jornada
    WHERE v.b <> '')
SELECT v.b, v.s, coalesce((SELECT total(out) FROM rm WHERE rm.s = v.s), 0.0)
                 + coalesce((SELECT total(max(xp - bar, 0.0)) FROM bar a WHERE a.b = v.b AND a.s = v.s), 0.0) AS gain
FROM v;

-- Every next move, at each bid its odds offer (at asking, sure, with
-- none), worth its points and its money at lam, a bid's by its chance of
-- winning; with what it does to the squad's size and the cash at the lock.
DROP VIEW IF EXISTS dec_options;
CREATE VIEW dec_options AS
WITH m AS (SELECT * FROM dec_money),
g AS MATERIALIZED (SELECT * FROM dec_gain),
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
FROM g LEFT JOIN bid b ON b.player = g.b LEFT JOIN sale sa ON sa.player = g.s
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

-- The board's moves: each of your players sold, each buy at the plan's bid
-- (or, not in the plan, the lowest bid his odds offer), valued alone from
-- the squad as it is (dec_start), and whether the plan makes it.
DROP VIEW IF EXISTS dec_rows;
CREATE VIEW dec_rows AS
SELECT 'sell' AS kind, s AS player, proceeds AS amount, 1.0 AS win, gain, money, worth,
       s IN (SELECT player FROM dec_plan WHERE kind = 'sell') AS in_plan
FROM dec_start WHERE b = ''
UNION ALL
SELECT 'buy', b, bid, win, gain, money, worth, in_plan FROM (
    SELECT d.*, p.player IS NOT NULL AS in_plan,
           row_number() OVER (PARTITION BY d.b ORDER BY p.player IS NULL, d.bid) AS n
    FROM dec_start d LEFT JOIN dec_plan p ON p.kind = 'buy' AND p.player = d.b AND p.amount = d.bid
    WHERE d.s = '' AND d.b <> '')
WHERE n = 1;
