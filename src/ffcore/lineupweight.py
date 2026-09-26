from __future__ import annotations

import itertools
import statistics as st

from ffcore.tidy import MATCH_LEN

GRID = (0.5, 1.0, 2.0, 3.0, 4.0, 6.0, 8.0, 12.0)
PRIOR_90 = 4.0
WARMUP = 3
MIN_ROWS = 300
REGULAR = 0.7
MIN_STATUS_ROWS = 20


def line_rows(outs, pos: dict, points: dict) -> list[dict]:
    return [{"key": o.key, "pos": pos[o.key], "j": o.jornada, "line": o.ff,
             "share": min(1.0, o.mins / MATCH_LEN), "mins": o.mins,
             "pts": points.get((o.key, o.jornada), 0.0)}
            for o in outs if o.in_squad and o.key and pos.get(o.key)]


def pairs(data):
    mine, league, out = {}, {}, []
    for r in data:
        a = mine.setdefault(r["key"], [0.0, 0.0, []])
        g = league.setdefault(r["pos"], [0.0, 0.0])
        if r["j"] >= WARMUP and r["line"] is not None and a[2] and g[1]:
            prior = g[0] / (g[1] / MATCH_LEN)
            rate = (PRIOR_90 * prior + a[0]) / (PRIOR_90 + a[1] / MATCH_LEN)
            out.append((r, rate, r["line"], sum(a[2]), len(a[2])))
        a[0] += r["pts"]
        a[1] += r["mins"]
        a[2].append(r["share"])
        g[0] += r["pts"]
        g[1] += r["mins"]
    return out


def mse(sample, k):
    return st.mean((r["pts"] - rate * (k * line + tot) / (k + n)) ** 2
                   for r, rate, line, tot, n in sample)


def fit_lineup_weight(data) -> float | None:
    p = pairs(data)
    if len(p) < MIN_ROWS:
        return None
    train = p[:int(0.6 * len(p))]
    return min(GRID, key=lambda k: mse(train, k))


def fit_status_factors(outs) -> dict[str, float]:
    earlier: dict[str, list[float]] = {}
    seen: dict[str, list[float]] = {}
    for _g, group in itertools.groupby(outs, key=lambda o: (o.at, o.group)):
        group = list(group)
        for o in group:
            hist = earlier.get(o.who, [])
            if o.listed and len(hist) >= 2 and st.mean(hist) >= REGULAR:
                seen.setdefault(o.status, []).append(min(1.0, o.mins / MATCH_LEN))
        for o in group:
            if o.in_squad:
                earlier.setdefault(o.who, []).append(min(1.0, o.mins / MATCH_LEN))
    base = st.mean(seen["ok"]) if seen.get("ok") else 0.0
    return {f: st.mean(v) / base for f, v in seen.items()
            if base > 0 and f != "ok" and len(v) >= MIN_STATUS_ROWS}


def _selftest() -> None:
    from ffcore.startprob import Outcome

    data, steady = [], []
    for i in range(60):
        for j in range(1, 9):
            for out, share, line in ((data, (i + j) % 2, (i + j) % 2),
                                     (steady, 1.0, (i * j) % 2)):
                out.append({"key": "p%d" % i, "pos": "MED", "j": j,
                            "at": "2026-09-%02dT1200Z" % j, "line": float(line),
                            "share": float(share), "mins": 90.0 * share,
                            "pts": 5.0 * share})
    data.sort(key=lambda r: r["at"])
    steady.sort(key=lambda r: r["at"])
    assert fit_lineup_weight(data) == max(GRID)
    assert fit_lineup_weight(steady) == min(GRID)
    assert fit_lineup_weight(data[:20]) is None
    assert not pairs(data[:1])

    outs = []
    for m in range(1, 9):
        for i in range(50):
            flag = ("injured" if i < 25 and m > 3 else "doubt" if i < 40 and
                    m > 3 and i % 2 else "ok")
            plays = flag == "ok" or (flag == "doubt" and m % 2)
            outs.append(Outcome("2026-09-%02dT0800Z" % m, m, "%d:t" % m,
                                "t:p%d" % i, None, True, None, flag, None,
                                bool(plays), 90.0 if plays else 0.0))
    got = fit_status_factors(outs)
    assert got["injured"] < 0.05 and 0.3 < got["doubt"] < 0.7, got
    assert fit_status_factors(outs[:10]) == {}

    assert line_rows([Outcome("t", 3, "g", "t:a", "k1", True, 0.8, "ok", None,
                              True, 45.0),
                      Outcome("t", 3, "g", "t:b", "k2", True, 0.8, "ok", None,
                              False, 0.0)],
                     {"k1": "MED", "k2": "MED"}, {("k1", 3): 4.0}) == [
        {"key": "k1", "pos": "MED", "j": 3, "line": 0.8, "share": 0.5,
         "mins": 45.0, "pts": 4.0}]
    print("ffcore.lineupweight self-test OK")


if __name__ == "__main__":
    _selftest()
