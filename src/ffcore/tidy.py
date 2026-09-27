
from __future__ import annotations

import csv
import io
import os
import re
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import NamedTuple

from ffcore.parse import money, pct100
from ffcore.text import norm

__all__ = ["ROOT", "TIDY", "SEASON", "DECISIONS", "REPORTS", "MADRID",
           "TABLES", "Table", "current", "history", "age_hours", "set_now",
           "input_path", "read_csv", "write_csv", "append_csv", "widen_csv",
           "log_row", "csv_string", "snapshot_stamp", "ledger_stamp",
           "Market", "Valuation", "row_key", "run_now", "load_crosswalk",
           "load_players", "read_ledger", "LEDGER", "load_deadline",
           "LINEUP_SOURCE", "kickoff_stamp", "MATCH_LEN",
           "minutes_played", "market_routes", "pending", "LISTED_SELLER",
           "lock_order", "JornadaClock", "shown", "table_stats",
           "load_perjornada", "clock", "clock_history", "jornada_of_match"]

ROOT = Path(os.environ.get("FF_ROOT", "./data"))
TIDY = ROOT / "tidy"
SEASON = ROOT / "season"
DECISIONS = ROOT / "decisions"
REPORTS = Path(os.environ.get("LFG_REPORTS", "reports"))




def _madrid():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("Europe/Madrid")
    except Exception:                                    # pragma: no cover
        return timezone(timedelta(hours=2))


MADRID = _madrid()


def input_path(name: str) -> Path:
    p = Path("inputs") / name
    return p if p.exists() else Path(name)


_READ_CACHE: dict[str, tuple] = {}


def _forget(path) -> None:
    _READ_CACHE.pop(str(Path(path)), None)


def _mtime_cached(path, cache: dict, key, build, *args):
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
    rows = _mtime_cached(path, _READ_CACHE, str(Path(path)), _parse_csv, path)
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


def load_deadline(with_source: bool = False):
    when = clock().next_deadline(run_now())
    return (when, "calendar" if when else "none") if with_source else when


@lru_cache(maxsize=4096)
def _digits_to_dt(s: str, tz):
    digits = re.sub(r"\D", "", s or "")
    if len(digits) < 8:
        return None
    try:
        return datetime(
            int(digits[:4]), int(digits[4:6]), int(digits[6:8]),
            int(digits[8:10]) if len(digits) >= 10 else 0,
            int(digits[10:12]) if len(digits) >= 12 else 0,
            tzinfo=tz)
    except ValueError:
        return None


def snapshot_stamp(s: str):
    return _digits_to_dt(s, timezone.utc)


def ledger_stamp(s: str):
    local = _digits_to_dt(s, MADRID)
    return local.astimezone(timezone.utc) if local else None


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
    return [r for r in read_csv(TIDY / f"{name}.csv")
            if r.get("observed_at", "") <= cut
            and (not source or r.get("source") == source)]


def _closed_days(name: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for r in read_csv(TIDY / f"{name}.csv"):
        stamp = r.get("observed_at", "")
        out[stamp[:10]] = max(out.get(stamp[:10], ""), stamp)
    return out


def _current_rows(name: str, cut: str) -> tuple:
    spec = TABLES[name]
    rows = sorted((r for r in read_csv(TIDY / f"{name}.csv")
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
    rows = _mtime_cached(TIDY / f"{name}.csv", _CURRENT_CACHE, (name, cut),
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


_NOW: list = []


def run_now() -> datetime:
    if not _NOW:
        pinned = os.environ.get("LFG_NOW", "").strip()
        _NOW.append(snapshot_stamp(pinned) if pinned
                    else datetime.now(timezone.utc))
    return _NOW[0]


def set_now(when: datetime | None) -> None:
    _NOW[:] = [when] if when is not None else []
    for cache in (_CURRENT_CACHE, _CLOCK, _CLOCK_HISTORY, _JORNADA_OF_MATCH):
        cache.clear()


def shown(t=None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    when = run_now() if t is None else t
    return when.astimezone(MADRID).strftime(fmt + " %Z")


LINEUP_SOURCE = "futbolfantasy"


def load_perjornada() -> list[dict]:
    files = sorted((SEASON / "live").glob("perjornada_*.csv"))
    cut = _cut()
    return [r for r in read_csv(files[-1]) if r.get("to_stamp", "") <= cut
            ] if files else []


def kickoff_stamp(s: str):
    try:
        when = datetime.fromisoformat((s or "").strip())
    except ValueError:
        return None
    return (when.replace(tzinfo=timezone.utc) if when.tzinfo is None
            else when.astimezone(timezone.utc))


_XW_CACHE: dict = {}


def load_crosswalk():
    from ffcore.crosswalk import Crosswalk
    path = TIDY / "players.csv"
    return _mtime_cached(path, _XW_CACHE, "xw", Crosswalk.read, path)


MATCH_LEN = 90.0


def minutes_played(role: str, raw_minute, match_len: float = MATCH_LEN) -> float:
    raw = (raw_minute or "").strip()
    if role == "starter":
        mins = float(raw) if raw else match_len
    elif role == "sub":
        mins = (match_len - float(raw)) if raw else 0.0
    else:
        return 0.0
    return max(0.0, mins)


def lock_order(locks: dict[int, datetime]) -> list[int]:
    return [j for j, _ in sorted(locks.items(), key=lambda kv: kv[1])]


class JornadaClock:

    def __init__(self, matches: list[dict]):
        latest: dict[tuple, tuple[int, datetime]] = {}
        for m in sorted(matches, key=lambda r: r.get("observed_at", "")):
            when = kickoff_stamp(m.get("kickoff"))
            jor = m.get("jornada") or ""
            if when is not None and str(jor).isdigit():
                latest[(m.get("home"), m.get("away"))] = (int(jor), when)
        self.team_locks: dict[tuple[int, str], datetime] = {}
        for (home, away), (jor, when) in latest.items():
            for team in (home, away):
                key = (jor, team)
                if key not in self.team_locks or when < self.team_locks[key]:
                    self.team_locks[key] = when

        self.round_locks: dict[int, datetime] = {}
        for (jor, _team), when in self.team_locks.items():
            if jor not in self.round_locks or when < self.round_locks[jor]:
                self.round_locks[jor] = when

    def team_lock(self, jornada: int, team: str) -> datetime | None:
        return self.team_locks.get((jornada, team))

    def round_lock(self, jornada: int) -> datetime | None:
        return self.round_locks.get(jornada)

    @property
    def order(self) -> list[int]:
        return lock_order(self.round_locks)

    def next_deadline(self, now: datetime) -> datetime | None:
        ahead = [t for t in self.round_locks.values() if t > now]
        return min(ahead) if ahead else None


_CLOCK: list = []
_CLOCK_HISTORY: list = []


def clock() -> JornadaClock:
    if not _CLOCK:
        _CLOCK.append(JornadaClock(current("matches")))
    return _CLOCK[0]


def clock_history() -> JornadaClock:
    if not _CLOCK_HISTORY:
        rows = history("matches")
        dated = {(r.get("home"), r.get("away")) for r in rows if r.get("kickoff")}
        blind = {int(r["jornada"]) for r in rows
                 if r.get("score") and str(r.get("jornada")).isdigit()
                 and (r.get("home"), r.get("away")) not in dated}
        full = JornadaClock(rows)
        full.round_locks = {j: t for j, t in full.round_locks.items()
                            if j not in blind}
        _CLOCK_HISTORY.append(full)
    return _CLOCK_HISTORY[0]


_JORNADA_OF_MATCH: list = []


def jornada_of_match() -> dict[str, int]:
    if not _JORNADA_OF_MATCH:
        out: dict[str, int] = {}
        for m in current("matches"):
            mid = (m.get("match_id") or "").strip()
            if not mid or mid in out:
                continue
            try:
                out[mid] = int(m.get("jornada"))
            except (TypeError, ValueError):
                continue
        _JORNADA_OF_MATCH.append(out)
    return _JORNADA_OF_MATCH[0]


MARKET_FIELDS = [("team", "team", None), ("club", "club", None),
                 ("pos", "position", None),
                 ("value", "value", money), ("delta_1d", "delta_1d", money)]
XI_FIELDS = [("club", "team_slug", None), ("start", "start_pct", pct100),
             ("status", "status", None)]


def _merge(players: dict, rows: list[dict], key_of, name_col: str,
           fields) -> dict:
    for r in rows:
        key = key_of(r)
        if not key:
            continue
        rec = players.setdefault(key, {})
        rec.setdefault("name", (r.get(name_col) or "").strip())
        for field, col, parse in fields:
            raw = r.get(col)
            if field in rec or raw in (None, ""):
                continue
            val = parse(raw) if parse else str(raw).strip()
            if val is not None:
                rec[field] = val
    return players


def load_players() -> dict[str, dict]:
    market, xi = current("market"), current("lineups", LINEUP_SOURCE)
    if not market and not xi:
        raise SystemExit("no rows in %s — run `ingest.py parse` first" % TIDY)
    xw = load_crosswalk()
    players = _merge({}, market, row_key, "name", MARKET_FIELDS)
    return _merge(players, xi, xw.key_of if xw else (lambda r: None),
                  "player_name", XI_FIELDS)


LEDGER = TIDY / "transactions.csv"


def read_ledger(path=LEDGER) -> list[dict]:
    return sorted((dict(r) for r in read_csv(path)
                   if (r.get("player") or "").strip()),
                  key=lambda r: r.get("date") or "")


class Valuation(NamedTuple):
    value: float
    observed_at: str
    lag_h: float
    name: str


def row_key(row: dict) -> str:
    return (row.get("ff_id") or "").strip() or norm(row.get("name"))


class Market:

    def __init__(self, rows: list[dict]):
        self.rows = rows
        self._by_key: dict[str, list[tuple[datetime, dict, float | None]]] = {}
        for r in rows:
            key = row_key(r)
            when = snapshot_stamp(r.get("observed_at", ""))
            if key and when is not None:
                self._by_key.setdefault(key, []).append(
                    (when, r, money(r.get("value"))))
        for hist in self._by_key.values():
            hist.sort(key=lambda t: t[0])

    def __len__(self) -> int:
        return len(self._by_key)

    def latest(self) -> dict[str, dict]:
        return {k: hist[-1][1] for k, hist in self._by_key.items()}

    def at(self, key, when: datetime | None) -> Valuation | None:
        hist = self._by_key.get(key)
        if not hist or when is None:
            return None
        prior = [h for h in hist if h[0] <= when]
        t, r, val = prior[-1] if prior else hist[0]
        if val is None:
            return None
        return Valuation(val, r.get("observed_at", ""),
                         (when - t).total_seconds() / 3600.0,
                         r.get("name", key))

    def series(self, key) -> list[tuple[datetime, float]]:
        return [(t, v) for t, _r, v in self._by_key.get(key, ()) if v is not None]

    def drift(self, key, since: datetime | None, days: float):
        base = self.at(key, since)
        if not base:
            return None
        target = since + timedelta(days=days)
        later = [(t, v) for t, v in self.series(key) if t >= target]
        if not later:
            return None
        _, v = later[0]
        return v - base.value, (v / base.value - 1) * 100.0 if base.value else None


LISTED_SELLER = "marketPlayerTeam"


def market_routes(mkt: list[dict]) -> tuple[dict[str, float], dict[str, str]]:
    price: dict[str, float] = {}
    route: dict[str, str] = {}
    for r in mkt:
        k = r["key"]
        if not k or not r.get("sale_price"):
            continue
        price[k] = float(r["sale_price"])
        route[k] = "listed" if r.get("seller") == LISTED_SELLER else "free"
    return price, route


def pending(rows, status_field: str, money_field: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for r in rows:
        if (r.get(status_field) or "") != "pending":
            continue
        amt = float(r.get(money_field) or 0)
        if not amt:
            continue
        k = r["key"]
        if k:
            out[k] = max(out.get(k, 0.0), amt)
    return out


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


def _selftest_crosswalk_cache() -> None:
    import tempfile
    from ffcore.crosswalk import PLAYER_COLS

    global TIDY
    real_tidy = TIDY
    with tempfile.TemporaryDirectory() as tmp:
        TIDY = Path(tmp)
        try:
            assert load_crosswalk() is None
            write_csv(TIDY / "players.csv",
                     [{"player_id": "a", "name": "A", "club_id": "c"}],
                     PLAYER_COLS)
            xw1 = load_crosswalk()
            assert xw1.players["a"].name == "A", xw1.players
            assert load_crosswalk() is xw1

            write_csv(TIDY / "players.csv",
                     [{"player_id": "a", "name": "Renamed", "club_id": "c"}],
                     PLAYER_COLS)
            xw2 = load_crosswalk()
            assert xw2 is not xw1 and xw2.players["a"].name == "Renamed", \
                xw2.players
        finally:
            TIDY = real_tidy
            _XW_CACHE.clear()


def _selftest_new_loaders() -> None:
    import tempfile
    global TIDY
    real = TIDY
    TIDY = Path(tempfile.mkdtemp())
    a, b, later = "2026-08-01T0900Z", "2026-08-02T0900Z", "2026-08-03T0900Z"
    try:
        write_csv(TIDY / "lineups.csv", [
            {"observed_at": a, "source": "futbolfantasy", "player_name": "Ane"},
            {"observed_at": b, "source": "analitica", "player_name": "Ane"},
            {"observed_at": b, "source": "futbolfantasy", "player_name": "Bo"},
            {"observed_at": a, "source": "analitica", "player_name": "Cai"},
            {"observed_at": later, "source": "futbolfantasy",
             "player_name": "Dan"}])
        write_csv(TIDY / "api_activity.csv", [
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
        TIDY = real
        set_now(None)

    c1 = clock()
    c2 = clock()
    assert c1 is c2, "clock() must return the SAME object on a second call"

    _full = clock_history()
    assert _full is clock_history(), "clock_history() must be memoized too"
    assert set(c1.team_locks) <= set(_full.team_locks)
    assert isinstance(c1, JornadaClock)

    j1 = jornada_of_match()
    j2 = jornada_of_match()
    assert j1 is j2, "jornada_of_match() must be memoized"
    expect: dict[str, int] = {}
    for m in current("matches"):
        mid = (m.get("match_id") or "").strip()
        if mid and mid not in expect:
            try:
                expect[mid] = int(m.get("jornada"))
            except (TypeError, ValueError):
                continue
    assert j1 == expect, "jornada_of_match() must be first-write-wins"



def _selftest() -> None:
    _selftest_cache()
    _selftest_new_loaders()
    _selftest_crosswalk_cache()
    assert run_now() is run_now()
    assert run_now().tzinfo is timezone.utc
    _NOW.clear()
    os.environ["LFG_NOW"] = "2026-08-20T0900Z"
    assert run_now() == snapshot_stamp("2026-08-20T0900Z")
    del os.environ["LFG_NOW"]
    _NOW.clear()
    assert run_now().year >= 2026

    tw = [{"ff_id": "867", "name": "Álvaro García", "team": "Rayo",
           "value": "20233300", "observed_at": "2026-08-19T1639Z"},
          {"ff_id": "12993", "name": "Álvaro García", "team": "Villarreal",
           "value": "501929", "observed_at": "2026-08-19T1639Z"},
          {"ff_id": "5001", "name": "Pepelu", "team": "Valencia",
           "value": "7669774", "observed_at": "2026-08-19T1639Z"}]
    tm = Market(tw + [dict(tw[0], value="21000000",
                           observed_at="2026-08-20T1639Z")])
    assert len(tm) == 3, len(tm)
    assert sorted(tm.latest()) == ["12993", "5001", "867"], sorted(tm.latest())
    for key, when, want in [("867", "2026-08-19T1700Z", 20233300.0),
                            ("867", "2026-08-20T1700Z", 21000000.0),
                            ("867", "2026-08-01T0000Z", 20233300.0),
                            ("12993", "2026-08-19T1700Z", 501929.0)]:
        assert tm.at(key, snapshot_stamp(when)).value == want, (key, when)
    assert tm.at("Álvaro García", snapshot_stamp("2026-08-19T1700Z")) is None
    assert [v for _t, v in tm.series("867")] == [20233300.0, 21000000.0]
    assert tm.drift("867", snapshot_stamp("2026-08-19T1639Z"), 1)[0] == 766700.0
    assert row_key({"name": "Iker Muñoz"}) == norm("Iker Munoz")

    mkt = [{"name": "Ane Aldea", "team": "Alavés", "position": "defensa",
            "value": "2.050.000", "delta_1d": "-12.000"},
           {"name": "Bo Bidal", "team": "Betis", "position": "delantero",
            "value": "", "delta_1d": "0"}]
    xi = [{"player_name": "Ane Aldea", "team_slug": "alaves",
           "start_pct": "0.72", "status": "doubt"},
          {"player_name": "Cai Coro", "team_slug": "celta",
           "start_pct": "85", "status": "ok"}]
    p = _merge(_merge({}, mkt, row_key, "name", MARKET_FIELDS), xi,
               lambda r: norm(r["player_name"]), "player_name", XI_FIELDS)

    a = p["ane aldea"]
    assert a["value"] == 2050000.0 and a["delta_1d"] == -12000.0
    assert a["pos"] == "defensa" and a["start"] == 72.0 and a["status"] == "doubt"
    assert a["team"] == "Alavés" and a["name"] == "Ane Aldea"

    assert "value" not in p["bo bidal"] and p["bo bidal"]["delta_1d"] == 0.0
    assert "start" not in p["bo bidal"]

    c = p["cai coro"]
    assert c["name"] == "Cai Coro" and c["club"] == "celta" and c["start"] == 85.0
    assert "value" not in c

    assert kickoff_stamp("2026-08-15T19:30:00+00:00") == datetime(
        2026, 8, 15, 19, 30, tzinfo=timezone.utc)
    assert kickoff_stamp("2026-08-15T21:30:00+02:00") == datetime(
        2026, 8, 15, 19, 30, tzinfo=timezone.utc)
    assert kickoff_stamp("2026-08-15T19:30:00") == datetime(
        2026, 8, 15, 19, 30, tzinfo=timezone.utc)
    assert kickoff_stamp("") is None and kickoff_stamp("soon") is None

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

    assert minutes_played("starter", "") == 90.0
    assert minutes_played("starter", "64") == 64.0
    assert minutes_played("sub", "") == 0.0
    assert minutes_played("sub", "64") == 26.0
    assert minutes_played("coach", "") == 0.0
    assert minutes_played("starter", "0") == 0.0

    mkt_rows = [
        {"player_name": "Free Agent", "sale_price": "5000000",
         "seller": "marketPlayerLeague", "bids": "0"},
        {"player_name": "Listed Rival", "sale_price": "8000000",
         "seller": "marketPlayerTeam", "bids": "2"},
        {"player_name": "Not Priced", "sale_price": "",
         "seller": "marketPlayerLeague"},
        {"player_name": "Unjoinable", "sale_price": "1000000",
         "seller": "marketPlayerTeam"},
    ]
    for r, k in zip(mkt_rows, ["free_agent", "listed_rival", "not_priced",
                               None]):
        r["key"] = k
    price, route = market_routes(mkt_rows)
    assert price == {"free_agent": 5000000.0, "listed_rival": 8000000.0}, price
    assert route == {"free_agent": "free", "listed_rival": "listed"}, route
    assert "not_priced" not in route and "not_priced" not in price
    unknown_seller = [{"player_name": "Free Agent", "sale_price": "1",
                       "seller": "something_new", "key": "free_agent"}]
    _, r2 = market_routes(unknown_seller)
    assert r2 == {"free_agent": "free"}, r2

    summer = datetime(2026, 9, 18, 16, 40, tzinfo=timezone.utc)
    winter = datetime(2026, 12, 18, 16, 40, tzinfo=timezone.utc)
    assert shown(summer) == "2026-09-18 18:40 CEST", shown(summer)
    assert shown(winter) == "2026-12-18 17:40 CET", shown(winter)
    assert shown(summer, "%d %b %H:%M") == "18 Sep 18:40 CEST"
    assert snapshot_stamp("2026-09-18T1640Z") == summer, \
        snapshot_stamp("2026-09-18T1640Z")

    mkt_bids = [
        {"player_name": "A", "bid_status": "pending", "bid_money": "5600000"},
        {"player_name": "B", "bid_status": "pending", "bid_money": "6795815"},
        {"player_name": "C", "bid_status": "", "bid_money": ""},
        {"player_name": "D", "bid_status": "accepted", "bid_money": "2000000"},
        {"player_name": "E", "bid_status": "pending", "bid_money": ""},
        {"player_name": "A", "bid_status": "pending", "bid_money": "5100000"},
        {"player_name": "", "bid_status": "pending", "bid_money": "9000000"},
    ]
    sent = pending([dict(r, key=r["player_name"]) for r in mkt_bids],
                   "bid_status", "bid_money")
    assert sent == {"A": 5600000.0, "B": 6795815.0}, sent
    assert pending([], "bid_status", "bid_money") == {}
    offers = [{"key": k, "status": st, "money": m} for k, st, m in [
        ("me_a", "pending", "6795815"), ("me_a", "pending", "1000000"),
        ("me_b", "accepted", "9000000"), ("me_b", "", ""),
        (None, "pending", "1")]]
    assert pending(offers, "status", "money") == {"me_a": 6795815.0}

    jl_matches = [
        {"observed_at": "2026-08-10T0900Z", "jornada": "1", "home": "alaves",
         "away": "getafe", "kickoff": "2026-08-14T19:30:00+00:00"},
        {"observed_at": "2026-08-12T0900Z", "jornada": "1", "home": "alaves",
         "away": "getafe", "kickoff": "2026-08-15T19:30:00+00:00"},
        {"observed_at": "2026-08-12T0900Z", "jornada": "1",
         "home": "espanyol", "away": "levante",
         "kickoff": "2026-08-16T17:00:00+00:00"},
        {"observed_at": "2026-08-12T0900Z", "jornada": "2",
         "home": "rayo-vallecano", "away": "alaves", "kickoff": ""}]
    clock = JornadaClock(jl_matches)
    jl = clock.round_locks
    assert list(jl) == [1] and jl[1].day == 15, jl
    assert 2 not in jl
    assert clock.round_lock(1) == jl[1] and clock.round_lock(2) is None
    assert clock.team_lock(1, "alaves") == jl[1]
    assert clock.team_lock(1, "espanyol") > jl[1]
    assert clock.next_deadline(
        datetime(2026, 8, 15, 20, tzinfo=timezone.utc)) is None
    assert clock.next_deadline(
        datetime(2026, 8, 15, 18, tzinfo=timezone.utc)) == jl[1]
    assert lock_order({3: datetime(2026, 9, 3, tzinfo=timezone.utc),
                       1: datetime(2026, 8, 15, tzinfo=timezone.utc),
                       2: datetime(2026, 8, 20, tzinfo=timezone.utc)}) \
        == [1, 2, 3]
    assert clock.order == [1]

    print("ffcore.tidy self-test OK")


if __name__ == "__main__":
    _selftest()
