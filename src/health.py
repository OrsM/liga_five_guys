"""Parser self-report: a source whose newest snapshot is late or thin is
told to you, with the last good one to start from. The view decides
(schema.sql: parser_health); this reads it for the shell (lfg-run).

    python src/health.py text    # what to tell you, empty if every source is fine
"""
from __future__ import annotations

import sys

from ffcore.db import rows

__all__ = ["text"]


def text() -> str:
    """One line a source that is not ok, empty if all are."""
    lines = []
    for src, last_at, n, usual, hours, gap, status, good in rows(
            "SELECT src, last_at, rows, usual_rows, hours_since, usual_gap_h, status, last_good"
            " FROM parser_health WHERE status <> 'ok' ORDER BY src"):
        what = ("no snapshot for %.0fh (one every %.0fh)" % (hours, gap) if status == "stale"
                else "%d rows against the usual %.0f" % (n, usual))
        lines.append("%s: %s; last good snapshot %s (data/raw/dt=%s.tar.xz)"
                     % (src, what, good, good))
    return ("parser trouble:\n" + "\n".join(lines)) if lines else ""


def _selftest() -> None:
    import tempfile
    from datetime import datetime, timedelta, timezone
    from pathlib import Path

    from ffcore.clock import set_now
    from ffcore.tidy import store_sql, tables_in

    now = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)

    def stamp(ago_h: float) -> str:
        return (now - timedelta(hours=ago_h)).strftime("%Y-%m-%dT%H%MZ")

    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        try:
            set_now(now)
            assert text() == "", "nothing fetched yet: nothing to say"
            # 21 snapshots `gap` hours apart, the newest `last` hours ago with `n` rows (the rest `usual`)
            def feed(table: str, key: str, last: float, n: int, usual: int = 10, gap: float = 2.0,
                     count: int = 21) -> None:
                for i in range(count):
                    store_sql(table, [{"observed_at": stamp(last + gap * i), key: "p%d" % j}
                                      for j in range(n if i == 0 else usual)])
            feed("market", "ff_id", last=1, n=5)                    # 5 of 10 is half: not thin
            feed("lineups", "player_slug", last=1, n=3)             # thin: 3 rows against 10
            feed("api_standings", "user_id", last=30, n=10)         # stale: 30h, one every 2h
            feed("api_teams", "player_id", last=8, n=10, gap=4.0)   # 8h late of every 4h: under 3 gaps, ok
            feed("api_lineup", "player_id", last=4, n=10, gap=0.5)  # 4h late of every half hour: under 6h, ok
            feed("api_leagues", "league_id", last=1, n=10, count=4)    # too few to know the usual: not judged
            got = {r[0]: r for r in rows("SELECT src, status, last_good FROM parser_health")}
            assert {s: v[1] for s, v in got.items()} == {
                "market": "ok", "lineups": "thin", "api_standings": "stale", "api_teams": "ok",
                "api_lineup": "ok"}, got
            assert got["lineups"][2] == stamp(3), "the last good one is the newest of the usual size"
            out = text()
            assert out.startswith("parser trouble:") and "lineups: 3 rows against the usual 10" in out \
                and "api_standings: no snapshot for 30h" in out and "market" not in out, out
        finally:
            set_now(None)
    print("health self-test OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        raise SystemExit(0)
    sys.stdout.write(text() + "\n" if sys.argv[1:] == ["text"] and text() else "")
