from __future__ import annotations

import bisect
import datetime as dt
import math
import re
from dataclasses import dataclass, replace
from typing import NamedTuple

import numpy as np

from ffcore.parse import pct100, snapshot_stamp, year_for
from ffcore.text import norm
from ffcore.rules import minutes_played
from stats import shrink

__all__ = ["Obs", "Outcome", "Calibration", "StartOdds", "Availability",
           "prognosis", "prognosis_of", "bucket", "fit_availability",
           "last_fit_listing", "calibrate", "fit", "outcomes",
           "observations", "fit_start_fallbacks", "NEUTRAL_START",
           "ABSENT_START"]

INTERCEPT = [round(-3.0 + 0.5 * i, 1) for i in range(13)]
SLOPE = [round(0.2 + 0.4 * i, 1) for i in range(15)]

FLOOR, CEIL = 0.01, 0.97
NEUTRAL_START = 60.0
ABSENT_START = 15.0
FALLBACK_K = 8.0


class Obs(NamedTuple):
    ff: float | None
    started: float
    group: str = ""


def _platt(p: float, alpha: float, beta: float) -> float:
    p = min(1.0 - 1e-6, max(1e-6, p))
    z = alpha + beta * math.log(p / (1.0 - p))
    q = 1.0 / (1.0 + math.exp(-max(-40.0, min(40.0, z))))
    return min(CEIL, max(FLOOR, q))


@dataclass
class Calibration:
    alpha: float = 0.0
    beta: float = 1.0
    neutral_start: float = NEUTRAL_START
    absent_start: float = ABSENT_START

    def p(self, ff_pct) -> float:
        if ff_pct is None:
            return 0.0
        if (self.alpha, self.beta) == (0.0, 1.0):
            return ff_pct / 100.0
        return _platt(ff_pct / 100.0, self.alpha, self.beta)


MONTHS = {m: i + 1 for i, m in enumerate(
    "enero febrero marzo abril mayo junio julio agosto septiembre octubre "
    "noviembre diciembre".split())}
PART = {"principios": 5, "princpios": 5, "mediados": 15, "finales": 25}
_UNTIL = re.compile(r"Baja (?:hasta|hsata) (?:(%s) de )?(%s)(?:-\w+)?(?: (\d{4}))?"
                    % ("|".join(PART), "|".join(MONTHS)))
_FOR = [(re.compile(r"Duda para la jornada (\d+)"), "doubt_for"),
        (re.compile(r"Baja confirmada para la jornada (\d+)"), "out_for"),
        (re.compile(r"Disponible para la jornada (\d+)"), "available_from")]
# What each prognosis says, read literally; the data moves these (AVAIL_K).
PRIOR = {"doubt_for:before": 0.0, "doubt_for:at": 0.5, "doubt_for:after": 1.0,
         "out_for:before": 0.0, "out_for:at": 0.0, "out_for:after": 1.0,
         "available_from:before": 0.0, "available_from:at": 1.0,
         "available_from:after": 1.0,
         "out_until:weeks_before": 0.0, "out_until:days_before": 0.0,
         "out_until:after": 1.0, "indefinite": 0.0}
AVAIL_K = 4.0
PICK_K = 4.0    # how many matches of a player's own history the prior is worth
# A flagged player with no prognosis in his note: the status, read as being
# about his next match (argument None until that jornada is known).
IMPLIED = {"doubt": "doubt_for", "suspended": "out_for",
           "unavailable": "out_for", "injured": "out_for"}
# From the LaLiga app we take only what futbolfantasy does not report: that
# a player has left the league and will not play again. Its injury flags lag
# futbolfantasy's daily prognoses (tried: worse on the backtest).
APP_STATUS = {"out_of_league": "gone"}


def prognosis(note: str, seen: dt.date) -> tuple[str, object] | None:
    """futbolfantasy's injury note, as (kind, jornada) or ("out_until",
    date): 'Duda para la jornada 8', 'Baja hasta mediados de octubre'."""
    for pat, kind in _FOR:
        if m := pat.search(note or ""):
            return kind, int(m.group(1))
    if m := _UNTIL.search(note or ""):
        month = MONTHS[m.group(2)]
        year = int(m.group(3)) if m.group(3) else year_for(
            month, seen, seen.month - 1)
        return "out_until", dt.date(year, month, PART.get(m.group(1), 15))
    if "Baja indefinida" in (note or ""):
        return "indefinite", None
    return None


def prognosis_of(status: str, note: str, seen: dt.date
                 ) -> tuple[str, object] | None:
    """What a listing says about the coming jornadas: its note's prognosis,
    else its status for the player's next match; nothing if he is fit."""
    if status in ("", "ok"):
        return None
    if status == "gone":
        return "indefinite", None
    return prognosis(note, seen) or (
        (IMPLIED[status], None) if status in IMPLIED else None)


def bucket(prog: tuple[str, object], jornada: int, when: dt.date,
           next_j: int) -> str:
    """Where a jornada falls against a prognosis: 'doubt_for:at',
    'out_until:days_before', ... - the row of the availability table."""
    kind, arg = prog
    if kind == "indefinite":
        return kind
    if arg is None:
        arg = next_j
    if kind == "out_until":
        days = (when - arg).days
        return "out_until:" + ("weeks_before" if days < -14 else
                               "days_before" if days < 0 else "after")
    return "%s:%s" % (kind, "before" if jornada < arg else
                      "at" if jornada == arg else "after")


class Availability:
    """The chance an injured player is fit for a jornada, by where it falls
    against his prognosis: fitted from past prognoses against who played,
    relative to how often the same players play when fit."""

    def __init__(self, level: dict[str, float] | None = None):
        self.level = {**PRIOR, **(level or {})}

    def of(self, prog: tuple[str, object], jornada: int, when: dt.date,
           next_j: int) -> float:
        return self.level[bucket(prog, jornada, when, next_j)]


def _fit(o: Outcome) -> bool:
    return o.status in ("", "ok")


def last_fit_listing(outs: list[Outcome]) -> dict[str, float]:
    """Each player's most recent start % while fit: what selection looked like
    before a flag, when his current listing is about the injury."""
    out: dict[str, float] = {}
    for o in sorted(outs, key=lambda o: o.at):
        if o.key and o.listed and o.status == "ok" and o.ff is not None:
            out[o.key] = o.ff * 100.0
    return out


def fit_rate(outs: list[Outcome]) -> dict[str, float]:
    """How often each player appears when listed fit. Only the reference for
    the fit factor, which is a ratio of like with like (appearances under a
    prognosis over appearances when fit); how often he is picked is a
    different question, answered by his matchday-squad record."""
    seen: dict[str, list[float]] = {}
    for o in outs:
        if o.key and o.listed and o.status == "ok":
            seen.setdefault(o.key, []).append(_played(o))
    return {key: sum(v) / len(v) for key, v in seen.items() if len(v) >= 3}


def fit_availability(outs: list[Outcome], k: float = AVAIL_K) -> Availability:
    """Pair each prognosis, as seen at one lock, with whether the player
    played in that jornada and every later one: the question the forecast
    asks of today's note."""
    by_key: dict[str, dict[int, Outcome]] = {}
    for o in outs:
        if o.key:
            by_key.setdefault(o.key, {})[o.jornada] = o
    base = fit_rate(outs)
    tally: dict[str, list[float]] = {}
    for key, seen in by_key.items():
        if key not in base:
            continue
        for i, oi in seen.items():
            prog = prognosis_of(oi.status, oi.note, _day(oi.at))
            for j, oj in seen.items() if prog else ():
                if j >= i:
                    t = tally.setdefault(bucket(prog, j, _day(oj.at), i),
                                         [0.0, 0.0])
                    t[0] += _played(oj)
                    t[1] += base[key]
    return Availability({b: min(1.0, shrink(PRIOR[b], k, got, fit))
                         for b, (got, fit) in tally.items()})


def _day(stamp: str) -> dt.date:
    return snapshot_stamp(stamp).date()


class StartOdds:
    """Whether a player plays in a jornada, as two factors: the chance he is
    fit (1 unless flagged; else his prognosis, through Availability) times the
    chance he is picked if fit. history is his record this season as
    (appearances, matchday squads), which is what picking is pulled toward."""

    def __init__(self, xi: list[dict], xw, cal: Calibration | None = None,
                 avail: Availability | None = None,
                 history: dict[str, tuple[float, float]] | None = None,
                 last_fit: dict[str, float] | None = None,
                 app: dict[str, str] | None = None):
        self.cal = cal or Calibration()
        self.avail = avail or Availability()
        self.history = history or {}
        self.last_fit = last_fit or {}
        self.start_pct: dict[str, float] = {}
        self.listed: set[str] = set()
        self.status: dict[str, str] = {}
        self.prognosis: dict[str, tuple[str, object]] = {}
        for r in xi or []:
            key = xw.key_of(r) if xw else None
            if not key:
                continue
            self.listed.add(key)
            p = pct100(r.get("start_pct"))
            if p is not None and p >= 0:
                self.start_pct[key] = max(self.start_pct.get(key, 0.0), p)
            if r.get("status") and r["status"] != "ok":
                self.status[key] = r["status"]
                prog = prognosis_of(r["status"], r.get("note") or "", _day(
                    r["observed_at"]) if r.get("observed_at") else dt.date.today())
                if prog:
                    self.prognosis[key] = prog
        # Flagged if either source flags him; futbolfantasy's prognosis, when
        # it has one, says for how long.
        for key, app_status in (app or {}).items():
            status = APP_STATUS.get(app_status)
            if status and (key not in self.status or status == "gone"):
                self.status[key] = status
                self.prognosis[key] = prognosis_of(status, "", dt.date.min)

    def p_now(self, key: str) -> float:
        """This week's listing, calibrated: the start %, else in the squad or not."""
        pct = self.start_pct.get(key)
        return self.cal.p(pct if pct is not None else (
            self.cal.neutral_start if key in self.listed
            else self.cal.absent_start))

    def listing(self, key: str) -> float:
        """What futbolfantasy last said about his selection while he was fit:
        this week's listing if he is fit, else his last one before the flag,
        else that he is not in the squad."""
        if key not in self.status:
            return self.p_now(key)
        return self.cal.p(self.last_fit.get(key, self.cal.absent_start))

    def picked(self, key: str, next_match: bool) -> float:
        """The chance he is picked if fit. His next match, if he is fit: this
        week's listing, pulled toward his record. Later matches (and the next
        one, if he is flagged): 60% pulled toward his record; with no record,
        his listing while fit."""
        got, n = self.history.get(key, (0.0, 0.0))
        if next_match and key not in self.status:
            return shrink(self.p_now(key), PICK_K, got, n)
        return shrink(NEUTRAL_START / 100.0, PICK_K, got, n) if n else self.listing(key)

    def status_of(self, key: str) -> str:
        return self.status.get(key, "")

    def fit(self, key: str, jornada: int, when: dt.date | None,
            next_j: int) -> float:
        """The chance he is fit: 1 unless flagged, else his prognosis."""
        prog = self.prognosis.get(key)
        if prog is None or (when is None and prog[0] == "out_until"):
            return 1.0
        return self.avail.of(prog, jornada, when, next_j)


def _grid_brier(obs) -> np.ndarray:
    ff = np.array([o.ff for o in obs])
    y = np.array([o.started for o in obs])
    al = np.array(INTERCEPT)[:, None, None]
    be = np.array(SLOPE)[None, :, None]
    p = np.clip(ff, 1e-6, 1.0 - 1e-6)
    z = np.clip(al + be * np.log(p / (1.0 - p)), -40.0, 40.0)
    pred = np.clip(1.0 / (1.0 + np.exp(-z)), FLOOR, CEIL)
    pred = np.where((al == 0.0) & (be == 1.0), ff, pred)
    return ((pred - y) ** 2).mean(axis=-1)


def _best(obs) -> Calibration:
    i, j = np.unravel_index(np.argmin(_grid_brier(obs)),
                            (len(INTERCEPT), len(SLOPE)))
    return Calibration(INTERCEPT[i], SLOPE[j])


def _brier(c: Calibration, obs) -> float:
    return sum((c.p(o.ff * 100.0) - o.started) ** 2 for o in obs)


def fit(obs) -> Calibration:
    obs = [o for o in obs if o.ff is not None]
    raw = Calibration()
    groups = sorted({o.group for o in obs})
    if len(obs) < 3 or len(groups) < 2:
        return raw
    held_out = sum(_brier(_best([o for o in obs if o.group != g]),
                          [o for o in obs if o.group == g]) for g in groups)
    return _best(obs) if held_out < _brier(raw, obs) else raw


class Outcome(NamedTuple):
    at: str
    jornada: int
    group: str
    who: str
    key: str | None
    listed: bool
    ff: float | None
    status: str
    in_squad: bool
    mins: float
    note: str = ""


def _last_before(hist: list, cut: str):
    i = bisect.bisect_right(hist, cut, key=lambda t: t[0])
    return hist[i - 1][1] if i else None


def _pct(row) -> float | None:
    try:
        return float(row.get("start_pct")) / 100.0
    except (TypeError, ValueError):
        return None


def outcomes(lineups, starters, locks: dict, jornada_of: dict, xw
             ) -> list[Outcome]:
    squads: dict[tuple, dict[str, dict]] = {}
    for r in starters:
        if r.get("role") in ("starter", "sub") and r.get("player_slug"):
            squads.setdefault((r["match_id"], r["team_slug"]),
                              {})[r["player_slug"]] = r
    wide: dict[str, dict[str, list]] = {}
    for r in sorted(lineups, key=lambda r: r.get("observed_at", "")):
        slug = r.get("player_slug") or norm(r.get("player_name"))
        wide.setdefault(r.get("team_slug"), {}).setdefault(
            slug, []).append((r.get("observed_at", ""), r))

    out = []
    for (match, team), squad in sorted(squads.items()):
        j = jornada_of.get(match)
        if j not in locks:
            continue
        cut = locks[j].strftime("%Y-%m-%dT%H%MZ")
        before = {slug: row for slug, hist in wide.get(team, {}).items()
                  if (row := _last_before(hist, cut)) is not None}
        for slug in sorted(set(before) | set(squad)):
            row, played = before.get(slug), squad.get(slug)
            out.append(Outcome(
                cut, j, "%s:%s" % (match, team), "%s:%s" % (team, slug),
                xw.key_of(row or played) if xw else None,
                row is not None,
                _pct(row) if row else None,
                (row.get("status") or "ok") if row else "",
                played is not None,
                minutes_played(played["role"], played.get("minute"))
                if played else 0.0,
                (row.get("note") or "") if row else ""))
    return sorted(out, key=lambda o: (o.at, o.group))


def _played(o: Outcome) -> float:
    return 1.0 if o.mins > 0 else 0.0


def observations(outs: list[Outcome], neutral: float = NEUTRAL_START,
                 absent: float = ABSENT_START) -> list[Obs]:
    return [Obs(o.ff if o.ff is not None
                else (neutral if o.listed else absent) / 100.0,
                _played(o), o.group) for o in outs]


def _shrunk(default_pct: float, shares: list[float]) -> float:
    if not shares:
        return default_pct
    rate = sum(shares) / len(shares)
    return shrink(default_pct / 100.0, FALLBACK_K, len(shares) * rate,
                  len(shares)) * 100.0


def fit_start_fallbacks(outs: list[Outcome]) -> tuple[float, float]:
    return (_shrunk(NEUTRAL_START, [_played(o) for o in outs
                                    if o.listed and o.ff is None]),
            _shrunk(ABSENT_START, [_played(o) for o in outs if not o.listed]))


def calibrate(outs: list[Outcome]) -> Calibration:
    """What a listing means for selection, fitted on fit players only: for a
    flagged player the listing is about the injury, which availability
    already covers."""
    fit_outs = [o for o in outs if _fit(o)]
    neutral, absent = fit_start_fallbacks(fit_outs)
    return replace(fit(observations(fit_outs, neutral, absent)),
                   neutral_start=neutral, absent_start=absent)


def _selftest() -> None:
    from ffcore.crosswalk import Crosswalk, Player

    xw = Crosswalk({"ana": Player("ana", "Ana"), "bo": Player("bo", "Bo")})
    odds = StartOdds([{"player_name": "Ana", "start_pct": "80", "status": "ok"},
                      {"player_name": "Ana", "start_pct": "90"},
                      {"player_name": "Bo", "start_pct": "", "status": "doubt"},
                      {"player_name": "Nobody", "start_pct": "99"}], xw)
    assert odds.p_now("ana") == 0.9, "the higher of two listings"
    assert odds.p_now("bo") == NEUTRAL_START / 100 and odds.status_of("bo") == "doubt"
    assert odds.p_now("cai") == ABSENT_START / 100 and odds.status_of("cai") == ""
    assert StartOdds([{"player_name": "Ana", "start_pct": "80"}], None).listed == set()

    sept = dt.date(2026, 9, 28)
    assert prognosis("Lesión Desde 13/09 (15 días) Duda para la jornada 8", sept) \
        == ("doubt_for", 8)
    assert prognosis("x Baja confirmada para la jornada 9", sept) == ("out_for", 9)
    assert prognosis("x Disponible para la jornada 10", sept) == ("available_from", 10)
    assert prognosis("x Baja hasta mediados de octubre", sept) \
        == ("out_until", dt.date(2026, 10, 15))
    assert prognosis("x Baja hasta enero", sept) == ("out_until", dt.date(2027, 1, 15)), \
        "a month already past is next year's"
    assert prognosis("x Baja hasta finales de enero 2028", sept) \
        == ("out_until", dt.date(2028, 1, 25))
    assert prognosis("x Baja hsata principios de noviembre", sept) \
        == ("out_until", dt.date(2026, 11, 5)), "the site's own typo"
    assert prognosis("x Baja indefinida", sept) == ("indefinite", None)
    assert prognosis("Sancionado", sept) is None and prognosis("", sept) is None
    assert bucket(("doubt_for", 8), 8, sept, 8) == "doubt_for:at"
    assert bucket(("out_until", dt.date(2026, 10, 15)), 9, dt.date(2026, 10, 5), 8) \
        == "out_until:days_before"
    assert Availability().of(("out_for", 9), 9, sept, 8) == 0.0
    assert Availability().of(("available_from", 8), 9, sept, 8) == 1.0
    assert prognosis_of("suspended", "", sept) == ("out_for", None)
    assert prognosis_of("doubt", "Duda para la jornada 9", sept) == ("doubt_for", 9)
    assert prognosis_of("ok", "Duda para la jornada 9", sept) is None
    assert prognosis_of("gone", "", sept) == ("indefinite", None)
    both = StartOdds([{"player_name": "Ana", "start_pct": "90", "status": "ok"}], xw,
                     app={"ana": "injured"})
    assert both.status_of("ana") == "", "injuries come from futbolfantasy"
    gone = StartOdds([{"player_name": "Ana", "start_pct": "90", "status": "ok"}], xw,
                     app={"ana": "out_of_league"})
    assert gone.fit("ana", 9, sept, 8) == 0.0, "left the league: out for good"
    assert bucket(("out_for", None), 8, sept, 8) == "out_for:at"
    assert bucket(("out_for", None), 9, sept, 8) == "out_for:after", \
        "a suspension is the next match, whichever jornada that is"

    def out(at, j, status, mins, note=""):
        return Outcome(at, j, "m", "t:a", "a", True, 0.8, status, True, mins, note)
    fit_days = ["2026-08-%02dT1800Z" % d for d in (1, 8, 15)]
    history = [out(a, j + 1, "ok", 90.0) for j, a in enumerate(fit_days)]
    history += [out("2026-08-22T1800Z", 4, "doubt", 0.0, "Duda para la jornada 4"),
                out("2026-08-29T1800Z", 5, "ok", 90.0)]
    fitted = fit_availability(history, k=0.0)
    assert fitted.level["doubt_for:at"] == 0.0, "doubtful for 4, missed 4"
    assert fitted.level["doubt_for:after"] == 1.0, "and was back for 5"
    assert fit_availability([]).level == PRIOR
    odds = StartOdds([{"player_name": "Ana", "status": "doubt",
                       "observed_at": "2026-09-28T0400Z",
                       "note": "Duda para la jornada 8"}], xw, avail=fitted)
    assert odds.fit("ana", 8, sept, 8) == 0.0
    assert odds.fit("ana", 9, sept, 8) == 1.0
    assert odds.fit("bo", 8, sept, 8) == 1.0, "not flagged, fit"
    rec = StartOdds([{"player_name": "Ana", "start_pct": "20", "status": "ok"},
                     {"player_name": "Bo", "start_pct": "0", "status": "injured",
                      "note": "Duda para la jornada 8"}], xw,
                    history={"ana": (9.0, 10.0)}, last_fit={"bo": 70.0})
    assert rec.picked("ana", True) == shrink(0.2, PICK_K, 9.0, 10.0)
    assert rec.picked("ana", False) == shrink(NEUTRAL_START / 100, PICK_K, 9.0, 10.0)
    assert rec.picked("bo", True) == rec.picked("bo", False) == 0.7, \
        "no record: his listing before the injury, not the 0% it caused"
    assert StartOdds([{"player_name": "Bo", "status": "injured"}], xw).picked(
        "bo", False) == ABSENT_START / 100, "never listed fit: not in the squad"
    assert last_fit_listing([
        Outcome("2026-08-01T1800Z", 1, "m", "t:a", "a", True, 0.8, "ok", True, 90.0),
        Outcome("2026-08-08T1800Z", 2, "m", "t:a", "a", True, 0.0, "injured", False, 0.0),
    ]) == {"a": 80.0}

    assert abs(_platt(0.5, 0.0, 1.0) - 0.5) < 1e-6
    assert abs(_platt(0.8, 0.0, 1.0) - 0.8) < 1e-6
    assert _platt(0.8, 0.0, 3.0) > 0.8 and _platt(0.2, 0.0, 3.0) < 0.2
    assert _platt(0.3, 1.5, 3.0) > _platt(0.3, 0.0, 3.0)
    for p, al, be in [(0.999, 0.0, 5.8), (0.001, 0.0, 5.8), (1.0, 3.0, 5.8),
                      (0.0, -3.0, 0.2)]:
        assert FLOOR <= _platt(p, al, be) <= CEIL

    raw = Calibration()
    assert raw.p(80.0) == 0.8
    assert raw.p(100.0) == 1.0 and raw.p(0.0) == 0.0
    assert raw.p(None) == 0.0

    sharp = [Obs(p, st, "sheet%d" % (i % 6))
             for i, (p, st) in enumerate([(0.8, 1), (0.7, 1), (0.3, 0),
                                          (0.2, 0)] * 12)]
    cal = fit(sharp)
    assert cal.beta > 1.0 and cal.p(80.0) > 0.8, cal
    noise = [Obs(0.5, i % 2, "sheet%d" % (i % 5)) for i in range(20)]
    for obs in (noise, [Obs(0.5, 1)],
                [Obs(0.9, i < 11, "same") for i in range(22)]):
        got = fit(obs)
        assert (got.alpha, got.beta) == (0.0, 1.0), got

    grid_obs = sharp + [Obs(0.4, 0, "sheet2")]
    brier = _grid_brier(grid_obs)
    for i, j in [(0, 0), (6, 2), (3, 9)]:
        c = Calibration(INTERCEPT[i], SLOPE[j])
        assert abs(brier[i, j] - _brier(c, grid_obs) / len(grid_obs)) < 1e-12


    locks = {1: dt.datetime(2026, 8, 15, 19, 30, tzinfo=dt.timezone.utc)}
    before, after = "2026-08-14T1000Z", "2026-08-16T1000Z"
    lineups = [
        {"observed_at": before, "source": "futbolfantasy", "team_slug": "t",
         "player_slug": "starter-man", "player_name": "Starter Man",
         "start_pct": "80", "role": "starter"},
        {"observed_at": before, "source": "futbolfantasy", "team_slug": "t",
         "player_slug": "bench-man", "player_name": "Bench Man",
         "start_pct": "20", "role": "sub"},
        {"observed_at": before, "source": "futbolfantasy", "team_slug": "t",
         "player_slug": "vague-man", "player_name": "Vague Man",
         "start_pct": "", "role": "sub"},
        {"observed_at": after, "source": "futbolfantasy", "team_slug": "t",
         "player_slug": "starter-man", "player_name": "Starter Man",
         "start_pct": "99", "role": "starter"},
        {"observed_at": before, "source": "futbolfantasy", "team_slug": "other",
         "player_slug": "elsewhere", "player_name": "Elsewhere",
         "start_pct": "90", "role": "starter"},
    ]
    starters = [
        {"match_id": "m1", "team_slug": "t", "player_slug": "starter-man",
         "player_name": "Starter Man", "role": "starter", "minute": ""},
        {"match_id": "m1", "team_slug": "t", "player_slug": "bench-man",
         "player_name": "Bench Man", "role": "sub", "minute": ""},
        {"match_id": "m1", "team_slug": "t", "player_slug": "surprise-man",
         "player_name": "Surprise Man", "role": "starter", "minute": "45"},
        {"match_id": "m9", "team_slug": "t", "player_slug": "starter-man",
         "player_name": "Starter Man", "role": "starter", "minute": ""},
    ]
    outs = outcomes(lineups, starters, locks, {"m1": 1, "m9": 9}, None)
    by = {o.who: o for o in outs}
    assert set(by) == {"t:starter-man", "t:bench-man", "t:vague-man",
                       "t:surprise-man"}, by
    assert (by["t:starter-man"].ff, by["t:starter-man"].mins) == (0.8, 90.0)
    assert by["t:bench-man"].mins == 0.0
    assert by["t:vague-man"].listed and by["t:vague-man"].ff is None
    assert not by["t:vague-man"].in_squad
    assert not by["t:surprise-man"].listed and by["t:surprise-man"].mins == 45.0
    assert outcomes(lineups, [], locks, {}, None) == []

    obs = {o.ff: o.started for o in observations(outs)}
    assert obs == {0.8: 1.0, 0.2: 0.0, 0.6: 0.0, 0.15: 1.0}, obs
    npct, apct = fit_start_fallbacks(outs)
    assert abs(npct - (8 * 60 + 1 * 0) / 9) < 1e-9, npct
    assert abs(apct - (8 * 15 + 1 * 100) / 9) < 1e-9, apct
    assert fit_start_fallbacks([]) == (60.0, 15.0)

    print("ffcore.startprob self-test OK")


if __name__ == "__main__":
    _selftest()
