"""Partition the player universe into owned vs buyable.

Reads:
  inputs/rosters_initial.txt  starting rosters — write once, never edit
  data/tidy/transactions.csv  the ledger of every market operation, generated
  inputs/league.ini           managers, budget, thresholds (all optional)
  inputs/cash.txt             any balance you have actually seen
  data/tidy/*.csv             values, 24h moves, start probabilities

Writes:
  reports/league.md       every squad in the league, what they paid, what
                          they can still spend. Named for what it holds:
                          wrote behaviour.md, which sent you to the wrong
                          file every time.
  data/decisions/slate_log.csv  append-only: which players were on offer when

watchlist.md went on 2026-08-18, with the board. It re-listed the slate with
a value and two probable-XI columns, under a heading that promised "everyone
unowned, ranked" — and after the cutover the ranking of everyone acquirable,
free agents and rivals' players alike, is the one table in REPORT.md. What was
genuinely only there is the FF/AF disagreement, which is now a column of the
bid table in latest.md, next to the player it would change your mind about.

Run via workflow_dispatch. No arguments.

MIGRATED: read_initial() and apply_transactions() now live in
ffcore.league, so every reader replays the ledger the same way rather than
forking a second copy. The tuning constants that used to sit at the top of
this file moved to inputs/league.ini — several were stale after the league
grew from three managers to five, which is the argument for having them in
one place.
"""

import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ffcore.bid import (HORIZONS, MAX_LAG_H, deals,  # noqa: E402
                        premiums, usable)
from ffcore.league import League  # noqa: E402
from ffcore.parse import fmt_money, fmt_pct  # noqa: E402
from ffcore.score import SLOT, formations  # noqa: E402
from ffcore.second import LEGEND, af_cell, second_cells  # noqa: E402
from ffcore.text import norm  # noqa: E402
from ffcore.tidy import (DECISIONS, REPORTS, append_csv,  # noqa: E402
                         load_players, write_lines)
from slate import read_slate  # noqa: E402

# Both probable-XI sources, side by side, in every table this module writes.
# One unlabelled Start% column hid which site said it, and the two disagree
# often enough that the disagreement is the useful part.
HEAD = ("| Player | Team | Pos | Value | 24h | FF | AF |\n"
        "|---|---|--:|--:|--:|--:|--:|")

SLATE_LOG = ["observed_at", "player", "value", "start_pct"]

# Pitch order, and what counts as fit. Only this module reads them.
POS_ORDER = ["portero", "defensa", "mediocampista", "delantero", "entrenador"]

OK_STATUS = ("ok", "", "none", "disponible", "available")


def flag(rec):
    """Marker for anything the feed says isn't a clean 'ok'."""
    st = (rec.get("status") or "").strip().lower()
    return "" if st in OK_STATUS else " ⚠︎%s" % st


def pos_key(p):
    p = (p or "").lower()
    return POS_ORDER.index(p) if p in POS_ORDER else len(POS_ORDER)


def row(rec, cells=None):
    return "| %s |" % " | ".join([
        rec.get("name", "?") + flag(rec),
        rec.get("team", "—"),
        (rec.get("pos") or "—")[:3],
        fmt_money(rec.get("value")),
        fmt_money(rec.get("delta_1d")),
        fmt_pct(rec.get("start")),
        af_cell((cells or {}).get(norm(rec.get("name", ""))))])


def log_slate(on_offer, players, stamp):
    """Append-only record of every slate you paste.

    A player who sat on the slate and was never bought is one nobody would pay
    the floor for, which is the ceiling evidence issue #21 asked about — and it
    arrives for free, as a by-product of the paste you already do, rather than
    from a field you have to come back and update. Join it to
    the ledger to ask what went unsold.

    Nothing reads this yet. A fortnight of slates is not a base rate, and
    saying so is cheaper than a section that pretends otherwise.
    """
    if not on_offer:
        return
    rows = []
    for k in sorted(on_offer):
        rec = players.get(k, {})
        rows.append({
            "observed_at": stamp,
            "player": rec.get("name", k),
            "value": "" if rec.get("value") is None else "%.0f" % rec["value"],
            "start_pct": ("" if rec.get("start") is None
                          else "%.0f" % rec["start"]),
        })
    append_csv(DECISIONS / "slate_log.csv", rows, SLATE_LOG)




def pct(v) -> str:
    """Signed, one decimal — a drift, not a level. Deliberately NOT
    ffcore.parse.fmt_pct, which prints an unsigned whole-number level."""
    return "—" if v is None else "%+.1f%%" % v



# ---------------------------------------------------------------------------
# 2. premium curve
# ---------------------------------------------------------------------------

def sec_premium(lg, dl) -> list[str]:
    out = ["## What they pay over value", ""]
    buys = [d for d in dl if d["side"] == "buy"]
    good = [d for d in buys if usable(d)]
    if not good:
        return out + ["_No purchase yet lines up with a market snapshot "
                      "close enough in time to price. This fills in as the "
                      "ingest history grows._", ""]

    out += ["| Manager | Buys | Median premium | Range | Round bids |",
            "|---|--:|--:|---|--:|"]
    for m in lg:
        mine = [d for d in good if d["actor"] == m.handle]
        if not mine:
            continue
        prem = sorted(d["premium"] for d in mine)
        rnd = sum(1 for d in buys if d["actor"] == m.handle and d["round"])
        out.append("| %s | %d | %s | %s to %s | %d/%d |" % (
            m.handle, len(mine), pct(prem[len(prem) // 2]),
            pct(prem[0]), pct(prem[-1]), rnd,
            len([d for d in buys if d["actor"] == m.handle])))

    all_prem = premiums(dl)
    if all_prem:
        # Computed, never asserted. This paragraph used to state that the floor
        # had never won, which was true of the first ten buys and false by the
        # fifteenth while still printing as fact (issue #23).
        won = all_prem.at_floor
        if won:
            head = ("**The floor sometimes wins.** %d of the %d priced "
                    "purchases in this league went at the market value itself "
                    "and the other %d cleared it, %s across all of them. "
                    "Bidding the minimum is therefore not the one number known "
                    "to lose — but %d of %d is a share of the bids that WON, "
                    "not the odds of winning one. Nothing in this ledger "
                    "records a bid that lost, so the floor's failure rate is "
                    "unmeasured and unmeasurable from here."
                    % (won, all_prem.n, all_prem.n - won, all_prem.label(),
                       won, all_prem.n))
        else:
            head = ("**The floor has not won yet.** All %d priced purchases in "
                    "this league landed above the market value at the time: "
                    "%s. On this evidence the minimum legal bid is the one "
                    "number every deal has beaten — but %d deals is a fortnight "
                    "of a season, not a rule."
                    % (all_prem.n, all_prem.label(), all_prem.n))
        out += ["", head]

    app = premiums(dl, "sell")
    if app and app.n >= 3:
        out += ["",
                "**The app does not pay you the value — it randomises around "
                "it.** The %d priced sales back to the market went for %s: %d "
                "below the value and %d above, never further than %.1f%% "
                "either way. So a sale raises the value give or take a tenth, "
                "and the value is not the money you will get. Whether the same "
                "randomiser bids against you for a free agent is inferred, not "
                "measured: every row in this ledger is a bid that won."
                % (app.n, app.label(), app.at_floor, app.n - app.at_floor,
                   app.swing())]

    out += ["",
            "A round bid was typed by a human. That is the whole of what "
            "roundness tells you — an exact bid is *not* the app's valuation "
            "and does not mean nobody competed, because the premium column "
            "two cells left already measures how far above the floor the "
            "buyer went. Sealed bids are paid as bid, so a purchase at exactly "
            "the value was only ever yours to take if the tie-break favoured "
            "you, and that rule is not documented anywhere we can read. Check "
            "it in-app before reading a floor purchase as a bargain you "
            "missed.", "",
            "| Date | Player | Buyer | Paid | Value then | Premium | Bid |",
            "|---|---|---|--:|--:|--:|---|"]
    for d in sorted(buys, key=lambda d: d["date"], reverse=True)[:25]:
        mark = "" if usable(d) else " ~"
        out.append("| %s | %s | %s | %s | %s%s | %s | %s |" % (
            d["date"][5:], d["player"], d["actor"], fmt_money(d["price"]),
            fmt_money(d["value"]), mark, pct(d["premium"]),
            "round" if d["round"] else "exact"))
    out += ["", "`~` priced against a snapshot more than %dh away and left "
            "out of the medians." % MAX_LAG_H, ""]
    return out



# ---------------------------------------------------------------------------
# 3. post-buy drift
# ---------------------------------------------------------------------------

def sec_drift(dl, market) -> list[str]:
    out = ["## What a deal did to the price", ""]
    rows = []
    for d in dl:
        drifts = [market.drift(d["player"], d["when"], h) for h in HORIZONS]
        if any(x is not None for x in drifts):
            rows.append((d, drifts))
    if not rows:
        return out + ["_No horizon has elapsed inside the snapshot history "
                      "yet. Needs %d days of daily ingest past a "
                      "transaction._" % min(HORIZONS), ""]

    out += ["| Date | Player | Actor | Side | " +
            " | ".join("+%dd" % h for h in HORIZONS) + " |",
            "|---|---|---|---|" + "--:|" * len(HORIZONS)]
    for d, drifts in sorted(rows, key=lambda r: r[0]["date"], reverse=True)[:25]:
        cells = [pct(x[1]) if x else "—" for x in drifts]
        out.append("| %s | %s | %s | %s | %s |" % (
            d["date"][5:], d["player"], d["actor"], d["side"],
            " | ".join(cells)))

    chasing = [d for d, _ in rows
               if d["side"] == "buy" and (d.get("value") or 0) > 0]
    if chasing:
        out += ["", "Two errors this table is built to catch: buying a "
                "player who has already risen (paying the top of the move), "
                "and selling one who has just dipped (realising the bottom). "
                "Both show as the drift column reversing sign against the "
                "actor.", ""]
    return out

def write_league(lg, players, stamp, second=None,
                 dl=None, market=None):
    out = ["# Squads — %s" % stamp, ""]

    out += ["| Manager | Players | Squad value | Spent | Raised | Cash |",
            "|---|--:|--:|--:|--:|--:|"]
    for m in lg:
        recs = [players.get(k, {}) for k in m.players]
        total = sum(r.get("value") or 0 for r in recs)
        out.append("| %s | %d | %s | %s | %s | %s |" % (
            ("**%s**" % m.handle) if m.handle == lg.cfg.me else m.handle,
            len(m.players), fmt_money(total), fmt_money(m.spend),
            fmt_money(m.proceeds), m.cash.label()))
    out += ["",
            "`~` is an estimate, not an observed balance — see the basis "
            "notes at the bottom. A negative one is a real position, not a "
            "broken input: going past the budget mid-window is allowed, and "
            "only being under water at the lock is not. Cash is a ceiling on "
            "what anyone can bid tomorrow, which is the point of tracking "
            "it.", "", LEGEND, ""]

    for m in lg:
        recs = [players.get(k, {"name": k}) for k in m.players]
        starters = sum(1 for r in recs
                       if (r.get("start") or 0) >= lg.cfg.start_cross)
        out += ["## %s" % ("You (%s)" % m.handle if m.handle == lg.cfg.me
                           else m.handle),
                "%d players · %s total · %d at %d%%+ · cash %s" % (
                    len(recs),
                    fmt_money(sum(r.get("value") or 0 for r in recs)),
                    starters, int(lg.cfg.start_cross), m.cash.label()),
                "", HEAD]
        recs.sort(key=lambda r: (pos_key(r.get("pos")),
                                 -(r.get("value") or 0)))
        out += [row(r, second) for r in recs]
        out.append("")

    paid = [t for t in lg.txns if (t.get("price") or "").strip()]
    if paid:
        out += ["## What they pay", "",
                "| Date | Player | From → To | Price |",
                "|---|---|---|--:|"]
        for t in paid[-25:]:
            out.append("| %s | %s | %s → %s | %s |" % (
                t.get("date", "?"), t["player"],
                t.get("from") or "market", t.get("to") or "market",
                t.get("price")))
        out.append("")

    out += ["## Cash basis", ""]
    for m in lg:
        out.append("- **%s** — %s (%s)" % (m.handle, m.cash.basis or "—",
                                           m.cash.confidence))
    out.append("")

    if lg.warnings:
        out += ["## Ledger warnings", ""] + ["- " + w for w in lg.warnings]
        out.append("")

    if lg.resolved:
        out += ["## Names the ledger did not spell exactly", "",
                "Placed by who the counterparty was, or by what the price "
                "implies — a player sold by a manager was in that manager's "
                "squad, and a player bought from the market was in nobody's "
                "(issue #26). The ledger itself is generated and editing it "
                "does nothing, so a wrong player here is fixed in "
                "`inputs/rosters_initial.txt`.", ""]
        out += ["- " + r for r in lg.resolved] + [""]

    unmatched = lg.unmatched(players)
    if unmatched:
        out += ["## Unmatched names", "",
                "In the ledger or the initial rosters but not in the tidy "
                "data. Until the names match, "
                "they carry no value and are missing from every total above.",
                ""]
        out += ["- " + u for u in unmatched] + [""]

    # The empirical bid data, folded in from the old rivals.py. It lived in a
    # second 15KB file that also reprinted this file's cash table, this file's
    # ledger warnings and a second view of the same squads — two reports over
    # the same facts, and the other one had 21 lines of tests behind 497 lines
    # of code. What it uniquely knew is these two tables, and they belong
    # beside the squads they describe.
    if dl:
        out += sec_premium(lg, dl)
        if market is not None:
            out += sec_drift(dl, market)

    write_lines(REPORTS / "league.md", out)


def write_lineup_file(lg, players, path="inputs/lineup.txt"):
    """The XI checklist: every player you own, with a mark you toggle.

    Typing a bench list stops scaling the moment the bench is four names, so
    this inverts it — the file lists the squad and you edit marks, never
    names. It is REGENERATED every run, which is what keeps it honest:
    players you sold disappear instead of lingering as a stale bench entry.

    Your marks survive regeneration; a player you just bought arrives
    BENCHED, so nobody is ever fielded by accident. The header line counts
    the marks and names the shape, so an illegal eleven is visible in the
    file itself, before xi.py ever reads it.

    This is now the ONLY file that describes your eleven. inputs/bench.txt
    is gone: it was a second, hand-typed answer to the same question, still
    read as a fallback and therefore able to contradict the marks here.
    """
    from pathlib import Path

    from xi import read_checklist

    # Written to inputs/, never to the repo root: input_path's
    # fallback is for reading files that may not exist yet, not for choosing
    # where a generated one lands.
    path = Path(path)
    prev = {}
    if path.exists():
        for fielded, raw in read_checklist(path.read_text(encoding="utf-8")):
            prev[norm(raw)] = fielded
    # No file yet means nobody is marked, and an unmarked squad reports as an
    # illegal XI in the header below. That is correct: only you know who sits,
    # and a guess here would be indistinguishable from a decision.

    rows = []
    for k in lg.squad(lg.cfg.me):
        rec = players.get(k, {})
        name = rec.get("name", k)
        pos = (rec.get("pos") or "").lower()
        rows.append((pos_key(pos), SLOT.get(pos, "???"), name,
                     prev.get(norm(name), False)))
    rows.sort(key=lambda r: (r[0], r[2]))

    n = sum(1 for r in rows if r[3])
    counts = {}
    for _, slot, _, fielded in rows:
        if fielded:
            counts[slot] = counts.get(slot, 0) + 1
    shape = (counts.get("DEF", 0), counts.get("MED", 0), counts.get("DEL", 0))
    legal = (counts.get("POR", 0) == 1 and shape in
             {(d, m, f) for d, m, f in formations()})
    verdict = ("%d-%d-%d legal" % shape if legal
               else "NOT a legal XI — fix the marks")

    write_lines(path, [
        "# XI CHECKLIST — edit the marks, not the names.",
        "#   [x] = fielded     [ ] = benched",
        "#",
        "# Regenerated by src/squads.py on every run: your marks are kept, "
        "sold",
        "# players vanish, and anyone you just bought arrives BENCHED — you "
        "field",
        "# him deliberately or not at all. Only the marks standing at lock "
        "matter;",
        "# xi.py logs them with the hours left, so the last row before "
        "kickoff is",
        "# the XI you actually fielded.",
        "#",
        "#",
        "# WRITTEN BY: src/squads.py (the names) and you (the marks).",
        "# READ BY:    src/xi.py, src/report.py.",
        "#",
        "# %d marked · %s" % (n, verdict),
        ""] + ["[%s] %s %s" % ("x" if fielded else " ", slot, name)
               for _, slot, name, fielded in rows])


def main():
    now = dt.datetime.now(dt.timezone.utc)
    stamp = now.strftime("%Y-%m-%d %H:%M UTC")
    players = load_players()
    lg = League.load()
    print("replayed %d transaction(s)" % len(lg.txns))

    on_offer, unresolved = read_slate(lg.market)
    ambiguous, auto = [], []      # the feed states who is on offer; nothing
    if on_offer or unresolved:    # is guessed, so nothing can be ambiguous
        print("slate: %d on offer, %d unjoined"
              % (len(on_offer), len(unresolved)))
    log_slate(on_offer, players, now.strftime("%Y-%m-%dT%H:%MZ"))

    # The second opinion, joined once for every player either file can print.
    second, _unclear = second_cells(r.get("name", "")
                                    for r in players.values())
    write_league(lg, players, stamp, second,
                 dl=deals(lg, lg.market), market=lg.market)
    write_lineup_file(lg, players)

    unmatched = lg.unmatched(players)
    free = [k for k in players if k not in lg.owner]
    print("%d players known, %d owned, %d free, %d owned names unmatched"
          % (len(players), len(lg.owner), len(free), len(unmatched)))
    for w in lg.warnings:
        print("WARN " + w)


if __name__ == "__main__":
    main()
