


from ffcore.bid import (HORIZONS, MAX_LAG_H, deals,
                        premiums, usable)
from ffcore.parse import fmt_money, fmt_pct
from ffcore.second import LEGEND, af_cell, second_cells
from ffcore.text import norm
from ffcore.tidy import (run_now, shown,
                         DECISIONS, PARTS, append_csv,
                         load_players, stale_owned_players,
                         widen_csv, write_lines)
from slate import read_slate

HEAD = ("| Player | Team | Pos | Value | 24h | FF | AF |\n"
        "|---|---|--:|--:|--:|--:|--:|")

SLATE_LOG = ["observed_at", "ff_id", "player", "value", "start_pct"]

POS_ORDER = ["portero", "defensa", "mediocampista", "delantero", "entrenador"]

OK_STATUS = ("ok", "", "none", "disponible", "available")


def flag(rec):
    st = (rec.get("status") or "").strip().lower()
    return "" if st in OK_STATUS else " ⚠︎%s" % st


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
    if not on_offer:
        return
    rows = []
    for k in sorted(on_offer):
        rec = players.get(k, {})
        rows.append({
            "observed_at": stamp,
            "ff_id": k,
            "player": rec.get("name", k),
            "value": "" if rec.get("value") is None else "%.0f" % rec["value"],
            "start_pct": ("" if rec.get("start") is None
                          else "%.0f" % rec["start"]),
        })
    path = DECISIONS / "slate_log.csv"
    widen_csv(path, SLATE_LOG)
    append_csv(path, rows, SLATE_LOG)


def pct(v) -> str:
    return "—" if v is None else "%+.1f%%" % v


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
        recs.sort(key=lambda r: (
            POS_ORDER.index((r.get("pos") or "").lower())
            if (r.get("pos") or "").lower() in POS_ORDER else len(POS_ORDER),
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
                "implies. The ledger is generated — a wrong player here is "
                "fixed in `inputs/rosters_initial.txt`.", ""]
        out += ["- " + r for r in lg.resolved] + [""]

    unmatched = lg.unmatched(players)
    if unmatched:
        out += ["## Unmatched names", "",
                "In the ledger or the initial rosters but not in the tidy "
                "data. Until the names match, "
                "they carry no value and are missing from every total above.",
                ""]
        out += ["- " + u for u in unmatched] + [""]

    if dl:
        out += sec_premium(lg, dl)
        if market is not None:
            out += sec_drift(dl, market)

    PARTS.mkdir(parents=True, exist_ok=True)
    write_lines(PARTS / "league.md", out)


def main():
    now = run_now()
    stamp = shown(now)
    players = load_players()
    from ffcore.model import session

    lg = session().lg
    print("replayed %d transaction(s)" % len(lg.txns))
    players = stale_owned_players(players, lg.owner, lg.market)

    on_offer, unresolved = read_slate(lg.market, xw=lg.xw)
    if on_offer or unresolved:
        print("slate: %d on offer, %d unjoined"
              % (len(on_offer), len(unresolved)))
    log_slate(on_offer, players, now.strftime("%Y-%m-%dT%H:%MZ"))

    second, _unclear = second_cells((k, r.get("name", ""))
                                    for k, r in players.items())
    write_league(lg, players, stamp, second,
                 dl=deals(lg, lg.market), market=lg.market)

    unmatched = lg.unmatched(players)
    free = [k for k in players if k not in lg.owner]
    print("%d players known, %d owned, %d free, %d owned names unmatched"
          % (len(players), len(lg.owner), len(free), len(unmatched)))
    for w in lg.warnings:
        print("WARN " + w)


if __name__ == "__main__":
    main()
