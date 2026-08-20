"""
ffcore.crosswalk — one player is one player, whatever a feed calls him.

    xw = Crosswalk.load()
    xw.player(ff_slug="antonio-sivera")      -> "antonio sivera"
    xw.player(app_name="A. Ferllo")          -> "alvaro fernandez"
    xw.club(ff_slug="rayo-vallecano")        -> "rayo"

FOUR FEEDS, FOUR IDENTITY SPACES, AND NAMES AS THE ONLY BRIDGE. The market
publishes its own slugs, futbolfantasy publishes different ones, analiticafan-
tasy different ones again, and the league's API has integer ids and its own
abbreviated spellings. Measured across the store on 2026-08-18, not one pair
of slug spaces overlapped at all — market to futbolfantasy, 0 of 553 — and the
name joins that had to carry the load ran anywhere from 25% to 93%:

    starters -> futbolfantasy  by slug   93%
    starters -> market         by name   25%
    api_teams -> market        by name   25%
    analitica -> futbolfantasy by slug    0%

So every consumer re-derived the join, and each did it slightly differently.
That is not a theoretical tidiness complaint: decide.py's own weaker version
hid five rival players who could not then be bought, and a grader joining
confirmed line-ups to market rows matched a quarter of them and fitted a model
on the wreckage. Seven functions in this repo exist to paper over it — norm,
resolve, key_for, api_key, _by_exact_value, match_team, club_key.

WHAT THIS IS, AND WHAT IT IS NOT. It is a crosswalk, not a renumbering. The id
stays `norm(market name)`, which is what every dict in the repo is already
keyed by, so adopting this is additive: a caller that has a futbolfantasy slug
and wants the repo's key can finally ask, and a caller that already has the
key carries on unchanged.

IT IS WRITTEN DOWN, AND THAT IS THE POINT. Resolution runs against whatever
the store holds today, and the store grows: a player the API named once and
never again is nameable forever after the run that saw it. Merging into the
file rather than rebuilding it means a mapping once learned is never lost, and
coverage only goes up. A mapping is dropped only when a feed contradicts it.
"""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass, field

from ffcore.text import norm

__all__ = ["Player", "Club", "Crosswalk", "PLAYER_COLS", "CLUB_COLS"]

PLAYER_COLS = ["player_id", "name", "club_id", "market_slug", "ff_slug",
               "af_slug", "app_id", "app_names"]
CLUB_FIELDS = ["club_id", "market", "ff_slug", "elo", "market_id", "af_id",
               "aliases"]
CLUB_COLS = CLUB_FIELDS


def _join(vals) -> str:
    """A set of spellings as one cell, stable order so a diff means a change."""
    return "|".join(sorted({v for v in vals if v}))


def _split(cell) -> set:
    return {v for v in (cell or "").split("|") if v}


@dataclass
class Player:
    player_id: str                 # norm(market name) — the repo's own key
    name: str = ""                 # the market's spelling, for display
    club_id: str = ""
    market_slug: str = ""
    ff_slug: str = ""
    af_slug: str = ""
    app_id: str = ""
    app_names: set = field(default_factory=set)

    def row(self) -> dict:
        return {"player_id": self.player_id, "name": self.name,
                "club_id": self.club_id, "market_slug": self.market_slug,
                "ff_slug": self.ff_slug, "af_slug": self.af_slug,
                "app_id": self.app_id, "app_names": _join(self.app_names)}

    def absorb(self, other: "Player") -> None:
        """Take anything `other` knows that this row does not.

        NEVER OVERWRITES a key with a blank. A feed that skipped a run must not
        erase what an earlier run learned from it — that is the difference
        between a crosswalk that improves and one that flickers.
        """
        for f in ("name", "club_id", "market_slug", "ff_slug", "af_slug",
                  "app_id"):
            if not getattr(self, f) and getattr(other, f):
                setattr(self, f, getattr(other, f))
        self.app_names |= other.app_names


@dataclass
class Club:
    club_id: str                   # norm(market team)
    market: str = ""
    ff_slug: str = ""
    elo: str = ""
    aliases: set = field(default_factory=set)
    # THE IDS THE SOURCES PUBLISH. market_id is futbolfantasy's data-equipo,
    # on every market row; af_id is analiticafantasy's data-af-team, on both
    # crests of every match anchor. Neither was read, so clubs were joined by
    # spelling — "Celta" against "Celta Vigo", "Betis" against "Real Betis" —
    # through a substring matcher that could return two candidates and then
    # answer with neither.
    market_id: str = ""
    af_id: str = ""

    def row(self) -> dict:
        return {"club_id": self.club_id, "market": self.market,
                "ff_slug": self.ff_slug, "elo": self.elo,
                "market_id": self.market_id, "af_id": self.af_id,
                "aliases": _join(self.aliases)}


class Crosswalk:
    """Every feed's name for every player and club, in one table."""

    def __init__(self, players=None, clubs=None):
        self.players: dict[str, Player] = dict(players or {})
        self.clubs: dict[str, Club] = dict(clubs or {})
        self._reindex()

    def _reindex(self) -> None:
        self._by_ff, self._by_af, self._by_app = {}, {}, {}
        self._by_app_name, self._by_market_slug = {}, {}
        # AN IDENTIFIER TWO PLAYERS CLAIM IDENTIFIES NEITHER. These indexes
        # were plain assignment, so the second writer won and the answer
        # depended on dict order. That is the worst failure available here: a
        # unique id is trusted precisely BECAUSE it does not guess, and a
        # silent coin toss wearing an id's clothes is trusted the same way.
        # A clash is recorded, refused, and reported by clashes().
        self._clash: dict[str, set] = {}
        for p in self.players.values():
            for idx, key, label in (
                    (self._by_ff, p.ff_slug, "ff_slug"),
                    (self._by_af, p.af_slug, "af_slug"),
                    (self._by_app, p.app_id, "app_id"),
                    (self._by_market_slug, p.market_slug, "market_slug")):
                if not key:
                    continue
                if key in idx and idx[key] != p.player_id:
                    self._clash.setdefault(label, set()).add(key)
                idx[key] = p.player_id
            for n in p.app_names:
                k = norm(n)
                if not k:
                    continue
                if k in self._by_app_name \
                        and self._by_app_name[k] != p.player_id:
                    self._clash.setdefault("app_name", set()).add(k)
                self._by_app_name[k] = p.player_id
        for label, keys in self._clash.items():
            idx = {"ff_slug": self._by_ff, "af_slug": self._by_af,
                   "app_id": self._by_app, "app_name": self._by_app_name,
                   "market_slug": self._by_market_slug}[label]
            for k in keys:
                idx.pop(k, None)
        self._club_ff = {c.ff_slug: c.club_id for c in self.clubs.values()
                         if c.ff_slug}
        self._club_alias = {}
        for c in self.clubs.values():
            for a in {c.market, c.elo, c.club_id} | c.aliases:
                if a:
                    self._club_alias[norm(a)] = c.club_id

    # -- asking ------------------------------------------------------------
    def clashes(self) -> dict:
        """{index: [ids two or more players claim]} — empty when all is well.

        Surfaced rather than swallowed: a clash means one of the joins that
        wrote an id down was wrong, and the row it wrote is still in the
        table. The report says so instead of the index quietly picking one.
        """
        return {k: sorted(v) for k, v in sorted(self._clash.items()) if v}

    def player(self, *, name=None, ff_slug=None, af_slug=None, app_id=None,
               app_name=None, market_slug=None) -> str | None:
        """The repo's key for a player, from whatever key you happen to hold.

        Exact lookups only. Nothing here guesses: the guessing happened once,
        when the table was built, with every feed in front of it — and the
        answer was written down. A caller that gets None has found a genuine
        gap, and a gap that stays visible is one that gets fixed.
        """
        for key, idx in ((ff_slug, self._by_ff), (af_slug, self._by_af),
                         (app_id, self._by_app),
                         (market_slug, self._by_market_slug)):
            if key and key in idx:
                return idx[key]
        if name:
            k = norm(name)
            if k in self.players:
                return k
        if app_name:
            k = norm(app_name)
            if k in self._by_app_name:
                return self._by_app_name[k]
            if k in self.players:
                return k
        return None

    def club(self, *, ff_slug=None, name=None) -> str | None:
        if ff_slug and ff_slug in self._club_ff:
            return self._club_ff[ff_slug]
        for cand in (ff_slug, name):
            if cand and norm(cand) in self._club_alias:
                return self._club_alias[norm(cand)]
        return None

    def coverage(self) -> dict:
        """How much of each feed's namespace the table can answer for.

        Printed by the report. A crosswalk nobody measures is a crosswalk that
        quietly stops covering a feed the day its format changes.
        """
        n = len(self.players) or 1
        return {"players": len(self.players),
                "ff": sum(1 for p in self.players.values() if p.ff_slug) / n,
                "af": sum(1 for p in self.players.values() if p.af_slug) / n,
                "app": sum(1 for p in self.players.values() if p.app_id) / n,
                "clubs": len(self.clubs)}

    # -- persistence -------------------------------------------------------
    @classmethod
    def read(cls, players_path, clubs_path) -> "Crosswalk":
        players, clubs = {}, {}
        for r in _rows(players_path):
            pid = r.get("player_id")
            if pid:
                players[pid] = Player(
                    pid, r.get("name", ""), r.get("club_id", ""),
                    r.get("market_slug", ""), r.get("ff_slug", ""),
                    r.get("af_slug", ""), r.get("app_id", ""),
                    _split(r.get("app_names")))
        for r in _rows(clubs_path):
            cid = r.get("club_id")
            if cid:
                clubs[cid] = Club(cid, r.get("market", ""),
                                  r.get("ff_slug", ""), r.get("elo", ""),
                                  _split(r.get("aliases")),
                                  r.get("market_id", ""), r.get("af_id", ""))
        return cls(players, clubs)

    def write(self, players_path, clubs_path) -> None:
        _write(players_path, PLAYER_COLS,
               [p.row() for p in sorted(self.players.values(),
                                        key=lambda p: p.player_id)])
        _write(clubs_path, CLUB_COLS,
               [c.row() for c in sorted(self.clubs.values(),
                                        key=lambda c: c.club_id)])

    def merge(self, other: "Crosswalk") -> "Crosswalk":
        """This table, plus anything `other` learned.

        Never subtracts, WITH ONE EXCEPTION: a unique id that `other` gives
        to a different player is taken off the player holding it here. Never
        subtracting is right for a feed that skipped a sweep and wrong for an
        identifier — app_id 2614 was written onto Isaac Romero by a name join
        that has since been fixed, and absorb() kept it there for every
        rebuild afterwards, so the corrected table and the stale one both
        claimed it. A fix that cannot displace what the bug wrote down is not
        a fix.
        """
        for pid, p in other.players.items():
            # EVERY UNIQUE IDENTIFIER THE INCOMING TABLE ASSIGNS IS TAKEN OFF
            # WHOEVER ELSE HOLDS IT. app_id showed why: a name join wrote
            # 2614 onto Isaac Romero, the join was fixed, and absorb() kept
            # the wrong row alive through every rebuild. The slugs had the
            # same disease from a different cause — when shared_names stopped
            # splitting `moussa diarra`, the old `moussa diarra@malaga` row
            # stayed behind still holding his ff_slug and market_slug, so one
            # player held one slug under two keys.
            for f in ("app_id", "ff_slug", "af_slug", "market_slug"):
                val = getattr(p, f)
                if not val:
                    continue
                for cur in self.players.values():
                    if cur.player_id != pid and getattr(cur, f) == val:
                        setattr(cur, f, "")
            # An alias the incoming table hangs on THIS player comes off
            # everyone else, for the reason above: app_name is a key
            # sim.py's market model looks players up by, and two holders
            # make it answer by dict order or not at all.
            if p.app_names:
                fresh = {norm(n) for n in p.app_names}
                for cur in self.players.values():
                    if cur.player_id != pid and cur.app_names:
                        cur.app_names = {n for n in cur.app_names
                                         if norm(n) not in fresh}
            if pid in self.players:
                self.players[pid].absorb(p)
            else:
                self.players[pid] = p
        for cid, c in other.clubs.items():
            cur = self.clubs.get(cid)
            if cur is None:
                self.clubs[cid] = c
            else:
                cur.market = cur.market or c.market
                cur.ff_slug = cur.ff_slug or c.ff_slug
                cur.elo = cur.elo or c.elo
                cur.market_id = cur.market_id or c.market_id
                cur.af_id = cur.af_id or c.af_id
                cur.aliases |= c.aliases
        self._reindex()
        return self


def _rows(path) -> list[dict]:
    if not path or not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _write(path, cols, rows) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


def _selftest() -> None:
    xw = Crosswalk({
        "alvaro fernandez": Player(
            "alvaro fernandez", "Alvaro Fernandez", "espanyol",
            "alvaro-fernandez-m", "alvaro-fernandez", "af-alvaro", "2101",
            {"A. Ferllo"}),
        "jonny castro": Player("jonny castro", "Jonny Castro", "alaves",
                               ff_slug="jonny-castro",
                               app_names={"Jonny Otto"}),
    }, {"rayo": Club("rayo", "Rayo", "rayo-vallecano", "Rayo Vallecano"),
        "athletic": Club("athletic", "Athletic", "athletic", "Bilbao",
                         {"Athletic Club"})})

    # -- every feed's key reaches the same player --------------------------
    for kw in ({"name": "Alvaro Fernandez"}, {"ff_slug": "alvaro-fernandez"},
               {"af_slug": "af-alvaro"}, {"app_id": "2101"},
               {"app_name": "A. Ferllo"},
               {"market_slug": "alvaro-fernandez-m"}):
        assert xw.player(**kw) == "alvaro fernandez", kw
    # THE ONE THAT USED TO COST MONEY: the app's abbreviation resolves, so a
    # rival's player carrying a buyout clause is buyable rather than invisible.
    assert xw.player(app_name="Jonny Otto") == "jonny castro"
    # An accented or punctuated spelling folds, because the id is norm()'d.
    assert xw.player(name="Álvaro Fernández") == "alvaro fernandez"
    # A key nothing knows is None, never a guess. The guessing happened once,
    # when the table was built, with every feed in front of it.
    assert xw.player(ff_slug="who-is-this") is None
    assert xw.player() is None

    # -- A UNIQUE ID BELONGS TO ONE PLAYER, AND A CLASH IS NOT AN ANSWER ---
    # REAL, AND IT OUTLIVED THE BUG THAT MADE IT. app_id 2614 is the app's
    # own id for Carlos Romero. A name join that read the app's abbreviated
    # "C. Romero" as Isaac Romero wrote 2614 onto Isaac; the join was fixed,
    # Carlos correctly took 2614 too, and NOTHING TOOK IT OFF ISAAC. Two
    # players then claimed one id and the index answered with whichever it
    # reindexed last — a coin toss wearing an identifier's clothes.
    clash = Crosswalk({
        "carlos romero": Player("carlos romero", app_id="2614"),
        "isaac romero": Player("isaac romero", app_id="2614")})
    assert clash.player(app_id="2614") is None, clash.player(app_id="2614")
    assert clash.clashes() == {"app_id": ["2614"]}, clash.clashes()
    # One holder, one answer.
    solo = Crosswalk({"carlos romero": Player("carlos romero", app_id="2614"),
                      "isaac romero": Player("isaac romero")})
    assert solo.player(app_id="2614") == "carlos romero"
    assert solo.clashes() == {}

    # A CORRECTED ID DISPLACES A STALE ONE ACROSS A MERGE. Never-subtracts
    # is right for a feed that skipped a sweep; for an identifier it would
    # keep the wrong answer alive forever.
    stale = Crosswalk({"isaac romero": Player("isaac romero", app_id="2614"),
                       "carlos romero": Player("carlos romero")})
    fixed = Crosswalk({"carlos romero": Player("carlos romero",
                                               app_id="2614")})
    stale.merge(fixed)
    assert stale.players["isaac romero"].app_id == ""
    # The same for a slug, which went stale for a different reason: a key
    # that changed shape left a ghost row still holding the identifiers.
    ghost = Crosswalk({
        "moussa diarra@malaga": Player("moussa diarra@malaga",
                                       market_slug="10832",
                                       ff_slug="moussa-diarra"),
        "moussa diarra": Player("moussa diarra")})
    ghost.merge(Crosswalk({"moussa diarra": Player("moussa diarra",
                                                   market_slug="10832",
                                                   ff_slug="moussa-diarra")}))
    assert ghost.players["moussa diarra@malaga"].market_slug == ""
    assert ghost.player(market_slug="10832") == "moussa diarra"
    assert ghost.clashes() == {}
    assert stale.player(app_id="2614") == "carlos romero"
    assert stale.clashes() == {}
    # The display name moves with the id, and for the same reason.
    st2 = Crosswalk({"isaac romero": Player("isaac romero",
                                            app_names={"C. Romero"}),
                     "carlos romero": Player("carlos romero")})
    st2.merge(Crosswalk({"carlos romero": Player("carlos romero",
                                                 app_names={"C. Romero"})}))
    assert st2.players["isaac romero"].app_names == set()
    assert st2.player(app_name="C. Romero") == "carlos romero"

    # -- clubs, which have three spellings and one identity ----------------
    assert xw.club(ff_slug="rayo-vallecano") == "rayo"
    assert xw.club(name="Rayo") == "rayo"
    assert xw.club(name="Rayo Vallecano") == "rayo"      # the Elo spelling
    # Club Elo names two Spanish clubs after their city; the alias lives in
    # the table now rather than in a constant inside the fixture module.
    assert xw.club(name="Bilbao") == "athletic"
    assert xw.club(name="Athletic Club") == "athletic"
    assert xw.club(name="Nowhere FC") is None

    # -- merging never subtracts -------------------------------------------
    # A feed that skipped a run must not erase what an earlier run learned
    # from it. This is the difference between a table that improves and one
    # that flickers with whatever the last sweep happened to fetch.
    thin = Crosswalk({"alvaro fernandez": Player("alvaro fernandez")})
    thin.merge(xw)
    assert thin.player(app_id="2101") == "alvaro fernandez"
    back = Crosswalk({"alvaro fernandez": Player("alvaro fernandez")})
    xw.merge(back)
    assert xw.players["alvaro fernandez"].ff_slug == "alvaro-fernandez"
    assert xw.players["alvaro fernandez"].app_names == {"A. Ferllo"}
    # A player only the new table knows about is added, not ignored.
    xw.merge(Crosswalk({"new man": Player("new man", "New Man")}))
    assert xw.player(name="New Man") == "new man"

    # -- a round trip through the files -------------------------------------
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        pp, cc = os.path.join(d, "p.csv"), os.path.join(d, "c.csv")
        xw.write(pp, cc)
        again = Crosswalk.read(pp, cc)
        assert again.player(app_name="A. Ferllo") == "alvaro fernandez"
        assert again.player(ff_slug="jonny-castro") == "jonny castro"
        assert again.club(name="Bilbao") == "athletic"
        assert set(again.players) == set(xw.players)
        # Reading a table that does not exist yet is an empty one, not a crash:
        # the first run has to be able to build it.
        assert Crosswalk.read(os.path.join(d, "nope.csv"), cc).players == {}

    cov = xw.coverage()
    assert cov["players"] == 3 and cov["clubs"] == 2
    assert 0.0 < cov["ff"] < 1.0

    print("ffcore.crosswalk self-test OK (28 cases)")


if __name__ == "__main__":
    _selftest()
