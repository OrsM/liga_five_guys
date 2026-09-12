"""
ffcore.profile — one record per player, shaped by how its data actually
changes.

FOUR TIERS, NOT ONE FLAT RECORD, because the fields change on genuinely
different rhythms and collapsing them into one scalar-per-field record
either loses information a future forecasting variable will need (market
value's daily TREND, not just its latest number) or forces every consumer
to guess whether a field is a live current value or a stale historical one.

    identity  — changes essentially never (ids, names)
    current   — the latest observed value of something that drifts
                (club, status, price) — overwritten each run, not accumulated
    history   — time-indexed sequences, the source of truth for anything a
                NEW forecasting variable needs. Adding a candidate variable
                should mean adding a field here once, not touching every
                downstream consumer.
    derived   — pre-aggregated, cached, and ALWAYS reproducible from
                history + current. Never a second source of truth.

FORECAST EVERYONE, SHOW ONLY WHAT'S ACTIONABLE. This module has no market
gate at all — build_profiles() covers every player load_players() knows,
listed or not, owned or not. The market-restriction rule ("only show me
players I can buy") is a REPORT rule, not a data rule: see slate.py for
where it actually applies. decide.py already computed market_exp/start for
this same full pool before this module existed (the "Everyone the market
prices, scored the same way" loop in load()) — this formalizes that, it
does not invent scoring-everyone as a new idea.

NOT a rerun of reports/watchlist.md (deleted 2026-08-18, and Universe's own
docstring says why: "pretending [an unactionable player matters today] is
most of why this repo grew a watchlist nobody read"). The difference: the
full pool computed here never gets its own report surface. It only ever
feeds calibration and the numbers behind whichever players slate.py's
market gate actually lets through.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ffcore.text import norm

__all__ = ["PlayerIdentity", "PlayerCurrent", "PlayerHistory",
          "PlayerDerived", "PlayerProfile", "build_profiles"]


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
    """The latest observed value of something that drifts — overwritten
    each run, not accumulated. Reading this answers 'what's true right
    now,' never 'what was true on some past date.'"""
    club: str = ""
    pos: str = ""
    status: str = ""              # LaLiga's own playerStatus, latest
    market_value: float | None = None
    listed: bool = False          # True only if in api_market.csv's live rows
    price: float | None = None    # current market price, only if listed
    owner: str | None = None      # current squad owner, if any


@dataclass
class PlayerHistory:
    """Time-indexed sequences — the source of truth. A future forecasting
    variable reads from here, not from a fresh tidy-CSV round trip.

    match_stats_by_jornada and opponent_by_jornada are real fields with a
    real, currently-empty default: the data sources they need
    (api_player_{id}'s playerStats array; a materialized fixtures/odds/elo
    join) are not tidied/built yet. Documented as the concrete next step,
    not silently dropped — see docs/notes/profile.md once written.
    """
    points_by_jornada: dict[int, float] = field(default_factory=dict)
    started_by_jornada: dict[int, bool] = field(default_factory=dict)
    match_stats_by_jornada: dict[int, dict] = field(default_factory=dict)
    opponent_by_jornada: dict[int, tuple] = field(default_factory=dict)
    market_value_series: list = field(default_factory=list)
    understat_season: dict | None = None


@dataclass
class PlayerDerived:
    """Pre-aggregated, cached, and — this is the contract — always
    reproducible from PlayerHistory + PlayerCurrent. Never hand-edit a
    Derived field; recompute and compare against History when in doubt."""
    ppm: float | None = None      # score.py's shrunk points-per-match
    pj: float = 0.0                # evidence count behind ppm
    start_p: float | None = None   # P(start), next jornada
    market_exp: float | None = None  # expected points, next jornada = ppm*fix*start_p
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
        after) — the shape decide.py's base/base_rest dicts feed Bootstrap.

        Both share the same points side (ppm*fix — a rate this thin has no
        more evidence by jornada 10 than jornada 3); only the START side
        differs, because P(start) firms up once a player has current-
        season minutes even when his points rate doesn't. Replaces the
        manual field-picking that used to live inline in decide.load().
        """
        s = self.derived.scored
        if s is None:
            return UNSCORED_DEFAULT, UNSCORED_DEFAULT
        pts = max(0.0, s.ppm * s.fix)
        return ((pts, min(1.0, (s.pct_used or 0) / 100)),
               (pts, min(1.0, (s.pct_rest or 0) / 100)))


def _match_stats_history(rows) -> dict[str, dict[int, dict]]:
    """{app_id: {week: {stat: (value, points)}}} from api_stats.csv's own
    rows — real per-match data (mins played, goals, cards, marca_points),
    currently 118 players / weeks 1-6, real but PARTIAL pool: it comes from
    api_teams's embedded lastStats, which only ever carries a player who
    has been on one of this league's 5 squads, the same limited-coverage
    shape the bulk /players fix solved for identity — not solved here,
    since api_stats has no bulk equivalent. Honest partial data, not full
    pool, not silently claimed to be.

    Keyed by `player_id` as api_stats.csv itself stores it — LaLiga's own
    app_id (from playerMaster.id, sources.py's _stat_rows()), NOT the
    futbolfantasy ff_id _perjornada_history() keys on. Two genuinely
    different id spaces; build_profiles() joins each against the field
    that actually matches it (PlayerIdentity.app_id here, not the
    crosswalk key `k`).
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
                   xw=None, match_stats_rows=None,
                   market_keyed: dict | None = None) -> dict[str, "PlayerProfile"]:
    """{player key: PlayerProfile} for every player `load_players()` knows —
    the full pool, no market/ownership gate at all.

    `sc.score(sc.row_for(k))` is the SAME call decide.load()'s own
    "Everyone the market prices, scored the same way" loop already makes —
    this does not add new scoring, it formalizes the existing one into a
    typed record instead of three parallel dicts (`pos`, `market_exp`,
    `start`).

    `players` is `ffcore.tidy.load_players()`'s own dict shape —
    {key: {name, team, pos, value, delta_1d, start, status}} — which does
    NOT carry app_id/understat_id/club_id despite the similar name to the
    crosswalk. Those identity fields come from `xw` (ffcore.crosswalk's
    Crosswalk, e.g. `lg.xw`), keyed the same way (`xw.players[key]`).
    `xw=None` degrades to blank identity fields rather than guessing or
    crashing — a player load_players() knows about that the crosswalk
    hasn't resolved yet still gets a profile.

    `market_keyed`, if given, is {key: {"listed":, "price":, "owner":}} —
    the market/ownership facts decide.load() already computes elsewhere
    (market_routes(), lg.owner). Optional so this stays testable without
    constructing a full League/market for every case.

    `match_stats_rows`, if given, is api_stats.csv's own rows — real
    per-match data (mins played, goals, cards) for whichever 118-ish
    players have been on one of this league's squads, keyed by LaLiga's
    own app_id (see _match_stats_history()), not the ff_id
    perjornada_rows joins on.
    """
    histories = _perjornada_history(perjornada_rows)
    match_stats = _match_stats_history(match_stats_rows or [])
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
        )
        row = sc.row_for(k)
        s = sc.score(row) if row else None
        der = PlayerDerived(
            ppm=s.ppm if s else None,
            pj=s.pj if s else 0.0,
            start_p=(min(1.0, (s.pct_used or 0) / 100) if s else None),
            market_exp=(max(0.0, s.ppm * s.fix) *
                       min(1.0, (s.pct_used or 0) / 100)) if s else None,
            scored=s,
        )
        if s is not None:
            cur.status = s.status
        # perjornada.csv's `ff_id` is futbolfantasy's own numeric id — a
        # DIFFERENT space from ident.app_id (LaLiga's own). row_key()'s own
        # convention makes the crosswalk key `k` itself that numeric id
        # whenever a player's market row carried one, so `k` is the right
        # lookup, not app_id.
        hist = histories.get(k) or histories.get(norm(ident.name)) \
            or PlayerHistory()
        if ident.app_id in match_stats:
            hist.match_stats_by_jornada = match_stats[ident.app_id]
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

    # `load_players()`'s REAL shape — {name, team, pos, value, delta_1d,
    # start, status} — carries NO app_id/understat_id/club identity at all;
    # that's what `xw` supplies, keyed the same way. Key "999" for the
    # known player because row_key()'s own convention makes the crosswalk
    # key itself the numeric ff_id whenever a market row carried one — the
    # same key perjornada.csv's `ff_id` column uses, and the thing this
    # test is specifically checking gets joined correctly.
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
    # api_stats.csv is keyed by LaLiga's app_id ("app-999"), a DIFFERENT
    # space from perjornada's ff_id ("999") — this is specifically testing
    # that build_profiles() joins each source against the right field.
    match_stats = [
        {"player_id": "app-999", "week": "1", "stat": "goals",
         "value": "1", "points": "4"},
        {"player_id": "app-999", "week": "1", "stat": "mins_played",
         "value": "90", "points": "2"},
    ]
    profiles = build_profiles(players, _FakeScorer(), perjornada,
                              xw=_FakeXW(), match_stats_rows=match_stats,
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

    # to_bootstrap_input(): (this jornada, rest of season) — same points
    # side (ppm*fix = 6.0*1.1 = 6.6) both times, only the start side
    # differs (pct_used=80% now, pct_rest=60% fitted this run).
    this_j, rest = k.to_bootstrap_input()
    assert abs(this_j[0] - 6.6) < 1e-9 and abs(this_j[1] - 0.8) < 1e-9, this_j
    assert abs(rest[0] - 6.6) < 1e-9 and abs(rest[1] - 0.6) < 1e-9, rest

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

    print("ffcore.profile self-test OK (14 cases)")


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        _selftest()
