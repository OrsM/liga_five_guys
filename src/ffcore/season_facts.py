"""The season's facts as of a cut (ingest): the calendar, the games, the points credited and the squads, each a fold
over the observations or a selector over the World's folds (state.py). Reads only matches, points, starters and the
market's club of a player; every selector is a pure function of the World (and, for players, of an identity).

Folds (registered): kickoffs, first_results, points_changes, season_last. Selectors: kickoffs, blind_results,
jornada_calendar, round_locks, next_lock, clubs_played, club_fixture, jornadas_done, club_games, scored,
squad_minutes, appearances, player_jornadas, season_record, pick, last_season. An identity maps a starter's
(player_slug, player_name, team_slug) to his player id.

Named rules, each tested:
  utc_stamp       a kickoff text is read in UTC (a text without a zone is UTC) and stamped YYYY-MM-DDTHHMMZ;
                  text that is not an instant has no stamp.
  julian_day      a kickoff's julian day is (unix milliseconds, rounded to the millisecond, + 210866760000000) / 86400000.
  newest_kickoff  a fixture's kickoff is the newest row's that has a jornada and a stamp; a played match's page that
                  drops it leaves the last one.
  first_seen      a fixture with no kickoff ever seen counts as played when its result was first seen; ties between
                  rows of one stamp go to the first stored.
  first_fetch     a player's points are credited only from changes after the season's first fetch, except a single
                  game seen at it, which goes to his club's first jornada.
  last_game       a change is credited to the jornada of the club's latest game at or before the stamp it was seen
                  (the latest kickoff, ties to the higher jornada); a player with no club in the market is credited
                  against all clubs' games.
  one_per_fixture two fixtures of a club in one jornada are two rows."""
from __future__ import annotations

import bisect
import random
import sys
from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone
from typing import Any, NamedTuple

from ffcore import observations as ob
from ffcore import state as st
from ffcore.db import stamp_jd
from ffcore.rules import MATCH_LEN
from stats import shrink

STAMP = "%Y-%m-%dT%H%MZ"
JULIAN_EPOCH_MS = 210866760000000
UNIX_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
ANY_CLUB = "*"

Identity = Mapping[tuple[str, str, str], str]


class Kickoff(NamedTuple):
    home: str
    away: str
    jornada: int
    kick: str
    t: float


class Blind(NamedTuple):
    home: str
    away: str
    jornada: int
    seen: str


class Calendar(NamedTuple):
    jornada: int
    lock: str | None
    next: str | None
    state: str


class RoundLock(NamedTuple):
    jornada: int
    lock: str


class NextLock(NamedTuple):
    lock: str | None
    t: float | None


class ClubPlayed(NamedTuple):
    jornada: int
    club: str


class Fixture(NamedTuple):
    jornada: int
    club: str
    opp: str
    kick: str | None


class ClubGame(NamedTuple):
    club: str
    jornada: int
    at: str


class Change(NamedTuple):
    player: str
    at: str
    id: int
    pts: float | None


class Scored(NamedTuple):
    player: str
    jornada: int
    pts: float | None
    at: str
    id: int


class SquadMinutes(NamedTuple):
    match_id: str
    team_slug: str
    player_slug: str
    player: str | None
    jornada: int
    mins: float


class Appearance(NamedTuple):
    player: str
    jornada: int


class PlayerJornada(NamedTuple):
    player: str
    jornada: int
    pts: float | None


class SeasonRecord(NamedTuple):
    player: str
    pts: float | None
    apps: float
    squads: float


class LastSeason(NamedTuple):
    ff_id: str
    pts: float | None
    pj: float | None


class PointsChanges(NamedTuple):
    first: dict[str, str]
    prev: dict[tuple[str, str], tuple[float | None, float | None]]
    changes: dict[str, list[Change]]
    rows: int


def _utc(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        t = datetime.fromisoformat(text)
    except ValueError:
        return None
    return t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t.astimezone(timezone.utc)


def utc_stamp(text: str | None) -> str | None:
    """The stamp YYYY-MM-DDTHHMMZ (UTC) of an instant's text, None when it is not one."""
    t = _utc(text)
    return None if t is None else t.strftime(STAMP)


def julian_day(text: str | None) -> float | None:
    """The julian day of an instant's text as SQLite's julianday gives it (rounded to the millisecond), None when
    it is not one."""
    t = _utc(text)
    if t is None:
        return None
    millis = ((t - UNIX_EPOCH) // timedelta(microseconds=1) + 500) // 1000
    return (millis + JULIAN_EPOCH_MS) / 86400000.0


def _kickoffs_step(state: dict[tuple[str, str], Kickoff], row: ob.Matches) -> dict[tuple[str, str], Kickoff]:
    kick = utc_stamp(row.kickoff)
    if row.jornada is not None and kick is not None:
        state[(row.home, row.away)] = Kickoff(row.home, row.away, row.jornada, kick, julian_day(row.kickoff) or 0.0)
    return state


def _first_results_step(state: dict[tuple[str, str], Blind], row: ob.Matches) -> dict[tuple[str, str], Blind]:
    if row.score != "" and row.jornada is not None:
        state.setdefault((row.home, row.away), Blind(row.home, row.away, row.jornada, row.observed_at))
    return state


def _points_step(state: PointsChanges, row: ob.Points) -> PointsChanges:
    first = state.first.setdefault(row.season, row.observed_at)
    if row.ff_id != "":
        key = (row.season, row.ff_id)
        points0, games0 = state.prev.get(key, (0.0, 0.0))
        state.prev[key] = (row.points, row.games)
        changed = not (row.points == points0 and row.games == games0)
        if changed and (row.observed_at > first or (row.games is not None and row.games <= 1)):
            pts = None if row.points is None or points0 is None else row.points - points0
            state.changes.setdefault(row.season, []).append(Change(row.ff_id, row.observed_at, state.rows + 1, pts))
    return state._replace(rows=state.rows + 1)


def _season_last_step(state: dict[str, dict[str, ob.Points]], row: ob.Points) -> dict[str, dict[str, ob.Points]]:
    state.setdefault(row.season, {})[row.ff_id] = row
    return state


KICKOFFS = st.register(st.Fold("kickoffs", "matches", dict, _kickoffs_step)).fact
FIRST_RESULTS = st.register(st.Fold("first_results", "matches", dict, _first_results_step)).fact
POINTS_CHANGES = st.register(st.Fold("points_changes", "points", lambda: PointsChanges({}, {}, {}, 0), _points_step)).fact
SEASON_LAST = st.register(st.Fold("season_last", "points", dict, _season_last_step)).fact


def kickoffs(world: st.World) -> tuple[Kickoff, ...]:
    """Each fixture's newest kickoff at the cut (rule newest_kickoff), in no particular order."""
    return tuple(st.fact(world, KICKOFFS).values())


def blind_results(world: st.World) -> tuple[Blind, ...]:
    """The fixtures with a score seen and no kickoff ever seen, each with when its first result was seen (rule
    first_seen)."""
    known = st.fact(world, KICKOFFS)
    return tuple(b for key, b in st.fact(world, FIRST_RESULTS).items() if key not in known)


class Game(NamedTuple):
    jornada: int
    match_id: str
    home: str
    away: str
    score: str


def _matches(world: st.World) -> tuple[Game, ...]:
    return tuple(Game(m.jornada, m.match_id, m.home, m.away, m.score) for m in st.latest(world, "matches")
                 if m.jornada is not None)


def jornada_calendar(world: st.World) -> tuple[Calendar, ...]:
    """One row a jornada of the current matches, by jornada: its lock (first kickoff; None with a blind result),
    the next kickoff of a match to play (None with none), and its state: done (every match scored), behind
    (started before the latest start at or before the cut, a match left), under way (started, a match left),
    ahead."""
    kicks = st.fact(world, KICKOFFS)
    blind = {b.jornada for b in blind_results(world)}
    span: dict[int, list[Any]] = {}
    for m in _matches(world):
        found = kicks.get((m.home, m.away))
        kick = None if found is None else found.kick
        entry = span.setdefault(m.jornada, [None, None, 0])
        if kick is not None and (entry[0] is None or kick < entry[0]):
            entry[0] = kick
        if m.score == "":
            entry[2] += 1
            if kick is not None and (entry[1] is None or kick < entry[1]):
                entry[1] = kick
    last = max((e[0] for e in span.values() if e[0] is not None and e[0] <= world.cut), default=None)
    out = []
    for jornada in sorted(span):
        start, nxt, left = span[jornada]
        if left == 0:
            state = "done"
        elif start is not None and last is not None and start < last:
            state = "behind"
        elif start is not None and start <= world.cut:
            state = "under way"
        else:
            state = "ahead"
        out.append(Calendar(jornada, None if jornada in blind else start, nxt, state))
    return tuple(out)


def round_locks(world: st.World) -> tuple[RoundLock, ...]:
    """When each jornada locks, those with a lock known."""
    return tuple(RoundLock(c.jornada, c.lock) for c in jornada_calendar(world) if c.lock is not None)


def next_lock(world: st.World) -> NextLock:
    """The first lock after the cut and its julian day; both None once none is left."""
    after = [r.lock for r in round_locks(world) if r.lock > world.cut]
    if not after:
        return NextLock(None, None)
    return NextLock(min(after), stamp_jd(min(after)))


def _unfinished(world: st.World) -> tuple[Mapping[int, Calendar], tuple[Game, ...]]:
    calendar = {c.jornada: c for c in jornada_calendar(world)}
    return calendar, tuple(m for m in _matches(world) if calendar[m.jornada].state != "done")


def clubs_played(world: st.World) -> tuple[ClubPlayed, ...]:
    """The clubs with a scored match in each jornada not done, by jornada then club."""
    _, open_matches = _unfinished(world)
    pairs = {ClubPlayed(m.jornada, club) for m in open_matches if m.score != "" for club in (m.home, m.away) if club != ""}
    return tuple(sorted(pairs))


def club_fixture(world: st.World) -> tuple[Fixture, ...]:
    """Each club's match still to play in each jornada not done: its opponent and kickoff (None if unknown); one
    row a fixture (rule one_per_fixture)."""
    kicks = st.fact(world, KICKOFFS)
    _, open_matches = _unfinished(world)
    rows = set()
    for m in open_matches:
        if m.score != "":
            continue
        found = kicks.get((m.home, m.away))
        kick = None if found is None else found.kick
        if m.home != "":
            rows.add(Fixture(m.jornada, m.home, m.away, kick))
        if m.away != "":
            rows.add(Fixture(m.jornada, m.away, m.home, kick))
    return tuple(sorted(rows, key=lambda f: (f.jornada, f.club, f.opp, f.kick or "")))


def jornadas_done(world: st.World) -> tuple[int, ...]:
    """The jornadas every match of which has a score, ascending."""
    return tuple(c.jornada for c in jornada_calendar(world) if c.state == "done")


def club_games(world: st.World) -> tuple[ClubGame, ...]:
    """When each club's games count as played: the kickoff, else when the result was first seen; a row a club and
    fixture."""
    rows = [(k.home, k.away, k.jornada, k.kick) for k in kickoffs(world)]
    rows += [(b.home, b.away, b.jornada, b.seen) for b in blind_results(world)]
    out = [ClubGame(home, j, at) for home, _, j, at in rows if home != ""]
    return tuple(out + [ClubGame(away, j, at) for _, away, j, at in rows if away != ""])


def _clubs(world: st.World) -> dict[str, str | None]:
    return {r.ff_id: r.club or None for r in st.latest(world, "market") if r.ff_id != ""}


def _credit(games: list[tuple[str, int]], at: str, first: str) -> int | None:
    if not games:
        return None
    if at == first:
        return min(j for _, j in games)
    i = bisect.bisect_right(games, (at, sys.maxsize))
    return games[i - 1][1] if i else None


def scored(world: st.World) -> tuple[Scored, ...]:
    """The points changes of the latest season at the cut, credited to a jornada (rules first_fetch, last_game),
    in the order stored."""
    seen = st.fact(world, POINTS_CHANGES)
    if not seen.first:
        return ()
    season = max(seen.first)
    games: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for g in club_games(world):
        games[g.club].append((g.at, g.jornada))
        games[ANY_CLUB].append((g.at, g.jornada))
    for held in games.values():
        held.sort()
    clubs = _clubs(world)
    credited: dict[tuple[str, str], int | None] = {}
    out = []
    for c in seen.changes.get(season, ()):
        key = (clubs.get(c.player) or ANY_CLUB, c.at)
        if key not in credited:
            credited[key] = _credit(games.get(key[0], []), c.at, seen.first[season])
        jornada = credited[key]
        if jornada is not None:
            out.append(Scored(c.player, jornada, c.pts, c.at, c.id))
    return tuple(out)


def _squads(world: st.World, identity: Identity) -> list[tuple[str, str, str, str | None, int, float]]:
    jornada = {m.match_id: m.jornada for m in _matches(world)}
    out = []
    for s in st.latest(world, "starters"):
        role, minute = s.role, s.minute
        if (role != "starter" and role != "sub") or s.match_id not in jornada:
            continue
        if role == "starter":
            mins = MATCH_LEN if minute is None else minute
        else:
            mins = 0.0 if minute is None else MATCH_LEN - minute
        out.append((s.match_id, s.team_slug, s.player_slug, identity.get((s.player_slug, s.player_name, s.team_slug)),
                    jornada[s.match_id], mins if mins > 0.0 else 0.0))
    return out


def squad_minutes(world: st.World, identity: Identity) -> tuple[SquadMinutes, ...]:
    """Each starter or sub of a current starters row of a match with a jornada, and his minutes: a starter the
    whole match unless he came off, a sub none unless he came on."""
    return tuple(map(SquadMinutes._make, _squads(world, identity)))


def _played(squads: Iterable[tuple[str, str, str, str | None, int, float]]) -> set[tuple[str, int]]:
    return {(s[3], s[4]) for s in squads if s[3] is not None and s[5] > 0}


def appearances(world: st.World, identity: Identity) -> tuple[Appearance, ...]:
    """Each jornada a known player started or came on in, once, by player and jornada."""
    return tuple(map(Appearance._make, sorted(_played(_squads(world, identity)))))


def _total(values: Iterable[float | None]) -> float | None:
    held = [v for v in values if v is not None]
    return sum(held) if held else None


def _jornadas(world: st.World, squads: Iterable[tuple[str, str, str, str | None, int, float]]) -> dict[tuple[str, int], list[float | None]]:
    points: dict[tuple[str, int], list[float | None]] = defaultdict(list)
    for s in scored(world):
        points[(s.player, s.jornada)].append(s.pts)
    scorers = {player for player, _ in points}
    for q in squads:
        if q[3] in scorers:
            points[(q[3], q[4])].append(0.0)
    return points


def player_jornadas(world: st.World, identity: Identity) -> tuple[PlayerJornada, ...]:
    """Each scoring player's jornadas (scored in, or in the matchday squad) and his points there, by player and
    jornada."""
    points = _jornadas(world, _squads(world, identity))
    return tuple(PlayerJornada(p, j, _total(v)) for (p, j), v in sorted(points.items()))


def season_record(world: st.World, identity: Identity) -> tuple[SeasonRecord, ...]:
    """Each scoring player's season so far: points, appearances and matchday squads (jornadas scored in or
    squadded), by player."""
    squads = _squads(world, identity)
    played = _played(squads)
    by_player: dict[str, list[tuple[int, float | None]]] = defaultdict(list)
    for (p, j), v in _jornadas(world, squads).items():
        by_player[p].append((j, _total(v)))
    return tuple(SeasonRecord(p, _total(pts for _, pts in rows), float(sum((p, j) in played for j, _ in rows)),
                              float(len(rows))) for p, rows in sorted(by_player.items()))


def pick(record: SeasonRecord, neutral_start: float, pick_k: float) -> float:
    """The chance a player is picked if fit after his next match: his appearances over matchday squads pulled
    toward neutral_start percent, worth pick_k matches."""
    return float(shrink(neutral_start / 100.0, pick_k, record.apps, record.squads))


def last_season(world: st.World) -> tuple[LastSeason, ...]:
    """Each player's last points row of the season before the latest one at the cut; none with one season only."""
    seasons = st.fact(world, SEASON_LAST)
    older = [s for s in seasons if s < max(seasons, default="")]
    if not older:
        return ()
    return tuple(LastSeason(r.ff_id, r.points, r.games) for r in seasons[max(older)].values())


def _m(at: str, mid: str, j: int | None, home: str, away: str, score: str, kickoff: str) -> ob.Matches:
    return ob.Matches(at, "s", j, mid, "", home, away, score, kickoff)


def _p(at: str, ff: str, points: float, games: float, season: str = "26") -> ob.Points:
    return ob.Points(at, "s", points, games, 0.0, ff, "", "", "", season)


def _mk(at: str, ff: str, club: str) -> ob.Market:
    return ob.Market(at, "s", 1.0, ff, "DEL", club)


def _st(at: str, mid: str, team: str, slug: str, role: str, minute: float | None) -> ob.Starters:
    return ob.Starters(at, "s", minute, slug, slug.upper(), team, mid, role)


SCRATCH_IDENTITY = {("pa", "PA", "A"): "ida", ("pb", "PB", "B"): "idb", ("pc", "PC", "C"): "idc"}


def _scratch() -> ob.Observations:
    d1, d2, d3, d4 = "2026-08-14T1000Z", "2026-08-16T1000Z", "2026-08-20T1000Z", "2026-08-23T1000Z"
    matches = [
        _m(d1, "m1", 1, "A", "B", "", "2026-08-15T20:00:00+02:00"),
        _m(d1, "m2", 1, "C", "D", "", ""),
        _m(d1, "m3", 2, "A", "C", "", "2026-08-22T19:00:00+00:00"),
        _m(d1, "m4", 2, "B", "D", "", "2026-08-22T21:00:00+00:00"),
        _m(d1, "m5", 2, "A", "D", "", "2026-08-23T12:00:00"),
        _m(d2, "m1", 1, "A", "B", "1-0", ""),
        _m(d2, "m2", 1, "C", "D", "2-2", ""),
        _m(d2, "m2", 3, "C", "D", "2-2", ""),
        _m(d3, "m3", 2, "A", "C", "0-0", ""),
        _m(d4, "m5", 2, "A", "D", "1-1", ""),
        _m(d4, "m4", 2, "B", "D", "", "2026-08-22T20:00:00+00:00"),
    ]
    points = [_p(d1, "ida", 10.0, 4.0, "25"), _p(d1, "idb", 7.0, 3.0, "25"), _p(d2, "ida", 12.0, 5.0, "25"), _p(d2, "", 1.0, 1.0, "25"),
              _p(d2, "ida", 5.0, 2.0), _p(d2, "idb", 2.0, 1.0), _p(d2, "idc", 3.0, 1.0), _p(d3, "ida", 9.0, 3.0), _p(d3, "idb", 2.0, 1.0),
              _p(d4, "ida", 9.0, 3.0), _p(d4, "idc", 6.0, 2.0), _p(d4, "idb", 4.0, 2.0)]
    market = [_mk(d1, "ida", "A"), _mk(d1, "idb", "B"), _mk(d1, "idc", "C"), _mk(d3, "ida", "A"), _mk(d3, "idb", "B"),
              _mk(d3, "idc", "")]
    starters = [_st(d2, "m1", "A", "pa", "starter", None), _st(d2, "m1", "B", "pb", "sub", 60.0),
                _st(d2, "m1", "B", "pz", "starter", 80.0), _st(d3, "m3", "C", "pc", "sub", None),
                _st(d3, "m3", "A", "pa", "starter", 70.0), _st(d4, "m5", "A", "pa", "starter", None),
                _st(d4, "m1", "A", "pa", "starter", 200.0)]
    return ob.of({"matches": matches, "points": points, "market": market, "starters": starters})


def _view(world: st.World) -> Any:
    ids = SCRATCH_IDENTITY
    return (world.cut, kickoffs(world), blind_results(world), jornada_calendar(world), round_locks(world),
            next_lock(world), clubs_played(world), club_fixture(world), jornadas_done(world), sorted(club_games(world)),
            scored(world), sorted(squad_minutes(world, ids), key=repr), appearances(world, ids), player_jornadas(world, ids),
            season_record(world, ids), sorted(last_season(world)))


def _selftest() -> None:
    """The named rules on a small league; later rows leave a cut unchanged, any subset or order of cuts gives each
    the same view, and a cut alone equals the cut inside a sweep."""
    assert utc_stamp("2026-08-15T20:00:00+02:00") == "2026-08-15T1800Z"
    assert utc_stamp("2026-08-15T20:00:00") == "2026-08-15T2000Z" and utc_stamp("") is None and utc_stamp("soon") is None
    assert julian_day("1970-01-01T00:00:00Z") == 2440587.5 and julian_day("x") is None
    assert julian_day("2026-01-01T00:00:00.0006Z") == julian_day("2026-01-01T00:00:00.001Z") != julian_day("2026-01-01T00:00:00Z")
    obs = _scratch()
    cuts = ["2026-08-13T0000Z", "2026-08-14T1000Z", "2026-08-15T1000Z", "2026-08-16T1000Z", "2026-08-20T1000Z", "2026-08-23T1000Z"]
    worlds = {c: st.world_at(obs, c) for c in cuts}
    views = {c: _view(w) for c, w in worlds.items()}
    assert [v for v in st.sweep(obs, cuts, _view)] == [views[c] for c in cuts]
    rng = random.Random(7)
    for _ in range(8):
        pick_cuts = [rng.choice(cuts) for _ in range(rng.randint(1, 8))]
        assert st.sweep(obs, pick_cuts, _view) == [views[c] for c in pick_cuts]
    later = ob.of({t: list(ob.rows_of(obs, t)) + [_m("2026-09-01T0000Z", "m9", 9, "A", "B", "", "2026-09-05T20:00:00Z")]
                   if t == "matches" else list(ob.rows_of(obs, t)) for t in ob.TABLES if t in obs.tables})
    assert all(_view(st.world_at(later, c)) == views[c] for c in cuts)

    kicks = {k.home + k.away: k for k in views[cuts[1]][1]}
    assert sorted(kicks) == ["AB", "AC", "AD", "BD"] and kicks["AB"].kick == "2026-08-15T1800Z"
    assert kicks["AD"].kick == "2026-08-23T1200Z" and views[cuts[1]][2] == ()
    lock_day = views[cuts[3]]
    assert {k.home + k.away: k.kick for k in lock_day[1]}["AB"] == "2026-08-15T1800Z"
    assert [(b.home, b.jornada, b.seen) for b in lock_day[2]] == [("C", 1, "2026-08-16T1000Z")]
    assert [(c.jornada, c.lock, c.state) for c in lock_day[3]] == [
        (1, None, "done"), (2, "2026-08-22T1900Z", "ahead"), (3, None, "done")]
    assert [r.jornada for r in lock_day[4]] == [2]
    assert lock_day[5] == NextLock("2026-08-22T1900Z", stamp_jd("2026-08-22T1900Z"))
    assert [f.opp for f in lock_day[7] if f.club == "A"] == ["C", "D"]
    assert lock_day[8] == (1, 3) and not lock_day[6]
    assert [(s.player, s.jornada, s.pts, s.id) for s in lock_day[10]] == [("idb", 1, 2.0, 6), ("idc", 1, 3.0, 7)]
    assert {(c.club, c.jornada) for c in lock_day[9] if c.club in ("C", "D")} == {("C", 1), ("D", 1), ("C", 2), ("D", 2)}
    end = views[cuts[-1]]
    assert end[5] == NextLock(None, None) and [c.state for c in end[3]] == ["done", "under way", "done"]
    assert {k.home + k.away: k.kick for k in end[1]}["BD"] == "2026-08-22T2000Z"
    assert [(s.player, s.jornada, s.pts) for s in end[10] if s.at > "2026-08-16T1000Z"] == [
        ("ida", 1, 4.0), ("idc", 2, 3.0), ("idb", 2, 2.0)]
    assert [(r.player, r.pts, r.apps, r.squads) for r in end[14]] == [
        ("ida", 4.0, 2.0, 2.0), ("idb", 4.0, 1.0, 2.0), ("idc", 6.0, 0.0, 2.0)]
    assert {(m.player_slug, m.mins) for m in end[11] if m.player is None} == {("pz", 80.0)}
    assert max(m.mins for m in end[11]) == 200.0 and min(m.mins for m in end[11]) == 0.0
    assert end[12] == (Appearance("ida", 1), Appearance("ida", 2), Appearance("idb", 1))
    assert last_season(worlds[cuts[0]]) == () and end[15] == [
        LastSeason("", 1.0, 1.0), LastSeason("ida", 12.0, 5.0), LastSeason("idb", 7.0, 3.0)]
    assert all(0.0 < pick(r, 60.0, 4.0) < 1.0 for r in end[14])
    twice = {(f.jornada, f.club) for f in views[cuts[1]][7]}
    assert len(views[cuts[1]][7]) == len(set(views[cuts[1]][7])) and ("A", 2) not in twice
    assert sum(f.club == "A" and f.jornada == 2 for f in views[cuts[1]][7]) == 2
    print("ffcore.season_facts self-test OK")


if __name__ == "__main__":
    _selftest()
