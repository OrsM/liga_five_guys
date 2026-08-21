"""
scout — the tidy facts about your squad, no model in between.

Nothing here shrinks a rate, blends a prior, or simulates a season. Every
column is a real number read straight off a tidy CSV this repo already
parses: last season's real points/match, this season jornada by jornada
(not lumped into one average), market price, probable-XI%, fitness.
`ffcore.score.Scorer` exists to turn these into ONE blended estimate for
ranking transfers; this exists for the opposite job — looking at the raw
ingredients yourself before trusting anyone's blend of them, mine included.

    python src/scout.py            your current squad, sorted by position
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

sys.path.insert(0, "src")

from ffcore.tidy import load_players  # noqa: E402

SEASON = Path("data/season")
POS_ORDER = {"por": 0, "def": 1, "med": 2, "del": 3}
SLOT = {"portero": "por", "defensa": "def", "mediocampista": "med",
        "delantero": "del"}


def _last_season() -> dict[str, tuple[float, float]]:
    """{ff_id: (points, games)}, last season's REAL total. No shrink.

    Keyed on ff_id, the SAME id the market (and squad ownership) key on —
    row_key() in ffcore.tidy prefers it over a name for exactly this
    reason: a display name a season is free to move on from is not a
    stable join key, an id is.
    """
    files = sorted(SEASON.glob("points_*.csv"))
    if not files:
        return {}
    out = {}
    for r in csv.DictReader(open(files[-1], encoding="utf-8")):
        pid = (r.get("ff_id") or "").strip()
        if pid:
            out[pid] = (float(r.get("points") or 0), float(r.get("games") or 0))
    return out


def _this_season() -> dict[str, dict[int, float]]:
    """{ff_id: {jornada: points}}, read straight off the real per-jornada
    log — one row per jornada, not lumped into a season average. Form
    this early is a trend across a handful of real matches, not a mean;
    an average of one jornada IS that jornada, so the shape only starts
    to matter once there are several to look at side by side."""
    path = SEASON / "live" / "perjornada_2026-27.csv"
    if not path.exists():
        return {}
    out: dict[str, dict[int, float]] = {}
    for r in csv.DictReader(open(path, encoding="utf-8")):
        pid = (r.get("ff_id") or "").strip()
        j = r.get("jornada")
        if not pid or not j:
            continue
        out.setdefault(pid, {})[int(j)] = float(r.get("points_delta") or 0)
    return out


def table(me: str | None = None) -> list[dict]:
    """One real row per player you own. `me` picks the manager; the
    league's own config default is used when it is omitted."""
    import decide

    u = decide.load()
    me = me or u.me
    players = load_players()
    last = _last_season()
    cur = _this_season()

    rows = []
    for key in u.state.squads.get(me, {}):
        p = players.get(key, {})
        lp, lg = last.get(key, (None, None))
        by_jornada = cur.get(key, {})
        rows.append({
            "name": p.get("name", key),
            "pos": SLOT.get(p.get("pos", ""), "?"),
            "team": p.get("team", ""),
            "price_eur": p.get("value"),
            "delta_1d_eur": p.get("delta_1d"),
            "xi_pct": p.get("start"),
            "status": p.get("status") or "ok",
            "last_season_avg": (lp / lg) if lg else None,
            "last_season_pj": lg,
            "by_jornada": by_jornada,
        })
    rows.sort(key=lambda r: (POS_ORDER.get(r["pos"], 9),
                             -(r["last_season_avg"] or 0)))
    return rows


def _form(by_jornada: dict[int, float], n: int = 5) -> str:
    """Last `n` real jornadas, most recent first — a trend, not an
    average. '-' for a jornada with no row at all (did not play), so a
    blank week is never confused with a zero he actually scored."""
    if not by_jornada:
        return "-"
    latest = max(by_jornada)
    cells = []
    for j in range(latest, max(latest - n, 0), -1):
        cells.append("%g" % by_jornada[j] if j in by_jornada else "-")
    return " ".join(cells)


def render(rows: list[dict]) -> list[str]:
    out = ["| Pos | Player | Team | Price | 1d | XI% | Fit | LastSzn avg/pj "
           "| Form (newest first) |",
          "|---|---|---|--:|--:|--:|---|--:|---|"]
    for r in rows:
        out.append(
            "| %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
                r["pos"].upper(),
                r["name"],
                r["team"],
                "%.2fM" % (r["price_eur"] / 1e6) if r["price_eur"] else "?",
                "%+.0fk" % (r["delta_1d_eur"] / 1e3)
                if r["delta_1d_eur"] is not None else "?",
                "%.0f%%" % r["xi_pct"] if r["xi_pct"] is not None else "?",
                r["status"],
                ("%.2f/%d" % (r["last_season_avg"], r["last_season_pj"]))
                if r["last_season_avg"] is not None else "-",
                _form(r["by_jornada"]),
            ))
    return out


def main() -> None:
    rows = table()
    for line in render(rows):
        print(line)


def _selftest() -> None:
    # -- _form(): newest first, a gap is "-" not a missing row -------------
    assert _form({}) == "-"
    assert _form({1: 6.0}) == "6"
    assert _form({1: 6.0, 3: 2.0}, n=3) == "2 - 6"
    assert _form({1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6}) == "6 5 4 3 2"

    rows = [
        {"name": "A", "pos": "def", "team": "X", "price_eur": 5_000_000,
         "delta_1d_eur": 12_000, "xi_pct": 80, "status": "ok",
         "last_season_avg": 3.5, "last_season_pj": 30,
         "by_jornada": {1: 4.0}},
        {"name": "B", "pos": "del", "team": "Y", "price_eur": None,
         "delta_1d_eur": None, "xi_pct": None, "status": "doubt",
         "last_season_avg": None, "last_season_pj": None,
         "by_jornada": {}},
    ]
    lines = render(rows)
    assert any("A" in l and "3.50/30" in l and "| 4 |" in l for l in lines), lines
    assert any("B" in l and "?" in l for l in lines), lines
    print("scout self-test OK (6 cases)")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        raise SystemExit(0)
    main()
