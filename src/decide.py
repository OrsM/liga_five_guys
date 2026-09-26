
from __future__ import annotations

import datetime as dt
import itertools
import math
import sys
from dataclasses import dataclass, field, replace
from functools import cached_property
from types import MappingProxyType
from typing import Mapping


from ffcore import forecast as _forecast
from ffcore.forecast import Bootstrap, pool_from_perjornada
import methodology as _methodology
from stats import percentile
from ffcore.crosswalk import club_key, Crosswalk
from ffcore.parse import fmt_money
from ffcore.schedule import (rounds_left, next_then_rest,
                             first_jornada_per_player, apply_fixtures,
                             phantom_fill, phantom_topup)
from ffcore.pricing import locked, burn, cash_price, respond
from ffcore.action import Action
from ffcore.profile import (PlayerProfile, UNSCORED_DEFAULT,
                            build_profiles)
from ffcore.score import SLOT, _calibrated, replacement, squad_pool, vor
from ffcore.text import norm
from ffcore.season import (LeagueState, best_xi,
                           simulate_many)
from ffcore.tidy import (run_now,
                         load as load_table, load_api,
                         load_fixtures,
                         load_api_stats, load_perjornada,
                         last_api_standings,
                         load_players, market_routes, pending_sent,
                         pending_received)
from ffcore.schema import text, num, API_TEAMS, API_STANDINGS
from ffcore.schema import MARKET as MARKET_TBL

__all__ = ["Action", "Universe",
          "pending_sent", "pending_received"]

SCREEN_TRIALS = 250
FINAL_TRIALS = 3000
KEEP = 12

KEEP_RELIABLE_MIN = 6

KEEP_VALUE_MIN = 4


@dataclass
class Universe:
    state: LeagueState
    forecaster: Bootstrap
    cash: float
    me: str
    players: dict[str, PlayerProfile] = field(default_factory=dict)
    rival_cash: dict[str, float] = field(default_factory=dict)
    part_played: dict[int, set[str]] = field(default_factory=dict)
    first_jornada_of: dict[str, int] = field(default_factory=dict)
    unjoined: list[str] = field(default_factory=list)
    start_note: str = ""
    cash_note: str = ""
    locked_cash: float = 0.0
    my_bids: dict[str, float] = field(default_factory=dict)
    received_offers: dict[str, float] = field(default_factory=dict)

    @cached_property
    def current_xi(self) -> tuple[dict[str, float], set[str]]:
        if self.first_jornada_of:
            exp = self.forecaster.expected_own(self.first_jornada_of)
        else:
            j = next((j for j in self.state.jornadas
                      if j not in self.part_played),
                     self.state.jornadas[0] if self.state.jornadas else 0)
            exp = self.forecaster.expected(j)
        return exp, set(best_xi(self.state.squads.get(self.me, {}), exp))

    @cached_property
    def xi_bar(self) -> float:
        exp, xi = self.current_xi
        return min((exp.get(k, 0.0) for k in xi), default=0.0)

    def route_kind(self, k: str) -> str:
        if k in self.state.squads.get(self.me, {}):
            return "mine"
        owner = self.view("owner").get(k)
        if not owner or owner == self.me:
            return "free"
        return ("raid" if self.view("route").get(k, "market") == "clause"
                else "listed")

    def dead_weight(self) -> list[tuple[str, float]]:
        mine = self.state.squads.get(self.me, {})
        choosable = [j for j in self.state.jornadas
                     if j not in self.part_played] or list(self.state.jornadas)
        starts: set[str] = set()
        for j in choosable:
            starts.update(best_xi(mine, self.forecaster.expected(j)))
        return sorted(((k, self.view("proceeds").get(k, 0.0)) for k in mine
                       if k not in starts),
                      key=lambda kv: -kv[1])

    def candidates(self, budget: float | None = None) -> list["Action"]:
        cash = self.cash if budget is None else budget
        mine = set(self.state.squads.get(self.me, {}))
        exp, _xi = self.current_xi
        par_of = {k: v["par"] for k, v in self.player_forecasts.items()}

        def _spare_rank(k):
            vr = value_rate(par_of.get(k, 0.0),
                            self.view("proceeds").get(k, 0.0))
            return (vr is None, vr if vr is not None else 0.0)

        spare = sorted(fieldable_spares(self), key=_spare_rank)

        out: list[Action] = []
        for c, price in sorted(self.view("price").items(), key=lambda kv: kv[1]):
            if c in mine or exp.get(c, 0.0) <= self.xi_bar:
                continue
            kind_ = self.route_kind(c)
            if kind_ == "listed":
                continue
            raid = kind_ == "raid"
            victim = self.view("owner").get(c, "") if raid else ""
            kind = "clause" if raid else "buy"
            swap = kind + "-swap" if raid else "swap"
            if price <= cash:
                out.append(Action(kind, buy=c, cost=price, victim=victim))
            for s in spare:
                got = self.view("proceeds").get(s, 0.0)
                if price <= cash + got:
                    out.append(Action(swap, buy=c, sell=s, cost=price,
                                      proceeds=got, victim=victim))
        return out

    @cached_property
    def player_forecasts(self) -> dict[str, dict]:
        jornadas = self.state.jornadas
        n_rem = len(jornadas)
        sim_season: dict[str, float] = {}
        sim_next: dict[str, float] = {}
        for i, j in enumerate(jornadas):
            exp = self.forecaster.expected(j)
            for k, pts in exp.items():
                sim_season[k] = sim_season.get(k, 0.0) + pts
            if i == 0:
                sim_next = exp

        pos = self.view("pos")
        wide_pool = squad_pool(
            {"key": k, "slot": pos.get(k, ""), "score": pts}
            for k, pts in sim_season.items() if pos.get(k))
        repl = replacement(wide_pool, len(self.state.squads)) \
            if self.state.squads else {}

        out = {}
        for k, p in self.players.items():
            in_sim = k in sim_season
            market_exp = self.view("market_exp").get(k, 0.0)
            season_pts = sim_season.get(k) if in_sim else market_exp * n_rem
            out[k] = {
                "season_pts": season_pts,
                "next_pts": sim_next.get(k) if in_sim else market_exp,
                "par": vor({"slot": pos.get(k), "score": season_pts}, repl),
                "pj": p.derived.pj,
                "simulated": in_sim,
            }
        return out

    def rank(self, acts: list["Action"], seed: int = 1, price=None,
             extra: list[tuple[str, "Action"]] = ()) -> tuple:
        screen = _score_many(self, [self.state.squads]
                             + [apply(self, a) for a in acts],
                             SCREEN_TRIALS, seed)
        base_s, rest = screen[0], screen[1:]
        screened, reach = [], []
        for a, r in zip(acts, rest):
            d, _lo, _hi = band(paired(r, base_s, self.me))
            reach.append((a.cost - a.proceeds - self.cash, d))
            if a.cost <= self.cash + a.proceeds:
                screened.append((d, a))
        measured = cash_price(reach)
        lam = price if price is not None else measured

        _, cur_xi = self.current_xi
        def _touches_xi(a) -> bool:
            return any(s in cur_xi for s in a.sell)

        pick: dict[str, tuple] = {}
        for d, a in screened:
            k = a.buy or a.sell
            cur = pick.get(k)
            key = (not _touches_xi(a), d, -a.net)
            if cur is None or key > (not _touches_xi(cur[1]), cur[0], -cur[1].net):
                pick[k] = (d, a)
        screened = sorted(pick.values(), key=lambda t: (-t[0], t[1].net))

        top = screened[:KEEP]
        top = _top_up(top, screened,
                     ok=lambda d, a: self.view("route").get(a.buy, "free") != "listed",
                     rank_key=lambda t: -t[0], minimum=KEEP_RELIABLE_MIN)
        ratio = lambda t: t[0] / (t[1].net / 1e6)                    # noqa: E731
        best_value = {a.buy or a.sell for _, a in
                     sorted((t for t in screened if t[0] > 0 and t[1].net > 0),
                            key=lambda t: -ratio(t))[:KEEP_VALUE_MIN]}
        top = _top_up(top, screened,
                     ok=lambda d, a: (a.buy or a.sell) in best_value,
                     rank_key=lambda t: -ratio(t), minimum=KEEP_VALUE_MIN)
        keep = [a for _, a in top]
        bonuses = [respond(self, a, lam) for a in keep]
        afters = [apply(self, a) for a in keep]
        answered = {a.buy for a in keep if a.buy}
        rest = [(k, a) for k, a in extra if k not in answered]
        final = _score_many(self, [self.state.squads] + afters
                            + [apply(self, a) for _k, a in rest], FINAL_TRIALS, seed)
        base, scored = final[0], final[1:len(afters) + 1]
        for a, r, bonus in zip(keep, scored, bonuses):
            if bonus and a.victim in r.totals:
                r.totals[a.victim] = [x + bonus for x in r.totals[a.victim]]
        bands = {k: (*band(pairs), a, sum(pairs) / len(pairs) if pairs else 0.0)
                for (k, a), pairs in ((ka, paired(r, base, self.me)) for ka, r in
                                      zip(rest, final[len(afters) + 1:]))}
        rivals = [m for m in self.state.squads if m != self.me]
        out = []
        for a, r in zip(keep, scored):
            b_ = burn(self, a)
            charge = 0.0 if (lam is None or b_ is None) else lam * b_ / 1e6
            pairs = paired(r, base, self.me)
            d_pts, lo, hi = band(pairs)
            out.append({
                "action": a,
                "helps": (sum(1 for d in pairs if d > 0) / len(pairs)
                          if pairs else 0.0),
                "d_pts": d_pts,
                "pts_lo": lo,
                "pts_hi": hi,
                "net_pts": d_pts - charge,
                "burn": b_,
                "charge": charge,
                "answer": None,
                "d_win": r.position().get(1, 0.0) - base.position().get(1, 0.0),
                "d_beat": {v: r.beat(v) - base.beat(v) for v in rivals},
                "mean": r.mean(self.me),
                "value": value_rate(d_pts, a.net),
            })
        rows = sorted(out, key=lambda d: (-d["net_pts"], d["action"].net))
        return rows, base, measured, bands

    _FIELDS = {
        "pos": (lambda p: p.current.pos, bool, lambda v: _pos_of(v)),
        "bids": (lambda p: p.current.bids, None, None),
        "price": (lambda p: p.current.price, None, None),
        "proceeds": (lambda p: p.current.proceeds, None, None),
        "owner": (lambda p: p.current.owner, bool, None),
        "value": (lambda p: p.current.value, None, None),
        "market_exp": (lambda p: p.derived.market_exp, None, None),
        "start": (lambda p: p.derived.start_p, None, None),
        "route": (lambda p: p.current.route, bool, None),
        "name": (lambda p: p.identity.name, lambda v: True, None),
    }

    def view(self, field: str) -> Mapping:
        cache = self.__dict__.setdefault("_view_cache", {})
        if field not in cache:
            get, keep, transform = self._FIELDS[field]
            keep = keep or (lambda v: v is not None)
            transform = transform or (lambda v: v)
            cache[field] = MappingProxyType(
                {k: transform(v) for k, p in self.players.items()
                 for v in (get(p),) if keep(v)})
        return cache[field]


def _pos_of(raw: str) -> str:
    mapped = SLOT.get(raw.lower())
    if mapped:
        return mapped
    return raw if raw in ("POR", "DEF", "MED", "DEL") else "MED"


def _fieldable(squad: dict[str, str]) -> bool:
    from ffcore.score import formations
    depth: dict[str, int] = {}
    for slot in squad.values():
        depth[slot] = depth.get(slot, 0) + 1
    if depth.get("POR", 0) < 1:
        return False
    return any(depth.get("DEF", 0) >= d and depth.get("MED", 0) >= m
              and depth.get("DEL", 0) >= n for d, m, n in formations())


def _score_many(u: Universe, many: list, trials: int, seed: int):
    return simulate_many(
        [LeagueState(squads=sq, jornadas=u.state.jornadas, me=u.me,
                     carried=u.state.carried) for sq in many],
        u.forecaster, trials=trials, seed=seed, antithetic=True)


def paired(after, base, me) -> list[float]:
    return sorted(x - y for x, y in zip(after.totals.get(me, []),
                                        base.totals.get(me, [])))


def band(pairs) -> tuple[float, float, float]:
    if not pairs:
        return (0.0, 0.0, 0.0)
    return (percentile(pairs, 50), percentile(pairs, 10),
            percentile(pairs, 90))


def _top_up(top: list[tuple], screened: list[tuple], ok, rank_key,
           minimum: int) -> list[tuple]:
    kept = {a.buy or a.sell for _, a in top}
    have = sum(1 for d, a in top if ok(d, a))
    if have >= minimum:
        return top
    more = sorted((t for t in screened
                   if (t[1].buy or t[1].sell) not in kept and ok(*t)),
                  key=rank_key)
    return top + more[:minimum - have]


def value_rate(pts, cost) -> float | None:
    if pts is None or cost is None or cost <= 0:
        return None
    return pts / (cost / 1e6)


def fieldable_spares(u) -> list[str]:
    mine_squad = u.state.squads.get(u.me, {})
    return [k for k in mine_squad if _fieldable(
        {p: s for p, s in mine_squad.items() if p != k})]


def max_spare_proceeds(u) -> float:
    return max((u.view("proceeds").get(k, 0.0) for k in fieldable_spares(u)),
              default=0.0)


def overdraft_fix(u) -> tuple[list[tuple[str, float]], float]:
    if u.cash >= 0:
        return [], 0.0
    need = -u.cash
    mine = dict(u.state.squads.get(u.me, {}))
    picked: list[tuple[str, float]] = []
    raised = 0.0
    for k, proceeds in u.dead_weight():
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


VALUE_TOLERANCE = 0.90


def _clears_par_floor(par_of: dict, mae, k: str, horizon: int = 1,
                      pj_of: dict | None = None) -> bool:
    if mae is None:
        return True
    par = par_of.get(k)
    if par is None:
        return False
    from ffcore.score import SHRINK_K

    pj = pj_of.get(k) if pj_of else None
    if pj is not None:
        par = par * pj / (pj + SHRINK_K)
    return par >= mae * math.sqrt(max(1, horizon))


def _best_raid_per_victim(raid_keys, won) -> list[str]:
    best: dict[str, tuple[float, str]] = {}
    for k in raid_keys:
        r = won[k]
        victim = r["action"].victim
        score = r["d_beat"].get(victim)
        if score is None:
            score = r.get("d_pts") or 0.0
        cur = best.get(victim)
        if cur is None or score > cur[0]:
            best[victim] = (score, k)
    return [k for _score, k in best.values()]


def raid_shortlist(u, rows, par_of, mae, pj_of=None) -> set:
    raids = {r["action"].buy: r for r in rows
             if r["action"].buy
             and u.route_kind(r["action"].buy) == "raid"
             and _gains(r)
             and _clears_par_floor(par_of, mae, r["action"].buy,
                                   len(u.state.jornadas), pj_of)}
    return set(_best_raid_per_victim(list(raids), raids))


def _gains(r) -> bool:
    d = r.get("d_pts")
    return d is not None and d > 0


def worth_doing(u, rows) -> list:
    par_of = {k: v["par"] for k, v in u.player_forecasts.items()}
    pj_of = {k: v["pj"] for k, v in u.player_forecasts.items()}
    mae = _methodology.current_mae()
    rows = [r for r in rows if not r["action"].buy
            or _clears_par_floor(par_of, mae, r["action"].buy,
                                 len(u.state.jornadas), pj_of)]
    keep_raid = raid_shortlist(u, rows, par_of, mae, pj_of)
    rows = [r for r in rows
            if u.route_kind(r["action"].buy) != "raid"
            or r["action"].buy in keep_raid]
    return [r for r in rows if _gains(r)]


def best_move(u, rows, rivals):
    if not rows:
        return None, False
    reliable = [r for r in rows
               if u.view("route").get(r["action"].buy, "free") != "listed"]
    pool, uncertain = (reliable, False) if reliable else (rows, True)
    best = max(pool, key=lambda r: r["d_pts"])
    if best["action"].net <= 0:
        return best, uncertain
    floor = VALUE_TOLERANCE * best["d_pts"]
    cheaper = [r for r in pool
              if r["d_pts"] >= floor and r["action"].net < best["action"].net]
    if cheaper:
        best = min(cheaper, key=lambda r: r["action"].net)
    return best, uncertain


_LOAD_CACHE: Universe | None = None


def load(trials_pool=None) -> Universe:
    global _LOAD_CACHE
    if _LOAD_CACHE is not None:
        return _LOAD_CACHE
    from ffcore.model import session
    _m = session()
    lg, sc = _m.lg, _m.sc
    players = load_players()

    m = load_table("matches")
    mkt_teams = sorted({text(r, MARKET_TBL.TEAM)
                        for r in (lg.market.latest().values()
                                  if lg.market is not None else [])
                        if text(r, MARKET_TBL.TEAM)})
    rem, played, unjoined_clubs = rounds_left(m, mkt_teams, load_fixtures())

    teams = load_api("teams")
    mkt = load_api("market")
    owner = dict(lg.owner)
    me = lg.cfg.me

    squads = {mgr: {k: SLOT[(players[k].get("pos") or "").lower()]
                    for k in lg.squad(mgr)
                    if k in players
                    and (players[k].get("pos") or "").lower() in SLOT}
              for mgr in lg.managers}

    xw = lg.xw or Crosswalk()
    xw.attach_market(lg.market)

    def market_key(r):
        return xw.resolve(r["player_name"], ledger_owner=owner,
                          hint_price=r.get("market_value"))

    price, route, bids = market_routes(mkt, market_key)
    now = run_now()
    clause_until: dict = {}
    pt_to_key: dict[str, str] = {}
    clause: dict[str, float] = {}
    for r in teams:
        k = xw.resolve(r["player_name"], handle=r["manager"],
                       ledger_owner=owner, hint_price=r.get("market_value"))
        buyout = text(r, API_TEAMS.BUYOUT)
        if not k:
            continue
        if r.get("player_team_id"):
            pt_to_key[r["player_team_id"]] = k
        raw = text(r, API_TEAMS.BUYOUT_UNTIL)
        if raw:
            try:
                clause_until[k] = dt.datetime.fromisoformat(raw)
            except ValueError:
                pass
        if buyout:
            clause.setdefault(k, float(r["buyout"]))
        if r["manager"] == me or not buyout:
            continue
        if locked(clause_until, k, now):
            continue
        if k not in price:
            route[k] = "clause"
        price.setdefault(k, float(r["buyout"]))

    proceeds = {k: float((players[k] or {}).get("value") or 0)
                for k in squads.get(me, {})}
    received_offers = pending_received(load_api("offers"), pt_to_key)
    for k, money in received_offers.items():
        if k in proceeds:
            proceeds[k] = max(proceeds[k], money)
    rival_cash = {h: (lg[h].cash.value or 0.0) for h in lg.managers
                  if h != me}
    value = {k: float((v or {}).get("value") or 0) for k, v in players.items()
             if (v or {}).get("value")}

    universe = set(price) | {k for s in squads.values() for k in s}

    perjornada_rows = load_perjornada()
    match_stats_rows = load_api_stats()
    mk_keys = (set(price) | set(owner) | set(value) | set(clause)
              | set(clause_until) | set(route) | set(bids) | set(proceeds))
    market_keyed = {k: {"listed": k in price, "price": price.get(k),
                        "owner": owner.get(k), "value": value.get(k),
                        "clause": clause.get(k),
                        "clause_until": clause_until.get(k),
                        "route": route.get(k), "bids": bids.get(k),
                        "proceeds": proceeds.get(k)}
                    for k in mk_keys}
    profiles = build_profiles(players, sc, perjornada_rows, xw=lg.xw,
                              match_stats_rows=match_stats_rows,
                              match_rows=m,
                              market_keyed=market_keyed)

    pos = {k: _pos_of(p.current.pos) for k, p in profiles.items()}

    base, base_rest = {}, {}
    scored: dict[str, object] = {}
    for k in universe:
        p = profiles.get(k)
        scored[k] = p.derived.scored if p else None
        base[k], base_rest[k] = (p.to_bootstrap_input() if p
                                 else (UNSCORED_DEFAULT, UNSCORED_DEFAULT))

    pool = pool_from_perjornada(perjornada_rows)
    club = {k: club_key(players[k].get("team"), mkt_teams)
            for k in base if k in players}
    matches = {}
    for k in base:
        s_ = scored.get(k)
        if s_ is not None:
            matches[k] = s_.pj
    from ffcore import fixture as _fixture
    from ffcore.fixture import club_volatility, fit_home_edge, season_board
    from ffcore.tidy import load_understat_players
    slug_of = {norm(c.market): c.ff_slug for c in lg.xw.clubs.values()
              if c.market and c.ff_slug} if lg.xw is not None else {}
    club_of_slug = {k: slug_of[v] for k, v in club.items() if v in slug_of}
    results_hist = load_table("results_history")
    club_rel = club_volatility(results_hist, list(slug_of.values()))
    _fixture.HOME_EDGE, _home_edge_why = fit_home_edge(results_hist, m)
    sboard = {j: {norm(team): m for team, m in layer.items()}
             for j, layer in season_board(
                 _m.market, m, rem, now, load_table("elo"), xw=lg.xw,
                 results=results_hist,
                 understat_rows=load_understat_players("2025")).items()}
    ppm_of = {k: s.ppm for k, s in scored.items() if s}
    status_of = {k: s.status for k, s in scored.items() if s}
    first_jornada_of = first_jornada_per_player(base, rem, played, club)
    per_j = apply_fixtures(
        next_then_rest(base, base_rest, rem, played, club),
        sboard, club, pos, ppm_of, status_of=status_of,
        first_jornada_of=first_jornada_of)
    squads, per_j = phantom_fill(squads, per_j, pos)
    for _m, _sq in squads.items():
        assert _fieldable(_sq), (_m, _sq)
    if rem:
        phantom_keys = {k for layer in per_j.values() for k in layer
                        if k.startswith("__phantom_")}
        for k in phantom_keys:
            first_jornada_of.setdefault(k, rem[0])
    _history = _methodology._graded_history()
    _forecast.DRIFT_FRAC, _drift_why = \
        _methodology.drift_frac_from_history(history=_history)
    _forecast.RATE_REL_FLOOR, _rate_floor_why = \
        _methodology.fit_rate_rel_floor(pool, history=_history)
    fc = Bootstrap(per_j, pool=pool, matches=matches,
                  club_of=club_of_slug, club_rel=club_rel)

    carried = {}
    for r in last_api_standings():
        if r.get("manager"):
            carried.setdefault(r["manager"],
                              num(r, API_STANDINGS.TEAM_POINTS, default=0.0))
    raw_cash = lg[me].cash.value or 0.0
    my_bids = pending_sent(mkt, market_key)
    locked_cash = sum(my_bids.values())
    cash = raw_cash

    _LOAD_CACHE = Universe(
        state=LeagueState(squads, rem, me, carried), forecaster=fc,
        cash=cash, me=me, players=profiles,
        rival_cash=rival_cash,
        part_played=played, first_jornada_of=first_jornada_of,
        start_note=(_calibrated()[0].note() + " "
                    + _calibrated()[0].lineup_why).strip(),
        unjoined=list(unjoined_clubs) + list(lg.api_unjoined),
        locked_cash=locked_cash, my_bids=my_bids,
        received_offers=received_offers)
    return _LOAD_CACHE


def _selftest() -> None:
    from ffcore.forecast import Bootstrap as B
    from ffcore.fixtures import players_from_flat

    ph_pos = {"d1": "DEF", "d2": "DEF", "other_def": "DEF",
              "x1": "MED", "x2": "MED", "x3": "MED", "p1": "POR", "f1": "DEL"}

    thin_riv = {"d1": "DEF", "d2": "DEF", "star": "DEF",
               "x1": "MED", "x2": "MED", "x3": "MED", "p1": "POR", "f1": "DEL"}
    u_thin = Universe(
        state=LeagueState({"me": {}, "riv": dict(thin_riv)}, [1], "me"),
        forecaster=B({1: {}}), cash=0.0, me="me",
        players=players_from_flat(pos={**ph_pos, "star": "DEF"},
                                  owner={"star": "riv"}))
    raided = apply(u_thin, Action("steal", buy="star", cost=1e6,
                                  victim="riv"))
    riv_after = raided["riv"]
    assert "star" not in riv_after, riv_after
    assert _fieldable(riv_after), riv_after
    assert any(k.startswith("__phantom_DEF_") for k in riv_after), riv_after

    sq = {"k": "POR", **{f"d{i}": "DEF" for i in range(1, 5)},
          **{f"m{i}": "MED" for i in range(1, 6)}, "f1": "DEL", "bench": "MED"}
    mine = {f"me_{k}": v for k, v in sq.items()}
    theirs = {f"th_{k}": v for k, v in sq.items()}
    allk = list(mine) + list(theirs) + ["star", "dud"]
    per = {1: {k: (3.0, 1.0) for k in allk}}
    per[1]["star"] = (12.0, 1.0)
    per[1]["dud"] = (0.2, 1.0)
    per[1]["me_bench"] = (0.5, 1.0)
    per[1]["th_m1"] = (6.0, 1.0)

    u = Universe(
        state=LeagueState({"me": dict(mine), "riv": dict(theirs)}, [1], "me"),
        forecaster=B(per), cash=12e6, me="me",
        players=players_from_flat(
            pos={**{k: v for k, v in mine.items()},
                **{k: v for k, v in theirs.items()},
                "star": "MED", "dud": "MED"},
            price={"star": 10e6, "dud": 1e6, "th_m1": 5e6},
            route={"th_m1": "clause"},
            proceeds={"me_bench": 8e6}, owner={"th_m1": "riv"}))
    exp = u.forecaster.expected(1)

    first = u.current_xi
    assert u.current_xi is first, "cached_property must not recompute"
    assert u.xi_bar is u.xi_bar, "cached_property must not recompute"

    cxi_exp, cxi = u.current_xi
    fallback_j = next((j for j in u.state.jornadas if j not in u.part_played),
                      u.state.jornadas[0] if u.state.jornadas else 0)
    assert cxi_exp == u.forecaster.expected(fallback_j), cxi_exp
    assert "me_bench" not in cxi, cxi
    assert len(cxi) == 11, cxi
    bar = u.xi_bar
    assert bar == min(cxi_exp.get(k, 0.0) for k in cxi), bar
    assert bar > 0.5, bar

    acts = u.candidates()
    names = {a.buy for a in acts}
    assert "dud" not in names, names
    assert "star" in names, names

    assert overdraft_fix(u) == ([], 0.0), "not overdrawn: nothing to fix"
    u_small = replace(u, cash=-3e6)
    sells, short = overdraft_fix(u_small)
    assert sells == [("me_bench", 8e6)] and short == 0.0, (sells, short)
    u_big = replace(u, cash=-50e6)
    sells2, short2 = overdraft_fix(u_big)
    assert sells2 == [("me_bench", 8e6)], sells2
    assert short2 == 50e6 - 8e6, short2
    acts = u.candidates()
    assert any(a.kind.startswith("clause") and a.buy == "th_m1"
               for a in acts), [a.kind for a in acts]
    listed_current = replace(u.players["th_m1"].current, route="listed")
    u_listed = replace(u, players={**u.players,
                                   "th_m1": replace(u.players["th_m1"],
                                                    current=listed_current)})
    listed = u_listed.candidates()
    assert not any(a.buy == "th_m1" for a in listed), \
        [a.kind for a in listed if a.buy == "th_m1"]
    assert all(a.cost <= u.cash + a.proceeds for a in acts), acts

    a = next(x for x in acts if x.buy == "th_m1" and not x.sell)
    after = apply(u, a)
    assert "th_m1" not in after["riv"], after["riv"]
    assert "th_m1" in after["me"]
    assert "th_m1" in u.state.squads["riv"], "apply must not mutate"

    uoc = Universe(state=LeagueState({"me": {"a": "MED", "b": "MED",
                                             "c": "MED", "d": "MED"}},
                                     jornadas=[1], me="me", carried={}),
                  forecaster=None,
                  cash=-10_000_000.0, me="me",
                  received_offers={"a": 4_000_000.0, "b": 4_000_000.0,
                                   "c": 9_000_000.0, "d": 3_000_000.0})
    got = {k: a.sell for k, a in offer_combos(uoc)}
    assert set(got.values()) == {("a", "c"), ("b", "c"), ("c", "d"),
                                 ("a", "b", "d")}, got
    for a in dict(offer_combos(uoc)).values():
        assert a.buy == "" and a.kind == "sell"
    assert sum(a.proceeds for a in dict(offer_combos(uoc)).values()
              if a.sell == ("c", "d")) == 12_000_000.0
    upos = replace(uoc, cash=0.0)
    assert offer_combos(upos) == []
    uno = replace(uoc, received_offers={})
    assert offer_combos(uno) == []
    ugone = replace(uoc, received_offers={**uoc.received_offers,
                                          "gone": 50_000_000.0})
    assert "gone" not in {p for a in dict(offer_combos(ugone)).values()
                          for p in a.sell}

    u3 = Universe(
        state=LeagueState({"me": dict(mine), "riv": dict(theirs)}, [1], "me"),
        forecaster=B(per), cash=4e6, me="me",
        players=players_from_flat(
            pos={**u.view("pos"), "dear": "MED"},
            price={"dear": 20e6},
            proceeds={"me_bench": 8e6, "me_spare2": 5e6, "me_spare3": 4e6}))
    u3.state.squads["me"]["me_spare2"] = "MED"
    u3.state.squads["me"]["me_spare3"] = "POR"
    per3 = {1: dict(per[1])}
    per3[1].update({"dear": (11.0, 1.0), "me_spare2": (0.4, 1.0),
                    "me_spare3": (0.3, 1.0)})
    u3.forecaster = B(per3)
    acts3 = u3.candidates()
    assert not any(a.buy == "dear" and len(a.sell) > 1 for a in acts3), \
        [a for a in acts3 if a.buy == "dear"]
    assert not any(a.buy == "dear" for a in acts3), acts3

    sw = next(x for x in acts if x.buy == "star" and x.sell == ("me_bench",))
    af = apply(u, sw)
    assert "me_bench" not in af["me"] and "star" in af["me"]

    rows, base, _lam, _b = u.rank(acts)
    assert rows, "something should be worth doing"
    top = rows[0]
    assert 0.5 < top["helps"] <= 1.0, top["helps"]
    assert top["d_pts"] > 0, top["d_pts"]
    assert top["pts_lo"] <= top["d_pts"] <= top["pts_hi"]
    assert top["net_pts"] > 0, top
    assert [r["net_pts"] for r in rows] == sorted(
        (r["net_pts"] for r in rows), reverse=True)
    assert set(top["d_beat"]) == {"riv"}

    spend = next(r for r in rows if r["action"].net > 0)
    assert abs(spend["value"] - spend["d_pts"] / (spend["action"].net / 1e6)
              ) < 1e-9, spend
    sale = next((r for r in rows if r["action"].net <= 0), None)
    if sale is not None:
        assert sale["value"] is None, sale

    vsq = {"me_k": "POR",
           **{"me_d%d" % i: "DEF" for i in range(1, 6)},
           **{"me_m%d" % i: "MED" for i in range(1, 7)},
           "me_f1": "DEL"}
    vth = {"th_" + k[3:]: v for k, v in vsq.items()}
    vrate = {k: (5.0 if v == "MED" else 3.0) for k, v in vsq.items()}
    vrate["me_f1"] = 1.0
    vrate.update({k: 3.0 for k in vth})
    vper = {1: {k: (r, 1.0) for k, r in vrate.items()}}
    vper[1]["thin_del"] = (8.0, 1.0)
    vper[1]["deep_med"] = (8.0, 1.0)
    uvor = Universe(
        state=LeagueState({"me": dict(vsq), "riv": dict(vth)}, [1], "me"),
        forecaster=B(vper), cash=6e6, me="me",
        players=players_from_flat(
            pos={**vsq, **vth, "thin_del": "DEL", "deep_med": "MED"},
            price={"thin_del": 5e6, "deep_med": 5e6},
            route={"thin_del": "free", "deep_med": "free"}))
    vexp, vxi = uvor.current_xi
    assert uvor.xi_bar == 1.0, uvor.xi_bar
    assert min(vexp[k] for k in vxi if uvor.view("pos")[k] == "DEL") == 1.0, vxi
    assert min(vexp[k] for k in vxi if uvor.view("pos")[k] == "MED") == 5.0, vxi
    vrows, _vb, _vl, _vbd = uvor.rank([Action("buy", buy="thin_del", cost=5e6),
               Action("buy", buy="deep_med", cost=5e6)])
    vby = {r["action"].buy: r for r in vrows}
    assert vby["thin_del"]["action"].net == vby["deep_med"]["action"].net
    assert vby["thin_del"]["d_pts"] > 2 * vby["deep_med"]["d_pts"] > 0, vby
    assert vby["thin_del"]["value"] > 2 * vby["deep_med"]["value"] > 0, vby

    bsq = {"me_k": "POR", "me_d1": "DEF", "me_d2": "DEF", "me_d3": "DEF",
           "me_d4": "DEF", "me_d5": "DEF", "me_m1": "MED", "me_m2": "MED",
           "me_m3": "MED", "me_m4": "MED", "me_f1": "DEL"}
    bexp = {"me_k": 3.0, "me_d1": 3.0, "me_d2": 3.0, "me_d3": 3.0,
            "me_d4": 3.0, "me_d5": 1.0, "me_m1": 4.0, "me_m2": 4.0,
            "me_m3": 4.0, "me_m4": 4.0, "me_f1": 3.0, "cand": 2.0}
    bxi = set(best_xi(bsq, bexp))
    assert bxi == set(bsq), bxi
    assert min(bexp[k] for k in bxi) == 1.0
    assert min(bexp[k] for k in bxi if bsq[k] == "MED") == 4.0
    bsq2 = {**bsq, "cand": "MED"}
    bxi2 = set(best_xi(bsq2, bexp))
    assert "cand" in bxi2 and "me_d5" not in bxi2, bxi2
    assert sum(1 for k in bxi2 if bsq2[k] == "DEF") == 4, bxi2
    assert sum(bexp[k] for k in bxi2) - sum(bexp[k] for k in bxi) == 1.0

    per2 = {1: dict(per[1])}
    per2[1]["free_x"] = (9.0, 1.0)
    per2[1]["th_m1"] = (9.0, 1.0)
    u2 = Universe(
        state=LeagueState({"me": dict(mine), "riv": dict(theirs)}, [1], "me"),
        forecaster=B(per2), cash=6e6, me="me",
        players=players_from_flat(
            pos={**u.view("pos"), "free_x": "MED", "th_m1": "MED"},
            price={"free_x": 5e6, "th_m1": 5e6}, route={"th_m1": "clause"},
            owner={"th_m1": "riv"}))
    got, _, _, _ = u2.rank([Action("buy", buy="free_x", cost=5e6),
                          Action("clause", buy="th_m1", cost=5e6,
                                 victim="riv")])
    by = {r["action"].buy: r["net_pts"] for r in got}
    assert abs(by["th_m1"] - by["free_x"]) < 1e-9, by
    d_beat = {r["action"].buy: r["d_beat"]["riv"] for r in got}
    assert d_beat["th_m1"] > d_beat["free_x"] > 0.0, d_beat

    top_a = [(9.0, Action("buy", buy="a", cost=1e6)),
             (8.0, Action("buy", buy="b", cost=1e6))]
    screened_a = top_a + [(7.0, Action("buy", buy="c", cost=1e6)),
                          (1.0, Action("buy", buy="ok1", cost=1e6)),
                          (0.5, Action("buy", buy="ok2", cost=1e6)),
                          (0.1, Action("buy", buy="bad", cost=1e6))]
    ok = lambda d, a: a.buy in ("ok1", "ok2", "bad")             # noqa: E731
    topped = _top_up(top_a, screened_a, ok,
                     rank_key=lambda t: -t[0], minimum=2)
    keys = [a.buy for _, a in topped]
    assert keys == ["a", "b", "ok1", "ok2"], keys
    already_enough = _top_up(top_a, screened_a, lambda d, a: True,
                             rank_key=lambda t: -t[0], minimum=2)
    assert already_enough == top_a, already_enough
    dup_check = _top_up([(9.0, Action("buy", buy="a", cost=1e6))],
                        [(9.0, Action("buy", buy="a", cost=1e6)),
                         (5.0, Action("buy", buy="b", cost=1e6))],
                        lambda d, a: True, rank_key=lambda t: -t[0],
                        minimum=2)
    assert [a.buy for _, a in dup_check] == ["a", "b"], dup_check
    gain_aware = _top_up([], screened_a, lambda d, a: d > 0.5,
                         rank_key=lambda t: -t[0], minimum=10)
    assert [a.buy for _, a in gain_aware] == ["a", "b", "c", "ok1"], \
        gain_aware

    per5 = {1: dict(per[1])}
    acts5 = []
    for i in range(15):
        key = "listed%d" % i
        per5[1][key] = (10.0 - i * 0.1, 1.0)
        acts5.append(Action("buy", buy=key, cost=1e6))
    for i in range(3):
        key = "reliable%d" % i
        per5[1][key] = (2.0, 1.0)
        acts5.append(Action("buy", buy=key, cost=1e6))
    route5 = {"listed%d" % i: "listed" for i in range(15)}
    route5.update({"reliable%d" % i: "free" for i in range(3)})
    u5 = Universe(
        state=LeagueState({"me": dict(mine), "riv": dict(theirs)}, [1], "me"),
        forecaster=B(per5), cash=100e6, me="me",
        players=players_from_flat(
            pos={**u.view("pos"), **{a.buy: "MED" for a in acts5}},
            price={a.buy: 1e6 for a in acts5}, route=route5))
    rows5, *_ = u5.rank(acts5)
    kept5 = {r["action"].buy for r in rows5}
    assert all(("reliable%d" % i) in kept5 for i in range(3)), kept5
    assert len(kept5) == 15, kept5
    assert sum(1 for k in kept5 if k.startswith("listed")) == 12, kept5

    per6 = {1: dict(per[1])}
    acts6 = []
    for i in range(15):
        key = "big%d" % i
        per6[1][key] = (10.0 - i * 0.1, 1.0)
        acts6.append(Action("buy", buy=key, cost=20e6))
    for i in range(3):
        key = "eff%d" % i
        per6[1][key] = (6.0, 1.0)
        acts6.append(Action("buy", buy=key, cost=1e4))
    per6[1]["sham"] = (0.1, 1.0)
    acts6.append(Action("buy", buy="sham", cost=1e3))
    u6 = Universe(
        state=LeagueState({"me": dict(mine), "riv": dict(theirs)}, [1], "me"),
        forecaster=B(per6), cash=1000e6, me="me",
        players=players_from_flat(
            pos={**u.view("pos"), **{a.buy: "MED" for a in acts6}},
            price={a.buy: a.cost for a in acts6}))
    rows6, *_ = u6.rank(acts6)
    kept6 = {r["action"].buy for r in rows6}
    assert all(("eff%d" % i) in kept6 for i in range(3)), kept6
    assert sum(1 for k in kept6 if k.startswith("big")) == 12, kept6
    assert "sham" not in kept6, kept6

    half = Universe(
        state=LeagueState({"me": dict(mine), "riv": dict(theirs)}, [1, 2],
                          "me", ),
        forecaster=B({1: {"me_k": (0.1, 1.0), "dud": (1.0, 1.0)},
                      2: {**{k: (5.0, 1.0) for k in mine}, "dud": (1.0, 1.0)}}),
        cash=50e6, me="me",
        players=players_from_flat(pos={**u.view("pos"), "dud": "MED"},
                                  price={"dud": 1e6}))
    half.part_played = {1: {"somewhere"}}
    assert not any(a.buy == "dud"
                   for a in half.candidates())

    ok_squad = {"k": "POR", "d1": "DEF", "d2": "DEF", "d3": "DEF",
               "d4": "DEF", "m1": "MED", "m2": "MED", "m3": "MED",
               "m4": "MED", "f1": "DEL", "f2": "DEL"}
    assert _fieldable(ok_squad), ok_squad
    assert not _fieldable({k: v for k, v in ok_squad.items() if k != "k"})
    two_keepers = {k: v for k, v in ok_squad.items() if k != "f2"}
    two_keepers["k2"] = "POR"
    assert not _fieldable(two_keepers), two_keepers
    assert _fieldable({**ok_squad, "d5": "DEF", "m5": "MED"})

    per_cd = {1: {"me_k": (2.0, 1.0), "me_d1": (3.0, 1.0), "me_d2": (3.0, 1.0),
                "me_d3": (3.0, 1.0), "me_d4": (3.0, 1.0),
                "me_m1": (3.0, 1.0), "me_m2": (3.0, 1.0), "me_m3": (3.0, 1.0),
                "me_m4": (3.0, 1.0), "me_f1": (5.0, 1.0), "me_f2": (4.0, 1.0),
                "me_f3": (0.1, 1.0),
                "target": (9.0, 1.0)}}
    sq_cd = {"me_k": "POR", "me_d1": "DEF", "me_d2": "DEF", "me_d3": "DEF",
           "me_d4": "DEF", "me_m1": "MED", "me_m2": "MED", "me_m3": "MED",
           "me_m4": "MED", "me_f1": "DEL", "me_f2": "DEL", "me_f3": "DEL"}
    from ffcore.forecast import Bootstrap as BCD
    u_cd = Universe(
        state=LeagueState({"me": dict(sq_cd)}, [1], "me"),
        forecaster=BCD(per_cd), cash=0.0, me="me",
        players=players_from_flat(pos={**sq_cd, "target": "DEL"},
                                  price={"target": 5e6},
                                  proceeds={"me_f3": 5e6}))
    exp_cd = u_cd.forecaster.expected(1)
    acts_cd = u_cd.candidates()
    assert any(a.buy == "target" and a.sell == ("me_f3",) for a in acts_cd), \
        acts_cd
    assert not any(k in a.sell for a in acts_cd
                  for k in ("me_k", "me_d1", "me_d2", "me_d3", "me_d4",
                           "me_m1", "me_m2", "me_m3", "me_m4")), \
        [a for a in acts_cd if a.sell]


    from ffcore.fixtures import tiny_profile as _tiny_p

    _FALSY_DROP = ("pos", "owner", "route")
    _NONE_ONLY_DROP = ("price", "proceeds", "value", "market_exp", "start")

    falsy_edge = _tiny_p("falsy_edge", pos="", owner="", route="")
    zero_edge = _tiny_p("zero_edge", price=0.0, proceeds=0.0, value=0.0,
                       market_exp=0.0, start_p=0.0)
    none_edge = _tiny_p("none_edge", owner=None, route=None, price=None,
                       proceeds=None, value=None, market_exp=None,
                       start_p=None)
    u_views = Universe(
        state=LeagueState({"me": {}}, [1], "me"), forecaster=B({}),
        cash=0.0, me="me",
        players={"falsy_edge": falsy_edge, "zero_edge": zero_edge,
                "none_edge": none_edge})

    for field_name in _FALSY_DROP:
        view = u_views.view(field_name)
        assert "falsy_edge" not in view, (field_name, dict(view))
    for field_name in ("owner", "route"):
        view = u_views.view(field_name)
        assert "none_edge" not in view, (field_name, dict(view))

    for field_name in _NONE_ONLY_DROP:
        view = u_views.view(field_name)
        assert "zero_edge" in view and not view["zero_edge"], \
            (field_name, dict(view))
        assert "none_edge" not in view, (field_name, dict(view))

    assert u_views.view("name")["falsy_edge"] == "falsy_edge"
    assert u_views.view("name")["zero_edge"] == "zero_edge"
    assert u_views.view("name")["none_edge"] == "none_edge"

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
    mine = u.state.squads["me"]
    spares = fieldable_spares(u)
    for s in spares:
        assert _fieldable({p: pos for p, pos in mine.items() if p != s}), s
    assert "k" not in spares and "f1" in spares and "dead_f" in spares, spares
    assert max_spare_proceeds(u) == 6e6, max_spare_proceeds(u)
    bare = Universe(
        state=LeagueState({"me": {"k": "POR", "d1": "DEF", "d2": "DEF",
                                  "d3": "DEF", "m1": "MED", "m2": "MED",
                                  "m3": "MED", "f1": "DEL"}}, [1], "me"),
        forecaster=Bootstrap({1: {}}), cash=0.0, me="me")
    assert fieldable_spares(bare) == [] and max_spare_proceeds(bare) == 0.0
    assert dict(u.dead_weight()) == {"dead_f": 6e6}, u.dead_weight()
    after = apply(u, Action("buy", buy="new_por", sell=("spare_d",)))
    assert "spare_d" not in after["me"], after["me"]
    assert after["me"]["new_por"] == "MED", after["me"]

    u2 = replace(u, cash=-9e6, received_offers={
        "spare_d": 4e6, "dead_f": 6e6, "f1": 20e6})
    labels = {c[0] for c in offer_combos(u2)}
    assert not any("|" in lab and "f1" in lab for lab in labels), labels
    assert "OFFERS:f1" in labels, labels
    assert "OFFERS:dead_f|spare_d" in labels, labels
    assert offer_combos(replace(u2, cash=5e6)) == []

    for pts, cost, want in [(120.0, 14.13e6, 120.0 / 14.13), (120.0, 0.0, None),
                            (120.0, -5e6, None), (None, 5e6, None),
                            (0.0, 5e6, 0.0)]:
        got = value_rate(pts, cost)
        assert got == want or abs(got - want) < 1e-9, (pts, cost, got)

    from ffcore.profile import mk_profile
    pf_per = {j: {"me_a": (2.0, 1.0), "me_b": (5.0, 1.0), "cand": (4.0, 1.0)}
              for j in (1, 2)}
    fc = Universe(
        state=LeagueState({"me": {"me_a": "MED", "me_b": "MED"}}, [1, 2], "me"),
        forecaster=Bootstrap(pf_per), cash=0.0, me="me",
        players={"me_a": mk_profile(20.0), "me_b": mk_profile(15.0),
                 "cand": mk_profile(8.0),
                 "unsimmed": mk_profile(1.0, "DEL", market_exp=3.0)}
    ).player_forecasts
    for k, season, nxt, par, simulated in [
            ("me_a", 4.0, 2.0, 0.0, True), ("me_b", 10.0, 5.0, 6.0, True),
            ("cand", 8.0, 4.0, 4.0, True), ("unsimmed", 6.0, 3.0, 6.0, False)]:
        assert (fc[k]["season_pts"], fc[k]["next_pts"], fc[k]["par"],
                fc[k]["simulated"]) == (season, nxt, par, simulated), (k, fc[k])
    assert fc["cand"]["pj"] == 8.0, fc["cand"]

    for d_pts, want in [(0.1, True), (-2.0, False), (0.0, False), (None, False)]:
        assert _gains({"d_pts": d_pts}) is want, d_pts
    assert not _gains({})

    won_raids = {b: {"action": Action("steal", buy=b, victim=v), "d_pts": d,
                     "d_beat": {v: beat}}
                 for b, v, d, beat in [("r1", "riv", 5.0, 0.20),
                                       ("r2", "riv", 8.0, 0.05),
                                       ("r3", "riv2", 3.0, 0.10),
                                       ("x", "solo", 1.0, None)]}
    won_raids["x"]["d_beat"] = {}
    assert sorted(_best_raid_per_victim(["r1", "r2", "r3"], won_raids)) \
        == ["r1", "r3"]
    assert _best_raid_per_victim(["x"], won_raids) == ["x"]

    par_of = {"good": 5.0, "weak": 1.99, "unknown": None}
    for mae, k, want in [(None, "weak", True), (2.9, "good", True),
                         (2.9, "weak", False), (2.9, "unknown", False),
                         (2.9, "missing", False)]:
        assert _clears_par_floor(par_of, mae, k) is want, (mae, k)

    print("decide self-test OK (150 cases)")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        raise SystemExit(0)
    u = load()
    acts = u.candidates(budget=float("inf"))
    print("%d jornadas left · cash %s · %d players acquirable · %d actions"
          % (len(u.state.jornadas), fmt_money(u.cash), len(u.view("price")), len(acts)))
    print(u.forecaster.pool_note())
    rows, base, _lam, _b = u.rank(acts)
    print("\nnow: expected position %.2f · P(win) %.0f%%"
          % (base.expected_position(), 100 * base.position().get(1, 0)))
    rivals = [m for m in u.state.squads if m != u.me]
    print("\n%-52s %7s %7s %10s   %s"
          % ("do this", "net pts", "Δwin", "net €", "biggest gain vs"))
    for r in rows[:8]:
        a = r["action"]
        who = max(rivals, key=lambda v: r["d_beat"][v])
        print("%-52s %+7.1f %+6.1f%% %10s   %s %+.0f%%"
              % (a.label()[:52], r["net_pts"], 100 * r["d_win"],
                 fmt_money(-a.net), who[:16], 100 * r["d_beat"][who]))
