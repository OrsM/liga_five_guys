-- The decision (docs/notes/plan.md; the simplify map, step 3). The aim is
-- to finish first, so every move is worth its points and nothing else;
-- money is a limit, never a goal:
--
--   f(S)          the points of squad S's best legal eleven, summed over
--                 the jornadas left, each player at his expected points
--                 (schema.sql cell); a move's, f of the squad it makes less
--                 f(S) (dec_gain)
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

-- The inputs, one decision's (decision.py inputs), beside what the model
-- looks at (schema.sql look: the market seen, the forecast): your squad and
-- cash (yours, or a replay's), the game's debt share and squad cap, the
-- eleven's size.
CREATE TABLE IF NOT EXISTS dec_state (
    cash      REAL NOT NULL,
    offers    INTEGER NOT NULL,      -- the offers seen are on this squad's players
    buys      INTEGER NOT NULL,      -- 0: sales only
    debt      REAL NOT NULL,
    squad_max INTEGER NOT NULL,
    xi_size   INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS dec_mine (
    player TEXT PRIMARY KEY,
    pos    TEXT NOT NULL
);
-- What the views below make of them, made once a decision (decision.py
-- inputs; the plan reads them at every step).
CREATE TABLE IF NOT EXISTS dec_player (
    player   TEXT PRIMARY KEY,
    pos      TEXT NOT NULL,
    mine     INTEGER NOT NULL,
    asking   REAL,
    proceeds REAL
);
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

-- The players the decision can move: yours, at what a sale of each gets
-- (with an offer on him seen, the offer or what waiting is worth; else his
-- value), and the market's you could buy, at his asking price (not yours,
-- nor any manager's).
DROP VIEW IF EXISTS dec_player_seen;
CREATE VIEW dec_player_seen AS
SELECT m.player, m.pos, 1 AS mine, NULL AS asking,
       coalesce(CASE WHEN st.offers THEN s.fetches END, s.value, 0.0) AS proceeds
FROM dec_mine m CROSS JOIN dec_state st CROSS JOIN look l
LEFT JOIN seen s ON s.cut = l.cut AND s.variant = l.variant AND s.player = m.player
UNION ALL
SELECT s.player, s.pos, 0, s.price, NULL
FROM seen s JOIN look l ON s.cut = l.cut AND s.variant = l.variant CROSS JOIN dec_state st
WHERE st.buys AND s.price IS NOT NULL AND s.owner IS NULL AND s.player NOT IN (SELECT player FROM dec_mine);

-- Each buy's bids and their chance of winning, as seen.
DROP VIEW IF EXISTS dec_odds_seen;
CREATE VIEW dec_odds_seen AS
SELECT o.player, o.ratio, o.p_win FROM seen_odds o JOIN look l ON o.cut = l.cut AND o.variant = l.variant;

-- Your cash, squad value (seen), the game's debt share and squad cap, the
-- eleven's size.
DROP VIEW IF EXISTS dec_money_seen;
CREATE VIEW dec_money_seen AS
SELECT st.cash, (SELECT total(s.value) FROM dec_mine m JOIN look l JOIN seen s ON s.cut = l.cut AND s.variant = l.variant
                 AND s.player = m.player) AS team_value, st.debt, st.squad_max, st.xi_size
FROM dec_state st;

-- The game's formations (rules.FORMATIONS, put in where it is named below
-- as connect() applies this file): each one's players by position, f its
-- order in the rules; each position's least and most in any of them.
DROP VIEW IF EXISTS formation;
CREATE VIEW formation AS /* rules.FORMATIONS */;
DROP VIEW IF EXISTS slot_bounds;
CREATE VIEW slot_bounds AS SELECT pos, min(n) AS lo, max(n) AS hi FROM formation GROUP BY pos;

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

-- What more you can bid once the plan's bids are in: the game takes bids up
-- to your cash and the debt it allows (DEBT_SHARE of your team's value);
-- sales do not count, made after the auctions or not at all.
DROP VIEW IF EXISTS dec_room;
CREATE VIEW dec_room AS
SELECT m.cash + m.debt * m.team_value - c.bids AS room FROM dec_money m, dec_cash c;

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

-- Each next move's points over the jornadas left: the best legal eleven
-- of the squad it makes, less the squad's as it is, each jornada (the
-- game's eleven rule, rules.ELEVEN, put in where it is named below as
-- connect() applies this file: each position's lo best, then the best of
-- the rest within each position's hi, to eleven; a slot nobody can fill
-- scores nothing; level players, the first by key).
DROP VIEW IF EXISTS dec_eleven;
DROP VIEW IF EXISTS dec_slots;
DROP VIEW IF EXISTS dec_gain;
CREATE VIEW dec_gain AS
WITH v AS MATERIALIZED (SELECT row_number() OVER (ORDER BY b, s) AS id, b, s FROM dec_variant
                        UNION ALL SELECT 0, '', ''),
sq AS MATERIALIZED (SELECT * FROM dec_squad),
members AS (SELECT v.id, q.player, q.pos FROM v JOIN sq q ON q.player <> v.s
            UNION ALL
            SELECT v.id, p.player, p.pos FROM v JOIN dec_player p ON p.player = v.b),
pick AS (SELECT m.id, c.jornada, m.player, m.pos AS slot, m.player AS ord, c.e
         FROM members m JOIN cell c USING (player)),
bounds AS (SELECT pos AS slot, lo, hi FROM slot_bounds),
/* rules.ELEVEN */,
f AS (SELECT v.id, coalesce((SELECT total(e) FROM xi WHERE xi.id = v.id), 0.0) AS f FROM v)
SELECT v.b, v.s, f.f - (SELECT f FROM f WHERE id = 0) AS gain FROM v JOIN f USING (id) WHERE v.id > 0;

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
SELECT o.* FROM dec_options o, dec_cash c, dec_money m, dec_room r, n
WHERE CASE WHEN c.free < 0 THEN o.b = '' AND o.s <> ''
           ELSE o.worth > 0
                AND n.size + o.size <= m.squad_max
                AND c.free + o.cash >= 0
                AND coalesce(o.bid, 0) <= r.room END
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

-- Every manager's squad as the model looks: each rival's as seen, yours as
-- the plan leaves it (dec_squad: none made, as you hold it), each with a
-- stand-in for each player it is short of the formation it is fewest short
-- of (the first such in the rules' order).
DROP VIEW IF EXISTS league_squad;
CREATE VIEW league_squad AS
WITH RECURSIVE i(i) AS (SELECT 0 UNION ALL SELECT i + 1 FROM i WHERE i < 10),
sq AS MATERIALIZED (
    SELECT s.owner AS manager, s.player, s.pos FROM look l JOIN seen s ON s.cut = l.cut AND s.variant = l.variant
    WHERE s.owner IS NOT NULL AND s.owner <> l.me
    UNION ALL
    SELECT l.me, d.player, d.pos FROM look l, dec_squad d),
have AS (SELECT manager, pos, count(*) AS n FROM sq GROUP BY manager, pos),
short AS (SELECT m.manager, f.f, f.pos, max(f.n - coalesce(h.n, 0), 0) AS k
          FROM (SELECT DISTINCT manager FROM sq) m CROSS JOIN formation f
          LEFT JOIN have h ON h.manager = m.manager AND h.pos = f.pos),
best AS (SELECT manager, f FROM (
             SELECT manager, f, row_number() OVER (PARTITION BY manager ORDER BY sum(k), f) AS r
             FROM short GROUP BY manager, f) WHERE r = 1)
SELECT manager, player, pos, 0 AS standin FROM sq
UNION ALL
SELECT s.manager, '__phantom_' || s.pos || '_' || i.i, s.pos, 1
FROM short s JOIN best b USING (manager, f) JOIN i ON i.i < s.k;

-- Every manager's eleven each jornada left (rules.ELEVEN), each player's
-- expected points and their variance (a stand-in's his position's).
DROP VIEW IF EXISTS league_xi;
CREATE VIEW league_xi AS
WITH sq AS MATERIALIZED (SELECT q.*, dense_rank() OVER (ORDER BY q.manager) AS id FROM league_squad q),
pick AS (SELECT q.id, c.jornada, q.player, q.pos AS slot, q.player AS ord, c.e
         FROM sq q JOIN cell c ON c.player = q.player WHERE NOT q.standin
         UNION ALL
         SELECT q.id, c.jornada, q.player, q.pos, q.player, c.e
         FROM sq q JOIN standin_cell c ON c.pos = q.pos WHERE q.standin),
bounds AS (SELECT pos AS slot, lo, hi FROM slot_bounds),
/* rules.ELEVEN */
SELECT DISTINCT q.manager, x.jornada, x.player, x.e, coalesce(c.v, h.v) AS v
FROM xi x JOIN sq q ON q.id = x.id AND q.player = x.player
LEFT JOIN cell c ON c.jornada = x.jornada AND c.player = x.player AND NOT q.standin
LEFT JOIN standin_cell h ON h.jornada = x.jornada AND h.pos = q.pos AND q.standin;

-- Each manager's points over the jornadas left as a normal: the mean, his
-- eleven's expected points with those banked (now), and its variance, each
-- jornada's cells' and the persistent share of each player's form shared
-- by his jornadas, (1 + share^2) x sum v + share^2 x sum (his points)^2.
DROP VIEW IF EXISTS league_season;
CREATE VIEW league_season AS
WITH per AS (SELECT manager, player, total(e) AS a, total(v) AS v FROM league_xi GROUP BY manager, player),
g AS (SELECT p.manager, total(p.a) AS mean, (1 + w.share * w.share) * total(p.v) + w.share * w.share * total(p.a * p.a) AS var
      FROM per p CROSS JOIN score_spread w GROUP BY p.manager)
SELECT g.manager, coalesce(b.points, 0.0) AS now, g.mean + coalesce(b.points, 0.0) AS mean, sqrt(max(g.var, 0.0)) AS sd
FROM g LEFT JOIN (SELECT manager, max(CAST(team_points AS REAL)) AS points FROM current_api_standings GROUP BY manager) b
     USING (manager);

-- Each manager's standing: his season's 10th-90th percentile band, his
-- expected final position (1 and the chance each other ends above him,
-- level counting half), his chance of first (integrated over his total,
-- 401 points of it: the chance every other ends no higher), the chance he
-- ends above you, and his cash (yours as the decision holds it).
DROP VIEW IF EXISTS league_standings;
CREATE VIEW league_standings AS
WITH RECURSIVE g(i) AS (SELECT -200 UNION ALL SELECT i + 1 FROM g WHERE i < 200),
d AS MATERIALIZED (SELECT * FROM league_season),
-- P(a ends above b)
above AS (SELECT a.manager AS a, b.manager AS b,
                 CASE WHEN a.sd * a.sd + b.sd * b.sd <= 1e-12
                      THEN CASE WHEN abs(a.mean - b.mean) <= 1e-9 THEN 0.5 WHEN a.mean - b.mean > 1e-9 THEN 1.0 ELSE 0.0 END
                      ELSE ncdf((a.mean - b.mean) / sqrt(a.sd * a.sd + b.sd * b.sd)) END AS p
          FROM d a JOIN d b ON a.manager <> b.manager),
-- each of his totals t, its weight, and the chance each other ends no higher
t AS (SELECT a.manager, g.i, a.mean + a.sd * g.i / 25.0 AS t, exp(-(g.i / 25.0) * (g.i / 25.0) / 2) AS w
      FROM d a, g WHERE a.sd > 0 OR g.i = 0),
low AS (SELECT t.manager, t.i, t.w,
               min(CASE WHEN o.sd <= 0 THEN o.mean <= t.t ELSE ncdf((t.t - o.mean) / o.sd) END) AS least,
               total(ln(max(CASE WHEN o.sd <= 0 THEN o.mean <= t.t ELSE ncdf((t.t - o.mean) / o.sd) END, 1e-300))) AS logs
        FROM t LEFT JOIN d o ON o.manager <> t.manager GROUP BY t.manager, t.i),
first AS (SELECT manager, total(w * CASE WHEN least = 0 THEN 0.0 ELSE exp(logs) END) / total(w) AS p FROM low GROUP BY manager)
SELECT d.manager, d.manager = l.me AS me, d.now, d.mean, d.mean + d.sd * ninv(0.1) AS lo, d.mean + d.sd * ninv(0.9) AS hi,
       1 + coalesce((SELECT total(p) FROM above WHERE a <> d.manager AND b = d.manager), 0.0) AS finish,
       f.p AS p_first,
       CASE WHEN d.manager <> l.me THEN 1 - (SELECT p FROM above WHERE a = l.me AND b = d.manager) END AS p_above,
       CASE WHEN d.manager = l.me THEN (SELECT cash FROM dec_state) ELSE coalesce(c.cash, 0.0) END AS cash
FROM d CROSS JOIN look l LEFT JOIN first f USING (manager) LEFT JOIN cash c USING (manager);

-- Each manager's points one jornada at a time, as a normal (a jornada's own
-- variance: each player's form shared by nothing else): what the standings
-- forecast is graded on (grading.sim_calibration).
DROP VIEW IF EXISTS league_jornada;
CREATE VIEW league_jornada AS
SELECT x.manager, x.jornada, total(x.e) AS mean,
       sqrt(max((1 + w.share * w.share) * total(x.v) + w.share * w.share * total(x.e * x.e), 0.0)) AS sd
FROM league_xi x CROSS JOIN score_spread w GROUP BY x.manager, x.jornada;
