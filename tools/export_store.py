"""Export the record tables of a SQLite file into a record store (ffcore.store) and prove it row by row.

    export_store.py [--replace] [--config league.ini] DB ROOT     export DB into ROOT/store, then verify
    export_store.py --verify-only DB ROOT                         verify only

DB is opened with plain sqlite3 mode=ro and never written. ROOT holds the store as ROOT/store (the store root is
beside FF_ROOT = ROOT/data, which this tool sets for itself). An existing non-empty store is refused unless
--replace, which clears it first. Published run by run in run_id order, the same DB gives the same bytes.

Table order is: runs by run_id; run_measures and params by run_id then rowid; measure_log by rowid; the keyed
tables (auction_calibration, auction_brier, grade_cell, dec_opportunity) by primary key.

Attachment rule: measure_log and dec_opportunity rows carry no run_id. A row belongs to the run with the greatest
`started` that is not after its time (`at`; for dec_opportunity the `cut`, read as HH:MM:00 of its minute), ties to
the greater run_id; a run whose `started` is empty is never chosen; a row earlier than every run belongs to the
earliest. The attached run_id must not decrease along table order, otherwise records(kind) could not return the
table's order and the export fails. Under it records(kind) equals the table row for row, and latest() over the
store's measure_log equals the measure_latest view.

league_config: the table is gone from the schema; league.load_config reads me and budget from inputs/league.ini
(relative to the working directory, or the --config path). They are exported as ROOT/store/league.json.

verify: per kind the row count and the sha256 of each row's canonical JSON, table against store in order; one line
per kind: kind, rows, EQUAL (or DIFFERENT with the first differing pair). Exit 1 unless all are EQUAL."""
from __future__ import annotations

import bisect
import hashlib
import json
import os
import re
import resource
import sqlite3
import sys
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from itertools import zip_longest
from pathlib import Path
from typing import Any, NamedTuple

from ffcore import store
from ffcore.league import load_config
from ffcore.records import KINDS, MeasureLog, Run, StoreError

LEAGUE_FILE = "league.json"


class Order(NamedTuple):
    """How a kind is read from its table: its columns (the row type's fields) and the ORDER BY."""
    run_keyed: bool
    order: str


ORDERS: Mapping[str, Order] = {
    "runs": Order(False, "run_id"),
    "run_measures": Order(True, "run_id, rowid"),
    "measure_log": Order(False, "rowid"),
    "params": Order(True, "run_id, rowid"),
    "auction_calibration": Order(True, "run_id, scope, bin"),
    "auction_brier": Order(True, "run_id, scope"),
    "grade_cell": Order(True, "run_id, from_j, jornada, player"),
    "dec_opportunity": Order(False, "cut, player")}


class Verdict(NamedTuple):
    kind: str
    rows: int
    first_difference: tuple[Any, Any] | None


def connect_ro(db: Path) -> sqlite3.Connection:
    return sqlite3.connect("file:%s?mode=ro" % db, uri=True)


def table_rows(db: sqlite3.Connection, kind: str, run_id: int | None = None) -> Iterator[Any]:
    """A kind's rows from its table as its NamedTuples, in the order the module docstring gives; one run's when
    run_id is given (kinds keyed by run only)."""
    spec, how = KINDS[kind], ORDERS[kind]
    where = "" if run_id is None else " WHERE run_id = %d" % run_id
    cur = db.execute("SELECT %s FROM %s%s ORDER BY %s" % (", ".join(spec.row._fields), kind, where, how.order))
    for r in cur:
        yield spec.row(*r)


def normal_time(stamp: str) -> str:
    """A measure_log `at` or dec_opportunity `cut` as the 'YYYY-MM-DDTHH:MM:SSZ' of a run's `started`."""
    m = re.fullmatch(r"(\d{4}-\d\d-\d\dT)(\d\d):?(\d\d)(?::?(\d\d))?Z", stamp)
    if m is None:
        raise StoreError("not a time: %r" % stamp)
    return "%s%s:%s:%sZ" % (m[1], m[2], m[3], m[4] or "00")


def attachment(runs: Sequence[Run]) -> Callable[[str], int]:
    """The run_id a row stamped at (normal_time) belongs to, by the rule in the module docstring."""
    chosen = sorted((r.started, r.run_id) for r in runs if r.started)
    if not chosen:
        raise StoreError("no run has a start time: nothing to attach measure_log and dec_opportunity rows to")
    starts = [s for s, _ in chosen]

    def run_of(at: str) -> int:
        return chosen[max(bisect.bisect_right(starts, at) - 1, 0)][1]
    return run_of


def attached(rows: Sequence[Any], stamp: Callable[[Any], str], run_of: Callable[[str], int]) -> dict[int, list[Any]]:
    """The rows (in table order) grouped by the run they attach to; StoreError when the run_id decreases."""
    groups: dict[int, list[Any]] = {}
    last = -1
    for r in rows:
        run = run_of(normal_time(stamp(r)))
        if run < last:
            raise StoreError("row %r attaches to run %d after a row of run %d: table order is not run order" % (r, run, last))
        last = run
        groups.setdefault(run, []).append(r)
    return groups


def digest(row: Any) -> str:
    """sha256 of a row's canonical JSON (field names sorted, no spaces)."""
    return hashlib.sha256(json.dumps(row._asdict(), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def write_league(config: str) -> None:
    cfg = load_config(config)
    path = store.store_root() / LEAGUE_FILE
    with open(path, "xb") as f:
        f.write((json.dumps({"format": "league_config", "v": 1, "me": cfg.me, "budget": cfg.budget}) + "\n").encode())
        f.flush()
        os.fsync(f.fileno())
    path.chmod(0o444)


def export(db: sqlite3.Connection, config: str) -> int:
    """Publish every run of the file into the store with its rows, then league.json; the number of runs."""
    runs = list(table_rows(db, "runs"))
    run_of = attachment(runs)
    logs = attached(list(table_rows(db, "measure_log")), lambda r: r.at, run_of)
    opps = attached(list(table_rows(db, "dec_opportunity")), lambda r: r.cut, run_of)
    for run in runs:
        store.publish(
            run,
            **{k: table_rows(db, k, run.run_id) for k, h in ORDERS.items() if h.run_keyed and k != "grade_cell"},
            grade_cell=table_rows(db, "grade_cell", run.run_id),
            measure_log=logs.get(run.run_id, []),
            dec_opportunity=opps.get(run.run_id, []))
    write_league(config)
    return len(runs)


def compare(kind: str, table: Iterator[Any], stored: Iterator[Any]) -> Verdict:
    """Count and per-row digests of the two sequences, in order; the first differing pair when they are not equal."""
    n = 0
    for t, s in zip_longest(table, stored):
        if t is None or s is None or digest(t) != digest(s):
            return Verdict(kind, n, (t, s))
        n += 1
    return Verdict(kind, n, None)


def verify(db: sqlite3.Connection) -> list[Verdict]:
    """One Verdict per kind: the table's rows against records(kind) of the store."""
    return [compare(k, table_rows(db, k), store.records(k)) for k in KINDS]


def report(verdicts: Sequence[Verdict]) -> bool:
    for v in verdicts:
        print("%-20s %9d %s" % (v.kind, v.rows, "EQUAL" if v.first_difference is None else "DIFFERENT: table %r store %r" % v.first_difference))
    return all(v.first_difference is None for v in verdicts)


def main(argv: Sequence[str]) -> int:
    args = list(argv)
    flags = {a for a in args if a in ("--replace", "--verify-only")}
    config = "league.ini"
    if "--config" in args:
        i = args.index("--config")
        config = args[i + 1]
        del args[i:i + 2]
    paths = [a for a in args if a not in flags]
    if len(paths) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    db_path, root = Path(paths[0]), Path(paths[1])
    os.environ["FF_ROOT"] = str(root / "data")
    started = time.perf_counter()
    db = connect_ro(db_path)
    if "--verify-only" not in flags:
        if store.store_root().exists() and any(store.store_root().iterdir()):
            if "--replace" not in flags:
                print("refusing: %s is not empty (use --replace)" % store.store_root(), file=sys.stderr)
                return 2
            store.clear()
        n = export(db, config)
        print("exported %d runs and %s to %s" % (n, LEAGUE_FILE, store.store_root()))
    ok = report(verify(db))
    print("%.1f s, peak %.0f MB" % (time.perf_counter() - started, resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024))
    return 0 if ok else 1


def _selftest() -> None:
    import contextlib
    import io
    import tempfile

    from ffcore.records import latest

    def table_ddl(name: str) -> str:
        for sql in ("src/ffcore/schema.sql", "src/ffcore/decide.sql"):
            m = re.search(r"CREATE TABLE IF NOT EXISTS %s \(.*?\n\)[^;]*;" % name, Path(sql).read_text(), re.S)
            if m:
                return m[0]
        raise AssertionError(name)

    def run(i: int, started: str) -> Run:
        return Run(i, started, started, "service", "abc", None, None, 1.5)

    runs = [run(1, "2026-11-01T10:00:00Z"), run(2, ""), run(3, "2026-11-02T10:00:00Z"), run(4, "2026-11-03T10:00:00Z")]
    run_of = attachment(runs)
    assert [run_of(t) for t in ("2026-10-01T00:00:00Z", "2026-11-01T10:00:00Z", "2026-11-02T09:59:59Z", "2026-11-02T10:00:00Z",
                                "2026-12-01T00:00:00Z")] == [1, 1, 1, 3, 4], "last start not after the time; never the empty one"
    assert normal_time("2026-11-08T2222Z") == "2026-11-08T22:22:00Z" and normal_time("2026-11-06T07:21:18Z") == "2026-11-06T07:21:18Z"
    assert attachment([run(7, "2026-11-01T10:00:00Z"), run(5, "2026-11-01T10:00:00Z")])("2026-11-01T10:00:00Z") == 7, "ties to the greater run_id"
    try:
        attached([MeasureLog("2026-11-03T10:00:00Z", "a", "m", "k", 1.0), MeasureLog("2026-11-01T10:00:00Z", "a", "m", "k", 2.0)],
                 lambda r: r.at, run_of)
    except StoreError:
        pass
    else:
        raise AssertionError("table order against run order is refused")

    saved = os.environ.get("FF_ROOT")
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        path = base / "lfg.db"
        db = sqlite3.connect(path)
        for name in KINDS:
            db.execute(table_ddl(name))
        view = re.search(r"CREATE VIEW measure_latest AS.*?;", Path("src/ffcore/schema.sql").read_text(), re.S)
        assert view is not None
        db.execute(view[0])
        db.executemany("INSERT INTO runs VALUES (?,?,?,?,?,?,?,?)", runs)
        for r in runs:
            db.execute("INSERT INTO run_measures VALUES (?,?,?,?)", (r.run_id, "board", "secs", 0.1 * r.run_id))
            db.execute("INSERT INTO params VALUES (?,?,?,?,?,?)", (r.run_id, "c", "t", 1e-300 * r.run_id, "fitted", None))
            db.execute("INSERT INTO auction_calibration VALUES (?,?,?,?,?,?,?)", (r.run_id, "all", "0-1", 3, 0.5, 0.25, 0))
            db.execute("INSERT INTO auction_brier VALUES (?,?,?,?,?)", (r.run_id, "all", 3, 0.2, 0.3))
        for j, (a, who, plays) in enumerate([(1, "Pedri ñ", None), (1, "Gavi", 0.5), (3, "Pedri ñ", 0.25), (3, "Gavi", None)]):
            db.execute("INSERT INTO grade_cell VALUES (?,?,?,?,?,?,?)", (a, 2, j, who, 0.1 + j, plays, j % 2))
        for i, (at, key) in enumerate([("2026-10-30T00:00:00Z", "a"), ("2026-11-01T10:00:00Z", "a"), ("2026-11-01T10:00:00Z", "b"),
                                       ("2026-11-02T12:00:00Z", "a"), ("2026-11-04T00:00:00Z", "a")]):
            db.execute("INSERT INTO measure_log VALUES (?,?,?,?,?)", (at, "sha", "accuracy", key, 1 / (i + 3)))
        for cut, who in (("2026-10-30T2200Z", "Pedri"), ("2026-11-01T1000Z", "Gavi"), ("2026-11-03T1001Z", "Pedri"), ("2026-11-03T1001Z", "Gavi")):
            db.execute("INSERT INTO dec_opportunity VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (cut, who, "MED", "open", None, None, 4, None, 1.5, None, None, None, 4.2, 0.3, None))
        db.commit()
        db.close()
        ini = base / "league.ini"
        ini.write_text("[league]\nme = someone\nbudget = 90.000.000\n", encoding="utf-8")

        def tree(root: Path) -> dict[str, str]:
            return {str(p.relative_to(root)): hashlib.md5(p.read_bytes()).hexdigest() for p in sorted((root / "store").rglob("*")) if p.is_file()}

        def cli(*args: str) -> tuple[int, str]:
            out = io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                return main([*args]), out.getvalue()

        code, out = cli("--config", str(ini), str(path), str(base / "a"))
        assert code == 0 and out.count("EQUAL") == len(KINDS) and "DIFFERENT" not in out, out
        mro = connect_ro(path)
        rows_by_run = {r.run_id: [x for x in store.records("dec_opportunity", r.run_id)] for r in runs}
        assert [len(rows_by_run[i]) for i in (1, 2, 3, 4)] == [2, 0, 0, 2], "attached by cut to the run before it"
        in_store = list(store.records("measure_log"))
        assert in_store == list(table_rows(mro, "measure_log")), "records() is the table, in order"
        want = {(m, k): (v, at, sha, p, pat, psha, ch) for m, k, v, at, sha, p, pat, psha, ch in mro.execute("SELECT * FROM measure_latest")}
        got = {k: (p.last.value, p.last.at, p.last.commit_sha, p.previous.value if p.previous else None,
                   p.previous.at if p.previous else None, p.previous.commit_sha if p.previous else None,
                   None if p.previous is None else p.last.value - p.previous.value)
               for k, p in latest(in_store, lambda r: (r.measure, r.key)).items()}
        assert got == want and len(got) == 2, "latest over the store is measure_latest"
        league = json.loads((base / "a" / "store" / LEAGUE_FILE).read_text())
        assert (league["me"], league["budget"]) == ("someone", 90e6)
        assert [c.player for c in store.records("grade_cell", 1)] == ["Pedri ñ", "Gavi"] and \
            [c.plays for c in store.records("grade_cell", 1)] == [None, 0.5], "table order by primary key, NULL kept"

        code, out = cli("--config", str(ini), str(path), str(base / "a"))
        assert code == 2 and "not empty" in out, "a non-empty store is refused"
        before = tree(base / "a")
        code, out = cli("--replace", "--config", str(ini), str(path), str(base / "a"))
        assert code == 0 and tree(base / "a") == before, "--replace rebuilds the same bytes"
        code, out = cli("--config", str(ini), str(path), str(base / "b"))
        assert code == 0 and tree(base / "b") == before and len(before) > 20, "a second export is byte-identical"
        code, out = cli("--verify-only", str(path), str(base / "a"))
        assert code == 0 and out.count("EQUAL") == len(KINDS)

        edit = sqlite3.connect(path)
        edit.execute("UPDATE params SET value = value + 1e-9 WHERE run_id = 3")
        edit.execute("DELETE FROM dec_opportunity WHERE player = 'Gavi'")
        edit.commit()
        edit.close()
        code, out = cli("--verify-only", str(path), str(base / "a"))
        assert code == 1 and "params" in out and out.count("DIFFERENT") == 2 and out.count("EQUAL") == len(KINDS) - 2, out
        for name in "ab":
            os.environ["FF_ROOT"] = str(base / name / "data")
            store.clear()
    if saved is None:
        os.environ.pop("FF_ROOT", None)
    else:
        os.environ["FF_ROOT"] = saved


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        sys.exit(main(sys.argv[1:]))
