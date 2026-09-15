"""
ffcore/candidates.py — every affordable move worth simulating, and what
a squad looks like after making one.

Split out of decide.py 2026-09-15 (rationalization plan session 4).
Lazy-imports current_xi/xi_bar/route_kind/_fieldable/player_forecasts/
value_rate from decide.py at CALL time, not module load time — the same
precedented pattern methodology.py's _fc() already uses to read decide.load()
without a circular import. Those six functions stay in decide.py (the
Universe/Action-adjacent core); moving them here too would just relocate
the tangle rather than resolve it. Safe because nothing here is called
during decide.py's own module-level execution — only after decide.py has
fully imported, by which point `import decide` sees a complete module.
"""

from __future__ import annotations

from ffcore.action import Action
from ffcore.par import player_forecasts, value_rate
from ffcore.schedule import phantom_topup
from ffcore.season import best_xi


def fieldable_spares(u) -> list[str]:
    """Everyone in my squad whose sale ALONE still leaves a fieldable
    shape — the real funding pool candidates() draws from, not just
    dead_weight()'s narrower "never starts at all" subset. Pulled out so
    sim.ladder_rows()'s "is this target reachable" check can ask the
    SAME question candidates() actually answers, instead of a cheaper,
    narrower one that can silently disagree with it.
    Why: docs/notes/decide.md#rank--funding-variant-noise
    """
    from decide import _fieldable

    mine_squad = u.state.squads.get(u.me, {})
    return [k for k in mine_squad if _fieldable(
        {p: s for p, s in mine_squad.items() if p != k})]


def max_spare_proceeds(u) -> float:
    """The most a SINGLE spare sale could ever raise — matching exactly
    what candidates() can fund a buy with (cash, or exactly one spare,
    never a sale chain). 0.0 with no fieldable spare at all.
    """
    return max((u.proceeds.get(k, 0.0) for k in fieldable_spares(u)),
              default=0.0)


def candidates(u, expected: dict[str, float],
               budget: float | None = None) -> list[Action]:
    """Every affordable move, pruned to the ones that could plausibly help.

    The prune is on EXPECTED points, not on the simulation: it is a filter for
    what to simulate, so it only has to be roughly right, and it turns
    thousands of combinations into dozens. A candidate who would not make your
    eleven on expectation will not make it on a draw either.

    FUNDED BY CASH, OR BY SELLING EXACTLY ONE SPARE PLAYER — deliberately
    not a multi-sale chain, which risks leaving the squad short a legal
    XI. A genuinely 2-sale-only move stops appearing; that's the trade.
    """
    from decide import current_xi, xi_bar, route_kind

    cash = u.cash if budget is None else budget
    mine_squad = u.state.squads.get(u.me, {})
    mine = set(mine_squad)
    # The eleven the signing has to beat is current_xi()'s own reading — a
    # bar that comes back empty (no jornada left to pick at all) falls back
    # to the round passed in rather than a bar of zero that clears nothing.
    bar_exp, xi = current_xi(u)
    if not bar_exp:
        bar_exp = expected
        xi = set(best_xi(u.state.squads[u.me], bar_exp))
    bar = xi_bar(bar_exp, xi)
    # EVERY spare: selling him ALONE still leaves a fieldable shape — tried
    # as a funder for every target, not just a cheap-by-some-metric top few.
    # An earlier cut to the "best" 6 by raw points, then by value-per-euro,
    # each missed the same real case a different way: a good player who is
    # genuinely redundant (a second keeper, MAX_SLOT["POR"]=1) can rank
    # above enough of the squad by ANY single-number heuristic to fall
    # outside a fixed-size cut, however that heuristic is defined — the cut
    # itself was the bug, not which ranking fed it. Measured: trying every
    # spare against every target costs ~0.5s more per report (candidates()
    # generating a few hundred more Actions for rank()'s already-cheap
    # SCREEN_TRIALS pass to discard) — not the thousands-of-combinations
    # explosion a cap was guarding against.
    # Ordered by value above LEAGUE replacement PER EURO he'd raise
    # (player_forecasts()'s own PAR, a fixed league-wide baseline, over
    # value_rate()'s cost normalisation) purely so a reader scanning
    # generated Actions sees the most plausible funders first; every one
    # of them still reaches rank()'s real simulation regardless of order.
    # _fieldable() is the one hard constraint (job 1: never propose an
    # illegal squad); value-per-euro only orders the search (job 2), it
    # doesn't gate it — see the Why: below for the ad hoc carve-out this
    # replaced when the two jobs were tangled into one cutoff.
    # Why: docs/notes/decide.md#optimize-for-competent-play-warn-dont-model-for-incompetent-play
    fieldable_spare = fieldable_spares(u)
    par_of = {k: v["par"] for k, v in player_forecasts(u).items()}

    def _spare_rank(k):
        vr = value_rate(par_of.get(k, 0.0), u.proceeds.get(k, 0.0))
        # No proceeds means nothing to fund with regardless of how little
        # he's worth keeping — ranks last, never first.
        return (vr is None, vr if vr is not None else 0.0)

    spare = sorted(fieldable_spare, key=_spare_rank)

    out: list[Action] = []
    for c, price in sorted(u.price.items(), key=lambda kv: kv[1]):
        if c in mine or bar_exp.get(c, 0.0) <= bar:
            continue
        # Never propose a listed target (owned, not a clause) as a move —
        # 0/119 real deals in this league have ever been a rival's own
        # choice to sell to a manager. route_kind() is the one place this
        # gets decided.
        kind_ = route_kind(u, c)
        if kind_ == "listed":
            continue
        raid = kind_ == "raid"
        victim = u.owner.get(c, "") if raid else ""
        kind = "clause" if raid else "buy"
        swap = kind + "-swap" if raid else "swap"
        if price <= cash:
            out.append(Action(kind, buy=c, cost=price, victim=victim))
        # Funded by a sale: every spare is tried (spare's own docstring
        # above), worst-value-per-euro first.
        for s in spare:
            got = u.proceeds.get(s, 0.0)
            if price <= cash + got:
                out.append(Action(swap, buy=c, sell=s, cost=price,
                                  proceeds=got, victim=victim))
    return out


def dead_weight(u) -> list[tuple[str, float]]:
    """[(player, what he raises)] for everyone in my squad who never starts.

    A man who makes none of the remaining elevens contributes nothing on the
    pitch, so selling him costs nothing and any offer is a gain — the one
    verdict reachable without valuing cash.

    Checked against every jornada you can still PICK, not every jornada
    left: a round already in progress has its eleven locked to a smaller
    pool (clubs that have kicked off drop out), so a man who only starts
    THERE isn't being fielded by any decision still open to you. Does not
    rank these against each other or say what to hold out for — see
    candidates(), where they pay for moves nothing else can reach.
    """
    mine = u.state.squads.get(u.me, {})
    choosable = [j for j in u.state.jornadas if j not in u.part_played] \
        or list(u.state.jornadas)
    starts: set[str] = set()
    for j in choosable:
        starts.update(best_xi(mine, u.forecaster.expected(j)))
    return sorted(((k, u.proceeds.get(k, 0.0)) for k in mine
                   if k not in starts),
                  key=lambda kv: -kv[1])


def overdraft_fix(u) -> tuple[list[tuple[str, float]], float]:
    """([(player, proceeds)] to sell, still-short amount) to clear an
    overdraft — [], 0.0 when not overdrawn at all.

    DEAD WEIGHT ONLY, DELIBERATELY — never a real starter, matching
    candidates()'s own refusal of multi-sale funding chains. Reaches
    only into dead_weight() (proceeds with zero points cost), and
    re-checks _fieldable() on the cumulative result after every sale,
    stopping rather than pushing through an unsafe one. A shortfall dead
    weight alone can't cover is returned honestly (second element > 0),
    never covered by selling a real starter.
    """
    from decide import _fieldable

    if u.cash >= 0:
        return [], 0.0
    need = -u.cash
    mine = dict(u.state.squads.get(u.me, {}))
    picked: list[tuple[str, float]] = []
    raised = 0.0
    for k, proceeds in dead_weight(u):
        if raised >= need:
            break
        trial = {p: s for p, s in mine.items() if p != k}
        if not _fieldable(trial):
            continue
        mine = trial
        picked.append((k, proceeds))
        raised += proceeds
    return picked, max(0.0, need - raised)


def apply(u, a: Action) -> dict[str, dict[str, str]]:
    """The squads as they would be after `a`. Pure — nothing is mutated.

    TOPPED UP TO SLOT_MIN, WHOEVER THE TRADE LEAVES SHORT — not just
    mine. A raid's victim never agreed to sell and gets no pre-check;
    left short, his simulated season scores zero every remaining
    jornada (no legal XI). See phantom_topup() for how this patches it
    without inventing new forecaster data.
    """
    sq = {m: dict(s) for m, s in u.state.squads.items()}
    for gone in a.sell:
        sq[u.me].pop(gone, None)
    if a.buy:
        # A steal removes him from his owner. This is the whole point.
        for m in sq:
            sq[m].pop(a.buy, None)
        sq[u.me][a.buy] = u.pos.get(a.buy, "MED")
    return {m: phantom_topup(s) for m, s in sq.items()}


def offer_combos(u) -> list[tuple[str, Action]]:
    """The minimal combinations of real pending offers that clear an
    overdraft, as `extra` for rank() — only fires when cash is actually
    negative (the jornada won't lock overdrawn, so something must be
    accepted). MINIMAL covers only, subset-sum sense: a combo with room to
    spare when a smaller one already clears it never appears. Pure sell,
    no rebuy priced — conflating the two is what Action's own `net` keeps
    apart. Keyed "OFFERS:a|b" since no single key can stand for a combo.
    """
    import itertools

    mine = u.state.squads.get(u.me, {})
    offers = {k: v for k, v in u.received_offers.items()
             if k in mine and v > 0}
    deficit = -u.cash
    if deficit <= 0 or not offers:
        return []
    names = sorted(offers)
    covers: list[tuple[str, ...]] = []
    for r in range(1, len(names) + 1):
        for combo in itertools.combinations(names, r):
            cs = set(combo)
            if any(set(c) <= cs for c in covers):
                continue
            if sum(offers[k] for k in combo) >= deficit:
                covers.append(combo)
    return [("OFFERS:" + "|".join(combo),
            Action("sell", sell=combo,
                  proceeds=sum(offers[k] for k in combo)))
           for combo in covers]


def _selftest() -> None:
    # Lazy import, same reasoning as ffcore/par.py's own self-test: the
    # real Universe/LeagueState/Bootstrap shapes live in decide.py/
    # ffcore.season/ffcore.forecast, and importing decide.py at module
    # load time here would be the exact circular import this split
    # exists to avoid.
    from decide import Universe
    from ffcore.season import LeagueState
    from ffcore.forecast import Bootstrap

    # A real (4,4,2) plus one extra DEF and one extra DEL beyond it — real
    # legal shapes are (DEF,MED,DEL) tuples like (5,4,1)/(4,3,3)/etc (see
    # ffcore.score.formations()), not independently-satisfiable per-slot
    # minimums, so _fieldable() genuinely needs checking, not predicting
    # by hand — this test asks it, rather than hard-coding its answer.
    sq = {"k": "POR", "d1": "DEF", "d2": "DEF", "d3": "DEF", "d4": "DEF",
         "spare_d": "DEF", "m1": "MED", "m2": "MED", "m3": "MED", "m4": "MED",
         "f1": "DEL", "dead_f": "DEL"}
    per = {1: {k: (3.0, 1.0) for k in sq}}
    per[1]["f1"] = (10.0, 1.0)     # the DEL who actually starts
    per[1]["dead_f"] = (0.1, 1.0)  # never the pick, but still fieldable to sell
    u = Universe(
        state=LeagueState({"me": dict(sq)}, [1], "me"),
        forecaster=Bootstrap(per), cash=0.0, me="me",
        pos=dict(sq), proceeds={"spare_d": 4e6, "dead_f": 6e6},
        owner={}, received_offers={})

    # -- fieldable_spares()/max_spare_proceeds(): the real funding pool --
    from decide import _fieldable

    mine = u.state.squads["me"]
    spares = fieldable_spares(u)
    # Every returned spare, checked directly against _fieldable() rather
    # than hand-predicted: removing him alone must leave SOME real legal
    # shape (ffcore.score.formations() — actual (DEF,MED,DEL) tuples, not
    # independently-satisfiable per-slot minimums).
    assert spares, spares
    for s in spares:
        assert _fieldable({p: pos for p, pos in mine.items() if p != s}), s
    # The only POR can never be a spare — no real formation fields zero.
    assert "k" not in spares, spares
    # _fieldable() counts POSITIONS, not who the forecaster picks to
    # start — it doesn't know f1 (not dead_f) is the DEL who actually
    # starts, so BOTH DEL are fieldable spares even though only one ever
    # plays. dead_weight() (below) is the function that knows the
    # difference; fieldable_spares() deliberately doesn't.
    assert "f1" in spares and "dead_f" in spares, spares
    # THE DEFINITION, tying the two functions together directly — this is
    # the actual thing this split has to get right, not _fieldable()'s
    # own legality logic (already exhaustively covered in decide.py's
    # self-test).
    assert max_spare_proceeds(u) == max(
        (u.proceeds.get(s, 0.0) for s in spares), default=0.0), \
        (max_spare_proceeds(u), spares)
    assert max_spare_proceeds(u) == 6e6, max_spare_proceeds(u)  # dead_f's proceeds
    # A squad with no safely-sellable spare at all has nothing to raise.
    bare = Universe(
        state=LeagueState({"me": {"k": "POR", "d1": "DEF", "d2": "DEF",
                                  "d3": "DEF", "m1": "MED", "m2": "MED",
                                  "m3": "MED", "f1": "DEL"}}, [1], "me"),
        forecaster=Bootstrap({1: {}}), cash=0.0, me="me",
        pos={}, proceeds={}, owner={})
    assert fieldable_spares(bare) == []
    assert max_spare_proceeds(bare) == 0.0

    # -- dead_weight(): only players who start NO choosable jornada -------
    dw = dict(dead_weight(u))
    assert dw == {"dead_f": 6e6}, dw

    # -- apply(): buy tops up whoever it leaves short, sell does not add --
    after = apply(u, Action("buy", buy="new_por", sell=("spare_d",)))
    assert "spare_d" not in after["me"], after["me"]
    assert after["me"]["new_por"] == "MED", after["me"]  # u.pos has no entry -> default

    # -- offer_combos(): minimal covers only, no redundant superset --------
    u2 = Universe(
        state=LeagueState({"me": dict(sq)}, [1], "me"),
        forecaster=Bootstrap(per), cash=-9e6, me="me",
        pos=dict(sq), proceeds={}, owner={},
        received_offers={"spare_d": 4e6, "dead_f": 6e6, "f1": 20e6})
    combos = offer_combos(u2)
    labels = {c[0] for c in combos}
    # f1 alone (20M) covers the 9M deficit — any combo also containing him
    # is a strict superset and must not appear.
    assert not any("|" in lab and "f1" in lab for lab in labels), labels
    assert any(lab == "OFFERS:f1" for lab in labels), labels
    # spare_d+dead_f (10M) also covers it without f1 — kept alongside.
    assert any(set(lab.split(":")[1].split("|")) == {"spare_d", "dead_f"}
              for lab in labels if "f1" not in lab), labels
    assert offer_combos(Universe(
        state=LeagueState({"me": {}}, [1], "me"), forecaster=Bootstrap({1: {}}),
        cash=5e6, me="me")) == [], "not overdrawn -> nothing to cover"

    print("ffcore.candidates self-test OK (13 cases)")


if __name__ == "__main__":
    _selftest()
