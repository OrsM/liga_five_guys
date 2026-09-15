"""
ffcore/par.py — a player's season forecast, and his points above the
league's own replacement level.

Split out of decide.py 2026-09-15 (rationalization plan session 4): both
functions are pure w.r.t. a Universe instance (duck-typed access to
.state/.forecaster/.pos/.players/.market_exp) — no dependency on
anything else in decide.py, so this needed no lazy import at all, unlike
ffcore/candidates.py's split.
"""

from __future__ import annotations


def value_rate(pts, cost) -> float | None:
    """Season points per million of a GENUINE positive cost, or None.

    Shared by decide.rank() (a BUY row's net spend) and sim.ladder()/
    ladder_rows() (a SAVE row's shortfall) — the same "is the price worth
    it" question, only ever answered when there is a real price: `cost`
    <= 0 means nothing to divide by (a funded sale that pays for itself,
    or a degenerate shortfall), and the raw points figure beside this one
    already says whether that is worth doing.
    """
    if pts is None or cost is None or cost <= 0:
        return None
    return pts / (cost / 1e6)


def player_forecasts(u) -> dict[str, dict]:
    """{key: {"season_pts", "next_pts", "par", "pj"}} for every player in
    u.players — the full pool, not gated on being listed (see slate.py
    for the market-restricted report view).

    TWO-TIER: u.forecaster is only simulated for the 89-player universe
    (five squads + market candidates), so season_pts sums
    forecaster.expected(j) per remaining jornada for those, and falls
    back to u.market_exp[k] held flat across the same count for
    everyone else — a cruder approximation, flagged via "simulated".

    "par" is ffcore.score.vor() against the LEAGUE's own replacement level
    (the score of the last man the league can start at that slot, pooled
    across every squad plus market candidates) — not "MY squad's weakest
    option", which answers "does he play Saturday" and goes stale the
    moment a transfer changes what my own weakest option is. Restored
    2026-09-13: this used to be exactly that squad-relative stand-in,
    which score.py's own replacement()/vor() (a fixed, league-wide
    baseline) were built to replace but got deleted with an old report
    format on 2026-08-19 and never re-wired here.
    Why: docs/notes/score.md#replacement-level--why-not-λ
    """
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

    # Pooled across every squad plus market candidates — a rival's player
    # still occupies one of the league's starting slots, which is exactly
    # what makes a position scarce.
    wide_pool = squad_pool(
        {"key": k, "slot": u.pos.get(k, ""), "score": pts}
        for k, pts in sim_season.items() if u.pos.get(k))
    repl = _replacement(wide_pool, len(u.state.squads)) if u.state.squads \
        else {}

    out = {}
    for k, p in u.players.items():
        in_sim = k in sim_season
        season_pts = sim_season.get(k) if in_sim \
            else u.market_exp.get(k, 0.0) * n_rem
        next_pts = sim_next.get(k) if in_sim else u.market_exp.get(k, 0.0)
        slot = u.pos.get(k)
        out[k] = {
            "season_pts": season_pts,
            "next_pts": next_pts,
            "par": vor({"slot": slot, "score": season_pts}, repl),
            "pj": p.derived.pj,
            "simulated": in_sim,
        }
    return out


def _selftest() -> None:
    # -- value_rate: the shared primitive, on its own -----------------------
    assert value_rate(120.0, 14.13e6) is not None
    assert abs(value_rate(120.0, 14.13e6) - 120.0 / 14.13) < 1e-9
    assert value_rate(120.0, 0.0) is None       # nothing to divide by
    assert value_rate(120.0, -5e6) is None       # a net-negative "cost" is not one
    assert value_rate(None, 5e6) is None
    assert value_rate(0.0, 5e6) == 0.0           # a real price, zero return: 0, not None

    # -- player_forecasts: full pool, two-tier, replacement-relative --------
    # Lazy imports: this fixture needs the real Universe/Bootstrap/
    # PlayerProfile shapes, and importing decide.py at module load time
    # here would be the exact circular import this split exists to avoid.
    from decide import Universe
    from ffcore.season import LeagueState
    from ffcore.forecast import Bootstrap
    from ffcore.crosswalk import Player
    from ffcore.profile import (PlayerProfile,
                                PlayerCurrent, PlayerHistory, PlayerDerived)

    def mk_profile(pj, pos="MED", market_exp=None):
        return PlayerProfile(
            identity=Player(player_id="x"), current=PlayerCurrent(pos=pos),
            history=PlayerHistory(),
            derived=PlayerDerived(pj=pj, market_exp=market_exp))

    # "me" holds two MED: me_a (weak, replacement baseline) and me_b
    # (strong). "cand" is a market candidate, simulated. "unsimmed" is a
    # full-pool-only player nobody's squad holds and nothing has listed —
    # exactly the case market_exp has to carry alone.
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

    # me_a and me_b: simulated, season = 2 jornadas summed.
    assert fc_out["me_a"]["season_pts"] == 4.0, fc_out["me_a"]
    assert fc_out["me_a"]["next_pts"] == 2.0, fc_out["me_a"]
    assert fc_out["me_a"]["simulated"] is True
    assert fc_out["me_b"]["season_pts"] == 10.0, fc_out["me_b"]

    # Replacement at MED = the WEAKER of me's two MEDs (me_a, 4.0 season
    # pts) — cand's PAR is his own season total minus that baseline.
    assert fc_out["cand"]["season_pts"] == 8.0, fc_out["cand"]
    assert fc_out["cand"]["par"] == 8.0 - 4.0, fc_out["cand"]
    # me_a against himself: PAR is 0, he IS the baseline he'd replace.
    assert fc_out["me_a"]["par"] == 0.0, fc_out["me_a"]
    # me_b's PAR uses the SAME baseline (me_a) — a squad's replacement
    # level does not move just because you are asking about its own star.
    assert fc_out["me_b"]["par"] == 10.0 - 4.0, fc_out["me_b"]

    # "unsimmed": not in u.forecaster at all — falls back to
    # market_exp * n_remaining_jornadas (3.0 * 2), flagged un-simulated.
    assert fc_out["unsimmed"]["season_pts"] == 6.0, fc_out["unsimmed"]
    assert fc_out["unsimmed"]["next_pts"] == 3.0, fc_out["unsimmed"]
    assert fc_out["unsimmed"]["simulated"] is False
    # DEL replacement: no DEL in my squad at all -> baseline 0.0, so PAR
    # is just his own season total.
    assert fc_out["unsimmed"]["par"] == 6.0, fc_out["unsimmed"]

    # pj passes straight through from derived — the existing SE-shrinkage
    # evidence count, not a new statistic.
    assert fc_out["cand"]["pj"] == 8.0, fc_out["cand"]

    print("ffcore.par self-test OK (18 cases)")


if __name__ == "__main__":
    _selftest()
