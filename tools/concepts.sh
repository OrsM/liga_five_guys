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
# exceptions below -- lower one whenever you retire a site.
#
# Claude: never raise a budget yourself to make this pass, on this check or
# any added later. Setting your own bar to whatever you just produced is
# not verification. If a change genuinely needs a budget higher, stop and
# ask the user -- don't edit the number.
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

[ "$fail" -eq 0 ] && echo "concepts: no duplication regained" || echo "concepts: a concept regained a second implementation"
exit "$fail"
