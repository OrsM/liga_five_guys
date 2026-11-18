"""The one module that reads storage for the sweep (ingest): the observations and the identity from the parse cache
(parsed.py: parsed.jsonl and snapindex.json under ingest.TIDY), the league, the model's knobs, and the opportunity
records of earlier live runs (SQL, until records move), read once into a Loaded, from which every cut's decision is
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
  replay_cuts_are_app_snapshots: the distinct observed_at stamps of the api_teams snapshots, from since on, in order."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, NamedTuple

import ingest
from ffcore import forecast_facts, identity, league, ledger, parsed
from ffcore import observations as ob
from ffcore.current import Identity

__all__ = ["SNAPSHOT_FOLDED", "Opportunity", "Loaded", "identity_in", "handles_once", "folded", "paths_in", "previous_opportunities", "load",
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


def previous_opportunities(db: sqlite3.Connection) -> tuple[Opportunity, ...]:
    """The opportunity records live runs kept, by cut then player."""
    return tuple(Opportunity._make(r) for r in db.execute(
        "SELECT %s FROM dec_opportunity ORDER BY cut, player" % ", ".join(OPPORTUNITY_COLUMNS)))


def paths_in(tidy: Path | None = None) -> parsed.Paths:
    """The parse cache and the snapshot index in tidy (ingest.TIDY when None)."""
    home = ingest.TIDY if tidy is None else tidy
    return parsed.Paths(home / ingest._CACHE, home / ingest._INDEX)  # noqa: SLF001 -- the file names are ingest's, not repeated here


def identity_in(obs: ob.Observations, cut: str | None = None) -> Identity:
    """Who each player is, from all of obs (rule identity_from_the_observations)."""
    people = identity.crosswalk(obs, identity.END_OF_TIME if cut is None else cut)
    return identity.identity_of(identity.ff_identity_now(obs, people), identity.app_player(people))


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


def assembled(obs: ob.Observations, who: Identity, cfg: league.Config, previous: tuple[Opportunity, ...]) -> Loaded:
    return Loaded(obs, who, ledger.League(cfg.me, cfg.budget), forecast_facts.knobs(), previous)


def loaded(obs: ob.Observations, cfg: league.Config, cut: str | None = None,
           previous: tuple[Opportunity, ...] = ()) -> Loaded:
    """The Loaded of obs at the cut: the folded observations, their identity, the league cfg names, the model's knobs and
    the opportunity records previous."""
    return assembled(folded(obs, cut), identity_in(obs, cut), cfg, previous)


def _read(paths: parsed.Paths, cut: str | None) -> tuple[ob.Observations, ob.Observations]:
    """The files at paths as the folded observations and, beside them, the observations the identity is read from: the
    whole ones are not kept, so the line-ups that only the identity reads leave memory before it is computed."""
    whole = parsed.read(paths)
    return folded(whole, cut), handles_once(whole)


def load(cfg: league.Config, cut: str | None = None, paths: parsed.Paths | None = None,
         previous: tuple[Opportunity, ...] = ()) -> Loaded:
    """The files at paths (paths_in() when None) read once, as loaded gives them; previous are the opportunity records
    (previous_opportunities of the database) a board needs, none for a replay."""
    obs, thin = _read(paths or paths_in(), cut)
    return assembled(obs, identity_in(thin, cut), cfg, previous)


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
    print("ffcore.loader self-test OK")


def _selftest_files() -> None:
    """load over a parse cache and a snapshot index gives loaded over the rows parsed.read makes of them."""
    import json
    import tempfile

    from sources import source
    row = {"source": "ff", "ff_id": "p1", "value": "5", "position": "DEL", "club": "RMA", "name": "Ana Lopez"}
    with tempfile.TemporaryDirectory() as tmp:
        paths = paths_in(Path(tmp))
        paths.cache.write_text(json.dumps({"k": "c1:%s" % ingest.fingerprint(source("market").parse), "r": [row]}) + "\n",
                               encoding="utf-8")
        paths.snaps.write_text(json.dumps({"snaps": {"2026-08-18T1800Z": {"size": 0, "pages": {"market": ["c1", "x"]}}}}),
                               encoding="utf-8")
        cfg = league.Config("ann", 5.0)
        got = load(cfg, None, paths)
        assert [r.ff_id for r in ob.rows_of(got.obs, "market")] == ["p1"] and got == loaded(parsed.read(paths), cfg)


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


if __name__ == "__main__":
    _selftest()
