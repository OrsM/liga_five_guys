"""When each jornada and each club locks, computed from match rows: the
earliest kickoff of a round, and each club's own kickoff in it. Pure: the
cached readers of the matches table are in ffcore.jornadas."""
from __future__ import annotations

from datetime import datetime

from ffcore.parse import kickoff_stamp

__all__ = ["JornadaClock", "lock_order"]


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
    late = cal.team_lock(1, "espanyol")
    assert late is not None and late > jl[1]
    assert cal.next_deadline(
        datetime(2026, 8, 15, 20, tzinfo=timezone.utc)) is None
    assert cal.next_deadline(
        datetime(2026, 8, 15, 18, tzinfo=timezone.utc)) == jl[1]
    assert lock_order({3: datetime(2026, 9, 3, tzinfo=timezone.utc),
                       1: datetime(2026, 8, 15, tzinfo=timezone.utc),
                       2: datetime(2026, 8, 20, tzinfo=timezone.utc)}) \
        == [1, 2, 3]
    assert cal.order == [1]
    print("ffcore.locks self-test OK")


if __name__ == "__main__":
    _selftest()
