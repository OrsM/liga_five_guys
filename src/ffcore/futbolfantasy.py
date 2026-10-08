"""futbolfantasy.com pages: the market, points, club line-ups, the
calendar and match sheets."""
from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date, datetime
from zoneinfo import ZoneInfo

from lxml import html as lh

from ffcore.text import match_one, norm
from ffcore.parse import year_for
from ffcore.source import Source, _once, _rebuild

__all__ = ["BASE", "SOURCE", "MARKET_URL", "POINTS_URL", "TEAM_URL", "TEAMS",
           "CLUB_ALIASES", "club_slug", "parse_market", "SEVERITY",
           "parse_fitness", "parse_team", "parse_points", "season_label",
           "CAL_KEY", "FF_CAL_URL", "MATCH_URL", "MATCH_KEY_RE",
           "parse_calendar", "parse_starters", "match_source",
           "played_sources"]


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

CLUB_ALIASES = {
    "ath bilbao": "athletic", "bilbao": "athletic", "ath madrid": "atletico",
    "atl madrid": "atletico", "espanol": "espanyol", "sociedad": "real-sociedad",
    "vallecano": "rayo-vallecano", "rayo": "rayo-vallecano",
    "dep a coruna": "deportivo", "la coruna": "deportivo",
    "santander": "racing",
}


def club_slug(name) -> str:
    if not name or "," in name:
        return ""
    return match_one(name, TEAMS) or CLUB_ALIASES.get(norm(name), "")


TEAM_SELECT_RE = re.compile(r'<select[^>]*name="equipo"[^>]*>(.*?)</select>', re.S)
OPTION_RE = re.compile(r'<option[^>]*value="(\d+)"[^>]*>([^<]+)</option>')


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
            return parts[0]
    m = PHOTO_RE.search(chunk)
    if m and m.group(1) != "00":
        return m.group(1)
    return None


def parse_market(html: str, observed_at: str, key: str = "market") -> list[dict]:
    doc = lh.fromstring(html)
    select = doc.xpath('(//select[@name="equipo"])[1]')
    teams = {o.get("value"): (o.text or "").strip()
             for o in (select[0].iter("option") if select else ())
             if (o.get("value") or "").isdigit() and o.get("value") != "0"
             and (o.text or "").strip() and not len(o)}
    rows = []
    for el in doc.xpath('//*[starts-with(@class, "elemento_jugador")]'):
        name, value = el.get("data-nombre"), el.get("data-valor")
        if not name or not value:
            continue
        team_id = el.get("data-equipo")
        rows.append({
            "observed_at": observed_at,
            "ff_id": el.get("data-id") or "",
            "name": name,
            "position": (el.get("data-posicion") or "").lower(),
            "team_id": team_id,
            "team": teams.get(team_id or "", ""),
            "club": club_slug(teams.get(team_id or "", "")),
            "value": int(value),
            "delta_1d": _num(el.get("data-diferencia1")),
            "delta_pct_1d": _num(el.get("data-diferencia-pct1")),
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
    flagged = [(el, FITNESS_ALT.get((icon[0].get("alt") or "").strip().lower()
                                    if icon else ""))
               for el in _css(doc, ".lesionados_wrapper section.mod.lesionados"
                                   " > .elemento")
               for icon in (_css(el, ".icono img"),)]
    flagged += [(el, "suspended") for sec in _suspension_sections(doc)
                for el in _css(sec, ".elemento")]
    flagged += [(el, "unavailable")
                for el in _css(doc, "section.mod.nodisponibles .elemento")]
    found: dict[str, dict] = {}
    for el, status in flagged:
        if not status:
            continue
        name, slug = _flagged_name(el)
        key = norm(name)
        prev = found.get(key)
        if not key or (prev and SEVERITY.index(prev["status"])
                       <= SEVERITY.index(status)):
            continue
        found[key] = {"name": name, "slug": slug, "status": status,
                      "note": _note(el)}
    return found


def _lineup_row(observed_at, source, team_slug, player_name, player_slug,
                role, start_pct, status, note) -> dict:
    return {
        "observed_at": observed_at,
        "source": source,
        "team_slug": team_slug,
        "player_name": player_name,
        "player_slug": player_slug,
        "role": role,
        "start_pct": start_pct,
        "status": status,
        "note": note,
    }


def _team_player(el) -> tuple:
    text = " ".join(el.text_content().split())
    m = NAME_RE.match(text)
    if m:
        name, pct = m.group(1).strip() or None, int(m.group(2))
    else:
        name, pct = (re.split(r"\d", text, 1)[0].strip() or None), None
    href = el.get("href") or ""
    if not href:
        a = el.find(".//a[@href]")
        href = a.get("href") if a is not None else ""
    return name, pct, href


def parse_team(html: str, observed_at: str, key: str = "team_test") -> list[dict]:
    slug = key[5:] if key.startswith("team_") else key
    doc = lh.fromstring(html)
    fitness = parse_fitness(doc)
    rows: list[dict] = []
    seen: set[str] = set()
    for role, selector in zip(("starter", "sub"), XI_SELECTORS):
        for el in _css(doc, selector):
            name, pct, href = _team_player(el)
            if not _once(seen, name.lower()):
                continue
            fit = fitness.get(norm(name))
            rows.append(_lineup_row(
                observed_at, SOURCE, slug, name,
                _slug('href="%s"' % href) if href else None, role, pct,
                fit["status"] if fit else "ok", fit["note"] if fit else ""))
    named = {norm(r["player_name"]) for r in rows}
    for fkey, fit in fitness.items():
        if fkey not in named:
            rows.append(_lineup_row(
                observed_at, SOURCE, slug, fit["name"], fit["slug"] or None,
                "absent", None, fit["status"], fit["note"]))
    return rows


WANT = {
    "name": ["jugador"],
    "points": ["puntos", "pts"],
    "games": ["pj"],
    "avg": ["media", "med"],
}


_SELECTORS: dict[str, Callable] = {}


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
                "season": season_label(html),
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


def _suspension_sections(doc):
    return [s for s in _css(doc, "section.mod.sancionados")
           if "mercado-box" not in " ".join(s.classes)]


CAL_KEY = "calendario"
FF_CAL_URL = f"{BASE}/laliga/calendario"
MATCH_URL = f"{BASE}/partidos/{{path}}"

MATCH_PATH_RE = re.compile(r"/partidos/(\d+-[a-z0-9-]+)")
MATCH_KEY_RE = re.compile(r"^match_(\d+-[a-z0-9-]+)$")
CAL_JORNADA_RE = re.compile(r"Jornada\s*(\d+)")
CAL_SCORE_RE = re.compile(r"\b(\d+\s*-\s*\d+)\b")
CAL_KICKOFF_RE = re.compile(r"(\d\d)/(\d\d) (\d\d):(\d\d)h")
MADRID = ZoneInfo("Europe/Madrid")


def _kickoff(text: str, observed_at: str) -> str:
    m = CAL_KICKOFF_RE.search(text)
    if not m or not observed_at[:4].isdigit() or not observed_at[5:7].isdigit():
        return ""
    day, month, hour, minute = map(int, m.groups())
    seen = date(int(observed_at[:4]), int(observed_at[5:7]), 1)
    try:
        return datetime(year_for(month, seen, 7), month, day, hour, minute,
                        tzinfo=MADRID).isoformat()
    except ValueError:
        return ""

MATCH_SIDES = (".stats-local", ".stats-visitante")
MATCH_SUBS_HEADER = "Suplentes"
MATCH_MINUTE_RE = re.compile(r"\s*\d+\s*'\s*$")
MATCH_MINUTE_CAPTURE_RE = re.compile(r"(\d+)\s*'\s*$")
XI_SIZE = 11


def _match_sides(slug: str) -> tuple[str, str] | None:
    known = {t: t for t in TEAMS}
    known.update(CLUB_ALIASES)
    for head in known:
        tail = slug[len(head) + 1:]
        if slug.startswith(head + "-") and tail in known:
            return known[head], known[tail]
    return None


def _calendar_row(a, observed_at: str) -> dict | None:
    m = MATCH_PATH_RE.search(a.get("href") or "")
    if not m:
        return None
    path = m.group(1)
    text = _WS.sub(" ", a.text_content()).strip()
    jor = CAL_JORNADA_RE.search(text)
    sides = _match_sides(path.split("-", 1)[1])
    if not (jor and sides):
        return None
    score = CAL_SCORE_RE.search(text)
    return {
        "key": path,
        "observed_at": observed_at,
        "source": SOURCE,
        "match_id": path.split("-", 1)[0],
        "path": path,
        "jornada": int(jor.group(1)),
        "home": sides[0],
        "away": sides[1],
        "score": score.group(1).replace(" ", "") if score else "",
        "kickoff": "" if score else _kickoff(text, observed_at),
    }


def parse_calendar(html: str, observed_at: str,
                   key: str = "calendario") -> list[dict]:
    rows: list[dict] = []
    seen: set[str] = set()
    for el in _css(lh.fromstring(html), 'a[href*="/partidos/"]'):
        row = _calendar_row(el, observed_at)
        if row is not None and _once(seen, row.pop("key")):
            rows.append(row)
    return rows


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


def match_source(key: str) -> Source | None:
    return _rebuild(key, MATCH_KEY_RE, "starters", parse_starters, lambda m: MATCH_URL.format(path=m.group(1)),
                    cadence="once")


def played_sources(cal_html: str, observed_at: str = "") -> list[Source]:
    return [s for r in parse_calendar(cal_html, observed_at)
            if r["score"] and (s := match_source("match_%s" % r["path"]))]


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


def _fixture_rows(prefix: str, xi: int, subs: int) -> str:
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


def _fixture_side(cls: str, prefix: str, xi: int, subs: int) -> str:
    return ('<div class="col-12 %s"><h2 class="title">Puntos</h2>'
            '<table class="tablestats"><tbody>%s</tbody></table></div>'
            '<div class="col-12 %s"><h2 class="title">En directo</h2>'
            '<table class="tablestats"><tbody>'
            '<tr class="plegado plegable"><td class="name">'
            'Nombre Completo Que No Vale</td></tr>'
            '</tbody></table></div>'
            % (cls, _fixture_rows(prefix, xi, subs), cls))


def _match_html(home_xi: int = 11, away_xi: int = 11) -> str:
    return ("<html><body><div class='row stats-table'>%s%s</div></body></html>"
            % (_fixture_side("stats-local", "loc", home_xi, 3),
               _fixture_side("stats-visitante", "vis", away_xi, 2)))


_MATCH_FIXTURE = _match_html()


def _selftest() -> None:
    for href, want in [("/jugadores/kazunari-kita/laliga-26-27", "kazunari-kita"),
                       ("/jugadores/pedri", "pedri"),
                       ("/jugadores/ficha/1234", "1234")]:
        assert _slug('href="%s"' % href) == want, (href, _slug('href="%s"' % href))
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


    for name, want in [("Real Sociedad", "real-sociedad"), ("Bilbao", "athletic"),
                       ("Atl. Madrid", "atletico"), ("La Coruna", "deportivo"),
                       ("Rayo", "rayo-vallecano"), ("Celta Vigo", "celta"),
                       ("Girona,Real Sociedad", ""), ("Girona", ""), ("", "")]:
        assert club_slug(name) == want, (name, club_slug(name))

    assert MARKET_URL.format(date="2026-08-16") == MARKET_URL

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
    assert byp["22421-alaves-getafe"]["kickoff"] == ""
    live = {r["path"]: r for r in parse_calendar(_CAL_FIXTURE, "2026-08-15T0900Z")}
    assert live["22424-elche-betis"]["kickoff"] == "2026-08-17T21:00:00+02:00"
    assert _kickoff("Jornada 20 Dom 10/01 20:00h", "2026-12-20T0900Z") \
        == "2027-01-10T20:00:00+01:00"
    assert _kickoff("Jornada 20 Dom 10/01 20:00h", "2027-01-02T0900Z") \
        == "2027-01-10T20:00:00+01:00"
    assert _kickoff("Jornada 1 Lun 17/08 21:00h", "t") == ""
    assert byp["22429-sevilla-rayo"]["away"] == "rayo-vallecano", byp
    assert _match_sides("real-madrid-real-sociedad") \
        == ("real-madrid", "real-sociedad")
    assert _match_sides("cadiz-celta-fortuna") is None
    ps = played_sources(_CAL_FIXTURE)
    assert [s.key for s in ps] == ["match_22421-alaves-getafe",
                                   "match_22429-sevilla-rayo"], ps
    assert ps[0].url.endswith("/partidos/22421-alaves-getafe")
    assert ps[0].cadence == "once" and ps[0].table == "starters"

    xi = parse_starters(_MATCH_FIXTURE, "2026-01-01T0000Z",
                        "match_22421-alaves-getafe")
    got: dict[tuple[str, str], int] = {}
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
    assert parse_starters(_MATCH_FIXTURE, "t", "market") == []
    assert match_source("market") is None
    ms = match_source("match_22421-alaves-getafe")
    assert ms is not None and ms.key == "match_22421-alaves-getafe"

    print("ffcore.futbolfantasy self-test OK")


if __name__ == "__main__":
    _selftest()
