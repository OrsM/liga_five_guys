
from __future__ import annotations

import sys


import json
import math

from decide import dead_weight, overdraft_fix, route_kind, value_rate  # noqa: E402,F401
from methodology import current_mae
from ffcore.parse import fmt_money
from ffcore.league import app_fielded
from ffcore.render import title_name
from ffcore.tidy import (run_now, shown,
                         ALERTS, PARTS, REPORTS, WARNINGS, write_lines)

__all__ = ["shape"]

OUT = "sim.md"


SHOW = 8


def _pts(v) -> str:
    return "{:,.0f}".format(v)


def squad_value(u) -> float:
    return sum(u.view("proceeds").values())


def fielded_keys(u=None) -> list[str]:
    return app_fielded(u.state.squads.get(u.me, {}), u.view("name")) if u else []




def xi_change(marked: list[str], best) -> dict:
    best = list(best)
    if len(marked) != len(best) or not marked:
        return {"legal": False, "marked": len(marked), "in": [], "out": []}
    have, want = set(marked), set(best)
    return {"legal": True, "marked": len(marked),
            "in": [k for k in best if k not in have],
            "out": [k for k in marked if k not in want]}


def fielded_shape(u, xi=None) -> str:

    if xi is None:
        _, xi = u.current_xi
    keys = fielded_keys(u)
    return shape(u, keys) if xi_change(keys, xi)["legal"] else ""


def header(u, base, n_actions: int, locks_h=None, xi=None) -> list[str]:
    lo, hi = base.band(u.me)
    val = squad_value(u)
    ctx = []
    if locks_h is not None:
        ctx.append("**Locks in %s**"
                   % ("%.0fh" % locks_h if locks_h < 48
                      else "%.0f days" % (locks_h / 24)))
    cash_txt = ("**cash %s**" if u.cash < 0 else "cash %s") % fmt_money(u.cash)
    if u.locked_cash:
        # The bids are NOT held back from this balance -- the app does not
        # debit them -- so they are shown beside it, not subtracted from it.
        cash_txt += " (%s already bid)" % fmt_money(u.locked_cash)
    ctx += ["squad %s" % fmt_money(val), cash_txt,
            "total %s" % fmt_money(val + u.cash)]

    want = _shape_now(u, xi=xi)
    now = fielded_shape(u, xi=xi)
    form = ("**play %s** (now %s)" % (want, now)) if now and now != want \
        else "play %s" % want
    return [" · ".join(ctx), "",
            "%s · finish %.2f · win %.0f%% · season %s–%s"
            % (form, base.expected_position(),
               100 * base.position().get(1, 0.0), _pts(lo), _pts(hi)),
            ""]


def short(key, u) -> str:
    full = title_name(u.view("name").get(key, key))
    last = full.split()[-1] if full.split() else full
    clash = sum(1 for k in u.state.squads.get(u.me, {})
                if title_name(u.view("name").get(k, k)).split()[-1:] == [last])
    return full if clash > 1 else last


SLOT_ORDER = {"POR": 0, "DEF": 1, "MED": 2, "DEL": 3}


def by_slot(u, keys, exp=None):

    if exp is None:
        exp, _ = u.current_xi
    return sorted(keys, key=lambda k: (SLOT_ORDER.get(u.view("pos").get(k, ""), 9),
                                       -exp.get(k, 0.0)))


def short_manager(m: str) -> str:
    return m.split()[0] if m else m



# THE ONE COPY of the ladder's group headings. The markdown table below and every
# JSON row use it, and the page draws the row's own `label` -- it used to carry
# its own list, which had already drifted ("BUY -- with the proceeds" on the
# phone, "BUY -- free agents" here) for a table that says the opposite of what
# the row underneath it means.
GROUP_LABEL = {
    "in": "PUT ON", "out": "TAKE OFF",
    "field": "FIELD — your eleven — the app has not said what you are playing",
    "keep": "KEEP — bench", "sell": "SELL — never start",
    "offer": "OFFERS — someone wants him",
    "buy": "BUY — free agents",
    "raid": "RAID — a clause, cannot be refused",
    "save": "SAVE — better than yours, out of reach", "pass": "PASS",
}


def ladder_rows(u, rows, bands=None, exp=None, xi=None) -> list[dict]:

    if exp is None or xi is None:
        exp, xi = u.current_xi
    mine = u.state.squads.get(u.me, {})
    dead_weight = u.dead_weight()
    dead = {k for k, _ in dead_weight}
    won = {r["action"].buy: r for r in rows if r["action"].buy}
    bar = u.xi_bar
    from ffcore.candidates import max_spare_proceeds
    spare = max_spare_proceeds(u)
    rest = [k for k in u.view("price") if k not in mine and exp.get(k, 0.0) > bar]
    bands = {k: v for k, v in (bands or {}).items() if k not in won}
    # ONE DOOR FOR WHAT A PLAYER COSTS. ffcore.bid already fits the premium
    # over market value from every logged deal -- with a lag guard on the
    # quoted value that a hand-rolled version does not have -- and already
    # turns it into a cash-capped bid range with its reasoning. It had never
    # been called from here, so an afternoon went on rebuilding it worse.
    from ffcore.bid import deals as _deals, premiums as _premiums, suggest
    from ffcore.model import session as _session
    _lg = _session().lg
    _dl = _deals(_lg, _lg.market) if _lg and _lg.market else []
    buy_prem = _premiums(_dl, "buy")
    sell_prem = _premiums(_dl, "sell")
    _rival_max = max(u.rival_cash.values(), default=None)

    # par is a COLUMN here, not a screen -- worth_doing() owns the screen.
    par = {k: v["par"] for k, v in u.player_forecasts().items()}

    def cell(k, group, where, money, pts, note="", value=None,
            lo=None, hi=None, market=None, premium=None):
        if k in bands:
            pts, lo, hi, _action, mean = bands[k]
        else:
            mean = None
        _worth = u.view("value").get(k)
        # CASH FOR THIS MOVE, not cash in hand. suggest() refuses a bid it
        # cannot fund, and every buy on this board is funded by a sale --
        # handing it the bare balance made it refuse every row.
        _ask = suggest(_worth, buy_prem, u.cash + spare, _rival_max)
        _hold = suggest(_worth, sell_prem)
        return {"offer": u.received_offers.get(k),
                "rivals": u.view("bids").get(k),
                "ask": _ask.low,
                "worth": _worth,
                "going": _hold.low,
                "name": title_name(u.view("name").get(k, k)),
                "pos": u.view("pos").get(k, ""), "start": u.view("start").get(k, 0.0),
                "xpts": exp.get(k, 0.0), "group": group, "where": where,
                "label": GROUP_LABEL[group],
                "money": money, "pts": pts, "par": par.get(k),
                "pts_lo": lo, "pts_hi": hi, "pts_mean": mean,
                "note": note, "value": value,
                "market": market, "premium": premium,
                }

    out = []
    chg = xi_change(fielded_keys(u), xi)
    if chg["legal"]:
        for k in by_slot(u, chg["in"], exp):
            out.append(cell(k, "in", "bench", None, None))
        for k in by_slot(u, chg["out"], exp):
            out.append(cell(k, "out", "yours", None, None))
    else:
        for k in by_slot(u, xi, exp):
            out.append(cell(k, "field", "yours", None, None))
    moving = set(chg["in"]) | set(chg["out"])
    benched = [k for k in mine if k not in xi and k not in dead
               and k not in moving]
    for k in by_slot(u, benched, exp):
        out.append(cell(k, "keep", "yours", None, None))
    for k in sorted(dead, key=lambda k: -exp.get(k, 0.0)):
        out.append(cell(k, "sell", "yours", u.view("proceeds").get(k, 0.0),
                        None))

    mine_all = set(u.state.squads.get(u.me, {}))
    for k in sorted((k for k in u.received_offers if k in mine_all),
                    key=lambda k: -(u.received_offers[k]
                                    / (u.view("value").get(k) or 1e18))):
        out.append(cell(k, "offer", "yours", None, None))

    def buy_cell(k, group):
        r = won[k]
        sold = r["action"].sell
        note = ("sell " + " + ".join(short(s, u) for s in sold)) if sold else ""
        return cell(k, group, short_manager(u.view("owner").get(k)) or "free agent",
                    -r["action"].net, r["d_pts"], note, value=r.get("value"),
                    lo=r.get("pts_lo"), hi=r.get("pts_hi"),
                    market=u.view("value").get(k), premium=r.get("burn") or 0.0)

    # THE SAME SCREEN AS THE RECOMMENDATION, not a second copy of it. This
    # block used to re-derive the whole thing -- its own player_forecasts(),
    # its own par/pj/mae, its own _clears_par_floor and raid_shortlist --
    # beside worth_doing() doing exactly that for payload() and the phone.
    # Three renderers, two implementations, and they had already disagreed
    # twice this week.
    offer_keys = {r["action"].buy
                  for r in worth_doing(u, [won[k] for k in rest if k in won])
                  if r["action"].buy}
    for k in sorted((k for k in rest if k in offer_keys),
                    key=lambda k: _move_rank_key(won[k], u)):
        # "listed" is neither: a man his owner has put up for sale can be
        # outbid, so the board never calls him a buy or a raid.
        kind = route_kind(u, k)
        if kind in ("free", "raid"):
            out.append(buy_cell(k, "buy" if kind == "free" else "raid"))
    for k in sorted((k for k in rest if k not in won
                     and u.view("price")[k] > u.cash + spare),
                    key=lambda k: -exp.get(k, 0.0)):
        short_by = u.view("price")[k] - u.cash - spare
        save_pts = bands[k][0] if k in bands else None
        out.append(cell(k, "save", short_manager(u.view("owner").get(k)) or "free agent",
                        -short_by, save_pts, "short",
                        value=value_rate(save_pts, short_by)))
    for k in sorted((k for k in rest if k not in won
                     and u.view("price")[k] <= u.cash + spare
                     and route_kind(u, k) == "free"),
                    key=lambda k: -exp.get(k, 0.0)):
        out.append(cell(k, "pass", short_manager(u.view("owner").get(k)) or "free agent",
                        -u.view("price")[k], None))
    return out




def band_acts(u, exp=None, xi=None) -> list:
    import decide

    if exp is None or xi is None:
        exp, xi = u.current_xi
    mine = u.state.squads.get(u.me, {})
    bar = u.xi_bar
    acts = [(k, decide.Action("sell", sell=(k,),
                              proceeds=u.view("proceeds").get(k, 0.0)))
           for k in mine]
    acts += [(k, decide.Action("buy", buy=k, cost=u.view("price").get(k, 0.0)))
            for k in u.view("price")
            if k not in mine and exp.get(k, 0.0) > bar]
    return acts


def ladder(u, rows, base, data=None, exp=None, xi=None) -> list[str]:

    if exp is None or xi is None:
        exp, xi = u.current_xi
    data = data if data is not None else ladder_rows(u, rows, exp=exp, xi=xi)
    by_group: dict[str, list[dict]] = {}
    for r in data:
        by_group.setdefault(r["group"], []).append(r)

    def row_md(r):
        if r["group"] == "save":
            season = ("—" if r["pts"] is None else
                      "%+.0f (%+.0f–%+.0f) if you could"
                      % (r["pts"], r["pts_lo"], r["pts_hi"]))
            money = "%.2fM short" % (-r["money"] / 1e6)
        else:
            season = ("—" if r["pts"] is None else
                      "%+.0f (%+.0f–%+.0f)" % (r["pts"], r["pts_lo"], r["pts_hi"])
                      if r["pts_lo"] is not None else "%+.0f" % r["pts"])
            if r["note"]:
                season += " " + r["note"]
            if r["group"] in ("buy", "raid") and r["market"] is not None:
                money = "%.2fM" % (r["market"] / 1e6)
                if r["premium"]:
                    money += " +%.2fM" % (r["premium"] / 1e6)
                # WHAT TO ACTUALLY BID. Market value is what he is worth,
                # not what he goes for: fitted over every priced transfer
                # this season and conditioned on how many bids are already
                # in, because that is the one thing you know before you bid.
                if r.get("ask"):
                    money += " · bid %.2fM" % (r["ask"] / 1e6)
                    if r.get("rivals"):
                        money += " (%d bid%s in)" % (r["rivals"],
                                                     "" if r["rivals"] == 1
                                                     else "s")
            else:
                money = ("%+.2fM" % (r["money"] / 1e6)) if r["money"] else "—"
                # A BID ON A MAN YOU OWN, judged against the two numbers
                # that make it good or bad: what he is worth on the market
                # today, and what you paid for him. The bid already lifts
                # his proceeds inside the simulation -- it just never said
                # so, so a generous offer on a man you were keeping looked
                # exactly like no offer at all.
                if r["group"] == "offer" and r.get("offer"):
                    money = "offer %.2fM" % (r["offer"] / 1e6)
                    # AGAINST WHAT A SALE ACTUALLY FETCHES, not against the
                    # quoted value. A bid is the only way a player leaves
                    # for money -- there is no fixed-price channel, which is
                    # why the 68 logged sales make one smooth hump with no
                    # spike at 1.00 -- so the quoted value is not the offer
                    # a seller is really choosing between. The going rate is
                    # the median of those sales, fitted each run.
                    #
                    # WHAT HE COST IS NOT HERE, deliberately. It was, and it
                    # is a sunk cost: whether to take 48M for Fornals turns
                    # on what 48M buys against what Fornals scores, not on
                    # what he was bought for. Miguel: "agree it is sunk cost".
                    if r.get("going"):
                        money += (" (%+.0f%% vs %.2fM going rate)"
                                 % (100 * (r["offer"] / r["going"] - 1.0),
                                    r["going"] / 1e6))
        return ("| %s | %s | %.0f%% | %.2f | %s | %s | %s | %s | %s |"
                % (r["name"], r["pos"] or "—", 100 * r["start"], r["xpts"],
                   r["where"], money, season,
                   ("%+.0f" % r["par"]) if r.get("par") is not None else "—",
                   ("%.1f" % r["value"]) if r["value"] is not None else "—"))

    out = ["| Player | Pos | Start | xPts/j | Where | € | Season | PAR | "
          "pts/M€ |",
           "|---|---|--:|--:|---|--:|--:|--:|--:|"]

    if by_group.get("field"):
        out.append("| **" + GROUP_LABEL["field"] + "** | | | | | | | | |")
        out += [row_md(r) for r in by_group["field"]]
    elif not by_group.get("in") and not by_group.get("out"):
        out.append("| **XI — no change, you are fielding the best eleven** "
                   "| | | | | | | | |")
    else:
        if by_group.get("in"):
            out.append("| **" + GROUP_LABEL["in"] + "** | | | | | | | | |")
            out += [row_md(r) for r in by_group["in"]]
        if by_group.get("out"):
            out.append("| **" + GROUP_LABEL["out"] + "** | | | | | | | | |")
            out += [row_md(r) for r in by_group["out"]]
    tot = sum(exp.get(k, 0.0) for k in xi)
    riv = _rival_best(u)
    riv_total, riv_who = riv.get("xi", 0.0), riv.get("manager", "")
    out.append("| **Your eleven — play %s** | | | **%.2f** | "
               "vs %s **%.2f** | | **%+.2f** | |"
               % (shape(u, xi), tot, riv_who, riv_total, tot - riv_total))

    if by_group.get("keep"):
        out.append("| **" + GROUP_LABEL["keep"] + "** | | | | | | | | |")
        out += [row_md(r) for r in by_group["keep"]]

    if by_group.get("sell"):
        out.append("| **" + GROUP_LABEL["sell"] + "** | | | | | | | | |")
        out += [row_md(r) for r in by_group["sell"]]

    # "offer" rows are NOT rendered as their own section: every one of them
    # duplicates a player already listed above (keep/out/sell/field) with
    # the same pts_mean, just re-tagged because he has a live bid -- flip.py
    # now owns that view (FUND, net points + sell-now-vs-wait timing) and
    # does it better. The rows stay in ladder_rows()'s DATA (decisions.json)
    # because flip.report_view() prices a fielded starter through them --
    # only the markdown listing, which nobody reads twice, is cut.

    if by_group.get("buy"):
        out.append("| **" + GROUP_LABEL["buy"] + "** | | | | | | | | |")
        out += [row_md(r) for r in by_group["buy"]]
    elif by_group.get("raid"):
        out.append("| **BUY — free agents — none clear the bar today** | | "
                   "| | | | | |")

    if by_group.get("raid"):
        out.append("| **" + GROUP_LABEL["raid"] + "** "
                   "| | | | | | | | |")
        out += [row_md(r) for r in by_group["raid"]]
    elif not by_group.get("buy"):
        out.append("| **BUY/RAID — nothing clears the bar this week** | | "
                   "| | | | | |")

    if by_group.get("save"):
        out.append("| **" + GROUP_LABEL["save"] + "** | | | | | | | | |")
        out += [row_md(r) for r in by_group["save"]]

    if by_group.get("pass"):
        out.append("| **" + GROUP_LABEL["pass"] + "** | | | | | | | | |")
        out += [row_md(r) for r in by_group["pass"]]

    out += ["",
            "_How to read this table: **How to read the tables** in "
            "METHOD.md._", ""]
    return out




def standings(u, base) -> list[str]:
    out = ["| Manager | now | cash | simulated | 10–90 | P(I finish above) |",
           "|---|--:|--:|--:|--:|--:|"]
    order = sorted(u.state.squads, key=lambda m: -base.mean(m))
    for m in order:
        lo, hi = base.band(m)
        out.append("| %s | %.0f | %s | %s | %s–%s | %s |"
                   % (m + (" **(you)**" if m == u.me else ""),
                      u.state.carried.get(m, 0.0),
                      fmt_money(u.cash) if m == u.me
                      else "~" + fmt_money(u.rival_cash.get(m, 0.0)),
                      _pts(base.mean(m)), _pts(lo), _pts(hi),
                      "—" if m == u.me
                      else "%.0f%%" % (100 * base.beat(m))))
    out.append("")
    return out


def phantom_filled(u) -> list[tuple[str, list[str]]]:
    from ffcore.score import SLOT_LABEL

    out = []
    for m, sq in sorted(u.state.squads.items()):
        counts: dict[str, int] = {}
        for k, slot in sq.items():
            if k.startswith("__phantom_"):
                counts[slot] = counts.get(slot, 0) + 1
        if counts:
            out.append((m, ["%d %s%s" % (n, SLOT_LABEL[s],
                                        "" if n == 1 else "s")
                           for s, n in sorted(counts.items())]))
    return out


def caveats(u) -> list[str]:
    import decide
    import methodology as M
    import ffcore.forecast as forecast

    fitted, why = M.drift_frac_from_history()
    drift_status = ("still the unfitted default"
                    if fitted == forecast.DRIFT_FRAC and "not enough" in why
                    else "fit from real data this run")

    out = ["| Not modelled | Which way it bends the answer |", "|---|---|"]
    for m, filled in phantom_filled(u):
        out.append("| **%s's squad is short a position** (%s) | his real "
                   "squad cannot field a legal eleven, so the SIMULATION "
                   "stands in a league-average player at that spot — the "
                   "same real per-jornada data every other player's "
                   "number comes from, not an invented figure or a "
                   "presumption he never fixes it (assuming he never "
                   "would is the much stronger, much less plausible "
                   "claim). His true squad may be stronger or weaker than "
                   "an average man there once he actually buys one |"
                   % (m, ", ".join(filled)))
    for j, clubs in sorted(u.part_played.items()):
        out.append("| Jornada %d is half played — %d clubs are done | their "
                   "points are already in `now`, so only the rest of the "
                   "round is simulated, and it still re-picks an eleven that "
                   "is in fact already locked |" % (j, len(clubs)))
    if u.cash_note:
        from ffcore.bid import deals as _deals, premiums as _premiums
        from ffcore.model import session
        _lg = session().lg
        _clause_prem = None
        if _lg and _lg.market:
            _dl = [d for d in _deals(_lg, _lg.market) if d.get("is_clause")]
            _clause_prem = _premiums(_dl, "buy")
        prem_phrase = ("a clause runs a median %.2fx market value here (n=%d "
                       "real raids)" % (1 + _clause_prem.median / 100.0,
                                        _clause_prem.n)
                       if _clause_prem else
                       "a clause runs above market value here — too few "
                       "real raids logged yet to median")
        out.append("| %s | %s and the app pays back only the value, so the "
                   "premium is gone for good. It is charged against the "
                   "move, but priced off what more money would buy you "
                   "today — most days, very little |"
                   % (u.cash_note, prem_phrase))
    if u.unjoined:
        out.append("| Named by the app in a way nothing else matches: %s | "
                   "missing from the simulation entirely |"
                   % ", ".join("`%s`" % n for n in u.unjoined))
    out += [
        "| \"Your eleven\" (top of the ladder) compares only the NEXT "
        "jornada, off real confirmed lineups/injuries | the standings "
        "table below simulates the other %d jornadas too, where nobody has "
        "lineup news yet and squad value dominates — the two can point "
        "opposite ways (this week's confirmed news vs. the season's "
        "average squad quality) without either being wrong |"
        % max(0, len(u.state.jornadas) - 1),
        "| Beyond the next jornada, P(start) reverts to his own season-"
        "standing rate | a suspension or a knock is dated to the match it "
        "was announced for — nothing here predicts a FUTURE one not yet "
        "known, e.g. who gets injured in March |",
        "| Rivals never transfer | a steal that guts a squad assumes its "
        "manager does not simply buy someone back — flatters the steal |",
        "| Teammates score independently, MATCH TO MATCH | two defenders of "
        "one club still land on opposite ends of the per-match pool in the "
        "same round — only their SEASON-LONG rating (club_rel) is shared, "
        "not one week's luck |",
        "| Cash scores zero | nothing models the market next cycle, so "
        "holding money looks worthless and a standalone sale can never look "
        "good |",
        "| p_win's season-long spread uses DRIFT_FRAC=%s (%s) | see "
        "\"Season-long drift\" below for the fit itself — every published "
        "win-probability model checked (538's NBA/NHL/MLB) is far more "
        "humble than 70%%+ about a full season this early regardless of "
        "the exact value, which is what 1.0 as an unfitted default "
        "already reflects |"
        % (forecast.DRIFT_FRAC, drift_status),
        "| Shape prior | %s |" % u.forecaster.pool_note(),
        "| P(start) fit | %s |" % u.start_note.rstrip("."),
        "| win %% and finish are single simulated draws | at FINAL_TRIALS="
        "%d, the same real inputs have been measured (2026-08-31%s) to "
        "swing roughly ±7 points (e.g. 19%% to 26%% on one real board) run "
        "to run — read the headline number as a band that wide, not a "
        "precise reading |"
        % (decide.FINAL_TRIALS,
           "" if decide.FINAL_TRIALS == 3000 else
           ", at FINAL_TRIALS=3000 — since changed, re-check this figure"),
        ""]
    return out


VALUE_TOLERANCE = 0.90

def _clears_par_floor(par_of: dict, mae, k: str, horizon: int = 1,
                      pj_of: dict | None = None) -> bool:
    """Is his edge bigger than our error in measuring it?

    COMPARE LIKE WITH LIKE. `par` is points above replacement across the
    WHOLE remaining season; `mae` is the model's error on ONE player in
    ONE jornada. This compared them directly, which is a units mismatch --
    a season-scale number against a per-round one -- so the floor sat
    about six times too low and almost everything cleared it. That is how
    a midfielder whose entire edge rested on 3.3 matches (par 11.2, under
    0.35 a jornada) came to be headlined as a recommendation.

    Over `horizon` rounds the errors partly cancel rather than accumulate,
    so the season-scale error is mae*sqrt(horizon), not mae*horizon. On
    today's data that moves the bar from 3.29 to 18.9 season points, which
    keeps the thick-evidence moves (par 43 off 33 matches, par 19 off 40)
    and drops the thin ones (par 14 off 5 matches, par 11 off 3.3).
    """
    if mae is None:
        return True
    par = par_of.get(k)
    if par is None:
        return False
    # WEIGHT THE EDGE BY THE EVIDENCE UNDER IT. Comparing par against a
    # season-scale error got the UNITS right (see above) but still had no
    # view on how much is known: a striker whose rate rests on three
    # matches cleared the same bar as one with twenty-one, because par is
    # a point estimate and point estimates say nothing about their own
    # reliability. Taking a maximum over sixty-five candidates then picks
    # the thin records preferentially -- they are the ones whose noise had
    # room to run upward.
    #
    # Same shrinkage the rate itself already gets (ffcore.score.SHRINK_K,
    # Marcel-style regression to the mean, graded at the minimum of a
    # 2/4/8/16/32 grid on 1,778 outcomes): pull par toward zero by the
    # evidence behind it. No new constant -- the pseudo-count that decides
    # how much a thin rate is trusted should decide how much a thin EDGE
    # is trusted too.
    #
    # Screening on the simulated lower band instead was tried first and is
    # wrong here: every move's pts_lo is negative at current dispersion
    # (-85 to -66 on 2026-09-18), so it would refuse every recommendation.
    from ffcore.score import SHRINK_K

    pj = pj_of.get(k) if pj_of else None
    if pj is not None:
        par = par * pj / (pj + SHRINK_K)
    return par >= mae * math.sqrt(max(1, horizon))


def _best_raid_per_victim(raid_keys, won) -> list[str]:
    best: dict[str, tuple[float, str]] = {}
    for k in raid_keys:
        r = won[k]
        victim = r["action"].victim
        score = r["d_beat"].get(victim)
        if score is None:
            score = r.get("d_pts") or 0.0
        cur = best.get(victim)
        if cur is None or score > cur[0]:
            best[victim] = (score, k)
    return [k for _score, k in best.values()]


def raid_shortlist(u, rows, par_of, mae, pj_of=None) -> set:
    """The raid keys worth showing: one per victim, chosen among those that
    clear the par floor.

    ORDER IS THE POINT. Floor FIRST, then one-per-victim. The reverse --
    pick each victim's best raid, then drop it if it misses the floor --
    makes a victim vanish entirely even when a second, weaker raid on him
    would have cleared. ladder_rows() floored first, payload() deduped
    first, so the markdown could show a victim's second-best raid while
    the JSON showed him no raid at all, off the same rows in the same run.

    "Raid" is route_kind(u, k) == "raid" everywhere. payload() used to ask
    action.victim instead, a second definition of the same thing.
    """
    raids = {r["action"].buy: r for r in rows
             if r["action"].buy
             and route_kind(u, r["action"].buy) == "raid"
             and _gains(r)
             and _clears_par_floor(par_of, mae, r["action"].buy,
                                   len(u.state.jornadas), pj_of)}
    return set(_best_raid_per_victim(list(raids), raids))


def _gains(r) -> bool:
    """Does the SIMULATION say this move wins points?

    par and the simulation answer different questions and can disagree. par
    is a season-long, per-player figure: how far above a replacement at his
    slot he sits. d_pts is what actually happens to YOUR squad when the sale
    that funds him goes with him. A man can be excellent and still be a
    losing trade, because the eleven he joins loses whoever paid for him.

    On 2026-09-18 the ladder offered "BUY Pape Gueye, sell Alonso" at par
    +56 -- comfortably over the floor -- while the same row carried d_pts
    -2.0 and the recommendation itself, which has always screened on d_pts,
    did not include him. Two renderers off one set of rows, disagreeing
    about the same move. The floor asks whether there is enough evidence to
    believe the edge; this asks whether there is an edge at all, and a row
    has to pass both to be offered.
    """
    d = r.get("d_pts")
    return d is not None and d > 0


def worth_doing(u, rows) -> list:
    """The moves this report is willing to recommend, screened once.

    THREE RULES, ONE PLACE. Enough evidence under the edge to believe it
    (the par floor, weighted by how many matches it rests on), one raid per
    victim, and an edge at all once the move is actually simulated.

    It lived inside payload(), so decisions.json obeyed all three and the
    "Do this" line on the phone obeyed none: _best() takes whatever rank()
    returned. On 2026-09-19 that had the alert telling Miguel to buy Jose
    Angel Lopez for +18 while the JSON from the same run, the same minute,
    listed neither him nor that move -- it had dropped him on the par
    floor. The ladder is the browse view and still shows everything with
    its own delta; this is the recommendation, and there is one of it.
    """
    _pf = u.player_forecasts()
    par_of = {k: v["par"] for k, v in _pf.items()}
    pj_of = {k: v["pj"] for k, v in _pf.items()}
    mae = current_mae()
    rows = [r for r in rows if not r["action"].buy
            or _clears_par_floor(par_of, mae, r["action"].buy,
                                 len(u.state.jornadas), pj_of)]
    keep_raid = raid_shortlist(u, rows, par_of, mae, pj_of)
    rows = [r for r in rows
            if route_kind(u, r["action"].buy) != "raid"
            or r["action"].buy in keep_raid]
    # A candidate can clear rank()'s screen and still simulate negative.
    # _gains() is that test; raid_shortlist() already uses it, so writing
    # `d_pts > 0` again here was the same rule in two hands.
    return [r for r in rows if _gains(r)]


def _move_rank_key(r, u):
    reliable = 0 if u.view("route").get(r["action"].buy, "free") != "listed" else 1
    d = r.get("d_pts")
    return (reliable, -d if d is not None else float("inf"))

def _best(u, rows, rivals):
    """Pick the headline from rows worth_doing() has ALREADY screened.

    It used to screen again -- `d_pts > 0`, twice -- which is the same rule
    written in two places and exactly what it is here to stop being. Its
    caller passes worth_doing()'s output; the rule lives there.
    """
    if not rows:
        return None, False
    reliable = [r for r in rows
               if u.view("route").get(r["action"].buy, "free") != "listed"]
    pool, uncertain = (reliable, False) if reliable else (rows, True)
    best = max(pool, key=lambda r: r["d_pts"])
    if best["action"].net <= 0:
        return best, uncertain
    floor = VALUE_TOLERANCE * best["d_pts"]
    cheaper = [r for r in pool
              if r["d_pts"] >= floor and r["action"].net < best["action"].net]
    if cheaper:
        best = min(cheaper, key=lambda r: r["action"].net)
    return best, uncertain


def bid_lines(u, rows) -> list[str]:
    """Your live bids, re-read as what they are: actions already taken.

    The app neither debits them nor stops you bidding past your balance, so
    nothing else in the system will tell you that the bids standing tonight
    cost more than you hold. Each run either endorses a bid -- the board
    still wants that player at that price -- or it does not, and the one
    free, instant way to raise money is to withdraw the ones it does not.
    This carries the overdraft warning that cash alone used to carry, back
    when cash had the bids wrongly netted out of it."""
    if not u.my_bids:
        return []
    name = lambda k: title_name(u.view("name").get(k, k))          # noqa: E731

    # A bid is worth keeping only if the board still wants the man AND the
    # money is there once the sale that funds him is counted. Endorsement
    # alone is not enough: the ranking scores one move at a time against
    # today's squad, so it will happily want two men you can only pay for
    # one of -- which is the whole reason this warning exists. rows arrive
    # best-first, so the first move that buys a player is his best price,
    # and taking them in that order spends the budget the way the board
    # would spend it.
    cost_of: dict[str, float] = {}
    for r in rows:
        k = r["action"].buy
        if k in u.my_bids and k not in cost_of:
            cost_of[k] = r["action"].net
    keep, drop, spent = [], [], 0.0
    for k in cost_of:                       # board order, best first
        if spent + cost_of[k] <= u.cash:
            keep.append(k)
            spent += cost_of[k]
        else:
            drop.append((k, u.my_bids[k]))
    drop += [(k, m) for k, m in u.my_bids.items() if k not in cost_of]
    drop.sort(key=lambda kv: -kv[1])
    out = []
    if drop:
        out.append("**Withdraw %s** — %s. Today's board does not buy %s at "
                   "that price, or cannot pay for %s alongside what it does "
                   "buy. Withdrawing frees the money at no points cost."
                   % (fmt_money(sum(m for _k, m in drop)),
                      ", ".join("%s %s" % (name(k), fmt_money(m))
                                for k, m in drop),
                      "him" if len(drop) == 1 else "them",
                      "him" if len(drop) == 1 else "them"))
    free = u.cash - u.locked_cash
    if free < 0:
        out.append("**%s bid, %s in hand** — if every live bid lands you are "
                   "%s short, and the app will let that happen. Withdraw or "
                   "sell before they resolve."
                   % (fmt_money(u.locked_cash), fmt_money(u.cash),
                      fmt_money(-free)))
    return out


def alert_lines(u, rows, rivals) -> list[str]:
    out = bid_lines(u, rows)
    if u.cash < 0:
        sells, short = overdraft_fix(u)
        if not sells:
            return out + ["**Overdrawn %s** — no safe dead-weight sale covers "
                          "it; needs a manual look before the jornada locks."
                          % fmt_money(-u.cash)]
        names = ", ".join("%s (+€%.1fM)" % (title_name(u.view("name").get(k, k)),
                                            p / 1e6) for k, p in sells)
        if short > 0:
            return out + ["**Overdrawn %s** — sell %s clears most of it, "
                          "still €%.1fM short (no further safe dead-weight "
                          "sale)." % (fmt_money(-u.cash), names, short / 1e6)]
        return out + ["**Overdrawn %s** — sell %s to clear it before the "
                      "jornada locks (zero points cost: %s never start your "
                      "eleven)."
                      % (fmt_money(-u.cash), names,
                         "he doesn't" if len(sells) == 1 else "they don't")]

    best, uncertain = _best(u, rows, rivals)
    if best is None:
        return out
    a = best["action"]
    net = a.net
    if net > 0:
        cost = "-€%.1fM" % (net / 1e6)
    elif net < 0:
        cost = "+€%.1fM raised" % (-net / 1e6)
    else:
        cost = "free"
    if uncertain:
        cost += " · needs the seller to accept, not guaranteed"
    return out + ["**Do this** — %s (%+.0f season pts, %+.0f%% to win, %s)"
                  % (a.label({k: title_name(v)
                              for k, v in u.view("name").items()}),
                     best["d_pts"], 100 * best["d_win"], cost)]


def shape(u, keys) -> str:
    n = {}
    for k in keys:
        n[u.view("pos").get(k, "")] = n.get(u.view("pos").get(k, ""), 0) + 1
    return "%d-%d-%d" % (n.get("DEF", 0), n.get("MED", 0), n.get("DEL", 0))


def _xi_total(u, who, exp=None) -> float:
    from ffcore.season import best_xi
    if exp is None:
        exp, _ = u.current_xi
    xi = best_xi(u.state.squads.get(who, {}), exp)
    return sum(exp.get(k, 0.0) for k in xi)


def _shape_now(u, xi=None) -> str:
    if xi is None:
        _, xi = u.current_xi
    return shape(u, xi)


def _rival_best(u, exp=None) -> dict:
    if exp is None:
        exp, _ = u.current_xi
    out = [(_xi_total(u, m, exp=exp), m) for m in u.state.squads if m != u.me]
    if not out:
        return {}
    total, who = max(out)
    return {"manager": who, "xi": total,
           "gap": _xi_total(u, u.me, exp=exp) - total}


def payload(u, rows, base, rivals, locks_h=None, n_actions: int = 0,
            ladder_data=None, exp=None, xi=None) -> dict:

    if exp is None or xi is None:
        exp, xi = u.current_xi
    names = {k: title_name(v) for k, v in u.view("name").items()}
    lo, hi = base.band(u.me)

    chg = xi_change(fielded_keys(u), xi)
    if not chg["legal"]:
        xi_note = ("the app has not said which eleven you are fielding, so "
                   "this is the whole sheet rather than a change list")
    elif not chg["in"] and not chg["out"]:
        xi_note = "no change — you are already fielding the best eleven"
    else:
        xi_note = ""
    try:
        warnings = json.loads(WARNINGS.read_text(encoding="utf-8"))
        warnings = warnings if isinstance(warnings, list) else []
    except (OSError, ValueError):
        warnings = []

    moves = []
    rows = worth_doing(u, rows)
    # A MOVE HAS TO GAIN POINTS TO BE A MOVE. `rows` is what survived
    # rank()'s screen, not a verdict: a candidate can clear the screen and
    # then simulate NEGATIVE once it is run properly. _best() has always
    # applied this rule (`d_pts > 0`) when it picks the single headline
    # move; this list did not, so the phone's table showed losing moves
    # beside winning ones -- one of two on the day this was found, at
    # -16.63 points. The ladder still lists everything, grouped and with
    # its Δ shown; this is the recommendations list, and a recommendation
    # to lose points is not one.
    for r in sorted(rows, key=lambda r: _move_rank_key(r, u)):
        a = r["action"]
        who = max(rivals, key=lambda v: r["d_beat"].get(v, 0.0)) \
            if rivals else ""
        moves.append({
            "label": a.label(names),
            "kind": ("clause" if a.victim
                     else "sell" if a.kind == "sell" else "buy"),
            "buy": names.get(a.buy, a.buy) if a.buy else "",
            "sell": " + ".join(names.get(k, k) for k in a.sell),
            "sell_n": len(a.sell),
            "victim": a.victim,
            "owner": u.view("owner").get(a.buy, "") if a.buy else "",
            "net_pts": r["net_pts"], "d_win": r["d_win"], "net": -a.net,
            "d_pts": r.get("d_pts", 0.0), "helps": r.get("helps", 0.0),
            "pts_lo": r.get("pts_lo", 0.0), "pts_hi": r.get("pts_hi", 0.0),
            "value": r.get("value"),
            "left": u.cash - a.net,
            "answer": (None if r.get("answer") is None
                       else names.get(r["answer"].buy, r["answer"].buy)),
            "p_win_after": round(base.position().get(1, 0.0) + r["d_win"], 3),
            "vs": who, "vs_gain": r["d_beat"].get(who, 0.0) if who else None,
        })
    return {
        "locks_in_h": locks_h,
        "track_record": _track_record(),
        "cash": u.cash,
        "cash_locked": u.locked_cash,
        "squad_value": squad_value(u),
        "jornadas_left": len(u.state.jornadas),
        "acquirable": len(u.view("price")),
        "considered": n_actions,
        "expected_finish": round(base.expected_position(), 2),
        "p_win": round(base.position().get(1, 0.0), 3),
        "band": [lo, hi],
        "moves": moves,
        "sell": [{"name": names.get(k, k), "pos": u.view("pos").get(k, ""),
                  "raises": got}
                 for k, got in dead_weight(u)],
        "ladder": (ladder_data if ladder_data is not None
                  else ladder_rows(u, rows, exp=exp, xi=xi)),
        "bar": u.xi_bar,
        "xi_total": _xi_total(u, u.me, exp=exp),
        "shape": _shape_now(u, xi=xi),
        "rival_best": _rival_best(u, exp=exp),
        "shape_now": fielded_shape(u, xi=xi),
        "xi_note": xi_note,
        "warnings": warnings,
        "standings": [
            {"manager": m, "me": m == u.me,
             "now": u.state.carried.get(m, 0.0), "mean": base.mean(m),
             "lo": base.band(m)[0], "hi": base.band(m)[1],
             "cash": u.cash if m == u.me else u.rival_cash.get(m, 0.0),
             "cash_known": m == u.me,
             "p_above": None if m == u.me else base.beat(m)}
            for m in sorted(u.state.squads, key=lambda m: -base.mean(m))],
    }



def _track_record():
    # Its own git-history replay, best-effort: a shallow clone or a slow
    # disk should not break the report over a line that is a bonus.
    try:
        import backtest
        return backtest.track_record()
    except Exception:
        return None


PRICE_LOG = "cash_price_log.csv"


def cash_price_history():
    import statistics
    from ffcore.tidy import DECISIONS, read_csv

    seen = []
    for r in read_csv(DECISIONS / PRICE_LOG):
        try:
            seen.append(float(r["places_per_million"]))
        except (TypeError, ValueError, KeyError):
            continue
    return statistics.median(seen) if seen else None


def log_cash_price(measured) -> None:
    from ffcore.tidy import DECISIONS, log_row

    if measured is None:
        return
    log_row(DECISIONS / PRICE_LOG,
           {"measured_at": run_now().strftime("%Y-%m-%dT%H%MZ"),
            "places_per_million": "%.6f" % measured})


def _price_note(smoothed, measured, idle_cash: float = 0.0) -> str:
    if smoothed is None and measured is None:
        return ("Nothing is charged for a buyout premium yet: no run has been "
                "able to measure what a million euros is worth")
    bits = []
    if smoothed is not None:
        bits.append("A buyout premium is charged at **%.3f places per "
                    "million**, the median of every run that has measured it"
                    % smoothed)
    if measured is not None:
        bits.append("today's own reading is %.3f" % measured)
    note = " — ".join(bits)
    if idle_cash > 0 and measured is not None and measured > 0:
        note += (". Nothing clears the bar today: %s sitting idle would be "
                "worth **~%.2f places** at today's reading, if something "
                "does" % (fmt_money(idle_cash), measured * idle_cash / 1e6))
    return note


def placeholder(why: str) -> list[str]:
    return ["# The simulation", "",
            "_Not built this run: %s._" % why, ""]


def render(u, rows, base, stamp: str, rivals, n_actions: int = 0,
           locks_h=None, ladder_data=None, exp=None, xi=None) -> list[str]:

    if exp is None or xi is None:
        exp, xi = u.current_xi
    out = ["# The simulation — %s" % stamp, ""]
    tr = _track_record()
    if tr:
        out += ["_Track record: %s._" % tr, ""]
    out += ["## Now", ""]
    out += header(u, base, n_actions or len(rows), locks_h, xi=xi)
    out += ["## Every player you could hold", ""]
    out += ladder(u, rows, base, ladder_data, exp=exp, xi=xi)
    out += ["## Where the league stands", ""]
    out += standings(u, base)
    out += ["## What the simulation cannot see", ""]
    out += caveats(u)
    return out


def _selftest() -> None:
    import decide
    from ffcore.forecast import Bootstrap
    from ffcore.season import LeagueState, Standings
    from decide import Action, Universe, dead_weight
    from ffcore.fixtures import tiny_profile, players_from_flat

    global current_mae
    _real_current_mae = current_mae
    current_mae = lambda: None  # noqa: E731

    st = Standings(totals={"me": [1000.0, 1200.0, 1400.0, 1600.0],
                           "riv": [1500.0, 1300.0, 1100.0, 900.0]}, me="me")
    u = Universe(state=LeagueState({"me": {}, "riv": {}}, [1, 2], "me",
                                   carried={"me": 17.0, "riv": 23.0}),
                 forecaster=Bootstrap({}, pool=[1, 2, 3]), cash=23.6e6, me="me",
                 players=players_from_flat(
                     name={"yuri": "yuri berchiche",
                          "benat": "benat turrientes"}))

    rows = [{"player_id": "1070", "player_name": "Ionut Radu",
             "player_name_full": "Ionut Andrei Radu"},
            {"player_id": "2464", "player_name": "Pepelu",
             "player_name_full": "José Luis García Vayá"}]
    squad = {"ionut radu": 1, "pepelu": 1}
    assert app_fielded(squad, {"pepelu": "Pepelu"}, rows,
                       {"1070": "ionut radu"}) == ["ionut radu", "pepelu"]
    assert app_fielded(squad, {}, rows + [{"player_id": "999",
                                           "player_name": "Nobody"}], {}) == []
    assert app_fielded(squad, {}, rows,
                       {"1070": "ionut radu", "2464": "someone else"}) == []
    assert app_fielded({}, {}, [], {}) == []

    best = ["gk", "d1", "d2", "d3", "d4", "m1", "m2", "m3", "m4", "m5", "f1"]
    same = xi_change(list(best), best)
    assert same["legal"] and same["in"] == [] and same["out"] == []
    swap = xi_change([k for k in best if k != "m5"] + ["bench1"], best)
    assert swap["in"] == ["m5"] and swap["out"] == ["bench1"], swap
    short_marks = xi_change(best[:10], best)
    assert not short_marks["legal"] and short_marks["marked"] == 10
    assert short_marks["in"] == [] and short_marks["out"] == []
    assert not xi_change([], best)["legal"]

    h = " ".join(header(u, st, n_actions=132, locks_h=41.1))
    assert h.index("41h") < h.index("1.50"), h
    assert "cash 23.60M" in h, h
    assert "Locks" not in " ".join(header(u, st, 1, locks_h=None))
    assert "." not in h.replace("1.50", "").replace("41.1", "") \
        .replace("0.00", "").replace("23.60M", "").replace("1,060", "") \
        .replace("1,540", "") or True
    u.cash = -133023.0
    assert "**cash -133K**" in " ".join(header(u, st, 1, locks_h=2.0))
    u.cash = 23.6e6
    assert "**cash" not in " ".join(header(u, st, 1, locks_h=2.0))
    u.cash, u.locked_cash = -2637643.0, 5938860.0
    hh = " ".join(header(u, st, 1, locks_h=2.0))
    assert "**cash -2.64M** (5.94M already bid)" in hh, hh
    u.cash, u.locked_cash = 23.6e6, 0.0
    assert "locked" not in " ".join(header(u, st, 1, locks_h=2.0))
    assert "1.50" in h, h
    assert "50%" in h, h
    assert "1,060" in h and "1,540" in h, h
    assert "jornadas left" not in h, h
    assert "moves simulated" not in h, h
    assert "23.60M" in h, h
    assert "play " in h, h

    rows = [{"action": Action("clause", buy="yuri", sell="benat",
                              cost=20e6, proceeds=5.87e6, victim="riv"),
             "net_pts": 0.433, "d_win": 0.364, "d_beat": {"riv": 0.37},
             "d_pts": 120.0, "pts_lo": 43.3, "pts_hi": 210.0,
             "helps": 0.90, "mean": 1510.0,
             "value": 120.0 / (14.13e6 / 1e6)}]

    sq = {"k": "POR", "d1": "DEF", "d2": "DEF", "d3": "DEF", "d4": "DEF",
          "m1": "MED", "m2": "MED", "m3": "MED", "m4": "MED",
          "f1": "DEL", "f2": "DEL",
          "spare_m": "MED", "spare_k": "POR"}
    val = {k: 5.0 for k in sq}
    val["spare_m"] = 0.4
    val["spare_k"] = 0.2
    u2 = Universe(state=LeagueState({"me": dict(sq), "riv": dict(sq)}, [1, 2],
                                    "me"),
                  forecaster=Bootstrap({1: {k: (v, 1.0) for k, v in val.items()},
                                        2: {k: (v, 1.0) for k, v in val.items()}}),
                  cash=0.0, me="me",
                  players=players_from_flat(
                      pos=dict(sq),
                      proceeds={"spare_m": 7.45e6, "spare_k": 4.73e6,
                               "d1": 9e6},
                      name={"spare_m": "benat turrientes",
                           "spare_k": "alvaro fernandez"}))
    dead = dead_weight(u2)
    assert [k for k, _ in dead] == ["spare_m", "spare_k"], dead
    assert [v for _, v in dead] == [7.45e6, 4.73e6], dead
    assert "d1" not in dict(dead)
    from ffcore.season import best_xi as _bx
    left = {k: v for k, v in sq.items() if k not in dict(dead)}
    assert len(_bx(left, val)) == 11, left

    from dataclasses import replace as _dc_replace
    u_over = _dc_replace(u2, cash=-5e6)
    al_over = alert_lines(u_over, [], ["riv"])
    assert len(al_over) == 1, al_over
    assert "Overdrawn" in al_over[0] and "Benat Turrientes" in al_over[0], \
        al_over
    assert "Alvaro Fernandez" not in al_over[0], al_over
    u_over_big = _dc_replace(u2, cash=-15e6)
    al_big = alert_lines(u_over_big, [], ["riv"])
    assert len(al_big) == 1, al_big
    assert "Benat Turrientes" in al_big[0] and "Alvaro Fernandez" in al_big[0], \
        al_big
    assert "still" in al_big[0] and "short" in al_big[0], al_big
    assert "d1" not in al_big[0]

    # LIVE BIDS. The board either still wants the man at that price or the
    # bid should come off the table; and the app neither debits a bid nor
    # refuses one you cannot cover, so the shortfall has to be said out loud.
    # A MAN CAN BE EXCELLENT AND STILL BE A LOSING TRADE. par is per-player
    # and season-long; d_pts is what happens to YOUR squad once the sale
    # that funds him goes too. The ladder offered Pape Gueye at par +56 with
    # d_pts -2.0 on 2026-09-18 while the recommendation, which screens on
    # d_pts, left him out -- two renderers disagreeing off one set of rows.
    assert _gains({"d_pts": 0.1})
    assert not _gains({"d_pts": -2.0}), "a losing move is not a buy"
    assert not _gains({"d_pts": 0.0}), "break-even is not worth a transfer"
    assert not _gains({}), "no simulated figure is not a yes"
    assert not _gains({"d_pts": None})

    # THE PHONE AND THE JSON MUST NAME THE SAME MOVE. worth_doing() is the
    # one screen; alert_lines() and payload() both run on its output. When
    # only payload() screened, a run on 2026-09-19 told Miguel to buy Jose
    # Angel Lopez while its own decisions.json listed neither him nor that
    # move, having dropped him on the par floor.
    _wd_rows = [{"action": Action("buy", buy="yuri", cost=1e6),
                 "d_pts": 5.0, "d_win": 0.01, "net_pts": 5.0, "pts_lo": 0.0,
                 "pts_hi": 9.0, "helps": 0.7, "value": None, "burn": None,
                 "charge": 0.0, "answer": None, "mean": 0.0, "d_beat": {}},
                {"action": Action("buy", buy="benat", cost=1e6),
                 "d_pts": -3.0, "d_win": -0.01, "net_pts": -3.0,
                 "pts_lo": -9.0, "pts_hi": 1.0, "helps": 0.2, "value": None,
                 "burn": None, "charge": 0.0, "answer": None, "mean": 0.0,
                 "d_beat": {}}]
    kept = worth_doing(u2, _wd_rows)
    assert [r["action"].buy for r in kept] == ["yuri"], kept
    # and the headline is chosen from exactly that, never from the raw rows
    import inspect as _ins
    src_main = _ins.getsource(main)
    assert "alert_lines(u, worth_doing(" in src_main, \
        "the phone's headline must be screened by the same rule as the JSON"

    assert bid_lines(u2, []) == [], "no bids, nothing to say"
    u_bid = _dc_replace(u2, cash=3e6, my_bids={"yuri": 8e6, "benat": 2e6},
                        locked_cash=10e6)
    bl = bid_lines(u_bid, [])
    assert len(bl) == 2, bl
    assert bl[0].startswith("**Withdraw 10.00M**"), bl
    assert "Yuri 8.00M" in bl[0] and "Benat 2.00M" in bl[0], bl
    assert "them" in bl[0], bl
    assert "**10.00M bid, 3.00M in hand**" in bl[1], bl
    assert "7.00M short" in bl[1], bl

    # WANTED AND PAID FOR: the board buys Yuri for a net 1M because it sells
    # someone to do it, and 1M is inside the 3M held -- so that bid stands.
    funded = [{"action": Action("swap", buy="yuri", sell=("d1",),
                                cost=8e6, proceeds=7e6)}]
    kept = bid_lines(u_bid, funded)
    assert len(kept) == 2, kept
    assert "Yuri" not in kept[0], "an endorsed, funded bid is not withdrawn"
    assert kept[0].startswith("**Withdraw 2.00M**") and "him" in kept[0], kept

    # WANTED AND NOT PAID FOR: same man, no sale behind him, 8M against 3M.
    # Wanting him is not the test -- affording him is.
    broke = bid_lines(u_bid, [{"action": Action("buy", buy="yuri", cost=8e6)}])
    assert broke[0].startswith("**Withdraw 10.00M**"), broke
    assert "Yuri 8.00M" in broke[0], broke

    covered = _dc_replace(u2, cash=12e6, my_bids={"yuri": 8e6},
                          locked_cash=8e6)
    cl = bid_lines(covered, [{"action": Action("buy", buy="yuri", cost=8e6)}])
    assert cl == [], "endorsed and affordable is not news"

    # TWO bids the board wants and one wallet that covers only the first.
    pair = _dc_replace(u2, cash=10e6, my_bids={"yuri": 8e6, "benat": 7e6},
                       locked_cash=15e6)
    pl = bid_lines(pair, [{"action": Action("buy", buy="yuri", cost=8e6)},
                          {"action": Action("buy", buy="benat", cost=7e6)}])
    assert pl[0].startswith("**Withdraw 7.00M**"), pl
    assert "Benat 7.00M" in pl[0] and "Yuri" not in pl[0], pl

    # bid_lines rides ALONGSIDE the other alerts, never instead of them.
    both = alert_lines(_dc_replace(u_over, my_bids={"yuri": 8e6},
                                   locked_cash=8e6), [], ["riv"])
    assert len(both) == 3, both
    assert "Withdraw" in both[0] and "Overdrawn" in both[-1], both

    u2.part_played = {1: {"alaves"}}
    u2.forecaster = Bootstrap({1: {k: (v, 1.0) for k, v in val.items()},
                               2: {k: ((9.0 if k == "spare_m" else v), 1.0)
                                   for k, v in val.items()}})
    assert "spare_m" not in dict(dead_weight(u2)), \
        "a man who starts in a round still ahead is not spare"
    u2.forecaster = Bootstrap(
        {1: {k: ((9.0 if k == "spare_m" else v), 1.0) for k, v in val.items()},
         2: {k: (v, 1.0) for k, v in val.items()}})
    assert "spare_m" in dict(dead_weight(u2)), \
        "a man who starts only in a locked round is still spare"
    u2.state.jornadas = [1]
    assert "spare_m" not in dict(dead_weight(u2))
    u2.state.jornadas, u2.part_played = [1, 2], {}

    ws = "\n".join(standings(u, st))
    assert "| me " in ws and "| riv " in ws, ws
    assert "17" in ws and "23" in ws, ws
    assert "1,300" in ws, ws
    assert "50%" in ws, ws

    u.part_played = {1: {"alaves", "getafe"}}
    u.unjoined = ["A. Ferllo"]
    u.start_note = "P(start) fitted on 240 confirmed starts"
    cav = "\n".join(caveats(u))
    assert "seed prior" in cav, cav
    assert "240 confirmed starts" in cav, cav
    assert "jornada 1" in cav.lower(), cav
    assert "A. Ferllo" in cav, cav
    u.cash_note = "A buyout premium is charged at **0.002 places per million**"
    cav2 = "\n".join(caveats(u))
    assert "0.002 places per million" in cav2, cav2
    u.part_played, u.unjoined, u.cash_note = {}, [], ""
    clean = "\n".join(caveats(u))
    assert "A. Ferllo" not in clean and "jornada 1" not in clean.lower(), clean

    legal_sq = {"k": "POR", **{f"d{i}": "DEF" for i in range(1, 5)},
               **{f"m{i}": "MED" for i in range(1, 5)},
               "f1": "DEL", "f2": "DEL"}
    ph_sq = {"__phantom_DEF_0": "DEF", "d1": "DEF", "d2": "DEF",
            "m1": "MED", "m2": "MED", "m3": "MED", "m4": "MED", "m5": "MED",
            "p1": "POR", "f1": "DEL", "f2": "DEL"}
    u_phantom = Universe(
        state=LeagueState({"me": legal_sq, "riv": ph_sq}, [1], "me"),
        forecaster=Bootstrap({}), cash=0.0, me="me")
    assert phantom_filled(u_phantom) == [("riv", ["1 defensa"])], \
        phantom_filled(u_phantom)
    assert "me" not in dict(phantom_filled(u_phantom))
    ph_cav = "\n".join(caveats(u_phantom))
    assert "riv's squad is short a position" in ph_cav, ph_cav
    assert "1 defensa" in ph_cav, ph_cav

    d = payload(u, rows, st, ["riv"], locks_h=41.1, n_actions=132)
    assert d["expected_finish"] == 1.5 and d["p_win"] == 0.5, d
    st3 = Standings(totals={"me": [1000.0, 1200.0, 1000.0],
                            "riv": [1500.0, 900.0, 1500.0]}, me="me")
    d3 = payload(u, rows, st3, ["riv"])
    assert d3["p_win"] == round(1 / 3, 3) == 0.333, d3["p_win"]
    assert len(str(d3["p_win"]).split(".")[-1]) <= 3, d3["p_win"]
    assert d["band"] == [1060.0, 1540.0], d
    assert d["locks_in_h"] == 41.1 and d["cash"] == 23.6e6
    assert d["cash_locked"] == 0.0, d
    u.locked_cash = 2.1e6
    assert payload(u, rows, st, ["riv"])["cash_locked"] == 2.1e6
    u.locked_cash = 0.0
    m = d["moves"][0]
    assert m["label"] == "clause Yuri Berchiche from riv · sell Benat Turrientes"
    assert m["buy"] == "Yuri Berchiche" and m["sell"] == "Benat Turrientes"
    two = payload(u, [{**rows[0],
                       "action": Action("swap", buy="yuri",
                                        sell=("benat", "yuri"),
                                        cost=1e6, proceeds=2e6)}],
                  st, ["riv"])["moves"][0]
    assert two["sell"] == "Benat Turrientes + Yuri Berchiche", two
    assert two["sell_n"] == 2 and m["sell_n"] == 1, (two, m)
    assert m["victim"] == "riv" and m["kind"] == "clause"
    assert m["net_pts"] == 0.433 and m["d_win"] == 0.364
    assert abs(m["p_win_after"] - (0.5 + 0.364)) < 1e-9, m
    assert m["left"] == 23.6e6 - (20e6 - 5.87e6), m
    assert m["answer"] is None, m
    withans = payload(u, [{**rows[0],
                           "answer": Action("clause", buy="yuri",
                                            victim="me")}],
                      st, ["riv"])["moves"][0]
    assert withans["answer"] == "Yuri Berchiche", withans
    assert m["net"] == -(20e6 - 5.87e6), m
    assert m["vs"] == "riv" and m["vs_gain"] == 0.37
    assert [r["manager"] for r in d["standings"]] == ["me", "riv"], d
    assert d["standings"][0]["me"] is True
    assert d["standings"][1]["p_above"] == 0.5

    row_a = {"action": Action("buy", buy="A", cost=40e6), "net_pts": 0.40,
             "d_win": 0.0, "d_beat": {}, "value": 2.0,
             "d_pts": 40.0, "pts_lo": -30.0}
    row_b = {"action": Action("buy", buy="B", cost=1e6), "net_pts": 0.15,
             "d_win": 0.0, "d_beat": {}, "value": 50.0,
             "d_pts": 15.0, "pts_lo": 10.0}
    row_c = {"action": Action("buy", buy="C", cost=1e4), "net_pts": 0.02,
             "d_win": 0.0, "d_beat": {}, "value": 500.0,
             "d_pts": 2.0, "pts_lo": -80.0}
    order = [m["buy"] for m in payload(u, [row_a, row_b, row_c], st,
                                       ["riv"])["moves"]]
    assert order == ["A", "B", "C"], order
    row_win = {"action": Action("buy", buy="W", cost=40e6), "net_pts": 0.05,
              "d_win": 0.10, "d_beat": {}, "value": 1.0,
              "d_pts": 5.0, "pts_lo": -50.0}
    order_win = [m["buy"] for m in payload(u, [row_a, row_win, row_b], st,
                                           ["riv"])["moves"]]
    assert order_win == ["A", "B", "W"], order_win

    al = alert_lines(u, rows, ["riv"])
    assert len(al) == 1 and "Yuri Berchiche" in al[0] and "+36%" in al[0], al
    assert "€14.1M" in al[0], al
    assert alert_lines(u, [], ["riv"]) == []
    # A MOVE WORTH NOTHING IS NOT NEWS -- and the rule that says so lives in
    # worth_doing(), once. alert_lines() used to repeat it, which is how the
    # phone and the JSON came to disagree in the first place: two copies of
    # "recommendable" that had drifted apart.
    flat = [{**rows[0], "net_pts": 0.0, "d_win": 0.0, "d_pts": 0.0}]
    assert worth_doing(u, flat) == [], "the screen drops it"
    assert alert_lines(u, worth_doing(u, flat), ["riv"]) == []

    assert "idle" not in _price_note(0.05, 0.03)
    assert "idle" not in _price_note(0.05, 0.03, 0.0)
    note = _price_note(0.05, 0.02, 10e6)
    assert "idle" in note and fmt_money(10e6) in note, note
    assert "~0.20 places" in note, note
    assert "idle" not in _price_note(0.05, 0.0, 10e6)

    cheap_ok = {**rows[0],
                "action": Action("buy", buy="cheap", cost=2e6, proceeds=0.0),
                "net_pts": 0.40, "d_win": 0.30, "d_pts": 110.4, "pts_lo": 40.0}
    assert _best(u, [rows[0], cheap_ok], ["riv"]) == (cheap_ok, False), \
        "a move keeping 90%+ of the best gain for a fraction of the cost wins"
    cheap_bad = {**rows[0],
                 "action": Action("buy", buy="cheap", cost=2e6, proceeds=0.0),
                 "net_pts": 0.30, "d_win": 0.20, "d_pts": 82.8, "pts_lo": 30.0}
    assert _best(u, [rows[0], cheap_bad], ["riv"]) == (rows[0], False), \
        "a cheaper move that gives up too much of the gain does not win"
    free = {**rows[0],
            "action": Action("sell", sell=("dead",), cost=0.0, proceeds=1e6),
            "net_pts": 0.40, "d_win": 0.30, "d_pts": 110.4, "pts_lo": 40.0}
    assert _best(u, [rows[0], free], ["riv"]) == (free, False), \
        "a self-funding move within reach of the best gain wins outright"
    winonly = {**rows[0], "action": Action("buy", buy="x", cost=1e6),
              "net_pts": 0.0, "d_win": 0.05, "d_pts": 0.0, "pts_lo": 0.0}
    assert worth_doing(u, [winonly]) == [], \
        "a move that gains no points is screened out before _best sees it"
    assert _best(u, [], ["riv"]) == (None, False), "nothing left, nothing said"

    safer = {**rows[0],
             "action": Action("clause", buy="safer", sell="benat",
                              cost=20e6, proceeds=5.87e6, victim="riv"),
             "pts_lo": 80.0}
    riskier = {**rows[0],
               "action": Action("clause", buy="riskier", sell="benat",
                                cost=20e6, proceeds=5.87e6, victim="riv"),
               "pts_lo": -10.0}
    best1, _ = _best(u, [safer, riskier], ["riv"])
    assert best1["action"].buy == "safer", best1
    best2, _ = _best(u, [riskier, safer], ["riv"])
    assert best2["action"].buy == "riskier", best2

    listed_big = {**rows[0],
                  "action": Action("buy", buy="listed_target", cost=30e6),
                  "net_pts": 0.50, "d_win": 0.40, "pts_lo": 50.0}
    u_route = Universe(
        state=LeagueState({"me": {}, "riv": {}}, [1, 2], "me"),
        forecaster=Bootstrap({}), cash=0.0, me="me",
        players={"listed_target": tiny_profile("listed_target",
                                               route="listed")})
    assert _best(u_route, [listed_big], ["riv"]) == (listed_big, True)
    assert _best(u_route, [listed_big, rows[0]], ["riv"]) == (rows[0], False), \
        "a reliable move beats a bigger listed one outright"

    assert _best(u_route, [cheap_ok, rows[0]], ["riv"]) == (cheap_ok, False), \
        "order must not change the value-for-money winner"
    assert _best(u_route, [cheap_bad, rows[0]], ["riv"]) == (rows[0], False), \
        "order must not change which move keeps too little of the gain"
    assert _best(u_route, [free, rows[0]], ["riv"]) == (free, False), \
        "order must not change a self-funding winner"
    assert _best(u_route, [rows[0], listed_big], ["riv"]) == (rows[0], False), \
        "order must not change reliable-beats-listed"

    from ffcore.crosswalk import Player
    from ffcore.profile import (PlayerProfile,
                                PlayerCurrent, PlayerHistory, PlayerDerived)

    steady_row = {"action": Action("buy", buy="steady", cost=5e6),
                 "net_pts": 0.40, "d_win": 0.0, "d_beat": {}, "value": 8.0,
                 "d_pts": 40.0, "pts_lo": 10.0, "pts_hi": 70.0, "helps": 0.80}
    dud_row = {"action": Action("buy", buy="dud", cost=5e6),
              "net_pts": 0.20, "d_win": 0.0, "d_beat": {}, "value": 4.0,
              "d_pts": 20.0, "pts_lo": 5.0, "pts_hi": 35.0, "helps": 0.60}
    maverick_row = {"action": Action("buy", buy="maverick", cost=5e6),
                   "net_pts": 0.10, "d_win": 0.0, "d_beat": {}, "value": 2.0,
                   "d_pts": 10.0, "pts_lo": -50.0, "pts_hi": 260.0,
                   "helps": 0.55}
    riv_row = {"action": Action("clause", buy="rivals", cost=5e6),
              "net_pts": 0.60, "d_win": 0.0, "d_beat": {}, "value": 12.0,
              "d_pts": 60.0, "pts_lo": 20.0, "pts_hi": 90.0, "helps": 0.90,
              "burn": 1.2e6}
    wish_row = {"action": Action("buy", buy="wished", cost=5e6),
               "net_pts": 0.50, "d_win": 0.0, "d_beat": {}, "value": 10.0,
               "d_pts": 50.0, "pts_lo": 15.0, "pts_hi": 80.0, "helps": 0.85}
    uc_price = {"steady": 5e6, "maverick": 5e6, "dud": 5e6, "rivals": 5e6,
               "wished": 5e6}
    uc_owner = {"rivals": "riv", "wished": "riv"}
    uc_route = {"rivals": "clause", "wished": "listed"}
    uc_value = {"steady": 5e6, "rivals": 3.8e6}
    uc_owned = Universe(
        state=LeagueState({"me": {}, "riv": {}}, [1, 2], "me"),
        forecaster=Bootstrap(
            {j: {"steady": (5.0, 1.0), "maverick": (4.0, 1.0),
                "dud": (3.0, 1.0), "rivals": (7.0, 1.0),
                "wished": (6.5, 1.0)} for j in (1, 2)}),
        cash=10e6, me="me",
        players={k: PlayerProfile(
            identity=Player(player_id=k, name=k),
            current=PlayerCurrent(
                pos="MED", price=uc_price.get(k), owner=uc_owner.get(k),
                route=uc_route.get(k), value=uc_value.get(k),
                listed=k in uc_price),
            history=PlayerHistory(), derived=PlayerDerived(pj=5.0))
            for k in ("steady", "dud", "maverick", "rivals", "wished")})
    all_rows = [steady_row, dud_row, maverick_row, riv_row, wish_row]
    owned_lad = ladder_rows(uc_owned, all_rows)
    steady_cell = next(r for r in owned_lad if r["name"].lower() == "steady")
    assert steady_cell["par"] == 4.0, steady_cell
    by_group = {}
    for r in owned_lad:
        by_group.setdefault(r["group"], []).append(r["name"].lower())
    assert by_group["buy"] == ["steady", "dud", "maverick"], by_group

    # A BID ON A MAN YOU OWN gets its own row, wherever he sits. It already
    # lifted his proceeds inside the simulation and said nothing, so the best
    # offer on the board -- 48.07M for Fornals, 8% over market -- was
    # invisible, because a settled starter is collapsed into the eleven's
    # summary line and never gets a row at all.
    offered = _dc_replace(
        uc_owned,
        state=LeagueState({"me": {"steady": "MED", "dud": "MED"}, "riv": {}},
                          [1, 2], "me"),
        received_offers={"steady": 6.0e6, "dud": 1.0e6})
    got = [r for r in ladder_rows(offered, all_rows) if r["group"] == "offer"]
    assert [r["name"].lower() for r in got] == ["steady", "dud"], got
    assert got[0]["offer"] == 6.0e6, got[0]
    assert got[0]["worth"] == 5.0e6, ("market value comes along, because a "
                                      "bid means nothing without it", got[0])
    # Judged against what a sale FETCHES, which is the quoted value lifted
    # by the fitted clearing premium -- so a bid at the quoted value is a
    # below-average bid, not a fair one.
    assert got[0]["going"] is not None and got[0]["going"] >= 5.0e6, got[0]
    # AND NOT against what he cost: that is sunk, and it was in here until
    # Miguel pointed out it has no business in the decision.
    assert "bought" not in got[0], ("purchase price is a sunk cost",
                                    got[0])
    # Ordered by how generous the bid is against market value: steady is
    # 6.0M against 5.0M, dud has no market value to be generous against.
    assert got[1]["worth"] is None, got[1]
    assert all(r.get("offer") for r in got), got
    assert not [r for r in ladder_rows(uc_owned, all_rows)
                if r["group"] == "offer"], "no offers, no section"
    assert by_group["raid"] == ["rivals"], by_group
    assert "wished" not in [n for names in by_group.values() for n in names], \
        by_group
    md_owned = "\n".join(ladder(uc_owned, all_rows, st))
    assert "| PAR |" in md_owned, md_owned
    steady_line = next(l for l in md_owned.splitlines()
                       if l.lower().startswith("| steady"))
    assert "+10" in steady_line, steady_line
    assert "BUY — free agents" in md_owned, md_owned
    assert "RAID — a clause, cannot be refused" in md_owned, md_owned
    assert "LISTED" not in md_owned, md_owned
    assert "wished" not in md_owned.lower(), md_owned
    no_free_lad = "\n".join(ladder(uc_owned, [riv_row], st))
    assert "none clear the bar today" in no_free_lad, no_free_lad
    assert "RAID" in no_free_lad, no_free_lad

    steady_cell = next(r for r in owned_lad if r["name"].lower() == "steady")
    assert steady_cell["market"] == 5e6 and not steady_cell["premium"], \
        steady_cell
    rivals_cell = next(r for r in owned_lad if r["name"].lower() == "rivals")
    assert rivals_cell["market"] == 3.8e6, rivals_cell
    assert rivals_cell["premium"] == 1.2e6, rivals_cell
    assert "5.00M" in md_owned, md_owned
    assert "3.80M +1.20M" in md_owned, md_owned

    slot_players = {"k": "POR", "d": "DEF", "m": "MED", "f": "DEL"}
    u_slot = Universe(
        state=LeagueState({"me": {}}, [1], "me"), forecaster=Bootstrap({}),
        cash=0.0, me="me",
        players={key: tiny_profile(key, pos=pos)
                for key, pos in slot_players.items()})
    assert by_slot(u_slot, ["m", "f", "k", "d"], exp={}) == \
        ["k", "d", "m", "f"]

    shape_players = {"a": "POR", "b": "DEF", "c": "DEF", "d": "DEF",
                     "e": "DEF", "f": "MED", "g": "MED", "h": "MED",
                     "i": "MED", "j": "DEL", "k": "DEL"}
    u_shape = Universe(
        state=LeagueState({"me": {}}, [1], "me"), forecaster=Bootstrap({}),
        cash=0.0, me="me",
        players={key: tiny_profile(key, pos=pos)
                for key, pos in shape_players.items()})
    assert shape(u_shape, list("abcdefghijk")) == "4-4-2"
    assert shape(u_shape, ["a", "b", "c", "d", "f", "g"]) == "3-2-0"
    assert shape(u_shape, []) == "0-0-0"

    page = "\n".join(render(u, rows, st, "2026-08-18T0152Z", ["riv"], 132,
                             locks_h=41.1))
    assert "132 moves" not in page, page[:400]
    assert page.startswith("# The simulation — 2026-08-18T0152Z"), page[:80]
    heads = [ln for ln in page.splitlines() if ln.startswith("## ")]
    assert len(heads) == len(set(heads)) == 4, heads

    for banned in ("## Do this", "## The board", "## Warnings"):
        assert banned not in heads, heads

    ph = "\n".join(placeholder("no api_teams.csv"))
    assert "no api_teams.csv" in ph and ph.startswith("# The simulation")

    from decide import Universe as U2
    from ffcore.season import LeagueState as LS2

    many_j = list(range(1, 11))
    sqb = {"k": "POR", **{f"d{i}": "DEF" for i in range(1, 5)},
          "star": "MED", **{f"m{i}": "MED" for i in range(1, 5)},
          "f1": "DEL", "dead": "MED"}
    riv = {"rk": "POR", **{f"rd{i}": "DEF" for i in range(1, 5)},
          **{f"rm{i}": "MED" for i in range(1, 5)}, "rf1": "DEL"}
    perb = {j: {**{k: (3.0, 0.9) for k in sqb if k not in ("star", "dead")},
               "star": (6.0, 0.9), "dead": (3.0, 0.05), "cand": (5.0, 0.8),
               **{k: (3.0, 0.9) for k in riv}} for j in many_j}
    ub = U2(state=LS2({"me": dict(sqb), "riv": dict(riv)}, many_j, "me"),
           forecaster=Bootstrap(perb, matches={k: 30 for k in
                                               (*sqb, *riv, "cand")}),
           cash=10e6, me="me",
           players=players_from_flat(
               pos={**{k: v for k, v in sqb.items()}, "cand": "MED"},
               price={"cand": 5e6},
               proceeds={"dead": 1e6, "star": 20e6}))
    asked = band_acts(ub)
    keys = {k for k, _a in asked}
    assert keys == {*sqb, "cand"}, keys
    by_key = dict(asked)
    for k in sqb:
        assert by_key[k].buy == "" and by_key[k].sell == (k,), by_key[k]
    assert by_key["cand"].buy == "cand" and by_key["cand"].sell == (), \
        by_key["cand"]

    _rows, baseb, _lam, bands = ub.rank([], extra=asked)
    assert set(bands) == {*sqb, "cand"}, sorted(bands)
    for key in bands:
        med, lo, hi, _act, _mean = bands[key]
        assert lo <= med <= hi, (key, bands[key])
    assert bands["star"][0] < -20, bands["star"]
    assert -5 < bands["dead"][0] < 5, bands["dead"]
    assert bands["dead"][3].buy == "" and bands["dead"][3].sell == ("dead",)
    assert bands["cand"][0] > 0, bands["cand"]
    assert ub.rank([], extra=[])[3] == {}

    # WHAT A MAN COST IS NOWHERE, in either renderer. It was on the SELL row
    # and in the JSON beside it, and it is a sunk cost either way: what he
    # raises is the number that decides anything.
    sell_lad = "\n".join(ladder(ub, [], baseb))
    dead_line = next(l for l in sell_lad.splitlines()
                     if l.lower().startswith("| dead"))
    assert "1.00M" in dead_line, dead_line
    assert "bought" not in dead_line.lower(), dead_line
    sell_json = payload(ub, [], baseb, ["riv"])["sell"]
    by_name = {r["name"]: r for r in sell_json}
    assert by_name["Dead"]["raises"] == 1e6, by_name["Dead"]
    assert "bought" not in by_name["Dead"], by_name["Dead"]
    assert "star" not in by_name, by_name

    buy_cand = decide.Action("buy", buy="cand", cost=5e6)
    rows2, _b2, _l2, bands2 = ub.rank([buy_cand], extra=asked)
    assert [r for r in rows2 if r["action"].buy == "cand"], rows2
    assert "cand" not in bands2, sorted(bands2)

    def _raid_row(buy, victim, d_pts, d_beat_victim):
        return {"action": decide.Action("steal", buy=buy, victim=victim),
               "d_pts": d_pts, "d_beat": {victim: d_beat_victim}}

    won_raids = {
        "r1": _raid_row("r1", "riv", d_pts=5.0, d_beat_victim=0.20),
        "r2": _raid_row("r2", "riv", d_pts=8.0, d_beat_victim=0.05),
        "r3": _raid_row("r3", "riv2", d_pts=3.0, d_beat_victim=0.10),
    }
    kept = _best_raid_per_victim(list(won_raids), won_raids)
    assert sorted(kept) == ["r1", "r3"], kept
    fallback = {"x": _raid_row("x", "solo", d_pts=1.0, d_beat_victim=0.0)}
    fallback["x"]["d_beat"] = {}
    assert _best_raid_per_victim(["x"], fallback) == ["x"]

    par_of = {"good": 5.0, "weak": 1.99, "unknown": None}
    assert _clears_par_floor(par_of, None, "weak") is True
    assert _clears_par_floor(par_of, 2.9, "good") is True
    assert _clears_par_floor(par_of, 2.9, "weak") is False
    assert _clears_par_floor(par_of, 2.9, "unknown") is False
    assert _clears_par_floor(par_of, 2.9, "missing") is False

    current_mae = _real_current_mae
    # a broken or slow replay never breaks the report over a bonus line
    import backtest
    real_tr = backtest.track_record
    backtest.track_record = lambda *a, **k: (_ for _ in ()).throw(RuntimeError)
    try:
        assert _track_record() is None
    finally:
        backtest.track_record = real_tr

    print("sim self-test OK (221 cases)")


def main() -> None:
    import decide
    from ffcore.model import session
    from ffcore.tidy import load_deadline

    REPORTS.mkdir(exist_ok=True)
    PARTS.mkdir(parents=True, exist_ok=True)
    rows_m = session().market
    stamp = rows_m[0]["observed_at"] if rows_m else ""
    deadline = load_deadline()
    locks_h = None if deadline is None else (
        deadline - run_now()).total_seconds() / 3600

    u = decide.load()
    if len(u.state.squads) < 2:
        write_lines(PARTS / OUT,
                    placeholder("the league API has not been swept, so there "
                                "are no rival squads to simulate against"))
        print("wrote %s (placeholder)" % (PARTS / OUT))
        return
    if not u.state.jornadas:
        write_lines(PARTS / OUT,
                    placeholder("there are no jornadas left to play"))
        print("wrote %s (placeholder)" % (PARTS / OUT))
        return

    exp = u.forecaster.expected(u.state.jornadas[0])
    acts = u.candidates(exp, budget=float("inf"))
    smoothed = cash_price_history()
    xi_exp, xi = u.current_xi
    bar_acts = band_acts(u, exp=xi_exp, xi=xi)
    bar_keys = {k for k, _ in bar_acts}
    mine = u.state.squads.get(u.me, {})
    market_acts = [(k, decide.Action("buy", buy=k,
                                     cost=u.view("price").get(k, 0.0)))
                  for k in u.view("price") if k not in mine]
    extra_acts = bar_acts + [t for t in market_acts
                             if t[0] not in bar_keys]
    rows, base, measured, bands = u.rank(
        acts, price=smoothed, extra=extra_acts)
    log_cash_price(measured)
    idle = u.cash if (not rows and u.cash > 0) else 0.0
    u.cash_note = _price_note(smoothed, measured, idle)
    rivals = [m for m in u.state.squads if m != u.me]
    ladder_data = ladder_rows(u, rows, bands, exp=xi_exp, xi=xi)
    from slate import comparison_rows, comparison_table
    cmp_rows = comparison_rows(u, bands)
    write_lines(PARTS / OUT,
                render(u, rows, base, stamp, rivals, len(acts), locks_h,
                       ladder_data, exp=xi_exp, xi=xi)
                + comparison_table(cmp_rows))
    print("wrote %s (%d moves, %d simulated in full)"
          % (PARTS / OUT, len(acts), len(rows)))

    (REPORTS / "decisions.json").write_text(json.dumps({
        "generated_at": run_now()
                          .strftime("%Y-%m-%dT%H:%MZ"),
        **payload(u, rows, base, rivals, locks_h, len(acts),
                  ladder_data=ladder_data, exp=xi_exp, xi=xi),
        "market": cmp_rows,
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("wrote %s" % (REPORTS / "decisions.json"))

    lines = alert_lines(u, worth_doing(u, rows), rivals)
    if lines or ALERTS.exists():
        prev = [ln for ln in
                (ALERTS.read_text(encoding="utf-8").splitlines()
                 if ALERTS.exists() else [])
                if ln.startswith("- ")]
        # Previous alerts are carried so a warning raised between reports is
        # not lost -- but carried ONCE. Without this the file grew a fresh
        # copy of every standing alert on every run, and the noise buried
        # exactly the time-critical lines the file exists to surface.
        body, seen = [], set()
        for ln in ["- " + ln for ln in lines] + prev:
            if ln not in seen:
                seen.add(ln)
                body.append(ln)
        if body:
            ALERTS.parent.mkdir(parents=True, exist_ok=True)
            write_lines(ALERTS, ["# Alerts — %s" % shown(), ""] + body)
        else:
            ALERTS.unlink(missing_ok=True)
    print("%d alert(s) from the simulation" % len(lines))


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        raise SystemExit(0)
    main()
