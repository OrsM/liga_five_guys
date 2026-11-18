
from __future__ import annotations

import collections
import gc
import importlib
import os
import resource
import subprocess
import sys
import threading
import time
import traceback
from collections.abc import Callable, Collection, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

from ffcore import store
from ffcore.clock import run_now
from ffcore.parse import stamp
from ffcore.db import RunRecords, set_now
from ffcore.records import Run


class Stage(NamedTuple):
    """A stage of a run: its name, the function (module:name) that does it and whether it makes the report (a failure
    ends the run)."""
    name: str
    spec: str
    makes: bool


# Every run, in order. A stage that makes the report stops the run when it fails; one after it (the report is out)
# is noted and the run goes on.
STAGES: list[Stage] = [
    Stage("parse", "ingest:parse", True),
    Stage("crosswalk", "crosswalk:main", True),
    Stage("board", "board:main", True),
    Stage("publish", "publish:main", False),
    Stage("grade", "grading:record_grades", False),
    Stage("health", "health:main", False),
]

COLLECTING = frozenset({"board", "grade", "health"})  # the stages passed the run's RunRecords


def call(spec: str) -> Callable[..., object]:
    mod, fn = spec.split(":")
    return getattr(importlib.import_module(mod), fn)


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


def _start() -> tuple[RunRecords, Run]:
    """The run begun: the id after the latest published one (the run lock is held), t set to its time (the wall clock,
    LFG_NOW when pinned: every stage reads the league as of it), its RunRecords and its Run row so far."""
    now = run_now()
    set_now(now)
    records = RunRecords.after_latest(_commit())
    return records, Run(records.run, _utc(), stamp(now), os.environ.get("LFG_TRIGGER", "service"), records.commit_sha, None, None, None)


def _measure(records: RunRecords, stage: str, seconds: float, counts: object) -> None:
    got = {**(counts if isinstance(counts, Mapping) else {}), "seconds": seconds}
    records.add("run_measures", [{"stage": stage, "measure": k, "value": float(v)} for k, v in got.items()])


def _finished(begun: Run, ok: bool) -> Run:
    return begun._replace(finished=_utc(), ok=int(ok), peak_mb=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)


def main(argv: list[str], every: Sequence[Stage] = STAGES, collecting: Collection[str] = COLLECTING) -> int:
    """Run the stages argv names (all by default), collecting the run's records (run_measures for each stage, the rest
    from the stages named in collecting, which are passed the RunRecords) and publishing them once with the Run row at the end, ok only if every
    stage was; a run killed before then leaves nothing in the store. 1 if a stage that makes the report failed."""
    records, begun = _start()
    failed: list[tuple[str, bool]] = [("", True)]
    try:
        failed = _stages(argv, every, records, collecting)
    finally:
        records.publish(_finished(begun, not failed))
    return 1 if any(made for _n, made in failed) else 0


def _stages(argv: list[str], every: Sequence[Stage], records: RunRecords, collecting: Collection[str]) -> list[tuple[str, bool]]:
    """The stages that failed, each with whether it makes the report (the
    first such ends the run)."""
    want = [a for a in argv if not a.startswith("-")]
    stages = sorted((s for s in every if s.name in want), key=lambda s: want.index(s.name)) if want else list(every)

    started, times, failed = time.time(), [], []
    for name, spec, makes in stages:
        t0 = time.time()
        print("%s" % name, flush=True)
        hits: collections.Counter[str] = collections.Counter()
        stop = threading.Event()
        threading.Thread(target=_sample, daemon=True, args=(
            hits, stop, threading.get_ident())).start()
        counts: object = None
        try:
            counts = call(spec)(records) if name in collecting else call(spec)()
        except SystemExit as e:
            if e.code:
                print("  FAILED: %s exited %s" % (name, e.code))
                failed.append((name, makes))
        except Exception:
            traceback.print_exc()
            print("  FAILED: %s" % name)
            failed.append((name, makes))
        gc.collect()
        stop.set()
        if failed and failed[-1][0] == name:
            if makes:
                return failed
            continue
        times.append((time.time() - t0, name))
        _measure(records, name, times[-1][0], counts)
        if times[-1][0] > float(os.environ.get("LFG_SLOW_S", 8)):
            print("  slow %s, where: %s" % (name, "  ".join(
                "%d%% %s" % (100 * v // sum(hits.values()), k)
                for k, v in hits.most_common(5))))

    print("  %s" % "  ".join("%s %.1fs" % (n, t) for t, n in times))
    print("  %d stages in %.1fs" % (len(times), time.time() - started))
    return failed


def _selftest_runs() -> None:
    import tempfile
    import types

    fake = types.ModuleType("fake_stages")

    def boom() -> None:
        raise RuntimeError("stage broke")

    def killed(records: RunRecords) -> None:
        records.add("params", [{"component": "c", "term": "t", "value": 1.0, "kind": "constant", "n": None}])
        raise SystemExit(1)
    fake.counted = lambda: {"rows": 7}  # type: ignore[attr-defined]
    fake.quiet = lambda records: {"run": records.run}  # type: ignore[attr-defined]
    fake.boom = boom  # type: ignore[attr-defined]
    fake.killed = killed  # type: ignore[attr-defined]
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
    asked = os.environ.pop("LFG_TRIGGER", None)  # the phone's button sets it: say what we want
    root = os.environ.get("FF_ROOT")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["FF_ROOT"] = str(Path(tmp) / "data")
            assert main([], [Stage("count", "fake_stages:counted", True),
                             Stage("quiet", "fake_stages:quiet", False)], {"quiet"}) == 0
            assert main([], [Stage("count", "fake_stages:counted", True),
                             Stage("boom", "fake_stages:boom", True),
                             Stage("quiet", "fake_stages:quiet", False)], {"quiet"}) == 1
            os.environ["LFG_TRIGGER"] = "button"
            assert main([], [Stage("boom", "fake_stages:boom", False),
                             Stage("quiet", "fake_stages:quiet", False)], {"quiet"}) == 0, \
                "a stage after the report failing does not fail the run's exit"
            assert main([], [Stage("killed", "fake_stages:killed", False)], {"killed"}) == 0
            runs = store.runs()
            assert [(r.run_id, r.ok, r.finished is not None, r.trigger) for r in runs] == [
                (1, 1, True, "service"), (2, 0, True, "service"), (3, 0, True, "button"), (4, 0, True, "button")], \
                "the trigger is how the run was asked for: LFG_TRIGGER, else the service; ids follow the store's latest"
            assert all(r.commit_sha and r.as_of and r.peak_mb for r in runs), "commit, clock and memory recorded"
            got = {(m.run_id, m.stage, m.measure): m.value for m in store.records("run_measures")}
            assert got[(1, "count", "rows")] == 7, "a stage's returned counts are measures"
            assert {k[1:] for k in got if k[0] == 1} == {
                ("count", "rows"), ("count", "seconds"), ("quiet", "run"), ("quiet", "seconds")}, got
            assert got[(1, "quiet", "run")] == 1, "a stage is passed the run's records and knows its run"
            assert {k[1:] for k in got if k[0] == 2} == {("count", "rows"), ("count", "seconds")}, \
                "a failed stage records nothing; the ones before it stay; one that makes the report ends the run"
            assert {k[1:] for k in got if k[0] == 3} == {("quiet", "run"), ("quiet", "seconds")}, \
                "after the report, the run goes on past a failed stage (the run is not ok)"
            assert [p.run_id for p in store.records("params")] == [4], "what a failed stage collected is published with its run"
            store.clear()
    finally:
        os.environ.pop("LFG_TRIGGER", None)
        if asked is not None:
            os.environ["LFG_TRIGGER"] = asked
        if root is None:
            os.environ.pop("FF_ROOT", None)
        else:
            os.environ["FF_ROOT"] = root


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest_runs()
        for _stage in STAGES:
            assert callable(call(_stage.spec)), _stage.name
        _names = [st.name for st in STAGES]
        assert len(_names) == len(set(_names)), "duplicate stage"
        assert COLLECTING <= set(_names), "every collecting stage is a stage"
        assert [st.makes for st in STAGES] == sorted((st.makes for st in STAGES), reverse=True), \
            "the stages that make the report come first"
        print("run.py selftest OK (%d stages)" % len(STAGES))
        raise SystemExit(0)
    raise SystemExit(main(sys.argv[1:]))
