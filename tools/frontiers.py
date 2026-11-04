"""Frontier check (docs/notes/simplify.md, "Modules and contracts"): read-only.
Builds a scratch DB from the schema, finds what each view reads (names after FROM/JOIN, CTEs
skipped) and prints every read past the frontier: a read of a later module, or of a scraped
table from outside ingest. Any module may read the facts and t (look) and an earlier module's contract. Exit 1 if there is one.
Python files' FROM/JOIN reads are listed for information only."""
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ORDER = ["ingest", "forecast", "decision", "settlement", "board"]
PREFIX = {"api_": "ingest", "current_": "ingest", "history_": "ingest", "ff_identity": "ingest",
          "name_": "ingest", "forecast_": "forecast", "price_": "forecast", "momentum": "forecast",
          "dec_": "decision", "seen": "decision", "my_": "decision", "league_": "decision",
          "board_": "board", "lineup_": "board", "grade_": "grading", "replay": "grading", "sim_": "grading"}
NAMES = {
    "ingest": "market lineups matches points results_history starters crosswalk app_player slug_player id_clashes "
              "names registry app_links owners users ledger cash league_config kickoffs blind_results jornada_calendar "
              "look auction_results player_club player_value player_status club_fixture club_rating clubs_played round_locks rounds_ahead match_jornada club_games jornadas_done scored squad_minutes appearances "
              "player_roster listings offers squads standings app_lineup price_history offer_ratios "
              "player_jornadas season_record season_points next_lock listing_ends price_paid",
    "forecast": "cell standin_cell score_spread run_forecast forecast_used "
                "late_arrivals ppm_prior last_season fitted horizons market_daily",
    "decision": "bid_odds recent_offers sale_odds waiting nights_left player_facts "
                "next_cell my_squad my_cash my_xi look_players formation slot_bounds "
                "decision_option decision_plan",
    "settlement": "",
    "board": "lock_due lineup_alert league_standings",
    "grading": "replayed lead_buckets"}
MODULE = {n: m for m, s in NAMES.items() for n in s.split()}
SKIP = {"k", "model_constants", "variants", "params", "runs", "run_measures", "facts_meta", "parser_health",
        "persistence_by_run", "board_history", "board_history_moves"}  # no module: config, bookkeeping


def module(name: str) -> str | None:
    if name in MODULE:
        return MODULE[name]
    return next((m for p, m in PREFIX.items() if name.startswith(p)), None)


RAW = {"market", "lineups", "matches", "points", "results_history", "starters"}


def raw(name: str) -> bool:
    """A scraped table: only ingest's views read it; the rest read ingest's facts."""
    return name in RAW or name.startswith(("api_", "current_", "history_"))


def reads(sql: str, own: str) -> set[str]:
    sql = re.sub(r"--[^\n]*|'[^']*'", " ", sql)
    ctes = set(re.findall(r"(\w+)\s+AS\s*\(", sql, re.I))
    return {t for t in re.findall(r"\b(?:from|join)\s+(\w+)", sql, re.I) if t not in ctes and t != own}


def scratch() -> list[tuple[str, str, str]]:
    tmp = tempfile.mkdtemp()
    try:
        subprocess.run([sys.executable, "-c", "from ffcore.db import connect; connect()"], check=True,
                       env={"PATH": "/usr/bin", "PYTHONPATH": str(ROOT / "src"), "FF_ROOT": tmp})
        db = sqlite3.connect(next(Path(tmp).glob("*.db")))
        return db.execute("select type, name, sql from sqlite_master where type in ('table','view')").fetchall()
    finally:
        shutil.rmtree(tmp)


def main() -> int:
    objs = scratch()
    names = {n for _, n, _ in objs}
    unassigned = sorted(n for n in names if module(n) is None and n not in SKIP)
    found = []
    for kind, n, sql in objs:
        if kind != "view" or module(n) in (None, "grading"):
            continue
        for t in sorted(reads(sql, n) & names):
            a, b = module(n), module(t)
            if a and b and b != "grading" and (ORDER.index(b) > ORDER.index(a) or (a != "ingest" and raw(t))):
                found.append((n, a, t, b + (" raw" if raw(t) else "")))
    for n, a, t, b in found:
        print(f"{n} ({a}) reads {t} ({b})")
    print("unassigned:", " ".join(unassigned) or "none")
    print("\nPython files reading across more than two modules (informational):")
    for f in sorted((ROOT / "src").glob("*.py")) + sorted((ROOT / "src/ffcore").glob("*.py")):
        got = {t for t in re.findall(r"\b(?:from|join)\s+(\w+)", f.read_text(), re.I) if t in names}
        mods = {module(t) for t in got} - {None, "grading"}
        if len(mods) > 2:
            print(f"{f.relative_to(ROOT)}: {', '.join(m for m in ORDER if m in mods)}")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
