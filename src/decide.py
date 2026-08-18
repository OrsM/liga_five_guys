"""
decide.py — every move you could make, ranked by whether it wins the league.

    python src/decide.py             # the table
    python src/decide.py --selftest

ONE QUESTION, ASKED OF EVERY ACTION: if I did this, how much does P(finishing
above each rival) move? Buy, sell, swap and steal are the same question with
different arguments, so there is one ranking and no verdict vocabulary.

WHAT THIS REPLACES. Points per million, value over replacement, the line, the
basket, Watch/pass/Cover/Hold, MAX_SLOT and THIN were all proxies for that
question, each with its own threshold, and twice this month two of them
contradicted each other in the same table. A simulation answers it directly.

THE STEAL IS WHY THIS MATTERS. Every rival player carries a buyout clause, so
cash can take him outright — and doing so REMOVES HIM FROM THEIR SQUAD. One
move both raises your total and lowers theirs, which is worth roughly twice
what the same player is worth from the free pool, and no per-player rate can
express it because the value depends on whose he is. 62 of the 75 players you
can buy today are somebody's.

COMMON RANDOM NUMBERS. Every option is simulated against the SAME seed, so the
seasons are identical and the difference between two options is the squads
rather than the weather. Without it a one-point edge is invisible under a
±120-point band and you would need tens of thousands of trials to see it;
with it, a few hundred will rank correctly.

WHAT IT CANNOT SEE, and each makes a hold look worse than it is:

  * Cash has option value — a better player appears next cycle — and nothing
    here models future markets, so holding cash scores zero rather than
    something. A standalone sale can therefore never look good.
  * Rivals do not respond. A steal that guts BurtonGM89 assumes he does not
    simply buy someone back.
"""

from __future__ import annotations

import csv
import datetime as dt
import os
import sys
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ffcore.forecast import Bootstrap, pool_from_perjornada  # noqa: E402
from ffcore.league import League  # noqa: E402
from ffcore.parse import fmt_money  # noqa: E402
from ffcore.score import SLOT, build  # noqa: E402
from ffcore.season import LeagueState, best_xi, simulate  # noqa: E402
from ffcore.tidy import (TIDY, SEASON, latest_only, load_api_market,  # noqa: E402
                         load_api_teams, load_lineups, load_market,
                         load_players)

__all__ = ["Action", "candidates", "rank", "Universe"]

# Screening runs at a fraction of the final trial count. With common random
# numbers the RANKING settles long before the levels do, so this buys an order
# of magnitude of speed and costs only precision on options that lose anyway.
SCREEN_TRIALS = 250
FINAL_TRIALS = 3000
KEEP = 12          # how many survive screening and get the full count


@dataclass(frozen=True)
class Action:
    """One move. `sell` is empty for a purchase out of the balance."""
    kind: str          # "buy" | "steal" | "swap" | "steal-swap" | "sell"
    buy: str = ""
    sell: str = ""
    cost: float = 0.0        # what leaves the balance
    proceeds: float = 0.0    # what a sale raises
    victim: str = ""         # the rival a steal takes from

    @property
    def net(self) -> float:
        return self.cost - self.proceeds

    def label(self) -> str:
        if self.kind == "sell":
            return "sell %s" % self.sell
        who = "steal %s from %s" % (self.buy, self.victim) if self.victim \
            else "buy %s" % self.buy
        return who + (" · sell %s" % self.sell if self.sell else "")


@dataclass
class Universe:
    """Everything the decision needs, and nothing else.

    89 players get scored, not 643: the five squads plus the free agents on
    offer. A player nobody can field and nobody can buy cannot change a
    decision today, and pretending otherwise is most of why this repo grew a
    watchlist nobody read.
    """
    state: LeagueState
    forecaster: Bootstrap
    pos: dict[str, str]
    price: dict[str, float]      # what it costs ME to acquire him
    proceeds: dict[str, float]   # what selling him raises
    owner: dict[str, str]
    cash: float
    me: str


def candidates(u: Universe, expected: dict[str, float]) -> list[Action]:
    """Every affordable move, pruned to the ones that could plausibly help.

    The prune is on EXPECTED points, not on the simulation: it is a filter for
    what to simulate, so it only has to be roughly right, and it turns
    thousands of combinations into dozens. A candidate who would not make your
    eleven on expectation will not make it on a draw either.
    """
    mine = set(u.state.squads.get(u.me, {}))
    xi = set(best_xi(u.state.squads[u.me], expected))
    # The weakest man in the current eleven is the bar a signing has to clear.
    bar = min((expected.get(k, 0.0) for k in xi), default=0.0)
    spare = sorted((k for k in mine if k not in xi),
                   key=lambda k: expected.get(k, 0.0))

    out: list[Action] = []
    for c, price in sorted(u.price.items(), key=lambda kv: kv[1]):
        if c in mine or expected.get(c, 0.0) <= bar:
            continue
        victim = u.owner.get(c, "")
        kind = "steal" if victim and victim != u.me else "buy"
        if price <= u.cash:
            out.append(Action(kind, buy=c, cost=price, victim=victim))
        # Funded by a sale: only the cheapest few spares are worth trying,
        # because selling a man you field to buy one you also field is a swap
        # the simulation will price at roughly nothing.
        for s in spare[:4]:
            got = u.proceeds.get(s, 0.0)
            if price <= u.cash + got:
                out.append(Action(kind + "-swap" if victim else "swap",
                                  buy=c, sell=s, cost=price, proceeds=got,
                                  victim=victim))
    return out


def apply(u: Universe, a: Action) -> dict[str, dict[str, str]]:
    """The squads as they would be after `a`. Pure — nothing is mutated."""
    sq = {m: dict(s) for m, s in u.state.squads.items()}
    if a.sell:
        sq[u.me].pop(a.sell, None)
    if a.buy:
        # A steal removes him from his owner. This is the whole point.
        for m in sq:
            sq[m].pop(a.buy, None)
        sq[u.me][a.buy] = u.pos.get(a.buy, "MED")
    return sq


def _score(u: Universe, squads, trials: int, seed: int):
    st = LeagueState(squads=squads, jornadas=u.state.jornadas, me=u.me,
                     carried=u.state.carried)
    return simulate(st, u.forecaster, trials=trials, seed=seed)


def rank(u: Universe, acts: list[Action], seed: int = 1) -> list[dict]:
    """Screen wide and cheap, then re-run the survivors properly.

    Returned rows carry the change in expected finishing position and in
    P(above) each rival — the second is what you act on when one rival is the
    one you are actually racing.
    """
    base_s = _score(u, u.state.squads, SCREEN_TRIALS, seed)
    screened = []
    for a in acts:
        r = _score(u, apply(u, a), SCREEN_TRIALS, seed)
        screened.append((base_s.expected_position() - r.expected_position(), a))

    # ONE ROW PER TARGET, chosen here rather than after the expensive pass.
    # Four funding variants of one signing screen identically — selling a man
    # who never makes the eleven changes nothing on the pitch, only in the
    # balance — so keeping all four wastes the final budget on duplicates and
    # crowds out genuinely different options. Ties break toward spending
    # less: same outcome, more cash left for a cycle this cannot value.
    pick: dict[str, tuple] = {}
    for d, a in screened:
        k = a.buy or a.sell
        cur = pick.get(k)
        if cur is None or (d, -a.net) > (cur[0], -cur[1].net):
            pick[k] = (d, a)
    screened = sorted(pick.values(), key=lambda t: (-t[0], t[1].net))

    base = _score(u, u.state.squads, FINAL_TRIALS, seed)
    rivals = [m for m in u.state.squads if m != u.me]
    out = []
    for _, a in screened[:KEEP]:
        r = _score(u, apply(u, a), FINAL_TRIALS, seed)
        out.append({
            "action": a,
            "d_pos": base.expected_position() - r.expected_position(),
            "d_win": r.position().get(1, 0.0) - base.position().get(1, 0.0),
            "d_beat": {v: r.beat(v) - base.beat(v) for v in rivals},
            "mean": r.mean(u.me),
        })
    rows = sorted(out, key=lambda d: (-d["d_pos"], d["action"].net))
    return rows, base


def load(trials_pool=None) -> Universe:
    """Assemble the universe from the store. The only IO in this module."""
    lg = League.load()
    players = load_players()
    sc, _ = build(load_market(), latest_only(load_lineups()),
                  dt.datetime.now(dt.timezone.utc), lg.cfg.shrink_k)

    m = latest_only(list(csv.DictReader(open(TIDY / "matches.csv"))))
    js = {r["jornada"] for r in m if r["jornada"].isdigit()}
    done = {j for j in js
            if all(r.get("score") for r in m if r["jornada"] == j)}
    rem = sorted(int(j) for j in js - done)

    teams = load_api_teams()
    mkt = load_api_market()
    owner = {r["player_name"]: r["manager"] for r in teams}
    me = lg.cfg.me

    squads = {mgr: {k: SLOT[(players[k].get("pos") or "").lower()]
                    for k in lg.squad(mgr)
                    if k in players
                    and (players[k].get("pos") or "").lower() in SLOT}
              for mgr in lg.managers}

    # What it costs ME. A clause is instant and cannot be refused; a market
    # row is a bid that can lose, and that difference is not priced here —
    # see the module docstring.
    price: dict[str, float] = {}
    for r in mkt:
        k = _key(r["player_name"], players)
        if k and r.get("sale_price"):
            price[k] = float(r["sale_price"])
    for r in teams:
        if r["manager"] == me or not (r.get("buyout") or "").strip():
            continue
        k = _key(r["player_name"], players)
        if k:
            price.setdefault(k, float(r["buyout"]))

    proceeds = {k: float((players[k] or {}).get("value") or 0)
                for k in squads.get(me, {})}

    pos, base = {}, {}
    universe = set(price) | {k for s in squads.values() for k in s}
    for k in universe:
        rec = players.get(k)
        if not rec:
            continue
        pos[k] = SLOT.get((rec.get("pos") or "").lower(), "MED")
        row = sc.row_for(k)
        s = sc.score(row) if row else None
        base[k] = ((max(0.0, s.ppm * s.fix), min(1.0, (s.pct_used or 0) / 100))
                   if s else (2.0, 0.5))

    pool = pool_from_perjornada(
        csv.DictReader(open(SEASON / "live" / "perjornada_2026-27.csv")))
    fc = Bootstrap({j: base for j in rem}, pool=pool)

    carried = {}
    for r in teams:
        carried.setdefault(r["manager"], float(r["team_points"] or 0))
    cash = next((float(r["money"]) for r in
                 csv.DictReader(open(TIDY / "api_leagues.csv"))
                 if r.get("money")), 0.0)

    return Universe(
        state=LeagueState(squads, rem, me, carried), forecaster=fc, pos=pos,
        price=price, proceeds=proceeds, owner={_key(n, players) or n: v
                                               for n, v in owner.items()},
        cash=cash, me=me)


def _key(name, players):
    from ffcore.text import norm, resolve
    k = norm(name)
    if k in players:
        return k
    rec, _ = resolve(name, list(players.values()))
    return norm(rec["name"]) if rec else None


def _selftest() -> None:
    from ffcore.forecast import Bootstrap as B

    sq = {"k": "POR", **{f"d{i}": "DEF" for i in range(1, 5)},
          **{f"m{i}": "MED" for i in range(1, 6)}, "f1": "DEL", "bench": "MED"}
    mine = {f"me_{k}": v for k, v in sq.items()}
    theirs = {f"th_{k}": v for k, v in sq.items()}
    allk = list(mine) + list(theirs) + ["star", "dud"]
    per = {1: {k: (3.0, 1.0) for k in allk}}
    per[1]["star"] = (12.0, 1.0)
    per[1]["dud"] = (0.2, 1.0)
    per[1]["me_bench"] = (0.5, 1.0)
    per[1]["th_m1"] = (6.0, 1.0)      # worth taking off a rival

    u = Universe(
        state=LeagueState({"me": dict(mine), "riv": dict(theirs)}, [1], "me"),
        forecaster=B(per), pos={**{k: v for k, v in mine.items()},
                                **{k: v for k, v in theirs.items()},
                                "star": "MED", "dud": "MED"},
        price={"star": 10e6, "dud": 1e6, "th_m1": 5e6},
        proceeds={"me_bench": 8e6}, owner={"th_m1": "riv"},
        cash=12e6, me="me")
    exp = u.forecaster.expected(1)

    acts = candidates(u, exp)
    names = {a.buy for a in acts}
    # A player worse than the weakest man you field is not a candidate.
    assert "dud" not in names, names
    assert "star" in names, names
    # A rival's player is reachable through his clause, and marked a steal.
    assert any(a.kind.startswith("steal") and a.buy == "th_m1" for a in acts)
    assert all(a.cost <= u.cash + a.proceeds for a in acts), acts

    # apply() is pure and a steal takes him OFF the rival.
    a = next(x for x in acts if x.buy == "th_m1" and not x.sell)
    after = apply(u, a)
    assert "th_m1" not in after["riv"], after["riv"]
    assert "th_m1" in after["me"]
    assert "th_m1" in u.state.squads["riv"], "apply must not mutate"

    # A swap removes the sold man and adds the bought one.
    sw = next(x for x in acts if x.buy == "star" and x.sell == "me_bench")
    af = apply(u, sw)
    assert "me_bench" not in af["me"] and "star" in af["me"]

    rows, base = rank(u, acts)
    assert rows, "something should be worth doing"
    top = rows[0]
    # Signing a 12-point player into an eleven of 3s must improve your
    # position, and the table must be sorted by that.
    assert top["d_pos"] > 0, top
    assert [r["d_pos"] for r in rows] == sorted(
        (r["d_pos"] for r in rows), reverse=True)
    assert set(top["d_beat"]) == {"riv"}

    # THE STEAL IS WORTH MORE THAN THE SAME PLAYER FROM THE POOL. Compared
    # like for like — same points, same price, same funding — taking him off a
    # rival beats buying an equivalent free agent, because it moves both
    # totals. This is the property no per-player rate can represent.
    per2 = {1: dict(per[1])}
    per2[1]["free_x"] = (9.0, 1.0)
    per2[1]["th_m1"] = (9.0, 1.0)
    u2 = Universe(
        state=LeagueState({"me": dict(mine), "riv": dict(theirs)}, [1], "me"),
        forecaster=B(per2),
        pos={**u.pos, "free_x": "MED", "th_m1": "MED"},
        price={"free_x": 5e6, "th_m1": 5e6}, proceeds={},
        owner={"th_m1": "riv"}, cash=6e6, me="me")
    got, _ = rank(u2, [Action("buy", buy="free_x", cost=5e6),
                       Action("steal", buy="th_m1", cost=5e6, victim="riv")])
    by = {r["action"].buy: r["d_pos"] for r in got}
    assert by["th_m1"] > by["free_x"], by

    assert Action("steal", buy="X", victim="R").label() == "steal X from R"
    assert Action("swap", buy="X", sell="Y").label() == "buy X · sell Y"

    print("decide self-test OK (16 cases)")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        raise SystemExit(0)
    u = load()
    exp = u.forecaster.expected(u.state.jornadas[0])
    acts = candidates(u, exp)
    print("%d jornadas left · cash %s · %d players acquirable · %d actions"
          % (len(u.state.jornadas), fmt_money(u.cash), len(u.price), len(acts)))
    print(u.forecaster.pool_note())
    rows, base = rank(u, acts)
    print("\nnow: expected position %.2f · P(win) %.0f%%"
          % (base.expected_position(), 100 * base.position().get(1, 0)))
    rivals = [m for m in u.state.squads if m != u.me]
    print("\n%-52s %6s %7s %10s   %s"
          % ("do this", "Δpos", "Δwin", "net €", "biggest gain vs"))
    for r in rows[:8]:
        a = r["action"]
        who = max(rivals, key=lambda v: r["d_beat"][v])
        print("%-52s %+6.3f %+6.1f%% %10s   %s %+.0f%%"
              % (a.label()[:52], r["d_pos"], 100 * r["d_win"],
                 fmt_money(-a.net), who[:16], 100 * r["d_beat"][who]))
