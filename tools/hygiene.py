"""Process hygiene, read-only (docs/notes/lfg.md "Processes"): what runs or is written must have a consumer, and say when it is reviewed.
Its selftest is the gate's check that (1) every unit file in tools/systemd names a row of the Processes table, and no review date in the
table has passed; (2) every record kind (ffcore.records.KINDS) and every top-level key of the decisions document (the dict `board.board`
returns) has a reader in src/ or tools/ (outside records, store, peek, board for its own keys, and the selftests), or an entry in EXEMPT
(name: 'YYYY-MM-DD reason'; one past its date fails: delete the thing, give it a reader, or renew the date with a reason)."""
import ast
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import frontiers  # noqa: E402

ROOT = frontiers.ROOT
NOT_READERS = ("src/ffcore/records.py", "src/ffcore/store.py", "tools/peek.py", "tools/hygiene.py")
KIND_READS = {"runs": r"\bruns\(\)", "grade_cell": r"\bgrade_cells\("}
EXEMPT = {
    "params": "2026-12-31 written every run, read only by tools/peek: use it (diff the constants between runs) or delete the kind",
    "place_dry": "2026-12-15 the raise experiment's rows; act.py reads its own file, nothing reads the store kind (sunset rule: Processes)",
    "shadow": "2026-12-31 shadow mode until #156 acts on the price of cash",
    "auction": "2026-12-15 no reader in this repo or on the phone page (Fantasy.jsx): show it or delete the key and board.auction_card",
    "ping": "2027-03-01 read by the website server (server/index.js), outside this repo",
    "considered": "2026-12-15 only a test reads it, and it is len(rejected): delete the key (moves the goldens: bless with the reason)",
    **dict.fromkeys(["sources", "lock_at", "cash", "cash_locked", "lambda", "alerts", "plans", "sells", "targets"],
                    "2027-03-01 read by the phone page (website Fantasy.jsx), outside this repo"),
}


def prod_sources() -> dict[str, str]:
    """The production code (selftests blanked) of every .py under src/ and tools/ that is not in NOT_READERS."""
    return {str(f.relative_to(ROOT)): frontiers.prod(f.read_text()) for d in ("src", "tools") for f in sorted((ROOT / d).rglob("*.py"))
            if "__pycache__" not in f.parts and str(f.relative_to(ROOT)) not in NOT_READERS}


def doc_keys() -> list[str]:
    """The top-level keys of the dict `board.board` returns."""
    tree = ast.parse((ROOT / "src/board.py").read_text())
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "board")
    last = [n.value for n in ast.walk(fn) if isinstance(n, ast.Return) and isinstance(n.value, ast.Dict)][-1]
    return [str(k.value) for k in last.keys if isinstance(k, ast.Constant)]


def unread(kinds: list[str], keys: list[str], code: dict[str, str]) -> list[str]:
    """The kinds and the doc keys (as `kind X` / `key X`) that no file of code reads."""
    every = "\n".join(code.values())
    boardless = "\n".join(t for f, t in code.items() if f != "src/board.py")
    return ([f"kind {k}" for k in kinds if not re.search(KIND_READS.get(k, r'(?:records|rows)\(\s*"%s"' % k), every)]
            + [f"key {k}" for k in keys if not re.search(r'[\w\])]\["%s"\]|\.get\("%s"' % (k, k), boardless)])


def due(exempt: dict[str, str], today: date) -> list[str]:
    """The exemptions whose date is not after today, or that carry no date and reason."""
    return [n for n, why in exempt.items() if not re.match(r"\d{4}-\d\d-\d\d \S", why) or date.fromisoformat(why[:10]) <= today]


def processes(doc: str) -> dict[str, str]:
    """The rows of the Processes table of the notes: process name -> its review date (the fifth column)."""
    cells = (line.split("|")[1:-1] for line in doc.splitlines() if line.startswith("| `"))
    return {c[0].strip(" `"): c[4].strip() for c in cells if len(c) >= 6}


def _selftest() -> None:
    """Every unit file is a row, no review has passed, every kind and key is read or exempt, and the exempt are not due."""
    today = date.today()
    rows = processes((ROOT / "docs/notes/lfg.md").read_text())
    units = {p.stem for p in (ROOT / "tools/systemd").iterdir()}
    assert units <= rows.keys(), "a unit file with no row in the Processes table: %s" % sorted(units - rows.keys())
    assert all(re.fullmatch(r"\d{4}-\d\d-\d\d", d) and date.fromisoformat(d) > today for d in rows.values()), "a review date has passed: %s" % rows
    from ffcore.records import KINDS
    found = unread(list(KINDS), doc_keys(), prod_sources())
    assert sorted(f.split()[1] for f in found) == sorted(EXEMPT), "unread and not exempt, or exempt and now read: %s" % found
    assert not due(EXEMPT, today), "an exemption has come due: %s" % due(EXEMPT, today)
    assert due({"a": "2026-01-01 x", "b": "2999-01-01 x", "c": "no date"}, today) == ["a", "c"], "past and undated exemptions are due"
    code = {"src/a.py": 'store.records("seen")\nd["used"]', "src/board.py": 'doc["mine"]'}
    assert unread(["seen", "lost"], ["used", "mine"], code) == ["kind lost", "key mine"], "a reader is found, a board's own read does not count"
    print("hygiene selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
