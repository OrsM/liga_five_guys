"""
report.py — daily decision report, written as markdown into the repo.

Renders in the GitHub mobile app, so it's readable wherever you are without
any hosting. Run after ff_ingest.py parse.

    python report.py

Scope note: before the season starts there are no points, so there are no
expected points. This report covers what the data actually supports today —
market momentum and, once probable XIs firm up, cheap likely starters.
Anything claiming to project points before a ball is kicked is decoration.
"""

from __future__ import annotations

import csv
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(os.environ.get("FF_ROOT", "./data"))
TIDY = ROOT / "tidy"
REPORTS = Path("reports")
SQUAD_FILE = Path("squad.txt")

POS_ORDER = ["portero", "defensa", "mediocampista", "centrocampista", "delantero"]
STARTER_THRESHOLD = 70  # start% at or above which we call someone "likely to start"


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def latest_only(rows: list[dict]) -> list[dict]:
    """Keep just the most recent observation. Everything is snapshot-stamped,
    so this is a point-in-time filter, not a dedupe."""
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
    if not SQUAD_FILE.exists():
        return []
    return [
        ln.strip() for ln in SQUAD_FILE.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]


def main() -> None:
    market = latest_only(read_csv(TIDY / "market.csv"))
    xi = latest_only(read_csv(TIDY / "probable_xi.csv"))

    if not market:
        raise SystemExit("No market data — run ff_ingest.py fetch && parse first.")

    by_slug = {r["slug"]: r for r in market if r.get("slug")}

    # Best available start% per player across list and pitch renderings.
    start_pct: dict[str, float] = {}
    status: dict[str, str] = {}
    for r in xi:
        s = r.get("player_slug")
        if not s:
            continue
        p = num(r.get("start_pct"), -1)
        if p >= 0:
            start_pct[s] = max(start_pct.get(s, 0), p)
        if r.get("status") and r["status"] != "ok":
            status[s] = r["status"]

    observed = market[0]["observed_at"]
    out: list[str] = [
        f"# Fantasy report — {observed}",
        "",
        f"_{len(market)} players, {len(start_pct)} with a probable-XI reading._",
        "",
    ]

    # --- your squad -------------------------------------------------------
    squad = load_squad()
    out.append("## Your squad")
    out.append("")
    if not squad:
        out += ["_Empty. Add player slugs to `squad.txt` (one per line) and this "
                "section fills in._", ""]
    else:
        out.append("| Player | Team | Value | 24h | Start% | |")
        out.append("|---|---|--:|--:|--:|---|")
        for s in squad:
            r = by_slug.get(s)
            if not r:
                out.append(f"| `{s}` | ? | — | — | — | **not found — check the slug** |")
                continue
            flag = {"injured": "🚑", "doubt": "❓"}.get(status.get(s, ""), "")
            pct = start_pct.get(s)
            out.append(
                f"| {r['name']} | {r['team']} | {eur(r['value'])} | "
                f"{eur(r['delta_1d'])} | {pct:.0f}% | {flag} |"
                if pct is not None else
                f"| {r['name']} | {r['team']} | {eur(r['value'])} | "
                f"{eur(r['delta_1d'])} | — | {flag} |"
            )
        out.append("")

    # --- momentum ---------------------------------------------------------
    movers = [r for r in market if r.get("delta_1d")]
    movers.sort(key=lambda r: num(r["delta_1d"]), reverse=True)

    def mover_table(rows, title):
        out.append(f"## {title}")
        out.append("")
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

    # --- cheap likely starters -------------------------------------------
    out.append(f"## Cheap likely starters (start% ≥ {STARTER_THRESHOLD})")
    out.append("")
    if not start_pct:
        out += ["_No probable-XI data yet. futbolfantasy publishes these in the "
                "24–48h before kickoff, so expect this to fill in on Thursday "
                "or Friday._", ""]
    else:
        buckets: dict[str, list[dict]] = defaultdict(list)
        for slug, pct in start_pct.items():
            r = by_slug.get(slug)
            if r and pct >= STARTER_THRESHOLD and slug not in squad:
                buckets[r["position"]].append({**r, "pct": pct})
        printed = False
        for pos in POS_ORDER:
            rows = sorted(buckets.get(pos, []), key=lambda r: num(r["value"]))[:6]
            if not rows:
                continue
            printed = True
            out.append(f"**{pos.title()}**")
            out.append("")
            out.append("| Player | Team | Value | Start% |")
            out.append("|---|---|--:|--:|")
            for r in rows:
                out.append(
                    f"| {r['name']} | {r['team']} | {eur(r['value'])} | {r['pct']:.0f}% |"
                )
            out.append("")
        if not printed:
            out += ["_Probable-XI readings exist but none cleared the threshold, or "
                    "they didn't match a market entry. If this persists close to "
                    "kickoff, the name/slug join is probably broken._", ""]

    out += [
        "---",
        "",
        "**No expected points yet** — no jornada has been played, so there is "
        "nothing to project from. Once results exist this report gains a points "
        "model and a best-XI recommendation (Phase 1 in the README).",
        "",
        f"_Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC._",
    ]

    REPORTS.mkdir(exist_ok=True)
    text = "\n".join(out) + "\n"
    (REPORTS / "latest.md").write_text(text, encoding="utf-8")
    (REPORTS / f"{observed[:10]}.md").write_text(text, encoding="utf-8")
    print(f"wrote reports/latest.md ({len(text)} chars)")


if __name__ == "__main__":
    main()
