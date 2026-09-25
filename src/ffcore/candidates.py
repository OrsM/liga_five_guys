
from __future__ import annotations

from ffcore.action import Action
from ffcore.par import player_forecasts, value_rate
from ffcore.schedule import phantom_topup
from ffcore.season import best_xi


def fieldable_spares(u) -> list[str]:
    from decide import _fieldable

    mine_squad = u.state.squads.get(u.me, {})
    return [k for k in mine_squad if _fieldable(
        {p: s for p, s in mine_squad.items() if p != k})]


def max_spare_proceeds(u) -> float:
    return max((u.view("proceeds").get(k, 0.0) for k in fieldable_spares(u)),
              default=0.0)


def candidates(u, expected: dict[str, float],
               budget: float | None = None) -> list[Action]:
    from decide import current_xi, xi_bar, route_kind

    cash = u.cash if budget is None else budget
    mine_squad = u.state.squads.get(u.me, {})
    mine = set(mine_squad)
    bar_exp, xi = current_xi(u)
    if not bar_exp:
        bar_exp = expected
        xi = set(best_xi(u.state.squads[u.me], bar_exp))
    bar = xi_bar(bar_exp, xi)
    fieldable_spare = fieldable_spares(u)
    par_of = {k: v["par"] for k, v in player_forecasts(u).items()}

    def _spare_rank(k):
        vr = value_rate(par_of.get(k, 0.0), u.view("proceeds").get(k, 0.0))
        return (vr is None, vr if vr is not None else 0.0)

    spare = sorted(fieldable_spare, key=_spare_rank)

    out: list[Action] = []
    for c, price in sorted(u.view("price").items(), key=lambda kv: kv[1]):
        if c in mine or bar_exp.get(c, 0.0) <= bar:
            continue
        kind_ = route_kind(u, c)
        if kind_ == "listed":
            continue
        raid = kind_ == "raid"
        victim = u.view("owner").get(c, "") if raid else ""
        kind = "clause" if raid else "buy"
        swap = kind + "-swap" if raid else "swap"
        if price <= cash:
            out.append(Action(kind, buy=c, cost=price, victim=victim))
        for s in spare:
            got = u.view("proceeds").get(s, 0.0)
            if price <= cash + got:
                out.append(Action(swap, buy=c, sell=s, cost=price,
                                  proceeds=got, victim=victim))
    return out


def dead_weight(u) -> list[tuple[str, float]]:
    mine = u.state.squads.get(u.me, {})
    choosable = [j for j in u.state.jornadas if j not in u.part_played] \
        or list(u.state.jornadas)
    starts: set[str] = set()
    for j in choosable:
        starts.update(best_xi(mine, u.forecaster.expected(j)))
    return sorted(((k, u.view("proceeds").get(k, 0.0)) for k in mine
                   if k not in starts),
                  key=lambda kv: -kv[1])


def overdraft_fix(u) -> tuple[list[tuple[str, float]], float]:
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
    sq = {m: dict(s) for m, s in u.state.squads.items()}
    for gone in a.sell:
        sq[u.me].pop(gone, None)
    if a.buy:
        for m in sq:
            sq[m].pop(a.buy, None)
        sq[u.me][a.buy] = u.view("pos").get(a.buy, "MED")
    return {m: phantom_topup(s) for m, s in sq.items()}


def offer_combos(u) -> list[tuple[str, Action]]:
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
    from decide import Universe
    from ffcore.season import LeagueState
    from ffcore.forecast import Bootstrap
    from ffcore.fixtures import players_from_flat

    sq = {"k": "POR", "d1": "DEF", "d2": "DEF", "d3": "DEF", "d4": "DEF",
         "spare_d": "DEF", "m1": "MED", "m2": "MED", "m3": "MED", "m4": "MED",
         "f1": "DEL", "dead_f": "DEL"}
    per = {1: {k: (3.0, 1.0) for k in sq}}
    per[1]["f1"] = (10.0, 1.0)
    per[1]["dead_f"] = (0.1, 1.0)
    u = Universe(
        state=LeagueState({"me": dict(sq)}, [1], "me"),
        forecaster=Bootstrap(per), cash=0.0, me="me",
        players=players_from_flat(pos=dict(sq),
                                  proceeds={"spare_d": 4e6, "dead_f": 6e6}),
        received_offers={})

    from decide import _fieldable

    mine = u.state.squads["me"]
    spares = fieldable_spares(u)
    assert spares, spares
    for s in spares:
        assert _fieldable({p: pos for p, pos in mine.items() if p != s}), s
    assert "k" not in spares, spares
    assert "f1" in spares and "dead_f" in spares, spares
    assert max_spare_proceeds(u) == max(
        (u.view("proceeds").get(s, 0.0) for s in spares), default=0.0), \
        (max_spare_proceeds(u), spares)
    assert max_spare_proceeds(u) == 6e6, max_spare_proceeds(u)
    bare = Universe(
        state=LeagueState({"me": {"k": "POR", "d1": "DEF", "d2": "DEF",
                                  "d3": "DEF", "m1": "MED", "m2": "MED",
                                  "m3": "MED", "f1": "DEL"}}, [1], "me"),
        forecaster=Bootstrap({1: {}}), cash=0.0, me="me")
    assert fieldable_spares(bare) == []
    assert max_spare_proceeds(bare) == 0.0

    dw = dict(dead_weight(u))
    assert dw == {"dead_f": 6e6}, dw

    after = apply(u, Action("buy", buy="new_por", sell=("spare_d",)))
    assert "spare_d" not in after["me"], after["me"]
    assert after["me"]["new_por"] == "MED", after["me"]

    u2 = Universe(
        state=LeagueState({"me": dict(sq)}, [1], "me"),
        forecaster=Bootstrap(per), cash=-9e6, me="me",
        players=players_from_flat(pos=dict(sq)),
        received_offers={"spare_d": 4e6, "dead_f": 6e6, "f1": 20e6})
    combos = offer_combos(u2)
    labels = {c[0] for c in combos}
    assert not any("|" in lab and "f1" in lab for lab in labels), labels
    assert any(lab == "OFFERS:f1" for lab in labels), labels
    assert any(set(lab.split(":")[1].split("|")) == {"spare_d", "dead_f"}
              for lab in labels if "f1" not in lab), labels
    assert offer_combos(Universe(
        state=LeagueState({"me": {}}, [1], "me"), forecaster=Bootstrap({1: {}}),
        cash=5e6, me="me")) == [], "not overdrawn -> nothing to cover"

    print("ffcore.candidates self-test OK (13 cases)")


if __name__ == "__main__":
    _selftest()
