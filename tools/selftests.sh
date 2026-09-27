#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

UV="$HOME/.local/bin/uv"
export PYTHONPATH=src
export FF_ROOT=./data

TESTS=(ffcore/parse.py ffcore/text.py ffcore/tidy.py
       ffcore/fixtures.py ffcore/auth.py
       ffcore/forecast.py ffcore/season.py ffcore/render.py
       ffcore/startprob.py ffcore/crosswalk.py sources.py
       "ingest.py --selftest" "ffcore/league.py --selftest" ffcore/fixture.py
       ffcore/score.py
       "grading.py --selftest"
       "ledger.py --selftest"
       "crosswalk.py --selftest" "run.py --selftest" "flip.py --selftest"
       ffcore/action.py ffcore/pricing.py ffcore/schedule.py
       "stats.py --selftest"
      )
SESSION_TESTS=(decide.py sim.py)

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
    exit 0
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
