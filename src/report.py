"""
report.py — daily decision report, written as markdown into the repo.

Renders in the GitHub mobile app, so it's readable anywhere with no hosting.
Run after ff_ingest.py parse.

    python report.py

Before the season starts there are no points, so there are no expected points.
This covers what the data supports today: your squad and market momentum.
Recruitment lives in reports/watchlist.md, which knows who owns whom.

reports/latest.md is the file to scroll; the dated copy goes in
reports/history/ so the folder stays short.
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
HISTORY = REPORTS / "history"
SQUAD_FILE = None  # resolved at runtime by input_path()


def input_path(name: str) -> Path:
    """Locate an editable input file. Prefers inputs/<name>; falls back to the
    repo root so a half-finished move doesn't break the run."""
    p = Path("inputs") / name
    return p if p.exists() else Path(name)


def fold(s) -> str:
    """Lowercase, strip accents — so 'Iñigo' matches 'inigo'."""
    return "".join(
        c for c in unicodedata.normalize("NFD", (s or "").lower())
        if unicodedata.category(c) != "Mn"
    )


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def latest_only(rows: list[dict]) -> list[dict]:
    if not rows:
        return []
    newest = max(r["observed_at"] for r in rows)
    return [r for r in rows if r["observed_at"] == newest]


def num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def eur(v) -> str:
    v = num(v)
    if abs(v) >= 1_000_000:
        return f"{v/1_000_000:.2f}M"
    if abs(v) >= 1_000:
        return f"{v/1_000:.0f}K"
    return f"{v:.0f}"


def load_squad() -> list[str]:
    path = input_path("squad.txt")
    if not path.exists():
        return []
    return [
        ln.strip() for ln in path.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]


def main() -> None:
    market = latest_only(read_csv(TIDY / "market.csv"))
    xi = latest_only(read_csv(TIDY / "probable_xi.csv"))

    REPORTS.mkdir(exist_ok=True)
    HISTORY.mkdir(parents=True, exist_ok=True)

    if not market:
        # Write a report saying so rather than crashing — a missing file is
        # indistinguishable from a broken workflow otherwise.
        (REPORTS / "latest.md").write_text(
            "# Fantasy report\n\nNo market data found in "
            f"`{TIDY/'market.csv'}`. Did `ff_ingest.py parse` run?\n",
            encoding="utf-8")
        print("no market data; wrote placeholder report")
        return

    lookup = {}
    for r in market:
        if r.get("slug"):
            lookup[r["slug"]] = r
        if r.get("name"):
            lookup[fold(r["name"])] = r

    start_pct: dict[str, float] = {}
    status: dict[str, str] = {}
    for r in xi:
        # Names are the only key both files share — neither page exposes
        # player links, so slugs are unavailable on the team pages.
        key = fold(r.get("player_name")) or r.get("player_slug")
        if not key:
            continue
        p = num(r.get("start_pct"), -1)
        if p >= 0:
            start_pct[key] = max(start_pct.get(key, 0), p)
        if r.get("status") and r["status"] != "ok":
            status[key] = r["status"]

    observed = market[0]["observed_at"]
    out: list[str] = [
        f"# Fantasy report — {observed}",
        "",
        f"_{len(market)} players, {len(start_pct)} with a probable-XI reading._",
        "",
    ]

    # --- squad ------------------------------------------------------------
    squad = load_squad()
    out += ["## Your squad", ""]
    if not squad:
        out += ["_Empty — add names to `squad.txt`, one per line._", ""]
    else:
        total = 0.0
        out.append("| Player | Team | Value | 24h | Start% | |")
        out.append("|---|---|--:|--:|--:|---|")
        for s in squad:
            r = lookup.get(s) or lookup.get(fold(s))
            if not r:
                out.append(f"| `{s}` | ? | — | — | — | **not found** |")
                continue
            total += num(r["value"])
            key = fold(r["name"])
            flag = {"injured": "INJ", "doubt": "?"}.get(status.get(key, ""), "")
            pct = start_pct.get(key)
            pct_s = f"{pct:.0f}%" if pct is not None else "—"
            out.append(
                f"| {r['name']} | {r['team']} | {eur(r['value'])} | "
                f"{eur(r['delta_1d'])} | {pct_s} | {flag} |"
            )
        out += ["", f"**Squad value: {eur(total)}** — compare with the app's "
                    "team value; a mismatch means a name matched the wrong "
                    "player.", ""]

    # --- momentum ---------------------------------------------------------
    movers = [r for r in market if num(r.get("delta_1d")) != 0]
    movers.sort(key=lambda r: num(r["delta_1d"]), reverse=True)

    def mover_table(rows, title):
        out.append(f"## {title}")
        out.append("")
        if not rows:
            out.extend(["_No movement recorded._", ""])
            return
        out.append("| Player | Team | Value | 24h | % |")
        out.append("|---|---|--:|--:|--:|")
        for r in rows:
            out.append(
                f"| {r['name']} | {r['team']} | {eur(r['value'])} | "
                f"{eur(r['delta_1d'])} | {num(r['delta_pct_1d']):+.2f}% |"
            )
        out.append("")

    mover_table(movers[:12], "Rising fastest (24h)")
    mover_table(list(reversed(movers[-12:])), "Falling fastest (24h)")

    out += [
        "---",
        "",
        "Recruitment targets live in `reports/watchlist.md` — it filters out "
        "players your rivals already own.",
        "",
        "**No expected points yet** — no jornada has been played. Once results "
        "exist this gains a points model and a best-XI recommendation.",
        "",
        f"_Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC._",
    ]

    text = "\n".join(out) + "\n"
    (REPORTS / "latest.md").write_text(text, encoding="utf-8")
    (HISTORY / f"{observed[:10]}.md").write_text(text, encoding="utf-8")
    print(f"wrote reports/latest.md ({len(text)} chars)")


if __name__ == "__main__":
    main()
