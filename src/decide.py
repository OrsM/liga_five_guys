"""The league a decision is made in (Universe): who holds whom, what
players will score and what they cost. The decision itself is SQL
(ffcore/decide.sql, decision.py)."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace

from ffcore.action import Action
from ffcore.forecast import Bootstrap
from ffcore.market import Market
from ffcore.rules import CLAUSE_BUYS, Squad, Squads, position, shortfall
from ffcore.schedule import Factors, phantom_topup
from ffcore.season import LeagueState

__all__ = ["Action", "Universe", "apply"]


@dataclass(frozen=True)
class Universe:
    """The decision: the league as it stands, what players will score and
    what they cost. Frozen: a different universe is a new one
    (dataclasses.replace)."""
    state: LeagueState
    forecaster: Bootstrap
    market: Market = field(default_factory=Market)
    rival_cash: dict[str, float] = field(default_factory=dict)
    factors: Mapping[int, Mapping[str, Factors]] = field(default_factory=dict)
    cut: str = ""          # the market as seen then (schema.sql seen); none: made by hand
    moment: int = 0        # the forecast then (forecast_cell); 0: made by hand

    @property
    def me(self) -> str:
        return self.state.me

    @property
    def mine(self) -> Squad:
        return self.state.squads.get(self.me, {})

    def acquire(self, k: str) -> Action | None:
        """How you could get him: a bid for a player on the market, at his
        asking price (the decision chooses the bid). Never a rival's player by his
        clause (rules.CLAUSE_BUYS)."""
        m = self.market
        if k in m.owner:
            if CLAUSE_BUYS:
                raise NotImplementedError("rules.CLAUSE_BUYS: the plan has no clause buy yet")
            return None
        if k not in m.price:
            return None
        return Action("buy", buy=k, cost=m.price[k])

    def after(self, *acts: Action) -> Universe:
        """The league once these moves are made, your cash with it."""
        return replace(self, state=replace(self.state, squads=apply(self, *acts)),
                       market=replace(self.market, cash=self.market.left(acts)))

    def squad_after(self, *acts: Action) -> Squad:
        sq = dict(self.mine)
        for a in acts:
            for k in a.sell:
                sq.pop(k, None)
            if a.buy:
                sq[a.buy] = position(self.market.pos.get(a.buy))
        return sq


def _fieldable(squad: Squad) -> bool:
    return not shortfall(squad)


def apply(u: Universe, *acts: Action) -> Squads:
    sq = {m: dict(s) for m, s in u.state.squads.items()}
    sq[u.me] = u.squad_after(*acts)
    return {m: phantom_topup(s) for m, s in sq.items()}
