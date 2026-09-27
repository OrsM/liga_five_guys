"""The boards a refactor must not change.

    python tools/golden.py freeze [N]   # copy data/, build N boards (default 16) with this tree's code
    python tools/golden.py check        # rebuild them with this tree's code, compare

Boards are built at N snapshot times spread over the season, with the
clock pinned to each, so the check sees sells, line-up changes and swaps
that any single day may not have. Every build starts from the frozen log,
because sim appends to decisions/cash_price_log.csv and later runs read it
back. Freeze and check hash strings differently, so a board that depends
on the order of a set shows up as a difference rather than by luck.
Everything but generated_at must match exactly.
"""
from __future__ import annotations

import csv
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HOME = Path(os.environ.get("LFG_GOLDEN", Path.home() / ".cache" / "lfg-golden"))
LOG = Path("decisions") / "cash_price_log.csv"


def stamps(n: int) -> list[str]:
    with open(HOME / "data" / "tidy" / "api_teams.csv", encoding="utf-8") as f:
        seen = sorted({r["observed_at"] for r in csv.DictReader(f)})
    if len(seen) <= n:
        return seen
    return sorted({seen[round(i * (len(seen) - 1) / (n - 1))] for i in range(n)})


def build(now: str, hash_seed: str) -> dict | None:
    scratch, out = HOME / "scratch", HOME / "out"
    if not scratch.exists():
        shutil.copytree(HOME / "data", scratch, symlinks=True)
    shutil.copy2(HOME / "data" / LOG, scratch / LOG)
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    env = dict(os.environ, PYTHONPATH="src", FF_ROOT=str(scratch),
               LFG_REPORTS=str(out), LFG_NOW=now, PYTHONHASHSEED=hash_seed)
    subprocess.run([sys.executable, "src/run.py", "sim"], env=env, check=True,
                   stdout=subprocess.DEVNULL)
    if not (out / "decisions.json").exists():
        return None
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


def kinds(board: dict | None) -> str:
    if board is None:
        return "no board"
    return " ".join(sorted(d["what"] for d in board["do"])) or "nothing to do"


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else ""
    if cmd == "freeze":
        shutil.rmtree(HOME, ignore_errors=True)
        shutil.copytree(os.environ.get("FF_ROOT", "data"), HOME / "data",
                        symlinks=True)
        boards = {}
        for now in stamps(int(argv[1]) if len(argv) > 1 else 16):
            boards[now] = build(now, "0")
            print("  %s  %s" % (now, kinds(boards[now])))
        (HOME / "boards.json").write_text(json.dumps(boards))
        print("froze %d boards in %s" % (len(boards), HOME))
        return 0
    if cmd == "check":
        want = json.loads((HOME / "boards.json").read_text())
        bad = 0
        for now, board in want.items():
            got = build(now, "1")
            lines = diff(board, got)
            bad += bool(lines)
            print("  %s  %s" % (now, "same" if not lines else
                                "%d differences" % len(lines)))
            for line in lines[:10]:
                print("      " + line)
        print("golden: %s" % ("%d of %d boards differ" % (bad, len(want))
                              if bad else "%d boards the same" % len(want)))
        return 1 if bad else 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
