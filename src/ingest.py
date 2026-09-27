from __future__ import annotations

import csv
import functools
import hashlib
import io
import itertools
import json
import lzma
import os
import random
import re
import sys
import tarfile
import time
import types
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse


from ffcore.auth import API_BASE
from ffcore.league import load_config
from ffcore.tidy import (TABLES, Table, ROOT, SEASON, TIDY, append_csv, csv_string, read_csv,
                         table_stats, widen_csv, write_csv)
from sources import (API_LEAGUES_KEY, CAL_KEY, MATCH_KEY_RE,
                     ROW_TABLE, league_sources,
                     offer_sources, parse_api_leagues, parse_points,
                     played_sources,
                     season_label, source_for, sources)

__all__ = ["snapshots", "state", "doc_keys", "documents", "due",
          "fetch", "parse", "baseline"]

RAW = ROOT / "raw"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.5",
}

DELAY = (1.0, 2.0)


def _by_host(srcs) -> list:
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
    return sorted(RAW.glob("dt=*.tar.xz"), key=_stamp_of)


def _read(path: Path, only: set | None = None) -> dict[str, str]:
    want = None if only is None else set(only) | {MANIFEST}
    with tarfile.open(path, "r:xz") as tf:
        return {m.name: tf.extractfile(m).read().decode("utf-8", "replace")
                for m in tf.getmembers()
                if m.isfile() and (want is None or m.name in want)}


def _write(path: Path, members: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
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
    return list(csv.DictReader(io.StringIO(members.get(MANIFEST, ""))))


def state() -> dict[str, dict]:
    snaps = snapshots()
    if not snaps:
        return {}
    return {r["page"]: r for r in _manifest(_read(snaps[-1]))}


_INDEX = "snapindex.json"


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write_json(path: Path, obj) -> None:
    TIDY.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(json.dumps(obj), encoding="utf-8")
    except OSError:
        pass


def doc_keys():
    idx = _read_json(TIDY / _INDEX, {}).get("snaps", {})
    out, carried, keys = [], {}, Sigs()
    fresh, opened = {}, 0
    for snap in snapshots():
        stamp = _stamp_of(snap)
        size = snap.stat().st_size
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
        _write_json(TIDY / _INDEX, {"snaps": fresh})
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


def page_sig(src, text: str) -> str | None:
    try:
        rows = src.parse(text, "", src.key)
    except Exception:
        rows = []
    if not rows:
        return None
    return hashlib.sha1("\n".join(sorted(json.dumps(r, sort_keys=True,
                                                     default=str)
                                          for r in rows)).encode("utf-8")
                        ).hexdigest()[:16]


def due(src, prev: dict, now: str) -> bool:
    from ffcore.tidy import snapshot_stamp

    if src.cadence == "once":
        return src.key not in prev
    seen = prev.get(src.key, {}).get("seen", "")
    if src.cadence == "twice_daily":
        a, b = snapshot_stamp(seen), snapshot_stamp(now)
        gap = None if a is None or b is None else (b - a).total_seconds() / 3600.0
        return gap is None or gap >= TWICE_DAILY_HOURS
    if src.cadence != "daily":
        return True
    return not (seen[:10] == now[:10])


def carry_matches(rows: list[dict], prev: dict) -> list[dict]:
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
            url = src.url.format(date=stamp[:10], base=API_BASE)
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
            try:
                r = c.get(url, **kw)
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
            if src.key == API_LEAGUES_KEY:
                queue += league_sources(r.text)
                leagues = parse_api_leagues(r.text, stamp)
                if leagues:
                    league_id = leagues[0]["league_id"]
            if src.key == "api_teams" and league_id:
                queue += offer_sources(r.text, me, league_id)

            sig = page_sig(src, r.text)
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
    members[MANIFEST] = csv_string(rows, MANIFEST_FIELDS)
    _write(dest, members)
    print(f"snapshot: {dest} ({dest.stat().st_size // 1024}KB) — "
          f"{len(store)} stored, {unchanged} unchanged, {skipped} not due"
          + (f", {rotted} ROTTED" if rotted else ""))
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


def parse() -> None:
    import sources

    version = fingerprint(sources.source_for) + repr(TABLES)
    walk = doc_keys()
    state = _read_json(TIDY / _STATE, {})
    done = set(state.get("stamps") or ())
    tables = set(state.get("tables") or ())
    if (state.get("version") != version
            or not done <= {stamp for stamp, _docs in walk}
            or not all((TIDY / f"{t}.csv").exists() for t in tables)):
        for t in tables:
            (TIDY / f"{t}.csv").unlink(missing_ok=True)
        done, tables = set(), set()
    todo = [(stamp, docs) for stamp, docs in walk if stamp not in done]
    if not todo:
        print("  nothing new to parse (%d snapshots already folded in)"
              % len(done))
        return

    parsed = rows_out = 0
    used: set[str] = set()
    for i in range(0, len(todo), CHUNK):
        chunk = [(stamp, {page: (_parsed_key(page, ck), origin)
                          for page, (ck, origin) in docs.items()
                          if source_for(page)}) for stamp, docs in todo[i:i + CHUNK]]
        cache = _read_cache({ck for _stamp, docs in chunk
                             for ck, _o in docs.values()})
        need: dict[str, set] = {}
        for _stamp, docs in chunk:
            for page, (ck, origin) in docs.items():
                used.add(ck)
                if ck not in cache:
                    need.setdefault(origin, set()).add(page)
        fresh = _parse_all(need)
        cache.update(fresh)
        parsed += len(fresh)
        TIDY.mkdir(parents=True, exist_ok=True)
        with (TIDY / _CACHE).open("a", encoding="utf-8") as fh:
            for ck, rows in fresh.items():
                fh.write(json.dumps({"k": ck, "r": rows}) + "\n")
        pending: dict[str, list[dict]] = {}
        for stamp, docs in chunk:
            for page, (ck, _origin) in sorted(docs.items()):
                route(pending, cache.get(ck, []), source_for(page).table, stamp)
        for table, rows in pending.items():
            _store(TIDY / f"{table}.csv", rows,
                   TABLES.get(table, Table(True)))
            rows_out += len(rows)
        tables |= set(pending)
        done |= {stamp for stamp, _docs in chunk}
        _write_json(TIDY / _STATE, {"version": version, "stamps": sorted(done),
                                    "tables": sorted(tables)})
    if len(todo) == len(walk):
        _keep_cache(used)
    print("  %d snapshot(s): %d document(s) parsed, %d row(s) routed"
          % (len(todo), parsed, rows_out))
    if not table_stats(TIDY / "market.csv")[0]:
        sys.exit("ERROR: market parse produced 0 rows — the markup changed.")


CHUNK = 25
_STATE = "parse_state.json"
_CACHE = "parsed.jsonl"


def _read_cache(keys: set) -> dict:
    out: dict = {}
    try:
        fh = (TIDY / _CACHE).open(encoding="utf-8")
    except OSError:
        return out
    with fh:
        for line in fh:
            if line[7:line.find('"', 7)] in keys:
                rec = json.loads(line)
                out[rec["k"]] = rec["r"]
    return out


def _keep_cache(keys: set) -> None:
    path = TIDY / _CACHE
    if not path.exists():
        return
    tmp = path.with_suffix(".tmp")
    with path.open(encoding="utf-8") as src, tmp.open("w", encoding="utf-8") as dst:
        dst.writelines(line for line in src
                       if line[7:line.find('"', 7)] in keys)
    tmp.replace(path)


def _leaf(x) -> str:
    if isinstance(x, (set, frozenset)):
        return repr(sorted(map(repr, x)))
    r = repr(x)
    return type(x).__qualname__ if " at 0x" in r else r


@functools.cache
def fingerprint(fn) -> str:
    seen, h = set(), hashlib.blake2b(digest_size=8)
    todo: list[tuple[bool, object]] = [(True, fn)]
    while todo:
        is_obj, obj = todo.pop()
        if not is_obj:
            h.update(_leaf(obj).encode())
            continue
        obj = getattr(obj, "__wrapped__", obj)
        if id(obj) in seen:
            continue
        seen.add(id(obj))
        code = obj if isinstance(obj, types.CodeType) else getattr(
            obj, "__code__", None)
        if code is None:
            h.update(_leaf(obj).encode())
            continue
        h.update(code.co_code)
        glb = getattr(obj, "__globals__", {})
        todo.extend(reversed(
            [(isinstance(c, types.CodeType), c) for c in code.co_consts]
            + [(True, glb[name]) for name in code.co_names if name in glb]))
    return h.hexdigest()


def _parsed_key(page: str, ck: str) -> str:
    return "%s:%s" % (ck, fingerprint(source_for(page).parse))


def _parse_origin(task) -> dict:
    origin, want = task
    out = {}
    for stamp, page, html in documents({origin: want}):
        try:
            rows = source_for(page).parse(html, stamp, page)
        except Exception as e:
            print(f"  warn: {stamp}/{page}: {type(e).__name__}: {e}")
            rows = []
        out[_parsed_key(page, Sigs().of(page, html))] = rows
    return out


def _parse_all(need: dict[str, set]) -> dict:
    tasks = sorted(need.items())
    if sum(len(v) for v in need.values()) < 24:
        return {k: v for t in tasks for k, v in _parse_origin(t).items()}
    import concurrent.futures as cf
    import multiprocessing as mp
    out: dict = {}
    with cf.ProcessPoolExecutor(max(1, (os.cpu_count() or 2) // 2),
                                mp_context=mp.get_context("fork")) as ex:
        for part in ex.map(_parse_origin, tasks):
            out.update(part)
    return out


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


def route(tables: dict, rows: list[dict], default: str, stamp: str) -> None:
    for r in rows:
        table = r.get(ROW_TABLE) or default
        d = dict(r)
        d.pop(ROW_TABLE, None)
        d["observed_at"] = stamp
        tables.setdefault(table, []).append(d)


def _content(r: dict) -> tuple:
    return tuple(sorted((k, str(v)) for k, v in r.items()
                        if k != "observed_at" and v not in ("", None)))


def _store(path: Path, rows: list[dict], spec: Table) -> None:
    fields = list(dict.fromkeys(f for r in rows for f in r))
    widen_csv(path, fields)
    if spec.store == "daily":
        by_day: dict = {}
        for r in list(read_csv(path)) + rows:
            k = tuple((r.get(c) or "") for c in spec.key)
            by_day[(k, (r.get("observed_at") or "")[:10]) if all(k)
                   else len(by_day)] = r
        write_csv(path, list(by_day.values()),
                  list(dict.fromkeys(f for r in by_day.values() for f in r)))
        return
    if spec.store == "once":
        latest = {tuple(r.get(c, "") for c in spec.key): _content(r)
                  for r in read_csv(path)}
        fresh = []
        for r in rows:
            k = tuple((r.get(c) or "") for c in spec.key)
            if latest.get(k) != _content(r):
                latest[k] = _content(r)
                fresh.append(r)
        rows = fresh
    append_csv(path, rows, fields)


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
            MANIFEST: csv_string([{"page": "points", "sig": "", "stored": label,
                                   "seen": label}], MANIFEST_FIELDS)})

    rows = parse_points(html)
    if not rows:
        sys.exit("PARSE FAILED — no table matched, so nothing was written and "
                 "the last good file is untouched. The markup has probably "
                 "changed: fix sources.parse_points.")

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    out = SEASON / f"points_{label}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    write_csv(out, [dict(r, season=label, observed_at=stamp, source_url=url)
                    for r in rows], POINTS_FIELDS)

    played = sum(1 for r in rows if (r["games"] or "0") != "0")
    print(f"wrote {out} — {len(rows)} players, "
          f"{played} with minutes, season label '{label}'")
    print("Spot-check a few names against the app before trusting the report.")


POINTS_FIELDS = ["player_name", "player_name_full", "team", "points",
                 "games", "avg", "ff_id", "season", "observed_at",
                 "source_url"]


def _selftest() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "dt=2026-01-01T0000Z.tar.xz"
        members = {"market.html": "<html>a</html>",
                   "team_celta.html": "<html>b</html>",
                   MANIFEST: csv_string(
                       [{"page": "market", "sig": "s1",
                         "stored": "2026-01-01T0000Z", "seen": "2026-01-01T0000Z"},
                        {"page": "team_celta", "sig": "s2",
                         "stored": "2026-01-01T0000Z", "seen": "2026-01-01T0000Z"}],
                       MANIFEST_FIELDS)}
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
        man = csv_string([{"page": "market", "sig": "s", "stored": "t",
                           "seen": "t"}], MANIFEST_FIELDS)
        _write(RAW / "dt=2026-01-01T0000Z.tar.xz",
               {"market.html": "<html>a</html>", MANIFEST: man})
        _write(RAW / "dt=2026-01-02T0000Z.tar.xz", {MANIFEST: man})

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
               {"market.html": "<html>bb</html>", MANIFEST: man})
        buf = _io.StringIO()
        with contextlib.redirect_stdout(buf):
            again = doc_keys()
        assert "read 1 of 2" in buf.getvalue(), buf.getvalue()
        assert again[1][1]["market"] != cold[1][1]["market"], again

        got = list(documents({"2026-01-01T0000Z": {"market"}}))
        assert got == [("2026-01-01T0000Z", "market", "<html>a</html>")], got
    RAW, TIDY = _raw, _tidy

    rows = [{"page": "market", "sig": "s1", "stored": "t0", "seen": "t1"}]
    assert _manifest({MANIFEST: csv_string(rows, MANIFEST_FIELDS)}) == rows


    line = {"player_id": "1337", "week": "1", "stat": "goals",
            "value": "1", "points": "4", "observed_at": "t1"}
    m1 = {"observed_at": "2026-09-20T0900Z", "ff_id": "1", "value": "10"}
    m3 = dict(m1, observed_at="2026-09-20T1800Z", value="11")
    m4 = dict(m1, observed_at="2026-09-21T0900Z", value="11")
    blank = dict(m1, ff_id="")
    cases = [
        ([[{"a": "1", "b": "x"}], [{"a": "2", "b": "y"}], [{"a": "3"}]],
         Table(True), "a,b\n1,x\n2,y\n3,\n"),
        ([[{"a": "1"}], [{"a": "2", "c": "new"}]], Table(True),
         "a,c\n1,\n2,new\n"),
        ([[line], [dict(line, observed_at="t2"),
                   dict(line, points="6", observed_at="t3")],
          [dict(line, observed_at="t4")]], TABLES["api_activity"],
         "player_id,week,stat,value,points,observed_at\n"
         "1337,1,goals,1,4,t1\n1337,1,goals,1,6,t3\n1337,1,goals,1,4,t4\n"),
        ([[m1], [m3], [m4]], TABLES["market"],
         "observed_at,ff_id,value\n"
         "2026-09-20T1800Z,1,11\n2026-09-21T0900Z,1,11\n"),
        ([[blank, dict(blank)]], TABLES["market"],
         "observed_at,ff_id,value\n2026-09-20T0900Z,,10\n"
         "2026-09-20T0900Z,,10\n"),
    ]
    k1 = {"observed_at": "t1", "match_id": "7", "kickoff": "a"}
    cases.append(([[k1], [dict(k1, observed_at="t2", kickoff="b")],
                   [dict(k1, observed_at="t3", kickoff="a")],
                   [dict(k1, observed_at="t4", kickoff="a")]],
                  TABLES["matches"],
                  "observed_at,match_id,kickoff\nt1,7,a\nt2,7,b\nt3,7,a\n"))
    for batches, spec, want in cases:
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "t.csv"
            for rows in batches:
                _store(f, rows, spec)
            got = f.read_text(encoding="utf-8")
            assert got == want, (batches, got)

    out: dict[str, list] = {}
    route(out, [{"a": "1"}, {ROW_TABLE: "api_standings", "stat": "goals"}],
          "api_teams", "t1")
    assert set(out) == {"api_teams", "api_standings"}, list(out)
    assert out["api_teams"][0] == {"a": "1", "observed_at": "t1"}
    assert out["api_standings"][0]["observed_at"] == "t1"
    route(out, [{ROW_TABLE: "api_teams", "b": "2"}], "api_teams", "t2")
    assert len(out["api_teams"]) == 2, out["api_teams"]
    assert all(ROW_TABLE not in r for rs in out.values() for r in rs), out

    from sources import Source, parse_market
    every = Source("m", "market", "u", parse_market, cadence="every_run")
    daily = Source("m", "market", "u", parse_market, cadence="daily")
    seen_today = {"m": {"seen": "2026-08-15T0940Z"}}
    assert due(every, seen_today, "2026-08-15")
    assert not due(daily, seen_today, "2026-08-15")
    assert due(daily, seen_today, "2026-08-16")
    assert due(daily, {}, "2026-08-15")

    twice = Source("m", "market", "u", parse_market, cadence="twice_daily")
    assert not due(twice, {"m": {"seen": "2026-08-15T0940Z"}},
                   "2026-08-15T1200Z")
    assert due(twice, {"m": {"seen": "2026-08-15T0940Z"}},
               "2026-08-15T1600Z")
    assert due(twice, {}, "2026-08-15T0000Z")

    got = [x.key for x in _by_host([
        Source(k, "t", "https://%s/%s" % (k[0], k), None, None, "every_run")
        for k in ("a1", "a2", "a3", "b1", "b2")])]
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

    from sources import _FIXTURE
    team = source_for("team_celta")
    base = page_sig(team, _FIXTURE)
    assert base and page_sig(team, "<html></html>") is None
    assert page_sig(team, _FIXTURE.replace(
        'class="jugadores-titulares"',
        'class="jugadores-titulares" data-posicionalternativa1-x="52%"')) == base
    for before, after in [("Pedri 70%", "Pedri 60%"),
                          ("Owen Bosch", "Owen Bosche"),
                          ('alt="Duda"', 'alt="Lesionado"'),
                          ("/jugadores/pedri", "/jugadores/pedri-gonzalez")]:
        assert page_sig(team, _FIXTURE.replace(before, after)) != base, after

    print("ingest.py selftest OK")


if __name__ == "__main__":
    argv = sys.argv[1:]
    cmd = argv[0] if argv else "fetch"
    if cmd in ("--selftest", "selftest"):
        _selftest()
    elif cmd == "baseline":
        url = argv[argv.index("--url") + 1] if "--url" in argv else ""
        label = argv[argv.index("--label") + 1] if "--label" in argv else ""
        baseline(url, label)
    elif cmd in ("fetch", "parse"):
        {"fetch": fetch, "parse": parse}[cmd]()
    else:
        sys.exit("usage: ingest.py fetch | parse | baseline [--url U] "
                 "[--label L] | --selftest")
