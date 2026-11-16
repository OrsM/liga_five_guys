"""The oracle of a migration (#79): every derived output of a git ref, per cut, in one JSON file, and the comparison
of two such files. The reference is a tag of the code to be matched (oracle-sql: the SQL views of 2026-11-15).

    python tools/oracle.py dump --ref oracle-sql --cuts 12+4 --out A.json
    python tools/oracle.py dump --ref HEAD --cuts @A.json --out B.json     # the cuts A was dumped at
    python tools/oracle.py diff A.json B.json [--first 10]                 # exit 1 when they differ

dump copies the live database read-only into a scratch under ~/.cache, checks REF out into a temporary worktree
there and runs ref's own src/ on that copy (never data/, never the account). --cuts is N (N cuts spread over the
seen cuts of the season plus the last 4 live runs), "N+K" (K live), a comma list of stamps (2026-11-15T0907Z), or
@FILE (the cuts of an earlier dump). Per cut it records, through the entry points of ref (board.replayed,
decision.read_inputs, board.context / board.board, forecast_store, the tables seen*, forecast_*): the decision's
inputs, the seen tables, the forecast cells and flags, momentum / price trend / carry / price forecast, club
ratings, offer_ratios, the ledger, cash and squad views, the settled eleven per horizon, the opportunities, the
params and the board document (decisions.json). Left out: row ids and moment numbers, seen.code, run bookkeeping,
timings, grading and calibration tables, kept stores (facts, price_step, momentum_day), the account.
A fact that ref cannot produce is recorded as {"absent": reason}, which diff reports.
Floats are compared to 1e-9 relative; ids, strings and ints exactly."""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from contextlib import closing
from pathlib import Path
from typing import Any

HOME = Path.home()
CACHE = HOME / ".cache"
UV = str(HOME / ".local/bin/uv")
REPO = Path(__file__).resolve().parent.parent
MAIN_TREE = Path(subprocess.run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=REPO,
                                capture_output=True, text=True, check=True).stdout.strip()).parent
LIVE_DB = MAIN_TREE / "data" / "lfg.db"
TOLERANCE = 1e-9
LIVE_CUTS = 4
FORMAT = 1

VIEWS = ("cash", "ledger", "my_squad", "my_cash", "price_paid", "player_status", "next_lock", "offer_ratios",
         "momentum", "price_carry", "price_trend", "price_forecast", "club_rating", "late_arrivals")
SEEN = ("seen", "seen_cash", "seen_auction", "seen_curve")
DROPPED = {"code", "moment", "variant", "cut"}


def jsonable(v: Any) -> Any:
    """v as plain JSON data: named rows as dicts, sets and tuples as lists (sets sorted)."""
    if hasattr(v, "_asdict"):
        return {k: jsonable(x) for k, x in v._asdict().items()}
    if isinstance(v, dict):
        return {str(k): jsonable(x) for k, x in v.items()}
    if isinstance(v, (set, frozenset)):
        return sorted((jsonable(x) for x in v), key=json.dumps)
    if isinstance(v, (list, tuple)):
        return [jsonable(x) for x in v]
    return v


def ordered(rows: list[Any]) -> list[Any]:
    """Rows in an order that does not depend on their floats."""
    return sorted(rows, key=lambda r: json.dumps([x for x in (r.values() if isinstance(r, dict) else [r])
                                                  if not isinstance(x, float)], default=str))


def sql_rows(db: sqlite3.Connection, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    cur = db.execute(query, params)
    names = [d[0] for d in cur.description]
    return ordered([{n: v for n, v in zip(names, r) if n not in DROPPED} for r in cur])


def fact(make: Any) -> Any:
    """What make() returns as JSON data, or {"absent": why} when ref has no such fact."""
    try:
        return jsonable(make())
    except Exception as e:
        return {"absent": "%s: %s" % (type(e).__name__, str(e)[:200])}


def collect_cut(stamp: str) -> dict[str, Any]:
    """Every derived output of the code on PYTHONPATH at one cut, made by its own entry points."""
    import board
    import decision
    from ffcore import forecast_store as store
    from ffcore.db import connect, set_now
    from ffcore.parse import snapshot_stamp

    set_now(snapshot_stamp(stamp))
    out: dict[str, Any] = {}
    replay = board.replayed()
    if replay is None:
        return {"stale": True}
    inp = replay.inputs
    out["inputs"] = {f: ordered(jsonable(getattr(inp, f)) if f != "money" else [jsonable(inp.money)])
                     for f in inp._fields}
    with closing(connect()) as db:
        for t in SEEN:
            out[t] = fact(lambda t=t: sql_rows(db, "SELECT * FROM %s WHERE cut = ? AND variant = variant()" % t,
                                               (stamp,)))
        out["forecast_cell"] = fact(lambda: sql_rows(db, "SELECT * FROM forecast_cell WHERE moment = ?", (replay.moment,)))
        out["forecast_player"] = fact(lambda: sql_rows(db, "SELECT * FROM forecast_player WHERE moment = ?", (replay.moment,)))
        for v in VIEWS:
            out["view:" + v] = fact(lambda v=v: sql_rows(db, "SELECT * FROM %s" % v))
    out["price_momentum"] = fact(lambda: store.price_momentum())
    out["club_ratings"] = fact(lambda: store.club_ratings())
    out["offer_ratios"] = fact(lambda: sorted(store.market_offer_ratios()))
    out["params"] = fact(lambda: board.params(board.forecast_fits(), store.market_offer_ratios(), store.price_carry()))

    def horizons() -> Any:
        ctx = board.context(inp)
        return {str(n): {"value": ordered([{"player": k, "v": v} for k, v in h.value.items()]), "settled": h.settled}
                for n, h in ctx.horizons.items()}
    out["eleven"] = fact(horizons)
    got = decision.opportunity_rows(inp)
    out["opportunities"] = ordered(jsonable(got))
    out["decisions"] = fact(lambda: board.board(inp, got, decision.read_arrivals()))
    return out


def collect(cuts: list[str], out: Path) -> None:
    result = {}
    for c in cuts:
        t0 = time.monotonic()
        result[c] = collect_cut(c)
        print("  %s %.1f s" % (c, time.monotonic() - t0), file=sys.stderr)
    out.write_text(json.dumps(result, allow_nan=True, sort_keys=True), encoding="utf-8")


def choose_cuts(db_path: Path, spec: str) -> list[str]:
    """The cuts a spec names (module doc) in a copy of the database."""
    if spec.startswith("@"):
        return sorted(json.loads(Path(spec[1:]).read_text())["cuts"])
    if spec[:1].isdigit() and "," not in spec and not spec.endswith("Z"):
        spread, _, live = spec.partition("+")
        with closing(sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)) as db:
            seen = [c for (c,) in db.execute("SELECT DISTINCT cut FROM seen WHERE variant = 'live' AND length(cut) = 16 ORDER BY cut")]
            runs = [c for (c,) in db.execute("SELECT DISTINCT as_of FROM runs WHERE ok = 1 ORDER BY as_of")]
        last = runs[-int(live or LIVE_CUTS):]
        n = int(spread)
        pool = [c for c in seen if c not in last]
        picked = [pool[round(i * (len(pool) - 1) / max(n - 1, 1))] for i in range(n)] if pool else []
        return sorted(set(picked) | set(last))
    return sorted(spec.split(","))


def scratch_copy(dest: Path) -> Path:
    """A copy of the live database made through a read-only connection."""
    (dest / "data").mkdir(parents=True)
    with closing(sqlite3.connect("file:%s?mode=ro" % LIVE_DB, uri=True)) as src, \
            closing(sqlite3.connect(dest / "data" / "lfg.db")) as dst:
        src.backup(dst)
    return dest / "data" / "lfg.db"


def run(cmd: list[str], **kw: Any) -> None:
    subprocess.run(cmd, check=True, **kw)


def dump(ref: str, spec: str, out: Path) -> float:
    """REF's outputs at the cuts of spec into out, run on a scratch copy; the seconds it took."""
    t0 = time.monotonic()
    CACHE.mkdir(exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="lfg-oracle-", dir=CACHE))
    tree = tmp / "tree"
    try:
        sha = subprocess.run(["git", "rev-parse", ref + "^{commit}"], cwd=REPO, check=True,
                             capture_output=True, text=True).stdout.strip()
        run(["git", "worktree", "add", "--detach", str(tree), sha], cwd=REPO, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL)
        db = scratch_copy(tmp)
        cuts = choose_cuts(db, spec)
        partial = tmp / "dump.json"
        env = {**os.environ, "PYTHONPATH": str(tree / "src"), "FF_ROOT": str(tmp / "data"),
               "LFG_REPORTS": str(tmp / "reports"), "LFG_NO_PUBLISH": "1", "TMPDIR": str(tmp),
               "UV_PYTHON": os.environ.get("UV_PYTHON", "/usr/bin/python3.14")}
        env.pop("LFG_NOW", None)
        env.pop("VIRTUAL_ENV", None)
        run([UV, "run", "--frozen", "python", str(REPO / "tools" / "oracle.py"), "collect",
             "--out", str(partial), ",".join(cuts)], cwd=tree, env=env)
        seconds = time.monotonic() - t0
        out.write_text(json.dumps({"format": FORMAT, "ref": ref, "sha": sha, "seconds": round(seconds, 1),
                                   "cuts": json.loads(partial.read_text())}, sort_keys=True), encoding="utf-8")
        return seconds
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", str(tree)], cwd=REPO, capture_output=True)
        subprocess.run(["git", "worktree", "prune"], cwd=REPO, capture_output=True)
        shutil.rmtree(tmp, ignore_errors=True)


def same(a: Any, b: Any) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        if not isinstance(a, (int, float)) or not isinstance(b, (int, float)) or isinstance(a, bool) or isinstance(b, bool):
            return False
        if math.isnan(a) or math.isnan(b):
            return math.isnan(a) and math.isnan(b)
        return a == b or abs(a - b) <= TOLERANCE * max(abs(a), abs(b))
    return type(a) is type(b) and a == b


def differences(a: Any, b: Any, path: str = "") -> list[str]:
    """Where two JSON values differ, each as 'path: a != b'."""
    if isinstance(a, dict) and isinstance(b, dict):
        found = [("%s/%s: only in A" % (path, k)) for k in sorted(a.keys() - b.keys())]
        found += [("%s/%s: only in B" % (path, k)) for k in sorted(b.keys() - a.keys())]
        for k in sorted(a.keys() & b.keys()):
            found += differences(a[k], b[k], "%s/%s" % (path, k))
        return found
    if isinstance(a, list) and isinstance(b, list):
        found = [] if len(a) == len(b) else ["%s: %d rows != %d rows" % (path, len(a), len(b))]
        for i, (x, y) in enumerate(zip(a, b)):
            found += differences(x, y, "%s[%d]" % (path, i))
        return found
    return [] if same(a, b) else ["%s: %r != %r" % (path, a, b)]


def diff(a: dict[str, Any], b: dict[str, Any], first: int = 10) -> dict[str, list[str]]:
    """The differences of two dumps' cuts, by fact ('cut fact'), the first `first` of each shown in full."""
    found: dict[str, list[str]] = {}
    for c in sorted(a["cuts"].keys() | b["cuts"].keys()):
        if c not in a["cuts"] or c not in b["cuts"]:
            found[c + " (cut)"] = ["cut only in %s" % ("A" if c in a["cuts"] else "B")]
            continue
        x, y = a["cuts"][c], b["cuts"][c]
        for f in sorted(x.keys() | y.keys()):
            d = differences(x.get(f, {"absent": "missing"}), y.get(f, {"absent": "missing"}))
            if d:
                found["%s %s" % (c, f)] = d[:first] + (["... %d more" % (len(d) - first)] if len(d) > first else [])
    return found


def _selftest() -> None:
    assert same(1.0, 1.0 + 1e-12) and not same(1.0, 1.0 + 1e-6), "floats to 1e-9 relative"
    assert same(float("nan"), float("nan")) and not same(1, "1") and not same(True, 1), "ids and strings exact"
    assert same(5, 5) and not same(5, 6)
    cuts = {"c1": {"seen": [{"player": "p1", "value": 5e6}, {"player": "p2", "value": 6e6}],
                   "decisions": {"cash": 1.5, "buys": [{"name": "A"}]}}}
    a = {"cuts": cuts}
    assert diff(a, json.loads(json.dumps(a))) == {}, "equal dumps differ nowhere"
    b = json.loads(json.dumps(a))
    b["cuts"]["c1"]["seen"][1]["value"] = 6e6 * (1 + 1e-6)
    b["cuts"]["c1"]["decisions"]["buys"][0]["name"] = "B"
    got = diff(a, b)
    assert set(got) == {"c1 seen", "c1 decisions"} and "/[1]" not in got["c1 seen"][0] and "[1]/value" in got["c1 seen"][0]
    assert any("'A' != 'B'" in d for d in got["c1 decisions"]), "a perturbed value is reported"
    del b["cuts"]["c1"]["seen"]
    assert any("only in A" in d for d in diff(a, b)["c1 seen"]) or "absent" in str(diff(a, b)["c1 seen"]), "a missing fact is reported"
    many = {"cuts": {"c": {"f": list(range(50))}}}
    other = {"cuts": {"c": {"f": [x + 1 for x in range(50)]}}}
    assert len(diff(many, other, 5)["c f"]) == 6, "first N differences, then a count"
    assert diff({"cuts": {"c": {}}}, {"cuts": {}}) == {"c (cut)": ["cut only in A"]}
    rows = ordered([{"p": "b", "v": 1.0}, {"p": "a", "v": 2.0}])
    assert [r["p"] for r in rows] == ["a", "b"], "rows ordered by their non-float columns"
    assert jsonable({"x": frozenset({"b", "a"})}) == {"x": ["a", "b"]}
    with tempfile.TemporaryDirectory(dir=CACHE if CACHE.exists() else None) as d:
        db = Path(d) / "x.db"
        with closing(sqlite3.connect(db)) as c, c:
            c.execute("CREATE TABLE seen (cut TEXT, variant TEXT)")
            c.execute("CREATE TABLE runs (as_of TEXT, ok INTEGER)")
            for i in range(20):
                c.execute("INSERT INTO seen VALUES (?, 'live')", ("2026-10-%02dT0900Z" % (i + 1),))
            for i in range(6):
                c.execute("INSERT INTO runs VALUES (?, 1)", ("2026-10-%02dT0907Z" % (i + 15),))
        got_cuts = choose_cuts(db, "5+2")
        assert len(got_cuts) == 7 and {"2026-10-19T0907Z", "2026-10-20T0907Z"} <= set(got_cuts) and got_cuts[0] == "2026-10-01T0900Z", got_cuts
        assert choose_cuts(db, "a,b") == ["a", "b"]
    print("oracle self-test OK")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("dump")
    d.add_argument("--ref", required=True)
    d.add_argument("--cuts", default="12+%d" % LIVE_CUTS)
    d.add_argument("--out", type=Path, required=True)
    c = sub.add_parser("collect")
    c.add_argument("--out", type=Path, required=True)
    c.add_argument("cuts")
    f = sub.add_parser("diff")
    f.add_argument("a", type=Path)
    f.add_argument("b", type=Path)
    f.add_argument("--first", type=int, default=10)
    args = p.parse_args()
    if args.cmd == "dump":
        print("dumped %s in %.1f s" % (args.out, dump(args.ref, args.cuts, args.out)))
    elif args.cmd == "collect":
        collect(args.cuts.split(","), args.out)
    else:
        found = diff(json.loads(args.a.read_text()), json.loads(args.b.read_text()), args.first)
        for k, v in found.items():
            print("%s: %d shown" % (k, len(v)))
            print("\n".join("    " + x for x in v))
        print("differences: %d facts" % len(found))
        raise SystemExit(1 if found else 0)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        raise SystemExit(0)
    main()
