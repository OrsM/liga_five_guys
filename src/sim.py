
from __future__ import annotations

import sys


import json

from decide import (best_move, max_spare_proceeds, overdraft_fix,  # noqa: E402
                    value_rate, worth_doing)
from ffcore.parse import fmt_money
from ffcore.league import app_fielded
from ffcore.render import title_name
from ffcore.tidy import (run_now, shown,
                         ALERTS, REPORTS, WARNINGS, write_lines)

__all__ = ["shape"]


def squad_value(u) -> float:
    return sum(u.view("proceeds").values())


def fielded_keys(u=None) -> list[str]:
    return app_fielded(u.state.squads.get(u.me, {}), u.view("name")) if u else []


def xi_change(marked: list[str], best) -> dict:
    best = list(best)
    if len(marked) != len(best) or not marked:
        return {"legal": False, "marked": len(marked), "in": [], "out": []}
    have, want = set(marked), set(best)
    return {"legal": True, "marked": len(marked),
            "in": [k for k in best if k not in have],
            "out": [k for k in marked if k not in want]}


def fielded_shape(u, xi=None) -> str:

    if xi is None:
        _, xi = u.current_xi
    keys = fielded_keys(u)
    return shape(u, keys) if xi_change(keys, xi)["legal"] else ""


def short(key, u) -> str:
    full = title_name(u.view("name").get(key, key))
    last = full.split()[-1] if full.split() else full
    clash = sum(1 for k in u.state.squads.get(u.me, {})
                if title_name(u.view("name").get(k, k)).split()[-1:] == [last])
    return full if clash > 1 else last


SLOT_ORDER = {"POR": 0, "DEF": 1, "MED": 2, "DEL": 3}


def by_slot(u, keys, exp=None):

    if exp is None:
        exp, _ = u.current_xi
    return sorted(keys, key=lambda k: (SLOT_ORDER.get(u.view("pos").get(k, ""), 9),
                                       -exp.get(k, 0.0)))


def short_manager(m: str) -> str:
    return m.split()[0] if m else m


GROUP_LABEL = {
    "in": "PUT ON", "out": "TAKE OFF",
    "field": "FIELD — your eleven — the app has not said what you are playing",
    "keep": "KEEP — bench", "sell": "SELL — never start",
    "offer": "OFFERS — someone wants him",
    "buy": "BUY — free agents",
    "raid": "RAID — a clause, cannot be refused",
    "save": "SAVE — better than yours, out of reach", "pass": "PASS",
}


def ladder_rows(u, rows, bands=None, exp=None, xi=None) -> list[dict]:

    if exp is None or xi is None:
        exp, xi = u.current_xi
    mine = u.state.squads.get(u.me, {})
    dead_weight = u.dead_weight()
    dead = {k for k, _ in dead_weight}
    won = {r["action"].buy: r for r in rows if r["action"].buy}
    bar = u.xi_bar
    spare = max_spare_proceeds(u)
    rest = [k for k in u.view("price") if k not in mine and exp.get(k, 0.0) > bar]
    bands = {k: v for k, v in (bands or {}).items() if k not in won}
    from ffcore.bid import deals, premiums as _premiums, suggest
    _dl = deals(u.lg, u.lg.market) if u.lg and u.lg.market else []
    buy_prem = _premiums(_dl, "buy")
    sell_prem = _premiums(_dl, "sell")
    _rival_max = max(u.rival_cash.values(), default=None)

    par = {k: v["par"] for k, v in u.player_forecasts.items()}

    def cell(k, group, where, money, pts, note="", value=None,
            lo=None, hi=None, market=None, premium=None):
        if k in bands:
            pts, lo, hi, _action, mean = bands[k]
        else:
            mean = None
        _worth = u.view("value").get(k)
        _ask = suggest(_worth, buy_prem, u.cash + spare, _rival_max)
        _hold = suggest(_worth, sell_prem)
        return {"offer": u.received_offers.get(k),
                "rivals": u.view("bids").get(k),
                "ask": _ask.low,
                "worth": _worth,
                "going": _hold.low,
                "name": title_name(u.view("name").get(k, k)),
                "pos": u.view("pos").get(k, ""), "start": u.view("start").get(k, 0.0),
                "xpts": exp.get(k, 0.0), "group": group, "where": where,
                "label": GROUP_LABEL[group],
                "money": money, "pts": pts, "par": par.get(k),
                "pts_lo": lo, "pts_hi": hi, "pts_mean": mean,
                "note": note, "value": value,
                "market": market, "premium": premium,
                }

    out = []
    chg = xi_change(fielded_keys(u), xi)
    if chg["legal"]:
        for k in by_slot(u, chg["in"], exp):
            out.append(cell(k, "in", "bench", None, None))
        for k in by_slot(u, chg["out"], exp):
            out.append(cell(k, "out", "yours", None, None))
    else:
        for k in by_slot(u, xi, exp):
            out.append(cell(k, "field", "yours", None, None))
    moving = set(chg["in"]) | set(chg["out"])
    benched = [k for k in mine if k not in xi and k not in dead
               and k not in moving]
    for k in by_slot(u, benched, exp):
        out.append(cell(k, "keep", "yours", None, None))
    for k in sorted(dead, key=lambda k: -exp.get(k, 0.0)):
        out.append(cell(k, "sell", "yours", u.view("proceeds").get(k, 0.0),
                        None))

    mine_all = set(u.state.squads.get(u.me, {}))
    for k in sorted((k for k in u.received_offers if k in mine_all),
                    key=lambda k: -(u.received_offers[k]
                                    / (u.view("value").get(k) or 1e18))):
        out.append(cell(k, "offer", "yours", None, None))

    def buy_cell(k, group):
        r = won[k]
        sold = r["action"].sell
        note = ("sell " + " + ".join(short(s, u) for s in sold)) if sold else ""
        return cell(k, group, short_manager(u.view("owner").get(k)) or "free agent",
                    -r["action"].net, r["d_pts"], note, value=r.get("value"),
                    lo=r.get("pts_lo"), hi=r.get("pts_hi"),
                    market=u.view("value").get(k), premium=r.get("burn") or 0.0)

    offer_keys = {r["action"].buy
                  for r in worth_doing(u, [won[k] for k in rest if k in won])
                  if r["action"].buy}
    for k in sorted((k for k in rest if k in offer_keys),
                    key=lambda k: _move_rank_key(won[k], u)):
        kind = u.route_kind(k)
        if kind in ("free", "raid"):
            out.append(buy_cell(k, "buy" if kind == "free" else "raid"))
    for k in sorted((k for k in rest if k not in won
                     and u.view("price")[k] > u.cash + spare),
                    key=lambda k: -exp.get(k, 0.0)):
        short_by = u.view("price")[k] - u.cash - spare
        save_pts = bands[k][0] if k in bands else None
        out.append(cell(k, "save", short_manager(u.view("owner").get(k)) or "free agent",
                        -short_by, save_pts, "short",
                        value=value_rate(save_pts, short_by)))
    for k in sorted((k for k in rest if k not in won
                     and u.view("price")[k] <= u.cash + spare
                     and u.route_kind(k) == "free"),
                    key=lambda k: -exp.get(k, 0.0)):
        out.append(cell(k, "pass", short_manager(u.view("owner").get(k)) or "free agent",
                        -u.view("price")[k], None))
    return out


def band_acts(u, exp=None, xi=None) -> list:
    import decide

    if exp is None or xi is None:
        exp, xi = u.current_xi
    mine = u.state.squads.get(u.me, {})
    bar = u.xi_bar
    acts = [(k, decide.Action("sell", sell=(k,),
                              proceeds=u.view("proceeds").get(k, 0.0)))
           for k in mine]
    acts += [(k, decide.Action("buy", buy=k, cost=u.view("price").get(k, 0.0)))
            for k in u.view("price")
            if k not in mine and exp.get(k, 0.0) > bar]
    return acts


def _move_rank_key(r, u):
    reliable = 0 if u.view("route").get(r["action"].buy, "free") != "listed" else 1
    d = r.get("d_pts")
    return (reliable, -d if d is not None else float("inf"))

def bid_lines(u, rows) -> list[str]:
    if not u.my_bids:
        return []
    name = lambda k: title_name(u.view("name").get(k, k))          # noqa: E731

    cost_of: dict[str, float] = {}
    for r in rows:
        k = r["action"].buy
        if k in u.my_bids and k not in cost_of:
            cost_of[k] = r["action"].net
    keep, drop, spent = [], [], 0.0
    for k in cost_of:
        if spent + cost_of[k] <= u.cash:
            keep.append(k)
            spent += cost_of[k]
        else:
            drop.append((k, u.my_bids[k]))
    drop += [(k, m) for k, m in u.my_bids.items() if k not in cost_of]
    drop.sort(key=lambda kv: -kv[1])
    out = []
    if drop:
        out.append("**Withdraw %s** — %s. Today's board does not buy %s at "
                   "that price, or cannot pay for %s alongside what it does "
                   "buy. Withdrawing frees the money at no points cost."
                   % (fmt_money(sum(m for _k, m in drop)),
                      ", ".join("%s %s" % (name(k), fmt_money(m))
                                for k, m in drop),
                      "him" if len(drop) == 1 else "them",
                      "him" if len(drop) == 1 else "them"))
    free = u.cash - u.locked_cash
    if free < 0:
        out.append("**%s bid, %s in hand** — if every live bid lands you are "
                   "%s short, and the app will let that happen. Withdraw or "
                   "sell before they resolve."
                   % (fmt_money(u.locked_cash), fmt_money(u.cash),
                      fmt_money(-free)))
    return out


def alert_lines(u, rows, rivals) -> list[str]:
    out = bid_lines(u, rows)
    if u.cash < 0:
        sells, short = overdraft_fix(u)
        if not sells:
            return out + ["**Overdrawn %s** — no safe dead-weight sale covers "
                          "it; needs a manual look before the jornada locks."
                          % fmt_money(-u.cash)]
        names = ", ".join("%s (+€%.1fM)" % (title_name(u.view("name").get(k, k)),
                                            p / 1e6) for k, p in sells)
        if short > 0:
            return out + ["**Overdrawn %s** — sell %s clears most of it, "
                          "still €%.1fM short (no further safe dead-weight "
                          "sale)." % (fmt_money(-u.cash), names, short / 1e6)]
        return out + ["**Overdrawn %s** — sell %s to clear it before the "
                      "jornada locks (zero points cost: %s never start your "
                      "eleven)."
                      % (fmt_money(-u.cash), names,
                         "he doesn't" if len(sells) == 1 else "they don't")]

    best, uncertain = best_move(u, rows, rivals)
    if best is None:
        return out
    a = best["action"]
    net = a.net
    if net > 0:
        cost = "-€%.1fM" % (net / 1e6)
    elif net < 0:
        cost = "+€%.1fM raised" % (-net / 1e6)
    else:
        cost = "free"
    if uncertain:
        cost += " · needs the seller to accept, not guaranteed"
    return out + ["**Do this** — %s (%+.0f season pts, %+.0f%% to win, %s)"
                  % (a.label({k: title_name(v)
                              for k, v in u.view("name").items()}),
                     best["d_pts"], 100 * best["d_win"], cost)]


def shape(u, keys) -> str:
    n = {}
    for k in keys:
        n[u.view("pos").get(k, "")] = n.get(u.view("pos").get(k, ""), 0) + 1
    return "%d-%d-%d" % (n.get("DEF", 0), n.get("MED", 0), n.get("DEL", 0))


def _xi_total(u, who, exp=None) -> float:
    from ffcore.season import best_xi
    if exp is None:
        exp, _ = u.current_xi
    xi = best_xi(u.state.squads.get(who, {}), exp)
    return sum(exp.get(k, 0.0) for k in xi)


def _shape_now(u, xi=None) -> str:
    if xi is None:
        _, xi = u.current_xi
    return shape(u, xi)


def _rival_best(u, exp=None) -> dict:
    if exp is None:
        exp, _ = u.current_xi
    out = [(_xi_total(u, m, exp=exp), m) for m in u.state.squads if m != u.me]
    if not out:
        return {}
    total, who = max(out)
    return {"manager": who, "xi": total,
           "gap": _xi_total(u, u.me, exp=exp) - total}


def payload(u, rows, base, rivals, locks_h=None, n_actions: int = 0,
            ladder_data=None, exp=None, xi=None) -> dict:

    if exp is None or xi is None:
        exp, xi = u.current_xi
    names = {k: title_name(v) for k, v in u.view("name").items()}
    lo, hi = base.band(u.me)

    chg = xi_change(fielded_keys(u), xi)
    if not chg["legal"]:
        xi_note = ("the app has not said which eleven you are fielding, so "
                   "this is the whole sheet rather than a change list")
    elif not chg["in"] and not chg["out"]:
        xi_note = "no change — you are already fielding the best eleven"
    else:
        xi_note = ""
    try:
        warnings = json.loads(WARNINGS.read_text(encoding="utf-8"))
        warnings = warnings if isinstance(warnings, list) else []
    except (OSError, ValueError):
        warnings = []

    moves = []
    rows = worth_doing(u, rows)
    for r in sorted(rows, key=lambda r: _move_rank_key(r, u)):
        a = r["action"]
        who = max(rivals, key=lambda v: r["d_beat"].get(v, 0.0)) \
            if rivals else ""
        moves.append({
            "label": a.label(names),
            "kind": ("clause" if a.victim
                     else "sell" if a.kind == "sell" else "buy"),
            "buy": names.get(a.buy, a.buy) if a.buy else "",
            "sell": " + ".join(names.get(k, k) for k in a.sell),
            "sell_n": len(a.sell),
            "victim": a.victim,
            "owner": u.view("owner").get(a.buy, "") if a.buy else "",
            "net_pts": r["net_pts"], "d_win": r["d_win"], "net": -a.net,
            "d_pts": r.get("d_pts", 0.0), "helps": r.get("helps", 0.0),
            "pts_lo": r.get("pts_lo", 0.0), "pts_hi": r.get("pts_hi", 0.0),
            "value": r.get("value"),
            "left": u.cash - a.net,
            "answer": (None if r.get("answer") is None
                       else names.get(r["answer"].buy, r["answer"].buy)),
            "p_win_after": round(base.position().get(1, 0.0) + r["d_win"], 3),
            "vs": who, "vs_gain": r["d_beat"].get(who, 0.0) if who else None,
        })
    return {
        "locks_in_h": locks_h,
        "track_record": _track_record(),
        "cash": u.cash,
        "cash_locked": u.locked_cash,
        "squad_value": squad_value(u),
        "jornadas_left": len(u.state.jornadas),
        "acquirable": len(u.view("price")),
        "considered": n_actions,
        "expected_finish": round(base.expected_position(), 2),
        "p_win": round(base.position().get(1, 0.0), 3),
        "band": [lo, hi],
        "moves": moves,
        "sell": [{"name": names.get(k, k), "pos": u.view("pos").get(k, ""),
                  "raises": got}
                 for k, got in u.dead_weight()],
        "ladder": (ladder_data if ladder_data is not None
                  else ladder_rows(u, rows, exp=exp, xi=xi)),
        "bar": u.xi_bar,
        "xi_total": _xi_total(u, u.me, exp=exp),
        "shape": _shape_now(u, xi=xi),
        "rival_best": _rival_best(u, exp=exp),
        "shape_now": fielded_shape(u, xi=xi),
        "xi_note": xi_note,
        "warnings": warnings,
        "standings": [
            {"manager": m, "me": m == u.me,
             "now": u.state.carried.get(m, 0.0), "mean": base.mean(m),
             "lo": base.band(m)[0], "hi": base.band(m)[1],
             "cash": u.cash if m == u.me else u.rival_cash.get(m, 0.0),
             "cash_known": m == u.me,
             "p_above": None if m == u.me else base.beat(m)}
            for m in sorted(u.state.squads, key=lambda m: -base.mean(m))],
    }


def _track_record():
    try:
        import backtest
        return backtest.track_record()
    except Exception:
        return None


PRICE_LOG = "cash_price_log.csv"


def cash_price_history():
    import statistics
    from ffcore.tidy import DECISIONS, read_csv

    seen = []
    for r in read_csv(DECISIONS / PRICE_LOG):
        try:
            seen.append(float(r["places_per_million"]))
        except (TypeError, ValueError, KeyError):
            continue
    return statistics.median(seen) if seen else None


def log_cash_price(measured) -> None:
    from ffcore.tidy import DECISIONS, log_row

    if measured is None:
        return
    log_row(DECISIONS / PRICE_LOG,
           {"measured_at": run_now().strftime("%Y-%m-%dT%H%MZ"),
            "places_per_million": "%.6f" % measured})


def market_rows(u, bands=None) -> list[dict]:
    mine = set(u.state.squads.get(u.me, {}))
    fc = u.player_forecasts
    out = []
    for k, price in u.view("price").items():
        if k in mine:
            continue
        f = fc.get(k, {})
        par, par_lo, par_hi = f.get("par"), None, None
        b = (bands or {}).get(k)
        if b is not None:
            par, par_lo, par_hi, _act, _mean = b
        out.append({
            "key": k, "name": title_name(u.view("name").get(k, k)),
            "pos": u.view("pos").get(k, ""), "price": price,
            "season_pts": f.get("season_pts"), "next_pts": f.get("next_pts"),
            "par": par, "par_lo": par_lo, "par_hi": par_hi,
            "value": value_rate(par, price),
            "simulated": b is not None,
        })
    out.sort(key=lambda r: (r["value"] is None, -(r["value"] or 0.0)))
    return out


def _selftest() -> None:
    import decide
    from ffcore.forecast import Bootstrap
    from ffcore.season import LeagueState, Standings
    from decide import Action, Universe
    from ffcore.crosswalk import Crosswalk, Player
    from ffcore.fixtures import tiny_profile, players_from_flat

    import grading
    _real_current_mae = grading.current_mae
    grading.current_mae = lambda: None  # noqa: E731

    st = Standings(totals={"me": [1000.0, 1200.0, 1400.0, 1600.0],
                           "riv": [1500.0, 1300.0, 1100.0, 900.0]}, me="me")
    u = Universe(state=LeagueState({"me": {}, "riv": {}}, [1, 2], "me",
                                   carried={"me": 17.0, "riv": 23.0}),
                 forecaster=Bootstrap({}, pool=[1, 2, 3]), cash=23.6e6, me="me",
                 players=players_from_flat(
                     name={"yuri": "yuri berchiche",
                          "benat": "benat turrientes"}))

    rows = [{"player_id": "1070", "player_name": "Ionut Radu",
             "player_name_full": "Ionut Andrei Radu"},
            {"player_id": "2464", "player_name": "Pepelu",
             "player_name_full": "José Luis García Vayá"}]
    squad = {"ionut radu": 1, "pepelu": 1}
    for sq, names, lineup, app_ids, want in [
            (squad, {"pepelu": "Pepelu"}, rows, {"1070": "ionut radu"},
             ["ionut radu", "pepelu"]),
            (squad, {}, rows + [{"player_id": "999", "player_name": "Nobody"}],
             {}, []),
            (squad, {}, rows, {"1070": "ionut radu", "2464": "someone else"},
             []),
            ({}, {}, [], {}, [])]:
        xw = Crosswalk({k: Player(k, app_id=a) for a, k in app_ids.items()})
        assert app_fielded(sq, names, lineup, xw) == want, (app_ids, want)

    best = ["gk", "d1", "d2", "d3", "d4", "m1", "m2", "m3", "m4", "m5", "f1"]
    same = xi_change(list(best), best)
    assert same["legal"] and same["in"] == [] and same["out"] == []
    swap = xi_change([k for k in best if k != "m5"] + ["bench1"], best)
    assert swap["in"] == ["m5"] and swap["out"] == ["bench1"], swap
    short_marks = xi_change(best[:10], best)
    assert not short_marks["legal"] and short_marks["marked"] == 10
    assert short_marks["in"] == [] and short_marks["out"] == []
    assert not xi_change([], best)["legal"]

    u.cash = -133023.0
    u.cash = 23.6e6
    u.cash, u.locked_cash = -2637643.0, 5938860.0
    u.cash, u.locked_cash = 23.6e6, 0.0

    rows = [{"action": Action("clause", buy="yuri", sell="benat",
                              cost=20e6, proceeds=5.87e6, victim="riv"),
             "net_pts": 0.433, "d_win": 0.364, "d_beat": {"riv": 0.37},
             "d_pts": 120.0, "pts_lo": 43.3, "pts_hi": 210.0,
             "helps": 0.90, "mean": 1510.0,
             "value": 120.0 / (14.13e6 / 1e6)}]

    sq = {"k": "POR", "d1": "DEF", "d2": "DEF", "d3": "DEF", "d4": "DEF",
          "m1": "MED", "m2": "MED", "m3": "MED", "m4": "MED",
          "f1": "DEL", "f2": "DEL",
          "spare_m": "MED", "spare_k": "POR"}
    val = {k: 5.0 for k in sq}
    val["spare_m"] = 0.4
    val["spare_k"] = 0.2
    u2 = Universe(state=LeagueState({"me": dict(sq), "riv": dict(sq)}, [1, 2],
                                    "me"),
                  forecaster=Bootstrap({1: {k: (v, 1.0) for k, v in val.items()},
                                        2: {k: (v, 1.0) for k, v in val.items()}}),
                  cash=0.0, me="me",
                  players=players_from_flat(
                      pos=dict(sq),
                      proceeds={"spare_m": 7.45e6, "spare_k": 4.73e6,
                               "d1": 9e6},
                      name={"spare_m": "benat turrientes",
                           "spare_k": "alvaro fernandez"}))
    dead = u2.dead_weight()
    assert [k for k, _ in dead] == ["spare_m", "spare_k"], dead
    assert [v for _, v in dead] == [7.45e6, 4.73e6], dead
    assert "d1" not in dict(dead)
    from ffcore.season import best_xi as _bx
    left = {k: v for k, v in sq.items() if k not in dict(dead)}
    assert len(_bx(left, val)) == 11, left

    from dataclasses import replace as _dc_replace
    u_over = _dc_replace(u2, cash=-5e6)
    al_over = alert_lines(u_over, [], ["riv"])
    assert len(al_over) == 1, al_over
    assert "Overdrawn" in al_over[0] and "Benat Turrientes" in al_over[0], \
        al_over
    assert "Alvaro Fernandez" not in al_over[0], al_over
    u_over_big = _dc_replace(u2, cash=-15e6)
    al_big = alert_lines(u_over_big, [], ["riv"])
    assert len(al_big) == 1, al_big
    assert "Benat Turrientes" in al_big[0] and "Alvaro Fernandez" in al_big[0], \
        al_big
    assert "still" in al_big[0] and "short" in al_big[0], al_big
    assert "d1" not in al_big[0]


    _wd_rows = [{"action": Action("buy", buy="yuri", cost=1e6),
                 "d_pts": 5.0, "d_win": 0.01, "net_pts": 5.0, "pts_lo": 0.0,
                 "pts_hi": 9.0, "helps": 0.7, "value": None, "burn": None,
                 "charge": 0.0, "answer": None, "mean": 0.0, "d_beat": {}},
                {"action": Action("buy", buy="benat", cost=1e6),
                 "d_pts": -3.0, "d_win": -0.01, "net_pts": -3.0,
                 "pts_lo": -9.0, "pts_hi": 1.0, "helps": 0.2, "value": None,
                 "burn": None, "charge": 0.0, "answer": None, "mean": 0.0,
                 "d_beat": {}}]
    kept = worth_doing(u2, _wd_rows)
    assert [r["action"].buy for r in kept] == ["yuri"], kept
    import inspect as _ins
    src_main = _ins.getsource(main)
    assert "alert_lines(u, worth_doing(" in src_main, \
        "the phone's headline must be screened by the same rule as the JSON"

    assert bid_lines(u2, []) == [], "no bids, nothing to say"
    u_bid = _dc_replace(u2, cash=3e6, my_bids={"yuri": 8e6, "benat": 2e6},
                        locked_cash=10e6)
    bl = bid_lines(u_bid, [])
    assert len(bl) == 2, bl
    assert bl[0].startswith("**Withdraw 10.00M**"), bl
    assert "Yuri 8.00M" in bl[0] and "Benat 2.00M" in bl[0], bl
    assert "them" in bl[0], bl
    assert "**10.00M bid, 3.00M in hand**" in bl[1], bl
    assert "7.00M short" in bl[1], bl

    funded = [{"action": Action("swap", buy="yuri", sell=("d1",),
                                cost=8e6, proceeds=7e6)}]
    kept = bid_lines(u_bid, funded)
    assert len(kept) == 2, kept
    assert "Yuri" not in kept[0], "an endorsed, funded bid is not withdrawn"
    assert kept[0].startswith("**Withdraw 2.00M**") and "him" in kept[0], kept

    broke = bid_lines(u_bid, [{"action": Action("buy", buy="yuri", cost=8e6)}])
    assert broke[0].startswith("**Withdraw 10.00M**"), broke
    assert "Yuri 8.00M" in broke[0], broke

    covered = _dc_replace(u2, cash=12e6, my_bids={"yuri": 8e6},
                          locked_cash=8e6)
    cl = bid_lines(covered, [{"action": Action("buy", buy="yuri", cost=8e6)}])
    assert cl == [], "endorsed and affordable is not news"

    pair = _dc_replace(u2, cash=10e6, my_bids={"yuri": 8e6, "benat": 7e6},
                       locked_cash=15e6)
    pl = bid_lines(pair, [{"action": Action("buy", buy="yuri", cost=8e6)},
                          {"action": Action("buy", buy="benat", cost=7e6)}])
    assert pl[0].startswith("**Withdraw 7.00M**"), pl
    assert "Benat 7.00M" in pl[0] and "Yuri" not in pl[0], pl

    both = alert_lines(_dc_replace(u_over, my_bids={"yuri": 8e6},
                                   locked_cash=8e6), [], ["riv"])
    assert len(both) == 3, both
    assert "Withdraw" in both[0] and "Overdrawn" in both[-1], both

    u2.part_played = {1: {"alaves"}}
    u2.forecaster = Bootstrap({1: {k: (v, 1.0) for k, v in val.items()},
                               2: {k: ((9.0 if k == "spare_m" else v), 1.0)
                                   for k, v in val.items()}})
    assert "spare_m" not in dict(u2.dead_weight()), \
        "a man who starts in a round still ahead is not spare"
    u2.forecaster = Bootstrap(
        {1: {k: ((9.0 if k == "spare_m" else v), 1.0) for k, v in val.items()},
         2: {k: (v, 1.0) for k, v in val.items()}})
    assert "spare_m" in dict(u2.dead_weight()), \
        "a man who starts only in a locked round is still spare"
    u2.state.jornadas = [1]
    assert "spare_m" not in dict(u2.dead_weight())
    u2.state.jornadas, u2.part_played = [1, 2], {}


    from ffcore.profile import mk_profile
    cu_sq = {"me": {"me_a": "MED"}}
    cu_per = {1: {"me_a": (2.0, 1.0), "cheap": (3.0, 1.0), "rich": (8.0, 1.0)},
              2: {"me_a": (2.0, 1.0), "cheap": (3.0, 1.0), "rich": (8.0, 1.0)}}
    cu = Universe(
        state=LeagueState(cu_sq, [1, 2], "me"),
        forecaster=Bootstrap(cu_per), cash=0.0, me="me",
        players={"me_a": mk_profile(5.0, price=3e6),
                 "cheap": mk_profile(5.0, price=2e6, name="cheap"),
                 "rich": mk_profile(5.0, price=20e6, name="rich")})
    mr = market_rows(cu)
    assert [r["key"] for r in mr] == ["cheap", "rich"], mr
    assert [(r["season_pts"], r["par"], r["par_lo"]) for r in mr] == [
        (6.0, 2.0, None), (16.0, 12.0, None)], mr
    mr2 = {r["key"]: r for r in market_rows(cu, bands={
        "cheap": (2.5, 1.0, 4.0, Action("buy", buy="cheap", cost=2e6), 2.4)})}
    assert (mr2["cheap"]["par"], mr2["cheap"]["par_lo"], mr2["cheap"]["par_hi"],
            mr2["cheap"]["simulated"], mr2["rich"]["simulated"]) == (
        2.5, 1.0, 4.0, True, False), mr2

    d = payload(u, rows, st, ["riv"], locks_h=41.1, n_actions=132)
    assert d["expected_finish"] == 1.5 and d["p_win"] == 0.5, d
    st3 = Standings(totals={"me": [1000.0, 1200.0, 1000.0],
                            "riv": [1500.0, 900.0, 1500.0]}, me="me")
    d3 = payload(u, rows, st3, ["riv"])
    assert d3["p_win"] == round(1 / 3, 3) == 0.333, d3["p_win"]
    assert len(str(d3["p_win"]).split(".")[-1]) <= 3, d3["p_win"]
    assert d["band"] == [1060.0, 1540.0], d
    assert d["locks_in_h"] == 41.1 and d["cash"] == 23.6e6
    assert d["cash_locked"] == 0.0, d
    u.locked_cash = 2.1e6
    assert payload(u, rows, st, ["riv"])["cash_locked"] == 2.1e6
    u.locked_cash = 0.0
    m = d["moves"][0]
    assert m["label"] == "clause Yuri Berchiche from riv · sell Benat Turrientes"
    assert m["buy"] == "Yuri Berchiche" and m["sell"] == "Benat Turrientes"
    two = payload(u, [{**rows[0],
                       "action": Action("swap", buy="yuri",
                                        sell=("benat", "yuri"),
                                        cost=1e6, proceeds=2e6)}],
                  st, ["riv"])["moves"][0]
    assert two["sell"] == "Benat Turrientes + Yuri Berchiche", two
    assert two["sell_n"] == 2 and m["sell_n"] == 1, (two, m)
    assert m["victim"] == "riv" and m["kind"] == "clause"
    assert m["net_pts"] == 0.433 and m["d_win"] == 0.364
    assert abs(m["p_win_after"] - (0.5 + 0.364)) < 1e-9, m
    assert m["left"] == 23.6e6 - (20e6 - 5.87e6), m
    assert m["answer"] is None, m
    withans = payload(u, [{**rows[0],
                           "answer": Action("clause", buy="yuri",
                                            victim="me")}],
                      st, ["riv"])["moves"][0]
    assert withans["answer"] == "Yuri Berchiche", withans
    assert m["net"] == -(20e6 - 5.87e6), m
    assert m["vs"] == "riv" and m["vs_gain"] == 0.37
    assert [r["manager"] for r in d["standings"]] == ["me", "riv"], d
    assert d["standings"][0]["me"] is True
    assert d["standings"][1]["p_above"] == 0.5

    row_a = {"action": Action("buy", buy="A", cost=40e6), "net_pts": 0.40,
             "d_win": 0.0, "d_beat": {}, "value": 2.0,
             "d_pts": 40.0, "pts_lo": -30.0}
    row_b = {"action": Action("buy", buy="B", cost=1e6), "net_pts": 0.15,
             "d_win": 0.0, "d_beat": {}, "value": 50.0,
             "d_pts": 15.0, "pts_lo": 10.0}
    row_c = {"action": Action("buy", buy="C", cost=1e4), "net_pts": 0.02,
             "d_win": 0.0, "d_beat": {}, "value": 500.0,
             "d_pts": 2.0, "pts_lo": -80.0}
    order = [m["buy"] for m in payload(u, [row_a, row_b, row_c], st,
                                       ["riv"])["moves"]]
    assert order == ["A", "B", "C"], order
    row_win = {"action": Action("buy", buy="W", cost=40e6), "net_pts": 0.05,
              "d_win": 0.10, "d_beat": {}, "value": 1.0,
              "d_pts": 5.0, "pts_lo": -50.0}
    order_win = [m["buy"] for m in payload(u, [row_a, row_win, row_b], st,
                                           ["riv"])["moves"]]
    assert order_win == ["A", "B", "W"], order_win

    al = alert_lines(u, rows, ["riv"])
    assert len(al) == 1 and "Yuri Berchiche" in al[0] and "+36%" in al[0], al
    assert "€14.1M" in al[0], al
    assert alert_lines(u, [], ["riv"]) == []
    flat = [{**rows[0], "net_pts": 0.0, "d_win": 0.0, "d_pts": 0.0}]
    assert worth_doing(u, flat) == [], "the screen drops it"
    assert alert_lines(u, worth_doing(u, flat), ["riv"]) == []

    cheap_ok = {**rows[0],
                "action": Action("buy", buy="cheap", cost=2e6, proceeds=0.0),
                "net_pts": 0.40, "d_win": 0.30, "d_pts": 110.4, "pts_lo": 40.0}
    assert best_move(u, [rows[0], cheap_ok], ["riv"]) == (cheap_ok, False), \
        "a move keeping 90%+ of the best gain for a fraction of the cost wins"
    cheap_bad = {**rows[0],
                 "action": Action("buy", buy="cheap", cost=2e6, proceeds=0.0),
                 "net_pts": 0.30, "d_win": 0.20, "d_pts": 82.8, "pts_lo": 30.0}
    assert best_move(u, [rows[0], cheap_bad], ["riv"]) == (rows[0], False), \
        "a cheaper move that gives up too much of the gain does not win"
    free = {**rows[0],
            "action": Action("sell", sell=("dead",), cost=0.0, proceeds=1e6),
            "net_pts": 0.40, "d_win": 0.30, "d_pts": 110.4, "pts_lo": 40.0}
    assert best_move(u, [rows[0], free], ["riv"]) == (free, False), \
        "a self-funding move within reach of the best gain wins outright"
    winonly = {**rows[0], "action": Action("buy", buy="x", cost=1e6),
              "net_pts": 0.0, "d_win": 0.05, "d_pts": 0.0, "pts_lo": 0.0}
    assert worth_doing(u, [winonly]) == [], \
        "a move that gains no points is screened out before best_move sees it"
    assert best_move(u, [], ["riv"]) == (None, False), "nothing left, nothing said"

    safer = {**rows[0],
             "action": Action("clause", buy="safer", sell="benat",
                              cost=20e6, proceeds=5.87e6, victim="riv"),
             "pts_lo": 80.0}
    riskier = {**rows[0],
               "action": Action("clause", buy="riskier", sell="benat",
                                cost=20e6, proceeds=5.87e6, victim="riv"),
               "pts_lo": -10.0}
    best1, _ = best_move(u, [safer, riskier], ["riv"])
    assert best1["action"].buy == "safer", best1
    best2, _ = best_move(u, [riskier, safer], ["riv"])
    assert best2["action"].buy == "riskier", best2

    listed_big = {**rows[0],
                  "action": Action("buy", buy="listed_target", cost=30e6),
                  "net_pts": 0.50, "d_win": 0.40, "pts_lo": 50.0}
    u_route = Universe(
        state=LeagueState({"me": {}, "riv": {}}, [1, 2], "me"),
        forecaster=Bootstrap({}), cash=0.0, me="me",
        players={"listed_target": tiny_profile("listed_target",
                                               route="listed")})
    assert best_move(u_route, [listed_big], ["riv"]) == (listed_big, True)
    assert best_move(u_route, [listed_big, rows[0]], ["riv"]) == (rows[0], False), \
        "a reliable move beats a bigger listed one outright"

    assert best_move(u_route, [cheap_ok, rows[0]], ["riv"]) == (cheap_ok, False), \
        "order must not change the value-for-money winner"
    assert best_move(u_route, [cheap_bad, rows[0]], ["riv"]) == (rows[0], False), \
        "order must not change which move keeps too little of the gain"
    assert best_move(u_route, [free, rows[0]], ["riv"]) == (free, False), \
        "order must not change a self-funding winner"
    assert best_move(u_route, [rows[0], listed_big], ["riv"]) == (rows[0], False), \
        "order must not change reliable-beats-listed"

    from ffcore.profile import (PlayerProfile,
                                PlayerCurrent, PlayerHistory, PlayerDerived)

    steady_row = {"action": Action("buy", buy="steady", cost=5e6),
                 "net_pts": 0.40, "d_win": 0.0, "d_beat": {}, "value": 8.0,
                 "d_pts": 40.0, "pts_lo": 10.0, "pts_hi": 70.0, "helps": 0.80}
    dud_row = {"action": Action("buy", buy="dud", cost=5e6),
              "net_pts": 0.20, "d_win": 0.0, "d_beat": {}, "value": 4.0,
              "d_pts": 20.0, "pts_lo": 5.0, "pts_hi": 35.0, "helps": 0.60}
    maverick_row = {"action": Action("buy", buy="maverick", cost=5e6),
                   "net_pts": 0.10, "d_win": 0.0, "d_beat": {}, "value": 2.0,
                   "d_pts": 10.0, "pts_lo": -50.0, "pts_hi": 260.0,
                   "helps": 0.55}
    riv_row = {"action": Action("clause", buy="rivals", cost=5e6),
              "net_pts": 0.60, "d_win": 0.0, "d_beat": {}, "value": 12.0,
              "d_pts": 60.0, "pts_lo": 20.0, "pts_hi": 90.0, "helps": 0.90,
              "burn": 1.2e6}
    wish_row = {"action": Action("buy", buy="wished", cost=5e6),
               "net_pts": 0.50, "d_win": 0.0, "d_beat": {}, "value": 10.0,
               "d_pts": 50.0, "pts_lo": 15.0, "pts_hi": 80.0, "helps": 0.85}
    uc_price = {"steady": 5e6, "maverick": 5e6, "dud": 5e6, "rivals": 5e6,
               "wished": 5e6}
    uc_owner = {"rivals": "riv", "wished": "riv"}
    uc_route = {"rivals": "clause", "wished": "listed"}
    uc_value = {"steady": 5e6, "rivals": 3.8e6}
    uc_owned = Universe(
        state=LeagueState({"me": {}, "riv": {}}, [1, 2], "me"),
        forecaster=Bootstrap(
            {j: {"steady": (5.0, 1.0), "maverick": (4.0, 1.0),
                "dud": (3.0, 1.0), "rivals": (7.0, 1.0),
                "wished": (6.5, 1.0)} for j in (1, 2)}),
        cash=10e6, me="me",
        players={k: PlayerProfile(
            identity=Player(player_id=k, name=k),
            current=PlayerCurrent(
                pos="MED", price=uc_price.get(k), owner=uc_owner.get(k),
                route=uc_route.get(k), value=uc_value.get(k),
                listed=k in uc_price),
            history=PlayerHistory(), derived=PlayerDerived(pj=5.0))
            for k in ("steady", "dud", "maverick", "rivals", "wished")})
    all_rows = [steady_row, dud_row, maverick_row, riv_row, wish_row]
    owned_lad = ladder_rows(uc_owned, all_rows)
    steady_cell = next(r for r in owned_lad if r["name"].lower() == "steady")
    assert steady_cell["par"] == 4.0, steady_cell
    by_group = {}
    for r in owned_lad:
        by_group.setdefault(r["group"], []).append(r["name"].lower())
    assert by_group["buy"] == ["steady", "dud", "maverick"], by_group

    offered = _dc_replace(
        uc_owned,
        state=LeagueState({"me": {"steady": "MED", "dud": "MED"}, "riv": {}},
                          [1, 2], "me"),
        received_offers={"steady": 6.0e6, "dud": 1.0e6})
    got = [r for r in ladder_rows(offered, all_rows) if r["group"] == "offer"]
    assert [r["name"].lower() for r in got] == ["steady", "dud"], got
    assert got[0]["offer"] == 6.0e6, got[0]
    assert got[0]["worth"] == 5.0e6, ("market value comes along, because a "
                                      "bid means nothing without it", got[0])
    assert got[0]["going"] is not None and got[0]["going"] >= 5.0e6, got[0]
    assert "bought" not in got[0], ("purchase price is a sunk cost",
                                    got[0])
    assert got[1]["worth"] is None, got[1]
    assert all(r.get("offer") for r in got), got
    assert not [r for r in ladder_rows(uc_owned, all_rows)
                if r["group"] == "offer"], "no offers, no section"
    assert by_group["raid"] == ["rivals"], by_group
    assert "wished" not in [n for names in by_group.values() for n in names], \
        by_group

    steady_cell = next(r for r in owned_lad if r["name"].lower() == "steady")
    assert steady_cell["market"] == 5e6 and not steady_cell["premium"], \
        steady_cell
    rivals_cell = next(r for r in owned_lad if r["name"].lower() == "rivals")
    assert rivals_cell["market"] == 3.8e6, rivals_cell
    assert rivals_cell["premium"] == 1.2e6, rivals_cell

    slot_players = {"k": "POR", "d": "DEF", "m": "MED", "f": "DEL"}
    u_slot = Universe(
        state=LeagueState({"me": {}}, [1], "me"), forecaster=Bootstrap({}),
        cash=0.0, me="me",
        players={key: tiny_profile(key, pos=pos)
                for key, pos in slot_players.items()})
    assert by_slot(u_slot, ["m", "f", "k", "d"], exp={}) == \
        ["k", "d", "m", "f"]

    shape_players = {"a": "POR", "b": "DEF", "c": "DEF", "d": "DEF",
                     "e": "DEF", "f": "MED", "g": "MED", "h": "MED",
                     "i": "MED", "j": "DEL", "k": "DEL"}
    u_shape = Universe(
        state=LeagueState({"me": {}}, [1], "me"), forecaster=Bootstrap({}),
        cash=0.0, me="me",
        players={key: tiny_profile(key, pos=pos)
                for key, pos in shape_players.items()})
    assert shape(u_shape, list("abcdefghijk")) == "4-4-2"
    assert shape(u_shape, ["a", "b", "c", "d", "f", "g"]) == "3-2-0"
    assert shape(u_shape, []) == "0-0-0"




    from decide import Universe as U2
    from ffcore.season import LeagueState as LS2

    many_j = list(range(1, 11))
    sqb = {"k": "POR", **{f"d{i}": "DEF" for i in range(1, 5)},
          "star": "MED", **{f"m{i}": "MED" for i in range(1, 5)},
          "f1": "DEL", "dead": "MED"}
    riv = {"rk": "POR", **{f"rd{i}": "DEF" for i in range(1, 5)},
          **{f"rm{i}": "MED" for i in range(1, 5)}, "rf1": "DEL"}
    perb = {j: {**{k: (3.0, 0.9) for k in sqb if k not in ("star", "dead")},
               "star": (6.0, 0.9), "dead": (3.0, 0.05), "cand": (5.0, 0.8),
               **{k: (3.0, 0.9) for k in riv}} for j in many_j}
    ub = U2(state=LS2({"me": dict(sqb), "riv": dict(riv)}, many_j, "me"),
           forecaster=Bootstrap(perb, matches={k: 30 for k in
                                               (*sqb, *riv, "cand")}),
           cash=10e6, me="me",
           players=players_from_flat(
               pos={**{k: v for k, v in sqb.items()}, "cand": "MED"},
               price={"cand": 5e6},
               proceeds={"dead": 1e6, "star": 20e6}))
    asked = band_acts(ub)
    keys = {k for k, _a in asked}
    assert keys == {*sqb, "cand"}, keys
    by_key = dict(asked)
    for k in sqb:
        assert by_key[k].buy == "" and by_key[k].sell == (k,), by_key[k]
    assert by_key["cand"].buy == "cand" and by_key["cand"].sell == (), \
        by_key["cand"]

    _rows, baseb, _lam, bands = ub.rank([], extra=asked)
    assert set(bands) == {*sqb, "cand"}, sorted(bands)
    for key in bands:
        med, lo, hi, _act, _mean = bands[key]
        assert lo <= med <= hi, (key, bands[key])
    assert bands["star"][0] < -20, bands["star"]
    assert -5 < bands["dead"][0] < 5, bands["dead"]
    assert bands["dead"][3].buy == "" and bands["dead"][3].sell == ("dead",)
    assert bands["cand"][0] > 0, bands["cand"]
    assert ub.rank([], extra=[])[3] == {}

    sell_json = payload(ub, [], baseb, ["riv"])["sell"]
    by_name = {r["name"]: r for r in sell_json}
    assert by_name["Dead"]["raises"] == 1e6, by_name["Dead"]
    assert "bought" not in by_name["Dead"], by_name["Dead"]
    assert "star" not in by_name, by_name

    buy_cand = decide.Action("buy", buy="cand", cost=5e6)
    rows2, _b2, _l2, bands2 = ub.rank([buy_cand], extra=asked)
    assert [r for r in rows2 if r["action"].buy == "cand"], rows2
    assert "cand" not in bands2, sorted(bands2)

    grading.current_mae = _real_current_mae
    import backtest
    real_tr = backtest.track_record
    backtest.track_record = lambda *a, **k: (_ for _ in ()).throw(RuntimeError)
    try:
        assert _track_record() is None
    finally:
        backtest.track_record = real_tr

    print("sim self-test OK (221 cases)")


def main() -> None:
    import decide
    from ffcore.tidy import load_deadline

    REPORTS.mkdir(exist_ok=True)
    deadline = load_deadline()
    locks_h = None if deadline is None else (
        deadline - run_now()).total_seconds() / 3600

    u = decide.load()
    if len(u.state.squads) < 2 or not u.state.jornadas:
        print("sim: nothing to simulate (%d squads, %d jornadas left)"
              % (len(u.state.squads), len(u.state.jornadas)))
        return

    acts = u.candidates(budget=float("inf"))
    smoothed = cash_price_history()
    xi_exp, xi = u.current_xi
    bar_acts = band_acts(u, exp=xi_exp, xi=xi)
    bar_keys = {k for k, _ in bar_acts}
    mine = u.state.squads.get(u.me, {})
    market_acts = [(k, decide.Action("buy", buy=k,
                                     cost=u.view("price").get(k, 0.0)))
                  for k in u.view("price") if k not in mine]
    extra_acts = bar_acts + [t for t in market_acts
                             if t[0] not in bar_keys]
    rows, base, measured, bands = u.rank(
        acts, price=smoothed, extra=extra_acts)
    log_cash_price(measured)
    rivals = [m for m in u.state.squads if m != u.me]
    ladder_data = ladder_rows(u, rows, bands, exp=xi_exp, xi=xi)

    (REPORTS / "decisions.json").write_text(json.dumps({
        "generated_at": run_now()
                          .strftime("%Y-%m-%dT%H:%MZ"),
        **payload(u, rows, base, rivals, locks_h, len(acts),
                  ladder_data=ladder_data, exp=xi_exp, xi=xi),
        "market": market_rows(u, bands),
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("wrote %s" % (REPORTS / "decisions.json"))

    lines = alert_lines(u, worth_doing(u, rows), rivals)
    if lines or ALERTS.exists():
        prev = [ln for ln in
                (ALERTS.read_text(encoding="utf-8").splitlines()
                 if ALERTS.exists() else [])
                if ln.startswith("- ")]
        body, seen = [], set()
        for ln in ["- " + ln for ln in lines] + prev:
            if ln not in seen:
                seen.add(ln)
                body.append(ln)
        if body:
            ALERTS.parent.mkdir(parents=True, exist_ok=True)
            write_lines(ALERTS, ["# Alerts — %s" % shown(), ""] + body)
        else:
            ALERTS.unlink(missing_ok=True)
    print("%d alert(s) from the simulation" % len(lines))


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        raise SystemExit(0)
    main()
