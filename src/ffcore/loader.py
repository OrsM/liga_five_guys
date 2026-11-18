"""The one module that reads storage for the sweep (ingest): the observations and the identity from the parse cache
(parsed.py: parsed.jsonl and snapindex.json under ingest.TIDY), the league, the model's knobs, and the opportunity
records of earlier live runs (the store), read once into a Loaded, from which every cut's decision is
a pure function of the World (decision.decision_at). A change of storage changes this file only.
Contract: load(cfg, cut, paths, previous) reads the files once and gives the same Loaded for the same files and cut;
loaded(obs, cfg, cut, previous) is the same from Observations already built; replay_cuts(obs, since) are the cuts a
replay runs at, by rule replay_cuts_are_app_snapshots.
Named rules, each tested:
  newest_snapshot_at_the_cut: with a cut, the tables of SNAPSHOT_FOLDED hold each source's newest snapshot at or
    before the cut and nothing older (their only readers keep just that); every other table is read whole.
  identity_from_the_observations: the identity is identity.identity_of of the kept identities and the app links of all
    the observations (before the fold), the crosswalk read at the cut (the end of time when none): nothing is stored.
  identity_reads_each_handle_once: a line-ups row whose handle (slug, name, team) an earlier row has adds nothing to the
    identity, so identity_in of handles_once(obs) is identity_in(obs).
  registry_computed_once: the registry with its app ids is identity.registered(obs), kept on obs: every loaded, identity_in
    and crosswalk of one Observations reads the one computation, whatever the cut.
  replay_cuts_are_app_snapshots: the distinct observed_at stamps of the api_teams snapshots, from since on, in order.
  opportunity_latest_by_cut_and_player: of the opportunity records of every published run, a cut's player has the row of
    the latest run that kept him; the rows come by cut then player.
  snapshots_are_of_the_whole: Loaded.snapshots are health.snapshots_of the whole observations up to the cut, before the
    fold of SNAPSHOT_FOLDED cuts three of the sources down to their newest snapshot.
  health_at_judges_what_was_seen: health_at(loaded, cut) is health.parser_health of the snapshots not after the cut."""
from __future__ import annotations

from pathlib import Path
from typing import Any, NamedTuple

import ingest
from health import Health, Snapshot, parser_health, snapshots_of
from ffcore import forecast_facts, identity, league, ledger, parsed, store
from ffcore import observations as ob
from ffcore.current import Identity

__all__ = ["SNAPSHOT_FOLDED", "Opportunity", "Loaded", "identity_in", "handles_once", "folded", "previous_opportunities", "snapshots_in", "health_at", "load",
           "loaded", "replay_cuts"]

SNAPSHOT_FOLDED = ("lineups", "api_lineup", "api_standings")

OPPORTUNITY_COLUMNS = ("cut", "player", "pos", "status", "asking", "close_at", "from_j", "replaces", "gain",
                       "per_million", "price_next", "price_lock", "bid", "win", "placed")


class Opportunity(NamedTuple):
    """A record kept by a live run: dec_opportunity's columns."""
    cut: str
    player: str
    pos: str
    status: str
    asking: float | None
    close_at: str | None
    from_j: int
    replaces: str | None
    gain: float
    per_million: float | None
    price_next: float | None
    price_lock: float | None
    bid: float | None
    win: float | None
    placed: float | None


class Loaded(NamedTuple):
    """Everything a decision reads that is not the World's fold of the observations."""
    obs: ob.Observations
    identity: Identity
    league: ledger.League
    knobs: forecast_facts.Knobs
    previous: tuple[Opportunity, ...]
    snapshots: tuple[Snapshot, ...] = ()


def previous_opportunities() -> tuple[Opportunity, ...]:
    """The opportunity records live runs kept of the newest cut (rule opportunity_latest_by_cut_and_player: a later run's
    row of a player replaces an earlier one's), by player; none with no record."""
    newest = ""
    kept: dict[str, Opportunity] = {}
    for o in store.records("dec_opportunity"):
        if o.cut > newest:
            newest, kept = o.cut, {}
        if o.cut == newest:
            kept[o.player] = Opportunity(**o._asdict())
    return tuple(kept[p] for p in sorted(kept))


def identity_in(obs: ob.Observations, cut: str | None = None) -> Identity:
    """Who each player is, from all of obs (rule identity_from_the_observations)."""
    thin = handles_once(obs)
    people = identity.slugged(identity.registered(obs), thin, identity.END_OF_TIME if cut is None else cut)
    return identity.identity_of(identity.ff_identity_now(thin, people), identity.app_player(people))


def handles_once(obs: ob.Observations) -> ob.Observations:
    """obs with only the first line-ups row of each handle (rule identity_reads_each_handle_once)."""
    first: dict[identity.Handle, Any] = {}
    for r in ob.rows_of(obs, "lineups"):
        first.setdefault(identity.handle_of(r), r)
    return ob.Observations({**obs.tables, **ob.of({"lineups": first.values()}).tables})


def folded(obs: ob.Observations, cut: str | None) -> ob.Observations:
    """obs with the tables of SNAPSHOT_FOLDED cut down by rule newest_snapshot_at_the_cut (obs itself without a cut)."""
    if cut is None:
        return obs
    newest = {name: ob.newest([r for r in ob.rows_of(obs, name) if r.observed_at <= cut]) for name in SNAPSHOT_FOLDED}
    return ob.Observations({**obs.tables, **ob.of(newest).tables})


def snapshots_in(obs: ob.Observations, cut: str | None) -> tuple[Snapshot, ...]:
    """health.snapshots_of obs up to the cut (all of them without one)."""
    return snapshots_of(obs, identity.END_OF_TIME if cut is None else cut)


def health_at(loaded: Loaded, cut: str) -> tuple[Health, ...]:
    """The status of each source at the cut (rule health_at_judges_what_was_seen)."""
    return parser_health((s for s in loaded.snapshots if s.at <= cut), cut)


def assembled(obs: ob.Observations, who: Identity, cfg: league.Config, previous: tuple[Opportunity, ...],
              snapshots: tuple[Snapshot, ...] = ()) -> Loaded:
    return Loaded(obs, who, ledger.League(cfg.me, cfg.budget), forecast_facts.knobs(), previous, snapshots)


def loaded(obs: ob.Observations, cfg: league.Config, cut: str | None = None,
           previous: tuple[Opportunity, ...] = ()) -> Loaded:
    """The Loaded of obs at the cut: the folded observations, their identity, the league cfg names, the model's knobs and
    the opportunity records previous and the snapshots of the whole obs (rule snapshots_are_of_the_whole)."""
    return assembled(folded(obs, cut), identity_in(obs, cut), cfg, previous, snapshots_in(obs, cut))


def load(cfg: league.Config, cut: str | None = None, paths: parsed.Paths | None = None,
         previous: tuple[Opportunity, ...] = (), obs: ob.Observations | None = None) -> Loaded:
    """loaded of obs, or of the files at paths (parsed.paths_in() when None) read once when obs is None: the same Loaded
    whichever, if obs is what parsed.read gives of those files; previous are the opportunity records
    (previous_opportunities of the store) a board needs, none for a replay."""
    return loaded(parsed.read(paths or parsed.paths_in()) if obs is None else obs, cfg, cut, previous)


def replay_cuts(obs: ob.Observations, since: str) -> list[str]:
    """The cuts a replay runs at: the distinct observed_at of the api_teams snapshots in obs that are not before since
    (a stamp of the tables, "" for all), in order."""
    return sorted({at for at in ob.stamps_of(obs, "api_teams") if at >= since})


def _selftest() -> None:
    assert OPPORTUNITY_COLUMNS == Opportunity._fields
    market = [ob.Market(at, "ff", 5.0, "p1", "DEL", "RMA", "Ana Lopez") for at in ("2026-08-17T1800Z", "2026-08-18T1800Z")]
    teams = [ob.ApiTeams("2026-08-18T1800Z", "laliga", 5.0, 0.0, 0.0, "a1", "", "ann", "Ana Lopez", "", "", "t1")]
    spotted = [ob.Lineups("2026-08-18T1800Z", "a", 50.0, slug, name, team, "", "")
               for slug, name, team in (("s1", "Ana Lopez", "c1"), ("s2", "Nobody Known", "c2"))]
    obs = ob.of({"market": market, "api_teams": teams, "lineups": spotted})
    assert identity_in(obs) == Identity({("s1", "Ana Lopez", "c1"): "p1"}, {"a1": "p1"}), "identity_from_the_observations"
    held = (Opportunity("2026-08-19T0000Z", "b", "MED", "market", 1.0, None, 1, "x", 2.0, None, None, None, None, None, None),)
    got = loaded(obs, league.Config("ann", 5.0), None, held)
    assert got.league == ledger.League("ann", 5.0) and got.knobs == forecast_facts.knobs() and got.previous == held
    assert got.obs == obs and got.identity == identity_in(obs)
    more = [ob.Lineups(at, "a", 50.0, slug, name, team, "", "") for at in ("2026-08-18T1900Z", "2026-08-19T1800Z")
            for slug, name, team in (("s1", "Ana Lopez", "c1"), ("", "Ana Lopez", "c1"), ("s2", "Nobody Known", "c2"))]
    repeated = ob.of({"market": market, "api_teams": teams, "lineups": [*spotted, *more]})
    thin = handles_once(repeated)
    assert len(ob.rows_of(thin, "lineups")) == 3 and ob.rows_of(thin, "market") == ob.rows_of(repeated, "market"), \
        "identity_reads_each_handle_once"
    for cut in (None, "2026-08-17T0000Z", "2026-08-18T1800Z", "2026-08-19T0000Z"):
        assert identity_in(thin, cut) == identity_in(repeated, cut), ("identity_reads_each_handle_once", cut)
        assert identity.crosswalk(thin, cut or "9") == identity.crosswalk(repeated, cut or "9"), cut
    snaps = ob.of({"api_teams": [ob.ApiTeams(at, "laliga", 0.0, 0.0, 0.0, "a1", "", "ann", "", "", "", "t1")
                                 for at in ("2026-08-19T0000Z", "2026-08-18T0000Z", "2026-08-19T0000Z",
                                            "2026-08-17T0000Z")]})
    assert replay_cuts(snaps, "2026-08-18") == ["2026-08-18T0000Z", "2026-08-19T0000Z"], "replay_cuts_are_app_snapshots"
    _selftest_files()
    _selftest_newest_snapshot()
    _selftest_previous()
    print("ffcore.loader self-test OK")


def _selftest_files() -> None:
    """load over a parse cache and a snapshot index gives loaded over the rows parsed.read makes of them."""
    import json
    import tempfile

    from sources import source
    row = {"source": "ff", "ff_id": "p1", "value": "5", "position": "DEL", "club": "RMA", "name": "Ana Lopez"}
    with tempfile.TemporaryDirectory() as tmp:
        paths = parsed.paths_in(Path(tmp))
        paths.cache.write_text(json.dumps({"k": "c1:%s" % ingest.fingerprint(source("market").parse), "r": [row]}) + "\n",
                               encoding="utf-8")
        paths.snaps.write_text(json.dumps({"snaps": {"2026-08-18T1800Z": {"size": 0, "pages": {"market": ["c1", "x"]}}}}),
                               encoding="utf-8")
        cfg = league.Config("ann", 5.0)
        got = load(cfg, None, paths)
        whole = parsed.read(paths)
        assert [r.ff_id for r in ob.rows_of(got.obs, "market")] == ["p1"] and got == loaded(whole, cfg)
        assert all(load(cfg, cut, paths) == load(cfg, cut, obs=whole) for cut in (None, "2026-08-18T1800Z", "2026-08-01T0000Z")), \
            "load_of_obs_is_load_of_the_files"
        assert all(ob.rows_of(parsed.read(paths, [t]), t) == ob.rows_of(whole, t) for t in ob.TABLES), \
            "a table read alone has the rows it has in the read of all"


def _selftest_newest_snapshot() -> None:
    """Rule newest_snapshot_at_the_cut on the scratch league with older, newer and later snapshots of the folded
    tables: at each of two cuts they hold each source's newest snapshot at or before it and decision_at over that
    equals decision_at over the full load."""
    from ffcore import state
    from decision import decision_at
    from ffcore.decision_facts import _APP, _CUTS, _ST, _LATER, _scratch, _standing, _tables

    def lineup(at: str, source: str) -> ob.ApiLineup:
        return ob.ApiLineup(at, source, 1.0, 0.0, "a1", "d1", "4-4-2", "", "")

    def spotted(at: str, source: str) -> ob.Lineups:
        return ob.Lineups(at, source, 50.0, "s1", "N1", "c1", "", "")
    tables = _tables()
    older = "2026-08-18T1700Z"
    tables["api_standings"] += [_standing("t-ann", "u1", "ann", 7.0, older), _standing("t-ann", "u1", "ann", 55.0, _CUTS[2]),
                                _standing("t-ann", "u1", "ann", 99.0, _LATER)]
    tables["api_lineup"] = [lineup(at, source) for at in (older, _ST, _CUTS[2], _LATER) for source in ("laliga", "other")]
    tables["lineups"] = [spotted(at, source) for at in (older, _ST, _CUTS[2], _LATER) for source in ("a", "b")]
    whole = ob.of(tables)
    ident = Identity({}, _APP)
    lg = ledger.League(_scratch()[2].me, _scratch()[2].budget)
    full = Loaded(whole, ident, lg, forecast_facts.knobs(), ())
    assert all(len(ob.rows_of(folded(whole, None), name)) == len(tables[name]) for name in ob.TABLES), "no cut loads every row"
    for cut in _CUTS[1:]:
        cut_loaded = Loaded(folded(whole, cut), ident, lg, forecast_facts.knobs(), ())
        for name in SNAPSHOT_FOLDED:
            newest = {src: max(r.observed_at for r in tables[name] if r.source == src and r.observed_at <= cut)
                      for src in {r.source for r in tables[name]}}
            assert sorted(map(tuple, ob.rows_of(cut_loaded.obs, name))) == sorted(
                tuple(r) for r in tables[name] if r.observed_at == newest[r.source]), (name, cut)
        for name in set(ob.TABLES) - set(SNAPSHOT_FOLDED):
            assert ob.rows_of(cut_loaded.obs, name) == ob.rows_of(full.obs, name), name
        assert decision_at(cut_loaded, state.world_at(cut_loaded.obs, cut)) == decision_at(
            full, state.world_at(full.obs, cut)), cut
    kept = loaded(whole, league.Config(), _CUTS[2])
    assert {older, _ST, _CUTS[2]} <= {s.at for s in kept.snapshots if s.src == "lineups"} and \
        len(set(ob.stamps_of(kept.obs, "lineups"))) == 1 and kept.snapshots == snapshots_in(whole, _CUTS[2]), \
        "snapshots_are_of_the_whole: the folded table holds one snapshot, the snapshots all up to the cut"
    early = loaded(whole, league.Config())
    assert health_at(early, older) == parser_health(snapshots_in(whole, older), older) and \
        health_at(early, _CUTS[2]) == parser_health(snapshots_in(whole, _CUTS[2]), _CUTS[2]), \
        "health_at_judges_what_was_seen: a cut sees none of the snapshots after it"


def _selftest_previous() -> None:
    """Rule opportunity_latest_by_cut_and_player: over published runs the later one's row of a cut's player replaces
    the earlier one's, and only the newest cut's rows are kept (the newest cut, not the newest run's), by player; no
    store is no records."""
    import os
    import tempfile

    from ffcore.records import DecOpportunity, Run

    def record(cut: str, player: str, gain: float) -> DecOpportunity:
        return DecOpportunity(cut, player, "MED", "market", 1.0, None, 1, None, gain, None, None, None, None, None, None)

    def run(i: int) -> Run:
        return Run(i, "t", "a", "service", "c", "f", 1, 1.0)
    with tempfile.TemporaryDirectory() as tmp:
        kept = os.environ.get("FF_ROOT")
        os.environ["FF_ROOT"] = str(Path(tmp) / "data")
        try:
            assert previous_opportunities() == ()
            store.publish(run(1), dec_opportunity=[record("2026-08-02T0000Z", "b", 1.0), record("2026-08-01T0000Z", "a", 2.0)])
            store.publish(run(2), dec_opportunity=[record("2026-08-02T0000Z", "b", 3.0), record("2026-08-02T0000Z", "c", 4.0)])
            assert [(o.cut[:10], o.player, o.gain) for o in previous_opportunities()] == [
                ("2026-08-02", "b", 3.0), ("2026-08-02", "c", 4.0)]
            store.publish(run(3), dec_opportunity=[record("2026-08-01T0000Z", "z", 5.0)])
            assert [(o.cut[:10], o.player) for o in previous_opportunities()] == [("2026-08-02", "b"), ("2026-08-02", "c")], \
                "the newest cut, not the newest run's"
        finally:
            store.clear()
            if kept is None:
                os.environ.pop("FF_ROOT")
            else:
                os.environ["FF_ROOT"] = kept


if __name__ == "__main__":
    _selftest()
