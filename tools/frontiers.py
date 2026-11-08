"""Frontier check (docs/notes/lfg.md, "Architecture"): read-only.
Builds a scratch DB from the schema, finds what each view reads (names after FROM/JOIN, CTEs
skipped) and prints every read past the frontier: a read of a later module, or of a scraped
table from outside ingest. Any module may read the facts and t (look) and an earlier module's contract. Exit 1 if there is one.
Each module's EXPORTS is its contract: reading another module's name that is not exported fails, in views and in the
production code of src/*.py (selftests excluded). No exemptions: grading is the last module and reads contracts only.
A Python file that implements several modules (assemble, sim, db, facts, fixtures) is no module's: whatever it reads
of a module that is not exported is a crossing. Every crossing is printed, grouped by the reading module, with
file:line. `--table` prints the exports as markdown."""
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ORDER = ["ingest", "forecast", "decision", "board"]
ORDER_ALL = ORDER + ["grading"]  # grading, the measure, is last: it reads contracts like the rest
PREFIX = {"api_": "ingest", "current_": "ingest", "history_": "ingest", "ff_identity": "ingest",
          "name_": "ingest", "forecast_": "forecast", "price_": "forecast", "momentum": "forecast",
          "dec_": "decision", "seen": "decision", "my_": "decision", "league_": "decision",
          "board_": "board", "lineup_": "board", "grade_": "grading", "replay": "grading", "sim_": "grading"}
NAMES = {
    "ingest": "market lineups matches points results_history starters crosswalk app_player slug_player id_clashes "
              "names registry app_links owners users ledger cash league_config kickoffs blind_results jornada_calendar "
              "look auction_results player_club player_value player_status club_fixture club_rating clubs_played round_locks rounds_ahead match_jornada club_games jornadas_done scored squad_minutes appearances "
              "player_roster listings offers squads standings app_lineup price_history offer_ratios "
              "player_jornadas season_record season_points next_lock listing_ends listing_close price_paid",
    "forecast": "cell cell_pool standin_cell score_spread forecast_used "
                "late_arrivals ppm_prior last_season fitted horizons market_daily",
    "decision": "bid_odds recent_offers sale_odds waiting nights_left player_facts my_squad my_cash",
    "board": "",
    "grading": "measure_log measure_latest"}
# Each module's contract, as the note lists it: what the next module reads and nothing else. Ingest's are its facts.
EXPORTS = {
    "ingest": """player_club player_value player_status player_roster appearances scored season_points season_record
        club_fixture club_rating jornada_calendar kickoffs next_lock round_locks look
        listings listing_close offers offer_ratios price_history price_paid auction_results
        squads owners cash standings league_config""",
    "forecast": "cell market_daily forecast_e price_carry price_trend price_trend_daily price_forecast price_grade",  # cell(player, jornada, pts, p), a stand-in for one not yet known; forecast_e: the cells of a stored moment; the price trend and carry the decision's odds read; price_forecast: each player's expected value night by night to the next lock; price_grade: its error against what happened
    "decision": "player_facts",  # the facts of each player the board shows; the decision's inputs are dec_* tables, read by decision.py only
    "board": "",
    "grading": "",  # a measure: nothing downstream reads it
}
EXPORT = {m: set(s.split()) for m, s in EXPORTS.items()}
# Python files that are wholly one module's: their reads are judged as that module's. A file not here (assemble,
# sim, db, facts, fixtures, ...) implements several modules and is nobody's: it may read only exports.
OWNS = {"ingest": "ingest", "crosswalk": "ingest", "league": "ingest", "jornadas": "ingest", "tidy": "ingest",
        "forecast": "forecast", "forecast_store": "forecast", "seen": "decision", "decision": "decision", "board": "board", "grading": "grading"}
MODULE = {n: m for m, s in NAMES.items() for n in s.split()}
SKIP = {"k", "model_constants", "params", "runs", "run_measures", "facts_meta", "parser_health"}  # no module: config, bookkeeping


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
    """A Python file's production code: its top-level _selftest functions blanked (line numbers kept)."""
    return re.sub(r"(?ms)^def _selftest\w*\(.*?(?=^\S)", lambda m: re.sub(r"[^\n]", "", m.group()), text + "\nX")[:-2]


def lines(text: str, t: str) -> list[int]:
    """The 1-based lines of text that read t (after FROM or JOIN), comments and quoted text ignored."""
    return [i for i, line in enumerate(text.split("\n"), 1)
            if re.search(r"\b(?:from|join)\s+%s\b" % t, re.sub(r"--.*|#.*|'[^']*'", " ", line), re.I)]


def view_spans() -> dict[str, tuple[Path, int, str]]:
    """Where each view is written: its .sql file, first line and text (up to the next CREATE VIEW)."""
    spans = {}
    for f in sorted((ROOT / "src/ffcore").glob("*.sql")):
        text = f.read_text()
        starts = [(m.start(), m.group(1))
                  for m in re.finditer(r"(?im)^CREATE\s+VIEW\s+(?:IF NOT EXISTS\s+)?(\w+)", text)]
        for k, (at, name) in enumerate(starts):
            end = starts[k + 1][0] if k + 1 < len(starts) else len(text)
            spans[name] = (f, text.count("\n", 0, at) + 1, text[at:end])
    return spans


def crossing(a: str | None, t: str) -> str | None:
    """Why a reader in module a (None: a file of no single module) may not read t, or None if it may."""
    b = module(t)
    if b is None or b == a:
        return None
    if a is not None and ORDER_ALL.index(b) > ORDER_ALL.index(a):
        return f"{b}, a later module"
    if a != "ingest" and raw(t):
        return f"{b}, a scraped table"
    return None if t in EXPORT[b] else f"{b}, not exported"


def table() -> str:
    rows = ["| Module | Exports (its contract) |", "|---|---|"]
    rows += [f"| {m} | {', '.join(sorted(EXPORT[m])) or '(none yet)'} |" for m in ORDER_ALL]
    return "\n".join(rows)


def _selftest() -> None:
    """The rule is enforced: a later module, a scraped table and an unexported name are crossings; an export is not."""
    assert crossing("forecast", "dec_player")  # a later module
    assert crossing("decision", "market")  # a scraped table
    assert crossing("ingest", "market") is None  # ingest reads its own
    assert crossing(None, "score_spread")  # a forecast internal, not exported
    assert crossing(None, "price_trend") is None  # exported: the trend the decision's odds read
    assert crossing(None, "cell") is None  # exported
    assert lines("select 1\nfrom a join cell c -- from cell", "cell") == [2]
    assert prod("x\ndef _selftest():\n    from cell\n\ny from cell\n").count("\n") == 5
    assert main() == 0, "the tree has crossings (listed above)"  # the gate: red until they are fixed
    print("frontiers selftest ok")


def main() -> int:
    if "--table" in sys.argv:
        print(table())
        return 0
    objs = scratch()
    names = {n for _, n, _ in objs}
    unassigned = sorted(n for n in names if module(n) is None and n not in SKIP)
    found: dict[str, set[str]] = {}  # the reading module (or a file of no module) -> its crossings
    spans = view_spans()
    for kind, n, sql in objs:
        a = module(n)
        if kind != "view" or a is None:
            continue
        f, first, text = spans.get(n, (None, 0, ""))
        for t in sorted(reads(sql, n) & names):
            why = crossing(a, t)
            for ln in [first + i - 1 for i in lines(text, t)] or [first] if why else []:
                where = f"{f.relative_to(ROOT)}:{ln}" if f else "(view source not found)"
                found.setdefault(a, set()).add(f"{where}  view {n} reads {t} ({why})")
    for f in sorted((ROOT / "src").glob("*.py")) + sorted((ROOT / "src/ffcore").glob("*.py")):
        a = OWNS.get(f.stem)
        text = prod(f.read_text())
        for t in sorted(names):
            why = crossing(a, t)
            for ln in lines(text, t) if why else []:
                found.setdefault(a or "no module: " + f.name, set()).add(
                    f"{f.relative_to(ROOT)}:{ln}  reads {t} ({why})")
    stale = sorted(f"{m}.{t}" for m, e in EXPORT.items() for t in e if t not in names)
    total = 0
    for who in sorted(found, key=lambda w: (ORDER_ALL.index(w) if w in ORDER_ALL else 9, w)):
        total += len(found[who])
        print(f"== {who}: {len(found[who])} crossings")
        print("\n".join("  " + r for r in sorted(found[who])))
    print(f"crossings: {total}")
    print("unassigned:", " ".join(unassigned) or "none")
    print("exported but not in the schema:", " ".join(stale) or "none")
    return 1 if found or stale else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        sys.exit(main())
