"""The record kinds a run leaves behind one NamedTuple each, and
their line format. A row on disk is one JSON object with the NamedTuple's field names; a file's first line is
its header {"format": kind, "v": N}. Writers write LATEST[kind]; a reader upgrades a row of version v through
READERS[(kind, v)] to the current NamedTuple, and an unknown version is an error. Floats round-trip exactly (JSON
repr), null is None. The big kind is stored as a typed array (GRADE_CELL_DTYPE: player is PLAYER_BYTES of UTF-8,
plays NaN for None), run_id being the directory's. latest(rows, key) is the last two measure_log rows per key by
(at, position in rows)."""
from __future__ import annotations

import json
import sys
from collections.abc import Callable, Hashable, Iterable, Iterator, Mapping
from typing import Any, NamedTuple

import numpy as np
import numpy.typing as npt

__all__ = ["AuctionBrier", "AuctionCalibration", "DEC_OFFER_V1", "DecOffer", "DecOpportunity", "GRADE_CELL_DTYPE", "GradeCell", "KINDS", "KindSpec",
           "LATEST", "Latest", "MeasureLog", "PLAYER_BYTES", "Param", "READERS", "Run", "RunMeasure", "SaleScore", "StoreError",
           "change", "decode", "encode", "grade_cell_array", "grade_cell_rows", "header", "latest", "parse_header"]


class StoreError(ValueError):
    """A stored file that cannot be trusted: its message names the file and line."""


class Run(NamedTuple):
    run_id: int
    started: str
    as_of: str
    trigger: str
    commit_sha: str
    finished: str | None
    ok: int | None
    peak_mb: float | None


class RunMeasure(NamedTuple):
    run_id: int
    stage: str
    measure: str
    value: float


class MeasureLog(NamedTuple):
    at: str
    commit_sha: str
    measure: str
    key: str
    value: float


class Param(NamedTuple):
    run_id: int
    component: str
    term: str
    value: float
    kind: str
    n: int | None


class AuctionCalibration(NamedTuple):
    run_id: int
    scope: str
    bin: str
    n: int
    stated: float
    observed: float
    drift: int


class AuctionBrier(NamedTuple):
    run_id: int
    scope: str
    n: int
    model: float
    pooled: float


class GradeCell(NamedTuple):
    run_id: int
    from_j: int
    jornada: int
    player: str
    predicted: float
    plays: float | None
    flagged: int


class DecOpportunity(NamedTuple):
    cut: str
    player: str
    pos: str
    status: str
    asking: float | None
    close_at: str | None
    from_j: int
    replaces: str | None
    gain: float
    per_million: float | None
    price_next: float | None
    price_lock: float | None
    bid: float | None
    win: float | None
    placed: float | None


class DecOffer(NamedTuple):
    """One standing offer on one of my players at a run's cut, as the board stated it: delta_next4 and delta_season the Delta of
    selling him over the next four jornadas and the season (0 for a bench player), None when no legal eleven remains without him
    (a replacement must be bought first) or when the row is from before the Delta existed; fields whether the squad after the sale
    fields a legal eleven (None: not recorded); gap the jornadas the sale leaves without one joined by ';' (empty: none; None: not
    recorded); funds the names of the buys it funds tonight joined by ';' (empty: none)."""
    cut: str
    player: str
    offer_id: str
    created_at: str
    offer: float
    value: float
    ratio: float | None
    percentile: float | None
    offers_seen: int
    delta_next4: float | None
    delta_season: float | None
    fields: bool | None
    gap: str | None
    funds: str
    trend: float | None


DEC_OFFER_V1 = ("cut", "player", "offer_id", "created_at", "offer", "value", "ratio", "percentile", "offers_seen", "lost4", "lost8",
                "lost_season", "funds", "bench", "trend")


class SaleScore(NamedTuple):
    run_id: int
    group: str
    case: str
    horizon: str
    n: int
    total_net: float
    per_million: float
    loo_min: float
    loo_max: float
    bootstrap_lo: float
    bootstrap_hi: float


class KindSpec(NamedTuple):
    """A kind's name, its row type and the file (inside a run directory) that holds it."""
    name: str
    row: type[Any]
    filename: str


KINDS: Mapping[str, KindSpec] = {s.name: s for s in (
    KindSpec("runs", Run, "run.json"),
    KindSpec("run_measures", RunMeasure, "measures.jsonl"),
    KindSpec("measure_log", MeasureLog, "measure_log.jsonl"),
    KindSpec("params", Param, "params.jsonl"),
    KindSpec("auction_calibration", AuctionCalibration, "calibration.jsonl"),
    KindSpec("auction_brier", AuctionBrier, "brier.jsonl"),
    KindSpec("grade_cell", GradeCell, "grade_cell.npy"),
    KindSpec("dec_opportunity", DecOpportunity, "opportunity.jsonl"),
    KindSpec("dec_offer", DecOffer, "offer.jsonl"),
    KindSpec("sale_score", SaleScore, "sale_score.jsonl"))}

LATEST: Mapping[str, int] = {name: 2 if name == "dec_offer" else 1 for name in KINDS}

PLAYER_BYTES = 24
GRADE_CELL_DTYPE = np.dtype([("from_j", "<i4"), ("jornada", "<i4"), ("player", f"S{PLAYER_BYTES}"),
                             ("predicted", "<f8"), ("plays", "<f8"), ("flagged", "<i4")])


def _read_v1(kind: str) -> Callable[[Mapping[str, Any]], Any]:
    row = KINDS[kind].row

    def read(obj: Mapping[str, Any]) -> Any:
        if set(obj) != set(row._fields):
            raise StoreError("%s row has fields %s, expected %s" % (kind, sorted(obj), sorted(row._fields)))
        return row(**obj)
    return read


def _dec_offer_v1(obj: Mapping[str, Any]) -> DecOffer:
    """A version 1 dec_offer row as a DecOffer: its lost4 and lost_season negated into delta_next4 and delta_season, fields and gap
    not recorded (None); lost8 and bench are dropped (bench is a Delta of 0)."""
    if set(obj) != set(DEC_OFFER_V1):
        raise StoreError("dec_offer row has fields %s, expected %s" % (sorted(obj), sorted(DEC_OFFER_V1)))
    def negated(x: float | None) -> float | None:
        return None if x is None else 0.0 - x
    return DecOffer(**{f: obj[f] for f in ("cut", "player", "offer_id", "created_at", "offer", "value", "ratio", "percentile",
                                           "offers_seen", "funds", "trend")},
                    delta_next4=negated(obj["lost4"]), delta_season=negated(obj["lost_season"]), fields=None, gap=None)


READERS: Mapping[tuple[str, int], Callable[[Mapping[str, Any]], Any]] = {
    (name, v): _read_v1(name) for name, v in LATEST.items()} | {("dec_offer", 1): _dec_offer_v1}


def header(kind: str) -> str:
    """The first line of a kind's file (newline-terminated)."""
    return json.dumps({"format": kind, "v": LATEST[kind]}) + "\n"


def parse_header(line: bytes, kind: str) -> int:
    """The version a file's first line declares; StoreError when it is not a header of that kind."""
    try:
        head = json.loads(line)
    except ValueError:
        head = None
    if not isinstance(head, dict) or head.get("format") != kind or not isinstance(head.get("v"), int):
        raise StoreError("not a %s file: first line is %r" % (kind, line[:60]))
    version: int = head["v"]
    if (kind, version) not in READERS:
        raise StoreError("%s file is version %d; this code reads %s" % (
            kind, version, sorted(v for k, v in READERS if k == kind)))
    return version


def encode(row: NamedTuple) -> str:
    """One row as a line of JSON (newline-terminated), keys the field names."""
    return json.dumps(row._asdict()) + "\n"


def decode(kind: str, version: int, line: bytes) -> Any:
    """A line of a kind's file at that version as the current NamedTuple; StoreError when it is torn or wrong."""
    if not line.endswith(b"\n"):
        raise StoreError("line is cut short (no newline): %r" % line[-40:])
    try:
        obj = json.loads(line)
    except ValueError as e:
        raise StoreError("line is not JSON (%s): %r" % (e, line[:60])) from None
    if not isinstance(obj, dict):
        raise StoreError("line is not an object: %r" % line[:60])
    return READERS[(kind, version)](obj)


def grade_cell_array(rows: Iterable[GradeCell]) -> npt.NDArray[Any]:
    """The rows (one run's) as a GRADE_CELL_DTYPE array; plays None becomes NaN. A player longer than PLAYER_BYTES
    in UTF-8 is a StoreError."""
    def cells() -> Iterator[tuple[int, int, bytes, float, float, int]]:
        for r in rows:
            name = r.player.encode()
            if len(name) > PLAYER_BYTES:
                raise StoreError("player %r is longer than %d bytes" % (r.player, PLAYER_BYTES))
            yield (r.from_j, r.jornada, name, r.predicted, float("nan") if r.plays is None else r.plays, r.flagged)
    return np.fromiter(cells(), dtype=GRADE_CELL_DTYPE)


def grade_cell_rows(cells: npt.NDArray[Any], run_id: int) -> Iterator[GradeCell]:
    """An array's rows as GradeCell tuples of that run, in order; NaN plays is None."""
    for c in cells:
        plays = float(c["plays"])
        yield GradeCell(run_id, int(c["from_j"]), int(c["jornada"]), bytes(c["player"]).decode(),
                        float(c["predicted"]), None if plays != plays else plays, int(c["flagged"]))


class Latest(NamedTuple):
    """A key's newest measure_log row and the one before it (None when there is only one)."""
    last: MeasureLog
    previous: MeasureLog | None


def latest(rows: Iterable[MeasureLog], key: Callable[[MeasureLog], Hashable]) -> dict[Hashable, Latest]:
    """Per key, the newest row by (at, position in rows) and the one before it."""
    kept: dict[Hashable, list[tuple[str, int, MeasureLog]]] = {}
    for position, r in enumerate(rows):
        pair = kept.setdefault(key(r), [])
        pair.append((r.at, position, r))
        pair.sort(key=lambda t: t[:2])
        del pair[:-2]
    return {k: Latest(v[-1][2], v[-2][2] if len(v) > 1 else None) for k, v in kept.items()}


def change(pair: Latest) -> float | None:
    """The newest value less the previous one; None when there is no previous."""
    return None if pair.previous is None else pair.last.value - pair.previous.value


def _selftest() -> None:
    sample: dict[str, Any] = {
        "runs": Run(7, "2026-11-01T10:00:00Z", "2026-11-01T10:00:00Z", "service", "abc1234", None, None, 12.5),
        "run_measures": RunMeasure(7, "board", "secs", 0.1 + 0.2),
        "measure_log": MeasureLog("2026-11-01T10:00:00Z", "abc1234", "accuracy", "rmse@5", 1 / 3),
        "params": Param(7, "auction", "", 5e-324, "fitted", None),
        "auction_calibration": AuctionCalibration(7, "all", "0.1-0.2", 12, 0.15, 1e300, 1),
        "auction_brier": AuctionBrier(7, "all", 30, float("inf"), -0.0),
        "grade_cell": GradeCell(7, 3, 4, "Pedri ñ", 2.5, None, 1),
        "dec_opportunity": DecOpportunity("2026-11-01T1000Z", "Pedri", "MED", "open", None, None, 4, None, 1.5,
                                          None, None, None, 4.2, 0.3, None),
        "dec_offer": DecOffer("2026-11-01T1000Z", "Pedri", "49892747", "2026-11-01T09:00:00Z", 6795815.0, 1 / 3, None, 0.1 + 0.2,
                              752, 0.0, -0.0, True, "14;15", "Gavi;Raphinha", None),
        "sale_score": SaleScore(7, "m1", "eleven-proxy bound", "season", 12, -3.5, -0.25, -9.0, 2.0, -11.5, 4.5)}
    assert set(sample) == set(KINDS) == set(LATEST), "every kind has a sample, a spec and a version"
    for kind, row in sample.items():
        assert type(row) is KINDS[kind].row, kind
        if kind == "grade_cell":
            back = list(grade_cell_rows(grade_cell_array([row]), row.run_id))[0]
        else:
            back = decode(kind, parse_header(header(kind).encode(), kind), encode(row).encode())
        assert back == row, (kind, back, row)
        assert [type(a) for a in back] == [type(a) for a in row], "types, so None stays None and 1.0 stays a float"
    v1 = {"cut": "2026-11-01T1000Z", "player": "Pedri", "offer_id": "9", "created_at": "c", "offer": 2.0, "value": 1.5, "ratio": None,
           "percentile": 0.5, "offers_seen": 4, "lost4": 1.5, "lost8": None, "lost_season": 0.0, "funds": "Gavi", "bench": True, "trend": 0.25}
    up = decode("dec_offer", parse_header(b'{"format": "dec_offer", "v": 1}', "dec_offer"), (json.dumps(v1) + "\n").encode())
    assert up == DecOffer("2026-11-01T1000Z", "Pedri", "9", "c", 2.0, 1.5, None, 0.5, 4, -1.5, 0.0, None, None, "Gavi", 0.25), \
        "an old row reads as a DecOffer: the loss negated into the Delta, fields and gap not recorded"
    assert LATEST["dec_offer"] == 2 and parse_header(header("dec_offer").encode(), "dec_offer") == 2
    try:
        decode("dec_offer", 1, (json.dumps(v1 | {"delta_next4": 1.0}) + "\n").encode())
    except StoreError:
        pass
    else:
        raise AssertionError("a version 1 row with a version 2 field is refused")
    for x in (0.1 + 0.2, float("nan"), -0.0, 1.7976931348623157e308):
        got = decode("run_measures", 1, encode(RunMeasure(1, "s", "m", x)).encode()).value
        assert np.float64(got).tobytes() == np.float64(x).tobytes(), "float bits survive the line format"

    for torn in (b'{"run_id": 1, "stage": "s", "measure": "m", "va', b'{"run_id": 1}\n', b"[1]\n", b'{"run_id": 1, "stage": "s", "measure": "m", "value": 1.0}'):
        try:
            decode("run_measures", 1, torn)
        except StoreError as e:
            assert str(e), "a readable message"
        else:
            raise AssertionError("accepted %r" % torn)
    for bad in (b'{"format": "params", "v": 1}', b'{"format": "run_measures", "v": 99}', b"nonsense"):
        try:
            parse_header(bad, "run_measures")
        except StoreError:
            pass
        else:
            raise AssertionError("accepted header %r" % bad)

    long_name = GradeCell(1, 1, 1, "x" * (PLAYER_BYTES + 1), 1.0, None, 0)
    try:
        grade_cell_array([long_name])
    except StoreError:
        pass
    else:
        raise AssertionError("a player that does not fit is refused, not cut")

    import random
    rng = random.Random(5)
    log = [MeasureLog("2026-11-%02dT00:00:00Z" % rng.randint(1, 4), "sha%d" % i, rng.choice(["accuracy", "sale_grade"]),
                      rng.choice("abc"), rng.random()) for i in range(300)]
    expected: dict[tuple[str, str], tuple[Any, ...]] = {}
    for measure, key in {(r.measure, r.key) for r in log}:
        rows_of_key = [r for r in log if (r.measure, r.key) == (measure, key)]
        order = sorted(range(len(rows_of_key)), key=lambda i: (rows_of_key[i].at, i))
        new, old = rows_of_key[order[-1]], (rows_of_key[order[-2]] if len(order) > 1 else None)
        expected[(measure, key)] = (new.value, new.at, new.commit_sha, old and old.value, old and old.at, old and old.commit_sha,
                                    None if old is None else new.value - old.value)
    got = {k: (p.last.value, p.last.at, p.last.commit_sha, p.previous.value if p.previous else None,
               p.previous.at if p.previous else None, p.previous.commit_sha if p.previous else None, change(p))
           for k, p in latest(log, lambda r: (r.measure, r.key)).items()}
    assert got == expected and len(got) == 6, "latest is each key's newest row and the one before it, ties by position"
    assert latest([], lambda r: r.key) == {}
    only = latest(log[:1], lambda r: r.key)[log[0].key]
    assert only.previous is None and change(only) is None


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
