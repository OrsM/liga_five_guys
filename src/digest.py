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

from typing import NamedTuple  # noqa: E402

from ffcore.tidy import REPORTS, write_lines  # noqa: E402


class Part(NamedTuple):
    title: str
    name: str
    sections: list | None      # None = the whole file
    nest: bool = True          # False = keep own heading levels and preamble


# latest.md's decision sections, and only these. It carries more — the sell
# shortlist, the movers table — and that material is NOT duplicated anywhere
# else, so it is left in latest.md and linked rather than deleted. This file is
# what you read on a phone before a lock; latest.md is what you read when you
# have time.
DECIDE = [
    # First, because it is the answer: every asset — owned, buyable, and the
    # cash — in one ranking. The five numbered questions below it are the
    # workings, and they are kept because a ranking you cannot audit is a
    # ranking you stop trusting.
    "The board",
    "1. Field these eleven",
    "2. Buy today",
    "3. What you give up by spending now",
    "4. Sell these",
    "5. Exceptions",
    "Warnings",
]

# Order matters: this is the order you read them in, not the order they are
# generated.
SOURCES = [
    Part("Decide today", "latest.md", DECIDE, nest=False),
]

# Printed as links, not content. Each is a whole file that would otherwise be
# inlined and duplicate something above.
#
# Rival cash used to be stitched in here, on the argument that cash is a
# ceiling on every bid. It is, and it is one tap away in rivals.md — but it was
# fourteen lines about four other managers sitting above the eleven names you
# came to check.
LINKS = [
    ("The rest of today's report — sell shortlist, movers", "latest.md"),
    ("Who to buy — everyone unowned, ranked", "watchlist.md"),
    ("Rival cash and ceilings, premiums, drift, projected XIs", "rivals.md"),
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
           "Field, buy, hold, sell — every table priced in the same currency, "
           "from `latest.md`. Everything else is reference and is linked, not "
           "reprinted.", ""]
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
        # A section named here but absent from the file is a heading that was
        # renamed upstream. Silently dropping it would quietly shorten the one
        # report you rely on, so it is named in the output instead.
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

    # THE DUPLICATION FIX: a section already printed is not printed again,
    # whichever file it came from. 'Warnings' in latest.md and 'Ledger
    # warnings' in rivals.md are different keys, so both survive — but the
    # body line they share appears once per section, not once per file.
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

    print("digest self-test OK (24 cases)")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        raise SystemExit(0)
    main()
