"""
digest.py — the render fragments, stitched into the appendix.

    python src/digest.py            # writes reports/METHOD.md

decisions.json (the board) carries the position, ladder, league table and
warnings; this file builds the ONE other document — how every number is
made and every way it's known to be wrong — rather than a second
rendering of the same answer.

Sources are build-artifact fragments under .runtime/parts/, one per
generator, read as text rather than imported: a missing one is a skipped
section, not a crash.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from typing import NamedTuple  # noqa: E402

from ffcore.tidy import run_now
from ffcore.tidy import PARTS, REPORTS, write_lines  # noqa: E402


class Part(NamedTuple):
    title: str
    name: str
    sections: list | None      # None = the whole file
    nest: bool = True          # False = keep own heading levels and preamble


# The appendix: everything true about HOW the numbers are made, kept
# separate from the report (the numbers themselves) because the caveats
# are long and change rarely.
APPENDIX = "METHOD.md"

APPENDIX_SOURCES = [
    Part("What it cannot see", "sim.md",
         ["What the simulation cannot see"], nest=False),
    Part("The forecast, and how it is doing", "methodology.md", None,
         nest=False),
]


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


def digest(read, sources=APPENDIX_SOURCES, stamp: str = "",
           links=None, skip=frozenset(), title="", lead="") -> list[str]:
    """Assemble one report. `read(name)` returns the file's text, or None."""
    out = [("# " + (title or "Liga Five Guys — one report"))
           + (" — " + stamp if stamp else ""),
           "",
           lead or "", ""]
    body: list[str] = []
    seen: set[str] = set()
    lost: list[str] = []

    for part in sources:
        title, name, wanted, nest = (part if isinstance(part, Part)
                                     else Part(*part))
        text = read(name)
        if not text:
            body += ["## " + title, "",
                     "_No `%s` yet — the generator has not run._" % name, ""]
            continue
        keep = {_key(w) for w in wanted} if wanted is not None else None
        if wanted is not None and nest:
            body += ["## " + title, ""]
        found = set()
        for heading, lines in split_sections(text):
            if heading:
                if keep is not None and _key(heading) not in keep:
                    continue
                if _key(heading) in skip:
                    continue
                if _key(heading) in seen:
                    continue
                seen.add(_key(heading))
                found.add(_key(heading))
                # A nested source sits under the title above it; an un-nested
                # one keeps its own heading levels.
                body.append(("### " if nest else "## ") + heading)
            elif wanted is not None and nest:
                continue      # preamble belongs to the file, not to an excerpt
            elif not heading:
                # latest.md's H1 becomes this report's H1, so drop it here.
                lines = [ln for ln in lines if not ln.startswith("# ")]
            body += [ln for ln in lines]
        # A named section absent from the file means a heading was renamed
        # upstream — reported, not silently dropped.
        lost += ["%s → %s" % (name, w) for w in (wanted or [])
                 if _key(w) not in found]
        body.append("")

    if lost:
        body += ["## ⚠ Sections missing", "",
                 "These were asked for and not found — a heading was renamed, "
                 "and `digest.py`'s list needs the new name:", ""]
        body += ["- `%s`" % m for m in lost]
        body.append("")

    if links:
        body += ["## Reference", ""]
        for title, name in links:
            missing = "" if read(name) else "  _(not generated yet)_"
            body.append("- [%s](%s)%s" % (title, name, missing))
        body.append("")

    return out + body


def main() -> None:
    def read(name):
        p = PARTS / name
        return p.read_text(encoding="utf-8") if p.exists() else None

    stamp = run_now().strftime("%Y-%m-%d %H:%M UTC")
    REPORTS.mkdir(exist_ok=True)
    write_lines(REPORTS / APPENDIX,
                digest(read, APPENDIX_SOURCES, stamp=stamp, links=None,
                       title="Liga Five Guys — how the numbers are made"))


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
    srcs = [Part("Decide today", "latest.md", None, nest=False),
            Part("Rival cash", "rivals.md", ["1. Cash and ceilings",
                                             "Ledger warnings"]),
            Part("Absent", "gone.md", None)]
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

    # A section already printed isn't printed again — 'Warnings' and 'Ledger
    # warnings' are different keys, so both survive once each.
    assert text.count("- Burton overdraws") == 2, text
    # ...and an identical heading really is dropped.
    lines2 = digest(lambda n: files.get(n),
                    [Part("A", "rivals.md", ["Ledger warnings"]),
                     Part("B", "rivals.md", ["Ledger warnings"])],
                    links=None)
    text2 = "\n".join(lines2)
    assert text2.count("### Ledger warnings") == 1, lines2
    # The second ask found nothing, and says so rather than going quiet.
    assert "Sections missing" in text2 and "rivals.md → Ledger" in text2

    # A section can be SKIPPED rather than cherry-picked.
    both = digest(lambda n: files.get(n),
                  [Part("D", "latest.md", None, nest=False)],
                  links=None, skip={"warnings"})
    assert "1. Am I fielding" in "\n".join(both)
    assert "## Warnings" not in "\n".join(both), both
    # ...and a different title and lead, for the appendix.
    apx = digest(lambda n: files.get(n), [], links=None,
                 title="How the numbers are made", lead="the fits")
    assert apx[0].startswith("# How the numbers are made")
    assert "the fits" in apx[2]
    # NO STANDING BLURB on the report: it was a sentence restating the title.
    plain = digest(lambda n: files.get(n), [], links=None)
    assert plain[2] == "", plain[:4]

    # THE SILENT-SHORTENING GUARD: a renamed heading is reported, not dropped.
    renamed = digest(lambda n: files.get(n),
                     [Part("D", "latest.md", ["Warnings", "5. Gone"],
                           nest=False)], links=None)
    assert "## Warnings" in "\n".join(renamed)
    assert "latest.md → 5. Gone" in "\n".join(renamed), renamed

    # An un-nested cherry-pick keeps '## ' levels and its preamble, because it
    # is the decision report and not an excerpt from someone else's file.
    picked = digest(lambda n: files.get(n),
                    [Part("Decide", "latest.md",
                          ["1. Am I fielding the right eleven?"], nest=False)],
                    links=None)
    ptext = "\n".join(picked)
    assert "## 1. Am I fielding the right eleven?" in ptext
    assert "Squad 138M" in ptext           # preamble survives
    assert "Warnings" not in ptext, ptext  # unasked section stays behind

    # A missing generator is a note, not a crash.
    assert "_No `gone.md` yet" in text
    # Links are printed as links, never inlined.
    assert "[Who to buy](watchlist.md)" in text
    assert "_(not generated yet)_" in text      # watchlist.md absent here

    # Numbering is ignored when deciding what is a duplicate.
    assert _key("6. Ledger warnings") == _key("Ledger warnings")

    print("digest self-test OK (28 cases)")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        raise SystemExit(0)
    main()
