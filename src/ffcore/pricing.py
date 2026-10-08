from __future__ import annotations

import math
from datetime import datetime, timezone
from statistics import mean, median

CLIP = 20.0
PRICE_WINDOW = 50
BID_BEATS = 0.8


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


def _clip(step: float, updates: int = 1) -> float:
    return max(-CLIP * updates, min(CLIP * updates, step))


def _ahead(s: list[tuple[str, float]], i: int, h: int) -> float:
    return 100 * (math.prod(1 + c / 100 for _, c in s[i + 1:i + 1 + h]) - 1)


class Momentum:
    """How much of a price change carries into the next h updates: one
    least-squares slope per horizon, fitted over every player. Changes are
    clipped at CLIP per update, so a long rise is not cut short."""

    def __init__(self, by_player: dict[str, list[tuple[str, float]]],
                 until: str = "9999"):
        self.hmax = max(1, int(median(len(s) for s in by_player.values())) // 4) \
            if by_player else 1
        self.slope: dict[int, float] = {}
        for h in range(1, self.hmax + 1):
            pairs = [(_clip(s[i][1]), _clip(_ahead(s, i, h), h))
                     for s in by_player.values() for i in range(len(s) - h)
                     if s[i + h][0] <= until]
            sxx = sum(x * x for x, _ in pairs)
            if sxx:
                self.slope[h] = sum(x * y for x, y in pairs) / sxx
        self.by_player = by_player

    @property
    def carry(self) -> tuple[float, ...]:
        """The share of a trend (its move over the horizon) already made
        after each update."""
        end = self.slope.get(self.horizon)
        return tuple(self.slope[h] / end for h in sorted(self.slope)) if end else ()

    @property
    def horizon(self) -> int:
        """The longest h the data can fit: a rise is still carrying there."""
        return max(self.slope, default=1)

    def trend(self) -> dict[str, float]:
        """Each player's value change, in %, over the horizon: as long as
        you would hold him, not just until the lock."""
        c = self.slope.get(self.horizon, 0.0)
        newest = max((s[-1][0] for s in self.by_player.values() if s), default="")
        return {k: c * _clip(s[-1][1]) for k, s in self.by_player.items()
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
    ends: dict[tuple[str, datetime], dict] = {}
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


def offer_ratios(offers: list[dict], teams: list[dict]) -> list[float]:
    """The game's nightly offers for listed players, each over the player's
    value when it was made, oldest first: each offer once."""
    value = {(r["observed_at"], r.get("player_team_id")): float(r.get("market_value") or 0)
             for r in teams}
    seen: dict[str, tuple[str, float]] = {}
    for r in offers:
        v = value.get((r["observed_at"], r.get("player_team_id")))
        if r.get("from_market") == "true" and r.get("money") and v \
                and r.get("offer_id") and r["offer_id"] not in seen:
            seen[r["offer_id"]] = (r.get("created_at") or "", float(r["money"]) / v)
    return [x for _at, x in sorted(seen.values())]


def premium_to_beat(ratios: list[float]) -> float:
    """The bid, over the asking price, at or above BID_BEATS of these
    recent winning bids."""
    recent = sorted(ratios[-PRICE_WINDOW:])
    return recent[min(len(recent) - 1, int(BID_BEATS * len(recent)))] if recent else 1.0


def _selftest() -> None:
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
    assert mo.horizon == 4
    assert mo.carry[-1] == 1.0 and 0 < mo.carry[0] < mo.carry[1], mo.carry
    t = mo.trend()
    assert t["up0"] > 0 > t["down0"] and t["flat0"] == 0.0, t
    assert Momentum({}).trend() == {}
    long_rise = {"r%d" % i: [("2026-08-%02d" % n, 15.0) for n in range(1, 13)] for i in range(3)}
    assert abs(Momentum(long_rise).slope[2] * 15.0 - (1.15 ** 2 - 1) * 100) < 1e-6, \
        "a rise over several updates is not clipped as if it were one"
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

    teams = [{"observed_at": "t1", "player_team_id": "9", "market_value": "10"}]
    offers = [{"observed_at": "t1", "player_team_id": "9", "from_market": "true", "money": "11",
               "offer_id": "o", "created_at": "c"}] * 2 + [
              {"observed_at": "t1", "player_team_id": "9", "from_market": "false", "money": "12",
               "offer_id": "m", "created_at": "c"}]
    assert offer_ratios(offers, teams) == [1.1], "the game's offers, each once, over value"

    assert premium_to_beat([1.0] * 5 + [1.3] * 5) == 1.3
    assert premium_to_beat([1.0] * 9 + [1.3]) == 1.0
    assert premium_to_beat([]) == 1.0

    print("ffcore.pricing self-test OK")


if __name__ == "__main__":
    _selftest()
