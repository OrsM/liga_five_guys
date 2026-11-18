"""The observations (ingest) read from the parse cache instead of from src_*: the same rows read() returns, row for
row, built from Paths.cache (parsed.jsonl: a document's rows by content key) and Paths.snaps (snapindex.json: which
document each page had at each snapshot stamp), through the typing store_sql applies (tidy.typed) and the dedupe
the tables apply on insert (observations.kept).

read(paths, names, until, latest) gives an Observations equal to observations.read of a database that parse filled
from the same cache; a page whose document is not in the cache has no rows, as parse would have needed to parse it.
Selectors: Paths."""
from __future__ import annotations

import json
import typing
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any, NamedTuple

from ffcore import observations as ob
from ffcore.laliga_api import ROW_TABLE
from ffcore.tidy import NUMBERS, typed
from ingest import fingerprint
from sources import source, source_for


class Paths(NamedTuple):
    cache: Path
    snaps: Path


class Doc(NamedTuple):
    page: str
    key: str


def _text(value: Any) -> str:
    return value if isinstance(value, str) else str(value or "")


def _real(value: Any) -> float | None:
    return None if value is None else float(value)


def _whole(value: Any) -> int | None:
    return None if value is None else int(value)


def _coercion(hint: Any) -> Callable[[Any], Any]:
    """How a column of the type hint is stored: text never NULL, a number as its affinity."""
    kinds = [a for a in typing.get_args(hint) if a is not type(None)] or [hint]
    return {str: _text, float: _real, int: _whole}[kinds[0]]


def stored(table: str, rows: Iterable[Mapping[str, Any]]) -> list[tuple[Any, ...]]:
    """The columns of rows (a document's, as parse emitted them) after observed_at, exactly as the table of ob.TABLES
    keeps them: text stripped, numbers typed (tidy.NUMBERS), a missing text column empty, a missing number None."""
    row = ob.TABLES[table].row
    hints = typing.get_type_hints(row)
    cols = [(c, _coercion(hints[c])) for c in row._fields[1:]]
    numbers = {c for c, _parse in NUMBERS.get(table, ())}
    clean = [{k: v.strip() if isinstance(v, str) else v for k, v in r.items()} for r in rows]
    return [ob.pooled(fix(r.get(c) if c in numbers else (r.get(c) or "")) for c, fix in cols)
            for r in typed(table, clean)]


def documents(snaps_file: Path, until: str | None) -> Iterator[tuple[str, list[Doc]]]:
    """Each snapshot stamp (at most until) in order, with the documents its pages had, in the order parse routes them
    (pages sorted); a page no source reads is left out."""
    snaps = json.loads(snaps_file.read_text(encoding="utf-8"))["snaps"]
    for stamp in sorted(snaps):
        if until is not None and stamp > until:
            return
        pages = snaps[stamp]["pages"]
        yield stamp, [Doc(page, "%s:%s" % (ck, fingerprint(source(page).parse)))
                      for page, (ck, _origin) in sorted(pages.items()) if source_for(page)]


def contents(cache: Path, keys: set[str]) -> dict[str, list[dict[str, Any]]]:
    """The rows of each wanted document key in cache (the last record of a key wins)."""
    out: dict[str, list[dict[str, Any]]] = {}
    with cache.open(encoding="utf-8") as fh:
        for line in fh:
            if line[7:line.find('"', 7)] in keys:
                rec = json.loads(line)
                out[rec["k"]] = rec["r"]
    return out


def routed(doc: Doc, rows: Iterable[Mapping[str, Any]], names: Iterable[str]) -> dict[str, list[tuple[Any, ...]]]:
    """A document's rows per table of names: each row goes to its own table (laliga_api.ROW_TABLE) or its page's."""
    home = source(doc.page).table
    parts: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for r in rows:
        parts[r.get(ROW_TABLE) or home].append(r)
    return {t: stored(t, parts[t]) for t in names if t in parts}


def read(paths: Paths, names: Iterable[str] = tuple(ob.TABLES), until: str | None = None,
         latest: bool = False) -> ob.Observations:
    """The named tables as ob.read would return them from a database parse filled from paths: rows of the
    snapshots at most until (all when None), deduped as the tables dedupe on insert, in the order parse stores them;
    with latest, only each source's newest snapshot of that range."""
    wanted = list(names)
    walk = list(documents(paths.snaps, until))
    docs = {d.key: d for _stamp, ds in walk for d in ds}
    raw = contents(paths.cache, set(docs))
    made = {key: routed(d, raw.get(key, ()), wanted) for key, d in docs.items()}
    del raw

    def stream(table: str) -> Iterator[ob.Stamped]:
        for stamp, ds in walk:
            at = ob.POOL.setdefault(stamp, stamp)
            for d in ds:
                for body in made[d.key].get(table, ()):
                    yield ob.Stamped(at, body)

    def table_of(table: str) -> list[Any]:
        row = ob.TABLES[table].row
        got = [row._make((r.observed_at, *r.body)) for r in ob.kept(table, stream(table))]
        return ob.newest(got) if latest else got
    return ob.of({t: table_of(t) for t in wanted})
