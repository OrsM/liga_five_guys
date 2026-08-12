"""
find_slug.py — resolve player names against the collected market data.

    python find_slug.py starfelt              # search
    python find_slug.py --team celta          # a club's whole squad
    python find_slug.py --many starfelt duro  # several at once
    python find_slug.py --file lookup.txt     # one query per LINE
    python find_slug.py --check squad.txt     # validate a squad file

Use --file for multi-word names: the shell splits on spaces, which turns
"alvaro fernandez" into two useless one-word searches.

Accent-insensitive and substring-based, because the app truncates names.
"""

from __future__ import annotations

import csv
import os
import sys
import unicodedata
from pathlib import Path

import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

TIDY = Path(os.environ.get("FF_ROOT", "./data")) / "tidy"


def input_path(name: str) -> Path:
    """Locate an editable input file. Prefers inputs/<name>; falls back to the
    repo root so a half-finished move doesn't break the run."""
    p = Path("inputs") / name
    return p if p.exists() else Path(name)


def fold(s) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", (s or "").lower())
        if unicodedata.category(c) != "Mn"
    )


def latest_market() -> list[dict]:
    path = TIDY / "market.csv"
    if not path.exists():
        sys.exit(f"ERROR: {path} not found — run ff_ingest.py fetch && parse.")
    with path.open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        sys.exit("ERROR: market.csv is empty.")
    newest = max(r["observed_at"] for r in rows)
    rows = [r for r in rows if r["observed_at"] == newest]
    print(f"# {len(rows)} players in snapshot {newest}")
    return rows


def val(r) -> float:
    try:
        return float(r.get("value") or 0)
    except ValueError:
        return 0.0


def show(rows: list[dict]) -> None:
    if not rows:
        print("  no match")
        return
    for r in sorted(rows, key=lambda r: -val(r)):
        print(f"  {r['name'][:28]:<28} {r['team'][:14]:<14} "
              f"{r['position'][:12]:<12} {val(r)/1e6:>7.2f}M")


def read_lines(path: Path) -> list[str]:
    if not path.exists():
        sys.exit(f"ERROR: {path} not found.")
    return [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.strip().startswith("#")]


def resolve_many(market: list[dict], queries: list[str]) -> None:
    print("# paste the plain lines below into squad.txt")
    if not queries:
        print("# nothing to look up — lookup.txt has no non-comment lines")
        return
    for q in queries:
        qf = fold(q)
        hits = [r for r in market if qf in fold(r["name"])]
        if len(hits) == 1:
            print(hits[0]["name"])
        elif not hits:
            print(f"# NO MATCH for '{q}' — try a shorter fragment")
        else:
            print(f"# AMBIGUOUS '{q}' — pick one:")
            for r in sorted(hits, key=lambda r: -val(r))[:6]:
                print(f"#   {r['name']}  ({r['team']}, {r['position']}, "
                      f"{val(r)/1e6:.2f}M)")


def check(market: list[dict], path: Path) -> None:
    index = {fold(r["name"]): r for r in market}
    entries = read_lines(path)
    ok, bad = [], []
    for e in entries:
        hit = index.get(fold(e))
        (ok if hit else bad).append(e)
    print(f"{len(ok)}/{len(entries)} resolved")
    if ok:
        print("\nresolved:")
        show([index[fold(e)] for e in ok])
    if bad:
        print("\nNOT FOUND:")
        for e in bad:
            print(f"  {e}")
            stem = fold(e).split()[-1] if fold(e).split() else fold(e)
            for r in [r for r in market if stem in fold(r["name"])][:3]:
                print(f"      did you mean: {r['name']}  ({r['team']})")


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return

    market = latest_market()

    if args[0] == "--team":
        want = fold(args[1]) if len(args) > 1 else ""
        show([r for r in market if want and want in fold(r["team"])])
    elif args[0] == "--file":
        resolve_many(market, read_lines(Path(args[1]) if len(args) > 1
                                        else input_path("lookup.txt")))
    elif args[0] == "--many":
        resolve_many(market, args[1:])
    elif args[0] == "--check":
        check(market, Path(args[1]) if len(args) > 1
              else input_path("squad.txt"))
    else:
        q = fold(" ".join(args))
        show([r for r in market if q in fold(r["name"])])


if __name__ == "__main__":
    main()
