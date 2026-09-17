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
check "src/ lines" 19303 \
  "$(find src -name '*.py' | xargs cat | wc -l)"

[ "$fail" -eq 0 ] && echo "concepts: no duplication regained" || echo "concepts: a concept regained a second implementation"
exit "$fail"
