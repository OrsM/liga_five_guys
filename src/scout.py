"""
scout — the tidy facts about your squad, no model in between.

Every column is a real number read straight off a tidy CSV: last season's
points/match, this season jornada by jornada, market price, probable-XI%,
fitness. `ffcore.score.Scorer` blends these into one estimate for ranking
transfers; this shows the raw ingredients instead.

    python src/scout.py            your current squad, sorted by position
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

sys.path.insert(0, "src")

from ffcore.score import OUT_STATUSES  # noqa: E402
from ffcore.tidy import load_players  # noqa: E402

SEASON = Path("data/season")
POS_ORDER = {"por": 0, "def": 1, "med": 2, "del": 3}
SLOT = {"portero": "por", "defensa": "def", "mediocampista": "med",
        "delantero": "del"}


def _last_season() -> dict[str, tuple[float, float]]:
    """{ff_id: (points, games)}, last season's real total, unshrunk.
    Keyed on ff_id — the same stable id the market/ownership join uses.
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
    """{ff_id: {jornada: points}}, one row per jornada — a trend, not a
    season average.

    Found by globbing, not a hardcoded season label — matching
    methodology.load_actuals()'s own reasoning: a hardcoded "2026-27" here
    once meant this would silently start reading nothing the moment the
    season rolled over and the file became perjornada_2027-28.csv.
    """
    files = sorted((SEASON / "live").glob("perjornada_*.csv"))
    if not files:
        return {}
    out: dict[str, dict[int, float]] = {}
    for r in csv.DictReader(open(files[-1], encoding="utf-8")):
        pid = (r.get("ff_id") or "").strip()
        j = r.get("jornada")
        if not pid or not j:
            continue
        out.setdefault(pid, {})[int(j)] = float(r.get("points_delta") or 0)
    return out


def _play(status: str, xi_pct: float | None, min_start: float) -> str:
    """Play status from two facts, not a model — OUT_STATUSES and
    min_start match the rest of this repo's own "probable starter" read.
    """
    if status in OUT_STATUSES:
        return "OUT (%s)" % status
    if xi_pct is None:
        return "?"
    return "likely" if xi_pct >= min_start else "doubt"


def table(me: str | None = None) -> list[dict]:
    """One real row per player you own. `me` picks the manager; the
    league's own config default is used when it is omitted."""
    import decide
    from ffcore.model import session

    u = decide.load()
    me = me or u.me
    players = load_players()
    last = _last_season()
    cur = _this_season()
    # decide.load() already warmed session() — reuse it for one config field.
    min_start = session().lg.cfg.min_start

    rows = []
    for key in u.state.squads.get(me, {}):
        p = players.get(key, {})
        lp, lg = last.get(key, (None, None))
        by_jornada = cur.get(key, {})
        status = p.get("status") or "ok"
        xi_pct = p.get("start")
        rows.append({
            "name": p.get("name", key),
            "pos": SLOT.get(p.get("pos", ""), "?"),
            "team": p.get("team", ""),
            "price_eur": p.get("value"),
            "delta_1d_eur": p.get("delta_1d"),
            "xi_pct": xi_pct,
            "status": status,
            "play": _play(status, xi_pct, min_start),
            "last_season_avg": (lp / lg) if lg else None,
            "last_season_pj": lg,
            "by_jornada": by_jornada,
        })
    rows.sort(key=lambda r: (POS_ORDER.get(r["pos"], 9),
                             -(r["last_season_avg"] or 0)))
    return rows


def _form(by_jornada: dict[int, float], n: int = 5) -> str:
    """Last `n` jornadas, most recent first. '-' for no row at all (did
    not play), never confused with a real zero.
    """
    if not by_jornada:
        return "-"
    latest = max(by_jornada)
    cells = []
    for j in range(latest, max(latest - n, 0), -1):
        cells.append("%g" % by_jornada[j] if j in by_jornada else "-")
    return " ".join(cells)


def render(rows: list[dict]) -> list[str]:
    out = ["| Pos | Player | Team | Price | 1d | XI% | Play? | Fit "
           "| LastSzn avg/pj | Form (newest first) |",
          "|---|---|---|--:|--:|--:|---|---|--:|---|"]
    for r in rows:
        out.append(
            "| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
                r["pos"].upper(),
                r["name"],
                r["team"],
                "%.2fM" % (r["price_eur"] / 1e6) if r["price_eur"] else "?",
                "%+.0fk" % (r["delta_1d_eur"] / 1e3)
                if r["delta_1d_eur"] is not None else "?",
                "%.0f%%" % r["xi_pct"] if r["xi_pct"] is not None else "?",
                r["play"],
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

    # -- _play(): OUT_STATUSES beats any XI%, then a plain threshold read --
    assert _play("injured", 90.0, 60.0) == "OUT (injured)"
    assert _play("ok", None, 60.0) == "?"
    assert _play("ok", 80.0, 60.0) == "likely"
    assert _play("ok", 40.0, 60.0) == "doubt"
    assert _play("ok", 60.0, 60.0) == "likely"          # the threshold itself

    rows = [
        {"name": "A", "pos": "def", "team": "X", "price_eur": 5_000_000,
         "delta_1d_eur": 12_000, "xi_pct": 80, "status": "ok",
         "play": "likely", "last_season_avg": 3.5, "last_season_pj": 30,
         "by_jornada": {1: 4.0}},
        {"name": "B", "pos": "del", "team": "Y", "price_eur": None,
         "delta_1d_eur": None, "xi_pct": None, "status": "injured",
         "play": "OUT (injured)", "last_season_avg": None,
         "last_season_pj": None, "by_jornada": {}},
    ]
    lines = render(rows)
    assert any("A" in l and "3.50/30" in l and "| 4 |" in l
              and "likely" in l for l in lines), lines
    assert any("B" in l and "OUT (injured)" in l for l in lines), lines
    print("scout self-test OK (11 cases)")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        raise SystemExit(0)
    main()
