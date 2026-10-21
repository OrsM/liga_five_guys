"""The jornada calendar as schema.sql's jornada_calendar has it, as of
cut(): when each round locks, the jornadas left, and which jornada a match
belongs to."""
from __future__ import annotations


from datetime import datetime
from functools import cache

from ffcore.clock import on_reset
from ffcore.db import rows
from ffcore.parse import snapshot_stamp
from ffcore.schedule import Rounds

__all__ = ["round_locks", "rounds", "rounds_ahead", "jornada_of_match"]


@cache
def round_locks() -> dict[int, datetime]:
    """Each jornada's lock (round_locks), in lock order."""
    return {j: when for j, at in rows("SELECT jornada, lock FROM round_locks ORDER BY lock, jornada")
            if (when := snapshot_stamp(at)) is not None}


@cache
def rounds_ahead() -> dict[int, datetime]:
    """Each jornada with a match still to play: when the next one kicks off (rounds_ahead)."""
    return {j: when for j, at in rows("SELECT jornada, next FROM rounds_ahead ORDER BY next, jornada")
            if (when := snapshot_stamp(at)) is not None}


@cache
def rounds() -> Rounds:
    """The jornadas left (jornada_calendar's not done), by when each next
    plays (rounds_ahead; none known last), the clubs that have played in each
    (clubs_played), and those behind."""
    nxt = rounds_ahead()
    left = {j: state for j, state in rows("SELECT jornada, state FROM jornada_calendar WHERE state <> 'done'")}
    rem = [j for j in nxt if j in left] + sorted(set(left) - set(nxt))
    played: dict[int, set[str]] = {}
    for j, club in rows("SELECT jornada, club FROM clubs_played"):
        played.setdefault(j, set()).add(club)
    return Rounds(rem, {j: nxt[j] for j in rem if j in nxt}, played,
                  {j for j, state in left.items() if state == "behind"})


@cache
def jornada_of_match() -> dict[str, int]:
    return dict(rows("SELECT match_id, jornada FROM match_jornada"))


for _cached in (round_locks, rounds, rounds_ahead, jornada_of_match):
    on_reset(_cached.cache_clear)


def _selftest() -> None:
    import tempfile
    from datetime import timezone
    from pathlib import Path

    from ffcore.clock import set_now
    from ffcore.tidy import store_sql, tables_in

    def utc(d: int, h: int, m: int = 0) -> datetime:
        return datetime(2026, 8, d, h, m, tzinfo=timezone.utc)

    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        try:
            set_now(utc(20, 0))
            store_sql("matches", [
                # Jornada 1's results, scraped before any kickoff was: its lock is unknown.
                {"observed_at": "2026-08-12T0900Z", "match_id": "b1", "jornada": "1",
                 "home": "alaves", "away": "getafe", "score": "3-0", "kickoff": ""},
                {"observed_at": "2026-08-12T0900Z", "match_id": "b2", "jornada": "1",
                 "home": "celta", "away": "betis", "score": "",
                 "kickoff": "2026-08-14T19:30:00+00:00"},
                # Jornada 2: moved once (the last kickoff seen counts), in Madrid time.
                {"observed_at": "2026-08-10T0900Z", "match_id": "m1", "jornada": "2",
                 "home": "rayo", "away": "osasuna", "kickoff": "2026-08-16T21:30:00+02:00"},
                {"observed_at": "2026-08-12T0900Z", "match_id": "m1", "jornada": "2",
                 "home": "rayo", "away": "osasuna", "kickoff": "2026-08-17T21:30:00+02:00"},
                {"observed_at": "2026-08-12T0900Z", "match_id": "m2", "jornada": "2",
                 "home": "elche", "away": "levante", "kickoff": "2026-08-18T17:00:00+00:00"},
                # Played: the page drops the kickoff once the score is up.
                {"observed_at": "2026-08-19T0900Z", "match_id": "m1", "jornada": "2",
                 "home": "rayo", "away": "osasuna", "score": "1-0", "kickoff": ""},
                # Jornada 3: kickoffs seen only after the cut.
                {"observed_at": "2026-08-21T0900Z", "match_id": "m3", "jornada": "3",
                 "home": "rayo", "away": "celta", "kickoff": "2026-08-23T17:00:00+00:00"},
                {"observed_at": "2026-08-12T0900Z", "match_id": "m4", "jornada": "4",
                 "home": "rayo", "away": "elche", "kickoff": "2026-08-30T17:00:00+00:00"},
                # Jornada 2's last match, put back past jornada 3's lock (seen after the first cut).
                {"observed_at": "2026-08-22T0900Z", "match_id": "m5", "jornada": "2",
                 "home": "celta", "away": "getafe", "kickoff": "2026-08-26T17:00:00+00:00"}])
            assert round_locks() == {2: utc(17, 19, 30), 4: utc(30, 17)}, round_locks()
            assert list(round_locks()) == [2, 4], "in lock order"
            assert jornada_of_match() == {"b1": 1, "b2": 1, "m1": 2, "m2": 2, "m4": 4}
            assert rounds_ahead() == {1: utc(14, 19, 30), 2: utc(18, 17), 4: utc(30, 17)}, \
                "jornada 2 by its match still to play"
            assert rows("SELECT kick FROM next_kickoff") == [("2026-08-30T1700Z",)]
            set_now(utc(17, 20))
            assert rows("SELECT kick FROM next_kickoff") == [("2026-08-18T1700Z",)], \
                "the rest of a jornada under way"
            set_now(utc(22, 0))
            assert 3 in round_locks(), "set_now clears the cache"
            assert rows("SELECT kick FROM next_kickoff") == [("2026-08-23T1700Z",)], \
                "jornada 3's lock comes before jornada 2's match put back"
            set_now(utc(24, 0))
            assert rows("SELECT kick FROM next_kickoff") == [("2026-08-30T1700Z",)], \
                "once jornada 3 locks, jornada 2's match put back locks nothing"
            assert rows("SELECT jornada, state FROM jornada_calendar ORDER BY jornada") == [
                (1, "behind"), (2, "behind"), (3, "under way"), (4, "ahead")], \
                rows("SELECT * FROM jornada_calendar ORDER BY jornada")
            assert rounds() == Rounds([1, 2, 3, 4], {1: utc(14, 19, 30), 2: utc(18, 17), 3: utc(23, 17),
                                                     4: utc(30, 17)},
                                      {1: {"alaves", "getafe"}, 2: {"rayo", "osasuna"}}, {1, 2}), \
                ("the jornadas left by when each next plays, the clubs played in each, those behind", rounds())
            store_sql("matches", [
                {"observed_at": "2026-08-27T0900Z", "match_id": m, "jornada": "2", "home": h, "away": a,
                 "score": "1-1", "kickoff": ""} for m, h, a in (("m2", "elche", "levante"),
                                                                ("m5", "celta", "getafe"))])
            set_now(utc(31, 0))
            assert rows("SELECT kick FROM next_kickoff") == [(None,)], "none left to play"
            assert rows("SELECT jornada FROM jornadas_done") == [(2,)], \
                "done: every match has a score (jornada 1 still has one to play, 3 and 4 theirs)"
        finally:
            set_now(None)
    print("ffcore.jornadas self-test OK")


if __name__ == "__main__":
    _selftest()
