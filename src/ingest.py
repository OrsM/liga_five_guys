from __future__ import annotations

import csv
from collections.abc import Iterable, Iterator
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
from typing import Any
from urllib.parse import urlparse


from ffcore.auth import API_BASE, TokenStore
from ffcore.league import load_config
from ffcore.tidy import (TABLES, Table, ROOT, SEASON, TIDY, drop_sql, sql_tables,
                         store_sql, stored, csv_string, write_csv)
from ffcore.futbolfantasy import (MATCH_KEY_RE, POINTS_URL, parse_points,
                                  season_label)
from ffcore.laliga_api import ROW_TABLE
from sources import source, source_for, sources

from ffcore.parse import Row, snapshot_stamp
from ffcore.parse import stamp as to_stamp
from ffcore.source import Source
__all__ = ["snapshots", "state", "doc_keys", "documents", "due",
          "fetch", "parse", "baseline"]

RAW = ROOT / "raw"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.5",
}

DELAY = (1.0, 2.0)


def _by_host(srcs: Iterable[Source]) -> list[Source]:
    lanes: dict[str, list[Source]] = {}
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


def _read(path: Path, only: set[str] | None = None) -> dict[str, str]:
    want = None if only is None else set(only) | {MANIFEST}
    with tarfile.open(path, "r:xz") as tf:
        return {m.name: f.read().decode("utf-8", "replace")
                for m in tf.getmembers()
                if (want is None or m.name in want) and (f := tf.extractfile(m))}


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


def _manifest(members: dict[str, str]) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(members.get(MANIFEST, ""))))


def state() -> dict[str, dict[str, str]]:
    snaps = snapshots()
    if not snaps:
        return {}
    return {r["page"]: r for r in _manifest(_read(snaps[-1]))}


_INDEX = "snapindex.json"


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write_json(path: Path, obj: object) -> None:
    TIDY.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(json.dumps(obj), encoding="utf-8")
    except OSError:
        pass


def doc_keys() -> list[tuple[str, dict[str, tuple[str, str]]]]:
    idx = _read_json(TIDY / _INDEX, {}).get("snaps", {})
    out: list[tuple[str, dict[str, tuple[str, str]]]] = []
    carried: dict[str, tuple[str, str]] = {}
    keys = Sigs()
    fresh: dict[str, dict[str, Any]] = {}
    opened = 0
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


def documents(need: dict[str, set[str]]) -> Iterator[tuple[str, str, str]]:
    for snap in snapshots():
        stamp = _stamp_of(snap)
        want = need.get(stamp)
        if not want:
            continue
        for name, html in _read(snap, {"%s.html" % p for p in want}).items():
            if name.endswith(".html"):
                yield stamp, name.removesuffix(".html"), html


TWICE_DAILY_HOURS = 6.0


def page_sig(src: Source, text: str) -> str | None:
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


def due(src: Source, prev: dict[str, dict[str, str]], now: str) -> bool:

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


def carry_matches(rows: list[dict[str, str]], prev: dict[str, dict[str, str]]
                  ) -> list[dict[str, str]]:
    have = {r["page"] for r in rows}
    return rows + [dict(r) for page, r in prev.items()
                   if MATCH_KEY_RE.match(page) and page not in have]


def fetch() -> Path:
    import httpx

    now = datetime.now(timezone.utc)
    stamp = to_stamp(now)
    dest = RAW / f"dt={stamp}.tar.xz"
    if dest.exists():
        print(f"{dest} already exists; nothing to do.")
        return dest

    prev = state()
    store: dict[str, str] = {}
    rows: list[dict[str, str]] = []
    unchanged = skipped = rotted = 0
    timing: list[tuple[float, str, object]] = []
    fails: dict[str, str] = {}

    bearer = None
    try:
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

    # retries: a connection that never opens is tried again; nothing is sent
    # twice, since httpx only retries connect errors and timeouts.
    with httpx.Client(headers=HEADERS, timeout=TIMEOUT, follow_redirects=True,
                      transport=httpx.HTTPTransport(retries=2)) as c:
        queue = _by_host(sources())
        last: dict[str, float] = {}
        context = {"me": load_config().me}
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
            try:
                r = c.get(url, headers=extra or None)
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
            if src.follow:
                more = src.follow(r.text, context)
                queue += more
                if more:
                    print(f"  {src.key}: leads to {len(more)} page(s)")

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


def _version() -> str:
    """What the stored tables depend on: the parsers and how each table is
    kept. Not Table.numbers: those parse at read time, never stored."""
    return fingerprint(source_for) + repr({n: t._replace(numbers=())
                                           for n, t in TABLES.items()})


def parse() -> dict[str, int]:
    """Fold new snapshots into the tidy tables; how many snapshots,
    documents parsed and rows routed."""
    version = _version()
    walk = doc_keys()
    state = _read_json(TIDY / _STATE, {})
    done = set(state.get("stamps") or ())
    tables = set(state.get("tables") or ())
    in_sql = sql_tables()
    if (state.get("version") != version
            or not done <= {stamp for stamp, _docs in walk}
            or not tables <= in_sql):
        for t in tables:
            drop_sql(t)
        done, tables = set(), set()
    todo = [(stamp, docs) for stamp, docs in walk if stamp not in done]
    if not todo:
        print("  nothing new to parse (%d snapshots already folded in)"
              % len(done))
        return {"snapshots": 0, "documents": 0, "rows": 0}

    parsed = rows_out = 0
    used: set[str] = set()
    for i in range(0, len(todo), CHUNK):
        chunk = [(stamp, {page: (_parsed_key(page, ck), origin)
                          for page, (ck, origin) in docs.items()
                          if source_for(page)}) for stamp, docs in todo[i:i + CHUNK]]
        cache = _read_cache({ck for _stamp, docs in chunk
                             for ck, _o in docs.values()})
        need: dict[str, set[str]] = {}
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
        pending: dict[str, list[Row]] = {}
        for stamp, docs in chunk:
            for page, (ck, _origin) in sorted(docs.items()):
                route(pending, cache.get(ck, []), source(page).table, stamp)
        for table, rows in pending.items():
            store_sql(table, rows)
            rows_out += len(rows)
        tables |= set(pending)
        done |= {stamp for stamp, _docs in chunk}
        _write_json(TIDY / _STATE, {"version": version, "stamps": sorted(done),
                                    "tables": sorted(tables)})
    if len(todo) == len(walk):
        _keep_cache(used)
    print("  %d snapshot(s): %d document(s) parsed, %d row(s) routed"
          % (len(todo), parsed, rows_out))
    if not stored("market"):
        sys.exit("ERROR: market parse produced 0 rows — the markup changed.")
    return {"snapshots": len(todo), "documents": parsed, "rows": rows_out}


CHUNK = 25
_STATE = "parse_state.json"
_CACHE = "parsed.jsonl"


def _read_cache(keys: set[str]) -> dict[str, list[Row]]:
    out: dict[str, list[Row]] = {}
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


def _keep_cache(keys: set[str]) -> None:
    path = TIDY / _CACHE
    if not path.exists():
        return
    tmp = path.with_suffix(".tmp")
    with path.open(encoding="utf-8") as src, tmp.open("w", encoding="utf-8") as dst:
        dst.writelines(line for line in src
                       if line[7:line.find('"', 7)] in keys)
    tmp.replace(path)


def _leaf(x: object) -> str:
    if isinstance(x, (set, frozenset)):
        return repr(sorted(map(repr, x)))
    if isinstance(x, types.ModuleType):
        return "module " + x.__name__
    r = repr(x)
    return type(x).__qualname__ if " at 0x" in r else r


@functools.cache
def fingerprint(fn: object) -> str:
    seen: set[int] = set()
    h = hashlib.blake2b(digest_size=8)
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
    return "%s:%s" % (ck, fingerprint(source(page).parse))


def _parse_origin(task: tuple[str, set[str]]) -> dict[str, list[Row]]:
    origin, want = task
    out: dict[str, list[Row]] = {}
    for stamp, page, html in documents({origin: want}):
        try:
            rows = source(page).parse(html, stamp, page)
        except Exception as e:
            print(f"  warn: {stamp}/{page}: {type(e).__name__}: {e}")
            rows = []
        out[_parsed_key(page, Sigs().of(page, html))] = rows
    return out


def _parse_all(need: dict[str, set[str]]) -> dict[str, list[Row]]:
    tasks = sorted(need.items())
    if sum(len(v) for v in need.values()) < 24:
        return {k: v for t in tasks for k, v in _parse_origin(t).items()}
    import concurrent.futures as cf
    import multiprocessing as mp
    out: dict[str, list[Row]] = {}
    with cf.ProcessPoolExecutor(max(1, (os.cpu_count() or 2) // 2),
                                mp_context=mp.get_context("fork")) as ex:
        for part in ex.map(_parse_origin, tasks):
            out.update(part)
    return out


class Sigs:

    def __init__(self) -> None:
        self._at: dict[tuple[str, int, int], str] = {}

    def of(self, page: str, html: str) -> str:
        k = (page, len(html), hash(html))
        ck = self._at.get(k)
        if ck is None:
            ck = self._at[k] = "%s:%s" % (page, hashlib.blake2b(
                html.encode("utf-8", "replace"), digest_size=12).hexdigest())
        return ck


def route(tables: dict[str, list[Row]], rows: list[Row], default: str, stamp: str) -> None:
    for r in rows:
        table = r.get(ROW_TABLE) or default
        d = dict(r)
        d.pop(ROW_TABLE, None)
        d["observed_at"] = stamp
        tables.setdefault(table, []).append(d)


def baseline(url: str = "", label: str = "") -> None:
    import httpx

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
                 "changed: fix ffcore.futbolfantasy.parse_points.")

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
    uses = compile("mod.thing", "<fp>", "eval")
    at = {}
    for where in ("/repo/.venv", "/elsewhere/.venv"):
        mod = types.ModuleType("somelib")
        mod.__file__ = where + "/somelib/__init__.py"
        at[where] = fingerprint(types.FunctionType(uses, {"mod": mod}))
    assert len(set(at.values())) == 1, \
        "a module is fingerprinted by name, not by where it is installed"
    import subprocess
    other = subprocess.run([sys.executable, "-c", "import ingest; print(ingest._version())"],
                           capture_output=True, text=True, check=True).stdout.strip()
    assert other == _version(), "the parse version is the same in every process"
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


    out: dict[str, list[Row]] = {}
    route(out, [{"a": "1"}, {ROW_TABLE: "api_standings", "stat": "goals"}],
          "api_teams", "t1")
    assert set(out) == {"api_teams", "api_standings"}, list(out)
    assert out["api_teams"][0] == {"a": "1", "observed_at": "t1"}
    assert out["api_standings"][0]["observed_at"] == "t1"
    route(out, [{ROW_TABLE: "api_teams", "b": "2"}], "api_teams", "t2")
    assert len(out["api_teams"]) == 2, out["api_teams"]
    assert all(ROW_TABLE not in r for rs in out.values() for r in rs), out

    from ffcore.futbolfantasy import parse_market
    from ffcore.source import Source
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

    order = [x.key for x in _by_host([
        Source(k, "t", "https://%s/%s" % (k[0], k), lambda *_: [])
        for k in ("a1", "a2", "a3", "b1", "b2")])]
    assert order == ["a1", "b1", "a2", "b2", "a3"], order
    assert not due(twice, {"m": {"seen": "2026-08-15T2340Z"}},
                   "2026-08-16T0005Z")

    from ffcore.futbolfantasy import match_source
    once = match_source("match_22421-alaves-getafe")
    assert once is not None
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

    from ffcore.futbolfantasy import _FIXTURE
    team = source("team_celta")
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

    _selftest_sql_store()
    print("ingest.py selftest OK")


def _selftest_sql_store() -> None:
    """How each way of keeping a table reads, at every cut, stored stamp by
    stamp and all at once: store_cases.json holds what the CSV store read
    for these rows (captured 2026-10-11, before it was deleted)."""
    import tempfile
    from ffcore.clock import set_now
    from ffcore.parse import count, money
    from ffcore.tidy import current, history, store_sql, tables_in

    a, b, c = "2026-08-01T0900Z", "2026-08-02T0900Z", "2026-08-03T0900Z"
    once = [{"observed_at": a, "source": "s", "k": "1", "v": "10", "j": "3", "who": "x"},
            {"observed_at": a, "source": "s", "k": "2", "v": "5", "j": "", "who": "y"},
            {"observed_at": b, "source": "s", "k": "1", "v": "12", "j": "3", "who": "x"},
            {"observed_at": b, "source": "s", "k": "2", "v": "5", "j": "", "who": "y"},
            {"observed_at": b, "source": "s", "k": "", "v": "7", "j": "1", "who": "z"},
            {"observed_at": c, "source": "s", "k": "3", "v": "1", "j": "4", "who": "x",
             "late": "new column"}]
    snap = [{"observed_at": a, "source": "s", "who": "x", "v": "1"},
            {"observed_at": a, "source": "t", "who": "y", "v": "2"},
            {"observed_at": b, "source": "s", "who": "x", "v": "3"},
            {"observed_at": b, "source": "s", "who": "z", "v": ""},
            {"observed_at": c, "source": "t", "who": "y", "v": "4", "late": "q"}]
    a1, a2 = "2026-08-01T2100Z", "2026-08-02T2300Z"
    daily = [{"observed_at": a, "k": "1", "v": "1"}, {"observed_at": a, "k": "2", "v": "2"},
             {"observed_at": a, "k": "", "v": "9"},
             {"observed_at": a1, "k": "1", "v": "3"}, {"observed_at": a1, "k": "", "v": "8"},
             {"observed_at": b, "k": "1", "v": "4"}, {"observed_at": b, "k": "3", "v": "5"},
             {"observed_at": a2, "k": "3", "v": "6"}, {"observed_at": c, "k": "2", "v": "7"}]
    numbers = (("v", money), ("j", count))
    want = json.loads(Path(__file__).with_name("ffcore").joinpath("store_cases.json").read_text())
    try:
        for name, spec, rows in [
                ("t_once", Table(False, ("k",), "once", numbers), once),
                ("t_snap", Table(True, (), "", numbers[:1]), snap),
                ("t_daily", Table(True, ("k",), "daily", numbers[:1]), daily)]:
            TABLES[name] = spec
            stamps = sorted({r["observed_at"] for r in rows})
            for one_batch in (False, True):
                with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
                    for batch in ([rows] if one_batch else
                                  [[r for r in rows if r["observed_at"] == st] for st in stamps]):
                        store_sql(name, batch)
                    for now, expect in want[name].items():
                        set_now(snapshot_stamp(now))
                        got = [[{k: v for k, v in r.items() if v not in ("", None)} for r in g]
                               for g in (current(name), current(name, "t"),
                                         history(name), history(name, "s"))]
                        assert got == expect, (name, now, one_batch, got, expect)
                        if name == "t_once" and now == "2026-08-04T0000Z":
                            assert all(type(r["j"]) is int for r in got[2] if "j" in r) and \
                                all(type(r["v"]) is float for r in got[2]), \
                                "a count is an int, money a float"
    finally:
        set_now(None)
        for name in ("t_once", "t_snap", "t_daily"):
            TABLES.pop(name, None)


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
