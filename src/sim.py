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


def header(u, base, n_actions: int, locks_h=None) -> list[str]:
    """The lines above the table: when it locks, then where I finish.

    THE DEADLINE LEADS. A decision you have missed is not a decision, and
    everything below it is worth reading only if there is still time to act.

    THE BAND IS NOT DECORATION. The mean of a simulated season is a number the
    league will never produce; the 10-90 interval is the honest form of the
    same statement, and printing the first without the second is how a
    forecast gets read as a fixture.
    """
    lo, hi = base.band(u.me)
    val = squad_value(u)
    ctx = []
    if locks_h is not None:
        ctx.append("**Locks in %s**"
                   % ("%.0fh" % locks_h if locks_h < 48
                      else "%.0f days" % (locks_h / 24)))
    ctx += ["squad %s" % fmt_money(val), "cash %s" % fmt_money(u.cash),
            "total %s" % fmt_money(val + u.cash)]
    return [" · ".join(ctx), "",
            "**Expected finish %.2f** · **P(win) %.0f%%** · season "
            "**%s–%s** (10–90)"
            % (base.expected_position(), 100 * base.position().get(1, 0.0),
               _pts(lo), _pts(hi)),
            "",
            "_%d jornadas left · %d players acquirable · "
            "%d moves simulated._"
            % (len(u.state.jornadas), len(u.price), n_actions),
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
    ranked = sorted(rows, key=lambda r: (-r["d_win"], -r["d_pos"]))
    out = ["| Get | Give up | P(win) | Net € |", "|---|---|--:|--:|"]
    for r in ranked[:SHOW]:
        a = r["action"]
        got = title_name(u.name.get(a.buy, a.buy)) if a.buy else "—"
        got += (" ← %s" % a.victim if a.victim
                else " (free)" if a.buy else "")
        gave = ("—" if not a.sell
                else " + ".join(short(k, u) for k in a.sell)
                if len(a.sell) <= 2 else "%d spares" % len(a.sell))
        ans = r.get("answer")
        if ans is not None:
            gave += " · **he takes %s**" % short(ans.buy, u)
        out.append("| %s | %s | %.0f%% | %s |"
                   % (got, gave, 100 * (now + r["d_win"]), _net(-a.net)))
    out += ["",
            "_**P(win)** is where the move LEAVES you — your chance of winning "
            "the league after making it, against %.0f%% if you do nothing. "
            "**Get** names the rival a steal takes him off, which is half of "
            "what a steal is worth: it raises your total and lowers theirs at "
            "once. **Net €** is what the move does to the balance — negative "
            "spends, positive raises. Who exactly you give up when it says "
            "*spares* is in the sell table below; none of them ever start. "
            "**he takes** is the rival's best answer, played before the "
            "season is: a clause pays the OWNER, so paying one funds the "
            "manager you are racing — and every rival is overdrawn until you "
            "do._"
            % (100 * now), ""]
    return out


def sells(u, dead) -> list[str]:
    """The dead weight, and what the app pays for it."""
    if not dead:
        return ["_Nothing spare — every player in the squad starts in at "
                "least one of the remaining jornadas._", ""]
    out = ["| Sell | Pos | Raises |", "|---|---|--:|"]
    for k, got in dead:
        out.append("| %s | %s | %s |"
                   % (title_name(u.name.get(k, k)), u.pos.get(k, "—"),
                      fmt_money(got)))
    out += ["",
            "_These start in none of the %d remaining jornadas, so they score "
            "nothing wherever the rest of the squad goes and any offer is a "
            "gain. The simulation rates selling them at exactly zero — it "
            "cannot value the cash, which is the whole of what they are "
            "worth. What it also cannot value is cover: P(start) is held flat "
            "here, so nobody is ever injured in March and a bench that exists "
            "for that is worth nothing to it._" % len(u.state.jornadas), ""]
    return out


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


def payload(u, rows, base, rivals, locks_h=None, n_actions: int = 0) -> dict:
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
            "kind": "steal" if a.victim else ("sell" if a.kind == "sell"
                                              else "buy"),
            "buy": names.get(a.buy, a.buy) if a.buy else "",
            "sell": " + ".join(names.get(k, k) for k in a.sell),
            # How many, so a renderer can make the same call the markdown
            # table makes without parsing the string back apart.
            "sell_n": len(a.sell),
            "victim": a.victim,
            "d_pos": r["d_pos"], "d_win": r["d_win"], "net": -a.net,
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
        "standings": [
            {"manager": m, "me": m == u.me,
             "now": u.state.carried.get(m, 0.0), "mean": base.mean(m),
             "lo": base.band(m)[0], "hi": base.band(m)[1],
             "p_above": None if m == u.me else base.beat(m)}
            for m in sorted(u.state.squads, key=lambda m: -base.mean(m))],
    }


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
           locks_h=None) -> list[str]:
    # EVERYTHING UNDER A HEADING, including the preamble. digest.py drops a
    # source's H1 when it stitches REPORT.md and keeps what follows, so a
    # preamble above the first `## ` arrives in the middle of the report
    # reading as the tail of whatever section came before it — which here is
    # the board's warnings.
    out = ["# The simulation — %s" % stamp, "",
           "## What the simulation says to do", "",
           "_One question, asked of every move you could make: if I did "
           "this, where would I finish?_", ""]
    out += header(u, base, n_actions or len(rows), locks_h)
    out += table(rows, u, base, rivals)
    out += ["## Sell — these never make the eleven", ""]
    out += sells(u, dead_weight(u))
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
    assert "1.50" in h, h                    # expected finish
    assert "50%" in h, h                     # P(win)
    assert "1,000" in h and "1,600" in h, h  # the 10-90 band, in points
    assert "2 jornadas left" in h, h
    assert "23.60M" in h, h
    assert "132" in h, h

    # -- one row -----------------------------------------------------------
    rows = [{"action": Action("steal", buy="yuri", sell="benat",
                              cost=20e6, proceeds=5.87e6, victim="riv"),
             "d_pos": 0.433, "d_win": 0.364, "d_beat": {"riv": 0.37},
             "mean": 1510.0}]
    body = table(rows, u, st, ["riv"])
    line = [ln for ln in body if ln.startswith("| Yuri")]
    assert len(line) == 1, body
    cells = [c.strip() for c in line[0].strip("|").split("|")]
    # WHAT YOU GET, and off whom. NAMES, NOT KEYS: the keys are what every
    # dict is keyed by and they are not what anybody calls these players.
    assert cells[0] == "Yuri Berchiche ← riv", cells
    assert cells[1] == "Turrientes", cells
    # THE NUMBER YOU LAND ON, not the one you move by. P(win) is 50% here and
    # the move is worth +36.4 points of it, so it reads 86% — no arithmetic,
    # and no delta that has to be added to a figure in the header.
    assert cells[2] == "86%", cells
    # MONEY LEAVING IS NEGATIVE, the way the balance sees it: this move spends
    # 20M and raises 5.87M, so it costs 14.13M.
    assert cells[3] == "-14.13M", cells
    # The three columns that said the same thing are gone: Δpos, Δwin and the
    # rival column all tracked each other, and the rival column named the same
    # manager on every row for days.
    assert len(cells) == 4, cells
    assert "+0.433" not in line[0] and "+36%" not in line[0], line[0]

    # A free agent is a different move from a steal and says so rather than
    # leaving the column blank.
    free = table([{**rows[0], "action": Action("buy", buy="yuri", cost=1e6)}],
                 u, st, ["riv"])
    assert "| Yuri Berchiche (free) |" in "\n".join(free), free
    # Nothing given up is a dash, never an empty cell.
    assert "| — |" in "\n".join(free), free

    # A move that RAISES money reads positive, so the sign is never decoration.
    raised = table([{**rows[0],
                     "action": Action("steal", buy="yuri", sell="benat",
                                      cost=1e6, proceeds=5e6, victim="riv")}],
                   u, st, ["riv"])
    assert "| +4.00M |" in "\n".join(raised), raised

    # THE TABLE IS SORTED BY THE COLUMN IT SHOWS. Ranking is on Δpos, which is
    # the finer statistic and the right one to screen on, but two moves can
    # trade places between the two — 0.302 was worth +22% and 0.301 was worth
    # +23% on the day this was written — and a P(win) column running 90, 85,
    # 84, 77, 76, 74, 75 reads as a bug rather than as a trade-off.
    pair = [{**rows[0], "d_pos": 0.302, "d_win": 0.22,
             "action": Action("buy", buy="lo", cost=0.0)},
            {**rows[0], "d_pos": 0.301, "d_win": 0.23,
             "action": Action("buy", buy="hi", cost=0.0)}]
    order = "\n".join(table(pair, u, st, ["riv"]))
    assert order.index("| Hi (free)") < order.index("| Lo (free)"), order

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
    answered = table([{"action": Action("steal", buy="yuri", sell="benat",
                                        cost=20e6, proceeds=5.87e6,
                                        victim="riv"),
                       "d_pos": 0.4, "d_win": 0.3, "d_beat": {"riv": 0.3},
                       "mean": 1.0,
                       "answer": Action("steal", buy="benat", victim="me")}],
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

    sl = "\n".join(sells(u2, dead))
    assert "Beñat Turrientes" not in sl      # the name comes from `name`
    assert "Benat Turrientes" in sl, sl
    assert "7.45M" in sl, sl
    # A squad with nothing spare says so rather than printing a bare header.
    tight = Universe(state=LeagueState({"me": {}, "riv": {}}, [1], "me"),
                     forecaster=Bootstrap({}), pos={}, price={}, proceeds={},
                     owner={}, cash=0.0, me="me")
    assert "|" not in "\n".join(sells(tight, []))

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
    assert m["label"] == "steal Yuri Berchiche from riv · sell Benat Turrientes"
    assert m["buy"] == "Yuri Berchiche" and m["sell"] == "Benat Turrientes"
    # More than one man can pay for a move, and the phone gets all of them.
    two = payload(u, [{**rows[0],
                       "action": Action("swap", buy="yuri",
                                        sell=("benat", "yuri"),
                                        cost=1e6, proceeds=2e6)}],
                  st, ["riv"])["moves"][0]
    assert two["sell"] == "Benat Turrientes + Yuri Berchiche", two
    assert two["sell_n"] == 2 and m["sell_n"] == 1, (two, m)
    assert m["victim"] == "riv" and m["kind"] == "steal"
    assert m["d_pos"] == 0.433 and m["d_win"] == 0.364
    # The phone draws the destination too, so it is computed once here rather
    # than added to a base figure by every renderer that wants it.
    assert abs(m["p_win_after"] - (0.5 + 0.364)) < 1e-9, m
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

    # -- the whole page ----------------------------------------------------
    page = "\n".join(render(u, rows, st, "2026-08-18T0152Z", ["riv"], 132,
                             locks_h=41.1))
    # The header counts what was CONSIDERED, not what survived screening: the
    # table shows twelve because the other hundred and twenty lost, and a
    # header that said twelve would be describing the table rather than the
    # decision.
    assert "132 moves" in page, page[:400]
    assert page.startswith("# The simulation — 2026-08-18T0152Z"), page[:80]
    # Headings the digest picks up must not collide with the board's, or one
    # of the two silently loses its section.
    heads = [ln for ln in page.splitlines() if ln.startswith("## ")]
    assert len(heads) == len(set(heads)) == 4, heads
    for banned in ("## Do this", "## The board", "## Warnings"):
        assert banned not in heads, heads

    # No data at all is a placeholder that says why, not a crash and not an
    # empty page that looks like an answer.
    ph = "\n".join(placeholder("no api_teams.csv"))
    assert "no api_teams.csv" in ph and ph.startswith("# The simulation")

    print("sim self-test OK (67 cases)")


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
    write_lines(REPORTS / OUT,
                render(u, rows, base, stamp, rivals, len(acts), locks_h))
    print("wrote %s (%d moves, %d simulated in full)"
          % (REPORTS / OUT, len(acts), len(rows)))

    (REPORTS / "decisions.json").write_text(json.dumps({
        "generated_at": dt.datetime.now(dt.timezone.utc)
                          .strftime("%Y-%m-%dT%H:%MZ"),
        **payload(u, rows, base, rivals, locks_h, len(acts)),
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
