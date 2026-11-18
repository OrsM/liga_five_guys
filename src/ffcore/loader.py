"""The one module that reads storage for the sweep (ingest): the observations, the identity, the league, the model's
knobs and the opportunity records of earlier live runs, read once into a Loaded, from which every cut's decision is
a pure function of the World (decision.decision_at). A change of storage changes this file only.
Contract: load(db, cfg, cut) reads each table once and gives the same Loaded for the same database and cut; replay_cuts(obs, since) are the
cuts a replay runs at, by rule replay_cuts_are_app_snapshots.
Named rules, each tested:
  newest_snapshot_at_the_cut: with a cut, the tables of SNAPSHOT_FOLDED hold each source's newest snapshot at or
    before the cut and nothing older (their only readers keep just that); every other table is read whole.
  identity_needs_a_player: identity_of keeps a futbolfantasy row (slug, name, team) and an app player only when it has a
    player.
  replay_cuts_are_app_snapshots: the distinct observed_at stamps of the api_teams snapshots, from since on, in order."""
from __future__ import annotations

import sqlite3
from typing import Any, NamedTuple

from ffcore import forecast_facts, league, ledger
from ffcore import observations as ob
from ffcore.current import Identity

__all__ = ["SNAPSHOT_FOLDED", "Opportunity", "Loaded", "identity_of", "previous_opportunities", "load", "replay_cuts"]

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


def identity_of(db: sqlite3.Connection) -> Identity:
    """The kept futbolfantasy identities that name a player, and the app players (rule identity_needs_a_player)."""
    ff = {(slug, name, team): player for slug, name, team, player in db.execute(
        "SELECT player_slug, player_name, team_slug, player FROM ff_identity_kept") if player is not None}
    app = {app_id: player for app_id, player in db.execute("SELECT app_id, player FROM app_player")}
    return Identity(ff, app)


def previous_opportunities(db: sqlite3.Connection) -> tuple[Opportunity, ...]:
    """The opportunity records live runs kept, by cut then player."""
    return tuple(Opportunity._make(r) for r in db.execute(
        "SELECT %s FROM dec_opportunity ORDER BY cut, player" % ", ".join(OPPORTUNITY_COLUMNS)))


def load(db: sqlite3.Connection, cfg: league.Config, cut: str | None = None) -> Loaded:
    """db read once: its observations, identity and kept records, with the league cfg names and the model's knobs.
    With a cut, the SNAPSHOT_FOLDED tables are only each source's newest snapshot at or before cut (rule
    newest_snapshot_at_the_cut), so a decision at the cut equals the one over the full load; without, all rows."""
    folded = () if cut is None else SNAPSHOT_FOLDED
    whole = ob.read(db, [name for name in ob.TABLES if name not in folded])
    newest = ob.read(db, folded, cut, latest=True)
    return Loaded(ob.Observations({**whole.tables, **newest.tables}), identity_of(db),
                  ledger.League(cfg.me, cfg.budget), forecast_facts.knobs(), previous_opportunities(db))


def replay_cuts(obs: ob.Observations, since: str) -> list[str]:
    """The cuts a replay runs at: the distinct observed_at of the api_teams snapshots in obs that are not before since
    (a stamp of the tables, "" for all), in order."""
    return sorted({at for at in ob.stamps_of(obs, "api_teams") if at >= since})


def _database(tables: dict[str, list[Any]]) -> sqlite3.Connection:
    db = sqlite3.connect(":memory:")
    for name, source in ob.TABLES.items():
        db.execute("CREATE TABLE src_%s (%s)" % (name, ", ".join(source.row._fields)))
        db.executemany("INSERT INTO src_%s VALUES (%s)" % (name, ", ".join("?" * len(source.row._fields))),
                       tables.get(name, []))
    db.execute("CREATE TABLE ff_identity_kept (player_slug, player_name, team_slug, player)")
    db.execute("CREATE TABLE app_player (app_id, player)")
    db.execute("CREATE TABLE dec_opportunity (%s)" % ", ".join(OPPORTUNITY_COLUMNS))
    return db


def _selftest() -> None:
    assert OPPORTUNITY_COLUMNS == Opportunity._fields
    db = _database({"api_teams": [ob.ApiTeams("2026-08-18T1800Z", "laliga", 0.0, 0.0, 0.0, "a1", "", "ann", "", "", "", "t1")]})
    db.executemany("INSERT INTO ff_identity_kept VALUES (?, ?, ?, ?)",
                   [("s1", "N1", "c1", "p1"), ("s2", "N2", "c2", None)])
    db.execute("INSERT INTO app_player VALUES ('a1', 'p1')")
    db.executemany("INSERT INTO dec_opportunity VALUES (%s)" % ", ".join("?" * len(OPPORTUNITY_COLUMNS)), [
        ("2026-08-19T0000Z", "b", "MED", "market", 1.0, None, 1, "x", 2.0, None, None, None, None, None, None),
        ("2026-08-18T0000Z", "a", "DEL", "rival", None, None, 1, None, 1.0, None, None, None, None, None, None)])
    assert identity_of(db) == Identity({("s1", "N1", "c1"): "p1"}, {"a1": "p1"}), "identity_needs_a_player"
    loaded = load(db, league.Config("ann", 5.0))
    assert loaded.league == ledger.League("ann", 5.0) and loaded.knobs == forecast_facts.knobs()
    assert [o.player for o in loaded.previous] == ["a", "b"] and loaded.previous[0].cut < loaded.previous[1].cut
    assert len(ob.rows_of(loaded.obs, "api_teams")) == 1
    snaps = ob.of({"api_teams": [ob.ApiTeams(at, "laliga", 0.0, 0.0, 0.0, "a1", "", "ann", "", "", "", "t1")
                                 for at in ("2026-08-19T0000Z", "2026-08-18T0000Z", "2026-08-19T0000Z",
                                            "2026-08-17T0000Z")]})
    assert replay_cuts(snaps, "2026-08-18") == ["2026-08-18T0000Z", "2026-08-19T0000Z"], "replay_cuts_are_app_snapshots"
    _selftest_newest_snapshot()
    print("ffcore.loader self-test OK")


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
    db = _database(tables)
    db.executemany("INSERT INTO app_player VALUES (?, ?)", list(_APP.items()))
    cfg = league.Config(_scratch()[2].me, _scratch()[2].budget)
    full = load(db, cfg)
    assert all(len(ob.rows_of(full.obs, name)) == len(tables[name]) for name in ob.TABLES), "no cut loads every row"
    for cut in _CUTS[1:]:
        cut_loaded = load(db, cfg, cut)
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
