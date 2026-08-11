"""
bids.py — maintain the auction log and learn what your rivals pay.

You fill in: date, player, my_bid, outcome, n_bids (optional), notes.
This fills in: value_at_bid (from the nearest snapshot) and premium_pct.

    outcome: pending | won | lost | outbid

Why bother: with only two rivals, ~10 jornadas of outcomes gives you their
empirical bid premium. That replaces guessed bid sizing with a real
distribution — the single biggest upgrade available before a points model.

Writes reports/bids.md.
"""

from __future__ import annotations

import csv
import os
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(os.environ.get("FF_ROOT", "./data"))
TIDY = ROOT / "tidy"
REPORTS = Path("reports")
BIDS = None  # resolved at runtime by input_path()
FIELDS = ["date", "player", "my_bid", "outcome", "n_bids",
          "value_at_bid", "premium_pct", "notes"]


def input_path(name: str) -> Path:
    """Locate an editable input file. Prefers inputs/<name>; falls back to the
    repo root so a half-finished move doesn't break the run."""
    p = Path("inputs") / name
    return p if p.exists() else Path(name)


def fold(s) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", (s or "").lower())
                   if unicodedata.category(c) != "Mn")


def num(v, d=None):
    """Parse a euro amount. Dots are thousands separators in the app's
    formatting (9.117.522), so they are stripped."""
    try:
        return float(str(v).replace(".", "").replace(",", "").strip())
    except (TypeError, ValueError):
        return d


def pct(v, d=None):
    """Parse a percentage — here the dot IS a decimal point, so num() would
    turn 2.37 into 237."""
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return d


def eur(v) -> str:
    if v is None:
        return "—"
    return f"{v/1_000_000:.2f}M" if abs(v) >= 1e6 else f"{v/1000:.0f}K"


def market_rows() -> list[dict]:
    p = TIDY / "market.csv"
    if not p.exists():
        return []
    with p.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def value_near(rows, player: str, date: str):
    """Value from the snapshot closest to the bid date — not the newest one.
    Using today's value for a bid placed last week would silently corrupt
    every premium figure in the log."""
    pf = fold(player)
    hits = [r for r in rows if pf in fold(r["name"])]
    if not hits:
        return None
    same_day = [r for r in hits if r["observed_at"][:10] <= date] or hits
    best = max(same_day, key=lambda r: r["observed_at"])
    return num(best["value"])


def main() -> None:
    REPORTS.mkdir(exist_ok=True)
    bids_file = input_path("bids.csv")
    if not bids_file.exists():
        print("no bids.csv")
        return

    rows = list(csv.DictReader(bids_file.open(encoding="utf-8")))
    market = market_rows()

    for r in rows:
        if not r.get("value_at_bid") and market:
            v = value_near(market, r["player"], r.get("date", "9999"))
            if v:
                r["value_at_bid"] = f"{v:.0f}"
        bid, val = num(r.get("my_bid")), num(r.get("value_at_bid"))
        if bid and val:
            r["premium_pct"] = f"{(bid/val - 1)*100:.2f}"

    with bids_file.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows([{k: r.get(k, "") for k in FIELDS} for r in rows])

    settled = [r for r in rows if r.get("outcome") in ("won", "lost", "outbid")]
    won = [r for r in settled if r["outcome"] == "won"]

    out = ["# Bid log", "",
           f"{len(rows)} bids, {len(settled)} settled, {len(won)} won.", ""]

    if settled:
        prem = [pct(r.get("premium_pct")) for r in settled]
        prem = [p for p in prem if p is not None]
        if prem:
            out += [f"Your premium over value: median "
                    f"{sorted(prem)[len(prem)//2]:.1f}%, "
                    f"range {min(prem):.1f}% to {max(prem):.1f}%.", ""]
        lost = [pct(r.get("premium_pct")) for r in settled
                if r["outcome"] != "won"]
        lost = [p for p in lost if p is not None]
        if lost:
            out += [f"**Losing bids averaged {sum(lost)/len(lost):.1f}% over "
                    f"value** — rivals are paying more than that.", ""]
    else:
        out += ["_Nothing settled yet. Set `outcome` to won/lost/outbid as "
                "auctions resolve._", ""]

    out += ["| Date | Player | Bid | Value | Premium | Outcome | Bids |",
            "|---|---|--:|--:|--:|---|--:|"]
    for r in sorted(rows, key=lambda r: r.get("date", ""), reverse=True):
        out.append(
            f"| {r.get('date','')} | {r.get('player','')} | "
            f"{eur(num(r.get('my_bid')))} | {eur(num(r.get('value_at_bid')))} | "
            f"{r.get('premium_pct','') or '—'}% | {r.get('outcome','')} | "
            f"{r.get('n_bids','') or '—'} |")

    out += ["", "---", "", "Record every auction, including losses — a loss at "
            "a known premium is what tells you where rivals actually sit.", "",
            f"_Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC._"]

    (REPORTS / "bids.md").write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"wrote reports/bids.md ({len(rows)} bids)")


if __name__ == "__main__":
    main()
