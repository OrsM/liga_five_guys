"""How tangled the code is, as numbers a change can move.

    python tools/structure.py            # the summary
    python tools/structure.py --lazy     # every import made inside a function
    python tools/structure.py --check    # fail on a model->data import or a
                                         # hidden local import (selftests.sh)

Layers: the data layer reads tables and the clock; the model computes from
what it is given. A model module importing a data module is counted, and
so is every import made inside a function (outside self-tests), since
those are either an optional dependency or a dependency hidden from the
module's header.
"""
from __future__ import annotations

import ast
import collections
import re
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
DATA = {"ffcore.tidy", "ffcore.clock", "ffcore.jornadas", "ffcore.points",
        "ffcore.players"}
MODEL = {"ffcore.score", "ffcore.startprob", "ffcore.schedule",
         "ffcore.fixture", "ffcore.forecast", "ffcore.season",
         "ffcore.outlook", "ffcore.market", "ffcore.pricing", "ffcore.action",
         "ffcore.rules"}
REACH = re.compile(r'\bu\.state\.squads\.get\(u\.me|\.view\("|'
                   r'\bself\.state\.squads\.get\(self\.me|\._order\b|'
                   r'\._pool_mean\b|\[3\]\) \+ bands|lg\.cfg\.me|lg\.xw\.')


def modules() -> dict[str, Path]:
    return {str(p.relative_to(SRC).with_suffix("")).replace("/", "."): p
            for p in sorted(SRC.glob("*.py")) + sorted(SRC.glob("ffcore/*.py"))
            if p.name != "__init__.py"}


def measure() -> dict:
    mods = modules()
    edges, lazy, code, reach, classes = set(), [], 0, 0, []
    for name, path in mods.items():
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        tests = [n for n in tree.body if isinstance(n, ast.FunctionDef)
                 and n.name.startswith("_selftest")]
        in_test = {id(x) for n in tests for x in ast.walk(n)}
        code += len(src.splitlines()) - sum(n.end_lineno - n.lineno + 1
                                            for n in tests)
        reach += len(REACH.findall(src))
        top = {id(n) for n in tree.body}
        for n in ast.walk(tree):
            if id(n) in in_test:
                continue
            if isinstance(n, ast.ImportFrom) and n.module:
                targets = [n.module]
            elif isinstance(n, ast.Import):
                targets = [a.name for a in n.names]
            else:
                continue
            for m in targets:
                if m in mods and m != name:
                    edges.add((name, m))
                if id(n) not in top and not name.endswith("fixtures"):
                    lazy.append((name, n.lineno, m))
        for c in (n for n in tree.body if isinstance(n, ast.ClassDef)):
            attrs = {x.target.id for x in c.body if isinstance(x, ast.AnnAssign)
                     and isinstance(x.target, ast.Name)}
            attrs |= {x.attr for x in ast.walk(c) if isinstance(x, ast.Attribute)
                      and isinstance(x.value, ast.Name) and x.value.id == "self"
                      and isinstance(x.ctx, ast.Store)}
            classes.append((len(attrs), "%s.%s" % (name, c.name)))
    fan = collections.Counter(a for a, _b in edges)
    return {"modules": len(mods), "code_lines": code, "edges": len(edges),
            "lazy": lazy, "fan_out": fan.most_common(3),
            "model_to_data": sorted((a, b) for a, b in edges
                                    if a in MODEL and b in DATA),
            "largest_classes": sorted(classes, reverse=True)[:3],
            "reach_throughs": reach}


# Imports that stay inside a function on purpose: optional or heavy (httpx
# keeps the test path off the network), a fallback, or inside a parser,
# where moving it would change the parser's fingerprint and re-parse every
# snapshot. Anything local that is not listed here belongs in the header.
LAZY_OK = {("ffcore.futbolfantasy", "ffcore.parse")}


def problems(m: dict) -> list[str]:
    mods = modules()
    out = ["model imports the data layer: %s -> %s" % ab
           for ab in m["model_to_data"]]
    out += ["%s:%d imports %s inside a function" % (n, line, t)
            for n, line, t in m["lazy"]
            if t in mods and (n, t) not in LAZY_OK]
    return out


def main(argv: list[str]) -> int:
    m = measure()
    if "--check" in argv:
        bad = problems(m)
        for p in bad:
            print("  structure: " + p)
        return 1 if bad else 0
    if "--lazy" in argv:
        for name, line, target in m["lazy"]:
            print("  %s:%d  %s" % (name, line, target))
        return 0
    print("modules %d, %d code lines (self-tests excluded), %d import edges"
          % (m["modules"], m["code_lines"], m["edges"]))
    print("imports inside functions: %d" % len(m["lazy"]))
    print("model -> data layer: %d %s" % (len(m["model_to_data"]), " ".join(
        "%s->%s" % (a.split(".")[-1], b.split(".")[-1])
        for a, b in m["model_to_data"])))
    print("widest fan-out: %s" % ", ".join("%s %d" % kv for kv in m["fan_out"]))
    print("largest classes: %s" % ", ".join("%s %d" % (n, k)
                                            for k, n in m["largest_classes"]))
    print("known reach-throughs: %d" % m["reach_throughs"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
