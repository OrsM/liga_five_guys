"""One-off (2026-11-02): sim_facts' rows into seen, under each run's cut (runs.as_of), and the table dropped."""
import re, sqlite3, sys
from pathlib import Path
db_path, schema = Path(sys.argv[1]), Path(sys.argv[2]).read_text()
db = sqlite3.connect(db_path)
if db.execute("SELECT type FROM sqlite_master WHERE name = 'sim_facts'").fetchone() != ("table",):
    sys.exit("sim_facts is not a table: nothing to move")
for t in ("seen", "seen_odds"):
    db.execute(re.search(r"CREATE TABLE IF NOT EXISTS %s \(.*?\) WITHOUT ROWID;" % t, schema, re.S).group(0))
if "cut" not in {r[1] for r in db.execute("PRAGMA table_info(sim_runs)")}:
    db.execute("ALTER TABLE sim_runs ADD COLUMN cut TEXT")
with db:
    db.execute("UPDATE sim_runs SET cut = (SELECT as_of FROM runs WHERE runs.run_id = sim_runs.run_id) WHERE cut IS NULL")
    n = db.execute("INSERT OR IGNORE INTO seen (cut, variant, player, name, pos, value, trend, paid, offer, clause, price,"
                   " route, my_bid, fetches) SELECT r.as_of, 'live', f.player, f.name, nullif(f.pos, ''), f.value, f.trend,"
                   " f.paid, f.offer, f.clause, f.price, f.route, f.my_bid, f.fetches"
                   " FROM sim_facts f JOIN runs r USING (run_id) ORDER BY f.run_id DESC").rowcount
    before = db.execute("SELECT count(*) FROM sim_facts").fetchone()[0]
    for (v,) in db.execute("SELECT name FROM sqlite_master WHERE type = 'view'").fetchall():
        db.execute('DROP VIEW "%s"' % v)            # connect() makes every view again
    db.execute("DROP TABLE sim_facts")
db.execute("PRAGMA user_version = 0")              # connect() applies the schema
print("%s: %d of %d rows moved into seen" % (db_path, n, before))
