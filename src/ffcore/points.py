"""Points history: each change in a player's season total, given to the
latest match his club had played, and minutes from a line-up role."""
from __future__ import annotations

from typing import NamedTuple

from ffcore.parse import kickoff_stamp
from ffcore.names import row_key
from ffcore.tidy import current, history

__all__ = ["Scored", "scored"]


class Scored(NamedTuple):
    key: str
    jornada: int
    pts: float
    games: float
    at: str


def _club_matches() -> dict[str, list[tuple[str, int]]]:
    when: dict[tuple, tuple[str, int]] = {}
    for m in sorted(history("matches"), key=lambda r: r.get("observed_at", "")):
        pair, jor = (m.get("home"), m.get("away")), m.get("jornada") or ""
        if not str(jor).isdigit():
            continue
        kick = kickoff_stamp(m.get("kickoff"))
        if kick is not None:
            when[pair] = (kick.strftime("%Y-%m-%dT%H%MZ"), int(jor))
        elif m.get("score") and pair not in when:
            when[pair] = (m.get("observed_at", ""), int(jor))
    out: dict[str, list[tuple[str, int]]] = {}
    for pair, at in when.items():
        for club in pair:
            out.setdefault(club, []).append(at)
    return {c: sorted(v) for c, v in out.items()}


def scored() -> list[Scored]:
    rows = history("points")
    season = max((r.get("season") or "" for r in rows), default="")
    rows = sorted((r for r in rows if r.get("season") == season),
                  key=lambda r: r.get("observed_at", ""))
    club = {r["ff_id"]: r.get("club") for r in current("market")
            if r.get("ff_id")}
    games = _club_matches()
    every = sorted(at for v in games.values() for at in v)
    first = rows[0]["observed_at"] if rows else ""
    last: dict[str, tuple[float, float]] = {}
    out = []
    for r in rows:
        key = row_key(r, ("player_name_full", "player_name"))
        now = (float(r["points"]), float(r["games"]))
        before = last.get(key, (0.0, 0.0))
        if not key or now == before:
            continue
        last[key] = now
        at = r["observed_at"]
        played = [j for t, j in games.get(club.get(key) or "", every) if t <= at]
        if at != first and played:
            out.append(Scored(key, played[-1], now[0] - before[0],
                              now[1] - before[1], at))
    return out


def _selftest() -> None:
    import tempfile
    from pathlib import Path

    from ffcore.clock import set_now
    from ffcore.parse import snapshot_stamp
    from ffcore.tidy import tables_in, write_csv

    a, b, later = "2026-08-01T0900Z", "2026-08-02T0900Z", "2026-08-03T0900Z"
    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)) as tidy:
        try:
            set_now(snapshot_stamp("2026-08-04T0000Z"))
            write_csv(tidy / "market.csv", [
                {"observed_at": a, "ff_id": "1", "club": "x"},
                {"observed_at": a, "ff_id": "2", "club": "y"}])
            write_csv(tidy / "matches.csv", [
                {"observed_at": a, "match_id": "m1", "jornada": "1", "home": "x",
                 "away": "y", "score": "", "kickoff": "2026-08-01T10:00:00+00:00"},
                {"observed_at": later, "match_id": "m2", "jornada": "2",
                 "home": "x", "away": "z", "score": "1-0", "kickoff": ""}])
            write_csv(tidy / "points.csv", [
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
        finally:
            set_now(None)

    print("ffcore.points self-test OK")


if __name__ == "__main__":
    _selftest()
