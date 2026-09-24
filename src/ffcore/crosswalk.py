
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
    return "|".join(sorted({v for v in vals if v}))


def _split(cell) -> set:
    return {v for v in (cell or "").split("|") if v}


@dataclass
class Player:
    player_id: str
    name: str = ""
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
        for f in ("name", "club_id", "ff_slug", "af_slug",
                  "app_id", "understat_id"):
            if not getattr(self, f) and getattr(other, f):
                setattr(self, f, getattr(other, f))
        self.app_names |= other.app_names


@dataclass
class Club:
    club_id: str
    market: str = ""
    ff_slug: str = ""
    elo: str = ""
    aliases: set = field(default_factory=set)
    market_id: str = ""
    af_id: str = ""

    def row(self) -> dict:
        return {"club_id": self.club_id, "market": self.market,
                "ff_slug": self.ff_slug, "elo": self.elo,
                "market_id": self.market_id, "af_id": self.af_id,
                "aliases": _join(self.aliases)}


class Crosswalk:

    def __init__(self, players=None, clubs=None):
        self.players: dict[str, Player] = dict(players or {})
        self.clubs: dict[str, Club] = dict(clubs or {})
        self._reindex()

    def _reindex(self) -> None:
        self._by_ff, self._by_af, self._by_app = {}, {}, {}
        self._by_understat = {}
        self._by_app_name = {}
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

    def clashes(self) -> dict:
        return {k: sorted(v) for k, v in sorted(self._clash.items()) if v}

    def player(self, *, name=None, ff_slug=None, af_slug=None, app_id=None,
               understat_id=None, app_name=None) -> str | None:
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
            if not _priced_like(key, "", market_value, index):
                key = None
        if not key:
            key = market.key_for(raw, value=market_value)
            if not _priced_like(key, raw, market_value, index):
                key = None
        if not key and (full or "").strip():
            key = market.key_for(full.strip(), value=market_value)
            if not _priced_like(key, full, market_value, index):
                key = None
        if not key and ledger_owner:
            _got, cands = market.candidates(raw)
            agreed = [c for c in cands if ledger_owner.get(c) == handle]
            if len(agreed) == 1:
                key = agreed[0]
        if not key:
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
        n = len(self.players) or 1
        return {"players": len(self.players),
                "ff": sum(1 for p in self.players.values() if p.ff_slug) / n,
                "af": sum(1 for p in self.players.values() if p.af_slug) / n,
                "app": sum(1 for p in self.players.values() if p.app_id) / n,
                "understat": sum(1 for p in self.players.values()
                                 if p.understat_id) / n,
                "clubs": len(self.clubs)}

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
        for pid, p in other.players.items():
            for f in ("app_id", "ff_slug", "af_slug", "understat_id"):
                val = getattr(p, f)
                if not val:
                    continue
                for cur in self.players.values():
                    if cur.player_id != pid and getattr(cur, f) == val:
                        setattr(cur, f, "")
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
    if xw is not None:
        hit = xw.club(ff_slug=raw, name=raw)
        if hit:
            return hit
    from ffcore.fixture import match_team
    hit = match_team(raw or "", teams)
    return norm(hit) if hit else ""


def _priced_like(key: str, raw: str, market_value, index) -> bool:
    if not key or key == norm(raw):
        return True
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
    try:
        want = float(raw_value)
    except (TypeError, ValueError):
        return None
    if not want:
        return None
    hits = set(_value_index(market).get(want, ()))
    hits.discard("")
    return hits.pop() if len(hits) == 1 else None


_VALUE_INDEX: dict[int, tuple[int, dict]] = {}


def _value_index(market) -> dict:
    """{value: {player key}} over the whole market history, built once per
    market. This used to rescan every row on every call -- 161 calls x ~28k
    rows of float() was 2.9s of a 3.9s crosswalk stage (2026-09-24, profiled).
    Keyed on the row count as well as the object, so appended rows rebuild."""
    hit = _VALUE_INDEX.get(id(market))
    if hit is None or hit[0] != len(market.rows):
        idx: dict[float, set] = {}
        for row in market.rows:
            try:
                idx.setdefault(float(row.get("value")), set()).add(
                    market.key_of(row))
            except (TypeError, ValueError):
                continue
        hit = _VALUE_INDEX[id(market)] = (len(market.rows), idx)
    return hit[1]


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

    for kw in ({"name": "Alvaro Fernandez"}, {"ff_slug": "alvaro-fernandez"},
               {"af_slug": "af-alvaro"}, {"app_id": "2101"},
               {"app_name": "A. Ferllo"}):
        assert xw.player(**kw) == "alvaro fernandez", kw
    assert xw.player(app_name="Jonny Otto") == "jonny castro"
    assert xw.player(name="Álvaro Fernández") == "alvaro fernandez"
    assert xw.player(ff_slug="who-is-this") is None
    assert xw.player() is None

    clash = Crosswalk({
        "carlos romero": Player("carlos romero", app_id="2614"),
        "isaac romero": Player("isaac romero", app_id="2614")})
    assert clash.player(app_id="2614") is None, clash.player(app_id="2614")
    assert clash.clashes() == {"app_id": ["2614"]}, clash.clashes()
    solo = Crosswalk({"carlos romero": Player("carlos romero", app_id="2614"),
                      "isaac romero": Player("isaac romero")})
    assert solo.player(app_id="2614") == "carlos romero"
    assert solo.clashes() == {}

    stale = Crosswalk({"isaac romero": Player("isaac romero", app_id="2614"),
                       "carlos romero": Player("carlos romero")})
    fixed = Crosswalk({"carlos romero": Player("carlos romero",
                                               app_id="2614")})
    stale.merge(fixed)
    assert stale.players["isaac romero"].app_id == ""
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
    st2 = Crosswalk({"isaac romero": Player("isaac romero",
                                            app_names={"C. Romero"}),
                     "carlos romero": Player("carlos romero")})
    st2.merge(Crosswalk({"carlos romero": Player("carlos romero",
                                                 app_names={"C. Romero"})}))
    assert st2.players["isaac romero"].app_names == set()
    assert st2.player(app_name="C. Romero") == "carlos romero"

    assert xw.club(ff_slug="rayo-vallecano") == "rayo"
    assert xw.club(name="Rayo") == "rayo"
    assert xw.club(name="Rayo Vallecano") == "rayo"
    assert xw.club(name="Bilbao") == "athletic"
    assert xw.club(name="Athletic Club") == "athletic"
    assert xw.club(name="Nowhere FC") is None

    thin = Crosswalk({"alvaro fernandez": Player("alvaro fernandez")})
    thin.merge(xw)
    assert thin.player(app_id="2101") == "alvaro fernandez"
    back = Crosswalk({"alvaro fernandez": Player("alvaro fernandez")})
    xw.merge(back)
    assert xw.players["alvaro fernandez"].ff_slug == "alvaro-fernandez"
    assert xw.players["alvaro fernandez"].app_names == {"A. Ferllo"}
    xw.merge(Crosswalk({"new man": Player("new man", "New Man")}))
    assert xw.player(name="New Man") == "new man"

    import tempfile
    with tempfile.TemporaryDirectory() as d:
        pp, cc = os.path.join(d, "p.csv"), os.path.join(d, "c.csv")
        xw.write(pp, cc)
        again = Crosswalk.read(pp, cc)
        assert again.player(app_name="A. Ferllo") == "alvaro fernandez"
        assert again.player(ff_slug="jonny-castro") == "jonny castro"
        assert again.club(name="Bilbao") == "athletic"
        assert set(again.players) == set(xw.players)
        assert Crosswalk.read(os.path.join(d, "nope.csv"), cc).players == {}

    cov = xw.coverage()
    assert cov["players"] == 3 and cov["clubs"] == 2
    assert 0.0 < cov["ff"] < 1.0

    from ffcore.tidy import Market

    assert xw.resolve("2101") == "2101"

    ag = Market([
        {"ff_id": "867", "name": "Álvaro García", "team": "Rayo",
         "value": "20233300", "observed_at": "2026-08-19T1639Z"},
        {"ff_id": "12993", "name": "Álvaro García", "team": "Villarreal",
         "value": "501929", "observed_at": "2026-08-19T1639Z"}])
    assert xw.resolve("Álvaro García", market=ag) is None
    assert xw.resolve("Álvaro García", hint_club="Rayo", market=ag) == "867"
    assert xw.resolve("Álvaro García", hint_price=501929, market=ag) \
        == "12993"

    rm = Market([
        {"ff_id": "1", "name": "Isaac Romero", "team": "Sevilla",
         "value": "6023939", "observed_at": "2026-08-19T1639Z"},
        {"ff_id": "2", "name": "Cristian Romero", "team": "Atletico",
         "value": "47546565", "observed_at": "2026-08-19T1639Z"},
        {"ff_id": "3", "name": "Carlos Romero", "team": "Espanyol",
         "value": "42510131", "observed_at": "2026-08-19T1639Z"}])
    assert xw.resolve("C. Romero", market=rm) is None
    assert xw.resolve("C. Romero", hint_price=45739000, market=rm) == "2"

    nick = Market([{"ff_id": "9", "name": "Pepelu", "team": "Valencia",
                    "value": "7669774", "observed_at": "2026-08-19T1639Z"}])
    assert xw.resolve("nobody knows this nickname", market=nick) is None
    assert xw.resolve("nobody knows this nickname",
                      hint_full="Pepelu", market=nick) == "9"

    moved = Crosswalk({"manu fernandez": Player(
        "manu fernandez", "Manu Fernandez", app_names={"Manuel Fernández"})})
    empty_market = Market([])
    assert moved.resolve("Manuel Fernández") == "manu fernandez"
    assert moved.resolve("Manuel Fernández", market=empty_market) \
        == "manu fernandez"

    assert xw.resolve("Absolutely Nobody") is None
    assert xw.resolve("") is None

    jc = Market([{"ff_id": "77", "name": "Jonny Castro", "team": "Alaves",
                 "value": "5602302", "observed_at": "2026-08-19T1639Z"}])
    elsewhere = Crosswalk({"someone else": Player("someone else", app_id="9")})
    assert elsewhere.resolve("Jonny Castro", hint_app_id="9", market=jc) \
        == "someone else"
    assert elsewhere.resolve("Jonny Castro", market=jc) == "77"

    slugged = Crosswalk({"alvaro fernandez": Player(
        "alvaro fernandez", ff_slug="alvaro-slug", af_slug="af-alvaro")})
    assert slugged.resolve("whatever a page called him",
                           hint_ff_slug="alvaro-slug") == "alvaro fernandez"
    assert slugged.resolve("whatever a page called him",
                           hint_af_slug="af-alvaro") == "alvaro fernandez"
    assert slugged.resolve("Alvaro Fernandez",
                           hint_ff_slug="no-such-slug") == "alvaro fernandez"

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
    with tempfile.TemporaryDirectory() as d:
        pp, cc = os.path.join(d, "p2.csv"), os.path.join(d, "c2.csv")
        us.write(pp, cc)
        again2 = Crosswalk.read(pp, cc)
        assert again2.player(understat_id="555") == "alvaro fernandez"

    teams = ["Alavés", "Getafe", "Celta Vigo", "Osasuna", "Rayo"]
    assert club_key("rayo-vallecano", teams) == "rayo"

    class _XW:
        def club(self, **kw):
            return "rayo" if "vallecano" in str(kw.values()).lower() else None

    assert club_key("Rayo Vallecano", [], xw=_XW()) == "rayo"
    assert club_key("celta", teams, xw=_XW()) == "celta vigo"
    assert club_key("Rayo", teams) == "rayo"
    assert club_key("celta", teams) == "celta vigo"
    assert club_key("zzz-united", teams) == ""
    assert club_key("", teams) == ""

    print("ffcore.crosswalk self-test OK (51 cases)")


if __name__ == "__main__":
    _selftest()
