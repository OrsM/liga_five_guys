"""The jornada calendar: when each round and each club locks, and which
jornada a match belongs to, read from the matches table."""
from __future__ import annotations


from ffcore.clock import on_reset, run_now
from ffcore.locks import JornadaClock
from ffcore.tidy import current, history

__all__ = ["clock", "clock_history", "jornada_of_match", "load_deadline"]


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
                out[mid] = int(m.get("jornada") or "")
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
                expect[mid] = int(m.get("jornada") or "")
            except (TypeError, ValueError):
                continue
    assert j1 == expect, "jornada_of_match() must be first-write-wins"
    print("ffcore.jornadas self-test OK")


if __name__ == "__main__":
    _selftest()
