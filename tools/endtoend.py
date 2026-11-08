"""End to end from an empty root: a handful of raw rows in the scraped tables and
the real stages over them, nothing seeded past the scraped tables, the crosswalk
and league config. The self-test is the only thing here (tools/selftests.sh)."""
from __future__ import annotations

import sys


def _selftest() -> None:
    """The view build (connect), assemble.built (seen, the forecast moment, look) and board.board (the decision's
    inputs, your squad and its eleven, the standing offers ranked): a forecast and a board come out."""
    import contextlib
    import tempfile
    from pathlib import Path

    from assemble import built
    from ffcore.db import record, rows, set_now
    from ffcore.parse import snapshot_stamp
    from ffcore.tidy import store_sql, tables_in
    from ffcore.league import Config
    import decision
    from board import board

    ME = Config.me

    seen_at, cut_at = "2026-09-20T0800Z", "2026-09-20T1200Z"
    clubs = ("a", "b", "c", "d")
    pos = ("portero", "defensa", "defensa", "defensa", "defensa", "centrocampista", "centrocampista",
           "centrocampista", "delantero", "delantero")
    players = [("%s%d" % (c, i), c, p) for c in clubs for i, p in enumerate(pos)]
    mine, rivals = [k for k, _c, _p in players[:12]], [k for k, _c, _p in players[12:24]]
    listed = [k for k, _c, _p in players[24:30]]
    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)), contextlib.chdir(tmp):  # no league.ini: the default you
        set_now(snapshot_stamp(cut_at))
        try:
            record([{"player_id": k, "app_id": str(n)} for n, (k, _c, _p) in enumerate(players, 1)], "crosswalk")
            store_sql("market", [{"observed_at": seen_at, "ff_id": k, "name": k, "position": p, "club": c,
                                  "value": str(2e6 + 1e5 * n)} for n, (k, c, p) in enumerate(players)])
            store_sql("matches", [
                {"observed_at": seen_at, "match_id": "m%d%s" % (j, h), "jornada": str(j), "home": h, "away": w,
                 "score": s, "kickoff": at}
                for j, h, w, s, at in [(3, "a", "b", "1-0", "2026-09-13T18:00:00+00:00"),
                                       (3, "c", "d", "0-0", "2026-09-13T18:00:00+00:00"),
                                       (4, "a", "c", "", "2026-09-26T18:00:00+00:00"),
                                       (4, "b", "d", "", "2026-09-26T18:00:00+00:00"),
                                       (5, "a", "d", "", "2026-10-03T18:00:00+00:00"),
                                       (5, "b", "c", "", "2026-10-03T18:00:00+00:00")]])
            store_sql("points", [{"observed_at": at, "season": "2026-27", "ff_id": k, "games": g,
                                  "points": str(float(g) * (1 + n % 7) * 2)}
                                 for at, g in (("2026-09-06T0800Z", "0"), ("2026-09-14T0800Z", "1"))
                                 for n, (k, _c, _p) in enumerate(players)])
            store_sql("api_standings", [{"observed_at": seen_at, "user_id": u, "manager": m, "position": i}
                                        for i, (u, m) in enumerate((("1", ME), ("2", "riv")), 1)])
            store_sql("api_teams", [{"observed_at": seen_at, "player_id": str(n), "manager": m,
                                     "player_team_id": "t%d" % n, "market_value": "3000000"}
                                    for n, (k, _c, _p) in enumerate(players, 1)
                                    for m in [ME if k in mine else "riv" if k in rivals else ""] if m])
            store_sql("api_market", [{"observed_at": seen_at, "player_id": str(n), "seller": "marketPlayerTeam",
                                      "sale_price": "3000000", "market_value": "3000000",
                                      "expires_at": "2026-09-21T22:00:00+02:00"}
                                     for n, (k, _c, _p) in enumerate(players, 1) if k in listed])
            store_sql("api_offers", [{"observed_at": seen_at, "money": "3300000", "player_team_id": "t1", "offer_id": "o1",
                                      "status": "pending", "expires_at": "2026-09-21T22:00:00+02:00"}])
            record([{"run_id": 1, "started": "t", "as_of": "t", "trigger": "test", "commit_sha": "c"}], "runs")

            b = built()
            assert rows("SELECT count(*) FROM forecast_cell WHERE moment = ?", (b.moment,))[0][0] > 0, "a forecast"
            assert len(rows("SELECT player FROM my_squad")) == len(mine), "your squad as the app showed it"
            doc = board(decision.read_inputs())
            assert len(doc["squad"]) == len(mine) and sum(r["xi"] for r in doc["squad"]) == 11, "your squad and its eleven"
            assert {"generated_at", "cash", "can_bid", "lock_at", "do", "buys", "sells", "finish", "band"} <= set(doc), doc
            (line,) = doc["sells"]
            assert (line["label"], line["step"], line["offer"]) == ("sell A0", "take offer", 3.3e6), line
            assert abs(line["ratio"] - 1.1) < 1e-9, "the standing offer on one of yours, over his value"
            (listing,) = doc["do"]
            assert listing["what"] == "list" and listing["accepts"] == [None] and len(listing["act"]) == len(mine), \
                "no one of yours is listed yet: one line lists them all, each with his place in your team"
            assert {a["player_team"] for a in listing["act"]} == {"t%d" % n for n in range(1, len(mine) + 1)}, listing["act"]
        finally:
            set_now(None)
    print("endtoend self-test OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
