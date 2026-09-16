
from __future__ import annotations

import sys


from typing import NamedTuple

from ffcore.tidy import run_now
from ffcore.tidy import PARTS, REPORTS, write_lines


class Part(NamedTuple):
    title: str
    name: str
    sections: list | None
    nest: bool = True


APPENDIX = "METHOD.md"

APPENDIX_SOURCES = [
    Part("What it cannot see", "sim.md",
         ["What the simulation cannot see"], nest=False),
    Part("The forecast, and how it is doing", "methodology.md", None,
         nest=False),
]


def split_sections(text: str) -> list[tuple[str, list[str]]]:
    out: list[tuple[str, list[str]]] = [("", [])]
    for line in text.splitlines():
        if line.startswith("## "):
            out.append((line[3:].strip(), []))
        else:
            out[-1][1].append(line)
    return out


def _key(heading: str) -> str:
    h = heading.lower().strip()
    while h and (h[0].isdigit() or h[0] in ". "):
        h = h[1:]
    return h


def digest(read, sources=APPENDIX_SOURCES, stamp: str = "",
           links=None, skip=frozenset(), title="", lead="") -> list[str]:
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
                body.append(("### " if nest else "## ") + heading)
            elif wanted is not None and nest:
                continue
            elif not heading:
                lines = [ln for ln in lines if not ln.startswith("# ")]
            body += [ln for ln in lines]
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
    assert len([l for l in lines if l.startswith("# ")]) == 1, \
        [l for l in lines if l.startswith("# ")]
    assert "## 1. Am I fielding the right eleven?" in text
    assert "- yes" in text
    assert "Squad 138M" in text

    assert "### 1. Cash and ceilings" in text
    assert "| a | b |" in text
    assert "2. What they pay over value" not in text, text
    assert "| long | table |" not in text, text

    assert text.count("- Burton overdraws") == 2, text
    lines2 = digest(lambda n: files.get(n),
                    [Part("A", "rivals.md", ["Ledger warnings"]),
                     Part("B", "rivals.md", ["Ledger warnings"])],
                    links=None)
    text2 = "\n".join(lines2)
    assert text2.count("### Ledger warnings") == 1, lines2
    assert "Sections missing" in text2 and "rivals.md → Ledger" in text2

    both = digest(lambda n: files.get(n),
                  [Part("D", "latest.md", None, nest=False)],
                  links=None, skip={"warnings"})
    assert "1. Am I fielding" in "\n".join(both)
    assert "## Warnings" not in "\n".join(both), both
    apx = digest(lambda n: files.get(n), [], links=None,
                 title="How the numbers are made", lead="the fits")
    assert apx[0].startswith("# How the numbers are made")
    assert "the fits" in apx[2]
    plain = digest(lambda n: files.get(n), [], links=None)
    assert plain[2] == "", plain[:4]

    renamed = digest(lambda n: files.get(n),
                     [Part("D", "latest.md", ["Warnings", "5. Gone"],
                           nest=False)], links=None)
    assert "## Warnings" in "\n".join(renamed)
    assert "latest.md → 5. Gone" in "\n".join(renamed), renamed

    picked = digest(lambda n: files.get(n),
                    [Part("Decide", "latest.md",
                          ["1. Am I fielding the right eleven?"], nest=False)],
                    links=None)
    ptext = "\n".join(picked)
    assert "## 1. Am I fielding the right eleven?" in ptext
    assert "Squad 138M" in ptext
    assert "Warnings" not in ptext, ptext

    assert "_No `gone.md` yet" in text
    assert "[Who to buy](watchlist.md)" in text
    assert "_(not generated yet)_" in text

    assert _key("6. Ledger warnings") == _key("Ledger warnings")

    print("digest self-test OK (28 cases)")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        raise SystemExit(0)
    main()
