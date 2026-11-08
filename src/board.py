"""The board stage: decisions.json, the phone's copy of the board, from the league at t. Your cash and what you can
bid, your squad with its eleven, and each standing offer on your players ranked by what taking it costs your eleven:
the one line act.py takes (do: list your unlisted players at their value). finish and band are placeholders: the page reads them."""
from __future__ import annotations

import json
import sys
from contextlib import closing
from typing import Any

import decision
from assemble import built
from ffcore.db import connect, cut, record, rows, this_run
from ffcore.jornadas import rounds
from ffcore.rules import POSITIONS, bid_limit
from ffcore.tidy import REPORTS

__all__ = ["board", "main"]

Doc = dict[str, Any]
AHEAD = (4, 8, 0)


def stamped(t: str) -> str:
    return t[:13] + ":" + t[13:]


def squad_rows(inp: dict[str, list[tuple[Any, ...]]]) -> tuple[list[Doc], dict[str, str]]:
    pos, cells = decision.cells_of(inp)
    mine = {k for k, _, m, *_ in inp["dec_player"] if m}
    names = {k: n for k, n in rows("SELECT player, name_shown(name) FROM player_facts")}
    first = min(cells, default=0)
    e = cells.get(first, {})
    xi = set(decision.eleven(pos, e, mine)[1])
    order = sorted(mine, key=lambda k: (k not in xi, POSITIONS.index(pos[k]) if k in pos else len(POSITIONS), -e.get(k, 0.0), k))
    return [{"name": names.get(k, k), "pos": pos.get(k, ""), "next": e.get(k, 0.0), "xi": k in xi, "sold": False,
             "key": k} for k in order], names


def offer_lines(inp: dict[str, list[tuple[Any, ...]]], names: dict[str, str]) -> list[Doc]:
    pos, cells = decision.cells_of(inp)
    mine = {k for k, _, m, *_ in inp["dec_player"] if m}
    js = sorted(cells)
    base = {n: sum(decision.by_jornada(pos, cells, mine, set(js[:n or len(js)])).values()) for n in AHEAD}
    got = rows("SELECT t.player, t.market_value, o.money, f.pos, f.trend FROM offers o"
               " JOIN squads t ON t.player_team_id = o.player_team_id LEFT JOIN player_facts f ON f.player = t.player"
               " WHERE o.status = 'pending' AND o.money <> 0 AND t.player IN (%s)" % ",".join("?" * len(mine)),
               sorted(mine))
    out = []
    for k, value, money, p, trend in got:
        lost = {n: base[n] - sum(decision.by_jornada(pos, cells, mine - {k}, set(js[:n or len(js)])).values()) for n in AHEAD}
        ratio = money / value if value else None
        name = names.get(k, k)
        out.append({
            "name": name, "pos": p or pos.get(k, ""), "what": "sell", "label": "sell " + name, "step": "take offer",
            "offer": money, "waiting": None, "trend": trend, "plan": False, "amount": money, "done": False, "key": k,
            "gain": 0.0 - lost[0], "per": "season", "more": False, "detail": None, "trace": None, "flip": None,
            "ratio": ratio, "lost": {"next4": lost[4], "next8": lost[8], "season": lost[0]},
            "per_million": lost[8] / (money / 1e6) if money else None,
            "facts": [["value", value, "money"], ["offer", money, "money"], ["offer over value", ratio, "ratio"],
                      ["points lost, next 4", lost[4], "points"], ["next 8", lost[8], "points"],
                      ["season", lost[0], "points"]],
            "accepts": [], "act": []})
    return sorted(out, key=lambda d: (d["per_million"] is None, d["per_million"], d["key"]))


def list_line(inp: dict[str, list[tuple[Any, ...]]], names: dict[str, str]) -> list[Doc]:
    mine = {k for k, _, m, *_ in inp["dec_player"] if m}
    listed = {k for (k,) in rows("SELECT player FROM listings WHERE player IS NOT NULL")}
    got = {k: (ptid, pid, value, p) for k, ptid, pid, value, p in rows(
        "SELECT t.player, t.player_team_id, t.player_id, t.market_value, f.pos FROM squads t"
        " LEFT JOIN player_facts f ON f.player = t.player WHERE t.player IN (%s)" % ",".join("?" * len(mine)), sorted(mine))}
    todo = sorted(mine - listed, key=lambda k: (names.get(k, k), k))
    if not todo:
        return []
    label = "list " + ", ".join(names.get(k, k) for k in todo)
    return [{"name": ", ".join(names.get(k, k) for k in todo), "pos": "", "what": "list", "label": label, "step": "list",
             "amount": None, "done": False, "accepts": [None], "gain": None, "per": None, "facts": [], "more": False,
             "detail": None, "trace": None, "flip": None, "plan": False,
             "act": [{"player_team": got[k][0], "player": got[k][1] or k, "market": None, "bid": None, "pos": got[k][3],
                      "value": got[k][2]} for k in todo if k in got]}]


def board(inp: dict[str, list[tuple[Any, ...]]]) -> Doc:
    (cash, team_value, debt, _squad_max, _xi), = inp["dec_money"]
    locks = [lock for _, lock in inp["dec_lock"]]
    bids = sum(amount for _, amount in inp["dec_bid"])
    squad, names = squad_rows(inp)
    return {
        "generated_at": stamped(cut()),
        "lock_at": stamped(min(locks)).replace("Z", ":00+00:00") if locks else None,
        "cash": cash, "cash_locked": bids, "can_bid": max(0.0, bid_limit(cash, team_value, debt) - bids),
        "ping": "", "do": list_line(inp, names), "buys": [], "sells": offer_lines(inp, names),
        "squad": [{k: v for k, v in r.items() if k != "key"} for r in squad],
        "finish": 0.0, "band": [0.0, 0.0]}


def main() -> None:
    b = built()
    (managers, ), = rows("SELECT count(*) FROM cash")
    left = rounds().rem
    if managers < 2 or not left:
        print("board: nothing to decide (%d managers, %d jornadas left)" % (managers, len(left)))
        return
    run = this_run()
    if run is None:
        raise SystemExit("board: the board is the run's: run it as run.py board")
    record([{"run_id": run, **r} for r in b.params], "params")
    with closing(connect()) as db, db:
        db.execute("UPDATE forecast_moment SET pinned = 1 WHERE moment = ?", (b.moment,))
    doc = board(decision.read_inputs())
    REPORTS.mkdir(exist_ok=True)
    (REPORTS / "decisions.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("wrote %s" % (REPORTS / "decisions.json"))


def _selftest() -> None:
    assert stamped("2026-10-30T1809Z") == "2026-10-30T18:09Z"
    print("board self-test OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        raise SystemExit(0)
    main()
