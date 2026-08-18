"""
sim.py — the simulation, written out as the report it is going to become.

    python src/sim.py             # writes reports/sim.md
    python src/sim.py --selftest

ONE TABLE, ONE QUESTION: for every move I could make, how much does it change
where I finish. `decide.py` answers that; this prints it. There is no metric to
explain, no threshold to tune and no verdict vocabulary, because the column IS
the answer — a move that gains nothing shows a Δ of nothing.

SIDE BY SIDE, ON PURPOSE, AND NOT YET IN CHARGE. reports/board.md still ranks
every asset on pts/M and REPORT.md still leads with it. The two are printed
together for a few jornadas so they can be compared on real data before the
board goes, because the board is the thing that currently works and the
forecast under this one still rests on approximations that flatter a lead —
they are listed at the foot of every page it writes, from the data rather than
from memory.

WHY A SEPARATE FILE and not a section inside report.py: report.py is the old
metric zoo, and most of it is scheduled for deletion. A generator that writes
its own file is what digest.py already expects, and when the board goes this
file stays exactly as it is instead of being cut out of a 2,300-line module.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ffcore.parse import fmt_money  # noqa: E402
from ffcore.render import title_name  # noqa: E402
from ffcore.tidy import REPORTS, write_lines  # noqa: E402

OUT = "sim.md"

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


def header(u, base, n_actions: int) -> list[str]:
    """The line above the table: where I finish, how often I win, the spread.

    THE BAND IS NOT DECORATION. The mean of a simulated season is a number the
    league will never produce; the 10-90 interval is the honest form of the
    same statement, and printing the first without the second is how a
    forecast gets read as a fixture.
    """
    lo, hi = base.band(u.me)
    return ["**Expected finish %.2f** · **P(win) %.0f%%** · season "
            "**%s–%s** (10–90)"
            % (base.expected_position(), 100 * base.position().get(1, 0.0),
               _pts(lo), _pts(hi)),
            "",
            "_%d jornadas left · cash %s · %d players acquirable · "
            "%d moves simulated._"
            % (len(u.state.jornadas), fmt_money(u.cash), len(u.price),
               n_actions),
            ""]


def table(rows, u, rivals) -> list[str]:
    """Every move, ranked by what it does to the finish.

    The order is `rank()`'s and is not re-sorted here: a report that sorted
    the rows itself would be a second opinion about which move is best, held
    by the half of the system that cannot simulate anything.
    """
    if not rows:
        return ["_Nothing on offer moves the finish — no affordable buy, "
                "steal or swap improves the eleven._", ""]
    out = ["| Do this | Δpos | Δwin | net € | biggest gain vs |",
           "|---|--:|--:|--:|---|"]
    for r in rows[:SHOW]:
        a = r["action"]
        who = max(rivals, key=lambda v: r["d_beat"].get(v, 0.0)) \
            if rivals else ""
        gain = ("%s %+.0f%%" % (who, 100 * r["d_beat"].get(who, 0.0))
                if who else "—")
        out.append("| %s | %+.3f | %+.0f%% | %s | %s |"
                   % (a.label({k: title_name(v) for k, v in u.name.items()}),
                      r["d_pos"], 100 * r["d_win"],
                      _net(-a.net), gain))
    out += ["",
            "_**Δpos** is places gained on the expected finish, **Δwin** is "
            "percentage points of P(winning the league), and **net €** is "
            "what the move does to the balance — negative spends, positive "
            "raises. **Biggest gain vs** is the rival the move takes the most "
            "from, which is the column to read when one of them is the race "
            "and the rest are not._", ""]
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
    out = ["_" + u.forecaster.pool_note() + "._", ""]
    for j, clubs in sorted(u.part_played.items()):
        out.append("- **Jornada %d is half played.** %d clubs are done and "
                   "their points are already in the `now` column, so the "
                   "simulation only plays the rest of the round. It still "
                   "re-picks an eleven that is in fact already locked."
                   % (j, len(clubs)))
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


def placeholder(why: str) -> list[str]:
    return ["# The simulation", "",
            "_Not built this run: %s._" % why, ""]


def render(u, rows, base, stamp: str, rivals, n_actions: int = 0) -> list[str]:
    # EVERYTHING UNDER A HEADING, including the preamble. digest.py drops a
    # source's H1 when it stitches REPORT.md and keeps what follows, so a
    # preamble above the first `## ` arrives in the middle of the report
    # reading as the tail of whatever section came before it — which here is
    # the board's warnings.
    out = ["# The simulation — %s" % stamp, "",
           "## What the simulation says to do", "",
           "_A trial, printed beside the board rather than in place of it. "
           "Same data, one question: if I made this move, where would I "
           "finish?_", ""]
    out += header(u, base, n_actions or len(rows))
    out += table(rows, u, rivals)
    out += ["## Where the league stands", ""]
    out += standings(u, base)
    out += ["## What the simulation cannot see", ""]
    out += caveats(u)
    return out


def _selftest() -> None:
    from ffcore.forecast import Bootstrap
    from ffcore.season import LeagueState, Standings
    from decide import Action, Universe

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
    h = " ".join(header(u, st, n_actions=132))
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
    body = table(rows, u, ["riv"])
    line = [ln for ln in body if ln.startswith("| steal")]
    assert len(line) == 1, body
    cells = [c.strip() for c in line[0].strip("|").split("|")]
    # NAMES, NOT KEYS. The keys are what every dict is keyed by and they are
    # not what anybody calls these players.
    assert cells[0] == "steal Yuri Berchiche from riv · sell Benat Turrientes", cells
    assert cells[1] == "+0.433", cells
    assert cells[2] == "+36%", cells
    # MONEY LEAVING IS NEGATIVE, the way the balance sees it: this move spends
    # 20M and raises 5.87M, so it costs 14.13M.
    assert cells[3] == "-14.13M", cells
    assert cells[4] == "riv +37%", cells

    # A move that RAISES money reads positive, so the sign is never decoration.
    raised = table([{**rows[0],
                     "action": Action("steal", buy="yuri", sell="benat",
                                      cost=1e6, proceeds=5e6, victim="riv")}],
                   u, ["riv"])
    assert "| +4.00M |" in "\n".join(raised), raised

    # Nothing worth doing is a sentence, not an empty table with a header on
    # top of it.
    empty = "\n".join(table([], u, ["riv"]))
    assert "|" not in empty, empty
    assert "nothing" in empty.lower(), empty

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
    cav = "\n".join(caveats(u))
    assert "seed prior" in cav, cav
    assert "jornada 1" in cav.lower(), cav
    assert "A. Ferllo" in cav, cav
    # ...and a clean run does not invent warnings it does not have.
    u.part_played, u.unjoined = {}, []
    clean = "\n".join(caveats(u))
    assert "A. Ferllo" not in clean and "jornada 1" not in clean.lower(), clean

    # -- the whole page ----------------------------------------------------
    page = "\n".join(render(u, rows, st, "2026-08-18T0152Z", ["riv"], 132))
    # The header counts what was CONSIDERED, not what survived screening: the
    # table shows twelve because the other hundred and twenty lost, and a
    # header that said twelve would be describing the table rather than the
    # decision.
    assert "132 moves" in page, page[:400]
    assert page.startswith("# The simulation — 2026-08-18T0152Z"), page[:80]
    # Headings the digest picks up must not collide with the board's, or one
    # of the two silently loses its section.
    heads = [ln for ln in page.splitlines() if ln.startswith("## ")]
    assert len(heads) == len(set(heads)) == 3, heads
    for banned in ("## Do this", "## The board", "## Warnings"):
        assert banned not in heads, heads

    # No data at all is a placeholder that says why, not a crash and not an
    # empty page that looks like an answer.
    ph = "\n".join(placeholder("no api_teams.csv"))
    assert "no api_teams.csv" in ph and ph.startswith("# The simulation")

    print("sim self-test OK (27 cases)")


def main() -> None:
    import decide
    from ffcore.tidy import TIDY, latest_only, read_csv

    REPORTS.mkdir(exist_ok=True)
    rows_m = latest_only(read_csv(TIDY / "market.csv"))
    stamp = rows_m[0]["observed_at"] if rows_m else ""

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
    acts = decide.candidates(u, exp)
    rows, base = decide.rank(u, acts)
    rivals = [m for m in u.state.squads if m != u.me]
    write_lines(REPORTS / OUT,
                render(u, rows, base, stamp, rivals, len(acts)))
    print("wrote %s (%d moves, %d simulated in full)"
          % (REPORTS / OUT, len(acts), len(rows)))


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        raise SystemExit(0)
    main()
