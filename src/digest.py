"""
digest.py — the four report files, stitched into the one you actually read.

    python src/digest.py            # writes reports/REPORT.md

Every generator writes its own file, and between them they repeat themselves.
Concatenating all four produced a 504-line report in which Ionut Radu appeared
six times, the same cash figure five times, and the same purchase rows four
times, because deduplicating by HEADING only catches a block repeated under
the same name — not the same fact printed under two different ones.

So this no longer concatenates. It takes latest.md whole (that is the decision
report), pulls only the named sections worth carrying from the others, and
LINKS to the rest. Nothing is deleted: the long tables still live in their own
files, one tap away, and that is where they belong.

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
# (title, filename, sections to include or None for the whole file)
SOURCES = [
    ("Decide today", "latest.md", None),
    # Cash is a ceiling on every bid, so it earns its place in the one file
    # you open. The premium curves, drift table and projected XIs behind it
    # do not — they are reference, and they are linked below.
    ("Rival cash", "rivals.md", ["1. Cash and ceilings", "Ledger warnings"]),
]

# Printed as links, not content. Each is a whole file that would otherwise be
# inlined and duplicate something above.
LINKS = [
    ("Who to buy — everyone unowned, ranked", "watchlist.md"),
    ("How your rivals bid — premiums, drift, projected XIs", "rivals.md"),
    ("Every squad in the league, deal history, cash basis", "squads.md"),
    ("How the forecast works, and how it's doing", "methodology.md"),
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


def digest(read, sources=SOURCES, stamp: str = "",
           links=LINKS) -> list[str]:
    """Assemble one report. `read(name)` returns the file's text, or None."""
    out = ["# Liga Five Guys — one report" + (" — " + stamp if stamp else ""),
           "",
           "The four questions first, from `latest.md`. Everything else is "
           "reference and is linked, not reprinted.", ""]
    body: list[str] = []
    seen: set[str] = set()

    for title, name, wanted in sources:
        text = read(name)
        if not text:
            body += ["## " + title, "",
                     "_No `%s` yet — the generator has not run._" % name, ""]
            continue
        keep = {_key(w) for w in wanted} if wanted is not None else None
        if wanted is not None:
            body += ["## " + title, ""]
        for heading, lines in split_sections(text):
            if heading:
                if keep is not None and _key(heading) not in keep:
                    continue
                if _key(heading) in seen:
                    continue
                seen.add(_key(heading))
                # A whole-file source keeps its own heading levels; a
                # cherry-picked one is nested under the title above it.
                body.append(("### " if wanted is not None else "## ")
                            + heading)
            elif wanted is not None:
                continue      # preamble belongs to the file, not to an excerpt
            else:
                # latest.md's H1 becomes this report's H1, so drop it here.
                lines = [ln for ln in lines if not ln.startswith("# ")]
            body += [ln for ln in lines]
        body.append("")

    if links:
        body += ["## Reference", "",
                 "Kept in full, one tap away — not reprinted here, because "
                 "that is what made this file 504 lines long.", ""]
        for title, name in links:
            missing = "" if read(name) else "  _(not generated yet)_"
            body.append("- [%s](%s)%s" % (title, name, missing))
        body.append("")

    return out + body


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
                     "## 1. Am I fielding the right eleven?\n\n- yes\n\n"
                     "## Warnings\n\n- Burton overdraws\n",
        "rivals.md": "# League behaviour — X\n\n"
                     "## 1. Cash and ceilings\n\n| a | b |\n\n"
                     "## 2. What they pay over value\n\n| long | table |\n\n"
                     "## Ledger warnings\n\n- Burton overdraws\n",
    }
    srcs = [("Decide today", "latest.md", None),
            ("Rival cash", "rivals.md", ["1. Cash and ceilings",
                                         "Ledger warnings"]),
            ("Absent", "gone.md", None)]
    lnks = [("Who to buy", "watchlist.md"), ("Rivals in full", "rivals.md")]
    lines = digest(lambda n: files.get(n), srcs, stamp="now", links=lnks)
    text = "\n".join(lines)

    assert text.count("# Liga Five Guys") == 1
    # Source H1s are dropped: exactly one '# ' heading survives.
    assert len([l for l in lines if l.startswith("# ")]) == 1, \
        [l for l in lines if l.startswith("# ")]
    # latest.md is carried whole, keeping its own heading levels.
    assert "## 1. Am I fielding the right eleven?" in text
    assert "- yes" in text
    assert "Squad 138M" in text          # preamble kept

    # A cherry-picked source brings ONLY the sections named.
    assert "### 1. Cash and ceilings" in text
    assert "| a | b |" in text
    assert "2. What they pay over value" not in text, text
    assert "| long | table |" not in text, text

    # THE DUPLICATION FIX: a section already printed is not printed again,
    # whichever file it came from. 'Warnings' in latest.md and 'Ledger
    # warnings' in rivals.md are different keys, so both survive — but the
    # body line they share appears once per section, not once per file.
    assert text.count("- Burton overdraws") == 2, text
    # ...and an identical heading really is dropped.
    lines2 = digest(lambda n: files.get(n),
                    [("A", "rivals.md", ["Ledger warnings"]),
                     ("B", "rivals.md", ["Ledger warnings"])],
                    links=None)
    assert "\n".join(lines2).count("Ledger warnings") == 1, lines2

    # A missing generator is a note, not a crash.
    assert "_No `gone.md` yet" in text
    # Links are printed as links, never inlined.
    assert "[Who to buy](watchlist.md)" in text
    assert "_(not generated yet)_" in text      # watchlist.md absent here

    # Numbering is ignored when deciding what is a duplicate.
    assert _key("6. Ledger warnings") == _key("Ledger warnings")

    print("digest self-test OK (16 cases)")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        raise SystemExit(0)
    main()
