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
check "src/ lines" 20175 \
  "$(find src -name '*.py' | xargs cat | wc -l)"

[ "$fail" -eq 0 ] && echo "concepts: no duplication regained" || echo "concepts: a concept regained a second implementation"
exit "$fail"
