"""
methodology.py — how the forecast works, and how it is doing against reality.

Writes .runtime/parts/methodology.md, which digest.py stitches in as the LAST
section of the appendix. Two halves:

  1. The formula, in words — pulled together from ffcore/score.py's
     constants so the text cannot drift from the code silently.
  2. Forecast vs actual — every prediction in data/decisions/squad_log.csv
     joined against realised match points in data/season/live/perjornada_*
     (written by points.py), over the last WINDOW_DAYS days.

The join, precisely: for each per-jornada row where a player's games went up,
take the LAST prediction logged strictly BEFORE the interval began. Nothing
predicted after the fact is ever scored — a forecast you could only have made
with hindsight is not a forecast. Predicted points for the interval are
score × games_delta, since the score is per match.

The sample is your own squad only — squad_log records the players the scorer
actually rated for you, which is also the sample you care about. It will be
thin for weeks: a jornada gives ~15 pairs. The section says so rather than
hiding it, and fills itself in as the season runs. No jornada yet means the
section states that and stops; an empty comparison is a fact, not an error.

Nothing else imports this. Deps: stdlib only.

    python src/methodology.py             # writes .runtime/parts/methodology.md
    python src/methodology.py --selftest  # pure join logic, no IO
"""

from __future__ import annotations

import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ffcore.fixture import FIX_BAND, HOME_EDGE  # noqa: E402
from ffcore.score import ABSENT_START, NEUTRAL_START, SHRINK_K  # noqa: E402
from ffcore.text import norm, resolve  # noqa: E402
from ffcore.tidy import (run_now,  # noqa: E402
                         DECISIONS, PARTS, LINEUP_SOURCE,  # noqa: E402
                         DAILY_FRESH_DAYS, EVERY_RUN_FRESH_DAYS,
                         SEASON, TIDY, age_phrase, load_elo,
                         stale_feeds,
                         load_lineups,
                         read_csv, snapshot_stamp, write_lines)

LIVE = SEASON / "live"
WINDOW_DAYS = 21

# Rows for the one "not graded, and why" table. Filled by the graders as they
# discard a claim, so nothing is dropped silently: a source that looks good
# because half its calls were thrown away is the failure mode here.
NOT_GRADED: list[str] = []


# ---------------------------------------------------------------------------
# pure join logic — selftested below
# ---------------------------------------------------------------------------

def latest_before(preds: list[tuple[dt.datetime, dict]],
                  cutoff: dt.datetime) -> dict | None:
    """The last prediction logged strictly before `cutoff`, or None.

    `preds` must be sorted by timestamp ascending. Returns the whole factor
    dict — score, and the terms that produced it — so the caller can attribute
    an error rather than only measure it.
    """
    best = None
    for when, fac in preds:
        if when < cutoff:
            best = fac
        else:
            break
    return best


def pair(actuals: list[dict],
         preds: dict[str, list[tuple[dt.datetime, dict]]]) -> list[dict]:
    """Join realised per-jornada rows with the prediction that preceded them.

    actuals: parsed perjornada rows with keys `key`, `keys` (all name forms),
    `from_dt`, `points_delta`, `games_delta`. preds: {norm name: [(dt, factor
    dict)] sorted ascending}. Returns one dict per matched pair, carrying the
    factors that need grading alongside the error.
    """
    out = []
    for a in actuals:
        if a["games_delta"] < 1:
            continue
        fac = None
        for k in a["keys"]:
            hits = preds.get(k)
            if hits:
                fac = latest_before(hits, a["from_dt"])
            if fac is not None:
                break
        if fac is None:
            continue
        predicted = fac["score"] * a["games_delta"]
        out.append({
            "name": a["name"],
            "predicted": predicted,
            "actual": a["points_delta"],
            "per_match": fac["score"],
            "matches": a["games_delta"],
            "err": predicted - a["points_delta"],
            "fix": fac.get("fix"),
        })
    return out


BUCKETS = [(-1e9, 2, "under 2"), (2, 3, "2–3"), (3, 4, "3–4"), (4, 1e9, "4+")]

# Where a fixture stops being a median one, for grading purposes only. Wide
# enough that a home game against an average side lands in "neutral".
FIX_EDGE = 0.03

FIX_BUCKETS = [(-1e9, 1.0 - FIX_EDGE, "harder"),
               (1.0 - FIX_EDGE, 1.0 + FIX_EDGE, "neutral"),
               (1.0 + FIX_EDGE, 1e9, "easier")]


def fixture_rows(pairs: list[dict]) -> tuple[list[tuple], int]:
    """[(label, n, mean forecast/match, mean actual/match, mean err/match)],
    plus how many pairs carried no fixture factor at all.

    This is what grades FIX_BAND. If the model's fixture term is too WIDE, the
    easier bucket over-forecasts and the harder one under-forecasts — a
    positive mean error against an easy draw and a negative one against a hard
    one. Too NARROW and the signs reverse. Both are only readable once each
    bucket has a real n; the report prints n so a two-row bucket cannot be
    mistaken for a verdict.
    """
    known = [p for p in pairs if p.get("fix") is not None]
    out = []
    for lo, hi, label in FIX_BUCKETS:
        grp = [p for p in known if lo <= p["fix"] < hi]
        if not grp:
            continue
        n = len(grp)
        per = lambda f: sum(p[f] / p["matches"] for p in grp) / n  # noqa: E731
        out.append((label, n, per("predicted"), per("actual"), per("err")))
    return out, len(pairs) - len(known)


def bucket_rows(pairs: list[dict]) -> list[tuple[str, int, float, float]]:
    """(label, n, mean predicted per match, mean actual per match)."""
    out = []
    for lo, hi, label in BUCKETS:
        grp = [p for p in pairs if lo <= p["per_match"] < hi]
        if not grp:
            continue
        n = len(grp)
        mp = sum(p["predicted"] / p["matches"] for p in grp) / n
        ma = sum(p["actual"] / p["matches"] for p in grp) / n
        out.append((label, n, mp, ma))
    return out


# ---------------------------------------------------------------------------
# grading the probable-XI sources — the gate for LINEUP_SOURCE
#
# Grades P(APPEAR) against points.py's per-jornada `games` diff, not P(start)
# — a 20-minute substitute counts, which flatters both sources equally so the
# COMPARISON stays valid even though the level doesn't. Why appearance (not
# starters.csv) is the ground truth here, and why the graded universe is
# market-priced players only: docs/notes/methodology.md
# #start-grade-appearance-vs-real-starters.
# ---------------------------------------------------------------------------

# A claim inside this band of the middle is not a call either way, and neither
# source is graded on one: it is what they publish when they do not know.
START_EDGE = 10.0


def appearances(actuals: list[dict]) -> list[tuple[dt.datetime, set]]:
    """[(interval start, {keys of everyone who played in it})], ascending.

    Built from the same rows the forecast join uses. Anyone absent from an
    interval did not play in it — points.py emits movers only.
    """
    by_start: dict[dt.datetime, set] = {}
    for a in actuals:
        seen = by_start.setdefault(a["from_dt"], set())
        if a["games_delta"] >= 1:
            seen.update(a["keys"])
    return sorted(by_start.items())


def start_grade(intervals, claims, universe=None):
    """Per source: did the players it called actually appear?

    Returns (numbered, named, skipped):

      numbered  (source, n, mean claim %, appeared %, Brier) for claims that
                carry a percentage. Brier is the mean squared error of the
                probability, so lower is better and 0.25 is a coin flip.
      named     (source, n, appeared %) for calls with no number on them —
                analiticafantasy's `titular` is a final answer, not a 100%,
                and turning it into one would invent the missing constant.
      skipped   how many claims fell in the undecided middle band.

    Whoever wins this table earns tidy.LINEUP_SOURCE. Nothing here changes
    which source is read — that is a decision to take once the n is real.
    """
    per: dict[str, dict[str, list]] = {}
    for r in claims:
        src = (r.get("source") or "").strip()
        key = norm(r.get("player_name", ""))
        when = snapshot_stamp(r.get("observed_at", ""))
        if not src or not key or when is None:
            continue
        if universe is not None and key not in universe:
            continue
        per.setdefault(src, {}).setdefault(key, []).append((when, r))
    for byname in per.values():
        for v in byname.values():
            v.sort(key=lambda t: t[0])

    num: dict[str, list] = {}
    nam: dict[str, list] = {}
    skipped = 0
    for interval in intervals:
        # A third element, when present, is the interval's population: the
        # clubs whose eleven we hold. Appearance intervals cover every club and
        # carry none.
        start, played = interval[0], interval[1]
        teams = interval[2] if len(interval) > 2 else None
        for src, byname in per.items():
            for key, hist in byname.items():
                row = latest_before(hist, start)
                if row is None:
                    continue
                if teams is not None \
                        and (row.get("team_slug") or "").strip() not in teams:
                    continue
                # The slug is tried as well as the name because one truth set —
                # the realised starters off the match pages — carries the same
                # /jugadores/ ids as futbolfantasy's claims. That join is exact
                # where the name join is merely usually right.
                slug = (row.get("player_slug") or "").strip()
                hit = 1.0 if key in played or (slug and slug in played) else 0.0
                try:
                    pct = float(row.get("start_pct"))
                except (TypeError, ValueError):
                    pct = None
                if pct is None:
                    if (row.get("role") or "") == "starter":
                        nam.setdefault(src, []).append(hit)
                    else:
                        skipped += 1
                elif abs(pct - 50.0) < START_EDGE:
                    skipped += 1
                else:
                    num.setdefault(src, []).append((pct, hit))

    numbered = []
    for src in sorted(num):
        rows = num[src]
        n = len(rows)
        mean_claim = sum(p for p, _ in rows) / n
        rate = 100.0 * sum(h for _, h in rows) / n
        brier = sum((p / 100.0 - h) ** 2 for p, h in rows) / n
        numbered.append((src, n, mean_claim, rate, brier))
    named = [(src, len(v), 100.0 * sum(v) / len(v))
             for src, v in sorted(nam.items())]
    return numbered, named, skipped


# ---------------------------------------------------------------------------
# grading against who ACTUALLY started
#
# starters.csv (confirmed elevens, not the appearance proxy above) graded
# against the JORNADA LOCK — the earliest kickoff observed that round, not
# each match's own — so a source is never credited with information it
# couldn't have used. A round with no observed lock is ungraded, not
# assumed. Why: docs/notes/methodology.md#start-grade-appearance-vs-real-starters.
# ---------------------------------------------------------------------------

def team_slug_of(side: str, slugs) -> str | None:
    """Our team slug for a fixture-page side name, or None.

    "Racing Santander" -> "racing", "Real Betis" -> "betis". The two sites
    spell clubs differently and neither publishes an id the other uses, so this
    is the same exact-then-substring, two-candidates-is-nothing rule the
    fixture board joins on — reused rather than reimplemented.
    """
    from ffcore.fixture import match_team

    spelled = {s.replace("-", " "): s for s in slugs}
    hit = match_team(side, list(spelled))
    return spelled.get(hit) if hit else None


def jornada_locks(matches: list[dict],
                  fixtures: list[dict]) -> dict[int, dt.datetime]:
    """{jornada: earliest kickoff observed in it} — the moment it locked."""
    from ffcore.tidy import kickoff_stamp

    jornada_of: dict[tuple[str, str], int] = {}
    for m in matches:
        try:
            jornada_of[(m["home"], m["away"])] = int(m["jornada"])
        except (KeyError, ValueError, TypeError):
            continue
    slugs = {s for pair_ in jornada_of for s in pair_}

    locks: dict[int, dt.datetime] = {}
    for f in fixtures:
        when = kickoff_stamp(f.get("kickoff"))
        home = team_slug_of(f.get("home") or "", slugs)
        away = team_slug_of(f.get("away") or "", slugs)
        jor = jornada_of.get((home, away))
        if when is None or jor is None:
            continue
        if jor not in locks or when < locks[jor]:
            locks[jor] = when
    return locks


def market_names(market: list[dict], slugs) -> dict[str, list[dict]]:
    """{team slug: [one row per player the market prices for that club]}.

    One row per NAME: market.csv holds a row per player per snapshot, and
    handing all of them to resolve() would make every player ambiguous with
    himself.
    """
    latest = {}
    for r in market:
        if r.get("name"):
            latest[r["name"]] = r
    out: dict[str, list[dict]] = {}
    for r in latest.values():
        slug = team_slug_of(r.get("team") or "", slugs)
        if slug:
            out.setdefault(slug, []).append(r)
    return out


_XW_CACHE: list = []


def _market_key(slug) -> str:
    """The market key for a futbolfantasy player-slug, or ""."""
    slug = (slug or "").strip()
    if not slug:
        return ""
    if not _XW_CACHE:
        from ffcore.tidy import load_crosswalk
        _XW_CACHE.append(load_crosswalk())
    xw = _XW_CACHE[0]
    return (xw.player(ff_slug=slug) or "") if xw else ""


def start_intervals(matches: list[dict], starters: list[dict],
                    fixtures: list[dict], market: list[dict] = ()):
    """([(lock, {keys who started}, {teams captured})], graded, no-lock rounds).

    Keys are both the normalised name and the player slug, so a claim can be
    matched on whichever it carries. Rows repeat across snapshots — a match page
    is stored once and carried forward into every later manifest — so they are
    deduplicated on (match, player) before anything is counted.

    A match page prints the short name — "Abqar", "Blanco" — where the other
    source publishes "Abdel Abqar". So each starter is also resolved to his
    market name against his own club's squad, and that name goes in the key set
    too. Ambiguity inside a squad ("Romero", of whom Sevilla have two) resolves
    to nothing rather than to a guess: that call goes ungraded for the source
    that has no slug on it, which is the honest outcome.

    The team set is the interval's population. Absence from the key set only
    means "did not start" for a club whose eleven we actually hold; for the rest
    of the round it means nothing, and grading them would score a source down
    for matches we never read.
    """
    jornada_of = {}
    for m in matches:
        try:
            jornada_of[m["match_id"]] = int(m["jornada"])
        except (KeyError, ValueError, TypeError):
            continue
    locks = jornada_locks(matches, fixtures)
    squads = market_names(market, {r.get("team_slug") for r in starters})

    seen, by_round, teams, ungraded = set(), {}, {}, set()
    graded = 0
    for r in starters:
        if (r.get("role") or "") != "starter":
            continue
        jor = jornada_of.get(r.get("match_id"))
        if jor is None:
            continue
        mark = (r.get("match_id"), r.get("player_name"))
        if mark in seen:
            continue
        seen.add(mark)
        if jor not in locks:
            ungraded.add(jor)
            continue
        graded += 1
        team = (r.get("team_slug") or "").strip()
        priced, _ = resolve(r.get("player_name", ""), squads.get(team, []))
        # THE MARKET KEY FIRST — the site's own id, reached from the slug on
        # this very row through the crosswalk. The name and the slug stay in
        # the set beside it: this is a membership test, and the predictions
        # it is matched against were keyed by name for every run before
        # squad_log carried the id. Dropping them would ungrade the history.
        by_round.setdefault(locks[jor], set()).update(
            k for k in (_market_key(r.get("player_slug")),
                        norm(r.get("player_name", "")),
                        (r.get("player_slug") or "").strip(),
                        norm(priced["name"]) if priced else "") if k)
        teams.setdefault(locks[jor], set()).add(team)
    out = [(lock, keys, teams[lock]) for lock, keys in sorted(by_round.items())]
    return out, graded, sorted(ungraded)


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------

def load_actuals() -> tuple[list[dict], str]:
    """Per-jornada rows from the newest season's file, parsed and windowed."""
    files = sorted(LIVE.glob("perjornada_*.csv")) if LIVE.exists() else []
    if not files:
        return [], ""
    label = files[-1].stem.replace("perjornada_", "")
    cutoff = (run_now()
              - dt.timedelta(days=WINDOW_DAYS))
    rows = []
    for r in read_csv(files[-1]):
        try:
            from_dt = snapshot_stamp(r["from_stamp"])
            to_dt = snapshot_stamp(r["to_stamp"])
            gd = float(r["games_delta"] or 0)
            pd_ = float(r["points_delta"] or 0)
        except (KeyError, ValueError, TypeError):
            continue
        if to_dt is None or from_dt is None or to_dt < cutoff:
            continue
        full = r.get("player_name_full", "")
        short = r.get("player_name", "")
        keys = [k for k in {norm(full), norm(short)} if k]
        rows.append({"name": full or short, "keys": keys,
                     "from_dt": from_dt, "points_delta": pd_,
                     "games_delta": gd})
    return rows, label


def load_universe() -> set:
    """Every player the app prices — the only players worth grading a call on.

    Read straight off the market table rather than through Market, because all
    this needs is the set of names and building the valuation index would be
    the expensive half of the job.
    """
    return {norm(r.get("name", "")) for r in read_csv(TIDY / "market.csv")
            if r.get("name")}


def load_starts():
    """(intervals, starters graded, rounds with no lock) from the tidy tables.

    Empty everywhere until a jornada has been played AND its opener's kickoff
    was observed before it kicked off. Both conditions are reported by the
    caller rather than collapsed into a silent zero.
    """
    return start_intervals(read_csv(TIDY / "matches.csv"),
                           read_csv(TIDY / "starters.csv"),
                           read_csv(TIDY / "fixtures.csv"),
                           read_csv(TIDY / "market.csv"))


def load_predictions() -> dict[str, list[tuple[dt.datetime, dict]]]:
    """{norm name: [(when, factors)] ascending} from squad_log.csv.

    The whole logged row comes through, not just the score, because grading a
    forecast means asking WHICH factor was wrong. `fix` is empty on every row
    written before the fixture term existed; those rows still score, they just
    cannot be attributed, and the section says how many.
    """
    preds: dict[str, list[tuple[dt.datetime, dict]]] = {}
    for r in read_csv(DECISIONS / "squad_log.csv"):
        try:
            when = snapshot_stamp(r["observed_at"])
            fac = {"score": float(r["score"])}
        except (KeyError, ValueError, TypeError):
            continue
        for col in ("fix", "ppm", "flat", "start_pct"):
            try:
                fac[col] = float(r[col])
            except (KeyError, ValueError, TypeError):
                fac[col] = None
        # THE ID THE ROW WAS LOGGED WITH, and the name for the rows written
        # before the column existed. Both sides of the grade have to agree
        # about who a prediction was about, and the confirmed-starts side
        # carries the id, the slug and the name for the same reason.
        key = (r.get("ff_id") or "").strip() or norm(r.get("player", ""))
        if key and when is not None:
            preds.setdefault(key, []).append((when, fac))
    for v in preds.values():
        v.sort(key=lambda t: t[0])
    return preds


# ---------------------------------------------------------------------------
# the page
# ---------------------------------------------------------------------------

# What each tidy table is FOR, in the report's terms. The feeds themselves are
# read out of the source registry so a new one cannot be missed here; this is
# only the half a registry entry does not know, which is what the number is
# used for once it arrives.
FILLS = {
    "market": "price, value, position, fitness — every player in the game",
    "lineups": "probable XI percentages, both sources",
    "matches": "fixtures, kickoffs, results",
    "starters": "confirmed elevens, which is what P(start) is graded on",
    "fixtures": "who plays whom next, for the fixture term",
    "elo": "team strength, which ranks the fixture term",
    "points": "realised points per jornada, the actuals in every table below",
    "api_leagues": "your cash and the league's id",
    "api_market": "what is on offer, and the bids on it",
    "api_teams": "all five squads",
    "api_lineup": "the eleven you have actually fielded, and the formation "
                  "the app says you are playing",
    "api_standings": "the league table — position, points, squad value, and "
                     "your balance",
    "api_activity": "every transfer, which is what the ledger replays — one row per deal, so the newest is the last deal and not the last sweep",
    "api_players": "names for players nobody owns any more — one row per player, first sighting kept",
    "api_stats": "what the app scored each player, broken into what he did — "
                 "one row per player per week per stat, a correction being a "
                 "later row rather than an overwrite",
    "players": "the crosswalk: one key per player across all four spellings",
    "clubs": "the same, for clubs",
}

# Written by src/crosswalk.py out of the tables above rather than fetched.
DERIVED = {"players", "clubs"}

# Sources the registry cannot name statically, because the sweep discovers
# them: the API's urls carry a league id that the API itself supplies, and the
# match pages are whichever ones the calendar says have been played.
HOSTS = {"api_leagues": ("LaLiga Fantasy API", "every_run"),
         "api_market": ("LaLiga Fantasy API", "every_run"),
         "api_teams": ("LaLiga Fantasy API", "every_run"),
         "api_lineup": ("LaLiga Fantasy API", "every_run"),
         "api_standings": ("LaLiga Fantasy API", "every_run"),
         "api_activity": ("LaLiga Fantasy API", "every_run"),
         "api_players": ("LaLiga Fantasy API", "once"),
         "api_stats": ("LaLiga Fantasy API", "every_run"),
         "starters": ("futbolfantasy.com", "once"),
         # The points PAGE is fetched every run; this table only gains a row
         # when somebody actually plays, so its age is the last jornada
         # scored and not a health reading.
         "points": ("futbolfantasy.com", "as played")}

# The same distinction, derived rather than listed: a table in
# sources.STORE_ONCE keeps the FIRST sighting of each fact, so its newest row
# is the last thing that HAPPENED and not the last time the feed answered.
# Ageing it as a health reading called the activity feed "17 hours stale" the
# moment it stopped being stored once per sweep — while it was answering every
# sweep, with nothing to report but a quiet transfer window.
def _as_it_happens() -> dict[str, str]:
    from sources import STORE_ONCE

    return {t: "as dealt" for t in STORE_ONCE}

# The column that carries the reading's time, where it is not observed_at.
STAMPED = {"points": "to_stamp"}

# How long a feed may go without answering before its age is the news.
# ffcore.tidy's own bounds, not a second opinion — load_elo() and the three
# load_api_* readers refuse a reading older than these. "every_run" and
# "twice_daily" share a bound because the fetch timer's two sweeps (11h/13h
# apart) are both longer than a naive 6h cadence would assume. Why:
# docs/notes/methodology.md#feed-freshness-bounds.
FRESH = {"every_run": EVERY_RUN_FRESH_DAYS, "daily": DAILY_FRESH_DAYS,
         "twice_daily": EVERY_RUN_FRESH_DAYS, "once": 1e9, "as played": 1e9,
         "as dealt": 1e9, "derived": 1e9}


def _hosts() -> dict[str, tuple[str, str, int]]:
    """{table: (host, cadence, how many pages)} from the source registry.

    Read from the registry rather than listed here, so a source added to
    sources.py appears in the appendix without anyone remembering to say so.
    """
    from urllib.parse import urlparse

    from sources import sources

    out: dict[str, tuple[set, set, set]] = {}
    for src in sources():
        host = urlparse(src.url).netloc.removeprefix("www.")
        hosts, cadences, keys = out.get(src.table, (set(), set(), set()))
        out[src.table] = (hosts | ({host} if host else set()),
                          cadences | {src.cadence}, keys | {src.key})
    return {t: (", ".join(sorted(h)) or HOSTS.get(t, ("—",))[0],
                "/".join(sorted(c)), k)
            for t, (h, c, k) in out.items()}


def _feed_state() -> dict[str, str]:
    """{page: what the sweep has been doing lately}, or {} before the first
    sweep that logged anything.

    Consecutive failures at the TAIL, and what they cost. A source that has
    failed once is weather; one that has failed every sweep for a week is a
    decision — either its url moved or it should be dropped — and the seconds
    it burns while never answering is the number that decides which. Club Elo
    was taking eight of a sweep's eleven seconds for a page that did not come.
    """
    runs: dict[str, list[dict]] = {}
    for r in read_csv(TIDY / "feeds.csv"):
        if r.get("page"):
            runs.setdefault(r["page"], []).append(r)
    out = {}
    for page, rows in runs.items():
        bad = []
        for r in reversed(rows):
            if r.get("status") != "FAILED":
                break
            bad.append(float(r.get("seconds") or 0))
        if bad:
            out[page] = ("failed the last sweep, %.1fs" % bad[0]
                         if len(bad) == 1 else
                         "failed the last %d sweeps, %.1fs each"
                         % (len(bad), sum(bad) / len(bad)))
    return out


def _age(stamp: str, now: dt.datetime) -> tuple[float | None, str]:
    when = snapshot_stamp(stamp)
    if when is None:
        return None, "—"
    days = (now - when).total_seconds() / 86400.0
    return days, when.strftime("%d %b %H:%M")


# Which fetched page feeds which tidy table, for the pages the registry cannot
# name statically. One payload can fill several tables: the squad call carries
# the league table and every player's stat lines as well as the squads.
API_PAGES = {"match": ("starters",),
             "api_lineup": ("api_lineup",),
             "api_leagues": ("api_leagues",),
             "api_market": ("api_market",),
             "api_teams": ("api_teams", "api_standings", "api_stats"),
             "api_activity": ("api_activity",),
             "api_player": ("api_players",)}


def _fetched() -> dict[str, list[float]]:
    """{table: [hours since each of its pages was last ASKED FOR]}.

    THE ONE HONEST AGE, and it is not the one the tidy store holds. parse
    re-stamps a carried-forward document with the sweep that carried it, so
    `observed_at` says "now" for a page nobody has requested since midnight —
    by design, because that column answers "which snapshot is this row from".
    The manifest keeps the other half: `seen` is when the page was last
    actually fetched, and that is what says whether a source is still feeding
    us. On 2026-08-19 the probable-XI pages read "ok, 0 hours" in this table
    while the last request for them was 17.6 hours old.
    """
    from ingest import state

    reg = _hosts()
    of_table: dict[str, list[float]] = {}
    now = run_now()
    for page, row in state().items():
        when = snapshot_stamp(row.get("seen") or "")
        if when is None:
            continue
        hours = (now - when).total_seconds() / 3600.0
        tables = [t for t, (_h, _c, keys) in reg.items() if page in keys]
        for prefix, named in API_PAGES.items():
            if page == prefix or page.startswith(prefix + "_"):
                tables = list(named)
        for t in tables:
            of_table.setdefault(t, []).append(hours)
    return of_table


# The light. A table is green when every page behind it was fetched within its
# own cadence, amber when one of them has missed a turn, red when the readers
# have given up on it — ffcore.tidy refuses a reading past FRESH, so red here
# and a fallback downstream are the same event.
GREEN, AMBER, RED, GREY = "🟢", "🟡", "🔴", "⚪"


def _light(cadence: str, ages: list[float], refused: bool) -> tuple[str, str]:
    """(light, what it means) for one table, from when its pages were fetched."""
    if cadence == "derived":
        return GREY, "rebuilt every run from the tables above"
    if not ages:
        # A page the last sweep did not put in its manifest: it failed, or it
        # has never been asked for at all. `why` below names which, off the
        # fetch log, because a 403 and a page nobody has ever requested are
        # not the same problem.
        return RED, "**not in the last sweep**"
    bound = FRESH.get(cadence, 1e9) * 24
    newest, oldest = min(ages), max(ages)
    if refused or newest > 2 * bound:
        return RED, "**not fetched for %s — the readers have dropped it**" % (
            age_phrase(newest / 24))
    if newest > bound:
        return AMBER, "**%s since it was asked** (due every %s)" % (
            age_phrase(newest / 24), CADENCE_WORD.get(cadence, cadence))
    if oldest > bound and len(ages) > 1:
        return AMBER, "%d of %d pages last asked %s ago" % (
            sum(1 for a in ages if a > bound), len(ages),
            age_phrase(oldest / 24))
    return GREEN, "fetched %s ago" % age_phrase(newest / 24)


CADENCE_WORD = {"every_run": "sweep", "twice_daily": "6 hours",
                "daily": "day", "once": "—", "as played": "—",
                "as dealt": "—"}


def feed_lines() -> list[str]:
    """The data flow, and which parts of it have stopped answering.

    THIS IS THE TABLE THE ELO BUG NEEDED. A fetch that fails leaves the last
    good rows in the tidy store and every reader downstream treats them as
    today's — the fixture board ranked twenty clubs by a rating from before
    the jornada for two days, and nothing anywhere said so. Age is the only
    honest thing to print about an input, so it is a column.
    """
    now = run_now()
    reg, feeds = _hosts(), _feed_state()
    asked, quiet = _fetched(), stale_feeds()
    rows = []
    for name in sorted(FILLS):
        path = (SEASON / "live" / "perjornada_2026-27.csv") if name == "points" \
            else (TIDY / f"{name}.csv")
        got = read_csv(path)
        col = STAMPED.get(name, "observed_at")
        newest = max((r.get(col, "") for r in got), default="")
        days, seen = _age(newest, now)

        host, cadence, pages = reg.get(name, (None, None, set()))
        if host is None or name in HOSTS:
            host = (host or HOSTS.get(name, ("—", ""))[0])
            cadence = HOSTS.get(name, (None, cadence or "derived"))[1]
        if name in DERIVED:
            host, cadence = "src/crosswalk.py", "derived"
        cadence = _as_it_happens().get(name, cadence)

        light, state = _light(cadence, asked.get(name, []), name in quiet)
        if days is None and cadence != "derived":
            light, state = RED, "**never answered**"
        why = sorted({feeds[k] for k in pages if k in feeds})
        if why and light != GREEN:
            state += " — " + "; ".join(why)
        rows.append("| %s | %s | %s | %s | %s | %s | %s |" % (
            light, name, FILLS[name],
            host + (" ×%d" % len(pages) if len(pages) > 1 else ""),
            "{:,}".format(len(got)), seen, state))

    return ["## Where the numbers come from", "",
            "🟢 asked for within its own cadence · 🟡 it has missed a turn and "
            "what you are reading is the last answer · 🔴 the readers have "
            "dropped it and the report is on its fallback · ⚪ not fetched at "
            "all, built here from the rest.", "",
            "**Newest row** is the snapshot that carried the reading, which is "
            "not when it was fetched: a page nobody asked for is carried into "
            "the next sweep and re-stamped. The light is on the asking.", "",
            "| | Table | What it is used for | Fetched from | Rows | "
            "Newest row | Fetching |",
            "|---|---|---|---|--:|---|---|"] + rows + [""]


def elo_basis() -> str:
    """What actually ranked the opponents in today's run.

    Asked of the same two functions the scorer uses, rather than described from
    memory: this sentence was wrong about the model for as long as it was a
    sentence, and a claim in the methodology that the code does not make is the
    one kind of error this file exists to prevent.
    """
    from ffcore.fixture import elo_strength, team_strength

    # load_elo() returns nothing for two different reasons and the reader is
    # owed which: never fetched, or fetched and now too old to be about these
    # teams. The raw file separates them — it still holds the stale rows.
    rows = load_elo()
    if not rows:
        return ("summed squad value — %s, so the wallet is standing in for "
                "the pitch (see the feed table for how long)"
                % ("Club Elo has stopped answering and its last reading is "
                   "too old to rank a jornada it predates"
                   if read_csv(TIDY / "elo.csv") else
                   "Club Elo has not been scraped yet"))
    teams = list(team_strength(latest_market()))
    if elo_strength(teams, rows) is None:
        return ("summed squad value — Club Elo was scraped but did not cover "
                "every club in the market, and half a league ranked by Elo is "
                "not a ranking")
    # AGE LIVES IN THE FEEDS TABLE, not here. A fetch that fails leaves the
    # last reading in place and everything downstream carries on as though it
    # were today's — Club Elo went two days without answering and the board
    # was still ranked by it. That is a fact about a feed, so it is a column
    # in the feed table rather than a sentence in this one.
    return "**Club Elo rating**, a result-based rating with no transfer fees in it"


def latest_market() -> list[dict]:
    """The newest market snapshot. Read here only to list the league's clubs."""
    from ffcore.tidy import load_market_latest

    return load_market_latest()


def formula_lines() -> list[str]:
    """The live constants, and a pointer to where they are explained.

    THIS USED TO BE THE ESSAY. Thirty paragraphs restating the formula, the
    shrinkage, the fixture band, what the index is not, and why λ was retired
    — roughly 300 lines of string literals, and every word of it already in
    the README. Two copies of an explanation drift, and the generated one wins
    arguments it should not: it looks authoritative because a program printed
    it, while the README is where somebody actually maintains the reasoning.

    What generating it DID buy was that the numbers could not drift from the
    code. That is worth keeping and costs a table, not an essay: the constants
    below are read from the modules that use them, so a changed K or a changed
    band shows up here on the next run.
    """
    return [
        "### The model, as configured right now", "",
        "| Term | Setting | Fitted? |",
        "|---|---|---|",
        f"| Formula | `xPts/j = shrunk pts-per-match × fixture × P(start)` | "
        "— |",
        f"| Shrinkage | K = {SHRINK_K:g} matches, applied twice: last season "
        "toward the positional prior, then this season toward that | yes |",
        f"| Fixture band | ±{FIX_BAND * 100:.0f}% across the opponents by "
        "rank, not by ratio | **no, a guess** |",
        f"| Home advantage | +{HOME_EDGE * 100:.0f}% | **no, a guess** |",
        f"| Team strength | {elo_basis()} | — |",
        f"| P(start) read from | `{LINEUP_SOURCE}` | see the Brier table |",
        "| Fixture applies to | fielding only — never a buy, a sale or the "
        "line | — |",
        "| Season spread, match to match | each round resamples a real "
        "per-match score, rescaled to the player's rate | from %d observed "
        "matches |" % getattr(_fc(), "_real_n", 0),
        "| Season spread, RATE ERROR | the rate is a mean of a few matches, "
        "so each simulated season multiplies it by one draw of "
        "cv/√(matches+K) held all year — median %s across the squads | "
        "derived, not fitted |" % _rate_note(), "",
    ]



def column_guide_lines() -> list[str]:
    """The full explanation of every table column the daily report only
    footnotes in one line now.

    MOVED HERE 2026-08-22, not deleted. The daily report used to carry this
    in full every single run — a 5-row column legend under the bid table, a
    paragraph under the XI table, another under fitness, another under
    starting — unchanging wording, re-read every day whether or not anything
    below it had changed. The report now prints one line per table pointing
    here; this is the "click here for details" the pointer promises, static
    because what a column MEANS does not change run to run, only what is in
    it does.
    """
    from ffcore.second import LEGEND

    return [
        "### How to read the tables", "",
        # Ladder/league-table glossary added 2026-09-01, replacing two
        # independently-drifted copies (sim.py's ladder(), Fantasy.jsx).
        # Why: docs/notes/methodology.md#column-guide-ladder-and-league-table.
        "**The ladder** — one table, read top to bottom: a plan, not a "
        "menu. The funding is implicit — sell the SELL rows and the BUY "
        "rows are what the money reaches. `Start` is the probable-XI "
        "read, recalibrated and blended, the same figure the forecast "
        "multiplies by. `xPts/j` already has that applied. `€` is what "
        "you end up with for doing that row, funding included — a SELL "
        "row is what it raises, a BUY row is that money minus what he "
        "costs, a SAVE row is how far short you are. `Season` is "
        "simulated: extra points over the jornadas left, the same "
        "seasons with and without the move — on a KEEP or SELL row, "
        "\"without\" is his best REAL replacement, not nothing, so a "
        "negative number there can still mean keep him: his own best "
        "alternative costs more than he does, not that he scores less "
        "than zero. A `*` on a KEEP/SELL row's \"vs X\" name means the "
        "BUY list's own row for X is funded by somebody ELSE's sale, "
        "not his — two different players' money legitimately reaching "
        "the same man, not an error to reconcile. `pts/M€` is Season "
        "per million actually spent — how CHEAPLY a gain arrived, not "
        "how big it is, so read Season first: there are only eleven "
        "starting shirts, and a tiny gain at a tiny price can still "
        "carry a flattering rate. A dash means the move is net "
        "cash-neutral-or-positive (nothing to divide by) or, on a SAVE "
        "row, a shortfall nobody can act on yet. `Where` names who "
        "holds him, and on a payable clause also the rival closest to "
        "affording it — a first name and `today`/`~Nd`, an ESTIMATE "
        "off their reconstructed balance, the app's daily allowance, "
        "and how fast that manager has actually raised money this "
        "season, never a prediction he actually wants the player.", "",
        "**The league table** — `Pts` is the real league total today. "
        "`Season` is the simulated total and its 10-90 band — a mean "
        "with no band beside it reads as a prediction it is not. "
        "`P(above)` is how often the simulation has you finish above "
        "them.", "",
        "**Field these eleven** — `pts/m` is points per match, last "
        "season shrunk toward the average and blended with this season as "
        "it accrues (`~` = no record at all, the baseline is assumed). "
        "`Fix` is how much the next opponent moves it (`=` a median team, "
        "`—` no fixture known). `FF`/`AF` are separate, never blended, "
        "because neither has been checked against a played jornada yet and "
        "a disagreement is worth more than an average. `xPts/j` = pts/m × "
        "Fix × FF, and uses FF only. `⚠` on a name means the Fitness or "
        "Starting section has something on him. On a `+SLOT` row, xPts/j "
        "is the change to the WHOLE eleven if you sign him and re-pick the "
        "shape — his own score, fixture excluded, since you'd own him for "
        "months, not one round.", "",
        "**What to bid** — `Bid` is what it costs to win him, never "
        "whether he is worth winning (that is the eleven table). A "
        "purchase is closer to a loan than a spend: the value comes back "
        "on sale, give or take a tenth, so a bid within a few percent is "
        "not a decision. It is priced as the floor plus what this league "
        "has actually paid over it — the range is what has happened, not "
        "a chance of winning, and every one of them is a bid that won. "
        f"`XI` is two readings, printed side by side. {LEGEND} `Competition` "
        "is demand, not roster counts: the rivals whose XI actually "
        "improves with him, strongest threat first — `?` means their cash "
        "is unknown (treat as live), `(n broke)` means they want him but "
        "cannot pay the floor.", "",
        "**Fitness** — FF's read is from the 'Estado físico', "
        "'Sancionados' and 'No disponibles' blocks of each team page; "
        "`Tocado` — a knock the site still lists as available — is folded "
        "into doubt. No entry is an absence of evidence, not evidence of "
        "fitness. 'App' is the game's own operator-stated availability, "
        "shown only when it differs from FF's read.", "",
        "**Starting** — both figures are editorial reads refreshed a few "
        "times a day, not live probabilities. `~` means listed with no "
        f"figure (assumed {NEUTRAL_START:.0f}%), `!` means not on the page "
        f"at all (assumed {ABSENT_START:.0f}%). The under-threshold "
        "cutoff is `min_start` in `inputs/league.ini`.", "",
    ]


def _fc():
    """The forecaster this run built, or None — asked, never described."""
    try:
        import decide
        return decide.load().forecaster
    except Exception:                                    # pragma: no cover
        return None


_RATE_NOTE: list = []


def _rate_note() -> str:
    """How wide the rate uncertainty came out, in this run's own numbers.

    THE ROW HAS TO BE ABLE TO BE WRONG. Printing "cv over root n" without the
    number it produced would be a formula nobody can check against the bands
    it widened — 10-90 went from 259 points to 365 the day this went in.
    """
    if _RATE_NOTE:
        return _RATE_NOTE[0]
    import statistics
    fc = _fc()
    rel = sorted(getattr(fc, "rate_rel", {}).values())
    _RATE_NOTE.append("±%.0f%% of a rate" % (100 * statistics.median(rel))
                      if rel else "not applied — no match counts")
    return _RATE_NOTE[0]


def start_lines() -> list[str]:
    """The same gate, graded on who actually started rather than who appeared.

    Printed above the appearance table because it answers the real question. It
    stays silent until there is something to say: this is the table that fills
    in as jornadas are played, and an empty one would read as a source scoring
    zero rather than as a season that has not started.
    """
    intervals, graded, ungraded = load_starts()
    out: list[str] = []
    if not intervals:
        if ungraded:
            NOT_GRADED.append(
                "| jornada " + ", ".join(str(j) for j in ungraded)
                + " — confirmed elevens exist but no kickoff was observed "
                  "before the round locked | all |")
        return out

    numbered, named, skipped = start_grade(intervals, load_lineups(source=""),
                                           load_universe())
    if not numbered and not named:
        return out

    out += [f"| **starts** — {graded} confirmed, {len(intervals)} locked "
            "round(s) | | | | |"]
    for src, n, claim, rate, brier in numbered:
        mark = " ←read" if src == LINEUP_SOURCE else ""
        out.append(f"| {src}{mark} | {n} | {claim:.0f}% | {rate:.0f}% | "
                   f"{brier:.3f} |")
    for src, n, rate in named:
        out.append(f"| {src} — named, no number | {n} | — | {rate:.0f}% | — |")
    if skipped:
        NOT_GRADED.append(f"| within {START_EDGE:.0f} points of 50%, on "
                          f"starts | {skipped} |")
    if ungraded:
        NOT_GRADED.append(
            "| jornada " + ", ".join(str(j) for j in ungraded)
            + " — its opener kicked off before this repo saw a kickoff for "
              "it, so there is no honest cutoff | all |")
    return out


def source_lines(actuals: list[dict]) -> list[str]:
    """The gate for LINEUP_SOURCE: which site's eleven was right more often.

    ONE TABLE, TWO SAMPLES. Starts is the question — a substitute is a miss —
    and appearances is the blunter one kept because it reaches back to before
    the match pages were collected, where a twenty-minute substitute counts.
    They flatter both sources equally, so the comparison holds even though the
    level does not; printing them as one table with a Sample column is what
    stops the second being read as the answer.
    """
    NOT_GRADED.clear()
    starts = start_lines()
    head = ["### Who to believe about the eleven", "",
            "| Source | Calls | Mean claim | Hit | Brier |",
            "|---|--:|--:|--:|--:|"]
    rows = list(starts)

    intervals = appearances(actuals)
    numbered, named, skipped = ([], [], 0) if not intervals else start_grade(
        intervals, load_lineups(source=""), load_universe())
    if numbered or named:
        rows.append("| **appearances** — the wider, blunter sample; a "
                    "20-minute substitute counts | | | | |")
        for src, n, claim, rate, brier in numbered:
            mark = " ←read" if src == LINEUP_SOURCE else ""
            rows.append(f"| {src}{mark} | {n} | {claim:.0f}% | {rate:.0f}% | "
                        f"{brier:.3f} |")
        for src, n, rate in named:
            rows.append(f"| {src} — named, no number | {n} | — | {rate:.0f}% "
                        "| — |")
        if skipped:
            NOT_GRADED.append(f"| within {START_EDGE:.0f} points of 50%, on "
                              f"appearances | {skipped} |")

    if not rows:
        return head[:2] + [
            f"| `{LINEUP_SOURCE}` | 0 | — | — | — |", "",
            "_Read because it was first, not because it won anything._", ""]

    out = head + rows + [""]
    if NOT_GRADED:
        out += ["| Not graded | Calls |", "|---|--:|"] + NOT_GRADED + [""]
    out += ["_Brier: mean squared error of the probability, 0 perfect and "
            "0.25 a coin flip. Claims are scored as last published before the "
            "round's first kickoff. Lower Brier **on starts** earns "
            "`LINEUP_SOURCE` in ffcore/tidy.py; appearances break ties only._",
            ""]
    return out


def comparison_lines() -> list[str]:
    out = [f"### Forecast vs actual — last {WINDOW_DAYS} days", ""]
    actuals, label = load_actuals()
    if not actuals:
        out += ["| Player-intervals scored | 0 |", "|---|--:|",
                "| Why | no completed jornada in the window yet |", ""]
        return out

    pairs = pair(actuals, load_predictions())
    if not pairs:
        out += ["| Player-intervals scored | 0 |", "|---|--:|",
                f"| Per-jornada rows for {label} | {len(actuals)} |",
                "| Why none matched | no prediction was logged before the "
                "matches; squad_log.csv starts once report.py has run with a "
                "roster |", ""]
        return out

    n = len(pairs)
    tp = sum(p["predicted"] for p in pairs)
    ta = sum(p["actual"] for p in pairs)
    mae = sum(abs(p["err"]) / p["matches"] for p in pairs) / n
    fx, no_fix = fixture_rows(pairs)
    out += [
        "| Measure | Value |", "|---|--:|",
        f"| Player-intervals scored ({label}) | {n} |",
        f"| Predicted, total | {tp:.0f} pts |",
        f"| Actual, total | {ta:.0f} pts |",
        f"| **Mean absolute error** | **{mae:.1f} pts per player-match** |",
        f"| Pairs predating the fixture term | {no_fix} of {n} |", "",
        "_Read every xPts/j in this report as ± the error above, at least. "
        "Only predictions logged before an interval are scored, so hindsight "
        "is excluded by construction; the sample is your own squad and grows "
        "about 15 pairs a jornada._", "",
        "| Forecast bucket | n | Mean forecast | Mean actual |",
        "|---|--:|--:|--:|",
    ]
    for label_, cnt, mp, ma in bucket_rows(pairs):
        out.append(f"| {label_} | {cnt} | {mp:.1f} | {ma:.1f} |")
    out.append("")

    # Attribution: not "how wrong", but "wrong about WHAT". One factor at a
    # time, starting with the newest and least-justified one — this is the
    # table that decides whether the fixture band is kept, widened or deleted.
    if fx:
        out += [f"| Next fixture (±{FIX_BAND*100:.0f}%, unfitted) | n | Mean "
                "forecast | Mean actual | Error |",
                "|---|--:|--:|--:|--:|"]
        for label_, cnt, mp, ma, me in fx:
            out.append(f"| {label_} | {cnt} | {mp:.1f} | {ma:.1f} | "
                       f"{me:+.1f} |")
        out += ["", "_Per player-match. Positive error on **easier** together "
                "with negative on **harder** means the band is too wide; the "
                "reverse, too narrow; both near zero, about right. Judge "
                "nothing on a single-digit n._", ""]

    out += ["| Biggest miss | Forecast | Actual | Error |",
            "|---|--:|--:|--:|"]
    for p in sorted(pairs, key=lambda p: -abs(p["err"]))[:5]:
        out.append(f"| {p['name']} | {p['predicted']:.1f} | "
                   f"{p['actual']:.0f} | {p['err']:+.1f} |")
    out.append("")
    return out


def main() -> None:
    out = ["# How the forecast works — and how it's doing", ""]
    out += feed_lines()
    out += formula_lines()
    out += column_guide_lines()
    out += comparison_lines()
    out += source_lines(load_actuals()[0])
    PARTS.mkdir(parents=True, exist_ok=True)
    write_lines(PARTS / "methodology.md", out)
    print(f"wrote {PARTS / 'methodology.md'} ({len(out)} lines)")


# ---------------------------------------------------------------------------
# selftest — join logic only
# ---------------------------------------------------------------------------

def _selftest() -> None:
    # -- the light is on the asking, not on the re-stamp --------------------
    # A page carried into a sweep it was never asked for is re-stamped with
    # that sweep, so `observed_at` said "0 hours" for probable-XI pages last
    # requested seventeen hours earlier. These are the states that must be
    # distinguishable at a glance.
    assert _light("every_run", [0.5], False)[0] == GREEN
    assert _light("twice_daily", [17.6], False)[0] == AMBER      # missed 11:40
    assert _light("twice_daily", [0.2, 30.0], False)[0] == AMBER  # one page of forty
    assert "1 of 2 pages" in _light("twice_daily", [0.2, 30.0], False)[1]
    assert _light("every_run", [80.0], False)[0] == RED
    # Refused by ffcore.tidy's gate IS red, whatever the clock says: the
    # readers have already dropped it and the report is on its fallback.
    assert _light("every_run", [1.0], True)[0] == RED
    assert _light("every_run", [], False)[0] == RED
    assert _light("derived", [], False)[0] == GREY

    utc = dt.timezone.utc
    t = lambda d, h=0: dt.datetime(2026, 8, d, h, tzinfo=utc)  # noqa: E731

    def f(score, fix=None):
        return {"score": score, "fix": fix}

    preds = {"ane": [(t(10), f(2.0)), (t(14), f(3.0, 1.10)),
                     (t(16), f(9.9))],
             "bo": [(t(14), f(1.5, 0.90))]}

    # Ane played once between the 15th and the 17th: the prediction that
    # counts is the one from the 14th (3.0), not the hindsight 9.9.
    actuals = [
        {"name": "Ane", "keys": ["ane"], "from_dt": t(15),
         "points_delta": 8.0, "games_delta": 1.0},
        {"name": "Bo", "keys": ["bo"], "from_dt": t(15),
         "points_delta": 4.0, "games_delta": 2.0},   # doubled interval
        {"name": "Cai", "keys": ["cai"], "from_dt": t(15),
         "points_delta": 5.0, "games_delta": 1.0},   # never predicted
        {"name": "Didi", "keys": ["didi"], "from_dt": t(15),
         "points_delta": 1.0, "games_delta": 0.0},   # no match played
    ]
    got = pair(actuals, preds)
    assert [g["name"] for g in got] == ["Ane", "Bo"], got
    ane, bo = got
    assert ane["predicted"] == 3.0 and ane["err"] == -5.0, ane
    assert bo["predicted"] == 3.0 and bo["matches"] == 2.0, bo

    # No prediction strictly before the cutoff -> excluded.
    assert latest_before(preds["bo"], t(14)) is None
    assert latest_before(preds["bo"], t(14, 1))["score"] == 1.5

    rows = bucket_rows(got)
    assert [r[0] for r in rows] == ["under 2", "3–4"], rows

    # -- grading the fixture term ------------------------------------------
    # Ane faced an easier fixture (1.10) and beat the forecast; Bo faced a
    # harder one (0.90) and beat it too. Both land in their own bucket, with
    # the error signed forecast-minus-actual and stated PER MATCH: Bo's two
    # matches are one row, not two.
    fx, no_fix = fixture_rows(got)
    assert [r[0] for r in fx] == ["harder", "easier"], fx
    assert no_fix == 0
    hard, easy = fx
    assert hard[1] == 1 and abs(hard[4] - (1.5 - 2.0)) < 1e-9, hard
    assert easy[1] == 1 and abs(easy[4] - (3.0 - 8.0)) < 1e-9, easy

    # A row logged before the fixture term existed is counted as unattributable
    # rather than dropped or treated as neutral — the difference between "we
    # did not measure it" and "it was average".
    old = pair([{"name": "Ane", "keys": ["ane"], "from_dt": t(11),
                 "points_delta": 4.0, "games_delta": 1.0}], preds)
    fx2, no_fix2 = fixture_rows(old)
    assert fx2 == [] and no_fix2 == 1, (fx2, no_fix2)

    # -- grading the probable-XI sources ------------------------------------
    # Ane and Bo played in the interval opening on the 15th; Cai did not, and
    # says so by being absent from it — points.py emits movers only.
    played = [{"name": "Ane", "keys": ["ane"], "from_dt": t(15),
               "points_delta": 8.0, "games_delta": 1.0},
              {"name": "Bo", "keys": ["bo"], "from_dt": t(15),
               "points_delta": 4.0, "games_delta": 2.0},
              {"name": "Didi", "keys": ["didi"], "from_dt": t(15),
               "points_delta": 1.0, "games_delta": 0.0}]   # points, no match
    iv = appearances(played)
    assert [s for s, _ in iv] == [t(15)], iv
    assert iv[0][1] == {"ane", "bo"}, iv[0][1]
    # A row with points but no match is not an appearance.
    assert "didi" not in iv[0][1]

    def claim(src, name, pct, when=14, role="starter"):
        return {"source": src, "player_name": name, "start_pct": pct,
                "role": role, "observed_at": when}

    # Two sites, four players, one interval. `ff` called all three right; `af`
    # was confident about Cai, who never played.
    claims = [claim("ff", "Ane", "90"), claim("ff", "Bo", "80"),
              claim("ff", "Cai", "20", role="doubt"),
              claim("af", "Ane", "90"), claim("af", "Bo", "80"),
              claim("af", "Cai", "90"),
              claim("ff", "Ane", "10", when=16),   # hindsight: never scored
              claim("ff", "Ghost", "90"),          # not in the app at all
              claim("ff", "Eve", "55"),            # no call either way
              claim("af", "Fay", "", role="starter"),      # a named starter
              claim("af", "Gus", "", role="doubt")]        # neither
    # observed_at wants a stamp string; snapshot_stamp parses these.
    for c in claims:
        c["observed_at"] = ("2026-08-%02dT1200Z" % c["observed_at"]
                            if isinstance(c["observed_at"], int)
                            else c["observed_at"])

    universe = {"ane", "bo", "cai", "eve", "fay", "gus"}
    num, named, skipped = start_grade(iv, claims, universe)
    got = {s: (n, round(b, 3)) for s, n, _, _, b in num}
    # Three graded calls each, and af is punished for the confident miss.
    assert got["ff"][0] == 3 and got["af"][0] == 3, got
    assert got["af"][1] > got["ff"][1], got
    # Perfect confidence on two hits and a correct doubt is a good Brier.
    assert got["ff"][1] < 0.05, got
    # The hindsight claim from the 16th never enters: only calls logged before
    # the interval opened are scored.
    ff = next(r for r in num if r[0] == "ff")
    assert abs(ff[2] - (90 + 80 + 20) / 3) < 1e-9, ff
    assert abs(ff[3] - 200.0 / 3) < 1e-9, ff        # 2 of 3 appeared
    # A name the app does not price is not graded, so the more complete source
    # is not penalised for being more complete.
    assert all(n == 3 for _, n, _, _, _ in num), num
    # A call with no number is a hit rate, never dressed up as 100%.
    assert named == [("af", 1, 0.0)], named
    # The undecided middle and a listing with neither figure are skipped, and
    # the count is reported rather than silently dropped.
    assert skipped == 2, skipped
    # No interval, no record: never a zero.
    assert start_grade([], claims, universe) == ([], [], 0)

    # -- grading against who actually started ------------------------------
    matches = [{"match_id": "1", "jornada": "1", "home": "alaves",
                "away": "getafe", "score": "3-0"},
               {"match_id": "2", "jornada": "1", "home": "espanyol",
                "away": "levante", "score": "1-0"},
               {"match_id": "9", "jornada": "2", "home": "rayo-vallecano",
                "away": "alaves", "score": "2-2"}]
    # The two sites spell clubs differently and still join, and a round whose
    # kickoff was never observed gets no lock rather than an assumed one.
    fixtures = [{"kickoff": "2026-08-16T17:00:00+00:00", "home": "Espanyol",
                 "away": "Levante"},
                {"kickoff": "2026-08-15T19:30:00+00:00", "home": "Alaves",
                 "away": "Getafe"}]
    assert team_slug_of("Racing Santander", {"racing", "real-madrid"}) \
        == "racing"
    assert team_slug_of("Real Betis", {"betis", "real-sociedad"}) == "betis"
    assert team_slug_of("Nowhere FC", {"racing"}) is None
    locks = jornada_locks(matches, fixtures)
    # The round locks at its EARLIEST kickoff, not each match's own: the app
    # locks the whole lineup once, so Sunday's starter is already frozen.
    assert list(locks) == [1] and locks[1].day == 15, locks
    assert 2 not in locks                       # no kickoff observed for it

    def start(match, name, slug, role="starter", team="alaves"):
        return {"match_id": match, "player_name": name, "player_slug": slug,
                "role": role, "team_slug": team}

    xi = [start("1", "Ane", "ane-slug"), start("1", "Bo", "bo-slug"),
          start("1", "Bo", "bo-slug"),          # repeats: carried forward
          start("2", "Cai", "cai-slug", team="levante"),
          start("2", "Dee", "dee-slug", role="sub"),   # a sub is not a starter
          start("9", "Eve", "eve-slug")]        # round 2 has no lock
    iv2, graded, ungraded = start_intervals(matches, xi, fixtures)
    assert graded == 3, graded                  # Ane, Bo, Cai — Bo once
    assert ungraded == [2], ungraded            # said out loud, not dropped
    assert len(iv2) == 1 and iv2[0][0] == locks[1]
    # Both keys are carried, so a claim matches on whichever it has.
    assert iv2[0][1] == {"ane", "ane-slug", "bo", "bo-slug", "cai",
                         "cai-slug"}, iv2[0][1]
    assert "dee" not in iv2[0][1] and "eve" not in iv2[0][1]
    assert iv2[0][2] == {"alaves", "levante"}, iv2[0][2]

    # The short name a match page prints resolves to the market name, so a
    # claim carrying only the full name is graded. Ambiguity inside the squad
    # adds nothing: "Romero" stays "romero" and whoever claimed a Romero by
    # full name alone goes ungraded rather than half-credited.
    market = [{"name": "abdel abqar", "team": "Alaves"},
              {"name": "abdel abqar", "team": "Alaves"},   # a second snapshot
              {"name": "ivan romero", "team": "Alaves"},
              {"name": "rafael romero", "team": "Alaves"},
              {"name": "someone else", "team": "Barcelona"}]
    iv3, _, _ = start_intervals(
        matches, [start("1", "Abqar", "abqar-slug"),
                  start("1", "Romero", "romero-slug")], fixtures, market)
    assert "abdel abqar" in iv3[0][1], iv3[0][1]
    assert "ivan romero" not in iv3[0][1] \
        and "rafael romero" not in iv3[0][1], iv3[0][1]
    # A club nobody played keeps its players out of the squad index entirely.
    assert set(market_names(market, {"alaves"})) == {"alaves"}

    # A claim is graded on the SLUG when the name would not match. This is the
    # whole reason the outcome comes off the same site: "U. Núñez" on one page
    # and "Unai Núñez" on another are the same player, and the id says so.
    slugged = [{"source": "ff", "player_name": "Whoever They Call Him",
                "player_slug": "ane-slug", "start_pct": "90", "role": "starter",
                "team_slug": "alaves", "observed_at": "2026-08-14T1200Z"}]
    num2, _, _ = start_grade(iv2, slugged, None)
    assert num2 == [("ff", 1, 90.0, 100.0, (0.9 - 1) ** 2)], num2
    # A confident call about a club whose match we never read is not a miss —
    # it is not graded at all. Scoring it would punish the source for our gap.
    absent = dict(slugged[0], player_slug="zed-slug", team_slug="barcelona")
    assert start_grade(iv2, [absent], None) == ([], [], 0)
    # Nothing played, nothing claimed: still not a zero score for anyone.
    assert start_intervals([], [], []) == ([], 0, [])

    # -- column_guide_lines: the full explanation, moved here 2026-08-22 ---
    guide = "\n".join(column_guide_lines())
    for heading in ("Field these eleven", "What to bid", "Fitness",
                   "Starting", "The ladder", "The league table"):
        assert heading in guide, heading
    # THE CURRENT COLUMNS ACTUALLY NAMED, not just the section headings —
    # 2026-09-01: two independent copies of this text (sim.py's ladder(),
    # the site's Fantasy.jsx) had already drifted before this single copy
    # replaced both; a test that only checked the heading existed would
    # not have caught that.
    for term in ("pts/M€", "vs X", "P(above)"):
        assert term in guide, term
    # The live NEUTRAL_START/ABSENT_START numbers, not hand-typed duplicates
    # of them — this drifting from ffcore.score silently is exactly the
    # class of bug formula_lines() already guards against for the other
    # constants.
    assert f"{NEUTRAL_START:.0f}%" in guide
    assert f"{ABSENT_START:.0f}%" in guide

    print("methodology.py selftest OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        main()
