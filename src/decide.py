"""The league a decision is made in (Universe): who holds whom, what
players will score and what they cost. The decision itself is SQL
(ffcore/decide.sql, decision.py)."""
from __future__ import annotations

from dataclasses import dataclass, field

from ffcore.action import Action
from ffcore.forecast import Bootstrap
from ffcore.market import Market
from ffcore.rules import Squad, position, shortfall
from ffcore.season import LeagueState

__all__ = ["Action", "Universe"]


@dataclass(frozen=True)
class Universe:
    """The decision: the league as it stands, what players will score and
    what they cost. Frozen: a different universe is a new one
    (dataclasses.replace)."""
    state: LeagueState
    forecaster: Bootstrap
    market: Market = field(default_factory=Market)
    cut: str = ""          # the market as seen then (schema.sql seen); none: made by hand
    moment: int = 0        # the forecast then (forecast_cell); 0: made by hand

    @property
    def me(self) -> str:
        return self.state.me

    @property
    def mine(self) -> Squad:
        return self.state.squads.get(self.me, {})

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
