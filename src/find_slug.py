"""
find_slug.py — resolve player names against the collected market data.

    python find_slug.py starfelt              # search
    python find_slug.py --team celta          # a club's whole squad
    python find_slug.py --many starfelt duro  # several at once
    python find_slug.py --file lookup.txt     # one query per LINE
    python find_slug.py --check squad.txt     # validate a squad file

Use --file for multi-word names: the shell splits on spaces, which turns
"alvaro fernandez" into two useless one-word searches.

This is the tool inputs/transactions.csv tells you to run before adding a row:
the ledger keys on the name as data/tidy spells it, and the app abbreviates.

WHY THIS FILE IS NOW THIN. It used to carry private copies of input_path(),
a market loader, and a fold() that stripped accents but not punctuation —
the exact normaliser ffcore.text was written to replace, which meant this
tool and the rest of the repo disagreed about "N'Diaye" and "C. Dominguez".
Matching is now ffcore.text.resolve(), the same three passes (exact,
substring, all-tokens) that seen.py uses on OCR'd names, so a name that
resolves here resolves everywhere.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ffcore.parse import money                                  # noqa: E402
from ffcore.text import norm, resolve                           # noqa: E402
from ffcore.tidy import input_path, latest_only, load_market    # noqa: E402


def market() -> list[dict]:
    rows = latest_only(load_market())
    if not rows:
        sys.exit("ERROR: data/tidy/market.csv missing or empty — "
                 "run ff_ingest.py fetch && parse.")
    print("# %d players in snapshot %s"
          % (len(rows), rows[0].get("observed_at", "?")))
    return rows


def show(rows) -> None:
    if not rows:
        print("  no match")
        return
    for r in sorted(rows, key=lambda r: -(money(r.get("value")) or 0)):
        print("  %-28s %-14s %-12s %7.2fM"
              % (r["name"][:28], (r.get("team") or "")[:14],
                 (r.get("position") or "")[:12],
                 (money(r.get("value")) or 0) / 1e6))


def read_lines(path: Path) -> list[str]:
    if not path.exists():
        sys.exit("ERROR: %s not found." % path)
    return [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.strip().startswith("#")]


def resolve_many(rows, queries) -> None:
    """One line per query: the canonical name, or why it could not be given.

    Ambiguity is printed as candidates and never resolved silently — the
    output of this is pasted into a ledger, where a wrong player costs money.
    """
    print("# paste the plain lines below into your squad or ledger")
    if not queries:
        print("# nothing to look up — the file has no non-comment lines")
        return
    for q in queries:
        hit, cands = resolve(q, rows)
        if hit:
            print(hit["name"])
        elif cands:
            print("# AMBIGUOUS '%s' — pick one:" % q)
            for r in sorted(cands, key=lambda r: -(money(r.get("value")) or 0))[:6]:
                print("#   %s  (%s, %s, %.2fM)"
                      % (r["name"], r.get("team", ""), r.get("position", ""),
                         (money(r.get("value")) or 0) / 1e6))
        else:
            print("# NO MATCH for '%s' — try a shorter fragment" % q)


def check(rows, path: Path) -> None:
    entries = read_lines(path)
    ok, bad = [], []
    for e in entries:
        hit, _ = resolve(e, rows)
        (ok if hit else bad).append((e, hit))
    print("%d/%d resolved" % (len(ok), len(entries)))
    if ok:
        print("\nresolved:")
        show([h for _, h in ok])
    if bad:
        print("\nNOT FOUND:")
        for e, _ in bad:
            print("  %s" % e)
            stem = (norm(e).split() or [""])[-1]
            for r in [r for r in rows if stem and stem in norm(r["name"])][:3]:
                print("      did you mean: %s  (%s)" % (r["name"], r.get("team", "")))


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return

    rows = market()

    if args[0] == "--team":
        want = norm(args[1]) if len(args) > 1 else ""
        show([r for r in rows if want and want in norm(r.get("team"))])
    elif args[0] == "--file":
        resolve_many(rows, read_lines(Path(args[1]) if len(args) > 1
                                      else input_path("lookup.txt")))
    elif args[0] == "--many":
        resolve_many(rows, args[1:])
    elif args[0] == "--check":
        check(rows, Path(args[1]) if len(args) > 1
              else input_path("squad.txt"))
    else:
        show([r for r in rows if norm(" ".join(args)) in norm(r.get("name"))])


if __name__ == "__main__":
    main()
