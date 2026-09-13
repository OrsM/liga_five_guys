"""
ffcore.profile — one record per player, keyed by how its data changes:

    identity  — immutable ids/names
    current   — latest observed value of a drifting fact (club, status,
                price) — overwritten each run, not accumulated
    history   — time-indexed sequences; add a new forecasting variable
                here once, not at every consumer
    derived   — pre-aggregated, cached, always reproducible from
                history + current. Never a second source of truth.

build_profiles() covers every player load_players() knows, listed or
not, owned or not — no market gate. The market-restriction rule ("only
show me players I can buy") is a report rule, applied in slate.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ffcore.text import norm

__all__ = ["PlayerIdentity", "PlayerCurrent", "PlayerHistory",
          "PlayerDerived", "PlayerProfile", "build_profiles",
          "status_adjusted"]


@dataclass
class PlayerIdentity:
    """Changes essentially never. Write once, read forever."""
    key: str                    # crosswalk player_id — the join key everything else uses
    app_id: str = ""            # LaLiga's own id
    # `key` itself IS futbolfantasy's numeric id whenever a player's market
    # row ever carried one (row_key()'s own convention) — there is no
    # separate numeric field to hold; ff_slug is the distinct, genuinely
    # separate piece of identity futbolfantasy actually adds.
    ff_slug: str = ""
    understat_id: str = ""
    name: str = ""
    full_name: str = ""


@dataclass
class PlayerCurrent:
    """Latest observed value of a drifting fact — overwritten each run,
    not accumulated. decide.load() computes value/clause/clause_until/
    route/bids/proceeds (needs market/ledger context this module doesn't
    have) and writes the result here; Universe's own fields are reads of
    this, not a second computation.
    """
    club: str = ""
    pos: str = ""
    status: str = ""              # LaLiga's own playerStatus, latest
    market_value: float | None = None
    listed: bool = False          # True only if in api_market.csv's live rows
    price: float | None = None    # current market price, only if listed
    owner: str | None = None      # current squad owner, if any
    value: float | None = None    # what the app says he's worth (sale payout)
    clause: float | None = None   # his buyout clause, whoever holds him
    clause_until: object = None   # datetime a locked clause reopens, if locked
    route: str | None = None      # "free" / "listed" / "clause" / None (not on the market)
    bids: int | None = None       # rival bids on a "listed" row; 0 if none, None if not listed
    proceeds: float | None = None  # what selling him raises, ME only


@dataclass
class PlayerHistory:
    """Time-indexed sequences — the source of truth for forecasting
    variables; add a new one here rather than a fresh tidy-CSV read.

    match_stats_by_jornada: from api_stats.csv, PARTIAL pool (~118
    players who've been on one of this league's 5 squads).
    opponent_by_jornada: (opponent_club, is_home) per jornada, FULL pool,
    from matches.csv. No difficulty yet — needs the elo.csv/club_id
    case-mismatch join decide.py's season_board()/club_key() already do.
    market_value_series/understat_season: real fields, not wired to a
    source yet.
    """
    points_by_jornada: dict[int, float] = field(default_factory=dict)
    started_by_jornada: dict[int, bool] = field(default_factory=dict)
    match_stats_by_jornada: dict[int, dict] = field(default_factory=dict)
    opponent_by_jornada: dict[int, tuple] = field(default_factory=dict)
    market_value_series: list = field(default_factory=list)
    understat_season: dict | None = None


def status_adjusted(pts: float, p_start: float, status: str
                     ) -> tuple[float, float]:
    """(pts, p_start) with Scorer.score()'s own status override applied:
    OUT_STATUSES forces p_start to 0, "doubt" halves pts. Scorer.score()
    already applies this to score/flat but leaves Scored.pct_used/ppm
    untouched by design — every other reader of (pts, p_start) must call
    this rather than rebuild the pair from raw fields.
    """
    from ffcore.score import OUT_STATUSES, DOUBT_FACTOR

    if status in OUT_STATUSES:
        return pts, 0.0
    if status == "doubt":
        return pts * DOUBT_FACTOR, p_start
    return pts, p_start


@dataclass
class PlayerDerived:
    """Pre-aggregated, cached, and — this is the contract — always
    reproducible from PlayerHistory + PlayerCurrent. Never hand-edit a
    Derived field; recompute and compare against History when in doubt."""
    ppm: float | None = None      # score.py's shrunk points-per-match
    pj: float = 0.0                # evidence count behind ppm
    start_p: float | None = None   # P(start), next jornada — status-adjusted
    market_exp: float | None = None  # expected points, next jornada = pts_now*start_p
    # Status-adjusted points-if-he-plays, next jornada only. Computed
    # once here by build_profiles(); to_bootstrap_input() reads this
    # field rather than recomputing it.
    pts_now: float | None = None
    scored: object = None          # the cached score.py Scored NamedTuple


# The neutral guess for a player Scorer has no row for at all — 2.0 points,
# 50% to start. Not a real estimate, a deliberately unopinionated default so
# an unscored player still has a legal (pts, p_start) pair to feed
# Bootstrap, rather than being silently dropped from the simulation.
# Matches the literal decide.load() already used before this module existed.
UNSCORED_DEFAULT = (2.0, 0.5)


@dataclass
class PlayerProfile:
    identity: PlayerIdentity
    current: PlayerCurrent
    history: PlayerHistory
    derived: PlayerDerived

    def to_bootstrap_input(self) -> tuple[tuple[float, float],
                                          tuple[float, float]]:
        """((pts, p_start) this jornada, (pts, p_start) every jornada
        after) — the shape decide.py's base/base_rest dicts feed
        Bootstrap.

        This jornada reads derived.pts_now/derived.start_p directly —
        build_profiles() is the only place that computes the
        status-adjusted pair; do not recompute it here.

        Rest-of-season is a separate, deliberately unadjusted figure:
        ppm*fix with no status override (a one-match ban doesn't carry
        to jornada 8) and pct_rest instead of pct_used.
        """
        s = self.derived.scored
        if s is None:
            return UNSCORED_DEFAULT, UNSCORED_DEFAULT
        pts_rest = max(0.0, s.ppm * s.fix)
        p_rest = min(1.0, (s.pct_rest or 0) / 100)
        return ((self.derived.pts_now, self.derived.start_p),
               (pts_rest, p_rest))


def _match_stats_history(rows) -> dict[str, dict[int, dict]]:
    """{app_id: {week: {stat: (value, points)}}} from api_stats.csv —
    mins played/goals/cards/marca_points, PARTIAL pool (only players
    who've been on one of this league's 5 squads; api_stats has no bulk
    equivalent). Keyed by LaLiga's own app_id (playerMaster.id), NOT the
    futbolfantasy ff_id _perjornada_history() uses — build_profiles()
    joins each against PlayerIdentity.app_id, not the crosswalk key.
    """
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
    """{club: {jornada: (opponent_club, is_home)}} from matches.csv's own
    jornada/home/away columns. No difficulty yet — needs the elo.csv
    proper-case-vs-slug join decide.py's season_board()/club_key()
    already solve.
    """
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
    """{key: PlayerHistory} from perjornada_2026-27.csv's own rows.

    Keyed the same way row_key() would key a market row: the site's own id
    (`ff_id`) when the row carries one, `norm(name)` otherwise — matching
    convention everywhere else in this repo joins on, not a fifth scheme.
    """
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
    """{player key: PlayerProfile} for every player `load_players()`
    knows — the full pool, no market/ownership gate.

    `players` is ffcore.tidy.load_players()'s shape — {key: {name, team,
    pos, value, delta_1d, start, status}}, no app_id/understat_id/
    club_id. Those come from `xw` (a Crosswalk, e.g. lg.xw), keyed the
    same way; `xw=None` degrades to blank identity fields rather than
    guessing.

    `market_keyed`, if given: {key: {"listed":, "price":, "owner":,
    "value":, "clause":, "clause_until":, "route":, "bids":, "proceeds":}}
    — decide.load()'s own market/ownership/ledger facts. Every key but
    "listed" is optional; a missing one leaves the matching PlayerCurrent
    field at its default (None).

    `match_stats_rows`, if given, is api_stats.csv's rows, keyed by
    LaLiga's app_id (see _match_stats_history()) — not the ff_id
    perjornada_rows joins on.
    """
    histories = _perjornada_history(perjornada_rows)
    match_stats = _match_stats_history(match_stats_rows or [])
    opponents = _opponent_history(match_rows or [])
    out: dict[str, PlayerProfile] = {}
    for k, rec in players.items():
        xp = xw.players.get(k) if xw is not None else None
        ident = PlayerIdentity(
            key=k,
            app_id=(xp.app_id if xp else "") or "",
            ff_slug=(xp.ff_slug if xp else "") or "",
            understat_id=(xp.understat_id if xp else "") or "",
            name=rec.get("name") or (xp.name if xp else "") or k,
            full_name=rec.get("name") or (xp.name if xp else "") or k,
        )
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
        # perjornada.csv's ff_id is a different id space from
        # ident.app_id; `k` (row_key()'s convention) is the right lookup.
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
    # -- identity/current/derived, from a minimal fake Scorer ---------------
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

    # load_players()'s shape carries no identity fields; xw supplies
    # those. "999" doubles as the ff_id perjornada.csv's own column uses.
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
    # api_stats.csv's app_id ("app-999") is a different space from
    # perjornada's ff_id ("999") — checks each source joins its own field.
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
    # market_exp = ppm * fix * start_p = 6.0 * 1.1 * 0.8
    assert abs(k.derived.market_exp - 5.28) < 1e-9
    assert k.history.points_by_jornada == {1: 5.0, 2: 3.0}
    assert k.history.started_by_jornada == {1: True, 2: True}
    assert k.history.match_stats_by_jornada == {
        1: {"goals": (1.0, 4.0), "mins_played": (90.0, 2.0)}}, \
        k.history.match_stats_by_jornada
    # Betis (his club, home j1 vs sevilla, away j2 at celta).
    assert k.history.opponent_by_jornada == {
        1: ("sevilla", True), 2: ("celta", False)}, \
        k.history.opponent_by_jornada

    # to_bootstrap_input(): (this jornada, rest of season) — same points
    # side (ppm*fix = 6.0*1.1 = 6.6) both times, only the start side
    # differs (pct_used=80% now, pct_rest=60% fitted this run).
    this_j, rest = k.to_bootstrap_input()
    assert abs(this_j[0] - 6.6) < 1e-9 and abs(this_j[1] - 0.8) < 1e-9, this_j
    assert abs(rest[0] - 6.6) < 1e-9 and abs(rest[1] - 0.6) < 1e-9, rest

    # Suspended/injured/unavailable and doubt, through the real pipeline
    # (build_profiles() -> to_bootstrap_input()).
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
    assert ks.derived.ppm == 6.0   # the raw rate itself is untouched
    susp_this, susp_rest = ks.to_bootstrap_input()
    assert abs(susp_this[0] - 6.6) < 1e-9 and susp_this[1] == 0.0, susp_this
    assert abs(susp_rest[0] - 6.6) < 1e-9 \
        and abs(susp_rest[1] - 0.6) < 1e-9, susp_rest  # untouched

    # DOUBT: halves the POINTS side for this jornada only (the same
    # multiplier Scorer.score() applies to flat/score), not the start
    # side — a 50-50 knock is a real chance to play, not a zero.
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
    assert kd.derived.start_p == 0.8, kd.derived.start_p  # unaffected
    doubt_this, doubt_rest = kd.to_bootstrap_input()
    assert abs(doubt_this[0] - 6.6 * DOUBT_FACTOR) < 1e-9, doubt_this
    assert abs(doubt_this[1] - 0.8) < 1e-9, doubt_this  # start% unaffected
    assert abs(doubt_rest[0] - 6.6) < 1e-9, doubt_rest  # untouched

    # A player the Scorer has no row for at all: not scored, not dropped —
    # still gets a profile, just with empty derived/history. This IS the
    # full-pool guarantee: nobody vanishes for lack of a market row.
    u = profiles["unknown"]
    assert u.identity.key == "unknown" and u.current.club == "celta"
    assert u.derived.ppm is None and u.derived.market_exp is None
    assert u.current.listed is False and u.current.price is None
    assert u.history.points_by_jornada == {}
    # No Scored at all -> the same neutral default decide.load() always
    # used for an unscored player, both jornada views identical.
    assert u.to_bootstrap_input() == (UNSCORED_DEFAULT, UNSCORED_DEFAULT)

    # -- _perjornada_history: falls back to norm(name) with no ff_id -------
    rows2 = [{"ff_id": "", "player_name": "No Id Here", "jornada": "3",
             "points_delta": "7", "games_delta": "1"}]
    h2 = _perjornada_history(rows2)
    assert set(h2) == {"no id here"}, h2
    assert h2["no id here"].points_by_jornada == {3: 7.0}

    # A row with a non-integer jornada/points is skipped, not fatal.
    bad = [{"ff_id": "1", "player_name": "X", "jornada": "n/a",
           "points_delta": "1", "games_delta": "1"}]
    assert _perjornada_history(bad) == {}

    # -- _match_stats_history: bad rows skipped, blank id/stat ignored -----
    ms_bad = [{"player_id": "", "week": "1", "stat": "goals",
              "value": "1", "points": "4"},
             {"player_id": "5", "week": "x", "stat": "goals",
              "value": "1", "points": "4"},
             {"player_id": "5", "week": "1", "stat": "",
              "value": "1", "points": "4"}]
    assert _match_stats_history(ms_bad) == {}
    assert _match_stats_history([]) == {}

    # -- _opponent_history: bad jornada skipped, blank club skipped --------
    op = _opponent_history([{"home": "betis", "away": "", "jornada": "1"},
                            {"home": "a", "away": "b", "jornada": "x"}])
    assert op == {}

    print("ffcore.profile self-test OK (23 cases)")


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        _selftest()
