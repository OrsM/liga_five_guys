
from __future__ import annotations

import sys


from ffcore.text import norm
from ffcore.tidy import (history, SEASON, write_csv)

LIVE = SEASON / "live"

DIFF_FIELDS = ["from_stamp", "to_stamp", "season", "ff_id", "player_name",
               "player_name_full", "team", "points_delta", "games_delta",
               "points_total", "games_total", "jornada"]


def match_jornadas(matches_history: list[dict]) -> list[tuple[str, int]]:
    first_scored: dict[str, tuple[str, int]] = {}
    for r in sorted(matches_history, key=lambda r: r.get("observed_at", "")):
        mid = (r.get("match_id") or "").strip()
        score = (r.get("score") or "").strip()
        if not mid or not score or mid in first_scored:
            continue
        try:
            jor = int(r.get("jornada"))
        except (TypeError, ValueError):
            continue
        first_scored[mid] = (r.get("observed_at", ""), jor)
    return sorted(first_scored.values())


def jornada_asof(timeline: list[tuple[str, int]], stamp: str) -> int | None:
    best = None
    for when, jor in timeline:
        if when <= stamp:
            best = jor
        else:
            break
    return best


def player_key(r: dict) -> str:
    return ((r.get("ff_id") or "").strip()
            or norm(r.get("player_name_full") or r.get("player_name") or ""))


def deltas(rows: list[dict], season: str,
           jornada_timeline: list[tuple[str, int]] = ()) -> list[dict]:
    rows = sorted(rows, key=lambda r: r.get("observed_at", ""))
    last: dict[str, tuple[float, float]] = {}
    moved: dict[str, list[tuple[dict, tuple[float, float]]]] = {}
    for r in rows:
        key = player_key(r)
        if not key:
            continue
        now = (float(r["points"]), float(r["games"]))
        before = last.get(key, (0.0, 0.0))
        if before != now:
            moved.setdefault(r["observed_at"], []).append((r, before))
            last[key] = now
    stamps = sorted(set(moved) | ({rows[0]["observed_at"]} if rows else set()))
    out = []
    for s0, s1 in zip(stamps, stamps[1:]):
        jor = jornada_asof(jornada_timeline, s1)
        for r, (p0, j0) in moved.get(s1, []):
            pts, pj = float(r["points"]), float(r["games"])
            out.append({
                "from_stamp": s0, "to_stamp": s1, "season": season,
                "ff_id": (r.get("ff_id") or "").strip(),
                "player_name": r.get("player_name", ""),
                "player_name_full": r.get("player_name_full", ""),
                "team": r.get("team", ""),
                "points_delta": f"{pts - p0:g}",
                "games_delta": f"{pj - j0:g}",
                "points_total": f"{pts:g}",
                "games_total": f"{pj:g}",
                "jornada": "" if jor is None else str(jor),
            })
    return out


def main() -> None:
    by_season: dict[str, list[dict]] = {}
    for r in history("points"):
        by_season.setdefault(r["season"], []).append(r)
    if not by_season:
        sys.exit("no points page found in any snapshot under data/raw/ — "
                 "run ingest.py fetch first")
    timeline = match_jornadas(history("matches"))
    LIVE.mkdir(parents=True, exist_ok=True)
    for season, rows in sorted(by_season.items()):
        flat = deltas(rows, season, timeline)
        write_csv(LIVE / f"perjornada_{season}.csv", flat, DIFF_FIELDS)
        print(f"{season}: {len(rows)} rows -> {len(flat)} per-jornada rows")


def _selftest() -> None:
    snaps = [("t0", [("Ane Aldea", "0", "0"), ("Bo Bidal", "0", "0")]),
             ("t1", [("Ane Aldea", "0", "0"), ("Bo Bidal", "0", "0")]),
             ("t2", [("Ane Aldea", "8", "1"), ("Bo Bidal", "0", "0")]),
             ("t3", [("Ane Aldea", "8", "1"), ("Bo Bidal", "3", "1"),
                     ("Cai Coro", "5", "1")])]
    full = [{"observed_at": t, "player_name": name, "player_name_full": name,
             "team": "X", "points": pts, "games": pj, "avg": ""}
            for t, snap in snaps for name, pts, pj in snap]
    changed_only = [r for i, r in enumerate(full)
                    if not any(q["player_name"] == r["player_name"]
                               and (q["points"], q["games"])
                               == (r["points"], r["games"])
                               for q in full[:i])]
    late = [dict(r, observed_at="t4", player_name=n, player_name_full=n,
                 points="0", games="0") for r, n in zip(full[:1], ["Dee Dow"])]
    assert deltas(full + late, "s") == deltas(full, "s")
    zeros = [dict(r, points="0", games="0") for r in full if r["observed_at"] == "t0"]
    assert [r["from_stamp"] for r in deltas(zeros + full[2:], "s")][0] == "t0"
    for rows in (full, changed_only):
        got = [(r["from_stamp"], r["to_stamp"], r["player_name_full"],
                r["points_delta"], r["games_delta"]) for r in deltas(rows, "s")]
        assert got == [("t0", "t2", "Ane Aldea", "8", "1"),
                       ("t2", "t3", "Bo Bidal", "3", "1"),
                       ("t2", "t3", "Cai Coro", "5", "1")], got

    history = [
        {"observed_at": "t0", "match_id": "1", "jornada": "1", "score": ""},
        {"observed_at": "t1", "match_id": "1", "jornada": "1", "score": "2-0"},
        {"observed_at": "t2", "match_id": "1", "jornada": "1", "score": "2-0"},
        {"observed_at": "t3", "match_id": "2", "jornada": "2", "score": "1-1"},
        {"observed_at": "t1b", "match_id": "3", "jornada": "", "score": "0-0"},
    ]
    tl = match_jornadas(history)
    assert tl == [("t1", 1), ("t3", 2)], tl
    assert jornada_asof(tl, "t0") is None
    assert jornada_asof(tl, "t1") == 1
    assert jornada_asof(tl, "t2") == 1
    assert jornada_asof(tl, "t3") == 2
    assert jornada_asof(tl, "t9") == 2

    assert [r["jornada"] for r in deltas(full, "s", tl)] == ["1", "2", "2"]
    assert deltas(full, "s")[0]["jornada"] == ""

    print("points.py selftest OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        main()
