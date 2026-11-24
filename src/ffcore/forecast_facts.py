"""The forecast's facts (forecast), grown one at a time. They read only what the ingest layer exports (current,
season_facts, stats.shrink); no fold of their own.

inputs: the shared pieces of one cut as one Inputs, each built once and eagerly (the only place the ingest selectors are
called for this group); the selectors are pure functions of an Inputs and call no other selector. forecast_now reads
the Rate of every player from Inputs.rate, not from forecast_rate.
knobs: the forecast's model constants as one Knobs, read at the call (a forecast.set_constants override is seen).
ppm_prior: one Prior a player_club row (the position's least-squares line of last season's points per match on ln(value),
at his value; the median of all fitted points when his position has no line).
forecast_rate: one Rate per player_club row: ppm (his points per match), picked (the chance he is picked), record (the
chance from his record alone) and listed_pct (whether his listing gives a start %).
fit_of: the chance a player is fit for one fixture, from his Prognosis (None: fit).
forecast_fit: one FitRow per (player with a club, fixture of his club).
forecast_now: one NowRow per (player with a position and a club, fixture of his club still to play): his points if he plays
  and the chance he plays. It returns Inputs.now, built in inputs() by _now_rows.
late_arrivals: one Late per (player, jornada) in Inputs.late, built in inputs() by _late from listing_close.
forecast_used: the Used rows of Inputs.used, forecast_now with the late pairs zeroed (built by _used).
forecast_now, late_arrivals and forecast_used read Inputs.now, Inputs.late and Inputs.used; no selector calls another.
spread: the Spread of one cut, from season_facts.played_points and forecast.SEED_POOL, in Inputs.spread (by _spread).
held: the players in the latest squads or listings, in Inputs.held (by _held).
pool: the PoolCell rows (player rows and each position's stand-in) of one cut, in Inputs.pool (by _pool).
score_spread, held, cell_pool, cell and cell_total read Inputs.spread, Inputs.held and Inputs.pool; cell and cell_total are
  selectors over Inputs.pool (both through _player_cells).

Named rules, each tested:
  fit_needs_value_and_ten_matches     a last-season row joins the fit only if its player has a value above 0, a position
                                      other than "" and pj >= 10.
  position_line_needs_three           per position the line is least squares only if it has at least 3 rows and
                                      avg(x*x) > avg(x)*avg(x); else its slope is 0.0 (the position's mean y).
  no_value_means_position_mean        a player with no value has x None, counted as the position's mean x: his prior is the
                                      position's mean y.
  never_below_zero_else_null_median   the prior is max(line, 0.0); a player whose position has no fit (none of the position
                                      qualified, or position "") has a None line and gets the median y of all fitted rows.
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
  knobs_read_at_call                  knobs() sees forecast.set_constants(SHRINK_K=...) inside the context and the constant after it.
  fixture_by_position                 a goalkeeper or defender faces 1 / attack of the opponent, anyone else its defence; an
                                      opponent with no rating gives 1.0.
  pts_floor                           pts = max(0.0, ppm * fixture); p = min(1.0, q + p_lift * q * (1 - q)), so p is in [0, 1].
  p_blends_listing_and_record         q = w * picked * (1 if his listing gives a start %, else fit) + (1 - w) * record * fit, with
                                      w = P_LISTING * P_DECAY ** (jornada - his club's first jornada left); p = min(1, q + P_LIFT * q * (1 - q)).
  coach_has_no_now                    a player with an empty position (a coach) has no NowRow.
  late_means_after_lock               a (player, jornada) is late when his listing close (listing_close) is strictly after
                                      the jornada's lock (its first kickoff, Calendar.lock, the freeze moment of rules.in_force),
                                      the jornada's calendar state is "under way" or "ahead" and his club has a fixture in it;
                                      a player with no club, or a jornada with no lock, is never late; one Late per pair.
  late_zeroes_p_and_e_not_pts         every Used row of a late pair (all its fixtures) has p 0.0 and e 0.0, pts unchanged;
                                      every other row has p as in NowRow and e = pts * p.
  pool_is_seed_until_enough           fewer than knobs.min_pool played rows (a row whose sum is None counts toward the size)
                                      -> the pool is forecast.SEED_POOL; otherwise the played sums; a None sum is not averaged.
  m2_zero_mean_guard                  m2 = mean(x*x) / (mean(x)*mean(x) if abs(mean(x)) > 1e-9 else 1.0), means over non-None
                                      x; m2 None when the pool has no non-None x.
  standin_is_average_of_held          per (jornada, position) one stand-in row (player None, pos set): pts and p are the means
                                      over the used rows of held players whose position is not ""; e = pts_mean * p_mean (the
                                      product of the means); a position with no held player in a jornada has no stand-in.
  variance_formula                    v = p * pts * pts * m2 - e * e (in that order) for player and stand-in rows alike; v None
                                      when m2 is None. m2 >= 1 (Jensen) and p in [0, 1] make a player's v >= 0.
  held_drops_unknown                  held is the players of the latest squads and listings rows; a row with no player adds none.
  cell_total_sums                     per player with a cell: e summed, v summed over its non-None values (None if all are None),
                                      jornadas the number of his cell rows."""
from __future__ import annotations

import datetime as dt
import math
import random
import re
import statistics
from collections import defaultdict
from collections.abc import Iterable, Mapping
from typing import Any, NamedTuple

from ffcore import forecast
from ffcore import observations as ob
from ffcore import state
from ffcore.current import (LIST_SOURCE, ClubRating, Identity, Listing, PlayerClub, PlayerStatus, Squad, club_rating, listings,
                            player_club, player_status, player_value, squads)
from ffcore.parse import snapshot_stamp, year_for
from ffcore.season_facts import (Calendar, Fixture, Kickoff, LastSeason, ListingClose, PlayedPoints, SeasonRecord, club_fixture,
                                 jornada_calendar, kickoffs, last_season, listing_close, pick, played_points, season_record,
                                 stamp_jd)
from stats import shrink

MIN_MATCHES = 10
MIN_FIT = 3


class Prior(NamedTuple):
    player: str
    prior: float | None


class Spread(NamedTuple):
    m2: float | None
    share: float


class PoolCell(NamedTuple):
    jornada: int
    player: str | None
    pos: str | None
    p: float
    e: float
    v: float | None


class Cell(NamedTuple):
    jornada: int
    player: str
    p: float
    e: float
    v: float | None


class Total(NamedTuple):
    player: str
    e: float
    v: float | None
    jornadas: int


class Inputs(NamedTuple):
    knobs: Knobs
    clubs: tuple[PlayerClub, ...]
    status: Mapping[str, PlayerStatus]
    record: Mapping[str, SeasonRecord]
    last: Mapping[str, LastSeason]
    value: Mapping[str, float]
    prior: Mapping[str, float | None]
    prog: Mapping[str, Prognosis]
    nxt: Mapping[str, int]
    rating: Mapping[str, ClubRating]
    fixtures: Mapping[str, tuple[Fixture, ...]]
    rate: Mapping[str, Rate]
    now: tuple[NowRow, ...]
    late: frozenset[Late]
    used: tuple[Used, ...]
    spread: Spread
    held: frozenset[str]
    pool: tuple[PoolCell, ...]


class _Point(NamedTuple):
    pos: str
    x: float
    y: float


class _Fit(NamedTuple):
    mean_y: float
    mean_x: float
    slope: float


def _fitted_points(clubs: Iterable[PlayerClub], value: Mapping[str, float], last: Iterable[LastSeason]) -> tuple[_Point, ...]:
    pos = {c.player: c.pos for c in clubs}
    out = []
    for r in last:
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


def _priors(clubs: tuple[PlayerClub, ...], value: Mapping[str, float], last: Mapping[str, LastSeason]) -> dict[str, float | None]:
    points = _fitted_points(clubs, value, last.values())
    fits = _fit_by_position(points)
    median = statistics.median(p.y for p in points) if points else None
    out: dict[str, float | None] = {}
    for c in clubs:
        fit = fits.get(c.pos)
        if fit is None:
            out[c.player] = median
            continue
        x = value.get(c.player)
        dx = 0.0 if x is None else math.log(x) - fit.mean_x
        out[c.player] = max(fit.mean_y + fit.slope * dx, 0.0)
    return out


def ppm_prior(inp: Inputs) -> tuple[Prior, ...]:
    """One Prior per player_club row, in no particular order (rules as in the module docstring)."""
    return tuple(Prior(player, prior) for player, prior in inp.prior.items())


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


def _rate_map(clubs: tuple[PlayerClub, ...], status: Mapping[str, PlayerStatus], record: Mapping[str, SeasonRecord],
           last: Mapping[str, LastSeason], prior: Mapping[str, float | None], knobs: Knobs) -> dict[str, Rate]:
    out = {}
    for c in clubs:
        s = status.get(c.player)
        r = record.get(c.player, SeasonRecord(c.player, None, 0.0, 0.0))
        lst = last.get(c.player, LastSeason(c.player, None, None))
        from_record = pick(r, _base(s, knobs), knobs.pick_k)
        from_last = shrink(prior.get(c.player) or 0.0, knobs.shrink_k, lst.pts or 0.0, lst.pj or 0.0)
        ppm = shrink(from_last, knobs.shrink_k, r.pts or 0.0, r.apps)
        listed_pct = s is not None and s.pct is not None
        picked = s.pct / 100.0 if s is not None and s.pct is not None else from_record
        out[c.player] = Rate(c.player, ppm, picked, from_record, listed_pct)
    return out


def forecast_rate(inp: Inputs) -> tuple[Rate, ...]:
    """One Rate per player_club row (rules: listing_beats_record, known_player_pull, no_record_means_prior)."""
    return tuple(inp.rate.values())


class Prognosis(NamedTuple):
    kind: str
    until: str | None
    j: int | None


MONTHS = {m: i + 1 for i, m in enumerate(
    "enero febrero marzo abril mayo junio julio agosto septiembre octubre noviembre diciembre".split())}
PART = {"principios": 5, "princpios": 5, "mediados": 15, "finales": 25}
UNTIL = re.compile(r"Baja (?:hasta|hsata) (?:(%s) de )?(%s)(?:-\w+)?(?: (\d{4}))?" % ("|".join(PART), "|".join(MONTHS)))
SAYS = [(re.compile(r"Duda para la jornada (\d+)"), "doubt"),
        (re.compile(r"Baja confirmada para la jornada (\d+)"), "out"),
        (re.compile(r"Disponible para la jornada (\d+)"), "back")]
STATUS_KIND = {"doubt": "doubt", "suspended": "out", "unavailable": "out", "injured": "out"}


def prognosis(status: str | None, note: str | None, seen_at: str | None) -> Prognosis | None:
    """What a flagged player's listing (status, note, the stamp it was seen at) says: kind doubt, out (up to and in j)
    or back (from j), j a jornada or None for his next match; kind until with the date; or kind never (left the league,
    or out indefinitely). None if he is fit or it says nothing. The note is read first ('Duda para la jornada 8',
    'Baja hasta mediados de octubre': a month already past is next year's), else the status."""
    if status in (None, "", "ok"):
        return None
    note = note or ""
    if status == "gone" or "Baja indefinida" in note:
        return Prognosis("never", None, None)
    for pat, kind in SAYS:
        if m := pat.search(note):
            return Prognosis(kind, None, int(m.group(1)))
    seen = snapshot_stamp(seen_at) if seen_at else None
    if seen and (m := UNTIL.search(note)):
        month = MONTHS[m.group(2)]
        year = int(m.group(3)) if m.group(3) else year_for(month, seen.date(), seen.month - 1)
        return Prognosis("until", dt.date(year, month, PART.get(m.group(1), 15)).isoformat(), None)
    return Prognosis(STATUS_KIND[status], None, None) if status in STATUS_KIND else None


def _prognoses(status: Iterable[PlayerStatus]) -> dict[str, Prognosis]:
    out: dict[str, Prognosis] = {}
    for s in status:
        if (p := prognosis(s.status, s.note, s.at) if s.status is not None else None) is not None:
            out[s.player] = p
    return out


def _next_order(f: Fixture) -> tuple[bool, str, int]:
    return (f.kick is None, f.kick or "", f.jornada)


def _next_jornadas(fixtures: Iterable[Fixture]) -> dict[str, int]:
    first: dict[str, Fixture] = {}
    for f in fixtures:
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


def forecast_fit(inp: Inputs) -> tuple[FitRow, ...]:
    """One FitRow per (player with a club that has fixtures, fixture of that club) (one_row_per_fixture)."""
    return tuple(FitRow(c.player, f.jornada, fit_of(inp.prog.get(c.player), inp.nxt[c.club], f))
                 for c in inp.clubs if c.club is not None
                 for f in inp.fixtures.get(c.club, ()))


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


def _now_rows(clubs: tuple[PlayerClub, ...], rate: Mapping[str, Rate], fixtures: Mapping[str, tuple[Fixture, ...]],
              prog: Mapping[str, Prognosis], nxt: Mapping[str, int], rating: Mapping[str, ClubRating], knobs: Knobs
              ) -> tuple[NowRow, ...]:
    """The rows of forecast_now, built once in inputs() (rules: fixture_by_position, pts_floor, one_row_per_fixture,
    coach_has_no_now)."""
    out = []
    for c in clubs:
        if c.pos == "" or c.club is None:
            continue
        r = rate[c.player]
        games = fixtures.get(c.club, ())
        first = min((f.jornada for f in games), default=0)
        for f in games:
            fit = fit_of(prog.get(c.player), nxt[c.club], f)
            fixture = _fixture_factor(c.pos, rating.get(f.opp))
            w = knobs.p_listing * math.pow(knobs.p_decay, f.jornada - first)
            q = w * r.picked * (1.0 if r.listed_pct else fit) + (1.0 - w) * r.record * fit
            p = min(1.0, q + knobs.p_lift * q * (1.0 - q))
            out.append(NowRow(c.player, f.jornada, max(0.0, r.ppm * fixture), p, r.ppm, fixture, r.picked, fit))
    return tuple(out)


def forecast_now(inp: Inputs) -> tuple[NowRow, ...]:
    """One NowRow per (player with a position and a club, fixture of his club still to play): points if he plays,
    the chance he plays (rules: fixture_by_position, pts_floor, one_row_per_fixture, coach_has_no_now)."""
    return inp.now


class Late(NamedTuple):
    player: str
    jornada: int


class Used(NamedTuple):
    player: str
    jornada: int
    pts: float
    p: float
    e: float


def _late(clubs: tuple[PlayerClub, ...], close: tuple[ListingClose, ...], kicks: tuple[Kickoff, ...],
          calendar: tuple[Calendar, ...]) -> frozenset[Late]:
    """The Late pairs: a player whose listing close is after the lock (the jornada's first kickoff, Calendar.lock) of an
    open jornada in which his club has a fixture (rule late_means_after_lock)."""
    locks = {c.jornada: jd for c in calendar if c.state in ("under way", "ahead") and (jd := stamp_jd(c.lock)) is not None}
    playing = {(club, k.jornada) for k in kicks for club in (k.home, k.away)}
    closes = {c.player: c.t for c in close}
    return frozenset(Late(c.player, jornada) for c in clubs
                     if c.club is not None and (t := closes.get(c.player)) is not None
                     for jornada, lock in locks.items() if (c.club, jornada) in playing and t > lock)


def _used(now: tuple[NowRow, ...], late: frozenset[Late]) -> tuple[Used, ...]:
    """forecast_now with the late pairs zeroed in p and e (rule late_zeroes_p_and_e_not_pts)."""
    out = []
    for n in now:
        p = 0.0 if Late(n.player, n.jornada) in late else n.p
        out.append(Used(n.player, n.jornada, n.pts, p, n.pts * p))
    return tuple(out)


def late_arrivals(inp: Inputs) -> tuple[Late, ...]:
    """One Late per pair of inp.late, no particular order (rule late_means_after_lock)."""
    return tuple(inp.late)


def forecast_used(inp: Inputs) -> tuple[Used, ...]:
    """inp.used: the forecast_now rows with the late pairs zeroed (rule late_zeroes_p_and_e_not_pts)."""
    return inp.used


def _spread(played: Iterable[PlayedPoints], knobs: Knobs) -> Spread:
    """m2 of the pool of played sums (or the seed pool) and the persistent share (rules pool_is_seed_until_enough,
    m2_zero_mean_guard)."""
    rows = tuple(played)
    xs: list[float | None] = [r.pts for r in rows] if len(rows) >= knobs.min_pool else list(forecast.SEED_POOL)
    vals = [x for x in xs if x is not None]
    if not vals:
        return Spread(None, knobs.persistent_share)
    mean = sum(vals) / len(vals)
    sq = sum(x * x for x in vals) / len(vals)
    return Spread(sq / (mean * mean if abs(mean) > 1e-9 else 1.0), knobs.persistent_share)


def _held(squad_rows: Iterable[Squad], listing_rows: Iterable[Listing]) -> frozenset[str]:
    """The players of the latest squads and listings (rule held_drops_unknown)."""
    rows: list[Squad | Listing] = [*squad_rows, *listing_rows]
    return frozenset(r.player for r in rows if r.player is not None)


def _pool(used: tuple[Used, ...], held: frozenset[str], clubs: tuple[PlayerClub, ...], spread: Spread) -> tuple[PoolCell, ...]:
    """Each used row as a player's PoolCell, then each (jornada, position) of held players as a stand-in (rules
    standin_is_average_of_held, variance_formula)."""
    m2 = spread.m2
    pos = {c.player: c.pos for c in clubs}

    def variance(p: float, pts: float, e: float) -> float | None:
        return None if m2 is None else p * pts * pts * m2 - e * e

    out = [PoolCell(u.jornada, u.player, None, u.p, u.e, variance(u.p, u.pts, u.e)) for u in used]
    by_position: dict[tuple[int, str], list[Used]] = defaultdict(list)
    for u in used:
        if u.player in held and pos.get(u.player, "") != "":
            by_position[(u.jornada, pos[u.player])].append(u)
    for (jornada, position), rows in by_position.items():
        pts = sum(u.pts for u in rows) / len(rows)
        p = sum(u.p for u in rows) / len(rows)
        e = pts * p
        out.append(PoolCell(jornada, None, position, p, e, variance(p, pts, e)))
    return tuple(out)


def _player_cells(pool: tuple[PoolCell, ...]) -> tuple[Cell, ...]:
    """The player rows of a pool as Cells (rule cell_pool reads player rows only)."""
    return tuple(Cell(r.jornada, r.player, r.p, r.e, r.v) for r in pool if r.player is not None)


def score_spread(inp: Inputs) -> Spread:
    """The spread of a jornada's points about a cell's expectation: m2 and the persistent share (rules
    pool_is_seed_until_enough, m2_zero_mean_guard)."""
    return inp.spread


def held(inp: Inputs) -> frozenset[str]:
    """The players in the latest squads or listings (rule held_drops_unknown)."""
    return inp.held


def cell_pool(inp: Inputs) -> tuple[PoolCell, ...]:
    """The player rows and the stand-in rows of one cut (rules standin_is_average_of_held, variance_formula)."""
    return inp.pool


def cell(inp: Inputs) -> tuple[Cell, ...]:
    """The player rows of cell_pool: each player's p, e and v for each jornada left."""
    return _player_cells(inp.pool)


def cell_total(inp: Inputs) -> tuple[Total, ...]:
    """One Total per player with a cell: his e summed, v summed over its non-None values, and his cell rows counted
    (rule cell_total_sums)."""
    by_player: dict[str, list[Cell]] = defaultdict(list)
    for c in _player_cells(inp.pool):
        by_player[c.player].append(c)
    out = []
    for player, rows in by_player.items():
        known = [c.v for c in rows if c.v is not None]
        out.append(Total(player, sum(c.e for c in rows), sum(known) if known else None, len(rows)))
    return tuple(out)


def inputs(world: state.World, identity: Identity, knobs: Knobs) -> Inputs:
    """The shared pieces of one cut, each built once: the one place the ingest selectors are called for this group.
    rate is the Rate of every player_club row, prior his Prior, prog and nxt as in fit_of, now the forecast_now rows,
    late the late pairs of listing_close against the kickoffs and calendar, used the forecast_used rows, spread the
    Spread of the played points, held the players of the latest squads and listings, pool the PoolCell rows."""
    clubs = player_club(world)
    status_rows = player_status(world, identity)
    status = {s.player: s for s in status_rows}
    record = {r.player: r for r in season_record(world, identity)}
    last = {r.ff_id: r for r in last_season(world)}
    value = {v.player: v.value for v in player_value(world)}
    fixture_rows = club_fixture(world)
    fixtures: dict[str, list[Fixture]] = defaultdict(list)
    for f in fixture_rows:
        fixtures[f.club].append(f)
    prior = _priors(clubs, value, last)
    prog = _prognoses(status_rows)
    nxt = _next_jornadas(fixture_rows)
    rating = {r.club: r for r in club_rating(world, knobs.fixture_k)}
    by_club = {c: tuple(fs) for c, fs in fixtures.items()}
    rate = _rate_map(clubs, status, record, last, prior, knobs)
    now = _now_rows(clubs, rate, by_club, prog, nxt, rating, knobs)
    late = _late(clubs, listing_close(world, identity), kickoffs(world), jornada_calendar(world))
    used = _used(now, late)
    spread = _spread(played_points(world, identity), knobs)
    held_players = _held(squads(world, identity), listings(world, identity))
    return Inputs(knobs, clubs, status, record, last, value, prior, prog, nxt, rating, by_club, rate, now, late, used,
                  spread, held_players, _pool(used, held_players, clubs, spread))


D0, D1, D2, D3, D4, D5 =("2026-08-13T0000Z", "2026-08-14T1000Z", "2026-08-16T1000Z", "2026-08-20T1000Z",
                          "2026-08-23T1000Z", "2026-08-30T1000Z")
CUTS = (D0, D1, D2, D3, D4)
FIT_Y = {"q1": 4.0, "q2": 3.0, "q3": 2.5, "qn": 1.0, "r1": 2.0, "r2": 4.0,
         "m1": 6.0, "m2": 3.0, "m3": 1.0, "m4": 0.0, "c1": 5.0}
LINE_VALUES = {"f1": 10.0, "f2": 100.0, "f3": 1000.0}
CLUBS = {"q1": "A", "q2": "B", "q3": "C", "qn": "D", "r1": "E", "r2": "F", "m1": "G", "m2": "H", "m3": "I",
         "m4": "J", "f1": "K", "f2": "L", "f3": "M", "c1": "N"}
IDENTITY = Identity(ff={(p, p.upper(), c): p for p, c in CLUBS.items()},
                    app={"api-m4": "m4", "api-q1": "q1", "api-q2": "q2", "api-z1": "z1", "api-r1": "r1"})
LEAGUE, TEAM = "marketPlayerLeague", "marketPlayerTeam"
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
            _mk(at, "f3", 1000.0, "DEF", "M"), _mk(at, "c1", 3.0, "Entrenador", "N"),
            _mk(at, "z1", 5.0, "DEL", "")]


def _listing(at: str, app_id: str, price: float | None, seller: str, expires: str) -> ob.ApiMarket:
    return ob.ApiMarket(at, "s", 1.0, price, None, 0, app_id, app_id, app_id.upper(), app_id.upper(), seller, "", "",
                        expires, "", "")


def _listings(at: str, q1_expires: str) -> list[ob.ApiMarket]:
    """q1's close is q1_expires; m4's does not parse and his zero-priced row is not counted; z1 has no club; q2 is a
    manager's listing (not counted)."""
    return [_listing(at, "api-q1", 5.0, LEAGUE, q1_expires), _listing(at, "api-m4", 5.0, LEAGUE, "soon"),
            _listing(at, "api-m4", 0.0, LEAGUE, "2026-08-16T10:00:00+00:00"),
            _listing(at, "api-z1", 5.0, LEAGUE, "2026-08-23T18:00:00+00:00"),
            _listing(at, "api-q2", 5.0, TEAM, "2026-08-23T18:00:00+00:00")]


def _lu(at: str, p: str, pct: float | None, status: str, note: str) -> ob.Lineups:
    return ob.Lineups(at, LIST_SOURCE, pct, p, p.upper(), CLUBS[p], status, note)


def _m(at: str, mid: str, j: int, home: str, away: str, kickoff: str) -> ob.Matches:
    return ob.Matches(at, "s", j, mid, "", home, away, "", kickoff)


def _st(at: str, mid: str, team: str, slug: str, role: str, minute: float | None) -> ob.Starters:
    return ob.Starters(at, "s", minute, slug, slug.upper(), team, mid, role)


def _tables(por: tuple[float, float] = (5.0, 20.0), q1_expires: str = "2026-08-23T18:00:00+00:00") -> dict[str, list[Any]]:
    y = dict(FIT_Y)
    y.update({ff: 1.0 + 0.5 * math.log(v) for ff, v in LINE_VALUES.items()})
    points = [_p(D1, ff, 10.0 * v, 10.0, "25") for ff, v in y.items()] + [_p(D1, "q1", 3.0, 3.0, "26")]
    lineups = [_lu(D1, "q1", None, "", ""), _lu(D1, "q2", 80.0, "ok", ""), _lu(D1, "r1", 40.0, "doubt", "Duda para la jornada 2")]
    matches = [_m(D1, mid, 1, h, a, k) for mid, h, a, k in MATCHES_1] + [_m(D1, mid, 2, h, a, k) for mid, h, a, k in MATCHES_2]
    starters = [_st(D3, "AB", "A", "q1", "starter", None), _st(D3, "AB", "B", "q2", "sub", 60.0),
                _st(D3, "EF", "E", "r1", "starter", 70.0)]
    api = [ob.ApiPlayersAll(D1, "s", None, "api-m4", "T", "J", "J", "out_of_league")]
    teams = [ob.ApiTeams(D1, "s", 1.0, 1.0, 1.0, "api-r1", "t", "mgr", "R1", "R1", "", "pr1"),
             ob.ApiTeams(D1, "s", 1.0, 1.0, 1.0, "api-nobody", "t", "mgr", "N", "N", "", "pn")]
    return {"market": _league(D1, por, 100.0) + _league(D3, por, 120.0), "points": points, "lineups": lineups,
            "matches": matches, "starters": starters, "api_players_all": api, "api_teams": teams,
            "api_market": _listings(D1, q1_expires),
            "results_history": [ob.ResultsHistory(D1, "fd", hg, ag, "s", day, "h" + h, "h" + a, h, a)
                                for h, a, hg, ag, day in (("A", "B", 2.0, 0.0, "d1"), ("B", "A", 1.0, 1.0, "d2"),
                                                          ("E", "F", 0.0, 3.0, "d3"), ("C", "D", 1.0, 1.0, "d4"),
                                                          ("G", "H", 2.0, 2.0, "d5"))]}


def _scratch() -> ob.Observations:
    return ob.of(_tables())


def _view(world: state.World) -> Any:
    inp = inputs(world, IDENTITY, knobs())
    return (world.cut, ppm_prior(inp), forecast_rate(inp), forecast_fit(inp), forecast_now(inp), late_arrivals(inp),
            forecast_used(inp), score_spread(inp), held(inp), cell_pool(inp), cell(inp), cell_total(inp))


def _inp(world: state.World) -> Inputs:
    return inputs(world, IDENTITY, knobs())


def _prior_of(world: state.World) -> dict[str, float]:
    out = {}
    for p in ppm_prior(_inp(world)):
        assert p.prior is not None, p.player
        out[p.player] = p.prior
    return out


def _rates(world: state.World) -> dict[str, Rate]:
    return {r.player: r for r in forecast_rate(_inp(world))}


def _selftest() -> None:
    """The named rules on a small league; later rows leave a cut unchanged, any subset or order of cuts gives each
    the same view, and a cut alone equals the cut inside a sweep."""
    def says(status: str, note: str = "", seen: str = "2026-09-28T1000Z") -> Any:
        return prognosis(status, note, seen)
    assert says("doubt", "Lesión Desde 13/09 (15 días) Duda para la jornada 8") == Prognosis("doubt", None, 8)
    assert says("injured", "x Baja confirmada para la jornada 9") == Prognosis("out", None, 9)
    assert says("injured", "x Disponible para la jornada 10") == Prognosis("back", None, 10)
    assert says("injured", "x Baja hasta mediados de octubre") == Prognosis("until", "2026-10-15", None)
    assert says("injured", "x Baja hasta enero") == Prognosis("until", "2027-01-15", None), "a month already past is next year's"
    assert says("injured", "x Baja hasta finales de enero 2028") == Prognosis("until", "2028-01-25", None)
    assert says("injured", "x Baja hsata principios de noviembre") == Prognosis("until", "2026-11-05", None), "the site's own typo"
    assert says("injured", "x Baja indefinida") == says("gone") == Prognosis("never", None, None), "out for good"
    assert says("suspended", "Sancionado") == Prognosis("out", None, None), "a status alone: his next match"
    assert says("doubt") == Prognosis("doubt", None, None)
    assert says("ok", "Duda para la jornada 9") is None and says("", "") is None, "fit: nothing"
    assert says("other") is None, "a status that says nothing of playing"
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
    later["api_market"].append(_listing(D5, "api-q1", 5.0, LEAGUE, "2026-08-14T12:00:00+00:00"))
    assert all(_view(state.world_at(ob.of(later), c)) == views[c] for c in CUTS)

    assert ppm_prior(_inp(state.world_at(obs, D0))) == () and len(views[D1][1]) == len(set(p.player for p in views[D1][1]))
    priors = _prior_of(state.world_at(obs, D1))
    assert set(priors) == {c.player for c in _inp(state.world_at(obs, D1)).clubs}

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
    grown = _prior_of(state.world_at(ob.of(with_new), D1))
    assert "pn" in grown and all(grown[k] == priors[k] for k in priors), "a last-season row of 9 matches is not fitted"

    other = _prior_of(state.world_at(ob.of(_tables(por=(50.0, 200.0))), D1))
    assert all(other[k] == priors[k] for k in priors if k not in ("r1", "r2")), "another position's data is not used"

    broken = _tables()
    broken["points"].append(_p(D1, "q3", None, 10.0, "25"))
    try:
        _inp(state.world_at(ob.of(broken), D1))
    except ValueError:
        pass
    else:
        raise AssertionError("a qualifying row with no points must not be skipped")

    w1 = state.world_at(obs, D1)
    rate = _rates(w1)
    inp1 = _inp(w1)
    rec = inp1.record
    k = inp1.knobs

    def rec_of(p: str) -> SeasonRecord:
        return rec.get(p, SeasonRecord(p, None, 0.0, 0.0))

    assert len(rate) == len(inp1.clubs) and all(0.0 <= x.picked <= 1.0 and 0.0 <= x.record <= 1.0 for x in rate.values())
    assert rate["q2"].listed_pct and rate["q2"].picked == 0.8 and rate["q2"].record == pick(rec_of("q2"), k.neutral_start, k.pick_k)
    assert rate["r1"].listed_pct and rate["r1"].picked == 0.4 and rate["r1"].record == pick(rec_of("r1"), k.neutral_start, k.pick_k), \
        "listing_beats_record: a doubtful player with a pct is picked at the pct, his record still from neutral"
    assert not rate["q1"].listed_pct and rate["q1"].picked == rate["q1"].record == pick(rec_of("q1"), k.neutral_start, k.pick_k), \
        "known_player_pull: listed with no pct"
    assert rate["f1"].picked == rate["f1"].record == pick(rec_of("f1"), k.absent_start, k.pick_k), "not listed: absent start"
    assert rate["m4"].record == pick(rec_of("m4"), k.neutral_start, k.pick_k), "gone from the league: neutral start"

    assert abs(rate["f1"].ppm - priors["f1"]) < 1e-12 and rec_of("f1").apps == 0.0, "no_record_means_prior (no last season, no record)"
    no_points = state.world_at(ob.of({"market": _tables()["market"]}), D1)
    assert all(x.ppm == 0.0 for x in forecast_rate(_inp(no_points))), "no prior at all: ppm 0.0"

    last = inp1.last
    for p, x in rate.items():
        hull = [priors[p]]
        lp = last.get(p)
        if lp is not None and lp.pts is not None and lp.pj:
            hull.append(lp.pts / lp.pj)
        rp = rec_of(p)
        if rp.pts is not None and rp.apps > 0:
            hull.append(rp.pts / rp.apps)
        assert min(hull) - 1e-9 <= x.ppm <= max(hull) + 1e-9, "ppm is a convex combination of its prior and its records"
        past = shrink(priors[p], k.shrink_k, (lp.pts or 0.0) if lp else 0.0, (lp.pj or 0.0) if lp else 0.0)
        assert abs(x.ppm - shrink(past, k.shrink_k, rp.pts or 0.0, rp.apps)) < 1e-12, \
            "no_record_means_prior: last season's points and matches first, then this season's, each worth SHRINK_K matches"

    t2 = _tables()
    t2["points"].append(_p(D3, "m2", 4.0, 2.0, "26"))
    base3, grown3 = _rates(state.world_at(obs, D3)), _rates(state.world_at(ob.of(t2), D3))
    assert all(base3[p] == grown3[p] for p in base3 if p != "m2"), "a season record row changes no other player's Rate"

    t3 = _tables()
    t3["lineups"] = [x._replace(start_pct=None) if x.player_slug == "q2" else x for x in t3["lineups"]]
    assert _rates(state.world_at(ob.of(t3), D1))["q2"].record == rate["q2"].record, "record does not depend on the pct"

    nxt = inp1.nxt
    assert nxt["A"] == 1 and nxt["C"] == 2 and nxt["D"] == 2 and nxt["E"] == 2, "next_fixture_order: kick None sorts last"
    fits = forecast_fit(inp1)
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
    assert [fit_of(Prognosis("out", None, None), 4, Fixture(j, "A", "C", None)) for j in (4, 5)] == [0.0, 1.0], \
        "fit_kinds: an out with no jornada is the club's next match"
    until = Prognosis("until", "2026-09-01", None)
    assert fit_of(until, 1, Fixture(1, "A", "C", "2026-08-30T1800Z")) == 0.0
    assert fit_of(until, 1, Fixture(1, "A", "C", "2026-09-02T1800Z")) == 1.0
    assert fit_of(until, 1, Fixture(1, "A", "C", None)) == 1.0

    with forecast.set_constants(SHRINK_K=1.0):
        assert knobs().shrink_k == 1.0
    assert knobs().shrink_k == forecast.SHRINK_K

    now = forecast_now(inp1)
    ratings = inp1.rating
    assert "c1" not in {n.player for n in now}, "coach_has_no_now"
    for n in now:
        assert 0.0 <= n.p <= 1.0 and n.pts >= 0.0, "pts_floor"
        assert n.pts == max(0.0, n.ppm * n.fixture)
    assert {n.player for n in now} == {c.player for c in inp1.clubs if c.pos != "" and c.club is not None}
    at_j1 = {(n.player, n.jornada): n.fixture for n in now if n.player in ("q1", "r1", "m3")}
    assert at_j1[("q1", 1)] == ratings["B"].defence, "fixture_by_position: a defender (DEL) faces the defence of B"
    assert at_j1[("r1", 1)] == 1.0 / ratings["F"].attack, "fixture_by_position: a goalkeeper faces 1 / attack of F"
    assert at_j1[("m3", 1)] == 1.0, "fixture_by_position: an opponent with no rating gives 1.0 (J is unrated)"
    q1_two = sorted(n.fixture for n in now if n.player == "q1" and n.jornada == 2)
    assert q1_two == sorted([ratings["C"].defence, ratings["D"].defence]), "one_row_per_fixture: two rows in jornada 2"
    club_of = {c.player: c.club for c in inp1.clubs if c.club is not None}
    for n in now:
        pr = rate[n.player]
        w = k.p_listing * k.p_decay ** (n.jornada - min(f.jornada for f in inp1.fixtures[club_of[n.player]]))
        q = w * pr.picked * (1.0 if pr.listed_pct else n.fit) + (1.0 - w) * pr.record * n.fit
        assert abs(n.p - min(1.0, q + k.p_lift * q * (1.0 - q))) < 1e-12, \
            "p_blends_listing_and_record: the listing's share P_LISTING falling by P_DECAY a jornada, lifted by P_LIFT"
    with forecast.set_constants(P_LISTING=0.0, P_LIFT=0.0):
        record_only = forecast_now(_inp(w1))
    assert all(abs(n.p - rate[n.player].record * n.fit) < 1e-12 for n in record_only), \
        "p_blends_listing_and_record: with no listing share the chance is his record times his fit"
    with forecast.set_constants(P_LIFT=0.5):
        lifted = forecast_now(_inp(w1))
    base_by = {(n.player, n.jornada, n.fixture): n for n in now}
    assert all(n.p >= base_by[(n.player, n.jornada, n.fixture)].p - 1e-12 for n in lifted), "raising P_LIFT never lowers p"
    t4 = _tables()
    t4["lineups"] = [x._replace(start_pct=None) if x.player_slug == "q2" else x for x in t4["lineups"]]
    moved = forecast_now(_inp(state.world_at(ob.of(t4), D1)))
    assert [n for n in now if n.player != "q2"] == [n for n in moved if n.player != "q2"], (
        "a player's rows do not change when another player's line-up row changes")

    late1 = {(x.player, x.jornada) for x in late_arrivals(inp1)}
    assert len(late_arrivals(inp1)) == len(late1), "one_per_pair: a club with two fixtures in a jornada gives one Late"
    assert late1 == {("q1", 1), ("q1", 2)}, "late_means_after_lock: q1 closes after both jornadas' first kickoffs"
    used1 = forecast_used(inp1)
    assert len(used1) == len(now) and sorted((u.player, u.jornada, u.pts) for u in used1) == sorted(
        (n.player, n.jornada, n.pts) for n in now), "forecast_used has one row per now row"
    for u, n in zip(used1, now):
        assert 0.0 <= u.p <= 1.0 and u.pts >= 0.0, "forecast_used: p in [0, 1], pts >= 0"
        if (u.player, u.jornada) in late1:
            assert u.p == 0.0 and u.e == 0.0 and u.pts == n.pts, "late_zeroes_p_and_e_not_pts"
        else:
            assert u.p == n.p and u.e == u.pts * u.p and u.pts == n.pts, "late_zeroes_p_and_e_not_pts: others as now"
    assert [u.p for u in used1 if (u.player, u.jornada) == ("q1", 2)] == [0.0, 0.0], "all fixtures of a late pair are zeroed"
    with forecast.set_constants(P_LIFT=0.5):
        lifted1 = forecast_used(_inp(w1))
    assert len(lifted1) == len(used1) and all(b.p >= a.p - 1e-12 for a, b in zip(used1, lifted1)), \
        "raising P_LIFT never lowers a used.p"
    t5 = _tables(q1_expires="2026-08-14T12:00:00+00:00")
    w5 = state.world_at(ob.of(t5), D1)
    assert late_arrivals(_inp(w5)) == (), "a close before the kickoff is not late"

    def jd(stamp: str) -> float:
        found = stamp_jd(stamp)
        assert found is not None
        return found
    cal = tuple(Calendar(j, lock, None, "ahead") for j, lock in ((1, "2026-08-15T1800Z"), (2, "2026-08-22T1900Z")))
    kicks = (Kickoff("A", "B", 1, "2026-08-15T1800Z", jd("2026-08-15T1800Z")), Kickoff("E", "F", 1, "2026-08-16T1800Z", jd("2026-08-16T1800Z")),
             Kickoff("E", "A", 2, "2026-08-22T2100Z", jd("2026-08-22T2100Z")), Kickoff("C", "D", 2, "2026-08-22T1900Z", jd("2026-08-22T1900Z")))
    pcs = tuple(PlayerClub(p, c, "DEL") for p, c in (("a", "A"), ("e", "E"), ("n", None)))

    def late_at(stamp: str, calendar: tuple[Calendar, ...] = cal) -> set[Late]:
        return set(_late(pcs, tuple(ListingClose(p, jd(stamp)) for p in ("a", "e", "n")), kicks, calendar))
    assert late_at("2026-08-16T1000Z") == {Late("a", 1), Late("e", 1)}, \
        "late_means_after_lock: after a jornada's first kickoff is late even before his own club's kickoff; a player with no club never"
    assert late_at("2026-08-15T1800Z") == set(), "a close at the lock is not late"
    assert late_at("2026-08-15T1000Z") == set(), "before every lock: not late"
    assert late_at("2026-08-22T2000Z") == {Late("a", 1), Late("e", 1), Late("a", 2), Late("e", 2)}
    assert late_at("2026-08-16T1000Z", (cal[0]._replace(state="behind"), cal[1]._replace(lock=None))) == set(), \
        "a jornada not open, or with no lock, is never late"
    assert [u for u in used1 if u.player != "q1"] == [u for u in forecast_used(_inp(w5)) if u.player != "q1"], \
        "a player's used rows do not change when another player's listing row changes"

    assert held(inp1) == {"q1", "q2", "m4", "z1", "r1"}, "held: squads and listings; the row with no known player adds none"
    seed = [float(x) for x in forecast.SEED_POOL]
    seed_m2 = (sum(x * x for x in seed) / len(seed)) / (sum(seed) / len(seed)) ** 2
    assert score_spread(inp1).share == k.persistent_share

    def near(a: float | None, b: float, tol: float = 1e-12) -> bool:
        return a is not None and abs(a - b) <= tol

    assert len(played_points(w1, IDENTITY)) < k.min_pool and near(score_spread(inp1).m2, seed_m2), \
        "pool_is_seed_until_enough: fewer played rows than MIN_POOL, the seed pool"
    scored_t = _tables()
    scored_t["points"] += [_p(D2, "q1", 5.0, 4.0, "26"), _p(D2, "q2", 1.0, 1.0, "26")]
    w_scored = state.world_at(ob.of(scored_t), D3)
    rows1 = played_points(w_scored, IDENTITY)
    assert _spread([PlayedPoints("a", 1, 0.0)], k._replace(min_pool=0.0)).m2 == 0.0, \
        "m2_zero_mean_guard: a zero mean divides by 1.0"
    assert _spread([PlayedPoints("a", 1, None)], k._replace(min_pool=0.0)).m2 is None, "m2 None with no non-None x"
    assert _spread([PlayedPoints("a", 1, None), PlayedPoints("b", 1, 2.0)], k._replace(min_pool=0.0)).m2 == 1.0, \
        "a None sum counts in the pool's size, not in its averages"
    assert near(_spread([PlayedPoints("a", 1, None)], k._replace(min_pool=2.0)).m2, seed_m2), \
        "a None row counts toward min_pool: one row of two needed is the seed pool"
    xs = [r.pts for r in rows1 if r.pts is not None]
    assert xs, "the fixture has played points"
    with forecast.set_constants(MIN_POOL=1):
        played_spread = score_spread(_inp(w_scored))
    assert near(played_spread.m2, (sum(x * x for x in xs) / len(xs)) / (sum(xs) / len(xs)) ** 2), \
        "pool_is_seed_until_enough: from MIN_POOL played rows, the played sums"

    pos = {c.player: c.pos for c in inp1.clubs}
    m2 = inp1.spread.m2
    assert m2 is not None and m2 >= 1.0 - 1e-12, "m2 >= 1 (Jensen)"
    pool1 = cell_pool(inp1)
    player_rows = [r for r in pool1 if r.player is not None]
    assert [(r.jornada, r.player) for r in player_rows] == [(u.jornada, u.player) for u in inp1.used], \
        "player rows are the used rows, in order"
    for r, u in zip(player_rows, inp1.used):
        assert r.p == u.p and r.e == u.e and r.pos is None and r.v == u.p * u.pts * u.pts * m2 - u.e * u.e, \
            "variance_formula: player rows"
        assert r.v is not None and r.v >= -1e-9, "v = p pts^2 (m2 - p) >= 0 for p in [0, 1] and m2 >= 1"
    standins = {(r.jornada, r.pos): r for r in pool1 if r.player is None}
    assert len(standins) == sum(1 for r in pool1 if r.player is None), "one stand-in per (jornada, position)"
    keys = {(u.jornada, pos[u.player]) for u in inp1.used if u.player in inp1.held and pos.get(u.player, "") != ""}
    assert set(standins) == keys, "standin_is_average_of_held: a stand-in per position a held player has in the jornada"
    for (j, ps), r in standins.items():
        held_rows = [u for u in inp1.used if u.jornada == j and u.player in inp1.held and pos.get(u.player, "") == ps]
        pts_m = sum(u.pts for u in held_rows) / len(held_rows)
        p_m = sum(u.p for u in held_rows) / len(held_rows)
        assert abs(r.p - p_m) < 1e-12 and abs(r.e - pts_m * p_m) < 1e-12, "standin: means of pts and p; e the product"
        assert near(r.v, p_m * pts_m * pts_m * m2 - (pts_m * p_m) ** 2, 1e-9), "variance_formula: stand-in rows"

    cells1 = cell(inp1)
    assert sorted((c.player, c.jornada) for c in cells1) == sorted((u.player, u.jornada) for u in inp1.used), \
        "cell: used's (player, jornada) keys with multiplicity"
    totals1 = cell_total(inp1)
    assert {t.player for t in totals1} == {c.player for c in cells1}, "cell_total: one row per player with a cell"
    for t in totals1:
        rows = [c for c in cells1 if c.player == t.player]
        known = [c.v for c in rows if c.v is not None]
        assert abs(t.e - sum(c.e for c in rows)) < 1e-12 and t.jornadas == len(rows), "cell_total_sums: e and the row count"
        assert (t.v is None) == (not known) and (t.v is None or abs(t.v - sum(known)) < 1e-9), "cell_total_sums: v"
    t6 = _tables()
    t6["api_teams"] = []
    no_squads = _inp(state.world_at(ob.of(t6), D1))
    assert held(no_squads) == held(inp1) - {"r1"}, "held: the squads row is the only source of r1"
    assert cell(no_squads) == cell(inp1), "a player's cell rows do not depend on who is held"
    no_squads_keys = {(r.jornada, r.pos) for r in cell_pool(no_squads) if r.player is None}
    assert no_squads_keys < set(standins), "a stand-in for each position with a held player, and only those"
    print("ffcore.forecast_facts self-test OK")


if __name__ == "__main__":
    _selftest()
