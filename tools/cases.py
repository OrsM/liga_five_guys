from __future__ import annotations

import json
import lzma
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

FIXTURE = Path(__file__).with_name("cases") / "league.json.xz"

AS_OF = "2026-11-08T1157Z"
WHOLE = ("starters", "points", "matches", "results_history")
RECENT = ("lineups", "api_teams", "api_market", "api_offers", "api_lineup", "api_standings", "api_leagues")
RECENT_COUNT = 6
MANAGERS = ("api_teams", "api_standings")
CASES = {"raphinha": "4288", "garcia": "7279", "diarra": "10832", "rodri": "2733", "david": "9573", "terrats": "8944",
         "correia": "8091"}
DEGRADED_CLUB = "getafe"
CUT_AFTER_MINUTES = 10
AHEAD = 4

Seen = dict[str, Any]
Check = tuple[str, str, str, Callable[[Seen], bool], Callable[[Seen], Any]]


def extract(me: str, as_of: str = AS_OF) -> dict[str, Any]:
    from ffcore.db import rows

    def table(name: str, where: str = "", args: tuple[object, ...] = ()) -> dict[str, Any]:
        cols = [r[1] for r in rows("SELECT * FROM pragma_table_info('%s')" % name)]
        got = rows("SELECT %s FROM %s %s ORDER BY rowid" % (", ".join(cols), name, where), args)
        return {"columns": cols, "rows": [list(r) for r in got]}

    def at(name: str, stamps: list[str]) -> dict[str, Any]:
        return table(name, "WHERE observed_at IN (%s)" % ",".join("?" * len(stamps)), tuple(stamps))

    recent = [r[0] for r in rows("SELECT DISTINCT observed_at FROM api_teams WHERE observed_at <= ? ORDER BY 1 DESC LIMIT ?",
                                 (as_of, RECENT_COUNT))]
    daily = [r[0] for r in rows("SELECT max(observed_at) FROM market WHERE observed_at <= ? GROUP BY substr(observed_at, 1, 10)",
                                (as_of,))]
    out = {t: table(t, "WHERE observed_at <= ?", (as_of,)) for t in WHOLE}
    out.update({t: at(t, recent) for t in RECENT})
    marks = ",".join("?" * len(recent))
    app = {r[0] for r in rows("SELECT player_id FROM api_teams WHERE observed_at IN (%s)" % marks, tuple(recent))}
    app |= {r[0] for r in rows("SELECT player_id FROM api_market WHERE observed_at IN (%s)" % marks, tuple(recent))}
    link = {k: a for k, a in rows("SELECT player_id, app_id FROM crosswalk")}
    app |= {link[k] for k in CASES.values() if link.get(k)}
    ff = {k for k, a in link.items() if a in app} | set(CASES.values())
    stamps = sorted(set(daily) | set(recent))
    out["market"] = table("market", "WHERE (observed_at IN (%s) AND ff_id IN (%s)) OR observed_at IN (%s)"
                          % (",".join("?" * len(stamps)), ",".join("?" * len(ff)), marks), (*stamps, *sorted(ff), *recent))
    out["api_players_all"] = table("api_players_all", "WHERE observed_at <= ? AND player_id IN (%s)" % ",".join("?" * len(app)),
                                   (as_of, *sorted(app)))
    out["season_points"] = table("season_points")
    for r in out["api_leagues"]["rows"]:
        r[out["api_leagues"]["columns"].index("league_name")] = "league"
    names: dict[str, str] = {}
    for t in MANAGERS:
        i = out[t]["columns"].index("manager")
        for r in out[t]["rows"]:
            if r[i] and r[i] != me:
                r[i] = names.setdefault(r[i], "rival%d" % (len(names) + 1))
    return {"as_of": max(recent), "tables": out}


def save(doc: dict[str, Any]) -> None:
    FIXTURE.parent.mkdir(exist_ok=True)
    with lzma.open(FIXTURE, "wt", encoding="utf-8", preset=9) as f:
        json.dump(doc, f, separators=(",", ":"), ensure_ascii=False)


def load() -> dict[str, Any]:
    with lzma.open(FIXTURE, "rt", encoding="utf-8") as f:
        doc: dict[str, Any] = json.load(f)
    return doc


def observe(doc: dict[str, Any], degraded: bool = False) -> Seen:
    import contextlib
    import tempfile
    from contextlib import closing
    from datetime import timedelta

    import crosswalk
    import decision
    import eleven
    from assemble import built
    from board import board
    from ffcore.db import connect, record, rows, set_now
    from ffcore.league import write_config
    from ffcore.parse import snapshot_stamp
    from ffcore.tidy import tables_in

    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)), contextlib.chdir(tmp):
        with closing(connect()) as db, db:
            for name, t in doc["tables"].items():
                cols = t["columns"]
                db.executemany("INSERT INTO %s (%s) VALUES (%s)" % (name, ", ".join(cols), ", ".join("?" * len(cols))), t["rows"])
            if degraded:
                db.execute("DELETE FROM lineups WHERE team_slug = ? AND observed_at = ?", (DEGRADED_CLUB, doc["as_of"]))
        write_config()
        stamp = snapshot_stamp(doc["as_of"])
        assert stamp is not None
        set_now(stamp + timedelta(minutes=CUT_AFTER_MINUTES))
        with closing(connect()) as db, db:
            crosswalk.build(db)
        record([{"run_id": 1, "started": "t", "as_of": "t", "trigger": "test", "commit_sha": "c"}], "runs")
        built()
        inp = decision.read_inputs()
        pos, cells = decision.cells_of(inp)
        mine = frozenset(k for k, _, m, *_ in inp["dec_player"] if m)
        first = rows("SELECT min(jornada) FROM jornada_calendar WHERE state = 'ahead'")[0][0]
        next_four = frozenset(range(first, first + AHEAD))
        windows = {"next4": next_four, "season": frozenset(cells)}
        values = {w: decision.window_value(pos, cells, js) for w, js in windows.items()}
        settled = {w: eleven.best_eleven(mine, pos, v) for w, v in values.items()}
        opportunities = set() if degraded else {k for k, *_ in decision.opportunities(inp)}
        doc_board: dict[str, Any] = {"buys": [], "can_bid": 0.0} if degraded else board(inp)
        seen: Seen = {"first": first, "opportunities": opportunities, "board": doc_board, "money": inp["dec_money"][0],
                      "squad": len(mine), "offers": rows("SELECT count(*) FROM offers WHERE status = 'pending'")[0][0],
                      "cases": {}}
        ids = tuple(CASES.values())
        marks = ",".join("?" * len(ids))

        def each(sql: str) -> dict[str, list[tuple[Any, ...]]]:
            out: dict[str, list[tuple[Any, ...]]] = {k: [] for k in ids}
            for r in rows(sql % marks, ids):
                out[r[0]].append(tuple(r[1:]))
            return out

        market = each("SELECT ff_id, value FROM market WHERE ff_id IN (%s) ORDER BY observed_at")
        points = each("SELECT ff_id, games, points FROM points WHERE ff_id IN (%s) AND season = (SELECT max(season) FROM points)"
                      " ORDER BY observed_at")
        known = each("SELECT player_id, name, club_id, ff_slug, app_id FROM crosswalk WHERE player_id IN (%s)")
        status = each("SELECT player, listed, pct, status, note FROM player_status WHERE player IN (%s)")
        rate = each("SELECT player, ppm, picked, record, listed_pct FROM forecast_rate WHERE player IN (%s)")
        cell = each("SELECT player, jornada, p, e FROM cell WHERE player IN (%s)")
        total = each("SELECT player, e, v, jornadas FROM cell_total WHERE player IN (%s)")
        late = each("SELECT player, jornada FROM late_arrivals WHERE player IN (%s)")
        for name, k in CASES.items():
            swaps = {w: eleven.swap(k, s.eleven, pos, values[w]) if s else None for w, s in settled.items()}
            seen["cases"][name] = {
                "id": k,
                "value": market[k][-1][0] if market[k] else None,
                "points": points[k][-1] if points[k] else None,
                "crosswalk": known[k][0] if known[k] else None,
                "status": status[k][0] if status[k] else None,
                "rate": rate[k][0] if rate[k] else None,
                "cells": {j: (p, e) for j, p, e in cell[k]},
                "total": total[k][0] if total[k] else None,
                "late": [j for (j,) in late[k]],
                "swaps": swaps,
                "buy": next((b for b in doc_board["buys"] if b["key"] == k), None)}
    return seen


def mean_e(c: Seen, first: int, count: int = AHEAD) -> float:
    return sum(c["cells"].get(j, (0.0, 0.0))[1] for j in range(first, first + count)) / count


def mean_p(c: Seen, first: int, count: int = AHEAD) -> float:
    return sum(c["cells"].get(j, (0.0, 0.0))[0] for j in range(first, first + count)) / count


def checks() -> list[Check]:
    def c(s: Seen, name: str) -> Seen:
        got: Seen = s["cases"][name]
        return got

    def funding(s: Seen) -> dict[str, Any]:
        got: dict[str, Any] = c(s, "raphinha")["buy"]["funding"]
        return got

    return [
        ("context", "tables", "cash is the pre-sale debt (about -49.5M)", lambda s: -50e6 < s["money"][0] < -49e6, lambda s: s["money"][0]),
        ("context", "tables", "team value about 464M", lambda s: 460e6 < s["money"][1] < 470e6, lambda s: s["money"][1]),
        ("context", "tables", "22 players and 17 standing offers", lambda s: (s["squad"], s["offers"]) == (22, 17), lambda s: (s["squad"], s["offers"])),
        ("context", "board", "bid limit about 43.4M", lambda s: 43e6 < s["board"]["can_bid"] < 44e6, lambda s: s["board"]["can_bid"]),
        ("raphinha", "tables", "price 150-200M, 8+ games at 12+ a game",
         lambda s: 150e6 < c(s, "raphinha")["value"] < 200e6 and c(s, "raphinha")["points"][0] >= 8
         and c(s, "raphinha")["points"][1] / c(s, "raphinha")["points"][0] >= 12, lambda s: (c(s, "raphinha")["value"], c(s, "raphinha")["points"])),
        ("raphinha", "crosswalk", "has his slug and his app id",
         lambda s: bool(c(s, "raphinha")["crosswalk"][2]) and bool(c(s, "raphinha")["crosswalk"][3]), lambda s: c(s, "raphinha")["crosswalk"]),
        ("raphinha", "status", "listed, starting 85%+, no flag",
         lambda s: c(s, "raphinha")["status"][1] >= 85 and c(s, "raphinha")["status"][2] is None, lambda s: c(s, "raphinha")["status"]),
        ("raphinha", "forecast", "plays (p 0.9+) and e 9-14 a jornada over the next 4",
         lambda s: mean_p(c(s, "raphinha"), s["first"]) >= 0.9 and 9 <= mean_e(c(s, "raphinha"), s["first"]) <= 14,
         lambda s: (round(mean_p(c(s, "raphinha"), s["first"]), 2), round(mean_e(c(s, "raphinha"), s["first"]), 1))),
        ("raphinha", "forecast", "jornada 12 is zero: his auction closes after Barcelona's kickoff (late arrival)",
         lambda s: c(s, "raphinha")["cells"][s["first"] - 1][1] == 0.0 and s["first"] - 1 in c(s, "raphinha")["late"],
         lambda s: (c(s, "raphinha")["cells"][s["first"] - 1], c(s, "raphinha")["late"])),
        ("raphinha", "eleven", "he improves my settled eleven, over the next 4 and the season",
         lambda s: all(c(s, "raphinha")["swaps"][w] and c(s, "raphinha")["swaps"][w].improves for w in ("next4", "season")),
         lambda s: {w: (round(v.gain, 1), v.replaces) for w, v in c(s, "raphinha")["swaps"].items() if v}),
        ("raphinha", "opportunities", "he is the opportunity", lambda s: c(s, "raphinha")["id"] in s["opportunities"], lambda s: sorted(s["opportunities"])),
        ("raphinha", "board", "a buy line with a chance to win",
         lambda s: c(s, "raphinha")["buy"] is not None and c(s, "raphinha")["buy"]["win"] > 0, lambda s: c(s, "raphinha")["buy"] and (c(s, "raphinha")["buy"]["amount"], c(s, "raphinha")["buy"]["win"])),
        ("raphinha", "board", "funding exists tonight: sales raise cash, the bid is allowed (margin >= 0)",
         lambda s: funding(s)["feasible"] and len(funding(s)["tonight"]) > 0 and funding(s)["margin"] >= 0,
         lambda s: {k: funding(s).get(k) for k in ("feasible", "raises", "margin", "lock_margin", "lock_margin_low")}),
        ("raphinha", "board", "if the bid is lost cash is left to reuse and no starter is stranded",
         lambda s: funding(s)["cash_if_lost"] > 0 and funding(s)["net"] > 0, lambda s: (funding(s)["cash_if_lost"], funding(s)["net"])),
        ("garcia", "tables", "price 40-70M, 8+ games", lambda s: 40e6 < c(s, "garcia")["value"] < 70e6 and c(s, "garcia")["points"][0] >= 8,
         lambda s: (c(s, "garcia")["value"], c(s, "garcia")["points"])),
        ("garcia", "crosswalk", "has his slug", lambda s: bool(c(s, "garcia")["crosswalk"][2]), lambda s: c(s, "garcia")["crosswalk"]),
        ("garcia", "forecast", "e 6-8 a jornada over the next 4", lambda s: 6 <= mean_e(c(s, "garcia"), s["first"]) <= 8,
         lambda s: round(mean_e(c(s, "garcia"), s["first"]), 1)),
        ("garcia", "opportunities", "not for sale, so not an opportunity", lambda s: c(s, "garcia")["id"] not in s["opportunities"], lambda s: sorted(s["opportunities"])),
        ("diarra", "tables", "under 1M, never scored", lambda s: c(s, "diarra")["value"] < 1e6 and c(s, "diarra")["points"] is None,
         lambda s: (c(s, "diarra")["value"], c(s, "diarra")["points"])),
        ("diarra", "crosswalk", "has his app id, so the app can say he left", lambda s: bool(c(s, "diarra")["crosswalk"][3]), lambda s: c(s, "diarra")["crosswalk"]),
        ("diarra", "status", "gone", lambda s: c(s, "diarra")["status"] is not None and c(s, "diarra")["status"][2] == "gone", lambda s: c(s, "diarra")["status"]),
        ("diarra", "forecast", "p 0 and e 0 all season", lambda s: all(v == (0.0, 0.0) for v in c(s, "diarra")["cells"].values()) and c(s, "diarra")["total"][0] == 0.0,
         lambda s: (c(s, "diarra")["total"], len(c(s, "diarra")["cells"]))),
        ("diarra", "opportunities", "never an opportunity", lambda s: c(s, "diarra")["id"] not in s["opportunities"], lambda s: sorted(s["opportunities"])),
        ("rodri", "status", "injured, with the return note", lambda s: c(s, "rodri")["status"][2] == "injured" and "diciembre" in c(s, "rodri")["status"][3],
         lambda s: c(s, "rodri")["status"]),
        ("rodri", "forecast", "out (p 0) for the 3 jornadas to come, then back", lambda s: all(c(s, "rodri")["cells"][s["first"] - 1 + i][0] == 0.0 for i in range(3))
         and c(s, "rodri")["cells"][s["first"] + 2][0] > 0, lambda s: [(j, round(p, 2)) for j, (p, _) in sorted(c(s, "rodri")["cells"].items())[:6]]),
        ("rodri", "forecast", "after the return e is near his 6 a game (4.5-7 over the 3 jornadas after it)",
         lambda s: 4.5 <= mean_e(c(s, "rodri"), s["first"] + 2, 3) <= 7, lambda s: round(mean_e(c(s, "rodri"), s["first"] + 2, 3), 1)),
        ("david", "status", "listed at a low start % (40 or less)", lambda s: c(s, "david")["status"][1] <= 40, lambda s: c(s, "david")["status"]),
        ("david", "forecast", "low but not zero over the next 4 (above 1, under his 8.8 a game)",
         lambda s: 1 < mean_e(c(s, "david"), s["first"]) < 8.8, lambda s: round(mean_e(c(s, "david"), s["first"]), 1)),
        ("david", "forecast", "season total clearly above 0 (over 30)", lambda s: c(s, "david")["total"][0] > 30, lambda s: c(s, "david")["total"]),
        ("correia", "tables", "6-12M, 7+ games at 6+ a game", lambda s: 6e6 < c(s, "correia")["value"] < 12e6 and c(s, "correia")["points"][0] >= 7
         and c(s, "correia")["points"][1] / c(s, "correia")["points"][0] >= 6, lambda s: (c(s, "correia")["value"], c(s, "correia")["points"])),
        ("correia", "forecast", "e 5-7 a jornada over the next 4", lambda s: 5 <= mean_e(c(s, "correia"), s["first"]) <= 7,
         lambda s: round(mean_e(c(s, "correia"), s["first"]), 1)),
        ("correia", "eleven", "a verdict from the swap rule over the next 4", lambda s: c(s, "correia")["swaps"]["next4"] is not None,
         lambda s: c(s, "correia")["swaps"]["next4"] and (round(c(s, "correia")["swaps"]["next4"].gain, 1), c(s, "correia")["swaps"]["next4"].improves)),
        ("correia", "opportunities", "not on the game's market, so not an opportunity", lambda s: c(s, "correia")["id"] not in s["opportunities"],
         lambda s: sorted(s["opportunities"])),
    ]


def degraded_checks() -> list[Check]:
    def t(s: Seen) -> Seen:
        got: Seen = s["cases"]["terrats"]
        return got

    return [
        ("terrats", "status", "no listing row (the club's page missing from the latest snapshot)", lambda s: t(s)["status"] is None, lambda s: t(s)["status"]),
        ("terrats", "forecast", "flagged as having no start % (listed_pct 0)", lambda s: t(s)["rate"][3] == 0, lambda s: t(s)["rate"]),
        ("terrats", "forecast", "from his record: p 0.8+ and e 3-4 a jornada over the next 4",
         lambda s: mean_p(t(s), s["first"]) >= 0.8 and 3 <= mean_e(t(s), s["first"]) <= 4,
         lambda s: (round(mean_p(t(s), s["first"]), 2), round(mean_e(t(s), s["first"]), 1))),
    ]


EXPECTED_FAILURES = {
    ("rodri", "forecast", "after the return e is near his 6 a game (4.5-7 over the 3 jornadas after it)"):
        "the injury listing's 0% start keeps weighing on his chance for weeks after the return",
}


def evaluate(seen: Seen, group: list[Check]) -> list[tuple[str, str, str, bool, Any]]:
    return [(case, stage, label, bool(ok(seen)), shown(seen)) for case, stage, label, ok, shown in group]


def failures(results: list[tuple[str, str, str, bool, Any]]) -> tuple[list[str], list[str]]:
    wrong = ["%s / %s: %s -> %s" % (case, stage, label, shown) for case, stage, label, ok, shown in results
             if not ok and (case, stage, label) not in EXPECTED_FAILURES]
    healed = ["%s / %s: %s" % (case, stage, label) for case, stage, label, ok, _ in results
              if ok and (case, stage, label) in EXPECTED_FAILURES]
    return wrong, healed


def report() -> None:
    doc = load()
    for group, degraded in ((checks(), False), (degraded_checks(), True)):
        for case, stage, label, ok, shown in evaluate(observe(doc, degraded), group):
            mark = "pass" if ok else "XFAIL" if (case, stage, label) in EXPECTED_FAILURES else "FAIL"
            print("%-5s %-8s %-13s %s -> %s" % (mark, case, stage, label, shown))


def _selftest() -> None:
    doc = load()
    results = evaluate(observe(doc), checks()) + evaluate(observe(doc, degraded=True), degraded_checks())
    wrong, healed = failures(results)
    assert not wrong, "cases that fail:\n" + "\n".join(wrong)
    assert not healed, "expected failures that now pass (remove them from EXPECTED_FAILURES):\n" + "\n".join(healed)
    print("cases self-test OK (%d checks, %d expected failures)" % (len(results), len(EXPECTED_FAILURES)))


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    elif sys.argv[1:2] == ["extract"]:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
        from ffcore.league import Config

        save(extract(Config.me))
        print("wrote %s (%d bytes)" % (FIXTURE, FIXTURE.stat().st_size))
    elif sys.argv[1:2] == ["report"]:
        report()
