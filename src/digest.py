"""
digest.py — the five report files, stitched into the one you actually read.

    python src/digest.py            # writes reports/REPORT.md

Every generator writes its own file, and between them they repeat themselves:
the ledger warnings appear in both rivals.md and behaviour.md, and the cash
basis in one is the cash table in the other. Five files also means five taps on
a phone. This assembles them in the order you need them — what to decide today
first, background last — and drops any section whose heading has already
appeared, so a repeated block is printed once.

It reads the generated files rather than importing the generators, so nothing
upstream has to change and a missing file is a skipped section, not a crash.

Run `python src/digest.py --selftest` to execute the self-test below.
"""

from __future__ import annotations

import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ffcore.tidy import REPORTS, write_lines  # noqa: E402

# Order matters: this is the order you read them in, not the order they are
# generated. Decisions first, reference material last.
SOURCES = [
    ("Decide today", "latest.md"),
    ("Rivals — cash, premiums, squads", "behaviour.md"),
    ("Who to buy", "watchlist.md"),
    ("On offer now", "offers.md"),
    ("Squad detail", "rivals.md"),
]

OUT = "REPORT.md"


def split_sections(text: str) -> list[tuple[str, list[str]]]:
    """[(heading or '', body lines)]. The H1 and anything before the first
    '## ' land in a leading section with an empty heading."""
    out: list[tuple[str, list[str]]] = [("", [])]
    for line in text.splitlines():
        if line.startswith("## "):
            out.append((line[3:].strip(), []))
        else:
            out[-1][1].append(line)
    return out


def _key(heading: str) -> str:
    """Section identity for dedup. 'Ledger warnings' and '## 6. Ledger
    warnings' are the same block printed twice, so leading numbering is
    ignored."""
    h = heading.lower().strip()
    while h and (h[0].isdigit() or h[0] in ". "):
        h = h[1:]
    return h


def digest(read, sources=SOURCES, stamp: str = "") -> list[str]:
    """Assemble one report. `read(name)` returns the file's text, or None."""
    out = ["# Liga Five Guys — one report" + (" — " + stamp if stamp else ""),
           "",
           "Everything the generators produced, in reading order. Sections "
           "that appeared twice are printed once.", ""]
    toc: list[str] = []
    body: list[str] = []
    seen: set[str] = set()

    for title, name in sources:
        text = read(name)
        if not text:
            body += ["## " + title, "",
                     "_No `%s` yet — the generator has not run._" % name, ""]
            toc.append("- " + title + " (missing)")
            continue
        toc.append("- " + title)
        body += ["## " + title, ""]
        for heading, lines in split_sections(text):
            if heading:
                if _key(heading) in seen:
                    continue
                seen.add(_key(heading))
                body.append("### " + heading)
            else:
                # Drop the source file's own H1; keep its preamble.
                lines = [ln for ln in lines if not ln.startswith("# ")]
            body += [ln for ln in lines]
        body.append("")

    return out + toc + [""] + body


def main() -> None:
    def read(name):
        p = REPORTS / name
        return p.read_text(encoding="utf-8") if p.exists() else None

    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    REPORTS.mkdir(exist_ok=True)
    write_lines(REPORTS / OUT, digest(read, stamp=stamp))


def _selftest() -> None:
    files = {
        "latest.md": "# Fantasy report — X\n\nSquad 138M\n\n"
                     "## Needs a decision\n\n- only 1 portero\n\n"
                     "## Ledger warnings\n\n- Burton overdraws\n",
        "behaviour.md": "# League behaviour — X\n\n"
                        "## 1. Cash and ceilings\n\n| a | b |\n\n"
                        "## Ledger warnings\n\n- Burton overdraws\n",
    }
    srcs = [("First", "latest.md"), ("Second", "behaviour.md"),
            ("Absent", "gone.md")]
    lines = digest(lambda n: files.get(n), srcs, stamp="now")
    text = "\n".join(lines)

    assert text.count("# Liga Five Guys") == 1
    # Source H1s are dropped: exactly one '# ' heading survives.
    assert len([l for l in lines if l.startswith("# ")]) == 1, \
        [l for l in lines if l.startswith("# ")]
    # A repeated section is printed once, not once per file.
    assert text.count("### Ledger warnings") == 1, text
    assert text.count("- Burton overdraws") == 1, text
    # Its first home keeps it.
    assert text.index("### Ledger warnings") < text.index("### 1. Cash")
    # Real content survives, demoted one level.
    assert "### Needs a decision" in text
    assert "- only 1 portero" in text
    assert "Squad 138M" in text          # preamble kept
    # A missing generator is a note, not a crash.
    assert "_No `gone.md` yet" in text
    # Table of contents lists every source.
    assert "- First" in text and "- Absent (missing)" in text

    # Numbering is ignored when deciding what is a duplicate.
    assert _key("6. Ledger warnings") == _key("Ledger warnings")

    print("digest self-test OK (10 cases)")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        raise SystemExit(0)
    main()
