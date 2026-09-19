#!/usr/bin/env bash
# tools/selftests.sh — run the repo's 29 self-test suites, 4 at a time.
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
       ffcore/startprob.py ffcore/crosswalk.py ffcore/market.py sources.py
       "ingest.py --selftest" "ffcore/league.py --selftest" ffcore/fixture.py
       ffcore/second.py ffcore/score.py ffcore/bid.py "digest.py --selftest"
       "xi.py --selftest" "slate.py --selftest" "points.py --selftest"
       "methodology.py --selftest" "report.py --selftest"
       "ledger.py --selftest" "decide.py --selftest" "sim.py --selftest"
       "crosswalk.py --selftest" "run.py --selftest")

if printf '%s\n' "${TESTS[@]}" \
    | xargs -P 4 -I {} sh -c "\"$UV\" run --frozen python src/{} >/dev/null 2>&1 \
        || { echo \"  FAILED: {}\"; exit 255; }"; then
    echo "selftests: ${#TESTS[@]} suites pass"
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
for t in "${TESTS[@]}"; do
    # shellcheck disable=SC2086
    if ! "$UV" run --frozen python src/$t >/dev/null 2>&1; then
        echo "  FAILED: $t" >&2
        # shellcheck disable=SC2086
        "$UV" run --frozen python src/$t 2>&1 | tail -20 >&2
        status=1
    fi
done
exit "$status"
