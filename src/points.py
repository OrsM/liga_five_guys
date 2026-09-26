
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


def totals(rows: list[dict]) -> dict[str, tuple[float, float]]:
    out = {}
    for r in rows:
        key = player_key(r)
        if key:
            out[key] = (float(r["points"]), float(r["games"]))
    return out


def keep_changed(seq: list[tuple[str, list[dict]]]) -> list[tuple[str, list[dict]]]:
    kept, prev = [], None
    for stamp, rows in seq:
        cur = totals(rows)
        if cur != prev:
            kept.append((stamp, rows))
            prev = cur
    return kept


def diff(prev_rows: list[dict], cur_rows: list[dict],
         from_stamp: str, to_stamp: str, season: str,
         jornada_timeline: list[tuple[str, int]] = ()) -> list[dict]:
    prev = totals(prev_rows)
    jor = jornada_asof(jornada_timeline, to_stamp)
    out = []
    for r in cur_rows:
        key = player_key(r)
        if not key:
            continue
        pts, pj = float(r["points"]), float(r["games"])
        p0, j0 = prev.get(key, (0.0, 0.0))
        if pts == p0 and pj == j0:
            continue
        out.append({
            "from_stamp": from_stamp, "to_stamp": to_stamp, "season": season,
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


def load_snapshots() -> dict[str, list[tuple[str, list[dict]]]]:
    by_label: dict[str, dict[str, list[dict]]] = {}
    for r in history("points"):
        by_label.setdefault(r["season"], {}).setdefault(
            r["observed_at"], []).append(r)
    return {label: sorted(stamps.items()) for label, stamps in by_label.items()}


def main() -> None:
    by_label = load_snapshots()
    if not by_label:
        sys.exit("no points page found in any snapshot under data/raw/ — "
                 "run ingest.py fetch first")

    timeline = match_jornadas(history("matches"))

    for label, seq in sorted(by_label.items()):
        kept = keep_changed(seq)

        deltas = []
        for (s0, r0), (s1, r1) in zip(kept, kept[1:]):
            deltas.append(diff(r0, r1, s0, s1, label, timeline))

        LIVE.mkdir(parents=True, exist_ok=True)
        flat = [row for d in deltas for row in d]
        write_csv(LIVE / f"perjornada_{label}.csv", flat, DIFF_FIELDS)

        moved = sum(1 for d in deltas if d)
        print(f"{label}: {len(seq)} snapshots -> {len(kept)} kept, "
              f"{moved} interval(s) with movement, "
              f"{len(flat)} per-jornada rows")

    print(f"wrote {LIVE}/ — report.py does not read this folder, on purpose.")


def _selftest() -> None:
    a, b, c, d = ([{"player_name": name, "player_name_full": name, "team": "X",
                    "points": pts, "games": pj, "avg": ""}
                   for name, pts, pj in snap]
                  for snap in ([("Ane Aldea", "0", "0"), ("Bo Bidal", "0", "0")],
                               [("Ane Aldea", "0", "0"), ("Bo Bidal", "0", "0")],
                               [("Ane Aldea", "8", "1"), ("Bo Bidal", "0", "0")],
                               [("Ane Aldea", "8", "1"), ("Bo Bidal", "3", "1"),
                                ("Cai Coro", "5", "1")]))

    kept = keep_changed([("t0", a), ("t1", b), ("t2", c), ("t3", d)])
    assert [s for s, _ in kept] == ["t0", "t2", "t3"], kept

    d1 = diff(a, c, "t0", "t2", "s")
    assert len(d1) == 1 and d1[0]["player_name_full"] == "Ane Aldea"
    assert d1[0]["points_delta"] == "8" and d1[0]["games_delta"] == "1"

    d2 = diff(c, d, "t2", "t3", "s")
    got = {r["player_name_full"]: r["points_delta"] for r in d2}
    assert got == {"Bo Bidal": "3", "Cai Coro": "5"}, got

    assert next(r for r in d2 if r["player_name_full"] == "Cai Coro"
                )["games_delta"] == "1"


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

    d3 = diff(a, c, "t0", "t2", "s", jornada_timeline=tl)
    assert d3[0]["jornada"] == "1", d3
    assert diff(a, c, "t0", "t2", "s")[0]["jornada"] == ""

    print("points.py selftest OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        main()
