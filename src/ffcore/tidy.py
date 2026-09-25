
from __future__ import annotations

import csv
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import NamedTuple

from ffcore.parse import money, pct100
from ffcore.text import index_by, norm, resolve

__all__ = ["ROOT", "TIDY", "SEASON", "DECISIONS", "REPORTS", "PARTS", "MADRID",
           "input_path", "read_csv", "write_csv", "append_csv", "widen_csv", "log_row",
           "write_lines", "snapshot_stamp", "ledger_stamp", "latest_only",
           "latest_per_key", "snapshots",
           "Market", "Valuation", "VALUE_TOLERANCE", "price_agrees",
           "load_market", "load_market_frozen", "load_lineups",
           "shared_names", "row_key", "run_now", "load_crosswalk",
           "load_players", "read_ledger", "LEDGER", "load_deadline", "LINEUP_SOURCE",
           "pick_source", "load_fixtures", "next_kickoff", "kickoff_stamp",
           "load_elo", "load_odds", "load_results_history", "load_understat_players",
           "MATCH_LEN", "minutes_played", "fresh_only", "DAILY_FRESH_DAYS",
           "EVERY_RUN_FRESH_DAYS", "stale_feeds",
           "GATED_API", "age_phrase", "last_api_standings",
           "load_api", "market_routes", "pending_sent",
           "pending_received", "LISTED_SELLER", "team_slug_of", "lock_order",
           "JornadaClock", "shown", "newest", "table_stats", "load_matches", "load_matches_history",
           "load_starters", "load_perjornada", "load_api_stats", "clock", "clock_history",
           "jornada_of_match"]

ROOT = Path(os.environ.get("FF_ROOT", "./data"))
TIDY = ROOT / "tidy"
SEASON = ROOT / "season"
DECISIONS = ROOT / "decisions"
REPORTS = Path(os.environ.get("LFG_REPORTS", "reports"))

PARTS = Path(os.environ.get("LFG_PARTS", ".runtime/parts"))

ALERTS = Path(os.environ.get("LFG_ALERTS", ".runtime/alerts.md"))


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


def read_csv(path) -> list[dict]:
    path = Path(path)
    try:
        st = path.stat()
    except OSError:
        return []
    hit = _READ_CACHE.get(str(path))
    if hit is None or hit[0] != (st.st_mtime_ns, st.st_size):
        with path.open(encoding="utf-8") as fh:
            r = csv.reader(fh)
            try:
                fieldnames = next(r)
            except StopIteration:
                fieldnames = []
            intern = sys.intern
            rows = [dict(zip(fieldnames, map(intern, row)))
                    for row in r if row]
        hit = ((st.st_mtime_ns, st.st_size), rows)
        _READ_CACHE[str(path)] = hit
    # READ-ONLY VIEWS, NOT COPIES. This used to rebuild every row with
    # `[dict(r) for r in hit[1]]` so a caller could mutate its result without
    # corrupting the cache. Nothing in the repo ever did: flipping this to a
    # view and running all 32 suites turned up exactly one mutator -- the
    # selftest written to prove the copy worked. The copy cost 188,184 fresh
    # dicts per read of starters.csv, seven times a run.
    # A caller that does need to mutate should build its own dict from a row;
    # it now fails loudly at the assignment rather than silently paying for
    # everyone else's safety.
    return [MappingProxyType(r) for r in hit[1]]


def read_csv_frozen(path) -> list:
    path = Path(path)
    try:
        st = path.stat()
    except OSError:
        return []
    hit = _READ_CACHE.get(str(path))
    if hit is None or hit[0] != (st.st_mtime_ns, st.st_size):
        read_csv(path)
        hit = _READ_CACHE[str(path)]
    return [MappingProxyType(r) for r in hit[1]]


def write_csv(path, rows, fieldnames=None) -> None:
    path = Path(path)
    _forget(path)
    if not rows:
        return
    fieldnames = fieldnames or list(rows[0])
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore",
                           lineterminator="\n")
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
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore",
                           restval="", lineterminator="\n")
        if fresh:
            w.writeheader()
        w.writerows(rows)


def load_deadline(with_source: bool = False):
    when = clock().next_deadline(run_now())
    return (when, "fixtures" if when else "none") if with_source else when


def write_lines(path, lines) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    print("wrote %s" % path)



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



def latest_only(rows: list[dict]) -> list[dict]:
    if not rows:
        return []
    newest = max(r.get("observed_at", "") for r in rows)
    return [r for r in rows if r.get("observed_at") == newest]


def latest_per_key(rows: list[dict], key_fn) -> list[dict]:
    best: dict = {}
    for r in rows:
        k = key_fn(r)
        if k is None:
            continue
        stamp = r.get("observed_at", "")
        prior = best.get(k)
        if prior is None or stamp >= prior.get("observed_at", ""):
            best[k] = r
    return list(best.values())


def latest_snapshot(path, keep=None) -> list[dict]:
    path = Path(path)
    try:
        fh = path.open(encoding="utf-8")
    except OSError:
        return []
    with fh:
        r = csv.reader(fh)
        try:
            fieldnames = next(r)
        except StopIteration:
            fieldnames = []
        newest = ""
        kept: list[dict] = []
        for raw in r:
            if not raw:
                continue
            row = dict(zip(fieldnames, raw))
            if keep is not None and not keep(row):
                continue
            stamp = row.get("observed_at", "")
            if stamp > newest:
                newest, kept = stamp, [row]
            elif stamp == newest:
                kept.append(row)
        return kept


_LATEST_SNAPSHOT_CACHE: dict[tuple, tuple] = {}


def _cached_latest_snapshot(path, keep=None, cache_key=None) -> list[dict]:
    path = Path(path)
    try:
        st = path.stat()
    except OSError:
        return []
    stamp = (st.st_mtime_ns, st.st_size)
    key = (str(path), cache_key)
    hit = _LATEST_SNAPSHOT_CACHE.get(key)
    if hit is None or hit[0] != stamp:
        hit = (stamp, latest_snapshot(path, keep=keep))
        _LATEST_SNAPSHOT_CACHE[key] = hit
    return [dict(r) for r in hit[1]]


def table_stats(path, col: str = "observed_at") -> tuple[int, str]:
    """How many rows, and the newest value in one column -- WITHOUT keeping
    the table.

    The freshness table wants exactly this for every tidy file, and it was
    getting it with read_csv(): five history tables materialised and held
    for the life of the process, 276MB of cache, to print a row count and a
    timestamp. If a table is already cached because something genuinely
    needed its rows, that copy is used and nothing is read twice.
    """
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


def newest(name: str, keep=None, cache_key=None) -> list[dict]:
    """The newest snapshot of a tidy table, read in ONE PASS.

    latest_only(read_csv(x)) gets the same answer by materialising the whole
    history first, which for starters.csv is 226,012 rows and 61MB held for
    the life of the process to keep the ~900 that are current. The streaming
    reader underneath this was already here and already used by
    load_market_latest(); thirteen other call sites had each re-derived the
    expensive version by hand.
    """
    return _cached_latest_snapshot(TIDY / name, keep=keep, cache_key=cache_key)


DAILY_FRESH_DAYS = 1.05

EVERY_RUN_FRESH_DAYS = 0.6


_NOW: list = []


def run_now() -> datetime:
    if not _NOW:
        pinned = os.environ.get("LFG_NOW", "").strip()
        _NOW.append(snapshot_stamp(pinned) if pinned
                    else datetime.now(timezone.utc))
    return _NOW[0]


def shown(t=None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """A clock for a PERSON to read: Madrid, where the league plays and where
    the reader is, labelled with whichever offset is in force (CEST or CET)
    rather than assumed.

    DATA MUST NOT USE THIS, and the split is the whole point. observed_at,
    the dt= snapshot names, logged_at and decisions.json's generated_at are
    KEYS -- they join tables, order snapshots and name files. They stay UTC,
    where an hour never repeats itself and the October clock change cannot
    reorder a season or make two snapshots collide on one name. What changes
    here is only what gets printed for someone to read.
    """
    when = run_now() if t is None else t
    return when.astimezone(MADRID).strftime(fmt + " %Z")


def _age_days(stamp: str, now) -> float | None:
    when = snapshot_stamp(stamp)
    return None if when is None else (now - when).total_seconds() / 86400.0


def fresh_only(rows: list[dict], max_age_days: float, now=None) -> list[dict]:
    if not rows:
        return []
    age = _age_days(max(r.get("observed_at", "") for r in rows), now or run_now())
    return rows if age is not None and age <= max_age_days else []


def snapshots(rows: list[dict]) -> list[str]:
    return sorted({r.get("observed_at", "") for r in rows if r.get("observed_at")})


def load_market() -> list[dict]:
    return read_csv(TIDY / "market.csv")


def load_market_frozen() -> list:
    return read_csv_frozen(TIDY / "market.csv")


def load_market_latest() -> list[dict]:
    return _cached_latest_snapshot(TIDY / "market.csv")


LINEUP_SOURCE = "futbolfantasy"


_LINEUPS_CACHE: dict[tuple, tuple] = {}


def load_lineups(source: str = LINEUP_SOURCE) -> list[dict]:
    """All lineup rows for `source` (default) or every source (""). One
    build() call hits this 3x across sources -- each used to re-read the
    whole ~174k-row CSV; the raw parse is now cached per file version and
    `source` filters the cached, unfiltered read."""
    path = TIDY / "lineups.csv"
    try:
        st = path.stat()
    except OSError:
        return []
    key = (st.st_mtime_ns, st.st_size)
    hit = _LINEUPS_CACHE.get(key)
    if hit is None:
        hit = tuple(read_csv(path))
        _LINEUPS_CACHE[key] = hit
    return pick_source([dict(r) for r in hit], source)


def load_lineups_latest(source: str = LINEUP_SOURCE) -> list[dict]:
    keep = (lambda r: r.get("source") == source) if source else None
    return _cached_latest_snapshot(TIDY / "lineups.csv", keep=keep,
                                   cache_key=source)


def pick_source(rows: list[dict], source: str) -> list[dict]:
    return rows if not source else [r for r in rows
                                    if r.get("source") == source]



def load_matches() -> list[dict]:
    return newest("matches.csv")


def load_matches_history() -> list[dict]:
    return read_csv(TIDY / "matches.csv")


def load_starters() -> list[dict]:
    return newest("starters.csv")


def _api_stats_key(r: dict):
    return ((r.get("player_id") or "").strip(), (r.get("week") or "").strip(),
            (r.get("stat") or "").strip())


def load_api_stats() -> list[dict]:
    return latest_per_key(read_csv(TIDY / "api_stats.csv"), _api_stats_key)


def load_perjornada() -> list[dict]:
    files = sorted((SEASON / "live").glob("perjornada_*.csv"))
    return read_csv(files[-1]) if files else []



def kickoff_stamp(s: str):
    try:
        when = datetime.fromisoformat((s or "").strip())
    except ValueError:
        return None
    return (when.replace(tzinfo=timezone.utc) if when.tzinfo is None
            else when.astimezone(timezone.utc))


def load_elo(now=None) -> list[dict]:
    return fresh_only(newest("elo.csv"),
                      DAILY_FRESH_DAYS, now)


GATED_API = ("api_teams", "api_market", "api_standings",
             "api_lineup", "api_offers")


def age_phrase(days: float) -> str:
    def unit(n: float, word: str) -> str:
        return "%.0f %s%s" % (n, word, "" if round(n) == 1 else "s")

    if days >= 2:
        return unit(days, "day")
    if days * 24 >= 1:
        return unit(days * 24, "hour")
    return unit(max(1, round(days * 1440)), "minute")


def stale_feeds(now=None, names=GATED_API) -> dict[str, float]:
    now = now or run_now()
    out = {}
    for name in names:
        _n, newest_stamp = table_stats(TIDY / f"{name}.csv")
        age = _age_days(newest_stamp, now)
        if age is not None and age > EVERY_RUN_FRESH_DAYS:
            out[name] = age
    return out


def load_api(name: str, now=None) -> list[dict]:
    return fresh_only(newest("api_%s.csv" % name), EVERY_RUN_FRESH_DAYS, now)


def load_api_team_history() -> list[dict]:
    return read_csv(TIDY / "api_teams.csv")


def _activity_order(r: dict):
    raw = (r.get("activity_id") or "").strip()
    return (r.get("at") or "", int(raw) if raw.isdigit() else 0)


def load_api_activity() -> list[dict]:
    return sorted(read_csv(TIDY / "api_activity.csv"), key=_activity_order)


def last_api_standings() -> list[dict]:
    return newest("api_standings.csv")



def load_api_players() -> dict[str, str]:
    out = {}
    for r in read_csv(TIDY / "api_players.csv"):
        if r.get("player_id") and r.get("player_name"):
            out[r["player_id"]] = r["player_name"]
    return out


_XW_CACHE: dict = {}


def load_crosswalk():
    """Cached like read_csv(): keyed on both files' (mtime, size), so every
    caller shares one parse per real change instead of each re-reading
    players.csv/clubs.csv by hand -- the pattern that had four independent
    copies (methodology.py's own _XW_CACHE among them) before this."""
    from ffcore.crosswalk import Crosswalk
    path = TIDY / "players.csv"
    clubs = TIDY / "clubs.csv"
    try:
        key = (path.stat().st_mtime_ns, path.stat().st_size,
              clubs.stat().st_mtime_ns, clubs.stat().st_size)
    except OSError:
        return None
    hit = _XW_CACHE.get("xw")
    if hit is None or hit[0] != key:
        hit = (key, Crosswalk.read(path, clubs))
        _XW_CACHE["xw"] = hit
    return hit[1]


def load_fixtures() -> list[dict]:
    rows = newest("fixtures.csv")
    return sorted(rows, key=lambda r: r.get("kickoff") or "")


def load_odds() -> list[dict]:
    """Newest bookmaker quote per fixture, from odds.csv.

    latest_per_key on (home, away) rather than latest_only: a fixture is
    quoted from the moment it is listed until it kicks off, so the newest
    SNAPSHOT only carries whatever was still unplayed when it was taken,
    while every fixture's own newest quote is what a consumer wants.
    """
    from ffcore import schema
    return latest_per_key(read_csv(TIDY / "odds.csv"),
                          lambda r: (schema.text(r, "home"),
                                     schema.text(r, "away")))


def load_results_history() -> list[dict]:
    return read_csv(TIDY / "results_history.csv")


_UNDERSTAT_CACHE: dict[tuple, tuple] = {}


def load_understat_players(season: str = "") -> list[dict]:
    """Latest row per (season, understat_id). The cache key used to include
    `season`, so each of a run's several same-season calls (score.py alone:
    5x across two seasons per build()) re-read and re-sorted the whole
    ~160k-row CSV. Cached once per file version instead; `season` only
    filters the already-deduped, far smaller result."""
    path = TIDY / "understat_players.csv"
    try:
        st = path.stat()
    except OSError:
        return []
    key = (st.st_mtime_ns, st.st_size)
    hit = _UNDERSTAT_CACHE.get(key)
    if hit is None:
        latest: dict[tuple, dict] = {}
        for r in sorted(read_csv(path), key=lambda r: r.get("observed_at", "")):
            k = (r.get("season"), r.get("understat_id"))
            if k[1]:
                latest[k] = r
        hit = tuple(latest.values())
        _UNDERSTAT_CACHE[key] = hit
    if season:
        return [dict(r) for r in hit if r.get("season") == season]
    return [dict(r) for r in hit]


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


def next_kickoff(now=None):
    now = now or run_now()
    ahead = [k for k in (kickoff_stamp(r.get("kickoff"))
                         for r in load_fixtures()) if k and k > now]
    return min(ahead) if ahead else None


def team_slug_of(side: str, slugs) -> str | None:
    from ffcore.fixture import match_team

    spelled = {s.replace("-", " "): s for s in slugs}
    hit = match_team(side, list(spelled))
    return spelled.get(hit) if hit else None


def lock_order(locks: dict[int, datetime]) -> list[int]:
    return [j for j, _ in sorted(locks.items(), key=lambda kv: kv[1])]


class JornadaClock:

    def __init__(self, matches: list[dict], fixtures: list[dict]):
        jornada_of: dict[tuple[str, str], int] = {}
        for m in matches:
            try:
                jornada_of[(m["home"], m["away"])] = int(m["jornada"])
            except (KeyError, ValueError, TypeError):
                continue
        slugs = {s for pair_ in jornada_of for s in pair_}

        self.team_locks: dict[tuple[int, str], datetime] = {}
        for f in fixtures:
            when = kickoff_stamp(f.get("kickoff"))
            home = team_slug_of(f.get("home") or "", slugs)
            away = team_slug_of(f.get("away") or "", slugs)
            jor = jornada_of.get((home, away))
            if when is None or jor is None:
                continue
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
        _CLOCK.append(JornadaClock(load_matches(), load_fixtures()))
    return _CLOCK[0]


def clock_history() -> JornadaClock:
    if not _CLOCK_HISTORY:
        _CLOCK_HISTORY.append(
            JornadaClock(load_matches(), read_csv(TIDY / "fixtures.csv")))
    return _CLOCK_HISTORY[0]


_JORNADA_OF_MATCH: list = []


def jornada_of_match() -> dict[str, int]:
    if not _JORNADA_OF_MATCH:
        out: dict[str, int] = {}
        for m in load_matches_history():
            mid = (m.get("match_id") or "").strip()
            if not mid or mid in out:
                continue
            try:
                out[mid] = int(m.get("jornada"))
            except (TypeError, ValueError):
                continue
        _JORNADA_OF_MATCH.append(out)
    return _JORNADA_OF_MATCH[0]


MARKET_FIELDS = [("team", "team", None), ("pos", "position", None),
                 ("value", "value", money), ("delta_1d", "delta_1d", money)]
XI_FIELDS = [("team", "team_slug", None), ("start", "start_pct", pct100),
             ("status", "status", None)]


def _merge(players: dict, rows: list[dict], name_col: str, fields,
           shared=(), club_of=None, by_ff_slug=None) -> dict:
    for r in rows:
        key = row_key(r, shared) if r.get("ff_id") else ""
        if not key:
            key = (by_ff_slug or {}).get(
                norm(r.get("player_slug") or "")) or ""
        if not key:
            key = norm(r.get(name_col))
            if key in shared:
                club = _club(r) or (club_of or {}).get(
                    norm(r.get("team_slug") or ""), "")
                if not club:
                    continue
                key = "%s@%s" % (key, club)
        if not key:
            continue
        rec = players.setdefault(key, {})
        rec.setdefault("name", (r.get(name_col) or "").strip())
        for field, col, parse in fields:
            if field in rec:
                continue
            raw = r.get(col)
            if raw in (None, ""):
                continue
            val = parse(raw) if parse else str(raw).strip()
            if val is not None:
                rec[field] = val
    return players


def stale_owned_players(players: dict, owned_keys, market) -> dict:
    """players, plus a last-known record for each owned key the live
    market snapshot dropped, via market.latest() (never expiring) and
    load_players()'s own field parsing. Tags the backfilled `status` as
    stale so squads.flag() renders a warning instead of reading as fresh.
    Does not touch load_players()'s own forecasting player universe.
    """
    missing = [k for k in owned_keys if k not in players]
    if not missing or market is None:
        return players
    latest = market.latest()
    stale = [latest[k] for k in missing if k in latest]
    if not stale:
        return players
    out = dict(players)
    _merge(out, stale, "name", MARKET_FIELDS)
    for r in stale:
        key = row_key(r, ())
        rec = out.get(key)
        if rec is not None:
            rec["status"] = "stale since %s" % r.get("observed_at", "?")
    return out


def load_players() -> dict[str, dict]:
    market, xi = load_market_latest(), _cached_latest_snapshot(TIDY / "lineups.csv")
    if not market and not xi:
        raise SystemExit("no rows in %s — run `ingest.py parse` first" % TIDY)
    shared = shared_names(market)
    club_of = {}
    for c in read_csv(TIDY / "clubs.csv"):
        if c.get("ff_slug") and c.get("market"):
            club_of[norm(c["ff_slug"])] = norm(c["market"])
    xw = load_crosswalk()
    by_ff_slug = {}
    if xw is not None:
        for pl in xw.players.values():
            if pl.ff_slug:
                by_ff_slug[norm(pl.ff_slug)] = pl.player_id
    players: dict[str, dict] = {}
    _merge(players, market, "name", MARKET_FIELDS, shared, club_of)
    _merge(players, xi, "player_name", XI_FIELDS, shared, club_of,
           by_ff_slug=by_ff_slug)
    return players


LEDGER = TIDY / "transactions.csv"


def read_ledger(path=LEDGER) -> list[dict]:
    path = Path(path)
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        lines = [ln for ln in fh if not ln.lstrip().startswith("#")]
    rows = [r for r in csv.DictReader(lines)
            if (r.get("player") or "").strip()
            and not r["player"].lstrip().startswith("#")]
    rows.sort(key=lambda r: (r.get("date") or ""))
    return rows


class Valuation(NamedTuple):
    value: float
    observed_at: str
    lag_h: float
    name: str


VALUE_TOLERANCE = 0.05


def price_agrees(a, b, tolerance: float = VALUE_TOLERANCE) -> bool:
    if not a or not b:
        return False
    return abs(a - b) <= tolerance * max(a, b)


def _club(row: dict) -> str:
    return norm(row.get("team") or "")


def narrow_by_club(candidates, want: str, club_of) -> object | None:
    if not want:
        return None
    hits = [c for c in candidates if club_of(c) == want]
    return hits[0] if len(hits) == 1 else None


def shared_names(rows) -> set:
    clubs: dict[str, set] = {}
    for r in latest_only(rows):
        n = norm(r.get("name"))
        if n:
            clubs.setdefault(n, set()).add(_club(r))
    return {n for n, c in clubs.items() if len(c) > 1}


def row_key(row: dict, shared: set) -> str:
    fid = (row.get("ff_id") or "").strip()
    if fid:
        return fid
    n = norm(row.get("name"))
    return "%s@%s" % (n, _club(row)) if n in shared else n


class Market:

    def __init__(self, rows: list[dict]):
        self.rows = rows
        self._latest: list | None = None
        self._name_idx: dict | None = None
        self._resolved: dict[str, str | None] = {}
        self._parsed_cache: dict[str, list] = {}
        self._by_key: dict[str, list[tuple[datetime, dict]]] = {}
        latest = latest_only(rows)
        self._by_name: dict[str, list] = {}
        for r in latest:
            n = norm(r.get("name"))
            k = row_key(r, ())
            if n and k:
                self._by_name.setdefault(n, [])
                if k not in self._by_name[n]:
                    self._by_name[n].append(k)
        self._shared = {n for n, ks in self._by_name.items() if len(ks) > 1}
        for r in rows:
            key = self.key_of(r)
            when = snapshot_stamp(r.get("observed_at", ""))
            if not key or when is None:
                continue
            self._by_key.setdefault(key, []).append((when, r))
        for hist in self._by_key.values():
            hist.sort(key=lambda t: t[0])

    def key_of(self, row: dict) -> str:
        return row_key(row, self._shared)

    def __len__(self) -> int:
        return len(self._by_key)

    def latest_rows(self) -> list:
        if self._latest is None:
            self._latest = latest_only(self.rows)
        return self._latest

    def _name_index(self) -> dict:
        if self._name_idx is None:
            self._name_idx = index_by(self.latest_rows(), "name")
        return self._name_idx

    def key_for(self, name, team: str = "", value=None):
        k = norm(name)
        if k in self._shared:
            return self._pick(k, team, value)
        if k in self._by_key:
            return k
        if value is None and k in self._resolved:
            return self._resolved[k]
        row, cands = resolve(name, self.latest_rows(), index=self._name_index())
        if row is not None:
            got = self.key_of(row)
            self._resolved[k] = got
            return got
        if cands and value is not None:
            return self._by_price(
                {self.key_of(r): r.get("value") for r in cands}, value)
        if value is None:
            self._resolved[k] = None
        return None

    def candidates(self, name) -> tuple:
        k = norm(name)
        if k in self._shared:
            return None, [key for key in self._by_name.get(k, [])
                          if key in self._by_key]
        if k in self._by_key:
            return k, []
        row, cands = resolve(name, self.latest_rows(), index=self._name_index())
        if row is not None:
            return self.key_of(row), []
        return None, [self.key_of(r) for r in cands]

    def _pick(self, shared: str, team: str, value):
        keys = [k for k in self._by_name.get(shared, []) if k in self._by_key]
        if team:
            return narrow_by_club(
                keys, _club({"team": team}),
                lambda k: _club(self._by_key[k][-1][1]))
        return self._by_price(
            {k: (self._by_key[k][-1][1]).get("value") for k in keys}, value)

    def _by_price(self, values: dict, value) -> str | None:
        if value is None:
            return None
        val = money(value) if isinstance(value, str) else float(value)
        if not val:
            return None
        hits = [k for k, raw in values.items() if price_agrees(money(raw), val)]
        return hits[0] if len(hits) == 1 else None

    def latest(self) -> dict[str, dict]:
        return {k: hist[-1][1] for k, hist in self._by_key.items() if hist}

    def _parsed(self, key: str) -> list[tuple[datetime, dict, float | None]]:
        """(t, row, money(row['value'])) per key, parsed once and cached --
        at()/series() used to re-parse a player's whole history every call."""
        cached = self._parsed_cache.get(key)
        if cached is None:
            cached = [(t, r, money(r.get("value")))
                     for t, r in self._by_key.get(key, ())]
            self._parsed_cache[key] = cached
        return cached

    def at(self, name, when: datetime | None) -> Valuation | None:
        key = self.key_for(name)
        if not key or when is None:
            return None
        hist = self._parsed(key)
        if not hist:
            return None
        prior = [(t, r, v) for t, r, v in hist if t <= when]
        t, r, val = prior[-1] if prior else hist[0]
        if val is None:
            return None
        return Valuation(val, r.get("observed_at", ""),
                         (when - t).total_seconds() / 3600.0,
                         r.get("name", name))

    def series(self, name) -> list[tuple[datetime, float]]:
        key = self.key_for(name)
        if not key:
            return []
        return [(t, v) for t, _r, v in self._parsed(key) if v is not None]

    def drift(self, name, since: datetime | None, days: float):
        if since is None:
            return None
        base = self.at(name, since)
        if not base:
            return None
        target = since + timedelta(days=days)
        later = [(t, v) for t, v in self.series(name) if t >= target]
        if not later:
            return None
        _, v = later[0]
        return v - base.value, (v / base.value - 1) * 100.0 if base.value else None


LISTED_SELLER = "marketPlayerTeam"


def market_routes(mkt: list[dict], key_of) -> tuple[dict[str, float],
                                                    dict[str, str],
                                                    dict[str, int]]:
    price: dict[str, float] = {}
    route: dict[str, str] = {}
    bids: dict[str, int] = {}
    for r in mkt:
        k = key_of(r)
        if not k or not r.get("sale_price"):
            continue
        price[k] = float(r["sale_price"])
        route[k] = "listed" if r.get("seller") == LISTED_SELLER else "free"
        bids[k] = int(r.get("bids") or 0)
    return price, route, bids


def pending_sent(mkt: list[dict], key_of) -> dict[str, float]:
    """Your own live bids, by player key -- money you have COMMITTED, not
    money you have SPENT. The app does not debit a bid when it is placed
    (verified against its own balance feed on 2026-09-18: two bids totalling
    34.7M stood against a 13.2M balance), so this must never be subtracted
    from cash. It is a list of actions already taken, to be re-endorsed or
    withdrawn -- mirror of pending_received()."""
    out: dict[str, float] = {}
    for r in mkt:
        if (r.get("bid_status") or "") != "pending" or not r.get("bid_money"):
            continue
        k = key_of(r)
        if not k:
            continue
        out[k] = max(out.get(k, 0.0), float(r["bid_money"]))
    return out


def pending_received(offers: list[dict], pt_to_key: dict[str, str]
                     ) -> dict[str, float]:
    out: dict[str, float] = {}
    for r in offers:
        if (r.get("status") or "") != "pending":
            continue
        k = pt_to_key.get(r.get("player_team_id") or "")
        money = float(r.get("money") or 0)
        if not k or not money:
            continue
        out[k] = max(out.get(k, 0.0), money)
    return out



def _selftest_cache() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "t.csv"
        write_csv(p, [{"a": "1", "b": "x"}, {"a": "2", "b": "y"}])
        first = read_csv(p)
        assert [r["a"] for r in first] == ["1", "2"], first

        # The rows are read-only views: a caller cannot corrupt the cache
        # because it cannot write at all. Stronger than the copy this
        # replaced, and free.
        try:
            first[0]["a"] = "999"
            raise AssertionError("read_csv rows must be read-only")
        except TypeError:
            pass
        first.append({"a": "3", "b": "z"})     # the LIST is still the caller's
        assert [r["a"] for r in read_csv(p)] == ["1", "2"], read_csv(p)

        write_csv(p, [{"a": "7", "b": "q"}])
        assert [r["a"] for r in read_csv(p)] == ["7"], read_csv(p)

        append_csv(p, [{"a": "8", "b": "r"}])
        assert [r["a"] for r in read_csv(p)] == ["7", "8"], read_csv(p)

        assert read_csv(Path(tmp) / "nope.csv") == []


def _selftest_crosswalk_cache() -> None:
    import tempfile

    global TIDY
    real_tidy = TIDY
    with tempfile.TemporaryDirectory() as tmp:
        TIDY = Path(tmp)
        try:
            assert load_crosswalk() is None    # no players.csv yet
            write_csv(TIDY / "players.csv",
                     [{"player_id": "a", "name": "A", "club_id": "c"}],
                     ["player_id", "name", "club_id", "ff_slug", "af_slug",
                      "app_id", "understat_id", "app_names"])
            write_csv(TIDY / "clubs.csv",
                     [{"club_id": "c", "market": "C", "ff_slug": "c-slug"}],
                     ["club_id", "market", "ff_slug", "elo", "market_id",
                      "af_id", "aliases"])
            xw1 = load_crosswalk()
            assert xw1.players["a"].name == "A", xw1.players
            assert load_crosswalk() is xw1     # same object: cache hit

            write_csv(TIDY / "players.csv",
                     [{"player_id": "a", "name": "Renamed", "club_id": "c"}],
                     ["player_id", "name", "club_id", "ff_slug", "af_slug",
                      "app_id", "understat_id", "app_names"])
            xw2 = load_crosswalk()
            assert xw2 is not xw1 and xw2.players["a"].name == "Renamed", \
                xw2.players
        finally:
            TIDY = real_tidy
            _XW_CACHE.clear()


def _selftest_new_loaders() -> None:
    rows = [{"k": "a", "observed_at": "t1", "v": "old-a"},
            {"k": "a", "observed_at": "t3", "v": "new-a"},
            {"k": "a", "observed_at": "t2", "v": "mid-a"},
            {"k": "b", "observed_at": "t1", "v": "only-b"}]
    got = latest_per_key(rows, lambda r: r["k"])
    by_k = {r["k"]: r["v"] for r in got}
    assert by_k == {"a": "new-a", "b": "only-b"}, by_k
    assert {r["k"] for r in latest_only(rows)} == {"a"}
    assert {r["k"] for r in got} == {"a", "b"}
    assert latest_per_key([], lambda r: r["k"]) == []
    assert latest_per_key([{"observed_at": "t1"}], lambda r: None) == []

    matches_full = read_csv(TIDY / "matches.csv")
    if matches_full:
        real_matches = {r.get("match_id") for r in matches_full
                        if r.get("match_id")}
        got_matches = {r.get("match_id") for r in load_matches()}
        assert got_matches == real_matches, \
            "load_matches() lost a match latest_only should have kept"
        assert load_matches_history() == matches_full

    starters_full = read_csv(TIDY / "starters.csv")
    if starters_full:
        real_keys = {(r.get("match_id"), r.get("player_name"))
                    for r in starters_full}
        got_keys = {(r.get("match_id"), r.get("player_name"))
                   for r in load_starters()}
        assert got_keys == real_keys, \
            "load_starters() lost a (match, player) key latest_only should keep"

    stats_all = read_csv(TIDY / "api_stats.csv")
    if stats_all:
        all_keys = {_api_stats_key(r) for r in stats_all}
        naive_keys = {_api_stats_key(r) for r in latest_only(stats_all)}
        real_keys = {_api_stats_key(r) for r in load_api_stats()}
        assert real_keys == all_keys, "load_api_stats() must cover every key"
        assert len(real_keys) > len(naive_keys), (
            "load_api_stats() returned no more keys than latest_only() "
            "would — this loader exists ONLY because latest_only() loses "
            "98% of this table's keys; if this assertion ever fails, "
            "someone put latest_only() back")

    files = sorted((SEASON / "live").glob("perjornada_*.csv"))
    if files:
        assert load_perjornada() == read_csv(files[-1])
    else:
        assert load_perjornada() == []

    c1 = clock()
    c2 = clock()
    assert c1 is c2, "clock() must return the SAME object on a second call"

    _full = clock_history()
    assert _full is clock_history(), "clock_history() must be memoized too"
    assert set(c1.team_locks) < set(_full.team_locks), \
        ("clock() is no longer upcoming-only -- if that was deliberate, "
         "load_deadline() changed with it; see clock()'s own docstring")
    assert isinstance(c1, JornadaClock)

    j1 = jornada_of_match()
    j2 = jornada_of_match()
    assert j1 is j2, "jornada_of_match() must be memoized"
    expect: dict[str, int] = {}
    for m in load_matches_history():
        mid = (m.get("match_id") or "").strip()
        if mid and mid not in expect:
            try:
                expect[mid] = int(m.get("jornada"))
            except (TypeError, ValueError):
                continue
    assert j1 == expect, "jornada_of_match() must be first-write-wins"

    _selftest_stale_owned()


def _selftest_stale_owned() -> None:
    live = {"live_id": {"name": "Still Listed", "value": 5_000_000}}
    fell_off = {"live_id": {"name": "Still Listed", "value": 5_000_000},
               "gone_id": {"ff_id": "gone_id", "name": "Gustavo",
                            "value": "13594034",
                            "observed_at": "2026-09-01T1056Z", "team": "Racing",
                            "position": "mediocampista"}}
    market = SimpleNamespace(latest=lambda: fell_off)

    out = stale_owned_players(live, ["live_id", "gone_id"], market)
    assert out["live_id"] == live["live_id"], "must not touch a live record"
    assert (out["gone_id"]["value"], out["gone_id"]["name"],
           out["gone_id"]["status"]) == \
        (13_594_034.0, "Gustavo", "stale since 2026-09-01T1056Z"), out
    assert "gone_id" not in live, "must not mutate the input dict"

    assert stale_owned_players(live, ["live_id"], market) is live
    assert stale_owned_players(live, ["never_seen"], market) is live
    assert stale_owned_players(live, ["gone_id"], None) is live


def _selftest() -> None:
    _selftest_cache()
    _selftest_new_loaders()
    _selftest_crosswalk_cache()
    rows = [{"observed_at": "t1", "name": "A"}, {"observed_at": "t2",
            "name": "B"}, {"observed_at": "t2", "name": "C"}]
    assert [r["name"] for r in latest_only(rows)] == ["B", "C"]
    assert latest_only([]) == []
    assert snapshots(rows) == ["t1", "t2"]

    now = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)
    day_old = [{"observed_at": "2026-08-18T2246Z", "club": "Barcelona"}]
    two_days = [{"observed_at": "2026-08-17T2246Z", "club": "Barcelona"}]
    import inspect as _inspect
    for _fn in (fresh_only, stale_feeds):
        _src = _inspect.getsource(_fn)
        assert "now or run_now()" in _src, _fn.__name__

    assert run_now() is run_now()
    assert run_now().tzinfo is timezone.utc
    _NOW.clear()
    os.environ["LFG_NOW"] = "2026-08-20T0900Z"
    assert run_now() == snapshot_stamp("2026-08-20T0900Z")
    del os.environ["LFG_NOW"]
    _NOW.clear()
    assert run_now().year >= 2026

    assert fresh_only(day_old, DAILY_FRESH_DAYS, now) == day_old
    assert fresh_only(two_days, DAILY_FRESH_DAYS, now) == []
    assert fresh_only([{"observed_at": "2026-08-18T1030Z"}],
                      DAILY_FRESH_DAYS, now) == []
    assert fresh_only([{"observed_at": "2026-08-18T1100Z"}],
                      DAILY_FRESH_DAYS, now) != []
    assert fresh_only([{"observed_at": "whenever"}], DAILY_FRESH_DAYS, now) == []
    assert fresh_only([{}], DAILY_FRESH_DAYS, now) == []
    assert fresh_only([], DAILY_FRESH_DAYS, now) == []
    assert fresh_only([{"observed_at": "2026-08-19T2300Z"}],
                      DAILY_FRESH_DAYS, now) != []

    assert age_phrase(3.2) == "3 days" and age_phrase(0.5) == "12 hours"
    assert age_phrase(0.01) == "14 minutes" and age_phrase(0.0) == "1 minute"
    assert age_phrase(1 / 24) == "1 hour", age_phrase(1 / 24)

    assert EVERY_RUN_FRESH_DAYS * 24 > 13 + 10 / 60
    assert EVERY_RUN_FRESH_DAYS < 1.0
    healthy = [{"observed_at": "2026-08-18T2300Z"}]
    missed = [{"observed_at": "2026-08-18T1100Z"}]
    assert fresh_only(healthy, EVERY_RUN_FRESH_DAYS, now) == healthy
    assert fresh_only(missed, EVERY_RUN_FRESH_DAYS, now) == []

    stale = datetime(2099, 1, 1, tzinfo=timezone.utc)
    assert load_api("teams", now=stale) == []
    assert load_api("market", now=stale) == []
    assert load_api("standings", now=stale) == []
    assert load_api("offers", now=stale) == []

    assert last_api_standings() != [] or read_csv(TIDY / "api_standings.csv") == []

    quiet = stale_feeds(now=stale)
    assert set(quiet) == set(GATED_API), quiet
    assert min(quiet.values()) > 365 * 70
    assert "api_nothing" not in stale_feeds(now=stale, names=("api_nothing",))

    tw = [{"ff_id": "867", "name": "Álvaro García", "team": "Rayo",
           "value": "20233300", "observed_at": "2026-08-19T1639Z"},
          {"ff_id": "12993", "name": "Álvaro García", "team": "Villarreal",
           "value": "501929", "observed_at": "2026-08-19T1639Z"},
          {"ff_id": "5001", "name": "Pepelu", "team": "Valencia",
           "value": "7669774", "observed_at": "2026-08-19T1639Z"}]
    tm = Market(tw)
    assert len(tm) == 3, len(tm)
    assert sorted(tm.latest()) == ["12993", "5001", "867"], sorted(tm.latest())
    rayo, villa = tm.key_for("Álvaro García", team="Rayo"), \
        tm.key_for("Álvaro García", team="Villarreal")
    assert (rayo, villa) == ("867", "12993"), (rayo, villa)
    assert tm.at(rayo, snapshot_stamp("2026-08-19T1700Z")).value == 20233300.0
    assert tm.at(villa, snapshot_stamp("2026-08-19T1700Z")).value == 501929.0

    assert tm.key_for("Álvaro García") is None
    assert tm.key_for("alvaro garcia") is None

    assert tm.key_for("Álvaro García", value=20233300) == rayo
    assert tm.key_for("Álvaro García", value=501929) == villa
    assert tm.key_for("Álvaro García", value=9e6) is None
    assert tm.key_for("Álvaro García", team="Elche") is None

    assert tm.key_for("Pepelu") == "5001"
    assert "5001" in tm.latest()
    assert not [k for k in tm.latest() if "@" in k]

    gone = [{"name": "Iker Munoz", "team": "Osasuna", "value": "1000000",
             "observed_at": "2026-07-01T1000Z"},
            {"name": "Iker Munoz", "team": "Getafe", "value": "2000000",
             "observed_at": "2026-07-01T1000Z"},
            {"name": "Iker Munoz", "team": "Getafe", "value": "2100000",
             "observed_at": "2026-08-19T1639Z"}]
    assert shared_names(gone) == set(), shared_names(gone)
    assert shared_names(latest_only(gone)) == shared_names(gone)
    assert row_key(gone[-1], shared_names(gone)) == norm("Iker Munoz")

    rom = [{"name": "Isaac Romero", "team": "Sevilla", "value": "6023939",
            "observed_at": "2026-08-19T1639Z"},
           {"name": "Cristian Romero", "team": "Atletico", "value": "47546565",
            "observed_at": "2026-08-19T1639Z"},
           {"name": "Carlos Romero", "team": "Espanyol", "value": "42510131",
            "observed_at": "2026-08-19T1639Z"}]
    rm = Market(rom)
    assert rm.key_for("C. Romero") is None
    assert rm.key_for("C. Romero", value=45739000) == norm("Cristian Romero")
    assert rm.key_for("C. Romero", value=45000000) is None
    assert rm.key_for("C. Romero", value=1000) is None
    assert rm.key_for("Isaac Romero", value=47546565) == norm("Isaac Romero")
    assert rm.key_for("C. Romero") is None

    assert tm.candidates("Pepelu") == ("5001", [])
    got, cands = tm.candidates("Álvaro García")
    assert got is None and sorted(cands) == ["12993", "867"], cands
    assert rm.candidates("C. Romero")[0] is None
    assert sorted(rm.candidates("C. Romero")[1]) == [
        norm("Carlos Romero"), norm("Cristian Romero"),
        norm("Isaac Romero")]
    assert tm.candidates("Nobody At All") == (None, [])

    assert rm.at("C. Romero", snapshot_stamp("2026-08-19T1700Z")) is None

    mkt = [{"name": "Ane Aldea", "team": "Alavés", "position": "defensa",
            "value": "2.050.000", "delta_1d": "-12.000"},
           {"name": "Bo Bidal", "team": "Betis", "position": "delantero",
            "value": "", "delta_1d": "0"}]
    xi = [{"player_name": "Ane Aldea", "team_slug": "alaves",
           "start_pct": "0.72", "status": "doubt"},
          {"player_name": "Cai Coro", "team_slug": "celta",
           "start_pct": "85", "status": "ok"}]
    p = _merge(_merge({}, mkt, "name", MARKET_FIELDS), xi,
               "player_name", XI_FIELDS)

    a = p["ane aldea"]
    assert a["value"] == 2050000.0 and a["delta_1d"] == -12000.0
    assert a["pos"] == "defensa" and a["start"] == 72.0 and a["status"] == "doubt"
    assert a["team"] == "Alavés" and a["name"] == "Ane Aldea"

    assert "value" not in p["bo bidal"] and p["bo bidal"]["delta_1d"] == 0.0
    assert "start" not in p["bo bidal"]

    c = p["cai coro"]
    assert c["name"] == "Cai Coro" and c["team"] == "celta" and c["start"] == 85.0
    assert "value" not in c

    both = [{"source": "futbolfantasy", "player_name": "Ane"},
            {"source": "analitica", "player_name": "Ane"},
            {"player_name": "Bo"}]
    assert [r["source"] for r in pick_source(both, "analitica")] == ["analitica"]
    assert len(pick_source(both, "futbolfantasy")) == 1
    assert pick_source(both, "") == both
    assert pick_source(both, "nobody") == []

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
    key_of = {"Free Agent": "free_agent", "Listed Rival": "listed_rival",
             "Not Priced": "not_priced"}.get
    price, route, bids = market_routes(
        mkt_rows, lambda r: key_of((r.get("player_name") or "")))
    assert price == {"free_agent": 5000000.0, "listed_rival": 8000000.0}, price
    assert route == {"free_agent": "free", "listed_rival": "listed"}, route
    assert bids == {"free_agent": 0, "listed_rival": 2}, bids
    assert "not_priced" not in route and "not_priced" not in price
    unknown_seller = [{"player_name": "Free Agent", "sale_price": "1",
                       "seller": "something_new"}]
    _, r2, _ = market_routes(unknown_seller, lambda r: "free_agent")
    assert r2 == {"free_agent": "free"}, r2

    # THE CLOCK A PERSON READS vs THE CLOCK THAT KEYS THE DATA.
    summer = datetime(2026, 9, 18, 16, 40, tzinfo=timezone.utc)
    winter = datetime(2026, 12, 18, 16, 40, tzinfo=timezone.utc)
    assert shown(summer) == "2026-09-18 18:40 CEST", shown(summer)
    # The October change is the reason the offset is read rather than
    # assumed -- the same UTC hour is 18:40 in September and 17:40 in
    # December, and a hardcoded "+2" would silently be an hour out all winter.
    assert shown(winter) == "2026-12-18 17:40 CET", shown(winter)
    assert shown(summer, "%d %b %H:%M") == "18 Sep 18:40 CEST"
    # A key must NOT move with the clocks: snapshot_stamp round-trips the
    # UTC spelling that names files and orders snapshots, untouched by any
    # of this.
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
    sent = pending_sent(mkt_bids, lambda r: r["player_name"])
    # The SAME player twice keeps the standing bid, not the sum -- two rows
    # are two snapshots of one commitment, and an unresolvable row is dropped
    # rather than folded into a total nobody can act on.
    assert sent == {"A": 5600000.0, "B": 6795815.0}, sent
    assert sum(sent.values()) == 5600000.0 + 6795815.0
    assert pending_sent([], lambda r: "x") == {}

    p2k = {"pt1": "me_a", "pt2": "me_b"}
    offers = [
        {"player_team_id": "pt1", "status": "pending", "money": "6795815"},
        {"player_team_id": "pt1", "status": "pending", "money": "1000000"},
        {"player_team_id": "pt2", "status": "accepted", "money": "9000000"},
        {"player_team_id": "pt2", "status": "", "money": ""},
        {"player_team_id": "unknown", "status": "pending", "money": "1"},
    ]
    got = pending_received(offers, p2k)
    assert got == {"me_a": 6795815.0}, got
    assert pending_received([], p2k) == {}
    assert pending_received(offers, {}) == {}

    jl_matches = [{"match_id": "1", "jornada": "1", "home": "alaves",
                  "away": "getafe", "score": "3-0"},
                 {"match_id": "2", "jornada": "1", "home": "espanyol",
                  "away": "levante", "score": "1-0"},
                 {"match_id": "9", "jornada": "2", "home": "rayo-vallecano",
                  "away": "alaves", "score": "2-2"}]
    jl_fixtures = [{"kickoff": "2026-08-16T17:00:00+00:00", "home": "Espanyol",
                   "away": "Levante"},
                  {"kickoff": "2026-08-15T19:30:00+00:00", "home": "Alaves",
                   "away": "Getafe"}]
    assert team_slug_of("Racing Santander", {"racing", "real-madrid"}) \
        == "racing"
    assert team_slug_of("Real Betis", {"betis", "real-sociedad"}) == "betis"
    assert team_slug_of("Nowhere FC", {"racing"}) is None
    clock = JornadaClock(jl_matches, jl_fixtures)
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

    print("ffcore.tidy self-test OK (80 cases)")


if __name__ == "__main__":
    _selftest()
