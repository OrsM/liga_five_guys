"""The jornada calendar: when each round and each club locks, and which
jornada a match belongs to, read from the matches table."""
from __future__ import annotations

from datetime import datetime

from ffcore.clock import kickoff_stamp, on_reset, run_now
from ffcore.tidy import current, history

__all__ = ["lock_order", "JornadaClock", "clock", "clock_history",
           "jornada_of_match", "load_deadline"]


def lock_order(locks: dict[int, datetime]) -> list[int]:
    return [j for j, _ in sorted(locks.items(), key=lambda kv: kv[1])]


class JornadaClock:

    def __init__(self, matches: list[dict]):
        latest: dict[tuple, tuple[int, datetime]] = {}
        for m in sorted(matches, key=lambda r: r.get("observed_at", "")):
            when = kickoff_stamp(m.get("kickoff"))
            jor = m.get("jornada") or ""
            if when is not None and str(jor).isdigit():
                latest[(m.get("home"), m.get("away"))] = (int(jor), when)
        self.team_locks: dict[tuple[int, str], datetime] = {}
        for (home, away), (jor, when) in latest.items():
            for team in (home, away):
                key = (jor, team)
                if key not in self.team_locks or when < self.team_locks[key]:
                    self.team_locks[key] = when

        self.round_locks: dict[int, datetime] = {}
        for (jor, _team), when in self.team_locks.items():
            if jor not in self.round_locks or when < self.round_locks[jor]:
                self.round_locks[jor] = when

    def team_lock(self, jornada: int, team: str) -> datetime | None:
        return self.team_locks.get((jornada, team))

    def round_lock(self, jornada: int) -> datetime | None:
        return self.round_locks.get(jornada)

    @property
    def order(self) -> list[int]:
        return lock_order(self.round_locks)

    def next_deadline(self, now: datetime) -> datetime | None:
        ahead = [t for t in self.round_locks.values() if t > now]
        return min(ahead) if ahead else None


_CLOCK: list = []


_CLOCK_HISTORY: list = []


def clock() -> JornadaClock:
    if not _CLOCK:
        _CLOCK.append(JornadaClock(current("matches")))
    return _CLOCK[0]


def clock_history() -> JornadaClock:
    if not _CLOCK_HISTORY:
        rows = history("matches")
        dated = {(r.get("home"), r.get("away")) for r in rows if r.get("kickoff")}
        blind = {int(r["jornada"]) for r in rows
                 if r.get("score") and str(r.get("jornada")).isdigit()
                 and (r.get("home"), r.get("away")) not in dated}
        full = JornadaClock(rows)
        full.round_locks = {j: t for j, t in full.round_locks.items()
                            if j not in blind}
        _CLOCK_HISTORY.append(full)
    return _CLOCK_HISTORY[0]


_JORNADA_OF_MATCH: list = []


def jornada_of_match() -> dict[str, int]:
    if not _JORNADA_OF_MATCH:
        out: dict[str, int] = {}
        for m in current("matches"):
            mid = (m.get("match_id") or "").strip()
            if not mid or mid in out:
                continue
            try:
                out[mid] = int(m.get("jornada"))
            except (TypeError, ValueError):
                continue
        _JORNADA_OF_MATCH.append(out)
    return _JORNADA_OF_MATCH[0]


def load_deadline(with_source: bool = False):
    when = clock().next_deadline(run_now())
    return (when, "calendar" if when else "none") if with_source else when


for _cache in (_CLOCK, _CLOCK_HISTORY, _JORNADA_OF_MATCH):
    on_reset(_cache.clear)


def _selftest() -> None:
    from datetime import timezone

    jl_matches = [
        {"observed_at": "2026-08-10T0900Z", "jornada": "1", "home": "alaves",
         "away": "getafe", "kickoff": "2026-08-14T19:30:00+00:00"},
        {"observed_at": "2026-08-12T0900Z", "jornada": "1", "home": "alaves",
         "away": "getafe", "kickoff": "2026-08-15T19:30:00+00:00"},
        {"observed_at": "2026-08-12T0900Z", "jornada": "1",
         "home": "espanyol", "away": "levante",
         "kickoff": "2026-08-16T17:00:00+00:00"},
        {"observed_at": "2026-08-12T0900Z", "jornada": "2",
         "home": "rayo-vallecano", "away": "alaves", "kickoff": ""}]
    cal = JornadaClock(jl_matches)
    jl = cal.round_locks
    assert list(jl) == [1] and jl[1].day == 15, jl
    assert 2 not in jl
    assert cal.round_lock(1) == jl[1] and cal.round_lock(2) is None
    assert cal.team_lock(1, "alaves") == jl[1]
    assert cal.team_lock(1, "espanyol") > jl[1]
    assert cal.next_deadline(
        datetime(2026, 8, 15, 20, tzinfo=timezone.utc)) is None
    assert cal.next_deadline(
        datetime(2026, 8, 15, 18, tzinfo=timezone.utc)) == jl[1]
    assert lock_order({3: datetime(2026, 9, 3, tzinfo=timezone.utc),
                       1: datetime(2026, 8, 15, tzinfo=timezone.utc),
                       2: datetime(2026, 8, 20, tzinfo=timezone.utc)}) \
        == [1, 2, 3]
    assert cal.order == [1]

    c1 = clock()
    c2 = clock()
    assert c1 is c2, "clock() must return the SAME object on a second call"

    _full = clock_history()
    assert _full is clock_history(), "clock_history() must be memoized too"
    assert set(c1.team_locks) <= set(_full.team_locks)
    assert isinstance(c1, JornadaClock)

    j1 = jornada_of_match()
    j2 = jornada_of_match()
    assert j1 is j2, "jornada_of_match() must be memoized"
    expect: dict[str, int] = {}
    for m in current("matches"):
        mid = (m.get("match_id") or "").strip()
        if mid and mid not in expect:
            try:
                expect[mid] = int(m.get("jornada"))
            except (TypeError, ValueError):
                continue
    assert j1 == expect, "jornada_of_match() must be first-write-wins"
    print("ffcore.jornadas self-test OK")


if __name__ == "__main__":
    _selftest()
