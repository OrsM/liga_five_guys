from __future__ import annotations

import math
from datetime import datetime, timezone
from statistics import mean, median

CLIP = 20.0


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


def _clip(step: float) -> float:
    return max(-CLIP, min(CLIP, step))


def _ahead(s: list[tuple[str, float]], i: int, h: int) -> float:
    return 100 * (math.prod(1 + c / 100 for _, c in s[i + 1:i + 1 + h]) - 1)


class Momentum:
    """How much of a price change carries into the next h updates: one
    least-squares slope per horizon, fitted over every player."""

    def __init__(self, by_player: dict[str, list[tuple[str, float]]],
                 until: str = "9999"):
        self.hmax = max(1, int(median(len(s) for s in by_player.values())) // 4) \
            if by_player else 1
        self.slope: dict[int, float] = {}
        for h in range(1, self.hmax + 1):
            pairs = [(_clip(s[i][1]), _clip(_ahead(s, i, h)))
                     for s in by_player.values() for i in range(len(s) - h)
                     if s[i + h][0] <= until]
            sxx = sum(x * x for x, _ in pairs)
            if sxx:
                self.slope[h] = sum(x * y for x, y in pairs) / sxx


def trend(by_player: dict[str, list[tuple[str, float]]],
          updates: int) -> dict[str, float]:
    mo = Momentum(by_player)
    c = mo.slope.get(max(1, min(updates, mo.hmax)), 0.0)
    newest = max((s[-1][0] for s in by_player.values() if s), default="")
    return {k: c * _clip(s[-1][1]) for k, s in by_player.items()
            if s and s[-1][0] == newest}


def grade(by_player: dict[str, list[tuple[str, float]]],
          horizons=(1, 3, 5)) -> dict[int, dict]:
    days = sorted({d for s in by_player.values() for d, _ in s})
    pairs: dict[int, list[tuple[float, float]]] = {h: [] for h in horizons}
    for d in days[len(days) // 4:]:
        mo = Momentum(by_player, until=d)
        for s in by_player.values():
            i = next((i for i, (day, _) in enumerate(s) if day == d), None)
            for h in horizons:
                if i is not None and i + h < len(s) and h in mo.slope:
                    pairs[h].append((mo.slope[h] * _clip(s[i][1]), _ahead(s, i, h)))
    return {h: {"n": len(v), "mae": mean(abs(p - a) for p, a in v),
                "zero": mean(abs(a) for _, a in v)}
            for h, v in pairs.items() if v}


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


def cash_price(reach) -> float | None:
    pts = sorted((max(0.0, c), d) for c, d in reach)
    if len(pts) < 2 or pts[-1][0] <= 0:
        return None
    best_now = max((d for c, d in pts if c <= 0.0), default=None)
    if best_now is None:
        return None
    best_any = max(d for _, d in pts)
    span = max(c for c, _ in pts) / 1e6
    return max(0.0, (best_any - best_now) / span) if span else None


def _selftest() -> None:
    flat = [(0.0, 0.40), (5e6, 0.30), (12e6, 0.20)]
    assert cash_price(flat) == 0.0
    step = [(0.0, 0.40), (10e6, 0.50)]
    assert abs(cash_price(step) - 0.10 / 10.0) < 1e-12
    assert cash_price([]) is None and cash_price([(0.0, 0.4)]) is None

    days = ["2026-08-%02d" % d for d in range(10, 30)]
    rows = [{"observed_at": d + "T2359Z", "ff_id": k + str(i),
             "value": str(100 * g ** n)}
            for k, g in (("up", 1.05), ("down", 0.98), ("flat", 1.0))
            for i in range(12) for n, d in enumerate(days)]
    by = steps(rows)
    assert by["up0"][0][0] == "2026-08-11" and abs(by["up0"][0][1] - 5.0) < 1e-9
    assert by["flat0"][0][1] == 0.0
    mo = Momentum(by)
    assert mo.hmax == 4 and set(mo.slope) == {1, 2, 3, 4}
    up = Momentum({k: v for k, v in by.items() if k.startswith("up")})
    assert abs(up.slope[3] * 5.0 - (1.05 ** 3 - 1) * 100) < 1e-6, up.slope
    t = trend(by, 3)
    assert t["up0"] > 0 > t["down0"] and t["flat0"] == 0.0, t
    assert trend(by, 99) == trend(by, 4) and trend({}, 3) == {}
    g = grade(by, horizons=(1, 2))
    assert g[1]["mae"] < g[1]["zero"] and g[2]["n"] > 0, g

    close = "2026-09-02T22:24:00+02:00"
    lst = [{"seller": "marketPlayerLeague", "player_id": "7", "expires_at": close,
            "sale_price": "10000000", "observed_at": "a"},
           {"seller": "marketPlayerTeam", "player_id": "8", "expires_at": close,
            "sale_price": "10000000", "observed_at": "a"}]
    buys = [{"player_id": "7", "at": "2026-09-02T22:24:10+02:00", "amount": "10400000"},
            {"player_id": "8", "at": "2026-09-02T22:24:10+02:00", "amount": "99"},
            {"player_id": "7", "at": "2026-09-05T10:00:00+02:00", "amount": "1"}]
    assert auction_ratios(lst, buys) == [1.04], auction_ratios(lst, buys)

    print("ffcore.pricing self-test OK")


if __name__ == "__main__":
    import sys
    if "--grade" in sys.argv:
        from ffcore.tidy import history
        for h, g in grade(steps(history("market"))).items():
            print("%d update(s) ahead: n=%d  error %.2f%%  vs %.2f%% for "
                  "'no change'" % (h, g["n"], g["mae"], g["zero"]))
    else:
        _selftest()
