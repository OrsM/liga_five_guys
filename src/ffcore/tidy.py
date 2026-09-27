from __future__ import annotations

import csv
import io
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from types import MappingProxyType
from typing import NamedTuple

from ffcore.clock import on_reset, run_now
from ffcore.parse import snapshot_stamp

__all__ = ["ROOT", "TIDY", "SEASON", "DECISIONS", "REPORTS", "TABLES", "Table",
           "current", "history", "age_hours", "table_path", "tables_in",
           "input_path", "read_csv", "write_csv", "append_csv", "widen_csv",
           "log_row", "csv_string", "mtime_cached", "table_stats",
           "LINEUP_SOURCE"]

ROOT = Path(os.environ.get("FF_ROOT", "./data"))
TIDY = ROOT / "tidy"
SEASON = ROOT / "season"
DECISIONS = ROOT / "decisions"
REPORTS = Path(os.environ.get("LFG_REPORTS", "reports"))


def table_path(name: str) -> Path:
    return TIDY / f"{name}.csv"


@contextmanager
def tables_in(path: Path):
    global TIDY
    real = TIDY
    TIDY = Path(path)
    try:
        yield TIDY
    finally:
        TIDY = real


def input_path(name: str) -> Path:
    p = Path("inputs") / name
    return p if p.exists() else Path(name)


_READ_CACHE: dict[str, tuple] = {}


def _forget(path) -> None:
    _READ_CACHE.pop(str(Path(path)), None)


def mtime_cached(path, cache: dict, key, build, *args):
    path = Path(path)
    try:
        st = path.stat()
    except OSError:
        return None
    stamp = (st.st_mtime_ns, st.st_size)
    hit = cache.get(key)
    if hit is None or hit[0] != stamp:
        hit = (stamp, build(*args))
        cache[key] = hit
    return hit[1]


def _parse_csv(path) -> list[dict]:
    with Path(path).open(encoding="utf-8") as fh:
        r = csv.reader(fh)
        fieldnames = next(r, [])
        return [dict(zip(fieldnames, map(sys.intern, row))) for row in r if row]


def read_csv(path) -> list[dict]:
    rows = mtime_cached(path, _READ_CACHE, str(Path(path)), _parse_csv, path)
    return [MappingProxyType(r) for r in (rows or [])]


@contextmanager
def _csv_dictwriter(path, fieldnames, mode, **writer_kw):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open(mode, newline="", encoding="utf-8") as fh:
        yield csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore",
                             lineterminator="\n", **writer_kw)


def csv_string(rows, fieldnames) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fieldnames, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def write_csv(path, rows, fieldnames=None) -> None:
    path = Path(path)
    _forget(path)
    if not rows:
        return
    fieldnames = fieldnames or list(rows[0])
    with _csv_dictwriter(path, fieldnames, "w") as w:
        w.writeheader()
        w.writerows(rows)


def widen_csv(path, fieldnames) -> bool:
    path = Path(path)
    if not path.exists():
        return False
    with path.open(encoding="utf-8") as fh:
        r = csv.DictReader(fh)
        have = list(r.fieldnames or [])
        if not [c for c in fieldnames if c not in have]:
            return False
        rows = list(r)
    cols = have + [c for c in fieldnames if c not in have]
    write_csv(path, [{c: row.get(c, "") for c in cols} for row in rows], cols)
    return True


def log_row(path, row: dict) -> None:
    append_csv(Path(path), [row], list(row))


def append_csv(path, rows, fieldnames=None) -> None:
    path = Path(path)
    _forget(path)
    if not rows:
        return
    fieldnames = fieldnames or list(rows[0])
    fresh = not path.exists()
    if not fresh:
        with path.open(encoding="utf-8") as fh:
            fieldnames = list(csv.DictReader(fh).fieldnames or fieldnames)
    with _csv_dictwriter(path, fieldnames, "a", restval="") as w:
        if fresh:
            w.writeheader()
        w.writerows(rows)


class Table(NamedTuple):
    snapshot: bool
    key: tuple = ()
    store: str = ""


TABLES: dict[str, Table] = {
    "market": Table(True, ("ff_id",), "daily"),
    "lineups": Table(True, ("source", "team_slug", "player_slug"), "daily"),
    "matches": Table(False, ("match_id",), "once"),
    "points": Table(False, ("season", "ff_id"), "once"),
    "api_teams": Table(True),
    "api_standings": Table(True),
    "api_market": Table(True),
    "api_offers": Table(True),
    "api_lineup": Table(True),
    "api_leagues": Table(True),
    "api_players_all": Table(False, ("player_id",), "daily"),
    "results_history": Table(False, ("season", "date", "home_name",
                                     "away_name"), "once"),
    "starters": Table(False, ("match_id", "team_slug", "player_slug"), "once"),
    "api_activity": Table(False, ("activity_id",), "once"),
}


def _cut() -> str:
    return run_now().strftime("%Y-%m-%dT%H%MZ")


def history(name: str, source: str = "") -> list:
    cut = _cut()
    return [r for r in read_csv(table_path(name))
            if r.get("observed_at", "") <= cut
            and (not source or r.get("source") == source)]


def _closed_days(name: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for r in read_csv(table_path(name)):
        stamp = r.get("observed_at", "")
        out[stamp[:10]] = max(out.get(stamp[:10], ""), stamp)
    return out


def _current_rows(name: str, cut: str) -> tuple:
    spec = TABLES[name]
    rows = sorted((r for r in read_csv(table_path(name))
                   if r.get("observed_at", "") <= cut),
                  key=lambda r: r.get("observed_at", ""))
    if spec.snapshot:
        closed = _closed_days(name) if spec.store == "daily" else {}
        last: dict[str, str] = {}
        for r in rows:
            stamp = r.get("observed_at", "")
            if closed.get(stamp[:10], stamp) <= cut:
                last[r.get("source", "")] = stamp
        return tuple(r for r in rows
                     if r.get("observed_at", "") == last.get(r.get("source", "")))
    latest = {tuple(r.get(c, "") for c in spec.key): r for r in rows}
    return tuple(r for k, r in latest.items() if all(k))


_CURRENT_CACHE: dict = {}


def current(name: str, source: str = "") -> list[dict]:
    cut = _cut()
    rows = mtime_cached(table_path(name), _CURRENT_CACHE, (name, cut),
                         _current_rows, name, cut) or ()
    return [dict(r) for r in rows if not source or r.get("source") == source]


def age_hours(name: str, now=None) -> float | None:
    stamps = [r.get("observed_at", "") for r in current(name)]
    when = snapshot_stamp(max(stamps)) if stamps else None
    return None if when is None else ((now or run_now()) - when).total_seconds() / 3600


def table_stats(path, col: str = "observed_at") -> tuple[int, str]:
    path = Path(path)
    try:
        st = path.stat()
    except OSError:
        return 0, ""
    hit = _READ_CACHE.get(str(path))
    if hit is not None and hit[0] == (st.st_mtime_ns, st.st_size):
        rows = hit[1]
        return len(rows), max((r.get(col, "") for r in rows), default="")
    n, best = 0, ""
    try:
        with path.open(encoding="utf-8") as fh:
            r = csv.reader(fh)
            try:
                fieldnames = next(r)
            except StopIteration:
                return 0, ""
            try:
                i = fieldnames.index(col)
            except ValueError:
                i = -1
            for raw in r:
                if not raw:
                    continue
                n += 1
                if i >= 0 and i < len(raw) and raw[i] > best:
                    best = raw[i]
    except OSError:
        return 0, ""
    return n, best


LINEUP_SOURCE = "futbolfantasy"


on_reset(_CURRENT_CACHE.clear)


def _selftest_cache() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "t.csv"
        write_csv(p, [{"a": "1", "b": "x"}, {"a": "2", "b": "y"}])
        first = read_csv(p)
        assert [r["a"] for r in first] == ["1", "2"], first

        try:
            first[0]["a"] = "999"
            raise AssertionError("read_csv rows must be read-only")
        except TypeError:
            pass
        first.append({"a": "3", "b": "z"})
        assert [r["a"] for r in read_csv(p)] == ["1", "2"], read_csv(p)

        write_csv(p, [{"a": "7", "b": "q"}])
        assert [r["a"] for r in read_csv(p)] == ["7"], read_csv(p)

        append_csv(p, [{"a": "8", "b": "r"}])
        assert [r["a"] for r in read_csv(p)] == ["7", "8"], read_csv(p)

        assert read_csv(Path(tmp) / "nope.csv") == []


def _selftest_tables() -> None:
    import tempfile

    from ffcore.clock import set_now

    a, b, later = "2026-08-01T0900Z", "2026-08-02T0900Z", "2026-08-03T0900Z"
    with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)) as tidy:
        try:
            write_csv(tidy / "lineups.csv", [
                {"observed_at": a, "source": "futbolfantasy", "player_name": "Ane"},
                {"observed_at": b, "source": "analitica", "player_name": "Ane"},
                {"observed_at": b, "source": "futbolfantasy", "player_name": "Bo"},
                {"observed_at": a, "source": "analitica", "player_name": "Cai"},
                {"observed_at": later, "source": "futbolfantasy",
                 "player_name": "Dan"}])
            write_csv(tidy / "api_activity.csv", [
                {"observed_at": a, "activity_id": "1", "value": "0"},
                {"observed_at": b, "activity_id": "1", "value": "1"},
                {"observed_at": a, "activity_id": "2", "value": "5"}])
            set_now(snapshot_stamp("2026-08-02T1200Z"))
            for name, source, every, now in [
                    ("lineups", "", 4, ["Ane", "Bo"]),
                    ("lineups", "analitica", 2, ["Ane"]),
                    ("lineups", "futbolfantasy", 2, ["Bo"]),
                    ("lineups", "nobody", 0, [])]:
                assert len(history(name, source)) == every, source
                assert sorted(r["player_name"] for r in current(name, source)) \
                    == now, source
            stats = {(r["activity_id"], r["value"]) for r in current("api_activity")}
            assert stats == {("1", "1"), ("2", "5")}, stats
            assert current("market") == [] and history("market") == []
            assert age_hours("market") is None
            assert abs(age_hours("lineups") - 3.0) < 1e-9
            set_now(snapshot_stamp("2026-08-01T1000Z"))
            assert stats != {(r["activity_id"], r["value"])
                             for r in current("api_activity")}
            set_now(snapshot_stamp("2026-08-04T0000Z"))
            assert [r["player_name"] for r in current("lineups", "futbolfantasy")] \
                == ["Dan"]
        finally:
            set_now(None)


def _selftest() -> None:
    _selftest_cache()
    _selftest_tables()

    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        log = Path(tmp) / "log.csv"
        append_csv(log, [{"a": "1", "b": "2"}], ["a", "b"])

        append_csv(log, [{"a": "3", "b": "4"}], ["b", "a"])
        assert read_csv(log)[1] == {"a": "3", "b": "4"}

        append_csv(log, [{"a": "5", "b": "6", "c": "7"}], ["a", "b", "c"])
        assert read_csv(log)[2] == {"a": "5", "b": "6"}

        assert widen_csv(log, ["a", "b", "c"]) is True
        rows = read_csv(log)
        assert [r["c"] for r in rows] == ["", "", ""]
        assert [r["a"] for r in rows] == ["1", "3", "5"]
        assert widen_csv(log, ["a", "b", "c"]) is False
        assert widen_csv(log, ["a"]) is False
        assert "b" in read_csv(log)[0]
        append_csv(log, [{"a": "8", "b": "9", "c": "10"}], ["a", "b", "c"])
        assert read_csv(log)[3]["c"] == "10"
        assert widen_csv(Path(tmp) / "nope.csv", ["a"]) is False
    print("ffcore.tidy self-test OK")


if __name__ == "__main__":
    _selftest()
