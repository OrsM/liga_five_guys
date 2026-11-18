"""The permanent golden regression test of the sweep (#80 step 10b, #107): the decision documents (decisions.json) and
the key per-cut features the sweep makes at pinned cuts, stored under tools/golden/ and compared on every run.

    python tools/golden.py --selftest              # replay the fixture cuts, compare with tools/golden/ (seconds)
    python tools/golden.py check                   # the same, printing every difference
    python tools/golden.py bless                   # regenerate the fixture goldens: the only way a golden file changes
    python tools/golden.py --live [check|bless] [DB]   # the 5 live cuts on a database (default FF_ROOT/lfg.db, opened mode=ro)

Fixture goldens (decisions-<stamp>.json, features-<stamp>.json): the two latest api_teams snapshots of the real-data
fixture tools/cases/league.json.xz, each decided CUT_AFTER_MINUTES later, through loader.load, state.sweep,
decision.decision_at, decision.opportunity_rows and board.board. Live goldens (live-<stamp>.json): the features only
(the board document of a live cut holds the live account's sources and auction scoring), at LIVE_CUTS, read through
loader.load on a mode=ro connection; they need the production database and are not part of selftests.sh.

Features (one document per cut): money, my squad, the 20 best opportunities, cash of every manager, the auction
rows, the cells of the cases.CASES players, price momentum (slope per horizon, carry), club ratings, offer ratio
quantiles, the ledger's row and inferred counts. Floats are compared to TOLERANCE relative; ids, strings and ints
exactly.

Provenance: the goldens are blessed from the sweep path as it stands (they are not checked against the SQL oracle:
auction numbers differ from it by design since #103). The fixed auction table ffcore/auction_table.py is an input of
every golden decision: after tools/auction_table.py regenerates it, bless in the same commit. A bless is a decision:
its commit says why the goldens changed. Nothing here reads tools/oracle.py or writes outside a bless.

Rules (one assertion each in the self-test):
golden_is_deterministic: two replays of one cut give identical text, in any process and under any hash seed.
generated_at_is_pinned: the document's wall-clock field is the cut, whatever the clock says.
bless_is_explicit: check never writes; only bless changes a golden file."""
from __future__ import annotations

import argparse
import contextlib
import json
import math
import os
import sqlite3
import sys
import tempfile
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import closing
from datetime import timedelta
from pathlib import Path
from typing import Any, NamedTuple

import cases
import crosswalk
import decision
from board import board, stamped
from ffcore import current, db as store, ledger, loader, price_fold, state
from ffcore.league import Config, load_config
from ffcore.parse import snapshot_stamp, stamp
from ffcore.tidy import tables_in
from stats import percentile

GOLDEN = Path(__file__).with_name("golden")
TOLERANCE = 1e-9
FIXTURE_CUTS = 2
LIVE_CUTS = ("2026-10-01T1945Z", "2026-10-22T1946Z", "2026-10-31T0339Z", "2026-11-07T0713Z", "2026-11-16T0905Z")
TOP_OPPORTUNITIES = 20
QUANTILES = (10, 50, 90)

Rendered = dict[str, str]
FIXTURE_KINDS = ("decisions", "features")
LIVE_KINDS = ("live",)


class Cut(NamedTuple):
    name: str
    at: str


class Replayed(NamedTuple):
    cut: Cut
    features: dict[str, Any]
    document: dict[str, Any] | None


def plain(v: Any) -> Any:
    """v as JSON data: named rows as dicts, tuples as lists, sets sorted, dict keys as strings in sorted order."""
    if hasattr(v, "_asdict"):
        return {k: plain(x) for k, x in v._asdict().items()}
    if isinstance(v, dict):
        return {str(k): plain(v[k]) for k in sorted(v, key=str)}
    if isinstance(v, (set, frozenset)):
        return sorted((plain(x) for x in v), key=json.dumps)
    if isinstance(v, (list, tuple)):
        return [plain(x) for x in v]
    return v


def text_of(document: Any) -> str:
    return json.dumps(plain(document), ensure_ascii=False, indent=1) + "\n"


def same(a: Any, b: Any) -> bool:
    """Floats equal to TOLERANCE relative (NaN equals NaN); anything else equal and of one type."""
    if isinstance(a, float) or isinstance(b, float):
        if not isinstance(a, (int, float)) or not isinstance(b, (int, float)) or isinstance(a, bool) or isinstance(b, bool):
            return False
        if math.isnan(a) or math.isnan(b):
            return math.isnan(a) and math.isnan(b)
        return a == b or abs(a - b) <= TOLERANCE * max(abs(a), abs(b))
    return type(a) is type(b) and a == b


def differences(a: Any, b: Any, path: str = "") -> list[str]:
    """Where two JSON values differ, each as 'path: a != b' (a is the golden, b what was made)."""
    if isinstance(a, dict) and isinstance(b, dict):
        found = ["%s/%s: only in the golden" % (path, k) for k in sorted(a.keys() - b.keys())]
        found += ["%s/%s: only in what was made" % (path, k) for k in sorted(b.keys() - a.keys())]
        for k in sorted(a.keys() & b.keys()):
            found += differences(a[k], b[k], "%s/%s" % (path, k))
        return found
    if isinstance(a, list) and isinstance(b, list):
        found = [] if len(a) == len(b) else ["%s: %d rows in the golden, %d made" % (path, len(a), len(b))]
        for i, (x, y) in enumerate(zip(a, b)):
            found += differences(x, y, "%s[%d]" % (path, i))
        return found
    return [] if same(a, b) else ["%s: %r != %r" % (path, a, b)]


def stored_in(directory: Path, kinds: tuple[str, ...]) -> list[Path]:
    """The golden files of directory named <kind>-<stamp>.json for a kind of kinds."""
    return [p for k in kinds for p in sorted(directory.glob("%s-*.json" % k))] if directory.is_dir() else []


def check(directory: Path, made: Rendered, kinds: tuple[str, ...] = FIXTURE_KINDS) -> list[str]:
    """The differences between the golden files of the kinds in directory and the texts made, each prefixed with its
    file; a missing or unexpected file is one. Reads only."""
    stored = {p.name for p in stored_in(directory, kinds)}
    found = ["%s: no golden file (bless it)" % n for n in sorted(made.keys() - stored)]
    found += ["%s: golden file that nothing makes" % n for n in sorted(stored - made.keys())]
    for name in sorted(made.keys() & stored):
        found += ["%s%s" % (name, d) for d in differences(json.loads((directory / name).read_text(encoding="utf-8")),
                                                          json.loads(made[name]))]
    return found


def bless(directory: Path, made: Rendered, kinds: tuple[str, ...] = FIXTURE_KINDS) -> None:
    """The golden files of the kinds in directory made exactly the texts: the only writer."""
    directory.mkdir(parents=True, exist_ok=True)
    for p in stored_in(directory, kinds):
        p.unlink()
    for name, text in made.items():
        (directory / name).write_text(text, encoding="utf-8")


def features(loaded: loader.Loaded, world: state.World, d: decision.Decided, got: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The key facts of one cut: what the decision, the forecast, the price fold and the ledger say there."""
    inp = d.inputs
    wanted = set(cases.CASES.values())
    best = sorted(got, key=lambda r: (-r["gain"], r["player"]))[:TOP_OPPORTUNITIES]
    prices = price_fold.price_momentum_of(price_fold.price_state(world))
    ratios = [r.ratio for r in current.offer_ratios(world)]
    entries = ledger.ledger(world, loaded.identity)
    return {
        "money": inp.money,
        "squad": sorted((p for p in inp.players if p.mine), key=lambda p: p.player),
        "opportunities": [{k: r[k] for k in ("player", "status", "gain", "bid", "win")} for r in best],
        "cash": sorted(d.seen.cash, key=lambda c: c.manager),
        "auctions": sorted(d.seen.auctions, key=lambda a: a.player),
        "cells": {name: sorted((c for c in inp.cells if c.player == k), key=lambda c: c.jornada)
                  for name, k in cases.CASES.items() if k in wanted},
        "price_momentum": {"slope": prices.slope, "carry": prices.carry},
        "club_rating": sorted(current.club_rating(world, loaded.knobs.fixture_k), key=lambda c: c.club),
        "offer_ratios": {"n": len(ratios), **{"p%d" % q: percentile(ratios, q) for q in QUANTILES}},
        "ledger": {"rows": len(entries), "inferred": sum(1 for e in entries if e.inferred)},
    }


def decided_at(loaded: loader.Loaded, world: state.World) -> decision.Decided:
    d = decision.decision_at(loaded, world)
    assert not isinstance(d, decision.Stale), d
    return d


def replayed(loaded: loader.Loaded, cuts: Sequence[Cut], with_document: bool) -> list[Replayed]:
    """Each cut decided in one state.sweep of the loaded observations, in the order given; the wall clock is set to
    the cut before anything reads it."""
    by_at = {c.at: c for c in cuts}

    def read(world: state.World) -> Replayed:
        store.set_now(snapshot_stamp(world.cut))
        d = decided_at(loaded, world)
        got = decision.opportunity_rows(d.inputs)
        document = board(d.inputs, got, [], d.extras) if with_document else None
        return Replayed(by_at[world.cut], features(loaded, world, d, got), document)

    return state.sweep(loaded.obs, [c.at for c in cuts], read)


def after(at: str, delay: timedelta) -> str:
    when = snapshot_stamp(at)
    assert when is not None, at
    return stamp(when + delay)


def fixture_cuts(doc: dict[str, Any]) -> list[Cut]:
    """The latest FIXTURE_CUTS distinct api_teams snapshots of the fixture, each as the cut CUT_AFTER_MINUTES later."""
    t = doc["tables"]["api_teams"]
    i = t["columns"].index("observed_at")
    stamps = sorted({r[i] for r in t["rows"]})[-FIXTURE_CUTS:]
    return [Cut(s, after(s, timedelta(minutes=cases.CUT_AFTER_MINUTES))) for s in stamps]


@contextlib.contextmanager
def fixture_loaded(doc: dict[str, Any]) -> Iterator[loader.Loaded]:
    """The fixture's tables in a scratch database, loaded, for the length of the block."""
    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)), contextlib.chdir(tmp):
        with closing(store.connect()) as db, db:
            for name, t in doc["tables"].items():
                cols = t["columns"]
                db.executemany("INSERT INTO %s (%s) VALUES (%s)" % (store.source_table(name), ", ".join(cols), ", ".join("?" * len(cols))),
                               t["rows"])
        with closing(store.connect()) as db, db:
            crosswalk.build(db)
        with closing(store.connect()) as db:
            yield loader.load(db, Config())


def rendered(loaded: loader.Loaded, cuts: Sequence[Cut]) -> Rendered:
    """The fixture goldens' texts: decisions-<stamp>.json and features-<stamp>.json of each of the cuts."""
    out: Rendered = {}
    for r in replayed(loaded, cuts, with_document=True):
        out["decisions-%s.json" % r.cut.name] = text_of(r.document)
        out["features-%s.json" % r.cut.name] = text_of(r.features)
    return out


def render_fixture(doc: dict[str, Any]) -> Rendered:
    with fixture_loaded(doc) as loaded:
        return rendered(loaded, fixture_cuts(doc))


def render_live(path: Path) -> Rendered:
    """The live goldens' texts: live-<stamp>.json of each LIVE_CUTS, from the database at path opened mode=ro."""
    with closing(sqlite3.connect("file:%s?mode=ro" % path, uri=True)) as db:
        loaded = loader.load(db, load_config())
    return {"live-%s.json" % r.cut.name: text_of(r.features)
            for r in replayed(loaded, [Cut(c, c) for c in LIVE_CUTS], with_document=False)}


def report(found: list[str], what: str, first: int = 20) -> int:
    for line in found[:first]:
        print("  " + line)
    if len(found) > first:
        print("  ... %d more" % (len(found) - first))
    print("golden %s: %s" % (what, "%d differences" % len(found) if found else "equal"))
    return 1 if found else 0


def _selftest() -> None:
    doc = cases.load()
    cuts = fixture_cuts(doc)
    t0 = time.monotonic()
    with fixture_loaded(doc) as loaded:
        made = rendered(loaded, cuts)
        last = cuts[-1]
        os.environ["LFG_NOW"] = "2031-01-01T0000Z"
        try:
            again = replayed(loaded, [last], with_document=True)[0]
        finally:
            del os.environ["LFG_NOW"]
    assert len(cuts) == FIXTURE_CUTS and set(made) == {"%s-%s.json" % (k, c.name) for c in cuts for k in ("decisions", "features")}

    found = check(GOLDEN, made)
    assert not found, "the sweep no longer makes the goldens (tools/golden.py bless, if the change is meant):\n" + "\n".join(found[:20])

    assert text_of(again.document) == made["decisions-%s.json" % last.name], "golden_is_deterministic"
    assert text_of(again.features) == made["features-%s.json" % last.name], "golden_is_deterministic"
    assert json.loads(made["decisions-%s.json" % last.name])["generated_at"] == stamped(last.at), "generated_at_is_pinned"

    with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as tmp:
        d = Path(tmp)
        assert check(d, made) == [f"{n}: no golden file (bless it)" for n in sorted(made)] and not list(d.iterdir()), "bless_is_explicit"
        bless(d, made)
        before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in d.iterdir()}
        assert check(d, made) == [] and before == {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in d.iterdir()}, "bless_is_explicit"
        name = "features-%s.json" % last.name
        edited = json.loads(made[name])
        edited["money"]["cash"] *= 1.001
        edited["squad"].pop()
        (d / name).write_text(text_of(edited), encoding="utf-8")
        found = check(d, made)
        assert any(x.startswith(name + "/money/cash: ") and "!=" in x for x in found) and any("rows in the golden" in x for x in found), found
        bless(d, made)
        assert check(d, made) == []

    assert same(1.0, 1.0 + 1e-12) and not same(1.0, 1.0 + 1e-6) and same(float("nan"), float("nan"))
    assert not same(1, "1") and not same(True, 1) and same(5, 5) and not same(5, 6)
    assert differences({"a": [1, 2], "b": "x"}, {"a": [1, 3], "c": 1}) == [
        "/b: only in the golden", "/c: only in what was made", "/a[1]: 2 != 3"]
    assert plain({"s": frozenset({"b", "a"}), 2: (cases.Market("p", 1.0),)}) == {"2": [{"player": "p", "value": 1.0}], "s": ["a", "b"]}
    print("golden self-test OK (%d cuts in %.1f s)" % (len(cuts), time.monotonic() - t0))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--selftest", action="store_true")
    p.add_argument("--live", action="store_true")
    p.add_argument("command", nargs="?", choices=("check", "bless"), default="check")
    p.add_argument("db", nargs="?", type=Path, default=store.DB)
    args = p.parse_args()
    if args.selftest:
        _selftest()
        return 0
    made = render_live(args.db) if args.live else render_fixture(cases.load())
    kinds = LIVE_KINDS if args.live else FIXTURE_KINDS
    if args.command == "bless":
        bless(GOLDEN, made, kinds)
        print("golden bless: wrote %d files to %s" % (len(made), GOLDEN))
        return 0
    return report(check(GOLDEN, made, kinds), "live check" if args.live else "check")


if __name__ == "__main__":
    sys.exit(main())
