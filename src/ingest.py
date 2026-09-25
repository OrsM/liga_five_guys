"""
ingest.py — the only thing that touches the network or the raw store.

    python src/ingest.py fetch      # sweep the registry, store what changed
    python src/ingest.py parse      # rebuild tidy CSV from every snapshot ever
    python src/ingest.py baseline   # once a season: last season's points table
    python src/ingest.py prune      # migrate/compact data/raw (dry run)
    python src/ingest.py prune --apply
    python src/ingest.py --selftest

Fetch and parse stay separate: when markup changes, fix sources.py and
re-run parse over the whole history — only possible because parsed
output isn't the only thing kept.

WHAT IS IN A SNAPSHOT. One xz-compressed tar per sweep:

    data/raw/dt=2026-08-15T0940Z.tar.xz
        market.html            only if its content changed
        team_celta.html        only if its content changed
        MANIFEST.csv           page, sig, stored, seen — ALWAYS, for every page

The manifest, not the file listing, defines what a snapshot observed —
a page absent from it was not fetched. `stored` names the archive whose
bytes actually hold a page's content, carried forward by later
snapshots; the store is append-only, so deleting one archive corrupts
every later snapshot that carries a page forward from it.

xz + one tar per sweep (not gzip-per-page): halves storage over
per-page gzip and lets pages across a sweep share dictionary — 60MB/29
snapshots to 8MB, keeping a season's projection under GitHub's push
limit.

httpx is imported inside fetch(), never at module level, so --selftest
runs on a box with no network client installed.
"""

from __future__ import annotations

import csv
import hashlib
import io
import itertools
import json
import gzip
import lzma
import os
import random
import re
import shutil
import sys
import tarfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse


from ffcore.auth import API_BASE
from ffcore.league import load_config
from ffcore.tidy import ROOT, SEASON, TIDY, append_csv
from sources import (API_LEAGUES_KEY, CAL_KEY, MATCH_KEY_RE,
                     ROW_TABLE, STORE_DAILY, STORE_ONCE, league_sources,
                     offer_sources, parse_api_leagues, parse_points,
                     parser_sig, played_sources, player_sources,
                     season_label, source_for, sources)

__all__ = ["snapshots", "state", "pages", "doc_keys", "documents", "due",
          "fetch", "parse", "parse_cache", "save_parse_cache", "baseline"]

RAW = ROOT / "raw"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.5",
}

# Per HOST, not per request: each site still sees 1.5-3s between its own
# requests, but 23 futbolfantasy + 21 analiticafantasy pages no longer queue
# behind one another's sleeps (2026-09-24: ~115s of a 122s fetch was sleeping).
# 1.5-3.0 -> 1.0-2.0 the same day: a full 72-request fetch, 73s -> 56s, no
# 403/429 from any of the six sites. Revert here if one starts to answer 429.
DELAY = (1.0, 2.0)


def _by_host(srcs) -> list:
    """`srcs` interleaved across hosts, each host's own order kept."""
    lanes: dict[str, list] = {}
    for s in srcs:
        lanes.setdefault(urlparse(s.url).netloc, []).append(s)
    return [s for row in itertools.zip_longest(*lanes.values())
            for s in row if s is not None]
TIMEOUT = 30.0

MANIFEST = "MANIFEST.csv"
MANIFEST_FIELDS = ["page", "sig", "stored", "seen"]



def _stamp_of(path: Path) -> str:
    return path.name.removeprefix("dt=").removesuffix(".tar.xz")


def snapshots() -> list[Path]:
    found = list(RAW.glob("dt=*.tar.xz")) + [p for p in RAW.glob("dt=*")
                                             if p.is_dir()]
    return sorted(found, key=_stamp_of)


def _read(path: Path, only: set | None = None) -> dict[str, str]:
    want = None if only is None else set(only) | {MANIFEST}
    if path.is_dir():
        return {f.name.removesuffix(".gz"):
                gzip.open(f, "rt", encoding="utf-8", errors="replace").read()
                for f in sorted(path.glob("*.html.gz"))
                if want is None or f.name.removesuffix(".gz") in want}
    with tarfile.open(path, "r:xz") as tf:
        return {m.name: tf.extractfile(m).read().decode("utf-8", "replace")
                for m in tf.getmembers()
                if m.isfile() and (want is None or m.name in want)}


def _write(path: Path, members: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    # preset 3, not 6 (2026-09-24): 1.6s vs 8.4s on a 57MB snapshot for +13%
    # size (1278KB vs 1129KB) -- a fixed ~7s off every full fetch.
    with lzma.open(tmp, "wb", preset=3) as xz:
        with tarfile.open(fileobj=xz, mode="w", format=tarfile.USTAR_FORMAT) as tf:
            for name in sorted(members):
                blob = members[name].encode("utf-8")
                info = tarfile.TarInfo(name)
                info.size = len(blob)
                info.mtime = 0
                info.uid = info.gid = 0
                info.uname = info.gname = ""
                tf.addfile(info, io.BytesIO(blob))
    tmp.replace(path)


def _manifest(members: dict[str, str]) -> list[dict]:
    body = members.get(MANIFEST)
    if body is None:
        return [{"page": k.removesuffix(".html"), "sig": "", "stored": "",
                 "seen": ""} for k in sorted(members) if k.endswith(".html")]
    return list(csv.DictReader(io.StringIO(body)))


def _manifest_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=MANIFEST_FIELDS, lineterminator="\n")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def state() -> dict[str, dict]:
    snaps = snapshots()
    if not snaps:
        return {}
    return {r["page"]: r for r in _manifest(_read(snaps[-1]))}


def pages(only: set | None = None):
    keep = None if only is None else {"%s.html" % p for p in only}
    carried: dict[str, str] = {}
    for snap in snapshots():
        members = _read(snap, keep)
        carried.update({k.removesuffix(".html"): v for k, v in members.items()
                        if k.endswith(".html")})
        present = [r["page"] for r in _manifest(members)]
        yield _stamp_of(snap), {p: carried[p] for p in present if p in carried}


_INDEX = "snapindex.json"


def _index_load() -> dict:
    try:
        blob = json.loads((TIDY / _INDEX).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return blob.get("snaps", {})


def _index_save(snaps: dict) -> None:
    TIDY.mkdir(parents=True, exist_ok=True)
    try:
        (TIDY / _INDEX).write_text(json.dumps({"snaps": snaps}),
                                   encoding="utf-8")
    except OSError:
        pass


def _size_of(path: Path) -> int:
    return path.stat().st_size if path.is_file() else -1


def doc_keys():
    idx, out, carried, keys = _index_load(), [], {}, Sigs()
    fresh, opened = {}, 0
    for snap in snapshots():
        stamp, size = _stamp_of(snap), _size_of(snap)
        have = idx.get(stamp)
        if have is not None and have.get("size") == size:
            resolved = {p: (v[0], v[1]) for p, v in have["pages"].items()}
        else:
            opened += 1
            members = _read(snap)
            carried.update({
                k.removesuffix(".html"): (keys.of(k.removesuffix(".html"), v),
                                          stamp)
                for k, v in members.items() if k.endswith(".html")})
            present = [r["page"] for r in _manifest(members)]
            resolved = {p: carried[p] for p in present if p in carried}
        carried.update(resolved)
        fresh[stamp] = {"size": size,
                        "pages": {p: list(v) for p, v in resolved.items()}}
        out.append((stamp, resolved))
    if fresh != idx:
        _index_save(fresh)
    if opened:
        print("  read %d of %d snapshots from disk" % (opened, len(out)))
    return out


def documents(need: dict[str, set]):
    for snap in snapshots():
        stamp = _stamp_of(snap)
        want = need.get(stamp)
        if not want:
            continue
        for name, html in _read(snap, {"%s.html" % p for p in want}).items():
            if name.endswith(".html"):
                yield stamp, name.removesuffix(".html"), html



TWICE_DAILY_HOURS = 6.0


def due(src, prev: dict, now: str) -> bool:
    if src.cadence == "once":
        return src.key not in prev
    seen = prev.get(src.key, {}).get("seen", "")
    if src.cadence == "twice_daily":
        gap = _hours_between(seen, now)
        return gap is None or gap >= TWICE_DAILY_HOURS
    if src.cadence != "daily":
        return True
    return not (seen[:10] == now[:10])


def _hours_between(then: str, now: str) -> float | None:
    from ffcore.tidy import snapshot_stamp

    a, b = snapshot_stamp(then), snapshot_stamp(now)
    if a is None or b is None:
        return None
    return (b - a).total_seconds() / 3600.0


def carry_matches(rows: list[dict], prev: dict) -> list[dict]:
    have = {r["page"] for r in rows}
    return rows + [dict(r) for page, r in prev.items()
                   if MATCH_KEY_RE.match(page) and page not in have]


def _odds_api_key() -> str | None:
    env = os.environ.get("ODDS_API_KEY")
    if env:
        return env.strip()
    repo_root = Path(__file__).resolve().parent.parent
    path = repo_root / ".odds_api_key"
    try:
        key = path.read_text().strip()
    except FileNotFoundError:
        return None
    return key or None


def fetch() -> Path:
    import httpx

    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y-%m-%dT%H%MZ")
    dest = RAW / f"dt={stamp}.tar.xz"
    if dest.exists():
        print(f"{dest} already exists; nothing to do.")
        return dest

    prev = state()
    store: dict[str, str] = {}
    rows: list[dict] = []
    unchanged = skipped = rotted = 0
    timing: list[tuple] = []
    fails: dict[str, str] = {}

    bearer = None
    try:
        from ffcore.auth import TokenStore
        store_ = TokenStore()
        bearer = store_.bearer()
        left = store_.expiry_days()
        if left is not None and left < 14:
            print(f"  WARNING: league login expires in {left:.0f} days — "
                  f"run `python -m ffcore.auth --login` before it does.")
    except FileNotFoundError:
        print("  note: no league token; API sources will be skipped.")
    except Exception as e:                              # noqa: BLE001
        print(f"  warn: league token unusable ({e}); API sources skipped.")

    odds_key = _odds_api_key()
    if odds_key is None:
        print("  note: no Odds API key (.odds_api_key or ODDS_API_KEY); "
              "the odds source will be skipped.")

    with httpx.Client(headers=HEADERS, timeout=TIMEOUT,
                      follow_redirects=True) as c:
        queue = _by_host(sources())
        last: dict[str, float] = {}
        league_id = None
        me = load_config().me
        while queue:
            src = queue.pop(0)
            if not due(src, prev, stamp):
                if src.key in prev:
                    rows.append(dict(prev[src.key]))
                skipped += 1
                continue
            if src.key == "odds" and odds_key is None:
                skipped += 1
                continue
            url = src.url.format(date=stamp[:10], base=API_BASE,
                                 odds_key=odds_key or "")
            extra = {}
            if src.auth:
                if bearer is None:
                    print(f"  warn: {src.key} needs the league token and "
                          f"there is none — skipping. "
                          f"Run `python -m ffcore.auth --login`.")
                    continue
                extra["Authorization"] = f"Bearer {bearer}"
            if not src.auth:
                host = urlparse(url).netloc
                time.sleep(max(0.0, last.get(host, -1e9) - time.monotonic()
                               + random.uniform(*DELAY)))
                last[host] = time.monotonic()
            t0 = time.monotonic()
            kw = {"headers": extra} if extra else {}
            if src.timeout is not None:
                kw["timeout"] = src.timeout
            try:
                r = (c.post(url, data=src.body, **kw) if src.body is not None
                    else c.get(url, **kw))
            except httpx.RequestError as e:
                timing.append((time.monotonic() - t0, src.key, "FAILED"))
                fails[src.key] = type(e).__name__
                print(f"  warn: {type(e).__name__} on {src.key}, skipping")
                continue
            timing.append((time.monotonic() - t0, src.key, r.status_code))
            if r.status_code in (403, 429):
                sys.exit(f"{r.status_code} on {url} — backing off, "
                         f"run again later. Nothing was written.")
            if r.status_code != 200:
                print(f"  warn: {r.status_code} on {src.key}, skipping")
                continue
            if src.key == CAL_KEY:
                queue += played_sources(r.text)
            if src.table == "api_activity":
                queue += player_sources(r.text)
            if src.key == API_LEAGUES_KEY:
                queue += league_sources(r.text)
                leagues = parse_api_leagues(r.text, stamp)
                if leagues:
                    league_id = leagues[0]["league_id"]
            if src.key == "api_teams" and league_id:
                queue += offer_sources(r.text, me, league_id)

            sig = src.sign(r.text)
            was = prev.get(src.key, {})
            if sig is None:
                print(f"  warn: {src.key} matched no known markup — stored "
                      f"unconditionally. Check the selectors.")
                rotted += 1
                store[src.key] = r.text
                rows.append({"page": src.key, "sig": "", "stored": stamp,
                             "seen": stamp})
            elif was.get("sig") == sig:
                unchanged += 1
                rows.append({"page": src.key, "sig": sig,
                             "stored": was.get("stored") or "", "seen": stamp})
            else:
                store[src.key] = r.text
                rows.append({"page": src.key, "sig": sig, "stored": stamp,
                             "seen": stamp})
                print(f"  {src.key}: {len(r.text) // 1024}KB")

    rows = carry_matches(rows, prev)

    if not rows:
        sys.exit("ERROR: no page fetched — nothing written.")

    members = {f"{k}.html": v for k, v in store.items()}
    members[MANIFEST] = _manifest_csv(rows)
    _write(dest, members)
    print(f"snapshot: {dest} ({dest.stat().st_size // 1024}KB) — "
          f"{len(store)} stored, {unchanged} unchanged, {skipped} not due"
          + (f", {rotted} ROTTED" if rotted else ""))
    _log_feeds(stamp, timing, fails)
    if timing:
        slow = sorted(timing, reverse=True)[:5]
        print("  slowest: " + ", ".join(
            "%s %.1fs%s" % (k, t, "" if st == 200 else " [%s]" % st)
            for t, k, st in slow))
        print("  fetch %.0fs over %d requests%s"
              % (sum(t for t, _k, _s in timing), len(timing),
                 (" — FAILED: " + ", ".join("%s (%s)" % kv
                                            for kv in fails.items()))
                 if fails else ""))
    return dest


FEEDS = "feeds.csv"
FEED_FIELDS = ["observed_at", "page", "status", "seconds"]


def _log_feeds(stamp: str, timing: list, fails: dict) -> None:
    if not timing:
        return
    TIDY.mkdir(parents=True, exist_ok=True)
    append_csv(TIDY / FEEDS,
               [{"observed_at": stamp, "page": k, "status": st,
                 "seconds": "%.2f" % t} for t, k, st in timing],
               FEED_FIELDS)



def parse() -> None:
    """Rebuild the tidy tables from the snapshot archives.

    THE WHOLE HISTORY, EVERY RUN, is the default and it is deliberate: a
    parser's signature is part of its cache key, so fixing a parser
    invalidates everything it ever produced and the next run re-derives the
    season without anyone remembering to backfill. That property is worth
    real money and it is why this is not simply incremental.

    It is also why it cannot stay unconditional. Measured 2026-09-18 at
    jornada 6 of 38: the cache alone is 368MB resident and holding every
    table before writing any of them adds 476MB, for a peak of 872MB against
    a 900MB MemoryMax -- already throttled on every run, and growing with
    the archive. So the full walk now runs when it CAN change the answer,
    and the tail alone when it provably cannot: same parser signatures, same
    snapshots already folded in, and only new archives at the end. The one
    case that needs the full walk is exactly the case that is detectable.
    """
    walk = doc_keys()
    sigs_now = _parser_sigs(walk)
    state = _load_parse_state()
    stamps = [stamp for stamp, _ in walk]
    routed = set(state.get("stamps") or ())
    tail = [stamp for stamp in stamps if stamp not in routed]
    settled = (routed and routed <= set(stamps)
               and state.get("sigs") == sigs_now
               and _tidy_has(state.get("tables") or ()))
    if settled and not tail:
        # Same parsers, same archives, and the tables were built from exactly
        # these -- so a rebuild would reproduce what is already on disk, at
        # 874MB and ten seconds, to change nothing. A run with no new
        # snapshot is normally a re-run by hand; the daily path always has
        # one, because fetch writes an archive every time.
        print("  nothing new to parse (%d snapshots already folded in)"
              % len(routed))
        return
    if settled and tail:
        if _parse_tail(walk, tail, sigs_now, stamps, state):
            return
        print("  tail append declined — rebuilding in full")
    _parse_everything(walk, sigs_now, stamps)


def _walkable_src(key: str):
    """The parser for `key`, or None if it has none or feeds the points
    table -- points.py walks that feed itself, on its own cache. Three
    walkers here each re-derived this guard separately."""
    src = source_for(key)
    return src if src is not None and src.table != "points" else None


def _parse_tail(walk, tail, sigs_now, stamps, state) -> bool:
    """Fold ONLY the new snapshots into the tables that already exist.

    Returns False rather than guessing whenever the append would not be
    identical to a full rebuild -- an unparsed document, a table that has
    grown a column, a table the state does not know about. The caller then
    does the whole thing. A wrong "yes" here corrupts the season quietly,
    so every doubt resolves to a rebuild.
    """
    tail_set = set(tail)
    want: dict[tuple[str, str], str] = {}
    keyed: list[tuple[str, str, object]] = []
    for stamp, docs in walk:
        if stamp not in tail_set:
            continue
        for key, (ck, origin) in sorted(docs.items()):
            src = _walkable_src(key)
            if src is None:
                continue
            pk = parse_key(ck, src)
            keyed.append((stamp, pk, src))
            want[(origin, key)] = pk

    cache = _cache_lines(set(want.values()))
    need: dict[str, set] = {}
    for (origin, key), pk in want.items():
        if pk not in cache:
            need.setdefault(origin, set()).add(key)

    fresh: dict = {}
    keys = Sigs()
    for origin, key, html in documents(need):
        pk, rows = _parse_one(origin, key, html, keys)
        cache[pk] = rows
        fresh[pk] = rows

    pending: dict[str, list[dict]] = {}
    for stamp, pk, src in keyed:
        if pk not in cache:
            return False          # never seen, never parsed -- rebuild
        route(pending, cache[pk], src.table, stamp)

    known = set(state.get("tables") or ())
    if not set(pending) <= known:
        return False              # a table the tables file has never seen

    for table in sorted(pending):
        daily = STORE_DAILY.get(table)
        if daily:
            if not _append_csv_daily(TIDY / f"{table}.csv", pending[table],
                                     daily):
                return False
        elif not _append_csv(TIDY / f"{table}.csv", pending[table],
                             STORE_ONCE.get(table)):
            return False

    _append_cache_lines(fresh)
    _save_parse_state({"sigs": sigs_now, "stamps": stamps,
                       "tables": sorted(known)})
    print("  tail: %d new snapshot(s), %d document(s) parsed, "
          "%d row(s) appended"
          % (len(tail), len(fresh), sum(len(v) for v in pending.values())))
    return True


def _parser_sigs(walk) -> dict[str, str]:
    """Signature of every parser the archives currently depend on. This is
    the guard: if one of these moves, the rows it produced are stale and the
    only honest answer is to derive the lot again."""
    out: dict[str, str] = {}
    for _stamp, docs in walk:
        for key in docs:
            src = _walkable_src(key)
            if src is None:
                continue
            name = getattr(src.parse, "__name__", "")
            if name and name not in out:
                out[name] = parser_sig(name)
    return out


def _tidy_has(tables) -> bool:
    return bool(tables) and all((TIDY / f"{t}.csv").exists() for t in tables)


def _parse_origin(task) -> dict:
    """One snapshot's needed documents -> {parse_key: rows}. A pure function
    of the snapshot, so a worker opens it ITSELF: only rows cross the pipe,
    never the ~2MB of HTML per page."""
    origin, want = task
    out, sigs = {}, Sigs()
    for o, key, html in documents({origin: want}):
        pk, rows = _parse_one(o, key, html, sigs)
        out[pk] = rows
    return out


def _parse_one(origin: str, key: str, html: str, sigs: "Sigs"
               ) -> tuple[str, list[dict]]:
    src = source_for(key)
    try:
        rows = src.parse(html, origin, key)
    except Exception as e:
        print(f"  warn: {origin}/{key}: {type(e).__name__}: {e}")
        rows = []
    return parse_key(sigs.of(key, html), src), rows


def _parse_workers(misses: int) -> int:
    """1 for the handful of documents a normal run parses (a pool would cost
    more than it saves); otherwise one per physical core, never more than the service's
    own memory cap leaves room for -- a parsing worker holds one ~2MB page and
    its lxml tree, ~200MB, and lfg.service is capped at 750MB with the parent
    already at ~360MB. A cold-cache rebuild (376s serial, 2026-09-24) is the
    only time this matters, and it must not become an OOM kill."""
    if misses < 24:
        return 1
    # PHYSICAL cores, not logical CPUs: lxml is cache/memory bound, and on this
    # box (i5-5200U, 2 cores x 2 threads) measured 2026-09-24 on 674 documents:
    # 1 worker 51.6s, 2 workers 32.1s, 4 workers 41.8s and 80% MORE total CPU.
    n = max(1, (os.cpu_count() or 2) // 2)
    try:
        cg = Path("/sys/fs/cgroup") / (Path("/proc/self/cgroup").read_text()
                                        .strip().split("::")[-1].lstrip("/"))
        cap = (cg / "memory.max").read_text().strip()
        if cap != "max":
            rss = int((cg / "memory.current").read_text())
            n = min(n, max(1, (int(cap) - rss - 100 * 2**20) // (200 * 2**20)))
    except (OSError, ValueError):
        pass
    return n


def _parse_everything(walk, sigs_now, stamps) -> None:
    pending: dict[str, list[dict]] = {}
    cache, fresh = parse_cache(), {}

    need: dict[str, set] = {}
    for stamp, docs in walk:
        for key, (ck, origin) in docs.items():
            src = _walkable_src(key)
            if src is not None and parse_key(ck, src) not in cache:
                need.setdefault(origin, set()).add(key)
    misses = sum(len(v) for v in need.values())
    tasks, n = sorted(need.items()), _parse_workers(misses)
    if n > 1:
        import concurrent.futures as cf
        import multiprocessing as mp
        with cf.ProcessPoolExecutor(n, mp_context=mp.get_context("fork")) as ex:
            for part in ex.map(_parse_origin, tasks):
                cache.update(part)
    else:
        for t in tasks:
            cache.update(_parse_origin(t))

    # ROWS GO TO DISK BEFORE THEY CAN GROW INTO THE PROCESS. Holding every
    # table until the last snapshot was routed cost 476MB on top of the
    # cache, for a peak of 874MB against a 900MB MemoryMax -- and the limit
    # is not going up, so the pipeline has to come down. Buffered in memory
    # up to SPILL_ROWS, appended to a per-table file past it, streamed back
    # one table at a time below. Same rows, same order, a bounded buffer.
    spill = TIDY / ".parse_spill"
    if spill.exists():
        for f in spill.glob("*.jsonl"):
            f.unlink()
    spilled: set[str] = set()
    buffered = 0

    hits = 0
    for stamp, docs in walk:
        for key, (ck, origin) in sorted(docs.items()):
            src = _walkable_src(key)
            if src is None:
                continue
            pk = parse_key(ck, src)
            rows = cache.get(pk, [])
            hits += 1
            fresh[pk] = rows
            route(pending, rows, src.table, stamp)
            buffered += len(rows)
        if buffered >= SPILL_ROWS:
            _spill_out(pending, spill, spilled)
            buffered = 0
    hits -= misses
    save_parse_cache(fresh)
    del cache, fresh
    print("  parsed %d documents, reused %d" % (misses, hits))

    TIDY.mkdir(parents=True, exist_ok=True)
    (TIDY / "probable_xi.csv").unlink(missing_ok=True)

    market_count = 0
    xi_count = 0
    tally: dict[str, int] = {}
    per_source: dict[str, int] = {}
    fixture_count = 0
    played: set = set()
    starters_count = 0
    starters_matches: set = set()

    written: list[str] = []
    for table in sorted(spilled | set(pending)):
        once = STORE_ONCE.get(table)
        daily = STORE_DAILY.get(table)
        seen: set[int] = set()
        w = fh = None
        fieldnames: list = []
        fieldset: set = set()
        n = 0
        rows_iter = _spilled_rows(spill, table, pending.pop(table, []))
        if daily:
            rows_iter = _compact_daily(list(rows_iter), daily)
        for r in rows_iter:
            if once:
                k = tuple((r.get(c) or "") for c in once)
                if all(k):
                    h = _key_hash(k)
                    if h in seen:
                        continue
                    seen.add(h)
            if w is None:
                fieldnames = list(r)
                fieldset = set(fieldnames)
                fh = (TIDY / f"{table}.csv").open(
                    "w", newline="", encoding="utf-8")
                w = csv.writer(fh, lineterminator="\n")
                w.writerow(fieldnames)
            if not r.keys() <= fieldset:
                fh.close()
                raise ValueError("dict contains fields not in fieldnames: "
                                 + ", ".join(repr(x)
                                             for x in r.keys() - fieldset))
            w.writerow([r.get(f, "") for f in fieldnames])
            n += 1
            if table == "lineups":
                tally[r["status"]] = tally.get(r["status"], 0) + 1
                per_source[r["source"]] = per_source.get(r["source"], 0) + 1
            elif table == "matches":
                if r["score"]:
                    played.add(r["match_id"])
            elif table == "starters":
                starters_matches.add(r["match_id"])
        if fh is not None:
            fh.close()
        written.append(table)
        if table == "market":
            market_count = n
        elif table == "lineups":
            xi_count = n
        elif table == "fixtures":
            fixture_count = n
        elif table == "starters":
            starters_count = n
    for f in spill.glob("*.jsonl"):
        f.unlink()

    if not market_count:
        sys.exit("ERROR: market parse produced 0 rows — the markup changed.")

    flags = ", ".join("%s %d" % (k or "not stated", v)
                      for k, v in sorted(tally.items()) if k != "ok")
    by_src = ", ".join("%s %d" % (k, v) for k, v in sorted(per_source.items()))
    print(f"market {market_count} rows, lineups {xi_count} rows "
          f"({by_src}), fixtures {fixture_count} rows")
    print("  played %d matches, starters %d rows for %d of them"
          % (len(played), starters_count, len(starters_matches)))
    print("  status: ok %d%s" % (tally.get("ok", 0),
                                 (", " + flags) if flags else ""))
    if not flags:
        print("  warn: no player flagged in any snapshot — if the site still "
              "shows injuries, the fitness selectors have rotted.")

    # LAST, and only on the path that actually derived everything. A state
    # written before the canary above would let a run that bailed on empty
    # market rows still licence tomorrow's tail append.
    _save_parse_state({"sigs": sigs_now, "stamps": stamps, "tables": written})


_CACHE = "parsed.json"


class Sigs:

    def __init__(self) -> None:
        self._at: dict[tuple, str] = {}

    def of(self, page: str, html: str) -> str:
        k = (page, len(html), hash(html))
        ck = self._at.get(k)
        if ck is None:
            ck = self._at[k] = "%s:%s" % (page, hashlib.blake2b(
                html.encode("utf-8", "replace"), digest_size=12).hexdigest())
        return ck


_SIG_CACHE: dict[str, str] = {}


def parse_key(content_key: str, src) -> str:
    name = getattr(src.parse, "__name__", "")
    sig = _SIG_CACHE.get(name)
    if sig is None:
        sig = _SIG_CACHE[name] = parser_sig(name)
    return "%s@%s" % (content_key, sig)


_STATE = "parse_state.json"


def _lines_name(name: str) -> str:
    """The line-file that stands in for a given blob cache.

    THIS TAKES THE NAME, and that is the whole point of it. There are two
    caches -- parsed.json for the pages and parsed_points.json for the
    points, points.py passing its own name to the same helpers -- and the
    first version of the line format ignored the argument and wrote both to
    one file. points then read the pages' cache, missed everything, and
    overwrote it with its own 88 entries, so every full walk re-parsed all
    3,383 documents: 395s instead of 11s. It looked like a slow rebuild
    rather than a broken cache, which is how it survived a commit.
    """
    return (name[:-5] if name.endswith(".json") else name) + ".jsonl"


def _load_parse_state(name: str = _STATE) -> dict:
    try:
        blob = json.loads((TIDY / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return blob if isinstance(blob, dict) else {}


def _save_parse_state(state: dict, name: str = _STATE) -> None:
    TIDY.mkdir(parents=True, exist_ok=True)
    try:
        (TIDY / name).write_text(json.dumps(state), encoding="utf-8")
    except OSError:
        pass


def _cache_lines(keys: set, name: str = _CACHE) -> dict:
    """The cached rows for JUST these keys, read a line at a time.

    The point of the line format is that a tail append needs a few hundred
    documents out of several thousand, and json.load() of the single-blob
    cache costs 368MB to get them. Streaming keeps only what was asked for.
    """
    out: dict = {}
    try:
        fh = (TIDY / _lines_name(name)).open(encoding="utf-8")
    except OSError:
        return out
    with fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            k = rec.get("k")
            if k in keys:
                out[k] = rec.get("r") or []
    return out


def _append_cache_lines(docs: dict, name: str = _CACHE) -> None:
    if not docs:
        return
    TIDY.mkdir(parents=True, exist_ok=True)
    try:
        with (TIDY / _lines_name(name)).open("a", encoding="utf-8") as fh:
            for k, rows in docs.items():
                fh.write(json.dumps({"k": k, "r": rows}) + "\n")
    except OSError:
        pass


def parse_cache(name: str = _CACHE) -> dict:
    """Every cached document. The line file is the live format; the single
    blob is read once, on the first run after the change, and then replaced
    by save_parse_cache below."""
    lines = TIDY / _lines_name(name)
    if lines.exists():
        out: dict = {}
        try:
            with lines.open(encoding="utf-8") as fh:
                for line in fh:
                    if not line.strip():
                        continue
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    if rec.get("k"):
                        out[rec["k"]] = rec.get("r") or []
        except OSError:
            return {}
        return out
    try:
        blob = json.loads((TIDY / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return blob.get("docs", {})


def save_parse_cache(docs: dict, name: str = _CACHE) -> None:
    """Rewrite the whole cache, pruned to what the walk actually used. Only
    the full path calls this -- a tail append has read a handful of entries
    and must never write its own view back as though it were the lot."""
    TIDY.mkdir(parents=True, exist_ok=True)
    tmp = TIDY / (_lines_name(name) + ".new")
    try:
        with tmp.open("w", encoding="utf-8") as fh:
            for k, rows in docs.items():
                fh.write(json.dumps({"k": k, "r": rows}) + "\n")
        tmp.replace(TIDY / _lines_name(name))
        (TIDY / name).unlink(missing_ok=True)
    except OSError:
        tmp.unlink(missing_ok=True)


def route(tables: dict, rows: list[dict], default: str, stamp: str) -> None:
    for r in rows:
        table = r.get(ROW_TABLE) or default
        d = dict(r)
        d.pop(ROW_TABLE, None)
        d["observed_at"] = stamp
        tables.setdefault(table, []).append(d)


def first_seen(rows: list[dict], key: tuple) -> list[dict]:
    out, seen = [], set()
    for r in rows:
        k = tuple((r.get(c) or "") for c in key)
        if all(k):
            if k in seen:
                continue
            seen.add(k)
        out.append(r)
    return out


SPILL_ROWS = 120_000


def _spill_out(pending: dict, root: Path, spilled: set) -> None:
    """Append what is buffered to per-table files and let go of it."""
    root.mkdir(parents=True, exist_ok=True)
    for table, rows in pending.items():
        if not rows:
            continue
        with (root / f"{table}.jsonl").open("a", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")
        spilled.add(table)
    pending.clear()


def _spilled_rows(root: Path, table: str, tail: list):
    """Everything routed to this table, spilled part first, in walk order --
    which is the order first_seen() depends on, so "as dealt" still means
    the first snapshot that carried the row."""
    path = root / f"{table}.jsonl"
    if path.exists():
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    yield json.loads(line)
    for r in tail:
        yield r


def _compact_daily(rows: list[dict], key_cols) -> list[dict]:
    """One row per (key, day): later rows for the same key on the same day
    overwrite the earlier one in place rather than adding a new row. `day`
    is observed_at's first 10 characters (observed_at is UTC, see
    ffcore.tidy.shown), and the group keeps its FIRST position so output
    order stays the original interleaving, just with reruns collapsed.
    A row missing any key column passes through uncompacted -- same
    all-or-nothing rule STORE_ONCE uses, so a blank id never merges rows.
    """
    out: list[dict] = []
    pos: dict[tuple, int] = {}
    for r in rows:
        k = tuple((r.get(c) or "") for c in key_cols)
        if not all(k):
            out.append(r)
            continue
        gk = (k, (r.get("observed_at") or "")[:10])
        i = pos.get(gk)
        if i is None:
            pos[gk] = len(out)
            out.append(r)
        else:
            out[i] = r
    return out


def _append_csv_daily(path: Path, rows: list[dict], key_cols) -> bool:
    """_append_csv's STORE_DAILY counterpart: a key's row for today is
    overwritten rather than appended, so N rounds in one day net one row
    per key. Needs the whole file (already ~6x smaller for a compacted
    table) since overwriting a mid-file row means rewriting the file.
    """
    if not rows:
        return True
    if not path.exists():
        _write_csv(path, _compact_daily(rows, key_cols))
        return True
    try:
        with path.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            header = reader.fieldnames
            if header is None:
                return False
            existing = list(reader)
    except OSError:
        return False
    fieldset = set(header)
    for r in rows:
        if not set(r) <= fieldset:
            return False          # shape moved -- a rebuild, not a guess
    _write_csv(path, _compact_daily(existing + rows, key_cols))
    return True


def _append_csv(path: Path, rows: list[dict], once_key=None) -> bool:
    """Append rows to a table that already exists, or say no.

    Refuses on a header that does not match exactly -- a new or missing
    column means the shape changed, and appending under the old header would
    silently drop or misalign a field. The caller rebuilds instead.

    STORE_ONCE tables are deduped against what is already on disk, which is
    the whole point of "as dealt": the same api_stats row is re-reported in
    every later snapshot. The existing keys are held as 64-bit hashes rather
    than tuples -- 710,850 rows of api_stats is ~23MB that way against
    ~230MB of Python tuples, and this exists to save memory.
    """
    if not rows:
        return True
    if not path.exists():
        _write_csv(path, rows)
        return True
    try:
        with path.open(newline="", encoding="utf-8") as fh:
            reader = csv.reader(fh)
            header = next(reader, None)
            if header is None:
                return False
            seen: set[int] = set()
            if once_key:
                try:
                    idx = [header.index(c) for c in once_key]
                except ValueError:
                    return False
                for row in reader:
                    k = tuple(row[i] if i < len(row) else "" for i in idx)
                    if all(k):
                        seen.add(_key_hash(k))
    except OSError:
        return False

    fieldset = set(header)
    add = []
    for r in rows:
        if not set(r) <= fieldset:
            return False          # shape moved -- a rebuild, not a guess
        if once_key:
            k = tuple((r.get(c) or "") for c in once_key)
            if all(k):
                h = _key_hash(k)
                if h in seen:
                    continue
                seen.add(h)
        add.append([r.get(f, "") for f in header])
    if not add:
        return True
    try:
        with path.open("a", newline="", encoding="utf-8") as fh:
            csv.writer(fh, lineterminator="\n").writerows(add)
    except OSError:
        return False
    return True


def _key_hash(k: tuple) -> int:
    return int.from_bytes(hashlib.blake2b(
        "\x00".join(k).encode("utf-8", "replace"), digest_size=8).digest(),
        "big")


def _write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    fieldnames = list(rows[0])
    fieldset = set(fieldnames)
    out = []
    for r in rows:
        if not r.keys() <= fieldset:
            raise ValueError("dict contains fields not in fieldnames: " +
                             ", ".join(repr(x) for x in r.keys() - fieldset))
        out.append([r.get(f, "") for f in fieldnames])
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(fieldnames)
        w.writerows(out)



def baseline(url: str = "", label: str = "") -> None:
    import httpx

    from sources import POINTS_URL
    url = url or POINTS_URL

    with httpx.Client(headers=HEADERS, timeout=45,
                      follow_redirects=True) as c:
        r = c.get(url)
        r.raise_for_status()
        html = r.text

    label = re.sub(r"[^0-9A-Za-z._-]", "", label or season_label(html)) or "unknown"

    _write(RAW / f"season={label}.tar.xz",
           {"points.html": html,
            MANIFEST: _manifest_csv([{"page": "points", "sig": "",
                                      "stored": label, "seen": label}])})

    rows = parse_points(html)
    if not rows:
        sys.exit("PARSE FAILED — no table matched, so nothing was written and "
                 "the last good file is untouched. The markup has probably "
                 "changed: fix sources.parse_points.")

    _write_points_csv(rows, label, url)


POINTS_FIELDS = ["player_name", "player_name_full", "team", "points",
                 "games", "avg", "ff_id", "season", "observed_at",
                 "source_url"]


def _write_points_csv(rows: list[dict], label: str, url: str) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    out = SEASON / f"points_{label}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=POINTS_FIELDS)
        w.writeheader()
        for row in rows:
            row.update(season=label, observed_at=stamp, source_url=url)
            w.writerow(row)

    played = sum(1 for r in rows if (r["games"] or "0") != "0")
    print(f"wrote {SEASON / f'points_{label}.csv'} — {len(rows)} players, "
          f"{played} with minutes, season label '{label}'")
    print("Spot-check a few names against the app before trusting the report.")



def prune(apply: bool = False) -> None:
    snaps = snapshots()
    if not snaps:
        sys.exit("nothing under data/raw")

    prev: dict[str, str] = {}
    stored_at: dict[str, str] = {}
    plan: list[tuple[Path, dict[str, str], list[dict], int]] = []
    seen_pages = kept_pages = rotted = 0

    for snap in snaps:
        stamp = _stamp_of(snap)
        members = _read(snap)
        html = {k.removesuffix(".html"): v for k, v in members.items()
                if k.endswith(".html")}
        rows, store = [], {}
        for page in [r["page"] for r in _manifest(members)]:
            body = html.get(page)
            if body is None:
                continue
            seen_pages += 1
            src = source_for(page)
            sig = src.sign(body) if src else None
            if sig is None:
                rotted += 1
                store[page] = body
                rows.append({"page": page, "sig": "", "stored": stamp,
                             "seen": stamp})
            elif prev.get(page) == sig:
                rows.append({"page": page, "sig": sig,
                             "stored": stored_at[page], "seen": stamp})
            else:
                store[page] = body
                rows.append({"page": page, "sig": sig, "stored": stamp,
                             "seen": stamp})
                stored_at[page] = stamp
            prev[page] = sig if sig is not None else prev.get(page)
        kept_pages += len(store)
        plan.append((snap, store, rows, len(html)))

    before = sum(f.stat().st_size for f in RAW.rglob("*") if f.is_file())
    print(f"{len(snaps)} snapshots, {seen_pages} stored pages -> "
          f"{kept_pages} kept ({100 * (1 - kept_pages / seen_pages):.0f}% "
          f"dropped as unchanged)"
          + (f", {rotted} kept because selectors matched nothing" if rotted
             else ""))

    if not apply:
        print(f"currently {before / 1e6:.1f} MB. Dry run — nothing written. "
              f"Re-run with --apply.")
        return

    for snap, store, rows, had in plan:
        stamp = _stamp_of(snap)
        members = {f"{k}.html": v for k, v in store.items()}
        members[MANIFEST] = _manifest_csv(rows)
        _write(RAW / f"dt={stamp}.tar.xz", members)
        if snap.is_dir():
            shutil.rmtree(snap)
        print(f"  {stamp}: {had} pages -> {len(store)} stored")

    after = sum(f.stat().st_size for f in RAW.rglob("*") if f.is_file())
    print(f"data/raw {before / 1e6:.1f} MB -> {after / 1e6:.1f} MB. "
          f"The pages dropped are still in git history; nothing is "
          f"unrecoverable.")



def _selftest() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "dt=2026-01-01T0000Z.tar.xz"
        members = {"market.html": "<html>a</html>",
                   "team_celta.html": "<html>b</html>",
                   MANIFEST: _manifest_csv(
                       [{"page": "market", "sig": "s1",
                         "stored": "2026-01-01T0000Z", "seen": "2026-01-01T0000Z"},
                        {"page": "team_celta", "sig": "s2",
                         "stored": "2026-01-01T0000Z", "seen": "2026-01-01T0000Z"}])}
        _write(p, members)
        assert _read(p) == members, _read(p)

        q = Path(tmp) / "dt=2026-01-02T0000Z.tar.xz"
        _write(q, members)
        assert p.read_bytes() == q.read_bytes(), "archive not reproducible"

    import io as _io
    import contextlib

    global RAW, TIDY
    _raw, _tidy = RAW, TIDY
    with tempfile.TemporaryDirectory() as tmp:
        RAW, TIDY = Path(tmp) / "raw", Path(tmp) / "tidy"
        RAW.mkdir(parents=True)
        man = lambda *ps: _manifest_csv(                        # noqa: E731
            [{"page": p, "sig": "s", "stored": "t", "seen": "t"} for p in ps])
        _write(RAW / "dt=2026-01-01T0000Z.tar.xz",
               {"market.html": "<html>a</html>", MANIFEST: man("market")})
        _write(RAW / "dt=2026-01-02T0000Z.tar.xz", {MANIFEST: man("market")})

        cold = doc_keys()
        assert [st for st, _ in cold] == ["2026-01-01T0000Z",
                                          "2026-01-02T0000Z"], cold
        assert cold[1][1]["market"] == cold[0][1]["market"], "carry-forward lost"
        assert cold[1][1]["market"][1] == "2026-01-01T0000Z", "wrong origin"

        buf = _io.StringIO()
        with contextlib.redirect_stdout(buf):
            warm = doc_keys()
        assert warm == cold, (warm, cold)
        assert "read" not in buf.getvalue(), "archives re-read on a warm index"

        _write(RAW / "dt=2026-01-02T0000Z.tar.xz",
               {"market.html": "<html>bb</html>", MANIFEST: man("market")})
        buf = _io.StringIO()
        with contextlib.redirect_stdout(buf):
            again = doc_keys()
        assert "read 1 of 2" in buf.getvalue(), buf.getvalue()
        assert again[1][1]["market"] != cold[1][1]["market"], again

        got = list(documents({"2026-01-01T0000Z": {"market"}}))
        assert got == [("2026-01-01T0000Z", "market", "<html>a</html>")], got
    RAW, TIDY = _raw, _tidy

    rows = [{"page": "market", "sig": "s1", "stored": "t0", "seen": "t1"}]
    assert _manifest({MANIFEST: _manifest_csv(rows)}) == rows

    legacy = _manifest({"market.html": "x", "team_celta.html": "y"})
    assert [r["page"] for r in legacy] == ["market", "team_celta"], legacy

    ev = [{"activity_id": "a1", "at": "2026-08-15T22:24", "observed_at": "t1"},
          {"activity_id": "a2", "at": "2026-08-16T09:00", "observed_at": "t1"},
          {"activity_id": "a1", "at": "2026-08-15T22:24", "observed_at": "t2"},
          {"activity_id": "a3", "at": "2026-08-17T11:00", "observed_at": "t2"}]
    once_only = first_seen(ev, ("activity_id",))
    assert [r["activity_id"] for r in once_only] == ["a1", "a2", "a3"], once_only
    assert once_only[0]["observed_at"] == "t1", once_only[0]
    assert [r["observed_at"] for r in once_only] == ["t1", "t1", "t2"]
    odd = first_seen([{"activity_id": "", "at": "x", "observed_at": "t1"},
                      {"activity_id": "", "at": "y", "observed_at": "t2"}],
                     ("activity_id",))
    assert len(odd) == 2, odd
    pairs = first_seen([{"a": "1", "b": "1"}, {"a": "1", "b": "2"},
                        {"a": "1", "b": "1"}], ("a", "b"))
    assert len(pairs) == 2, pairs
    assert first_seen([], ("activity_id",)) == []
    assert set(STORE_ONCE) == {"api_activity", "api_players",
                              "api_stats", "results_history"}, STORE_ONCE
    assert "api_teams" not in STORE_ONCE and "market" not in STORE_ONCE
    assert set(STORE_DAILY) == {"market", "lineups",
                                "understat_players"}, STORE_DAILY
    assert not set(STORE_ONCE) & set(STORE_DAILY), \
        "a table cannot be both keep-first-forever and one-per-day"
    line = {"player_id": "1337", "week": "1", "stat": "goals",
            "value": "1", "points": "4", "observed_at": "t1"}
    again = dict(line, observed_at="t2")
    fixed = dict(line, points="6", observed_at="t3")
    kept = first_seen([line, again, fixed], STORE_ONCE["api_stats"])
    assert [r["observed_at"] for r in kept] == ["t1", "t3"], kept

    # APPENDING THE TAIL MUST EQUAL REBUILDING THE LOT, or it is not an
    # optimisation, it is data loss on a schedule. _append_csv says no rather
    # than guessing, and every no sends the caller back to the full walk.
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "t.csv"
        _write_csv(f, [{"a": "1", "b": "x"}])
        assert _append_csv(f, [{"a": "2", "b": "y"}])
        assert f.read_text(encoding="utf-8") == "a,b\n1,x\n2,y\n", \
            f.read_text(encoding="utf-8")
        # A ROW WITH FEWER FIELDS is what _write_csv already tolerates, so
        # the appender must too -- refusing here would rebuild for ever and
        # the tail path would never once run.
        assert _append_csv(f, [{"a": "3"}])
        assert f.read_text(encoding="utf-8").endswith("3,\n")
        # A NEW COLUMN is a shape change: appending under the old header
        # would drop it silently, so this is a refusal, not a best effort.
        assert not _append_csv(f, [{"a": "4", "b": "z", "c": "new"}])
        assert _append_csv(f, []), "nothing to add is not a failure"
        missing = Path(tmp) / "fresh.csv"
        assert _append_csv(missing, [{"a": "1"}]) and missing.exists(), \
            "no file yet is a write, not a refusal"

        # STORE_ONCE across the append boundary: the same api_stats line is
        # re-reported by every later snapshot, so what is already on disk has
        # to be deduped against, not just what is in this batch.
        once = Path(tmp) / "api_stats.csv"
        _write_csv(once, [line])
        assert _append_csv(once, [again, fixed], STORE_ONCE["api_stats"])
        body = once.read_text(encoding="utf-8").strip().splitlines()
        assert len(body) == 3, body            # header + t1 + t3, never t2
        assert body[-1].endswith("t3"), body
        assert _append_csv(once, [dict(again)], STORE_ONCE["api_stats"])
        assert len(once.read_text(encoding="utf-8").strip().splitlines()) == 3

        # STORE_DAILY: same key, same day, three rounds -- one row, the
        # last round's data, in the FIRST round's position.
        m1 = {"observed_at": "2026-09-20T0900Z", "ff_id": "1", "value": "10"}
        m2 = {"observed_at": "2026-09-20T1300Z", "ff_id": "1", "value": "10"}
        m3 = {"observed_at": "2026-09-20T1800Z", "ff_id": "1", "value": "11"}
        other = {"observed_at": "2026-09-20T0900Z", "ff_id": "2", "value": "5"}
        got = _compact_daily([m1, other, m2, m3], ("ff_id",))
        assert got == [m3, other], got
        # A new day for the same key is a new row, not an overwrite.
        m4 = {"observed_at": "2026-09-21T0900Z", "ff_id": "1", "value": "11"}
        assert _compact_daily([m1, m4], ("ff_id",)) == [m1, m4]
        # A blank key column passes through uncompacted rather than merging
        # with every other blank-keyed row.
        blank = {"observed_at": "2026-09-20T0900Z", "ff_id": "", "value": "x"}
        assert _compact_daily([blank, dict(blank)], ("ff_id",)) == \
            [blank, dict(blank)]

        daily = Path(tmp) / "market.csv"
        assert _append_csv_daily(daily, [m1], ("ff_id",)) and daily.exists()
        assert daily.read_text(encoding="utf-8") == \
            "observed_at,ff_id,value\n2026-09-20T0900Z,1,10\n"
        # Same day, no change -- overwrites the existing line in place.
        assert _append_csv_daily(daily, [m2], ("ff_id",))
        assert daily.read_text(encoding="utf-8") == \
            "observed_at,ff_id,value\n2026-09-20T1300Z,1,10\n"
        # Same day, value moved -- still one line, now the new value.
        assert _append_csv_daily(daily, [m3], ("ff_id",))
        assert daily.read_text(encoding="utf-8") == \
            "observed_at,ff_id,value\n2026-09-20T1800Z,1,11\n"
        # A new day appends a second line rather than overwriting the first.
        assert _append_csv_daily(daily, [m4], ("ff_id",))
        assert daily.read_text(encoding="utf-8") == (
            "observed_at,ff_id,value\n"
            "2026-09-20T1800Z,1,11\n2026-09-21T0900Z,1,11\n"), \
            daily.read_text(encoding="utf-8")
        # Shape moved -- refuse, same as _append_csv, so the caller rebuilds.
        assert not _append_csv_daily(
            daily, [dict(m4, extra="z")], ("ff_id",))

    # TWO CACHES, TWO FILES. points.py passes its own name to these same
    # helpers; a line file that ignored the argument sent both to one path,
    # so points read the pages' cache, missed every entry and then wrote its
    # own 88 over the top -- and every full walk re-parsed all 3,383
    # documents at 395s instead of 11s, looking like slowness rather than a
    # broken cache.
    assert _lines_name("parsed.json") == "parsed.jsonl"
    assert _lines_name("parsed_points.json") == "parsed_points.jsonl"
    assert _lines_name("parsed.json") != _lines_name("parsed_points.json")

    assert _key_hash(("a", "b")) == _key_hash(("a", "b"))
    # The separator matters: without it ("ab","c") and ("a","bc") collide,
    # and a collision here drops a row that should have been kept.
    assert _key_hash(("ab", "c")) != _key_hash(("a", "bc"))

    assert not _tidy_has(()), "no tables recorded is not a licence to append"
    assert not _tidy_has(("no_such_table_here",))

    out: dict[str, list] = {}
    route(out, [{"a": "1"}, {ROW_TABLE: "api_stats", "stat": "goals"}],
          "api_teams", "t1")
    assert set(out) == {"api_teams", "api_stats"}, list(out)
    assert out["api_teams"][0] == {"a": "1", "observed_at": "t1"}
    assert out["api_stats"][0]["observed_at"] == "t1"
    route(out, [{ROW_TABLE: "api_teams", "b": "2"}], "api_teams", "t2")
    assert len(out["api_teams"]) == 2, out["api_teams"]
    assert all(ROW_TABLE not in r for rs in out.values() for r in rs), out

    from sources import Source, parse_market, sign_market
    every = Source("m", "market", "u", parse_market, sign_market, "every_run")
    daily = Source("m", "market", "u", parse_market, sign_market, "daily")
    seen_today = {"m": {"seen": "2026-08-15T0940Z"}}
    assert due(every, seen_today, "2026-08-15")
    assert not due(daily, seen_today, "2026-08-15")
    assert due(daily, seen_today, "2026-08-16")
    assert due(daily, {}, "2026-08-15")

    twice = Source("m", "market", "u", parse_market, sign_market, "twice_daily")
    assert not due(twice, {"m": {"seen": "2026-08-15T0940Z"}},
                   "2026-08-15T1200Z")
    assert due(twice, {"m": {"seen": "2026-08-15T0940Z"}},
               "2026-08-15T1600Z")
    assert due(twice, {}, "2026-08-15T0000Z")

    # -- _by_host: hosts interleaved so their per-host delays overlap, and
    # each host's own order kept (a source can depend on an earlier one).
    mk = lambda k, h: Source(k, "t", "https://%s/%s" % (h, k), None, None,
                             "every_run")
    got = [x.key for x in _by_host([mk("a1", "a"), mk("a2", "a"),
                                    mk("a3", "a"), mk("b1", "b"),
                                    mk("b2", "b")])]
    assert got == ["a1", "b1", "a2", "b2", "a3"], got
    assert not due(twice, {"m": {"seen": "2026-08-15T2340Z"}},
                   "2026-08-16T0005Z")

    from sources import match_source
    once = match_source("match_22421-alaves-getafe")
    assert due(once, {}, "2026-08-15")
    assert not due(once, {once.key: {"seen": "2026-08-15T0940Z"}}, "2026-08-16")

    prev = {"market": {"page": "market", "sig": "s", "stored": "t0",
                       "seen": "t0"},
            once.key: {"page": once.key, "sig": "s", "stored": "t0",
                       "seen": "t0"}}
    carried = carry_matches(
        [{"page": "market", "sig": "s2", "stored": "t1", "seen": "t1"}], prev)
    assert [r["page"] for r in carried] == ["market", once.key], carried
    assert carried[1]["stored"] == "t0" and carried[0]["sig"] == "s2"
    assert len(carry_matches(carried, prev)) == 2

    assert _stamp_of(Path("data/raw/dt=2026-08-15T0940Z.tar.xz")) \
        == _stamp_of(Path("data/raw/dt=2026-08-15T0940Z")) == "2026-08-15T0940Z"

    print("ingest.py selftest OK (24 cases)")


if __name__ == "__main__":
    argv = sys.argv[1:]
    cmd = argv[0] if argv else "fetch"
    if cmd in ("--selftest", "selftest"):
        _selftest()
    elif cmd == "prune":
        prune(apply="--apply" in argv)
    elif cmd == "baseline":
        url = argv[argv.index("--url") + 1] if "--url" in argv else ""
        label = argv[argv.index("--label") + 1] if "--label" in argv else ""
        baseline(url, label)
    elif cmd in ("fetch", "parse"):
        {"fetch": fetch, "parse": parse}[cmd]()
    else:
        sys.exit(__doc__)
