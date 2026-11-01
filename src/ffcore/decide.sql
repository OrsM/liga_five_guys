-- The decision (docs/notes/plan.md; the simplify map, step 3). The aim is
-- to finish first, so every move is worth its points and nothing else;
-- money is a limit, never a goal:
--
--   f(S)          the points of squad S's best legal eleven, summed over
--                 the jornadas left, each player at his forecast cell times
--                 whether he is yours by then (dec_xp); a move's, from the
--                 eleven now by the slots it changes (dec_slots, dec_gain)
--   a buy p at b  win(b) x (f(S + p) - f(S)); of bids as likely to win,
--                 the least
--   a sale q      f(S - q) - f(S): never more than nothing alone, so made
--                 only to pay for a buy (a swap) or to clear a debt
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
-- move, yours (mine) and the market's (asking), his position and what a
-- sale of him gets; each one's expected points a jornada left as yours;
-- each buy's bids and their chance of winning; your cash, squad value, the
-- game's debt share and squad cap, the eleven's size; the formations'
-- bounds.
CREATE TABLE IF NOT EXISTS dec_player (
    player   TEXT PRIMARY KEY,
    pos      TEXT NOT NULL,
    mine     INTEGER NOT NULL,
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
    debt       REAL NOT NULL,
    squad_max  INTEGER NOT NULL,
    xi_size    INTEGER NOT NULL
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
    worth    REAL NOT NULL
);

DROP VIEW IF EXISTS money_rate;

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
-- where his highest bid is more than the cash left or the squad is full (a
-- sale is what pays for him, or makes room for him).
DROP VIEW IF EXISTS dec_variant;
CREATE VIEW dec_variant AS
WITH sq AS MATERIALIZED (SELECT * FROM dec_squad),
buys AS (SELECT p.player AS b, p.asking * coalesce((SELECT max(ratio) FROM dec_odds o WHERE o.player = p.player), 1.0)
                AS top
         FROM dec_player p WHERE p.asking IS NOT NULL AND p.player NOT IN (SELECT player FROM sq)),
sells AS (SELECT player AS s FROM sq WHERE player IN (SELECT player FROM dec_player WHERE mine))
SELECT b, '' AS s FROM buys UNION ALL SELECT '', s FROM sells
UNION ALL SELECT b, s FROM buys, sells
WHERE top > (SELECT free FROM dec_cash) OR (SELECT count(*) FROM sq) >= (SELECT squad_max FROM dec_money);

-- The squad's best legal eleven each jornada: the game's eleven rule
-- (rules.ELEVEN, put in where it is named below as connect() applies this
-- file): each position's lo best, then the best of the rest within each
-- position's hi, to eleven; the best of any formation. A slot nobody can
-- fill scores nothing. Level players: the first by key.
DROP VIEW IF EXISTS dec_eleven;
CREATE VIEW dec_eleven AS
WITH pick AS (SELECT 1 AS id, x.jornada, q.player, q.pos AS slot, q.player AS ord, x.xp AS e
              FROM dec_squad q JOIN dec_xp x USING (player)),
bounds AS (SELECT pos AS slot, lo, hi FROM dec_shape),
/* rules.ELEVEN */
SELECT jornada, player, slot AS pos, e AS xp FROM xi;

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
               OR (g.cnt + 1 <= g.hi AND g.fill - (o.cnt > o.lo) + (g.cnt >= g.lo) <= (SELECT xi_size FROM dec_money))) c)
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
        CASE WHEN p.cnt1 + 1 <= p.hi AND f.fill1 + (p.cnt1 >= p.lo) <= (SELECT xi_size FROM dec_money) THEN 0.0 ELSE 1e9 END,
        coalesce((SELECT min(o.low1) FROM st1 o
                  WHERE o.s = v.s AND o.jornada = x.jornada AND o.cnt1 >= 1
                    AND (o.pos = p.pos OR (p.cnt1 + 1 <= p.hi
                                           AND f.fill1 - (o.cnt1 > o.lo) + (p.cnt1 >= p.lo) <= (SELECT xi_size FROM dec_money)))), 1e9)) AS bar
    FROM v JOIN dec_player bp ON bp.player = v.b JOIN dec_xp x ON x.player = v.b
    JOIN st1 p ON p.s = v.s AND p.jornada = x.jornada AND p.pos = bp.pos
    JOIN f1 f ON f.s = v.s AND f.jornada = x.jornada
    WHERE v.b <> '')
SELECT v.b, v.s, coalesce((SELECT total(out) FROM rm WHERE rm.s = v.s), 0.0)
                 + coalesce((SELECT total(max(xp - bar, 0.0)) FROM bar a WHERE a.b = v.b AND a.s = v.s), 0.0) AS gain
FROM v;

-- Every next move, at each bid its odds offer (at asking, sure, with
-- none), worth its points, a bid's by its chance of winning; with what it
-- does to the squad's size and the cash at the lock.
DROP VIEW IF EXISTS dec_options;
CREATE VIEW dec_options AS
WITH g AS MATERIALIZED (SELECT * FROM dec_gain),
bid AS (SELECT p.player, p.asking * coalesce(o.ratio, 1.0) AS amount, coalesce(o.p_win, 1.0) AS win
        FROM dec_player p LEFT JOIN dec_odds o USING (player) WHERE p.asking IS NOT NULL),
sale AS (SELECT player, proceeds FROM dec_player WHERE mine)
SELECT g.b, g.s, b.amount AS bid, b.win, sa.proceeds, g.gain, coalesce(b.win, 1.0) * g.gain AS worth,
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

-- When each of the plan's sales is made: at once, or, when the plan bids,
-- after the auctions, once you know they are won (a sale made for a bid
-- that is then lost is a player gone for nobody; after the auctions the
-- game still buys him at his value).
DROP VIEW IF EXISTS dec_when;
CREATE VIEW dec_when AS
SELECT d.*, d.kind = 'sell' AND EXISTS (SELECT 1 FROM dec_plan b WHERE b.kind = 'buy') AS after
FROM dec_plan d;

-- The board's moves: each of your players sold, each buy at the plan's bid
-- (or, not in the plan, the lowest bid his odds offer), valued alone from
-- the squad as it is (dec_start), and whether the plan makes it.
DROP VIEW IF EXISTS dec_rows;
CREATE VIEW dec_rows AS
SELECT 'sell' AS kind, s AS player, proceeds AS amount, 1.0 AS win, gain, worth,
       s IN (SELECT player FROM dec_plan WHERE kind = 'sell') AS in_plan
FROM dec_start WHERE b = ''
UNION ALL
SELECT 'buy', b, bid, win, gain, worth, in_plan FROM (
    SELECT d.*, p.player IS NOT NULL AS in_plan,
           row_number() OVER (PARTITION BY d.b ORDER BY p.player IS NULL, d.bid) AS n
    FROM dec_start d LEFT JOIN dec_plan p ON p.kind = 'buy' AND p.player = d.b AND p.amount = d.bid
    WHERE d.s = '' AND d.b <> '')
WHERE n = 1;
