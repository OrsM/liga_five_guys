"""Buy players whose market value is drifting up, hold while it does, sell what holding will not pay for.

WHY THIS SHAPE (measured 2026-09-24 on 44 days of values and the league's own
ledger). A player's market value has momentum: after a large update he tends to
rise again. But an OVERNIGHT flip loses money -- the auction winner pays over the
ask and the app buys back under value -- so a buy has to be HELD for the drift to
pay for that friction. The managers who made money here held a median 8-9 days.

NOTHING BELOW IS A CHOSEN NUMBER, AND NOTHING IS A KNOB. Every figure a decision
rests on is computed from the data on each run: what an update of a given size
has been followed by (its K = sqrt(n) nearest past neighbours), how much the
auction premium and the app's offer discount cost (measured from the feed and the
offers), how long to hold (the length whose expected return per update is best),
what money is worth in season points (the report's own points-per-million), and
what selling a player costs in season points (the simulation's expected change).
Decisions use the EXPECTED drift: walked forward it matched what happened
(+20.8% predicted, +20.8% realised); a "confident low" bound understated it.

WHAT COUNTS IS AN UPDATE, NOT A CALENDAR DAY. The daily rows are not aligned
with the nightly value update, so every figure is "the next h updates".

THE REPORT OWNS WHO IS DISPENSABLE. A bench player is optionality, and the season
simulation prices it. This module sells one only when what the market pays beats
holding by more than the report says he is worth to the season.

WORDS COME FROM ONE PLACE. A decision carries a REASON -- a code and the numbers
behind it -- and say() is the only function that turns one into a sentence.
present() builds the whole view (headings, rows, the ping) from the decisions;
the page draws it and contains no wording of its own.
"""
from __future__ import annotations

import bisect
import csv
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from itertools import accumulate
from statistics import mean, median

# ------------------------------------------------------------ numbers from data
def steps(rows: list[dict]) -> dict[str, list[tuple[str, float]]]:
    """{player: [(day, % change from the previous daily row)]}, oldest first."""
    vals: dict[str, list[tuple[str, float]]] = {}
    for r in sorted(rows, key=lambda r: r["observed_at"]):
        try:
            vals.setdefault(r["ff_id"], []).append(
                (r["observed_at"][:10], float(r["value"])))
        except (KeyError, ValueError):
            pass
    return {k: [(b[0], 100 * (b[1] / a[1] - 1)) for a, b in zip(v, v[1:])
                if a[1] > 0] for k, v in vals.items()}


def belief(window: list[tuple], h: int) -> dict | None:
    """The expected outcome of the h updates that followed, and how much it rests on.

    The market moves TOGETHER (one repricing lifts every player on the same
    night), so `days` -- the calendar days the cases fall on -- is what counts as
    evidence, not the number of player-days. None when the window spans a single
    day: one repricing is not a pattern."""
    days = {day for _, _, day in window}
    if len(days) < 2:
        return None
    return {"mean": mean(out for _, out, _ in window), "n": len(window),
            "days": len(days)}


class Outlook:
    """What an update of a given size has been followed by, for every hold length.

    No buckets: a query takes the K most similar updates ever seen, K = sqrt(n)
    (the standard nearest-neighbour rule), so the resolution follows the data --
    fine where updates are common, wide where they are rare."""

    def __init__(self, by_player: dict[str, list[tuple[str, float]]]):
        self.hmax = max(1, int(median(len(s) for s in by_player.values())) // 4) \
            if by_player else 1
        self.obs: dict[int, list[tuple]] = {h: [] for h in range(1, self.hmax + 1)}
        for s in by_player.values():
            pref = [1.0, *accumulate((1 + c / 100 for _, c in s),
                                     lambda a, b: a * b)]
            for i, (day, step) in enumerate(s):
                for h in range(1, min(self.hmax, len(s) - 1 - i) + 1):
                    self.obs[h].append((step, 100 * (pref[i + 1 + h] / pref[i + 1]
                                                     - 1), day))
        for v in self.obs.values():
            v.sort()
        self.keys = {h: [o[0] for o in v] for h, v in self.obs.items()}

    def near(self, step: float, h: int) -> list[tuple]:
        """The K past updates closest in size to `step`, and what followed."""
        v, ks = self.obs[h], self.keys[h]
        k = max(3, round(math.sqrt(len(v))))
        lo = hi = bisect.bisect_left(ks, step)
        while hi - lo < k and (lo > 0 or hi < len(v)):
            if lo > 0 and (hi >= len(v) or step - ks[lo - 1] <= ks[hi] - step):
                lo -= 1
            else:
                hi += 1
        return v[lo:hi]

    def best(self, step: float | None, offer: float, premium: float) -> dict | None:
        """The hold length whose expected return per update, after the premium
        and the offer discount, is best -- with the belief behind it."""
        if step is None or not self.obs:
            return None
        best = None
        for h in self.obs:
            bel = belief(self.near(step, h), h)
            if bel is None:
                continue
            net = ((1 + bel["mean"] / 100) * offer / premium - 1) / h
            if best is None or net > best["net"]:
                best = {**bel, "h": h, "net": net}
        return best


def offer_ratios(offers: list[dict], teams: list[dict], value_at) -> list[float]:
    """offer / market value, for every offer the app has made on a player."""
    who = {t["player_team_id"]: t["player_name"] for t in teams
           if t.get("player_team_id")}
    seen, out = set(), []
    for o in offers:
        if not o.get("offer_id") or o["offer_id"] in seen:
            continue
        seen.add(o["offer_id"])
        v = value_at(who.get(o["player_team_id"], ""), o["created_at"])
        if v and float(o["money"] or 0) > 0:
            out.append(float(o["money"]) / v)
    return out


def auction_ratios(listings: list[dict], buys: list[dict]) -> list[float]:
    """price paid / ask, for every purchase that ends a free-market listing:
    the buy lands within minutes of the listing's own close."""
    ends = {}
    for r in listings:
        if r.get("seller") != "marketPlayerLeague" or not r.get("expires_at"):
            continue
        k = (r["player_id"], datetime.fromisoformat(r["expires_at"])
             .astimezone(timezone.utc))
        if k not in ends or r["observed_at"] > ends[k]["observed_at"]:
            ends[k] = r
    out = []
    for b in buys:
        at = datetime.fromisoformat(b["at"]).astimezone(timezone.utc)
        for (pid, close), r in ends.items():
            if pid == b["player_id"] and abs((at - close).total_seconds()) < 600 \
                    and float(r["sale_price"] or 0) > 0:
                out.append(float(b["amount"]) / float(r["sale_price"]))
                break
    return out


def report_view(ladder: list[dict], name_key) -> dict:
    """{key: (group, expected season points change if he is sold)} for my
    players, as the season report's ladder says. `name_key` maps a ladder
    name to a key.

    "offer" ROWS ARE INCLUDED. A currently-fielded starter carries no verdict
    under his own group ("field" is not one of the groups below -- the report
    is silent on starters by default), but the ladder still prices what
    losing him costs wherever he has a live received offer, and that is
    what a funding menu needs: not just "is he dispensable" but "what would
    selling him cost", for anyone with money on the table."""
    verdict = {}
    for r in ladder:
        k = name_key(r["name"])
        if r["where"] == "yours" and k \
                and r["group"] in ("sell", "keep", "out", "in", "offer") \
                and r.get("pts_mean") is not None:
            verdict[k] = (r["group"], r["pts_mean"])
    return verdict


def money_rate(moves: list[dict]) -> float:
    """Season points a million buys, as the report's OWN ranked moves measure it
    (their points-per-million). 0 when the report wants to do nothing."""
    rates = [m["value"] for m in moves if m.get("value") is not None]
    return median(rates) if rates else 0.0


def reserve(moves: list[dict]) -> float:
    """Cash the report's top-ranked move needs after its own funding -- its net
    cash out, premium included. The report decides what the cash is for first;
    the market only gets what that leaves."""
    return max(0.0, -moves[0]["net"]) if moves else 0.0


# ---------------------------------------------------------------- the decisions
def picks(listings: list[dict], last: dict[str, float], model: dict,
          cash: float) -> list[dict]:
    """Free-market listings expected to pay, best gain per million paid first.

    `model` = {outlook, offer, premium}. A listing is worth buying when what he
    is expected to fetch after holding beats what winning him costs; `max_bid`
    is where the two are equal."""
    out = []
    for l in listings:
        bel = model["outlook"].best(last.get(l["key"]), model["offer"],
                                    model["premium"])
        if bel is None:
            continue
        leave = l["value"] * (1 + bel["mean"] / 100) * model["offer"]
        pay = l["ask"] * model["premium"]
        if leave <= pay:
            continue
        out.append({**l, "last": last[l["key"]], "h": bel["h"], "n": bel["n"],
                    "days": bel["days"], "drift": bel["mean"], "gain": leave - pay,
                    "pay": pay, "max_bid": leave,
                    "fits": pay <= cash, "short": max(0.0, pay - cash),
                    "reason": {"code": "rising", "last": last[l["key"]],
                               "drift": bel["mean"], "h": bel["h"], "n": bel["n"],
                               "days": bel["days"], "ask": l["ask"], "pay": pay}})
    return sorted(out, key=lambda x: -x["gain"] / x["pay"])


def sells(bench: list[dict], last: dict[str, float], offers: dict[str, float],
          model: dict, verdict: dict, rate: float) -> tuple[list[dict], list[dict]]:
    """(sales, held back) among non-starters. `bench` [{key, name, value}].

    Selling now brings the app's offer if there is one, else what an offer is
    expected to be; holding is expected to bring the drift the history predicts.
    The difference is money, and the report says what money buys in season
    points (`rate`) and what the player is worth to the season (his expected
    points change if sold, `verdict`). Sold when the money buys more than he is
    worth PLUS what winning him back would cost (the auction premium), so a
    sale is only advised when it will still be right after the next update. A
    player the report has no verdict on is left alone."""
    out, held = [], []
    for p in bench:
        v = verdict.get(p["key"])
        bel = model["outlook"].best(last.get(p["key"]), model["offer"],
                                    model["premium"])
        if v is None or bel is None:
            continue
        group, exp_pts = v
        now = offers.get(p["key"]) or p["value"] * model["offer"]
        hold = p["value"] * (1 + bel["mean"] / 100) * model["offer"]
        gain = now - hold
        if gain <= 0:
            continue
        cost = 0.0 if group == "sell" else max(0.0, -exp_pts)
        benefit = rate * gain / 1e6
        back = rate * p["value"] * (model["premium"] - 1) / 1e6
        sale = benefit >= cost + back
        row = {**p, "offer": offers.get(p["key"]), "last": last.get(p["key"]),
               "gain": gain, "cost_pts": cost, "benefit_pts": benefit,
               "reason": {"code": "sale" if sale else "keep",
                          "now": now, "hold": hold, "h": bel["h"],
                          "offered": offers.get(p["key"]) is not None,
                          "cost": cost, "benefit": benefit}}
        (out if sale else held).append(row)
    return out, held


def fund(offers: dict[str, float], mine: dict, names: dict, xi: set,
        verdict: dict, rate: float, value: dict, last: dict, model: dict,
        need: float = 0.0) -> list[dict]:
    """Every squad player with a real received offer on the table, cheapest
    in season points first -- what selling him costs, whether he starts or
    not. This answers "where do I get the cash", nothing more: the report
    OWNS who is dispensable, so nobody here is picked FOR you, a starter
    least of all -- sells()/picks() already act where the arithmetic is a
    clear yes; this is a menu for when it is not.

    cost_pts IS ALREADY SEASON- AND XI-AWARE, not a guess bolted on here:
    it is `pts_mean` off the same ladder every other section reads, and that
    number comes from sim.band_acts() running a stand-alone sell of THIS
    player, alone, through the full season Monte Carlo -- best XI
    re-optimised inside every simulated week, an average-player phantom
    filling his slot. A key starter prices in the tens of points (Pablo
    Fornals: -60.0, 2026-09-25); a fringe bench player in tenths.

    TRIMMED TO `need`, NOT THE WHOLE SQUAD. Found 2026-09-25: listing every
    offered player -- 17 of them, most of the squad -- read as "sell all of
    these" when it meant "pick from these", so it is cut to the cheapest
    ones whose offers cover `need` plus ONE further option, never the rest.
    need=0.0 (the default) keeps everything, for a caller (a test, a script)
    that wants the full menu rather than a specific shortfall.

    money_now IS THE SAME "sell now vs. wait" READING sells() ALREADY MAKES
    for its own bench-only, obvious-yes cases -- the offer against what the
    price history says holding would be expected to bring (Outlook.best(),
    the identical nearest-neighbour model the buy side uses). Positive: the
    offer beats waiting. Negative: the price is trending up and the market
    is expected to pay more later -- selling now leaves that on the table.
    None when there is no reading yet (a brand-new update, nothing to
    compare against): the row still shows, points cost and all, just
    without a money-timing opinion. This is TIMING, not ranking: it says
    when to take a sale already worth making, not whether one is.

    RANKED ON net_pts = cost_pts - rate*offer/1e6 -- what selling him costs
    the season MINUS what that cash is worth put toward what the report
    already considers the best use of a million (its own measured
    points-per-million, the identical rate sells() converts a gain into
    points with). This is the direct answer to "can I use that money
    better elsewhere": net_pts <= 0 means yes, even setting the immediate
    need aside -- the cash outearns him wherever it goes. The ranking is
    ascending on net_pts, so the most attractive sale is always first,
    whether that means "costs you almost nothing" or "is a bargain outright".

    ONE CAVEAT WORTH KEEPING: each cost is marginal -- HIM ALONE, everyone
    else held as they are. Selling several players from this menu at once is
    not guaranteed to cost exactly the sum of their costs (two players in
    the same position interact), the same caveat every other one-at-a-time
    row on the ladder already carries. Only cash is summed below (`running`)
    -- points never are, and nothing here claims to have costed a COMBINED
    sale.

    A player with no report verdict is left off, same rule as sells(): an
    unpriced sale is not a sale this module can grade."""
    out = []
    for k, offer in offers.items():
        if k not in mine:
            continue
        v = verdict.get(k)
        if v is None:
            continue
        group, exp_pts = v
        cost = 0.0 if group == "sell" else max(0.0, -exp_pts)
        benefit = rate * offer / 1e6
        net = cost - benefit
        bel = model["outlook"].best(last.get(k), model["offer"],
                                    model["premium"])
        money_now, hold, h = None, None, None
        if bel is not None and value.get(k):
            hold = value[k] * (1 + bel["mean"] / 100) * model["offer"]
            money_now, h = offer - hold, bel["h"]
        out.append({"key": k, "name": names.get(k, k), "offer": offer,
                    "cost_pts": cost, "benefit_pts": benefit, "net_pts": net,
                    "starter": k in xi, "money_now": money_now,
                    "reason": {"code": "fund", "offer": offer, "cost": cost,
                               "benefit": benefit, "net": net,
                               "starter": k in xi, "money_now": money_now,
                               "hold": hold, "h": h}})
    out.sort(key=lambda p: (p["net_pts"], -p["offer"]))
    running = 0.0
    for p in out:
        running += p["offer"]
        p["running"] = running
    if need > 0:
        covered = next((i for i, p in enumerate(out) if p["running"] >= need),
                       len(out) - 1)
        out = out[:covered + 2]      # the cover point, plus one further option
    return out


# ------------------------------------------------------------------- the words
def _m(v: float) -> str:
    from ffcore.parse import fmt_money
    return fmt_money(v)


def _p(v: float) -> str:
    return "%+.1f%%" % v


def _pts(v: float) -> str:
    return "%.1f" % v if v < 10 else "%.0f" % v


def say(r: dict) -> str:
    """The ONE place a reason becomes a sentence."""
    c = r["code"]
    if c == "rising":
        return ("last update %s; the history says %s over the next %d "
                "updates (%d similar cases on %d different days); ask %s, "
                "expect to pay ~%s"
                % (_p(r["last"]), _p(r["drift"]), r["h"], r["n"], r["days"],
                   _m(r["ask"]), _m(r["pay"])))
    if c in ("sale", "keep"):
        how = ("the app offers %s" % _m(r["now"]) if r["offered"]
               else "an offer would bring ~%s" % _m(r["now"]))
        more = _m(r["now"] - r["hold"])
        if c == "sale":
            return ("%s against ~%s expected from holding %d updates: %s more, "
                    "worth %s season points at what the report's buys return, "
                    "for the %s he is expected to cost the season"
                    % (how, _m(r["hold"]), r["h"], more,
                       _pts(r["benefit"]), _pts(r["cost"])))
        return ("%s against ~%s expected from holding %d updates, but the %s "
                "gained is worth %s season points and he is expected to add "
                "%s -- the report keeps him"
                % (how, _m(r["hold"]), r["h"], more,
                   _pts(r["benefit"]), _pts(r["cost"])))
    if c == "fund":
        where = "currently in your eleven" if r["starter"] else "on the bench"
        if r["net"] <= 0:
            verdict = ("costs nothing net -- the cash outearns him elsewhere"
                      if r["cost"] > 0 else
                      "free to take, the report already has him leaving")
        else:
            verdict = "a net %s season points, cash included" % _pts(r["net"])
        if r["money_now"] is None:
            timing = ""
        elif r["money_now"] >= 0:
            timing = "; the offer beats waiting %d updates by %s" \
                % (r["h"], _m(r["money_now"]))
        else:
            timing = ("; the price is trending up -- waiting %d updates is "
                     "expected to bring ~%s more" % (r["h"], _m(-r["money_now"])))
        return "%s, %s%s; %s" % (_m(r["offer"]), where, timing, verdict)
    raise ValueError("no wording for reason code %r" % c)


def present(out: dict) -> dict:
    """The whole display, from the decisions: what the page draws and the ping
    says. Rows are {name, detail, right: [main, caption]}; a section's `tone`
    is all the page needs to colour it."""
    def row(p, right):
        return {"name": p["name"], "detail": say(p["reason"]), "right": right}

    buys = [row(p, ["≤ " + _m(p["max_bid"]),
                    "bid" if p["fits"] else "bid; needs %s more" % _m(p["short"])])
            for p in out["picks"]]
    sold = [row(p, [_m(p["value"]),
                    "offer " + _m(p["offer"]) if p["offer"] else ""])
            for p in out["sells"]]
    held = [row(p, [_m(p["value"]), ""]) for p in out["held"]]
    funded = [row(p, [_m(p["offer"]),
                      "net %s pts" % _pts(p["net_pts"])
                      if p["net_pts"] > 0 else "a net gain"])
             for p in out["fund"]]
    doable = [p for p in out["picks"] if p["fits"]]      # the ping is for what you can act on
    ping = ("\nBuy: " + ", ".join("%s (bid up to %s)" % (p["name"], _m(p["max_bid"]))
                                  for p in doable[:2]) if doable else "")
    ping += ("\nSell: " + ", ".join(p["name"] for p in out["sells"][:3])
             if sold else "")
    hold = ("; %s held back for the report's top move" % _m(out["reserve"])
            if out["reserve"] > 0 else "")
    sections = [
        {"label": "BUY — free market", "tone": "buy", "rows": buys,
         "empty": "Nothing on the free market is expected to beat the cost "
                  "of tying the cash up."},
        {"label": "SELL — not starting", "tone": "sell", "rows": sold},
        {"label": "HELD BACK — the report keeps them", "tone": "held",
         "rows": held}]
    # ONLY WHEN SOMETHING IS SHORT (out["fund"] is empty otherwise, set by
    # main()) -- a menu of real offers already on the table, cheapest in
    # season points first, trimmed to what covers the actual shortfall (plus
    # one further option) rather than the whole squad: listing all 17 read as
    # "sell everyone" when it meant "pick from these" (found 2026-09-25).
    # Never a recommendation on its own: sells() already acts where selling
    # is a clear yes, and a starter sale is too big a call for this to make
    # FOR you, so every candidate shown is priced and left to you.
    if funded:
        sections.append({
            "label": "FUND — %s short, not a recommendation: %d option(s) "
                     "that would cover it, cheapest first"
                     % (_m(out["fund_need"]), len(funded)),
            "tone": "fund", "rows": funded})
    return {
        "heading": "MARKET",
        "summary": "%s to spend%s" % (_m(out["spendable"]), hold),
        "sections": sections,
        "ping": ping}


# ------------------------------------------------------------------- the log
LOG = ["day", "run_at", "action", "key", "name", "ask", "value", "offer",
       "last", "horizon", "max_bid", "cash", "why"]


def record(path, out: dict, now) -> int:
    """Write today's recommendations to the decision log, AT THE TIME they are
    made, so what was advised can never be reconstructed after the outcome is
    known. Keyed on the market close they are for (22:24 Madrid): a later run
    before that close replaces the day's rows, so the LAST word before the
    close is the one that stays."""
    close = now.replace(hour=22, minute=24, second=0, microsecond=0)
    day = (close if now < close else close + timedelta(days=1)).strftime("%Y-%m-%d")
    keep = []
    if path.exists():
        with open(path, newline="", encoding="utf-8") as fh:
            keep = [r for r in csv.DictReader(fh) if r["day"] != day]
    base = {"day": day, "run_at": now.strftime("%Y-%m-%dT%H:%M"),
            "cash": round(out["cash"])}
    new = [{**base, "action": "BUY", "key": p["key"], "name": p["name"],
            "ask": round(p["ask"]), "value": round(p["value"]), "last": p["last"],
            "horizon": p["h"], "max_bid": round(p["max_bid"]),
            "why": say(p["reason"])} for p in out["picks"]]
    new += [{**base, "action": "SELL", "key": p["key"], "name": p["name"],
             "value": round(p["value"]), "offer": round(p["offer"] or 0),
             "last": p["last"], "horizon": p["reason"]["h"],
             "why": say(p["reason"])} for p in out["sells"]]
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=LOG, restval="", extrasaction="ignore")
        w.writeheader()
        w.writerows(keep + new)      # rows from an older layout keep what still fits
    return len(new)


def recently(path, now) -> set[tuple[str, str]]:
    """{(key, action)} still inside the hold the advice assumed: what was advised
    within its own horizon (in updates, about days) is not reversed the next day."""
    if not path.exists():
        return set()
    today = datetime.strptime(now.strftime("%Y-%m-%d"), "%Y-%m-%d")
    with open(path, newline="", encoding="utf-8") as fh:
        return {(r["key"], r["action"]) for r in csv.DictReader(fh)
                if r.get("horizon") and datetime.strptime(r["day"], "%Y-%m-%d")
                < today <= datetime.strptime(r["day"], "%Y-%m-%d")
                + timedelta(days=int(float(r["horizon"])))}


# ------------------------------------------------------------------------ main
def main() -> None:
    import decide
    from ffcore.text import norm
    from ffcore.tidy import (DECISIONS, MADRID, REPORTS, TIDY, Market,
                             load_market_frozen, read_csv, run_now)

    u = decide.load()
    rows = read_csv(TIDY / "market.csv")
    by_player = steps(rows)
    mk = Market(load_market_frozen())
    value_at = lambda name, when: (lambda v: v.value if v else None)(
        mk.at(name, datetime.fromisoformat(when).astimezone(timezone.utc)))
    offer_r = offer_ratios(read_csv(TIDY / "api_offers.csv"),
                           read_csv(TIDY / "api_teams.csv"), value_at)
    paid_r = auction_ratios(read_csv(TIDY / "api_market.csv"),
                            [a for a in read_csv(TIDY / "api_activity.csv")
                             if a["kind"] == "buy"])
    if not offer_r or not paid_r or not by_player:
        print("flip: not enough history to measure the cost of trading yet")
        return
    offer, premium = mean(offer_r), mean(paid_r)
    model = {"outlook": Outlook(by_player), "offer": offer, "premium": premium}

    newest = max(r["observed_at"] for r in rows)
    last = {r["ff_id"]: float(r["delta_pct_1d"]) for r in rows
            if r["observed_at"] == newest
            and r.get("delta_pct_1d") not in (None, "", "None")}
    name, price, value = u.view("name"), u.view("price"), u.view("value")
    _, xi = u.current_xi
    mine = u.state.squads.get(u.me, {})
    free = [{"key": k, "name": name.get(k, k), "ask": price[k],
             "value": value.get(k) or price[k]}
            for k, r in u.view("route").items() if r == "free" and k in price]
    bench = [{"key": k, "name": name.get(k, k), "value": value.get(k, 0.0)}
             for k in mine if k not in xi]
    by_name = {norm(name.get(k, k)): k for k in mine}

    verdict, moves = {}, []
    try:
        doc = json.loads((REPORTS / "decisions.json").read_text())
        verdict = report_view(doc["ladder"], lambda n: by_name.get(norm(n)))
        moves = doc["moves"]
    except (OSError, ValueError, KeyError):
        pass
    held_back = reserve(moves)
    now = run_now().astimezone(MADRID)
    recent = recently(DECISIONS / "flip_log.csv", now)
    sales, kept = sells(bench, last, dict(u.received_offers), model, verdict,
                        money_rate(moves))
    spend = max(0.0, u.cash - held_back)
    pick_list = [p for p in picks(free, last, model, spend)
                if (p["key"], "SELL") not in recent]
    short = max([held_back - u.cash] + [p["short"] for p in pick_list],
               default=0.0)
    funding = (fund(dict(u.received_offers), mine, name, xi, verdict,
                    money_rate(moves), value, last, model, need=short)
              if short > 0 else [])
    out = {"cash": u.cash, "reserve": held_back, "spendable": spend,
           "fund_need": short,
           "picks": pick_list,
           "sells": [p for p in sales if (p["key"], "BUY") not in recent],
           "held": kept, "fund": funding,
           "measured": {"offer": offer, "premium": premium,
                        "offers": len(offer_r), "auctions": len(paid_r),
                        "points_per_million": money_rate(moves)}}
    out["view"] = present(out)
    path = REPORTS / "decisions.json"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["flip"] = out
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n",
                        encoding="utf-8")
    except (OSError, ValueError):
        print("flip: no decisions.json to attach to")
    logged = record(DECISIONS / "flip_log.csv", out, now)
    print("flip: %d buy(s), %d sale(s) logged; %s to spend (%s held back for the "
          "report's own targets); offers pay %.3fx value (%d), auctions cost "
          "%.3fx ask (%d)"
          % (logged - len(out["sells"]), len(out["sells"]), _m(spend),
             _m(held_back), offer, len(offer_r), premium, len(paid_r)))


# -------------------------------------------------------------------- self-test
def _selftest() -> None:
    # A history where a big rise is followed by more rise, a fall by more fall.
    days = ["2026-08-%02d" % d for d in range(10, 30)]
    rows = []
    for k, g in (("up", 1.05), ("down", 0.98), ("flat", 1.0)):
        for i in range(12):
            for n, d in enumerate(days):
                rows.append({"observed_at": d + "T2359Z", "ff_id": k + str(i),
                             "value": str(100 * g ** n)})
    by = steps(rows)
    assert by["up0"][0] == ("2026-08-11", by["up0"][0][1]) and abs(by["up0"][0][1] - 5.0) < 1e-9
    assert by["flat0"][0][1] == 0.0
    ol = Outlook(by)
    assert ol.hmax == 4 and set(ol.obs) == {1, 2, 3, 4}       # median 19 updates // 4
    # NO BUCKETS: the K nearest past updates to +5% are the 'up' players, to -2% the 'down' ones
    assert {round(o[0]) for o in ol.near(5.0, 3)} == {5}
    assert {round(o[0]) for o in ol.near(-2.0, 3)} == {-2}
    assert all(abs(o[1] - (1.05 ** 3 - 1) * 100) < 1e-6 for o in ol.near(5.0, 3))
    # uncertainty is across DAYS: many players on one day are one observation
    one_day = [(5.0, 10.0, "d1")] * 50
    assert belief(one_day, 1) is None
    two_days = [(5.0, 10.0, "d1"), (5.0, 20.0, "d2")] * 10
    bel = belief(two_days, 1)
    assert bel == {"mean": 15.0, "n": 20, "days": 2}, bel
    top = ol.best(5.0, 0.98, 1.05)
    assert top["h"] >= 1 and top["net"] > 0 and top["days"] >= 2, top
    assert ol.best(-2.0, 0.98, 1.05)["net"] < 0 and ol.best(None, 0.98, 1.05) is None
    model = {"outlook": ol, "offer": 0.98, "premium": 1.05}

    # what the trade costs is MEASURED
    teams = [{"player_team_id": "9", "player_name": "Zed"}]
    offs = [{"offer_id": "1", "player_team_id": "9", "money": "5500000",
             "created_at": "2026-09-01T22:24:00+02:00"},
            {"offer_id": "1", "player_team_id": "9", "money": "5500000",
             "created_at": "2026-09-01T22:24:00+02:00"},
            {"offer_id": "", "player_team_id": "9", "money": "", "created_at": ""}]
    assert offer_ratios(offs, teams, lambda n, w: 5e6 if n == "Zed" else None) == [1.1]
    close = "2026-09-02T22:24:00+02:00"
    lst = [{"seller": "marketPlayerLeague", "player_id": "7", "expires_at": close,
            "sale_price": "10000000", "observed_at": "a"},
           {"seller": "marketPlayerTeam", "player_id": "8", "expires_at": close,
            "sale_price": "10000000", "observed_at": "a"}]
    buys = [{"player_id": "7", "at": "2026-09-02T22:24:10+02:00", "amount": "10400000"},
            {"player_id": "8", "at": "2026-09-02T22:24:10+02:00", "amount": "99"},   # not a free-market listing
            {"player_id": "7", "at": "2026-09-05T10:00:00+02:00", "amount": "1"}]    # not at the close
    assert auction_ratios(lst, buys) == [1.04], auction_ratios(lst, buys)

    # the report, read: verdicts from its ladder; cash and points-per-million from
    # its own ranked MOVES (net is cash in, so a cash need is -net; a raid whose
    # clause premium is paid out is a bigger need than the player's value)
    ladder = [{"name": "Kept", "where": "yours", "group": "keep", "pts_mean": -0.5},
              {"name": "Free", "where": "yours", "group": "sell", "pts_mean": -3.0},
              # a fielded starter carries no "field" verdict, but the ladder
              # still prices him wherever he has a real offer -- report_view
              # must pick that up, or a starter can never be priced at all
              {"name": "Star", "where": "yours", "group": "offer",
               "pts_mean": -9.0},
              {"name": "Rival", "where": "x", "group": "raid", "pts_mean": None}]
    ver = report_view(ladder, {"Kept": "k", "Free": "f", "Star": "s"}.get)
    assert ver == {"k": ("keep", -0.5), "f": ("sell", -3.0),
                   "s": ("offer", -9.0)}, ver
    moves = [{"net": -18.4e6, "value": 1.0}, {"net": -12.3e6, "value": 0.6}, {"net": 5e6, "value": None}]
    assert reserve(moves) == 18.4e6 and money_rate(moves) == 0.8
    assert reserve([{"net": 3e6, "value": 1.0}]) == 0.0      # a move that RAISES cash reserves none
    assert reserve([]) == 0.0 and money_rate([]) == 0.0

    # buys: only what is expected to beat the friction
    lst = [{"key": "a", "name": "Riser", "ask": 10e6, "value": 10e6},
           {"key": "b", "name": "Faller", "ask": 10e6, "value": 10e6},
           {"key": "c", "name": "NoData", "ask": 10e6, "value": 10e6}]
    got = picks(lst, {"a": 5.0, "b": -2.0}, model, 50e6)
    assert [g["name"] for g in got] == ["Riser"], got
    g = got[0]
    assert g["fits"] and g["gain"] > 0 and g["max_bid"] >= g["pay"], g
    poor = picks(lst, {"a": 5.0}, model, 1e6)[0]
    assert poor["fits"] is False and abs(poor["short"] - (poor["pay"] - 1e6)) < 1e-6, poor
    # ranked by gain per million paid, not by gain: the dear one gains more in
    # money (3.9M against 1.5M) but the cheap one earns more per million
    two = [{"key": "a", "name": "Dear", "ask": 30e6, "value": 30e6},
           {"key": "d", "name": "Cheap", "ask": 4e6, "value": 5e6}]
    ranked = picks(two, {"a": 5.0, "d": 5.0}, model, 99e6)
    assert max(ranked, key=lambda r: r["gain"])["name"] == "Dear", ranked
    assert [r["name"] for r in ranked] == ["Cheap", "Dear"], ranked

    # sells: money must buy more season points than the player is worth
    bench = [{"key": "k", "name": "Kept", "value": 5e6},
             {"key": "f", "name": "Free", "value": 5e6},
             {"key": "u", "name": "Unjudged", "value": 5e6}]
    fall = {"k": -2.0, "f": -2.0, "u": -2.0}
    sold, held = sells(bench, fall, {}, model, ver, rate=0.1)
    assert [x["name"] for x in sold] == ["Free"], (sold, held)   # group sell: costs the season 0
    assert [x["name"] for x in held] == ["Kept"], held           # 0.1 pts/M is worth less than the 0.5 he adds
    # the same player, but money buys more: now the sale beats what he adds
    sold2, _ = sells(bench, fall, {}, model, ver, rate=8.0)
    assert "Kept" in [x["name"] for x in sold2], sold2
    # a player nobody has a verdict on is never advised; no gain from selling: no sale
    assert "Unjudged" not in [x["name"] for x in sold2 + held]
    assert sells(bench[:1], {"k": 5.0}, {}, model, ver, 8.0) == ([], [])   # rising: hold
    # a sale must also clear what buying him back would cost (the auction premium):
    # a gain smaller than that is not worth advising, because it can be undone only at a loss
    thin = [{"key": "f", "name": "Free", "value": 5e6}]
    top_fall = model["outlook"].best(-2.0, 0.98, 1.05)
    back_pts = 8.0 * 5e6 * 0.05 / 1e6
    edge = 5e6 * (1 + top_fall["mean"] / 100) * 0.98 + (back_pts * 1e6 / 8.0)
    assert sells(thin, {"f": -2.0}, {"f": edge - 1e4}, model, ver, 8.0)[0] == []
    assert len(sells(thin, {"f": -2.0}, {"f": edge + 1e4}, model, ver, 8.0)[0]) == 1

    # fund(): every squad player with a real offer, RANKED ON NET points --
    # cost minus what the cash is worth elsewhere (rate*offer) -- not on
    # cost alone. A costly STARTER with a huge offer (Star: costs 9.0, but
    # a 30M offer at rate=1.0 is worth 30.0) ranks ABOVE a nearly-free bench
    # player with a small one, and a small offer that barely covers a real
    # cost (Kept: costs 0.5, a 300K offer is worth 0.3) ranks last, net
    # POSITIVE -- an honest "this one is not worth it" signal cost-only
    # sorting could never give.
    my_squad = {"k": 1, "f": 1, "s": 1, "u": 1}
    offers = {"k": 3e5, "f": 2e6, "s": 30e6, "u": 1e6, "outside": 9e6}
    names = {"k": "Kept", "f": "Free", "s": "Star", "u": "Unjudged"}
    menu = fund(offers, my_squad, names, {"s"}, ver, rate=1.0, value={},
               last={}, model=model)
    # "outside" is not on the squad, "u" has no verdict: both excluded
    assert [p["name"] for p in menu] == ["Star", "Free", "Kept"], menu
    assert abs(menu[0]["net_pts"] - (9.0 - 30.0)) < 1e-9, menu[0]
    assert abs(menu[1]["net_pts"] - (0.0 - 2.0)) < 1e-9, menu[1]
    assert abs(menu[2]["net_pts"] - (0.5 - 0.3)) < 1e-9 and menu[2]["net_pts"] > 0, menu[2]
    assert menu[0]["starter"] and not menu[1]["starter"], menu
    assert menu[-1]["running"] == 30e6 + 2e6 + 3e5, menu
    # no price history was given (value={}): no money-timing opinion
    assert all(p["money_now"] is None for p in menu), menu

    # money_now: the SAME "sell now vs. wait" reading sells() uses, now on
    # a full-priced player. "Riser" (the up-cohort, +5%/update) is worth
    # more waited-for than offered now; "Faller" (down, -2%/update) is not.
    priced = fund({"a": 5e6, "b": 5e6}, {"a": 1, "b": 1},
                 {"a": "Riser", "b": "Faller"}, set(),
                 {"a": ("keep", -1.0), "b": ("keep", -1.0)}, rate=1.0,
                 value={"a": 5e6, "b": 5e6}, last={"a": 5.0, "b": -2.0},
                 model=model)
    by_name = {p["name"]: p for p in priced}
    assert by_name["Riser"]["money_now"] < 0, by_name["Riser"]
    assert by_name["Faller"]["money_now"] > 0, by_name["Faller"]

    # need TRIMS the menu: 5 candidates on the table, but listing all of
    # them read as "sell everyone" when it meant "pick from these" -- cut to
    # what covers the shortfall plus ONE further option, never the rest
    five = {"a": 1, "b": 1, "c": 1, "d": 1, "e": 1}
    five_offers = {"a": 1e6, "b": 2e6, "c": 3e6, "d": 4e6, "e": 5e6}
    five_names = {k: k.upper() for k in five}
    five_ver = {k: ("keep", -1.0) for k in five}
    whole = fund(five_offers, five, five_names, set(), five_ver, rate=0.1,
                value={}, last={}, model=model)
    assert len(whole) == 5, whole                          # need=0: everything
    trimmed = fund(five_offers, five, five_names, set(), five_ver, rate=0.1,
                   value={}, last={}, model=model, need=2.5e6)
    # cheapest-net first is A (1M, smallest offer, ties broken by net_pts);
    # covering 2.5M needs A+B+C (1+2+3=6M >= 2.5M at the ties this fixture
    # produces) plus one further -- never all five
    assert 2 <= len(trimmed) < 5, trimmed
    assert sum(p["offer"] for p in trimmed[:-1]) < 2.5e6 \
        or len(trimmed) <= 2, trimmed
    assert trimmed[-2]["running"] >= 2.5e6, trimmed

    # the words: one function, every number from the reason
    out = {"cash": 20e6, "reserve": 10e6, "spendable": 10e6, "picks": got,
           "sells": sold, "held": held, "fund": [], "fund_need": 0.0}
    v = present(out)
    assert v["summary"] == "10.00M to spend; 10.00M held back for the report's top move", v["summary"]
    tight = present({**out, "picks": [poor]})["sections"][0]["rows"][0]["right"]
    assert tight[1].startswith("bid; needs ") and tight[1].endswith(" more"), tight
    lab = {s["tone"]: s for s in v["sections"]}
    assert lab["buy"]["rows"][0]["name"] == "Riser" and "bid" in lab["buy"]["rows"][0]["right"]
    assert "+5.0%" in lab["buy"]["rows"][0]["detail"], lab["buy"]["rows"][0]
    assert "the report keeps him" in lab["held"]["rows"][0]["detail"]
    assert v["ping"].startswith("\nBuy: Riser (bid up to") and "Sell: Free" in v["ping"], v["ping"]
    assert present({**out, "picks": [], "sells": [], "held": []})["ping"] == ""
    # ...and only what you can afford: an unaffordable pick stays on the page
    # (with what is missing) but does not go in the notification
    assert "Buy:" not in present({**out, "picks": [poor]})["ping"]
    fund_view = present({**out, "fund": menu, "fund_need": 12e6})
    fund_sec = {s["tone"]: s for s in fund_view["sections"]}["fund"]
    assert [r["name"] for r in fund_sec["rows"]] == ["Star", "Free", "Kept"]
    assert fund_sec["rows"][0]["right"] == ["30.00M", "a net gain"], \
        fund_sec["rows"][0]
    assert fund_sec["rows"][2]["right"][1].startswith("net "), fund_sec["rows"][2]
    assert "a net" in fund_sec["rows"][0]["detail"] \
        or "outearns" in fund_sec["rows"][0]["detail"], fund_sec["rows"][0]
    # the label states the real shortfall and says, plainly, it is not an
    # instruction to sell everything shown
    assert "12.00M short" in fund_sec["label"] and "not a recommendation" \
        in fund_sec["label"] and "3 option" in fund_sec["label"], fund_sec
    assert not any(s["tone"] == "fund" for s in v["sections"]), \
        "an empty fund list must not draw a section"
    try:
        say({"code": "nope"})
        raise AssertionError("an unknown reason must not be papered over")
    except ValueError:
        pass

    # the decision log: last word before the close wins; after it, tomorrow's
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "log.csv"
        assert record(p, out, datetime(2026, 9, 24, 21, 45)) == 2
        assert record(p, {**out, "picks": []}, datetime(2026, 9, 24, 22, 10)) == 1
        assert [r["action"] for r in csv.DictReader(open(p))] == ["SELL"]
        record(p, out, datetime(2026, 9, 24, 23, 0))
        assert sorted({r["day"] for r in csv.DictReader(open(p))}) == ["2026-09-24", "2026-09-25"]
        # no whipsaw: advised on the 24th with a 2-update hold -> not reversed until the 27th
        q = Path(d) / "l2.csv"
        with open(q, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=LOG, restval="")
            w.writeheader()
            w.writerow({"day": "2026-09-24", "action": "SELL", "key": "a", "horizon": "2"})
            w.writerow({"day": "2026-09-24", "action": "BUY", "key": "b", "horizon": ""})
        assert recently(q, datetime(2026, 9, 25, 12, 0)) == {("a", "SELL")}
        assert recently(q, datetime(2026, 9, 27, 12, 0)) == set()
        assert recently(q, datetime(2026, 9, 24, 12, 0)) == set()      # not before it was advised
    # a log written by an older layout must never break a run
    with tempfile.TemporaryDirectory() as d:
        old = Path(d) / "old.csv"
        old.write_text("day,run_at,action,key,name,expected_pct\n2026-09-20,x,BUY,k,K,12.5\n")
        assert recently(old, datetime(2026, 9, 24, 12, 0)) == set()
        record(old, out, datetime(2026, 9, 24, 21, 45))
        assert [r["day"] for r in csv.DictReader(open(old))][0] == "2026-09-20"
    print("flip self-test OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        main()
