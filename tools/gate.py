"""The gate (the one entry point of every check): `tools/gate --full` runs ruff, frontiers, mypy and every suite, in one
pool of 4 workers, longest first; `tools/gate --changed [BASE]` (BASE defaults to main) runs the suites that import,
directly or through others, a module changed since BASE (merge base, working tree and untracked files included), ruff and
mypy on the changed files, and frontiers; a change it cannot map to modules falls back to --full.
A suite is a .py file under src/ or tools/ with a top-level `_selftest()`; each runs as `python FILE --selftest` on an
empty data root under ~/.cache. The verdict is exit 0 if and only if every check passes; a failing check prints its own
output once. Prints one table (check, seconds, longest first), the time spent waiting for ~/.cache/lfg-heavy.lock apart
from the time spent running, and the peak memory of the run against the cap.
The gate takes the lock and the memory cap itself (tools/capped, LFG_CAP): run it bare, not under flock or capped; if a
caller already holds the lock the gate does not wait for it again."""
import fcntl
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import frontiers  # noqa: E402

ROOT = frontiers.ROOT
WORKERS = 4
LOCK = Path.home() / ".cache" / "lfg-heavy.lock"
MYPY_CACHE = Path.home() / ".cache" / "lfg-mypy"
TIMES = Path.home() / ".cache" / "lfg-gate-times.json"
UNKNOWN_SECONDS = 5.0
UV = str(Path.home() / ".local/bin/uv")
RUN = [UV, "run", "--frozen"]
DOCS = (".md", ".txt")


class Check(NamedTuple):
    name: str
    argv: Sequence[str]


class Result(NamedTuple):
    name: str
    ok: bool
    seconds: float
    output: str


def suites() -> list[str]:
    """Every .py file under src/ and tools/ with a top-level `_selftest`, as a path relative to the repo, sorted."""
    found = (f for d in ("src", "tools") for f in (ROOT / d).rglob("*.py") if "__pycache__" not in f.parts)
    return sorted(str(f.relative_to(ROOT)) for f in found if re.search(r"(?m)^def _selftest", f.read_text()))


def sources() -> dict[str, Path]:
    """Every .py file under src/ and tools/, by path relative to the repo."""
    return {str(f.relative_to(ROOT)): f for d in ("src", "tools") for f in sorted((ROOT / d).rglob("*.py"))
            if "__pycache__" not in f.parts}


def changed_files(base: str) -> list[str]:
    """The paths that differ from the merge base of BASE and HEAD in the working tree, plus untracked files."""
    def git(*args: str) -> list[str]:
        return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout.split()
    return sorted(set(git("diff", "--name-only", git("merge-base", base, "HEAD")[0])) | set(git("ls-files", "--others", "--exclude-standard")))


def affected(changed: Sequence[str], graph: Mapping[str, set[str]]) -> set[str] | None:
    """The .py files of graph that are changed or import one that is; None if some changed path is neither a .py file
    of the graph nor documentation, i.e. the change cannot be mapped to modules."""
    if any(c not in graph and not c.endswith(DOCS) for c in changed):
        return None
    return frontiers.dependents(graph, {c for c in changed if c in graph})


def plan(changed: Sequence[str] | None) -> list[Check]:
    """The checks: everything for changed None, else those of `affected` (ruff and mypy on those files, only the suites
    among them); the full plan when the change is unmappable."""
    every = suites()
    hit = None if changed is None else affected(changed, frontiers.import_graph(sources()))
    files = ["src", "tools"] if hit is None else sorted(hit)
    run = every if hit is None else [s for s in every if s in hit]
    lint = [Check("ruff", [*RUN, "ruff", "check", "-q", *files]), Check("mypy", [*RUN, "mypy", *files])] if files else []
    return lint + [Check("frontiers", [*RUN, "python", "tools/frontiers.py"])] + [Check(s, [*RUN, "python", s, "--selftest"]) for s in run]


def priorities(times: Mapping[str, float], checks: Sequence[Check]) -> list[Check]:
    """The checks longest first by the last recorded seconds (UNKNOWN_SECONDS for one never timed), ties by name."""
    return sorted(checks, key=lambda c: (-times.get(c.name, UNKNOWN_SECONDS), c.name))


def run_check(check: Check, env: Mapping[str, str]) -> Result:
    start = time.monotonic()
    done = subprocess.run(check.argv, cwd=ROOT, env=dict(env), capture_output=True, text=True)
    return Result(check.name, done.returncode == 0, time.monotonic() - start, done.stdout + done.stderr)


def run_pool(checks: Sequence[Check], env: Mapping[str, str], workers: int = WORKERS,
             runner: Callable[[Check, Mapping[str, str]], Result] = run_check) -> list[Result]:
    """Every check run once, `workers` at a time, started in the order given; the results in that order."""
    with ThreadPoolExecutor(workers) as pool:
        return list(pool.map(lambda c: runner(c, env), checks))


def table(results: Sequence[Result]) -> str:
    """One row per check, longest first: `seconds  check`."""
    return "\n".join(f"{r.seconds:7.1f}  {'ok  ' if r.ok else 'FAIL'} {r.name}" for r in sorted(results, key=lambda r: -r.seconds))


def holder_is_ancestor() -> bool:
    """True if the process holding a flock on LOCK is this process or one of its ancestors."""
    inode = LOCK.stat().st_ino
    mine, pid = set(), os.getpid()
    while pid > 1:
        mine.add(pid)
        pid = int(re.search(r"^PPid:\s*(\d+)", Path(f"/proc/{pid}/status").read_text(), re.M).group(1))  # type: ignore[union-attr]
    for line in Path("/proc/locks").read_text().splitlines():
        parts = line.split()
        if "FLOCK" in parts and parts[5].endswith(f":{inode}") and int(parts[4]) in mine:
            return True
    return False


def take_lock() -> float:
    """Block until LOCK is held by this process (inherited by what it runs); the seconds waited, 0.0 if a caller holds it."""
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(LOCK, os.O_RDWR | os.O_CREAT, 0o644)
    start = time.monotonic()
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.set_inheritable(fd, True)
            return time.monotonic() - start
        except BlockingIOError:
            if holder_is_ancestor():
                return 0.0
            time.sleep(0.2)


def peak_megabytes() -> float | None:
    """The peak memory of the cgroup this process runs in (the capped scope), in MB, None if the kernel does not say."""
    try:
        path = Path("/sys/fs/cgroup" + Path("/proc/self/cgroup").read_text().strip().split("::")[-1]) / "memory.peak"
        return int(path.read_text()) / 2**20
    except (OSError, ValueError):
        return None


def clean(path: Path) -> None:
    for p in (path, *path.rglob("*")):
        if p.is_dir() and not p.is_symlink():
            p.chmod(p.stat().st_mode | stat.S_IRWXU)
    shutil.rmtree(path, ignore_errors=True)


def environment(scratch: Path) -> dict[str, str]:
    """The environment of every check: the source on the path, hashing pinned, one empty data root, mypy's shared cache."""
    cache = MYPY_CACHE / re.sub(r"\W", "_", str(ROOT))
    return {**os.environ, "PYTHONPATH": "src", "PYTHONHASHSEED": os.environ.get("PYTHONHASHSEED", "0"),
            "FF_ROOT": str(scratch / "data"), "MYPY_CACHE_DIR": str(cache)}


def gate(changed: Sequence[str] | None) -> int:
    waited = float(os.environ.get("LFG_GATE_WAIT", "0"))
    start = time.monotonic()
    checks = plan(changed)
    times = json.loads(TIMES.read_text()) if TIMES.exists() else {}
    scratch = Path(tempfile.mkdtemp(prefix="selftests.", dir=Path.home() / ".cache"))
    try:
        results = run_pool(priorities(times, checks), environment(scratch))
    finally:
        clean(scratch)
    TIMES.write_text(json.dumps(times | {r.name: round(r.seconds, 2) for r in results if r.ok}, indent=1, sort_keys=True))
    for r in sorted(results, key=lambda r: r.name):
        if not r.ok:
            print(f"  FAILED: {r.name}\n" + "\n".join(r.output.rstrip().splitlines()[-20:]))
    print(table(results))
    peak = peak_megabytes()
    print(f"flock wait {waited:.1f} s | run {time.monotonic() - start:.1f} s | peak memory "
          + ("unknown" if peak is None else f"{peak:.0f} MB") + " of cap " + os.environ.get("LFG_CAP", "1500M"))
    bad = [r for r in results if not r.ok]
    if not bad:
        print(f"gate: {len(results)} checks pass")
    return 1 if bad else 0


def main(argv: Sequence[str]) -> int:
    if argv[:1] == ["--full"]:
        changed = None
    elif argv[:1] == ["--changed"] and len(argv) <= 2:
        changed = changed_files(argv[1] if len(argv) == 2 else "main")
    else:
        print("usage: tools/gate --full | --changed [BASE]", file=sys.stderr)
        return 2
    if "LFG_GATE_INNER" not in os.environ:
        waited = take_lock()
        env = {**os.environ, "LFG_GATE_INNER": "1", "LFG_GATE_WAIT": f"{waited:.3f}"}
        os.execvpe(str(ROOT / "tools/capped"), [str(ROOT / "tools/capped"), sys.executable, __file__, *argv], env)
    return gate(changed)


def _selftest() -> None:
    """The plan lists each suite once and the gate's own checks; a changed module selects its importers and no others;
    an unmappable change selects everything; the pool runs every check and reports failures; the order is longest first."""
    every = suites()
    assert "tools/gate.py" in every and "src/eleven.py" in every and len(set(every)) == len(every), "the suites found, once each"
    full = plan(None)
    own = ("ruff", "mypy", "frontiers")
    assert sorted(c.name for c in full if c.name in own) == sorted(own), "ruff, mypy and frontiers"
    assert sorted(c.name for c in full if c.name not in own) == every, "--full runs every suite and no other"
    graph = {"src/a.py": {"src/b.py"}, "src/b.py": set(), "tools/t.py": {"src/b.py"}, "src/c.py": set()}
    assert affected(["src/b.py"], graph) == {"src/a.py", "src/b.py", "tools/t.py"}, "importers of the changed module"
    assert affected(["src/c.py", "docs/x.md"], graph) == {"src/c.py"}, "documentation maps to nothing"
    assert affected(["src/b.py", "pyproject.toml"], graph) is None, "a file that is no module cannot be mapped"
    only = plan(["src/eleven.py"])
    wanted = frontiers.dependents(frontiers.import_graph(sources()), {"src/eleven.py"})
    assert {c.name for c in only if c.name.endswith(".py")} == {s for s in every if s in wanted}, "--changed runs the dependents' suites"
    assert "src/eleven.py" in [a for c in only if c.name == "mypy" for a in c.argv], "mypy on the changed files"
    assert "src" not in [a for c in only for a in c.argv], "not the whole tree"
    assert plan(["pyproject.toml"]) == full, "an unmappable change falls back to --full"
    assert [c.name for c in plan(["docs/x.md"])] == ["frontiers"], "documentation only: nothing but frontiers"
    order = [c.name for c in priorities({"slow": 9.0, "fast": 0.1}, [Check("fast", []), Check("new", []), Check("slow", [])])]
    assert order == ["slow", "new", "fast"], "longest first, a check never timed in between"
    ran: list[str] = []

    def fake(check: Check, env: Mapping[str, str]) -> Result:
        ran.append(check.name)
        return Result(check.name, check.name != "bad", 0.0, "")
    got = run_pool([Check(n, []) for n in ("a", "bad", "c", "d", "e")], {}, 2, fake)
    assert sorted(ran) == sorted(r.name for r in got) == ["a", "bad", "c", "d", "e"], "every check runs once"
    assert [r.name for r in got if not r.ok] == ["bad"], "a failure is reported as the check's own"
    mine = run_check(Check("x", [sys.executable, "-c", "import sys; print('out'); sys.exit(3)"]), {})
    assert not mine.ok and mine.output.strip() == "out", "a nonzero exit is a failure with its output"
    print("gate selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        sys.exit(main(sys.argv[1:]))
