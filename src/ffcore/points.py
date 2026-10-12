"""The points history, as schema.sql's views have it as of cut(): each
change in a player's season total, by jornada, and his season so far."""
from __future__ import annotations



from ffcore.db import rows
from ffcore.score import Scored

__all__ = ["jornada_points", "scored", "season_record"]


def scored() -> list[Scored]:
    """The scored view: each change in a player's points total this season,
    in the order stored (the forecast's bootstrap draws from it by position)."""
    return [Scored(*r) for r in rows("SELECT player, jornada, pts, games, at FROM scored ORDER BY at, id")]


def jornada_points() -> dict[tuple[str, int], float]:
    """Each player's points in each jornada he scored in."""
    return {(k, j): pts for k, j, pts in rows(
        "SELECT player, jornada, sum(pts) FROM scored GROUP BY player, jornada")}


def season_record() -> dict[str, tuple[float, float, float]]:
    """Each scoring player's season so far: (points, appearances, matchday squads)."""
    return {k: (pts, apps, n) for k, pts, apps, n in rows("SELECT player, pts, apps, squads FROM season_record")}


def _selftest() -> None:
    import tempfile
    from pathlib import Path

    from ffcore.clock import set_now
    from ffcore.db import record
    from ffcore.parse import snapshot_stamp
    from ffcore.rules import MATCH_LEN
    from ffcore.tidy import store_sql, tables_in

    a, b, later = "2026-08-01T0900Z", "2026-08-02T0900Z", "2026-08-03T0900Z"
    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
        try:
            set_now(snapshot_stamp("2026-08-04T0000Z"))
            store_sql("market", [
                {"observed_at": a, "ff_id": "1", "club": "x"},
                {"observed_at": a, "ff_id": "2", "club": "y"}])
            store_sql("matches", [
                {"observed_at": a, "match_id": "m1", "jornada": "1", "home": "x",
                 "away": "y", "score": "", "kickoff": "2026-08-01T10:00:00+00:00"},
                {"observed_at": later, "match_id": "m2", "jornada": "2",
                 "home": "x", "away": "z", "score": "1-0", "kickoff": ""}])
            store_sql("points", [
                {"observed_at": a, "season": "2025-26", "ff_id": "1",
                 "points": "90", "games": "30"},
                *({"observed_at": a, "season": "2026-27", "ff_id": k,
                   "points": "0", "games": "0"} for k in "12"),
                {"observed_at": b, "season": "2026-27", "ff_id": "1",
                 "points": "5", "games": "1"},
                {"observed_at": later, "season": "2026-27", "ff_id": "1",
                 "points": "12", "games": "2"},
                {"observed_at": later, "season": "2026-27", "ff_id": "2",
                 "points": "3", "games": "1"}])
            assert set(scored()) == {Scored("1", 1, 5.0, 1.0, b),
                                     Scored("1", 2, 7.0, 1.0, later),
                                     Scored("2", 1, 3.0, 1.0, later)}, scored()
            assert jornada_points() == {("1", 1): 5.0, ("1", 2): 7.0, ("2", 1): 3.0}

            record([{"name": "MATCH_LEN", "value": MATCH_LEN}], "constants")
            record([{"player_id": k, "ff_slug": slug} for k, slug in
                    [("1", "uno"), ("2", "dos"), ("3", "tres")]], "crosswalk")
            store_sql("starters", [
                {"observed_at": a, "match_id": "m1", "team_slug": "x", "player_slug": p,
                 "player_name": n, "role": r, "minute": m}
                for p, n, r, m in [("uno", "1", "starter", ""), ("dos", "2", "sub", "70"),
                                   ("tres", "3", "sub", ""), ("uno-coach", "1", "coach", "")]] + [
                {"observed_at": later, "match_id": "m2", "team_slug": "x", "player_slug": "uno",
                 "player_name": "1", "role": "starter", "minute": "45"}])
            assert season_record() == {"1": (12.0, 2.0, 2.0), "2": (3.0, 1.0, 1.0)}, \
                "a starter the match unless he came off, a sub from when he came on"
        finally:
            set_now(None)

    print("ffcore.points self-test OK")


if __name__ == "__main__":
    _selftest()
