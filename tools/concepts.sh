#!/usr/bin/env bash
# tools/concepts.sh -- a RATCHET on concept duplication.
#
# The byte-identical gate (tools/regress.sh) proves a change did not break
# the pipeline. It cannot prove a migration FINISHED: a half-migrated
# concept, with the old spelling still live beside the new one, passes it
# perfectly. That is how waves 1-2 left the repo larger than they found it.
#
# This counts the OLD spelling of each unified concept and fails if the
# number goes UP. Budgets are the current count plus the documented
# exceptions below -- lower one whenever you retire a site, never raise one
# without saying why in the same commit.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

fail=0
check() {   # name, budget, count
    local name="$1" budget="$2" n="$3"
    if [ "$n" -gt "$budget" ]; then
        printf '  FAIL  %-28s %d old-style sites (budget %d)\n' "$name" "$n" "$budget"
        fail=1
    else
        printf '  ok    %-28s %d/%d\n' "$name" "$n" "$budget"
    fi
}

# Hand-built JornadaClock. Budget 7: tidy.py's clock() and clock_history()
# builders plus its guard and selftest (4), methodology.start_intervals()
# (parametrised by design -- its own tests feed it synthetic rows, so a
# process-memoized global would break their isolation), and two
# methodology selftests built from literal rows.
check "hand-built JornadaClock" 7 \
  "$(grep -rc 'JornadaClock(' --include='*.py' src/ | awk -F: '{s+=$2} END{print s+0}')"

# Raw store reads that have a loader. Budget 6: the loader definitions
# themselves in tidy.py, its selftest, and score.py's _calibrated(), which
# genuinely needs the EARLIEST snapshot, not the newest.
check "raw matches/starters read" 6 \
  "$(grep -rc 'TIDY / "matches.csv"\|TIDY / "starters.csv"' --include='*.py' src/ \
     | awk -F: '{s+=$2} END{print s+0}')"

# (r.get(x) or "").strip(). Budget 54: 20 in sources.py (UPSTREAM feed
# fields -- football-data's HomeTeam, the odds API's commence_time -- not
# our own columns, deliberately excluded), 1 in schema.py (the
# implementation), 33 in files no migration wave has reached yet.
check "hand-written field read" 54 \
  "$(grep -rEc '\(\s*[a-z_]+\.get\([^)]*\)\s*or\s*""\s*\)\.strip\(\)' --include='*.py' src/ \
     | awk -F: '{s+=$2} END{print s+0}')"

# WHAT A PLAYER COSTS, derived in more than one place. This is the check
# that was missing on 2026-09-19, when a second premium-over-market-value
# fitter went into methodology.py while ffcore/bid.py already had one -- a
# better one, with a lag guard on the quoted value. Every gate passed,
# because the others count duplicated IDIOMS and lines, and neither can see
# a function that recomputes what another module already derives.
#
# The contract: a premium over market value is computed in ffcore/bid.py and
# nowhere else. Everything else renders it. Budget 2, and both are named so
# a third has to be argued for rather than added: ffcore/bid.py, which owns
# it, and ffcore/tidy.py's value-delta helper, which answers "how has his
# price moved" rather than "what did he go for".
check "premium over market value" 2 \
  "$(grep -rEc '/ *[a-z_.]*value[a-z_]* *- *1' --include='*.py' src/ \
     | awk -F: '$2>0 {n++} END{print n+0}')"

# Per-player facts stored outside PlayerProfile. Budget 0 -- this one is
# finished, and must stay finished.
check "Universe flat-dict storage" 0 \
  "$(grep -c 'InitVar\[' src/decide.py || true)"

# SIZE RATCHET. The concept counts above cannot see the failure this whole
# exercise actually hit: eight waves that each gave a concept one home,
# each passing every gate, and together adding 1,696 lines to a codebase
# the work was meant to shrink. An empirical study of AI agents refactoring
# (arXiv 2511.04824) reports exactly that as the characteristic failure --
# agents grow size and complexity where humans shrink it -- and recommends
# a hard constraint on complexity change before starting. This is it.
#
# Raise the budget only in a commit that says why, in words, and only for
# work that adds a capability rather than tidies an existing one.
#
# 19212 -> 19303 on 2026-09-17: over/under odds collection. A NEW
# CAPABILITY -- the feed was already being paid for daily and half of
# what it returns was being thrown away -- so it qualifies under the
# rule above. Most of the 91 lines are the parser's own explanation and
# its self-test.
#
# 19192 -> 19212 on 2026-09-17: the conditional-predictor correction to
# drift_frac_from_history(). A forecasting bug fix, not a tidy, and 20 of
# its lines are the comment recording WHY the two fitters must use the
# same predictor -- which is the knowledge that was lost the first time.
# 19473 -> 19625 on 2026-09-18: live bids became a LIST rather than a
# total netted out of cash. A NEW CAPABILITY, not a tidy -- the app never
# debits a bid and never refuses one you cannot cover, so nothing in the
# system could say "this bid is no longer worth keeping" or "these bids
# cost more than you hold". Both are now said. Most of the lines are the
# self-test for bid_lines() and the two comments recording the evidence
# (the app's own balance feed) that placing a bid does not spend the money.
# The endorsement rule then had to become budget-aware -- the ranking scores
# one move at a time, so it wants two men you can pay for one of -- and
# alerts.md had to stop carrying a second copy of every standing alert,
# which was burying the new line under its own history.
# 19625 -> 19655 on 2026-09-18: shown() -- one place that renders a clock
# for a person, in Madrid, with the offset READ rather than assumed so the
# October change cannot leave it an hour out all winter. Seven display sites
# that each spelled their own strftime now call it, so this REMOVES a
# duplicated idiom; the added lines are its docstring (why data keeps UTC)
# and a self-test that pins both sides of the DST change.
# 19655 -> 19990 on 2026-09-18: parse only walks the whole history when it
# CAN change the answer. A NEW CAPABILITY, not a tidy -- measured at
# jornada 6 of 38 the full walk peaked at 874MB against a 900MB MemoryMax
# and was being throttled on every run, and it grows with the archive.
# Parser signatures are the guard: if one moves, the rows it produced are
# stale and the full walk runs exactly as before. Most of the added lines
# are _append_csv (which refuses rather than guesses) and its self-test,
# plus the docstrings recording why the full walk is worth keeping at all.
# Measured: full 874MB/11.5s, tail 63MB/1.9s, no-op 53MB/0.4s, tidy tables
# byte-identical across all three.
# 19990 -> 20040 on 2026-09-18: newest() and table_stats(). Both REMOVE a
# duplicated idiom rather than add one -- thirteen call sites had each
# hand-rolled latest_only(read_csv(x)), materialising a whole history to
# keep its last snapshot, and the freshness table read five history tables
# in full to print a row count and a timestamp. The streaming reader they
# now share was already in this file and already used by
# load_market_latest(). Measured: pipeline 55.8s -> 29.5s, peak 672MB ->
# 468MB, and table_stats agrees with len(read_csv())/max across all 23
# tables on both its cached and its streaming branch.
# 20040 -> 20120 on 2026-09-18: the full parse walk spills rows to disk
# instead of holding every table until the last snapshot. Full rebuild peak
# 874MB -> 466MB, tidy tables byte-identical across all 23. This is what
# lets the systemd caps come DOWN rather than up -- the limits exist for a
# reason and the pipeline has to fit them, not the other way round.
# 20120 -> 20140 on 2026-09-18: _lines_name(). There are TWO parse caches
# -- pages and points, points.py passing its own filename to the same
# helpers -- and the line format ignored the argument, so points read the
# pages' cache, missed every entry, and wrote its own 88 over the top. Every
# full walk then re-parsed all 3,383 documents: 395s where it should be 30s.
# The fix is the name, the lines are its self-test and the note that says
# what it looked like (a slow rebuild) rather than what it was.
# 20140 -> 20175 on 2026-09-18: _gains(). The BUY and RAID sections were
# gated on the par floor alone, so the ladder offered "buy Pape Gueye, sell
# Alonso" at par +56 while the SAME row carried d_pts -2.0 and the
# recommendation, which has always screened on d_pts, left him out. par is
# per-player and season-long; d_pts is what happens to your squad once the
# sale that funds him goes too. Both questions now have to be answered yes.
# Lines are the docstring recording that distinction and the self-test.
# 20175 -> 20300 on 2026-09-18: the clause buyout, and the end of silent
# drops. parse_api_activity skipped any activityTypeId it had no name for,
# so type 1 -- one manager taking another's player at his release clause --
# was scraped from day one of the season and thrown away every run. Two had
# happened; neither reached the ledger, so Albert Laporta showed 141M he had
# in fact spent on Raphinha. An unknown type is now KEPT and named
# (unknown:<id>) rather than discarded, because the allowlist was right and
# the silence was not. An audit of every snapshot ever taken says type 1 was
# the only gap, and the same audit run over the lineup feed says
# tacticalFormation is its only non-slot key and is already read.
# 20300 -> 20450 on 2026-09-19: jornada_bounds() -- the best and worst legal
# eleven a manager could have fielded from the squad he owned when a round
# locked, against what those players actually scored. A real award has to sit
# between them, so anything outside is an illegal side or the app docking a
# manager who was overdrawn at the lock. An INDEPENDENT reading of solvency,
# owing nothing to our own ledger. Reuses csv_as_of + latest_only for the
# historical squad, load_api_stats for the scores and best_xi for the shape
# rules; the lines are its self-test and the note that api_stats is a
# per-STAT breakdown, which cost two wrong runs before it was summed.
# 20450 -> 20495 on 2026-09-19: the OFFERS block. Six live bids on players
# Miguel owns were lifting their proceeds inside the simulation and saying
# nothing anywhere -- and the biggest, 48.07M for Fornals at 8% over market,
# could never have shown, because a settled starter is collapsed into the
# eleven's summary line and gets no row. A bid is now judged against the two
# numbers that make it good or bad: what he fetches today, and what was paid
# for him. One section, so the offer is stated once rather than sprinkled
# across whichever rows happen to appear.
# 20495 -> 20575 on 2026-09-19: clearing_premium(). A bid is the ONLY way a
# player leaves your squad for money -- 68 logged sales against market value
# make one smooth hump with no spike at 1.00, and no sale ever pairs with
# another manager's buy -- so judging a bid against the quoted value was
# judging it against a price nobody trades at. The going rate is now fitted
# each run (+3.6% at 68 sales) and the offer is measured against that. The
# purchase-price comparison came OUT: it is a sunk cost and had no business
# in the decision.
# 20575 -> 20610 on 2026-09-19: transfer_premium() replaces
# clearing_premium(), and it is ONE fitter for what had been drifting into
# two questions -- what a bid on your player is worth, and what to bid for
# someone else's. Both are the premium over market value at which a player
# changes hands, so both read the same fit, conditioned on how contested
# the listing is and SHRUNK toward the unconditional median by a
# pseudo-count. The shrinkage is the point: two contested sales at +40% must
# not become a +40% recommendation, and the cell earns its voice as the
# season fills in without anyone re-deciding the number.
# 20610 -> 20638 on 2026-09-23: three real perf fixes, not a duplicated
# concept -- Market.at()/series() re-parsed a player's whole price history
# from scratch on every call (4.6M redundant money() calls, 76s of a 196s
# run, from sec_drift() alone calling drift() once per deal per horizon);
# load_understat_players()'s cache key included `season`, so the same
# season's several callers each re-read and re-sorted the ~160k-row CSV;
# load_lineups() had no cache at all and re-read the ~174k-row CSV on
# each of 3 calls in one build(). All three now parse once and are reused.
# 20638 -> 20754 on 2026-09-23: STORE_DAILY. market.csv/lineups.csv/
# understat_players.csv write a full row per key every scrape round
# regardless of whether anything changed -- measured 83.7%/86.5%/98.9%
# duplicate consecutive rows, a 5-8x amplification on every reader that
# re-parses these files (Market, load_market_frozen, load_lineups,
# load_understat_players). A NEW CAPABILITY, not a tidy: a key's row for
# today is now overwritten in place rather than appended, so N rounds in
# one day net one row. _compact_daily() is the one function both the full
# rebuild and the tail-append path call, so retroactive compaction of the
# existing history is just running a full rebuild, not a second script.
# Most of the added lines are _append_csv_daily (refuses on a shape change,
# same as _append_csv) and its self-test.
# 20754 -> 20763 on 2026-09-24: run.py timed gc.collect() outside the
# per-stage timer it fed, so ten forced collections on a memory-pressured
# box (swap in use) turned into a 332.7s gap between the printed per-stage
# sum (365.7s) and the printed total (698.4s) with no stage to blame it on.
# A bug fix, not a tidy -- the lines are the comment recording why the
# collect stays (this repo runs under a 750M MemoryMax) while its cost
# moves inside the timer it always should have been in.
# 20763 -> 20788 on 2026-09-24: run.py names where a stage that takes over
# 8s spent it (a 0.25s sampler of the main thread's innermost src/ frame).
# `squads` took 38s in scheduled runs and 2.2s by hand on the same data in
# a fresh process under the same MemoryHigh/MemoryMax, so the number alone
# cannot say what differs and cProfile's overhead would distort the run.
# Printed only for a slow stage, so a healthy run's log is unchanged.
# 20788 -> 20795 on 2026-09-24: score._calibrated() keyed its cached startprob
# fit on len(starters.csv), which grows every scrape, so it refit on every
# scheduled run -- 36s of the 38s `squads` stage, found by run.py's new
# sampler. Now keyed on a hash of the observations (0.11s to build), so it
# refits only when a match result actually changes them. Same values.
# 20795 -> 20822 on 2026-09-24: ingest.fetch() slept 1.5-3s after every
# public request, serially across hosts, so 52 requests to six sites cost
# ~115s of a 122s fetch. Now paced per host (_by_host + a per-host clock):
# each site still waits 1.5-3s between its OWN requests, and the waits
# overlap. Simulated 123s -> 52s on the real source list.
# 20822 -> 20866 on 2026-09-24: ingest._parse_everything() parsed every
# document serially -- 376s for a cold-cache rebuild, of 458s total -- though
# each is a pure function of one snapshot. Now one worker per physical core
# (_parse_origin/_parse_workers), each opening its own snapshot so only rows
# cross the pipe, and never more than the service's memory cap allows. Output
# byte-identical to serial on the full history.
# 20866 -> 20882 on 2026-09-24: crosswalk._by_exact_value() rescanned the whole
# market history (~28k rows of float()) on each of 161 calls -- 2.9s of a 3.9s
# stage, found by profiling; now a value index built once per market. Output
# byte-identical (players.csv/clubs.csv). run.py's slow-stage threshold reads
# LFG_SLOW_S so a stage can be profiled in place, where shared caches make it
# behave as it does in a real run and not as it does standalone.
# 20882 -> 20884 on 2026-09-24: two comment lines recording why snapshots are
# compressed at xz preset 3 (1.6s vs 8.4s, +13% size) -- see ingest._write().
# 20884 -> 20887 on 2026-09-24: the "app says nobody holds him" warning now says
# WHY it matters (the feed has no sale, so that manager's cash counts his cost
# as spent) instead of leaving it to be worked out. Words only -- an attempt
# to infer the missing sales into the ledger was reverted as more error
# surface than the estimate is worth.
# 20887 -> 20924 on 2026-09-24: the app removes a player from a squad WITHOUT a
# feed sale, so the ledger kept the purchase and the cash estimate read it as
# money gone (Albert Laporta -67.35M included a 49.99M signing with no refund).
# League.dropped records those players once, where the ledger's and the app's
# ownership both exist, and the estimate prices them at market value as one
# more STATED ASSUMPTION in its math line -- beside the unmeasured-income one,
# valued AS OF the first roster snapshot without him (gone_at + Market.at),
# not today: the value at the time is what the app paid.
# The ledger itself stays exactly what the feed said (an inference layer that
# wrote guessed rows into it was prototyped and reverted).
# 20924 -> 20973 on 2026-09-24: the same refund is now priced AS OF the day he
# left, not today. gone_at() finds the first roster snapshot without him (a
# first-absence search over the roster history, ~10 lines); Market.at() -- which
# already priced purchases -- supplies the value then; the cash line prints the
# date and the value. Not the app's own value table: it only begins 2026-09-12,
# so an as-of lookup there silently returns a value from AFTER the event.
# 20973 -> 21254 on 2026-09-24: src/flip.py, a new stage. Buys free-market players
# whose value is drifting up, holds them, and sells non-starters when holding is
# expected to lose value or an offer beats it. Every constant in it was measured
# (44 days of values, 70 winning bids, 75 app offers) and is documented where it
# is set; the expectation table is recomputed from market.csv each run, so it
# needs no stored model. Recommendations are logged when made (flip_log.csv).
# 21254 -> 21336 on 2026-09-24: flip may only advise selling a player the REPORT would
# let go for free (its SELL group, or ~0 season points with no downside band);
# anyone else the market says to sell is shown as HELD BACK with what selling
# costs. The first version used this week's XI to decide who was dispensable and
# told me to sell four players the report said to keep (Yeray -6, Raul -3, Carl
# -0 with a -51 downside): selling a bench player gives up optionality the
# season simulation already prices. Plus a cooldown so a player sold this week
# is not bought back next.
# 21336 -> 21595 on 2026-09-24: flip.py rebuilt so no decision rests on a number or a
# sentence written into the code. The trade's cost (auction premium, offer
# discount) is measured each run; what an update of a given size leads to is a
# K=sqrt(n) nearest-neighbour lookup with uncertainty across CALENDAR DAYS (the
# market moves together; deciles put +2% and +20% in one bucket and said +32%);
# money's worth in season points is the report's own points-per-million; what a
# bench player costs the season is the simulation's expected (MEAN) change --
# now exported in the ladder, because a median hides optionality (Carl Starfelt:
# median -0.4, mean -4.7). No preferences: decisions use the expected drift (flip.py header).
# Sentences come from ONE function (say) fed structured reasons, and the page and
# ping draw the box's view. Same rule applied to the ladder: sim.GROUP_LABEL is
# the one copy of the group headings; the page had its own list and it had drifted.
# 21595 -> 21605 on 2026-09-24: flip reserves what the report's own TOP MOVE needs
# (its net cash out, clause premium included: 18.41M for a raid whose player is
# valued 11.89M) instead of a price I inferred from candidate rows; picks say how
# much more cash they need, and the ping lists only what can be afforded.
# 21605 -> 21179 on 2026-09-24: deleted code nothing called -- score.py's
# experiment scaffolding (backtest_predictor, walk_forward_compare,
# log_experiment, experiment_history, _precision_blend), the whole unused
# ffcore/market.py offer sampler, and sim._net. The ratchet only ever goes down.
# 21179 -> 21319 on 2026-09-24: ffcore/lineupweight.py (~140 lines with its
# selftest) replaces the reused constant SHRINK_K as the weight on a line-up
# percentage with one fitted walk-forward on points every run (k=2 vs 8, held-out
# MSE -0.9%, bootstrap over players excludes zero); tools/forecast_walkforward.py
# is its scorecard and imports the same code.
# 21319 -> 21400 on 2026-09-24: lineupweight.fit_status_factors measures what a
# "injured"/"doubt" flag means for a regular's minutes (0.54 / 0.60, where
# "injured" was hard-coded 0) instead of assuming it; the two-line table in
# score.status_multiplier applies it.
# 21400 -> 21397 on 2026-09-24: the daily 100k reward is counted in whole
# payments (floor of days, unmeasured income rounded to whole payments), not
# 100k x fractional days: the residual in the user's own balance is exactly
# 46 x 100k over 37.6 days.
# 21397 -> 21445 on 2026-09-24: backtest.track_record() (reused replay_recommendations)
# and its sim.py wiring -- the report now prints its own measured track record
# instead of nothing, guarded so a broken replay drops the line, not the run.
# 21445 -> 21480 on 2026-09-24: load_actuals() now keys on ff_id too --
# squad_log.csv (what its predictions are matched against) has keyed its own
# rows on ff_id since 2026-08-20, so name-only matching missed 98% of the
# real log; fit_rate_rel_floor and drift_frac_from_history went from ~50
# stale August pairs to 370 real ones.
# 21480 -> 21527 on 2026-09-24: one cached load_crosswalk() (tidy.py, same
# mtime/size discipline as read_csv()) replaces four independent readers of
# players.csv+clubs.csv (methodology.py's own _XW_CACHE, backtest.py,
# score.py's _calibrated(), methodology.py's forecast_claims()).
# 21527 -> 21660 on 2026-09-24: PROMOTED_DISCOUNT (0.70, stated but never checked
# per docs/notes/score.md) is now fitted each run against real promoted-team
# matches, shrunk toward the stated default so a thin/lopsided sample cannot
# swing it far -- measured 0.75 on 336 real matches, close to the guess.
# 21660 -> 21760 on 2026-09-24: flip.fund() -- every squad player (starter or
# bench) with a live received offer, priced in season points via the SAME
# ladder every other section reads (sim.band_acts() runs a stand-alone sale
# through the full season Monte Carlo, best XI re-optimised each trial), so a
# key starter (Fornals: -60.0 pts) and a fringe bench player (0.1 pts) are on
# one honestly-priced menu. Shown only when a pick or the reserve is short.
# 21760 -> 21838 on 2026-09-25: fund() ranks on net points (cost minus what
# the cash is worth at the report's own points-per-million, not cost alone)
# and prices the sell-now-vs-wait timing with the same Outlook model the buy
# side already uses -- a costly starter with a big offer can rank ABOVE a
# nearly-free bench player with a small one.
# 21838 -> 21883 on 2026-09-25: fund() trims to what covers the shortfall plus
# one further option, not the whole squad (listing all 17 read as "sell
# everyone"), and the section label states the real shortfall and says
# plainly it is not a recommendation; sim.ladder() stops rendering the
# "offer" group as a second listing of a player already shown elsewhere --
# FUND (flip.py) now owns that view. The DATA stays (flip.report_view()
# still reads it); only the duplicate markdown section is cut.
# 21883 -> 21893 on 2026-09-25: report.py stops re-deriving DEF/MED/DEL counts
# from the scorer's own rows -- a second implementation of exactly what
# sim.shape() already does from the Universe. The SAME class of "two
# computations of one fact" this codebase has already been burned by
# (report vs. workings disagreeing). The score TOTAL logged stays local on
# purpose: it is the Scorer's per-round rating, not sim.py's season xi_total
# -- methodology.py's own fits grade against that specific quantity.
# 21893 -> 21908 on 2026-09-24: rounds_left() orders remaining jornadas by real
# kickoff, not jornada number -- a rescheduled fixture (jornada 6, one match
# moved a month out for a European date) was anchoring the whole season
# forecast to a jornada that was, in practice, over. Reuses JornadaClock
# (tidy.py) rather than a fresh kickoff join: a first attempt at the join
# mismatched jornada 38 to an October kickoff (two legs, no jornada tag to
# disambiguate) and was reverted before shipping.
check "src/ lines" 21853 \
  "$(find src -name '*.py' | xargs cat | wc -l)"

[ "$fail" -eq 0 ] && echo "concepts: no duplication regained" || echo "concepts: a concept regained a second implementation"
exit "$fail"
