"""Buy players whose market value is drifting up, hold while it does, sell when it turns.

WHY THIS SHAPE (measured 2026-09-24 on 44 days of values and the league's own
ledger). A player's market value has momentum: after an update of +3% or more
he rises again 90% of the time, +10.7% over the next three updates. But an
OVERNIGHT flip loses money -- the auction winner pays a median 4% over the ask
and the app buys back at a median 0.985x value -- so a buy has to be HELD for
the drift to pay for that friction, and SOLD on an actual fall (a flat update
is a pause, not a reversal: a quarter of all updates are flat). The managers
who made money here (Albert +72M, Burton +47M) held a median 8-9 days.

WHAT COUNTS IS AN UPDATE, NOT A CALENDAR DAY. The daily rows are not aligned
with the nightly value update (before ~Sep 6 the last snapshot of a day fell
after it, after that before it), so every figure here is "the next h updates".

Only the FREE MARKET (the app's own listings) is considered -- not clause
raids, not other managers' listings. The capital is cash plus players who will
not start; those are sold on a fall or an offer above value.
"""
from __future__ import annotations

import csv
import json
import sys
from datetime import timedelta

HORIZON = 5            # updates a buy is expected to be held
HURDLE = 0.01          # net return per update the cash must earn to be worth locking up
OFFER = 0.985          # median app offer / value (75 offers, 2026-09-24)
PREMIUM = 1.04         # median winning price / ask (70 feed buys, 2026-09-24)
MIN_N = 30             # observations behind an expectation before it is believed
BUCKETS = ((-1e9, -3.0), (-3.0, -1.0), (-1.0, 1.0),
           (1.0, 3.0), (3.0, 6.0), (6.0, 1e9))   # size of the last update, in %
GOOD_OFFER = 1.03      # with no expectation to weigh it against, an offer this many times value is taken
# THE REPORT OWNS "WHO IS DISPENSABLE". A bench player is optionality -- he covers
# injuries and rotation -- and the season simulation already prices it: each
# row carries the season points selling him alone gains or costs, with a 10th-
# percentile band. This module may sell only a player the report itself would
# let go for nothing: never starts (its SELL group), or a median cost above
# SELL_PTS_FLOOR AND no real downside (10th percentile above SELL_LO_FLOOR).
SELL_PTS_FLOOR = -1.0
SELL_LO_FLOOR = -10.0
COOLDOWN_DAYS = 3      # a player just sold is not bought back (or the reverse): no whipsaw


def steps(rows: list[dict]) -> dict[str, list[float]]:
    """{player: [% change from each daily row to the next]}, oldest first."""
    vals: dict[str, list[float]] = {}
    for r in sorted(rows, key=lambda r: r["observed_at"]):
        try:
            vals.setdefault(r["ff_id"], []).append(float(r["value"]))
        except (KeyError, ValueError):
            pass
    return {k: [100 * (b / a - 1) for a, b in zip(v, v[1:]) if a > 0]
            for k, v in vals.items()}


def outlook(by_player: dict[str, list[float]], lo: float, hi: float,
            h: int = HORIZON) -> tuple[float, int]:
    """(mean compounded % over the next h updates, observations) after an
    update in [lo, hi) -- over EVERY player's history, not only the ones for
    sale, because that is where the sample is."""
    got = []
    for s in by_player.values():
        for i in range(len(s) - h):
            if lo <= s[i] < hi:
                g = 1.0
                for c in s[i + 1:i + 1 + h]:
                    g *= 1 + c / 100
                got.append(100 * (g - 1))
    return (sum(got) / len(got), len(got)) if got else (0.0, 0)


def bucket(step: float | None):
    return next((b for b in BUCKETS if step is not None
                 and b[0] <= step < b[1]), None)


def picks(listings: list[dict], last: dict[str, float], table: dict,
          cash: float, h: int = HORIZON) -> list[dict]:
    """Free-market listings worth buying, best expected millions first.

    `listings` [{key, name, ask, value}]; `last` {key: % of the last update};
    `table` {bucket: (mean %, n)}; `cash` what may be spent. `max_bid` is the
    dearest price at which the cash still earns HURDLE per update; `fits` says
    whether the price you would probably pay (ask x PREMIUM) is affordable.
    """
    out = []
    for l in listings:
        b = bucket(last.get(l["key"]))
        exp, n = table.get(b, (0.0, 0))
        if b is None or n < MIN_N:
            continue
        leave = l["value"] * (1 + exp / 100) * OFFER
        pay = l["ask"] * PREMIUM
        if (leave / pay - 1) / h < HURDLE:
            continue
        out.append({**l, "last": last[l["key"]], "expected_pct": exp, "n": n,
                    "expected_gain": leave - pay, "pay": pay,
                    "max_bid": leave / (1 + HURDLE * h),
                    "fits": pay <= cash})
    return sorted(out, key=lambda x: -x["expected_gain"])


def sells(bench: list[dict], last: dict[str, float],
          offers: dict[str, float], table: dict,
          cost: dict[str, tuple]) -> tuple[list[dict], list[dict]]:
    """(sales, held back) among non-starters. `bench` [{key, name, value}];
    `cost` {key: (group, season pts, 10th-percentile pts)} from the report.

    Sold when holding is expected to LOSE value, or an offer already beats what
    holding is expected to fetch (the same table that picks the buys, so a
    player on the way up is not sold at +6%) -- AND the report says letting
    him go costs nothing. A player the market says to sell but the report says
    to keep is `held back`, with what selling him would cost in season points.
    """
    out, held = [], []
    for p in bench:
        offer, step = offers.get(p["key"]), last.get(p["key"])
        exp, n = table.get(bucket(step), (0.0, 0))
        known = n >= MIN_N
        hold = p["value"] * (1 + exp / 100) * OFFER
        if offer and known and offer >= hold:
            why = "the app's offer beats what holding is expected to fetch"
        elif offer and not known and offer >= GOOD_OFFER * p["value"]:
            why = "the app offers %.0f%% over his value" % (
                100 * (offer / p["value"] - 1))
        elif known and exp < 0:
            why = "expected to lose %.1f%% over the next %d updates (last " \
                  "update %+.1f%%)" % (-exp, HORIZON, step)
        else:
            continue
        group, pts, lo = cost.get(p["key"], (None, None, None))
        free = group == "sell" or (pts is not None and pts >= SELL_PTS_FLOOR
                                   and lo is not None and lo >= SELL_LO_FLOOR)
        row = {**p, "offer": offer, "last": step, "why": why, "season_pts": pts}
        if free:
            out.append(row)
        else:
            held.append({**row, "why": "%s -- but the report keeps him: selling "
                         "costs %s season points%s" % (
                             why.split(" (")[0],
                             "an unknown number of" if pts is None
                             else "%.0f" % -pts,
                             "" if lo is None or lo >= SELL_LO_FLOOR else
                             " (up to %.0f in a bad case)" % -lo)})
    return out, held


LOG = ["day", "run_at", "action", "key", "name", "ask", "value", "offer",
       "last", "expected_pct", "max_bid", "cash", "why"]


def record(path, out: dict, now) -> int:
    """Write today's recommendations to the decision log, AT THE TIME they are
    made, so what was advised can never be reconstructed after the outcome is
    known. Keyed on the market close they are for (22:24 Madrid): a later run
    before that close replaces the day's rows, so the LAST word before the
    close is the one that stays."""
    close = now.replace(hour=22, minute=24, second=0, microsecond=0)
    day = (close if now < close else close + timedelta(days=1)).strftime("%Y-%m-%d")
    keep = []
    if path.exists():
        with open(path, newline="", encoding="utf-8") as fh:
            keep = [r for r in csv.DictReader(fh) if r["day"] != day]
    base = {"day": day, "run_at": now.strftime("%Y-%m-%dT%H:%M"),
            "cash": round(out["cash"])}
    new = [{**base, "action": "BUY", "key": p["key"], "name": p["name"],
            "ask": round(p["ask"]), "value": round(p["value"]),
            "last": p["last"], "expected_pct": round(p["expected_pct"], 2),
            "max_bid": round(p["max_bid"])} for p in out["picks"]]
    new += [{**base, "action": "SELL", "key": p["key"], "name": p["name"],
             "value": round(p["value"]), "offer": round(p["offer"] or 0),
             "last": p["last"], "why": p["why"]} for p in out["sells"]]
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=LOG, restval="")
        w.writeheader()
        w.writerows(keep + new)
    return len(new)


def recently(path, now) -> set[tuple[str, str]]:
    """{(key, action)} advised in the last COOLDOWN_DAYS days, before today's
    close -- so a player sold on Monday is not recommended back on Tuesday."""
    if not path.exists():
        return set()
    today = now.strftime("%Y-%m-%d")
    since = (now - timedelta(days=COOLDOWN_DAYS)).strftime("%Y-%m-%d")
    with open(path, newline="", encoding="utf-8") as fh:
        return {(r["key"], r["action"]) for r in csv.DictReader(fh)
                if since <= r["day"] < today}


def main() -> None:
    import decide
    from ffcore.text import norm
    from ffcore.tidy import DECISIONS, MADRID, REPORTS, TIDY, read_csv, run_now

    u = decide.load()
    rows = read_csv(TIDY / "market.csv")
    table = {b: outlook(steps(rows), *b) for b in BUCKETS}
    newest = max(r["observed_at"] for r in rows)
    last = {}
    for r in rows:
        if r["observed_at"] == newest and r.get("delta_pct_1d") not in (None, "", "None"):
            last[r["ff_id"]] = float(r["delta_pct_1d"])

    name, price, value = u.name_view, u.price_view, u.value_view
    _, xi = u.current_xi
    mine = u.state.squads.get(u.me, {})
    free = [{"key": k, "name": name.get(k, k), "ask": price[k],
             "value": value.get(k) or price[k]}
            for k, r in u.route_view.items() if r == "free" and k in price]
    bench = [{"key": k, "name": name.get(k, k), "value": value.get(k, 0.0)}
             for k in mine if k not in xi]

    reserve, cost = 0.0, {}
    try:
        ladder = json.loads((REPORTS / "decisions.json").read_text())["ladder"]
        reserve = max((r["market"] or 0.0 for r in ladder
                       if r["group"] == "buy"), default=0.0)
        said = {norm(r["name"]): (r["group"], r["pts"], r["pts_lo"])
                for r in ladder if r["where"] == "yours"
                and r["group"] in ("sell", "keep", "out", "in")}
        cost = {b["key"]: said[norm(b["name"])] for b in bench
                if norm(b["name"]) in said}
    except (OSError, ValueError, KeyError):
        pass
    sales, held = sells(bench, last, dict(u.received_offers), table, cost)
    recent = recently(DECISIONS / "flip_log.csv", run_now().astimezone(MADRID))

    out = {"horizon": HORIZON, "hurdle": HURDLE, "cash": u.cash,
           "reserve": reserve, "spendable": max(0.0, u.cash - reserve),
           "picks": [p for p in picks(free, last, table,
                                      max(0.0, u.cash - reserve))
                     if (p["key"], "SELL") not in recent],
           "sells": [p for p in sales if (p["key"], "BUY") not in recent],
           "held": held,
           "table": {"%g..%g" % b: v for b, v in table.items()}}
    path = REPORTS / "decisions.json"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["flip"] = out
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n",
                        encoding="utf-8")
    except (OSError, ValueError):
        print("flip: no decisions.json to attach to")
    logged = record(DECISIONS / "flip_log.csv", out, run_now().astimezone(MADRID))
    print("flip: %d buy(s), %d sale(s) logged; %.1fM to spend (%.1fM held back for "
          "the report's own targets)" % (logged - len(out["sells"]),
                                         len(out["sells"]),
                                         out["spendable"] / 1e6, reserve / 1e6))


def _selftest() -> None:
    # A player who rose a lot last update keeps rising for a while in this
    # history; one who fell keeps falling. Two rows per day per player.
    days = ["2026-08-%02d" % d for d in range(10, 26)]
    rows = []
    for k, path in (("up", [100 * 1.05 ** i for i in range(16)]),
                    ("down", [100 * 0.98 ** i for i in range(16)]),
                    ("flat", [100.0] * 16)):
        for _ in range(12):        # enough players for MIN_N
            for d, v in zip(days, path):
                rows.append({"observed_at": d + "T2359Z", "ff_id": k + str(_),
                             "value": str(v)})
    by = steps(rows)
    assert abs(by["up0"][0] - 5.0) < 1e-9 and by["flat0"][0] == 0.0, by["up0"][:2]
    exp, n = outlook(by, 3.0, 6.0)
    assert n >= MIN_N and abs(exp - (1.05 ** 5 - 1) * 100) < 1e-6, (exp, n)
    assert outlook(by, 50.0, 60.0) == (0.0, 0)          # nothing there: no belief
    assert bucket(4.0) == (3.0, 6.0) and bucket(0.5) == (-1.0, 1.0)
    assert bucket(None) is None and bucket(-9.0) == BUCKETS[0]
    table = {b: outlook(by, *b) for b in BUCKETS}
    lst = [{"key": "a", "name": "Riser", "ask": 10e6, "value": 10e6},
           {"key": "b", "name": "Flat", "ask": 10e6, "value": 10e6},
           {"key": "c", "name": "NoData", "ask": 10e6, "value": 10e6}]
    got = picks(lst, {"a": 4.0, "b": 0.0}, table, cash=50e6)
    assert [g["name"] for g in got] == ["Riser"], got
    g = got[0]
    assert g["fits"] and g["expected_gain"] > 0 and g["max_bid"] > g["pay"], g
    # ...the cheapest price at which it still clears the hurdle is exactly max_bid
    leave = 10e6 * 1.05 ** 5 * OFFER
    assert abs(g["max_bid"] - leave / (1 + HURDLE * HORIZON)) < 1e-3, g
    assert picks(lst, {"a": 4.0}, table, cash=1e6)[0]["fits"] is False
    # a hurdle it cannot clear: same player, but he only rises a little
    weak = {b: (1.0, 100) for b in BUCKETS}
    assert picks(lst, {"a": 4.0}, weak, cash=50e6) == []
    # sells: expected to fall; an offer that beats holding; a rising player who
    # is offered only a little over value (HOLD -- he is expected to go up
    # further); a steady one nobody wants.
    bench = [{"key": "f", "name": "Faller", "value": 5e6},
             {"key": "o", "name": "Offered", "value": 5e6},
             {"key": "r", "name": "Riser", "value": 5e6},
             {"key": "s", "name": "Steady", "value": 5e6}]
    last = {"f": -2.0, "o": 0.0, "r": 4.0, "s": 0.0}
    free_to_go = {"f": ("keep", -0.3, -2.0), "o": ("sell", -50.0, -200.0),
                  "r": ("keep", 0.0, 0.0), "s": ("keep", 0.0, 0.0)}
    got, held = sells(bench, last, {"o": 5.5e6, "r": 5.3e6}, table, free_to_go)
    assert [x["name"] for x in got] == ["Faller", "Offered"] and held == [], (got, held)
    assert "lose" in got[0]["why"] and "beats" in got[1]["why"], got
    # no history behind a bucket: fall back to a plain 'offer well above value'
    assert [x["name"] for x in sells(bench, last, {"s": 5.2e6}, {}, free_to_go)[0]] == ["Steady"]

    # THE REPORT OWNS WHO IS DISPENSABLE. The market says sell a falling player,
    # but if the season simulation says letting him go costs points -- or has a
    # real downside band, or says nothing at all -- he is HELD BACK, and the
    # reason says what it would cost. Only a player the report lets go for free
    # (its own SELL group, or ~0 median cost with no downside) is ever advised.
    kept = {"f": ("keep", -6.0, -51.0)}
    got, held = sells(bench[:1], last, {}, table, kept)
    assert got == [] and len(held) == 1, (got, held)
    assert "the report keeps him" in held[0]["why"] and "6 season points" in held[0]["why"], held
    assert "up to 51 in a bad case" in held[0]["why"], held
    assert sells(bench[:1], last, {}, table, {"f": ("keep", -0.3, -51.0)})[0] == []   # median ~0, wide downside
    assert sells(bench[:1], last, {}, table, {})[0] == []                              # report silent: do nothing
    assert "unknown number" in sells(bench[:1], last, {}, table, {})[1][0]["why"]

    # no whipsaw: what was advised in the last few days (before today) is remembered
    import tempfile
    from datetime import datetime
    from pathlib import Path
    with tempfile.TemporaryDirectory() as d:
        lp = Path(d) / "log.csv"
        assert recently(lp, datetime(2026, 9, 24, 12, 0)) == set()
        for day, act, key in (("2026-09-20", "SELL", "old"), ("2026-09-22", "SELL", "a"),
                              ("2026-09-23", "BUY", "b"), ("2026-09-24", "SELL", "today")):
            with open(lp, "a", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=LOG, restval="")
                if lp.stat().st_size == 0:
                    w.writeheader()
                w.writerow({"day": day, "action": act, "key": key})
        assert recently(lp, datetime(2026, 9, 24, 12, 0)) == {("a", "SELL"), ("b", "BUY")}, \
            recently(lp, datetime(2026, 9, 24, 12, 0))
    # the decision log: last word before the close wins; after the close it is tomorrow's
    import tempfile
    from datetime import datetime
    out = {"cash": 20e6, "picks": [{"key": "a", "name": "Riser", "ask": 10e6,
                                    "value": 10e6, "last": 4.0,
                                    "expected_pct": 27.6, "max_bid": 12e6}],
           "sells": [{"key": "f", "name": "Faller", "value": 5e6, "offer": None,
                      "last": -2.0, "why": "x"}]}
    with tempfile.TemporaryDirectory() as d:
        from pathlib import Path
        p = Path(d) / "log.csv"
        assert record(p, out, datetime(2026, 9, 24, 21, 45)) == 2
        out2 = {**out, "picks": [], "sells": out["sells"]}
        assert record(p, out2, datetime(2026, 9, 24, 22, 10)) == 1   # replaces
        rows = list(csv.DictReader(open(p)))
        assert [(r["day"], r["action"]) for r in rows] == [("2026-09-24", "SELL")], rows
        record(p, out, datetime(2026, 9, 24, 23, 0))                 # after the close: tomorrow's
        days = sorted({r["day"] for r in csv.DictReader(open(p))})
        assert days == ["2026-09-24", "2026-09-25"], days
    print("flip self-test OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        main()
