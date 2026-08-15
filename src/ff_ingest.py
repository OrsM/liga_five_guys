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


# --- fitness ---------------------------------------------------------------
#
# CLASSES LIE HERE. `elemento lesionado elemento_jugador` is the generic class
# on every tile of the pitch graphic — the containers are literally called
# `jugadores-titulares-22421 mod lesionados`, and Barcelona alone carries 40 of
# them. Reading it as an injury marker flags the whole squad. That is the bug
# the previous version of this function was written to avoid, and it avoided it
# by never opening the real block at all.
#
# The real signal is the "Estado físico de la plantilla" panel, and the STATE
# comes from the icon's alt text, not from any class:
#
#   .lesionados_wrapper section.mod.lesionados > .elemento   alt=Lesionado
#                                                            alt=Duda
#                                                            alt=Tocado
#
# Suspensions live in `section.mod.sancionados`, but that class is reused by a
# transfer-listing box (`.mercado-box`) holding 214 elements league-wide, none
# of them suspended. Excluding it is not optional.
#
# `Tocado` is a knock the site still lists as available. It is folded into
# `doubt` rather than given a state of its own: the decision it should drive —
# think twice before fielding him — is the same one.

FITNESS_ALT = {
    "lesionado": "injured",
    "duda": "doubt",
    "tocado": "doubt",
}

# Anything worse than a doubt. Ordered worst-first: a player listed in two
# blocks keeps the more serious reading.
SEVERITY = ["unavailable", "suspended", "injured", "doubt"]


def _fold(name: str) -> str:
    """Accent- and case-insensitive key. The two blocks on a team page spell
    a player the same way, but not always with the same diacritics."""
    import unicodedata
    s = unicodedata.normalize("NFD", (name or "").lower())
    return " ".join("".join(c for c in s if c.isalnum() or c.isspace()).split())


def _flagged_name(el) -> tuple[str, str]:
    """(display name, player-page slug) from a fitness block element."""
    a = el.cssselect("a.jugador")
    if not a:
        return "", ""
    href = a[0].get("href") or ""
    return a[0].text_content().strip(), (_slug('href="%s"' % href) or "")


def _note(el) -> str:
    """The diagnosis and expected return, when the page carries one.

    'Rotura de lig. cruzado anterior — Desde 10/08 (5 días) — Baja hasta
    marzo' is the difference between knowing a player is hurt and knowing
    whether to sell him.
    """
    parts = [" ".join(c.text_content().split())
             for c in el.cssselect(".comentario")]
    return " · ".join(p for p in parts if p)[:200]


def parse_fitness(doc) -> dict[str, dict]:
    """{folded name: {name, slug, status, note}} for everyone flagged.

    Silence here is not a claim of fitness — a player absent from every block
    is simply one this page says nothing about, which is why the caller keeps
    'ok' and 'no data' distinguishable.
    """
    found: dict[str, dict] = {}

    def put(name, slug, status, note=""):
        key = _fold(name)
        if not key:
            return
        prev = found.get(key)
        if prev and SEVERITY.index(prev["status"]) <= SEVERITY.index(status):
            return
        found[key] = {"name": name, "slug": slug, "status": status,
                      "note": note}

    for el in doc.cssselect(
            ".lesionados_wrapper section.mod.lesionados > .elemento"):
        icon = el.cssselect(".icono img")
        alt = (icon[0].get("alt") or "").strip().lower() if icon else ""
        status = FITNESS_ALT.get(alt)
        if not status:
            continue
        name, slug = _flagged_name(el)
        put(name, slug, status, _note(el))

    for sec in doc.cssselect("section.mod.sancionados"):
        if "mercado-box" in " ".join(sec.classes):
            continue          # transfer listings, not suspensions
        for el in sec.cssselect(".elemento"):
            name, slug = _flagged_name(el)
            put(name, slug, "suspended", _note(el))

    for el in doc.cssselect("section.mod.nodisponibles .elemento"):
        name, slug = _flagged_name(el)
        put(name, slug, "unavailable", _note(el))

    return found


def parse_team(html: str, slug: str, observed_at: str) -> list[dict]:
    from lxml import html as lh

    doc = lh.fromstring(html)
    fitness = parse_fitness(doc)
    rows = []
    seen = set()

    def add(el, role):
        text = " ".join(el.text_content().split())
        name, pct = _name_from_blob(text)
        if not name or name.lower() in seen:
            return
        seen.add(name.lower())
        fit = fitness.get(_fold(name))
        href = el.get("href") or ""
        if not href:
            a = el.find(".//a[@href]")
            href = a.get("href") if a is not None else ""
        rows.append({
            "observed_at": observed_at,
            "team_slug": slug,
            "player_name": name,
            # _slug() reads a markup chunk, not a bare URL: it matches on
            # href="…". Passing the URL alone silently returned None for every
            # player ever parsed, which is why this column was empty.
            "player_slug": _slug('href="%s"' % href) if href else None,
            "role": role,
            "start_pct": pct,
            "status": fit["status"] if fit else "ok",
            "note": fit["note"] if fit else "",
        })

    for el in doc.cssselect('[class*="jugadores-titulares"] .jugador.tipo_lista'):
        add(el, "starter")
    for el in doc.cssselect('[class*="jugadores-suplentes"] .jugador.tipo_lista'):
        add(el, "sub")

    # A flagged player who appears in neither list still has to reach the CSV.
    # Dropping him would mean the one player the page is shouting about is the
    # one row we do not have.
    for key, fit in fitness.items():
        if key in {_fold(r["player_name"]) for r in rows}:
            continue
        rows.append({
            "observed_at": observed_at,
            "team_slug": slug,
            "player_name": fit["name"],
            "player_slug": fit["slug"] or None,
            "role": "absent",
            "start_pct": None,
            "status": fit["status"],
            "note": fit["note"],
        })

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

    # Print the status breakdown every run. The injury column sat on 'ok' for
    # 14,765 rows without anything noticing; a count in the log is what makes
    # that visible the next time the markup moves.
    tally: dict[str, int] = {}
    for r in xi_rows:
        tally[r["status"]] = tally.get(r["status"], 0) + 1
    flags = ", ".join("%s %d" % (k, v) for k, v in sorted(tally.items())
                      if k != "ok")
    print(f"market {len(market_rows)} rows, probable XI {len(xi_rows)} rows")
    print("  status: ok %d%s" % (tally.get("ok", 0),
                                 (", " + flags) if flags else ""))
    if not flags:
        print("  warn: no player flagged in any snapshot — if the site still "
              "shows injuries, the fitness selectors have rotted.")


def _write(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


# ---------------------------------------------------------------------------
# self-test
# ---------------------------------------------------------------------------

_FIXTURE = """
<html><body>
  <div class="relative campo-wrapper with-tabs liga">
    <div class="jugadores-titulares-22421 mod lesionados mb-0">
      <div class="elemento lesionado elemento_jugador jugador-1 clickable">
        <a href="/jugadores/joan-garcia" class="jugador tipo_lista">
          Joan Garcia 80% 24 a\u00f1os</a>
      </div>
    </div>
  </div>
  <div class="jugadores-titulares">
    <a href="https://www.futbolfantasy.com/jugadores/joan-garcia"
       class="jugador tipo_lista">Joan Garcia 80% 24 a\u00f1os 1.85m</a>
    <a href="https://www.futbolfantasy.com/jugadores/pedri"
       class="jugador tipo_lista">Pedri 70% 22 a\u00f1os</a>
  </div>
  <div class="jugadores-suplentes">
    <a href="https://www.futbolfantasy.com/jugadores/frenkie-de-jong"
       class="jugador tipo_lista">Frenkie de Jong 0% 28 a\u00f1os</a>
    <a href="https://www.futbolfantasy.com/jugadores/eric-garcia"
       class="jugador tipo_lista">Eric Garc\u00eda 50% 24 a\u00f1os</a>
  </div>

  <section class="mod sancionados mercado-box order-0 block-new">
    <header class="title">Mercado</header>
    <div class="elemento sancionado mercado">
      <a href="https://www.futbolfantasy.com/jugadores/pedri"
         class="jugador">Pedri</a>
    </div>
  </section>

  <section class="mod sancionados order-0 order-md-1 block-new">
    <header class="title">Sancionados</header>
    <div class="elemento sancionado">
      <a href="https://www.futbolfantasy.com/jugadores/eric-garcia"
         class="jugador">Eric Garc\u00eda</a>
    </div>
  </section>

  <div class="lesionados_wrapper">
    <section class="mod lesionados order-1 block-new">
      <header class="title">Estado f\u00edsico de la plantilla</header>
      <div class="elemento lesionado">
        <div class="icono"><img src="/lesionado_box_min.png" alt="Lesionado"/></div>
        <a href="https://www.futbolfantasy.com/jugadores/frenkie-de-jong"
           class="jugador">Frenkie de Jong</a>
        <div class="comentario"><span>Lesi\u00f3n de rodilla</span>
          <span>Baja hasta octubre</span></div>
      </div>
      <div class="elemento lesionado">
        <div class="icono"><img src="/disponible_box_min.png" alt="Tocado"/></div>
        <a href="https://www.futbolfantasy.com/jugadores/pedri"
           class="jugador">Pedri</a>
        <div class="comentario"><span>Molestias</span></div>
      </div>
      <div class="elemento lesionado">
        <div class="icono"><img src="/duda_box_min.png" alt="Duda"/></div>
        <a href="https://www.futbolfantasy.com/jugadores/owen-bosch"
           class="jugador">Owen Bosch</a>
      </div>
    </section>
  </div>

  <section class="mod nodisponibles order-2 block-new">
    <header class="title">No disponibles</header>
    <div class="elemento nodisponible">
      <a href="https://www.futbolfantasy.com/jugadores/facundo-garces"
         class="jugador">Facundo Garc\u00e9s</a>
    </div>
  </section>
</body></html>
"""


def _selftest() -> None:
    rows = parse_team(_FIXTURE, "test", "2026-01-01T0000Z")
    by = {r["player_name"]: r for r in rows}

    # THE TRAP: Joan Garcia sits inside a container classed
    # 'jugadores-titulares-22421 mod lesionados' and carries the class
    # 'elemento lesionado elemento_jugador' himself, but appears in no fitness
    # block. He is fit. Selecting on those classes would say otherwise.
    assert by["Joan Garcia"]["status"] == "ok", by["Joan Garcia"]
    assert by["Joan Garcia"]["role"] == "starter"

    # THE OTHER TRAP: Pedri is in a .mercado-box classed 'elemento sancionado'
    # — a transfer listing, not a suspension. His real state is the Tocado
    # icon, which folds into doubt.
    assert by["Pedri"]["status"] == "doubt", by["Pedri"]

    # A genuine suspension, from the section that is NOT the mercado box.
    assert by["Eric Garc\u00eda"]["status"] == "suspended", by["Eric Garc\u00eda"]

    # Injury, with the diagnosis and the expected return carried through.
    fdj = by["Frenkie de Jong"]
    assert fdj["status"] == "injured", fdj
    assert "rodilla" in fdj["note"] and "octubre" in fdj["note"], fdj

    # Unavailable is its own state and outranks nothing else here.
    assert by["Facundo Garc\u00e9s"]["status"] == "unavailable"
    assert by["Facundo Garc\u00e9s"]["role"] == "absent"

    # A flagged player in neither lineup list still reaches the CSV.
    assert by["Owen Bosch"]["status"] == "doubt"
    assert by["Owen Bosch"]["role"] == "absent"
    assert by["Owen Bosch"]["start_pct"] is None

    # Silence is not fitness: nobody gets a status the page did not give.
    assert {r["status"] for r in rows} == {
        "ok", "doubt", "suspended", "injured", "unavailable"}

    # player_slug is populated — it was None for every row ever parsed
    # because _slug() was handed a bare URL instead of a markup chunk.
    assert by["Pedri"]["player_slug"] == "pedri", by["Pedri"]
    assert all(r["player_slug"] for r in rows), \
        [r for r in rows if not r["player_slug"]]

    # Severity ordering: the worse reading wins when two blocks disagree.
    assert SEVERITY.index("injured") < SEVERITY.index("doubt")

    # Accent folding, so 'Eric García' in one block matches 'Eric Garcia' in
    # the other.
    assert _fold("Eric Garc\u00eda") == _fold("Eric Garcia") == "eric garcia"

    print("ff_ingest self-test OK (14 cases)")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "fetch"
    if cmd in ("--selftest", "selftest"):
        _selftest()
        raise SystemExit(0)
    {"fetch": fetch, "parse": parse}[cmd]()
