"""
methodology.py — how the forecast works, and how it is doing against reality.

Writes reports/methodology.md, which digest.py stitches in as the LAST
section of REPORT.md. Two halves:

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

    python src/methodology.py             # writes reports/methodology.md
    python src/methodology.py --selftest  # pure join logic, no IO
"""

from __future__ import annotations

import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ffcore.fixture import FIX_BAND, HOME_EDGE  # noqa: E402
from ffcore.score import SHRINK_K  # noqa: E402
from ffcore.text import norm  # noqa: E402
from ffcore.tidy import (DECISIONS, REPORTS, SEASON, read_csv,  # noqa: E402
                         snapshot_stamp, write_lines)

LIVE = SEASON / "live"
WINDOW_DAYS = 21


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
# loading
# ---------------------------------------------------------------------------

def load_actuals() -> tuple[list[dict], str]:
    """Per-jornada rows from the newest season's file, parsed and windowed."""
    files = sorted(LIVE.glob("perjornada_*.csv")) if LIVE.exists() else []
    if not files:
        return [], ""
    label = files[-1].stem.replace("perjornada_", "")
    cutoff = (dt.datetime.now(dt.timezone.utc)
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
        key = norm(r.get("player", ""))
        if key and when is not None:
            preds.setdefault(key, []).append((when, fac))
    for v in preds.values():
        v.sort(key=lambda t: t[0])
    return preds


# ---------------------------------------------------------------------------
# the page
# ---------------------------------------------------------------------------

def formula_lines() -> list[str]:
    k = f"{SHRINK_K:g}"
    return [
        "### The formula", "",
        "Every player's **xPts/j** — expected points per jornada — is:", "",
        "    xPts/j = shrunk points-per-match × fixture × P(start)", "",
        f"**Shrunk points-per-match** pulls an average toward the median for "
        f"the position: `(points + {k}×prior) / (matches + {k})`, prior = "
        f"median pts/match among players in that position with 10+ matches. "
        f"{k} matches of prior weight means a 3-game wonder is mostly prior "
        "and a 34-game regular is mostly himself.", "",
        f"It runs **twice**. Last season is shrunk toward the positional "
        f"prior, and the result becomes the prior for THIS season, shrunk the "
        f"same way with the same K={k}. So a player who has played two "
        "jornadas is still mostly last season, and one who has played twenty "
        "is mostly this one, with no switch-over date to pick and no second "
        "constant to guess. With no matches played yet it collapses exactly "
        "to last season's number.", "",
        "**Fixture** is who he plays next: teams are ranked by total squad "
        f"value and the rank is mapped onto ±{FIX_BAND*100:.0f}%, with "
        f"±{HOME_EDGE*100:.0f}% for home advantage. It is a RANK, not a "
        "ratio — Real Madrid's squad is worth 4.6× the median one, and facing "
        "them does not cost a defender four fifths of his points. **Both "
        "numbers are guesses**, not fits: nothing has been played, so there "
        "is nothing to fit them to. They are deliberately small, and the "
        "table below grades them as soon as jornadas exist.", "",
        "The fixture applies to **fielding**, which is one round. It is left "
        "OUT of the buy-side figure in question 1, because you own a player "
        "for months and next Saturday's draw is not a reason to sign him.", "",
        "**P(start)** is futbolfantasy's probable-XI percentage, read twice "
        "daily. A player listed without a percentage gets a neutral prior; "
        "one absent from the page entirely gets a low one. Promoted-side "
        "players have no top-flight record, fall back to the positional "
        "prior, and are marked **assumed**. analiticafantasy's reading is "
        "printed beside it and is **not** blended in: neither source has been "
        "checked against a played jornada, so there is no weight to blend "
        "them by.", "",
        "The **team forecast** is the sum over the best legal XI, so ≈35 "
        "means: this eleven is expected to score about 35 points in a "
        "jornada, before variance — and single-match variance is huge.", "",
        "### What it deliberately ignores (for now)", "",
        "- **Sub cameos** — P(start) multiplies the whole average, so a 30% "
        "starter is modelled as 0.3 × his points, when in reality he often "
        "plays 20 minutes and scores something. Forecasts for rotation "
        "players run low.",
        "- **Position-specific fixture sensitivity** — a clean sheet is far "
        "more opponent-driven than a striker's goal, and the fixture term "
        "treats them identically. This is the first thing to add once "
        f"±{FIX_BAND*100:.0f}% itself has been graded.",
        "- **Anything but points and minutes** — no goals, assists, cards or "
        "expected-goals data is scraped, so nothing about HOW a player scores "
        "reaches the forecast.", "",
        "Each of these is a candidate fix, but only after the comparison "
        "below shows which one actually costs points.", "",
    ]


def comparison_lines() -> list[str]:
    out = [f"### Forecast vs actual — last {WINDOW_DAYS} days", ""]
    actuals, label = load_actuals()
    if not actuals:
        out += ["_No completed jornada in the window yet. points.py has no "
                "per-jornada rows to compare against; this section fills "
                "itself in after the first matches._", ""]
        return out

    pairs = pair(actuals, load_predictions())
    if not pairs:
        out += [f"_{len(actuals)} per-jornada rows exist for {label}, but "
                "none matched a prediction logged before the matches — "
                "squad_log.csv starts recording only once report.py has "
                "run with a roster._", ""]
        return out

    n = len(pairs)
    tp = sum(p["predicted"] for p in pairs)
    ta = sum(p["actual"] for p in pairs)
    mae = sum(abs(p["err"]) / p["matches"] for p in pairs) / n
    out += [
        f"**{n} player-intervals** ({label}): predicted **{tp:.0f}** pts "
        f"total, actual **{ta:.0f}**. Mean absolute error "
        f"**{mae:.1f} pts per player-match** — read every xPts/j in this "
        "report as ± that, at least.", "",
        "Only predictions logged **before** each interval are scored; "
        "hindsight is excluded by construction. Sample is your own squad, "
        "so it grows ~15 pairs a jornada.", "",
        "| Forecast bucket | n | Mean forecast | Mean actual |",
        "|---|--:|--:|--:|",
    ]
    for label_, cnt, mp, ma in bucket_rows(pairs):
        out.append(f"| {label_} | {cnt} | {mp:.1f} | {ma:.1f} |")
    out.append("")

    # Attribution: not "how wrong", but "wrong about WHAT". One factor at a
    # time, starting with the newest and least-justified one.
    fx, no_fix = fixture_rows(pairs)
    if fx:
        out += [f"**Is the fixture term earning its place?** It moves a "
                f"forecast by up to ±{FIX_BAND*100:.0f}% and was never "
                "fitted, so this is the table that decides whether to keep "
                "it, widen it, or delete it.", "",
                "| Next fixture | n | Mean forecast | Mean actual | Error |",
                "|---|--:|--:|--:|--:|"]
        for label_, cnt, mp, ma, me in fx:
            out.append(f"| {label_} | {cnt} | {mp:.1f} | {ma:.1f} | "
                       f"{me:+.1f} |")
        out += ["", "_Per player-match. A positive error against an **easier** "
                "fixture together with a negative one against a **harder** "
                "fixture means the band is too wide; the reverse means too "
                "narrow; both near zero means it is roughly right. Judge "
                "nothing on a bucket with a single-digit n._", ""]
        if no_fix:
            out += [f"_{no_fix} of {n} pairs predate the fixture term and "
                    "carry no factor, so they are in the totals above but not "
                    "in this table._", ""]
    elif no_fix:
        out += [f"_All {no_fix} pairs predate the fixture term, so there is "
                "nothing to grade it with yet._", ""]

    worst = sorted(pairs, key=lambda p: -abs(p["err"]))[:5]
    out += ["Biggest misses (forecast − actual):", ""]
    for p in worst:
        out.append(f"- **{p['name']}** — forecast {p['predicted']:.1f}, "
                   f"actual {p['actual']:.0f} ({p['err']:+.1f})")
    out.append("")
    return out


def main() -> None:
    out = ["# How the forecast works — and how it's doing", ""]
    out += formula_lines()
    out += comparison_lines()
    write_lines(REPORTS / "methodology.md", out)
    print(f"wrote {REPORTS/'methodology.md'} ({len(out)} lines)")


# ---------------------------------------------------------------------------
# selftest — join logic only
# ---------------------------------------------------------------------------

def _selftest() -> None:
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

    print("methodology.py selftest OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        main()
