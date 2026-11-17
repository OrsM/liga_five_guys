"""The ledger and each manager's cash (ingest): the SQL views ledger and cash as selectors over a World. The ledger is
the feed's events (current.feed_ledger) plus the exits the squad snapshots show beyond those the feed records, each a
sale at the player's last value; cash is each manager's budget less what he paid, plus what he received and his
bonuses, plus the untracked gap for everyone, which is your real balance less the same sum for you.
Named rules, each tested:
  exits_counted_not_matched: an exit is inferred only beyond the feed: the k-th exit of a (manager, player) is inferred
    only if the feed holds fewer than k rows with that seller and player (a sale and a transfer both name their
    seller), so an inferred row can leave a later cut when its feed row arrives.
  exit_needs_manager_present: no exit is inferred at a stamp where the manager has no known rows at all.
  exit_at_next_snapshot: an inferred event is stamped with the first later snapshot without him; its amount is his
    value at the last snapshot with him (market_value, or 0.0).
  snapshot_stamps_from_known_players: a stamp is a snapshot only through rows with manager != "" and a player the
    identity names; consecutive stamps are those, over all sources.
  duplicate_squad_rows_count_each: each of two identical rows of (manager, player) in one snapshot is an exit when he
    leaves (n = 1, then 2).
  my_cash_is_standings_money: your cash is the first standings row's team_money that is neither none nor 0; everyone
    else's is flow plus untracked.
  untracked_gap_is_zero_without_me: untracked is 0.0 when you are not a manager of users or have no standings money.
  managers_distinct: one Cash per distinct manager of users, in first-seen order.
Contract: ledger(world, identity) gives the feed's rows (inferred 0) then the inferred sales (inferred 1); cash(world,
identity, league) gives one Cash per manager, and each cut's result is the same alone, in any subset or order of cuts,
and unchanged by rows stamped after it."""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, NamedTuple

from ffcore import current
from ffcore import observations as ob
from ffcore import state

SQUAD_HISTORY = "squad_history"
LEDGER_FACTS = (SQUAD_HISTORY, state.latest_fact("api_activity"), state.latest_fact("api_standings"))


class League(NamedTuple):
    me: str
    budget: float


class Held(NamedTuple):
    manager: str
    player: str
    value: float


class Exit(NamedTuple):
    at: str
    manager: str
    player: str
    value: float
    n: int


class LedgerEntry(NamedTuple):
    at: str
    kind: str
    who: str | None
    other: str | None
    player: str | None
    amount: float
    buyer: str | None
    seller: str | None
    inferred: int


class Cash(NamedTuple):
    manager: str
    cash: float


class SquadHistory:
    """The squad rows of every stamp (manager != "") and, per stamp, the Held list converted from them under the app
    identity `names`: a stamp is converted once and again only when it gains rows or `names` is another mapping."""

    def __init__(self) -> None:
        self.rows: dict[str, list[ob.ApiTeams]] = {}
        self.names: Mapping[str, str] | None = None
        self.known: dict[str, tuple[int, list[Held]]] = {}


def _squad_step(history: SquadHistory, row: ob.ApiTeams) -> SquadHistory:
    if row.manager != "":
        history.rows.setdefault(row.observed_at, []).append(row)
    return history


state.register(state.Fold(SQUAD_HISTORY, "api_teams", SquadHistory, _squad_step))


def _known(rows: Iterable[ob.ApiTeams], identity: current.Identity) -> list[Held]:
    out = []
    for r in rows:
        player = identity.app.get(r.player_id)
        if player is not None:
            out.append(Held(r.manager, player, r.market_value or 0.0))
    return out


def squad_snapshots(world: state.World, identity: current.Identity) -> dict[str, list[Held]]:
    """Per stamp, the squad rows with a known player (snapshot_stamps_from_known_players); a stamp with none is absent.
    A stamp's rows are converted once per identity, so a cut converts only the stamps it adds."""
    history: SquadHistory = state.fact(world, SQUAD_HISTORY)
    if history.names is not identity.app:
        history.names, history.known = identity.app, {}
    out: dict[str, list[Held]] = {}
    for stamp, rows in history.rows.items():
        converted = history.known.get(stamp)
        if converted is None or converted[0] != len(rows):
            converted = history.known[stamp] = (len(rows), _known(rows, identity))
        if converted[1]:
            out[stamp] = converted[1]
    return out


def exits(snapshots: Mapping[str, Sequence[Held]]) -> tuple[Exit, ...]:
    """Each row of a snapshot whose manager is in the next snapshot without his player there, stamped with that next
    snapshot, with n the count of his exits of that player up to it (exit_at_next_snapshot,
    exit_needs_manager_present, duplicate_squad_rows_count_each)."""
    stamps = sorted(snapshots)
    counted: Counter[tuple[str, str]] = Counter()
    out: list[Exit] = []
    for a, b in zip(stamps, stamps[1:]):
        now = snapshots[b]
        managers = {h.manager for h in now}
        holding = {(h.manager, h.player) for h in now}
        for h in snapshots[a]:
            if h.manager in managers and (h.manager, h.player) not in holding:
                counted[(h.manager, h.player)] += 1
                out.append(Exit(b, h.manager, h.player, h.value, counted[(h.manager, h.player)]))
    return tuple(out)


def _from_feed(e: current.FeedEntry) -> LedgerEntry:
    return LedgerEntry(at=e.at, kind=e.kind, who=e.who, other=e.other, player=e.player, amount=e.amount,
                       buyer=e.buyer, seller=e.seller, inferred=0)


def ledger(world: state.World, identity: current.Identity) -> tuple[LedgerEntry, ...]:
    """The feed's events, then each exit beyond the feed's records as a sale by him (exits_counted_not_matched)."""
    feed = current.feed_ledger(world, identity)
    recorded = Counter((e.seller, e.player) for e in feed if e.seller is not None)
    inferred = tuple(LedgerEntry(at=x.at, kind="sell", who=x.manager, other=None, player=x.player, amount=x.value,
                                 buyer=None, seller=x.manager, inferred=1)
                     for x in exits(squad_snapshots(world, identity)) if x.n > recorded[(x.manager, x.player)])
    return tuple(_from_feed(e) for e in feed) + inferred


def _sums(pairs: Iterable[tuple[str | None, float]]) -> dict[str, float]:
    out: dict[str, float] = {}
    for key, amount in pairs:
        if key is not None:
            out[key] = out.get(key, 0.0) + amount
    return out


def _my_money(world: state.World, me: str) -> float | None:
    for s in current.standings(world):
        if s.manager == me and s.team_money is not None and s.team_money != 0:
            return s.team_money
    return None


def cash(world: state.World, identity: current.Identity, league: League) -> tuple[Cash, ...]:
    """Each manager's cash (my_cash_is_standings_money, untracked_gap_is_zero_without_me, managers_distinct)."""
    rows = ledger(world, identity)
    paid = _sums((e.buyer, e.amount) for e in rows)
    got = _sums((e.seller, e.amount) for e in rows)
    bonus = _sums((e.who, e.amount) for e in rows if e.kind == "bonus")
    managers = tuple(dict.fromkeys(u.manager for u in current.users(world)))
    flow = {m: league.budget - paid.get(m, 0.0) + got.get(m, 0.0) + bonus.get(m, 0.0) for m in managers}
    mine = _my_money(world, league.me)
    untracked = mine - flow[league.me] if mine is not None and league.me in flow else 0.0
    return tuple(Cash(m, mine if mine is not None and m == league.me else flow[m] + untracked) for m in managers)


_APP = {"a1": "p1", "a2": "p2", "a3": "p3", "a4": "p4", "a5": "p5", "a6": "p6"}


def _team(at: str, pid: str, manager: str, value: float | None = 5.0) -> ob.ApiTeams:
    return ob.ApiTeams(at, "laliga", value, 1.0, 0.0, pid, "", manager, "", "", "", "")


def _act(stamp: str, aid: str, kind: str, uid: str, pid: str, amount: float | None, other: str = "") -> ob.ApiActivity:
    return ob.ApiActivity(stamp, "laliga", amount, pid, aid, stamp, kind, uid, other)


def _standing(stamp: str, team_id: str, uid: str, manager: str, money: float | None) -> ob.ApiStandings:
    return ob.ApiStandings(stamp, "laliga", 1, 0.0, 0.0, 0.0, money, team_id, uid, manager)


def _tables() -> dict[str, list[Any]]:
    return {
        "api_teams": [
            _team("01", "a1", "ann"), _team("01", "a2", "ann", 6.0), _team("01", "a3", "bob", 4.0),
            _team("01", "a4", "cat", 7.0),
            _team("02", "a1", "ann"), _team("02", "a3", "bob", 4.0), _team("02", "a4", "cat", 7.0),
            _team("03", "a1", "ann"), _team("03", "a2", "ann", 8.0), _team("03", "a3", "bob", 4.0),
            _team("04", "a1", "ann"), _team("04", "a4", "cat", 7.0),
            _team("05", "a1", "ann"), _team("05", "a3", "bob", 4.0), _team("05", "a5", "cat", 9.0)],
        "api_activity": [_act("02", "e1", "sell", "u1", "a2", 6.0), _act("03", "e2", "buy", "u1", "a2", 8.0),
                         _act("03", "e3", "transfer", "u2", "a3", 3.0, "u3"), _act("04", "e4", "bonus", "u2", "", 10.0)],
        "api_standings": [_standing("01", "t1", "u1", "ann", 100.0), _standing("01", "t2", "u2", "bob", 50.0),
                          _standing("01", "t3", "u3", "cat", 0.0)],
    }


def _scratch() -> tuple[ob.Observations, current.Identity, League]:
    return ob.of(_tables()), current.Identity({}, _APP), League("ann", 200.0)


def _view(world: state.World, identity: current.Identity, league: League) -> tuple[str, Any, Any]:
    return world.cut, ledger(world, identity), cash(world, identity, league)


def _selftest() -> None:
    """Each named rule holds on the scratch league; later rows leave a cut unchanged, any subset or order of cuts gives
    each cut the same facts, and a cut alone equals the cut inside a sweep."""
    obs, ident, league = _scratch()
    only = {n: state.FOLDS[n] for n in LEDGER_FACTS}
    cuts = ["00", "01", "02", "03", "04", "05"]
    alone = {c: _view(state.world_at(obs, c, only), ident, league) for c in cuts}

    def read(w: state.World) -> Any:
        return _view(w, ident, league)
    assert state.sweep(obs, cuts, read, only) == [alone[c] for c in cuts]
    for order in (cuts[::-1], ["04", "02", "04"], ["05"], ["01", "05"]):
        assert state.sweep(obs, order, read, only) == [alone[c] for c in order]
    other = current.Identity({}, {**_APP, "a2": "p6"})
    fresh = {c: _view(state.world_at(obs, c, only), other, league) for c in cuts}
    assert fresh["05"] != alone["05"]
    shared = state.world_at(obs, "05", only)
    assert [_view(shared, i, league) for i in (ident, other, ident)] == [alone["05"], fresh["05"], alone["05"]], (
        "a stamp's conversion is cached per identity and never leaks into another's")
    later_tables = _tables()
    later_tables["api_teams"] += [_team("06", "a1", "ann"), _team("06", "a4", "cat", 7.0)]
    later_tables["api_activity"].append(_act("06", "e9", "sell", "u3", "a4", 7.0))
    later = ob.of(later_tables)
    assert all(_view(state.world_at(later, c, only), ident, league) == alone[c] for c in cuts)

    led05 = alone["05"][1]
    inferred = [e for e in led05 if e.inferred]
    assert {(e.who, e.player, e.at, e.amount) for e in inferred} == {("ann", "p2", "04", 8.0), ("cat", "p4", "05", 7.0)}
    assert not any(e.inferred and e.at == "02" for e in led05), "a sale the feed records is not inferred"
    assert all(e.kind == "sell" and e.buyer is None and e.seller == e.who for e in inferred)
    assert [e for e in led05 if not e.inferred] == [_from_feed(e) for e in current.feed_ledger(
        state.world_at(obs, "05", only), ident)], "the feed's rows are in the ledger unchanged"

    tabs = _tables()
    kept = [a for a in tabs["api_activity"] if a.activity_id != "e1"]
    tabs["api_activity"] = kept + [_act("05", "e5", "sell", "u1", "a2", 6.0)]
    moved = ob.of(tabs)
    early = ledger(state.world_at(moved, "02", only), ident)
    late = ledger(state.world_at(moved, "05", only), ident)
    assert LedgerEntry("02", "sell", "ann", None, "p2", 6.0, None, "ann", 1) in early
    assert not any(e.inferred and e.at == "02" for e in late), "exits_counted_not_matched: a later feed row drops it"

    tabs = _tables()
    tabs["api_activity"] += [_act("04", "e6", "sell", "u1", "a2", 8.0), _act("05", "e7", "sell", "u3", "a4", 7.0)]
    recorded = ledger(state.world_at(ob.of(tabs), "05", only), ident)
    assert not any(e.inferred for e in recorded), "with every exit recorded in the feed there are no inferred rows"

    unknown = ob.of({"api_teams": [_team("01", "zz", "ann"), _team("02", "a1", "ann"), _team("02", "zz", "ann"),
                                   _team("03", "a1", "ann"), _team("04", "a1", "")]})
    snaps = squad_snapshots(state.world_at(unknown, "04", only), ident)
    assert snaps == {"02": [Held("ann", "p1", 5.0)], "03": [Held("ann", "p1", 5.0)]}, "snapshot_stamps_from_known_players"
    assert exits(snaps) == ()

    dup = exits({"01": [Held("ann", "p6", 1.0), Held("ann", "p6", 1.0), Held("bob", "p1", 1.0)],
                 "02": [Held("ann", "p1", 1.0), Held("bob", "p1", 1.0)]})
    assert [(x.manager, x.player, x.at, x.n) for x in dup] == [("ann", "p6", "02", 1), ("ann", "p6", "02", 2)]

    assert exits({"01": [Held("ann", "p1", 1.0), Held("bob", "p2", 1.0)], "02": [Held("bob", "p2", 1.0)]}) == (), (
        "exit_needs_manager_present: ann has no row at 02, so his p1 is not an exit")
    assert [(x.at, x.value) for x in exits({"01": [Held("ann", "p1", 3.0), Held("bob", "p1", 1.0)],
                                            "02": [Held("ann", "p2", 1.0), Held("bob", "p1", 1.0)],
                                            "03": [Held("bob", "p1", 1.0), Held("ann", "p9", 1.0)]})
            ] == [("02", 3.0), ("03", 1.0)], "exit_at_next_snapshot"

    money = {c.manager: c.cash for c in alone["05"][2]}
    assert money == {"ann": 100.0, "bob": 101.0, "cat": 104.0}, "my_cash_is_standings_money and the flow of the others"
    assert [c.manager for c in alone["05"][2]] == [u.manager for u in current.users(state.world_at(obs, "05", only))]
    zed = {c.manager: c.cash for c in cash(state.world_at(obs, "05", only), ident, League("zed", 200.0))}
    assert zed == {"ann": 206.0, "bob": 207.0, "cat": 210.0}
    assert money["bob"] - zed["bob"] == money["ann"] - zed["ann"], "the gap is the same for everyone"
    no_money = ob.of({**_tables(), "api_standings": [_standing("01", "t1", "u1", "ann", 0.0),
                                                     _standing("01", "t2", "u2", "bob", 50.0)]})
    assert {c.manager: c.cash for c in cash(state.world_at(no_money, "05", only), ident, league)}["ann"] == 206.0, (
        "untracked_gap_is_zero_without_me")
    print("ffcore.ledger self-test OK")


if __name__ == "__main__":
    _selftest()
