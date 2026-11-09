"""Run a saved query of datasette.json against data/lfg.db: the same SQL
Datasette serves.

    python tools/q.py                     # the queries, with what each answers
    python tools/q.py NAME [param=value]  # one, as a table
    python tools/q.py --time              # every query timed on the real database; fails past Datasette's limit
"""
from __future__ import annotations

import json
import sys
from contextlib import closing
from pathlib import Path
from collections.abc import Sequence
from typing import Any

from ffcore.db import META, SLOW_S, connect, named, params, queries, timed



def run(name: str, given: dict[str, str], path: Path | None = None, meta: Path = META
        ) -> tuple[list[str], list[tuple[Any, ...]]]:
    """A saved query's columns and rows, run as Datasette runs it."""
    cols, got = named(name, given, path, meta)
    return cols, [tuple(r) for r in got]


def _cell(v: Any) -> str:
    return "" if v is None else "%.3f" % v if isinstance(v, float) else str(v)


def table(cols: list[str], rows: Sequence[Sequence[Any]]) -> str:
    """Columns and rows as aligned text."""
    cells = [cols] + [[_cell(v) for v in r] for r in rows]
    width = [max(len(r[i]) for r in cells) for i in range(len(cols))]
    return "\n".join("  ".join(c.rjust(w) for c, w in zip(r, width)) for r in cells)


def main(argv: list[str]) -> int:
    if argv == ["--time"]:
        over = 0
        for name, secs, ok in sorted(timed(), key=lambda t: -t[1]):
            print("%-24s %6.2fs %s" % (name, secs, "" if ok else "  OVER %.0fs: close to Datasette's limit" % SLOW_S))
            over += not ok
        return 1 if over else 0
    if not argv:
        for name, q in queries().items():
            print("%-22s %s%s" % (name, q.get("title", ""), "".join(
                "  :" + p for p in params(q["sql"]))))
        return 0
    name, given = argv[0], dict(a.split("=", 1) for a in argv[1:])
    cols, rows = run(name, given)
    print(table(cols, rows))
    return 0


def _selftest() -> None:
    import tempfile
    from ffcore.tidy import store_sql, tables_in

    with tempfile.TemporaryDirectory() as tmp:
        meta = Path(tmp) / "datasette.json"
        meta.write_text(json.dumps({"databases": {"lfg": {"queries": {
            "big": {"title": "runs over a size",
                    "sql": "SELECT run_id FROM runs WHERE peak_mb > coalesce(nullif(:mb, ''), 0)"
                           " ORDER BY run_id"}}}}}))
        db = Path(tmp) / "lfg.db"
        with closing(connect(db)) as c, c:
            c.executemany("INSERT INTO runs (started, as_of, trigger, commit_sha, peak_mb)"
                          " VALUES ('t', 't', 'service', 'a', ?)", [(100.0,), (300.0,)])
        assert queries(meta) == {"big": queries(meta)["big"]} and "sql" in queries(meta)["big"]
        assert run("big", {"mb": "200"}, db, meta) == (["run_id"], [(2,)])
        assert run("big", {}, db, meta) == (["run_id"], [(1,), (2,)])
        assert run("big", {"mb": ""}, db, meta) == (["run_id"], [(1,), (2,)]), \
            "a param left empty is '', as Datasette sends it"
    with tempfile.TemporaryDirectory() as tmp2, tables_in(Path(tmp2)):
        row = {"observed_at": "2026-01-01T0000Z"}
        store_sql("api_activity", [dict(row, activity_id="1", at="", kind="", user_id="",
                                        counterparty="", player_id="")])
        store_sql("api_standings", [dict(row, user_id="", manager="")])
        store_sql("api_teams", [dict(row, manager="", player_id="", player_team_id="")])
        store_sql("market", [dict(row, ff_id="1", value="1")])
        store_sql("api_market", [dict(row, seller="", player_id="", expires_at="")])
        store_sql("api_offers", [dict(row, player_team_id="", offer_id="", created_at="",
                                      from_market="")])
        _compiles(Path(tmp2) / "lfg.db")
    _selftest_queries()
    _selftest_paired()
    print("q.py self-test OK")


def _selftest_paired() -> None:
    """paired_gain against outcomes whose differences are worked out by hand."""
    import tempfile

    from ffcore.db import record
    from ffcore.tidy import tables_in
    from grading import view_table

    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        record([{"run_id": i, "started": "t", "as_of": "t", "trigger": "service", "commit_sha": "c"}
                for i in (1, 2)], "runs")

        def row(run: int, j: int, k: str, pred: float, actual: float, flagged: int) -> dict[str, Any]:
            return {"run_id": run, "from_j": 8, "jornada": j, "player": k, "predicted": pred,
                    "actual": actual, "flagged": flagged}
        # squared error old -> new: p1 4 -> 1, p2 4 -> 1, p3 (flagged) 0 -> 4
        record([row(1, 9, "p1", 1, 3, 0), row(2, 9, "p1", 2, 3, 0),
                row(1, 9, "p2", 2, 0, 0), row(2, 9, "p2", 1, 0, 0),
                row(1, 9, "p3", 5, 5, 1), row(2, 9, "p3", 3, 5, 1),
                row(1, 9, "p4", 1, 1, 0),                       # only in the old run: not paired
                row(1, 10, "p5", 0, 9, 0), row(2, 10, "p5", 9, 9, 0)], view_table("grade_forecast"))
        got = {r[0]: dict(zip(("scope", "n", "gain", "se", "lo", "hi", "verdict"), r))
               for r in named("paired_gain", {"old": 1, "new": 2, "ahead": 1})[1]}
        assert set(got) == {"all", "flagged", "not flagged"}, got
        a = got["all"]
        se = (((3 - 2 / 3) ** 2 * 2 + (-4 - 2 / 3) ** 2) / 2 / 3) ** 0.5
        assert a["n"] == 3, "the player missing from one run, and the other horizon, are not paired"
        assert abs(a["gain"] - 2 / 3) < 1e-4 and abs(a["se"] - se) < 1e-4, a
        assert abs(a["lo"] - (2 / 3 - 1.96 * se)) < 1e-4 and abs(a["hi"] - (2 / 3 + 1.96 * se)) < 1e-4
        assert a["verdict"] == "not established", "the interval straddles zero"
        assert (got["not flagged"]["n"], got["not flagged"]["gain"], got["not flagged"]["verdict"]) \
            == (2, 3.0, "new better")
        assert (got["flagged"]["n"], got["flagged"]["gain"], got["flagged"]["se"], got["flagged"]["verdict"]) \
            == (1, -4.0, None, "too few"), "one outcome has no spread"
        far = {r[0]: r for r in named("paired_gain", {"old": 1, "new": 2, "ahead": 2})[1]}
        assert far["all"][1] == 1 and far["all"][2] == 81.0, "ahead picks the horizon: 81 -> 0 is a gain of +81"


def _selftest_queries() -> None:
    """The saved queries that answer a question, against rows whose answers
    can be worked out by hand: bench_points."""
    import tempfile
    from datetime import datetime, timezone

    from ffcore.db import set_now
    from ffcore.db import record
    from ffcore.tidy import store_sql, tables_in

    def asked(name: str, given: dict[str, object] | None = None) -> list[tuple[Any, ...]]:
        return [tuple(r) for r in named(name, given or {})[1]]

    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        try:
            set_now(datetime(2026, 8, 25, tzinfo=timezone.utc))
            record([{"me": "me", "budget": 1e8}], "league_config")
            record([{"player_id": k, "name": n, "app_id": a} for k, n, a in
                    [("x", "X Man", "1"), ("y", "Y Man", "2"), ("z", "Z Man", "3")]], "crosswalk")
            store_sql("matches", [{"observed_at": "2026-08-01T0000Z", "match_id": "m", "jornada": "1",
                                   "home": "c1", "away": "c2", "kickoff": "2026-08-20T19:00:00+00:00",
                                   "score": "1-0"}])
            store_sql("market", [{"observed_at": "2026-08-24T1000Z", "ff_id": k, "club": "c1"} for k in "xyz"])
            store_sql("points", [{"observed_at": "2026-08-10T0000Z", "season": "2026-27", "ff_id": k,
                                  "points": "0", "games": "0"} for k in "xyz"]
                      + [{"observed_at": "2026-08-21T0000Z", "season": "2026-27", "ff_id": k,
                          "points": p, "games": "1"} for k, p in (("x", "5"), ("y", "3"), ("z", "2"))])
            # the app line-up before the lock is x and z; the squad is x, y, z; later ones do not count
            store_sql("api_teams", [{"observed_at": "2026-08-20T1800Z", "player_id": a, "manager": "me",
                                     "player_team_id": "t" + a} for a in "123"])
            store_sql("api_lineup", [{"observed_at": at, "player_id": a, "player_name": a}
                                     for at, ids in (("2026-08-19T1000Z", "12"), ("2026-08-20T1800Z", "13"),
                                                     ("2026-08-21T1000Z", "23")) for a in ids])
            assert asked("bench_points") == [(1, 7.0, 3.0, 2, "Y Man", 3.0)], asked("bench_points")
        finally:
            set_now(None)


def _compiles(path: Path) -> None:
    """Every saved query compiles against path (EXPLAIN), as Datasette runs it."""
    with closing(connect(path)) as c:
        for name, q in queries().items():
            try:
                c.execute("EXPLAIN " + q["sql"], {p: "" for p in params(q["sql"])})
            except Exception as e:
                raise AssertionError("datasette.json query %s: %s" % (name, e)) from e


if __name__ == "__main__":
    if sys.argv[1:] == ["--selftest"]:
        _selftest()
        raise SystemExit(0)
    raise SystemExit(main(sys.argv[1:]))
