"""
find_slug.py — look up a player's slug from the collected market data.

    python find_slug.py starfelt
    python find_slug.py "le norm"        # truncated app names work fine
    python find_slug.py --team celta     # everyone at one club
    python find_slug.py --check squad.txt

Accent-insensitive, substring-based. The app truncates names ("Le Norm...",
"Dani Lor..."), so partial matches are the normal case, not the exception.
"""

from __future__ import annotations

import csv
import os
import sys
import unicodedata
from pathlib import Path

TIDY = Path(os.environ.get("FF_ROOT", "./data")) / "tidy"


def fold(s: str) -> str:
    """Lowercase and strip accents, so 'Iñigo' matches 'inigo'."""
    return "".join(
        c for c in unicodedata.normalize("NFD", (s or "").lower())
        if unicodedata.category(c) != "Mn"
    )


def latest_market() -> list[dict]:
    path = TIDY / "market.csv"
    if not path.exists():
        sys.exit(f"{path} not found — run ff_ingest.py fetch && parse first.")
    with path.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        sys.exit("market.csv is empty.")
    newest = max(r["observed_at"] for r in rows)
    return [r for r in rows if r["observed_at"] == newest]


def show(rows: list[dict]) -> None:
    if not rows:
        print("  no match")
        return
    for r in sorted(rows, key=lambda r: -float(r.get("value") or 0)):
        val = float(r.get("value") or 0) / 1_000_000
        print(f"  {r['slug']:<32} {r['name'][:24]:<24} "
              f"{r['team'][:14]:<14} {r['position'][:12]:<12} {val:>7.2f}M")


def check(path: Path) -> None:
    """Validate a squad file: report which slugs don't resolve."""
    rows = latest_market()
    market = {r["slug"]: r for r in rows if r.get("slug")}
    # Names are the practical key until slugs are re-collected, so accept either.
    market.update({fold(r["name"]): r for r in rows if r.get("name")})
    slugs = [
        ln.strip() for ln in path.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    ok, bad = [], []
    resolved = {}
    for s in slugs:
        hit = market.get(s) or market.get(fold(s))
        if hit:
            resolved[s] = hit
            ok.append(s)
        else:
            bad.append(s)
    print(f"{len(ok)}/{len(slugs)} resolved")
    if ok:
        print("\nresolved:")
        show([resolved[s] for s in ok])
    if bad:
        print("\nNOT FOUND — fix these:")
        for s in bad:
            print(f"  {s}")
            # Offer near misses so the fix is obvious.
            stem = fold(s).replace("-", " ").split()[-1]
            near = [r for r in market.values()
                    if stem in fold(r["slug"] or "") or stem in fold(r["name"])]
            for r in near[:3]:
                print(f"      did you mean: {r['slug']}  ({r['name']}, {r['team']})")


def main() -> None:
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__)

    if args[0] == "--many":
        # Resolve several surnames at once and print ready-to-paste squad lines.
        market = latest_market()
        print("# paste the lines below into squad.txt")
        for q in args[1:]:
            qf = fold(q)
            hits = [r for r in market if qf in fold(r["name"])]
            if len(hits) == 1:
                print(hits[0]["name"])
            elif not hits:
                print(f"# NO MATCH for '{q}' — try a shorter fragment")
            else:
                print(f"# AMBIGUOUS '{q}' — pick one:")
                for r in sorted(hits, key=lambda r: -float(r.get("value") or 0))[:6]:
                    print(f"#   {r['name']}  ({r['team']}, {r['position']}, "
                          f"{float(r['value'])/1e6:.2f}M)")
        return

    if args[0] == "--check":
        check(Path(args[1] if len(args) > 1 else "squad.txt"))
        return

    market = latest_market()

    if args[0] == "--team":
        want = fold(args[1])
        show([r for r in market if want in fold(r["team"])])
        return

    q = fold(" ".join(args))
    hits = [r for r in market if q in fold(r["name"]) or q in fold(r["slug"])]
    if not hits:
        # Fall back to matching any single word — handles "ruiz de galarreta"
        # when the app showed "De Galar...".
        words = q.split()
        hits = [r for r in market
                if any(w in fold(r["name"]) or w in fold(r["slug"]) for w in words)]
    show(hits)


if __name__ == "__main__":
    main()
