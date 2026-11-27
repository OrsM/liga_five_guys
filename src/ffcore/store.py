"""The record store: one immutable directory a run, store/runs/<run_id>/, beside data/ ($FF_ROOT/../store). Nothing
but publish (called under the run lock) writes under it; every reader opens files rb and takes the loaders here.
publish stages the whole run in store/.tmp-<pid>, fsyncs, makes it read-only and renames it into place: a run is
visible entirely or not at all, run.json (the Run row and each file's row count) being written last. A directory
without run.json, or a .tmp-*, is debris that the next publish removes. Publishing a run id twice is a StoreError.
Loaders return the NamedTuples of ffcore.records; a file whose lines or count do not match what was written is a
StoreError naming the file and line."""
from __future__ import annotations

import json
import os
import shutil
import stat
import sys
import tempfile
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from ffcore.records import (GRADE_CELL_DTYPE, KINDS, LATEST, GradeCell, Run, StoreError, decode, encode, grade_cell_array,
                            grade_cell_rows, header, parse_header)

__all__ = ["RunRecords", "clear", "grade_cells", "latest_run", "publish", "records", "runs", "stage", "commit", "store_root"]


def store_root() -> Path:
    """The store, beside the data directory FF_ROOT names (default ./data)."""
    return Path(os.environ.get("FF_ROOT", "./data")).parent / "store"


def _runs_dir() -> Path:
    return store_root() / "runs"


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _remove(path: Path) -> None:
    for p in [path, *path.rglob("*")]:
        if p.is_dir():
            p.chmod(stat.S_IRWXU)
    shutil.rmtree(path)


def clear() -> None:
    """Remove the whole store, published runs included; nothing when there is none."""
    if store_root().exists():
        _remove(store_root())


def _sweep(keep: Path) -> None:
    root = store_root()
    for d in [*(p for p in root.glob(".tmp-*") if p != keep), *(p for p in _runs_dir().glob("*") if not (p / "run.json").exists())]:
        _remove(d)


def _write(path: Path, data: bytes) -> None:
    with open(path, "xb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def stage(run: Run, **kinds: Iterable[Any]) -> Path:
    """Write the run's files into store/.tmp-<pid> and return that directory; nothing is visible yet. kinds maps a
    record kind (not runs) to its rows; grade_cell takes GradeCell rows or a GRADE_CELL_DTYPE array."""
    unknown = sorted(k for k in kinds if k not in KINDS or k == "runs")
    if unknown:
        raise StoreError("not a record kind of a run: %s" % unknown)
    root = store_root()
    root.mkdir(parents=True, exist_ok=True)
    tmp = root / (".tmp-%d" % os.getpid())
    if tmp.exists():
        _remove(tmp)
    tmp.mkdir()
    counts: dict[str, int] = {}
    for kind, rows in kinds.items():
        path = tmp / KINDS[kind].filename
        if kind == "grade_cell":
            array = rows if isinstance(rows, np.ndarray) else grade_cell_array(rows)
            if array.dtype != GRADE_CELL_DTYPE:
                raise StoreError("grade_cell array has dtype %s" % array.dtype)
            with open(path, "xb") as f:
                np.save(f, array)
                f.flush()
                os.fsync(f.fileno())
            counts[kind] = len(array)
            continue
        lines = [header(kind)]
        for r in rows:
            if not isinstance(r, KINDS[kind].row):
                raise StoreError("%s row is a %s" % (kind, type(r).__name__))
            if "run_id" in r._fields and r.run_id != run.run_id:
                raise StoreError("%s row of run %d in run %d" % (kind, r.run_id, run.run_id))
            lines.append(encode(r))
        _write(path, "".join(lines).encode())
        counts[kind] = len(lines) - 1
    meta = {"format": "runs", "v": LATEST["runs"], "run": run._asdict(), "counts": counts}
    _write(tmp / "run.json", (json.dumps(meta) + "\n").encode())
    _fsync_dir(tmp)
    for p in tmp.iterdir():
        p.chmod(0o444)
    return tmp


def commit(tmp: Path, run_id: int) -> Path:
    """Rename a staged directory to store/runs/<run_id>/, sweeping debris first; StoreError when that run exists."""
    runs_dir = _runs_dir()
    runs_dir.mkdir(parents=True, exist_ok=True)
    _sweep(tmp)
    final = runs_dir / str(run_id)
    if final.exists():
        _remove(tmp)
        raise StoreError("run %d is already published" % run_id)
    os.rename(tmp, final)
    final.chmod(0o555)
    _fsync_dir(runs_dir)
    return final


def publish(run: Run, **kinds: Iterable[Any]) -> Path:
    """Stage and commit one run: its Run row and its rows of each kind given. Returns the run's directory."""
    return commit(stage(run, **kinds), run.run_id)


class RunRecords:
    """The records of one run, collected in memory and published once. add(kind, rows) keeps the rows of a kind in the
    order given, a mapping lacking run_id taking this run's (a kind's row type is ffcore.records.KINDS); rows(kind)
    returns what was added; publish(run) writes the Run row and every kind added as one store directory
    (store.publish), so a run that does not reach it leaves nothing. commit_sha is the code the run is."""

    def __init__(self, run: int, commit_sha: str) -> None:
        self.run = run
        self.commit_sha = commit_sha
        self._kinds: dict[str, list[Any]] = {}

    @classmethod
    def after_latest(cls, commit_sha: str) -> "RunRecords":
        """The RunRecords of the run after the latest published one (called under the run lock)."""
        latest = latest_run()
        return cls((latest.run_id if latest else 0) + 1, commit_sha)

    def add(self, kind: str, rows: Iterable[Any]) -> None:
        row = KINDS[kind].row
        owned = {"run_id": self.run} if "run_id" in row._fields else {}
        self._kinds.setdefault(kind, []).extend(r if isinstance(r, row) else row(**owned, **r) for r in rows)

    def rows(self, kind: str) -> list[Any]:
        return list(self._kinds.get(kind, ()))

    def publish(self, run: Run) -> Path:
        return publish(run, **self._kinds)


def _meta(d: Path) -> dict[str, Any]:
    path = d / "run.json"
    with open(path, "rb") as f:
        raw = f.read()
    try:
        meta = json.loads(raw)
        if meta.get("format") != "runs" or ("runs", meta.get("v")) not in {("runs", LATEST["runs"])}:
            raise ValueError("format %r v %r" % (meta.get("format"), meta.get("v")))
        meta["run"] = Run(**meta["run"])
        return meta  # type: ignore[no-any-return]
    except (ValueError, TypeError, AttributeError) as e:
        raise StoreError("%s: %s" % (path, e)) from None


def runs() -> list[Run]:
    """Every visible run's Run row, by run_id."""
    return sorted((_meta(d)["run"] for d in _runs_dir().glob("*") if (d / "run.json").exists()),
                  key=lambda r: r.run_id)


def latest_run() -> Run | None:
    """The visible run with the greatest run_id, or None when there is none."""
    seen = runs()
    return seen[-1] if seen else None


def _lines(path: Path, kind: str, expected: int) -> Iterator[Any]:
    with open(path, "rb") as f:
        version = parse_header(f.readline(), kind)
        n = 0
        for n, line in enumerate(f, 1):
            try:
                yield decode(kind, version, line)
            except StoreError as e:
                raise StoreError("%s line %d: %s" % (path, n + 1, e)) from None
        if n != expected:
            raise StoreError("%s has %d rows, run.json says %d" % (path, n, expected))


def grade_cells(run_id: int) -> npt.NDArray[Any]:
    """The run's grade_cell array (GRADE_CELL_DTYPE), empty when it has none."""
    d = _runs_dir() / str(run_id)
    meta = _meta(d)
    path = d / KINDS["grade_cell"].filename
    if "grade_cell" not in meta["counts"]:
        return np.empty(0, dtype=GRADE_CELL_DTYPE)
    with open(path, "rb") as f:
        array: npt.NDArray[Any] = np.load(f, allow_pickle=False)
    if array.dtype != GRADE_CELL_DTYPE or len(array) != meta["counts"]["grade_cell"]:
        raise StoreError("%s: dtype %s with %d rows, run.json says %d" % (
            path, array.dtype, len(array), meta["counts"]["grade_cell"]))
    return array


def records(kind: str, run_id: int | None = None) -> Iterator[Any]:
    """Every row of a kind, runs in run_id order and each file in its written order (all runs when run_id is None).
    The rows of kind runs are runs()'s."""
    if kind not in KINDS:
        raise StoreError("no record kind %r" % kind)
    if kind == "runs":
        yield from (r for r in runs() if run_id in (None, r.run_id))
        return
    for r in runs():
        if run_id not in (None, r.run_id):
            continue
        meta = _meta(_runs_dir() / str(r.run_id))
        if kind not in meta["counts"]:
            continue
        if kind == "grade_cell":
            yield from grade_cell_rows(grade_cells(r.run_id), r.run_id)
        else:
            yield from _lines(_runs_dir() / str(r.run_id) / KINDS[kind].filename, kind, meta["counts"][kind])


def _selftest() -> None:
    import random
    import time

    from ffcore import records as rec

    def run_row(i: int) -> Run:
        return Run(i, "2026-11-0%dT10:00:00Z" % i, "2026-11-0%dT10:00:00Z" % i, "service", "abc%d" % i,
                   "2026-11-0%dT10:01:00Z" % i, 1, 0.1 * i)

    def sample(i: int) -> dict[str, list[Any]]:
        rng = random.Random(i)
        return {
            "run_measures": [rec.RunMeasure(i, "board", "secs", rng.random()), rec.RunMeasure(i, "board", "n", 3.0)],
            "measure_log": [rec.MeasureLog("2026-11-0%dT10:00:00Z" % i, "abc", "accuracy", "rmse@5", rng.random())],
            "params": [rec.Param(i, "auction", "", rng.random() * 1e-300, "fitted", None), rec.Param(i, "x", "t", 2.0, "constant", 5)],
            "auction_calibration": [rec.AuctionCalibration(i, "all", "0-1", 4, 0.5, rng.random(), 0)],
            "auction_brier": [rec.AuctionBrier(i, "all", 4, rng.random(), 0.25)],
            "grade_cell": [GradeCell(i, 3, 4, "Pedri ñ", rng.random(), None, 1), GradeCell(i, 3, 5, "Gavi", 2.0, 0.5, 0)],
            "dec_opportunity": [rec.DecOpportunity("2026-11-0%dT1000Z" % i, "Pedri", "MED", "open", None, None, 4, None,
                                                   rng.random(), None, None, None, 4.2, 0.3, None)],
            "dec_offer": [rec.DecOffer("2026-11-0%dT1000Z" % i, "Pedri", "9%d" % i, "2026-11-01T09:00:00Z", 2e6, 1.9e6, rng.random(), None,
                                       5, 0.0, -3.0, True, "", "", 0.1)],
            "place_dry": [rec.PlaceDry("2026-11-0%dT20:00Z" % i, "2026-11-0%dT21:06:00Z" % i, "2026-11-0%d" % i, 1, "Pedri", "m%d" % i,
                                       2e6, 2.5e6, 2.4e6, 4e5, True, None), rec.PlaceDry("c", "a", "d", 2, "Gavi", None, 1.0, 2.0, None, None, False, "no bid")],
            "sale_score": [rec.SaleScore(i, "miguel", "all", "lock", 30, rng.random(), 0.05, -1.0, 1.0, -2.0, 2.0)]}

    with tempfile.TemporaryDirectory() as tmp:
        os.environ["FF_ROOT"] = str(Path(tmp) / "data")
        assert store_root() == Path(tmp) / "store" and runs() == [] and latest_run() is None
        assert list(records("params")) == [], "an absent store reads as empty"

        orphan = stage(run_row(1), **sample(1))
        assert runs() == [], "staged files without the rename are invisible"
        assert (orphan / "run.json").exists()
        child = os.fork()
        if child == 0:
            stage(run_row(9), **sample(9))
            os._exit(0)
        os.waitpid(child, 0)
        assert runs() == [] and list(records("params")) == []
        (store_root() / "runs" / "5").mkdir(parents=True)
        (store_root() / "runs" / "5" / "params.jsonl").write_bytes(b"junk")
        assert runs() == [], "a directory without run.json is invisible"

        for i in (1, 2, 3):
            publish(run_row(i), **sample(i))
        assert sorted(p.name for p in store_root().iterdir()) == ["runs"] and \
            sorted(p.name for p in (store_root() / "runs").iterdir()) == ["1", "2", "3"], "debris is swept by publish"
        assert [r.run_id for r in runs()] == [1, 2, 3] and latest_run() == run_row(3)
        d = store_root() / "runs" / "1"
        assert not os.access(d, os.W_OK) and not any(os.access(p, os.W_OK) for p in d.iterdir()), "published read-only"

        for kind, spec in KINDS.items():
            if kind == "runs":
                assert list(records(kind)) == [run_row(i) for i in (1, 2, 3)]
                continue
            want = [r for i in (1, 2, 3) for r in sample(i)[kind]]
            got = list(records(kind))
            assert got == want and [[type(a) for a in r] for r in got] == [[type(a) for a in r] for r in want], kind
            assert list(records(kind, 2)) == sample(2)[kind], "one run's rows"
        assert list(records("params", 99)) == []

        try:
            publish(run_row(2), **sample(2))
        except StoreError as e:
            assert "already published" in str(e)
        else:
            raise AssertionError("a run id publishes once")
        assert [r.run_id for r in runs()] == [1, 2, 3] and list(records("params", 2)) == sample(2)["params"]
        try:
            publish(run_row(4), params=[rec.Param(5, "a", "", 1.0, "constant", None)])
        except StoreError:
            pass
        else:
            raise AssertionError("a row of another run is refused")
        assert [r.run_id for r in runs()] == [1, 2, 3]

        path = store_root() / "runs" / "2" / "params.jsonl"
        for damaged in (path.read_bytes()[:-9], path.read_bytes().replace(b"term", b"trem", 1),
                        path.read_bytes().rsplit(b"\n", 2)[0] + b"\n"):
            path.chmod(0o644)
            path.write_bytes(damaged)
            try:
                list(records("params", 2))
            except StoreError as e:
                assert "params.jsonl" in str(e), e
            else:
                raise AssertionError("accepted a damaged file")
        assert list(records("params", 1)) == sample(1)["params"], "damage in one run is that run's"

        rng = np.random.default_rng(3)
        n = 1_200_000
        big = np.zeros(n, dtype=GRADE_CELL_DTYPE)
        big["from_j"] = rng.integers(1, 40, n)
        big["jornada"] = big["from_j"] + rng.integers(0, 6, n)
        big["player"] = rng.integers(0, 10**6, n).astype("S24")
        big["predicted"] = rng.random(n)
        big["plays"] = np.where(rng.random(n) < 0.3, np.nan, rng.random(n))
        big["flagged"] = rng.integers(0, 2, n)
        publish(run_row(4), grade_cell=big)
        start = time.perf_counter()
        back = grade_cells(4)
        secs = time.perf_counter() - start
        assert back.dtype == big.dtype and back.tobytes() == big.tobytes(), "bits, NaN included"
        assert secs < 1.0, "load of 1.2M grade_cell rows took %.2f s" % secs
        first = next(records("grade_cell", 4))
        assert first == GradeCell(4, int(big[0]["from_j"]), int(big[0]["jornada"]), bytes(big[0]["player"]).decode(),
                                  float(big[0]["predicted"]), None if big[0]["plays"] != big[0]["plays"] else float(big[0]["plays"]),
                                  int(big[0]["flagged"]))
        assert len(grade_cells(1)) == 2 and len(grade_cells(2)) == 2
        _remove(store_root())

        assert RunRecords.after_latest("x").run == 1
        pending = RunRecords(3, "abc")
        pending.add("params", [{"component": "c", "term": "t", "value": 1.0, "kind": "fitted", "n": None}, rec.Param(3, "d", "", 2.0, "constant", 4)])
        pending.add("dec_opportunity", [])
        assert [(p.run_id, p.component) for p in pending.rows("params")] == [(3, "c"), (3, "d")] and pending.rows("auction_brier") == [], \
            "a mapping takes the run's id; rows come back in the order added"
        assert runs() == [], "nothing is visible before publish"
        done = Run(3, "t", "a", "service", "abc", "f", 1, 1.0)
        pending.publish(done)
        assert runs() == [done] and list(records("params", 3)) == pending.rows("params"), "published once, whole"
        assert RunRecords.after_latest("x").run == 4, "the run after the latest published"
        try:
            pending.add("params", [{"component": "c"}])
        except TypeError:
            pass
        else:
            raise AssertionError("a row lacking a field is refused")
        clear()
        os.environ.pop("FF_ROOT")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
