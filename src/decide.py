"""A league made by hand, for the self-tests (Universe): who holds whom,
what players will score and what they cost, written as the rows the league
at t is (decision.inputs). A run reads the league at t from the data
(schema.sql look, assemble.built); the decision itself is SQL
(ffcore/decide.sql, decision.py)."""
from __future__ import annotations

from dataclasses import dataclass, field

from ffcore.action import Action
from ffcore.forecast import Bootstrap
from ffcore.market import Market
from ffcore.rules import Squad
from ffcore.season import LeagueState

__all__ = ["Action", "Universe"]


@dataclass(frozen=True)
class Universe:
    """A league by hand: the squads, what players will score and what they
    cost. Frozen: a different one is a new one (dataclasses.replace)."""
    state: LeagueState
    forecaster: Bootstrap
    market: Market = field(default_factory=Market)

    @property
    def me(self) -> str:
        return self.state.me

    @property
    def mine(self) -> Squad:
        return self.state.squads.get(self.me, {})
