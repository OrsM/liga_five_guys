"""
report.py — daily decision report, written as markdown into the repo.

Renders in the GitHub mobile app, so it's readable anywhere with no hosting.
Run after ff_ingest.py parse.

    python report.py

Sections: your squad, the recommended XI and bench, and market momentum.
Recruitment lives in reports/watchlist.md, which knows who owns whom.

reports/latest.md is the file to scroll; the dated copy goes in
reports/history/ so the folder stays short.

SCORING, while no jornada of this season has been played:

    score = shrunk points-per-match  x  P(start)

Points-per-match comes from data/season/points_*.csv (run src/history.py once
a season). A raw average is untrustworthy on few appearances, so it is pulled
toward the median for that position:

    shrunk = (total_points + K * prior) / (matches + K)

with K = 8 matches. A player with one 10-point cameo lands near the prior; a
36-match regular keeps almost all of his own average. P(start) is the
probable-XI reading, so a 7-a-match player at 20% ranks below a 4-a-match
nailed starter — which is the whole point of the bench question.

This is a proxy, not a points model. Last season's average says nothing about
a new signing, a promoted side or a changed role, and nothing at all about
anyone who didn't play. Replace it with real expected points once a few
jornadas exist.
"""

from __future__ import annotations

import csv
import os
import statistics
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(os.environ.get("FF_ROOT", "./data"))
TIDY = ROOT / "tidy"
SEASON = ROOT / "season"
REPORTS = Path("reports")
HISTORY = REPORTS / "history"
SQUAD_FILE = None  # resolved at runtime by input_path()

# Spanish positions -> XI slots. entrenador is deliberately excluded: it is a
# separate slot in the app and does not compete for one of the eleven.
SLOT = {
    "portero": "POR",
    "defensa": "DEF",
    "mediocampista": "MED",
    "centrocampista": "MED",
    "delantero": "DEL",
}
SLOT_LABEL = {"POR": "Portero", "DEF": "Defensa", "MED": "Mediocampista",
              "DEL": "Delantero"}
SLOT_MIN = {"POR": 1, "DEF": 3, "MED": 3, "DEL": 1}

# Legal shapes, confirmed against the app's formation picker. The free ones
# are exactly: 3-5 defenders, 3-5 midfielders, 1-3 forwards, ten outfielders.
FREE_FORMATIONS = [(5, 4, 1), (5, 3, 2), (4, 5, 1), (4, 4, 2), (4, 3, 3),
                   (3, 5, 2), (3, 4, 3)]
# Behind the premium subscription — every one breaks the bounds above. The
# captain boost and the coach slot are premium as well, so PREMIUM gates all
# three. Flip it to True if you ever subscribe.
PREMIUM_FORMATIONS = [(5, 2, 3), (4, 6, 0), (4, 2, 4), (3, 6, 1), (3, 3, 4)]
PREMIUM = False
FORMATIONS = FREE_FORMATIONS + (PREMIUM_FORMATIONS if PREMIUM else [])

SHRINK_K = 8.0          # matches of prior weight
DEFAULT_START = 60.0    # assumed start% when no probable-XI reading exists
DOUBT_FACTOR = 0.5      # a flagged doubt is half a player


def input_path(name: str) -> Path:
    """Locate an editable input file. Prefers inputs/<name>; falls back to the
    repo root so a half-finished move doesn't break the run."""
    p = Path("inputs") / name
    return p if p.exists() else Path(name)


def fold(s) -> str:
    """Lowercase, strip accents — so 'Iñigo' matches 'inigo'."""
    return "".join(
        c for c in unicodedata.normalize("NFD", (s or "").lower())
        if unicodedata.category(c) != "Mn"
    )


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def latest_only(rows: list[dict]) -> list[dict]:
    if not rows:
        return []
    newest = max(r["observed_at"] for r in rows)
    return [r for r in rows if r["observed_at"] == newest]


def num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def eur(v) -> str:
    v = num(v)
    if abs(v) >= 1_000_000:
        return f"{v/1_000_000:.2f}M"
    if abs(v) >= 1_000:
        return f"{v/1_000:.0f}K"
    return f"{v:.0f}"


def load_squad() -> list[str]:
    path = input_path("squad.txt")
    if not path.exists():
        return []
    return [
        ln.strip() for ln in path.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]


def load_history() -> tuple[dict, str]:
    """{folded name: {'pts','pj'}} from the newest data/season/points_*.csv."""
    files = sorted(SEASON.glob("points_*.csv")) if SEASON.exists() else []
    if not files:
        return {}, ""
    path = files[-1]
    out: dict[str, dict] = {}
    for r in read_csv(path):
        rec = {"pts": num(r.get("points")), "pj": num(r.get("games"))}
        for key in (r.get("player_name"), r.get("player_name_full")):
            if key:
                out.setdefault(fold(key), rec)
    return out, path.stem.replace("points_", "")


def main() -> None:
    market = latest_only(read_csv(TIDY / "market.csv"))
    xi = latest_only(read_csv(TIDY / "probable_xi.csv"))

    REPORTS.mkdir(exist_ok=True)
    HISTORY.mkdir(parents=True, exist_ok=True)

    if not market:
        # Write a report saying so rather than crashing — a missing file is
        # indistinguishable from a broken workflow otherwise.
        (REPORTS / "latest.md").write_text(
            "# Fantasy report\n\nNo market data found in "
            f"`{TIDY/'market.csv'}`. Did `ff_ingest.py parse` run?\n",
            encoding="utf-8")
        print("no market data; wrote placeholder report")
        return

    lookup = {}
    for r in market:
        if r.get("slug"):
            lookup[r["slug"]] = r
        if r.get("name"):
            lookup[fold(r["name"])] = r

    start_pct: dict[str, float] = {}
    status: dict[str, str] = {}
    for r in xi:
        # Names are the only key both files share — neither page exposes
        # player links, so slugs are unavailable on the team pages.
        key = fold(r.get("player_name")) or r.get("player_slug")
        if not key:
            continue
        p = num(r.get("start_pct"), -1)
        if p >= 0:
            start_pct[key] = max(start_pct.get(key, 0), p)
        if r.get("status") and r["status"] != "ok":
            status[key] = r["status"]

    hist, hist_label = load_history()

    # Positional priors: median of last season's per-match average among
    # players with a real sample, bucketed by the position market.csv gives.
    samples: dict[str, list[float]] = {}
    for r in market:
        h = hist.get(fold(r.get("name", "")))
        slot = SLOT.get((r.get("position") or "").lower())
        if h and slot and h["pj"] >= 10:
            samples.setdefault(slot, []).append(h["pts"] / h["pj"])
    priors = {k: statistics.median(v) for k, v in samples.items() if v}
    flat = [p for v in samples.values() for p in v]
    global_prior = statistics.median(flat) if flat else 0.0

    def rate(rec) -> tuple[float, str]:
        """Shrunk points-per-match, plus a note about where it came from."""
        slot = SLOT.get((rec.get("position") or "").lower(), "")
        prior = priors.get(slot, global_prior)
        h = hist.get(fold(rec.get("name", "")))
        if not h or h["pj"] <= 0:
            return prior, "no history"
        shrunk = (h["pts"] + SHRINK_K * prior) / (h["pj"] + SHRINK_K)
        return shrunk, f"{h['pts']:.0f}p / {h['pj']:.0f}j"

    observed = market[0]["observed_at"]
    out: list[str] = [
        f"# Fantasy report — {observed}",
        "",
        f"_{len(market)} players, {len(start_pct)} with a probable-XI reading._",
        "",
    ]

    # --- squad ------------------------------------------------------------
    squad = load_squad()
    players: list[dict] = []
    out += ["## Your squad", ""]
    if not squad:
        out += ["_Empty — add names to `squad.txt`, one per line._", ""]
    else:
        total = 0.0
        out.append("| Player | Team | Value | 24h | Start% | |")
        out.append("|---|---|--:|--:|--:|---|")
        for s in squad:
            r = lookup.get(s) or lookup.get(fold(s))
            if not r:
                out.append(f"| `{s}` | ? | — | — | — | **not found** |")
                continue
            total += num(r["value"])
            key = fold(r["name"])
            st = status.get(key, "")
            flag = {"injured": "INJ", "doubt": "?"}.get(st, "")
            pct = start_pct.get(key)
            pct_s = f"{pct:.0f}%" if pct is not None else "—"
            out.append(
                f"| {r['name']} | {r['team']} | {eur(r['value'])} | "
                f"{eur(r['delta_1d'])} | {pct_s} | {flag} |"
            )

            base, why = rate(r)
            p_start = (pct if pct is not None else DEFAULT_START) / 100.0
            score = base * p_start
            if st == "injured":
                score = 0.0
            elif st == "doubt":
                score *= DOUBT_FACTOR
            players.append({
                "name": r["name"], "team": r["team"],
                "slot": SLOT.get((r.get("position") or "").lower(), ""),
                "pos": (r.get("position") or "").lower(),
                "pct": pct, "assumed": pct is None, "status": st,
                "base": base, "why": why, "score": score,
            })
        out += ["", f"**Squad value: {eur(total)}** — compare with the app's "
                    "team value; a mismatch means a name matched the wrong "
                    "player.", ""]

    # --- lineup -----------------------------------------------------------
    out += ["## Lineup", ""]
    if not players:
        out += ["_Nothing to pick from._", ""]
    else:
        if not hist:
            out += ["_No `data/season/points_*.csv` yet — run the **history** "
                    "workflow. Until then every player carries the same "
                    "baseline and only start% separates them._", ""]
        pool: dict[str, list[dict]] = {}
        for p in players:
            if p["slot"]:
                pool.setdefault(p["slot"], []).append(p)
        for v in pool.values():
            v.sort(key=lambda p: p["score"], reverse=True)

        best = None
        for d, m, f in FORMATIONS:
            need = {"POR": 1, "DEF": d, "MED": m, "DEL": f}
            if any(len(pool.get(k, [])) < n for k, n in need.items()):
                continue
            picked = [p for k, n in need.items() for p in pool[k][:n]]
            tot = sum(p["score"] for p in picked)
            if best is None or tot > best[0]:
                best = (tot, (d, m, f), picked)
        if best is None:
            short = [SLOT_LABEL[k] for k, n in SLOT_MIN.items()
                     if len(pool.get(k, [])) < n]
            out += ["_No legal XI from this squad — short of: "
                    + (", ".join(short) or "?") + "._", ""]
        else:
            tot, (d, m, f), picked = best
            chosen = {id(p) for p in picked}
            out += [f"**{d}-{m}-{f}** — projected {tot:.1f} pts", "",
                    "| | Player | Start% | Score | Last season |",
                    "|---|---|--:|--:|---|"]
            for slot in ("POR", "DEF", "MED", "DEL"):
                for p in [x for x in picked if x["slot"] == slot]:
                    pct_s = (f"~{DEFAULT_START:.0f}%" if p["assumed"]
                             else f"{p['pct']:.0f}%")
                    # A thin squad can force a flagged player in — say so.
                    mark = {"injured": " **INJ**",
                            "doubt": " **?**"}.get(p["status"], "")
                    out.append(f"| {slot} | {p['name']}{mark} | {pct_s} | "
                               f"{p['score']:.1f} | {p['why']} |")
            out.append("")

            # Captain and the coach slot are premium too (crowned in the app),
            # so they stay quiet unless PREMIUM is on. The captain boost is
            # multiplicative, which makes the best captain simply the
            # highest-scoring man in the XI — true whatever the multiplier is.
            if PREMIUM:
                cap = max(picked, key=lambda x: x["score"])
                out += [f"**Captain: {cap['name']}** ({cap['score']:.1f}) — "
                        "highest projected score in the XI.", ""]
                coaches = [p for p in players if p["pos"] == "entrenador"]
                if coaches:
                    out += ["**Coach slot:** "
                            + ", ".join(c["name"] for c in coaches)
                            + " — separate slot, not one of the eleven.", ""]

            bench = [p for p in players if id(p) not in chosen]
            out += ["**Bench**", ""]
            if not bench:
                out += ["_Nobody spare._", ""]
            else:
                out += ["| Player | Pos | Score | Why |", "|---|---|--:|---|"]
                for p in sorted(bench, key=lambda x: x["score"], reverse=True):
                    if p["status"] == "injured":
                        reason = "injured"
                    elif p["status"] == "doubt":
                        reason = "doubt — halved"
                    elif not p["slot"]:
                        reason = p["pos"] or "no position"
                    elif p["assumed"]:
                        reason = "no XI reading"
                    else:
                        reason = "outscored"
                    out.append(f"| {p['name']} | {p['pos'][:3]} | "
                               f"{p['score']:.1f} | {reason} |")
                out.append("")
            out += [f"_Score = shrunk pts/match (K={SHRINK_K:.0f}"
                    + (f", from {hist_label}" if hist_label else "")
                    + ") × P(start). A proxy until real jornadas exist — "
                      "sanity-check it against your own read._", ""]

    # --- momentum ---------------------------------------------------------
    movers = [r for r in market if num(r.get("delta_1d")) != 0]
    movers.sort(key=lambda r: num(r["delta_1d"]), reverse=True)

    def mover_table(rows, title):
        out.append(f"## {title}")
        out.append("")
        if not rows:
            out.extend(["_No movement recorded._", ""])
            return
        out.append("| Player | Team | Value | 24h | % |")
        out.append("|---|---|--:|--:|--:|")
        for r in rows:
            out.append(
                f"| {r['name']} | {r['team']} | {eur(r['value'])} | "
                f"{eur(r['delta_1d'])} | {num(r['delta_pct_1d']):+.2f}% |"
            )
        out.append("")

    mover_table(movers[:12], "Rising fastest (24h)")
    mover_table(list(reversed(movers[-12:])), "Falling fastest (24h)")

    out += [
        "---",
        "",
        "Recruitment targets live in `reports/watchlist.md` — it filters out "
        "players your rivals already own.",
        "",
        f"_Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC._",
    ]

    text = "\n".join(out) + "\n"
    (REPORTS / "latest.md").write_text(text, encoding="utf-8")
    (HISTORY / f"{observed[:10]}.md").write_text(text, encoding="utf-8")
    print(f"wrote reports/latest.md ({len(text)} chars)")


if __name__ == "__main__":
    main()
