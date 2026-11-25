"""What the latest rows say (ingest): the facts player_status, player_club, player_value, club_rating,
listings, offers, squads, standings, owners, users, app_lineup, feed_ledger, price_paid, offer_ratios and
player_roster hold, as selectors over a World (state.py) and, for offer_ratios, two folds of their own (offers_made,
team_values).

A selector takes the World at a cut (and an Identity where the player is named by a futbolfantasy or app row) and returns
tuples of named rows, in no particular order. Identity is the one identity type, used by season_facts too: its ff
maps a futbolfantasy row (slug, name, team) to a player id, its app maps an app player_id to a player id; a row with
none has no player. Named rules, each tested:
  one_status_per_player: of a player's futbolfantasy rows, the flagged one, then the highest start_pct (none is
    lowest), a tie to the first in observation order; the app's out_of_league word makes his status gone.
  one_row_per_key: users, owners, player_club and player_value keep one row per user_id / player, the first in
    observation order.
  zero_value_is_none: player_value has no row for a value that is none or 0.
  empty_club_is_none: player_club.club is None for an empty club.
  no_league_mean_no_ratings: club_rating has no rows when the league's mean goals for is 0 or there is no goal data.
  league_mean_is_every_team_game: club_rating's league mean is over both sides of every result, a side whose club is
    not a league club (a blank club) included; only league clubs get a rating.
  one_row_per_match: club_rating reads one result per season, day and pair of clubs (a club's name where it has no
    slug): the source's spellings of a club change between fetches (Rayo Vallecano, Vallecano), the newest row wins.
  roles_from_kind: a feed event's buyer is its user for a buy or transfer; its seller is its user for a sell and its
    counterparty for a transfer; no other kind has either.
  paid_is_last_buy_to_owner: price_paid is, per owned player, the amount of the latest (by stamp, ties to the first
    in feed order) event whose buyer owns him now.
  offer_first_sighting: offer_ratios holds each offer once, at its first sighting that has a team value other than 0
    at that stamp; only market offers with money other than 0 and an offer id count.
  roster_market_rows: player_roster has one row per latest market row with a ff_id that is not a coach; no
    de-duplication.
  roster_lineup_only: player_roster adds one row, at MED with no value, per latest futbolfantasy line-up row whose
    player the identity names and no latest market row holds (a coach's ff_id counts).
  trim_spaces_only: a roster name loses its leading and trailing spaces, and nothing else.
  unknown_position_is_med: a roster position that is none of the four is MED.
FACT_NAMES are the folds the selectors read (pass {n: state.FOLDS[n] for n in FACT_NAMES} to sweep to run only
those). Selectors: player_status, player_club, player_value, club_rating, listings, offers, squads, standings, owners,
users, app_lineup, feed_ledger, price_paid, offer_ratios, player_roster."""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any, NamedTuple

from ffcore import observations as ob
from ffcore import state
from ffcore.rules import position, slot

OFFERS_MADE = "offers_made"
TEAM_VALUES = "team_values"
OK_STATUS = ("", "ok")
LIST_SOURCE = "futbolfantasy"
OUT_OF_LEAGUE = "out_of_league"
BUYING = ("buy", "transfer")


class Identity(NamedTuple):
    ff: Mapping[tuple[str, str, str], str]
    app: Mapping[str, str]


class PlayerStatus(NamedTuple):
    player: str
    listed: bool
    pct: float | None
    at: str | None
    status: str | None
    note: str


class PlayerClub(NamedTuple):
    player: str
    club: str | None
    pos: str


class PlayerValue(NamedTuple):
    player: str
    value: float


class RosterRow(NamedTuple):
    player: str
    name: str
    pos: str
    value: float | None


class ClubRating(NamedTuple):
    club: str
    n: int
    attack: float
    defence: float


class Listing(NamedTuple):
    player: str | None
    player_id: str
    market_id: str
    bid_id: str
    seller: str
    expires_at: str
    sale_price: float | None
    market_value: float | None
    bids: int | None
    bid_money: float | None
    bid_status: str


class Offer(NamedTuple):
    offer_id: str
    player_team_id: str
    status: str
    money: float | None
    created_at: str
    expires_at: str
    from_market: str


class Squad(NamedTuple):
    player: str | None
    player_id: str
    player_team_id: str
    manager: str
    buyout: float | None
    market_value: float | None


class Standing(NamedTuple):
    user_id: str
    manager: str
    position: int | None
    team_points: float | None
    fixture_points: float | None
    team_value: float | None
    team_money: float | None


class User(NamedTuple):
    user_id: str
    manager: str


class Owner(NamedTuple):
    player: str
    manager: str


class AppLineup(NamedTuple):
    app_id: str
    player: str | None
    slot: str
    name: str


class FeedEntry(NamedTuple):
    at: str
    kind: str
    who: str | None
    other: str | None
    player: str | None
    amount: float
    buyer: str | None
    seller: str | None


class Paid(NamedTuple):
    player: str
    paid: float


class OfferRatio(NamedTuple):
    created_at: str
    ratio: float


def _first_per(rows: Iterable[Any], key: Callable[[Any], Any]) -> list[Any]:
    held: dict[Any, Any] = {}
    for r in rows:
        held.setdefault(key(r), r)
    return list(held.values())


def player_status(world: state.World, identity: Identity) -> tuple[PlayerStatus, ...]:
    """One PlayerStatus per player the futbolfantasy listing names or the app says is gone (one_status_per_player)."""
    best: dict[str, ob.Lineups] = {}
    for r in state.latest(world, "lineups"):
        p = identity.ff.get((r.player_slug, r.player_name, r.team_slug))
        if r.source != LIST_SOURCE or p is None:
            continue
        held = best.get(p)
        if held is None or _rank(r) > _rank(held):
            best[p] = r
    gone = {identity.app[r.player_id] for r in state.latest(world, "api_players_all")
            if r.player_status == OUT_OF_LEAGUE and r.player_id in identity.app}
    out = [PlayerStatus(p, True, r.start_pct, r.observed_at,
                        "gone" if p in gone else (r.status if r.status not in OK_STATUS else None),
                        "" if p in gone else r.note) for p, r in best.items()]
    out.extend(PlayerStatus(p, False, None, None, "gone", "") for p in sorted(gone - best.keys()))
    return tuple(out)


def _rank(r: ob.Lineups) -> tuple[bool, bool, float]:
    return r.status not in OK_STATUS, r.start_pct is not None, r.start_pct or 0.0


def player_club(world: state.World) -> tuple[PlayerClub, ...]:
    """One PlayerClub per player on the market page (one_row_per_key; empty_club_is_none)."""
    return tuple(PlayerClub(r.ff_id, r.club or None, slot(r.position))
                 for r in _first_per((r for r in state.latest(world, "market") if r.ff_id != ""), lambda r: r.ff_id))


def player_value(world: state.World) -> tuple[PlayerValue, ...]:
    """One PlayerValue per player whose market value is above 0 (one_row_per_key; zero_value_is_none)."""
    return tuple(PlayerValue(r.ff_id, r.value) for r in _first_per(
        (r for r in state.latest(world, "market") if r.ff_id != "" and r.value is not None and r.value > 0),
        lambda r: r.ff_id))


def player_roster(world: state.World, identity: Identity) -> tuple[RosterRow, ...]:
    """The roster at the cut: the market rows, then the line-up-only players (roster_market_rows, roster_lineup_only,
    trim_spaces_only, unknown_position_is_med)."""
    market = state.latest(world, "market")
    listed = {r.ff_id for r in market}
    out = [RosterRow(r.ff_id, r.name.strip(" "), position(r.position.upper()), r.value)
           for r in market if r.ff_id != "" and r.position.lower() != "entrenador"]
    for r in state.latest(world, "lineups"):
        p = identity.ff.get((r.player_slug, r.player_name, r.team_slug))
        if r.source == LIST_SOURCE and p is not None and p not in listed:
            out.append(RosterRow(p, r.player_name.strip(" "), position(""), None))
    return tuple(out)


def played(world: state.World) -> tuple[ob.ResultsHistory, ...]:
    """The latest results with goals, one per match (one_row_per_match)."""
    newest: dict[tuple[str, str, str, str], ob.ResultsHistory] = {}
    for r in sorted(state.latest(world, "results_history"), key=lambda r: r.observed_at):
        if r.home_goals is not None and r.away_goals is not None:
            newest[(r.season, r.date, r.home or r.home_name, r.away or r.away_name)] = r
    return tuple(newest.values())


def club_rating(world: state.World, fixture_k: float) -> tuple[ClubRating, ...]:
    """Each league club's goals for and against per match over the league's mean, pulled toward 1 by fixture_k matches
    at that mean, over the latest results (no_league_mean_no_ratings, league_mean_is_every_team_game, one_row_per_match)."""
    games: dict[str, list[tuple[float, float]]] = {}
    scored: list[float] = []
    for r in played(world):
        assert r.home_goals is not None and r.away_goals is not None
        scored += (r.home_goals, r.away_goals)
        if r.home != "":
            games.setdefault(r.home, []).append((r.home_goals, r.away_goals))
        if r.away != "":
            games.setdefault(r.away, []).append((r.away_goals, r.home_goals))
    mu = sum(scored) / len(scored) if scored else 0.0
    if mu <= 0:
        return ()
    return tuple(ClubRating(c, len(v), (sum(f for f, _ in v) + fixture_k * mu) / ((len(v) + fixture_k) * mu),
                            (sum(a for _, a in v) + fixture_k * mu) / ((len(v) + fixture_k) * mu))
                 for c, v in games.items())


def listings(world: state.World, identity: Identity) -> tuple[Listing, ...]:
    """The app's market listings at the cut, each with the player the crosswalk names."""
    return tuple(Listing(identity.app.get(r.player_id), r.player_id, r.market_id, r.bid_id, r.seller, r.expires_at,
                         r.sale_price, r.market_value, r.bids, r.bid_money, r.bid_status)
                 for r in state.latest(world, "api_market"))


def offers(world: state.World) -> tuple[Offer, ...]:
    """The game's offers standing at the cut."""
    return tuple(Offer(r.offer_id, r.player_team_id, r.status, r.money, r.created_at, r.expires_at, r.from_market)
                 for r in state.latest(world, "api_offers"))


def squads(world: state.World, identity: Identity) -> tuple[Squad, ...]:
    """Every squad row at the cut, with the player the crosswalk names."""
    return tuple(Squad(identity.app.get(r.player_id), r.player_id, r.player_team_id, r.manager, r.buyout,
                       r.market_value) for r in state.latest(world, "api_teams"))


def standings(world: state.World) -> tuple[Standing, ...]:
    """The league's standing rows at the cut."""
    return tuple(Standing(r.user_id, r.manager, r.position, r.team_points, r.fixture_points, r.team_value,
                          r.team_money) for r in state.latest(world, "api_standings"))


def users(world: state.World) -> tuple[User, ...]:
    """One User per user_id with a manager name (one_row_per_key)."""
    return tuple(User(r.user_id, r.manager) for r in _first_per(
        (r for r in state.latest(world, "api_standings") if r.user_id != "" and r.manager != ""),
        lambda r: r.user_id))


def owners(world: state.World, identity: Identity) -> tuple[Owner, ...]:
    """One Owner per player a manager holds in the latest squads (one_row_per_key)."""
    held = ((identity.app.get(r.player_id), r.manager) for r in state.latest(world, "api_teams")
            if r.manager != "" and r.player_id in identity.app)
    return tuple(Owner(p, m) for p, m in _first_per(held, lambda x: x[0]) if p is not None)


def app_lineup(world: state.World, identity: Identity) -> tuple[AppLineup, ...]:
    """The eleven the app has fielded, each with the player the crosswalk names."""
    return tuple(AppLineup(r.player_id, identity.app.get(r.player_id), r.slot, r.player_name)
                 for r in state.latest(world, "api_lineup"))


def feed_ledger(world: state.World, identity: Identity) -> tuple[FeedEntry, ...]:
    """Each activity event once, with who paid and who was paid (roles_from_kind)."""
    manager = {u.user_id: u.manager for u in users(world)}
    out = []
    for r in state.latest(world, "api_activity"):
        who, other = manager.get(r.user_id), manager.get(r.counterparty)
        out.append(FeedEntry(r.at, r.kind, who, other, identity.app.get(r.player_id),
                             0.0 if r.amount is None else r.amount,
                             who if r.kind in BUYING else None,
                             who if r.kind == "sell" else (other if r.kind == "transfer" else None)))
    return tuple(out)


def price_paid(world: state.World, identity: Identity) -> tuple[Paid, ...]:
    """What each owned player's owner last paid for him (paid_is_last_buy_to_owner)."""
    owner = {o.player: o.manager for o in owners(world, identity)}
    last: dict[str, FeedEntry] = {}
    for e in feed_ledger(world, identity):
        if e.player is None or e.buyer is None or owner.get(e.player) != e.buyer:
            continue
        held = last.get(e.player)
        if held is None or e.at > held.at:
            last[e.player] = e
    return tuple(Paid(p, e.amount) for p, e in last.items())


def offer_ratios(world: state.World) -> tuple[OfferRatio, ...]:
    """Each market offer's money over the player's team value at its first sighting with a value other than 0
    (offer_first_sighting), by (created_at, ratio)."""
    value = state.fact(world, TEAM_VALUES)
    seen: set[str] = set()
    out = []
    for r in state.fact(world, OFFERS_MADE):
        v = value.get((r.observed_at, r.player_team_id))
        if r.offer_id in seen or not v:
            continue
        seen.add(r.offer_id)
        out.append(OfferRatio(r.created_at, r.money / v))
    return tuple(sorted(out))


def _made_step(held: list[ob.ApiOffers], row: ob.ApiOffers) -> list[ob.ApiOffers]:
    if row.from_market == "true" and row.money and row.offer_id:
        held.append(row)
    return held


def _values_step(held: dict[tuple[str, str], float], row: ob.ApiTeams) -> dict[tuple[str, str], float]:
    held[(row.observed_at, row.player_team_id)] = row.market_value or 0.0
    return held


state.register(state.Fold(OFFERS_MADE, "api_offers", list, _made_step))
state.register(state.Fold(TEAM_VALUES, "api_teams", dict, _values_step))

FACT_NAMES = (OFFERS_MADE, TEAM_VALUES) + tuple(state.latest_fact(t) for t in (
    "market", "lineups", "api_teams", "api_standings", "api_market", "api_offers", "api_lineup",
    "api_players_all", "results_history", "api_activity"))


def facts(world: state.World, identity: Identity, fixture_k: float) -> dict[str, Any]:
    """Every selector of this module at the world, by name."""
    return {"player_status": player_status(world, identity), "player_club": player_club(world),
            "player_value": player_value(world), "club_rating": club_rating(world, fixture_k),
            "listings": listings(world, identity), "offers": offers(world), "squads": squads(world, identity),
            "standings": standings(world), "owners": owners(world, identity), "users": users(world),
            "app_lineup": app_lineup(world, identity), "feed_ledger": feed_ledger(world, identity),
            "price_paid": price_paid(world, identity), "offer_ratios": offer_ratios(world),
            "player_roster": player_roster(world, identity)}


def _selftest() -> None:
    """Each named rule holds on a small league; later rows leave a cut unchanged, any subset or order of cuts
    gives each cut the same facts, and a cut alone equals the cut inside a sweep."""
    ident = Identity({("s1", "n1", "t"): "p1", ("s2", "n2", "t"): "p2", ("s3", "n3", "t"): "p3",
                      ("s4", "n4", "t"): "p4", ("s5", "n5", "t"): "p5"},
                     {"a1": "p1", "a2": "p2", "a3": "p3", "a9": "p9"})

    def ln(at: str, slug: str, pct: float | None, status: str = "", note: str = "",
           source: str = LIST_SOURCE) -> ob.Lineups:
        return ob.Lineups(at, source, pct, slug, "n" + slug[1:], "t", status, note)

    def pa(at: str, pid: str, status: str) -> ob.ApiPlayersAll:
        return ob.ApiPlayersAll(at, "laliga", 1.0, pid, "", "", "", status)

    def standing(at: str, uid: str, manager: str) -> ob.ApiStandings:
        return ob.ApiStandings(at, "laliga", 1, 0.0, 0.0, 0.0, 0.0, "t" + uid, uid, manager)

    def team(at: str, pid: str, manager: str, ptid: str = "", value: float | None = 5.0) -> ob.ApiTeams:
        return ob.ApiTeams(at, "laliga", value, 1.0, 0.0, pid, "", manager, "", "", "", ptid)

    def act(at: str, aid: str, kind: str, uid: str, other: str, pid: str, amount: float | None,
            stamp: str) -> ob.ApiActivity:
        return ob.ApiActivity(at, "laliga", amount, pid, aid, stamp, kind, uid, other)

    def made(at: str, oid: str, ptid: str, money: float | None, market: str = "true") -> ob.ApiOffers:
        return ob.ApiOffers(at, "laliga", money, ptid, oid, "", "c" + oid, "", market)

    def res(at: str, home: str, away: str, hg: float | None, ag: float | None, day: str) -> ob.ResultsHistory:
        return ob.ResultsHistory(at, "fd", hg, ag, "s", day, "h" + home, "h" + away, home, away)

    def mk(at: str, ff: str, value: float | None, position: str, club: str, source: str = "",
           name: str = "") -> ob.Market:
        return ob.Market(at, source, value, ff, position, club, name)

    tables: dict[str, list[Any]] = {
        "lineups": [ln("01", "s1", 50.0), ln("01", "s2", 60.0), ln("01", "s2", 60.0, "doubt", "knock"),
                    ln("01", "s3", None), ln("01", "s3", 10.0), ln("01", "s4", 70.0, "", "", "other"),
                    ln("01", "x9", 90.0), ln("01", "s5", 40.0)],
        "api_players_all": [pa("01", "a1", "ok"), pa("01", "a9", OUT_OF_LEAGUE), pa("02", "a1", OUT_OF_LEAGUE)],
        "market": [mk("01", "p1", 0.0, "Portero", "RMA"), mk("01", "p2", None, "DEL", ""),
                   mk("01", "p3", 4.0, "Entrenador", "BAR"), mk("01", "p1", 9.0, "DEL", "ATM", "b"), mk("01", "", 1.0, "", ""),
                   mk("01", "p6", 3.0, "DEL", "RMA", "c", "  Ana\t"), mk("01", "p7", 2.0, "Tercero", "VAL", "c", "Bo")],
        "api_standings": [standing("01", "u1", "ann"), standing("01", "u1", "zed"), standing("01", "u2", "bob"),
                          standing("01", "u3", ""), standing("01", "", "nobody")],
        "api_teams": [team("01", "a1", "ann", "t1", None), team("01", "a2", "bob", "t2"),
                      team("01", "a2", "cat", "t2"), team("01", "a3", ""), team("01", "a7", "ann", "t7"),
                      team("02", "a1", "ann", "t1", 8.0), team("02", "a2", "bob", "t2", 6.0)],
        "api_activity": [act("01", "e1", "buy", "u1", "", "a1", 3.0, "2026-01-01"),
                         act("01", "e2", "buy", "u1", "", "a1", 4.0, "2026-01-03"),
                         act("01", "e3", "buy", "u2", "", "a1", 9.0, "2026-01-05"),
                         act("01", "e4", "transfer", "u2", "u1", "a2", 7.0, "2026-01-02"),
                         act("01", "e5", "sell", "u1", "", "a2", 8.0, "2026-01-04"),
                         act("01", "e6", "bonus", "u2", "", "", None, "2026-01-06"),
                         act("02", "e1", "buy", "u1", "", "a1", 3.5, "2026-01-01")],
        "api_offers": [made("01", "o1", "t1", 10.0), made("01", "o2", "t9", 10.0), made("01", "o3", "t1", 5.0, "false"),
                       made("01", "o4", "t1", 0.0), made("01", "", "t1", 5.0), made("02", "o1", "t1", 20.0),
                       made("02", "o2", "t2", 12.0), made("02", "o5", "t2", 6.0)],
        "results_history": [res("01", "A", "B", 2.0, 0.0, "d1"), res("01", "B", "A", 1.0, 1.0, "d2"),
                            res("01", "A", "C", None, 1.0, "d3"), res("02", "A", "B", 3.0, 0.0, "d1")],
    }
    obs = ob.of(tables)
    k = 2.0

    def at(cut: str) -> dict[str, Any]:
        w = state.world_at(obs, cut)
        return {n: sorted(v, key=repr) for n, v in facts(w, ident, k).items()}
    one, two = at("01"), at("02")

    st = {s.player: s for s in one["player_status"]}
    assert st["p1"].pct == 50.0 and st["p1"].status is None and st["p1"].listed
    assert st["p2"] == PlayerStatus("p2", True, 60.0, "01", "doubt", "knock"), "flagged beats unflagged"
    assert st["p3"].pct == 10.0, "a start_pct beats none"
    assert "p4" not in st and "p9" in st and st["p9"] == PlayerStatus("p9", False, None, None, "gone", "")
    assert {s.player for s in two["player_status"]} == {"p1", "p2", "p3", "p5", "p9"}
    assert {s.player: s.status for s in two["player_status"]}["p1"] == "gone"
    only = {n: state.FOLDS[n] for n in FACT_NAMES}
    tied = state.world_at(ob.of({"lineups": [ln("01", "s1", 50.0, "", "first"), ln("01", "s1", 50.0, "", "second")]}),
                          "01", only)
    assert player_status(tied, ident)[0].note == "first", "a tie goes to the first in observation order"

    assert {c.player: c for c in one["player_club"]} == {"p1": PlayerClub("p1", "RMA", "POR"),
                                                         "p2": PlayerClub("p2", None, "DEL"),
                                                         "p3": PlayerClub("p3", "BAR", ""),
                                                         "p6": PlayerClub("p6", "RMA", "DEL"),
                                                         "p7": PlayerClub("p7", "VAL", "")}
    assert {v.player: v.value for v in one["player_value"]} == {"p1": 9.0, "p3": 4.0, "p6": 3.0, "p7": 2.0}, (
        "a zero or none value has no row")
    assert {(u.user_id, u.manager) for u in one["users"]} == {("u1", "ann"), ("u2", "bob")}
    assert {(o.player, o.manager) for o in one["owners"]} == {("p1", "ann"), ("p2", "bob")}

    roster = one["player_roster"]
    assert roster == sorted([RosterRow("p1", "", "POR", 0.0), RosterRow("p1", "", "DEL", 9.0),
                             RosterRow("p2", "", "DEL", None), RosterRow("p6", "Ana\t", "DEL", 3.0),
                             RosterRow("p7", "Bo", "MED", 2.0), RosterRow("p5", "n5", "MED", None)], key=repr), (
        "market rows keep zero, none and repeated values; a line-up player listed in the market is not repeated")
    assert "p3" not in {r.player for r in roster}, "a coach is not on the roster, and his line-up row adds nothing"
    assert [r.name for r in roster if r.player == "p6"] == ["Ana\t"], "trim_spaces_only: spaces go, a tab stays"
    assert [r.pos for r in roster if r.player == "p7"] == ["MED"], "unknown_position_is_med"
    assert [r.value for r in roster if r.player == "p5"] == [None], "a line-up-only player has no value"
    assert two["player_roster"] == roster, "a later cut with no new rows has the same roster"

    fl = {e.at + e.kind: e for e in one["feed_ledger"]}
    assert fl["2026-01-02transfer"].buyer == "bob" and fl["2026-01-02transfer"].seller == "ann"
    assert fl["2026-01-04sell"].seller == "ann" and fl["2026-01-04sell"].buyer is None
    assert fl["2026-01-06bonus"].buyer is None and fl["2026-01-06bonus"].seller is None and fl["2026-01-06bonus"].amount == 0.0
    assert {p.player: p.paid for p in one["price_paid"]} == {"p1": 4.0, "p2": 7.0}, "last buy to the owner; no buy by another"
    assert one["offer_ratios"] == [], "a sighting with no team value, or 0, is not the first"
    assert two["offer_ratios"] == [OfferRatio("co1", 2.5), OfferRatio("co2", 2.0), OfferRatio("co5", 1.0)], (
        "each market offer once at its first valued sighting; no zero-money, id-less or non-market offer")

    cr = {c.club: c for c in one["club_rating"]}
    mu = (2.0 + 0.0 + 1.0 + 1.0) / 4
    assert mu == 1.0 and cr["A"] == ClubRating("A", 2, 1.25, 0.75) and cr["B"] == ClubRating("B", 2, 0.75, 1.25)
    assert "C" not in cr, "a result with a goal count none is not a game"
    flat = state.world_at(ob.of({"results_history": [res("01", "A", "B", 0.0, 0.0, "d")]}), "01", only)
    assert club_rating(flat, k) == (), "no_league_mean_no_ratings"

    def rated(rows: list[ob.ResultsHistory]) -> dict[str, ClubRating]:
        return {c.club: c for c in club_rating(state.world_at(ob.of({"results_history": rows}), "09", only), k)}
    away_side = ob.ResultsHistory("01", "fd", 3.0, 0.0, "s", "d1", "hX", "hA", "", "A")
    alone = rated([res("01", "A", "B", 1.0, 1.0, "d0")])
    mixed = rated([res("01", "A", "B", 1.0, 1.0, "d0"), away_side])
    assert set(mixed) == {"A", "B"}, "a blank club is no rating"
    assert mixed["A"].n == 2 and mixed["B"].n == 1, "its opponent's game still counts"
    assert mixed["A"].attack == (1.0 + 0.0 + k * 1.25) / ((2 + k) * 1.25), "the blank side's goals are in the league mean"
    assert alone["B"].attack == 1.0, "a league of one match at its own mean is neutral"
    spelt = ob.ResultsHistory("02", "fd", 1.0, 1.0, "s", "d0", "hAA", "hB", "A", "B")
    once = rated([res("01", "A", "B", 1.0, 1.0, "d0"), spelt])
    assert once["A"].n == 1 and once["B"].n == 1, "one match under two spellings is one match"
    assert rated([res("01", "A", "B", 1.0, 1.0, "d0"), res("01", "A", "B", 1.0, 1.0, "d1")])["A"].n == 2, "two days, two matches"
    blanks = rated([ob.ResultsHistory("01", "fd", 1.0, 1.0, "s", "d0", "hX", "hY", "", ""),
                    ob.ResultsHistory("01", "fd", 2.0, 0.0, "s", "d0", "hZ", "hW", "", "")])
    assert blanks == {}, "two matches between blank clubs on one day are two matches, and rate no one"

    cuts = ["00", "01", "02", "03"]
    alone = {c: at(c) for c in cuts}

    def read(w: state.World) -> dict[str, Any]:
        return {n: sorted(v, key=repr) for n, v in facts(w, ident, k).items()}
    assert state.sweep(obs, cuts, read) == [alone[c] for c in cuts]
    for order in (cuts[::-1], ["02", "00", "02"], ["01"], ["01", "03"]):
        assert state.sweep(obs, order, read) == [alone[c] for c in order]
    more = {n: list(rows) for n, rows in tables.items()}
    more["api_teams"].append(team("03", "a2", "dan", "t2", 99.0))
    more["api_offers"].append(made("03", "o9", "t2", 1.0))
    more["api_activity"].append(act("03", "e9", "buy", "u2", "", "a2", 1.0, "2026-02-01"))
    more["lineups"].append(ln("03", "s1", 1.0))
    later = ob.of(more)
    assert all({n: sorted(v, key=repr) for n, v in facts(state.world_at(later, c), ident, k).items()} == alone[c]
               for c in cuts[:3])
    print("ffcore.current self-test OK")


if __name__ == "__main__":
    _selftest()
