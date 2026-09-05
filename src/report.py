"""
report.py — the squad's own bookkeeping: warnings, the recommendation log,
and the notification surface. Run after ingest.py parse.

    python src/report.py

NOT A REPORT ANY MORE. This used to render a markdown board — one table,
one metric, points above replacement per million — plus five sections of
workings under it (field these eleven, buy today, fitness, starting splits).
sim.py's simulated ladder replaced all of that on 2026-08-18/08-22: it prices
every real move against the actual rest of the season instead of a static
replacement level, so the board here became a second, weaker answer to the
same question, computed and written to `.runtime/parts/latest.md` every run
for nobody — digest.py stopped stitching it into the appendix on 2026-08-22,
and the phone has only ever read sim.py's decisions.json. Deleted 2026-09-05
once that was confirmed (grepped every reader; there were none).

What is left, and genuinely still runs every day:

  * WARNINGS — a stale feed, a thin position, an unmodelled player, a
    crosswalk clash, an unrecorded cash balance. Written to
    `.runtime/warnings.json`, which sim.py's own _warnings() folds into
    decisions.json — this is the ONLY place these facts are produced.
  * ALERTS — the same warnings, filtered to what is worth interrupting
    someone for, plus a login nudge when the league token is expiring.
  * squad_log.csv — one row per player per snapshot, so a scorer can later
    ask what the model's own pick would have cost you, once jornadas exist
    to grade it against.

SCORING lives in ffcore/score.py, shared with sim.py — one builder,
build(), one session (ffcore/model.py) — so this file's warnings and the
simulation's ladder are read off the same squad, never two.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# WHAT IS LEFT OF ffcore/bid.py's SURFACE HERE is low_priced_buys() — a real
# warning (a purchase priced below the floor, worth a by-hand check) — and
# deals(), which feeds it. suggest(), demand_summary(), premiums() and
# xi_snapshots() went with sec_slate() (2026-09-05): they priced a live bid,
# which nothing here still shows.
from ffcore.bid import deals, low_priced_buys  # noqa: E402
from ffcore.render import title_name  # noqa: E402
from ffcore.score import SLOT_LABEL, SLOT_MIN, pick_xi, squad_pool  # noqa: E402
from ffcore.tidy import (run_now,  # noqa: E402
                         DECISIONS,  # noqa: E402
                         age_phrase, append_csv, load_crosswalk,
                         load_deadline, read_csv,
                         snapshot_stamp, stale_feeds, widen_csv, write_lines)

# reports/history/ held a copy of the workings per day. Git already holds
# every version of every generated file with the run that produced it, so the
# archive was a second copy of one, and it was the only thing in reports/ that
# grew without bound. Dropped 2026-08-20.
# In .runtime/ (gitignored): this file is a signal for a notifier, not a
# document. Under reports/ or data/ the run would commit a "you have a Buy"
# note that stops being true within the hour.
ALERTS = Path(os.environ.get("LFG_ALERTS", ".runtime/alerts.md"))
# The same warnings as data, for the renderers that are not markdown.
WARNINGS = Path(os.environ.get("LFG_WARNINGS", ".runtime/warnings.json"))

STALE_HOURS = 14.0

# data/decisions/squad_log.csv, in order. One list, so the migration and the
# write cannot disagree about what the file holds.
#
# The last six arrived with the fixture term and the current-season blend, and
# each is a SEPARATE column rather than folded into `score`: grading a forecast
# means attributing its error to one factor at a time. Was the eleven wrong
# about who starts, about the opponent, or about the player? A single number
# cannot answer that, and the two fixture constants are guesses waiting to be
# graded. Rows written before they existed keep an empty cell, which honestly
# says "not measured" rather than "average".
# `player` is a display name and stays one, for reading. `ff_id` is what a
# later grade will actually join on: a log keyed on a spelling is a log that
# stops matching the moment the source changes how it writes somebody's name,
# and this file exists to be graded months from now.
LOG_COLS = ["observed_at", "hours_to_lock", "formation", "index_total",
            "ff_id", "player", "pos", "slot", "start_pct", "start_source", "status",
            "assumed", "value", "score", "picked",
            "ppm", "fix", "opp", "home", "cur_pj", "flat",
            "fix_basis", "elo_gap"]


def squad_names(lg) -> tuple[list[str], str]:
    """Your roster, and where it came from.

    There is no squad.txt fallback any more. It was a generated copy of this
    same list, read only when League failed to load — but squads.py is what
    wrote it and squads.py needs League too, so a fresh one could never exist
    at the moment it was wanted. All the fallback could ever do was serve a
    stale squad, silently, in the one situation where you most needed to be
    told something was wrong.
    """
    if lg is not None:
        mine = lg.managers.get(lg.cfg.me)
        if mine and mine.players:
            # THE KEYS, NOT THEIR NAMES. Turning a key back into a name so
            # the scorer can match the name is a round trip through the one
            # thing that does not identify anybody — and since the market
            # keys on the site's own id, the name no longer resolves at all.
            return (list(mine.players), "ledger")
    return [], "nothing"


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
    path = DECISIONS / "squad_log.csv"
    # Before the dedup check, not after: a run that has nothing new to log
    # still has to carry the migration, or the columns would only appear on
    # whichever run happens to see a fresh snapshot first.
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
        })
    append_csv(path, rows, LOG_COLS)


# ---------------------------------------------------------------------------
# The five tables
#
# Field these eleven, buy today, what you give up by spending now, sell these,
# and the exceptions. In that order, because that is the order the decisions get
# made in. NONE OF THEM DECIDES ANYTHING: questions 2, 3 and 4 are presentations
# of board_rows(), so the columns they share with the board are the same numbers
# and not a second measurement of them. What each one adds is the market — a bid
# band, who else wants him, what a sale pays — which is the part a single ranked
# table has no room for. Everything else — premium curves, drift, full rosters,
# deal history, methodology — is reference and lives below the fold or in its
# own file.
#
# Question 1 leads with WHAT YOU ARE FIELDING, not with what the model would
# field. The app's own lineup is a fact; the recommendation is advice, and
# printing advice as though it were the team is how the old report managed to
# show two different benches under the same word.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# The board — one row per asset, one rate, one order
#
# The five sections each answered their own question and left the joining-up
# to the reader, which is how the report told you to field a man in question 1
# and sell him in question 4. This table is the join, and the source: the five
# read it. Every asset you could hold — owned, buyable, and the cash — gets one
# row priced in ONE unit, points above replacement per million.
#
# CASH IS A ROW, not a footnote about opportunity cost: a competing asset with
# a rate of its own, so the line it sits on IS the decision. Hold above, sell
# below, buy above, pass below — four verdicts from one comparison instead of
# four rules that can disagree.
#
# Replacement level is fixed by the rules, so a row is a decision rather than a
# snapshot of a search. That is what λ, measured against your current eleven,
# could not be.
# ---------------------------------------------------------------------------


def stale_feed_warnings(quiet=None) -> list[str]:
    """The app's feed has gone quiet — said once, or [].

    A GATE THAT ONLY REFUSES IS HALF A FIX. ffcore.tidy hands every reader []
    once the league's API stops answering, which is the honest thing to do
    with a three-day-old squad; but [] arrives downstream as "nobody owns
    anybody and nothing is for sale". Aged three days, the store still
    produced a full report: squads silently fell back to the ledger, all five
    managers' points read 0, and the only word about it was one row in
    METHOD.md's appendix. This is the sentence that belongs next to the
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
    """What is worth interrupting somebody for ABOUT THE SQUAD, or [].

    This is half the notification surface; sim.py writes the other half and
    puts the decision above these, because a move is news and a shortage is
    the context for it.

    The whole design is what it LEAVES OUT. It replaced watch.py, which
    diffed market values and emitted every player who moved 2% in a day —
    thirty-odd rows, league-wide, not one of them a decision. Nobody read that
    file, which is the only reason it was harmless; pushed to a phone it would
    be spam, and spam is how you learn to swipe away the one that mattered.
    The verdict scan that used to run here went the same way with the board:
    it fired on every Buy and every Sell in a twenty-row table.

    Returns [] when there is nothing to say, and the caller must send NOTHING
    rather than "no news" — a twice-daily "all quiet" is the same spam by
    another route.
    """
    out = ["**Squad** — %s" % w for w in warnings]
    if token_days is not None and token_days < 14:
        out.append("**Log in again** — the league token expires in %d days "
                   "(`python -m ffcore.auth --login`)" % token_days)
    return out


def main() -> None:
    # ONE MODEL PER RUN — see ffcore/model.py. This built its own League and
    # its own Scorer while decide.load() built a second pair from different
    # rows, so the two surfaces could describe two different models and
    # nothing said which was right.
    from ffcore.model import session
    m = session()
    market = m.market

    if not market:
        print("no market data; nothing to warn about")
        return

    # No try/except. A League that will not load means rosters_initial.txt is
    # missing, and the only thing the old fallback could produce was a report
    # built on a stale generated copy of the squad. Failing here is the honest
    # outcome: the run stops, systemd records it, and nothing publishes a
    # report that looks fine and is not.
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

    xw = load_crosswalk()
    pool = squad_pool(players)
    best = pick_xi(pool) if players else None

    cash = lg[lg.cfg.me].cash if lg and lg.cfg.me in lg.managers else None
    dl = deals(lg, lg.market) if lg and lg.market else []

    # Still read here, because log_squad records it against every snapshot: a
    # forecast is graded by how long before the lock it was made.
    deadline, _dl_src = load_deadline(with_source=True)
    if players and best:
        log_squad(observed, players, {id(p) for p in best[2]}, best[1],
                  best[0], deadline, obs_dt)

    # --- the warnings themselves -------------------------------------------
    warnings: list[str] = stale_feed_warnings()
    if age_h is not None and age_h > STALE_HOURS:
        warnings.append(f"**Data is {age_h:.0f}h old** — the ingest workflow "
                        "may have failed. Everything above is that snapshot.")
    if best:
        # NOT A DECISION RULE ANY MORE. THIN used to gate whether a sale was
        # allowed; what a shortage actually is, is a fact about the squad —
        # you cannot field a legal eleven if one of these gets hurt — and the
        # simulation prices the squad you would hold rather than consulting a
        # threshold about it.
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
        # A clash means an identifier two players claim, which identifies
        # neither — crosswalk.py refuses it rather than guessing, so nothing
        # crashes, but it silently costs a join everywhere that id was the
        # only bridge. This ran unattended and printed to a log nobody reads
        # (2026-08-20) until surfaced here; the identity bug it would have
        # caught (app_id 2614 held by two players) predates this warning.
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
    # Written every run, and DELETED when there is nothing to say, so a
    # notifier can simply test for the file rather than parse it to find out
    # whether it matters. An empty alerts file that has to be read to discover
    # it is empty is how "no news" gets pushed to a phone twice a day.
    try:
        from ffcore.auth import TokenStore
        token_days = TokenStore().expiry_days()
    except Exception:                                       # noqa: BLE001
        token_days = None
    # THE WARNINGS BELONG ON THE PAGE THAT IS READ, and the page that is read
    # is the board on the phone. They were only ever in REPORT.md, which is
    # the same content the board draws — so the one section the board did NOT
    # have was the one telling you something is wrong. Handed over as data;
    # sim.py folds it into decisions.json a moment later.
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
    # -- a feed that has gone quiet is a warning, not a silence ------------
    # The gate in ffcore.tidy hands every reader [] when the app's feed goes
    # stale, and [] reads downstream as "you own nothing, nothing is for
    # sale". Measured on the store aged three days: the squad table quietly
    # became the ledger's, five managers' points all read 0, and nothing on
    # the page said why.
    w = stale_feed_warnings({"api_teams": 3.1, "api_market": 3.1})
    assert len(w) == 1 and "3 days" in w[0], w
    assert "api_teams" in w[0] and "api_market" in w[0], w
    assert stale_feed_warnings({}) == []

    # --- names ---
    # A particle stays lowercase inside a name, but not as its first word.
    # title_name itself is tested in ffcore/render.py, where it lives now.
    assert title_name("nico van gaal") == "Nico van Gaal"

    # -- alerts: the short list worth interrupting somebody for -------------
    # This replaced watch.py, which diffed market values and emitted every
    # player who moved 2% in a day — thirty-odd rows, league-wide, none of them
    # a decision. As a file nobody read that was harmless; as a notification it
    # would be spam, and spam trains you to ignore the one that mattered. The
    # verdict scan that used to run here went with the board, for the same
    # reason: it fired on every Buy and Sell in a twenty-row table. What is
    # left is the squad, and sim.py puts the decision above it.
    body = "\n".join(alerts(["Only 1 delantero"], token_days=None))
    assert "delantero" in body, body
    # Nothing to say means NO alert, not an empty one. A notification that
    # says "no news" every twelve hours is the same spam by another route.
    assert alerts([], None) == []
    # The login expiring is the one piece of plumbing worth a nudge, because
    # the failure is silent and the fix needs a human at a browser.
    assert any("Log in again" in ln for ln in alerts([], token_days=9))
    assert alerts([], token_days=60) == []

    print("report self-test OK (%d cases)" % 10)


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        raise SystemExit(0)
    main()
