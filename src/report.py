"""
report.py — daily decision report, written as markdown into the repo.

Renders in the GitHub mobile app, so it's readable anywhere with no hosting.
Run after ff_ingest.py parse.

    python report.py

Structure, top to bottom: what needs a decision today, your team as one table
(XI then bench, with the cost of each swap), and your own squad's price moves.
League-wide movers and recruitment live in reports/watchlist.md, which knows
who owns whom; repeating them here was noise.

SCORING, while no jornada of this season has been played:

    score = shrunk points-per-match  x  P(start)

Points-per-match comes from data/season/points_*.csv (run src/history.py once
a season). A raw average is untrustworthy on few appearances, so it is pulled
toward the median for that position:

    shrunk = (total_points + K * prior) / (matches + K)

with K = 8 matches. P(start) is the probable-XI reading.

The score is a RANKING INDEX, not a points forecast. Three things it cannot
know, all of them flagged in the report rather than hidden:

  * Promoted-side players have no LaLiga record at all, so they fall back to
    the positional prior — the median top-flight starter, which flatters them.
    Their rows say "assumed", and the prior is discounted.
  * A player absent from the probable-XI page is not the same as one listed
    with no percentage. The first gets a low default; the second, a neutral one.
  * Nothing here has been checked against reality yet. Every snapshot appends
    the whole squad — XI and bench, with the inputs that produced each score —
    to data/decisions/squad_log.csv, so that once jornadas exist you can ask
    what the ranking cost you. None of it is reconstructable afterwards, which
    is why it is logged now and scored later.
"""

from __future__ import annotations

import csv
import os
import re
import statistics
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(os.environ.get("FF_ROOT", "./data"))
TIDY = ROOT / "tidy"
SEASON = ROOT / "season"
DECISIONS = ROOT / "decisions"
REPORTS = Path("reports")
HISTORY = REPORTS / "history"

# Spanish positions -> XI slots. entrenador is excluded: separate slot in the
# app, and premium anyway.
SLOT = {
    "portero": "POR",
    "defensa": "DEF",
    "mediocampista": "MED",
    "centrocampista": "MED",
    "delantero": "DEL",
}
SLOT_LABEL = {"POR": "portero", "DEF": "defensa", "MED": "mediocampista",
              "DEL": "delantero"}
SLOT_MIN = {"POR": 1, "DEF": 3, "MED": 3, "DEL": 1}
# Most that can ever be on the pitch — anyone deeper than this in his position
# can never start under any legal formation.
MAX_SLOT = {"POR": 1, "DEF": 5, "MED": 5, "DEL": 3}
# Below this you cannot absorb a single injury without a scramble.
THIN = {"POR": 2, "DEF": 4, "MED": 4, "DEL": 2}

# Confirmed against the app's formation picker.
FREE_FORMATIONS = [(5, 4, 1), (5, 3, 2), (4, 5, 1), (4, 4, 2), (4, 3, 3),
                   (3, 5, 2), (3, 4, 3)]
# Premium subscription: these shapes, the captain boost and the coach slot.
PREMIUM_FORMATIONS = [(5, 2, 3), (4, 6, 0), (4, 2, 4), (3, 6, 1), (3, 3, 4)]
PREMIUM = False
FORMATIONS = FREE_FORMATIONS + (PREMIUM_FORMATIONS if PREMIUM else [])

SHRINK_K = 8.0            # matches of prior weight
NEUTRAL_START = 60.0      # listed on the XI page but no percentage given
ABSENT_START = 15.0       # not on the XI page at all — not in the picture
DOUBT_FACTOR = 0.5
PROMOTED_DISCOUNT = 0.70  # the LaLiga median overstates a promoted squad
STALE_HOURS = 14.0
MOVER_PCT = 1.0           # squad price moves worth printing

# Name particles that stay lowercase when title-casing a folded name.
# "le"/"el"/"la" are deliberately absent: Le Normand and El Hilali are far
# more common in this league than "de la Fuente" losing its lowercase.
PARTICLES = {"de", "del", "van", "von", "der", "den", "di", "da", "dos",
             "do", "y", "bin", "ibn", "ter"}


def input_path(name: str) -> Path:
    """Prefers inputs/<name>; falls back to the repo root."""
    p = Path("inputs") / name
    return p if p.exists() else Path(name)


def fold(s) -> str:
    """Lowercase, strip accents — so 'Iñigo' matches 'inigo'."""
    return "".join(
        c for c in unicodedata.normalize("NFD", (s or "").lower())
        if unicodedata.category(c) != "Mn"
    )


def title_name(s: str) -> str:
    """market.csv hands us folded names; make them readable again.

    Accents survive (they're in the source string), particles stay lowercase,
    hyphenated parts are capitalised on both sides.
    """
    s = (s or "").strip()
    if not s or s != s.lower():
        return s  # already cased — leave it alone
    words = []
    for i, w in enumerate(s.split()):
        if i and w in PARTICLES:
            words.append(w)
        else:
            words.append("-".join(p[:1].upper() + p[1:] for p in w.split("-")))
    return " ".join(words)


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


def parse_stamp(s: str):
    """Tolerant: 2026-08-11T2325Z, 2026-08-11T23:25Z, 2026-08-11 both parse."""
    digits = re.sub(r"\D", "", s or "")
    if len(digits) < 8:
        return None
    try:
        y, mo, d = int(digits[:4]), int(digits[4:6]), int(digits[6:8])
        h = int(digits[8:10]) if len(digits) >= 10 else 0
        mi = int(digits[10:12]) if len(digits) >= 12 else 0
        return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)
    except ValueError:
        return None


def madrid_tz():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("Europe/Madrid")
    except Exception:
        return timezone(timedelta(hours=2))  # CEST, good enough Mar-Oct


def load_squad() -> list[str]:
    path = input_path("squad.txt")
    if not path.exists():
        return []
    return [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.strip().startswith("#")]


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


def load_cash() -> tuple[float | None, datetime | None]:
    """inputs/cash.txt: a balance, optionally with the date you checked it.

        12500000  2026-08-11
    """
    path = input_path("cash.txt")
    if not path.exists():
        return None, None
    text = path.read_text(encoding="utf-8")
    body = "\n".join(ln.split("#")[0] for ln in text.splitlines())
    money = re.search(r"-?\d[\d.,]*\d|-?\d", body)
    when = re.search(r"\d{4}-\d{2}-\d{2}", body)
    amount = None
    if money:
        raw = money.group(0).strip().replace(" ", "")
        # 12.500.000 and 12,500,000 both mean the same thing here.
        raw = raw.replace(".", "").replace(",", "") if raw.count(".") > 1 \
            or raw.count(",") > 1 else raw.replace(",", "")
        amount = num(raw, None) if raw else None
    return amount, (parse_stamp(when.group(0)) if when else None)


def last_transaction() -> datetime | None:
    """Newest date found anywhere in inputs/transactions.csv."""
    rows = read_csv(input_path("transactions.csv"))
    best = None
    for r in rows:
        for v in r.values():
            m = re.search(r"\d{4}-\d{2}-\d{2}", str(v or ""))
            if m:
                d = parse_stamp(m.group(0))
                if d and (best is None or d > best):
                    best = d
    return best


def load_deadline() -> datetime | None:
    """inputs/deadline.txt: next lock, e.g. 2026-08-15T18:30 (Madrid time)."""
    path = input_path("deadline.txt")
    if not path.exists():
        return None
    body = "\n".join(ln.split("#")[0] for ln in
                     path.read_text(encoding="utf-8").splitlines())
    m = re.search(r"\d{4}-\d{2}-\d{2}[T ]?\d{0,2}:?\d{0,2}", body)
    if not m:
        return None
    d = parse_stamp(m.group(0))
    return d.replace(tzinfo=madrid_tz()).astimezone(timezone.utc) if d else None


def pick_xi(pool: dict, force: dict | None = None):
    """Best legal XI by total score. force pins one player into his slot.

    Exact, not heuristic: the only coupling between players is the per-slot
    count, so top-N per slot within each legal shape is optimal.
    """
    best = None
    for d, m, f in FORMATIONS:
        need = {"POR": 1, "DEF": d, "MED": m, "DEL": f}
        if force is not None:
            slot = force["slot"]
            if not slot or need.get(slot, 0) < 1:
                continue
        picked, ok = [], True
        for k, n in need.items():
            avail = pool.get(k, [])
            if force is not None and force["slot"] == k:
                rest = [p for p in avail if p is not force][:n - 1]
                take = [force] + rest
            else:
                take = avail[:n]
            if len(take) < n:
                ok = False
                break
            picked += take
        if not ok:
            continue
        tot = sum(p["score"] for p in picked)
        if best is None or tot > best[0]:
            best = (tot, (d, m, f), picked)
    return best


def log_squad(observed, players, chosen, formation, total, deadline,
              obs_dt) -> None:
    """Append-only record of every recommendation, for scoring later.

    One row per player per snapshot — long format, so a scorer can group by
    snapshot without parsing packed strings. hours_to_lock is stored rather
    than an at-lock flag, because a run cannot know whether a later snapshot
    will still beat the deadline: the scorer picks, per jornada, the row with
    the smallest non-negative value.

    The bench is logged too. Without it "what did the ranking cost me" is
    unanswerable after the fact, and that is the whole point of keeping this.
    """
    DECISIONS.mkdir(parents=True, exist_ok=True)
    path = DECISIONS / "squad_log.csv"
    seen = {r.get("observed_at") for r in read_csv(path)}
    if observed in seen:
        return
    htl = ""
    if deadline and obs_dt:
        htl = f"{(deadline - obs_dt).total_seconds() / 3600:.1f}"
    cols = ["observed_at", "hours_to_lock", "formation", "index_total",
            "player", "pos", "slot", "start_pct", "start_source", "status",
            "assumed", "value", "score", "picked"]
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        if new:
            w.writeheader()
        for p_ in players:
            if p_["pct"] is not None:
                src = "read"
            elif p_["on_page"]:
                src = "listed_blank"
            else:
                src = "absent"
            w.writerow({
                "observed_at": observed, "hours_to_lock": htl,
                "formation": "-".join(str(x) for x in formation),
                "index_total": f"{total:.2f}",
                "player": p_["name"], "pos": p_["pos"], "slot": p_["slot"],
                "start_pct": "" if p_["pct"] is None else f"{p_['pct']:.0f}",
                "start_source": src, "status": p_["status"] or "ok",
                "assumed": int(bool(p_["assumed"])),
                "value": f"{p_['value']:.0f}", "score": f"{p_['score']:.3f}",
                "picked": int(id(p_) in chosen),
            })


def main() -> None:
    market = latest_only(read_csv(TIDY / "market.csv"))
    xi_rows = latest_only(read_csv(TIDY / "probable_xi.csv"))

    REPORTS.mkdir(exist_ok=True)
    HISTORY.mkdir(parents=True, exist_ok=True)

    if not market:
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
    listed: set[str] = set()
    status: dict[str, str] = {}
    for r in xi_rows:
        # Names are the only key both files share — neither page exposes
        # player links, so slugs are unavailable on the team pages.
        key = fold(r.get("player_name")) or r.get("player_slug")
        if not key:
            continue
        listed.add(key)
        p = num(r.get("start_pct"), -1)
        if p >= 0:
            start_pct[key] = max(start_pct.get(key, 0), p)
        if r.get("status") and r["status"] != "ok":
            status[key] = r["status"]

    hist, hist_label = load_history()

    # Promoted sides have no top-flight record at all, so a team with players
    # but essentially no history is promoted. Detected, not hardcoded, so it
    # keeps working next season.
    per_team: dict[str, list[int]] = {}
    for r in market:
        team = r.get("team") or "?"
        h = hist.get(fold(r.get("name", "")))
        tally = per_team.setdefault(team, [0, 0])
        tally[0] += 1
        tally[1] += 1 if h and h["pj"] > 0 else 0
    promoted = {t for t, (n, k) in per_team.items() if n >= 10 and k / n < 0.15}

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
        slot = SLOT.get((rec.get("position") or "").lower(), "")
        prior = priors.get(slot, global_prior)
        h = hist.get(fold(rec.get("name", "")))
        if h and h["pj"] > 0:
            return (h["pts"] + SHRINK_K * prior) / (h["pj"] + SHRINK_K), \
                f"{h['pts']:.0f}p/{h['pj']:.0f}j"
        if (rec.get("team") or "") in promoted:
            return prior * PROMOTED_DISCOUNT, "**assumed**"
        return prior, "**assumed**"

    observed = market[0]["observed_at"]
    obs_dt = parse_stamp(observed)
    now = datetime.now(timezone.utc)
    age_h = (now - obs_dt).total_seconds() / 3600 if obs_dt else None

    # --- build squad records ---------------------------------------------
    squad = load_squad()
    players: list[dict] = []
    missing: list[str] = []
    squad_value = 0.0
    for s in squad:
        r = lookup.get(s) or lookup.get(fold(s))
        if not r:
            missing.append(s)
            continue
        key = fold(r["name"])
        st = status.get(key, "")
        pct = start_pct.get(key)
        on_page = key in listed
        base, why = rate(r)
        if pct is None:
            pct_used = NEUTRAL_START if on_page else ABSENT_START
        else:
            pct_used = pct
        score = base * pct_used / 100.0
        if st == "injured":
            score = 0.0
        elif st == "doubt":
            score *= DOUBT_FACTOR
        squad_value += num(r.get("value"))
        players.append({
            "name": title_name(r["name"]), "team": r.get("team", "?"),
            "slot": SLOT.get((r.get("position") or "").lower(), ""),
            "pos": (r.get("position") or "").lower(),
            "pct": pct, "on_page": on_page, "status": st,
            "assumed": why.startswith("**"), "why": why, "score": score,
            "value": num(r.get("value")), "delta_1d": num(r.get("delta_1d")),
            "delta_pct": num(r.get("delta_pct_1d")),
        })

    pool: dict[str, list[dict]] = {}
    for p in players:
        if p["slot"]:
            pool.setdefault(p["slot"], []).append(p)
    for v in pool.values():
        v.sort(key=lambda p: p["score"], reverse=True)

    best = pick_xi(pool) if players else None
    chosen = {id(p) for p in best[2]} if best else set()

    # --- header -----------------------------------------------------------
    out: list[str] = [f"# Fantasy report — {observed}", ""]

    alerts: list[str] = []
    if age_h is not None and age_h > STALE_HOURS:
        alerts.append(f"**Data is {age_h:.0f}h old** — the ingest workflow may "
                      "have failed. Everything below is that snapshot.")
    deadline = load_deadline()
    if deadline:
        left = (deadline - now).total_seconds() / 3600
        if left < 0:
            alerts.append("**Deadline passed** — update `inputs/deadline.txt`.")
        elif left < 48:
            alerts.append(f"**Locks in {left:.0f}h.** Probable XIs are at their "
                          "most reliable now.")
        else:
            alerts.append(f"Locks in {left/24:.0f} days — readings will still "
                          "move, so this is provisional.")

    if best:
        for k, n in THIN.items():
            have = len(pool.get(k, []))
            if have < n:
                alerts.append(f"**Only {have} {SLOT_LABEL[k]}"
                              f"{'s' if have != 1 else ''}** — one knock and "
                              "you can't field a legal XI.")
        hurt = [p["name"] for p in best[2] if p["status"]]
        if hurt:
            alerts.append("**Flagged in the XI:** " + ", ".join(hurt) + ".")
        guessed = [p["name"] for p in best[2] if p["assumed"]]
        if guessed:
            alerts.append(f"**{len(guessed)} of the XI are unmodelled** "
                          f"({', '.join(guessed)}) — no LaLiga record, so "
                          "they're carrying an assumed baseline, not an "
                          "earned one.")
    if missing:
        alerts.append("**Not found in the market:** "
                      + ", ".join(f"`{m}`" for m in missing) + ".")

    if alerts:
        out += ["## Needs a decision", ""] + [f"- {a}" for a in alerts] + [""]

    cash, cash_at = load_cash()
    last_tx = last_transaction()
    money = f"**Squad {eur(squad_value)}**"
    if cash is not None:
        money += f" · cash {eur(cash)} · total {eur(squad_value + cash)}"
        if cash_at and last_tx and last_tx > cash_at:
            money += (f" — cash last checked {cash_at:%d %b}, but the ledger "
                      f"has a move on {last_tx:%d %b}. Re-check it.")
        elif not cash_at:
            money += " — undated; add the date to `inputs/cash.txt`."
    else:
        money += " — add `inputs/cash.txt` to see cash alongside it."
    out += [money, "", "Compare squad value with the app; a mismatch means a "
                       "name matched the wrong player.", ""]

    # --- team -------------------------------------------------------------
    out += ["## Team", ""]
    if not players:
        out += ["_Empty — add names to `squad.txt`, one per line._", ""]
    elif best is None:
        short = [SLOT_LABEL[k] for k, n in SLOT_MIN.items()
                 if len(pool.get(k, [])) < n]
        out += ["_No legal XI from this squad — short of: "
                + (", ".join(short) or "?") + "._", ""]
    else:
        tot, (d, m, f), picked = best
        out += [f"**{d}-{m}-{f}** · index {tot:.1f} "
                "(a ranking number, not a points forecast)", "",
                "| | Player | Start% | Value | 24h | Score | Last season |",
                "|---|---|--:|--:|--:|--:|---|"]
        for slot in ("POR", "DEF", "MED", "DEL"):
            for p in [x for x in picked if x["slot"] == slot]:
                mark = {"injured": " **INJ**",
                        "doubt": " **?**"}.get(p["status"], "")
                pct_s = (f"{p['pct']:.0f}%" if p["pct"] is not None
                         else ("~" if p["on_page"] else "!") +
                         f"{NEUTRAL_START if p['on_page'] else ABSENT_START:.0f}%")
                out.append(f"| {slot} | {p['name']}{mark} | {pct_s} | "
                           f"{eur(p['value'])} | {eur(p['delta_1d'])} | "
                           f"{p['score']:.1f} | {p['why']} |")
        out.append("")

        bench = [p for p in players if id(p) not in chosen]
        out += ["**Bench** — gap is what the XI index loses by playing him "
                "instead, after re-picking the formation. €/pt is his value "
                "per point of score: the sell shortlist, worst first.", ""]
        if not bench:
            out += ["_Nobody spare._", ""]
        else:
            rank = {id(p): i for v in pool.values() for i, p in enumerate(v)}
            rows = []
            for p in bench:
                forced = pick_xi(pool, force=p) if p["slot"] else None
                gap = (forced[0] - tot) if forced else None
                cpp = p["value"] / p["score"] if p["score"] > 0.05 \
                    else float("inf")
                if p["status"] == "injured":
                    why = "injured"
                elif p["slot"] and rank.get(id(p), 0) >= MAX_SLOT[p["slot"]]:
                    nth = rank[id(p)] + 1
                    sfx = ("th" if nth % 100 in (11, 12, 13)
                           else {1: "st", 2: "nd", 3: "rd"}.get(nth % 10, "th"))
                    why = (f"{nth}{sfx} {p['slot']} — only "
                           f"{MAX_SLOT[p['slot']]} can ever play")
                elif p["delta_pct"] >= MOVER_PCT:
                    why = "rising — sell into strength"
                elif p["pos"] == "entrenador":
                    why = "coach slot"
                elif gap is not None and gap > -0.15:
                    why = "as good as the man ahead"
                else:
                    why = "outscored"
                rows.append((cpp, p, gap, why))
            rows.sort(key=lambda t: t[0], reverse=True)
            out += ["| Player | Pos | Value | Score | Gap | €/pt | Why |",
                    "|---|---|--:|--:|--:|--:|---|"]
            for cpp, p, gap, why in rows:
                out.append(
                    f"| {p['name']} | {p['pos'][:3]} | {eur(p['value'])} | "
                    f"{p['score']:.1f} | "
                    f"{'—' if gap is None else format(gap, '+.1f')} | "
                    f"{'—' if cpp == float('inf') else eur(cpp)} | {why} |")
            out += ["", "_A sale lands above or below value depending on who "
                        "bids, and whether anyone can bid at all is per-league "
                        "state you can only see in the app._", ""]

        log_squad(observed, players, chosen, (d, m, f), tot, deadline,
                  obs_dt)

    # --- your movers ------------------------------------------------------
    movers = sorted((p for p in players if abs(p["delta_pct"]) >= MOVER_PCT),
                    key=lambda p: p["delta_pct"], reverse=True)
    out += [f"## Your movers (24h, over {MOVER_PCT:.0f}%)", ""]
    if not movers:
        out += ["_Nothing in your squad moved much._", ""]
    else:
        out += ["| Player | Value | 24h | % |", "|---|--:|--:|--:|"]
        for p in movers:
            out.append(f"| {p['name']} | {eur(p['value'])} | "
                       f"{eur(p['delta_1d'])} | {p['delta_pct']:+.2f}% |")
        out.append("")

    out += [
        "---",
        "",
        f"_{len(market)} players tracked, {len(start_pct)} with a probable-XI "
        "reading. Who to buy is in `reports/watchlist.md`, which filters out "
        "players your rivals already own._",
        "",
        f"_Score = shrunk pts/match (K={SHRINK_K:.0f}"
        + (f", {hist_label}" if hist_label else "")
        + ") × P(start). Recommended XIs are logged to "
          "`data/decisions/xi_log.csv` for scoring against reality later._",
        "",
        f"_Generated {now:%Y-%m-%d %H:%M} UTC._",
    ]

    text = "\n".join(out) + "\n"
    (REPORTS / "latest.md").write_text(text, encoding="utf-8")
    (HISTORY / f"{observed[:10]}.md").write_text(text, encoding="utf-8")
    print(f"wrote reports/latest.md ({len(text)} chars)")


if __name__ == "__main__":
    main()
