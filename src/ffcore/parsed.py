"""The observations (ingest) read from the parse cache instead of from src_*: the same rows read() returns, row for
row, built from Paths.cache (parsed.jsonl: a document's rows by content key) and Paths.snaps (snapindex.json: which
document each page had at each snapshot stamp), through the typing store_sql applies (tidy.cell) and the dedupe
the tables apply on insert (observations.keeper).

read(paths, names, until, latest) gives an Observations equal to observations.read of a database that parse filled
from the same cache; a page whose document is not in the cache has no rows, as parse would have needed to parse it.
A document is typed once, each distinct raw value of a column once; a document the tables already hold is answered
by one comparison. Selectors: Paths."""
from __future__ import annotations

import contextlib
import functools
import gc
import itertools
import json
import typing
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any, NamedTuple

from ffcore import observations as ob
from ffcore.laliga_api import ROW_TABLE
from ffcore.tidy import cell
import ingest
from ingest import fingerprint
from sources import source, source_for


class Paths(NamedTuple):
    cache: Path
    snaps: Path


def paths_in(tidy: Path | None = None) -> Paths:
    """The parse cache and the snapshot index in tidy (ingest.TIDY when None)."""
    home = ingest.TIDY if tidy is None else tidy
    return Paths(home / ingest._CACHE, home / ingest._INDEX)  # noqa: SLF001 -- the file names are ingest's, not repeated here


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


class Column(dict[Any, Any]):
    """The stored value of each raw value of one column, found once (a str or None raw value is remembered; every str
    result is the pooled object of ob.POOL)."""

    def __init__(self, store: Callable[[Any], Any]) -> None:
        super().__init__()
        self.store = store

    def convert(self, raw: Any) -> Any:
        got = self.store(raw)
        return ob.POOL.setdefault(got, got) if type(got) is str else got

    def __missing__(self, raw: Any) -> Any:
        got = self.convert(raw)
        if raw is None or type(raw) is str:
            self[raw] = got
        return got

    def of(self, raws: Iterable[Any]) -> list[Any]:
        """The stored values of raws, in order."""
        held = list(raws)
        try:
            return list(map(self.__getitem__, held))
        except TypeError:
            return list(map(self.convert, held))


@functools.cache
def _column(to: Callable[[Any], Any], coerce: Callable[[Any], Any]) -> Column:
    return Column(lambda raw: coerce(to(raw)))


@functools.cache
def column(table: str, name: str) -> Column:
    """The Column of table's column name, as tidy.cell and the table's declared type store it; columns stored alike
    share one."""
    coerce = _coercion(typing.get_type_hints(ob.TABLES[table].row)[name])
    return _column(cell(table, name), coerce)


def stored(table: str, rows: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
    """The columns of rows (a document's, as parse emitted them) after observed_at, exactly as the table of ob.TABLES
    keeps them: text stripped, numbers typed (tidy.NUMBERS), a missing text column empty, a missing number None."""
    names = ob.TABLES[table].row._fields[1:]
    return list(zip(*(column(table, n).of(map(dict.get, rows, itertools.repeat(n))) for n in names)))


def documents(snaps_file: Path, until: str | None) -> Iterator[tuple[str, list[Doc]]]:
    """Each snapshot stamp (at most until) in order, with the documents its pages had, in the order parse routes them
    (pages sorted); a page no source reads is left out."""
    snaps = json.loads(snaps_file.read_text(encoding="utf-8"))["snaps"]
    prints: dict[str, str | None] = {}
    docs: dict[tuple[str, str], Doc] = {}

    def print_of(page: str) -> str | None:
        if page not in prints:
            prints[page] = fingerprint(source(page).parse) if source_for(page) else None
        return prints[page]

    def doc_of(page: str, ck: str, fp: str) -> Doc:
        return docs.setdefault((page, ck), Doc(page, "%s:%s" % (ck, fp)))
    for stamp in sorted(snaps):
        if until is not None and stamp > until:
            return
        pages = snaps[stamp]["pages"]
        yield stamp, [doc_of(page, ck, fp)
                      for page, (ck, _origin) in sorted(pages.items()) if (fp := print_of(page))]


def routed(doc: Doc, rows: list[dict[str, Any]], names: Iterable[str]) -> dict[str, ob.Part]:
    """A document's rows per table of names: each row goes to its own table (laliga_api.ROW_TABLE) or its page's."""
    home = source(doc.page).table
    parts: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if any(map(dict.get, rows, itertools.repeat(ROW_TABLE))):
        for r in rows:
            parts[r.get(ROW_TABLE) or home].append(r)
    else:
        parts[home] = rows
    return {t: ob.part(t, stored(t, parts[t])) for t in names if t in parts}


def contents(cache: Path, docs: Mapping[str, Doc], names: Iterable[str]) -> dict[str, dict[str, ob.Part]]:
    """The routed rows of each wanted document key of docs in cache (the last record of a key wins)."""
    wanted = list(names)
    out: dict[str, dict[str, ob.Part]] = {}
    with cache.open(encoding="utf-8") as fh:
        for line in fh:
            key = line[7:line.find('"', 7)]
            if key in docs:
                out[key] = routed(docs[key], json.loads(line)["r"], wanted)
    return out


@contextlib.contextmanager
def _steady() -> Iterator[None]:
    """The cyclic collector paused (it only finds cycles, and the rows read are all kept), as it was after."""
    was = gc.isenabled()
    gc.disable()
    try:
        yield
    finally:
        if was:
            gc.enable()


def read(paths: Paths, names: Iterable[str] = tuple(ob.TABLES), until: str | None = None,
         latest: bool = False) -> ob.Observations:
    """The named tables as ob.read would return them from a database parse filled from paths: rows of the
    snapshots at most until (all when None), deduped as the tables dedupe on insert, in the order parse stores them;
    with latest, only each source's newest snapshot of that range."""
    with _steady():
        wanted = list(names)
        walk = list(documents(paths.snaps, until))
        made = contents(paths.cache, {d.key: d for _stamp, ds in walk for d in ds}, wanted)
        keeps = {t: ob.keeper(t) for t in wanted}
        shapes = {t: itertools.repeat(ob.TABLES[t].row) for t in wanted}
        got: dict[str, list[Any]] = {t: [] for t in wanted}
        ends: dict[int, list[str]] = defaultdict(list)
        for key, last in {d.key: i for i, (_stamp, ds) in enumerate(walk) for d in ds}.items():
            ends[last].append(key)
        for i, (stamp, ds) in enumerate(walk):
            at = ob.POOL.setdefault(stamp, stamp)
            head = (at,)
            for d in ds:
                for t, held in made.get(d.key, {}).items():
                    got[t].extend(map(tuple.__new__, shapes[t], map(head.__add__, keeps[t](at, held))))
            for key in ends[i]:
                made.pop(key, None)
        return ob.of({t: ob.newest(rows) if latest else rows for t, rows in got.items()})
