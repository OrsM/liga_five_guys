"""Run a saved query of datasette.json against data/lfg.db: the same SQL
Datasette serves.

    python tools/q.py                     # the queries, with what each answers
    python tools/q.py NAME [param=value]  # one, as a table
"""
from __future__ import annotations

import json
import sys
from contextlib import closing
from pathlib import Path
from typing import Any

from ffcore.db import META, connect, named, params, queries



def run(name: str, given: dict[str, str], path: Path | None = None, meta: Path = META
        ) -> tuple[list[str], list[tuple[Any, ...]]]:
    """A saved query's columns and rows, run as Datasette runs it."""
    cols, got = named(name, given, path, meta)
    return cols, [tuple(r) for r in got]


def _cell(v: Any) -> str:
    return "" if v is None else "%.3f" % v if isinstance(v, float) else str(v)


def main(argv: list[str]) -> int:
    if not argv:
        for name, q in queries().items():
            print("%-22s %s%s" % (name, q.get("title", ""), "".join(
                "  :" + p for p in params(q["sql"]))))
        return 0
    name, given = argv[0], dict(a.split("=", 1) for a in argv[1:])
    cols, rows = run(name, given)
    cells = [cols] + [[_cell(v) for v in r] for r in rows]
    width = [max(len(r[i]) for r in cells) for i in range(len(cols))]
    for r in cells:
        print("  ".join(c.rjust(w) for c, w in zip(r, width)))
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
    print("q.py self-test OK")


def _selftest_queries() -> None:
    """The saved queries that answer a question, against rows whose answers
    can be worked out by hand: unmatched_players, constants_vs_measured (and
    persistence), bench_points, rivals."""
    import tempfile
    from datetime import datetime, timezone

    from ffcore.clock import set_now
    from ffcore.db import record
    from ffcore.tidy import store_sql, tables_in

    def asked(name: str, given: dict[str, object] | None = None) -> list[tuple[Any, ...]]:
        return [tuple(r) for r in named(name, given or {})[1]]

    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        try:
            set_now(datetime(2026, 10, 12, tzinfo=timezone.utc))
            # unmatched_players: the dearest first, a player with no value last, the matched one absent
            record([{"player_id": k, "name": n, "app_id": app, "ff_slug": slug} for k, n, app, slug in [
                ("p1", "Ana", "1", "ana"), ("p2", "Bo", "", "bo"), ("p3", "Cy", "3", ""),
                ("p4", "Di", "", "")]], "crosswalk")
            store_sql("market", [{"observed_at": "2026-10-11T1000Z", "ff_id": k, "value": v}
                                 for k, v in (("p1", "1000000"), ("p2", "5000000"), ("p3", "9000000"))])
            assert asked("unmatched_players") == [
                ("Cy", None, 9.0, "no line-up page"), ("Bo", None, 5.0, "no app id"),
                ("Di", None, None, "neither")], asked("unmatched_players")

            # persistence and constants_vs_measured: a and b miss the same way in both jornadas
            record([{"run_id": 1, "started": "t", "as_of": "t", "trigger": "test", "commit_sha": "c"}], "runs")
            record([{"run_id": 1, "from_j": j, "jornada": j, "player": k, "predicted": 2.0,
                     "actual": a, "flagged": 0} for j in (1, 2) for k, a in (("a", 3.0), ("b", 1.0))],
                   "grade_forecast")
            record([{"run_id": 1, "ord": i, "lock": 1, "move": m, "reached": r, "kind": "get", "real": v}
                    for i, (m, r, v) in enumerate([("x", "plan", 3.0), ("y", "cleared", -1.0),
                                                   ("z", "rejected", 5.0)])], "grade_decisions")
            record([{"name": "PERSISTENT_SHARE", "value": 0.08}, {"name": "CONFIDENCE", "value": 0.7}],
                   "constants")
            assert asked("persistence") == [(0.5,)], "misses of +1 and -1 repeat: sqrt(2 / 8)"
            assert asked("constants_vs_measured") == [
                ("PERSISTENT_SHARE", 0.08, 0.5, 2, "share of a player's miss that repeats the next jornada"),
                ("CONFIDENCE", 0.7, 0.5, 2,
                 "share of moves that cleared the bar (in the plan or not) that then gained")], \
                asked("constants_vs_measured")
        finally:
            set_now(None)

    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        try:
            set_now(datetime(2026, 8, 25, tzinfo=timezone.utc))
            record([{"me": "me", "budget": 1e8}], "league_config")
            record([{"player_id": k, "name": n, "app_id": a} for k, n, a in
                    [("x", "X Man", "1"), ("y", "Y Man", "2"), ("z", "Z Man", "3")]], "crosswalk")
            store_sql("matches", [{"observed_at": "2026-08-01T0000Z", "match_id": "m", "jornada": "1",
                                   "home": "c1", "away": "c2", "kickoff": "2026-08-20T19:00:00+00:00"}])
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

            # rivals: riv bought one player and sold another this week and an old one a month ago
            record([{"run_id": 1, "started": "t", "as_of": "t", "trigger": "test", "commit_sha": "c"}], "runs")
            record([{"run_id": 1, "plan_gain": 0.0}], "sim_runs")
            record([{"run_id": 1, "manager": m, "me": me, "now": 100.0 * i, "mean": 1000.0 * i,
                     "finish": float(i), "p_above": None if me else 0.25, "cash": 5e6 * i}
                    for i, (m, me) in enumerate((("me", 1), ("riv", 0)), 1)], "sim_standings")
            store_sql("api_standings", [{"observed_at": "2026-08-24T1000Z", "user_id": u, "manager": m}
                                        for u, m in (("1", "me"), ("2", "riv"))])
            store_sql("api_teams", [{"observed_at": "2026-08-24T1000Z", "player_id": a, "manager": "riv",
                                     "player_team_id": "r" + a} for a in "56"])
            record([{"run_id": 1, "player": k, "name": k, "pos": "", "value": v}
                    for k, v in (("5", 2e6), ("6", 3e6))], "sim_facts")
            record([{"player_id": k, "app_id": k} for k in "56"], "crosswalk")
            store_sql("api_activity", [
                {"observed_at": "2026-08-24T1000Z", "activity_id": i, "kind": kind, "user_id": "2",
                 "player_id": "7", "at": at, "amount": amt, "counterparty": ""}
                for i, kind, at, amt in [("a", "buy", "2026-08-22T10:00:00+02:00", "3000000"),
                                         ("b", "sell", "2026-08-23T10:00:00+02:00", "1000000"),
                                         ("c", "buy", "2026-07-20T10:00:00+02:00", "9000000")]])
            got = {r[0]: r for r in asked("rivals")}
            riv = got["riv"]
            assert (riv[1], riv[2], riv[4], riv[5], riv[6]) == (0, 200.0, 2.0, 25.0, 10.0), riv
            assert (riv[7], riv[8], riv[9], riv[10], riv[11]) == (2, 5.0, 1, 1, -2.0), \
                "two players worth 5M; this week one buy (3M) and one sale (1M); the old buy is not counted"
            assert got["me"][5] is None, "no chance he is above himself"
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
