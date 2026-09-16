
from __future__ import annotations

import sys


from ffcore.league import app_fielded
from ffcore.text import norm
from ffcore.tidy import (run_now,
                         DECISIONS, append_csv, load_deadline, read_csv, write_csv)

FIELDS = ["logged_at", "hours_to_lock", "n_xi", "xi", "bench",
          "xi_names", "bench_names", "warnings"]

XI_SIZE = 11


def fielded(squad: list[str]):
    xi = sorted(app_fielded(squad, {}))
    if not xi:
        return [], [], ["the app's lineup feed is quiet — nothing logged"]
    if len(xi) != XI_SIZE:
        return [], [], ["%d players for an XI of %d — not logged"
                        % (len(xi), XI_SIZE)]
    return xi, sorted(norm(s) for s in squad if norm(s) not in set(xi)), []


def migrate(path, fields) -> None:
    rows = read_csv(path)
    if not rows or set(fields) <= set(rows[0]):
        return
    for r in rows:
        for f in fields:
            r.setdefault(f, "")
    write_csv(path, rows, fields)
    print("migrated %s to %d columns" % (path, len(fields)))


def main() -> None:
    from ffcore.model import session

    lg = session().lg
    squad = lg.squad(lg.cfg.me)

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

    got = app(eleven)
    assert len(got) == 10, got
    assert norm("Iñigo Vicente") in got, got
    assert norm("Beñat Turrientes") not in got

    assert app(eleven + ["Somebody Else"]) == []

    xi, bench, warns = fielded([])
    assert xi == [] and bench == []
    assert any("quiet" in w for w in warns), warns

    real = globals()["app_fielded"]
    try:
        globals()["app_fielded"] = lambda *a, **k: [norm(n) for n in eleven]
        xi2, bench2, warns2 = fielded(squad)
        assert xi2 == [] and any("XI of 11" in w for w in warns2), warns2

        globals()["app_fielded"] = lambda *a, **k: [
            norm(n) for n in eleven + ["Beñat Turrientes"]]
        xi3, bench3, warns3 = fielded(squad)
        assert len(xi3) == 11 and not warns3, (xi3, warns3)
        assert bench3 == [norm("Igor Zubeldia")], bench3
    finally:
        globals()["app_fielded"] = real

    print("xi self-test OK (12 cases)")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        raise SystemExit(0)
    main()
