"""The jornada calendar: when each round and each club locks, and which
jornada a match belongs to, read from the matches table."""
from __future__ import annotations


from datetime import datetime
from functools import cache

from ffcore.clock import on_reset, run_now
from ffcore.locks import JornadaClock
from ffcore.tidy import current, history

__all__ = ["clock", "clock_history", "jornada_of_match", "load_deadline"]


@cache
def clock() -> JornadaClock:
    return JornadaClock(current("matches"))


@cache
def clock_history() -> JornadaClock:
    rows = history("matches")
    dated = {(r.get("home"), r.get("away")) for r in rows if r.get("kickoff")}
    blind = {r["jornada"] for r in rows
             if r.get("score") and r.get("jornada") is not None
             and (r.get("home"), r.get("away")) not in dated}
    full = JornadaClock(rows)
    full.round_locks = {j: t for j, t in full.round_locks.items() if j not in blind}
    return full


@cache
def jornada_of_match() -> dict[str, int]:
    out: dict[str, int] = {}
    for m in current("matches"):
        mid = (m.get("match_id") or "").strip()
        if mid and mid not in out and m.get("jornada") is not None:
            out[mid] = m["jornada"]
    return out


def load_deadline() -> datetime | None:
    return clock().next_deadline(run_now())


for _cached in (clock, clock_history, jornada_of_match):
    on_reset(_cached.cache_clear)


def _selftest() -> None:


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
        if mid and mid not in expect and m.get("jornada") is not None:
            expect[mid] = m["jornada"]
    assert j1 == expect, "jornada_of_match() must be first-write-wins"
    print("ffcore.jornadas self-test OK")


if __name__ == "__main__":
    _selftest()
