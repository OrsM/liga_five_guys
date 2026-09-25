#!/usr/bin/env python3
"""tools/microclones.py -- count near-identical 3-statement windows repeated
across DIFFERENT top-level functions/methods in src/.

Unlike tools/concepts.sh's other checks (which track named, already-found
concepts), this is a blunt, general duplication detector: normalize each
function's body (strip string/number literals) and slide a 3-line window
over it, then count windows that recur, verbatim, inside some OTHER
function. A function's own nested closures are excluded from its own
count -- a closure's lines are a physical subset of its enclosing
function's lines, so comparing a function against itself that way is not
duplication, it is the same bytes counted twice (see cell()/ladder_rows()
in sim.py, which this script used to -- wrongly -- flag).

Run directly to see the actual matches; run with --count for the bare
number tools/concepts.sh ratchets on.
"""
from __future__ import annotations

import ast
import glob
import re
import sys
import collections


def _norm_line(l: str) -> str:
    l = re.sub(r'"[^"]*"', '""', l)
    l = re.sub(r"'[^']*'", "''", l)
    l = re.sub(r"\b\d+(\.\d+)?\b", "N", l)
    return l.strip()


def _top_level_funcs(tree: ast.AST):
    """FunctionDefs whose lines exclude any NESTED FunctionDef's lines --
    the fix for the cell()/ladder_rows() false positive."""
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name.startswith("_selftest"):
            continue
        nested_ranges = [(n.lineno, n.end_lineno) for n in ast.walk(node)
                         if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                         and n is not node]
        yield node, nested_ranges


def scan() -> list[tuple[tuple, list]]:
    windows = collections.defaultdict(list)
    for f in glob.glob("src/*.py") + glob.glob("src/ffcore/*.py"):
        src = open(f, encoding="utf-8").read()
        tree = ast.parse(src)
        lines = src.splitlines()
        for node, nested in _top_level_funcs(tree):
            body_start = node.lineno
            excluded = set()
            for lo, hi in nested:
                excluded.update(range(lo, hi + 1))
            keep = [i for i in range(body_start, node.end_lineno + 1)
                   if i not in excluded]
            norm = [(_norm_line(lines[i - 1]), i) for i in keep
                    if lines[i - 1].strip()
                    and not lines[i - 1].strip().startswith("#")]
            for i in range(len(norm) - 2):
                win = tuple(x[0] for x in norm[i:i + 3])
                if sum(len(w) for w in win) < 30:
                    continue
                windows[win].append((f[len("src/"):], node.name, norm[i][1]))

    groups = [(w, v) for w, v in windows.items()
             if len({(f, n) for f, n, _ in v}) >= 2]
    groups.sort(key=lambda kv: -len({(f, n) for f, n, _ in kv[1]}))
    return groups


if __name__ == "__main__":
    groups = scan()
    if "--count" in sys.argv:
        print(len(groups))
        raise SystemExit(0)
    print(len(groups), "3-line windows repeated across 2+ different functions")
    for w, v in groups:
        funcs = sorted({(f, n) for f, n, _ in v})
        print("---", len(funcs), "functions ---")
        for line in w:
            print("   ", line[:80])
        for f, n in funcs:
            print("     in", f, n)
