"""
ownership.py — who is allowed to compute which decision-making number,
checked automatically rather than trusted from memory or a stale doc.

REAL BUG THIS EXISTS BECAUSE OF, 2026-09-13: Scorer.score() (ffcore/
score.py) zeroes a suspended/injured/unavailable player's score and
halves a "doubt" one's — but FOUR separate places (Scorer.score() itself,
PlayerProfile.to_bootstrap_input(), build_profiles()'s own market_exp/
start_p, decide.apply_fixtures()) each independently rebuilt (points,
start-probability) from the same raw components (ppm, fix, pct_used),
and three of the four silently skipped the override. Found only because
Miguel checked one specific player's (Zaid Romero, suspended) number by
hand and it didn't add up. His own follow-up questions are what this
file answers going forward: "are you ensuring all decision making
metrics can be sourced to our single pipeline? Any orphaned ones we
should check", then "please track ownership of the decision making
numbers."

HOW THIS WORKS: for a metric with a known, real, past orphan risk, the
REGISTRY below records the OWNER (the one function that gets to decide
its value) and an EXACT EXPECTED COUNT of the raw-reconstruction shape
per file — not a claim that only one line of code may ever multiply ppm
by fix (there are legitimately several, one per real reason to reprice),
but that every one is accounted for. audit() counts real (non-comment)
occurrences and fails if a count changes — a NEW, unreviewed rebuild is
exactly the shape of bug this file exists to catch on the next self-test
run, not the next time someone happens to check a suspended player's
forecast by hand.

COUNTS, NOT PER-SITE PATTERNS — a real design correction made while
building this: to_bootstrap_input() and build_profiles() both contain
the literal substring "s.ppm * s.fix" (the same bug, in two places, is
BY DEFINITION near-identical code), so a pattern meant to match "just
the first one" also matches the second, and vice versa. Pretending to
pin down which exact line is which is fragile; counting how many times
the shape appears in a file, and reviewing the total, is not.

Deliberately narrow, not a generic static-analysis framework: this
tracks the SPECIFIC patterns that have already burned this repo once,
and grows one entry at a time as a real orphan is found and fixed — the
same way this repo's other constants (SHRINK_K, HOME_EDGE) earned their
place by being real, not by anticipating every possible future case.
"""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parent

# {metric: {"owner": str, "counts": {relpath: (expected_n, pattern, why)}}}
# `pattern` is matched against SOURCE LINES WITH COMMENTS STRIPPED, so a
# docstring/comment mentioning the shape (there is one, profile.py's own
# "market_exp = ppm * fix * start_p" note) never counts as a real hit.
REGISTRY: dict[str, dict] = {
    "pts (points-if-he-plays) x fixture factor": {
        "owner": ("ffcore.score.Scorer.score() computes the CANONICAL "
                 "flat = ppm * pct_used, then score = flat * fix_factor, "
                 "with the status override applied right there. Every "
                 "site below is a DIFFERENT question (repricing for a "
                 "future jornada's own opponent, or rebuilding the raw "
                 "(pts, p_start) pair Bootstrap wants) that has to reach "
                 "the same status-adjusted answer via "
                 "ffcore.profile.status_adjusted() — not a second "
                 "definition of what fixture-adjusted points ARE."),
        "counts": {
            "ffcore/profile.py": (2, r"s\.ppm \* s\.fix",
                "to_bootstrap_input() (this jornada's pts for Bootstrap) "
                "and build_profiles()'s own market_exp/start_p — both "
                "status-adjusted via status_adjusted()"),
            "decide.py": (1, r"ppm_of\[k\] \* fix",
                "apply_fixtures(): reprices for THIS jornada's real "
                "opponent (not the frozen next-fixture one base/"
                "base_rest were built with), status applied only at "
                "first_jornada_of[k] via status_adjusted()"),
        },
    },
}


def _stripped_lines(path: Path) -> list[str]:
    """Real code lines only — a trailing `# comment` chopped off, a
    whole-line comment or blank line dropped outright. Crude (does not
    understand triple-quoted strings containing a `#`), which is fine:
    this only needs to not double-count a docstring that happens to
    mention the pattern in prose, and every known case is a plain `#`
    comment line, not a string literal.
    """
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.split("#", 1)[0].strip()
        if stripped:
            out.append(stripped)
    return out


def audit() -> list[str]:
    """[] if every tracked metric's real-code occurrence count matches
    the registry exactly; otherwise one message per mismatch — either a
    count that dropped (the fix moved, or was reverted) or one that rose
    (a NEW, unreviewed rebuild the registry doesn't account for yet — a
    real, actionable find: go add it here with the same review this
    file's own history required, or fix the orphan).
    """
    problems = []
    for metric, spec in REGISTRY.items():
        for relpath, (expected, pattern, why) in spec["counts"].items():
            path = SRC / relpath
            if not path.exists():
                problems.append(f"{metric}: {relpath} no longer exists "
                               f"(was: {why})")
                continue
            lines = _stripped_lines(path)
            hits = sum(1 for ln in lines if re.search(pattern, ln))
            if hits != expected:
                problems.append(
                    f"{metric} in {relpath}: expected {expected} "
                    f"occurrence(s) of {pattern!r} ({why}), found {hits}. "
                    f"{'A new, unreviewed rebuild — go add it to the registry or fix it.' if hits > expected else 'The known fix moved or was reverted.'}")
    return problems


def _selftest() -> None:
    import tempfile

    # -- _stripped_lines: comments and blanks dropped, real code kept ---
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "x.py"
        p.write_text(
            "real = 1\n"
            "# a whole-line comment mentioning ppm * fix\n"
            "\n"
            "also_real = ppm * fix  # trailing comment\n",
            encoding="utf-8")
        lines = _stripped_lines(p)
        assert lines == ["real = 1", "also_real = ppm * fix"], lines

    # -- audit(): the real registry, checked against the real repo ------
    # No synthetic fixture here on purpose — the whole point is whether
    # THIS repo's actual source matches what's documented; a mock would
    # only prove the counting logic works, not that the thing it counts
    # is still true.
    problems = audit()
    assert problems == [], problems

    # -- audit() actually catches a real regression, not just a real pass
    # (a) a count that's too LOW (the fix moved/reverted) and (b) a count
    # that's too HIGH (a new, unreviewed rebuild appeared) both surface.
    real_registry = {k: dict(v) for k, v in REGISTRY.items()}
    try:
        REGISTRY["fake: too low"] = {
            "owner": "nobody",
            "counts": {"ffcore/score.py": (
                1, r"this pattern matches nothing at all XYZZY", "test")},
        }
        broken_low = audit()
        assert any("expected 1 occurrence" in p and "found 0" in p
                  for p in broken_low), broken_low
        del REGISTRY["fake: too low"]

        REGISTRY["fake: too high"] = {
            "owner": "nobody",
            # SHRINK_K is a real, known constant referenced many times —
            # asserting "expect 0" against something real forces a
            # mismatch deterministically, without depending on exact count.
            "counts": {"ffcore/score.py": (0, r"SHRINK_K", "test")},
        }
        broken_high = audit()
        assert any("expected 0 occurrence" in p and "new, unreviewed" in p
                  for p in broken_high), broken_high
    finally:
        REGISTRY.clear()
        REGISTRY.update(real_registry)

    print("ownership.py self-test OK (3 cases)")


if __name__ == "__main__":
    import sys

    if "--selftest" in sys.argv:
        _selftest()
    else:
        problems = audit()
        if problems:
            print("ownership audit found %d problem(s):" % len(problems))
            for p in problems:
                print(" -", p)
            sys.exit(1)
        print("ownership audit: every tracked metric matches the registry")
