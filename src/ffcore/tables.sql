-- The source facts: the tables parse fills from data/raw (ingest.py), each
-- named src_<concept> (code says the concept: store_sql("market", ...), and
-- tidy maps it), declared: a column a parser emits that is not here is
-- reported and not kept. Numbers are typed as stored (tidy.NUMBERS); text is
-- stripped and never NULL. Each table says its grain and its key, held once.
-- What is computed from them is rebuildable and carries its module's prefix
-- (forecast_, price_, momentum_, dec_, seen, grade_); runs, run_measures,
-- measure_log and facts_meta are the run's bookkeeping.
--
-- A snapshot table keeps every fetch's rows: its current_ view is each
-- source's last fetch, as of t. A keyed table keeps a row once, and again
-- only when it changes (its trigger): its current_ view is each key's last
-- row. history_ views are every row up to t. Rows about a player carry
-- him (identity.sql): an app row by its app id, a futbolfantasy row by
-- ff_identity.


-- market: futbolfantasy's market page: every player's value, a snapshot each fetch.
-- grain: snapshot; key (source, observed_at, ff_id)
CREATE TABLE IF NOT EXISTS src_market (
    observed_at TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    value REAL,
    ff_id TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL DEFAULT '',
    position TEXT NOT NULL DEFAULT '',
    team_id TEXT NOT NULL DEFAULT '',
    team TEXT NOT NULL DEFAULT '',
    club TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS market_at ON src_market (source, observed_at);
CREATE UNIQUE INDEX IF NOT EXISTS market_unique ON src_market (source, observed_at, ff_id);
DROP VIEW IF EXISTS history_market;
DROP VIEW IF EXISTS current_market;
CREATE VIEW current_market AS
SELECT t.observed_at, t.source, t.value, t.ff_id, t.name, t.position, t.team_id, t.team, t.club
FROM src_market t
WHERE /* latest market */
ORDER BY t.rowid;

-- lineups: probable line-ups and injury notes (futbolfantasy, analitica), a snapshot each fetch.
-- grain: snapshot; key (source, observed_at, player_slug, player_name, team_slug)
CREATE TABLE IF NOT EXISTS src_lineups (
    observed_at TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    start_pct REAL,
    player_slug TEXT NOT NULL DEFAULT '',
    player_name TEXT NOT NULL DEFAULT '',
    team_slug TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS lineups_at ON src_lineups (source, observed_at);
CREATE UNIQUE INDEX IF NOT EXISTS lineups_unique ON src_lineups (source, observed_at, player_slug, player_name, team_slug);
-- Who each row is (ff_identity's distinct rows) and each club's listings in order.
CREATE INDEX IF NOT EXISTS lineups_who ON src_lineups (player_slug, player_name, team_slug);
CREATE INDEX IF NOT EXISTS lineups_club ON src_lineups (team_slug, observed_at);
DROP VIEW IF EXISTS history_lineups;
CREATE VIEW history_lineups AS
SELECT t.rowid AS seq, t.observed_at, t.source, t.start_pct, t.player_slug, t.player_name, t.team_slug, t.role, t.status, t.note, f.player
FROM src_lineups t LEFT JOIN ff_identity f ON f.player_slug = t.player_slug AND f.player_name = t.player_name AND f.team_slug = t.team_slug
WHERE t.observed_at <= (SELECT cut()) ORDER BY t.rowid;
DROP VIEW IF EXISTS current_lineups;
CREATE VIEW current_lineups AS
SELECT t.observed_at, t.source, t.start_pct, t.player_slug, t.player_name, t.team_slug, t.role, t.status, t.note, f.player
FROM src_lineups t LEFT JOIN ff_identity f ON f.player_slug = t.player_slug AND f.player_name = t.player_name AND f.team_slug = t.team_slug
WHERE /* latest lineups */
ORDER BY t.rowid;

-- matches: fixtures and results, a row a match.
-- grain: per match, a row again when it changes; key (match_id, observed_at)
CREATE TABLE IF NOT EXISTS src_matches (
    observed_at TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    jornada INTEGER,
    match_id TEXT NOT NULL DEFAULT '',
    path TEXT NOT NULL DEFAULT '',
    home TEXT NOT NULL DEFAULT '',
    away TEXT NOT NULL DEFAULT '',
    score TEXT NOT NULL DEFAULT '',
    kickoff TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS matches_at ON src_matches (source, observed_at);
CREATE UNIQUE INDEX IF NOT EXISTS matches_unique ON src_matches (match_id, observed_at);
CREATE INDEX IF NOT EXISTS matches_key ON src_matches (match_id);
CREATE TRIGGER IF NOT EXISTS matches_unchanged BEFORE INSERT ON src_matches
WHEN EXISTS (SELECT 1 FROM src_matches o WHERE o.rowid = (SELECT max(rowid) FROM src_matches WHERE match_id IS NEW.match_id)
         AND o.source IS NEW.source
         AND o.jornada IS NEW.jornada
         AND o.match_id IS NEW.match_id
         AND o.path IS NEW.path
         AND o.home IS NEW.home
         AND o.away IS NEW.away
         AND o.score IS NEW.score
         AND o.kickoff IS NEW.kickoff)
BEGIN SELECT RAISE(IGNORE); END;
DROP VIEW IF EXISTS history_matches;
DROP VIEW IF EXISTS current_matches;
CREATE VIEW current_matches AS
SELECT t.observed_at, t.source, t.jornada, t.match_id, t.path, t.home, t.away, t.score, t.kickoff
FROM src_matches t
WHERE t.match_id <> ''
  AND t.rowid = (SELECT max(rowid) FROM src_matches x WHERE x.match_id = t.match_id AND x.observed_at <= (SELECT cut())) ORDER BY t.rowid;

-- points: each player's season points total, a row a season and player.
-- grain: per season and player, a row again when it changes; key (season, ff_id, observed_at)
CREATE TABLE IF NOT EXISTS src_points (
    observed_at TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    points REAL,
    games REAL,
    avg REAL,
    ff_id TEXT NOT NULL DEFAULT '',
    player_name TEXT NOT NULL DEFAULT '',
    player_name_full TEXT NOT NULL DEFAULT '',
    team TEXT NOT NULL DEFAULT '',
    season TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS points_at ON src_points (source, observed_at);
CREATE UNIQUE INDEX IF NOT EXISTS points_unique ON src_points (season, ff_id, observed_at);
CREATE INDEX IF NOT EXISTS points_key ON src_points (season, ff_id);
CREATE TRIGGER IF NOT EXISTS points_unchanged BEFORE INSERT ON src_points
WHEN EXISTS (SELECT 1 FROM src_points o WHERE o.rowid = (SELECT max(rowid) FROM src_points WHERE season IS NEW.season AND ff_id IS NEW.ff_id)
         AND o.source IS NEW.source
         AND o.points IS NEW.points
         AND o.games IS NEW.games
         AND o.avg IS NEW.avg
         AND o.ff_id IS NEW.ff_id
         AND o.player_name IS NEW.player_name
         AND o.player_name_full IS NEW.player_name_full
         AND o.team IS NEW.team
         AND o.season IS NEW.season)
BEGIN SELECT RAISE(IGNORE); END;
DROP VIEW IF EXISTS history_points;

-- api_teams: the app's squads: who owns whom, values and clauses, a snapshot each fetch.
-- grain: snapshot; key (source, observed_at, team_id, player_id)
CREATE TABLE IF NOT EXISTS src_api_teams (
    observed_at TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    market_value REAL,
    buyout REAL,
    points REAL,
    player_id TEXT NOT NULL DEFAULT '',
    team_id TEXT NOT NULL DEFAULT '',
    manager TEXT NOT NULL DEFAULT '',
    player_name TEXT NOT NULL DEFAULT '',
    player_name_full TEXT NOT NULL DEFAULT '',
    player_status TEXT NOT NULL DEFAULT '',
    player_team_id TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS api_teams_at ON src_api_teams (source, observed_at);
CREATE UNIQUE INDEX IF NOT EXISTS api_teams_unique ON src_api_teams (source, observed_at, team_id, player_id);
DROP VIEW IF EXISTS history_api_teams;
CREATE VIEW history_api_teams AS
SELECT t.observed_at, t.source, t.market_value, t.buyout, t.points, t.player_id, t.team_id, t.manager, t.player_name, t.player_name_full, t.player_status, t.player_team_id, a.player
FROM src_api_teams t LEFT JOIN app_player a ON a.app_id = t.player_id
WHERE t.observed_at <= (SELECT cut()) ORDER BY t.rowid;
DROP VIEW IF EXISTS current_api_teams;
CREATE VIEW current_api_teams AS
SELECT t.observed_at, t.source, t.market_value, t.buyout, t.points, t.player_id, t.team_id, t.manager, t.player_name, t.player_name_full, t.player_status, t.player_team_id, a.player
FROM src_api_teams t LEFT JOIN app_player a ON a.app_id = t.player_id
WHERE /* latest api_teams */
ORDER BY t.rowid;

-- api_standings: the app's standings: points, value, cash, a snapshot each fetch.
-- grain: snapshot; key (source, observed_at, team_id)
CREATE TABLE IF NOT EXISTS src_api_standings (
    observed_at TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    position INTEGER,
    team_points REAL,
    fixture_points REAL,
    team_value REAL,
    team_money REAL,
    team_id TEXT NOT NULL DEFAULT '',
    user_id TEXT NOT NULL DEFAULT '',
    manager TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS api_standings_at ON src_api_standings (source, observed_at);
CREATE UNIQUE INDEX IF NOT EXISTS api_standings_unique ON src_api_standings (source, observed_at, team_id);
DROP VIEW IF EXISTS current_api_standings;
CREATE VIEW current_api_standings AS
SELECT t.observed_at, t.source, t.position, t.team_points, t.fixture_points, t.team_value, t.team_money, t.team_id, t.user_id, t.manager
FROM src_api_standings t
WHERE /* latest api_standings */
ORDER BY t.rowid;

-- api_market: the app's market listings and your bids, a snapshot each fetch.
-- grain: snapshot; key (source, observed_at, market_id, bid_id)
CREATE TABLE IF NOT EXISTS src_api_market (
    observed_at TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    market_value REAL,
    sale_price REAL,
    bid_money REAL,
    bids INTEGER,
    player_id TEXT NOT NULL DEFAULT '',
    market_id TEXT NOT NULL DEFAULT '',
    player_name TEXT NOT NULL DEFAULT '',
    player_name_full TEXT NOT NULL DEFAULT '',
    seller TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '',
    player_status TEXT NOT NULL DEFAULT '',
    expires_at TEXT NOT NULL DEFAULT '',
    bid_id TEXT NOT NULL DEFAULT '',
    bid_status TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS api_market_at ON src_api_market (source, observed_at);
CREATE UNIQUE INDEX IF NOT EXISTS api_market_unique ON src_api_market (source, observed_at, market_id, bid_id);
DROP VIEW IF EXISTS current_api_market;
CREATE VIEW current_api_market AS
SELECT t.observed_at, t.source, t.market_value, t.sale_price, t.bid_money, t.bids, t.player_id, t.market_id, t.player_name, t.player_name_full, t.seller, t.status, t.player_status, t.expires_at, t.bid_id, t.bid_status, a.player
FROM src_api_market t LEFT JOIN app_player a ON a.app_id = t.player_id
WHERE /* latest api_market */
ORDER BY t.rowid;

-- api_offers: the game's nightly offers for your listed players, a snapshot each fetch.
-- grain: snapshot; key (source, observed_at, player_team_id, offer_id)
CREATE TABLE IF NOT EXISTS src_api_offers (
    observed_at TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    money REAL,
    player_team_id TEXT NOT NULL DEFAULT '',
    offer_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT '',
    expires_at TEXT NOT NULL DEFAULT '',
    from_market TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS api_offers_at ON src_api_offers (source, observed_at);
CREATE UNIQUE INDEX IF NOT EXISTS api_offers_unique ON src_api_offers (source, observed_at, player_team_id, offer_id);
DROP VIEW IF EXISTS current_api_offers;
CREATE VIEW current_api_offers AS
SELECT t.observed_at, t.source, t.money, t.player_team_id, t.offer_id, t.status, t.created_at, t.expires_at, t.from_market
FROM src_api_offers t
WHERE /* latest api_offers */
ORDER BY t.rowid;

-- api_lineup: your line-up as the app has it, a snapshot each fetch.
-- grain: snapshot; key (source, observed_at, player_id)
CREATE TABLE IF NOT EXISTS src_api_lineup (
    observed_at TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    market_value REAL,
    points REAL,
    player_id TEXT NOT NULL DEFAULT '',
    slot TEXT NOT NULL DEFAULT '',
    formation TEXT NOT NULL DEFAULT '',
    player_name TEXT NOT NULL DEFAULT '',
    player_name_full TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS api_lineup_at ON src_api_lineup (source, observed_at);
CREATE UNIQUE INDEX IF NOT EXISTS api_lineup_unique ON src_api_lineup (source, observed_at, player_id);
DROP VIEW IF EXISTS current_api_lineup;
CREATE VIEW current_api_lineup AS
SELECT t.observed_at, t.source, t.week, t.market_value, t.points, t.player_id, t.slot, t.formation, t.player_name, t.player_name_full, t.position_id, t.snapshot_at, a.player
FROM api_lineup t LEFT JOIN app_player a ON a.app_id = t.player_id
WHERE /* latest api_lineup */
ORDER BY t.rowid;

-- api_leagues: the leagues you are in, a snapshot each fetch.
-- grain: snapshot; key (source, observed_at, league_id)
CREATE TABLE IF NOT EXISTS src_api_leagues (
    observed_at TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    managers INTEGER,
    money REAL,
    team_value REAL,
    team_points REAL,
    league_id TEXT NOT NULL DEFAULT '',
    league_name TEXT NOT NULL DEFAULT '',
    team_id TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS api_leagues_at ON src_api_leagues (source, observed_at);
CREATE UNIQUE INDEX IF NOT EXISTS api_leagues_unique ON src_api_leagues (source, observed_at, league_id);

-- api_players_all: every player the app lists, a row a player.
-- grain: per player, a row again when it changes; key (player_id, observed_at)
CREATE TABLE IF NOT EXISTS src_api_players_all (
    observed_at TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    market_value REAL,
    player_id TEXT NOT NULL DEFAULT '',
    team_id TEXT NOT NULL DEFAULT '',
    player_name TEXT NOT NULL DEFAULT '',
    player_name_full TEXT NOT NULL DEFAULT '',
    player_status TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS api_players_all_at ON src_api_players_all (source, observed_at);
CREATE UNIQUE INDEX IF NOT EXISTS api_players_all_unique ON src_api_players_all (player_id, observed_at);
CREATE INDEX IF NOT EXISTS api_players_all_key ON src_api_players_all (player_id);
CREATE TRIGGER IF NOT EXISTS api_players_all_unchanged BEFORE INSERT ON src_api_players_all
WHEN EXISTS (SELECT 1 FROM src_api_players_all o WHERE o.rowid = (SELECT max(rowid) FROM src_api_players_all WHERE player_id IS NEW.player_id)
         AND o.source IS NEW.source
         AND o.market_value IS NEW.market_value
         AND o.player_id IS NEW.player_id
         AND o.team_id IS NEW.team_id
         AND o.player_name IS NEW.player_name
         AND o.player_name_full IS NEW.player_name_full
         AND o.player_status IS NEW.player_status)
BEGIN SELECT RAISE(IGNORE); END;
DROP VIEW IF EXISTS current_api_players_all;
CREATE VIEW current_api_players_all AS
SELECT t.observed_at, t.source, t.market_value, t.player_id, t.team_id, t.player_name, t.player_name_full, t.player_status, a.player
FROM src_api_players_all t LEFT JOIN app_player a ON a.app_id = t.player_id
WHERE t.player_id <> ''
  AND t.rowid = (SELECT max(rowid) FROM src_api_players_all x WHERE x.player_id = t.player_id AND x.observed_at <= (SELECT cut())) ORDER BY t.rowid;

-- results_history: past seasons' results with xG (football-data), a row a match.
-- grain: per match, a row again when it changes; key (season, date, home_name, away_name, observed_at)
CREATE TABLE IF NOT EXISTS src_results_history (
    observed_at TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    home_goals REAL,
    away_goals REAL,
    season TEXT NOT NULL DEFAULT '',
    date TEXT NOT NULL DEFAULT '',
    home_name TEXT NOT NULL DEFAULT '',
    away_name TEXT NOT NULL DEFAULT '',
    home TEXT NOT NULL DEFAULT '',
    away TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS results_history_at ON src_results_history (source, observed_at);
CREATE UNIQUE INDEX IF NOT EXISTS results_history_unique ON src_results_history (season, date, home_name, away_name, observed_at);
CREATE INDEX IF NOT EXISTS results_history_key ON src_results_history (season, date, home_name, away_name);
CREATE TRIGGER IF NOT EXISTS results_history_unchanged BEFORE INSERT ON src_results_history
WHEN EXISTS (SELECT 1 FROM src_results_history o WHERE o.rowid = (SELECT max(rowid) FROM src_results_history WHERE season IS NEW.season AND date IS NEW.date AND home_name IS NEW.home_name AND away_name IS NEW.away_name)
         AND o.source IS NEW.source
         AND o.home_goals IS NEW.home_goals
         AND o.away_goals IS NEW.away_goals
         AND o.season IS NEW.season
         AND o.date IS NEW.date
         AND o.home_name IS NEW.home_name
         AND o.away_name IS NEW.away_name
         AND o.home IS NEW.home
         AND o.away IS NEW.away)
BEGIN SELECT RAISE(IGNORE); END;
DROP VIEW IF EXISTS current_results_history;
CREATE VIEW current_results_history AS
SELECT t.observed_at, t.source, t.home_goals, t.away_goals, t.season, t.date, t.home_name, t.away_name, t.home, t.away
FROM src_results_history t
WHERE t.season <> '' AND t.date <> '' AND t.home_name <> '' AND t.away_name <> ''
  AND t.rowid = (SELECT max(rowid) FROM src_results_history x WHERE x.season = t.season AND x.date = t.date AND x.home_name = t.home_name AND x.away_name = t.away_name AND x.observed_at <= (SELECT cut())) ORDER BY t.rowid;

-- starters: who started or came on, a row a match, team and player.
-- grain: per match, team and player, a row again when it changes; key (match_id, team_slug, player_slug, observed_at)
CREATE TABLE IF NOT EXISTS src_starters (
    observed_at TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    minute REAL,
    player_slug TEXT NOT NULL DEFAULT '',
    player_name TEXT NOT NULL DEFAULT '',
    team_slug TEXT NOT NULL DEFAULT '',
    match_id TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS starters_at ON src_starters (source, observed_at);
CREATE UNIQUE INDEX IF NOT EXISTS starters_unique ON src_starters (match_id, team_slug, player_slug, observed_at);
CREATE INDEX IF NOT EXISTS starters_key ON src_starters (match_id, team_slug, player_slug);
CREATE TRIGGER IF NOT EXISTS starters_unchanged BEFORE INSERT ON src_starters
WHEN EXISTS (SELECT 1 FROM src_starters o WHERE o.rowid = (SELECT max(rowid) FROM src_starters WHERE match_id IS NEW.match_id AND team_slug IS NEW.team_slug AND player_slug IS NEW.player_slug)
         AND o.source IS NEW.source
         AND o.minute IS NEW.minute
         AND o.player_slug IS NEW.player_slug
         AND o.player_name IS NEW.player_name
         AND o.team_slug IS NEW.team_slug
         AND o.match_id IS NEW.match_id
         AND o.role IS NEW.role)
BEGIN SELECT RAISE(IGNORE); END;
DROP VIEW IF EXISTS current_starters;
CREATE VIEW current_starters AS
SELECT t.rowid AS seq, t.observed_at, t.source, t.minute, t.player_slug, t.player_name, t.team_slug, t.match_id, t.role, f.player
FROM src_starters t LEFT JOIN ff_identity f ON f.player_slug = t.player_slug AND f.player_name = t.player_name AND f.team_slug = t.team_slug
WHERE t.match_id <> '' AND t.team_slug <> '' AND t.player_slug <> ''
  AND t.rowid = (SELECT max(rowid) FROM src_starters x WHERE x.match_id = t.match_id AND x.team_slug = t.team_slug AND x.player_slug = t.player_slug AND x.observed_at <= (SELECT cut())) ORDER BY t.rowid;

-- api_activity: the league's activity feed, a row an event.
-- grain: per event, a row again when it changes; key (activity_id, observed_at)
CREATE TABLE IF NOT EXISTS src_api_activity (
    observed_at TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT '',
    amount REAL,
    player_id TEXT NOT NULL DEFAULT '',
    activity_id TEXT NOT NULL DEFAULT '',
    at TEXT NOT NULL DEFAULT '',
    kind TEXT NOT NULL DEFAULT '',
    user_id TEXT NOT NULL DEFAULT '',
    counterparty TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS api_activity_at ON src_api_activity (source, observed_at);
CREATE UNIQUE INDEX IF NOT EXISTS api_activity_unique ON src_api_activity (activity_id, observed_at);
CREATE INDEX IF NOT EXISTS api_activity_key ON src_api_activity (activity_id);
CREATE TRIGGER IF NOT EXISTS api_activity_unchanged BEFORE INSERT ON src_api_activity
WHEN EXISTS (SELECT 1 FROM src_api_activity o WHERE o.rowid = (SELECT max(rowid) FROM src_api_activity WHERE activity_id IS NEW.activity_id)
         AND o.source IS NEW.source
         AND o.amount IS NEW.amount
         AND o.player_id IS NEW.player_id
         AND o.activity_id IS NEW.activity_id
         AND o.at IS NEW.at
         AND o.kind IS NEW.kind
         AND o.user_id IS NEW.user_id
         AND o.counterparty IS NEW.counterparty)
BEGIN SELECT RAISE(IGNORE); END;
DROP VIEW IF EXISTS history_api_activity;
DROP VIEW IF EXISTS current_api_activity;
CREATE VIEW current_api_activity AS
SELECT t.observed_at, t.source, t.amount, t.player_id, t.activity_id, t.at, t.kind, t.user_id, t.counterparty, a.player
FROM src_api_activity t LEFT JOIN app_player a ON a.app_id = t.player_id
WHERE t.activity_id <> ''
  AND t.rowid = (SELECT max(rowid) FROM src_api_activity x WHERE x.activity_id = t.activity_id AND x.observed_at <= (SELECT cut())) ORDER BY t.rowid;
