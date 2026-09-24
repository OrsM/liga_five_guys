#!/usr/bin/env bash
# tools/selftests.sh — run the repo's self-test suites: 26 independent ones,
# 4 at a time, plus xi/report/decide/sim, which all call session() (the
# whole real-data forecasting build, ~100-165s the first time, ~free after)
# and used to each pay that first-call cost separately as 4 different `uv
# run` processes -- tools/session_selftests.py runs those four in ONE
# process instead, so only the first one actually builds it (measured
# 2026-09-23: 4 separate ~9min sim.py-sized runs down to one 9min run
# covering all four). It runs alongside the other 26, not after, since nether
# waits on the other's files.
#
# The TESTS array is copied verbatim from ~/.local/bin/lfg-run (read-only
# reference; never edited by this script). On failure, the failing suites
# are re-run serially so the output says WHY, not just which.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

UV="$HOME/.local/bin/uv"
export PYTHONPATH=src
export FF_ROOT=./data

TESTS=(ffcore/parse.py ffcore/text.py ffcore/schema.py ffcore/tidy.py
       ffcore/fixtures.py ffcore/auth.py
       ffcore/model.py ffcore/attributes.py
       ffcore/forecast.py ffcore/season.py ffcore/render.py
       ffcore/startprob.py ffcore/lineupweight.py ffcore/crosswalk.py sources.py
       "ingest.py --selftest" "ffcore/league.py --selftest" ffcore/fixture.py
       ffcore/second.py ffcore/score.py ffcore/bid.py "digest.py --selftest"
       "slate.py --selftest" "points.py --selftest"
       "methodology.py --selftest"
       "ledger.py --selftest"
       "crosswalk.py --selftest" "run.py --selftest" "flip.py --selftest")
SESSION_TESTS=(xi.py report.py decide.py sim.py)

session_ok=0
"$UV" run --frozen python tools/session_selftests.py >/dev/null 2>&1 &
session_pid=$!

if printf '%s\n' "${TESTS[@]}" \
    | xargs -P 4 -I {} sh -c "\"$UV\" run --frozen python src/{} >/dev/null 2>&1 \
        || { echo \"  FAILED: {}\"; exit 255; }"
then
    tests_ok=0
else
    tests_ok=1
fi
wait "$session_pid" || session_ok=1

if [ "$tests_ok" -eq 0 ] && [ "$session_ok" -eq 0 ]; then
    echo "selftests: $(( ${#TESTS[@]} + ${#SESSION_TESTS[@]} )) suites pass"
    # AND THE DUPLICATION GATE, in the same breath, because lfg-run already
    # refuses to publish when this script fails and nothing else about the
    # concept ratchet was automatic. It was run by hand, which means it was
    # run when somebody remembered -- and on 2026-09-19 a second
    # premium-over-market-value fitter went in while every gate that DID run
    # passed it. Miguel: "I don't want to be checking after".
    #
    # concepts.sh is seconds and reads no data, so it costs a report nothing
    # and cannot fail because the league moved. regress.sh deliberately
    # stays out: it compares against a recorded baseline and would fail
    # every time the market legitimately changed.
    exec bash "$(dirname "${BASH_SOURCE[0]}")/concepts.sh"
fi

echo "selftests: failures detected — re-running serially for detail" >&2
status=0
for t in "${TESTS[@]}" "${SESSION_TESTS[@]/%/ --selftest}"; do
    # shellcheck disable=SC2086
    if ! "$UV" run --frozen python src/$t >/dev/null 2>&1; then
        echo "  FAILED: $t" >&2
        # shellcheck disable=SC2086
        "$UV" run --frozen python src/$t 2>&1 | tail -20 >&2
        status=1
    fi
done
exit "$status"
