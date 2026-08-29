"""
ingest.py — the only thing that touches the network or the raw store.

    python src/ingest.py fetch      # sweep the registry, store what changed
    python src/ingest.py parse      # rebuild tidy CSV from every snapshot ever
    python src/ingest.py baseline   # once a season: last season's points table
    python src/ingest.py prune      # migrate/compact data/raw (dry run)
    python src/ingest.py prune --apply
    python src/ingest.py --selftest

Fetch and parse stay separate for the reason they always did: scrapers rot.
When the markup changes, or when you realise you want a field you never
extracted, you fix `sources.py` and re-run `parse` over the whole history. Keep
only the parsed output and that option is gone.

WHAT IS IN A SNAPSHOT. One xz-compressed tar per sweep:

    data/raw/dt=2026-08-15T0940Z.tar.xz
        market.html            only if its content changed
        team_celta.html        only if its content changed
        MANIFEST.csv           page, sig, stored, seen — ALWAYS, for every page

`stored` names the snapshot whose archive actually holds those bytes, so a
page whose content did not change is listed but not written again. `parse`
carries it forward. Two consequences worth being explicit about:

  * The manifest, not the file listing, defines what a snapshot observed. A
    page absent from the manifest was not fetched — a 403, or a cadence skip —
    and parse emits no rows for it, exactly as before.
  * Deleting one archive corrupts every later snapshot that carries a page
    forward from it. This store is append-only. It always was; now it matters.

WHY tar.xz AND NOT gzip-PER-PAGE. Measured on the first 29 snapshots: 638
pages, 60 MB, and every single file byte-distinct thanks to ad ids and cache
busters. Deduplicating on content signature drops 59% of them. xz instead of
gzip halves what is left, and tarring the pages of one sweep together halves
it again, because twenty team pages share almost all of their boilerplate and
a solid archive can see across them. 60 MB becomes 8 MB, and a season's
projection falls from ~4.4 GB — past the point GitHub blocks a push — to
~0.2 GB. Both codecs are stdlib, so the test job still installs only lxml and
cssselect. The cost is that you can no longer open one page in the GitHub web
UI, which is a fair trade because `parse` reads whole snapshots anyway.

httpx is imported inside fetch(), never at module level. The test job installs
no network client, and history.py's module-level import is precisely what used
to break `--selftest` on a machine that never intended to fetch anything.
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

    `only` is the set of members worth decoding, MANIFEST.csv aside — a reader
    that wants one page out of forty (points.py wants exactly that) pays for
    the archive's decompression either way, but not for turning twenty team
    pages it will throw away into str. The manifest is always kept because
    that is what says which pages the snapshot claims to hold.
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
    """One tar.xz, byte-reproducible: same content in, same bytes out.

    mtime and ownership are zeroed because git stores whatever we hand it, and
    a timestamp inside the archive would make an otherwise identical snapshot a
    new blob.
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
    """{page: manifest row} as of the newest snapshot — what fetch compares to.

    Read from the newest manifest alone rather than by walking history,
    because the manifest carries the full picture at that moment by design.
    """
    snaps = snapshots()
    if not snaps:
        return {}
    return {r["page"]: r for r in _manifest(_read(snaps[-1]))}


def pages(only: set | None = None):
    """(stamp, {page: html}) per snapshot, oldest first, carrying forward.

    The carry-forward is what makes deduplication invisible downstream: a
    snapshot that stored no new market page still yields the market page that
    was current at the time, so market.csv has the same rows it always had.

    `only` narrows it to the pages named, for a caller that wants one of them.
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

    THE ARCHIVES ARE IMMUTABLE, so which document each stamp carried is a fact
    that only has to be established once. Establishing it every run meant
    decompressing thirteen megabytes and decoding two thousand documents to
    rediscover a mapping that had not changed since the season started, and
    that cost grows with the season — four of parse's seven seconds by the
    time this was written, on the way to a minute by May.

    The index is written next to the parse cache and validated by file size,
    so an archive that was rewritten is re-read rather than trusted. A stamp
    that is not in the index is read normally, which makes the usual case —
    a run with one new snapshot in it — cost one archive rather than fifty.

    Callers get keys, not text, and fetch the text only for a document whose
    parse they do not already have; `document()` is how.
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

    BATCHED, because the caller's misses are correlated: a cold parse cache
    wants every document there is, and fetching them one at a time meant a
    scan of the snapshot list and an archive open per document — 2,500 opens
    of 53 archives, which turned a rebuild from forty seconds into seventy.
    A parser change is exactly when a rebuild happens, and exactly when you
    are iterating and cannot wait a minute for each attempt.
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

# How long a "twice_daily" page may go unasked. Six hours, so both scheduled
# sweeps get their own reading — 00:40 and 11:40 are eleven hours apart — while
# a rerun pressed from the phone an hour later re-uses what is already stored
# instead of asking forty sites again.
TWICE_DAILY_HOURS = 6.0


def due(src, prev: dict, now: str) -> bool:
    """Should this sweep request `src`? `now` is this sweep's stamp.

    Cadence "daily" says no when we already requested the page today. A page
    not due is carried into this snapshot's manifest with its previous `seen`,
    so it is due again tomorrow and `parse` still sees it today.

    Cadence "twice_daily" says no until TWICE_DAILY_HOURS have passed since it
    was last asked. THE CALENDAR IS THE WRONG BOUND FOR A PAGE THAT MOVES
    DURING THE DAY: the probable-XI pages were "daily", so the 11:40 sweep —
    the run that exists because the XIs have firmed up by late morning, which
    is what lfg.timer says about itself — skipped them and reported the reading
    from 00:13. Forcing the sweep at 17:50 on 2026-08-19 measured what that
    costs: futbolfantasy moved 23 of 512 rows, analitica 70 of 197, and four
    rows of the fielded squad. Hours rather than a second calendar rule,
    because a rerun at five past midnight must not re-ask forty pages.

    Cadence "once" says no as soon as we have the page at all. That is the
    match pages: a confirmed eleven does not change after kickoff, so asking
    again buys the live stats we do not parse, 380 times a season.
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
    """Hours from one sweep stamp to another, or None if either is unreadable.

    None means "ask": a stamp we cannot read is not evidence that the page is
    current, and one extra fetch is cheaper than a day of not noticing.
    """
    from ffcore.tidy import snapshot_stamp

    a, b = snapshot_stamp(then), snapshot_stamp(now)
    if a is None or b is None:
        return None
    return (b - a).total_seconds() / 3600.0


def carry_matches(rows: list[dict], prev: dict) -> list[dict]:
    """`rows` plus every match page the last manifest knew and this one missed.

    Match pages are fetched once ever, so they have to be carried into each new
    manifest by hand: state() reads the newest manifest alone, and a page that
    fell out of it would look unfetched and be requested again every run for the
    rest of the season. The calendar is only swept once a day, so on the second
    run of a day no match page is even in the queue to be carried the usual way.
    """
    have = {r["page"] for r in rows}
    return rows + [dict(r) for page, r in prev.items()
                   if MATCH_KEY_RE.match(page) and page not in have]


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
    # WHICH PAGES COST THE TIME, AND WHICH ARE ROTTING. The sweep is the
    # longest step in the run by a distance and nothing said where it went —
    # forty pages fetched in a queue with one line of output for the lot. A
    # source that times out every day is a source to drop; without this the
    # only evidence was a warn line scrolling past in a journal.
    timing: list[tuple] = []
    fails: dict[str, str] = {}

    # One token for the whole sweep, fetched before the first request so a
    # login that has expired fails here — loudly, once — rather than four
    # times in the middle of a queue. A missing token is NOT fatal: the public
    # scrapers are the older half of this repo and still work without it, so
    # the sweep degrades to what it always did rather than producing nothing.
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

    with httpx.Client(headers=HEADERS, timeout=TIMEOUT,
                      follow_redirects=True) as c:
        # A queue rather than a loop over the registry, because the calendar
        # adds work to it: the match pages it lists are not knowable until it
        # has been read. Everything else about the sweep is unchanged — same
        # spacing, same backoff, same manifest.
        queue = list(sources())
        # Captured off api_leagues the moment it answers, exactly like the
        # league id league_sources() already bakes into the market/teams/
        # activity URLs it queues — offer_sources() needs the same id and
        # api_teams carries none, so it is read here rather than re-derived.
        league_id = None
        me = load_config().me
        while queue:
            src = queue.pop(0)
            if not due(src, prev, stamp):
                if src.key in prev:
                    rows.append(dict(prev[src.key]))     # carried, not fetched
                skipped += 1
                continue
            # {date} is filled for the one source whose URL carries the day it
            # is asking about (Club Elo); {base} for the league API, whose host
            # lives next to the token that opens it. Every other URL has no
            # placeholder in it, so this is a no-op for them.
            url = src.url.format(date=stamp[:10], base=API_BASE)
            # The bearer goes ONLY on entries that asked for it. Sending it
            # with a futbolfantasy request would hand a third party the
            # credential to the league account, so this is a per-request
            # header and never a client-wide one.
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
                # BODY MEANS POST, ITS ONLY JOB. Every source before
                # Understat left this None and got exactly the GET this
                # loop always sent; a source that sets it is asking for
                # its own player-stats endpoint, which answers a GET with
                # an error rather than the data (verified directly).
                r = (c.post(url, data=src.body, **kw) if src.body is not None
                    else c.get(url, **kw))
            except httpx.RequestError as e:
                timing.append((time.monotonic() - t0, src.key, "FAILED"))
                fails[src.key] = type(e).__name__
                # A host that refuses the connection or never answers is one
                # missing page, not a reason to lose the sweep. Same treatment
                # as a non-200: warn, skip, keep going.
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
                # Only matches the calendar shows a score for: an unplayed
                # match page has no lineup on it to read.
                queue += played_sources(r.text)
            if src.table == "api_activity":
                # The feed names players only by id, and half of them belong
                # to players since sold — neither in a squad nor on the
                # market, so nothing else in the store can name them. One
                # lookup each, once ever, deduplicated by the "once" cadence.
                queue += player_sources(r.text)
            if src.key == API_LEAGUES_KEY:
                # Same trick: the market, squad and activity URLs all carry a
                # league id that this page is what tells us, so the sweep
                # discovers its own work rather than reading an id out of a
                # config file that could go stale.
                queue += league_sources(r.text)
                leagues = parse_api_leagues(r.text, stamp)
                if leagues:
                    league_id = leagues[0]["league_id"]
            if src.key == "api_teams" and league_id:
                # RECEIVED offers, one lookup per player YOU hold.
                # offer_sources() itself filters to `me`'s roster — querying
                # a rival's playerTeamId 403s (see its own note), and a 403
                # anywhere in this sweep is fatal, below. `league_id` here is
                # only ever missing if api_leagues itself came back empty.
                queue += offer_sources(r.text, me, league_id)

            sig = src.sign(r.text)
            was = prev.get(src.key, {})
            if sig is None:
                # Selectors matched nothing. Never deduplicate this: every
                # rotted page looks like the last one, and dropping it would
                # throw away the evidence of the rot.
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
            # THE GAP IS FOR THE SCRAPED SITES, and only for them. It is
            # there because someone maintains futbolfantasy for free; the
            # league's own API is this account asking the app about itself,
            # over an authenticated connection, and pausing two seconds
            # between those requests is politeness aimed at nobody. Keyed on
            # `auth` because that is exactly the line: the bearer marks a
            # first-party call.
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
    """One row per request per sweep, appended.

    A SOURCE THAT STOPS ANSWERING DOES NOT LOOK BROKEN ANYWHERE. Club Elo had
    been timing out for two days and the fixture board carried on ranking
    twenty clubs by a rating from before the jornada, because a failed fetch
    leaves the last good rows in the tidy store and every reader downstream
    treats them as today's. The warn line scrolls past in a journal nobody
    reads.

    This is the record that lets the appendix print how old each feed's last
    answer is, which is the only form of that fact anyone will see.
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

    PARSED ONCE PER DOCUMENT, NOT ONCE PER SNAPSHOT. The carry-forward hands
    the same market page to forty-seven consecutive stamps when it changed
    twice, and every one of them was being run through lxml again — a hundred
    seconds a run, growing with the season. A page is now parsed once per
    distinct CONTENT and the rows are re-stamped for every snapshot that
    carried it, which is the same output by construction: `observed_at` is the
    only field any parser takes from the stamp, and the self-test holds that.
    """
    tables: dict[str, list[dict]] = {}
    cache, fresh = _parse_cache(), {}
    walk = doc_keys()
    keys = _Sigs()

    # What has to be read off disk: the documents this run needs a parse of
    # and does not already have one for. Gathered first so the archives can be
    # opened once each rather than once per document.
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
            route(tables, rows, src.table, stamp)
    hits -= misses
    _save_parse_cache(fresh)
    print("  parsed %d documents, reused %d" % (misses, hits))

    TIDY.mkdir(parents=True, exist_ok=True)
    # One file per table, named by the table. This used to be three hardcoded
    # lines, so a new source in the registry was still half-wired here — the
    # rows were collected and then never written. A registry entry is now the
    # whole change.
    for table, rows in sorted(tables.items()):
        # A table of immutable facts keeps the first sighting of each and
        # nothing else — see sources.STORE_ONCE. Applied here, once, at the
        # only place tidy files are written, so a new such table is one
        # registry line rather than a special case in a writer.
        if table in STORE_ONCE:
            rows = first_seen(rows, STORE_ONCE[table])
        _write_csv(TIDY / f"{table}.csv", rows)
    market_rows = tables.get("market", [])
    xi_rows = tables.get("lineups", [])
    fixture_rows = tables.get("fixtures", [])
    # probable_xi.csv was this file before it grew a `source` column. Tidy is
    # disposable and rebuilt whole every run, so the old copy is deleted rather
    # than left to be read by mistake.
    (TIDY / "probable_xi.csv").unlink(missing_ok=True)

    # Fail loudly on an empty parse: a silently-empty probable XI would set
    # every start probability to zero and quietly bench your best players.
    if not market_rows:
        sys.exit("ERROR: market parse produced 0 rows — the markup changed.")

    # Print the status breakdown every run. The injury column sat on 'ok' for
    # 14,765 rows without anything noticing; a count in the log is what makes
    # that visible the next time the markup moves.
    tally: dict[str, int] = {}
    per_source: dict[str, int] = {}
    for r in xi_rows:
        tally[r["status"]] = tally.get(r["status"], 0) + 1
        per_source[r["source"]] = per_source.get(r["source"], 0) + 1
    # "" is a real status meaning "this page said nothing about fitness", and
    # it needs a printable name or it renders as a blank in the run log.
    flags = ", ".join("%s %d" % (k or "not stated", v)
                      for k, v in sorted(tally.items()) if k != "ok")
    by_src = ", ".join("%s %d" % (k, v) for k, v in sorted(per_source.items()))
    played = {r["match_id"] for r in tables.get("matches", []) if r["score"]}
    starters = tables.get("starters", [])
    print(f"market {len(market_rows)} rows, lineups {len(xi_rows)} rows "
          f"({by_src}), fixtures {len(fixture_rows)} rows")
    # The realised elevens are what grades every probable-XI source, so a match
    # played and never read has to be visible rather than merely absent.
    print("  played %d matches, starters %d rows for %d of them"
          % (len(played), len(starters),
             len({r["match_id"] for r in starters})))
    print("  status: ok %d%s" % (tally.get("ok", 0),
                                 (", " + flags) if flags else ""))
    if not flags:
        print("  warn: no player flagged in any snapshot — if the site still "
              "shows injuries, the fitness selectors have rotted.")


# Parsed rows, kept between runs and keyed by the CONTENT of the page.
#
# lxml over three hundred and eighty documents is twelve seconds, every run,
# for documents that have not changed since the last one — the raw archives
# are immutable, so the parse of a given page can only change when the PARSER
# changes. The fingerprint is the parser source: touch sources.py and the
# whole cache is discarded, which is the only event that can invalidate it.
#
# Content-hashed rather than stamp-ranged, so the carry-forward needs no
# special case and a re-fetched identical page costs nothing.
_CACHE = "parsed.json"


class _Sigs:
    """Cache key per document, computed once per DISTINCT document.

    The carry-forward hands the same market page to every stamp since it last
    changed, and it hands back the same str OBJECT each time — so hashing it
    per stamp was hashing 2.4 GB of html to get a few hundred distinct
    digests. Two seconds of every run, spent proving that a string equals
    itself.

    Memoised on (page, len, hash) rather than on the text, because CPython
    stores a str's hash on the object the first time it is asked for: the
    carried-forward copies are the same object, so every lookup after the
    first is free and nothing has to be held onto. Holding onto them was the
    first attempt and it ran the box out of memory — a quarter of a gigabyte
    of pinned html to save two seconds is not a trade.
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
    """The cache key for one document read by one parser.

    THE CONTENT KEY IS NOT ENOUGH. A parse is a function OF the page and of
    the code that reads it, and this cache used to carry only the page: a
    single fingerprint over the whole of sources.py gated the lot, so touching
    any parser — or a fixture string, or a comment — discarded four hundred
    documents' worth of work and cost a forty-second rebuild.

    Keyed per parser instead, on the closure of everything in sources.py that
    parser can reach. A changed parser misses its own entries and re-parses
    only its own pages; everybody else's stay. Stale entries need no eviction
    because _save_parse_cache writes back only what this run actually used.
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
    """Only what THIS run used, so the file cannot grow without bound: a page
    nobody carried forward any more is a page nobody will ask about again —
    and neither is a parser nobody has any more."""
    TIDY.mkdir(parents=True, exist_ok=True)
    try:
        (TIDY / name).write_text(json.dumps({"docs": docs}), encoding="utf-8")
    except OSError:
        pass


def route(tables: dict, rows: list[dict], default: str, stamp: str) -> None:
    """File each row under the table it names, or the source's if it names
    none, stamped with the snapshot it was observed in.

    A ROW MAY NAME ITS OWN TABLE, and almost none do: a source has one table
    and its rows go there. The exception is a document carrying two grains —
    the squad feed answers with a squad (one row per player) and with each
    player's match history (one row per player per week per stat), and
    fetching it twice to separate them would be absurd. See sources.ROW_TABLE.

    The marker is consumed here rather than written out: how a row was routed
    is not a fact about the row.
    """
    for r in rows:
        table = r.get(ROW_TABLE) or default
        # dict(r) + pop + set, not a filtered comprehension unioned with a
        # second dict — same result, one dict allocation instead of two.
        # Measured 2026-08-29: 2.7x faster per row.
        d = dict(r)
        d.pop(ROW_TABLE, None)
        d["observed_at"] = stamp
        tables.setdefault(table, []).append(d)


def first_seen(rows: list[dict], key: tuple) -> list[dict]:
    """`rows` with every repeat of a key after its first sighting dropped.

    For the tables in sources.STORE_ONCE, which the feed republishes whole on
    every sweep. The FIRST copy is the one kept, so `observed_at` records when
    a fact entered the store and never moves again — which also means the file
    only ever gains lines, and git only ever stores the difference.

    A row with an empty key is not collapsed. Two unlabelled rows are two
    facts we cannot tell apart, and throwing one away because neither carries
    an id is worse than keeping both.
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
    """LF, matching ffcore.tidy.write_csv. These two files were CRLF for their
    whole life because that is csv.DictWriter's default; the reshape that added
    the `source` column rewrote every row anyway, so the split ended here.

    csv.writer + a manual row-list, not DictWriter — same "extra key"
    ValueError DictWriter itself raises (route() feeds one table from
    several sources, so rows[0]'s shape is not a given for every row; that
    check stays, just done as a set-difference here instead of a fresh
    generator inside DictWriter's own per-row _dict_to_list()). Missing keys
    still default to "" — same as DictWriter's own restval. Measured
    2026-08-29: 24% faster over market.csv's 96,361 rows (0.36s -> 0.28s).
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
    """Last season's points table -> data/season/points_<label>.csv.

    The one thing history.py did that the daily sweep does not. Both read the
    same page and the same parser, but the sweep only ever sees whichever
    season the selector defaults to. Once that flips to the new season, the
    completed season is reachable only by asking for it, which is what --url
    is for. Run it once a season.
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

    # Raw first, and unconditionally. If the parse below fails, the page that
    # broke it is the only thing that can tell you why.
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


# The fields written to data/season/points_<label>.csv. ff_id WAS MISSING —
# parse_points() already extracts it (the same id every other reader in this
# repo joins on), but this writer's own fieldnames list did not name it, so
# csv.DictWriter silently dropped it from every row before it ever reached
# disk. That made the PRIOR season — a name-keyed, never-updated snapshot,
# exactly the shape rosters_initial.txt was — the one place left where a
# player whose display name moved on between seasons would silently lose
# his shrinkage prior rather than being found by id.
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

    Two jobs in one pass, because they are the same pass: turn pre-migration
    directories into archives, and drop pages whose signature never changed.
    Signatures come from the CURRENT sources.py, so re-running this after
    changing a selector would re-decide what to keep from a smaller set of
    pages than the original fetch saw. Don't. Migrate once; after that, fetch
    does the deduplicating as it goes.
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
    # This is the load-bearing claim of doc_keys: that the second run of a
    # season sees exactly what the first one did, having opened no archives.
    # If it ever drifts, every tidy CSV is quietly built from a stale picture
    # of which snapshot carried which page — the worst failure in the repo,
    # because nothing downstream would look wrong.
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
        # which is the case the index has to reproduce and the reason it
        # records the stamp that stored each document rather than just its key.
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

        # A rewritten archive is a different size, and a different size is the
        # only thing standing between the index and trusting itself blindly.
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
    # THE FEED REPEATS ITSELF ON EVERY SWEEP and the store was keeping every
    # copy. A transfer that happened on 15 August is the same row in all
    # twenty snapshots that have seen it since: on 2026-08-19 api_activity.csv
    # held 1,225 rows carrying 63 distinct events, api_players.csv 1,020 rows
    # carrying 55 names. Worse than untidy — it is QUADRATIC. Every sweep
    # rewrites the whole file with one more copy of everything, and every run
    # commits it: 14.5 KB to 77 KB in thirty-six hours, and the increment
    # itself grows. A season of it is hundreds of megabytes of git history
    # saying the same thing.
    ev = [{"activity_id": "a1", "at": "2026-08-15T22:24", "observed_at": "t1"},
          {"activity_id": "a2", "at": "2026-08-16T09:00", "observed_at": "t1"},
          {"activity_id": "a1", "at": "2026-08-15T22:24", "observed_at": "t2"},
          {"activity_id": "a3", "at": "2026-08-17T11:00", "observed_at": "t2"}]
    once_only = first_seen(ev, ("activity_id",))
    assert [r["activity_id"] for r in once_only] == ["a1", "a2", "a3"], once_only
    # THE FIRST SIGHTING WINS, so observed_at means "when this entered the
    # store" and stays put. Keeping the last would rewrite the whole file
    # every sweep and lose the one fact the column carries here.
    assert once_only[0]["observed_at"] == "t1", once_only[0]
    # Order is the order things were first seen, because the file is a log.
    assert [r["observed_at"] for r in once_only] == ["t1", "t1", "t2"]
    # A row missing the key is kept rather than collapsed onto every other
    # row missing it — dropping a fact because it is unlabelled is worse.
    odd = first_seen([{"activity_id": "", "at": "x", "observed_at": "t1"},
                      {"activity_id": "", "at": "y", "observed_at": "t2"}],
                     ("activity_id",))
    assert len(odd) == 2, odd
    # Compound keys, for a table whose identity is a pair.
    pairs = first_seen([{"a": "1", "b": "1"}, {"a": "1", "b": "2"},
                        {"a": "1", "b": "1"}], ("a", "b"))
    assert len(pairs) == 2, pairs
    assert first_seen([], ("activity_id",)) == []
    # Only the tables that ARE immutable. api_teams and api_market are time
    # series — a value, a clause and a bid count all move — and collapsing
    # those would throw away the history the market model is fitted on.
    assert set(STORE_ONCE) == {"api_activity", "api_players",
                              "api_stats", "results_history"}, STORE_ONCE
    assert "api_teams" not in STORE_ONCE and "market" not in STORE_ONCE
    # A CORRECTED STAT IS A NEW FACT, not a repeat: the key carries the value,
    # so an unchanged line is stored once and a rescored one arrives as a
    # second row with a later observed_at rather than overwriting the first.
    line = {"player_id": "1337", "week": "1", "stat": "goals",
            "value": "1", "points": "4", "observed_at": "t1"}
    again = dict(line, observed_at="t2")
    fixed = dict(line, points="6", observed_at="t3")
    kept = first_seen([line, again, fixed], STORE_ONCE["api_stats"])
    assert [r["observed_at"] for r in kept] == ["t1", "t3"], kept

    # -- one document, two grains ------------------------------------------
    # The squad feed carries a squad and each player's match history in one
    # payload. A row names its own table and the assembler routes on it, so
    # neither grain is fetched twice or split by guessing at its shape.
    out: dict[str, list] = {}
    route(out, [{"a": "1"}, {ROW_TABLE: "api_stats", "stat": "goals"}],
          "api_teams", "t1")
    assert set(out) == {"api_teams", "api_stats"}, list(out)
    # The stamp is written onto every row wherever it lands.
    assert out["api_teams"][0] == {"a": "1", "observed_at": "t1"}
    assert out["api_stats"][0]["observed_at"] == "t1"
    # A row that names no table belongs to its source's, and a row that names
    # its source's own table is not a special case.
    route(out, [{ROW_TABLE: "api_teams", "b": "2"}], "api_teams", "t2")
    assert len(out["api_teams"]) == 2, out["api_teams"]
    # The marker itself is not a column in the file it routed to: it is how
    # the row got there, not something anybody would read afterwards.
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

    # TWICE A DAY, BOUNDED BY HOURS AND NOT BY THE CALENDAR. The probable-XI
    # pages were "daily", which the 11:40 sweep read as "already done at
    # 00:13" — so the run whose whole reason is that the XIs have firmed up
    # was reading XIs from just after midnight. Measured on 2026-08-19 by
    # forcing the sweep at 17:50: futbolfantasy moved 23 of 512 rows since
    # 00:13 and analitica 70 of 197, four of them in the fielded squad.
    twice = Source("m", "market", "u", parse_market, sign_market, "twice_daily")
    assert not due(twice, {"m": {"seen": "2026-08-15T0940Z"}},
                   "2026-08-15T1200Z")               # 2.3h ago — not yet
    assert due(twice, {"m": {"seen": "2026-08-15T0940Z"}},
               "2026-08-15T1600Z")                   # 6.3h ago — due
    assert due(twice, {}, "2026-08-15T0000Z")        # never swept
    # The hours are what bound it, so a rerun at 00:05 does not refetch forty
    # pages just because the date changed.
    assert not due(twice, {"m": {"seen": "2026-08-15T2340Z"}},
                   "2026-08-16T0005Z")

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
    # The bytes stay in the archive that first held them, and market — fetched
    # this run — is not overwritten by the older row.
    assert carried[1]["stored"] == "t0" and carried[0]["sig"] == "s2"
    # A page already in this manifest is not carried twice.
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
