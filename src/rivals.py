"""
rivals.py — how the other four behave, where it costs them, and how to use it.

    python src/rivals.py            # writes reports/rivals.md

Five sections, in descending order of how much they are worth to you today:

  1. Cash and ceilings   what each rival can still spend. A hard bound on
                         tomorrow's bidding, and the app never shows it.
  2. Premium curve       what they pay over market value, and what the app
                         pays you — every count in it is computed from the
                         ledger on each run, because the version of this
                         section that asserted the floor never wins was still
                         printing that after the floor had won (issue #23).
  3. Post-buy drift      what their purchases did next. Tests two specific
                         errors: momentum chasing and selling into a dip.
  4. Squad diagnostics   trapped capital, injured holds, positional holes,
                         and whether they can field a legal XI at all.
  5. Demand forecast     which unowned players each rival structurally needs
                         — so you know where not to start a bidding war, and
                         what to list to whom. With a slate pasted, this now
                         prices every slate player through EVERY manager's
                         eyes: their XI gain, capped by their cash.
  6. Projected XIs       each manager's best legal XI under the same scorer,
                         with expected points — the projected jornada table.
                         Logged to data/decisions/rival_xi_log.csv so their
                         forecasts can be scored against the app's actual
                         standings once jornadas exist.

Every run appends its estimates to data/decisions/rival_log.csv. Premiums and
cash estimates are not reconstructable after the fact, and the point of
writing them down as they are made is that they can be scored later.

READ IT SCEPTICALLY FOR NOW. Sections 1 and 5 work off today's data.
Section 2 needs roughly 30-40 settled purchases before the medians mean
anything, and section 3 needs jornadas to have been played. With a fortnight
of ledger and five managers, most of this is a hypothesis with a number
attached, not a finding.
"""

from __future__ import annotations

import datetime as dt
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ffcore.bid import (MAX_LAG_H, deals, gain, premiums,  # noqa: E402
                        rivals_short, usable, xi_snapshots)
from ffcore.league import League  # noqa: E402
from ffcore.parse import fmt_money as eur  # noqa: E402
from ffcore.score import (MAX_SLOT, SLOT_LABEL, SLOT_MIN, THIN,  # noqa: E402
                          build, pick_xi, squad_pool)
from ffcore.text import norm  # noqa: E402
from ffcore.tidy import (DECISIONS, REPORTS, append_csv, latest_only,  # noqa: E402
                         load_market, load_lineups, write_lines)
from seen import read_slate  # noqa: E402

# Drift horizons, in days after a purchase.
HORIZONS = (3, 7, 14)
# Below this start probability, money is parked rather than working.
TRAPPED_START = 50.0


def pct(v) -> str:
    """Signed, one decimal — a drift, not a level. Deliberately NOT
    ffcore.parse.fmt_pct, which prints an unsigned whole-number level."""
    return "—" if v is None else "%+.1f%%" % v


# ---------------------------------------------------------------------------
# 1. cash
# ---------------------------------------------------------------------------

def sec_cash(lg) -> list[str]:
    out = ["## 1. Cash and ceilings", "",
           "| Manager | Players | Spent | Raised | Net | Cash | Max bid |",
           "|---|--:|--:|--:|--:|--:|--:|"]
    for m in lg:
        out.append("| %s | %d | %s | %s | %s | %s | %s |" % (
            ("**%s**" % m.handle) if m.handle == lg.cfg.me else m.handle,
            len(m.players), eur(m.spend), eur(m.proceeds), eur(m.net),
            m.cash.label(), eur(m.max_bid)))
    out += ["",
            "`~` is an estimate: the starting budget less every ledger row, "
            "not an observed balance. The starting squad was dealt free, so "
            "it costs nothing here. A `—` means the ledger overdraws the "
            "budget, so the number would be fiction — see the warnings. Any "
            "time a rival mentions a balance, put it in `inputs/cash.txt` — "
            "one observed number turns their whole estimate into "
            "arithmetic.", ""]

    poor = [m for m in lg
            if m.handle != lg.cfg.me and m.max_bid is not None
            and m.max_bid < 5e6]
    if poor:
        out += ["**Cash-constrained right now:** %s. Against these, open at "
                "the minimum increment — they cannot escalate."
                % ", ".join("%s (%s)" % (m.handle, eur(m.max_bid))
                            for m in poor), ""]
    return out


# ---------------------------------------------------------------------------
# 2. premium curve
# ---------------------------------------------------------------------------

def sec_premium(lg, dl) -> list[str]:
    out = ["## 2. What they pay over value", ""]
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
            d["date"][5:], d["player"], d["actor"], eur(d["price"]),
            eur(d["value"]), mark, pct(d["premium"]),
            "round" if d["round"] else "exact"))
    out += ["", "`~` priced against a snapshot more than %dh away and left "
            "out of the medians." % MAX_LAG_H, ""]
    return out


# ---------------------------------------------------------------------------
# 3. post-buy drift
# ---------------------------------------------------------------------------

def sec_drift(dl, market) -> list[str]:
    out = ["## 3. What happened next", ""]
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


# ---------------------------------------------------------------------------
# 4. squad diagnostics
# ---------------------------------------------------------------------------

def sec_squads(lg, sc, players_by_key) -> list[str]:
    out = ["## 4. Squad diagnostics", "",
           "| Manager | xPts/j | Shape | Trapped | Injured | Thin at | "
           "Unmatched |", "|---|--:|---|--:|--:|---|--:|"]
    detail = []
    for m in lg:
        names = [players_by_key.get(k, {}).get("name", k) for k in m.players]
        scored, missing = sc.score_squad(names)
        best = pick_xi(squad_pool(scored)) if scored else None
        trapped = sum(p.value for p in scored
                      if p.pct_used < TRAPPED_START)
        injured = sum(1 for p in scored if p.status == "injured")

        counts = Counter(p.slot for p in scored if p.slot)
        thin = [SLOT_LABEL[s][:3] for s, need in THIN.items()
                if counts.get(s, 0) < need]

        out.append("| %s | %s | %s | %s | %d | %s | %d |" % (
            ("**%s**" % m.handle) if m.handle == lg.cfg.me else m.handle,
            "%.1f" % best[0] if best else "**illegal**",
            "-".join(str(x) for x in best[1]) if best else "—",
            eur(trapped), injured, ",".join(thin) or "—", len(missing)))

        if not best:
            short = ["%s %d/%d" % (SLOT_LABEL[s], counts.get(s, 0), need)
                     for s, need in SLOT_MIN.items() if counts.get(s, 0) < need]
            detail.append(
                "- **%s cannot field a legal XI** — short at %s. They have to "
                "buy there before the next lock, whatever it costs, which is "
                "the one situation where their premium goes out of the window."
                % (m.handle, ", ".join(short) or "no legal shape"))
        over = [s for s, n in counts.items() if n > MAX_SLOT.get(s, 99)]
        if over:
            detail.append("- %s is carrying more %s than can ever start."
                          % (m.handle,
                             "/".join(SLOT_LABEL[s] for s in over)))

    out.append("")
    if detail:
        out += detail + [""]
    out += ["Trapped is value held in players below %d%% start probability — "
            "money that cannot score. Unmatched is names in their squad "
            "missing from data/tidy, which are absent from the xPts total, so "
            "a large number there means the comparison flatters you."
            % int(TRAPPED_START), ""]
    return out


def short_handle(handle: str, me: bool = False) -> str:
    """A column header that fits a phone screen."""
    return "You" if me else handle.split()[0][:8]


def gain_cell(g, slot: str, snap: dict, max_bid, value,
              me: bool = False) -> str:
    """One cell of the slate matrix.

    `needs`   they cannot field a legal XI and this slot is a hole — they
              are forced buyers here, premium be damned.
    (+x.x)    the gain, but their estimated cash cannot pay even the floor,
              so the demand is real and the threat is not.
    +x.x?     the gain, cash unknown — unknown is not zero, treat as live.
    """
    if snap["best"] is None:
        return "**needs**" if slot in snap["short"] else "—"
    if g is None:
        return "—"
    cell = "%+.1f" % g
    if me:
        return cell
    if max_bid is None:
        return cell + "?"
    if value and max_bid < value:
        return "(%s)" % cell
    return cell


def sec_slate_matrix(lg, sc, by_key, on_offer, snaps) -> list[str]:
    """Every slate player through every manager's eyes."""
    out: list[str] = []
    if not on_offer:
        return out

    free = [r for k, r in by_key.items()
            if k not in lg.owner and norm(r.get("name")) in on_offer]
    cands = [sc.score(r).as_row() for r in free]
    cands = [c for c in cands if c["slot"]]
    if not cands:
        return out

    handles = [m.handle for m in lg]
    max_bid = {m.handle: m.max_bid for m in lg}
    rows = []
    for c in cands:
        cells = {}
        for h in handles:
            snap = snaps[h]
            g = (gain(snap["pool"], c, snap["best"][0])
                 if snap["best"] is not None else None)
            cells[h] = gain_cell(g, c["slot"], snap, max_bid[h],
                                 c["value"], me=(h == lg.cfg.me))
        my = cells[lg.cfg.me]
        try:
            my_sort = float(my.strip("()"))
        except ValueError:
            my_sort = float("-inf")
        rows.append((my_sort, c, cells))
    rows.sort(key=lambda t: -t[0])

    ordered = [lg.cfg.me] + [h for h in handles if h != lg.cfg.me]
    out += ["**The slate through every manager's eyes.** XI gain per "
            "jornada if that manager owned him, under the one shared "
            "scorer. `(…)` = wants him but their estimated cash cannot pay "
            "the floor. `?` = wants him, cash unknown — treat as live. "
            "**needs** = they cannot field a legal XI without buying in "
            "this position — a forced buyer. Your column has no cash cap: "
            "you know your own balance.", "",
            "| Player | Pos | Value | "
            + " | ".join(short_handle(h, h == lg.cfg.me) for h in ordered)
            + " |",
            "|---|---|--:|" + "--:|" * len(ordered)]
    for _, c, cells in rows:
        out.append("| %s | %s | %s | %s |" % (
            c["name"], c["slot"], eur(c["value"]),
            " | ".join(cells[h] for h in ordered)))
    out += ["",
            "Read it as an auction map: a player whose gain is big only in "
            "YOUR column is a quiet buy at the floor; big in a funded "
            "rival's column too means price the bid off their premium in "
            "section 2, or walk.", ""]
    return out


# ---------------------------------------------------------------------------
# 6. projected XIs
# ---------------------------------------------------------------------------

def sec_projected(lg, snaps) -> list[str]:
    me = lg.cfg.me
    my_best = snaps.get(me, {}).get("best")
    my_tot = my_best[0] if my_best else None

    out = ["## 6. Projected XIs", "",
           "Each manager's best legal XI under the same scorer — what a "
           "rational version of them fields. Once jornadas run, their "
           "actual points versus this forecast measures two things at "
           "once: the model's calibration (5× the sample your own squad "
           "gives), and who manages actively versus who set-and-forgets — "
           "a leak worth knowing at deal time.", "",
           "| Manager | ≈pts/j | vs you | Shape | Unmatched |",
           "|---|--:|--:|---|--:|"]

    ranked = sorted(lg, key=lambda m: -(snaps[m.handle]["best"][0]
                                        if snaps[m.handle]["best"] else -1))
    for m in ranked:
        snap = snaps[m.handle]
        best = snap["best"]
        out.append("| %s | %s | %s | %s | %d |" % (
            ("**%s**" % m.handle) if m.handle == me else m.handle,
            "%.1f" % best[0] if best else "**illegal**",
            ("—" if best is None or my_tot is None or m.handle == me
             else "%+.1f" % (best[0] - my_tot)),
            "-".join(str(x) for x in best[1]) if best else "—",
            len(snap["missing"])))
    out += ["",
            "Unmatched names are absent from that manager's total, so a "
            "big number there understates them. Variance in one jornada "
            "dwarfs these gaps; over ten it does not.", ""]

    for m in ranked:
        snap = snaps[m.handle]
        best = snap["best"]
        if best is None:
            out += ["**%s** — cannot field a legal XI (short at %s)."
                    % (m.handle,
                       ", ".join(SLOT_LABEL[sl] for sl in snap["short"])), ""]
            continue
        tot, (d, mm, f), picked = best
        out.append("**%s** — %d-%d-%d · ≈%.0f pts" % (m.handle, d, mm, f, tot))
        for slot in ("POR", "DEF", "MED", "DEL"):
            names = ["%s %.1f%s" % (p["name"], p["score"],
                                    "~" if (p.get("pct") or 100) < 50 else "")
                     for p in picked if p["slot"] == slot]
            if names:
                out.append("- %s: %s" % (slot, " · ".join(names)))
        out.append("")
    out += ["`~` start probability under 50% — the model expects rotation "
            "there, so that is where their real XI will differ from this "
            "one.", ""]
    return out


def log_projections(lg, snaps) -> None:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    rows = []
    for m in lg:
        best = snaps[m.handle]["best"]
        rows.append({
            "observed_at": stamp, "manager": m.handle,
            "formation": ("-".join(str(x) for x in best[1])
                          if best else "illegal"),
            "xpts": "%.2f" % best[0] if best else "",
            "unmatched": len(snaps[m.handle]["missing"]),
            "players": ("|".join("%s:%.2f" % (p["name"], p["score"])
                                 for p in best[2]) if best else ""),
        })
    append_csv(DECISIONS / "rival_xi_log.csv", rows,
               ["observed_at", "manager", "formation", "xpts",
                "unmatched", "players"])


# ---------------------------------------------------------------------------
# 5. demand forecast
# ---------------------------------------------------------------------------

def sec_demand(lg, sc, market_latest, on_offer=None, snaps=None) -> list[str]:
    out = ["## 5. Who wants what", ""]

    rivals_need = rivals_short(lg, sc, market_latest)

    free = [r for k, r in market_latest.items() if k not in lg.owner]
    if on_offer:
        # A slate was pasted, so "who wants what" is only worth asking about
        # players you can actually bid on today. Everyone else is a name to
        # recognise later, and section 5 was the longest table in the report.
        free = [r for r in free if norm(r.get("name")) in on_offer]
        out += ["Restricted to the %d players on today's slate — the rest of "
                "the market is not a decision you can make today." % len(free),
                ""]
    if snaps and on_offer:
        out += sec_slate_matrix(lg, sc, market_latest, on_offer, snaps)

    scored_free = [sc.score(r) for r in free]
    scored_free = [p for p in scored_free if p.slot]
    scored_free.sort(key=lambda p: -p.score)

    cap = None if on_offer else 12
    contested = [] if on_offer else \
        [p for p in scored_free if rivals_need.get(p.slot)][:cap]
    if contested:
        out += ["**Expect competition for these** — the position is one a "
                "rival is short in, so assume a bidding war and price "
                "accordingly.", "",
                "| Player | Pos | Value | Start% | Short here |",
                "|---|---|--:|--:|---|"]
        for p in contested:
            out.append("| %s | %s | %s | %s | %s |" % (
                p.name, p.slot, eur(p.value),
                "—" if p.pct is None else "%.0f%%" % p.pct,
                ", ".join(rivals_need[p.slot])))
        out.append("")

    uncontested = [] if on_offer else \
        [p for p in scored_free if not rivals_need.get(p.slot)][:8]
    if uncontested:
        out += ["**Nobody else needs these.** Same quality, no auction — "
                "take the equivalent player here instead of paying a premium "
                "above.", "",
                "| Player | Pos | Value | Start% |", "|---|---|--:|--:|"]
        for p in uncontested:
            out.append("| %s | %s | %s | %s |" % (
                p.name, p.slot, eur(p.value),
                "—" if p.pct is None else "%.0f%%" % p.pct))
        out.append("")

    mine = lg.managers.get(lg.cfg.me)
    if mine and rivals_need:
        sellable = []
        for k in mine.players:
            r = market_latest.get(k)
            if not r:
                continue
            p = sc.score(r)
            buyers = rivals_need.get(p.slot) or []
            if buyers and p.pct_used < TRAPPED_START:
                sellable.append((p, buyers))
        if sellable:
            out += ["**List these to them.** Players of yours who aren't "
                    "starting, in a position a rival is short in. You stop "
                    "competing with them and start selling to them; price "
                    "just under the premium they showed in section 2.", "",
                    "| Player | Pos | Value | Start% | Short |",
                    "|---|---|--:|--:|---|"]
            for p, buyers in sellable:
                out.append("| %s | %s | %s | %s | %s |" % (
                    p.name, p.slot, eur(p.value),
                    "—" if p.pct is None else "%.0f%%" % p.pct,
                    ", ".join(buyers)))
            out.append("")

    if not on_offer and not contested and not uncontested:
        out += ["_Nothing to forecast: no rival is currently short anywhere, "
                "or the market data hasn't loaded._", ""]
    return out


# ---------------------------------------------------------------------------

def log(lg, dl) -> None:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    rows = [{"observed_at": stamp, "manager": m.handle,
             "players": len(m.players), "spend": "%.0f" % m.spend,
             "proceeds": "%.0f" % m.proceeds,
             "cash": "" if m.cash.value is None else "%.0f" % m.cash.value,
             "cash_confidence": m.cash.confidence} for m in lg]
    append_csv(DECISIONS / "rival_log.csv", rows,
               ["observed_at", "manager", "players", "spend", "proceeds",
                "cash", "cash_confidence"])
    prem = [{"observed_at": stamp, "date": d["date"], "player": d["player"],
             "actor": d["actor"], "side": d["side"],
             "price": "%.0f" % d["price"],
             "value_then": "" if d["value"] is None else "%.0f" % d["value"],
             "lag_h": "" if d["lag_h"] is None else "%.1f" % d["lag_h"],
             "premium_pct": ("" if d["premium"] is None
                             else "%.2f" % d["premium"]),
             "round_bid": "1" if d["round"] else "0"} for d in dl]
    if prem:
        append_csv(DECISIONS / "premium_log.csv", prem, list(prem[0]))


def main() -> None:
    lg = League.load()
    market_rows = load_market()
    if not market_rows:
        REPORTS.mkdir(exist_ok=True)
        write_lines(REPORTS / "rivals.md",
                    ["# League behaviour", "",
                     "No market data. Run `ingest.py parse` first."])
        return

    latest = latest_only(market_rows)
    # The SAME builder report.py uses: same points blend, same fixture
    # board. A rival's XI and yours are only comparable if the arithmetic
    # behind them is one function, not two copies of one.
    sc, (hist_label, cur_label) = build(
        latest, latest_only(load_lineups()),
        dt.datetime.now(dt.timezone.utc))
    by_key = lg.market.latest() if lg.market else {}

    dl = deals(lg, lg.market)
    on_offer, _, _, _ = read_slate(by_key, lg.owner)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    out = ["# League behaviour — %s" % stamp, "",
           "%d managers, %d ledger rows, %d market snapshots%s."
           % (len(lg.managers), len(lg.txns),
              len({r.get("observed_at") for r in market_rows}),
              (", points %s" % "+".join(x for x in (hist_label, cur_label) if x))
              if hist_label else
              ", **no points baseline** (run `ingest.py baseline`)"), ""]
    out += sec_cash(lg)
    out += sec_premium(lg, dl)
    out += sec_drift(dl, lg.market)
    out += sec_squads(lg, sc, by_key)
    snaps = xi_snapshots(lg, sc, by_key)
    out += sec_demand(lg, sc, by_key, on_offer, snaps)
    out += sec_projected(lg, snaps)

    # Kept ABOVE the warnings, not below. digest.py excerpts the warnings
    # into REPORT.md, and a trailing block sitting inside that section would
    # travel with it — arriving in a file where sections 2, 3, 5 and 6 are
    # not printed and the sentence makes no sense.
    out += ["## How much of this to believe", "",
            "Sections 2 and 3 are hypotheses until the sample grows: with "
            "%d ledger rows across %d managers, a median is one or two deals. "
            "Sections 1, 5 and 6 are usable today."
            % (len(lg.txns), len(lg.managers)), ""]

    if lg.warnings:
        out += ["## Ledger warnings", ""] + ["- " + w for w in lg.warnings]
        out.append("")

    REPORTS.mkdir(exist_ok=True)
    write_lines(REPORTS / "rivals.md", out)
    log(lg, dl)
    log_projections(lg, snaps)
    print("%d deals priced, %d managers" % (len(dl), len(lg.managers)))


def _selftest() -> None:
    assert short_handle("Magic Mike 333") == "Magic"
    assert short_handle("BurtonGM89") == "BurtonGM"
    assert short_handle("anything", me=True) == "You"

    legal = {"best": (30.0, (4, 4, 2), []), "short": []}
    broke = {"best": None, "short": ["POR"]}
    # Funded and legal: the gain, plain.
    assert gain_cell(2.34, "MED", legal, 50e6, 10e6) == "+2.3"
    # Wants him but cannot pay the floor.
    assert gain_cell(2.34, "MED", legal, 5e6, 10e6) == "(+2.3)"
    # Unknown cash is flagged, not hidden — unknown is not zero.
    assert gain_cell(2.34, "MED", legal, None, 10e6) == "+2.3?"
    # Your own column carries no cash cap and no question mark.
    assert gain_cell(-0.4, "MED", legal, None, 10e6, me=True) == "-0.4"
    # Cannot field an XI: forced buyer in the hole, dash elsewhere.
    assert gain_cell(None, "POR", broke, 1e6, 10e6) == "**needs**"
    assert gain_cell(None, "DEF", broke, 1e6, 10e6) == "—"
    # Can never start for a legal squad.
    assert gain_cell(None, "MED", legal, 50e6, 10e6) == "—"
    print("rivals.py selftest OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        main()
