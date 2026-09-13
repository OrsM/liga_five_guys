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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ffcore.auth import API_BASE                    # noqa: E402
from ffcore.league import load_config                # noqa: E402
from ffcore.tidy import ROOT, SEASON, TIDY, append_csv  # noqa: E402
from sources import (API_LEAGUES_KEY, CAL_KEY, MATCH_KEY_RE,  # noqa: E402
                     ROW_TABLE, STORE_ONCE, league_sources, offer_sources,
                     parse_api_leagues, parse_points, parser_sig,
                     played_sources, player_sources, season_label,
                     source_for, sources)

RAW = ROOT / "raw"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.5",
}

# One sweep per run, sequential, with a human-ish gap. Someone maintains this
# site for free; don't make them regret leaving it open.
DELAY = (1.5, 3.0)
TIMEOUT = 30.0

MANIFEST = "MANIFEST.csv"
MANIFEST_FIELDS = ["page", "sig", "stored", "seen"]


# ---------------------------------------------------------------------------
# the raw store
# ---------------------------------------------------------------------------

def _stamp_of(path: Path) -> str:
    """dt=2026-08-15T0940Z.tar.xz -> 2026-08-15T0940Z, for either layout."""
    return path.name.removeprefix("dt=").removesuffix(".tar.xz")


def snapshots() -> list[Path]:
    """Every snapshot, oldest first. Archives and pre-migration directories
    both appear, so a half-finished `prune` still parses."""
    found = list(RAW.glob("dt=*.tar.xz")) + [p for p in RAW.glob("dt=*")
                                             if p.is_dir()]
    return sorted(found, key=_stamp_of)


def _read(path: Path, only: set | None = None) -> dict[str, str]:
    """{member name: text} for one snapshot, whichever layout it is in.

    `only` is the set of members worth decoding, MANIFEST.csv aside — a
    reader that wants one page of forty pays for the archive's
    decompression either way, but not for decoding pages it will discard.
    """
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
    """One tar.xz, byte-reproducible: same content in, same bytes out —
    mtime/ownership are zeroed so an unchanged snapshot never becomes a
    new git blob.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with lzma.open(tmp, "wb", preset=6) as xz:
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
        # A pre-migration directory: what is on disk is what was observed.
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
    """{page: manifest row} as of the newest snapshot — what fetch compares to."""
    snaps = snapshots()
    if not snaps:
        return {}
    return {r["page"]: r for r in _manifest(_read(snaps[-1]))}


def pages(only: set | None = None):
    """(stamp, {page: html}) per snapshot, oldest first, carrying a page
    forward across snapshots that didn't re-store it. `only` narrows to
    the pages named.
    """
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
    """[(stamp, {page: (content key, the stamp that stored it)})], oldest
    first — the same sequence pages() yields, named rather than read.

    Cached in data/tidy/snapindex.json and validated by file size, so a
    run with one new snapshot costs one archive read, not the whole
    history re-decoded. Callers get keys, not text; documents() reads
    text only for a document whose parse isn't already cached.
    """
    idx, out, carried, keys = _index_load(), [], {}, _Sigs()
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
    """Yield (stamp, page, html) for the documents named in {stamp: {page}}.
    Batched per snapshot so a cold cache doesn't reopen the same archive
    once per document.
    """
    for snap in snapshots():
        stamp = _stamp_of(snap)
        want = need.get(stamp)
        if not want:
            continue
        for name, html in _read(snap, {"%s.html" % p for p in want}).items():
            if name.endswith(".html"):
                yield stamp, name.removesuffix(".html"), html


# ---------------------------------------------------------------------------
# fetch
# ---------------------------------------------------------------------------

# How long a "twice_daily" page may go unasked, so both scheduled sweeps
# (~11h apart) each get their own reading.
TWICE_DAILY_HOURS = 6.0


def due(src, prev: dict, now: str) -> bool:
    """Should this sweep request `src`? `now` is this sweep's stamp.

    "daily": no if already requested today. "twice_daily": no until
    TWICE_DAILY_HOURS have passed since it was last asked — bounded by
    elapsed hours, not the calendar, since a page can move mid-day and a
    date change alone shouldn't gate it. "once": no once we have it at
    all (e.g. a confirmed lineup, unchanged after kickoff).
    """
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
    """Hours from one sweep stamp to another, or None if either is
    unreadable — treated as "ask", since one extra fetch is cheaper than
    missing a day.
    """
    from ffcore.tidy import snapshot_stamp

    a, b = snapshot_stamp(then), snapshot_stamp(now)
    if a is None or b is None:
        return None
    return (b - a).total_seconds() / 3600.0


def carry_matches(rows: list[dict], prev: dict) -> list[dict]:
    """`rows` plus every match page the last manifest knew and this one
    missed — match pages are fetched once ever, so a manifest that
    dropped one would re-request it for the rest of the season.
    """
    have = {r["page"] for r in rows}
    return rows + [dict(r) for page, r in prev.items()
                   if MATCH_KEY_RE.match(page) and page not in have]


def _odds_api_key() -> str | None:
    """The Odds API credential, or None — never in source, never
    committed. `ODDS_API_KEY` env var first, else `.odds_api_key` at the
    repo root (gitignored).
    """
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
    # Per-request timing, so a slow or failing source is visible in the log.
    timing: list[tuple] = []
    fails: dict[str, str] = {}

    # One token for the whole sweep, fetched up front so an expired login
    # fails loudly once rather than mid-queue. Not fatal: public scrapers
    # still work without it.
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

    # Same "missing credential degrades to no fetch" shape as the league
    # bearer above, for the odds source.
    odds_key = _odds_api_key()
    if odds_key is None:
        print("  note: no Odds API key (.odds_api_key or ODDS_API_KEY); "
              "the odds source will be skipped.")

    with httpx.Client(headers=HEADERS, timeout=TIMEOUT,
                      follow_redirects=True) as c:
        # A queue, not a fixed loop: the calendar adds match pages to it that
        # aren't knowable until it's been read.
        queue = list(sources())
        # Captured off api_leagues the moment it answers — offer_sources()
        # needs the league id and api_teams carries none.
        league_id = None
        me = load_config().me
        while queue:
            src = queue.pop(0)
            if not due(src, prev, stamp):
                if src.key in prev:
                    rows.append(dict(prev[src.key]))     # carried, not fetched
                skipped += 1
                continue
            # No key -> nothing to ask for; skip before a blank apiKey=
            # wastes a slot on a guaranteed 401.
            if src.key == "odds" and odds_key is None:
                skipped += 1
                continue
            # {date}/{base}/{odds_key} are filled for the sources whose URL
            # needs them; a no-op slot for every other URL.
            url = src.url.format(date=stamp[:10], base=API_BASE,
                                 odds_key=odds_key or "")
            # The bearer goes ONLY on entries that asked for it — sending it
            # to a third party would leak the league account's credential.
            extra = {}
            if src.auth:
                if bearer is None:
                    print(f"  warn: {src.key} needs the league token and "
                          f"there is none — skipping. "
                          f"Run `python -m ffcore.auth --login`.")
                    continue
                extra["Authorization"] = f"Bearer {bearer}"
            t0 = time.monotonic()
            kw = {"headers": extra} if extra else {}
            if src.timeout is not None:
                kw["timeout"] = src.timeout
            try:
                # A source with a body wants its own POST endpoint (e.g.
                # Understat's player-stats); everything else is a GET.
                r = (c.post(url, data=src.body, **kw) if src.body is not None
                    else c.get(url, **kw))
            except httpx.RequestError as e:
                timing.append((time.monotonic() - t0, src.key, "FAILED"))
                fails[src.key] = type(e).__name__
                print(f"  warn: {type(e).__name__} on {src.key}, skipping")
                continue
            timing.append((time.monotonic() - t0, src.key, r.status_code))
            if r.status_code in (403, 429):
                # Stop the whole run rather than retrying into a harder block.
                sys.exit(f"{r.status_code} on {url} — backing off, "
                         f"run again later. Nothing was written.")
            if r.status_code != 200:
                print(f"  warn: {r.status_code} on {src.key}, skipping")
                continue
            if src.key == CAL_KEY:
                # Only matches the calendar shows a score for have a lineup.
                queue += played_sources(r.text)
            if src.table == "api_activity":
                # Names players only by id, half of them since sold — one
                # lookup each, deduplicated by the "once" cadence.
                queue += player_sources(r.text)
            if src.key == API_LEAGUES_KEY:
                # This page carries the league id the market/squad/activity
                # URLs need, discovered here rather than read from config.
                queue += league_sources(r.text)
                leagues = parse_api_leagues(r.text, stamp)
                if leagues:
                    league_id = leagues[0]["league_id"]
            if src.key == "api_teams" and league_id:
                # Received offers, one lookup per player YOU hold — filtered
                # to `me`'s roster (a rival's playerTeamId 403s).
                queue += offer_sources(r.text, me, league_id)

            sig = src.sign(r.text)
            was = prev.get(src.key, {})
            if sig is None:
                # Selectors matched nothing — store unconditionally, never
                # dedup, so the rot itself stays visible.
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
            # The gap is for the scraped sites only (keyed on `auth`) —
            # someone maintains futbolfantasy for free; the league's own API
            # is this account asking about itself and needs no politeness pause.
            if not src.auth:
                time.sleep(random.uniform(*DELAY))

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
    """One row per request per sweep, appended — lets the report show how
    stale each feed's last real answer is, since a failed fetch otherwise
    leaves the last good rows looking current to every downstream reader.
    """
    if not timing:
        return
    TIDY.mkdir(parents=True, exist_ok=True)
    append_csv(TIDY / FEEDS,
               [{"observed_at": stamp, "page": k, "status": st,
                 "seconds": "%.2f" % t} for t, k, st in timing],
               FEED_FIELDS)


# ---------------------------------------------------------------------------
# parse
# ---------------------------------------------------------------------------

def parse() -> None:
    """Every snapshot ever taken -> data/tidy/*.csv. Full rebuild each run.

    Parsed once per distinct document CONTENT, not once per snapshot — a
    carried-forward page is re-stamped for every snapshot it appears in
    rather than re-parsed. Written one table at a time so only the table
    currently being written is fully materialised in memory.
    """
    pending: dict[str, list[tuple[str, dict]]] = {}
    cache, fresh = _parse_cache(), {}
    walk = doc_keys()
    keys = _Sigs()

    # What has to be read off disk: documents this run needs a parse of
    # and doesn't already have one for. Gathered first so each archive
    # opens once rather than once per document.
    need: dict[str, set] = {}
    for stamp, docs in walk:
        for key, (ck, origin) in docs.items():
            src = source_for(key)
            if (src is not None and src.table != "points"
                    and parse_key(ck, src) not in cache):
                need.setdefault(origin, set()).add(key)
    misses = sum(len(v) for v in need.values())

    for origin, key, html in documents(need):
        src = source_for(key)
        try:
            rows = src.parse(html, origin, key)
        except Exception as e:
            # One bad page must not lose the rest of the run.
            print(f"  warn: {origin}/{key}: {type(e).__name__}: {e}")
            rows = []
        cache[parse_key(keys.of(key, html), src)] = rows

    hits = 0
    for stamp, docs in walk:
        for key, (ck, origin) in sorted(docs.items()):
            src = source_for(key)
            if src is None or src.table == "points":
                continue          # points feeds data/season/live, via points.py
            pk = parse_key(ck, src)
            rows = cache.get(pk, [])
            hits += 1
            fresh[pk] = rows
            for r in rows:
                table = r.get(ROW_TABLE) or src.table
                pending.setdefault(table, []).append((stamp, r))
    hits -= misses
    _save_parse_cache(fresh)
    print("  parsed %d documents, reused %d" % (misses, hits))

    TIDY.mkdir(parents=True, exist_ok=True)
    # probable_xi.csv was this file before it grew a `source` column —
    # tidy is disposable, so the old copy is deleted rather than read by
    # mistake.
    (TIDY / "probable_xi.csv").unlink(missing_ok=True)

    market_count = 0
    xi_count = 0
    tally: dict[str, int] = {}
    per_source: dict[str, int] = {}
    fixture_count = 0
    played: set = set()
    starters_count = 0
    starters_matches: set = set()

    # One file per table, named by the table — a registry entry is the
    # whole change needed to wire in a new source.
    for table in sorted(pending):
        rows = []
        for stamp, r in pending.pop(table):
            d = dict(r)
            d.pop(ROW_TABLE, None)
            d["observed_at"] = stamp
            rows.append(d)
        # An immutable-facts table (sources.STORE_ONCE) keeps only the
        # first sighting of each key.
        if table in STORE_ONCE:
            rows = first_seen(rows, STORE_ONCE[table])
        _write_csv(TIDY / f"{table}.csv", rows)
        if table == "market":
            market_count = len(rows)
        elif table == "lineups":
            xi_count = len(rows)
            for r in rows:
                tally[r["status"]] = tally.get(r["status"], 0) + 1
                per_source[r["source"]] = per_source.get(r["source"], 0) + 1
        elif table == "fixtures":
            fixture_count = len(rows)
        elif table == "matches":
            played = {r["match_id"] for r in rows if r["score"]}
        elif table == "starters":
            starters_count = len(rows)
            starters_matches = {r["match_id"] for r in rows}

    # Fail loudly on an empty parse: a silently-empty probable XI would set
    # every start probability to zero and quietly bench your best players.
    if not market_count:
        sys.exit("ERROR: market parse produced 0 rows — the markup changed.")

    # "" is a real status meaning "this page said nothing about fitness".
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


# Parsed rows, kept between runs, keyed by the CONTENT of the page and the
# parser's own signature — a raw archive is immutable, so a page's parse
# can only change when the parser does; touching sources.py invalidates
# only the parsers it actually changed.
_CACHE = "parsed.json"


class _Sigs:
    """Cache key per document, computed once per DISTINCT document —
    memoised on (page, len, hash) rather than the text itself, since a
    carried-forward page is the same str object every time and CPython
    caches its hash on first use.
    """

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
    """The cache key for one document read by one parser — keyed per
    parser (not one signature over all of sources.py), so a changed
    parser only misses its own entries.
    """
    name = getattr(src.parse, "__name__", "")
    sig = _SIG_CACHE.get(name)
    if sig is None:
        sig = _SIG_CACHE[name] = parser_sig(name)
    return "%s@%s" % (content_key, sig)


def _parse_cache(name: str = _CACHE) -> dict:
    try:
        blob = json.loads((TIDY / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return blob.get("docs", {})


def _save_parse_cache(docs: dict, name: str = _CACHE) -> None:
    """Only what THIS run used, so the file can't grow without bound."""
    TIDY.mkdir(parents=True, exist_ok=True)
    try:
        (TIDY / name).write_text(json.dumps({"docs": docs}), encoding="utf-8")
    except OSError:
        pass


def route(tables: dict, rows: list[dict], default: str, stamp: str) -> None:
    """File each row under the table it names, or the source's if it
    names none, stamped with the snapshot it was observed in. A row
    names its own table only when one document carries two grains (e.g.
    the squad feed's per-player match history) — see sources.ROW_TABLE.
    """
    for r in rows:
        table = r.get(ROW_TABLE) or default
        d = dict(r)
        d.pop(ROW_TABLE, None)
        d["observed_at"] = stamp
        tables.setdefault(table, []).append(d)


def first_seen(rows: list[dict], key: tuple) -> list[dict]:
    """`rows` with every repeat of a key after its first sighting dropped
    — for tables the feed republishes whole every sweep, so `observed_at`
    records when a fact first entered the store. A row with an empty key
    is kept, not collapsed with every other unlabelled row.
    """
    out, seen = [], set()
    for r in rows:
        k = tuple((r.get(c) or "") for c in key)
        if all(k):
            if k in seen:
                continue
            seen.add(k)
        out.append(r)
    return out


def _write_csv(path: Path, rows: list[dict]) -> None:
    """LF line endings, matching ffcore.tidy.write_csv. Raises the same
    "extra key" error DictWriter would if a row carries a field outside
    the first row's shape.
    """
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


# ---------------------------------------------------------------------------
# season baseline
# ---------------------------------------------------------------------------

def baseline(url: str = "", label: str = "") -> None:
    """Last season's points table -> data/season/points_<label>.csv. Run
    once a season, once the live sweep's selector has moved on to the new
    one and the completed season is reachable only via --url.
    """
    import httpx

    from sources import POINTS_URL
    url = url or POINTS_URL

    with httpx.Client(headers=HEADERS, timeout=45,
                      follow_redirects=True) as c:
        r = c.get(url)
        r.raise_for_status()
        html = r.text

    label = re.sub(r"[^0-9A-Za-z._-]", "", label or season_label(html)) or "unknown"

    # Raw first, and unconditionally — if the parse below fails, the page
    # that broke it is the only thing that can say why.
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


# ff_id was previously missing from this list despite parse_points()
# already extracting it — DictWriter silently drops any field not named
# here.
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


# ---------------------------------------------------------------------------
# prune — one-time migration, and re-compaction after it
# ---------------------------------------------------------------------------

def prune(apply: bool = False) -> None:
    """Rewrite data/raw as deduplicated archives. Dry run unless --apply.

    Migrates pre-migration directories into archives and drops pages
    whose signature never changed, in one pass. Signatures come from the
    CURRENT sources.py, so re-run this only right after a fetch, not
    after changing a selector.
    """
    snaps = snapshots()
    if not snaps:
        sys.exit("nothing under data/raw")

    prev: dict[str, str] = {}
    # `stored` must name the archive the bytes actually landed in, which is not
    # the snapshot being written whenever a page is carried forward.
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
                continue                      # carried in from an earlier one
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


# ---------------------------------------------------------------------------
# self-test
# ---------------------------------------------------------------------------

def _selftest() -> None:
    import tempfile

    # -- archives round-trip, and do so reproducibly -----------------------
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

        # Same content must give the same bytes, or git gains a blob per run
        # for a snapshot that did not change.
        q = Path(tmp) / "dt=2026-01-02T0000Z.tar.xz"
        _write(q, members)
        assert p.read_bytes() == q.read_bytes(), "archive not reproducible"

    # -- the snapshot index answers the same walk without reading disk -----
    # Load-bearing claim of doc_keys: a second run sees exactly what the
    # first one did, having opened no archives.
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
        # The second snapshot stores nothing new: market is CARRIED into it,
        # which is why the index records the stamp that stored each document.
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

        # A rewritten archive is a different size, which is what the index
        # checks rather than trusting itself blindly.
        _write(RAW / "dt=2026-01-02T0000Z.tar.xz",
               {"market.html": "<html>bb</html>", MANIFEST: man("market")})
        buf = _io.StringIO()
        with contextlib.redirect_stdout(buf):
            again = doc_keys()
        assert "read 1 of 2" in buf.getvalue(), buf.getvalue()
        assert again[1][1]["market"] != cold[1][1]["market"], again

        # documents() opens each archive once and hands back what was asked.
        got = list(documents({"2026-01-01T0000Z": {"market"}}))
        assert got == [("2026-01-01T0000Z", "market", "<html>a</html>")], got
    RAW, TIDY = _raw, _tidy

    # -- the manifest defines what a snapshot observed ---------------------
    rows = [{"page": "market", "sig": "s1", "stored": "t0", "seen": "t1"}]
    assert _manifest({MANIFEST: _manifest_csv(rows)}) == rows

    # A pre-migration directory has no manifest: the files on disk are it.
    legacy = _manifest({"market.html": "x", "team_celta.html": "y"})
    assert [r["page"] for r in legacy] == ["market", "team_celta"], legacy

    # -- an immutable fact is stored once, not once per sweep ---------------
    ev = [{"activity_id": "a1", "at": "2026-08-15T22:24", "observed_at": "t1"},
          {"activity_id": "a2", "at": "2026-08-16T09:00", "observed_at": "t1"},
          {"activity_id": "a1", "at": "2026-08-15T22:24", "observed_at": "t2"},
          {"activity_id": "a3", "at": "2026-08-17T11:00", "observed_at": "t2"}]
    once_only = first_seen(ev, ("activity_id",))
    assert [r["activity_id"] for r in once_only] == ["a1", "a2", "a3"], once_only
    # First sighting wins: observed_at means "when this entered the store".
    assert once_only[0]["observed_at"] == "t1", once_only[0]
    assert [r["observed_at"] for r in once_only] == ["t1", "t1", "t2"]
    # A row missing the key is kept rather than collapsed with every other
    # row missing it.
    odd = first_seen([{"activity_id": "", "at": "x", "observed_at": "t1"},
                      {"activity_id": "", "at": "y", "observed_at": "t2"}],
                     ("activity_id",))
    assert len(odd) == 2, odd
    # Compound keys, for a table whose identity is a pair.
    pairs = first_seen([{"a": "1", "b": "1"}, {"a": "1", "b": "2"},
                        {"a": "1", "b": "1"}], ("a", "b"))
    assert len(pairs) == 2, pairs
    assert first_seen([], ("activity_id",)) == []
    # Only tables that ARE immutable — api_teams/market are time series and
    # collapsing them would lose the history the market model fits on.
    assert set(STORE_ONCE) == {"api_activity", "api_players",
                              "api_stats", "results_history"}, STORE_ONCE
    assert "api_teams" not in STORE_ONCE and "market" not in STORE_ONCE
    # A corrected stat is a new fact, not a repeat: the key carries the
    # value, so a rescored line arrives as a second row.
    line = {"player_id": "1337", "week": "1", "stat": "goals",
            "value": "1", "points": "4", "observed_at": "t1"}
    again = dict(line, observed_at="t2")
    fixed = dict(line, points="6", observed_at="t3")
    kept = first_seen([line, again, fixed], STORE_ONCE["api_stats"])
    assert [r["observed_at"] for r in kept] == ["t1", "t3"], kept

    # -- one document, two grains ------------------------------------------
    out: dict[str, list] = {}
    route(out, [{"a": "1"}, {ROW_TABLE: "api_stats", "stat": "goals"}],
          "api_teams", "t1")
    assert set(out) == {"api_teams", "api_stats"}, list(out)
    assert out["api_teams"][0] == {"a": "1", "observed_at": "t1"}
    assert out["api_stats"][0]["observed_at"] == "t1"
    route(out, [{ROW_TABLE: "api_teams", "b": "2"}], "api_teams", "t2")
    assert len(out["api_teams"]) == 2, out["api_teams"]
    # The marker is consumed by route(), not written to the output table.
    assert all(ROW_TABLE not in r for rs in out.values() for r in rs), out

    # -- cadence ----------------------------------------------------------
    from sources import Source, parse_market, sign_market
    every = Source("m", "market", "u", parse_market, sign_market, "every_run")
    daily = Source("m", "market", "u", parse_market, sign_market, "daily")
    seen_today = {"m": {"seen": "2026-08-15T0940Z"}}
    assert due(every, seen_today, "2026-08-15")      # cadence ignores history
    assert not due(daily, seen_today, "2026-08-15")  # already swept today
    assert due(daily, seen_today, "2026-08-16")      # new day
    assert due(daily, {}, "2026-08-15")              # never swept

    # Twice a day, bounded by hours elapsed rather than the calendar.
    twice = Source("m", "market", "u", parse_market, sign_market, "twice_daily")
    assert not due(twice, {"m": {"seen": "2026-08-15T0940Z"}},
                   "2026-08-15T1200Z")               # 2.3h ago — not yet
    assert due(twice, {"m": {"seen": "2026-08-15T0940Z"}},
               "2026-08-15T1600Z")                   # 6.3h ago — due
    assert due(twice, {}, "2026-08-15T0000Z")        # never swept
    assert not due(twice, {"m": {"seen": "2026-08-15T2340Z"}},
                   "2026-08-16T0005Z")                # a date change alone

    # A match page is fetched once, ever, whatever day it is asked about.
    from sources import match_source
    once = match_source("match_22421-alaves-getafe")
    assert due(once, {}, "2026-08-15")
    assert not due(once, {once.key: {"seen": "2026-08-15T0940Z"}}, "2026-08-16")

    # ...which is exactly why its manifest row has to be carried forward.
    prev = {"market": {"page": "market", "sig": "s", "stored": "t0",
                       "seen": "t0"},
            once.key: {"page": once.key, "sig": "s", "stored": "t0",
                       "seen": "t0"}}
    carried = carry_matches(
        [{"page": "market", "sig": "s2", "stored": "t1", "seen": "t1"}], prev)
    assert [r["page"] for r in carried] == ["market", once.key], carried
    assert carried[1]["stored"] == "t0" and carried[0]["sig"] == "s2"
    assert len(carry_matches(carried, prev)) == 2

    # -- stamps read out of either layout ---------------------------------
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
