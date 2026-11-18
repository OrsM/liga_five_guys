#!/usr/bin/env bash
# The gate: every module under src/ and tools/ that defines a top-level
# _selftest() is run (nothing to list or keep in step), then mypy and ruff.
# Modules that gate their self-test on a flag take --selftest; the rest
# ignore it. A failure prints that suite's own output once.
# Every suite runs on an empty data root (and so an empty store) under ~/.cache: none sees or writes the live data/ or store/.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

UV="$HOME/.local/bin/uv"
export PYTHONPATH=src
FF_SCRATCH="$(mktemp -d "$HOME/.cache/selftests.XXXXXX")"
trap 'chmod -R u+w "$FF_SCRATCH" 2>/dev/null; rm -rf "$FF_SCRATCH"' EXIT
export FF_ROOT="$FF_SCRATCH/data"
export UV

mapfile -t SUITES < <(grep -rl --include='*.py' '^def _selftest' src tools | sort)

status=0
printf '%s\n' "${SUITES[@]}" | xargs -P 4 -I {} sh -c '
    out=$("$UV" run --frozen python {} --selftest 2>&1) \
        || { echo "  FAILED: {}"; echo "$out" | tail -20; exit 255; }' || status=1

for cmd in "mypy src tools" "ruff check -q src tools"; do
    # shellcheck disable=SC2086
    out=$("$UV" run --frozen $cmd 2>&1) \
        || { echo "  FAILED: $cmd"; echo "$out" | tail -20; status=1; }
done

[ "$status" -eq 0 ] && echo "selftests: ${#SUITES[@]} suites pass"
exit "$status"
