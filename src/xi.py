"""
xi.py — record the XI you actually fielded.

    python src/xi.py                 # log today's XI
    python src/xi.py --selftest

READ FROM THE APP, which publishes it: ffcore.league.app_fielded resolves
/v1/competition/1/teams/{team}/lineup/week/{n} to squad keys. Nothing is typed
and nothing is ticked.

WHAT THIS REPLACED, on 2026-08-19: inputs/lineup.txt, a checklist regenerated
every run with your marks kept. It was the best answer available while the app
was believed to publish none, and its failure was structural — sell a man out
of your eleven, squads.py drops his line, ten marks are left, and this file
logged those ten as the eleven you fielded. The log is the historical record a
P(start) grade will be judged against, so a record of what somebody remembered
to tick was worth less than no record at all.

NOTHING IS LOGGED WHEN THE APP IS QUIET. A row invented from a stale file is
the one failure this must not have; a gap in the log is honest and visible.

ONLY THE ELEVEN AT LOCK MATTERS. hours_to_lock (from the next kickoff, the
same reading report.py uses) is stamped on every row, so the scorer can take
the last row before kickoff per jornada instead of guessing which one was
live. Without a deadline file the column is blank and nothing else changes.

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

from ffcore.league import League, app_fielded  # noqa: E402
from ffcore.text import norm  # noqa: E402
from ffcore.tidy import (DECISIONS, append_csv, input_path,  # noqa: E402
                         load_deadline, read_csv, write_csv)

FIELDS = ["logged_at", "hours_to_lock", "n_xi", "xi", "bench", "warnings"]

# 11 on the pitch. Fewer means the app would have auto-filled someone and the
# log would not match what actually played.
XI_SIZE = 11


def fielded(squad: list[str]):
    """(xi, bench, warnings) — the app's eleven and what it leaves out.

    [] for the XI means the app has not answered recently enough to be about
    the round you are picking, and the caller logs NOTHING rather than fall
    back to a guess.
    """
    xi = sorted(app_fielded(squad, {}))
    if not xi:
        return [], [], ["the app's lineup feed is quiet — nothing logged"]
    if len(xi) != XI_SIZE:
        # The app should never say anything but eleven. If it does, the
        # reading is about something other than a fielded eleven and must not
        # be recorded as one.
        return [], [], ["%d players for an XI of %d — not logged"
                        % (len(xi), XI_SIZE)]
    return xi, sorted(norm(s) for s in squad if norm(s) not in set(xi)), []


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


def main() -> None:
    # WITH the market, even though this script prices nothing. Ownership now
    # comes from the league API, and that join needs Market.key_for to key
    # players the way every other reader does; without it League falls back to
    # the ledger replay and this script ends up with a different squad from
    # the checklist squads.py just wrote — each then reports the other's
    # players as strangers. Loading market.csv costs a second.
    lg = League.load()
    squad = lg.squad(lg.cfg.me)

    # No name map: a squad key IS the normalised name, which is what the
    # app's nickname normalises to, so the join needs nothing extra.
    xi, benched, warnings = fielded(squad)
    if not xi:
        for w in warnings:
            print("  warning:", w)
        print("nothing logged")
        return

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

    print("logged XI of %d from the app (bench: %s)%s"
          % (len(xi), ", ".join(benched) or "—",
             "" if not htl else " — %sh to lock" % htl))
    for w in warnings:
        print("  warning:", w)


def _selftest() -> None:
    squad = ["Alvaro Fernandez", "Beñat Turrientes", "Carl Starfelt",
             "Igor Zubeldia", "Iñigo Ruiz de Galarreta", "Iñigo Vicente",
             "Jon Moncayola", "Lucien Agoume", "Omar El Hilali", "Pepelu",
             "Robin Le Normand", "Marcos Alonso"]
    eleven = [n for n in squad if n not in ("Beñat Turrientes",
                                            "Igor Zubeldia")]

    def rows(names):
        return [{"player_id": "", "player_name": n} for n in names]

    def app(names):
        from ffcore.league import app_fielded
        keys = {norm(n) for n in squad}
        return app_fielded(keys, {}, rows(names), {})

    # THE APP'S ELEVEN, resolved on the nickname the way every other API
    # reader does. Accents are the case that separates norm() from .lower():
    # the squad key is folded, so an unfolded 'ñ' would drop a man out of the
    # eleven and he would be logged as benched when he played.
    got = app(eleven)
    assert len(got) == 10, got
    assert norm("Iñigo Vicente") in got, got
    assert norm("Beñat Turrientes") not in got

    # ALL OR NOTHING. One man the squad does not contain means the two
    # readings disagree, and a half-resolved eleven logged as fielded is worse
    # than no row at all.
    assert app(eleven + ["Somebody Else"]) == []

    # A quiet feed logs NOTHING. This is the whole reason the checklist went:
    # it always had an answer, and the answer was whatever was last ticked.
    xi, bench, warns = fielded([])
    assert xi == [] and bench == []
    assert any("quiet" in w for w in warns), warns

    # An eleven that is not eleven is not a fielded eleven.
    # Patched HERE, in this module's globals, because the name was imported
    # into it — rebinding ffcore.league.app_fielded would leave xi.py holding
    # the original and the test would pass for the wrong reason.
    real = globals()["app_fielded"]
    try:
        globals()["app_fielded"] = lambda *a, **k: [norm(n) for n in eleven]
        xi2, bench2, warns2 = fielded(squad)
        assert xi2 == [] and any("XI of 11" in w for w in warns2), warns2

        globals()["app_fielded"] = lambda *a, **k: [
            norm(n) for n in eleven + ["Beñat Turrientes"]]
        xi3, bench3, warns3 = fielded(squad)
        assert len(xi3) == 11 and not warns3, (xi3, warns3)
        # The bench is the squad minus the eleven, keyed the same way.
        assert bench3 == [norm("Igor Zubeldia")], bench3
    finally:
        globals()["app_fielded"] = real

    print("xi self-test OK (12 cases)")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        raise SystemExit(0)
    main()
