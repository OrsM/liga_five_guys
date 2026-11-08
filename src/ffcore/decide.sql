-- The decision (docs/notes/lfg.md). The aim is
-- to finish first, so every move is worth its points and nothing else;
-- money is a limit, never a goal:
--
--   f(S)          the points of squad S's best legal eleven, summed over
--                 the jornadas left, each player at his expected points
--                 (schema.sql cell); a move's, f of the squad it makes less
--                 f(S) (recommender.points)
--   a buy p at b  win(b) x (f(S + p) - f(S)); of bids as likely to win,
--                 the least
--   a sale q      f(S - q) - f(S): never more than nothing alone, so made
--                 only to pay for a buy (a swap) or to clear a debt
--   a swap        a buy with the sale that pays for it, its points on the
--                 squad they make together
--
-- The plan: the recommender (src/recommender.py) picks the buys, a loop
-- over the purchases by worth per euro of bid, each valued again once the
-- one before is in; the plan funds them (src/decision.py plan_rows): where
-- cash at a lock would be below zero, sales until it is zero or more (one
-- that loses nothing; else the one that clears it alone for the fewest
-- points; else the fewest points per euro), and no buy that the sales cost
-- more points than it gains. In debt with no buy: the same sales.
-- decision.record keeps each offer on one of yours assessed (decision_sale).

-- The inputs, one decision's (decision.py inputs), beside what the model
-- looks at (schema.sql look: the market seen, the forecast, your squad and
-- cash at t, my_squad and my_cash): the game's debt
-- share and squad cap, the eleven's size.
DROP TABLE IF EXISTS dec_state;
CREATE TABLE dec_state (
    debt      REAL NOT NULL,
    squad_max INTEGER NOT NULL,
    xi_size   INTEGER NOT NULL
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
-- The bids you have placed (seen), what the plan's checks (decision.after) take as won.
CREATE TABLE IF NOT EXISTS dec_bid (
    player TEXT PRIMARY KEY,
    amount REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS dec_close (
    player TEXT PRIMARY KEY,
    close  TEXT NOT NULL
);
-- Your players whose sale is to a standing offer (it beats waiting), and when that offer lapses: a sale for it is
-- due by then (decision.schedule).
CREATE TABLE IF NOT EXISTS dec_offer (
    player TEXT PRIMARY KEY,
    until  TEXT NOT NULL
);
DROP TABLE IF EXISTS dec_lock;
CREATE TABLE dec_lock (
    jornada INTEGER PRIMARY KEY,
    lock    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dec_money (
    cash       REAL NOT NULL,
    team_value REAL NOT NULL,
    debt       REAL NOT NULL,
    squad_max  INTEGER NOT NULL,
    xi_size    INTEGER NOT NULL
);

-- The players the decision can move: yours (my_squad), at what a sale of
-- each gets as seen (seen.fetches: with an offer on him, the offer or what
-- waiting is worth; else his value), and the market's you could buy, at his
-- asking price (not yours, nor any manager's).
DROP VIEW IF EXISTS dec_player_seen;
CREATE VIEW dec_player_seen AS
SELECT m.player, m.pos, 1 AS mine, NULL AS asking, coalesce(s.fetches, s.value, 0.0) AS proceeds
FROM my_squad m CROSS JOIN look l
LEFT JOIN seen s ON s.cut = l.cut AND s.variant = l.variant AND s.player = m.player
UNION ALL
SELECT s.player, s.pos, 0, s.price, NULL
FROM seen s JOIN look l ON s.cut = l.cut AND s.variant = l.variant
WHERE s.price IS NOT NULL AND s.owner IS NULL AND s.player NOT IN (SELECT player FROM my_squad);

-- Each buy's bids and their chance of winning, as seen.
DROP VIEW IF EXISTS dec_odds_seen;
CREATE VIEW dec_odds_seen AS
SELECT o.player, o.ratio, o.p_win FROM seen_odds o JOIN look l ON o.cut = l.cut AND o.variant = l.variant;

-- When each market player's auction closes (the game's listings, the last close seen), and when each jornada
-- still to lock does (ingest's round_locks), as the game's clock; the plan's steps are due by them
-- (decision.schedule) and count for a jornada if made by its lock (rules.in_force).
DROP VIEW IF EXISTS dec_close_seen;
CREATE VIEW dec_close_seen AS
SELECT player, strftime('%Y-%m-%dT%H:%MZ', t) AS close FROM listing_close;
DROP VIEW IF EXISTS dec_lock_seen;
CREATE VIEW dec_lock_seen AS
SELECT jornada, strftime('%Y-%m-%dT%H:%MZ', stamp_jd(lock)) AS lock FROM round_locks WHERE lock > (SELECT cut());


DROP VIEW IF EXISTS dec_offer_seen;
CREATE VIEW dec_offer_seen AS
SELECT t.player, strftime('%Y-%m-%dT%H:%MZ', min(julianday(o.expires_at))) AS until
FROM offers o JOIN squads t ON t.player_team_id = o.player_team_id JOIN sale_odds s ON s.player = t.player
WHERE o.status = 'pending' AND o.money = s.offer AND s.offer >= coalesce(s.waiting, 0) AND coalesce(o.expires_at, '') <> ''
GROUP BY t.player;

-- The bids you have placed, as seen.
DROP VIEW IF EXISTS dec_bid_seen;
CREATE VIEW dec_bid_seen AS
SELECT s.player, s.my_bid AS amount FROM seen s JOIN look l ON s.cut = l.cut AND s.variant = l.variant WHERE s.my_bid > 0;

-- Your cash, squad value (seen), the game's debt share and squad cap, the
-- eleven's size.
DROP VIEW IF EXISTS dec_money_seen;
CREATE VIEW dec_money_seen AS
SELECT c.cash, (SELECT total(s.value) FROM my_squad m JOIN look l JOIN seen s ON s.cut = l.cut AND s.variant = l.variant
                 AND s.player = m.player) AS team_value, st.debt, st.squad_max, st.xi_size
FROM dec_state st, my_cash c;

-- The game's formations (rules.FORMATIONS, put in where it is named below
-- as connect() applies this file): each one's players by position, f its
-- order in the rules; each position's least and most in any of them.
DROP VIEW IF EXISTS formation;
CREATE VIEW formation AS /* rules.FORMATIONS */;
DROP VIEW IF EXISTS slot_bounds;
CREATE VIEW slot_bounds AS SELECT pos, min(n) AS lo, max(n) AS hi FROM formation GROUP BY pos;
