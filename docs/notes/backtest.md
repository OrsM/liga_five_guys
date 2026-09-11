# backtest.py — design notes

`src/backtest.py` is new this session (2026-09-06), so most of its own
docstrings already carry the full reasoning inline rather than a short
pointer here — this file exists for the one place that got long enough to
move out, and for future additions that follow the same pattern the rest
of this repo uses.

## csv_as_of() — not read_csv()'s cache, a deliberately separate path

`ffcore.tidy.read_csv()`'s own cache is keyed on the file's CURRENT
(mtime, size) on disk. A historical git blob has neither of those — it's
text read out of a past commit, not a file on disk at all — so reusing
the same cache dict under a path-only key would risk a live run's fresh
read colliding with a backtest's historical one, silently, the moment
both happened to touch the same tidy path in one process. Kept as its
own small, uncached reader instead: the same reason
`ffcore.tidy.read_csv_frozen()` stays separate from the live cache, for a
different isolation need (a snapshot immune to a later write, not a
snapshot from the past). The cost — no caching, `git show` runs fresh
every call — is real but small: this is a research tool run standalone,
not part of the live daily report (`replay_recommendations()` and its
siblings all confirm real speed is fine at their own real scale, tens of
git subprocess calls per run, not thousands).

## Why this file exists at all, not a bigger rewrite

The forecast-first rebuild plan's Stage 3 needed to know what the tidy
store actually looked like at a past point in time, to backtest a
candidate forecast approach with no hindsight. The more complete record
is `data/raw/dt=*.tar.xz` (every sweep's raw HTML) — but replaying THAT
means re-running `sources.py`'s parsers, `ffcore.crosswalk`'s join, and
everything downstream, for every historical point a comparison wants to
check. `lfg-run` already commits `data/tidy/*.csv` after every real run
(the parsed, crosswalked state, one commit per sweep) — `git show
<commit>:data/tidy/X.csv` reads that straight out of git, no re-parsing,
no re-joining. 187 real commits for `market.csv` alone, back to
2026-08-11, verified against this repo's own real history rather than a
synthetic fixture before anything downstream trusted it.

NO HINDSIGHT, BY CONSTRUCTION, is the property that makes this a real
backtest rather than a rewritten one: `commit_as_of(when, path)` only
ever looks at commits at or before `when`, so a row that arrives six
minutes after the moment being asked about cannot appear — the same
property a live forecast has for free, and a naive replay (running
today's code against `git show HEAD:...` and pretending that was "the
data as of last month") would lose without ever noticing.

## horizon_days — fixed window, not "episode to now"

`_grade_episodes()` originally scored every episode from its own day to
`now`, open-ended. Found broken by a 2026-09-11 swarm audit (Miguel wanted
"3 good proven tests, not a bunch of iffy ones"): an arm firing more
episodes, or firing earlier in the replay window, racked up more total
points independent of call quality, and once a rank slot's pick changed the
OLD episode kept scoring to `now` too — two episodes for the same slot
silently double-counting the same real jornadas. This was the actual
mechanism behind both `-94 vs +90` (`replay_ladder_percentile()`,
2026-09-06) and `+64 vs +31` (`replay_percentile_rank()`, same day) —
neither was a clean measurement. See `docs/notes/sim.md#ladder_rows--pts_lo-ranking-re-audited-2026-09-11`
for what changed once it was re-run correctly.

Fixed: every episode is now graded over `[when, when + HORIZON_DAYS]`, a
window every arm gets the same length of, regardless of when in the season
it fired or how many other episodes the same arm has fired since.
`HORIZON_DAYS=10.0` was picked empirically (2026-09-11): at ~5 real days
between jornada locks so far this season, 10 days is about 2 jornadas of
real runway, and still lets 193 of 235 real `reports/decisions.json`
commits clear it, versus 123 of 235 at a 21-day (~4 jornada) horizon. A
longer horizon is more "complete" per episode but starves every arm's n
given how little of the season has actually happened yet — revisit this
tradeoff as more jornadas lock and a wider window stops being so costly in
sample size.

`stats.bootstrap_gap()` (new, `src/stats.py`) replaced the bare
`total_net > total_net` verdict with a bootstrap CI on the two arms'
per-episode nets, exposed via `compare_arms()` — "BEATS" now means the 90%
CI on the gap excludes zero, not just that one number was bigger than
another at whatever n and noise level happened to be sitting there that
day.

## screen_audit — full historical checkout, not per-file reconstruction

Every OTHER function in this file (`replay_recommendations()` and its
siblings) can only re-rank candidates a historical `reports/decisions.json`
already contains — none check whether `decide.candidates()`'s own
expected-points prune (`decide.py`, the `bar_exp.get(c, 0.0) <= bar: continue`
line) ever discarded something better BEFORE it reached `rank()`. This is
the "full historical Universe reconstruction" this repo's project notes
flagged repeatedly as real, unbuilt, larger work.

It turned out simpler than that framing implied. `decide.load()`'s only
inputs are git-tracked (`data/tidy/*`, `inputs/cash.txt`,
`reports/decisions.json` — one `lfg-run` commit bundles all of them
atomically), and `ffcore.tidy.ROOT`/`TIDY`/`DECISIONS` all resolve relative
to `cwd`, not a hardcoded path. So instead of patching `csv_as_of()`
reconstruction through `ffcore.model.session()`'s whole object graph (not
threadable cleanly — checked, `model.session()`/`load_market_latest()` etc.
take no "as of" argument), `screen_audit_episode()` does a real `git
worktree add --detach` checkout of the historical commit, then runs a small
script via `sys.executable` (this interpreter's own venv, no per-sample
`uv sync`) with `cwd` set to the worktree — that commit's OWN `decide.py`/
`ffcore`, unmodified, reading that commit's OWN data, in a fresh subprocess
(required: `decide.load()` memoizes per-process, so each historical sample
needs its own).

**What it currently checks, and the real limitation found running it
(2026-09-12):** a "near miss" is a candidate `candidates()` excluded that
day (expected points below the live XI bar) but not by much, AND
affordable by CASH ALONE (`price <= u.cash`, no swap/sale funding) — a
deliberate simplification to avoid re-implementing `candidates()`'s own
spare-selling funding-chain logic. Sampling every 10th of 235 real
`reports/decisions.json` commits found: the first ~15 predate `current_xi()`
existing at all (script fails cleanly, `"error": "..."`, doesn't kill the
run — the API itself hadn't stabilized yet that early), and every one of
the 9 samples that DID run clean (2026-08-24 onward) found **zero**
cash-affordable near-misses — even re-checked at `near_miss_frac=0.0` (ANY
excluded candidate, however far below the bar) on one real day, still
zero. That is NOT strong evidence the screen never misses a winner — this
project's own real reports show cash is typically thin relative to any
real candidate's price (most real BUY/RAID rows are funded by a sale, not
outright cash), so "cash-affordable" alone is close to an empty set on a
typical day almost BY CONSTRUCTION, independent of whether the screen's
bar is well-placed. The honest reading of today's run: infrastructure
works, is safe (git worktree cleaned up in `finally` even under
`subprocess.TimeoutExpired`; verified with a real OOM-kill mid-run this
session — no dangling worktree survived, `git worktree list` came back
clean once `git worktree remove --force` was re-run by hand), but the
CURRENT near-miss definition is too narrow to say much yet.

**The real next step, not yet built:** extend the near-miss set to
swap-funded candidates too (reusing `candidates()`'s own
`spare[:6]`-cheapest-first funding logic, not a new invention) before
treating "zero near-misses" as a real finding about screen quality rather
than an artifact of only checking the rarer cash-only case. `NEAR_MISS_FRAC`
(0.85) itself was NOT the limiting factor — re-run at 0.0 on a real day
still found nothing, so the funding restriction is the actual bottleneck,
not the score-closeness threshold.

**Memory note for this box specifically:** a full 24-sample run OOM-killed
once on this machine's ~3.7GB RAM (`free -h` showed under 1GB free even at
idle) — `replay_screen_misses()`'s in-memory loop is fine on a bigger box,
but here a checkpointed, resumable driver (append each result to a JSONL
file, skip already-done shas on restart) was needed to survive a kill
without losing progress. Worth keeping in mind before re-running a large
sample on this same machine.
