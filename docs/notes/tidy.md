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

## `_cached_latest_snapshot` — cache the small result, not the whole file

Caches `load_market_latest()`/`load_lineups_latest()`'s own RESULT (the
already-filtered "now" slice), not the file `latest_snapshot()` reads —
`latest_snapshot()` itself stays a bounded-memory forward pass with no
cache of its own (see its own note above on why: caching a filtered
slice under the whole file's cache key would hand a later full-history
reader a wrong answer). But `load_market_latest()`/`load_lineups_latest()`
are each called several times over one `run.py` process (`load_players()`
alone, plus every stage that wants "the market as of now") and, with no
cache of their own, each call re-walked the entire multi-decade file
again for an answer that cannot change mid-run — measured 6-8 calls to
the same file on a real report. `cache_key` disambiguates callers like
`load_lineups_latest(source=)` that pass a fresh `keep` lambda every
call (so keying on `keep` itself would never hit); same mtime+size
invalidation and same copy-on-return guarantee as `read_csv()`'s own
cache, for the same reason.

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

`load_deadline()` itself calls `JornadaClock.next_deadline()`, not
`next_kickoff()` — a round already under way can still have later matches
listed in fixtures.csv, staggered days apart (a TV reschedule, or a
weekend spread), and those are leftovers of an already-locked round, not
a fresh deadline. Using `next_kickoff()` here once reported a lock
minutes away for a match that was really just one of jornada 5's
remaining fixtures, days after jornada 5 itself had already locked
(2026-09-13).

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
old at its oldest, which 0.5 would call dead every night. `fresh_only()`
itself is the shared gate mechanism — a stopped feed's last rows still
parse and still join, so nothing but the observed-at stamp can tell it
apart from a live one, checked here once rather than trusted by every
caller. A reading stamped in the FUTURE is not stale: a clock a few
minutes out is a machine problem, and throwing away good data over it
would be worse than the skew. Each gated loader also has its own concrete
incident behind why gating matters for that table specifically:

- **`load_elo()`** — Club Elo's API host died on 2026-08-17 and kept
  answering with the same twenty ratings for two days; they covered every
  club, so `elo_strength` succeeded and the fixture board ranked the
  league on form from before the jornada. Falling back to squad value is
  a worse ranking and an honest one.
- **`load_api_teams()`** — a dead token doesn't empty this file; the tidy
  store keeps the last good reading forever, so the failure mode is a
  squad three days old that joins perfectly and prices a market that's
  moved. The stale-Elo failure a second time, with the app in the
  typist's chair.
- **`load_api_standings()`** — this row carries the balance;
  `ffcore.league` anchors the cash estimate on it in preference to
  anything typed. An anchor calling itself observed while three days old
  is the exact bug the allowance fix was about.
- **`load_api_lineup()`** — a lineup read three days late is a lineup for
  a round already played; `inputs/lineup.txt` held this by hand until
  2026-08-19 and was deleted because the only runs that ever read it were
  runs where a sale had already made it wrong.
- **`load_api_offers()`** — an offer read three days ago isn't an offer
  today; treating it as one shows a shortfall closed by money that may
  already be withdrawn or accepted.

`last_api_standings()` and `load_api_players()` are the deliberate
exceptions: standings' points/position columns only ever grow (a stale
reading is incomplete, not wrong — the balance on the same row still goes
through the gated reader), and the player-id lookup only ever grows too
(a player sold weeks ago is exactly the one the activity feed still names).

## `load_api_activity()` — an event log read whole, not latest_only

Sorted by the app's own timestamp, not by observation: this is a history,
and the order that matters is the order the deals happened. Used to read
through `latest_only`, and it worked only by accident — the feed
republished every event on every sweep and the store kept every copy, so
"the newest snapshot" happened to contain the whole season, at quadratic
storage cost. Once that duplicate storage was fixed (`sources.STORE_ONCE`),
`latest_only` here would have hidden the whole season behind the handful
of deals done since the last sweep, deleting the rest of it from the
ledger's view.

## `pending_received()` — a real bid is a floor, never an overwrite

What a caller otherwise prices a sale at is the market's own valuation —
an estimate nothing has tested. A real pending offer sitting on a player
you've actually listed is ground truth for at least that much, so it's
applied as a FLOOR on `proceeds`, never an overwrite (another bidder
could still beat it before you act).

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

## `load_matches()` / `load_starters()` — measured safe for latest_only

Added because the module owned no loader at all for the four most-read
tables (matches.csv opened by hand at 13 call sites, starters.csv at 7)
and the question "does this table accumulate snapshots, and do I want only
the newest" was being decided per call site instead of once. For these two
tables the answer is settled and safe: measured 2026-09-16, matches.csv
holds 75,240 rows behind 380 real matches (198x repeated snapshots),
starters.csv holds 180,372 rows behind 2,512 real (match, player) keys
(~72x repeated) — `latest_only()` keeps 380/380 and 2,512/2,512
respectively. Nothing is lost, so `load_matches()`/`load_starters()`
apply it unconditionally rather than leaving it to the caller.

`load_matches_history()` exists alongside `load_matches()` because not
every caller of matches.csv can take the latest-only cut: `points.py`'s
`match_jornadas()` (`points.py:227`) needs the FULL snapshot history to
find the first time each match's score appeared, which is this repo's
only record of when it actually finished. Collapsing that read to
`latest_only()` would silently answer every match with its most recently
observed timestamp instead of the one that matters. Two loaders, not one
with a flag, so a caller cannot get the wrong one by leaving an argument
at its default.

## `load_api_stats()` — why latest_only is forbidden here

The one table where the matches/starters reasoning above does NOT apply,
which is exactly why it needs saying loudly rather than left to be
rediscovered. Measured 2026-09-16: api_stats.csv holds 11,190 rows behind
10,857 distinct `(player_id, week, stat)` keys — almost no duplication,
because each sweep's newest snapshot only reports the handful of
gameweeks the API happens to be serving that moment (232 keys), not the
table's accumulated history. `latest_only()` on this table would keep 232
of 10,857 keys — 98% of every stat this table has ever recorded would
silently vanish, and nothing about the code would look wrong: it would
just quietly know almost nothing about most weeks for most players.

`latest_per_key(rows, key_fn)` is the primitive this needed and
`latest_only()` could not become without changing what every other caller
of it means: `latest_only()` answers "what does the single newest
snapshot say", which is correct exactly when one snapshot covers the
whole keyspace (matches, starters, lineups, market). `latest_per_key()`
answers "what is the newest row for EACH key, wherever in the history it
lives" — the only question that makes sense when a table's coverage is
scattered thinly across many snapshots instead of concentrated in the
latest one. `load_api_stats()` is `latest_per_key()` applied to
`(player_id, week, stat)`; the self-test's guard asserts it returns more
keys than a `latest_only()` read of the same file would, specifically so
that "simplifying" this loader back to `latest_only()` fails loudly
instead of shipping a quiet 98% data loss.

## `clock()` — one JornadaClock per process

Before this, `JornadaClock` was constructed 11 times across 5 files
(`methodology.py` alone at lines 259, 306, 597, 1385, 1922 and 1943),
each its own fresh parse of matches.csv (75,240 rows) and fixtures.csv —
the same reparse of an answer that cannot change mid-run, paid over and
over. `clock()` follows the same one-value-per-process pattern `run_now()`
already uses (a module-level list as the memo cell, appended to once,
never invalidated mid-process): a run that asks "when does this lock"
ten times gets the identical `JornadaClock` ten times, not ten
independent parses. Built from `load_matches()` + `load_fixtures()` —
the `latest_only()` matches read, which is what every existing hand-built
`JornadaClock` construction site already passes in.

## `jornada_of_match()` — first-write-wins, matching score.py:538, not methodology.py:588

A `{match_id: jornada}` map has been hand-built at least twice with two
different, disagreeing rules. `ffcore.score._per_jornada_current()`
(score.py:538) writes it as `if mid and mid not in jornada_of_match:
jornada_of_match[mid] = ...` — FIRST write wins, so whichever jornada
value a match_id was first recorded under is the one that sticks across
every later snapshot. `methodology.start_intervals()` (methodology.py:588)
writes the same-shaped map unconditionally — LAST write wins instead.
These are not the same function accidentally spelled two ways; they can
disagree in practice for a match_id whose jornada value actually changes
across snapshots (a rescheduled fixture spanning a round boundary, for
instance), and nothing today reconciles them.

`jornada_of_match()` matches `score.py`'s semantics ONLY, because that is
the rule this task was told to preserve exactly — it does not attempt to
settle which of the two pre-existing behaviours is correct, and it must
not be pointed at as a drop-in replacement for `methodology.py`'s own map
without checking, call site by call site, that first-write-wins is what
that caller actually wants. Built off `load_matches_history()` (the full
snapshot history), not `load_matches()` — a single-write rule is only
meaningful when there is more than one snapshot per match_id to choose
from, and score.py's own read of matches.csv for this purpose is
similarly the unfiltered file, not the latest-only cut.
