"""
xi.py — record the XI you actually fielded, by naming who you benched.

    python src/xi.py                 # log today's XI
    python src/xi.py --selftest

Preferred input is the checklist squads.py regenerates every run:

    inputs/lineup.txt
        [x] POR ionut radu
        [ ] POR alvaro fernandez
        [x] DEF carl starfelt
        ...

You toggle marks, never names. That matters once the bench is four players
rather than one: a bench list has to be retyped as the squad churns, while a
checklist is regenerated from the ledger and only your marks persist.

    inputs/bench.txt        # fallback, still read when lineup.txt is absent
        pepelu

Everything else is derived. Your squad comes from the ledger, so the XI is
squad minus bench — you never retype eleven names, and a name that is no
longer yours is caught instead of silently logged. A squad member the
checklist does not mention at all is benched AND reported: silently fielding
someone you never considered is the one failure this must not have.

ONLY THE MARKS AT LOCK MATTER. The scheduled run logs twice a day, so most
rows record a file you did not touch. hours_to_lock (from inputs/deadline.txt,
the same reading report.py uses) is stamped on every row, so the scorer can
take the last row before kickoff per jornada instead of guessing which one
was live. Without a deadline file the column is blank and nothing else
changes.

Appends one immutable row per run to data/decisions/xi_fielded.csv. Nothing
is ever edited: a second run on the same day appends a second row, and the
latest row for a date is the XI that stood. That is what makes the log
scoreable later — the app never shows you your own history.

Not xi_log.csv: that file has a different schema (formation, index_total) and
holds report.py's best-XI *suggestion*. What you were advised and what you
fielded are two different facts, and scoring needs both kept apart.
"""

from __future__ import annotations

import datetime as dt
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ffcore.league import League  # noqa: E402
from ffcore.text import norm  # noqa: E402
from ffcore.tidy import (DECISIONS, append_csv, input_path,  # noqa: E402
                         load_deadline, read_csv, write_csv)

FIELDS = ["logged_at", "hours_to_lock", "n_xi", "xi", "bench", "warnings"]

# 11 on the pitch. Fewer means the app would have auto-filled someone and the
# log would not match what actually played.
XI_SIZE = 11


def read_bench(text: str) -> list[str]:
    """One name per line. '#' comments and blank lines ignored."""
    out = []
    for line in (text or "").splitlines():
        line = line.split("#")[0].strip()
        if line:
            out.append(line)
    return out


MARK_RE = re.compile(r"^\[(.?)\]\s*(.+)$")
SLOTS = {"POR", "DEF", "MED", "DEL"}


def read_checklist(text: str) -> list[tuple[bool, str]]:
    """[(fielded, name)] from lineup.txt. Non-matching lines are ignored.

    A mark of x or X is fielded; anything else, including a blank, is
    benched. The position token squads.py writes for readability is stripped
    here — it is a label, not part of the name.
    """
    out = []
    for line in (text or "").splitlines():
        line = line.split("#")[0].strip()
        m = MARK_RE.match(line)
        if not m:
            continue
        mark, rest = m.group(1).strip(), m.group(2).strip()
        head, _, tail = rest.partition(" ")
        if head.upper() in SLOTS and tail.strip():
            rest = tail.strip()
        if rest:
            out.append((mark.lower() == "x", rest))
    return out


def match_key(name: str, squad: list[str]):
    """A checklist name -> the squad key it refers to, or None.

    The two sides are spelled differently on purpose: the ledger holds the
    app's abbreviation ("agoume", "eriksson") while the checklist shows the
    market's full name ("lucien agoume"), which is the one worth reading.
    Exact match first, then the unambiguous containment either way; anything
    ambiguous returns None and is reported rather than guessed at.
    """
    k = norm(name)
    keys = [norm(s) for s in squad]
    if k in keys:
        return squad[keys.index(k)]
    hits = [s for s, sk in zip(squad, keys) if sk in k or k in sk]
    return hits[0] if len(hits) == 1 else None


def bench_from_checklist(squad: list[str], entries: list[tuple[bool, str]]):
    """(bench_keys, warnings) — unmarked players, plus anyone uncovered.

    Returns SQUAD keys, not the checklist's spelling, so what comes back is
    always something pick_xi can subtract.

    A squad member missing from the checklist means the file predates a
    purchase. He is benched, never fielded by default, and named in a
    warning so the staleness is visible rather than absorbed.
    """
    covered, bench, warns = set(), [], []
    for fielded, raw in entries:
        key = match_key(raw, squad)
        if key is None:
            warns.append("checklist name '%s' matches nothing you own — "
                         "ignored; run squads.py to regenerate" % raw)
            continue
        covered.add(norm(key))
        if not fielded:
            bench.append(key)
    for name in squad:
        if norm(name) not in covered:
            bench.append(name)
            warns.append("'%s' is in your squad but not on the checklist — "
                         "benched by default; run squads.py to regenerate"
                         % name)
    return bench, warns


def pick_xi(squad: list[str], bench_names: list[str]):
    """(xi, benched, warnings) — squad minus bench, keyed by norm().

    Squad keys come from the ledger and bench names from your typing, so both
    sides are normalised before subtracting. A bench name you no longer own is
    a warning, never a silent drop: it means the file is stale and the XI
    below it cannot be trusted.
    """
    keys = {norm(s) for s in squad}
    warnings, benched = [], []

    for raw in bench_names:
        k = norm(raw)
        if k in keys:
            keys.discard(k)
            benched.append(k)
        else:
            warnings.append("benched '%s' is not in your squad" % raw)

    xi = sorted(keys)
    if len(xi) != XI_SIZE:
        warnings.append("%d players for an XI of %d — bench %d more"
                        % (len(xi), XI_SIZE, len(xi) - XI_SIZE))
    return xi, sorted(benched), warnings


def migrate(path, fields) -> None:
    """Widen an existing log to `fields`, once, filling old rows blank.

    xi_fielded.csv predates hours_to_lock. Appending a wider row to a
    narrower header would silently shift every column, so the header is
    reconciled first. Existing values are never rewritten — only the new
    column is added, empty, which is the truth: those runs did not know the
    deadline.
    """
    rows = read_csv(path)
    if not rows or set(fields) <= set(rows[0]):
        return
    for r in rows:
        for f in fields:
            r.setdefault(f, "")
    write_csv(path, rows, fields)
    print("migrated %s to %d columns" % (path, len(fields)))


def read_input(squad):
    """(bench_names, warnings, source) — checklist first, bench.txt after."""
    chk = input_path("lineup.txt")
    if chk.exists():
        entries = read_checklist(chk.read_text(encoding="utf-8"))
        if entries:
            bench, warns = bench_from_checklist(squad, entries)
            return bench, warns, "lineup.txt"
        return [], ["lineup.txt has no marked lines — run squads.py"], \
            "lineup.txt"
    old = input_path("bench.txt")
    return (read_bench(old.read_text(encoding="utf-8") if old.exists()
                       else ""), [], "bench.txt")


def main() -> None:
    lg = League.load(with_market=False)
    squad = lg.squad(lg.cfg.me)

    bench_names, warnings, source = read_input(squad)
    xi, benched, more = pick_xi(squad, bench_names)
    warnings += more

    now = dt.datetime.now(dt.timezone.utc)
    deadline = load_deadline()
    htl = ("" if deadline is None
           else "%.1f" % ((deadline - now).total_seconds() / 3600))

    path = DECISIONS / "xi_fielded.csv"
    migrate(path, FIELDS)
    append_csv(path, [{
        "logged_at": now.strftime("%Y-%m-%dT%H%MZ"),
        "hours_to_lock": htl,
        "n_xi": len(xi),
        "xi": "|".join(xi),
        "bench": "|".join(benched),
        "warnings": "; ".join(warnings),
    }], FIELDS)

    print("logged XI of %d from %s (bench: %s)%s"
          % (len(xi), source, ", ".join(benched) or "—",
             "" if not htl else " — %sh to lock" % htl))
    for w in warnings:
        print("  warning:", w)


def _selftest() -> None:
    squad = ["Alvaro Fernandez", "Beñat Turrientes", "Carl Starfelt",
             "Dani Lorenzo", "Igor Zubeldia", "Iñigo Ruiz de Galarreta",
             "Iñigo Vicente", "Jon Moncayola", "Omar El Hilali", "Pepelu",
             "Robin Le Normand", "Ruben Garcia"]

    # The real case: 12 owned, bench one, field eleven.
    xi, benched, warns = pick_xi(squad, ["pepelu"])
    assert len(xi) == 11, xi
    assert benched == ["pepelu"]
    assert not warns, warns
    assert "pepelu" not in xi

    # Accents survive the round trip whichever way you spell it. Typing the
    # accent is the case that separates norm() from a plain .lower(): the
    # ledger key is folded, so an unfolded 'ñ' would miss and the player would
    # be logged as fielded when you benched him.
    assert norm("Beñat Turrientes") in xi
    for typed in ("Benat Turrientes", "Beñat Turrientes"):
        xi2, benched2, warns2 = pick_xi(squad, [typed])
        assert benched2 == [norm("Beñat Turrientes")], (typed, benched2)
        assert not warns2, (typed, warns2)

    # A stale bench name is reported, not absorbed.
    xi3, benched3, warns3 = pick_xi(squad, ["someone i sold"])
    assert benched3 == []
    assert any("not in your squad" in w for w in warns3), warns3
    # ...and it leaves 12, so the count warning fires too.
    assert any("bench 1 more" in w for w in warns3), warns3

    # Benching nobody is wrong and says so.
    _, _, warns4 = pick_xi(squad, [])
    assert any("12 players for an XI of 11" in w for w in warns4), warns4

    # Comments and blanks are not player names.
    assert read_bench("pepelu\n\n# injured\n  \n") == ["pepelu"]
    assert read_bench("pepelu  # sitting\n") == ["pepelu"]
    assert read_bench("") == []

    # --- checklist ---------------------------------------------------------
    text = ("# XI CHECKLIST\n"
            "# 11 marked\n"
            "[x] POR Ionut Radu\n"
            "[ ] POR Alvaro Fernandez\n"
            "[X] DEF Carl Starfelt\n"
            "[] MED Pepelu\n"
            "\n"
            "[x] MED Beñat Turrientes  # captain-ish\n")
    entries = read_checklist(text)
    assert entries == [(True, "Ionut Radu"), (False, "Alvaro Fernandez"),
                       (True, "Carl Starfelt"), (False, "Pepelu"),
                       (True, "Beñat Turrientes")], entries
    # A name that happens to start like a slot token is not truncated.
    assert read_checklist("[x] Pordenone Rossi") == [(True, "Pordenone Rossi")]
    assert read_checklist("just a comment\n") == []

    # Unmarked players become the bench; the rest of the squad is untouched.
    small = ["Ionut Radu", "Alvaro Fernandez", "Carl Starfelt"]
    bench, warns5 = bench_from_checklist(
        small, [(True, "Ionut Radu"), (False, "Alvaro Fernandez"),
                (True, "Carl Starfelt")])
    assert bench == ["Alvaro Fernandez"] and not warns5, (bench, warns5)

    # The ledger abbreviates; the checklist spells it out. Same player.
    abbrev = ["agoume", "eriksson", "ionut radu"]
    bench7, warns7 = bench_from_checklist(
        abbrev, [(True, "Lucien Agoume"), (False, "Simon Eriksson"),
                 (True, "Ionut Radu")])
    assert bench7 == ["eriksson"] and not warns7, (bench7, warns7)
    assert match_key("Lucien Agoume", abbrev) == "agoume"
    # Ambiguity is reported, never guessed.
    assert match_key("garcia", ["ruben garcia", "alvaro garcia"]) is None

    # A player the checklist never mentions is benched AND reported.
    bench6, warns6 = bench_from_checklist(
        small + ["New Signing"],
        [(True, "Ionut Radu"), (False, "Alvaro Fernandez"),
         (True, "Carl Starfelt")])
    assert "New Signing" in bench6, bench6
    assert any("not on the checklist" in w for w in warns6), warns6

    print("xi self-test OK (23 cases)")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        raise SystemExit(0)
    main()
