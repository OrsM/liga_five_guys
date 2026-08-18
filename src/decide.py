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
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ffcore.forecast import Bootstrap, pool_from_perjornada  # noqa: E402
from ffcore.league import League, api_key  # noqa: E402
from ffcore.parse import fmt_money  # noqa: E402
from ffcore.score import SLOT, build  # noqa: E402
from ffcore.text import norm  # noqa: E402
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

    def label(self, names: dict[str, str] | None = None) -> str:
        """The move in words. `names` swaps join keys for readable names —
        the report needs that and the terminal does not, and the grammar of a
        move is written here so there is only one of it."""
        def show(k):
            return (names or {}).get(k, k)
        if self.kind == "sell":
            return "sell %s" % show(self.sell)
        who = "steal %s from %s" % (show(self.buy), self.victim) \
            if self.victim else "buy %s" % show(self.buy)
        return who + (" · sell %s" % show(self.sell) if self.sell else "")


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
    # Provenance, for the report to print rather than for anything to act on:
    # a round part-played and how much of it is left, and any club or player
    # the app names in a way nothing else could join.
    part_played: dict[int, set[str]] = field(default_factory=dict)
    unjoined: list[str] = field(default_factory=list)
    # The source's own spelling, for display. Never a key: the keys are what
    # every dict here is keyed by, and they have already lost their accents.
    name: dict[str, str] = field(default_factory=dict)


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


def rounds_left(matches, teams) -> tuple[list[int], dict[int, set[str]], list]:
    """(jornadas still to come, who has already played one, unjoined clubs).

    A jornada with every score in is finished and is not simulated. A jornada
    with SOME scores in is the August case, and it is the one that pays twice:
    it is still ahead, so the simulator plays it — while the app has already
    banked the played matches into the carried total. Simulating those clubs
    again credits their points a second time, and NOT equally: on the day this
    was found, four of ten J1 matches were in, and it was handing BurtonGM89
    20.3 phantom points a round against my 7.8.

    So the round stays, and the clubs inside it that are done drop out. Their
    real points are already carried; what is left of the round is what has not
    kicked off.

    `teams` is the MARKET's list of clubs and the clubs come back as
    `club_key` keys, which is what the players are keyed by too — see the note
    there about the club with two spellings. A club that will not join comes
    back in `unjoined` rather than being assumed unplayed, because assumed-
    unplayed is exactly the double count this exists to remove, wearing a
    different name.

    What it still does not model: the eleven for a round in progress is
    ALREADY LOCKED, and the simulator re-picks it from whoever is left. That
    flatters everybody by letting them field a team they can no longer field,
    for one round out of thirty-eight.
    """
    js = {r["jornada"] for r in matches if (r.get("jornada") or "").isdigit()}
    finished = {j for j in js
                if all(r.get("score") for r in matches if r["jornada"] == j)}
    rem = sorted(int(j) for j in js - finished)

    played: dict[int, set[str]] = {}
    unjoined: list[str] = []
    for r in matches:
        j = r.get("jornada") or ""
        if not j.isdigit() or int(j) not in rem or not r.get("score"):
            continue
        for side in (r.get("home"), r.get("away")):
            club = club_key(side, teams)
            if not club:
                if side and side not in unjoined:
                    unjoined.append(side)
                continue
            played.setdefault(int(j), set()).add(club)
    return rem, played, unjoined


def club_key(raw, teams) -> str:
    """One club, one key, whichever page spelled it — or "" if it will not
    place.

    Three sources name clubs three ways: the market says "Rayo", the fixture
    page "rayo-vallecano", and the probable-XI page files most players under
    the first and a handful under the second. Folding the case and the
    punctuation is not enough, because "rayo" and "rayo vallecano" are still
    two strings. So every name is resolved against the MARKET's list — the one
    spelling this repo treats as canonical everywhere else — through the same
    `match_team` the fixture board uses.

    "" for a name nothing can place, and "" is never equal to a club: an
    unplaceable club must not accidentally compare equal to another one.
    """
    from ffcore.fixture import match_team
    hit = match_team(raw or "", teams)
    return norm(hit) if hit else ""


def load(trials_pool=None) -> Universe:
    """Assemble the universe from the store. The only IO in this module."""
    lg = League.load()
    players = load_players()
    sc, _ = build(load_market(), latest_only(load_lineups()),
                  dt.datetime.now(dt.timezone.utc), lg.cfg.shrink_k)

    m = latest_only(list(csv.DictReader(open(TIDY / "matches.csv"))))
    # The market's spelling of every club, and only the market's: it is the
    # canonical side of the join in club_key().
    mkt_teams = sorted({(r.get("team") or "").strip()
                        for r in (lg.market.latest().values()
                                  if lg.market is not None else [])
                        if (r.get("team") or "").strip()})
    rem, played, unjoined_clubs = rounds_left(m, mkt_teams)

    teams = load_api_teams()
    mkt = load_api_market()
    # OWNERSHIP IS League's, NOT RE-DERIVED HERE. It has already resolved the
    # app's own spelling three ways (ffcore.league.api_key), and a second,
    # weaker join in this module is not a second opinion — it is five rival
    # players who cannot be stolen because nothing knows whose they are.
    owner = dict(lg.owner)
    me = lg.cfg.me

    squads = {mgr: {k: SLOT[(players[k].get("pos") or "").lower()]
                    for k in lg.squad(mgr)
                    if k in players
                    and (players[k].get("pos") or "").lower() in SLOT}
              for mgr in lg.managers}

    # What it costs ME. A clause is instant and cannot be refused; a market
    # row is a bid that can lose, and that difference is not priced here —
    # see the module docstring.
    #
    # Both sides join through ffcore.league.api_key, keyed on the market's
    # spelling like everything else. The clause is ON the api_teams row, so a
    # name that will not resolve is not a missing price — it is a rival's
    # player who silently cannot be bought at all.
    index = latest_only(lg.market.rows) if lg.market is not None else []
    price: dict[str, float] = {}
    for r in mkt:
        k = api_key(r["player_name"], "", lg.market, owner, index,
                    r.get("market_value"))
        if k and r.get("sale_price"):
            price[k] = float(r["sale_price"])
    for r in teams:
        if r["manager"] == me or not (r.get("buyout") or "").strip():
            continue
        k = api_key(r["player_name"], r["manager"], lg.market, owner, index,
                    r.get("market_value"))
        if k:
            price.setdefault(k, float(r["buyout"]))

    proceeds = {k: float((players[k] or {}).get("value") or 0)
                for k in squads.get(me, {})}

    pos, base, name = {}, {}, {}
    universe = set(price) | {k for s in squads.values() for k in s}
    for k in universe:
        rec = players.get(k)
        if not rec:
            continue
        name[k] = rec.get("name") or k
        pos[k] = SLOT.get((rec.get("pos") or "").lower(), "MED")
        row = sc.row_for(k)
        s = sc.score(row) if row else None
        base[k] = ((max(0.0, s.ppm * s.fix), min(1.0, (s.pct_used or 0) / 100))
                   if s else (2.0, 0.5))

    pool = pool_from_perjornada(
        csv.DictReader(open(SEASON / "live" / "perjornada_2026-27.csv")))
    # A round in progress carries only the players who have not played it yet.
    # Everyone else scored their real points hours ago and they are in
    # `carried` — see rounds_left().
    club = {k: club_key(players[k].get("team"), mkt_teams)
            for k in base if k in players}
    fc = Bootstrap({j: ({k: v for k, v in base.items()
                         if club.get(k) not in played[j]}
                        if j in played else base)
                    for j in rem}, pool=pool)

    carried = {}
    for r in teams:
        carried.setdefault(r["manager"], float(r["team_points"] or 0))
    cash = next((float(r["money"]) for r in
                 csv.DictReader(open(TIDY / "api_leagues.csv"))
                 if r.get("money")), 0.0)

    return Universe(
        state=LeagueState(squads, rem, me, carried), forecaster=fc, pos=pos,
        price=price, proceeds=proceeds, owner=owner, cash=cash, me=me,
        part_played=played, name=name,
        unjoined=list(unjoined_clubs) + list(lg.api_unjoined))


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
    # The grammar of a move is written once. A report that spelled it out
    # again to swap the keys for names would be a second place for "steal"
    # and "sell" to drift apart from each other.
    assert Action("steal", buy="x", victim="R").label({"x": "Xavi"}) \
        == "steal Xavi from R"
    assert Action("sell", sell="y").label({"y": "Yuri"}) == "sell Yuri"
    assert Action("swap", buy="x", sell="y").label({"x": "Xavi"}) \
        == "buy Xavi · sell y"

    # -- a round already half played ---------------------------------------
    # THE CASE THAT ACTUALLY EXISTS IN AUGUST, and the one that pays twice. A
    # jornada is not finished, so it is still ahead — but four of its ten
    # matches have been played, the app has already banked those points into
    # the carried total, and simulating them again credits them a second time.
    teams = ["Alavés", "Getafe", "Celta Vigo", "Osasuna", "Rayo"]
    ms = [{"jornada": "1", "home": "alaves", "away": "getafe", "score": "3-0"},
          {"jornada": "1", "home": "celta", "away": "osasuna", "score": ""},
          {"jornada": "2", "home": "alaves", "away": "celta", "score": ""},
          {"jornada": "3", "home": "alaves", "away": "getafe", "score": "1-1"},
          {"jornada": "", "home": "alaves", "away": "celta", "score": ""}]
    rem, done, unjoined = rounds_left(ms, teams)
    # J1 is still ahead (six matches to come); J3 is finished and is not.
    assert rem == [1, 2], rem
    # ...and within J1, these two clubs have nothing left to give.
    assert done == {1: {"alaves", "getafe"}}, done
    assert unjoined == [], unjoined

    # ONE CLUB, TWO SPELLINGS, and this is not hypothetical: the market calls
    # them "Rayo", the fixture page "rayo-vallecano", and the probable-XI page
    # files twenty-eight players under one and one player under the other. The
    # two sides of this join have to land on the same key or the round-in-
    # progress exclusion silently covers one player and misses the rest, which
    # is the double count back under a different name. Both sides go through
    # the MARKET's list of clubs, which is the one canonical spelling there is.
    assert club_key("rayo-vallecano", teams) == "rayo"
    assert club_key("Rayo", teams) == "rayo"
    assert club_key("celta", teams) == "celta vigo"
    # No club, or one nothing can place, is not "some club" — it is nothing,
    # and nothing is never equal to a club that has played.
    assert club_key("zzz-united", teams) == ""
    assert club_key("", teams) == ""

    # A club the fixture page spells in a way the market does not is NOT
    # silently treated as unplayed — that is the double count coming back
    # under a name nobody prints. It comes back to be reported.
    _r, _d, un = rounds_left(
        [{"jornada": "1", "home": "zzz-united", "away": "getafe",
          "score": "1-0"},
         {"jornada": "1", "home": "celta", "away": "osasuna", "score": ""}],
        teams)
    assert un == ["zzz-united"], un
    assert _d == {1: {"getafe"}}, _d

    print("decide self-test OK (28 cases)")


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
