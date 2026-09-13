"""
ffcore.crosswalk — one player is one player, whatever a feed calls him.

    xw = Crosswalk.load()
    xw.player(ff_slug="antonio-sivera")      -> "antonio sivera"
    xw.player(app_name="A. Ferllo")          -> "alvaro fernandez"
    xw.club(ff_slug="rayo-vallecano")        -> "rayo"

Four feeds (market, futbolfantasy, analiticafantasy, the league API) use
four different id spaces with almost no slug overlap; names are the only
bridge, and every consumer used to re-derive that join independently.
This is the one join every caller should reach for instead.

The id stays `norm(market name)` — every dict in the repo already keys
on it, so adopting this crosswalk is additive. Persisted to disk: a
mapping learned once stays known, and coverage only grows (a mapping is
dropped only when a later feed contradicts it).
"""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass, field

from ffcore.parse import money
from ffcore.text import norm
from ffcore.tidy import price_agrees

__all__ = ["Player", "Club", "Crosswalk", "PLAYER_COLS", "CLUB_COLS",
          "club_key"]

PLAYER_COLS = ["player_id", "name", "club_id", "ff_slug",
               "af_slug", "app_id", "understat_id", "app_names"]
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
    player_id: str                 # the site's own id — the repo's key
    name: str = ""                 # the market's spelling, for display
    club_id: str = ""
    ff_slug: str = ""
    af_slug: str = ""
    app_id: str = ""
    understat_id: str = ""
    app_names: set = field(default_factory=set)

    def row(self) -> dict:
        return {"player_id": self.player_id, "name": self.name,
                "club_id": self.club_id,
                "ff_slug": self.ff_slug, "af_slug": self.af_slug,
                "app_id": self.app_id, "understat_id": self.understat_id,
                "app_names": _join(self.app_names)}

    def absorb(self, other: "Player") -> None:
        """Take anything `other` knows that this row does not. Never
        overwrites a filled field with a blank."""
        for f in ("name", "club_id", "ff_slug", "af_slug",
                  "app_id", "understat_id"):
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
    # The ids the sources publish (market_id: futbolfantasy's data-equipo;
    # af_id: analiticafantasy's data-af-team) — avoids joining clubs by
    # spelling ("Celta" vs "Celta Vigo").
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
        self._by_understat = {}
        self._by_app_name = {}
        # An id two players claim identifies neither — recorded, refused,
        # reported by clashes(), rather than resolved by dict order.
        self._clash: dict[str, set] = {}
        for p in self.players.values():
            for idx, key, label in (
                    (self._by_ff, p.ff_slug, "ff_slug"),
                    (self._by_af, p.af_slug, "af_slug"),
                    (self._by_app, p.app_id, "app_id"),
                    (self._by_understat, p.understat_id, "understat_id")):
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
                   "app_id": self._by_app, "understat_id": self._by_understat,
                   "app_name": self._by_app_name}[label]
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
        """{index: [ids two or more players claim]} — empty when clean."""
        return {k: sorted(v) for k, v in sorted(self._clash.items()) if v}

    def player(self, *, name=None, ff_slug=None, af_slug=None, app_id=None,
               understat_id=None, app_name=None) -> str | None:
        """The repo's key for a player, from whatever id you hold. Exact
        lookups only — None means a genuine gap, never a guess."""
        for key, idx in ((ff_slug, self._by_ff), (af_slug, self._by_af),
                         (app_id, self._by_app),
                         (understat_id, self._by_understat)):
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

    def resolve(self, raw="", *, hint_app_id="", hint_ff_slug="",
                hint_af_slug="", hint_club="", hint_price=None,
                hint_full="", market=None) -> str | None:
        """The repo's one join: given whatever a source calls a player,
        his key. Replaces `player()`/`Market.key_for`/`text.resolve`/
        league.py's `api_key` each re-deriving the same answer.

        Order of trust:
          1. `raw` already a bare digit string — this repo's key is
             numeric for most players, no resolution needed.
          2. An id hint (`hint_app_id`/`hint_ff_slug`/`hint_af_slug`),
             translated through the crosswalk's own table — an id never
             needs guessing, only the id-to-key mapping is learned, and
             `Crosswalk.merge()` displaces a stale one on every rebuild.
          3. `market.key_for(raw, ...)` — the market's current spelling,
             disambiguated by `hint_club`/`hint_price`.
          4. The same against `hint_full`, a longer alternate spelling.
          5. `self.player(app_name=raw)` then `self.player(name=raw)` —
             the crosswalk's own memory, for a spelling the market has
             since moved past.

        None means genuinely unresolved, never a guess or a candidate
        list — an ambiguous name a caller wants to prune by its own
        evidence goes to `market.candidates()` directly.
        """
        raw = (raw or "").strip()
        if raw.isdigit():
            return raw
        hint_app_id = (hint_app_id or "").strip()
        hint_ff_slug = (hint_ff_slug or "").strip()
        hint_af_slug = (hint_af_slug or "").strip()
        if hint_app_id or hint_ff_slug or hint_af_slug:
            got = self.player(app_id=hint_app_id or None,
                              ff_slug=hint_ff_slug or None,
                              af_slug=hint_af_slug or None)
            if got:
                return got
        if market is not None:
            for candidate in (raw, (hint_full or "").strip()):
                if not candidate:
                    continue
                key = market.key_for(candidate, team=hint_club,
                                     value=hint_price)
                if key:
                    return key
        if raw:
            return self.player(app_name=raw) or self.player(name=raw)
        return None

    def resolve_api(self, raw: str, handle: str, market,
                    ledger_owner: dict | None = None, index: list | None = None,
                    market_value=None, full: str = "", app_id: str = ""
                    ) -> str | None:
        """One API row's player, as a key the rest of the repo recognises.

        Five-step chain, id first always: (1) `self.player(app_id=...)`,
        (2) `market.key_for` on the app's nickname, (3) `market.key_for`
        on the full name, (4) the ledger breaking a surname tie, (5) an
        exact market-value match. `index` is the latest market snapshot
        (derived here if omitted, passed in when a caller is looping).
        None means unresolved — must stay visible, never guessed.

        NOT `resolve()`: steps 2-5 aren't `resolve()` calls because
        `_priced_like` applies differently per step (unconditional trust
        on the id, price-validated on the two name guesses) and
        `resolve()`'s single return value doesn't say which step
        answered — the two are shaped for different callers, not a
        duplicate of each other.
        Why (join order, the concrete cases each step exists for):
        docs/notes/league.md#api_key--the-resolution-order-and-why
        """
        from ffcore.tidy import latest_only

        raw = (raw or "").strip()
        if not raw:
            return None
        if market is None:
            return norm(raw) or None
        if index is None:
            index = latest_only(market.rows)
        key = None
        if (app_id or "").strip():
            key = self.player(app_id=app_id.strip())
            # Still checked against the price when one is stated: cheap, and
            # true (never blocking) whenever the row is silent about it.
            if not _priced_like(key, "", market_value, index):
                key = None
        # Each NAME join is price-checked (see _priced_like) — a wrong-value
        # match falls through rather than confidently seating the wrong man in
        # a rival's squad (real case: two Álvaro Garcías, 20.23M vs 0.50M).
        if not key:
            key = market.key_for(raw, value=market_value)
            if not _priced_like(key, raw, market_value, index):
                key = None
        if not key and (full or "").strip():
            key = market.key_for(full.strip(), value=market_value)
            if not _priced_like(key, full, market_value, index):
                key = None
        if not key and ledger_owner:
            # Same producer, same keys — the ledger's owner map is keyed the
            # way the market keys players, so a candidate must be too.
            _got, cands = market.candidates(raw)
            agreed = [c for c in cands if ledger_owner.get(c) == handle]
            if len(agreed) == 1:
                key = agreed[0]
        if not key:
            # Last resort, and the strongest key of the three: an EXACT
            # market value, anywhere in the recorded history. Only when it
            # identifies exactly one player.
            key = _by_exact_value(market_value, market)
        return key or None

    def club(self, *, ff_slug=None, name=None) -> str | None:
        if ff_slug and ff_slug in self._club_ff:
            return self._club_ff[ff_slug]
        for cand in (ff_slug, name):
            if cand and norm(cand) in self._club_alias:
                return self._club_alias[norm(cand)]
        return None

    def coverage(self) -> dict:
        """How much of each feed's namespace the table can answer for."""
        n = len(self.players) or 1
        return {"players": len(self.players),
                "ff": sum(1 for p in self.players.values() if p.ff_slug) / n,
                "af": sum(1 for p in self.players.values() if p.af_slug) / n,
                "app": sum(1 for p in self.players.values() if p.app_id) / n,
                "understat": sum(1 for p in self.players.values()
                                 if p.understat_id) / n,
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
                    r.get("ff_slug", ""),
                    r.get("af_slug", ""), r.get("app_id", ""),
                    r.get("understat_id", ""),
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
        """This table, plus anything `other` learned. Never subtracts,
        with one exception: a unique id `other` assigns to a player is
        taken off anyone else here holding it — a fix that can't displace
        a stale id from a past bad join isn't a fix.
        """
        for pid, p in other.players.items():
            # Every unique identifier the incoming table assigns is taken
            # off whoever else holds it (a name join can leave one id on
            # two players across a rebuild otherwise).
            for f in ("app_id", "ff_slug", "af_slug", "understat_id"):
                val = getattr(p, f)
                if not val:
                    continue
                for cur in self.players.values():
                    if cur.player_id != pid and getattr(cur, f) == val:
                        setattr(cur, f, "")
            # Same for an app_name alias: sim.py looks players up by it,
            # and two holders make it answer by dict order.
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


def club_key(raw, teams, xw=None) -> str:
    """One club, one key, whichever page spelled it — or "" if it will
    not place.

    Three sources name clubs three ways ("Rayo", "rayo-vallecano",
    etc.) — folding case/punctuation alone isn't enough. THE CROSSWALK
    ANSWERS THIS when there is one (clubs.csv, resolved once); the
    fallback is `match_team` against the market's list. "" for an
    unplaceable name, never equal to a real club.
    """
    if xw is not None:
        hit = xw.club(ff_slug=raw, name=raw)
        if hit:
            return hit
    from ffcore.fixture import match_team
    hit = match_team(raw or "", teams)
    return norm(hit) if hit else ""


def _priced_like(key: str, raw: str, market_value, index) -> bool:
    """Does the market price this player roughly the way the app does?

    Checks a GUESS from key_for, never an exact name match (an exact name is
    trusted outright). Reuses tidy.price_agrees()'s tolerance rather than a
    second copy of it. True whenever either side is silent — an absent
    number disproves nothing. Why:
    docs/notes/league.md#price-as-a-name-join-sanity-check-_priced_like
    """
    if not key or key == norm(raw):
        return True                       # the market carries this very name
    if market_value in (None, ""):
        return True
    try:
        theirs = float(str(market_value).strip())
    except (TypeError, ValueError):
        return True
    ours = next((money(r.get("value")) for r in (index or [])
                 if norm(r.get("name")) == key), None)
    if not ours:
        return True
    return price_agrees(theirs, ours)


def _by_exact_value(raw_value, market) -> str | None:
    """The one market player who has ever been worth exactly this, or None.

    Exact match (no tolerance), searched across all recorded history (not
    just the newest snapshot), unique per PLAYER not per row. Why:
    docs/notes/league.md#exact-value-join-as-a-last-resort-_by_exact_value
    """
    try:
        want = float(raw_value)
    except (TypeError, ValueError):
        return None
    if not want:
        return None
    # IN THE MARKET'S OWN KEYS. This answered norm(name), which was the key
    # only for as long as the market keyed on names — and a key the index
    # does not contain resolves nowhere, which reads downstream as a player
    # nobody owns rather than as a join that missed.
    hits = set()
    for row in market.rows:
        try:
            if float(row.get("value")) == want:
                hits.add(market.key_of(row))
        except (TypeError, ValueError):
            continue
    hits.discard("")
    return hits.pop() if len(hits) == 1 else None


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
            "alvaro-fernandez", "af-alvaro", "2101",
            app_names={"A. Ferllo"}),
        "jonny castro": Player("jonny castro", "Jonny Castro", "alaves",
                               ff_slug="jonny-castro",
                               app_names={"Jonny Otto"}),
    }, {"rayo": Club("rayo", "Rayo", "rayo-vallecano", "Rayo Vallecano"),
        "athletic": Club("athletic", "Athletic", "athletic", "Bilbao",
                         {"Athletic Club"})})

    # -- every feed's key reaches the same player --------------------------
    for kw in ({"name": "Alvaro Fernandez"}, {"ff_slug": "alvaro-fernandez"},
               {"af_slug": "af-alvaro"}, {"app_id": "2101"},
               {"app_name": "A. Ferllo"}):
        assert xw.player(**kw) == "alvaro fernandez", kw
    # The app's abbreviation resolves, so a rival's clause-carrying player
    # is buyable rather than invisible.
    assert xw.player(app_name="Jonny Otto") == "jonny castro"
    # An accented or punctuated spelling folds, because the id is norm()'d.
    assert xw.player(name="Álvaro Fernández") == "alvaro fernandez"
    assert xw.player(ff_slug="who-is-this") is None
    assert xw.player() is None

    # -- a unique id belongs to one player; a clash is not an answer -------
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

    # A corrected id displaces a stale one across a merge.
    stale = Crosswalk({"isaac romero": Player("isaac romero", app_id="2614"),
                       "carlos romero": Player("carlos romero")})
    fixed = Crosswalk({"carlos romero": Player("carlos romero",
                                               app_id="2614")})
    stale.merge(fixed)
    assert stale.players["isaac romero"].app_id == ""
    # The same for a slug on a ghost row a key-shape change left behind.
    ghost = Crosswalk({
        "moussa diarra@malaga": Player("moussa diarra@malaga",
                                       ff_slug="moussa-diarra"),
        "moussa diarra": Player("moussa diarra")})
    ghost.merge(Crosswalk({"moussa diarra": Player("moussa diarra",
                                                   ff_slug="moussa-diarra")}))
    assert ghost.players["moussa diarra@malaga"].ff_slug == ""
    assert ghost.player(ff_slug="moussa-diarra") == "moussa diarra"
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
    # A city name (Club Elo's convention) resolves via the alias table.
    assert xw.club(name="Bilbao") == "athletic"
    assert xw.club(name="Athletic Club") == "athletic"
    assert xw.club(name="Nowhere FC") is None

    # -- merging never subtracts -------------------------------------------
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
        # A table that doesn't exist yet reads as empty, not a crash.
        assert Crosswalk.read(os.path.join(d, "nope.csv"), cc).players == {}

    cov = xw.coverage()
    assert cov["players"] == 3 and cov["clubs"] == 2
    assert 0.0 < cov["ff"] < 1.0

    # -- resolve(): the one join ---------------------------------------------
    from ffcore.tidy import Market

    # A bare digit is already this repo's key — no lookup needed.
    assert xw.resolve("2101") == "2101"

    # Two men share a name; the market refuses without a discriminator, and
    # resolve() must refuse too rather than pick one.
    ag = Market([
        {"ff_id": "867", "name": "Álvaro García", "team": "Rayo",
         "value": "20233300", "observed_at": "2026-08-19T1639Z"},
        {"ff_id": "12993", "name": "Álvaro García", "team": "Villarreal",
         "value": "501929", "observed_at": "2026-08-19T1639Z"}])
    assert xw.resolve("Álvaro García", market=ag) is None
    assert xw.resolve("Álvaro García", hint_club="Rayo", market=ag) == "867"
    assert xw.resolve("Álvaro García", hint_price=501929, market=ag) \
        == "12993"

    # An abbreviated surname three players share, settled by price only.
    rm = Market([
        {"ff_id": "1", "name": "Isaac Romero", "team": "Sevilla",
         "value": "6023939", "observed_at": "2026-08-19T1639Z"},
        {"ff_id": "2", "name": "Cristian Romero", "team": "Atletico",
         "value": "47546565", "observed_at": "2026-08-19T1639Z"},
        {"ff_id": "3", "name": "Carlos Romero", "team": "Espanyol",
         "value": "42510131", "observed_at": "2026-08-19T1639Z"}])
    assert xw.resolve("C. Romero", market=rm) is None
    assert xw.resolve("C. Romero", hint_price=45739000, market=rm) == "2"

    # A full name reaches a player the abbreviated nickname alone cannot.
    nick = Market([{"ff_id": "9", "name": "Pepelu", "team": "Valencia",
                    "value": "7669774", "observed_at": "2026-08-19T1639Z"}])
    assert xw.resolve("nobody knows this nickname", market=nick) is None
    assert xw.resolve("nobody knows this nickname",
                      hint_full="Pepelu", market=nick) == "9"

    # A roster line typed against a spelling the market has since moved
    # past — only the crosswalk's app_name memory still has it.
    moved = Crosswalk({"manu fernandez": Player(
        "manu fernandez", "Manu Fernandez", app_names={"Manuel Fernández"})})
    empty_market = Market([])
    assert moved.resolve("Manuel Fernández") == "manu fernandez"
    assert moved.resolve("Manuel Fernández", market=empty_market) \
        == "manu fernandez"

    # Nothing anywhere knows this name — refuse, don't guess.
    assert xw.resolve("Absolutely Nobody") is None
    assert xw.resolve("") is None

    # An id beats a name, even one that would otherwise resolve cleanly.
    jc = Market([{"ff_id": "77", "name": "Jonny Castro", "team": "Alaves",
                 "value": "5602302", "observed_at": "2026-08-19T1639Z"}])
    elsewhere = Crosswalk({"someone else": Player("someone else", app_id="9")})
    assert elsewhere.resolve("Jonny Castro", hint_app_id="9", market=jc) \
        == "someone else"
    # And with no id offered at all, the name still resolves on its own.
    assert elsewhere.resolve("Jonny Castro", market=jc) == "77"

    # ff_slug/af_slug are exact ids too, and outrank the market the same way.
    slugged = Crosswalk({"alvaro fernandez": Player(
        "alvaro fernandez", ff_slug="alvaro-slug", af_slug="af-alvaro")})
    assert slugged.resolve("whatever a page called him",
                           hint_ff_slug="alvaro-slug") == "alvaro fernandez"
    assert slugged.resolve("whatever a page called him",
                           hint_af_slug="af-alvaro") == "alvaro fernandez"
    # An id nothing knows changes nothing — falls through to the name path.
    assert slugged.resolve("Alvaro Fernandez",
                           hint_ff_slug="no-such-slug") == "alvaro fernandez"

    # -- understat_id: a fourth identity space, same join/displace rules --
    us = Crosswalk({"alvaro fernandez": Player(
        "alvaro fernandez", "Alvaro Fernandez", understat_id="555")})
    assert us.player(understat_id="555") == "alvaro fernandez"
    assert "understat" in us.coverage()
    us_clash = Crosswalk({
        "carlos romero": Player("carlos romero", understat_id="9"),
        "isaac romero": Player("isaac romero", understat_id="9")})
    assert us_clash.player(understat_id="9") is None
    assert us_clash.clashes() == {"understat_id": ["9"]}
    us_stale = Crosswalk({"isaac romero": Player("isaac romero",
                                                  understat_id="9"),
                          "carlos romero": Player("carlos romero")})
    us_stale.merge(Crosswalk({"carlos romero": Player(
        "carlos romero", understat_id="9")}))
    assert us_stale.players["isaac romero"].understat_id == ""
    assert us_stale.player(understat_id="9") == "carlos romero"
    # A round trip through the files carries it too.
    with tempfile.TemporaryDirectory() as d:
        pp, cc = os.path.join(d, "p2.csv"), os.path.join(d, "c2.csv")
        us.write(pp, cc)
        again2 = Crosswalk.read(pp, cc)
        assert again2.player(understat_id="555") == "alvaro fernandez"

    # -- club_key: one club, one key, whichever page spelled it -------------
    # ONE CLUB, TWO SPELLINGS, and this is not hypothetical: the market calls
    # them "Rayo", the fixture page "rayo-vallecano". Both sides go through
    # the MARKET's list of clubs, which is the one canonical spelling there is.
    teams = ["Alavés", "Getafe", "Celta Vigo", "Osasuna", "Rayo"]
    assert club_key("rayo-vallecano", teams) == "rayo"

    # THE CROSSWALK ANSWERS FIRST when there is one, because it resolved this
    # once with every feed in front of it instead of guessing per call.
    class _XW:
        def club(self, **kw):
            return "rayo" if "vallecano" in str(kw.values()).lower() else None

    assert club_key("Rayo Vallecano", [], xw=_XW()) == "rayo"
    # ...and the fallback still works where the table has nothing.
    assert club_key("celta", teams, xw=_XW()) == "celta vigo"
    assert club_key("Rayo", teams) == "rayo"
    assert club_key("celta", teams) == "celta vigo"
    # No club, or one nothing can place, is not "some club" — it is nothing,
    # and nothing is never equal to a club that has played.
    assert club_key("zzz-united", teams) == ""
    assert club_key("", teams) == ""

    print("ffcore.crosswalk self-test OK (51 cases)")


if __name__ == "__main__":
    _selftest()
