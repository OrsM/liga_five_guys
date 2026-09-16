
from __future__ import annotations

from dataclasses import dataclass, field

from ffcore.crosswalk import Player
from ffcore.text import norm

__all__ = ["PlayerCurrent", "PlayerHistory",
          "PlayerDerived", "PlayerProfile", "build_profiles",
          "status_adjusted"]


@dataclass
class PlayerCurrent:
    club: str = ""
    pos: str = ""
    status: str = ""
    market_value: float | None = None
    listed: bool = False
    price: float | None = None
    owner: str | None = None
    value: float | None = None
    clause: float | None = None
    clause_until: object = None
    route: str | None = None
    bids: int | None = None
    proceeds: float | None = None


@dataclass
class PlayerHistory:
    points_by_jornada: dict[int, float] = field(default_factory=dict)
    started_by_jornada: dict[int, bool] = field(default_factory=dict)
    match_stats_by_jornada: dict[int, dict] = field(default_factory=dict)
    opponent_by_jornada: dict[int, tuple] = field(default_factory=dict)
    market_value_series: list = field(default_factory=list)
    understat_season: dict | None = None


def status_adjusted(pts: float, p_start: float, status: str
                     ) -> tuple[float, float]:
    from ffcore.score import OUT_STATUSES, status_multiplier

    mult = status_multiplier(status)
    if status in OUT_STATUSES:
        return pts, p_start * mult
    return pts * mult, p_start


@dataclass
class PlayerDerived:
    ppm: float | None = None
    pj: float = 0.0
    start_p: float | None = None
    market_exp: float | None = None
    pts_now: float | None = None
    scored: object = None


UNSCORED_DEFAULT = (2.0, 0.5)


@dataclass
class PlayerProfile:
    identity: Player
    current: PlayerCurrent
    history: PlayerHistory
    derived: PlayerDerived

    def to_bootstrap_input(self) -> tuple[tuple[float, float],
                                          tuple[float, float]]:
        s = self.derived.scored
        if s is None:
            return UNSCORED_DEFAULT, UNSCORED_DEFAULT
        pts_rest = max(0.0, s.ppm * s.fix)
        p_rest = min(1.0, (s.pct_rest or 0) / 100)
        return ((self.derived.pts_now, self.derived.start_p),
               (pts_rest, p_rest))


def _match_stats_history(rows) -> dict[str, dict[int, dict]]:
    out: dict[str, dict[int, dict]] = {}
    for r in rows:
        pid = (r.get("player_id") or "").strip()
        if not pid:
            continue
        try:
            week = int(r["week"])
        except (KeyError, TypeError, ValueError):
            continue
        stat = (r.get("stat") or "").strip()
        if not stat:
            continue
        try:
            value = float(r.get("value") or 0)
            points = float(r.get("points") or 0)
        except (TypeError, ValueError):
            continue
        out.setdefault(pid, {}).setdefault(week, {})[stat] = (value, points)
    return out


def _opponent_history(match_rows) -> dict[str, dict[int, tuple]]:
    out: dict[str, dict[int, tuple]] = {}
    for r in match_rows:
        home, away = (r.get("home") or "").strip(), (r.get("away") or "").strip()
        if not home or not away:
            continue
        try:
            j = int(r["jornada"])
        except (KeyError, TypeError, ValueError):
            continue
        out.setdefault(home, {})[j] = (away, True)
        out.setdefault(away, {})[j] = (home, False)
    return out


def _perjornada_history(rows) -> dict[str, PlayerHistory]:
    out: dict[str, PlayerHistory] = {}
    for r in rows:
        fid = (r.get("ff_id") or "").strip()
        key = fid or norm(r.get("player_name") or "")
        if not key:
            continue
        try:
            j = int(r["jornada"])
            pts = float(r.get("points_delta") or 0)
            games = int(r.get("games_delta") or 0)
        except (KeyError, TypeError, ValueError):
            continue
        h = out.setdefault(key, PlayerHistory())
        h.points_by_jornada[j] = h.points_by_jornada.get(j, 0.0) + pts
        if games > 0:
            h.started_by_jornada[j] = True

    return out


def build_profiles(players: dict, sc, perjornada_rows,
                   xw=None, match_stats_rows=None, match_rows=None,
                   market_keyed: dict | None = None) -> dict[str, "PlayerProfile"]:
    histories = _perjornada_history(perjornada_rows)
    match_stats = _match_stats_history(match_stats_rows or [])
    opponents = _opponent_history(match_rows or [])
    out: dict[str, PlayerProfile] = {}
    for k, rec in players.items():
        xp = xw.players.get(k) if xw is not None else None
        ident = xp if xp is not None else Player(player_id=k)
        ident.name = rec.get("name") or ident.name or k
        mk = (market_keyed or {}).get(k, {})
        cur = PlayerCurrent(
            club=(xp.club_id if xp else "") or rec.get("team") or "",
            pos=(rec.get("pos") or "").upper(),
            market_value=None,
            listed=bool(mk.get("listed")),
            price=mk.get("price"),
            owner=mk.get("owner"),
            value=mk.get("value"),
            clause=mk.get("clause"),
            clause_until=mk.get("clause_until"),
            route=mk.get("route"),
            bids=mk.get("bids"),
            proceeds=mk.get("proceeds"),
        )
        row = sc.row_for(k)
        s = sc.score(row) if row else None
        if s is not None:
            pts_adj, p_start_adj = status_adjusted(
                max(0.0, s.ppm * s.fix), min(1.0, (s.pct_used or 0) / 100),
                s.status)
        else:
            pts_adj = p_start_adj = None
        der = PlayerDerived(
            ppm=s.ppm if s else None,
            pj=s.pj if s else 0.0,
            start_p=p_start_adj,
            market_exp=(pts_adj * p_start_adj) if s else None,
            pts_now=pts_adj,
            scored=s,
        )
        if s is not None:
            cur.status = s.status
        hist = histories.get(k) or histories.get(norm(ident.name)) \
            or PlayerHistory()
        if ident.app_id in match_stats:
            hist.match_stats_by_jornada = match_stats[ident.app_id]
        if cur.club in opponents:
            hist.opponent_by_jornada = opponents[cur.club]
        out[k] = PlayerProfile(identity=ident, current=cur,
                              history=hist, derived=der)
    return out


def _selftest() -> None:
    class _FakeScored:
        def __init__(self, ppm, pj, pct_used, fix, status, pct_rest=None):
            self.ppm, self.pj, self.pct_used = ppm, pj, pct_used
            self.fix, self.status = fix, status
            self.pct_rest = pct_used if pct_rest is None else pct_rest

    class _FakeScorer:
        def row_for(self, k):
            return {"key": k} if k == "999" else None

        def score(self, row):
            return _FakeScored(ppm=6.0, pj=12.0, pct_used=80.0, fix=1.1,
                               status="ok", pct_rest=60.0) if row else None

    from ffcore.crosswalk import Player

    players = {"999": {"name": "Known Player", "pos": "DEL", "team": "betis"},
              "unknown": {"name": "Unknown Player", "pos": "MED",
                          "team": "celta"}}

    class _FakeXW:
        players = {"999": Player("999", "Known Player", club_id="betis",
                                 app_id="app-999", understat_id="us-999")}

    perjornada = [
        {"ff_id": "999", "player_name": "Known Player", "jornada": "1",
         "points_delta": "5", "games_delta": "1"},
        {"ff_id": "999", "player_name": "Known Player", "jornada": "2",
         "points_delta": "3", "games_delta": "1"},
    ]
    match_stats = [
        {"player_id": "app-999", "week": "1", "stat": "goals",
         "value": "1", "points": "4"},
        {"player_id": "app-999", "week": "1", "stat": "mins_played",
         "value": "90", "points": "2"},
    ]
    matches = [{"home": "betis", "away": "sevilla", "jornada": "1"},
              {"home": "celta", "away": "betis", "jornada": "2"}]
    profiles = build_profiles(players, _FakeScorer(), perjornada,
                              xw=_FakeXW(), match_stats_rows=match_stats,
                              match_rows=matches,
                              market_keyed={"999": {"listed": True,
                                                     "price": 5e6,
                                                     "owner": "alice"}})

    assert set(profiles) == {"999", "unknown"}, profiles
    k = profiles["999"]
    assert k.identity.app_id == "app-999", k.identity
    assert k.identity.understat_id == "us-999", k.identity
    assert k.identity.name == "Known Player"
    assert k.current.club == "betis" and k.current.listed is True
    assert k.current.price == 5e6 and k.current.owner == "alice"
    assert k.current.status == "ok"
    assert k.derived.ppm == 6.0 and k.derived.pj == 12.0
    assert abs(k.derived.start_p - 0.8) < 1e-9
    assert abs(k.derived.market_exp - 5.28) < 1e-9
    assert k.history.points_by_jornada == {1: 5.0, 2: 3.0}
    assert k.history.started_by_jornada == {1: True, 2: True}
    assert k.history.match_stats_by_jornada == {
        1: {"goals": (1.0, 4.0), "mins_played": (90.0, 2.0)}}, \
        k.history.match_stats_by_jornada
    assert k.history.opponent_by_jornada == {
        1: ("sevilla", True), 2: ("celta", False)}, \
        k.history.opponent_by_jornada

    this_j, rest = k.to_bootstrap_input()
    assert abs(this_j[0] - 6.6) < 1e-9 and abs(this_j[1] - 0.8) < 1e-9, this_j
    assert abs(rest[0] - 6.6) < 1e-9 and abs(rest[1] - 0.6) < 1e-9, rest

    from ffcore.score import DOUBT_FACTOR

    class _StatusScorer(_FakeScorer):
        def __init__(self, status):
            self.status = status

        def score(self, row):
            return _FakeScored(ppm=6.0, pj=12.0, pct_used=80.0, fix=1.1,
                               status=self.status, pct_rest=60.0) \
                if row else None

    susp_profiles = build_profiles(players, _StatusScorer("suspended"),
                                   perjornada, xw=_FakeXW(),
                                   match_stats_rows=match_stats,
                                   match_rows=matches,
                                   market_keyed={"999": {"listed": True,
                                                          "price": 5e6,
                                                          "owner": "alice"}})
    ks = susp_profiles["999"]
    assert ks.derived.start_p == 0.0, ks.derived.start_p
    assert ks.derived.market_exp == 0.0, ks.derived.market_exp
    assert ks.derived.ppm == 6.0
    susp_this, susp_rest = ks.to_bootstrap_input()
    assert abs(susp_this[0] - 6.6) < 1e-9 and susp_this[1] == 0.0, susp_this
    assert abs(susp_rest[0] - 6.6) < 1e-9 \
        and abs(susp_rest[1] - 0.6) < 1e-9, susp_rest

    doubt_profiles = build_profiles(players, _StatusScorer("doubt"),
                                    perjornada, xw=_FakeXW(),
                                    match_stats_rows=match_stats,
                                    match_rows=matches,
                                    market_keyed={"999": {"listed": True,
                                                           "price": 5e6,
                                                           "owner": "alice"}})
    kd = doubt_profiles["999"]
    assert abs(kd.derived.pts_now - 6.6 * DOUBT_FACTOR) < 1e-9, \
        kd.derived.pts_now
    assert kd.derived.start_p == 0.8, kd.derived.start_p
    doubt_this, doubt_rest = kd.to_bootstrap_input()
    assert abs(doubt_this[0] - 6.6 * DOUBT_FACTOR) < 1e-9, doubt_this
    assert abs(doubt_this[1] - 0.8) < 1e-9, doubt_this
    assert abs(doubt_rest[0] - 6.6) < 1e-9, doubt_rest

    u = profiles["unknown"]
    assert u.identity.player_id == "unknown" and u.current.club == "celta"
    assert u.derived.ppm is None and u.derived.market_exp is None
    assert u.current.listed is False and u.current.price is None
    assert u.history.points_by_jornada == {}
    assert u.to_bootstrap_input() == (UNSCORED_DEFAULT, UNSCORED_DEFAULT)

    rows2 = [{"ff_id": "", "player_name": "No Id Here", "jornada": "3",
             "points_delta": "7", "games_delta": "1"}]
    h2 = _perjornada_history(rows2)
    assert set(h2) == {"no id here"}, h2
    assert h2["no id here"].points_by_jornada == {3: 7.0}

    bad = [{"ff_id": "1", "player_name": "X", "jornada": "n/a",
           "points_delta": "1", "games_delta": "1"}]
    assert _perjornada_history(bad) == {}

    ms_bad = [{"player_id": "", "week": "1", "stat": "goals",
              "value": "1", "points": "4"},
             {"player_id": "5", "week": "x", "stat": "goals",
              "value": "1", "points": "4"},
             {"player_id": "5", "week": "1", "stat": "",
              "value": "1", "points": "4"}]
    assert _match_stats_history(ms_bad) == {}
    assert _match_stats_history([]) == {}

    op = _opponent_history([{"home": "betis", "away": "", "jornada": "1"},
                            {"home": "a", "away": "b", "jornada": "x"}])
    assert op == {}

    print("ffcore.profile self-test OK (23 cases)")


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        _selftest()
