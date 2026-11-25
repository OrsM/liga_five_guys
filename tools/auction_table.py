"""Regenerates src/ffcore/auction_table.py: the win-chance Model fitted once, with the fixed cuts auction.ASK_CUTS and auction.MOMENTUM_CUTS,
over every closed auction of the parse cache.

    PYTHONPATH=src python tools/auction_table.py [--tidy data/tidy] [--cut STAMP]

The parse cache (parsed.jsonl and snapindex.json under --tidy) is read, no database; the World is read at --cut
(default: the last replay cut), so the same files and cut give the same file byte for byte. Prints one digest line:
the cells, the closed auctions they hold, the sha256 of the file."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
from typing import Mapping, NamedTuple

from ffcore import auction, league, loader, parsed, state
from ffcore.auction import Model

TARGET = Path(__file__).resolve().parent.parent / "src" / "ffcore" / "auction_table.py"
HEADER = '''"""The win-chance Model of the auction forecast: auction.fit over every closed auction of the parse cache at one cut,
with the fixed cuts auction.ASK_CUTS and auction.MOMENTUM_CUTS. A tracked knob, not computed per run: seen_at reads auction.forecast(TABLE, ...).
Generated, never edited: regenerate with `PYTHONPATH=src python tools/auction_table.py` (reads the parse cache, no database;
--tidy and --cut choose the cache and the cut)."""
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
    def cells(table: Mapping[auction.Key, tuple[int, object]]) -> str:
        return "".join("        %r: %r,\n" % (k, table[k]) for k in sorted(table))
    return "%s    %r,\n    %r,\n    %r,\n    {\n%s    },\n    {\n%s    },\n)\n" % (
        HEADER, model.ask_cuts, model.momentum_cuts, model.floor, cells(model.demand), cells(model.top))


def digest(model: Model, text: str) -> Digest:
    return Digest(len(model.demand) + len(model.top), model.demand.get(auction.POOLED, (0, 0.0))[0], hashlib.sha256(text.encode()).hexdigest())


def table_at(paths: parsed.Paths, cut: str | None = None) -> Model:
    """auction.fit of the looks of the World at cut, over the parse cache at paths."""
    loaded = loader.load(league.load_config(), paths=paths)
    at = cut if cut is not None else loader.replay_cuts(loaded.obs, "")[-1]
    world = state.world_at(loaded.obs, at)
    return auction.fit(auction.looks_of(world, loaded.identity, loaded.league))


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tidy", type=Path)
    ap.add_argument("--cut")
    args = ap.parse_args(argv)
    model = table_at(parsed.paths_in(args.tidy), args.cut)
    text = render(model)
    TARGET.write_text(text, encoding="utf-8")
    d = digest(model, text)
    print("auction_table cells %d auctions %d sha256 %s" % d)
    return 0


def _selftest() -> None:
    cv = tuple((r, min(1.0, r / 2)) for r in auction.RATIOS)
    model = Model(auction.ASK_CUTS, auction.MOMENTUM_CUTS, 0.98, {auction.POOLED: (30, 0.5), (1, 2, 3): (4, 0.9), (0, 1, auction.ANY): (9, 0.1)},
                  {(): (30, cv), (2,): (7, cv)})
    text = render(model)
    assert text == render(Model(model.ask_cuts, model.momentum_cuts, model.floor, dict(reversed(list(model.demand.items()))),
                                dict(reversed(list(model.top.items()))))), "the text does not depend on the order the cells were built in"
    scope: dict[str, Model] = {}
    exec(compile(text, "auction_table", "exec"), {"__name__": "auction_table"}, scope)
    assert scope["TABLE"] == model, "the text reads back as the model, floats exactly"
    assert digest(model, text).cells == 5 and digest(model, text).auctions == 30
    from ffcore import auction_table
    t = auction_table.TABLE
    assert t.ask_cuts == auction.ASK_CUTS and t.momentum_cuts == auction.MOMENTUM_CUTS
    assert auction.POOLED in t.demand and () in t.top and all(0.0 <= p <= 1.0 for _, p in t.demand.values()) and all(
        n >= 0 and all(a[1] <= b[1] for a, b in zip(c, c[1:])) for n, c in t.top.values()), (
        "the committed table has the fixed cuts, a pooled cell, chances and monotone distributions")
    print("tools.auction_table self-test OK")


if __name__ == "__main__":
    import sys
    if sys.argv[1:] == ["--selftest"]:
        _selftest()
    else:
        raise SystemExit(main(sys.argv[1:]))
