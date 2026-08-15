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

from ffcore.score import SHRINK_K  # noqa: E402
from ffcore.text import norm  # noqa: E402
from ffcore.tidy import (DECISIONS, REPORTS, SEASON, read_csv,  # noqa: E402
                         snapshot_stamp, write_lines)

LIVE = SEASON / "live"
WINDOW_DAYS = 21


# ---------------------------------------------------------------------------
# pure join logic — selftested below
# ---------------------------------------------------------------------------

def latest_before(preds: list[tuple[dt.datetime, float]],
                  cutoff: dt.datetime) -> float | None:
    """The last predicted score logged strictly before `cutoff`, or None.

    `preds` must be sorted by timestamp ascending.
    """
    best = None
    for when, score in preds:
        if when < cutoff:
            best = score
        else:
            break
    return best


def pair(actuals: list[dict],
         preds: dict[str, list[tuple[dt.datetime, float]]]) -> list[dict]:
    """Join realised per-jornada rows with the prediction that preceded them.

    actuals: parsed perjornada rows with keys `key`, `keys` (all name forms),
    `from_dt`, `points_delta`, `games_delta`. preds: {norm name: [(dt, score)]
    sorted ascending}. Returns one dict per matched pair.
    """
    out = []
    for a in actuals:
        if a["games_delta"] < 1:
            continue
        score = None
        for k in a["keys"]:
            hits = preds.get(k)
            if hits:
                score = latest_before(hits, a["from_dt"])
            if score is not None:
                break
        if score is None:
            continue
        predicted = score * a["games_delta"]
        out.append({
            "name": a["name"],
            "predicted": predicted,
            "actual": a["points_delta"],
            "per_match": score,
            "matches": a["games_delta"],
            "err": predicted - a["points_delta"],
        })
    return out


BUCKETS = [(-1e9, 2, "under 2"), (2, 3, "2–3"), (3, 4, "3–4"), (4, 1e9, "4+")]


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


def load_predictions() -> dict[str, list[tuple[dt.datetime, float]]]:
    """{norm name: [(when, score)] ascending} from squad_log.csv."""
    preds: dict[str, list[tuple[dt.datetime, float]]] = {}
    for r in read_csv(DECISIONS / "squad_log.csv"):
        try:
            when = snapshot_stamp(r["observed_at"])
            score = float(r["score"])
        except (KeyError, ValueError, TypeError):
            continue
        key = norm(r.get("player", ""))
        if key and when is not None:
            preds.setdefault(key, []).append((when, score))
    for v in preds.values():
        v.sort()
    return preds


# ---------------------------------------------------------------------------
# the page
# ---------------------------------------------------------------------------

def formula_lines() -> list[str]:
    k = f"{SHRINK_K:g}"
    return [
        "### The formula", "",
        "Every player's **xPts/j** — expected points per jornada — is:", "",
        "    xPts/j = shrunk points-per-match × P(start)", "",
        f"**Shrunk points-per-match** pulls a player's last-season average "
        f"toward the median for his position: `(points + {k}×prior) / "
        f"(matches + {k})`, prior = median pts/match among players in that "
        f"position with 10+ matches. {k} matches of prior weight means a "
        "3-game wonder is mostly prior and a 34-game regular is mostly "
        "himself.", "",
        "**P(start)** is futbolfantasy's probable-XI percentage, read twice "
        "daily. A player listed without a percentage gets a neutral prior; "
        "one absent from the page entirely gets a low one. Promoted-side "
        "players have no top-flight record, fall back to the positional "
        "prior, and are marked **assumed**.", "",
        "The **team forecast** is the sum over the best legal XI, so ≈35 "
        "means: this eleven is expected to score about 35 points in a "
        "jornada, before variance — and single-match variance is huge.", "",
        "### What it deliberately ignores (for now)", "",
        "- **Fixtures** — no opponent-strength or home/away adjustment.",
        "- **Sub cameos** — P(start) multiplies the whole average, so a 30% "
        "starter is modelled as 0.3 × his points, when in reality he often "
        "plays 20 minutes and scores something. Forecasts for rotation "
        "players run low.",
        "- **This season** — the baseline is last season until the live "
        "points blend is turned on deliberately; a two-jornada sample "
        "should not drive an XI.", "",
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

    preds = {"ane": [(t(10), 2.0), (t(14), 3.0), (t(16), 9.9)],
             "bo": [(t(14), 1.5)]}

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
    assert latest_before(preds["bo"], t(14, 1)) == 1.5

    rows = bucket_rows(got)
    assert [r[0] for r in rows] == ["under 2", "3–4"], rows

    print("methodology.py selftest OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        main()
