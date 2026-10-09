
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from decide import Universe
    from ffcore.forecast import Bootstrap
    from ffcore.season import LeagueState


__all__ = ["tiny_state", "tiny_bootstrap", "tiny_universe",
          "tiny_market_universe"]


DEFAULT_SQUAD: dict[str, str] = {
    "k": "POR",
    "d1": "DEF", "d2": "DEF", "d3": "DEF", "d4": "DEF",
    "m1": "MED", "m2": "MED", "m3": "MED", "m4": "MED",
    "f1": "DEL", "f2": "DEL",
}

DEFAULT_JORNADAS: list[int] = [1, 2]

def _with_overrides(name: str, defaults: dict[str, Any], overrides: dict[str, Any]
                    ) -> dict[str, Any]:
    for k in overrides:
        if k not in defaults:
            raise TypeError(f"{name}: unknown override {k!r}")
    defaults = dict(defaults)
    defaults.update(overrides)
    return defaults


def tiny_state(**overrides: Any) -> LeagueState:
    from ffcore.season import LeagueState

    defaults = dict(
        squads={"me": dict(DEFAULT_SQUAD)},
        jornadas=list(DEFAULT_JORNADAS),
        me="me",
        carried={},
    )
    return LeagueState(**_with_overrides("tiny_state", defaults, overrides))


def tiny_bootstrap(**overrides: Any) -> Bootstrap:
    from ffcore.forecast import Bootstrap

    default_per_jornada = {
        j: {k: (5.0, 0.8) for k in DEFAULT_SQUAD} for j in DEFAULT_JORNADAS
    }
    defaults = dict(
        per_jornada=default_per_jornada,
        pool=(),
    )
    return Bootstrap(**_with_overrides("tiny_bootstrap", defaults, overrides))


def _universe(defaults: dict[str, Any], market: dict[str, Any], overrides: dict[str, Any]
              ) -> Universe:
    from dataclasses import fields

    from decide import Universe
    from ffcore.market import Market

    tables = {f.name for f in fields(Market)}
    market.update({k: v for k, v in overrides.items() if k in tables})
    defaults.update({k: v for k, v in overrides.items() if k not in tables})
    return Universe(market=Market(**market), **defaults)


def tiny_universe(**overrides: Any) -> Universe:
    return _universe(dict(state=tiny_state(), forecaster=tiny_bootstrap()),
                     {"pos": dict(DEFAULT_SQUAD), "cash": 20e6}, overrides)


def tiny_market_universe(**overrides: Any) -> Universe:
    squad = dict(DEFAULT_SQUAD)
    squad["bench_m"] = "MED"
    squad["bench_k"] = "POR"

    per_jornada = {j: {k: (5.0, 0.8) for k in DEFAULT_SQUAD}
                   for j in DEFAULT_JORNADAS}
    for j in DEFAULT_JORNADAS:
        per_jornada[j]["bench_m"] = (1.0, 0.5)
        per_jornada[j]["bench_k"] = (1.0, 0.5)
        per_jornada[j]["cand_free"] = (9.0, 0.8)
        per_jornada[j]["cand_rival"] = (10.0, 0.9)

    market = {"pos": {**squad, "cand_free": "MED", "cand_rival": "MED"},
              "price": {"cand_free": 5e6, "cand_rival": 100e6},
              "value": {"bench_m": 3e6, "bench_k": 2e6},
              "owner": {"cand_rival": "riv"},
              "route": {"cand_rival": "listed"},
              "cash": 5.5e6}

    return _universe(dict(state=tiny_state(squads={"me": squad}),
                          forecaster=tiny_bootstrap(per_jornada=per_jornada)),
                     market, overrides)


def _selftest() -> None:
    from decide import _fieldable

    u = tiny_universe()
    assert u.state.squads["me"] == DEFAULT_SQUAD, u.state.squads
    for manager, squad in u.state.squads.items():
        assert _fieldable(squad), (manager, squad)
    assert u.market.pos == DEFAULT_SQUAD, u.market.pos

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

    base_u = tiny_universe()
    changed_u = tiny_universe(cash=1.0)
    assert changed_u.market.cash == 1.0
    assert changed_u.state.squads == base_u.state.squads

    base_st = tiny_state()
    changed_st = tiny_state(me="riv")
    assert changed_st.me == "riv"
    assert changed_st.squads == base_st.squads
    assert changed_st.jornadas == base_st.jornadas

    mu = tiny_market_universe()
    assert _fieldable(mu.state.squads["me"]), mu.state.squads["me"]

    exp = mu.outlook.xi.expected
    acts = mu.candidates()
    assert acts, "tiny_market_universe() must produce real candidate actions"
    targets = {a.buy for a in acts if a.buy}
    assert "cand_free" in targets, targets
    assert "cand_rival" not in targets, targets

    rows, _base = mu.rank(acts, seed=1)
    assert rows, "rank() must return at least one row for a real market"
    assert any(r.action.buy == "cand_free" for r in rows), rows

    print("ffcore.fixtures self-test OK")


if __name__ == "__main__":
    _selftest()
