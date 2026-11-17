"""Regenerates src/ffcore/auction_table.py: the win-chance Model fitted once, with the fixed cuts auction.FIXED_CUTS,
over every closed auction of a database.

    PYTHONPATH=src python tools/auction_table.py [--db data/lfg.db] [--cut STAMP]

The database is opened read-only; the World is read at --cut (default: the last replay cut), so the same database and
cut give the same file byte for byte. Prints one digest line: the cells, the closed auctions they hold, the sha256 of
the file."""
from __future__ import annotations

import argparse
import hashlib
import sqlite3
from pathlib import Path
from typing import NamedTuple

from ffcore import auction, league, loader, state
from ffcore.auction import Model

TARGET = Path(__file__).resolve().parent.parent / "src" / "ffcore" / "auction_table.py"
HEADER = '''"""The win-chance Model of the auction forecast: auction.fit over every closed auction of the database at one cut,
with the fixed cuts auction.FIXED_CUTS. A tracked knob, not computed per run: seen_at reads auction.forecast(TABLE, ...).
Generated, never edited: regenerate with `PYTHONPATH=src python tools/auction_table.py` (reads data/lfg.db read-only;
--db and --cut choose the database and the cut)."""
from __future__ import annotations

from ffcore.auction import Model

TABLE = Model(
'''


class Digest(NamedTuple):
    cells: int
    auctions: int
    sha256: str


def render(model: Model) -> str:
    """The text of auction_table.py for model: python literals, cells in key order, one per line."""
    cells = "".join("    %r: %r,\n" % (k, model.cells[k]) for k in sorted(model.cells))
    return "%s    %r,\n    %r,\n    {\n%s    },\n)\n" % (HEADER, model.ask_cuts, model.trend_cuts, cells)


def digest(model: Model, text: str) -> Digest:
    return Digest(len(model.cells), model.cells.get((), (0, ()))[0], hashlib.sha256(text.encode()).hexdigest())


def table_at(db: sqlite3.Connection, cut: str | None = None) -> Model:
    """auction.fit of the looks of the World at cut, with the fixed cuts."""
    loaded = loader.load(db, league.load_config())
    at = cut if cut is not None else loader.replay_cuts(loaded.obs, "")[-1]
    world = state.world_at(loaded.obs, at)
    return auction.fit(auction.looks_of(world, loaded.identity, loaded.league), auction.FIXED_CUTS)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/lfg.db")
    ap.add_argument("--cut")
    args = ap.parse_args(argv)
    db = sqlite3.connect("file:%s?mode=ro" % args.db, uri=True)
    model = table_at(db, args.cut)
    text = render(model)
    TARGET.write_text(text, encoding="utf-8")
    d = digest(model, text)
    print("auction_table cells %d auctions %d sha256 %s" % d)
    return 0


def _selftest() -> None:
    cv = tuple((r, min(1.0, r / 2)) for r in auction.RATIOS)
    model = Model(auction.ASK_CUTS, auction.TREND_CUTS, {(1, 2): (7, cv), (): (30, cv), (0,): (9, cv), (0, 1, 0): (4, cv)})
    text = render(model)
    assert text == render(Model(model.ask_cuts, model.trend_cuts, dict(reversed(list(model.cells.items()))))), \
        "the text does not depend on the order the cells were built in"
    scope: dict[str, Model] = {}
    exec(compile(text, "auction_table", "exec"), {"__name__": "auction_table"}, scope)
    assert scope["TABLE"] == model, "the text reads back as the model, floats exactly"
    assert digest(model, text).cells == 4 and digest(model, text).auctions == 30
    from ffcore import auction_table
    assert auction_table.TABLE.ask_cuts == auction.ASK_CUTS and auction_table.TABLE.trend_cuts == auction.TREND_CUTS
    assert () in auction_table.TABLE.cells and all(
        n >= 0 and all(a[1] <= b[1] for a, b in zip(c, c[1:])) for n, c in auction_table.TABLE.cells.values()), (
        "the committed table has the fixed cuts, a pooled cell, and monotone curves")
    print("tools.auction_table self-test OK")


if __name__ == "__main__":
    import sys
    if sys.argv[1:] == ["--selftest"]:
        _selftest()
    else:
        raise SystemExit(main(sys.argv[1:]))
