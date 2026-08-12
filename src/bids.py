"""
bids.py — maintain the auction log and learn what your rivals pay.

You fill in: date, player, my_bid, outcome, n_bids (optional), notes.
This fills in: value_at_bid (from the nearest snapshot), value_lag_h and
premium_pct.

    outcome: pending | won | lost | outbid

Why bother: with four rivals, ~10 jornadas of outcomes gives you their
empirical bid premium. That replaces guessed bid sizing with a real
distribution — the single biggest upgrade available before a points model.

Writes reports/bids.md.

MIGRATED onto ffcore. Three behaviour changes worth knowing:

  * Dates are read as Europe/Madrid, because that is the clock the app
    showed you. They used to be compared against UTC snapshot stamps as if
    both were the same, which pulled in a snapshot up to two hours after the
    bid — the direction that makes an overpay look like a bargain.
  * value_near() is gone; Market.at() in ffcore.tidy does it for every
    script. It refuses ambiguous names instead of taking the first substring
    hit, so a bid on a name matching two players is now reported as
    unresolved rather than silently priced against the wrong one.
  * A value_lag_h column is added on the next run. It records how stale the
    snapshot was, so a premium built on a two-day-old value can be told
    apart from one built on a two-hour-old value.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ffcore.parse import money, ratio  # noqa: E402
from ffcore.tidy import (REPORTS, Market, input_path, ledger_stamp,  # noqa: E402
                         load_market, read_csv, write_csv, write_lines)

FIELDS = ["date", "player", "my_bid", "outcome", "n_bids",
          "value_at_bid", "value_lag_h", "premium_pct", "notes"]

SETTLED = ("won", "lost", "outbid")

# Beyond this the snapshot is too old for the premium to mean much. The
# figure is still written; the report just stops averaging it in.
MAX_LAG_H = 36.0


def eur(v) -> str:
    """Moves to ffcore.render once the other reports are migrated."""
    if v is None:
        return "—"
    return f"{v/1_000_000:.2f}M" if abs(v) >= 1e6 else f"{v/1000:.0f}K"


def enrich(rows: list[dict], market: Market) -> list[str]:
    """Fill value_at_bid / value_lag_h / premium_pct in place.

    Returns the names that could not be resolved, so the report can ask you
    to fix them rather than quietly dropping them from the statistics.
    """
    unresolved = []
    for r in rows:
        name = (r.get("player") or "").strip()
        if not r.get("value_at_bid") and len(market):
            v = market.at(name, ledger_stamp(r.get("date", "")))
            if v:
                r["value_at_bid"] = f"{v.value:.0f}"
                r["value_lag_h"] = f"{v.lag_h:.1f}"
            elif name:
                unresolved.append(name)
        bid, val = money(r.get("my_bid")), money(r.get("value_at_bid"))
        if bid and val:
            r["premium_pct"] = f"{(bid / val - 1) * 100:.2f}"
    return unresolved


def fresh(r: dict) -> bool:
    lag = ratio(r.get("value_lag_h"))
    return lag is None or abs(lag) <= MAX_LAG_H


def main() -> None:
    bids_file = input_path("bids.csv")
    if not bids_file.exists():
        print("no bids.csv")
        return

    rows = read_csv(bids_file)
    market = Market(load_market())
    unresolved = enrich(rows, market)
    write_csv(bids_file, [{k: r.get(k, "") or "" for k in FIELDS}
                          for r in rows], FIELDS)

    settled = [r for r in rows if r.get("outcome") in SETTLED]
    won = [r for r in settled if r["outcome"] == "won"]

    out = ["# Bid log", "",
           f"{len(rows)} bids, {len(settled)} settled, {len(won)} won.", ""]

    usable = [r for r in settled if fresh(r)]
    stale = len(settled) - len(usable)

    if usable:
        prem = [ratio(r.get("premium_pct")) for r in usable]
        prem = sorted(p for p in prem if p is not None)
        if prem:
            out += [f"Your premium over value: median "
                    f"{prem[len(prem)//2]:.1f}%, "
                    f"range {prem[0]:.1f}% to {prem[-1]:.1f}%.", ""]
        lost = [ratio(r.get("premium_pct")) for r in usable
                if r["outcome"] != "won"]
        lost = [p for p in lost if p is not None]
        if lost:
            out += [f"**Losing bids averaged {sum(lost)/len(lost):.1f}% over "
                    f"value** — rivals are paying more than that.", ""]
    else:
        out += ["_Nothing settled yet. Set `outcome` to won/lost/outbid as "
                "auctions resolve._", ""]

    if stale:
        out += [f"_{stale} settled bid(s) priced against a snapshot more than "
                f"{MAX_LAG_H:.0f}h away and left out of the figures above._",
                ""]

    out += ["| Date | Player | Bid | Value | Premium | Outcome | Bids |",
            "|---|---|--:|--:|--:|---|--:|"]
    for r in sorted(rows, key=lambda r: r.get("date", ""), reverse=True):
        mark = "" if fresh(r) else " ~"
        out.append(
            f"| {r.get('date','')} | {r.get('player','')} | "
            f"{eur(money(r.get('my_bid')))} | "
            f"{eur(money(r.get('value_at_bid')))}{mark} | "
            f"{r.get('premium_pct','') or '—'}% | {r.get('outcome','')} | "
            f"{r.get('n_bids','') or '—'} |")

    if unresolved:
        out += ["", "**Unresolved names** — no unique match in market.csv. "
                "Check them with find_slug.py; until then they carry no "
                "value and no premium.", ""]
        out += [f"- {n}" for n in sorted(set(unresolved))]

    out += ["", "---", "", "Record every auction, including losses — a loss at "
            "a known premium is what tells you where rivals actually sit.",
            "", "`~` marks a value read from a snapshot more than "
            f"{MAX_LAG_H:.0f}h from the bid.", "",
            f"_Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC._"]

    REPORTS.mkdir(exist_ok=True)
    write_lines(REPORTS / "bids.md", out)
    print(f"{len(rows)} bids, {len(unresolved)} unresolved")


if __name__ == "__main__":
    main()
