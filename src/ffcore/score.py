
from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any, NamedTuple

from ffcore.parse import Rows, text
from ffcore.startprob import PICK_K, StartOdds
from ffcore.names import Name
from ffcore.rules import slot
from stats import shrink


__all__ = ["Rating", "Rates", "Scored", "Scorer", "Tally", "fit_promoted_discount"]


SHRINK_K = 8.0    # was 4: replayed over 4 jornadas it forecast better in each (variant_gap: +0.071 +-0.06 in squared error); 2 is clearly worse, 16 not established

PPM_BANDS = 4.0  # ppm_prior: his position's median in his quarter of value (was 0, position only: variant_ahead, better at 0-4 jornadas ahead, every interval above 0)

PROMOTED_DISCOUNT = 0.70

PROMOTED_DISCOUNT_K = 50.0

class Scored(NamedTuple):
    """A player's points in a jornada as the points table first showed them."""
    key: str
    jornada: int
    pts: float
    games: float
    at: str


# A player's season so far, by key: {"pts": points, "pj": matches played}.
Tally = dict[str, float]


def detect_promoted(market: Rows, history: Mapping[str, Tally]) -> set[str]:
    per_club: dict[str, list[int]] = {}
    for r in market:
        h = history.get(text(r, "ff_id"))
        tally = per_club.setdefault(r.get("club") or "?", [0, 0])
        tally[0] += 1
        tally[1] += 1 if h and h["pj"] > 0 else 0
    return {c for c, (n, k) in per_club.items() if n >= 10 and k / n < 0.15}


def fit_promoted_discount(market: Rows, history: Mapping[str, Tally],
                          played: list[Scored], priors: Mapping[str, float]) -> float:
    """How a promoted club's players score against their prior (ppm_prior),
    pulled toward PROMOTED_DISCOUNT."""
    promoted = detect_promoted(market, history)
    prior_of: dict[str, float | None] = {k: priors.get(k) for r in market
                                         if r.get("club") in promoted and slot(r.get("position"))
                                         for k in [text(r, "ff_id")]}
    pts = expected = n = 0.0
    for s in played:
        prior = prior_of.get(s.key)
        if prior and s.games > 0:
            pts += s.pts
            expected += prior * s.games
            n += s.games
    if expected <= 0:
        return PROMOTED_DISCOUNT
    return shrink(PROMOTED_DISCOUNT, PROMOTED_DISCOUNT_K, n * pts / expected, n)


class Rating(NamedTuple):
    ppm: float
    assumed: bool
    cur_pj: float = 0.0
    pj: float = 0.0


class Rates(NamedTuple):
    """A player's forecast inputs: points per match, and the chance he is
    picked if fit for his next match (p_now) and later ones (p_rest)."""
    key: str
    slot: str
    ppm: float
    p_now: float
    p_rest: float
    pj: float


class Scorer:
    """Points per match for any player: last season shrunk toward his
    prior (ppm_prior; discounted for promoted clubs), then this season's
    matches; and, from StartOdds, his chance of starting."""

    def __init__(self, market: Rows, starts: StartOdds,
                 last_season: Mapping[str, Tally] | None = None, shrink_k: float | None = None,
                 current: Mapping[str, Tally] | None = None,
                 promoted_discount: float = PROMOTED_DISCOUNT,
                 priors: Mapping[str, float] | None = None) -> None:
        self.market = market
        self.starts = starts
        self.last_season = last_season or {}
        self.shrink_k = SHRINK_K if shrink_k is None else shrink_k
        self.promoted_discount = promoted_discount
        self.current = current or {}
        self.lookup: dict[str, Mapping[str, Any]] = {text(r, "ff_id"): r for r in market
                                        if r.get("name")}
        self.promoted = detect_promoted(self.market, self.last_season)
        self.priors = priors or {}

    def rate(self, rec: Mapping[str, Any]) -> Rating:
        """Points per match: last season pulled toward his prior (or that
        prior, discounted for a promoted club); then this season pulled
        toward that."""
        key = text(rec, "ff_id")
        prior = self.priors.get(key, 0.0)
        k = self.shrink_k
        h = self.last_season.get(key)
        prior_pj = float(h["pj"]) if h and h["pj"] > 0 else 0.0
        if h and prior_pj:
            base = shrink(prior, k, h["pts"], prior_pj)
        elif (rec.get("club") or "") in self.promoted:
            base = prior * self.promoted_discount
        else:
            base = prior
        c = self.current.get(key)
        cur_pj = float(c["pj"]) if c and c["pj"] > 0 else 0.0
        return Rating(shrink(base, k, c["pts"] if c and cur_pj else 0.0, cur_pj),
                      not prior_pj and cur_pj < k, cur_pj, prior_pj + cur_pj)

    def fit(self, key: str, jornada: int, when: date | None, next_j: int) -> float:
        return self.starts.fit(key, jornada, when, next_j)

    def rates(self, rec: Mapping[str, Any]) -> Rates:
        key = text(rec, "ff_id")
        return Rates(key, slot(rec.get("position")),
                     self.rate(rec).ppm, self.starts.picked(key, True),
                     self.starts.picked(key, False), self.rate(rec).pj)


def _selftest_prior() -> None:
    """schema.sql's last_season and ppm_prior on season_points and
    current_market as tables: the median of last season's points per match
    (10+ matches) in his position, or with PPM_BANDS in his position and
    value band; a position with none, everyone's."""
    import sqlite3
    from ffcore.db import SCHEMA, functions

    def prior(bands: float) -> dict[str, float]:
        db = sqlite3.connect(":memory:")
        functions(db)
        db.execute("CREATE TABLE model_constants (variant, name, value)")
        db.execute("INSERT INTO model_constants VALUES ('live', 'PPM_BANDS', ?)", (bands,))
        db.execute("CREATE TABLE season_points (season, ff_id, points, games)")
        db.execute("CREATE TABLE current_market (ff_id, position, value)")
        db.executemany("INSERT INTO season_points VALUES (?, ?, ?, ?)", [
            ("2024-25", "a", 999.0, 30.0),                        # an older season: not read
            ("2025-26", "a", 60.0, 30.0), ("2025-26", "b", 90.0, 30.0),
            ("2025-26", "c", 150.0, 30.0), ("2025-26", "d", 210.0, 30.0),
            ("2025-26", "e", 300.0, 9.0), ("2025-26", "f", 40.0, 10.0)])
        db.executemany("INSERT INTO current_market VALUES (?, ?, ?)", [
            ("a", "DEF", 1e6), ("b", "DEF", 2e6), ("c", "DEF", 50e6), ("d", "DEF", 60e6),
            ("e", "DEF", 70e6), ("f", "portero", 3e6), ("x", "", 5e6)])
        sql = SCHEMA.read_text()
        for view in ("k", "last_season", "ppm_prior"):
            i = sql.index("CREATE VIEW %s AS" % view)
            db.execute(sql[i:sql.index(";\n", i)])
        return dict(db.execute("SELECT player, prior FROM ppm_prior"))

    flat = prior(0)
    assert flat["a"] == flat["e"] == 4.0 and flat["f"] == 4.0, \
        "DEF: the median of 2, 3, 5, 7 (e's 9 matches are too few); POR: f's 4"
    assert flat["x"] == 4.0, "no position: the median of everyone's (2, 3, 4, 5, 7)"
    two = prior(2)
    assert (two["a"], two["c"]) == (2.5, 6.0), \
        "two bands by value: DEF's cheaper (a, b) and dearer (c, d)"


def _selftest() -> None:
    _selftest_prior()
    from ffcore.tidy import typed

    K = SHRINK_K

    row = dict(typed("market", [{"position": "defensa", "team": "Mid", "club": "Mid",
                                 "value": "10.00M"}])[0])

    market = [dict(row, name=n, ff_id=Name(n).key) for n in
              ["p%d" % i for i in range(10)] + ["Sub", "Newbie"]]
    hist = {"p%d" % i: {"pts": 100.0 + i, "pj": 34.0} for i in range(10)}
    hist["sub"] = {"pts": 20.0, "pj": 4.0}
    xi = typed("lineups", [{"player": Name(n).key, "player_name": n, "start_pct": "100"}
          for n in [r["name"] for r in market]])

    prior = 3.05
    pri = {r["ff_id"]: prior for r in market}
    sc = Scorer(market, StartOdds(xi), hist, priors=pri)

    thin = sc.rate(dict(row, name="Sub", ff_id="sub"))
    assert abs(thin.ppm - (20.0 + K * prior) / (4.0 + K)) < 1e-9
    assert not thin.assumed and thin.cur_pj == 0.0
    assert sc.rate(dict(row, name="Newbie", ff_id="newbie")).assumed

    full = sc.rate(dict(row, name="p0", ff_id="p0"))
    cur = {"p0": {"pts": 30.0, "pj": 3.0}}
    sc2 = Scorer(market, StartOdds(xi), hist, current=cur, priors=pri)
    blended = sc2.rate(dict(row, name="p0", ff_id="p0"))
    assert abs(blended.ppm - (30.0 + K * full.ppm) / (3.0 + K)) < 1e-9
    assert blended.cur_pj == 3.0
    assert full.ppm < blended.ppm < 10.0

    promo_market = [dict(row, name="q%d" % i, team="Rise", club="Rise",
                         ff_id="q%d" % i)
                    for i in range(10)]
    promo_hist = {"q0": {"pts": 34.0, "pj": 34.0}}
    assert detect_promoted(promo_market, promo_hist) == {"Rise"}
    accented = [dict(row, name="r%d" % i, team="Málaga", club="Málaga")
                for i in range(10)]
    assert detect_promoted(accented, {}) == {"Málaga"}
    promo_pri = {r["ff_id"]: prior for r in promo_market}
    sc3 = Scorer(promo_market, StartOdds([]), promo_hist, priors=promo_pri)
    assert sc3.promoted == {"Rise"}
    q5 = dict(row, name="q5", team="Rise", club="Rise", ff_id="q5")
    newbie = sc3.rate(q5)
    assert newbie.assumed and abs(newbie.ppm - prior * PROMOTED_DISCOUNT) < 1e-9, newbie
    lower = Scorer(promo_market, StartOdds([]), promo_hist, promoted_discount=0.5, priors=promo_pri)
    assert lower.rate(q5).ppm < newbie.ppm

    assert fit_promoted_discount(promo_market, promo_hist, [], promo_pri) == PROMOTED_DISCOUNT
    at_discount = [Scored("q%d" % (i % 10), 1, round(prior * PROMOTED_DISCOUNT, 1), 1.0, "t")
                   for i in range(80)]
    assert abs(fit_promoted_discount(promo_market, promo_hist, at_discount, promo_pri)
               - PROMOTED_DISCOUNT) < 0.01
    at_prior = [r._replace(pts=round(prior, 1)) for r in at_discount * 5]
    assert fit_promoted_discount(promo_market, promo_hist, at_prior, promo_pri) \
        > PROMOTED_DISCOUNT + 0.15

    assert Scorer(market, StartOdds(xi), hist, current={}, priors=pri).rate(dict(row, name="p0", ff_id="p0")) == full
    assert Scorer(market, StartOdds(xi), hist, priors=pri,
                  current={"p0": {"pts": 0.0, "pj": 0.0}}).rate(dict(row, name="p0", ff_id="p0")) \
        == full

    dbt = typed("lineups", [{"player": "p0", "player_name": "p0", "start_pct": "100", "status": "doubt"}])
    r0 = Scorer(market, StartOdds(dbt), hist, priors=pri).rates(dict(row, name="p0", ff_id="p0"))
    assert (r0.key, r0.slot, r0.p_now, r0.p_rest) == ("p0", "DEF", 0.15, 0.15), \
        "flagged, no record, never listed fit: his 100% is not a selection signal"
    assert abs(r0.ppm - full.ppm) < 1e-9 and r0.pj == full.pj


    benched = Scorer(market, StartOdds(xi, history={"p0": (0.0, 6.0)}), hist,
                     current={"p0": {"pts": 30.0, "pj": 3.0}}).rates(dict(row, name="p0", ff_id="p0"))
    assert abs(benched.p_now - PICK_K / (PICK_K + 6)) < 1e-9, benched
    susp = typed("lineups", [{"player": "p0", "player_name": "p0", "start_pct": "0", "status": "suspended"}])
    back = Scorer(market, StartOdds(susp, history={"p0": (1.8, 2.0)}, later={"p0": 0.7}), hist,
                  current={"p0": {"pts": 30.0, "pj": 2.0}}).rates(dict(row, name="p0", ff_id="p0"))
    assert back.p_rest == 0.7, "after his next match: pick_later's"
    assert back.p_now == back.p_rest, "the suspension is the fit factor's, not selection's"

    print("ffcore.score self-test OK")


if __name__ == "__main__":
    _selftest()
