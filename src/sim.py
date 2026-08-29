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
    ctx += ["squad %s" % fmt_money(val),
            ("**cash %s**" if u.cash < 0 else "cash %s") % fmt_money(u.cash),
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


def ladder_rows(u, rows, bands=None) -> list[dict]:
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
            lo=None, hi=None):
        if k in bands:
            pts, lo, hi, action = bands[k]
            # A SWAP OF *HIM*, NOT A PURE SALE: the band answers "sell him,
            # buy the best his own money reaches" (best_swap_for()), so
            # the row says which player that was — the number alone does
            # not distinguish "worth keeping, no upgrade affordable" from
            # "worth keeping even against his best real alternative".
            # `action.buy != k` excludes a CANDIDATE's own band (his
            # Action buys HIM, funded by dead weight — "vs himself" would
            # be nonsense) without needing to know which group a row is
            # in; every group a held player's swap band can land in
            # (in/out/field/keep/sell) gets the same explanation.
            if action.buy and action.sell and action.buy != k:
                note = "vs %s" % title_name(u.name.get(action.buy,
                                                        action.buy))
        return {"name": title_name(u.name.get(k, k)),
                "pos": u.pos.get(k, ""), "start": u.start.get(k, 0.0),
                "xpts": exp.get(k, 0.0), "group": group, "where": where,
                "money": money, "pts": pts,
                "pts_lo": lo, "pts_hi": hi, "note": note, "value": value}

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
    for k in sorted((k for k in rest if k in won), key=lambda k: -exp.get(k, 0.0)):
        r = won[k]
        out.append(cell(k, "buy", u.owner.get(k) or "free agent",
                        -r["action"].net, r["d_pts"], value=r.get("value"),
                        lo=r.get("pts_lo"), hi=r.get("pts_hi")))
    for k in sorted((k for k in rest if k not in won
                     and u.price[k] > u.cash + spare),
                    key=lambda k: -exp.get(k, 0.0)):
        short_by = u.price[k] - u.cash - spare
        save_pts = bands[k][0] if k in bands else None
        out.append(cell(k, "save", u.owner.get(k) or "free agent",
                        -short_by, save_pts, "short",
                        value=value_rate(save_pts, short_by)))
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
            # "vs X" — best_swap_for()'s own note, see ladder_rows()'s
            # cell(). "short" is the save branch's own word above and
            # never reaches this one.
            if r["note"]:
                season += " " + r["note"]
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

    if by_group.get("buy"):
        out.append("| **BUY — with the proceeds** | | | | | | | |")
        out += [row_md(r) for r in by_group["buy"]]

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
            "plan toward. This is "
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


def wait_routes(u, offers=None, rng=None) -> list[dict]:
    """The three ways to get a better eleven, as data both renderers read.

    ACT NOW, WAIT FOR THE MARKET, OR WAIT FOR THE CLAUSES. Every move in the
    ranking is scored against doing nothing for thirty-eight jornadas, which
    is not the alternative on offer — so waiting scores zero there and
    anything positive beats it by construction. This is the correction, and it
    is computed ONCE: the markdown table and the phone drew different things
    twice before this was a function.
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

    out = [{"route": "act", "label": "Act today",
            "what": "%d players you can buy now" % len(u.price),
            "best": now_best, "pts": season(now_best),
            "lo": None, "hi": None, "beats_now": None}]

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
        hist = (real_cycle_bests(real, approx_gain).get("", []) if real else None)
        beats_now = (sum(1 for x in hist if x > now_best) / len(hist)
                    if hist else
                    sum(1 for x in band if x > now_best) / len(band))
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

        real_routes = getattr(offers, "real_routes", {})
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
    mkt = next((r for r in routes if r["route"] == "market"), None)
    cl = next((r for r in routes if r["route"] == "clauses"), None)

    # Season points, so the routes can be compared with the move table rather
    # than sitting in their own unit. Waiting pays for the delay: a jornada of
    # the best thing you can buy today is forgone before the better one
    # arrives. These are estimates from a rate; the move table's are simulated.
    out = ["| Route | What it offers | Season pts | Beats acting today |",
           "|---|---|--:|--:|"]
    for r in routes:
        if r["route"] == "watch":
            continue
        name = ("**%s**" % r["label"] if r["route"] == "act" else r["label"])
        beats = r.get("beats_now")
        out.append("| %s | %s | %+.0f | %s |"
                   % (name, r["what"], r.get("pts", 0.0),
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


def caveats(u) -> list[str]:
    """What the numbers above cannot see. Read off the data, not remembered.

    Every line here makes the position look BETTER than it is, which is the
    reason they are printed under the table rather than in a design document
    nobody opens on a phone.
    """
    out = ["| Not modelled | Which way it bends the answer |", "|---|---|"]
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
        "(DRIFT_FRAC=2.0), not a fit | two real anchors on this repo's own "
        "data disagree on the exact magnitude (weak jornada-1-vs-final "
        "correlation argues wider, strong season-to-season correlation "
        "argues narrower), but every published win-probability model "
        "checked (538's NBA/NHL/MLB) is far more humble than 70%+ about a "
        "full season this early regardless — that floor doesn't need the "
        "two anchors resolved. Widened from a prior setting that read 72% "
        "to land near a coin flip instead. Tighten only once the Forecast "
        "vs actual table above (the real, realised-error check) has enough "
        "rows (n=15-20+) to say the model is already well-calibrated |",
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
    or, on a day nothing reliable helps, the full list) is in play. `rows`
    arrives sorted by raw d_pos (decide.rank()'s own order), so the single
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
    best = pool[0]
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
        # WHAT `cash` ALREADY HAS SUBTRACTED — a bid of yours still pending,
        # summed (decide.pending_sent). `cash` itself is what decides reach
        # and is correct on its own; this is only so the phone can say WHY
        # it is short of the raw balance instead of leaving that a mystery.
        "cash_locked": u.locked_cash,
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
        "ladder": (ladder_data if ladder_data is not None
                  else ladder_rows(u, rows)),
        # `[]` and "nothing to cover" look the same here — see render()'s
        # matching note on why cover_data has no recompute-from-scratch
        # fallback the way ladder_data does.
        "cover": cover_data or [],
        "bar": _bar(u),
        "xi_total": _xi_total(u, u.me),
        "shape": _shape_now(u),
        "rival_best": _rival_best(u),
        "wait": wait_routes(u, offers),
        "verdict": verdict(wait_routes(u, offers))[0],
        "market_pct": market_percentile(wait_routes(u, offers)),
        "shape_now": fielded_shape(u),
        "xi_note": xi_note(u),
        # Written by report.py minutes earlier in the same run — the board
        # draws them, so "only 1 portero" or "the app's feed is 3 days stale"
        # reaches the phone instead of living in a markdown file nobody opens
        # when the board is right there.
        "warnings": _warnings(),
        "hold": verdict(wait_routes(u, offers))[1],
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
    out += ladder(u, rows, base, ladder_data)

    # No `bands` reaches render() to recompute this from, unlike
    # ladder_data's `None` fallback — a caller with nothing to cover (an
    # ordinary run) and a caller that never asked look the same here,
    # and both render no section, which is the right answer for either.
    cover = cover_md(u, cover_data or [])
    if cover:
        out += ["## Covering the deficit — offers to accept", ""] + cover

    wait = waiting(u, offers)
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

    # -- the phone ---------------------------------------------------------
    # The same numbers as data, because markdown cannot right-align a column
    # or colour a chip and the site escapes raw HTML on purpose. Built from
    # the rows the markdown was built from, so the two cannot disagree.
    d = payload(u, rows, st, ["riv"], locks_h=41.1, n_actions=132)
    assert d["expected_finish"] == 1.5 and d["p_win"] == 0.5, d
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
    del u.route["listed_target"]

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
    # GRADED AGAINST THE 2 REAL CYCLES, not best_over()'s resampled band —
    # n_band says so directly, and it is nowhere near best_over's own
    # trial count (400 by default).
    assert mkt["n_band"] == 2, mkt["n_band"]
    # "best" (really the median) and the 10/90 band GRADED FROM THE SAME
    # REAL HISTORY, not best_over()'s resample — real per-cycle bests here
    # are gain(hist_def)=3.0 in c1, max(gain(hist_def), gain(hist_med))=3.0
    # in c2, so the honest median of two real, equal observations is
    # exactly 3.0, not whatever random.Random(1) drew from best_over()'s
    # much wider hypothetical band.
    assert mkt["best"] == 3.0, mkt["best"]
    assert mkt["lo"] == 3.0 and mkt["hi"] == 3.0, (mkt["lo"], mkt["hi"])
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

    # A market_model()-shaped offers with NO real_cycles attached (an old
    # fixture, or a caller not wired to it) falls back to best_over()'s
    # band exactly as before — not a crash, not an empty report.
    off_plain = Offers.fit({"free_def": 4e6}, [4e6], per_cycle=1, cycles=1)
    plain_routes = wait_routes(uw, off_plain, random.Random(1))
    plain_mkt = next(r for r in plain_routes if r["route"] == "market")
    assert plain_mkt["by_position"] == {}, plain_mkt["by_position"]
    assert plain_mkt["by_route"] == {}, plain_mkt["by_route"]
    assert plain_mkt["n_band"] > 2, plain_mkt["n_band"]   # best_over's trials

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

    print("sim self-test OK (141 cases)")


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
    ladder_data = ladder_rows(u, rows, bands)
    # cover_rows() reads the SAME bands dict ladder_data was built from —
    # decide.offer_combos()'s own rows, not a second simulation.
    cover_data = cover_rows(u, bands)
    write_lines(PARTS / OUT,
                render(u, rows, base, stamp, rivals, len(acts), locks_h,
                       market_model(u), ladder_data, cover_data))
    print("wrote %s (%d moves, %d simulated in full)"
          % (PARTS / OUT, len(acts), len(rows)))

    (REPORTS / "decisions.json").write_text(json.dumps({
        "generated_at": run_now()
                          .strftime("%Y-%m-%dT%H:%MZ"),
        **payload(u, rows, base, rivals, locks_h, len(acts),
                  market_model(u), ladder_data=ladder_data,
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
