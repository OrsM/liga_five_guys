"""The boards a refactor must not change.

    python tools/golden.py freeze [N]   # copy the raw data, build N boards (default 16) with this tree's code
    python tools/golden.py check        # rebuild them with this tree's code, compare

Only what no rebuild recovers is copied: data/raw, data/season and the
tidy files git tracks. They are parsed with the tree's own code, so a change
to how tables are stored or read is checked too, not only the model. The
parse (~6 minutes cold) is kept in parsed/ and made again only when the
parse version (ingest._version: the parsers, the number parsers and
tables.sql) is not the one it was made with, so a change to the model or the
views checks in the time of the boards alone. Each board is built in its own
copy of it, three at a time (LFG_GOLDEN_JOBS), and shown as it is done.

Boards are built at N snapshot times spread over the season, with the
clock pinned to each, so the check sees sells, line-up changes and swaps
that any single day may not have: the snapshots that stored the app's
squads (api_teams). Freeze and check hash strings differently, so a board
that depends on the order of a set shows up as a difference rather than by
luck. Everything but generated_at must match: floats to math.isclose (a sum
in SQL and in Python may differ in the last bits), the rest exactly.
"""
from __future__ import annotations

import json
import math
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from typing import Any, TypeVar

HOME = Path(os.environ.get("LFG_GOLDEN", Path.home() / ".cache" / "lfg-golden"))
JOBS = int(os.environ.get("LFG_GOLDEN_JOBS", "3"))   # a sim is ~120MB; this box has 4 cores, 3.7GB
T = TypeVar("T")
R = TypeVar("R")


def stamps(n: int) -> list[str]:
    seen = sorted(p.name.removeprefix("dt=").removesuffix(".tar.xz")
                  for p in (HOME / "data" / "raw").glob("dt=*.tar.xz")
                  if any(m.startswith("api_teams") for m in tarfile.open(p).getnames()))
    if len(seen) <= n:
        return seen
    return sorted({seen[round(i * (len(seen) - 1) / (n - 1))] for i in range(n)})


def keep(src: Path, dst: Path) -> None:
    """Copy what no rebuild recovers: raw, season and git-tracked tidy."""
    tracked = subprocess.run(["git", "ls-files", "data/tidy"], capture_output=True,
                             text=True, check=True).stdout.split()
    for d in ("raw", "season"):
        shutil.copytree(src / d, dst / d, symlinks=True)
    (dst / "tidy").mkdir(parents=True)
    for t in tracked:
        shutil.copy2(src / "tidy" / Path(t).name, dst / "tidy")


def fresh(cache: Path, mark: Path, version: str) -> bool:
    """Whether the parse kept in cache was made with this parse version."""
    return cache.exists() and mark.exists() and mark.read_text() == version


def parse_version() -> str:
    """This tree's parse version (ingest._version)."""
    return subprocess.run([sys.executable, "-c", "import ingest; print(ingest._version())"],
                          check=True, capture_output=True, text=True,
                          env=dict(os.environ, PYTHONPATH="src")).stdout.strip()


def parsed() -> Path:
    """The kept data parsed with this tree's code: reused while the parse
    version is the same, made afresh (a cold parse) when it is not."""
    cache, mark, version = HOME / "parsed", HOME / "parsed.version", parse_version()
    if fresh(cache, mark, version):
        return cache
    shutil.rmtree(cache, ignore_errors=True)
    mark.unlink(missing_ok=True)
    shutil.copytree(HOME / "data", cache, symlinks=True)
    subprocess.run([sys.executable, "src/run.py", "parse"], check=True,
                   stdout=subprocess.DEVNULL,
                   env=dict(os.environ, PYTHONPATH="src", FF_ROOT=str(cache)))
    with closing(sqlite3.connect(cache / "lfg.db")) as db:
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    mark.write_text(version)
    return cache


def in_order(items: Sequence[T], fn: Callable[[T], R], jobs: int = JOBS) -> list[R]:
    """fn over items, jobs at a time, the results in the order of items."""
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        return list(pool.map(fn, items))


def build(now: str, hash_seed: str, base: Path) -> dict[str, Any] | None:
    """The board at now, built in its own copy of the parsed data (the raw
    snapshots, which a sim does not read, are left out)."""
    work = HOME / "work" / now
    shutil.rmtree(work, ignore_errors=True)
    shutil.copytree(base, work, symlinks=True, ignore=shutil.ignore_patterns("raw"))
    out = work / "reports"
    out.mkdir()
    env = dict(os.environ, PYTHONPATH="src", FF_ROOT=str(work),
               LFG_REPORTS=str(out), LFG_NOW=now, PYTHONHASHSEED=hash_seed)
    try:
        subprocess.run([sys.executable, "src/run.py", "sim"], env=env, check=True,
                       stdout=subprocess.DEVNULL)
        if not (out / "decisions.json").exists():
            return None
        board = json.loads((out / "decisions.json").read_text(encoding="utf-8"))
        board.pop("generated_at", None)
        return board
    finally:
        shutil.rmtree(work, ignore_errors=True)


def diff(a: object, b: object, at: str = "") -> list[str]:
    if isinstance(a, dict) and isinstance(b, dict):
        return [d for k in sorted(set(a) | set(b), key=str)
                for d in diff(a.get(k), b.get(k), "%s.%s" % (at, k))]
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        return [d for i, (x, y) in enumerate(zip(a, b))
                for d in diff(x, y, "%s[%d]" % (at, i))]
    if type(a) in (int, float) and type(b) in (int, float):
        assert isinstance(a, (int, float)) and isinstance(b, (int, float))
        return [] if math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-12) \
            else ["%s: %r != %r" % (at or ".", a, b)]
    return [] if type(a) is type(b) and a == b else ["%s: %r != %r" % (at or ".", a, b)]


def kinds(board: dict[str, Any] | None) -> str:
    if board is None:
        return "no board"
    return " ".join(sorted(d["what"] for d in board["do"])) or "nothing to do"


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else ""
    if cmd == "freeze":
        shutil.rmtree(HOME, ignore_errors=True)
        keep(Path(os.environ.get("FF_ROOT", "data")), HOME / "data")
        base, boards = parsed(), {}
        for now, board in zip(stamps_ := stamps(int(argv[1]) if len(argv) > 1 else 16),
                              in_order(stamps_, lambda now: build(now, "0", base))):
            boards[now] = board
            print("  %s  %s" % (now, kinds(board)), flush=True)
        (HOME / "boards.json").write_text(json.dumps(boards))
        print("froze %d boards in %s" % (len(boards), HOME))
        return 0
    if cmd == "check":
        want = json.loads((HOME / "boards.json").read_text())
        base = parsed()
        results = in_order(list(want), lambda now: diff(want[now], build(now, "1", base)))
        bad = 0
        for now, lines in zip(want, results):
            bad += bool(lines)
            print("  %s  %s" % (now, "same" if not lines else
                                "%d differences" % len(lines)), flush=True)
            for line in lines[:10]:
                print("      " + line)
        print("golden: %s" % ("%d of %d boards differ" % (bad, len(want))
                              if bad else "%d boards the same" % len(want)))
        return 1 if bad else 0
    print(__doc__)
    return 2


def _selftest() -> None:
    assert diff({"a": [1.0, "x"]}, {"a": [1.0 + 1e-13, "x"]}) == [], \
        "floats equal to the last bits are the same (math.isclose)"
    assert diff({"a": 1.0}, {"a": 1.001}) == [".a: 1.0 != 1.001"]
    assert diff({"n": 5000000}, {"n": 5000000.0}) == [], "money as INTEGER or float: one number"
    assert diff({"a": "x"}, {"a": "y"}) and diff([1, 2], [1, 2, 3]), "the rest is exact"
    assert diff(True, 1.0) and diff(None, 0.0), "a bool or None is not a number"
    import tempfile
    import time

    with tempfile.TemporaryDirectory() as tmp:
        cache, mark = Path(tmp) / "parsed", Path(tmp) / "parsed.version"
        assert not fresh(cache, mark, "v1"), "nothing parsed yet"
        cache.mkdir()
        assert not fresh(cache, mark, "v1"), "a parse nobody marked is not trusted"
        mark.write_text("v1")
        assert fresh(cache, mark, "v1"), "the parse made with this version is reused"
        assert not fresh(cache, mark, "v2"), "a different parser or table layout parses again"

    lag = {"a": 0.3, "b": 0.0, "c": 0.1, "d": 0.0}

    def slow(k: str) -> str:
        time.sleep(lag[k])
        return k.upper()
    started = time.monotonic()
    got = in_order(list(lag), slow, 3)
    assert got == ["A", "B", "C", "D"], "results come back in the order asked, whoever finishes first"
    assert time.monotonic() - started < 0.55, "and they ran side by side, not one after another"
    print("golden self-test OK")


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        _selftest()
        raise SystemExit(0)
    raise SystemExit(main(sys.argv[1:]))
