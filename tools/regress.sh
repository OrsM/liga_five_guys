#!/usr/bin/env bash
# tools/regress.sh — byte-for-byte regression gate for the report generators.
#
# --record   run the generator chain with a pinned clock and stash the
#            resulting artifacts (everything under LFG_REPORTS/LFG_PARTS)
#            in tools/baseline/.
# --check    run it again into a fresh scratch dir and diff -r against
#            tools/baseline/. Exit 0 iff byte-identical.
#
# Every pipeline run appends to data/decisions/*.csv; both modes restore
# that with `git checkout -- data/` so the working tree stays clean.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

UV="$HOME/.local/bin/uv"
BASELINE="$REPO_ROOT/tools/baseline"
PINNED_NOW="2026-09-16T1200Z"
STAGES=(report xi methodology sim digest)

mode="${1:-}"
if [[ "$mode" != "--record" && "$mode" != "--check" ]]; then
    echo "usage: $0 --record|--check" >&2
    exit 2
fi

scratch="$(mktemp -d /tmp/lfg-regress.XXXXXX)"
reports_dir="$scratch/reports"
parts_dir="$scratch/.runtime/parts"
alerts_file="$scratch/.runtime/alerts.md"
warnings_file="$scratch/.runtime/warnings.json"
mkdir -p "$reports_dir" "$parts_dir"

cleanup() {
    rm -rf "$scratch"
    # Undo the decisions-log appends the run just made, whichever mode.
    # ONLY data/decisions/ -- the four log CSVs a run appends to. NOT all of
    # data/: the tidy store holds legitimately uncommitted work (a fetch
    # lands new snapshots and parse rewrites the store), and reverting that
    # silently destroys it and makes the very next --check fail against a
    # baseline recorded on data the run just threw away.
    git -C "$REPO_ROOT" checkout -- data/decisions/ 2>/dev/null || true
}
trap cleanup EXIT

export PYTHONPATH=src
export FF_ROOT=./data
export LFG_NOW="$PINNED_NOW"
export LFG_REPORTS="$reports_dir"
export LFG_PARTS="$parts_dir"
export LFG_ALERTS="$alerts_file"
export LFG_WARNINGS="$warnings_file"

echo "regress: running ${STAGES[*]} (LFG_NOW=$PINNED_NOW) into $scratch"
if ! "$UV" run --frozen python src/run.py "${STAGES[@]}"; then
    echo "regress: pipeline run failed" >&2
    exit 1
fi

# Artifacts = everything written to LFG_REPORTS and LFG_PARTS, discovered
# rather than hardcoded.
capture_dir="$scratch/capture"
mkdir -p "$capture_dir/reports" "$capture_dir/parts"
if [ -d "$reports_dir" ]; then
    cp -a "$reports_dir/." "$capture_dir/reports/"
fi
if [ -d "$parts_dir" ]; then
    cp -a "$parts_dir/." "$capture_dir/parts/"
fi
# alerts.md is the phone notification (report.py writes it, sim.py rewrites
# it) and warnings.json is report.py's warning set. Both are real,
# user-facing run outputs that live OUTSIDE LFG_REPORTS/LFG_PARTS, so a
# refactor could change either with the rest of the report byte-identical.
# Captured so "byte-identical" means the whole run, not just the two report
# directories.
[ -f "$alerts_file" ] && cp -a "$alerts_file" "$capture_dir/alerts.md"
[ -f "$warnings_file" ] && cp -a "$warnings_file" "$capture_dir/warnings.json"

if [[ "$mode" == "--record" ]]; then
    rm -rf "$BASELINE"
    mkdir -p "$BASELINE"
    cp -a "$capture_dir/." "$BASELINE/"
    n_files="$(find "$BASELINE" -type f | wc -l)"
    echo "regress: recorded baseline into $BASELINE ($n_files files)"
    exit 0
fi

# --check
if [ ! -d "$BASELINE" ]; then
    echo "regress: no baseline at $BASELINE — run --record first" >&2
    exit 1
fi

if diff -r "$BASELINE" "$capture_dir"; then
    echo "regress: OK — byte-identical to baseline"
    exit 0
else
    echo "regress: MISMATCH — output differs from baseline (see diff above)" >&2
    exit 1
fi
