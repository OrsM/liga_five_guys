
from __future__ import annotations


def value_rate(pts, cost) -> float | None:
    if pts is None or cost is None or cost <= 0:
        return None
    return pts / (cost / 1e6)


def player_forecasts(u) -> dict[str, dict]:
    from ffcore.score import replacement as _replacement, squad_pool, vor

    jornadas = u.state.jornadas
    n_rem = len(jornadas)
    sim_season: dict[str, float] = {}
    sim_next: dict[str, float] = {}
    for i, j in enumerate(jornadas):
        exp = u.forecaster.expected(j)
        for k, pts in exp.items():
            sim_season[k] = sim_season.get(k, 0.0) + pts
        if i == 0:
            sim_next = exp

    wide_pool = squad_pool(
        {"key": k, "slot": u.view("pos").get(k, ""), "score": pts}
        for k, pts in sim_season.items() if u.view("pos").get(k))
    repl = _replacement(wide_pool, len(u.state.squads)) if u.state.squads \
        else {}

    out = {}
    for k, p in u.players.items():
        in_sim = k in sim_season
        season_pts = sim_season.get(k) if in_sim \
            else u.view("market_exp").get(k, 0.0) * n_rem
        next_pts = sim_next.get(k) if in_sim else u.view("market_exp").get(k, 0.0)
        slot = u.view("pos").get(k)
        out[k] = {
            "season_pts": season_pts,
            "next_pts": next_pts,
            "par": vor({"slot": slot, "score": season_pts}, repl),
            "pj": p.derived.pj,
            "simulated": in_sim,
        }
    return out


def _selftest() -> None:
    assert value_rate(120.0, 14.13e6) is not None
    assert abs(value_rate(120.0, 14.13e6) - 120.0 / 14.13) < 1e-9
    assert value_rate(120.0, 0.0) is None
    assert value_rate(120.0, -5e6) is None
    assert value_rate(None, 5e6) is None
    assert value_rate(0.0, 5e6) == 0.0

    from decide import Universe
    from ffcore.season import LeagueState
    from ffcore.forecast import Bootstrap
    from ffcore.profile import mk_profile

    pf_sq = {"me": {"me_a": "MED", "me_b": "MED"}}
    pf_per = {1: {"me_a": (2.0, 1.0), "me_b": (5.0, 1.0), "cand": (4.0, 1.0)},
             2: {"me_a": (2.0, 1.0), "me_b": (5.0, 1.0), "cand": (4.0, 1.0)}}
    pf_u = Universe(
        state=LeagueState(pf_sq, [1, 2], "me"),
        forecaster=Bootstrap(pf_per), cash=0.0, me="me",
        players={"me_a": mk_profile(20.0), "me_b": mk_profile(15.0),
                "cand": mk_profile(8.0),
                "unsimmed": mk_profile(1.0, "DEL", market_exp=3.0)})
    fc_out = player_forecasts(pf_u)

    assert fc_out["me_a"]["season_pts"] == 4.0, fc_out["me_a"]
    assert fc_out["me_a"]["next_pts"] == 2.0, fc_out["me_a"]
    assert fc_out["me_a"]["simulated"] is True
    assert fc_out["me_b"]["season_pts"] == 10.0, fc_out["me_b"]

    assert fc_out["cand"]["season_pts"] == 8.0, fc_out["cand"]
    assert fc_out["cand"]["par"] == 8.0 - 4.0, fc_out["cand"]
    assert fc_out["me_a"]["par"] == 0.0, fc_out["me_a"]
    assert fc_out["me_b"]["par"] == 10.0 - 4.0, fc_out["me_b"]

    assert fc_out["unsimmed"]["season_pts"] == 6.0, fc_out["unsimmed"]
    assert fc_out["unsimmed"]["next_pts"] == 3.0, fc_out["unsimmed"]
    assert fc_out["unsimmed"]["simulated"] is False
    assert fc_out["unsimmed"]["par"] == 6.0, fc_out["unsimmed"]

    assert fc_out["cand"]["pj"] == 8.0, fc_out["cand"]

    print("ffcore.par self-test OK (18 cases)")


if __name__ == "__main__":
    _selftest()
