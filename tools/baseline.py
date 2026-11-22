"""The baseline cache of pinned dry runs (#142): the decisions.json and board time of `tools/lfg-dry-run board` at a pinned
LFG_NOW, kept under ~/.cache/lfg-baseline/<git tree hash>/<LFG_NOW>/ so a before/after comparison runs the tree under
test once per cut and reuses main's side.

    python tools/baseline.py compare CUT...    this tree against main on each cut: the field-level difference of their
                                               decisions.json and both board times (tools/lfg-dry-run --compare CUT...)
    python tools/baseline.py record SCRATCH CUT   (called by tools/lfg-dry-run after a pinned run)

An entry is valid for one source stamp: size and mtime of the tracked tree's data/tidy/parsed.jsonl and snapindex.json and
the id of the latest run in its store/, the inputs of a dry run besides the code. record keeps a run only if its tree is
clean, under that tree's own hash; compare builds a missing or stale entry of main from a clean `git archive main`, never
from the working tree. Reads the tracked tree's data/ and store/ and writes only the cache and the dry-run scratch."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, NamedTuple

ROOT = Path(__file__).resolve().parent.parent
CACHE = Path(os.environ.get("LFG_BASELINE", str(Path.home() / ".cache" / "lfg-baseline")))
PARSE_CACHE = ("parsed.jsonl", "snapindex.json")
SHOWN = 12
WIDTH = 60


class Baseline(NamedTuple):
    decisions: str
    seconds: float


class Change(NamedTuple):
    kind: str
    path: str
    old: str
    new: str


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


def tracked_tree(repo: Path = ROOT) -> Path:
    """The tracked tree that holds data/ and store/, also seen from a worktree of it."""
    return (repo / git(repo, "rev-parse", "--git-common-dir")).resolve().parent


def scratch_dir() -> Path:
    return Path(os.environ.get("LFG_SCRATCH", str(Path.home() / ".cache" / "lfg-dry-run")))


def source_stamp(tree: Path) -> str:
    """The text that changes when the parse cache or the store of tree changes."""
    parts = []
    for name in PARSE_CACHE:
        s = (tree / "data" / "tidy" / name).stat()
        parts.append("%s:%d:%d" % (name, s.st_size, s.st_mtime_ns))
    runs = [int(p.name) for p in (tree / "store" / "runs").iterdir() if p.name.isdigit()]
    return ";".join(parts + ["run:%d" % max(runs, default=0)])


def clean_tree_hash(repo: Path) -> str | None:
    """The git tree hash of repo's HEAD when nothing in it is modified or untracked, else None."""
    return None if git(repo, "status", "--porcelain") else git(repo, "rev-parse", "HEAD^{tree}")


def board_seconds(log: str) -> float | None:
    """The board stage's seconds from the summary line of a run.py log, None if it has none."""
    found = re.findall(r"^\s+(?:\w+ [\d.]+s\s+)*board ([\d.]+)s", log, re.M)
    return float(found[-1]) if found else None


def entry(tree_hash: str, cut: str) -> Path:
    return CACHE / tree_hash / cut


def store_entry(tree_hash: str, cut: str, stamp: str, decisions: str, seconds: float) -> None:
    """The entry of tree_hash at cut made: decisions.json, board time and stamp, the stamp last."""
    d = entry(tree_hash, cut)
    d.mkdir(parents=True, exist_ok=True)
    (d / "stamp").unlink(missing_ok=True)
    (d / "decisions.json").write_text(decisions, encoding="utf-8")
    (d / "board_seconds").write_text("%r\n" % seconds, encoding="utf-8")
    (d / "stamp").write_text(stamp + "\n", encoding="utf-8")


def lookup(tree_hash: str, cut: str, stamp: str) -> Baseline | None:
    """The entry of tree_hash at cut if it was made for this stamp, else None."""
    d = entry(tree_hash, cut)
    try:
        if (d / "stamp").read_text(encoding="utf-8").strip() != stamp:
            return None
        return Baseline((d / "decisions.json").read_text(encoding="utf-8"), float((d / "board_seconds").read_text()))
    except (OSError, ValueError):
        return None


def record(scratch: Path, cut: str, repo: Path = ROOT) -> bool:
    """The last run in scratch kept as the entry of repo's tree at cut when repo is clean and the run has both outputs;
    whether it was."""
    tree_hash = clean_tree_hash(repo)
    seconds = board_seconds((scratch / "last-run.log").read_text(encoding="utf-8"))
    out = scratch / "reports" / "decisions.json"
    if tree_hash is None or seconds is None or not out.exists():
        return False
    store_entry(tree_hash, cut, source_stamp(tracked_tree(repo)), out.read_text(encoding="utf-8"), seconds)
    return True


def dry_run(cut: str, code: Path | None = None) -> Baseline:
    """tools/lfg-dry-run board of this tree (of the tree at code, if given) pinned at cut, its decisions.json and board
    time; exits with the run's log if it failed."""
    env = {**os.environ, "LFG_NOW": cut, **({"LFG_CODE": str(code)} if code else {})}
    done = subprocess.run([str(ROOT / "tools" / "lfg-dry-run"), "board"], env=env, capture_output=True, text=True)
    seconds = board_seconds(done.stdout)
    out = scratch_dir() / "reports" / "decisions.json"
    if done.returncode or seconds is None or not out.exists():
        sys.exit("baseline: the dry run at %s failed:\n%s%s" % (cut, done.stdout[-2000:], done.stderr[-2000:]))
    return Baseline(out.read_text(encoding="utf-8"), seconds)


def of_main(cuts: list[str]) -> dict[str, tuple[Baseline, bool]]:
    """Main's baseline at each cut and whether it came from the cache; a missing or stale one is built once, from a clean
    archive of main."""
    main = git(ROOT, "rev-parse", "main^{tree}")
    stamp = source_stamp(tracked_tree())
    got = {c: lookup(main, c, stamp) for c in cuts}
    missing = [c for c, b in got.items() if b is None]
    if missing:
        base = CACHE / "build"
        base.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as tmp:
            subprocess.run("git -C %s archive main | tar -x -C %s" % (ROOT, tmp), shell=True, check=True)
            for c in missing:
                b = dry_run(c, Path(tmp))
                store_entry(main, c, stamp, b.decisions, b.seconds)
                got[c] = b
    return {c: (b, c not in missing) for c, b in got.items() if b}


def flat(v: Any, path: str = "") -> dict[str, Any]:
    """The leaves of a JSON value keyed by their path (/key, [index]); an empty container is a leaf."""
    if isinstance(v, dict) and v:
        return {k: x for key, item in v.items() for k, x in flat(item, "%s/%s" % (path, key)).items()}
    if isinstance(v, list) and v:
        return {k: x for i, item in enumerate(v) for k, x in flat(item, "%s[%d]" % (path, i)).items()}
    return {path: v}


def trimmed(v: Any) -> str:
    text = json.dumps(v, ensure_ascii=False)
    return text if len(text) <= WIDTH else text[:WIDTH] + "..."


def changes(old: Any, new: Any) -> list[Change]:
    """The leaves added, removed or changed from old to new (exactly equal values are not listed), in path order, values
    trimmed."""
    a, b = flat(old), flat(new)
    out = [Change("removed", k, trimmed(a[k]), "") for k in sorted(a.keys() - b.keys())]
    out += [Change("added", k, "", trimmed(b[k])) for k in sorted(b.keys() - a.keys())]
    out += [Change("changed", k, trimmed(a[k]), trimmed(b[k]))
            for k in sorted(a.keys() & b.keys()) if json.dumps(a[k]) != json.dumps(b[k])]
    return sorted(out, key=lambda c: c.path)


def report(cut: str, base: Baseline, cached: bool, tree: Baseline) -> bool:
    """One cut's comparison printed; whether the two decisions.json are byte-identical."""
    print("%s: board main %.1f s (%s), tree %.1f s" % (cut, base.seconds, "cached" if cached else "built", tree.seconds))
    if base.decisions == tree.decisions:
        print("  decisions.json byte-identical")
        return True
    found = changes(json.loads(base.decisions), json.loads(tree.decisions))
    counts = {k: sum(1 for c in found if c.kind == k) for k in ("added", "removed", "changed")}
    print("  decisions.json differs: %(added)d added, %(removed)d removed, %(changed)d changed" % counts)
    for c in found[:SHOWN]:
        print("  %-7s %s %s" % (c.kind, c.path, (c.old + " -> " + c.new) if c.kind == "changed" else c.old or c.new))
    if len(found) > SHOWN:
        print("  ... %d more" % (len(found) - SHOWN))
    return False


def compare(cuts: list[str]) -> int:
    """This tree run once on each cut against main's cached baseline; 0 when every decisions.json is identical, else 1."""
    base = of_main(cuts)
    identical = [report(c, *base[c], dry_run(c)) for c in cuts]
    return 0 if all(identical) else 1


def _selftest() -> None:
    global CACHE
    old = {"a": 1, "b": [1, 2, {"x": "p"}], "c": {"d": 1.5}, "e": [], "f": "gone"}
    assert changes(old, json.loads(json.dumps(old))) == [], "equal values have no changes"
    new = {"a": 2, "b": [1, 2, {"x": "p"}, 9], "c": {"d": 1.5, "n": 1}, "e": [], "g": "x" * 200}
    got = {(c.kind, c.path) for c in changes(old, new)}
    assert got == {("changed", "/a"), ("added", "/b[3]"), ("added", "/c/n"), ("removed", "/f"), ("added", "/g")}, got
    assert all(len(c.new) <= WIDTH + 3 for c in changes(old, new)), "values are trimmed"
    assert changes({"a": 1}, {"a": 1.0}) != [] and changes({"a": 1}, {"a": True}) != [], "a type change is a change"
    assert board_seconds("  parse 0.1s  board 10.9s  health 1.0s\n  3 stages in 12.0s") == 10.9
    assert board_seconds("board\n  slow board, where: 50% x") is None
    with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as tmp:
        t = Path(tmp)
        tree = t / "tree"
        for name in PARSE_CACHE:
            (tree / "data" / "tidy").mkdir(parents=True, exist_ok=True)
            (tree / "data" / "tidy" / name).write_text("x")
        (tree / "store" / "runs" / "7").mkdir(parents=True)
        stamp = source_stamp(tree)
        assert source_stamp(tree) == stamp
        (tree / "data" / "tidy" / PARSE_CACHE[0]).write_text("xy")
        assert source_stamp(tree) != stamp, "a changed parse cache changes the stamp"
        stamp = source_stamp(tree)
        (tree / "store" / "runs" / "8").mkdir()
        assert source_stamp(tree) != stamp, "a new run changes the stamp"
        was, CACHE = CACHE, t / "cache"
        try:
            store_entry("h", "cut", "s1", "{}\n", 3.5)
            assert lookup("h", "cut", "s1") == Baseline("{}\n", 3.5)
            assert lookup("h", "cut", "s2") is None and lookup("h", "other", "s1") is None, "an entry is for one stamp and cut"
            assert not (CACHE / "h" / "cut").is_symlink() and entry("h", "cut").is_dir()
        finally:
            CACHE = was
    print("baseline self-test OK")


def main(argv: list[str]) -> int:
    if argv[:1] == ["--selftest"]:
        _selftest()
        return 0
    if argv[:1] == ["compare"] and len(argv) > 1:
        return compare(argv[1:])
    if argv[:1] == ["record"] and len(argv) == 3:
        record(Path(argv[1]), argv[2])
        return 0
    sys.exit(__doc__)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
