"""End to end from an empty root: a handful of observation rows and the real stages
over them, nothing seeded past the rows, the app ids of the players and the league
config. The self-test is the only thing here (tools/selftests.sh)."""
from __future__ import annotations

import sys


def _selftest() -> None:
    """The observation rows, their Loaded, decision.decisions (the seen facts, the forecast, the inputs, the extras) and
    board.board (the decision's inputs, your squad and its eleven, the standing offers ranked): a forecast and a board come out."""
    from ffcore import decision_facts, forecast_facts, identity, ledger
    from ffcore import observations as ob
    from ffcore.clock import cut, set_now
    from ffcore.parse import snapshot_stamp
    from ffcore.league import Config
    from ffcore.loader import Loaded, health_at
    import decision
    from board import TOP_TARGETS, board

    ME = Config.me

    seen_at, cut_at = "2026-09-20T0800Z", "2026-09-20T1200Z"
    clubs = ("a", "b", "c", "d")
    pos = ("portero", "defensa", "defensa", "defensa", "defensa", "centrocampista", "centrocampista",
           "centrocampista", "delantero", "delantero")
    players = [("%s%d" % (c, i), c, p) for c in clubs for i, p in enumerate(pos)]
    mine, rivals = [k for k, _c, _p in players[:12]], [k for k, _c, _p in players[12:24]]
    listed = [k for k, _c, _p in players[24:30]]
    obs = ob.of({
        "market": [ob.Market(seen_at, "", 2e6 + 1e5 * n, k, p, c, k) for n, (k, c, p) in enumerate(players)],
        "matches": [ob.Matches(seen_at, "", j, "m%d%s" % (j, h), "", h, w, s, at)
                    for j, h, w, s, at in [(3, "a", "b", "1-0", "2026-09-13T18:00:00+00:00"),
                                           (3, "c", "d", "0-0", "2026-09-13T18:00:00+00:00"),
                                           (4, "a", "c", "", "2026-09-26T18:00:00+00:00"),
                                           (4, "b", "d", "", "2026-09-26T18:00:00+00:00"),
                                           (5, "a", "d", "", "2026-10-03T18:00:00+00:00"),
                                           (5, "b", "c", "", "2026-10-03T18:00:00+00:00")]],
        "points": [ob.Points(at, "", float(g) * (1 + n % 7) * 2, float(g), None, k, "", "", "", "2026-27")
                   for at, g in (("2026-09-06T0800Z", 0), ("2026-09-14T0800Z", 1))
                   for n, (k, _c, _p) in enumerate(players)],
        "api_standings": [ob.ApiStandings(seen_at, "", i, None, None, None, None, "t" + u, u, m)
                          for i, (u, m) in enumerate((("1", ME), ("2", "riv")), 1)],
        "api_teams": [ob.ApiTeams(seen_at, "", 3e6, None, None, str(n), "", m, "", "", "", "t%d" % n)
                      for n, (k, _c, _p) in enumerate(players, 1)
                      for m in [ME if k in mine else "riv" if k in rivals else ""] if m],
        "api_market": [ob.ApiMarket(seen_at, "", 3e6, 3e6, None, None, str(n), "m%d" % n, "", "", "marketPlayerTeam", "", "",
                                    "2026-09-21T22:00:00+02:00", "", "")
                       for n, (k, _c, _p) in enumerate(players, 1) if k in listed],
        "api_offers": [ob.ApiOffers(seen_at, "", 3.3e6, "t1", "o1", "pending", "", "2026-09-21T22:00:00+02:00", "")]})
    apps = [identity.AppLink(str(n), k) for n, (k, _c, _p) in enumerate(players, 1)]
    cfg = Config()
    loaded = Loaded(obs, identity.identity_of((), apps), ledger.League(cfg.me, cfg.budget), forecast_facts.knobs(), ())
    set_now(snapshot_stamp(cut_at))
    try:
        [d] = decision.decisions(loaded, [cut()])
        assert not isinstance(d, decision.Stale), d
        assert len(d.fc.now) > 0, "a forecast"
        assert len(decision_facts.my_squad(d.seen, loaded.league)) == len(mine), "your squad as the app showed it"
        inp = d.inputs
        got = decision.opportunity_rows(inp)
        doc = board(inp, got, decision.arrivals(got, loaded.previous), d.extras, health_at(loaded, cut()))
        assert len(doc["squad"]) == len(mine) and sum(r["xi"] for r in doc["squad"]) == 11, "your squad and its eleven"
        assert {"generated_at", "market_at", "sources", "cash", "can_bid", "lock_at", "do", "buys", "sells"} <= set(doc), doc
        assert "market" not in doc, "the market block is gone: one exchange rate, `lambda`"
        (line,) = doc["sells"]
        assert (line["label"], line["step"], line["offer"]) == ("sell A0", "take offer", 3.3e6), line
        assert abs(line["ratio"] - 1.1) < 1e-9, "the standing offer on one of yours, over his value"
        assert all(b["gain"] > 0 and b["accepts"] == [] for b in doc["buys"]), doc["buys"]
        assert all((b["advised"] is None) == (b["advised_win"] is None) and (b["win"] is None or 0 < b["win"] <= 1)
                   for b in doc["buys"]), ("a chance is one from a curve or none at all", doc["buys"])
        kinds = {r["player"]: r["status"] for r in got}
        assert {kinds[k] for k in rivals} == {"rival"} and {kinds[k] for k in listed} <= {"market"}, \
            "the market's players are on sale, the rivals' are not: both are judged"
        assert all(b["advised"] is None or b["advised"] >= b["asking"] for b in doc["buys"]), "never a bid advised below the asking price"
        assert {t["status"] for t in doc["targets"]} >= {"rival"} or not doc["targets"], doc["targets"]
        assert len(doc["targets"]) == min(TOP_TARGETS, sum(1 for r in got if r["gain"] > 0)), "the targets: those with a gain"
        for_sale = sum(1 for r in got if r["gain"] > 0 and r["status"] == "market" and r["asking"] is not None)
        assert len(doc["plans"]) + len(doc["rejected"]) == for_sale, \
            "every listing on sale with a gain is a funded plan or in the rejected with its reason: none is dropped silently"
        assert {b["key"] for b in doc["buys"]} <= {p["key"] for p in doc["plans"]} and all(r["reason"] for r in doc["rejected"]), "the buys are funded plans"
        assert all(b["net"] is None or b["net"] > 0 for b in doc["buys"]), "no buy that adds nothing to doing nothing is shown"
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
