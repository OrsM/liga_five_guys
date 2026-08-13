"""Partition the player universe into owned vs buyable.

Reads:
  inputs/rosters_initial.txt  starting rosters — write once, never edit
  inputs/transactions.csv     append-only ledger of every market operation
  inputs/league.ini           managers, budgets, thresholds (all optional)
  inputs/cash.txt             any balance you have actually seen
  inputs/seen.txt             optional — today's market slate, OCR'd off your
                              phone. Marks which of the watchlist you can
                              actually buy right now. Scratch, not state.
  data/tidy/*.csv             values, 24h moves, start probabilities

Writes:
  reports/rivals.md       every rival squad, what they pay, what they can
                          still spend
  reports/watchlist.md    everyone unowned, cut to something phone-readable
  inputs/squad.txt        GENERATED — your roster, so report.py keeps working
                          unchanged. Stop hand-editing this file.

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

from common import (load_players, fmt_money, fmt_pct, pos_key,  # noqa: E402
                    flag, POS_ORDER)
from ffcore.league import League  # noqa: E402
from ffcore.text import norm  # noqa: E402
from ffcore.tidy import REPORTS, input_path, write_lines  # noqa: E402
from seen import match, read_names  # noqa: E402

HEAD = ("| Player | Team | Pos | Value | 24h | Start% |\n"
        "|---|---|--:|--:|--:|--:|")

HEAD_SEEN = ("| Player | Team | Pos | Value | 24h | Start% | On offer |\n"
             "|---|---|--:|--:|--:|--:|---|")


def row(rec, on_offer=None):
    cells = [rec.get("name", "?") + flag(rec),
             rec.get("team", "—"),
             (rec.get("pos") or "—")[:3],
             fmt_money(rec.get("value")),
             fmt_money(rec.get("delta_1d")),
             fmt_pct(rec.get("start"))]
    if on_offer:
        cells.append("✅" if norm(rec.get("name")) in on_offer else "—")
    return "| %s |" % " | ".join(cells)


def read_seen(players):
    """(keys on offer, unresolved, ambiguous) from inputs/seen.txt.

    Absent file is the normal case — you only paste a slate when deciding.
    The file is scratch, not state: nothing downstream depends on it being
    current, so it cannot drift the way offers.txt did.
    """
    path = input_path("seen.txt")
    if not path.exists():
        return set(), [], []
    return match(read_names(path.read_text(encoding="utf-8")), players)


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

    unmatched = lg.unmatched(players)
    if unmatched:
        out += ["## Unmatched names", "",
                "In the ledger or the initial rosters but not in the tidy "
                "data — check spelling with find_slug.py. Until they match, "
                "they carry no value and are missing from every total above.",
                ""]
        out += ["- " + u for u in unmatched] + [""]

    write_lines(REPORTS / "rivals.md", out)


def write_watchlist(lg, players, stamp):
    cash = lg[lg.cfg.me].cash if lg.cfg.me in lg.managers else None
    budget = cash.value if cash and cash.confidence == "known" else None

    on_offer, unresolved, ambiguous = read_seen(players)

    free = [r for k, r in players.items() if k not in lg.owner]
    out = ["# Watchlist — %s" % stamp, "",
           "Everyone not owned by the %d of us, %d%% start or better."
           % (len(lg.managers), int(lg.cfg.min_start)), ""]
    if budget:
        out += ["Filtered to what your %s of cash can reach."
                % fmt_money(budget), ""]
    if on_offer:
        out += ["**%d of these are on offer right now** (from the slate you "
                "pasted in) — they sort to the top of each position and carry "
                "a ✅." % len(on_offer), ""]

    head = HEAD_SEEN if on_offer else HEAD
    for pos in POS_ORDER:
        pool = [r for r in free
                if (r.get("pos") or "").lower() == pos
                and (r.get("start") or 0) >= lg.cfg.min_start
                and (budget is None or (r.get("value") or 0) <= budget)]
        # On offer first: a 95% starter you cannot buy today is not a
        # decision, and the whole point of the slate is to say which are.
        pool.sort(key=lambda r: (norm(r.get("name")) not in on_offer,
                                 -(r.get("start") or 0),
                                 -(r.get("delta_1d") or 0)))
        if not pool:
            continue
        out += ["## %s" % pos, "", head]
        out += [row(r, on_offer) for r in pool[:lg.cfg.top_n_per_pos]]
        out.append("")

    if unresolved or ambiguous:
        out += ["## Names I could not place", "",
                "OCR mangled these past matching, so they are missing from "
                "the ✅ marks above — re-read them off the app if one matters.",
                ""]
        out += ["- **%s** — no match" % u for u in unresolved]
        out += ["- **%s** — could be %s" % (raw, ", ".join(cands))
                for raw, cands in ambiguous]
        out.append("")

    out += ["---", ""]
    if on_offer:
        out += ["A ✅ means you told me it was on the slate. Everything else "
                "is the shortlist to recognise against when the slate "
                "rotates.", ""]
    else:
        out += ["Not all of these are purchasable today — the app deals a "
                "limited slate. Paste today's slate into the `seen` input to "
                "mark which ones you can actually buy.", ""]
    write_lines(REPORTS / "watchlist.md", out)


def write_squad_file(lg, players, path="inputs/squad.txt"):
    """Regenerate squad.txt from the ledger so report.py needs no changes.

    Written in the app's own spelling where we have it, one name per line,
    which is exactly the format report.py already reads.
    """
    names = sorted(players.get(k, {}).get("name", k)
                   for k in lg.squad(lg.cfg.me))
    write_lines(path, [
        "# GENERATED by src/squads.py — do not edit.",
        "# Source of truth is inputs/rosters_initial.txt + "
        "inputs/transactions.csv.",
        ""] + names)


def main():
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    players = load_players()
    lg = League.load()
    print("replayed %d transaction(s)" % len(lg.txns))

    write_rivals(lg, players, stamp)
    write_watchlist(lg, players, stamp)
    write_squad_file(lg, players)

    unmatched = lg.unmatched(players)
    free = [k for k in players if k not in lg.owner]
    print("%d players known, %d owned, %d free, %d owned names unmatched"
          % (len(players), len(lg.owner), len(free), len(unmatched)))
    for w in lg.warnings:
        print("WARN " + w)


if __name__ == "__main__":
    main()
