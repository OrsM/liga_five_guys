"""The jornada calendar as schema.sql's jornada_calendar has it, as of
t: when each round locks, the jornadas left, and which jornada a match
belongs to."""
from __future__ import annotations


from datetime import datetime
from ffcore import season_facts, state
from ffcore.parse import snapshot_stamp
from typing import NamedTuple

__all__ = ["Rounds", "rounds_at"]


class Rounds(NamedTuple):
    """The jornadas left (jornada_calendar): those not
    done, by when each next plays (none known last); when each next plays;
    the clubs that have played in each; and those behind (a match put back:
    no line-up is picked for them)."""
    rem: list[int]
    next: dict[int, datetime]
    played: dict[int, set[str]]
    behind: set[int]


def rounds_at(world: state.World) -> Rounds:
    """The Rounds of the World (rule rounds_left_order): rem is the jornadas not done, those with a next kickoff by
    when it is (ties by jornada), those with none last by jornada; next is when each next plays, played the clubs with a
    scored match in each jornada left, behind the jornadas with a match put back (no line-up is picked for them)."""
    calendar = season_facts.jornada_calendar(world)
    nxt = {c.jornada: when for c in sorted(calendar, key=lambda c: (c.next or "", c.jornada))
           if c.next is not None and (when := snapshot_stamp(c.next)) is not None}
    left = {c.jornada: c.state for c in calendar if c.state != "done"}
    rem = [j for j in nxt if j in left] + sorted(set(left) - set(nxt))
    played: dict[int, set[str]] = {}
    for p in season_facts.clubs_played(world):
        played.setdefault(p.jornada, set()).add(p.club)
    return Rounds(rem, {j: nxt[j] for j in rem if j in nxt}, played, {j for j, s in left.items() if s == "behind"})


def _selftest_rounds_at() -> None:
    """rounds_left_order, and the same facts alone, in a sweep and unchanged by rows after the cut."""
    from datetime import timezone

    from ffcore import observations as ob

    def m(at: str, j: int, mid: str, home: str, away: str, score: str, kick: str) -> ob.Matches:
        return ob.Matches(at, "s", j, mid, "", home, away, score, kick)
    t0 = "2026-08-10T0900Z"
    rows_ = [m(t0, 1, "m1", "a", "b", "1-0", "2026-08-10T17:00:00+00:00"),
             m(t0, 2, "m2", "c", "d", "", "2026-08-16T17:00:00+00:00"),
             m(t0, 2, "m3", "e", "f", "", "2026-08-26T17:00:00+00:00"),
             m(t0, 3, "m4", "g", "h", "", "2026-08-22T17:00:00+00:00"),
             m(t0, 4, "m5", "i", "j", "", "2026-08-30T17:00:00+00:00"),
             m(t0, 5, "m6", "k", "l", "", ""),
             m("2026-08-17T0900Z", 2, "m2", "c", "d", "1-0", "")]
    obs = ob.of({"matches": rows_})
    cut = "2026-08-24T0000Z"
    got = rounds_at(state.world_at(obs, cut))
    utc = timezone.utc
    assert got.rem == [3, 2, 4, 5], "by when each next plays, the unknown last, a done jornada not at all"
    assert got.next == {3: datetime(2026, 8, 22, 17, tzinfo=utc), 2: datetime(2026, 8, 26, 17, tzinfo=utc),
                        4: datetime(2026, 8, 30, 17, tzinfo=utc)}
    assert got.played == {2: {"c", "d"}} and got.behind == {2}, "jornada 2 started before jornada 3 did"
    cuts = ["2026-08-09T0000Z", "2026-08-18T0000Z", cut]
    alone = {c: rounds_at(state.world_at(obs, c)) for c in cuts}
    assert state.sweep(obs, cuts, rounds_at) == [alone[c] for c in cuts]
    assert state.sweep(obs, cuts[::-1], rounds_at) == [alone[c] for c in cuts[::-1]]
    later = ob.of({"matches": rows_ + [m("2026-08-25T0900Z", 3, "m4", "g", "h", "2-0", "")]})
    assert all(rounds_at(state.world_at(later, c)) == alone[c] for c in cuts)
    assert alone[cuts[0]] == Rounds([], {}, {}, set())


def _selftest_calendar() -> None:
    """blind_result_keeps_next (a jornada whose result was seen before any kickoff has no lock, still its next
    kickoff) and put_back_match_keeps_lock (a match put back changes neither its jornada's lock nor which lock comes
    next)."""
    from ffcore import observations as ob

    def m(at: str, j: int, mid: str, home: str, away: str, score: str, kick: str) -> ob.Matches:
        return ob.Matches(at, "s", j, mid, "", home, away, score, kick)
    t0 = "2026-08-12T0900Z"
    obs = ob.of({"matches": [
        m(t0, 1, "b1", "alaves", "getafe", "3-0", ""), m(t0, 1, "b2", "celta", "betis", "", "2026-08-14T19:30:00+00:00"),
        m(t0, 2, "m1", "rayo", "osasuna", "", "2026-08-17T21:30:00+02:00"),
        m("2026-08-19T0900Z", 2, "m1", "rayo", "osasuna", "1-0", ""),
        m(t0, 2, "m2", "elche", "levante", "", "2026-08-18T17:00:00+00:00"),
        m(t0, 4, "m4", "rayo", "elche", "", "2026-08-30T17:00:00+00:00"),
        m("2026-08-21T0900Z", 3, "m3", "rayo", "celta", "", "2026-08-23T17:00:00+00:00"),
        m("2026-08-22T0900Z", 2, "m5", "celta", "getafe", "", "2026-08-26T17:00:00+00:00")]})
    before = {c.jornada: c for c in season_facts.jornada_calendar(state.world_at(obs, "2026-08-20T0000Z"))}
    assert before[1] == season_facts.Calendar(1, None, "2026-08-14T1930Z", "behind"), "blind_result_keeps_next"
    assert before[2].lock == "2026-08-17T1930Z", "the newest kickoff, in Madrid time"
    world = state.world_at(obs, "2026-08-24T0000Z")
    after = {c.jornada: c for c in season_facts.jornada_calendar(world)}
    assert after[2].lock == before[2].lock and after[2].state == "behind", "put_back_match_keeps_lock: its jornada's lock"
    assert season_facts.next_lock(world).lock == "2026-08-30T1700Z", \
        "put_back_match_keeps_lock: once jornada 3 has locked, the next lock is jornada 4's"


def _selftest() -> None:
    _selftest_calendar()
    _selftest_rounds_at()
    print("ffcore.jornadas self-test OK")


if __name__ == "__main__":
    _selftest()
