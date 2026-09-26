
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from datetime import datetime, timezone
from functools import lru_cache, partial
from typing import Callable, NamedTuple

from ffcore.text import match_one, norm

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
           "ACT_CLAUSE",
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



TEAM_SELECT_RE = re.compile(r'<select[^>]*name="equipo"[^>]*>(.*?)</select>', re.S)
OPTION_RE = re.compile(r'<option[^>]*value="(\d+)"[^>]*>([^<]+)</option>')


def _once(seen: set, key) -> bool:
    """True the first time `key` is seen (and marks it); False on a repeat
    or a falsy key. The "skip if already seen, else mark it" filter this
    file wrote out by hand at six call sites."""
    if not key or key in seen:
        return False
    seen.add(key)
    return True


def _attr(chunk: str, name: str) -> str | None:
    m = re.search(rf'data-{name}="([^"]*)"', chunk)
    return m.group(1) if m else None


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


HREF_RE = re.compile(r'href="[^"]*?/jugadores/([^"?#]+)"')
PHOTO_RE = re.compile(r'/jugadores/ficha/(\d+)\.(?:png|jpg|jpeg|webp)')
ASSET_RE = re.compile(r'\.(png|jpg|jpeg|webp|svg)$', re.I)


def _slug(chunk: str) -> str | None:
    path = None
    for cand in HREF_RE.findall(chunk):
        cand = cand.rstrip("/")
        if cand and not ASSET_RE.search(cand):
            path = cand
            break
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
    m = TEAM_SELECT_RE.search(html)
    teams = ({tid: name.strip() for tid, name in OPTION_RE.findall(m.group(1))
             if tid != "0"} if m else {})
    rows = []
    for chunk in html.split('class="elemento_jugador')[1:]:
        name, value = _attr(chunk, "nombre"), _attr(chunk, "valor")
        if not name or not value:
            continue
        team_id = _attr(chunk, "equipo")
        rows.append({
            "observed_at": observed_at,
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



NAME_RE = re.compile(r"^(.*?)\s*(\d{1,3})\s*%")





FITNESS_ALT = {
    "lesionado": "injured",
    "duda": "doubt",
    "tocado": "doubt",
}

SEVERITY = ["unavailable", "suspended", "injured", "doubt"]

XI_SELECTORS = ['[class*="jugadores-titulares"] .jugador.tipo_lista',
                '[class*="jugadores-suplentes"] .jugador.tipo_lista']
FITNESS_SELECTORS = [".lesionados_wrapper section.mod.lesionados > .elemento",
                     "section.mod.nodisponibles .elemento"]


def _flagged_name(el) -> tuple[str, str]:
    a = _css(el, "a.jugador")
    if not a:
        return "", ""
    href = a[0].get("href") or ""
    return a[0].text_content().strip(), (_slug('href="%s"' % href) or "")


def _note(el) -> str:
    parts = [" ".join(c.text_content().split())
             for c in _css(el, ".comentario")]
    return " · ".join(p for p in parts if p)[:200]


def parse_fitness(doc) -> dict[str, dict]:
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
    slug = key[5:] if key.startswith("team_") else key
    doc = lh.fromstring(html)
    fitness = parse_fitness(doc)
    rows = []
    seen = set()

    def add(el, role):
        text = " ".join(el.text_content().split())
        m = NAME_RE.match(text)
        if m:
            name, pct = m.group(1).strip() or None, int(m.group(2))
        else:
            name, pct = (re.split(r"\d", text, 1)[0].strip() or None), None
        if not _once(seen, name.lower()):
            return
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



WANT = {
    "name": ["jugador"],
    "points": ["puntos", "pts"],
    "games": ["pj"],
    "avg": ["media", "med"],
}


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


_POINTS_ID_RE = re.compile(r"openPlayerPointsStats\(\s*(\d+)")


def parse_points(html: str, observed_at: str = "", key: str = "points") -> list[dict]:
    from ffcore.parse import ratio

    doc = lh.fromstring(html)
    best: list[dict] = []

    for table in doc.xpath("//table"):
        head = table.xpath(".//thead//tr")
        if not head:
            continue
        headers = [" ".join(_cell_texts(c))
                   for c in head[-1].xpath("./th|./td")]
        cols = {}
        for i, raw in enumerate(headers):
            low = raw.lower()
            for field, needles in WANT.items():
                if field in cols:
                    continue
                if any(n in low for n in needles):
                    cols[field] = i
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
            _pid = _POINTS_ID_RE.search(tr.get("onclick") or "")
            rows.append({
                "ff_id": _pid.group(1) if _pid else "",
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
    hits = re.findall(r"20\d{2}\s*/\s*(?:20)?\d{2}", html)
    if hits:
        return re.sub(r"\s*/\s*", "-", hits[0])
    return "unknown"



_WS = re.compile(r"\s+")

MARKET_SURFACE_RE = re.compile(
    r'data-(?:nombre|posicion|valor|diferencia1|diferencia-pct1|equipo)="[^"]*"')




_DEFS: dict[int, dict] = {}


def _defs(source: str) -> dict[str, tuple[str, set]]:
    import ast

    hit = _DEFS.get(hash(source))
    if hit is not None:
        return hit
    tree = ast.parse(source)
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
    out = {n: (t, refs & set(out)) for n, (t, refs) in out.items()}
    _DEFS[hash(source)] = out
    return out


def top_level(source: str) -> dict[str, str]:
    return {n: t for n, (t, _refs) in _defs(source).items()}


def parser_deps(source: str, name: str) -> set[str]:
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
    if not any(parts):
        return None
    return hashlib.sha1("\x1f".join(parts).encode("utf-8")).hexdigest()[:16]


def _sign_rows(text: str, parse, fmt, sort: bool = False) -> str | None:
    rows = parse(text)
    if not rows:
        return None
    parts = [fmt(r) for r in rows]
    return _digest(sorted(parts) if sort else parts)


def _surface(elements) -> list[str]:
    out: list[str] = []
    for el in elements:
        out.append(_WS.sub(" ", el.text_content()).strip())
        out += [a.get("href") or "" for a in _css(el, "a[href]")]
        out += [i.get("alt") or "" for i in _css(el, "img[alt]")]
    return out


def sign_market(html: str) -> str | None:
    return _digest(MARKET_SURFACE_RE.findall(html))


def _sign_elements(html: str, selectors) -> str | None:
    doc = lh.fromstring(html)
    els = []
    for sel in selectors:
        els += sel(doc) if callable(sel) else _css(doc, sel)
    return _digest(_surface(els))


def _suspension_sections(doc):
    return [s for s in _css(doc, "section.mod.sancionados")
           if "mercado-box" not in " ".join(s.classes)]


def sign_team(html: str) -> str | None:
    return _sign_elements(html, [*XI_SELECTORS, *FITNESS_SELECTORS,
                                 _suspension_sections])



AF_BASE = "https://www.analiticafantasy.com"
AF_SOURCE = "analitica"
AF_TEAM_URL = f"{AF_BASE}/equipo/{{slug}}"

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
AF_SPLIT_RE = re.compile(r"^(.+?)(\d+)\s*/\s*(\d+)\s+titular", re.S)
AF_FRACTION_RE = re.compile(r"\d+\s*/\s*\d+")


def parse_af_team(html: str, observed_at: str,
                  key: str = "af_test") -> list[dict]:
    slug = key[3:] if key.startswith("af_") else key
    doc = lh.fromstring(html)
    rows, seen = [], set()

    def add(name, img_src, role, start_pct, note):
        if not _once(seen, name.lower()):
            return
        m = AF_PHOTO_RE.search(img_src or "")
        rows.append({
            "observed_at": observed_at,
            "source": AF_SOURCE,
            "team_slug": slug,
            "player_name": name,
            "player_slug": m.group(1) if m else None,
            "role": role,
            "start_pct": start_pct,
            "status": "",
            "note": note,
        })

    def photo(li):
        img = _css(li, "img[src]")
        return img[0].get("src") if img else ""

    for li in _css(doc, AF_XI_SELECTOR):
        label = li.get("aria-label") or ""
        name = (label[len(AF_NAME_PREFIX):].strip()
                if label.startswith(AF_NAME_PREFIX) else "")
        add(name, photo(li), "starter", None, "titular")
    if rows:
        return rows

    for block in _css(doc, AF_CONSENSO_SELECTOR):
        for ul in _css(block, "ul"):
            section = None
            parent = ul.getparent()
            if parent is not None:
                ptext = _WS.sub(" ", parent.text_content()).strip()
                section = next((h for h in (AF_UNANIMOUS, AF_DIVIDED)
                               if ptext.startswith(h)), None)
            if section is None:
                continue
            for li in _css(ul, "li"):
                text = _WS.sub(" ", li.text_content()).strip()
                if section == AF_UNANIMOUS:
                    if AF_FRACTION_RE.search(text):
                        continue
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
    return _sign_elements(html, ['ul[aria-label^="Titulares"]',
                                 AF_CONSENSO_SELECTOR])



AF_HUB_URL = f"{AF_BASE}/la-liga/alineaciones-probables"
AF_MATCH_RE = re.compile(r"/partido/(\d+)")


def parse_af_fixtures(html: str, observed_at: str,
                      key: str = "af_fixtures") -> list[dict]:
    doc = lh.fromstring(html)
    rows, seen = [], set()
    for a in _css(doc, 'a[href*="/partido/"]'):
        m = AF_MATCH_RE.search(a.get("href") or "")
        times = _css(a, "time[datetime]")
        teams = [i.get("alt") for i in _css(a, "img[alt]") if i.get("alt")]
        ids = [i.get("data-af-team")
               for i in _css(a, "img[data-af-team]") if i.get("data-af-team")]
        if not (m and times and len(teams) >= 2) or not _once(seen, m.group(1)):
            continue
        rows.append({
            "observed_at": observed_at,
            "source": AF_SOURCE,
            "match_id": m.group(1),
            "kickoff": times[0].get("datetime"),
            "home": teams[0],
            "away": teams[1],
            "home_id": ids[0] if len(ids) > 1 else "",
            "away_id": ids[1] if len(ids) > 1 else "",
        })
    return rows


def _sign_links(html: str, href_substr: str) -> str | None:
    return _digest(_surface(_css(lh.fromstring(html),
                                 'a[href*="%s"]' % href_substr)))


def sign_af_fixtures(html: str) -> str | None:
    return _sign_links(html, "/partido/")


sign_points = partial(
    _sign_rows, parse=lambda t: parse_points(t, ""),
    fmt=lambda r: "%s|%s|%s" % (r["ff_id"], r["points"], r["games"]),
    sort=True)



CAL_KEY = "calendario"
FF_CAL_URL = f"{BASE}/laliga/calendario"
MATCH_URL = f"{BASE}/partidos/{{path}}"

MATCH_PATH_RE = re.compile(r"/partidos/(\d+-[a-z0-9-]+)")
MATCH_KEY_RE = re.compile(r"^match_(\d+-[a-z0-9-]+)$")
CAL_JORNADA_RE = re.compile(r"Jornada\s*(\d+)")
CAL_SCORE_RE = re.compile(r"\b(\d+\s*-\s*\d+)\b")

MATCH_SIDES = (".stats-local", ".stats-visitante")
MATCH_SUBS_HEADER = "Suplentes"
MATCH_MINUTE_RE = re.compile(r"\s*\d+\s*'\s*$")
MATCH_MINUTE_CAPTURE_RE = re.compile(r"(\d+)\s*'\s*$")
MATCH_ALIASES = {"rayo": "rayo-vallecano"}
XI_SIZE = 11


def _match_sides(slug: str) -> tuple[str, str] | None:
    known = {t: t for t in TEAMS}
    known.update(MATCH_ALIASES)
    for head in known:
        tail = slug[len(head) + 1:]
        if slug.startswith(head + "-") and tail in known:
            return known[head], known[tail]
    return None


def parse_calendar(html: str, observed_at: str,
                   key: str = "calendario") -> list[dict]:
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
    return _sign_links(html, "/partidos/")


def parse_starters(html: str, observed_at: str,
                   key: str = "match_1-alaves-getafe") -> list[dict]:
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
            a = _css(tr, 'a[href*="/jugadores/"]')
            if a and side_rows:
                side_rows[-1]["player_slug"] = _slug(
                    'href="%s"' % (a[0].get("href") or ""))
        if sum(1 for r in side_rows if r["role"] == "starter") != XI_SIZE:
            continue
        rows += side_rows
    return rows


def _xi_rows(doc, side: str) -> list:
    tables = _css(doc, "%s table.tablestats" % side)
    return _css(tables[0], "tbody tr") if tables else []


def sign_starters(html: str) -> str | None:
    return _sign_elements(html, [partial(_xi_rows, side=s) for s in MATCH_SIDES])


def _rebuild(key: str, pattern, table: str, parse, sign, url_for, **kw):
    m = pattern.match(key or "")
    if not m:
        return None
    return Source(key, table, url_for(m), parse, sign, **kw)


def match_source(key: str) -> Source | None:
    return _rebuild(key, MATCH_KEY_RE, "starters", parse_starters,
                    sign_starters, lambda m: MATCH_URL.format(path=m.group(1)),
                    cadence="once")


def played_sources(cal_html: str, observed_at: str = "") -> list[Source]:
    return [match_source("match_%s" % r["path"])
            for r in parse_calendar(cal_html, observed_at) if r["score"]]


ELO_SOURCE = "clubelo"
ELO_URL = "https://clubelo.com/ESP"
ELO_COUNTRY = "ESP"
ELO_LEVEL = "1"
ELO_COLS = ("Name", "Elo", "FedURL", "Level")
ELO_MARK = "var vegaJson ="


def parse_elo(text: str, observed_at: str, key: str = "elo") -> list[dict]:
    def elo_records(html: str) -> list[dict]:
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

    rows = []
    for rec in elo_records(text):
        if (str(rec["FedURL"]).strip() != ELO_COUNTRY
                or str(rec["Level"]).strip() != ELO_LEVEL):
            continue
        club = str(rec["Name"]).strip()
        try:
            rating = float(rec["Elo"])
        except (TypeError, ValueError):
            continue
        if club:
            rows.append({"observed_at": observed_at, "source": ELO_SOURCE,
                         "club": club, "elo": str(rating)})
    return rows


sign_elo = partial(_sign_rows, parse=lambda t: parse_elo(t, ""),
                   fmt=lambda r: "%s=%s" % (r["club"], r["elo"]))


FD_BASE = "https://www.football-data.co.uk"
FD_URL = FD_BASE + "/mmz4281/{season}/SP1.csv"
FD_SOURCE = "football-data"

FD_ALIASES = {
    "ath bilbao": "athletic", "ath madrid": "atletico",
    "espanol": "espanyol", "sociedad": "real-sociedad",
    "vallecano": "rayo-vallecano", "dep a coruna": "deportivo",
    "santander": "racing",
}

FD_SEASONS_BACK = 3

FD_FIELDS = ("FTHG", "FTAG", "HxG", "AxG", "HS", "AS", "HST", "AST",
            "HC", "AC")


def fd_season_code(now: datetime) -> str:
    y = now.year if now.month >= 7 else now.year - 1
    return "%02d%02d" % (y % 100, (y + 1) % 100)


def _season_start_year(now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    return now.year if now.month >= 7 else now.year - 1


def _season_suffix(key: str, prefix: str) -> str:
    m = re.match(r"^%s_(\d{4})$" % re.escape(prefix), key)
    return m.group(1) if m else ""


def fd_sources(now: datetime | None = None) -> list["Source"]:
    cur_y = _season_start_year(now)
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
    if not text:
        return []
    return list(csv.DictReader(io.StringIO(text.lstrip("﻿"))))


def _fd_match_team(side: str, teams) -> str | None:
    return match_one(side, teams)


def _fd_slug(name: str) -> str:
    hit = _fd_match_team(name, TEAMS)
    if hit:
        return hit
    slug = FD_ALIASES.get(norm(name) or "")
    return slug if slug in TEAMS else ""


def parse_fd_results(text: str, observed_at: str,
                     key: str = "fd_2526") -> list[dict]:
    season = _season_suffix(key, "fd")
    rows = []
    for r in _fd_rows(text):
        home, away = (r.get("HomeTeam") or "").strip(), \
                     (r.get("AwayTeam") or "").strip()
        if not home or not away:
            continue
        date = ""
        for fmt in ("%d/%m/%Y", "%d/%m/%y"):
            try:
                date = datetime.strptime(r.get("Date") or "", fmt
                                         ).strftime("%Y-%m-%d")
                break
            except ValueError:
                continue
        row = {
            "observed_at": observed_at, "source": FD_SOURCE,
            "season": season, "date": date,
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


sign_fd_results = partial(
    _sign_rows, parse=_fd_rows,
    fmt=lambda r: "%s|%s|%s|%s|%s" % (r.get("Date"), r.get("HomeTeam"),
                                      r.get("AwayTeam"), r.get("FTHG"),
                                      r.get("FTAG")))


ODDS_URL = ("https://api.the-odds-api.com/v4/sports/soccer_spain_la_liga"
           "/odds/?apiKey={odds_key}&regions=eu&markets=h2h,totals"
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
        # OVER/UNDER, the half this feed was not asking for until
        # 2026-09-17. h2h alone says who wins; it cannot say how many
        # goals, and a clean sheet is the single biggest driver of a
        # defender's or keeper's points. With p_home/p_draw/p_away and
        # P(over N) a consumer can solve for each side's expected goals
        # and read P(clean sheet) off them directly, instead of inferring
        # it from a goals-conceded rate the way the Elo path does.
        #
        # ONE LINE ONLY, the modal one across books (almost always 2.5):
        # medianing prices quoted at different lines would average
        # unrelated questions. Books that quote another line are skipped
        # rather than bent onto this one.
        #
        # OPTIONAL. Ten of fourteen books offered totals when this was
        # written; an event with h2h and no totals still emits its row
        # with these three fields blank, because losing the h2h data to
        # gain nothing would be the worse trade.
        line_prices: dict[float, dict[str, list[float]]] = {}
        for bk in ev.get("bookmakers") or []:
            tot = next((m for m in bk.get("markets") or []
                       if m.get("key") == "totals"), None)
            if tot is None:
                continue
            for o in tot.get("outcomes") or []:
                try:
                    pt = float(o.get("point"))
                    pr = float(o.get("price"))
                except (TypeError, ValueError):
                    continue
                side = (o.get("name") or "").strip().lower()
                if side not in ("over", "under"):
                    continue
                line_prices.setdefault(pt, {"over": [], "under": []})[side].append(pr)
        total_line = p_over = p_under = ""
        if line_prices:
            modal = max(line_prices,
                       key=lambda pt: len(line_prices[pt]["over"])
                       + len(line_prices[pt]["under"]))
            mo = _median(line_prices[modal]["over"])
            mu = _median(line_prices[modal]["under"])
            if mo and mu:
                io, iu = 1.0 / mo, 1.0 / mu
                tot_imp = io + iu
                if tot_imp > 0:
                    total_line = modal
                    p_over = io / tot_imp
                    p_under = iu / tot_imp

        rows.append({
            "observed_at": observed_at, "source": ODDS_SOURCE,
            "kickoff": (ev.get("commence_time") or "").strip(),
            "home_name": home_name, "away_name": away_name,
            "home": _fd_slug(home_name), "away": _fd_slug(away_name),
            "n_bookmakers": n_books,
            "p_home": implied["home"] / total,
            "p_draw": implied["draw"] / total,
            "p_away": implied["away"] / total,
            "total_line": total_line,
            "p_over": p_over,
            "p_under": p_under,
        })
    return rows


sign_odds = partial(
    _sign_rows, parse=lambda t: parse_odds(t, ""),
    fmt=lambda r: "%s|%s|%.3f|%.3f|%.3f" % (
        r["home"] or r["home_name"], r["away"] or r["away_name"],
        r["p_home"], r["p_draw"], r["p_away"]))


UNDERSTAT_URL = "https://understat.com/main/getPlayersStats/"
UNDERSTAT_SOURCE = "understat"
UNDERSTAT_LEAGUE = "La_liga"
UNDERSTAT_SEASONS_BACK = 1


def understat_sources(now: datetime | None = None) -> list["Source"]:
    cur_y = _season_start_year(now)
    out = []
    for back in range(UNDERSTAT_SEASONS_BACK + 1):
        y = cur_y - back
        out.append(Source(
            "understat_%d" % y, "understat_players", UNDERSTAT_URL,
            parse_understat_players, sign_understat_players,
            cadence="every_run" if back == 0 else "once",
            body={"league": UNDERSTAT_LEAGUE, "season": str(y)}))
    return out


def parse_understat_players(text: str, observed_at: str,
                            key: str = "understat_2026") -> list[dict]:
    season = _season_suffix(key, "understat")
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


def _understat_rows(text: str) -> list[dict]:
    try:
        data = json.loads(text or "")
    except (TypeError, ValueError):
        return []
    if not isinstance(data, dict) or not data.get("success"):
        return []
    players = data.get("players")
    return players if isinstance(players, list) else []


sign_understat_players = partial(
    _sign_rows, parse=_understat_rows,
    fmt=lambda p: "%s|%s|%s|%s|%s|%s" % (
        p.get("id"), p.get("games"), p.get("time"), p.get("goals"),
        p.get("assists"), p.get("xG")))


LFG_SOURCE = "laliga"
API_LEAGUES_KEY = "api_leagues"
API_LEAGUES_URL = "{base}/v1/competition/1/leagues?x-lang=es"
API_MARKET_URL = "{base}/v1/competition/1/league/{league}/market?x-lang=es"
API_ACTIVITY_URL = ("{base}/v1/competition/1/leagues/{league}"
                    "/activity/{page}?x-lang=es")
API_TEAMS_URL = "{base}/v1/competition/1/leagues/{league}/teams?x-lang=es"
API_LINEUP_URL = ("{base}/v1/competition/1/teams/{team}"
                  "/lineup/week/{week}?x-lang=es")
LINEUP_WEEK = 38

STORE_ONCE = {"api_activity": ("activity_id",),
              "api_players": ("player_id",),
              "api_stats": ("player_id", "week", "stat", "value", "points"),
              "results_history": ("season", "date", "home_name", "away_name",
                                  "home_goals", "away_goals")}

# Values here keep changing (price drifts, a status flips, a season total
# ticks up) so STORE_ONCE's keep-the-first-forever rule is wrong for them --
# but most scrape rounds still see no change, so writing a fresh row every
# round is 5-8x duplication. One row per (key, day) instead: a round that
# matches what is already on disk for today overwrites that day's row
# rather than appending a new one.
STORE_DAILY = {"market": ("ff_id",),
              "lineups": ("source", "team_slug", "player_slug"),
              "understat_players": ("source", "season", "understat_id")}

ROW_TABLE = "table"

ACT_JOINED, ACT_BUY, ACT_SELL = 9, 31, 33
ACT_BONUS, ACT_BONUS_ZERO = 6, 7

# A CLAUSE BUYOUT -- one manager taking another's player at his release
# clause, which the app announces as a "Market operation". It was scraped
# from the first day and thrown away for want of this line: parse_api_activity
# skips any activityTypeId it has no name for, and 1 was not on the list. Two
# of them had happened (Luismi Cruz, and Raphinha for 141,425,721 on
# 2026-09-18) and neither reached the ledger, so the buyer was never debited
# and the victim never credited -- which is why Albert Laporta appeared to be
# sitting on 141M he had in fact spent.
#
# An allowlist is still right: an unknown event should be dropped rather than
# guessed at. What was missing is anyone checking what it had dropped. An
# audit of every snapshot ever taken says type 1 is the only one, 2 events.
ACT_CLAUSE = 1

ACT_KIND = {ACT_JOINED: "joined", ACT_BUY: "buy", ACT_SELL: "sell",
           ACT_BONUS: "bonus", ACT_BONUS_ZERO: "bonus",
           ACT_CLAUSE: "clause"}


def _j(text: str):
    try:
        return json.loads(text or "")
    except (ValueError, TypeError):
        return None


def _j_dict(text: str) -> dict | None:
    d = _j(text)
    return d if isinstance(d, dict) else None


def _pm(item: dict) -> dict:
    return (item or {}).get("playerMaster") or {}


def _player_identity(pm: dict) -> dict:
    nick, name = pm.get("nickname") or "", pm.get("name") or ""
    return {
        "player_id": str(pm.get("id") or ""),
        "player_name": nick or name,
        "player_name_full": name if nick else "",
        "position_id": str(pm.get("positionId") or ""),
        "market_value": str(pm.get("marketValue") or ""),
    }


def _parse_json_list(text: str, observed_at: str, row_fn,
                     if_empty=None) -> list[dict]:
    """The shared skeleton behind every api_* JSON-list endpoint: parse,
    require a list, call row_fn(item) -> a list of field dicts (own fields
    only -- observed_at/source are stamped here), flatten, stamp. row_fn
    returning [] for an item drops it; `if_empty` (api_offer's one real use)
    supplies a placeholder row set when nothing survived at all."""
    d = _j(text)
    if not isinstance(d, list):
        return []
    rows = [{"observed_at": observed_at, "source": LFG_SOURCE, **row}
            for it in d for row in (row_fn(it) or ())]
    if not rows and if_empty is not None:
        rows = [{"observed_at": observed_at, "source": LFG_SOURCE, **row}
               for row in if_empty()]
    return rows


def parse_api_leagues(text: str, observed_at: str,
                      key: str = "api_leagues") -> list[dict]:
    def row(lg):
        if not lg.get("id"):
            return []
        t = lg.get("team") or {}
        return [{"league_id": str(lg["id"]), "league_name": lg.get("name") or "",
                "access": lg.get("access") or "",
                "managers": str(lg.get("managersNumber") or ""),
                "team_id": str(t.get("id") or ""),
                "money": str(t.get("money") or ""),
                "team_value": str(t.get("teamValue") or ""),
                "team_points": str(t.get("teamPoints") or "")}]
    return _parse_json_list(text, observed_at, row)


sign_api_leagues = partial(
    _sign_rows, parse=lambda t: parse_api_leagues(t, ""),
    fmt=lambda r: "%s=%s/%s" % (r["league_id"], r["money"], r["team_value"]))


def parse_api_market(text: str, observed_at: str,
                     key: str = "api_market") -> list[dict]:
    def row(it):
        pm = _pm(it)
        if not pm.get("id"):
            return []
        return [{
            ROW_TABLE: "api_market",
            "market_id": str(it.get("id") or ""),
            **_player_identity(pm),
            "sale_price": str(it.get("salePrice") or ""),
            "bids": next((str(it[k]) for k in ("numberOfBids", "numberOfOffers")
                          if it.get(k) is not None), ""),
            "seller": it.get("discr") or "",
            "status": it.get("status") or "",
            "player_status": pm.get("playerStatus") or "",
            "shielded": "" if (it.get("playerTeam") or {}).get("isShielded")
                              is None else
                        str((it["playerTeam"]["isShielded"])).lower(),
            "expires_at": it.get("expirationDate") or "",
            "bid_id": str((it.get("bid") or {}).get("id") or ""),
            "bid_money": str((it.get("bid") or {}).get("money") or ""),
            "bid_status": (it.get("bid") or {}).get("status") or "",
        }]
    return _parse_json_list(text, observed_at, row)


sign_api_market = partial(
    _sign_rows, parse=lambda t: parse_api_market(t, ""),
    fmt=lambda r: "%s@%s/%s/%s" % (r["player_id"], r["sale_price"],
                                   r["bids"], r["bid_status"]))


def parse_api_activity(text: str, observed_at: str,
                       key: str = "api_activity") -> list[dict]:
    # NOTHING IS DROPPED SILENTLY ANY MORE. An allowlist is still right -- an
    # event whose meaning is unknown must not be guessed at, and the ledger
    # below ignores any kind it does not recognise. What was wrong was that
    # the dropped ones left no trace, so a clause buyout was discarded from
    # the first day of the season and only surfaced when Miguel noticed a
    # raid missing from the report six weeks later. An unknown type is now
    # KEPT, named for what it is, and warned about, so the next one costs a
    # warning rather than a season of bad rival cash.
    def row(a):
        if not a.get("id"):
            return []
        kind = ACT_KIND.get(a.get("activityTypeId")) \
            or ("unknown:%s" % a.get("activityTypeId"))
        return [{
            "activity_id": str(a["id"]),
            "at": a.get("createdAt") or "",
            "kind": kind,
            "user_id": str(a.get("user1Id") or ""),
            # THE OTHER SIDE. Only a clause has one -- a buy is from the
            # market and a sell is to it -- and without it the money has a
            # payer but no payee.
            "counterparty": str(a.get("user2Id") or ""),
            "player_id": str(a.get("playerMasterId") or ""),
            "amount": str(a.get("amount") or ""),
            "week": str(a.get("weekNumber") or ""),
        }]
    return _parse_json_list(text, observed_at, row)


sign_api_activity = partial(_sign_rows, parse=lambda t: parse_api_activity(t, ""),
                            fmt=lambda r: r["activity_id"], sort=True)


def parse_api_teams(text: str, observed_at: str,
                    key: str = "api_teams") -> list[dict]:
    def row(t):
        out = []
        m = t.get("manager") or {}
        if t.get("id"):
            out.append({
                ROW_TABLE: "api_standings",
                "team_id": str(t["id"]),
                "user_id": str(m.get("id") or ""),
                "manager": m.get("managerName") or "",
                "position": str(t.get("position") or ""),
                "previous_position": str(t.get("previousPosition") or ""),
                "team_points": str(t.get("teamPoints") or ""),
                "fixture_points": str(t.get("fixturePoints") or ""),
                "team_value": str(t.get("teamValue") or ""),
                "team_money": str(t.get("teamMoney") or ""),
                "banned": "" if t.get("banned") is None
                          else str(t["banned"]).lower(),
                "starting_week": str(t.get("startingWeek") or ""),
            })
        for p in (t.get("players") or []):
            pm = _pm(p)
            if not pm.get("id"):
                continue
            out.append({
                ROW_TABLE: "api_teams",
                "team_id": str(t.get("id") or ""),
                "manager": m.get("managerName") or "",
                **_player_identity(pm),
                "points": str(pm.get("points") or ""),
                "buyout": str(p.get("buyoutClause") or ""),
                "buyout_until": str(p.get("buyoutClauseLockedEndTime") or ""),
                "player_status": pm.get("playerStatus") or "",
                "player_team_id": str(p.get("playerTeamId") or ""),
            })
            for line in (pm.get("lastStats") or []):
                week = line.get("weekNumber")
                for stat, pair in (line.get("stats") or {}).items():
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
    return _parse_json_list(text, observed_at, row)


def sign_api_teams(text: str) -> str | None:
    rows = parse_api_teams(text, "")
    squads = [r for r in rows if r[ROW_TABLE] == "api_teams"]
    table = [r for r in rows if r[ROW_TABLE] == "api_standings"]
    return _digest(["%s:%s" % (r["team_id"], r["player_id"]) for r in squads]
                   + ["$%s=%s/%s" % (r["team_id"], r["team_money"],
                                     r["position"]) for r in table])


LINEUP_SLOTS = {"goalkeeper": "POR", "defender": "DEF",
                "midfield": "MED", "striker": "DEL"}

LINEUP_WEEK_RE = re.compile(r"api_lineup_(\d+)$")


def parse_api_lineup(text: str, observed_at: str,
                     key: str = "api_lineup_1") -> list[dict]:
    d = _j_dict(text)
    if d is None:
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
                "snapshot_at": str(d.get("teamSnapshotTookOn") or ""),
                "points": str(d.get("points") if d.get("points") is not None
                              else ""),
            })
    return rows


def sign_api_lineup(text: str) -> str | None:
    rows = parse_api_lineup(text, "")
    return _digest(["%s:%s" % (r["slot"], r["player_id"]) for r in rows]
                   + ["=%s" % (rows[0]["formation"] if rows else "")])


API_PLAYER_URL = "{base}/v1/competition/1/player/{pid}?x-lang=es"
API_PLAYER_KEY_RE = re.compile(r"^api_player_(\d+)$")


def parse_api_player(text: str, observed_at: str,
                     key: str = "api_player_0") -> list[dict]:
    d = _j_dict(text)
    if d is None:
        return []
    m = API_PLAYER_KEY_RE.match(key or "")
    pid = m.group(1) if m else str(d.get("id") or "")
    if not pid:
        return []
    return [{
        "observed_at": observed_at, "source": LFG_SOURCE,
        "team_id": str(d.get("teamId") or ""),
        **_player_identity(d),
        "player_id": pid,
    }]


def sign_api_player(text: str) -> str | None:
    rows = parse_api_player(text, "")
    return _digest([rows[0]["player_name"]]) if rows else None


API_PLAYERS_ALL_URL = "{base}/v1/competition/1/players?x-lang=es"


def parse_api_players_all(text: str, observed_at: str,
                          key: str = "api_players_all") -> list[dict]:
    def row(p):
        if not p.get("id"):
            return []
        return [{"team_id": str(p.get("teamId") or ""), **_player_identity(p),
                "player_status": p.get("playerStatus") or ""}]
    return _parse_json_list(text, observed_at, row)


sign_api_players_all = partial(
    _sign_rows, parse=lambda t: parse_api_players_all(t, ""),
    fmt=lambda r: "%s@%s/%s" % (r["player_id"], r["player_status"],
                                r["market_value"]),
    sort=True)


def player_source(key: str) -> Source | None:
    return _rebuild(key, API_PLAYER_KEY_RE, "api_players", parse_api_player,
                    sign_api_player,
                    lambda m: API_PLAYER_URL.format(base="{base}",
                                                    pid=m.group(1)),
                    cadence="once", auth=True)


def player_sources(activity_json: str, observed_at: str = "") -> list[Source]:
    """Which players a detail page is worth fetching for, read off the feed.

    KNOWN KINDS ONLY. The parser keeps an event it has no name for rather
    than dropping it silently, which is right -- but "keep the record" and
    "go and fetch against it" are different decisions. A player id read out
    of an event whose meaning is unknown is a request against a guess, so
    this stays on the kinds whose shape is understood. A clause counts: its
    player really did change hands.
    """
    out, seen = [], set()
    for r in parse_api_activity(activity_json, observed_at):
        pid = r.get("player_id")
        if not pid or pid in seen or (r.get("kind") or "").startswith(
                "unknown:"):
            continue
        seen.add(pid)
        out.append(player_source("api_player_%s" % pid))
    return out


API_OFFER_URL = ("{base}/v1/competition/1/league/{league}/playerTeam/{ptid}"
                 "/offer?x-lang=es")
API_OFFER_KEY_RE = re.compile(r"^api_offer_(\d+)$")


def parse_api_offer(text: str, observed_at: str,
                    key: str = "api_offer_0") -> list[dict]:
    m = API_OFFER_KEY_RE.match(key or "")
    ptid = m.group(1) if m else ""
    if not ptid:
        return []

    def row(it):
        if not it.get("id"):
            return []
        return [{
            "player_team_id": ptid, "offer_id": str(it["id"]),
            "money": str(it.get("money") or ""), "status": it.get("status") or "",
            "created_at": it.get("createdAt") or "",
            "expires_at": it.get("expirationDate") or "",
            "from_market": "" if it.get("isFromMarket") is None else
                           str(it["isFromMarket"]).lower(),
        }]

    def empty():
        return [{"player_team_id": ptid, "offer_id": "", "money": "",
                "status": "", "created_at": "", "expires_at": "",
                "from_market": ""}]
    return _parse_json_list(text, observed_at, row, if_empty=empty)


sign_api_offer = partial(
    _sign_rows, parse=lambda t: parse_api_offer(t, "", key="api_offer_0"),
    fmt=lambda r: "%s@%s/%s" % (r["offer_id"], r["money"], r["status"]))


def offer_source(key: str) -> Source | None:
    return _rebuild(key, API_OFFER_KEY_RE, "api_offers", parse_api_offer,
                    sign_api_offer, lambda m: API_OFFER_URL, auth=True)


def offer_sources(teams_json: str, me: str, league: str,
                  observed_at: str = "") -> list[Source]:
    out, seen = [], set()
    for r in parse_api_teams(teams_json, observed_at):
        if r.get(ROW_TABLE) != "api_teams" or r.get("manager") != me:
            continue
        ptid = r.get("player_team_id")
        if not _once(seen, ptid):
            continue
        out.append(Source(
            "api_offer_%s" % ptid, "api_offers",
            API_OFFER_URL.format(base="{base}", league=league, ptid=ptid),
            parse_api_offer, sign_api_offer, auth=True))
    return out


def api_source(key: str) -> Source | None:
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
    out = []
    for r in parse_api_leagues(leagues_json, observed_at):
        lg = r["league_id"]
        out.append(Source("api_market", "api_market",
                          API_MARKET_URL.format(base="{base}", league=lg),
                          parse_api_market, sign_api_market, auth=True))
        out.append(Source("api_teams", "api_teams",
                          API_TEAMS_URL.format(base="{base}", league=lg),
                          parse_api_teams, sign_api_teams, auth=True))
        if r.get("team_id"):
            out.append(Source(
                "api_lineup_%d" % LINEUP_WEEK, "api_lineup",
                API_LINEUP_URL.format(base="{base}", team=r["team_id"],
                                      week=LINEUP_WEEK),
                parse_api_lineup, sign_api_lineup, auth=True))
        for page in (0, 1):
            out.append(Source(
                "api_activity_%d" % page, "api_activity",
                API_ACTIVITY_URL.format(base="{base}", league=lg, page=page),
                parse_api_activity, sign_api_activity, auth=True))
    return out



class Source(NamedTuple):
    key: str
    table: str
    url: str
    parse: Callable
    sign: Callable
    cadence: str = "every_run"
    body: dict | None = None
    timeout: float | None = None
    enabled: bool = True
    auth: bool = False


@lru_cache(maxsize=None)
def sources(enabled_only: bool = True) -> list[Source]:
    out = [
        Source("market", "market", MARKET_URL, parse_market, sign_market),
        Source("points", "points", POINTS_URL, parse_points, sign_points),
    ]
    out += [Source(f"team_{s}", "lineups", TEAM_URL.format(slug=s),
                   parse_team, sign_team, cadence="twice_daily")
            for s in TEAMS]
    out += [Source(f"af_{s}", "lineups", AF_TEAM_URL.format(slug=af),
                   parse_af_team, sign_af_team, cadence="twice_daily")
            for s, af in sorted(AF_TEAMS.items())]
    out += [Source("af_fixtures", "fixtures", AF_HUB_URL,
                   parse_af_fixtures, sign_af_fixtures, cadence="daily")]
    out += [Source("elo", "elo", ELO_URL, parse_elo, sign_elo,
                   cadence="daily", timeout=8.0)]
    out += fd_sources()
    out += understat_sources()
    out += [Source("odds", "odds", ODDS_URL, parse_odds, sign_odds,
                   cadence="daily", timeout=15.0)]
    out += [Source(CAL_KEY, "matches", FF_CAL_URL, parse_calendar,
                   sign_calendar, cadence="daily")]
    out += [Source(API_LEAGUES_KEY, "api_leagues", API_LEAGUES_URL,
                   parse_api_leagues, sign_api_leagues, auth=True)]
    out += [Source("api_players_all", "api_players_all", API_PLAYERS_ALL_URL,
                   parse_api_players_all, sign_api_players_all,
                   cadence="daily", auth=True)]
    return [s for s in out if s.enabled or not enabled_only]


def source_for(key: str) -> Source | None:
    for s in sources(enabled_only=False):
        if s.key == key:
            return s
    return (match_source(key) or api_source(key) or player_source(key)
            or offer_source(key))



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
  "user1Id":3480702,"createdAt":"2026-09-01T04:34:11+02:00"},
 {"id":"a7","activityTypeId":1,"amount":141425721,"playerMasterId":2522,
  "user1Id":3480702,"user2Id":11877808,
  "createdAt":"2026-09-18T22:25:51+02:00"}]"""

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

_API_OFFER_FIXTURE = """[
 {"id":"49892747","money":6795815,"status":"pending",
  "createdAt":"2026-08-24T22:24:30+02:00","updatedAt":"2026-08-24T22:24:30+02:00",
  "isFromMarket":true,"expirationDate":"2026-08-25T22:24:00+02:00"}]"""


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
    rows = parse_team(_FIXTURE, "2026-01-01T0000Z", "team_test")
    by = {r["player_name"]: r for r in rows}

    assert by["Joan Garcia"]["status"] == "ok", by["Joan Garcia"]
    assert by["Joan Garcia"]["role"] == "starter"

    assert by["Pedri"]["status"] == "doubt", by["Pedri"]

    assert by["Eric García"]["status"] == "suspended", by["Eric García"]

    fdj = by["Frenkie de Jong"]
    assert fdj["status"] == "injured", fdj
    assert "rodilla" in fdj["note"] and "octubre" in fdj["note"], fdj

    assert by["Facundo Garcés"]["status"] == "unavailable"
    assert by["Facundo Garcés"]["role"] == "absent"

    assert by["Owen Bosch"]["status"] == "doubt"
    assert by["Owen Bosch"]["role"] == "absent"
    assert by["Owen Bosch"]["start_pct"] is None

    assert {r["status"] for r in rows} == {
        "ok", "doubt", "suspended", "injured", "unavailable"}

    assert by["Pedri"]["player_slug"] == "pedri", by["Pedri"]
    assert all(r["player_slug"] for r in rows), \
        [r for r in rows if not r["player_slug"]]

    assert SEVERITY.index("injured") < SEVERITY.index("doubt")

    assert norm("Eric García") == norm("Eric Garcia") == "eric garcia"
    assert norm("N'Diaye") == "ndiaye"

    assert all(r["team_slug"] == "test" for r in rows)
    assert all(r["source"] == SOURCE for r in rows)
    assert {r["source"] for r in rows if r["role"] == "absent"} == {SOURCE}

    m = parse_market(_MARKET_FIXTURE, "2026-01-01T0000Z")
    assert len(m) == 1, m
    assert m[0]["name"] == "Pedri" and m[0]["value"] == 21500000
    assert m[0]["team"] == "Barcelona", m[0]
    assert m[0]["position"] == "mediocampista"
    assert "slug" not in m[0] and "player_path" not in m[0], m[0]
    assert m[0]["ff_id"] == "1234", m[0]

    p = parse_points(_POINTS_FIXTURE)
    assert len(p) == 1, p
    assert p[0]["player_name"] == "R. de Galarreta"
    assert p[0]["player_name_full"] == "Íñigo Ruiz de Galarreta"
    assert p[0]["points"] == "156" and p[0]["games"] == "34"
    assert p[0]["avg"] == "4.590", p[0]
    assert p[0]["ff_id"] == "4242", p[0]
    assert parse_points(_POINTS_FIXTURE.replace(
        "openPlayerPointsStats(4242,", "somethingElse("))[0]["ff_id"] == ""
    assert season_label(_POINTS_FIXTURE) == "2025-26"
    assert season_label("<html>nothing</html>") == "unknown"

    assert sign_team(_FIXTURE) == sign_team(_FIXTURE)
    assert sign_market(_MARKET_FIXTURE) and sign_points(_POINTS_FIXTURE)

    moved = _FIXTURE.replace('class="jugadores-titulares"',
                             'class="jugadores-titulares" '
                             'data-posicionalternativa1-x="52%"')
    assert sign_team(moved) == sign_team(_FIXTURE)

    for before, after in [("Pedri 70%", "Pedri 60%"),
                          ("Owen Bosch", "Owen Bosche"),
                          ('alt="Duda"', 'alt="Lesionado"'),
                          ("/jugadores/pedri", "/jugadores/pedri-gonzalez")]:
        assert sign_team(_FIXTURE.replace(before, after)) != sign_team(_FIXTURE), \
            before

    assert sign_team("<html><body><p>nothing here</p></body></html>") is None
    assert sign_market("<html><body>no players</body></html>") is None
    assert sign_points("<html><body>no table</body></html>") is None

    af = parse_af_team(_AF_FIXTURE, "2026-01-01T0000Z", "af_test")
    assert [r["player_name"] for r in af] == ["Sivera", "Aitor Mañas",
                                              "Sin Foto"], af
    assert all(r["source"] == AF_SOURCE for r in af)
    assert all(r["team_slug"] == "test" for r in af)
    assert all(r["role"] == "starter" for r in af)
    assert af[0]["player_name"] == "Sivera" and af[0]["player_slug"] == "47353"
    assert all(r["start_pct"] is None and r["status"] == "" for r in af)
    assert af[2]["player_slug"] is None
    assert "No Deberia" not in {r["player_name"] for r in af}
    assert list(af[0]) == list(rows[0]), (list(af[0]), list(rows[0]))
    assert af[0]["note"] == "titular"
    assert sign_af_team(_AF_FIXTURE) is not None
    assert sign_af_team("<html><body>no lineup</body></html>") is None

    con = parse_af_team(_AF_CONSENSO_FIXTURE, "2026-01-01T0000Z", "af_test")
    byc = {r["player_name"]: r for r in con}
    assert set(byc) == {"Unai Simón", "Yuri", "Aitor Paredes",
                        "Robert Navarro"}, sorted(byc)
    assert byc["Unai Simón"]["start_pct"] == 100.0
    assert byc["Unai Simón"]["note"] == "consenso unánime"
    assert byc["Unai Simón"]["role"] == "starter"
    assert byc["Aitor Paredes"]["start_pct"] == 66.7, byc["Aitor Paredes"]
    assert byc["Aitor Paredes"]["note"] == "consenso 2/3"
    assert byc["Robert Navarro"]["start_pct"] == 33.3
    assert byc["Robert Navarro"]["role"] == "doubt"
    assert "Nico Williams" not in byc, con
    assert not any(c.isdigit() for r in con for c in r["player_name"]), con
    assert all(r["status"] == "" for r in con)
    assert list(con[0]) == list(rows[0])
    assert parse_af_team("<html><body>new design</body></html>", "t") == []
    assert sign_af_team(_AF_CONSENSO_FIXTURE) is not None

    fx = parse_af_fixtures(_AF_HUB_FIXTURE, "2026-01-01T0000Z")
    assert len(fx) == 1, fx
    assert fx[0]["match_id"] == "100011934"
    assert fx[0]["home"] == "Sevilla" and fx[0]["away"] == "Rayo Vallecano"
    assert fx[0]["home_id"] == "536" and fx[0]["away_id"] == "728", fx[0]
    assert fx[0]["kickoff"] == "2026-08-15T19:30:00+00:00", fx[0]
    assert fx[0]["source"] == AF_SOURCE
    assert sign_af_fixtures(_AF_HUB_FIXTURE) is not None
    assert sign_af_fixtures("<html><body>no matches</body></html>") is None

    sample = "\n".join([
        "A = 1", "B = 2",
        "def helper(x):", "    return x + A",
        "def one(t):", "    return helper(t)",
        "def two(t):", "    return B",
    ])
    assert parser_deps(sample, "one") == {"one", "helper", "A"}, \
        parser_deps(sample, "one")
    assert parser_deps(sample, "two") == {"two", "B"}
    assert parser_deps(sample, "helper") == {"helper", "A"}
    assert parser_deps(sample, "missing") == set()
    edited = sample.replace("return x + A", "return x - A")
    assert parser_sig("one", edited) != parser_sig("one", sample)
    assert parser_sig("two", edited) == parser_sig("two", sample)
    assert parser_sig("one", sample.replace("B = 2", "B = 3")) \
        == parser_sig("one", sample)
    assert parser_sig("one", sample) != parser_sig("two", sample)
    rec = "def loops(x):\n    return loops(x)"
    assert parser_deps(rec, "loops") == {"loops"}
    assert parser_sig("missing", sample) != parser_sig("missing", edited)

    whole = parser_sig("no such function at all")
    for src_ in sources():
        assert parser_sig(src_.parse.__name__) != whole, src_.key

    el = parse_elo(_ELO_FIXTURE, "2026-01-01T0000Z", "elo")
    assert [r["club"] for r in el] == ["Barcelona", "Real Madrid",
                                       "Elche"], el
    assert el[0]["elo"] == "2043.1" and el[0]["source"] == ELO_SOURCE, el[0]
    assert not any(r["club"] in ("Bayern", "Zaragoza") for r in el), el
    assert parse_elo(_ELO_FIXTURE.replace("2043.1", "1980.0455939177232"),
                     "t")[0]["elo"] == "1980.0455939177232"
    assert parse_elo(_ELO_FIXTURE.replace('"Elo":', '"Rating":'), "t") == []
    assert parse_elo(_ELO_FIXTURE.replace('"FedURL":', '"Fed":'), "t") == []
    assert [r["club"] for r in
            parse_elo(_ELO_FIXTURE.replace("2043.1", '"n/a"'), "t")] \
        == ["Real Madrid", "Elche"]
    assert parse_elo("", "t") == []
    assert parse_elo("<html><body>no chart here</body></html>", "t") == []
    assert parse_elo("<script>var vegaJson = {not json;</script>", "t") == []
    assert sign_elo(_ELO_FIXTURE) is not None
    assert sign_elo(_ELO_FIXTURE) == sign_elo(
        _ELO_FIXTURE.replace("#A4234B", "#123456"))
    assert sign_elo(_ELO_FIXTURE.replace("2043.1", "2050.0")) \
        != sign_elo(_ELO_FIXTURE)
    assert sign_elo("nothing like the page") is None
    assert ELO_URL.format(date="2026-08-16") == ELO_URL
    assert MARKET_URL.format(date="2026-08-16") == MARKET_URL

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
    assert cur[0]["home"] == "alaves" and cur[0]["away"] == "getafe"
    assert cur[0]["home_goals"] == "3" and cur[0]["home_xg"] == "1.93"
    assert cur[1]["home"] == "sevilla" and cur[1]["away"] == "rayo-vallecano"

    old = parse_fd_results(_FD_OLD, "2026-08-20T1200Z", "fd_2223")
    assert len(old) == 1, old
    assert old[0]["home_xg"] == "" and old[0]["away_xg"] == ""
    assert old[0]["home"] == "athletic"
    assert old[0]["away"] == "", old[0]
    assert old[0]["away_name"] == "Girona"

    assert parse_fd_results("", "t", "fd_2627") == []
    assert parse_fd_results("not a csv file at all", "t", "fd_2627") == []
    assert parse_fd_results("﻿Div,Date,HomeTeam,AwayTeam,FTHG,FTAG\n"
                            "SP1,17/08/22,,,,\n", "t", "fd_2223") == []

    assert sign_fd_results(_FD_CUR) is not None
    assert sign_fd_results("") is None
    assert sign_fd_results(_FD_CUR) == sign_fd_results(
        _FD_CUR.replace("18,6,8,2", "99,6,8,2"))
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
    assert abs(o["p_home"] - 0.393) < 0.005, o
    assert abs(o["p_away"] - 0.289) < 0.005, o
    assert abs(o["p_draw"] - 0.318) < 0.005, o
    assert abs(o["p_home"] + o["p_away"] + o["p_draw"] - 1.0) < 1e-9, o

    assert parse_odds("", "t") == []
    assert parse_odds("not json", "t") == []
    assert parse_odds(json.dumps([{"home_team": "Espanyol",
                                   "away_team": "Sevilla",
                                   "bookmakers": [{"key": "x",
                                                   "markets": []}]}]),
                      "t") == []
    unresolved = parse_odds(json.dumps([{
        "home_team": "Espanyol", "away_team": "Not A Real Club FC",
        "bookmakers": [{"key": "x", "markets": [{"key": "h2h",
                        "outcomes": [{"name": "Espanyol", "price": 2.0},
                                    {"name": "Not A Real Club FC",
                                     "price": 2.0},
                                    {"name": "Draw", "price": 3.0}]}]}]}]),
        "t")
    assert len(unresolved) == 1 and unresolved[0]["away"] == "", unresolved

    # -- totals: the over/under half, added 2026-09-17 ------------------
    tot = parse_odds(json.dumps([{
        "home_team": "Espanyol", "away_team": "Elche",
        "bookmakers": [
            {"key": "a", "markets": [
                {"key": "h2h", "outcomes": [
                    {"name": "Espanyol", "price": 2.0},
                    {"name": "Elche", "price": 4.0},
                    {"name": "Draw", "price": 3.5}]},
                {"key": "totals", "outcomes": [
                    {"name": "Over", "price": 2.0, "point": 2.5},
                    {"name": "Under", "price": 2.0, "point": 2.5}]}]},
            # a book quoting a DIFFERENT line is skipped, not averaged in
            {"key": "b", "markets": [
                {"key": "h2h", "outcomes": [
                    {"name": "Espanyol", "price": 2.0},
                    {"name": "Elche", "price": 4.0},
                    {"name": "Draw", "price": 3.5}]},
                {"key": "totals", "outcomes": [
                    {"name": "Over", "price": 9.0, "point": 3.5},
                    {"name": "Under", "price": 1.05, "point": 3.5}]}]},
        ]}]), "t")
    assert len(tot) == 1, tot
    assert tot[0]["total_line"] == 2.5, tot[0]
    # 2.0/2.0 is an even market: both sides 0.5 once the overround is out,
    # and the 3.5 book's lopsided prices must not drag it.
    assert abs(tot[0]["p_over"] - 0.5) < 1e-9, tot[0]
    assert abs(tot[0]["p_under"] - 0.5) < 1e-9, tot[0]

    # h2h with NO totals still yields its row, with the fields blank --
    # losing the h2h data to gain nothing would be the worse trade.
    notot = parse_odds(json.dumps([{
        "home_team": "Espanyol", "away_team": "Elche",
        "bookmakers": [{"key": "a", "markets": [{"key": "h2h", "outcomes": [
            {"name": "Espanyol", "price": 2.0},
            {"name": "Elche", "price": 4.0},
            {"name": "Draw", "price": 3.5}]}]}]}]), "t")
    assert len(notot) == 1 and notot[0]["p_over"] == "", notot
    assert notot[0]["p_home"] > 0, notot

    assert sign_odds(_ODDS_LIVE) is not None
    assert sign_odds("") is None
    assert sign_odds(_ODDS_LIVE) == sign_odds(
        _ODDS_LIVE.replace("2026-09-06T19:21:00Z", "2026-09-06T19:45:00Z"))
    assert sign_odds(_ODDS_LIVE) != sign_odds(
        _ODDS_LIVE.replace('"price": 2.3', '"price": 4.5'))

    assert source_for("odds").parse is parse_odds
    assert source_for("odds").table == "odds"
    assert source_for("odds").cadence == "daily"

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
    assert past[0]["team"] == "real-madrid", past[0]
    assert past[0]["games"] == "31" and past[0]["minutes"] == "2623"
    assert past[0]["xg"] == "25.796528611332178"
    assert past[0]["xa"] == "7.240631651133299"

    live = parse_understat_players(_UNDERSTAT_LIVE, "2026-08-21T0522Z",
                                   "understat_2026")
    assert live[0]["player_name"] == "Roberto Fernández"
    assert live[0]["team"] == "espanyol" and live[0]["season"] == "2026"

    assert parse_understat_players('{"error": {"error_code": 4}}', "t") == []
    assert parse_understat_players("", "t") == []
    assert parse_understat_players("not json at all", "t") == []
    assert parse_understat_players(
        '{"success": true, "players": [{"id": "", "player_name": "X"}, '
        '{"id": "1", "player_name": ""}]}', "t") == []

    assert sign_understat_players(_UNDERSTAT_PAST) is not None
    assert sign_understat_players("") is None
    assert sign_understat_players('{"success": true, "players": []}') is None
    assert sign_understat_players(_UNDERSTAT_PAST) == sign_understat_players(
        _UNDERSTAT_PAST.replace('"shots": "146"', '"shots": "147"'))
    assert sign_understat_players(_UNDERSTAT_PAST) != sign_understat_players(
        _UNDERSTAT_PAST.replace('"goals": "25"', '"goals": "26"'))

    us = understat_sources(datetime(2026, 8, 20, tzinfo=timezone.utc))
    assert [s.key for s in us] == ["understat_2026", "understat_2025"]
    assert us[0].cadence == "every_run" and us[1].cadence == "once"
    assert all(s.table == "understat_players" for s in us)
    assert all(s.url == UNDERSTAT_URL for s in us)
    assert us[0].body == {"league": "La_liga", "season": "2026"}
    assert us[1].body == {"league": "La_liga", "season": "2025"}
    assert all(s.body is None for s in sources() if s.key not in
              ("understat_2026", "understat_2025"))

    lg = parse_api_leagues(_API_LEAGUES_FIXTURE, "2026-01-01T0000Z")
    assert len(lg) == 1 and lg[0]["league_id"] == "017998544", lg
    assert lg[0]["team_id"] == "38091967" and lg[0]["money"] == "23596582", lg
    assert lg[0]["source"] == LFG_SOURCE
    assert parse_api_leagues("<html>maintenance</html>", "t") == []
    assert sign_api_leagues("<html>") is None

    mk = parse_api_market(_API_MARKET_FIXTURE, "t")
    assert len(mk) == 2, mk
    assert mk[0]["bids"] == "1" and mk[0]["seller"] == "marketPlayerLeague"
    assert mk[1]["bids"] == "3" and mk[1]["seller"] == "marketPlayerTeam", mk[1]
    assert parse_api_market(
        _API_MARKET_FIXTURE.replace('"numberOfOffers":3,', ""), "t")[1]["bids"] == ""
    assert mk[1]["shielded"] == "true" and mk[0]["shielded"] == "", mk[1]
    assert mk[0]["player_status"] == "ok" and mk[1]["player_status"] == ""
    assert mk[0]["player_name"] == "Simeone", mk[0]
    assert mk[0]["player_name_full"] == "Giuliano Simeone", mk[0]
    assert mk[1]["player_name_full"] == "", mk[1]
    assert sign_api_market(_API_MARKET_FIXTURE) == sign_api_market(
        _API_MARKET_FIXTURE.replace("2026-08-18T22", "2026-08-20T22"))
    assert sign_api_market(_API_MARKET_FIXTURE) != sign_api_market(
        _API_MARKET_FIXTURE.replace("5552694,\"numberOfBids\":1",
                                    "5552694,\"numberOfBids\":3"))
    assert mk[0]["bid_id"] == "b1" and mk[0]["bid_money"] == "5600000"
    assert mk[0]["bid_status"] == "pending"
    assert mk[1]["bid_id"] == "" and mk[1]["bid_money"] == ""
    assert sign_api_market(_API_MARKET_FIXTURE) != sign_api_market(
        _API_MARKET_FIXTURE.replace('"status":"pending"',
                                    '"status":"accepted"'))

    ac = parse_api_activity(_API_ACTIVITY_FIXTURE, "t")
    # EVERY EVENT SURVIVES THE PARSE, including one this code has no name
    # for. a4 is activityTypeId 77, which means nothing here; it used to be
    # dropped without trace, and that silence is what hid the clause buyout
    # (type 1) for a whole season -- scraped from day one, discarded every
    # run, and only noticed when a raid went missing from the report. The
    # ledger still refuses to act on a kind it does not understand; it just
    # cannot pretend the event never happened.
    assert ([r["kind"] for r in ac] ==
            ["buy", "sell", "joined", "unknown:77", "bonus", "bonus",
             "clause"]), ac
    assert ac[0]["amount"] == "58220110" and ac[0]["user_id"] == "11881989"
    assert ac[4]["week"] == "2" and ac[4]["amount"] == "2200000", ac[4]
    assert ac[5]["week"] == "3" and ac[5]["amount"] == "", ac[5]

    # A CLAUSE CARRIES BOTH SIDES. user2Id is the only place the payee
    # appears, and without it the money has a payer and no recipient.
    clause = ac[6]
    assert clause["kind"] == "clause", clause
    assert clause["user_id"] == "3480702", clause
    assert clause["counterparty"] == "11877808", clause
    assert clause["amount"] == "141425721", clause
    assert clause["player_id"] == "2522", clause
    # Nothing else has a counterparty: a buy is from the market, a sell to it.
    assert all(r["counterparty"] == "" for r in ac if r["kind"] != "clause")

    # A NEW EVENT MUST CHANGE THE SIGNATURE, or its page would be served
    # from the parse cache and the event never reach the table. It does,
    # because the signature is the set of activity ids and a new event
    # carries a new one. NOT COVERED, and worth knowing: an event whose
    # TYPE the app later corrected in place, keeping its id, would be
    # served stale -- accepted, because ids here are per-event and the app
    # has never rewritten one.
    import json as _aj
    _one_more = _aj.dumps(_aj.loads(_API_ACTIVITY_FIXTURE) + [
        {"id": "a8", "activityTypeId": 1, "amount": 5, "playerMasterId": 9,
         "user1Id": 1, "user2Id": 2,
         "createdAt": "2026-09-19T10:00:00+02:00"}])
    assert len(parse_api_activity(_one_more, "t")) == len(ac) + 1
    assert sign_api_activity(_one_more) != sign_api_activity(
        _API_ACTIVITY_FIXTURE)
    assert sign_api_activity(_API_ACTIVITY_FIXTURE) is not None
    import json as _json
    _rev = _json.dumps(list(reversed(_json.loads(_API_ACTIVITY_FIXTURE))))
    assert sign_api_activity(_rev) == sign_api_activity(_API_ACTIVITY_FIXTURE)

    all_rows = parse_api_teams(_API_TEAMS_FIXTURE, "t")
    assert {r[ROW_TABLE] for r in all_rows} == {"api_teams", "api_stats",
                                            "api_standings"}
    tm = [r for r in all_rows if r[ROW_TABLE] == "api_teams"]
    assert len(tm) == 2, tm
    assert tm[0]["manager"] == "miguel_autentico"
    assert tm[0]["buyout"] == "47000000", tm[0]
    assert tm[0]["buyout_until"] == "2026-08-25T14:07:38+02:00", tm[0]
    assert tm[1]["buyout_until"] == ""
    assert tm[1]["manager"] == "BurtonGM89" and tm[1]["buyout"] == ""
    assert tm[0]["player_name"] == "Fornals", tm[0]
    assert tm[0]["player_name_full"] == "Pablo Fornals Malla", tm[0]
    assert tm[1]["player_name_full"] == "Giuliano Simeone", tm[1]
    assert tm[0]["player_team_id"] == "24338726", tm[0]
    assert tm[1]["player_team_id"] == "", tm[1]

    sd = [r for r in all_rows if r[ROW_TABLE] == "api_standings"]
    assert len(sd) == 2, sd
    assert sd[0]["manager"] == "miguel_autentico" and sd[0]["position"] == "3"
    assert sd[0]["team_points"] == "17" and sd[0]["team_money"] == "23596582"
    assert sd[0]["user_id"] == "11881989" and sd[0]["team_id"] == "38091967"
    assert sd[0]["previous_position"] == "5", sd[0]
    assert sd[0]["team_value"] == "236374060" and sd[0]["fixture_points"] == "17"
    assert sd[0]["banned"] == "false" and sd[0]["starting_week"] == "1"
    assert sd[1]["team_money"] == "" and sd[1]["manager"] == "BurtonGM89"
    lonely = parse_api_teams(
        '[{"id":"9","position":5,"manager":{"id":"7","managerName":"Empty"},'
        '"players":[]}]', "t")
    assert [r[ROW_TABLE] for r in lonely] == ["api_standings"], lonely

    assert "team_points" not in tm[0] and "team_money" not in tm[0], tm[0]
    assert "position" not in tm[0] and "user_id" not in tm[0], tm[0]
    assert tm[0]["manager"] == "miguel_autentico" and tm[0]["team_id"]

    assert tm[0]["player_status"] == "doubt", tm[0]
    assert tm[1]["player_status"] == "", tm[1]
    assert "offers" not in tm[0] and "listed_until" not in tm[0], tm[0]

    st = [r for r in all_rows if r[ROW_TABLE] == "api_stats"]
    assert len(st) == 4, st
    goals = next(r for r in st if r["stat"] == "goals")
    assert goals["player_id"] == "1337" and goals["week"] == "1", goals
    assert goals["value"] == "1" and goals["points"] == "4", goals
    assert next(r for r in st if r["stat"] == "yellow_card")["points"] == "-1"
    assert sum(int(r["points"]) for r in st) == 5
    assert len(tm) == 2 and "stat" not in tm[0], tm[0]
    assert not any(r["player_id"] == "2621" for r in st), st
    assert sign_api_teams(_API_TEAMS_FIXTURE) == sign_api_teams(
        _API_TEAMS_FIXTURE.replace('"goals":[1,4]', '"goals":[1,9]'))

    disc = league_sources(_API_LEAGUES_FIXTURE)
    assert [s.key for s in disc] == ["api_market", "api_teams",
                                     "api_lineup_%d" % LINEUP_WEEK,
                                     "api_activity_0", "api_activity_1"], disc
    assert "/teams/38091967/lineup/" in next(
        s.url for s in disc if s.table == "api_lineup")
    assert all(s.auth for s in disc), "every API entry needs the bearer"
    elo = next(s for s in sources() if s.key == "elo")
    assert elo.timeout == 8.0, elo.timeout
    assert all(s.cadence == "every_run" for s in disc if s.key == "api_teams")
    assert "017998544" in disc[0].url and "{base}" in disc[0].url
    assert league_sources("<html>") == []
    for k in ("api_market", "api_teams", "api_activity_0", "api_activity_1"):
        assert source_for(k) is not None, k
        assert source_for(k).table == ("api_activity"
                                       if "activity" in k else k), k
    assert source_for("api_market").parse is parse_api_market
    assert api_source("market") is None

    pl = parse_api_player(_API_PLAYER_FIXTURE, "t", "api_player_1191")
    assert len(pl) == 1, pl
    assert pl[0]["player_id"] == "1191", pl
    assert pl[0]["player_name"] == "Hugo Duro", pl
    assert pl[0]["player_name_full"] == "Hugo Duro Perales", pl
    assert pl[0]["market_value"] == "8534068", pl
    assert parse_api_player("<html>", "t", "api_player_1") == []

    assert parse_api_player('{"nickname":"X"}', "t",
                            "api_player_777")[0]["player_id"] == "777"

    # A player's FIRST activity row having an unknown kind must not block a
    # LATER, real one for the same player -- "seen" may only be marked once
    # the unknown-kind skip has already been decided, not before. Caught by
    # hand, not by this suite, when a refactor moved the mark earlier.
    unk_then_known = json.dumps([
        {"id": "1", "activityTypeId": 9999, "playerMasterId": "42"},
        {"id": "2", "activityTypeId": list(ACT_KIND)[0], "playerMasterId": "42"},
    ])
    assert [s.key for s in player_sources(unk_then_known)] == ["api_player_42"]

    ps = player_sources(_API_ACTIVITY_FIXTURE)
    # 2522 is there because a CLAUSE moved him: a player who changed hands
    # is exactly one whose detail page is worth having. The unknown type 77
    # in the fixture names player 1 and is NOT here -- the event is kept in
    # the table, but a request built on a meaning we do not know would be a
    # request built on a guess.
    assert [s.key for s in ps] == ["api_player_1337", "api_player_652",
                                   "api_player_2522"], ps
    assert not any(s.key == "api_player_1" for s in ps), ps
    assert all(s.cadence == "once" and s.auth for s in ps), ps
    assert all(s.table == "api_players" for s in ps), ps
    assert not any("None" in s.key or s.key == "api_player_" for s in ps)
    assert source_for("api_player_1191").parse is parse_api_player
    assert source_for("api_player_1191").table == "api_players"
    assert not any(s.auth for s in sources() if not s.key.startswith("api_"))

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
    _rev = _json.dumps(list(reversed(_json.loads(_API_PLAYERS_ALL_FIXTURE))))
    assert (sign_api_players_all(_rev) ==
            sign_api_players_all(_API_PLAYERS_ALL_FIXTURE))
    assert source_for("api_players_all").parse is parse_api_players_all
    assert source_for("api_players_all").table == "api_players_all"
    assert source_for("api_players_all").cadence == "daily"
    assert source_for("api_players_all").auth is True

    off = parse_api_offer(_API_OFFER_FIXTURE, "t", "api_offer_24338726")
    assert len(off) == 1, off
    assert off[0]["player_team_id"] == "24338726", off
    assert off[0]["offer_id"] == "49892747" and off[0]["money"] == "6795815"
    assert off[0]["status"] == "pending", off
    assert off[0]["from_market"] == "true", off
    assert off[0]["expires_at"] == "2026-08-25T22:24:00+02:00", off
    empty = parse_api_offer("[]", "t", "api_offer_24338726")
    assert len(empty) == 1 and empty[0]["status"] == "", empty
    assert empty[0]["player_team_id"] == "24338726", empty
    assert empty[0]["offer_id"] == "", empty
    assert parse_api_offer(_API_OFFER_FIXTURE, "t", "not-a-key") == []
    assert sign_api_offer(_API_OFFER_FIXTURE) is not None
    assert sign_api_offer("[]") is not None
    assert sign_api_offer("[]") != sign_api_offer(_API_OFFER_FIXTURE)
    assert sign_api_offer("not json") is None
    assert sign_api_offer(_API_OFFER_FIXTURE) != sign_api_offer(
        _API_OFFER_FIXTURE.replace('"pending"', '"accepted"'))

    osrc = offer_sources(_API_TEAMS_FIXTURE, "miguel_autentico", "017998544")
    assert [s.key for s in osrc] == ["api_offer_24338726"], osrc
    assert osrc[0].table == "api_offers" and osrc[0].auth
    assert osrc[0].cadence == "every_run", osrc[0]
    assert "017998544" in osrc[0].url and "24338726" in osrc[0].url, osrc[0]
    assert offer_sources(_API_TEAMS_FIXTURE, "nobody", "017998544") == []
    assert source_for("api_offer_24338726").parse is parse_api_offer
    assert source_for("api_offer_24338726").table == "api_offers"
    assert offer_source("not-an-offer-key") is None

    # A path anchor whose text has no parseable jornada/score (skipped for
    # THAT reason) must not block a later, valid anchor for the SAME path --
    # "seen" may only be marked once the jornada/sides check has passed, not
    # on first sight of the path. Same class of bug as player_sources above.
    dup_path_html = (
        '<a href="/partidos/22421-alaves-getafe">no jornada here</a>'
        '<a href="/partidos/22421-alaves-getafe">Jornada 1 3-0</a>')
    dup_cal = parse_calendar(dup_path_html, "2026-01-01T0000Z")
    assert len(dup_cal) == 1 and dup_cal[0]["jornada"] == 1, dup_cal

    cal = parse_calendar(_CAL_FIXTURE, "2026-01-01T0000Z")
    byp = {r["path"]: r for r in cal}
    assert len(cal) == 3, cal
    assert byp["22421-alaves-getafe"]["score"] == "3-0"
    assert byp["22421-alaves-getafe"]["jornada"] == 1
    assert byp["22421-alaves-getafe"]["match_id"] == "22421"
    assert byp["22424-elche-betis"]["score"] == ""
    assert byp["22429-sevilla-rayo"]["away"] == "rayo-vallecano", byp
    assert _match_sides("real-madrid-real-sociedad") \
        == ("real-madrid", "real-sociedad")
    assert _match_sides("cadiz-celta-fortuna") is None
    ps = played_sources(_CAL_FIXTURE)
    assert [s.key for s in ps] == ["match_22421-alaves-getafe",
                                   "match_22429-sevilla-rayo"], ps
    assert ps[0].url.endswith("/partidos/22421-alaves-getafe")
    assert ps[0].cadence == "once" and ps[0].table == "starters"
    assert sign_calendar(_CAL_FIXTURE) is not None
    assert sign_calendar("<html><body>no matches</body></html>") is None

    xi = parse_starters(_MATCH_FIXTURE, "2026-01-01T0000Z",
                        "match_22421-alaves-getafe")
    got = {}
    for r in xi:
        got[(r["team_slug"], r["role"])] = got.get((r["team_slug"], r["role"]),
                                                   0) + 1
    assert got == {("alaves", "starter"): 11, ("alaves", "sub"): 3,
                   ("getafe", "starter"): 11, ("getafe", "sub"): 2}, got
    assert xi[0]["team_slug"] == "alaves" and xi[0]["player_name"] == "loc0"
    assert all(r["player_slug"] for r in xi), xi
    assert xi[0]["player_slug"] == "loc0", xi[0]
    assert xi[0]["match_id"] == "22421" and xi[0]["source"] == SOURCE
    assert {r["role"] for r in xi} == {"starter", "sub"}
    assert {r["role"] for r in xi} < {r["role"] for r in rows}
    assert "jornada" not in xi[0]
    assert not any("Completo" in r["player_name"] for r in xi), xi
    assert xi[1]["player_name"] == "loc1", xi[1]
    assert not any("'" in r["player_name"] for r in xi), xi
    assert xi[1]["minute"] == "64", xi[1]
    assert xi[0]["minute"] == "", xi[0]

    short = parse_starters(_match_html(home_xi=9), "t",
                           "match_22421-alaves-getafe")
    assert {r["team_slug"] for r in short} == {"getafe"}, short
    assert parse_starters("<html><body>sin datos</body></html>", "t",
                          "match_22421-alaves-getafe") == []
    assert sign_starters("<html><body>sin datos</body></html>") is None
    assert sign_starters(_MATCH_FIXTURE) is not None
    assert parse_starters(_MATCH_FIXTURE, "t", "market") == []
    assert match_source("market") is None
    assert match_source("match_22421-alaves-getafe").key \
        == "match_22421-alaves-getafe"
    assert source_for("match_22421-alaves-getafe").parse is parse_starters

    ln = parse_api_lineup(LINEUP_FIXTURE, "t1", "api_lineup_38")
    assert len(ln) == 4, ln
    assert [r["slot"] for r in ln] == ["POR", "DEF", "MED", "DEL"], ln
    assert {r["formation"] for r in ln} == {"4-5-1"}
    assert ln[0]["week"] == "38" and ln[0]["player_id"] == "1070"
    assert ln[2]["player_name"] == "Pepelu"
    assert ln[2]["player_name_full"].startswith("Jos")
    assert ln[0]["snapshot_at"].startswith("2026-08-19T20:26")
    assert sign_api_lineup(LINEUP_FIXTURE) == sign_api_lineup(
        LINEUP_FIXTURE.replace("4350000", "4360000"))
    assert sign_api_lineup(LINEUP_FIXTURE) != sign_api_lineup(
        LINEUP_FIXTURE.replace('"1070"', '"1071"'))
    assert parse_api_lineup("not json", "t1") == []
    assert source_for("api_lineup_38").table == "api_lineup"

    reg = sources()
    assert len(reg) == (8 + len(TEAMS) + len(AF_TEAMS) + FD_SEASONS_BACK + 1
                        + UNDERSTAT_SEASONS_BACK + 1) == 54, len(reg)
    assert set(AF_TEAMS) == set(TEAMS), set(AF_TEAMS) ^ set(TEAMS)
    assert {s.cadence for s in reg if s.key.startswith(("team_", "af_"))
            and s.key != "af_fixtures"} == {"twice_daily"}
    assert next(s.cadence for s in reg if s.key == "af_fixtures") == "daily"
    assert {s.cadence for s in reg if s.key in ("market", "points")} \
        == {"every_run"}
    assert {s.key for s in reg} >= {"market", "points", "team_barcelona",
                                    "af_fixtures"}
    assert len({s.key for s in reg}) == len(reg)
    assert {s.table for s in reg} == {"market", "points", "lineups",
                                      "fixtures", "elo", "matches",
                                      "api_leagues", "results_history",
                                      "understat_players", "odds",
                                      "api_players_all"}
    assert source_for("team_celta").parse is parse_team
    assert source_for("gone") is None

    assert source_for("af_celta").parse is parse_af_team
    samples = {"market": _MARKET_FIXTURE, "points": _POINTS_FIXTURE,
               "af_fixtures": _AF_HUB_FIXTURE, "elo": _ELO_FIXTURE,
               CAL_KEY: _CAL_FIXTURE, API_LEAGUES_KEY: _API_LEAGUES_FIXTURE,
               "understat_2026": _UNDERSTAT_LIVE,
               "understat_2025": _UNDERSTAT_PAST, "odds": _ODDS_LIVE,
               "api_players_all": _API_PLAYERS_ALL_FIXTURE}
    for i, k in enumerate(sorted(AF_TEAMS)):
        samples[f"af_{k}"] = _AF_FIXTURE if i % 2 else _AF_CONSENSO_FIXTURE
    for s in reg:
        html = samples.get(s.key, _FIXTURE)
        assert s.sign(html) is not None, s.key
        assert isinstance(s.parse(html, "2026-01-01T0000Z", s.key), list), s.key

    print("sources.py selftest OK (266 cases)")


if __name__ == "__main__":
    _selftest()
