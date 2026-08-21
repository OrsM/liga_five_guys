"""
scout — the tidy facts about your squad, no model in between.

Nothing here shrinks a rate, blends a prior, or simulates a season. Every
column is a real number read straight off a tidy CSV this repo already
parses: last season's real points/match, this season's real points/match so
far, market price, probable-XI%, fitness. `ffcore.score.Scorer` exists to
turn these into ONE blended estimate for ranking transfers; this exists for
the opposite job — looking at the raw ingredients yourself before trusting
anyone's blend of them, mine included.

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


def _this_season() -> dict[str, tuple[float, float]]:
    """{ff_id: (points, games)}, summed straight off the real per-jornada
    log — no recency weighting, no decay grid, just added up."""
    path = SEASON / "live" / "perjornada_2026-27.csv"
    if not path.exists():
        return {}
    out: dict[str, list[float]] = {}
    for r in csv.DictReader(open(path, encoding="utf-8")):
        pid = (r.get("ff_id") or "").strip()
        if not pid:
            continue
        acc = out.setdefault(pid, [0.0, 0.0])
        acc[0] += float(r.get("points_delta") or 0)
        acc[1] += float(r.get("games_delta") or 0)
    return {k: (v[0], v[1]) for k, v in out.items()}


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
        cp, cg = cur.get(key, (None, None))
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
            "this_season_avg": (cp / cg) if cg else None,
            "this_season_pj": cg,
        })
    rows.sort(key=lambda r: (POS_ORDER.get(r["pos"], 9),
                             -(r["last_season_avg"] or 0)))
    return rows


def render(rows: list[dict]) -> list[str]:
    out = ["| Pos | Player | Team | Price | 1d | XI% | Fit | LastSzn avg/pj "
           "| ThisSzn avg/pj |",
          "|---|---|---|--:|--:|--:|---|--:|--:|"]
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
                ("%.2f/%d" % (r["this_season_avg"], r["this_season_pj"]))
                if r["this_season_avg"] is not None else "-",
            ))
    return out


def main() -> None:
    rows = table()
    for line in render(rows):
        print(line)


def _selftest() -> None:
    rows = [
        {"name": "A", "pos": "def", "team": "X", "price_eur": 5_000_000,
         "delta_1d_eur": 12_000, "xi_pct": 80, "status": "ok",
         "last_season_avg": 3.5, "last_season_pj": 30,
         "this_season_avg": 4.0, "this_season_pj": 1},
        {"name": "B", "pos": "del", "team": "Y", "price_eur": None,
         "delta_1d_eur": None, "xi_pct": None, "status": "doubt",
         "last_season_avg": None, "last_season_pj": None,
         "this_season_avg": None, "this_season_pj": None},
    ]
    lines = render(rows)
    assert any("A" in l and "3.50/30" in l for l in lines), lines
    assert any("B" in l and "?" in l and "-" in l for l in lines), lines
    print("scout self-test OK (2 cases)")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        raise SystemExit(0)
    main()
