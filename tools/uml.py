"""Draw the code as it is: module dependencies, domain classes and the
decision funnel, as Mermaid, generated from src/ so the pictures cannot
drift from the code.

    python tools/uml.py [outdir]     # writes modules.mmd, classes.mmd, funnel.mmd

Render with any Mermaid viewer (mermaid-cli: mmdc -i modules.mmd -o m.png).
Layers come from tools/structure.py; an arrow from the model into the data
layer is drawn red, and there should be none.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from structure import DATA, MODEL, SRC, modules  # noqa: E402

FETCH = {"sources", "ffcore.source", "ffcore.futbolfantasy",
         "ffcore.footballdata", "ffcore.laliga_api", "ffcore.auth"}
STAGES = {"run", "ingest", "crosswalk", "assemble", "decide", "sim", "grading"}
BY_NAME = [("run", "ingest"), ("run", "crosswalk"), ("run", "sim")]
LAYERS = [("stages", "Stages (src/)", STAGES), ("fetch", "Fetch and parse", FETCH),
          ("data", "Tables and time", DATA | {"ffcore.crosswalk", "ffcore.league"}),
          ("model", "Model", MODEL)]


def node(m: str) -> str:
    return m.replace("ffcore.", "ff_") if m == "ffcore.crosswalk" else m.replace("ffcore.", "")


def imports(tree) -> set[str]:
    tests = {id(x) for n in tree.body if isinstance(n, ast.FunctionDef)
             and n.name.startswith("_selftest") for x in ast.walk(n)}
    out = set()
    for n in ast.walk(tree):
        if id(n) in tests:
            continue
        if isinstance(n, ast.ImportFrom) and n.module:
            out.add(n.module)
        elif isinstance(n, ast.Import):
            out |= {a.name for a in n.names}
    return out


def module_diagram() -> str:
    mods = modules()
    trees = {m: ast.parse(p.read_text(encoding="utf-8")) for m, p in mods.items()}
    size = {m: len(p.read_text(encoding="utf-8").splitlines()) for m, p in mods.items()}
    edges = sorted((m, t) for m, tr in trees.items() for t in imports(tr)
                   if t in mods and t != m and not m.endswith("fixtures"))
    fan_in = {m: sum(1 for _a, b in edges if b == m) for m in mods}
    fan_out = {m: sum(1 for a, _b in edges if a == m) for m in mods}
    out = ["flowchart LR",
           "    classDef hub fill:#fee2e2,stroke:#b91c1c,color:#111",
           "    classDef leaf fill:#f3f4f6,stroke:#6b7280,color:#111"]
    placed = set()
    for key, title, members in LAYERS + [("util", "Pure utilities", set(mods))]:
        members = sorted(m for m in members if m in mods and m not in placed
                         and not m.endswith("fixtures"))
        placed |= set(members)
        out.append('    subgraph %s["%s"]' % (key, title))
        for m in members:
            label = "%s %d" % (node(m), size[m])
            if fan_out[m] >= 10 or fan_in[m] >= 10:
                label += "<br/>in %d · out %d" % (fan_in[m], fan_out[m])
            cls = ":::hub" if (fan_out[m] >= 10 or fan_in[m] >= 10) else (
                ":::leaf" if key == "util" else "")
            out.append('        %s["%s"]%s' % (node(m), label, cls))
        out.append("    end")
    bad = []
    for a, b in BY_NAME:
        out.append('    %s -. "by name" .-> %s' % (node(a), node(b)))
    for i, (a, b) in enumerate(edges):
        out.append("    %s --> %s" % (node(a), node(b)))
        if a in MODEL and b in DATA:
            bad.append(i + len(BY_NAME))
    if bad:
        out.append("    linkStyle %s stroke:#dc2626,stroke-width:3px"
                   % ",".join(map(str, bad)))
    return "\n".join(out) + "\n"


def _fields(c: ast.ClassDef) -> list[tuple[str, str]]:
    got = [(x.target.id, ast.unparse(x.annotation)) for x in c.body
           if isinstance(x, ast.AnnAssign) and isinstance(x.target, ast.Name)]
    seen = {n for n, _ in got}
    init = next((f for f in c.body if isinstance(f, ast.FunctionDef)
                 and f.name == "__init__"), None)
    params = {a.arg: ast.unparse(a.annotation) for a in init.args.args
              if a.annotation} if init else {}
    def source(v):
        if isinstance(v, ast.BoolOp):
            v = v.values[0]
        return v.id if isinstance(v, ast.Name) and v.id in params else None

    for x in ast.walk(init) if init else ():
        if not isinstance(x, ast.Assign):
            continue
        for t in x.targets:
            pairs = (zip(t.elts, x.value.elts) if isinstance(t, ast.Tuple)
                     and isinstance(x.value, ast.Tuple) else [(t, x.value)])
            for tgt, val in pairs:
                arg = source(val)
                if (arg and isinstance(tgt, ast.Attribute)
                        and isinstance(tgt.value, ast.Name)
                        and tgt.value.id == "self" and tgt.attr not in seen):
                    got.append((tgt.attr, params[arg]))
                    seen.add(tgt.attr)
    for x in ast.walk(c):
        if (isinstance(x, ast.Attribute) and isinstance(x.value, ast.Name)
                and x.value.id == "self" and isinstance(x.ctx, ast.Store)
                and x.attr not in seen and not x.attr.startswith("_")):
            got.append((x.attr, ""))
            seen.add(x.attr)
    return got


def class_diagram() -> str:
    classes = {}
    for m, p in modules().items():
        if m.endswith("fixtures"):
            continue
        for c in (n for n in ast.parse(p.read_text(encoding="utf-8")).body
                  if isinstance(n, ast.ClassDef) and not n.name.startswith("_")):
            classes[c.name if c.name not in classes else "%s_%s" % (m.split(".")[-1], c.name)] = (m, c)
    out = ["classDiagram", "    direction LR"]
    links = set()
    for name, (m, c) in sorted(classes.items(), key=lambda kv: kv[1][0]):
        kind = ("NamedTuple" if any(ast.unparse(b) == "NamedTuple" for b in c.bases)
                else "frozen dataclass" if any("frozen" in ast.unparse(d) for d in c.decorator_list)
                else "dataclass" if any("dataclass" in ast.unparse(d) for d in c.decorator_list)
                else "")
        out.append("    class %s {" % name)
        if kind:
            out.append("        <<%s>>" % kind)
        out.append("        %s" % m)
        fields = _fields(c)
        for f, ann in fields[:8]:
            out.append("        +%s%s" % (f, ": " + ann.replace("[", "~").replace("]", "~").replace(",", "")
                                            if ann and len(ann) < 30 else ""))
        if len(fields) > 8:
            out.append("        ...%d more fields" % (len(fields) - 8))
        for f in c.body:
            if isinstance(f, ast.FunctionDef) and not f.name.startswith("_"):
                out.append("        +%s()" % f.name)
        out.append("    }")
        for f, ann in fields:
            for other in classes:
                if other != name and other in ann.replace("[", " ").replace("]", " ").replace(",", " ").split():
                    links.add("    %s *-- %s : %s" % (name, other, f))
    return "\n".join(out + sorted(links)) + "\n"


def funnel_diagram() -> str:
    """decide.FUNNEL, step by step, each with the first sentence of its
    docstring: the steps tools/ask.py names when it explains a board."""
    tree = ast.parse((SRC / "decide.py").read_text(encoding="utf-8"))
    defs = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
    steps = next(n.value.elts for n in tree.body if isinstance(n, ast.Assign)
                 and any(getattr(t, "id", "") == "FUNNEL" for t in n.targets))
    out = ["flowchart LR"]
    for i, step in enumerate(steps):
        if isinstance(step, ast.Attribute):
            owner, name = defs[step.value.id], step.attr
            fn = next(n for n in owner.body if getattr(n, "name", "") == name)
        else:
            name = step.id
            fn = defs[name]
        first = " ".join((ast.get_docstring(fn) or "").split(". ")[0].split())
        out.append('    s%d["<b>%s</b><br/>%s"]' % (i, name, first.replace('"', "'")))
        if i:
            out.append("    s%d --> s%d" % (i - 1, i))
    out.append('    s%d --> board(["the board"])' % (len(steps) - 1))
    return "\n".join(out) + "\n"


def main(argv: list[str]) -> int:
    outdir = Path(argv[0]) if argv else SRC.parent / "docs"
    outdir.mkdir(exist_ok=True)
    (outdir / "modules.mmd").write_text(module_diagram(), encoding="utf-8")
    (outdir / "classes.mmd").write_text(class_diagram(), encoding="utf-8")
    (outdir / "funnel.mmd").write_text(funnel_diagram(), encoding="utf-8")
    print("wrote %s/modules.mmd, classes.mmd and funnel.mmd" % outdir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
