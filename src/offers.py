"""
offers.py — rank whatever is currently purchasable in your league.

Your league's market is private to the app, so the list of who's on offer has
to come from you. Fill offers.txt when you're deciding, not continuously —
the free-agent slate rotates every few hours and rival listings expire in 3
days, so a maintained file would always be stale.

offers.txt format, one per line:
    martin satriano
    matias dituro, 6900000        # optional asking price from the app

Writes reports/offers.md.
"""

from __future__ import annotations

import csv
import os
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ROOT = Path(os.environ.get("FF_ROOT", "./data"))
TIDY = ROOT / "tidy"
REPORTS = Path("reports")
OFFERS_FILE = None  # resolved at runtime by input_path()


def input_path(name: str) -> Path:
    """Locate an editable input file. Prefers inputs/<name>; falls back to the
    repo root so a half-finished move doesn't break the run."""
    p = Path("inputs") / name
    return p if p.exists() else Path(name)


def fold(s) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", (s or "").lower())
                   if unicodedata.category(c) != "Mn")


def read_csv(p: Path) -> list[dict]:
    if not p.exists():
        return []
    with p.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def latest(rows):
    if not rows:
        return []
    newest = max(r["observed_at"] for r in rows)
    return [r for r in rows if r["observed_at"] == newest]


def num(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def eur(v) -> str:
    v = num(v)
    if abs(v) >= 1_000_000:
        return f"{v/1_000_000:.2f}M"
    if abs(v) >= 1_000:
        return f"{v/1_000:.0f}K"
    return f"{v:.0f}"


def main() -> None:
    REPORTS.mkdir(exist_ok=True)
    market = latest(read_csv(TIDY / "market.csv"))
    xi = latest(read_csv(TIDY / "probable_xi.csv"))

    if not market:
        (REPORTS / "offers.md").write_text("# Offers\n\nNo market data.\n")
        return

    index = {fold(r["name"]): r for r in market}

    def resolve(q: str):
        """Exact, then substring, then token match. Returns (row, candidates)."""
        qf = fold(q)
        if qf in index:
            return index[qf], []
        subs = [r for r in market if qf in fold(r["name"])]
        if len(subs) == 1:
            return subs[0], []
        if not subs:
            # "C. Dominguez" -> drop initials and punctuation, match remaining
            # words. The app abbreviates first names; the CSV spells them out.
            toks = [t for t in fold(q).replace(".", " ").split() if len(t) > 1]
            if toks:
                subs = [r for r in market
                        if all(t in fold(r["name"]) for t in toks)]
                if len(subs) == 1:
                    return subs[0], []
        return None, subs
    start, status = {}, {}
    for r in xi:
        k = fold(r.get("player_name"))
        if not k:
            continue
        p = num(r.get("start_pct"), -1)
        if p >= 0:
            start[k] = max(start.get(k, 0), p)
        if r.get("status") and r["status"] != "ok":
            status[k] = r["status"]

    offers_file = input_path("offers.txt")
    if not offers_file.exists():
        (REPORTS / "offers.md").write_text(
            "# Offers\n\nCreate `inputs/offers.txt` with one player per line "
            "(optionally `name, asking price`) and re-run.\n")
        print("no offers.txt")
        return

    rows = []
    for line in offers_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, _, price = line.partition(",")
        r, candidates = resolve(name.strip())
        if not r:
            rows.append({"missing": name.strip(), "candidates": candidates})
            continue
        k = fold(r["name"])
        value = num(r["value"])
        ask = num(price.replace(".", "").replace(" ", "")) if price.strip() else None
        rows.append({
            "name": r["name"], "team": r["team"], "pos": r["position"],
            "value": value, "delta": num(r["delta_1d"]),
            "pct": start.get(k), "status": status.get(k, ""),
            "ask": ask,
            # If the app's price is genuinely yesterday's value, a riser is
            # bought at a discount to what it's worth today.
            "edge": (value - ask) if ask else num(r["delta_1d"]),
        })

    found = [r for r in rows if "missing" not in r]
    # Rank by start probability first — a non-starter scores nothing, so no
    # amount of price edge rescues one. Price edge breaks ties.
    found.sort(key=lambda r: (-(r["pct"] or 0), -r["edge"] / max(r["value"], 1)))

    out = [f"# Offers — {market[0]['observed_at']}", "",
           "Ranked by start probability, then price edge. "
           "**Edge** is value minus asking price (or the 24h move if you "
           "didn't give a price): positive means you pay less than today's "
           "value.", "",
           "| Player | Team | Pos | Value | Ask | Edge | 24h | Start% | |",
           "|---|---|---|--:|--:|--:|--:|--:|---|"]
    for r in found:
        flag = {"injured": "INJ", "doubt": "?"}.get(r["status"], "")
        pct = f"{r['pct']:.0f}%" if r["pct"] is not None else "—"
        ask = eur(r["ask"]) if r["ask"] else "—"
        out.append(
            f"| {r['name']} | {r['team']} | {r['pos'][:3]} | "
            f"{eur(r['value'])} | {ask} | {eur(r['edge'])} | "
            f"{eur(r['delta'])} | {pct} | {flag} |"
        )

    missing = [r for r in rows if "missing" in r]
    if missing:
        out += ["", "**Unresolved:**", ""]
        for m in missing:
            if m["candidates"]:
                names = ", ".join(f"`{c['name']}` ({c['team']})"
                                  for c in m["candidates"][:5])
                out.append(f"- **{m['missing']}** — ambiguous: {names}")
            else:
                out.append(f"- **{m['missing']}** — no match")

    out += ["", "---", "",
            "No expected-points model yet, so this ranks on start probability "
            "and price only. A 90% starter at a weak club still scores less "
            "than a 70% starter at a strong one — use judgement.", "",
            f"_Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC._"]

    (REPORTS / "offers.md").write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"wrote reports/offers.md ({len(found)} players)")


if __name__ == "__main__":
    main()
