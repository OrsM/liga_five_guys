# ffcore/tidy.py — design notes

The long-form "why" behind `ffcore/tidy.py`'s CSV IO, caching and gated-feed
rules. The source keeps a one-line pointer to each section here.

## why CSV IO/path handling was consolidated here

Paths, CSV IO and timestamp parsing were copy-pasted across report.py,
offers.py, find_slug.py and history.py. `Market` is the part that actually
matters: an index over EVERY snapshot in market.csv, not just the newest
one, because "what was this player worth when that transaction happened"
and "how stale is the reading I just used" can't be answered from
`latest_only()`, which is all the pre-consolidation code ever looked at.

## timezones — the trap this module exists to close

ff_ingest stamps snapshots in UTC ("2026-08-12T2100Z"). The app's Activity
feed (and every ledger date) is Europe/Madrid wall-clock with no offset
("2026-08-12T21:24") — two hours apart in August. Compared naively, a
purchase gets matched to a snapshot two hours after it, which is exactly
the direction that makes an overpay look like a bargain. Ledger strings go
through `ledger_stamp()`, snapshot strings through `snapshot_stamp()`, both
come back as aware UTC.

## `read_csv()` — the parse cache, its isolation, and interning

One parse per file per process, keyed on (mtime, size) rather than
filename. The chain now runs in one interpreter (src/run.py), and in that
interpreter market.csv was parsed sixteen times and lineups.csv eleven —
2.9 of ten seconds spent turning the same 2.8MB into the same 32,515 dicts,
because every stage that wants a player asks for the whole table.

CALLERS GET THEIR OWN DICTS on every call. Handing back the cached rows
directly would be faster and would be a silent-corruption bug waiting for
the first caller that writes to a row — this file has sixty-one read sites,
too many to audit as new ones get added. The copy costs a sixth of the
parse, so safety here is nearly free: measured 2026-08-24 over one sim
stage, 43 calls across 13 files cost 1.52s, ~1.0s unavoidable first-parse
and ~0.5s this copy (market.csv/lineups.csv are ~85K rows each, read three
times apiece, ~0.067s a copy). A mutation-detector run over that one stage
found no caller mutating a handed-out row, so the copy is defensive rather
than currently load-bearing — dropping it needs the same check over the
whole pipeline (ingest/crosswalk/sources are where a transform-in-place
would plausibly live), not this one stage's evidence.

Invalidated two ways, deliberately overlapping: the cache key carries the
file's mtime+size, and every writer drops its own path — the key alone
would be enough except for a rewrite inside one clock tick that happens to
land on the same length.

CELL VALUES ARE INTERNED at parse time (`sys.intern()`), before ever
reaching the cache. market.csv/lineups.csv are a snapshot log — ~94k/~75k
rows behind a few hundred distinct players and ~20 clubs — so a fresh
"Athletic Club" string on every one of a club's ~4,700 rows is near-pure
duplication. Measured 2026-08-28: market.csv's 94,353 rows hold 849,177
cells behind only 32,465 distinct strings; parsing this way took peak RSS
from 80MB to 39MB. Safe everywhere read_csv is safe — an interned string is
an ordinary immutable str, indistinguishable from one that isn't; this
changes nothing about the copy-on-return contract above, which is about the
dict each row lives in, never its values.

Uses `csv.reader` + `zip(fieldnames, ...)` instead of `DictReader`: same
rows (verified byte-for-byte equal on every tidy CSV in the store,
2026-08-29) — DictReader's per-row restkey/restval bookkeeping is dead
weight since ff_ingest never writes a ragged row. ~25% faster parsing
market.csv (0.53s -> 0.40s).

## `read_csv_frozen()` — the uncopied path, for one long-lived caller

`read_csv()`'s copy-on-return is the right default precisely because most
callers are one function's local variable, live briefly, and were never
individually audited. `Market` is the opposite shape: built once in
`League.load()`, held in `ffcore.model`'s one process-wide Session for the
rest of the run, and read by exactly three modules (checked 2026-08-29, all
of them only iterate or `.get()`, never assign into a row). For that caller
the copy was pure standing cost — market.csv's ~96k rows held twice (once
in the cache, once in `Market.rows`) just to guard against a mutation
nothing does. Returns `MappingProxyType`, not a raw dict reference, so the
guard stays real: a future `row["x"] = y` gets a loud `TypeError` at the
write instead of a silent wrong answer seven modules away. Building 96k
proxies costs about half what copying 96k dicts did — faster AND lighter,
not a speed/memory trade.

## `latest_snapshot()` — one forward pass, bounded memory

`latest_only(read_csv(path))` without ever holding the whole file in
memory. market.csv/lineups.csv are every snapshot ever taken so `Market`
and rivals.py can answer historical questions — but a caller that only
wants "now" (`ffcore.model.session()`, `methodology.latest_market()`) was
paying to materialise and copy all of it just to keep the <1% that share
the newest `observed_at`. Measured 2026-08-28: `ffcore/model.py`'s own
self-test alone peaked at 380MB RSS, the largest single contributor to
`lfg-run`'s parallel self-test phase (~680MB combined) by a wide margin
over every other stage (next highest ~70MB) — almost all of it this. One
forward pass: `kept` only ever holds rows from the current-newest block
seen so far, so peak memory is bounded by ONE snapshot's worth of rows,
matching `latest_only()`'s "max over all rows" semantics regardless of file
order. Deliberately bypasses the read cache — caching a filtered slice
under the whole file's cache key would hand a later full-history reader a
wrong answer. `keep(row)`, when given, filters BEFORE the newest-stamp
comparison — needed for `load_lineups_latest()`, where "newest" must mean
newest row from ONE probable-XI source, not newest across every source in
the file.

## `run_now()` — one clock per run

Twenty-five call sites used to ask the clock themselves, so one report
could stamp 23:52 while the document explaining it stamped 23:53, and a
rival's credited cash grew between the stage that scored him and the stage
that printed him — never a wrong answer, but it made outputs undiffable:
re-running unchanged code moved nine fields, so "nothing moved" could only
be eyeballed, never asserted. `run.py` runs every reporting stage in ONE
interpreter, so one sample here is one instant for the whole report. Not
for the sweep — `ingest.py` fetches over minutes and needs the live clock,
and runs in its own process anyway. `LFG_NOW` pins it for a diffable
before/after run: with the clock held, running the pipeline twice over one
store produces byte-identical reports, so anything that moves is the
change under test, not the eleven seconds between runs. A measuring tool —
the timer and every real run leave it unset.

## `load_understat_players()` — per-season cache

Not yet wired into forecasting — captured deliberately unused, verified
against real data first (the same caution starters.csv's per-match minutes
and `resolve_fitness()` got). Cached per `(season, mtime, size)`, the same
invalidation `read_csv()` uses, because nothing in this pipeline writes
`understat_players.csv` mid-run. Without this, `score.build()` called in
with "2026"/"2025" five times in one session — a full re-filter + re-sort
of 39,606 rows each time — for an answer that cannot have changed since the
last call in the same run.

## `minutes_played()` — one column read in opposite directions

A STARTER's `minute` column is when he came OFF (blank = played the whole
match). A SUB's `minute` is when he came ON (blank = never did).
`ffcore.score._per_jornada_current` and `ffcore.startprob.observations`
both need exactly this and used to each carry their own copy.

## `load_deadline()` — the fixture list IS the deadline, no typed fallback

The next kickoff in fixtures.csv is the whole deadline, not a floor — the
app locks the lineup once per jornada, so a player whose own match is
Sunday is already frozen at Friday's kickoff (verified in-app, 2026-08-16,
issue #28). The typed fallback (`inputs/deadline.txt`) is gone: it was read
when no fixture was available and was wrong the moment it expired,
undetected until a lapsed date made a report say "deadline passed" for a
locked-open squad. None means "the report doesn't know", never a
substitute number wrong in an undetectable way.

## gated API feeds — one shared reason, not five copies of it

`stale_feeds()`/`load_elo()`/`load_api_teams()`/`load_api_standings()`/
`load_api_lineup()`/`load_api_offers()` all gate on the same freshness
rule (`EVERY_RUN_FRESH_DAYS`/`DAILY_FRESH_DAYS`) for the same reason: a
gate that just hands back `[]` reads downstream as "nothing there", not
"the feed went quiet" — with the market feed three days old, a report once
said "market 0th percentile, a poor week" and printed no BUY list at all,
an emptiness presented as a finding (measured by ageing the store three
days and generating). `EVERY_RUN_FRESH_DAYS=0.6`, not 0.5: `lfg.timer`
fires at 00:40/11:40 local, so the two legs are 11h/13h plus up to 5
minutes of `RandomizedDelaySec` — a feed answering every sweep is 13h10m
old at its oldest, which 0.5 would call dead every night. Each gated loader
has its own concrete incident behind why gating matters for that table
specifically (a 3-day-stale cash anchor, a 2-day Club-Elo outage, a
lineup read after a sale already changed it) — see the function's own
short docstring for its case; the mechanism is one, shared.

`last_api_standings()` and `load_api_players()` are the deliberate
exceptions: standings' points/position columns only ever grow (a stale
reading is incomplete, not wrong — the balance on the same row still goes
through the gated reader), and the player-id lookup only ever grows too
(a player sold weeks ago is exactly the one the activity feed still names).

## `price_agrees()` / `shared_names()` / `row_key()` — one tolerance, one id

`VALUE_TOLERANCE=0.05` used to be defined twice (here and in
`ffcore.league`), same figure, same evidence, never guaranteed to move
together. Evidence: across 70 owned players the two sources agreed to
within 0.2%; the one wrong join was out by 603% — three thousand times the
worst true disagreement — so anything between the two thresholds works.
`shared_names()` decides "does this name belong to more than one player"
off TODAY's market only (`latest_only`), because three separate indexes
(Market, the Scorer, the crosswalk) key the same rows and must agree, or a
key built by one misses in another — a history-wide answer once let
decide.py share a name between a departed player and an arriving one.
`row_key()` prefers the site's own numeric id (`ff_id`, present on all
44,912 rows of market history, 666 distinct players including the two Iker
Muñoz) over the old name/name@club scheme, which existed only because the
id beside the name went unread.

## `Market.key_for()` / `.candidates()` — the shared-name refusal, and why

A name two players share resolves only when something else says which (the
club, or a price nobody else's value agrees with) — real case: two Álvaro
Garcías, 20.23M at Rayo and 0.50M at Villarreal, sharing a key would have
meant sharing a price history, with a lookup returning whichever the index
happened to hand back. `candidates()` exists because callers used to run
`ffcore.text.resolve()` over a raw row list instead, whose exact-name index
holds ONE row per name — so a shared name arrived as a confident single
match (the ambiguity hidden) and was then keyed `norm(name)`, which this
index does not contain (a shared name is keyed on club). A guess and an
unusable key in one step. `_by_price()` never returns a preference between
two agreeing candidates, only one match or none — the whole point of
asking is to get one answer or no answer.
