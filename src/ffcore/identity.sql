-- Who each player is: the registry crosswalk.py builds (players.csv, its
-- committed record, mirrored here by players.load_crosswalk), and one rule
-- per identity question: an id or a name names a player only when it names
-- exactly one. ffcore.crosswalk runs these same views on the players it
-- holds, so the rule is here and only here.
CREATE TABLE IF NOT EXISTS crosswalk (
    player_id TEXT PRIMARY KEY,
    name      TEXT NOT NULL DEFAULT '',
    club_id   TEXT NOT NULL DEFAULT '',
    ff_slug   TEXT NOT NULL DEFAULT '',
    app_id    TEXT NOT NULL DEFAULT '',
    app_names TEXT NOT NULL DEFAULT ''
);

DROP VIEW IF EXISTS app_player;
CREATE VIEW app_player AS
SELECT app_id, min(player_id) AS player FROM crosswalk
WHERE app_id <> '' GROUP BY app_id HAVING count(*) = 1;

DROP VIEW IF EXISTS slug_player;
CREATE VIEW slug_player AS
SELECT ff_slug, min(player_id) AS player FROM crosswalk
WHERE ff_slug <> '' GROUP BY ff_slug HAVING count(*) = 1;

-- A name (name_key: accents, case and punctuation never count) names a
-- player when one has it; else, within a club, when one there has it.
DROP VIEW IF EXISTS name_player;
CREATE VIEW name_player AS
SELECT name_key(name) AS name_key, min(player_id) AS player FROM crosswalk
WHERE name_key(name) <> '' GROUP BY name_key(name) HAVING count(*) = 1;

DROP VIEW IF EXISTS name_club_player;
CREATE VIEW name_club_player AS
SELECT name_key(name) AS name_key, club_id, min(player_id) AS player FROM crosswalk
WHERE name_key(name) <> '' AND club_id <> ''
GROUP BY name_key(name), club_id HAVING count(*) = 1;

-- The ids two players claim: refused (above) until resolved.
DROP VIEW IF EXISTS id_clashes;
CREATE VIEW id_clashes AS
SELECT 'ff_slug' AS id, ff_slug AS value FROM crosswalk
WHERE ff_slug <> '' GROUP BY ff_slug HAVING count(*) > 1
UNION ALL
SELECT 'app_id', app_id FROM crosswalk WHERE app_id <> '' GROUP BY app_id HAVING count(*) > 1;

-- The names as the board shows them.
DROP VIEW IF EXISTS names;
CREATE VIEW names AS SELECT player_id AS player, name_shown(name) AS name FROM crosswalk;

-- Who each futbolfantasy row (line-ups, starters) is about: its slug, else
-- a name one player has (or one keyed by that name), else a name one player
-- of its club has. A row nothing resolves is about nobody.
-- Kept in the table ff_identity_kept (facts.identity refills it when the registry or
-- the rows change): every view reading a player's row would else derive it
-- again from 227k line-up rows.
DROP VIEW IF EXISTS ff_identity;
DROP VIEW IF EXISTS ff_identity_now;
CREATE VIEW ff_identity_now AS
SELECT d.player_slug, d.player_name, d.team_slug,
       coalesce(s.player, c.player_id, nm.player, nc.player) AS player
FROM (SELECT player_slug, player_name, team_slug FROM lineups
      UNION SELECT player_slug, player_name, team_slug FROM starters) d
LEFT JOIN slug_player s ON s.ff_slug = d.player_slug
LEFT JOIN crosswalk c ON c.player_id = name_key(d.player_name)
LEFT JOIN name_player nm ON nm.name_key = name_key(d.player_name)
LEFT JOIN name_club_player nc ON nc.name_key = name_key(d.player_name)
     AND nc.club_id = d.team_slug;

CREATE TABLE IF NOT EXISTS ff_identity_kept (
    player_slug TEXT NOT NULL,
    player_name TEXT NOT NULL,
    team_slug   TEXT NOT NULL,
    player      TEXT,
    PRIMARY KEY (player_slug, player_name, team_slug)
) WITHOUT ROWID;
CREATE VIEW ff_identity AS SELECT * FROM ff_identity_kept;
