"""The crosswalk stage: who each player is, computed over the observations of the parse cache (identity.crosswalk,
identity.unmatched, identity.id_clashes), and what to tell about it.

Named rules, each tested:
  registry_line: how many players the registry holds and the share of them with a probable-XI slug and with an app id.
  dearest_unmatched_first: the unmatched players are told by value, then player, five at most.
  clashes_by_kind: each kind of id (app_id before ff_slug) that two players claim is told once, with its values in order."""
from __future__ import annotations

import sys
from collections.abc import Iterable

from ffcore import identity, loader, parsed
from ffcore import observations as ob
from ffcore.db import cut

__all__ = ["lines", "main"]

TOLD = 5


def lines(people: Iterable[identity.Person], lacking: Iterable[identity.Unmatched]) -> list[str]:
    """What the stage prints for the crosswalk people and its unmatched (rules registry_line, dearest_unmatched_first,
    clashes_by_kind)."""
    held = tuple(people)
    n = len(held)
    slugs = sum(p.ff_slug != "" for p in held)
    apps = sum(p.app_id != "" for p in held)
    out = ["registry: %d players — %.0f%% carry a probable-XI slug, %.0f%% an app id"
           % (n, 100 * slugs / (n or 1), 100 * apps / (n or 1))]
    for u in sorted(lacking, key=lambda u: (-u.value, u.player))[:TOLD]:
        out.append("  unmatched: %s (%s, %.1fM) has no %s" % (u.name, u.club, u.value / 1e6, " or ".join(
            w for w, no in (("app id", u.no_app), ("probable-XI slug", u.no_slug)) if no)))
    clashes: dict[str, list[str]] = {}
    for c in sorted(identity.id_clashes(held), key=lambda c: (c.id, c.value)):
        clashes.setdefault(c.id, []).append(c.value)
    out.extend("  warn: %s claimed by two players, refused until resolved: %s" % (kind, ", ".join(values))
               for kind, values in clashes.items())
    return out


def players(obs: ob.Observations, at: str) -> list[str]:
    """lines of the crosswalk of obs at the stamp at."""
    people = identity.crosswalk(obs, at)
    return lines(people, identity.unmatched(people, obs, at))


def main() -> None:
    """The crosswalk stage: print the lines of the players the parse cache names as of t."""
    print("\n".join(players(parsed.read(loader.paths_in(), identity.NEEDS), cut())))


def _selftest() -> None:
    def mk(at: str, ff: str, name: str, club: str, value: float | None) -> ob.Market:
        return ob.Market(at, "ff", value, ff, "DEL", club, name)

    def team(at: str, aid: str, name: str, value: float | None) -> ob.ApiTeams:
        return ob.ApiTeams(at, "laliga", value, None, None, aid, "t", "m", name, "", "", "")

    def lineup(at: str, slug: str, name: str, club: str) -> ob.Lineups:
        return ob.Lineups(at, "futbolfantasy", None, slug, name, club, "", "")

    a, b = "2026-08-01T0900Z", "2026-08-02T0900Z"
    obs = ob.of({
        "market": [mk(a, "x", "Xavi Uno", "c", 9e6), mk(a, "y", "Yago Dos", "c", 1e6), mk(a, "af", "Alvaro Fernandez", "espanyol", 5e6),
                   mk(b, "x", "Xavi Uno", "c", 9.1e6)],
        "api_teams": [team(a, "2101", "Alvaro Fernandez", 5e6), team(b, "2101", "Alvaro Fernandez", 5e6)],
        "lineups": [lineup(a, "alvaro-fernandez", "Alvaro Fernandez", "espanyol")],
    })
    got = players(obs, b)
    assert got[0] == "registry: 3 players — 33% carry a probable-XI slug, 33% an app id", got
    assert got[1:] == ["  unmatched: Xavi Uno (c, 9.1M) has no app id or probable-XI slug",
                       "  unmatched: Yago Dos (c, 0.0M) has no app id or probable-XI slug"], \
        "dearest_unmatched_first: valued by the newest snapshot (none: 0), a player with an app id and a slug is not told"
    many = [mk(a, "p%d" % i, "Player %d" % i, "c", float(i)) for i in range(1, 9)]
    told = players(ob.of({"market": many}), a)[1:]
    assert len(told) == TOLD and [u.split("(")[0] for u in told][0] == "  unmatched: Player 8 ", "five, the dearest first"
    pair = (identity.Person("1", "A", "c", "s", "7"), identity.Person("2", "B", "c", "s", "7"), identity.Person("3", "C", "c", "t", ""))
    assert lines(pair, ())[1:] == ["  warn: app_id claimed by two players, refused until resolved: 7",
                                   "  warn: ff_slug claimed by two players, refused until resolved: s"], "clashes_by_kind"
    assert lines((), ())[0] == "registry: 0 players — 0% carry a probable-XI slug, 0% an app id"
    print("crosswalk self-test OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
        raise SystemExit(0)
    main()
