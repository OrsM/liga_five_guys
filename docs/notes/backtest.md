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
