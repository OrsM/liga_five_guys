"""Sell rules: how a standing offer on one of your players is judged. A rule takes the offer (Standing) and the windows of
jornadas the board reads (Context) and gives a Verdict: the key the offers are sorted by, the points your eleven loses
over each window, and the reserve price (None when the rule states none). LIVE is the rule the board runs."""
from __future__ import annotations

import random
import sys
from collections.abc import Callable, Mapping
from typing import NamedTuple

import eleven

PER_MILLION_AHEAD = 8


class Standing(NamedTuple):
    player: str
    value: float
    offer: float | None
    later: float


class Horizon(NamedTuple):
    value: Mapping[str, float]
    settled: eleven.Settled | None


class Context(NamedTuple):
    pos: Mapping[str, str]
    horizons: Mapping[int, Horizon]
    cash: float


class Verdict(NamedTuple):
    key: float | None
    lost: Mapping[int, float]
    reserve: float | None


Rule = Callable[[Standing, Context], Verdict]


def lost_points(player: str, ctx: Context) -> Mapping[int, float]:
    """The points your eleven loses without player, for each horizon of ctx (keyed as ctx.horizons). Zero where there is
    no settled eleven; the total of the eleven where removing player leaves no legal one."""
    out: dict[int, float] = {}
    for n, h in ctx.horizons.items():
        if h.settled is None:
            out[n] = 0.0
            continue
        gone = eleven.remove(player, h.settled.eleven, h.settled.bench, ctx.pos, h.value)
        out[n] = gone.cost if gone else eleven.total(h.settled.eleven, h.value)
    return out


def points_per_million(standing: Standing, ctx: Context) -> Verdict:
    """Ranks an offer by the points lost over PER_MILLION_AHEAD per million raised: None when the offer is None or 0.
    States no reserve price."""
    lost = lost_points(standing.player, ctx)
    key = lost[PER_MILLION_AHEAD] / (standing.offer / 1e6) if standing.offer else None
    return Verdict(key, lost, None)


LIVE: Rule = points_per_million


def _selftest() -> None:
    for seed in range(40):
        for ties in (False, True):
            fielded, bench, pos, value = eleven.random_league(random.Random(seed), ties)
            squad = fielded | bench
            horizons = {n: Horizon(value, eleven.best_eleven(squad, pos, value, fielded)) for n in (4, 8, PER_MILLION_AHEAD, 0)}
            ctx = Context(pos, horizons, 0.0)
            for n, h in horizons.items():
                if h.settled is None:
                    continue
                for p in h.settled.bench:
                    assert lost_points(p, ctx)[n] == 0.0, "a bench player costs the eleven nothing"
                for p in h.settled.eleven:
                    assert lost_points(p, ctx)[n] >= -1e-9, "an eleven player never costs negative points"
            for p in squad:
                offer = 1e6 * (1 + seed)
                s = Standing(p, value[p], offer, value[p])
                v = LIVE(s, ctx)
                want = v.lost[PER_MILLION_AHEAD] / (offer / 1e6)
                assert v.key == want, "key is points lost per million"
                half = LIVE(s._replace(offer=2 * offer), ctx).key
                assert half is not None and abs(half - want / 2) < 1e-9, "key halves when the offer doubles"
                assert LIVE(s._replace(offer=None), ctx).key is None and LIVE(s._replace(offer=0.0), ctx).key is None
                assert v.reserve is None, "the baseline states no reserve"
                assert v.lost == lost_points(p, ctx)

    def stub(standing: Standing, ctx: Context) -> Verdict:
        return Verdict(1.0, {}, 2.0)
    rule: Rule = stub
    assert rule(Standing("x", 1.0, 1.0, 1.0), Context({}, {}, 0.0)) == Verdict(1.0, {}, 2.0)
    print("worth self-test OK")


if __name__ == "__main__" and "--selftest" in sys.argv:
    _selftest()
