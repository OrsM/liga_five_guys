from __future__ import annotations

import datetime as dt
import math
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from ffcore.parse import Rows, snapshot_stamp, year_for
from stats import shrink

__all__ = ["Calibration", "StartOdds", "Availability", "prognosis", "prognosis_of",
           "bucket", "prognosis_bucket", "fit_availability", "platt",
           "NEUTRAL_START", "ABSENT_START"]

FLOOR, CEIL = 0.01, 0.97
NEUTRAL_START = 60.0
ABSENT_START = 15.0
FALLBACK_K = 8.0


def platt(p: float, alpha: float, beta: float) -> float:
    """A start % (as a share) through alpha + beta x its log-odds, kept in [FLOOR, CEIL]."""
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

    def p(self, ff_pct: float | None) -> float:
        if ff_pct is None:
            return 0.0
        if (self.alpha, self.beta) == (0.0, 1.0):
            return ff_pct / 100.0
        return platt(ff_pct / 100.0, self.alpha, self.beta)


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


def bucket(prog: tuple[str, Any], jornada: int, when: dt.date | None,
           next_j: int) -> str:
    """Where a jornada falls against a prognosis: 'doubt_for:at',
    'out_until:days_before', ... - the row of the availability table."""
    kind, arg = prog
    if kind == "indefinite":
        return kind
    if arg is None:
        arg = next_j
    if kind == "out_until":
        assert when is not None, "an out_until prognosis needs the jornada's date"
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

    def of(self, prog: tuple[str, Any], jornada: int, when: dt.date | None,
           next_j: int) -> float:
        return self.level[bucket(prog, jornada, when, next_j)]


def fit_availability(tally: Iterable[tuple[str, float, float]], k: float | None = None
                     ) -> Availability:
    """Availability from availability_tally's rows (bucket, played,
    expected): played over expected, shrunk toward the bucket's PRIOR."""
    k = AVAIL_K if k is None else k
    return Availability({b: min(1.0, shrink(PRIOR[b], k, got, fit)) for b, got, fit in tally})


def prognosis_bucket(status: str, note: str, seen_at: str, jornada: int, at: str,
                     next_j: int) -> str | None:
    """bucket() for SQL: a listing (status, note) seen at one stamp against a
    jornada at another, its next jornada next_j; None if he was fit."""
    prog = prognosis_of(status, note, _day(seen_at))
    return None if prog is None else bucket(prog, jornada, _day(at), next_j)


def _day(stamp: str) -> dt.date:
    when = snapshot_stamp(stamp)
    assert when is not None, "a row's observed_at is always a stamp: %r" % stamp
    return when.date()


class StartOdds:
    """Whether a player plays in a jornada, as two factors: the chance he is
    fit (1 unless flagged; else his prognosis, through Availability) times the
    chance he is picked if fit. history is his record this season as
    (appearances, matchday squads), which is what picking is pulled toward."""

    def __init__(self, xi: Rows, cal: Calibration | None = None,
                 avail: Availability | None = None,
                 history: dict[str, tuple[float, float]] | None = None,
                 last_fit: dict[str, float] | None = None,
                 app: dict[str, str] | None = None) -> None:
        self.cal = cal or Calibration()
        self.avail = avail or Availability()
        self.history = history or {}
        self.last_fit = last_fit or {}
        self.start_pct: dict[str, float] = {}
        self.listed: set[str] = set()
        self.status: dict[str, str] = {}
        self.prognosis: dict[str, tuple[str, Any] | None] = {}
        for r in xi or []:
            key = r.get("player")
            if not key:
                continue
            self.listed.add(key)
            p = r.get("start_pct")
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
        for flagged, app_status in (app or {}).items():
            status = APP_STATUS.get(app_status)
            if status and (flagged not in self.status or status == "gone"):
                self.status[flagged] = status
                self.prognosis[flagged] = prognosis_of(status, "", dt.date.min)

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


def _selftest() -> None:
    from ffcore.tidy import typed

    odds = StartOdds(typed("lineups", [{"player": "ana", "player_name": "Ana", "start_pct": "80", "status": "ok"},
                      {"player": "ana", "player_name": "Ana", "start_pct": "90"},
                      {"player": "bo", "player_name": "Bo", "start_pct": "", "status": "doubt"},
                      {"player_name": "Nobody", "start_pct": "99"}]))
    assert odds.p_now("ana") == 0.9, "the higher of two listings"
    assert odds.p_now("bo") == NEUTRAL_START / 100 and odds.status_of("bo") == "doubt"
    assert odds.p_now("cai") == ABSENT_START / 100 and odds.status_of("cai") == ""
    assert StartOdds(typed("lineups", [{"player_name": "Ana", "start_pct": "80"}])).listed \
        == set(), "a row no player was resolved for lists nobody"

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
    both = StartOdds(typed("lineups", [{"player": "ana", "player_name": "Ana", "start_pct": "90", "status": "ok"}]),
                     app={"ana": "injured"})
    assert both.status_of("ana") == "", "injuries come from futbolfantasy"
    gone = StartOdds(typed("lineups", [{"player": "ana", "player_name": "Ana", "start_pct": "90", "status": "ok"}]),
                     app={"ana": "out_of_league"})
    assert gone.fit("ana", 9, sept, 8) == 0.0, "left the league: out for good"
    assert bucket(("out_for", None), 8, sept, 8) == "out_for:at"
    assert bucket(("out_for", None), 9, sept, 8) == "out_for:after", \
        "a suspension is the next match, whichever jornada that is"

    assert abs(platt(0.5, 0.0, 1.0) - 0.5) < 1e-6
    assert abs(platt(0.8, 0.0, 1.0) - 0.8) < 1e-6
    assert platt(0.8, 0.0, 3.0) > 0.8 and platt(0.2, 0.0, 3.0) < 0.2
    assert platt(0.3, 1.5, 3.0) > platt(0.3, 0.0, 3.0)
    for p, al, be in [(0.999, 0.0, 5.8), (0.001, 0.0, 5.8), (1.0, 3.0, 5.8),
                      (0.0, -3.0, 0.2)]:
        assert FLOOR <= platt(p, al, be) <= CEIL

    raw = Calibration()
    assert raw.p(80.0) == 0.8
    assert raw.p(100.0) == 1.0 and raw.p(0.0) == 0.0
    assert raw.p(None) == 0.0
    assert fit_availability([]).level == PRIOR
    assert fit_availability([("doubt_for:at", 0.0, 2.0)], k=0.0).level["doubt_for:at"] == 0.0
    assert prognosis_bucket("doubt", "Duda para la jornada 4", "2026-08-22T1800Z", 5,
                            "2026-08-29T1800Z", 4) == "doubt_for:after"
    assert prognosis_bucket("ok", "", "2026-08-22T1800Z", 5, "2026-08-29T1800Z", 4) is None

    _selftest_views()


def _selftest_views() -> None:
    """schema.sql's start views: start_outcomes, fit_rate, last_fit,
    availability_tally, start_calibration."""
    import tempfile
    from pathlib import Path

    from ffcore.clock import set_now
    from ffcore.db import record, rows
    from ffcore.rules import MATCH_LEN
    from ffcore.tidy import store_sql, tables_in

    def views(matches: list[dict[str, str]], lineups: list[dict[str, str]],
              starters: list[dict[str, str]], *queries: str) -> list[list[tuple[Any, ...]]]:
        with tempfile.TemporaryDirectory() as tmp, tables_in(Path(tmp)):
            try:
                set_now(dt.datetime(2026, 12, 1, tzinfo=dt.timezone.utc))
                record([{"name": n, "value": v} for n, v in [
                    ("MATCH_LEN", MATCH_LEN), ("NEUTRAL_START", NEUTRAL_START),
                    ("ABSENT_START", ABSENT_START), ("FALLBACK_K", FALLBACK_K)]], "model_constants")
                record([{"player_id": s, "ff_slug": s} for s in
                        sorted({r["player_slug"] for r in lineups + starters})], "crosswalk")
                store_sql("matches", matches)
                store_sql("lineups", [{"source": "futbolfantasy", **r} for r in lineups])
                store_sql("starters", [{"observed_at": "2026-11-30T0000Z", **r} for r in starters])
                return [[tuple(r) for r in rows(q)] for q in queries]
            finally:
                set_now(None)

    def match(mid: str, j: int, team: str, kickoff: str) -> dict[str, str]:
        return {"observed_at": "2026-08-01T0000Z", "match_id": mid, "jornada": str(j),
                "home": team, "away": "opp-" + mid, "kickoff": kickoff}

    def listing(at: str, team: str, slug: str, pct: str, status: str = "ok",
                note: str = "") -> dict[str, str]:
        return {"observed_at": at, "team_slug": team, "player_slug": slug, "player_name": slug,
                "start_pct": pct, "status": status, "note": note}

    def squad(mid: str, team: str, slug: str, role: str = "starter", minute: str = ""
              ) -> dict[str, str]:
        return {"match_id": mid, "team_slug": team, "player_slug": slug, "player_name": slug,
                "role": role, "minute": minute}

    before, after = "2026-08-14T1000Z", "2026-08-16T1000Z"
    outs, cal = views(
        [match("m1", 1, "t", "2026-08-15T21:30:00+02:00"), match("m9", 9, "t", "")],
        [listing(before, "t", "starter-man", "80"), listing(before, "t", "bench-man", "20"),
         listing(before, "t", "vague-man", ""), listing(after, "t", "starter-man", "99"),
         listing(before, "other", "elsewhere", "90"),
         listing("2026-08-10T1000Z", "t", "gone-man", "50")],
        [squad("m1", "t", "starter-man"), squad("m1", "t", "bench-man", "sub"),
         squad("m1", "t", "surprise-man", "starter", "45"), squad("m9", "t", "starter-man")],
        "SELECT player_slug, at, jornada, grp, player, listed, ff, status, mins FROM start_outcomes",
        "SELECT neutral_start, absent_start FROM start_calibration")
    assert {o[0]: o[1:] for o in outs} == {
        "starter-man": ("2026-08-15T1930Z", 1, "m1:t", "starter-man", 1, 0.8, "ok", 90.0),
        "bench-man": ("2026-08-15T1930Z", 1, "m1:t", "bench-man", 1, 0.2, "ok", 0.0),
        "vague-man": ("2026-08-15T1930Z", 1, "m1:t", "vague-man", 1, None, "ok", 0.0),
        "surprise-man": ("2026-08-15T1930Z", 1, "m1:t", "surprise-man", 0, None, "", 45.0),
    }, "gone-man: not in the club's last listing before the lock, so not listed"
    (neutral, absent), = cal
    assert abs(neutral - (8 * 60 + 1 * 0) / 9) < 1e-9, neutral
    assert abs(absent - (8 * 15 + 1 * 100) / 9) < 1e-9, absent

    weeks = ["2026-08-%02dT18:00:00+00:00" % d for d in (1, 8, 15, 22, 29)]
    tally, last, rate = views(
        [match("m%d" % j, j, "t", w) for j, w in enumerate(weeks, 1)],
        [listing("2026-08-%02dT1000Z" % d, "t", "a", pct, status, note) for d, pct, status, note in
         [(1, "80", "ok", ""), (8, "80", "ok", ""), (15, "80", "ok", ""),
          (22, "0", "doubt", "Duda para la jornada 4"), (29, "70", "ok", "")]],
        [squad("m%d" % j, "t", "a") for j in (1, 2, 3, 5)] + [squad("m4", "t", "b")],
        "SELECT bucket, played, expected FROM availability_tally",
        "SELECT player, pct FROM last_fit", "SELECT player, rate FROM fit_rate")
    assert sorted(tally) == [("doubt_for:after", 1, 1.0), ("doubt_for:at", 0, 1.0)], \
        "doubtful for 4, missed 4, back for 5"
    assert last == [("a", 70.0)] and rate == [("a", 1.0)], (last, rate)

    teams = ["t%d" % g for g in range(6)]
    sharp = [(t, "p%d%s" % (i, t), pct, pct >= 70)
             for t in teams for i, pct in enumerate([80, 70, 30, 20] * 2)]
    (got,), = views(
        [match("m" + t, 1, t, "2026-08-15T19:30:00+00:00") for t in teams],
        [listing(before, t, slug, str(pct)) for t, slug, pct, _ in sharp],
        [squad("m" + t, t, slug) for t, slug, _, played in sharp if played],
        "SELECT alpha, beta, neutral_start, absent_start FROM start_calibration")
    fitted = Calibration(*got)
    assert fitted.beta > 1.0 and fitted.p(80.0) > 0.8, fitted

    teams = ["t%d" % g for g in range(5)]
    noise = [(t, "p%d%s" % (i, t), i % 2) for t in teams for i in range(4)]
    (got,), = views(
        [match("m" + t, 1, t, "2026-08-15T19:30:00+00:00") for t in teams],
        [listing(before, t, slug, "50") for t, slug, _ in noise],
        [squad("m" + t, t, slug) for t, slug, played in noise if played],
        "SELECT alpha, beta FROM start_calibration")
    assert got == (0.0, 1.0), "noise: the raw %"

    (got,), = views([], [], [], "SELECT * FROM start_calibration")
    assert got == (0.0, 1.0, NEUTRAL_START, ABSENT_START), "no outcomes yet: the constants"

    print("ffcore.startprob self-test OK")


if __name__ == "__main__":
    _selftest()
