"""The board a refactor must not change.

    python tools/golden.py freeze   # copy data/, build the board with this tree's code
    python tools/golden.py check    # rebuild it with this tree's code, compare

Both run the sim stage on a fresh copy of the frozen data with the clock
pinned, because sim appends to decisions/cash_price_log.csv and later runs
read it back. Everything but generated_at must match exactly.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HOME = Path(os.environ.get("LFG_GOLDEN", "/tmp/lfg-golden"))
NOW = "2026-09-27T1500Z"


def build(out: Path) -> dict:
    scratch = HOME / "scratch"
    shutil.rmtree(scratch, ignore_errors=True)
    shutil.copytree(HOME / "data", scratch, symlinks=True)
    out.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, PYTHONPATH="src", FF_ROOT=str(scratch),
               LFG_REPORTS=str(out), LFG_NOW=NOW)
    subprocess.run([sys.executable, "src/run.py", "sim"], env=env, check=True,
                   stdout=subprocess.DEVNULL)
    board = json.loads((out / "decisions.json").read_text(encoding="utf-8"))
    board.pop("generated_at", None)
    return board


def diff(a, b, at="") -> list[str]:
    if isinstance(a, dict) and isinstance(b, dict):
        return [d for k in sorted(set(a) | set(b), key=str)
                for d in diff(a.get(k), b.get(k), "%s.%s" % (at, k))]
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        return [d for i, (x, y) in enumerate(zip(a, b))
                for d in diff(x, y, "%s[%d]" % (at, i))]
    return [] if a == b else ["%s: %r != %r" % (at or ".", a, b)]


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else ""
    if cmd == "freeze":
        shutil.rmtree(HOME, ignore_errors=True)
        shutil.copytree(os.environ.get("FF_ROOT", "data"), HOME / "data",
                        symlinks=True)
        (HOME / "board.json").write_text(json.dumps(build(HOME / "ref")))
        print("froze %s" % HOME)
        return 0
    if cmd == "check":
        want = json.loads((HOME / "board.json").read_text())
        got = build(HOME / "new")
        bad = diff(want, got)
        for line in bad[:20]:
            print("  " + line)
        print("golden: %s" % ("%d differences" % len(bad) if bad else "same board"))
        return 1 if bad else 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
