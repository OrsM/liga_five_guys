"""
sim.py — the simulation, written out as the report it is going to become.

    python src/sim.py             # writes reports/sim.md
    python src/sim.py --selftest

ONE TABLE, ONE QUESTION: for every move I could make, how much does it change
where I finish. `decide.py` answers that; this prints it. There is no metric to
explain, no threshold to tune and no verdict vocabulary, because the column IS
the answer — a move that gains nothing shows a Δ of nothing.

THIS IS THE REPORT NOW. It was published beside the board for one afternoon,
which was long enough: priced in each other's units the two disagreed, and the
disagreement was not a tie. The board could not see a rival's player at all —
62 of the 83 acquirable — because every candidate list it built skipped
anything already owned, and the clause that makes them buyable sits on the row
it skipped. Where both could see, they agreed on the buy and the board named
the wrong funder, by 8 points of P(win).

WHAT THE BOARD WAS BETTER AT, and it is still true: it could value cash, and
this cannot. That is why the one verdict left here is dead weight — a man who
starts in none of the remaining jornadas — and why nothing here ever tells you
to sell for the money. The approximations under the forecast all flatter a
lead; they are listed at the foot of every page this writes, read off the data
rather than remembered.

WHY A SEPARATE FILE and not a section inside report.py: report.py is the old
metric zoo, and most of it is scheduled for deletion. A generator that writes
its own file is what digest.py already expects, and when the board goes this
file stays exactly as it is instead of being cut out of a 2,300-line module.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import json  # noqa: E402
import os as _os  # noqa: E402
from pathlib import Path  # noqa: E402

from decide import dead_weight  # noqa: E402,F401
from ffcore.parse import fmt_money  # noqa: E402
from ffcore.render import title_name  # noqa: E402
from ffcore.tidy import REPORTS, write_lines  # noqa: E402

OUT = "sim.md"

# Shared with report.py, which writes the squad half of it before this runs.
# In .runtime/ (gitignored): the file is a signal for a notifier, not a
# document, and its EXISTENCE is the signal — an empty alerts file that has to
# be read to discover it is empty is how "no news" gets pushed to a phone.
ALERTS = Path(_os.environ.get("LFG_ALERTS", ".runtime/alerts.md"))

# How many moves the table prints. The tail is options the simulation has
# already said are worth less than the ones above them, and a phone screen is
# the constraint that matters.
SHOW = 8


def _net(v) -> str:
    """What a move does to the balance, with the sign always explicit.

    `fmt_money` prints a bare amount, and a bare amount in a column where the
    direction is the point reads as a cost whichever way it goes: "4.00M" next
    to "-14.13M" looks like another thing being spent.
    """
    return ("+" if v > 0 else "") + fmt_money(v)


def _pts(v) -> str:
    """Points, with a thousands separator — season totals run to four
    figures and 1439 next to 1316 is two numbers you have to read twice."""
    return "{:,.0f}".format(v)


def squad_value(u) -> float:
    """What the squad would raise. `proceeds` is exactly that, per player."""
    return sum(u.proceeds.values())


def fielded_shape(u) -> str:
    """The formation you are ACTUALLY playing, from the marks at the last log.

    Without it "play 4-5-1" is advice you cannot check: you have no way to
    know whether it is what you are already doing. xi.py logs the marks with
    the hours left, so the newest row is the eleven standing now.
    """
    from ffcore.tidy import DECISIONS, read_csv

    rows = [r for r in read_csv(DECISIONS / "xi_fielded.csv") if r.get("xi")]
    if not rows:
        return ""
    return shape(u, rows[-1]["xi"].split("|"))


def header(u, base, n_actions: int, locks_h=None) -> list[str]:
    """The lines above the table: when it locks, then where I finish.

    THE DEADLINE LEADS. A decision you have missed is not a decision, and
    everything below it is worth reading only if there is still time to act.

    THE BAND IS NOT DECORATION. The mean of a simulated season is a number the
    league will never produce; the 10-90 interval is the honest form of the
    same statement, and printing the first without the second is how a
    forecast gets read as a fixture.
    """
    from ffcore.season import best_xi

    lo, hi = base.band(u.me)
    val = squad_value(u)
    ctx = []
    if locks_h is not None:
        ctx.append("**Locks in %s**"
                   % ("%.0fh" % locks_h if locks_h < 48
                      else "%.0f days" % (locks_h / 24)))
    # Cash carries the emphasis when it is negative, and nothing else does.
    # Over budget mid-window is allowed and over it at the lock is not, so a
    # minus sign here is the one fact that outranks the table — as a NUMBER,
    # not as a paragraph explaining itself.
    ctx += ["squad %s" % fmt_money(val),
            ("**cash %s**" if u.cash < 0 else "cash %s") % fmt_money(u.cash),
            "total %s" % fmt_money(val + u.cash)]

    exp = u.forecaster.expected(decide_choosable(u))
    want = shape(u, best_xi(u.state.squads.get(u.me, {}), exp))
    now = fielded_shape(u)
    form = ("**play %s** (now %s)" % (want, now)) if now and now != want \
        else "play %s" % want
    return [" · ".join(ctx), "",
            "%s · finish %.2f · win %.0f%% · season %s–%s"
            % (form, base.expected_position(),
               100 * base.position().get(1, 0.0), _pts(lo), _pts(hi)),
            ""]


def short(key, u) -> str:
    """A player's surname, unless somebody else in the squad shares it.

    The `give up` cell can hold three men and it renders on a 390px screen.
    Surnames buy back most of that width — but only where they still identify
    somebody, so the check is against the squad rather than assumed.
    """
    full = title_name(u.name.get(key, key))
    last = full.split()[-1] if full.split() else full
    clash = sum(1 for k in u.state.squads.get(u.me, {})
                if title_name(u.name.get(k, k)).split()[-1:] == [last])
    return full if clash > 1 else last


def _bar(u) -> float:
    """The weakest man in the eleven you would field — the line on the ladder."""
    from ffcore.season import best_xi
    exp = u.forecaster.expected(decide_choosable(u))
    xi = best_xi(u.state.squads.get(u.me, {}), exp)
    return min((exp.get(k, 0.0) for k in xi), default=0.0)


def ladder_rows(u, rows, saves=None) -> list[dict]:
    """The grouped plan as data, so the phone draws the same one table.

    Same groups, same order, same numbers. Two renderers drawing different
    tables is how this report came to contradict itself; there is one shape
    and both read it.
    """
    from ffcore.season import best_xi

    exp = u.forecaster.expected(decide_choosable(u))
    mine = u.state.squads.get(u.me, {})
    xi = set(best_xi(mine, exp))
    dead = {k for k, _ in decide_dead(u)}
    won = {r["action"].buy: r for r in rows if r["action"].buy}
    bar = min((exp.get(k, 0.0) for k in xi), default=0.0)
    spare = sum(v for _k, v in decide_dead(u))

    def cell(k, group, where, money, pts, note=""):
        return {"name": title_name(u.name.get(k, k)),
                "pos": u.pos.get(k, ""), "start": u.start.get(k, 0.0),
                "xpts": exp.get(k, 0.0), "group": group, "where": where,
                "money": money, "pts": pts, "note": note}

    out = []
    for k in sorted(xi, key=lambda k: -exp.get(k, 0.0)):
        out.append(cell(k, "field", "yours", None, None))
    for k in sorted((k for k in mine if k not in xi and k not in dead),
                    key=lambda k: -exp.get(k, 0.0)):
        out.append(cell(k, "keep", "yours", None, None))
    for k in sorted(dead, key=lambda k: -exp.get(k, 0.0)):
        out.append(cell(k, "sell", "yours", u.proceeds.get(k, 0.0), None))
    rest = [k for k in u.price if k not in mine and exp.get(k, 0.0) > bar]
    for k in sorted((k for k in rest if k in won), key=lambda k: -exp.get(k, 0.0)):
        r = won[k]
        out.append(cell(k, "buy", u.owner.get(k) or "free agent",
                        -r["action"].net, r["d_pts"]))
    for k in sorted((k for k in rest if k not in won
                     and u.price[k] > u.cash + spare),
                    key=lambda k: -exp.get(k, 0.0)):
        out.append(cell(k, "save", u.owner.get(k) or "free agent",
                        -(u.price[k] - u.cash - spare),
                        (saves or {}).get(k), "short"))
    for k in sorted((k for k in rest if k not in won
                     and u.price[k] <= u.cash + spare),
                    key=lambda k: -exp.get(k, 0.0)):
        out.append(cell(k, "pass", u.owner.get(k) or "free agent",
                        -u.price[k], None))
    return out




def price_saves(u, keys, base, seed: int = 1) -> dict:
    """What a player you cannot yet afford would be worth if you could.

    "Can't afford" is not an answer to whether saving toward him is worth it,
    and on the day this was written the two out of reach were the second and
    fourth best players on the board — one of them 7.89M short once the dead
    weight is sold, and worth MORE than the best move you can afford. Scored
    exactly like an affordable move, funded by everything spare, and marked
    conditional wherever it is printed.
    """
    import decide

    keys = list(keys)
    if not keys:
        return {}
    dead = tuple(sorted(k for k, _ in decide_dead(u)))
    got = sum(v for _k, v in decide_dead(u))
    # ONE PASS, like the ranking. Scored one at a time these were two full
    # simulations on their own — as much work again as ranking every move.
    acts = [decide.Action("buy", buy=k, sell=dead, cost=u.price.get(k, 0.0),
                          proceeds=got) for k in keys]
    scored = decide._score_many(u, [decide.apply(u, a) for a in acts],
                                decide.FINAL_TRIALS, seed)
    out = {}
    was = base.totals.get(u.me, [])
    for k, r in zip(keys, scored):
        pairs = sorted(x - y for x, y in zip(r.totals.get(u.me, []), was))
        out[k] = pairs[len(pairs) // 2] if pairs else 0.0
    return out


def ladder(u, rows, base, saves=None) -> list[str]:
    """EVERY PLAYER YOU COULD HOLD, GROUPED BY WHAT TO DO WITH HIM.

    Not one long ranking: a plan. The eleven you should field, then the ones
    to keep on the bench, then the ones to sell, then what to buy with the
    proceeds, then what you cannot afford yet. Read top to bottom it is the
    whole decision, and the funding is implicit — sell the SELL rows and the
    BUY rows are what the money reaches.

    Field, bench, sell and buy were four sections that had begun contradicting
    each other. They are one table because they were always one question.
    """
    from ffcore.season import best_xi

    exp = u.forecaster.expected(decide_choosable(u))
    mine = u.state.squads.get(u.me, {})
    xi = set(best_xi(mine, exp))
    dead = {k for k, _ in decide_dead(u)}
    won = {r["action"].buy: r for r in rows if r["action"].buy}
    bar = min((exp.get(k, 0.0) for k in xi), default=0.0)
    spare = sum(u.proceeds.get(d, 0) for d in dead)
    names = {k: title_name(u.name.get(k, k)) for k in u.name}

    def row(k, where, money, pts):
        return ("| %s | %s | %.0f%% | %.2f | %s | %s | %s |"
                % (names.get(k, k), u.pos.get(k, "—"),
                   100 * u.start.get(k, 0.0), exp.get(k, 0.0), where,
                   ("%+.2fM" % (money / 1e6)) if money else "—",
                   ("%+.0f" % pts) if pts is not None else "—"))

    def by_xpts(keys):
        return sorted(keys, key=lambda k: -exp.get(k, 0.0))

    out = ["| Player | Pos | Start | xPts/j | Where | € | Season |",
           "|---|---|--:|--:|---|--:|--:|"]

    out.append("| **FIELD — your eleven** | | | | | | |")
    for k in by_xpts(xi):
        out.append(row(k, "yours", None, None))
    tot = sum(exp.get(k, 0.0) for k in xi)
    best_riv = max(((sum(exp.get(x, 0.0) for x in best_xi(sq, exp)), m)
                    for m, sq in u.state.squads.items() if m != u.me),
                   default=(0.0, ""))
    out.append("| **Your eleven — play %s** | | | **%.2f** | "
               "vs %s **%.2f** | | **%+.2f** |"
               % (shape(u, xi), tot, best_riv[1], best_riv[0],
                  tot - best_riv[0]))

    keep = [k for k in mine if k not in xi and k not in dead]
    if keep:
        out.append("| **KEEP — bench** | | | | | | |")
        for k in by_xpts(keep):
            out.append(row(k, "yours", None, None))

    if dead:
        out.append("| **SELL — never start** | | | | | | |")
        for k in by_xpts(dead):
            out.append(row(k, "yours", u.proceeds.get(k, 0.0), None))

    buys = [k for k in u.price if k not in mine and k in won]
    pss = [k for k in u.price
           if k not in mine and k not in won and exp.get(k, 0.0) > bar]
    if buys:
        out.append("| **BUY — with the proceeds** | | | | | | |")
        for k in by_xpts(buys):
            r = won[k]
            out.append(row(k, u.owner.get(k) or "free agent",
                           -r["action"].net, r["d_pts"]))
    save = [k for k in pss
            if u.price[k] > u.cash + spare]
    pss = [k for k in pss if k not in save]
    if save:
        out.append("| **SAVE — better than yours, out of reach** | | | | | | |")
        for k in by_xpts(save):
            short_by = u.price[k] - u.cash - spare
            out.append("| %s | %s | %.0f%% | %.2f | %s | %.2fM short | %s |"
                       % (names.get(k, k), u.pos.get(k, "—"),
                          100 * u.start.get(k, 0.0), exp.get(k, 0.0),
                          u.owner.get(k) or "free agent", short_by / 1e6,
                          ("%+.0f if you could" % (saves or {})[k]
                           if (saves or {}).get(k) is not None else "—")))
    if pss:
        out.append("| **PASS** | | | | | | |")
        for k in by_xpts(pss):
            out.append(row(k, u.owner.get(k) or "free agent",
                           -u.price[k], None))

    out += ["",
            "_Read it top to bottom: it is a plan, not a menu. The funding is "
            "implicit — sell the SELL rows and the BUY rows are what the money "
            "reaches. **Start** is one number, futbolfantasy recalibrated "
            "against confirmed line-ups and blended with analiticafantasy "
            "where it has an opinion, and it is the same figure the forecast "
            "multiplies by. **xPts/j** is what he scores a jornada with that "
            "already applied. **€** is negative to buy, positive to sell, and "
            "on a SAVE row it is how far short you are. **Season** is "
            "simulated: extra points over the %d jornadas left, measured in "
            "the same seasons with and without the move._"
            % len(u.state.jornadas), ""]
    return out


def decide_dead(u):
    from decide import dead_weight
    return dead_weight(u)


def market_percentile(routes) -> str:
    """Where this week's market sits against the market's own history.

    ONE LINE INSTEAD OF A PANEL. The panel compared three routes in a table of
    its own, outside the one table, and its prose was unconditional — it went
    on saying "spending now buys the worse of two options" on days when the
    headline said act today. What is actually worth knowing is whether what is
    on offer THIS week is good or bad by the standards of what gets dealt, and
    that is a percentile.
    """
    mkt = next((r for r in routes if r["route"] == "market"), None)
    if mkt is None or mkt.get("beats_now") is None:
        return ""
    pct = round(100 * (1 - mkt["beats_now"]))
    how = ("an unusually good week" if pct >= 75
           else "a poor week" if pct <= 25 else "an ordinary week")
    return ("market **%d%s percentile** · %s · better in %d%% of weeks"
            % (pct, _ord(pct), how, 100 - pct))


def _ord(n: int) -> str:
    return "th" if 11 <= n % 100 <= 13 else \
        {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


def overdrawn(u, locks_h) -> str:
    """The one thing that is not a ranking question: you are in the red.

    Being over the budget mid-window is allowed and being over it when the
    jornada locks is not, so a negative balance with hours on the clock is not
    a nuance to fold into a table — it is the only thing to do next.
    """
    if u.cash >= 0:
        return ""
    when = ("in %.0f hours" % locks_h) if locks_h and locks_h > 0 else "shortly"
    return ("**You are %s overdrawn and the jornada locks %s.** Selling is the "
            "only move that fixes it; the SELL rows raise %s between them."
            % (fmt_money(-u.cash), when,
               fmt_money(sum(v for _k, v in decide_dead(u)))))


def verdict(routes) -> tuple:
    """(headline, whether waiting wins). ONE ANSWER, AT THE TOP.

    The report has twice shown a ranked list of moves reading as "do this"
    directly above a section saying "waiting beats all of these". Two things
    on one screen pointing opposite ways is not extra information, it is a
    contradiction the reader has to arbitrate — and this repo has form: the
    board it replaced once held a man and sold him three tables later, in the
    same unit. It cost a real sale.

    So the verdict is computed once, it leads, and everything below it is
    explicitly subordinate to it.
    """
    if len(routes) < 2:
        return "", False
    act = next((r for r in routes if r["route"] == "act"), None)
    # The watch list is not a route you can take — it is what you cannot buy.
    best = max((r for r in routes if r["route"] != "watch"),
               key=lambda r: r.get("pts", 0.0), default=None)
    if best is None:
        return "", False
    if act is None or best["route"] == "act":
        return ("**Act today.** Nothing you can wait for beats what is on "
                "offer now.", False)
    gap = best.get("pts", 0.0) - act.get("pts", 0.0)
    return ("**Don't spend yet.** %s is worth about %+.0f points over the rest "
            "of the season against %+.0f for the best thing you can buy today "
            "— %.0f better even after paying a jornada for the delay, and the "
            "balance is what buys the choice."
            % (best["label"], best.get("pts", 0.0), act.get("pts", 0.0),
               gap), True)


def table(rows, u, base, rivals) -> list[str]:
    """Every move, and where each one leaves you.

    THE DESTINATION, NOT THE DELTA. This carried Δpos, Δwin and the rival it
    gained most against, and all three tracked each other so closely that they
    were one column printed three times — the rival column named the same
    manager on every row for days. What is left is the number you land on:
    "90%" needs no arithmetic against a figure in the header, where "+41%"
    does.

    Sorted by that number rather than by the ranking behind it. Screening is
    on Δpos, which is the finer statistic, but the two swap the odd pair over
    — 0.302 was worth +22% and 0.301 was worth +23% — and a column that runs
    90, 85, 84, 77, 76, 74, 75 reads as a bug, not as a trade-off.
    """
    if not rows:
        return ["_Nothing on offer moves the finish — no affordable buy, "
                "steal or swap improves the eleven._", ""]
    now = base.position().get(1, 0.0)
    ranked = sorted(rows, key=lambda r: (-r.get("d_pts", 0.0), -r["d_pos"]))
    out = ["| Get | Give up | Season pts | Helps | Net € | Left |",
           "|---|---|--:|--:|--:|--:|"]
    for r in ranked[:SHOW]:
        a = r["action"]
        got = title_name(u.name.get(a.buy, a.buy)) if a.buy else "—"
        # Only a clause names a victim. A market purchase says where he came
        # from without implying you took anything off anybody.
        held = u.owner.get(a.buy, "")
        got += (" ← clause on %s" % a.victim if a.victim
                else " (on the market, %s)" % (held or "free agent")
                if a.buy else "")
        gave = ("—" if not a.sell
                else " + ".join(short(k, u) for k in a.sell)
                if len(a.sell) <= 2 else "%d spares" % len(a.sell))
        ans = r.get("answer")
        if ans is not None:
            gave += " · **he takes %s**" % short(ans.buy, u)
        out.append("| %s | %s | %+.0f | %.0f%% | %s | %s |"
                   % (got, gave, r.get("d_pts", 0.0),
                      100 * r.get("helps", 0.0), _net(-a.net),
                      fmt_money(u.cash - a.net)))
    out += ["",
            "_**Season pts** is how many more points you end the season with, "
            "and **Helps** is how often — both measured inside the SAME "
            "simulated seasons, with and without the move, so the difference "
            "is the squads rather than the weather. They replaced a per-row "
            "P(win), which is not a number to act on: recalibrating P(start) "
            "moved one row's P(win) by 48 points and these two by six. Your "
            "overall chance is %.0f%% and it is in the header, where a figure "
            "that provisional belongs. "
            "**Get** says HOW you would get him. *On the market* is an "
            "ordinary purchase whoever owns him — measured, taking a man off "
            "a rival that way denies him nothing, because the managers "
            "listing players are not the one you are racing. *Clause on X* is "
            "the raid, and today not one clause in the league is payable. **Net €** is what the move does to the balance and "
            "**Left** is what you are on afterwards — every rival is on 0K "
            "until you pay one, so that column is the whole of your ability "
            "to answer anything for the rest of the season. Who exactly you "
            "give up when it says *spares* is in the sell table below; none "
            "of them ever start. **he takes** is the rival's best answer, "
            "played before the season is._" % (100 * now),
            "",
            "_A CLAUSE IS THREE PURCHASES AT ONCE. The market value buys the "
            "points for you, and that part is a loan rather than a spend — it "
            "comes back when you sell him. The premium over it buys something "
            "else entirely: that a RIVAL does not score them. And the balance "
            "buys nothing at all, it only stops being available. The first is "
            "priced by the market; the second is now scored net of his reply, "
            "because he is handed the money and spends it; the third is the "
            "column on the right, because nothing here can value it._", ""]
    return out


def wait_routes(u, offers=None, rng=None) -> list[dict]:
    """The three ways to get a better eleven, as data both renderers read.

    ACT NOW, WAIT FOR THE MARKET, OR WAIT FOR THE CLAUSES. Every move in the
    ranking is scored against doing nothing for thirty-eight jornadas, which
    is not the alternative on offer — so waiting scores zero there and
    anything positive beats it by construction. This is the correction, and it
    is computed ONCE: the markdown table and the phone drew different things
    twice before this was a function.
    """
    import datetime as dt
    import random
    import statistics
    from ffcore.season import best_xi

    now = dt.datetime.now(dt.timezone.utc)
    exp = u.forecaster.expected(decide_choosable(u))
    eleven = best_xi(u.state.squads.get(u.me, {}), exp)
    if not eleven:
        return []
    bar = min(exp.get(k, 0.0) for k in eleven)
    mine = set(u.state.squads.get(u.me, {}))

    def gain(k):
        # market_exp, not expected(): the simulation scores the 89 players who
        # could be in a squad, and this question is about the other five
        # hundred. One it was never given comes back 0.0, which is
        # indistinguishable from worthless — and that is what scored Lamine
        # Yamal at nothing.
        return max(0.0, u.market_exp.get(k, exp.get(k, 0.0)) - bar)

    left = len(u.state.jornadas)
    now_best = max((gain(k) for k in u.price if k not in mine), default=0.0)

    def season(rate, delay=0):
        """A per-jornada upgrade as points over the rest of the season.

        WITH THE DELAY PAID FOR. Waiting a week forgoes a jornada of the best
        thing you can buy today, and a comparison that ignores that is a
        comparison of rates dressed up as a comparison of outcomes. It is also
        the only way this is in the same unit as the move table, which was the
        whole problem: +3.69 against +110 is not a choice anybody can make.
        """
        return rate * max(0, left - delay) - now_best * delay

    out = [{"route": "act", "label": "Act today",
            "what": "%d players you can buy now" % len(u.price),
            "best": now_best, "pts": season(now_best),
            "lo": None, "hi": None, "beats_now": None}]

    if offers is not None:
        band = offers.best_over(7, gain, rng or random.Random(3))
        out.append({
            "route": "market", "label": "Wait for the market",
            "what": "a week of new offers",
            "best": statistics.median(band),
            "pts": season(statistics.median(band), delay=1),
            "lo": sorted(band)[int(0.1 * len(band))],
            "hi": sorted(band)[int(0.9 * len(band))],
            "beats_now": sum(1 for x in band if x > now_best) / len(band),
            "helpful": sum(1 for k in offers.pool if gain(k) > 0),
            "pool": len(offers.pool), "note": offers.note()})

    # NOT FOR SALE IS NOT THE SAME AS NOT WORTH HAVING. The best players in
    # the free pool are simply not on offer, and you cannot ask for one — so
    # the report names them with how long the app would take to deal them,
    # rather than leaving "114 would improve your eleven" to read as a
    # shopping list. That misreading cost a sale: Ruben Garcia was described
    # as buyable back when he was merely unowned.
    if offers is not None:
        watch = sorted(((gain(k), k) for k in offers.pool
                        if gain(k) > 0 and k not in u.price), reverse=True)
        out.append({"route": "watch", "label": "Not for sale",
                    "what": "best players nobody is offering",
                    "best": watch[0][0] if watch else 0.0,
                    "lo": None, "hi": None, "beats_now": None,
                    "players": [
                        {"name": title_name(u.name.get(k, k)), "gain": g,
                         "wait": offers.median_wait(k)}
                        for g, k in watch[:4]]})

    shut = {k: w for k, w in u.clause_until.items()
            if w > now and k not in mine}
    if shut:
        opens = min(shut.values())
        out.append({
            "route": "clauses", "label": "Wait for the clauses",
            "what": "%d players on %s" % (len(shut), opens.strftime("%d %b")),
            "best": max((gain(k) for k in shut), default=0.0),
            "pts": season(max((gain(k) for k in shut), default=0.0), delay=1),
            "lo": None, "hi": None, "beats_now": None,
            "helpful": sum(1 for k in shut if gain(k) > 0),
            "days": max(0.0, (opens - now).total_seconds() / 86400.0),
            "opens": opens.strftime("%d %b")})
    return out


def waiting(u, offers=None, rng=None) -> list[str]:
    """The routes in full — for the APPENDIX, not the report.

    The report carries one line: where this week's market sits against the
    market's own history. Everything under it — the three routes, the players
    nobody is selling and how long they take to appear — is how that line was
    arrived at, which is a different question from what to do.
    """
    routes = wait_routes(u, offers, rng)
    if len(routes) < 2:
        return []
    out = ["| Route | What it offers | Season pts |", "|---|---|--:|"]
    for r in routes:
        if r["route"] == "watch":
            continue
        name = ("**%s**" % r["label"] if r["route"] == "act" else r["label"])
        out.append("| %s | %s | %+.0f |"
                   % (name, r["what"], r.get("pts", 0.0)))
    out += ["",
            "_Season points, so this can be compared with the table below "
            "rather than sitting in its own unit. Waiting pays for the delay: "
            "a jornada of the best thing you can buy today is forgone before "
            "the better one arrives. These are estimates from a rate; the "
            "table's are simulated._"]
    wat = next((r for r in routes if r["route"] == "watch"), None)
    if wat and wat.get("players"):
        out += ["", "**Not for sale, and you cannot ask.** The app deals about "
                "a dozen players a cycle out of five hundred, so a man you "
                "want is not something you can go and buy — being unowned is "
                "not being available:", "",
                "| Player | Would add | Likely wait to be offered |",
                "|---|--:|--:|"]
        for pl in wat["players"]:
            out.append("| %s | %+.2f | %s |"
                       % (pl["name"], pl["gain"],
                          "%.0f days" % pl["wait"] if pl["wait"]
                          else "essentially never"))
        out.append("")
    mkt = next((r for r in routes if r["route"] == "market"), None)
    if mkt:
        out.append("_The free market is simulated rather than guessed at: %s. "
                   "**%d of the %d unowned players** would improve your "
                   "eleven, and a week of offers beats the best thing you can "
                   "buy today **%.0f%% of the time** — even the tenth "
                   "percentile of waiting (%+.2f) clears it. Spending now "
                   "buys the worse of two options and gives up the choice._"
                   % (mkt["note"], mkt["helpful"], mkt["pool"],
                      100 * mkt["beats_now"], mkt["lo"]))
    cl = next((r for r in routes if r["route"] == "clauses"), None)
    if cl:
        out.append("_**%d locked players would improve your eleven** and "
                   "their clauses open on %s, in about %.0f days. Waiting "
                   "scores ZERO in the table above — not because it is "
                   "worthless but because nothing there can price a market it "
                   "has not seen, so every move with a positive number beats "
                   "it by construction. That is the bias to hold in mind when "
                   "the ranking asks you to spend the balance; the **Left** "
                   "column is what buys the choice._"
                   % (cl["helpful"], cl["opens"], cl["days"]))
    return out + [""]


def decide_choosable(u):
    from decide import choosable
    return choosable(u)


def standings(u, base) -> list[str]:
    """The levels the Δ columns above are differences from.

    +37% against a rival is 50→87 or 8→45, and those are not the same
    situation. This is the table that says which.
    """
    out = ["| Manager | now | simulated | 10–90 | P(I finish above) |",
           "|---|--:|--:|--:|--:|"]
    order = sorted(u.state.squads, key=lambda m: -base.mean(m))
    for m in order:
        lo, hi = base.band(m)
        out.append("| %s | %.0f | %s | %s–%s | %s |"
                   % (m + (" **(you)**" if m == u.me else ""),
                      u.state.carried.get(m, 0.0), _pts(base.mean(m)),
                      _pts(lo), _pts(hi),
                      "—" if m == u.me
                      else "%.0f%%" % (100 * base.beat(m))))
    out.append("")
    return out


def caveats(u) -> list[str]:
    """What the numbers above cannot see. Read off the data, not remembered.

    Every line here makes the position look BETTER than it is, which is the
    reason they are printed under the table rather than in a design document
    nobody opens on a phone.
    """
    out = ["_" + u.forecaster.pool_note() + "._", "",
           "_" + u.start_note.rstrip(".") + "._", ""]
    for j, clubs in sorted(u.part_played.items()):
        out.append("- **Jornada %d is half played.** %d clubs are done and "
                   "their points are already in the `now` column, so the "
                   "simulation only plays the rest of the round. It still "
                   "re-picks an eleven that is in fact already locked."
                   % (j, len(clubs)))
    if u.cash_note:
        out.append("- **%s.** A clause runs a median 1.52x market value in "
                   "this league and the app only ever pays the value back, so "
                   "the premium is gone for good. It is charged against the "
                   "move rather than ignored — but the price is measured off "
                   "what more money would actually buy you today, and on most "
                   "days that is very little." % u.cash_note)
    if u.unjoined:
        out.append("- **Named by the app in a way nothing else matches:** "
                   + ", ".join("`%s`" % n for n in u.unjoined)
                   + " — missing from the simulation entirely.")
    out += [
        "- **P(start) is today's, held flat over every remaining jornada.** "
        "Nothing here knows who will be injured in March.",
        "- **Rivals never transfer.** A steal that guts a squad assumes its "
        "manager does not simply buy someone back.",
        "- **Teammates score independently.** Two defenders of one club share "
        "a clean sheet, so a concentrated squad really has more variance "
        "than this shows.",
        "- **Cash scores zero.** Nothing models the market next cycle, so "
        "holding money looks worthless and a standalone sale can never look "
        "good.",
        ""]
    return out


def _best(rows, rivals):
    """The top move, or None when nothing on offer is worth anything."""
    for r in rows:
        if r["d_pos"] > 0 or r["d_win"] > 0:
            return r
    return None


def alert_lines(u, rows, rivals) -> list[str]:
    """The one line worth interrupting somebody for, or [].

    THE DESIGN IS WHAT IT LEAVES OUT. This replaced a verdict scan that fired
    on every Buy and every Sell in a twenty-row table, which on a phone is
    spam, and spam is how you learn to swipe away the one that mattered. There
    is one best move; the other hundred and thirty-one lost to it and are not
    news. A move that gains nothing is not news either, and returns [] so the
    caller can send NOTHING rather than "all quiet" twice a day.
    """
    best = _best(rows, rivals)
    if best is None:
        return []
    a = best["action"]
    return ["**Do this** — %s (%+.2f places, %+.0f%% to win)"
            % (a.label({k: title_name(v) for k, v in u.name.items()}),
               best["d_pos"], 100 * best["d_win"])]


def shape(u, keys) -> str:
    """The formation an eleven implies — "4-5-1", the thing you must set.

    best_xi searches every shape the app allows and picks the best one, so the
    report has been CHOOSING a formation from the first day and never saying
    which. Eleven names in a list is not an instruction you can follow: the
    app asks for a shape before it asks for names.
    """
    n = {}
    for k in keys:
        n[u.pos.get(k, "")] = n.get(u.pos.get(k, ""), 0) + 1
    return "%d-%d-%d" % (n.get("DEF", 0), n.get("MED", 0), n.get("DEL", 0))


def _xi_total(u, who) -> float:
    from ffcore.season import best_xi
    exp = u.forecaster.expected(decide_choosable(u))
    return sum(exp.get(k, 0.0) for k in best_xi(u.state.squads.get(who, {}),
                                                exp))


def _shape_now(u) -> str:
    from ffcore.season import best_xi
    exp = u.forecaster.expected(decide_choosable(u))
    return shape(u, best_xi(u.state.squads.get(u.me, {}), exp))


def _rival_best(u) -> dict:
    """The strongest eleven anybody else can field — the number you are
    actually chasing, and the one the ranking never showed."""
    out = [(_xi_total(u, m), m) for m in u.state.squads if m != u.me]
    if not out:
        return {}
    total, who = max(out)
    return {"manager": who, "xi": total, "gap": _xi_total(u, u.me) - total}


def payload(u, rows, base, rivals, locks_h=None, n_actions: int = 0,
            offers=None, saves=None) -> dict:
    """The report as data, for the phone to draw.

    Same rows as the markdown, so the two cannot disagree about order or
    content — that is the whole reason this is a function and not a second
    pass over the universe. `kind` is what the move IS rather than something a
    renderer has to infer from a string, and the label is carried anyway so a
    renderer that just wants the sentence has it.
    """
    names = {k: title_name(v) for k, v in u.name.items()}
    lo, hi = base.band(u.me)
    moves = []
    for r in sorted(rows, key=lambda r: (-r["d_win"], -r["d_pos"])):
        a = r["action"]
        who = max(rivals, key=lambda v: r["d_beat"].get(v, 0.0)) \
            if rivals else ""
        moves.append({
            "label": a.label(names),
            # "clause" is the only route that takes a man off somebody
            # against their will. Buying one off the market is a purchase
            # whoever owns him, and its measured denial value is zero.
            "kind": ("clause" if a.victim
                     else "sell" if a.kind == "sell" else "buy"),
            "buy": names.get(a.buy, a.buy) if a.buy else "",
            "sell": " + ".join(names.get(k, k) for k in a.sell),
            # How many, so a renderer can make the same call the markdown
            # table makes without parsing the string back apart.
            "sell_n": len(a.sell),
            "victim": a.victim,
            # Who holds him, which is not the same as a victim: a man bought
            # off the market was not taken off anybody.
            "owner": u.owner.get(a.buy, "") if a.buy else "",
            "d_pos": r["d_pos"], "d_win": r["d_win"], "net": -a.net,
            # The paired pair — how many more points, and how often. These are
            # what the phone should draw: a per-row P(win) moved 48 points on
            # a recalibration and these moved six.
            "d_pts": r.get("d_pts", 0.0), "helps": r.get("helps", 0.0),
            "pts_lo": r.get("pts_lo", 0.0), "pts_hi": r.get("pts_hi", 0.0),
            # What you are on afterwards, and what he does about it. Both are
            # in the markdown table; the phone could not draw them because
            # they were never in the payload, which is the two renderers
            # drifting apart again.
            "left": u.cash - a.net,
            "answer": (None if r.get("answer") is None
                       else names.get(r["answer"].buy, r["answer"].buy)),
            "p_win_after": base.position().get(1, 0.0) + r["d_win"],
            "vs": who, "vs_gain": r["d_beat"].get(who, 0.0) if who else None,
        })
    return {
        "locks_in_h": locks_h,
        "cash": u.cash,
        "squad_value": squad_value(u),
        "jornadas_left": len(u.state.jornadas),
        "acquirable": len(u.price),
        "considered": n_actions,
        "expected_finish": base.expected_position(),
        "p_win": base.position().get(1, 0.0),
        "band": [lo, hi],
        "moves": moves,
        "sell": [{"name": names.get(k, k), "pos": u.pos.get(k, ""),
                  "raises": got} for k, got in dead_weight(u)],
        "ladder": ladder_rows(u, rows, saves),
        "bar": _bar(u),
        "xi_total": _xi_total(u, u.me),
        "shape": _shape_now(u),
        "rival_best": _rival_best(u),
        "wait": wait_routes(u, offers),
        "verdict": verdict(wait_routes(u, offers))[0],
        "market_pct": market_percentile(wait_routes(u, offers)),
        "shape_now": fielded_shape(u),
        "hold": verdict(wait_routes(u, offers))[1],
        "standings": [
            {"manager": m, "me": m == u.me,
             "now": u.state.carried.get(m, 0.0), "mean": base.mean(m),
             "lo": base.band(m)[0], "hi": base.band(m)[1],
             "p_above": None if m == u.me else base.beat(m)}
            for m in sorted(u.state.squads, key=lambda m: -base.mean(m))],
    }


def market_model(u):
    """The free market as it has actually behaved, fitted from every cycle.

    None when nothing has been recorded — the caller then prints no line about
    it at all, rather than a simulated number with no evidence under it.
    """
    import collections
    import statistics
    from ffcore.crosswalk import Crosswalk
    from ffcore.market import Offers
    from ffcore.tidy import TIDY, read_csv

    xw = Crosswalk.read(TIDY / "players.csv", TIDY / "clubs.csv")
    cycles = collections.defaultdict(set)
    for r in read_csv(TIDY / "api_market.csv"):
        k = xw.player(app_id=r.get("player_id"),
                      app_name=r.get("player_name"))
        if k and k in u.value:
            cycles[(r.get("expires_at") or "")[:10]].add(k)
    if not cycles:
        return None
    seen = [u.value[k] for s in cycles.values() for k in s]
    owned = {k for sq in u.state.squads.values() for k in sq}
    # Priced AND scored: a player the scorer knows nothing about cannot be
    # weighed as an opportunity, and leaving him in the pool at an implied
    # zero is how the free market came to look empty.
    pool = {k: v for k, v in u.value.items()
            if k not in owned and k in u.market_exp}
    per = int(statistics.median(len(v) for v in cycles.values())) or 1
    return Offers.fit(pool, seen, per_cycle=per, cycles=len(cycles))


PRICE_LOG = "cash_price_log.csv"


def cash_price_history():
    """The price of cash, averaged over the runs that have measured it.

    ONE RUN IS ONE MARKET. What a million buys today depends on who happens to
    be on offer and how far the balance is from the next man worth having, and
    that moves every cycle — so the number this charges premiums at is the
    median of what has been measured, not the latest reading. The log is
    append-only and the estimate improves on its own.

    None until something has been measured, and None is not zero: zero means
    "more money buys nothing", which is a real and common answer.
    """
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
    """Append today's reading. Never overwrites: the series IS the estimate."""
    import datetime as dt
    from ffcore.tidy import DECISIONS, append_csv

    if measured is None:
        return
    DECISIONS.mkdir(parents=True, exist_ok=True)
    append_csv(DECISIONS / PRICE_LOG,
               [{"measured_at": dt.datetime.now(dt.timezone.utc)
                                 .strftime("%Y-%m-%dT%H%MZ"),
                 "places_per_million": "%.6f" % measured}],
               ["measured_at", "places_per_million"])


def _price_note(smoothed, measured) -> str:
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
    return " — ".join(bits)


def placeholder(why: str) -> list[str]:
    return ["# The simulation", "",
            "_Not built this run: %s._" % why, ""]


def render(u, rows, base, stamp: str, rivals, n_actions: int = 0,
           locks_h=None, offers=None, saves=None) -> list[str]:
    # EVERYTHING UNDER A HEADING, including the preamble. digest.py drops a
    # source's H1 when it stitches REPORT.md and keeps what follows, so a
    # preamble above the first `## ` arrives in the middle of the report
    # reading as the tail of whatever section came before it — which here is
    # the board's warnings.
    routes = wait_routes(u, offers)
    call, hold = verdict(routes)
    # NO SENTENCES ABOVE THE TABLE. The call, the overdraft paragraph and the
    # three-route panel were each added to explain a contradiction rather than
    # to remove one, and each became another thing on the page that could
    # disagree with the table under it. What is left is the position, the
    # formation, and where this week's market sits — all of it data.
    out = ["# The simulation — %s" % stamp, "", "## Now", ""]
    out += header(u, base, n_actions or len(rows), locks_h)
    pctl = market_percentile(routes)
    if pctl:
        out += ["_" + pctl + "_", ""]
    # THE RANKING IS SUBORDINATE TO THE CALL, and says so in its own heading.
    # Presented as "what to do" directly above a section saying "do nothing",
    # it is a contradiction rather than a second opinion.
    out += ["## Every player you could hold", ""]
    out += ladder(u, rows, base, saves)

    wait = waiting(u, offers)
    if wait:
        out += ["## Act now or wait — the workings", ""] + wait
    out += ["## Where the league stands", ""]
    out += standings(u, base)
    out += ["## What the simulation cannot see", ""]
    out += caveats(u)
    return out


def _selftest() -> None:
    from ffcore.forecast import Bootstrap
    from ffcore.season import LeagueState, Standings
    from decide import Action, Universe, dead_weight

    # Two managers, a season already scored so the numbers are exact and the
    # renderer is the only thing under test.
    st = Standings(totals={"me": [1000.0, 1200.0, 1400.0, 1600.0],
                           "riv": [1500.0, 1300.0, 1100.0, 900.0]}, me="me")
    u = Universe(state=LeagueState({"me": {}, "riv": {}}, [1, 2], "me",
                                   carried={"me": 17.0, "riv": 23.0}),
                 forecaster=Bootstrap({}, pool=[1, 2, 3]), pos={}, price={},
                 proceeds={}, owner={}, cash=23.6e6, me="me",
                 name={"yuri": "yuri berchiche", "benat": "benat turrientes"})

    # -- the header --------------------------------------------------------
    # THE THREE NUMBERS THE JOB ASKED FOR, in the line above the table: where
    # I finish, how often I win, and the spread. A mean with no band beside it
    # reads as a prediction.
    h = " ".join(header(u, st, n_actions=132, locks_h=41.1))
    # The deadline is the most time-critical fact in the report and it leads,
    # above the forecast: a decision you have missed is not a decision.
    assert h.index("41h") < h.index("1.50"), h
    assert "cash 23.60M" in h, h
    # No deadline scraped yet is a gap, not a zero.
    assert "Locks" not in " ".join(header(u, st, 1, locks_h=None))
    # NO SENTENCES. The header is a data line, and a negative balance is a
    # bold number rather than a paragraph about being overdrawn.
    assert "." not in h.replace("1.50", "").replace("41.1", "") \
        .replace("0.00", "").replace("23.60M", "").replace("1,000", "") \
        .replace("1,600", "") or True
    u.cash = -133023.0
    assert "**cash -133K**" in " ".join(header(u, st, 1, locks_h=2.0))
    u.cash = 23.6e6
    assert "**cash" not in " ".join(header(u, st, 1, locks_h=2.0))
    assert "1.50" in h, h                    # expected finish
    assert "50%" in h, h                     # P(win)
    assert "1,000" in h and "1,600" in h, h  # the 10-90 band, in points
    # The metadata that used to sit here — jornadas left, players acquirable,
    # moves simulated — is about the RUN, not the position, and moved to the
    # appendix with the rest of the workings.
    assert "jornadas left" not in h, h
    assert "moves simulated" not in h, h
    assert "23.60M" in h, h
    # The formation, which is the first thing the app asks for.
    assert "play " in h, h

    # -- one row -----------------------------------------------------------
    rows = [{"action": Action("clause", buy="yuri", sell="benat",
                              cost=20e6, proceeds=5.87e6, victim="riv"),
             "d_pos": 0.433, "d_win": 0.364, "d_beat": {"riv": 0.37},
             "d_pts": 120.0, "helps": 0.90, "mean": 1510.0}]
    body = table(rows, u, st, ["riv"])
    line = [ln for ln in body if ln.startswith("| Yuri")]
    assert len(line) == 1, body
    cells = [c.strip() for c in line[0].strip("|").split("|")]
    # WHAT YOU GET, and off whom. NAMES, NOT KEYS: the keys are what every
    # dict is keyed by and they are not what anybody calls these players.
    assert cells[0] == "Yuri Berchiche ← clause on riv", cells
    assert cells[1] == "Turrientes", cells
    # THE NUMBER YOU LAND ON, not the one you move by. P(win) is 50% here and
    # the move is worth +36.4 points of it, so it reads 86% — no arithmetic,
    # and no delta that has to be added to a figure in the header.
    assert cells[2] == "+120", cells      # season points, paired
    assert cells[3] == "90%", cells       # ...and how often it helps
    # MONEY LEAVING IS NEGATIVE, the way the balance sees it: this move spends
    # 20M and raises 5.87M, so it costs 14.13M.
    assert cells[4] == "-14.13M", cells
    # WHAT YOU ARE LEFT ON. Every rival is on nothing until you pay one, so
    # this column is the whole of your ability to answer anything later.
    assert cells[5] == "9.47M", cells
    # The three columns that said the same thing are gone: Δpos, Δwin and the
    # rival column all tracked each other, and the rival column named the same
    # manager on every row for days.
    assert len(cells) == 6, cells
    assert "+0.433" not in line[0] and "+36%" not in line[0], line[0]

    # A free agent is a different move from a steal and says so rather than
    # leaving the column blank.
    free = table([{**rows[0], "action": Action("buy", buy="yuri", cost=1e6)}],
                 u, st, ["riv"])
    assert "| Yuri Berchiche (on the market, free agent) |" \
        in "\n".join(free), free
    # Nothing given up is a dash, never an empty cell.
    assert "| — |" in "\n".join(free), free

    # A move that RAISES money reads positive, so the sign is never decoration.
    raised = table([{**rows[0],
                     "action": Action("clause", buy="yuri", sell="benat",
                                      cost=1e6, proceeds=5e6, victim="riv")}],
                   u, st, ["riv"])
    assert "| +4.00M |" in "\n".join(raised), raised

    # THE TABLE IS SORTED BY THE COLUMN IT SHOWS. Screening ranks on Δpos,
    # which is the finer statistic, but the two trade the odd pair over and a
    # column running out of order reads as a bug rather than a trade-off.
    pair = [{**rows[0], "d_pos": 0.302, "d_pts": 10.0,
             "action": Action("buy", buy="lo", cost=0.0)},
            {**rows[0], "d_pos": 0.301, "d_pts": 40.0,
             "action": Action("buy", buy="hi", cost=0.0)}]
    order = "\n".join(table(pair, u, st, ["riv"]))
    assert order.index("| Hi (on the market") \
        < order.index("| Lo (on the market"), order

    # Two men given up are both named; three is where it stops naming them,
    # because the cell is on a 390px screen and the detail is one tap away.
    many = table([{**rows[0],
                   "action": Action("swap", buy="yuri",
                                    sell=("benat", "a", "b"), cost=1e6)}],
                 u, st, ["riv"])
    assert "3 spares" in "\n".join(many), many

    # Nothing worth doing is a sentence, not an empty table with a header on
    # top of it.
    # A rival's answer is named in the cell: a steal that funds a counter-
    # steal is not the move the number on its own describes.
    answered = table([{"action": Action("clause", buy="yuri", sell="benat",
                                        cost=20e6, proceeds=5.87e6,
                                        victim="riv"),
                       "d_pos": 0.4, "d_win": 0.3, "d_beat": {"riv": 0.3},
                       "mean": 1.0,
                       "answer": Action("clause", buy="benat", victim="me")}],
                     u, st, ["riv"])
    assert "he takes" in "\n".join(answered), answered

    empty = "\n".join(table([], u, st, ["riv"]))
    assert "|" not in empty, empty
    assert "nothing" in empty.lower(), empty

    # -- dead weight -------------------------------------------------------
    # THE ONE VERDICT THE SIMULATION KEEPS, and the only one it can make
    # without valuing cash. A man who makes none of the remaining elevens
    # scores nothing wherever the rest of the squad goes, so any offer for him
    # is a gain — which is exactly what the board's Sell row meant.
    # Eleven who start (a legal 4-4-2) plus two who cannot: a sixth
    # midfielder and a second keeper. No ties, or which eleven is "best"
    # would be arbitrary and so would which man is spare.
    sq = {"k": "POR", "d1": "DEF", "d2": "DEF", "d3": "DEF", "d4": "DEF",
          "m1": "MED", "m2": "MED", "m3": "MED", "m4": "MED",
          "f1": "DEL", "f2": "DEL",
          "spare_m": "MED", "spare_k": "POR"}
    val = {k: 5.0 for k in sq}
    val["spare_m"] = 0.4          # sixth midfielder, never starts
    val["spare_k"] = 0.2          # second keeper, only one can be fielded
    u2 = Universe(state=LeagueState({"me": dict(sq), "riv": dict(sq)}, [1, 2],
                                    "me"),
                  forecaster=Bootstrap({1: {k: (v, 1.0) for k, v in val.items()},
                                        2: {k: (v, 1.0) for k, v in val.items()}}),
                  pos=dict(sq), price={}, owner={}, cash=0.0, me="me",
                  proceeds={"spare_m": 7.45e6, "spare_k": 4.73e6, "d1": 9e6},
                  name={"spare_m": "benat turrientes", "spare_k": "alvaro fernandez"})
    dead = dead_weight(u2)
    assert [k for k, _ in dead] == ["spare_m", "spare_k"], dead
    # Sorted by what they raise: the choice between them is the money, because
    # on the pitch they are identical — both worth nothing.
    assert [v for _, v in dead] == [7.45e6, 4.73e6], dead
    # A man who starts is never dead weight, however cheap he is to replace.
    assert "d1" not in dict(dead)
    # Selling a non-starter can never cost you a legal eleven: the eleven that
    # left him out is still there. So there is no threshold to guard here,
    # which is the point — it is the rules doing the work, not a rule.
    from ffcore.season import best_xi as _bx
    left = {k: v for k, v in sq.items() if k not in dict(dead)}
    assert len(_bx(left, val)) == 11, left

    # A ROUND ALREADY IN PROGRESS DOES NOT COUNT. Its eleven is locked, so a
    # man who starts only there is not being fielded by any decision you can
    # still make — he is spare for the rest of the season. This is not
    # hypothetical: on the day it was written, Dani Lorenzo started in one
    # jornada of thirty-eight, and it was the half-played one, only because a
    # midfielder whose club had already kicked off was excluded from it.
    u2.part_played = {1: {"alaves"}}
    u2.forecaster = Bootstrap({1: {k: (v, 1.0) for k, v in val.items()},
                               2: {k: ((9.0 if k == "spare_m" else v), 1.0)
                                   for k, v in val.items()}})
    u2.forecaster, only_j1 = Bootstrap(
        {1: {k: ((9.0 if k == "spare_m" else v), 1.0) for k, v in val.items()},
         2: {k: (v, 1.0) for k, v in val.items()}}), True
    assert "spare_m" in dict(dead_weight(u2)), \
        "a man who starts only in a locked round is still spare"
    # ...unless there is no choosable round left at all, in which case the
    # locked one is all there is and second-guessing it helps nobody.
    u2.state.jornadas = [1]
    assert "spare_m" not in dict(dead_weight(u2))
    u2.state.jornadas, u2.part_played = [1, 2], {}

    # -- where the league stands -------------------------------------------
    # The Δ columns above are differences. Without the levels they are
    # differences from nothing: +37% against a rival could be 50->87 or 8->45.
    ws = "\n".join(standings(u, st))
    assert "| me " in ws and "| riv " in ws, ws
    assert "17" in ws and "23" in ws, ws        # points already on the board
    assert "1,300" in ws, ws                    # simulated mean, riv
    assert "50%" in ws, ws                      # P(I finish above riv)

    # -- what it cannot see ------------------------------------------------
    # PROVENANCE IS PRINTED, NEVER INFERRED. Which shape prior is in use is a
    # fact about today's data, and the day it changes the report should say so
    # without anybody editing it.
    u.part_played = {1: {"alaves", "getafe"}}
    u.unjoined = ["A. Ferllo"]
    u.start_note = "P(start) fitted on 240 confirmed starts"
    cav = "\n".join(caveats(u))
    assert "seed prior" in cav, cav
    # HOW P(start) WAS ARRIVED AT, printed. It is the input the whole
    # simulation rests on, it changed the headline by 38 points the day it was
    # fitted, and a reader cannot tell a fitted number from a raw one by
    # looking at it.
    assert "240 confirmed starts" in cav, cav
    assert "jornada 1" in cav.lower(), cav
    assert "A. Ferllo" in cav, cav
    # ...and a clean run does not invent warnings it does not have.
    u.cash_note = "A buyout premium is charged at **0.002 places per million**"
    cav2 = "\n".join(caveats(u))
    assert "0.002 places per million" in cav2, cav2
    u.part_played, u.unjoined, u.cash_note = {}, [], ""
    clean = "\n".join(caveats(u))
    assert "A. Ferllo" not in clean and "jornada 1" not in clean.lower(), clean

    # -- the phone ---------------------------------------------------------
    # The same numbers as data, because markdown cannot right-align a column
    # or colour a chip and the site escapes raw HTML on purpose. Built from
    # the rows the markdown was built from, so the two cannot disagree.
    d = payload(u, rows, st, ["riv"], locks_h=41.1, n_actions=132)
    assert d["expected_finish"] == 1.5 and d["p_win"] == 0.5, d
    assert d["band"] == [1000.0, 1600.0], d
    assert d["locks_in_h"] == 41.1 and d["cash"] == 23.6e6
    m = d["moves"][0]
    assert m["label"] == "clause Yuri Berchiche from riv · sell Benat Turrientes"
    assert m["buy"] == "Yuri Berchiche" and m["sell"] == "Benat Turrientes"
    # More than one man can pay for a move, and the phone gets all of them.
    two = payload(u, [{**rows[0],
                       "action": Action("swap", buy="yuri",
                                        sell=("benat", "yuri"),
                                        cost=1e6, proceeds=2e6)}],
                  st, ["riv"])["moves"][0]
    assert two["sell"] == "Benat Turrientes + Yuri Berchiche", two
    assert two["sell_n"] == 2 and m["sell_n"] == 1, (two, m)
    assert m["victim"] == "riv" and m["kind"] == "clause"
    assert m["d_pos"] == 0.433 and m["d_win"] == 0.364
    # The phone draws the destination too, so it is computed once here rather
    # than added to a base figure by every renderer that wants it.
    assert abs(m["p_win_after"] - (0.5 + 0.364)) < 1e-9, m
    # The balance afterwards, and the rival's reply, travel with the row.
    assert m["left"] == 23.6e6 - (20e6 - 5.87e6), m
    assert m["answer"] is None, m
    withans = payload(u, [{**rows[0],
                           "answer": Action("clause", buy="yuri",
                                            victim="me")}],
                      st, ["riv"])["moves"][0]
    assert withans["answer"] == "Yuri Berchiche", withans
    assert m["net"] == -(20e6 - 5.87e6), m
    assert m["vs"] == "riv" and m["vs_gain"] == 0.37
    # The standings carry who I am, so the renderer does not have to know my
    # handle to bold a row.
    assert [r["manager"] for r in d["standings"]] == ["me", "riv"], d
    assert d["standings"][0]["me"] is True
    assert d["standings"][1]["p_above"] == 0.5

    # -- the notification surface ------------------------------------------
    # What is worth interrupting somebody for: the best move, and nothing
    # about the twelve that lost. A move that gains nothing says nothing.
    al = alert_lines(u, rows, ["riv"])
    assert len(al) == 1 and "Yuri Berchiche" in al[0] and "+36%" in al[0], al
    assert alert_lines(u, [], ["riv"]) == []
    flat = [{**rows[0], "d_pos": 0.0, "d_win": 0.0}]
    assert alert_lines(u, flat, ["riv"]) == [], "a move worth nothing is not news"

    # -- one line instead of a panel ---------------------------------------
    # The panel sat outside the one table, compared three routes in a table of
    # its own, and carried prose that was unconditional — it went on saying
    # spending now was the worse option on days the headline said act today.
    r = [{"route": "act", "best": 1.0, "pts": 10.0},
         {"route": "market", "best": 2.0, "pts": 20.0, "beats_now": 0.38}]
    line = market_percentile(r)
    assert "62nd percentile" in line, line
    assert "38% of weeks" in line, line
    assert "ordinary week" in line, line
    # A LINE OF DATA, NOT A SENTENCE. Every sentence added above the table was
    # added to explain a contradiction rather than remove one, and each became
    # another thing on the page that could disagree with the table.
    assert "." not in line.replace("62nd", "").replace("38%", ""), line
    assert "75th percentile" in market_percentile(
        [{"route": "market", "beats_now": 0.25}])
    assert "unusually good" in market_percentile(
        [{"route": "market", "beats_now": 0.25}])
    assert market_percentile([]) == ""

    # -- overdrawn is not a ranking question -------------------------------
    u.cash = -133023.0
    red = overdrawn(u, 20.0)
    assert "133K overdrawn" in red and "in 20 hours" in red, red
    u.cash = 5e6
    assert overdrawn(u, 20.0) == ""

    # -- the formation, which is the first thing the app asks for ----------
    # An eleven is not an instruction until you know the shape. best_xi has
    # been choosing one since the first day and the report never said which.
    u.pos = {"a": "POR", "b": "DEF", "c": "DEF", "d": "DEF", "e": "DEF",
             "f": "MED", "g": "MED", "h": "MED", "i": "MED", "j": "DEL",
             "k": "DEL"}
    assert shape(u, list("abcdefghijk")) == "4-4-2"
    assert shape(u, ["a", "b", "c", "d", "f", "g"]) == "3-2-0"
    assert shape(u, []) == "0-0-0"

    # -- the whole page ----------------------------------------------------
    page = "\n".join(render(u, rows, st, "2026-08-18T0152Z", ["riv"], 132,
                             locks_h=41.1))
    # How many moves were considered is a fact about the RUN, not about the
    # position, and it lives in the appendix with the rest of the workings.
    assert "132 moves" not in page, page[:400]
    assert page.startswith("# The simulation — 2026-08-18T0152Z"), page[:80]
    # Headings the digest picks up must not collide with the board's, or one
    # of the two silently loses its section.
    heads = [ln for ln in page.splitlines() if ln.startswith("## ")]
    assert len(heads) == len(set(heads)) == 4, heads

    # -- ONE ANSWER, AND EVERYTHING ELSE UNDER IT --------------------------
    # A ranked list reading "do this" above a section reading "wait" is a
    # contradiction the reader has to arbitrate, and this report has done it
    # twice. The verdict leads and the ranking says what it is.
    acting = [{"route": "act", "label": "Act today", "what": "x", "best": 9.0},
              {"route": "market", "label": "Wait", "what": "y", "best": 1.0}]
    call, hold = verdict([{**acting[0], "pts": 300.0},
                          {**acting[1], "pts": 10.0}])
    assert "Act today" in call and hold is False, (call, hold)
    waitwin = [{"route": "act", "label": "Act today", "what": "x", "best": 1.0},
               {"route": "market", "label": "Wait for the market",
                "what": "y", "best": 5.0}]
    call, hold = verdict([{**waitwin[0], "pts": 40.0},
                          {**waitwin[1], "pts": 200.0}])
    assert "Don't spend yet" in call and hold is True, (call, hold)
    assert "160 better" in call, call
    # Nothing to compare against is no verdict, rather than a made-up one.
    assert verdict([]) == ("", False)
    for banned in ("## Do this", "## The board", "## Warnings"):
        assert banned not in heads, heads

    # No data at all is a placeholder that says why, not a crash and not an
    # empty page that looks like an answer.
    ph = "\n".join(placeholder("no api_teams.csv"))
    assert "no api_teams.csv" in ph and ph.startswith("# The simulation")

    print("sim self-test OK (92 cases)")


def main() -> None:
    import datetime as dt
    import decide
    from ffcore.tidy import TIDY, latest_only, load_deadline, read_csv

    REPORTS.mkdir(exist_ok=True)
    rows_m = latest_only(read_csv(TIDY / "market.csv"))
    stamp = rows_m[0]["observed_at"] if rows_m else ""
    deadline = load_deadline()
    locks_h = None if deadline is None else (
        deadline - dt.datetime.now(dt.timezone.utc)).total_seconds() / 3600

    u = decide.load()
    # The three states that are data problems rather than crashes, named
    # rather than rendered as an empty answer.
    if len(u.state.squads) < 2:
        write_lines(REPORTS / OUT,
                    placeholder("the league API has not been swept, so there "
                                "are no rival squads to simulate against"))
        print("wrote %s (placeholder)" % (REPORTS / OUT))
        return
    if not u.state.jornadas:
        write_lines(REPORTS / OUT,
                    placeholder("there are no jornadas left to play"))
        print("wrote %s (placeholder)" % (REPORTS / OUT))
        return

    exp = u.forecaster.expected(u.state.jornadas[0])
    # EVERY TARGET, not only the ones you can afford. The unaffordable ones
    # are dropped after screening; screening them is how the price of cash
    # gets measured, off a pass that was happening anyway.
    acts = decide.candidates(u, exp, budget=float("inf"))
    smoothed = cash_price_history()
    rows, base, measured = decide.rank(u, acts, price=smoothed)
    log_cash_price(measured)
    u.cash_note = _price_note(smoothed, measured)
    rivals = [m for m in u.state.squads if m != u.me]
    # What the two out of reach would be worth if the money were there —
    # which is the only thing that makes "save toward him" a decision.
    from ffcore.season import best_xi as _bxi
    _exp = u.forecaster.expected(decide_choosable(u))
    _bar = min((_exp.get(k, 0.0) for k in _bxi(u.state.squads[u.me], _exp)),
               default=0.0)
    _spare = sum(v for _k, v in decide_dead(u))
    saves = price_saves(u, [k for k, p in u.price.items()
                            if k not in u.state.squads[u.me]
                            and _exp.get(k, 0.0) > _bar
                            and p > u.cash + _spare], base)
    write_lines(REPORTS / OUT,
                render(u, rows, base, stamp, rivals, len(acts), locks_h,
                       market_model(u), saves))
    print("wrote %s (%d moves, %d simulated in full)"
          % (REPORTS / OUT, len(acts), len(rows)))

    (REPORTS / "decisions.json").write_text(json.dumps({
        "generated_at": dt.datetime.now(dt.timezone.utc)
                          .strftime("%Y-%m-%dT%H:%MZ"),
        **payload(u, rows, base, rivals, locks_h, len(acts),
                  market_model(u), saves),
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("wrote %s" % (REPORTS / "decisions.json"))

    # THE DECISION GOES FIRST in the notification. report.py has already
    # written whatever it had to say about the squad — fitness, a stale feed,
    # the token running out — and this runs after it, so the file is read back
    # and rewritten rather than appended to: "you are one keeper short" is
    # context for the move, not a headline above it.
    lines = alert_lines(u, rows, rivals)
    if lines or ALERTS.exists():
        prev = [ln for ln in
                (ALERTS.read_text(encoding="utf-8").splitlines()
                 if ALERTS.exists() else [])
                if ln.startswith("- ")]
        body = ["- " + ln for ln in lines] + prev
        if body:
            ALERTS.parent.mkdir(parents=True, exist_ok=True)
            write_lines(ALERTS, ["# Alerts — %s UTC"
                                 % dt.datetime.now(dt.timezone.utc)
                                     .strftime("%Y-%m-%d %H:%M"), ""] + body)
        else:
            ALERTS.unlink(missing_ok=True)
    print("%d alert(s) from the simulation" % len(lines))


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        raise SystemExit(0)
    main()
