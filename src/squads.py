"""Partition the player universe into owned vs buyable.

Reads:
  inputs/rosters_initial.txt  starting rosters — write once, never edit
  inputs/transactions.csv     append-only ledger of every market operation
  inputs/league.ini           managers, budgets, thresholds (all optional)
  inputs/cash.txt             any balance you have actually seen
  inputs/seen.txt             optional — today's market slate, OCR'd off your
                              phone. When present the watchlist BECOMES the
                              slate. Scratch, not state.
  data/tidy/*.csv             values, 24h moves, start probabilities

Writes:
  reports/squads.md       every squad in the league, what they paid, what
                          they can still spend. Named for what it holds:
                          it used to be called rivals.md while rivals.py
                          wrote behaviour.md, which sent you to the wrong
                          file every time.
  reports/watchlist.md    the slate when you pasted one, otherwise everyone
                          unowned cut to something phone-readable
  inputs/squad.txt        GENERATED — your roster, so report.py keeps working
                          unchanged. Stop hand-editing this file.
  data/decisions/slate_log.csv  append-only: which players were on offer when

Run via workflow_dispatch. No arguments.

MIGRATED: read_initial() and apply_transactions() now live in
ffcore.league, so rivals.py replays the ledger the same way rather than
forking a second copy. The tuning constants that used to sit at the top of
this file moved to inputs/league.ini — several were stale after the league
grew from three managers to five, which is the argument for having them in
one place.
"""

import datetime as dt
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ffcore.league import League  # noqa: E402
from ffcore.parse import fmt_money, fmt_pct  # noqa: E402
from ffcore.score import SLOT, formations  # noqa: E402
from ffcore.text import norm  # noqa: E402
from ffcore.tidy import (DECISIONS, REPORTS, append_csv,  # noqa: E402
                         load_players, write_lines)
from seen import read_slate  # noqa: E402

HEAD = ("| Player | Team | Pos | Value | 24h | Start% |\n"
        "|---|---|--:|--:|--:|--:|")

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


def row(rec):
    return "| %s |" % " | ".join([
        rec.get("name", "?") + flag(rec),
        rec.get("team", "—"),
        (rec.get("pos") or "—")[:3],
        fmt_money(rec.get("value")),
        fmt_money(rec.get("delta_1d")),
        fmt_pct(rec.get("start"))])


def log_slate(on_offer, players, stamp):
    """Append-only record of every slate you paste.

    A player who sat on the slate and was never bought is one nobody would pay
    the floor for, which is the ceiling evidence issue #21 asked about — and it
    arrives for free, as a by-product of the paste you already do, rather than
    from a field you have to come back and update. Join it to
    inputs/transactions.csv to ask what went unsold.

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


def write_rivals(lg, players, stamp):
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
            "notes at the bottom. Cash is a ceiling on what anyone can bid "
            "tomorrow, which is the point of tracking it.", ""]

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
        out += [row(r) for r in recs]
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
                "(issue #26). Fix the spelling in `inputs/transactions.csv` if "
                "one of these is the wrong player.", ""]
        out += ["- " + r for r in lg.resolved] + [""]

    unmatched = lg.unmatched(players)
    if unmatched:
        out += ["## Unmatched names", "",
                "In the ledger or the initial rosters but not in the tidy "
                "data — check spelling with find_slug.py. Until they match, "
                "they carry no value and are missing from every total above.",
                ""]
        out += ["- " + u for u in unmatched] + [""]

    write_lines(REPORTS / "squads.md", out)


def write_watchlist(lg, players, stamp, on_offer, unresolved, ambiguous):
    cash = lg[lg.cfg.me].cash if lg.cfg.me in lg.managers else None
    budget = cash.value if cash and cash.confidence == "known" else None

    free = [r for k, r in players.items() if k not in lg.owner]
    out = ["# Watchlist — %s" % stamp, ""]
    if on_offer:
        # Slate pasted: this list IS the decision, so it is not filtered and
        # not truncated. A 40%-start player on today's slate is a choice you
        # are making; the 95% starter who isn't on it is not.
        out += ["The %d players you pasted as today's slate — everyone you can "
                "actually bid on right now, unfiltered. What each one is worth "
                "to your XI, and what to bid, is in the slate table at the top "
                "of this report." % len(on_offer), ""]
    else:
        out += ["Everyone not owned by the %d of us, %d%% start or better."
                % (len(lg.managers), int(lg.cfg.min_start)), ""]
        if budget:
            out += ["Filtered to what your %s of cash can reach."
                    % fmt_money(budget), ""]

    for pos in POS_ORDER:
        if on_offer:
            pool = [r for r in free
                    if (r.get("pos") or "").lower() == pos
                    and norm(r.get("name")) in on_offer]
        else:
            pool = [r for r in free
                    if (r.get("pos") or "").lower() == pos
                    and (r.get("start") or 0) >= lg.cfg.min_start
                    and (budget is None or (r.get("value") or 0) <= budget)]
        pool.sort(key=lambda r: (-(r.get("start") or 0),
                                 -(r.get("delta_1d") or 0)))
        if not pool:
            continue
        out += ["## %s" % pos, "", HEAD]
        out += [row(r) for r in (pool if on_offer
                                 else pool[:lg.cfg.top_n_per_pos])]
        out.append("")

    if unresolved or ambiguous:
        out += ["## Names I could not place", "",
                "OCR mangled these past matching, so they are missing from the "
                "tables above — re-read them off the app if one matters.", ""]
        out += ["- **%s** — no match" % u for u in unresolved]
        out += ["- **%s** — could be %s" % (raw, ", ".join(cands))
                for raw, cands in ambiguous]
        out.append("")

    out += ["---", ""]
    if not on_offer:
        out += ["Not all of these are purchasable today — the app deals a "
                "limited slate. Paste today's slate into the `seen` input and "
                "this list becomes the slate itself.", ""]
    write_lines(REPORTS / "watchlist.md", out)


def write_squad_file(lg, players, path="inputs/squad.txt"):
    """Regenerate squad.txt from the ledger so report.py needs no changes.

    Written in the app's own spelling where we have it, one name per line,
    which is exactly the format report.py already reads.
    """
    names = sorted(players.get(k, {}).get("name", k)
                   for k in lg.squad(lg.cfg.me))
    write_lines(path, [
        "# GENERATED — do not edit. Your marks live in inputs/lineup.txt.",
        "#",
        "# WRITTEN BY: src/squads.py, from the ledger.",
        "# READ BY:    src/report.py, and only as a fallback for when",
        "#             rosters_initial.txt + transactions.csv fail to load.",
        "# Source of truth is inputs/rosters_initial.txt + "
        "inputs/transactions.csv.",
        ""] + names)


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

    # Written to inputs/ like squad.txt, never to the repo root: input_path's
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

    on_offer, unresolved, ambiguous, auto = read_slate(players, lg.owner)
    if on_offer or unresolved or ambiguous:
        print("slate: %d on offer, %d unresolved, %d ambiguous, %d placed by "
              "ownership" % (len(on_offer), len(unresolved), len(ambiguous),
                             len(auto)))
    log_slate(on_offer, players, now.strftime("%Y-%m-%dT%H:%MZ"))

    write_rivals(lg, players, stamp)
    write_watchlist(lg, players, stamp, on_offer, unresolved, ambiguous)
    write_squad_file(lg, players)
    write_lineup_file(lg, players)

    unmatched = lg.unmatched(players)
    free = [k for k in players if k not in lg.owner]
    print("%d players known, %d owned, %d free, %d owned names unmatched"
          % (len(players), len(lg.owner), len(free), len(unmatched)))
    for w in lg.warnings:
        print("WARN " + w)


if __name__ == "__main__":
    main()
