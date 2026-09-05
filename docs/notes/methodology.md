# methodology.py — design notes

Long-form rationale moved out of inline comments 2026-09-05 (comment-volume
cleanup) so the source carries a one-line pointer instead of the full
narrative.

## start-grade-appearance-vs-real-starters

P(start) is the largest term in every xPts/j and nothing measured it. The
ground truth was already in the repo: the points page carries `games`, so
points.py's per-jornada diff names everyone whose appearance count went up,
and absence from an interval IS the answer — he did not play.

Two limits, stated in the report rather than smoothed over. `start_grade()`
grades P(APPEAR), not P(start), so a 20-minute substitute counts — which
flatters both sources equally, leaving the COMPARISON valid and the level
not. And an interval is the gap between two kept snapshots, usually one
jornada but sometimes two; the claim scored is the last one logged strictly
before it opened, the same no-hindsight rule the forecast join uses.

Graded universe is players the market prices. Team pages list academy
names the game does not carry, and counting those as misses would penalise
whichever source is more complete.

The appearance grading above was the best available while the only outcome
in the store was the points page's `games` column. starters.csv is the
real thing: the confirmed elevens off each played match page, so a
20-minute substitute is now a MISS rather than a hit, which is the
question both sources are actually answering.

**The cutoff is the jornada lock, not each match's own kickoff.** The app
locks the whole lineup once a round, so the last claim you could have
acted on is the one published before the round's FIRST kickoff. Grading a
Sunday starter against Sunday-morning news would credit a source with
information you were never able to use.

The lock is the earliest kickoff we OBSERVED for that round, which is the
same rule tidy.load_deadline() uses, and it comes from fixtures.csv — the
Analítica hub, which lists a match only until it starts. So a round whose
opener was played before this repo ever swept the hub has no lock, and is
reported as ungraded rather than given an assumed one. Where the true
opener was missed the cutoff can sit a few hours late; that flatters every
source equally, and the count of ungraded rounds is printed so the reader
can see how much of the sample it is.

## feed-freshness-bounds

How long a feed may go without answering before its age is the news. A
page asked for on every sweep and missing from the last one has failed; a
daily page has a day, and not much more, because the sweep runs twice a
day and missing both is not a cadence.

Both numbers (`FRESH`) are ffcore.tidy's, not a second opinion about them:
`load_elo()` and the three `load_api_*` readers REFUSE a reading older
than these, so a table calling one "ok" while the scorer had thrown it
away would be the exact contradiction this file exists to prevent.

`"every_run"` was 0.5 (days) here and nowhere else, and it was wrong: the
timer's legs are 11h and 13h, so this column called a feed that had
answered every sweep "13 hours stale" every night between 23:50 and the
00:40 run.

`"twice_daily"` gets the same bound as `"every_run"` and that is not a
mistake: the timer's two sweeps are 11h and 13h apart, both longer than
the 6-hour rule, so with this schedule a twice-daily page IS asked every
sweep and the 6 hours only stop a rerun from re-asking forty sites an hour
later.

## column-guide-ladder-and-league-table

The ladder and the league table were added to `column_guide_lines()` on
2026-09-01 — the two tables the board (decisions.json) and the site
actually draw now, and the ONLY explanation of them that existed anywhere
was two independent, hand-written copies that had already drifted apart:
sim.py's own `ladder()` carried the full version in markdown nobody
published reads, and src/pages/Fantasy.jsx on the site carried its own
shorter, separately-worded rewrite of the same facts — the exact "two
renderings of one answer" duplication `column_guide_lines()` was built on
2026-08-22 to stop, just never extended to the tables that replaced "Field
these eleven"/"What to bid" as the actual daily board. One copy now; both
renderers point here instead of carrying their own (Miguel: "do we need
all that long long text? shouldn't it go somewhere else?").
