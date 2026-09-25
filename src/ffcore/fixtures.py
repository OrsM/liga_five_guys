
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from decide import Universe
    from ffcore.forecast import Bootstrap
    from ffcore.profile import PlayerProfile
    from ffcore.season import LeagueState



__all__ = ["tiny_profile", "tiny_state", "tiny_bootstrap", "tiny_universe",
          "tiny_market_universe", "players_from_flat"]


DEFAULT_SQUAD: dict[str, str] = {
    "k": "POR",
    "d1": "DEF", "d2": "DEF", "d3": "DEF", "d4": "DEF",
    "m1": "MED", "m2": "MED", "m3": "MED", "m4": "MED",
    "f1": "DEL", "f2": "DEL",
}

DEFAULT_JORNADAS: list[int] = [1, 2]

_CURRENT_DEFAULTS: dict[str, object] = dict(
    club="", pos="MED", status="ok", market_value=5e6, listed=True,
    price=5e6, owner=None, value=5e6, clause=None, clause_until=None,
    route="listed", bids=0, proceeds=None,
)
_DERIVED_DEFAULTS: dict[str, object] = dict(
    ppm=5.0, pj=10.0, start_p=0.8, market_exp=4.0, pts_now=5.0, scored=None,
)


def tiny_profile(key: str, **overrides) -> "PlayerProfile":
    from ffcore.crosswalk import Player
    from ffcore.profile import PlayerCurrent, PlayerDerived, PlayerHistory
    from ffcore.profile import PlayerProfile

    name = overrides.pop("name", key)
    current_kwargs = dict(_CURRENT_DEFAULTS)
    derived_kwargs = dict(_DERIVED_DEFAULTS)
    for k, v in overrides.items():
        if k in current_kwargs:
            current_kwargs[k] = v
        elif k in derived_kwargs:
            derived_kwargs[k] = v
        else:
            raise TypeError(f"tiny_profile: unknown override {k!r}")

    return PlayerProfile(
        identity=Player(player_id=key, name=name),
        current=PlayerCurrent(**current_kwargs),
        history=PlayerHistory(),
        derived=PlayerDerived(**derived_kwargs),
    )


def players_from_flat(pos=None, price=None, proceeds=None, owner=None,
                      value=None, market_exp=None, start=None, clause=None,
                      clause_until=None, route=None, bids=None, name=None
                      ) -> dict[str, "PlayerProfile"]:
    from ffcore.crosswalk import Player
    from ffcore.profile import (PlayerCurrent, PlayerHistory,
                                PlayerDerived, PlayerProfile as _PP)
    keys = (set(pos or {}) | set(price or {}) | set(proceeds or {})
           | set(owner or {}) | set(value or {}) | set(market_exp or {})
           | set(start or {}) | set(clause or {}) | set(clause_until or {})
           | set(route or {}) | set(bids or {}) | set(name or {}))
    out = {}
    for k in keys:
        out[k] = _PP(
            identity=Player(player_id=k, name=(name or {}).get(k, k)),
            current=PlayerCurrent(
                pos=(pos or {}).get(k, ""),
                listed=k in (price or {}),
                price=(price or {}).get(k),
                proceeds=(proceeds or {}).get(k),
                owner=(owner or {}).get(k),
                value=(value or {}).get(k),
                clause=(clause or {}).get(k),
                clause_until=(clause_until or {}).get(k),
                route=(route or {}).get(k),
                bids=(bids or {}).get(k),
            ),
            history=PlayerHistory(),
            derived=PlayerDerived(
                market_exp=(market_exp or {}).get(k),
                start_p=(start or {}).get(k),
            ),
        )
    return out


def tiny_state(**overrides) -> "LeagueState":
    from ffcore.season import LeagueState

    defaults = dict(
        squads={"me": dict(DEFAULT_SQUAD)},
        jornadas=list(DEFAULT_JORNADAS),
        me="me",
        carried={},
    )
    for k in overrides:
        if k not in defaults:
            raise TypeError(f"tiny_state: unknown override {k!r}")
    defaults.update(overrides)
    return LeagueState(**defaults)


def tiny_bootstrap(**overrides) -> "Bootstrap":
    from ffcore.forecast import Bootstrap

    default_per_jornada = {
        j: {k: (5.0, 0.8) for k in DEFAULT_SQUAD} for j in DEFAULT_JORNADAS
    }
    defaults = dict(
        per_jornada=default_per_jornada,
        pool=(),
        matches=None,
        club_of=None,
        club_rel=None,
    )
    for k in overrides:
        if k not in defaults:
            raise TypeError(f"tiny_bootstrap: unknown override {k!r}")
    defaults.update(overrides)
    return Bootstrap(**defaults)


def tiny_universe(**overrides) -> "Universe":
    from decide import Universe

    defaults = dict(
        state=tiny_state(),
        forecaster=tiny_bootstrap(),
        players={k: tiny_profile(k, pos=pos)
                for k, pos in DEFAULT_SQUAD.items()},
        cash=20e6,
        me="me",
    )
    defaults.update(overrides)
    return Universe(**defaults)


def tiny_market_universe(**overrides) -> "Universe":
    from decide import Universe

    squad = dict(DEFAULT_SQUAD)
    squad["bench_m"] = "MED"
    squad["bench_k"] = "POR"

    per_jornada = {j: {k: (5.0, 0.8) for k in DEFAULT_SQUAD}
                   for j in DEFAULT_JORNADAS}
    for j in DEFAULT_JORNADAS:
        per_jornada[j]["bench_m"] = (1.0, 0.5)
        per_jornada[j]["bench_k"] = (1.0, 0.5)
        per_jornada[j]["cand_free"] = (9.0, 0.8)
        per_jornada[j]["cand_raid"] = (10.0, 0.9)

    players = {k: tiny_profile(k, pos=pos) for k, pos in DEFAULT_SQUAD.items()}
    players["bench_m"] = tiny_profile(
        "bench_m", pos="MED", listed=False, price=None,
        proceeds=3e6, pts_now=1.0, start_p=0.5, market_exp=0.5)
    players["bench_k"] = tiny_profile(
        "bench_k", pos="POR", listed=False, price=None,
        proceeds=2e6, pts_now=1.0, start_p=0.5, market_exp=0.5)
    players["cand_free"] = tiny_profile(
        "cand_free", pos="MED", price=5e6, listed=True,
        pts_now=9.0, start_p=0.8, market_exp=7.2)
    players["cand_raid"] = tiny_profile(
        "cand_raid", pos="MED", price=100e6, listed=True,
        owner="riv", route="clause", pts_now=10.0, start_p=0.9,
        market_exp=9.0)

    defaults = dict(
        state=tiny_state(squads={"me": squad}),
        forecaster=tiny_bootstrap(per_jornada=per_jornada),
        players=players,
        cash=5.5e6,
        me="me",
    )
    defaults.update(overrides)
    return Universe(**defaults)


def _selftest() -> None:
    from decide import _fieldable

    u = tiny_universe()
    assert u.state.squads["me"] == DEFAULT_SQUAD, u.state.squads
    for manager, squad in u.state.squads.items():
        assert _fieldable(squad), (manager, squad)
    assert set(u.players) == set(DEFAULT_SQUAD), u.players
    assert u.view("pos") == DEFAULT_SQUAD, u.view("pos")

    boot = tiny_bootstrap()
    for j in DEFAULT_JORNADAS:
        exp = boot.expected(j)
        assert set(exp) == set(DEFAULT_SQUAD), (j, exp)
        assert all(v > 0 for v in exp.values()), exp
    st = tiny_state()
    for j in st.jornadas:
        assert boot.expected(j), j

    for j in u.state.jornadas:
        assert u.forecaster.expected(j), j

    base = tiny_profile("x")
    changed = tiny_profile("x", pos="DEL", price=9e6)
    assert changed.current.pos == "DEL" and changed.current.price == 9e6
    assert changed.current.status == base.current.status == "ok"
    assert changed.derived.ppm == base.derived.ppm == 5.0
    assert changed.identity.name == "x" == base.identity.name

    base_u = tiny_universe()
    changed_u = tiny_universe(cash=1.0)
    assert changed_u.cash == 1.0
    assert changed_u.state.squads == base_u.state.squads
    assert set(changed_u.players) == set(base_u.players)

    base_st = tiny_state()
    changed_st = tiny_state(me="riv")
    assert changed_st.me == "riv"
    assert changed_st.squads == base_st.squads
    assert changed_st.jornadas == base_st.jornadas

    try:
        tiny_profile("x", nonsense=1)
        raise AssertionError("expected TypeError for unknown override")
    except TypeError:
        pass

    mu = tiny_market_universe()
    assert _fieldable(mu.state.squads["me"]), mu.state.squads["me"]

    dw = dict(mu.dead_weight())
    assert set(dw) == {"bench_m", "bench_k"}, dw
    assert dw["bench_m"] == 3e6 and dw["bench_k"] == 2e6, dw

    exp, _xi = mu.current_xi
    acts = mu.candidates(exp)
    assert acts, "tiny_market_universe() must produce real candidate actions"
    targets = {a.buy for a in acts if a.buy}
    assert "cand_free" in targets, targets
    assert "cand_raid" not in targets, targets

    rows, _base, _measured, _bands = mu.rank(acts, seed=1)
    assert rows, "rank() must return at least one row for a real market"
    assert any(r["action"].buy == "cand_free" for r in rows), rows

    flat_players = players_from_flat(
        pos={"a": "DEF", "b": "MED"}, price={"a": 5e6},
        owner={"b": "riv"}, name={"a": "Alpha"})
    assert set(flat_players) == {"a", "b"}, flat_players
    assert flat_players["a"].current.pos == "DEF"
    assert flat_players["a"].current.price == 5e6
    assert flat_players["a"].current.listed is True
    assert flat_players["b"].current.listed is False
    assert flat_players["b"].current.owner == "riv"
    assert flat_players["a"].identity.name == "Alpha"
    assert flat_players["b"].identity.name == "b"
    assert players_from_flat() == {}

    print("ffcore.fixtures self-test OK (24 cases)")


if __name__ == "__main__":
    _selftest()
