
from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo
from typing import Callable, NamedTuple

from ffcore.text import match_one, norm

from lxml import html as lh

__all__ = ["BASE", "SOURCE", "MARKET_URL", "POINTS_URL", "TEAM_URL", "TEAMS",
           "Source", "sources", "source_for", "SEVERITY",
           "parse_market", "parse_team", "parse_points", "parse_fitness",
           "season_label",
           "FD_BASE", "FD_URL", "FD_SOURCE", "CLUB_ALIASES", "club_slug", "FD_SEASONS_BACK",
           "fd_sources", "parse_fd_results",
           "CAL_KEY", "FF_CAL_URL", "MATCH_URL", "MATCH_KEY_RE",
           "parse_calendar", "parse_starters", "match_source", "played_sources",
           "LFG_SOURCE", "API_LEAGUES_KEY", "API_LEAGUES_URL",
           "API_MARKET_URL", "API_ACTIVITY_URL", "API_TEAMS_URL",
           "ACT_KIND", "ACT_JOINED", "ACT_BUY", "ACT_SELL", "ACT_BONUS",
           "ACT_CLAUSE",
           "ACT_BONUS_ZERO",
           "ROW_TABLE",
           "parse_api_leagues", "parse_api_market", "parse_api_activity",
           "parse_api_teams", "league_sources",
           
           "API_OFFER_URL", "API_OFFER_KEY_RE", "parse_api_offer",
           "offer_source", "offer_sources"]

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


def _once(seen: set, key) -> bool:
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
            return parts[0]
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
            "club": club_slug(teams.get(team_id or "", "")),
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
    rows, seen = [], set()
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
    start = int(observed_at[:4]) - (int(observed_at[5:7]) < 7)
    try:
        return datetime(start + (month < 7), month, day, hour, minute,
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
    rows, seen = [], set()
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


def _rebuild(key: str, pattern, table: str, parse, url_for, **kw):
    m = pattern.match(key or "")
    if not m:
        return None
    return Source(key, table, url_for(m), parse, **kw)


def match_source(key: str) -> Source | None:
    return _rebuild(key, MATCH_KEY_RE, "starters", parse_starters, lambda m: MATCH_URL.format(path=m.group(1)),
                    cadence="once")


def played_sources(cal_html: str, observed_at: str = "") -> list[Source]:
    return [match_source("match_%s" % r["path"])
            for r in parse_calendar(cal_html, observed_at) if r["score"]]


FD_BASE = "https://www.football-data.co.uk"
FD_URL = FD_BASE + "/mmz4281/{season}/SP1.csv"
FD_SOURCE = "football-data"


FD_SEASONS_BACK = 3

FD_FIELDS = ("FTHG", "FTAG", "HxG", "AxG", "HS", "AS", "HST", "AST",
            "HC", "AC")


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
            FD_URL.format(season=season), parse_fd_results,
            cadence="every_run" if back == 0 else "once"))
    return out


def _fd_rows(text: str) -> list[dict]:
    if not text:
        return []
    return list(csv.DictReader(io.StringIO(text.lstrip("﻿"))))


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
            "home": club_slug(home), "away": club_slug(away),
        }
        for col, out_key in zip(FD_FIELDS,
                                ("home_goals", "away_goals", "home_xg",
                                 "away_xg", "home_shots", "away_shots",
                                 "home_shots_on_target", "away_shots_on_target",
                                 "home_corners", "away_corners")):
            row[out_key] = (r.get(col) or "").strip()
        rows.append(row)
    return rows


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

ROW_TABLE = "table"

ACT_JOINED, ACT_BUY, ACT_SELL = 9, 31, 33
ACT_BONUS, ACT_BONUS_ZERO = 6, 7

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


def _parse_json_list(text: str, observed_at: str, row_fn, *args,
                     if_empty=()) -> list[dict]:
    d = _j(text)
    if not isinstance(d, list):
        return []
    rows = [row for it in d for row in (row_fn(it, *args) or ())] or list(if_empty)
    return [{"observed_at": observed_at, "source": LFG_SOURCE, **row}
            for row in rows]


def _league_row(lg) -> list[dict]:
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


def parse_api_leagues(text: str, observed_at: str,
                      key: str = "api_leagues") -> list[dict]:
    return _parse_json_list(text, observed_at, _league_row)


def _market_row(it) -> list[dict]:
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


def parse_api_market(text: str, observed_at: str,
                     key: str = "api_market") -> list[dict]:
    return _parse_json_list(text, observed_at, _market_row)


def _activity_row(a) -> list[dict]:
    if not a.get("id"):
        return []
    kind = ACT_KIND.get(a.get("activityTypeId")) \
        or ("unknown:%s" % a.get("activityTypeId"))
    return [{
        "activity_id": str(a["id"]),
        "at": a.get("createdAt") or "",
        "kind": kind,
        "user_id": str(a.get("user1Id") or ""),
        "counterparty": str(a.get("user2Id") or ""),
        "player_id": str(a.get("playerMasterId") or ""),
        "amount": str(a.get("amount") or ""),
        "week": str(a.get("weekNumber") or ""),
    }]


def parse_api_activity(text: str, observed_at: str,
                       key: str = "api_activity") -> list[dict]:
    return _parse_json_list(text, observed_at, _activity_row)


def _team_rows(t, observed_at: str) -> list[dict]:
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
    return out


def parse_api_teams(text: str, observed_at: str,
                    key: str = "api_teams") -> list[dict]:
    return _parse_json_list(text, observed_at, _team_rows, observed_at)


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


API_PLAYERS_ALL_URL = "{base}/v1/competition/1/players?x-lang=es"


def _player_all_row(p) -> list[dict]:
    if not p.get("id"):
        return []
    return [{"team_id": str(p.get("teamId") or ""), **_player_identity(p),
            "player_status": p.get("playerStatus") or ""}]


def parse_api_players_all(text: str, observed_at: str,
                          key: str = "api_players_all") -> list[dict]:
    return _parse_json_list(text, observed_at, _player_all_row)


API_OFFER_URL = ("{base}/v1/competition/1/league/{league}/playerTeam/{ptid}"
                 "/offer?x-lang=es")
API_OFFER_KEY_RE = re.compile(r"^api_offer_(\d+)$")


def _offer_row(it, ptid: str) -> list[dict]:
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


def parse_api_offer(text: str, observed_at: str,
                    key: str = "api_offer_0") -> list[dict]:
    m = API_OFFER_KEY_RE.match(key or "")
    ptid = m.group(1) if m else ""
    if not ptid:
        return []
    return _parse_json_list(text, observed_at, _offer_row, ptid, if_empty=[{
        "player_team_id": ptid, "offer_id": "", "money": "", "status": "",
        "created_at": "", "expires_at": "", "from_market": ""}])


def offer_source(key: str) -> Source | None:
    return _rebuild(key, API_OFFER_KEY_RE, "api_offers", parse_api_offer, lambda m: API_OFFER_URL, auth=True)


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
            parse_api_offer, auth=True))
    return out


def api_source(key: str) -> Source | None:
    table = {"api_market": (API_MARKET_URL, parse_api_market),
             "api_teams": (API_TEAMS_URL, parse_api_teams)}
    if key.startswith("api_activity_"):
        return Source(key, "api_activity", API_ACTIVITY_URL,
                      parse_api_activity, auth=True)
    if key.startswith("api_lineup_"):
        return Source(key, "api_lineup", API_LINEUP_URL,
                      parse_api_lineup, auth=True)
    if key in table:
        url, p = table[key]
        return Source(key, key, url, p,
                      cadence="daily" if key == "api_teams" else "every_run",
                      auth=True)
    return None


def league_sources(leagues_json: str, observed_at: str = "") -> list[Source]:
    out = []
    for r in parse_api_leagues(leagues_json, observed_at):
        lg = r["league_id"]
        out.append(Source("api_market", "api_market",
                          API_MARKET_URL.format(base="{base}", league=lg),
                          parse_api_market, auth=True))
        out.append(Source("api_teams", "api_teams",
                          API_TEAMS_URL.format(base="{base}", league=lg),
                          parse_api_teams, auth=True))
        if r.get("team_id"):
            out.append(Source(
                "api_lineup_%d" % LINEUP_WEEK, "api_lineup",
                API_LINEUP_URL.format(base="{base}", team=r["team_id"],
                                      week=LINEUP_WEEK),
                parse_api_lineup, auth=True))
        for page in (0, 1):
            out.append(Source(
                "api_activity_%d" % page, "api_activity",
                API_ACTIVITY_URL.format(base="{base}", league=lg, page=page),
                parse_api_activity, auth=True))
    return out


class Source(NamedTuple):
    key: str
    table: str
    url: str
    parse: Callable
    cadence: str = "every_run"
    enabled: bool = True
    auth: bool = False


@lru_cache(maxsize=None)
def sources(enabled_only: bool = True) -> list[Source]:
    out = [
        Source("market", "market", MARKET_URL, parse_market),
        Source("points", "points", POINTS_URL, parse_points),
    ]
    out += [Source(f"team_{s}", "lineups", TEAM_URL.format(slug=s),
                   parse_team, cadence="twice_daily")
            for s in TEAMS]
    out += fd_sources()
    out += [Source(CAL_KEY, "matches", FF_CAL_URL, parse_calendar, cadence="daily")]
    out += [Source(API_LEAGUES_KEY, "api_leagues", API_LEAGUES_URL,
                   parse_api_leagues, auth=True)]
    out += [Source("api_players_all", "api_players_all", API_PLAYERS_ALL_URL,
                   parse_api_players_all,
                   cadence="daily", auth=True)]
    return [s for s in out if s.enabled or not enabled_only]


def source_for(key: str) -> Source | None:
    for s in sources(enabled_only=False):
        if s.key == key:
            return s
    return (match_source(key) or api_source(key)
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



    fs = fd_sources(datetime(2026, 8, 20, tzinfo=timezone.utc))
    assert [s.key for s in fs] == ["fd_2627", "fd_2526", "fd_2425", "fd_2324"]
    assert fs[0].cadence == "every_run"
    assert all(s.cadence == "once" for s in fs[1:])
    assert all(s.table == "results_history" for s in fs)
    assert source_for("fd_2627").parse is parse_fd_results


    lg = parse_api_leagues(_API_LEAGUES_FIXTURE, "2026-01-01T0000Z")
    assert len(lg) == 1 and lg[0]["league_id"] == "017998544", lg
    assert lg[0]["team_id"] == "38091967" and lg[0]["money"] == "23596582", lg
    assert lg[0]["source"] == LFG_SOURCE
    assert parse_api_leagues("<html>maintenance</html>", "t") == []

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
    assert mk[0]["bid_id"] == "b1" and mk[0]["bid_money"] == "5600000"
    assert mk[0]["bid_status"] == "pending"
    assert mk[1]["bid_id"] == "" and mk[1]["bid_money"] == ""

    ac = parse_api_activity(_API_ACTIVITY_FIXTURE, "t")
    assert ([r["kind"] for r in ac] ==
            ["buy", "sell", "joined", "unknown:77", "bonus", "bonus",
             "clause"]), ac
    assert ac[0]["amount"] == "58220110" and ac[0]["user_id"] == "11881989"
    assert ac[4]["week"] == "2" and ac[4]["amount"] == "2200000", ac[4]
    assert ac[5]["week"] == "3" and ac[5]["amount"] == "", ac[5]

    clause = ac[6]
    assert clause["kind"] == "clause", clause
    assert clause["user_id"] == "3480702", clause
    assert clause["counterparty"] == "11877808", clause
    assert clause["amount"] == "141425721", clause
    assert clause["player_id"] == "2522", clause
    assert all(r["counterparty"] == "" for r in ac if r["kind"] != "clause")

    import json as _aj
    _one_more = _aj.dumps(_aj.loads(_API_ACTIVITY_FIXTURE) + [
        {"id": "a8", "activityTypeId": 1, "amount": 5, "playerMasterId": 9,
         "user1Id": 1, "user2Id": 2,
         "createdAt": "2026-09-19T10:00:00+02:00"}])
    assert len(parse_api_activity(_one_more, "t")) == len(ac) + 1
    import json as _json
    _rev = _json.dumps(list(reversed(_json.loads(_API_ACTIVITY_FIXTURE))))

    all_rows = parse_api_teams(_API_TEAMS_FIXTURE, "t")
    assert {r[ROW_TABLE] for r in all_rows} == {"api_teams", "api_standings"}
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

    disc = league_sources(_API_LEAGUES_FIXTURE)
    assert [s.key for s in disc] == ["api_market", "api_teams",
                                     "api_lineup_%d" % LINEUP_WEEK,
                                     "api_activity_0", "api_activity_1"], disc
    assert "/teams/38091967/lineup/" in next(
        s.url for s in disc if s.table == "api_lineup")
    assert all(s.auth for s in disc), "every API entry needs the bearer"
    assert all(s.cadence == "every_run" for s in disc if s.key == "api_teams")
    assert "017998544" in disc[0].url and "{base}" in disc[0].url
    assert league_sources("<html>") == []
    for k in ("api_market", "api_teams", "api_activity_0", "api_activity_1"):
        assert source_for(k) is not None, k
        assert source_for(k).table == ("api_activity"
                                       if "activity" in k else k), k
    assert source_for("api_market").parse is parse_api_market
    assert api_source("market") is None

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
    _rev = _json.dumps(list(reversed(_json.loads(_API_PLAYERS_ALL_FIXTURE))))
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

    osrc = offer_sources(_API_TEAMS_FIXTURE, "miguel_autentico", "017998544")
    assert [s.key for s in osrc] == ["api_offer_24338726"], osrc
    assert osrc[0].table == "api_offers" and osrc[0].auth
    assert osrc[0].cadence == "every_run", osrc[0]
    assert "017998544" in osrc[0].url and "24338726" in osrc[0].url, osrc[0]
    assert offer_sources(_API_TEAMS_FIXTURE, "nobody", "017998544") == []
    assert source_for("api_offer_24338726").parse is parse_api_offer
    assert source_for("api_offer_24338726").table == "api_offers"
    assert offer_source("not-an-offer-key") is None

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
    assert parse_api_lineup("not json", "t1") == []
    assert source_for("api_lineup_38").table == "api_lineup"

    reg = sources()
    assert len(reg) == 5 + len(TEAMS) + FD_SEASONS_BACK + 1 == 29, len(reg)
    assert {s.cadence for s in reg if s.key.startswith("team_")} \
        == {"twice_daily"}
    assert {s.cadence for s in reg if s.key in ("market", "points")} \
        == {"every_run"}
    assert {s.key for s in reg} >= {"market", "points", "team_barcelona"}
    assert len({s.key for s in reg}) == len(reg)
    assert {s.table for s in reg} == {"market", "points", "lineups",
                                      "matches",
                                      "api_leagues", "results_history",
                                      "api_players_all"}
    assert source_for("team_celta").parse is parse_team
    assert source_for("gone") is None

    samples = {"market": _MARKET_FIXTURE, "points": _POINTS_FIXTURE,
               CAL_KEY: _CAL_FIXTURE, API_LEAGUES_KEY: _API_LEAGUES_FIXTURE,
               "api_players_all": _API_PLAYERS_ALL_FIXTURE}
    for s in fd_sources():
        samples[s.key] = _FD_CUR
    for s in reg:
        html = samples.get(s.key, _FIXTURE)
        assert s.parse(html, "2026-01-01T0000Z", s.key), s.key

    print("sources.py selftest OK")


if __name__ == "__main__":
    _selftest()
