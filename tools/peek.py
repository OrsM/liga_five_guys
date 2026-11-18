"""Look at the record store, read-only.

    peek.py KIND [FIELD=VALUE ...] [--run N] [--limit n]    the rows of KIND of the latest run (run N), as a table
    peek.py runs [FIELD=VALUE ...] [--limit n]              the runs, the newest n
    peek.py -i                                              a REPL with store, records and parsed bound

KIND is one of the record kinds (ffcore.records.KINDS): runs, run_measures, measure_log, params, auction_calibration,
auction_brier, grade_cell, dec_opportunity, dec_offer, sale_score. A FIELD=VALUE keeps the rows whose field, written as text, is VALUE
(an unknown field is an error). At most n rows (default 20) are printed, then how many matched. The store is the one
beside FF_ROOT (default ./data), opened only to read.

-i binds: store (ffcore.store: runs(), latest_run(), records(kind, run_id), grade_cells(run_id)), records
(ffcore.records: the row types, KINDS, latest()) and parsed (ffcore.parsed: paths_in(), read(paths) the observations
of the parse cache)."""
from __future__ import annotations

import argparse
import code
import itertools
import sys
from collections.abc import Iterable, Sequence
from typing import Any

from ffcore import parsed, records, store

LIMIT = 20


def render(fields: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    """The rows as a table under a header of fields, each column as wide as its widest cell."""
    cells = [list(fields)] + [["" if v is None else str(v) for v in r] for r in rows]
    widths = [max(len(r[i]) for r in cells) for i in range(len(fields))]
    return "\n".join("  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip() for r in cells)


def peek(kind: str, where: Iterable[str], run: int | None, limit: int) -> str:
    """The table of the first limit rows of kind in the run (the latest when None; kind runs: the newest limit runs)
    matching every FIELD=VALUE of where, and the count that matched; an unknown kind, field or run is a ValueError."""
    if kind not in records.KINDS:
        raise ValueError("no record kind %r (kinds: %s)" % (kind, ", ".join(records.KINDS)))
    fields = records.KINDS[kind].row._fields
    keep = {}
    for w in where:
        name, sep, value = w.partition("=")
        if not sep or name not in fields:
            raise ValueError("%r is not FIELD=VALUE with a field of %s: %s" % (w, kind, ", ".join(fields)))
        keep[name] = value
    if kind != "runs" and run is None:
        latest = store.latest_run()
        run = latest.run_id if latest else None
    rows = (r for r in store.records(kind, run) if all(str(getattr(r, n)) == v for n, v in keep.items()))
    if kind == "runs":
        shown = list(rows)
        return render(fields, shown[-limit:]) + "\n%d of %d runs" % (min(limit, len(shown)), len(shown))
    first = list(itertools.islice(rows, limit))
    more = sum(1 for _ in rows)
    return render(fields, first) + "\n%d of %d rows" % (len(first), len(first) + more)


def main(argv: Sequence[str]) -> int:
    ap = argparse.ArgumentParser(description="Look at the record store, read-only.")
    ap.add_argument("kind", nargs="?")
    ap.add_argument("where", nargs="*", metavar="FIELD=VALUE")
    ap.add_argument("--run", type=int)
    ap.add_argument("--limit", type=int, default=LIMIT)
    ap.add_argument("-i", dest="interactive", action="store_true")
    args = ap.parse_args(argv)
    if args.interactive:
        code.interact(banner="store, records, parsed are bound", local={"store": store, "records": records, "parsed": parsed})
        return 0
    if args.kind is None:
        ap.error("a kind (%s) or -i" % ", ".join(records.KINDS))
    try:
        print(peek(args.kind, args.where, args.run, args.limit))
    except ValueError as e:
        print("peek: %s" % e, file=sys.stderr)
        return 2
    return 0


def _selftest() -> None:
    import contextlib
    import io
    import os
    import tempfile
    from pathlib import Path

    kept = os.environ.get("FF_ROOT")
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["FF_ROOT"] = str(Path(tmp) / "data")
        try:
            for i in (1, 2):
                got = store.RunRecords(i, "abc%d" % i)
                got.add("params", [{"component": "c%d" % k, "term": "t", "value": 0.5 * k, "kind": "fitted", "n": None} for k in range(30)])
                got.add("run_measures", [{"stage": "board", "measure": "secs", "value": 1.5 * i}])
                got.publish(records.Run(i, "2026-11-0%dT10:00:00Z" % i, "", "service", "abc%d" % i, None, 1, None))
            table = peek("params", [], None, 5).split("\n")
            assert table[0].split() == list(records.Param._fields) and table[-1] == "5 of 30 rows" and len(table) == 7, \
                "a header, the rows up to the limit, the count that matched"
            assert all(line.split()[0] == "2" for line in table[1:-1]), "the latest run unless --run says another"
            assert peek("params", [], 1, 100).split("\n")[1].split()[0] == "1"
            assert peek("params", ["component=c7"], None, 20).endswith("1 of 1 rows") and "c7" in peek("params", ["component=c7"], None, 20)
            assert peek("params", ["n=None"], None, 1).endswith("1 of 30 rows"), "a NULL is the text None"
            assert peek("run_measures", [], None, 20).endswith("1 of 1 rows")
            assert peek("runs", [], None, 1).endswith("1 of 2 runs") and "abc2" in peek("runs", [], None, 1), "the newest runs"
            assert peek("measure_log", [], None, 20).endswith("0 of 0 rows"), "a kind with no rows prints its header and a zero"
            for bad in (("nonsense", []), ("params", ["nofield=1"]), ("params", ["component"])):
                try:
                    peek(bad[0], bad[1], None, 5)
                except ValueError:
                    pass
                else:
                    raise AssertionError("accepted %r" % (bad,))
            before = sorted(p.name for p in (Path(tmp) / "store").rglob("*"))
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                assert main(["runs", "--limit", "5"]) == 0 and main(["params"]) == 0 and main(["bogus"]) == 2
            assert sorted(p.name for p in (Path(tmp) / "store").rglob("*")) == before, "reading leaves the store as it was"
        finally:
            if kept is None:
                os.environ.pop("FF_ROOT")
            else:
                os.environ["FF_ROOT"] = kept
    print("peek self-test OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        sys.exit(main(sys.argv[1:]))
