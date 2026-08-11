"""Partition the player universe into owned vs buyable.

Reads:
  inputs/owned.txt        baseline rosters, one section per manager
  inputs/transactions.csv the ledger — applied on top of the baseline
  data/tidy/*.csv         values, 24h moves, start probabilities

Writes:
  reports/rivals.md       every rival squad, plus what they pay over value
  reports/watchlist.md    everyone unowned, cut to something phone-readable

Run via workflow_dispatch. No arguments.
"""

import csv
import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (norm, load_players, fmt_money, fmt_pct, pos_key,
                    POS_ORDER)  # noqa: E402

ME = "me"                 # section name in owned.txt for your own squad
MARKET = "market"         # reserved counterparty: the free-agent pool

# Watchlist cuts — the whole point is that it fits on a phone screen.
MIN_START = 60.0          # ignore anyone below this start probability
TOP_N_PER_POS = 8         # rows per position
CASH = None               # set an int (euros) to hide unaffordable players


def read_owned(path="inputs/owned.txt"):
    """[manager] sections, one player name per line, # for comments."""
    rosters, current = {}, None
    if not os.path.exists(path):
        raise SystemExit("missing %s" % path)
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            if line.startswith("[") and line.endswith("]"):
                current = line[1:-1].strip()
                rosters.setdefault(current, [])
            elif current:
                rosters[current].append(line)
    return rosters


def read_transactions(path="inputs/transactions.csv"):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        lines = [ln for ln in fh if not ln.lstrip().startswith("#")]
    rows = [r for r in csv.DictReader(lines)
            if r.get("player") and not r["player"].lstrip().startswith("#")]
    rows.sort(key=lambda r: (r.get("date") or ""))
    return rows


def apply_transactions(rosters, txns):
    """Baseline + ledger = current ownership. Ledger wins."""
    owner = {}
    for mgr, names in rosters.items():
        for n in names:
            owner[norm(n)] = mgr
    warnings = []
    for t in txns:
        key = norm(t["player"])
        src = (t.get("from") or "").strip() or MARKET
        dst = (t.get("to") or "").strip() or MARKET
        if src != MARKET and owner.get(key) not in (src, None):
            warnings.append("%s: %s was not owned by %s" % (
                t.get("date", "?"), t["player"], src))
        if dst == MARKET:
            owner.pop(key, None)
        else:
            owner[key] = dst
    return owner, warnings


def row(rec):
    return "| %s | %s | %s | %s | %s | %s |" % (
        rec.get("name", "?"),
        rec.get("team", "—"),
        (rec.get("pos") or "—")[:3],
        fmt_money(rec.get("value")),
        fmt_money(rec.get("delta_1d")),
        fmt_pct(rec.get("start")),
    )


HEAD = "| Player | Team | Pos | Value | 24h | Start% |\n|---|---|---|--:|--:|--:|"


def write_rivals(players, owner, txns, warnings, stamp):
    by_mgr = {}
    for key, mgr in owner.items():
        by_mgr.setdefault(mgr, []).append(key)

    out = ["# Squads — %s" % stamp, ""]
    for mgr in sorted(by_mgr, key=lambda m: (m != ME, m)):
        keys = by_mgr[mgr]
        recs = [players.get(k, {"name": k}) for k in keys]
        total = sum(r.get("value") or 0 for r in recs)
        starters = sum(1 for r in recs if (r.get("start") or 0) >= 70)
        out += ["## %s" % ("You" if mgr == ME else mgr),
                "%d players · %s total · %d at 70%%+" % (
                    len(recs), fmt_money(total), starters),
                "", HEAD]
        recs.sort(key=lambda r: (pos_key(r.get("pos")),
                                 -(r.get("value") or 0)))
        out += [row(r) for r in recs]
        out.append("")

    paid = [t for t in txns if (t.get("price") or "").strip()]
    if paid:
        out += ["## What they pay", "",
                "| Date | Player | From → To | Price |",
                "|---|---|---|--:|"]
        for t in paid[-25:]:
            out.append("| %s | %s | %s → %s | %s |" % (
                t.get("date", "?"), t["player"],
                t.get("from") or MARKET, t.get("to") or MARKET,
                t.get("price")))
        out.append("")

    if warnings:
        out += ["## Ledger warnings", ""] + ["- " + w for w in warnings] + [""]

    unmatched = [k for k in owner if k not in players]
    if unmatched:
        out += ["## Unmatched names", "",
                "These are in owned.txt but not in the tidy data — "
                "check spelling with find_slug.py.", ""]
        out += ["- " + u for u in sorted(unmatched)] + [""]

    _write("reports/rivals.md", out)


def write_watchlist(players, owner, stamp):
    free = [r for k, r in players.items() if k not in owner]
    out = ["# Watchlist — %s" % stamp, "",
           "Everyone not owned by the three of us, %s%% start or better."
           % int(MIN_START), ""]
    if CASH:
        out += ["Filtered to what %s of cash can reach." % fmt_money(CASH), ""]

    for pos in POS_ORDER:
        pool = [r for r in free
                if (r.get("pos") or "").lower() == pos
                and (r.get("start") or 0) >= MIN_START
                and (CASH is None or (r.get("value") or 0) <= CASH)]
        pool.sort(key=lambda r: (-(r.get("start") or 0),
                                 -(r.get("delta_1d") or 0)))
        if not pool:
            continue
        out += ["## %s" % pos, "", HEAD]
        out += [row(r) for r in pool[:TOP_N_PER_POS]]
        out.append("")

    out += ["---", "",
            "Not all of these are purchasable today — the app deals a "
            "limited slate. This is the shortlist to recognise against.", ""]
    _write("reports/watchlist.md", out)


def _write(path, lines):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines).rstrip() + "\n")
    print("wrote %s" % path)


def main():
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    players = load_players()
    rosters = read_owned()
    txns = read_transactions()
    owner, warnings = apply_transactions(rosters, txns)
    write_rivals(players, owner, txns, warnings, stamp)
    write_watchlist(players, owner, stamp)
    print("%d players known, %d owned, %d free"
          % (len(players), len(owner), len(players) - len(owner)))
    for w in warnings:
        print("WARN " + w)


if __name__ == "__main__":
    main()
