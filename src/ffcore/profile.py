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
    ff_id: str = ""             # futbolfantasy's id (market.csv)
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


@dataclass
class PlayerProfile:
    identity: PlayerIdentity
    current: PlayerCurrent
    history: PlayerHistory
    derived: PlayerDerived


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
                   market_keyed: dict | None = None) -> dict[str, "PlayerProfile"]:
    """{player key: PlayerProfile} for every player `load_players()` knows —
    the full pool, no market/ownership gate at all.

    `sc.score(sc.row_for(k))` is the SAME call decide.load()'s own
    "Everyone the market prices, scored the same way" loop already makes —
    this does not add new scoring, it formalizes the existing one into a
    typed record instead of three parallel dicts (`pos`, `market_exp`,
    `start`).

    `market_keyed`, if given, is {key: {"listed":, "price":, "owner":}} —
    the market/ownership facts decide.load() already computes elsewhere
    (market_routes(), lg.owner). Optional so this stays testable without
    constructing a full League/market for every case.
    """
    histories = _perjornada_history(perjornada_rows)
    out: dict[str, PlayerProfile] = {}
    for k, rec in players.items():
        ident = PlayerIdentity(
            key=k,
            app_id=rec.get("app_id") or "",
            ff_id=rec.get("player_id") or "",
            understat_id=rec.get("understat_id") or "",
            name=rec.get("name") or k,
            full_name=rec.get("name") or k,
        )
        mk = (market_keyed or {}).get(k, {})
        cur = PlayerCurrent(
            club=rec.get("club_id") or "",
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
        hist = histories.get(rec.get("player_id") or "") or \
            histories.get(norm(ident.name)) or PlayerHistory()
        out[k] = PlayerProfile(identity=ident, current=cur,
                              history=hist, derived=der)
    return out


def _selftest() -> None:
    # -- identity/current/derived, from a minimal fake Scorer ---------------
    class _FakeScored:
        def __init__(self, ppm, pj, pct_used, fix, status):
            self.ppm, self.pj, self.pct_used = ppm, pj, pct_used
            self.fix, self.status = fix, status

    class _FakeScorer:
        def row_for(self, k):
            return {"key": k} if k == "known" else None

        def score(self, row):
            return _FakeScored(ppm=6.0, pj=12.0, pct_used=80.0, fix=1.1,
                               status="ok") if row else None

    players = {"known": {"name": "Known Player", "pos": "DEL",
                         "club_id": "betis", "player_id": "999",
                         "app_id": "999"},
              "unknown": {"name": "Unknown Player", "pos": "MED",
                          "club_id": "celta"}}
    perjornada = [
        {"ff_id": "999", "player_name": "Known Player", "jornada": "1",
         "points_delta": "5", "games_delta": "1"},
        {"ff_id": "999", "player_name": "Known Player", "jornada": "2",
         "points_delta": "3", "games_delta": "1"},
    ]
    profiles = build_profiles(players, _FakeScorer(), perjornada,
                              market_keyed={"known": {"listed": True,
                                                       "price": 5e6,
                                                       "owner": "alice"}})

    assert set(profiles) == {"known", "unknown"}, profiles
    k = profiles["known"]
    assert k.identity.app_id == "999" and k.identity.name == "Known Player"
    assert k.current.club == "betis" and k.current.listed is True
    assert k.current.price == 5e6 and k.current.owner == "alice"
    assert k.current.status == "ok"
    assert k.derived.ppm == 6.0 and k.derived.pj == 12.0
    assert abs(k.derived.start_p - 0.8) < 1e-9
    # market_exp = ppm * fix * start_p = 6.0 * 1.1 * 0.8
    assert abs(k.derived.market_exp - 5.28) < 1e-9
    assert k.history.points_by_jornada == {1: 5.0, 2: 3.0}
    assert k.history.started_by_jornada == {1: True, 2: True}

    # A player the Scorer has no row for at all: not scored, not dropped —
    # still gets a profile, just with empty derived/history. This IS the
    # full-pool guarantee: nobody vanishes for lack of a market row.
    u = profiles["unknown"]
    assert u.identity.key == "unknown" and u.current.club == "celta"
    assert u.derived.ppm is None and u.derived.market_exp is None
    assert u.current.listed is False and u.current.price is None
    assert u.history.points_by_jornada == {}

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

    print("ffcore.profile self-test OK (12 cases)")


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        _selftest()
