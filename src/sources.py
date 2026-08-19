"""
sources.py — what we collect, and how to read it. No network, no filesystem.

This is the registry. One entry per page we fetch, and everything that entry
needs to be useful lives on the entry itself:

    Source(key, table, url, parse, sign, cadence)

    key      the page's filename inside a snapshot: "market", "team_celta"
    table    which tidy table its rows land in
    url      where to get it
    parse    (html, observed_at, key) -> list[dict]
    sign     (html) -> str | None   content signature; see below
    cadence  "every_run" or "daily" — how often ingest sweeps it

Adding a source is one entry plus one parse function plus self-test cases. It
is not a new script, a new workflow input, or a new output file to wire into
the report. That is the point: the second probable-XI source is meant to cost
one entry, and the third likewise.

WHY THIS FILE EXISTS. Three modules used to fetch and parse this site —
ff_ingest.py, history.py and points.py — and two of them fetched the *same*
points page ff_ingest already snapshots twice a day. They carried four
different number parsers between them. history.py also imported httpx at
module level, which is the one thing the test job cannot install, so importing
it broke a machine that never intended to fetch anything. Parsing is pure and
now lives here; fetching and files live in ingest.py; nothing here imports a
network client, so the self-test runs with lxml alone.

SIGNATURES, and why they hash what they hash. ingest stores a page only when
its signature changed since the last time we saw it, which is how a season of
snapshots fits in a repo (see ingest.py). The signature is deliberately the
PARSER'S INPUT SURFACE — every string the selectors below can reach: text,
`href`, and `img alt`. Nothing else.

Two rejected alternatives, both measured:

  * Hashing the whole page drops 32% of fetches. The pages carry a news
    ticker, transfer rumours, cache-busted asset URLs and forum comment
    usernames, so 638 stored pages were byte-distinct — every one of them.
  * Hashing only the fields we extract today drops 87%, and is wrong. Raw is
    kept forever precisely so that a field we did not extract can be
    recovered by fixing `parse` and re-running over history. Key on extracted
    fields and that promise quietly becomes false: the page that first
    carried the new field was thrown away for looking unchanged.

The input surface drops 59%, which is the honest middle. It preserves any
change to content the selectors reach whether we read it yet or not, and
ignores only what no parser can ever see.

`sign` returns None when its selectors match NOTHING. That is the selector-rot
case, and it is the one time deduplication must not happen — a rotted page
looks identical to the last rotted page. ingest stores those unconditionally
and warns.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Callable, NamedTuple

from lxml import html as lh

__all__ = ["BASE", "SOURCE", "MARKET_URL", "POINTS_URL", "TEAM_URL", "TEAMS",
           "AF_BASE", "AF_SOURCE", "AF_TEAM_URL", "AF_TEAMS", "AF_HUB_URL",
           "Source", "sources", "source_for", "SEVERITY",
           "parse_market", "parse_team", "parse_points", "parse_fitness",
           "parse_af_team", "parse_af_fixtures", "season_label",
           "sign_market", "sign_team", "sign_points", "sign_af_team",
           "sign_af_fixtures",
           "CAL_KEY", "FF_CAL_URL", "MATCH_URL", "MATCH_KEY_RE",
           "parse_calendar", "parse_starters", "sign_calendar",
           "sign_starters", "match_source", "played_sources",
           "LFG_SOURCE", "API_LEAGUES_KEY", "API_LEAGUES_URL",
           "API_MARKET_URL", "API_ACTIVITY_URL", "API_TEAMS_URL",
           "ACT_KIND", "ACT_JOINED", "ACT_BUY", "ACT_SELL",
           "parse_api_leagues", "parse_api_market", "parse_api_activity",
           "parse_api_teams", "sign_api_leagues", "sign_api_market",
           "sign_api_activity", "sign_api_teams", "league_sources",
           "API_PLAYER_URL", "parse_api_player", "sign_api_player",
           "player_source", "player_sources"]

BASE = "https://www.futbolfantasy.com"
# The `source` column on every lineups row from this site. It exists so a
# second probable-XI site can be stored alongside rather than instead, and so
# a reader always knows which one it is looking at.
SOURCE = "futbolfantasy"
MARKET_URL = f"{BASE}/analytics/laliga-fantasy/mercado"
POINTS_URL = f"{BASE}/analytics/laliga-fantasy/puntos"
TEAM_URL = f"{BASE}/laliga/equipos/{{slug}}"

TEAMS = [
    "alaves", "athletic", "atletico", "barcelona", "betis", "celta",
    "deportivo", "elche", "espanyol", "getafe", "levante", "malaga",
    "osasuna", "racing", "rayo-vallecano", "real-madrid", "real-sociedad",
    "sevilla", "valencia", "villarreal",
]


# ---------------------------------------------------------------------------
# market values
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


def parse_market(html: str, observed_at: str, key: str = "market") -> list[dict]:
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


# ---------------------------------------------------------------------------
# probable XI + fitness
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


# CLASSES LIE HERE. `elemento lesionado elemento_jugador` is the generic class
# on every tile of the pitch graphic — the containers are literally called
# `jugadores-titulares-22421 mod lesionados`, and Barcelona alone carries 40 of
# them. Reading it as an injury marker flags the whole squad.
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

# The regions of a team page any parser here reads. Named once, because the
# signature has to hash exactly these and nothing else — a signature that
# drifts from the selectors is a silent data-loss bug.
XI_SELECTORS = ['[class*="jugadores-titulares"] .jugador.tipo_lista',
                '[class*="jugadores-suplentes"] .jugador.tipo_lista']
FITNESS_SELECTORS = [".lesionados_wrapper section.mod.lesionados > .elemento",
                     "section.mod.nodisponibles .elemento"]


def _fold(name: str) -> str:
    """Accent- and case-insensitive key. The two blocks on a team page spell
    a player the same way, but not always with the same diacritics."""
    import unicodedata
    s = unicodedata.normalize("NFD", (name or "").lower())
    return " ".join("".join(c for c in s if c.isalnum() or c.isspace()).split())


def _flagged_name(el) -> tuple[str, str]:
    """(display name, player-page slug) from a fitness block element."""
    a = _css(el, "a.jugador")
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
             for c in _css(el, ".comentario")]
    return " · ".join(p for p in parts if p)[:200]


def _suspension_sections(doc):
    """section.mod.sancionados, minus the transfer-listing box that shares
    the class. Shared with sign_team so the two cannot disagree."""
    return [s for s in _css(doc, "section.mod.sancionados")
            if "mercado-box" not in " ".join(s.classes)]


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

    for el in _css(doc, 
            ".lesionados_wrapper section.mod.lesionados > .elemento"):
        icon = _css(el, ".icono img")
        alt = (icon[0].get("alt") or "").strip().lower() if icon else ""
        status = FITNESS_ALT.get(alt)
        if not status:
            continue
        name, slug = _flagged_name(el)
        put(name, slug, status, _note(el))

    for sec in _suspension_sections(doc):
        for el in _css(sec, ".elemento"):
            name, slug = _flagged_name(el)
            put(name, slug, "suspended", _note(el))

    for el in _css(doc, "section.mod.nodisponibles .elemento"):
        name, slug = _flagged_name(el)
        put(name, slug, "unavailable", _note(el))

    return found


def parse_team(html: str, observed_at: str, key: str = "team_test") -> list[dict]:
    """Probable XI, bench and fitness for one team page.

    `key` is the snapshot page name, so the team slug is key without its
    "team_" prefix — the registry owns that naming, not the caller.

    Every row carries `source`, stamped here rather than by the caller, so the
    label cannot drift from the parser that produced the row. A second site's
    parse function stamps its own, and both land in the same lineups table.
    """
    slug = key[5:] if key.startswith("team_") else key
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
            "source": SOURCE,
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

    for el in _css(doc, XI_SELECTORS[0]):
        add(el, "starter")
    for el in _css(doc, XI_SELECTORS[1]):
        add(el, "sub")

    # A flagged player who appears in neither list still has to reach the CSV.
    # Dropping him would mean the one player the page is shouting about is the
    # one row we do not have.
    for fkey, fit in fitness.items():
        if fkey in {_fold(r["player_name"]) for r in rows}:
            continue
        rows.append({
            "observed_at": observed_at,
            "source": SOURCE,
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
# season points table
# ---------------------------------------------------------------------------

# Header text -> our column. Matched case-insensitively as a substring, so
# "PuntosPts" and "MediaMed" both land correctly.
WANT = {
    "name": ["jugador"],
    "points": ["puntos", "pts"],
    "games": ["pj"],
    "avg": ["media", "med"],
}


# COMPILED ONCE PER SELECTOR. lxml's .cssselect(css) translates the CSS to
# XPath and compiles it on EVERY call, and parse walks three hundred and
# eighty documents through thirty selectors — fifteen seconds of the run was
# recompiling the same handful of strings. The translation cannot change, so
# it is done once and kept.
_SELECTORS: dict[str, object] = {}


def _css(node, css: str):
    sel = _SELECTORS.get(css)
    if sel is None:
        from lxml.cssselect import CSSSelector
        sel = _SELECTORS[css] = CSSSelector(css)
    return sel(node)


def _cell_texts(el) -> list[str]:
    out = []
    for t in el.xpath(".//text()"):
        t = re.sub(r"\s+", " ", t).strip()
        if t:
            out.append(t)
    return out


def _map_headers(cells: list[str]) -> dict:
    """Map our field names onto this table's header row."""
    got = {}
    for i, raw in enumerate(cells):
        low = raw.lower()
        for field, needles in WANT.items():
            if field in got:
                continue
            if any(n in low for n in needles):
                got[field] = i
    return got


def parse_points(html: str, observed_at: str = "", key: str = "points") -> list[dict]:
    """Total points, matches played and average per player.

    The table has no position column — positions come from market.csv at
    report time. The player cell holds two names, the full one and the short
    display form; both are kept, because the market page uses the short form
    and the name is the only join key this site gives us.

    Numbers go through ffcore.parse.ratio, imported lazily so this module
    stays importable from anywhere: it used to have a fourth private copy of
    a float parser.
    """
    from ffcore.parse import ratio

    doc = lh.fromstring(html)
    best: list[dict] = []

    for table in doc.xpath("//table"):
        head = table.xpath(".//thead//tr")
        if not head:
            continue
        headers = [" ".join(_cell_texts(c))
                   for c in head[-1].xpath("./th|./td")]
        cols = _map_headers(headers)
        if not {"name", "points", "games"} <= set(cols):
            continue

        rows = []
        for tr in table.xpath(".//tbody//tr"):
            tds = tr.xpath("./td")
            if len(tds) <= max(cols.values()):
                continue
            names = _cell_texts(tds[cols["name"]])
            if not names:
                continue
            full = names[0]
            short = names[1] if len(names) > 1 else names[0]
            team = names[2] if len(names) > 2 else ""
            pts = ratio(" ".join(_cell_texts(tds[cols["points"]])[:1]))
            pj = ratio(" ".join(_cell_texts(tds[cols["games"]])[:1]))
            avg = None
            if "avg" in cols:
                avg = ratio(" ".join(_cell_texts(tds[cols["avg"]])[:1]))
            if avg is None and pts is not None and pj:
                avg = pts / pj
            if pts is None or pj is None:
                continue
            rows.append({
                "player_name": short,
                "player_name_full": full,
                "team": team,
                "points": f"{pts:g}",
                "games": f"{pj:g}",
                "avg": f"{avg:.3f}" if avg is not None else "",
            })

        if len(rows) > len(best):
            best = rows

    return best


def season_label(html: str) -> str:
    """Best-effort label like 2025-26, taken from the season selector."""
    hits = re.findall(r"20\d{2}\s*/\s*(?:20)?\d{2}", html)
    if hits:
        return re.sub(r"\s*/\s*", "-", hits[0])
    return "unknown"


# ---------------------------------------------------------------------------
# content signatures
# ---------------------------------------------------------------------------

_WS = re.compile(r"\s+")

# The market page's whole payload is data-* attributes on player elements, so
# its input surface is those attributes rather than a region of the document.
MARKET_SURFACE_RE = re.compile(
    r'data-(?:nombre|posicion|valor|diferencia1|diferencia-pct1|equipo)="[^"]*"')


def _digest(parts) -> str | None:
    """Signature over a list of strings, or None if the list is empty.

    None means "the selectors found nothing", which ingest treats as
    keep-this-page-and-warn. Do not turn it into a hash of the empty string:
    that would make every rotted page look like every other rotted page and
    silently drop the evidence.
    """
    if not any(parts):
        return None
    return hashlib.sha1("\x1f".join(parts).encode("utf-8")).hexdigest()[:16]


def _surface(elements) -> list[str]:
    """Every string a parser can read out of these elements: text, href, alt.

    Explicitly NOT attributes like data-posicionalternativa1-x, which is a
    pitch coordinate. It moves from "50%" to "52%" when nothing about the
    lineup changed, and hashing it cost 11 percentage points of deduplication
    for no information.
    """
    out: list[str] = []
    for el in elements:
        out.append(_WS.sub(" ", el.text_content()).strip())
        out += [a.get("href") or "" for a in _css(el, "a[href]")]
        out += [i.get("alt") or "" for i in _css(el, "img[alt]")]
    return out


def sign_market(html: str) -> str | None:
    return _digest(MARKET_SURFACE_RE.findall(html))


def sign_team(html: str) -> str | None:
    doc = lh.fromstring(html)
    els = []
    for sel in XI_SELECTORS + FITNESS_SELECTORS:
        els += _css(doc, sel)
    els += _suspension_sections(doc)
    return _digest(_surface(els))


# ---------------------------------------------------------------------------
# Analítica Fantasy — the second probable-XI source
# ---------------------------------------------------------------------------
#
# TWO PAGE SHAPES, depending on how close the next match is. Try Titulares,
# fall back to Consenso; neither means rot, which is the correct outcome.
#
#   1. Imminent: <ul aria-label="Titulares <Team>"> — their final call, and
#      binary, with no percentage on the page. `start_pct` stays EMPTY and
#      `note` says "titular". Never write 100: a confident editorial call is
#      not a stated probability and the report must tell them apart.
#   2. Further out: aria-label="Consenso de alineaciones" — three editors
#      split into Unánimes and Más divididos ("Aitor Paredes2/3 titular").
#      That fraction is a probability THEY published, so it lands in
#      `start_pct` as 100·n/d with the raw fraction kept in `note`.
#
# NO FITNESS on either shape, so `status` is "" (not stated), never "ok" —
# silence must not be stored as a clean bill of health. Their position codes
# are dropped: the app's positions are the ones the scorer uses.

AF_BASE = "https://www.analiticafantasy.com"
AF_SOURCE = "analitica"
AF_TEAM_URL = f"{AF_BASE}/equipo/{{slug}}"

# Our canonical team slug -> their path segment, which carries their own team
# id. Mapped rather than derived: "athletic" is "athletic-club-531" there, and
# guessing would break silently the day a promoted side arrives.
AF_TEAMS = {
    "alaves": "alaves-542", "athletic": "athletic-club-531",
    "atletico": "atletico-madrid-530", "barcelona": "barcelona-529",
    "betis": "real-betis-543", "celta": "celta-vigo-538",
    "deportivo": "deportivo-la-coruna-544", "elche": "elche-797",
    "espanyol": "espanyol-540", "getafe": "getafe-546",
    "levante": "levante-539", "malaga": "malaga-535",
    "osasuna": "osasuna-727", "racing": "racing-santander-4665",
    "rayo-vallecano": "rayo-vallecano-728", "real-madrid": "real-madrid-541",
    "real-sociedad": "real-sociedad-548", "sevilla": "sevilla-536",
    "valencia": "valencia-532", "villarreal": "villarreal-533",
}

AF_XI_SELECTOR = 'ul[aria-label^="Titulares"] li[aria-label^="Ver resumen de"]'
AF_NAME_PREFIX = "Ver resumen de "
AF_PHOTO_RE = re.compile(r"/jugadores/(\d+)\.(?:png|jpg|jpeg|webp)")

AF_CONSENSO_SELECTOR = '[aria-label="Consenso de alineaciones"]'
AF_UNANIMOUS = "Unánimes"
AF_DIVIDED = "Más divididos"
# "Aitor Paredes2/3 titular" — name, then the editor fraction. The trailing
# word matters: the captain-candidate block repeats a name with a bare "1/3"
# and no "titular", and counting that as a lineup call would double-count him.
AF_SPLIT_RE = re.compile(r"^(.+?)(\d+)\s*/\s*(\d+)\s+titular", re.S)
AF_FRACTION_RE = re.compile(r"\d+\s*/\s*\d+")


def _af_section(ul) -> str | None:
    """Which consensus heading this <ul> sits under, or None for a section we
    do not read.

    The IMMEDIATE parent only. Walking further up finds the div that wraps all
    three sections, whose text begins with the first heading — which labelled
    the captain-candidate list as "Unánimes" and stored a 1/3 player as a
    certain starter. An unrecognised section returns None and is skipped, so a
    fourth block appearing on their page is ignored rather than guessed at.
    """
    parent = ul.getparent()
    if parent is None:
        return None
    text = _WS.sub(" ", parent.text_content()).strip()
    for head in (AF_UNANIMOUS, AF_DIVIDED):
        if text.startswith(head):
            return head
    return None


def _af_row(observed_at, slug, name, img_src, role, start_pct, note) -> dict:
    m = AF_PHOTO_RE.search(img_src or "")
    return {
        "observed_at": observed_at,
        "source": AF_SOURCE,
        "team_slug": slug,
        "player_name": name,
        "player_slug": m.group(1) if m else None,
        "role": role,
        "start_pct": start_pct,
        "status": "",               # no fitness panel — "" is "not stated"
        "note": note,
    }


def parse_af_team(html: str, observed_at: str,
                  key: str = "af_test") -> list[dict]:
    """Analítica Fantasy's lineup call for one team, in whichever shape the
    page carries. See the block comment above for the two shapes.

    In the Titulares shape the name comes from the row's own aria-label, not
    from its visible text: the text is a truncated shirt-number-plus-name
    ("1 - Sivera") inside a CSS-truncated element, while the aria-label is the
    full name the site means. In the Consenso shape there is no aria-label per
    player and the visible text IS the name, with the fraction glued to it.

    Rows carry `source` so they can share the lineups table with futbolfantasy
    without either being mistaken for the other.
    """
    slug = key[3:] if key.startswith("af_") else key
    doc = lh.fromstring(html)
    rows, seen = [], set()

    def add(name, img_src, role, start_pct, note):
        if not name or name.lower() in seen:
            return
        seen.add(name.lower())
        rows.append(_af_row(observed_at, slug, name, img_src,
                            role, start_pct, note))

    def photo(li):
        img = _css(li, "img[src]")
        return img[0].get("src") if img else ""

    for li in _css(doc, AF_XI_SELECTOR):
        label = li.get("aria-label") or ""
        name = (label[len(AF_NAME_PREFIX):].strip()
                if label.startswith(AF_NAME_PREFIX) else "")
        # start_pct stays None: their final call is binary, not a percentage.
        add(name, photo(li), "starter", None, "titular")
    if rows:
        return rows

    for block in _css(doc, AF_CONSENSO_SELECTOR):
        for ul in _css(block, "ul"):
            section = _af_section(ul)
            if section is None:
                continue            # captain candidates and anything new
            for li in _css(ul, "li"):
                text = _WS.sub(" ", li.text_content()).strip()
                if section == AF_UNANIMOUS:
                    # Second guard: a name with any fraction glued to it is not
                    # a unanimous pick, whatever section it was found in.
                    if AF_FRACTION_RE.search(text):
                        continue
                    # Unanimous is 100% whatever the editor count is, so this
                    # needs no denominator and invents no constant.
                    add(text, photo(li), "starter", 100.0, "consenso unánime")
                    continue
                m = AF_SPLIT_RE.match(text)
                if not m:
                    continue
                name, n, d = m.group(1).strip(), int(m.group(2)), int(m.group(3))
                if not d:
                    continue
                add(name, photo(li), "doubt", round(100.0 * n / d, 1),
                    "consenso %d/%d" % (n, d))
    return rows


def sign_af_team(html: str) -> str | None:
    """Signs both shapes, so a page that switches shape is never called
    unchanged."""
    doc = lh.fromstring(html)
    return _digest(_surface(_css(doc, 'ul[aria-label^="Titulares"]')
                            + _css(doc, AF_CONSENSO_SELECTOR)))


# --- fixtures ---------------------------------------------------------------
#
# The hub page lists every upcoming match as <a href="/partido/<id>"> holding a
# <time datetime="...+00:00"> and the two crests as <img alt="<Team>">. That is
# the whole fixtures table, and it is what makes inputs/deadline.txt derivable
# instead of typed once a jornada.

AF_HUB_URL = f"{AF_BASE}/la-liga/alineaciones-probables"
AF_MATCH_RE = re.compile(r"/partido/(\d+)")


def parse_af_fixtures(html: str, observed_at: str,
                      key: str = "af_fixtures") -> list[dict]:
    """Upcoming matches with their kickoff, newest page wins.

    `kickoff` is stored exactly as they publish it — an ISO 8601 stamp with an
    explicit +00:00 offset — rather than reformatted into this repo's compact
    snapshot style. It is the one timestamp here that came from someone else,
    and rewriting it would hide that.

    A match already under way drops off their page, which is why this cannot
    be treated as a complete jornada calendar. It answers one question: what
    is the next kickoff.
    """
    doc = lh.fromstring(html)
    rows, seen = [], set()
    for a in _css(doc, 'a[href*="/partido/"]'):
        m = AF_MATCH_RE.search(a.get("href") or "")
        times = _css(a, "time[datetime]")
        teams = [i.get("alt") for i in _css(a, "img[alt]") if i.get("alt")]
        if not (m and times and len(teams) >= 2) or m.group(1) in seen:
            continue
        seen.add(m.group(1))
        rows.append({
            "observed_at": observed_at,
            "source": AF_SOURCE,
            "match_id": m.group(1),
            "kickoff": times[0].get("datetime"),
            "home": teams[0],
            "away": teams[1],
        })
    return rows


def sign_af_fixtures(html: str) -> str | None:
    return _digest(_surface(lh.fromstring(html).cssselect(
        'a[href*="/partido/"]')))


def sign_points(html: str) -> str | None:
    return _digest(_surface(lh.fromstring(html).cssselect("table")))


# ---------------------------------------------------------------------------
# who actually started — the outcome every probable-XI source is guessing at
# ---------------------------------------------------------------------------
#
# The ground truth that grades both probable-XI sources: the calendar says
# which matches were played, each played match page carries the confirmed
# elevens.
#
# THIS SITE AND NOT FBref, because the outcome rows carry the same
# /jugadores/<slug> ids as the probable-XI pages, so the join needs no name
# resolution — the step that would have dropped exactly the hard spellings
# ("U. Núñez", "El Hilali"). FBref is also behind a Cloudflare challenge.
# robots.txt checked 2026-08-16: "User-agent: *", empty Disallow.
#
# PAGE SHAPE: .stats-local / .stats-visitante, each one table.tablestats whose
# tbody alternates a player row (tr.plegado.plegable, name in td.name) with a
# detail row (tr.desglose) holding the only link to the player. A tr.header
# "Suplentes" splits eleven from bench. An UNPLAYED match has no table at all,
# which is why the calendar's score is the gate: no score, no request.
#
# Fetched ONCE ever. A confirmed eleven does not change after kickoff, and
# re-reading it for live stats we do not parse would cost 380 requests a day.

CAL_KEY = "calendario"
FF_CAL_URL = f"{BASE}/laliga/calendario"
MATCH_URL = f"{BASE}/partidos/{{path}}"

# "/partidos/22425-espanyol-levante" — the id alone is not enough to build the
# URL back, so the whole path segment is what a key carries.
MATCH_PATH_RE = re.compile(r"/partidos/(\d+-[a-z0-9-]+)")
MATCH_KEY_RE = re.compile(r"^match_(\d+-[a-z0-9-]+)$")
# Every link on the calendar reads "Jornada 1 3-0" once played and
# "Jornada 1 Lun 17/08 21:00h" before. The round is always there; a score means
# the ball has been kicked, and that is all we need to know.
CAL_JORNADA_RE = re.compile(r"Jornada\s*(\d+)")
CAL_SCORE_RE = re.compile(r"\b(\d+\s*-\s*\d+)\b")

MATCH_SIDES = (".stats-local", ".stats-visitante")   # home, away, in that order
MATCH_SUBS_HEADER = "Suplentes"
# The minute a player left the pitch, printed in the same cell as his name.
MATCH_MINUTE_RE = re.compile(r"\s*\d+\s*'\s*$")
# The match paths shorten exactly one club: /partidos/…-rayo-…, whose team page
# is /laliga/equipos/rayo-vallecano. Without this alias his 38 matches — a
# tenth of the season — split into nothing and vanish. Every other club spells
# the same in both places, checked against the whole 2026-27 calendar.
MATCH_ALIASES = {"rayo": "rayo-vallecano"}
# A confirmed eleven is eleven. Fewer means the page was caught half-rendered
# or the markup moved, and a nine-man "eleven" would quietly bias every hit
# rate computed from it downwards.
XI_SIZE = 11


def _match_sides(slug: str) -> tuple[str, str] | None:
    """("real-madrid", "real-sociedad") from "real-madrid-real-sociedad".

    Split against the known team slugs rather than on a hyphen: half the names
    in the league contain one. Anything that does not split into two teams we
    collect is not a match this repo can file — the calendar page also lists
    the second division — so it yields None and is skipped, never half-guessed.
    """
    known = {t: t for t in TEAMS}
    known.update(MATCH_ALIASES)
    for head in known:
        tail = slug[len(head) + 1:]
        if slug.startswith(head + "-") and tail in known:
            return known[head], known[tail]
    return None


def parse_calendar(html: str, observed_at: str,
                   key: str = "calendario") -> list[dict]:
    """Every match of the season: its path, its round, and its score if played.

    This is the only table here that says whether a match HAPPENED, which is
    what makes the starters sweep bounded: 380 pages across a season, each
    fetched once, and none of them fetched before there is anything on it.
    """
    doc = lh.fromstring(html)
    rows, seen = [], set()
    for a in _css(doc, 'a[href*="/partidos/"]'):
        m = MATCH_PATH_RE.search(a.get("href") or "")
        if not m or m.group(1) in seen:
            continue
        path = m.group(1)
        text = _WS.sub(" ", a.text_content()).strip()
        jor = CAL_JORNADA_RE.search(text)
        sides = _match_sides(path.split("-", 1)[1])
        if not (jor and sides):
            continue
        seen.add(path)
        score = CAL_SCORE_RE.search(text)
        rows.append({
            "observed_at": observed_at,
            "source": SOURCE,
            "match_id": path.split("-", 1)[0],
            "path": path,
            "jornada": int(jor.group(1)),
            "home": sides[0],
            "away": sides[1],
            "score": score.group(1).replace(" ", "") if score else "",
        })
    return rows


def sign_calendar(html: str) -> str | None:
    return _digest(_surface(lh.fromstring(html).cssselect(
        'a[href*="/partidos/"]')))


def _xi_rows(doc, side: str) -> list:
    """The player rows of the FIRST table.tablestats on that side of the page.

    A side has four .stats-local sections and two of them hold tables of their
    own — live stats and set pieces — repeating the same squad with FULL names
    and no links to their pages. Taking every match turned a 12-man bench into
    58 and lost the slug the whole join rests on. The first table is the fantasy
    points one, and it is the only one that carries tr.desglose links.
    """
    tables = _css(doc, "%s table.tablestats" % side)
    return _css(tables[0], "tbody tr") if tables else []


def parse_starters(html: str, observed_at: str,
                   key: str = "match_1-alaves-getafe") -> list[dict]:
    """The two confirmed elevens, plus the benches, for one played match.

    `role` uses the same two words the probable-XI table uses — "starter" and
    "sub" — because the whole point is to compare the two, and a second
    vocabulary would mean a translation step nobody would maintain.

    No jornada column: it lives on the matches row this joins to by match_id,
    and the match page states two round numbers (this one and the next), so
    reading it here would be guessing between them.
    """
    m = MATCH_KEY_RE.match(key)
    if not m:
        return []
    path = m.group(1)
    sides = _match_sides(path.split("-", 1)[1])
    if not sides:
        return []
    doc = lh.fromstring(html)
    rows = []

    for sel, team in zip(MATCH_SIDES, sides):
        side_rows, role = [], "starter"
        for tr in _xi_rows(doc, sel):
            classes = " ".join(tr.classes)
            if "header" in classes:
                if MATCH_SUBS_HEADER in tr.text_content():
                    role = "sub"
                continue
            cell = _css(tr, "td.name")
            if cell:
                side_rows.append({
                    "observed_at": observed_at,
                    "source": SOURCE,
                    "match_id": path.split("-", 1)[0],
                    "team_slug": team,
                    "player_name": MATCH_MINUTE_RE.sub(
                        "", _WS.sub(" ", cell[0].text_content()).strip()),
                    "player_slug": None,
                    "role": role,
                })
                continue
            # The detail row that follows a player carries the only link to
            # his page, and that slug is the join key. It arrives one row late,
            # so it is written back onto the row it belongs to.
            a = _css(tr, 'a[href*="/jugadores/"]')
            if a and side_rows:
                side_rows[-1]["player_slug"] = _slug(
                    'href="%s"' % (a[0].get("href") or ""))
        if sum(1 for r in side_rows if r["role"] == "starter") != XI_SIZE:
            continue        # not an eleven: see XI_SIZE
        rows += side_rows
    return rows


def sign_starters(html: str) -> str | None:
    els = []
    doc = lh.fromstring(html)
    for sel in MATCH_SIDES:
        els += _xi_rows(doc, sel)
    return _digest(_surface(els))


def match_source(key: str) -> Source | None:
    """The entry for one match page, built from its key.

    Match keys are not in the registry because their URLs are only known once
    the calendar has been read, and there are 380 of them a season. The key
    carries the whole path, so this needs no lookup table and an old snapshot
    stays parseable long after the match is forgotten.
    """
    m = MATCH_KEY_RE.match(key)
    if not m:
        return None
    return Source(key, "starters", MATCH_URL.format(path=m.group(1)),
                  parse_starters, sign_starters, cadence="once")


def played_sources(cal_html: str, observed_at: str = "") -> list[Source]:
    """One entry per match the calendar shows a score for, in calendar order.

    Handed to fetch the moment the calendar comes back, so a sweep discovers
    its own work list. `due()` drops the ones already stored, which is what
    makes this once-ever rather than daily.
    """
    return [match_source("match_%s" % r["path"])
            for r in parse_calendar(cal_html, observed_at) if r["score"]]


# ---------------------------------------------------------------------------
# Club Elo — how strong a team actually is, rather than how expensive
# ---------------------------------------------------------------------------
#
# The fixture term used summed squad value, a poor proxy twice over: a
# promoted side that spends is not thereby good, and one 100M signing moves
# the whole total. Elo is fitted on results, free, and published daily.
#
# NOT AN HTML TABLE, so nothing lxml-shaped may touch it — the country page
# embeds its ranking chart as a Vega-Lite spec, and the clubs are records in
# that spec's `datasets` with their federation and division on the record.
# That is a structured read, not a scrape of the rendered table beside it:
# a moved column cannot silently become a rating, and a renamed key yields
# nothing, which is the rot signal.
#
# ROBOTS: clubelo.com serves none (the path 302s away). One request a day.
#
# THE CSV API DIED WITH ITS HOST, 2026-08-17. `api.clubelo.com` still resolves
# to 37.128.134.74 and answers on neither 80 nor 443, from a home network and
# from a GitHub runner alike, while the SITE moved to a new one — clubelo.com
# now resolves into Cloudflare in front of an ondigitalocean.app, serves
# current ratings, and 302s `/API`, `/api/<date>` and `/<date>` to its
# homepage. There is no CSV endpoint on the new host to move to, so this reads
# the country page the site does publish. It cost two days of a fixture board
# ranked on pre-jornada ratings to notice, because a failed fetch leaves the
# last rows in place and every one of them still joins — see load_elo(), which
# now refuses a reading older than the cadence allows.
ELO_SOURCE = "clubelo"
ELO_URL = "https://clubelo.com/ESP"
ELO_COUNTRY = "ESP"
ELO_LEVEL = "1"
# The record shape, checked rather than assumed. `FedURL` and not `Federation`
# because it is the same three-letter code the old CSV filtered on, and a code
# does not get translated.
ELO_COLS = ("Name", "Elo", "FedURL", "Level")
# What the chart data is assigned to. The page carries exactly one of these;
# every one found is read, so a second chart is a second source of clubs and
# not a reason for the first to be missed.
ELO_MARK = "var vegaJson ="


def _elo_records(html: str) -> list[dict]:
    """Every club record in the page's chart data, or [].

    The spec is JSON in a <script>, so it is decoded rather than matched: a
    regex for the closing brace would end at the first nested one, and the
    spec is nothing but nested ones. `raw_decode` reads one value and stops
    where it ends, which is what makes the trailing `;` and the rest of the
    page harmless.
    """
    text, out, at = html or "", [], 0
    dec = json.JSONDecoder()
    while True:
        at = text.find(ELO_MARK, at)
        if at < 0:
            return out
        at += len(ELO_MARK)
        start = text.find("{", at)
        if start < 0:
            return out
        try:
            spec, at = dec.raw_decode(text, start)
        except ValueError:
            continue
        if not isinstance(spec, dict):
            continue
        for data in (spec.get("datasets") or {}).values():
            if isinstance(data, list):
                out += [r for r in data if isinstance(r, dict)
                        and all(c in r for c in ELO_COLS)]


def parse_elo(text: str, observed_at: str, key: str = "elo") -> list[dict]:
    """One row per Spanish top-flight club: its rating on the day we asked.

    THE CHART IS A TOP-N, not the division. It plots the strongest clubs in
    the country, so a top-flight side that sank below the cut is simply
    absent — and that is why `ffcore.fixture.elo_strength` refuses partial
    coverage rather than ranking nineteen clubs by Elo and one by its wallet.
    """
    rows = []
    for rec in _elo_records(text):
        if (str(rec["FedURL"]).strip() != ELO_COUNTRY
                or str(rec["Level"]).strip() != ELO_LEVEL):
            continue
        club = str(rec["Name"]).strip()
        try:
            rating = float(rec["Elo"])
        except (TypeError, ValueError):
            continue                  # a rating that is not a number is none
        if club:
            rows.append({"observed_at": observed_at, "source": ELO_SOURCE,
                         "club": club, "elo": str(rating)})
    return rows


def sign_elo(text: str) -> str | None:
    return _digest(["%s=%s" % (r["club"], r["elo"])
                    for r in parse_elo(text, "")])


# ---------------------------------------------------------------------------
# the league's own API — the state no public page publishes
# ---------------------------------------------------------------------------
#
# Everything above is a public page read anonymously. These four are LaLiga's
# own endpoints, behind the token ffcore/auth.py holds, and they carry what no
# scrape could: the live market (including players managers have listed), every
# transaction, and the balances. Worth a credential because the hand-typed
# alternatives went stale — see ledger.py.
#
# They return JSON, not HTML; `parse` and `sign` take text either way, as Club
# Elo's CSV does, so the registry needs no new concept.
#
# TERMS: the app's own API, read with the account's own credential for its own
# league. No robots.txt applies, but the same restraint does — ask once a day,
# cache, never poll.
LFG_SOURCE = "laliga"
API_LEAGUES_KEY = "api_leagues"
# {base} is filled by ingest from ffcore.auth.API_BASE rather than hardcoded,
# so the host lives in exactly one place — next to the token that opens it.
API_LEAGUES_URL = "{base}/v1/competition/1/leagues?x-lang=es"
API_MARKET_URL = "{base}/v1/competition/1/league/{league}/market?x-lang=es"
API_ACTIVITY_URL = ("{base}/v1/competition/1/leagues/{league}"
                    "/activity/{page}?x-lang=es")
API_TEAMS_URL = "{base}/v1/competition/1/leagues/{league}/teams?x-lang=es"

# The feed's verbs, decoded 2026-08-18 by checking each against the squad it
# should have produced: of five type-31 rows, four were still in the squad
# (the fifth was later sold); of five type-33, NONE were. A 9 is a manager
# joining — there were exactly five, one per manager.
ACT_JOINED, ACT_BUY, ACT_SELL = 9, 31, 33
ACT_KIND = {ACT_JOINED: "joined", ACT_BUY: "buy", ACT_SELL: "sell"}


def _j(text: str):
    """JSON or nothing. A rotted endpoint returns HTML, not an exception."""
    try:
        return json.loads(text or "")
    except (ValueError, TypeError):
        return None


def _pm(item: dict) -> dict:
    return (item or {}).get("playerMaster") or {}


def parse_api_leagues(text: str, observed_at: str,
                      key: str = "api_leagues") -> list[dict]:
    """One row per league this account plays in — normally exactly one.

    This is the discovery page: it is what turns a league id and a team id
    into the URLs of the three entries below, the same way the calendar turns
    into 380 match pages.
    """
    d = _j(text)
    if not isinstance(d, list):
        return []
    rows = []
    for lg in d:
        t = lg.get("team") or {}
        if not lg.get("id"):
            continue
        rows.append({
            "observed_at": observed_at, "source": LFG_SOURCE,
            "league_id": str(lg["id"]), "league_name": lg.get("name") or "",
            "access": lg.get("access") or "",
            "managers": str(lg.get("managersNumber") or ""),
            "team_id": str(t.get("id") or ""),
            # Your own balance, to the euro. The one number cash.txt asked you
            # to read off a screen. Rivals' is null here — see parse_api_teams.
            "money": str(t.get("money") or ""),
            "team_value": str(t.get("teamValue") or ""),
            "team_points": str(t.get("teamPoints") or ""),
        })
    return rows


def sign_api_leagues(text: str) -> str | None:
    return _digest(["%s=%s/%s" % (r["league_id"], r["money"], r["team_value"])
                    for r in parse_api_leagues(text, "")])


def parse_api_market(text: str, observed_at: str,
                     key: str = "api_market") -> list[dict]:
    """Everything on offer in this league right now.

    Two kinds of row, and the difference matters: `marketPlayerLeague` is the
    app dealing a free agent, `marketPlayerTeam` is a manager listing one of
    his own. The OCR slate only ever saw the first kind and only as many as
    fitted on a screenshot — the live feed carried 41 rows the day this was
    written, 13 of them app-dealt and 28 listed by managers.

    `numberOfBids` is the genuinely new thing. Nothing else in this repo can
    see how many people are already bidding on a player.
    """
    d = _j(text)
    if not isinstance(d, list):
        return []
    rows = []
    for it in d:
        pm = _pm(it)
        if not pm.get("id"):
            continue
        rows.append({
            "observed_at": observed_at, "source": LFG_SOURCE,
            "market_id": str(it.get("id") or ""),
            "player_id": str(pm["id"]),
            "player_name": pm.get("nickname") or pm.get("name") or "",
            # BOTH, because neither joins alone — see parse_api_teams.
            "player_name_full": (pm.get("name") or "")
                                if pm.get("nickname") else "",
            "position_id": str(pm.get("positionId") or ""),
            "sale_price": str(it.get("salePrice") or ""),
            "market_value": str(pm.get("marketValue") or ""),
            "bids": "" if it.get("numberOfBids") is None
                    else str(it["numberOfBids"]),
            "seller": it.get("discr") or "",
            "status": it.get("status") or "",
            "expires_at": it.get("expirationDate") or "",
        })
    return rows


def sign_api_market(text: str) -> str | None:
    # The slate turns over on a clock and prices move; both belong in the
    # signature, and expirationDate deliberately does NOT — it ticks down
    # continuously and would store a fresh archive every single sweep.
    return _digest(["%s@%s/%s" % (r["player_id"], r["sale_price"], r["bids"])
                    for r in parse_api_market(text, "")])


def parse_api_activity(text: str, observed_at: str,
                       key: str = "api_activity") -> list[dict]:
    """The league's transaction feed — what `transactions.csv` was typed from.

    The feed names only `user1Id`, never a counterparty, so a row says "X
    bought" or "X sold" and not who from. That is enough: replaying buys and
    sells in order reconstructs ownership exactly, and the counterparty is
    recoverable from whoever last held the player.

    Rows are stamped with the kind rather than the raw id, because 31 and 33
    are meaningless three months from now and the mapping was established
    empirically (see ACT_KIND).
    """
    d = _j(text)
    if not isinstance(d, list):
        return []
    rows = []
    for a in d:
        tid = a.get("activityTypeId")
        if tid not in ACT_KIND or not a.get("id"):
            continue
        rows.append({
            "observed_at": observed_at, "source": LFG_SOURCE,
            "activity_id": str(a["id"]),
            "at": a.get("createdAt") or "",
            "kind": ACT_KIND[tid],
            "user_id": str(a.get("user1Id") or ""),
            "player_id": str(a.get("playerMasterId") or ""),
            "amount": str(a.get("amount") or ""),
        })
    return rows


def sign_api_activity(text: str) -> str | None:
    # Append-only in practice, so the newest id would do — but a feed that
    # rewrote history would then look unchanged, and this feed is about to
    # become the ledger. Hash every id.
    return _digest(sorted(r["activity_id"]
                          for r in parse_api_activity(text, "")))


def parse_api_teams(text: str, observed_at: str,
                    key: str = "api_teams") -> list[dict]:
    """Every squad in the league, as the app holds it — one row per player.

    This is ownership without a replay: no ledger, no starting roster, no
    accumulated drift. It also carries each manager's points and position.

    ONE THING IT WILL NOT TELL YOU: `teamMoney` is null for everyone but you.
    Rivals' cash stays an estimate and `inputs/cash.txt` keeps its job — the
    `~` in the reports is still honest. Verified 2026-08-18: of five teams,
    only the account's own carried a balance.
    """
    d = _j(text)
    if not isinstance(d, list):
        return []
    rows = []
    for t in d:
        m = t.get("manager") or {}
        for p in (t.get("players") or []):
            pm = _pm(p)
            if not pm.get("id"):
                continue
            rows.append({
                "observed_at": observed_at, "source": LFG_SOURCE,
                "team_id": str(t.get("id") or ""),
                "user_id": str(m.get("id") or ""),
                "manager": m.get("managerName") or "",
                "position": str(t.get("position") or ""),
                "team_points": str(t.get("teamPoints") or ""),
                # Empty for everyone but you. Empty means NOT STATED, never
                # zero — the same rule the fitness parser follows.
                "team_money": str(t.get("teamMoney") or ""),
                "player_id": str(pm["id"]),
                # TWO NAMES, AND BOTH ARE NEEDED. The app publishes a
                # nickname and a full name and neither joins the market on
                # its own: of the 76 owned players on 2026-08-19, twelve join
                # only on the nickname ("Raphinha", "Pepelu", "Gavi") and
                # three only on the full name — "Aimar" is Aimar Oroz,
                # "Brahim" is Brahim Díaz, and "Llorente" is one of the two
                # the market carries. Keeping the nickname alone is what left
                # ffcore.league.api_key with a ledger tie-breaker to build.
                # The nickname stays `player_name` because it is the better
                # single guess; the full name rides beside it, and is empty
                # rather than duplicated when there is only one name.
                "player_name": pm.get("nickname") or pm.get("name") or "",
                "player_name_full": (pm.get("name") or "")
                                    if pm.get("nickname") else "",
                "position_id": str(pm.get("positionId") or ""),
                "market_value": str(pm.get("marketValue") or ""),
                "points": str(pm.get("points") or ""),
                "buyout": str(p.get("buyoutClause") or ""),
                # WHEN THE CLAUSE CAN ACTUALLY BE PAID. A transfer locks it
                # for about a week, and on 2026-08-18 every one of the 76
                # rival players in this league was locked — the whole steal
                # side of the report was recommending moves the app would
                # refuse. Empty means NOT STATED, never "available now".
                "buyout_until": str(p.get("buyoutClauseLockedEndTime") or ""),
            })
    return rows


def sign_api_teams(text: str) -> str | None:
    return _digest(["%s:%s" % (r["team_id"], r["player_id"])
                    for r in parse_api_teams(text, "")]
                   + ["$%s=%s" % (r["team_id"], r["team_money"])
                      for r in parse_api_teams(text, "")])


# One player, fetched once ever, purely to put a name to an id the activity
# feed mentions. See the self-test for why nothing else can do it.
API_PLAYER_URL = "{base}/v1/competition/1/player/{pid}?x-lang=es"
API_PLAYER_KEY_RE = re.compile(r"^api_player_(\d+)$")


def parse_api_player(text: str, observed_at: str,
                     key: str = "api_player_0") -> list[dict]:
    """One row naming one player.

    The id comes from the KEY, not the body: a payload that stopped carrying
    `id` would otherwise produce rows keyed on nothing.

    `player_name` is the NICKNAME ("Hugo Duro") because that is futbolfantasy's
    spelling and therefore what every join here goes through; the legal name
    rides along for when the nickname is too short to resolve.
    """
    d = _j(text)
    if not isinstance(d, dict):
        return []
    m = API_PLAYER_KEY_RE.match(key or "")
    pid = m.group(1) if m else str(d.get("id") or "")
    if not pid:
        return []
    return [{
        "observed_at": observed_at, "source": LFG_SOURCE,
        "player_id": pid,
        "player_name": d.get("nickname") or d.get("name") or "",
        "full_name": d.get("name") or "",
        "position_id": str(d.get("positionId") or ""),
        "market_value": str(d.get("marketValue") or ""),
        "team_id": str(d.get("teamId") or ""),
    }]


def sign_api_player(text: str) -> str | None:
    rows = parse_api_player(text, "")
    return _digest([rows[0]["player_name"]]) if rows else None


def player_source(key: str) -> Source | None:
    """The entry for one player lookup, rebuilt from its key."""
    m = API_PLAYER_KEY_RE.match(key or "")
    if not m:
        return None
    return Source(key, "api_players",
                  API_PLAYER_URL.format(base="{base}", pid=m.group(1)),
                  parse_api_player, sign_api_player, cadence="once", auth=True)


def player_sources(activity_json: str, observed_at: str = "") -> list[Source]:
    """One lookup per player the activity feed mentions, in feed order.

    Queued the moment the feed comes back, exactly like the calendar's match
    pages, and deduplicated the same way: cadence "once" means `due()` skips
    every id already in the store, so this is ~50 requests on the first sweep
    and none on the next.
    """
    out, seen = [], set()
    for r in parse_api_activity(activity_json, observed_at):
        pid = r.get("player_id")
        if not pid or pid in seen:
            continue
        seen.add(pid)
        out.append(player_source("api_player_%s" % pid))
    return out


def api_source(key: str) -> Source | None:
    """The entry for a stored API page, rebuilt from its key alone.

    The three below are queued at run time from a league id, so they are not
    in the registry and `source_for` cannot find them — exactly the problem
    `match_source` solves for match pages, and solved the same way. Parsing a
    stored page needs the parser and the table, never the URL, so the URL here
    is the un-substituted template: a snapshot stays readable years after the
    league id in it has stopped meaning anything.
    """
    table = {"api_market": (API_MARKET_URL, parse_api_market, sign_api_market),
             "api_teams": (API_TEAMS_URL, parse_api_teams, sign_api_teams)}
    if key.startswith("api_activity_"):
        return Source(key, "api_activity", API_ACTIVITY_URL,
                      parse_api_activity, sign_api_activity, auth=True)
    if key in table:
        url, p, s = table[key]
        return Source(key, key, url, p, s,
                      cadence="daily" if key == "api_teams" else "every_run",
                      auth=True)
    return None


def league_sources(leagues_json: str, observed_at: str = "") -> list[Source]:
    """The three entries whose URLs the discovery page just revealed.

    Handed to fetch the moment `api_leagues` comes back, exactly as
    `played_sources` is handed the calendar. The league id is therefore never
    configured anywhere: it is read from the account that owns it, so it
    cannot go stale and there is no id to paste into league.ini.
    """
    out = []
    for r in parse_api_leagues(leagues_json, observed_at):
        lg = r["league_id"]
        out.append(Source("api_market", "api_market",
                          API_MARKET_URL.format(base="{base}", league=lg),
                          parse_api_market, sign_api_market, auth=True))
        # EVERY RUN, not daily. It was daily to save a call, and the cost of
        # that was the squad being up to a day stale — so the report went on
        # telling you to sell a player you had already sold, and the rerun
        # button, whose entire purpose is picking up a deal you just made,
        # could not see it. One extra API call a run against the report being
        # wrong about what you own is not a trade.
        out.append(Source("api_teams", "api_teams",
                          API_TEAMS_URL.format(base="{base}", league=lg),
                          parse_api_teams, sign_api_teams, auth=True))
        # Page 0 is the newest ~55 rows and page 1 the remainder; the feed is
        # short because the season is young. Both are swept so the ledger can
        # be rebuilt from scratch rather than appended to, which is what makes
        # a missed run harmless.
        for page in (0, 1):
            out.append(Source(
                "api_activity_%d" % page, "api_activity",
                API_ACTIVITY_URL.format(base="{base}", league=lg, page=page),
                parse_api_activity, sign_api_activity, auth=True))
    return out


# ---------------------------------------------------------------------------
# the registry
# ---------------------------------------------------------------------------

class Source(NamedTuple):
    key: str                      # page name inside a snapshot
    table: str                    # tidy table its rows feed
    url: str
    parse: Callable               # (html, observed_at, key) -> rows
    sign: Callable                # (html) -> signature or None
    cadence: str = "every_run"    # "every_run" | "daily" | "once"
    # Seconds to wait on THIS host. One global timeout means a single dead
    # source holds the whole sweep for it: Club Elo has been timing out since
    # 2026-08-17 and was costing thirty of the thirty-two seconds a sweep took
    # — 94% of it, for a page that never arrived. A source nothing depends on
    # gets a short one and the sweep moves on.
    timeout: float | None = None
    enabled: bool = True
    # Does this page need the league bearer token? The entry says THAT it
    # does; ingest.py knows HOW, because this module is pure by design and a
    # credential means a file to read and a token to refresh. Keeping the two
    # apart is what lets sources.py self-test with lxml and nothing else.
    auth: bool = False


def sources(enabled_only: bool = True) -> list[Source]:
    """Every page we collect, in fetch order.

    Team pages are one entry each rather than one entry with twenty URLs, so
    that a per-page signature, a per-page failure and a per-page cadence all
    have somewhere to live.
    """
    out = [
        Source("market", "market", MARKET_URL, parse_market, sign_market),
        Source("points", "points", POINTS_URL, parse_points, sign_points),
    ]
    # Both team sweeps run once a day. Twice a day was forty requests for a
    # page whose editorial XI moved for 22 of 511 players across a fortnight;
    # once a day for two sources costs the same forty and answers more.
    out += [Source(f"team_{s}", "lineups", TEAM_URL.format(slug=s),
                   parse_team, sign_team, cadence="daily") for s in TEAMS]
    out += [Source(f"af_{s}", "lineups", AF_TEAM_URL.format(slug=af),
                   parse_af_team, sign_af_team, cadence="daily")
            for s, af in sorted(AF_TEAMS.items())]
    # One page, and it replaces a file you had to retype every jornada.
    out += [Source("af_fixtures", "fixtures", AF_HUB_URL,
                   parse_af_fixtures, sign_af_fixtures, cadence="daily")]
    # A rating changes when matches are played, so once a day is generous.
    # EIGHT SECONDS, NOT THIRTY. Club Elo is the one source nothing depends
    # on — a missing rating sends the fixture board back to squad value, which
    # is where it was before Elo existed — and while the dead API host was
    # still in this slot it timed out on every sweep, costing thirty of the
    # thirty-two seconds a sweep took. The new host answers in a third of a
    # second; the short timeout stays, because the reason it is short is what
    # this source is worth, not who was hosting it.
    out += [Source("elo", "elo", ELO_URL, parse_elo, sign_elo,
                   cadence="daily", timeout=8.0)]
    # The whole season's results in one page. It is what tells the starters
    # sweep which match pages exist and which are worth asking for.
    out += [Source(CAL_KEY, "matches", FF_CAL_URL, parse_calendar,
                   sign_calendar, cadence="daily")]
    # The league's own API. Only the discovery page is listed: the market,
    # activity and squad URLs all carry a league id that this page is what
    # tells us, so they are added to the sweep at run time by
    # league_sources() — the same shape as the calendar and its match pages.
    out += [Source(API_LEAGUES_KEY, "api_leagues", API_LEAGUES_URL,
                   parse_api_leagues, sign_api_leagues, auth=True)]
    return [s for s in out if s.enabled or not enabled_only]


def source_for(key: str) -> Source | None:
    """The registry entry for a stored page name, or None if we no longer
    collect it. Old snapshots outlive registry entries, so parse has to cope
    with a page nothing claims."""
    for s in sources(enabled_only=False):
        if s.key == key:
            return s
    # Match pages and the API's discovered pages are built from their key
    # rather than listed, so a stored one resolves here whether the calendar
    # or the league still mentions it or not.
    return match_source(key) or api_source(key) or player_source(key)


# ---------------------------------------------------------------------------
# self-test
# ---------------------------------------------------------------------------

_FIXTURE = """
<html><body>
  <div class="relative campo-wrapper with-tabs liga">
    <div class="jugadores-titulares-22421 mod lesionados mb-0">
      <div class="elemento lesionado elemento_jugador jugador-1 clickable">
        <a href="/jugadores/joan-garcia" class="jugador tipo_lista">
          Joan Garcia 80% 24 años</a>
      </div>
    </div>
  </div>
  <div class="jugadores-titulares">
    <a href="https://www.futbolfantasy.com/jugadores/joan-garcia"
       class="jugador tipo_lista">Joan Garcia 80% 24 años 1.85m</a>
    <a href="https://www.futbolfantasy.com/jugadores/pedri"
       class="jugador tipo_lista">Pedri 70% 22 años</a>
  </div>
  <div class="jugadores-suplentes">
    <a href="https://www.futbolfantasy.com/jugadores/frenkie-de-jong"
       class="jugador tipo_lista">Frenkie de Jong 0% 28 años</a>
    <a href="https://www.futbolfantasy.com/jugadores/eric-garcia"
       class="jugador tipo_lista">Eric García 50% 24 años</a>
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
         class="jugador">Eric García</a>
    </div>
  </section>

  <div class="lesionados_wrapper">
    <section class="mod lesionados order-1 block-new">
      <header class="title">Estado físico de la plantilla</header>
      <div class="elemento lesionado">
        <div class="icono"><img src="/lesionado_box_min.png" alt="Lesionado"/></div>
        <a href="https://www.futbolfantasy.com/jugadores/frenkie-de-jong"
           class="jugador">Frenkie de Jong</a>
        <div class="comentario"><span>Lesión de rodilla</span>
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
         class="jugador">Facundo Garcés</a>
    </div>
  </section>
</body></html>
"""

_MARKET_FIXTURE = """
<html><body>
<select name="equipo"><option value="0">Todos</option>
  <option value="7">Barcelona</option></select>
<div class="elemento_jugador" data-nombre="Pedri" data-posicion="Mediocampista"
     data-valor="21500000" data-diferencia1="150000"
     data-diferencia-pct1="0.7" data-equipo="7">
  <img src="/jugadores/ficha/1234.png"/>
  <a href="https://www.futbolfantasy.com/jugadores/pedri">Pedri</a>
</div>
</body></html>
"""

_POINTS_FIXTURE = """
<html><body>
<select name="temporada"><option>2025 / 26</option></select>
<table><thead><tr><th>Jugador</th><th>PuntosPts</th><th>PJ</th>
  <th>MediaMed</th></tr></thead>
<tbody>
  <tr><td><span>Íñigo Ruiz de Galarreta</span>
          <span>R. de Galarreta</span><span>Athletic</span></td>
      <td>156</td><td>34</td><td>4,59</td></tr>
  <tr><td><span>Sin Datos</span></td><td>-</td><td>-</td><td>-</td></tr>
</tbody></table>
</body></html>
"""


# Analítica Fantasy, trimmed to the structure the parser depends on: the
# starters list keyed by aria-label, the shirt-number-plus-name visible text
# the parser must NOT read, the photo URL carrying their player id, and a
# position chip we deliberately ignore. "Aitor Mañas" appears twice — once on
# the pitch graphic, once in the list — because the real page renders both.
_AF_FIXTURE = """<html><body>
<div><img alt="Foto de Aitor Ma\u00f1as"
     src="https://assets.analiticafantasy.com/jugadores/1.png?v=13&width=90"/>
     <button>Aitor Ma\u00f1as</button></div>
<ul role="tabpanel" aria-label="Titulares Test">
  <li role="button" aria-label="Ver resumen de Sivera">
    <img alt="Foto de Sivera"
         src="https://assets.analiticafantasy.com/jugadores/47353.png?v=13&width=66"/>
    <span title="Portero">PT</span>
    <p><span>1</span><span> - </span>Sivera</p></li>
  <li role="button" aria-label="Ver resumen de Aitor Ma\u00f1as">
    <img alt="Foto de Aitor Ma\u00f1as"
         src="https://assets.analiticafantasy.com/jugadores/1.png?v=13&width=66"/>
    <span title="Delantero">DL</span>
    <p><span>9</span><span> - </span>Aitor Ma\u00f1as</p></li>
  <li role="button" aria-label="Ver resumen de Sin Foto">
    <span title="Defensa">DF</span>
    <p><span>4</span><span> - </span>Sin Foto</p></li>
</ul>
<ul aria-label="Suplentes Test">
  <li role="button" aria-label="Ver resumen de No Deberia">x</li>
</ul>
</body></html>"""


# The other shape, which 16 of 20 pages carried on the first live sweep. Nesting
# copied from the real page, because the nesting IS the thing under test: all
# three lists sit in sibling divs inside one wrapper whose text begins with the
# first heading. Reading the heading off an ancestor instead of the immediate
# parent stored the captain candidate — "Nico Williams1/3", a 1-of-3 pick — as a
# unanimous starter at 100%.
_AF_CONSENSO_FIXTURE = """<html><body>
<section aria-label="Consenso de alineaciones">
  <div>
    <div><h3>Unánimes</h3><p>2 jugadores en el once de todos</p>
      <ul>
        <li><img src="https://assets.analiticafantasy.com/jugadores/47270.png?v=13&width=36"/>
            <span>Unai Simón</span></li>
        <li><img src="https://assets.analiticafantasy.com/jugadores/47273.png?v=13&width=36"/>
            <span>Yuri</span></li>
      </ul></div>
    <div><h3>Más divididos</h3><p>Titulares en algunos editores</p>
      <ul>
        <li><img src="https://assets.analiticafantasy.com/jugadores/183849.png?v=13&width=33"/>
            <span>Aitor Paredes</span><span>2/3 titular</span></li>
        <li><img src="https://assets.analiticafantasy.com/jugadores/84086.png?v=13&width=33"/>
            <span>Robert Navarro</span><span>1/3 titular</span></li>
      </ul></div>
    <div><h3>Candidato a capitán</h3><p>Editores que marcan</p>
      <ul>
        <li><img src="https://assets.analiticafantasy.com/jugadores/183799.png?v=13&width=33"/>
            <span>Nico Williams</span><span>1/3</span></li>
      </ul></div>
  </div>
</section>
</body></html>"""

# The hub page, trimmed to one match link. The bare /partido/ link with no
# <time> is the "Once posibles" teaser the page repeats without a kickoff.
_AF_HUB_FIXTURE = """<html><body>
<a href="/partido/100011934">
  <time datetime="2026-08-15T19:30:00+00:00">15 ago, 21:30</time>
  <span>Once posibles →</span>
  <img alt="Sevilla" src="/escudos/536.png"/><span>Sevilla</span>
  <img alt="Rayo Vallecano" src="/escudos/728.png"/><span>Rayo Vallecano</span>
</a>
<a href="/partido/100011934">duplicate, same id</a>
<a href="/partido/999">no time, no crests</a>
</body></html>"""

# Club Elo's published shape: the chart spec the country page embeds, trimmed
# from the real 2026-08-19 /ESP page. Worldwide and multi-division — which is
# the whole reason the parser filters on federation and level rather than
# trusting what the chart happens to plot.
_ELO_FIXTURE = """<!DOCTYPE html><html><body>
<h2><a href="ESP/Ranking">Ranking</a></h2>
<div id="chartEloGolo" style="width: 100%;"></div>
<script type="text/javascript">
            var vegaJson = {
  "$schema": "https://vega.github.io/schema/vega-lite/v5.20.1.json",
  "config": {"background": "#A2AAA5", "view": {"continuousWidth": 300}},
  "datasets": {
    "data-4f53cda18c2baa0c0354bb5f9a3ecbe5": [],
    "data-7c739729bfdfdd6abc8ff5e88cc19d07": [
      {"Colour": "#A4234B", "Elo": 2043.1, "FedURL": "ESP",
       "Federation": "Spain", "Golo": 2.029053, "Level": 1,
       "Name": "Barcelona", "TLC": "BAR"},
      {"Colour": "#DC052D", "Elo": 2010.4, "FedURL": "GER",
       "Federation": "Germany", "Golo": 1.94, "Level": 1,
       "Name": "Bayern", "TLC": "BAY"},
      {"Colour": "#FFFFFF", "Elo": 1988.7, "FedURL": "ESP",
       "Federation": "Spain", "Golo": 1.585982, "Level": 1,
       "Name": "Real Madrid", "TLC": "RMA"},
      {"Colour": "#00913F", "Elo": 1602.5, "FedURL": "ESP",
       "Federation": "Spain", "Golo": 1.08, "Level": 1,
       "Name": "Elche", "TLC": "ELC"},
      {"Colour": "#0B4EA2", "Elo": 1521.0, "FedURL": "ESP",
       "Federation": "Spain", "Golo": 1.01, "Level": 2,
       "Name": "Zaragoza", "TLC": "ZAR"}
    ]
  },
  "mark": {"type": "point"}
};
        </script>
</body></html>"""


# --- the API, trimmed from real 2026-08-18 payloads ------------------------
# Trimmed, not invented: field names, the string/int mixture and the nulls are
# exactly as the API returns them. The nulls are the point of several cases
# below — `teamMoney` is null for rivals, `numberOfBids` is null on a
# manager-listed player, and both must read as NOT STATED rather than zero.
_API_LEAGUES_FIXTURE = """[{"id":"017998544","access":"private",
 "name":"Some Guys","managersNumber":5,
 "team":{"id":"38091967","money":23596582,"teamValue":213113164,
         "teamPoints":17,"playersNumber":14}}]"""

_API_MARKET_FIXTURE = """[
 {"id":"m1","salePrice":5552694,"numberOfBids":0,"status":"on_sale",
  "discr":"marketPlayerLeague","expirationDate":"2026-08-18T22:00:00+02:00",
  "playerMaster":{"id":"2621","nickname":"Simeone","positionId":5,
                  "name":"Giuliano Simeone","marketValue":5552694}},
 {"id":"m2","salePrice":5403735,"numberOfBids":null,"status":"on_sale",
  "discr":"marketPlayerTeam","expirationDate":"2026-08-19T22:00:00+02:00",
  "playerMaster":{"id":"2963","nickname":"Marc Roca","positionId":3,
                  "marketValue":5100000}},
 {"id":"m3","salePrice":1,"playerMaster":{}}]"""

_API_PLAYER_FIXTURE = """{"id":"1191","name":"Hugo Duro Perales",
 "nickname":"Hugo Duro","positionId":4,"marketValue":8534068,
 "teamId":"12","points":0}"""

_API_ACTIVITY_FIXTURE = """[
 {"id":"a1","activityTypeId":31,"amount":58220110,"playerMasterId":1337,
  "user1Id":11881989,"createdAt":"2026-08-15T22:24:00+02:00"},
 {"id":"a2","activityTypeId":33,"amount":15202722,"playerMasterId":652,
  "user1Id":11881989,"createdAt":"2026-08-17T00:21:10+02:00"},
 {"id":"a3","activityTypeId":9,"amount":0,"playerMasterId":null,
  "user1Id":3480702,"createdAt":"2026-08-10T22:24:00+02:00"},
 {"id":"a4","activityTypeId":77,"amount":1,"playerMasterId":1,"user1Id":1,
  "createdAt":"2026-08-10T22:24:00+02:00"}]"""

_API_TEAMS_FIXTURE = """[
 {"id":"38091967","position":3,"teamPoints":17,"teamMoney":23596582,
  "manager":{"id":"11881989","managerName":"miguel_autentico"},
  "players":[{"buyoutClause":47000000,
    "buyoutClauseLockedEndTime":"2026-08-25T14:07:38+02:00",
              "playerMaster":{"id":"1337","nickname":"Fornals",
                              "name":"Pablo Fornals Malla","slug":"fornals",
                              "positionId":3,"marketValue":58300000,
                              "points":5}}]},
 {"id":"38099509","position":1,"teamPoints":24,"teamMoney":null,
  "manager":{"id":"11883172","managerName":"BurtonGM89"},
  "players":[{"buyoutClause":null,
              "playerMaster":{"id":"2621","nickname":"Simeone",
                              "name":"Giuliano Simeone","slug":"simeone-1",
                              "positionId":5,"marketValue":5552694,
                              "points":1}},
             {"playerMaster":{}}]}]"""


# The calendar, one link per case it has to get right: a played LaLiga match, an
# unplayed one, the club whose path is shortened, a duplicate link for a match
# already seen, a second-division match (the page lists both divisions), and a
# link with no round on it.
_CAL_FIXTURE = """<html><body>
<div class="calendario">
  <h3>Jornada 1</h3>
  <a href="/partidos/22421-alaves-getafe">Jornada 1 3-0</a>
  <a href="/partidos/22421-alaves-getafe">Jornada 1 3-0</a>
  <a href="/partidos/22429-sevilla-rayo">Jornada 1 2-1</a>
  <a href="/partidos/22424-elche-betis">Jornada 1 Lun 17/08 21:00h</a>
  <a href="/partidos/24078-cadiz-celta-fortuna">Jornada 1 1-1</a>
  <a href="/partidos/24082-r-sociedad-b-castellon">Vie 20:30h 01</a>
</div>
</body></html>"""


# A match page is a hundred rows of markup, so the fixture is built rather than
# typed: the size of the eleven is itself a rule under test, and three variants
# of it are needed. Everything the parser touches is here — the collapsed detail
# row that carries the only link to the player, the "Suplentes" header, and the
# DECOY second section, which repeats the same squad with full names and no
# links and used to turn a 12-man bench into 58.
def _match_html(home_xi: int = 11, away_xi: int = 11) -> str:
    def rows(prefix, xi, subs):
        out = []
        for i in range(xi + subs):
            if i == xi:
                out.append('<tr class="header"><td>Suplentes</td></tr>')
            out.append('<tr class="plegado plegable">'
                       '<td class="name">%s%d%s</td>'
                       '<td class="picas">SC</td>'
                       '</tr>' % (prefix, i, " 64'" if i == 1 else ""))
            out.append('<tr class="desglose"><td><a href='
                       '"https://www.futbolfantasy.com/jugadores/%s%d">'
                       'Ver la ficha del jugador</a></td></tr>' % (prefix, i))
        return "\n".join(out)

    def side(cls, prefix, xi, subs):
        return ('<div class="col-12 %s"><h2 class="title">Puntos</h2>'
                '<table class="tablestats"><tbody>%s</tbody></table></div>'
                '<div class="col-12 %s"><h2 class="title">En directo</h2>'
                '<table class="tablestats"><tbody>'
                '<tr class="plegado plegable"><td class="name">'
                'Nombre Completo Que No Vale</td></tr>'
                '</tbody></table></div>'
                % (cls, rows(prefix, xi, subs), cls))

    return ("<html><body><div class='row stats-table'>%s%s</div></body></html>"
            % (side("stats-local", "loc", home_xi, 3),
               side("stats-visitante", "vis", away_xi, 2)))


_MATCH_FIXTURE = _match_html()


def _selftest() -> None:
    # -- team page: the traps this parser exists to avoid -------------------
    rows = parse_team(_FIXTURE, "2026-01-01T0000Z", "team_test")
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
    assert by["Eric García"]["status"] == "suspended", by["Eric García"]

    # Injury, with the diagnosis and the expected return carried through.
    fdj = by["Frenkie de Jong"]
    assert fdj["status"] == "injured", fdj
    assert "rodilla" in fdj["note"] and "octubre" in fdj["note"], fdj

    # Unavailable is its own state and outranks nothing else here.
    assert by["Facundo Garcés"]["status"] == "unavailable"
    assert by["Facundo Garcés"]["role"] == "absent"

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
    assert _fold("Eric García") == _fold("Eric Garcia") == "eric garcia"

    # The team slug comes off the registry key, not a separate argument.
    assert all(r["team_slug"] == "test" for r in rows)
    # Every row says which site it came from, starters and absentees alike, so
    # a second probable-XI source lands in the same table without ambiguity.
    assert all(r["source"] == SOURCE for r in rows)
    assert {r["source"] for r in rows if r["role"] == "absent"} == {SOURCE}

    # -- market page -------------------------------------------------------
    m = parse_market(_MARKET_FIXTURE, "2026-01-01T0000Z")
    assert len(m) == 1, m
    assert m[0]["name"] == "Pedri" and m[0]["value"] == 21500000
    assert m[0]["team"] == "Barcelona", m[0]        # team_id -> name via select
    assert m[0]["position"] == "mediocampista"
    assert m[0]["slug"] == "pedri", m[0]            # anchor beats the photo id

    # -- points table ------------------------------------------------------
    p = parse_points(_POINTS_FIXTURE)
    assert len(p) == 1, p                           # the '-' row is dropped
    assert p[0]["player_name"] == "R. de Galarreta"
    assert p[0]["player_name_full"] == "Íñigo Ruiz de Galarreta"
    assert p[0]["points"] == "156" and p[0]["games"] == "34"
    assert p[0]["avg"] == "4.590", p[0]             # comma decimal, via ratio()
    assert season_label(_POINTS_FIXTURE) == "2025-26"
    assert season_label("<html>nothing</html>") == "unknown"

    # -- signatures --------------------------------------------------------
    # Stable: same input, same signature.
    assert sign_team(_FIXTURE) == sign_team(_FIXTURE)
    assert sign_market(_MARKET_FIXTURE) and sign_points(_POINTS_FIXTURE)

    # Blind to cosmetics. A pitch coordinate moving must NOT store the page
    # again — this is the 11 points of deduplication the whole policy rests on.
    moved = _FIXTURE.replace('class="jugadores-titulares"',
                             'class="jugadores-titulares" '
                             'data-posicionalternativa1-x="52%"')
    assert sign_team(moved) == sign_team(_FIXTURE)

    # Sensitive to anything a parser reads: a start percentage, a name, a
    # fitness icon's alt text, an href.
    for before, after in [("Pedri 70%", "Pedri 60%"),
                          ("Owen Bosch", "Owen Bosche"),
                          ('alt="Duda"', 'alt="Lesionado"'),
                          ("/jugadores/pedri", "/jugadores/pedri-gonzalez")]:
        assert sign_team(_FIXTURE.replace(before, after)) != sign_team(_FIXTURE), \
            before

    # Rotted selectors return None, never a hash. ingest keeps those pages.
    assert sign_team("<html><body><p>nothing here</p></body></html>") is None
    assert sign_market("<html><body>no players</body></html>") is None
    assert sign_points("<html><body>no table</body></html>") is None

    # -- Analítica Fantasy --------------------------------------------------
    af = parse_af_team(_AF_FIXTURE, "2026-01-01T0000Z", "af_test")
    assert [r["player_name"] for r in af] == ["Sivera", "Aitor Mañas",
                                              "Sin Foto"], af
    assert all(r["source"] == AF_SOURCE for r in af)
    assert all(r["team_slug"] == "test" for r in af)      # slug from the key
    assert all(r["role"] == "starter" for r in af)
    # The name is the aria-label, never the visible "1 - Sivera" text.
    assert af[0]["player_name"] == "Sivera" and af[0]["player_slug"] == "47353"
    # No probability and no fitness panel on this page. status must be "" —
    # "ok" would be this parser inventing a clean bill of health.
    assert all(r["start_pct"] is None and r["status"] == "" for r in af)
    # A player with no photo still reaches the table, with no id.
    assert af[2]["player_slug"] is None
    # Substitutes are not collected: only the "Titulares" list is selected.
    assert "No Deberia" not in {r["player_name"] for r in af}
    # The row shape is byte-for-byte the futbolfantasy one, because both feed
    # one CSV whose columns are taken from whichever row is written first.
    assert list(af[0]) == list(rows[0]), (list(af[0]), list(rows[0]))
    assert af[0]["note"] == "titular"                     # the shape it came in
    assert sign_af_team(_AF_FIXTURE) is not None
    assert sign_af_team("<html><body>no lineup</body></html>") is None

    # -- Analítica, consensus shape ----------------------------------------
    # 16 of 20 pages carried this on the first live sweep, when the match was
    # more than a day out. It is the better shape: their three editors give a
    # published fraction, so start_pct is theirs and not a constant of ours.
    con = parse_af_team(_AF_CONSENSO_FIXTURE, "2026-01-01T0000Z", "af_test")
    byc = {r["player_name"]: r for r in con}
    assert set(byc) == {"Unai Simón", "Yuri", "Aitor Paredes",
                        "Robert Navarro"}, sorted(byc)
    # Unanimous is 100% for any editor count, so no denominator is invented.
    assert byc["Unai Simón"]["start_pct"] == 100.0
    assert byc["Unai Simón"]["note"] == "consenso unánime"
    assert byc["Unai Simón"]["role"] == "starter"
    # A published fraction, carried through as both a number and its source.
    assert byc["Aitor Paredes"]["start_pct"] == 66.7, byc["Aitor Paredes"]
    assert byc["Aitor Paredes"]["note"] == "consenso 2/3"
    assert byc["Robert Navarro"]["start_pct"] == 33.3
    assert byc["Robert Navarro"]["role"] == "doubt"        # editors disagree
    # THE BUG THIS FIXTURE EXISTS FOR: the captain candidate must not appear.
    # He is "Nico Williams1/3" in a third list, and an ancestor-walking heading
    # lookup filed him under "Unánimes" and stored him at 100%.
    assert "Nico Williams" not in byc, con
    assert not any(c.isdigit() for r in con for c in r["player_name"]), con
    # Same fitness rule as the other shape: silence is not health.
    assert all(r["status"] == "" for r in con)
    assert list(con[0]) == list(rows[0])                  # one CSV, one shape
    # A page with neither shape yields nothing and is reported as rot, rather
    # than a third shape being guessed at.
    assert parse_af_team("<html><body>new design</body></html>", "t") == []
    assert sign_af_team(_AF_CONSENSO_FIXTURE) is not None

    # -- Analítica, fixtures -----------------------------------------------
    fx = parse_af_fixtures(_AF_HUB_FIXTURE, "2026-01-01T0000Z")
    assert len(fx) == 1, fx        # duplicate id and the time-less link dropped
    assert fx[0]["match_id"] == "100011934"
    assert fx[0]["home"] == "Sevilla" and fx[0]["away"] == "Rayo Vallecano"
    # Stored exactly as published, offset included — see the docstring.
    assert fx[0]["kickoff"] == "2026-08-15T19:30:00+00:00", fx[0]
    assert fx[0]["source"] == AF_SOURCE
    assert sign_af_fixtures(_AF_HUB_FIXTURE) is not None
    assert sign_af_fixtures("<html><body>no matches</body></html>") is None

    # -- Club Elo, read out of a chart rather than off an API --------------
    el = parse_elo(_ELO_FIXTURE, "2026-01-01T0000Z", "elo")
    assert [r["club"] for r in el] == ["Barcelona", "Real Madrid",
                                       "Elche"], el
    assert el[0]["elo"] == "2043.1" and el[0]["source"] == ELO_SOURCE, el[0]
    # The chart is worldwide and multi-division; only the Spanish top flight
    # is a fixture. Elche above Bayern is not a difficulty.
    assert not any(r["club"] in ("Bayern", "Zaragoza") for r in el), el
    # A rating arrives at the precision the page carries it, because the band
    # it feeds is still unfitted and rounding is a decision nobody has made.
    assert parse_elo(_ELO_FIXTURE.replace("2043.1", "1980.0455939177232"),
                     "t")[0]["elo"] == "1980.0455939177232"
    # Records are found BY KEY, so a reordered or extended chart still reads
    # and a RENAMED key yields nothing rather than a rating from the wrong
    # field — the rot signal ingest already knows how to report.
    assert parse_elo(_ELO_FIXTURE.replace('"Elo":', '"Rating":'), "t") == []
    assert parse_elo(_ELO_FIXTURE.replace('"FedURL":', '"Fed":'), "t") == []
    # A rating that is not a number is dropped, not stored as one.
    assert [r["club"] for r in
            parse_elo(_ELO_FIXTURE.replace("2043.1", '"n/a"'), "t")] \
        == ["Real Madrid", "Elche"]
    # Neither a page without the chart nor a page that is not this page is a
    # rating, and neither may raise.
    assert parse_elo("", "t") == []
    assert parse_elo("<html><body>no chart here</body></html>", "t") == []
    assert parse_elo("<script>var vegaJson = {not json;</script>", "t") == []
    assert sign_elo(_ELO_FIXTURE) is not None
    # Same clubs, same ratings, a different colour in the chart: one archive,
    # not two. The signature is the surface this repo reads, as everywhere
    # else — and the /ESP page carries fixtures, odds and kickoff times that
    # move all day for clubs no fantasy squad can hold.
    assert sign_elo(_ELO_FIXTURE) == sign_elo(
        _ELO_FIXTURE.replace("#A4234B", "#123456"))
    assert sign_elo(_ELO_FIXTURE.replace("2043.1", "2050.0")) \
        != sign_elo(_ELO_FIXTURE)
    assert sign_elo("nothing like the page") is None
    # No URL in the registry carries the day it is asking about any more; the
    # substitution is left in ingest because it costs nothing and the next
    # dated source will want it.
    assert ELO_URL.format(date="2026-08-16") == ELO_URL
    assert MARKET_URL.format(date="2026-08-16") == MARKET_URL

    # -- the league's own API ---------------------------------------------
    lg = parse_api_leagues(_API_LEAGUES_FIXTURE, "2026-01-01T0000Z")
    assert len(lg) == 1 and lg[0]["league_id"] == "017998544", lg
    assert lg[0]["team_id"] == "38091967" and lg[0]["money"] == "23596582", lg
    assert lg[0]["source"] == LFG_SOURCE
    # HTML where JSON was promised is rot, and rot yields nothing rather than
    # an exception that loses the whole sweep.
    assert parse_api_leagues("<html>maintenance</html>", "t") == []
    assert sign_api_leagues("<html>") is None

    mk = parse_api_market(_API_MARKET_FIXTURE, "t")
    assert len(mk) == 2, mk                 # the id-less third row is dropped
    assert mk[0]["bids"] == "0" and mk[0]["seller"] == "marketPlayerLeague"
    # A manager-listed player has null bids. Empty means NOT STATED; storing
    # it as "0" would claim nobody is bidding, which is a different fact.
    assert mk[1]["bids"] == "" and mk[1]["seller"] == "marketPlayerTeam", mk[1]
    # BOTH NAMES ARE KEPT. The app publishes a nickname and a full name, and
    # neither one joins on its own: measured across the 76 owned players on
    # 2026-08-19, 12 join only on the nickname ("Raphinha", "Pepelu") and 3
    # only on the full name — "Aimar" is Aimar Oroz, "Brahim" is Brahim Díaz,
    # "Llorente" is one of two Llorentes in the market. Keeping one and
    # discarding the other is what made a three-tier fallback necessary
    # downstream. `player_name` stays the nickname because it is the better
    # single guess; the full name rides beside it.
    assert mk[0]["player_name"] == "Simeone", mk[0]
    assert mk[0]["player_name_full"] == "Giuliano Simeone", mk[0]
    # Absent is empty, never the nickname repeated: a caller trying both must
    # be able to tell that there was only ever one.
    assert mk[1]["player_name_full"] == "", mk[1]
    # The clock in expirationDate ticks every sweep and must not sign.
    assert sign_api_market(_API_MARKET_FIXTURE) == sign_api_market(
        _API_MARKET_FIXTURE.replace("2026-08-18T22", "2026-08-20T22"))
    # A price move or a new bid must.
    assert sign_api_market(_API_MARKET_FIXTURE) != sign_api_market(
        _API_MARKET_FIXTURE.replace("5552694,\"numberOfBids\":0",
                                    "5552694,\"numberOfBids\":3"))

    ac = parse_api_activity(_API_ACTIVITY_FIXTURE, "t")
    # Three known verbs kept, the unknown 77 dropped rather than guessed at.
    assert [r["kind"] for r in ac] == ["buy", "sell", "joined"], ac
    assert ac[0]["amount"] == "58220110" and ac[0]["user_id"] == "11881989"
    assert sign_api_activity(_API_ACTIVITY_FIXTURE) is not None
    # Reordering the feed is not a change; a new row is.
    import json as _json
    _rev = _json.dumps(list(reversed(_json.loads(_API_ACTIVITY_FIXTURE))))
    assert sign_api_activity(_rev) == sign_api_activity(_API_ACTIVITY_FIXTURE)

    tm = parse_api_teams(_API_TEAMS_FIXTURE, "t")
    assert len(tm) == 2, tm                 # the empty playerMaster is dropped
    assert tm[0]["manager"] == "miguel_autentico"
    assert tm[0]["team_money"] == "23596582" and tm[0]["buyout"] == "47000000"
    # The lock comes through, because a clause you cannot pay is not a price.
    assert tm[0]["buyout_until"] == "2026-08-25T14:07:38+02:00", tm[0]
    assert tm[1]["buyout_until"] == ""
    # The limit worth encoding: a rival states no balance, and that is empty,
    # not zero. A zero here would read as "they are broke".
    assert tm[1]["team_money"] == "" and tm[1]["manager"] == "BurtonGM89"
    assert tm[1]["buyout"] == ""
    # The same two names as the market rows, for the same reason — this is
    # the feed the ownership join reads, so it is the one the full name
    # actually rescues players in.
    assert tm[0]["player_name"] == "Fornals", tm[0]
    assert tm[0]["player_name_full"] == "Pablo Fornals Malla", tm[0]
    assert tm[1]["player_name_full"] == "Giuliano Simeone", tm[1]

    # Discovery: the league id comes off the account, never a config file.
    disc = league_sources(_API_LEAGUES_FIXTURE)
    assert [s.key for s in disc] == ["api_market", "api_teams",
                                     "api_activity_0", "api_activity_1"], disc
    assert all(s.auth for s in disc), "every API entry needs the bearer"
    # The one source nothing depends on waits the least. A global timeout let
    # it hold 94% of the sweep for a page that never arrived.
    elo = next(s for s in sources() if s.key == "elo")
    assert elo.timeout == 8.0, elo.timeout
    # THE SQUAD IS FETCHED EVERY RUN. On a daily cadence the report kept
    # recommending the sale of a player already sold, and the rerun button
    # could not see a deal at all — which is the only thing it is for.
    assert all(s.cadence == "every_run" for s in disc if s.key == "api_teams")
    assert "017998544" in disc[0].url and "{base}" in disc[0].url
    # Nothing to discover from a rotted page means no work queued, not a crash.
    assert league_sources("<html>") == []
    # A stored API page must resolve by key alone, or `parse` silently drops
    # it — which is exactly what happened the first time this was wired: the
    # pages were fetched and stored, and no tidy table appeared.
    for k in ("api_market", "api_teams", "api_activity_0", "api_activity_1"):
        assert source_for(k) is not None, k
        assert source_for(k).table == ("api_activity"
                                       if "activity" in k else k), k
    assert source_for("api_market").parse is parse_api_market
    assert api_source("market") is None      # the scraped one is not the API's

    # -- naming the players the feed only gives an id for -------------------
    # The activity feed says "user 11881989 bought playerMasterId 1191". Half
    # those ids belong to players who have since been sold and are neither in
    # a squad nor on the market, so nothing else in the store can name them —
    # 24 of 50 on the day this was written. A named player is the whole point
    # of a ledger row, so each id is fetched once, ever, exactly like a match
    # page: same "once" cadence, same discovery-from-a-feed, same dedup.
    pl = parse_api_player(_API_PLAYER_FIXTURE, "t", "api_player_1191")
    assert len(pl) == 1, pl
    assert pl[0]["player_id"] == "1191", pl
    # The NICKNAME is the market's spelling ("Hugo Duro"), not the full legal
    # name ("Hugo Duro Perales"), and the market is what everything joins on.
    assert pl[0]["player_name"] == "Hugo Duro", pl
    assert pl[0]["full_name"] == "Hugo Duro Perales", pl
    assert pl[0]["market_value"] == "8534068", pl
    assert parse_api_player("<html>", "t", "api_player_1") == []

    # The id comes from the KEY, not the body, so a stored page still names
    # itself if the payload ever stops carrying an id.
    assert parse_api_player('{"nickname":"X"}', "t",
                            "api_player_777")[0]["player_id"] == "777"

    # One entry per id in the feed; `due()` drops the ones already stored,
    # which is what makes 50 requests today and none tomorrow.
    ps = player_sources(_API_ACTIVITY_FIXTURE)
    assert [s.key for s in ps] == ["api_player_1337", "api_player_652"], ps
    assert all(s.cadence == "once" and s.auth for s in ps), ps
    assert all(s.table == "api_players" for s in ps), ps
    # A "joined" row carries no player and must not become a lookup.
    assert not any("None" in s.key or s.key == "api_player_" for s in ps)
    # Resolvable by key alone, long after the deal is forgotten.
    assert source_for("api_player_1191").parse is parse_api_player
    assert source_for("api_player_1191").table == "api_players"
    # And the public sources must NOT be marked auth — a bearer sent to
    # futbolfantasy is a credential leaked to a third party.
    assert not any(s.auth for s in sources() if not s.key.startswith("api_"))

    # -- the calendar: which matches happened ------------------------------
    cal = parse_calendar(_CAL_FIXTURE, "2026-01-01T0000Z")
    byp = {r["path"]: r for r in cal}
    assert len(cal) == 3, cal          # duplicate, second division, no round
    assert byp["22421-alaves-getafe"]["score"] == "3-0"
    assert byp["22421-alaves-getafe"]["jornada"] == 1
    assert byp["22421-alaves-getafe"]["match_id"] == "22421"
    # An unplayed match is still a row — it is just not a page to ask for.
    assert byp["22424-elche-betis"]["score"] == ""
    # The one club the match paths shorten. Without the alias his 38 matches
    # split into nothing and the sweep never sees a tenth of the season.
    assert byp["22429-sevilla-rayo"]["away"] == "rayo-vallecano", byp
    assert _match_sides("real-madrid-real-sociedad") \
        == ("real-madrid", "real-sociedad")           # hyphens in both names
    assert _match_sides("cadiz-celta-fortuna") is None      # second division
    # Only played matches become work, and each one resolves back to its URL.
    ps = played_sources(_CAL_FIXTURE)
    assert [s.key for s in ps] == ["match_22421-alaves-getafe",
                                   "match_22429-sevilla-rayo"], ps
    assert ps[0].url.endswith("/partidos/22421-alaves-getafe")
    assert ps[0].cadence == "once" and ps[0].table == "starters"
    assert sign_calendar(_CAL_FIXTURE) is not None
    assert sign_calendar("<html><body>no matches</body></html>") is None

    # -- who actually started ----------------------------------------------
    xi = parse_starters(_MATCH_FIXTURE, "2026-01-01T0000Z",
                        "match_22421-alaves-getafe")
    got = {}
    for r in xi:
        got[(r["team_slug"], r["role"])] = got.get((r["team_slug"], r["role"]),
                                                   0) + 1
    assert got == {("alaves", "starter"): 11, ("alaves", "sub"): 3,
                   ("getafe", "starter"): 11, ("getafe", "sub"): 2}, got
    # Home is .stats-local and away is .stats-visitante, in that order.
    assert xi[0]["team_slug"] == "alaves" and xi[0]["player_name"] == "loc0"
    # THE JOIN KEY. It arrives one row late, on the collapsed detail row, and
    # it is the same /jugadores/ slug the probable-XI table stores — which is
    # why grading needs no name matching at all.
    assert all(r["player_slug"] for r in xi), xi
    assert xi[0]["player_slug"] == "loc0", xi[0]
    assert xi[0]["match_id"] == "22421" and xi[0]["source"] == SOURCE
    # Same two words the forecast uses, so the two tables compare directly.
    assert {r["role"] for r in xi} == {"starter", "sub"}
    assert {r["role"] for r in xi} < {r["role"] for r in rows}
    # No jornada column: it lives on the matches row, keyed by match_id.
    assert "jornada" not in xi[0]
    # THE DECOY SECTION is not read. It repeats the squad with full names and
    # no links, and reading it made the bench five times too long.
    assert not any("Completo" in r["player_name"] for r in xi), xi
    # THE MINUTE a player came off is part of the same cell as his name. Left
    # on, it makes "Aitor Mañas 56'" — a name that matches nothing, which is
    # how the other source's name-only claims came to look wrong.
    assert xi[1]["player_name"] == "loc1", xi[1]
    assert not any("'" in r["player_name"] for r in xi), xi

    # A side that is not eleven players is not an eleven: those rows are
    # dropped, because a nine-man XI would bias every hit rate downwards. The
    # other side still counts.
    short = parse_starters(_match_html(home_xi=9), "t",
                           "match_22421-alaves-getafe")
    assert {r["team_slug"] for r in short} == {"getafe"}, short
    # An unplayed match page has no such table at all: no rows, and no
    # signature either, so ingest reports it rather than storing it silently.
    assert parse_starters("<html><body>sin datos</body></html>", "t",
                          "match_22421-alaves-getafe") == []
    assert sign_starters("<html><body>sin datos</body></html>") is None
    assert sign_starters(_MATCH_FIXTURE) is not None
    # A key that is not a match key parses to nothing rather than half a row.
    assert parse_starters(_MATCH_FIXTURE, "t", "market") == []
    assert match_source("market") is None
    assert match_source("match_22421-alaves-getafe").key \
        == "match_22421-alaves-getafe"
    # A stored match page resolves long after the calendar has moved on.
    assert source_for("match_22421-alaves-getafe").parse is parse_starters

    # -- the registry ------------------------------------------------------
    reg = sources()
    # 6 standalone pages: market, points, af_fixtures, elo, the calendar, and
    # the API's discovery page. The three API entries it reveals are not here
    # — they are queued at run time, like the match pages.
    assert len(reg) == 6 + len(TEAMS) + len(AF_TEAMS) == 46, len(reg)
    assert set(AF_TEAMS) == set(TEAMS), set(AF_TEAMS) ^ set(TEAMS)
    # Both team sweeps are daily; market and points still run every sweep.
    assert {s.cadence for s in reg if s.key.startswith(("team_", "af_"))} \
        == {"daily"}
    assert {s.cadence for s in reg if s.key in ("market", "points")} \
        == {"every_run"}
    assert {s.key for s in reg} >= {"market", "points", "team_barcelona",
                                    "af_fixtures"}
    assert len({s.key for s in reg}) == len(reg)          # keys are unique
    assert {s.table for s in reg} == {"market", "points", "lineups",
                                      "fixtures", "elo", "matches",
                                      "api_leagues"}
    assert source_for("team_celta").parse is parse_team
    assert source_for("gone") is None                     # retired page name

    # Every entry can actually parse and sign, with the key it declares —
    # this is what stops a new source being added half-wired.
    assert source_for("af_celta").parse is parse_af_team
    samples = {"market": _MARKET_FIXTURE, "points": _POINTS_FIXTURE,
               "af_fixtures": _AF_HUB_FIXTURE, "elo": _ELO_FIXTURE,
               CAL_KEY: _CAL_FIXTURE, API_LEAGUES_KEY: _API_LEAGUES_FIXTURE}
    # Half the AF teams get each shape, so neither branch can rot unnoticed.
    for i, k in enumerate(sorted(AF_TEAMS)):
        samples[f"af_{k}"] = _AF_FIXTURE if i % 2 else _AF_CONSENSO_FIXTURE
    for s in reg:
        html = samples.get(s.key, _FIXTURE)
        assert s.sign(html) is not None, s.key
        assert isinstance(s.parse(html, "2026-01-01T0000Z", s.key), list), s.key

    print("sources.py selftest OK (178 cases)")


if __name__ == "__main__":
    _selftest()
