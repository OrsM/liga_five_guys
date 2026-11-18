-- What a live run keeps of the decision (src/decision.py): the opportunities the plan saw at each cut, one row a player.
CREATE TABLE IF NOT EXISTS dec_opportunity (
    cut         TEXT NOT NULL,
    player      TEXT NOT NULL,
    pos         TEXT NOT NULL,
    status      TEXT NOT NULL,
    asking      REAL,
    close_at    TEXT,
    from_j      INTEGER NOT NULL,
    replaces    TEXT,
    gain        REAL NOT NULL,
    per_million REAL,
    price_next  REAL,
    price_lock  REAL,
    bid         REAL,
    win         REAL,
    placed      REAL,
    PRIMARY KEY (cut, player)
) WITHOUT ROWID;
