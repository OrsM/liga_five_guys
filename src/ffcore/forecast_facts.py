"""The forecast's facts (forecast), grown one at a time. Selectors: ppm_prior, knobs, forecast_rate, prognoses,
next_jornadas, fit_of, forecast_fit. They read only what the ingest layer exports (current, season_facts, db.prognosis,
stats.shrink); no fold of their own.

ppm_prior: one Prior a player_club row (the position's least-squares line of last season's points per match on ln(value),
at his value; the median of all fitted points when his position has no line).

knobs: the forecast's model constants as one Knobs, read at the call (a db.set_constants variant is seen).
forecast_rate: one Rate per player_club row: ppm (his points per match), picked (the chance he is picked), record (the
chance from his record alone) and listed_pct (whether his listing gives a start %).
prognoses: player -> Prognosis, for each flagged player (a status and a prognosis).
next_jornadas: club -> the jornada of its first fixture.
fit_of: the chance a player is fit for one fixture, from his Prognosis (None: fit).
forecast_fit: one FitRow per (player with a club, fixture of his club).
forecast_now: one NowRow per (player with a position and a club, fixture of his club still to play): his points if he plays
  and the chance he plays, from forecast_rate, prognoses, next_jornadas and club_rating.

Named rules, each tested:
  fit_needs_value_and_ten_matches     a last-season row joins the fit only if its player has a value above 0, a position
                                      other than "" and pj >= 10.
  position_line_needs_three           per position the line is least squares only if it has at least 3 rows and
                                      avg(x*x) > avg(x)*avg(x); else its slope is 0.0 (the position's mean y).
  no_value_means_position_mean        a player with no value has x NULL, counted as the position's mean x: his prior is the
                                      position's mean y.
  never_below_zero_else_null_median   the prior is max(line, 0.0); a player whose position has no fit (none of the position
                                      qualified, or position "") has a NULL line and gets the median y of all fitted rows.
  last_season_with_null_points        a qualifying row with no points is an error, not a skipped row.
  listing_beats_record                picked is pct / 100 whenever the listing has a pct, whatever his status; listed_pct says so.
  known_player_pull                   with no pct, picked and record are pick(base / 100, PICK_K, apps, squads), base NEUTRAL_START
                                      if he is listed or has any status (a player gone from the league: neutral), else ABSENT_START.
  no_record_means_prior               ppm pulls the prior by last season's points and matches first, then by this season's, each
                                      worth SHRINK_K matches; with no rows at all ppm is the prior, and no prior at all is 0.0.
  fit_kinds                           never 0; until 1 only for a fixture with no kick or whose kick date is on or after the date;
                                      doubt 0 before his jornada, 0.5 at it, 1 after; out 0 up to and at it; back 0 before it,
                                      1 from it; no prognosis 1. A prognosis with no jornada uses the club's next_jornadas entry.
  next_fixture_order                  the club's next fixture is the first by kick (a missing kick sorts after any kick), ties by
                                      the lower jornada.
  one_row_per_fixture                 a club with two fixtures in one jornada gives each of its players two FitRows; never a product.
  knobs_read_at_call                  knobs() sees db.set_constants(SHRINK_K=...) inside the context and the constant after it.
  fixture_by_position                 a goalkeeper or defender faces 1 / attack of the opponent, anyone else its defence; an
                                      opponent with no rating gives 1.0.
  pts_floor                           pts = max(0.0, ppm * fixture); p = min(1.0, q + p_lift * q * (1 - q)), so p is in [0, 1].
  coach_has_no_now                    a player with an empty position (a coach) has no NowRow."""
from __future__ import annotations

import json
import math
import random
import statistics
from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Any, NamedTuple

from ffcore import db
from ffcore import forecast
from ffcore import observations as ob
from ffcore import state
from ffcore.current import LIST_SOURCE, ClubRating, Identity, PlayerStatus, club_rating, player_club, player_status, player_value
from ffcore.season_facts import Fixture, LastSeason, SeasonRecord, club_fixture, last_season, pick, season_record
from stats import shrink

MIN_MATCHES = 10
MIN_FIT = 3


class Prior(NamedTuple):
    player: str
    prior: float | None


class _Point(NamedTuple):
    pos: str
    x: float
    y: float


class _Fit(NamedTuple):
    mean_y: float
    mean_x: float
    slope: float


def _fitted_points(world: state.World) -> tuple[_Point, ...]:
    pos = {c.player: c.pos for c in player_club(world)}
    value = {v.player: v.value for v in player_value(world)}
    out = []
    for r in last_season(world):
        if r.pj is None or r.pj < MIN_MATCHES or pos.get(r.ff_id, "") == "" or r.ff_id not in value:
            continue
        if r.pts is None:
            raise ValueError("last_season_with_null_points: %s has %s matches and no points" % (r.ff_id, r.pj))
        out.append(_Point(pos[r.ff_id], math.log(value[r.ff_id]), r.pts / r.pj))
    return tuple(out)


def _fit_by_position(points: Iterable[_Point]) -> dict[str, _Fit]:
    by_pos: dict[str, list[_Point]] = defaultdict(list)
    for p in points:
        by_pos[p.pos].append(p)
    out = {}
    for pos, rows in by_pos.items():
        n = len(rows)
        mx = sum(r.x for r in rows) / n
        my = sum(r.y for r in rows) / n
        xx = sum(r.x * r.x for r in rows) / n
        xy = sum(r.x * r.y for r in rows) / n
        slope = (xy - mx * my) / (xx - mx * mx) if n >= MIN_FIT and xx > mx * mx else 0.0
        out[pos] = _Fit(my, mx, slope)
    return out


def ppm_prior(world: state.World) -> tuple[Prior, ...]:
    """One Prior per current.player_club row, in no particular order (rules as in the module docstring)."""
    points = _fitted_points(world)
    fits = _fit_by_position(points)
    value = {v.player: v.value for v in player_value(world)}
    median = statistics.median(p.y for p in points) if points else None
    out = []
    for c in player_club(world):
        fit = fits.get(c.pos)
        if fit is None:
            out.append(Prior(c.player, median))
            continue
        x = value.get(c.player)
        dx = 0.0 if x is None else math.log(x) - fit.mean_x
        out.append(Prior(c.player, max(fit.mean_y + fit.slope * dx, 0.0)))
    return tuple(out)


class Knobs(NamedTuple):
    shrink_k: float
    neutral_start: float
    absent_start: float
    pick_k: float
    fixture_k: float
    p_lift: float
    p_listing: float
    p_decay: float
    min_pool: float
    persistent_share: float


def knobs() -> Knobs:
    """The forecast's model constants as they stand at the call (forecast.py's attributes, read now)."""
    return Knobs(forecast.SHRINK_K, forecast.NEUTRAL_START, forecast.ABSENT_START, forecast.PICK_K, forecast.FIXTURE_K,
                 forecast.P_LIFT, forecast.P_LISTING, forecast.P_DECAY, forecast.MIN_POOL, forecast.PERSISTENT_SHARE)


class Rate(NamedTuple):
    player: str
    ppm: float
    picked: float
    record: float
    listed_pct: bool


def _base(s: PlayerStatus | None, k: Knobs) -> float:
    flagged = s is not None and (s.listed or s.status is not None)
    return k.neutral_start if flagged else k.absent_start


def forecast_rate(world: state.World, identity: Identity, knobs: Knobs) -> tuple[Rate, ...]:
    """One Rate per current.player_club row (rules: listing_beats_record, known_player_pull, no_record_means_prior)."""
    prior = {p.player: p.prior for p in ppm_prior(world)}
    last = {r.ff_id: r for r in last_season(world)}
    record = {r.player: r for r in season_record(world, identity)}
    status = {s.player: s for s in player_status(world, identity)}
    out = []
    for c in player_club(world):
        s = status.get(c.player)
        r = record.get(c.player, SeasonRecord(c.player, None, 0.0, 0.0))
        lst = last.get(c.player, LastSeason(c.player, None, None))
        from_record = pick(r, _base(s, knobs), knobs.pick_k)
        from_last = shrink(prior.get(c.player) or 0.0, knobs.shrink_k, lst.pts or 0.0, lst.pj or 0.0)
        ppm = shrink(from_last, knobs.shrink_k, r.pts or 0.0, r.apps)
        listed_pct = s is not None and s.pct is not None
        picked = s.pct / 100.0 if s is not None and s.pct is not None else from_record
        out.append(Rate(c.player, ppm, picked, from_record, listed_pct))
    return tuple(out)


class Prognosis(NamedTuple):
    kind: str
    until: str | None
    j: int | None


def prognoses(world: state.World, identity: Identity) -> Mapping[str, Prognosis]:
    """Player -> his Prognosis, for each player_status row with a status and a prognosis (db.prognosis)."""
    out: dict[str, Prognosis] = {}
    for s in player_status(world, identity):
        raw = db.prognosis(s.status, s.note, s.at) if s.status is not None else None
        if raw is None:
            continue
        g = json.loads(raw)
        out[s.player] = Prognosis(g["kind"], g.get("until"), g.get("j"))
    return out


def _next_order(f: Fixture) -> tuple[bool, str, int]:
    return (f.kick is None, f.kick or "", f.jornada)


def next_jornadas(world: state.World) -> Mapping[str, int]:
    """Club -> the jornada of its first fixture (next_fixture_order)."""
    first: dict[str, Fixture] = {}
    for f in club_fixture(world):
        held = first.get(f.club)
        if held is None or _next_order(f) < _next_order(held):
            first[f.club] = f
    return {club: f.jornada for club, f in first.items()}


def fit_of(p: Prognosis | None, next_jornada: int, fixture: Fixture) -> float:
    """The chance a player is fit for one fixture (fit_kinds)."""
    if p is None:
        return 1.0
    if p.kind == "never":
        return 0.0
    if p.kind == "until":
        if fixture.kick is None:
            return 1.0
        return 1.0 if p.until is not None and fixture.kick[:10] >= p.until else 0.0
    j = next_jornada if p.j is None else p.j
    if p.kind == "doubt":
        return 0.0 if fixture.jornada < j else (0.5 if fixture.jornada == j else 1.0)
    if p.kind == "out":
        return 0.0 if fixture.jornada <= j else 1.0
    if p.kind == "back":
        return 0.0 if fixture.jornada < j else 1.0
    return 1.0


class FitRow(NamedTuple):
    player: str
    jornada: int
    fit: float


def forecast_fit(world: state.World, identity: Identity) -> tuple[FitRow, ...]:
    """One FitRow per (player with a club that has fixtures, fixture of that club) (one_row_per_fixture)."""
    prog = prognoses(world, identity)
    nxt = next_jornadas(world)
    fixtures: dict[str, list[Fixture]] = defaultdict(list)
    for f in club_fixture(world):
        fixtures[f.club].append(f)
    return tuple(FitRow(c.player, f.jornada, fit_of(prog.get(c.player), nxt[c.club], f))
                 for c in player_club(world) if c.club is not None
                 for f in fixtures.get(c.club, ()))


class NowRow(NamedTuple):
    player: str
    jornada: int
    pts: float
    p: float
    ppm: float
    fixture: float
    picked: float
    fit: float


def _fixture_factor(pos: str, opp: ClubRating | None) -> float:
    if opp is None:
        return 1.0
    return 1.0 / opp.attack if pos in ("POR", "DEF") else opp.defence


def forecast_now(world: state.World, identity: Identity, knobs: Knobs) -> tuple[NowRow, ...]:
    """One NowRow per (player with a position and a club, fixture of his club still to play): points if he plays,
    the chance he plays (rules: fixture_by_position, pts_floor, one_row_per_fixture, coach_has_no_now)."""
    rate = {r.player: r for r in forecast_rate(world, identity, knobs)}
    prog = prognoses(world, identity)
    nxt = next_jornadas(world)
    ratings = {r.club: r for r in club_rating(world, knobs.fixture_k)}
    fixtures: dict[str, list[Fixture]] = defaultdict(list)
    for f in club_fixture(world):
        fixtures[f.club].append(f)
    out = []
    for c in player_club(world):
        if c.pos == "" or c.club is None:
            continue
        r = rate[c.player]
        games = fixtures.get(c.club, [])
        first = min((f.jornada for f in games), default=0)
        for f in games:
            fit = fit_of(prog.get(c.player), nxt[c.club], f)
            fixture = _fixture_factor(c.pos, ratings.get(f.opp))
            w = knobs.p_listing * math.pow(knobs.p_decay, f.jornada - first)
            q = w * r.picked * (1.0 if r.listed_pct else fit) + (1.0 - w) * r.record * fit
            p = min(1.0, q + knobs.p_lift * q * (1.0 - q))
            out.append(NowRow(c.player, f.jornada, max(0.0, r.ppm * fixture), p, r.ppm, fixture, r.picked, fit))
    return tuple(out)


D0, D1, D2, D3, D4, D5 =("2026-08-13T0000Z", "2026-08-14T1000Z", "2026-08-16T1000Z", "2026-08-20T1000Z",
                          "2026-08-23T1000Z", "2026-08-30T1000Z")
CUTS = (D0, D1, D2, D3, D4)
FIT_Y = {"q1": 4.0, "q2": 3.0, "q3": 2.5, "qn": 1.0, "r1": 2.0, "r2": 4.0,
         "m1": 6.0, "m2": 3.0, "m3": 1.0, "m4": 0.0, "c1": 5.0}
LINE_VALUES = {"f1": 10.0, "f2": 100.0, "f3": 1000.0}
CLUBS = {"q1": "A", "q2": "B", "q3": "C", "qn": "D", "r1": "E", "r2": "F", "m1": "G", "m2": "H", "m3": "I",
         "m4": "J", "f1": "K", "f2": "L", "f3": "M", "c1": "N"}
IDENTITY = Identity(ff={(p, p.upper(), c): p for p, c in CLUBS.items()}, app={"api-m4": "m4"})
MATCHES_1 = (("AB", "A", "B", "2026-08-15T20:00:00+02:00"), ("CD", "C", "D", ""), ("EF", "E", "F", ""),
             ("GH", "G", "H", ""), ("IJ", "I", "J", ""), ("KL", "K", "L", ""), ("MN", "M", "N", ""))
MATCHES_2 = (("AC", "A", "C", "2026-08-22T19:00:00+00:00"), ("AD", "A", "D", "2026-08-23T12:00:00"),
             ("BE", "B", "E", "2026-08-22T21:00:00+00:00"), ("FG", "F", "G", ""), ("HI", "H", "I", ""),
             ("JK", "J", "K", ""), ("LM", "L", "M", ""))


def _mk(at: str, ff: str, value: float | None, position: str, club: str) -> ob.Market:
    return ob.Market(at, "s", value, ff, position, club)


def _p(at: str, ff: str, points: float | None, games: float, season: str) -> ob.Points:
    return ob.Points(at, "s", points, games, 0.0, ff, "", "", "", season)


def _league(at: str, por: tuple[float, float], q2: float) -> list[ob.Market]:
    return [_mk(at, "q1", 10.0, "DEL", "A"), _mk(at, "q2", q2, "DEL", "B"), _mk(at, "q3", 1000.0, "DEL", "C"),
            _mk(at, "qn", None, "DEL", "D"), _mk(at, "r1", por[0], "POR", "E"), _mk(at, "r2", por[1], "POR", "F"),
            _mk(at, "m1", 10.0, "MED", "G"), _mk(at, "m2", 100.0, "MED", "H"), _mk(at, "m3", 1000.0, "MED", "I"),
            _mk(at, "m4", 1e12, "MED", "J"), _mk(at, "f1", 10.0, "DEF", "K"), _mk(at, "f2", 100.0, "DEF", "L"),
            _mk(at, "f3", 1000.0, "DEF", "M"), _mk(at, "c1", 3.0, "Entrenador", "N")]


def _lu(at: str, p: str, pct: float | None, status: str, note: str) -> ob.Lineups:
    return ob.Lineups(at, LIST_SOURCE, pct, p, p.upper(), CLUBS[p], status, note)


def _m(at: str, mid: str, j: int, home: str, away: str, kickoff: str) -> ob.Matches:
    return ob.Matches(at, "s", j, mid, "", home, away, "", kickoff)


def _st(at: str, mid: str, team: str, slug: str, role: str, minute: float | None) -> ob.Starters:
    return ob.Starters(at, "s", minute, slug, slug.upper(), team, mid, role)


def _tables(por: tuple[float, float] = (5.0, 20.0)) -> dict[str, list[Any]]:
    y = dict(FIT_Y)
    y.update({ff: 1.0 + 0.5 * math.log(v) for ff, v in LINE_VALUES.items()})
    points = [_p(D1, ff, 10.0 * v, 10.0, "25") for ff, v in y.items()] + [_p(D1, "q1", 3.0, 3.0, "26")]
    lineups = [_lu(D1, "q1", None, "", ""), _lu(D1, "q2", 80.0, "ok", ""), _lu(D1, "r1", 40.0, "doubt", "Duda para la jornada 2")]
    matches = [_m(D1, mid, 1, h, a, k) for mid, h, a, k in MATCHES_1] + [_m(D1, mid, 2, h, a, k) for mid, h, a, k in MATCHES_2]
    starters = [_st(D3, "AB", "A", "q1", "starter", None), _st(D3, "AB", "B", "q2", "sub", 60.0),
                _st(D3, "EF", "E", "r1", "starter", 70.0)]
    api = [ob.ApiPlayersAll(D1, "s", None, "api-m4", "T", "J", "J", "out_of_league")]
    return {"market": _league(D1, por, 100.0) + _league(D3, por, 120.0), "points": points, "lineups": lineups,
            "matches": matches, "starters": starters, "api_players_all": api,
            "results_history": [ob.ResultsHistory(D1, "fd", hg, ag, "s", day, "h" + h, "h" + a, h, a)
                                for h, a, hg, ag, day in (("A", "B", 2.0, 0.0, "d1"), ("B", "A", 1.0, 1.0, "d2"),
                                                          ("E", "F", 0.0, 3.0, "d3"), ("C", "D", 1.0, 1.0, "d4"),
                                                          ("G", "H", 2.0, 2.0, "d5"))]}


def _scratch() -> ob.Observations:
    return ob.of(_tables())


def _view(world: state.World) -> Any:
    return (world.cut, ppm_prior(world), forecast_rate(world, IDENTITY, knobs()), forecast_fit(world, IDENTITY),
            forecast_now(world, IDENTITY, knobs()))


def _priors(world: state.World) -> dict[str, float]:
    out = {}
    for p in ppm_prior(world):
        assert p.prior is not None, p.player
        out[p.player] = p.prior
    return out


def _rates(world: state.World) -> dict[str, Rate]:
    return {r.player: r for r in forecast_rate(world, IDENTITY, knobs())}


def _selftest() -> None:
    """The named rules on a small league; later rows leave a cut unchanged, any subset or order of cuts gives each
    the same view, and a cut alone equals the cut inside a sweep."""
    obs = _scratch()
    views = {c: _view(state.world_at(obs, c)) for c in CUTS}
    assert state.sweep(obs, CUTS, _view) == [views[c] for c in CUTS]
    rng = random.Random(7)
    for _ in range(8):
        picked = [rng.choice(CUTS) for _ in range(rng.randint(1, 8))]
        assert state.sweep(obs, picked, _view) == [views[c] for c in picked]
    later = _tables()
    later["points"].append(_p(D5, "q2", 1.0, 10.0, "25"))
    later["lineups"].append(_lu(D5, "m4", 5.0, "doubt", "Duda para la jornada 9"))
    assert all(_view(state.world_at(ob.of(later), c)) == views[c] for c in CUTS)

    assert ppm_prior(state.world_at(obs, D0)) == () and len(views[D1][1]) == len(set(p.player for p in views[D1][1]))
    priors = _priors(state.world_at(obs, D1))
    assert set(priors) == {c.player for c in player_club(state.world_at(obs, D1))}

    for ff, v in LINE_VALUES.items():
        assert abs(priors[ff] - (1.0 + 0.5 * math.log(v))) < 1e-9, "a perfect line is reproduced"
    assert priors["r1"] == priors["r2"] == 3.0, "two qualifying players: the position's mean"
    assert abs(priors["qn"] - (4.0 + 3.0 + 2.5) / 3) < 1e-12, "no value: the position's mean (his own 10 matches are not fitted)"
    assert priors["m4"] == 0.0 and priors["m1"] > 0.0, "a line below zero is 0.0"
    assert all(p >= 0.0 for p in priors.values() if p is not None)
    fitted = [FIT_Y[ff] for ff in FIT_Y if ff not in ("qn", "c1")] + [1.0 + 0.5 * math.log(v) for v in LINE_VALUES.values()]
    assert abs(priors["c1"] - statistics.median(fitted)) < 1e-12, "a coach: the median of all fitted points"

    with_new = _tables()
    with_new["market"] += [_mk(D1, "pn", 7.0, "DEL", "P"), _mk(D3, "pn", 7.0, "DEL", "P")]
    with_new["points"].append(_p(D1, "pn", 9.0, 9.0, "25"))
    grown = _priors(state.world_at(ob.of(with_new), D1))
    assert "pn" in grown and all(grown[k] == priors[k] for k in priors), "a last-season row of 9 matches is not fitted"

    other = _priors(state.world_at(ob.of(_tables(por=(50.0, 200.0))), D1))
    assert all(other[k] == priors[k] for k in priors if k not in ("r1", "r2")), "another position's data is not used"

    broken = _tables()
    broken["points"].append(_p(D1, "q3", None, 10.0, "25"))
    try:
        ppm_prior(state.world_at(ob.of(broken), D1))
    except ValueError:
        pass
    else:
        raise AssertionError("a qualifying row with no points must not be skipped")

    w1 = state.world_at(obs, D1)
    rate = _rates(w1)
    rec = {r.player: r for r in season_record(w1, IDENTITY)}
    k = knobs()

    def rec_of(p: str) -> SeasonRecord:
        return rec.get(p, SeasonRecord(p, None, 0.0, 0.0))

    assert len(rate) == len(player_club(w1)) and all(0.0 <= x.picked <= 1.0 and 0.0 <= x.record <= 1.0 for x in rate.values())
    assert rate["q2"].listed_pct and rate["q2"].picked == 0.8 and rate["q2"].record == pick(rec_of("q2"), k.neutral_start, k.pick_k)
    assert rate["r1"].listed_pct and rate["r1"].picked == 0.4 and rate["r1"].record == pick(rec_of("r1"), k.neutral_start, k.pick_k), \
        "listing_beats_record: a doubtful player with a pct is picked at the pct, his record still from neutral"
    assert not rate["q1"].listed_pct and rate["q1"].picked == rate["q1"].record == pick(rec_of("q1"), k.neutral_start, k.pick_k), \
        "known_player_pull: listed with no pct"
    assert rate["f1"].picked == rate["f1"].record == pick(rec_of("f1"), k.absent_start, k.pick_k), "not listed: absent start"
    assert rate["m4"].record == pick(rec_of("m4"), k.neutral_start, k.pick_k), "gone from the league: neutral start"

    assert abs(rate["f1"].ppm - priors["f1"]) < 1e-12 and rec_of("f1").apps == 0.0, "no_record_means_prior (no last season, no record)"
    no_points = state.world_at(ob.of({"market": _tables()["market"]}), D1)
    assert all(x.ppm == 0.0 for x in forecast_rate(no_points, IDENTITY, k)), "no prior at all: ppm 0.0"

    last = {r.ff_id: r for r in last_season(w1)}
    for p, x in rate.items():
        hull = [priors[p]]
        lp = last.get(p)
        if lp is not None and lp.pts is not None and lp.pj:
            hull.append(lp.pts / lp.pj)
        rp = rec_of(p)
        if rp.pts is not None and rp.apps > 0:
            hull.append(rp.pts / rp.apps)
        assert min(hull) - 1e-9 <= x.ppm <= max(hull) + 1e-9, "ppm is a convex combination of its prior and its records"

    t2 = _tables()
    t2["points"].append(_p(D3, "m2", 4.0, 2.0, "26"))
    base3, grown3 = _rates(state.world_at(obs, D3)), _rates(state.world_at(ob.of(t2), D3))
    assert all(base3[p] == grown3[p] for p in base3 if p != "m2"), "a season record row changes no other player's Rate"

    t3 = _tables()
    t3["lineups"] = [x._replace(start_pct=None) if x.player_slug == "q2" else x for x in t3["lineups"]]
    assert _rates(state.world_at(ob.of(t3), D1))["q2"].record == rate["q2"].record, "record does not depend on the pct"

    nxt = next_jornadas(w1)
    assert nxt["A"] == 1 and nxt["C"] == 2 and nxt["D"] == 2 and nxt["E"] == 2, "next_fixture_order: kick None sorts last"
    fits = forecast_fit(w1, IDENTITY)
    assert all(0.0 <= f.fit <= 1.0 for f in fits)
    assert sorted(f.jornada for f in fits if f.player == "q1") == [1, 2, 2], "one_row_per_fixture: A's two fixtures in jornada 2"
    assert sorted((f.jornada, f.fit) for f in fits if f.player == "r1") == [(1, 0.0), (2, 0.5)], "doubt with j 2"
    assert {f.fit for f in fits if f.player == "m4"} == {0.0}, "gone: never"

    fx = Fixture(2, "A", "C", None)
    assert fit_of(None, 1, fx) == 1.0 and fit_of(Prognosis("never", None, None), 1, fx) == 0.0
    assert [fit_of(Prognosis("doubt", None, 3), 1, Fixture(j, "A", "C", None)) for j in (2, 3, 4)] == [0.0, 0.5, 1.0]
    assert [fit_of(Prognosis("out", None, 3), 1, Fixture(j, "A", "C", None)) for j in (2, 3, 4)] == [0.0, 0.0, 1.0]
    assert [fit_of(Prognosis("back", None, 3), 1, Fixture(j, "A", "C", None)) for j in (2, 3, 4)] == [0.0, 1.0, 1.0]
    assert fit_of(Prognosis("doubt", None, None), 4, Fixture(4, "A", "C", None)) == 0.5, "no jornada: the club's next"
    until = Prognosis("until", "2026-09-01", None)
    assert fit_of(until, 1, Fixture(1, "A", "C", "2026-08-30T1800Z")) == 0.0
    assert fit_of(until, 1, Fixture(1, "A", "C", "2026-09-02T1800Z")) == 1.0
    assert fit_of(until, 1, Fixture(1, "A", "C", None)) == 1.0

    with db.set_constants(SHRINK_K=1.0):
        assert knobs().shrink_k == 1.0
    assert knobs().shrink_k == forecast.SHRINK_K

    now = forecast_now(w1, IDENTITY, k)
    ratings = {r.club: r for r in club_rating(w1, k.fixture_k)}
    assert "c1" not in {n.player for n in now}, "coach_has_no_now"
    for n in now:
        assert 0.0 <= n.p <= 1.0 and n.pts >= 0.0, "pts_floor"
        assert n.pts == max(0.0, n.ppm * n.fixture)
    assert {n.player for n in now} == {c.player for c in player_club(w1) if c.pos != "" and c.club is not None}
    at_j1 = {(n.player, n.jornada): n.fixture for n in now if n.player in ("q1", "r1", "m3")}
    assert at_j1[("q1", 1)] == ratings["B"].defence, "fixture_by_position: a defender (DEL) faces the defence of B"
    assert at_j1[("r1", 1)] == 1.0 / ratings["F"].attack, "fixture_by_position: a goalkeeper faces 1 / attack of F"
    assert at_j1[("m3", 1)] == 1.0, "fixture_by_position: an opponent with no rating gives 1.0 (J is unrated)"
    q1_two = sorted(n.fixture for n in now if n.player == "q1" and n.jornada == 2)
    assert q1_two == sorted([ratings["C"].defence, ratings["D"].defence]), "one_row_per_fixture: two rows in jornada 2"
    with db.set_constants(P_LIFT=0.5):
        lifted = forecast_now(w1, IDENTITY, knobs())
    base_by = {(n.player, n.jornada, n.fixture): n for n in now}
    assert all(n.p >= base_by[(n.player, n.jornada, n.fixture)].p - 1e-12 for n in lifted), "raising P_LIFT never lowers p"
    t4 = _tables()
    t4["lineups"] = [x._replace(start_pct=None) if x.player_slug == "q2" else x for x in t4["lineups"]]
    moved = forecast_now(state.world_at(ob.of(t4), D1), IDENTITY, k)
    assert [n for n in now if n.player != "q2"] == [n for n in moved if n.player != "q2"], (
        "a player's rows do not change when another player's line-up row changes")
    print("ffcore.forecast_facts self-test OK")


if __name__ == "__main__":
    _selftest()
