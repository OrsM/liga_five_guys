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
    _selftest_changes()
    _selftest_paired()
    _selftest_variant_gap()
    _selftest_phantom()
    _selftest_board_history()
    _selftest_sim_calibration()
    print("q.py self-test OK")


def _selftest_changes() -> None:
    """what_changed and cash_cliffs, against rows whose answers are worked out
    by hand."""
    import tempfile
    from datetime import datetime, timezone

    from ffcore.clock import set_now
    from ffcore.db import record
    from ffcore.tidy import store_sql, tables_in

    def asked(name: str, given: dict[str, object] | None = None) -> list[dict[str, Any]]:
        cols, got = named(name, given or {})
        return [dict(zip(cols, r)) for r in got]

    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        try:
            set_now(datetime(2026, 10, 14, 12, tzinfo=timezone.utc))
            record([{"run_id": i, "started": s, "as_of": "t", "trigger": "service", "commit_sha": "c"}
                    for i, s in ((1, "2026-10-13T08:00:00Z"), (2, "2026-10-14T08:00:00Z"),
                                 (3, "2026-10-14T11:00:00Z"))], "runs")
            record([{"player_id": k, "name": k.upper()} for k in "abcdef"], "crosswalk")

            def cell(run: int, j: int, k: str, ppm: float, picked: float, fit: float) -> dict[str, Any]:
                return {"run_id": run, "jornada": j, "player": k, "ppm": ppm, "fixture": 1.0,
                        "picked": picked, "fit": fit, "points": ppm, "plays": picked * fit,
                        "expected": ppm * picked * fit}
            record([cell(1, 9, "a", 4, .5, 1), cell(1, 9, "b", 4, .5, 1), cell(1, 9, "c", 4, 1.0, 1),
                    cell(1, 9, "e", 3, 1.0, 1), cell(1, 10, "a", 9, 1, 1),
                    cell(3, 9, "a", 5, .5, 1), cell(3, 9, "b", 4, 1.0, 1), cell(3, 9, "c", 4, 1.0, .5),
                    cell(3, 9, "d", 2, 1.0, 1), cell(3, 10, "a", 1, 1, 1),
                    cell(1, 9, "f", 4, .5, 1.0), cell(3, 9, "f", 4, 1.0, .8)], "forecast")
            # run 3 against the latest run at least 12 hours before it (run 1): the next jornada, 9
            got = {r["name"]: r for r in asked("what_changed")}
            assert set(got) == {"A", "B", "C", "D", "E", "F"}, got.keys()
            assert (got["A"]["was"], got["A"]["now"], got["A"]["change"]) == (2.0, 2.5, 0.5)
            assert (got["A"]["from_points"], got["A"]["from_selection"], got["A"]["from_fitness"]) \
                == (0.5, 0.0, 0.0), "he scores more when he plays: 5 against 4, at half a chance"
            assert (got["B"]["from_points"], got["B"]["from_selection"], got["B"]["from_fitness"]) \
                == (0.0, 2.0, 0.0), "picked half as often again: 4 points, +0.5 chance"
            assert (got["C"]["from_points"], got["C"]["from_selection"], got["C"]["from_fitness"]) \
                == (0.0, 0.0, -2.0), "half as likely to be fit: 4 points x half a chance lost"
            assert (got["F"]["from_points"], got["F"]["from_selection"], got["F"]["from_fitness"]) \
                == (0.0, 2.0, -0.8), "both move: picked more (4 x +0.5 x the old fitness 1) and less fit (4 x 1 x -0.2)"
            assert all(abs(g["change"] - (g["from_points"] + g["from_selection"] + g["from_fitness"])) < 1e-9
                       for n, g in got.items() if n in "ABCF"), "the three parts add up to the change"
            assert (got["D"]["was"], got["D"]["now"], got["D"]["scope"]) == (None, 2.0, "new in the forecast")
            assert (got["E"]["was"], got["E"]["now"], got["E"]["scope"]) == (3.0, None, "gone from the forecast")
            explicit = asked("what_changed", {"old": 1, "new": 3, "jornada": 10})
            assert [(r["name"], r["was"], r["now"]) for r in explicit] == [("A", 9.0, 1.0)], \
                "any two runs, any jornada"
        finally:
            set_now(None)

    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        try:
            set_now(datetime(2026, 10, 14, 12, tzinfo=timezone.utc))
            record([{"run_id": 1, "started": "t", "as_of": "t", "trigger": "service", "commit_sha": "c"}], "runs")
            record([{"run_id": 1, "plan_gain": 0.0}], "sim_runs")
            record([{"run_id": 1, "manager": m, "me": 0, "now": 0.0, "mean": 0.0, "cash": c}
                    for m, c in (("debtor", -5e6), ("fine", 4e6), ("nearly", -1e6))], "sim_standings")
            store_sql("api_teams", [{"observed_at": "2026-10-14T1000Z", "player_id": a, "manager": m,
                                     "player_team_id": "t" + a}
                                    for a, m in (("1", "debtor"), ("2", "debtor"), ("3", "debtor"),
                                                 ("4", "fine"), ("5", "nearly"))])
            record([{"player_id": k, "name": "P" + k, "app_id": k} for k in "12345"], "crosswalk")
            record([{"run_id": 1, "player": k, "name": "P" + k, "pos": "", "value": v}
                    for k, v in (("1", 1e6), ("2", 1e6), ("3", 6e6), ("4", 9e6), ("5", 1e6))], "sim_facts")
            got = {r["manager"]: r for r in asked("cash_cliffs")}
            assert set(got) == {"debtor", "nearly"}, "nobody in credit"
            assert (got["debtor"]["debt_m"], got["debtor"]["squad_value_m"], got["debtor"]["players"],
                    got["debtor"]["fewest_sales"]) == (5.0, 8.0, 3, 1), \
                "5M to raise from players worth 6, 1 and 1: the 6M one alone does it (the cheap ones first would need all three)"
            assert (got["nearly"]["debt_m"], got["nearly"]["fewest_sales"]) == (1.0, 1)
        finally:
            set_now(None)


def _selftest_paired() -> None:
    """paired_gain against outcomes whose differences are worked out by hand."""
    import tempfile

    from ffcore.db import record
    from ffcore.tidy import tables_in

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
                row(1, 10, "p5", 0, 9, 0), row(2, 10, "p5", 9, 9, 0)], "grade_forecast")
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


def _selftest_variant_gap() -> None:
    """variant_gap: two variants' replays of one jornada, at the last moment
    at least :hours before its lock, set against each other."""
    import tempfile

    from ffcore.db import record
    from ffcore.tidy import tables_in

    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        def row(v: str, at: str, j: int, k: str, pred: float, fl: int, h: float) -> dict[str, Any]:
            return {"variant": v, "as_of": at, "jornada": j, "player": k, "predicted": pred,
                    "actual": 3.0, "flagged": fl, "lead_h": h}
        # actual 3 throughout. Jornada 2: moments 50h, 30h, 10h out; with hours=24 the 30h one counts.
        # live errors^2 at 30h: a 4, b 1, c 0; challenger: a 1, b 4, c 0 (and different at the others)
        record([row("live", "m1", 2, "a", 1, 0, 50), row("slow", "m1", 2, "a", 3, 0, 50),
                row("live", "m2", 2, "a", 1, 0, 30), row("slow", "m2", 2, "a", 2, 0, 30),
                row("live", "m2", 2, "b", 4, 1, 30), row("slow", "m2", 2, "b", 5, 1, 30),
                row("live", "m2", 2, "c", 3, 0, 30), row("slow", "m2", 2, "c", 3, 0, 30),
                row("live", "m3", 2, "a", 1, 0, 10), row("slow", "m3", 2, "a", 3, 0, 10),
                # jornada 3: only a moment inside the window: nothing to compare
                row("live", "m4", 3, "a", 0, 0, 5), row("slow", "m4", 3, "a", 3, 0, 5),
                # jornada 4: a moment exactly :hours out counts (same error either way)
                row("live", "m5", 4, "z", 1, 0, 24), row("slow", "m5", 4, "z", 1, 0, 24)], "replay_forecast")
        got = {r[0]: r for r in named("variant_gap", {"old": "live", "new": "slow", "hours": 24})[1]}
        assert set(got) == {"all", "flagged", "not flagged"}, got
        # old - new squared error: a 4 - 1 = 3, b 1 - 4 = -3, c 0 - 0 = 0
        assert got["all"][1] == 4 and abs(got["all"][2]) < 1e-9, got["all"]
        assert got["not flagged"][1] == 3 and got["not flagged"][2] == 1.0, got["not flagged"]
        assert got["flagged"][1] == 1 and got["flagged"][2] == -3.0 and got["flagged"][6] == "too few"
        assert got["all"][6] == "not established"
        far = {r[0]: r for r in named("variant_gap", {"old": "live", "new": "slow", "hours": 0})[1]}
        assert far["all"][1] == 3 and abs(far["all"][2] - 13 / 3) < 1e-3, "the last moment of each jornada: 4, 9 and 0"
        assert named("variant_gap", {"old": "live", "new": "slow", "hours": 60})[1] == [], \
            "no moment that early: nothing to compare"


def _selftest_phantom() -> None:
    """phantom_coach: the phantom's eleven each jornada beside the same squad
    never moved, with a running total."""
    import tempfile

    from ffcore.db import record
    from ffcore.tidy import tables_in

    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        record([{"run_id": i, "started": "t", "as_of": "t", "trigger": "test", "commit_sha": "c"} for i in (1, 2)], "runs")
        record([{"run_id": r, "jornada": j, "phantom": ph, "standpat": sp, "moves": m, "cash": 0.0}
                for r, j, ph, sp, m in ((1, 3, 50.0, 40.0, "buy A"), (1, 2, 30.0, 35.0, ""),
                                        (2, 2, 99.0, 0.0, "later run"))], "phantom_jornadas")
        cols, got = named("phantom_coach", {"run": 1})
        rows_ = [dict(zip(cols, r)) for r in got]
        assert [(r["jornada"], r["phantom"], r["standpat"], r["gain"], r["total_gain"]) for r in rows_] == [
            (2, 30.0, 35.0, -5.0, -5.0), (3, 50.0, 40.0, 10.0, 5.0), ("total", 80.0, 75.0, 5.0, 5.0)], \
            "jornada order, a running total, the named run's rows only"
        assert rows_[1]["moves"] == "buy A"
        assert [r[0] for r in named("phantom_coach", {})[1]] == [2, "total"], "no run named: the latest that has rows"


def _selftest_board_history() -> None:
    """board_history: how long a move stayed on the published board."""
    import tempfile

    from ffcore.db import record
    from ffcore.tidy import tables_in

    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        record([{"generated_at": at, "commit_sha": "c"} for at in ("a1", "a2", "a3")], "board_history")
        record([{"generated_at": at, "ord": i, "what": w, "name": n, "gain": g}
                for at, i, w, n, g in (("a1", 0, "buy", "X", 10.0), ("a2", 0, "buy", "X", 20.0),
                                       ("a3", 0, "buy", "X", 30.0), ("a1", 1, "sell", "Y", -4.0),
                                       ("a2", 1, "sell", "Y", -6.0), ("a3", 1, "field", None, 1.0),
                                       ("a1", 2, "buy", "Z", 5.0), ("a3", 2, "buy", "W", 1.0))], "board_history_moves")
        cols, got = named("board_history", {})
        rows_ = [dict(zip(cols, r)) for r in got]
        assert [(r["what"], r["name"], r["boards"], r["first_seen"], r["last_seen"], r["avg_gain"], r["on_latest"])
                for r in rows_] == [("buy", "X", 3, "a1", "a3", 20.0, 1), ("sell", "Y", 2, "a1", "a2", -5.0, 0),
                                    ("buy", "W", 1, "a3", "a3", 1.0, 1), ("buy", "Z", 1, "a1", "a1", 5.0, 0)], rows_


def _selftest_sim_calibration() -> None:
    """sim_calibration: coverage and z of the simulation's draws."""
    import tempfile

    from ffcore.db import record
    from ffcore.tidy import tables_in

    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        record([{"run_id": i, "started": "t", "as_of": "t", "trigger": "test", "commit_sha": "c"} for i in (1, 2)], "runs")

        def row(run: int, j: int, m: str, actual: float, mean: float, sd: float, pit: float,
                d: tuple[float, float, float, float] | None = None) -> dict[str, Any]:
            r = {"run_id": run, "jornada": j, "manager": m, "actual": actual, "mean": mean, "sd": sd, "pit": pit}
            if d:
                r.update(diff_actual=d[0], diff_mean=d[1], diff_sd=d[2], diff_pit=d[3])
            return r
        # totals: z = +1, -2, 0.5, and one with no spread (dropped); pit in the 10-90 band for 2 of 3
        record([row(1, 2, "a", 60, 50, 10, 0.84, (20, 10, 10, 0.84)), row(1, 2, "b", 30, 50, 10, 0.02, (-10, 10, 5, 0.02)),
                row(1, 3, "a", 55, 50, 10, 0.72), row(1, 3, "c", 9, 9, 0, 0.5),
                row(2, 2, "z", 1, 1, 1, 0.5)], "sim_calibration")
        cols, got = named("sim_calibration", {"run": 1})
        rows_ = {r[0]: dict(zip(cols, r)) for r in got}
        t = rows_["totals"]
        assert (t["n"], t["jornadas"], t["in_10_90"], t["in_25_75"]) == (3, 2, 0.67, 0.33), t
        assert t["z_mean"] == -0.17 and t["z_sd"] == 1.61, t      # z = 1, -2, .5: mean -.1667, sd sqrt(5.1667 / 2) = 1.607
        dd = rows_["you minus him"]
        assert (dd["n"], dd["in_10_90"], dd["z_mean"], dd["z_sd"]) == (2, 0.5, -1.5, 3.54), dd   # diff z = 1, -4: mean -1.5, sd 3.54
        assert [r[0] for r in named("sim_calibration", {})[1]] == ["totals"], "no run named: the latest, run 2"


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
                   "model_constants")
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
