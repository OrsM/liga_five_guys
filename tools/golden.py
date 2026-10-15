"""The boards a refactor must not change.

    python tools/golden.py freeze [N]   # copy the raw data, build N boards (default 16) with this tree's code
    python tools/golden.py check [N|all] # rebuild N of them (default 6: one of each kind, the rest spread) with this tree's code, compare
    python tools/golden.py leak [N|all]  # the same, on data cut off at each board's time

Only what no rebuild recovers is copied: data/raw, data/season and the
tidy files git tracks. They are parsed with the tree's own code, so a change
to how tables are stored or read is checked too, not only the model. The
parse (~6 minutes cold) is kept in parsed/ and made again only when the
parse version (ingest._version: the parsers, the number parsers and
tables.sql) is not the one it was made with, so a change to the model or the
views checks in the time of the boards alone. Each board is built in its own
copy of it, three at a time (LFG_GOLDEN_JOBS), and shown as it is done.

check and leak take N boards (default 6: one of each kind of board, the rest
spread over the season; `all` for every one, ~2 minutes).

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


def subset(kinds_of: dict[str, str], n: int) -> list[str]:
    """n of the frozen boards (in time order): the earliest of each distinct
    kind of board first, so what a refactor could change is all covered, the
    rest spread evenly over the season."""
    stamps = list(kinds_of)
    if n >= len(stamps):
        return stamps
    chosen = [min(s for s in stamps if kinds_of[s] == k) for k in dict.fromkeys(kinds_of.values())][:n]
    rest = [s for s in stamps if s not in chosen]
    free = n - len(chosen)
    if free > 0 and rest:
        chosen += [rest[round(i * (len(rest) - 1) / max(free - 1, 1))] if free > 1 else rest[len(rest) // 2]
                   for i in range(free)]
    return sorted(set(chosen), key=stamps.index)


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


def truncate(db: Path, stamp: str) -> None:
    """The database as if it ended at stamp: every row observed (or fitted)
    after it deleted."""
    with closing(sqlite3.connect(db)) as c, c:
        for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall():
            for col in ("observed_at", "as_of"):
                if col in [r[1] for r in c.execute("PRAGMA table_info(%s)" % t)]:
                    c.execute("DELETE FROM %s WHERE %s > ?" % (t, col), (stamp,))


def build(now: str, hash_seed: str, base: Path, ended: bool = False) -> dict[str, Any] | None:
    """The board at now, built in its own copy of the parsed data (the raw
    snapshots, which a sim does not read, are left out). With ended, the copy
    holds nothing observed after now."""
    work = HOME / "work" / now
    shutil.rmtree(work, ignore_errors=True)
    shutil.copytree(base, work, symlinks=True, ignore=shutil.ignore_patterns("raw"))
    if ended:
        truncate(work / "lfg.db", now)
    out = work / "reports"
    out.mkdir()
    env = dict(os.environ, PYTHONPATH="src", FF_ROOT=str(work),
               LFG_REPORTS=str(out), LFG_NOW=now, PYTHONHASHSEED=hash_seed,
               LFG_KEPT="off" if ended else str(HOME / "kept"))     # a leak check reads no answer of its own
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


def builds(nows: Sequence[str], hash_seed: str, base: Path) -> list[dict[str, Any] | None]:
    """The boards at these times, JOBS processes each taking a share of them
    in turn on its own copy of the parsed data: a process starts (imports,
    schema) once, not once a board."""
    shares = [list(nows[i::JOBS]) for i in range(JOBS) if nows[i::JOBS]]

    def one(share: list[str]) -> dict[str, dict[str, Any] | None]:
        work = HOME / "work" / share[0]
        shutil.rmtree(work, ignore_errors=True)
        shutil.copytree(base, work, symlinks=True, ignore=shutil.ignore_patterns("raw"))
        out = work / "reports"
        out.mkdir()
        env = dict(os.environ, PYTHONPATH="src", FF_ROOT=str(work), LFG_REPORTS=str(out),
                   PYTHONHASHSEED=hash_seed, LFG_KEPT=str(HOME / "kept"),
                   OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1")
        try:
            subprocess.run([sys.executable, __file__, "--worker", *share], env=env, check=True,
                           stdout=subprocess.DEVNULL)
            return {n: (json.loads((out / (n + ".json")).read_text(encoding="utf-8"))
                        if (out / (n + ".json")).exists() else None) for n in share}
        finally:
            shutil.rmtree(work, ignore_errors=True)
    got: dict[str, dict[str, Any] | None] = {}
    for part in in_order(shares, one):
        got.update(part)
    boards = []
    for n in nows:
        board = got[n]
        if board is not None:
            board.pop("generated_at", None)
        boards.append(board)
    return boards


def _worker(nows: Sequence[str]) -> None:
    """Build the board at each time, in this process, as run.py sim does."""
    import assemble
    import grading
    import run
    from ffcore import clock
    out = Path(os.environ["LFG_REPORTS"])
    for now in nows:
        os.environ["LFG_NOW"] = now
        clock.set_now(None)
        assemble.built.cache_clear()
        grading._AT.clear()
        (out / "decisions.json").unlink(missing_ok=True)
        run.main(["sim"])
        if (out / "decisions.json").exists():
            shutil.copy(out / "decisions.json", out / (now + ".json"))


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
                              builds(stamps_, "0", base)):
            boards[now] = board
            print("  %s  %s" % (now, kinds(board)), flush=True)
        (HOME / "boards.json").write_text(json.dumps(boards))
        print("froze %d boards in %s" % (len(boards), HOME))
        return 0
    if cmd == "check":
        frozen = json.loads((HOME / "boards.json").read_text())
        arg = argv[1] if len(argv) > 1 else "6"
        want = {k: frozen[k] for k in subset({k: kinds(v) for k, v in frozen.items()},
                                             len(frozen) if arg == "all" else int(arg))}
        base = parsed()
        results = [diff(want[now], board) for now, board in zip(want, builds(list(want), "1", base))]
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
    if cmd == "leak":
        want = json.loads((HOME / "boards.json").read_text())
        arg = argv[1] if len(argv) > 1 else "6"
        base, stamps_ = parsed(), subset({k: kinds(v) for k, v in want.items()},
                                         len(want) if arg == "all" else int(arg))
        results = in_order(stamps_, lambda now: diff(want[now], build(now, "1", base, ended=True)))
        for now, lines in zip(stamps_, results):
            print("  %s  %s" % (now, "same" if not lines else "LEAKS: %d differences" % len(lines)))
            for line in lines[:10]:
                print("      " + line)
        return 1 if any(results) else 0
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

    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "lfg.db"
        with closing(sqlite3.connect(db)) as c, c:
            c.execute("CREATE TABLE seen (observed_at TEXT, v INT)")
            c.execute("CREATE TABLE fits (as_of TEXT, v INT)")
            c.execute("CREATE TABLE plain (v INT)")
            for t in ("seen", "fits"):
                c.executemany("INSERT INTO %s VALUES (?, 1)" % t, [("2026-10-01T0800Z",), ("2026-10-02T0800Z",), ("2026-10-03T0800Z",)])
            c.execute("INSERT INTO plain VALUES (1)")
        truncate(db, "2026-10-02T0800Z")
        with closing(sqlite3.connect(db)) as c:
            assert [r[0] for r in c.execute("SELECT observed_at FROM seen ORDER BY 1")] \
                == ["2026-10-01T0800Z", "2026-10-02T0800Z"], "what was seen after the cut is gone, at the cut stays"
            assert c.execute("SELECT count(*) FROM fits").fetchone()[0] == 2, "fits stamped as_of too"
            assert c.execute("SELECT count(*) FROM plain").fetchone()[0] == 1, "no time column: left alone"

    kinds_of = {"a": "buy field sell", "b": "buy field sell", "c": "field", "d": "buy field sell", "e": "field",
                "f": "buy sell", "g": "field", "h": "buy field sell", "i": "no board"}
    assert subset(kinds_of, 4) == ["a", "c", "f", "i"], \
        "one board of each kind of board first (the earliest), however many that is under the limit"
    assert subset(kinds_of, 6) == ["a", "b", "c", "f", "h", "i"], \
        "the rest spread evenly over the season"
    assert subset(kinds_of, 3) == ["a", "c", "f", "i"][:3] and subset(kinds_of, 99) == list(kinds_of)
    assert subset({}, 5) == []

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
    if sys.argv[1:2] == ["--worker"]:
        _worker(sys.argv[2:])
        raise SystemExit(0)
    if sys.argv[1:] == ["--selftest"]:
        _selftest()
        raise SystemExit(0)
    raise SystemExit(main(sys.argv[1:]))
