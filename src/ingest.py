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
import io
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

from ffcore.tidy import ROOT, SEASON, TIDY          # noqa: E402
from sources import parse_points, season_label, source_for, sources  # noqa: E402

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


def _read(path: Path) -> dict[str, str]:
    """{member name: text} for one snapshot, whichever layout it is in."""
    if path.is_dir():
        return {f.name.removesuffix(".gz"):
                gzip.open(f, "rt", encoding="utf-8", errors="replace").read()
                for f in sorted(path.glob("*.html.gz"))}
    with tarfile.open(path, "r:xz") as tf:
        return {m.name: tf.extractfile(m).read().decode("utf-8", "replace")
                for m in tf.getmembers() if m.isfile()}


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


def pages():
    """(stamp, {page: html}) per snapshot, oldest first, carrying forward.

    The carry-forward is what makes deduplication invisible downstream: a
    snapshot that stored no new market page still yields the market page that
    was current at the time, so market.csv has the same rows it always had.
    """
    carried: dict[str, str] = {}
    for snap in snapshots():
        members = _read(snap)
        carried.update({k.removesuffix(".html"): v for k, v in members.items()
                        if k.endswith(".html")})
        present = [r["page"] for r in _manifest(members)]
        yield _stamp_of(snap), {p: carried[p] for p in present if p in carried}


# ---------------------------------------------------------------------------
# fetch
# ---------------------------------------------------------------------------

def due(src, prev: dict, today: str) -> bool:
    """Should this sweep request `src`?

    Only cadence "daily" can say no, and only when we already requested the
    page today. Both team sweeps are daily, so a sweep asks for forty team
    pages once a day and two pages the rest of the time. A page not due is
    carried into this snapshot's manifest with its previous `seen`, so it is
    due again tomorrow and `parse` still sees it today.
    """
    if src.cadence != "daily":
        return True
    return not (prev.get(src.key, {}).get("seen", "")[:10] == today)


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

    with httpx.Client(headers=HEADERS, timeout=TIMEOUT,
                      follow_redirects=True) as c:
        for src in sources():
            if not due(src, prev, stamp[:10]):
                if src.key in prev:
                    rows.append(dict(prev[src.key]))     # carried, not fetched
                skipped += 1
                continue
            r = c.get(src.url)
            if r.status_code in (403, 429):
                # Stop the whole run rather than retrying into a harder block.
                sys.exit(f"{r.status_code} on {src.url} — backing off, "
                         f"run again later. Nothing was written.")
            if r.status_code != 200:
                print(f"  warn: {r.status_code} on {src.key}, skipping")
                continue

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
            time.sleep(random.uniform(*DELAY))

    if not rows:
        sys.exit("ERROR: no page fetched — nothing written.")

    members = {f"{k}.html": v for k, v in store.items()}
    members[MANIFEST] = _manifest_csv(rows)
    _write(dest, members)
    print(f"snapshot: {dest} ({dest.stat().st_size // 1024}KB) — "
          f"{len(store)} stored, {unchanged} unchanged, {skipped} not due"
          + (f", {rotted} ROTTED" if rotted else ""))
    return dest


# ---------------------------------------------------------------------------
# parse
# ---------------------------------------------------------------------------

def parse() -> None:
    """Every snapshot ever taken -> data/tidy/*.csv. Full rebuild each run."""
    tables: dict[str, list[dict]] = {}

    for stamp, docs in pages():
        for key, html in sorted(docs.items()):
            src = source_for(key)
            if src is None or src.table == "points":
                continue          # points feeds data/season/live, via points.py
            try:
                tables.setdefault(src.table, []).extend(
                    src.parse(html, stamp, key))
            except Exception as e:
                # One bad page must not lose the rest of the run.
                print(f"  warn: {stamp}/{key}: {type(e).__name__}: {e}")

    TIDY.mkdir(parents=True, exist_ok=True)
    market_rows = tables.get("market", [])
    xi_rows = tables.get("lineups", [])
    _write_csv(TIDY / "market.csv", market_rows)
    _write_csv(TIDY / "lineups.csv", xi_rows)
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
    flags = ", ".join("%s %d" % (k, v) for k, v in sorted(tally.items())
                      if k != "ok")
    by_src = ", ".join("%s %d" % (k, v) for k, v in sorted(per_source.items()))
    print(f"market {len(market_rows)} rows, lineups {len(xi_rows)} rows "
          f"({by_src})")
    print("  status: ok %d%s" % (tally.get("ok", 0),
                                 (", " + flags) if flags else ""))
    if not flags:
        print("  warn: no player flagged in any snapshot — if the site still "
              "shows injuries, the fitness selectors have rotted.")


def _write_csv(path: Path, rows: list[dict]) -> None:
    """LF, matching ffcore.tidy.write_csv. These two files were CRLF for their
    whole life because that is csv.DictWriter's default; the reshape that added
    the `source` column rewrote every row anyway, so the split ended here."""
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


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

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    out = SEASON / f"points_{label}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=[
            "player_name", "player_name_full", "team", "points", "games",
            "avg", "season", "observed_at", "source_url"])
        w.writeheader()
        for row in rows:
            row.update(season=label, observed_at=stamp, source_url=url)
            w.writerow(row)

    played = sum(1 for r in rows if (r["games"] or "0") != "0")
    print(f"wrote {out} — {len(rows)} players, {played} with minutes, "
          f"season label '{label}'")
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

    # -- the manifest defines what a snapshot observed ---------------------
    rows = [{"page": "market", "sig": "s1", "stored": "t0", "seen": "t1"}]
    assert _manifest({MANIFEST: _manifest_csv(rows)}) == rows

    # A pre-migration directory has no manifest: the files on disk are it.
    legacy = _manifest({"market.html": "x", "team_celta.html": "y"})
    assert [r["page"] for r in legacy] == ["market", "team_celta"], legacy

    # -- cadence ----------------------------------------------------------
    from sources import Source, parse_market, sign_market
    every = Source("m", "market", "u", parse_market, sign_market, "every_run")
    daily = Source("m", "market", "u", parse_market, sign_market, "daily")
    seen_today = {"m": {"seen": "2026-08-15T0940Z"}}
    assert due(every, seen_today, "2026-08-15")      # cadence ignores history
    assert not due(daily, seen_today, "2026-08-15")  # already swept today
    assert due(daily, seen_today, "2026-08-16")      # new day
    assert due(daily, {}, "2026-08-15")              # never swept

    # -- stamps read out of either layout ---------------------------------
    assert _stamp_of(Path("data/raw/dt=2026-08-15T0940Z.tar.xz")) \
        == _stamp_of(Path("data/raw/dt=2026-08-15T0940Z")) == "2026-08-15T0940Z"

    print("ingest.py selftest OK (12 cases)")


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
