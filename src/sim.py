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

from decide import dead_weight, value_rate  # noqa: E402,F401
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
    # Cash carries the emphasis when it is negative, and nothing else does.
    # Over budget mid-window is allowed and over it at the lock is not, so a
    # minus sign here is the one fact that outranks the table — as a NUMBER,
    # not as a paragraph explaining itself.
    cash_txt = ("**cash %s**" if u.cash < 0 else "cash %s") % fmt_money(u.cash)
    # BOTH NUMBERS, NOT JUST THE ONE THAT LOOKS WRONG. `u.cash` is what you can
    # actually spend — the app's raw balance minus a bid of yours still
    # pending, held until it is accepted, rejected or withdrawn — and it is
    # the number every decision in this report is made against, so it stays
    # the headline. But a reader checking this against the app's own balance
    # screen sees a bigger, positive number and no explanation for the gap
    # (found 2026-08-29: balance read +3.30M, this line read -2.64M, and
    # nothing on the page said the 5.94M difference was three real pending
    # bids, not an error). Said here as the same two numbers the phone
    # already shows, not a new one to reconcile by hand.
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


def race(u, key: str) -> list[dict]:
    """`decide.contest()` for one target, as the shape BOTH renderers draw.

    ONE ANSWER, TWO RENDERINGS — the markdown's own cell (race_cell()) and
    the phone's `contest` field are two drawings of this list, never two
    computations of it. That is the same rule ladder()/ladder_rows() and
    _move_rank_key() were each pulled out to enforce, applied to the newest
    number on the board rather than learned again the day the two disagree.

    `[]` for everything that is not a payable clause, which is most rows —
    see decide.contest()'s own note on why a bid that can lose is a
    different question with a different signal (`Universe.bids`).
    """
    import decide
    return [{"manager": m, "days": d} for m, d in decide.contest(u, key)]


def race_cell(u, r: list[dict]) -> str:
    """race()'s own list in one short phrase, or "" for nothing to say.

    Only the SOONEST rival is named. The board is already a wide table on a
    390px phone and the second-soonest changes no decision — what a reader
    acts on is whether anybody is close, and who.
    """
    if not r:
        return ""
    who, days = r[0]["manager"], r[0]["days"]
    when = ("can pay today" if days <= 0
            else "can pay in ~%d day%s" % (days, "" if days == 1 else "s"))
    return "%s %s" % (who.split()[0], when)


def ladder_rows(u, rows, bands=None, base=None) -> list[dict]:
    """The grouped plan as data, so the phone draws the same one table.

    Same groups, same order, same numbers. Two renderers drawing different
    tables is how this report came to contradict itself; there is one shape
    and both read it.

    `bands`, when given, is decide.rank()'s own `bands` — a real season band
    (pts_lo/pts_hi) for EVERY row, not just the point estimate xpts already
    carried. Before this, only "buy" rows (already ranked as a move) had
    one; a squad member's own row showed a bare xPts/j snapshot with no
    uncertainty at all, which is a single jornada's P(start), not a season
    of them — see band_acts()'s own note on why that understates a bench
    player's real range. `bands=None` (an old caller, or the self-test) is
    the unpriced table, not a crash.

    `base`, when given, is the run's own baseline Standings — the ONE
    thing chase_keys() needs in order to know whether the account is
    trailing (see trailing()). Given one, 1-2 BUY rows may come back in a
    "chase" group instead; `base=None` is the table exactly as it was
    before trailing mode existed, which is what an old caller and the
    self-test want.
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
            lo=None, hi=None, contest=()):
        if k in bands:
            pts, lo, hi, action = bands[k]
            # A SWAP OF *HIM*, NOT A PURE SALE: the band answers "sell him,
            # buy the best his own money reaches" (best_swap_for()), so
            # the row says which player that was — the number alone does
            # not distinguish "worth keeping, no upgrade affordable" from
            # "worth keeping even against his best real alternative".
            # `action.buy != k` excludes a CANDIDATE's own band (his
            # Action buys HIM, funded by dead weight — "vs himself" would
            # be nonsense).
            #
            # NOT ON "in"/"out" — xi_change()'s own diff, a free lineup
            # change (bench <-> start a man ALREADY ON YOUR SQUAD), never a
            # transfer. This band still answers a real question about him
            # (is he worth more than his own money elsewhere), but "vs X"
            # reads as "sell him for X" on a row that costs nothing and
            # sells nobody — found 2026-09-01 in a swarm review.
            if (action.buy and action.sell and action.buy != k
                    and group not in ("in", "out")):
                note = "vs %s" % title_name(u.name.get(action.buy,
                                                        action.buy))
                # THE SAME TARGET CAN ALSO BE A REAL, ALREADY-RANKED BUY ROW
                # a few lines below, funded by somebody ELSE's sale —
                # best_swap_for() asks "what THIS man's own money reaches",
                # a different question from candidates()/rank()'s one
                # winning funder per target (see best_swap_for()'s own
                # docstring), so the two can legitimately disagree on who
                # pays. Left unsaid, a reader sees the same name twice at
                # two different prices with nothing connecting them — found
                # 2026-09-01 (swarm review): Ali Houary's row named Antonio
                # Blanco while the real Antonio Blanco BUY row was funded by
                # Moi Gomez. Named here, once, rather than left for the
                # reader to notice the coincidence and wonder which plan is
                # real (both are — they are just two different players'
                # money reaching the same man).
                funder = won.get(action.buy)
                if funder and funder["action"].sell != action.sell:
                    sellers = funder["action"].sell
                    if sellers:
                        note += (" — BUY row below reaches him via %s instead"
                                % ", ".join(title_name(u.name.get(s, s))
                                           for s in sellers))
        return {"name": title_name(u.name.get(k, k)),
                "pos": u.pos.get(k, ""), "start": u.start.get(k, 0.0),
                "xpts": exp.get(k, 0.0), "group": group, "where": where,
                "money": money, "pts": pts,
                "pts_lo": lo, "pts_hi": hi, "note": note, "value": value,
                # WHO ELSE COULD TAKE HIM, AND WHEN — see race(). `[]` on
                # every row that is not a payable clause, which is every
                # row you already own and every free/listed target.
                "contest": list(contest)}

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
    buy_floor = _moves_floor(rows)
    # THE CHASE GROUP IS A SEPARATE GROUP, NOT A RE-SORT OF THIS ONE — see
    # chase_keys(). Empty on any run where trailing() is silent, which
    # leaves the BUY group byte-identical to what it was. Emitted ahead of
    # BUY and in CEILING order (chase[k], 1 then 2) rather than in
    # _move_rank_key order, because that is the order the group is being
    # read in: widest band first is the whole point of it.
    chase = chase_keys(u, rows, base) if base is not None else {}

    def buy_cell(k, group):
        r = won[k]
        return cell(k, group, u.owner.get(k) or "free agent",
                    -r["action"].net, r["d_pts"], value=r.get("value"),
                    lo=r.get("pts_lo"), hi=r.get("pts_hi"),
                    contest=race(u, k))

    buys = [k for k in rest if k in won]
    for k in sorted((k for k in buys if k in chase), key=lambda k: chase[k]):
        out.append(buy_cell(k, "chase"))
    # FREE AGENTS, THEN A REAL RAID, THEN A WISH — SAME ORDER THROUGHOUT,
    # THREE HEADINGS, NOTHING DROPPED. Before this (Miguel, 2026-09-01:
    # "I want to know if any free players are worth buying honestly")
    # every BUY row sorted together — value_rate()'s own ranking has no
    # reason to prefer a free pickup over a clause raid that pays for its
    # premium in points, so a real report could (and did) show nothing
    # but rival-owned targets at the top, with a reader unable to tell
    # "no good free options today" from "free options exist but a bigger
    # raid outranked them" without reading every row.
    #
    # A RIVAL'S OWN PLAYER IS NOT ONE THING. A clause CANNOT be refused —
    # pay it and he is yours, the same certainty as a free pickup. A
    # LISTED target is the owner's own choice to sell, and this repo's
    # own measurement (decide.py's own note, re-derived and PROVEN
    # 2026-08-31 by replaying ownership forward over the real ledger, not
    # just read off its columns) is that in 119 real deals this league
    # has ever recorded, ZERO were manager-to-manager — not rare, the
    # entire sample. Grouping a listed target under "RAID" implied a
    # rival choosing to sell to Miguel is a live option the same way a
    # clause is; it is not (Miguel: "no way they're selling willingly to
    # me") — found 2026-09-01, the same day the two-way split shipped,
    # while checking it against a real report where three of the rows
    # under "RAID" turned out to be exactly this: listed, not clause.
    #
    # ONE SORTED LIST, FILTERED THREE TIMES, NOT THREE SEPARATELY RANKED
    # ONES — `non_chase` keeps _move_rank_key()'s own order throughout,
    # split only on ownership and (for owned targets) route, so no
    # section can disagree with another about relative order. Nothing is
    # dropped: a genuinely great clause raid, or even a listed one, stays
    # a real, visible option — just never mixed with a stronger claim
    # than it can actually make.
    non_chase = sorted((k for k in buys if k not in chase),
                       key=lambda k: _move_rank_key(won[k], buy_floor, u))
    for k in non_chase:
        if not u.owner.get(k):
            out.append(buy_cell(k, "buy"))
    for k in non_chase:
        if u.owner.get(k) and u.route.get(k, "market") == "clause":
            out.append(buy_cell(k, "raid"))
    for k in non_chase:
        if u.owner.get(k) and u.route.get(k, "market") != "clause":
            out.append(buy_cell(k, "listed"))
    for k in sorted((k for k in rest if k not in won
                     and u.price[k] > u.cash + spare),
                    key=lambda k: -exp.get(k, 0.0)):
        short_by = u.price[k] - u.cash - spare
        save_pts = bands[k][0] if k in bands else None
        # THE SAVE GROUP IS WHERE THE RACE MATTERS MOST, and it is the one
        # place this used to be silent: a clause target you are saving
        # toward is the exact case where "somebody else can pay it in two
        # days" turns a plan into a dead plan, and "nobody for a month"
        # turns a shortfall into a real option.
        out.append(cell(k, "save", u.owner.get(k) or "free agent",
                        -short_by, save_pts, "short",
                        value=value_rate(save_pts, short_by),
                        contest=race(u, k)))
    for k in sorted((k for k in rest if k not in won
                     and u.price[k] <= u.cash + spare),
                    key=lambda k: -exp.get(k, 0.0)):
        out.append(cell(k, "pass", u.owner.get(k) or "free agent",
                        -u.price[k], None))
    return out




def band_acts(u) -> list:
    """The one-man questions the ladder needs a season band for, as
    `[(key, Action), ...]`.

    For EVERY player the ladder shows, not just the ones already ranked as
    a move: a squad member's OWN real value — sell him, buy the best
    upgrade his own proceeds reach (decide.best_swap_for()), or a pure
    sale when nothing does — and a reachable candidate's (what buying him
    alone would gain).

    A PURE SALE UNDERSTATES A BENCH PLAYER: "what selling him costs, no
    replacement bought" is not the question a KEEP row is actually
    answering, which is "is he worth more than what his own money could
    buy instead" — see best_swap_for()'s own note on why this is a
    different question from candidates()'s swap search, and why reusing
    that search's output cannot answer it for every held player. Found
    2026-08-25: the pure-sale framing had every KEEP row read as a net
    cost with no context for why keeping was still right — true (selling
    for nothing is never good), but not the comparison a reader actually
    wants next to a KEEP chip.

    COMPUTES NOTHING — it names the questions and decide.rank() answers them
    in the final pass it was already running, handing them back as its
    `bands`. This used to be player_bands(), a SECOND simulation at
    FINAL_TRIALS against the same seed and the same seasons: the draw does
    not depend on the squad, so that pass re-drew about 1.2s of identical
    seasons to score squads the first pass could have scored for 0.03s each
    (measured 2026-08-24). Retired price_saves() before it (2026-08-22),
    which did the same for the SAVE group alone — see ladder()'s and
    ladder_rows()'s own notes on the duplication that was.

    Deliberately does NOT drop the players rank() ends up ranking as moves.
    Which moves survive screening is rank()'s own answer and is not known
    here; rank() makes that cut itself, where it is known.

    WHY THIS MATTERS MORE THAN IT LOOKS: dead_weight() decides who counts
    as sellable by checking best_xi() against forecaster.expected(j) for
    every remaining jornada — but expected() returns a FLAT p_start for
    every jornada (per_jornada[j][key][1] never varies by j), so looping
    over twenty jornadas re-checks the identical frozen number twenty
    times. The only place a jornada's DISTANCE actually widens anything is
    inside the stochastic trials these Actions are scored in
    (Bootstrap.start_draw, wired into rate_draw's own DRIFT_FRAC-style
    walk) — so a player who
    reads as safely dead weight on today's snapshot can still show a real,
    wide pts_hi here if the season has enough jornadas left for his rate
    to plausibly recover. The classification (dead_weight) stays the cheap
    heuristic gate; the BAND shown for him is the real answer.

    A pure sell (buy="") is a legal Action — apply() already handles
    a.buy == "" as a no-op purchase, only removing a.sell.
    """
    import decide

    exp, xi = decide.current_xi(u)
    mine = u.state.squads.get(u.me, {})
    bar = decide.xi_bar(exp, xi)
    dead = tuple(sorted(k for k, _ in decide_dead(u)))
    got = sum(v for _k, v in decide_dead(u))
    # THE SAME POOL ladder_rows() ranks — every man you hold, and everyone
    # you do not who beats the weakest man in your eleven. One derivation of
    # "who is on the ladder", read there for rendering and here for pricing.
    acts = [(k, decide.best_swap_for(u, k, exp)
            or decide.Action("sell", sell=(k,), proceeds=u.proceeds.get(k, 0.0)))
           for k in mine]
    acts += [(k, decide.Action("buy", buy=k, sell=dead,
                               cost=u.price.get(k, 0.0), proceeds=got))
            for k in u.price
            if k not in mine and exp.get(k, 0.0) > bar]
    return acts


def cover_rows(u, bands) -> list[dict]:
    """decide.offer_combos()'s own bands, pulled out of the SAME pass
    ladder_rows() reads, as data — the second and only other reader of
    it, keyed apart by the "OFFERS:" prefix decide.offer_combos() gives
    them so this never collides with a held player's own key.

    `[]` whenever there is nothing to cover (cash is not negative, or no
    combination was asked about) — the caller renders that as no section
    at all, not an empty one.

    Ranked cheapest-in-points first: with every combo already a MINIMAL
    cover (decide.offer_combos()'s own guarantee), the real choice left
    is which one costs the season the least, and that is a straight sort
    on the same paired figure every other row in this report is banded
    by — no separate metric invented for this one question.
    """
    deficit = -u.cash
    out = []
    for k, (pts, lo, hi, action) in (bands or {}).items():
        if not k.startswith("OFFERS:"):
            continue
        raised = action.proceeds
        out.append({
            "who": [title_name(u.name.get(p, p)) for p in action.sell],
            "raised": raised, "surplus": raised - deficit,
            "pts": pts, "pts_lo": lo, "pts_hi": hi,
            # Points lost per million RAISED, not spent — there is no
            # cost side to this move, only proceeds, so value_rate()'s
            # own "genuine positive cost" contract does not apply here.
            "rate": pts / (raised / 1e6) if raised else None,
        })
    out.sort(key=lambda r: -r["pts"])
    return out


def cover_md(u, data: list[dict]) -> list[str]:
    """`cover_rows()`'s own table, as markdown. `[]` when `data` is —
    see its own docstring for when that is.
    """
    if not data:
        return []
    out = ["_Balance is **%s** — accepting %s clears it. Every combination "
          "below is a real pending offer (or offers), never a market "
          "guess, and each alone raises enough — there is no case for "
          "taking more than one. Cheapest in season points first._"
          % (fmt_money(u.cash), fmt_money(-u.cash)), "",
          "| Accept | Raises | Season | pts/M€ |", "|---|--:|--:|--:|"]
    for r in data:
        season = ("—" if r["pts"] is None else
                  "%+.0f (%+.0f–%+.0f)" % (r["pts"], r["pts_lo"], r["pts_hi"]))
        rate = "%.1f" % r["rate"] if r["rate"] is not None else "—"
        out.append("| %s | %s (+%.2fM spare) | %s | %s |"
                   % (" + ".join(r["who"]), fmt_money(r["raised"]),
                      r["surplus"] / 1e6, season, rate))
    return out


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
    data = data if data is not None else ladder_rows(u, rows, base=base)
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
            # "vs X" — best_swap_for()'s own note, see ladder_rows()'s
            # cell(). "short" is the save branch's own word above and
            # never reaches this one.
            if r["note"]:
                season += " " + r["note"]
            money = ("%+.2fM" % (r["money"] / 1e6)) if r["money"] else "—"
        # THE RACE, IN THE `Where` COLUMN — beside the manager who holds
        # him, which is the only place it reads as one fact: "he is
        # BurtonGM89's, and SusoGattuso can pay his clause in 3 days." A
        # column of its own would be an eighth column on a phone for a cell
        # that is empty on most rows. `race_cell()` renders `contest`, it
        # does not recompute it — see race().
        where = r["where"]
        held = race_cell(u, r.get("contest") or [])
        if held:
            where += " · " + held
        return ("| %s | %s | %.0f%% | %.2f | %s | %s | %s | %s |"
                % (r["name"], r["pos"] or "—", 100 * r["start"], r["xpts"],
                   where, money, season,
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

    # ABOVE BUY, AND ONLY WHEN TRAILING — see trailing()/chase_keys(). The
    # heading names the manager the model says is winning, because that is
    # the whole justification for the section: a reader who is level or
    # ahead should never see it, and a reader who is behind should be told
    # why he is being shown a worse-on-average move.
    if by_group.get("chase"):
        t = trailing(u, base)
        out.append("| **CHASE — %s wins %.0f%% of the simulated seasons to "
                   "your %.0f%%, and you finish above him in only %.0f%% — "
                   "these are the widest bands on the board, worse on "
                   "average** | | | | | | | |"
                   % (t.get("leader", ""), 100 * t.get("leader_p_win", 0.0),
                      100 * t.get("p_win", 0.0), 100 * t.get("p_above", 0.0)))
        out += [row_md(r) for r in by_group["chase"]]

    # FREE AGENTS, ON THEIR OWN — see ladder_rows()'s own note on why this
    # split exists. A reader asking "is there anything worth buying that
    # isn't a raid" gets a direct answer here, not a search through a
    # mixed list. An explicit line when there is nothing here (rather than
    # silence) matters MORE once RAID exists as a second section below it
    # — silence used to mean only "no buy candidates at all"; now it could
    # also mean "candidates exist, all of them are rival-owned", and a
    # reader should be told which.
    if by_group.get("buy"):
        out.append("| **BUY — free agents** | | | | | | | |")
        out += [row_md(r) for r in by_group["buy"]]
    elif by_group.get("raid") or by_group.get("listed"):
        out.append("| **BUY — free agents — none clear the bar today** | | "
                   "| | | | | |")

    if by_group.get("raid"):
        out.append("| **RAID — a clause, cannot be refused** "
                   "| | | | | | | |")
        out += [row_md(r) for r in by_group["raid"]]

    # SEPARATE FROM RAID, ON PURPOSE — see ladder_rows()'s own note. A
    # listed target is the OWNER'S CHOICE, and 119 real deals in this
    # league have never once been that choice made in Miguel's favour —
    # shown here, not folded into RAID, so the heading itself carries the
    # honest odds rather than implying a clause's certainty.
    if by_group.get("listed"):
        out.append("| **LISTED — his own choice to sell, and in 119 real "
                   "deals this league has recorded, that has happened "
                   "zero times** | | | | | | | |")
        out += [row_md(r) for r in by_group["listed"]]

    if by_group.get("save"):
        out.append("| **SAVE — better than yours, out of reach** | | | | | | | |")
        out += [row_md(r) for r in by_group["save"]]

    if by_group.get("pass"):
        out.append("| **PASS** | | | | | | | |")
        out += [row_md(r) for r in by_group["pass"]]

    out += ["",
            "_Read it top to bottom: it is a plan, not a menu. The funding is "
            "implicit — sell the SELL rows and the BUY rows are what the money "
            "reaches. **Start** is one number, futbolfantasy recalibrated "
            "against confirmed line-ups and blended with analiticafantasy "
            "where it has an opinion, and it is the same figure the forecast "
            "multiplies by. **xPts/j** is what he scores a jornada with that "
            "already applied. **€** is the cash you END UP with for doing that row, funding included — a SELL row is what it raises, a BUY row is that money minus what he costs — and "
            "on a SAVE row it is how far short you are. **Season** is "
            "simulated: extra points over the %d jornadas left, measured in "
            "the same seasons with and without the move. **pts/M€** is "
            "Season points per million the move actually costs — not just "
            "whether it helps, but whether the price is worth it, so a big "
            "gain at a steep price and a small gain that is nearly free "
            "read against each other rather than only against themselves. "
            "`—` on a BUY row means the move is net cash-NEUTRAL-OR-"
            "POSITIVE (the funding sales raise at least as much as the "
            "buy costs) — there is no price to divide by, and Season "
            "already says whether it is worth doing. On a SAVE row it is "
            "the shortfall's own rate, which nobody can act on yet, only "
            "plan toward. **Where** names who holds him, and on a target "
            "whose buyout is payable today it also names the rival who "
            "could pay that same buyout soonest, and when — a clause "
            "cannot be refused by ME or by THEM, so a man at a payable "
            "clause is not an option you hold, he is the first solvent "
            "manager's. `can pay today` means the race is already on; a "
            "number of days is an ESTIMATE off their balance (`~`, "
            "reconstructed), the app's 100K daily allowance, and the "
            "rate that manager has actually raised money at this season — "
            "see the caveat below for what it does and does not assume. "
            "This is "
            "NOT the old λ (retired 2026-08-17): λ was measured against "
            "your OWN current eleven off a ladder of the whole unowned "
            "pool, so the same player was worth a different λ on "
            "different days for reasons that had nothing to do with him. "
            "This divides the SAME paired Season figure in the column "
            "beside it — the same simulated seasons, with the move and "
            "without it — so it only moves when the real trade-off does. "
            "READ IT BESIDE SEASON, NEVER ALONE: there are only eleven "
            "starting shirts, so a rate on its own cannot say whether a "
            "move earns one. Season already prices that in — it is the "
            "REAL simulated gain, picking the actual best eleven every "
            "jornada, so a player who cannot break in shows up there as a "
            "small or a zero, and a small Season figure at a tiny price "
            "can still carry a flattering rate despite being a marginal "
            "move. The rate says how CHEAPLY a gain arrived, not how BIG "
            "it is — check Season first._"
            % len(u.state.jornadas), ""]
    # ONLY WHEN THE SECTION IS THERE. A paragraph explaining a table that
    # is not on the page is the kind of standing prose this repo has
    # already had to delete once for reading as static.
    if by_group.get("chase"):
        out += ["_**CHASE** is the one place this report does NOT rank by "
                "value for money, and it appears only while the simulation "
                "says another manager is winning the league. A trailing "
                "manager's objective is not the most expected points per "
                "euro — it is P(win), and those stop being the same "
                "question the moment somebody is ahead of you: the move "
                "with the best average leaves you second more reliably. "
                "So these are the 1-2 candidates with the widest Season "
                "band — the biggest number on the RIGHT of the range — "
                "even though each is worse on average than the BUY rows "
                "under it. One or two and no more is the finding, not a "
                "setting: the returns to a high-variance pick diminish and "
                "then reverse past two, which is why the rest of the board "
                "is still ranked the ordinary way. Every number in the row "
                "is the same simulated number a BUY row carries; only the "
                "reason for showing it is different._", ""]
    return out


def decide_dead(u):
    from decide import dead_weight
    return dead_weight(u)


def real_cycle_bests(cycles: dict[str, set], gain, group_of=None
                     ) -> dict[str, list[float]]:
    """{group: [best gain() seen, one number per REAL cycle that offered a
    player of that group]} — from `cycles` (market_model()'s own {label:
    {player keys offered that cycle}}), not a resampled hypothetical.

    ONE NUMBER PER CYCLE, not one per player: a cycle offering three duds
    and one gem is a single real observation of "what a cycle can produce,"
    and counting all four would let a crowded cycle drown out a thin one
    that happened to offer exactly the right man.

    `group_of(key)`, when given, buckets each cycle's own best by group
    (position, route, whatever the caller wants graded separately) — a
    cycle with no entry for a group contributes NOTHING to that group's
    list, silence rather than a guessed zero, same rule this repo already
    applies to a jornada nobody has a row for. `group_of=None` puts
    everything in one bucket, keyed "".
    """
    out: dict[str, list[float]] = {}
    for keys in cycles.values():
        best_by_group: dict[str, float] = {}
        for k in keys:
            g = float(gain(k))
            grp = group_of(k) if group_of else ""
            if grp is None:
                continue
            if g > best_by_group.get(grp, float("-inf")):
                best_by_group[grp] = g
        for grp, best in best_by_group.items():
            out.setdefault(grp, []).append(best)
    return out


def market_percentile(routes, quiet=None) -> str:
    """Where this week's market sits against the market's own history.

    ONE LINE INSTEAD OF A PANEL. The panel compared three routes in a table of
    its own, outside the one table, and its prose was unconditional — it went
    on saying "spending now buys the worse of two options" on days when the
    headline said act today. What is actually worth knowing is whether what is
    on offer THIS week is good or bad by the standards of what gets dealt, and
    that is a percentile.
    """
    quiet = stale_feeds() if quiet is None else quiet
    # A QUIET FEED IS NOT A POOR MARKET, and this line is where the two get
    # confused. Gate api_market on freshness and nothing is buyable, so
    # now_best is 0, every simulated week beats it, and this printed "0th
    # percentile · a poor week" — a claim about the market with no market in
    # front of it. Say which it is.
    if "api_market" in quiet:
        return ("the app's market feed is **%s stale** — what is on offer now "
                "is unknown, not empty" % age_phrase(quiet["api_market"]))
    mkt = next((r for r in routes if r["route"] == "market"), None)
    if mkt is None or mkt.get("beats_now") is None:
        return ""
    beats = mkt["beats_now"]
    # AT THE EDGE OF WHAT THE SAMPLE CAN RESOLVE. beats_now is a count out
    # of `n_band` simulated weeks (ffcore.market.Offers.best_over(),
    # default trials=400) — if EVERY one of them beat today, or NONE did,
    # the true percentile could be anywhere inside the smallest gap that
    # many trials can tell apart, not exactly 0 or 100. Reporting a bare
    # "0th percentile · better in 100% of weeks" claims a precision the
    # sample does not have. Real case, 2026-08-21: today's actual
    # 33-player market topped out at a gain of 4.03; the sim draws from
    # the full ~600-player unowned pool (weighted toward value) and its
    # single BEST trial alone reached 7.11 — every one of 400 draws beat
    # today's number, which is a real, extreme, thin-listing day, not a
    # bug, but "0th percentile" overstated how precisely that is known.
    n = mkt.get("n_band", 400)
    if n and (beats <= 0.0 or beats >= 1.0):
        floor = max(1, round(100 / n))
        if beats >= 1.0:
            return ("market **under the %d%s percentile** · an unusually "
                    "poor week · better in over %d%% of weeks"
                    % (floor, _ord(floor), 100 - floor))
        return ("market **over the %d%s percentile** · an unusually "
                "good week · better in under %d%% of weeks"
                % (100 - floor, _ord(100 - floor), floor))
    pct = round(100 * (1 - beats))
    how = ("an unusually good week" if pct >= 75
           else "a poor week" if pct <= 25 else "an ordinary week")
    return ("market **%d%s percentile** · %s · better in %d%% of weeks"
            % (pct, _ord(pct), how, 100 - pct))


def _ord(n: int) -> str:
    return "th" if 11 <= n % 100 <= 13 else \
        {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")




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


def wait_routes(u, offers=None, rng=None, rows=None) -> list[dict]:
    """The three ways to get a better eleven, as data both renderers read.

    ACT NOW, WAIT FOR THE MARKET, OR WAIT FOR THE CLAUSES. Every move in the
    ranking is scored against doing nothing for thirty-eight jornadas, which
    is not the alternative on offer — so waiting scores zero there and
    anything positive beats it by construction. This is the correction, and it
    is computed ONCE: the markdown table and the phone drew different things
    twice before this was a function.

    `rows`, when given, is decide.rank()'s own scored candidates — the SAME
    ones the BUY table ranks. Before this (found 2026-09-01, swarm review),
    "Act today"'s own figure was ALWAYS `season(now_best)` — a cheap linear
    stand-in (today's best single-jornada margin times jornadas left, no
    re-picked XI, no simulation) — sharing its column header ("Season pts")
    with the BUY table's real simulated figure two sections above while
    being a genuinely cruder number under it: a live case read "Act today
    +244" off the same player the BUY table correctly priced, simulated,
    at +74. `rows` only ever holds moves already affordable today (rank()'s
    own screen: `cost <= cash + proceeds`) — exactly what "Act today"
    means — so the best of THEIR real `d_pts` (and its own season band) IS
    the number the BUY table's top row already carries, not a second
    estimate of it. `rows=None` (an old caller, or the self-test) keeps the
    estimate — there is nothing real to fall back to without it.
    """
    import random
    import statistics
    import decide

    now = run_now()
    exp, eleven = decide.current_xi(u)
    if not eleven:
        return []
    bar = decide.xi_bar(exp, eleven)
    mine = set(u.state.squads.get(u.me, {}))

    def approx_gain(k):
        # NOT ffcore.bid.gain() — that re-picks a whole best XI per
        # candidate via pick_xi(), real but too expensive to run per
        # candidate per Monte Carlo trial here. This is the cheap linear
        # stand-in this question has always used; named apart from the
        # real one so the two are never mistaken for each other.
        #
        # market_exp, not expected(): the simulation scores the 89 players who
        # could be in a squad, and this question is about the other five
        # hundred. One it was never given comes back 0.0, which is
        # indistinguishable from worthless — and that is what scored Lamine
        # Yamal at nothing.
        return max(0.0, u.market_exp.get(k, exp.get(k, 0.0)) - bar)

    left = len(u.state.jornadas)
    now_best = max((approx_gain(k) for k in u.price if k not in mine),
                  default=0.0)

    def season(rate, delay=0):
        """A per-jornada upgrade as points over the rest of the season.

        WITH THE DELAY PAID FOR. Waiting a week forgoes a jornada of the best
        thing you can buy today, and a comparison that ignores that is a
        comparison of rates dressed up as a comparison of outcomes. It is also
        the only way this is in the same unit as the move table, which was the
        whole problem: +3.69 against +110 is not a choice anybody can make.
        """
        return rate * max(0, left - delay) - now_best * delay

    # THE REAL FIGURE, WHEN THERE IS ONE — see this function's own note.
    best_row = max(rows, key=lambda r: r["d_pts"], default=None) if rows \
        else None
    out = [{"route": "act", "label": "Act today",
            "what": "%d players you can buy now" % len(u.price),
            "best": now_best,
            "pts": best_row["d_pts"] if best_row else season(now_best),
            "lo": best_row["pts_lo"] if best_row else None,
            "hi": best_row["pts_hi"] if best_row else None,
            "simulated": best_row is not None, "beats_now": None}]

    if offers is not None:
        band = offers.best_over(7, approx_gain, rng or random.Random(3))
        # BEATS_NOW GRADED AGAINST REAL SINGLE CYCLES, NOT THE RESAMPLED
        # BAND ABOVE. `band` (best_over) is a maximum over many independent
        # draws from the whole pool — it beats one real day's actual
        # listing almost by construction, which is why this line read
        # "under the 1st percentile" on 15 real days running regardless of
        # whether the market was actually weak that day. real_cycle_bests()
        # asks the fair question instead: how does today's real best
        # compare to the OTHER real cycles this repo has observed. Only
        # `real_cycles` (a getattr, same optional-capability pattern
        # forecaster.rate_draw/start_draw use) carries that; an `offers`
        # built without it (a caller not wired to market_model(), or an
        # old test fixture) falls back to `band` exactly as before, not a
        # crash.
        real = getattr(offers, "real_cycles", None)
        real_routes = getattr(offers, "real_routes", {})
        # A LISTED CYCLE IS NOT EVIDENCE OF A GOOD WEEK TO WAIT. This pooled
        # history used to count a rival's own contested listing exactly like
        # a real free-agent offer — decide.py's own finding (9b25510: 108
        # real transactions in this league, zero of them manager-to-manager)
        # is that a "listed" cycle is not a real opportunity at all, so a
        # week whose only good offer was three rivals listing their stars
        # (who will almost certainly never actually sell) scored as "a great
        # week to wait" off zero real opportunities. `group_of` returning
        # `None` for a listed player drops him from the cycle's own best —
        # the same silence-not-a-guessed-zero rule real_cycle_bests() already
        # applies to a cycle with nothing of a group in it — so a cycle whose
        # ONLY offer was listed contributes nothing to `hist` at all, exactly
        # as if that week had no market. `by_route`'s own "listed" bucket
        # below is untouched — grading a listed offer against listed history
        # is still the right question, just not the HEADLINE one.
        reliable_only = lambda k: None if real_routes.get(k) == "listed" else ""
        hist = (real_cycle_bests(real, approx_gain, reliable_only).get("", [])
               if real else None)
        # GRADED AGAINST THE SAME POOL THE HISTORY IS DRAWN FROM. `hist`
        # (and `band`, resampled from `offers.pool` — market_model()'s own
        # pool is already unowned-players-only) is real FREE-AGENT market
        # history, "listed" dropped above. `now_best` a few lines up is the
        # best of the WHOLE acquirable board — clause raids included, which
        # `api_market.csv` (what `hist`/`band` are fitted from) never
        # observes at all. Comparing that mixed "best of anything" against
        # a free-agent-only history meant a single big clause opportunity
        # today — nothing to do with whether the free/listed market is
        # actually good this week — could alone push "market Nth
        # percentile" to an extreme. Found 2026-08-31 (Miguel questioning a
        # "93rd percentile · unusually good week" reading that was really
        # just today's best clause target, Nahuel Tenaglia, being large).
        # Restricted to the same routes `hist` represents so the two sides
        # of the comparison are asking about the same market.
        market_now_best = max(
            (approx_gain(k) for k in u.price if k not in mine
             and u.route.get(k, "market") not in ("clause", "listed")),
            default=0.0)
        beats_now = (sum(1 for x in hist if x > market_now_best) / len(hist)
                    if hist else
                    sum(1 for x in band if x > market_now_best) / len(band))
        n_band = len(hist) if hist else len(band)

        def graded_by(group_of, real_group_of):
            """{group: {n, now_best, beats_now}} — TODAY'S OWN best per
            group (never the single best offer of ANY group compared
            against one group's history), graded against that same
            group's real cycle history. A group with no real cycles behind
            it, or nothing of it live today, is silently absent — the same
            rule real_cycle_bests() already applies one level up.
            """
            now_by_group: dict[str, float] = {}
            for k in u.price:
                if k in mine:
                    continue
                grp = group_of(k)
                if grp is None:
                    continue
                g = approx_gain(k)
                if g > now_by_group.get(grp, 0.0):
                    now_by_group[grp] = g
            hist_by_group = (real_cycle_bests(real, approx_gain, real_group_of)
                             if real else {})
            return {grp: {"n": len(vals), "now_best": now_by_group[grp],
                         "beats_now": sum(1 for x in vals
                                          if x > now_by_group[grp])
                                     / len(vals)}
                   for grp, vals in hist_by_group.items()
                   if vals and grp in now_by_group}

        # SAME FIX AS beats_now, ONE LEVEL UP: "best" (the median), "lo"
        # and "hi" used to read straight off best_over()'s resampled band
        # too — the exact reason a live check the day this was fixed found
        # its median at 7.37 against real history's own 4.15, with the
        # band's MINIMUM (4.33) already above the real MEDIAN. A player
        # who has never actually been offered (this repo's own "Not for
        # sale" table already knows who) still gets drawn into that
        # resample by value alone, and skews every number built from it.
        # Real history first when there is enough of it to say anything;
        # the resampled band remains the fallback, unchanged, for an
        # `offers` with no real_cycles attached.
        pool_stats = sorted(hist) if hist else sorted(band)
        best = statistics.median(pool_stats)
        lo = pool_stats[int(0.1 * len(pool_stats))]
        hi = pool_stats[int(0.9 * len(pool_stats))]
        out.append({
            "route": "market", "label": "Wait for the market",
            "what": "a week of new offers",
            "best": best,
            "pts": season(best, delay=1),
            "lo": lo,
            "hi": hi,
            "beats_now": beats_now,
            "n_band": n_band,
            "by_position": graded_by(lambda k: u.pos.get(k),
                                    lambda k: u.pos.get(k)),
            # FREE AGENT vs A RIVAL'S OWN LISTED PLAYER — Step 1's split,
            # graded here rather than blended: "how good is today's real
            # free-pickup market" and "how good is today's contested-bid
            # market" are different questions with different risk, the
            # exact thing conflating them under one "market" label used to
            # hide (see decide.Universe.route's own docstring).
            "by_route": graded_by(lambda k: u.route.get(k),
                                 lambda k: real_routes.get(k)),
            "helpful": sum(1 for k in offers.pool if approx_gain(k) > 0),
            "pool": len(offers.pool), "note": offers.note()})

    # NOT FOR SALE IS NOT THE SAME AS NOT WORTH HAVING. The best players in
    # the free pool are simply not on offer, and you cannot ask for one — so
    # the report names them with how long the app would take to deal them,
    # rather than leaving "114 would improve your eleven" to read as a
    # shopping list. That misreading cost a sale: Ruben Garcia was described
    # as buyable back when he was merely unowned.
    if offers is not None:
        watch = sorted(((approx_gain(k), k) for k in offers.pool
                        if approx_gain(k) > 0 and k not in u.price), reverse=True)
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
            "best": max((approx_gain(k) for k in shut), default=0.0),
            "pts": season(max((approx_gain(k) for k in shut), default=0.0), delay=1),
            "lo": None, "hi": None, "beats_now": None,
            "helpful": sum(1 for k in shut if approx_gain(k) > 0),
            "days": max(0.0, (opens - now).total_seconds() / 86400.0),
            "opens": opens.strftime("%d %b")})
    return out


def waiting(u, offers=None, rng=None, routes=None) -> list[str]:
    """The three routes, priced against each other.

    `routes`, when given, is a caller's own `wait_routes()` result — the
    SAME list `market_percentile()` already read, computed once (see
    render()'s own note on why this used to run wait_routes() twice per
    report, once here and once for the percentile line above it).
    `routes=None` (an old caller, or the self-test) computes it fresh.
    """
    routes = routes if routes is not None else wait_routes(u, offers, rng)
    if len(routes) < 2:
        return []
    mkt = next((r for r in routes if r["route"] == "market"), None)
    cl = next((r for r in routes if r["route"] == "clauses"), None)

    # SEASON PTS, MARKED WHERE IT ISN'T REAL. "Act today" carries a REAL
    # `d_pts` when `wait_routes()` was given `rows` — the same paired
    # Monte Carlo the move table above runs, off the same affordable
    # candidates, not a second estimate of them. "Wait for the market"/
    # "the clauses" cannot be: those players are not a concrete, known
    # offer yet, only a rate this repo has measured real history for, so
    # they stay `season()`'s flat `rate * jornadas_left` extrapolation —
    # marked `~` so the two kinds of number are never read as the same
    # kind. Before this (found 2026-09-01, swarm review) EVERY row here
    # used the estimate, "Act today" included, sharing its header with the
    # move table's real "Season" column while overstating it — a live
    # case read "Act today +244" off the same player the move table two
    # sections below priced, correctly, at +74.
    out = ["_`~` marks an estimate — a rate times jornadas left, not a "
          "simulation — for a route whose players are not a known, "
          "concrete offer yet. \"Act today\" is real when it can be: the "
          "same simulated best gain the move table above shows._", "",
          "| Route | What it offers | Season pts | Beats acting today |",
           "|---|---|--:|--:|"]
    for r in routes:
        if r["route"] == "watch":
            continue
        name = ("**%s**" % r["label"] if r["route"] == "act" else r["label"])
        beats = r.get("beats_now")
        real = bool(r.get("simulated"))
        pts_txt = ("%+.0f (%+.0f–%+.0f)" % (r["pts"], r["lo"], r["hi"])
                  if real and r.get("lo") is not None
                  else ("%+.0f" % r.get("pts", 0.0) if real
                        else "~%+.0f" % r.get("pts", 0.0)))
        out.append("| %s | %s | %s | %s |"
                   % (name, r["what"], pts_txt,
                      "—" if beats is None else "%.0f%%" % (100 * beats)))
    out.append("")

    facts = []
    if mkt:
        facts += [
            "| Unowned players who would improve your eleven | %d of %d |"
            % (mkt["helpful"], mkt["pool"]),
            "| Tenth percentile of a week's waiting | %+.2f |" % mkt["lo"],
            "| Market model | %s |" % mkt["note"]]
    if cl:
        facts += [
            "| Locked players who would improve your eleven | %d |"
            % cl["helpful"],
            "| Their clauses open | %s, in about %.0f days"
            % (cl["opens"], cl["days"]) + " |"]
    if facts:
        out += ["| The workings | |", "|---|--:|"] + facts + [""]

    # UNOWNED IS NOT AVAILABLE. The app deals about a dozen players a cycle
    # out of five hundred and you cannot ask for one, so a man who is merely
    # unowned is not a man you can go and buy. Leaving that implicit cost a
    # sale once; the wait column is what says it.
    wat = next((r for r in routes if r["route"] == "watch"), None)
    if wat and wat.get("players"):
        out += ["| Nobody is offering | Would add | Likely wait |",
                "|---|--:|--:|"]
        for pl in wat["players"]:
            out.append("| %s | %+.2f | %s |"
                       % (pl["name"], pl["gain"],
                          "%.0f days" % pl["wait"] if pl["wait"]
                          else "essentially never"))
        out.append("")
    return out + [""]


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


def _tempo_note(u) -> str:
    """The four rivals' own measured money-raising rates, in one phrase.

    READ OFF THE DATA, NOT REMEMBERED — the same rule every other line in
    caveats() follows. The rates are what makes the "days" figure above a
    per-rival number rather than one league-wide constant, so the reader
    gets to see the spread between them and judge the estimate himself; a
    caveat that says "measured per rival" without showing the measurement
    is asking to be believed.
    """
    have = [(m, u.tempo.get(m, {})) for m in sorted(u.state.squads)
            if m != u.me and u.tempo.get(m)]
    if not have:
        return ("nothing is on the ledger yet to measure that rate from, so "
                "it is the allowance alone")
    bits = ["%s %.1fM/day off %d sale%s"
            % (m.split()[0], t.get("sell_rate", 0.0) / 1e6,
               t.get("sells", 0), "" if t.get("sells") == 1 else "s")
            for m, t in have]
    return ("measured over the ledger's own %.0f days: %s. They differ by "
            "an order of magnitude, which is the whole reason this is per "
            "rival and not one number"
            % (have[0][1].get("days", 0.0), ", ".join(bits)))


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
        "| \"X can pay in ~N days\" is an estimate, and says what it "
        "assumes | it is their reconstructed balance (`~`: the app states "
        "`teamMoney` for your account alone, so a rival's can be a whole "
        "unseen sale wrong), plus the %s daily allowance, plus the rate "
        "that manager has ACTUALLY raised money at across the ledger — "
        "%s. Capped at what his squad is worth, since nobody can sell "
        "more than he holds. What it does NOT model is whether he WANTS "
        "the player, only whether he could pay: read it as how long the "
        "door stays open, never as a prediction that he walks through it. "
        "An allowance-only version was tried first and rejected as "
        "unactionable — it put the manager who raised 86.9M in six sales "
        "last week 450 days away from affording anything |"
        % (fmt_money(u.daily_bonus), _tempo_note(u)),
        # SEASON/VALUE PRICE THE MOVE AS IF YOU WIN THE RACE, DELIBERATELY —
        # not a gap left unpriced, a genuine "N-player preemption game"
        # (real-options economics: several agents racing an indivisible,
        # irreversible payoff — patent races, first-mover investment races
        # are the standard examples). That literature's own answer to "how
        # much does losing cost" is the reason this is not folded into a
        # fabricated win-probability discount: THE LOSER'S CAPITAL IS NOT
        # DESTROYED, only the option on this one target is — the cash still
        # buys whatever is next-best. Losing this race does not cost the
        # gap between Season and zero, it costs the difference between this
        # row and the NEXT row on this same table, which is already ranked,
        # already priced, and does not need a guessed rate to read. (Found
        # 2026-09-01, swarm review; the literature check is 2026-09-01 too
        # — Miguel asked whether anyone else modelling this kind of race
        # had already worked out how to price it. The equilibrium result in
        # that literature is also worth knowing: a genuinely CONTESTED race
        # trends toward roughly even odds at the point either side actually
        # pulls the trigger — a reason to read "can pay today" as real
        # 50/50-territory risk, not sharpenable further without bidding
        # data this repo does not have.)
        "| A clause race's Season/pts-per-M€ price the move as if you win "
        "it | losing does not cost you that figure — it costs you the NEXT "
        "row on this table instead, because the cash is not lost, only "
        "this one target is. \"Can pay today\" in the Where column is real "
        "contested-race risk, roughly even odds at the moment it's worth "
        "racing at all — there is no sharper number to give it without "
        "real bidding data |",
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


# How much of the best available move's season gain a materially cheaper
# alternative may give up and still be the one recommended. Real judgment,
# not measured, but not arbitrary either: the cash spent this week does not
# come back this season (rank()'s own net-cost accounting), so a move that
# keeps 90%+ of the best gain for meaningfully less money leaves next week's
# options open in a way the last 10% does not buy back. Not 1.0 (that is
# today's old behaviour, biggest gain wins outright regardless of cost) and
# not much lower (a move worth noticeably less of the season is a worse
# move, full stop, whatever it costs).
VALUE_TOLERANCE = 0.90

# THE FLOOR HALF OF "BAR, THEN RATIO" — VALUE_TOLERANCE above answers a
# different question (how much of the SINGLE BEST move's gain a cheaper
# alternative may give up and still BE the recommendation). This answers a
# looser question for the whole table, not one headline pick: what counts
# as worth ranking by efficiency at all. Without a floor, pure
# points-per-euro sorting hits the fractional-knapsack trap — a near-zero
# gain at an even smaller cost divides out to an enormous ratio and would
# win a table nobody would act on. Judgment call, not measured; tune
# against real reports if it buries a real move or promotes a trivial one.
MOVES_VALUE_FLOOR = 0.25


def _moves_floor(rows) -> float:
    """MOVES_VALUE_FLOOR's own cutoff for THIS batch of rows.

    Shared by payload()'s `moves` resort and ladder_rows()'s BUY group, so
    the phone's JSON and the markdown table rank the same candidates in
    the same order — see _move_rank_key()'s own note on why they used to
    not.
    """
    return MOVES_VALUE_FLOOR * max((r["d_pos"] for r in rows), default=0.0)


def _move_rank_key(r, floor, u):
    """BAR ON GAIN, THEN RANK BY VALUE — see MOVES_VALUE_FLOOR's own note.
    A d_win-driven move still leads outright (win-probability is a
    different axis than points-per-euro, no principled ratio between
    them). Among d_pos-driven moves, anything clearing `floor` is ranked
    by `value` (points per euro); below the floor, pushed to the bottom in
    raw-gain order, never hidden.

    SHARED KEY, not two independent sorts: before this, payload()'s
    `moves` ranked by value-for-money (shipped 24d2a8b, 2026-08-29) but
    ladder_rows()'s BUY group — the markdown table a reader actually
    scrolls top to bottom — still sorted by raw `exp` (projected points
    per jornada, price-blind), so a genuine bargain (high pts/M€) could
    sit near the bottom while a big, poor-value, even NEGATIVE-season-
    impact name led the list. The two renderings disagreeing on order is
    exactly the duplication ladder()'s own docstring warns about.

    RELIABLE ROUTES FIRST, WITHIN EVERY TIER — the same rule _best() has
    always used for the single headline pick (decide.py's own note, 108
    real transactions checked 2026-08-29, zero of them manager-to-manager):
    a "listed" candidate (Universe.route — a rival's own sale, who can
    simply not sell, or get outbid) is not a slightly-riskier version of a
    free-agent or clause buy, it is a route that has never once actually
    gone through in this league. `_best()` already refuses to push one as
    THE move unless nothing reliable clears the bar at all, but that
    demotion never reached the ordinary list — a listed target competed on
    equal footing with a guaranteed one everywhere the reliable-vs-listed
    split was not the single headline. Demoted here, not removed: a real
    listed opportunity — the rare one that might actually be worth a bid —
    stays fully visible, just under the routes that cannot be refused.
    """
    reliable = 0 if u.route.get(r["action"].buy, "free") != "listed" else 1
    # D_WIN ALONE USED TO WIN TIER 0 OUTRIGHT, UNCHARGED. `d_win` (the
    # season's win-probability swing) is computed straight off the
    # simulated squads, before rank()'s own lam*burn() premium charge —
    # the same charge `d_pos` already carries (`d_pos = gross - charge`,
    # see rank()'s own note). A clause costing well over its market value
    # could still lead the whole table on win-probability alone even when
    # PAYING that premium left `d_pos` negative — the model's own honest
    # answer that the move is a net loss in expected finish once the
    # premium is paid, silently overruled by an axis that never saw the
    # premium at all. Found 2026-08-31 (Miguel: "why would having a
    # clause, which means you pay above market rates, be a good thing?").
    # Requiring `d_pos > 0` too does not invent a win-probability-to-money
    # exchange rate (this repo's own principled objection to charging
    # `d_win` directly, still correct) — it only refuses to let a move
    # lead purely on `d_win` once its OWN priced d_pos says the premium
    # already lost more than the pitch gained. `trailing()`/`chase_keys()`
    # is the one place this repo already accepts an average-case loss for
    # a title shot — deliberately, gated on actually trailing, and labelled
    # "worse on average" — not the ordinary tier every ranked buy passes
    # through unlabelled.
    if r["d_win"] > 0 and r["d_pos"] > 0:
        return (0, reliable, -r["d_win"], -r["d_pos"])
    if r["d_pos"] >= floor and r["d_pos"] > 0:
        value = r.get("value")
        return (1, reliable, -value if value is not None else float("-inf"),
                -r["d_pos"])
    return (2, reliable, 0.0, -r["d_pos"])


# HOW MANY HIGH-CEILING PICKS A TRAILING MANAGER IS SHOWN. Not a tuning
# knob and not this repo's own measurement — it is the cited finding it
# implements. Frontier Economics' fantasy-football analysis of exactly this
# situation (a team trailing the leader, small league, winner-take-most)
# found P(win) is maximised by fielding ONE OR TWO high-variance,
# high-ceiling "maverick" picks, with the returns diminishing and then
# REVERSING past two — the same shape as a short stack seeking variance in
# tournament poker (gambler's ruin / ICM), where "expected points" is not
# the objective the payout structure actually rewards. Two is therefore a
# ceiling, not a target: chase_keys() returns fewer whenever fewer qualify,
# and nothing here manufactures a second pick to fill the quota.
#
# The reason a number is needed at all, rather than "rank everything by
# ceiling": variance is not free. Every chase pick is bought with expected
# points, so a table sorted by ceiling would be a strictly worse table for
# a manager who is level or ahead — and past two picks it is a worse table
# even for one who is behind. That is why the whole mode is gated on
# trailing() rather than being a column beside `value`.
CHASE_PICKS = 2


def trailing(u, base) -> dict:
    """Is the model saying somebody else wins this league? `{}` if not.

    `{"leader", "p_win", "leader_p_win", "p_above"}` when it is.

    THE TRIGGER, AND WHY IT IS THIS ONE. Both halves must hold:

      1. Some rival's P(win) is higher than mine — the model's own most
         likely champion is not me.
      2. I finish above THAT manager in fewer than half the simulated
         seasons (`Standings.beat`, the report's own "P(I finish above)"
         column).

    NO NEW THRESHOLD, and deliberately so: every number here is one the
    simulation already computes and the report already prints, and the two
    comparison points are 0.5 and "more than mine" — the definitions of
    "more likely behind than ahead" and "not the favourite", not levels
    anybody picked. Contrast the alternatives considered and rejected:
    `p_win < 1/N` (an at-parity share) reads a five-manager league as
    at-parity at 20% even when one manager is on 60% and the rest split
    the remainder; `expected_finish > (N+1)/2` has the same problem one
    statistic further out; and SQUAD VALUE rank — the thing that actually
    caused this situation — is an INPUT to the model, not its verdict, so
    triggering on it would make the report act on a number it does not
    itself believe is decisive.

    BOTH HALVES rather than either. A rival can hold the highest P(win)
    while STILL losing to me head to head, when a third manager takes the
    seasons I win. The gambler's-ruin argument is about the man ahead of
    ME, so a "leader" I beat more often than not is not one I need to take
    risk against — and the self-test carries that exact three-manager
    shape, because it is the case one half alone gets wrong.

    ON THE BOUNDARY, both readings are Monte Carlo estimates and will
    flicker between runs at 50/50. No margin is added for it: 50/50 IS the
    at-parity case, both answers describe it honestly, and the mode is
    purely ADDITIVE (it labels 1-2 extra rows and hides nothing), so a
    flicker there costs a reader nothing. A threshold that removed rows
    would need one.

    It is a STATE, not a move: nothing here is charged for, ranked, or
    subtracted from a gain. It only decides whether chase_keys() below is
    allowed to speak at all, and when it is silent the report is exactly
    the report it was before this existed.
    """
    rivals = [m for m in u.state.squads if m != u.me]
    if not rivals:
        return {}
    mine = base.position().get(1, 0.0)
    leader = max(rivals, key=lambda m: base.position(m).get(1, 0.0))
    theirs = base.position(leader).get(1, 0.0)
    above = base.beat(leader)
    if theirs <= mine or above >= 0.5:
        return {}
    return {"leader": leader, "p_win": mine, "leader_p_win": theirs,
            "p_above": above}


def chase_keys(u, rows, base) -> dict[str, int]:
    """`{buy key: 1 or 2}` — the trailing-mode high-ceiling picks, widest
    band first. `{}` whenever trailing() is silent, which is most days.

    THE CEILING IS ALREADY SIMULATED, so this runs no simulation of its
    own. `rank()` gives every row a full paired band — `pts_lo`/`pts_hi`,
    the 10th and 90th percentiles of the season-points difference over the
    SAME simulated seasons with the move and without it (decide.band()) —
    so "what is this move's upside" is a lookup. That is the same rule the
    ladder's own bands were fixed to obey (see ladder()'s docstring): one
    computation, read by every renderer that wants it, never a second pass
    recomputing a number the first pass already has.

    WHAT COUNTS AS A CHASE PICK, in one line: a candidate whose ceiling
    beats the ceiling of the move the ordinary ranking ALREADY puts first.

    That definition does the work three separate guards would otherwise
    have to. It is inherently relative, so there is no absolute variance
    threshold to tune — the bar moves with the board. It cannot return the
    recommendation you are being given anyway (`ranked[0]` is excluded by
    construction), because relabelling the top of the list as a gamble
    would be noise rather than a second option. And it silently excludes
    the squad-breaking rows that sit at the bottom of a real report — a
    move that guts the shape has a CEILING of several hundred points
    NEGATIVE, nowhere near the leader's — so no separate "never propose
    something catastrophic" rule is needed on top of it.

    RELIABLE ROUTES ONLY, with no fallback — unlike _best(), which must
    name something and so falls back to a "listed" move on a day nothing
    reliable clears the bar. 9b25510's own evidence: 108 real transactions
    in this league, zero of them manager-to-manager, so a seller who can
    simply refuse is not a variance play, it is a wish. This signal is
    ADDITIVE, so staying silent costs the reader nothing — which is
    exactly the licence _best() does not have.

    NOT A RE-SORT of the ranking it rides on. The value-for-money order
    (b499df7) is untouched and still correct: for a manager who is level
    or ahead it is the whole answer, and for one who is behind it is still
    what to do with the rest of the money. This only labels 1-2 rows the
    reader would otherwise have no reason to look twice at.
    """
    if not trailing(u, base):
        return {}
    floor = _moves_floor(rows)
    ranked = sorted(rows, key=lambda r: _move_rank_key(r, floor, u))
    if not ranked or ranked[0].get("pts_hi") is None:
        return {}
    ceiling = ranked[0]["pts_hi"]
    wide = sorted((r for r in ranked[1:]
                   if r["action"].buy
                   and u.route.get(r["action"].buy, "free") != "listed"
                   and r.get("pts_hi") is not None
                   and r["pts_hi"] > ceiling),
                  key=lambda r: -r["pts_hi"])
    return {r["action"].buy: i + 1
            for i, r in enumerate(wide[:CHASE_PICKS])}


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
    """
    candidates = [r for r in rows if r["d_pos"] > 0 or r["d_win"] > 0]
    if not candidates:
        return None, False
    reliable = [r for r in candidates
               if u.route.get(r["action"].buy, "free") != "listed"]
    pool, uncertain = (reliable, False) if reliable else (candidates, True)
    best = max(pool, key=lambda r: r["d_pos"])
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
    """
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
            offers=None, ladder_data=None, cover_data=None) -> dict:
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
    # ONE wait_routes() CALL, READ THREE TIMES — this used to call it fresh
    # for "wait", again for "verdict" (now retired, see below), and again
    # for "market_pct", resampling the same real market history three times
    # over for facts that cannot differ between them. Found 2026-09-01,
    # alongside the same duplication in render() (one direct call, plus a
    # second inside waiting(), also fixed) — five resamples per report,
    # total, for one fact.
    wait = wait_routes(u, offers, rows=rows)
    moves = []
    # BAR ON GAIN, THEN RANK BY VALUE — see MOVES_VALUE_FLOOR's own note.
    # A d_win-driven move still leads outright (unchanged: win-probability
    # is a different axis than points-per-euro, no principled ratio between
    # them). Among d_pos-driven moves, anything clearing the floor is
    # ranked by `value` (points per euro, already computed by rank() and
    # None-guarded by value_rate() — no re-derivation of that guard here);
    # a move with no ratio (a self-funding sale, net <= 0) that clears the
    # floor wins outright, same "free beats a rate" logic _best() already
    # uses. Below the floor: pushed to the bottom, in raw-gain order, never
    # hidden — just not competing on efficiency terms too small a gain
    # cannot make meaningful.
    floor = _moves_floor(rows)
    # A FLAG ON THE EXISTING ROWS, NOT A REORDER OF THEM — see chase_keys().
    # The value-for-money order below is unchanged whether or not the mode
    # fires; a chase pick is marked where it already sits. The markdown
    # ladder promotes the same picks into their own section (ladder_rows()'s
    # "chase" group, carried in `ladder` below), so both renderers read the
    # one chase_keys() answer rather than each deciding for itself.
    chase = chase_keys(u, rows, base)

    for r in sorted(rows, key=lambda r: _move_rank_key(r, floor, u)):
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
            # 1 or 2 on a trailing-mode high-ceiling pick, None otherwise
            # (which is every row on a run where trailing() is silent) —
            # the phone's own half of the markdown's CHASE section, so the
            # board can mark the row instead of the reader having to spot a
            # wide band by eye. See chase_keys().
            "chase": chase.get(a.buy) if a.buy else None,
            # WHO ELSE COULD TAKE HIM, AND WHEN — `[{"manager", "days"}]`,
            # soonest first, `[]` on everything that is not a payable
            # clause. Same race() the markdown ladder's `Where` cell draws,
            # so the phone and the appendix cannot disagree about who is
            # racing me for a man; the phone gets the whole list rather
            # than the markdown's one-name summary because it has the room
            # to draw more of it. See decide.contest().
            "contest": race(u, a.buy) if a.buy else [],
            # What you are on afterwards, and what he does about it. Both are
            # in the markdown table; the phone could not draw them because
            # they were never in the payload, which is the two renderers
            # drifting apart again.
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
        # WHAT `cash` ALREADY HAS SUBTRACTED — a bid of yours still pending,
        # summed (decide.pending_sent). `cash` itself is what decides reach
        # and is correct on its own; this is only so the phone can say WHY
        # it is short of the raw balance instead of leaving that a mystery.
        "cash_locked": u.locked_cash,
        "squad_value": squad_value(u),
        "jornadas_left": len(u.state.jornadas),
        "acquirable": len(u.price),
        "considered": n_actions,
        # ROUNDED, NOT RAW — checked 2026-08-31 (Miguel asked directly
        # whether FINAL_TRIALS itself was false precision; see its own
        # comment in decide.py for the fuller answer). These two are
        # LEVELS, not the paired differences the ranking runs on, so they
        # carry real Monte Carlo noise — a few points either way across a
        # realistic trial-count range — and the raw float (previously
        # something like 0.22233333333333335) implied precision the sample
        # does not have. 2dp on expected_finish matches the markdown's own
        # "%.2f" (header()); 3dp on p_win is finer than the markdown's
        # "%.0f%%" but nowhere near the 17-digit noise this replaces.
        "expected_finish": round(base.expected_position(), 2),
        "p_win": round(base.position().get(1, 0.0), 3),
        "band": [lo, hi],
        "moves": moves,
        "sell": [{"name": names.get(k, k), "pos": u.pos.get(k, ""),
                  "raises": got} for k, got in dead_weight(u)],
        # WHO THE MODEL SAYS IS WINNING, and the two readings that say so —
        # None on any run where it is me, which is the state this whole
        # mode is defined against. The phone needs it to caption the chase
        # rows for the same reason the markdown heading carries it: a
        # worse-on-average recommendation with no stated reason is worse
        # than no recommendation. See trailing(). Rounded here rather than
        # in trailing() itself — same "p_win" false-precision note above —
        # so trailing()'s own return value stays exact for its 0.5 trigger
        # comparison and for anything else that reads it directly.
        "trailing": ({**t, "p_win": round(t["p_win"], 3),
                     "leader_p_win": round(t["leader_p_win"], 3),
                     "p_above": round(t["p_above"], 3)}
                    if (t := trailing(u, base)) else None),
        "ladder": (ladder_data if ladder_data is not None
                  else ladder_rows(u, rows, base=base)),
        # `[]` and "nothing to cover" look the same here — see render()'s
        # matching note on why cover_data has no recompute-from-scratch
        # fallback the way ladder_data does.
        "cover": cover_data or [],
        "bar": _bar(u),
        "xi_total": _xi_total(u, u.me),
        "shape": _shape_now(u),
        "rival_best": _rival_best(u),
        "wait": wait,
        # "verdict"/"hold" RETIRED 2026-09-01. render()'s own comment
        # ("NO SENTENCES ABOVE THE TABLE") explains why verdict()'s prose
        # was deliberately dropped from the markdown report: a headline
        # sentence next to a table is one more thing that can disagree
        # with the table. This function kept computing and shipping that
        # same sentence to the phone anyway (Fantasy.jsx does not read
        # either field — checked before removing) — the exact fix-lands-
        # in-one-renderer-not-its-sibling shape this repo keeps finding,
        # just with the roles reversed (removed from markdown, missed in
        # JSON). verdict()/the "act"/"hold" prose stays defined for
        # whatever else reads it directly, only no longer surfaced here.
        "market_pct": market_percentile(wait),
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

    from decide import LISTED_SELLER

    xw = Crosswalk.read(TIDY / "players.csv", TIDY / "clubs.csv")
    cycles = collections.defaultdict(set)
    # LAST ROW SEEN WINS, same as decide.market_routes() — a player's route
    # rarely flips inside the observed window, so "his most recently seen
    # listing type" is the reading used for every cycle he appeared in,
    # not a per-cycle-exact one the feed does not cheaply support here.
    route_of: dict[str, str] = {}
    for r in read_csv(TIDY / "api_market.csv"):
        k = xw.player(app_id=r.get("player_id"),
                      app_name=r.get("player_name"))
        if k and k in u.value:
            cycles[(r.get("expires_at") or "")[:10]].add(k)
            route_of[k] = ("listed" if r.get("seller") == LISTED_SELLER
                           else "free")
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
    off = Offers.fit(pool, seen, per_cycle=per, cycles=len(cycles))
    # THE RAW PER-CYCLE SETS, attached rather than returned separately —
    # Offers.cycles is already a COUNT (len(cycles)), so this cannot
    # collide with it, and every caller of market_model() that only wants
    # the fitted sampler is unaffected. wait_routes() reads it (via
    # getattr, the same optional-capability pattern forecaster.rate_draw/
    # start_draw already use) to grade today's real best against real
    # single-cycle history instead of a resampled hypothetical — see
    # real_cycle_bests()'s own docstring for why that comparison exists.
    off.real_cycles = dict(cycles)
    off.real_routes = route_of
    return off


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
           locks_h=None, offers=None, ladder_data=None,
           cover_data=None) -> list[str]:
    # EVERYTHING UNDER A HEADING, including the preamble. digest.py drops a
    # source's H1 when it stitches the appendix and keeps what follows, so a
    # preamble above the first `## ` arrives in the middle of the report
    # reading as the tail of whatever section came before it — which here is
    # the board's warnings.
    #
    # ONE wait_routes() CALL FOR THE WHOLE REPORT, not three. Before this
    # (found 2026-09-01, swarm review) this ran here, again inside
    # waiting() a few lines below, and twice more in payload() for the
    # phone — four resamples of the same real market history per report
    # for a fact that does not change between them.
    routes = wait_routes(u, offers, rows=rows)
    # NO SENTENCES ABOVE THE TABLE. verdict() (and the overdraft paragraph
    # and the three-route panel it once introduced) was added to explain a
    # contradiction rather than to remove one, and each became another
    # thing on the page that could disagree with the table under it. What
    # is left is the position, the formation, and where this week's market
    # sits — all of it data. verdict() itself is retired below (it was
    # still being computed here and thrown away, and shipped unused to the
    # phone's JSON from payload() — see payload()'s own note).
    out = ["# The simulation — %s" % stamp, "", "## Now", ""]
    out += header(u, base, n_actions or len(rows), locks_h)
    pctl = market_percentile(routes)
    if pctl:
        out += ["_" + pctl + "_", ""]
    # THE RANKING IS SUBORDINATE TO THE CALL, and says so in its own heading.
    # Presented as "what to do" directly above a section saying "do nothing",
    # it is a contradiction rather than a second opinion.
    out += ["## Every player you could hold", ""]
    out += ladder(u, rows, base, ladder_data)

    # No `bands` reaches render() to recompute this from, unlike
    # ladder_data's `None` fallback — a caller with nothing to cover (an
    # ordinary run) and a caller that never asked look the same here,
    # and both render no section, which is the right answer for either.
    cover = cover_md(u, cover_data or [])
    if cover:
        out += ["## Covering the deficit — offers to accept", ""] + cover

    wait = waiting(u, offers, routes=routes)
    if wait:
        out += ["## Act now or wait — the workings", ""] + wait
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
             "d_pts": 120.0, "helps": 0.90, "mean": 1510.0,
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

    # -- payload()'s `moves` order: bar on d_pos, THEN rank by value ---------
    # Not pure points-per-euro (the fractional-knapsack trap this whole
    # change exists to avoid): C's near-zero gain divides out to a huge
    # ratio and must NOT leapfrog real moves on that alone.
    row_a = {"action": Action("buy", buy="A", cost=40e6), "d_pos": 0.40,
             "d_win": 0.0, "d_beat": {}, "value": 2.0}       # big, poor value
    row_b = {"action": Action("buy", buy="B", cost=1e6), "d_pos": 0.15,
             "d_win": 0.0, "d_beat": {}, "value": 50.0}      # clears the
    # floor (0.25 * 0.40 = 0.10 <= 0.15), excellent value
    row_c = {"action": Action("buy", buy="C", cost=1e4), "d_pos": 0.02,
             "d_win": 0.0, "d_beat": {}, "value": 500.0}     # below the
    # floor, spuriously huge ratio
    order = [m["buy"] for m in payload(u, [row_a, row_b, row_c], st,
                                       ["riv"])["moves"]]
    assert order == ["B", "A", "C"], order
    # A d_win-driven move still leads regardless of value — unchanged
    # behaviour, not something this change touches.
    row_win = {"action": Action("buy", buy="W", cost=40e6), "d_pos": 0.05,
              "d_win": 0.10, "d_beat": {}, "value": 1.0}
    order_win = [m["buy"] for m in payload(u, [row_a, row_win, row_b], st,
                                           ["riv"])["moves"]]
    assert order_win == ["W", "B", "A"], order_win

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
    cheap_ok = {**rows[0],
                "action": Action("buy", buy="cheap", cost=2e6, proceeds=0.0),
                "d_pos": 0.40, "d_win": 0.30}     # 92% of 0.433, 1/7th the cost
    assert _best(u, [rows[0], cheap_ok], ["riv"]) == (cheap_ok, False), \
        "a move keeping 90%+ of the best gain for a fraction of the cost wins"
    cheap_bad = {**rows[0],
                 "action": Action("buy", buy="cheap", cost=2e6, proceeds=0.0),
                 "d_pos": 0.30, "d_win": 0.20}    # 69% of 0.433 — below the floor
    assert _best(u, [rows[0], cheap_bad], ["riv"]) == (rows[0], False), \
        "a cheaper move that gives up too much of the gain does not win"
    free = {**rows[0],
            "action": Action("sell", sell=("dead",), cost=0.0, proceeds=1e6),
            "d_pos": 0.40, "d_win": 0.30}
    assert _best(u, [rows[0], free], ["riv"]) == (free, False), \
        "a self-funding move within reach of the best gain wins outright"
    # A pure-win-probability gain (d_pos <= 0) has no "% of the best gain"
    # to compare against — the cost guard is skipped, not divided by zero.
    winonly = {**rows[0], "action": Action("buy", buy="x", cost=1e6),
              "d_pos": 0.0, "d_win": 0.05}
    assert _best(u, [winonly], ["riv"]) == (winonly, False)

    # -- reliable routes first: a "listed" move (a rival's own sale, which
    # this league's real history says has never once gone through — see
    # _best()'s own docstring) is not the automatic pick while a free/clause
    # move is on the table, even a smaller one -----------------------------
    listed_big = {**rows[0],
                  "action": Action("buy", buy="listed_target", cost=30e6),
                  "d_pos": 0.50, "d_win": 0.40}
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

    # -- trailing mode: 1-2 high-ceiling "chase" picks, and ONLY when the
    # model itself says somebody else is winning this league ---------------
    # THE CASE THIS EXISTS FOR: the value-for-money ranking (b499df7) is
    # right for a leading or at-parity manager and answers "most points per
    # euro". A manager the model says is LOSING does not want the most
    # points per euro, he wants the widest band — see trailing()'s and
    # CHASE_PICKS's own notes. `maverick` below is exactly the candidate the
    # existing ranking buries and the trailing case wants: worst d_pos of
    # the three, worst value of the three, and by far the widest band.
    # Checked against the pre-change code before implementing: this fixture
    # ranks steady, dud, maverick — the highest ceiling on the board sits
    # DEAD LAST, in both renderers.
    lead_row = {"action": Action("buy", buy="steady", cost=5e6),
                "d_pos": 0.40, "d_win": 0.0, "d_beat": {}, "value": 8.0,
                "d_pts": 40.0, "pts_lo": 10.0, "pts_hi": 70.0, "helps": 0.80}
    maverick = {"action": Action("buy", buy="maverick", cost=5e6),
                "d_pos": 0.05, "d_win": 0.0, "d_beat": {}, "value": 1.0,
                "d_pts": 5.0, "pts_lo": -120.0, "pts_hi": 260.0,
                "helps": 0.45}
    dud = {"action": Action("buy", buy="dud", cost=5e6),
           "d_pos": 0.20, "d_win": 0.0, "d_beat": {}, "value": 4.0,
           "d_pts": 20.0, "pts_lo": 0.0, "pts_hi": 45.0, "helps": 0.70}
    ch_rows = [lead_row, maverick, dud]
    # `st` is a dead heat — both managers win 2 of 4 simulated seasons — so
    # the mode must not fire at all. The report is already correct there.
    assert trailing(u, st) == {}, trailing(u, st)
    assert chase_keys(u, ch_rows, st) == {}
    # Behind: riv takes 3 of the 4 simulated seasons, and I finish above him
    # in only 1. Both of the trigger's halves, not one.
    st_lo = Standings(totals={"me": [1000.0, 1100.0, 1200.0, 1300.0],
                              "riv": [1500.0, 1400.0, 1350.0, 1250.0]},
                      me="me")
    t = trailing(u, st_lo)
    assert t and t["leader"] == "riv", t
    assert t["p_above"] == 0.25 and t["p_win"] == 0.25, t
    assert t["leader_p_win"] == 0.75, t
    # A LEADER I NONETHELESS BEAT HEAD TO HEAD is not "trailing". riv takes
    # the league 50% to my 25% (half 1 fires) — but I OUTSCORE him in half
    # the seasons, and the two he loses to me are two `third` steals. The
    # gambler's-ruin argument is about the man ahead of ME, so half 2 does
    # not fire and neither does the mode. (Written against an
    # implementation with half 1 only, to confirm it fires there.)
    st_odd = Standings(totals={"me": [1000.0, 1000.0, 1000.0, 1000.0],
                               "riv": [900.0, 900.0, 1500.0, 1500.0],
                               "third": [800.0, 1200.0, 700.0, 700.0]},
                       me="me")
    u_third = Universe(state=LeagueState({"me": {}, "riv": {}, "third": {}},
                                         [1, 2], "me"),
                       forecaster=Bootstrap({}, pool=[1, 2, 3]), pos={},
                       price={}, proceeds={}, owner={}, cash=0.0, me="me")
    assert st_odd.position("riv").get(1) == 0.5, "fixture: riv leads on p_win"
    assert st_odd.beat("riv") == 0.5, "fixture: but I am not behind him"
    assert trailing(u_third, st_odd) == {}, trailing(u_third, st_odd)
    # The value ranking's own winner leads the shopping list and is NOT a
    # chase pick — a chase pick is by construction something the existing
    # ranking did not already put first. The low-EV, wide-band candidate is.
    assert chase_keys(u, ch_rows, st_lo) == {"maverick": 1}, \
        chase_keys(u, ch_rows, st_lo)
    # 1-2, NEVER MORE — the research's own finding. A third genuinely
    # wide-band candidate is still scored and still shown, just not as a
    # chase pick.
    wide2 = {**maverick, "action": Action("buy", buy="wide2", cost=5e6),
             "pts_hi": 200.0}
    wide3 = {**maverick, "action": Action("buy", buy="wide3", cost=5e6),
             "pts_hi": 150.0}
    assert chase_keys(u, ch_rows + [wide2, wide3], st_lo) == \
        {"maverick": 1, "wide2": 2}, "1-2 picks, diminishing past two"
    # A LISTED ROUTE IS NOT A CHASE. 9b25510's own finding — 108 real
    # transactions in this league, zero manager-to-manager — so a candidate
    # whose seller can simply refuse is not a variance play, it is a wish.
    # No fallback either, unlike _best(): silence is a fine answer here.
    u.route["maverick"] = "listed"
    assert chase_keys(u, ch_rows, st_lo) == {}, \
        "a listed candidate cannot be a chase pick"
    del u.route["maverick"]

    # -- ...and it reaches BOTH renderers off that ONE computation ----------
    # "two renderings of one answer is how they come to disagree" — the same
    # rule b499df7 extracted _move_rank_key for. The markdown ladder and the
    # phone's JSON both read chase_keys(); neither re-derives it.
    uc = Universe(state=LeagueState({"me": {}, "riv": {}}, [1, 2], "me"),
                  forecaster=Bootstrap(
                      {j: {"steady": (5.0, 1.0), "maverick": (4.0, 1.0),
                           "dud": (3.0, 1.0)} for j in (1, 2)}),
                  pos={"steady": "MED", "maverick": "MED", "dud": "MED"},
                  price={"steady": 5e6, "maverick": 5e6, "dud": 5e6},
                  proceeds={}, owner={}, cash=10e6, me="me",
                  name={"steady": "steady", "maverick": "maverick",
                        "dud": "dud"})
    lad = ladder_rows(uc, ch_rows, base=st_lo)
    assert [r["group"] for r in lad] == ["chase", "buy", "buy"], lad
    assert lad[0]["name"].lower() == "maverick", lad[0]
    # The chase row keeps its real numbers — this is a LABEL on a genuine
    # ranked move, not a second opinion with arithmetic of its own.
    assert lad[0]["pts_hi"] == 260.0 and lad[0]["pts"] == 5.0, lad[0]
    # Not trailing: every row is an ordinary BUY, ranked by value as before.
    flat_lad = ladder_rows(uc, ch_rows, base=st)
    assert [r["group"] for r in flat_lad] == ["buy"] * 3, flat_lad
    assert ladder_rows(uc, ch_rows) == flat_lad, "no base, no chase"

    # -- BUY/RAID/LISTED split: free agents, a clause (cannot be
    # refused), and a listed target (the owner's own choice, which this
    # league's own real history says essentially never goes Miguel's way)
    # — three sections, same relative order preserved in each. 2026-09-01,
    # Miguel: "I want to know if any free players are worth buying
    # honestly", then "the raid should focus on the clause impacted
    # ones... no way they're selling willingly to me" once a real report
    # showed listed targets sitting under RAID as if a clause's certainty
    # applied to them too.
    riv_row = {"action": Action("clause", buy="rivals", cost=5e6),
              "d_pos": 0.60, "d_win": 0.0, "d_beat": {}, "value": 12.0,
              "d_pts": 60.0, "pts_lo": 20.0, "pts_hi": 90.0, "helps": 0.90}
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
        name={"steady": "steady", "maverick": "maverick", "dud": "dud",
             "rivals": "rivals", "wished": "wished"})
    all_rows = ch_rows + [riv_row, wish_row]
    owned_lad = ladder_rows(uc_owned, all_rows, base=st)
    # STILL ONE SORTED LIST, FILTERED, NOT THREE SEPARATELY RANKED ONES:
    # the two highest-value real candidates overall ("rivals" 12.0,
    # "wished" 10.0) lead the whole ranking and land in RAID/LISTED
    # respectively — neither promoted to BUY for being the best, neither
    # silently dropped, and "wished" specifically does NOT land in RAID
    # despite outranking every free-agent row on value.
    by_group = {}
    for r in owned_lad:
        by_group.setdefault(r["group"], []).append(r["name"].lower())
    assert by_group["buy"] == ["steady", "dud", "maverick"], by_group
    assert by_group["raid"] == ["rivals"], by_group
    assert by_group["listed"] == ["wished"], by_group
    md_owned = "\n".join(ladder(uc_owned, all_rows, st))
    assert "BUY — free agents" in md_owned, md_owned
    assert "RAID — a clause, cannot be refused" in md_owned, md_owned
    assert "LISTED — his own choice to sell" in md_owned, md_owned
    # BOTH SECTIONS PRESENT even when free-agent buys is empty: the
    # explicit "none clear the bar" line, not silence that could be
    # mistaken for "no candidates were even screened".
    no_free_lad = "\n".join(ladder(uc_owned, [riv_row], st))
    assert "none clear the bar today" in no_free_lad, no_free_lad
    assert "RAID" in no_free_lad, no_free_lad

    md = "\n".join(ladder(uc, ch_rows, st_lo))
    assert "CHASE" in md and "riv" in md, md
    assert "CHASE" not in "\n".join(ladder(uc, ch_rows, st))
    pl = payload(uc, ch_rows, st_lo, ["riv"])
    assert pl["trailing"]["leader"] == "riv", pl["trailing"]
    assert {m["buy"].lower(): m["chase"] for m in pl["moves"]} == \
        {"maverick": 1, "steady": None, "dud": None}, pl["moves"]
    # The JSON's ladder IS the markdown's ladder, chase group included.
    assert [r["group"] for r in pl["ladder"]] == [r["group"] for r in lad]
    flat = payload(uc, ch_rows, st, ["riv"])
    # ...and the existing value ranking of the moves list is untouched by
    # any of this: an ADDITIONAL signal, not a re-sort.
    assert [m["buy"] for m in pl["moves"]] == [m["buy"] for m in flat["moves"]]
    assert flat["trailing"] is None, flat["trailing"]
    assert all(m["chase"] is None for m in flat["moves"]), flat["moves"]

    # -- real_cycle_bests: real single-day bests, not a resampled fiction --
    # market_percentile() used to compare today's real best against a band
    # RESAMPLED from the whole unowned pool every simulated trial — which
    # beats a real day's actual listing almost by construction (a maximum
    # over many independent draws vs. one real day's), and read "under the
    # 1st percentile" on 15 real days running regardless of whether the
    # market was actually good or bad that day. This asks the fair question:
    # how does today's real best compare to the OTHER real days this repo
    # has actually observed.
    cyc = {"2026-08-15": {"def1", "med1"}, "2026-08-16": {"def2", "del1"},
          "2026-08-17": {"med2"}}
    posn = {"def1": "DEF", "def2": "DEF", "med1": "MED", "med2": "MED",
           "del1": "DEL"}.get
    gains = {"def1": 1.0, "def2": 3.0, "med1": 2.0, "med2": 0.5,
            "del1": 5.0}.get
    # One bucket, unstratified (group_of=None): the best PER CYCLE, not
    # every player's own gain — a cycle offering three duds and one gem is
    # one real observation of "what a cycle can produce," not four.
    flat_hist = real_cycle_bests(cyc, gains)
    assert flat_hist == {"": [2.0, 5.0, 0.5]}, flat_hist
    # STRATIFIED: DEF's own history only has two real cycles with a DEF row
    # in them at all — the third cycle (med2 only) contributes nothing to
    # DEF's bucket, silence rather than a guessed zero.
    by_pos = real_cycle_bests(cyc, gains, posn)
    assert by_pos == {"DEF": [1.0, 3.0], "MED": [2.0, 0.5], "DEL": [5.0]}, \
        by_pos
    assert real_cycle_bests({}, gains) == {}

    # -- wait_routes()'s "market" branch actually reads real_cycles --------
    # THIS WAS NEVER EXERCISED BEFORE — no test in this file constructed a
    # real `offers` and called wait_routes with it, so the whole `if offers
    # is not None:` branch (everything above) could have been silently
    # broken and every suite would still have read green. Built here rather
    # than left green-by-omission.
    import random
    from ffcore.market import Offers
    from ffcore.season import LeagueState as LS

    sqw = {"k": "POR", **{f"d{i}": "DEF" for i in range(1, 5)},
          **{f"m{i}": "MED" for i in range(1, 6)}, "f1": "DEL"}
    perw = {1: {f"me_{k}": (3.0, 1.0) for k in sqw}}
    uw = Universe(
        state=LS({"me": {f"me_{k}": v for k, v in sqw.items()}}, [1], "me"),
        forecaster=Bootstrap(perw), pos={"free_def": "DEF", "hist_def": "DEF",
                                         "hist_med": "MED"},
        price={"free_def": 1e6}, proceeds={}, owner={}, cash=99e6, me="me",
        route={"free_def": "free"},
        market_exp={"free_def": 4.0, "hist_def": 6.0, "hist_med": 5.0,
                   "phantom_star": 23.0})
    # today's only DEF offer (free_def) gains 4.0 - bar; two real past
    # cycles each offered ONE better DEF (hist_def, gain 6.0) — a real,
    # thin, but genuine history to grade against. phantom_star is in the
    # SIMULATED pool (value-weighted, so best_over() draws him often) but
    # has NEVER actually been observed in a real cycle — the exact Lamine
    # Yamal pattern that made best_over()'s own median 7.37 against a real
    # median of 4.15 on live data. If "best"/"lo"/"hi" still read from
    # best_over() he shows up in them; if they read from real_cycles he
    # cannot, because he is not in it.
    off = Offers.fit({"free_def": 4e6, "hist_def": 6e6, "hist_med": 5e6,
                      "phantom_star": 200e6},
                     [4e6, 6e6], per_cycle=2, cycles=2)
    off.real_cycles = {"c1": {"hist_def"}, "c2": {"hist_def", "hist_med"}}
    # hist_def has always been a rival's own LISTED player (contested,
    # per Step 1); hist_med has always been a true free agent.
    off.real_routes = {"hist_def": "listed", "hist_med": "free"}
    routes = wait_routes(uw, off, random.Random(1))
    mkt = next(r for r in routes if r["route"] == "market")
    # THE HEADLINE POOL IS RELIABLE CYCLES ONLY — see wait_routes()'s own
    # note. c1 offered nothing but hist_def, a LISTED rival player (route
    # data below), so it now contributes NOTHING to the headline: n_band=1,
    # not 2, and the single surviving observation is c2's real free agent
    # (hist_med) alone. Before this fix n_band read 2 and best/lo/hi read
    # 3.0 — hist_def's contested listing, which per decide.py's own finding
    # (9b25510) has converted zero times in 108 real transactions, counted
    # as a full real cycle of evidence that "this is a good week to wait."
    assert mkt["n_band"] == 1, mkt["n_band"]
    # "best" (really the median) and the 10/90 band GRADED FROM RELIABLE
    # REAL HISTORY ONLY — the sole surviving observation is gain(hist_med)
    # in c2, so the "median" of one real number is that number.
    assert mkt["best"] == 2.0, mkt["best"]
    assert mkt["lo"] == 2.0 and mkt["hi"] == 2.0, (mkt["lo"], mkt["hi"])
    assert "DEF" in mkt["by_position"], mkt["by_position"]
    # hist_def (gain 6.0) beat today's own DEF best (free_def, gain ~2.0
    # after the bar) in BOTH real cycles — beats_now must read 1.0, not
    # some fraction only best_over()'s hypothetical band could produce.
    assert mkt["by_position"]["DEF"]["beats_now"] == 1.0, mkt["by_position"]
    assert mkt["by_position"]["DEF"]["n"] == 2
    # MED has real history (one cycle, c2) but NOTHING of that position is
    # actually offered today — "how does today's MED market compare" has
    # no today to grade, so it is correctly absent, not padded to zero.
    assert "MED" not in mkt["by_position"], mkt["by_position"]

    # -- by_route: a free pickup graded against real free history, a
    # contested rival listing against real listed history — never blended,
    # the whole reason Step 1 split "market" into "free"/"listed" at all.
    assert mkt["by_route"]["free"]["n"] == 1, mkt["by_route"]     # hist_med
    assert "listed" not in mkt["by_route"], mkt["by_route"]
    # WHY "listed" IS ABSENT: today's only real offer (free_def) is
    # route="free" — nothing LISTED is on offer today, so there is no
    # "today" for the listed side to grade, same silence-not-a-guess rule
    # as MED above. hist_def's real history (2 cycles, all "listed")
    # exists but has nothing of TODAY to compare against.

    # A REAL HISTORY THAT IS ENTIRELY LISTED falls back to best_over()'s
    # band too, exactly like no real history at all — the whole point of
    # the fix above. Two real cycles, both offering hist_def and nothing
    # else, hist_def LISTED throughout: every cycle's own best is filtered
    # to None, so `hist` ends up genuinely empty (not a guessed zero), and
    # n_band reads best_over's own trial count rather than claiming two
    # cycles of evidence for a market that offered zero real opportunities.
    off_all_listed = Offers.fit({"free_def": 4e6, "hist_def": 6e6},
                                [4e6, 6e6], per_cycle=1, cycles=2)
    off_all_listed.real_cycles = {"c1": {"hist_def"}, "c2": {"hist_def"}}
    off_all_listed.real_routes = {"hist_def": "listed"}
    all_listed_routes = wait_routes(uw, off_all_listed, random.Random(1))
    all_listed_mkt = next(r for r in all_listed_routes
                          if r["route"] == "market")
    assert all_listed_mkt["n_band"] > 2, all_listed_mkt["n_band"]

    # A market_model()-shaped offers with NO real_cycles attached (an old
    # fixture, or a caller not wired to it) falls back to best_over()'s
    # band exactly as before — not a crash, not an empty report.
    off_plain = Offers.fit({"free_def": 4e6}, [4e6], per_cycle=1, cycles=1)
    plain_routes = wait_routes(uw, off_plain, random.Random(1))
    plain_mkt = next(r for r in plain_routes if r["route"] == "market")
    assert plain_mkt["by_position"] == {}, plain_mkt["by_position"]
    assert plain_mkt["by_route"] == {}, plain_mkt["by_route"]
    assert plain_mkt["n_band"] > 2, plain_mkt["n_band"]   # best_over's trials

    # -- "Act today" is the REAL simulated figure when `rows` is given -----
    # Before this (found 2026-09-01, swarm review), "Act today" was ALWAYS
    # season()'s cheap linear estimate — sharing the move table's own
    # "Season" column header two sections above while being a genuinely
    # cruder number under it (a live case overstated the real figure by
    # more than 3x). No caller in this file passed `rows` before this test
    # was added, so the branch that uses it had never actually run.
    plain_act = next(r for r in plain_routes if r["route"] == "act")
    assert not plain_act.get("simulated"), plain_act
    fake_rows = [{"d_pts": 12.0, "pts_lo": -5.0, "pts_hi": 40.0},
                {"d_pts": 55.0, "pts_lo": 3.0, "pts_hi": 120.0}]
    real_routes = wait_routes(uw, off_plain, random.Random(1),
                              rows=fake_rows)
    real_act = next(r for r in real_routes if r["route"] == "act")
    assert real_act["simulated"] is True, real_act
    # THE BEST OF `rows`, NOT THE FIRST OR AN AVERAGE — "Act today" means
    # the single best thing reachable right now.
    assert (real_act["pts"], real_act["lo"], real_act["hi"]) \
        == (55.0, 3.0, 120.0), real_act

    # -- one line instead of a panel ---------------------------------------
    # The panel sat outside the one table, compared three routes in a table of
    # its own, and carried prose that was unconditional — it went on saying
    # spending now was the worse option on days the headline said act today.
    r = [{"route": "act", "best": 1.0, "pts": 10.0},
         {"route": "market", "best": 2.0, "pts": 20.0, "beats_now": 0.38}]
    # quiet={} throughout this section pins the feeds to "all fresh" — these
    # calls are testing the formatting logic, not the staleness gate (that
    # gets its own case below with an explicit quiet=). Without it, this
    # defaults to the REAL stale_feeds() reading the box's live tidy store,
    # so the test's pass/fail depended on how long ago the last successful
    # fetch was — and since this self-test runs BEFORE the fetch stage,
    # every run saw yesterday's fetch timestamp, already past
    # EVERY_RUN_FRESH_DAYS by the time the once-a-day timer fired again.
    line = market_percentile(r, quiet={})
    assert "62nd percentile" in line, line
    assert "38% of weeks" in line, line
    assert "ordinary week" in line, line
    # A LINE OF DATA, NOT A SENTENCE. Every sentence added above the table was
    # added to explain a contradiction rather than remove one, and each became
    # another thing on the page that could disagree with the table.
    assert "." not in line.replace("62nd", "").replace("38%", ""), line
    assert "75th percentile" in market_percentile(
        [{"route": "market", "beats_now": 0.25}], quiet={})
    assert "unusually good" in market_percentile(
        [{"route": "market", "beats_now": 0.25}], quiet={})
    assert market_percentile([], quiet={}) == ""
    # A QUIET FEED IS NOT A POOR MARKET. With api_market gated on freshness,
    # nothing is buyable, now_best is 0, every simulated offer beats it, and
    # this line said "0th percentile · a poor week · better in 100% of weeks"
    # — the report's headline claim about the market, made with no market in
    # front of it. Measured by ageing the store three days and generating.
    blind = market_percentile(r, quiet={"api_market": 3.1})
    assert "percentile" not in blind, blind
    assert "3 days stale" in blind, blind
    assert "unknown" in blind, blind
    # ...and a fresh feed is unaffected, whatever else has gone quiet.
    assert "62nd percentile" in market_percentile(
        r, quiet={"api_teams": 3.1}), r

    # -- EVERY simulated week beating today is a REAL result (a thin day's
    # actual listings vs. the full pool a typical week draws from, not a
    # stale/empty feed) — but reporting it as an exact "0th percentile" and
    # "100%" overclaims what n_band trials can resolve. Real case,
    # 2026-08-21: n_band=400, so the finest percentile it can name is
    # 1/400 = 0.25%, rounding to 1. ------------------------------------------
    extreme = market_percentile(
        [{"route": "market", "beats_now": 1.0, "n_band": 400}], quiet={})
    assert "under the 1st percentile" in extreme, extreme
    assert "unusually poor" in extreme, extreme
    assert "over 99%" in extreme, extreme
    assert "0th" not in extreme and "100%" not in extreme, extreme
    # The mirror case: NOTHING beat today, an unusually good market.
    best_ever = market_percentile(
        [{"route": "market", "beats_now": 0.0, "n_band": 400}], quiet={})
    assert "over the 99th percentile" in best_ever, best_ever
    assert "unusually good" in best_ever, best_ever
    assert "under 1%" in best_ever, best_ever
    # A smaller sample has a coarser floor — n=50 can only resolve to 2%.
    coarse = market_percentile(
        [{"route": "market", "beats_now": 1.0, "n_band": 50}], quiet={})
    assert "under the 2nd percentile" in coarse, coarse

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
    # EVERY MAN YOU HOLD gets a key, either his own best swap or a pure
    # sell if nothing affordable beats him; everyone above the bar you do
    # not hold gets a buy. "cand" (exp 4.0) beats every regular squad
    # member's (2.7) and "dead"'s (0.15), so band_acts() should offer
    # each of them a SWAP to "cand" rather than a pure sale — "star"
    # (exp 5.4) beats "cand" outright, so his stays a pure sell.
    asked = band_acts(ub)
    keys = {k for k, _a in asked}
    assert keys == {*sqb, "cand"}, keys
    by_key = dict(asked)
    assert by_key["dead"].buy == "cand" and by_key["dead"].sell == ("dead",)
    assert by_key["star"].buy == "" and by_key["star"].sell == ("star",)
    assert by_key["cand"].buy == "cand", by_key["cand"]

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
    # SELLING DEAD WEIGHT TO BUY A REAL UPGRADE GAINS POINTS — the whole
    # feature: a pure sale would have priced this near zero (nothing lost
    # fielding him), but the swap into "cand" (rated well above the bar)
    # is a real, positive gain, not a wash.
    assert bands["dead"][0] > 0, bands["dead"]
    assert bands["dead"][3].buy == "cand", bands["dead"]
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

    # -- cover_rows/cover_md: the OFFERS combos, end to end -----------------
    # SAME SQUAD, overdrawn, with real offers on two men. "star" is the
    # nailed starter (see above, costs 20+ points sold pure); "dead" never
    # plays, so his sale is close to free — the cheaper cover by a mile,
    # and cover_rows() should rank it first without being told which is
    # which.
    from dataclasses import replace as _replace

    uo = _replace(ub, cash=-2e6,
                  received_offers={"star": 4e6, "dead": 3e6})
    askedo = band_acts(uo) + decide.offer_combos(uo)
    assert any(k.startswith("OFFERS:") for k, _a in askedo), askedo
    _ro, _bo, _lo, bandso = decide.rank(uo, [], extra=askedo)
    data = cover_rows(uo, bandso)
    assert data, bandso
    assert {tuple(sorted(r["who"])) for r in data} == \
          {("Dead",), ("Star",)}, data
    # CHEAPEST FIRST: dead weight sold outright costs far less than a
    # nailed starter, and the sort has to find that from the numbers,
    # not from the label.
    assert data[0]["who"] == ["Dead"], data
    assert data[0]["pts"] > data[1]["pts"], data
    for r in data:
        assert r["surplus"] >= 0, r          # every combo really covers
        assert r["rate"] == r["pts"] / (r["raised"] / 1e6)
    md = cover_md(uo, data)
    assert "Dead" in md[-2] and "Star" in md[-1], md
    assert "Balance is" in md[0] and fmt_money(uo.cash) in md[0], md[0]

    # Nothing to cover — cash is not negative — renders nothing at all.
    assert cover_rows(ub, bands2) == []
    assert cover_md(ub, []) == []
    assert set(bands2) == set(sqb), sorted(bands2)

    # -- the race: who else can pay this clause, and when -------------------
    # THE CASE THE LEAGUE-WIDE PRIOR CANNOT MAKE. Two rivals, identical
    # balances (both flat broke) and identical squads, differing ONLY in the
    # rate each has actually raised money at this season — `quick` sells
    # 4M/day, `slow` 0.1M/day. Under one league-wide constant they would be
    # the same threat; measured per rival they are 5 days apart on the same
    # 20M clause, which is the difference between racing a target and
    # letting it go.
    ur = Universe(
        state=LeagueState({"me": {"me_a": "MED"}, "own": {"prize": "MED"},
                           "quick": {"q_a": "MED"}, "slow": {"s_a": "MED"}},
                          [1, 2], "me"),
        forecaster=Bootstrap({j: {k: (5.0, 1.0) for k in
                                  ("me_a", "prize", "q_a", "s_a")}
                             for j in (1, 2)}),
        pos={"prize": "MED", "me_a": "MED", "q_a": "MED", "s_a": "MED"},
        price={"prize": 20e6}, proceeds={}, cash=1e6, me="me",
        route={"prize": "clause"}, owner={"prize": "own"},
        value={"q_a": 60e6, "s_a": 60e6, "prize": 20e6},
        name={"prize": "prize"}, daily_bonus=1e5,
        rival_cash={"own": 0.0, "quick": 0.0, "slow": 0.0},
        tempo={"quick": {"sell_rate": 4e6, "sells": 8, "days": 20.0},
               "slow": {"sell_rate": 0.1e6, "sells": 1, "days": 20.0}})
    r_race = race(ur, "prize")
    assert [x["manager"] for x in r_race] == ["quick", "slow"], r_race
    assert r_race[0]["days"] == 5 and r_race[1]["days"] == 100, r_race
    # SAME BALANCE, SAME SQUAD, 20x THE WAIT — the distinction is the
    # measured rate and nothing else.
    assert r_race[1]["days"] == 20 * r_race[0]["days"], r_race
    assert race_cell(ur, r_race) == "quick can pay in ~5 days", \
        race_cell(ur, r_race)
    assert race_cell(ur, []) == ""                # nothing to say, say it
    assert race_cell(ur, [{"manager": "quick", "days": 0}]) == \
        "quick can pay today"
    assert race_cell(ur, [{"manager": "quick", "days": 1}]) == \
        "quick can pay in ~1 day"                 # not "1 days"

    # -- ...and it reaches BOTH renderers off that ONE computation ----------
    # Same rule as the chase block above: one race(), two drawings of it.
    race_rows = [{"action": decide.Action("clause", buy="prize", cost=20e6,
                                          victim="own"),
                  "d_pos": 0.5, "d_win": 0.0, "d_pts": 40.0, "value": 2.0,
                  "pts_lo": 10.0, "pts_hi": 70.0, "helps": 0.9,
                  "d_beat": {"own": 0.1}, "answer": None}]
    lad_r = ladder_rows(ur, race_rows)
    got_r = next(r for r in lad_r if r["name"].lower() == "prize")
    assert got_r["contest"] == r_race, got_r
    md_r = "\n".join(ladder(ur, race_rows, st))
    assert "quick can pay in ~5 days" in md_r, md_r
    # Beside the owner in the SAME cell, not a new column — the table still
    # has exactly its eight.
    assert "own · quick can pay in ~5 days" in md_r, md_r
    assert md_r.splitlines()[0].count("|") == 9, md_r.splitlines()[0]
    pl_r = payload(ur, race_rows, st, ["own", "quick", "slow"])
    assert next(m for m in pl_r["moves"]
               if m["buy"].lower() == "prize")["contest"] == r_race, \
        pl_r["moves"]
    # The JSON's ladder IS the markdown's ladder, race included.
    assert [r["contest"] for r in pl_r["ladder"]] == \
          [r["contest"] for r in lad_r], pl_r["ladder"]
    # A row that is not a payable clause carries an empty race in BOTH, and
    # renders nothing — no "nobody can pay this" noise on every free agent.
    ur.route["prize"] = "free"
    assert race(ur, "prize") == []
    # (The legend below the table explains the phrase and so contains it;
    # what must vanish is the NAMED rival on the row itself.)
    assert "quick can pay" not in "\n".join(ladder(ur, race_rows, st))
    ur.route["prize"] = "clause"

    # -- the caveat shows the measurement rather than asserting it ---------
    cav = "\n".join(caveats(ur))
    assert "can pay in ~N days" in cav, cav
    assert "quick 4.0M/day off 8 sales" in cav, cav
    assert "slow 0.1M/day off 1 sale" in cav, cav      # not "1 sales"
    assert fmt_money(ur.daily_bonus) in cav, cav
    # No ledger at all: the caveat says so instead of printing a rate it
    # does not have.
    ur.tempo = {}
    assert "nothing is on the ledger yet" in "\n".join(caveats(ur))
    # ...and with no measured rate the estimate DEGRADES to the allowance
    # alone (20M at 100K a day = 200 days for both) rather than inventing a
    # rate or going silent. Both rivals collapse onto the same number,
    # which is exactly the undifferentiated answer this commit replaced —
    # correct as a fallback, and visibly the weaker reading.
    assert [x["days"] for x in race(ur, "prize")] == [200, 200], \
        race(ur, "prize")

    print("sim self-test OK (194 cases)")


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
    # ONE PASS, SAME PRINCIPLE AS band_acts(): offer_combos() is empty
    # whenever cash is not negative, so this costs nothing on an ordinary
    # run and rides along in the pass already happening on the day it
    # matters.
    rows, base, measured, bands = decide.rank(
        u, acts, price=smoothed, extra=band_acts(u) + decide.offer_combos(u))
    log_cash_price(measured)
    u.cash_note = _price_note(smoothed, measured)
    rivals = [m for m in u.state.squads if m != u.me]
    # ONE COMPUTATION, READ BY BOTH RENDERERS. This used to be two: a
    # separate price_saves() call here feeding the markdown's SAVE section
    # a median with no band, and a second banded pass for the same players —
    # the exact "two renderings of one answer" duplication ladder()'s own
    # docstring now names directly. ladder_rows() already covers "save"
    # (and every other group) with a real band, so the separate median-only
    # pass is retired rather than kept as a second source for the same fact.
    # `base` so the ladder can carry the trailing-mode CHASE group — see
    # chase_keys(). Computed HERE, once, exactly like the bands beside it:
    # render() and payload() both draw this one list, so the markdown's
    # CHASE section and the phone's `chase` flags cannot disagree about
    # which 1-2 rows they are.
    ladder_data = ladder_rows(u, rows, bands, base)
    # cover_rows() reads the SAME bands dict ladder_data was built from —
    # decide.offer_combos()'s own rows, not a second simulation.
    cover_data = cover_rows(u, bands)
    # ONE market_model() CALL FOR THE WHOLE REPORT, not two — it fits an
    # Offers distribution off api_market.csv from scratch (a real CSV read
    # plus statistics.median() over every cycle), and render() and
    # payload() used to each trigger that fit independently for the exact
    # same market. Same principle as `ladder_data`/`bands` a few lines up:
    # computed here, once, and handed to both. Found 2026-09-01.
    offers = market_model(u)
    write_lines(PARTS / OUT,
                render(u, rows, base, stamp, rivals, len(acts), locks_h,
                       offers, ladder_data, cover_data))
    print("wrote %s (%d moves, %d simulated in full)"
          % (PARTS / OUT, len(acts), len(rows)))

    (REPORTS / "decisions.json").write_text(json.dumps({
        "generated_at": run_now()
                          .strftime("%Y-%m-%dT%H:%MZ"),
        **payload(u, rows, base, rivals, locks_h, len(acts),
                  offers, ladder_data=ladder_data,
                  cover_data=cover_data),
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
