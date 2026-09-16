"""
points.py — this season's points, from the snapshots already taken.

Writes data/season/live/perjornada_<label>.csv — the diff between
consecutive KEPT snapshots (ones whose totals actually moved). A full
rebuild from raw on every run, output disposable.

Two deliberate limits:
  * No jornada numbers here beyond what match_jornadas() infers from
    matches.csv's own score-appeared timeline — there's no kickoff-date
    calendar in this repo to join against otherwise.
  * report.py does not read this folder. ffcore/score.py's Scorer blends
    this season into pts/match through the same shrinkage the prior gets
    (from data/season/points_<label>.csv); reading the raw per-jornada
    diffs here too would be a second, unshrunk path to the same number.

    python src/points.py              # rebuild data/season/live/ from raw
    python src/points.py --selftest   # pure-logic checks, no deps, no IO
"""

from __future__ import annotations

import sys


from ffcore.text import norm
from ffcore.tidy import SEASON, load_matches_history, write_csv

LIVE = SEASON / "live"

DIFF_FIELDS = ["from_stamp", "to_stamp", "season", "ff_id", "player_name",
               "player_name_full", "team", "points_delta", "games_delta",
               "points_total", "games_total", "jornada"]

# What the site puts in the table body when the season it is showing has no
# played matches. Matched as text on the raw page, because the parser cannot
# tell "nobody has scored yet" from "the columns moved" — both give it 0 rows.
EMPTY_MARKS = ("no se encontraron resultados", "sin resultados")


# ---------------------------------------------------------------------------
# pure logic — selftested below, no parsing, no IO
# ---------------------------------------------------------------------------

def empty_season(html: str) -> bool:
    """True when the page says it has no results (a fresh season rollover),
    as opposed to a parser that broke and lost them.
    """
    low = (html or "").lower()
    return any(m in low for m in EMPTY_MARKS)


def match_jornadas(matches_history: list[dict]) -> list[tuple[str, int]]:
    """[(when this match's result was first seen, jornada)], oldest first.

    `matches_history` must be the FULL table, not latest_only() — the first
    snapshot where a match's score is non-empty is this repo's own record
    of when that match finished. One entry per match, first-seen-scored
    only, so a later re-scrape can't push its jornada later.
    """
    first_scored: dict[str, tuple[str, int]] = {}
    for r in sorted(matches_history, key=lambda r: r.get("observed_at", "")):
        mid = (r.get("match_id") or "").strip()
        score = (r.get("score") or "").strip()
        if not mid or not score or mid in first_scored:
            continue
        try:
            jor = int(r.get("jornada"))
        except (TypeError, ValueError):
            continue
        first_scored[mid] = (r.get("observed_at", ""), jor)
    return sorted(first_scored.values())


def jornada_asof(timeline: list[tuple[str, int]], stamp: str) -> int | None:
    """The jornada of the most recently confirmed-scored match at or before
    `stamp`, or None if nothing had finished yet. `timeline` is
    `match_jornadas()`'s own output — already oldest first.
    """
    best = None
    for when, jor in timeline:
        if when <= stamp:
            best = jor
        else:
            break
    return best


def player_key(r: dict) -> str:
    """The row's own ff_id first (a name that changes spelling between two
    sweeps must not read as one player leaving and another arriving);
    normalised name as the fallback for older snapshots with no id.
    """
    return ((r.get("ff_id") or "").strip()
            or norm(r.get("player_name_full") or r.get("player_name") or ""))


def totals(rows: list[dict]) -> dict[str, tuple[float, float]]:
    """{key: (points, games)} — the comparable core of one snapshot."""
    out = {}
    for r in rows:
        key = player_key(r)
        if key:
            out[key] = (float(r["points"]), float(r["games"]))
    return out


def keep_changed(seq: list[tuple[str, list[dict]]]) -> list[tuple[str, list[dict]]]:
    """Drop snapshots whose totals are identical to the previous kept one.

    `seq` is [(stamp, rows)] in time order. The first is always kept.
    """
    kept, prev = [], None
    for stamp, rows in seq:
        cur = totals(rows)
        if cur != prev:
            kept.append((stamp, rows))
            prev = cur
    return kept


def diff(prev_rows: list[dict], cur_rows: list[dict],
         from_stamp: str, to_stamp: str, season: str,
         jornada_timeline: list[tuple[str, int]] = ()) -> list[dict]:
    """Per-player deltas between two kept snapshots — only players whose
    points or games moved; absent-before diffs against (0, 0).

    `jornada_timeline` (match_jornadas()'s output) stamps each row via
    jornada_asof(timeline, to_stamp); blank when no timeline is given or
    nothing had finished yet, never a guess.
    """
    prev = totals(prev_rows)
    jor = jornada_asof(jornada_timeline, to_stamp)
    out = []
    for r in cur_rows:
        key = player_key(r)
        if not key:
            continue
        pts, pj = float(r["points"]), float(r["games"])
        p0, j0 = prev.get(key, (0.0, 0.0))
        if pts == p0 and pj == j0:
            continue
        out.append({
            "from_stamp": from_stamp, "to_stamp": to_stamp, "season": season,
            "ff_id": (r.get("ff_id") or "").strip(),
            "player_name": r.get("player_name", ""),
            "player_name_full": r.get("player_name_full", ""),
            "team": r.get("team", ""),
            "points_delta": f"{pts - p0:g}",
            "games_delta": f"{pj - j0:g}",
            "points_total": f"{pts:g}",
            "games_total": f"{pj:g}",
            "jornada": "" if jor is None else str(jor),
        })
    return out


# ---------------------------------------------------------------------------
# rebuild from raw
# ---------------------------------------------------------------------------

_CACHE = "parsed_points.json"


def load_snapshots() -> dict[str, list[tuple[str, list[dict]]]]:
    """{season label: [(stamp, rows)]} from every snapshot, in order.

    ingest.pages and sources.parse_points are imported lazily so --selftest
    needs neither lxml nor a raw store.
    """
    from ingest import (_parse_cache, _save_parse_cache, _Sigs, parser_sig,
                        doc_keys, documents)
    from sources import parse_points, season_label

    by_label: dict[str, list[tuple[str, list[dict]]]] = {}
    # Cached per document (raw archives are immutable), fingerprinted on
    # the parser too — a parse is a function of page AND parser.
    _psig = parser_sig("parse_points")
    cache, fresh, walk = _parse_cache(_CACHE), {}, doc_keys()

    # Read first, parse second, in one pass per archive — see ingest.documents.
    need: dict[str, set] = {}
    for _stamp, docs in walk:
        if "points" in docs and not isinstance(
                cache.get("%s@%s" % (docs["points"][0], _psig)), dict):
            need.setdefault(docs["points"][1], set()).add("points")
    for origin, _key, html in documents(need):
        try:
            rows = parse_points(html)
            got = {"rows": rows, "label": season_label(html),
                   "empty": bool(empty_season(html))}
        except Exception as e:
            # One bad page must not lose the rest of the run.
            print(f"  warn: {origin}/points: {type(e).__name__}: {e}")
            got = {"rows": [], "label": "", "empty": True}
        cache["%s@%s" % (_Sigs().of("points", html), _psig)] = got

    for stamp, docs in walk:
        if "points" not in docs:
            continue
        ck, origin = docs["points"]
        ck = "%s@%s" % (ck, _psig)
        got = cache.get(ck)
        if not isinstance(got, dict):
            continue
        fresh[ck] = got
        if not got["rows"]:
            print(f"  note: {stamp}/points has no rows yet — the season has "
                  "not started." if got["empty"] else
                  f"  warn: {stamp}/points parsed to 0 rows — markup "
                  "changed? Raw is kept; fix parse and re-run.")
            continue
        by_label.setdefault(got["label"], []).append((stamp, got["rows"]))
    _save_parse_cache(fresh, _CACHE)
    return by_label


def main() -> None:
    by_label = load_snapshots()
    if not by_label:
        sys.exit("no points page found in any snapshot under data/raw/ — "
                 "run ingest.py fetch first")

    # Full history, not latest_only() — see match_jornadas()'s docstring.
    timeline = match_jornadas(load_matches_history())

    for label, seq in sorted(by_label.items()):
        kept = keep_changed(seq)

        deltas = []
        for (s0, r0), (s1, r1) in zip(kept, kept[1:]):
            deltas.append(diff(r0, r1, s0, s1, label, timeline))

        LIVE.mkdir(parents=True, exist_ok=True)
        flat = [row for d in deltas for row in d]
        write_csv(LIVE / f"perjornada_{label}.csv", flat, DIFF_FIELDS)

        moved = sum(1 for d in deltas if d)
        print(f"{label}: {len(seq)} snapshots -> {len(kept)} kept, "
              f"{moved} interval(s) with movement, "
              f"{len(flat)} per-jornada rows")

    print(f"wrote {LIVE}/ — report.py does not read this folder, on purpose.")


# ---------------------------------------------------------------------------
# selftest — pure logic only
# ---------------------------------------------------------------------------

def _selftest() -> None:
    def row(full, pts, pj, short=None, team="X"):
        return {"player_name": short or full, "player_name_full": full,
                "team": team, "points": str(pts), "games": str(pj),
                "avg": ""}

    a = [row("Ane Aldea", 0, 0), row("Bo Bidal", 0, 0)]
    b = [row("Ane Aldea", 0, 0), row("Bo Bidal", 0, 0)]      # identical
    c = [row("Ane Aldea", 8, 1), row("Bo Bidal", 0, 0)]      # Ane played
    d = [row("Ane Aldea", 8, 1), row("Bo Bidal", 3, 1),      # Bo played,
         row("Cai Coro", 5, 1)]                               # Cai appeared

    kept = keep_changed([("t0", a), ("t1", b), ("t2", c), ("t3", d)])
    assert [s for s, _ in kept] == ["t0", "t2", "t3"], kept

    d1 = diff(a, c, "t0", "t2", "s")
    assert len(d1) == 1 and d1[0]["player_name_full"] == "Ane Aldea"
    assert d1[0]["points_delta"] == "8" and d1[0]["games_delta"] == "1"

    d2 = diff(c, d, "t2", "t3", "s")
    got = {r["player_name_full"]: r["points_delta"] for r in d2}
    assert got == {"Bo Bidal": "3", "Cai Coro": "5"}, got   # Ane unchanged

    # A mid-season first appearance diffs against zero, not an error.
    assert next(r for r in d2 if r["player_name_full"] == "Cai Coro"
                )["games_delta"] == "1"

    # An empty season and a broken parser both yield 0 rows; only one of them
    # is a problem, and the page says which.
    assert empty_season("<tbody><tr><td>No se encontraron resultados</td>")
    assert empty_season("<TD>NO SE ENCONTRARON RESULTADOS</TD>")   # case-blind
    assert not empty_season("<tbody><tr><td>Ane Aldea</td>")
    assert not empty_season("")

    # -- jornada, stamped from when a match's score was first seen ---------
    history = [
        # Two snapshots of the SAME match: unscored, then scored. Only the
        # FIRST scored sighting counts.
        {"observed_at": "t0", "match_id": "1", "jornada": "1", "score": ""},
        {"observed_at": "t1", "match_id": "1", "jornada": "1", "score": "2-0"},
        {"observed_at": "t2", "match_id": "1", "jornada": "1", "score": "2-0"},
        {"observed_at": "t3", "match_id": "2", "jornada": "2", "score": "1-1"},
        # A row with no jornada (unparseable) is skipped, not a crash.
        {"observed_at": "t1b", "match_id": "3", "jornada": "", "score": "0-0"},
    ]
    tl = match_jornadas(history)
    assert tl == [("t1", 1), ("t3", 2)], tl
    assert jornada_asof(tl, "t0") is None       # nothing finished yet
    assert jornada_asof(tl, "t1") == 1
    assert jornada_asof(tl, "t2") == 1           # between J1 and J2 finishing
    assert jornada_asof(tl, "t3") == 2
    assert jornada_asof(tl, "t9") == 2           # still the latest known

    d3 = diff(a, c, "t0", "t2", "s", jornada_timeline=tl)
    assert d3[0]["jornada"] == "1", d3
    # No timeline at all: blank, never a guess.
    assert diff(a, c, "t0", "t2", "s")[0]["jornada"] == ""

    print("points.py selftest OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        main()
