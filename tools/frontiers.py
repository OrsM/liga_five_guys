"""Layer check (CLAUDE.md, principle 2: a layer reads only what an earlier layer exports), read-only.
Parses every .py under src/ and src/ffcore with ast and maps each module to its layer through OWNS (layers in order:
rules, ingest, forecast, decision, board, grading, run). An import of a module of a LATER layer, in production code (top-level
_selftest functions excluded, lazy imports inside functions counted), is a crossing, printed grouped by the importing
layer with file:line. Every module of src/ is assigned (a module in no layer is listed as unassigned and not checked: a new one must be added).
A for statement or comprehension that unpacks an Inputs row by position is a crossing of its module as well.
Exit 1 if there is one. `--table` prints the layers and their modules as markdown."""
import ast
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parent.parent
ORDER = ["rules", "ingest", "forecast", "decision", "board", "grading", "run"]
OWNS = {**dict.fromkeys(["rules", "pricing", "parse", "text", "names", "clock", "stats", "source", "publish"], "rules"),
        **dict.fromkeys(["ingest", "crosswalk", "league", "jornadas", "tidy", "observations", "parsed", "identity", "state", "current",
                         "season_facts", "ledger", "store", "records", "auction_facts", "auth", "sources", "footballdata", "futbolfantasy",
                         "laliga_api", "source_health"], "ingest"),
        **dict.fromkeys(["forecast", "forecast_facts", "price_fold", "eleven"], "forecast"),
        **dict.fromkeys(["auction", "auction_table", "inflow", "decision", "hurdle", "valuation", "worth", "approach", "decision_facts",
                         "loader"], "decision"),
        **dict.fromkeys(["board", "shadow"], "board"),
        **dict.fromkeys(["grading", "salegrade", "health"], "grading"),
        **dict.fromkeys(["act", "run"], "run")}


class Crossing(NamedTuple):
    file: str
    line: int
    layer: str
    what: str


def prod(text: str) -> str:
    """A Python file's production code: its top-level _selftest functions blanked (line numbers kept)."""
    return re.sub(r"(?ms)^def _selftest\w*\(.*?(?=^\S)", lambda m: re.sub(r"[^\n]", "", m.group()), text + "\nX")[:-2]


def imported(tree: ast.AST, modules: set[str]) -> list[tuple[int, str]]:
    """The (line, module stem) of every import in tree of a module in modules, whether written `import a.b`,
    `from a import b` or `from ffcore import b`."""
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            names = [a.name for a in n.names]
        elif isinstance(n, ast.ImportFrom) and n.module and not n.level:
            names = [n.module] + [n.module + "." + a.name for a in n.names]
        else:
            continue
        for name in names:
            parts = name.split(".")
            stem = parts[1] if parts[0] == "ffcore" and len(parts) > 1 else parts[0]
            if stem in modules:
                out.append((n.lineno, stem))
    return sorted(set(out))


def unpacks_inp(target: ast.expr, iterable: ast.expr) -> bool:
    """A loop target that is a tuple or list, over an attribute of inp: the fields of an Inputs row read by position."""
    return (isinstance(target, (ast.Tuple, ast.List)) and isinstance(iterable, ast.Attribute)
            and isinstance(iterable.value, ast.Name) and iterable.value.id == "inp")


def positional_reads(text: str) -> list[int]:
    """The lines of a Python file with a for statement or comprehension that unpacks an Inputs row by position."""
    hits = []
    for n in ast.walk(ast.parse(prod(text))):
        if isinstance(n, ast.For):
            pairs = [(n.target, n.iter)]
        elif isinstance(n, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            pairs = [(g.target, g.iter) for g in n.generators]
        else:
            continue
        if any(unpacks_inp(t, i) for t, i in pairs):
            hits.append(n.lineno)
    return sorted(hits)


def crossings(sources: Mapping[str, str]) -> list[Crossing]:
    """The crossings of the modules whose text is given, by module stem: each import of a later layer's module by a
    module in an earlier one, each positional read of an Inputs row by a module in a layer."""
    stems = set(sources)
    found = []
    for stem, text in sorted(sources.items()):
        layer = OWNS.get(stem)
        if layer is None:
            continue
        for line, other in imported(ast.parse(prod(text)), stems):
            if ORDER.index(OWNS.get(other, layer)) > ORDER.index(layer):
                found.append(Crossing(stem, line, layer, "imports %s, of the later layer %s" % (other, OWNS[other])))
        found += [Crossing(stem, line, layer, "unpacks an Inputs row by position") for line in positional_reads(text)]
    return found


def tree_sources() -> dict[str, Path]:
    """Each module stem under src/ and src/ffcore and its file."""
    return {f.stem: f for f in sorted((ROOT / "src").glob("*.py")) + sorted((ROOT / "src/ffcore").glob("*.py"))
            if f.stem != "__init__"}


def import_graph(files: Mapping[str, Path]) -> dict[str, set[str]]:
    """For each key of files (a path under ROOT, as a string) the keys it imports, by `imported`, over the whole file
    (self-tests and lazy imports included). A module stem shared by several files stands for all of them, so the graph
    may name too many dependencies, never too few."""
    by_stem: dict[str, list[str]] = {}
    for key in files:
        by_stem.setdefault(Path(key).stem, []).append(key)
    return {key: {k for _, stem in imported(ast.parse(f.read_text()), set(by_stem)) for k in by_stem[stem]} - {key}
            for key, f in files.items()}


def dependents(graph: Mapping[str, set[str]], changed: set[str]) -> set[str]:
    """The keys of graph that import, directly or through others, a key of changed, and the changed keys themselves."""
    out = set(changed)
    grew = True
    while grew:
        more = {k for k, uses in graph.items() if uses & out} - out
        out |= more
        grew = bool(more)
    return out


def table() -> str:
    rows = ["| Layer | Modules |", "|---|---|"]
    rows += [f"| {m} | {', '.join(sorted(s for s, o in OWNS.items() if o == m))} |" for m in ORDER]
    return "\n".join(rows)


def _selftest() -> None:
    """A later layer's import is a crossing, in any form and lazily; an earlier or same layer's, a utility's and a
    self-test's are not; a clean graph has none."""
    clean = {"state": "x = 1\n", "forecast_facts": "from ffcore import state\n", "auction": "from ffcore.forecast_facts import f\n",
             "board": "import ffcore.auction\nfrom ffcore import state, auction\n", "grading": "from board import b\n",
             "run": "import grading\n", "mystery": "import run\n"}
    assert crossings(clean) == [], "earlier and same layers, and a module in no layer, cross nothing"
    seeded = {**clean, "state": "def f():\n    from ffcore import forecast_facts\n",
              "forecast_facts": "import ffcore.board\n", "auction": "from grading import g\n", "stats": "import state\n",
              "hurdle": "import board\n", "grading": "import run\n"}
    got = {(c.file, c.layer) for c in crossings(seeded)}
    assert got == {("state", "ingest"), ("forecast_facts", "forecast"), ("auction", "decision"), ("stats", "rules"),
                  ("hurdle", "decision"), ("grading", "grading")}, got
    assert {c.line for c in crossings(seeded) if c.file == "state"} == {2}, "a lazy import inside a function is found, at its line"
    assert crossings({**clean, "state": "def _selftest():\n    import board\n\nX = 1\n"}) == [], "a self-test may import anything"
    assert crossings({**clean, "state": "import mystery\n"}) == [], "a module in no layer is not a crossing"
    assert positional_reads("for a, b in inp.bids:\n    pass\n") == [1]
    assert positional_reads("ys = [a for a, b in inp.bids]\n") == [1]
    assert positional_reads("for c in inp.universe:\n    pass\n") == []
    assert positional_reads("def _selftest():\n    for a, b in inp.bids:\n        pass\n\nX = 1\n") == []
    assert [c.what for c in crossings({"board": "for a, b in inp.bids:\n    pass\n"})] == ["unpacks an Inputs row by position"]
    assert prod("x\ndef _selftest():\n    import a\n\ny = 1\n").count("\n") == 5
    graph = {"a": {"b"}, "b": {"c"}, "c": set(), "d": {"c"}, "e": set()}
    assert dependents(graph, {"c"}) == {"a", "b", "c", "d"} and dependents(graph, {"e"}) == {"e"}, "importers, transitively, and the module itself"
    assert dependents(graph, set()) == set()
    graph = import_graph({key: ROOT / key for key in ("tools/frontiers.py", "src/ffcore/rules.py", "src/eleven.py")})
    assert graph["src/eleven.py"] == {"src/ffcore/rules.py"} and graph["src/ffcore/rules.py"] == set(), "real imports, by file"
    assert main() == 0, "the tree has crossings (listed above)"
    print("frontiers selftest ok")


def main() -> int:
    if "--table" in sys.argv:
        print(table())
        return 0
    files = tree_sources()
    found = crossings({stem: f.read_text() for stem, f in files.items()})
    total = 0
    for layer in ORDER:
        mine = sorted(c for c in found if c.layer == layer)
        if mine:
            total += len(mine)
            print(f"== {layer}: {len(mine)} crossings")
            print("\n".join(f"  {files[c.file].relative_to(ROOT)}:{c.line}  {c.what}" for c in mine))
    print(f"crossings: {total}")
    print("unassigned:", " ".join(sorted(s for s in files if s not in OWNS)) or "none")
    return 1 if found else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        sys.exit(main())
