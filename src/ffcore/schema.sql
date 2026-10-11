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
