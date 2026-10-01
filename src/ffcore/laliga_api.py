"""The LaLiga Fantasy app's API: leagues, market, activity, teams, line-ups
and offers."""
from __future__ import annotations

import json
import re

from ffcore.source import Source, _once, _rebuild

__all__ = ["LFG_SOURCE", "API_LEAGUES_KEY", "API_LEAGUES_URL",
           "API_MARKET_URL", "API_ACTIVITY_URL", "API_TEAMS_URL", "ROW_TABLE",
           "ACT_JOINED", "ACT_BUY", "ACT_SELL", "ACT_BONUS", "ACT_BONUS_ZERO",
           "ACT_TRANSFER", "ACT_KIND", "parse_api_leagues", "parse_api_market",
           "parse_api_activity", "parse_api_teams", "API_PLAYERS_ALL_URL",
           "parse_api_players_all", "API_OFFER_URL", "API_OFFER_KEY_RE",
           "parse_api_offer", "offer_source", "offer_sources", "api_source",
           "league_sources"]


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

# A player moving between two managers, by clause or by an accepted offer:
# the feed records both alike (Luismi Cruz moved under a protected clause).
ACT_TRANSFER = 1

ACT_KIND = {ACT_JOINED: "joined", ACT_BUY: "buy", ACT_SELL: "sell",
           ACT_BONUS: "bonus", ACT_BONUS_ZERO: "bonus",
           ACT_TRANSFER: "transfer"}


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


def _offers_for(league: str):
    def follow(teams_json: str, context: dict) -> list[Source]:
        return offer_sources(teams_json, context["me"], league)
    return follow


def league_sources(leagues_json: str, observed_at: str = "") -> list[Source]:
    out = []
    for r in parse_api_leagues(leagues_json, observed_at):
        lg = r["league_id"]
        out.append(Source("api_market", "api_market",
                          API_MARKET_URL.format(base="{base}", league=lg),
                          parse_api_market, auth=True))
        out.append(Source("api_teams", "api_teams",
                          API_TEAMS_URL.format(base="{base}", league=lg),
                          parse_api_teams, auth=True, follow=_offers_for(lg)))
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
             "transfer"]), ac
    assert ac[0]["amount"] == "58220110" and ac[0]["user_id"] == "11881989"
    assert ac[4]["week"] == "2" and ac[4]["amount"] == "2200000", ac[4]
    assert ac[5]["week"] == "3" and ac[5]["amount"] == "", ac[5]

    moved = ac[6]
    assert moved["kind"] == "transfer", moved
    assert moved["user_id"] == "3480702", moved
    assert moved["counterparty"] == "11877808", moved
    assert moved["amount"] == "141425721", moved
    assert moved["player_id"] == "2522", moved
    assert all(r["counterparty"] == "" for r in ac if r["kind"] != "transfer")

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
    teams = next(s for s in disc if s.key == "api_teams")
    assert [s.key for s in teams.follow(_API_TEAMS_FIXTURE,
                                        {"me": "miguel_autentico"})] \
        == ["api_offer_24338726"], "api_teams follows to your offers"
    assert teams.follow(_API_TEAMS_FIXTURE, {"me": "nobody"}) == []
    assert all(s.follow is None for s in disc if s.key != "api_teams")
    assert api_source("market") is None

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
    assert offer_source("not-an-offer-key") is None

    ln = parse_api_lineup(LINEUP_FIXTURE, "t1", "api_lineup_38")
    assert len(ln) == 4, ln
    assert [r["slot"] for r in ln] == ["POR", "DEF", "MED", "DEL"], ln
    assert {r["formation"] for r in ln} == {"4-5-1"}
    assert ln[0]["week"] == "38" and ln[0]["player_id"] == "1070"
    assert ln[2]["player_name"] == "Pepelu"
    assert ln[2]["player_name_full"].startswith("Jos")
    assert ln[0]["snapshot_at"].startswith("2026-08-19T20:26")
    assert parse_api_lineup("not json", "t1") == []

    print("ffcore.laliga_api self-test OK")


if __name__ == "__main__":
    _selftest()
