from __future__ import annotations

import json
import lzma
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple

from ffcore import observations as ob

FIXTURE = Path(__file__).with_name("cases") / "league.json.xz"

CASES = {"raphinha": "4288", "garcia": "7279", "diarra": "10832", "rodri": "2733", "david": "9573", "terrats": "8944",
         "correia": "8091"}
DEGRADED_CLUB = "getafe"
CUT_AFTER_MINUTES = 10
AHEAD = 4



class Market(NamedTuple):
    player: str
    value: float


class Played(NamedTuple):
    player: str
    games: float
    points: float


class Known(NamedTuple):
    player: str
    name: str
    club_id: str
    ff_slug: str
    app_id: str


Seen = dict[str, Any]
Check = tuple[str, str, str, Callable[[Seen], bool], Callable[[Seen], Any]]


def load() -> dict[str, Any]:
    with lzma.open(FIXTURE, "rt", encoding="utf-8") as f:
        doc: dict[str, Any] = json.load(f)
    return doc


def observations(doc: dict[str, Any]) -> ob.Observations:
    """The fixture's tables as Observations: every table of ob.TABLES, its rows in the order stored, each column read by
    name into the table's row type (none for a table the fixture lacks)."""
    out: dict[str, list[Any]] = {}
    for name, source in ob.TABLES.items():
        t = doc["tables"].get(name, {"columns": [], "rows": []})
        at = [t["columns"].index(f) for f in source.row._fields] if t["rows"] else []
        out[name] = [source.row._make(ob.pooled(r[i] for i in at)) for r in t["rows"]]
    return ob.of(out)


def observed(doc: dict[str, Any], degraded: bool = False) -> Seen:
    from datetime import timedelta

    import decision
    import eleven
    from board import board
    from ffcore import current, forecast_facts, identity, league, loader, season_facts, state
    from ffcore.clock import cut, set_now
    from ffcore.parse import snapshot_stamp

    obs = observations(doc)
    if degraded:
        lineups = [r for r in ob.rows_of(obs, "lineups") if (r.team_slug, r.observed_at) != (DEGRADED_CLUB, doc["as_of"])]
        obs = ob.Observations({**obs.tables, **ob.of({"lineups": lineups}).tables})
    stamp = snapshot_stamp(doc["as_of"])
    assert stamp is not None
    set_now(stamp + timedelta(minutes=CUT_AFTER_MINUTES))
    loaded = loader.loaded(obs, league.Config())
    world = state.world_at(loaded.obs, cut())
    d = decision.decision_at(loaded, world)
    assert not isinstance(d, decision.Stale), d
    inp = d.inputs
    pos, cells = decision.cells_of(inp)
    mine = decision.squad_of(inp)
    first = min(c.jornada for c in season_facts.jornada_calendar(world) if c.state == "ahead")
    next_four = frozenset(range(first, first + AHEAD))
    windows = {"next4": next_four, "season": frozenset(cells)}
    values = {w: decision.window_value(pos, cells, js) for w, js in windows.items()}
    settled = {w: eleven.best_eleven(mine, pos, v) for w, v in values.items()}
    got = [] if degraded else decision.opportunity_rows(inp)
    opportunities = {r["player"] for r in got if r["status"] == "market" and r["gain"] > 0}
    doc_board: dict[str, Any] = {"buys": [], "can_bid": 0.0} if degraded else board(inp, got, [], d.extras, loader.health_at(loaded, cut()))
    seen: Seen = {"first": first, "opportunities": opportunities, "board": doc_board, "money": inp.money,
                  "squad": len(mine), "offers": sum(1 for o in current.offers(world) if o.status == "pending"),
                  "cases": {}}
    ids = tuple(CASES.values())
    people = {p.player_id: Known(*p) for p in identity.crosswalk(obs, identity.END_OF_TIME) if p.player_id in ids}
    stored = [r for r in ob.rows_of(obs, "points") if r.ff_id in ids]
    season = max((r.season for r in ob.rows_of(obs, "points")), default="")
    market = {k: [Market(r.ff_id, r.value) for r in ob.rows_of(obs, "market") if r.ff_id == k] for k in ids}
    points = {k: [Played(r.ff_id, r.games, r.points) for r in stored if r.ff_id == k and r.season == season] for k in ids}
    cell = {k: {c.jornada: (c.p, c.e) for c in forecast_facts.cell(d.fc) if c.player == k} for k in ids}
    total = {t.player: t for t in forecast_facts.cell_total(d.fc)}
    for name, k in CASES.items():
        swaps = {w: eleven.swap(k, s.eleven, pos, values[w]) if s else None for w, s in settled.items()}
        seen["cases"][name] = {
            "id": k,
            "value": market[k][-1].value if market[k] else None,
            "points": points[k][-1] if points[k] else None,
            "crosswalk": people.get(k),
            "status": d.fc.status.get(k),
            "rate": d.fc.rate.get(k),
            "cells": cell[k],
            "total": total.get(k),
            "late": sorted(x.jornada for x in d.fc.late if x.player == k),
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

    return [
        ("context", "tables", "cash is the pre-sale debt (about -49.5M)", lambda s: -50e6 < s["money"].cash < -49e6, lambda s: s["money"].cash),
        ("context", "tables", "team value about 464M", lambda s: 460e6 < s["money"].team_value < 470e6, lambda s: s["money"].team_value),
        ("context", "tables", "22 players and 17 standing offers", lambda s: (s["squad"], s["offers"]) == (22, 17), lambda s: (s["squad"], s["offers"])),
        ("context", "board", "bid limit about 43.4M", lambda s: 43e6 < s["board"]["can_bid"] < 44e6, lambda s: s["board"]["can_bid"]),
        ("context", "lineup", "the app's eleven is read: 11 fielded players all matched, or the board says why there is none",
         lambda s: (len(s["board"]["lineup"]["eleven"]) == 11 and s["board"]["lineup"]["unmapped"] == [])
         if s["board"]["lineup"]["eleven"] is not None else bool(s["board"]["lineup"]["message"]),
         lambda s: (s["board"]["lineup"]["eleven"], s["board"]["lineup"]["message"], s["board"]["lineup"]["unmapped"])),
        ("context", "lineup", "the advice is swaps of a bench player for a fielded one, each worth points",
         lambda s: all(a["gain"] > 0 and a["in"] != a["out"] for a in s["board"]["lineup"]["advice"]),
         lambda s: s["board"]["lineup"]["advice"]),
        ("raphinha", "tables", "price 150-200M, 8+ games at 12+ a game",
         lambda s: 150e6 < c(s, "raphinha")["value"] < 200e6 and c(s, "raphinha")["points"].games >= 8
         and c(s, "raphinha")["points"].points / c(s, "raphinha")["points"].games >= 12, lambda s: (c(s, "raphinha")["value"], c(s, "raphinha")["points"])),
        ("raphinha", "crosswalk", "has his slug and his app id",
         lambda s: bool(c(s, "raphinha")["crosswalk"].ff_slug) and bool(c(s, "raphinha")["crosswalk"].app_id), lambda s: c(s, "raphinha")["crosswalk"]),
        ("raphinha", "status", "listed, starting 85%+, no flag",
         lambda s: c(s, "raphinha")["status"].pct >= 85 and c(s, "raphinha")["status"].status is None, lambda s: c(s, "raphinha")["status"]),
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
        ("raphinha", "board", "no buy line: against the cheapest plan with no buy his net is 0 or less, so he is hidden and counted",
         lambda s: c(s, "raphinha")["buy"] is None and s["board"]["considered"] >= 1 and s["board"]["base"]["cost"] > 0,
         lambda s: (c(s, "raphinha")["buy"], s["board"]["considered"], s["board"]["base"]["cost"])),
        ("garcia", "tables", "price 40-70M, 8+ games", lambda s: 40e6 < c(s, "garcia")["value"] < 70e6 and c(s, "garcia")["points"].games >= 8,
         lambda s: (c(s, "garcia")["value"], c(s, "garcia")["points"])),
        ("garcia", "crosswalk", "has his slug", lambda s: bool(c(s, "garcia")["crosswalk"].ff_slug), lambda s: c(s, "garcia")["crosswalk"]),
        ("garcia", "forecast", "e 6-8 a jornada over the next 4", lambda s: 6 <= mean_e(c(s, "garcia"), s["first"]) <= 8,
         lambda s: round(mean_e(c(s, "garcia"), s["first"]), 1)),
        ("garcia", "opportunities", "not for sale, so not an opportunity", lambda s: c(s, "garcia")["id"] not in s["opportunities"], lambda s: sorted(s["opportunities"])),
        ("diarra", "tables", "under 1M, never scored", lambda s: c(s, "diarra")["value"] < 1e6 and c(s, "diarra")["points"] is None,
         lambda s: (c(s, "diarra")["value"], c(s, "diarra")["points"])),
        ("diarra", "crosswalk", "has his app id, so the app can say he left", lambda s: bool(c(s, "diarra")["crosswalk"].app_id), lambda s: c(s, "diarra")["crosswalk"]),
        ("diarra", "status", "gone", lambda s: c(s, "diarra")["status"] is not None and c(s, "diarra")["status"].status == "gone", lambda s: c(s, "diarra")["status"]),
        ("diarra", "forecast", "p 0 and e 0 all season", lambda s: all(v == (0.0, 0.0) for v in c(s, "diarra")["cells"].values()) and c(s, "diarra")["total"].e == 0.0,
         lambda s: (c(s, "diarra")["total"], len(c(s, "diarra")["cells"]))),
        ("diarra", "opportunities", "never an opportunity", lambda s: c(s, "diarra")["id"] not in s["opportunities"], lambda s: sorted(s["opportunities"])),
        ("rodri", "status", "injured, with the return note", lambda s: c(s, "rodri")["status"].status == "injured" and "diciembre" in c(s, "rodri")["status"].note,
         lambda s: c(s, "rodri")["status"]),
        ("rodri", "forecast", "out (p 0) for the 3 jornadas to come, then back", lambda s: all(c(s, "rodri")["cells"][s["first"] - 1 + i][0] == 0.0 for i in range(3))
         and c(s, "rodri")["cells"][s["first"] + 2][0] > 0, lambda s: [(j, round(p, 2)) for j, (p, _) in sorted(c(s, "rodri")["cells"].items())[:6]]),
        ("rodri", "forecast", "after the return e is near his 6 a game (4.5-7 over the 3 jornadas after it)",
         lambda s: 4.5 <= mean_e(c(s, "rodri"), s["first"] + 2, 3) <= 7, lambda s: round(mean_e(c(s, "rodri"), s["first"] + 2, 3), 1)),
        ("david", "status", "listed at a low start % (40 or less)", lambda s: c(s, "david")["status"].pct <= 40, lambda s: c(s, "david")["status"]),
        ("david", "forecast", "low but not zero over the next 4 (above 1, under his 8.8 a game)",
         lambda s: 1 < mean_e(c(s, "david"), s["first"]) < 8.8, lambda s: round(mean_e(c(s, "david"), s["first"]), 1)),
        ("david", "forecast", "season total clearly above 0 (over 30)", lambda s: c(s, "david")["total"].e > 30, lambda s: c(s, "david")["total"]),
        ("correia", "tables", "6-12M, 7+ games at 6+ a game", lambda s: 6e6 < c(s, "correia")["value"] < 12e6 and c(s, "correia")["points"].games >= 7
         and c(s, "correia")["points"].points / c(s, "correia")["points"].games >= 6, lambda s: (c(s, "correia")["value"], c(s, "correia")["points"])),
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
        ("terrats", "forecast", "flagged as having no start % (listed_pct 0)", lambda s: not t(s)["rate"].listed_pct, lambda s: t(s)["rate"]),
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
        for case, stage, label, ok, shown in evaluate(observed(doc, degraded), group):
            mark = "pass" if ok else "XFAIL" if (case, stage, label) in EXPECTED_FAILURES else "FAIL"
            print("%-5s %-8s %-13s %s -> %s" % (mark, case, stage, label, shown))


def _selftest() -> None:
    doc = load()
    results = evaluate(observed(doc), checks()) + evaluate(observed(doc, degraded=True), degraded_checks())
    wrong, healed = failures(results)
    assert not wrong, "cases that fail:\n" + "\n".join(wrong)
    assert not healed, "expected failures that now pass (remove them from EXPECTED_FAILURES):\n" + "\n".join(healed)
    print("cases self-test OK (%d checks, %d expected failures)" % (len(results), len(EXPECTED_FAILURES)))


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    elif sys.argv[1:2] == ["report"]:
        report()
