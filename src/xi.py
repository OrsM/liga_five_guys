"""
xi.py — record the XI you actually fielded.

    python src/xi.py                 # log today's XI
    python src/xi.py --selftest

Read from the app (ffcore.league.app_fielded), never typed or ticked, and
never logged when the app is quiet — a gap is honest, an invented row is not.

hours_to_lock is stamped on every row so a later grade can take the last
row before kickoff per jornada.

Appends one immutable row per run to data/decisions/xi_fielded.csv — never
edited, so the latest row for a date is the XI that stood.

Not xi_log.csv (a different schema, report.py's best-XI *suggestion* —
what you were advised, not what you fielded).
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ffcore.league import app_fielded  # noqa: E402
from ffcore.text import norm  # noqa: E402
from ffcore.tidy import (run_now,  # noqa: E402
                         DECISIONS, append_csv, load_deadline, read_csv, write_csv)

FIELDS = ["logged_at", "hours_to_lock", "n_xi", "xi", "bench",
          "xi_names", "bench_names", "warnings"]

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
        return [], [], ["%d players for an XI of %d — not logged"
                        % (len(xi), XI_SIZE)]
    return xi, sorted(norm(s) for s in squad if norm(s) not in set(xi)), []


def migrate(path, fields) -> None:
    """Widen an existing log to `fields`, once, filling old rows blank —
    appending a wider row to a narrower header would silently shift every
    column.
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
    # session().lg — the one League+Scorer report.py/decide.py/sim.py share
    # per run, not a second load.
    from ffcore.model import session

    lg = session().lg
    squad = lg.squad(lg.cfg.me)

    # A squad key is the site's ID now, not a normalised name, so the log
    # gets both: the ids are what a later grade joins on, and the names are
    # what makes a row anybody keeps for months readable.
    xi, benched, warnings = fielded(squad)
    named = {k: (v.get("name") or k)
             for k, v in (lg.market.latest().items() if lg.market else ())}
    if not xi:
        for w in warnings:
            print("  warning:", w)
        print("nothing logged")
        return

    now = run_now()
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
        "xi_names": "|".join(named.get(k, k) for k in xi),
        "bench_names": "|".join(named.get(k, k) for k in benched),
        "warnings": "; ".join(warnings),
    }], FIELDS)

    print("logged XI of %d from the app (bench: %s)%s"
          % (len(xi), ", ".join(named.get(k, k) for k in benched) or "—",
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

    # Accents matter: the squad key is norm()-folded, so an unfolded 'ñ'
    # would drop a man from the eleven.
    got = app(eleven)
    assert len(got) == 10, got
    assert norm("Iñigo Vicente") in got, got
    assert norm("Beñat Turrientes") not in got

    # ALL OR NOTHING: one unresolved man means a half-resolved eleven,
    # logged as nothing rather than as a fielded XI.
    assert app(eleven + ["Somebody Else"]) == []

    xi, bench, warns = fielded([])
    assert xi == [] and bench == []
    assert any("quiet" in w for w in warns), warns

    # Patched in this module's globals (the name was imported into it) —
    # rebinding ffcore.league.app_fielded directly wouldn't reach here.
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
