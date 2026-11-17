"""The one module that reads storage for the sweep (ingest): the observations, the identity, the league, the model's
knobs and the opportunity records of earlier live runs, read once into a Loaded, from which every cut's decision is
a pure function of the World (decision.decision_at). A change of storage changes this file only.
Contract: load(db, cfg) reads each table once and gives the same Loaded for the same database; replay_cuts(db) are the
cuts a replay runs at, by rule replay_cuts_are_successful_runs.
Named rules, each tested:
  identity_needs_a_player: identity_of keeps a futbolfantasy row (slug, name, team) and an app player only when it has a
    player.
  replay_cuts_are_successful_runs: the as_of of the runs with ok = 1, each cut to its minute in the stamp format of the
    tables, distinct, in order; an as_of that is no time is left out."""
from __future__ import annotations

import sqlite3
from typing import NamedTuple

from ffcore import forecast_facts, league, ledger
from ffcore import observations as ob
from ffcore.current import Identity
from ffcore.parse import snapshot_stamp, stamp

__all__ = ["Opportunity", "Loaded", "identity_of", "previous_opportunities", "load", "replay_cuts"]

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


def load(db: sqlite3.Connection, cfg: league.Config) -> Loaded:
    """db read once: its observations, identity and kept records, with the league cfg names and the model's knobs."""
    return Loaded(ob.read(db), identity_of(db), ledger.League(cfg.me, cfg.budget), forecast_facts.knobs(),
                  previous_opportunities(db))


def replay_cuts(db: sqlite3.Connection) -> list[str]:
    """The cuts a replay runs at: the as_of of the successful runs (ok = 1), each as a stamp of the tables (a run's
    as_of with seconds is cut to its minute; one that is no time is left out), distinct, in order."""
    times = (snapshot_stamp(at) for (at,) in db.execute("SELECT as_of FROM runs WHERE ok = 1"))
    return sorted({stamp(t) for t in times if t is not None})


def _selftest() -> None:
    assert OPPORTUNITY_COLUMNS == Opportunity._fields
    db = sqlite3.connect(":memory:")
    for name, source in ob.TABLES.items():
        db.execute("CREATE TABLE src_%s (%s)" % (name, ", ".join(source.row._fields)))
    db.execute("INSERT INTO src_api_teams VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
               ob.ApiTeams("2026-08-18T1800Z", "laliga", 0.0, 0.0, 0.0, "a1", "", "ann", "", "", "", "t1"))
    db.execute("CREATE TABLE ff_identity_kept (player_slug, player_name, team_slug, player)")
    db.executemany("INSERT INTO ff_identity_kept VALUES (?, ?, ?, ?)",
                   [("s1", "N1", "c1", "p1"), ("s2", "N2", "c2", None)])
    db.execute("CREATE TABLE app_player (app_id, player)")
    db.execute("INSERT INTO app_player VALUES ('a1', 'p1')")
    db.execute("CREATE TABLE dec_opportunity (%s)" % ", ".join(OPPORTUNITY_COLUMNS))
    db.executemany("INSERT INTO dec_opportunity VALUES (%s)" % ", ".join("?" * len(OPPORTUNITY_COLUMNS)), [
        ("2026-08-19T0000Z", "b", "MED", "market", 1.0, None, 1, "x", 2.0, None, None, None, None, None, None),
        ("2026-08-18T0000Z", "a", "DEL", "rival", None, None, 1, None, 1.0, None, None, None, None, None, None)])
    db.execute("CREATE TABLE runs (as_of, ok)")
    db.executemany("INSERT INTO runs VALUES (?, ?)", [
        ("2026-08-19T0000Z", 1), ("2026-08-18T0000Z", 1), ("2026-08-19T00:00:25Z", 1), ("2026-08-17T0000Z", 0),
        ("2026-08-17T0100Z", None), ("", 1)])
    assert identity_of(db) == Identity({("s1", "N1", "c1"): "p1"}, {"a1": "p1"}), "identity_needs_a_player"
    loaded = load(db, league.Config("ann", 5.0))
    assert loaded.league == ledger.League("ann", 5.0) and loaded.knobs == forecast_facts.knobs()
    assert [o.player for o in loaded.previous] == ["a", "b"] and loaded.previous[0].cut < loaded.previous[1].cut
    assert len(ob.rows_of(loaded.obs, "api_teams")) == 1
    assert replay_cuts(db) == ["2026-08-18T0000Z", "2026-08-19T0000Z"], (
        "replay_cuts_are_successful_runs")
    print("ffcore.loader self-test OK")


if __name__ == "__main__":
    _selftest()
