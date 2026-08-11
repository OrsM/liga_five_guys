"""
ff_ingest.py — daily snapshot of public LaLiga Fantasy data from futbolfantasy.com.

No login. No token. Nothing secret anywhere.

Two commands, deliberately separate:

    python ff_ingest.py fetch     # download raw HTML, gzip it, never touch it again
    python ff_ingest.py parse     # turn every snapshot ever taken into tidy CSV

Why separate: HTML scrapers rot. When futbolfantasy changes its markup, or when
you realise you want a field you didn't extract, you fix `parse` and re-run it
over the whole history. If you only kept the parsed output, that data is gone.
Raw HTML gzips to a few hundred KB a day — cheap insurance.

Deps: pip install httpx lxml
"""

from __future__ import annotations

import gzip
import os
import re
import csv
import sys
import time
import random
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(os.environ.get("FF_ROOT", "./data"))
RAW = ROOT / "raw"
OUT = ROOT / "tidy"

BASE = "https://www.futbolfantasy.com"
MARKET_URL = f"{BASE}/analytics/laliga-fantasy/mercado"
POINTS_URL = f"{BASE}/analytics/laliga-fantasy/puntos"
TEAM_URL = f"{BASE}/laliga/equipos/{{slug}}"

TEAMS = [
    "alaves", "athletic", "atletico", "barcelona", "betis", "celta",
    "deportivo", "elche", "espanyol", "getafe", "levante", "malaga",
    "osasuna", "racing", "rayo-vallecano", "real-madrid", "real-sociedad",
    "sevilla", "valencia", "villarreal",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.5",
}

# One sweep a day, sequential, with a human-ish gap. Someone maintains this
# site for free; don't make them regret leaving it open.
DELAY = (1.5, 3.0)
TIMEOUT = 30.0


# ---------------------------------------------------------------------------
# fetch
# ---------------------------------------------------------------------------

def fetch() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%MZ")
    dest = RAW / f"dt={stamp}"
    if (dest / "_SUCCESS").exists():
        print(f"{dest} already complete; nothing to do.")
        return dest
    dest.mkdir(parents=True, exist_ok=True)

    targets = [("market", MARKET_URL), ("points", POINTS_URL)]
    targets += [(f"team_{s}", TEAM_URL.format(slug=s)) for s in TEAMS]

    with httpx.Client(headers=HEADERS, timeout=TIMEOUT, follow_redirects=True) as c:
        for name, url in targets:
            path = dest / f"{name}.html.gz"
            if path.exists():
                continue
            r = c.get(url)
            if r.status_code in (403, 429):
                # Stop the whole run rather than retrying into a harder block.
                sys.exit(f"{r.status_code} on {url} — backing off, run again later.")
            if r.status_code != 200:
                print(f"  warn: {r.status_code} on {name}, skipping")
                continue
            with gzip.open(path, "wt", encoding="utf-8") as fh:
                fh.write(r.text)
            print(f"  {name}: {len(r.text) // 1024}KB")
            time.sleep(random.uniform(*DELAY))

    (dest / "_SUCCESS").touch()
    print(f"snapshot: {dest}")
    return dest


# ---------------------------------------------------------------------------
# parse — market values
# ---------------------------------------------------------------------------

TEAM_SELECT_RE = re.compile(r'<select[^>]*name="equipo"[^>]*>(.*?)</select>', re.S)
OPTION_RE = re.compile(r'<option[^>]*value="(\d+)"[^>]*>([^<]+)</option>')


def _team_map(html: str) -> dict[str, str]:
    m = TEAM_SELECT_RE.search(html)
    if not m:
        return {}
    return {tid: name.strip() for tid, name in OPTION_RE.findall(m.group(1)) if tid != "0"}


def _attr(chunk: str, name: str) -> str | None:
    m = re.search(rf'data-{name}="([^"]*)"', chunk)
    return m.group(1) if m else None


def parse_market(html: str, observed_at: str) -> list[dict]:
    """
    Players are rendered as elements carrying data-* attributes:
    data-nombre, data-posicion, data-valor, data-diferencia1,
    data-diferencia-pct1, data-equipo.
    """
    teams = _team_map(html)
    rows = []
    for chunk in html.split('class="elemento_jugador')[1:]:
        name, value = _attr(chunk, "nombre"), _attr(chunk, "valor")
        if not name or not value:
            continue
        team_id = _attr(chunk, "equipo")
        rows.append({
            "observed_at": observed_at,
            "name": name,
            "position": (_attr(chunk, "posicion") or "").lower(),
            "team_id": team_id,
            "team": teams.get(team_id or "", ""),
            "value": int(value),
            "delta_1d": _num(_attr(chunk, "diferencia1")),
            "delta_pct_1d": _num(_attr(chunk, "diferencia-pct1")),
            "slug": _slug(chunk),
            "player_path": _player_path(chunk),
        })
    return rows


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# Player photos live at /jugadores/ficha/<id>.png and appear BEFORE the player
# link in the markup, so a naive /jugadores/ match grabs the image. Worse,
# players with no photo all share 00.png. Match the anchor href specifically.
HREF_RE = re.compile(r'href="[^"]*?/jugadores/([^"?#]+)"')
PHOTO_RE = re.compile(r'/jugadores/ficha/(\d+)\.(?:png|jpg|jpeg|webp)')
ASSET_RE = re.compile(r'\.(png|jpg|jpeg|webp|svg)$', re.I)


def _player_path(chunk: str) -> str | None:
    for cand in HREF_RE.findall(chunk):
        cand = cand.rstrip("/")
        if cand and not ASSET_RE.search(cand):
            return cand
    return None


def _slug(chunk: str) -> str | None:
    """Stable per-player id. Prefers the player-page path; falls back to the
    numeric photo id, but never to the shared 00 placeholder."""
    path = _player_path(chunk)
    if path:
        parts = [p for p in path.split("/") if p and p != "ficha"]
        for p in parts:
            if p.isdigit() and p != "00":
                return p
        if parts:
            return parts[-1]
    m = PHOTO_RE.search(chunk)
    if m and m.group(1) != "00":
        return m.group(1)
    return None


# ---------------------------------------------------------------------------
# parse — probable XI
# ---------------------------------------------------------------------------

# "Carles Aleñá 50% 28 años Izquierdo 1.80m 0 0 ..." -> name is the text
# before the start percentage. Everything after it is biography and stat junk.
NAME_RE = re.compile(r"^(.*?)\s*(\d{1,3})\s*%")


def _name_from_blob(text: str) -> tuple[str | None, int | None]:
    m = NAME_RE.match(text)
    if m:
        return m.group(1).strip() or None, int(m.group(2))
    # No percentage: fall back to the text before the first digit.
    head = re.split(r"\d", text, 1)[0].strip()
    return (head or None), None


def parse_team(html: str, slug: str, observed_at: str) -> list[dict]:
    from lxml import html as lh

    doc = lh.fromstring(html)
    rows = []
    seen = set()

    def add(el, role):
        text = " ".join(el.text_content().split())
        name, pct = _name_from_blob(text)
        if not name or name.lower() in seen:
            return
        seen.add(name.lower())
        # Injury/doubt markers must come from the player element itself. Reading
        # the parent's classes tagged every starter in a section as injured.
        own = " ".join(el.classes).lower()
        status = ("injured" if "lesionad" in own
                  else "doubt" if "duda" in own or "sancion" in own
                  else "ok")
        href = el.get("href") or ""
        if not href:
            a = el.find(".//a[@href]")
            href = a.get("href") if a is not None else ""
        rows.append({
            "observed_at": observed_at,
            "team_slug": slug,
            "player_name": name,
            "player_slug": _slug(href) if href else None,
            "role": role,
            "start_pct": pct,
            "status": status,
        })

    for el in doc.cssselect('[class*="jugadores-titulares"] .jugador.tipo_lista'):
        add(el, "starter")
    for el in doc.cssselect('[class*="jugadores-suplentes"] .jugador.tipo_lista'):
        add(el, "sub")

    return rows


# ---------------------------------------------------------------------------
# parse driver
# ---------------------------------------------------------------------------

def parse() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    market_rows, xi_rows = [], []

    for snap in sorted(RAW.glob("dt=*")):
        observed_at = snap.name.removeprefix("dt=")
        for f in sorted(snap.glob("*.html.gz")):
            html = gzip.open(f, "rt", encoding="utf-8").read()
            kind = f.name.removesuffix(".html.gz")
            try:
                if kind == "market":
                    market_rows += parse_market(html, observed_at)
                elif kind.startswith("team_"):
                    xi_rows += parse_team(html, kind[5:], observed_at)
            except Exception as e:
                # One bad page must not lose the rest of the run.
                print(f"  warn: {snap.name}/{kind}: {type(e).__name__}: {e}")

    _write(OUT / "market.csv", market_rows)
    _write(OUT / "probable_xi.csv", xi_rows)

    # Fail loudly on an empty parse: a silently-empty probable XI would set
    # every start probability to zero and quietly bench your best players.
    if not market_rows:
        sys.exit("ERROR: market parse produced 0 rows — the markup changed.")
    print(f"market {len(market_rows)} rows, probable XI {len(xi_rows)} rows")


def _write(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "fetch"
    {"fetch": fetch, "parse": parse}[cmd]()
