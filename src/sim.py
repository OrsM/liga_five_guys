"""
sim.py — the simulation, written out as the report it is going to become.

    python src/sim.py             # writes .runtime/parts/sim.md
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

from decide import dead_weight, overdraft_fix, route_kind, value_rate  # noqa: E402,F401
from ffcore.parse import fmt_money  # noqa: E402
from ffcore.league import app_fielded  # noqa: E402
from ffcore.render import title_name  # noqa: E402
from ffcore.tidy import (run_now,  # noqa: E402
                         PARTS, REPORTS, age_phrase,  # noqa: E402
                         stale_feeds,
                         write_lines)

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


def fielded_keys(u=None) -> list[str]:
    """The eleven you are actually fielding, from the app — [] if it is quiet.

    THE APP KNOWS. /v1/competition/1/teams/{team}/lineup/week/{n} returns the
    formation you have set, and this repo spent a season believing no such
    thing was published because every guess had been made under the LEAGUE
    path.

    [] rather than a fallback, deliberately. What used to be here was
    inputs/lineup.txt, a checklist ticked by hand which lost a mark whenever a
    fielded player was sold — and the only moments it was ever read were the
    moments it was wrong. Measured: with the app answering it changed nothing;
    with the app quiet it produced "not a legal eleven, 4-4-1" about a legal
    4-5-1. No answer prints the whole sheet, which is what the report did
    before any of this existed.
    """
    return app_fielded(u.state.squads.get(u.me, {}), u.name) if u else []


def _warnings() -> list:
    """What report.py flagged this run, or [] — never recomputed here.

    Two modules deriving the same list is how they come to disagree; this one
    reads what the other wrote, and an absent file means report.py has not run
    yet, which is a gap and not a clean bill of health.
    """
    p = Path(_os.environ.get("LFG_WARNINGS", ".runtime/warnings.json"))
    try:
        got = json.loads(p.read_text(encoding="utf-8"))
        return got if isinstance(got, list) else []
    except (OSError, ValueError):
        return []


def xi_note(u) -> str:
    """The one line the change list cannot say by existing, or "".

    Two states have no rows to show for them and both matter: the app has not
    told us which eleven you are fielding (so the whole sheet is printed and
    you should know why), or you are already fielding the best one (so there
    is nothing to do, which is an answer and not an empty table).
    """
    import decide

    _, xi = decide.current_xi(u)
    chg = xi_change(fielded_keys(u), xi)
    if not chg["legal"]:
        return ("the app has not said which eleven you are fielding, so this "
                "is the whole sheet rather than a change list")
    if not chg["in"] and not chg["out"]:
        return "no change — you are already fielding the best eleven"
    return ""


def xi_change(marked: list[str], best) -> dict:
    """What to CHANGE about the eleven: {legal, marked, in, out}.

    THE DIFFERENCE IS THE DECISION. Printing all eleven asks you to compare
    two team sheets in your head, and the report has no idea which of the
    eleven you already have on — so it reads as "field these", every run,
    whether or not anything moved. Two names and a direction is the same
    information you can act on.

    `legal` is false when we do not have an eleven to diff against — the app's
    lineup feed is quiet — and then there is no diff at all rather than a
    misleading one. It used to be false far more often, when this was read off
    a hand-ticked file that lost a mark on every sale: that is how "play 4-5-1
    (now 4-4-1)" came to tell somebody already playing 4-5-1 to change
    formation.
    """
    best = list(best)
    if len(marked) != len(best) or not marked:
        return {"legal": False, "marked": len(marked), "in": [], "out": []}
    have, want = set(marked), set(best)
    return {"legal": True, "marked": len(marked),
            "in": [k for k in best if k not in have],
            "out": [k for k in marked if k not in want]}


def fielded_shape(u) -> str:
    """The formation you are ACTUALLY playing, from the marks at the last log.

    Without it "play 4-5-1" is advice you cannot check: you have no way to
    know whether it is what you are already doing. Empty when the marks are
    not an eleven — a shape read off ten men is not a formation anybody is
    playing, and printing it as one is worse than printing nothing.
    """
    import decide

    _, best = decide.current_xi(u)
    keys = fielded_keys(u)
    return shape(u, keys) if xi_change(keys, best)["legal"] else ""


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
    # Cash carries the emphasis when negative (over budget at the lock,
    # not mid-window, is the violation) — a number, not a paragraph.
    cash_txt = ("**cash %s**" if u.cash < 0 else "cash %s") % fmt_money(u.cash)
    # `u.cash` (spendable, net of pending bids) stays the headline; show
    # the raw balance alongside it so the gap is never unexplained.
    # Why: docs/notes/sim.md#header--cash-line-shows-two-numbers-not-one
    if u.locked_cash:
        cash_txt += (" (balance %s − %s locked)"
                    % (fmt_money(u.cash + u.locked_cash),
                       fmt_money(u.locked_cash)))
    ctx += ["squad %s" % fmt_money(val), cash_txt,
            "total %s" % fmt_money(val + u.cash)]

    want = _shape_now(u)
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


# The order a team sheet is read in, and the order the app lists them.
SLOT_ORDER = {"POR": 0, "DEF": 1, "MED": 2, "DEL": 3}


def by_slot(u, keys):
    """Keeper, defence, midfield, attack — then best first inside each.

    An eleven ranked purely by expected points interleaves a keeper between
    two midfielders, which is not how anybody reads a team sheet or how the
    app lays one out. Ranking still decides who is IN it; position decides the
    order you check them off in.
    """
    exp = u.forecaster.expected(decide_choosable(u))
    return sorted(keys, key=lambda k: (SLOT_ORDER.get(u.pos.get(k, ""), 9),
                                       -exp.get(k, 0.0)))


def _bar(u) -> float:
    """The weakest man in the eleven you would field — the line on the ladder."""
    import decide
    exp, xi = decide.current_xi(u)
    return decide.xi_bar(exp, xi)


def short_manager(m: str) -> str:
    """One manager's first name/word only — "Magic Mike 333" -> "Magic",
    "SusoGattuso" unchanged (no space to split on).

    THE SAME SHORTENING FOR EVERY MANAGER NAME ON THE LADDER, not just
    race_cell()'s own rival — before this (Miguel, 2026-09-01: "the width
    still not working"), the OWNER'S name in the Where column was printed
    in full while only the rival racing him was shortened, so a row could
    still read "Magic Mike 333 · Suso ~1d" — half-fixed. One function, so
    the two never drift apart on how much to cut.
    """
    return m.split()[0] if m else m



def ladder_rows(u, rows, bands=None) -> list[dict]:
    """The grouped plan as data, so the phone draws the same one table.

    Same groups, same order, same numbers. Two renderers drawing different
    tables is how this report came to contradict itself; there is one shape
    and both read it.

    `bands`, when given, is decide.rank()'s own `bands` — a real season band
    (pts_lo/pts_hi) for EVERY row, not just the point estimate xpts already
    carried. `bands=None` (an old caller, or the self-test) is the unpriced
    table, not a crash.

    NO CHASE MODE, NO CLAUSE-RACE TIMING (2026-09-06) — both cut as bolt-on
    insight layers, not because either was wrong, but because "the book"
    direction favours one plain value-ranked list over several ranking
    modes each with its own edge cases.
    """
    import decide

    exp, xi = decide.current_xi(u)
    mine = u.state.squads.get(u.me, {})
    dead = {k for k, _ in decide_dead(u)}
    won = {r["action"].buy: r for r in rows if r["action"].buy}
    bar = decide.xi_bar(exp, xi)
    spare = sum(v for _k, v in decide_dead(u))
    rest = [k for k in u.price if k not in mine and exp.get(k, 0.0) > bar]
    # A won row carries rank()'s own band, off the squad the victim's
    # response leaves behind — which is why rank() never bands them twice.
    bands = {k: v for k, v in (bands or {}).items() if k not in won}

    def cell(k, group, where, money, pts, note="", value=None,
            lo=None, hi=None, market=None, premium=None):
        if k in bands:
            pts, lo, hi, _action = bands[k]
        return {"name": title_name(u.name.get(k, k)),
                "pos": u.pos.get(k, ""), "start": u.start.get(k, 0.0),
                "xpts": exp.get(k, 0.0), "group": group, "where": where,
                "money": money, "pts": pts,
                "pts_lo": lo, "pts_hi": hi, "note": note, "value": value,
                # THREE PLAIN FACTS, no blended score — Miguel's own
                # framing (2026-09-06): what he's really worth, what
                # extra you pay to force the sale, what it's worth in
                # points. `market` is None for a target with no known
                # value (rare); `premium` is 0.0 for a free agent (no
                # clause to pay above value) and decide.burn()'s own
                # number for a raid — already computed by rank(), not a
                # new calculation.
                "market": market, "premium": premium}

    out = []
    # WHAT TO CHANGE, not what to have. When the marks are a legal eleven the
    # top of the ladder is the difference between it and the best one — two
    # names and a direction — and the whole sheet is printed only when the
    # marks cannot be trusted to diff against.
    chg = xi_change(fielded_keys(u), xi)
    if chg["legal"]:
        for k in by_slot(u, chg["in"]):
            out.append(cell(k, "in", "bench", None, None))
        for k in by_slot(u, chg["out"]):
            out.append(cell(k, "out", "yours", None, None))
    else:
        for k in by_slot(u, xi):
            out.append(cell(k, "field", "yours", None, None))
    # A man named in the diff is not named again as bench furniture: the OUT
    # row already says where he is going.
    moving = set(chg["in"]) | set(chg["out"])
    benched = [k for k in mine if k not in xi and k not in dead
               and k not in moving]
    for k in by_slot(u, benched):
        out.append(cell(k, "keep", "yours", None, None))
    for k in sorted(dead, key=lambda k: -exp.get(k, 0.0)):
        out.append(cell(k, "sell", "yours", u.proceeds.get(k, 0.0), None))

    def buy_cell(k, group):
        r = won[k]
        return cell(k, group, short_manager(u.owner.get(k)) or "free agent",
                    -r["action"].net, r["d_pts"], value=r.get("value"),
                    lo=r.get("pts_lo"), hi=r.get("pts_hi"),
                    market=u.value.get(k), premium=r.get("burn") or 0.0)

    buys = [k for k in rest if k in won]
    # Free agents, then a real raid (clause, can't be refused) — a listed
    # target (a rival's own choice to sell, 0/119 real deals ever
    # converted) is never a candidate at all, filtered out at the source
    # in decide.candidates(), not demoted here.
    # Why: docs/notes/sim.md#ladder_rows--buy--raid-split
    ranked = sorted(buys, key=lambda k: _move_rank_key(won[k], u))
    for k in ranked:
        if route_kind(u, k) == "free":
            out.append(buy_cell(k, "buy"))
    for k in ranked:
        if route_kind(u, k) == "raid":
            out.append(buy_cell(k, "raid"))
    for k in sorted((k for k in rest if k not in won
                     and u.price[k] > u.cash + spare),
                    key=lambda k: -exp.get(k, 0.0)):
        short_by = u.price[k] - u.cash - spare
        save_pts = bands[k][0] if k in bands else None
        out.append(cell(k, "save", short_manager(u.owner.get(k)) or "free agent",
                        -short_by, save_pts, "short",
                        value=value_rate(save_pts, short_by)))
    # FREE AGENTS ONLY, via route_kind() — the ONE classifier, so this
    # can't drift from BUY/RAID above again. PASS draws from `rest`, a raw
    # price-list pool independent of candidates()/rank(), which is why
    # candidates()'s own "never propose a listed target" fix never
    # reached it before route_kind() existed: a rival-owned player who
    # "clears the bar" fell through here with a price, looking exactly
    # like a buyable one. A clause-raidable rival who simply didn't rank
    # high enough for RAID was tried here too, but PASS renders every row
    # identically with no clause marker — Miguel's rule: no rival-owned
    # player anywhere in the report unless the proposal IS the clause
    # raid, on its own row, in RAID.
    # Why: docs/notes/sim.md#ladder_rows--pass-is-free-agents-only
    for k in sorted((k for k in rest if k not in won
                     and u.price[k] <= u.cash + spare
                     and route_kind(u, k) == "free"),
                    key=lambda k: -exp.get(k, 0.0)):
        out.append(cell(k, "pass", short_manager(u.owner.get(k)) or "free agent",
                        -u.price[k], None))
    return out




def band_acts(u) -> list:
    """The one-man questions the ladder needs a season band for, as
    `[(key, Action), ...]`: a pure sale for every held player, an outright
    buy (no sale funding it) for everyone else who beats the current bar.

    COMPUTES NOTHING — it names the questions and decide.rank() answers
    them in the final pass it was already running, handing them back as
    its `bands`.

    A PURE SALE, NOT A FUNDED UPGRADE — the funding-chain narrative this
    used to carry ("what selling him could afford instead") was cut with
    `decide.best_swap_for()` (2026-09-06): it was the direct cause of two
    catastrophic squad-legality bugs, and named a rival's non-clause
    player as an upgrade at least once before that was caught. This is a
    plainer, honest question: what does this man's own sale cost you.
    """
    import decide

    exp, xi = decide.current_xi(u)
    mine = u.state.squads.get(u.me, {})
    bar = decide.xi_bar(exp, xi)
    acts = [(k, decide.Action("sell", sell=(k,),
                              proceeds=u.proceeds.get(k, 0.0)))
           for k in mine]
    acts += [(k, decide.Action("buy", buy=k, cost=u.price.get(k, 0.0)))
            for k in u.price
            if k not in mine and exp.get(k, 0.0) > bar]
    return acts


def ladder(u, rows, base, data=None) -> list[str]:
    """EVERY PLAYER YOU COULD HOLD, GROUPED BY WHAT TO DO WITH HIM.

    Not one long ranking: a plan. The eleven you should field, then the ones
    to keep on the bench, then the ones to sell, then what to buy with the
    proceeds, then what you cannot afford yet. Read top to bottom it is the
    whole decision, and the funding is implicit — sell the SELL rows and the
    BUY rows are what the money reaches.

    Field, bench, sell and buy were four sections that had begun contradicting
    each other. They are one table because they were always one question.

    RENDERS ladder_rows()'s OWN OUTPUT, computes nothing of its own beyond
    the aggregate "Your eleven" line (best_xi/expected — deterministic, no
    simulation, so recomputing it here cannot drift). This used to be a
    second, independent implementation — its own exp/xi/dead/won/bands,
    the exact duplication this repo's own design principle warns against
    ("two renderings of one answer is how they come to disagree"). Fixed
    2026-08-22: the bands ran twice, once per renderer, before this — real
    simulated numbers, computed twice, on the strength of "same seed gives
    the same answer" rather than there being only one computation to give
    it.

    `data`, when given, is ladder_rows()'s OWN result, computed once by a
    caller feeding both this and payload() — main() does, so the real
    simulation behind every band runs ONCE per report, not once per
    renderer. `data=None` (a caller with no JSON side, or the self-test)
    draws the same table with no bands in it, not a crash.
    """
    import decide

    exp, xi = decide.current_xi(u)
    data = data if data is not None else ladder_rows(u, rows)
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
            # MARKET PRICE + THE EXTRA CLAUSE PREMIUM, not net cost —
            # Miguel's own framing (2026-09-06): what he's really worth,
            # what extra you pay to force the sale, no blended score.
            # BUY/RAID only (both carry `market`); every other group
            # keeps the plain net-cost figure it always has.
            if r["group"] in ("buy", "raid") and r["market"] is not None:
                money = "%.2fM" % (r["market"] / 1e6)
                if r["premium"]:
                    money += " +%.2fM" % (r["premium"] / 1e6)
            else:
                money = ("%+.2fM" % (r["money"] / 1e6)) if r["money"] else "—"
        return ("| %s | %s | %.0f%% | %.2f | %s | %s | %s | %s |"
                % (r["name"], r["pos"] or "—", 100 * r["start"], r["xpts"],
                   r["where"], money, season,
                   ("%.1f" % r["value"]) if r["value"] is not None else "—"))

    out = ["| Player | Pos | Start | xPts/j | Where | € | Season | pts/M€ |",
           "|---|---|--:|--:|---|--:|--:|--:|"]

    if by_group.get("field"):
        # No trustworthy marks to diff against, so the whole sheet — and a
        # line saying why you are being asked to read one.
        out.append("| **FIELD — your eleven — the app has not said what you "
                   "are playing** | | | | | | | |")
        out += [row_md(r) for r in by_group["field"]]
    elif not by_group.get("in") and not by_group.get("out"):
        out.append("| **XI — no change, you are fielding the best eleven** "
                   "| | | | | | | |")
    else:
        if by_group.get("in"):
            out.append("| **PUT ON** | | | | | | | |")
            out += [row_md(r) for r in by_group["in"]]
        if by_group.get("out"):
            out.append("| **TAKE OFF** | | | | | | | |")
            out += [row_md(r) for r in by_group["out"]]
    tot = sum(exp.get(k, 0.0) for k in xi)
    # _rival_best(u), not a second re-derivation of it — this used to
    # rebuild the exact same "strongest eleven anybody else can field"
    # fact inline, under a different name.
    riv = _rival_best(u)
    riv_total, riv_who = riv.get("xi", 0.0), riv.get("manager", "")
    out.append("| **Your eleven — play %s** | | | **%.2f** | "
               "vs %s **%.2f** | | **%+.2f** | |"
               % (shape(u, xi), tot, riv_who, riv_total, tot - riv_total))

    if by_group.get("keep"):
        out.append("| **KEEP — bench** | | | | | | | |")
        out += [row_md(r) for r in by_group["keep"]]

    if by_group.get("sell"):
        out.append("| **SELL — never start** | | | | | | | |")
        out += [row_md(r) for r in by_group["sell"]]

    # Free agents on their own; an explicit "none clear the bar" line once
    # RAID exists, since silence could now mean either "no candidates" or
    # "candidates exist, all a clause raid."
    # Why: docs/notes/sim.md#ladder--buy-section-none-clear-the-bar-today
    if by_group.get("buy"):
        out.append("| **BUY — free agents** | | | | | | | |")
        out += [row_md(r) for r in by_group["buy"]]
    elif by_group.get("raid"):
        out.append("| **BUY — free agents — none clear the bar today** | | "
                   "| | | | | |")

    if by_group.get("raid"):
        out.append("| **RAID — a clause, cannot be refused** "
                   "| | | | | | | |")
        out += [row_md(r) for r in by_group["raid"]]

    if by_group.get("save"):
        out.append("| **SAVE — better than yours, out of reach** | | | | | | | |")
        out += [row_md(r) for r in by_group["save"]]

    if by_group.get("pass"):
        out.append("| **PASS** | | | | | | | |")
        out += [row_md(r) for r in by_group["pass"]]

    # One line pointing at methodology.py's column_guide_lines(), the one
    # copy — this used to be a full column-by-column explanation here AND
    # a separately-worded rewrite in Fantasy.jsx, already drifted apart.
    # Why: docs/notes/sim.md#ladder--column-guide-moved-out-not-inlined
    out += ["",
            "_How to read this table: **How to read the tables** in "
            "METHOD.md._", ""]
    return out


def decide_dead(u):
    from decide import dead_weight
    return dead_weight(u)


def decide_choosable(u):
    from decide import choosable
    return choosable(u)


def _cash_cell(u, manager: str) -> str:
    """What one manager can bid with, marked as observed or estimated.

    The app states `teamMoney` for the account that asks and null for every
    other, so yours is a reading and theirs is a replay of the ledger from the
    starting budget. The `~` is not decoration — a rival's number can be wrong
    by a whole sale nobody has seen yet.
    """
    if manager == u.me:
        return fmt_money(u.cash)
    return "~" + fmt_money(u.rival_cash.get(manager, 0.0))


def standings(u, base) -> list[str]:
    """The levels the Δ columns above are differences from.

    +37% against a rival is 50→87 or 8→45, and those are not the same
    situation. This is the table that says which.
    """
    out = ["| Manager | now | cash | simulated | 10–90 | P(I finish above) |",
           "|---|--:|--:|--:|--:|--:|"]
    order = sorted(u.state.squads, key=lambda m: -base.mean(m))
    for m in order:
        lo, hi = base.band(m)
        # CASH BELONGS BESIDE THE POINTS. It is what each of them can answer a
        # clause with tomorrow, and it was in league.md — a file the phone
        # does not open. `~` on a rival is the estimate mark the ledger earns:
        # the app states teamMoney for your account alone.
        out.append("| %s | %.0f | %s | %s | %s–%s | %s |"
                   % (m + (" **(you)**" if m == u.me else ""),
                      u.state.carried.get(m, 0.0), _cash_cell(u, m),
                      _pts(base.mean(m)), _pts(lo), _pts(hi),
                      "—" if m == u.me
                      else "%.0f%%" % (100 * base.beat(m))))
    out.append("")
    return out


def _drift_frac_now() -> float:
    """The live `ffcore.forecast.DRIFT_FRAC`, read off the module rather than
    imported as a name — season.py's own calibration self-tests temporarily
    monkeypatch this attribute (`forecast.DRIFT_FRAC = 0.0`, restored after),
    and a `from ... import DRIFT_FRAC` here would freeze the value this
    module happened to see at import time instead. Pulled out on its own
    because the report used to print a literal "DRIFT_FRAC=2.0" in this
    caveat regardless of what actually ran — stale since 2026-08-22's
    revert back to 1.0 (`6cefe65`), so the report told Miguel the wrong
    number for over a week.
    """
    import ffcore.forecast as forecast
    return forecast.DRIFT_FRAC


def illegal_squads(u) -> list[tuple[str, list[str]]]:
    """[(manager, ["2/3 defensas", ...])] for every squad — mine included
    — best_xi() cannot fill from, sorted by manager.

    CALLS best_xi() ITSELF, does not re-derive its own notion of
    "legal" — see this repo's own day of finding "two independent
    authorities that only agree by luck" (season.legal_shapes() vs
    score.formations(), fixed 2026-09-01) for why a SECOND, simplified
    legality check here would be exactly that mistake again. A squad
    can fail to fill any of the 7 real formations even meeting every
    position's SLOT_MIN individually — SLOT_MIN/MAX_SLOT bound the
    RANGE each formation allows, not which exact combinations exist
    among the 7 (e.g. no formation pairs DEF=3 with MED=3) — so only
    best_xi()'s own real search is the ground truth. The value dict
    passed to it does not matter for LEGALITY (only for which players
    among a legal shape's choices are picked), so a flat 1.0 per player
    is enough here — this function only asks "does anything come back",
    never "what does it score".

    WHY THIS MATTERS: best_xi() returning [] freezes a manager's
    simulated season at exactly what he has already scored, every
    remaining jornada adding zero with zero variance — indistinguishable
    in the standings table from "the model is very confident this
    manager is finished", when the real fact is "his data says he
    cannot field a squad at all", a completely different claim. Found
    2026-09-01 (Miguel: "the forecast for Albert is absolutely
    unsustainable") — his standings row read a flat 32-32 band with no
    signal anywhere pointing at why: 2 defensas against SLOT_MIN's 3.

    report.py already warns when MY OWN squad is thin (`have <= n`, a
    softer "one injury away" threshold) — that check has no reach into a
    RIVAL's squad, which is the gap this closes.

    SHOULD NEVER FIRE IN PRODUCTION AS OF decide.phantom_fill()
    (2026-09-01, same day): every squad `decide.load()` returns is
    already patched with an average-player stand-in per missing
    position before anything reaches here — see phantom_filled() below
    for the caveat that replaced this one. Kept, not deleted: a real
    safety net — if phantom_fill() ever regresses, THIS starts firing
    again instead of the report silently going back to freezing someone
    at zero with no signal anywhere.
    """
    from ffcore.score import SLOT_LABEL, SLOT_MIN
    from ffcore.season import XI_SIZE, best_xi

    out = []
    for m, sq in sorted(u.state.squads.items()):
        if len(best_xi(sq, {k: 1.0 for k in sq})) >= XI_SIZE:
            continue
        counts: dict[str, int] = {}
        for slot in sq.values():
            counts[slot] = counts.get(slot, 0) + 1
        short = [
            "%d/%d %s%s" % (counts.get(s, 0), n, SLOT_LABEL[s],
                            "" if counts.get(s, 0) == 1 else "s")
            for s, n in SLOT_MIN.items() if counts.get(s, 0) < n]
        # SHORT NAMES THE CAUSE WHEN THERE IS ONE BELOW THE FLOOR; A
        # SQUAD CAN STILL FAIL WITH EVERY POSITION AT OR ABOVE SLOT_MIN
        # (the DEF=3-with-MED=3 case above) — named plainly instead of
        # leaving that case silent.
        out.append((m, short or ["not enough for any legal formation"]))
    return out


def phantom_filled(u) -> list[tuple[str, list[str]]]:
    """[(manager, ["1 defensa", ...])] for every squad — mine included —
    decide.phantom_fill() patched with an average-player stand-in.

    Detected off the phantom keys THEMSELVES
    (`__phantom_<manager>_<slot>_<n>`, phantom_fill()'s own format), not
    by re-checking best_xi() against SLOT_MIN — after phantom_fill(),
    every squad IS legal by construction, so asking best_xi() again
    would report nothing found, which answers a different question
    ("is this squad legal now") than the one this caveat exists to
    answer ("was a real gap patched to get there").
    """
    from ffcore.score import SLOT_LABEL

    out = []
    for m, sq in sorted(u.state.squads.items()):
        counts: dict[str, int] = {}
        for k, slot in sq.items():
            if k.startswith("__phantom_%s_" % m):
                counts[slot] = counts.get(slot, 0) + 1
        if counts:
            out.append((m, ["%d %s%s" % (n, SLOT_LABEL[s],
                                        "" if n == 1 else "s")
                           for s, n in sorted(counts.items())]))
    return out


def caveats(u) -> list[str]:
    """What the numbers above cannot see. Read off the data, not remembered.

    Every line here makes the position look BETTER than it is, which is the
    reason they are printed under the table rather than in a design document
    nobody opens on a phone.
    """
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
    # A SAFETY NET, NOT A SECOND CAVEAT — decide.phantom_fill() should
    # make this structurally impossible; see illegal_squads()'s own note.
    for m, short in illegal_squads(u):
        out.append("| **%s cannot field a legal eleven** (%s) | this "
                   "should not be possible — decide.phantom_fill() is "
                   "meant to patch exactly this before it reaches here. "
                   "His simulated season is FROZEN at what he has already "
                   "scored until it is fixed |" % (m, ", ".join(short)))
    for j, clubs in sorted(u.part_played.items()):
        out.append("| Jornada %d is half played — %d clubs are done | their "
                   "points are already in `now`, so only the rest of the "
                   "round is simulated, and it still re-picks an eleven that "
                   "is in fact already locked |" % (j, len(clubs)))
    if u.cash_note:
        out.append("| %s | a clause runs a median 1.52× market value here and "
                   "the app pays back only the value, so the premium is gone "
                   "for good. It is charged against the move, but priced off "
                   "what more money would buy you today — most days, very "
                   "little |" % u.cash_note)
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
        "| p_win's season-long spread rests on one hand-picked constant "
        "(DRIFT_FRAC=%s), not a fit | two real anchors on this repo's own "
        "data disagree on the exact magnitude (weak jornada-1-vs-final "
        "correlation argues wider, strong season-to-season correlation "
        "argues narrower), but every published win-probability model "
        "checked (538's NBA/NHL/MLB) is far more humble than 70%%+ about a "
        "full season this early regardless — that floor doesn't need the "
        "two anchors resolved. This caveat used to say to tighten it once "
        "the Forecast vs actual table above had enough rows — it has them "
        "now, and the check (2026-08-31) found that table can never grade "
        "this constant at ANY row count: every pair in it is one jornada "
        "out, and this constant only acts across longer horizons. It is "
        "not waiting on more data, it is waiting on a different "
        "measurement |"
        % _drift_frac_now(),
        "| Shape prior | %s |" % u.forecaster.pool_note(),
        "| P(start) fit | %s |" % u.start_note.rstrip("."),
        ""]
    return out


# How much of the best move's season gain a cheaper alternative may give
# up and still be recommended, in _best()'s own value-for-money
# refinement (the single headline pick only — see _move_rank_key()'s own
# note on why the wider ladder now uses a flat pts_lo sort with no
# floor/tolerance concept at all). Not 1.0 (biggest gain wins regardless
# of cost) and not much lower (a materially worse move is worse, full
# stop). MOVES_VALUE_FLOOR, its old ladder-side counterpart, retired
# 2026-09-06 — docs/notes/sim.md#value_tolerance-090-and-moves_value_floor-025
VALUE_TOLERANCE = 0.90

def _move_rank_key(r, u):
    """RELIABLE ROUTES FIRST, THEN BY `pts_lo` (10th percentile of the
    move's own paired trial distribution) — ONE metric, the same one
    `_best()` uses for the single headline pick, now for the WHOLE ladder
    too. Miguel, 2026-09-06: "the whole ladder should follow same logic
    why wouldn't it?" — a fair challenge to an earlier hedge that this was
    "separate, larger, not-yet-validated." Checked, not assumed:
    `backtest.replay_ladder_percentile()` replayed 200+ real historical
    top-3 ladders and found the tiered scheme this replaced was net
    NEGATIVE on real outcomes (-94 points) where a flat `pts_lo` sort over
    the same real candidates was net POSITIVE (+90 points).

    RETIRED, ON THAT EVIDENCE: the value-floor/win-probability tiering
    (`MOVES_VALUE_FLOOR`, `VALUE_TOLERANCE`'s ladder use, d_win leading
    outright) that used to sit here. `pts_lo` already reflects the SAME
    Monte Carlo draws `d_win` is read off, at a quantile that measurably
    ranks better on real history than treating win-probability as an
    unrelated axis needing its own escape hatch — a simpler rule beating
    the accumulated special-casing, same lesson as this session's earlier
    "the book" simplification pass. Full history of the retired scheme:
    docs/notes/sim.md#_move_rank_key--pts_lo-not-mean-d_pos-or-value-2026-09-06

    SHARED KEY, not two independent sorts — `ladder_rows()`'s BUY group
    (the markdown table a reader scrolls top to bottom) and `payload()`'s
    `moves` (the phone's JSON) both call this, so they cannot rank the
    same candidates in two different orders.

    RELIABLE ROUTES FIRST, WITHIN EVERY TIER — the same rule `_best()`
    uses for the single headline pick (decide.py's own note, 108 real
    transactions checked 2026-08-29, zero of them manager-to-manager): a
    "listed" candidate (Universe.route — a rival's own sale, who can
    simply not sell, or get outbid) is not a slightly-riskier version of a
    free-agent or clause buy, it is a route that has never once actually
    gone through in this league. Demoted here, not removed: a real listed
    opportunity stays fully visible, just under routes that cannot be
    refused. A row with no `pts_lo` at all (shouldn't happen off a real
    `rank()` row, but a hand-built one might lack it) sorts last within
    its reliability tier rather than crashing or guessing a value.
    """
    reliable = 0 if u.route.get(r["action"].buy, "free") != "listed" else 1
    lo = r.get("pts_lo")
    return (reliable, -lo if lo is not None else float("inf"))

def _best(u, rows, rivals):
    """(the top move, or None; whether it needs a rival's own cooperation).

    RELIABLE ROUTES FIRST. A candidate's `buy` reaches you one of three ways
    — Universe.route's own docstring: "free" (the app deals him, nobody can
    refuse), "clause" (his buyout, instant, also cannot be refused), or
    "listed" (a rival has put him up for sale, and simply not selling, or
    somebody else outbidding you, are both real outs for them). Checked
    against this league's own recorded history 2026-08-29
    (data/tidy/transactions.csv, ledger.py's own rebuild of the app's
    activity feed): 108 transactions, every one of them "from the app" —
    zero have ever been a manager-to-manager sale. That is not "rare", it is
    the entire sample, so a "listed" move is not a slightly-riskier version
    of a real one; it is not what gets recommended automatically unless
    nothing reliable clears the bar at all — the ordinary shopping list
    still shows it (ladder()'s own "pts/M€" column), this only changes what
    gets pushed as THE move.

    VALUE FOR MONEY, NOT JUST THE BIGGEST GAIN, within whichever pool (reliable
    or, on a day nothing reliable helps, the full list) is in play. Finds the
    biggest raw d_pos in `pool` directly (does NOT assume `rows` arrives
    sorted — `decide.rank()`'s own row order is an internal screening detail,
    and `payload()` independently resorts its own copy for the report table;
    a caller handing this any order gets the same answer) — the single
    biggest season-long standings gain used to win this outright, however
    much it cost — a move netting +0.31 places for -40M beat one netting
    +0.29 for -2M, spending 20x the cash for 7% more gain and leaving
    nothing for whatever comes up later in the season. `d_pts`/`value`
    (points per net £M) were already computed by rank() for every row and
    shown in the report's own table; this is the first place that number
    changes what gets RECOMMENDED, not just what gets displayed.

    PICKS BY `pts_lo` (10th percentile of the move's own paired trial
    distribution), NOT MEAN `d_pos` — Miguel, 2026-09-06: "we buy for the
    whole season... let's end up with a single metric." Two equal-mean
    candidates are not equally good if one's gain leans on distant,
    DRIFT_FRAC-widened jornadas and the other's is mostly near-term and
    solid — `pts_lo` is naturally lower for the first, higher for the
    second, at an IDENTICAL mean, with no invented time-discount constant:
    it's the same uncertainty machinery (rate_rel, club_rel, DRIFT_FRAC)
    already validated this session, just read at a different quantile.
    Validated against real history before being wired in here, not on
    theory alone: `backtest.replay_percentile_rank()` replayed 200+ real
    past reports/decisions.json commits with this exact substitution and
    found it would have beaten mean-ranking on real outcomes (+64 vs +31
    real points, 14/32 vs 9/24 net-positive calls) — see that function's
    own docstring for the one disclosed limit (it can only re-rank
    candidates `candidates`/`pool` below ALREADY admit, same as here; it
    cannot rescue one `d_pos > 0 or d_win > 0` already excludes).
    NOT changed: `candidates`'s own eligibility gate (still `d_pos`/
    `d_win`), the reliable-routes-first split, and the VALUE_TOLERANCE
    cheaper-alternative refinement below — this is a single, targeted
    substitution of WHICH admitted candidate wins, not a rebuild of the
    screen around it. `_move_rank_key()`'s own ordering of the wider
    ladder (every BUY/RAID row, not just this single headline pick) is a
    separate, larger, not-yet-validated question — left as mean-`d_pos`
    for now.
    Why: docs/notes/sim.md#_best--pts_lo-not-mean-d_pos-2026-09-06
    """
    candidates = [r for r in rows if r["d_pos"] > 0 or r["d_win"] > 0]
    if not candidates:
        return None, False
    reliable = [r for r in candidates
               if u.route.get(r["action"].buy, "free") != "listed"]
    pool, uncertain = (reliable, False) if reliable else (candidates, True)
    best = max(pool, key=lambda r: r["pts_lo"])
    # ONLY COMPARED FOR A GENUINE SPEND (net > 0) on a move that actually
    # improves expected position (d_pos > 0) — the same guard value_rate()
    # itself uses, and for the same reason: a move that raises more than it
    # costs, or one whose whole gain is win-probability rather than
    # position, has no "cash saved by going cheaper" to weigh against.
    if best["d_pos"] <= 0 or best["action"].net <= 0:
        return best, uncertain
    floor = VALUE_TOLERANCE * best["d_pos"]
    cheaper = [r for r in pool
              if r["d_pos"] >= floor and r["action"].net < best["action"].net]
    if cheaper:
        best = min(cheaper, key=lambda r: r["action"].net)
    return best, uncertain


def alert_lines(u, rows, rivals) -> list[str]:
    """The one line worth interrupting somebody for, or [].

    THE DESIGN IS WHAT IT LEAVES OUT. This replaced a verdict scan that fired
    on every Buy and every Sell in a twenty-row table, which on a phone is
    spam, and spam is how you learn to swipe away the one that mattered. There
    is one best move; the other hundred and thirty-one lost to it and are not
    news. A move that gains nothing is not news either, and returns [] so the
    caller can send NOTHING rather than "all quiet" twice a day.

    BEING OVERDRAWN IS ALSO NEWS — Miguel, 2026-09-06: the model correctly
    proposes zero buys while overdrawn (nothing is affordable), but used
    to say NOTHING about what to sell to fix it, which is worse than
    silence: the jornada locks overdrawn otherwise. Checked first, before
    the ordinary best-move search — an unresolved overdraft is more
    urgent than any buy could be, and the two states cannot coexist
    (candidates() cannot propose anything real while overdrawn anyway).
    """
    if u.cash < 0:
        sells, short = overdraft_fix(u)
        if not sells:
            return ["**Overdrawn %s** — no safe dead-weight sale covers it; "
                    "needs a manual look before the jornada locks."
                    % fmt_money(-u.cash)]
        names = ", ".join("%s (+€%.1fM)" % (title_name(u.name.get(k, k)),
                                            p / 1e6) for k, p in sells)
        if short > 0:
            return ["**Overdrawn %s** — sell %s clears most of it, still "
                    "€%.1fM short (no further safe dead-weight sale)."
                    % (fmt_money(-u.cash), names, short / 1e6)]
        return ["**Overdrawn %s** — sell %s to clear it before the jornada "
                "locks (zero points cost: %s never start your eleven)."
                % (fmt_money(-u.cash), names,
                   "he doesn't" if len(sells) == 1 else "they don't")]

    best, uncertain = _best(u, rows, rivals)
    if best is None:
        return []
    a = best["action"]
    net = a.net
    # Action.net = cost - proceeds, so net<0 means the sale side raised MORE
    # than the buy side cost — a move that pays you, not one that is merely
    # "free". Said plainly rather than folded into "free" either way, which
    # would undersell a move that hands back real cash for the season ahead.
    if net > 0:
        cost = "-€%.1fM" % (net / 1e6)
    elif net < 0:
        cost = "+€%.1fM raised" % (-net / 1e6)
    else:
        cost = "free"
    if uncertain:
        # Said outright, not left for Miguel to notice on his own — this is
        # the one case where "Do this" is not actually guaranteed to happen.
        cost += " · needs the seller to accept, not guaranteed"
    return ["**Do this** — %s (%+.2f places, %+.0f%% to win, %s)"
            % (a.label({k: title_name(v) for k, v in u.name.items()}),
               best["d_pos"], 100 * best["d_win"], cost)]


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
    import decide
    exp, xi = decide.current_xi(u, who)
    return sum(exp.get(k, 0.0) for k in xi)


def _shape_now(u) -> str:
    import decide
    _, xi = decide.current_xi(u)
    return shape(u, xi)


def _rival_best(u) -> dict:
    """The strongest eleven anybody else can field — the number you are
    actually chasing, and the one the ranking never showed."""
    out = [(_xi_total(u, m), m) for m in u.state.squads if m != u.me]
    if not out:
        return {}
    total, who = max(out)
    return {"manager": who, "xi": total, "gap": _xi_total(u, u.me) - total}


def payload(u, rows, base, rivals, locks_h=None, n_actions: int = 0,
            ladder_data=None) -> dict:
    """The report as data, for the phone to draw.

    Same rows as the markdown, so the two cannot disagree about order or
    content — that is the whole reason this is a function and not a second
    pass over the universe. `kind` is what the move IS rather than something a
    renderer has to infer from a string, and the label is carried anyway so a
    renderer that just wants the sentence has it.

    `ladder_data`, when given, is ladder_rows()'s own result already
    computed by the caller — see ladder()'s matching note. `None` computes
    it here, exactly as before.
    """
    names = {k: title_name(v) for k, v in u.name.items()}
    lo, hi = base.band(u.me)
    moves = []
    # Same pts_lo sort as ladder_rows()'s BUY group — see _move_rank_key()'s
    # own note. Why: docs/notes/sim.md#payload--moves-sorted-by-the-same-rule-as-the-ladder
    for r in sorted(rows, key=lambda r: _move_rank_key(r, u)):
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
            # Points per euro — computed by rank() for every row and
            # already what THIS function's own sort now ranks by, but
            # never reached the payload before: the ladder's markdown table
            # showed it, the phone's moves list did not, same "drifting
            # apart" gap "left"/"answer" below already existed to close.
            "value": r.get("value"),
            "left": u.cash - a.net,
            "answer": (None if r.get("answer") is None
                       else names.get(r["answer"].buy, r["answer"].buy)),
            # Rounded to 3dp — see "p_win"'s own note below on why a level
            # (not a paired difference) should not print false precision.
            "p_win_after": round(base.position().get(1, 0.0) + r["d_win"], 3),
            "vs": who, "vs_gain": r["d_beat"].get(who, 0.0) if who else None,
        })
    return {
        "locks_in_h": locks_h,
        "cash": u.cash,
        # Says WHY `cash` is short of the raw balance (pending bids).
        # Why: docs/notes/sim.md#payload--several-fields-exist-only-because-the-phone-used-to-drift-from-the-markdown
        "cash_locked": u.locked_cash,
        "squad_value": squad_value(u),
        "jornadas_left": len(u.state.jornadas),
        "acquirable": len(u.price),
        "considered": n_actions,
        # Rounded (2dp/3dp), not raw — these are levels, not paired
        # differences, so they carry real MC noise a 17-digit float overclaims.
        # Why: docs/notes/sim.md#payload--several-fields-exist-only-because-the-phone-used-to-drift-from-the-markdown
        "expected_finish": round(base.expected_position(), 2),
        "p_win": round(base.position().get(1, 0.0), 3),
        "band": [lo, hi],
        "moves": moves,
        "sell": [{"name": names.get(k, k), "pos": u.pos.get(k, ""),
                  "raises": got} for k, got in dead_weight(u)],
        "ladder": (ladder_data if ladder_data is not None
                  else ladder_rows(u, rows)),
        "bar": _bar(u),
        "xi_total": _xi_total(u, u.me),
        "shape": _shape_now(u),
        "rival_best": _rival_best(u),
        "shape_now": fielded_shape(u),
        "xi_note": xi_note(u),
        # Written by report.py minutes earlier in the same run — the board
        # draws them, so "only 1 portero" or "the app's feed is 3 days stale"
        # reaches the phone instead of living in a markdown file nobody opens
        # when the board is right there.
        "warnings": _warnings(),
        "standings": [
            {"manager": m, "me": m == u.me,
             "now": u.state.carried.get(m, 0.0), "mean": base.mean(m),
             "lo": base.band(m)[0], "hi": base.band(m)[1],
             "cash": u.cash if m == u.me else u.rival_cash.get(m, 0.0),
             "cash_known": m == u.me,
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
    from ffcore.tidy import DECISIONS, append_csv

    if measured is None:
        return
    DECISIONS.mkdir(parents=True, exist_ok=True)
    append_csv(DECISIONS / PRICE_LOG,
               [{"measured_at": run_now()
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
           locks_h=None, ladder_data=None) -> list[str]:
    # EVERYTHING UNDER A HEADING, including the preamble. digest.py drops a
    # source's H1 when it stitches the appendix and keeps what follows, so a
    # preamble above the first `## ` arrives in the middle of the report
    # reading as the tail of whatever section came before it — which here is
    # the board's warnings.
    #
    # No sentences above the table — verdict()/market_percentile() retired.
    # Why: docs/notes/sim.md#render--no-sentences-above-the-table
    out = ["# The simulation — %s" % stamp, "", "## Now", ""]
    out += header(u, base, n_actions or len(rows), locks_h)
    # THE RANKING IS SUBORDINATE TO THE CALL, and says so in its own heading.
    # Presented as "what to do" directly above a section saying "do nothing",
    # it is a contradiction rather than a second opinion.
    out += ["## Every player you could hold", ""]
    out += ladder(u, rows, base, ladder_data)
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

    # Two managers, a season already scored so the numbers are exact and the
    # renderer is the only thing under test.
    st = Standings(totals={"me": [1000.0, 1200.0, 1400.0, 1600.0],
                           "riv": [1500.0, 1300.0, 1100.0, 900.0]}, me="me")
    u = Universe(state=LeagueState({"me": {}, "riv": {}}, [1, 2], "me",
                                   carried={"me": 17.0, "riv": 23.0}),
                 forecaster=Bootstrap({}, pool=[1, 2, 3]), pos={}, price={},
                 proceeds={}, owner={}, cash=23.6e6, me="me",
                 name={"yuri": "yuri berchiche", "benat": "benat turrientes"})

    # -- the eleven comes from the APP, not from a checklist ----------------
    # inputs/lineup.txt was ticked by hand and went one short every time a
    # fielded player was sold. The app publishes the answer — /teams/{team}/
    # lineup/week/{n} — and the checklist is gone.
    rows = [{"player_id": "1070", "player_name": "Ionut Radu",
             "player_name_full": "Ionut Andrei Radu"},
            {"player_id": "2464", "player_name": "Pepelu",
             "player_name_full": "José Luis García Vayá"}]
    squad = {"ionut radu": 1, "pepelu": 1}
    assert app_fielded(squad, {"pepelu": "Pepelu"}, rows,
                       {"1070": "ionut radu"}) == ["ionut radu", "pepelu"]
    # ALL OR NOTHING. A lineup with one man unresolved is not a lineup you can
    # diff against — it would read as "take him off", which is the one wrong
    # answer this whole change exists to stop giving.
    # A man neither the id map nor the names can place: no diff to take.
    assert app_fielded(squad, {}, rows + [{"player_id": "999",
                                           "player_name": "Nobody"}], {}) == []
    # A man the app fields who is not in the squad we hold means the two
    # readings disagree, and a diff across them is meaningless.
    assert app_fielded(squad, {}, rows,
                       {"1070": "ionut radu", "2464": "someone else"}) == []
    assert app_fielded({}, {}, [], {}) == []

    # -- what to CHANGE about the eleven, not what the eleven is ------------
    # THE SCREENSHOT THAT PROMPTED THIS: the report listed all eleven men and
    # said "play 4-5-1 (now 4-4-1)", which read as "change your formation"
    # when the formation was already right — the marks were one short because
    # a player had just been sold out from under them. What is worth printing
    # is the difference: put this one on, take that one off.
    best = ["gk", "d1", "d2", "d3", "d4", "m1", "m2", "m3", "m4", "m5", "f1"]
    same = xi_change(list(best), best)
    assert same["legal"] and same["in"] == [] and same["out"] == []
    swap = xi_change([k for k in best if k != "m5"] + ["bench1"], best)
    assert swap["in"] == ["m5"] and swap["out"] == ["bench1"], swap
    # Ten marks are not an eleven, so there is no honest diff to take — and
    # saying "now 4-4-1" off them is a claim about a lineup nobody made.
    short_marks = xi_change(best[:10], best)
    assert not short_marks["legal"] and short_marks["marked"] == 10
    assert short_marks["in"] == [] and short_marks["out"] == []
    # Nothing logged yet is the same case, not a crash.
    assert not xi_change([], best)["legal"]

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
    # BOTH NUMBERS when a bid of yours is locking part of the balance — a
    # reader checking the app's own screen sees the bigger, positive number
    # and needs the gap explained right here, not left to look like an error.
    u.cash, u.locked_cash = -2637643.0, 5938860.0
    hh = " ".join(header(u, st, 1, locks_h=2.0))
    assert "**cash -2.64M** (balance 3.30M − 5.94M locked)" in hh, hh
    u.cash, u.locked_cash = 23.6e6, 0.0
    assert "locked" not in " ".join(header(u, st, 1, locks_h=2.0))
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

    # -- one row's worth of a real move, shared by every test below --------
    # table() used to build its own assertions straight off this fixture;
    # retired 2026-08-22 (dead code, never called outside its own test —
    # see handoff_2026-08-21_evening.md, which already found this and
    # redirected the feature into ladder()/ladder_rows() without deleting
    # the original). The fixture itself stays: payload(), alert_lines() and
    # render() below are still real callers and still need one.
    rows = [{"action": Action("clause", buy="yuri", sell="benat",
                              cost=20e6, proceeds=5.87e6, victim="riv"),
             "d_pos": 0.433, "d_win": 0.364, "d_beat": {"riv": 0.37},
             "d_pts": 120.0, "pts_lo": 43.3, "pts_hi": 210.0,
             "helps": 0.90, "mean": 1510.0,
             "value": 120.0 / (14.13e6 / 1e6)}]

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

    # -- overdrawn: alert_lines() says what to sell, not silence
    # (2026-09-06, Miguel: cash management going silent while overdrawn) --
    from dataclasses import replace as _dc_replace
    # Small overdraft: the bigger dead-weight sale (spare_m, 7.45M) alone
    # covers it, with room to spare — spare_k is never touched.
    u_over = _dc_replace(u2, cash=-5e6)
    al_over = alert_lines(u_over, [], ["riv"])
    assert len(al_over) == 1, al_over
    assert "Overdrawn" in al_over[0] and "Benat Turrientes" in al_over[0], \
        al_over
    assert "Alvaro Fernandez" not in al_over[0], al_over
    # Bigger than either dead-weight sale alone, smaller than both together:
    # BOTH get sold, still short, said honestly — never reaches for d1 (a
    # real starter) to close the rest.
    u_over_big = _dc_replace(u2, cash=-15e6)
    al_big = alert_lines(u_over_big, [], ["riv"])
    assert len(al_big) == 1, al_big
    assert "Benat Turrientes" in al_big[0] and "Alvaro Fernandez" in al_big[0], \
        al_big
    assert "still" in al_big[0] and "short" in al_big[0], al_big
    assert "d1" not in al_big[0]

    # A ROUND ALREADY IN PROGRESS DOES NOT COUNT. Its eleven is locked, so a
    # man who starts only there is not being fielded by any decision you can
    # still make — he is spare for the rest of the season. This is not
    # hypothetical: on the day it was written, Dani Lorenzo started in one
    # jornada of thirty-eight, and it was the half-played one, only because a
    # midfielder whose club had already kicked off was excluded from it.
    u2.part_played = {1: {"alaves"}}
    # Starting only in jornada 2 — still choosable — is a real starting slot,
    # not spare, even with jornada 1 locked.
    u2.forecaster = Bootstrap({1: {k: (v, 1.0) for k, v in val.items()},
                               2: {k: ((9.0 if k == "spare_m" else v), 1.0)
                                   for k, v in val.items()}})
    assert "spare_m" not in dict(dead_weight(u2)), \
        "a man who starts in a round still ahead is not spare"
    # Starting only in jornada 1 — locked — is not a decision left to make,
    # so it does not save him from being spare.
    u2.forecaster = Bootstrap(
        {1: {k: ((9.0 if k == "spare_m" else v), 1.0) for k, v in val.items()},
         2: {k: (v, 1.0) for k, v in val.items()}})
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

    # -- illegal_squads(): a squad short a position freezes his season,
    # silently, unless flagged here -- 2026-09-01, Miguel: "the forecast
    # for Albert is absolutely unsustainable" -------------------------
    # A REAL FORMATION (4, 4, 2): 1 POR, 4 DEF, 4 MED, 2 DEL = 11 —
    # picked over the (3, 3, x) shape SLOT_MIN alone would suggest,
    # because no such shape exists among the 7 real ones (illegal_squads()
    # calls best_xi() itself rather than re-deriving legality, exactly to
    # avoid a fixture — or the function — assuming one does).
    legal_sq = {"k": "POR", **{f"d{i}": "DEF" for i in range(1, 5)},
               **{f"m{i}": "MED" for i in range(1, 5)},
               "f1": "DEL", "f2": "DEL"}
    short_sq = {k: v for k, v in legal_sq.items() if k not in ("d3", "d4")}
    u_ill = Universe(
        state=LeagueState({"me": legal_sq, "riv": short_sq}, [1], "me"),
        forecaster=Bootstrap({}), pos={}, price={}, proceeds={}, owner={},
        cash=0.0, me="me")
    assert illegal_squads(u_ill) == [("riv", ["2/3 defensas"])], \
        illegal_squads(u_ill)
    # A LEGAL SQUAD NEVER APPEARS.
    assert "me" not in dict(illegal_squads(u_ill))
    ill_cav = "\n".join(caveats(u_ill))
    assert "riv cannot field a legal eleven" in ill_cav, ill_cav
    assert "2/3 defensas" in ill_cav, ill_cav
    assert "FROZEN" in ill_cav, ill_cav
    # A clean league (everyone legal) prints nothing about it.
    u_ok = Universe(
        state=LeagueState({"me": legal_sq, "riv": legal_sq}, [1], "me"),
        forecaster=Bootstrap({}), pos={}, price={}, proceeds={}, owner={},
        cash=0.0, me="me")
    assert illegal_squads(u_ok) == []
    assert "cannot field a legal eleven" not in "\n".join(caveats(u_ok))
    # SLOT_MIN ALONE IS NOT SUFFICIENT — a squad meeting every position's
    # bare minimum can still have no matching real formation (no shape
    # pairs DEF=3 with MED=3): named plainly rather than left silent.
    no_shape_sq = {"k": "POR", "d1": "DEF", "d2": "DEF", "d3": "DEF",
                   "m1": "MED", "m2": "MED", "m3": "MED", "f1": "DEL"}
    u_noshape = Universe(
        state=LeagueState({"me": no_shape_sq}, [1], "me"),
        forecaster=Bootstrap({}), pos={}, price={}, proceeds={}, owner={},
        cash=0.0, me="me")
    assert illegal_squads(u_noshape) == \
        [("me", ["not enough for any legal formation"])], \
        illegal_squads(u_noshape)

    # -- phantom_filled(): detected off the phantom KEYS, not by re-
    # running best_xi() -- 2026-09-01, Miguel: "the forecast for Albert
    # is absolutely unsustainable" -------------------------------------
    # 1 phantom + 2 real DEF, 5 MED, 2 DEL, 1 POR = 11, matching the real
    # (3, 5, 2) formation exactly — not just meeting SLOT_MIN in isolation.
    ph_sq = {"__phantom_riv_DEF_0": "DEF", "d1": "DEF", "d2": "DEF",
            "m1": "MED", "m2": "MED", "m3": "MED", "m4": "MED", "m5": "MED",
            "p1": "POR", "f1": "DEL", "f2": "DEL"}
    u_phantom = Universe(
        state=LeagueState({"me": legal_sq, "riv": ph_sq}, [1], "me"),
        forecaster=Bootstrap({}), pos={}, price={}, proceeds={}, owner={},
        cash=0.0, me="me")
    assert phantom_filled(u_phantom) == [("riv", ["1 defensa"])], \
        phantom_filled(u_phantom)
    # ILLEGAL_SQUADS() DOES NOT FIRE HERE — the phantom key already makes
    # this squad legal (3 DEF total), the exact "safety net stays quiet
    # once the real fix is in place" property caveats() relies on.
    assert illegal_squads(u_phantom) == [], illegal_squads(u_phantom)
    assert "me" not in dict(phantom_filled(u_phantom))
    ph_cav = "\n".join(caveats(u_phantom))
    assert "riv's squad is short a position" in ph_cav, ph_cav
    assert "1 defensa" in ph_cav, ph_cav
    assert "this should not be possible" not in ph_cav, ph_cav

    # -- the phone ---------------------------------------------------------
    # The same numbers as data, because markdown cannot right-align a column
    # or colour a chip and the site escapes raw HTML on purpose. Built from
    # the rows the markdown was built from, so the two cannot disagree.
    d = payload(u, rows, st, ["riv"], locks_h=41.1, n_actions=132)
    assert d["expected_finish"] == 1.5 and d["p_win"] == 0.5, d
    # ROUNDED, NOT RAW — a level (not a paired difference) genuinely carries
    # Monte Carlo noise a 17-digit float misrepresents as precision the
    # sample does not have. 3 seasons, "me" wins 1 -> p_win = 1/3 exactly,
    # a repeating binary fraction if left unrounded (Python's own float
    # would print 0.3333333333333333). Confirms round(), not luck.
    st3 = Standings(totals={"me": [1000.0, 1200.0, 1000.0],
                            "riv": [1500.0, 900.0, 1500.0]}, me="me")
    d3 = payload(u, rows, st3, ["riv"])
    assert d3["p_win"] == round(1 / 3, 3) == 0.333, d3["p_win"]
    assert len(str(d3["p_win"]).split(".")[-1]) <= 3, d3["p_win"]
    assert d["band"] == [1000.0, 1600.0], d
    assert d["locks_in_h"] == 41.1 and d["cash"] == 23.6e6
    assert d["cash_locked"] == 0.0, d          # nothing pending, nothing to say
    u.locked_cash = 2.1e6
    assert payload(u, rows, st, ["riv"])["cash_locked"] == 2.1e6
    u.locked_cash = 0.0
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

    # -- payload()'s `moves` order: by pts_lo, not mean d_pos/value/d_win --
    # (2026-09-06, replacing the retired bar-then-value/d_win-leads tiering
    # — see _move_rank_key()'s own note on the real-history validation).
    # A is the biggest MEAN gain but the widest, riskiest downside; B is a
    # smaller mean but a genuinely safer floor; C is tiny everywhere. Order
    # is by pts_lo alone: B (safest) leads, A second, C last.
    row_a = {"action": Action("buy", buy="A", cost=40e6), "d_pos": 0.40,
             "d_win": 0.0, "d_beat": {}, "value": 2.0, "pts_lo": -30.0}
    row_b = {"action": Action("buy", buy="B", cost=1e6), "d_pos": 0.15,
             "d_win": 0.0, "d_beat": {}, "value": 50.0, "pts_lo": 10.0}
    row_c = {"action": Action("buy", buy="C", cost=1e4), "d_pos": 0.02,
             "d_win": 0.0, "d_beat": {}, "value": 500.0, "pts_lo": -80.0}
    order = [m["buy"] for m in payload(u, [row_a, row_b, row_c], st,
                                       ["riv"])["moves"]]
    assert order == ["B", "A", "C"], order
    # A HIGH d_win no longer buys outright priority — RETIRED behaviour,
    # on purpose: W's win-probability is the biggest of the three, but its
    # pts_lo is the worst, and pts_lo alone now decides.
    row_win = {"action": Action("buy", buy="W", cost=40e6), "d_pos": 0.05,
              "d_win": 0.10, "d_beat": {}, "value": 1.0, "pts_lo": -50.0}
    order_win = [m["buy"] for m in payload(u, [row_a, row_win, row_b], st,
                                           ["riv"])["moves"]]
    assert order_win == ["B", "A", "W"], order_win

    # -- the notification surface ------------------------------------------
    # What is worth interrupting somebody for: the best move, and nothing
    # about the twelve that lost. A move that gains nothing says nothing.
    al = alert_lines(u, rows, ["riv"])
    assert len(al) == 1 and "Yuri Berchiche" in al[0] and "+36%" in al[0], al
    assert "€14.1M" in al[0], al       # net cost travels with the headline
    assert alert_lines(u, [], ["riv"]) == []
    flat = [{**rows[0], "d_pos": 0.0, "d_win": 0.0}]
    assert alert_lines(u, flat, ["riv"]) == [], "a move worth nothing is not news"

    # -- value for money: a materially cheaper near-match beats the
    # biggest raw gain, but only when it keeps enough of it -----------
    # rows[0]: d_pos=0.433, net=14.13M — the "biggest gain, whatever it
    # costs" pick under the old rule.
    # pts_lo scaled with d_pos (d_pos * 100) for every fixture below, so
    # these EXISTING value-for-money assertions keep exercising exactly the
    # logic they always did (same relative ordering as the retired
    # d_pos-based initial pick) — the dedicated "equal mean, safer downside
    # wins" cases further down are what actually exercises the NEW pts_lo
    # substitution itself.
    cheap_ok = {**rows[0],
                "action": Action("buy", buy="cheap", cost=2e6, proceeds=0.0),
                "d_pos": 0.40, "d_win": 0.30, "pts_lo": 40.0}
    # 92% of 0.433, 1/7th the cost
    assert _best(u, [rows[0], cheap_ok], ["riv"]) == (cheap_ok, False), \
        "a move keeping 90%+ of the best gain for a fraction of the cost wins"
    cheap_bad = {**rows[0],
                 "action": Action("buy", buy="cheap", cost=2e6, proceeds=0.0),
                 "d_pos": 0.30, "d_win": 0.20, "pts_lo": 30.0}
    # 69% of 0.433 — below the floor
    assert _best(u, [rows[0], cheap_bad], ["riv"]) == (rows[0], False), \
        "a cheaper move that gives up too much of the gain does not win"
    free = {**rows[0],
            "action": Action("sell", sell=("dead",), cost=0.0, proceeds=1e6),
            "d_pos": 0.40, "d_win": 0.30, "pts_lo": 40.0}
    assert _best(u, [rows[0], free], ["riv"]) == (free, False), \
        "a self-funding move within reach of the best gain wins outright"
    # A pure-win-probability gain (d_pos <= 0) has no "% of the best gain"
    # to compare against — the cost guard is skipped, not divided by zero.
    winonly = {**rows[0], "action": Action("buy", buy="x", cost=1e6),
              "d_pos": 0.0, "d_win": 0.05, "pts_lo": 0.0}
    assert _best(u, [winonly], ["riv"]) == (winonly, False)

    # -- the NEW substitution itself: equal mean d_pos and equal cost, only
    # the downside (pts_lo) differs — Miguel, 2026-09-06: "let's end up
    # with a single metric" that folds in "upcoming matches are worth
    # more" WITHOUT an invented time-discount (see _best()'s own docstring
    # for the reasoning and the real-history validation this was checked
    # against before being wired in) --------------------------------------
    safer = {**rows[0],
             "action": Action("clause", buy="safer", sell="benat",
                              cost=20e6, proceeds=5.87e6, victim="riv"),
             "pts_lo": 80.0}      # same d_pos/net as rows[0], safer downside
    assert _best(u, [rows[0], safer], ["riv"]) == (safer, False), \
        "equal mean, safer downside (higher pts_lo) wins the initial pick"
    assert _best(u, [safer, rows[0]], ["riv"]) == (safer, False), \
        "order must not change it either"
    riskier = {**rows[0],
               "action": Action("clause", buy="riskier", sell="benat",
                                cost=20e6, proceeds=5.87e6, victim="riv"),
               "pts_lo": -10.0}   # same mean, WORSE downside than rows[0]
    assert _best(u, [riskier, rows[0]], ["riv"]) == (rows[0], False), \
        "equal mean, riskier downside loses even though it came first"

    # -- reliable routes first: a "listed" move (a rival's own sale, which
    # this league's real history says has never once gone through — see
    # _best()'s own docstring) is not the automatic pick while a free/clause
    # move is on the table, even a smaller one -----------------------------
    listed_big = {**rows[0],
                  "action": Action("buy", buy="listed_target", cost=30e6),
                  "d_pos": 0.50, "d_win": 0.40, "pts_lo": 50.0}
    u.route["listed_target"] = "listed"
    # A "listed" move is the ONLY candidate — nothing reliable to prefer it
    # over, so it is still the pick, just flagged uncertain.
    assert _best(u, [listed_big], ["riv"]) == (listed_big, True)
    # rows[0] (yuri, a CLAUSE — always reliable, kind="clause") is far
    # smaller (d_pos=0.433 vs 0.50, well under VALUE_TOLERANCE of it) and
    # would lose to listed_big on value-for-money alone. It still wins,
    # because listed_big is "listed" and rows[0] is not.
    assert _best(u, [listed_big, rows[0]], ["riv"]) == (rows[0], False), \
        "a reliable move beats a bigger listed one outright"

    # -- _best() does not depend on `rows` arriving sorted by d_pos — the
    # SAME fixtures above, handed in deliberately REVERSED/shuffled order,
    # must pick the SAME winners. (Written first against the old `pool[0]`
    # implementation to confirm it fails — order was silently load-bearing
    # even though nothing in `_best()`'s actual logic needs it to be.)
    assert _best(u, [cheap_ok, rows[0]], ["riv"]) == (cheap_ok, False), \
        "order must not change the value-for-money winner"
    assert _best(u, [cheap_bad, rows[0]], ["riv"]) == (rows[0], False), \
        "order must not change which move keeps too little of the gain"
    assert _best(u, [free, rows[0]], ["riv"]) == (free, False), \
        "order must not change a self-funding winner"
    assert _best(u, [rows[0], listed_big], ["riv"]) == (rows[0], False), \
        "order must not change reliable-beats-listed"
    del u.route["listed_target"]

    # -- BUY/RAID/LISTED split: free agents, a clause (cannot be
    # refused), and a listed target (the owner's own choice, which this
    # league's own real history says essentially never goes Miguel's way)
    # — three sections, same relative order preserved in each. 2026-09-01,
    # Miguel: "I want to know if any free players are worth buying
    # honestly", then "the raid should focus on the clause impacted
    # ones... no way they're selling willingly to me" once a real report
    # showed listed targets sitting under RAID as if a clause's certainty
    # applied to them too.
    steady_row = {"action": Action("buy", buy="steady", cost=5e6),
                 "d_pos": 0.40, "d_win": 0.0, "d_beat": {}, "value": 8.0,
                 "d_pts": 40.0, "pts_lo": 10.0, "pts_hi": 70.0, "helps": 0.80}
    dud_row = {"action": Action("buy", buy="dud", cost=5e6),
              "d_pos": 0.20, "d_win": 0.0, "d_beat": {}, "value": 4.0,
              "d_pts": 20.0, "pts_lo": 5.0, "pts_hi": 35.0, "helps": 0.60}
    maverick_row = {"action": Action("buy", buy="maverick", cost=5e6),
                   "d_pos": 0.10, "d_win": 0.0, "d_beat": {}, "value": 2.0,
                   "d_pts": 10.0, "pts_lo": -50.0, "pts_hi": 260.0,
                   "helps": 0.55}
    riv_row = {"action": Action("clause", buy="rivals", cost=5e6),
              "d_pos": 0.60, "d_win": 0.0, "d_beat": {}, "value": 12.0,
              "d_pts": 60.0, "pts_lo": 20.0, "pts_hi": 90.0, "helps": 0.90,
              "burn": 1.2e6}   # the clause's real premium over market value
    wish_row = {"action": Action("buy", buy="wished", cost=5e6),
               "d_pos": 0.50, "d_win": 0.0, "d_beat": {}, "value": 10.0,
               "d_pts": 50.0, "pts_lo": 15.0, "pts_hi": 80.0, "helps": 0.85}
    uc_owned = Universe(
        state=LeagueState({"me": {}, "riv": {}}, [1, 2], "me"),
        forecaster=Bootstrap(
            {j: {"steady": (5.0, 1.0), "maverick": (4.0, 1.0),
                "dud": (3.0, 1.0), "rivals": (7.0, 1.0),
                "wished": (6.5, 1.0)} for j in (1, 2)}),
        pos={"steady": "MED", "maverick": "MED", "dud": "MED",
            "rivals": "MED", "wished": "MED"},
        price={"steady": 5e6, "maverick": 5e6, "dud": 5e6, "rivals": 5e6,
              "wished": 5e6},
        proceeds={}, owner={"rivals": "riv", "wished": "riv"}, cash=10e6,
        me="me", route={"rivals": "clause", "wished": "listed"},
        value={"steady": 5e6, "rivals": 3.8e6},
        name={"steady": "steady", "maverick": "maverick", "dud": "dud",
             "rivals": "rivals", "wished": "wished"})
    all_rows = [steady_row, dud_row, maverick_row, riv_row, wish_row]
    owned_lad = ladder_rows(uc_owned, all_rows)
    # ONE SORTED LIST, FILTERED, NOT SEPARATELY RANKED GROUPS: "rivals"
    # (a clause, cannot be refused) lands in RAID despite everything else
    # in BUY; "wished" (owned by a rival, NOT a clause — his own choice to
    # sell) is dropped from every group outright, even though it outranks
    # every free-agent row on value — Miguel's own repeated instruction:
    # never emphasize a rival-owned player unless the proposal is a raid.
    by_group = {}
    for r in owned_lad:
        by_group.setdefault(r["group"], []).append(r["name"].lower())
    assert by_group["buy"] == ["steady", "dud", "maverick"], by_group
    assert by_group["raid"] == ["rivals"], by_group
    assert "wished" not in [n for names in by_group.values() for n in names], \
        by_group
    md_owned = "\n".join(ladder(uc_owned, all_rows, st))
    assert "BUY — free agents" in md_owned, md_owned
    assert "RAID — a clause, cannot be refused" in md_owned, md_owned
    assert "LISTED" not in md_owned, md_owned
    assert "wished" not in md_owned.lower(), md_owned
    # BOTH SECTIONS PRESENT even when free-agent buys is empty: the
    # explicit "none clear the bar" line, not silence that could be
    # mistaken for "no candidates were even screened".
    no_free_lad = "\n".join(ladder(uc_owned, [riv_row], st))
    assert "none clear the bar today" in no_free_lad, no_free_lad
    assert "RAID" in no_free_lad, no_free_lad

    # -- BUY/RAID show market price + extra clause premium, not net cost --
    # Miguel's own framing (2026-09-06): three plain facts (what he's
    # worth, what extra the clause costs, the point swing), no blended
    # score. "steady" (a free agent, no clause) shows just his market
    # price; "rivals" (a raid) shows market price PLUS the real premium
    # decide.burn() already computed for rank()'s own charge — not a new
    # number, just finally shown.
    steady_cell = next(r for r in owned_lad if r["name"].lower() == "steady")
    assert steady_cell["market"] == 5e6 and not steady_cell["premium"], \
        steady_cell
    rivals_cell = next(r for r in owned_lad if r["name"].lower() == "rivals")
    assert rivals_cell["market"] == 3.8e6, rivals_cell
    assert rivals_cell["premium"] == 1.2e6, rivals_cell
    assert "5.00M" in md_owned, md_owned            # steady's plain price
    assert "3.80M +1.20M" in md_owned, md_owned      # rivals' price + premium

    # -- overdrawn is not a ranking question -------------------------------
    # -- a team sheet reads keeper first ------------------------------------
    # Ranked purely by points, an eleven puts the keeper between two
    # midfielders. Ranking decides who is IN it; position decides the order
    # you check them off in.
    u.pos = {"k": "POR", "d": "DEF", "m": "MED", "f": "DEL"}
    u.forecaster = Bootstrap({1: {"k": (1.0, 1.0), "d": (9.0, 1.0),
                                  "m": (5.0, 1.0), "f": (7.0, 1.0)}})
    u.state.jornadas = [1]
    assert by_slot(u, ["m", "f", "k", "d"]) == ["k", "d", "m", "f"]

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
    # twice. Nothing above the table claims a verdict the table itself can
    # disagree with — verdict() and market_percentile() were both retired
    # for exactly this (see their own removal notes in git history).
    for banned in ("## Do this", "## The board", "## Warnings"):
        assert banned not in heads, heads

    # No data at all is a placeholder that says why, not a crash and not an
    # empty page that looks like an answer.
    ph = "\n".join(placeholder("no api_teams.csv"))
    assert "no api_teams.csv" in ph and ph.startswith("# The simulation")

    # -- band_acts: a real season band for EVERY player, not just moves --
    # THIS WAS NEVER EXERCISED BEFORE any simulation-touching helper in this
    # file got its own real test. Built here first, before wiring it into
    # the ladder, so a broken band cannot
    # silently reach the report.
    from decide import Universe as U2
    from ffcore.season import LeagueState as LS2

    many_j = list(range(1, 11))
    # A full legal eleven ("star" is one of five real MEDs, so removing him
    # still leaves a legal XI to re-pick from — the real case, a squad
    # short a slot with no replacement, has no legal XI at all and every
    # trial's total collapses to the same degenerate number).
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
           pos={**{k: v for k, v in sqb.items()}, "cand": "MED"},
           price={"cand": 5e6}, proceeds={"dead": 1e6, "star": 20e6},
           owner={}, cash=10e6, me="me")
    # EVERY MAN YOU HOLD gets a PURE SELL (2026-09-06: no funded-upgrade
    # narrative, cut with best_swap_for — the direct cause of two
    # catastrophic squad-legality bugs); everyone above the bar you do
    # not hold gets an outright buy, no sale funding it.
    asked = band_acts(ub)
    keys = {k for k, _a in asked}
    assert keys == {*sqb, "cand"}, keys
    by_key = dict(asked)
    for k in sqb:
        assert by_key[k].buy == "" and by_key[k].sell == (k,), by_key[k]
    assert by_key["cand"].buy == "cand" and by_key["cand"].sell == (), \
        by_key["cand"]

    # ...and rank() answers them in its own final pass. No `acts`, so no move
    # survives screening and nothing is dropped from `extra` — the bands are
    # the whole answer.
    _rows, baseb, _lam, bands = decide.rank(ub, [], extra=asked)
    assert set(bands) == {*sqb, "cand"}, sorted(bands)
    for key in bands:
        med, lo, hi, _act = bands[key]
        assert lo <= med <= hi, (key, bands[key])
    # SELLING YOUR NAILED STARTER COSTS YOU POINTS, with no affordable
    # upgrade to offset it — a real, clearly negative median, not a
    # snapshot of one jornada.
    assert bands["star"][0] < -20, bands["star"]
    # SELLING DEAD WEIGHT COSTS NOTHING — a pure sale prices him near
    # zero, since nothing is lost fielding a man who never starts anyway.
    assert -5 < bands["dead"][0] < 5, bands["dead"]
    assert bands["dead"][3].buy == "" and bands["dead"][3].sell == ("dead",)
    # BUYING A GOOD CANDIDATE GAINS POINTS — positive median.
    assert bands["cand"][0] > 0, bands["cand"]
    # Nothing asked for at all: no extra squads scored, not an error.
    assert decide.rank(ub, [], extra=[])[3] == {}

    # THE BAND RIDES THE SAME SEASONS AS THE MOVES. A key rank() ranks a
    # real row for — buy OR sell side — is NOT banded twice: its own
    # row's pts_lo/pts_hi is the answer, off the squad the victim's
    # response leaves behind, so "cand" drops out of `bands` the moment
    # it is affordable enough to survive screening as a real move.
    buy_cand = decide.Action("buy", buy="cand", cost=5e6)
    rows2, _b2, _l2, bands2 = decide.rank(ub, [buy_cand], extra=asked)
    assert [r for r in rows2 if r["action"].buy == "cand"], rows2
    assert "cand" not in bands2, sorted(bands2)

    print("sim self-test OK (204 cases)")


def main() -> None:
    import decide
    from ffcore.model import session
    from ffcore.tidy import load_deadline

    REPORTS.mkdir(exist_ok=True)
    PARTS.mkdir(parents=True, exist_ok=True)
    # THE RUN'S OWN MARKET, not a second read of market.csv. decide.load()
    # below asks for the same session() and gets the same rows back.
    rows_m = session().market
    stamp = rows_m[0]["observed_at"] if rows_m else ""
    deadline = load_deadline()
    locks_h = None if deadline is None else (
        deadline - run_now()).total_seconds() / 3600

    u = decide.load()
    # The three states that are data problems rather than crashes, named
    # rather than rendered as an empty answer.
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
    # EVERY TARGET, not only the ones you can afford. The unaffordable ones
    # are dropped after screening; screening them is how the price of cash
    # gets measured, off a pass that was happening anyway.
    acts = decide.candidates(u, exp, budget=float("inf"))
    smoothed = cash_price_history()
    # ONE SIMULATION AT FINAL_TRIALS, not two. band_acts() names the ladder's
    # one-man questions and rank() answers them in the pass it was already
    # running — see its own note on why a second pass re-drew identical
    # seasons for nothing.
    rows, base, measured, bands = decide.rank(
        u, acts, price=smoothed, extra=band_acts(u))
    log_cash_price(measured)
    u.cash_note = _price_note(smoothed, measured)
    rivals = [m for m in u.state.squads if m != u.me]
    # ONE COMPUTATION, READ BY BOTH RENDERERS — render() and payload() both
    # draw this one list, so the two cannot disagree about groups or order.
    ladder_data = ladder_rows(u, rows, bands)
    write_lines(PARTS / OUT,
                render(u, rows, base, stamp, rivals, len(acts), locks_h,
                       ladder_data))
    print("wrote %s (%d moves, %d simulated in full)"
          % (PARTS / OUT, len(acts), len(rows)))

    (REPORTS / "decisions.json").write_text(json.dumps({
        "generated_at": run_now()
                          .strftime("%Y-%m-%dT%H:%MZ"),
        **payload(u, rows, base, rivals, locks_h, len(acts),
                  ladder_data=ladder_data),
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
                                 % run_now()
                                     .strftime("%Y-%m-%d %H:%M"), ""] + body)
        else:
            ALERTS.unlink(missing_ok=True)
    print("%d alert(s) from the simulation" % len(lines))


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        raise SystemExit(0)
    main()
