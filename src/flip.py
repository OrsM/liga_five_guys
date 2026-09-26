from __future__ import annotations

import bisect
import csv
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from itertools import accumulate
from statistics import mean, median

def steps(rows: list[dict]) -> dict[str, list[tuple[str, float]]]:
    vals: dict[str, list[tuple[str, float]]] = {}
    for r in sorted(rows, key=lambda r: r["observed_at"]):
        try:
            vals.setdefault(r["ff_id"], []).append(
                (r["observed_at"][:10], float(r["value"])))
        except (KeyError, ValueError):
            pass
    return {k: [(b[0], 100 * (b[1] / a[1] - 1)) for a, b in zip(v, v[1:])
                if a[1] > 0] for k, v in vals.items()}


def belief(window: list[tuple], h: int) -> dict | None:
    days = {day for _, _, day in window}
    if len(days) < 2:
        return None
    return {"mean": mean(out for _, out, _ in window), "n": len(window),
            "days": len(days)}


class Outlook:

    def __init__(self, by_player: dict[str, list[tuple[str, float]]]):
        self.hmax = max(1, int(median(len(s) for s in by_player.values())) // 4) \
            if by_player else 1
        self.obs: dict[int, list[tuple]] = {h: [] for h in range(1, self.hmax + 1)}
        for s in by_player.values():
            pref = [1.0, *accumulate((1 + c / 100 for _, c in s),
                                     lambda a, b: a * b)]
            for i, (day, step) in enumerate(s):
                for h in range(1, min(self.hmax, len(s) - 1 - i) + 1):
                    self.obs[h].append((step, 100 * (pref[i + 1 + h] / pref[i + 1]
                                                     - 1), day))
        for v in self.obs.values():
            v.sort()
        self.keys = {h: [o[0] for o in v] for h, v in self.obs.items()}

    def near(self, step: float, h: int) -> list[tuple]:
        v, ks = self.obs[h], self.keys[h]
        k = max(3, round(math.sqrt(len(v))))
        lo = hi = bisect.bisect_left(ks, step)
        while hi - lo < k and (lo > 0 or hi < len(v)):
            if lo > 0 and (hi >= len(v) or step - ks[lo - 1] <= ks[hi] - step):
                lo -= 1
            else:
                hi += 1
        return v[lo:hi]

    def best(self, step: float | None, offer: float, premium: float) -> dict | None:
        if step is None or not self.obs:
            return None
        best = None
        for h in self.obs:
            bel = belief(self.near(step, h), h)
            if bel is None:
                continue
            net = ((1 + bel["mean"] / 100) * offer / premium - 1) / h
            if best is None or net > best["net"]:
                best = {**bel, "h": h, "net": net}
        return best


def offer_ratios(offers: list[dict], teams: list[dict], value_at) -> list[float]:
    who = {t["player_team_id"]: t["player_name"] for t in teams
           if t.get("player_team_id")}
    seen, out = set(), []
    for o in offers:
        if not o.get("offer_id") or o["offer_id"] in seen:
            continue
        seen.add(o["offer_id"])
        v = value_at(who.get(o["player_team_id"], ""), o["created_at"])
        if v and float(o["money"] or 0) > 0:
            out.append(float(o["money"]) / v)
    return out


def auction_ratios(listings: list[dict], buys: list[dict]) -> list[float]:
    ends = {}
    for r in listings:
        if r.get("seller") != "marketPlayerLeague" or not r.get("expires_at"):
            continue
        k = (r["player_id"], datetime.fromisoformat(r["expires_at"])
             .astimezone(timezone.utc))
        if k not in ends or r["observed_at"] > ends[k]["observed_at"]:
            ends[k] = r
    out = []
    for b in buys:
        at = datetime.fromisoformat(b["at"]).astimezone(timezone.utc)
        for (pid, close), r in ends.items():
            if pid == b["player_id"] and abs((at - close).total_seconds()) < 600 \
                    and float(r["sale_price"] or 0) > 0:
                out.append(float(b["amount"]) / float(r["sale_price"]))
                break
    return out


def report_view(ladder: list[dict], name_key) -> dict:
    verdict = {}
    for r in ladder:
        k = name_key(r["name"])
        if r["where"] == "yours" and k \
                and r["group"] in ("sell", "keep", "out", "in", "offer") \
                and r.get("pts_mean") is not None:
            verdict[k] = (r["group"], r["pts_mean"])
    return verdict


def money_rate(moves: list[dict]) -> float:
    rates = [m["value"] for m in moves if m.get("value") is not None]
    return median(rates) if rates else 0.0


def reserve(moves: list[dict]) -> float:
    return max(0.0, -moves[0]["net"]) if moves else 0.0


def _belief(model: dict, last: dict[str, float], key: str) -> dict | None:
    return model["outlook"].best(last.get(key), model["offer"], model["premium"])


def _hold_value(value: float, bel: dict, model: dict) -> float:
    return value * (1 + bel["mean"] / 100) * model["offer"]


def _cost_pts(group: str, exp_pts: float) -> float:
    return 0.0 if group == "sell" else max(0.0, -exp_pts)


def picks(listings: list[dict], last: dict[str, float], model: dict,
          cash: float) -> list[dict]:
    out = []
    for l in listings:
        bel = _belief(model, last, l["key"])
        if bel is None:
            continue
        leave = _hold_value(l["value"], bel, model)
        pay = l["ask"] * model["premium"]
        if leave <= pay:
            continue
        out.append({**l, "last": last[l["key"]], "h": bel["h"], "n": bel["n"],
                    "days": bel["days"], "drift": bel["mean"], "gain": leave - pay,
                    "pay": pay, "max_bid": leave,
                    "fits": pay <= cash, "short": max(0.0, pay - cash),
                    "reason": {"code": "rising", "last": last[l["key"]],
                               "drift": bel["mean"], "h": bel["h"], "n": bel["n"],
                               "days": bel["days"], "ask": l["ask"], "pay": pay}})
    return sorted(out, key=lambda x: -x["gain"] / x["pay"])


def sells(bench: list[dict], last: dict[str, float], offers: dict[str, float],
          model: dict, verdict: dict, rate: float) -> tuple[list[dict], list[dict]]:
    out, held = [], []
    for p in bench:
        v = verdict.get(p["key"])
        bel = _belief(model, last, p["key"])
        if v is None or bel is None:
            continue
        group, exp_pts = v
        now = offers.get(p["key"]) or p["value"] * model["offer"]
        hold = _hold_value(p["value"], bel, model)
        gain = now - hold
        if gain <= 0:
            continue
        cost = _cost_pts(group, exp_pts)
        benefit = rate * gain / 1e6
        back = rate * p["value"] * (model["premium"] - 1) / 1e6
        sale = benefit >= cost + back
        row = {**p, "offer": offers.get(p["key"]), "last": last.get(p["key"]),
               "gain": gain, "cost_pts": cost, "benefit_pts": benefit,
               "reason": {"code": "sale" if sale else "keep",
                          "now": now, "hold": hold, "h": bel["h"],
                          "offered": offers.get(p["key"]) is not None,
                          "cost": cost, "benefit": benefit}}
        (out if sale else held).append(row)
    return out, held


def fund(offers: dict[str, float], mine: dict, names: dict, xi: set,
        verdict: dict, rate: float, value: dict, last: dict, model: dict,
        need: float = 0.0) -> list[dict]:
    out = []
    for k, offer in offers.items():
        if k not in mine:
            continue
        v = verdict.get(k)
        if v is None:
            continue
        group, exp_pts = v
        cost = _cost_pts(group, exp_pts)
        benefit = rate * offer / 1e6
        net = cost - benefit
        bel = _belief(model, last, k)
        money_now, hold, h = None, None, None
        if bel is not None and value.get(k):
            hold = _hold_value(value[k], bel, model)
            money_now, h = offer - hold, bel["h"]
        out.append({"key": k, "name": names.get(k, k), "offer": offer,
                    "cost_pts": cost, "benefit_pts": benefit, "net_pts": net,
                    "starter": k in xi, "money_now": money_now,
                    "reason": {"code": "fund", "offer": offer, "cost": cost,
                               "benefit": benefit, "net": net,
                               "starter": k in xi, "money_now": money_now,
                               "hold": hold, "h": h}})
    out.sort(key=lambda p: (p["net_pts"], -p["offer"]))
    running = 0.0
    for p in out:
        running += p["offer"]
        p["running"] = running
    if need > 0:
        covered = next((i for i, p in enumerate(out) if p["running"] >= need),
                       len(out) - 1)
        out = out[:covered + 2]
    return out


def _m(v: float) -> str:
    from ffcore.parse import fmt_money
    return fmt_money(v)


def _p(v: float) -> str:
    return "%+.1f%%" % v


def _pts(v: float) -> str:
    return "%.1f" % v if v < 10 else "%.0f" % v


def say(r: dict) -> str:
    c = r["code"]
    if c == "rising":
        return ("last update %s; the history says %s over the next %d "
                "updates (%d similar cases on %d different days); ask %s, "
                "expect to pay ~%s"
                % (_p(r["last"]), _p(r["drift"]), r["h"], r["n"], r["days"],
                   _m(r["ask"]), _m(r["pay"])))
    if c in ("sale", "keep"):
        how = ("the app offers %s" % _m(r["now"]) if r["offered"]
               else "an offer would bring ~%s" % _m(r["now"]))
        more = _m(r["now"] - r["hold"])
        if c == "sale":
            return ("%s against ~%s expected from holding %d updates: %s more, "
                    "worth %s season points at what the report's buys return, "
                    "for the %s he is expected to cost the season"
                    % (how, _m(r["hold"]), r["h"], more,
                       _pts(r["benefit"]), _pts(r["cost"])))
        return ("%s against ~%s expected from holding %d updates, but the %s "
                "gained is worth %s season points and he is expected to add "
                "%s -- the report keeps him"
                % (how, _m(r["hold"]), r["h"], more,
                   _pts(r["benefit"]), _pts(r["cost"])))
    if c == "fund":
        where = "currently in your eleven" if r["starter"] else "on the bench"
        if r["net"] <= 0:
            verdict = ("costs nothing net -- the cash outearns him elsewhere"
                      if r["cost"] > 0 else
                      "free to take, the report already has him leaving")
        else:
            verdict = "a net %s season points, cash included" % _pts(r["net"])
        if r["money_now"] is None:
            timing = ""
        elif r["money_now"] >= 0:
            timing = "; the offer beats waiting %d updates by %s" \
                % (r["h"], _m(r["money_now"]))
        else:
            timing = ("; the price is trending up -- waiting %d updates is "
                     "expected to bring ~%s more" % (r["h"], _m(-r["money_now"])))
        return "%s, %s%s; %s" % (_m(r["offer"]), where, timing, verdict)
    raise ValueError("no wording for reason code %r" % c)


def present(out: dict) -> dict:
    def row(p, right):
        return {"name": p["name"], "detail": say(p["reason"]), "right": right}

    buys = [row(p, ["≤ " + _m(p["max_bid"]),
                    "bid" if p["fits"] else "bid; needs %s more" % _m(p["short"])])
            for p in out["picks"]]
    sold = [row(p, [_m(p["value"]),
                    "offer " + _m(p["offer"]) if p["offer"] else ""])
            for p in out["sells"]]
    held = [row(p, [_m(p["value"]), ""]) for p in out["held"]]
    funded = [row(p, [_m(p["offer"]),
                      "net %s pts" % _pts(p["net_pts"])
                      if p["net_pts"] > 0 else "a net gain"])
             for p in out["fund"]]
    doable = [p for p in out["picks"] if p["fits"]]
    ping = ("\nBuy: " + ", ".join("%s (bid up to %s)" % (p["name"], _m(p["max_bid"]))
                                  for p in doable[:2]) if doable else "")
    ping += ("\nSell: " + ", ".join(p["name"] for p in out["sells"][:3])
             if sold else "")
    hold = ("; %s held back for the report's top move" % _m(out["reserve"])
            if out["reserve"] > 0 else "")
    sections = [
        {"label": "BUY — free market", "tone": "buy", "rows": buys,
         "empty": "Nothing on the free market is expected to beat the cost "
                  "of tying the cash up."},
        {"label": "SELL — not starting", "tone": "sell", "rows": sold},
        {"label": "HELD BACK — the report keeps them", "tone": "held",
         "rows": held}]
    if funded:
        sections.append({
            "label": "FUND — %s short, not a recommendation: %d option(s) "
                     "that would cover it, cheapest first"
                     % (_m(out["fund_need"]), len(funded)),
            "tone": "fund", "rows": funded})
    return {
        "heading": "MARKET",
        "summary": "%s to spend%s" % (_m(out["spendable"]), hold),
        "sections": sections,
        "ping": ping}


LOG = ["day", "run_at", "action", "key", "name", "ask", "value", "offer",
       "last", "horizon", "max_bid", "cash", "why"]


def record(path, out: dict, now) -> int:
    close = now.replace(hour=22, minute=24, second=0, microsecond=0)
    day = (close if now < close else close + timedelta(days=1)).strftime("%Y-%m-%d")
    keep = []
    if path.exists():
        with open(path, newline="", encoding="utf-8") as fh:
            keep = [r for r in csv.DictReader(fh) if r["day"] != day]
    base = {"day": day, "run_at": now.strftime("%Y-%m-%dT%H:%M"),
            "cash": round(out["cash"])}
    new = [{**base, "action": "BUY", "key": p["key"], "name": p["name"],
            "ask": round(p["ask"]), "value": round(p["value"]), "last": p["last"],
            "horizon": p["h"], "max_bid": round(p["max_bid"]),
            "why": say(p["reason"])} for p in out["picks"]]
    new += [{**base, "action": "SELL", "key": p["key"], "name": p["name"],
             "value": round(p["value"]), "offer": round(p["offer"] or 0),
             "last": p["last"], "horizon": p["reason"]["h"],
             "why": say(p["reason"])} for p in out["sells"]]
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=LOG, restval="", extrasaction="ignore")
        w.writeheader()
        w.writerows(keep + new)
    return len(new)


def recently(path, now) -> set[tuple[str, str]]:
    if not path.exists():
        return set()
    today = datetime.strptime(now.strftime("%Y-%m-%d"), "%Y-%m-%d")
    with open(path, newline="", encoding="utf-8") as fh:
        return {(r["key"], r["action"]) for r in csv.DictReader(fh)
                if r.get("horizon") and datetime.strptime(r["day"], "%Y-%m-%d")
                < today <= datetime.strptime(r["day"], "%Y-%m-%d")
                + timedelta(days=int(float(r["horizon"])))}


def main() -> None:
    import decide
    from ffcore.text import norm
    from ffcore.tidy import (DECISIONS, MADRID, REPORTS, TIDY, Market,
                             load_market_frozen, read_csv, run_now)

    u = decide.load()
    rows = read_csv(TIDY / "market.csv")
    by_player = steps(rows)
    mk = Market(load_market_frozen())
    value_at = lambda name, when: (lambda v: v.value if v else None)(
        mk.at(name, datetime.fromisoformat(when).astimezone(timezone.utc)))
    offer_r = offer_ratios(read_csv(TIDY / "api_offers.csv"),
                           read_csv(TIDY / "api_teams.csv"), value_at)
    paid_r = auction_ratios(read_csv(TIDY / "api_market.csv"),
                            [a for a in read_csv(TIDY / "api_activity.csv")
                             if a["kind"] == "buy"])
    if not offer_r or not paid_r or not by_player:
        print("flip: not enough history to measure the cost of trading yet")
        return
    offer, premium = mean(offer_r), mean(paid_r)
    model = {"outlook": Outlook(by_player), "offer": offer, "premium": premium}

    newest = max(r["observed_at"] for r in rows)
    last = {r["ff_id"]: float(r["delta_pct_1d"]) for r in rows
            if r["observed_at"] == newest
            and r.get("delta_pct_1d") not in (None, "", "None")}
    name, price, value = u.view("name"), u.view("price"), u.view("value")
    _, xi = u.current_xi
    mine = u.state.squads.get(u.me, {})
    free = [{"key": k, "name": name.get(k, k), "ask": price[k],
             "value": value.get(k) or price[k]}
            for k, r in u.view("route").items() if r == "free" and k in price]
    bench = [{"key": k, "name": name.get(k, k), "value": value.get(k, 0.0)}
             for k in mine if k not in xi]
    by_name = {norm(name.get(k, k)): k for k in mine}

    verdict, moves = {}, []
    try:
        doc = json.loads((REPORTS / "decisions.json").read_text())
        verdict = report_view(doc["ladder"], lambda n: by_name.get(norm(n)))
        moves = doc["moves"]
    except (OSError, ValueError, KeyError):
        pass
    held_back = reserve(moves)
    now = run_now().astimezone(MADRID)
    recent = recently(DECISIONS / "flip_log.csv", now)
    sales, kept = sells(bench, last, dict(u.received_offers), model, verdict,
                        money_rate(moves))
    spend = max(0.0, u.cash - held_back)
    pick_list = [p for p in picks(free, last, model, spend)
                if (p["key"], "SELL") not in recent]
    short = max([held_back - u.cash] + [p["short"] for p in pick_list],
               default=0.0)
    funding = (fund(dict(u.received_offers), mine, name, xi, verdict,
                    money_rate(moves), value, last, model, need=short)
              if short > 0 else [])
    out = {"cash": u.cash, "reserve": held_back, "spendable": spend,
           "fund_need": short,
           "picks": pick_list,
           "sells": [p for p in sales if (p["key"], "BUY") not in recent],
           "held": kept, "fund": funding,
           "measured": {"offer": offer, "premium": premium,
                        "offers": len(offer_r), "auctions": len(paid_r),
                        "points_per_million": money_rate(moves)}}
    out["view"] = present(out)
    path = REPORTS / "decisions.json"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["flip"] = out
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n",
                        encoding="utf-8")
    except (OSError, ValueError):
        print("flip: no decisions.json to attach to")
    logged = record(DECISIONS / "flip_log.csv", out, now)
    print("flip: %d buy(s), %d sale(s) logged; %s to spend (%s held back for the "
          "report's own targets); offers pay %.3fx value (%d), auctions cost "
          "%.3fx ask (%d)"
          % (logged - len(out["sells"]), len(out["sells"]), _m(spend),
             _m(held_back), offer, len(offer_r), premium, len(paid_r)))


def _selftest() -> None:
    days = ["2026-08-%02d" % d for d in range(10, 30)]
    rows = []
    for k, g in (("up", 1.05), ("down", 0.98), ("flat", 1.0)):
        for i in range(12):
            for n, d in enumerate(days):
                rows.append({"observed_at": d + "T2359Z", "ff_id": k + str(i),
                             "value": str(100 * g ** n)})
    by = steps(rows)
    assert by["up0"][0] == ("2026-08-11", by["up0"][0][1]) and abs(by["up0"][0][1] - 5.0) < 1e-9
    assert by["flat0"][0][1] == 0.0
    ol = Outlook(by)
    assert ol.hmax == 4 and set(ol.obs) == {1, 2, 3, 4}
    assert {round(o[0]) for o in ol.near(5.0, 3)} == {5}
    assert {round(o[0]) for o in ol.near(-2.0, 3)} == {-2}
    assert all(abs(o[1] - (1.05 ** 3 - 1) * 100) < 1e-6 for o in ol.near(5.0, 3))
    one_day = [(5.0, 10.0, "d1")] * 50
    assert belief(one_day, 1) is None
    two_days = [(5.0, 10.0, "d1"), (5.0, 20.0, "d2")] * 10
    bel = belief(two_days, 1)
    assert bel == {"mean": 15.0, "n": 20, "days": 2}, bel
    top = ol.best(5.0, 0.98, 1.05)
    assert top["h"] >= 1 and top["net"] > 0 and top["days"] >= 2, top
    assert ol.best(-2.0, 0.98, 1.05)["net"] < 0 and ol.best(None, 0.98, 1.05) is None
    model = {"outlook": ol, "offer": 0.98, "premium": 1.05}

    teams = [{"player_team_id": "9", "player_name": "Zed"}]
    offs = [{"offer_id": "1", "player_team_id": "9", "money": "5500000",
             "created_at": "2026-09-01T22:24:00+02:00"},
            {"offer_id": "1", "player_team_id": "9", "money": "5500000",
             "created_at": "2026-09-01T22:24:00+02:00"},
            {"offer_id": "", "player_team_id": "9", "money": "", "created_at": ""}]
    assert offer_ratios(offs, teams, lambda n, w: 5e6 if n == "Zed" else None) == [1.1]
    close = "2026-09-02T22:24:00+02:00"
    lst = [{"seller": "marketPlayerLeague", "player_id": "7", "expires_at": close,
            "sale_price": "10000000", "observed_at": "a"},
           {"seller": "marketPlayerTeam", "player_id": "8", "expires_at": close,
            "sale_price": "10000000", "observed_at": "a"}]
    buys = [{"player_id": "7", "at": "2026-09-02T22:24:10+02:00", "amount": "10400000"},
            {"player_id": "8", "at": "2026-09-02T22:24:10+02:00", "amount": "99"},
            {"player_id": "7", "at": "2026-09-05T10:00:00+02:00", "amount": "1"}]
    assert auction_ratios(lst, buys) == [1.04], auction_ratios(lst, buys)

    ladder = [{"name": "Kept", "where": "yours", "group": "keep", "pts_mean": -0.5},
              {"name": "Free", "where": "yours", "group": "sell", "pts_mean": -3.0},
              {"name": "Star", "where": "yours", "group": "offer",
               "pts_mean": -9.0},
              {"name": "Rival", "where": "x", "group": "raid", "pts_mean": None}]
    ver = report_view(ladder, {"Kept": "k", "Free": "f", "Star": "s"}.get)
    assert ver == {"k": ("keep", -0.5), "f": ("sell", -3.0),
                   "s": ("offer", -9.0)}, ver
    moves = [{"net": -18.4e6, "value": 1.0}, {"net": -12.3e6, "value": 0.6}, {"net": 5e6, "value": None}]
    assert reserve(moves) == 18.4e6 and money_rate(moves) == 0.8
    assert reserve([{"net": 3e6, "value": 1.0}]) == 0.0
    assert reserve([]) == 0.0 and money_rate([]) == 0.0

    lst = [{"key": "a", "name": "Riser", "ask": 10e6, "value": 10e6},
           {"key": "b", "name": "Faller", "ask": 10e6, "value": 10e6},
           {"key": "c", "name": "NoData", "ask": 10e6, "value": 10e6}]
    got = picks(lst, {"a": 5.0, "b": -2.0}, model, 50e6)
    assert [g["name"] for g in got] == ["Riser"], got
    g = got[0]
    assert g["fits"] and g["gain"] > 0 and g["max_bid"] >= g["pay"], g
    poor = picks(lst, {"a": 5.0}, model, 1e6)[0]
    assert poor["fits"] is False and abs(poor["short"] - (poor["pay"] - 1e6)) < 1e-6, poor
    two = [{"key": "a", "name": "Dear", "ask": 30e6, "value": 30e6},
           {"key": "d", "name": "Cheap", "ask": 4e6, "value": 5e6}]
    ranked = picks(two, {"a": 5.0, "d": 5.0}, model, 99e6)
    assert max(ranked, key=lambda r: r["gain"])["name"] == "Dear", ranked
    assert [r["name"] for r in ranked] == ["Cheap", "Dear"], ranked

    bench = [{"key": "k", "name": "Kept", "value": 5e6},
             {"key": "f", "name": "Free", "value": 5e6},
             {"key": "u", "name": "Unjudged", "value": 5e6}]
    fall = {"k": -2.0, "f": -2.0, "u": -2.0}
    sold, held = sells(bench, fall, {}, model, ver, rate=0.1)
    assert [x["name"] for x in sold] == ["Free"], (sold, held)
    assert [x["name"] for x in held] == ["Kept"], held
    sold2, _ = sells(bench, fall, {}, model, ver, rate=8.0)
    assert "Kept" in [x["name"] for x in sold2], sold2
    assert "Unjudged" not in [x["name"] for x in sold2 + held]
    assert sells(bench[:1], {"k": 5.0}, {}, model, ver, 8.0) == ([], [])
    thin = [{"key": "f", "name": "Free", "value": 5e6}]
    top_fall = model["outlook"].best(-2.0, 0.98, 1.05)
    back_pts = 8.0 * 5e6 * 0.05 / 1e6
    edge = 5e6 * (1 + top_fall["mean"] / 100) * 0.98 + (back_pts * 1e6 / 8.0)
    assert sells(thin, {"f": -2.0}, {"f": edge - 1e4}, model, ver, 8.0)[0] == []
    assert len(sells(thin, {"f": -2.0}, {"f": edge + 1e4}, model, ver, 8.0)[0]) == 1

    my_squad = {"k": 1, "f": 1, "s": 1, "u": 1}
    offers = {"k": 3e5, "f": 2e6, "s": 30e6, "u": 1e6, "outside": 9e6}
    names = {"k": "Kept", "f": "Free", "s": "Star", "u": "Unjudged"}
    menu = fund(offers, my_squad, names, {"s"}, ver, rate=1.0, value={},
               last={}, model=model)
    assert [p["name"] for p in menu] == ["Star", "Free", "Kept"], menu
    assert abs(menu[0]["net_pts"] - (9.0 - 30.0)) < 1e-9, menu[0]
    assert abs(menu[1]["net_pts"] - (0.0 - 2.0)) < 1e-9, menu[1]
    assert abs(menu[2]["net_pts"] - (0.5 - 0.3)) < 1e-9 and menu[2]["net_pts"] > 0, menu[2]
    assert menu[0]["starter"] and not menu[1]["starter"], menu
    assert menu[-1]["running"] == 30e6 + 2e6 + 3e5, menu
    assert all(p["money_now"] is None for p in menu), menu

    priced = fund({"a": 5e6, "b": 5e6}, {"a": 1, "b": 1},
                 {"a": "Riser", "b": "Faller"}, set(),
                 {"a": ("keep", -1.0), "b": ("keep", -1.0)}, rate=1.0,
                 value={"a": 5e6, "b": 5e6}, last={"a": 5.0, "b": -2.0},
                 model=model)
    by_name = {p["name"]: p for p in priced}
    assert by_name["Riser"]["money_now"] < 0, by_name["Riser"]
    assert by_name["Faller"]["money_now"] > 0, by_name["Faller"]

    five = {"a": 1, "b": 1, "c": 1, "d": 1, "e": 1}
    five_offers = {"a": 1e6, "b": 2e6, "c": 3e6, "d": 4e6, "e": 5e6}
    five_names = {k: k.upper() for k in five}
    five_ver = {k: ("keep", -1.0) for k in five}
    whole = fund(five_offers, five, five_names, set(), five_ver, rate=0.1,
                value={}, last={}, model=model)
    assert len(whole) == 5, whole
    trimmed = fund(five_offers, five, five_names, set(), five_ver, rate=0.1,
                   value={}, last={}, model=model, need=2.5e6)
    assert 2 <= len(trimmed) < 5, trimmed
    assert sum(p["offer"] for p in trimmed[:-1]) < 2.5e6 \
        or len(trimmed) <= 2, trimmed
    assert trimmed[-2]["running"] >= 2.5e6, trimmed

    out = {"cash": 20e6, "reserve": 10e6, "spendable": 10e6, "picks": got,
           "sells": sold, "held": held, "fund": [], "fund_need": 0.0}
    v = present(out)
    assert v["summary"] == "10.00M to spend; 10.00M held back for the report's top move", v["summary"]
    tight = present({**out, "picks": [poor]})["sections"][0]["rows"][0]["right"]
    assert tight[1].startswith("bid; needs ") and tight[1].endswith(" more"), tight
    lab = {s["tone"]: s for s in v["sections"]}
    assert lab["buy"]["rows"][0]["name"] == "Riser" and "bid" in lab["buy"]["rows"][0]["right"]
    assert "+5.0%" in lab["buy"]["rows"][0]["detail"], lab["buy"]["rows"][0]
    assert "the report keeps him" in lab["held"]["rows"][0]["detail"]
    assert v["ping"].startswith("\nBuy: Riser (bid up to") and "Sell: Free" in v["ping"], v["ping"]
    assert present({**out, "picks": [], "sells": [], "held": []})["ping"] == ""
    assert "Buy:" not in present({**out, "picks": [poor]})["ping"]
    fund_view = present({**out, "fund": menu, "fund_need": 12e6})
    fund_sec = {s["tone"]: s for s in fund_view["sections"]}["fund"]
    assert [r["name"] for r in fund_sec["rows"]] == ["Star", "Free", "Kept"]
    assert fund_sec["rows"][0]["right"] == ["30.00M", "a net gain"], \
        fund_sec["rows"][0]
    assert fund_sec["rows"][2]["right"][1].startswith("net "), fund_sec["rows"][2]
    assert "a net" in fund_sec["rows"][0]["detail"] \
        or "outearns" in fund_sec["rows"][0]["detail"], fund_sec["rows"][0]
    assert "12.00M short" in fund_sec["label"] and "not a recommendation" \
        in fund_sec["label"] and "3 option" in fund_sec["label"], fund_sec
    assert not any(s["tone"] == "fund" for s in v["sections"]), \
        "an empty fund list must not draw a section"
    try:
        say({"code": "nope"})
        raise AssertionError("an unknown reason must not be papered over")
    except ValueError:
        pass

    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "log.csv"
        assert record(p, out, datetime(2026, 9, 24, 21, 45)) == 2
        assert record(p, {**out, "picks": []}, datetime(2026, 9, 24, 22, 10)) == 1
        assert [r["action"] for r in csv.DictReader(open(p))] == ["SELL"]
        record(p, out, datetime(2026, 9, 24, 23, 0))
        assert sorted({r["day"] for r in csv.DictReader(open(p))}) == ["2026-09-24", "2026-09-25"]
        q = Path(d) / "l2.csv"
        with open(q, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=LOG, restval="")
            w.writeheader()
            w.writerow({"day": "2026-09-24", "action": "SELL", "key": "a", "horizon": "2"})
            w.writerow({"day": "2026-09-24", "action": "BUY", "key": "b", "horizon": ""})
        assert recently(q, datetime(2026, 9, 25, 12, 0)) == {("a", "SELL")}
        assert recently(q, datetime(2026, 9, 27, 12, 0)) == set()
        assert recently(q, datetime(2026, 9, 24, 12, 0)) == set()
    with tempfile.TemporaryDirectory() as d:
        old = Path(d) / "old.csv"
        old.write_text("day,run_at,action,key,name,expected_pct\n2026-09-20,x,BUY,k,K,12.5\n")
        assert recently(old, datetime(2026, 9, 24, 12, 0)) == set()
        record(old, out, datetime(2026, 9, 24, 21, 45))
        assert [r["day"] for r in csv.DictReader(open(old))][0] == "2026-09-20"
    print("flip self-test OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        main()
