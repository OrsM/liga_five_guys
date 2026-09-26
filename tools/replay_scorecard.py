from __future__ import annotations

import collections
import datetime as dt
import json
import random
import statistics as st
import sys

sys.path.insert(0, "src")
import backtest as B  # noqa: E402

HORIZON = B.HORIZON_DAYS
FIELDS = ("d_pts", "pts_lo", "d_win")


def graded():
    commits = B.commits_touching("reports/decisions.json")
    points_between, now = B._actuals_index()
    rows, seen = [], set()
    for sha, when in commits:
        if (now - when).days < HORIZON:
            continue
        text = B._show(sha, "reports/decisions.json")
        try:
            moves = (json.loads(text) if text else {}).get("moves") or []
        except ValueError:
            continue
        until = when + dt.timedelta(days=HORIZON)
        for rank, m in enumerate(moves):
            key = (m.get("buy"), m.get("sell"), when.date())
            if not m.get("buy") or not m.get("sell") or key in seen:
                continue
            seen.add(key)
            bought = points_between(m["buy"], when, until)
            sold = sum(points_between(n.strip(), when, until)
                       for n in m["sell"].split(" + "))
            rows.append({"day": when.date(), "rank": rank, "kind": m.get("kind"),
                         "net": bought - sold,
                         **{f: m.get(f) for f in FIELDS}})
    return rows


def main():
    rows = graded()
    days = collections.defaultdict(list)
    for r in rows:
        days[r["day"]].append(r)
    print("%d graded (move, day) pairs over %d days, %g-day horizon\n"
          % (len(rows), len(days), HORIZON))
    print("%-6s %5s %9s %6s" % ("rank", "n", "mean net", "wins"))
    for k in range(5):
        v = [r["net"] for r in rows if min(r["rank"], 4) == k]
        if v:
            print("#%d%-4s %5d %+9.2f %5.0f%%" % (k + 1, "+" if k == 4 else "",
                  len(v), st.mean(v), 100 * sum(x > 0 for x in v) / len(v)))
    diffs = []
    for rs in days.values():
        top = min(r["rank"] for r in rs)
        first = [r["net"] for r in rs if r["rank"] == top]
        rest = [r["net"] for r in rs if r["rank"] != top]
        if rest:
            diffs.append(st.mean(first) - st.mean(rest))
    rng = random.Random(1)
    boot = sorted(st.mean(rng.choices(diffs, k=len(diffs))) for _ in range(2000))
    print("\ntop move minus that day's other moves: %+.2f pts (95%% %+.2f .. "
          "%+.2f, %d days)" % (st.mean(diffs), boot[50], boot[1950], len(diffs)))
    for kind in sorted({r["kind"] for r in rows if r["kind"]}):
        v = [r["net"] for r in rows if r["kind"] == kind]
        print("  %-7s n=%3d mean %+.2f" % (kind, len(v), st.mean(v)))
    print("\ncorrelation of the report's own prediction with the realised net:")
    for f in FIELDS:
        pairs = [(r[f], r["net"]) for r in rows if isinstance(r[f], (int, float))]
        if len(pairs) > 20:
            a, b = zip(*pairs)
            print("  %-7s %+.2f (n=%d)" % (f, st.correlation(a, b), len(pairs)))


if __name__ == "__main__":
    main()
