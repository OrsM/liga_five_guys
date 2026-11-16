"""The jornada calendar as schema.sql's jornada_calendar has it, as of
t: when each round locks, the jornadas left, and which jornada a match
belongs to."""
from __future__ import annotations


from datetime import datetime
from ffcore import season_facts, state
from ffcore.db import rows
from ffcore.parse import snapshot_stamp
from typing import NamedTuple

__all__ = ["Rounds", "round_locks", "rounds", "rounds_ahead", "rounds_at"]


class Rounds(NamedTuple):
    """The jornadas left as rounds reads them (jornada_calendar): those not
    done, by when each next plays (none known last); when each next plays;
    the clubs that have played in each; and those behind (a match put back:
    no line-up is picked for them)."""
    rem: list[int]
    next: dict[int, datetime]
    played: dict[int, set[str]]
    behind: set[int]


def round_locks() -> dict[int, datetime]:
    """Each jornada's lock (round_locks), in lock order."""
    return {j: when for j, at in rows("SELECT jornada, lock FROM round_locks ORDER BY lock, jornada")
            if (when := snapshot_stamp(at)) is not None}


def rounds_ahead() -> dict[int, datetime]:
    """Each jornada with a match still to play: when the next one kicks off (rounds_ahead)."""
    return {j: when for j, at in rows("SELECT jornada, next FROM rounds_ahead ORDER BY next, jornada")
            if (when := snapshot_stamp(at)) is not None}


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


def rounds_at(world: state.World) -> Rounds:
    """rounds() over the World (rule rounds_left_order): rem is the jornadas not done, those with a next kickoff by
    when it is (ties by jornada), those with none last by jornada; next, played (the clubs with a scored match in each
    jornada left) and behind as rounds() has them."""
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


def _selftest() -> None:
    import tempfile
    from datetime import timezone
    from pathlib import Path

    from contextlib import closing

    from ffcore.db import set_now
    from ffcore.db import connect
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
            assert rounds_ahead() == {1: utc(14, 19, 30), 2: utc(18, 17), 4: utc(30, 17)}, \
                "jornada 2 by its match still to play"
            assert rows("SELECT lock FROM next_lock") == [("2026-08-30T1700Z",)]
            set_now(utc(17, 20))
            assert rows("SELECT lock FROM next_lock") == [("2026-08-30T1700Z",)], \
                "the rest of a jornada under way locks nothing: the next jornada's lock"
            set_now(utc(22, 0))
            assert 3 in round_locks(), "read as of t"
            assert rows("SELECT lock FROM next_lock") == [("2026-08-23T1700Z",)], \
                "jornada 3's lock comes before jornada 2's match put back"
            set_now(utc(24, 0))
            store_sql("market", [{"observed_at": "2026-08-23T0900Z", "ff_id": k, "club": c, "value": "1"}
                                 for k, c in (("late", "rayo"), ("early", "elche"), ("theirs", "rayo"))])
            store_sql("api_market", [
                {"observed_at": "2026-08-23T0900Z", "seller": seller, "player_id": app, "sale_price": "100",
                 "expires_at": close}
                for app, seller, close in (("1", "marketPlayerLeague", "2026-08-30T22:24:00+02:00"),
                                           ("2", "marketPlayerLeague", "2026-08-29T22:24:00+02:00"),
                                           ("3", "marketPlayerTeam", "2026-08-30T22:24:00+02:00"))])
            with closing(connect()) as db, db:
                db.executemany("INSERT INTO crosswalk (player_id, app_id) VALUES (?, ?)",
                               [("late", "1"), ("early", "2"), ("theirs", "3")])
            assert sorted(rows("SELECT player, jornada FROM late_arrivals")) == [("late", 3), ("late", 4)], \
                ("his auction closes after rayo kick off in jornadas 3 (under way) and 4: he plays neither; "
                 "elche's man joins the night before; a manager's player plays for him till sold")
            with closing(connect()) as db, db:
                db.execute("INSERT INTO forecast_moment (moment, as_of) VALUES (1, '')")
                db.executemany("INSERT INTO forecast_cell (moment, player, jornada, pts, p) VALUES (1, ?, ?, 5.0, 0.8)",
                               [(k, j) for k in ("late", "early") for j in (3, 4, 5)])
            assert rows("SELECT player, jornada, pts, p FROM forecast_used WHERE moment = 1 ORDER BY player, jornada") \
                == [("early", j, 5.0, 0.8) for j in (3, 4, 5)] + [("late", 3, 5.0, 0.0), ("late", 4, 5.0, 0.0),
                                                                  ("late", 5, 5.0, 0.8)], \
                "the forecast as the decision uses it: a late arrival plays none of a jornada he joins after"
            assert rows("SELECT lock FROM next_lock") == [("2026-08-30T1700Z",)], \
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
            assert rows("SELECT lock FROM next_lock") == [(None,)], "none left to play"
            assert rows("SELECT jornada FROM jornadas_done") == [(2,)], \
                "done: every match has a score (jornada 1 still has one to play, 3 and 4 theirs)"
        finally:
            set_now(None)
    _selftest_rounds_at()
    print("ffcore.jornadas self-test OK")


if __name__ == "__main__":
    _selftest()
