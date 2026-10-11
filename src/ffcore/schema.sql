-- data/lfg.db, applied on every connect (ffcore/db.py). Only adds: a
-- changed table is dropped by hand and rebuilt (docs/plan-datasette.md).

-- One row per run of src/run.py. finished and ok stay NULL while it runs.
CREATE TABLE IF NOT EXISTS runs (
    run_id     INTEGER PRIMARY KEY,
    started    TEXT NOT NULL,     -- wall clock, UTC
    as_of      TEXT NOT NULL,     -- the clock the model read (LFG_NOW when pinned)
    trigger    TEXT NOT NULL,     -- LFG_TRIGGER: service, button
    commit_sha TEXT NOT NULL,
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
