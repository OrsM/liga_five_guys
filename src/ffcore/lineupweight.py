from __future__ import annotations

import collections
import statistics as st
from datetime import datetime, timedelta

LEAD_H = 24.0
GRID = (0.5, 1.0, 2.0, 3.0, 4.0, 6.0, 8.0, 12.0)
PRIOR_90 = 4.0
WARMUP = 3
MIN_ROWS = 300
REGULAR = 0.7
MIN_STATUS_ROWS = 20


def stamp(s):
    return datetime.strptime(s, "%Y-%m-%dT%H%MZ")


def rows():
    from ffcore.tidy import (MATCH_LEN, jornada_of_match, load_crosswalk,
                             load_perjornada, minutes_played, LINEUP_SOURCE,
                             newest, table)

    xw = load_crosswalk()
    pos = {r["ff_id"]: r["position"] for r in table("market")}
    jor = jornada_of_match()
    points = {}
    for r in load_perjornada():
        if r["games_delta"] == "1":
            points[(r["ff_id"], int(r["jornada"]))] = float(r["points_delta"])
    seen, scraped = {}, {}
    for r in newest("starters"):
        if r.get("role") not in ("starter", "sub"):
            continue
        m = r["match_id"]
        scraped[m] = min(scraped.get(m, "9"), r["observed_at"])
        seen.setdefault((m, r["team_slug"], r["player_slug"]), r)
    snaps = collections.defaultdict(list)
    for r in sorted(table("lineups", LINEUP_SOURCE), key=lambda r: r["observed_at"]):
        if r.get("start_pct") not in (None, ""):
            snaps[(r["team_slug"], r["player_slug"])].append(
                (stamp(r["observed_at"]), float(r["start_pct"]) / 100.0))
    out = []
    for (m, team, slug), r in seen.items():
        j, key = jor.get(m), xw.key_of(r)
        if j is None or not key or pos.get(key) is None:
            continue
        cut = stamp(scraped[m]) - timedelta(hours=LEAD_H)
        line = [p for t, p in snaps[(team, slug)] if t <= cut]
        mins = minutes_played(r["role"], r.get("minute"))
        out.append({"key": key, "pos": pos[key], "j": j, "at": scraped[m],
                    "line": line[-1] if line else None,
                    "share": min(1.0, mins / MATCH_LEN), "mins": mins,
                    "pts": points.get((key, j), 0.0)})
    return sorted(out, key=lambda r: r["at"])


def pairs(data):
    from ffcore.tidy import MATCH_LEN

    mine, league, out = {}, {}, []
    for r in data:
        a = mine.setdefault(r["key"], [0.0, 0.0, []])
        g = league.setdefault(r["pos"], [0.0, 0.0])
        if r["j"] >= WARMUP and r["line"] is not None and a[2] and g[1]:
            prior = g[0] / (g[1] / MATCH_LEN)
            rate = (PRIOR_90 * prior + a[0]) / (PRIOR_90 + a[1] / MATCH_LEN)
            out.append((r, rate, r["line"], sum(a[2]), len(a[2])))
        a[0] += r["pts"]; a[1] += r["mins"]; a[2].append(r["share"])
        g[0] += r["pts"]; g[1] += r["mins"]
    return out


def mse(sample, k):
    return st.mean((r["pts"] - rate * (k * line + tot) / (k + n)) ** 2
                   for r, rate, line, tot, n in sample)


def fit_lineup_weight(data=None) -> float | None:
    p = pairs(data if data is not None else rows())
    if len(p) < MIN_ROWS:
        return None
    train = p[:int(0.6 * len(p))]
    return min(GRID, key=lambda k: mse(train, k))


def fit_status_factors(lineups=None, starters=None) -> dict[str, float]:
    from ffcore.tidy import (MATCH_LEN, minutes_played, LINEUP_SOURCE, newest,
                             table)

    play, scraped, teams = {}, {}, collections.defaultdict(set)
    for r in (starters if starters is not None else newest("starters")):
        if r.get("role") in ("starter", "sub"):
            m = r["match_id"]
            scraped[m] = min(scraped.get(m, "9"), r["observed_at"])
            teams[m].add(r["team_slug"])
            play[(m, r["team_slug"], r["player_slug"])] = min(
                1.0, minutes_played(r["role"], r.get("minute")) / MATCH_LEN)
    flags = collections.defaultdict(list)
    for r in sorted(lineups if lineups is not None else table("lineups", LINEUP_SOURCE),
                    key=lambda r: r["observed_at"]):
        flags[(r["team_slug"], r["player_slug"])].append(
            (stamp(r["observed_at"]), r.get("status") or "ok"))
    earlier, seen = collections.defaultdict(list), collections.defaultdict(list)
    for m in sorted(scraped, key=scraped.get):
        cut = stamp(scraped[m]) - timedelta(hours=LEAD_H)
        for who, marks in flags.items():
            hist = earlier[who]
            before = [f for t, f in marks if t <= cut]
            if who[0] in teams[m] and before and len(hist) >= 2 \
                    and st.mean(hist) >= REGULAR:
                seen[before[-1]].append(play.get((m, *who), 0.0))
        for (mm, team, slug), share in play.items():
            if mm == m:
                earlier[(team, slug)].append(share)
    base = st.mean(seen["ok"]) if seen["ok"] else 0.0
    return {f: st.mean(v) / base for f, v in seen.items()
            if base > 0 and f != "ok" and len(v) >= MIN_STATUS_ROWS}


def _selftest() -> None:
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
    starters, lineups = [], []
    for m in range(1, 9):
        for i in range(50):
            who = "p%d" % i
            flag = "injured" if i < 25 and m > 3 else "doubt" if i < 40 and \
                m > 3 and i % 2 else "ok"
            lineups.append({"team_slug": "t", "player_slug": who,
                            "observed_at": "2026-09-%02dT0800Z" % m,
                            "status": flag})
            plays = flag == "ok" or (flag == "doubt" and m % 2)
            if plays:
                starters.append({"match_id": str(m), "team_slug": "t",
                                 "player_slug": who, "role": "starter",
                                 "minute": "", "observed_at":
                                 "2026-09-%02dT2000Z" % (m + 1)})
    got = fit_status_factors(lineups, starters)
    assert got["injured"] < 0.05 and 0.3 < got["doubt"] < 0.7, got
    assert fit_status_factors(lineups[:10], starters[:10]) == {}
    print("ffcore.lineupweight self-test OK")


if __name__ == "__main__":
    _selftest()
