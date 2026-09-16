"""
report.py — the squad's own bookkeeping: warnings, the recommendation log,
and the notification surface. Run after ingest.py parse.

    python src/report.py

  * WARNINGS — a stale feed, a thin position, an unmodelled player, a
    crosswalk clash, an unrecorded cash balance. Written to
    `.runtime/warnings.json`, which sim.py folds into decisions.json —
    the only place these facts are produced.
  * ALERTS — the same warnings, filtered to what's worth interrupting
    someone for, plus a login nudge when the league token is expiring.
  * squad_log.csv — one row per player per snapshot, to grade the
    model's own pick later.

Scoring lives in ffcore/score.py, shared with sim.py through one session
(ffcore/model.py) — warnings and the ladder read off the same squad.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ffcore.bid import deals, low_priced_buys  # noqa: E402
from ffcore.render import title_name  # noqa: E402
from ffcore.score import SLOT_LABEL, SLOT_MIN, squad_pool  # noqa: E402
from ffcore.tidy import (run_now,  # noqa: E402
                         ALERTS, DECISIONS,  # noqa: E402
                         age_phrase, append_csv, load_crosswalk,
                         load_deadline, read_csv,
                         snapshot_stamp, stale_feeds, widen_csv, write_lines)

# ALERTS: in .runtime/ (gitignored): a signal for a notifier, not a
# document — under reports/ or data/ the run would commit a "you have a
# Buy" note that stops being true within the hour.
# The same warnings as data, for the renderers that are not markdown.
WARNINGS = Path(os.environ.get("LFG_WARNINGS", ".runtime/warnings.json"))

STALE_HOURS = 14.0

# data/decisions/squad_log.csv columns, in order — one list, so the migration
# and the write cannot disagree about what the file holds.
#
# The last six are separate columns rather than folded into `score`, so a
# forecast error can be attributed to one factor (start, opponent, player)
# at a time. Rows written before they existed keep an empty cell ("not
# measured", not "average"). `ff_id` is the join key for later grading —
# `player` is display-only and can't be relied on to stay unique/stable.
LOG_COLS = ["observed_at", "hours_to_lock", "formation", "index_total",
            "ff_id", "player", "pos", "slot", "start_pct", "start_source", "status",
            "assumed", "value", "score", "picked",
            "ppm", "fix", "opp", "home", "cur_pj", "flat",
            "fix_basis", "elo_gap", "score_h3"]

# THE SHORT-HORIZON FIGURE. `score` is jornada+1 with a real fixture factor;
# the next 2 rounds have no fixture drawn yet at log time, so they use
# `ppm * pct_rest` (Scored's own "rest of season" rate/start blend) with no
# fixture adjustment — an honest approximation, not a second simulation.
# Graded 3 jornadas later against real cumulative points over that window.
# Why: docs/notes/report.md#score_h3--the-short-horizon-figure
def _score_h3(p: dict) -> float:
    rest_rate = p["ppm"] * (p["pct_rest"] / 100.0)
    return p["score"] + 2 * rest_rate


def squad_names(lg) -> tuple[list[str], str]:
    """Your roster, and where it came from. No fallback: a League that fails
    to load returns nothing rather than serve a stale squad silently."""
    if lg is not None:
        mine = lg.managers.get(lg.cfg.me)
        if mine and mine.players:
            # Keys, not names — the market keys on the site's own id, and a
            # name round-tripped back from a key no longer resolves at all.
            return (list(mine.players), "ledger")
    return [], "nothing"


def log_squad(observed, players, chosen, formation, total, deadline,
              obs_dt) -> None:
    """Append-only record of every recommendation, for scoring later.

    One row per player per snapshot (bench included), long format, so a
    scorer can group by snapshot without parsing packed strings.
    hours_to_lock is stored rather than an at-lock flag: a run can't know
    whether a later snapshot will still beat the deadline, so the scorer
    picks, per jornada, the row with the smallest non-negative value.
    """
    path = DECISIONS / "squad_log.csv"
    # Before the dedup check: a no-op run still has to carry the migration,
    # or new columns only appear on whichever run next sees a fresh snapshot.
    widen_csv(path, LOG_COLS)
    if observed in {r.get("observed_at") for r in read_csv(path)}:
        return
    htl = ""
    if deadline and obs_dt:
        htl = f"{(deadline - obs_dt).total_seconds() / 3600:.1f}"
    rows = []
    for p in players:
        src = ("read" if p["pct"] is not None
               else "listed_blank" if p["on_page"] else "absent")
        rows.append({
            "observed_at": observed, "hours_to_lock": htl,
            "formation": "-".join(str(x) for x in formation),
            "index_total": f"{total:.2f}",
            "ff_id": p.get("key", ""),
            "player": p["name"], "pos": p["pos"], "slot": p["slot"],
            "start_pct": "" if p["pct"] is None else f"{p['pct']:.0f}",
            "start_source": src, "status": p["status"] or "ok",
            "assumed": int(bool(p["assumed"])),
            "value": f"{p['value']:.0f}", "score": f"{p['score']:.3f}",
            "picked": int(id(p) in chosen),
            "ppm": f"{p['ppm']:.3f}", "fix": f"{p['fix']:.3f}",
            "opp": p["opp"], "home": int(bool(p["home"])) if p["opp"] else "",
            "cur_pj": f"{p['cur_pj']:.0f}", "flat": f"{p['flat']:.3f}",
            # Which scale ranked his opponent, and by how much. Empty rather
            # than zero when there was no Elo: a missing rating is not a
            # level match-up.
            "fix_basis": p.get("fix_basis") or "none",
            "elo_gap": ("" if p.get("elo_gap") is None
                        else f"{p['elo_gap']:.1f}"),
            "score_h3": f"{_score_h3(p):.3f}",
        })
    append_csv(path, rows, LOG_COLS)




def stale_feed_warnings(quiet=None) -> list[str]:
    """The app's feed has gone quiet — said once, or [].

    ffcore.tidy hands every reader [] once the league's API stops answering,
    which downstream reads as "nobody owns anybody, nothing is for sale"
    rather than "stale data" — this is the sentence that belongs next to the
    numbers it explains.
    """
    quiet = stale_feeds() if quiet is None else quiet
    if not quiet:
        return []
    return ["**The app's own feed is %s stale** (%s) — squads, prices and "
            "balances below are the ledger's estimate, not the app's reading. "
            "The token may have expired: `python -m ffcore.auth --login`."
            % (age_phrase(max(quiet.values())), ", ".join(sorted(quiet)))]


def alerts(warnings, token_days) -> list[str]:
    """What is worth interrupting somebody for about the squad, or [].

    Half the notification surface; sim.py writes the other half (a move) and
    puts it above this (the shortage that is context for it). Deliberately
    narrow — must return [] rather than a "no news" line when there is
    nothing to say, since the caller sends nothing on an empty list.
    """
    out = ["**Squad** — %s" % w for w in warnings]
    if token_days is not None and token_days < 14:
        out.append("**Log in again** — the league token expires in %d days "
                   "(`python -m ffcore.auth --login`)" % token_days)
    return out


def main() -> None:
    # One model per run — see ffcore/model.py — so this and decide.load()
    # can't build two Scorers off different rows and disagree silently.
    from ffcore.model import session
    m = session()
    market = m.market

    if not market:
        print("no market data; nothing to warn about")
        return

    # No try/except: a League that won't load should stop the run, not fall
    # back to a stale generated squad that looks fine and is not.
    lg = m.lg
    sc = m.sc

    observed = market[0]["observed_at"]
    obs_dt = snapshot_stamp(observed)
    now = run_now()
    age_h = (now - obs_dt).total_seconds() / 3600 if obs_dt else None

    # --- build squad records ---------------------------------------------
    squad, _squad_src = squad_names(lg)
    scored, missing = sc.score_squad(squad)
    players = []
    for s in scored:
        row = s.as_row()
        row["name"] = title_name(row["name"])
        players.append(row)

    # The WHOLE market, not just this squad — squad_log.csv is the only
    # record of what the model predicted for a player, and every fit that
    # grades our own past predictions (DRIFT_FRAC, current_mae(), the
    # start-probability Brier table) can only ever see players who show up
    # here. `sc` is the one shared Scorer for this whole run (same session
    # decide.py's candidate scan reads), so this is not a second pricing
    # pass — every player is already priced for the BUY/RAID scan; this
    # just keeps a record of it instead of throwing it away.
    # Why: docs/notes/report.md#market_wide_log--every-player-gets-a-forecast-on-record
    all_keys = sorted({r["ff_id"] for r in market if r.get("ff_id")})
    market_scored, _market_missing = sc.score_squad(all_keys)
    squad_keys = {p["key"] for p in players}
    market_players = []
    for s in market_scored:
        row = s.as_row()
        if row["key"] in squad_keys:
            continue                          # already logged via `players`
        row["name"] = title_name(row["name"])
        market_players.append(row)

    xw = load_crosswalk()
    pool = squad_pool(players)

    # THE ONE "current best eleven" ANSWER — decide.current_xi(), not this
    # module's own search. decide.load() is memoized per process (see its
    # own docstring), so this is the same Universe sim.py builds later in
    # the same run, not a second one: report.py and sim.py used to pick
    # the XI from two independently-prepared value functions (this
    # module's single-match Scored.score vs. decide's fitted Bootstrap
    # forecast) that could name a different eleven for the same squad.
    # Why: docs/notes/decide.md#current_xi--one-computation-seven-old-copies
    import decide
    xi = decide.load().current_xi[1] if players else set()
    if players and xi:
        chosen = [p for p in players if p.get("key") in xi]
        formation = tuple(sum(1 for p in chosen if p["slot"] == s)
                          for s in ("DEF", "MED", "DEL"))
        best = (sum(p["score"] for p in chosen), formation, chosen)
    else:
        best = None

    cash = lg[lg.cfg.me].cash if lg and lg.cfg.me in lg.managers else None
    dl = deals(lg, lg.market) if lg and lg.market else []

    # Still read here, because log_squad records it against every snapshot: a
    # forecast is graded by how long before the lock it was made.
    deadline, _dl_src = load_deadline(with_source=True)
    if players and best:
        log_squad(observed, players + market_players,
                  {id(p) for p in best[2]}, best[1],
                  best[0], deadline, obs_dt)

    # --- the warnings themselves -------------------------------------------
    warnings: list[str] = stale_feed_warnings()
    if age_h is not None and age_h > STALE_HOURS:
        warnings.append(f"**Data is {age_h:.0f}h old** — the ingest workflow "
                        "may have failed. Everything above is that snapshot.")
    if best:
        # A shortage is a fact about the squad (can't field a legal eleven
        # if one gets hurt), not a decision rule — sim.py prices the squad
        # you'd hold instead of consulting a threshold.
        for k, n in SLOT_MIN.items():
            have = len(pool.get(k, []))
            if have <= n:
                warnings.append(f"**Only {have} {SLOT_LABEL[k]}"
                                f"{'s' if have != 1 else ''}** — one knock and "
                                "you can't field a legal XI.")
        guessed = [p["name"] for p in best[2] if p["assumed"]]
        if guessed:
            warnings.append(f"**{len(guessed)} unmodelled** "
                            f"({', '.join(guessed)}) — no LaLiga record, so "
                            "they carry an assumed baseline, not an earned "
                            "one.")
    if missing:
        warnings.append("**Not found in the market:** "
                        + ", ".join(f"`{m}`" for m in missing)
                        + ".")
    below_floor = low_priced_buys(dl)
    if below_floor:
        warnings.append(
            "**%d purchase%s priced below the floor** (%s) — check by hand: "
            "either a mis-join, or a discounted relist after an instant "
            "sale (unverified either way)." % (
                len(below_floor), "" if len(below_floor) == 1 else "s",
                ", ".join("%s %+.1f%%" % (d["player"], d["premium"])
                          for d in below_floor)))
    clashes = xw.clashes() if xw else {}
    if clashes:
        # An identifier two players claim identifies neither — crosswalk.py
        # refuses it rather than guessing, silently costing a join everywhere
        # that id was the only bridge.
        warnings.append(
            "**Crosswalk identifier clash:** %s — an id two players claim, "
            "refused rather than guessed at. Run `python src/crosswalk.py` "
            "to see which players." % (
                "; ".join("%s: %s" % (k, ", ".join(v))
                          for k, v in clashes.items())))
    if cash and cash.value is not None and cash.confidence != "known":
        warnings.append("Cash is an estimate — record an observed balance in "
                        "`inputs/cash.txt`.")
    elif cash and cash.value is not None:
        last_tx = max((t.get("date") or "" for t in lg.txns), default="")
        anchor = re.search(r"\d{4}-\d{2}-\d{2}", cash.basis or "")
        if anchor and last_tx and last_tx[:10] > anchor.group(0):
            warnings.append(f"Balance last checked {anchor.group(0)}, but the "
                            f"ledger moved on {last_tx[:10]}. Re-check it.")
    elif not cash or cash.value is None:
        warnings.append("No cash figure — add `inputs/cash.txt`.")

    # --- the notification surface -----------------------------------------
    # Deleted (not left empty) when there's nothing to say, so a notifier can
    # test for the file's existence rather than parse it.
    try:
        from ffcore.auth import TokenStore
        token_days = TokenStore().expiry_days()
    except Exception:                                       # noqa: BLE001
        token_days = None
    # Handed over as data; sim.py folds it into decisions.json a moment later.
    WARNINGS.parent.mkdir(parents=True, exist_ok=True)
    WARNINGS.write_text(json.dumps(warnings, ensure_ascii=False),
                        encoding="utf-8")

    lines = alerts(warnings, token_days)
    if lines:
        write_lines(ALERTS, [f"# Alerts — {now:%Y-%m-%d %H:%M} UTC", ""]
                    + [f"- {ln}" for ln in lines])
    else:
        Path(ALERTS).unlink(missing_ok=True)
        print("no alerts")


def _selftest() -> None:
    """The cells that carry a judgement. main() needs a repo to run against;
    these do not, so they are the part CI can hold still."""
    # -- a feed that has gone quiet is a warning, not a silence -------------
    w = stale_feed_warnings({"api_teams": 3.1, "api_market": 3.1})
    assert len(w) == 1 and "3 days" in w[0], w
    assert "api_teams" in w[0] and "api_market" in w[0], w
    assert stale_feed_warnings({}) == []

    # --- names ---
    # A particle stays lowercase inside a name, but not as its first word.
    # title_name itself is tested in ffcore/render.py, where it lives now.
    assert title_name("nico van gaal") == "Nico van Gaal"

    # -- alerts: the short list worth interrupting somebody for -------------
    body = "\n".join(alerts(["Only 1 delantero"], token_days=None))
    assert "delantero" in body, body
    # Nothing to say means NO alert, not an empty one. A notification that
    # says "no news" every twelve hours is the same spam by another route.
    assert alerts([], None) == []
    # The login expiring is the one piece of plumbing worth a nudge, because
    # the failure is silent and the fix needs a human at a browser.
    assert any("Log in again" in ln for ln in alerts([], token_days=9))
    assert alerts([], token_days=60) == []

    # -- _score_h3: the short-horizon figure ---------------------------------
    nailed = {"score": 5.0, "ppm": 4.0, "pct_rest": 100.0}
    assert _score_h3(nailed) == 5.0 + 2 * 4.0, _score_h3(nailed)
    # A bench player (pct_rest low) contributes almost nothing to the two
    # unfixtured rounds, same as he would in the real simulation.
    bench = {"score": 0.5, "ppm": 4.0, "pct_rest": 10.0}
    assert abs(_score_h3(bench) - (0.5 + 2 * 0.4)) < 1e-9, _score_h3(bench)

    print("report self-test OK (%d cases)" % 11)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        raise SystemExit(0)
    main()
