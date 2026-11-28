"""The parser self-report's facts: the snapshots of each source (stamps with their row counts) and, over them, the status
of each source as of a cut (rules usual_needs_five, stale_before_thin, last_good_is_newest_usual, rounded_half_up, in
health.py's header; health.py tells it, the loader and the board read it)."""
from __future__ import annotations

from bisect import bisect_right
from collections import Counter
from collections.abc import Iterable
from decimal import ROUND_HALF_UP, Decimal
from typing import NamedTuple

from ffcore import observations as ob
from ffcore.season_facts import stamp_jd

HEALTH_SOURCES = ("market", "lineups", "api_standings", "api_lineup", "api_teams", "api_leagues", "api_market", "api_offers")
HEALTH_USUAL = 21


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


def _selftest() -> None:
    feed = [Snapshot("x", "2026-10-15T%02d00Z" % (20 - 2 * i), 10) for i in range(8)]
    got = parser_health(feed, "2026-10-15T2100Z")
    assert got == (Health("x", "2026-10-15T2000Z", 10, 10.0, 1.0, 2.0, "ok", "2026-10-15T2000Z"),), got
    assert parser_health(feed[:5], "2026-10-15T2100Z") == (), "usual_needs_five"
    print("source_health self-test OK")


if __name__ == "__main__":
    _selftest()
