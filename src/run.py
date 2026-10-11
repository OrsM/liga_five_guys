
from __future__ import annotations

import gc
import collections
import os
import resource
import sqlite3
import subprocess
import sys
import threading
import time
import traceback
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path

from ffcore.clock import run_now
from ffcore.parse import stamp
from ffcore.db import RUN_ENV, connect, this_run


STAGES: list[tuple[str, str]] = [
    ("parse", "ingest:parse"),
    ("crosswalk", "crosswalk:main"),
    ("sim", "sim:main"),
]
# Run only when named (run.py grade): lfg-run calls it after the report is out.
ON_REQUEST: list[tuple[str, str]] = [
    ("grade", "grading:record_grades"),
]


def call(spec: str) -> Callable[[], object]:
    mod, fn = spec.split(":")
    return getattr(__import__(mod), fn)


def _sample(hits: collections.Counter[str], stop: threading.Event, tid: int) -> None:
    while not stop.wait(0.25):
        f, at = sys._current_frames().get(tid), "(outside src/)"  # noqa: SLF001 -- the sampler needs another thread's frame
        while f is not None:
            if "/src/" in f.f_code.co_filename:
                at = "%s:%s" % (f.f_code.co_filename.rsplit("/src/", 1)[1],
                                f.f_code.co_name)
                break
            f = f.f_back
        hits[at] += 1


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _commit(repo: Path = Path(".")) -> str:
    """The last commit that changed the code: the report, data and input
    commits every run makes do not count."""
    try:
        return subprocess.run(
            ["git", "-C", str(repo), "log", "-1", "--format=%h", "--", ".",
             ":!data", ":!reports", ":!inputs"],
            capture_output=True, text=True, check=True).stdout.strip() or "?"
    except (OSError, subprocess.CalledProcessError):
        return "?"


def _start(db: sqlite3.Connection) -> int:
    with db:
        cur = db.execute("INSERT INTO runs (started, as_of, trigger, commit_sha)"
                         " VALUES (?, ?, ?, ?)",
                         (_utc(), stamp(run_now()),
                          os.environ.get("LFG_TRIGGER", "service"), _commit()))
    assert cur.lastrowid is not None
    return cur.lastrowid


def _measure(db: sqlite3.Connection, run: int, stage: str, seconds: float,
             counts: object) -> None:
    got = {**(counts if isinstance(counts, Mapping) else {}), "seconds": seconds}
    with db:
        db.executemany("INSERT INTO run_measures VALUES (?, ?, ?, ?)",
                       [(run, stage, k, float(v)) for k, v in got.items()])


def _finish(db: sqlite3.Connection, run: int, ok: bool) -> None:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    with db:
        db.execute("UPDATE runs SET finished = ?, ok = ?, peak_mb = ? WHERE run_id = ?",
                   (_utc(), int(ok), peak, run))


def main(argv: list[str], every: Sequence[tuple[str, str]] = STAGES,
         path: Path | None = None) -> int:
    """Run the stages argv names (all by default), each recorded in runs
    and run_measures; 1 at the first that fails."""
    db = connect(path)
    run = _start(db)
    os.environ[RUN_ENV] = str(run)
    ok = False
    try:
        ok = _stages(argv, every, db, run) == 0
    finally:
        _finish(db, run, ok)
        db.close()
    return 0 if ok else 1


def _stages(argv: list[str], every: Sequence[tuple[str, str]],
            db: sqlite3.Connection, run: int) -> int:
    want = [a for a in argv if not a.startswith("-")]
    stages = [s for s in [*every, *ON_REQUEST] if s[0] in want] if want else list(every)
    if want:
        stages = sorted(stages, key=lambda s: want.index(s[0]))

    started, times = time.time(), []
    for name, spec in stages:
        t0 = time.time()
        print("%s" % name, flush=True)
        hits: collections.Counter[str] = collections.Counter()
        stop = threading.Event()
        threading.Thread(target=_sample, daemon=True, args=(
            hits, stop, threading.get_ident())).start()
        counts: object = None
        try:
            counts = call(spec)()
        except SystemExit as e:
            if e.code:
                print("  FAILED: %s exited %s" % (name, e.code))
                return 1
        except Exception:
            traceback.print_exc()
            print("  FAILED: %s" % name)
            return 1
        gc.collect()
        stop.set()
        times.append((time.time() - t0, name))
        _measure(db, run, name, times[-1][0], counts)
        if times[-1][0] > float(os.environ.get("LFG_SLOW_S", 8)):
            print("  slow %s, where: %s" % (name, "  ".join(
                "%d%% %s" % (100 * v // sum(hits.values()), k)
                for k, v in hits.most_common(5))))

    print("  %s" % "  ".join("%s %.1fs" % (n, t) for t, n in times))
    print("  %d stages in %.1fs" % (len(times), time.time() - started))
    return 0


def _selftest_runs() -> None:
    import sqlite3
    import tempfile
    import types
    from pathlib import Path

    fake = types.ModuleType("fake_stages")

    def boom() -> None:
        raise RuntimeError("stage broke")
    fake.counted = lambda: {"rows": 7}  # type: ignore[attr-defined]
    fake.quiet = lambda: {"run": this_run()}  # type: ignore[attr-defined]
    fake.boom = boom  # type: ignore[attr-defined]
    sys.modules["fake_stages"] = fake
    with tempfile.TemporaryDirectory() as tmp:
        git = ["git", "-C", tmp, "-c", "user.name=t", "-c", "user.email=t@t"]
        subprocess.run([*git[:3], "init", "-q"], check=True)
        for f in ("src/a.py", "reports/b.json", "data/c.csv", "inputs/d.ini"):
            (Path(tmp) / f).parent.mkdir(parents=True, exist_ok=True)
            (Path(tmp) / f).write_text(f)
            subprocess.run([*git, "add", f], check=True)
            subprocess.run([*git, "commit", "-qm", f], check=True)
            if f == "src/a.py":
                code = _commit(Path(tmp))
        assert code != "?" and _commit(Path(tmp)) == code, \
            "the code's commit: a report, data or input commit does not change it"
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "lfg.db"
        assert main([], [("count", "fake_stages:counted"), ("quiet", "fake_stages:quiet")],
                    path) == 0
        assert main([], [("count", "fake_stages:counted"), ("boom", "fake_stages:boom")],
                    path) == 1
        db = sqlite3.connect(path)
        runs = db.execute("SELECT run_id, ok, finished IS NOT NULL, trigger, commit_sha,"
                          " as_of, peak_mb > 0 FROM runs ORDER BY run_id").fetchall()
        assert [r[:4] for r in runs] == [(1, 1, 1, "service"), (2, 0, 1, "service")], runs
        assert all(r[4] and r[5] and r[6] for r in runs), "commit, clock and memory recorded"
        got = {(r, s, m): v for r, s, m, v in db.execute("SELECT * FROM run_measures")}
        assert got[(1, "count", "rows")] == 7, "a stage's returned counts are measures"
        assert {k[1:] for k in got if k[0] == 1} == {
            ("count", "rows"), ("count", "seconds"), ("quiet", "run"), ("quiet", "seconds")}, got
        assert got[(1, "quiet", "run")] == 1, "a stage knows its run (LFG_RUN)"
        assert {k[1:] for k in got if k[0] == 2} == {("count", "rows"), ("count", "seconds")}, \
            "a failed stage records nothing; the ones before it stay"
        db.close()


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest_runs()
        for _name, _spec in STAGES + ON_REQUEST:
            assert callable(call(_spec)), _name
        assert [n for n, _ in STAGES] == sorted(
            {n for n, _ in STAGES}, key=[n for n, _ in STAGES].index), \
            "duplicate stage"
        print("run.py selftest OK (%d stages)" % len(STAGES))
        raise SystemExit(0)
    raise SystemExit(main(sys.argv[1:]))
