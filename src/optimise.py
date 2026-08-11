"""
optimise.py — decision layer for LaLiga Fantasy Oficial.

STATUS: not wired into any workflow. Nothing imports it, so it cannot break
a run. It becomes useful once expected points exist (Phase 1+).

BEFORE USING, two known gaps:
  * No coach slot. `entrenador` is a real, buyable position in this game and
    the quotas below ignore it.
  * SQUAD_QUOTA and formation bounds are assumed, not verified against the
    app's actual rules. Check them first.

Three problems, one objective:
  1. best_xi()          — which 11 to field this jornada
  2. plan()             — multi-week buy/sell plan under a budget that compounds
  3. reservation_price() — the most you should bid for a player on the market

Everything takes an `xpts` DataFrame produced upstream by the model layer.
The optimiser deliberately knows nothing about how xpts was made — swap in a
5-jornada mean as your baseline and it still works.

    uv pip install pulp pandas
"""

from __future__ import annotations

import pandas as pd
import pulp

# Formation constraints: (min, max) outfield players by position.
# Always exactly 1 GK and 11 total.
POSITION_BOUNDS = {"DEF": (3, 5), "MID": (2, 5), "FWD": (1, 3)}
SQUAD_QUOTA = {"GK": (2, 3), "DEF": (5, 8), "MID": (5, 8), "FWD": (3, 5)}


# ---------------------------------------------------------------------------
# 1. Best XI
# ---------------------------------------------------------------------------

def best_xi(squad: pd.DataFrame) -> pd.DataFrame:
    """
    squad: player_id, name, position in {GK,DEF,MID,FWD}, xpts.

    Returns the 11 that maximise expected points over all legal formations.
    Note xpts should already be P(start)-weighted — a 12-point ceiling on a
    player with a 30% chance of starting is not a 12-point pick.
    """
    prob = pulp.LpProblem("xi", pulp.LpMaximize)
    x = {p: pulp.LpVariable(f"x_{p}", cat="Binary") for p in squad.player_id}

    prob += pulp.lpSum(x[r.player_id] * r.xpts for r in squad.itertuples())
    prob += pulp.lpSum(x.values()) == 11
    prob += pulp.lpSum(x[r.player_id] for r in squad.itertuples() if r.position == "GK") == 1

    for pos, (lo, hi) in POSITION_BOUNDS.items():
        sel = pulp.lpSum(x[r.player_id] for r in squad.itertuples() if r.position == pos)
        prob += sel >= lo
        prob += sel <= hi

    prob.solve(pulp.PULP_CBC_CMD(msg=0))
    chosen = [p for p, v in x.items() if v.value() == 1]
    out = squad[squad.player_id.isin(chosen)].sort_values(
        ["position", "xpts"], ascending=[True, False]
    )
    return out


# ---------------------------------------------------------------------------
# 2. Multi-week plan
# ---------------------------------------------------------------------------

def plan(
    universe: pd.DataFrame,
    owned: set,
    cash: float,
    horizon: int = 5,
    gamma: float = 0.85,
    lambda_eur: float = 250_000.0,
    clause_tolerance: float | None = None,
) -> dict:
    """
    universe: player_id, name, position, value, xpts_1..xpts_H, xvalue_gain, available
    owned:    player_ids currently in your squad
    cash:     spendable budget
    lambda_eur: euros per expected point — the exchange rate between the two
                halves of the objective. Take it from the budget constraint's
                shadow price rather than guessing; it is the single number that
                decides whether this plays for points or for capital.
    clause_tolerance: cap on total buyout exposure, if you want one.

    Single-period formulation over an H-jornada horizon: decide the squad you
    hold now, valued by its discounted expected points plus expected capital
    gain. Re-solve daily as the market rotates — this is a rolling decision,
    not a season-long commitment.
    """
    u = universe.copy()
    pts_cols = [c for c in u.columns if c.startswith("xpts_")][:horizon]
    u["discounted"] = sum(gamma ** t * u[c].fillna(0) for t, c in enumerate(pts_cols))

    prob = pulp.LpProblem("squad", pulp.LpMaximize)
    hold = {p: pulp.LpVariable(f"h_{p}", cat="Binary") for p in u.player_id}

    def is_buy(r):
        return 0 if r.player_id in owned else 1

    prob += (
        pulp.lpSum(hold[r.player_id] * r.discounted for r in u.itertuples())
        + pulp.lpSum(
            hold[r.player_id] * r.xvalue_gain / lambda_eur for r in u.itertuples()
        )
    )

    # Budget: net spend on newly bought players cannot exceed cash plus what
    # you free up by dropping players you already own.
    spend = pulp.lpSum(
        hold[r.player_id] * r.value * is_buy(r) for r in u.itertuples()
    )
    freed = pulp.lpSum(
        (1 - hold[r.player_id]) * r.value for r in u.itertuples() if r.player_id in owned
    )
    prob += spend - freed <= cash

    for pos, (lo, hi) in SQUAD_QUOTA.items():
        sel = pulp.lpSum(hold[r.player_id] for r in u.itertuples() if r.position == pos)
        prob += sel >= lo
        prob += sel <= hi

    # You can only buy what the market is actually offering today.
    for r in u.itertuples():
        if r.player_id not in owned and not r.available:
            prob += hold[r.player_id] == 0

    if clause_tolerance is not None and "clause" in u.columns:
        prob += pulp.lpSum(
            hold[r.player_id] * r.clause for r in u.itertuples()
        ) <= clause_tolerance

    prob.solve(pulp.PULP_CBC_CMD(msg=0))
    target = {p for p, v in hold.items() if v.value() == 1}

    return {
        "buy": sorted(target - owned),
        "sell": sorted(owned - target),
        "hold": sorted(target & owned),
        "objective": pulp.value(prob.objective),
    }


# ---------------------------------------------------------------------------
# 3. Bid sizing
# ---------------------------------------------------------------------------

def reservation_price(
    marginal_points: float,
    expected_capital_gain: float,
    lambda_eur: float = 250_000.0,
) -> float:
    """
    The most a player is worth to you: the horizon points they add over the
    best alternative you could field instead, converted to euros, plus what
    you expect to make on their price while you hold them.

    Get `marginal_points` by solving plan() twice — once forcing the player in,
    once forcing them out — and taking the objective difference. That accounts
    for who they displace, which a raw xpts comparison does not.
    """
    return marginal_points * lambda_eur + expected_capital_gain


def optimal_bid(
    reservation: float,
    market_value: float,
    rival_premium: pd.Series,
    n_rivals: int = 2,
    steps: int = 40,
) -> dict:
    """
    Choose the bid maximising expected surplus (R - b) * P(win at b).

    rival_premium: empirical premiums over market value from past auctions,
    e.g. pd.Series([0.02, 0.11, 0.0, 0.07, ...]) built from the league's
    /activity history. With two rivals this becomes usable after ~10 jornadas.
    Before then, assume rivals bid market value + 0-15%.
    """
    best = {"bid": market_value, "surplus": 0.0, "p_win": 0.0}
    for i in range(steps + 1):
        bid = market_value * (1 + 0.30 * i / steps)
        if bid > reservation:
            break
        # Win if every rival's premium is below yours (independence assumed —
        # crude, but the ranking of bids is what matters, not the exact level).
        p_beat_one = float((rival_premium < (bid / market_value - 1)).mean())
        p_win = p_beat_one ** n_rivals
        surplus = (reservation - bid) * p_win
        if surplus > best["surplus"]:
            best = {"bid": round(bid), "surplus": surplus, "p_win": p_win}
    return best


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    demo = pd.DataFrame({
        "player_id": range(1, 16),
        "name": [f"P{i}" for i in range(1, 16)],
        "position": ["GK"] * 2 + ["DEF"] * 5 + ["MID"] * 5 + ["FWD"] * 3,
        "xpts": [4.1, 3.2, 5.0, 4.6, 4.4, 3.9, 2.1, 6.2, 5.8, 5.1, 4.0, 3.3, 6.9, 5.5, 2.8],
    })
    print(best_xi(demo).to_string(index=False))
