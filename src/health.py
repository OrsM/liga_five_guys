"""Parser self-report: a source whose newest snapshot is late or thin is
told to you, with the last good one to start from: once, again only when
the trouble changes, forgotten when it is gone. parser_health (the view in
schema.sql, or the Python parser_health over snapshots_of) decides; main is
the run's health stage.

Named rules, each tested:
  usual_needs_five: a source is judged only with at least 5 snapshots among the 20 before its newest.
  stale_before_thin: stale when its newest is later than max(6 h, 3 usual gaps), else thin when under half the usual
    rows, else ok.
  last_good_is_newest_usual: last_good is the newest snapshot (of all, up to the cut) of at least half the usual
    rows.
  rounded_half_up: usual_rows, hours_since and usual_gap_h are rounded to a tenth, a tie away from zero (as SQLite).

    python src/health.py text    # what to tell you, empty if every source is fine
"""
from __future__ import annotations

import sys
from bisect import bisect_right
from collections import Counter
from collections.abc import Iterable
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import NamedTuple

from ffcore import observations as ob
from ffcore.db import HEALTH_SOURCES, HEALTH_USUAL, rows, stamp_jd
from ffcore.identity import Unmatched
from publish import notify

__all__ = ["Snapshot", "Health", "main", "parser_health", "snapshots_of", "text", "trouble"]

SENT = Path.home() / ".local/state/lfg/health.sent"
UNMATCHED_FROM = 5e6


class Snapshot(NamedTuple):
    src: str
    at: str
    n: int


class Health(NamedTuple):
    src: str
    last_at: str
    rows: int
    usual_rows: float
    hours_since: float
    usual_gap_h: float
    status: str
    last_good: str | None


def tenth(x: float) -> float:
    """x rounded to a tenth, a tie away from zero (rule rounded_half_up)."""
    return float(Decimal(x).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def julian(stamp: str) -> float:
    """The Julian day of an observed_at stamp (every stored one is a stamp)."""
    day = stamp_jd(stamp)
    assert day is not None, stamp
    return day


def snapshots_of(obs: ob.Observations, cut: str) -> tuple[Snapshot, ...]:
    """The snapshots of each HEALTH_SOURCES table obs holds (the stamps at or before cut, with their row counts)."""
    out: list[Snapshot] = []
    for src in HEALTH_SOURCES:
        stamps = ob.stamps_of(obs, src)
        counts = Counter(stamps[:bisect_right(stamps, cut)])
        out.extend(Snapshot(src, at, counts[at]) for at in sorted(counts))
    return tuple(out)


def parser_health(snapshots: Iterable[Snapshot], cut: str) -> tuple[Health, ...]:
    """The status of each source that has a usual (rules usual_needs_five, stale_before_thin,
    last_good_is_newest_usual) as of cut."""
    by_src: dict[str, list[Snapshot]] = {}
    for s in sorted(snapshots, key=lambda s: s.at, reverse=True):
        by_src.setdefault(s.src, []).append(s)
    now = julian(cut)
    out: list[Health] = []
    for src in sorted(by_src):
        newest, *older = by_src[src][:HEALTH_USUAL]
        before = older
        if len(before) < 5:
            continue
        usual = sum(s.n for s in before) / len(before)
        days = [julian(s.at) for s in before]
        gap = (max(days) - min(days)) * 24 / (len(before) - 1)
        late = (now - julian(newest.at)) * 24
        status = "stale" if late > max(6.0, 3 * gap) else "thin" if newest.n < 0.5 * usual else "ok"
        good = max((s.at for s in by_src[src] if s.n >= 0.5 * usual), default=None)
        out.append(Health(src, newest.at, newest.n, tenth(usual), tenth(late), tenth(gap), status, good))
    return tuple(out)


def health_line(h: Health) -> str:
    what = ("no snapshot for %.0fh (one every %.0fh)" % (h.hours_since, h.usual_gap_h) if h.status == "stale"
            else "%d rows against the usual %.0f" % (h.rows, h.usual_rows))
    return "%s: %s; last good snapshot %s (data/raw/dt=%s.tar.xz)" % (h.src, what, h.last_good, h.last_good)


def unmatched_line(u: Unmatched) -> str:
    return "unmatched: %s (%s, %.1fM) has no %s" % (u.name, u.club, u.value / 1e6, "app id" if u.no_app else "probable-XI slug")


def trouble(health: Iterable[Health], unmatched: Iterable[Unmatched]) -> list[str]:
    """One line a source that is not ok, then one a player worth UNMATCHED_FROM or more, dearest first, that the app
    or the line-ups do not reach."""
    return [health_line(h) for h in sorted(health, key=lambda h: h.src) if h.status != "ok"] + [
        unmatched_line(u) for u in sorted(unmatched, key=lambda u: -u.value) if u.value >= UNMATCHED_FROM]


def text() -> str:
    """One line a source that is not ok, and one a player worth UNMATCHED_FROM or more the app or the line-ups do
    not reach; empty if all is well."""
    lines = trouble(
        (Health._make(r) for r in rows(
            "SELECT src, last_at, rows, usual_rows, hours_since, usual_gap_h, status, last_good"
            " FROM parser_health WHERE status <> 'ok' ORDER BY src")),
        (Unmatched._make(r) for r in rows("SELECT player, name, club, value, no_app, no_slug FROM unmatched"
                                          " WHERE value >= ? ORDER BY value DESC", (UNMATCHED_FROM,))))
    drift = list(rows(
            "SELECT scope, bin, n, stated, observed FROM auction_calibration WHERE drift = 1"
            " AND run_id = (SELECT max(run_id) FROM auction_calibration) ORDER BY abs(stated - observed) DESC, scope, bin"))
    if drift:
        scope, bin_, n, stated, observed = drift[0]
        scopes = ", ".join(sorted({scope for scope, _, _, _, _ in drift}))
        lines.append("auction forecast drifting in %d bins (%s); worst %s, chance %s: stated %.2f, observed %.2f over %d"
                     % (len(drift), scopes, scope, bin_, stated, observed, n))
    return ("parser trouble:\n" + "\n".join(lines)) if lines else ""


def main(sent: Path = SENT) -> dict[str, int]:
    """The health stage: tell text() if it is new (sent holds what was
    told last), forget it when there is nothing to say."""
    said = text()
    if not said:
        sent.unlink(missing_ok=True)
    elif not sent.is_file() or sent.read_text(encoding="utf-8") != said:
        if notify(said):
            sent.parent.mkdir(parents=True, exist_ok=True)
            sent.write_text(said, encoding="utf-8")
    return {"trouble": int(bool(said))}


def _selftest() -> None:
    import tempfile
    from contextlib import closing
    from datetime import datetime, timedelta, timezone
    from pathlib import Path

    from ffcore.db import connect, set_now
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
            feed("api_market", "market_id", last=1, n=2)            # thin: 2 rows against 10
            got = {r[0]: r for r in rows("SELECT src, status, last_good FROM parser_health")}
            assert {s: v[1] for s, v in got.items()} == {
                "market": "ok", "lineups": "thin", "api_standings": "stale", "api_teams": "ok",
                "api_lineup": "ok", "api_market": "thin"}, got
            assert got["lineups"][2] == stamp(3), "the last good one is the newest of the usual size"
            out = text()
            assert out.startswith("parser trouble:") and "lineups: 3 rows against the usual 10" in out \
                and "api_standings: no snapshot for 30h" in out and "\nmarket:" not in out, out
        finally:
            set_now(None)

    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        try:
            set_now(now)
            store_sql("market", [{"observed_at": stamp(1), "ff_id": i, "name": n, "club": "x", "value": v}
                                 for i, n, v in [("s", "Star Man", "6000000"), ("t", "Cheap Man", "1000000"),
                                                 ("m", "Matched Man", "9000000")]])
            with closing(connect()) as db, db:
                db.executemany("INSERT INTO crosswalk (player_id, name, club_id, ff_slug, app_id) VALUES (?, ?, 'x', 'slug', ?)",
                               [("s", "Star Man", ""), ("t", "Cheap Man", ""), ("m", "Matched Man", "7")])
            out = text()
            assert "unmatched: Star Man (x, 6.0M) has no app id" in out and "Cheap Man" not in out and "Matched" not in out, \
                "a player of UNMATCHED_FROM or more the app does not reach is told; a cheap one or a matched one is not"
        finally:
            set_now(None)

    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        try:
            set_now(now)
            with closing(connect()) as db, db:
                db.executemany("INSERT INTO auction_calibration (run_id, scope, bin, n, stated, observed, drift) VALUES (?, ?, ?, ?, ?, ?, ?)",
                               [(1, "rookies", "high", 52, 0.93, 0.77, 1), (1, "veterans", "mid", 42, 0.97, 0.85, 1),
                                (1, "keepers", "low", 30, 0.50, 0.49, 0)])
            out = text()
            drifting = [line for line in out.splitlines() if "drifting" in line]
            assert len(drifting) == 1, out
            assert "rookies" in drifting[0] and "veterans" in drifting[0] and "0.93" in drifting[0] and "0.77" in drifting[0], out
            assert "keepers" not in out, out
        finally:
            set_now(None)
    def feed_py(src: str, last: float, n: int, usual: int = 10, gap: float = 2.0, count: int = 21) -> list[Snapshot]:
        return [Snapshot(src, stamp(last + gap * i), n if i == 0 else usual) for i in range(count)]
    snaps = (feed_py("market", 1, 5) + feed_py("lineups", 1, 3) + feed_py("api_standings", 30, 10)
             + feed_py("api_teams", 8, 10, gap=4.0) + feed_py("api_leagues", 1, 10, count=6)
             + feed_py("api_market", 1, 2) + feed_py("api_offers", 1, 10, count=5))
    got_py = {h.src: h for h in parser_health(snaps, stamp(0))}
    assert "api_offers" not in got_py and "api_leagues" in got_py, "usual_needs_five: 4 snapshots before the newest is too few"
    assert {s: h.status for s, h in got_py.items()} == {
        "market": "ok", "lineups": "thin", "api_standings": "stale", "api_teams": "ok", "api_leagues": "ok",
        "api_market": "thin"}, "stale_before_thin"
    assert got_py["lineups"].last_good == stamp(3), "last_good_is_newest_usual"
    tie = [Snapshot("x", stamp(1), 12)] + [Snapshot("x", stamp(3 + 2 * i), n) for i, n in enumerate([12] * 6 + [13, 13])]
    assert tenth(12.35) == 12.3 and parser_health(tie, stamp(0))[0].usual_rows == 12.3, \
        "rounded_half_up"
    print("health self-test OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        raise SystemExit(0)
    sys.stdout.write(text() + "\n" if sys.argv[1:] == ["text"] and text() else "")
