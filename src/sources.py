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
import re
from typing import Callable, NamedTuple

from lxml import html as lh

__all__ = ["BASE", "SOURCE", "MARKET_URL", "POINTS_URL", "TEAM_URL", "TEAMS",
           "AF_BASE", "AF_SOURCE", "AF_TEAM_URL", "AF_TEAMS", "AF_HUB_URL",
           "Source", "sources", "source_for", "SEVERITY",
           "parse_market", "parse_team", "parse_points", "parse_fitness",
           "parse_af_team", "parse_af_fixtures", "season_label",
           "sign_market", "sign_team", "sign_points", "sign_af_team",
           "sign_af_fixtures"]

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


def _suspension_sections(doc):
    """section.mod.sancionados, minus the transfer-listing box that shares
    the class. Shared with sign_team so the two cannot disagree."""
    return [s for s in doc.cssselect("section.mod.sancionados")
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

    for el in doc.cssselect(
            ".lesionados_wrapper section.mod.lesionados > .elemento"):
        icon = el.cssselect(".icono img")
        alt = (icon[0].get("alt") or "").strip().lower() if icon else ""
        status = FITNESS_ALT.get(alt)
        if not status:
            continue
        name, slug = _flagged_name(el)
        put(name, slug, status, _note(el))

    for sec in _suspension_sections(doc):
        for el in sec.cssselect(".elemento"):
            name, slug = _flagged_name(el)
            put(name, slug, "suspended", _note(el))

    for el in doc.cssselect("section.mod.nodisponibles .elemento"):
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

    for el in doc.cssselect(XI_SELECTORS[0]):
        add(el, "starter")
    for el in doc.cssselect(XI_SELECTORS[1]):
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
        out += [a.get("href") or "" for a in el.cssselect("a[href]")]
        out += [i.get("alt") or "" for i in el.cssselect("img[alt]")]
    return out


def sign_market(html: str) -> str | None:
    return _digest(MARKET_SURFACE_RE.findall(html))


def sign_team(html: str) -> str | None:
    doc = lh.fromstring(html)
    els = []
    for sel in XI_SELECTORS + FITNESS_SELECTORS:
        els += doc.cssselect(sel)
    els += _suspension_sections(doc)
    return _digest(_surface(els))


# ---------------------------------------------------------------------------
# Analítica Fantasy — the second probable-XI source
# ---------------------------------------------------------------------------
#
# THEIR TEAM PAGE HAS TWO SHAPES, and which one you get depends on how close
# the next match is. The first live sweep found only 4 of 20 pages carrying the
# shape this parser was first written against, which is how the second shape
# was found — before it could be mistaken for rot.
#
#   1. Match imminent: <ul aria-label="Titulares <Team>"> holds exactly the
#      eleven, one <li aria-label="Ver resumen de <Name>"> each. This is their
#      final call, and it is binary: no percentage anywhere on the page. So
#      `start_pct` stays empty and `note` says "titular". Do NOT write 100
#      here — a confident editorial call is not a stated probability, and the
#      report must be able to tell the difference.
#
#   2. Match further out: an aria-label="Consenso de alineaciones" block
#      instead, and this one is richer than a binary XI. It splits their three
#      editors into "Unánimes" (in every editor's eleven) and "Más divididos"
#      (text like "Aitor Paredes2/3 titular"). That fraction is a start
#      probability THEY published, editor-count based — not a constant this
#      repo invented — so it lands in `start_pct` as 100·n/d with the raw
#      fraction preserved in `note`.
#
# The two shapes are mutually exclusive on every page seen so far, so the
# parser tries Titulares and falls back to Consenso. A page with neither
# returns no rows and ingest reports it as rot, which is the correct outcome:
# the third shape does not exist yet, and when it does we should hear about it.
#
# NO FITNESS, EITHER WAY. Neither shape carries an injury panel, so `status`
# is "" meaning *not stated*, never "ok" — silence must not be stored as a
# clean bill of health. Their position codes are deliberately not stored: the
# app's own positions are the ones the scorer must use.
#
# The per-match pages (/partido/<id>) carry substitutes too, but their URLs
# change every jornada. We now read the ids off the hub page (see
# parse_af_fixtures) — for kickoff times, not yet for lineups.

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
        img = li.cssselect("img[src]")
        return img[0].get("src") if img else ""

    for li in doc.cssselect(AF_XI_SELECTOR):
        label = li.get("aria-label") or ""
        name = (label[len(AF_NAME_PREFIX):].strip()
                if label.startswith(AF_NAME_PREFIX) else "")
        # start_pct stays None: their final call is binary, not a percentage.
        add(name, photo(li), "starter", None, "titular")
    if rows:
        return rows

    for block in doc.cssselect(AF_CONSENSO_SELECTOR):
        for ul in block.cssselect("ul"):
            section = _af_section(ul)
            if section is None:
                continue            # captain candidates and anything new
            for li in ul.cssselect("li"):
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
    return _digest(_surface(doc.cssselect('ul[aria-label^="Titulares"]')
                            + doc.cssselect(AF_CONSENSO_SELECTOR)))


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
    for a in doc.cssselect('a[href*="/partido/"]'):
        m = AF_MATCH_RE.search(a.get("href") or "")
        times = a.cssselect("time[datetime]")
        teams = [i.get("alt") for i in a.cssselect("img[alt]") if i.get("alt")]
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
# Club Elo — how strong a team actually is, rather than how expensive
# ---------------------------------------------------------------------------
#
# The fixture term ranked teams by summed squad value, because that was the
# only team-level number in the repo. It is a poor proxy twice over: a promoted
# side that spends is not thereby good, and one 100M signing moves a whole
# squad's total. Club Elo is a rating fitted on results, published free, with
# no key, as one plain CSV a day.
#
# THE ONLY PAGE HERE THAT IS NOT HTML. `parse` and `sign` receive text either
# way, so the registry needs no new concept — but nothing lxml-shaped may touch
# this, which is why sign_elo hashes rows rather than calling _surface.
#
# The file is worldwide and the URL carries a date, so the signature is the
# input surface again: only the Spanish top-flight rows this repo can read.
# Hashing the whole file would store a fresh archive every day for four
# thousand clubs we never rank.
#
# ROBOTS: clubelo.com serves no robots.txt (the path 302s away, with no
# Disallow anywhere), and the API exists to be read by programs — one request
# a day, on the daily cadence, is the whole footprint.
ELO_SOURCE = "clubelo"
ELO_URL = "http://api.clubelo.com/{date}"      # https is refused by the host
ELO_COUNTRY = "ESP"
ELO_LEVEL = "1"
# The published header. Checked rather than assumed: a column order that moved
# would otherwise be read as a rating.
ELO_COLS = ("club", "country", "level", "elo")


def parse_elo(text: str, observed_at: str, key: str = "elo") -> list[dict]:
    """One row per Spanish top-flight club: its rating on the day we asked.

    Columns are found BY NAME, so a reordered file still reads and a renamed
    one yields nothing — which is the rot signal, not a rating read out of the
    wrong column.
    """
    lines = [ln for ln in (text or "").splitlines() if ln.strip()]
    if not lines:
        return []
    head = [c.strip().lower() for c in lines[0].split(",")]
    if not all(c in head for c in ELO_COLS):
        return []
    at = {c: head.index(c) for c in ELO_COLS}
    rows = []
    for ln in lines[1:]:
        cells = [c.strip() for c in ln.split(",")]
        if len(cells) < len(head):
            continue
        if (cells[at["country"]] != ELO_COUNTRY
                or cells[at["level"]] != ELO_LEVEL):
            continue
        rows.append({"observed_at": observed_at, "source": ELO_SOURCE,
                     "club": cells[at["club"]], "elo": cells[at["elo"]]})
    return rows


def sign_elo(text: str) -> str | None:
    return _digest(["%s=%s" % (r["club"], r["elo"])
                    for r in parse_elo(text, "")])


# ---------------------------------------------------------------------------
# the registry
# ---------------------------------------------------------------------------

class Source(NamedTuple):
    key: str                      # page name inside a snapshot
    table: str                    # tidy table its rows feed
    url: str
    parse: Callable               # (html, observed_at, key) -> rows
    sign: Callable                # (html) -> signature or None
    cadence: str = "every_run"    # "every_run" | "daily"
    enabled: bool = True


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
    out += [Source("elo", "elo", ELO_URL, parse_elo, sign_elo,
                   cadence="daily")]
    return [s for s in out if s.enabled or not enabled_only]


def source_for(key: str) -> Source | None:
    """The registry entry for a stored page name, or None if we no longer
    collect it. Old snapshots outlive registry entries, so parse has to cope
    with a page nothing claims."""
    for s in sources(enabled_only=False):
        if s.key == key:
            return s
    return None


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

# Club Elo's published shape, worldwide and multi-division — which is the whole
# reason the parser filters on country and level rather than trusting the file.
_ELO_FIXTURE = """Rank,Club,Country,Level,Elo,From,To
1,Barcelona,ESP,1,2043.1,2026-08-10,2026-08-16
3,Bayern,GER,1,2010.4,2026-08-10,2026-08-16
4,Real Madrid,ESP,1,1988.7,2026-08-10,2026-08-16
78,Elche,ESP,1,1602.5,2026-08-10,2026-08-16
,Zaragoza,ESP,2,1521.0,2026-08-10,2026-08-16
"""


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

    # -- Club Elo, the one source that is not HTML -------------------------
    el = parse_elo(_ELO_FIXTURE, "2026-01-01T0000Z", "elo")
    assert [r["club"] for r in el] == ["Barcelona", "Real Madrid",
                                       "Elche"], el
    assert el[0]["elo"] == "2043.1" and el[0]["source"] == ELO_SOURCE, el[0]
    # The file is worldwide and multi-division; only the Spanish top flight is
    # a fixture. Elche above Bayern is not a difficulty.
    assert not any(r["club"] in ("Bayern", "Zaragoza") for r in el), el
    # Columns are found by name, so a reordered header still reads.
    swapped = "\n".join([
        "Club,Elo,Rank,Country,Level,From,To",
        "Barcelona,2043.1,1,ESP,1,2026-08-10,2026-08-16"])
    assert parse_elo(swapped, "t")[0]["elo"] == "2043.1", parse_elo(swapped, "t")
    # A RENAMED column yields nothing rather than a rating from the wrong
    # place, and nothing is the rot signal ingest already knows how to report.
    assert parse_elo("Club,Country,Level,Rating\nBarcelona,ESP,1,2043", "t") \
        == []
    assert parse_elo("", "t") == [] and parse_elo("Club\n", "t") == []
    assert sign_elo(_ELO_FIXTURE) is not None
    # Same clubs, same ratings, a different date in the file: one archive, not
    # two. The signature is the surface this repo reads, as everywhere else.
    assert sign_elo(_ELO_FIXTURE) == sign_elo(
        _ELO_FIXTURE.replace("2026-08-16", "2026-08-17"))
    assert sign_elo(_ELO_FIXTURE.replace("2043.1", "2050.0")) \
        != sign_elo(_ELO_FIXTURE)
    assert sign_elo("nothing like a csv") is None
    # The one URL in the registry that carries the day it is asking about.
    # ingest fills it; every other URL has no placeholder and is untouched.
    assert ELO_URL.format(date="2026-08-16").endswith("/2026-08-16")
    assert MARKET_URL.format(date="2026-08-16") == MARKET_URL

    # -- the registry ------------------------------------------------------
    reg = sources()
    assert len(reg) == 4 + len(TEAMS) + len(AF_TEAMS) == 44, len(reg)
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
                                      "fixtures", "elo"}
    assert source_for("team_celta").parse is parse_team
    assert source_for("gone") is None                     # retired page name

    # Every entry can actually parse and sign, with the key it declares —
    # this is what stops a new source being added half-wired.
    assert source_for("af_celta").parse is parse_af_team
    samples = {"market": _MARKET_FIXTURE, "points": _POINTS_FIXTURE,
               "af_fixtures": _AF_HUB_FIXTURE, "elo": _ELO_FIXTURE}
    # Half the AF teams get each shape, so neither branch can rot unnoticed.
    for i, k in enumerate(sorted(AF_TEAMS)):
        samples[f"af_{k}"] = _AF_FIXTURE if i % 2 else _AF_CONSENSO_FIXTURE
    for s in reg:
        html = samples.get(s.key, _FIXTURE)
        assert s.sign(html) is not None, s.key
        assert isinstance(s.parse(html, "2026-01-01T0000Z", s.key), list), s.key

    print("sources.py selftest OK (92 cases)")


if __name__ == "__main__":
    _selftest()
