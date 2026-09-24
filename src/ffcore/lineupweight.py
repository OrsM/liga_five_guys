"""How many played matches is a line-up percentage worth? Measured, not assumed.

Every listed player-match so far (0 points if he did not play), predicted from
what was known BEFORE it: his own record and the last line-up snapshot taken
at least LEAD_H before the match page was scraped. The scrape follows the
final whistle, so a later snapshot can show the real XI -- the leak that made
an early version of this look 12% better than it was.

The forecast being judged is rate * (k*lineup + sum(past minute shares)) /
(k + n): `k` is the weight the line-up gets against the player's own minutes.
It used to be SHRINK_K (8), reused from an unrelated fit. The shares are
minutes/90 per listed match; the rate is points per 90 shrunk to his position.
Judged on points, because points are what the forecast is for: fitted to
minute share alone the best k came out at 12, on points at 2.
"""
from __future__ import annotations

import collections
import statistics as st
from datetime import datetime, timedelta

LEAD_H = 24.0
GRID = (0.5, 1.0, 2.0, 3.0, 4.0, 6.0, 8.0, 12.0)
PRIOR_90 = 4.0        # 90-minute units of position prior on a player's rate
WARMUP = 3            # jornadas of history before rows are judged
MIN_ROWS = 300


def stamp(s):
    return datetime.strptime(s, "%Y-%m-%dT%H%MZ")


def rows():
    from ffcore.tidy import (MATCH_LEN, TIDY, jornada_of_match, load_crosswalk,
                             load_lineups, load_perjornada, load_starters,
                             minutes_played, read_csv)

    xw = load_crosswalk()
    pos = {r["ff_id"]: r["position"] for r in read_csv(TIDY / "market.csv")}
    jor = jornada_of_match()
    points = {}
    for r in load_perjornada():
        if r["games_delta"] == "1":
            points[(r["ff_id"], int(r["jornada"]))] = float(r["points_delta"])
    seen, scraped = {}, {}
    for r in load_starters():
        if r.get("role") not in ("starter", "sub"):
            continue
        m = r["match_id"]
        scraped[m] = min(scraped.get(m, "9"), r["observed_at"])
        seen.setdefault((m, r["team_slug"], r["player_slug"]), r)
    snaps = collections.defaultdict(list)
    for r in sorted(load_lineups(), key=lambda r: r["observed_at"]):
        if r.get("start_pct") not in (None, ""):
            snaps[(r["team_slug"], r["player_slug"])].append(
                (stamp(r["observed_at"]), float(r["start_pct"]) / 100.0))
    out = []
    for (m, team, slug), r in seen.items():
        j, key = jor.get(m), xw.player(ff_slug=slug, name=r.get("player_name"))
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
    """(row, predicted-rate-per-90, line-up, past shares) for judged rows."""
    mine, league = collections.defaultdict(lambda: [0.0, 0.0, []]), \
        collections.defaultdict(lambda: [0.0, 0.0])
    out = []
    for r in data:
        a, g = mine[r["key"]], league[r["pos"]]
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


def fit_lineup_weight(data=None) -> tuple[float | None, str]:
    """Best k on the first 60% of judged rows, with the evidence as a line."""
    p = pairs(data if data is not None else rows())
    if len(p) < MIN_ROWS:
        return None, "only %d comparable player-matches (need %d)" % (
            len(p), MIN_ROWS)
    train, test = p[:int(0.6 * len(p))], p[int(0.6 * len(p)):]
    best = min(GRID, key=lambda k: mse(train, k))
    return best, ("line-up worth %g played matches: %.3f vs %.3f at 8 on the "
                  "%d most recent of %d player-matches" % (
                      best, mse(test, best), mse(test, 8.0), len(test),
                      len(p)))


def _selftest() -> None:
    def game(who, j, share, line):
        return {"key": who, "pos": "MED", "j": j, "at": "2026-09-%02dT1200Z" % j,
                "line": line, "share": share, "mins": 90.0 * share,
                "pts": 5.0 * share}
    # an exact line-up and a record that alternates (so history says nothing):
    # the fit has to lean on the line-up
    data = []
    for i in range(60):
        for j in range(1, 9):
            on = (i + j) % 2
            data.append(game("p%d" % i, j, float(on), float(on)))
    data.sort(key=lambda r: r["at"])
    k, why = fit_lineup_weight(data)
    assert k == max(GRID), (k, why)
    # a line-up that is noise and a steady record: the fit has to lean on history
    steady = [game("p%d" % i, j, 1.0, float((i * j) % 2)) for i in range(60)
              for j in range(1, 9)]
    steady.sort(key=lambda r: r["at"])
    k, _ = fit_lineup_weight(steady)
    assert k == min(GRID), k
    k, why = fit_lineup_weight(data[:20])
    assert k is None and "need" in why, (k, why)
    assert not pairs([game("a", 1, 1.0, 1.0)]), "no history, nothing to judge"
    print("ffcore.lineupweight self-test OK")


if __name__ == "__main__":
    _selftest()
