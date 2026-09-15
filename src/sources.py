"""
sources.py — the fetch registry: what we collect and how to parse it.
No network, no filesystem; parsing only (fetching/files live in ingest.py).

    Source(key, table, url, parse, sign, cadence)

    key      page filename inside a snapshot: "market", "team_celta"
    table    tidy table its rows land in
    url      where to get it
    parse    (html, observed_at, key) -> list[dict]
    sign     (html) -> str | None   content signature; see below
    cadence  "every_run" or "daily"

Adding a source is one entry, one parse function, self-test cases — not a
new script or a new report wiring.

SIGNATURES: ingest stores a page only when its signature changed since last
seen. `sign` hashes the PARSER'S INPUT SURFACE (every string a selector can
reach: text, `href`, `img alt`) rather than the whole page (misses ~32% of
real changes — noise like tickers and cache-busted URLs dominates) or only
today's extracted fields (drops the ability to backfill a field added
later, since an unextracted change would look like no change at all).

`sign` returns None when its selectors match nothing at all (selector rot).
That page is stored unconditionally and ingest warns — a rotted page looks
identical to the last rotted page, so dedup must not apply.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from datetime import datetime, timezone
from functools import lru_cache, partial
from typing import Callable, NamedTuple

from ffcore.text import match_one, norm  # pure stdlib itself; not ffcore.fixture/tidy

from lxml import html as lh

__all__ = ["BASE", "SOURCE", "MARKET_URL", "POINTS_URL", "TEAM_URL", "TEAMS",
           "AF_BASE", "AF_SOURCE", "AF_TEAM_URL", "AF_TEAMS", "AF_HUB_URL",
           "Source", "sources", "source_for", "SEVERITY",
           "parse_market", "parse_team", "parse_points", "parse_fitness",
           "parse_af_team", "parse_af_fixtures", "season_label",
           "sign_market", "sign_team", "sign_points", "sign_af_team",
           "sign_af_fixtures",
           "FD_BASE", "FD_URL", "FD_SOURCE", "FD_ALIASES", "FD_SEASONS_BACK",
           "fd_season_code", "fd_sources", "parse_fd_results",
           "sign_fd_results",
           "CAL_KEY", "FF_CAL_URL", "MATCH_URL", "MATCH_KEY_RE",
           "parse_calendar", "parse_starters", "sign_calendar",
           "sign_starters", "match_source", "played_sources",
           "LFG_SOURCE", "API_LEAGUES_KEY", "API_LEAGUES_URL",
           "API_MARKET_URL", "API_ACTIVITY_URL", "API_TEAMS_URL",
           "ACT_KIND", "ACT_JOINED", "ACT_BUY", "ACT_SELL", "ACT_BONUS",
           "ACT_BONUS_ZERO", "STORE_ONCE",
           "ROW_TABLE", "parser_sig", "parser_deps", "top_level",
           "parse_api_leagues", "parse_api_market", "parse_api_activity",
           "parse_api_teams", "sign_api_leagues", "sign_api_market",
           "sign_api_activity", "sign_api_teams", "league_sources",
           "API_PLAYER_URL", "parse_api_player", "sign_api_player",
           "player_source", "player_sources",
           "API_OFFER_URL", "API_OFFER_KEY_RE", "parse_api_offer",
           "sign_api_offer", "offer_source", "offer_sources"]

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
            # The site's own player id. See the self-test: present and unique
            # on every row, and it tells apart the names that collide.
            "ff_id": _attr(chunk, "id") or "",
            "name": name,
            "position": (_attr(chunk, "posicion") or "").lower(),
            "team_id": team_id,
            "team": teams.get(team_id or "", ""),
            "value": int(value),
            "delta_1d": _num(_attr(chunk, "diferencia1")),
            "delta_pct_1d": _num(_attr(chunk, "diferencia-pct1")),
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


# Classes lie here (the injury CSS class is on every tile, not just injured
# ones) — real signal is the fitness panel's icon alt text.
# Why: docs/notes/sources.md#fitness-classes-lie-in-the-markup

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
        key = norm(name)
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
        fit = fitness.get(norm(name))
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
        if fkey in {norm(r["player_name"]) for r in rows}:
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


_POINTS_ID_RE = re.compile(r"openPlayerPointsStats\(\s*(\d+)")


def _points_id(tr) -> str:
    """The site's player id, from the row's own click handler."""
    m = _POINTS_ID_RE.search(tr.get("onclick") or "")
    return m.group(1) if m else ""


def parse_points(html: str, observed_at: str = "", key: str = "points") -> list[dict]:
    """Total points, matches played and average per player.

    No position column here — positions come from market.csv at report
    time. The player cell holds a full name and a short display form; both
    are kept.

    The id is in the row's onclick handler, not a data-* attribute:
    onclick="openPlayerPointsStats(63, 'Koke Resurrección', ...)" — the
    same id space as the market page's data-id, so points history joins on
    a real identifier rather than on name alone.
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
                "ff_id": _points_id(tr),
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


# What a parser is made of, so ingest's cache dies only when ITS parser
# changes — not on every edit anywhere in this file.
# Why: docs/notes/sources.md#parser_sig-cache-key


_DEFS: dict[int, dict] = {}


def _defs(source: str) -> dict[str, tuple[str, set]]:
    """{top-level name: (its source text, the top-level names it references)}.

    Built once and memoised — every parser in the registry wants this map,
    and rebuilding it per parser is real, avoidable cost over a file this
    size.
    """
    import ast

    hit = _DEFS.get(hash(source))
    if hit is not None:
        return hit
    tree = ast.parse(source)
    # Line-sliced once rather than via ast.get_source_segment (which
    # re-splits the whole file per node) — real cost at this file's size.
    lines = source.splitlines()
    bodies: dict[str, list] = {}
    text: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            names = [node.name]
        elif isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target,
                                                            ast.Name):
            names = [node.target.id]
        else:
            continue
        seg = "\n".join(lines[node.lineno - 1:(node.end_lineno or node.lineno)])
        for n in names:
            text[n] = text.get(n, "") + seg
            bodies.setdefault(n, []).append(node)
    out = {n: (text[n],
               {d.id for node in bodies[n] for d in ast.walk(node)
                if isinstance(d, ast.Name)})
           for n in bodies}
    # Only references to things defined HERE are dependencies; a builtin, an
    # import or a local simply is not in the map.
    out = {n: (t, refs & set(out)) for n, (t, refs) in out.items()}
    _DEFS[hash(source)] = out
    return out


def top_level(source: str) -> dict[str, str]:
    """{top-level name: the source text that defines it}."""
    return {n: t for n, (t, _refs) in _defs(source).items()}


def parser_deps(source: str, name: str) -> set[str]:
    """`name` and every top-level name it reaches, transitively.

    Attribute access counts as a plain name — `SEVERITY.get` reaches SEVERITY
    — because ast.Name covers the base of the attribute chain. Anything not
    defined at the top level of this module drops out.
    """
    defs = _defs(source)
    if name not in defs:
        return set()
    seen, stack = set(), [name]
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend(defs[cur][1] - seen)
    return seen


def parser_sig(name: str, source: str | None = None) -> str:
    """A digest of everything in this file that `name` can depend on."""
    if source is None:
        source = _MY_SOURCE
    defs, deps = top_level(source), parser_deps(source, name)
    if not deps:
        return hashlib.blake2b(source.encode("utf-8", "replace"),
                               digest_size=8).hexdigest()
    body = "\n".join("%s=%s" % (n, defs[n]) for n in sorted(deps))
    return hashlib.blake2b(body.encode("utf-8", "replace"),
                           digest_size=8).hexdigest()


try:
    _MY_SOURCE = __import__("pathlib").Path(__file__).read_text(
        encoding="utf-8")
except OSError:                                          # pragma: no cover
    _MY_SOURCE = ""


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


def _sign_rows(text: str, parse, fmt, sort: bool = False) -> str | None:
    """One digest over `parse(text)`'s own rows, each turned into a string
    by `fmt` — the shared shape behind most sign_X functions below (built
    2026-09-16 to replace 10 near-identical one-off definitions). `fmt`
    IS the domain knowledge (which fields dedup should key on, which drift
    without meaning anything changed) — this function only owns the loop
    and the hashing, not what to include.
    """
    rows = parse(text)
    if not rows:
        return None
    parts = [fmt(r) for r in rows]
    return _digest(sorted(parts) if sort else parts)


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


# Analítica Fantasy — the second probable-XI source. Two page shapes
# (Titulares vs Consenso) depending on how close the next match is.
# Why: docs/notes/sources.md#analitica-fantasy-two-page-shapes

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
        ids = [i.get("data-af-team")
               for i in _css(a, "img[data-af-team]") if i.get("data-af-team")]
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
            # data-af-team on the crest, inside this match's own anchor.
            "home_id": ids[0] if len(ids) > 1 else "",
            "away_id": ids[1] if len(ids) > 1 else "",
        })
    return rows


def sign_af_fixtures(html: str) -> str | None:
    return _digest(_surface(_css(lh.fromstring(html), 'a[href*="/partido/"]')))


# Digests parse_points()'s own extracted rows, not every table on the page
# — this page carries unrelated tables (ads, widgets) whose churn used to
# bump the signature and defeat dedup for no real change.
sign_points = partial(
    _sign_rows, parse=lambda t: parse_points(t, ""),
    fmt=lambda r: "%s|%s|%s" % (r["ff_id"], r["points"], r["games"]),
    sort=True)


# Who actually started — ground truth for grading both probable-XI sources.
# Why: docs/notes/sources.md#starters-ground-truth-for-grading-probable-xi

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
# Captured, not just stripped — meaning depends on `role`: on a starter's
# row it's the minute subbed OFF (blank = played full match); on a sub's
# row it's the minute he came ON (blank = unused, zero minutes).
MATCH_MINUTE_CAPTURE_RE = re.compile(r"(\d+)\s*'\s*$")
# Match paths shorten one club: /partidos/…-rayo-… vs. team page
# /laliga/equipos/rayo-vallecano. Every other club spells the same both
# places.
MATCH_ALIASES = {"rayo": "rayo-vallecano"}
# Fewer than 11 means the page was caught half-rendered or markup moved —
# not a real confirmed eleven.
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
    return _digest(_surface(_css(lh.fromstring(html), 'a[href*="/partidos/"]')))


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
                raw = _WS.sub(" ", cell[0].text_content()).strip()
                m = MATCH_MINUTE_CAPTURE_RE.search(raw)
                side_rows.append({
                    "observed_at": observed_at,
                    "source": SOURCE,
                    "match_id": path.split("-", 1)[0],
                    "team_slug": team,
                    "player_name": MATCH_MINUTE_RE.sub("", raw),
                    "player_slug": None,
                    "role": role,
                    "minute": m.group(1) if m else "",
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


def _rebuild(key: str, pattern, table: str, parse, sign, url_for, **kw):
    """A Source rebuilt from its key alone, or None if the key doesn't match.

    match_source(), player_source() and offer_source() were three copies of
    exactly this "match a regex, extract its group, build a Source or bail"
    shape — one per key family that isn't in the static registry because its
    URL is only known once a discovery page (the calendar, the activity
    feed, the team list) has already been read. `api_source()` stays its
    own function: it dispatches on FOUR different sub-cases with different
    cadences, not one regex, so folding it into this shape would be the
    over-abstraction the one-shape-per-real-difference rule warns against.
    """
    m = pattern.match(key or "")
    if not m:
        return None
    return Source(key, table, url_for(m), parse, sign, **kw)


def match_source(key: str) -> Source | None:
    """The entry for one match page, built from its key.

    Match keys are not in the registry because their URLs are only known once
    the calendar has been read, and there are 380 of them a season. The key
    carries the whole path, so this needs no lookup table and an old snapshot
    stays parseable long after the match is forgotten.
    """
    return _rebuild(key, MATCH_KEY_RE, "starters", parse_starters,
                    sign_starters, lambda m: MATCH_URL.format(path=m.group(1)),
                    cadence="once")


def played_sources(cal_html: str, observed_at: str = "") -> list[Source]:
    """One entry per match the calendar shows a score for, in calendar order.

    Handed to fetch the moment the calendar comes back, so a sweep discovers
    its own work list. `due()` drops the ones already stored, which is what
    makes this once-ever rather than daily.
    """
    return [match_source("match_%s" % r["path"])
            for r in parse_calendar(cal_html, observed_at) if r["score"]]


# Club Elo — team strength, not price. Reads the country page's embedded
# Vega-Lite chart spec, not an HTML table (the CSV API is dead).
# Why: docs/notes/sources.md#club-elo-strength-not-price
ELO_SOURCE = "clubelo"
ELO_URL = "https://clubelo.com/ESP"
ELO_COUNTRY = "ESP"
ELO_LEVEL = "1"
ELO_COLS = ("Name", "Elo", "FedURL", "Level")
# What the chart data is assigned to — every match read, so a second chart
# adds clubs rather than replacing the first.
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


sign_elo = partial(_sign_rows, parse=lambda t: parse_elo(t, ""),
                   fmt=lambda r: "%s=%s" % (r["club"], r["elo"]))


# football-data.co.uk — real match results (goals/shots/corners), splitting
# clean-sheet luck from goal luck in a way one Elo scalar can't.
# Why: docs/notes/sources.md#football-data-co-uk-real-results
FD_BASE = "https://www.football-data.co.uk"
FD_URL = FD_BASE + "/mmz4281/{season}/SP1.csv"
FD_SOURCE = "football-data"

# Consulted only after match_team()'s ordinary join fails. Keyed by THEIR
# name (opposite direction from ELO_ALIASES, which starts from our teams),
# since the input here is football-data's own CSV spelling.
FD_ALIASES = {
    "ath bilbao": "athletic", "ath madrid": "atletico",
    "espanol": "espanyol", "sociedad": "real-sociedad",
    "vallecano": "rayo-vallecano", "dep a coruna": "deportivo",
    "santander": "racing",
}

# Completed seasons to backfill, once. A promoted side's earlier seasons
# here are simply absent — a fact about its history, not a join failure.
FD_SEASONS_BACK = 3

# HxG/AxG exist only on the current season's file, not completed ones. A
# season without them gets "" — never a guess.
FD_FIELDS = ("FTHG", "FTAG", "HxG", "AxG", "HS", "AS", "HST", "AST",
            "HC", "AC")


def fd_season_code(now: datetime) -> str:
    """"2627" for the season running Aug 2026 - Jun 2027, from today's date.

    La Liga's close season is June-July; competitive fixtures run Aug-May.
    July is the cutover because it is the one point in the year this cannot
    be wrong at — no match of either the outgoing or incoming season is ever
    played in July.
    """
    y = now.year if now.month >= 7 else now.year - 1
    return "%02d%02d" % (y % 100, (y + 1) % 100)


def fd_sources(now: datetime | None = None) -> list["Source"]:
    """The current season's file (updates as it's played) plus FD_SEASONS_BACK
    completed ones (fetched once, never again — a finished season's result
    does not change).
    """
    now = now or datetime.now(timezone.utc)
    cur_y = now.year if now.month >= 7 else now.year - 1
    out = []
    for back in range(FD_SEASONS_BACK + 1):
        y = cur_y - back
        season = "%02d%02d" % (y % 100, (y + 1) % 100)
        out.append(Source(
            "fd_%s" % season, "results_history",
            FD_URL.format(season=season), parse_fd_results, sign_fd_results,
            cadence="every_run" if back == 0 else "once"))
    return out


def _fd_rows(text: str) -> list[dict]:
    """csv.DictReader over the file, BOM stripped. Empty on anything that
    does not even look like the file this parser expects, rather than an
    exception mid-run."""
    if not text:
        return []
    return list(csv.DictReader(io.StringIO(text.lstrip("﻿"))))


def _fd_date(raw: str) -> str:
    """dd/mm/yy or dd/mm/yyyy -> yyyy-mm-dd. "" on anything else — a date
    this repo cannot read is not evidence of when the match was played."""
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


def _fd_match_team(side: str, teams) -> str | None:
    """Exact then substring, over this repo's own twenty slugs.

    ffcore.text.match_one() is the shared rule — also used by
    ffcore.fixture.match_team for the equivalent question against a
    different page. This file still doesn't need ffcore.fixture (which
    pulls in ffcore.tidy); ffcore.text has no ffcore-internal imports, so
    depending on it keeps this file self-testing with lxml alone.
    """
    return match_one(side, teams)


def _fd_slug(name: str) -> str:
    """This repo's slug for a football-data team name, or "" unresolved.

    "" is kept, not dropped: a match with one side in this repo's current
    twenty and one side that has since been relegated still says something
    real about the side that IS current.
    """
    hit = _fd_match_team(name, TEAMS)
    if hit:
        return hit
    slug = FD_ALIASES.get(norm(name) or "")
    return slug if slug in TEAMS else ""


def parse_fd_results(text: str, observed_at: str,
                     key: str = "fd_2526") -> list[dict]:
    """One row per match: goals, xG where the file carries it, and both
    teams resolved to this repo's own club slugs.
    """
    m = re.match(r"^fd_(\d{4})$", key)
    season = m.group(1) if m else ""
    rows = []
    for r in _fd_rows(text):
        home, away = (r.get("HomeTeam") or "").strip(), \
                     (r.get("AwayTeam") or "").strip()
        if not home or not away:
            continue
        row = {
            "observed_at": observed_at, "source": FD_SOURCE,
            "season": season, "date": _fd_date(r.get("Date") or ""),
            "home_name": home, "away_name": away,
            "home": _fd_slug(home), "away": _fd_slug(away),
        }
        for col, out_key in zip(FD_FIELDS,
                                ("home_goals", "away_goals", "home_xg",
                                 "away_xg", "home_shots", "away_shots",
                                 "home_shots_on_target", "away_shots_on_target",
                                 "home_corners", "away_corners")):
            row[out_key] = (r.get(col) or "").strip()
        rows.append(row)
    return rows


# Every match RESULT, not the odds columns — those move after publication
# and would make yesterday's scoreline look like new content.
sign_fd_results = partial(
    _sign_rows, parse=_fd_rows,
    fmt=lambda r: "%s|%s|%s|%s|%s" % (r.get("Date"), r.get("HomeTeam"),
                                      r.get("AwayTeam"), r.get("FTHG"),
                                      r.get("FTAG")))


# Bookmaker-implied match odds — team-level, logged now to accumulate real
# history for a future backtest. Not wired into scoring yet — season_board()
# still reads only Elo/value, on purpose.
# Why: docs/notes/forecast.md#odds-parked-log-dont-integrate-yet
#
# {odds_key} is a credential placeholder, not a URL param like {date}/{base}:
# ingest.py fills it from a gitignored file and skips this source (loudly,
# once) when that file is missing, same as the league bearer token.
#
# Free tier is 500 credits/month; one call covers every upcoming match
# regardless of match count. Daily cadence, like Elo.
ODDS_URL = ("https://api.the-odds-api.com/v4/sports/soccer_spain_la_liga"
           "/odds/?apiKey={odds_key}&regions=eu&markets=h2h"
           "&oddsFormat=decimal")
ODDS_SOURCE = "odds_api"


def _median(xs: list[float]) -> float | None:
    xs = sorted(xs)
    n = len(xs)
    if n == 0:
        return None
    mid = n // 2
    return xs[mid] if n % 2 else (xs[mid - 1] + xs[mid]) / 2.0


def parse_odds(text: str, observed_at: str,
              key: str = "odds_api") -> list[dict]:
    """One row per upcoming match: the MEDIAN bookmaker price per outcome
    (robust to one outlying book, cheaper than a trimmed mean to reason
    about), converted to an overround-free implied probability.

    MEDIAN, NOT MEAN — a single mispriced book (seen live: a 2.5 next to a
    dozen books all at 2.3) should not move the estimate as much as it
    would move an average. Overround removed by normalising the three
    outcomes' raw implied probabilities (1/price) to sum to 1 — a
    bookmaker's own three prices always imply MORE than 100% (their
    margin), so an unnormalised reading would systematically understate
    every outcome.

    Teams resolved to this repo's own slugs via `_fd_match_team()` (the
    same small name-matching rule football-data's results already use,
    not a new one) — unresolved sides keep their raw odds-API name rather
    than being dropped, so a genuinely new/renamed club still leaves a
    row worth having once resolved by hand later.
    """
    try:
        events = json.loads(text) if text else []
    except (ValueError, TypeError):
        return []
    rows = []
    for ev in events:
        home_name = (ev.get("home_team") or "").strip()
        away_name = (ev.get("away_team") or "").strip()
        if not home_name or not away_name:
            continue
        prices: dict[str, list[float]] = {"home": [], "away": [], "draw": []}
        n_books = 0
        for bk in ev.get("bookmakers") or []:
            h2h = next((m for m in bk.get("markets") or []
                       if m.get("key") == "h2h"), None)
            if h2h is None:
                continue
            outcomes = {o.get("name"): o.get("price")
                       for o in h2h.get("outcomes") or []}
            try:
                prices["home"].append(float(outcomes[home_name]))
                prices["away"].append(float(outcomes[away_name]))
                prices["draw"].append(float(outcomes["Draw"]))
            except (KeyError, TypeError, ValueError):
                continue
            n_books += 1
        if n_books == 0:
            continue
        med = {k: _median(v) for k, v in prices.items()}
        implied = {k: (1.0 / p if p else 0.0) for k, p in med.items()}
        total = sum(implied.values())
        if total <= 0:
            continue
        rows.append({
            "observed_at": observed_at, "source": ODDS_SOURCE,
            "kickoff": (ev.get("commence_time") or "").strip(),
            "home_name": home_name, "away_name": away_name,
            "home": _fd_slug(home_name), "away": _fd_slug(away_name),
            "n_bookmakers": n_books,
            "p_home": implied["home"] / total,
            "p_draw": implied["draw"] / total,
            "p_away": implied["away"] / total,
        })
    return rows


# Resolved teams and MEDIAN implied probabilities, rounded to 3dp — real
# line movement changes the signature; per-millisecond `last_update` churn
# on an unmoved line does not.
sign_odds = partial(
    _sign_rows, parse=lambda t: parse_odds(t, ""),
    fmt=lambda r: "%s|%s|%.3f|%.3f|%.3f" % (
        r["home"] or r["home_name"], r["away"] or r["away_name"],
        r["p_home"], r["p_draw"], r["p_away"]))


# Player-level xG/xA — the skill side points can't separate from luck.
# Endpoint is POST main/getPlayersStats/ form data, not the documented
# <script>-tag scrape (dead) — the one source needing `Source.body`.
# Why: docs/notes/sources.md#understat-player-level-xg
UNDERSTAT_URL = "https://understat.com/main/getPlayersStats/"
UNDERSTAT_SOURCE = "understat"
UNDERSTAT_LEAGUE = "La_liga"
# One prior completed season alongside the live one — the same shape
# points.py's own baseline/live split already uses, not a new convention.
UNDERSTAT_SEASONS_BACK = 1


def understat_sources(now: datetime | None = None) -> list["Source"]:
    """This season's player stats, refreshed every run, plus
    UNDERSTAT_SEASONS_BACK completed ones fetched once — a finished season's
    xG does not change. Understat labels a season by its START year
    ("2026" for 2026/2027), which `now.month >= 7` derives the same way
    fd_sources() derives football-data.co.uk's two-digit season code.
    """
    now = now or datetime.now(timezone.utc)
    cur_y = now.year if now.month >= 7 else now.year - 1
    out = []
    for back in range(UNDERSTAT_SEASONS_BACK + 1):
        y = cur_y - back
        out.append(Source(
            "understat_%d" % y, "understat_players", UNDERSTAT_URL,
            parse_understat_players, sign_understat_players,
            cadence="every_run" if back == 0 else "once",
            body={"league": UNDERSTAT_LEAGUE, "season": str(y)}))
    return out


def _understat_rows(text: str) -> list[dict]:
    """The response's own `players` list, or [] on anything else — a
    malformed or error body ({"error": {...}}, seen from the GET form of
    this same URL) is not evidence of an empty league.
    """
    try:
        data = json.loads(text or "")
    except (TypeError, ValueError):
        return []
    if not isinstance(data, dict) or not data.get("success"):
        return []
    players = data.get("players")
    return players if isinstance(players, list) else []


def parse_understat_players(text: str, observed_at: str,
                            key: str = "understat_2026") -> list[dict]:
    """One row per player, this source's own season totals.

    Club resolved to this repo's own slug via the same `_fd_match_team`
    matcher football-data.co.uk's team names use — unresolved is kept, not
    dropped, since a team_title this repo can't place still says something
    real about the player.

    Understat's own numeric id passes through as `understat_id`, unresolved
    against the crosswalk (this file stays dependency-free).
    """
    m = re.match(r"^understat_(\d{4})$", key)
    season = m.group(1) if m else ""
    out = []
    for p in _understat_rows(text):
        pid = str(p.get("id") or "").strip()
        name = (p.get("player_name") or "").strip()
        if not pid or not name:
            continue
        out.append({
            "observed_at": observed_at, "source": UNDERSTAT_SOURCE,
            "season": season, "understat_id": pid, "player_name": name,
            "team_title": (p.get("team_title") or "").strip(),
            "team": _fd_match_team(p.get("team_title") or "", TEAMS) or "",
            "position": (p.get("position") or "").strip(),
            "games": (p.get("games") or "").strip(),
            "minutes": (p.get("time") or "").strip(),
            "goals": (p.get("goals") or "").strip(),
            "assists": (p.get("assists") or "").strip(),
            "xg": (p.get("xG") or "").strip(),
            "xa": (p.get("xA") or "").strip(),
            "npg": (p.get("npg") or "").strip(),
            "npxg": (p.get("npxG") or "").strip(),
            "shots": (p.get("shots") or "").strip(),
            "key_passes": (p.get("key_passes") or "").strip(),
        })
    return out


# Every player's own counting stats — not xGChain/xGBuildup, which can
# drift by a thousandth between two reads of the same match data.
sign_understat_players = partial(
    _sign_rows, parse=_understat_rows,
    fmt=lambda p: "%s|%s|%s|%s|%s|%s" % (
        p.get("id"), p.get("games"), p.get("time"), p.get("goals"),
        p.get("assists"), p.get("xG")))


# The league's own API — state no public page publishes (live market,
# transactions, balances), behind the token ffcore/auth.py holds.
# Why: docs/notes/sources.md#the-leagues-own-api
LFG_SOURCE = "laliga"
API_LEAGUES_KEY = "api_leagues"
# {base} is filled by ingest from ffcore.auth.API_BASE rather than hardcoded,
# so the host lives in exactly one place — next to the token that opens it.
API_LEAGUES_URL = "{base}/v1/competition/1/leagues?x-lang=es"
API_MARKET_URL = "{base}/v1/competition/1/league/{league}/market?x-lang=es"
API_ACTIVITY_URL = ("{base}/v1/competition/1/leagues/{league}"
                    "/activity/{page}?x-lang=es")
API_TEAMS_URL = "{base}/v1/competition/1/leagues/{league}/teams?x-lang=es"
# THE ELEVEN YOU'VE ACTUALLY FIELDED. Hangs off /teams/{team}, NOT
# /leagues/{league}/teams/{team}.
API_LINEUP_URL = ("{base}/v1/competition/1/teams/{team}"
                  "/lineup/week/{week}?x-lang=es")
# The last round of the season — deliberate. Any unplayed round answers
# with the CURRENT lineup (verified: weeks 2, 5, 38 were byte-identical),
# so this needs no idea which round is current and can't read a stale one.
LINEUP_WEEK = 38

# Tables whose rows are immutable facts, keyed by an id that never changes
# (unlike most tidy tables, which are time series). Kept as full history,
# not one row per sweep — else each sweep rewrites the whole file with one
# more copy of everything, growing quadratically (api_activity.csv: 14.5KB
# -> 77KB in 36h before this existed). `observed_at` here means "when this
# entered the store", not "when it was last true": the value is the key, so
# ingest keeps the first sighting of each and drops the rest.
STORE_ONCE = {"api_activity": ("activity_id",),
              "api_players": ("player_id",),
              # Key includes the value: a stat line is corrigible, so an
              # unchanged line is stored once and a correction arrives as a
              # new row rather than overwriting the first.
              "api_stats": ("player_id", "week", "stat", "value", "points"),
              # The current season's file is cadence="every_run" and
              # re-parses the whole file every time, so this dedupes it the
              # same way (score included, since a scoreline can be corrected).
              "results_history": ("season", "date", "home_name", "away_name",
                                  "home_goals", "away_goals")}

# ONE DOCUMENT, TWO GRAINS. A source has one table and its rows go there —
# except that the squad feed carries both a squad (one row per player) and
# that player's match history (one row per player per week per stat). They
# arrive in one payload and must not be fetched twice, so a row may name the
# table it belongs to and ingest routes on it. Every row carries the column,
# so nothing has to guess what an absent one meant.
ROW_TABLE = "table"

ACT_JOINED, ACT_BUY, ACT_SELL = 9, 31, 33
# The per-jornada performance prize, flat 100,000/point for every manager.
# ACT_BONUS_ZERO is the same event with `amount` dropped rather than 0, for
# a zero-point jornada. NOT the daily "watch a video" bonus, which this
# feed carries no trace of at all.
# Why: docs/notes/league.md#the-weekly-performance-bonus-vs-the-video-bonus
ACT_BONUS, ACT_BONUS_ZERO = 6, 7
ACT_KIND = {ACT_JOINED: "joined", ACT_BUY: "buy", ACT_SELL: "sell",
           ACT_BONUS: "bonus", ACT_BONUS_ZERO: "bonus"}


def _j(text: str):
    """JSON or nothing. A rotted endpoint returns HTML, not an exception."""
    try:
        return json.loads(text or "")
    except (ValueError, TypeError):
        return None


def _pm(item: dict) -> dict:
    return (item or {}).get("playerMaster") or {}


def _player_identity(pm: dict) -> dict:
    """player_id/player_name/player_name_full/position_id/market_value,
    pulled the same way for every player-shaped API record — 5 call sites
    had drifted on this before being unified.
    Why: docs/notes/sources.md#_player_identity--one-join-five-drifted-copies
    """
    nick, name = pm.get("nickname") or "", pm.get("name") or ""
    return {
        "player_id": str(pm.get("id") or ""),
        "player_name": nick or name,
        "player_name_full": name if nick else "",
        "position_id": str(pm.get("positionId") or ""),
        "market_value": str(pm.get("marketValue") or ""),
    }


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


sign_api_leagues = partial(
    _sign_rows, parse=lambda t: parse_api_leagues(t, ""),
    fmt=lambda r: "%s=%s/%s" % (r["league_id"], r["money"], r["team_value"]))


def parse_api_market(text: str, observed_at: str,
                     key: str = "api_market") -> list[dict]:
    """Everything on offer in this league right now.

    Two kinds of row: `marketPlayerLeague` (app dealing a free agent) vs.
    `marketPlayerTeam` (a manager listing his own) — the difference matters
    downstream (route_kind()).
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
            ROW_TABLE: "api_market",
            "market_id": str(it.get("id") or ""),
            **_player_identity(pm),
            "sale_price": str(it.get("salePrice") or ""),
            # Bid count uses different keys per row kind: app-dealt uses
            # numberOfBids, manager-listed uses numberOfOffers (no
            # numberOfBids at all). Empty = not stated; "0" = listed, no bids.
            "bids": next((str(it[k]) for k in ("numberOfBids", "numberOfOffers")
                          if it.get(k) is not None), ""),
            "seller": it.get("discr") or "",
            "status": it.get("status") or "",
            # The player's own fitness — `status` above is the LISTING's
            # state (on_sale), this is the player's.
            "player_status": pm.get("playerStatus") or "",
            # A clause the player's own team has shielded (unpayable),
            # separate from a time-locked one. Only manager-listed rows
            # carry it.
            "shielded": "" if (it.get("playerTeam") or {}).get("isShielded")
                              is None else
                        str((it["playerTeam"]["isShielded"])).lower(),
            "expires_at": it.get("expirationDate") or "",
            # A bid you've already made rides on the listing itself, no
            # separate call — empty means no bid of yours on this listing,
            # not "unknown" (the field is always present).
            "bid_id": str((it.get("bid") or {}).get("id") or ""),
            "bid_money": str((it.get("bid") or {}).get("money") or ""),
            "bid_status": (it.get("bid") or {}).get("status") or "",
        })
    return rows


# expirationDate deliberately excluded — it ticks down continuously and
# would store a fresh archive every sweep. bid_status is included: a bid
# going pending -> accepted/rejected doesn't always move price/bids.
sign_api_market = partial(
    _sign_rows, parse=lambda t: parse_api_market(t, ""),
    fmt=lambda r: "%s@%s/%s/%s" % (r["player_id"], r["sale_price"],
                                   r["bids"], r["bid_status"]))


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

    A "bonus" row (kind == "bonus") is the weekly performance prize, not a
    transfer — it carries no `player_id` and instead names the jornada in
    `week`, so a reader must branch on `kind` rather than assume every row
    describes a buy or sell.
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
            "week": str(a.get("weekNumber") or ""),
        })
    return rows


# Append-only in practice, so the newest id would do — but a feed that
# rewrote history would then look unchanged, and this feed is about to
# become the ledger. Hash every id.
sign_api_activity = partial(_sign_rows, parse=lambda t: parse_api_activity(t, ""),
                            fmt=lambda r: r["activity_id"], sort=True)


def parse_api_teams(text: str, observed_at: str,
                    key: str = "api_teams") -> list[dict]:
    """Every squad in the league, as the app holds it — one row per player.

    Ownership without a replay: no ledger, no starting roster, no
    accumulated drift. Also carries each manager's points and position.

    `teamMoney` is null for everyone but the account's own — rivals' cash
    stays an estimate.
    """
    d = _j(text)
    if not isinstance(d, list):
        return []
    rows = []
    for t in d:
        m = t.get("manager") or {}
        # The league table is a fact about a TEAM, not repeated per player —
        # a team with no players is still a row here.
        if t.get("id"):
            rows.append({
                "observed_at": observed_at, "source": LFG_SOURCE,
                ROW_TABLE: "api_standings",
                "team_id": str(t["id"]),
                "user_id": str(m.get("id") or ""),
                "manager": m.get("managerName") or "",
                "position": str(t.get("position") or ""),
                # HOW THE TABLE MOVED, which a snapshot of position cannot
                # say on its own.
                "previous_position": str(t.get("previousPosition") or ""),
                "team_points": str(t.get("teamPoints") or ""),
                "fixture_points": str(t.get("fixturePoints") or ""),
                # The app's own squad valuation, vs. this repo's own sum of
                # market prices — a check, not something to average with it.
                "team_value": str(t.get("teamValue") or ""),
                # Null for every account but your own. Empty = not stated,
                # never zero (a zero would wrongly zero every bid ceiling
                # built on it).
                "team_money": str(t.get("teamMoney") or ""),
                "banned": "" if t.get("banned") is None
                          else str(t["banned"]).lower(),
                "starting_week": str(t.get("startingWeek") or ""),
            })
        for p in (t.get("players") or []):
            pm = _pm(p)
            if not pm.get("id"):
                continue
            rows.append({
                "observed_at": observed_at, "source": LFG_SOURCE,
                ROW_TABLE: "api_teams",
                # A fact about a PLAYER plus his owning manager; team-level
                # facts (position/points/balance) live on api_standings.
                "team_id": str(t.get("id") or ""),
                "manager": m.get("managerName") or "",
                **_player_identity(pm),
                "points": str(pm.get("points") or ""),
                "buyout": str(p.get("buyoutClause") or ""),
                # When the clause can actually be paid — a transfer locks it
                # for about a week. Empty means NOT STATED, never "now".
                "buyout_until": str(p.get("buyoutClauseLockedEndTime") or ""),
                # The game's OWN fitness call, distinct from the two
                # probable-XI website reads — stored beside them, not
                # blended in. Empty is NOT STATED.
                "player_status": pm.get("playerStatus") or "",
                # This ownership record's own id (distinct from the
                # player's) — needed for offers, which key on it rather
                # than the player id, only for a playerTeamId this account
                # holds.
                "player_team_id": str(p.get("playerTeamId") or ""),
            })
            rows += _stat_rows(pm, observed_at)
    return rows


# What the app scored him, broken into what he actually did. Long, not
# wide: each stat carries two numbers (count, points earned), so a new stat
# is a new row rather than a new column. Week total is derived
# (sum(points) == totalPoints), not stored.
def _stat_rows(pm: dict, observed_at: str) -> list[dict]:
    out = []
    for line in (pm.get("lastStats") or []):
        week = line.get("weekNumber")
        for stat, pair in (line.get("stats") or {}).items():
            # Every value the feed publishes is [what he did, what it scored].
            # Anything else is the shape moving, and is skipped rather than
            # read as a number in the wrong place.
            if not isinstance(pair, list) or len(pair) != 2:
                continue
            out.append({
                "observed_at": observed_at, "source": LFG_SOURCE,
                ROW_TABLE: "api_stats",
                "player_id": str(pm.get("id") or ""),
                "week": str(week if week is not None else ""),
                "stat": str(stat),
                "value": str(pair[0]), "points": str(pair[1]),
            })
    return out


def sign_api_teams(text: str) -> str | None:
    """Who is in which squad, and what you have. NOT the stat lines: points
    are recalculated after a match and a stored archive per recalculation
    would be a copy of every squad to record a corrected assist."""
    rows = parse_api_teams(text, "")
    squads = [r for r in rows if r[ROW_TABLE] == "api_teams"]
    table = [r for r in rows if r[ROW_TABLE] == "api_standings"]
    return _digest(["%s:%s" % (r["team_id"], r["player_id"]) for r in squads]
                   + ["$%s=%s/%s" % (r["team_id"], r["team_money"],
                                     r["position"]) for r in table])


# The app's own answer to "who am I playing", by slot. The keys are the app's
# and the order inside each is the app's; `tacticalFormation` is [4, 5, 1].
LINEUP_SLOTS = {"goalkeeper": "POR", "defender": "DEF",
                "midfield": "MED", "striker": "DEL"}

LINEUP_WEEK_RE = re.compile(r"api_lineup_(\d+)$")


def parse_api_lineup(text: str, observed_at: str,
                     key: str = "api_lineup_1") -> list[dict]:
    """The eleven you are fielding this week, one row per man.

    WHAT THIS REPLACES: `inputs/lineup.txt`, a checklist a human ticked. The
    marks went stale the moment a fielded player was sold — squads.py drops
    him and the file is left with ten — and the report then read ten men as a
    formation and told somebody already playing 4-5-1 to change it.

    `snapshot_at` is the app's own `teamSnapshotTookOn`: when the lineup was
    last CHANGED, which is a different fact from when we asked, and the one
    that says whether you have touched it since the last deal.

    A future week answers with the lineup standing now, so asking for the
    round being played and the round after it gives the same eleven. The week
    is in the page key rather than the payload, which does not name it.
    """
    d = _j(text)
    if not isinstance(d, dict):
        return []
    form = d.get("formation") or {}
    tactical = form.get("tacticalFormation") or []
    m = LINEUP_WEEK_RE.search(key or "")
    rows = []
    for slot, men in form.items():
        if slot not in LINEUP_SLOTS or not isinstance(men, list):
            continue
        for p in men:
            pm = (p or {}).get("playerMaster") or {}
            if not pm.get("id"):
                continue
            rows.append({
                "observed_at": observed_at,
                "source": "laliga",
                "week": m.group(1) if m else "",
                "slot": LINEUP_SLOTS[slot],
                "formation": "-".join(str(n) for n in tactical),
                **_player_identity(pm),
                # When YOU last moved it, not when we read it.
                "snapshot_at": str(d.get("teamSnapshotTookOn") or ""),
                "points": str(d.get("points") if d.get("points") is not None
                              else ""),
            })
    return rows


def sign_api_lineup(text: str) -> str | None:
    """Who is on, in which slot. Points and market values move under a lineup
    nobody has touched, and storing a copy for those would archive the same
    eleven twice a day for a season."""
    rows = parse_api_lineup(text, "")
    return _digest(["%s:%s" % (r["slot"], r["player_id"]) for r in rows]
                   + ["=%s" % (rows[0]["formation"] if rows else "")])


def lineup_source(team_id: str, week: int) -> Source:
    """The fielded-XI page for one team and one round.

    EVERY RUN, like the squad it must agree with: the whole point of the
    rerun button is picking up something you just changed, and a lineup you
    edited on the phone two minutes ago is exactly that.
    """
    return Source("api_lineup_%d" % week, "api_lineup",
                  API_LINEUP_URL.format(base="{base}", team=team_id,
                                        week=week),
                  parse_api_lineup, sign_api_lineup, auth=True)


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
        "team_id": str(d.get("teamId") or ""),
        **_player_identity(d),
        "player_id": pid,     # from the KEY, not the body — see above
    }]


def sign_api_player(text: str) -> str | None:
    rows = parse_api_player(text, "")
    return _digest([rows[0]["player_name"]]) if rows else None


API_PLAYERS_ALL_URL = "{base}/v1/competition/1/players?x-lang=es"


def parse_api_players_all(text: str, observed_at: str,
                          key: str = "api_players_all") -> list[dict]:
    """Every player in the competition, one row each — not just the ones
    this league happens to have transacted.

    `api_player_{id}` only ever gets queued for an id the activity feed
    mentioned (player_sources(), below), so a player nobody in a small
    private league has bought or sold stays unnamed forever. This is the
    one call that names all of them, including the reserve goalkeeper
    nobody has ever put up for sale — closing the crosswalk's app_id gap
    for players this specific league simply has no other way to observe.
    """
    d = _j(text)
    if not isinstance(d, list):
        return []
    return [{
        "observed_at": observed_at, "source": LFG_SOURCE,
        "team_id": str(p.get("teamId") or ""),
        **_player_identity(p),
        "player_status": p.get("playerStatus") or "",
    } for p in d if p.get("id")]


sign_api_players_all = partial(
    _sign_rows, parse=lambda t: parse_api_players_all(t, ""),
    fmt=lambda r: "%s@%s/%s" % (r["player_id"], r["player_status"],
                                r["market_value"]),
    sort=True)


def player_source(key: str) -> Source | None:
    """The entry for one player lookup, rebuilt from its key."""
    return _rebuild(key, API_PLAYER_KEY_RE, "api_players", parse_api_player,
                    sign_api_player,
                    lambda m: API_PLAYER_URL.format(base="{base}",
                                                    pid=m.group(1)),
                    cadence="once", auth=True)


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


# ---------------------------------------------------------------------------
# offers — who wants to buy a player you have listed
# ---------------------------------------------------------------------------
# `GET .../playerTeam/{id}/offer` answers for a playerTeamId you hold and
# 403s for one you don't — RECEIVED offers only. What you've bid on
# somebody else's listing rides on api_market's own `bid` field instead.
API_OFFER_URL = ("{base}/v1/competition/1/league/{league}/playerTeam/{ptid}"
                 "/offer?x-lang=es")
API_OFFER_KEY_RE = re.compile(r"^api_offer_(\d+)$")


def parse_api_offer(text: str, observed_at: str,
                    key: str = "api_offer_0") -> list[dict]:
    """Every pending bid on one of your own listed players.

    The playerTeamId comes from the KEY, exactly as api_player's player id
    does — the id in each item is the OFFER's, not the player's, and the
    endpoint's URL is the only place the playerTeamId being answered for
    is ever stated.
    """
    d = _j(text)
    if not isinstance(d, list):
        return []
    m = API_OFFER_KEY_RE.match(key or "")
    ptid = m.group(1) if m else ""
    if not ptid:
        return []
    out = []
    for it in d:
        if not it.get("id"):
            continue
        out.append({
            "observed_at": observed_at, "source": LFG_SOURCE,
            "player_team_id": ptid,
            "offer_id": str(it["id"]),
            "money": str(it.get("money") or ""),
            "status": it.get("status") or "",
            "created_at": it.get("createdAt") or "",
            "expires_at": it.get("expirationDate") or "",
            # True for a bid against a market listing, per every offer seen
            # live so far. Stored rather than assumed: a direct clause offer
            # (made on a player never listed) would carry the same shape
            # without it, and nothing here has observed one yet to check.
            "from_market": "" if it.get("isFromMarket") is None else
                           str(it["isFromMarket"]).lower(),
        })
    if out:
        return out
    # CHECKED AND EMPTY IS ITSELF THE READING, and it has to be a row. One
    # dedicated call per player means "nothing pending" contributes no row
    # at all otherwise — and load_api_offers() gates on the table's newest
    # stamp, exactly as every other API table does, so a sweep that adds no
    # row would silently leave a stale accepted-or-expired offer from days
    # ago looking like today's answer. A placeholder, stamped now, is what
    # keeps that gate honest for a table this sparse.
    return [{"observed_at": observed_at, "source": LFG_SOURCE,
             "player_team_id": ptid, "offer_id": "", "money": "",
             "status": "", "created_at": "", "expires_at": "",
             "from_market": ""}]


sign_api_offer = partial(
    _sign_rows, parse=lambda t: parse_api_offer(t, "", key="api_offer_0"),
    fmt=lambda r: "%s@%s/%s" % (r["offer_id"], r["money"], r["status"]))


def offer_source(key: str) -> Source | None:
    """The entry for one owned player's received-offers lookup, rebuilt from
    its key alone. No league id needed to rebuild it: parsing a stored page
    never fetches, so the URL stays a template, exactly as api_lineup_'s own
    rebuild in api_source() below leaves {team} and {week} unfilled.
    """
    return _rebuild(key, API_OFFER_KEY_RE, "api_offers", parse_api_offer,
                    sign_api_offer, lambda m: API_OFFER_URL, auth=True)


def offer_sources(teams_json: str, me: str, league: str,
                  observed_at: str = "") -> list[Source]:
    """One lookup per player YOU hold, never a rival's — see API_OFFER_URL's
    own note on the 403 that answers for the rest.

    Queued the moment api_teams comes back, the same shape as
    player_sources() off the activity feed — but EVERY RUN, not "once": a
    player you still hold can pick up or lose a pending offer between one
    sweep and the next in a way his name never does.
    """
    out, seen = [], set()
    for r in parse_api_teams(teams_json, observed_at):
        if r.get(ROW_TABLE) != "api_teams" or r.get("manager") != me:
            continue
        ptid = r.get("player_team_id")
        if not ptid or ptid in seen:
            continue
        seen.add(ptid)
        out.append(Source(
            "api_offer_%s" % ptid, "api_offers",
            API_OFFER_URL.format(base="{base}", league=league, ptid=ptid),
            parse_api_offer, sign_api_offer, auth=True))
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
    if key.startswith("api_lineup_"):
        return Source(key, "api_lineup", API_LINEUP_URL,
                      parse_api_lineup, sign_api_lineup, auth=True)
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
        # The eleven you are actually fielding, from the same account. It
        # hangs off the TEAM and not the league, which is why it was believed
        # not to exist.
        if r.get("team_id"):
            out.append(lineup_source(r["team_id"], LINEUP_WEEK))
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
    # GET for every source until Understat: its player-stats endpoint is a
    # POST-only XHR (verified directly — the GET form of the same URL
    # answers {"error": {...}}), so `body` (form data, sent to a POST) is
    # the one field that turns "which HTTP verb" from a repo-wide constant
    # into a per-source choice. None means GET, unchanged for every
    # existing source.
    body: dict | None = None
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


@lru_cache(maxsize=None)
def sources(enabled_only: bool = True) -> list[Source]:
    """Every page we collect, in fetch order.

    MEMOISED. The registry is built from static constants (TEAMS, AF_TEAMS,
    the URL templates) — nothing about the answer changes between calls. It
    was being rebuilt from scratch, all ~46 entries with a .format() call
    each, on every one of source_for()'s 13,040 calls in one parse() run —
    34% of parse's runtime for a lookup that should have been near-free.

    Team pages are one entry each rather than one entry with twenty URLs, so
    that a per-page signature, a per-page failure and a per-page cadence all
    have somewhere to live.
    """
    out = [
        Source("market", "market", MARKET_URL, parse_market, sign_market),
        Source("points", "points", POINTS_URL, parse_points, sign_points),
    ]
    # BOTH TEAM SWEEPS, TWICE A DAY. They were "daily", which the sweep read
    # as once per calendar day, so the 11:40 run — the one lfg.timer says
    # exists because the probable XIs have firmed up by late morning — was
    # reading the XIs fetched at 00:13 and calling them current.
    #
    # Once a day was chosen on a measurement: a second sweep moved the XI for
    # 22 of 511 players across a fortnight, and the same forty requests bought
    # a whole second source instead. That trade is no longer forced. Forcing a
    # full sweep at 17:50 on 2026-08-19 cost 4 seconds of request time for
    # fifty pages, and moved 23 of 512 futbolfantasy rows and 70 of 197
    # analitica rows against the 00:13 reading — analitica firms up through
    # the morning as more pundits publish, which is the whole value of the
    # column. Four of the moved rows were in the fielded squad.
    out += [Source(f"team_{s}", "lineups", TEAM_URL.format(slug=s),
                   parse_team, sign_team, cadence="twice_daily")
            for s in TEAMS]
    out += [Source(f"af_{s}", "lineups", AF_TEAM_URL.format(slug=af),
                   parse_af_team, sign_af_team, cadence="twice_daily")
            for s, af in sorted(AF_TEAMS.items())]
    # One page, and it replaces a file you had to retype every jornada.
    out += [Source("af_fixtures", "fixtures", AF_HUB_URL,
                   parse_af_fixtures, sign_af_fixtures, cadence="daily")]
    # A rating changes when matches are played, so once a day is generous.
    # Short timeout deliberately: Club Elo is the one source nothing depends
    # on (a missing rating just falls back to squad value), so it should
    # never be allowed to eat much of a sweep's time budget.
    out += [Source("elo", "elo", ELO_URL, parse_elo, sign_elo,
                   cadence="daily", timeout=8.0)]
    # Real match results, team-level. Deterministic from today's date alone
    # (fd_sources() computes which seasons, no discovery page needed first),
    # so it is listed directly rather than queued at run time the way the
    # calendar's match pages and the league's discovered pages are.
    out += fd_sources()
    # Player-level xG/xA — see understat_sources()'s own docstring.
    out += understat_sources()
    # Bookmaker-implied match odds — see ODDS_URL's own note. `{odds_key}`
    # is filled by ingest.py from a never-committed credential file; a
    # missing key skips this one source, loudly, once — same shape as a
    # missing league token for auth=True sources, different mechanism.
    out += [Source("odds", "odds", ODDS_URL, parse_odds, sign_odds,
                   cadence="daily", timeout=15.0)]
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
    # Identity/position/team barely move; market_value/points/status do,
    # but both already update daily via api_teams and the per-player
    # lookups anyway — same cadence already used for elo/af_fixtures/the
    # calendar, all similarly slow-changing.
    out += [Source("api_players_all", "api_players_all", API_PLAYERS_ALL_URL,
                   parse_api_players_all, sign_api_players_all,
                   cadence="daily", auth=True)]
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
    return (match_source(key) or api_source(key) or player_source(key)
            or offer_source(key))


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
<div class="elemento_jugador" data-id="1234" data-nombre="Pedri"
     data-posicion="Mediocampista"
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
  <tr onclick="openPlayerPointsStats(4242, 'Íñigo Ruiz de Galarreta', 'R. de Galarreta', 'laliga-fantasy');">
      <td><span>Íñigo Ruiz de Galarreta</span>
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
  <img alt="Sevilla" src="/escudos/536.png" data-af-team="536"/><span>Sevilla</span>
  <img alt="Rayo Vallecano" src="/escudos/728.png" data-af-team="728"/>
  <span>Rayo Vallecano</span>
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
 {"id":"m1","salePrice":5552694,"numberOfBids":1,"status":"on_sale",
  "discr":"marketPlayerLeague","expirationDate":"2026-08-18T22:00:00+02:00",
  "bid":{"id":"b1","money":5600000,"status":"pending",
         "createdAt":"2026-08-25T16:06:17+02:00"},
  "playerMaster":{"id":"2621","nickname":"Simeone","positionId":5,
                  "name":"Giuliano Simeone","playerStatus":"ok",
                  "marketValue":5552694}},
 {"id":"m2","salePrice":5403735,"numberOfOffers":3,"status":"on_sale",
  "discr":"marketPlayerTeam","expirationDate":"2026-08-19T22:00:00+02:00",
  "directOffer":false,
  "sellerTeam":{"id":"38066616",
                "manager":{"id":"3480702","managerName":"Albert Laporta"}},
  "playerTeam":{"buyoutClause":8477136,"isShielded":true,
                "buyoutClauseLockedEndTime":"2026-08-24T22:26:49+02:00"},
  "playerMaster":{"id":"2963","nickname":"Marc Roca","positionId":3,
                  "marketValue":5100000}},
 {"id":"m3","salePrice":1,"playerMaster":{}}]"""

_API_PLAYER_FIXTURE = """{"id":"1191","name":"Hugo Duro Perales",
 "nickname":"Hugo Duro","positionId":4,"marketValue":8534068,
 "teamId":"12","points":0}"""

_API_PLAYERS_ALL_FIXTURE = """[
 {"id":"1191","positionId":"3","nickname":"Hugo Duro","playerStatus":"ok",
  "marketValue":"8534068","points":12,"teamId":"12"},
 {"id":"68","positionId":"1","nickname":"Unai Simón","playerStatus":"ok",
  "marketValue":"49195828","points":34,"teamId":"3"}]"""

_API_ACTIVITY_FIXTURE = """[
 {"id":"a1","activityTypeId":31,"amount":58220110,"playerMasterId":1337,
  "user1Id":11881989,"createdAt":"2026-08-15T22:24:00+02:00"},
 {"id":"a2","activityTypeId":33,"amount":15202722,"playerMasterId":652,
  "user1Id":11881989,"createdAt":"2026-08-17T00:21:10+02:00"},
 {"id":"a3","activityTypeId":9,"amount":0,"playerMasterId":null,
  "user1Id":3480702,"createdAt":"2026-08-10T22:24:00+02:00"},
 {"id":"a4","activityTypeId":77,"amount":1,"playerMasterId":1,"user1Id":1,
  "createdAt":"2026-08-10T22:24:00+02:00"},
 {"id":"a5","activityTypeId":6,"amount":2200000,"weekNumber":2,
  "user1Id":3480702,"createdAt":"2026-08-25T04:28:22+02:00"},
 {"id":"a6","activityTypeId":7,"weekNumber":3,
  "user1Id":3480702,"createdAt":"2026-09-01T04:34:11+02:00"}]"""

_API_TEAMS_FIXTURE = """[
 {"id":"38091967","position":3,"previousPosition":5,"teamPoints":17,
  "fixturePoints":17,"teamValue":236374060,"banned":false,"startingWeek":"1",
  "teamMoney":23596582,
  "manager":{"id":"11881989","managerName":"miguel_autentico"},
  "players":[{"buyoutClause":47000000,"playerTeamId":"24338726",
    "buyoutClauseLockedEndTime":"2026-08-25T14:07:38+02:00",
    "playerMarket":{"id":"14186511","numberOfOffers":2,"directOffer":false,
                    "expirationDate":"2026-08-21T22:46:38+02:00"},
              "playerMaster":{"id":"1337","nickname":"Fornals",
                              "name":"Pablo Fornals Malla","slug":"fornals",
                              "positionId":3,"marketValue":58300000,
                              "playerStatus":"doubt","points":5,
   "lastStats":[{"weekNumber":1,"totalPoints":5,
                 "stats":{"mins_played":[90,2],"goals":[1,4],
                          "yellow_card":[1,-1],"marca_points":[7,0]}}]}}]},
 {"id":"38099509","position":1,"previousPosition":5,"teamPoints":24,
  "fixturePoints":24,"teamValue":253280692,"banned":false,"startingWeek":"1",
  "teamMoney":null,
  "manager":{"id":"11883172","managerName":"BurtonGM89"},
  "players":[{"buyoutClause":null,
              "playerMaster":{"id":"2621","nickname":"Simeone",
                              "name":"Giuliano Simeone","slug":"simeone-1",
                              "positionId":5,"marketValue":5552694,
                              "points":1}},
             {"playerMaster":{}}]}]"""

# One real pending bid on a listed player of mine, captured live 2026-08-25
# against Moncayola — the shape parse_api_offer reads.
_API_OFFER_FIXTURE = """[
 {"id":"49892747","money":6795815,"status":"pending",
  "createdAt":"2026-08-24T22:24:30+02:00","updatedAt":"2026-08-24T22:24:30+02:00",
  "isFromMarket":true,"expirationDate":"2026-08-25T22:24:00+02:00"}]"""


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


LINEUP_FIXTURE = """
{"formation": {"goalkeeper": [{"playerMaster": {"id": "1070",
   "nickname": "Ionut Radu", "name": "Ionut Andrei Radu", "positionId": 1,
   "marketValue": 4350000}}],
  "defender": [{"playerMaster": {"id": "255", "nickname": "Starfelt",
   "name": "Carl Starfelt", "positionId": 2, "marketValue": 9000000}}],
  "midfield": [{"playerMaster": {"id": "2464", "nickname": "Pepelu",
   "name": "Jos\u00e9 Luis Garc\u00eda Vay\u00e1", "positionId": 3,
   "marketValue": 7669774}}],
  "striker": [{"playerMaster": {"id": "3123", "nickname": "I\u00f1igo Vicente",
   "name": "I\u00f1igo Vicente", "positionId": 4, "marketValue": 12000000}}],
  "tacticalFormation": [4, 5, 1]},
 "teamSnapshotTookOn": "2026-08-19T20:26:10+02:00", "points": 0,
 "initialPoints": 0}
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
    # the other — via the shared ffcore.text.norm(), not a second fold. A
    # locally hand-rolled fold here once disagreed with norm() on apostrophe
    # names ("N'Diaye" -> "n diaye" vs norm()'s "ndiaye"), silently splitting
    # one player's starting-XI and fitness rows into two keys.
    assert norm("Eric García") == norm("Eric Garcia") == "eric garcia"
    assert norm("N'Diaye") == "ndiaye"

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
    # NO `slug` AND NO `player_path`. slug was this parser's own id, dug out
    # of the anchor or the photo filename — and it is the SAME NUMBER as
    # data-id, on every one of the 40,136 rows that had one, while data-id is
    # on all 48,182. A derived duplicate of a published id, missing 8,046
    # rows. player_path was never populated at all.
    assert "slug" not in m[0] and "player_path" not in m[0], m[0]
    # THE PAGE'S OWN ID, ON EVERY ROW. data-id sits beside data-nombre in the
    # same element and was never read: the parser took the name and derived a
    # slug from the anchor, which is absent for 112 of 654 players. On the
    # 2026-08-19 snapshot data-id is present and DISTINCT on all 654, and it
    # separates the three names two men share — iker muñoz is 16975 and
    # 11362. Every key built out of "name, or name@club when two men share
    # it" existed because this attribute was not being read.
    assert m[0]["ff_id"] == "1234", m[0]

    # -- points table ------------------------------------------------------
    p = parse_points(_POINTS_FIXTURE)
    assert len(p) == 1, p                           # the '-' row is dropped
    assert p[0]["player_name"] == "R. de Galarreta"
    assert p[0]["player_name_full"] == "Íñigo Ruiz de Galarreta"
    assert p[0]["points"] == "156" and p[0]["games"] == "34"
    assert p[0]["avg"] == "4.590", p[0]             # comma decimal, via ratio()
    # THE ID IS IN THE CLICK HANDLER, not a data-* attribute — the only place
    # this page puts it, and the reason the season's points history was
    # keyed by name for as long as it was.
    assert p[0]["ff_id"] == "4242", p[0]
    # A row without one still parses; the oldest snapshots have no handler.
    assert parse_points(_POINTS_FIXTURE.replace(
        "openPlayerPointsStats(4242,", "somethingElse("))[0]["ff_id"] == ""
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
    # THEIR OWN CLUB IDS, inside the match anchor — both of them, on all 14
    # matches of the 2026-08-20 page. The names are kept for reading; the
    # ids are what the fixture board should join on, because "Celta" and
    # "Celta Vigo" are the same club and only one of those strings knows it.
    assert fx[0]["home_id"] == "536" and fx[0]["away_id"] == "728", fx[0]
    # Stored exactly as published, offset included — see the docstring.
    assert fx[0]["kickoff"] == "2026-08-15T19:30:00+00:00", fx[0]
    assert fx[0]["source"] == AF_SOURCE
    assert sign_af_fixtures(_AF_HUB_FIXTURE) is not None
    assert sign_af_fixtures("<html><body>no matches</body></html>") is None

    # -- what a parser is made of ------------------------------------------
    # A document's parse is cached between runs and the WHOLE cache used to be
    # discarded whenever this file changed — correct, and far too broad:
    # touching one parser re-parsed four hundred documents through the other
    # twenty, forty seconds, and editing a fixture string did it too.
    sample = "\n".join([
        "A = 1", "B = 2",
        "def helper(x):", "    return x + A",
        "def one(t):", "    return helper(t)",
        "def two(t):", "    return B",
    ])
    assert parser_deps(sample, "one") == {"one", "helper", "A"}, \
        parser_deps(sample, "one")
    assert parser_deps(sample, "two") == {"two", "B"}
    # A name defined nowhere at the top level — a builtin, an import, a local
    # — is simply not in the map and drops out rather than being guessed at.
    assert parser_deps(sample, "helper") == {"helper", "A"}
    assert parser_deps(sample, "missing") == set()
    # THE POINT: a change reaches exactly the parsers that can see it.
    edited = sample.replace("return x + A", "return x - A")
    assert parser_sig("one", edited) != parser_sig("one", sample)
    assert parser_sig("two", edited) == parser_sig("two", sample)
    # ...and a change to an unrelated constant reaches neither.
    assert parser_sig("one", sample.replace("B = 2", "B = 3")) \
        == parser_sig("one", sample)
    # Two parsers are two fingerprints, or one cache entry would answer for
    # the other the moment they were keyed together.
    assert parser_sig("one", sample) != parser_sig("two", sample)
    # Recursion terminates.
    rec = "def loops(x):\n    return loops(x)"
    assert parser_deps(rec, "loops") == {"loops"}
    # A name nothing defines falls back to the digest of the whole file, which
    # is what this always used to be — the safe answer, never a guess.
    assert parser_sig("missing", sample) != parser_sig("missing", edited)

    # Every parser in the registry must actually resolve, or it silently gets
    # the whole-file fallback and the cache goes back to being all-or-nothing.
    whole = parser_sig("no such function at all")
    for src_ in sources():
        assert parser_sig(src_.parse.__name__) != whole, src_.key

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

    # -- football-data.co.uk --------------------------------------------
    # Real column order and BOM, pulled live 2026-08-20 from
    # football-data.co.uk/mmz4281/2627/SP1.csv (current season, so it
    # carries HxG/AxG) and mmz4281/2223/SP1.csv (older, no xG columns).
    _FD_CUR = ("﻿Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HTHG,"
              "HTAG,HTR,HxG,AxG,HS,AS,HST,AST,HF,AF,HC,AC,HY,AY,HR,AR\n"
              "SP1,15/08/2026,18:30,Alaves,Getafe,3,0,H,0,0,D,1.93,0.24,"
              "18,6,8,2,16,13,5,3,4,3,0,1\n"
              "SP1,15/08/2026,20:30,Sevilla,Vallecano,2,1,H,0,1,A,2.19,"
              "1.89,13,6,4,3,18,18,1,2,4,4,1,0\n")
    _FD_OLD = ("﻿Div,Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR\n"
              "SP1,17/08/22,Ath Bilbao,Girona,0,0,D\n")

    cur = parse_fd_results(_FD_CUR, "2026-08-20T1200Z", "fd_2627")
    assert len(cur) == 2, cur
    assert cur[0]["season"] == "2627" and cur[0]["date"] == "2026-08-15"
    # Both club names resolve WITHOUT an alias — "Alaves"/"Getafe" already
    # match this repo's own slugs exactly.
    assert cur[0]["home"] == "alaves" and cur[0]["away"] == "getafe"
    assert cur[0]["home_goals"] == "3" and cur[0]["home_xg"] == "1.93"
    assert cur[1]["home"] == "sevilla" and cur[1]["away"] == "rayo-vallecano"

    old = parse_fd_results(_FD_OLD, "2026-08-20T1200Z", "fd_2223")
    assert len(old) == 1, old
    # No xG column in an old file: "" not a guess, not a crash.
    assert old[0]["home_xg"] == "" and old[0]["away_xg"] == ""
    assert old[0]["home"] == "athletic"
    # A club not in this repo's current twenty (Girona, relegated since)
    # resolves to "" — kept, not dropped: Athletic's side of that match
    # still says something real about Athletic.
    assert old[0]["away"] == "", old[0]
    assert old[0]["away_name"] == "Girona"

    assert parse_fd_results("", "t", "fd_2627") == []
    assert parse_fd_results("not a csv file at all", "t", "fd_2627") == []
    # A row with no team names at all (a blank line, a header repeated) is
    # skipped rather than emitted with two empty slugs and no evidence.
    assert parse_fd_results("﻿Div,Date,HomeTeam,AwayTeam,FTHG,FTAG\n"
                            "SP1,17/08/22,,,,\n", "t", "fd_2223") == []

    assert sign_fd_results(_FD_CUR) is not None
    assert sign_fd_results("") is None
    # The odds columns move after publication; the signature must not, or a
    # result already stored looks like new content every sweep.
    assert sign_fd_results(_FD_CUR) == sign_fd_results(
        _FD_CUR.replace("18,6,8,2", "99,6,8,2"))
    # The RESULT moving is real content.
    assert sign_fd_results(_FD_CUR) != sign_fd_results(
        _FD_CUR.replace("Alaves,Getafe,3,0", "Alaves,Getafe,4,0"))

    assert fd_season_code(datetime(2026, 8, 20, tzinfo=timezone.utc)) == "2627"
    assert fd_season_code(datetime(2027, 5, 1, tzinfo=timezone.utc)) == "2627"
    assert fd_season_code(datetime(2026, 6, 30, tzinfo=timezone.utc)) == "2526"
    assert fd_season_code(datetime(2026, 7, 1, tzinfo=timezone.utc)) == "2627"

    fs = fd_sources(datetime(2026, 8, 20, tzinfo=timezone.utc))
    assert [s.key for s in fs] == ["fd_2627", "fd_2526", "fd_2425", "fd_2324"]
    assert fs[0].cadence == "every_run"
    assert all(s.cadence == "once" for s in fs[1:])
    assert all(s.table == "results_history" for s in fs)
    assert source_for("fd_2627").parse is parse_fd_results

    # -- odds: bookmaker-implied match odds ---------------------------------
    # Real payload, pulled live 2026-09-06 from the-odds-api.com's own
    # v4 soccer_spain_la_liga odds endpoint, trimmed to 3 bookmakers
    # (h2h_lay from betfair_ex_eu dropped — not the market this reads).
    _ODDS_LIVE = json.dumps([{
        "id": "823ef5c97dc93ff1e8fd7dbafb90c9d5",
        "sport_key": "soccer_spain_la_liga", "sport_title": "La Liga - Spain",
        "commence_time": "2026-09-06T19:00:00Z",
        "home_team": "Espanyol", "away_team": "Sevilla",
        "bookmakers": [
            {"key": "betsson", "title": "Betsson",
             "last_update": "2026-09-06T19:21:00Z",
             "markets": [{"key": "h2h", "last_update": "2026-09-06T19:21:00Z",
                         "outcomes": [{"name": "Espanyol", "price": 2.3},
                                     {"name": "Sevilla", "price": 3.2},
                                     {"name": "Draw", "price": 2.78}]}]},
            {"key": "betfair_ex_eu", "title": "Betfair",
             "last_update": "2026-09-06T19:20:59Z",
             "markets": [{"key": "h2h",
                         "last_update": "2026-09-06T19:20:59Z",
                         "outcomes": [{"name": "Espanyol", "price": 2.5},
                                     {"name": "Sevilla", "price": 3.55},
                                     {"name": "Draw", "price": 3.05}]}]},
            {"key": "betclic_fr", "title": "Betclic (FR)",
             "last_update": "2026-09-06T19:17:33Z",
             "markets": [{"key": "h2h",
                         "last_update": "2026-09-06T19:17:33Z",
                         "outcomes": [{"name": "Espanyol", "price": 2.35},
                                     {"name": "Sevilla", "price": 3.0},
                                     {"name": "Draw", "price": 2.9}]}]},
        ],
    }])
    odds = parse_odds(_ODDS_LIVE, "2026-09-06T1930Z")
    assert len(odds) == 1, odds
    o = odds[0]
    assert o["home"] == "espanyol" and o["away"] == "sevilla", o
    assert o["kickoff"] == "2026-09-06T19:00:00Z"
    assert o["n_bookmakers"] == 3, o
    # MEDIAN price per side: home [2.3, 2.5, 2.35] -> 2.35; away
    # [3.2, 3.55, 3.0] -> 3.2; draw [2.78, 3.05, 2.9] -> 2.9. Implied
    # 1/price, normalised to sum to 1 (removes the books' own margin).
    assert abs(o["p_home"] - 0.393) < 0.005, o
    assert abs(o["p_away"] - 0.289) < 0.005, o
    assert abs(o["p_draw"] - 0.318) < 0.005, o
    assert abs(o["p_home"] + o["p_away"] + o["p_draw"] - 1.0) < 1e-9, o

    assert parse_odds("", "t") == []
    assert parse_odds("not json", "t") == []
    # No h2h market on any book: no price to read, row dropped rather than
    # invented from nothing.
    assert parse_odds(json.dumps([{"home_team": "Espanyol",
                                   "away_team": "Sevilla",
                                   "bookmakers": [{"key": "x",
                                                   "markets": []}]}]),
                      "t") == []
    # A club not in this repo's current twenty: kept, unresolved slug "" —
    # same "still says something real about the resolved side" rule as
    # parse_fd_results above.
    unresolved = parse_odds(json.dumps([{
        "home_team": "Espanyol", "away_team": "Not A Real Club FC",
        "bookmakers": [{"key": "x", "markets": [{"key": "h2h",
                        "outcomes": [{"name": "Espanyol", "price": 2.0},
                                    {"name": "Not A Real Club FC",
                                     "price": 2.0},
                                    {"name": "Draw", "price": 3.0}]}]}]}]),
        "t")
    assert len(unresolved) == 1 and unresolved[0]["away"] == "", unresolved

    assert sign_odds(_ODDS_LIVE) is not None
    assert sign_odds("") is None
    # A `last_update` timestamp moving with the price unchanged: same
    # signature — that churn is not real content.
    assert sign_odds(_ODDS_LIVE) == sign_odds(
        _ODDS_LIVE.replace("2026-09-06T19:21:00Z", "2026-09-06T19:45:00Z"))
    # A real price move: different signature.
    assert sign_odds(_ODDS_LIVE) != sign_odds(
        _ODDS_LIVE.replace('"price": 2.3', '"price": 4.5'))

    assert source_for("odds").parse is parse_odds
    assert source_for("odds").table == "odds"
    assert source_for("odds").cadence == "daily"

    # -- understat: player-level xG/xA -------------------------------------
    # Real rows, pulled live 2026-08-21 from a direct POST to
    # understat.com/main/getPlayersStats/ with {league: La_liga,
    # season: 2025|2026} — the GET form of the same URL was tried first and
    # answers {"error": {...}}, which is why this is the one source that
    # needs Source.body.
    _UNDERSTAT_PAST = ('{"success": true, "players": [{"id": "3423", '
                       '"player_name": "Kylian Mbappe-Lottin", "games": '
                       '"31", "time": "2623", "goals": "25", "xG": '
                       '"25.796528611332178", "assists": "5", "xA": '
                       '"7.240631651133299", "shots": "146", '
                       '"key_passes": "65", "position": "F S", '
                       '"team_title": "Real Madrid", "npg": "17", '
                       '"npxG": "19.107029650360346"}]}')
    _UNDERSTAT_LIVE = ('{"success": true, "players": [{"id": "13350", '
                       '"player_name": "Roberto Fern\\u00e1ndez", "games": '
                       '"1", "time": "90", "goals": "2", "xG": '
                       '"1.1255605220794678", "assists": "0", "xA": '
                       '"0.1955643743276596", "shots": "2", '
                       '"key_passes": "2", "position": "F", "team_title": '
                       '"Espanyol", "npg": "2", "npxG": '
                       '"1.1255605220794678"}]}')

    past = parse_understat_players(_UNDERSTAT_PAST, "2026-08-21T0000Z",
                                   "understat_2025")
    assert len(past) == 1, past
    assert past[0]["season"] == "2025" and past[0]["understat_id"] == "3423"
    assert past[0]["player_name"] == "Kylian Mbappe-Lottin"
    # "Real Madrid" resolves to this repo's own slug without an alias.
    assert past[0]["team"] == "real-madrid", past[0]
    assert past[0]["games"] == "31" and past[0]["minutes"] == "2623"
    assert past[0]["xg"] == "25.796528611332178"
    assert past[0]["xa"] == "7.240631651133299"

    live = parse_understat_players(_UNDERSTAT_LIVE, "2026-08-21T0522Z",
                                   "understat_2026")
    assert live[0]["player_name"] == "Roberto Fernández"  # á decoded
    assert live[0]["team"] == "espanyol" and live[0]["season"] == "2026"

    # A GET-form error body is not an empty league — it is unusable, and the
    # two must not be confused: [] either way, but proven here to be the
    # SAME [] a genuinely empty response gives, not a crash.
    assert parse_understat_players('{"error": {"error_code": 4}}', "t") == []
    assert parse_understat_players("", "t") == []
    assert parse_understat_players("not json at all", "t") == []
    # A row missing an id or a name is dropped, not emitted half-blank.
    assert parse_understat_players(
        '{"success": true, "players": [{"id": "", "player_name": "X"}, '
        '{"id": "1", "player_name": ""}]}', "t") == []

    assert sign_understat_players(_UNDERSTAT_PAST) is not None
    assert sign_understat_players("") is None
    assert sign_understat_players('{"success": true, "players": []}') is None
    # npxG/shots/key_passes are NOT in the signature — xGChain/xGBuildup-
    # style figures can drift by a thousandth between two reads of the same
    # match data, and the counting stats (games, minutes, goals, assists,
    # xG) are what a changed page actually means here.
    assert sign_understat_players(_UNDERSTAT_PAST) == sign_understat_players(
        _UNDERSTAT_PAST.replace('"shots": "146"', '"shots": "147"'))
    # A real counting-stat change IS new content.
    assert sign_understat_players(_UNDERSTAT_PAST) != sign_understat_players(
        _UNDERSTAT_PAST.replace('"goals": "25"', '"goals": "26"'))

    us = understat_sources(datetime(2026, 8, 20, tzinfo=timezone.utc))
    assert [s.key for s in us] == ["understat_2026", "understat_2025"]
    assert us[0].cadence == "every_run" and us[1].cadence == "once"
    assert all(s.table == "understat_players" for s in us)
    assert all(s.url == UNDERSTAT_URL for s in us)
    assert us[0].body == {"league": "La_liga", "season": "2026"}
    assert us[1].body == {"league": "La_liga", "season": "2025"}
    # Every OTHER source in the registry is still a plain GET.
    assert all(s.body is None for s in sources() if s.key not in
              ("understat_2026", "understat_2025"))

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
    assert mk[0]["bids"] == "1" and mk[0]["seller"] == "marketPlayerLeague"
    # THE TWO KINDS COUNT THEIR BIDDERS UNDER DIFFERENT NAMES, and reading
    # only one of them left the count blank for the larger half. The app deals
    # a free agent and calls it `numberOfBids`; a manager lists one of his own
    # and it is `numberOfOffers`, on a row that carries no numberOfBids at
    # all. On 2026-08-19 that was 28 of 41 rows reading as "nobody knows" —
    # the exact number this feed exists to supply.
    assert mk[1]["bids"] == "3" and mk[1]["seller"] == "marketPlayerTeam", mk[1]
    # Empty is still NOT STATED, for a row that states neither.
    assert parse_api_market(
        _API_MARKET_FIXTURE.replace('"numberOfOffers":3,', ""), "t")[1]["bids"] == ""
    # A CLAUSE YOU CANNOT PAY IS NOT A PRICE — the lock already taught this
    # repo that. A shield is the other way it can be unpayable, and it rides
    # on the listing rather than on the squad. No True has been seen in the
    # wild yet (28 of 28 False on 2026-08-19), so it is stored and not acted
    # on, which is the only honest thing to do with an unobserved flag.
    assert mk[1]["shielded"] == "true" and mk[0]["shielded"] == "", mk[1]
    # The app's own fitness, on the market rows too.
    assert mk[0]["player_status"] == "ok" and mk[1]["player_status"] == ""
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
        _API_MARKET_FIXTURE.replace("5552694,\"numberOfBids\":1",
                                    "5552694,\"numberOfBids\":3"))
    # YOUR OWN BID, embedded on the listing rather than a second call — see
    # the module note on why nothing else can supply this.
    assert mk[0]["bid_id"] == "b1" and mk[0]["bid_money"] == "5600000"
    assert mk[0]["bid_status"] == "pending"
    assert mk[1]["bid_id"] == "" and mk[1]["bid_money"] == ""    # no bid here
    # A bid changing status must sign differently even when nothing else on
    # the listing does — the transition this field exists to notice.
    assert sign_api_market(_API_MARKET_FIXTURE) != sign_api_market(
        _API_MARKET_FIXTURE.replace('"status":"pending"',
                                    '"status":"accepted"'))

    ac = parse_api_activity(_API_ACTIVITY_FIXTURE, "t")
    # Known verbs kept, the unknown 77 dropped rather than guessed at.
    assert ([r["kind"] for r in ac] ==
            ["buy", "sell", "joined", "bonus", "bonus"]), ac
    assert ac[0]["amount"] == "58220110" and ac[0]["user_id"] == "11881989"
    # The bonus rows: a paid week and 7's zero-amount variant, both "bonus",
    # both carrying the jornada in `week` instead of a `player_id`.
    assert ac[3]["week"] == "2" and ac[3]["amount"] == "2200000", ac[3]
    assert ac[4]["week"] == "3" and ac[4]["amount"] == "", ac[4]
    assert sign_api_activity(_API_ACTIVITY_FIXTURE) is not None
    # Reordering the feed is not a change; a new row is.
    import json as _json
    _rev = _json.dumps(list(reversed(_json.loads(_API_ACTIVITY_FIXTURE))))
    assert sign_api_activity(_rev) == sign_api_activity(_API_ACTIVITY_FIXTURE)

    # ONE DOCUMENT, TWO GRAINS: the squad and the match history come in one
    # payload and every row names which table it is for, so the split is read
    # off the row rather than guessed at by shape.
    all_rows = parse_api_teams(_API_TEAMS_FIXTURE, "t")
    assert {r[ROW_TABLE] for r in all_rows} == {"api_teams", "api_stats",
                                            "api_standings"}
    tm = [r for r in all_rows if r[ROW_TABLE] == "api_teams"]
    assert len(tm) == 2, tm                 # the empty playerMaster is dropped
    assert tm[0]["manager"] == "miguel_autentico"
    assert tm[0]["buyout"] == "47000000", tm[0]
    # The lock comes through, because a clause you cannot pay is not a price.
    assert tm[0]["buyout_until"] == "2026-08-25T14:07:38+02:00", tm[0]
    assert tm[1]["buyout_until"] == ""
    assert tm[1]["manager"] == "BurtonGM89" and tm[1]["buyout"] == ""
    # The same two names as the market rows, for the same reason — this is
    # the feed the ownership join reads, so it is the one the full name
    # actually rescues players in.
    assert tm[0]["player_name"] == "Fornals", tm[0]
    assert tm[0]["player_name_full"] == "Pablo Fornals Malla", tm[0]
    assert tm[1]["player_name_full"] == "Giuliano Simeone", tm[1]
    # THIS OWNERSHIP RECORD'S OWN ID — what offer_sources() keys the
    # received-offers lookup on, distinct from the player id above.
    assert tm[0]["player_team_id"] == "24338726", tm[0]
    assert tm[1]["player_team_id"] == "", tm[1]     # not stated for this one

    # -- the league table is a fact about a TEAM ----------------------------
    # It was carried on every player row: five managers' positions, points and
    # balances repeated 76 times a sweep, 27% of the bytes in the file. Wrong
    # grain as well as wasteful — the standings over a season were buried in a
    # player-grain table and could not be read without deduplicating it.
    sd = [r for r in all_rows if r[ROW_TABLE] == "api_standings"]
    assert len(sd) == 2, sd
    assert sd[0]["manager"] == "miguel_autentico" and sd[0]["position"] == "3"
    assert sd[0]["team_points"] == "17" and sd[0]["team_money"] == "23596582"
    assert sd[0]["user_id"] == "11881989" and sd[0]["team_id"] == "38091967"
    # ...and it brings the fields nobody was reading either. previous_position
    # is how the table MOVED, which a snapshot of position alone cannot say.
    assert sd[0]["previous_position"] == "5", sd[0]
    assert sd[0]["team_value"] == "236374060" and sd[0]["fixture_points"] == "17"
    assert sd[0]["banned"] == "false" and sd[0]["starting_week"] == "1"
    # A rival states no balance, and empty is NOT STATED — never zero, which
    # would read as broke and wrongly zero every bid ceiling built on it.
    assert sd[1]["team_money"] == "" and sd[1]["manager"] == "BurtonGM89"
    # A team with no players at all is still a row in the table: it is a fact
    # about the team, not about anybody's squad.
    lonely = parse_api_teams(
        '[{"id":"9","position":5,"manager":{"id":"7","managerName":"Empty"},'
        '"players":[]}]', "t")
    assert [r[ROW_TABLE] for r in lonely] == ["api_standings"], lonely

    # The squad rows keep only what is true of a PLAYER, plus the manager who
    # owns him — which is the grain of the row, not a repeated fact.
    assert "team_points" not in tm[0] and "team_money" not in tm[0], tm[0]
    assert "position" not in tm[0] and "user_id" not in tm[0], tm[0]
    assert tm[0]["manager"] == "miguel_autentico" and tm[0]["team_id"]

    # -- what the app knows and nobody was reading --------------------------
    # THE APP'S OWN FITNESS. Both probable-XI columns in every report are
    # editorial reads off two websites; this is the operator of the game
    # saying whether he can play. Stored beside them, not blended into them —
    # nothing has been graded against a played jornada yet.
    assert tm[0]["player_status"] == "doubt", tm[0]
    assert tm[1]["player_status"] == "", tm[1]      # absent is not "ok"
    # `playerMarket` ON A SQUAD ROW IS NOT KEPT, and that is a decision. It
    # says the player is listed and how many offers are in — and every one of
    # its 28 blocks joined, id for id, to a marketPlayerTeam row already in
    # api_market, with the same expiry to the second. Two copies of one fact
    # in two tables is how a number gets added to one and not the other, which
    # has bitten this repo three times. The market table is the one place; a
    # squad row reaches it on player_id.
    assert "offers" not in tm[0] and "listed_until" not in tm[0], tm[0]

    # THE STAT LINES, one row per player per week per stat. Long and not wide
    # because each stat carries TWO numbers — what he did and what it scored —
    # so a wide table would be 42 columns and a new stat would widen it. Long,
    # a new stat is a new row and every reader keeps working.
    st = [r for r in all_rows if r[ROW_TABLE] == "api_stats"]
    assert len(st) == 4, st
    goals = next(r for r in st if r["stat"] == "goals")
    assert goals["player_id"] == "1337" and goals["week"] == "1", goals
    assert goals["value"] == "1" and goals["points"] == "4", goals
    # A stat that costs points keeps its sign.
    assert next(r for r in st if r["stat"] == "yellow_card")["points"] == "-1"
    # THE WEEK TOTAL IS THE SUM OF THE PARTS — verified against all 32 stat
    # lines in the store on 2026-08-19, 32 of 32 — so it is derived and not
    # stored. If that ever stops being true it is the shape moving, and the
    # raw archive is what re-reads it.
    assert sum(int(r["points"]) for r in st) == 5
    # The squad rows are NOT polluted by the stat rows: one document, two
    # grains, and the router splits them on the table each row names.
    assert len(tm) == 2 and "stat" not in tm[0], tm[0]
    # A player who has not played carries no stat line, which is not a zero.
    assert not any(r["player_id"] == "2621" for r in st), st
    # The signature is about the SQUAD, not the stats: a points recalculation
    # must not store a fresh archive of everything.
    assert sign_api_teams(_API_TEAMS_FIXTURE) == sign_api_teams(
        _API_TEAMS_FIXTURE.replace('"goals":[1,4]', '"goals":[1,9]'))

    # Discovery: the league id comes off the account, never a config file.
    disc = league_sources(_API_LEAGUES_FIXTURE)
    assert [s.key for s in disc] == ["api_market", "api_teams",
                                     "api_lineup_%d" % LINEUP_WEEK,
                                     "api_activity_0", "api_activity_1"], disc
    # The lineup page hangs off the TEAM id, which the same payload states.
    assert "/teams/38091967/lineup/" in next(
        s.url for s in disc if s.table == "api_lineup")
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
    assert pl[0]["player_name_full"] == "Hugo Duro Perales", pl
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

    # -- the bulk player list: every player, one call, no per-id lookup ----
    # api_player_N only ever names a player THIS league has transacted
    # (queued from the activity feed) — the bulk list names every player in
    # the competition regardless of whether anyone here has ever bought him,
    # which is what closes the crosswalk's app_id gap for players nobody in
    # a small private league has happened to trade yet.
    pa = parse_api_players_all(_API_PLAYERS_ALL_FIXTURE, "t")
    assert len(pa) == 2, pa
    assert pa[0]["player_id"] == "1191", pa
    assert pa[0]["player_name"] == "Hugo Duro", pa
    assert pa[0]["position_id"] == "3", pa
    assert pa[0]["team_id"] == "12", pa
    assert pa[0]["market_value"] == "8534068", pa
    assert pa[0]["player_status"] == "ok", pa
    assert pa[1]["player_id"] == "68" and pa[1]["player_name"] == "Unai Simón", pa
    assert parse_api_players_all("<html>", "t") == []
    assert sign_api_players_all(_API_PLAYERS_ALL_FIXTURE) is not None
    # Reordering the list is not a change; a different player is.
    _rev = _json.dumps(list(reversed(_json.loads(_API_PLAYERS_ALL_FIXTURE))))
    assert (sign_api_players_all(_rev) ==
            sign_api_players_all(_API_PLAYERS_ALL_FIXTURE))
    assert source_for("api_players_all").parse is parse_api_players_all
    assert source_for("api_players_all").table == "api_players_all"
    assert source_for("api_players_all").cadence == "daily"
    assert source_for("api_players_all").auth is True

    # -- offers: what somebody else is bidding for a player you have listed -
    off = parse_api_offer(_API_OFFER_FIXTURE, "t", "api_offer_24338726")
    assert len(off) == 1, off
    assert off[0]["player_team_id"] == "24338726", off
    assert off[0]["offer_id"] == "49892747" and off[0]["money"] == "6795815"
    assert off[0]["status"] == "pending", off
    assert off[0]["from_market"] == "true", off
    assert off[0]["expires_at"] == "2026-08-25T22:24:00+02:00", off
    # The id comes from the KEY, exactly as api_player's does, because the
    # body never states which player is being answered for. NO OFFERS IS
    # STILL A ROW: one call per player means silence would otherwise leave
    # the table's newest stamp behind, and load_api_offers()'s freshness
    # gate would then serve a stale accepted-or-expired offer as current.
    empty = parse_api_offer("[]", "t", "api_offer_24338726")
    assert len(empty) == 1 and empty[0]["status"] == "", empty
    assert empty[0]["player_team_id"] == "24338726", empty
    assert empty[0]["offer_id"] == "", empty
    # A key that fails the regex has no playerTeamId to stamp a row with —
    # there is nothing honest to say, so nothing is said.
    assert parse_api_offer(_API_OFFER_FIXTURE, "t", "not-a-key") == []
    assert sign_api_offer(_API_OFFER_FIXTURE) is not None
    # "No offers" is a real, signable reading too, not the rotted-page None —
    # that stays reserved for a body that isn't even a list.
    assert sign_api_offer("[]") is not None
    assert sign_api_offer("[]") != sign_api_offer(_API_OFFER_FIXTURE)
    assert sign_api_offer("not json") is None
    # A status change must sign differently even though nothing else moved —
    # accepted/rejected is exactly the transition this exists to notice.
    assert sign_api_offer(_API_OFFER_FIXTURE) != sign_api_offer(
        _API_OFFER_FIXTURE.replace('"pending"', '"accepted"'))

    # NEVER a rival's — see API_OFFER_URL's own note on the 403 that answers
    # for the rest. Fornals (mine) yields a lookup; Simeone (BurtonGM89's)
    # does not, even though both carry a buyout clause.
    osrc = offer_sources(_API_TEAMS_FIXTURE, "miguel_autentico", "017998544")
    assert [s.key for s in osrc] == ["api_offer_24338726"], osrc
    assert osrc[0].table == "api_offers" and osrc[0].auth
    assert osrc[0].cadence == "every_run", osrc[0]        # not "once": offers move
    assert "017998544" in osrc[0].url and "24338726" in osrc[0].url, osrc[0]
    # A manager not in the league yields nothing rather than a KeyError.
    assert offer_sources(_API_TEAMS_FIXTURE, "nobody", "017998544") == []
    # Resolvable by key alone, long after the offer has come and gone —
    # exactly what a stored snapshot needs, since re-parsing never re-fetches.
    assert source_for("api_offer_24338726").parse is parse_api_offer
    assert source_for("api_offer_24338726").table == "api_offers"
    assert offer_source("not-an-offer-key") is None

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
    # CAPTURED, NOT JUST DELETED. What it means depends on role (off-minute
    # for a starter, on-minute for a sub) — nothing here decides that, it
    # only carries the number off the page.
    assert xi[1]["minute"] == "64", xi[1]
    assert xi[0]["minute"] == "", xi[0]

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
    # -- the eleven you are actually fielding -------------------------------
    # THE FILE THIS REPLACES was ticked by hand and went one short every time
    # a fielded player was sold. The app has published the answer all along,
    # off /teams/{team} rather than /leagues/{league}/teams/{team}.
    ln = parse_api_lineup(LINEUP_FIXTURE, "t1", "api_lineup_38")
    assert len(ln) == 4, ln
    assert [r["slot"] for r in ln] == ["POR", "DEF", "MED", "DEL"], ln
    assert {r["formation"] for r in ln} == {"4-5-1"}
    assert ln[0]["week"] == "38" and ln[0]["player_id"] == "1070"
    # The nickname AND the birth name, because the crosswalk joins on both:
    # twelve of 76 owned players resolve only on one of them.
    assert ln[2]["player_name"] == "Pepelu"
    assert ln[2]["player_name_full"].startswith("Jos")
    # WHEN YOU LAST MOVED IT, which is not when we asked.
    assert ln[0]["snapshot_at"].startswith("2026-08-19T20:26")
    # A signature over who is on and where, so a run that only moved market
    # values does not archive the same eleven again.
    assert sign_api_lineup(LINEUP_FIXTURE) == sign_api_lineup(
        LINEUP_FIXTURE.replace("4350000", "4360000"))
    assert sign_api_lineup(LINEUP_FIXTURE) != sign_api_lineup(
        LINEUP_FIXTURE.replace('"1070"', '"1071"'))
    assert parse_api_lineup("not json", "t1") == []
    assert source_for("api_lineup_38").table == "api_lineup"

    reg = sources()
    # 8 standalone pages: market, points, af_fixtures, elo, the calendar, the
    # odds source, the API's discovery page, and the bulk player list (the
    # one API entry that needs no league id, so it is listed directly here
    # rather than queued once the discovery page reveals a league). The
    # three PER-LEAGUE API entries the discovery page reveals are not here —
    # they are queued at run time, like the match pages. Plus
    # FD_SEASONS_BACK + 1 football-data entries and UNDERSTAT_SEASONS_BACK +
    # 1 understat entries (both deterministic from today's date, so listed
    # directly rather than queued).
    assert len(reg) == (8 + len(TEAMS) + len(AF_TEAMS) + FD_SEASONS_BACK + 1
                        + UNDERSTAT_SEASONS_BACK + 1) == 54, len(reg)
    assert set(AF_TEAMS) == set(TEAMS), set(AF_TEAMS) ^ set(TEAMS)
    # Both team sweeps run twice a day — the 11:40 sweep exists because the
    # XIs firm up late morning, and a calendar-day cadence skipped it there.
    # market and points still run every sweep.
    # (af_fixtures is the hub page, not a team page, and stays daily: a
    # kickoff time does not move between breakfast and lunch.)
    assert {s.cadence for s in reg if s.key.startswith(("team_", "af_"))
            and s.key != "af_fixtures"} == {"twice_daily"}
    assert next(s.cadence for s in reg if s.key == "af_fixtures") == "daily"
    assert {s.cadence for s in reg if s.key in ("market", "points")} \
        == {"every_run"}
    assert {s.key for s in reg} >= {"market", "points", "team_barcelona",
                                    "af_fixtures"}
    assert len({s.key for s in reg}) == len(reg)          # keys are unique
    assert {s.table for s in reg} == {"market", "points", "lineups",
                                      "fixtures", "elo", "matches",
                                      "api_leagues", "results_history",
                                      "understat_players", "odds",
                                      "api_players_all"}
    assert source_for("team_celta").parse is parse_team
    assert source_for("gone") is None                     # retired page name

    # Every entry can actually parse and sign, with the key it declares —
    # this is what stops a new source being added half-wired.
    assert source_for("af_celta").parse is parse_af_team
    samples = {"market": _MARKET_FIXTURE, "points": _POINTS_FIXTURE,
               "af_fixtures": _AF_HUB_FIXTURE, "elo": _ELO_FIXTURE,
               CAL_KEY: _CAL_FIXTURE, API_LEAGUES_KEY: _API_LEAGUES_FIXTURE,
               "understat_2026": _UNDERSTAT_LIVE,
               "understat_2025": _UNDERSTAT_PAST, "odds": _ODDS_LIVE,
               "api_players_all": _API_PLAYERS_ALL_FIXTURE}
    # Half the AF teams get each shape, so neither branch can rot unnoticed.
    for i, k in enumerate(sorted(AF_TEAMS)):
        samples[f"af_{k}"] = _AF_FIXTURE if i % 2 else _AF_CONSENSO_FIXTURE
    for s in reg:
        html = samples.get(s.key, _FIXTURE)
        assert s.sign(html) is not None, s.key
        assert isinstance(s.parse(html, "2026-01-01T0000Z", s.key), list), s.key

    print("sources.py selftest OK (264 cases)")


if __name__ == "__main__":
    _selftest()
