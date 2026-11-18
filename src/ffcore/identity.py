"""Who each player is (ingest), computed from Observations in Python: the crosswalk, the identity maps the sweep
consumes, the kept futbolfantasy identities, the registry, app_links, id_clashes and unmatched. Each is the value
identity.sql and schema.sql give (the SQL stays until the consumers switch); the previous kept identities are an
explicit input of kept_identity.

Named rules, each tested:
  registry_is_newest_row: a registry player is each non-empty ff_id of the market, named (trimmed of spaces) and
    clubbed by his newest row (the last of its observed_at in storage order), whatever the source.
  link_by_value: an app id links to the player whose positive value it equalled at the most distinct market stamps (an
    api_teams or api_market row at its own stamp; an api_players_all row from its stamp until that player's next
    row, a row of value 0 or none still ending the one before it).
  link_needs_a_stem: a pair that shares no name stem (text.stems_meet, the best over its name triples) is no link.
  link_refuses_a_tie: an app id with two best pairs (most stamps, then most stems), or a player with two best app
    ids among the unique ones, is linked to nobody.
  unique_names_a_player: an app id, a slug, a name_key (not empty) or a name_key within a non-empty club names the
    one player that has it, and nobody when two have it; a clash lists the slug and the app id that two claim.
  identity_order: a futbolfantasy row (slug, name, team) is its slug's player, else the player whose id is the name's
    key, else the player the name_key names, else the one the name_key names within the team; else nobody.
  slug_is_first_given: a player's slug is the first of a row about him (by name only, no slugs yet): the line-ups
    in order, then each key's newest starters row (non-empty match, team and slug) at or before the cut.
  kept_survives_sources: the kept identities are those of the current rows, and each previous one whose row is no
    longer among the observations unchanged.
  app_player_only_with_player: Identity keeps an identity row only when it names a player.
  unmatched_value: a player lacking an app id or a slug appears once per market row of that ff_id in each source's
    newest snapshot at or before the cut, with its value (0 for none), once with 0 when there is none; no_app and
    no_slug say what he lacks."""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import defaultdict
from collections.abc import Collection, Iterable, Mapping
from typing import Any, NamedTuple

from ffcore import observations as ob
from ffcore.current import Identity
from ffcore.names import Name
from ffcore.text import stems_meet

__all__ = ["Registered", "Person", "Handle", "Kept", "AppLink", "SlugPlayer", "NamePlayer", "NameClubPlayer", "Clash",
           "Shown", "Unmatched", "app_links", "registry", "crosswalk", "ff_identity_now", "kept_identity",
           "app_player", "slug_player", "name_player", "name_club_player", "id_clashes", "names", "unmatched",
           "identity_of", "NEEDS"]

NEEDS = ("market", "lineups", "starters", "api_teams", "api_market", "api_players_all")
END_OF_TIME = "9999"


class Registered(NamedTuple):
    player_id: str
    name: str
    club_id: str
    app_id: str


class Person(NamedTuple):
    player_id: str
    name: str
    club_id: str
    ff_slug: str
    app_id: str


class Handle(NamedTuple):
    player_slug: str
    player_name: str
    team_slug: str


class Kept(NamedTuple):
    player_slug: str
    player_name: str
    team_slug: str
    player: str | None


class AppLink(NamedTuple):
    app_id: str
    player: str


class SlugPlayer(NamedTuple):
    ff_slug: str
    player: str


class NamePlayer(NamedTuple):
    name_key: str
    player: str


class NameClubPlayer(NamedTuple):
    name_key: str
    club_id: str
    player: str


class Clash(NamedTuple):
    id: str
    value: str


class Shown(NamedTuple):
    player: str
    name: str


class Unmatched(NamedTuple):
    player: str
    name: str
    club: str
    value: float
    no_app: bool
    no_slug: bool


class Evidence(NamedTuple):
    stamps: set[str]
    triples: set[tuple[str, str, str]]


class Rank(NamedTuple):
    stamps: int
    stems: int


def handle_of(row: Any) -> Handle:
    """The identity a futbolfantasy row (line-ups or starters) is read by."""
    return Handle(row.player_slug, row.player_name, row.team_slug)


def key_of(name: str) -> str:
    """name as it is compared: accents, case and punctuation never count."""
    return Name(name).key


def newest_by(rows: Iterable[Any], key: Any) -> dict[Any, Any]:
    """The last of rows (in storage order) for each key(row)."""
    held: dict[Any, Any] = {}
    for row in rows:
        held[key(row)] = row
    return held


def until(obs: ob.Observations, table: str, cut: str) -> tuple[Any, ...]:
    """The rows of table observed at or before cut, in order."""
    return ob.rows_of(obs, table)[:bisect_right(ob.stamps_of(obs, table), cut)]


def newest_snapshot(obs: ob.Observations, table: str, cut: str) -> tuple[Any, ...]:
    """The rows of table that are of their source's newest stamp at or before cut."""
    held = until(obs, table, cut)
    stamp = {r.source: r.observed_at for r in held}
    return tuple(r for r in held if r.observed_at == stamp[r.source])


def positive(value: float | None) -> bool:
    return value is not None and value > 0


def evidence(obs: ob.Observations) -> dict[tuple[str, str], Evidence]:
    """For each (app id, ff_id) the market stamps at which the app value equalled the player's, and the name triples
    (app name, app full name, market name) seen then (rules link_by_value)."""
    market = [r for r in ob.rows_of(obs, "market") if r.ff_id != "" and positive(r.value)]
    at_value: dict[tuple[str, float], list[Any]] = defaultdict(list)
    by_value: dict[float, list[Any]] = defaultdict(list)
    for r in market:
        at_value[r.observed_at, r.value].append(r)
        by_value[r.value].append(r)
    stamps = {v: [r.observed_at for r in held] for v, held in by_value.items()}
    found: dict[tuple[str, str], Evidence] = {}

    def meet(aid: str, f: Any, n1: str, n2: str) -> None:
        e = found.setdefault((aid, f.ff_id), Evidence(set(), set()))
        e.stamps.add(f.observed_at)
        e.triples.add((n1, n2, f.name))
    for table in ("api_teams", "api_market"):
        for r in ob.rows_of(obs, table):
            if positive(r.market_value):
                for f in at_value.get((r.observed_at, r.market_value), ()):
                    meet(r.player_id, f, r.player_name, r.player_name_full)
    held: dict[str, list[Any]] = defaultdict(list)
    for r in ob.rows_of(obs, "api_players_all"):
        held[r.player_id].append(r)
    for aid, rows in held.items():
        for r, nxt in zip(rows, [x.observed_at for x in rows[1:]] + [END_OF_TIME]):
            if positive(r.market_value) and r.market_value in by_value:
                span = stamps[r.market_value]
                for f in by_value[r.market_value][bisect_left(span, r.observed_at):bisect_left(span, nxt)]:
                    meet(aid, f, r.player_name, r.player_name_full)
    return found


def sole_best(ranked: Iterable[tuple[str, Rank, str]]) -> list[tuple[str, Rank, str]]:
    """Of (group, rank, other) the entries that are the one best of their group (none for a group whose best rank
    two entries share)."""
    groups: dict[str, list[tuple[str, Rank, str]]] = defaultdict(list)
    for entry in ranked:
        groups[entry[0]].append(entry)
    kept = []
    for entries in groups.values():
        best = max(e[1] for e in entries)
        top = [e for e in entries if e[1] == best]
        if len(top) == 1:
            kept.append(top[0])
    return kept


def app_links(obs: ob.Observations) -> tuple[AppLink, ...]:
    """The app id to player links the values and names support (rules link_by_value, link_needs_a_stem,
    link_refuses_a_tie), over all rows."""
    pairs = [(aid, Rank(len(e.stamps), max(stems_meet(a, b, c) for a, b, c in e.triples)), ff)
             for (aid, ff), e in evidence(obs).items()]
    by_app = sole_best((aid, rank, ff) for aid, rank, ff in pairs if rank.stems > 0)
    by_player = sole_best((ff, rank, aid) for aid, rank, ff in by_app)
    return tuple(sorted(AppLink(aid, ff) for ff, _, aid in by_player))


def registry(obs: ob.Observations, links: Iterable[AppLink]) -> tuple[Registered, ...]:
    """Every ff_id of the market by his newest row, with his app id or '' (rule registry_is_newest_row)."""
    app = {link.player: link.app_id for link in links}
    newest = newest_by((r for r in ob.rows_of(obs, "market") if r.ff_id != ""), lambda r: r.ff_id)
    return tuple(sorted(Registered(i, r.name.strip(" "), r.club, app.get(i, "")) for i, r in newest.items()))


def unique(items: Iterable[tuple[Any, str]]) -> dict[Any, str]:
    """Of (key, player) the player of each key that has exactly one (rule unique_names_a_player)."""
    held: dict[Any, list[str]] = defaultdict(list)
    for key, player in items:
        held[key].append(player)
    return {key: players[0] for key, players in held.items() if len(players) == 1}


def app_player(people: Iterable[Person]) -> tuple[AppLink, ...]:
    """The app ids one player has, with him."""
    return tuple(sorted(AppLink(k, v) for k, v in unique((p.app_id, p.player_id) for p in people if p.app_id != "").items()))


def slug_player(people: Iterable[Person]) -> tuple[SlugPlayer, ...]:
    """The slugs one player has, with him."""
    return tuple(sorted(SlugPlayer(k, v) for k, v in unique(
        (p.ff_slug, p.player_id) for p in people if p.ff_slug != "").items()))


def name_player(people: Iterable[Person]) -> tuple[NamePlayer, ...]:
    """The name keys one player has, with him."""
    return tuple(sorted(NamePlayer(k, v) for k, v in unique(
        (key_of(p.name), p.player_id) for p in people if key_of(p.name) != "").items()))


def name_club_player(people: Iterable[Person]) -> tuple[NameClubPlayer, ...]:
    """The (name key, club) pairs one player has, with him."""
    return tuple(sorted(NameClubPlayer(k[0], k[1], v) for k, v in unique(
        ((key_of(p.name), p.club_id), p.player_id) for p in people
        if key_of(p.name) != "" and p.club_id != "").items()))


def id_clashes(people: Iterable[Person]) -> tuple[Clash, ...]:
    """The slugs, then the app ids, that two or more players claim."""
    held = tuple(people)
    out: list[Clash] = []
    for column, ids in (("ff_slug", [p.ff_slug for p in held]), ("app_id", [p.app_id for p in held])):
        counts: dict[str, int] = defaultdict(int)
        for i in ids:
            if i != "":
                counts[i] += 1
        out.extend(Clash(column, i) for i in sorted(counts) if counts[i] > 1)
    return tuple(out)


def names(people: Iterable[Person]) -> tuple[Shown, ...]:
    """Each player with his name as the board shows it."""
    return tuple(Shown(p.player_id, Name(p.name).shown) for p in people)


def handles(obs: ob.Observations) -> tuple[Handle, ...]:
    """The distinct futbolfantasy identities of the line-ups and starters rows, sorted."""
    return tuple(sorted({handle_of(r) for table in ("lineups", "starters") for r in ob.rows_of(obs, table)}))


def ff_identity_now(obs: ob.Observations, people: Iterable[Person]) -> tuple[Kept, ...]:
    """Each futbolfantasy identity of obs with its player by rule identity_order."""
    held = tuple(people)
    by_id = {p.player_id for p in held}
    slug = {s.ff_slug: s.player for s in slug_player(held)}
    name = {n.name_key: n.player for n in name_player(held)}
    club = {(n.name_key, n.club_id): n.player for n in name_club_player(held)}
    out: list[Kept] = []
    for h in handles(obs):
        key = key_of(h.player_name)
        player = next((p for p in (slug.get(h.player_slug), key if key in by_id else None, name.get(key),
                                   club.get((key, h.team_slug))) if p is not None), None)
        out.append(Kept(h.player_slug, h.player_name, h.team_slug, player))
    return tuple(out)


def first_slugs(obs: ob.Observations, kept: Iterable[Kept], cut: str) -> dict[str, str]:
    """For each player the first slug a row about him gave (rule slug_is_first_given)."""
    who = {Handle(k.player_slug, k.player_name, k.team_slug): k.player for k in kept}
    starters = newest_by((r for r in until(obs, "starters", cut)
                          if r.match_id != "" and r.team_slug != "" and r.player_slug != ""),
                         lambda r: (r.match_id, r.team_slug, r.player_slug))
    slugs: dict[str, str] = {}
    for r in (*until(obs, "lineups", cut), *starters.values()):
        player = who.get(handle_of(r))
        if player is not None and r.player_slug != "":
            slugs.setdefault(player, r.player_slug)
    return slugs


def crosswalk(obs: ob.Observations, cut: str) -> tuple[Person, ...]:
    """The crosswalk of obs: the registry, with each player's slug."""
    registered = registry(obs, app_links(obs))
    bare = tuple(Person(r.player_id, r.name, r.club_id, "", r.app_id) for r in registered)
    slugs = first_slugs(obs, ff_identity_now(obs, bare), cut)
    return tuple(p._replace(ff_slug=slugs.get(p.player_id, "")) for p in bare)


def kept_identity(now: Iterable[Kept], previous: Iterable[Kept], observed: Collection[Handle]) -> tuple[Kept, ...]:
    """now, with each previous identity whose handle is not in observed (rule kept_survives_sources)."""
    held = tuple(now)
    seen = {Handle(k.player_slug, k.player_name, k.team_slug) for k in held}
    return tuple(sorted((*held, *(k for k in previous if Handle(k.player_slug, k.player_name, k.team_slug)
                                  not in seen and Handle(k.player_slug, k.player_name, k.team_slug) not in observed))))


def unmatched(people: Iterable[Person], obs: ob.Observations, cut: str) -> tuple[Unmatched, ...]:
    """The players without an app id or a slug (rule unmatched_value)."""
    market: dict[str, list[Any]] = defaultdict(list)
    for r in newest_snapshot(obs, "market", cut):
        market[r.ff_id].append(r)
    out: list[Unmatched] = []
    for p in people:
        if p.app_id == "" or p.ff_slug == "":
            values = [0.0 if m.value is None else m.value for m in market.get(p.player_id, ())] or [0.0]
            out.extend(Unmatched(p.player_id, p.name, p.club_id, v, p.app_id == "", p.ff_slug == "") for v in values)
    return tuple(out)


def identity_of(kept: Iterable[Kept], apps: Iterable[AppLink]) -> Identity:
    """The Identity the sweep reads: the kept identities that name a player, and the app players (rule
    app_player_only_with_player)."""
    ff: Mapping[tuple[str, str, str], str] = {(k.player_slug, k.player_name, k.team_slug): k.player
                                              for k in kept if k.player is not None}
    return Identity(ff, {a.app_id: a.player for a in apps})


def _selftest() -> None:
    def mk(at: str, ff: str, name: str, club: str = "c", value: float | None = 1.0, source: str = "ff") -> ob.Market:
        return ob.Market(at, source, value, ff, "DEL", club, name)

    def app(at: str, aid: str, value: float | None, n1: str, n2: str = "") -> ob.ApiTeams:
        return ob.ApiTeams(at, "app", value, None, None, aid, "t", "m", n1, n2, "", "")

    def held(at: str, aid: str, value: float | None, n1: str) -> ob.ApiPlayersAll:
        return ob.ApiPlayersAll(at, "app", value, aid, "t", n1, "", "")

    def lineup(slug: str, name: str, team: str, at: str = "2") -> ob.Lineups:
        return ob.Lineups(at, "ff", None, slug, name, team, "", "")

    def starter(slug: str, name: str, team: str, match: str = "m", at: str = "2") -> ob.Starters:
        return ob.Starters(at, "ff", None, slug, name, team, match, "")

    def people(*rows: tuple[str, str, str, str, str]) -> tuple[Person, ...]:
        return tuple(Person(*r) for r in rows)

    obs = ob.of({"market": [mk("1", "a", " Old Name ", "x"), mk("2", "a", " New  Name ", "y", source="other"),
                            mk("1", "b", "Bee", "z"), mk("3", "", "No Id"), mk("2", "a", "Same Stamp", "w")]})
    got = {r.player_id: r for r in registry(obs, [AppLink("9", "a")])}
    assert set(got) == {"a", "b"} and got["a"] == Registered("a", "Same Stamp", "w", "9") and got["b"].app_id == "", \
        "registry_is_newest_row"

    stamps = ["1", "2", "3"]
    market = [mk(t, "p", "Jonny Castro", value=5.0) for t in stamps] + [mk(t, "q", "Zeta Qux", value=7.0) for t in stamps]
    obs = ob.of({"market": market, "api_teams": [app(t, "A", 5.0, "Jonny Otto") for t in stamps[:2]],
                 "api_players_all": [held("1", "B", 7.0, "Zeta Qux"), held("3", "B", 0.0, "Zeta Qux")]})
    assert app_links(obs) == (AppLink("A", "p"), AppLink("B", "q")) and \
        {k: len(e.stamps) for k, e in evidence(obs).items()} == {("A", "p"): 2, ("B", "q"): 2}, \
        "link_by_value: A at two stamps, B held from 1 until its next row (3), a value-0 row ending it"

    obs = ob.of({"market": [mk("1", "p", "Jonny Castro", value=5.0)], "api_teams": [app("1", "A", 5.0, "Zzzz Yyyy")]})
    assert app_links(obs) == (), "link_needs_a_stem"

    obs = ob.of({"market": [mk(t, i, n, value=5.0) for t in ("1", "2") for i, n in (("p", "Jonny Castro"), ("r", "Jonny Ramos"))]
                 + [mk("1", "s", "Zeta Qux", value=6.0), mk("2", "s", "Zeta Qux", value=6.0)],
                 "api_teams": [app("1", "A", 5.0, "Jonny Otto"), app("1", "B", 6.0, "Zeta Qux"), app("2", "B", 6.0, "Zeta Qux"),
                               app("1", "C", 6.0, "Zeta Qux"), app("2", "C", 6.0, "Zeta Qux")]})
    assert app_links(obs) == (), "link_refuses_a_tie: A has two players, B and C share player s"

    crew = people(("1", "Álvaro García", "betis", "slug1", "7"), ("2", "Alvaro Garcia", "rayo", "", "7"),
                  ("3", "Jose", "", "slug1", "8"), ("4", "Pepe", "betis", "", ""))
    assert [a.app_id for a in app_player(crew)] == ["8"] and slug_player(crew) == () and name_player(crew) == (
        NamePlayer("jose", "3"), NamePlayer("pepe", "4")) and name_club_player(crew) == (
        NameClubPlayer("alvaro garcia", "betis", "1"), NameClubPlayer("alvaro garcia", "rayo", "2"),
        NameClubPlayer("pepe", "betis", "4")), "unique_names_a_player"
    assert id_clashes(crew) == (Clash("ff_slug", "slug1"), Clash("app_id", "7")), "unique_names_a_player: clashes"

    rows = ob.of({"lineups": [lineup("alvaro-f", "x", "espanyol"), lineup("", "Álvaro Fernández", ""),
                              lineup("who", "", ""), lineup("", "Álvaro García", ""), lineup("", "Alvaro Garcia", "rayo"),
                              lineup("", "af", "")]})
    cast = people(("af", "Alvaro Fernandez", "espanyol", "alvaro-f", ""), ("11", "Álvaro García", "betis", "", ""),
                  ("12", "Alvaro Garcia", "rayo", "", ""))
    assert {k.player_name: k.player for k in ff_identity_now(rows, cast)} == {
        "x": "af", "Álvaro Fernández": "af", "": None, "Álvaro García": None, "Alvaro Garcia": "12", "af": "af"}, \
        "identity_order: slug, name key, unique name, name within the team; nobody otherwise"

    obs = ob.of({"market": [mk("1", "p", "Jonny Castro", "alaves"), mk("1", "q", "Zeta Qux", "x")],
                 "lineups": [lineup("first", "Jonny Castro", "alaves", "1"), lineup("second", "Jonny Castro", "alaves", "2"),
                             lineup("zq-late", "Zeta Qux", "x", "3")],
                 "starters": [starter("zq-start", "Zeta Qux", "x", "m1", "1"), starter("zq-newest", "Zeta Qux", "x", "m1", "2"),
                              starter("lone", "Nobody", "x", "", "1")]})
    assert {p.player_id: p.ff_slug for p in crosswalk(obs, "9")} == {"p": "first", "q": "zq-late"}, "slug_is_first_given"
    assert {p.player_id: p.ff_slug for p in crosswalk(obs, "0")} == {"p": "", "q": ""} and \
        {p.player_id: p.ff_slug for p in crosswalk(obs, "1")} == {"p": "first", "q": "zq-start"}, \
        "slug_is_first_given: rows after the cut are not read; the line-ups first"
    obs = ob.of({"starters": [starter("z", "Zeta Qux", "x", "m1", "1"), starter("z", "Other", "x", "m1", "2")],
                 "market": [mk("1", "q", "Zeta Qux", "x")]})
    assert {p.ff_slug for p in crosswalk(obs, "9")} == {""}, "slug_is_first_given: each key's newest starters row"

    old = (Kept("gone", "G", "t", "g"), Kept("s", "S", "t", None), Kept("kept", "K", "t", "old"))
    fresh = (Kept("s", "S", "t", "p"), Kept("kept", "K", "t", "new"))
    got_kept = kept_identity(fresh, old, {Handle("kept", "K", "t")})
    assert sorted(got_kept) == sorted([Kept("gone", "G", "t", "g"), *fresh]) and kept_identity(fresh, (), ()) == tuple(sorted(fresh)), \
        "kept_survives_sources"
    assert identity_of(got_kept, [AppLink("a1", "p")]) == Identity(
        {("gone", "G", "t"): "g", ("kept", "K", "t"): "new", ("s", "S", "t"): "p"}, {"a1": "p"}), "app_player_only_with_player"

    cast = people(("a", "A", "x", "sa", "1"), ("b", "B", "x", "", "2"), ("c", "C", "x", "sc", ""), ("d", "D", "x", "sd", "4"))
    obs = ob.of({"market": [mk("1", "b", "B", value=3.0), mk("2", "b", "B", value=4.0), mk("2", "c", "C", value=None),
                            mk("1", "b", "B", value=9.0, source="old"), mk("5", "c", "C", value=8.0)]})
    assert sorted(unmatched(cast, obs, "2")) == [Unmatched("b", "B", "x", 4.0, False, True), Unmatched("b", "B", "x", 9.0, False, True),
                                               Unmatched("c", "C", "x", 0.0, True, False)] and \
        unmatched(cast, obs, "9")[-1].value == 8.0, "unmatched_value"
    print("ffcore.identity self-test OK")


if __name__ == "__main__":
    _selftest()
