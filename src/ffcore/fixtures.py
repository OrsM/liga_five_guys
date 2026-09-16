"""
ffcore/fixtures.py — TEST-ONLY builders for the three structures every
self-test in this repo eventually needs: a Universe, its LeagueState, its
Bootstrap, and the PlayerProfiles that back them.

DO NOT IMPORT THIS FROM PRODUCTION CODE. Nothing in decide.py, sim.py,
slate.py or any ffcore/*.py module (outside its own `_selftest`) may
depend on this file — it exists only so `_selftest` bodies stop
hand-building the same three dataclasses from scratch.

WHY THIS EXISTS. `Universe` is built by hand 25 times across 6 files,
`LeagueState` 29 times, `Bootstrap` 51 times (counted 2026-09-16,
docs/notes/rationalization-2026-09-16.md) — each call site re-deriving
the same shapes, and each shape change meaning ~105 edits somewhere in
`src/`. That fixture cost is what has been blocking the player-record
collapse a later wave wants to do; this file is the fix, not the
migration — existing `_selftest` bodies still build their own fixtures
by hand until a later wave moves them onto these builders.

EVERY BUILDER TAKES KEYWORD OVERRIDES. Defaults are chosen to be
*legal and usable*, not just non-crashing: `tiny_universe()` with no
arguments produces a squad `decide._fieldable()` actually accepts — a
real formation from `ffcore.score.formations()`, not merely enough
players to clear each position's SLOT_MIN in isolation (that clears the
count and still fields nothing; see decide._fieldable()'s own
docstring). A caller overrides exactly the field it's testing and
inherits sane defaults for everything else, the same shape par.py's
`_selftest` already leans on (its `mk_profile()` helper is the same
idea, one level less general).

An override tiny_profile()/tiny_state()/tiny_bootstrap() do not
recognise raises TypeError rather than being silently dropped — a typo
in a test fixture's override is a worse failure mode quiet than loud,
and this is the one place to catch it before it produces a fixture that
silently isn't testing what its author thinks it is. Do not "fix" this
into a lenient **kwargs pass-through.

TWO UNIVERSE BUILDERS, TWO DIFFERENT JOBS. `tiny_universe()` is the
minimal-legal floor — exactly 11 players, zero spares, nothing priced
to buy — and stays that way; most tests want the honest minimum, not a
populated market. `tiny_market_universe()` is what a test reaches for
when it actually needs decide.py's market machinery (dead_weight(),
candidates(), rank()) to have something real to chew on: it starts from
tiny_universe()'s own squad and adds bench spares plus priced
candidates, one affordable and one deliberately not. Don't collapse the
two into one "does everything" builder — the whole point of
tiny_universe() is that it can't.

LAZY IMPORTS THROUGHOUT, ON PURPOSE. `decide.py` imports from
`ffcore.*` at module load time, so a module-level `from decide import
Universe` here would risk exactly the circular import `ffcore/par.py`'s
own `_selftest` already dodges by importing `decide.Universe`,
`ffcore.season.LeagueState` and `ffcore.forecast.Bootstrap` lazily,
inside the function body. Followed here rather than re-litigated.
"""

from __future__ import annotations

import os
import sys

# Run directly (`python src/ffcore/fixtures.py`), sys.path[0] is
# src/ffcore/ itself, so the lazy `from decide import ...` below can't
# find `decide` — same problem ffcore/model.py and ffcore/fixture.py
# already solved; same fix, not a new one.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

__all__ = ["tiny_profile", "tiny_state", "tiny_bootstrap", "tiny_universe",
          "tiny_market_universe", "players_from_flat"]


# The one squad shape every default in this module agrees on: exactly
# (4, 4, 2) — one of ffcore.score.FREE_FORMATIONS' 7 real shapes — with
# no spare beyond it. 11 players is the minimum that can be fieldable at
# all (every real formation sums to 11), so this is the smallest squad
# `decide._fieldable()` will accept, not an arbitrary round number.
# Naming follows the k/d/m/f convention ffcore/candidates.py's and
# sim.py's own hand-built squads already use.
DEFAULT_SQUAD: dict[str, str] = {
    "k": "POR",
    "d1": "DEF", "d2": "DEF", "d3": "DEF", "d4": "DEF",
    "m1": "MED", "m2": "MED", "m3": "MED", "m4": "MED",
    "f1": "DEL", "f2": "DEL",
}

# The jornadas DEFAULT_SQUAD's Bootstrap has data for, and LeagueState's
# own default "still to come" list — kept as one constant so the two
# builders can't quietly drift apart.
DEFAULT_JORNADAS: list[int] = [1, 2]

# PlayerCurrent/PlayerDerived fields tiny_profile() will accept as a
# keyword override, and what each defaults to. Split by which dataclass
# they land on; "name" is handled separately since it lives on the
# crosswalk Player, not on either of these.
_CURRENT_DEFAULTS: dict[str, object] = dict(
    club="", pos="MED", status="ok", market_value=5e6, listed=True,
    price=5e6, owner=None, value=5e6, clause=None, clause_until=None,
    route="listed", bids=0, proceeds=None,
)
_DERIVED_DEFAULTS: dict[str, object] = dict(
    ppm=5.0, pj=10.0, start_p=0.8, market_exp=4.0, pts_now=5.0, scored=None,
)


def tiny_profile(key: str, **overrides) -> "PlayerProfile":
    """One PlayerProfile, fully populated with legal-looking defaults.

    `key` becomes both the crosswalk identity's player_id and (absent a
    `name=` override) his display name. Every PlayerCurrent/PlayerDerived
    field (see ffcore/profile.py) can be overridden by keyword; anything
    else raises rather than silently landing nowhere.
    """
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
    """One PlayerProfile per key across the given per-field flat dicts —
    a bridge for the many hand-built `Universe(pos={...}, price={...},
    ...)` fixtures across this repo's `_selftest`s onto the `players=`
    constructor path.

    WAVE 3F: this is decide.py's own `_synthetic_profiles()`, moved here
    verbatim (same field-by-field construction, same defaults) rather
    than reimplemented, when `Universe.__post_init__` and its 12
    `InitVar` flat-dict params were deleted — every fixture that used to
    lean on that constructor-time synthesis for convenience now calls
    this explicitly and passes `players=players_from_flat(...)` instead.

    NOT `tiny_profile()`. `tiny_profile()` builds ONE fully-populated,
    legal-looking player from keyword overrides; this builds MANY
    sparse ones straight from parallel per-field dicts, matching
    whatever subset of fields the caller happens to have on hand (most
    fields default to "" / None / 0, not tiny_profile()'s populated
    defaults) — the shape a fixture ported from the old flat-dict
    constructor actually has.
    """
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
    """A one-manager LeagueState fielding DEFAULT_SQUAD — legal on its
    own (ffcore/candidates.py's and ffcore/pricing.py's own selftests
    both build single-squad states the same way; a second rival squad is
    not required for a LeagueState to be usable).
    """
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
    """A Bootstrap that can answer .expected(j) for every jornada in
    tiny_state()'s own default list, for every key in DEFAULT_SQUAD.

    `pool` defaults to empty, same as most hand-built Bootstrap fixtures
    in this repo (e.g. ffcore/par.py's `Bootstrap(pf_per)`) — Bootstrap
    itself falls back to SEED_POOL below MIN_POOL real observations, so
    an empty pool is not a degenerate case, just the "no real match
    history on hand" one.
    """
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
    """A Universe assembled from this module's other three builders.

    Defaults to a single "me" squad in DEFAULT_SQUAD's exact (4, 4, 2)
    shape, with a real PlayerProfile per player — `decide._fieldable()`
    accepts it because that shape IS one of ffcore.score.formations()'s
    7 legal tuples, not because every position's minimum happens to be
    cleared (see decide._fieldable()'s own docstring for why that
    distinction is the whole check).
    """
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
    """`tiny_universe()` plus dead weight and a real market — the case
    `tiny_universe()` deliberately does NOT cover.

    `tiny_universe()`'s squad is the minimal-legal floor: exactly 11
    players, zero spares, nobody to sell and nothing priced to buy. Fed
    to `decide.dead_weight()`/`candidates()`/`rank()` it returns nothing
    at all, which means it cannot exercise selling, funding, buying or
    raiding — most of what decide.py does. This builder starts from
    `tiny_universe()`'s own squad/state/forecaster and adds:

      * "bench_m" (MED) and "bench_k" (POR) — two spares beyond the
        legal eleven, each given a low forecaster rate so `best_xi()`
        never selects them in ANY formation, in EITHER jornada — the
        exact shape `dead_weight()` looks for, with real proceeds behind
        each so a sale has something to show for it.
      * "cand_free" — unowned, listed, priced within `tiny_universe()`'s
        default cash and scored well above the default squad's own bar,
        so `candidates()` proposes a real buy for him (and swap variants
        funded by each bench spare).
      * "cand_raid" — owned by "riv" on a clause route, priced past what
        cash-plus-any-single-spare-sale could ever cover. `candidates()`
        prunes him entirely rather than proposing an unaffordable move —
        the OTHER branch of the same affordability gate "cand_free"
        exercises, on purpose.
    """
    from decide import Universe

    squad = dict(DEFAULT_SQUAD)
    squad["bench_m"] = "MED"
    squad["bench_k"] = "POR"

    per_jornada = {j: {k: (5.0, 0.8) for k in DEFAULT_SQUAD}
                   for j in DEFAULT_JORNADAS}
    for j in DEFAULT_JORNADAS:
        # Low points AND low P(start): never worth best_xi() picking
        # over a full-rate starter, in any legal shape.
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
    # -- tiny_universe(): the legality guarantee this whole file exists for
    from decide import _fieldable

    u = tiny_universe()
    assert u.state.squads["me"] == DEFAULT_SQUAD, u.state.squads
    for manager, squad in u.state.squads.items():
        assert _fieldable(squad), (manager, squad)
    # Every squad member has a matching PlayerProfile, positioned the
    # same way LeagueState itself says he is.
    assert set(u.players) == set(DEFAULT_SQUAD), u.players
    assert u.pos_view == DEFAULT_SQUAD, u.pos_view

    # -- tiny_bootstrap(): can answer every jornada tiny_state() promises
    boot = tiny_bootstrap()
    for j in DEFAULT_JORNADAS:
        exp = boot.expected(j)
        assert set(exp) == set(DEFAULT_SQUAD), (j, exp)
        assert all(v > 0 for v in exp.values()), exp
    # tiny_bootstrap() answers its OWN default state's jornadas too —
    # the two builders' defaults agree without either importing the other.
    st = tiny_state()
    for j in st.jornadas:
        assert boot.expected(j), j

    # tiny_universe()'s own forecaster/state pairing, same check.
    for j in u.state.jornadas:
        assert u.forecaster.expected(j), j

    # -- overrides: change one field, everything else keeps its default --
    base = tiny_profile("x")
    changed = tiny_profile("x", pos="DEL", price=9e6)
    assert changed.current.pos == "DEL" and changed.current.price == 9e6
    # Untouched fields: identical to the unmodified default build.
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

    # An unknown override is a mistake worth catching, not silently
    # dropped into nowhere.
    try:
        tiny_profile("x", nonsense=1)
        raise AssertionError("expected TypeError for unknown override")
    except TypeError:
        pass

    # -- tiny_market_universe(): the case tiny_universe() can't exercise --
    mu = tiny_market_universe()
    assert _fieldable(mu.state.squads["me"]), mu.state.squads["me"]

    # dead_weight(): the two bench spares, and only them — never a core
    # starter, regardless of jornada.
    dw = dict(mu.dead_weight())
    assert set(dw) == {"bench_m", "bench_k"}, dw
    assert dw["bench_m"] == 3e6 and dw["bench_k"] == 2e6, dw

    # candidates(): a real, affordable buy proposed for cand_free; the
    # far-too-expensive cand_raid pruned outright, not merely ranked last.
    exp, _xi = mu.current_xi
    acts = mu.candidates(exp)
    assert acts, "tiny_market_universe() must produce real candidate actions"
    targets = {a.buy for a in acts if a.buy}
    assert "cand_free" in targets, targets
    assert "cand_raid" not in targets, targets

    # rank(): the actual simulation, run for real, returns a real row.
    rows, _base, _measured, _bands = mu.rank(acts, seed=1)
    assert rows, "rank() must return at least one row for a real market"
    assert any(r["action"].buy == "cand_free" for r in rows), rows

    # -- players_from_flat(): the bridge Wave 3F's own hand-built
    # `Universe(pos={...}, price={...}, ...)` fixtures now go through,
    # in place of the constructor-time synthesis `Universe.__post_init__`
    # used to do — same per-field inclusion rules, since it's the same
    # code, moved rather than reimplemented.
    flat_players = players_from_flat(
        pos={"a": "DEF", "b": "MED"}, price={"a": 5e6},
        owner={"b": "riv"}, name={"a": "Alpha"})
    assert set(flat_players) == {"a", "b"}, flat_players
    assert flat_players["a"].current.pos == "DEF"
    assert flat_players["a"].current.price == 5e6
    assert flat_players["a"].current.listed is True     # key in `price`
    assert flat_players["b"].current.listed is False    # not in `price`
    assert flat_players["b"].current.owner == "riv"
    assert flat_players["a"].identity.name == "Alpha"   # given explicitly
    assert flat_players["b"].identity.name == "b"       # falls back to the key
    # A field never mentioned by any flat dict: not a KeyError, just an
    # empty result — the whole point of a "bridge for callers without a
    # real PlayerProfile" existing at all.
    assert players_from_flat() == {}

    print("ffcore.fixtures self-test OK (24 cases)")


if __name__ == "__main__":
    _selftest()
