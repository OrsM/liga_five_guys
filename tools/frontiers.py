"""Frontier check (docs/notes/simplify.md, "Modules and contracts"): read-only.
Builds a scratch DB from the schema, finds what each view reads (names after FROM/JOIN, CTEs
skipped) and prints every read past the frontier: a read of a later module, or of a scraped
table from outside ingest. Any module may read the facts and t (look) and an earlier module's contract. Exit 1 if there is one.
Each module's EXPORTS is its contract: reading another module's name that is not exported fails, in views and in the
production code of src/*.py (selftests excluded). grading may read anything. `--table` prints the exports as markdown."""
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
    "forecast": "cell cell_pool standin_cell score_spread run_forecast forecast_used "
                "late_arrivals ppm_prior last_season fitted horizons market_daily",
    "decision": "bid_odds recent_offers sale_odds waiting nights_left player_facts "
                "next_cell my_squad my_cash my_xi look_players formation slot_bounds "
                "decision_option decision_plan",
    "settlement": "",
    "board": "lock_due lineup_alert league_standings",
    "grading": "replayed lead_buckets measure_log measure_latest"}
# Each module's contract: the views and tables other modules may read; a comment says why where it is not a plain fact.
EXPORTS = {
    "ingest": """player_club player_value player_status player_roster appearances scored season_points season_record
        club_fixture club_rating jornada_calendar kickoffs next_lock look
        listings listing_ends offers offer_ratios price_history price_paid auction_results
        squads owners cash standings league_config
        app_lineup app_player name_player""",  # last three: the lineup facts the board's lineup_gap compares
    "forecast": "cell standin_cell score_spread price_carry price_trend price_trend_daily",  # cell is the contract; the rest feed the decision's odds and standings
    "decision": """bid_odds recent_offers nights_left league_season seen_cash my_cash
        decision_option decision_plan""",  # the board reads the plan, its options and the odds; league_standings reads cash and season
    "settlement": "",
    "board": "board_json",
    "grading": "sim_facts sim_players sim_runs sim_standings sim_waiting",  # the replay's stand-ins the board reads
}
EXPORT = {m: set(s.split()) for m, s in EXPORTS.items()}
# Python files that implement more than their own module: they may read those modules' internals.
OWNS = {"assemble": {"forecast", "decision", "grading"}, "sim": {"decision", "board", "grading"},
        "db": {"forecast", "grading"}, "facts": {"ingest", "forecast"}, "remind": {"board"},
        "decision": {"decision"}, "fixtures": set(ORDER) | {"grading"}, "grading": set(ORDER) | {"grading"}}
OWNS |= {f: {"ingest"} for f in ("ingest", "crosswalk", "league", "jornadas", "tidy")}
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
def prod(text: str) -> str:
    """A Python file's production code: its top-level _selftest functions removed."""
    return re.sub(r"(?ms)^def _selftest\w*\(.*?(?=^\S)", "", text + "\nX")


def table() -> str:
    rows = ["| Module | Exports (its contract) |", "|---|---|"]
    rows += [f"| {m} | {', '.join(sorted(EXPORT[m])) or '(none yet)'} |" for m in ORDER + ["grading"]]
    return "\n".join(rows)


def _selftest() -> None:
    """The tree passes; and dropping an export makes the check fail (the rule is enforced, not just printed)."""
    assert main() == 0
    EXPORT["forecast"].discard("cell")
    assert main() == 1
    EXPORT["forecast"].add("cell")
    print("frontiers selftest ok")


def main() -> int:
    if "--table" in sys.argv:
        print(table())
        return 0
    objs = scratch()
    names = {n for _, n, _ in objs}
    unassigned = sorted(n for n in names if module(n) is None and n not in SKIP)
    found = []
    for kind, n, sql in objs:
        a = module(n)
        if kind != "view" or a in (None, "grading"):
            continue
        for t in sorted(reads(sql, n) & names):
            b = module(t)
            if not b or b == a:
                continue
            if b != "grading" and (ORDER.index(b) > ORDER.index(a) or (a != "ingest" and raw(t))):
                found.append(f"{n} ({a}) reads {t} ({b}{' raw' if raw(t) else ''})")
            elif t not in EXPORT[b]:
                found.append(f"{n} ({a}) reads {t} ({b}), not exported")
    for f in sorted((ROOT / "src").glob("*.py")) + sorted((ROOT / "src/ffcore").glob("*.py")):
        if f.stem == "grading":
            continue
        for t in sorted({t for t in re.findall(r"\b(?:from|join)\s+(\w+)", prod(f.read_text()), re.I) if t in names}):
            b = module(t)
            if b and b not in OWNS.get(f.stem, set()) and t not in EXPORT[b]:
                found.append(f"{f.relative_to(ROOT)} reads {t} ({b}), not exported")
    stale = sorted(f"{m}.{t}" for m, e in EXPORT.items() for t in e if t not in names)
    print("\n".join(found))
    print("unassigned:", " ".join(unassigned) or "none")
    print("exported but not in the schema:", " ".join(stale) or "none")
    return 1 if found or stale else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        sys.exit(main())
