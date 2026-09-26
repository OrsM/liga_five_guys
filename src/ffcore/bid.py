
from __future__ import annotations

import statistics
from collections import Counter
from typing import NamedTuple

from ffcore.league import MARKET
from ffcore.parse import money
from ffcore.score import SLOT_MIN, pick_xi, squad_pool
from ffcore.tidy import ledger_stamp

__all__ = ["MAX_LAG_H", "ROUND_TO", "FLOOR_EPS", "HORIZONS", "Premiums", "Advice",
           "is_round", "deals", "usable", "premiums", "low_priced_buys",
           "suggest", "gain",
           "xi_snapshots", "demand_summary",
           ]

HORIZONS = (3, 7, 14)

MAX_LAG_H = 36.0

ROUND_TO = 10_000

FLOOR_EPS = 0.5


class Premiums(NamedTuple):
    n: int
    median: float
    lo: float
    hi: float
    at_floor: int = 0

    def label(self) -> str:
        return "median %+.1f%%, %+.1f%% to %+.1f%% (n=%d)" % (
            self.median, self.lo, self.hi, self.n)

    def swing(self) -> float:
        return max(abs(self.lo), abs(self.hi))


class Advice(NamedTuple):
    low: float | None
    high: float | None
    why: str


def is_round(price) -> bool:
    v = money(price)
    return v is not None and v > 0 and v % ROUND_TO == 0


def deals(lg, market) -> list[dict]:
    rows = []
    for t in lg.txns:
        price = money(t.get("price"))
        when = ledger_stamp(t.get("date", ""))
        if price is None or when is None:
            continue
        who = lg.txn_key(t) if hasattr(lg, "txn_key") else None
        v = market.at(who or t["player"], when)
        src = (t.get("from") or "").strip() or MARKET
        dst = (t.get("to") or "").strip() or MARKET
        rows.append({
            "date": t.get("date", ""), "player": t["player"],
            "key": who,
            "actor": dst if dst != MARKET else src,
            "side": "buy" if dst != MARKET else "sell",
            "is_clause": dst != MARKET and src != MARKET,
            "price": price, "when": when,
            "value": v.value if v else None,
            "lag_h": v.lag_h if v else None,
            "premium": ((price / v.value - 1) * 100.0
                        if v and v.value else None),
            "round": is_round(t.get("price")),
        })
    return rows


def usable(d) -> bool:
    return (d.get("premium") is not None and d.get("lag_h") is not None
            and abs(d["lag_h"]) <= MAX_LAG_H)


def premiums(deals, side: str = "buy") -> Premiums | None:
    vals = sorted(d["premium"] for d in deals
                  if d.get("side") == side and usable(d))
    if not vals:
        return None
    return Premiums(len(vals), statistics.median(vals), vals[0], vals[-1],
                    sum(1 for v in vals if v <= FLOOR_EPS))


def low_priced_buys(deals) -> list[dict]:
    return [d for d in deals if d.get("side") == "buy"
            and d.get("premium") is not None
            and d["premium"] < -FLOOR_EPS]


def suggest(value, prem: Premiums | None, cash=None,
            rival_max=None) -> Advice:
    if not value or value <= 0:
        return Advice(None, None, "no value on record")
    if cash is not None and cash < value:
        return Advice(None, None, "%.2fM short of the floor"
                      % ((value - cash) / 1e6))
    if rival_max is not None and rival_max < value:
        return Advice(value, value, "floor — no rival can afford it")
    if prem is None:
        return Advice(value, value, "floor — no premium history yet")

    low = value * (1 + prem.median / 100.0)
    high = value * (1 + prem.hi / 100.0)
    why = "floor %+.1f%% median, %+.1f%% worst (n=%d)" % (
        prem.median, prem.hi, prem.n)
    if cash is not None and cash < high:
        high = cash
        low = min(low, cash)
        why += ", capped by your cash"
    return Advice(low, high, why)


def gain(pool: dict, candidate: dict, base_total: float):
    slot = candidate.get("slot")
    if not slot:
        return None
    trial = {k: list(v) for k, v in pool.items()}
    trial.setdefault(slot, []).append(candidate)
    trial[slot].sort(key=lambda p: p["score"], reverse=True)
    best = pick_xi(trial, force=candidate)
    return None if best is None else best[0] - base_total


def xi_snapshots(lg, sc, by_key) -> dict[str, dict]:
    out = {}
    for m in lg:
        scored, missing = sc.score_squad(m.players)
        pool = squad_pool(scored)
        counts = Counter(p.slot for p in scored if p.slot)
        out[m.handle] = {
            "pool": pool,
            "best": pick_xi(pool) if scored else None,
            "missing": missing,
            "short": [sl for sl, need in SLOT_MIN.items()
                      if counts.get(sl, 0) < need],
        }
    return out


def demand_summary(cand: dict, lg, snaps: dict) -> str:
    threats, broke = [], 0
    for m in lg:
        if m.handle == lg.cfg.me:
            continue
        snap = snaps[m.handle]
        if snap["best"] is None:
            if cand.get("slot") in snap["short"]:
                threats.append((float("inf"), m.handle, "needs"))
            continue
        g = gain(snap["pool"], cand, snap["best"][0])
        if g is None or g <= 0:
            continue
        if m.max_bid is not None and m.max_bid < (cand.get("value") or 0):
            broke += 1
        else:
            threats.append((g, m.handle,
                            "?" if m.max_bid is None else ""))
    threats.sort(key=lambda t: -t[0])

    bits = []
    if threats:
        g, h, mark = threats[0]
        head = h.split()[0][:8]
        bits.append("%s needs" % head if mark == "needs"
                    else "%s %+.1f%s" % (head, g, mark))
        if len(threats) > 1:
            bits.append("+%d more" % (len(threats) - 1))
    if broke:
        bits.append("(%d broke)" % broke)
    return ", ".join(bits) if bits else "none"


def _selftest() -> None:
    fixed = [
        {"side": "buy", "premium": 2.0, "lag_h": 0.1},
        {"side": "buy", "premium": 10.0, "lag_h": 8.8},
        {"side": "buy", "premium": 20.0, "lag_h": -2.0},
        {"side": "buy", "premium": 99.0, "lag_h": 100.0},
        {"side": "buy", "premium": None, "lag_h": 1.0},
        {"side": "sell", "premium": 50.0, "lag_h": 1.0},
    ]
    p = premiums(fixed)
    assert p.n == 3, p
    assert p.median == 10.0, p
    assert (p.lo, p.hi) == (2.0, 20.0), p
    assert "n=3" in p.label(), p.label()
    assert premiums([]) is None
    assert premiums([{"side": "buy", "premium": None, "lag_h": 1.0}]) is None

    floors = premiums([{"side": "buy", "premium": x, "lag_h": 1.0}
                       for x in (0.0, -0.19, 4.18, 21.6)])
    assert floors.at_floor == 2, floors
    assert floors.n == 4, floors
    assert premiums(fixed).at_floor == 0, premiums(fixed)

    app = premiums([{"side": "sell", "premium": x, "lag_h": 1.0}
                    for x in (-9.43, 1.53, 9.75)], "sell")
    assert round(app.swing(), 2) == 9.75, app.swing()
    assert round(premiums(fixed).swing(), 1) == 20.0, premiums(fixed)
    assert usable({"premium": 1.0, "lag_h": MAX_LAG_H}) is True
    assert usable({"premium": 1.0, "lag_h": MAX_LAG_H + 0.1}) is False

    deals_ = [
        {"player": "C. Romero", "side": "buy", "premium": -87.0,
         "lag_h": 1.0},
        {"player": "Far lag", "side": "buy", "premium": -50.0,
         "lag_h": MAX_LAG_H + 50},
        {"player": "At floor", "side": "buy", "premium": -0.1,
         "lag_h": 1.0},
        {"player": "Normal", "side": "buy", "premium": 8.9, "lag_h": 1.0},
        {"player": "App pays", "side": "sell", "premium": -50.0,
         "lag_h": 1.0},
        {"player": "Unpriced", "side": "buy", "premium": None, "lag_h": 1.0},
    ]
    low = low_priced_buys(deals_)
    assert [d["player"] for d in low] == ["C. Romero", "Far lag"], low
    assert low_priced_buys([]) == []

    prem = Premiums(10, 8.9, 1.45, 21.6)

    a = suggest(1_000_000, prem, cash=10_000_000, rival_max=5_000_000)
    assert round(a.low) == 1_089_000, a
    assert round(a.high) == 1_216_000, a
    assert "8.9" in a.why and "n=10" in a.why, a.why

    a = suggest(1_000_000, prem, cash=10_000_000, rival_max=900_000)
    assert (a.low, a.high) == (1_000_000, 1_000_000), a
    assert "no rival" in a.why, a.why

    a = suggest(1_000_000, prem, cash=10_000_000, rival_max=None)
    assert round(a.high) == 1_216_000, a

    a = suggest(1_000_000, prem, cash=800_000)
    assert (a.low, a.high) == (None, None), a
    assert "short" in a.why, a.why

    a = suggest(1_000_000, None, cash=10_000_000)
    assert (a.low, a.high) == (1_000_000, 1_000_000), a
    assert "no premium history" in a.why, a.why

    a = suggest(1_000_000, prem, cash=1_100_000)
    assert a.high == 1_100_000, a
    assert round(a.low) == 1_089_000, a
    assert "capped by your cash" in a.why, a.why

    a = suggest(1_000_000, prem, cash=1_050_000)
    assert (a.low, a.high) == (1_050_000, 1_050_000), a

    assert suggest(None, prem).low is None
    assert suggest(0, prem).low is None

    def pl(slot, score):
        return {"slot": slot, "score": score, "name": "%s%.1f" % (slot, score)}

    pool = {
        "POR": [pl("POR", 5.0)],
        "DEF": [pl("DEF", 4.0), pl("DEF", 3.5), pl("DEF", 3.0),
                pl("DEF", 2.5)],
        "MED": [pl("MED", 4.0), pl("MED", 3.5), pl("MED", 3.0),
                pl("MED", 2.5), pl("MED", 2.0)],
        "DEL": [pl("DEL", 6.0), pl("DEL", 1.5), pl("DEL", 1.0)],
    }
    base = pick_xi(pool)
    assert base is not None
    total = base[0]
    assert base[1] == (4, 5, 1) and total == 39.0, base[:2]

    up = gain(pool, pl("DEL", 9.0), total)
    assert up is not None and up > 0, up

    down = gain(pool, pl("DEL", 0.1), total)
    assert down is not None and down < 0, down

    assert gain(pool, {"slot": "", "score": 9.0}, total) is None

    assert len(pool["DEL"]) == 3, pool["DEL"]
    assert [p["score"] for p in pool["DEF"]] == [4.0, 3.5, 3.0, 2.5], \
        pool["DEF"]

    thin = {"POR": [pl("POR", 5.0)], "DEF": [pl("DEF", 4.0)]}
    assert gain(thin, pl("MED", 9.0), 0.0) is None

    assert is_round("1.000.000 €") is True
    assert is_round("1.234.567") is False
    assert is_round("0") is False
    assert is_round("") is False

    class _Val(NamedTuple):
        value: float
        lag_h: float

    class _Market:
        def at(self, name, when):
            return None if name == "Unpriced" else _Val(1_000_000.0, 2.0)

    class _Lg:
        txns = [
            {"date": "2026-08-10 12:00", "player": "Bought",
             "from": "", "to": "me", "price": "1.100.000"},
            {"date": "2026-08-11 12:00", "player": "Sold",
             "from": "me", "to": "", "price": "900.000"},
            {"date": "2026-08-12 12:00", "player": "Traded",
             "from": "a", "to": "b", "price": "1.234.567"},
            {"date": "2026-08-12 12:00", "player": "Unpriced",
             "from": "", "to": "me", "price": "5.000.000"},
            {"date": "2026-08-12 12:00", "player": "Free",
             "from": "", "to": "me", "price": ""},
            {"date": "nonsense", "player": "Undated",
             "from": "", "to": "me", "price": "1.000.000"},
        ]

    dl = deals(_Lg(), _Market())
    assert [d["player"] for d in dl] == ["Bought", "Sold", "Traded",
                                         "Unpriced"], dl
    d0 = dl[0]
    assert d0["side"] == "buy" and d0["actor"] == "me", d0
    assert round(d0["premium"], 1) == 10.0, d0
    assert d0["round"] is True and usable(d0)
    assert dl[1]["side"] == "sell" and dl[1]["actor"] == "me", dl[1]
    assert dl[2]["actor"] == "b" and dl[2]["round"] is False, dl[2]
    assert dl[3]["value"] is None and dl[3]["premium"] is None
    assert not usable(dl[3])
    pd = premiums(dl)
    assert pd.n == 2, pd
    assert (round(pd.lo, 1), round(pd.hi, 1)) == (10.0, 23.5), pd
    assert round(premiums(dl, "sell").median, 1) == -10.0, premiums(dl, "sell")

    class _M:
        def __init__(self, handle, max_bid):
            self.handle, self.max_bid = handle, max_bid

    class _Lg:
        class cfg:
            me = "me"

        def __init__(self, managers):
            self._m = managers

        def __iter__(self):
            return iter(self._m)

    def snap(pool=None, short=()):
        best = pick_xi(pool) if pool else None
        return {"best": best, "pool": pool or {},
                "short": list(short), "missing": []}

    weak_pool = {"POR": [{"slot": "POR", "score": 5.0}],
                 "DEF": [{"slot": "DEF", "score": 3.0}] * 5,
                 "MED": [{"slot": "MED", "score": 1.0}] * 5,
                 "DEL": [{"slot": "DEL", "score": 2.0}] * 3}
    strong_pool = {k: [dict(p, score=9.0) for p in v]
                   for k, v in weak_pool.items()}
    cand = {"slot": "MED", "score": 5.0, "value": 10e6, "name": "x"}

    lg = _Lg([_M("me", None), _M("Rich Guy", 50e6), _M("Poor", 1e6),
              _M("Mystery", None), _M("Full", 50e6)])
    snaps = {"me": snap(weak_pool), "Rich Guy": snap(weak_pool),
             "Poor": snap(weak_pool), "Mystery": snap(weak_pool),
             "Full": snap(strong_pool)}
    cell = demand_summary(cand, lg, snaps)
    assert "Rich" in cell and "+1 more" in cell and "(1 broke)" in cell, cell
    assert demand_summary(cand, _Lg([_M("me", None), _M("Full", 50e6)]),
                          {"me": snap(weak_pool), "Full": snap(strong_pool)}
                          ) == "none"
    got = demand_summary(
        cand, _Lg([_M("me", None), _M("Stuck", 1e6)]),
        {"me": snap(weak_pool), "Stuck": snap(short=["MED"])})
    assert got == "Stuck needs", got

    print("ffcore.bid self-test OK (127 cases)")


if __name__ == "__main__":                      # pragma: no cover
    _selftest()
